"""
Скрипт для обнуления промокодов через SQLAlchemy
Использует те же модели что и основное приложение
"""

import sys
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Railway PostgreSQL connection string (публичный доступ)
DATABASE_URL = "postgresql://postgres:upZTbJrZvoxnoSPdUDaOwnLuOvnNSbML@nozomi.proxy.rlwy.net:52451/railway"

print(f"\n🔗 Connecting to Railway PostgreSQL...")
print(f"Host: nozomi.proxy.rlwy.net:52451")
print(f"Database: railway\n")

try:
    # Создаем engine и session
    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    # Получаем текущее состояние
    result = session.execute(text("""
        SELECT code, is_used, used_count,
               CASE WHEN used_by_users = '[]' THEN 'clean' ELSE 'used' END as user_status
        FROM promo_codes
        ORDER BY code
    """))
    
    promo_codes = result.fetchall()
    
    if not promo_codes:
        print("⚠️  No promo codes found in database")
        sys.exit(0)
    
    print(f"\n📋 Found {len(promo_codes)} promo codes:\n")
    for row in promo_codes:
        code, is_used, used_count, user_status = row
        status = "✅ Clean" if not is_used and used_count == 0 and user_status == 'clean' else "❌ Used"
        print(f"  {status} {code}: used={is_used}, count={used_count}, users={user_status}")
    
    # Спрашиваем подтверждение
    print("\n⚠️  This will reset ALL promo codes to unused state:")
    print("   - is_used = FALSE")
    print("   - used_count = 0")
    print("   - used_by_users = '[]'")
    
    confirm = input("\nContinue? (yes/no): ").strip().lower()
    
    if confirm != 'yes':
        print("❌ Cancelled")
        session.close()
        sys.exit(0)
    
    # Обнуляем промокоды
    result = session.execute(text("""
        UPDATE promo_codes
        SET is_used = FALSE,
            used_count = 0,
            used_by_users = '[]'
        WHERE is_used = TRUE OR used_count > 0 OR used_by_users != '[]'
    """))
    
    rows_updated = result.rowcount
    session.commit()
    
    print(f"\n✅ Successfully reset {rows_updated} promo codes!")
    
    # Проверяем результат
    result = session.execute(text("""
        SELECT code, is_used, used_count, used_by_users
        FROM promo_codes
        ORDER BY code
    """))
    
    print("\n📋 Updated state:\n")
    for row in result.fetchall():
        code, is_used, used_count, used_by_users = row
        print(f"  ✅ {code}: used={is_used}, count={used_count}, users={used_by_users}")
    
    session.close()
    print("\n✨ Done! All promo codes are now reusable.")

except Exception as e:
    print(f"\n❌ Error: {e}")
    if 'session' in locals():
        session.rollback()
        session.close()
    sys.exit(1)
