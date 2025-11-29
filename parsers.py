import re
import pandas as pd
from abc import ABC, abstractmethod
# Import AI parsers (lazy import to avoid circular dependency if needed, but here is fine)
# Note: We will import them inside the factory or at top if no circular dep.
# But ai_parsers imports BankParser from parsers, so we have a circular dependency if we import ai_parsers here at top level.
# We should move BankParser to a separate file or handle imports carefully.
# For now, let's keep BankParser here and import ai_parsers inside get_parser or similar.


class BankParser(ABC):
    """Abstract base class for bank statement parsers."""

    def __init__(self, text, pdf_path=None):
        self.text = text
        self.pdf_path = pdf_path

    @abstractmethod
    def extract_account_number(self):
        """Extracts the account number from the text."""
        pass

    @abstractmethod
    def extract_movements(self):
        """Extracts movements and returns a DataFrame."""
        pass

    def parse(self):
        """Runs the full parsing process and returns a dict with metadata and movements."""
        return {
            "account_number": self.extract_account_number(),
            "movements": self.extract_movements()
        }


import itertools

class BBVADebitParser(BankParser):
    """Parser for BBVA Debit account statements."""

    def extract_account_number(self):
        m = re.search(r"No\. de Cuenta\s+(\d+)", self.text)
        return m.group(1) if m else None

    def _extract_summary_totals(self):
        """Extracts total deposits and total charges from the summary section."""
        # Example: "Depósitos / Abonos (+) 2 15,000.00"
        # Example: "Retiros / Cargos (-) 4 15,704.10"
        
        deposits = 0.0
        charges = 0.0
        
        # Regex for Deposits
        m_dep = re.search(r"Depósitos / Abonos \(\+\)\s+\d+\s+([\d,]+\.\d{2})", self.text)
        if m_dep:
            deposits = float(m_dep.group(1).replace(",", ""))
            
        # Regex for Charges
        m_chr = re.search(r"Retiros / Cargos \(\-\)\s+\d+\s+([\d,]+\.\d{2})", self.text)
        if m_chr:
            charges = float(m_chr.group(1).replace(",", ""))
            
        return deposits, charges

    def _obtener_bloque(self):
        ini = self.text.find("Detalle de Movimientos Realizados")
        fin = self.text.find("Total de Movimientos")
        if ini == -1 or fin == -1:
            return None
        return self.text[ini:fin]

    def _es_mov(self, linea):
        return bool(re.match(r"^\d{2}/[A-Z]{3}\s+\d{2}/[A-Z]{3}", linea.strip()))

    def _parsear_linea(self, linea):
        linea = " ".join(linea.split())
        m = re.match(r"^(\d{2}/[A-Z]{3})\s+(\d{2}/[A-Z]{3})\s+(.+)$", linea)
        if not m:
            return None
        
        fecha_op, fecha_liq, resto = m.groups()

        # Detect all amounts
        montos = re.findall(r"(\d{1,3}(?:,\d{3})*\.\d{2})", resto)
        
        # The description is everything before the first amount
        if montos:
            descripcion = resto.split(montos[0])[0].strip()
        else:
            descripcion = resto.strip()

        return fecha_op, fecha_liq, descripcion, montos

    def extract_movements(self):
        bloque = self._obtener_bloque()
        if not bloque:
            raise ValueError("No se encontró la sección de movimientos.")

        total_depositos_esperado, total_retiros_esperado = self._extract_summary_totals()
        
        lineas = bloque.splitlines()
        candidatos = [] # List of (index, amount, metadata)

        # 1. First pass: Collect all potential transaction amounts
        for linea in lineas:
            if self._es_mov(linea):
                parsed = self._parsear_linea(linea)
                if not parsed:
                    continue
                fecha_op, fecha_liq, desc, montos = parsed
                
                if not montos:
                    continue

                # We assume the FIRST amount is the transaction amount.
                # The last amount might be the balance, but we are ignoring balance column for now
                # as per the combinatorial strategy.
                monto_str = montos[0]
                monto_val = float(monto_str.replace(",", ""))
                
                candidatos.append({
                    "fecha_oper": fecha_op,
                    "fecha_liq": fecha_liq,
                    "descripcion": desc,
                    "monto": monto_val
                })

        # 2. Combinatorial Solver for Deposits
        # We need to find a subset of 'candidatos' whose amounts sum to 'total_depositos_esperado'
        
        indices = range(len(candidatos))
        deposit_indices = set()
        
        found_solution = False
        
        # Try to find a combination that matches deposits
        # We limit r to avoid explosion if N is large, but for bank statements usually N < 100
        # If N is large, this is risky. But let's try.
        # Optimization: Filter out amounts > total_depositos_esperado (impossible to be part of sum if all positive)
        
        # Note: Floating point comparison needs tolerance
        TOLERANCE = 0.01
        
        # Try to match Deposits first (usually fewer items?)
        # Or maybe Charges?
        # Let's try Deposits.
        
        # If total deposits is 0, then no deposits.
        if total_depositos_esperado == 0:
            deposit_indices = set()
            found_solution = True
        else:
            # We try combinations of length 1 up to N
            # To optimize, we can stop if we find one solution.
            # Warning: There could be multiple combinations summing to the same value.
            # But in accounting, usually exact match is good enough.
            
            for r in range(1, len(candidatos) + 1):
                for subset_indices in itertools.combinations(indices, r):
                    subset_sum = sum(candidatos[i]["monto"] for i in subset_indices)
                    if abs(subset_sum - total_depositos_esperado) < TOLERANCE:
                        deposit_indices = set(subset_indices)
                        found_solution = True
                        break
                if found_solution:
                    break
        
        # 3. Assign Types
        registros = []
        calculated_charges = 0.0
        
        for i, cand in enumerate(candidatos):
            tipo = "Cargo"
            if i in deposit_indices:
                tipo = "Abono"
            else:
                calculated_charges += cand["monto"]
            
            registros.append({
                "fecha_oper": cand["fecha_oper"],
                "fecha_liq": cand["fecha_liq"],
                "descripcion": cand["descripcion"],
                "monto": cand["monto"],
                "tipo": tipo
            })
            
        # 4. Validation
        if not found_solution:
            print(f"WARNING: No se encontró combinación para Depósitos: {total_depositos_esperado}")
        
        if abs(calculated_charges - total_retiros_esperado) > TOLERANCE:
            print(f"WARNING: Discrepancia en Cargos. Calculado: {calculated_charges}, Esperado: {total_retiros_esperado}")

        return pd.DataFrame(registros)


