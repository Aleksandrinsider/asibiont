"""Тест edge cases и потенциальных ошибок"""
import asyncio
import os
os.environ['LOCAL'] = '1'

from ai_integration.chat import chat_with_ai
from models import Session, User, UserProfile, Task

async def test_edge_cases():
    user_id = 111222333
    
    print("🔍 ТЕСТ EDGE CASES\n" + "="*60)
    
    # Инициализация
    from models import Base, engine
    Base.metadata.create_all(engine)
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(telegram_id=user_id, username='edge_test', timezone='Europe/Moscow')
        session.add(user)
        session.commit()
        profile = UserProfile(user_id=user.id, city='Москва')
        session.add(profile)
        session.commit()
    session.query(Task).filter_by(user_id=user.id).delete()
    session.commit()
    session.close()
    
    # TEST 1: Пустое сообщение
    print("\n[1] Пустое сообщение...")
    try:
        session = Session()
        r = await chat_with_ai('', user_id=user_id, db_session=session)
        session.close()
        print(f"✓ Обработано: {r.get('response', '')[:50]}")
    except Exception as e:
        print(f"✗ ОШИБКА: {e}")
    
    # TEST 2: Очень длинное сообщение
    print("\n[2] Очень длинное сообщение...")
    long_msg = "Привет " * 500  # 3000 слов
    try:
        session = Session()
        r = await chat_with_ai(long_msg, user_id=user_id, db_session=session)
        session.close()
        print(f"✓ Обработано: {len(r.get('response', ''))} символов")
    except Exception as e:
        print(f"✗ ОШИБКА: {e}")
    
    # TEST 3: Спецсимволы
    print("\n[3] Спецсимволы...")
    special_msg = "Создай задачу <script>alert('XSS')</script> на завтра в 10:00"
    try:
        session = Session()
        r = await chat_with_ai(special_msg, user_id=user_id, db_session=session)
        session.close()
        if '<script>' not in r.get('response', ''):
            print(f"✓ Спецсимволы безопасно обработаны")
        else:
            print(f"✗ УЯЗВИМОСТЬ: спецсимволы не экранированы")
    except Exception as e:
        print(f"✗ ОШИБКА: {e}")
    
    # TEST 4: SQL injection попытка
    print("\n[4] SQL injection тест...")
    sql_msg = "Покажи задачи WHERE 1=1; DROP TABLE tasks; --"
    try:
        session = Session()
        r = await chat_with_ai(sql_msg, user_id=user_id, db_session=session)
        session.close()
        # Проверяем что таблица tasks существует
        session2 = Session()
        count = session2.query(Task).count()
        session2.close()
        print(f"✓ SQL injection защищен (tasks table exists, {count} записей)")
    except Exception as e:
        print(f"✗ ОШИБКА: {e}")
    
    # TEST 5: Несуществующий user_id
    print("\n[5] Несуществующий user_id...")
    try:
        session = Session()
        r = await chat_with_ai('Привет', user_id=999999999999, db_session=session)
        session.close()
        print(f"✓ Обработано: {r.get('response', '')[:50]}")
    except Exception as e:
        print(f"✗ ОШИБКА: {e}")
    
    # TEST 6: Задача с None в reminder_time
    print("\n[6] Задача без времени напоминания...")
    try:
        session = Session()
        r = await chat_with_ai('Создай задачу купить молоко', user_id=user_id, db_session=session)
        session.close()
        print(f"✓ Ответ: {r.get('response', '')[:80]}")
        
        # Проверка что AI спросил время
        if 'время' in r.get('response', '').lower() or 'когда' in r.get('response', '').lower():
            print("  ✓ AI правильно спрашивает время")
        else:
            print("  ⚠ AI НЕ спросил время")
    except Exception as e:
        print(f"✗ ОШИБКА: {e}")
    
    # TEST 7: Одновременные запросы (race condition)
    print("\n[7] Одновременные запросы...")
    try:
        tasks = [
            chat_with_ai('Создай задачу 1 завтра в 10:00', user_id=user_id, db_session=Session()),
            chat_with_ai('Создай задачу 2 завтра в 11:00', user_id=user_id, db_session=Session()),
            chat_with_ai('Создай задачу 3 завтра в 12:00', user_id=user_id, db_session=Session()),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        errors = [r for r in results if isinstance(r, Exception)]
        if not errors:
            print(f"✓ Все {len(results)} запроса обработаны")
        else:
            print(f"✗ {len(errors)} ошибок из {len(results)}")
    except Exception as e:
        print(f"✗ ОШИБКА: {e}")
    
    # TEST 8: Unicode и эмодзи
    print("\n[8] Unicode и эмодзи...")
    try:
        session = Session()
        r = await chat_with_ai('Создай задачу 🎉🚀💡 на завтра в 15:00', user_id=user_id, db_session=session)
        session.close()
        print(f"✓ Unicode обработан: {r.get('response', '')[:50]}")
    except Exception as e:
        print(f"✗ ОШИБКА: {e}")
    
    # TEST 9: Очень много задач
    print("\n[9] Создание множества задач...")
    try:
        session = Session()
        user_obj = session.query(User).filter_by(telegram_id=user_id).first()
        # Создаем 50 задач напрямую
        for i in range(50):
            task = Task(
                user_id=user_obj.id,
                title=f"Тест задача {i}",
                status='pending'
            )
            session.add(task)
        session.commit()
        session.close()
        
        # Проверяем что список задач работает
        session = Session()
        r = await chat_with_ai('Покажи мои задачи', user_id=user_id, db_session=session)
        session.close()
        print(f"✓ Список с 50+ задачами: {len(r.get('response', ''))} символов")
    except Exception as e:
        print(f"✗ ОШИБКА: {e}")
    
    # TEST 10: Закрытая сессия
    print("\n[10] Закрытая сессия...")
    try:
        session = Session()
        session.close()  # Закрываем сразу
        r = await chat_with_ai('Привет', user_id=user_id, db_session=session)
        print(f"✗ Не должно работать с закрытой сессией!")
    except Exception as e:
        print(f"✓ Правильно обработана ошибка: {type(e).__name__}")
    
    print("\n" + "="*60)
    print("🎉 ТЕСТ ЗАВЕРШЕН\n")

if __name__ == '__main__':
    asyncio.run(test_edge_cases())
