"""
Мультиагентная архитектура — внутренние роли для глубокого мышления.

НЕ отдельные AI-вызовы, а разные «режимы мышления» агента,
реализованные как инструкции в одном вызове DeepSeek.

Роли:
- Analyst: анализирует ситуацию, определяет приоритеты
- Strategist: планирует действия, выбирает инструменты
- Companion: отвечает эмпатично, по-человечески
- Critic: проверяет качество ответа перед отправкой

Workflow:
1. Analyst определяет: что за ситуация? что нужно?
2. Strategist решает: какие tools вызвать? в каком порядке?
3. Companion формирует ответ: человечный, тёплый
4. Critic проверяет: нет ли шаблонов, списков, автоответчика?
"""

import logging

logger = logging.getLogger(__name__)


class AgentRole:
    """Базовый класс для ролей внутри агента."""
    
    def __init__(self, name, instruction):
        self.name = name
        self.instruction = instruction
    
    def get_prompt_injection(self, context):
        """Возвращает инструкцию для инъекции в промпт."""
        return f"\n[{self.name}]: {self.instruction}"


class Analyst(AgentRole):
    """Анализирует ситуацию: эмоции, контекст, приоритеты."""
    
    def __init__(self):
        super().__init__("АНАЛИТИК", "")
    
    def analyze(self, user_message, profile_data, tasks_data, memory_context):
        """Возвращает анализ ситуации как текст для промпта."""
        analysis = []
        
        # Анализ полноты профиля
        if not profile_data or len(profile_data) < 3:
            missing = []
            for field in ['city', 'company', 'position', 'goals', 'skills', 'interests']:
                if field not in profile_data:
                    field_names = {
                        'city': 'город', 'company': 'компания', 'position': 'должность',
                        'goals': 'цели', 'skills': 'навыки', 'interests': 'интересы'
                    }
                    missing.append(field_names.get(field, field))
            analysis.append(f"ПРОФИЛЬ НЕПОЛНЫЙ! Не хватает: {', '.join(missing[:3])}. ПРИОРИТЕТ: узнать о человеке.")
        
        # Анализ задач
        if not tasks_data:
            analysis.append("ЗАДАЧ НЕТ. Фокус на СЕЙЧАС: спроси над чем работает, чем помочь прямо сейчас. НЕ предлагай откладывать на завтра.")
        
        # Анализ памяти — есть ли контекст для персонализации
        if memory_context:
            analysis.append(f"ЕСТЬ ПАМЯТЬ: используй для персонализации.")
        else:
            analysis.append("ПАМЯТИ МАЛО: запоминай факты через update_profile/запись в память.")
        
        # Длина сообщения → matching energy
        msg_len = len(user_message)
        if msg_len < 15:
            analysis.append("КОРОТКОЕ сообщение → короткий ответ (1-2 предложения).")
        elif msg_len > 100:
            analysis.append("РАЗВЁРНУТОЕ сообщение → можно ответить подробнее (3-5 предложений).")
        
        return "\n".join(analysis)


class Strategist(AgentRole):
    """Планирует стратегию: какие tools, в каком порядке, почему."""
    
    def __init__(self):
        super().__init__("СТРАТЕГ", "")
    
    def plan(self, intent, emotion, profile_data, has_tasks):
        """Возвращает стратегию действий."""
        strategies = []
        
        # Стратегия по намерению
        intent_strategies = {
            'greeting': 'ПРИВЕТСТВИЕ → Если профиль пустой: один тёплый вопрос. Если задач нет: спроси чем занят СЕЙЧАС, предложи помощь в текущем моменте. НЕ предлагай создать задачу на завтра.',
            'farewell': 'ПРОЩАНИЕ → Кратко, тепло. Напомни о планах если есть задачи.',
            'task_management': 'ЗАДАЧИ → check_time_conflicts → предложи точку (не создавай сам). Спроси согласие.',
            'information_request': 'ИНФОРМАЦИЯ → Обязательно research_topic. Проанализируй, синтезируй ВЫВОД.',
            'advice_seeking': 'СОВЕТ → Дай СВОЁ мнение. "Я бы на твоём месте..." Не "есть варианты".',
            'emotional_sharing': 'ЭМОЦИИ → ЭМПАТИЯ ПЕРВАЯ. Не решай проблему, а поддержи. Потом один вопрос.',
        }
        if intent in intent_strategies:
            strategies.append(intent_strategies[intent])
        
        # Стратегия по эмоции
        if emotion in ('tired', 'sad', 'anxious'):
            strategies.append('ЭМОЦИЯ НЕГАТИВНАЯ → Лёгкий тон, не нагружай, покажи что понимаешь.')
        elif emotion == 'excited':
            strategies.append('ЭМОЦИЯ ПОЗИТИВНАЯ → Поддержи! Предложи следующий шаг.')
        elif emotion == 'frustrated':
            strategies.append('РАЗДРАЖЕНИЕ → Признай проблему. Потом конкретное решение.')
        
        # Стратегия по профилю
        if not profile_data:
            strategies.append('ПРОФИЛЬ ПУСТОЙ → КРИТИЧНО узнать: чем занимается, где живёт, что интересно.')
        
        if not has_tasks:
            strategies.append('ЗАДАЧ НЕТ → Спроси над чем работает СЕЙЧАС. Предложи помощь в ТЕКУЩЕМ моменте. НЕ откладывай на завтра.')
        
        return "\n".join(strategies)


