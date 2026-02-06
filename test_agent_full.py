"""
Полное тестирование агента - все типы запросов
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import asyncio
import logging
from models import Session, User
from ai_integration.autonomous_agent import get_autonomous_agent

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# Все тестовые кейсы по категориям
TEST_CATEGORIES = {
    "Базовое общение": [
        ("привет", "general_chat"),
        ("как дела?", "general_chat"),
        ("спасибо", "general_chat"),
        ("расскажи о себе", "general_chat"),
    ],
    
    "Создание задач": [
        ("создай задачу купить молоко завтра", "add_task"),
        ("напомни позвонить маме через 2 часа", "add_task"),
        ("добавь задачу сдать отчет в пятницу в 15:00", "add_task"),
        ("задача: сходить в спортзал сегодня вечером", "add_task"),
    ],
    
    "Список задач": [
        ("мои задачи", "list_tasks"),
        ("покажи задачи", "list_tasks"),
        ("что у меня на сегодня", "list_tasks"),
    ],
    
    "Выполнение задач": [
        ("готово", "complete_task"),
        ("задача выполнена", "complete_task"),
        ("я сделал купить молоко", "complete_task"),
    ],
    
    "Удаление задач": [
        ("удали задачу купить молоко", "delete_task"),
        ("сотри эту задачу", "delete_task"),
    ],
    
    "Перенос задач": [
        ("перенеси на завтра", "reschedule_task"),
        ("отложи задачу на вечер", "reschedule_task"),
    ],
    
    "Анализ": [
        ("что делать сейчас", "analyze_tasks"),
        ("проанализируй мои дела", "analyze_tasks"),
    ],
    
    "Партнеры и контакты": [
        ("найди единомышленников", "find_partners"),
        ("кто может помочь с проектом", "find_relevant_contacts_for_task"),
    ],
    
    "Профиль и память": [
        ("обновь мой профиль", "update_profile"),
        ("запомни что я люблю программирование", "update_user_memory"),
    ],
    
    "Подтверждения": [
        ("да", "confirmation"),
        ("давай", "confirmation"),
        ("отлично", "confirmation"),
    ],
    
    "Краевые случаи": [
        ("", "empty"),
        ("   ", "whitespace"),
        ("123", "numbers"),
        ("!@#$", "special"),
    ],
}


async def full_test():
    """Полное тестирование всех категорий"""
    
    # Открываем файл для записи результатов
    log_file = open("test_results_full.txt", "w", encoding="utf-8")
    
    def log(msg):
        try:
            print(msg)
        except UnicodeEncodeError:
            # Windows console может не поддерживать emoji
            print(msg.encode('ascii', 'replace').decode('ascii'))
        log_file.write(msg + "\n")
        log_file.flush()
    
    # Получаем тестового пользователя
    session = Session()
    try:
        test_user = session.query(User).filter_by(telegram_id=123456789).first()
        if not test_user:
            test_user = User(
                telegram_id=123456789,
                username="test_user",
                timezone="Europe/Moscow"
            )
            session.add(test_user)
            session.commit()
        user_id = test_user.telegram_id
    finally:
        session.close()
    
    agent = get_autonomous_agent()
    
    total_passed = 0
    total_failed = 0
    total_tests = 0
    category_results = {}
    all_errors = []
    
    log("\n" + "="*80)
    log("ПОЛНОЕ ТЕСТИРОВАНИЕ АГЕНТА")
    log("="*80 + "\n")
    
    # Тестируем по категориям
    for category, tests in TEST_CATEGORIES.items():
        log(f"\n{'='*80}")
        log(f"КАТЕГОРИЯ: {category}")
        log(f"{'='*80}")
        
        passed = 0
        failed = 0
        errors = []
        
        for i, (message, expected_intent) in enumerate(tests, 1):
            total_tests += 1
            log(f"\n{i}. Тест: '{message}'")
            log(f"   Ожидается: {expected_intent}")
            
            try:
                # Планирование
                plan = await agent.plan_strategy(message, user_id, None)
                actual_intent = plan.get('intent', 'unknown')
                actions = plan.get('actions', [])
                
                # Проверка
                success = True
                error_detail = None
                
                # Краевые случаи - должны обрабатываться без ошибок
                if expected_intent in ["empty", "whitespace", "numbers", "special"]:
                    success = True
                    log(f"   ✅ Обработано без ошибок")
                # Подтверждения
                elif expected_intent == "confirmation":
                    success = True
                    log(f"   ✅ Подтверждение обработано: intent={actual_intent}")
                # Общение
                elif expected_intent == "general_chat":
                    if actual_intent != "general_chat":
                        success = False
                        error_detail = f"Ожидался {expected_intent}, получен {actual_intent}"
                        log(f"   ❌ {error_detail}")
                    else:
                        log(f"   ✅ Корректное общение")
                # Команды
                else:
                    if actual_intent == "general_chat" and len(actions) == 0:
                        success = False
                        error_detail = f"Команда не распознана"
                        log(f"   ❌ {error_detail}")
                    elif actions:
                        tools_used = [a.get('tool') for a in actions]
                        log(f"   🔧 Инструменты: {', '.join(tools_used)}")
                        
                        # Выполняем действия
                        execution_results = await agent.execute_actions(actions, user_id)
                        
                        all_success = all(r.get('success', False) for r in execution_results)
                        if all_success:
                            log(f"   ✅ Выполнено успешно")
                        else:
                            success = False
                            failed_tools = [f"{r.get('tool')}: {r.get('error')}" for r in execution_results if not r.get('success')]
                            error_detail = f"Ошибка выполнения - {', '.join(failed_tools)}"
                            log(f"   ❌ {error_detail}")
                
                if success:
                    passed += 1
                    total_passed += 1
                else:
                    failed += 1
                    total_failed += 1
                    errors.append(f"'{message}': {error_detail}")
                    all_errors.append(f"[{category}] '{message}': {error_detail}")
                    
            except Exception as e:
                log(f"   ❌ ИСКЛЮЧЕНИЕ: {e}")
                failed += 1
                total_failed += 1
                errors.append(f"'{message}': исключение - {str(e)}")
                all_errors.append(f"[{category}] '{message}': исключение - {str(e)}")
            
            await asyncio.sleep(0.1)
        
        # Итог по категории
        log(f"\n{'─'*80}")
        log(f"Итог категории '{category}':")
        log(f"✅ Успешно: {passed}/{len(tests)}")
        log(f"❌ Ошибок: {failed}/{len(tests)}")
        
        category_results[category] = {
            'passed': passed,
            'failed': failed,
            'total': len(tests),
            'errors': errors
        }
    
    # Общий итоговый отчет
    log(f"\n{'='*80}")
    log("ОБЩИЙ ИТОГОВЫЙ ОТЧЕТ")
    log(f"{'='*80}")
    log(f"Всего тестов: {total_tests}")
    log(f"✅ Успешно: {total_passed} ({total_passed/total_tests*100:.1f}%)")
    log(f"❌ Ошибок: {total_failed} ({total_failed/total_tests*100:.1f}%)")
    log(f"{'='*80}\n")
    
    # Детализация по категориям
    log("ДЕТАЛИЗАЦИЯ ПО КАТЕГОРИЯМ:")
    log(f"{'─'*80}")
    for category, results in category_results.items():
        success_rate = results['passed'] / results['total'] * 100
        status = "✅" if results['failed'] == 0 else "⚠️" if success_rate >= 80 else "❌"
        log(f"{status} {category}: {results['passed']}/{results['total']} ({success_rate:.1f}%)")
    
    # Все ошибки
    if all_errors:
        log(f"\n{'='*80}")
        log(f"ВСЕ ОШИБКИ ({len(all_errors)}):")
        log(f"{'='*80}")
        for i, error in enumerate(all_errors, 1):
            log(f"{i}. {error}")
    
    log_file.close()
    
    if total_failed == 0:
        print(f"\n{'='*80}")
        print("✅ ВСЕ ТЕСТЫ ПРОЙДЕНЫ НА 100%!")
        print(f"{'='*80}\n")
        return 0
    else:
        print(f"\n{'='*80}")
        print(f"⚠️ Успешность: {total_passed/total_tests*100:.1f}%")
        print(f"{'='*80}\n")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(full_test())
    sys.exit(exit_code)
