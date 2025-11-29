import re
from pathlib import Path

import pdfplumber
import pandas as pd


# === Cambia el nombre si tu archivo se llama distinto ===
PDF_PATH = Path("scotiabank_edo_2025-10-17_2487 2.pdf")


def parse_monto(texto: str):
    """Convierte '11,185.21' o '$11,185.21' a float. Devuelve None si no encuentra monto."""
    if not texto:
        return None
    texto = texto.replace("$", "").replace(" ", "")
    m = re.search(r"([\d,]+\.\d{2})", texto)
    if not m:
        return None
    return float(m.group(1).replace(",", ""))


def asignar_lineas(words, y_tol=3):
    """
    Asigna un número de línea a cada palabra según su coordenada vertical (top).
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


def obtener_limites_columnas(df_words):
    """
    A partir de las palabras de encabezado (Fecha, Concepto, Origen, Depósito, Retiro, Saldo)
    calcula la posición horizontal de cada columna y los límites izquierda/derecha.
    """
    headers_map = {
        "fecha": ["Fecha"],
        "concepto": ["Concepto"],
        "origen": ["Origen"],
        "deposito": ["Depósito", "Deposito"],
        "retiro": ["Retiro"],
        "saldo": ["Saldo"],
    }

    col_x = {}
    for col, keywords in headers_map.items():
        cand = df_words[df_words["text"].isin(keywords)]
        if not cand.empty:
            col_x[col] = cand["x0"].mean()

    if len(col_x) < 5:
        raise RuntimeError("No se pudieron detectar bien las columnas en el encabezado.")

    # ordenamos columnas por posición x
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
            right_bounds.append(10_000)  # suficientemente grande
        else:
            right_bounds.append((x + xs[i + 1]) / 2)

    limites = {
        "cols": col_names,
        "left": left_bounds,
        "right": right_bounds,
    }

    # línea del encabezado (donde está 'Fecha')
    header_lines = df_words[df_words["text"] == "Fecha"]["line_id"].unique()
    header_line = int(header_lines[0]) if len(header_lines) else 0
    return limites, header_line


def asignar_columna(x_center, limites):
    """Devuelve el nombre de la columna según sus límites."""
    for name, l, r in zip(limites["cols"], limites["left"], limites["right"]):
        if l <= x_center < r:
            return name
    return limites["cols"][-1]


def extraer_movimientos_pagina(page):
    """
    Extrae la tabla 'Detalle de tus movimientos' de una página (si existe).
    Devuelve lista de dicts.
    """
    texto = page.extract_text() or ""
    if "Detalle de tus movimientos" not in texto:
        return []

    words = page.extract_words(x_tolerance=1, y_tolerance=3, keep_blank_chars=False)
    if not words:
        return []

    words = asignar_lineas(words)
    df = pd.DataFrame(words)

    # Detectar encabezado y columnas
    limites, header_line = obtener_limites_columnas(df)

    filas_raw = []

    # Recorremos cada línea después del encabezado
    for line_id, line in df.groupby("line_id"):
        if line_id <= header_line:
            continue

        # Cortamos cuando empieza el texto de notas
        line_text_full = " ".join(line["text"].tolist())
        if "LAS TASAS DE INTERES ESTAN EXPRESADAS" in line_text_full:
            break

        row = {c: "" for c in limites["cols"]}

        for _, w in line.iterrows():
            x_center = (w["x0"] + w["x1"]) / 2
            col = asignar_columna(x_center, limites)
            if row[col]:
                row[col] += " " + w["text"]
            else:
                row[col] = w["text"]

        # descartamos líneas totalmente vacías
        if not any(row.values()):
            continue

        filas_raw.append(row)

    # Unir líneas que pertenecen al mismo movimiento
    movimientos = []
    fecha_regex = re.compile(r"^\d{1,2}\s+[A-ZÁÉÍÓÚÑ]{3}$")

    for row in filas_raw:
        fecha_txt = (row.get("fecha") or "").strip()

        if fecha_regex.match(fecha_txt):
            # Nueva operación
            movimientos.append(row)
        else:
            # Continuación de la descripción/origen de la operación anterior
            if not movimientos:
                continue
            last = movimientos[-1]
            for col in ["concepto", "origen"]:
                extra = (row.get(col) or "").strip()
                if extra:
                    last[col] = (last.get(col, "") + " " + extra).strip()

            for col in ["deposito", "retiro", "saldo"]:
                extra = (row.get(col) or "").strip()
                if extra and extra not in (last.get(col) or ""):
                    last[col] = (last.get(col, "") + " " + extra).strip()

    return movimientos


def main():
    movimientos = []

    with pdfplumber.open(str(PDF_PATH)) as pdf:
        for page in pdf.pages:
            movs_page = extraer_movimientos_pagina(page)
            movimientos.extend(movs_page)

    if not movimientos:
        print("No se detectaron movimientos en el PDF.")
        return

    df = pd.DataFrame(movimientos)

    # Aseguramos columnas estándar
    for col in ["fecha", "concepto", "origen", "deposito", "retiro", "saldo"]:
        if col not in df.columns:
            df[col] = ""

    # Montos numéricos
    df["deposito_monto"] = df["deposito"].apply(parse_monto)
    df["retiro_monto"] = df["retiro"].apply(parse_monto)
    df["saldo_monto"] = df["saldo"].apply(parse_monto)

    print("=== MOVIMIENTOS CUENTA SCOTIA ===")
    print(df)

    # Guardar CSV y Excel
    df.to_csv("scotia_cuenta_movimientos.csv", index=False, encoding="utf-8-sig")
    with pd.ExcelWriter("scotia_cuenta_movimientos.xlsx") as writer:
        df.to_excel(writer, sheet_name="Movimientos", index=False)


if __name__ == "__main__":
    main()