"""
Скрипт для обнуления промокодов в Railway PostgreSQL
Сбрасывает: is_used, used_count, used_by_users
"""

import psycopg2
import sys

# Railway PostgreSQL credentials
DB_HOST = input("Enter PostgreSQL host (e.g., monorail.proxy.rlwy.net): ").strip()
DB_PORT = input("Enter port (default 5432): ").strip() or "5432"
DB_NAME = input("Enter database name (default railway): ").strip() or "railway"
DB_USER = "postgres"
DB_PASSWORD = "upZTbJrZvoxnoSPdUDaOwnLuOvnNSbML"

print(f"\n🔗 Connecting to: {DB_USER}@{DB_HOST}:{DB_PORT}/{DB_NAME}")

try:
    # Подключение к БД
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )
    
    cursor = conn.cursor()
    
    # Получаем текущее состояние промокодов
    cursor.execute("""
        SELECT code, is_used, used_count, 
               CASE WHEN used_by_users = '[]' THEN 0 ELSE 1 END as has_users
        FROM promo_codes
        ORDER BY code
    """)
    
    promo_codes = cursor.fetchall()
    
    print(f"\n📋 Found {len(promo_codes)} promo codes:\n")
    for code, is_used, used_count, has_users in promo_codes:
        status = "✅ Clean" if not is_used and used_count == 0 and has_users == 0 else "❌ Used"
        print(f"  {status} {code}: used={is_used}, count={used_count}, has_users={bool(has_users)}")
    
    # Спрашиваем подтверждение
    print("\n⚠️  This will reset ALL promo codes to unused state.")
    confirm = input("Continue? (yes/no): ").strip().lower()
    
    if confirm != 'yes':
        print("❌ Cancelled")
        sys.exit(0)
    
    # Обнуляем промокоды
    cursor.execute("""
        UPDATE promo_codes
        SET is_used = FALSE,
            used_count = 0,
            used_by_users = '[]'
        WHERE is_used = TRUE OR used_count > 0 OR used_by_users != '[]'
    """)
    
    rows_updated = cursor.rowcount
    conn.commit()
    
    print(f"\n✅ Successfully reset {rows_updated} promo codes!")
    
    # Проверяем результат
    cursor.execute("""
        SELECT code, is_used, used_count
        FROM promo_codes
        ORDER BY code
    """)
    
    print("\n📋 Updated state:\n")
    for code, is_used, used_count in cursor.fetchall():
        print(f"  ✅ {code}: used={is_used}, count={used_count}")
    
    cursor.close()
    conn.close()
    
    print("\n✨ Done!")

except psycopg2.Error as e:
    print(f"\n❌ Database error: {e}")
    sys.exit(1)
except Exception as e:
    print(f"\n❌ Error: {e}")
    sys.exit(1)
