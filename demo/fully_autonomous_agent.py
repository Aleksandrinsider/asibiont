import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import aiohttp
import json
from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL

class FullyAutonomousAgent:
    """Полностью автономный агент - сам генерирует весь код и логику"""

    def __init__(self):
        self.generated_code = {}  # Хранилище сгенерированного кода
        self.execution_history = []  # История выполнения
        self.db_schema = self._get_db_schema()  # Схема базы данных

    def _get_db_schema(self):
        """Получить реальную схему базы данных из существующих моделей"""
        try:
            # Импортируем модели для получения реальной схемы
            from models import User, Task, UserProfile, Subscription

            # Получаем схему из SQLAlchemy моделей
            schema = {}

            # User model
            user_columns = {}
            for column in User.__table__.columns:
                user_columns[column.name] = str(column.type)
            schema['users'] = user_columns

            # Task model
            task_columns = {}
            for column in Task.__table__.columns:
                task_columns[column.name] = str(column.type)
            schema['tasks'] = task_columns

            # UserProfile model
            profile_columns = {}
            for column in UserProfile.__table__.columns:
                profile_columns[column.name] = str(column.type)
            schema['user_profiles'] = profile_columns

            return schema

        except Exception as e:
            print(f"Не удалось получить схему из моделей: {e}")
            # Fallback на упрощенную схему
            return {
                'users': {'id': 'INTEGER', 'telegram_id': 'INTEGER', 'username': 'VARCHAR'},
                'tasks': {'id': 'INTEGER', 'user_id': 'INTEGER', 'title': 'VARCHAR', 'description': 'TEXT', 'due_date': 'DATETIME'},
                'user_profiles': {'user_id': 'INTEGER', 'total_tasks': 'INTEGER'}
            }

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

    async def analyze_and_generate_code(self, user_message, user_id):
        """AI анализирует запрос и генерирует весь необходимый код"""

        schema_info = f"""
        СХЕМА БАЗЫ ДАННЫХ:
        {json.dumps(self.db_schema, indent=2, ensure_ascii=False)}

        ДОСТУПНЫЕ МОДУЛИ:
        - sqlite3 (для работы с SQLite)
        - datetime, json, asyncio
        - aiohttp (для API вызовов)
        """

        system_prompt = f"""Ты - ПРОДВИНУТЫЙ АВТОНОМНЫЙ AI-АГЕНТ для управления задачами.

{schema_info}

ТВОЯ ЗАДАЧА: Проанализировать запрос пользователя и сгенерировать ВЕСЬ необходимый код для его выполнения.

ВЕРНИ JSON в ТОЧНО таком формате:
{{
    "analysis": "подробный анализ запроса",
    "actions": [
        {{
            "type": "sql_query",
            "description": "что делает запрос",
            "query": "SQL запрос",
            "params": ["параметры"]
        }},
        {{
            "type": "python_code",
            "description": "что делает код",
            "code": "Python код для выполнения",
            "imports": ["импорты"]
        }},
        {{
            "type": "api_call",
            "description": "что делает API вызов",
            "url": "endpoint",
            "method": "GET/POST",
            "data": {{}}
        }},
        {{
            "type": "direct_response",
            "response": "естественный ответ пользователю"
        }}
    ],
    "response_template": "шаблон ответа с {{placeholders}}"
}}

СТИЛЬ ОТВЕТОВ:
1. БУДЬ КОНВЕРСАЦИОННЫМ: используй "я", "ты", дружелюбный тон
2. ДАВАЙ ДЕТАЛЬНЫЕ ОТВЕТЫ: конкретное время, полезная информация
3. ДОБАВЛЯЙ КОНТЕКСТ: сколько задач, что дальше, советы
4. БУДЬ ПОМОЩНИКОМ: предлагай альтернативы, давай рекомендации

ПРАВИЛА:
1. Используй ТОЛЬКО предоставленную схему БД
2. Генерируй самодостаточный код
3. Не используй внешние функции или модули проекта
4. Для работы с БД генерируй стандартные SQL запросы (SELECT, INSERT, UPDATE, DELETE)
5. Используй ? для плейсхолдеров в SQL запросах
6. Параметры передавай как простой массив значений: ["value1", "value2", 123]
7. Все действия должны быть выполнимы автономно
8. Для INSERT: INSERT INTO table (col1, col2) VALUES (?, ?)
9. Для WHERE условий: WHERE col = ?"""

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
            # Попытка извлечь JSON
            import re
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                try:
                    return json.loads(json_match.group())
                except:
                    pass
            # Fallback
            return {
                "analysis": "Не удалось распознать запрос",
                "actions": [{
                    "type": "direct_response",
                    "response": "Извините, не удалось обработать запрос. Попробуйте переформулировать."
                }],
                "response_template": "Извините, не удалось обработать запрос. Попробуйте переформулировать."
            }

    async def execute_action(self, action, user_id):
        """Выполнение действия"""

        action_type = action.get('type')

        if action_type == 'sql_query':
            return await self.execute_sql_query(action, user_id)
        elif action_type == 'python_code':
            return await self.execute_python_code(action, user_id)
        elif action_type == 'api_call':
            return await self.execute_api_call(action)
        elif action_type == 'direct_response':
            return action.get('response', 'OK')
        else:
            return f"Неизвестный тип действия: {action_type}"

    async def execute_sql_query(self, action, user_id):
        """Выполнение SQL запроса через sqlite3"""

        query = action.get('query', '')
        params = action.get('params', [])

        try:
            # Используем sqlite3 напрямую, как указано в правилах
            import sqlite3
            from config import DATABASE_URL

            # Подключаемся к базе данных
            conn = sqlite3.connect(DATABASE_URL)
            cursor = conn.cursor()

            # Выполняем запрос
            if params:
                cursor.execute(query, tuple(params))
            else:
                cursor.execute(query)

            # Получаем результаты
            if query.strip().upper().startswith(('SELECT', 'SHOW')):
                # Для SELECT запросов
                rows = cursor.fetchall()
                column_names = [desc[0] for desc in cursor.description] if cursor.description else []
                if rows:
                    # Преобразуем в список словарей
                    formatted_results = [dict(zip(column_names, row)) for row in rows]
                    conn.close()
                    return formatted_results
                else:
                    conn.close()
                    return []
            else:
                # Для INSERT, UPDATE, DELETE - коммитим изменения
                conn.commit()
                conn.close()
                return f"Запрос выполнен успешно"

        except Exception as e:
            # Попытка закрыть соединение в случае ошибки
            try:
                conn.close()
            except:
                pass
            return f"Ошибка выполнения SQL: {str(e)}"

            # Получаем результаты
            if query.strip().upper().startswith(('SELECT', 'SHOW')):
                # Для SELECT запросов
                rows = result.fetchall()
                if rows:
                    # Преобразуем в список словарей
                    column_names = result.keys()
                    formatted_results = [dict(zip(column_names, row)) for row in rows]
                    session.close()
                    return formatted_results
                else:
                    session.close()
                    return []
            else:
                # Для INSERT, UPDATE, DELETE
                session.commit()
                session.close()
                return f"Запрос выполнен успешно"

        except Exception as e:
            # Попытка закрыть сессию в случае ошибки
            try:
                session.close()
            except:
                pass
            return f"Ошибка выполнения SQL: {str(e)}"

    async def execute_python_code(self, action, user_id):
        """Выполнение Python кода"""

        code = action.get('code', '')
        imports = action.get('imports', [])

        try:
            # Создаем пространство имен
            namespace = {
                'user_id': user_id,
                'asyncio': asyncio,
                'json': json,
                'datetime': None,
                'result': None
            }

            # Импортируем необходимые модули
            for imp in imports:
                try:
                    if imp == 'datetime':
                        import datetime
                        namespace['datetime'] = datetime
                    elif imp == 'json':
                        namespace['json'] = json
                    elif imp == 'asyncio':
                        namespace['asyncio'] = asyncio
                except ImportError:
                    pass

            # Выполняем код
            exec(code, namespace)
            result = namespace.get('result')

            return result if result is not None else "Код выполнен успешно"

        except Exception as e:
            return f"Ошибка выполнения кода: {str(e)}"

    async def execute_api_call(self, action):
        """Выполнение API вызова"""

        url = action.get('url', '')
        method = action.get('method', 'GET')
        data = action.get('data', {})

        try:
            async with aiohttp.ClientSession() as session:
                if method.upper() == 'POST':
                    async with session.post(url, json=data) as response:
                        return await response.json()
                else:
                    async with session.get(url) as response:
                        return await response.json()
        except Exception as e:
            return f"Ошибка API вызова: {str(e)}"

    async def process_request(self, user_message, user_id):
        """Основная функция обработки запроса"""

        # Анализируем и генерируем план
        plan = await self.analyze_and_generate_code(user_message, user_id)

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

async def demo_fully_autonomous_agent():
    """Демо полностью автономного агента"""

    agent = FullyAutonomousAgent()

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

        print(f"\n📊 История действий: {len(agent.execution_history)}")

if __name__ == "__main__":
    asyncio.run(demo_fully_autonomous_agent())
