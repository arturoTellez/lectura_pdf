import os
import time
import json
import pandas as pd
from ai_parsers import OpenAIVisionParser, GeminiVisionParser

# Configuration
PDF_PATH = "/Volumes/nve/Emprendimiento/lectura_estados_cuenta/EstadodeCuenta.pdf"  # Update this if needed
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

def run_parser(parser_class, name, pdf_path, api_key):
    print(f"\n--- Running {name} ---")
    start_time = time.time()
    try:
        parser = parser_class(text="", pdf_path=pdf_path, api_key=api_key)
        result = parser.parse()
        duration = time.time() - start_time
        
        movements = result.get("movements", pd.DataFrame())
        metadata = result.get("metadata", {})
        informative = result.get("informative_data", [])
        
        print(f"✅ {name} completed in {duration:.2f}s")
        print(f"Found {len(movements)} movements.")
        print("Metadata:", json.dumps(metadata, indent=2))
        print(f"Informative Items: {len(informative)}")
        
        return {
            "name": name,
            "duration": duration,
            "count": len(movements),
            "movements": movements,
            "metadata": metadata,
            "informative": informative,
            "error": None
        }
    except Exception as e:
        print(f"❌ {name} failed: {e}")
        return {
            "name": name,
            "error": str(e)
        }

def compare_results(results):
    print("\n\n=== COMPARISON REPORT ===")
    
    # Summary Table
    summary = []
    for r in results:
        if r.get("error"):
            summary.append({
                "Model": r["name"],
                "Status": "Failed",
                "Time (s)": "-",
                "Movements": "-",
                "Balance": "-"
            })
        else:
            meta = r["metadata"]
            summary.append({
                "Model": r["name"],
                "Status": "Success",
                "Time (s)": f"{r['duration']:.2f}",
                "Movements": r["count"],
                "Balance": meta.get("saldo_nuevo", "N/A")
            })
            
    df_summary = pd.DataFrame(summary)
    print(df_summary.to_markdown(index=False))
    
    # Detailed Movement Comparison (First 5)
    print("\n--- First 5 Movements Comparison ---")
    for r in results:
        if not r.get("error"):
            print(f"\n[{r['name']}]")
            if not r["movements"].empty:
                print(r["movements"][["fecha_oper", "descripcion", "monto", "tipo"]].head().to_markdown(index=False))
            else:
                print("No movements found.")

if __name__ == "__main__":
    if not os.path.exists(PDF_PATH):
        print(f"Error: PDF not found at {PDF_PATH}")
        # Try to find a pdf in current dir
        files = [f for f in os.listdir(".") if f.endswith(".pdf")]
        if files:
            PDF_PATH = os.path.abspath(files[0])
            print(f"Using found PDF: {PDF_PATH}")
        else:
            exit(1)

    results = []
    
    # Run OpenAI
    if OPENAI_KEY:
        results.append(run_parser(OpenAIVisionParser, "OpenAI GPT-4o", PDF_PATH, OPENAI_KEY))
    else:
        print("Skipping OpenAI (No API Key)")

    # Run Gemini
    if GEMINI_KEY:
        results.append(run_parser(GeminiVisionParser, "Gemini 1.5 Pro", PDF_PATH, GEMINI_KEY))
    else:
        print("Skipping Gemini (No API Key)")
        
    compare_results(results)
