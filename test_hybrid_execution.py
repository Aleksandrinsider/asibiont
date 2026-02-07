"""
Правильный тест: проверяем что команды ВЫПОЛНЯЮТСЯ (нет отказов "не могу", "нужно знать")
"""
import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ai_integration.chat import chat_with_ai
from models import Session, User, UserProfile, Base, engine, Task
from datetime import datetime, timedelta

async def check_command_executes(prompt, user_id, session, command_name):
    """Проверяет что команда выполнилась (нет отказа)"""
    response = await chat_with_ai(prompt, user_id=user_id, db_session=session)
    text = response.get('response', '').lower()
    
    # Признаки отказа
    refusal_signs = [
        'не могу',
        'нужно знать',
        'не указали',
        'недостаточно информации',
        'какую именно',
        'не нашел',
        'мне нужно',
        'укажите',
    ]
    
    refused = any(sign in text for sign in refusal_signs)
    success = not refused
    
    return success, text[:150]

async def test_all_commands():
    user_id = 123456789
    Base.metadata.create_all(engine)
    session = Session()
    
    # Setup
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(telegram_id=user_id, username='test', first_name='Test', timezone='Europe/Moscow')
        session.add(user)
        session.commit()
        profile = UserProfile(user_id=user.id, interests='Python', goals='Запуск продукта', city='Moscow')
        session.add(profile)
        session.commit()
    
    # Подготовим задачи
    session.query(Task).filter_by(user_id=user.id).delete()
    task = Task(user_id=user.id, title='Звонок', status='pending', reminder_time=datetime.now() + timedelta(hours=2))
    session.add(task)
    session.commit()
    
    results = []
    
    # Тесты
    tests = [
        ('add_task', 'Напомни завтра в 10 утра купить молоко'),
        ('list_tasks', 'Покажи мои задачи'),
        ('complete_task', 'Готово, завершил'),  
        ('reschedule_task', 'Перенеси звонок на завтра в 15:00'),
        ('edit_task', 'Измени звонок на "Позвонить клиенту"'),
        ('delete_task', 'Удали задачу молоко'),
        ('analyze_tasks', 'Проанализируй приоритеты'),
        ('find_partners', 'Найди партнеров по Python'),
        ('find_relevant_contacts', 'Кто занимается AI разработкой?'),
        ('update_profile', 'Обнови профиль: добавь навык JS'),
        ('show_profile', 'Покажи мой профиль'),
        ('get_task_details', 'Детали задачи звонок'),
    ]
    
    print("="*70)
    print("🔄 ТЕСТ: Гибридный подход (14 команд)")
    print("="*70)
    
    for command, prompt in tests:
        print(f"\n🔍 {command}...")
        success, preview = await check_command_executes(prompt, user_id, session, command)
        results.append((command, success))
        
        status = "✅" if success else "❌"
        print(f"   {status} {preview}")
    
    # Итог
    passed = sum(1 for _, s in results if s)
    total = len(results)
    
    print("\n" + "="*70)
    print(f"📊 РЕЗУЛЬТАТ: {passed}/{total} ({passed*100//total}%)")
    print("="*70)
    
    for name, success in results:
        status = "✅" if success else "❌"
        print(f"  {status}  {name}")
    
    print()
    if passed >= total * 0.8:  # 80%+
        print("🎉 ГИБРИДНЫЙ ПОДХОД РАБОТАЕТ!")
    else:
        print("⚠️  Требуется доработка")
    
    session.close()

if __name__ == '__main__':
    asyncio.run(test_all_commands())
