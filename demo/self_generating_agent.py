import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import aiohttp
import json
import importlib
from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL

class SelfGeneratingAgent:
    """Агент, который сам генерирует tool calls и код для их выполнения"""

    def __init__(self):
        self.generated_functions = {}  # Хранилище сгенерированных функций
        self.execution_history = []    # История выполнения

    async def call_ai(self, messages, **kwargs):
        """Универсальный вызов AI"""
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }

        data = {
            "model": DEEPSEEK_MODEL,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 3000,
            **kwargs
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    raise Exception(f"AI call failed: {response.status} {await response.text()}")

    async def analyze_request(self, user_message, user_id):
        """AI анализирует запрос и определяет план действий"""

        system_prompt = """Ты - ASI Biont, самообучающийся AI-агент для управления задачами.

ТВОЯ ЗАДАЧА: Проанализировать запрос пользователя и определить план действий.

ВЕРНИ JSON в ТОЧНО таком формате:
{
    "actions": [
        {
            "type": "function_call",
            "name": "имя_функции",
            "code": "Python код функции",
            "parameters": {"param": "value"}
        },
        {
            "type": "database_query",
            "query": "SQL запрос",
            "description": "что получает запрос"
        },
        {
            "type": "api_call",
            "url": "API endpoint",
            "method": "GET/POST",
            "data": {}
        },
        {
            "type": "direct_response",
            "response": "прямой текстовый ответ"
        }
    ],
    "response_template": "шаблон ответа пользователю с {placeholders}"
}

ТИПЫ ДЕЙСТВИЙ:
- function_call: генерировать и выполнять Python функцию
- database_query: выполнять SQL запрос
- api_call: делать HTTP запрос
- direct_response: просто ответить текстом

ГЕНЕРИРУЙ ТОЛЬКО НЕОБХОДИМЫЕ ДЕЙСТВИЯ!"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Запрос пользователя: {user_message}\nUser ID: {user_id}"}
        ]

        response = await self.call_ai(messages)
        content = response['choices'][0]['message']['content']

        try:
            plan = json.loads(content)
            return plan
        except json.JSONDecodeError:
            # Если AI не вернул JSON, попробуем извлечь его
            import re
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                try:
                    return json.loads(json_match.group())
                except:
                    pass
            # Fallback - создаем простой план
            return {
                "actions": [{
                    "type": "conversation",
                    "response": content
                }],
                "response_template": content
            }

    async def execute_action(self, action, user_id):
        """Выполнение одного действия"""

        action_type = action.get('type')

        if action_type == 'function_call':
            return await self.execute_function_call(action, user_id)
        elif action_type == 'database_query':
            return await self.execute_database_query(action)
        elif action_type == 'api_call':
            return await self.execute_api_call(action)
        elif action_type == 'direct_response':
            return action.get('response', 'OK')
        elif action_type == 'conversation':
            return action.get('response', 'OK')
        else:
            return f"Неизвестный тип действия: {action_type}"

    async def execute_function_call(self, action, user_id):
        """Генерация и выполнение функции"""

        func_name = action['name']
        code = action.get('code', '')
        parameters = action.get('parameters', {})

        # Добавляем user_id если не указан
        if 'user_id' not in parameters:
            parameters['user_id'] = user_id

        # Если функция уже сгенерирована, используем её
        if func_name in self.generated_functions:
            func = self.generated_functions[func_name]
        else:
            # Генерируем функцию из кода
            try:
                # Создаем пространство имен с импортами
                namespace = {
                    'Session': None,
                    'User': None,
                    'Task': None,
                    'json': json,
                    'datetime': None,
                    'asyncio': asyncio,
                    'user_id': user_id
                }

                # Импортируем необходимые модули
                try:
                    from models import Session, User, Task
                    namespace.update({'Session': Session, 'User': User, 'Task': Task})
                except:
                    pass

                try:
                    from datetime import datetime, timezone
                    namespace.update({'datetime': datetime, 'timezone': timezone})
                except:
                    pass

                # Выполняем код функции
                exec(code, namespace)
                func = namespace.get(func_name)

                if func:
                    self.generated_functions[func_name] = func
                else:
                    return f"Функция {func_name} не найдена в сгенерированном коде"

            except Exception as e:
                return f"Ошибка генерации функции {func_name}: {str(e)}"

        # Выполняем функцию
        try:
            if asyncio.iscoroutinefunction(func):
                result = await func(**parameters)
            else:
                result = func(**parameters)
            return result
        except Exception as e:
            return f"Ошибка выполнения {func_name}: {str(e)}"

    async def execute_database_query(self, action):
        """Выполнение SQL запроса"""
        query = action.get('query', '')

        try:
            from models import Session
            session = Session()
            result = session.execute(query)
            data = result.fetchall()
            session.close()
            return data
        except Exception as e:
            return f"Ошибка выполнения запроса: {str(e)}"

    async def execute_api_call(self, action):
        """Выполнение API вызова"""
        url = action.get('url', '')
        method = action.get('method', 'GET')
        data = action.get('data', {})

        try:
            async with aiohttp.ClientSession() as session:
                if method.upper() == 'GET':
                    async with session.get(url) as response:
                        return await response.json()
                elif method.upper() == 'POST':
                    async with session.post(url, json=data) as response:
                        return await response.json()
        except Exception as e:
            return f"Ошибка API вызова: {str(e)}"

    async def process_request(self, user_message, user_id):
        """Основная функция обработки запроса"""

        # Анализируем запрос
        plan = await self.analyze_request(user_message, user_id)

        # Выполняем действия
        results = []
        for action in plan.get('actions', []):
            result = await self.execute_action(action, user_id)
            results.append(result)

            # Сохраняем в истории
            self.execution_history.append({
                'action': action,
                'result': result,
                'timestamp': asyncio.get_event_loop().time()
            })

        # Формируем ответ
        response_template = plan.get('response_template', 'Выполнено: {results}')

        # Заменяем плейсхолдеры
        response = response_template
        if '{results}' in response:
            response = response.replace('{results}', '\n'.join(str(r) for r in results))

        return response

async def demo_self_generating_agent():
    """Демо полностью самообучающегося агента"""

    agent = SelfGeneratingAgent()

    test_requests = [
        "Создай задачу 'позвонить другу' на завтра в 15:00",
        "Покажи все мои задачи",
        "Найди пользователей с похожими интересами",
        "Создай задачу и сразу покажи список задач",
        "Расскажи о себе"
    ]

    user_id = 123456789

    for request in test_requests:
        print(f"\n{'='*60}")
        print(f"ЗАПРОС: {request}")
        print(f"{'='*60}")

        try:
            response = await agent.process_request(request, user_id)
            print(f"ОТВЕТ: {response}")
        except Exception as e:
            print(f"ОШИБКА: {e}")
            import traceback
            traceback.print_exc()

        print(f"\nСгенерированные функции: {list(agent.generated_functions.keys())}")
        print(f"История выполнения: {len(agent.execution_history)} действий")

if __name__ == "__main__":
    asyncio.run(demo_self_generating_agent())