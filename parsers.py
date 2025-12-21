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
        # Remover símbolos de moneda y comas
        clean = txt.replace("$", "").replace(",", "").strip()
        # Buscar el número (puede ser negativo)
        m = re.search(r"[-]?\d+\.\d{2}", clean)
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

    # Inicio de movimiento regular: Fecha Op + Fecha Cargo
    # Ejemplo: 12-NOV-2025 13-NOV-2025 ...
    REGULAR_START_PATTERN = re.compile(r"^\d{2}-[A-Z]{3}-\d{4}\s+\d{2}-[A-Z]{3}-\d{4}")
    
    # Monto con signo: +$13.00 o -$12,855.46
    AMOUNT_PATTERN = re.compile(r"[+-]\$[\d,]+\.\d{2}")

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
                "monto": self._parse_monto(g["pago_requerido"]),
                "tipo": "Cargo MSI Informativo", # Marcado para no sumar doble
                "categoria": "MSI",
                "meta_monto_original": self._parse_monto(g["monto_original"]),
                "meta_saldo_pendiente": self._parse_monto(g["saldo_pendiente"]),
                "meta_tasa": g["tasa"],
            })
        return registros

    def _parse_regular_section(self, lines):
        """
        Parsea movimientos regulares manejando múltiples líneas.
        Busca fecha de inicio y luego busca el monto en esa línea o las siguientes.
        """
        registros = []
        current_mov = None

        def flush_mov():
            nonlocal current_mov
            if not current_mov:
                return
            
            # Procesar el movimiento acumulado
            full_desc = " ".join(current_mov["desc_lines"]).strip()
            monto_str = current_mov["amount_str"]
            
            if monto_str:
                monto = self._parse_monto(monto_str)
                # Determinar tipo basado en el signo en el texto (+ o -)
                tipo = "Abono" if "-" in monto_str else "Cargo"
                monto_abs = abs(monto)
                
                # Extraer fechas de la primera línea
                fechas = current_mov["date_line"].split()[:2] # Asumimos las 2 primeras palabras son fechas
                fecha_op = fechas[0]
                fecha_liq = fechas[1] if len(fechas) > 1 else fecha_op

                registros.append({
                    "fecha_oper": fecha_op,
                    "fecha_liq": fecha_liq,
                    "descripcion": full_desc,
                    "monto": monto_abs,
                    "tipo": tipo,
                    "categoria": "Regular",
                })
            current_mov = None

        for raw in lines:
            line = raw.strip()
            if not line:
                continue

            # Checar si es inicio de nuevo movimiento
            if self.REGULAR_START_PATTERN.match(line):
                flush_mov() # Guardar el anterior
                current_mov = {
                    "date_line": line,
                    "desc_lines": [],
                    "amount_str": None
                }
                
                # Buscar monto en la misma línea
                amt = self.AMOUNT_PATTERN.search(line)
                if amt:
                    current_mov["amount_str"] = amt.group(0)
                    desc_part = line.replace(amt.group(0), "")
                    current_mov["desc_lines"].append(desc_part)
                else:
                    # No monto aun, todo es parte de la linea inicial (fechas + desc parcial)
                    current_mov["desc_lines"].append(line)
            
            elif current_mov:
                # Continuación de movimiento
                # Si no tenemos monto, buscarlo
                if not current_mov["amount_str"]:
                    amt = self.AMOUNT_PATTERN.search(line)
                    if amt:
                        current_mov["amount_str"] = amt.group(0)
                        desc_part = line.replace(amt.group(0), "")
                        current_mov["desc_lines"].append(desc_part)
                    else:
                        current_mov["desc_lines"].append(line)
                else:
                    # Ya tenemos monto, esto es más descripción (ej. referencia, clabe)
                    current_mov["desc_lines"].append(line)
        
        flush_mov() # Flush final
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
            # "Total cargos" marca el fin de la sección regular usualmente
            if l.startswith("Total cargos") or l.startswith("Total abonos") \
               or "ATENCIÓN DE QUEJAS" in l:
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

    # ==========================================
    # VALIDACIÓN Y HEADER
    # ==========================================

    def _parse_header(self) -> dict:
        """Extrae totales del encabezado para validación."""
        text = self.text
        
        def find(pattern):
            m = re.search(pattern, text, re.IGNORECASE)
            return m.group(1).strip() if m else None
            
        def money(val):
            return self._parse_monto(val) if val else 0.0

        # Buscar totales explícitos al final de la sección regular
        # Patron más flexible: "Total cargos" seguido de signo opcional, espacio y monto
        total_cargos_match = re.search(r"Total\s+cargos\s*[+]?\s*\$?([\d,]+\.\d{2})", text, re.IGNORECASE)
        total_abonos_match = re.search(r"Total\s+abonos\s*[-]?\s*\$?([\d,]+\.\d{2})", text, re.IGNORECASE)
        
        # Buscar saldo anterior (Adeudo del periodo anterior)
        saldo_ant_match = re.search(r"Adeudo\s+del\s+periodo\s+anterior\s*\$?([\d,]+\.\d{2})", text, re.IGNORECASE)
        
        # Pago para no generar intereses (puede tener superíndice 2)
        saldo_corte_match = re.search(r"Pago\s+para\s+no\s+generar\s+intereses\s*:?\s*\d?\s*\$?([\d,]+\.\d{2})", text, re.IGNORECASE)

        header = {
            "periodo": find(r"Periodo\s*:\s*([^\n]+)"),
            "fecha_corte": find(r"Fecha\s+de\s+corte\s*:\s*(\d{2}-[A-Z]{3}-\d{4})"),
            "no_cuenta": self.extract_account_number(),
            
            # Totales
            "saldo_anterior": money(saldo_ant_match.group(1)) if saldo_ant_match else 0.0,
            "pagos_abonos": money(total_abonos_match.group(1)) if total_abonos_match else 0.0,
            "compras_cargos": money(total_cargos_match.group(1)) if total_cargos_match else 0.0,
            "saldo_actual": money(saldo_corte_match.group(1)) if saldo_corte_match else 0.0,
        }
        return header

    def _validation_report(self, header: dict, df: pd.DataFrame, tol: float = 0.05) -> dict:
        """Genera reporte de validación comparando totales extraídos vs header."""
        # Filtrar solo movimientos regulares para la suma de cargos (excluir MSI informativos)
        # Los MSI informativos tienen tipo "Cargo MSI Informativo"
        
        cargos_df = df[df["tipo"] == "Cargo"]
        abonos_df = df[df["tipo"] == "Abono"]
        
        sum_cargos = float(cargos_df["monto"].sum()) if not cargos_df.empty else 0.0
        sum_abonos = float(abonos_df["monto"].sum()) if not abonos_df.empty else 0.0
        
        resumen_cargos = header.get("compras_cargos") or 0.0
        resumen_abonos = header.get("pagos_abonos") or 0.0
        
        # Validación de saldo: Saldo Anterior - Pagos + Compras = Saldo Actual
        saldo_ant = header.get("saldo_anterior") or 0.0
        saldo_act = header.get("saldo_actual") or 0.0
        saldo_calc = saldo_ant - resumen_abonos + resumen_cargos
        
        return {
            "tipo": "TDC",
            "controles": {
                "cargos_vs_resumen_ok": abs(sum_cargos - resumen_cargos) <= tol,
                "abonos_vs_resumen_ok": abs(sum_abonos - resumen_abonos) <= tol,
                "balance_header_ok": abs(saldo_calc - saldo_act) <= tol
            },
            "detalles": {
                "sum_cargos_df": sum_cargos,
                "resumen_cargos": resumen_cargos,
                "diff_cargos": sum_cargos - resumen_cargos,
                "sum_abonos_df": sum_abonos,
                "resumen_abonos": resumen_abonos,
                "diff_abonos": sum_abonos - resumen_abonos,
                "saldo_anterior": saldo_ant,
                "saldo_actual": saldo_act,
                "saldo_calculado_header": saldo_calc
            }
        }

    def parse(self):
        """Ejecuta el parsing completo y retorna resultado con metadata."""
        self.header = self._parse_header()
        df = self.extract_movements()
        report = self._validation_report(self.header, df)
        
        return {
            "account_number": self.extract_account_number(),
            "movements": df,
            "metadata": {
                "account_type": "TDC",
                "header": self.header,
                "validation": report
            }
        }

    # ==========================================
    # VALIDACIÓN Y HEADER
    # ==========================================

    def _parse_header(self) -> dict:
        """Extrae totales del encabezado para validación."""
        text = self.text
        
        def find(pattern):
            m = re.search(pattern, text, re.IGNORECASE)
            return m.group(1).strip() if m else None
            
        def money(val):
            return self._parse_monto(val)

        # Patrones comunes en Banorte (ajustar según ejemplos reales)
        # Buscamos en las primeras líneas o en el resumen
        
        header = {
            "periodo": find(r"Periodo\s*:\s*([^\n]+)"),
            "fecha_corte": find(r"Fecha\s*de\s*Corte\s*:\s*(\d{2}-[A-Z]{3}-\d{4})"),
            "no_cuenta": self.extract_account_number(),
            # Resumen de saldos
            "saldo_anterior": money(find(r"Saldo\s*Anterior\s*\$([\d,]+\.\d{2})")),
            "pagos_abonos": money(find(r"Pagos\s*y\s*Abonos\s*[-]?\s*\$([\d,]+\.\d{2})")),
            "compras_cargos": money(find(r"Compras\s*y\s*Cargos\s*\$([\d,]+\.\d{2})")),
            "saldo_actual": money(find(r"Saldo\s*Actual\s*\$([\d,]+\.\d{2})")),
        }
        return header

    def _validation_report(self, header: dict, df: pd.DataFrame, tol: float = 0.05) -> dict:
        """Genera reporte de validación comparando totales extraídos vs header."""
        sum_cargos = float(df[df["tipo"] == "Cargo"]["monto"].sum()) if not df.empty else 0.0
        sum_abonos = float(df[df["tipo"] == "Abono"]["monto"].sum()) if not df.empty else 0.0
        
        resumen_cargos = header.get("compras_cargos") or 0.0
        resumen_abonos = header.get("pagos_abonos") or 0.0
        
        # Validación de saldo: Saldo Anterior - Pagos + Compras = Saldo Actual
        saldo_ant = header.get("saldo_anterior") or 0.0
        saldo_act = header.get("saldo_actual") or 0.0
        saldo_calc = saldo_ant - resumen_abonos + resumen_cargos
        
        return {
            "tipo": "TDC",
            "controles": {
                "cargos_vs_resumen_ok": abs(sum_cargos - resumen_cargos) <= tol,
                "abonos_vs_resumen_ok": abs(sum_abonos - resumen_abonos) <= tol,
                "balance_header_ok": abs(saldo_calc - saldo_act) <= tol
            },
            "detalles": {
                "sum_cargos_df": sum_cargos,
                "resumen_cargos": resumen_cargos,
                "diff_cargos": sum_cargos - resumen_cargos,
                "sum_abonos_df": sum_abonos,
                "resumen_abonos": resumen_abonos,
                "diff_abonos": sum_abonos - resumen_abonos,
                "saldo_anterior": saldo_ant,
                "saldo_actual": saldo_act,
                "saldo_calculado_header": saldo_calc
            }
        }

    def parse(self):
        """Ejecuta el parsing completo y retorna resultado con metadata."""
        self.header = self._parse_header()
        df = self.extract_movements()
        report = self._validation_report(self.header, df)
        
        return {
            "account_number": self.extract_account_number(),
            "movements": df,
            "metadata": {
                "account_type": "TDC",
                "header": self.header,
                "validation": report
            }
        }

        
