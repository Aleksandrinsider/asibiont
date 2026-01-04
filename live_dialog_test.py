import asyncio
import json
import os
from datetime import datetime
from ai_integration import chat_with_ai
from models import User, Task, Interaction, UserProfile
from config import DATABASE_URL
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Настройка базы данных
engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

async def generate_user_message(conversation_history):
    """Генерирует следующее сообщение пользователя на основе истории разговора"""
    history_text = "\n".join([f"{'User' if msg['role'] == 'user' else 'AI'}: {msg['content']}" for msg in conversation_history])

    prompt = f"""You are a user with ID 146333757 interacting with an AI task management bot.
The bot helps with tasks, partners, subscriptions, etc.

Conversation history:
{history_text}

Generate a natural, realistic next message from the user. The message should be in Russian, as the bot is in Russian.
Make it conversational and dependent on the previous messages.
Keep it short, like a real chat message.
Do not include any explanations, just the message text."""

    # Используем тот же AI для генерации пользовательских сообщений
    response = await chat_with_ai(prompt, user_id=146333757)
    return response.strip()

async def live_dialog_test():
    user_id = 146333757

    # Создать пользователя, если не существует
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(telegram_id=user_id, username="test_user", first_name="Test")
        session.add(user)
        session.commit()
        print(f"Пользователь {user_id} создан.")

    conversation = []
    max_turns = 10  # Ограничим до 10 обменов

    # Начальное сообщение пользователя
    initial_user_message = "Привет! Расскажи, какие у меня задачи?"
    print(f"User: {initial_user_message}")
    conversation.append({"role": "user", "content": initial_user_message, "timestamp": datetime.now().isoformat()})

    for turn in range(max_turns):
        # Получить ответ AI
        current_message = initial_user_message if turn == 0 else conversation[-1]["content"]
        ai_response = await chat_with_ai(current_message, user_id=user_id)
        print(f"AI: {ai_response}")
        conversation.append({"role": "assistant", "content": ai_response, "timestamp": datetime.now().isoformat()})

        # Генерировать следующее сообщение пользователя
        user_message = await generate_user_message(conversation)
        print(f"User: {user_message}")
        conversation.append({"role": "user", "content": user_message, "timestamp": datetime.now().isoformat()})

        # Проверка на завершение диалога
        if "пока" in user_message.lower() or "до свидания" in user_message.lower():
            break

    # Сохранить диалог в файл
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"live_dialog_{timestamp}.json"
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(conversation, f, ensure_ascii=False, indent=2)

    print(f"\nДиалог сохранен в {filename}")

if __name__ == "__main__":
    asyncio.run(live_dialog_test())