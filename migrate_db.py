import sqlite3
from database import DB_PATH

def migrate():
    print(f"Migrating database at {DB_PATH}...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        cursor.execute("ALTER TABLE movements ADD COLUMN user_classification TEXT")
        conn.commit()
        print("SUCCESS: Added 'user_classification' column.")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            print("INFO: Column 'user_classification' already exists.")
        else:
            print(f"ERROR: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    migrate()
