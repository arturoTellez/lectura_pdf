import re
import json
import sys
from pathlib import Path
from datetime import datetime

import pdfplumber
import pandas as pd
from typing import Optional, Tuple

# Configuración
OUT_DIR = Path("./salida_scotia")
OUT_DIR.mkdir(parents=True, exist_ok=True)

MONTHS_ES = {
    "ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AGO": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DIC": 12,
    # Variantes minúsculas/títulos por si acaso
    "Ene": 1, "Feb": 2, "Mar": 3, "Abr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Ago": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dic": 12
}

DATE_RE = re.compile(r"^(?P<day>\d{2})\s+(?P<mon>ENE|FEB|MAR|ABR|MAY|JUN|JUL|AGO|SEP|OCT|NOV|DIC)\b", re.IGNORECASE)
MONEY_RE = re.compile(r"\$[\d,]+\.\d{2}")

def money_to_float(value: str) -> float:
    # "$301,515.28" -> 301515.28
    if not value: return 0.0
    return float(value.replace("$", "").replace(",", "").strip())

def extract_lines(pdf_path: str) -> list[str]:
    lines = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for ln in text.splitlines():
                if ln.strip():
                    lines.append(ln.strip())
    return lines

def almost_equal(a: Optional[float], b: Optional[float], tol: float = 0.05) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= tol

# ==========================================
# DETECTOR DE TIPO DE CUENTA
# ==========================================

def detect_account_type(lines: list[str]) -> str:
    text = "\n".join(lines[:50]).upper()
    # Usar regex para tolerar espacios comprimidos (e.g. "LÍMITEDECRÉDITO")
    if re.search(r"L[ÍI]MITE\s*DE\s*CR[ÉE]DITO", text) or \
       re.search(r"PAGO\s*M[ÍI]NIMO", text) or \
       re.search(r"TARJETA\s*DE\s*CR[ÉE]DITO", text):
        return "TDC"
    
    # Para checking, buscar CLABE y Saldo Inicial
    if re.search(r"CLABE", text) and re.search(r"SALDO\s*INICIAL", text):
        return "CHECKING"
        
    return "UNKNOWN"

# ==========================================
# PARSER: CUENTA DE CHEQUES (CHECKING)
# ==========================================

def parse_checking_header(lines: list[str]) -> dict:
    text = "\n".join(lines)

    def find(pattern, flags=0):
        # Allow lenient spacing
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
            header[k] = money_to_float(header[k])

    return header

def classify_amount_checking(amount: float, concept: str):
    c = (concept or "").upper()
    if any(w in c for w in ["PAGO", "RETIRO", "CARGO", "TRANSFERENCIA A", "COMISION", "INTERES"]):
        return None, amount
    if any(w in c for w in ["DEPOS", "DEPÓS", "ABONO", "NOMINA", "TRANSFERENCIA DE", "TRASPASO DE"]):
        return amount, None
    return None, None

def parse_checking_movements(lines: list[str], start_balance: float = None) -> pd.DataFrame:
    text = "\n".join(lines)
    m = re.search(r"Fechadecorte\s+\d{2}-[A-Z]{3}-(\d{2})", text)
    year = 2000 + int(m.group(1)) if m else datetime.now().year

    movements = []
    current = None
    last_saldo = start_balance

    def flush():
        nonlocal current, last_saldo
        if not current: return

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
                d_heur, r_heur = classify_amount_checking(monto_cand, concept)
                if d_heur: deposito = d_heur
                if r_heur: retiro = r_heur
                if not deposito and not retiro: monto_sin_clasificar = monto_cand
            elif len(amounts) == 1:
                monto_sin_clasificar = amounts[0]

        if saldo is not None: last_saldo = saldo

        ref = None
        refs = re.findall(r"\b\d{10,}\b", concept)
        if refs: ref = refs[0]

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

    for ln in lines:
        up = ln.upper()
        if up.startswith(("DETALLE DE TUS MOVIMIENTOS", "FECHA CONCEPTO", "PAGINA", "SCOTIABANK")): continue

        md = DATE_RE.match(ln)
        if md:
            flush()
            try:
               fecha = datetime(year, MONTHS_ES[md.group("mon").upper()], int(md.group("day"))).date().isoformat()
            except:
               fecha = ln.split()[0]
            
            current = {
                "fecha": fecha,
                "concept": [ln],
                "amounts": [money_to_float(x) for x in MONEY_RE.findall(ln)]
            }
        else:
            if current:
                current["concept"].append(ln)
                current["amounts"].extend(money_to_float(x) for x in MONEY_RE.findall(ln))

    flush()
    return pd.DataFrame(movements)

