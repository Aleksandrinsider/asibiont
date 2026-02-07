"""
Финальный тест: все 14 команд с гарантированным tool_choice
"""
import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ai_integration.chat import chat_with_ai
from models import Session, User, UserProfile, Base, engine, Task
from datetime import datetime, timedelta

async def test_all_14():
    user_id = 123456789
    Base.metadata.create_all(engine)
    session = Session()
    
    # Setup
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(telegram_id=user_id, username='test', first_name='Test', timezone='Europe/Moscow')
        session.add(user)
        session.commit()
        profile = UserProfile(user_id=user.id, interests='Python, AI', goals='Запустить продукт', city='Moscow')
        session.add(profile)
        session.commit()
    
    session.query(Task).filter_by(user_id=user.id).delete()
    session.commit()
    
    test_results = []
    
    # 1. add_task
    print("\n1️⃣ TEST: add_task")
    response = await chat_with_ai('Напомни завтра в 10 утра позвонить', user_id=user_id, db_session=session)
    success = len(response.get('actions', [])) > 0 and response.get('actions', [{}])[0].get('tool') == 'add_task'
    test_results.append(('add_task', success))
    print(f"  {'✅' if success else '❌'} Actions: {response.get('actions', [])}")
    
    # 2. list_tasks
    print("\n2️⃣ TEST: list_tasks")
    response = await chat_with_ai('Покажи мои задачи', user_id=user_id, db_session=session)
    success = len(response.get('actions', [])) > 0 and response.get('actions', [{}])[0].get('tool') == 'list_tasks'
    test_results.append(('list_tasks', success))
    print(f"  {'✅' if success else '❌'} Actions: {response.get('actions', [])}")
    
    # 3. complete_task
    print("\n3️⃣ TEST: complete_task")
    response = await chat_with_ai('Готово, сделал', user_id=user_id, db_session=session)
    success = len(response.get('actions', [])) > 0 and response.get('actions', [{}])[0].get('tool') == 'complete_task'
    test_results.append(('complete_task', success))
    print(f"  {'✅' if success else '❌'} Actions: {response.get('actions', [])}")
    
    # 4. reschedule_task  
    print("\n4️⃣ TEST: reschedule_task")
    response = await chat_with_ai('Перенеси задачу на завтра', user_id=user_id, db_session=session)
    success = len(response.get('actions', [])) > 0 and response.get('actions', [{}])[0].get('tool') == 'reschedule_task'
    test_results.append(('reschedule_task', success))
    print(f"  {'✅' if success else '❌'} Actions: {response.get('actions', [])}")
    
    # 5. edit_task
    print("\n5️⃣ TEST: edit_task")
    response = await chat_with_ai('Измени название задачи на "Проверить почту"', user_id=user_id, db_session=session)
    success = len(response.get('actions', [])) > 0 and response.get('actions', [{}])[0].get('tool') == 'edit_task'
    test_results.append(('edit_task', success))
    print(f"  {'✅' if success else '❌'} Actions: {response.get('actions', [])}")
    
    # 6. delete_task
    print("\n6️⃣ TEST: delete_task")
    response = await chat_with_ai('Удали задачу про звонок', user_id=user_id, db_session=session)
    success = len(response.get('actions', [])) > 0 and response.get('actions', [{}])[0].get('tool') == 'delete_task'
    test_results.append(('delete_task', success))
    print(f"  {'✅' if success else '❌'} Actions: {response.get('actions', [])}")
    
    # Create tasks for analysis
    task1 = Task(user_id=user.id, title='Срочная задача', status='pending', reminder_time=datetime.now() + timedelta(hours=1))
    task2 = Task(user_id=user.id, title='Обычная задача', status='pending', reminder_time=datetime.now() + timedelta(days=1))
    session.add_all([task1, task2])
    session.commit()
    
    # 7. analyze_tasks
    print("\n7️⃣ TEST: analyze_tasks")
    response = await chat_with_ai('Что делать в первую очередь?', user_id=user_id, db_session=session)
    success = len(response.get('actions', [])) > 0 and response.get('actions', [{}])[0].get('tool') == 'analyze_tasks'
    test_results.append(('analyze_tasks', success))
    print(f"  {'✅' if success else '❌'} Actions: {response.get('actions', [])}")
    
    # 8. find_partners
    print("\n8️⃣ TEST: find_partners")
    response = await chat_with_ai('Найди партнеров по AI', user_id=user_id, db_session=session)
    success = len(response.get('actions', [])) > 0 and response.get('actions', [{}])[0].get('tool') == 'find_partners'
    test_results.append(('find_partners', success))
    print(f"  {'✅' if success else '❌'} Actions: {response.get('actions', [])}")
    
    # 9. find_relevant_contacts_for_task
    print("\n9️⃣ TEST: find_relevant_contacts_for_task")
    response = await chat_with_ai('Кто может помочь с Python?', user_id=user_id, db_session=session)
    success = len(response.get('actions', [])) > 0 and response.get('actions', [{}])[0].get('tool') == 'find_relevant_contacts_for_task'
    test_results.append(('find_relevant_contacts', success))
    print(f"  {'✅' if success else '❌'} Actions: {response.get('actions', [])}")
    
    # 10. delegate_task (должен спросить кому)
    print("\n🔟 TEST: delegate_task")
    response = await chat_with_ai('Делегируй задачу Ивану', user_id=user_id, db_session=session)
    success = len(response.get('actions', [])) > 0 and response.get('actions', [{}])[0].get('tool') == 'delegate_task'
    test_results.append(('delegate_task', success))
    print(f"  {'✅' if success else '❌'} Actions: {response.get('actions', [])}")
    
    # 11. update_profile
    print("\n1️⃣1️⃣ TEST: update_profile")
    response = await chat_with_ai('Обнови профиль: добавь навык TypeScript', user_id=user_id, db_session=session)
    success = len(response.get('actions', [])) > 0 and response.get('actions', [{}])[0].get('tool') == 'update_profile'
    test_results.append(('update_profile', success))
    print(f"  {'✅' if success else '❌'} Actions: {response.get('actions', [])}")
    
    # 12. show_profile
    print("\n1️⃣2️⃣ TEST: show_profile")
    response = await chat_with_ai('Покажи мой профиль', user_id=user_id, db_session=session)
    success = len(response.get('actions', [])) > 0 and response.get('actions', [{}])[0].get('tool') == 'show_profile'
    test_results.append(('show_profile', success))
    print(f"  {'✅' if success else '❌'} Actions: {response.get('actions', [])}")
    
    # 13. get_task_details
    print("\n1️⃣3️⃣ TEST: get_task_details")
    response = await chat_with_ai('Покажи детали задачи про срочную', user_id=user_id, db_session=session)
    success = len(response.get('actions', [])) > 0 and response.get('actions', [{}])[0].get('tool') == 'get_task_details'
    test_results.append(('get_task_details', success))
    print(f"  {'✅' if success else '❌'} Actions: {response.get('actions', [])}")
    
    # 14. delete_all_tasks
    print("\n1️⃣4️⃣ TEST: delete_all_tasks")
    response = await chat_with_ai('Удали все задачи', user_id=user_id, db_session=session)
    success = len(response.get('actions', [])) > 0 and response.get('actions', [{}])[0].get('tool') == 'delete_all_tasks'
    test_results.append(('delete_all_tasks', success))
    print(f"  {'✅' if success else '❌'} Actions: {response.get('actions', [])}")
    
    # Summary
    print("\n" + "="*60)
    print("📊 ИТОГОВЫЙ РЕЗУЛЬТАТ:")
    print("="*60)
    passed = sum(1 for _, success in test_results if success)
    total = len(test_results)
    print(f"Прошло: {passed}/{total} ({passed*100//total}%)\n")
    
    for name, success in test_results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"  {status}  {name}")
    
    print("\n" + "="*60)
    if passed == total:
        print("🎉 ВСЕ 14 КОМАНД РАБОТАЮТ С ГАРАНТИЕЙ!")
    else:
        print(f"⚠️  {total - passed} команд требуют доработки")
    print("="*60)
    
    session.close()

if __name__ == '__main__':
    asyncio.run(test_all_14())