class BBVACreditParser(BankParser):
    """Parser for BBVA Credit account statements."""
    
    # Regex patterns
    MSI_PATTERN = re.compile(
        r"""
        ^(?P<fecha>\d{2}-[a-zA-Z]{3}-\d{4})      # fecha operación
        \s+
        (?P<descripcion>.+?)                     # descripción (lo que sea hasta el primer $)
        \s+\$(?P<monto_original>[\d,]+\.\d{2})   # monto original
        \s+\$(?P<saldo_pendiente>[\d,]+\.\d{2})  # saldo pendiente
        \s+\$(?P<pago_requerido>[\d,]+\.\d{2})   # pago requerido
        \s+(?P<num_pago>\d+\s+de\s+\d+)          # '6 de 18'
        \s+(?P<tasa>[\d\.,]+)%?                  # tasa '0.00%' (opcional el %)
        """,
        re.VERBOSE,
    )

    REGULAR_PATTERN = re.compile(
        r"""
        ^(?P<fecha_op>\d{2}-[a-zA-Z]{3}-\d{4})   # fecha operación
        \s+
        (?P<fecha_cargo>\d{2}-[a-zA-Z]{3}-\d{4}) # fecha de cargo
        \s+
        (?P<descripcion>.+?)                     # descripción
        \s+
        (?P<signo>[+-])                          # + o -
        \s*\$(?P<monto>[\d,]+\.\d{2})            # monto
        $
        """,
        re.VERBOSE,
    )

    def extract_account_number(self):
        # Attempt to find account number in credit statement
        # Usually "Tarjeta Digital ***1234" or similar
        # For now, let's try to find a pattern or return a placeholder if not found easily
        # The user's script didn't extract it, so we'll leave a placeholder or try a generic regex
        m = re.search(r"Tarjeta\s+(?:Digital|Física)?\s*\*{3,}(\d{4})", self.text)
        if m:
            return f"****{m.group(1)}"
        return "BBVA-CREDIT-UNKNOWN"

    def _parse_monto(self, monto_str: str) -> float:
        return float(monto_str.replace(",", ""))

    def extract_movements(self):
        lineas = self.text.splitlines()
        
        en_msi = False
        en_regulares = False

        registros = []

        for linea in lineas:
            linea = linea.strip()
            
            # --- Detectar cambio de sección ---
            if "COMPRAS Y CARGOS DIFERIDOS A MESES SIN INTERESES" in linea:
                en_msi = True
                en_regulares = False
                continue

            if linea.startswith("CARGOS,COMPRAS Y ABONOS REGULARES"):
                en_regulares = True
                en_msi = False
                continue

            # cortar sección cuando llegamos a totales o notas
            if linea.startswith("TOTAL CARGOS") or linea.startswith("TOTAL ABONOS") \
               or linea.startswith("Notas:"):
                en_msi = False
                en_regulares = False

            # saltar encabezados de tabla
            if "Fecha de la" in linea or ("Fecha" in linea and "Descripción del movimiento" in linea):
                continue

            # --- Parsear filas de MSI ---
            if en_msi:
                m = self.MSI_PATTERN.match(linea)
                if m:
                    data = m.groupdict()
                    try:
                        monto = self._parse_monto(data["pago_requerido"]) # Use pago requerido as the 'amount' for this month? 
                        # Or should we list the full original amount? 
                        # Usually for a statement parser we want the amount affecting the balance *this month*.
                        # But for MSI, 'pago_requerido' is what you pay now.
                        # Let's store everything in metadata.
                        
                        registros.append({
                            "fecha_oper": data["fecha"],
                            "fecha_liq": data["fecha"], # Same date for MSI usually
                            "descripcion": f"{data['descripcion']} ({data['num_pago']})",
                            "monto": monto,
                            "tipo": "Cargo", # MSI payment is a charge
                            "categoria": "MSI",
                            "meta_monto_original": self._parse_monto(data["monto_original"]),
                            "meta_saldo_pendiente": self._parse_monto(data["saldo_pendiente"])
                        })
                    except ValueError:
                        pass

            # --- Parsear filas regulares ---
            if en_regulares:
                r = self.REGULAR_PATTERN.match(linea)
                if r:
                    data = r.groupdict()
                    try:
                        monto = self._parse_monto(data["monto"])
                        signo = 1 if data["signo"] == "+" else -1
                        monto_con_signo = signo * monto
                        
                        tipo = "Abono" if signo == 1 else "Cargo" # + is Abono (Payment to card), - is Cargo (Purchase) usually in Credit Cards?
                        # Wait, in BBVA Credit:
                        # "STR*DALEFON + $220.00" -> This looks like a refund or payment?
                        # "BMOVIL.PAGO TDC - $7,643.10" -> Payment to card is usually a credit (Abono).
                        # Let's check the user's script logic:
                        # signo = 1 if data["signo"] == "+" else -1
                        # It doesn't explicitly say "Cargo" or "Abono".
                        # Usually:
                        # - (Negative) is a Charge (Debt increases? Or Debt decreases?)
                        # Actually in credit cards:
                        # Purchases are positive (add to debt) or negative?
                        # Let's look at the user's example:
                        # "BMOVIL.PAGO TDC - $7,643.10" -> Payment TO the card. This reduces debt.
                        # "STR*DALEFON + $220.00" -> This might be a purchase? Or a refund?
                        # Let's stick to the sign for now.
                        
                        # Standard convention:
                        # Cargo (Purchase) -> Increases Balance
                        # Abono (Payment) -> Decreases Balance
                        
                        # If "Pago TDC" has a minus, it reduces the balance. So it's an Abono.
                        # If "+" is used for purchases... wait, usually it's the other way around in math, but banks are weird.
                        # Let's assume:
                        # (-) = Abono (Payment)
                        # (+) = Cargo (Purchase)
                        
                        # Let's just store the signed amount and infer type.
                        
                        final_type = "Abono" if data["signo"] == "-" else "Cargo"
                        
                        registros.append({
                            "fecha_oper": data["fecha_op"],
                            "fecha_liq": data["fecha_cargo"],
                            "descripcion": data["descripcion"],
                            "monto": monto,
                            "tipo": final_type,
                            "categoria": "Regular"
                        })
                    except ValueError:
                        pass

        return pd.DataFrame(registros)


