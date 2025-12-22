import os
import tempfile
import math
import io
import shutil
import uuid
import pdfplumber
import pandas as pd
from pathlib import Path
from typing import List, Optional, Any
from fastapi import FastAPI, File, UploadFile, HTTPException, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

# Import local modules
import database
from parsers import (
    get_parser, 
    BBVADebitParser, 
    BBVACreditParser, 
    ScotiabankCreditParser, 
    ScotiabankDebitParser, 
    BanorteCreditParser,
    ScotiabankV2Parser
)
from ai_parsers import (
    AIBankParser, 
    OpenAIVisionParser, 
    NemotronParser, 
    LocalNemotronParser, 
    GeminiVisionParser
)

# Map names to classes
PARSERS_MAP = {
    "BBVA Débito": BBVADebitParser,
    "BBVA Crédito": BBVACreditParser,
    "Scotiabank Crédito": ScotiabankCreditParser,
    "Scotiabank Débito": ScotiabankDebitParser,
    "Scotiabank V2 (Mejorado)": ScotiabankV2Parser,
    "Banorte Crédito": BanorteCreditParser,
    "Scotiabank - OpenAI Vision": OpenAIVisionParser,
    "Scotiabank - Gemini 1.5 Pro": GeminiVisionParser,
    "Scotiabank - Nvidia Nemotron (Cloud)": NemotronParser,
    "Scotiabank - Nvidia Nemotron (Local)": LocalNemotronParser
}

def sanitize_json(obj: Any) -> Any:
    """Recursively replace NaN and Inf with None, and handle numpy types."""
    import numpy as np
    
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    elif isinstance(obj, (np.float32, np.float64)):
        if np.isnan(obj) or np.isinf(obj):
            return None
        return float(obj)
    elif isinstance(obj, (np.int32, np.int64)):
        return int(obj)
    elif isinstance(obj, dict):
        return {k: sanitize_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_json(i) for i in obj]
    return obj

app = FastAPI(title="Gestor de Estados de Cuenta")

# Initialize DB
database.init_db()

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

class Movement(BaseModel):
    id: int
    account_number: str
    bank: str
    fecha_oper: str
    descripcion: str
    monto: float
    tipo: str
    user_classification: Optional[str]
    recurrence_period: Optional[str]

@app.get("/")
async def read_index():
    from fastapi.responses import FileResponse
    return FileResponse("static/index.html")

@app.post("/upload")
async def upload_files(
    files: List[UploadFile] = File(...),
    manual_parser: Optional[str] = Form(None),
    month: Optional[str] = Form(None)
):
    results = []
    for file in files:
        temp_dir = tempfile.mkdtemp()
        temp_path = Path(temp_dir) / file.filename
        
        try:
            content = await file.read()
            with open(temp_path, "wb") as f:
                f.write(content)
            
            # Extract text
            texto = []
            with pdfplumber.open(temp_path) as pdf:
                for p in pdf.pages:
                    texto.append(p.extract_text(x_tolerance=1) or "")
            full_text = "\n".join(texto)
            
            # Determine parser
            parser_instance = None
            if manual_parser and manual_parser != "Automático":
                parser_class = PARSERS_MAP.get(manual_parser)
                if parser_class:
                    parser_instance = parser_class(full_text, pdf_path=temp_path, month_context=month)
            else:
                parser_instance = get_parser(full_text, pdf_path=temp_path, month_context=month)
            
            if not parser_instance:
                results.append({"filename": file.filename, "status": "error", "message": "No parser found"})
                continue
                
            # Parse
            parsed_data = parser_instance.parse()
            df_movements = parsed_data["movements"]
            account_number = parsed_data["account_number"]
            metadata = parsed_data.get("metadata", {})
            informative = parsed_data.get("informative_data", [])
            
            # Bank name and Account type logic
            bank_name = "Desconocido"
            account_type = "Desconocido"
            p_name = type(parser_instance).__name__
            
            if "BBVA" in p_name: bank_name = "BBVA"
            elif "Scotiabank" in p_name: bank_name = "Scotiabank"
            elif "Banorte" in p_name: bank_name = "Banorte"
            
            if "Credit" in p_name or "Crédito" in p_name: account_type = "Crédito"
            elif "Debit" in p_name or "Débito" in p_name or "V2" in p_name: account_type = "Débito"
            
            # Save PDF permanently with standardized name
            uploads_dir = Path("uploads")
            uploads_dir.mkdir(exist_ok=True)
            
            # Create standardized filename: Banco_mes_año_tipo.pdf
            # Example: BBVA_dic_2025_Debito.pdf
            tipo_clean = account_type.replace("é", "e").replace("í", "i")  # Remove accents
            month_part = month.replace("-", "_") if month else "unknown"
            base_filename = f"{bank_name}_{month_part}_{tipo_clean}"
            
            # Check if file already exists, add number if needed
            counter = 1
            unique_filename = f"{base_filename}.pdf"
            while (uploads_dir / unique_filename).exists():
                unique_filename = f"{base_filename}_{counter}.pdf"
                counter += 1
            
            permanent_path = uploads_dir / unique_filename
            shutil.copy2(temp_path, permanent_path)

            
            # Save movements and check for duplicates
            save_result = database.save_movements(df_movements, account_number, bank_name, account_type)
            saved_count = save_result["saved_count"]
            duplicate_details = save_result["duplicate_details"]
            
            # Register upload in database
            upload_id = database.save_upload(
                filename=unique_filename,
                original_filename=file.filename,
                bank=bank_name,
                account_type=account_type,
                month=month or "",
                file_path=str(permanent_path),
                movement_count=saved_count
            )
            
            result_data = {
                "filename": file.filename,
                "status": "success",
                "movements_count": saved_count,
                "bank": bank_name,
                "account": account_number,
                "account_type": account_type,
                "upload_id": upload_id,
                "metadata": metadata,
                "informative": informative,
                "movements": df_movements.head(15).to_dict(orient="records")
            }
            
            # Include duplicate info if any
            if duplicate_details:
                result_data["has_duplicates"] = True
                result_data["duplicates"] = duplicate_details
                result_data["duplicates_count"] = len(duplicate_details)
            
            results.append(result_data)
            
        except Exception as e:
            results.append({"filename": file.filename, "status": "error", "message": str(e)})
        finally:
            if temp_path.exists():
                os.remove(temp_path)
            os.rmdir(temp_dir)
            
    return sanitize_json({"results": results})

