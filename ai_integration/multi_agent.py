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
    
    def analyze(self, user_message, profile_data, tasks_data, memory_context, lang='ru'):
        """Возвращает анализ ситуации как текст для промпта."""
        analysis = []
        
        # Анализ полноты профиля
        if not profile_data or len(profile_data) < 3:
            missing = []
            for field in ['city', 'company', 'position', 'goals', 'skills', 'interests']:
                if field not in profile_data:
                    if lang == 'en':
                        field_names = {
                            'city': 'city', 'company': 'company', 'position': 'position',
                            'goals': 'goals', 'skills': 'skills', 'interests': 'interests'
                        }
                    else:
                        field_names = {
                            'city': 'город', 'company': 'компания', 'position': 'должность',
                            'goals': 'цели', 'skills': 'навыки', 'interests': 'интересы'
                        }
                    missing.append(field_names.get(field, field))
            if lang == 'en':
                analysis.append(f"PROFILE INCOMPLETE! Missing: {', '.join(missing[:3])}. PRIORITY: learn about the person.")
            else:
                analysis.append(f"ПРОФИЛЬ НЕПОЛНЫЙ! Не хватает: {', '.join(missing[:3])}. ПРИОРИТЕТ: узнать о человеке.")
        
        # Анализ задач
        if not tasks_data:
            if not profile_data or len(profile_data) < 3:
                if lang == 'en':
                    analysis.append("NO TASKS, but PROFILE EMPTY → tasks can WAIT. First FIND OUT who this person is: what they do, interests, goals. Show you're a thinking partner, not a task planner.")
                else:
                    analysis.append("ЗАДАЧ НЕТ, но ПРОФИЛЬ ПУСТ → задачи ПОДОЖДУТ. Сначала УЗНАЙ кто этот человек: чем занимается, что интересно, к чему стремится. Покажи что ты мыслящий партнёр, а не планировщик задач.")
            else:
                if lang == 'en':
                    analysis.append("NO TASKS. Focus on NOW: ask what they're working on, how to help right now. DON'T suggest postponing to tomorrow.")
                else:
                    analysis.append("ЗАДАЧ НЕТ. Фокус на СЕЙЧАС: спроси над чем работает, чем помочь прямо сейчас. НЕ предлагай откладывать на завтра.")
        
        # Анализ памяти
        if memory_context:
            analysis.append("HAS MEMORY: use for personalization." if lang == 'en' else "ЕСТЬ ПАМЯТЬ: используй для персонализации.")
        else:
            analysis.append("LOW MEMORY: save facts via update_profile/memory." if lang == 'en' else "ПАМЯТИ МАЛО: запоминай факты через update_profile/запись в память.")
        
        # Длина сообщения
        msg_len = len(user_message)
        short_confirms = {'да', 'давай', 'создай', 'поставь', 'ок', 'хорошо', 'го', 'сделай', 'ставь', 'окей', 'ага', 'угу', 'yes', 'sure', 'ok', 'yeah', 'yep', 'do it', 'go ahead', 'create', 'set', 'ладно', 'конечно'}
        msg_lower = user_message.strip().lower().rstrip('!.')
        if msg_lower in short_confirms or msg_len < 15:
            if lang == 'en':
                analysis.append("SHORT CONFIRMATION → user agrees with your PREVIOUS suggestion. Check your last response in history and EXECUTE that action (add_task, create_post etc.). DON'T re-ask, DON'T re-analyze.")
            else:
                analysis.append("КОРОТКОЕ ПОДТВЕРЖДЕНИЕ → пользователь соглашается с тем, что ты предложил в ПРЕДЫДУЩЕМ сообщении. Посмотри свой последний ответ в истории и ВЫПОЛНИ то действие (add_task, create_post и т.д.). НЕ переспрашивай, НЕ анализируй заново.")
        elif msg_len > 100:
            analysis.append("DETAILED message → can give deep expert response with data." if lang == 'en' else "РАЗВЁРНУТОЕ сообщение → можно дать глубокий экспертный ответ с данными.")
        
        # Свободный анализ
        opportunity_hint = self._detect_situation_signals(profile_data, tasks_data, user_message, lang=lang)
        if opportunity_hint:
            analysis.append(opportunity_hint)
        
        return "\n".join(analysis)
    
    def _detect_situation_signals(self, profile_data, tasks_data, user_message, lang='ru'):
        """Обнаруживает структурные сигналы в данных пользователя."""
        if not profile_data:
            return None
        
        signals = []
        
        goals = profile_data.get('goals', '')
        skills = profile_data.get('skills', '')
        interests = profile_data.get('interests', '')
        position = profile_data.get('position', '')
        company = profile_data.get('company', '')
        city = profile_data.get('city', '')
        
        filled = sum(1 for f in [goals, skills, interests, position] if f)
        
        if lang == 'en':
            if filled >= 3:
                signals.append(f"RESOURCES: [{position}|{skills[:40]}|{interests[:40]}] — find where skills + interests create a unique combo")
            if skills and goals:
                signals.append(f"LINK: skills [{skills[:40]}] + goal [{goals[:40]}] — what's the shortest path?")
            if position and interests and position.lower() not in interests.lower():
                signals.append(f"INTERSECTION: work [{position}] + interest [{interests[:40]}] — potential growth at the junction")
            if goals and not tasks_data:
                signals.append(f"GAP: goal [{goals[:50]}] without tasks — intention without action")
            if tasks_data and not goals:
                signals.append("GAP: tasks without goals — actions without direction")
            if tasks_data and len(tasks_data) > 5:
                signals.append(f"OVERLOAD: {len(tasks_data)} tasks — what's unnecessary?")
            if (skills or interests) and not goals:
                signals.append("POTENTIAL WITHOUT DIRECTION: skills/interests exist, no direction set")
        else:
            if filled >= 3:
                signals.append(f"РЕСУРСЫ: [{position}|{skills[:40]}|{interests[:40]}] — ищи где навыки + интересы создают уникальную комбинацию")
            if skills and goals:
                signals.append(f"СВЯЗКА: навыки [{skills[:40]}] + цель [{goals[:40]}] — какой кратчайший путь?")
            if position and interests and position.lower() not in interests.lower():
                signals.append(f"ПЕРЕСЕЧЕНИЕ: работа [{position}] + интерес [{interests[:40]}] — возможная точка роста на стыке")
            if goals and not tasks_data:
                signals.append(f"РАЗРЫВ: цель [{goals[:50]}] без задач — намерение без действия")
            if tasks_data and not goals:
                signals.append("РАЗРЫВ: задачи без цели — действия без направления")
            if tasks_data and len(tasks_data) > 5:
                signals.append(f"ПЕРЕГРУЗКА: {len(tasks_data)} задач — что лишнее?")
            if (skills or interests) and not goals:
                signals.append("ПОТЕНЦИАЛ БЕЗ ВЕКТОРА: навыки/интересы есть, направления нет")
        
        return ' | '.join(signals) if signals else None


