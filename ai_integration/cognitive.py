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

    # Модификаторы усиливающие эмоцию (weight +1.0 к близким эмоциям)
    EMOTION_INTENSIFIERS = [
        'очень', 'сильно', 'ужасно', 'невероятно', 'капец', 'жесть',
        'реально', 'прям', 'вообще', 'полностью', 'максимально',
    ]
    # Негативные маркеры (повышают вес tired/frustrated/sad/anxious)
    NEGATIVE_MARKERS = ['не могу', 'не получается', 'не выходит', 'сломалось', 'упало', 'всё плохо']
    # Позитивные маркеры (повышают вес excited)
    POSITIVE_MARKERS = ['наконец-то', 'ура', 'получилось', 'сработало', 'вышло', 'удалось']

    @staticmethod
    def detect_emotion(message):
        """Определяет эмоцию через multi-signal scoring.
        
        Вместо first-match по ключевым словам — считает score
        для КАЖДОЙ эмоции и возвращает ту, что набрала больше.
        Учитывает: keywords (1.5), intensifiers (+1.0), 
        neg/pos markers (+1.0), пунктуацию (+0.5), caps (+0.5).
        """
        msg = message.lower()
        original = message  # для проверки CAPS
        
        scores = {}  # emotion -> score
        
        for emotion, (keywords, _) in CognitiveEngine.EMOTIONS.items():
            score = 0.0
            
            # Ключевые слова: +1.5 каждое (max +3.0)
            kw_matches = sum(1 for kw in keywords if kw in msg)
            score += min(kw_matches * 1.5, 3.0)
            
            if score == 0:
                continue  # Нет ни одного ключевого слова — пропускаем
            
            # Интенсификаторы: +1.0 если есть хотя бы один
            if any(w in msg for w in CognitiveEngine.EMOTION_INTENSIFIERS):
                score += 1.0
            
            # Негативные маркеры → усиливают negative emotions
            if emotion in ('tired', 'frustrated', 'sad', 'anxious', 'confused'):
                neg_count = sum(1 for m in CognitiveEngine.NEGATIVE_MARKERS if m in msg)
                score += min(neg_count * 1.0, 2.0)
            
            # Позитивные маркеры → усиливают excited
            if emotion == 'excited':
                pos_count = sum(1 for m in CognitiveEngine.POSITIVE_MARKERS if m in msg)
                score += min(pos_count * 1.0, 2.0)
            
            # Пунктуация: !!! → усиливает excited/frustrated (+0.5)
            if emotion in ('excited', 'frustrated') and original.count('!') >= 2:
                score += 0.5
            
            # CAPS слова → усиливают frustrated (+0.5)
            if emotion == 'frustrated':
                caps_words = sum(1 for w in original.split() if w.isupper() and len(w) > 2)
                if caps_words >= 1:
                    score += 0.5
            
            # Длина сообщения: длинное эмоциональное → +0.5
            if len(msg.split()) > 15:
                score += 0.5
            
            scores[emotion] = score
        
        if not scores:
            return 'neutral'
        
        # Побеждает эмоция с максимальным score (порог >= 1.5)
        best_emotion = max(scores, key=scores.get)
        if scores[best_emotion] >= 1.5:
            return best_emotion
        return 'neutral'

    # ═══════════════════════════════════════════════════════════════
    # КЛАССИФИКАЦИЯ НАМЕРЕНИЙ
    # ═══════════════════════════════════════════════════════════════

    # Веса ключевых слов для classify_intent
    INTENT_DEFINITIONS = {
        'greeting': {
            'keywords': ['привет', 'здравствуй', 'здорово', 'хай',
                          'доброе утро', 'добрый день', 'добрый вечер',
                          'ку', 'хеллоу', 'hello', 'hi'],
            'weight': 2.0,  # приветствия однозначны
            'exact_match': True,  # только начало/полное совпадение
        },
        'farewell': {
            'keywords': ['пока', 'до свидания', 'спокойной', 'пойду спать',
                          'до завтра', 'удачи'],
            'weight': 2.0,
            'exact_match': False,
        },
        'task_management': {
            'keywords': ['задач', 'создай задачу', 'добавь задачу',
                          'запланируй', 'напомни', 'что по делам',
                          'мои задачи', 'список задач', 'удали задачу',
                          'перенеси', 'сделал', 'готово', 'выполнил'],
            'weight': 1.5,
            'exact_match': False,
        },
        'information_request': {
            'keywords': ['что такое', 'как работает', 'расскажи про',
                          'тренды', 'новости', 'исследуй',
                          'найди информацию', 'что известно',
                          'какие сейчас', 'что нового'],
            'weight': 1.5,
            'exact_match': False,
        },
        'advice_seeking': {
            'keywords': ['что думаешь', 'стоит ли', 'как лучше',
                          'посоветуй', 'что делать', 'как быть',
                          'как считаешь', 'твоё мнение', 'что выбрать',
                          'подскажи', 'порекомендуй',
                          'помоги понять', 'помоги разобраться',
                          'как устроен', 'как работает'],
            'weight': 1.5,
            'exact_match': False,
        },
        'emotional_sharing': {
            'keywords': ['устал', 'грустно', 'рад', 'злюсь',
                          'боюсь', 'переживаю', 'счастлив',
                          'бесит', 'достало', 'выгорел'],
            'weight': 1.5,
            'exact_match': False,
        },
    }

    # Структурные паттерны усиливающие intent (+1.0)
    INTENT_STRUCTURAL_SIGNALS = {
        'information_request': [r'\?$', r'расскаж', r'объясни', r'покажи'],
        'advice_seeking': [r'или\s', r'\bлучше\b', r'стоит\b', r'\?.*\?'],
        'task_management': [r'к\s+\d', r'на\s+(?:завтра|понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье)', r'в\s+\d{1,2}[:\.]\d{2}'],
        'emotional_sharing': [r'!{2,}', r'\.{3,}', r'(?:не могу|не хочу|надоело)'],
    }

    @staticmethod
    def classify_intent(message):
        """Классификация намерения через multi-signal scoring.
        
        Вместо first-match — считает score для каждого intent.
        Учитывает: keywords (weight), structural signals (+1.0),
        длину сообщения, пунктуацию.
        Побеждает intent с max score (порог >= 1.5).
        """
        msg = message.lower().strip()
        words = msg.split()
        scores = {}  # intent -> score
        
        for intent, cfg in CognitiveEngine.INTENT_DEFINITIONS.items():
            score = 0.0
            kw_weight = cfg['weight']
            
            if cfg.get('exact_match'):
                # Greeting: полное совпадение или начало
                if msg in cfg['keywords'] or any(msg.startswith(g) for g in cfg['keywords']):
                    score += kw_weight * 2  # Сильный сигнал
            else:
                # Совпадение ключевых слов: weight за каждое (max weight*2)
                kw_count = sum(1 for kw in cfg['keywords'] if kw in msg)
                score += min(kw_count * kw_weight, kw_weight * 2)
            
            if score == 0:
                continue
            
            # Структурные паттерны: +1.0 за каждый (max +2.0)
            struct_patterns = CognitiveEngine.INTENT_STRUCTURAL_SIGNALS.get(intent, [])
            struct_hits = sum(1 for p in struct_patterns if re.search(p, msg))
            score += min(struct_hits * 1.0, 2.0)
            
            # Длина сообщения: advice_seeking/information_request усиливаются длиной
            if intent in ('advice_seeking', 'information_request') and len(words) > 10:
                score += 0.5
            
            # Короткое сообщение: greeting/farewell усиливаются краткостью
            if intent in ('greeting', 'farewell') and len(words) <= 3:
                score += 1.0
            
            scores[intent] = score
        
        if not scores:
            return 'general'
        
        best_intent = max(scores, key=scores.get)
        if scores[best_intent] >= 1.5:
            return best_intent
        return 'general'

    # ═══════════════════════════════════════════════════════════════
    # ДЕТЕКТОР ТЕКУЩЕЙ ДЕЯТЕЛЬНОСТИ
    # ═══════════════════════════════════════════════════════════════

    # Паттерны активной работы (weight +1.5 phrase, +1.0 single verb)
    ACTIVE_WORK_PHRASES = [
        'работаю над', 'работаю с', 'сейчас делаю', 'прямо сейчас',
        'в процессе', 'сижу над', 'вожусь с', 'мучаюсь с',
        'готовлюсь к', 'сейчас работаю', 'сейчас занят',
    ]
    ACTIVE_WORK_VERBS = [
        'делаю', 'готовлю', 'пишу', 'занимаюсь',
        'запускаю', 'настраиваю', 'тестирую', 'отлаживаю',
        'разрабатываю', 'собираю', 'верстаю', 'кодирую', 'кодю',
        'допиливаю', 'доделываю', 'переделываю', 'чиню', 'фикшу',
        'рефакторю', 'оптимизирую', 'деплою', 'катаю', 'ковыряю',
    ]
    # Контекстные усилители: "сейчас", "прямо", "в данный момент" (+1.0)
    ACTIVE_CONTEXT_WORDS = ['сейчас', 'прямо', 'в данный момент', 'щас', 'ща']

    # Порог для detect_active_work
    ACTIVE_WORK_THRESHOLD = 1.5

    @staticmethod
    def detect_active_work(message):
        """Определяет активную работу через multi-signal scoring.
        
        Суммирует: фразы (+2.0), глаголы (+1.0), контекст (+1.0),
        первое лицо (+0.5), объект деятельности (+0.5). Порог >= 1.5.
        """
        msg = message.lower()
        words = msg.split()
        score = 0.0

        # Фразы (сильный сигнал): +2.0 каждая (max +3.0)
        phrase_hits = sum(1 for p in CognitiveEngine.ACTIVE_WORK_PHRASES if p in msg)
        score += min(phrase_hits * 2.0, 3.0)

        # Глаголы деятельности: +1.0 каждый (max +2.0)
        verb_hits = sum(1 for v in CognitiveEngine.ACTIVE_WORK_VERBS if v in msg)
        score += min(verb_hits * 1.0, 2.0)

        # Контекстные слова "сейчас/прямо": +1.0
        if any(w in msg for w in CognitiveEngine.ACTIVE_CONTEXT_WORDS):
            score += 1.0

        # Первое лицо ("я ..."): +0.5
        if re.search(r'\bя\s+\w+[юу]\b', msg):  # я делаю, я пишу
            score += 0.5

        # Объект деятельности (глагол + существительное): +0.5
        if verb_hits > 0 and len(words) >= 2:
            score += 0.5

        return score >= CognitiveEngine.ACTIVE_WORK_THRESHOLD

    # ═══════════════════════════════════════════════════════════════
    # КОГНИТИВНЫЕ ПОДСКАЗКИ ДЛЯ СИСТЕМНОГО ПРОМПТА
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def build_cognitive_hints(user_message, profile_data=None):
        """Строит контекст для мышления: наблюдения + вопросы, не команды.
        
        Предоставляет AI сигналы для рассуждения, а не предписания что делать.
        """
        emotion = CognitiveEngine.detect_emotion(user_message)
        intent = CognitiveEngine.classify_intent(user_message)
        is_active = CognitiveEngine.detect_active_work(user_message)

        observations = []

        # --- Эмоциональный сигнал ---
        emotion_labels = {
            'tired': 'усталость/выгорание',
            'excited': 'подъём/энтузиазм',
            'frustrated': 'раздражение',
            'anxious': 'тревога',
            'sad': 'грусть/тяжесть',
            'confused': 'растерянность',
        }
        if emotion != 'neutral' and emotion in emotion_labels:
            observations.append(f"Эмоция: {emotion_labels[emotion]} — что стоит за этим?")

        # --- Текущая деятельность ---
        if is_active:
            observations.append("Человек СЕЙЧАС занят делом — пришёл за помощью в текущем, не за планированием")

        # --- Профиль: что знаешь и чего не знаешь ---
        field_labels = {
            'goals': 'цели', 'skills': 'навыки', 'interests': 'интересы',
            'position': 'сфера/роль', 'city': 'город'
        }
        known = []
        unknown = []
        if profile_data:
            for k, label in field_labels.items():
                if profile_data.get(k):
                    known.append(f"{label}: {str(profile_data[k])[:40]}")
                else:
                    unknown.append(label)
        else:
            unknown = list(field_labels.values())

        if unknown:
            blind_level = "слеп" if len(unknown) >= 3 else "частично слеп"
            observations.append(
                f"Профиль: {blind_level} (не знаешь: {', '.join(unknown)}). "
                f"Без этих данных твои советы — общие фразы, а не живая помощь"
            )

        # --- Намерение ---
        intent_context = {
            'greeting': 'Приветствие — первое впечатление, покажи что ты не просто бот',
            'farewell': 'Прощание — кратко, тепло',
            'task_management': 'Работа с задачами — действуй инструментом',
            'information_request': 'Запрос информации — нужны данные',
            'advice_seeking': 'Просит совета — сформируй СВОЁ мнение',
            'emotional_sharing': 'Делится эмоциями — сначала услышь, потом помогай',
        }
        if intent in intent_context:
            observations.append(intent_context[intent])

        if not observations:
            return ""

        result = "\n\n[НАБЛЮДЕНИЯ — подумай что это значит для ЭТОГО человека СЕЙЧАС]\n"
        result += "\n".join(f"• {o}" for o in observations)
        return result

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

        # 3b. Пересказ профиля — критическая ошибка
        profile_leak_patterns = [
            r'вижу[,.]?\s*(?:что\s+)?ты\s+',
            r'ты\s+основател',
            r'судя\s+по\s+профил',
            r'из\s+твоего\s+профил',
            r'в\s+тво[еём]\s+профил',
            r'у\s+тебя\s+(?:есть\s+)?интерес',
            r'интересное\s+направление',
            r'мощное\s+сочетание',
            r'на\s+стыке\s+',
        ]
        for pattern in profile_leak_patterns:
            match = re.search(pattern, text.lower())
            if match:
                # Удаляем предложение с утечкой
                sentences = re.split(r'(?<=[.!?])\s+', text)
                cleaned = [s for s in sentences if not re.search(pattern, s.lower())]
                if cleaned:
                    text = ' '.join(cleaned)
                    issues.append(f'profile_leak:{pattern[:20]}')
                break

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

        except Exception as e:
            logger.debug(f"JSON compression fallback: {e}")

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
        """Компактная оценка ситуации — даёт AI контекст для САМОСТОЯТЕЛЬНОГО решения.
        
        Вместо предписаний "ВЫЗОВИ X" — предоставляет наблюдения о ситуации,
        чтобы AI мог рассуждать и сам решить что делать.
        """
        emotion = CognitiveEngine.detect_emotion(user_message)
        intent = CognitiveEngine.classify_intent(user_message)
        is_active = CognitiveEngine.detect_active_work(user_message)
        
        # Определяем что знаем о человеке
        missing_fields = []
        known_topics = []
        if profile_data:
            for k in ['goals', 'skills', 'interests', 'position', 'city']:
                if profile_data.get(k):
                    known_topics.append(str(profile_data[k])[:30])
                else:
                    missing_fields.append(k)
        else:
            missing_fields = ['goals', 'skills', 'interests', 'position', 'city']
        
        profile_blind = len(missing_fields) >= 3
        
        # Определяем тон
        if emotion in ('tired', 'sad', 'anxious'):
            tone = 'мягкий, с эмпатией'
        elif emotion == 'excited':
            tone = 'энергичный, поддерживающий'
        elif emotion == 'frustrated':
            tone = 'спокойный, конструктивный'
        else:
            tone = 'живой, как партнёр'
        
        # Формируем ситуацию — что видишь
        situation_parts = []
        
        if profile_blind:
            situation_parts.append(f"Почти НИЧЕГО не знаешь о человеке (нет: {', '.join(missing_fields[:3])})")
        elif missing_fields:
            field_labels = {'goals': 'цели', 'skills': 'навыки', 'interests': 'интересы', 'position': 'сфера', 'city': 'город'}
            labels = [field_labels.get(f, f) for f in missing_fields]
            situation_parts.append(f"Не знаешь: {', '.join(labels)}")
        
        if is_active:
            situation_parts.append("Человек СЕЙЧАС работает — помогай в моменте")
        
        if tasks_data:
            situation_parts.append(f"Активных задач: {len(tasks_data)}")
        else:
            situation_parts.append("Задач нет")
        
        if known_topics:
            situation_parts.append(f"Известные темы: {', '.join(known_topics[:3])}")
        
        # Формируем вопрос для размышления
        if profile_blind:
            thinking_q = "Что ты можешь узнать о человеке ЕСТЕСТВЕННО в этом разговоре?"
        elif is_active:
            thinking_q = "Чем конкретно ты можешь помочь ПРЯМО СЕЙЧАС?"
        elif intent == 'greeting' and known_topics:
            thinking_q = "Что из того что ты знаешь о человеке позволяет ОБОГАТИТЬ ответ свежими данными?"
        elif intent == 'advice_seeking':
            thinking_q = "Какое СВОЁ мнение ты можешь сформировать, и нужны ли тебе дополнительные данные?"
        elif not tasks_data and not profile_blind:
            thinking_q = "Чем человек занят СЕЙЧАС и как ты можешь быть полезен?"
        else:
            thinking_q = "Что ОДНО самое ценное ты можешь дать в этом ответе?"
        
        strategy = {
            'priority': 'profile' if profile_blind else ('help_now' if is_active else 'engage'),
            'tone': tone,
            'action': 'think',  # AI сам решает
            'why': f"Ситуация: {'; '.join(situation_parts)}. Подумай: {thinking_q}",
            'missing_fields': missing_fields,
            'extract_profile': bool(missing_fields)
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
