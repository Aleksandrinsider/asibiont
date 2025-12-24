from ai_integration import chat_with_ai
import os

# Установить LOCAL=1 и локальную базу для теста
os.environ["LOCAL"] = "1"
os.environ["DATABASE_URL"] = "sqlite:///local.db"

def test_dialogue():
    context = []  # Список для истории
    user_id = 12345  # Тестовый user_id

    print("Тестирование диалога: Введите сообщения пользователя вручную. Для выхода введите 'exit'.")

    while True:
        user_input = input("Пользователь: ")
        if user_input.lower() == 'exit':
            break

        response = chat_with_ai(user_input, context, user_id)
        print(f"Агент: {response}")

        # Сохранить контекст
        context.append({"user": user_input, "agent": response})
        if len(context) > 10:  # Ограничить контекст
            context = context[-10:]

if __name__ == "__main__":
    test_dialogue()