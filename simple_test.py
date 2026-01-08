"""
Простое тестирование AI агента без запуска сервера
"""
import asyncio
import logging
import os
import sys

# Установить переменные окружения перед импортом
os.environ['LOCAL'] = '1'
os.environ['TESTING'] = '1'  # Флаг для тестирования

# Добавить текущую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Тестовый user_id
TEST_USER_ID = 123999888


async def test_single_query(query, test_name):
    """Тестирование одного запроса"""
    from ai_integration import chat_with_ai
    from datetime import datetime
    
    logger.info(f"\n{'='*60}")
    logger.info(f"ТЕСТ: {test_name}")
    logger.info(f"Запрос: {query}")
    logger.info(f"{'='*60}")
    
    start_time = datetime.now()
    try:
        response = await chat_with_ai(query, context=[], user_id=TEST_USER_ID)
        elapsed = (datetime.now() - start_time).total_seconds()
        
        logger.info(f"\nОтвет ({elapsed:.2f}s):")
        logger.info(f"{response}")
        
        # Проверка на ошибки
        error_markers = ['ошибка', 'error', 'exception', 'failed']
        has_error = any(marker in response.lower() for marker in error_markers)
        
        if has_error:
            logger.warning(f"⚠️  Ответ содержит признаки ошибки")
            return False
        
        if len(response) < 10:
            logger.warning(f"⚠️  Ответ слишком короткий")
            return False
        
        logger.info(f"✅ PASSED")
        return True
        
    except Exception as e:
        logger.error(f"❌ ОШИБКА: {e}", exc_info=True)
        return False


async def run_tests():
    """Запуск основных тестов"""
    logger.info(f"\n{'#'*60}")
    logger.info("ТЕСТИРОВАНИЕ AI АГЕНТА")
    logger.info(f"{'#'*60}\n")
    
    # Проверяем, есть ли пользователь в БД
    from models import Session, User, UserProfile
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=TEST_USER_ID).first()
        if not user:
            logger.warning(f"Пользователь {TEST_USER_ID} не найден, создаем тестового...")
            user = User(
                telegram_id=TEST_USER_ID,
                username="test_user",
                first_name="Test",
                timezone="Europe/Moscow"
            )
            session.add(user)
            session.commit()
            
            profile = UserProfile(
                user_id=user.id,
                position="Python Developer",
                city="Москва",
                interests="AI, ML",
                skills="Python, FastAPI"
            )
            session.add(profile)
            session.commit()
            logger.info("✅ Тестовый пользователь создан")
        else:
            logger.info(f"✅ Найден пользователь: {user.username}")
    finally:
        session.close()
    
    tests = [
        ("Приветствие", "Привет!"),
        ("Показать задачи", "покажи мои задачи"),
        ("Добавить задачу", "Добавь задачу: позвонить клиенту через 1 час"),
        ("Поиск контакта", "найди программиста Python"),
        ("Статистика", "покажи статистику"),
    ]
    
    results = []
    for test_name, query in tests:
        result = await test_single_query(query, test_name)
        results.append(result)
        await asyncio.sleep(2)  # Пауза между запросами
    
    # Итоги
    logger.info(f"\n{'#'*60}")
    logger.info("ИТОГИ ТЕСТИРОВАНИЯ")
    logger.info(f"{'#'*60}")
    
    total = len(results)
    passed = sum(1 for r in results if r)
    failed = total - passed
    
    logger.info(f"Всего: {total}")
    logger.info(f"✅ Успешно: {passed}")
    logger.info(f"❌ Провалено: {failed}")
    logger.info(f"Успешность: {passed/total*100:.1f}%")


if __name__ == "__main__":
    asyncio.run(run_tests())
