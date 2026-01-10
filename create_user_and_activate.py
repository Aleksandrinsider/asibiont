"""Скрипт для создания пользователя и активации подписки"""
import os
from datetime import datetime, timedelta
import pytz
from dotenv import load_dotenv

load_dotenv()

from models import Session, User, Subscription, UserProfile

def create_user_with_subscription(telegram_id: int, username: str = None, first_name: str = None, days: int = 30):
    """Создаёт пользователя и активирует подписку"""
    session = Session()
    try:
        # Проверяем, существует ли пользователь
        user = session.query(User).filter_by(telegram_id=telegram_id).first()
        
        if not user:
            # Создаём нового пользователя
            user = User(
                telegram_id=telegram_id,
                username=username or f"user{telegram_id}",
                first_name=first_name or "User",
                timezone='Europe/Moscow'
            )
            session.add(user)
            session.commit()
            print(f"✅ Создан пользователь: @{user.username} (ID: {user.id})")
            
            # Создаём профиль
            profile = UserProfile(
                user_id=user.id,
                contact_info=f"@{user.username}"
            )
            session.add(profile)
            session.commit()
            print(f"✅ Создан профиль пользователя")
        else:
            print(f"✅ Найден существующий пользователь: @{user.username} (ID: {user.id})")
        
        # Активируем подписку
        subscription = session.query(Subscription).filter_by(user_id=user.id).first()
        
        now = datetime.now(pytz.UTC)
        
        if not subscription:
            subscription = Subscription(user_id=user.id)
            session.add(subscription)
        
        subscription.status = 'active'
        subscription.start_date = now
        
        if subscription.end_date and subscription.end_date > now:
            subscription.end_date = subscription.end_date + timedelta(days=days)
        else:
            subscription.end_date = now + timedelta(days=days)
        
        session.commit()
        
        # Выводим информацию
        user_tz = pytz.timezone(user.timezone if user.timezone else 'Europe/Moscow')
        end_date_local = subscription.end_date.astimezone(user_tz).strftime('%d.%m.%Y %H:%M')
        
        print(f"\n🎉 Подписка успешно активирована!")
        print(f"📅 Активна до: {end_date_local}")
        print(f"👤 Пользователь: @{user.username}")
        print(f"🆔 Telegram ID: {telegram_id}")
        
        return True
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        session.rollback()
        return False
    finally:
        session.close()

if __name__ == "__main__":
    # Активируем подписку для пользователя 146333757
    create_user_with_subscription(
        telegram_id=146333757,
        username="aleksandrinsider",
        first_name="Aleksandr",
        days=30
    )
