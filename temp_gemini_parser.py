
class GeminiVisionParser(AIBankParser):
    """Parser using Google Gemini 1.5 Pro Vision (via google-genai SDK)."""
    
    def __init__(self, text, pdf_path=None, api_key=None):
        super().__init__(text, pdf_path, api_key)
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")

    def extract_account_number(self):
        m = re.search(r"Tarjeta titular:.*(\d{4})", self.text)
        if m:
            return f"****{m.group(1)}"
        return "SCOTIA-GEMINI-UNKNOWN"

    def extract_movements(self):
        if not self.api_key:
            raise ValueError("Gemini API Key is missing.")
            
        try:
            from google import genai
            from google.genai import types
        except ImportError:
            raise ImportError("google-genai package not found. Please install it.")

        client = genai.Client(api_key=self.api_key)
        
        images = self._pdf_to_images()
        movements = []

        prompt_text = """
        You are an expert AI specialized in extracting data from Scotiabank Mexico credit card statements.
        
        Your goal is to extract TWO distinct sets of data from the image:
        1. **Transactions (Movimientos)**: The main list of purchases, payments, and fees.
        2. **Informative Data (Información Informativa)**: Tables showing "Saldo Pendiente", "Plan de Pagos", or "Meses Sin Intereses" summaries.
        
        ### SECTION 1: TRANSACTIONS (Key: "movements")
        Extract the main transaction table. Each item must have:
        - "fecha_oper": Operation Date (DD-MMM).
        - "fecha_liq": Liquidation/Posting Date. **CRITICAL**: If this column is empty or missing (common in MSI installments), set it to null.
        - "descripcion": Full description.
        - "monto": The amount. **CRITICAL**: For "Meses Sin Intereses" installments (e.g., "3/12"), extract the *installment amount* (Pago Requerido/Monto del Periodo), NOT the total original purchase amount.
        - "tipo": "Cargo" or "Abono".
          * **Look for the sign**: A minus sign "-" (e.g., -500.00) usually indicates an "Abono" (Payment/Credit). No sign usually means "Cargo".
          * Verify with description keywords (e.g., "PAGO", "ABONO" -> Abono).
        - "categoria": "MSI" if it is an installment (e.g., "3/12"), else "Regular".

        ### SECTION 2: INFORMATIVE DATA (Key: "informative_data")
        Scotiabank statements often have a separate table/section for "Meses Sin Intereses" or "Plan de Pagos" that shows the *Total Balance Remaining* for each plan.
        - IF you see a table with headers like "Saldo Pendiente", "Monto Original", "Pagos Restantes":
          - Extract these rows into "informative_data".
          - Do NOT add them to "movements" to avoid double counting.
          - Structure: {"descripcion": "...", "saldo_pendiente": 123.45, "monto_original": ...}

        ### METADATA (Key: "metadata")
        Extract summary values usually found at the top/bottom:
        - "saldo_anterior": Previous Balance.
        - "saldo_nuevo": New Balance / Ending Balance.
        - "total_abonos": Total Payments.
        - "total_cargos": Total Purchases.

        **OUTPUT FORMAT**:
        Return ONLY a valid JSON object:
        {
            "movements": [ ... ],
            "informative_data": [ ... ],
            "metadata": { ... }
        }
        """

        for i, img_str in enumerate(images):
            print(f"DEBUG: Processing page {i+1}/{len(images)} with Gemini (google-genai)...")
            
            try:
                # Convert base64 to bytes
                img_bytes = base64.b64decode(img_str)
                
                # Create content parts
                image_part = types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg")
                text_part = types.Part.from_text(text=prompt_text)
                
                response = client.models.generate_content(
                    model='gemini-1.5-pro',
                    contents=[text_part, image_part],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json"
                    )
                )
                
                # Parse JSON from response
                content = response.text
                if not content:
                    print(f"Warning: Empty response from Gemini for page {i+1}")
                    continue
                    
                # Cleanup markdown code blocks if present
                content = content.replace("```json", "").replace("```", "").strip()
                
                data = json.loads(content)
                
                if "movements" in data:
                    movements.extend(data["movements"])
                
                if "metadata" in data:
                    self._update_metadata(data["metadata"])
                    
                if "informative_data" in data:
                    self._collect_informative(data["informative_data"])
                    
            except Exception as e:
                print(f"Error processing page {i+1} with Gemini: {e}")
                
        return pd.DataFrame(movements)
