"""
Тест работы агента с большим объемом данных и задач.
Проверяет корректность операций CRUD, отсутствие дублей,
производительность и стабильность при большом объеме данных.
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from models import Session, User, UserProfile, Task
from ai_integration.handlers import add_task, list_tasks, complete_task, delete_task, reschedule_task
from ai_integration.chat import chat_with_ai

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class LargeDataTester:
    def __init__(self):
        self.user_id = None
        self.created_tasks = []
        self.test_results = {
            "total_tasks_created": 0,
            "duplicates_found": 0,
            "operations_completed": 0,
            "errors_encountered": 0,
            "performance_metrics": {},
            "data_integrity": True
        }

    async def setup_test_user(self):
        """Создает тестового пользователя с профилем"""
        session = Session()
        try:
            # Создаем пользователя
            user = User(
                telegram_id=999999999,  # Тестовый ID
                username="test_user_large_data",
                first_name="Test",
                memory="Тестовый пользователь для проверки работы с большим объемом данных",
                timezone="Europe/Moscow"
            )
            session.add(user)
            session.commit()

            # Создаем профиль
            profile = UserProfile(
                user_id=user.id,
                skills="Python, Machine Learning, Data Analysis",
                interests="Programming, AI, Sports, Business",
                goals="Become ML expert, Start own business",
                city="Moscow",
                company="Test Company",
                position="Developer",
                bio="Test user for large data testing"
            )
            session.add(profile)
            session.commit()

            self.user_id = user.id
            logger.info(f"Created test user with ID: {self.user_id}")

        except Exception as e:
            session.rollback()
            logger.error(f"Error creating test user: {e}")
            raise
        finally:
            session.close()

    async def create_bulk_tasks(self, count=50):
        """Создает большое количество задач"""
        logger.info(f"Creating {count} test tasks...")

        session = Session()
        start_time = time.time()

        try:
            for i in range(count):
                # Создаем разнообразные задачи
                task_types = [
                    f"Task {i+1}: Review Python documentation",
                    f"Task {i+1}: Implement ML algorithm",
                    f"Task {i+1}: Call client about project",
                    f"Task {i+1}: Prepare presentation",
                    f"Task {i+1}: Debug application",
                    f"Task {i+1}: Write unit tests",
                    f"Task {i+1}: Update dependencies",
                    f"Task {i+1}: Research new technology",
                    f"Task {i+1}: Refactor code",
                    f"Task {i+1}: Deploy to production"
                ]

                task_title = task_types[i % len(task_types)]
                reminder_time = datetime.now(timezone.utc) + timedelta(hours=i+1)

                task = Task(
                    user_id=self.user_id,
                    title=task_title,
                    description=f"Detailed description for {task_title}",
                    reminder_time=reminder_time,
                    status='pending',
                    created_at=datetime.now(timezone.utc)
                )

                session.add(task)
                self.created_tasks.append(task)

            session.commit()

            creation_time = time.time() - start_time
            self.test_results["total_tasks_created"] = count
            self.test_results["performance_metrics"]["bulk_creation_time"] = creation_time

            logger.info(f"Created {count} tasks in {creation_time:.2f} seconds")

        except Exception as e:
            session.rollback()
            logger.error(f"Error creating bulk tasks: {e}")
            self.test_results["errors_encountered"] += 1
            raise
        finally:
            session.close()

    async def test_duplicates(self):
        """Проверяет наличие дублей задач"""
        logger.info("Checking for duplicate tasks...")

        session = Session()
        try:
            # Проверяем дубли по названию
            tasks = session.query(Task).filter_by(user_id=self.user_id).all()
            titles = [task.title for task in tasks]

            duplicates = len(titles) - len(set(titles))
            self.test_results["duplicates_found"] = duplicates

            if duplicates > 0:
                logger.warning(f"Found {duplicates} duplicate tasks")
                self.test_results["data_integrity"] = False
            else:
                logger.info("No duplicates found")

        except Exception as e:
            logger.error(f"Error checking duplicates: {e}")
            self.test_results["errors_encountered"] += 1
        finally:
            session.close()

    async def test_crud_operations(self):
        """Тестирует операции CRUD с большим объемом данных"""
        logger.info("Testing CRUD operations...")

        # Тест чтения (list_tasks)
        start_time = time.time()
        try:
            tasks_list = list_tasks(user_id=self.user_id)
            list_time = time.time() - start_time
            self.test_results["performance_metrics"]["list_tasks_time"] = list_time
            logger.info(f"Listed {len(tasks_list.split())} tasks in {list_time:.2f} seconds")
        except Exception as e:
            logger.error(f"Error listing tasks: {e}")
            self.test_results["errors_encountered"] += 1

        # Тест обновления (reschedule_task)
        session = Session()
        try:
            # Берем первую задачу для теста
            first_task = session.query(Task).filter_by(user_id=self.user_id).first()
            if first_task:
                new_time = datetime.now(timezone.utc) + timedelta(hours=2)
                result = await reschedule_task(
                    task_title=first_task.title,
                    new_time=new_time.isoformat(),
                    user_id=999999999  # telegram_id
                )
                if "обновлена" in result or "перенесена" in result:
                    self.test_results["operations_completed"] += 1
                    logger.info("Successfully rescheduled task")
                else:
                    logger.warning(f"Failed to reschedule task: {result}")
        except Exception as e:
            logger.error(f"Error rescheduling task: {e}")
            self.test_results["errors_encountered"] += 1
        finally:
            session.close()

        # Тест завершения задач
        try:
            # Завершаем несколько задач
            tasks_to_complete = 5
            session = Session()
            pending_tasks = session.query(Task).filter_by(
                user_id=self.user_id,
                status='pending'
            ).limit(tasks_to_complete).all()

            for task in pending_tasks:
                result = await complete_task(task_title=task.title, user_id=999999999)
                if "завершена" in result or "готово" in result:
                    self.test_results["operations_completed"] += 1
                else:
                    logger.warning(f"Failed to complete task {task.title}: {result}")

            session.close()
            logger.info(f"Completed {tasks_to_complete} tasks")

        except Exception as e:
            logger.error(f"Error completing tasks: {e}")
            self.test_results["errors_encountered"] += 1

        # Тест удаления задач
        try:
            # Удаляем несколько задач
            tasks_to_delete = 3
            session = Session()
            tasks_for_deletion = session.query(Task).filter_by(
                user_id=self.user_id,
                status='pending'
            ).limit(tasks_to_delete).all()

            for task in tasks_for_deletion:
                result = await delete_task(task_title=task.title, user_id=999999999)
                if "удалена" in result or "удалил" in result:
                    self.test_results["operations_completed"] += 1
                else:
                    logger.warning(f"Failed to delete task {task.title}: {result}")

            session.close()
            logger.info(f"Deleted {tasks_to_delete} tasks")

        except Exception as e:
            logger.error(f"Error deleting tasks: {e}")
            self.test_results["errors_encountered"] += 1

    async def test_ai_interaction(self):
        """Тестирует взаимодействие AI с большим объемом данных"""
        logger.info("Testing AI interaction with large dataset...")

        try:
            # Тест запроса списка задач
            response = await chat_with_ai(
                user_id=999999999,  # telegram_id
                message="покажи мои задачи",
                context=""
            )

            if isinstance(response, dict) and "response" in response:
                response_text = response["response"]
            else:
                response_text = str(response)

            if "задач" in response_text.lower():
                logger.info("AI successfully handled task listing")
            else:
                logger.warning("AI failed to list tasks properly")

            # Тест создания новой задачи
            response = await chat_with_ai(
                user_id=999999999,  # telegram_id
                message="напомни мне о встрече через 2 часа",
                context=""
            )

            if isinstance(response, dict) and "response" in response:
                response_text = response["response"]
            else:
                response_text = str(response)

            if "создал" in response_text.lower() or "добавил" in response_text.lower():
                logger.info("AI successfully created new task")
                self.test_results["operations_completed"] += 1
            else:
                logger.warning("AI failed to create task")

        except Exception as e:
            logger.error(f"Error testing AI interaction: {e}")
            self.test_results["errors_encountered"] += 1

    async def test_data_consistency(self):
        """Проверяет консистентность данных после всех операций"""
        logger.info("Checking data consistency...")

        session = Session()
        try:
            # Проверяем общее количество задач
            total_tasks = session.query(Task).filter_by(user_id=self.user_id).count()
            pending_tasks = session.query(Task).filter_by(user_id=self.user_id, status='pending').count()
            completed_tasks = session.query(Task).filter_by(user_id=self.user_id, status='completed').count()

            logger.info(f"Data consistency check: Total={total_tasks}, Pending={pending_tasks}, Completed={completed_tasks}")

            # Проверяем, что нет задач с некорректным статусом
            invalid_tasks = session.query(Task).filter(
                Task.user_id == self.user_id,
                Task.status.notin_(['pending', 'completed', 'cancelled'])
            ).count()

            if invalid_tasks > 0:
                logger.warning(f"Found {invalid_tasks} tasks with invalid status")
                self.test_results["data_integrity"] = False

            # Проверяем, что все задачи имеют корректные временные метки
            tasks_without_time = session.query(Task).filter(
                Task.user_id == self.user_id,
                Task.created_at.is_(None)
            ).count()

            if tasks_without_time > 0:
                logger.warning(f"Found {tasks_without_time} tasks without creation time")
                self.test_results["data_integrity"] = False

        except Exception as e:
            logger.error(f"Error checking data consistency: {e}")
            self.test_results["errors_encountered"] += 1
        finally:
            session.close()

    async def cleanup(self):
        """Очищает тестовые данные"""
        logger.info("Cleaning up test data...")

        session = Session()
        try:
            # Удаляем все задачи тестового пользователя
            deleted_tasks = session.query(Task).filter_by(user_id=self.user_id).delete()

            # Удаляем профиль
            session.query(UserProfile).filter_by(user_id=self.user_id).delete()

            # Удаляем пользователя
            session.query(User).filter_by(id=self.user_id).delete()

            session.commit()
            logger.info(f"Cleaned up {deleted_tasks} tasks and test user")

        except Exception as e:
            session.rollback()
            logger.error(f"Error during cleanup: {e}")
        finally:
            session.close()

    async def run_comprehensive_test(self):
        """Запускает полный набор тестов"""
        logger.info("Starting comprehensive large data test...")

        try:
            # Настройка
            await self.setup_test_user()

            # Создание большого объема данных
            await self.create_bulk_tasks(100)  # Создаем 100 задач

            # Проверка на дубли
            await self.test_duplicates()

            # Тестирование операций CRUD
            await self.test_crud_operations()

            # Тестирование AI взаимодействия
            await self.test_ai_interaction()

            # Проверка консистентности данных
            await self.test_data_consistency()

        except Exception as e:
            logger.error(f"Test failed: {e}")
            self.test_results["errors_encountered"] += 1
        finally:
            # Очистка
            await self.cleanup()

        # Вывод результатов
        self.print_results()

    def print_results(self):
        """Выводит результаты тестирования"""
        print("\n" + "="*50)
        print("РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ БОЛЬШОГО ОБЪЕМА ДАННЫХ")
        print("="*50)

        print(f"Всего создано задач: {self.test_results['total_tasks_created']}")
        print(f"Найдено дублей: {self.test_results['duplicates_found']}")
        print(f"Выполнено операций: {self.test_results['operations_completed']}")
        print(f"Возникло ошибок: {self.test_results['errors_encountered']}")
        print(f"Целостность данных: {'✅' if self.test_results['data_integrity'] else '❌'}")

        print("\nМетрики производительности:")
        for metric, value in self.test_results['performance_metrics'].items():
            print(f"  {metric}: {value:.2f} сек")

        print("\n" + "="*50)

        # Оценка результатов
        if (self.test_results['duplicates_found'] == 0 and
            self.test_results['errors_encountered'] == 0 and
            self.test_results['data_integrity']):
            print("✅ ТЕСТ ПРОЙДЕН: Агент корректно работает с большим объемом данных")
        else:
            print("❌ ТЕСТ НЕ ПРОЙДЕН: Обнаружены проблемы с обработкой данных")

async def main():
    tester = LargeDataTester()
    await tester.run_comprehensive_test()

if __name__ == "__main__":
    asyncio.run(main())