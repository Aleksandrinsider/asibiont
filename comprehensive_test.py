#!/usr/bin/env python3
"""
Полный тест всех функций агента TaskChat
Тестирует каждую функцию на корректность работы, обработку ошибок и edge cases
"""

import asyncio
import sys
import os
import json
from datetime import datetime, timedelta
import traceback

# Добавляем текущую директорию в путь
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Импортируем функции агента
from ai_integration import (
    chat_with_ai, add_task, list_tasks, complete_task, delete_task,
    delegate_task, find_partners, update_profile, update_user_memory,
    set_reminder, edit_task, get_task_details, set_priority,
    accept_delegated_task, reject_delegated_task, get_delegation_progress,
    get_partners_list, parse_relative_time, parse_absolute_time,
    parse_tool_arguments, clean_content, replace_placeholders,
    clean_technical_details, encrypt_data, decrypt_data
)

# Импортируем модели для работы с БД
from models import Session, User, Task, UserProfile, Interaction
from config import ENCRYPTION_KEY

class AgentFunctionTester:
    def __init__(self):
        self.test_user_id = 999888777
        self.session = None
        self.user = None
        self.passed = 0
        self.failed = 0
        self.errors = []

    def log(self, message):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

    def test_pass(self, test_name):
        self.passed += 1
        self.log(f"✅ PASS {test_name}")

    def test_fail(self, test_name, error):
        self.failed += 1
        self.errors.append(f"{test_name}: {error}")
        self.log(f"❌ FAIL {test_name}: {error}")

    def setup_database(self):
        """Настраивает тестовую базу данных"""
        try:
            self.session = Session()
            # Создаем тестового пользователя
            self.user = self.session.query(User).filter_by(telegram_id=self.test_user_id).first()
            if not self.user:
                self.user = User(
                    telegram_id=self.test_user_id,
                    username="test_user",
                    timezone="Europe/Moscow"
                )
                self.session.add(self.user)
                self.session.commit()
            self.log("База данных настроена")
        except Exception as e:
            self.test_fail("setup_database", str(e))

    def cleanup_database(self):
        """Очищает тестовые данные"""
        try:
            if self.session:
                # Удаляем все тестовые задачи
                self.session.query(Task).filter_by(user_id=self.user.id).delete()
                # Удаляем тестовые взаимодействия
                self.session.query(Interaction).filter_by(user_id=self.user.id).delete()
                # Удаляем тестовый профиль
                self.session.query(UserProfile).filter_by(user_id=self.user.id).delete()
                self.session.commit()
                self.session.close()
            self.log("База данных очищена")
        except Exception as e:
            self.log(f"Ошибка очистки БД: {e}")

    async def test_chat_with_ai_basic(self):
        """Тест базовой функциональности chat_with_ai"""
        try:
            response = await chat_with_ai("Привет!", user_id=self.test_user_id)
            if response and len(response.strip()) > 0:
                self.test_pass("test_chat_with_ai_basic")
            else:
                self.test_fail("test_chat_with_ai_basic", "Пустой ответ")
        except Exception as e:
            self.test_fail("test_chat_with_ai_basic", str(e))

    async def test_add_task(self):
        """Тест функции add_task"""
        try:
            result = add_task(
                user_id=self.test_user_id,
                title="Тестовая задача",
                description="Описание тестовой задачи",
                reminder_time="2024-01-15 10:00:00"
            )
            if "успешно" in result.lower() or "добавлена" in result.lower():
                self.test_pass("test_add_task")
            else:
                self.test_fail("test_add_task", f"Неожиданный результат: {result}")
        except Exception as e:
            self.test_fail("test_add_task", str(e))

    async def test_list_tasks(self):
        """Тест функции list_tasks"""
        try:
            result = list_tasks(user_id=self.test_user_id)
            if isinstance(result, str) and len(result.strip()) > 0:
                self.test_pass("test_list_tasks")
            else:
                self.test_fail("test_list_tasks", f"Неожиданный результат: {result}")
        except Exception as e:
            self.test_fail("test_list_tasks", str(e))

    async def test_complete_task(self):
        """Тест функции complete_task"""
        try:
            # Сначала создаем задачу
            add_result = add_task(
                user_id=self.test_user_id,
                title="Задача для завершения",
                description="Тест",
                reminder_time="2024-01-15 10:00:00"
            )

            # Извлекаем ID из результата
            import re
            id_match = re.search(r'ID:\s*(\d+)', add_result)
            if id_match:
                task_id = int(id_match.group(1))
                result = complete_task(user_id=self.test_user_id, task_title="Задача для завершения")
                if "завершена" in result.lower() or "выполнена" in result.lower():
                    self.test_pass("test_complete_task")
                else:
                    self.test_fail("test_complete_task", f"Неожиданный результат: {result}")
            else:
                self.test_fail("test_complete_task", "Не удалось извлечь ID задачи")
        except Exception as e:
            self.test_fail("test_complete_task", str(e))

    async def test_delete_task(self):
        """Тест функции delete_task"""
        try:
            # Создаем задачу для удаления
            add_result = add_task(
                user_id=self.test_user_id,
                title="Задача для удаления",
                description="Тест",
                reminder_time="2024-01-15 10:00:00"
            )

            import re
            id_match = re.search(r'ID:\s*(\d+)', add_result)
            if id_match:
                task_id = int(id_match.group(1))
                result = delete_task(user_id=self.test_user_id, task_title="Задача для удаления")
                if "удалена" in result.lower() or "удалил" in result.lower():
                    self.test_pass("test_delete_task")
                else:
                    self.test_fail("test_delete_task", f"Неожиданный результат: {result}")
            else:
                self.test_fail("test_delete_task", "Не удалось создать задачу")
        except Exception as e:
            self.test_fail("test_delete_task", str(e))

    async def test_edit_task(self):
        """Тест функции edit_task"""
        try:
            # Создаем задачу
            add_result = add_task(
                user_id=self.test_user_id,
                title="Задача для редактирования",
                description="Тест",
                reminder_time="2024-01-15 10:00:00"
            )

            import re
            id_match = re.search(r'ID:\s*(\d+)', add_result)
            if id_match:
                task_id = int(id_match.group(1))
                result = edit_task(
                    user_id=self.test_user_id,
                    task_id=task_id,
                    title="Отредактированная задача"
                )
                if "изменена" in result.lower() or "обновлена" in result.lower():
                    self.test_pass("test_edit_task")
                else:
                    self.test_fail("test_edit_task", f"Неожиданный результат: {result}")
            else:
                self.test_fail("test_edit_task", "Не удалось создать задачу")
        except Exception as e:
            self.test_fail("test_edit_task", str(e))

    async def test_set_priority(self):
        """Тест функции set_priority"""
        try:
            # Создаем задачу
            add_result = add_task(
                user_id=self.test_user_id,
                title="Задача с приоритетом",
                description="Тест",
                reminder_time="2024-01-15 10:00:00"
            )

            import re
            id_match = re.search(r'ID:\s*(\d+)', add_result)
            if id_match:
                task_id = int(id_match.group(1))
                result = set_priority(task_id=task_id, priority="высокий", user_id=self.test_user_id)
                if "приоритет" in result.lower():
                    self.test_pass("test_set_priority")
                else:
                    self.test_fail("test_set_priority", f"Неожиданный результат: {result}")
            else:
                self.test_fail("test_set_priority", "Не удалось создать задачу")
        except Exception as e:
            self.test_fail("test_set_priority", str(e))

    async def test_update_profile(self):
        """Тест функции update_profile"""
        try:
            result = update_profile(
                user_id=self.test_user_id,
                city="Москва",
                company="Тестовая компания",
                position="Тестировщик"
            )
            if "обновлен" in result.lower() or "профиль" in result.lower():
                self.test_pass("test_update_profile")
            else:
                self.test_fail("test_update_profile", f"Неожиданный результат: {result}")
        except Exception as e:
            self.test_fail("test_update_profile", str(e))

    async def test_update_user_memory(self):
        """Тест функции update_user_memory"""
        try:
            result = update_user_memory(
                info="Тестовая информация о пользователе",
                user_id=self.test_user_id
            )
            if "сохранена" in result.lower() or "информация" in result.lower():
                self.test_pass("test_update_user_memory")
            else:
                self.test_fail("test_update_user_memory", f"Неожиданный результат: {result}")
        except Exception as e:
            self.test_fail("test_update_user_memory", str(e))

    async def test_find_partners(self):
        """Тест функции find_partners"""
        try:
            result = find_partners(user_id=self.test_user_id)
            if isinstance(result, str):
                self.test_pass("test_find_partners")
            else:
                self.test_fail("test_find_partners", f"Неожиданный результат: {result}")
        except Exception as e:
            self.test_fail("test_find_partners", str(e))

    async def test_delegate_task(self):
        """Тест функции delegate_task"""
        try:
            result = delegate_task(
                title="Задача для делегирования",
                description="Тест",
                reminder_time="2024-01-15 10:00:00",
                delegated_to_username="test_delegate",
                user_id=self.test_user_id
            )
            if "не найден" in result.lower() or "зарегистрирован" in result.lower():
                self.test_pass("test_delegate_task")
            else:
                self.test_fail("test_delegate_task", f"Неожиданный результат: {result}")
        except Exception as e:
            self.test_fail("test_delegate_task", str(e))

    async def test_parse_relative_time(self):
        """Тест функции parse_relative_time"""
        try:
            from datetime import datetime
            current_time = datetime.now()
            result = parse_relative_time("через 2 часа", current_time)
            if result and isinstance(result, datetime):
                self.test_pass("test_parse_relative_time")
            else:
                self.test_fail("test_parse_relative_time", f"Неожиданный результат: {result}")
        except Exception as e:
            self.test_fail("test_parse_relative_time", str(e))

    async def test_parse_absolute_time(self):
        """Тест функции parse_absolute_time"""
        try:
            result = parse_absolute_time("завтра в 15:00")
            if result and isinstance(result, str):
                self.test_pass("test_parse_absolute_time")
            else:
                self.test_fail("test_parse_absolute_time", f"Неожиданный результат: {result}")
        except Exception as e:
            self.test_fail("test_parse_absolute_time", str(e))

    async def test_encrypt_decrypt(self):
        """Тест функций шифрования/дешифрования"""
        try:
            test_data = "Тестовые данные для шифрования"
            encrypted = encrypt_data(test_data)
            decrypted = decrypt_data(encrypted)

            if decrypted == test_data:
                self.test_pass("test_encrypt_decrypt")
            else:
                self.test_fail("test_encrypt_decrypt", "Шифрование/дешифрование не работает")
        except Exception as e:
            self.test_fail("test_encrypt_decrypt", str(e))

    async def test_clean_content(self):
        """Тест функции clean_content"""
        try:
            test_content = "Тестовый контент с <b>HTML</b> и {json} данными"
            result = clean_content(test_content)
            if isinstance(result, str) and len(result) > 0:
                self.test_pass("test_clean_content")
            else:
                self.test_fail("test_clean_content", f"Неожиданный результат: {result}")
        except Exception as e:
            self.test_fail("test_clean_content", str(e))

    async def test_replace_placeholders(self):
        """Тест функции replace_placeholders"""
        try:
            from datetime import datetime
            import pytz
            user_now = datetime.now(pytz.UTC)
            result = replace_placeholders("Текущее время: {{current_time}}", user_now)
            if isinstance(result, str) and "{{" not in result:
                self.test_pass("test_replace_placeholders")
            else:
                self.test_fail("test_replace_placeholders", f"Неожиданный результат: {result}")
        except Exception as e:
            self.test_fail("test_replace_placeholders", str(e))

    async def test_clean_technical_details(self):
        """Тест функции clean_technical_details"""
        try:
            test_text = "list_tasks() результат: Задачи: 1. Купить молоко"
            result = clean_technical_details(test_text)
            if isinstance(result, str):
                self.test_pass("test_clean_technical_details")
            else:
                self.test_fail("test_clean_technical_details", f"Неожиданный результат: {result}")
        except Exception as e:
            self.test_fail("test_clean_technical_details", str(e))

    async def test_error_handling(self):
        """Тест обработки ошибок"""
        try:
            # Тест с несуществующим user_id
            result = list_tasks(user_id=999999999)
            if isinstance(result, str):
                self.test_pass("test_error_handling")
            else:
                self.test_fail("test_error_handling", f"Неожиданный результат: {result}")
        except Exception as e:
            self.test_fail("test_error_handling", str(e))

    async def test_force_tool_calls(self):
        """Тест функции force_tool_calls"""
        try:
            from ai_integration import force_tool_calls
            result = force_tool_calls(
                message="Напомни купить хлеб",
                content="",
                mentions_str="нет",
                user_id=self.test_user_id
            )
            if result is None or isinstance(result, list):
                self.test_pass("test_force_tool_calls")
            else:
                self.test_fail("test_force_tool_calls", f"Неожиданный результат: {result}")
        except Exception as e:
            self.test_fail("test_force_tool_calls", str(e))

    async def run_all_tests(self):
        """Запускает все тесты"""
        self.log("🚀 НАЧАЛО ПОЛНОГО ТЕСТИРОВАНИЯ АГЕНТА")
        self.log("=" * 50)

        # Настройка
        self.setup_database()

        # Тесты функций
        tests = [
            ("Базовый чат", self.test_chat_with_ai_basic),
            ("Добавление задачи", self.test_add_task),
            ("Просмотр задач", self.test_list_tasks),
            ("Завершение задачи", self.test_complete_task),
            ("Удаление задачи", self.test_delete_task),
            ("Редактирование задачи", self.test_edit_task),
            ("Установка приоритета", self.test_set_priority),
            ("Обновление профиля", self.test_update_profile),
            ("Обновление памяти", self.test_update_user_memory),
            ("Поиск партнеров", self.test_find_partners),
            ("Делегирование задачи", self.test_delegate_task),
            ("Парсинг относительного времени", self.test_parse_relative_time),
            ("Парсинг абсолютного времени", self.test_parse_absolute_time),
            ("Шифрование/дешифрование", self.test_encrypt_decrypt),
            ("Очистка контента", self.test_clean_content),
            ("Замена плейсхолдеров", self.test_replace_placeholders),
            ("Очистка технических деталей", self.test_clean_technical_details),
            ("Force tool calls", self.test_force_tool_calls),
            ("Обработка ошибок", self.test_error_handling),
        ]

        for test_name, test_func in tests:
            self.log(f"\n--- {test_name} ---")
            try:
                await test_func()
            except Exception as e:
                self.test_fail(test_name, f"Исключение: {str(e)}")
                traceback.print_exc()

        # Очистка
        self.cleanup_database()

        # Результаты
        self.log("\n" + "=" * 50)
        self.log("📊 РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ")
        self.log(f"✅ Пройдено: {self.passed}")
        self.log(f"❌ Провалено: {self.failed}")
        self.log(f"📈 Успешность: {(self.passed / (self.passed + self.failed) * 100):.1f}%")

        if self.errors:
            self.log("\n❌ ОШИБКИ:")
            for error in self.errors:
                self.log(f"  - {error}")

        return self.failed == 0

async def main():
    tester = AgentFunctionTester()
    success = await tester.run_all_tests()
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    asyncio.run(main())
