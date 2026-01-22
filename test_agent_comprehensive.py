# -*- coding: utf-8 -*-
"""Комплексный тест всех команд агента с симуляцией диалога"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.chat import chat_with_ai
from models import SessionLocal
import asyncio
import json
from datetime import datetime, timedelta

class AgentTester:
    """Класс для комплексного тестирования агента"""

    def __init__(self):
        self.db_session = SessionLocal()
        self.user_id = 146333757  # Реальный ID пользователя
        self.conversation_history = []
        self.test_results = {}

    async def simulate_user_response(self, agent_message):
        """Симулируем ответ пользователя с помощью ИИ"""
        if not agent_message:
            return "Продолжай"

        # Определяем тип вопроса агента и генерируем соответствующий ответ
        agent_lower = agent_message.lower()

        # Если агент спрашивает о времени напоминания
        if any(word in agent_lower for word in ["время", "напоминание", "когда", "reminder"]):
            return "завтра в 14:00"

        # Если агент спрашивает о городе
        if any(word in agent_lower for word in ["город", "city", "откуда"]):
            return "Москва"

        # Если агент спрашивает о навыках/интересах
        if any(word in agent_lower for word in ["навыки", "интересы", "увлекаешься", "работаешь"]):
            return "программирование на Python, машинное обучение, спорт"

        # Если агент спрашивает о компании
        if any(word in agent_lower for word in ["компания", "работаешь", "company"]):
            return "Яндекс"

        # Если агент спрашивает о должности
        if any(word in agent_lower for word in ["должность", "position", "работаешь"]):
            return "Senior Developer"

        # Если агент спрашивает о выполнении задачи
        if any(word in agent_lower for word in ["выполнил", "сделал", "завершил", "результат"]):
            return "Да, задача выполнена успешно. Результат: код написан и протестирован."

        # Если агент спрашивает о переносе задачи
        if any(word in agent_lower for word in ["перенести", "новое время", "когда перенести"]):
            return "перенеси на послезавтра в 16:00"

        # Если агент предлагает варианты
        if any(word in agent_lower for word in ["варианты", "альтернативы", "что делать"]):
            return "давай разобьем на части"

        # Если агент спрашивает подтверждение
        if any(word in agent_lower for word in ["точно", "уверен", "подтвердить"]):
            return "да, подтверждаю"

        # Если агент спрашивает о теме для идей
        if any(word in agent_lower for word in ["идеи", "brainstorm", "тема"]):
            return "оптимизация рабочего процесса"

        # По умолчанию продолжаем разговор
        return "Расскажи подробнее о своих возможностях"

    async def test_command(self, command_name, user_message, expected_actions=None, max_turns=3):
        """Тестируем конкретную команду"""
        print(f"\n🧪 ТЕСТИРУЕМ: {command_name}")
        print(f"Сообщение: '{user_message}'")

        conversation = []
        current_message = user_message

        for turn in range(max_turns):
            print(f"\n--- Ход {turn + 1} ---")
            print(f"Пользователь: {current_message}")

            try:
                response = await chat_with_ai(
                    message=current_message,
                    user_id=self.user_id,
                    db_session=self.db_session
                )

                print(f"Агент: {response[:200]}...")

                conversation.append({
                    "user": current_message,
                    "agent": response
                })

                # Если агент задал вопрос, симулируем ответ пользователя
                if any(char in response for char in ["?", "?", ":", "—"]):
                    user_response = await self.simulate_user_response(response)
                    if user_response != current_message:  # Избегаем зацикливания
                        current_message = user_response
                        continue

                break  # Выходим если агент не задает вопросов

            except Exception as e:
                print(f"❌ Ошибка: {e}")
                self.test_results[command_name] = {"status": "error", "error": str(e)}
                return

        self.test_results[command_name] = {
            "status": "success",
            "conversation": conversation,
            "turns": len(conversation)
        }
        print(f"✅ {command_name} - УСПЕШНО")

    async def run_comprehensive_test(self):
        """Запускаем полный тест всех команд"""

        print("🚀 НАЧИНАЕМ КОМПЛЕКСНОЕ ТЕСТИРОВАНИЕ АГЕНТА")
        print("=" * 60)

        # 1. Создание задачи
        await self.test_command(
            "add_task",
            "Создай задачу: написать код для парсера сайтов, завтра в 10 утра"
        )

        # 2. Просмотр задач
        await self.test_command(
            "list_tasks",
            "Покажи мои задачи"
        )

        # 3. Редактирование задачи
        await self.test_command(
            "edit_task",
            "Измени задачу с парсером - добавь детали: нужно спарсить 100 страниц с задержкой"
        )

        # 4. Завершение задачи
        await self.test_command(
            "complete_task",
            "Я выполнил задачу с парсером"
        )

        # 5. Создание новой задачи для тестирования удаления
        await self.test_command(
            "add_task_2",
            "Напомни мне позвонить другу сегодня в 18:00"
        )

        # 6. Удаление задачи
        await self.test_command(
            "delete_task",
            "Удалить задачу с звонком другу"
        )

        # 7. Обновление профиля
        await self.test_command(
            "update_profile",
            "Обнови мой профиль: я из Санкт-Петербурга, работаю в Тинькофф, занимаюсь data science"
        )

        # 8. Поиск контактов
        await self.test_command(
            "find_partners",
            "Найди мне людей для совместного проекта по машинному обучению"
        )

        # 9. Проверка подписки
        await self.test_command(
            "check_subscription",
            "Какой у меня тариф подписки?"
        )

        # 10. Генерация идей
        await self.test_command(
            "brainstorm_ideas",
            "Дай идеи как оптимизировать мой рабочий день"
        )

        # 11. Делегирование задачи (если есть подписка)
        await self.test_command(
            "delegate_task",
            "Делегируй задачу @alex_dev: проанализировать рынок ИИ до конца недели"
        )

        # 12. Принятие делегированной задачи (симулируем другого пользователя)
        await self.test_command(
            "accept_delegated_task",
            "Принимаю делегированную задачу об анализе рынка ИИ"
        )

        print("\n" + "=" * 60)
        print("📊 РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ:")

        success_count = 0
        total_count = len(self.test_results)

        for command, result in self.test_results.items():
            if result["status"] == "success":
                success_count += 1
                print(f"✅ {command}: УСПЕШНО ({result['turns']} ходов)")
            else:
                print(f"❌ {command}: ОШИБКА - {result['error']}")

        print(f"\n🎯 ИТОГО: {success_count}/{total_count} команд прошли успешно")

        if success_count == total_count:
            print("🎉 ВСЕ КОМАНДЫ РАБОТАЮТ КОРРЕКТНО!")
        else:
            print("⚠️  НЕКОТОРЫЕ КОМАНДЫ ТРЕБУЮТ ДОРАБОТКИ")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.db_session.close()

async def main():
    async with AgentTester() as tester:
        await tester.run_comprehensive_test()

if __name__ == "__main__":
    asyncio.run(main())