# ==========================================
# ScotiabankV2Parser - Parser mejorado basado en main_scotia.py
# Soporta tanto TDC (Tarjeta de Crédito) como CHECKING (Cuenta de Cheques)
# ==========================================

class ScotiabankV2Parser(BankParser):
    """
    Parser mejorado para estados de cuenta Scotiabank.
    Detecta automáticamente el tipo de cuenta (TDC o CHECKING) y aplica
    el parser correspondiente. Basado en main_scotia.py.
    """
    
    # Configuración
    MONTHS_ES = {
        "ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6,
        "JUL": 7, "AGO": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DIC": 12,
        "Ene": 1, "Feb": 2, "Mar": 3, "Abr": 4, "May": 5, "Jun": 6,
        "Jul": 7, "Ago": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dic": 12
    }
    
    DATE_RE = re.compile(r"^(?P<day>\d{2})\s+(?P<mon>ENE|FEB|MAR|ABR|MAY|JUN|JUL|AGO|SEP|OCT|NOV|DIC)\b", re.IGNORECASE)
    MONEY_RE = re.compile(r"\$[\d,]+\.\d{2}")

    def __init__(self, text, pdf_path=None):
        super().__init__(text, pdf_path)
        self.lines = self._extract_lines()
        self.account_type = self._detect_account_type()
        self.header = {}
        self.validation_report = {}

    def _extract_lines(self):
        """Extrae líneas del PDF usando pdfplumber."""
        if not self.pdf_path:
            # Fallback: usar el texto ya extraído
            return [ln.strip() for ln in self.text.splitlines() if ln.strip()]
        
        import pdfplumber
        lines = []
        with pdfplumber.open(self.pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                for ln in text.splitlines():
                    if ln.strip():
                        lines.append(ln.strip())
        return lines

    def _money_to_float(self, value: str) -> float:
        """Convierte '$301,515.28' a 301515.28"""
        if not value:
            return 0.0
        return float(str(value).replace("$", "").replace(",", "").strip())

    def _almost_equal(self, a, b, tol: float = 0.05) -> bool:
        """Compara dos floats con tolerancia."""
        if a is None or b is None:
            return False
        return abs(a - b) <= tol

    # ==========================================
    # DETECTOR DE TIPO DE CUENTA
    # ==========================================

    def _detect_account_type(self) -> str:
        """Detecta si es TDC (Tarjeta de Crédito) o CHECKING (Cuenta de Cheques)."""
        text = "\n".join(self.lines[:50]).upper()
        
        if re.search(r"L[ÍI]MITE\s*DE\s*CR[ÉE]DITO", text) or \
           re.search(r"PAGO\s*M[ÍI]NIMO", text) or \
           re.search(r"TARJETA\s*DE\s*CR[ÉE]DITO", text):
            return "TDC"
        
        if re.search(r"CLABE", text) and re.search(r"SALDO\s*INICIAL", text):
            return "CHECKING"
            
        return "UNKNOWN"

    # ==========================================
    # EXTRACCIÓN DE CUENTA
    # ==========================================

    def extract_account_number(self):
        """Extrae el número de cuenta del estado."""
        text = "\n".join(self.lines)
        
        # Para CHECKING: buscar "Cuenta 123456789"
        m = re.search(r"Cuenta\s+(\d+)", text)
        if m:
            return m.group(1)
        
        # Para TDC: buscar "No. Tarjeta 1234567890"
        m = re.search(r"No\.\s*Tarjeta\s+(\d+)", text)
        if m:
            return m.group(1)
        
        # Fallback CLABE
        m = re.search(r"CLABE\s+(\d{18})", text)
        if m:
            return m.group(1)
            
        return "SCOTIA-UNKNOWN"

    # ==========================================
    # PARSER: CUENTA DE CHEQUES (CHECKING)
    # ==========================================

    def _parse_checking_header(self) -> dict:
        """Extrae información del encabezado para cuentas de cheques."""
        text = "\n".join(self.lines)

        def find(pattern, flags=0):
            m = re.search(pattern, text, flags)
            return m.group(1).strip() if m else None

        header = {
            "cuenta": find(r"Cuenta\s+(\d+)"),
            "clabe": find(r"CLABE\s+(\d{18})"),
            "fecha_corte": find(r"Fechadecorte\s+([0-9]{2}-[A-Z]{3}-[0-9]{2})"),
            "periodo": find(r"Periodo\s+([0-9]{2}-[A-Z]{3}-[0-9]{2}/[0-9]{2}-[A-Z]{3}-[0-9]{2})"),
            "moneda": find(r"Moneda\s+([A-Z]+)"),
            "saldo_inicial": find(r"Saldo\s*inicial(?:\s*=)?\s+\$([\d,]+\.\d{2})"),
            "depositos": find(r"\(\+\)\s*Depósitos\s+\$([\d,]+\.\d{2})"),
            "retiros": find(r"\(-\)\s*Retiros\s+\$([\d,]+\.\d{2})"),
            "saldo_final": find(r"(?:\(=\)\s*)?Saldofinal(?:delacuenta)?\s*(?:=)?\s*\$([\d,]+\.\d{2})"),
        }

        for k in ["saldo_inicial", "depositos", "retiros", "saldo_final"]:
            if header[k] is not None:
                header[k] = self._money_to_float(header[k])

        return header

    def _classify_amount_checking(self, amount: float, concept: str):
        """Clasifica un monto como depósito o retiro basado en el concepto."""
        c = (concept or "").upper()
        if any(w in c for w in ["PAGO", "RETIRO", "CARGO", "TRANSFERENCIA A", "COMISION", "INTERES"]):
            return None, amount
        if any(w in c for w in ["DEPOS", "DEPÓS", "ABONO", "NOMINA", "TRANSFERENCIA DE", "TRASPASO DE"]):
            return amount, None
        return None, None

    def _parse_checking_movements(self, start_balance: float = None) -> pd.DataFrame:
        """Parsea movimientos de cuenta de cheques."""
        from datetime import datetime
        
        text = "\n".join(self.lines)
        m = re.search(r"Fechadecorte\s+\d{2}-[A-Z]{3}-(\d{2})", text)
        year = 2000 + int(m.group(1)) if m else datetime.now().year

        movements = []
        current = None
        last_saldo = start_balance

        def flush():
            nonlocal current, last_saldo
            if not current:
                return

            concept = " ".join(current["concept"]).strip()
            amounts = current["amounts"]
            deposito = retiro = monto_sin_clasificar = saldo = None

            found_match = False
            if last_saldo is not None and len(amounts) >= 2:
                for i in range(len(amounts) - 1):
                    pos_monto = amounts[i]
                    pos_saldo = amounts[i+1]
                    if abs(last_saldo - pos_monto - pos_saldo) < 0.05:
                        retiro, saldo, found_match = pos_monto, pos_saldo, True
                        break
                    if abs(last_saldo + pos_monto - pos_saldo) < 0.05:
                        deposito, saldo, found_match = pos_monto, pos_saldo, True
                        break
            
            if not found_match:
                if len(amounts) >= 2:
                    if len(amounts) >= 3 and amounts[-1] == 0.0:
                        monto_cand, saldo_cand = amounts[-3], amounts[-2]
                    else:
                        monto_cand, saldo_cand = amounts[-2], amounts[-1]
                    
                    saldo = saldo_cand
                    d_heur, r_heur = self._classify_amount_checking(monto_cand, concept)
                    if d_heur:
                        deposito = d_heur
                    if r_heur:
                        retiro = r_heur
                    if not deposito and not retiro:
                        monto_sin_clasificar = monto_cand
                elif len(amounts) == 1:
                    monto_sin_clasificar = amounts[0]

            if saldo is not None:
                last_saldo = saldo

            ref = None
            refs = re.findall(r"\b\d{10,}\b", concept)
            if refs:
                ref = refs[0]

            movements.append({
                "fecha": current["fecha"],
                "concepto": concept,
                "referencia": ref,
                "deposito": deposito,
                "retiro": retiro,
                "monto_sin_clasificar": monto_sin_clasificar,
                "saldo": saldo
            })
            current = None

        for ln in self.lines:
            up = ln.upper()
            if up.startswith(("DETALLE DE TUS MOVIMIENTOS", "FECHA CONCEPTO", "PAGINA", "SCOTIABANK")):
                continue

            md = self.DATE_RE.match(ln)
            if md:
                flush()
                try:
                    fecha = datetime(year, self.MONTHS_ES[md.group("mon").upper()], int(md.group("day"))).date().isoformat()
                except:
                    fecha = ln.split()[0]
                
                current = {
                    "fecha": fecha,
                    "concept": [ln],
                    "amounts": [self._money_to_float(x) for x in self.MONEY_RE.findall(ln)]
                }
            else:
                if current:
                    current["concept"].append(ln)
                    current["amounts"].extend(self._money_to_float(x) for x in self.MONEY_RE.findall(ln))

        flush()
        return pd.DataFrame(movements)

    def _validation_report_checking(self, header: dict, df: pd.DataFrame, tol: float = 0.02) -> dict:
        """Genera reporte de validación para cuenta de cheques."""
        sum_dep = float(df["deposito"].fillna(0).sum()) if "deposito" in df else 0.0
        sum_ret = float(df["retiro"].fillna(0).sum()) if "retiro" in df else 0.0
        
        saldo_inicial = header.get("saldo_inicial") or 0.0
        dep_resumen = header.get("depositos") or 0.0
        ret_resumen = header.get("retiros") or 0.0
        saldo_final = header.get("saldo_final") or 0.0

        expected_final = saldo_inicial + dep_resumen - ret_resumen
        
        last_saldo = None
        if "saldo" in df.columns and df["saldo"].notna().any():
            last_saldo = float(df.loc[df["saldo"].notna(), "saldo"].iloc[-1])

        return {
            "tipo": "CHECKING",
            "controles": {
                "depositos_ok": self._almost_equal(sum_dep, dep_resumen, tol),
                "retiros_ok": self._almost_equal(sum_ret, ret_resumen, tol),
                "saldo_final_calc_ok": self._almost_equal(expected_final, saldo_final, tol),
                "saldo_final_df_ok": self._almost_equal(last_saldo, saldo_final, tol)
            },
            "detalles": {
                "sum_dep_df": sum_dep,
                "sum_ret_df": sum_ret,
                "dep_resumen": dep_resumen,
                "ret_resumen": ret_resumen,
                "diff_dep": sum_dep - dep_resumen,
                "diff_ret": sum_ret - ret_resumen
            }
        }

    # ==========================================
    # PARSER: TARJETA DE CRÉDITO (TDC)
    # ==========================================

    def _parse_tdc_header(self) -> dict:
        """Extrae información del encabezado para tarjeta de crédito."""
        text = "\n".join(self.lines[:100])  # Resumen suele estar al principio
        full_text = "\n".join(self.lines)   # Para buscar totales que pueden estar más abajo
        
        def find(pattern, search_text=text):
            m = re.search(pattern, search_text, re.IGNORECASE)
            return m.group(1).strip() if m else None

        pagos_abonos = find(r"Pagos\s*y\s*abonos\s*[-–]?\s*\$([\d,]+\.\d{2})")
        cargos_regulares = find(r"Cargos\s*regulares.*?\+\s*\$([\d,]+\.\d{2})")
        cargos_meses = find(r"Cargos.*?\s*meses.*?\+\s*\$([\d,]+\.\d{2})")
        
        # Buscar totales en todo el documento
        total_cargos_final = find(r"Total\s*cargos\s*\+\s*\$([\d,]+\.\d{2})", full_text) or "0"
        total_abonos_final = find(r"Total\s*abonos\s*[-–]?\s*\$([\d,]+\.\d{2})", full_text) or "0"
        
        header = {
            "periodo": find(r"Periodo:\s*([^\n]+)"),
            "fecha_corte": find(r"Fecha\s*de\s*corte:\s*(\d{2}-[a-z]{3}-\d{4})"),
            "no_tarjeta": find(r"No\.\s*Tarjeta\s*(\d+)"),
            "saldo_deudor_total": find(r"Saldo\s*deudor\s*total:\s*\$([\d,]+\.\d{2})"),
            "pago_no_intereses": find(r"Pago\s*para\s*no\s*generar\s*intereses:\s*\d*\s*\$([\d,]+\.\d{2})"),
            "resumen_pagos_abonos": self._money_to_float(pagos_abonos or total_abonos_final),
            "resumen_cargos_total": self._money_to_float(total_cargos_final)
        }
        
        if header["resumen_cargos_total"] == 0:
            c1 = self._money_to_float(cargos_regulares)
            c2 = self._money_to_float(cargos_meses)
            header["resumen_cargos_total"] = c1 + c2

        return header

    def _parse_tdc_movements(self) -> pd.DataFrame:
        """Parsea movimientos de tarjeta de crédito."""
        movements = []
        capture = False
        
        # Regex más flexible para línea de movimiento TDC
        mv_re = re.compile(
            r"^(?P<f_ops>\d{2}-[a-z]{3}-\d{4})\s+(?P<f_carg>\d{2}-[a-z]{3}-\d{4})\s+(?P<desc>.+?)\s*(?P<signo>[-+])\s*\$(?P<monto>[\d,]+\.\d{2})$",
            re.IGNORECASE
        )

        for ln in self.lines:
            # Detección de sección robusta (ignorar espacios internos que pdfplumber a veces elimina)
            normalized_ln = ln.upper().replace(" ", "")
            
            if "CARGOS,ABONOSYCOMPRASREGULARES" in normalized_ln:
                capture = True
                continue
            
            # Fin de sección: "ATENCIÓN DE QUEJAS" suele estar al final
            if "ATENCIÓN" in normalized_ln and "QUEJAS" in normalized_ln:
                capture = False
            
            if not capture:
                continue
            
            # Filtro rápido: debe empezar con número (fecha) y tener $
            if not ln or not ln[0].isdigit() or "$" not in ln:
                continue
                
            m = mv_re.match(ln)
            if m:
                d = m.groupdict()
                monto = self._money_to_float(d["monto"])
                signo = d["signo"]
                tipo = "Abono" if signo == "-" else "Cargo"
                
                movements.append({
                    "fecha_operacion": d["f_ops"],
                    "fecha_cargo": d["f_carg"],
                    "descripcion": d["desc"].strip(),
                    "monto": monto,
                    "tipo": tipo,
                    "abono": monto if tipo == "Abono" else None,
                    "cargo": monto if tipo == "Cargo" else None
                })

        return pd.DataFrame(movements)

    def _validation_report_tdc(self, header: dict, df: pd.DataFrame, tol: float = 0.05) -> dict:
        """Genera reporte de validación para tarjeta de crédito."""
        sum_cargos = float(df["cargo"].fillna(0).sum()) if "cargo" in df.columns else 0.0
        sum_abonos = float(df["abono"].fillna(0).sum()) if "abono" in df.columns else 0.0
        
        resumen_cargos = header.get("resumen_cargos_total") or 0.0
        resumen_abonos = header.get("resumen_pagos_abonos") or 0.0

        return {
            "tipo": "TDC",
            "controles": {
                "cargos_vs_resumen_ok": self._almost_equal(sum_cargos, resumen_cargos, tol),
                "abonos_vs_resumen_ok": self._almost_equal(sum_abonos, resumen_abonos, tol),
            },
            "detalles": {
                "sum_cargos_df": sum_cargos,
                "resumen_cargos": resumen_cargos,
                "diff_cargos": sum_cargos - resumen_cargos,
                "sum_abonos_df": sum_abonos,
                "resumen_abonos": resumen_abonos,
                "diff_abonos": sum_abonos - resumen_abonos,
            }
        }

    # ==========================================
    # API PÚBLICA: extract_movements y parse
    # ==========================================

    def extract_movements(self) -> pd.DataFrame:
        """Extrae movimientos según el tipo de cuenta detectado."""
        if self.account_type == "CHECKING":
            self.header = self._parse_checking_header()
            df_raw = self._parse_checking_movements(start_balance=self.header.get("saldo_inicial"))
            self.validation_report = self._validation_report_checking(self.header, df_raw)
            
            # Normalizar a formato estándar
            registros = []
            for _, row in df_raw.iterrows():
                monto = row.get("deposito") or row.get("retiro") or row.get("monto_sin_clasificar") or 0.0
                tipo = "Abono" if row.get("deposito") else ("Cargo" if row.get("retiro") else "Desconocido")
                
                registros.append({
                    "fecha_oper": row.get("fecha"),
                    "fecha_liq": row.get("fecha"),
                    "descripcion": row.get("concepto", ""),
                    "monto": monto,
                    "tipo": tipo,
                    "categoria": "Regular",
                    "saldo_calculado": row.get("saldo"),
                })
            return pd.DataFrame(registros)
            
        elif self.account_type == "TDC":
            self.header = self._parse_tdc_header()
            df_raw = self._parse_tdc_movements()
            self.validation_report = self._validation_report_tdc(self.header, df_raw)
            
            # Normalizar a formato estándar
            registros = []
            for _, row in df_raw.iterrows():
                registros.append({
                    "fecha_oper": row.get("fecha_operacion"),
                    "fecha_liq": row.get("fecha_cargo"),
                    "descripcion": row.get("descripcion", ""),
                    "monto": row.get("monto", 0.0),
                    "tipo": row.get("tipo", "Desconocido"),
                    "categoria": "Regular",
                    "saldo_calculado": None,
                })
            return pd.DataFrame(registros)
        else:
            print(f"Tipo de cuenta no soportado: {self.account_type}")
            return pd.DataFrame()

    def parse(self):
        """Ejecuta el parsing completo y retorna resultado con metadata."""
        result = super().parse()
        result["metadata"] = {
            "account_type": self.account_type,
            "header": self.header,
            "validation": self.validation_report
        }
        return result


def get_parser(text, pdf_path=None):
    """Factory function to determine the correct parser based on text content."""
    text_upper = text.upper()
    
    # Check Scotiabank first - usar el nuevo parser V2 por defecto
    if "SCOTIABANK" in text_upper or "DISTRIBUCIÓN DE TU ÚLTIMO PAGO" in text_upper or "COMPRAS Y CARGOS DIFERIDOS" in text_upper or "DETALLE DE TUS MOVIMIENTOS" in text_upper:
        # Usar ScotiabankV2Parser que detecta automáticamente TDC vs CHECKING
        return ScotiabankV2Parser(text, pdf_path)
        
    elif "BBVA" in text_upper:
        if "DETALLE DE MOVIMIENTOS REALIZADOS" in text_upper:
            return BBVADebitParser(text, pdf_path)
        return BBVACreditParser(text, pdf_path)
        
    elif "BANORTE" in text_upper:
        return BanorteCreditParser(text, pdf_path)
    
    return None
