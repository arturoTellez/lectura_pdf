import os
import base64
import json
import requests
import pandas as pd
from abc import abstractmethod
import pdfplumber
import fitz  # PyMuPDF
import re
from parsers import BankParser


# Try to load env vars
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

class AIBankParser(BankParser):
    """Base class for AI-powered bank parsers."""
    
    def __init__(self, text, pdf_path=None, api_key=None, month_context=None):
        super().__init__(text, pdf_path, month_context=month_context)
        self.api_key = api_key

    def _pdf_to_images(self):
        """Converts PDF pages to base64 encoded images using PyMuPDF."""
        if not self.pdf_path:
            raise ValueError("PDF path is required for AI parsing.")
        
        encoded_images = []
        try:
            doc = fitz.open(self.pdf_path)
            print(f"DEBUG: PDF has {len(doc)} pages.")
            for i, page in enumerate(doc):
                print(f"DEBUG: Converting page {i+1}/{len(doc)} to image...")
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2)) # Zoom x2 for better OCR quality
                img_data = pix.tobytes("jpg")
                img_str = base64.b64encode(img_data).decode("utf-8")
                encoded_images.append(img_str)
            doc.close()
            print(f"DEBUG: Converted {len(encoded_images)} pages to images.")
        except Exception as e:
            print(f"Error converting PDF to images with PyMuPDF: {e}")
            raise e
            
        return encoded_images



    def parse(self):
        """Runs the full parsing process and returns a dict with metadata and movements."""
        movements_df = self.extract_movements()
        movements_df = self._normalize_movements(movements_df) # Normalize dates
        
        return {
            "account_number": self.extract_account_number(),
            "movements": movements_df,
            "metadata": getattr(self, "last_metadata", {}),
            "informative_data": getattr(self, "last_informative_data", [])
        }


    def validate_balance(self, movements, initial_balance, final_balance, income, expenses):

        """
        Validates that Initial Balance + Income - Expenses = Final Balance.
        Returns a dict with validation status and details.
        """
        # Calculate from movements
        calc_income = sum(m['monto'] for m in movements if m['tipo'] == 'Abono')
        calc_expenses = sum(m['monto'] for m in movements if m['tipo'] == 'Cargo')
        
        # Check if extracted totals match calculated totals
        income_match = abs(calc_income - (income or 0)) < 0.1
        expenses_match = abs(calc_expenses - (expenses or 0)) < 0.1
        
        # Check balance equation
        # Note: Scotiabank statements usually show:
        # Saldo Anterior + Depósitos - Retiros = Saldo Nuevo
        expected_final = (initial_balance or 0) + (income or 0) - (expenses or 0)
        balance_match = abs(expected_final - (final_balance or 0)) < 0.1

        
        return {
            "valid": balance_match and income_match and expenses_match,
            "balance_match": balance_match,
            "income_match": income_match,
            "expenses_match": expenses_match,
            "calc_income": calc_income,
            "calc_expenses": calc_expenses,
            "expected_final": expected_final,
            "diff": final_balance - expected_final
        }

