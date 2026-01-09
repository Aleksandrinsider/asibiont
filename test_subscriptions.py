import asyncio
import os
from dotenv import load_dotenv

# Force production mode for testing subscriptions
os.environ['FREE_ACCESS_MODE'] = 'False'

load_dotenv()

from subscription_service import check_subscription, create_subscription_payment, get_subscription_status
from models import Session, User, Subscription
import datetime

def test_subscription_logic():
    """Тестирование логики подписок"""

    print("🧪 ТЕСТИРОВАНИЕ ЛОГИКИ ПОДПИСОК")
    print("=" * 50)

    # Тестовый user_id
    test_user_id = 123456789

    # 1. Проверка подписки для пользователя без подписки
    print("1. Проверка подписки для пользователя без подписки:")
    has_subscription = check_subscription(test_user_id)
    print(f"   Результат: {has_subscription} (должен быть False)")

    # 2. Создание платежа
    print("\n2. Создание платежа для подписки:")
    try:
        payment_url = create_subscription_payment(test_user_id)
        print(f"   URL платежа: {payment_url[:50]}...")
    except Exception as e:
        print(f"   Ошибка: {e}")

    # 3. Проверка статуса подписки
    print("\n3. Проверка статуса подписки:")
    status = get_subscription_status(test_user_id)
    print(f"   Статус: {status}")

    # 4. Имитация активации подписки (как в webhook)
    print("\n4. Имитация активации подписки:")
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=test_user_id).first()
        if not user:
            user = User(telegram_id=test_user_id, username="testuser")
            session.add(user)
            session.commit()

        subscription = session.query(Subscription).filter_by(user_id=user.id).first()
        if not subscription:
            subscription = Subscription(user_id=user.id)
            session.add(subscription)

        subscription.status = 'active'
        subscription.start_date = datetime.datetime.now(datetime.timezone.utc)
        subscription.end_date = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)

        session.commit()
        print("   Подписка активирована на 30 дней")

        # 5. Проверка подписки после активации
        print("\n5. Проверка подписки после активации:")
        has_subscription_after = check_subscription(test_user_id)
        print(f"   Результат: {has_subscription_after} (должен быть True)")

        status_after = get_subscription_status(test_user_id)
        print(f"   Статус: {status_after}")

    except Exception as e:
        print(f"   Ошибка: {e}")
    finally:
        session.close()

    print("\n" + "=" * 50)
    print("✅ ТЕСТИРОВАНИЕ ЗАВЕРШЕНО")

if __name__ == "__main__":
    test_subscription_logic()