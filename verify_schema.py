import sqlite3
from database import DB_PATH

def check_schema():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(movements)")
    columns = cursor.fetchall()
    conn.close()
    
    col_names = [c[1] for c in columns]
    if "user_classification" in col_names:
        print("SUCCESS: 'user_classification' column exists.")
    else:
        print("FAILURE: 'user_classification' column MISSING.")
        
if __name__ == "__main__":
    check_schema()
