import pdfplumber
import re
import pandas as pd
from pathlib import Path

# === Cambia aquí el nombre de tu archivo si es diferente ===
PDF_PATH = Path("scotiabank_edo_2025-11-23_0095.pdf")


def parse_monto(monto_str: str) -> float:
    """
    Convierte textos tipo '1,254.00' o '699.00' a float.
    """
    monto_str = monto_str.replace(",", "")
    return float(monto_str)


# --- Patrones / regex ---

# Línea que solo tiene fecha + resto (para MSI, primera línea del renglón)
fecha_line_pattern = re.compile(
    r"^(?P<fecha>\d{2}-[a-zA-Z]{3}-\d{4})\s+(?P<resto>.+)$"
)

# Cola de la línea MSI (segunda línea, donde vienen montos, num pago, tasa)
# Ejemplo: 'MEX $699.00 $640.75 $58.25 1/12 0.0%'
msi_tail_pattern = re.compile(
    r"""
    \$(?P<monto_original>[\d,]+\.\d{2})   # $699.00
    \s+\$(?P<saldo_pendiente>[\d,]+\.\d{2})
    \s+\$(?P<pago_requerido>[\d,]+\.\d{2})
    \s+(?P<num_pago>[\d/]+)               # 1/12, 3/3, 3/18, etc.
    \s+(?P<tasa>[\d\.,]+)%                # 0.0%
    """,
    re.VERBOSE,
)

# Movimientos regulares (NO a meses)
# Ejemplo:
# 24-oct-2025 24-oct-2025 LIVERPOOL POR INTERNET CIUDAD DE MEX 3/3 + $632.66
regular_pattern = re.compile(
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


def extraer_lineas(pdf_path: Path):
    """
    Lee todo el PDF y regresa una lista de líneas de texto.
    """
    lineas = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for linea in text.split("\n"):
                lineas.append(linea.strip())
    return lineas


def parsear_movimientos_scotia(lineas):
    """
    Detecta las secciones de:
      - COMPRAS Y CARGOS DIFERIDOS A MESES SIN INTERESES
      - CARGOS, ABONOS Y COMPRAS REGULARES (NO A MESES)
    y arma listas de dicts listos para DataFrame.
    """
    en_msi = False
    en_regulares = False

    registros_msi = []
    registros_regulares = []

    # fila MSI pendiente (porque vienen en 2 líneas)
    pendiente_msi = None

    for linea in lineas:
        # --- Cambios de sección ---
        if "COMPRAS Y CARGOS DIFERIDOS A MESES SIN INTERESES" in linea:
            en_msi = True
            en_regulares = False
            pendiente_msi = None
            continue

        if linea.startswith("CARGOS, ABONOS Y COMPRAS REGULARES (NO A MESES)"):
            en_regulares = True
            en_msi = False
            pendiente_msi = None
            continue

        # fin de tablas
        if linea.startswith("Total cargos") or linea.startswith("Total abonos") \
           or linea.startswith("ATENCIÓN DE QUEJAS") \
           or linea.startswith("Notas:"):
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
            m_fecha = fecha_line_pattern.match(linea)
            if m_fecha:
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
                antes_dolar, despues_dolar = linea.split("$", 1)
                pendiente_msi["descripcion"] += " " + antes_dolar.strip()
                cola = "$" + despues_dolar.strip()

                m_tail = msi_tail_pattern.search(cola)
                if m_tail:
                    data = pendiente_msi.copy()
                    data.update(m_tail.groupdict())

                    # Convertir montos a float
                    try:
                        data["monto_original"] = parse_monto(data["monto_original"])
                        data["saldo_pendiente"] = parse_monto(data["saldo_pendiente"])
                        data["pago_requerido"] = parse_monto(data["pago_requerido"])
                    except ValueError:
                        pass

                    registros_msi.append(data)
                    pendiente_msi = None

            # pasamos a la siguiente línea
            continue

        # --- Movimientos regulares (NO a meses) ---
        if en_regulares:
            r = regular_pattern.match(linea)
            if r:
                data = r.groupdict()
                try:
                    data["monto"] = parse_monto(data["monto"])
                except ValueError:
                    pass

                signo = 1 if data["signo"] == "+" else -1
                data["monto_con_signo"] = signo * data["monto"]
                registros_regulares.append(data)

    return registros_msi, registros_regulares


def main():
    lineas = extraer_lineas(PDF_PATH)
    msi, regulares = parsear_movimientos_scotia(lineas)

    df_msi = pd.DataFrame(msi)
    df_regulares = pd.DataFrame(regulares)

    print("=== COMPRAS A MESES SIN INTERESES (Scotiabank) ===")
    print(df_msi)

    print("\n=== CARGOS / ABONOS / COMPRAS REGULARES (NO A MESES) ===")
    print(df_regulares)

    # Guardar en CSV
    df_msi.to_csv("scotia_msi.csv", index=False, encoding="utf-8-sig")
    df_regulares.to_csv("scotia_regulares.csv", index=False, encoding="utf-8-sig")

    # O en un solo Excel con 2 hojas
    with pd.ExcelWriter("scotia_movimientos.xlsx") as writer:
        df_msi.to_excel(writer, sheet_name="MSI", index=False)
        df_regulares.to_excel(writer, sheet_name="Regulares", index=False)


if __name__ == "__main__":
    main()