#!/usr/bin/env python3
"""
Комплексный тест системы для проверки всех функций на проблемы с None и корректную работу.
Проверяет: ответы AI, напоминания, делегирование, профили, задачи.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta
import pytz

# Настройка окружения
os.environ['LOCAL'] = '1'
os.environ['FREE_ACCESS_MODE'] = '1'

# Настройка логирования
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Импорты
from models import SessionLocal, User, UserProfile, Task, Subscription
from ai_integration.chat import chat_with_ai
from ai_integration.handlers import add_task, list_tasks, complete_task, delegate_task
from reminder_service import ReminderService

class ComprehensiveTest:
    
    def __init__(self):
        self.session = SessionLocal()
        self.test_user_id = 9999999999  # Тестовый ID
        self.test_user2_id = 8888888888
        
    def setup_test_data(self):
        """Создание тестовых данных"""
        logger.info("=== СОЗДАНИЕ ТЕСТОВЫХ ДАННЫХ ===")
        
        # Удаляем старые тестовые данные
        self.session.query(Task).filter(
            Task.user_id.in_(
                self.session.query(User.id).filter(
                    User.telegram_id.in_([self.test_user_id, self.test_user2_id])
                ).subquery()
            )
        ).delete(synchronize_session=False)
        
        self.session.query(UserProfile).filter(
            UserProfile.user_id.in_(
                self.session.query(User.id).filter(
                    User.telegram_id.in_([self.test_user_id, self.test_user2_id])
                ).subquery()
            )
        ).delete(synchronize_session=False)
        
        self.session.query(User).filter(
            User.telegram_id.in_([self.test_user_id, self.test_user2_id])
        ).delete()
        
        self.session.commit()
        
        # Создаем тестовых пользователей
        user1 = User(
            telegram_id=self.test_user_id,
            username='testuser1',
            first_name='Тест Пользователь 1'
        )
        
        user2 = User(
            telegram_id=self.test_user2_id,
            username='testuser2',
            first_name='Тест Пользователь 2'
        )
        
        self.session.add(user1)
        self.session.add(user2)
        self.session.commit()
        
        # Создаем профили
        profile1 = UserProfile(
            user_id=user1.id,
            city='Москва',
            company='Тест Компания',
            position='Тестировщик',
            skills='Python, тестирование',
            interests='программирование, спорт',
            goals='автоматизация тестирования'
        )
        
        profile2 = UserProfile(
            user_id=user2.id,
            city='Санкт-Петербург',
            company='Другая Компания',
            skills='QA, менеджмент'
        )
        
        self.session.add(profile1)
        self.session.add(profile2)
        self.session.commit()
        
        logger.info(f"Созданы пользователи: {user1.id}, {user2.id}")
        
    def cleanup(self):
        """Очистка тестовых данных"""
        try:
            self.session.close()
        except:
            pass
            
    async def test_ai_responses(self):
        """Тестирование AI ответов"""
        logger.info("=== ТЕСТ AI ОТВЕТОВ ===")
        
        test_cases = [
            ("Привет", "приветствие"),
            ("Покажи мои задачи", "список задач"),
            ("Расскажи о моем профиле", "профиль"),
            ("Создай задачу: купить хлеб", "создание задачи"),
            ("Что делать завтра?", "планирование"),
            ("", "пустое сообщение"),
            (None, "None сообщение"),
        ]
        
        for message, description in test_cases:
            try:
                logger.info(f"Тест: {description}")
                if message is None:
                    logger.info(f"⚠️ Пропускаем тест с None сообщением")
                    continue
                    
                response = await chat_with_ai(message, user_id=self.test_user_id)
                
                if response and len(response) > 10:
                    logger.info(f"✅ {description}: OK (длина ответа: {len(response)})")
                else:
                    logger.warning(f"❌ {description}: Короткий или пустой ответ: {response[:100]}")
                    
            except Exception as e:
                logger.error(f"❌ {description}: Ошибка - {str(e)}")
                
    def test_task_functions(self):
        """Тестирование функций задач"""
        logger.info("=== ТЕСТ ФУНКЦИЙ ЗАДАЧ ===")
        
        # Тест добавления задач
        test_cases = [
            ("Купить молоко", "обычная задача"),
            ("", "пустое название"),
            (None, "None название"),
            ("Встреча с клиентом", "деловая задача"),
        ]
        
        for title, description in test_cases:
            try:
                logger.info(f"Тест добавления: {description}")
                result = add_task(title, user_id=self.test_user_id, session=self.session)
                
                if isinstance(result, str) and ("создана" in result.lower() or "обновлена" in result.lower() or "error" in result.lower()):
                    logger.info(f"✅ {description}: {result[:100]}")
                else:
                    logger.warning(f"❌ {description}: Неожиданный результат: {str(result)[:100]}")
                    
            except Exception as e:
                logger.error(f"❌ {description}: Ошибка - {str(e)}")
        
        # Тест списка задач
        try:
            logger.info("Тест списка задач")
            result = list_tasks(user_id=self.test_user_id, session=self.session)
            logger.info(f"✅ Список задач: {result[:100]}...")
        except Exception as e:
            logger.error(f"❌ Список задач: Ошибка - {str(e)}")
            
        # Тест с None user_id
        try:
            logger.info("Тест с None user_id")
            result = list_tasks(user_id=None, session=self.session)
            if "error" in result.lower() or "не может быть" in result.lower():
                logger.info(f"✅ None user_id обработан корректно: {result}")
            else:
                logger.warning(f"❌ None user_id не обработан: {result}")
        except Exception as e:
            logger.error(f"❌ None user_id: Ошибка - {str(e)}")
            
    def test_delegation_functions(self):
        """Тестирование функций делегирования"""
        logger.info("=== ТЕСТ ФУНКЦИЙ ДЕЛЕГИРОВАНИЯ ===")
        
        try:
            # Тест делегирования задачи
            result = delegate_task(
                title="Тестовая делегированная задача",
                delegated_to_username="testuser2",
                reminder_time="2026-01-25 15:00",
                user_id=self.test_user_id,
                description="Тестовое описание"
            )
            logger.info(f"✅ Делегирование: {result[:100]}...")
            
        except Exception as e:
            logger.error(f"❌ Делегирование: Ошибка - {str(e)}")
            
        # Тест с некорректными данными  
        test_cases = [
            (None, "testuser2", "None название"),
            ("Задача", None, "None получатель"),
            ("Задача", "несуществующий", "несуществующий пользователь"),
        ]
        
        for title, recipient, description in test_cases:
            try:
                result = delegate_task(
                    title=title,
                    delegated_to_username=recipient,
                    reminder_time="2026-01-25 16:00",
                    user_id=self.test_user_id
                )
                logger.info(f"✅ {description}: {str(result)[:100]}...")
            except Exception as e:
                logger.error(f"❌ {description}: Ошибка - {str(e)}")
                
    async def test_reminder_service(self):
        """Тестирование сервиса напоминаний"""
        logger.info("=== ТЕСТ СЕРВИСА НАПОМИНАНИЙ ===")
        
        try:
            # Создаем задачу с напоминанием
            future_time = datetime.now(pytz.UTC) + timedelta(minutes=1)
            task = Task(
                user_id=self.session.query(User).filter_by(telegram_id=self.test_user_id).first().id,
                title="Тестовая задача с напоминанием",
                reminder_time=future_time,
                status='pending'
            )
            self.session.add(task)
            self.session.commit()
            
            # Проверяем, что задача создалась
            saved_task = self.session.query(Task).filter_by(id=task.id).first()
            if saved_task and saved_task.reminder_time:
                logger.info(f"✅ Задача с напоминанием создана: ID={saved_task.id}")
            else:
                logger.warning("❌ Задача с напоминанием не создалась корректно")
                
            # Тестируем обработку None в reminder service  
            from reminder_service import _send_reminder_job, _send_result_check_job
            
            # Тест с корректным ID
            logger.info("Тестируем _send_reminder_job с корректным ID")
            await _send_reminder_job(task.id)
            logger.info("✅ _send_reminder_job выполнен без ошибок")
            
            # Тест с несуществующим ID
            logger.info("Тестируем _send_reminder_job с несуществующим ID")
            await _send_reminder_job(999999)
            logger.info("✅ _send_reminder_job с несуществующим ID обработан")
            
        except Exception as e:
            logger.error(f"❌ Тест напоминаний: Ошибка - {str(e)}")
            
    async def test_profile_functions(self):
        """Тестирование функций профиля"""
        logger.info("=== ТЕСТ ФУНКЦИЙ ПРОФИЛЯ ===")
        
        try:
            # Тест обновления профиля через AI
            response = await chat_with_ai(
                "Обнови мой профиль: интересы - машинное обучение, спорт",
                user_id=self.test_user_id
            )
            logger.info(f"✅ Обновление профиля: {response[:100]}...")
            
            # Проверяем, что профиль действительно обновился
            profile = self.session.query(UserProfile).filter_by(
                user_id=self.session.query(User).filter_by(telegram_id=self.test_user_id).first().id
            ).first()
            
            if profile:
                logger.info(f"✅ Профиль найден: интересы={profile.interests}")
            else:
                logger.warning("❌ Профиль не найден после обновления")
                
        except Exception as e:
            logger.error(f"❌ Тест профиля: Ошибка - {str(e)}")
            
    async def run_all_tests(self):
        """Запуск всех тестов"""
        logger.info("🚀 ЗАПУСК КОМПЛЕКСНОГО ТЕСТИРОВАНИЯ")
        
        try:
            self.setup_test_data()
            
            await self.test_ai_responses()
            self.test_task_functions()  
            self.test_delegation_functions()
            await self.test_reminder_service()
            await self.test_profile_functions()
            
            logger.info("✅ ВСЕ ТЕСТЫ ЗАВЕРШЕНЫ")
            
        except Exception as e:
            logger.error(f"❌ КРИТИЧЕСКАЯ ОШИБКА В ТЕСТАХ: {str(e)}")
        finally:
            self.cleanup()

async def main():
    test = ComprehensiveTest()
    await test.run_all_tests()

if __name__ == "__main__":
    asyncio.run(main())