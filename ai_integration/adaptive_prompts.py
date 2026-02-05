"""
Адаптивная система генерации промптов с обучением и персонализацией

Вместо статического промпта реализует гибкий подход:
- AI-генерация промптов на основе успешных диалогов
- Адаптация тона/стиля под пользователя
- Динамическое добавление примеров из истории
- Персонализация инструкций под предпочтения
- Обучение на feedback от пользователей
"""

import logging
import json
from typing import Dict, List, Optional, Any
from datetime import datetime
import asyncio
import aiohttp

logger = logging.getLogger(__name__)


class AdaptivePromptSystem:
    """
    Класс для адаптивной генерации и оптимизации промптов
    """
    
    def __init__(self):
        self.user_styles = {}  # Стиль общения каждого пользователя
        self.successful_patterns = []  # Успешные паттерны диалогов
        self.prompt_performance = {}  # Эффективность промптов
        self.user_feedback = {}  # Feedback от пользователей
        self.prompt_templates = self._init_base_templates()
        
    def _init_base_templates(self) -> Dict[str, str]:
        """Инициализирует базовые шаблоны промптов"""
        return {
            "formal": """Ты - профессиональный AI-помощник для управления задачами.
Стиль: деловой, четкий, структурированный.
Приоритет: эффективность и точность.""",
            
            "friendly": """Ты - дружелюбный AI-помощник для управления задачами.
Стиль: непринужденный, но профессиональный.
Приоритет: создание комфортной атмосферы общения.""",
            
            "concise": """Ты - AI-помощник, который ценит время пользователя.
Стиль: максимально краткий, без воды.
Приоритет: быстрые и точные ответы.""",
            
            "detailed": """Ты - AI-помощник, который дает развернутые объяснения.
Стиль: подробный, с примерами и контекстом.
Приоритет: полное понимание пользователя."""
        }
    
    def detect_user_style(self, user_id: int, message_history: List[Dict]) -> str:
        """
        Анализирует стиль общения пользователя
        
        Args:
            user_id: ID пользователя
            message_history: История сообщений
            
        Returns:
            Определенный стиль: 'formal', 'friendly', 'concise', 'detailed'
        """
        if user_id in self.user_styles:
            return self.user_styles[user_id]
        
        if not message_history:
            return "friendly"  # По умолчанию
        
        # Анализ паттернов
        user_messages = [m for m in message_history if m.get('role') == 'user']
        
        if not user_messages:
            return "friendly"
        
        # Считаем характеристики
        total_length = sum(len(m.get('content', '')) for m in user_messages)
        avg_length = total_length / len(user_messages) if user_messages else 0
        
        # Проверяем формальность
        formal_words = ['пожалуйста', 'спасибо', 'благодарю', 'уважаемый']
        casual_words = ['привет', 'ок', 'круто', 'давай', 'ага']
        
        formal_count = 0
        casual_count = 0
        
        for msg in user_messages:
            content = msg.get('content', '').lower()
            formal_count += sum(1 for word in formal_words if word in content)
            casual_count += sum(1 for word in casual_words if word in content)
        
        # Определяем стиль с улучшенной логикой
        if formal_count > 0 and (formal_count > casual_count * 2 or avg_length > 80):
            style = "formal"
        elif avg_length > 150:
            style = "detailed"
        elif avg_length < 20:
            style = "concise"
        else:
            style = "friendly"
        
        # Сохраняем определенный стиль
        self.user_styles[user_id] = style
        logger.info(f"[PROMPT ADAPT] Detected style for user {user_id}: {style}")
        
        return style
    
    async def generate_adaptive_prompt(
        self,
        base_prompt: str,
        user_id: int,
        context: Optional[Dict] = None,
        message_history: Optional[List[Dict]] = None
    ) -> str:
        """
        Генерирует адаптивный промпт с учетом контекста пользователя
        
        Args:
            base_prompt: Базовый промпт (из prompts.py)
            user_id: ID пользователя
            context: Дополнительный контекст
            message_history: История сообщений
            
        Returns:
            Адаптированный промпт
        """
        # Определяем стиль пользователя
        style = self.detect_user_style(user_id, message_history or [])
        
        # Добавляем стилевые инструкции
        style_instructions = self._get_style_instructions(style)
        
        # Добавляем примеры успешных диалогов
        examples = self._get_successful_examples(user_id, limit=3)
        examples_text = ""
        if examples:
            examples_text = "\n\nУСПЕШНЫЕ ПРИМЕРЫ ВЗАИМОДЕЙСТВИЯ:\n"
            for i, ex in enumerate(examples, 1):
                examples_text += f"{i}. Запрос: '{ex['user_message'][:50]}...'\n"
                examples_text += f"   Ответ: '{ex['ai_response'][:50]}...'\n"
                examples_text += f"   Результат: ✅ Успешно\n"
        
        # Добавляем персонализированные инструкции
        personal_instructions = self._get_personal_instructions(user_id)
        
        # Добавляем динамические подсказки
        dynamic_hints = await self._generate_dynamic_hints(user_id, context)
        
        # Собираем адаптированный промпт
        adapted_prompt = f"""{base_prompt}

{style_instructions}

{personal_instructions}

{examples_text}

{dynamic_hints}

ВАЖНО: Адаптируйся под стиль пользователя и используй успешные паттерны из примеров."""
        
        return adapted_prompt
    
    def _get_style_instructions(self, style: str) -> str:
        """Возвращает инструкции для конкретного стиля"""
        instructions = {
            "formal": """
СТИЛЬ ОБЩЕНИЯ: Деловой и профессиональный
- Используй вежливые обращения
- Структурируй ответы логично
- Избегай сленга и эмодзи
- Фокусируйся на эффективности""",
            
            "friendly": """
СТИЛЬ ОБЩЕНИЯ: Дружелюбный и непринужденный
- Будь теплым и открытым
- Используй 1-2 эмодзи для эмоциональности
- Можно использовать разговорные обороты
- Создавай комфортную атмосферу""",
            
            "concise": """
СТИЛЬ ОБЩЕНИЯ: Максимально краткий
- Ответы до 50 слов для простых вопросов
- Без воды и повторений
- Сразу к делу, без вступлений
- Цени время пользователя""",
            
            "detailed": """
СТИЛЬ ОБЩЕНИЯ: Подробный и развернутый
- Давай полные объяснения
- Приводи примеры и контекст
- Покрывай все аспекты вопроса
- Помогай глубже понять тему"""
        }
        
        return instructions.get(style, instructions["friendly"])
    
    def _get_personal_instructions(self, user_id: int) -> str:
        """Возвращает персонализированные инструкции"""
        if user_id not in self.user_feedback:
            return ""
        
        feedback = self.user_feedback[user_id]
        instructions = "\nПЕРСОНАЛИЗИРОВАННЫЕ ИНСТРУКЦИИ:\n"
        
        # Анализируем feedback
        if feedback.get('prefers_short_answers', False):
            instructions += "- Пользователь предпочитает краткие ответы (30-50 слов)\n"
        
        if feedback.get('likes_examples', False):
            instructions += "- Пользователь ценит конкретные примеры\n"
        
        if feedback.get('prefers_no_emoji', False):
            instructions += "- Не используй эмодзи\n"
        
        if feedback.get('likes_proactive', False):
            instructions += "- Будь более проактивным в предложениях\n"
        
        if feedback.get('preferred_topics'):
            topics = ', '.join(feedback['preferred_topics'])
            instructions += f"- Предпочитаемые темы: {topics}\n"
        
        return instructions if len(instructions) > 50 else ""
    
    async def _generate_dynamic_hints(
        self,
        user_id: int,
        context: Optional[Dict]
    ) -> str:
        """Генерирует динамические подсказки на основе контекста"""
        if not context:
            return ""
        
        hints = "\nДИНАМИЧЕСКИЕ ПОДСКАЗКИ ДЛЯ ТЕКУЩЕГО КОНТЕКСТА:\n"
        
        # Анализируем время
        if context.get('time_of_day'):
            time = context['time_of_day']
            if time == 'morning':
                hints += "- Утро: фокусируйся на планировании дня и энергичных задачах\n"
            elif time == 'evening':
                hints += "- Вечер: предлагай анализ дня и подготовку к завтрашнему\n"
        
        # Анализируем активность пользователя
        if context.get('recent_activity'):
            activity = context['recent_activity']
            if activity == 'inactive':
                hints += "- Пользователь давно не активен: будь более вовлекающим\n"
            elif activity == 'very_active':
                hints += "- Высокая активность: предлагай больше задач и идей\n"
        
        # Анализируем успешность последних действий
        if context.get('recent_success_rate'):
            rate = context['recent_success_rate']
            if rate < 0.5:
                hints += "- Низкая успешность: упрости задачи, разбей на более мелкие шаги\n"
            elif rate > 0.8:
                hints += "- Высокая успешность: усложняй задачи, предлагай челленджи\n"
        
        return hints if len(hints) > 50 else ""
    
    def _get_successful_examples(self, user_id: int, limit: int = 3) -> List[Dict]:
        """Возвращает примеры успешных диалогов"""
        user_patterns = [
            p for p in self.successful_patterns
            if p.get('user_id') == user_id
        ]
        
        # Сортируем по эффективности
        user_patterns.sort(
            key=lambda x: x.get('effectiveness', 0),
            reverse=True
        )
        
        return user_patterns[:limit]
    
    def learn_from_interaction(
        self,
        user_id: int,
        user_message: str,
        ai_response: str,
        was_successful: bool,
        effectiveness: float = 1.0,
        feedback: Optional[str] = None
    ):
        """
        Обучается на взаимодействии с пользователем
        
        Args:
            user_id: ID пользователя
            user_message: Сообщение пользователя
            ai_response: Ответ AI
            was_successful: Был ли диалог успешным
            effectiveness: Эффективность (0.0-1.0)
            feedback: Опциональный feedback от пользователя
        """
        pattern = {
            'user_id': user_id,
            'user_message': user_message,
            'ai_response': ai_response,
            'successful': was_successful,
            'effectiveness': effectiveness,
            'timestamp': datetime.now().isoformat(),
            'feedback': feedback
        }
        
        if was_successful:
            self.successful_patterns.append(pattern)
            
            # Ограничиваем размер истории
            if len(self.successful_patterns) > 100:
                self.successful_patterns = self.successful_patterns[-100:]
            
            logger.info(f"[PROMPT LEARNING] Learned successful pattern for user {user_id}")
        
        # Обновляем feedback, если есть
        if feedback:
            if user_id not in self.user_feedback:
                self.user_feedback[user_id] = {}
            
            # Анализируем feedback
            self._analyze_feedback(user_id, feedback)
    
    def _analyze_feedback(self, user_id: int, feedback: str):
        """Анализирует feedback для извлечения предпочтений"""
        feedback_lower = feedback.lower()
        
        if user_id not in self.user_feedback:
            self.user_feedback[user_id] = {
                'prefers_short_answers': False,
                'likes_examples': False,
                'prefers_no_emoji': False,
                'likes_proactive': False,
                'preferred_topics': []
            }
        
        user_prefs = self.user_feedback[user_id]
        
        # Анализируем ключевые слова
        if any(word in feedback_lower for word in ['кратко', 'короче', 'коротко', 'быстро']):
            user_prefs['prefers_short_answers'] = True
        
        if any(word in feedback_lower for word in ['пример', 'конкретно', 'покажи как']):
            user_prefs['likes_examples'] = True
        
        if any(word in feedback_lower for word in ['без эмодзи', 'серьезнее', 'формально']):
            user_prefs['prefers_no_emoji'] = True
        
        if any(word in feedback_lower for word in ['предлагай', 'предложи', 'советуй']):
            user_prefs['likes_proactive'] = True
        
        logger.info(f"[PROMPT FEEDBACK] Updated preferences for user {user_id}")
    
    def optimize_prompt_for_task(
        self,
        base_prompt: str,
        task_type: str,
        user_id: Optional[int] = None
    ) -> str:
        """
        Оптимизирует промпт для конкретного типа задачи
        
        Args:
            base_prompt: Базовый промпт
            task_type: Тип задачи ('create_task', 'list_tasks', 'analyze', etc.)
            user_id: ID пользователя
            
        Returns:
            Оптимизированный промпт
        """
        task_instructions = {
            'create_task': """
ФОКУС НА СОЗДАНИИ ЗАДАЧ:
- Извлекай четкие параметры: название, время, детали
- Уточняй недостающую информацию
- Предлагай оптимальное время если не указано
- Проверяй конфликты в расписании""",
            
            'list_tasks': """
ФОКУС НА ОТОБРАЖЕНИИ ЗАДАЧ:
- Структурируй список логично (по времени/приоритету)
- Выделяй срочные задачи
- Указывай статус выполнения
- Предлагай действия по задачам""",
            
            'complete_task': """
ФОКУС НА ЗАВЕРШЕНИИ ЗАДАЧ:
- Найди точное совпадение задачи
- Спроси о результатах выполнения
- Предложи следующий шаг
- Зафиксируй достижение""",
            
            'analyze': """
ФОКУС НА АНАЛИЗЕ:
- Выяви паттерны и закономерности
- Предложи оптимизации
- Укажи на проблемы
- Дай конкретные рекомендации""",
            
            'find_contacts': """
ФОКУС НА ПОИСКЕ КОНТАКТОВ:
- Точно определи критерии поиска
- Учитывай контекст задачи
- Предлагай релевантных людей
- Объясняй, почему они подходят"""
        }
        
        task_specific = task_instructions.get(task_type, "")
        
        if task_specific:
            optimized = f"""{base_prompt}

{task_specific}

КРИТИЧЕСКИ ВАЖНО: Оптимизируй все действия под тип задачи '{task_type}'."""
            return optimized
        
        return base_prompt
    
    def save_state(self, filepath: str = "prompt_state.json"):
        """Сохраняет состояние системы"""
        try:
            data = {
                "user_styles": self.user_styles,
                "successful_patterns": self.successful_patterns[-50:],
                "user_feedback": self.user_feedback,
                "prompt_performance": self.prompt_performance
            }
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            logger.info(f"[PROMPT STATE] Saved to {filepath}")
        except Exception as e:
            logger.error(f"[PROMPT STATE] Failed to save: {e}")
    
    def load_state(self, filepath: str = "prompt_state.json"):
        """Загружает состояние системы"""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Конвертируем строковые ключи обратно в int для user_styles и user_feedback
            user_styles_raw = data.get("user_styles", {})
            self.user_styles = {int(k): v for k, v in user_styles_raw.items()}
            
            user_feedback_raw = data.get("user_feedback", {})
            self.user_feedback = {int(k): v for k, v in user_feedback_raw.items()}
            
            self.successful_patterns = data.get("successful_patterns", [])
            self.prompt_performance = data.get("prompt_performance", {})
            
            logger.info(f"[PROMPT STATE] Loaded from {filepath}")
        except FileNotFoundError:
            logger.info(f"[PROMPT STATE] No state file found: {filepath}")
        except Exception as e:
            logger.error(f"[PROMPT STATE] Failed to load: {e}")


# Глобальный экземпляр
adaptive_prompt_system = AdaptivePromptSystem()


async def get_adaptive_prompt(
    base_prompt: str,
    user_id: int,
    context: Optional[Dict] = None,
    message_history: Optional[List[Dict]] = None,
    task_type: Optional[str] = None
) -> str:
    """
    Основная функция для получения адаптивного промпта
    
    Args:
        base_prompt: Базовый промпт из prompts.py
        user_id: ID пользователя
        context: Дополнительный контекст
        message_history: История сообщений
        task_type: Тип текущей задачи
        
    Returns:
        Адаптированный промпт
    """
    # Генерируем адаптивный промпт
    adapted = await adaptive_prompt_system.generate_adaptive_prompt(
        base_prompt=base_prompt,
        user_id=user_id,
        context=context,
        message_history=message_history
    )
    
    # Оптимизируем под тип задачи, если указан
    if task_type:
        adapted = adaptive_prompt_system.optimize_prompt_for_task(
            base_prompt=adapted,
            task_type=task_type,
            user_id=user_id
        )
    
    return adapted
