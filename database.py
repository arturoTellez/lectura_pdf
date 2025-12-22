import sqlite3
from pathlib import Path
import pandas as pd

DB_PATH = Path("bank_data.db")
UPLOADS_DIR = Path("uploads")

def init_db():
    """Initializes the database with the movements and uploads tables."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Uploads table to track uploaded PDFs
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            original_filename TEXT,
            bank TEXT,
            account_type TEXT,
            month TEXT,
            upload_date TEXT DEFAULT CURRENT_TIMESTAMP,
            file_path TEXT,
            movement_count INTEGER DEFAULT 0
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            upload_id INTEGER,
            account_number TEXT,
            bank TEXT,
            account_type TEXT,
            fecha_oper TEXT,
            fecha_liq TEXT,
            descripcion TEXT,
            monto REAL,
            tipo TEXT,
            categoria TEXT,
            saldo_calculado REAL,
            meta_monto_original REAL,
            meta_saldo_pendiente REAL,
            user_classification TEXT,
            recurrence_period TEXT,
            UNIQUE(account_number, fecha_oper, descripcion, monto, tipo),
            FOREIGN KEY (upload_id) REFERENCES uploads(id)
        )
    """)
    
    # Migrations for existing DB
    try:
        cursor.execute("ALTER TABLE movements ADD COLUMN account_type TEXT")
    except sqlite3.OperationalError:
        pass # Already exists
    
    try:
        cursor.execute("ALTER TABLE movements ADD COLUMN upload_id INTEGER")
    except sqlite3.OperationalError:
        pass # Already exists
    
    # Create uploads directory
    UPLOADS_DIR.mkdir(exist_ok=True)
    
    conn.commit()
    conn.close()

def save_movements(df, account_number, bank_name, account_type="Desconocido", upload_id=None, force_duplicates=False):
    """
    Saves a DataFrame of movements to the database.
    Returns: dict with 'saved_count', 'skipped_duplicates', and 'duplicate_details'
    """
    if df.empty:
        return {"saved_count": 0, "skipped_duplicates": [], "duplicate_details": []}

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Add missing columns if they don't exist in DF
    required_cols = ["fecha_oper", "fecha_liq", "descripcion", "monto", "tipo"]
    for col in required_cols:
        if col not in df.columns:
            df[col] = None
            
    # Optional columns
    optional_cols = ["categoria", "saldo_calculado", "meta_monto_original", "meta_saldo_pendiente"]
    for col in optional_cols:
        if col not in df.columns:
            df[col] = None

    saved_count = 0
    skipped_duplicates = []
    duplicate_details = []
    
    for idx, row in df.iterrows():
        try:
            # Check for existing duplicate
            cursor.execute("""
                SELECT id, fecha_oper, descripcion, monto, tipo FROM movements 
                WHERE account_number = ? AND fecha_oper = ? AND descripcion = ? AND monto = ? AND tipo = ?
            """, (account_number, row["fecha_oper"], row["descripcion"], row["monto"], row["tipo"]))
            
            existing = cursor.fetchone()
            
            if existing and not force_duplicates:
                # Duplicate found - record it for user review
                duplicate_details.append({
                    "existing_id": existing[0],
                    "fecha_oper": row["fecha_oper"],
                    "descripcion": row["descripcion"],
                    "monto": float(row["monto"]) if row["monto"] else 0,
                    "tipo": row["tipo"],
                    "row_index": int(idx)
                })
                skipped_duplicates.append(int(idx))
                continue
            
            # Insert the movement (with a slight modification to make it unique if forced)
            if existing and force_duplicates:
                # Add a unique suffix to avoid UNIQUE constraint
                cursor.execute("""
                    INSERT INTO movements (
                        upload_id, account_number, bank, account_type, fecha_oper, fecha_liq, descripcion, 
                        monto, tipo, categoria, saldo_calculado, 
                        meta_monto_original, meta_saldo_pendiente, user_classification, recurrence_period
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    upload_id,
                    account_number,
                    bank_name,
                    account_type,
                    row["fecha_oper"],
                    row["fecha_liq"],
                    row["descripcion"] + " (2)",  # Mark as duplicate
                    row["monto"],
                    row["tipo"],
                    row["categoria"],
                    row["saldo_calculado"],
                    row["meta_monto_original"],
                    row["meta_saldo_pendiente"],
                    "Duplicado confirmado",
                    None
                ))
            else:
                cursor.execute("""
                    INSERT OR IGNORE INTO movements (
                        upload_id, account_number, bank, account_type, fecha_oper, fecha_liq, descripcion, 
                        monto, tipo, categoria, saldo_calculado, 
                        meta_monto_original, meta_saldo_pendiente, user_classification, recurrence_period
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    upload_id,
                    account_number,
                    bank_name,
                    account_type,
                    row["fecha_oper"],
                    row["fecha_liq"],
                    row["descripcion"],
                    row["monto"],
                    row["tipo"],
                    row["categoria"],
                    row["saldo_calculado"],
                    row["meta_monto_original"],
                    row["meta_saldo_pendiente"],
                    None,
                    None
                ))
            
            if cursor.rowcount > 0:
                saved_count += 1
        except Exception as e:
            print(f"Error saving row: {e}")

    conn.commit()
    conn.close()
    print(f"Saved {saved_count} records to database. {len(skipped_duplicates)} duplicates found.")
    return {
        "saved_count": saved_count, 
        "skipped_duplicates": skipped_duplicates,
        "duplicate_details": duplicate_details
    }


