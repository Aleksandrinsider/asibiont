import os
import asyncio
from aiohttp import web, ClientSession
from aiohttp.web import Application
from config import *
from models import *
from ai_integration.chat import chat_with_ai
import json

async def test_ai_agent():
    """Тестируем AI агента с реальными данными"""

    print("🚀 Starting AI Agent Test with Production Data")
    print("=" * 50)

    # Создаем сессию базы данных
    session = Session()

    try:
        # Найдем пользователя aleksandrinsider
        user = session.query(User).filter_by(username='aleksandrinsider').first()
        if not user:
            print("❌ User aleksandrinsider not found")
            return

        print(f"✅ Found user: {user.username} (ID: {user.id}, Telegram ID: {user.telegram_id})")

        # Проверим его задачи
        user_tasks = session.query(Task).filter_by(user_id=user.id).all()
        print(f"📋 User has {len(user_tasks)} tasks:")
        for task in user_tasks:
            delegation_info = f" (delegated to @{task.delegated_to_username}, status: {task.delegation_status})" if task.delegated_to_username else ""
            print(f"   - {task.title} [{task.status}]{delegation_info}")

        # Проверим делегированные ему задачи
        delegated_tasks = session.query(Task).filter_by(delegated_to_username=user.username).all()
        print(f"📋 Tasks delegated to user: {len(delegated_tasks)}")
        for task in delegated_tasks:
            delegator = session.query(User).filter_by(id=task.user_id).first()
            delegator_name = delegator.username if delegator else "unknown"
            print(f"   - {task.title} from @{delegator_name} [{task.delegation_status}]")

        # Создаем AI обработчик
        print("\n🤖 Testing AI Agent functionality...")

        # Тест 1: Запрос списка задач
        print("\n📝 Test 1: Requesting task list")
        test_message_1 = "Покажи мои активные задачи"

        # Тестируем обработку сообщения через chat_with_ai
        response_1 = await chat_with_ai(test_message_1, context=[], user_id=user.telegram_id, db_session=session)
        print(f"AI Response: {response_1[:200]}..." if len(response_1) > 200 else f"AI Response: {response_1}")

        # Тест 2: Запрос делегированных задач
        print("\n📝 Test 2: Requesting delegated tasks")
        test_message_2 = "Какие задачи мне делегировали?"

        response_2 = await chat_with_ai(test_message_2, context=[], user_id=user.telegram_id, db_session=session)
        print(f"AI Response: {response_2[:200]}..." if len(response_2) > 200 else f"AI Response: {response_2}")

        # Тест 3: Попытка завершить задачу с делегированием
        print("\n📝 Test 3: Attempting to complete delegated task")
        test_message_3 = "Заверши задачу 'Подготовить презентацию по проекту AI-ассистента'"

        response_3 = await chat_with_ai(test_message_3, context=[], user_id=user.telegram_id, db_session=session)
        print(f"AI Response: {response_3[:200]}..." if len(response_3) > 200 else f"AI Response: {response_3}")

        # Тест 4: Принятие делегированной задачи
        print("\n📝 Test 4: Accepting delegated task")
        test_message_4 = "Прими задачу 'Подготовить презентацию по проекту AI-ассистента'"

        response_4 = await chat_with_ai(test_message_4, context=[], user_id=user.telegram_id, db_session=session)
        print(f"AI Response: {response_4[:200]}..." if len(response_4) > 200 else f"AI Response: {response_4}")

        # Проверим, изменился ли статус задачи
        task = session.query(Task).filter_by(title='Подготовить презентацию по проекту AI-ассистента').first()
        if task:
            print(f"Task status after acceptance: {task.delegation_status}")

        print("\n✅ AI Agent testing completed successfully!")

    except Exception as e:
        print(f"❌ Error during AI agent testing: {e}")
        import traceback
        traceback.print_exc()

    finally:
        session.close()

if __name__ == "__main__":
    asyncio.run(test_ai_agent())