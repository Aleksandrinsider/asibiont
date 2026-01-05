"""
Интерактивный тест AI диалога
Диалог развивается органично на основе ответов AI
"""
import asyncio
import os
from models import SessionLocal, User
from ai_integration import chat_with_ai
from datetime import datetime
import pytz

# Диалог развивается на основе ответов AI
async def test_ai_dialogue():
    """Тестирует AI диалог с органичным развитием разговора"""
    print("\n" + "="*80)
    print("ИНТЕРАКТИВНЫЙ ТЕСТ AI ДИАЛОГА")
    print("="*80 + "\n")
    
    session = SessionLocal()
    
    try:
        # Находим или создаём тестового пользователя
        user = session.query(User).filter_by(telegram_id=146333757).first()
        
        if not user:
            print("[INFO] Создаю тестового пользователя...")
            user = User(
                telegram_id=146333757,
                username="test_user",
                timezone="Europe/Moscow"
            )
            session.add(user)
            session.commit()
            print("[OK] Пользователь создан\n")
        else:
            print(f"[OK] Пользователь: {user.username} (ID: {user.telegram_id})")
            print(f"    Timezone: {user.timezone or 'UTC'}\n")
        
        # Диалог: ответы пользователя зависят от вопросов AI
        conversation = [
            ("user", "Привет!"),
            ("ai", None),  # AI ответит
            ("user", "Поручи @testuser подготовить отчет до завтра 15:00"),
            ("ai", None),  # AI должен вызвать delegate_task БЕЗ вопросов
            ("user", "Добавь задачу: позвонить клиенту через 2 часа"),
            ("ai", None),
            ("user", "Покажи мои задачи"),
            ("ai", None),
            ("user", "Я переехал в Москву и теперь работаю в Google как Senior Engineer"),
            ("ai", None)
        ]
        
        print("="*80)
        print("ИНТЕРАКТИВНЫЙ ДИАЛОГ")
        print("Пользователь отвечает на основе вопросов AI")
        print("="*80 + "\n")
        
        step = 0
        for i, (role, content) in enumerate(conversation):
            if role == "user":
                step += 1
                user_message = content
                
                print(f"[Шаг {step}]")
                print(f"👤 Пользователь: {user_message}")
                print()
                
                try:
                    # Вызываем AI
                    response = await chat_with_ai(
                        message=user_message,
                        user_id=user.telegram_id
                    )
                    
                    print(f"🤖 AI: {response}")
                    print()
                    print("-" * 80)
                    print()
                    
                    # Задержка между сообщениями
                    await asyncio.sleep(1)
                    
                except Exception as e:
                    print(f"❌ ОШИБКА: {e}")
                    import traceback
                    traceback.print_exc()
                    print()
                    print("-" * 80)
                    print()
                    break
        
        print("="*80)
        print("ДИАЛОГ ЗАВЕРШЁН")
        print("="*80)
        
    finally:
        session.close()

if __name__ == "__main__":
    asyncio.run(test_ai_dialogue())
