"""
Тест интеллектуального советника get_task_advice
Проверяет глубокий анализ с учётом: профиля, всех задач, паттернов, делегирования
"""

import asyncio
import sys
from datetime import datetime, timedelta
from ai_integration.handlers import get_task_advice, add_task, update_profile
from ai_integration.memory import decrypt_data
from models import Session, User, Task

# Тестовый user_id (замените на реальный)
TEST_USER_ID = 12345678


async def setup_test_scenario():
    """Создаём сценарий: перегруженный пользователь с застрявшей сложной задачей"""
    session = Session()
    
    # Проверяем/создаём пользователя
    user = session.query(User).filter_by(telegram_id=TEST_USER_ID).first()
    if not user:
        print("ℹ️  Пользователь не найден. Создаём тестового пользователя...")
        user = User(
            telegram_id=TEST_USER_ID,
            username="test_user",
            first_name="Тестовый Пользователь",
            timezone="Europe/Moscow"
        )
        session.add(user)
        session.commit()
        print(f"✅ Создан пользователь: {user.first_name}")
    else:
        print(f"✅ Пользователь найден: {user.first_name or user.username}")
    
    # Обновляем профиль для контекста
    update_profile(
        user_id=TEST_USER_ID,
        city="Москва",
        skills="Python, AI, дизайн интерфейсов",
        interests="стартапы, машинное обучение, спорт",
        session=session
    )
    print("✅ Профиль обновлён")
    
    # Создаём 12 активных задач (перегрузка)
    task_titles = [
        "Запустить MVP стартапа",
        "Подготовить презентацию для инвесторов",
        "Реализовать API интеграцию",
        "Провести code review",
        "Написать документацию",
        "Организовать митинг команды",
        "Изучить новый фреймворк",
        "Закрыть баги в production",
        "Оптимизировать базу данных",
        "Подготовить отчёт для CEO",
        "Запланировать спринт",
        "Обновить CI/CD пайплайн"
    ]
    
    for i, title in enumerate(task_titles):
        # Делаем первую задачу застрявшей (14 дней назад)
        created = datetime.now() - timedelta(days=14) if i == 0 else datetime.now() - timedelta(days=i)
        
        task = Task(
            user_id=user.id,
            title=title,
            description=f"Описание задачи: {title}" if i == 0 else "",
            status='active',
            created_at=created,
            reminder_time=datetime.now() - timedelta(days=2) if i == 0 else None  # Пропущенный дедлайн
        )
        session.add(task)
    
    session.commit()
    print(f"✅ Создано {len(task_titles)} задач (первая висит 14 дней с пропущенным дедлайном)")
    
    # Примечание: модель Contact не существует, делегирование будет предлагаться на основе других данных
    print("ℹ️  Проверка делегирования пропущена (модель Contact не реализована)")
    
    # Находим первую задачу (застрявшую)
    stuck_task = session.query(Task).filter_by(
        user_id=user.id,
        title="Запустить MVP стартапа"
    ).first()
    
    session.close()
    return stuck_task.id if stuck_task else None, user.id


async def test_intelligent_advice():
    """Тестируем интеллектуального советника"""
    
    print("\n" + "="*80)
    print("🧠 ТЕСТ ИНТЕЛЛЕКТУАЛЬНОГО СОВЕТНИКА get_task_advice")
    print("="*80 + "\n")
    
    # Настраиваем тестовый сценарий
    print("📋 Шаг 1: Настройка тестового сценария...")
    task_id, user_id = await setup_test_scenario()
    
    if not task_id:
        print("❌ Не удалось создать тестовый сценарий")
        return
    
    print(f"✅ Сценарий готов. Task ID: {task_id}\n")
    
    # Вызываем интеллектуального советника
    print("🔍 Шаг 2: Вызов get_task_advice()...")
    print("-"*80)
    
    result = await get_task_advice(
        task_id=task_id,
        user_id=TEST_USER_ID
    )
    
    print("\n📊 РЕЗУЛЬТАТ АНАЛИЗА:")
    print("="*80)
    print(result)
    print("="*80 + "\n")
    
    # Проверяем что в результате есть ключевые элементы
    checks = {
        "✅ ПЛАН ДЕЙСТВИЙ": "✅" in result or "ПЛАН" in result.upper(),
        "💡 ОПТИМИЗАЦИЯ": "💡" in result or "ОПТИМИЗ" in result.upper(),
        "⚠️ РИСКИ": "⚠" in result or "РИСК" in result.upper(),
        "Персонализация": "навык" in result.lower() or "опыт" in result.lower() or "профиль" in result.lower(),
        "Упоминание перегрузки": "ПЕРЕГРУЗК" in result.upper() or "МНОГО" in result.upper() or "12" in result or "задач" in result.lower(),
        "Упоминание дедлайна": "ДЕДЛАЙН" in result.upper() or "ПРОПУЩЕН" in result.upper() or "14 дн" in result.lower(),
    }
    
    print("✅ ПРОВЕРКА КАЧЕСТВА АНАЛИЗА:")
    for check_name, passed in checks.items():
        status = "✅" if passed else "❌"
        print(f"  {status} {check_name}")
    
    success_rate = sum(checks.values()) / len(checks) * 100
    print(f"\n📈 Качество анализа: {success_rate:.1f}%")
    
    if success_rate >= 70:
        print("✅ Тест пройден! Советник использует контекст.")
    else:
        print("⚠️  Тест частично пройден. Советник может быть улучшен.")


async def cleanup():
    """Очистка тестовых данных"""
    session = Session()
    user = session.query(User).filter_by(telegram_id=TEST_USER_ID).first()
    
    if user:
        # Удаляем тестовые задачи
        session.query(Task).filter_by(user_id=user.id).delete()
        # session.query(Contact).filter_by(user_id=user.id).delete()  # Contact не реализован
        session.commit()
        print("\n🧹 Тестовые данные удалены")
    
    session.close()


if __name__ == "__main__":
    print("\n" + "STARTING INTELLIGENT ADVISOR TEST")
    print("="*80 + "\n")
    
    try:
        asyncio.run(test_intelligent_advice())
        
        # Спрашиваем про очистку
        cleanup_choice = input("\n🧹 Удалить тестовые данные? (y/n): ").lower()
        if cleanup_choice == 'y':
            asyncio.run(cleanup())
        else:
            print("ℹ️  Тестовые данные оставлены для дальнейшей проверки")
            
    except KeyboardInterrupt:
        print("\n\n⚠️  Тест прерван пользователем")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
