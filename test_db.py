import psycopg2
from urllib.parse import urlparse
url='postgresql://postgres:EzKuRTaADIaiEaFWQHvluInZpiMlUcHt@shortline.proxy.rlwy.net:42709/railway'
try:
    conn = psycopg2.connect(url)
    cur = conn.cursor()
    cur.execute('SELECT version();')
    print('PG_VERSION:', cur.fetchone()[0])
    cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")
    print('PUBLIC_TABLES:', cur.fetchone()[0])
    # check tasks table existence
    try:
        cur.execute('SELECT count(*) FROM tasks;')
        print('TASKS_COUNT:', cur.fetchone()[0])
    except Exception as e:
        print('TASKS_CHECK_ERROR:', e)
    conn.close()
except Exception as e:
    print('PG_ERROR:', e)
