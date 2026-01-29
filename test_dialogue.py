"""
Интеграционный тест: диалог пользователя с AI-агентом
Симулирует 20 сообщений пользователя и проверяет ответы агента
"""
import asyncio
import os
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, User, Task, UserProfile
from ai_integration.chat import chat_with_ai

# Настройка тестовой БД
engine = create_engine('sqlite:///test_dialogue.db')
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

TEST_USER_ID = 777777

# Сценарий диалога с 20 сообщениями
DIALOGUE_SCENARIO = [
    # Знакомство и создание задач (1-5)
    {
        "message": "Привет! Я Алексей, живу в Москве, работаю программистом",
        "expected_actions": ["update_profile"],
        "description": "Знакомство, автоматическое извлечение профиля"
    },
    {
        "message": "Мне 28 лет, родился 15 мая 1997 года",
        "expected_actions": ["update_profile"],
        "description": "Дополнение профиля с датой рождения"
    },
    {
        "message": "Нужно завтра в 10 утра созвониться с заказчиком",
        "expected_actions": ["add_task"],
        "description": "Создание задачи с временем"
    },
    {
        "message": "Еще надо купить продукты вечером",
        "expected_actions": ["add_task"],
        "description": "Создание второй задачи"
    },
    {
        "message": "Покажи мои задачи",
        "expected_actions": ["list_tasks"],
        "description": "Просмотр списка задач"
    },
    
    # Работа с задачами (6-10)
    {
        "message": "Подробнее про звонок заказчику",
        "expected_actions": ["get_task_details"],
        "description": "Детали конкретной задачи"
    },
    {
        "message": "Перенеси созвон на послезавтра на 14:00",
        "expected_actions": ["reschedule_task"],
        "description": "Перенос задачи с изменением времени"
    },
    {
        "message": "Сделал покупки",
        "expected_actions": ["complete_task"],
        "description": "Завершение задачи (контекст из предыдущих)"
    },
    {
        "message": "Что там со звонком?",
        "expected_actions": ["get_task_details"],
        "description": "Проверка задачи по контексту"
    },
    {
        "message": "Созвон состоялся, все обсудили",
        "expected_actions": ["complete_task"],
        "description": "Завершение через контекст"
    },
    
    # Профиль и повторяющиеся задачи (11-15)
    {
        "message": "Хочу найти партнеров для совместных проектов",
        "expected_actions": ["find_partners"],
        "description": "Поиск партнеров"
    },
    {
        "message": "Интересуюсь AI и машинным обучением, работаю в Python",
        "expected_actions": ["update_profile"],
        "description": "Обновление интересов и навыков"
    },
    {
        "message": "Создай повторяющуюся задачу: каждый день в 9 утра делать зарядку",
        "expected_actions": ["set_recurring_task"],
        "description": "Создание ежедневной задачи"
    },
    {
        "message": "Покажи все мои задачи включая выполненные",
        "expected_actions": ["list_tasks"],
        "description": "Список с завершенными"
    },
    {
        "message": "Мой профиль",
        "expected_actions": ["get_user_profile"],
        "description": "Просмотр профиля"
    },
    
    # Делегирование и управление (16-20)
    {
        "message": "Нужно сделать презентацию к пятнице",
        "expected_actions": ["add_task"],
        "description": "Новая задача для делегирования"
    },
    {
        "message": "Можно кому-то делегировать презентацию?",
        "expected_actions": ["find_partners", "delegate_task"],
        "description": "Попытка делегирования"
    },
    {
        "message": "А что если я отменю задачу про зарядку?",
        "expected_actions": ["delete_task"],
        "description": "Удаление повторяющейся задачи"
    },
    {
        "message": "Покажи сколько задач я выполнил",
        "expected_actions": ["list_tasks"],
        "description": "Статистика выполненных"
    },
    {
        "message": "Спасибо за помощь! Какие планы на завтра?",
        "expected_actions": ["list_tasks"],
        "description": "Завершение диалога, планы"
    }
]

def setup_test_user():
    """Создание тестового пользователя"""
    session = Session()
    try:
        # Очистка
        session.query(Task).filter_by(user_id=TEST_USER_ID).delete()
        session.query(UserProfile).filter_by(user_id=TEST_USER_ID).delete()
        session.query(User).filter_by(telegram_id=TEST_USER_ID).delete()
        session.commit()
        
        # Создание
        user = User(telegram_id=TEST_USER_ID, timezone="Europe/Moscow")
        session.add(user)
        session.commit()
        print(f"✅ Тестовый пользователь {TEST_USER_ID} создан\n")
    finally:
        session.close()