def force_save_duplicates(duplicates_data, account_number, bank_name, account_type, upload_id=None):
    """Force saves confirmed duplicate transactions."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    saved_count = 0
    
    for dup in duplicates_data:
        try:
            cursor.execute("""
                INSERT INTO movements (
                    upload_id, account_number, bank, account_type, fecha_oper, fecha_liq, descripcion, 
                    monto, tipo, categoria, user_classification
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                upload_id,
                account_number,
                bank_name,
                account_type,
                dup["fecha_oper"],
                dup["fecha_oper"],  # fecha_liq same as fecha_oper
                dup["descripcion"] + " (transacción real duplicada)",
                dup["monto"],
                dup["tipo"],
                "Regular",
                "Duplicado confirmado por usuario"
            ))
            saved_count += 1
        except Exception as e:
            print(f"Error force saving duplicate: {e}")
    
    conn.commit()
    conn.close()
    return saved_count


def save_upload(filename, original_filename, bank, account_type, month, file_path, movement_count):
    """Registers a new upload in the database and returns its ID."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO uploads (filename, original_filename, bank, account_type, month, file_path, movement_count)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (filename, original_filename, bank, account_type, month, file_path, movement_count))
    
    upload_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return upload_id


def get_uploads():
    """Returns list of all uploads."""
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("""
        SELECT id, filename, original_filename, bank, account_type, month, upload_date, file_path, movement_count
        FROM uploads
        ORDER BY upload_date DESC
    """, conn)
    conn.close()
    return df.to_dict(orient="records")


def delete_upload(upload_id):
    """Deletes an upload and all its associated movements."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get file path to delete the file
    cursor.execute("SELECT file_path FROM uploads WHERE id = ?", (upload_id,))
    row = cursor.fetchone()
    file_path = row[0] if row else None
    
    # Delete movements associated with this upload
    cursor.execute("DELETE FROM movements WHERE upload_id = ?", (upload_id,))
    deleted_movements = cursor.rowcount
    
    # Delete the upload record
    cursor.execute("DELETE FROM uploads WHERE id = ?", (upload_id,))
    
    conn.commit()
    conn.close()
    
    # Delete the physical file if it exists
    if file_path:
        try:
            Path(file_path).unlink(missing_ok=True)
        except Exception as e:
            print(f"Error deleting file: {e}")
    
    return deleted_movements


