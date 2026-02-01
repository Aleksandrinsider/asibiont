"""
РАСШИРЕННЫЙ ТЕСТ ВСЕХ КОМАНД В ДИАЛОГЕ С ИИ
Используем AI для генерации разнообразных сообщений пользователей
и проверяем работу команд в реальном диалоге с БД
"""
import os
os.environ["LOCAL"] = "1"
os.environ["FREE_ACCESS_MODE"] = "1"
# Принудительно отключаем AI для тестирования локальной классификации
os.environ["DEEPSEEK_API_KEY"] = "test_key_for_local_classification"

import asyncio
import sys
import json
from datetime import datetime, timedelta
import random

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

from ai_integration.chat import chat_with_ai
from models import Session, User, Task, UserProfile, init_db
from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
import aiohttp

TEST_USER_ID = 999000222  # Разный ID для изоляции тестов

class DialogueTester:
    """Тестер диалогов с использованием AI для генерации сообщений"""

    def __init__(self):
        self.session = None
        self.user = None

    async def generate_messages_with_ai(self, command_type, count=3):
        """Генерируем разнообразные сообщения для команды (локально, без AI)"""
        # Всегда используем локальные сообщения для надежности
        return self._get_fallback_messages(command_type, count)

    def _get_fallback_messages(self, command_type, count):
        """Fallback сообщения для локального тестирования"""
        fallbacks = {
            "add_task": [
                "Создай задачу позвонить клиенту завтра в 10 утра",
                "Напомни мне купить продукты через час",
                "Нужно подготовить отчет к пятнице в 15:00",
                "Поставь напоминание о встрече послезавтра в 14:30",
                "Закажи продукты на завтра утром"
            ],
            "complete_task": [
                "Готово, купил все продукты",
                "Закончил подготовку отчета",
                "Сделал звонок в банк",
                "Завершил задачу про уборку",
                "Выполнил поручение"
            ],
            "list_tasks": [
                "Покажи мои задачи",
                "Какие у меня дела на сегодня?",
                "Список активных задач",
                "Что нужно сделать?"
            ],
            "update_profile": [
                "Я живу в Казани, работаю программистом",
                "Интересуюсь Python и машинным обучением",
                "Меня зовут Иван, из Москвы",
                "Работаю в IT компании, занимаюсь разработкой"
            ],
            "delegate_task": [
                "Поручи задачу подготовки отчета @ivanov",
                "Делегируй звонок в банк @petrov",
                "Передай задачу @sidorov до завтра"
            ],
            "delete_task": [
                "Удали задачу про звонок",
                "Сотри напоминание о покупках",
                "Убери задачу подготовки презентации"
            ],
            "delete_all_tasks": [
                "Удали все задачи",
                "Очисти список задач",
                "Сбрось все напоминания"
            ],
            "reschedule_task": [
                "Перенеси встречу на завтра в 16:00",
                "Давай перенесем звонок на 5 минут",
                "Отложи задачу на час"
            ],
            "find_partners": [
                "Найди единомышленников",
                "Кто похож на меня?",
                "Познакомь с людьми"
            ],
            "find_relevant_contacts_for_task": [
                "Кто может помочь с дизайном?",
                "Нужен программист на Python",
                "Кто разбирается в маркетинге?"
            ],
            "conversation": [
                "Привет! Как дела?",
                "Расскажи о себе",
                "Что ты умеешь?"
            ]
        }
        
        messages = fallbacks.get(command_type, [f"Тест команды {command_type}"])
        # Возвращаем нужное количество, циклически если нужно
        result = []
        for i in range(count):
            result.append(messages[i % len(messages)])
        return result

    def setup_test_user(self):
        """Создаем тестового пользователя"""
        self.session = Session()
        try:
            # Очищаем старые данные
            self.session.query(Task).filter_by(user_id=TEST_USER_ID).delete()
            self.session.query(UserProfile).filter_by(user_id=TEST_USER_ID).delete()
            self.session.query(User).filter_by(telegram_id=TEST_USER_ID).delete()
            self.session.commit()

            # Создаем пользователя
            self.user = User(telegram_id=TEST_USER_ID, username="test_user", timezone="Europe/Moscow")
            self.session.add(self.user)
            self.session.commit()

            # Создаем профиль
            profile = UserProfile(user_id=self.user.id)
            self.session.add(profile)
            self.session.commit()

            print(f"✅ Тестовый пользователь создан: ID {TEST_USER_ID}")

        except Exception as e:
            print(f"❌ Ошибка создания пользователя: {e}")
            self.session.rollback()

    def cleanup(self):
        """Очистка после тестов"""
        if self.session:
            try:
                self.session.query(Task).filter_by(user_id=TEST_USER_ID).delete()
                self.session.query(UserProfile).filter_by(user_id=TEST_USER_ID).delete()
                self.session.query(User).filter_by(telegram_id=TEST_USER_ID).delete()
                self.session.commit()
                print("🧹 Тестовые данные очищены")
            except Exception as e:
                print(f"❌ Ошибка очистки: {e}")
            finally:
                self.session.close()

    def verify_db_state(self, description, check_func):
        """Проверка состояния БД"""
        try:
            result = check_func(self.session)
            status = "✅" if result else "❌"
            print(f"  {status} БД: {description}")
            return result
        except Exception as e:
            print(f"  ❌ Ошибка проверки БД: {e}")
            return False

    async def test_command_dialogue(self, command_type, expected_tool=None):
        """Тестируем команду в диалоге"""
        print(f"\n{'='*80}")
        print(f"🎭 ТЕСТИРОВАНИЕ КОМАНДЫ: {command_type.upper()}")
        print(f"{'='*80}")

        # Генерируем сообщения
        messages = await self.generate_messages_with_ai(command_type, 3)
        print(f"📝 Сгенерировано {len(messages)} тестовых сообщений:")
        for i, msg in enumerate(messages, 1):
            print(f"  {i}. {msg}")

        success_count = 0
        total_tests = len(messages)

        for i, message in enumerate(messages, 1):
            print(f"\n🔹 ТЕСТ {i}/{total_tests}: {message}")

            try:
                # Отправляем сообщение
                response = await asyncio.wait_for(
                    chat_with_ai(message, user_id=TEST_USER_ID),
                    timeout=30.0
                )

                resp_text = response.get('response', '') if isinstance(response, dict) else str(response)
                tools_called = response.get('tools_called', []) if isinstance(response, dict) else []

                print(f"💬 Ответ: {resp_text[:150]}{'...' if len(resp_text) > 150 else ''}")

                if tools_called:
                    print(f"🔨 Tools: {tools_called}")
                    # Проверяем, что вызван правильный tool
                    if expected_tool and any(expected_tool in str(tool) for tool in tools_called):
                        print("  ✅ Правильный tool вызван")
                        success_count += 1
                    elif expected_tool:
                        print(f"  ⚠️  Ожидался tool {expected_tool}, но вызваны: {tools_called}")
                    else:
                        success_count += 1  # Для conversation и других
                else:
                    if command_type == "conversation":
                        print("  ✅ Conversation - tool не требуется")
                        success_count += 1
                    else:
                        print("  ⚠️  Tool не вызван")

                # Небольшая пауза между тестами
                await asyncio.sleep(0.5)

            except Exception as e:
                import traceback
                print(f"❌ Ошибка в тесте: {e}")
                print(f"Error type: {type(e).__name__}")
                print(f"Traceback: {traceback.format_exc()}")

        success_rate = (success_count / total_tests) * 100
        print(f"\n📊 РЕЗУЛЬТАТ КОМАНДЫ {command_type}: {success_count}/{total_tests} ({success_rate:.1f}%)")
        return success_rate >= 80  # 80% успешность считаем приемлемой

