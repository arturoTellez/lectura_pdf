import pdfplumber
import sys
from pathlib import Path

def test_extraction(path):
    print(f"Testing extraction for: {path}")
    with pdfplumber.open(path) as pdf:
        print(f"Total pages: {len(pdf.pages)}")
        
        # Use first page
        page = pdf.pages[0]
        
        print("\n--- Default Extraction ---")
        text = page.extract_text()
        print(text[:500] if text else "No text extracted")
        
        print("\n--- x_tolerance=2 ---")
        text = page.extract_text(x_tolerance=2)
        print(text[:500] if text else "No text extracted")
        
        print("\n--- x_tolerance=3 ---")
        text = page.extract_text(x_tolerance=3)
        print(text[:500] if text else "No text extracted")
        
        print("\n--- layout=True ---")
        text = page.extract_text(layout=True)
        print(text[:500] if text else "No text extracted")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        test_extraction(sys.argv[1])
    else:
        print("Please provide a file path.")
