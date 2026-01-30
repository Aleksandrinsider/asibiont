import os
os.environ["FREE_ACCESS_MODE"] = "1"

import asyncio
import logging
import sys
from datetime import datetime
from ai_integration.chat import chat_with_ai
from models import Session, User, Task, UserProfile, init_db, SubscriptionTier
from ai_integration.handlers import delete_all_tasks
import io
from contextlib import redirect_stdout, redirect_stderr

# Настройка кодировки для Windows
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

# Настройка логирования
logging.basicConfig(
    level=logging.WARNING,  # Только WARNING и выше
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Тестовые кейсы для всех функций
TEST_CASES = {
    "add_task": [
        "Напомни позвонить клиенту завтра в 10",
        "Создай задачу купить молоко через час",
        "Добавь задачу проверить почту через 15 минут",
        "Нужно сделать презентацию к понедельнику",
        "Поставь напоминание встреча с командой послезавтра в 14:00",
    ],
    "complete_task": [
        "Готово",
        "Сделал презентацию",
        "Выполнил задачу про почту",
        "Закончил встречу",
        "Уже проверил почту",
    ],
    "list_tasks": [
        "Покажи мои задачи",
        "Что у меня запланировано",
        "Список задач",
        "Мои дела",
        "Что нужно сделать",
    ],
    "delete_task": [
        "Удали задачу про молоко",
        "Убери встречу",
        "Отмени напоминание про звонок",
        "Удали последнюю задачу",
    ],
    "reschedule_task": [
        "Перенеси на завтра",
        "Перенеси встречу на 5 минут",
        "Отложи задачу на час",
        "Подвинь презентацию на понедельник",
        "Измени время звонка на 16:00",
        "Перенеси просроченную задачу на завтра",
    ],
    "update_profile": [
        "Я из Москвы",
        "Работаю программистом в Яндексе",
        "Люблю программирование и спорт",
        "Мои навыки: Python, JavaScript",
        "Хочу развивать бизнес",
    ],
    "find_partners": [
        "Найди партнеров",
        "Ищу единомышленников",
        "Найди людей с похожими интересами",
        "Подбери коллег для проекта",
    ],
    "set_recurring_task": [
        "Напоминай делать зарядку каждый день в 7 утра",
        "Каждую пятницу проверять отчеты в 16:00",
        "Повтори задачу звонок каждый понедельник",
    ],
    "delete_all_tasks": [
        "Удали все задачи",
        "Очисти все напоминания",
        "Убери все дела",
    ],
    "conversation": [
        "Привет",
        "Как дела?",
        "Спасибо за помощь",
        "Что ты умеешь?",
    ],
}

async def test_all_functions():
    """Комплексное тестирование всех функций бота"""
    
    # Инициализация БД
    init_db()
    
    # Создаем тестового пользователя
    db_session = Session()
    
    try:
        user = db_session.query(User).filter_by(telegram_id=999999).first()
        if not user:
            user = User(
                telegram_id=999999,
                username="test_all_functions",
                subscription_tier=SubscriptionTier.LIGHT
            )
            db_session.add(user)
            db_session.commit()
        
        test_user_id = user.id
        
        # Создаем профиль
        profile = db_session.query(UserProfile).filter_by(user_id=test_user_id).first()
        if not profile:
            profile = UserProfile(
                user_id=test_user_id,
                city="Москва",
                interests="тестирование"
            )
            db_session.add(profile)
            db_session.commit()
        
        print("=" * 100)
        print("🧪 КОМПЛЕКСНОЕ ТЕСТИРОВАНИЕ ВСЕХ ФУНКЦИЙ БОТА")
        print("=" * 100)
        print()
        
        all_results = {}
        total_tests = sum(len(cases) for cases in TEST_CASES.values())
        current_test = 0
        
        for function_name, test_cases in TEST_CASES.items():
            print(f"\n{'═' * 100}")
            print(f"📦 ТЕСТИРОВАНИЕ ФУНКЦИИ: {function_name.upper()}")
            print(f"{'═' * 100}\n")
            
            function_results = []
            
            for i, test_case in enumerate(test_cases, 1):
                current_test += 1
                print(f"[{current_test}/{total_tests}] 🧪 Тест: '{test_case}'")
                
                # Перехватываем логи для анализа
                log_capture = io.StringIO()
                handler = logging.StreamHandler(log_capture)
                handler.setLevel(logging.INFO)
                ai_logger = logging.getLogger('ai_integration.chat')
                ai_logger.addHandler(handler)
                
                try:
                    # Выполняем запрос
                    response = await chat_with_ai(
                        message=test_case,
                        user_id=test_user_id,
                        db_session=db_session
                    )
                    
                    # Анализируем логи
                    logs = log_capture.getvalue()
                    
                    # DEBUG: выводим response для отладки
                    print(f"   [DEBUG] Response type: {type(response)}")
                    print(f"   [DEBUG] Response keys: {response.keys() if isinstance(response, dict) else 'not dict'}")
                    if isinstance(response, dict) and 'tool_calls' in response:
                        print(f"   [DEBUG] Tool calls: {response['tool_calls']}")
                    
                    # Определяем вызванную функцию из tool_calls в ответе
                    called_function = None
                    if response and isinstance(response, dict):
                        tool_calls = response.get('tool_calls', [])
                        if tool_calls and len(tool_calls) > 0:
                            # Берем первый вызов функции
                            first_call = tool_calls[0]
                            if isinstance(first_call, dict):
                                called_function = first_call.get('function')
                            elif hasattr(first_call, 'function'):
                                called_function = first_call.function.name if hasattr(first_call.function, 'name') else first_call.function
                        
                        # Если tool_calls пустой, проверяем логи на наличие [TOOL CHOICE]
                        if not called_function:
                            for line in logs.split('\n'):
                                if '[TOOL CHOICE]' in line:
                                    # Формат: [TOOL CHOICE] add_task (confidence: 0.9)
                                    parts = line.split('[TOOL CHOICE]')[1].split('(')[0].strip()
                                    called_function = parts
                                    break
                    
                    # Если ничего не найдено - значит conversation
                    if not called_function:
                        called_function = "conversation"
                    
                    # Проверяем на ошибки
                    has_error = "ERROR" in logs or "Error" in logs or "error" in logs.lower()
                    
                    # Определяем результат
                    if called_function == function_name:
                        result = "✅ PASS"
                        success = True
                    elif function_name == "conversation" and called_function in ["conversation", None]:
                        result = "✅ PASS"
                        success = True
                    else:
                        result = f"❌ FAIL - вызвана {called_function} вместо {function_name}"
                        success = False
                    
                    if has_error:
                        result += " ⚠️ (с ошибками в логах)"
                        success = False
                    
                    print(f"   {result}")
                    print(f"   📝 Ответ: {str(response)[:150]}...")
                    
                    function_results.append({
                        "test_case": test_case,
                        "expected": function_name,
                        "actual": called_function,
                        "success": success,
                        "has_error": has_error,
                        "response": str(response)[:200]
                    })
                    
                except Exception as e:
                    print(f"   ❌ EXCEPTION: {e}")
                    function_results.append({
                        "test_case": test_case,
                        "expected": function_name,
                        "actual": "exception",
                        "success": False,
                        "has_error": True,
                        "response": str(e)
                    })
                
                finally:
                    ai_logger.removeHandler(handler)
                
                # Небольшая пауза между тестами
                await asyncio.sleep(0.5)
            
            all_results[function_name] = function_results
        
        # Итоговая статистика
        print("\n" + "=" * 100)
        print("📊 ИТОГОВАЯ СТАТИСТИКА")
        print("=" * 100)
        
        total_success = 0
        total_fail = 0
        total_errors = 0
        
        for function_name, results in all_results.items():
            success_count = sum(1 for r in results if r["success"])
            fail_count = len(results) - success_count
            error_count = sum(1 for r in results if r["has_error"])
            
            total_success += success_count
            total_fail += fail_count
            total_errors += error_count
            
            status = "✅" if fail_count == 0 else "❌"
            print(f"\n{status} {function_name}: {success_count}/{len(results)} успешно")
            
            if fail_count > 0:
                print(f"   Провальные тесты:")
                for r in results:
                    if not r["success"]:
                        print(f"   - '{r['test_case']}'")
                        print(f"     Ожидалось: {r['expected']}, Получено: {r['actual']}")
        
        print(f"\n{'─' * 100}")
        print(f"📈 ОБЩИЙ ИТОГ:")
        print(f"   ✅ Успешных: {total_success}/{total_tests} ({total_success/total_tests*100:.1f}%)")
        print(f"   ❌ Провальных: {total_fail}/{total_tests} ({total_fail/total_tests*100:.1f}%)")
        if total_errors > 0:
            print(f"   ⚠️  С ошибками: {total_errors}")
        
        # Рекомендации
        print("\n" + "=" * 100)
        print("💡 РЕКОМЕНДАЦИИ ПО УЛУЧШЕНИЮ")
        print("=" * 100)
        
        problem_functions = []
        for function_name, results in all_results.items():
            fail_count = sum(1 for r in results if not r["success"])
            if fail_count > 0:
                problem_functions.append((function_name, fail_count, len(results)))
        
        if problem_functions:
            print("\n🔧 Требуют улучшения промптов:")
            for func_name, fails, total in sorted(problem_functions, key=lambda x: x[1], reverse=True):
                print(f"   • {func_name}: {fails}/{total} провальных тестов ({fails/total*100:.1f}%)")
        else:
            print("\n✅ Все функции работают отлично! Промпты не требуют улучшения.")
        
        # Детальный отчет по ошибкам
        if total_fail > 0:
            print("\n" + "=" * 100)
            print("📋 ДЕТАЛЬНЫЙ ОТЧЕТ ПО ОШИБКАМ")
            print("=" * 100)
            
            for function_name, results in all_results.items():
                failed = [r for r in results if not r["success"]]
                if failed:
                    print(f"\n❌ {function_name}:")
                    for r in failed:
                        print(f"   Тест: '{r['test_case']}'")
                        print(f"   Ожидалось: {r['expected']}")
                        print(f"   Получено: {r['actual']}")
                        if r["has_error"]:
                            print(f"   ⚠️ Присутствуют ошибки в логах")
                        print(f"   Ответ: {r['response'][:150]}...")
                        print()
        
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        db_session.close()

if __name__ == "__main__":
    asyncio.run(test_all_functions())
