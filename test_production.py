"""
АВТОМАТИЧЕСКИЙ тест для production сервера на Railway
AI генерирует сообщения за пользователя через DeepSeek
Открывайте dashboard в браузере: https://task-production-31b6.up.railway.app/dashboard
User: aleksandrinsider

ВАЖНО: Запускать БЕЗ LOCAL=1 чтобы использовать production PostgreSQL!
"""
import asyncio
import sys
import os
import aiohttp
import json

# Убираем LOCAL чтобы использовать production БД
if 'LOCAL' in os.environ:
    del os.environ['LOCAL']

from ai_integration import chat_with_ai
from models import Session, Task, User, UserProfile, Interaction
from datetime import datetime
from config import DEEPSEEK_API_KEY

sys.stdout.reconfigure(encoding='utf-8')

TELEGRAM_ID = 146333757  # aleksandrinsider

def get_user_id():
    """Получить internal user.id из telegram_id"""
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=TELEGRAM_ID).first()
        if user:
            return user.id
        return None
    finally:
        session.close()

def show_state():
    """Показать текущее состояние БД"""
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=TELEGRAM_ID).first()
        if not user:
            print("❌ Пользователь не найден")
            return
        
        tasks = session.query(Task).filter_by(user_id=user.id).order_by(Task.reminder_time).all()
        if tasks:
            print(f"\n📋 ЗАДАЧИ В БД ({len(tasks)}):")
            for task in tasks:
                emoji = "✅" if task.status == "completed" else "⏳"
                reminder = task.reminder_time.strftime("%d.%m %H:%M") if task.reminder_time else "нет"
                print(f"  {emoji} {task.title} - {reminder} ({task.status})")
        else:
            print(f"\n📋 Задач нет")
        
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            print(f"\n👤 ПРОФИЛЬ:")
            if profile.city:
                print(f"  Город: {profile.city}")
            if profile.interests:
                print(f"  Интересы: {profile.interests}")
    finally:
        session.close()

async def generate_user_message(context, situation):
    """Генерирует сообщение пользователя через DeepSeek API"""
    
    prompt = f"""Ты — обычный человек, который общается с AI-помощником для управления задачами.

СИТУАЦИЯ: {situation}

ПРЕДЫДУЩИЙ ДИАЛОГ:
{chr(10).join([f"{'Ты' if msg['role'] == 'user' else 'Бот'}: {msg['content']}" for msg in context[-6:]]) if context else "Начало диалога"}

Напиши короткое естественное сообщение (1-2 предложения) для этой ситуации.
При добавлении задач указывай конкретное время.
Пиши ТОЛЬКО текст сообщения без пояснений."""

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.deepseek.com/chat/completions",
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "deepseek-chat",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.9,
                    "max_tokens": 100
                },
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    message = data["choices"][0]["message"]["content"].strip()
                    return message.strip('"').strip("'").strip()
                else:
                    return "Привет, покажи мои задачи"
    except Exception as e:
        print(f"⚠️ Ошибка генерации: {e}")
        return "Что у меня в планах?"

async def chat_session():
    """Автоматическая сессия где AI генерирует сообщения пользователя"""
    context = []
    message_count = 0
    
    # Получаем internal user.id из telegram_id
    USER_ID = get_user_id()
    if not USER_ID:
        print("❌ Пользователь не найден в БД")
        return
    
    print("""
╔════════════════════════════════════════════════════════════════════╗
║         АВТОМАТИЧЕСКИЙ ЧАТ (AI → AI) - PRODUCTION                  ║
║            https://task-production-31b6.up.railway.app             ║
╚════════════════════════════════════════════════════════════════════╝

📱 User: aleksandrinsider (ID: 146333757)
🌐 Dashboard: https://task-production-31b6.up.railway.app/dashboard

🤖 AI (DeepSeek) генерирует сообщения пользователя
🤖 Бот отвечает с боевым промптом
📊 Все сохраняется в панели dashboard

Откройте dashboard в браузере и наблюдайте!
    """)
    
    show_state()
    print("\n" + "="*70)
    
    situations = [
        "Поприветствуй и спроси про задачи на сегодня",
        "Добавь задачу со встречей завтра в 10:00",
        "Попроси напомнить через 30 минут про почту",
        "Спроси что в планах",
        "Скажи что выполнил задачу про встречу",
        "Попроси показать оставшиеся задачи",
        "Добавь задачу позвонить клиенту в 18:00",
        "Удали задачу про почту",
        "Упомяни интерес к AI и разработке",
        "Спроси про завтрашние дела",
        "Попроси перенести звонок на 19:00",
        "Скажи что звонок сделан",
        "Добавь встречу с инвестором послезавтра в 14:00",
        "Попроси список всех задач",
        "Удали встречу с инвестором",
        "Добавь срочную задачу написать отчет через час",
        "Попроси показать все дела",
        "Скажи что отчет готов",
        "Добавь презентацию проекта в понедельник в 11:00",
        "Поблагодари за работу",
    ]
    
    for i, situation in enumerate(situations, 1):
        try:
            message_count += 1
            
            print(f"\n{'='*70}")
            print(f"СООБЩЕНИЕ {message_count}/{len(situations)}")
            print(f"{'='*70}")
            print(f"📝 Ситуация: {situation}")
            print(f"⏳ AI генерирует сообщение...")
            
            # AI генерирует сообщение пользователя
            user_message = await generate_user_message(context, situation)
            print(f"\n💬 Пользователь (AI): {user_message}")
            
            # Отправляем боту с боевым промптом
            print(f"⏳ Отправка боту...")
            response = await chat_with_ai(user_message, context, TELEGRAM_ID)
            
            # Сохраняем в БД
            session = Session()
            try:
                # Сохраняем сообщение пользователя
                user_interaction = Interaction(
                    user_id=USER_ID,
                    message_type='user',
                    content=user_message,
                    created_at=datetime.utcnow()
                )
                session.add(user_interaction)
                
                # Сохраняем ответ бота
                ai_interaction = Interaction(
                    user_id=USER_ID,
                    message_type='ai',
                    content=response,
                    created_at=datetime.utcnow()
                )
                session.add(ai_interaction)
                session.commit()
            finally:
                session.close()
            
            # Показываем ответ
            print(f"\n🤖 Бот: {response[:150]}..." if len(response) > 150 else f"\n🤖 Бот: {response}")
            
            # Обновляем контекст
            context.append({"role": "user", "content": user_message})
            context.append({"role": "assistant", "content": response})
            
            if len(context) > 20:
                context = context[-20:]
            
            # Состояние БД каждые 5 сообщений
            if message_count % 5 == 0:
                show_state()
            
            print("\n" + "-"*70)
            await asyncio.sleep(2)
            
        except KeyboardInterrupt:
            print("\n\n⚠️ Прервано (Ctrl+C)")
            break
        except Exception as e:
            print(f"\n❌ ОШИБКА: {e}")
            import traceback
            traceback.print_exc()
            await asyncio.sleep(1)
    
    print(f"\n{'='*70}")
    print("✅ ТЕСТ ЗАВЕРШЕН!")
    print(f"{'='*70}")
    show_state()
    print(f"\n📊 Всего сообщений: {message_count}")
    print("\nПроверьте диалог в dashboard:")
    print("https://task-production-31b6.up.railway.app/dashboard")

async def main():
    await chat_session()

if __name__ == "__main__":
    asyncio.run(main())
