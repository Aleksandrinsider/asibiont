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
        """Строит когнитивный контекст для инъекции в системный промпт.
        
        Даёт агенту «подсказку» как реагировать, основываясь на
        эмоции и намерении пользователя.
        """
        emotion = CognitiveEngine.detect_emotion(user_message)
        intent = CognitiveEngine.classify_intent(user_message)

        hints = []

        # Эмоциональная подсказка
        if emotion != 'neutral' and emotion in CognitiveEngine.EMOTIONS:
            hints.append(f"⚡ {CognitiveEngine.EMOTIONS[emotion][1]}")

        # Подсказка по намерению
        intent_hints = {
            'greeting': '🎯 Приветствие → будь проактивным, дай ценность '
                        '(research/trends по интересам), не спрашивай «чем помочь».',
            'farewell': '🎯 Прощание → кратко, тепло (2 предложения max). '
                        'Напомни про завтрашние планы если есть.',
            'emotional_sharing': '🎯 Делится эмоциями → ЭМПАТИЯ ПЕРВАЯ. '
                                 'Потом один вопрос. Без советов, без задач.',
            'advice_seeking': '🎯 Просит совет → дай СВОЁ мнение с аргументами. '
                              'Не «зависит от», а «я бы на твоём месте...».',
            'information_request': '🎯 Информационный запрос → используй '
                                   'research_topic для экспертного ответа с данными.',
        }
        if intent in intent_hints:
            hints.append(intent_hints[intent])

        if not hints:
            return ""

        return "\n\n[КОГНИТИВНЫЙ АНАЛИЗ]\n" + "\n".join(hints)

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
    # ИЗВЛЕЧЕНИЕ ТЕМ ИЗ ИСТОРИИ (без API вызова)
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def extract_conversation_topics(messages, top_n=8):
        """Извлекает ключевые темы из списка сообщений.
        
        Использует частотный анализ без AI API вызова.
        Возвращает список ключевых слов.
        """
        all_text = " ".join(m.get('content', '')[:300] for m in messages)

        stop = {
            'что', 'как', 'где', 'это', 'для', 'при', 'или', 'если', 'так',
            'уже', 'ещё', 'еще', 'будет', 'был', 'мне', 'тебе', 'нас', 'них',
            'она', 'они', 'его', 'можно', 'нужно', 'просто', 'могу', 'хочу',
            'есть', 'нет', 'всё', 'все', 'тоже', 'надо', 'вот', 'тут',
            'привет', 'пока', 'давай', 'ладно', 'окей', 'будем', 'только',
            'потом', 'сейчас', 'когда', 'чтобы', 'через', 'после', 'перед',
            'which', 'that', 'this', 'with', 'have', 'from', 'your', 'will',
            'about', 'would', 'could', 'should', 'been', 'more', 'very',
        }

        words = re.findall(r'\b[а-яёa-z]{4,}\b', all_text.lower())
        filtered = [w for w in words if w not in stop]

        counter = Counter(filtered)
        return [word for word, _ in counter.most_common(top_n)]
