import asyncio
import json
import redis
from ai_integration import chat_with_ai
from config import REDIS_URL
from models import Session, Task, User, Interaction, UserProfile
import sys

# Настройка кодировки
sys.stdout.reconfigure(encoding='utf-8')

# Redis для контекста
r = redis.from_url(REDIS_URL)

# ID пользователя aleksandrinsider (реальный Telegram ID)
USER_ID = 146333757

def print_user_info(user_id):
    """Показать информацию о пользователе"""
    db = Session()
    try:
        user = db.query(User).filter_by(telegram_id=user_id).first()
        if user:
            print(f"Пользователь: {user.username} (ID: {user.telegram_id})")

            # Задачи
            tasks = db.query(Task).filter_by(user_id=user.id).all()
            print(f"Задачи ({len(tasks)}):")
            for task in tasks:
                print(f"  - {task.title}: {task.status}, reminder: {task.reminder_time}")

            # Профиль
            profile = db.query(UserProfile).filter_by(user_id=user.id).first()
            if profile:
                print(f"Профиль: {profile.city}, {profile.interests}")
                print(f"Статистика: задач создано {profile.total_tasks_created}, выполнено {profile.completed_tasks}")

            # Подписка
            from models import Subscription
            subscription = db.query(Subscription).filter_by(user_id=user.id).first()
            if subscription:
                print(f"Подписка: {subscription.status}, до {subscription.end_date}")
        else:
            print(f"Пользователь {user_id} не найден")
    except Exception as e:
        print(f"Ошибка: {e}")
    finally:
        db.close()

def get_context(user_id):
    """Получить контекст из Redis"""
    try:
        context_data = r.get(f"context:{user_id}")
        if context_data:
            return json.loads(context_data.decode('utf-8'))
        return []
    except Exception as e:
        print(f"Ошибка получения контекста: {e}")
        return []

def save_context(user_id, context):
    """Сохранить контекст в Redis"""
    try:
        r.set(f"context:{user_id}", json.dumps(context))
    except Exception as e:
        print(f"Ошибка сохранения контекста: {e}")

async def simulate_message(user_id, message_text):
    """Симулировать получение сообщения от пользователя"""
    print(f"\n--- Новое сообщение от пользователя {user_id}: '{message_text}' ---")

    # Получить контекст
    context = get_context(user_id)

    # Получить ответ от AI
    response = await chat_with_ai(message_text, context, user_id)

    print(f"Ответ агента: {response}")

    # Сохранить в контекст
    context.append({"user": message_text, "agent": response})
    if len(context) > 10:
        context = context[-10:]
    save_context(user_id, context)

    # Записать взаимодействие в БД
    db = Session()
    try:
        user = db.query(User).filter_by(telegram_id=user_id).first()
        if user:
            interaction = Interaction(
                user_id=user.id,
                message_type='user',
                content=message_text
            )
            db.add(interaction)

            interaction = Interaction(
                user_id=user.id,
                message_type='agent',
                content=response
            )
            db.add(interaction)
            db.commit()
    except Exception as e:
        print(f"Ошибка записи взаимодействия: {e}")
    finally:
        db.close()

    return response

async def test_dialogue():
    """Тестовый диалог от лица aleksandrinsider"""
    print("=== Тестирование диалога для пользователя aleksandrinsider ===")

    # Показать начальную информацию
    print_user_info(USER_ID)

    # Диалог
    messages = [
        "Привет! Я aleksandrinsider, хочу управлять своими задачами",
        "Добавь задачу: подготовить презентацию для команды на завтра в 14:00",
        "Какие у меня задачи?",
        "Добавь задачу: позвонить клиенту Иванову сегодня в 16:00",
        "Найди партнеров для разработки мобильных приложений",
        "Обнови мой профиль: город Москва, интересы - Python, AI, стартапы",
        "Какие напоминания у меня на сегодня?"
    ]

    for msg in messages:
        await simulate_message(USER_ID, msg)
        await asyncio.sleep(1)  # Небольшая пауза

    print("\n=== Финальная информация о пользователе ===")
    print_user_info(USER_ID)

    # Показать контекст
    context = get_context(USER_ID)
    print(f"\nКонтекст чата ({len(context)} сообщений):")
    for i, item in enumerate(context[-5:], 1):  # Показать последние 5
        print(f"{i}. User: {item['user'][:50]}...")
        print(f"   Agent: {item['agent'][:50]}...")

if __name__ == "__main__":
    asyncio.run(test_dialogue())