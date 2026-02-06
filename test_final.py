"""Финальный быстрый тест после всех фиксов"""
import asyncio
import os
os.environ['LOCAL'] = '1'

from ai_integration.chat import chat_with_ai
from models import Session, User, UserProfile, Task, Base, engine

async def final_test():
    print("\n🎯 ФИНАЛЬНЫЙ ТЕСТ ПОСЛЕ ИСПРАВЛЕНИЙ\n" + "="*60)
    
    user_id = 999888777
    
    # Инициализация
    Base.metadata.create_all(engine)
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(telegram_id=user_id, username='final_test', timezone='Europe/Moscow')
        session.add(user)
        session.commit()
        profile = UserProfile(user_id=user.id, city='Москва')
        session.add(profile)
        session.commit()
    
    user_db_id = user.id
    session.query(Task).filter_by(user_id=user_db_id).delete()
    session.commit()
    session.close()
    
    # ТЕСТ 1: Создание
    print("\n[1] Создание задачи...")
    try:
        session = Session()
        r = await chat_with_ai('Создай задачу позвонить Петрову завтра в 15:00', user_id=user_id, db_session=session)
        session.close()
        print(f"✅ {r.get('response', '')[:70]}")
    except Exception as e:
        print(f"❌ ОШИБКА: {e}")
    
    # ТЕСТ 2: Список
    print("\n[2] Показ списка...")
    try:
        session = Session()
        r = await chat_with_ai('Покажи мои задачи', user_id=user_id, db_session=session)
        session.close()
        print(f"✅ {r.get('response', '')[:70]}")
    except Exception as e:
        print(f"❌ ОШИБКА: {e}")
    
    # ТЕСТ 3: Редактирование
    print("\n[3] Редактирование...")
    try:
        session = Session()
        r = await chat_with_ai('Переименуй задачу про Петрова в срочный звонок', user_id=user_id, db_session=session)
        session.close()
        print(f"✅ {r.get('response', '')[:70]}")
    except Exception as e:
        print(f"❌ ОШИБКА: {e}")
    
    # ТЕСТ 4: Завершение
    print("\n[4] Завершение...")
    try:
        session = Session()
        r = await chat_with_ai('Отметь задачу звонок выполненной', user_id=user_id, db_session=session)
        session.close()
        print(f"✅ {r.get('response', '')[:70]}")
    except Exception as e:
        print(f"❌ ОШИБКА: {e}")
    
    # ТЕСТ 5: Проверка БД
    print("\n[5] Проверка БД...")
    try:
        session = Session()
        user_obj = session.query(User).filter_by(telegram_id=user_id).first()
        tasks = session.query(Task).filter_by(user_id=user_obj.id).all()
        session.close()
        print(f"✅ В БД {len(tasks)} задач")
        for t in tasks:
            print(f"   - {t.title} ({t.status})")
    except Exception as e:
        print(f"❌ ОШИБКА: {e}")
    
    print("\n" + "="*60)
    print("🎉 ТЕСТ ЗАВЕРШЕН!\n")

if __name__ == '__main__':
    asyncio.run(final_test())
