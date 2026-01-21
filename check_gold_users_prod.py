"""
Проверка Gold пользователей на продакшен базе
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import User, UserProfile, SubscriptionTier

def check_gold_users():
    db_url = os.environ.get('DATABASE_PUBLIC_URL') or os.environ.get('DATABASE_URL')
    print(f"Используется продакшен база данных\n")
    
    if not db_url:
        print("❌ Не указан DATABASE_URL")
        return
    
    engine = create_engine(db_url)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    try:
        # Ищем aleksandrinsider
        main_user = session.query(User).filter(User.username.ilike('aleksandrinsider')).first()
        if main_user:
            print(f"=== Основной пользователь ===")
            print(f"ID: {main_user.id}")
            print(f"Username: @{main_user.username}")
            print(f"Telegram ID: {main_user.telegram_id}")
            print(f"Тариф: {main_user.subscription_tier.value if main_user.subscription_tier else 'None'}")
            print()
        
        # Ищем всех Gold пользователей кроме aleksandrinsider
        gold_users = session.query(User).filter(
            User.subscription_tier == SubscriptionTier.GOLD
        ).all()
        
        print(f"=== Все Gold пользователи ({len(gold_users)}) ===")
        for user in gold_users:
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            print(f"\nID: {user.id}")
            print(f"Username: @{user.username if user.username else 'None'}")
            print(f"Telegram ID: {user.telegram_id}")
            print(f"Имя: {user.first_name}")
            print(f"Тариф: {user.subscription_tier.value}")
            if profile:
                print(f"Город: {profile.city}")
                print(f"Компания: {profile.company}")
                print(f"Позиция: {profile.position}")
        
        # Подсчитываем Gold пользователей кроме aleksandrinsider
        other_gold = [u for u in gold_users if main_user and u.id != main_user.id]
        print(f"\n{'='*60}")
        print(f"✅ Найдено {len(other_gold)} Gold пользователей (кроме aleksandrinsider)")
        
        if len(other_gold) == 0:
            print("⚠️  Нет других Gold пользователей для отображения в 'Премиум статус'")
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
    finally:
        session.close()

if __name__ == '__main__':
    check_gold_users()
