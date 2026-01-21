"""
Миграция для добавления полей average_rating и rating_count в таблицу users
"""
import sqlite3

def migrate():
    conn = sqlite3.connect('local.db')
    cursor = conn.cursor()
    
    try:
        # Проверяем существование полей
        cursor.execute("PRAGMA table_info(users)")
        columns = [row[1] for row in cursor.fetchall()]
        
        print(f"Текущие поля в таблице users: {columns}")
        
        # Добавляем поля если их нет
        if 'average_rating' not in columns:
            cursor.execute('ALTER TABLE users ADD COLUMN average_rating INTEGER DEFAULT 0')
            print("OK: Добавлено поле average_rating")
        else:
            print("INFO: Поле average_rating уже существует")
            
        if 'rating_count' not in columns:
            cursor.execute('ALTER TABLE users ADD COLUMN rating_count INTEGER DEFAULT 0')
            print("OK: Добавлено поле rating_count")
        else:
            print("INFO: Поле rating_count уже существует")
        
        conn.commit()
        print("\nOK: Миграция завершена успешно!")
        
    except Exception as e:
        print(f"ERROR: Ошибка миграции: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == '__main__':
    migrate()
