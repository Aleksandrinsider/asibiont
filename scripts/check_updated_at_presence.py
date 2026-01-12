import os
import sys
import psycopg2

DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    print('DATABASE_URL not set')
    sys.exit(1)

try:
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='updated_at'")
    res = cur.fetchone()
    if res:
        print('FOUND', res[0])
    else:
        print('NOT FOUND')
    cur.close()
    conn.close()
except Exception as e:
    print('ERROR', e)
    sys.exit(1)
