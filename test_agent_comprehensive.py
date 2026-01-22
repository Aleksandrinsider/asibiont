import asyncio
import logging
from datetime import datetime, timedelta
import pytz
from models import Session, User, Task
from ai_integration import (
    chat_with_ai, generate_reminder, generate_result_check,
    generate_proactive_message, generate_daily_report, generate_overdue_reminder
)
from config import LOCAL

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_agent_comprehensive():
    """Комплексное тестирование агента на различные типы сообщений"""

    # Получить тестового пользователя
    db = Session()
    user = db.query(User).filter_by(username='testuser').first()
    if not user:
        logger.error("Test user not found")
        return
    user_id = user.telegram_id
    db.close()

    user_now = datetime.now(pytz.UTC)
    current_time_str = user_now.strftime("%H:%M")
    current_date_str = user_now.strftime("%Y-%m-%d")

    test_cases = [
        # Обычные запросы
        {
            "type": "create_task",
            "message": "Создай задачу: купить продукты завтра в 10:00",
            "expected": "Задача создана"
        },
        {
            "type": "complete_task",
            "message": "Заверши задачу о продуктах",
            "expected": "Задача выполнена"
        },
        {
            "type": "list_tasks",
            "message": "Покажи мои задачи",
            "expected": "Список задач"
        },
        {
            "type": "advice",
            "message": "Как лучше организовать день?",
            "expected": "Совет"
        },
        # Проактивные сообщения
        {
            "type": "proactive",
            "function": "generate_proactive_message",
            "args": [user_id],
            "expected": "Короткое предложение"
        },
        # Напоминания
        {
            "type": "reminder",
            "function": "generate_reminder",
            "args": [user_id, "Тестовая задача", 1],
            "expected": "Напоминание с вопросом"
        },
        # Результат проверки
        # {
        #     "type": "result_check",
        #     "function": "generate_result_check",
        #     "args": [user_id, "Тестовая задача", 1],
        #     "expected": "Проверка результата"
        # },
        # Дневной отчёт
        {
            "type": "daily_report",
            "function": "generate_daily_report",
            "args": [user_id],
            "expected": "Отчёт с вопросом"
        },
        # Просроченные задачи
        {
            "type": "overdue",
            "function": "generate_overdue_reminder",
            "args": [user_id, [{"title": "Просроченная задача"}], 1],
            "expected": "Напоминание о просроченных"
        }
    ]

    results = []

    for i, test_case in enumerate(test_cases):
        logger.info(f"Testing case {i+1}: {test_case['type']}")

        try:
            if "function" in test_case:
                # Вызов функции напрямую
                func_name = test_case["function"]
                args = test_case["args"]
                if func_name == "generate_proactive_message":
                    response = await generate_proactive_message(*args)
                elif func_name == "generate_reminder":
                    response = await generate_reminder(*args)
                elif func_name == "generate_result_check":
                    response = await generate_result_check(*args)
                elif func_name == "generate_daily_report":
                    response = await generate_daily_report(*args)
                elif func_name == "generate_overdue_reminder":
                    response = await generate_overdue_reminder(*args)
                else:
                    response = "Function not implemented"
            else:
                # Обычный чат
                response = await chat_with_ai(
                    message=test_case["message"],
                    user_id=user_id
                )

            # Проверка соответствия промту
            compliant = check_response_compliance(response, test_case["type"])
            results.append({
                "case": test_case["type"],
                "response": response,
                "compliant": compliant,
                "length": len(response.split())
            })

            logger.info(f"Response: {response[:100]}...")
            logger.info(f"Compliant: {compliant}")

        except Exception as e:
            logger.error(f"Error in test case {test_case['type']}: {e}")
            results.append({
                "case": test_case["type"],
                "error": str(e)
            })

    # Вывод результатов
    print("\n=== ТЕСТИРОВАНИЕ АГЕНТА ===")
    for result in results:
        if "error" in result:
            print(f"❌ {result['case']}: Ошибка - {result['error']}")
        else:
            status = "✅" if result["compliant"] else "⚠️"
            print(f"{status} {result['case']}: {result['length']} слов, {'соответствует' if result['compliant'] else 'не соответствует'} промту")
            print(f"   Ответ: {result['response'][:150]}...")

def check_response_compliance(response, msg_type):
    """Проверка соответствия ответа промту"""
    response_lower = response.lower()

    # Общие правила
    if len(response.split()) > 100:  # Слишком длинный
        return False
    if any(word in response_lower for word in ["здравствуйте", "спасибо за вопрос", "я помогу"]):  # Клише
        return False

    # Специфические по типу
    if msg_type in ["reminder", "proactive", "overdue"]:
        if "?" not in response:  # Должен быть вопрос
            return False
        if len(response.split()) > 30:  # Слишком длинный
            return False

    if msg_type == "create_task":
        if "завтра в" not in response_lower and "время" not in response_lower:
            return False

    if msg_type == "complete_task":
        if "выполнена" not in response_lower and "завершена" not in response_lower:
            return False

    return True

if __name__ == "__main__":
    if LOCAL:
        asyncio.run(test_agent_comprehensive())
    else:
        print("Тест только для локального режима")