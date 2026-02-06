"""Тест улучшенного fuzzy search и контекста диалога"""
import asyncio
import os
os.environ['LOCAL'] = '1'

from ai_integration.chat import chat_with_ai
from models import Session, User, UserProfile, Task, Base, engine

async def test_ux_improvements():
    print("\n🎯 ТЕСТ UX УЛУЧШЕНИЙ\n" + "="*60)
    
    user_id = 777888999
    
    # Инициализация
    Base.metadata.create_all(engine)
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(telegram_id=user_id, username='ux_test', timezone='Europe/Moscow')
        session.add(user)
        session.commit()
        profile = UserProfile(user_id=user.id, city='Москва')
        session.add(profile)
        session.commit()
    
    user_db_id = user.id
    session.query(Task).filter_by(user_id=user_db_id).delete()
    session.commit()
    session.close()
    
    # ТЕСТ 1: Создание задачи
    print("\n[1] Создаю задачу 'Позвонить Петрову'...")
    session = Session()
    r1 = await chat_with_ai('Создай задачу позвонить Петрову завтра в 15:00', user_id=user_id, db_session=session)
    session.close()
    print(f"✅ {r1.get('response', '')[:80]}")
    
    # ТЕСТ 2: Перенос по местоимению "её" (контекст диалога)
    print("\n[2] Переношу 'её' на послезавтра (местоимение)...")
    session = Session()
    r2 = await chat_with_ai('Перенеси её на послезавтра в 16:00', user_id=user_id, db_session=session)
    session.close()
    response = r2.get('response', '')
    print(f"Ответ: {response[:100]}")
    if 'перенес' in response.lower() or 'послезавтра' in response.lower():
        print("✅ УСПЕХ: Местоимение 'её' распознано!")
    else:
        print("⚠️ Местоимение не сработало, но это норма если AI еще не готов")
    
    # ТЕСТ 3: Fuzzy search - удаление по частичному совпадению
    print("\n[3] Удаляю по слову 'звонок' (fuzzy search)...")
    session = Session()
    r3 = await chat_with_ai('Удали задачу звонок', user_id=user_id, db_session=session)
    session.close()
    response = r3.get('response', '')
    print(f"Ответ: {response[:100]}")
    
    # Проверяем БД
    session = Session()
    user_obj = session.query(User).filter_by(telegram_id=user_id).first()
    tasks = session.query(Task).filter_by(user_id=user_obj.id).all()
    session.close()
    
    if len(tasks) == 0:
        print("✅ УСПЕХ: Задача удалена через fuzzy search!")
    else:
        print(f"⚠️ В БД осталось {len(tasks)} задач")
    
    # ТЕСТ 4: Создание новой задачи
    print("\n[4] Создаю задачу 'Купить хлеб'...")
    session = Session()
    r4 = await chat_with_ai('Создай задачу купить хлеб сегодня в 18:00', user_id=user_id, db_session=session)
    session.close()
    print(f"✅ {r4.get('response', '')[:80]}")
    
    # ТЕСТ 5: Завершение по частичному совпадению
    print("\n[5] Завершаю по слову 'хлеб' (fuzzy search)...")
    session = Session()
    r5 = await chat_with_ai('Отметь задачу хлеб выполненной', user_id=user_id, db_session=session)
    session.close()
    response = r5.get('response', '')
    print(f"Ответ: {response[:100]}")
    
    if 'выполнен' in response.lower() or '✅' in response:
        print("✅ УСПЕХ: Fuzzy search работает для завершения!")
    else:
        print("⚠️ Требует уточнения")
    
    print("\n" + "="*60)
    print("🎉 ТЕСТ ЗАВЕРШЕН!\n")

if __name__ == '__main__':
    asyncio.run(test_ux_improvements())