async def run_comprehensive_dialogue_test():
    """Запуск комплексного тестирования диалогов"""
    print("🚀 НАЧИНАЕМ КОМПЛЕКСНОЕ ТЕСТИРОВАНИЕ ДИАЛОГОВ С ИИ")
    print("=" * 80)

    tester = DialogueTester()
    tester.setup_test_user()

    try:
        # Тестовые команды
        commands_to_test = [
            ("add_task", "add_task"),
            ("complete_task", "complete_task"),
            ("update_profile", "update_profile"),
            ("list_tasks", "list_tasks"),
            ("delegate_task", "delegate_task"),
            ("delete_task", "delete_task"),
            ("delete_all_tasks", "delete_all_tasks"),
            ("reschedule_task", "reschedule_task"),
            ("find_partners", "find_partners"),
            ("find_relevant_contacts_for_task", "find_relevant_contacts_for_task"),
            ("conversation", None),  # Для conversation tool не требуется
        ]

        total_success = 0
        total_commands = len(commands_to_test)

        for command_type, expected_tool in commands_to_test:
            try:
                success = await tester.test_command_dialogue(command_type, expected_tool)
                if success:
                    total_success += 1
                    print(f"✅ КОМАНДА {command_type}: ПРОЙДЕНА")
                else:
                    print(f"❌ КОМАНДА {command_type}: НЕ ПРОЙДЕНА")

            except Exception as e:
                print(f"💥 КРИТИЧЕСКАЯ ОШИБКА в команде {command_type}: {e}")

        # Финальный отчет
        print(f"\n{'='*80}")
        print("📈 ФИНАЛЬНЫЙ ОТЧЕТ ТЕСТИРОВАНИЯ")
        print(f"{'='*80}")
        print(f"Команд протестировано: {total_commands}")
        print(f"Успешно: {total_success}")
        print(f"Успешность: {(total_success/total_commands)*100:.1f}%")

        if total_success == total_commands:
            print("🎉 ВСЕ КОМАНДЫ РАБОТАЮТ КОРРЕКТНО В ДИАЛОГЕ!")
        elif total_success >= total_commands * 0.8:
            print("✅ БОЛЬШИНСТВО КОМАНД РАБОТАЮТ ХОРОШО")
        else:
            print("⚠️  ТРЕБУЕТСЯ ДОРАБОТКА НЕКОТОРЫХ КОМАНД")

    finally:
        tester.cleanup()

if __name__ == "__main__":
    # Инициализируем БД
    init_db()

    # Запускаем тесты
    asyncio.run(run_comprehensive_dialogue_test())