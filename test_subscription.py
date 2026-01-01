"""
Скрипт для тестирования системы подписок
"""
from models import Session, User, Subscription
from datetime import datetime, timedelta
import pytz

def create_test_subscription():
    """Создает тестовую подписку для пользователя"""
    session = Session()
    
    # Найти тестового пользователя
    user = session.query(User).filter_by(telegram_id=146333757).first()
    if not user:
        print("Тестовый пользователь не найден")
        session.close()
        return
    
    # Проверить существующую подписку
    subscription = session.query(Subscription).filter_by(user_id=user.id).first()
    
    now = datetime.now(pytz.UTC)
    end_date = now + timedelta(days=30)
    
    if subscription:
        print(f"Найдена существующая подписка:")
        print(f"  Статус: {subscription.status}")
        print(f"  Дата окончания: {subscription.end_date}")
        
        # Обновить подписку
        subscription.status = 'active'
        subscription.start_date = now
        subscription.end_date = end_date
    else:
        print("Создаем новую подписку")
        subscription = Subscription(
            user_id=user.id,
            status='active',
            start_date=now,
            end_date=end_date
        )
        session.add(subscription)
    
    session.commit()
    
    # Вывести результат
    user_tz = pytz.timezone(user.timezone if user.timezone else 'Europe/Moscow')
    local_end = subscription.end_date.astimezone(user_tz).strftime('%d.%m.%Y %H:%M')
    
    print(f"\n✓ Подписка активна до: {local_end}")
    print(f"  User ID: {user.id}, Telegram ID: {user.telegram_id}")
    
    session.close()


def test_subscription_extension():
    """Тестирует продление подписки"""
    session = Session()
    
    user = session.query(User).filter_by(telegram_id=146333757).first()
    if not user:
        print("Тестовый пользователь не найден")
        session.close()
        return
    
    subscription = session.query(Subscription).filter_by(user_id=user.id).first()
    if not subscription:
        print("Подписка не найдена. Сначала запустите create_test_subscription()")
        session.close()
        return
    
    user_tz = pytz.timezone(user.timezone if user.timezone else 'Europe/Moscow')
    old_end = subscription.end_date
    
    # Убедиться что old_end имеет timezone
    if old_end.tzinfo is None:
        old_end = pytz.UTC.localize(old_end)
    
    old_end_local = old_end.astimezone(user_tz).strftime('%d.%m.%Y %H:%M')
    
    print(f"Текущая дата окончания: {old_end_local}")
    
    # Симуляция логики из yookassa_webhook
    now = datetime.now(pytz.UTC)
    
    # Убедиться что subscription.end_date имеет timezone
    if subscription.end_date.tzinfo is None:
        subscription.end_date = pytz.UTC.localize(subscription.end_date)
    
    if subscription.end_date and subscription.end_date > now:
        # Подписка еще активна - продлеваем от end_date
        subscription.end_date = subscription.end_date + timedelta(days=30)
        print(f"✓ Подписка активна, продлеваем от текущей даты окончания")
    else:
        # Подписка истекла - продлеваем от текущей даты
        subscription.end_date = now + timedelta(days=30)
        print(f"✓ Подписка истекла, продлеваем от текущей даты")
    
    session.commit()
    
    new_end_local = subscription.end_date.astimezone(user_tz).strftime('%d.%m.%Y %H:%M')
    print(f"Новая дата окончания: {new_end_local}")
    
    session.close()


if __name__ == "__main__":
    print("=== Тестирование системы подписок ===\n")
    
    print("1. Создание тестовой подписки:")
    create_test_subscription()
    
    print("\n2. Тестирование продления:")
    test_subscription_extension()
    
    print("\n=== Готово ===")
    print("\nТеперь можно:")
    print("1. Открыть http://127.0.0.1:8000/dashboard")
    print("2. Нажать 'продлить' в профиле")
    print("3. В локальном режиме это добавит еще 30 дней")
    print("4. В продакшене ссылка ведет на Yookassa для оплаты")