def delete_movements_by_month(bank, month):
    """Deletes movements for a specific bank and month."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Month format in fecha_oper is DD-mmm-YYYY, so we need to match the month part
    cursor.execute("""
        DELETE FROM movements 
        WHERE bank = ? AND LOWER(fecha_oper) LIKE ?
    """, (bank, f"%-{month.lower()}%"))
    
    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted_count


def update_movement_classification(movement_id, classification, recurrence_period=None):
    """Updates the user_classification and recurrence_period for a specific movement."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        UPDATE movements
        SET user_classification = ?, recurrence_period = ?
        WHERE id = ?
    """, (classification, recurrence_period, movement_id))
    
    conn.commit()
    conn.close()

def get_all_movements(bank=None, month=None, account_type=None):
    """Retrieves movements with optional filters, ordered by date."""
    conn = sqlite3.connect(DB_PATH)
    query = "SELECT * FROM movements WHERE 1=1"
    params = []
    
    if bank:
        query += " AND bank = ?"
        params.append(bank)
    
    if account_type:
        query += " AND account_type = ?"
        params.append(account_type)
    
    if month:
        # Normalize input to lowercase and use LOWER in SQL for case-insensitive match
        query += " AND LOWER(fecha_oper) LIKE ?"
        params.append(f"%-{month.lower()}")
    
    # Order by date: extract year, then month number, then day
    # Date format is DD-mmm-YYYY (e.g., "01-dic-2025")
    query += """
        ORDER BY 
            SUBSTR(fecha_oper, 8, 4) DESC,
            CASE LOWER(SUBSTR(fecha_oper, 4, 3))
                WHEN 'ene' THEN 1
                WHEN 'feb' THEN 2
                WHEN 'mar' THEN 3
                WHEN 'abr' THEN 4
                WHEN 'may' THEN 5
                WHEN 'jun' THEN 6
                WHEN 'jul' THEN 7
                WHEN 'ago' THEN 8
                WHEN 'sep' THEN 9
                WHEN 'oct' THEN 10
                WHEN 'nov' THEN 11
                WHEN 'dic' THEN 12
                ELSE 0
            END DESC,
            CAST(SUBSTR(fecha_oper, 1, 2) AS INTEGER) DESC
    """
    
    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return df.to_dict(orient="records")


def get_dashboard_stats():
    """Calculates summary statistics for the dashboard."""
    conn = sqlite3.connect(DB_PATH)
    
    # Simple aggregations
    stats = {}
    
    # Total by type
    cursor = conn.cursor()
    cursor.execute("SELECT tipo, SUM(monto) FROM movements GROUP BY tipo")
    type_totals = cursor.fetchall()
    stats["totals"] = {t: m for t, m in type_totals}
    
    # Total by bank
    cursor.execute("SELECT bank, SUM(monto) FROM movements GROUP BY bank")
    bank_totals = cursor.fetchall()
    stats["by_bank"] = {b: m for b, m in bank_totals}
    
    conn.close()
    return stats

def get_recurring_suggestions():
    """Detects movements that repeat in at least 3 distinct months."""
    conn = sqlite3.connect(DB_PATH)
    
    # Group by description and extract month/year from fecha_oper
    # This is slightly complex in SQLite without better date parsing
    # We'll pull the data and process with Pandas for reliability
    df = pd.read_sql_query("SELECT id, descripcion, monto, fecha_oper, tipo FROM movements", conn)
    conn.close()
    
    if df.empty:
        return []

    # Simple normalization of dates (assuming 'DD-MMM-YYYY' or 'DD/MM/YYYY')
    # We just need to identify the month-year bucket
    def get_month_year(date_str):
        if not date_str: return "Unknown"
        # Try to find a month name or 2-digit month
        # This is a heuristic
        parts = re.split(r'[-/ ]', date_str)
        if len(parts) >= 2:
            return "-".join(parts[1:]) # Returns MMM-YYYY or MM/YYYY
        return date_str

    import re
    df['month_year'] = df['fecha_oper'].apply(get_month_year)
    
    # Group by description and count distinct months
    summary = df.groupby('descripcion').agg({
        'month_year': 'nunique',
        'monto': 'mean',
        'tipo': 'first',
        'id': 'first'
    }).reset_index()
    
    # Filter those appearing in 3+ months
    suggestions = summary[summary['month_year'] >= 3]
    
    return suggestions.to_dict(orient="records")

def get_unique_months():
    """Extracts unique months (MMM-YYYY) from fecha_oper in DD-MMM-YYYY format."""
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT fecha_oper FROM movements", conn)
    conn.close()
    
    if df.empty:
        return []
    
    import re
    
    def extract_month_year(date_str):
        if not date_str:
            return None
        # Match DD-MMM-YYYY format (e.g., 02-dic-2025) - Case insensitive match for MMM
        match = re.match(r'\d{2}-(\w{3})-(\d{4})', date_str, re.IGNORECASE)
        if match:
            return f"{match.group(1).lower()}-{match.group(2)}"
        return None
    
    months = df['fecha_oper'].apply(extract_month_year).dropna().unique()
    return sorted(list(months), reverse=True)

def get_upload_status_matrix():
    """Returns a dictionary of {month-year: {full_bank_name: count}} for existing data."""
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT bank, account_type, fecha_oper FROM movements", conn)
    conn.close()
    
    if df.empty:
        return {}
        
    import re
    def extract_month_year(date_str):
        if not date_str: return None
        match = re.match(r'\d{2}-(\w{3})-(\d{4})', date_str, re.IGNORECASE)
        if match:
            return f"{match.group(1).lower()}-{match.group(2)}"
        return None
    
    df['month_year'] = df['fecha_oper'].apply(extract_month_year)
    df = df.dropna(subset=['month_year'])
    
    # Combine Bank and Account Type for matrix key
    df['full_bank'] = df['bank'] + ' ' + df['account_type']
    
    # Group and count
    matrix = df.groupby(['month_year', 'full_bank']).size().unstack(fill_value=0).to_dict(orient='index')
    return matrix