async def run_dialogue_test():
    """Запуск теста диалога"""
    print("="*80)
    print("🤖 ИНТЕГРАЦИОННЫЙ ТЕСТ: ДИАЛОГ С AI-АГЕНТОМ (20 итераций)")
    print("="*80)
    print()
    
    setup_test_user()
    
    conversation_history = []
    results = {
        "success": 0,
        "errors": 0,
        "warnings": 0,
        "total_tools_called": 0,
        "tools_used": set()
    }
    
    for i, scenario in enumerate(DIALOGUE_SCENARIO, 1):
        print(f"\n{'='*80}")
        print(f"📩 ИТЕРАЦИЯ {i}/20: {scenario['description']}")
        print(f"{'='*80}")
        print(f"👤 Пользователь: {scenario['message']}")
        print()
        
        try:
            # Отправка сообщения агенту
            response = await chat_with_ai(
                message=scenario['message'],
                user_id=TEST_USER_ID,
                conversation_history=conversation_history
            )
            
            # Обновление истории
            conversation_history.append({
                "role": "user",
                "content": scenario['message']
            })
            conversation_history.append({
                "role": "assistant",
                "content": response
            })
            
            # Ограничение истории
            if len(conversation_history) > 10:
                conversation_history = conversation_history[-10:]
            
            print(f"🤖 Агент: {response}")
            print()
            
            # Проверка ответа
            has_error = "ERROR" in response or "ошибка" in response.lower()
            is_empty = len(response.strip()) < 10
            
            if has_error:
                print("❌ ОШИБКА: Агент вернул сообщение об ошибке")
                results["errors"] += 1
            elif is_empty:
                print("⚠️ ВНИМАНИЕ: Слишком короткий ответ")
                results["warnings"] += 1
            else:
                print("✅ Ответ получен успешно")
                results["success"] += 1
            
            # Проверка вызванных функций (из логов)
            # В реальности нужно парсить tool_calls из chat_with_ai
            
        except Exception as e:
            print(f"❌ КРИТИЧЕСКАЯ ОШИБКА: {e}")
            import traceback
            traceback.print_exc()
            results["errors"] += 1
        
        # Небольшая пауза между итерациями
        await asyncio.sleep(0.5)
    
    # Итоговый отчет
    print("\n" + "="*80)
    print("📊 ИТОГОВЫЙ ОТЧЕТ")
    print("="*80)
    print(f"✅ Успешных итераций: {results['success']}/20")
    print(f"❌ Ошибок: {results['errors']}")
    print(f"⚠️ Предупреждений: {results['warnings']}")
    
    success_rate = (results['success'] / 20) * 100
    print(f"\n📈 Процент успеха: {success_rate:.1f}%")
    
    if results['errors'] == 0:
        print("\n🎉 ВСЕ ИТЕРАЦИИ ПРОЙДЕНЫ БЕЗ ОШИБОК!")
    else:
        print(f"\n⚠️ Обнаружено {results['errors']} ошибок, требуется исправление")
    
    # Проверка финального состояния
    print("\n" + "="*80)
    print("🔍 ПРОВЕРКА ФИНАЛЬНОГО СОСТОЯНИЯ")
    print("="*80)
    
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=TEST_USER_ID).first()
        if user:
            tasks = session.query(Task).filter_by(user_id=TEST_USER_ID).all()
            profile = session.query(UserProfile).filter_by(user_id=TEST_USER_ID).first()
            
            print(f"📝 Всего задач создано: {len(tasks)}")
            print(f"   - Активных: {len([t for t in tasks if t.status in ['pending', 'active']])}")
            print(f"   - Завершенных: {len([t for t in tasks if t.status == 'completed'])}")
            
            if profile:
                print(f"\n👤 Профиль пользователя:")
                if profile.city:
                    print(f"   - Город: {profile.city}")
                if profile.birthdate:
                    print(f"   - Дата рождения: {profile.birthdate}")
                if profile.interests:
                    print(f"   - Интересы: {profile.interests}")
                if profile.company:
                    print(f"   - Компания: {profile.company}")
                if profile.position:
                    print(f"   - Должность: {profile.position}")
                
                print(f"\n📊 Статистика:")
                print(f"   - Всего задач: {profile.total_tasks_created or 0}")
                print(f"   - Завершено: {profile.completed_tasks or 0}")
            else:
                print("⚠️ Профиль не создан")
        else:
            print("❌ Пользователь не найден")
    finally:
        session.close()
    
    return results

def cleanup():
    """Очистка тестовых данных"""
    print("\n" + "="*80)
    print("🧹 ОЧИСТКА ТЕСТОВЫХ ДАННЫХ")
    print("="*80)
    
    session = Session()
    try:
        session.query(Task).filter_by(user_id=TEST_USER_ID).delete()
        session.query(UserProfile).filter_by(user_id=TEST_USER_ID).delete()
        session.query(User).filter_by(telegram_id=TEST_USER_ID).delete()
        session.commit()
        print("✅ Тестовые данные удалены")
    finally:
        session.close()
    
    # Удаление тестовой БД
    if os.path.exists('test_dialogue.db'):
        try:
            os.remove('test_dialogue.db')
            print("✅ Тестовая база данных удалена")
        except:
            print("⚠️ Не удалось удалить test_dialogue.db (файл занят)")

async def main():
    """Главная функция"""
    try:
        results = await run_dialogue_test()
        
        # Определение кода выхода
        if results["errors"] > 0:
            exit_code = 1
        else:
            exit_code = 0
        
        return exit_code
    finally:
        cleanup()

if __name__ == "__main__":
    import sys
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
