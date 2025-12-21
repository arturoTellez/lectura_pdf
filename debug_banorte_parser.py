import re
import pandas as pd

class BanorteCreditParser:
    def __init__(self):
        self.text = ""

    def _parse_monto(self, txt: str) -> float:
        if not txt:
            return 0.0
        # Remover símbolos de moneda y comas
        clean = txt.replace("$", "").replace(",", "").strip()
        # Buscar el número (puede ser negativo)
        m = re.search(r"[-]?\d+\.\d{2}", clean)
        return float(m.group(0)) if m else 0.0

    # Inicio de movimiento regular: Fecha Op + Fecha Cargo
    REGULAR_START_PATTERN = re.compile(r"^\d{2}-[A-Z]{3}-\d{4}\s+\d{2}-[A-Z]{3}-\d{4}")
    
    # Monto con signo: +$13.00 o -$12,855.46
    # Making it more robust with \s*
    AMOUNT_PATTERN = re.compile(r"[+-]\s*\$[\d,]+\.\d{2}")

    def _parse_regular_section(self, lines):
        registros = []
        current_mov = None

        def flush_mov():
            nonlocal current_mov
            if not current_mov:
                return
            
            full_desc = " ".join(current_mov["desc_lines"]).strip()
            monto_str = current_mov["amount_str"]
            
            if monto_str:
                monto = self._parse_monto(monto_str)
                tipo = "Abono" if "-" in monto_str else "Cargo"
                monto_abs = abs(monto)
                
                fechas = current_mov["date_line"].split()[:2]
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
            else:
                print(f"WARNING: Movimiento sin monto: {current_mov['date_line']}")
            current_mov = None

        for raw in lines:
            line = raw.strip()
            if not line:
                continue

            if self.REGULAR_START_PATTERN.match(line):
                flush_mov()
                current_mov = {
                    "date_line": line,
                    "desc_lines": [],
                    "amount_str": None
                }
                
                amt = self.AMOUNT_PATTERN.search(line)
                if amt:
                    current_mov["amount_str"] = amt.group(0)
                    desc_part = line.replace(amt.group(0), "")
                    current_mov["desc_lines"].append(desc_part)
                else:
                    current_mov["desc_lines"].append(line)
            
            elif current_mov:
                if not current_mov["amount_str"]:
                    amt = self.AMOUNT_PATTERN.search(line)
                    if amt:
                        current_mov["amount_str"] = amt.group(0)
                        desc_part = line.replace(amt.group(0), "")
                        current_mov["desc_lines"].append(desc_part)
                    else:
                        current_mov["desc_lines"].append(line)
                else:
                    current_mov["desc_lines"].append(line)
        
        flush_mov()
        return registros

# Test data from the PDF extraction
lines = [
    "24-NOV-2025 25-NOV-2025 HR LQ 22:55:58 PAGO TDC POR SPEI",
    "Transferencia a Arturo",
    "40044SCOTIABANK / CLABE 00044180001028324876 -$12,855.46",
    "0241125 / TELLEZ CORTES ARTURO",
    "CVE RAST 2025112440044B36L0000417703828 TECA9406173GA"
]

parser = BanorteCreditParser()
regs = parser._parse_regular_section(lines)
print("Registros encontrados:", len(regs))
for r in regs:
    print(r)
