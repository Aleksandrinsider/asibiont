import os
if 'LOCAL' in os.environ:
    del os.environ['LOCAL']

from models import SessionLocal, User, UserProfile
from sqlalchemy import text

try:
    session = SessionLocal()
    
    print("="*70)
    print("МИГРАЦИЯ: Добавление average_rating и rating_count в таблицу users")
    print("="*70)
    
    # Проверяем, существуют ли колонки
    result = session.execute(text("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = 'users' 
        AND column_name IN ('average_rating', 'rating_count')
    """))
    existing_columns = [row[0] for row in result]
    
    print(f"\nСуществующие колонки: {existing_columns}")
    
    # Добавляем average_rating если нет
    if 'average_rating' not in existing_columns:
        print("\n1️⃣ Добавляю колонку average_rating...")
        session.execute(text("""
            ALTER TABLE users 
            ADD COLUMN average_rating INTEGER DEFAULT 0
        """))
        session.commit()
        print("✅ Колонка average_rating добавлена")
    else:
        print("\n1️⃣ Колонка average_rating уже существует")
    
    # Добавляем rating_count если нет
    if 'rating_count' not in existing_columns:
        print("\n2️⃣ Добавляю колонку rating_count...")
        session.execute(text("""
            ALTER TABLE users 
            ADD COLUMN rating_count INTEGER DEFAULT 0
        """))
        session.commit()
        print("✅ Колонка rating_count добавлена")
    else:
        print("\n2️⃣ Колонка rating_count уже существует")
    
    # Синхронизируем данные из user_profiles
    print("\n3️⃣ Синхронизирую данные из user_profiles...")
    
    all_profiles = session.query(UserProfile).all()
    synced_count = 0
    
    for profile in all_profiles:
        user = session.query(User).filter_by(id=profile.user_id).first()
        if not user:
            continue
        
        # Используем raw SQL для обновления, т.к. модель может быть не перезагружена
        session.execute(
            text("""
                UPDATE users 
                SET average_rating = :rating, rating_count = :count 
                WHERE id = :user_id
            """),
            {
                'rating': profile.average_rating or 0,
                'count': profile.rating_count or 0,
                'user_id': user.id
            }
        )
        synced_count += 1
    
    session.commit()
    print(f"✅ Синхронизировано пользователей: {synced_count}")
    
    # Проверка результата
    print("\n4️⃣ Проверка результата...")
    
    result = session.execute(text("""
        SELECT u.username, u.average_rating, p.average_rating as profile_rating
        FROM users u
        LEFT JOIN user_profiles p ON u.id = p.user_id
        WHERE u.average_rating != p.average_rating
        LIMIT 5
    """))
    
    mismatches = list(result)
    if len(mismatches) == 0:
        print("✅ Все рейтинги синхронизированы!")
    else:
        print(f"⚠️ Найдено расхождений: {len(mismatches)}")
        for row in mismatches:
            print(f"  @{row[0]}: users.{row[1]} ≠ profile.{row[2]}")
    
    print("\n" + "="*70)
    print("✅ МИГРАЦИЯ ЗАВЕРШЕНА")
    print("="*70)
    
    session.close()
    
except Exception as e:
    print(f"\n❌ Ошибка: {e}")
    import traceback
    traceback.print_exc()
