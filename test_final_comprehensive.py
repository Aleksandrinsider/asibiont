"""
ФИНАЛЬНЫЙ ПОЛНЫЙ ТЕСТ ВСЕХ ФУНКЦИЙ
"""
import asyncio
from models import User, UserProfile, Task, Session
from ai_integration.handlers import (
    add_task, complete_task, list_tasks, delete_task, 
    reschedule_task, get_task_details,
    update_profile, update_user_memory_async,
    get_partners_list, find_relevant_contacts_for_task,
    delete_all_tasks
)

async def test():
    s = Session()
    u = s.query(User).filter_by(telegram_id=123456789).first()
    if not u:
        print("❌ User not found")
        return
    
    uid = 123456789
    errors = []
    ok = 0
    
    print("=" * 60)
    print("COMPREHENSIVE TEST")
    print("=" * 60)
    
    # 1. add_task - разные сценарии
    print("\n1. add_task")
    try:
        r = add_task(title="Тест1", user_id=uid, session=s)
        print(f"✅ Без времени")
        ok += 1
    except Exception as e:
        errors.append(f"add_task: {e}")
        print(f"❌ {e}")
    
    try:
        r = add_task(title="Тест2", description="Описание", reminder_time="завтра в 10:00", user_id=uid, session=s)
        print(f"✅ С описанием и временем")
        ok += 1
    except Exception as e:
        errors.append(f"add_task+desc: {e}")
        print(f"❌ {e}")
    
    # 2. list_tasks
    print("\n2. list_tasks")
    try:
        r = list_tasks(user_id=uid, session=s, include_completed=False)
        print(f"✅ Активные задачи")
        ok += 1
    except Exception as e:
        errors.append(f"list_tasks: {e}")
        print(f"❌ {e}")
    
    try:
        r = list_tasks(user_id=uid, session=s, include_completed=True)
        print(f"✅ Все задачи")
        ok += 1
    except Exception as e:
        errors.append(f"list_tasks+completed: {e}")
        print(f"❌ {e}")
    
    # 3. get_task_details
    print("\n3. get_task_details")
    try:
        tasks = s.query(Task).filter_by(user_id=u.id, status='pending').first()
        if tasks:
            r = get_task_details(task_title=tasks.title[:5], user_id=uid, session=s)
            print(f"✅ Детали задачи")
            ok += 1
    except Exception as e:
        errors.append(f"get_task_details: {e}")
        print(f"❌ {e}")
    
    # 4. reschedule_task
    print("\n4. reschedule_task")
    try:
        r = reschedule_task(task_title="Тест", new_time="через 2 часа", user_id=uid, session=s)
        print(f"✅ Перенос через 2 часа")
        ok += 1
    except Exception as e:
        errors.append(f"reschedule: {e}")
        print(f"❌ {e}")
    
    try:
        r = reschedule_task(task_title="Тест", new_time="завтра в 15:00", user_id=uid, session=s)
        print(f"✅ Перенос на завтра")
        ok += 1
    except Exception as e:
        errors.append(f"reschedule2: {e}")
        print(f"❌ {e}")
    
    # 5. update_profile - проверка добавления
    print("\n5. update_profile - append mode")
    try:
        profile = s.query(UserProfile).filter_by(user_id=u.id).first()
        before = profile.interests if profile else None
        
        r = update_profile(user_id=uid, interests="робототехника", session=s)
        s.refresh(profile)
        after = profile.interests
        
        if "робототехника" in after and (before is None or "робототехника" not in before):
            print(f"✅ Добавили интерес (было: {before}, стало: {after})")
            ok += 1
        else:
            print(f"⚠️ Интерес не добавлен корректно")
    except Exception as e:
        errors.append(f"update_profile: {e}")
        print(f"❌ {e}")
    
    # Дубликат
    try:
        r = update_profile(user_id=uid, interests="робототехника", session=s)
        if "уже есть" in r.lower():
            print(f"✅ Дубликат определен")
            ok += 1
    except Exception as e:
        errors.append(f"update_profile dup: {e}")
        print(f"❌ {e}")
    
    # 6. update_user_memory_async - разные типы
    print("\n6. update_user_memory_async")
    try:
        r = await update_user_memory_async(memory_type="interest", content="нейросети", user_id=uid, session=s)
        print(f"✅ Interest")
        ok += 1
    except Exception as e:
        errors.append(f"update_memory interest: {e}")
        print(f"❌ {e}")
    
    try:
        r = await update_user_memory_async(memory_type="skill", content="тестирование", user_id=uid, session=s)
        print(f"✅ Skill")
        ok += 1
    except Exception as e:
        errors.append(f"update_memory skill: {e}")
        print(f"❌ {e}")
    
    try:
        r = await update_user_memory_async(memory_type="goal", content="автоматизировать процессы", user_id=uid, session=s)
        print(f"✅ Goal")
        ok += 1
    except Exception as e:
        errors.append(f"update_memory goal: {e}")
        print(f"❌ {e}")
    
    # 7. get_partners_list
    print("\n7. get_partners_list")
    try:
        partners = get_partners_list(user_id=uid, session=s)
        print(f"✅ Найдено партнеров: {len(partners)}")
        ok += 1
    except Exception as e:
        errors.append(f"get_partners: {e}")
        print(f"❌ {e}")
    
    # 8. find_relevant_contacts_for_task
    print("\n8. find_relevant_contacts_for_task")
    try:
        r = find_relevant_contacts_for_task(task_description="пойти на пробежку", user_id=uid, limit=3, session=s)
        print(f"✅ Спорт: {r[:80]}")
        ok += 1
    except Exception as e:
        errors.append(f"find_contacts sport: {e}")
        print(f"❌ {e}")
    
    try:
        r = find_relevant_contacts_for_task(task_description="обсудить стартап", user_id=uid, limit=3, session=s)
        print(f"✅ Бизнес: {r[:80]}")
        ok += 1
    except Exception as e:
        errors.append(f"find_contacts business: {e}")
        print(f"❌ {e}")
    
    # 9. complete_task
    print("\n9. complete_task")
    try:
        r = complete_task(user_id=uid, session=s)  # Без параметров - последняя активная
        print(f"✅ Завершение последней задачи")
        ok += 1
    except Exception as e:
        errors.append(f"complete_task: {e}")
        print(f"❌ {e}")
    
    try:
        add_task(title="Задача для завершения", user_id=uid, session=s)
        r = complete_task(task_title="завершения", user_id=uid, session=s)
        print(f"✅ Завершение по названию")
        ok += 1
    except Exception as e:
        errors.append(f"complete_task+title: {e}")
        print(f"❌ {e}")
    
    # 10. delete_task
    print("\n10. delete_task")
    try:
        add_task(title="Задача для удаления", user_id=uid, session=s)
        r = delete_task(task_title="удаления", user_id=uid, session=s)
        print(f"✅ Удаление задачи")
        ok += 1
    except Exception as e:
        errors.append(f"delete_task: {e}")
        print(f"❌ {e}")
    
    # 11. delete_all_tasks
    print("\n11. delete_all_tasks")
    try:
        add_task(title="Тест для массового удаления 1", user_id=uid, session=s)
        add_task(title="Тест для массового удаления 2", user_id=uid, session=s)
        r = delete_all_tasks(user_id=uid, session=s)
        print(f"✅ Массовое удаление")
        ok += 1
    except Exception as e:
        errors.append(f"delete_all: {e}")
        print(f"❌ {e}")
    
    s.close()
    
    print("\n" + "=" * 60)
    print(f"ИТОГ: {ok} успешно, {len(errors)} ошибок")
    print("=" * 60)
    
    if errors:
        print("\nОШИБКИ:")
        for e in errors:
            print(f"  • {e}")
    else:
        print("\n🎉 ВСЕ ТЕСТЫ ПРОШЛИ!")

if __name__ == "__main__":
    asyncio.run(test())
