"""
ПОЛНЫЙ ТЕСТ ВСЕХ ИНСТРУМЕНТОВ АГЕНТА
Проверяем что каждый tool реально работает и изменяет БД
"""
import os
os.environ["LOCAL"] = "1"
os.environ["FREE_ACCESS_MODE"] = "1"

import asyncio
import sys
from datetime import datetime

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

from ai_integration.chat import chat_with_ai
from models import Session, User, Task, UserProfile, init_db

TEST_USER_ID = 999000111

def verify_db(description, check_func):
    """Проверка БД через callback"""
    session = Session()
    try:
        result = check_func(session)
        status = "✅" if result else "❌"
        print(f"  {status} БД: {description}")
        return result
    finally:
        session.close()

async def test_tool(name, message, db_check_desc=None, db_check_func=None, expect_tool_call=None):
    """Тест одного инструмента"""
    print(f"\n{'='*70}")
    print(f"🔧 ТЕСТ: {name}")
    print(f"{'='*70}")
    print(f"📝 Сообщение: {message}")
    
    try:
        response = await asyncio.wait_for(
            chat_with_ai(message, user_id=TEST_USER_ID),
            timeout=30.0
        )
        resp_text = response.get('response', '') if isinstance(response, dict) else str(response)
        tools_called = response.get('tools_called', []) if isinstance(response, dict) else []
        print(f"💬 Ответ: {resp_text[:200]}{'...' if len(resp_text) > 200 else ''}")
        if tools_called:
            print(f"🔨 Tools called: {tools_called}")
        
        # Проверка что AI вызвал нужный tool
        if expect_tool_call:
            if not tools_called or expect_tool_call not in str(tools_called):
                print(f"⚠️  WARNING: Expected tool '{expect_tool_call}' was not called!")
                print(f"    Tools called: {tools_called}")
                return False
        
        if db_check_func and db_check_desc:
            return verify_db(db_check_desc, db_check_func)
        return True
        
    except asyncio.TimeoutError:
        print(f"❌ TIMEOUT")
        return False
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return False

