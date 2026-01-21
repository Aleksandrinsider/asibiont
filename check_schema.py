import sqlite3

conn = sqlite3.connect('local.db')
c = conn.cursor()
c.execute('PRAGMA table_info(users)')
rows = c.fetchall()

print("Schema of users table:")
for r in rows:
    print(f"  {r[0]}: {r[1]} ({r[2]})")

print(f"\nTotal columns: {len(rows)}")

conn.close()
