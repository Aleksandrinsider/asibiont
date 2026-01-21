"""
Устанавливает Gold тариф для пользователя aleksandrinsider
Использует продакшен базу данных если не установлена переменная LOCAL
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import User, SubscriptionTier

def set_gold_tier():
    # Определяем URL базы данных
    if os.environ.get('LOCAL') == '1':
        db_url = 'sqlite:///local.db'
        print("Используется локальная база данных (local.db)")
    else:
        db_url = os.environ.get('DATABASE_PUBLIC_URL') or os.environ.get('DATABASE_URL')
        print(f"Используется продакшен база данных")
    
    if not db_url:
        print("❌ Не указан DATABASE_URL или DATABASE_PUBLIC_URL")
        return
    
    engine = create_engine(db_url)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    try:
        # Ищем пользователя по username
        user = session.query(User).filter(User.username.ilike('aleksandrinsider')).first()
        
        if user:
            print(f"Найден пользователь: @{user.username} (ID: {user.id}, Telegram ID: {user.telegram_id})")
            print(f"Текущий тариф: {user.subscription_tier.value if user.subscription_tier else 'None'}")
            
            # Устанавливаем Gold тариф
            user.subscription_tier = SubscriptionTier.GOLD
            session.commit()
            
            print(f"Новый тариф: {user.subscription_tier.value}")
            print("✅ Тариф успешно обновлен на GOLD")
        else:
            print("❌ Пользователь aleksandrinsider не найден в базе")
            print("Сначала войдите в бота через Telegram")
            
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        session.rollback()
    finally:
        session.close()

if __name__ == '__main__':
    set_gold_tier()
