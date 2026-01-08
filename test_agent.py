"""
Тестирование AI агента на различные запросы (без запуска сервера)
"""
import asyncio
import logging
import os
import sys

# Установить LOCAL режим чтобы не запускать Telegram бота
os.environ['LOCAL'] = '1'

from ai_integration import chat_with_ai
from models import Session, User, Task, UserProfile
from datetime import datetime
import pytz

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Тестовый user_id (замените на реальный из БД)
TEST_USER_ID = 123999888

class AgentTester:
    def __init__(self, user_id):
        self.user_id = user_id
        self.test_results = []
        
    async def test_query(self, test_name, query, expected_keywords=None):
        """Тест одного запроса"""
        logger.info(f"\n{'='*60}")
        logger.info(f"ТЕСТ: {test_name}")
        logger.info(f"Запрос: {query}")
        logger.info(f"{'='*60}")
        
        try:
            start_time = datetime.now()
            response = await chat_with_ai(query, context=[], user_id=self.user_id)
            elapsed = (datetime.now() - start_time).total_seconds()
            
            logger.info(f"Ответ ({elapsed:.2f}s):\n{response}")
            
            # Проверка наличия ключевых слов
            success = True
            if expected_keywords:
                missing = []
                for keyword in expected_keywords:
                    if keyword.lower() not in response.lower():
                        missing.append(keyword)
                        success = False
                
                if missing:
                    logger.warning(f"⚠️  Отсутствуют ключевые слова: {', '.join(missing)}")
            
            # Проверка на ошибки
            error_markers = ['ошибка', 'error', 'exception', 'failed', 'не удалось']
            has_error = any(marker in response.lower() for marker in error_markers)
            
            if has_error:
                logger.warning(f"⚠️  Ответ содержит признаки ошибки")
                success = False
            
            # Проверка длины ответа
            if len(response) < 10:
                logger.warning(f"⚠️  Ответ слишком короткий: {len(response)} символов")
                success = False
            
            result = {
                'test_name': test_name,
                'query': query,
                'response': response,
                'elapsed': elapsed,
                'success': success,
                'has_error': has_error,
                'length': len(response)
            }
            
            self.test_results.append(result)
            
            status = "✅ PASSED" if success else "❌ FAILED"
            logger.info(f"Статус: {status}\n")
            
            return response
            
        except Exception as e:
            logger.error(f"❌ ОШИБКА при выполнении теста: {e}", exc_info=True)
            self.test_results.append({
                'test_name': test_name,
                'query': query,
                'response': None,
                'elapsed': 0,
                'success': False,
                'has_error': True,
                'error': str(e)
            })
            return None

    async def run_all_tests(self):
        """Запустить все тесты"""
        logger.info(f"\n{'#'*60}")
        logger.info(f"НАЧАЛО ТЕСТИРОВАНИЯ АГЕНТА")
        logger.info(f"User ID: {self.user_id}")
        logger.info(f"{'#'*60}\n")
        
        # 1. Приветствие
        await self.test_query(
            "Приветствие",
            "Привет! Как дела?",
            expected_keywords=[]
        )
        
        # 2. Показать задачи
        await self.test_query(
            "Показать задачи",
            "покажи мои задачи",
            expected_keywords=[]
        )
        
        # 3. Добавить задачу
        await self.test_query(
            "Добавить задачу",
            "Добавь задачу: проверить отчет о продажах через 2 часа",
            expected_keywords=[]
        )
        
        # 4. Найти контакт
        await self.test_query(
            "Поиск контакта",
            "найди программиста Python в Москве",
            expected_keywords=[]
        )
        
        # 5. Делегировать задачу
        await self.test_query(
            "Делегирование",
            "делегируй @testuser проверить документы через 3 часа",
            expected_keywords=[]
        )
        
        # 6. Обновить профиль
        await self.test_query(
            "Обновление профиля",
            "обнови мой профиль: я Senior Python разработчик из Санкт-Петербурга",
            expected_keywords=[]
        )
        
        # 7. Завершить задачу
        await self.test_query(
            "Завершить задачу",
            "выполнил задачу проверить отчет",
            expected_keywords=[]
        )
        
        # 8. Статистика
        await self.test_query(
            "Статистика",
            "покажи мою статистику",
            expected_keywords=[]
        )
        
        # 9. Граничный случай: пустой запрос
        await self.test_query(
            "Пустой запрос",
            "",
            expected_keywords=[]
        )
        
        # 10. Граничный случай: спецсимволы
        await self.test_query(
            "Спецсимволы",
            "!@#$%^&*()",
            expected_keywords=[]
        )
        
        # 11. Длинный запрос
        await self.test_query(
            "Очень длинный запрос",
            "Тест " * 100,
            expected_keywords=[]
        )
        
        # 12. Сложный запрос с множественными действиями
        await self.test_query(
            "Сложный запрос",
            "Добавь задачу 'позвонить клиенту' через 1 час, найди маркетолога в Москве и обнови мой профиль: интересы - спорт, музыка",
            expected_keywords=[]
        )
        
        # Печать итогов
        self.print_summary()
    
    def print_summary(self):
        """Вывести итоговую статистику"""
        logger.info(f"\n{'#'*60}")
        logger.info(f"ИТОГОВАЯ СТАТИСТИКА ТЕСТОВ")
        logger.info(f"{'#'*60}\n")
        
        total = len(self.test_results)
        passed = sum(1 for r in self.test_results if r['success'])
        failed = total - passed
        
        logger.info(f"Всего тестов: {total}")
        logger.info(f"✅ Успешно: {passed} ({passed/total*100:.1f}%)")
        logger.info(f"❌ Провалено: {failed} ({failed/total*100:.1f}%)")
        
        # Средние показатели
        avg_response_time = sum(r.get('elapsed', 0) for r in self.test_results) / total if total > 0 else 0
        avg_length = sum(r.get('length', 0) for r in self.test_results) / total if total > 0 else 0
        
        logger.info(f"\nСреднее время ответа: {avg_response_time:.2f}s")
        logger.info(f"Средняя длина ответа: {avg_length:.0f} символов")
        
        # Провальные тесты
        if failed > 0:
            logger.info(f"\n{'='*60}")
            logger.info("ПРОВАЛЕННЫЕ ТЕСТЫ:")
            logger.info(f"{'='*60}")
            for r in self.test_results:
                if not r['success']:
                    logger.info(f"\n❌ {r['test_name']}")
                    logger.info(f"   Запрос: {r['query'][:50]}...")
                    if 'error' in r:
                        logger.info(f"   Ошибка: {r['error']}")
                    else:
                        logger.info(f"   Ответ: {r.get('response', 'N/A')[:100]}...")
        
        logger.info(f"\n{'#'*60}\n")


async def main():
    """Главная функция"""
    # Проверить, существует ли тестовый пользователь
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=TEST_USER_ID).first()
        if not user:
            logger.warning(f"⚠️  Пользователь {TEST_USER_ID} не найден в БД")
            logger.info("Создаем тестового пользователя...")
            
            # Создаем тестового пользователя
            user = User(
                telegram_id=TEST_USER_ID,
                username="test_user",
                first_name="Test",
                timezone="Europe/Moscow"
            )
            session.add(user)
            session.commit()
            logger.info("✅ Тестовый пользователь создан")
        else:
            logger.info(f"✅ Найден пользователь: {user.username} (ID: {user.id})")
        
        # Создать профиль, если его нет
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if not profile:
            profile = UserProfile(
                user_id=user.id,
                position="Python Developer",
                city="Москва",
                interests="AI, ML, тестирование",
                skills="Python, FastAPI, PostgreSQL"
            )
            session.add(profile)
            session.commit()
            logger.info("✅ Профиль создан")
        
    finally:
        session.close()
    
    # Запустить тесты
    tester = AgentTester(TEST_USER_ID)
    await tester.run_all_tests()


if __name__ == "__main__":
    asyncio.run(main())
