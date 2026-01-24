"""
Тест подключения к БД и проверка persistent storage
"""
import os
import sys
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Test 1: Check configuration
print("=" * 60)
print("🔍 ПРОВЕРКА КОНФИГУРАЦИИ БД")
print("=" * 60)

LOCAL = os.getenv("LOCAL", "False").lower() in ("true", "1", "yes")
if LOCAL:
    db_path = os.path.join(os.path.dirname(__file__), "local.db")
    DATABASE_URL = f"sqlite:///{db_path}"
    print(f"✅ Режим: LOCAL")
    print(f"✅ База данных: SQLite ({db_path})")
else:
    DATABASE_URL = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")
    print(f"✅ Режим: PRODUCTION")
    print(f"✅ База данных: PostgreSQL")
    if DATABASE_URL:
        # Hide password
        safe_url = DATABASE_URL.split('@')[1] if '@' in DATABASE_URL else DATABASE_URL
        print(f"✅ URL: ...@{safe_url}")
    else:
        print(f"❌ DATABASE_URL не найден!")
        sys.exit(1)

print()

# Test 2: Check database connection
print("=" * 60)
print("🔗 ПРОВЕРКА ПОДКЛЮЧЕНИЯ К БД")
print("=" * 60)

try:
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    
    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    # Test connection
    result = session.execute(text("SELECT 1")).scalar()
    if result == 1:
        print("✅ Подключение к базе данных успешно!")
    
    session.close()
    
except Exception as e:
    print(f"❌ Ошибка подключения: {e}")
    sys.exit(1)

print()

# Test 3: Check tables exist
print("=" * 60)
print("📊 ПРОВЕРКА ТАБЛИЦ")
print("=" * 60)

try:
    from models import User, Task, Subscription, Base
    from sqlalchemy import inspect
    
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    
    required_tables = ['users', 'tasks', 'subscriptions']
    
    for table in required_tables:
        if table in tables:
            # Count records
            session = Session()
            if table == 'users':
                count = session.query(User).count()
            elif table == 'tasks':
                count = session.query(Task).count()
            elif table == 'subscriptions':
                count = session.query(Subscription).count()
            session.close()
            
            print(f"✅ Таблица '{table}': {count} записей")
        else:
            print(f"⚠️ Таблица '{table}' не найдена")
    
except Exception as e:
    print(f"❌ Ошибка проверки таблиц: {e}")

print()

# Test 4: Write and read test
print("=" * 60)
print("💾 ТЕСТ ЗАПИСИ/ЧТЕНИЯ")
print("=" * 60)

try:
    from models import User
    
    session = Session()
    
    # Create test user
    test_user = User(
        telegram_id=999999999,
        username="test_persistence_user",
        timezone="Europe/Moscow",
        created_at=datetime.now()
    )
    
    # Check if exists
    existing = session.query(User).filter_by(telegram_id=999999999).first()
    if existing:
        print(f"✅ Тестовый пользователь уже существует (ID: {existing.id})")
        print(f"   Создан: {existing.created_at}")
        print(f"   ⭐ ДАННЫЕ СОХРАНЯЮТСЯ МЕЖДУ СЕССИЯМИ!")
    else:
        session.add(test_user)
        session.commit()
        print(f"✅ Создан тестовый пользователь (ID: {test_user.id})")
        print(f"   После redeploy проверь снова - пользователь должен остаться!")
    
    session.close()
    
except Exception as e:
    print(f"❌ Ошибка теста записи/чтения: {e}")
    session.rollback()
    session.close()

print()

# Test 5: Volume configuration (production only)
if not LOCAL:
    print("=" * 60)
    print("📦 PERSISTENT STORAGE")
    print("=" * 60)
    print("✅ PostgreSQL должен использовать volume:")
    print("   Mount Path: /var/lib/postgresql/data")
    print("   Size: 5 GB")
    print()
    print("🔍 Проверь в Railway Dashboard:")
    print("   PostgreSQL → Settings → Volumes")
    print()
    print("⭐ Если volume настроен:")
    print("   ✅ Данные сохраняются при redeploy")
    print("   ✅ Пользователи вернутся через месяц")
    print("   ✅ Задачи не удалятся")
    print()

print("=" * 60)
print("✅ ПРОВЕРКА ЗАВЕРШЕНА")
print("=" * 60)