class Strategist(AgentRole):
    """Планирует стратегию: какие tools, в каком порядке, почему."""
    
    def __init__(self):
        super().__init__("СТРАТЕГ", "")
    
    def plan(self, intent, emotion, profile_data, has_tasks, lang='ru'):
        """Возвращает стратегию действий."""
        strategies = []
        
        if lang == 'en':
            intent_strategies = {
                'greeting': "GREETING → If profile empty: introduce yourself as a thinking partner (not a feature list!), explain you see the whole person (work, health, goals, growth), ask ONE lively question about them. DON'T suggest tasks until you know who they are. If profile filled and no tasks: ask what they're doing NOW.",
                'farewell': "FAREWELL → Brief, warm. Remind about plans if tasks exist.",
                'task_management': "TASKS → check_time_conflicts → suggest a slot. Ask for confirmation.",
                'information_request': "INFORMATION / QUESTION → Answer the question. If you need data — call the right tool (list_tasks, get_incoming_messages, get_delegation_progress, etc.) and report the fact. DON'T create tasks, DON'T delegate to agents, DON'T start action chains. Question = answer, not action. research_topic only if external data is needed (prices, trends, internet facts).",
                'advice_seeking': "ADVICE → research_topic for data. Give YOUR opinion with numbers. Suggest a concrete plan with deadlines and create tasks.",
                'emotional_sharing': "EMOTIONS → EMPATHY FIRST. Don't solve the problem, support. Then one question.",
            }
        else:
            intent_strategies = {
                'greeting': 'ПРИВЕТСТВИЕ → Если профиль пустой: представься как мыслящий партнёр (не список функций!), расскажи что видишь человека целиком (работа, здоровье, цели, развитие), задай ОДИН живой вопрос о нём самом. НЕ предлагай задачи пока не знаешь кто он. Если профиль заполнен и задач нет: спроси чем занят СЕЙЧАС.',
                'farewell': 'ПРОЩАНИЕ → Кратко, тепло. Напомни о планах если есть задачи.',
                'task_management': 'ЗАДАЧИ → check_time_conflicts → предложи точку. Спроси согласие.',
                'information_request': 'ИНФОРМАЦИЯ / ВОПРОС → Ответь на вопрос. Если нужны данные — вызови подходящий инструмент (list_tasks, get_incoming_messages, get_delegation_progress и т.д.) и сообщи факт. НЕ создавай задачи, НЕ поручай агентам, НЕ запускай цепочки действий. Вопрос = ответ, не действие. research_topic — только если нужны внешние данные (цены, тренды, факты из интернета).',
                'advice_seeking': 'СОВЕТ → research_topic для данных. Дай СВОЁ мнение с цифрами: "Я бы на твоём месте сделал X, потому что [Y% компаний так делают]". Предложи конкретный план с дедлайнами и создай задачи.',
                'emotional_sharing': 'ЭМОЦИИ → ЭМПАТИЯ ПЕРВАЯ. Не решай проблему, а поддержи. Потом один вопрос.',
            }
        if intent in intent_strategies:
            strategies.append(intent_strategies[intent])
        
        if lang == 'en':
            if emotion in ('tired', 'sad', 'anxious'):
                strategies.append("NEGATIVE EMOTION → Light tone, don't overload, show understanding.")
            elif emotion == 'excited':
                strategies.append("POSITIVE EMOTION → Support! Suggest next step.")
            elif emotion == 'frustrated':
                strategies.append("FRUSTRATION → Acknowledge the problem. Then concrete solution.")
            if not profile_data:
                strategies.append("PROFILE EMPTY → CRITICAL to learn: what they do, where they live, interests.")
            if not has_tasks:
                if not profile_data:
                    strategies.append("NO TASKS + EMPTY PROFILE → DON'T SUGGEST TASKS. First get to know: field, interests, goals. Tasks will come naturally from conversation.")
                else:
                    strategies.append("NO TASKS → Ask what they're working on NOW. Offer help in the CURRENT moment. DON'T postpone to tomorrow.")
        else:
            if emotion in ('tired', 'sad', 'anxious'):
                strategies.append('ЭМОЦИЯ НЕГАТИВНАЯ → Лёгкий тон, не нагружай, покажи что понимаешь.')
            elif emotion == 'excited':
                strategies.append('ЭМОЦИЯ ПОЗИТИВНАЯ → Поддержи! Предложи следующий шаг.')
            elif emotion == 'frustrated':
                strategies.append('РАЗДРАЖЕНИЕ → Признай проблему. Потом конкретное решение.')
            if not profile_data:
                strategies.append('ПРОФИЛЬ ПУСТОЙ → КРИТИЧНО узнать: чем занимается, где живёт, что интересно.')
            if not has_tasks:
                if not profile_data:
                    strategies.append('ЗАДАЧ НЕТ + ПРОФИЛЬ ПУСТ → НЕ ПРЕДЛАГАЙ ЗАДАЧИ. Сначала познакомься: узнай сферу, интересы, цели. Задачи появятся естественно из разговора.')
                else:
                    strategies.append('ЗАДАЧ НЕТ → Спроси над чем работает СЕЙЧАС. Предложи помощь в ТЕКУЩЕМ моменте. НЕ откладывай на завтра.')
        
        # Адаптивный фолбэк — если стратег не добавил ничего (general intent + всё нормально)
        # ИИ в этом случае получал пустой блок [СТРАТЕГ], теперь всегда получает контекст
        if not strategies:
            _parts = []
            if profile_data:
                _pos   = profile_data.get('position', '')
                _goals = profile_data.get('goals', '')
                _skl   = profile_data.get('skills', '')
                _int   = profile_data.get('interests', '')
                _gaps  = [k for k in ('goals', 'skills', 'interests') if not profile_data.get(k)]

                if lang == 'en':
                    if _pos or _int:
                        _parts.append(f"Context: {_pos or 'specialist'}"
                                      + (f", interests: {_int[:60]}" if _int else "")
                                      + (f", goals: {_goals[:60]}" if _goals else ""))
                    if _gaps:
                        _parts.append(f"Profile gaps (learn naturally): {', '.join(_gaps)}")
                    if has_tasks and _goals:
                        _parts.append(f"Active pursuit: {_goals[:70]} — connect current message to their deeper goal")
                    elif has_tasks and not _goals:
                        _parts.append("Has tasks but no declared goals — ask what big outcome they're working toward")
                else:
                    if _pos or _int:
                        _parts.append(f"Контекст: {_pos or 'специалист'}"
                                      + (f", интересы: {_int[:60]}" if _int else "")
                                      + (f", цели: {_goals[:60]}" if _goals else ""))
                    if _gaps:
                        _parts.append(f"Не заполнено в профиле (узнавай естественно): {', '.join(_gaps)}")
                    if has_tasks and _goals:
                        _parts.append(f"В работе: {_goals[:70]} — свяжи это сообщение с их настоящей целью")
                    elif has_tasks and not _goals:
                        _parts.append("Есть задачи, но целей нет — уместно спросить к чему ведёт эта работа")
            elif has_tasks:
                _parts.append(
                    "Has tasks, profile unknown — help with the current request, learn who they are organically"
                    if lang == 'en' else
                    "Есть задачи, профиль неизвестен — помоги с текущим запросом, узнай кто этот человек органично"
                )
            else:
                _parts.append(
                    "Fresh start: no profile, no tasks — introduce yourself as a thinking partner, ask one genuine question"
                    if lang == 'en' else
                    "Чистый старт: нет профиля, нет задач — представься как мыслящий партнёр, задай один живой вопрос"
                )
            if _parts:
                strategies.append("\n".join(_parts))

        return "\n".join(strategies)


