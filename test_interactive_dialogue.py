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

TEST_USER_ID = 999000333  # Уникальный ID для диалогового теста

class InteractiveDialogueTester:
    """Тестер интерактивного диалога с ИИ"""

    def __init__(self):
        self.session = None
        self.user = None
        self.conversation_history = []

    async def setup(self):
        """Настройка тестовой среды"""
        self.session = Session()

        # Создаем тестового пользователя
        self.user = self.session.query(User).filter_by(telegram_id=TEST_USER_ID).first()
        if not self.user:
            self.user = User(
                telegram_id=TEST_USER_ID,
                username="test_user_dialogue",
                first_name="Test",
                last_name="Dialogue",
                referral_balance=0
            )
            self.session.add(self.user)
            self.session.commit()

        # Создаем профиль
        profile = self.session.query(UserProfile).filter_by(user_id=self.user.id).first()
        if not profile:
            profile = UserProfile(
                user_id=self.user.id,
                city="Москва",
                interests="технологии, ИИ, бизнес",
                skills="программирование, управление",
                goals="развитие бизнеса, поиск партнеров"
            )
            self.session.add(profile)
            self.session.commit()

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

async def run_interactive_dialogue_test(max_turns=8):
    """Запуск интерактивного диалогового теста"""
    tester = InteractiveDialogueTester()
    await tester.run_interactive_dialogue(max_turns)

if __name__ == "__main__":
    # Инициализируем БД
    init_db()

    # Запускаем интерактивный диалог
    asyncio.run(run_interactive_dialogue_test())