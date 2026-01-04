import requests
import json
from datetime import datetime

# URL сервера
BASE_URL = "http://localhost:8000"

def login_and_get_session():
    """Войти и получить сессию"""
    session = requests.Session()

    # Войти через direct_login
    login_url = f"{BASE_URL}/direct_login?user_id=146333757"
    response = session.get(login_url)

    if response.status_code != 200:
        print(f"Login failed: {response.status_code}")
        return None

    print("Login successful")
    return session

def send_message(session, message):
    """Отправить сообщение через /chat"""
    chat_url = f"{BASE_URL}/chat"
    data = {'message': message}

    response = session.post(chat_url, data=data)

    if response.status_code == 200:
        result = response.json()
        return result.get('response', 'No response')
    else:
        print(f"Chat failed: {response.status_code} - {response.text}")
        return None

def main():
    # Войти
    session = login_and_get_session()
    if not session:
        return

    # Диалог
    conversation = []

    # Начальное сообщение
    user_message = "Привет! Расскажи, какие у меня задачи?"
    print(f"User: {user_message}")
    conversation.append({"role": "user", "content": user_message, "timestamp": datetime.now().isoformat()})

    ai_response = send_message(session, user_message)
    if ai_response:
        print(f"AI: {ai_response}")
        conversation.append({"role": "assistant", "content": ai_response, "timestamp": datetime.now().isoformat()})

        # Следующие сообщения (упрощенные для примера)
        next_messages = [
            "Хочу добавить задачу: позвонить партнеру завтра в 10:00",
            "Найди мне партнеров для разработки",
            "Обнови мой профиль: я Python разработчик"
        ]

        for msg in next_messages:
            print(f"User: {msg}")
            conversation.append({"role": "user", "content": msg, "timestamp": datetime.now().isoformat()})

            ai_resp = send_message(session, msg)
            if ai_resp:
                print(f"AI: {ai_resp}")
                conversation.append({"role": "assistant", "content": ai_resp, "timestamp": datetime.now().isoformat()})
            else:
                break

    # Сохранить диалог
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"web_dialog_{timestamp}.json"
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(conversation, f, ensure_ascii=False, indent=2)

    print(f"\nДиалог сохранен в {filename}")
    print("Теперь проверьте панель - диалог должен отображаться в чате!")

if __name__ == "__main__":
    main()