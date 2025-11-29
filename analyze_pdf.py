import pdfplumber
from pathlib import Path

RUTA_PDF = Path("file.pdf")

def analyze_layout(ruta):
    with pdfplumber.open(ruta) as pdf:
        # Analyze the first page with movements (usually page 0 or 1)
        for i, page in enumerate(pdf.pages):
            print(f"--- Page {i+1} ---")
            words = page.extract_words()
            
            # Filter for words that look like amounts or headers
            relevant_words = [w for w in words if "Cargos" in w['text'] or "Abonos" in w['text'] or "." in w['text']]
            
            print(f"{'Text':<20} {'x0':<10} {'x1':<10} {'top':<10}")
            for w in relevant_words[:20]: # Print first 20 relevant words
                print(f"{w['text']:<20} {w['x0']:<10.2f} {w['x1']:<10.2f} {w['top']:<10.2f}")
            
            # Also print a few full lines to see the flow
            print("\n--- Sample Lines with Coords ---")
            lines = page.extract_text().splitlines()
            for line in lines[:5]:
                print(line)
            
            break # Just one page for now

if __name__ == "__main__":
    analyze_layout(RUTA_PDF)
