"""
Тест работы агента - проверка основных сценариев
"""
import asyncio
import sys
import os

# Добавляем текущую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration import chat_with_ai
from models import Base, engine, Session, User

# Создаём таблицы если их нет
Base.metadata.create_all(engine)

async def test_agent():
    """Тестируем основные сценарии работы агента"""
    
    # Тестовый user_id
    user_id = 999999
    
    # Создаём тестового пользователя
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(
            telegram_id=user_id,
            username="testuser",
            first_name="Test",
            timezone="Europe/Moscow"
        )
        session.add(user)
        session.commit()
    session.close()
    
    print("="*80)
    print("ТЕСТ РАБОТЫ АГЕНТА")
    print("="*80)
    
    test_scenarios = [
        {
            "name": "1. Приветствие",
            "message": "привет",
            "context": []
        },
        {
            "name": "2. Добавление задачи",
            "message": "напомни завтра в 10:00 купить молоко",
            "context": []
        },
        {
            "name": "3. Список задач",
            "message": "какие у меня задачи?",
            "context": []
        },
        {
            "name": "4. Обычный разговор",
            "message": "как дела?",
            "context": []
        },
        {
            "name": "5. Вопрос про задачу",
            "message": "что мне нужно сделать сегодня?",
            "context": []
        }
    ]
    
    context = []
    
    for scenario in test_scenarios:
        print(f"\n{'='*80}")
        print(f"[TEST] {scenario['name']}")
        print(f"[MSG] Сообщение: '{scenario['message']}'")
        print("-"*80)
        
        try:
            response = await chat_with_ai(
                message=scenario['message'],
                context=context,
                user_id=user_id
            )
            
            print(f"[BOT] Ответ: {response}")
            print(f"[INFO] Длина: {len(response)} символов")
            
            # Анализ ответа
            issues = []
            if len(response) > 500:
                issues.append(f"[X] Слишком длинный ({len(response)} символов)")
            if len(response) < 10:
                issues.append("[X] Слишком короткий")
            if response.count('\n') > 10:
                issues.append(f"[X] Много переносов строк ({response.count(chr(10))})")
            
            # Проверка на запрещенные элементы
            forbidden = []
            if "**" in response:
                forbidden.append("жирный текст")
            if any(emoji in response for emoji in ["🚀", "📝", "🎯", "⚠️", "💡", "📋"]):
                forbidden.append("технические эмодзи")
            
            if forbidden:
                issues.append(f"[X] Запрещенные элементы: {', '.join(forbidden)}")
            
            if issues:
                print("\n[!] Проблемы:")
                for issue in issues:
                    print(f"  {issue}")
            else:
                print("\n[OK] Ответ соответствует требованиям")
            
            # Обновляем контекст
            context.append({"user": scenario['message'], "agent": response})
            if len(context) > 5:
                context = context[-5:]
                
        except Exception as e:
            print(f"[ERROR] ОШИБКА: {e}")
            import traceback
            traceback.print_exc()
    
    print(f"\n{'='*80}")
    print("ИТОГИ ТЕСТИРОВАНИЯ")
    print("="*80)
    print(f"[OK] Протестировано сценариев: {len(test_scenarios)}")
    print("\n[INFO] Рекомендации:")
    print("1. Проверьте длину ответов - они должны быть краткими (1-2 предложения)")
    print("2. Убедитесь что нет запрещенных элементов (**, эмодзи, и т.д.)")
    print("3. Ответы должны быть естественными и разговорными")
    

if __name__ == "__main__":
    asyncio.run(test_agent())