class OpenAIVisionParser(AIBankParser):
    """Parser using OpenAI GPT-4o Vision."""
    
    def __init__(self, text, pdf_path=None, api_key=None, month_context=None):
        super().__init__(text, pdf_path, api_key, month_context=month_context)
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")

    def extract_account_number(self):
        # We can try to extract it from text first, or ask AI
        # Let's use the regex from base classes or simple search
        m = re.search(r"Tarjeta titular:.*(\d{4})", self.text)
        if m:
            return f"****{m.group(1)}"
        return "SCOTIA-AI-UNKNOWN"

    def _call_openai(self, client, img_str, prompt):
        """Helper to call OpenAI API."""
        try:
            response = client.chat.completions.create(
                model="gpt-4o", 
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{img_str}",
                                    "detail": "high"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=4096,
                response_format={ "type": "json_object" }
            )
            content = response.choices[0].message.content
            return json.loads(content)
        except Exception as e:
            print(f"Error calling OpenAI: {e}")
            return {}

    def _deduplicate_movements(self, movements):
        """Deduplicates movements based on a composite key."""
        unique_movs = []
        seen = set()
        
        for m in movements:
            # Create a unique key. Amount is float, so be careful.
            # Using string representation of amount to 2 decimal places.
            key = (
                m.get("fecha_oper"), 
                m.get("descripcion"), 
                f"{m.get('monto', 0):.2f}", 
                m.get("tipo")
            )
            
            if key not in seen:
                seen.add(key)
                unique_movs.append(m)
                
        return unique_movs

    def _collect_informative(self, info_data):
        """Collects informative data."""
        if not info_data:
            return
            
        if not hasattr(self, "last_informative_data"):
            self.last_informative_data = []
            
        if isinstance(info_data, list):
            self.last_informative_data.extend(info_data)

    def _update_metadata(self, new_meta):
        """Updates metadata safely."""
        if not new_meta:
            return
            
        if not hasattr(self, "last_metadata"):
            self.last_metadata = {}
        
        for k, v in new_meta.items():
            if v is not None and v != 0 and v != "":
                self.last_metadata[k] = v


    def extract_movements(self):
        if not self.api_key:
            raise ValueError("OpenAI API Key is missing.")

        # Use OpenAI Client with SSL verification disabled for this environment
        import httpx
        from openai import OpenAI
        
        client = OpenAI(
            api_key=self.api_key,
            http_client=httpx.Client(verify=False)
        )

        prompt = """
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
        
        movements = []
        
        # Open PDF with fitz to handle splitting
        try:
            doc = fitz.open(self.pdf_path)
            print(f"DEBUG: PDF has {len(doc)} pages.")
            
            for i, page in enumerate(doc):
                print(f"DEBUG: Processing page {i+1}/{len(doc)}...")
                
                # 1. Generate Images
                # Full Page
                pix_full = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                img_full_b64 = base64.b64encode(pix_full.tobytes("jpg")).decode("utf-8")
                
                # Split Pages (Top and Bottom with overlap)
                rect = page.rect
                h = rect.height
                w = rect.width
                overlap = h * 0.1 # 10% overlap
                
                rect_top = fitz.Rect(0, 0, w, h/2 + overlap)
                rect_bottom = fitz.Rect(0, h/2 - overlap, w, h)
                
                pix_top = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=rect_top)
                img_top_b64 = base64.b64encode(pix_top.tobytes("jpg")).decode("utf-8")
                
                pix_bottom = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=rect_bottom)
                img_bottom_b64 = base64.b64encode(pix_bottom.tobytes("jpg")).decode("utf-8")
                
                # 2. Run API on Full Image
                print(f"DEBUG: Calling OpenAI for Full Page {i+1}...")
                full_data = self._call_openai(client, img_full_b64, prompt)
                full_movs = full_data.get("movements", [])
                
                # 3. Run API on Split Images
                print(f"DEBUG: Calling OpenAI for Top Half {i+1}...")
                top_data = self._call_openai(client, img_top_b64, prompt)
                
                print(f"DEBUG: Calling OpenAI for Bottom Half {i+1}...")
                bottom_data = self._call_openai(client, img_bottom_b64, prompt)
                
                split_movs = top_data.get("movements", []) + bottom_data.get("movements", [])
                
                # 4. Deduplicate Split Results
                unique_split_movs = self._deduplicate_movements(split_movs)
                
                # 5. Compare and Select
                print(f"DEBUG: Page {i+1} - Full Count: {len(full_movs)}, Split Unique Count: {len(unique_split_movs)}")
                
                if len(unique_split_movs) > len(full_movs):
                    print("DEBUG: Using split results (found more transactions).")
                    final_page_movs = unique_split_movs
                    # Merge metadata from all sources to be safe
                    self._update_metadata(full_data.get("metadata"))
                    self._update_metadata(top_data.get("metadata"))
                    self._update_metadata(bottom_data.get("metadata"))
                    
                    # Collect informative data
                    self._collect_informative(full_data.get("informative_data"))
                    self._collect_informative(top_data.get("informative_data"))
                    self._collect_informative(bottom_data.get("informative_data"))
                else:
                    print("DEBUG: Using full page results.")
                    final_page_movs = full_movs
                    self._update_metadata(full_data.get("metadata"))
                    self._collect_informative(full_data.get("informative_data"))
                
                movements.extend(final_page_movs)
                
            doc.close()

            
        except Exception as e:
            print(f"Error processing PDF with OpenAI: {e}")
            raise e
                
        return pd.DataFrame(movements)



class NemotronParser(AIBankParser):
    """Parser using Nvidia Nemotron via Hugging Face Inference API."""
    
    def __init__(self, text, pdf_path=None, api_key=None, month_context=None):
        super().__init__(text, pdf_path, api_key, month_context=month_context)
        self.api_key = api_key or os.getenv("HUGGINGFACE_API_TOKEN")

    def extract_account_number(self):
        m = re.search(r"Tarjeta titular:.*(\d{4})", self.text)
        if m:
            return f"****{m.group(1)}"
        return "SCOTIA-HF-UNKNOWN"

    def extract_movements(self):
        if not self.api_key:
            raise ValueError("Hugging Face API Token is missing.")
            
        images = self._pdf_to_images()
        movements = []
        
        # Hugging Face Inference API URL for the specific model
        # The user mentioned: https://huggingface.co/nvidia/nemotron-ocr-v1
        # This model might be an image-to-text model.
        # API URL usually: https://api-inference.huggingface.co/models/nvidia/nemotron-ocr-v1
        
        api_url = "https://api-inference.huggingface.co/models/nvidia/nemotron-ocr-v1"
        
        headers = {
            "Authorization": f"Bearer {self.api_key}"
        }
        
        for i, img_str in enumerate(images):
            print(f"DEBUG: Processing page {i+1}/{len(images)} with Nemotron...")
            # For HF Inference API with image models, we usually send the raw image bytes, not base64 in JSON.

            # But we already converted to base64 string in _pdf_to_images.
            # Let's decode it back to bytes for the request.
            img_bytes = base64.b64decode(img_str)
            
            try:
                response = requests.post(api_url, headers=headers, data=img_bytes)
                response.raise_for_status()
                
                # The output format depends on the model.
                # Based on user documentation, if running locally it returns objects with 'text', 'confidence', etc.
                # If using HF Inference API, it might return a list of dicts or the specific structure.
                # Let's handle the documented structure: ocr_txts = [...]
                
                result = response.json()
                
                # Check for documented format
                if isinstance(result, dict):
                    if "ocr_txts" in result:
                        # This is the format from the docs!
                        texts = result["ocr_txts"]
                        # We need to construct movements from these texts.
                        # Since we lose spatial layout if we just take the list, 
                        # we might need to rely on the order or try to reconstruct.
                        # For a bank statement, line-by-line is usually preserved in the list order.
                        
                        full_text = "\n".join(texts)
                        
                        # Now parse this text
                        lines = full_text.split('\n')
                        for line in lines:
                            if re.match(r"\d{2}-[A-Z]{3}", line.upper()):
                                amounts = re.findall(r"[\d,]+\.\d{2}", line)
                                if amounts:
                                    monto = float(amounts[-1].replace(",", ""))
                                    tipo = "Cargo"
                                    if "ABONO" in line.upper() or "PAGO" in line.upper():
                                        tipo = "Abono"
                                        
                                    movements.append({
                                        "fecha_oper": line.split()[0],
                                        "fecha_liq": line.split()[0],
                                        "descripcion": line,
                                        "monto": monto,
                                        "tipo": tipo,
                                        "categoria": "Regular"
                                    })
                        continue # Next image

                # Fallback to standard HF Inference API format (list of dicts with generated_text)
                text_content = ""
                if isinstance(result, list) and len(result) > 0:
                    if "generated_text" in result[0]:
                        text_content = result[0]["generated_text"]
                    else:
                        text_content = str(result)
                elif isinstance(result, dict):
                    text_content = str(result)
                else:
                    text_content = str(result)
                    
                # Parse text_content (same logic as above)
                lines = text_content.split('\n')
                for line in lines:
                    if re.match(r"\d{2}-[A-Z]{3}", line.upper()):
                        amounts = re.findall(r"[\d,]+\.\d{2}", line)
                        if amounts:
                            monto = float(amounts[-1].replace(",", ""))
                            tipo = "Cargo"
                            if "ABONO" in line.upper() or "PAGO" in line.upper():
                                tipo = "Abono"
                                
                            movements.append({
                                "fecha_oper": line.split()[0],
                                "fecha_liq": line.split()[0],
                                "descripcion": line,
                                "monto": monto,
                                "tipo": tipo,
                                "categoria": "Regular"
                            })

            except Exception as e:
                print(f"Error processing page {i+1} with Hugging Face: {e}")
                # If API fails, it might be because the model is not supported on Inference API.
                print("Note: Nemotron OCR v1 might require a dedicated Inference Endpoint or local GPU.")
                
        return pd.DataFrame(movements)

class LocalNemotronParser(AIBankParser):
    """Parser using local Nvidia Nemotron OCR (requires GPU and nemotron-ocr package)."""
    
    def __init__(self, text, pdf_path=None, api_key=None, month_context=None):
        super().__init__(text, pdf_path, api_key, month_context=month_context)
        # No API key needed for local, but we need the package

    def extract_account_number(self):
        m = re.search(r"Tarjeta titular:.*(\d{4})", self.text)
        if m:
            return f"****{m.group(1)}"
        return "SCOTIA-LOCAL-UNKNOWN"

    def extract_movements(self):
        try:
            from nemotron_ocr.inference.pipeline import NemotronOCR
        except ImportError:
            raise ImportError("nemotron-ocr package not found. Please install it with 'pip install nemotron-ocr' (requires Nvidia GPU).")
            
        images = self._pdf_to_images()
        movements = []
        
        # Initialize OCR pipeline (this might be slow and requires GPU)
        try:
            ocr = NemotronOCR()
        except Exception as e:
            raise RuntimeError(f"Failed to initialize NemotronOCR: {e}. Ensure you have an Nvidia GPU and CUDA installed.")

        for i, img_str in enumerate(images):
            print(f"DEBUG: Processing page {i+1}/{len(images)} with Local Nemotron...")
            
            # NemotronOCR expects a file path or numpy array. 
            # We have base64 string. Let's save to temp file or convert.
            # Saving to temp file is safer for the library.
            import tempfile
            
            img_bytes = base64.b64decode(img_str)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_img:
                tmp_img.write(img_bytes)
                tmp_path = tmp_img.name
                
            try:
                predictions = ocr(tmp_path)
                
                # predictions is a list of dicts: {'text': ..., 'confidence': ..., 'bbox': ...}
                # We need to reconstruct lines or use the text directly.
                # The library might return text chunks.
                
                # Simple reconstruction: join all text
                # Or try to group by Y coordinate (lines)
                
                # Let's just collect all text and try to parse line by line
                # Note: This loses layout info if we just join.
                # But for now, let's try to join with newlines if they seem to be separate lines?
                # The output doesn't guarantee order or line breaks.
                
                # Heuristic: Sort by 'upper' (Y coordinate) then 'left' (X coordinate)
                sorted_preds = sorted(predictions, key=lambda x: (x['upper'], x['left']))
                
                full_text = "\n".join([p['text'] for p in sorted_preds])
                
                lines = full_text.split('\n')
                for line in lines:
                    if re.match(r"\d{2}-[A-Z]{3}", line.upper()):
                        amounts = re.findall(r"[\d,]+\.\d{2}", line)
                        if amounts:
                            monto = float(amounts[-1].replace(",", ""))
                            tipo = "Cargo"
                            if "ABONO" in line.upper() or "PAGO" in line.upper():
                                tipo = "Abono"
                                
                            movements.append({
                                "fecha_oper": line.split()[0],
                                "fecha_liq": line.split()[0],
                                "descripcion": line,
                                "monto": monto,
                                "tipo": tipo,
                                "categoria": "Regular"
                            })
                            
            except Exception as e:
                print(f"Error processing page {i+1} with Local Nemotron: {e}")
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                
        return pd.DataFrame(movements)





class GeminiVisionParser(AIBankParser):
    """Parser using Google Gemini 1.5 Pro Vision (via google-genai SDK)."""
    
    def __init__(self, text, pdf_path=None, api_key=None, month_context=None):
        super().__init__(text, pdf_path, api_key, month_context=month_context)
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
                content = content.replace("", "").strip()
                
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
