import sqlite3
import pandas as pd

conn = sqlite3.connect("bank_data.db")
df = pd.read_sql_query("SELECT * FROM movements", conn)
conn.close()

print("Total records:", len(df))
print("\nColumns:", df.columns.tolist())

print("\nUnique Categories:", df["categoria"].unique())

msi_df = df[df["categoria"] == "MSI"]
print(f"\nMSI Records Found: {len(msi_df)}")

if not msi_df.empty:
    print("\nSample MSI Records:")
    print(msi_df[["fecha_oper", "descripcion", "monto", "categoria"]].head())
else:
    print("\nNo MSI records found with exact match 'MSI'.")
    # Check for whitespace issues
    print("\nChecking for 'MSI' with whitespace:")
    print(df[df["categoria"].str.contains("MSI", na=False)][["categoria", "descripcion"]])
