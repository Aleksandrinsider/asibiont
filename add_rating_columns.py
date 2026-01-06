from config import DATABASE_URL
import psycopg2

conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

try:
    # Добавляем колонки average_rating и rating_count
    cursor.execute("""
        ALTER TABLE user_profiles 
        ADD COLUMN IF NOT EXISTS average_rating INTEGER DEFAULT 0,
        ADD COLUMN IF NOT EXISTS rating_count INTEGER DEFAULT 0;
    """)
    
    conn.commit()
    print("Columns added successfully")
except Exception as e:
    print(f"Error: {e}")
    conn.rollback()
finally:
    cursor.close()
    conn.close()
