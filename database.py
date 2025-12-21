import sqlite3
from pathlib import Path
import pandas as pd

DB_PATH = Path("bank_data.db")

def init_db():
    """Initializes the database with the movements table."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_number TEXT,
            bank TEXT,
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
            UNIQUE(account_number, fecha_oper, descripcion, monto, tipo)
        )
    """)
    
    conn.commit()
    conn.close()

def save_movements(df, account_number, bank_name):
    """Saves a DataFrame of movements to the database."""
    if df.empty:
        return

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

    # Insert loop
    for _, row in df.iterrows():
        try:
            cursor.execute("""
                INSERT OR IGNORE INTO movements (
                    account_number, bank, fecha_oper, fecha_liq, descripcion, 
                    monto, tipo, categoria, saldo_calculado, 
                    meta_monto_original, meta_saldo_pendiente, user_classification, recurrence_period
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                account_number,
                bank_name,
                row["fecha_oper"],
                row["fecha_liq"],
                row["descripcion"],
                row["monto"],
                row["tipo"],
                row["categoria"],
                row["saldo_calculado"],
                row["meta_monto_original"],
                row["meta_saldo_pendiente"],
                None,  # user_classification default
                None   # recurrence_period default
            ))
        except Exception as e:
            print(f"Error saving row: {e}")

    conn.commit()
    conn.close()
    print(f"Saved {len(df)} records to database.")

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

