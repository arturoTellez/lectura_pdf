import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import pandas as pd

# Add current directory to path
sys.path.append(str(Path(__file__).parent))

from ai_parsers import OpenAIVisionParser, NemotronParser

def test_openai(pdf_path):
    print(f"\n--- Testing OpenAI Vision Parser on {pdf_path} ---")
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("Skipping OpenAI test: OPENAI_API_KEY not found.")
        return

    try:
        # Read text (dummy for now as AI parser reads PDF directly)
        text = "DUMMY TEXT"
        parser = OpenAIVisionParser(text, pdf_path=Path(pdf_path), api_key=api_key)
        result = parser.parse()
        
        print("Extraction Successful!")
        print(f"Account: {result['account_number']}")
        print(f"Movements Found: {len(result['movements'])}")
        print("Metadata:", result.get("metadata"))
        
        if not result['movements'].empty:
            print("\nFirst 5 movements:")
            print(result['movements'])
            
        # Validation
        if result.get("metadata"):
            meta = result["metadata"]
            val = parser.validate_balance(
                result['movements'].to_dict('records'),
                float(meta.get('saldo_anterior') or 0),
                float(meta.get('saldo_nuevo') or 0),
                float(meta.get('total_abonos') or 0),
                float(meta.get('total_cargos') or 0)
            )

            print("\nValidation Result:", val)
            
        if result.get("informative_data"):
            print("\nInformative Data Found:")
            print(json.dumps(result["informative_data"], indent=2))

            
    except Exception as e:
        print(f"Error testing OpenAI: {e}")

def test_nemotron(pdf_path):
    print(f"\n--- Testing Nemotron Parser (HF) on {pdf_path} ---")
    api_key = os.getenv("HUGGINGFACE_API_TOKEN")
    if not api_key:
        print("Skipping Nemotron test: HUGGINGFACE_API_TOKEN not found.")
        return


    try:
        text = "DUMMY TEXT"
        parser = NemotronParser(text, pdf_path=Path(pdf_path), api_key=api_key)
        result = parser.parse()
        
        print("Extraction Successful!")
        print(f"Account: {result['account_number']}")
        print(f"Movements Found: {len(result['movements'])}")
        
    except Exception as e:
        print(f"Error testing Nemotron: {e}")

if __name__ == "__main__":
    load_dotenv()
    
    if len(sys.argv) > 1:
        pdf_file = sys.argv[1]
    else:
        # Default to one of the files in the directory if exists
        files = list(Path(".").glob("scotiabank*.pdf"))
        if files:
            pdf_file = str(files[0])
        else:
            print("Usage: python test_ai_extraction.py <path_to_pdf>")
            sys.exit(1)
            
    test_openai(pdf_file)
    test_nemotron(pdf_file)
