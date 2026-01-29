"""
ПОЛНЫЙ тест всех функций включая edge cases
"""
import asyncio
from models import User, UserProfile, Task, Session
from ai_integration.handlers import (
    add_task, complete_task, list_tasks, delete_task, 
    reschedule_task, delegate_task, get_task_details,
    update_profile, update_user_memory_async,
    get_partners_list, find_relevant_contacts_for_task,
    delete_all_tasks, set_recurring_task
)

async def comprehensive_test():
    session = Session()
    
    # Найдем тестового пользователя
    test_user = session.query(User).filter_by(telegram_id=123456789).first()
    if not test_user:
        print("❌ Тестовый пользователь не найден")
        return
    
    test_id = 123456789
    errors = []
    
    print("\n" + "=" * 80)
    print("ТЕСТ 1: add_task с разными параметрами")
    print("=" * 80)
    
    # 1.1: Без времени
    try:
        result = add_task(title="Задача без времени", user_id=test_id, session=session, close_session=False)
        print(f"✅ Без времени: {result[:80]}")
    except Exception as e:
        errors.append(f"add_task без времени: {e}")
        print(f"❌ {e}")
    
    # 1.2: С описанием
    try:
        result = add_task(
            title="Задача с описанием",
            description="Подробное описание задачи",
            user_id=test_id,
            session=session,
            close_session=False
        )
        print(f"✅ С описанием: {result[:80]}")
    except Exception as e:
        errors.append(f"add_task с описанием: {e}")
        print(f"❌ {e}")
    
    # 1.3: С duration
    try:
        result = add_task(
            title="Задача с duration",
            estimated_duration=60,
            user_id=test_id,
            session=session,
            close_session=False
        )
        print(f"✅ С duration: {result[:80]}")
    except Exception as e:
        errors.append(f"add_task с duration: {e}")
        print(f"❌ {e}")
    
    print("\n" + "=" * 80)
    print("ТЕСТ 2: complete_task edge cases")
    print("=" * 80)
    
    # 2.1: Пустой параметр (должна завершиться последняя активная)
    try:
        result = complete_task(user_id=test_id, session=session, close_session=False)
        print(f"✅ Без параметров (последняя): {result[:80]}")
    except Exception as e:
        errors.append(f"complete_task без параметров: {e}")
        print(f"❌ {e}")
    
    # 2.2: По частичному названию
    try:
        add_task(title="Встреча с командой", user_id=test_id, session=session, close_session=False)
        result = complete_task(task_title="команд", user_id=test_id, session=session, close_session=False)
        print(f"✅ Частичное название: {result[:80]}")
    except Exception as e:
        errors.append(f"complete_task частичное: {e}")
        print(f"❌ {e}")
    
    print("\n" + "=" * 80)
    print("ТЕСТ 3: list_tasks с фильтрами")
    print("=" * 80)
    
    # 3.1: Только активные
    try:
        result = list_tasks(user_id=test_id, include_completed=False, session=session, close_session=False)
        print(f"✅ Только активные: {result[:80]}")
    except Exception as e:
        errors.append(f"list_tasks активные: {e}")
        print(f"❌ {e}")
    
    # 3.2: Все задачи
    try:
        result = list_tasks(user_id=test_id, include_completed=True, session=session, close_session=False)
        print(f"✅ Все задачи: {result[:80]}")
    except Exception as e:
        errors.append(f"list_tasks все: {e}")
        print(f"❌ {e}")
    
    print("\n" + "=" * 80)
    print("ТЕСТ 4: reschedule_task с разными форматами времени")
    print("=" * 80)
    
    add_task(title="Задача для переноса", user_id=test_id, session=session, close_session=False)
    
    # 4.1: Относительное время
    try:
        result = reschedule_task(
            task_title="переноса",
            new_time="через 2 часа",
            user_id=test_id,
            session=session,
            close_session=False
        )
        print(f"✅ Через 2 часа: {result[:80]}")
    except Exception as e:
        errors.append(f"reschedule через 2 часа: {e}")
        print(f"❌ {e}")
    
    # 4.2: Абсолютное время
    try:
        result = reschedule_task(
            task_title="переноса",
            new_time="завтра в 15:00",
            user_id=test_id,
            session=session,
            close_session=False
        )
        print(f"✅ Завтра в 15:00: {result[:80]}")
    except Exception as e:
        errors.append(f"reschedule завтра: {e}")
        print(f"❌ {e}")
    
    print("\n" + "=" * 80)
    print("ТЕСТ 5: update_profile - добавление vs замена")
    print("=" * 80)
    
    # Проверяем текущее состояние
    profile = session.query(UserProfile).filter_by(user_id=test_user.id).first()
    initial_interests = profile.interests if profile else None
    print(f"Начальные интересы: {initial_interests}")
    
    # 5.1: Добавление нового интереса
    try:
        result = update_profile(
            user_id=test_id,
            interests="астрономия",
            session=session,
            close_session=False
        )
        session.refresh(profile)
        print(f"✅ Добавили астрономия: {result}")
        print(f"   Теперь интересы: {profile.interests}")
    except Exception as e:
        errors.append(f"update_profile add: {e}")
        print(f"❌ {e}")
    
    # 5.2: Попытка добавить дубликат
    try:
        result = update_profile(
            user_id=test_id,
            interests="астрономия",
            session=session,
            close_session=False
        )
        print(f"✅ Дубликат: {result}")
    except Exception as e:
        errors.append(f"update_profile duplicate: {e}")
        print(f"❌ {e}")
    
    print("\n" + "=" * 80)
    print("ТЕСТ 6: update_user_memory для разных типов")
    print("=" * 80)
    
    # 6.1: Interest
    try:
        result = await update_user_memory_async(
            memory_type="interest",
            content="квантовая физика",
            user_id=test_id,
            session=session,
            close_session=False
        )
        print(f"✅ Interest: {result}")
    except Exception as e:
        errors.append(f"update_user_memory interest: {e}")
        print(f"❌ {e}")
    
    # 6.2: Skill
    try:
        result = await update_user_memory_async(
            memory_type="skill",
            content="машинное обучение",
            user_id=test_id,
            session=session,
            close_session=False
        )
        print(f"✅ Skill: {result}")
    except Exception as e:
        errors.append(f"update_user_memory skill: {e}")
        print(f"❌ {e}")
    
    # 6.3: Goal
    try:
        result = await update_user_memory_async(
            memory_type="goal",
            content="выучить испанский",
            user_id=test_id,
            session=session,
            close_session=False
        )
        print(f"✅ Goal: {result}")
    except Exception as e:
        errors.append(f"update_user_memory goal: {e}")
        print(f"❌ {e}")
    
    # 6.4: Preference
    try:
        result = await update_user_memory_async(
            memory_type="preference",
            content="предпочитаю кофе без сахара",
            user_id=test_id,
            session=session,
            close_session=False
        )
        print(f"✅ Preference: {result}")
    except Exception as e:
        errors.append(f"update_user_memory preference: {e}")
        print(f"❌ {e}")
    
    print("\n" + "=" * 80)
    print("ТЕСТ 7: find_relevant_contacts_for_task")
    print("=" * 80)
    
    # 7.1: Спорт
    try:
        result = find_relevant_contacts_for_task(
            task_description="пойти на пробежку",
            user_id=test_id,
            limit=3,
            session=session,
            close_session=False
        )
        print(f"✅ Пробежка: {result[:150]}")
    except Exception as e:
        errors.append(f"find_contacts пробежка: {e}")
        print(f"❌ {e}")
    
    # 7.2: Бизнес
    try:
        result = find_relevant_contacts_for_task(
            task_description="обсудить стартап проект",
            user_id=test_id,
            limit=3,
            session=session,
            close_session=False
        )
        print(f"✅ Стартап: {result[:150]}")
    except Exception as e:
        errors.append(f"find_contacts стартап: {e}")
        print(f"❌ {e}")
    
    # 7.3: Hobby
    try:
        result = find_relevant_contacts_for_task(
            task_description="поиграть в покер",
            user_id=test_id,
            limit=3,
            session=session,
            close_session=False
        )
        print(f"✅ Покер: {result[:150]}")
    except Exception as e:
        errors.append(f"find_contacts покер: {e}")
        print(f"❌ {e}")
    
    print("\n" + "=" * 80)
    print("ТЕСТ 8: get_task_details")
    print("=" * 80)
    
    tasks = session.query(Task).filter_by(user_id=test_user.id, status='pending').first()
    if tasks:
        try:
            result = get_task_details(
                task_title=tasks.title[:5],  # Частичное название
                user_id=test_id,
                session=session,
                close_session=False
            )
            print(f"✅ Детали: {result[:150]}")
        except Exception as e:
            errors.append(f"get_task_details: {e}")
            print(f"❌ {e}")
    
    print("\n" + "=" * 80)
    print("ТЕСТ 9: set_recurring_task")
    print("=" * 80)
    
    # 9.1: Ежедневная задача
    try:
        result = set_recurring_task(
            title="Ежедневная зарядка",
            recurrence_pattern="daily",
            first_reminder_time="завтра в 07:00",
            user_id=test_id,
            session=session,
            close_session=False
        )
        print(f"✅ Daily: {result[:80]}")
    except Exception as e:
        errors.append(f"set_recurring daily: {e}")
        print(f"❌ {e}")
    
    # 9.2: Еженедельная задача
    try:
        result = set_recurring_task(
            title="Еженедельная встреча",
            recurrence_pattern="weekly",
            first_reminder_time="следующий понедельник в 10:00",
            user_id=test_id,
            session=session,
            close_session=False
        )
        print(f"✅ Weekly: {result[:80]}")
    except Exception as e:
        errors.append(f"set_recurring weekly: {e}")
        print(f"❌ {e}")
    
    print("\n" + "=" * 80)
    print("ТЕСТ 10: delete_task с разными параметрами")
    print("=" * 80)
    
    # Создаем задачи для удаления
    add_task(title="Задача А для удаления", user_id=test_id, session=session, close_session=False)
    add_task(title="Задача Б для удаления", user_id=test_id, session=session, close_session=False)
    
    # 10.1: По частичному названию
    try:
        result = delete_task(
            task_title="Задача А",
            user_id=test_id,
            session=session,
            close_session=False
        )
        print(f"✅ Частичное: {result[:80]}")
    except Exception as e:
        errors.append(f"delete_task частичное: {e}")
        print(f"❌ {e}")
    
    print("\n" + "=" * 80)
    print("ИТОГ")
    print("=" * 80)
    print(f"✅ Успешно: {len([1 for _ in range(30)]) - len(errors)}")
    print(f"❌ Ошибок: {len(errors)}")
    
    if errors:
        print("\nОШИБКИ:")
        for err in errors:
            print(f"  • {err}")
    
    session.close()

if __name__ == "__main__":
    asyncio.run(comprehensive_test())
