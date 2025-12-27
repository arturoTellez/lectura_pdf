import sqlite3
from pathlib import Path
import pandas as pd
import re

DB_PATH = Path("data/bank_data.db")
UPLOADS_DIR = Path("uploads")

def init_db():
    """Initializes the database with the movements and uploads tables."""
    # Ensure data directory exists
    DB_PATH.parent.mkdir(exist_ok=True)
    
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
            movement_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active'
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            upload_id INTEGER,
            row_index INTEGER,
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
            UNIQUE(account_number, fecha_oper, descripcion, monto, tipo, row_index),
            FOREIGN KEY (upload_id) REFERENCES uploads(id) ON DELETE CASCADE
        )
    """)

    # Table for MSI tracking
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS msi_movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            upload_id INTEGER,
            account_number TEXT,
            bank TEXT,
            fecha_oper TEXT,
            descripcion TEXT,
            monto REAL,
            monto_original REAL,
            saldo_pendiente REAL,
            pago_numero INTEGER,
            pagos_totales INTEGER,
            tasa REAL,
            FOREIGN KEY (upload_id) REFERENCES uploads(id) ON DELETE CASCADE
        )
    """)
    
    # Migrations for existing DB
    try:
        cursor.execute("ALTER TABLE uploads ADD COLUMN status TEXT DEFAULT 'active'")
    except sqlite3.OperationalError:
        pass

    try:
        cursor.execute("ALTER TABLE movements ADD COLUMN account_type TEXT")
    except sqlite3.OperationalError:
        pass # Already exists
    
    try:
        cursor.execute("ALTER TABLE movements ADD COLUMN upload_id INTEGER")
    except sqlite3.OperationalError:
        pass # Already exists

    try:
        cursor.execute("ALTER TABLE movements ADD COLUMN row_index INTEGER")
    except sqlite3.OperationalError:
        pass # Already exists
    
    # Create uploads directory
    UPLOADS_DIR.mkdir(exist_ok=True)

    # Balances table to track initial and final balances per month
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS balances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_number TEXT,
            bank TEXT,
            account_type TEXT,
            month TEXT,
            saldo_inicial REAL,
            saldo_final REAL,
            fecha_corte TEXT,
            UNIQUE(account_number, bank, account_type, month)
        )
    """)
    
    conn.commit()
    conn.close()

def save_balance(account_number, bank, account_type, month, saldo_inicial, saldo_final, fecha_corte=None):
    """Saves or updates the balance for a specific account and month."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO balances (account_number, bank, account_type, month, saldo_inicial, saldo_final, fecha_corte)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_number, bank, account_type, month) 
        DO UPDATE SET 
            saldo_inicial = excluded.saldo_inicial,
            saldo_final = excluded.saldo_final,
            fecha_corte = excluded.fecha_corte
    """, (account_number, bank, account_type, month, saldo_inicial, saldo_final, fecha_corte))
    conn.commit()
    conn.close()

def get_balance(account_number=None, bank=None, account_type=None, month=None):
    """
    Retrieves the balance for a specific month. 
    If not found, tries to find the closest previous month's final balance.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Try exact match
    query = "SELECT saldo_inicial, saldo_final, month FROM balances WHERE 1=1"
    params = []
    if account_number:
        query += " AND account_number = ?"
        params.append(account_number)
    if bank:
        query += " AND bank = ?"
        params.append(bank)
    if account_type:
        query += " AND account_type = ?"
        params.append(account_type)
    
    exact_query = query + " AND month = ?"
    cursor.execute(exact_query, params + [month])
    row = cursor.fetchone()
    
    if row:
        conn.close()
        return {"saldo_inicial": row[0], "saldo_final": row[1], "month": row[2], "source": "exact"}
    
    # 2. Try to find the closest previous month
    # month format is "mmm-YYYY" (e.g., "dic-2025")
    cursor.execute(query, params)
    all_balances = cursor.fetchall()
    conn.close()
    
    if not all_balances:
        return None
        
    def month_to_sortable(m_str):
        try:
            meses = {'ene': 1, 'feb': 2, 'mar': 3, 'abr': 4, 'may': 5, 'jun': 6,
                     'jul': 7, 'ago': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dic': 12}
            parts = m_str.split('-')
            return int(parts[1]) * 100 + meses.get(parts[0].lower(), 0)
        except:
            return 0

    target_val = month_to_sortable(month)
    
    # Filter for months before target and sort descending
    previous_balances = [b for b in all_balances if month_to_sortable(b[2]) < target_val]
    previous_balances.sort(key=lambda x: month_to_sortable(x[2]), reverse=True)
    
    if previous_balances:
        best = previous_balances[0]
        return {"saldo_inicial": best[1], "saldo_final": None, "month": best[2], "source": "previous_final"}
        
    return None

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
            # Handle MSI separately if requested
            if row.get("categoria") == "MSI":
                import re
                m = re.search(r"(\d+)\s*(?:de|/)\s*(\d+)", row["descripcion"])
                pago_num = int(m.group(1)) if m else None
                pagos_tot = int(m.group(2)) if m else None
                
                cursor.execute("""
                    INSERT INTO msi_movements (
                        upload_id, account_number, bank, fecha_oper, descripcion, 
                        monto, monto_original, saldo_pendiente, pago_numero, pagos_totales, tasa
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    upload_id, account_number, bank_name, row["fecha_oper"], row["descripcion"],
                    row["monto"], row.get("meta_monto_original"), row.get("meta_saldo_pendiente"),
                    pago_num, pagos_tot, row.get("meta_tasa")
                ))
                saved_count += 1
                continue

            # Check for existing duplicate including row_index to allow identical transactions in same file
            cursor.execute("""
                SELECT id, fecha_oper, descripcion, monto, tipo, categoria, user_classification 
                FROM movements 
                WHERE account_number = ? AND fecha_oper = ? AND descripcion = ? AND monto = ? AND tipo = ? AND row_index = ?
            """, (account_number, row["fecha_oper"], row["descripcion"], row["monto"], row["tipo"], int(idx)))
            
            existing = cursor.fetchone()
            
            if existing and not force_duplicates:
                # Duplicate found - record it for user review
                duplicate_details.append({
                    "existing": {
                        "id": existing[0],
                        "fecha_oper": existing[1],
                        "descripcion": existing[2],
                        "monto": existing[3],
                        "tipo": existing[4],
                        "categoria": existing[5],
                        "user_classification": existing[6]
                    },
                    "new": {
                        "fecha_oper": row["fecha_oper"],
                        "descripcion": row["descripcion"],
                        "monto": float(row["monto"]) if row["monto"] else 0,
                        "tipo": row["tipo"],
                        "categoria": row.get("categoria"),
                        "row_index": int(idx)
                    }
                })
                skipped_duplicates.append(int(idx))
                continue
            
            # Insert the movement
            if existing and force_duplicates:
                # Add a unique suffix to avoid UNIQUE constraint if it still exists in old format
                cursor.execute("""
                    INSERT INTO movements (
                        upload_id, row_index, account_number, bank, account_type, fecha_oper, fecha_liq, descripcion, 
                        monto, tipo, categoria, saldo_calculado, 
                        meta_monto_original, meta_saldo_pendiente, user_classification, recurrence_period
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    upload_id,
                    int(idx),
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
                        upload_id, row_index, account_number, bank, account_type, fecha_oper, fecha_liq, descripcion, 
                        monto, tipo, categoria, saldo_calculado, 
                        meta_monto_original, meta_saldo_pendiente, user_classification, recurrence_period
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    upload_id,
                    int(idx),
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
                    upload_id, row_index, account_number, bank, account_type, fecha_oper, fecha_liq, descripcion, 
                    monto, tipo, categoria, user_classification
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                upload_id,
                dup.get("row_index"),
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
    cursor.execute("DELETE FROM msi_movements WHERE upload_id = ?", (upload_id,))
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

def get_all_movements(bank=None, month=None, account_type=None, include_msi=False):
    """Retrieves movements with optional filters, ordered by date."""
    conn = sqlite3.connect(DB_PATH)
    query = "SELECT * FROM movements WHERE 1=1"
    params = []
    
    if not include_msi:
        query += " AND (categoria != 'MSI' OR categoria IS NULL)"
    
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


def get_msi_movements(bank=None, month=None):
    """Retrieves MSI movements with optional filters."""
    conn = sqlite3.connect(DB_PATH)
    query = "SELECT * FROM msi_movements WHERE 1=1"
    params = []
    
    if bank:
        query += " AND bank = ?"
        params.append(bank)
    
    if month:
        query += " AND LOWER(fecha_oper) LIKE ?"
        params.append(f"%-{month.lower()}")
        
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

    # Evolution (last 6 months)
    # We need to extract month-year from fecha_oper (DD-MMM-YYYY)
    # This is tricky in SQLite, so we'll use Pandas
    df = pd.read_sql_query("SELECT monto, tipo, fecha_oper FROM movements WHERE tipo = 'Cargo'", conn)
    
    if not df.empty:
        import re
        def extract_month_year(date_str):
            if not date_str: return None
            match = re.match(r'\d{2}-(\w{3})-(\d{4})', date_str, re.IGNORECASE)
            if match:
                return f"{match.group(1).lower()}-{match.group(2)}"
            return None
        
        df['month_year'] = df['fecha_oper'].apply(extract_month_year)
        df = df.dropna(subset=['month_year'])
        
        # Group by month_year and sum
        evolution = df.groupby('month_year')['monto'].sum().to_dict()
        
        # Sort months (this is a bit complex with MMM-YYYY)
        meses_map = {'ene': 1, 'feb': 2, 'mar': 3, 'abr': 4, 'may': 5, 'jun': 6,
                     'jul': 7, 'ago': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dic': 12}
        
        def sort_key(m_y):
            m, y = m_y.split('-')
            return (int(y), meses_map.get(m, 0))
            
        sorted_months = sorted(evolution.keys(), key=sort_key)
        stats["evolution"] = {
            "labels": [m.upper() for m in sorted_months],
            "values": [evolution[m] for m in sorted_months]
        }
    else:
        stats["evolution"] = {"labels": [], "values": []}
    
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

def resolve_duplicate(action, existing_id, new_data, account_number, bank_name, account_type, upload_id):
    """
    Resolves a duplicate movement.
    Actions: 'keep_existing', 'replace_with_new', 'keep_both'
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        if action == 'replace_with_new':
            # Update existing record with new data
            cursor.execute("""
                UPDATE movements SET 
                    fecha_oper = ?, fecha_liq = ?, descripcion = ?, monto = ?, tipo = ?, 
                    categoria = ?, upload_id = ?, row_index = ?
                WHERE id = ?
            """, (
                new_data["fecha_oper"], new_data.get("fecha_liq"), new_data["descripcion"], 
                new_data["monto"], new_data["tipo"], new_data.get("categoria"), 
                upload_id, new_data.get("row_index"), existing_id
            ))
        elif action == 'keep_both':
            # Insert new record as a separate entry
            # We append a suffix to avoid the UNIQUE constraint if it's exactly the same
            cursor.execute("""
                INSERT INTO movements (
                    upload_id, row_index, account_number, bank, account_type, fecha_oper, fecha_liq, descripcion, 
                    monto, tipo, categoria
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                upload_id, new_data.get("row_index"), account_number, bank_name, account_type,
                new_data["fecha_oper"], new_data.get("fecha_liq"), new_data["descripcion"] + " (Duplicado)",
                new_data["monto"], new_data["tipo"], new_data.get("categoria")
            ))
        # 'keep_existing' does nothing
        
        conn.commit()
        return True
    except Exception as e:
        print(f"Error resolving duplicate: {e}")
        return False
    finally:
        conn.close()

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

def _parse_date_internal(d_str):
    """Helper to parse DD-mmm-YYYY to datetime object."""
    if not d_str: return None
    meses = {'ene': 1, 'feb': 2, 'mar': 3, 'abr': 4, 'may': 5, 'jun': 6,
             'jul': 7, 'ago': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dic': 12}
    try:
        # Handle both DD-MMM-YYYY and DD/MM/YYYY if needed, but mostly DD-MMM-YYYY
        parts = re.split(r'[-/]', d_str)
        if len(parts) != 3: return None
        day = int(parts[0])
        # Month can be name or number
        if parts[1].isdigit():
            month = int(parts[1])
        else:
            month = meses.get(parts[1].lower(), 1)
        year = int(parts[2])
        if year < 100: year += 2000 # Handle 2-digit years
        from datetime import datetime
        return datetime(year, month, day)
    except:
        return None

def calculate_starting_balance(bank, account_type, target_date_str):
    """
    Calculates the balance at the start of target_date_str.
    
    Strategy:
    - Find the balance record whose period contains the target_date
    - The period runs from previous_corte+1 to current_corte
    - Use that record's saldo_inicial as anchor (balance before first movement of that period)
    - Apply movements from the period that are before target_date
    """
    conn = sqlite3.connect(DB_PATH)
    
    # Normalize inputs
    bank = str(bank or "").strip()
    account_type = str(account_type or "").strip()
    
    target_dt = _parse_date_internal(target_date_str)
    if not target_dt:
        conn.close()
        return 0, target_date_str
    
    # 1. Get ALL balance records for this account
    df_balances = pd.read_sql_query("""
        SELECT saldo_inicial, saldo_final, fecha_corte, month 
        FROM balances 
        WHERE TRIM(bank) = ? AND TRIM(account_type) = ?
    """, conn, params=[bank, account_type])
    
    if df_balances.empty:
        conn.close()
        return 0, target_date_str

    # Parse dates
    df_balances['dt_corte'] = df_balances['fecha_corte'].apply(_parse_date_internal)
    df_balances = df_balances.dropna(subset=['dt_corte'])
    if df_balances.empty:
        conn.close()
        return 0, target_date_str
    
    df_balances = df_balances.sort_values('dt_corte', ascending=True)
    
    # 2. Find the balance record whose period CONTAINS target_date
    # Period for a balance runs from (previous_corte + 1 day) to (current_corte)
    # Target date falls in period if: previous_corte < target_dt <= current_corte
    
    containing_balance = None
    prev_corte_dt = None
    period_start_dt = None  # The day after previous corte
    
    for i, row in df_balances.iterrows():
        current_corte = row['dt_corte']
        if prev_corte_dt is None:
            # First period - starts from beginning of time
            if target_dt <= current_corte:
                containing_balance = row
                period_start_dt = None  # No lower bound
                break
        else:
            if prev_corte_dt < target_dt <= current_corte:
                containing_balance = row
                period_start_dt = prev_corte_dt  # Movements must be > prev_corte
                break
        prev_corte_dt = current_corte
    
    # Get all movements
    df_movements = pd.read_sql_query("""
        SELECT monto, tipo, fecha_oper FROM movements 
        WHERE TRIM(bank) = ? AND TRIM(account_type) = ?
    """, conn, params=[bank, account_type])
    conn.close()
    
    if df_movements.empty:
        if containing_balance is not None:
            return round(containing_balance['saldo_inicial'], 2), target_date_str
        return 0, target_date_str

    df_movements['dt_oper'] = df_movements['fecha_oper'].apply(_parse_date_internal)
    df_movements = df_movements.dropna(subset=['dt_oper'])
    
    if df_movements.empty:
        if containing_balance is not None:
            return round(containing_balance['saldo_inicial'], 2), target_date_str
        return 0, target_date_str
    
    # If target is after all cortes, use the last balance's saldo_final
    if containing_balance is None:
        latest = df_balances.iloc[-1]
        anchor_balance = latest['saldo_final']
        anchor_date = latest['dt_corte']
        
        # Apply movements after last corte up to target
        mask = (df_movements['dt_oper'] > anchor_date) & (df_movements['dt_oper'] < target_dt)
        intermediate_movs = df_movements[mask]
    else:
        # We found the containing period
        anchor_balance = containing_balance['saldo_inicial']
        period_corte = containing_balance['dt_corte']
        
        # Filter movements that belong to THIS period only
        # Period movements: period_start_dt < dt_oper <= period_corte
        if period_start_dt is not None:
            period_mask = (df_movements['dt_oper'] > period_start_dt) & (df_movements['dt_oper'] <= period_corte)
        else:
            period_mask = df_movements['dt_oper'] <= period_corte
        
        period_movs = df_movements[period_mask]
        
        if period_movs.empty:
            return round(anchor_balance, 2), target_date_str
        
        # Now filter those that are before target_dt
        mask = period_movs['dt_oper'] < target_dt
        intermediate_movs = period_movs[mask]
    
    # Apply movements
    current_balance = anchor_balance
    for _, mov in intermediate_movs.iterrows():
        monto = float(mov['monto'] or 0)
        tipo_clean = str(mov['tipo'] or "").strip().lower()
        if tipo_clean == 'abono':
            current_balance += monto
        elif tipo_clean == 'cargo':
            current_balance -= monto
            
    return round(current_balance, 2), target_date_str
