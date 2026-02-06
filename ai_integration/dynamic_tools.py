"""
Динамическая система обнаружения и адаптации инструментов

Вместо статического массива TOOLS реализует гибкий подход:
- Автоматическое обнаружение доступных функций
- AI-генерация описаний на основе docstrings
- Обучение на успешных вызовах
- Адаптация под предпочтения пользователя
"""

import logging
import inspect
import json
from typing import Dict, List, Optional, Any
from datetime import datetime
import asyncio
import aiohttp

logger = logging.getLogger(__name__)


class DynamicToolDiscovery:
    """
    Класс для динамического обнаружения и адаптации инструментов
    """
    
    def __init__(self):
        self.discovered_tools = {}
        self.tool_usage_stats = {}  # Статистика использования инструментов
        self.user_preferences = {}  # Предпочтения пользователей
        self.successful_patterns = []  # Успешные паттерны использования
        
    def discover_tools_from_module(self, module) -> Dict[str, Any]:
        """
        Автоматически обнаруживает все функции из модуля
        
        Args:
            module: Модуль для сканирования
            
        Returns:
            Словарь с обнаруженными инструментами
        """
        tools = {}
        
        for name, obj in inspect.getmembers(module):
            # Пропускаем приватные функции и не-функции
            if name.startswith('_') or not inspect.isfunction(obj):
                continue
                
            # Получаем сигнатуру функции
            try:
                signature = inspect.signature(obj)
                parameters = {}
                required_params = []
                
                for param_name, param in signature.parameters.items():
                    # Пропускаем self и служебные параметры
                    if param_name in ['self', 'cls', 'session']:
                        continue
                    
                    param_info = {
                        "type": self._get_param_type(param),
                        "description": self._extract_param_description(obj, param_name)
                    }
                    
                    # Определяем обязательные параметры
                    if param.default == inspect.Parameter.empty:
                        required_params.append(param_name)
                    else:
                        param_info["default"] = param.default
                    
                    parameters[param_name] = param_info
                
                # Извлекаем docstring
                docstring = inspect.getdoc(obj) or f"Function {name}"
                
                tools[name] = {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": self._enhance_description(docstring, name),
                        "parameters": {
                            "type": "object",
                            "properties": parameters,
                            "required": required_params
                        }
                    },
                    "usage_count": 0,
                    "success_rate": 1.0,
                    "last_used": None
                }
                
                logger.info(f"[TOOL DISCOVERY] Discovered function: {name}")
                
            except Exception as e:
                logger.warning(f"[TOOL DISCOVERY] Failed to process function {name}: {e}")
                continue
        
        self.discovered_tools.update(tools)
        return tools
    
    def _get_param_type(self, param) -> str:
        """Определяет тип параметра"""
        if param.annotation != inspect.Parameter.empty:
            annotation = str(param.annotation)
            if 'str' in annotation:
                return "string"
            elif 'int' in annotation:
                return "integer"
            elif 'bool' in annotation:
                return "boolean"
            elif 'float' in annotation:
                return "number"
            elif 'list' in annotation or 'List' in annotation:
                return "array"
            elif 'dict' in annotation or 'Dict' in annotation:
                return "object"
        return "string"  # По умолчанию
    
    def _extract_param_description(self, func, param_name: str) -> str:
        """Извлекает описание параметра из docstring"""
        docstring = inspect.getdoc(func)
        if not docstring:
            return f"Parameter {param_name}"
        
        # Ищем описание параметра в docstring
        lines = docstring.split('\n')
        for i, line in enumerate(lines):
            if param_name in line and ':' in line:
                # Берем описание после двоеточия
                parts = line.split(':', 1)
                if len(parts) > 1:
                    return parts[1].strip()
        
        return f"Parameter {param_name}"
    
    def _enhance_description(self, docstring: str, func_name: str) -> str:
        """
        Улучшает описание функции, делая его более понятным для AI
        """
        # Базовое улучшение - добавляем контекст
        if len(docstring) < 50:
            # Краткое описание - добавляем контекст из имени
            words = func_name.split('_')
            action = words[0] if words else "handle"
            context = ' '.join(words[1:]) if len(words) > 1 else "operation"
            return f"{docstring}. Action: {action}, Context: {context}"
        
        return docstring
    
    async def generate_ai_description(self, func_name: str, docstring: str, 
                                     parameters: Dict) -> str:
        """
        Генерирует улучшенное описание функции с помощью AI
        
        Args:
            func_name: Имя функции
            docstring: Оригинальный docstring
            parameters: Параметры функции
            
        Returns:
            Улучшенное описание
        """
        try:
            from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
            
            prompt = f"""Создай краткое и четкое описание функции для AI-ассистента.

Функция: {func_name}
Оригинальное описание: {docstring}
Параметры: {json.dumps(parameters, ensure_ascii=False, indent=2)}

Требования к описанию:
1. Максимум 2-3 предложения
2. Четко указать КОГДА использовать функцию
3. Указать ключевые слова из запросов пользователя
4. Быть на русском языке
5. Использовать эмодзи для наглядности (1-2 максимум)

Пример хорошего описания:
"⚠️ ТОЛЬКО ДЛЯ НОВЫХ ЗАДАЧ! Создает новую задачу с напоминанием. Ключевые слова: 'создай', 'напомни', 'добавь задачу'."

Верни только описание, без пояснений."""

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.deepseek.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": DEEPSEEK_MODEL,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.7,
                        "max_tokens": 200
                    },
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        description = data['choices'][0]['message']['content'].strip()
                        logger.info(f"[TOOL AI] Generated description for {func_name}")
                        return description
                    else:
                        logger.warning(f"[TOOL AI] Failed to generate description: {response.status}")
                        return docstring
                        
        except Exception as e:
            logger.error(f"[TOOL AI] Error generating AI description: {e}")
            return docstring
    
    def learn_from_success(self, func_name: str, user_id: int, 
                          context: str, result: Any):
        """
        Обучается на успешном вызове функции
        
        Args:
            func_name: Имя функции
            user_id: ID пользователя
            context: Контекст запроса
            result: Результат выполнения
        """
        # Обновляем статистику использования
        if func_name not in self.tool_usage_stats:
            self.tool_usage_stats[func_name] = {
                "total_calls": 0,
                "successful_calls": 0,
                "failed_calls": 0,
                "common_contexts": []
            }
        
        stats = self.tool_usage_stats[func_name]
        stats["total_calls"] += 1
        stats["successful_calls"] += 1
        
        # Сохраняем контекст для обучения
        if len(stats["common_contexts"]) < 10:
            stats["common_contexts"].append(context[:100])  # Первые 100 символов
        
        # Обновляем предпочтения пользователя
        if user_id not in self.user_preferences:
            self.user_preferences[user_id] = {}
        
        if func_name not in self.user_preferences[user_id]:
            self.user_preferences[user_id][func_name] = {
                "usage_count": 0,
                "last_used": None
            }
        
        self.user_preferences[user_id][func_name]["usage_count"] += 1
        self.user_preferences[user_id][func_name]["last_used"] = datetime.now().isoformat()
        
        # Сохраняем успешный паттерн
        pattern = {
            "func_name": func_name,
            "user_id": user_id,
            "context": context[:200],
            "timestamp": datetime.now().isoformat(),
            "success": True
        }
        
        self.successful_patterns.append(pattern)
        
        # Ограничиваем размер истории
        if len(self.successful_patterns) > 100:
            self.successful_patterns = self.successful_patterns[-100:]
        
        logger.info(f"[TOOL LEARNING] Learned from success: {func_name} for user {user_id}")
    
    def learn_from_failure(self, func_name: str, error: str):
        """Обучается на ошибках"""
        if func_name not in self.tool_usage_stats:
            self.tool_usage_stats[func_name] = {
                "total_calls": 0,
                "successful_calls": 0,
                "failed_calls": 0,
                "common_contexts": []
            }
        
        stats = self.tool_usage_stats[func_name]
        stats["total_calls"] += 1
        stats["failed_calls"] += 1
        
        logger.warning(f"[TOOL LEARNING] Learned from failure: {func_name} - {error}")
    
    def get_prioritized_tools(self, user_id: Optional[int] = None) -> List[Dict]:
        """
        Возвращает инструменты с приоритизацией
        
        Args:
            user_id: ID пользователя для персонализации
            
        Returns:
            Список инструментов, отсортированных по релевантности
        """
        tools = []
        
        for name, tool_info in self.discovered_tools.items():
            # Базовый приоритет
            priority = 0
            
            # Учитываем общую статистику использования
            if name in self.tool_usage_stats:
                stats = self.tool_usage_stats[name]
                success_rate = (stats["successful_calls"] / stats["total_calls"] 
                              if stats["total_calls"] > 0 else 0)
                priority += success_rate * 10
                priority += min(stats["successful_calls"] / 10, 5)  # До +5 за частоту
            
            # Учитываем предпочтения конкретного пользователя
            if user_id and user_id in self.user_preferences:
                if name in self.user_preferences[user_id]:
                    user_pref = self.user_preferences[user_id][name]
                    priority += min(user_pref["usage_count"] / 5, 10)  # До +10 за личную частоту
            
            tool_with_priority = tool_info.copy()
            tool_with_priority["priority"] = priority
            tools.append(tool_with_priority)
        
        # Сортируем по приоритету
        tools.sort(key=lambda x: x.get("priority", 0), reverse=True)
        
        # Удаляем служебное поле priority перед возвратом
        for tool in tools:
            tool.pop("priority", None)
        
        return tools
    
    def get_tools_for_context(self, context: str, user_id: Optional[int] = None) -> List[Dict]:
        """
        Возвращает наиболее релевантные инструменты для данного контекста.
        Гибридный подход: просто возвращаем все приоритизированные инструменты,
        пусть AI сам решает через DeepSeek tool_calls какие использовать.
        
        Args:
            context: Контекст запроса пользователя (не используется в гибридном подходе)
            user_id: ID пользователя
            
        Returns:
            Список приоритизированных инструментов
        """
        # Гибридный подход: возвращаем все приоритизированные инструменты
        # AI сам решит через DeepSeek какие инструменты вызвать
        return self.get_prioritized_tools(user_id)
    
    def save_stats(self, filepath: str = "tool_stats.json"):
        """Сохраняет статистику использования"""
        try:
            data = {
                "tool_usage_stats": self.tool_usage_stats,
                "user_preferences": self.user_preferences,
                "successful_patterns": self.successful_patterns[-50:]  # Только последние 50
            }
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            logger.info(f"[TOOL STATS] Saved statistics to {filepath}")
        except Exception as e:
            logger.error(f"[TOOL STATS] Failed to save statistics: {e}")
    
    def load_stats(self, filepath: str = "tool_stats.json"):
        """Загружает статистику использования"""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            self.tool_usage_stats = data.get("tool_usage_stats", {})
            self.user_preferences = data.get("user_preferences", {})
            self.successful_patterns = data.get("successful_patterns", [])
            
            logger.info(f"[TOOL STATS] Loaded statistics from {filepath}")
        except FileNotFoundError:
            logger.info(f"[TOOL STATS] No statistics file found: {filepath}")
        except Exception as e:
            logger.error(f"[TOOL STATS] Failed to load statistics: {e}")


# Глобальный экземпляр для использования в приложении
tool_discovery = DynamicToolDiscovery()


def get_dynamic_tools(user_id: Optional[int] = None, context: Optional[str] = None) -> List[Dict]:
    """
    Основная функция для получения инструментов
    
    Args:
        user_id: ID пользователя для персонализации
        context: Контекст запроса для фильтрации
        
    Returns:
        Список инструментов
    """
    if context:
        return tool_discovery.get_tools_for_context(context, user_id)
    else:
        return tool_discovery.get_prioritized_tools(user_id)
