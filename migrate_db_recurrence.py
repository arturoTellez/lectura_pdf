import sqlite3
from pathlib import Path

DB_PATH = Path("bank_data.db")

def migrate():
    print(f"Migrating database at {DB_PATH}...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute("ALTER TABLE movements ADD COLUMN recurrence_period TEXT")
        conn.commit()
        print("SUCCESS: Added 'recurrence_period' column.")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            print("INFO: Column 'recurrence_period' already exists.")
        else:
            print(f"ERROR: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    migrate()
