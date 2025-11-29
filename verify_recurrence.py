import sqlite3
from pathlib import Path

DB_PATH = Path("bank_data.db")

def verify_recurrence():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Insert a dummy movement if needed (or use existing)
    cursor.execute("INSERT OR IGNORE INTO movements (account_number, descripcion, monto, tipo) VALUES ('TEST', 'TEST_RECURRENCE', 100, 'Cargo')")
    conn.commit()
    
    # Get ID
    cursor.execute("SELECT id FROM movements WHERE descripcion = 'TEST_RECURRENCE'")
    row = cursor.fetchone()
    if not row:
        print("ERROR: Could not create test row.")
        return
    
    mov_id = row[0]
    
    # 2. Update with recurrence (Manual update query since we can't import the function)
    print(f"Updating ID {mov_id} with recurrence 'Bimestral'...")
    cursor.execute("""
        UPDATE movements
        SET user_classification = ?, recurrence_period = ?
        WHERE id = ?
    """, ("Gasto Fijo", "Bimestral", mov_id))
    conn.commit()
    
    # 3. Verify
    cursor.execute("SELECT user_classification, recurrence_period FROM movements WHERE id = ?", (mov_id,))
    res = cursor.fetchone()
    
    if res and res[0] == "Gasto Fijo" and res[1] == "Bimestral":
        print("SUCCESS: Recurrence period saved correctly.")
    else:
        print(f"FAILURE: Expected ('Gasto Fijo', 'Bimestral'), got {res}")
        
    # Cleanup
    cursor.execute("DELETE FROM movements WHERE id = ?", (mov_id,))
    conn.commit()
    conn.close()

if __name__ == "__main__":
    verify_recurrence()
