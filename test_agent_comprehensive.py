"""
Комплексное тестирование агента для поиска ошибок
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import asyncio
import logging
from datetime import datetime
from models import Session, User
from ai_integration.autonomous_agent import get_autonomous_agent

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Тестовые кейсы
TEST_CASES = [
    # Базовые команды
    ("привет", "general_chat", "Приветствие"),
    ("как дела?", "general_chat", "Общий вопрос"),
    
    # Создание задач
    ("создай задачу купить молоко завтра в 10:00", "add_task", "Создание задачи с временем"),
    ("напомни позвонить маме через 2 часа", "add_task", "Напоминание с относительным временем"),
    ("добавь задачу сдать отчет в пятницу", "add_task", "Задача на конкретный день"),
    
    # Список задач
    ("мои задачи", "list_tasks", "Запрос списка задач"),
    ("что у меня на сегодня", "list_tasks", "Задачи на сегодня"),
    ("покажи все дела", "list_tasks", "Все задачи"),
    
    # Выполнение задач
    ("задача готова", "complete_task", "Выполнение без названия"),
    ("я сделал купить молоко", "complete_task", "Выполнение с названием"),
    ("готово позвонить маме", "complete_task", "Выполнение с названием 2"),
    
    # Перенос задач
    ("перенеси задачу на завтра", "reschedule_task", "Перенос без названия"),
    ("отложи купить молоко на вечер", "reschedule_task", "Перенос с названием"),
    
    # Удаление задач
    ("удали задачу купить молоко", "delete_task", "Удаление с названием"),
    ("сотри эту задачу", "delete_task", "Удаление без названия"),
    
    # Анализ
    ("что делать сейчас", "analyze_tasks", "Анализ задач"),
    ("проанализируй мои дела", "analyze_tasks", "Анализ задач 2"),
    
    # Поиск партнеров
    ("найди единомышленников", "find_partners", "Поиск партнеров"),
    ("кто может помочь с проектом", "find_relevant_contacts_for_task", "Поиск контактов"),
    
    # Подтверждения
    ("да", "confirmation", "Подтверждение"),
    ("давай", "confirmation", "Подтверждение 2"),
    ("согласен", "confirmation", "Подтверждение 3"),
    
    # Краевые случаи
    ("", "empty", "Пустое сообщение"),
    ("   ", "whitespace", "Только пробелы"),
    ("123456", "numbers", "Только цифры"),
    ("!@#$%^", "special", "Спецсимволы"),
]


class AgentTester:
    """Класс для тестирования агента"""
    
    def __init__(self, user_id):
        self.user_id = user_id
        self.agent = get_autonomous_agent()
        self.results = []
        self.errors = []
        
    async def test_case(self, message, expected_intent, description):
        """Тестирует один кейс"""
        logger.info(f"\n{'='*80}")
        logger.info(f"ТЕСТ: {description}")
        logger.info(f"Сообщение: '{message}'")
        logger.info(f"Ожидаемый intent: {expected_intent}")
        
        try:
            # Планирование
            plan = await self.agent.plan_strategy(message, self.user_id, context=None)
            
            actual_intent = plan.get('intent', 'unknown')
            actions = plan.get('actions', [])
            response_strategy = plan.get('response_strategy', 'unknown')
            
            logger.info(f"✅ Планирование успешно")
            logger.info(f"   Intent: {actual_intent}")
            logger.info(f"   Actions: {len(actions)}")
            logger.info(f"   Strategy: {response_strategy}")
            
            if actions:
                for action in actions:
                    logger.info(f"   - {action.get('tool')} с параметрами: {action.get('params')}")
            
            # Проверка соответствия
            test_passed = True
            error_msg = None
            
            if expected_intent == "general_chat" and actual_intent != "general_chat":
                test_passed = False
                error_msg = f"Неверный intent. Ожидалось: {expected_intent}, получено: {actual_intent}"
            elif expected_intent != "general_chat" and actual_intent == "general_chat":
                test_passed = False
                error_msg = f"Команда не распознана. Ожидалось: {expected_intent}, получено: {actual_intent}"
            
            # Выполнение действий (если есть)
            execution_result = None
            if actions:
                try:
                    execution_result = await self.agent.execute_actions(actions, self.user_id)
                    logger.info(f"✅ Выполнение успешно: {len(execution_result)} результатов")
                    
                    for result in execution_result:
                        if result.get('success'):
                            logger.info(f"   ✅ {result.get('tool')}: {str(result.get('result'))[:100]}")
                        else:
                            logger.error(f"   ❌ {result.get('tool')}: {result.get('error')}")
                            test_passed = False
                            error_msg = f"Ошибка выполнения: {result.get('error')}"
                            
                except Exception as e:
                    logger.error(f"❌ Ошибка выполнения: {e}")
                    test_passed = False
                    error_msg = f"Исключение при выполнении: {str(e)}"
            
            # Формирование ответа
            try:
                if execution_result:
                    response = await self.agent.reflect_and_respond(
                        message, plan, execution_result, None, self.user_id
                    )
                else:
                    # Для общего чата может быть уже ответ в плане
                    response = plan.get('ai_response', 'OK')
                
                logger.info(f"✅ Ответ сформирован: {response[:100]}...")
                
            except Exception as e:
                logger.error(f"❌ Ошибка формирования ответа: {e}")
                test_passed = False
                error_msg = f"Исключение при формировании ответа: {str(e)}"
                response = None
            
            # Сохранение результата
            self.results.append({
                'description': description,
                'message': message,
                'expected_intent': expected_intent,
                'actual_intent': actual_intent,
                'actions': len(actions),
                'passed': test_passed,
                'error': error_msg,
                'response': response[:100] if response else None
            })
            
            if not test_passed:
                self.errors.append({
                    'description': description,
                    'message': message,
                    'error': error_msg
                })
            
            return test_passed
            
        except Exception as e:
            logger.error(f"❌ КРИТИЧЕСКАЯ ОШИБКА: {e}")
            import traceback
            traceback.print_exc()
            
            self.results.append({
                'description': description,
                'message': message,
                'expected_intent': expected_intent,
                'actual_intent': 'ERROR',
                'actions': 0,
                'passed': False,
                'error': f"Критическое исключение: {str(e)}",
                'response': None
            })
            
            self.errors.append({
                'description': description,
                'message': message,
                'error': f"Критическое исключение: {str(e)}"
            })
            
            return False
    
    async def run_all_tests(self):
        """Запускает все тесты"""
        logger.info(f"\n{'='*80}")
        logger.info(f"НАЧАЛО ТЕСТИРОВАНИЯ: {len(TEST_CASES)} тестов")
        logger.info(f"{'='*80}\n")
        
        start_time = datetime.now()
        
        passed = 0
        failed = 0
        
        for message, expected_intent, description in TEST_CASES:
            try:
                result = await self.test_case(message, expected_intent, description)
                if result:
                    passed += 1
                else:
                    failed += 1
                    
                # Небольшая пауза между тестами
                await asyncio.sleep(0.5)
                
            except Exception as e:
                logger.error(f"Ошибка при запуске теста '{description}': {e}")
                failed += 1
        
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        
        # Итоговый отчет
        logger.info(f"\n{'='*80}")
        logger.info(f"ИТОГОВЫЙ ОТЧЕТ")
        logger.info(f"{'='*80}")
        logger.info(f"Всего тестов: {len(TEST_CASES)}")
        logger.info(f"✅ Успешно: {passed} ({passed/len(TEST_CASES)*100:.1f}%)")
        logger.info(f"❌ Ошибок: {failed} ({failed/len(TEST_CASES)*100:.1f}%)")
        logger.info(f"⏱️ Время: {duration:.1f}s")
        logger.info(f"{'='*80}\n")
        
        # Детализация ошибок
        if self.errors:
            logger.error(f"\n{'='*80}")
            logger.error(f"ДЕТАЛИЗАЦИЯ ОШИБОК ({len(self.errors)}):")
            logger.error(f"{'='*80}")
            
            for i, error in enumerate(self.errors, 1):
                logger.error(f"\n{i}. {error['description']}")
                logger.error(f"   Сообщение: '{error['message']}'")
                logger.error(f"   Ошибка: {error['error']}")
        
        return {
            'total': len(TEST_CASES),
            'passed': passed,
            'failed': failed,
            'duration': duration,
            'success_rate': passed / len(TEST_CASES) * 100,
            'errors': self.errors
        }


async def main():
    """Главная функция"""
    
    # Получаем тестового пользователя
    session = Session()
    try:
        # Создаем или получаем тестового пользователя
        test_user = session.query(User).filter_by(telegram_id=123456789).first()
        if not test_user:
            test_user = User(
                telegram_id=123456789,
                username="test_user",
                timezone="Europe/Moscow"
            )
            session.add(test_user)
            session.commit()
            logger.info("Создан тестовый пользователь")
        else:
            logger.info("Используется существующий тестовый пользователь")
        
        user_id = test_user.telegram_id
        
    finally:
        session.close()
    
    # Создаем тестер и запускаем
    tester = AgentTester(user_id)
    report = await tester.run_all_tests()
    
    # Выводим финальную статистику
    print(f"\n{'='*80}")
    print(f"ФИНАЛЬНАЯ СТАТИСТИКА")
    print(f"{'='*80}")
    print(f"Успешность: {report['success_rate']:.1f}%")
    print(f"Пройдено: {report['passed']}/{report['total']}")
    print(f"Время: {report['duration']:.1f}s")
    print(f"{'='*80}\n")
    
    if report['success_rate'] < 100:
        print(f"⚠️ Агент не достиг 100% успешности!")
        print(f"Найдено {len(report['errors'])} ошибок")
        return 1
    else:
        print(f"✅ Агент работает на 100%!")
        return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