class ScotiabankCreditParser(BankParser):
    """Parser for Scotiabank Credit statements."""
    
    # Regex patterns
    FECHA_LINE_PATTERN = re.compile(
        r"^(?P<fecha>\d{2}-[a-zA-Z]{3}-\d{4})\s+(?P<resto>.+)$"
    )

    MSI_TAIL_PATTERN = re.compile(
        r"""
        \$(?P<monto_original>[\d,]+\.\d{2})   # $699.00
        \s+\$(?P<saldo_pendiente>[\d,]+\.\d{2})
        \s+\$(?P<pago_requerido>[\d,]+\.\d{2})
        \s+(?P<num_pago>[\d/]+)               # 1/12, 3/3, 3/18, etc.
        \s+(?P<tasa>[\d\.,]+)%                # 0.0%
        """,
        re.VERBOSE,
    )

    REGULAR_PATTERN = re.compile(
        r"""
        ^(?P<fecha_op>\d{2}-[a-zA-Z]{3}-\d{4})   # fecha operación
        \s+
        (?P<fecha_cargo>\d{2}-[a-zA-Z]{3}-\d{4}) # fecha de cargo
        \s+
        (?P<descripcion>.+?)                     # descripción (incluye 3/3, 8/9, etc.)
        \s+
        (?P<signo>[+-])                          # + o -
        \s*\$(?P<monto>[\d,]+\.\d{2})            # monto
        $
        """,
        re.VERBOSE,
    )

    def extract_account_number(self):
        # Attempt to find account number
        # Usually "Tarjeta titular: **** **** **** 1234"
        m = re.search(r"Tarjeta titular:.*(\d{4})", self.text)
        if m:
            return f"****{m.group(1)}"
        return "SCOTIA-CREDIT-UNKNOWN"

    def _parse_monto(self, monto_str: str) -> float:
        return float(monto_str.replace(",", ""))

    def extract_movements(self):
        lineas = self.text.splitlines()
        
        en_msi = False
        en_regulares = False

        registros = []
        pendiente_msi = None

        print("DEBUG: Starting ScotiabankCreditParser.extract_movements")
        for linea in lineas:
            linea = linea.strip()
            
            # --- Cambios de sección ---
            if "COMPRAS Y CARGOS DIFERIDOS A MESES SIN INTERESES" in linea:
                print("DEBUG: Entered MSI section")
                en_msi = True
                en_regulares = False
                pendiente_msi = None
                continue

            if linea.startswith("CARGOS, ABONOS Y COMPRAS REGULARES (NO A MESES)"):
                print("DEBUG: Entered Regular section")
                en_regulares = True
                en_msi = False
                pendiente_msi = None
                continue

            # fin de tablas
            if linea.startswith("Total cargos") or linea.startswith("Total abonos") \
               or linea.startswith("ATENCIÓN DE QUEJAS") \
               or linea.startswith("Notas:"):
                if en_msi or en_regulares:
                    print(f"DEBUG: Leaving section. Line: {linea}")
                en_msi = False
                en_regulares = False
                pendiente_msi = None

            # Saltar encabezados y subtítulos
            if "Tarjeta titular" in linea:
                continue
            if "Fecha de la" in linea and "operación" in linea:
                continue
            if "Descripción del movimiento" in linea and "Monto" in linea:
                continue

            # --- MSI: vienen en 2 líneas ---
            if en_msi:
                # 1a línea: fecha + descripción (sin montos)
                m_fecha = self.FECHA_LINE_PATTERN.match(linea)
                if m_fecha:
                    print(f"DEBUG: MSI Header Line matched: {linea}")
                    
                    # Try to parse as single line first
                    parsed_single = False
                    if "$" in linea:
                        try:
                            resto = m_fecha.group("resto")
                            if "$" in resto:
                                antes_dolar, despues_dolar = resto.split("$", 1)
                                descripcion = antes_dolar.strip()
                                cola = "$" + despues_dolar.strip()
                                
                                m_tail = self.MSI_TAIL_PATTERN.search(cola)
                                if m_tail:
                                    data = m_tail.groupdict()
                                    
                                    try:
                                        monto = self._parse_monto(data["pago_requerido"])
                                        registros.append({
                                            "fecha_oper": m_fecha.group("fecha"),
                                            "fecha_liq": m_fecha.group("fecha"),
                                            "descripcion": f"{descripcion} ({data['num_pago']})",
                                            "monto": monto,
                                            "tipo": "Cargo",
                                            "categoria": "MSI",
                                            "meta_monto_original": self._parse_monto(data["monto_original"]),
                                            "meta_saldo_pendiente": self._parse_monto(data["saldo_pendiente"])
                                        })
                                        print(f"DEBUG: Added MSI record (Single Line): {descripcion}")
                                        parsed_single = True
                                    except ValueError as e:
                                        print(f"DEBUG: Error parsing amounts (Single Line): {e}")
                        except Exception as e:
                            print(f"DEBUG: Exception in single line parsing: {e}")

                    if parsed_single:
                        pendiente_msi = None
                        continue

                    # If not parsed as single line, treat as pending
                    pendiente_msi = {
                        "fecha": m_fecha.group("fecha"),
                        "descripcion": m_fecha.group("resto").strip(),
                    }
                    continue

                # Si tenemos una fila pendiente y aún no hay montos
                if pendiente_msi:
                    # Si NO hay $, sólo extendemos descripción (por si viene cortada)
                    if "$" not in linea:
                        pendiente_msi["descripcion"] += " " + linea.strip()
                        continue

                    # Si ya hay $, es la línea con montos
                    try:
                        if "$" in linea:
                            antes_dolar, despues_dolar = linea.split("$", 1)
                            pendiente_msi["descripcion"] += " " + antes_dolar.strip()
                            cola = "$" + despues_dolar.strip()
                            
                            print(f"DEBUG: Checking MSI Tail: {cola}")

                            m_tail = self.MSI_TAIL_PATTERN.search(cola)
                            if m_tail:
                                data = pendiente_msi.copy()
                                data.update(m_tail.groupdict())

                                # Convertir montos a float
                                try:
                                    monto = self._parse_monto(data["pago_requerido"])
                                    registros.append({
                                        "fecha_oper": data["fecha"],
                                        "fecha_liq": data["fecha"],
                                        "descripcion": f"{data['descripcion']} ({data['num_pago']})",
                                        "monto": monto,
                                        "tipo": "Cargo",
                                        "categoria": "MSI",
                                        "meta_monto_original": self._parse_monto(data["monto_original"]),
                                        "meta_saldo_pendiente": self._parse_monto(data["saldo_pendiente"])
                                    })
                                    print(f"DEBUG: Added MSI record: {data['descripcion']}")
                                except ValueError as e:
                                    print(f"DEBUG: Error parsing amounts: {e}")
                                
                                pendiente_msi = None
                            else:
                                print(f"DEBUG: MSI Tail Pattern FAILED: {cola}")
                    except ValueError:
                        pass
                continue

            # --- Movimientos regulares (NO a meses) ---
            if en_regulares:
                r = self.REGULAR_PATTERN.match(linea)
                if r:
                    data = r.groupdict()
                    try:
                        monto = self._parse_monto(data["monto"])
                        # In Scotia: + is Charge, - is Payment?
                        # User script: signo = 1 if data["signo"] == "+" else -1
                        # Let's assume + is Charge.
                        final_type = "Cargo" if data["signo"] == "+" else "Abono"
                        
                        registros.append({
                            "fecha_oper": data["fecha_op"],
                            "fecha_liq": data["fecha_cargo"],
                            "descripcion": data["descripcion"],
                            "monto": monto,
                            "tipo": final_type,
                            "categoria": "Regular"
                        })
                    except ValueError:
                        pass

        print(f"DEBUG: Finished ScotiabankCreditParser. Found {len(registros)} records.")
        return pd.DataFrame(registros)


