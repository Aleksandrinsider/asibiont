"""
Ручное тестирование - вводи команды сам
"""
import asyncio
from ai_integration import chat_with_ai
from models import SessionLocal, User, Task

USER_ID = 146333757

async def manual_chat():
    print("\n" + "="*70)
    print("РУЧНОЙ ТЕСТ - вводи команды сам")
    print("="*70)
    print(f"Пользователь: {USER_ID}")
    print("Команды: 'exit' для выхода, 'tasks' для просмотра БД")
    print("="*70 + "\n")
    
    session = SessionLocal()
    user = session.query(User).filter_by(telegram_id=USER_ID).first()
    
    if not user:
        print("❌ Пользователь не найден!")
        return
    
    step = 1
    while True:
        # Показываем задачи в БД
        tasks = session.query(Task).filter_by(user_id=user.id).all()
        print(f"\n[БД: {len(tasks)} задач]", end=" ")
        
        # Ввод пользователя
        user_msg = input(f"\n[{step}] YOU: ").strip()
        
        if user_msg.lower() == 'exit':
            break
        
        if user_msg.lower() == 'tasks':
            session.expire_all()
            tasks = session.query(Task).filter_by(user_id=user.id).all()
            if tasks:
                print(f"\n📋 Задач в БД: {len(tasks)}")
                for t in tasks:
                    print(f"  - {t.title} ({t.reminder_time})")
            else:
                print("\n📋 Задач нет")
            continue
        
        if not user_msg:
            continue
        
        # Вызов AI
        try:
            print(f"\n[Ожидание ответа...]")
            ai_response = await asyncio.wait_for(
                chat_with_ai(message=user_msg, user_id=USER_ID),
                timeout=120.0
            )
            
            print(f"\nAI: {ai_response}\n")
            
            # Обновляем БД
            session.expire_all()
            tasks = session.query(Task).filter_by(user_id=user.id).all()
            print(f"[После ответа: {len(tasks)} задач в БД]")
            
        except asyncio.TimeoutError:
            print("⏱️ Таймаут!")
        except Exception as e:
            print(f"❌ Ошибка: {e}")
        
        step += 1
    
    session.close()
    print("\n✅ Завершено!")


if __name__ == "__main__":
    asyncio.run(manual_chat())
