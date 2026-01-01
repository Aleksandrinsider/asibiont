"""
Скрипт для обновления информации пользователя
"""
from models import Session, User, UserProfile, Subscription
from datetime import datetime, timedelta
import pytz

def update_user_info(telegram_id, username=None, first_name=None):
    """Обновить информацию пользователя"""
    session = Session()
    
    user = session.query(User).filter_by(telegram_id=telegram_id).first()
    if not user:
        print(f"Пользователь с telegram_id={telegram_id} не найден")
        session.close()
        return
    
    print(f"Найден пользователь: ID={user.id}, telegram_id={user.telegram_id}")
    print(f"Текущие данные: username={user.username}, first_name={user.first_name}")
    
    if username:
        user.username = username
        print(f"✓ Обновлен username: {username}")
    
    if first_name:
        user.first_name = first_name
        print(f"✓ Обновлен first_name: {first_name}")
    
    session.commit()
    
    # Проверить подписку
    subscription = session.query(Subscription).filter_by(user_id=user.id).first()
    if subscription:
        user_tz = pytz.timezone(user.timezone if user.timezone else 'Europe/Moscow')
        if subscription.end_date:
            if subscription.end_date.tzinfo is None:
                subscription.end_date = pytz.UTC.localize(subscription.end_date)
            end_local = subscription.end_date.astimezone(user_tz).strftime('%d.%m.%Y %H:%M')
            print(f"\nПодписка: статус={subscription.status}, до {end_local}")
    else:
        print("\nПодписка: не найдена")
    
    session.close()
    print("\n✓ Данные обновлены")


if __name__ == "__main__":
    # Обновить пользователя 146333757
    update_user_info(
        telegram_id=146333757,
        username="aleksandrinsider",
        first_name="Aleksandr"
    )