def get_parser(text, pdf_path=None):
    """Factory function to determine the correct parser based on text content."""
    text_upper = text.upper()
    
    print("DEBUG: Detecting parser...")
    print(f"DEBUG: Text start: {text_upper[:300]}") # Print first 300 chars to see headers
    
    # Check BBVA explicitly first
    if "BBVA" in text_upper or "BANCOMER" in text_upper:
        print("DEBUG: Detected BBVA/BANCOMER keywords.")
        if "DETALLE DE MOVIMIENTOS REALIZADOS" in text_upper:
            print("DEBUG: Detected 'DETALLE DE MOVIMIENTOS REALIZADOS' -> BBVADebitParser")
            return BBVADebitParser(text, pdf_path)
        print("DEBUG: Defaulting to BBVACreditParser")
        return BBVACreditParser(text, pdf_path)

    # Check Scotiabank
    # Make it stricter: Must have SCOTIABANK in text OR specific unique phrases
    # "DETALLE DE TUS MOVIMIENTOS" is too generic?
    # Scotiabank Debit has "DETALLE DE TUS MOVIMIENTOS".
    # BBVA Debit has "DETALLE DE MOVIMIENTOS REALIZADOS".
    
    is_scotiabank = False
    if "SCOTIABANK" in text_upper[:1000]: # Only check header to avoid transaction descriptions like "SPEI SCOTIABANK"
        is_scotiabank = True
    elif "INVERLAT" in text_upper:
        is_scotiabank = True
    elif "COMPRAS Y CARGOS DIFERIDOS" in text_upper: # Very specific to Credit
        is_scotiabank = True
    elif "DISTRIBUCIÓN DE TU ÚLTIMO PAGO" in text_upper: # Very specific to Credit
        is_scotiabank = True
        
    if is_scotiabank:
        print("DEBUG: Detected Scotiabank keywords.")
        
        # PRIORITIZE Credit/MSI detection
        if "COMPRAS Y CARGOS DIFERIDOS" in text_upper:
             print("DEBUG: Detected 'COMPRAS Y CARGOS DIFERIDOS' -> ScotiabankCreditParser")
             return ScotiabankCreditParser(text, pdf_path)
             
        if "DETALLE DE TUS MOVIMIENTOS" in text_upper:
             print("DEBUG: Detected 'DETALLE DE TUS MOVIMIENTOS' -> ScotiabankDebitParser")
             return ScotiabankDebitParser(text, pdf_path)
        
        # Fallback if we know it's Scotiabank but don't see specific section headers
        # Maybe check for "CUENTA UNICA" (Debit) vs "TARJETA DE CREDITO"
        if "CUENTA UNICA" in text_upper or "CUENTA DE DEPOSITO" in text_upper:
            print("DEBUG: Detected 'CUENTA UNICA/DEPOSITO' -> ScotiabankDebitParser")
            return ScotiabankDebitParser(text, pdf_path)

        print("DEBUG: Defaulting to ScotiabankCreditParser")
        return ScotiabankCreditParser(text, pdf_path)
        
    # Check Banorte
    if "BANORTE" in text_upper:
        print("DEBUG: Detected BANORTE keywords.")
        # For now, default to Credit as we only have BanorteCreditParser
        return BanorteCreditParser(text, pdf_path)
    
    print("DEBUG: No parser detected.")
    return None

