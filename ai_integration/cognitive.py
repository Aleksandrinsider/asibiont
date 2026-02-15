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
            'greeting': '🎯 ПРИВЕТСТВИЕ: Профиль пустой? → Заполнить. Задач нет? → Проактивность с research.',
            'farewell': '🎯 ПРОЩАНИЕ: Коротко, тепло. Напомнить о планах.',
            'task_management': '🎯 ЗАДАЧИ: Только предлагать, не создавать. Проверить конфликты.',
            'information_request': '🎯 ИНФОРМАЦИЯ: Research + анализ, не сырые факты. Понять ПОЧЕМУ спрашивает.',
            'advice_seeking': '🎯 СОВЕТ: Моё мнение с аргументами. Не варианты, а выбор.',
            'emotional_sharing': '🎯 ЭМОЦИИ: Эмпатия ПЕРВАЯ. Слушать, не решать. Один вопрос.',
            'general': '🎯 ОБЩЕЕ: Профиль? Задачи? Проактивность по интересам.'
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

        # 1. Убираем шаблонные начала
        bad_starts = [
            'Отлично!', 'Отлично,', 'Отлично ',
            'Конечно!', 'Конечно,', 'Конечно ',
            'Хорошо!', 'Хорошо,', 'Хорошо ',
            'Замечательно!', 'Замечательно,',
            'Понял!', 'Понял,', 'Понял.',
            'Правильно!', 'Правильно,',
            'Заметил!', 'Заметил,',
            'Принял!', 'Принял,',
            'Здорово!', 'Здорово,',
            'Классно!', 'Классно,',
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

        # 3. Убираем фразы автоответчика (удаляем предложение с ними)
        autoresponder = [
            'чем могу помочь', 'что-то беспокоит', 'есть что-то другое',
            'чем помочь', 'как я могу помочь', 'чем-то помочь',
            'могу ли я чем-то', 'если нужна помощь', 'обращайся если что',
            'всегда на связи',
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

        # 5. Обрезаем если слишком длинный (>1200 символов)
        if len(text) > 1200:
            cut = text[:1000]
            last_end = max(cut.rfind('.'), cut.rfind('!'), cut.rfind('?'))
            if last_end > 400:
                text = cut[:last_end + 1]
            else:
                text = cut
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
        
        strategy = {
            'priority': 'profile' if not profile_data else ('tasks' if not tasks_data else 'proactive'),
            'tone': 'empathetic' if emotion in ['tired', 'sad', 'anxious'] else 'enthusiastic' if emotion == 'excited' else 'normal',
            'action': 'ask_profile' if not profile_data else ('research_topic' if intent == 'information_request' else 'chat'),
            'why': 'Заполнить профиль для персонализации' if not profile_data else 'Дать ценность без задач'
        }
        
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
