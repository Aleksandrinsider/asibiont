"""Скрипт для активации подписки пользователя"""
import os
import sys
from datetime import datetime, timedelta
import pytz
from dotenv import load_dotenv

load_dotenv()

from models import Session, User, Subscription

def activate_subscription(telegram_id: int, days: int = 30):
    """Активирует подписку для пользователя"""
    session = Session()
    try:
        # Находим пользователя
        user = session.query(User).filter_by(telegram_id=telegram_id).first()
        if not user:
            print(f"❌ Пользователь с telegram_id {telegram_id} не найден")
            return False
        
        print(f"✅ Найден пользователь: @{user.username} (ID: {user.id})")
        
        # Находим или создаём подписку
        subscription = session.query(Subscription).filter_by(user_id=user.id).first()
        
        now = datetime.now(pytz.UTC)
        
        if not subscription:
            subscription = Subscription(user_id=user.id)
            session.add(subscription)
            print(f"✅ Создана новая подписка")
        else:
            print(f"✅ Найдена существующая подписка (статус: {subscription.status})")
        
        # Активируем подписку
        subscription.status = 'active'
        subscription.start_date = now
        
        # Если подписка активна, продлеваем от end_date, иначе от текущей даты
        if subscription.end_date and subscription.end_date > now:
            subscription.end_date = subscription.end_date + timedelta(days=days)
            print(f"✅ Подписка продлена на {days} дней")
        else:
            subscription.end_date = now + timedelta(days=days)
            print(f"✅ Подписка активирована на {days} дней")
        
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
        session.rollback()
        return False
    finally:
        session.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Использование: python activate_subscription.py <telegram_id> [days]")
        print("Пример: python activate_subscription.py 146333757 30")
        sys.exit(1)
    
    telegram_id = int(sys.argv[1])
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    
    activate_subscription(telegram_id, days)
