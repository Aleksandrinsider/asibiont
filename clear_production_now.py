"""
Скрипт для очистки PRODUCTION базы данных на Railway
ВНИМАНИЕ: Удаляет ВСЕ данные из продакшена!
"""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Загружаем переменные окружения
load_dotenv()

# ВАЖНО: Используем production DATABASE_URL из Railway
# Если в .env указан sqlite, нужно взять реальный PostgreSQL URL из Railway
PRODUCTION_DB_URL = os.getenv("DATABASE_URL")

def clear_db(db_url=None):
    """Очистить БД по указанному URL"""
    url = db_url or PRODUCTION_DB_URL
    
    print(f"\n🗄️  Подключение к БД...")
    print(f"URL: {url[:30]}...{url[-20:]}")
    
    try:
        engine = create_engine(url)
        Session = sessionmaker(bind=engine)
        session = Session()
        
        # Проверяем количество записей
        from models import User, Task, UserProfile, Subscription, Interaction, UserRating
        
        users_count = session.query(User).count()
        tasks_count = session.query(Task).count()
        profiles_count = session.query(UserProfile).count()
        subs_count = session.query(Subscription).count()
        
        print(f"\n📊 Текущее состояние БД:")
        print(f"Users: {users_count}")
        print(f"Tasks: {tasks_count}")
        print(f"UserProfile: {profiles_count}")
        print(f"Subscription: {subs_count}")
        
        if users_count == 0 and tasks_count == 0:
            print("\n✅ БД уже пуста!")
            session.close()
            return
        
        # Подтверждение
        confirm = input(f"\n⚠️  УДАЛИТЬ ВСЕ ДАННЫЕ? (yes для подтверждения): ")
        if confirm.lower() != "yes":
            print("❌ Отменено")
            session.close()
            return
        
        # Удаляем данные в правильном порядке (с учётом foreign keys)
        print("\n🗑️  Удаление данных...")
        
        session.query(Interaction).delete()
        print("  ✓ Interactions удалены")
        
        session.query(UserRating).delete()
        print("  ✓ UserRatings удалены")
        
        session.query(Task).delete()
        print("  ✓ Tasks удалены")
        
        session.query(Subscription).delete()
        print("  ✓ Subscriptions удалены")
        
        session.query(UserProfile).delete()
        print("  ✓ UserProfiles удалены")
        
        session.query(User).delete()
        print("  ✓ Users удалены")
        
        session.commit()
        
        # Проверяем результат
        users_count = session.query(User).count()
        tasks_count = session.query(Task).count()
        
        print(f"\n✅ БД очищена!")
        print(f"Users: {users_count}")
        print(f"Tasks: {tasks_count}")
        
        session.close()
        
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        raise

if __name__ == "__main__":
    clear_db()
