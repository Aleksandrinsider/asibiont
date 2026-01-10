import asyncio
import os
from ai_integration import chat_with_ai
from models import Session, Task, User, Subscription, Subscription
from datetime import datetime, timedelta

async def test_list_tasks_ai():
    """Test AI response for list_tasks intent"""

    # Set up test environment BEFORE imports
    os.environ['LOCAL'] = '1'  # Use local mode
    os.environ['FREE_ACCESS_MODE'] = '1'  # Enable free access for testing

    # Create test user and tasks
    session = Session()
    try:
        # Clean up previous test data
        session.query(Subscription).filter(Subscription.user_id.in_(
            session.query(User.id).filter(User.username == "test_user")
        )).delete()
        session.query(Task).filter(Task.user_id.in_(
            session.query(User.id).filter(User.username == "test_user")
        )).delete()
        session.query(User).filter(User.username == "test_user").delete()
        session.commit()

        # Create test user
        import random
        test_telegram_id = random.randint(100000000, 999999999)
        test_user = User(
            telegram_id=test_telegram_id,
            username="test_user",
            first_name="Test"
        )
        session.add(test_user)
        session.commit()

        # Create active subscription for testing
        subscription = Subscription(
            user_id=test_user.id,
            status="active",
            end_date=datetime.now() + timedelta(days=30)
        )
        session.add(subscription)
        session.commit()

        # Create some test tasks
        tasks = [
            Task(
                user_id=test_user.id,
                title="Позвонить маме",
                reminder_time=datetime.now().replace(hour=15, minute=0),
                priority="medium"
            ),
            Task(
                user_id=test_user.id,
                title="Подготовить отчет",
                reminder_time=datetime.now().replace(hour=18, minute=0),
                priority="high"
            ),
            Task(
                user_id=test_user.id,
                title="Купить продукты",
                reminder_time=None,
                priority="low"
            )
        ]

        for task in tasks:
            session.add(task)
        session.commit()

        # Test AI response for list_tasks
        print("🧪 ТЕСТИРОВАНИЕ AI ОТВЕТА ДЛЯ LIST_TASKS")
        print("=" * 50)

        user_message = "Показать мои задачи"
        intent = "list_tasks"
        params = {}

        print(f"📝 Запрос: {user_message}")
        print(f"🎯 Намерение: {intent}")

        # Get AI response
        response = await chat_with_ai(
            message=user_message,
            context={"intent": intent, "params": params},
            user_id=test_user.id
        )

        print(f"🤖 AI Ответ:\n{response}")
        print("\n" + "=" * 50)

        # Basic checks
        checks = [
            ("Длина ответа > 300 символов", len(response) > 300),
            ("Нет эмодзи", "🚀" not in response and "✅" not in response and "📝" not in response),
            ("Нет списков с маркерами", "- " not in response and "• " not in response),
            ("Нет жирного текста", "**" not in response),
            ("Естественный текст", "СТРОГО ЗАПРЕЩЕНО" not in response)
        ]

        print("📊 ПРОВЕРКИ:")
        for check_name, passed in checks:
            status = "✅" if passed else "❌"
            print(f"{status} {check_name}")

        all_passed = all(passed for _, passed in checks)
        print(f"\n🎯 ОБЩИЙ РЕЗУЛЬТАТ: {'✅ ПРОЙДЕН' if all_passed else '❌ ПРОВАЛЕН'}")

    except Exception as e:
        print(f"❌ ОШИБКА ТЕСТИРОВАНИЯ: {str(e)}")
    finally:
        session.close()

if __name__ == "__main__":
    asyncio.run(test_list_tasks_ai())