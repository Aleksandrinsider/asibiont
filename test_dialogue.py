from ai_integration import chat_with_ai
import requests
from config import DEEPSEEK_API_KEY, REDIS_URL
import os
import asyncio
from models import Session, Task, User, Interaction, UserProfile
from reminder_service import ReminderService
import json
import sys

# Настройка кодировки для корректного вывода Unicode в Windows
sys.stdout.reconfigure(encoding='utf-8')

# Логирование в файл для чтения полного текста
import logging
logging.basicConfig(filename='test_dialogue.log', level=logging.INFO, encoding='utf-8', format='%(message)s')

# Боевые настройки: без LOCAL=1, используем реальные Redis и БД
import os
os.environ["FREE_ACCESS_MODE"] = "True"  # Для теста

if os.getenv("LOCAL") == "1":
    # Для локального тестирования использовать dict вместо Redis
    context_store = {}
else:
    import redis
    r = redis.from_url(REDIS_URL)

def print_user_tasks(user_id):
    db = Session()
    try:
        user = db.query(User).filter_by(telegram_id=user_id).first()
        if user:
            tasks = db.query(Task).filter_by(user_id=user.id).all()
            print(f"Текущие задачи пользователя {user_id}:")
            for task in tasks:
                print(f"  - {task.title}: {task.status}, reminder: {task.reminder_time}, sent: {task.reminder_sent}")
        else:
            print(f"Пользователь {user_id} не найден в БД")
    except Exception as e:
        print(f"Ошибка при выводе задач: {e}")
    finally:
        db.close()

def generate_user_message(context):
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    messages = [{"role": "system", "content": "Ты - пользователь, который общается с ИИ-ботом для управления задачами. На основе истории диалога, сгенерируй следующее естественное сообщение пользователя на русском языке, отвечая на последнее сообщение агента. Не повторяйся, будь разнообразным. Держи коротко."}]
    for item in context[-5:]:  # Последние 5 сообщений для контекста
        messages.append({"role": "user", "content": item["user"]})
        messages.append({"role": "assistant", "content": item["agent"]})
    messages.append({"role": "user", "content": "Сгенерируй следующее сообщение пользователя."})

    data = {
        "model": "deepseek-chat",
        "messages": messages,
        "max_tokens": 200
    }
    response = requests.post(url, headers=headers, json=data)
    if response.status_code == 200:
        generated = response.json()["choices"][0]["message"]["content"].strip()
        # Убрать кавычки если есть
        if generated.startswith('"') and generated.endswith('"'):
            generated = generated[1:-1]
        return generated
    return "добавь задачу купить молоко"

async def test_dialogue():
    user_id = 12346  # Новый тестовый user_id для симуляции нового пользователя

    # Очистить контекст
    if os.getenv("LOCAL") == "1":
        context_store[f"context:{user_id}"] = []
    else:
        try:
            r.delete(f"context:{user_id}")
        except Exception as e:
            print(f"Error deleting context: {e}")

    # Очистить БД для этого пользователя
    db = Session()
    try:
        # Найти пользователя
        user = db.query(User).filter_by(telegram_id=user_id).first()
        if user:
            # Удалить профиль, задачи и взаимодействия
            db.query(UserProfile).filter_by(user_id=user.id).delete()
            db.query(Task).filter_by(user_id=user.id).delete()
            db.query(Interaction).filter_by(user_id=user.id).delete()
            db.delete(user)
            db.commit()  # Commit delete
        # Создать нового пользователя
        user = User(telegram_id=user_id, username=f"test_user_{user_id}")
        db.add(user)
        db.commit()
        print(f"Создан тестовый пользователь {user_id}")
    except Exception as e:
        print(f"Error clearing/creating DB: {e}")
        db.rollback()
    finally:
        db.close()

    # Загрузить контекст (теперь пустой)
    context = []

    # Создать reminder_service для проверки напоминаний и проактивных сообщений
    reminder_service = ReminderService(bot=None)  # Без бота, для теста
    await reminder_service.start()

    print("Тестирование диалога в продакшен режиме: Агент отвечает на ИИ-генерированные запросы пользователя.")

    # Новый пользователь - без предустановленных задач

    for i in range(10):  # 10 итераций для тестирования
        try:
            if i == 0:
                user_input = "привет"
            elif i == 5 and any("завершил" not in msg["agent"] for msg in context[-3:]):  # После добавления задач
                user_input = "завершил задачу 'Подготовить отчет по проекту X'"
            else:
                user_input = generate_user_message(context)
            print(f"Пользователь: {user_input}")
            logging.info(f"Пользователь: {user_input}")

            response = await chat_with_ai(user_input, context, user_id)
            print(f"Агент: {response}")
            logging.info(f"Агент: {response}")
            print("---")

            # Сохранить контекст
            context.append({"user": user_input, "agent": response})
            if len(context) > 10:
                context = context[-10:]
            if os.getenv("LOCAL") == "1":
                context_store[f"context:{user_id}"] = context
            else:
                try:
                    r.set(f"context:{user_id}", json.dumps(context))
                except Exception as e:
                    print(f"Error saving context: {e}")

            # Записать взаимодействие для проактивных проверок
            db = Session()
            user = db.query(User).filter_by(telegram_id=user_id).first()
            if user:
                interaction = Interaction(user_id=user.id, message_type='user', content=user_input)
                db.add(interaction)
                interaction = Interaction(user_id=user.id, message_type='agent', content=response)
                db.add(interaction)
                db.commit()
            db.close()

            # Проверка работы с БД: вывести текущие задачи
            print_user_tasks(user_id)

            # Проверка напоминаний: вывести запланированные jobs
            print(f"Запланированные jobs в scheduler: {len(reminder_service.scheduler.get_jobs())}")

            # Проверка проактивных сообщений: вызвать проверку после каждого шага
            print("Проверка proactive...")
            await reminder_service.check_and_send_proactive(user_id)
            print("Proactive проверен.")
        except Exception as e:
            print(f"Ошибка на шаге {i+1}: {e}")
            break

    # Остановить scheduler после теста
    reminder_service.scheduler.shutdown(wait=True)

if __name__ == "__main__":
    asyncio.run(test_dialogue())