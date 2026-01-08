"""
Тестирование делегирования задач с проверкой обязательного времени
"""
import asyncio
import logging
import os

os.environ['LOCAL'] = '1'
os.environ['TESTING'] = '1'

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TEST_USER_ID = 123999888


async def test_delegation_scenarios():
    """Тестирование различных сценариев делегирования"""
    from ai_integration import chat_with_ai
    
    logger.info("\n" + "="*60)
    logger.info("ТЕСТИРОВАНИЕ ДЕЛЕГИРОВАНИЯ ЗАДАЧ")
    logger.info("="*60 + "\n")
    
    test_cases = [
        {
            "name": "Делегирование без времени",
            "query": "делегируй @testuser проверить отчет",
            "expected_keywords": ["время", "дату", "дедлайн", "когда", "уточни"],
            "should_ask_for_time": True
        },
        {
            "name": "Делегирование с относительным временем",
            "query": "поручи @testuser сделать презентацию через 3 часа",
            "expected_keywords": [],
            "should_ask_for_time": False
        },
        {
            "name": "Делегирование с точным временем",
            "query": "делегируй @testuser проверить код 2026-01-10 15:00",
            "expected_keywords": [],
            "should_ask_for_time": False
        }
    ]
    
    results = []
    for i, test_case in enumerate(test_cases, 1):
        logger.info(f"\n{'='*60}")
        logger.info(f"ТЕСТ {i}: {test_case['name']}")
        logger.info(f"Запрос: {test_case['query']}")
        logger.info(f"{'='*60}")
        
        try:
            response = await chat_with_ai(test_case['query'], context=[], user_id=TEST_USER_ID)
            
            logger.info(f"\nОтвет:\n{response}\n")
            
            # Проверка: должен ли AI спросить время?
            if test_case['should_ask_for_time']:
                # Проверяем наличие вопроса о времени
                asks_for_time = any(keyword in response.lower() for keyword in test_case['expected_keywords'])
                
                if asks_for_time:
                    logger.info("✅ PASSED - AI правильно запросил уточнение времени")
                    results.append(True)
                else:
                    logger.warning("❌ FAILED - AI не запросил время, хотя должен был")
                    results.append(False)
            else:
                # Проверяем что задача создана (не содержит слов об ошибке времени)
                error_markers = ["требуется точная", "укажите точное время", "на какое время"]
                has_error = any(marker in response.lower() for marker in error_markers)
                
                if not has_error:
                    logger.info("✅ PASSED - Задача делегирована успешно")
                    results.append(True)
                else:
                    logger.warning("❌ FAILED - AI запросил время, хотя оно было указано")
                    results.append(False)
                    
        except Exception as e:
            logger.error(f"❌ ОШИБКА: {e}", exc_info=True)
            results.append(False)
        
        await asyncio.sleep(2)  # Пауза между запросами
    
    # Итоги
    logger.info(f"\n{'='*60}")
    logger.info("ИТОГИ ТЕСТИРОВАНИЯ ДЕЛЕГИРОВАНИЯ")
    logger.info(f"{'='*60}")
    
    total = len(results)
    passed = sum(results)
    failed = total - passed
    
    logger.info(f"Всего тестов: {total}")
    logger.info(f"✅ Успешно: {passed}")
    logger.info(f"❌ Провалено: {failed}")
    logger.info(f"Успешность: {passed/total*100:.1f}%\n")
    
    return passed == total


if __name__ == "__main__":
    success = asyncio.run(test_delegation_scenarios())
    exit(0 if success else 1)
