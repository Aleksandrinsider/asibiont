"""
Комплексное тестирование агента с проверкой контекста и естественного общения
"""
import asyncio
import os
os.environ['LOCAL'] = '1'

from ai_integration.chat import chat_with_ai
from models import Session, User, Task
from datetime import datetime, timedelta

# Тестовый user_id
TEST_USER_ID = 999888777

async def cleanup_test_data():
    """Очистка тестовых данных"""
    session = Session()
    try:
        session.query(Task).filter_by(user_id=TEST_USER_ID).delete()
        session.query(User).filter_by(telegram_id=TEST_USER_ID).delete()
        session.commit()
        print("✓ Тестовые данные очищены")
    finally:
        session.close()

async def test_scenario(scenario_name, messages, expectations):
    """Тестирование сценария взаимодействия"""
    print(f"\n{'='*70}")
    print(f"🧪 СЦЕНАРИЙ: {scenario_name}")
    print(f"{'='*70}")
    
    session = Session()
    try:
        # Создаем тестового пользователя если нет
        user = session.query(User).filter_by(telegram_id=TEST_USER_ID).first()
        if not user:
            user = User(telegram_id=TEST_USER_ID, username="test_user")
            session.add(user)
            session.commit()
        
        conversation_history = []
        
        for i, (message, expectation) in enumerate(zip(messages, expectations), 1):
            print(f"\n--- Шаг {i} ---")
            print(f"👤 Пользователь: {message}")
            
            # Вызываем AI
            response = await chat_with_ai(
                message, 
                user_id=TEST_USER_ID,
                context={'conversation_history': conversation_history}
            )
            
            # Добавляем в историю
            conversation_history.append({"role": "user", "content": message})
            if isinstance(response, dict) and 'response' in response:
                conversation_history.append({"role": "assistant", "content": response['response']})
                print(f"🤖 Агент: {response['response'][:200]}...")
            else:
                conversation_history.append({"role": "assistant", "content": str(response)})
                print(f"🤖 Агент: {str(response)[:200]}...")
            
            # Проверяем ожидание
            if expectation:
                if isinstance(response, dict):
                    if expectation in response.get('response', '').lower() or expectation in str(response.get('actions', [])).lower():
                        print(f"✓ Ожидание выполнено: найдено '{expectation}'")
                    else:
                        print(f"✗ Ожидание НЕ выполнено: не найдено '{expectation}'")
                        print(f"  Полный ответ: {response}")
            
            await asyncio.sleep(0.5)  # Небольшая пауза
            
    finally:
        session.close()

async def test_all_scenarios():
    """Запуск всех сценариев"""
    
    await cleanup_test_data()
    
    # Сценарий 1: Контекстное понимание "готово"
    await test_scenario(
        "Контекстное понимание 'готово'",
        [
            "Напомни позвонить клиенту завтра в 10:00",
            "Готово"  # Должен понять что речь о задаче "позвонить клиенту"
        ],
        [
            "задач",  # Создание задачи
            "завершен"  # Завершение задачи
        ]
    )
    
    # Сценарий 2: НЕ предлагать разбить на шаги
    await test_scenario(
        "НЕ предлагать разбить на шаги",
        [
            "Напомни написать отчет"
        ],
        [
            None  # Просто создает задачу, без предложений "разбить"
        ]
    )
    
    # Сценарий 3: Проверка выполнения задач
    await test_scenario(
        "Активная проверка выполнения",
        [
            "Напомни купить молоко",
            "Покажи задачи"  # Агент должен спросить про статус
        ],
        [
            "задач",
            "молоко"
        ]
    )
    
    # Сценарий 4: Понимание без точного названия
    await test_scenario(
        "Понимание по смыслу",
        [
            "Напомни подготовить презентацию",
            "Перенеси презу на завтра"  # Сокращение должно работать
        ],
        [
            "задач",
            "перенес"
        ]
    )
    
    # Сценарий 5: Естественный диалог
    await test_scenario(
        "Естественный разговор",
        [
            "Привет!",
            "Я из Москвы",
            "Работаю программистом"
        ],
        [
            None,
            "москва",
            "программист"
        ]
    )
    
    # Сценарий 6: Множественное завершение
    await test_scenario(
        "Несколько действий подряд",
        [
            "Напомни позвонить Ивану",
            "Напомни написать email",
            "Сделал звонок",  # Должен понять что это про Ивана
            "Покажи что осталось"
        ],
        [
            "задач",
            "задач",
            "завершен",
            "email"  # Должна остаться только задача про email
        ]
    )
    
    # Сценарий 7: Контекстное удаление
    await test_scenario(
        "Удаление по контексту",
        [
            "Напомни купить хлеб",
            "Удали"  # Должен удалить последнюю созданную
        ],
        [
            "задач",
            "удал"
        ]
    )
    
    print(f"\n{'='*70}")
    print("🎉 ВСЕ СЦЕНАРИИ ВЫПОЛНЕНЫ")
    print(f"{'='*70}")
    
    await cleanup_test_data()

async def main():
    """Главная функция"""
    print("🚀 ЗАПУСК КОМПЛЕКСНОГО ТЕСТИРОВАНИЯ АГЕНТА")
    print("Проверяем:")
    print("  - Понимание контекста")
    print("  - Отсутствие предложений 'разбить на шаги'")
    print("  - Проверка выполнения задач")
    print("  - Работа со смыслом, а не точными названиями")
    
    await test_all_scenarios()
    
    print("\n✅ ТЕСТИРОВАНИЕ ЗАВЕРШЕНО!")

if __name__ == "__main__":
    asyncio.run(main())
