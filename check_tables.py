import psycopg2

conn = psycopg2.connect(
    host='shinkansen.proxy.rlwy.net',
    port=27224,
    user='postgres',
    password='sANXAzJHOtUZkUeeiUUvdNqgxBuAVtdd',
    database='railway'
)

cur = conn.cursor()

# Список всех таблиц
print("📊 Таблицы в БД:")
cur.execute("""
    SELECT table_name 
    FROM information_schema.tables 
    WHERE table_schema = 'public'
    ORDER BY table_name
""")
tables = cur.fetchall()
if tables:
    for table in tables:
        print(f"  ✓ {table[0]}")
else:
    print("  ❌ Таблицы не найдены!")
    print("\n⚠️  Нужно создать таблицы. Запускаю создание...")
    
    # Импортируем модели и создаем таблицы
    import sys
    import os
    sys.path.insert(0, os.path.dirname(__file__))
    
    from models import Base, engine
    Base.metadata.create_all(engine)
    print("✅ Таблицы созданы!")
    
    # Проверяем снова
    cur.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public'
        ORDER BY table_name
    """)
    tables = cur.fetchall()
    print("\n📊 Созданные таблицы:")
    for table in tables:
        print(f"  ✓ {table[0]}")

conn.close()
