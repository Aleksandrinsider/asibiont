import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import aiohttp
import json
from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
from ai_integration.tools import TOOLS
from ai_integration.handlers import *  # Импортируем все функции-обработчики

class UniversalAgent:
    """Универсальный агент, который использует tool calling для выполнения любых запросов"""

    def __init__(self):
        self.tools = TOOLS
        self.functions = {
            'add_task': add_task,
            'complete_task': complete_task,
            'list_tasks': list_tasks,
            'delete_task': delete_task,
            'reschedule_task': reschedule_task,
            'update_profile': update_profile,
            'show_profile': show_profile,
            'find_partners': find_partners,
            'find_relevant_contacts_for_task': find_relevant_contacts_for_task,
            'delegate_task': delegate_task,
            'get_task_details': get_task_details,
            'analyze_tasks': analyze_tasks,
            'analyze_goal_progress': analyze_goal_progress,
            'update_user_memory': update_user_memory_async,
            'cancel_subscription': cancel_subscription,
            'create_subscription_payment': create_subscription_payment,
            'check_subscription_status': check_subscription_status,
        }

    async def call_ai(self, messages, tools=None):
        """Вызов AI с tool calling"""
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }

        data = {
            "model": DEEPSEEK_MODEL,
            "messages": messages,
            "tools": tools or self.tools,
            "tool_choice": "auto",
            "temperature": 0.7,
            "max_tokens": 2000
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    raise Exception(f"AI call failed: {response.status} {await response.text()}")

    async def execute_tool_call(self, tool_call, user_id):
        """Выполнение tool call"""
        function_name = tool_call['function']['name']
        arguments = json.loads(tool_call['function']['arguments'])

        # Добавляем user_id к аргументам, если его нет
        if 'user_id' not in arguments:
            arguments['user_id'] = user_id

        func = self.functions.get(function_name)
        if not func:
            raise Exception(f"Function {function_name} not found")

        # Выполняем функцию
        if asyncio.iscoroutinefunction(func):
            result = await func(**arguments)
        else:
            result = func(**arguments)

        return result

    async def process_request(self, user_message, user_id, context=None):
        """Обработка запроса пользователя"""
        system_prompt = """Ты - ASI Biont, универсальный AI-агент для управления задачами.

ПРАВИЛА:
1. Анализируй запрос пользователя и определяй необходимые действия
2. Используй tool calls для выполнения конкретных операций
3. Будь краток и полезен в ответах
4. Если нужно выполнить несколько действий - вызывай несколько tools
5. Отвечай на русском языке

Доступные инструменты позволяют:
- Создавать и управлять задачами
- Искать контакты и партнеров
- Обновлять профиль пользователя
- Анализировать прогресс по целям
- Управлять подпиской

Если запрос не требует конкретных действий - просто ответь в разговорном стиле."""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]

        # Если есть контекст, добавляем его
        if context:
            messages.insert(1, {"role": "system", "content": f"Контекст: {context}"})

        # Вызываем AI
        response = await self.call_ai(messages)

        message = response['choices'][0]['message']

        # Проверяем, есть ли tool calls
        if 'tool_calls' in message and message['tool_calls']:
            tool_results = []
            for tool_call in message['tool_calls']:
                try:
                    result = await self.execute_tool_call(tool_call, user_id)
                    tool_results.append(f"Выполнено: {tool_call['function']['name']} - {result}")
                except Exception as e:
                    tool_results.append(f"Ошибка в {tool_call['function']['name']}: {str(e)}")

            # Формируем итоговый ответ
            final_response = "\n".join(tool_results)

            # Если AI дал дополнительный контент
            if message.get('content'):
                final_response = message['content'] + "\n\n" + final_response

            return final_response
        else:
            # Просто текстовый ответ
            return message.get('content', 'Извините, не удалось обработать запрос')

async def demo_agent():
    """Демо-функция для тестирования агента"""
    agent = UniversalAgent()

    # Пример запросов
    test_requests = [
        "Создай задачу: позвонить маме завтра в 10 утра",
        "Покажи мои задачи",
        "Найди партнеров для бега",
        "Обнови мой профиль: люблю программирование",
        "Создай задачу на пробежку и найди партнеров для этого",
        "Что ты умеешь делать?"
    ]

    user_id = 123456789  # Тестовый user_id

    for request in test_requests:
        print(f"\n{'='*50}")
        print(f"Запрос: {request}")
        print(f"{'='*50}")
        try:
            response = await agent.process_request(request, user_id)
            print(f"Ответ: {response}")
        except Exception as e:
            print(f"Ошибка: {e}")
        print()

if __name__ == "__main__":
    asyncio.run(demo_agent())