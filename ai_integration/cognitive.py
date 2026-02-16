"""
Когнитивный движок — intelligent layer поверх базового tool calling.

Возможности:
- Эмоциональный интеллект (detect_emotion)
- Классификация намерений (classify_intent)
- Когнитивные подсказки для системного промпта (build_cognitive_hints)
- Программная валидация ответов (validate_response)
- Smart memory retrieval (retrieve_relevant_memories)
- Сжатие контекстного окна (compress_tool_result)
- Извлечение тем из истории (extract_conversation_topics)

Все функции lightweight — без дополнительных API вызовов.
"""

import re
import json
import logging
from collections import Counter

logger = logging.getLogger(__name__)


class CognitiveEngine:
    """Когнитивные функции агента: эмоции, намерения, валидация, сжатие."""

    # ═══════════════════════════════════════════════════════════════
    # ЭМОЦИОНАЛЬНЫЙ ИНТЕЛЛЕКТ
    # ═══════════════════════════════════════════════════════════════

    EMOTIONS = {
        'tired': (
            ['устал', 'утомил', 'вымота', 'нет сил', 'выгора', 'глаза болят',
             'спать хочу', 'измотан', 'еле живой', 'без сил'],
            'Пользователь устал/выгорел. Эмпатия первая, не нагружай информацией.'
        ),
        'excited': (
            ['круто', 'офигенно', 'вау', 'класс', 'супер', 'ура',
             'получилось', 'заработало', 'ого', 'обалдеть'],
            'Пользователь на подъёме! Поддержи энтузиазм, предложи следующий шаг.'
        ),
        'frustrated': (
            ['бесит', 'задолба', 'не работает', 'опять', 'достало',
             'ненавижу', 'фигня', 'чёрт', 'блин'],
            'Пользователь раздражён. Сначала эмпатия, потом решение.'
        ),
        'anxious': (
            ['волнуюсь', 'переживаю', 'страшно', 'не уверен', 'боюсь',
             'нервничаю', 'стрёмно', 'тревожн'],
            'Пользователь тревожится. Дай опору, разложи по шагам.'
        ),
        'sad': (
            ['грустно', 'тоскливо', 'одиноко', 'плохо', 'тяжело на душе',
             'депресси', 'тяжко', 'паршиво'],
            'Человеку тяжело. Будь мягким, поддержи без навязчивости.'
        ),
        'confused': (
            ['не понимаю', 'запутался', 'что делать', 'не знаю как',
             'голова кругом', 'растерян', 'хз', 'без понятия'],
            'Пользователь запутался. Помоги разобраться просто и чётко.'
        ),
    }

    @staticmethod
    def detect_emotion(message):
        """Определяет эмоциональное состояние пользователя."""
        msg = message.lower()
        for emotion, (keywords, _) in CognitiveEngine.EMOTIONS.items():
            if any(kw in msg for kw in keywords):
                return emotion
        return 'neutral'

    # ═══════════════════════════════════════════════════════════════
    # КЛАССИФИКАЦИЯ НАМЕРЕНИЙ
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def classify_intent(message):
        """Быстрая классификация намерения пользователя."""
        msg = message.lower().strip()

        intents = [
            ('greeting', ['привет', 'здравствуй', 'здорово', 'хай',
                           'доброе утро', 'добрый день', 'добрый вечер',
                           'ку', 'хеллоу', 'hello', 'hi']),
            ('farewell', ['пока', 'до свидания', 'спокойной', 'пойду спать',
                           'до завтра', 'удачи']),
            ('task_management', ['задач', 'создай задачу', 'добавь задачу',
                                  'запланируй', 'напомни', 'что по делам',
                                  'мои задачи', 'список задач']),
            ('information_request', ['что такое', 'как работает', 'расскажи про',
                                      'тренды', 'новости', 'исследуй',
                                      'найди информацию', 'что известно',
                                      'какие сейчас', 'что нового']),
            ('advice_seeking', ['что думаешь', 'стоит ли', 'как лучше',
                                 'посоветуй', 'что делать', 'как быть',
                                 'как считаешь', 'твоё мнение']),
            ('emotional_sharing', ['устал', 'грустно', 'рад', 'злюсь',
                                    'боюсь', 'переживаю', 'счастлив',
                                    'бесит', 'достало', 'выгорел']),
        ]

        for intent, keywords in intents:
            if intent == 'greeting':
                if msg in keywords or any(msg.startswith(g) for g in keywords):
                    return intent
            else:
                if any(kw in msg for kw in keywords):
                    return intent

        return 'general'

    # ═══════════════════════════════════════════════════════════════
    # КОГНИТИВНЫЕ ПОДСКАЗКИ ДЛЯ СИСТЕМНОГО ПРОМПТА
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def build_cognitive_hints(user_message):
        """Строит глубокий когнитивный анализ для инъекции в системный промпт.
        
        Добавляет внутренний монолог: эмоции, намерение, стратегия ответа.
        """
        emotion = CognitiveEngine.detect_emotion(user_message)
        intent = CognitiveEngine.classify_intent(user_message)

        hints = []

        # Эмоциональный анализ
        if emotion != 'neutral' and emotion in CognitiveEngine.EMOTIONS:
            hints.append(f"⚡ ЭМОЦИЯ: {CognitiveEngine.EMOTIONS[emotion][1]}")

        # Анализ намерения
        intent_hints = {
            'greeting': '🎯 ПРИВЕТСТВИЕ: Развёрнутое приветствие + мысль/наблюдение по теме + вопрос. НЕ пересказывай профиль как досье. Есть задачи/цели → спроси о прогрессе. Есть интересы → подкинь идею. Минимум 4-5 предложений.',
            'farewell': '🎯 ПРОЩАНИЕ: Коротко, тепло. Напомнить о планах.',
            'task_management': '🎯 ЗАДАЧИ: Только предлагать, не создавать. Проверить конфликты.',
            'information_request': '🎯 ИНФОРМАЦИЯ: Research + анализ, но ОДИН раз за 3-4 сообщения. Не сырые факты.',
            'advice_seeking': '🎯 СОВЕТ: СНАЧАЛА задай 1-2 уточняющих вопроса (что пробовал? какие ограничения? кто аудитория?). НЕ давай советы сразу — собери контекст. Потом дай ОДНУ рекомендацию с аргументами.',
            'emotional_sharing': '🎯 ЭМОЦИИ: Эмпатия ПЕРВАЯ. Слушать, не решать. Один вопрос.',
            'general': '🎯 ОБЩЕЕ: Если пользователь описывает проблему — СНАЧАЛА задай уточняющие вопросы, потом советуй. Тихо обновляй профиль если узнал новое.'
        }
        if intent in intent_hints:
            hints.append(intent_hints[intent])

        # Стратегия ответа
        hints.append("🧠 СТРАТЕГИЯ: Одно действие максимум ценности. Человечность: юмор/спор/память.")

        if not hints:
            return ""

        return "\n\n[КОГНИТИВНЫЙ АНАЛИЗ — ДУМАЙ ГЛУБОКО]\n" + "\n".join(hints)

    # ═══════════════════════════════════════════════════════════════
    # ПРОГРАММНАЯ ВАЛИДАЦИЯ ОТВЕТА (Quality Gate)
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def validate_response(response, user_message=None):
        """Программная проверка и исправление ответа перед отправкой.
        
        Returns: (cleaned_text, issues_list)
        """
        text = response
        issues = []

        # 1. Убираем ТОЛЬКО сухие шаблонные начала (НЕ эмоциональные)
        bad_starts = [
            'Понял!', 'Понял,', 'Понял.',
            'Принял!', 'Принял,',
        ]
        for bs in bad_starts:
            if text.startswith(bs):
                text = text[len(bs):].lstrip()
                issues.append(f'bad_start:{bs.strip()}')
                break

        # 2. Убираем markdown (Telegram его не рендерит нормально)
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'(?<!\*)\*([^*]+?)\*(?!\*)', r'\1', text)
        text = re.sub(r'__(.+?)__', r'\1', text)
        text = re.sub(r'`([^`]+?)`', r'\1', text)

        # 3. Убираем фразы автоответчика (ТОЛЬКО самые шаблонные)
        autoresponder = [
            'чем могу помочь', 'как я могу помочь', 'чем-то помочь',
            'могу ли я чем-то',
        ]
        for phrase in autoresponder:
            if phrase in text.lower():
                sentences = re.split(r'(?<=[.!?])\s+', text)
                cleaned = [s for s in sentences if phrase not in s.lower()]
                if cleaned:
                    text = ' '.join(cleaned)
                    issues.append(f'autoresponder:{phrase}')

        # 4. Убираем нумерованные списки (конвертируем в текст)
        # "1. Сделай X\n2. Потом Y" → "Сначала сделай X. Потом Y."
        numbered_pattern = re.findall(r'^\d+[\.\)]\s+(.+)$', text, re.MULTILINE)
        if len(numbered_pattern) >= 3:
            # Слишком много пунктов — это список, конвертируем
            items = numbered_pattern[:4]
            text_without_list = re.sub(r'^\d+[\.\)]\s+.+$', '', text, flags=re.MULTILINE)
            # Собираем оставшийся текст + items как предложения
            prefix = text_without_list.strip()
            joined = '. '.join(items)
            text = f"{prefix}\n\n{joined}." if prefix else f"{joined}."
            text = re.sub(r'\.\s*\.', '.', text)
            issues.append('list_converted')

        # 5. Убираем пустые секции ("Вот почему:" без содержимого)
        # Паттерн: заголовок с двоеточием, за которым сразу следующий заголовок или конец
        empty_section_pattern = re.compile(
            r'([^\n]*:\s*)\n\s*\n\s*(?=[А-ЯA-Z])', re.MULTILINE
        )
        prev = text
        text = empty_section_pattern.sub('', text)
        if text != prev:
            issues.append('empty_sections_removed')

        # 5b. Убираем множественные пустые строки (оставляем максимум одну)
        text = re.sub(r'\n{3,}', '\n\n', text)

        # 6. Обрезаем ТОЛЬКО если ответ неоправданно огромный (>2000 символов)
        if len(text) > 2000:
            cut = text[:1800]
            last_end = max(cut.rfind('.'), cut.rfind('!'), cut.rfind('?'))
            if last_end > 800:
                text = cut[:last_end + 1]
            else:
                text = cut[:1500]
            issues.append('truncated')

        return text.strip(), issues

    # ═══════════════════════════════════════════════════════════════
    # SMART MEMORY RETRIEVAL
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def retrieve_relevant_memories(memory_text, current_message, max_lines=6):
        """Извлекает из памяти только релевантные записи.
        
        Использует keyword overlap + recency scoring.
        Если записей мало, возвращает всё.
        """
        if not memory_text:
            return ""

        lines = [l.strip() for l in memory_text.strip().split('\n') if l.strip()]
        if len(lines) <= max_lines:
            return memory_text

        # Ключевые слова из текущего сообщения
        msg_words = set(
            w.lower() for w in re.findall(r'\b\w{3,}\b', current_message)
        )

        # Скоринг: overlap * 3 + recency * 2
        scored = []
        for i, line in enumerate(lines):
            line_words = set(w.lower() for w in re.findall(r'\b\w{3,}\b', line))
            overlap = len(msg_words & line_words)
            recency = i / len(lines)  # 0..1, новые = выше
            score = overlap * 3 + recency * 2
            scored.append((score, line))

        scored.sort(key=lambda x: -x[0])
        return "\n".join(line for _, line in scored[:max_lines])

    # ═══════════════════════════════════════════════════════════════
    # СЖАТИЕ РЕЗУЛЬТАТОВ ИНСТРУМЕНТОВ
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def compress_tool_result(result_json, max_length=1200):
        """Сжимает результат tool call для экономии контекстного окна.
        
        - Списки: оставляет только ключевые поля (title, status, id)
        - Словари: обрезает длинные значения
        - Fallback: простая обрезка
        """
        if len(result_json) <= max_length:
            return result_json

        try:
            data = json.loads(result_json)

            if isinstance(data, list):
                compressed = []
                for item in data[:10]:
                    if isinstance(item, dict):
                        compressed.append({
                            k: v for k, v in item.items()
                            if k in ('title', 'status', 'id', 'name',
                                     'description', 'progress', 'city',
                                     'username', 'query', 'summary')
                        })
                    else:
                        compressed.append(str(item)[:100])
                return json.dumps(compressed, ensure_ascii=False)[:max_length]

            if isinstance(data, dict):
                compressed = {}
                for k, v in data.items():
                    if isinstance(v, str) and len(v) > 300:
                        compressed[k] = v[:300] + '...'
                    elif isinstance(v, list) and len(v) > 5:
                        compressed[k] = v[:5]
                    else:
                        compressed[k] = v
                return json.dumps(compressed, ensure_ascii=False)[:max_length]

        except Exception:
            pass

        return result_json[:max_length]

    # ═══════════════════════════════════════════════════════════════
    # ИЗВЛЕЧЕНИЕ ТЕМ ИЗ ИСТОРИИ
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def extract_conversation_topics(messages, top_n=5):
        """Извлекает ключевые темы из старых сообщений без API вызова.
        
        Использует частотный анализ значимых слов.
        Args:
            messages: list of {role, content} dicts
            top_n: сколько тем вернуть
        Returns: list[str] — список тем
        """
        stop_words = {
            'что', 'как', 'где', 'когда', 'почему', 'зачем', 'кто',
            'это', 'так', 'вот', 'уже', 'ещё', 'еще', 'тоже', 'очень',
            'можно', 'нужно', 'надо', 'будет', 'было', 'есть', 'нет',
            'для', 'при', 'про', 'или', 'если', 'чтобы', 'потому',
            'привет', 'пока', 'спасибо', 'пожалуйста', 'хорошо',
            'давай', 'ладно', 'окей', 'ага', 'угу', 'мне', 'тебе',
            'меня', 'тебя', 'мой', 'твой', 'его', 'она', 'они',
            'все', 'всё', 'этот', 'эта', 'эти', 'тот', 'там', 'тут',
            'одна', 'один', 'два', 'три', 'только', 'просто', 'вообще',
        }
        
        all_words = []
        for msg in messages:
            content = msg.get('content', '') if isinstance(msg, dict) else str(msg)
            words = re.findall(r'\b[а-яёa-z]{4,}\b', content.lower())
            all_words.extend(w for w in words if w not in stop_words)
        
        if not all_words:
            return []
        
        counts = Counter(all_words)
        return [word for word, _ in counts.most_common(top_n)]

    # ═══════════════════════════════════════════════════════════════
    # ПЛАНИРОВАНИЕ И РЕФЛЕКСИЯ
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def plan_response_strategy(user_message, profile_data, tasks_data):
        """Планирует стратегию ответа: что сказать, какие инструменты, почему.
        
        Returns: dict with strategy hints.
        """
        emotion = CognitiveEngine.detect_emotion(user_message)
        intent = CognitiveEngine.classify_intent(user_message)
        
        # Определяем приоритет действия
        if not profile_data:
            priority = 'profile'
            action = 'ask_profile'
            why = 'Заполнить профиль для персонализации'
        elif intent == 'information_request':
            priority = 'research'
            action = 'research_topic'
            why = 'Пользователь явно запрашивает информацию'
        elif intent == 'advice_seeking':
            priority = 'opinion'
            action = 'give_opinion'
            why = 'Дай СВОЁ мнение, НЕ делай research — ты эксперт'
        elif not tasks_data:
            priority = 'tasks'
            action = 'suggest_task'
            why = 'Предложи задачу на основе контекста'
        else:
            priority = 'proactive'
            action = 'chat'
            why = 'Дай ценность из контекста'
        
        # Определяем тон
        if emotion in ['tired', 'sad', 'anxious']:
            tone = 'empathetic'
        elif emotion == 'excited':
            tone = 'enthusiastic'
        elif emotion == 'frustrated':
            tone = 'calm_supportive'
        else:
            tone = 'normal'
        
        strategy = {
            'priority': priority,
            'tone': tone,
            'action': action,
            'why': why,
            'extract_profile': intent == 'general' and not all(profile_data.get(k) for k in ['goals', 'skills', 'interests', 'position'])
        }
        
        # Добавляем hint про извлечение данных в профиль
        if strategy['extract_profile']:
            strategy['why'] += '. ВАЖНО: если пользователь рассказывает о себе — тихо обновляй профиль через update_profile'
        
        return strategy

    @staticmethod
    def reflect_on_response(user_message, response, tools_used):
        """Рефлексия после ответа: что было хорошо, что улучшить.
        
        Для будущего обучения (сейчас логируется).
        """
        issues = CognitiveEngine.validate_response(response, user_message)[1]
        reflection = {
            'quality': 'good' if not issues else 'needs_improvement',
            'issues': issues,
            'tools_effective': len(tools_used) > 0,
            'human_like': 'high' if len(response.split()) < 50 else 'medium'
        }
        
        logger.info(f"[REFLECTION] {reflection}")
        return reflection