@app.get("/movements")
async def get_movements(bank: Optional[str] = None, month: Optional[str] = None, account_type: Optional[str] = None):
    return sanitize_json(database.get_all_movements(bank=bank, month=month, account_type=account_type))

@app.get("/dashboard")
async def get_dashboard():
    return sanitize_json(database.get_dashboard_stats())

@app.get("/recurrence/suggestions")
async def get_recurrence():
    return sanitize_json(database.get_recurring_suggestions())

@app.get("/months")
async def get_available_months():
    return database.get_unique_months()

@app.get("/upload/matrix")
async def get_upload_matrix():
    return sanitize_json(database.get_upload_status_matrix())

@app.get("/export/excel")
async def export_excel(bank: Optional[str] = None, month: Optional[str] = None, account_type: Optional[str] = None):
    try:
        # Get movements with same filters
        movements = database.get_all_movements(bank=bank, month=month, account_type=account_type)
        if not movements:
            raise HTTPException(status_code=404, detail="No movements found for the selected filters.")
            
        df = pd.DataFrame(movements)
        
        # Select and rename columns for better Excel display
        cols = ["fecha_oper", "bank", "account_type", "descripcion", "monto", "tipo"]
        # Ensure columns exist before filtering
        cols = [c for c in cols if c in df.columns]
        df = df[cols]
        
        # Create Excel in memory
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Movimientos')
        
        output.seek(0)
        
        # Build dynamic filename based on filters
        filename_parts = []
        if bank:
            filename_parts.append(bank.replace(" ", "_"))
        if month:
            # month format is "YYYY-MM", convert to "mes_YYYY"
            try:
                from datetime import datetime
                month_date = datetime.strptime(month, "%Y-%m")
                meses = {1: "ene", 2: "feb", 3: "mar", 4: "abr", 5: "may", 6: "jun",
                         7: "jul", 8: "ago", 9: "sep", 10: "oct", 11: "nov", 12: "dic"}
                month_name = meses.get(month_date.month, str(month_date.month))
                filename_parts.append(f"{month_name}_{month_date.year}")
            except:
                filename_parts.append(month.replace("-", "_"))
        if account_type:
            filename_parts.append(account_type)
        
        if filename_parts:
            filename = "_".join(filename_parts) + ".xlsx"
        else:
            filename = "movimientos.xlsx"
        
        headers = {
            'Content-Disposition': f'attachment; filename="{filename}"'
        }
        return Response(output.getvalue(), headers=headers, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/classify")
async def classify_movement(movement_id: int, classification: str, period: Optional[str] = None):
    try:
        database.update_movement_classification(movement_id, classification, period)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/uploads")
async def list_uploads():
    """Returns list of all uploaded PDFs."""
    return sanitize_json(database.get_uploads())


@app.delete("/uploads/{upload_id}")
async def delete_upload_endpoint(upload_id: int):
    """Deletes an upload and all its associated movements."""
    try:
        deleted_movements = database.delete_upload(upload_id)
        return {"status": "success", "deleted_movements": deleted_movements}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/movements/by-month")
async def delete_movements_by_month_endpoint(bank: str, month: str):
    """Deletes movements for a specific bank and month."""
    try:
        deleted_count = database.delete_movements_by_month(bank, month)
        return {"status": "success", "deleted_count": deleted_count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ConfirmDuplicatesRequest(BaseModel):
    duplicates: List[dict]
    account_number: str
    bank: str
    account_type: str
    upload_id: Optional[int] = None


@app.post("/confirm-duplicates")
async def confirm_duplicates(request: ConfirmDuplicatesRequest):
    """Confirms and saves duplicate transactions that the user confirmed as real."""
    try:
        saved_count = database.force_save_duplicates(
            request.duplicates,
            request.account_number,
            request.bank,
            request.account_type,
            request.upload_id
        )
        return {"status": "success", "saved_count": saved_count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)