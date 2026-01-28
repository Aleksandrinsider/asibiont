import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import Session, User, init_db
from datetime import datetime, timezone
import pytz

async def test_ai_function(user_message, user_id=1):
    """Test AI function with a specific message"""
    try:
        # Initialize database if needed
        init_db()

        # Get or create test user
        session = Session()
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            user = User(
                telegram_id=user_id,
                username="test_user",
                timezone="Europe/Moscow",
                created_at=datetime.now(timezone.utc)
            )
            session.add(user)
            session.commit()

        # Test the AI function
        print(f"\n=== Testing: '{user_message}' ===")

        response = await chat_with_ai(
            message=user_message,
            user_id=user_id,
            db_session=session,
            context=None,
            message_type=None
        )

        print(f"Response: {response}")
        session.close()
        return response

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return None

async def run_tests():
    """Run comprehensive tests for all AI agent functions"""

    test_cases = [
        # add_task tests
        ("Напомни купить хлеб завтра в 9:00", "add_task"),
        ("Создай задачу подготовить отчет на завтра", "add_task"),
        ("Добавь напоминание позвонить клиенту через час", "add_task"),

        # complete_task tests
        ("Готово позвонить клиенту", "complete_task"),
        ("Сделал отчет", "complete_task"),
        ("Готово", "complete_task"),  # without task title

        # delete_task tests
        ("Удали задачу про встречу", "delete_task"),
        ("Убери напоминание о покупках", "delete_task"),

        # delete_all_tasks tests
        ("Удали все задачи", "delete_all_tasks"),
        ("Очисти все мои задачи", "delete_all_tasks"),

        # list_tasks tests
        ("Покажи мои задачи", "list_tasks"),
        ("Что у меня запланировано?", "list_tasks"),

        # reschedule_task tests
        ("Перенеси встречу на завтра в 16:00", "reschedule_task"),
        ("Измени время задачи про почту на 15:30", "reschedule_task"),

        # set_recurring_task tests
        ("Напоминай о зарядке каждый день в 8:00", "set_recurring_task"),
        ("Проверяй почту каждую неделю по понедельникам", "set_recurring_task"),

        # delegate_task tests
        ("Делегируй Ивану задачу проверить код", "delegate_task"),
        ("Поручи @maria задачу подготовить отчет завтра в 10:00", "delegate_task"),

        # update_profile tests
        ("Я из Москвы", "update_profile"),
        ("Работаю программистом в Google", "update_profile"),
        ("Люблю фотографию и музыку", "update_profile"),

        # find_partners tests
        ("Найди партнеров по разработке", "find_partners"),
        ("Поищи контакты для проекта по дизайну", "find_partners"),

        # get_task_details tests
        ("Покажи детали задачи про презентацию", "get_task_details"),
        ("Что в задаче о встрече?", "get_task_details"),

        # Edge cases
        ("Привет", "no_action"),
        ("Спасибо", "no_action"),
        ("Создай задачу", "add_task"),  # incomplete
        ("Готово позвонить", "complete_task"),
        ("Удали", "delete_task"),  # incomplete
        ("Перенеси на завтра", "reschedule_task"),  # incomplete
        ("Каждый день", "set_recurring_task"),  # incomplete
        ("Делегируй Ивану", "delegate_task"),  # incomplete
        ("Я из", "update_profile"),  # incomplete
        ("Найди", "find_partners"),  # incomplete
    ]

    results = []

    for message, expected_action in test_cases:
        try:
            response = await test_ai_function(message)
            results.append({
                'message': message,
                'expected': expected_action,
                'response': response,
                'success': response is not None
            })
            await asyncio.sleep(1)  # Small delay between tests
        except Exception as e:
            results.append({
                'message': message,
                'expected': expected_action,
                'error': str(e),
                'success': False
            })

    # Analyze results
    print("\n" + "="*80)
    print("TEST RESULTS SUMMARY")
    print("="*80)

    successful = [r for r in results if r['success']]
    failed = [r for r in results if not r['success']]

    print(f"Total tests: {len(results)}")
    print(f"Successful: {len(successful)}")
    print(f"Failed: {len(failed)}")

    if failed:
        print("\nFAILED TESTS:")
        for fail in failed:
            print(f"- '{fail['message']}' -> {fail.get('error', 'Unknown error')}")

    # Check for potential issues
    print("\nPOTENTIAL ISSUES FOUND:")

    # Check for messages that should trigger actions but might not
    action_triggers = {
        'add_task': ['напомни', 'создай', 'добавь', 'нужно', 'запланируй'],
        'complete_task': ['готово', 'сделал', 'выполнил', 'закончил', 'завершил'],
        'delete_task': ['удали', 'убери', 'сотри'],
        'delete_all_tasks': ['все задачи', 'очисти все', 'закрой все'],
        'list_tasks': ['покажи', 'список', 'что у меня'],
        'reschedule_task': ['перенеси', 'измени время', 'поставь на'],
        'set_recurring_task': ['каждый день', 'еженедельно', 'каждую неделю', 'повторять'],
        'delegate_task': ['делегируй', 'поручи', 'передай', 'отправь'],
        'update_profile': ['я из', 'работаю', 'люблю', 'хочу', 'интересует', 'увлекаюсь', 'занимаюсь'],
        'find_partners': ['найди партнеров', 'поищи контакты', 'кто может'],
        'get_task_details': ['детали', 'покажи задачу', 'что в задаче'],
    }

    issues = []
    for result in successful:
        message = result['message'].lower()
        found_action = False
        for action, triggers in action_triggers.items():
            if any(trigger in message for trigger in triggers):
                found_action = True
                break
        if not found_action and result['expected'] != 'no_action':
            issues.append(f"Message '{result['message']}' expected {result['expected']} but no trigger found")

    if issues:
        for issue in issues:
            print(f"- {issue}")
    else:
        print("No obvious trigger issues found")

    return results

if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Test single message
        user_message = " ".join(sys.argv[1:])
        asyncio.run(test_ai_function(user_message))
    else:
        # Run all tests
        asyncio.run(run_tests())