# We do not import AI parsers here to avoid circular imports. 
# They will be instantiated directly in app.py based on user selection.




class ScotiabankDebitParser(BankParser):
    """Parser para estados de cuenta de débito Scotiabank usando análisis espacial."""

    # -------------------------------------------------------------------------
    # Métodos auxiliares
    # -------------------------------------------------------------------------

    def extract_account_number(self):
        """
        Intenta encontrar el número de cuenta en el texto completo del PDF.
        Normalmente viene como 'Cuenta: 123456789'.
        """
        m = re.search(r"Cuenta:?\s*(\d+)", self.text)
        if m:
            return m.group(1)
        return "SCOTIA-DEBIT-UNKNOWN"

    def _parse_monto(self, texto: str) -> float:
        """
        Convierte '11,185.21' o '$11,185.21' a float.
        Si no encuentra monto, devuelve 0.0.
        """
        if not texto:
            return 0.0
        texto = texto.replace("$", "").replace(" ", "")
        m = re.search(r"([\d,]+\.\d{2})", texto)
        if not m:
            return 0.0
        return float(m.group(1).replace(",", ""))

    def _asignar_lineas(self, words, y_tol: int = 3):
        """
        Agrupa palabras en líneas usando la coordenada vertical (top) con una
        tolerancia y_tol. Agrega la clave 'line_id' a cada word.
        """
        words = sorted(words, key=lambda w: w["top"])
        current_line = 0
        last_top = None
        for w in words:
            top = w["top"]
            if last_top is None or abs(top - last_top) > y_tol:
                current_line += 1
            w["line_id"] = current_line
            last_top = top
        return words

    def _obtener_limites_columnas(self, df_words: pd.DataFrame):
        """
        A partir de las palabras que corresponden a los encabezados,
        calcula el centro aproximado de cada columna y devuelve límites
        izquierda/derecha para asignar palabras.
        """
        headers_map = {
            "fecha": ["Fecha", "FECHA"],
            "concepto": ["Concepto", "CONCEPTO", "Descripcion", "DESCRIPCION"],
            "origen": ["Origen/Referencia", "Origen", "ORIGEN/REFERENCIA", "ORIGEN"],
            "deposito": ["Depósito", "Deposito", "DEPÓSITO", "DEPOSITO"],
            "retiro": ["Retiro", "RETIRO"],
            "saldo": ["Saldo", "SALDO"],
        }

        col_x = {}
        for col, keywords in headers_map.items():
            cand = df_words[df_words["text"].isin(keywords)]
            if not cand.empty:
                col_x[col] = cand["x0"].mean()
            else:
                # Debug opcional para ver qué no se encontró
                print(f"DEBUG: No se encontró columna {col} con keywords {keywords}")

        if len(col_x) < 5:
            print(f"DEBUG: Solo se detectaron {len(col_x)} columnas: {list(col_x.keys())}")
            print("DEBUG: Algunas palabras de esta zona:")
            print(df_words.head(15)[["text", "x0", "top"]].to_string(index=False))
            return None, None

        # Ordenamos columnas de izquierda a derecha
        sorted_items = sorted(col_x.items(), key=lambda kv: kv[1])
        col_names = [name for name, _ in sorted_items]
        xs = [x for _, x in sorted_items]

        left_bounds = []
        right_bounds = []
        for i, x in enumerate(xs):
            if i == 0:
                left_bounds.append(0)
            else:
                left_bounds.append((xs[i - 1] + x) / 2)

            if i == len(xs) - 1:
                right_bounds.append(10_000)  # algo suficientemente grande
            else:
                right_bounds.append((x + xs[i + 1]) / 2)

        limites = {
            "cols": left_bounds and col_names,
            "left": left_bounds,
            "right": right_bounds,
        }

        # Línea del encabezado (donde aparece "Fecha")
        header_lines = df_words[df_words["text"] == "Fecha"]["line_id"].unique()
        header_line = int(header_lines[0]) if len(header_lines) else 0
        return limites, header_line

    def _asignar_columna(self, x_center: float, limites: dict) -> str:
        """
        Devuelve el nombre de la columna en función de x_center y los límites calculados.
        """
        for name, l, r in zip(limites["cols"], limites["left"], limites["right"]):
            if l <= x_center < r:
                return name
        # fallback: última columna
        return limites["cols"][-1]

    # -------------------------------------------------------------------------
    # Parsing de una página
    # -------------------------------------------------------------------------

    def _extraer_movimientos_pagina(self, page):
        """
        Extrae la tabla 'Detalle de tus movimientos' de una página (si existe) y
        devuelve una lista de dicts crudos con columnas:
        fecha, concepto, origen, deposito, retiro, saldo.
        """
        import pdfplumber  # por si usas esta clase sin importar arriba

        # 1) Ver si la página es de movimientos
        texto = page.extract_text() or ""
        normalized = re.sub(r"\s+", "", texto).lower()
        # En el PDF suele aparecer como "Detalledetusmovimientos" pegado
        if "detalledetusmovimientos" not in normalized:
            return []

        # 2) Extraer palabras con coordenadas
        words = page.extract_words(
            x_tolerance=1,
            y_tolerance=3,
            keep_blank_chars=False,
        )
        if not words:
            return []

        words = self._asignar_lineas(words)
        df = pd.DataFrame(words)

        # 3) Detectar columnas y línea del encabezado
        limites, header_line = self._obtener_limites_columnas(df)
        if not limites:
            return []

        filas_raw = []

        # 4) Recorremos cada línea después del encabezado
        for line_id, line in df.groupby("line_id"):
            if line_id <= header_line:
                continue

            line_text_full = " ".join(line["text"].tolist())
            # Cortamos cuando empieza el bloque de notas
            if "LAS TASAS DE INTERES ESTAN EXPRESADAS" in line_text_full:
                break

            row = {c: "" for c in limites["cols"]}

            for _, w in line.iterrows():
                x_center = (w["x0"] + w["x1"]) / 2
                col = self._asignar_columna(x_center, limites)
                if row[col]:
                    row[col] += " " + w["text"]
                else:
                    row[col] = w["text"]

            # descartamos líneas completamente vacías
            if not any(row.values()):
                continue

            filas_raw.append(row)

        # 5) Unir líneas que pertenecen al mismo movimiento
        movimientos = []
        # En el PDF, la fecha viene como "24 SEP", "3 OCT", etc.
        fecha_regex = re.compile(r"^\d{1,2}\s+[A-ZÁÉÍÓÚÑ]{3}$")

        for row in filas_raw:
            fecha_txt = (row.get("fecha") or "").strip()

            if fecha_regex.match(fecha_txt):
                # Nueva operación
                movimientos.append(row)
            else:
                # Continuación de la descripción / origen de la operación anterior
                if not movimientos:
                    continue
                last = movimientos[-1]

                # concatenar concepto y origen
                for col in ["concepto", "origen"]:
                    extra = (row.get(col) or "").strip()
                    if extra:
                        last[col] = (last.get(col, "") + " " + extra).strip()

                # si alguna línea adicional trae algo en depósito/retiro/saldo
                for col in ["deposito", "retiro", "saldo"]:
                    extra = (row.get(col) or "").strip()
                    if extra and extra not in (last.get(col) or ""):
                        last[col] = (last.get(col, "") + " " + extra).strip()

        return movimientos

    # -------------------------------------------------------------------------
    # API pública: extraer movimientos en formato estándar
    # -------------------------------------------------------------------------

    def extract_movements(self) -> pd.DataFrame:
        """
        Recorre todas las páginas, extrae los movimientos y los normaliza a un
        DataFrame estándar con columnas:

        - fecha_oper
        - fecha_liq
        - descripcion
        - monto
        - tipo ("Abono"/"Cargo"/"Desconocido")
        - categoria ("Regular")
        - saldo_calculado
        """
        if not getattr(self, "pdf_path", None):
            raise ValueError("ScotiabankDebitParser requires pdf_path to be set.")

        import pdfplumber

        movimientos = []

        with pdfplumber.open(self.pdf_path) as pdf:
            for page in pdf.pages:
                movs_page = self._extraer_movimientos_pagina(page)
                movimientos.extend(movs_page)

        registros = []
        for m in movimientos:
            deposito = self._parse_monto(m.get("deposito", ""))
            retiro = self._parse_monto(m.get("retiro", ""))

            monto = 0.0
            tipo = "Desconocido"

            if deposito > 0:
                monto = deposito
                tipo = "Abono"
            elif retiro > 0:
                monto = retiro
                tipo = "Cargo"

            registros.append({
                "fecha_oper": m.get("fecha"),
                "fecha_liq": m.get("fecha"),  # en débito casi siempre es el mismo día
                "descripcion": f"{m.get('concepto', '')} {m.get('origen', '')}".strip(),
                "monto": monto,
                "tipo": tipo,
                "categoria": "Regular",
                "saldo_calculado": self._parse_monto(m.get("saldo", "")),
            })

        return pd.DataFrame(registros)


