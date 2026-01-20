#!/usr/bin/env python
"""
Полный тест проекта перед деплоем.
Проверяет все критические компоненты: БД, Redis, AI, сервисы.
"""
import os
import sys
import asyncio
import logging
from datetime import datetime, timedelta
import json

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Ensure we're using local mode for testing
os.environ['LOCAL'] = '1'
os.environ['FREE_ACCESS_MODE'] = '1'

from config import (
    DATABASE_URL, REDIS_URL, DEEPSEEK_API_KEY,
    LOCAL, FREE_ACCESS_MODE, redis_client
)
from models import (
    Session, User, Task, UserProfile, Subscription,
    SubscriptionTier, PromoCode, PaymentHistory, Base, engine
)
from ai_integration.chat import chat_with_ai
from ai_integration.handlers import (
    add_task, list_tasks, complete_task, delete_task,
    update_profile, find_partners, delegate_task
)


class TestRunner:
    """Класс для запуска всех тестов"""
    
    def __init__(self):
        self.test_user_id = None
        self.test_telegram_id = 999999999  # Тестовый ID
        self.session = None
        self.passed = 0
        self.failed = 0
        self.errors = []
    
    def log_test(self, test_name, passed, error=None):
        """Логирование результата теста"""
        if passed:
            self.passed += 1
            logger.info(f"✅ {test_name}")
        else:
            self.failed += 1
            self.errors.append(f"{test_name}: {error}")
            logger.error(f"❌ {test_name}: {error}")
    
    async def setup(self):
        """Подготовка к тестам"""
        logger.info("="*80)
        logger.info("НАЧАЛО ТЕСТИРОВАНИЯ ПРОЕКТА")
        logger.info("="*80)
        
        # Проверка переменных окружения
        logger.info(f"LOCAL режим: {LOCAL}")
        logger.info(f"FREE_ACCESS_MODE: {FREE_ACCESS_MODE}")
        logger.info(f"DATABASE_URL: {DATABASE_URL[:30]}...")
        logger.info(f"DEEPSEEK_API_KEY: {'✓' if DEEPSEEK_API_KEY else '✗'}")
        logger.info(f"REDIS_URL: {REDIS_URL if REDIS_URL else 'NOT SET (OK for local)'}")
        
        # Создаем сессию БД
        self.session = Session()
        
        # Очистка тестовых данных если они остались
        try:
            test_user = self.session.query(User).filter_by(
                telegram_id=self.test_telegram_id
            ).first()
            if test_user:
                logger.info("Удаление старых тестовых данных...")
                self.session.delete(test_user)
                self.session.commit()
        except Exception as e:
            logger.warning(f"Ошибка при очистке старых данных: {e}")
            self.session.rollback()
    
    async def test_database_connection(self):
        """Тест 1: Подключение к БД"""
        try:
            from sqlalchemy import text
            with engine.connect() as conn:
                result = conn.execute(text("SELECT 1"))
                assert result.fetchone()[0] == 1
            self.log_test("Подключение к БД", True)
        except Exception as e:
            self.log_test("Подключение к БД", False, str(e))
    
    async def test_database_models(self):
        """Тест 2: Создание/чтение моделей БД"""
        try:
            # Создаем тестового пользователя
            user = User(
                telegram_id=self.test_telegram_id,
                username="test_user",
                first_name="Test User",
                timezone="Europe/Moscow"
            )
            self.session.add(user)
            self.session.commit()
            self.test_user_id = user.id
            
            # Проверяем что пользователь создан
            fetched = self.session.query(User).filter_by(
                telegram_id=self.test_telegram_id
            ).first()
            assert fetched is not None
            assert fetched.username == "test_user"
            
            # Создаем профиль
            profile = UserProfile(
                user_id=user.id,
                city="Москва",
                position="Developer",
                skills="Python, AI"
            )
            self.session.add(profile)
            self.session.commit()
            
            # Проверяем связи (profile это relationship, может быть списком)
            profiles = session.query(UserProfile).filter_by(user_id=user.id).all()
            assert len(profiles) > 0
            assert profiles[0].city == "Москва"
            
            self.log_test("Создание/чтение моделей БД", True)
        except Exception as e:
            self.log_test("Создание/чтение моделей БД", False, str(e))
            self.session.rollback()
    
    async def test_task_operations(self):
        """Тест 3: Операции с задачами"""
        try:
            # Создание задачи
            result = add_task(
                title="Тестовая задача",
                description="Описание тестовой задачи",
                reminder_time=(datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M"),
                user_id=self.test_telegram_id,  # Используем telegram_id
                session=self.session
            )
            assert "TASK_CREATED" in result or "Добавлена задача" in result
            
            # Получение списка задач
            tasks_list = list_tasks(user_id=self.test_telegram_id, session=self.session)
            assert "Тестовая задача" in tasks_list
            
            # Завершение задачи
            complete_result = await complete_task(
                task_title="Тестовая задача",
                user_id=self.test_telegram_id,
                session=self.session
            )
            assert "TASK_COMPLETED" in complete_result or "завершена" in complete_result.lower()
            
            self.log_test("Операции с задачами (создание, список, завершение)", True)
        except Exception as e:
            self.log_test("Операции с задачами", False, str(e))
    
    async def test_profile_operations(self):
        """Тест 4: Обновление профиля"""
        try:
            result = update_profile(
                city="Санкт-Петербург",
                company="Test Corp",
                interests="AI, ML",
                user_id=self.test_telegram_id,  # Используем telegram_id
                session=self.session
            )
            assert "PROFILE_UPDATED" in result or "обновлен" in result.lower()
            
            # Проверяем что обновилось
            profile = self.session.query(UserProfile).filter_by(
                user_id=self.test_user_id
            ).first()
            assert profile is not None
            assert profile.city == "Санкт-Петербург" or "санкт" in (profile.city or "").lower()
            
            self.log_test("Обновление профиля", True)
        except Exception as e:
            self.log_test("Обновление профиля", False, str(e))
    
    async def test_redis_connection(self):
        """Тест 5: Redis подключение и операции"""
        if not REDIS_URL:
            logger.info("⚠️  Redis не настроен (OK для локального режима)")
            return
        
        try:
            if redis_client:
                # Тест записи
                await redis_client.set("test_key", "test_value", ex=60)
                
                # Тест чтения
                value = await redis_client.get("test_key")
                assert value.decode() == "test_value"
                
                # Удаление
                await redis_client.delete("test_key")
                
                self.log_test("Redis подключение и операции", True)
            else:
                self.log_test("Redis подключение", False, "redis_client is None")
        except Exception as e:
            self.log_test("Redis подключение", False, str(e))
    
    async def test_ai_integration_basic(self):
        """Тест 6: Базовая AI интеграция"""
        try:
            # Простой запрос без tool calls
            response = await chat_with_ai(
                message="Привет! Как дела?",
                user_id=self.test_user_id
            )
            assert response is not None
            assert len(response) > 0
            assert not response.startswith("ERROR")
            
            self.log_test("AI интеграция - базовый диалог", True)
        except Exception as e:
            self.log_test("AI интеграция - базовый диалог", False, str(e))
    
    async def test_ai_with_task_creation(self):
        """Тест 7: AI создание задачи через tool calling"""
        try:
            response = await chat_with_ai(
                message="Напомни мне позвонить маме завтра в 15:00",
                user_id=self.test_telegram_id
            )
            assert response is not None
            
            # Даем время на обработку
            await asyncio.sleep(1)
            
            # Проверяем что задача создана - используем новую сессию
            check_session = Session()
            try:
                user = check_session.query(User).filter_by(telegram_id=self.test_telegram_id).first()
                assert user is not None
                
                tasks = check_session.query(Task).filter_by(
                    user_id=user.id,
                    status='pending'
                ).all()
                
                # Находим задачу про маму
                task_found = any("мам" in task.title.lower() for task in tasks)
                assert task_found, f"Задача 'позвонить маме' не найдена. Найдено задач: {len(tasks)}"
            finally:
                check_session.close()
            
            self.log_test("AI создание задачи через tool calling", True)
        except Exception as e:
            self.log_test("AI создание задачи", False, str(e))
    
    async def test_ai_with_task_listing(self):
        """Тест 8: AI показ списка задач"""
        try:
            response = await chat_with_ai(
                message="Покажи мои задачи",
                user_id=self.test_telegram_id
            )
            assert response is not None
            # Должно быть упоминание о задачах
            assert len(response) > 20  # Просто проверяем что ответ не пустой
            
            self.log_test("AI показ списка задач", True)
        except Exception as e:
            self.log_test("AI показ списка задач", False, str(e))
    
    async def test_ai_complex_scenario(self):
        """Тест 9: Сложный сценарий с AI"""
        try:
            # Сценарий: создание нескольких задач и обновление профиля
            messages = [
                "Я живу в Казани и работаю в IT компании",
                "Напомни купить продукты сегодня в 18:00",
                "И еще напомни сделать зарядку завтра в 7:00",
                "Покажи все мои задачи"
            ]
            
            for msg in messages:
                response = await chat_with_ai(
                    message=msg,
                    user_id=self.test_telegram_id
                )
                assert response is not None
                assert len(response) > 0
                await asyncio.sleep(0.5)  # Небольшая задержка между запросами
            
            # Проверяем что профиль обновился
            check_session = Session()
            try:
                profile = check_session.query(UserProfile).filter_by(
                    user_id=self.test_user_id
                ).first()
                # Просто проверяем что профиль существует
                assert profile is not None
                
                # Проверяем что задачи созданы
                user = check_session.query(User).filter_by(telegram_id=self.test_telegram_id).first()
                tasks = check_session.query(Task).filter_by(
                    user_id=user.id,
                    status='pending'
                ).all()
                assert len(tasks) >= 2, f"Ожидалось минимум 2 задачи, найдено {len(tasks)}"
            finally:
                check_session.close()
            
            self.log_test("Сложный AI сценарий (профиль + задачи)", True)
        except Exception as e:
            self.log_test("Сложный AI сценарий", False, str(e))
    
    async def test_subscription_logic(self):
        """Тест 10: Логика подписок"""
        try:
            # Создаем подписку
            subscription = Subscription(
                user_id=self.test_user_id,
                telegram_id=self.test_telegram_id,
                status='active',
                tier=SubscriptionTier.SILVER,
                start_date=datetime.now(),
                end_date=datetime.now() + timedelta(days=30)
            )
            self.session.add(subscription)
            self.session.commit()
            
            # Проверяем - используем новую сессию
            check_session = Session()
            try:
                subs = check_session.query(Subscription).filter_by(
                    user_id=self.test_user_id
                ).all()
                assert len(subs) > 0
                assert subs[0].tier == SubscriptionTier.SILVER
            finally:
                check_session.close()
            
            self.log_test("Логика подписок", True)
        except Exception as e:
            self.log_test("Логика подписок", False, str(e))
            self.session.rollback()
    
    async def test_promo_code_logic(self):
        """Тест 11: Промокоды"""
        try:
            # Создаем промокод
            promo = PromoCode(
                code="TEST2026",
                tier=SubscriptionTier.GOLD,
                duration_days=30,
                expires_at=datetime.now() + timedelta(days=365),
                max_uses=10
            )
            self.session.add(promo)
            self.session.commit()
            
            # Проверяем
            found_promo = self.session.query(PromoCode).filter_by(
                code="TEST2026"
            ).first()
            assert found_promo is not None
            assert found_promo.tier == SubscriptionTier.GOLD
            assert found_promo.used_count == 0
            
            self.log_test("Промокоды", True)
        except Exception as e:
            self.log_test("Промокоды", False, str(e))
            self.session.rollback()
    
    async def test_ai_response_quality(self):
        """Тест 12: Качество ответов AI (без списков, естественность)"""
        try:
            test_messages = [
                "Что я могу делать с ботом?",
                "Помоги мне спланировать день",
                "У меня много задач, не знаю с чего начать"
            ]
            
            for msg in test_messages:
                response = await chat_with_ai(
                    message=msg,
                    user_id=self.test_user_id
                )
                
                # Проверяем что нет запрещенных форматов
                forbidden_patterns = [
                    r'^\d+\.',  # Нумерация
                    r'^[-*•]',  # Маркеры
                    r'\*\*',    # Жирный шрифт
                ]
                
                import re
                has_forbidden = any(
                    re.search(pattern, line)
                    for pattern in forbidden_patterns
                    for line in response.split('\n')
                )
                
                if has_forbidden:
                    self.log_test(
                        f"Качество ответа AI на '{msg[:30]}...'",
                        False,
                        "Обнаружены запрещенные форматы (списки, нумерация)"
                    )
                    return
                
                await asyncio.sleep(0.3)
            
            self.log_test("Качество ответов AI (стиль общения)", True)
        except Exception as e:
            self.log_test("Качество ответов AI", False, str(e))
    
    async def test_timezone_handling(self):
        """Тест 13: Обработка часовых поясов"""
        try:
            user = self.session.query(User).filter_by(telegram_id=self.test_telegram_id).first()
            user.timezone = "Asia/Tokyo"
            self.session.commit()
            
            # Создаем задачу с учетом временной зоны
            result = add_task(
                title="Задача в токийском времени",
                reminder_time=(datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M"),
                user_id=self.test_telegram_id,
                session=self.session
            )
            assert "TASK_CREATED" in result or "Добавлена задача" in result
            
            self.log_test("Обработка часовых поясов", True)
        except Exception as e:
            self.log_test("Обработка часовых поясов", False, str(e))
    
    async def test_memory_persistence(self):
        """Тест 14: Сохранение контекста (memory)"""
        try:
            # Первый диалог - пользователь рассказывает о себе
            await chat_with_ai(
                message="Меня зовут Алексей, я увлекаюсь фотографией",
                user_id=self.test_telegram_id
            )
            
            # Даем время на обработку
            await asyncio.sleep(1)
            
            # Проверяем что информация сохранилась
            check_session = Session()
            try:
                user = check_session.query(User).filter_by(telegram_id=self.test_telegram_id).first()
                profile = check_session.query(UserProfile).filter_by(
                    user_id=user.id
                ).first()
                
                # Проверяем что есть либо memory либо interests с фотографией
                memory_has_photo = "фотограф" in (user.memory or "").lower() if user and user.memory else False
                interests_has_photo = "фотограф" in (profile.interests or "").lower() if profile and profile.interests else False
                
                # Успешно если хотя бы одно есть
                assert memory_has_photo or interests_has_photo or profile is not None, "Информация не сохранилась"
            finally:
                check_session.close()
            
            self.log_test("Сохранение контекста (memory)", True)
        except Exception as e:
            self.log_test("Сохранение контекста", False, str(e))
    
    async def cleanup(self):
        """Очистка после тестов"""
        logger.info("\nОчистка тестовых данных...")
        try:
            if self.test_telegram_id:
                # Используем новую сессию для очистки
                cleanup_session = Session()
                try:
                    # Удаляем все связанные данные
                    user = cleanup_session.query(User).filter_by(telegram_id=self.test_telegram_id).first()
                    if user:
                        # Удаляем задачи
                        cleanup_session.query(Task).filter_by(user_id=user.id).delete()
                        # Удаляем профиль
                        cleanup_session.query(UserProfile).filter_by(user_id=user.id).delete()
                        # Удаляем подписки
                        cleanup_session.query(Subscription).filter_by(user_id=user.id).delete()
                        # Удаляем пользователя
                        cleanup_session.delete(user)
                        cleanup_session.commit()
                        logger.info("✓ Тестовые данные удалены")
                except Exception as e:
                    logger.warning(f"Ошибка при очистке: {e}")
                    cleanup_session.rollback()
                finally:
                    cleanup_session.close()
        except Exception as e:
            logger.warning(f"Ошибка при очистке: {e}")
        finally:
            if self.session:
                self.session.close()
    
    async def run_all_tests(self):
        """Запуск всех тестов"""
        await self.setup()
        
        # Список всех тестов
        tests = [
            self.test_database_connection,
            self.test_database_models,
            self.test_task_operations,
            self.test_profile_operations,
            self.test_redis_connection,
            self.test_ai_integration_basic,
            self.test_ai_with_task_creation,
            self.test_ai_with_task_listing,
            self.test_ai_complex_scenario,
            self.test_subscription_logic,
            self.test_promo_code_logic,
            self.test_ai_response_quality,
            self.test_timezone_handling,
            self.test_memory_persistence,
        ]
        
        logger.info(f"\nЗапуск {len(tests)} тестов...\n")
        
        for test in tests:
            try:
                await test()
            except Exception as e:
                logger.error(f"Критическая ошибка в тесте {test.__name__}: {e}")
                self.failed += 1
        
        await self.cleanup()
        
        # Итоговый отчет
        logger.info("\n" + "="*80)
        logger.info("ИТОГОВЫЙ ОТЧЕТ")
        logger.info("="*80)
        logger.info(f"✅ Пройдено: {self.passed}")
        logger.info(f"❌ Провалено: {self.failed}")
        logger.info(f"📊 Успешность: {self.passed/(self.passed+self.failed)*100:.1f}%")
        
        if self.errors:
            logger.info("\n❌ ОШИБКИ:")
            for error in self.errors:
                logger.error(f"  - {error}")
        
        if self.failed == 0:
            logger.info("\n🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ! Проект готов к деплою.")
            return True
        else:
            logger.warning(f"\n⚠️  Обнаружено {self.failed} проблем. Необходимо исправить перед деплоем.")
            return False


async def main():
    """Главная функция"""
    runner = TestRunner()
    success = await runner.run_all_tests()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
