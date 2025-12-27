from ai_integration import chat_with_ai
import requests
from config import DEEPSEEK_API_KEY
import os

# Установить LOCAL=1 для локального теста
os.environ["LOCAL"] = "1"
os.environ["DATABASE_URL"] = "sqlite:///local.db"

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

def test_dialogue():
    context = []  # Список для истории
    user_id = 12345  # Тестовый user_id

    print("Тестирование диалога в продакшен режиме: Агент отвечает на ИИ-генерированные запросы пользователя.")

    # Добавить несколько задач для теста низкого прогресса
    from ai_integration import add_task
    add_task("Купить продукты", user_id=user_id)
    add_task("Позвонить другу", user_id=user_id)
    add_task("Почитать книгу", user_id=user_id)

    for i in range(5):  # 5 итераций для быстрого теста
        try:
            if i == 0:
                user_input = "Напомни через час заняться уборкой в квартире"
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

            # Сохранить контекст
            context.append({"user": user_input, "agent": response})
            if len(context) > 10:  # Ограничить контекст
                context = context[-10:]
        except Exception as e:
            print(f"Ошибка на шаге {i+1}: {e}")
            break

if __name__ == "__main__":
    test_dialogue()