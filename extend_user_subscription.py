"""
Скрипт для продления подписки конкретного пользователя
"""
from models import Session, User, Subscription
from datetime import datetime, timedelta
import pytz

def extend_user_subscription(username=None, telegram_id=None):
    """Продлить подписку пользователя по username или telegram_id"""
    session = Session()
    
    # Найти пользователя
    if username:
        user = session.query(User).filter_by(username=username).first()
        if not user:
            print(f"Пользователь @{username} не найден")
            session.close()
            return
    elif telegram_id:
        user = session.query(User).filter_by(telegram_id=telegram_id).first()
        if not user:
            print(f"Пользователь с telegram_id {telegram_id} не найден")
            session.close()
            return
    else:
        print("Укажите username или telegram_id")
        session.close()
        return
    
    print(f"Найден пользователь: @{user.username} (ID: {user.telegram_id})")
    
    # Проверить подписку
    subscription = session.query(Subscription).filter_by(user_id=user.id).first()
    
    now = datetime.now(pytz.UTC)
    
    if not subscription:
        print("Создаем новую подписку...")
        subscription = Subscription(
            user_id=user.id,
            status='active',
            start_date=now,
            end_date=now + timedelta(days=30)
        )
        session.add(subscription)
    else:
        print(f"Существующая подписка: статус={subscription.status}, end_date={subscription.end_date}")
        
        # Симуляция логики webhook
        subscription.status = 'active'
        subscription.start_date = now
        
        # Убедиться что end_date имеет timezone
        if subscription.end_date and subscription.end_date.tzinfo is None:
            subscription.end_date = pytz.UTC.localize(subscription.end_date)
        
        # Если подписка активна, продлеваем от end_date, иначе от текущей даты
        if subscription.end_date and subscription.end_date > now:
            old_end = subscription.end_date
            subscription.end_date = subscription.end_date + timedelta(days=30)
            print(f"✓ Продлена от {old_end} до {subscription.end_date}")
        else:
            subscription.end_date = now + timedelta(days=30)
            print(f"✓ Продлена от текущей даты до {subscription.end_date}")
    
    session.commit()
    
    # Вывести результат
    user_tz = pytz.timezone(user.timezone if user.timezone else 'Europe/Moscow')
    local_end = subscription.end_date.astimezone(user_tz).strftime('%d.%m.%Y %H:%M')
    
    print(f"\n✓ Подписка активна до: {local_end}")
    print(f"  User: @{user.username}, Telegram ID: {user.telegram_id}")
    
    session.close()


if __name__ == "__main__":
    import sys
    
    # Если указан аргумент, использовать его
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg.startswith('@'):
            extend_user_subscription(username=arg[1:])
        else:
            try:
                telegram_id = int(arg)
                extend_user_subscription(telegram_id=telegram_id)
            except ValueError:
                extend_user_subscription(username=arg)
    else:
        # По умолчанию для тестового пользователя
        print("Использование: python extend_user_subscription.py <@username или telegram_id>")
        print("Продлеваем для тестового пользователя...")
        extend_user_subscription(telegram_id=146333757)