class Companion(AgentRole):
    """Отвечает по-человечески: тон, стиль, эмпатия."""
    
    def __init__(self):
        super().__init__("КОМПАНЬОН", "")
    
    def get_tone_guide(self, emotion, intent, time_of_day=None, lang='ru'):
        """Возвращает руководство по тону ответа."""
        guides = []
        
        if lang == 'en':
            tone_map = {
                'tired': 'Gently, no pressure. "I get it, tough day…"',
                'excited': 'Energetically! Match the enthusiasm.',
                'frustrated': 'Calmly, with understanding. Don\'t dismiss.',
                'anxious': 'Confidently, give support. "Let\'s figure it out together."',
                'sad': 'Warmly, without being pushy. Just be there.',
                'confused': 'Clearly, step by step. No extra info.',
            }
            _tone_lbl = "TONE"
        else:
            tone_map = {
                'tired': 'Мягко, без давления. "Понимаю, тяжёлый день..."',
                'excited': 'Энергично! Подхвати энтузиазм.',
                'frustrated': 'Спокойно, с пониманием. Не обесценивай.',
                'anxious': 'Уверенно, дай опору. "Давай разберёмся вместе."',
                'sad': 'Тепло, без навязчивости. Просто будь рядом.',
                'confused': 'Чётко, по шагам. Без лишней информации.',
            }
            _tone_lbl = "ТОН"
        if emotion in tone_map:
            guides.append(f"{_tone_lbl}: {tone_map[emotion]}")
        
        if time_of_day:
            if lang == 'en':
                if time_of_day == 'ночь':
                    guides.append("TIME: Night → brief, don't overload.")
                elif time_of_day == 'утро':
                    guides.append("TIME: Morning → upbeat, day plan.")
                elif time_of_day == 'вечер':
                    guides.append("TIME: Evening → relaxed, wrap-up.")
            else:
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
    
    def get_checklist(self, lang='ru'):
        """Возвращает чеклист для AI перед ответом."""
        if lang == 'en':
            return (
                "CHECK BEFORE RESPONDING:\n"
                "- No template openings (Great/Sure/Of course)?\n"
                "- No numbered lists (1. 2. 3.)?\n"
                "- No auto-reply (how can I help?)?\n"
                "- Has specifics: numbers, deadlines, metrics, examples?\n"
                "- If advising — say WHAT EXACTLY, not 'research the market'?\n"
                "- Does it sound like a smart colleague, not a bot?"
            )
        return (
            "ПРОВЕРЬ ПЕРЕД ОТВЕТОМ:\n"
            "- Нет шаблонных начал (Отлично/Конечно/Хорошо)?\n"
            "- Нет нумерованных списков (1. 2. 3.)?\n"
            "- Нет автоответчика (чем помочь?)?\n"
            "- Есть конкретика: цифры, сроки, метрики, примеры?\n"
            "- Если советую — говорю ЧТО КОНКРЕТНО, а не 'изучи рынок'?\n"
            "- Это звучит как умный коллега, а не бот?"
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
                                   memory_context, emotion, intent, time_of_day=None, lang='ru'):
        """Собирает контекст всех агентов в один блок для промпта.
        
        Returns: str — блок для инъекции в системный промпт.
        """
        parts = []
        
        # 1. Аналитик
        analysis = self.analyst.analyze(user_message, profile_data, tasks_data, memory_context, lang=lang)
        if analysis:
            _lbl = "ANALYST" if lang == 'en' else "АНАЛИТИК"
            parts.append(f"[{_lbl}]\n{analysis}")
        
        # 2. Стратег
        has_tasks = bool(tasks_data)
        strategy = self.strategist.plan(intent, emotion, profile_data, has_tasks, lang=lang)
        if strategy:
            _lbl = "STRATEGIST" if lang == 'en' else "СТРАТЕГ"
            parts.append(f"[{_lbl}]\n{strategy}")
        
        # 3. Компаньон
        tone = self.companion.get_tone_guide(emotion, intent, time_of_day, lang=lang)
        if tone:
            _lbl = "COMPANION" if lang == 'en' else "КОМПАНЬОН"
            parts.append(f"[{_lbl}]\n{tone}")
        
        # 4. Критик
        _lbl = "CRITIC" if lang == 'en' else "КРИТИК"
        parts.append(f"[{_lbl}]\n{self.critic.get_checklist(lang=lang)}")
        
        if not parts:
            return ""
        
        _section = "MULTI-AGENT ANALYSIS" if lang == 'en' else "МУЛЬТИАГЕНТНЫЙ АНАЛИЗ"
        return f"\n\n[{_section}]\n" + "\n\n".join(parts)


# Глобальный оркестратор
_orchestrator = None

def get_orchestrator():
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = MultiAgentOrchestrator()
    return _orchestrator