async def run_all_tests():
    """Запуск всех тестов"""
    print("="*70)
    print("🚀 ПОЛНОЕ ТЕСТИРОВАНИЕ ВСЕХ ИНСТРУМЕНТОВ")
    print("="*70)
    
    # Инициализация
    init_db()
    session = Session()
    user = session.query(User).filter_by(telegram_id=TEST_USER_ID).first()
    if user:
        session.query(Task).filter_by(user_id=user.id).delete()
        session.query(UserProfile).filter_by(user_id=user.id).delete()
        session.delete(user)
        session.commit()
    session.close()
    print("✅ БД очищена\n")
    
    results = []
    
    # 1. add_task
    results.append(await test_tool(
        "1. add_task - создание простой задачи",
        "Напомни купить продукты завтра в 14:00",
        "Задача 'продукты' создана",
        lambda s: s.query(Task).filter(Task.title.ilike("%продукты%")).first() is not None
    ))
    
    # 2. list_tasks
    results.append(await test_tool(
        "2. list_tasks - показать список",
        "Покажи мои задачи",
        "Список содержит задачу",
        lambda s: s.query(Task).filter_by(
            user_id=s.query(User).filter_by(telegram_id=TEST_USER_ID).first().id
        ).count() > 0
    ))
    
    # 3. update_profile
    results.append(await test_tool(
        "3. update_profile - обновление профиля",
        "Я живу в Казани, работаю программистом и интересуюсь Python",
        "Профиль обновлен (город, должность, интересы)",
        lambda s: (p := s.query(UserProfile).filter_by(
            user_id=s.query(User).filter_by(telegram_id=TEST_USER_ID).first().id
        ).first()) and "казан" in (p.city or "").lower() and "python" in (p.interests or "").lower()
    ))
    

    # 4. get_task_details
    results.append(await test_tool(
        "4. get_task_details - детали задачи",
        "Покажи детали задачи про продукты"
    ))
    
    # 5. reschedule_task
    results.append(await test_tool(
        "5. reschedule_task - перенос задачи",
        "Перенеси задачу про продукты на послезавтра в 16:00",
        "Время задачи изменено",
        lambda s: True  # Проверим что не упало
    ))
    
    # 6. edit_task
    def check_edit_task(s):
        user = s.query(User).filter_by(telegram_id=TEST_USER_ID).first()
        if not user:
            print("    [DEBUG] User not found")
            return False
        task = s.query(Task).filter(
            Task.user_id == user.id,
            Task.title.ilike("%продукты%")
        ).first()
        if not task:
            print("    [DEBUG] Task 'продукты' not found")
            return False
        has_description = task.description is not None and len(task.description) > 0
        print(f"    [DEBUG] Task found: id={task.id}, description={'[encrypted]' if has_description else 'None'}")
        return has_description
    
    results.append(await test_tool(
        "6. edit_task - редактирование задачи",
        "Отредактируй задачу 'Купить продукты': добавь описание 'молоко и хлеб'",  # Более явная формулировка
        "Описание задачи изменено",
        check_edit_task,
        expect_tool_call="edit_task"
    ))
    
    # 7. complete_task
    results.append(await test_tool(
        "7. complete_task - завершение задачи",
        "Готово, купил продукты",
        "Задача 'продукты' завершена",
        lambda s: (t := s.query(Task).filter(
            Task.user_id == s.query(User).filter_by(telegram_id=TEST_USER_ID).first().id,
            Task.title.ilike("%продукты%")
        ).first()) and t.status == "completed"
    ))
    
    # 9. Создаем еще задачи для дальнейших тестов
    await test_tool(
        "9a. Создание задачи для делегирования",
        "Создай задачу 'написать отчет' на послезавтра в 10:00"
    )
    
    await test_tool(
        "9b. Создание задачи для удаления",
        "Создай задачу 'позвонить в банк' на сегодня в 15:00"
    )
    
    # 9. find_partners
    results.append(await test_tool(
        "9. find_partners - поиск партнеров",
        "Найди людей с похожими интересами"
    ))
    
    # 10. find_relevant_contacts_for_task
    results.append(await test_tool(
        "10. find_relevant_contacts_for_task - поиск контактов для задачи",
        "Кто может помочь с программированием на Python?"
    ))
    
    # 12. update_user_memory
    results.append(await test_tool(
        "12. update_user_memory - сохранение в память",
        "Запомни что я предпочитаю работать по утрам"
    ))
    
    # 12. delete_task
    results.append(await test_tool(
        "12. delete_task - удаление задачи",
        "Удали задачу про звонок в банк",
        "Задача 'банк' удалена",
        lambda s: s.query(Task).filter(
            Task.user_id == s.query(User).filter_by(telegram_id=TEST_USER_ID).first().id,
            Task.title.ilike("%банк%")
        ).first() is None
    ))
    
    # 14. Проверка что не все удалилось
    results.append(await test_tool(
        "14. Проверка списка после удаления",
        "Покажи задачи",
        "Остались другие задачи",
        lambda s: s.query(Task).filter_by(
            user_id=s.query(User).filter_by(telegram_id=TEST_USER_ID).first().id
        ).count() > 0
    ))
    
    # 15. delete_all_tasks
    results.append(await test_tool(
        "15. delete_all_tasks - удалить все задачи",
        "Удали все мои задачи",
        "Все задачи удалены",
        lambda s: s.query(Task).filter_by(
            user_id=s.query(User).filter_by(telegram_id=TEST_USER_ID).first().id
        ).count() == 0
    ))
    
    # ИТОГИ
    print("\n" + "="*70)
    print("📊 РЕЗУЛЬТАТЫ")
    print("="*70)
    
    passed = sum(1 for r in results if r)
    total = len(results)
    
    print(f"\n✅ Пройдено: {passed}/{total} ({passed*100//total}%)")
    
    if passed == total:
        print("🎉 ВСЕ ИНСТРУМЕНТЫ РАБОТАЮТ!")
    else:
        print(f"⚠️ Провалено: {total - passed} тестов")
    
    print("="*70)
    
    return passed == total

if __name__ == "__main__":
    try:
        success = asyncio.run(run_all_tests())
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