class BanorteCreditParser(BankParser):
    """Parser para estados de cuenta de Tarjeta de Crédito Banorte."""

    # --------------------- Utilidades ---------------------

    def extract_account_number(self):
        """
        Ejemplo en el estado:
        'Número de Cuenta: XXXX-XXXX-XXXX-2468'
        """
        m = re.search(r"N[uú]mero\s+de\s+Cuenta:\s*([X\d\- ]+)", self.text)
        if m:
            return m.group(1).strip()
        return "BANORTE-CREDIT-UNKNOWN"

    def _parse_monto(self, txt: str) -> float:
        if not txt:
            return 0.0
        txt = txt.replace("$", "").replace(",", "").strip()
        m = re.search(r"-?\d+\.\d{2}", txt)
        return float(m.group(0)) if m else 0.0

    # Líneas MSI tipo:
    # 25-NOV-2024 AMAZON $8,612.56 $0.00 $717.75 12/12 0.00%
    MSI_PATTERN = re.compile(
        r"^(?P<fecha>\d{2}-[A-Z]{3}-\d{4})\s+"
        r"(?P<desc>.+?)\s+\$(?P<monto_original>[\d,]+\.\d{2})\s+"
        r"\$(?P<saldo_pendiente>[\d,]+\.\d{2})\s+"
        r"\$(?P<pago_requerido>[\d,]+\.\d{2})\s+"
        r"(?P<num_pago>\d+/\d+)\s+"
        r"(?P<tasa>[\d\.]+)%"
    )

    # Líneas regulares simples tipo:
    # 11-OCT-2025 13-OCT-2025 STARBUCKS ... +$133.00
    REGULAR_PATTERN = re.compile(
        r"^(?P<fecha_op>\d{2}-[A-Z]{3}-\d{4})\s+"
        r"(?P<fecha_cargo>\d{2}-[A-Z]{3}-\d{4})\s+"
        r"(?P<desc>.+?)\s+"
        r"(?P<signo>[+-])\s*\$(?P<monto>[\d,]+\.\d{2})$"
    )

    def _parse_msi_section(self, lines):
        registros = []
        for line in lines:
            m = self.MSI_PATTERN.match(line.strip())
            if not m:
                continue
            g = m.groupdict()
            registros.append({
                "fecha_oper": g["fecha"],
                "fecha_liq": g["fecha"],
                "descripcion": f"{g['desc']} ({g['num_pago']})",
                "monto": self._parse_monto(g["pago_requerido"]),   # lo que pega al saldo este mes
                "tipo": "Cargo",
                "categoria": "MSI",
                "meta_monto_original": self._parse_monto(g["monto_original"]),
                "meta_saldo_pendiente": self._parse_monto(g["saldo_pendiente"]),
                "meta_tasa": g["tasa"],
            })
        return registros

    def _parse_regular_section(self, lines):
        """
        Nota: aquí se parsean solo las filas de una línea.
        Los movimientos multi-línea (como el SPEI largo) se podrían
        mejorar juntando líneas, pero para empezar esto ya te da
        casi todo lo de este estado.
        """
        registros = []
        buffer = []

        for raw in lines:
            line = raw.strip()
            if not line:
                continue

            # Si matchea directamente: flush buffer previo y procesa
            m = self.REGULAR_PATTERN.match(line)
            if m:
                # si había texto acumulado (caso multi-línea anterior), lo ignoramos de momento
                buffer = []
                g = m.groupdict()
                monto = self._parse_monto(g["monto"])
                signo = g["signo"]
                tipo = "Abono" if signo == "-" else "Cargo"

                registros.append({
                    "fecha_oper": g["fecha_op"],
                    "fecha_liq": g["fecha_cargo"],
                    "descripcion": g["desc"],
                    "monto": monto,
                    "tipo": tipo,
                    "categoria": "Regular",
                })
            else:
                # Podríamos ir acumulando para mejorar SPEI, etc.
                buffer.append(line)

        return registros

    def extract_movements(self) -> pd.DataFrame:
        """
        Recorre el texto completo y detecta:
        - Secciones MSI
        - Secciones CARGOS, ABONOS Y COMPRAS REGULARES (NO A MESES)
        Devuelve un DataFrame estándar.
        """
        lineas = self.text.splitlines()
        registros = []

        en_msi = False
        en_reg = False
        buffer_sec = []

        def flush_section():
            nonlocal buffer_sec, registros, en_msi, en_reg
            if en_msi:
                registros.extend(self._parse_msi_section(buffer_sec))
            elif en_reg:
                registros.extend(self._parse_regular_section(buffer_sec))
            buffer_sec = []

        for line in lineas:
            l = line.strip()

            # Detectar inicio de secciones
            if "COMPRAS Y CARGOS DIFERIDOS A MESES SIN INTERESES" in l:
                flush_section()
                en_msi, en_reg = True, False
                continue

            if "CARGOS, ABONOS Y COMPRAS REGULARES (NO A MESES)" in l:
                flush_section()
                en_msi, en_reg = False, True
                continue

            # Fin de secciones
            if l.startswith("Total cargos") or l.startswith("Total abonos") \
               or "ATENCIÓN DE QUEJAS" in l or "Notas:" in l:
                flush_section()
                en_msi, en_reg = False, False
                continue

            # Saltar encabezados de tablas
            if "Fecha de" in l and "operación" in l:
                continue
            if "Descripción del movimiento" in l and "Monto" in l:
                continue
            if "Tarjeta titular" in l or "Tarjeta adicional" in l:
                continue

            # Acumular solo si estamos dentro de alguna sección
            if en_msi or en_reg:
                buffer_sec.append(l)

        # Flush final
        flush_section()

        df = pd.DataFrame(registros)
        if df.empty:
            return df

        # Ordenar columnas
        base_cols = ["fecha_oper", "fecha_liq", "descripcion", "monto", "tipo", "categoria"]
        other_cols = [c for c in df.columns if c not in base_cols]
        return df[base_cols + other_cols]

        
