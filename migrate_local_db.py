"""
Добавление колонки followup_reminder_sent в локальную SQLite БД
"""
import sqlite3
import os

db_path = os.path.join(os.path.dirname(__file__), "local.db")

if not os.path.exists(db_path):
    print(f"База данных не найдена: {db_path}")
    exit(1)

try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Проверяем, существует ли колонка
    cursor.execute("PRAGMA table_info(tasks)")
    columns = [row[1] for row in cursor.fetchall()]
    
    if 'followup_reminder_sent' in columns:
        print("✅ Колонка followup_reminder_sent уже существует")
    else:
        print("Добавляем колонку followup_reminder_sent...")
        cursor.execute("""
            ALTER TABLE tasks 
            ADD COLUMN followup_reminder_sent BOOLEAN DEFAULT 0
        """)
        conn.commit()
        print("✅ Колонка followup_reminder_sent успешно добавлена")
    
    conn.close()
    print("\nМиграция завершена!")

except Exception as e:
    print(f"❌ Ошибка: {e}")
    import traceback
    traceback.print_exc()