class Companion(AgentRole):
    """Отвечает по-человечески: тон, стиль, эмпатия."""
    
    def __init__(self):
        super().__init__("КОМПАНЬОН", "")
    
    def get_tone_guide(self, emotion, intent, time_of_day=None):
        """Возвращает руководство по тону ответа."""
        guides = []
        
        # Тон по эмоции
        tone_map = {
            'tired': 'Мягко, без давления. "Понимаю, тяжёлый день..."',
            'excited': 'Энергично! Подхвати энтузиазм.',
            'frustrated': 'Спокойно, с пониманием. Не обесценивай.',
            'anxious': 'Уверенно, дай опору. "Давай разберёмся вместе."',
            'sad': 'Тепло, без навязчивости. Просто будь рядом.',
            'confused': 'Чётко, по шагам. Без лишней информации.',
        }
        if emotion in tone_map:
            guides.append(f"ТОН: {tone_map[emotion]}")
        
        # Стиль по времени суток
        if time_of_day:
            if time_of_day == 'ночь':
                guides.append("ВРЕМЯ: Ночь → кратко, не нагружай.")
            elif time_of_day == 'утро':
                guides.append("ВРЕМЯ: Утро → бодро, план дня.")
            elif time_of_day == 'вечер':
                guides.append("ВРЕМЯ: Вечер → расслабленно, итоги.")
        
        return "\n".join(guides)


class Critic(AgentRole):
    """Проверяет качество ответа перед отправкой."""
    
    def __init__(self):
        super().__init__("КРИТИК", "")
    
    def get_checklist(self):
        """Возвращает чеклист для AI перед ответом."""
        return (
            "ПРОВЕРЬ ПЕРЕД ОТВЕТОМ:\n"
            "- Нет шаблонных начал (Отлично/Конечно/Хорошо)?\n"
            "- Нет списков (1. 2. 3.)?\n"
            "- Нет автоответчика (чем помочь?)?\n"
            "- Ответ < 400 символов?\n"
            "- Есть ОДНА ценная мысль?\n"
            "- Это звучит как живой человек, а не бот?"
        )


# ═══════════════════════════════════════════════════════════════
# ОРКЕСТРАТОР — собирает всех агентов
# ═══════════════════════════════════════════════════════════════

class MultiAgentOrchestrator:
    """Оркестрирует внутренние роли агента.
    
    Не делает отдельные API-вызовы — собирает мультиагентный
    контекст в единый промпт для одного вызова DeepSeek.
    """
    
    def __init__(self):
        self.analyst = Analyst()
        self.strategist = Strategist()
        self.companion = Companion()
        self.critic = Critic()
    
    def build_multi_agent_context(self, user_message, profile_data, tasks_data, 
                                   memory_context, emotion, intent, time_of_day=None):
        """Собирает контекст всех агентов в один блок для промпта.
        
        Returns: str — блок для инъекции в системный промпт.
        """
        parts = []
        
        # 1. Аналитик
        analysis = self.analyst.analyze(user_message, profile_data, tasks_data, memory_context)
        if analysis:
            parts.append(f"[АНАЛИТИК]\n{analysis}")
        
        # 2. Стратег
        has_tasks = bool(tasks_data)
        strategy = self.strategist.plan(intent, emotion, profile_data, has_tasks)
        if strategy:
            parts.append(f"[СТРАТЕГ]\n{strategy}")
        
        # 3. Компаньон
        tone = self.companion.get_tone_guide(emotion, intent, time_of_day)
        if tone:
            parts.append(f"[КОМПАНЬОН]\n{tone}")
        
        # 4. Критик
        parts.append(f"[КРИТИК]\n{self.critic.get_checklist()}")
        
        if not parts:
            return ""
        
        return "\n\n[МУЛЬТИАГЕНТНЫЙ АНАЛИЗ]\n" + "\n\n".join(parts)


# Глобальный оркестратор
_orchestrator = None

def get_orchestrator():
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = MultiAgentOrchestrator()
    return _orchestrator
