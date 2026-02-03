"""
ИНТЕРАКТИВНЫЙ ДИАЛОГОВЫЙ ТЕСТ С ИИ
ИИ генерирует сообщения пользователя, система отвечает, создавая непрерывный диалог
"""
import os
os.environ["LOCAL"] = "1"
os.environ["FREE_ACCESS_MODE"] = "1"

import asyncio
import sys
import json
from datetime import datetime
import random

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

from ai_integration.chat import chat_with_ai
from models import Session, User, Task, UserProfile, init_db

TEST_USER_ID = 999000333999  # Уникальный ID для диалогового теста

class InteractiveDialogueTester:
    """Тестер интерактивного диалога с ИИ"""

    def __init__(self):
        self.session = None
        self.user = None
        self.conversation_history = []

    async def setup(self):
        """Настройка тестовой среды"""
        self.session = Session()

        print("🚀 НАЧИНАЕМ ИНТЕРАКТИВНЫЙ ДИАЛОГ С ИИ")
        print("=" * 80)

    def cleanup(self):
        """Очистка тестовой среды"""
        if self.session:
            # Удаляем тестовые задачи
            self.session.query(Task).filter_by(user_id=self.user.id).delete()
            self.session.commit()
            self.session.close()
        print("\n🧹 Тестовые данные очищены")

    async def generate_user_message(self, system_response, turn_number):
        """ИИ генерирует следующее сообщение пользователя на основе ответа системы"""
        if turn_number == 1:
            # Первое сообщение - простое приветствие
            return "Привет! Расскажи о себе и что ты умеешь."

        # Для последующих сообщений используем AI для генерации
        prompt = f"""
Ты - пользователь, общающийся с ИИ-ассистентом для управления задачами.
Предыдущий ответ ассистента: "{system_response}"

Сгенерируй следующее сообщение пользователя. Оно должно быть:
1. Естественным и conversational
2. Включать запрос на выполнение какой-то задачи (создание, редактирование, просмотр и т.д.)
3. Быть связанным с предыдущим контекстом, но не повторять одно и то же

Примеры возможных сообщений:
- "Создай задачу на завтра в 10 утра"
- "Покажи мои задачи"
- "Измени задачу про молоко на послезавтра"
- "Кто может помочь с дизайном?"
- "Напомни мне о встрече через час"

Сгенерируй одно сообщение пользователя:
"""

        try:
            # Используем DeepSeek для генерации сообщения пользователя
            import aiohttp

            headers = {
                "Authorization": f"Bearer {os.getenv('DEEPSEEK_API_KEY')}",
                "Content-Type": "application/json"
            }

            data = {
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 100,
                "temperature": 0.7
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.deepseek.com/v1/chat/completions",
                    headers=headers,
                    json=data,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        generated_message = result["choices"][0]["message"]["content"].strip()
                        # Очищаем от лишних символов
                        generated_message = generated_message.strip('"').strip("'")
                        return generated_message
                    else:
                        print(f"❌ Ошибка генерации сообщения: {response.status}")
                        return self._get_fallback_user_message(turn_number)

        except Exception as e:
            print(f"❌ Ошибка при генерации сообщения пользователя: {e}")
            return self._get_fallback_user_message(turn_number)

    def _get_fallback_user_message(self, turn_number):
        """Fallback сообщения пользователя"""
        messages = [
            "Привет! Что ты умеешь?",
            "Создай задачу купить продукты завтра в 10 утра",
            "Покажи мои задачи",
            "Измени эту задачу на послезавтра в 14:00",
            "Кто может помочь с программированием?",
            "Напомни мне позвонить в банк через час",
            "Готово, купил продукты",
            "Покажи детали задачи про продукты",
            "Спасибо за помощь!"
        ]
        return messages[min(turn_number - 1, len(messages) - 1)]

    async def generate_messages_with_ai(self, command_type, count):
        """Генерирует тестовые сообщения для типа команды"""
        return self.get_fallback_messages(command_type, count)

    async def run_interactive_dialogue(self, max_turns=10):
        """Запуск интерактивного диалога"""
        await self.setup()

        try:
            for turn in range(1, max_turns + 1):
                print(f"\n{'='*60}")
                print(f"🔄 ХОД {turn}/{max_turns}")
                print(f"{'='*60}")

                # Генерируем сообщение пользователя
                if turn == 1:
                    user_message = "Привет! Расскажи о себе и что ты умеешь."
                else:
                    # Используем предыдущий ответ системы для генерации
                    last_system_response = self.conversation_history[-1] if self.conversation_history else ""
                    user_message = await self.generate_user_message(last_system_response, turn)

                print(f"👤 ПОЛЬЗОВАТЕЛЬ: {user_message}")

                # Получаем ответ системы
                try:
                    system_response = await chat_with_ai(
                        message=user_message,
                        user_id=self.user.telegram_id,
                        username=self.user.username,
                        context=""
                    )

                    print(f"🤖 СИСТЕМА: {system_response}")

                    # Сохраняем в истории
                    self.conversation_history.append(system_response)

                    # Проверяем, не закончен ли диалог
                    if any(phrase in system_response.lower() for phrase in ["до свидания", "пока", "всего хорошего"]):
                        print("🏁 Диалог завершен системой")
                        break

                except Exception as e:
                    print(f"❌ Ошибка в ответе системы: {e}")
                    break

                # Небольшая пауза между ходами
                await asyncio.sleep(1)

            print(f"\n{'='*80}")
            print("📊 РЕЗУЛЬТАТЫ ИНТЕРАКТИВНОГО ДИАЛОГА")
            print(f"{'='*80}")
            print(f"Всего ходов: {len(self.conversation_history)}")
            print("Диалог завершен успешно!")

            # Анализ диалога
            self.analyze_dialogue()

        finally:
            self.cleanup()

    def analyze_dialogue(self):
        """Анализ проведенного диалога"""
        print("\n🔍 АНАЛИЗ ДИАЛОГА:")

        # Проверяем использование инструментов
        tool_usage = []
        for response in self.conversation_history:
            if "✅" in response or "⚠️" in response or "❌" in response:
                tool_usage.append("Инструмент использован")
            elif "💬" in response:
                tool_usage.append("Простой ответ")

        print(f"- Ходов с использованием инструментов: {tool_usage.count('Инструмент использован')}")
        print(f"- Ходов с простыми ответами: {tool_usage.count('Простой ответ')}")

        # Проверяем разнообразие команд
        commands_used = []
        for response in self.conversation_history:
            if "Добавлена задача" in response or "Записал" in response:
                commands_used.append("add_task")
            elif "список задач" in response.lower() or "задачи" in response.lower():
                commands_used.append("list_tasks")
            elif "обновлено" in response.lower() or "изменено" in response.lower():
                commands_used.append("update_profile")

        unique_commands = set(commands_used)
        print(f"- Уникальных команд использовано: {len(unique_commands)}")
        if unique_commands:
            print(f"- Команды: {', '.join(unique_commands)}")

        # Рекомендации по улучшению
        print("\n💡 РЕКОМЕНДАЦИИ ПО УЛУЧШЕНИЮ:")
        if len(unique_commands) < 3:
            print("- Добавить больше разнообразия в команды")
        if tool_usage.count('Инструмент использован') < len(self.conversation_history) * 0.5:
            print("- Увеличить использование инструментов в ответах")
        if len(self.conversation_history) < 5:
            print("- Диалог слишком короткий, добавить продолжение")
        print("- Добавить обработку ошибок и edge cases")
        print("- Улучшить контекстную память между ходами")

    def get_fallback_messages(self, command_type, count=1):
        """Получить тестовые сообщения для типа команды"""
        fallbacks = {
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
            ],
            "add_task": [
                "Создай задачу купить молоко завтра в 10 утра",
                "Напомни мне позвонить в банк через час",
                "Добавь задачу подготовить отчет на сегодня в 15:00"
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
        total_time = 0
        errors = []

        for i, message in enumerate(messages, 1):
            print(f"\n🔹 ТЕСТ {i}/{total_tests}: {message}")

            start_time = asyncio.get_event_loop().time()
            try:
                # Отправляем сообщение
                response = await asyncio.wait_for(
                    chat_with_ai(message, user_id=TEST_USER_ID),
                    timeout=60.0  # Увеличен до 60 секунд, чтобы соответствовать API таймауту
                )

                end_time = asyncio.get_event_loop().time()
                response_time = end_time - start_time
                total_time += response_time

                resp_text = response.get('response', '') if isinstance(response, dict) else str(response)
                tools_called = response.get('tools_called', []) if isinstance(response, dict) else []

                print(f"💬 Ответ: {resp_text[:150]}{'...' if len(resp_text) > 150 else ''}")
                print(f"⏱️  Время ответа: {response_time:.2f} сек")

                if tools_called:
                    print(f"🔨 Tools: {tools_called}")
                    # Проверяем, что вызван правильный tool
                    expected = expected_tool
                    if expected and any(expected in str(tool) for tool in tools_called):
                        print("  ✅ Правильный tool вызван")
                        success_count += 1
                    elif expected:
                        print(f"  ⚠️  Ожидался tool {expected}, но вызваны: {tools_called}")
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
                end_time = asyncio.get_event_loop().time()
                response_time = end_time - start_time
                total_time += response_time

                import traceback
                error_info = {
                    'message': message,
                    'error': str(e),
                    'error_type': type(e).__name__,
                    'response_time': response_time
                }
                errors.append(error_info)

                print(f"❌ Ошибка в тесте: {e}")
                print(f"⏱️  Время до ошибки: {response_time:.2f} сек")
                print(f"Error type: {type(e).__name__}")

        success_rate = (success_count / total_tests) * 100
        avg_time = total_time / total_tests if total_tests > 0 else 0

        print(f"\n📊 РЕЗУЛЬТАТ КОМАНДЫ {command_type}: {success_count}/{total_tests} ({success_rate:.1f}%)")
        print(f"⏱️  Среднее время ответа: {avg_time:.2f} сек")
        print(f"📈 Всего времени: {total_time:.2f} сек")

        if errors:
            print(f"❌ Ошибок: {len(errors)}")
            for error in errors:
                print(f"  • {error['message'][:50]}...: {error['error_type']} ({error['response_time']:.2f} сек)")

        return success_rate >= 80  # 80% успешность считаем приемлемой

async def run_comprehensive_dialogue_test():
    """Запуск комплексного тестирования диалогов"""
    print("🚀 НАЧИНАЕМ КОМПЛЕКСНОЕ ТЕСТИРОВАНИЕ ДИАЛОГОВ С ИИ")
    print("=" * 80)

    tester = InteractiveDialogueTester()
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
        total_execution_time = 0
        command_times = []
        total_errors = 0
        commands_with_errors = 0
        avg_response_time = 0

        for command_type, expected_tool in commands_to_test:
            command_start_time = asyncio.get_event_loop().time()
            try:
                success = await tester.test_command_dialogue(command_type, expected_tool)
                command_end_time = asyncio.get_event_loop().time()
                command_time = command_end_time - command_start_time
                command_times.append(command_time)
                total_execution_time += command_time

                if success:
                    total_success += 1
                    print(f"✅ КОМАНДА {command_type}: ПРОЙДЕНА ({command_time:.2f} сек)")
                else:
                    print(f"❌ КОМАНДА {command_type}: НЕ ПРОЙДЕНА ({command_time:.2f} сек)")

            except Exception as e:
                command_end_time = asyncio.get_event_loop().time()
                command_time = command_end_time - command_start_time
                command_times.append(command_time)
                total_execution_time += command_time
                total_errors += 1
                commands_with_errors += 1

                print(f"💥 КРИТИЧЕСКАЯ ОШИБКА в команде {command_type}: {e} ({command_time:.2f} сек)")

        # Вычисляем статистику
        max_command_times = max(command_times) if command_times else 0
        min_command_times = min(command_times) if command_times else 0
        avg_response_time = sum(command_times) / len(command_times) if command_times else 0

        # Финальный отчет
        print(f"\n{'='*80}")
        print("📈 ФИНАЛЬНЫЙ ОТЧЕТ ТЕСТИРОВАНИЯ")
        print(f"{'='*80}")
        print(f"Команд протестировано: {total_commands}")
        print(f"Успешно: {total_success}")
        print(f"Успешность: {(total_success/total_commands)*100:.1f}%")

        # Детальная статистика
        print(f"\n📊 ДЕТАЛЬНАЯ СТАТИСТИКА:")
        print(f"• Общее время выполнения: {total_execution_time:.2f} сек")
        print(f"• Среднее время на команду: {total_execution_time/total_commands:.2f} сек")
        print(f"• Максимальное время: {max_command_times:.2f} сек")
        print(f"• Минимальное время: {min_command_times:.2f} сек")

        if total_errors > 0:
            print(f"• Всего ошибок: {total_errors}")
            print(f"• Команд с ошибками: {commands_with_errors}")

        # Рекомендации
        print(f"\n💡 РЕКОМЕНДАЦИИ:")
        if (total_success/total_commands)*100 >= 95:
            print("• 🎉 Отличные результаты! Все команды работают стабильно")
        elif (total_success/total_commands)*100 >= 80:
            print("• ✅ Хорошие результаты, но есть место для улучшения")
        else:
            print("• ⚠️  Требуется доработка некоторых команд")

        if avg_response_time > 10:
            print("• 🐌 Высокое время ответа - рассмотрите оптимизацию API вызовов")
        if total_errors > 0:
            print("• 🔧 Есть ошибки - проверьте обработку исключений")

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

    # Запускаем комплексное тестирование диалогов
    asyncio.run(run_comprehensive_dialogue_test())