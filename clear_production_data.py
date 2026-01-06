"""
Скрипт для очистки данных пользователя в продакшн базе Railway
"""
import os
import sys
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Актуальный DATABASE_URL для Railway
POSSIBLE_DATABASE_URLS = [
    "postgresql://postgres:EzKuRTaADIaiEaFWQHvluInZpiMlUcHt@shortline.proxy.rlwy.net:42709/railway",
]

USER_TELEGRAM_ID = 146333757

def clear_user_data(database_url):
    """Попытка очистить данные пользователя"""
    try:
        print(f"[Attempting] Connecting to database...")
        engine = create_engine(database_url, connect_args={
            "connect_timeout": 10,
            "options": "-c statement_timeout=30000"
        })
        
        Session = sessionmaker(bind=engine)
        session = Session()
        
        # Получаем user_id
        result = session.execute(
            text("SELECT id FROM users WHERE telegram_id = :tid"),
            {"tid": USER_TELEGRAM_ID}
        )
        user = result.fetchone()
        
        if not user:
            print(f"[WARNING] User with telegram_id {USER_TELEGRAM_ID} not found")
            return True  # Технически успешно - данных нет
        
        user_id = user[0]
        print(f"[OK] Found user ID: {user_id}")
        
        # Удаляем данные
        print("[Deleting] Tasks...")
        session.execute(
            text("DELETE FROM tasks WHERE user_id = :uid"),
            {"uid": user_id}
        )
        
        print("[Deleting] Interactions...")
        session.execute(
            text("DELETE FROM interactions WHERE user_id = :uid"),
            {"uid": user_id}
        )
        
        print("[Deleting] UserProfile...")
        session.execute(
            text("DELETE FROM user_profiles WHERE user_id = :uid"),
            {"uid": user_id}
        )
        
        session.commit()
        print(f"[SUCCESS] ✅ Cleared all data for user {USER_TELEGRAM_ID}")
        
        # Проверка
        result = session.execute(
            text("SELECT COUNT(*) FROM tasks WHERE user_id = :uid"),
            {"uid": user_id}
        )
        tasks_count = result.scalar()
        
        result = session.execute(
            text("SELECT COUNT(*) FROM user_profiles WHERE user_id = :uid"),
            {"uid": user_id}
        )
        profile_count = result.scalar()
        
        print(f"[Verification] Tasks: {tasks_count}, Profiles: {profile_count}")
        
        session.close()
        return True
        
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {str(e)[:200]}")
        return False

def main():
    print("=" * 60)
    print("Railway Production Database Cleanup")
    print("=" * 60)
    
    for i, db_url in enumerate(POSSIBLE_DATABASE_URLS, 1):
        if not db_url:
            continue
            
        print(f"\n[Attempt {i}] Trying database connection...")
        if clear_user_data(db_url):
            print("\n" + "=" * 60)
            print("SUCCESS! Production data cleared!")
            print("=" * 60)
            return 0
    
    print("\n" + "=" * 60)
    print("FAILED: Could not connect to any database")
    print("=" * 60)
    print("\nManual cleanup required:")
    print("1. Go to Railway Dashboard → Database → Data")
    print("2. Find user with telegram_id = 146333757")
    print("3. Delete related Tasks, Interactions, UserProfile")
    return 1

if __name__ == "__main__":
    sys.exit(main())
