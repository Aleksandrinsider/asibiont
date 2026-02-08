"""
Простой тест вызова инструментов
"""
import asyncio
from ai_integration.chat import chat_with_ai
from models import Session, User, UserProfile, Base, engine

async def test_tools():
    """Тест базовых инструментов"""
    
    user_id = 999888777
    Base.metadata.create_all(engine)
    session = Session()
    
    try:
        # Очистка
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if user:
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            if profile:
                session.delete(profile)
            session.commit()
            session.delete(user)
            session.commit()
        
        # Создаем пользователя
        user = User(telegram_id=user_id, username='test_user', first_name='Тест', timezone='Europe/Moscow')
        session.add(user)
        session.commit()
        
        print("="*60)
        print("[ТЕСТ ИНСТРУМЕНТОВ]")
        print("="*60)
        
        # Тест 1: list_tasks
        print("\n[ТЕСТ 1] Команда: 'покажи мои задачи'")
        response = await chat_with_ai("покажи мои задачи", user_id=user_id)
        tools_used = response.get('tools_used', [])
        print(f"Инструментов вызвано: {len(tools_used)}")
        if tools_used:
            print(f"Вызванные: {[t.get('name') for t in tools_used]}")
        print(f"Ответ: {response.get('response', '')[:100]}...")
        
        # Тест 2: add_task
        print("\n[ТЕСТ 2] Команда: 'создай задачу позвонить завтра в 10:00'")
        response2 = await chat_with_ai("создай задачу позвонить завтра в 10:00", user_id=user_id)
        tools_used2 = response2.get('tools_used', [])
        print(f"Инструментов вызвано: {len(tools_used2)}")
        if tools_used2:
            print(f"Вызванные: {[t.get('name') for t in tools_used2]}")
        print(f"Ответ: {response2.get('response', '')[:100]}...")
        
        # Тест 3: update_profile
        print("\n[ТЕСТ 3] Команда: 'Я основатель стартапа TechCorp'")
        response3 = await chat_with_ai("Я основатель стартапа TechCorp", user_id=user_id)
        tools_used3 = response3.get('tools_used', [])
        print(f"Инструментов вызвано: {len(tools_used3)}")
        if tools_used3:
            print(f"Вызванные: {[t.get('name') for t in tools_used3]}")
        print(f"Ответ: {response3.get('response', '')[:100]}...")
        
        # Итог
        total_tools = len(tools_used) + len(tools_used2) + len(tools_used3)
        print("\n" + "="*60)
        print(f"[ИТОГ] Всего инструментов вызвано: {total_tools} из 3 тестов")
        print("="*60)
        
        if total_tools == 0:
            print("❌ ПРОВАЛ: Инструменты НЕ вызываются!")
        elif total_tools == 3:
            print("✅ УСПЕХ: Все инструменты работают!")
        else:
            print(f"⚠️ ЧАСТИЧНО: Только {total_tools}/3 работают")
        
    finally:
        # Очистка
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if user:
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            if profile:
                session.delete(profile)
            session.commit()
            session.delete(user)
            session.commit()
        session.close()

if __name__ == '__main__':
    asyncio.run(test_tools())