def get_parser(text, pdf_path=None):
    """Factory function to determine the correct parser based on text content."""
    text_upper = text.upper()
    
    # Check Scotiabank first (as it might contain 'BBVA' in transactions)
    if "SCOTIABANK" in text_upper or "DISTRIBUCIÓN DE TU ÚLTIMO PAGO" in text_upper or "COMPRAS Y CARGOS DIFERIDOS" in text_upper or "DETALLE DE TUS MOVIMIENTOS" in text_upper:
        # Distinguish Credit vs Debit
        # Credit usually has "COMPRAS Y CARGOS DIFERIDOS"
        # Debit has "DETALLE DE TUS MOVIMIENTOS" and "DEPOSITO" / "RETIRO" columns
        if "DETALLE DE TUS MOVIMIENTOS" in text_upper:
             return ScotiabankDebitParser(text, pdf_path)
        return ScotiabankCreditParser(text, pdf_path)
        
    elif "BBVA" in text_upper:
        if "DETALLE DE MOVIMIENTOS REALIZADOS" in text_upper: # Strong indicator for the debit format we have
            return BBVADebitParser(text, pdf_path)
        return BBVACreditParser(text, pdf_path)
        
    elif "BANORTE" in text_upper:
        return BanorteParser(text, pdf_path)
    
    # Default fallback or error
    return None
