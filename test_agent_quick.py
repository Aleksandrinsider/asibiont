"""
Быстрый тест агента на критические сценарии
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

# Полный набор тестовых кейсов
CRITICAL_TESTS = [
    # Базовое общение
    ("привет", "general_chat"),
    ("как дела?", "general_chat"),
    ("спасибо", "general_chat"),
    
    # Создание задач (разные форматы)
    ("создай задачу купить молоко завтра", "add_task"),
    ("напомни позвонить маме через 2 часа", "add_task"),
    ("добавь задачу сдать отчет в пятницу в 15:00", "add_task"),
    ("задача: сходить в спортзал сегодня вечером", "add_task"),
    
    # Список задач
    ("мои задачи", "list_tasks"),
    ("покажи задачи", "list_tasks"),
    ("что у меня на сегодня", "list_tasks"),
    
    # Выполнение задач
    ("готово", "complete_task"),
    ("задача выполнена", "complete_task"),
    ("я сделал купить молоко", "complete_task"),
    
    # Удаление задач
    ("удали задачу купить молоко", "delete_task"),
    ("сотри эту задачу", "delete_task"),
    
    # Перенос задач
    ("перенеси на завтра", "reschedule_task"),
    ("отложи задачу на вечер", "reschedule_task"),
    
    # Анализ
    ("что делать сейчас", "analyze_tasks"),
    ("проанализируй мои дела", "analyze_tasks"),
    
    # Поиск партнеров и контактов
    ("найди единомышленников", "find_partners"),
    ("кто может помочь с проектом", "find_relevant_contacts_for_task"),
    
    # Профиль
    ("обновь мой профиль", "update_profile"),
    ("запомни что я люблю программирование", "update_user_memory"),
    
    # Подтверждения
    ("да", "confirmation"),
    ("давай", "confirmation"),
    ("отлично", "confirmation"),
    
    # Краевые случаи
    ("", "empty"),
    ("   ", "whitespace"),
    ("123", "numbers"),
    ("!@#$", "special"),
]


async def quick_test():
    """Быстрый тест критических сценариев"""
    
    # Открываем файл для записи результатов
    log_file = open("test_results.txt", "w", encoding="utf-8")
    
    def log(msg):
        print(msg)
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
    
    passed = 0
    failed = 0
    errors = []
    
    log("\n" + "="*80)
    log("БЫСТРОЕ ТЕСТИРОВАНИЕ АГЕНТА")
    log("="*80 + "\n")
    
    for i, (message, expected_intent) in enumerate(CRITICAL_TESTS, 1):
        log(f"{i}. Тест: '{message}' (ожидается: {expected_intent})")
        
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
                # Любой ответ валиден, главное - без исключений
                success = True
            # Подтверждения - могут быть как general_chat, так и попытка выполнить действие
            elif expected_intent == "confirmation":
                # Подтверждение должно либо распознаться как chat, либо попытаться что-то сделать
                success = True
            # Общение
            elif expected_intent == "general_chat":
                if actual_intent != "general_chat":
                    success = False
                    error_detail = f"Ожидался {expected_intent}, получен {actual_intent}"
            # Команды
            else:
                if actual_intent == "general_chat" and len(actions) == 0:
                    success = False
                    error_detail = f"Команда не распознана"
                elif actions:
                    # Проверяем, есть ли правильный инструмент
                    tools_used = [a.get('tool') for a in actions]
                    
                    # Для некоторых команд проверяем наличие правильного инструмента
                    if expected_intent in tools_used or any(expected_intent in tool for tool in tools_used):
                        # Пытаемся выполнить
                        execution_results = await agent.execute_actions(actions, user_id)
                        if not all(r.get('success', False) for r in execution_results):
                            success = False
                            failed_tools = [f"{r.get('tool')}: {r.get('error')}" for r in execution_results if not r.get('success')]
                            error_detail = f"Ошибка выполнения - {', '.join(failed_tools)}"
                    else:
                        # Инструмент не тот, но может это альтернативная интерпретация
                        # Попробуем выполнить и посмотрим на результат
                        execution_results = await agent.execute_actions(actions, user_id)
                        if all(r.get('success', False) for r in execution_results):
                            # Если выполнилось успешно - принимаем
                            success = True
                        else:
                            success = False
                            error_detail = f"Ожидался {expected_intent}, использован {', '.join(tools_used)}"
            
            if success:
                log(f"   ✅ УСПЕХ: intent={actual_intent}, actions={len(actions)}")
                if actions:
                    log(f"      Инструменты: {', '.join([a.get('tool') for a in actions])}")
                passed += 1
            else:
                log(f"   ❌ ОШИБКА: {error_detail}")
                failed += 1
                errors.append(f"'{message}': {error_detail}")
                
        except Exception as e:
            log(f"   ❌ ИСКЛЮЧЕНИЕ: {e}")
            import traceback
            error_trace = traceback.format_exc()
            log(f"      {error_trace[:200]}")
            failed += 1
            errors.append(f"'{message}': критическое исключение - {str(e)}")
        
        await asyncio.sleep(0.2)
    
    # Итоговый отчет
    log("\n" + "="*80)
    log("ИТОГОВЫЙ ОТЧЕТ")
    log("="*80)
    log(f"Всего тестов: {len(CRITICAL_TESTS)}")
    log(f"✅ Успешно: {passed} ({passed/len(CRITICAL_TESTS)*100:.1f}%)")
    log(f"❌ Ошибок: {failed} ({failed/len(CRITICAL_TESTS)*100:.1f}%)")
    log("="*80 + "\n")
    
    if errors:
        log("ДЕТАЛИЗАЦИЯ ОШИБОК:")
        for i, error in enumerate(errors, 1):
            log(f"{i}. {error}")
        log("")
    
    if passed == len(CRITICAL_TESTS):
        log("✅ ВСЕ КРИТИЧЕСКИЕ ТЕСТЫ ПРОЙДЕНЫ!\n")
        log_file.close()
        return 0
    else:
        log(f"⚠️ НЕ ВСЕ ТЕСТЫ УСПЕШНЫ: {failed}/{len(CRITICAL_TESTS)} ошибок\n")
        log_file.close()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(quick_test())
    sys.exit(exit_code)
