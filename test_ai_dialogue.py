"""
Тест диалога: AI-пользователь общается с AI-агентом
"""
import asyncio
import sys
import os
from datetime import datetime, timedelta
import pytz
import aiohttp
import json

sys.path.insert(0, '.')

from models import User, Task, Base, Subscription
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from ai_integration.chat import chat_with_ai
from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL


async def generate_user_message(conversation_history, agent_last_response):
    """Генерирует сообщение от лица пользователя через DeepSeek API"""
    
    user_prompt = f"""Ты - обычный пользователь, который общается с AI-помощником для управления задачами (ASI Biont).

ТВОЯ РОЛЬ:
- Веди естественный диалог
- Создавай задачи, спрашивай о них, редактируй
- Иногда делегируй задачи контакту @colleague
- Реагируй на предложения агента
- Будь реалистичным: иногда забывай детали, меняй планы

КОНТЕКСТ:
- Ты занятой менеджер, живешь в Москве
- Сейчас вечер: {datetime.now().strftime('%d.%m.%Y %H:%M')}
- У тебя есть коллега @colleague для делегирования

ИСТОРИЯ ДИАЛОГА:
{conversation_history}

ПОСЛЕДНИЙ ОТВЕТ АГЕНТА:
{agent_last_response}

Напиши ОДНО КОРОТКОЕ сообщение (1-2 предложения) как реакцию на последний ответ агента.
Пиши ТОЛЬКО сам текст сообщения, без пояснений."""

    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    
    data = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": "Ты - обычный пользователь приложения для управления задачами. Отвечай кратко и естественно."},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.9,
        "max_tokens": 150
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=30)) as response:
            if response.status == 200:
                result = await response.json()
                message = result["choices"][0]["message"]["content"].strip()
                # Убираем возможные кавычки
                message = message.strip('"\'')
                return message
            else:
                return "привет"


async def run_dialogue_test():
    """Запускает тест-диалог между AI-пользователем и AI-агентом"""
    
    print("=" * 80)
    print("ТЕСТ ДИАЛОГА: AI-ПОЛЬЗОВАТЕЛЬ ↔ AI-АГЕНТ")
    print("=" * 80)
    print()
    
    # Создаем тестовую сессию с SQLite
    engine = create_engine("sqlite:///test_dialogue.db")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    
    # Создаем тестового пользователя
    test_user_id = 888888
    test_user = session.query(User).filter_by(telegram_id=test_user_id).first()
    if not test_user:
        test_user = User(
            telegram_id=test_user_id,
            username='test_manager',
            first_name='Тест',
            timezone='Europe/Moscow',
            conversation_state='normal'
        )
        session.add(test_user)
        session.commit()
    
    # Создаем активную подписку для теста
    from models import Subscription
    from datetime import timedelta
    import pytz
    
    subscription = session.query(Subscription).filter_by(user_id=test_user.id, status="active").first()
    if not subscription:
        subscription = Subscription(
            user_id=test_user.id,
            telegram_id=test_user.telegram_id,
            status="active",
            start_date=datetime.now(pytz.UTC),
            end_date=datetime.now(pytz.UTC) + timedelta(days=30)
        )
        session.add(subscription)
        session.commit()
    
    # Очищаем старые задачи
    old_tasks = session.query(Task).filter_by(user_id=test_user.id).all()
    for task in old_tasks:
        session.delete(task)
    session.commit()
    
    conversation_history = []
    num_turns = 4  # Уменьшаем количество раундов для быстрого теста
    
    # Первое сообщение от пользователя
    user_message = "привет, как дела?"
    
    try:
        for turn in range(num_turns):
            print(f"{'─' * 80}")
            print(f"РАУНД {turn + 1}/{num_turns}")
            print(f"{'─' * 80}")
            print()
            
            # Пользователь пишет
            print(f"👤 ПОЛЬЗОВАТЕЛЬ: {user_message}")
            print()
            
            conversation_history.append(f"Пользователь: {user_message}")
            
            # Агент отвечает
            try:
                agent_response = await chat_with_ai(
                    message=user_message,
                    user_id=test_user_id,
                    db_session=session
                )
            except Exception as e:
                agent_response = f"[ОШИБКА: {str(e)}]"
            
            print(f"🤖 АГЕНТ: {agent_response}")
            print()
            
            conversation_history.append(f"Агент: {agent_response}")
            
            # Небольшая пауза для реалистичности
            await asyncio.sleep(0.5)
            
            # Если не последний раунд - генерируем следующее сообщение пользователя
            if turn < num_turns - 1:
                # Ограничиваем историю последними 6 сообщениями
                recent_history = '\n'.join(conversation_history[-6:])
                
                try:
                    user_message = await generate_user_message(recent_history, agent_response)
                except Exception as e:
                    print(f"⚠️ Ошибка генерации сообщения пользователя: {e}")
                    user_message = "покажи мои задачи"
                
                await asyncio.sleep(1)
    
    except KeyboardInterrupt:
        print("\n⚠️  Тест прерван пользователем")
    
    print()
    print("=" * 80)
    print("ИТОГИ ДИАЛОГА")
    print("=" * 80)
    print()
    
    # Показываем созданные задачи
    tasks = session.query(Task).filter_by(user_id=test_user.id).all()
    if tasks:
        print(f"📋 Создано задач: {len(tasks)}")
        for task in tasks:
            status = "✓" if task.status == "completed" else "•"
            time_info = task.reminder_time.strftime('%d.%m %H:%M') if task.reminder_time else "без времени"
            delegation_info = f" → @{task.delegated_to_username}" if task.delegated_to_username else ""
            print(f"  {status} {task.title} ({time_info}){delegation_info}")
    else:
        print("📋 Задачи не созданы")
    
    print()
    print("✓ Тест завершен")
    
    # Очистка
    session.close()


if __name__ == "__main__":
    # Устанавливаем FREE_ACCESS_MODE для теста
    import os
    os.environ['FREE_ACCESS_MODE'] = '1'
    
    # Устанавливаем UTF-8 для вывода в консоль
    import sys
    if sys.platform == 'win32':
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
    
    # Проверяем наличие API ключа
    if not DEEPSEEK_API_KEY or DEEPSEEK_API_KEY == "your-api-key-here":
        print("❌ ОШИБКА: Не настроен DEEPSEEK_API_KEY в config.py")
        sys.exit(1)
    
    # Запускаем тест
    asyncio.run(run_dialogue_test())
