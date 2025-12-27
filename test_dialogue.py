from ai_integration import chat_with_ai
import requests
from config import DEEPSEEK_API_KEY
import os
import asyncio
from models import Session, Task, User
from reminder_service import ReminderService

# Боевые настройки: без LOCAL=1, используем реальные Redis и БД

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
        "max_tokens": 50
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
    context = []  # Список для истории
    user_id = 12345  # Тестовый user_id

    # Создать reminder_service для проверки напоминаний и проактивных сообщений
    reminder_service = ReminderService(bot=None)  # Без бота, для теста
    reminder_service.start()

    print("Тестирование диалога в продакшен режиме: Агент отвечает на ИИ-генерированные запросы пользователя.")

    # Новый пользователь - без предустановленных задач

    for i in range(30):  # 30 итераций для глубокого тестирования
        try:
            if i == 0:
                user_input = "привет"
            elif i == 1:
                user_input = "/find_partners"
            elif i == 3:
                user_input = "заверши задачу уборка"
            else:
                user_input = generate_user_message(context)
            print(f"Пользователь: {user_input}")

            response = chat_with_ai(user_input, context, user_id)
            print(f"Агент: {response}")
            print("---")

            # Проверка работы с БД: вывести текущие задачи
            print_user_tasks(user_id)

            # Проверка напоминаний: вывести запланированные jobs
            print(f"Запланированные jobs в scheduler: {len(reminder_service.scheduler.get_jobs())}")

            # Проверка проактивных сообщений: вызвать проверку после каждого шага
            await reminder_service.check_and_send_proactive(user_id)

            # Сохранить контекст
            context.append({"user": user_input, "agent": response})
            if len(context) > 10:  # Ограничить контекст
                context = context[-10:]
        except Exception as e:
            print(f"Ошибка на шаге {i+1}: {e}")
            break

if __name__ == "__main__":
    asyncio.run(test_dialogue())