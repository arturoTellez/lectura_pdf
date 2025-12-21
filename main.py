import pdfplumber
import sys
import argparse
from pathlib import Path
from parsers import get_parser, ScotiabankV2Parser
import database

def leer_pdf(ruta):
    texto = []
    with pdfplumber.open(ruta) as pdf:
        for p in pdf.pages:
            # x_tolerance=1 prevents merging of close characters/words
            texto.append(p.extract_text(x_tolerance=1) or "")
    return "\n".join(texto)

if __name__ == "__main__":
    # Initialize DB
    database.init_db()
    
    parser_arg = argparse.ArgumentParser(description="Parse bank statements.")
    parser_arg.add_argument("file", nargs="?", default="file.pdf", help="Path to the PDF file")
    parser_arg.add_argument("--parser", choices=["auto", "scotiabank-v2"], default="auto", 
                           help="Parser to use (default: auto)")
    args = parser_arg.parse_args()
    
    ruta_pdf = Path(args.file)
    
    if not ruta_pdf.exists():
        print(f"El archivo {ruta_pdf} no existe.")
    else:
        print(f"Procesando: {ruta_pdf}")
        texto = leer_pdf(ruta_pdf)
        
        # Seleccionar parser
        if args.parser == "scotiabank-v2":
            parser = ScotiabankV2Parser(texto, pdf_path=ruta_pdf)
        else:
            parser = get_parser(texto, pdf_path=ruta_pdf)
        
        if parser:
            parser_name = type(parser).__name__
            print(f"Parser detectado: {parser_name}")
            
            try:
                resultado = parser.parse()
                account_number = resultado["account_number"]
                df_movements = resultado["movements"]
                
                print(f"Cuenta detectada: {account_number}")
                print(f"Movimientos encontrados: {len(df_movements)}")
                print(df_movements.head())
                
                # Mostrar validación si está disponible (ScotiabankV2Parser)
                if "metadata" in resultado:
                    meta = resultado["metadata"]
                    print(f"\n--- Tipo de cuenta: {meta.get('account_type', 'N/A')} ---")
                    
                    validation = meta.get("validation", {})
                    if validation:
                        print("\nValidación:")
                        controles = validation.get("controles", {})
                        for ctrl, ok in controles.items():
                            icon = "✅" if ok else "❌"
                            print(f"  {icon} {ctrl}")
                        
                        detalles = validation.get("detalles", {})
                        if detalles:
                            print("\nDetalles:")
                            for key, val in detalles.items():
                                print(f"  {key}: {val}")
                
                # Determine Bank Name from parser name
                bank_name = "Desconocido"
                if "BBVA" in parser_name:
                    bank_name = "BBVA"
                elif "Scotiabank" in parser_name:
                    bank_name = "Scotiabank"
                elif "Banorte" in parser_name:
                    bank_name = "Banorte"
                
                # Save to DB
                database.save_movements(df_movements, account_number, bank_name)
                
            except Exception as e:
                print(f"Error durante el parsing: {e}")
                import traceback
                traceback.print_exc()
        else:
            print("No se pudo detectar un parser adecuado para este archivo.")
            print("Intenta verificar si el PDF contiene texto seleccionable o si es una imagen escaneada.")
            print("Primeras lineas del texto extraido:")
            print(texto[:200])