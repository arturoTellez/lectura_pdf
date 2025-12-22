import sqlite3
import re
from pathlib import Path

DB_PATH = Path("bank_data.db")

def normalize_date(date_str, year_context="2025"):
    if not date_str:
        return None
    
    months_map = {
        'ENE': 'ene', 'FEB': 'feb', 'MAR': 'mar', 'ABR': 'abr', 'MAY': 'may', 'JUN': 'jun',
        'JUL': 'jul', 'AGO': 'ago', 'SEP': 'sep', 'OCT': 'oct', 'NOV': 'nov', 'DIC': 'dic'
    }
    
    d = date_str.strip().upper().replace('/', '-')
    
    # 1. Matches DD-MMM-YYYY (e.g., 02-DIC-2025)
    m1 = re.match(r"(\d{2})[- ]([A-Z]{3})[- ](\d{4})", d)
    if m1:
        day, mon, year = m1.groups()
        return f"{day}-{months_map.get(mon, mon.lower())}-{year}"
        
    # 2. Matches DD-MMM (e.g., 02-DIC or 02/DIC)
    m2 = re.match(r"(\d{2})[- ]([A-Z]{3})", d)
    if m2:
        day, mon = m2.groups()
        return f"{day}-{months_map.get(mon, mon.lower())}-{year_context}"

    # 3. Matches DD-MM-YYYY
    m3 = re.match(r"(\d{2})-(\d{2})-(\d{4})", d)
    if m3:
        day, mon_num, year = m3.groups()
        month_names = list(months_map.values())
        mon_idx = int(mon_num) - 1
        if 0 <= mon_idx < 12:
            return f"{day}-{month_names[mon_idx]}-{year}"

    return d.lower()

def migrate():
    if not DB_PATH.exists():
        print("No database found.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, fecha_oper, fecha_liq FROM movements")
    rows = cursor.fetchall()
    
    updated_count = 0
    for row_id, f_oper, f_liq in rows:
        norm_oper = normalize_date(f_oper)
        norm_liq = normalize_date(f_liq)
        
        if norm_oper != f_oper or norm_liq != f_liq:
            cursor.execute(
                "UPDATE movements SET fecha_oper = ?, fecha_liq = ? WHERE id = ?",
                (norm_oper, norm_liq, row_id)
            )
            updated_count += 1
            
    conn.commit()
    conn.close()
    print(f"Migration complete. Updated {updated_count} rows.")

if __name__ == "__main__":
    migrate()
