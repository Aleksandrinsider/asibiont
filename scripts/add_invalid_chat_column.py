import psycopg2

url='postgresql://postgres:EzKuRTaADIaiEaFWQHvluInZpiMlUcHt@shortline.proxy.rlwy.net:42709/railway'
conn = psycopg2.connect(url)
cur = conn.cursor()
try:
    cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS invalid_chat boolean DEFAULT false;")
    conn.commit()
    print('ALTER TABLE executed: invalid_chat column added or already exists')
except Exception as e:
    print('MIGRATION ERROR:', e)
finally:
    conn.close()
