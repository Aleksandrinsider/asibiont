"""
Тест AI диалога с имитацией пользователя
"""
import asyncio
import os
from models import SessionLocal, User
from ai_integration import chat_with_ai
from datetime import datetime
import pytz

# Тестовые сценарии
TEST_SCENARIOS = [
    {
        "name": "Приветствие и использование контекста",
        "message": "Привет!",
        "expected": ["профиль", "задач", "контакт"]
    },
    {
        "name": "Делегирование задачи",
        "message": "Поручи @testuser подготовить отчет до завтра 15:00",
        "expected": ["delegate_task", "@testuser", "отчет"]
    },
    {
        "name": "Сложная задача - советы",
        "message": "Мне нужно сделать презентацию на 50 слайдов за 3 дня",
        "expected": ["конкретн", "подход", "альтернатив"]
    },
    {
        "name": "Поиск контактов",
        "message": "Найди мне разработчиков Python",
        "expected": ["find_partners", "Python"]
    },
    {
        "name": "Добавление задачи с временем",
        "message": "Напомни через 2 часа позвонить клиенту",
        "expected": ["add_task", "позвонить"]
    },
    {
        "name": "Обновление профиля",
        "message": "Я теперь работаю в Google на должности Senior Engineer",
        "expected": ["update_profile", "Google", "Senior"]
    },
    {
        "name": "Смена города",
        "message": "Я переехал в Москву",
        "expected": ["update_profile", "Москва", "Europe/Moscow"]
    },
    {
        "name": "Проверка делегирования",
        "message": "Как там с задачей что я поручил?",
        "expected": ["делегир", "статус"]
    }
]

async def test_ai_dialogue():
    """Тестирует AI диалог с реальной базой данных"""
    print("\n" + "="*80)
    print("ТЕСТ AI ДИАЛОГА")
    print("="*80 + "\n")
    
    session = SessionLocal()
    
    try:
        # Находим или создаём тестового пользователя
        user = session.query(User).filter_by(telegram_id=146333757).first()
        
        if not user:
            print("[INFO] Пользователь не найден, создаём...")
            user = User(
                telegram_id=146333757,
                username="test_user",
                timezone="Europe/Moscow"
            )
            session.add(user)
            session.commit()
            print("[OK] Пользователь создан")
        
        print(f"[OK] Пользователь найден: {user.username} (ID: {user.telegram_id})")
        print(f"   Timezone: {user.timezone or 'UTC'}")
        print()
        
        # Запускаем тесты
        for i, scenario in enumerate(TEST_SCENARIOS, 1):
            print(f"\n{'-'*80}")
            print(f"[СЦЕНАРИЙ {i}/{len(TEST_SCENARIOS)}] {scenario['name']}")
            print(f"{'-'*80}")
            print(f"[Пользователь]: {scenario['message']}")
            
            try:
                # Вызываем AI
                response = await chat_with_ai(
                    message=scenario['message'],
                    user_id=user.telegram_id
                )
                
                print(f"\n[AI ответ]:\n{response}\n")
                
                # Проверка ожиданий
                response_lower = response.lower()
                
                found_expected = []
                missing_expected = []
                
                for expected in scenario['expected']:
                    expected_lower = expected.lower()
                    
                    # Проверяем в ответе
                    if expected_lower in response_lower:
                        found_expected.append(expected)
                    else:
                        missing_expected.append(expected)
                
                if missing_expected:
                    print(f"\n[!] Не найдено ожидаемое: {', '.join(missing_expected)}")
                else:
                    print(f"\n[OK] Все ожидания выполнены!")
                
                # Задержка между запросами
                await asyncio.sleep(2)
                
            except Exception as e:
                print(f"\n[ERROR] Ошибка: {e}")
                import traceback
                traceback.print_exc()
        
        print(f"\n{'='*80}")
        print("ТЕСТИРОВАНИЕ ЗАВЕРШЕНО")
        print(f"{'='*80}\n")
        
    finally:
        session.close()

if __name__ == "__main__":
    asyncio.run(test_ai_dialogue())