def validation_report_checking(header: dict, df: pd.DataFrame, tol: float = 0.02) -> dict:
    sum_dep = float(df["deposito"].fillna(0).sum()) if "deposito" in df else 0.0
    sum_ret = float(df["retiro"].fillna(0).sum()) if "retiro" in df else 0.0
    
    saldo_inicial = header.get("saldo_inicial") or 0.0
    dep_resumen = header.get("depositos") or 0.0
    ret_resumen = header.get("retiros") or 0.0
    saldo_final = header.get("saldo_final") or 0.0

    expected_final = saldo_inicial + dep_resumen - ret_resumen
    
    last_saldo = None
    if "saldo" in df and df["saldo"].notna().any():
        last_saldo = float(df.loc[df["saldo"].notna(), "saldo"].iloc[-1])

    return {
        "tipo": "CHECKING",
        "controles": {
            "depositos_ok": almost_equal(sum_dep, dep_resumen, tol),
            "retiros_ok": almost_equal(sum_ret, ret_resumen, tol),
            "saldo_final_calc_ok": almost_equal(expected_final, saldo_final, tol),
            "saldo_final_df_ok": almost_equal(last_saldo, saldo_final, tol)
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

def parse_tdc_header(lines: list[str]) -> dict:
    text = "\n".join(lines[:100]) # Resumen suele estar al principio
    
    def find(pattern):
        # Allow extra spaces
        m = re.search(pattern, text, re.IGNORECASE)
        return m.group(1).strip() if m else None

    # Extracción de valores del resumen con regex más flexibles
    # "Pagos y abonos" puede aparecer como "Pagosyabonos"
    pagos_abonos = find(r"Pagos\s*y\s*abonos\s*[-–]?\s*\$([\d,]+\.\d{2})")
    
    # "Cargos regulares" con posibles saltos o falta de espacios
    cargos_regulares = find(r"Cargos\s*regulares.*?\+\s*\$([\d,]+\.\d{2})")
    cargos_meses = find(r"Cargos.*?\s*meses.*?\+\s*\$([\d,]+\.\d{2})")
    
    full_text = "\n".join(lines)
    total_cargos_final = find(r"Total\s*cargos\s*\+\s*\$([\d,]+\.\d{2})") or "0"
    total_abonos_final = find(r"Total\s*abonos\s*[-–]?\s*\$([\d,]+\.\d{2})") or "0"
    
    header = {
        "periodo": find(r"Periodo:\s*([^\n]+)"),
        "fecha_corte": find(r"Fecha\s*de\s*corte:\s*(\d{2}-[a-z]{3}-\d{4})"),
        "no_tarjeta": find(r"No\.\s*Tarjeta\s*(\d+)"),
        "saldo_deudor_total": find(r"Saldo\s*deudor\s*total:\s*\$([\d,]+\.\d{2})"),
        "pago_no_intereses": find(r"Pago\s*para\s*no\s*generar\s*intereses:\s*\d*\s*\$([\d,]+\.\d{2})"),
        "resumen_pagos_abonos": money_to_float(pagos_abonos or total_abonos_final),
        "resumen_cargos_total": money_to_float(total_cargos_final)
    }
    
    if header["resumen_cargos_total"] == 0:
        c1 = money_to_float(cargos_regulares)
        c2 = money_to_float(cargos_meses)
        header["resumen_cargos_total"] = c1 + c2

    return header

def parse_tdc_movements(lines: list[str]) -> pd.DataFrame:
    # Estrategia: Buscar sección "CARGOS, ABONOS Y COMPRAS REGULARES"
    # Formato típico línea: "24-oct-2025 24-oct-2025 AMAZON... + $58.25"
    
    movements = []
    capture = False
    
    # Regex para línea de movimiento TDC más flexible
    # Captura fechas al inicio, y monto/signo al final. Lo del medio es descripción.
    # Toleramos falta de espacios estrictos en medio.
    mv_re = re.compile(r"^(?P<f_ops>\d{2}-[a-z]{3}-\d{4})\s+(?P<f_carg>\d{2}-[a-z]{3}-\d{4})\s+(?P<desc>.+?)\s*(?P<signo>[-+])\s*\$(?P<monto>[\d,]+\.\d{2})$", re.IGNORECASE)

    for ln in lines:
        # Detección de sección robusta (ignorar espacios internos que pdfplumber a veces elimina)
        normalized_ln = ln.upper().replace(" ", "")
        
        if "CARGOS,ABONOSYCOMPRASREGULARES" in normalized_ln:
            capture = True
            continue
            
        # Fin de sección probable (Total cargos/abonos suele estar al final de la lista)
        # Pero a veces el total está en la misma página. Mejor no parar hasta fin de archivo o nueva sección clara.
        # Si encontramos otra sección (ej. "PAGINA", "TASAS"), tal vez parar? 
        # Pero cuidado, "PAGINA" sale en cada hoja.
        # "ATENCIÓNDEQUEJAS" suele estar al final.
        if "ATENCIÓN" in normalized_ln and "QUEJAS" in normalized_ln:
            capture = False
            
        if not capture:
            continue
            
        # Intentar parsear línea que parece movimiento
        # Filtro rápido: debe empezar con número (fecha) y tener $
        if not (ln[0].isdigit() and "$" in ln):
            continue

        m = mv_re.match(ln)
        if m:
            d = m.groupdict()
            monto = money_to_float(d["monto"])
            signo = d["signo"]
            tipo = "ABONO" if signo == "-" else "CARGO"
            
            # En CSV de salida:
            # Abono column -> positivo
            # Cargo column -> positivo
            movements.append({
                "fecha_operacion": d["f_ops"],
                "fecha_cargo": d["f_carg"],
                "descripcion": d["desc"].strip(),
                "monto": monto, # Monto absoluto
                "tipo": tipo,
                "abono": monto if tipo == "ABONO" else None,
                "cargo": monto if tipo == "CARGO" else None
            })
        else:
            # Líneas que no matchean pueden ser wraps de descripción o encabezados de tabla repetidos
            pass

    return pd.DataFrame(movements)

def validation_report_tdc(header: dict, df: pd.DataFrame, tol: float = 0.05) -> dict:
    sum_cargos = float(df["cargo"].fillna(0).sum()) if "cargo" in df else 0.0
    sum_abonos = float(df["abono"].fillna(0).sum()) if "abono" in df else 0.0
    
    resumen_cargos = header.get("resumen_cargos_total") or 0.0
    resumen_abonos = header.get("resumen_pagos_abonos") or 0.0

    return {
        "tipo": "TDC",
        "controles": {
            "cargos_vs_resumen_ok": almost_equal(sum_cargos, resumen_cargos, tol),
            "abonos_vs_resumen_ok": almost_equal(sum_abonos, resumen_abonos, tol),
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
# MAIN
# ==========================================

def main(pdf_file=None):
    # Por defecto usa el argumento o busca PDFs en la carpeta
    if pdf_file:
        files = [Path(pdf_file)]
    else:
        # Si no hay args, procesa el hardcoded o el detectado
        # Prioridad al hardcoded original si existe, o buscar
        files = [Path("Scotiabank_TARJETA DE CREDITO_2025Diciembre.pdf"), Path("scotiabank_edo_2025-11-23_0095.pdf")]
    
    for pdf_path in files:
        if not pdf_path.exists():
            continue
            
        print(f"\n>>> PROCESANDO: {pdf_path}")
        lines = extract_lines(str(pdf_path))
        ctype = detect_account_type(lines)
        print(f"Tipo detectado: {ctype}")
        
        prefix = pdf_path.stem.replace(" ", "_")
        
        if ctype == "CHECKING":
            header = parse_checking_header(lines)
            df = parse_checking_movements(lines, start_balance=header.get("saldo_inicial"))
            report = validation_report_checking(header, df)
        
        elif ctype == "TDC":
            header = parse_tdc_header(lines)
            df = parse_tdc_movements(lines)
            report = validation_report_tdc(header, df)
            
        else:
            print("Formato no reconocido o no soportado.")
            continue
            
        # Exportar
        (OUT_DIR / f"{prefix}_header.json").write_text(json.dumps(header, indent=2, default=str), encoding="utf-8")
        df.to_csv(OUT_DIR / f"{prefix}_movimientos.csv", index=False)
        (OUT_DIR / f"{prefix}_validacion.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        
        # Print validación rápida
        print("Validación:", json.dumps(report["controles"], indent=2))
        print(f"Salida generada en {OUT_DIR}/{prefix}_*")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        main(sys.argv[1])
    else:
        main()