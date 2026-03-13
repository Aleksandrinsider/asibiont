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
                          'перенеси', 'сделал', 'готово', 'выполнил',
                          'проверил', 'уже сделал', 'уже выполнил',
                          'разобрался', 'прочитал',
                          'отправил', 'посмотрел', 'доделал',
                          'позвонил', 'закончил', 'завершил', 'закрыть',
                          'цель', 'цели', 'мои цели'],
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
    def build_cognitive_hints(user_message, profile_data=None, conversation_history=None, lang='ru'):
        """Строит карту ситуации — целостную картину для AI."""
        emotion = CognitiveEngine.detect_emotion(user_message)
        intent = CognitiveEngine.classify_intent(user_message)
        is_active = CognitiveEngine.detect_active_work(user_message)

        signals = []

        # --- Эмоциональный фон ---
        if emotion != 'neutral':
            _lbl = "Emotion" if lang == 'en' else "Эмоция"
            signals.append(f"{_lbl}: {emotion}")

        # --- Активность ---
        if is_active:
            signals.append("Working right now" if lang == 'en' else "Работает прямо сейчас")

        # --- Anti-repetition ---
        anti_rep = CognitiveEngine._build_anti_repetition(conversation_history, lang=lang)
        if anti_rep:
            signals.append(anti_rep)

        # --- Карта профиля: что знаешь целиком ---
        if profile_data:
            known_parts = []
            gaps = []
            if lang == 'en':
                field_map = {
                    'position': 'role', 'company': 'company', 'city': 'city',
                    'goals': 'goals', 'skills': 'skills', 'interests': 'interests'
                }
            else:
                field_map = {
                    'position': 'роль', 'company': 'компания', 'city': 'город',
                    'goals': 'цели', 'skills': 'навыки', 'interests': 'интересы'
                }
            for k, label in field_map.items():
                val = profile_data.get(k)
                if val:
                    known_parts.append(f"{label}: {str(val)[:50]}")
                elif k in ('goals', 'skills', 'interests', 'position', 'city'):
                    gaps.append(label)
            
            if gaps:
                _lbl = "Unknown" if lang == 'en' else "Неизвестно"
                signals.append(f"{_lbl}: {', '.join(gaps)}")
        else:
            signals.append("Profile empty — find out who this person is" if lang == 'en' else "Профиль пуст — узнай кто этот человек")

        # --- Намерение (только если не очевидно) ---
        if intent not in ('general', 'greeting', 'farewell'):
            _lbl = "Intent" if lang == 'en' else "Намерение"
            signals.append(f"{_lbl}: {intent}")

        if not signals:
            return ""

        _section = "SITUATION" if lang == 'en' else "СИТУАЦИЯ"
        return f"\n\n[{_section}]\n" + " | ".join(signals)

    # ═══════════════════════════════════════════════════════════════
    # ANTI-REPETITION & TARGETED PROFILE QUESTIONS
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def _build_anti_repetition(conversation_history, lang='ru'):
        """Анализирует последний ответ бота и возвращает антиповтор-подсказку."""
        if not conversation_history:
            return None

        last_bot_msgs = [m for m in conversation_history if m.get('role') == 'assistant']
        if not last_bot_msgs:
            return None

        last_response = last_bot_msgs[-1].get('content', '').lower()
        if not last_response:
            return None

        repeated_patterns = []

        if 'проверил задачи' in last_response or 'у вас пока нет' in last_response or 'нет активных' in last_response:
            repeated_patterns.append('"проверил задачи / нет задач"')

        if 'вижу, что вы' in last_response or 'вижу что вы' in last_response or 'вижу, что ты' in last_response:
            repeated_patterns.append('"вижу что вы..."')

        if 'интересуетесь' in last_response or 'интересуешься' in last_response:
            repeated_patterns.append('пересказ интересов')

        if 'работаете в' in last_response or 'работаешь в' in last_response:
            repeated_patterns.append('пересказ сферы работы')

        if 'могу помочь с' in last_response or 'чем могу помочь' in last_response:
            repeated_patterns.append('"могу помочь с..."')

        if ('над чем' in last_response and 
            ('работаете' in last_response or 'сосредоточен' in last_response or 'работаешь' in last_response)):
            repeated_patterns.append('"над чем работаете?"')

        if 'анализ' in last_response and 'поиск' in last_response and ('структурирован' in last_response or 'структурировани' in last_response):
            repeated_patterns.append('перечисление своих возможностей')

        # Повтор фраз: если >50% слов первого предложения совпадают
        if len(last_bot_msgs) >= 2:
            prev = last_bot_msgs[-2].get('content', '').lower()
            curr = last_response
            prev_start = set(prev.split()[:12])
            curr_start = set(curr.split()[:12])
            if prev_start and curr_start:
                overlap = len(prev_start & curr_start) / max(len(prev_start), len(curr_start))
                if overlap > 0.5:
                    repeated_patterns.append('начало ответа повторяет предыдущее из предыдущих ответов')

        # Паттерн «только вопросы» — 3+ ответа подряд заканчиваются вопросом,
        # но ни один не содержит вызова инструмента → пора ДЕЙСТВОВАТЬ
        if len(last_bot_msgs) >= 3:
            _last3 = [m.get('content', '') for m in last_bot_msgs[-3:]]
            _q_count = sum(
                1 for _m in _last3
                if _m.rstrip().endswith('?') or _m.rstrip().endswith('?')
                or (_m.count('?') >= 1 and len(_m) < 800)
            )
            _has_tool_signal = any(
                any(kw in _m.lower() for kw in ('записал', 'создал', 'добавил', 'нашёл',
                                                  'поставил', 'отправил', 'делегировал',
                                                  'запустил', 'опубликовал', 'сохранил',
                                                  'recorded', 'created', 'added', 'found',
                                                  'sent', 'delegated', 'started'))
                for _m in _last3
            )
            if _q_count >= 3 and not _has_tool_signal:
                if lang == 'en':
                    repeated_patterns.append(
                        "3 replies in a row end with questions, no actions taken — "
                        "STOP ASKING, use a tool to ACT or give concrete advice"
                    )
                else:
                    repeated_patterns.append(
                        "3 ответа подряд заканчиваются вопросом, действий нет — "
                        "ХВАТИТ СПРАШИВАТЬ, вызови инструмент или дай конкретный совет"
                    )

        if repeated_patterns:
            if lang == 'en':
                return (
                    f"⚠️ ANTI-REPEAT: in your last reply you ALREADY said: {', '.join(repeated_patterns)}. "
                    f"DO NOT REPEAT. Different structure, different question, different opening. Give NEW value"
                )
            return (
                f"⚠️ АНТИПОВТОР: в прошлом ответе ты УЖЕ говорил: {', '.join(repeated_patterns)}. "
                f"НЕ ПОВТОРЯЙ. Другая структура, другой вопрос, другое начало. Дай НОВУЮ ценность"
            )

        return None

    @staticmethod
    def _suggest_profile_question(missing_labels, conversation_history=None):
        """Returns a brief hint about what to ask. No templates — AI decides how."""
        if not missing_labels:
            return ""
        return f"Узнай: {', '.join(missing_labels[:2])}"

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

        # 2b. Заменяем /dashboard на полный URL (только если ещё не полный URL)
        text = re.sub(r'(?<!asibiont\.com)/dashboard', 'https://asibiont.com/dashboard', text)

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
            r'вижу[,.]?\s*(?:что\s+)?(?:ты|вы)\s+',
            r'(?:ты|вы)\s+основател',
            r'судя\s+по\s+профил',
            r'из\s+(?:твоего|вашего)\s+профил',
            r'в\s+(?:тво[еём]|вашем)\s+профил',
            r'у\s+(?:тебя|вас)\s+(?:есть\s+)?интерес',
            r'интересное\s+направление',
            r'мощное\s+сочетание',
            r'на\s+стыке\s+',
            r'знаю,?\s+что\s+(?:ты|вы)',
            r'(?:ты|вы)\s+(?:интересуетесь|интересуешься|увлекаешься|увлекаетесь)',
            r'(?:твой|ваш)\s+профиль\s+(?:говорит|показывает|содержит)',
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
        if len(numbered_pattern) >= 2:
            # Нумерованный список — конвертируем в связный текст
            items = numbered_pattern[:4]
            text_without_list = re.sub(r'^\d+[\.\)]\s+.+$', '', text, flags=re.MULTILINE)
            # Собираем оставшийся текст + items как предложения
            prefix = text_without_list.strip()
            joined = '. '.join(items)
            text = f"{prefix}\n\n{joined}." if prefix else f"{joined}."
            text = re.sub(r'\.\s*\.', '.', text)
            issues.append('list_converted')

        # 4b. Убираем bullet-списки (•, —, –, - в начале строки)
        # НО сохраняем строки с URL-ссылками
        bullet_lines = re.findall(r'^[•—–\-]\s+(.+)$', text, re.MULTILINE)
        bullet_lines_no_url = [l for l in bullet_lines if not re.search(r'https?://', l)]
        if len(bullet_lines_no_url) >= 2:
            # Убираем только bullet-строки БЕЗ ссылок
            def replace_bullet_no_url(m):
                content = m.group(0).lstrip('•—–- ')
                if re.search(r'https?://', content):
                    return m.group(0)  # Сохраняем строки с URL
                return content
            text_new = re.sub(r'^[•—–\-]\s+.+$', replace_bullet_no_url, text, flags=re.MULTILINE)
            if text_new != text:
                # Собираем не-URL items в текст
                non_url_items = [l for l in bullet_lines if not re.search(r'https?://', l)][:6]
                url_items = [l for l in bullet_lines if re.search(r'https?://', l)]
                text_without_bullets = re.sub(r'^[•—–\-]\s+.+$', '', text, flags=re.MULTILINE)
                prefix = text_without_bullets.strip()
                joined = ', '.join(non_url_items)
                url_section = '\n'.join(url_items) if url_items else ''
                text = f"{prefix} {joined}." if prefix else f"{joined}."
                if url_section:
                    text = f"{text}\n\n{url_section}"
                text = re.sub(r'\.\s*\.', '.', text)
                text = re.sub(r'\s{2,}', ' ', text)
                issues.append('bullets_converted')

        # 4c. Убираем emoji-заголовки (🔍 Анализ:, 💡 Выводы:, etc.)
        text = re.sub(r'^[🔍💡🎯✅📎📊📰🚀⚡️🔥💰📋]\s*[А-Яа-яA-Za-z\s]+:\s*\n?', '', text, flags=re.MULTILINE)
        # Убираем остаточные секционные заголовки (НО НЕ "Источники" — там ссылки)
        text = re.sub(r'^(Ключевые выводы|Возможности|Рекомендации|Результаты исследования):\s*\n?', '', text, flags=re.MULTILINE)

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
        # Финальная нормализация: \n\n → \n (мессенджер-стиль без лишних пропусков)
        text = re.sub(r'\n\n', '\n', text)

        # 6. Обрезаем ЕСЛИ ответ слишком длинный
        # Увеличиваем лимит если есть URL-ссылки (чтобы не обрезать их)
        has_urls = bool(re.search(r'https?://', text))
        max_len = 1500 if has_urls else 800
        cut_at = 1400 if has_urls else 700
        if len(text) > max_len:
            cut = text[:cut_at]
            last_end = max(cut.rfind('.'), cut.rfind('!'), cut.rfind('?'))
            if last_end > 300:
                text = cut[:last_end + 1]
            else:
                text = cut
            # Если обрезали, но URL остался в хвосте — добавляем его
            if has_urls and not re.search(r'https?://', text):
                # Достаём все URL из оригинала
                urls_in_original = re.findall(r'(?:.*?\s)?(https?://\S+)', response)
                if urls_in_original:
                    url_lines = '\n'.join(urls_in_original[:5])
                    text = f"{text}\n\n{url_lines}"
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
    def compress_tool_result(result_json, max_length=2000):
        """Сжимает результат tool call для экономии контекстного окна.
        
        - Списки: оставляет только ключевые поля (title, status, id)
        - Словари: обрезает длинные значения, убирает ссылки
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
                                     'username', 'query', 'summary',
                                     'key_insights', 'opportunities',
                                     'action_plan', 'link', 'url', 'source')
                        })
                    else:
                        compressed.append(str(item)[:100])
                return json.dumps(compressed, ensure_ascii=False)[:max_length]

            if isinstance(data, dict):
                compressed = {}
                # Убираем поля, которые не нужны AI для ответа
                skip_keys = {'cached', 'success'}
                for k, v in data.items():
                    if k in skip_keys:
                        continue
                    if isinstance(v, str) and len(v) > 500:
                        compressed[k] = v[:500] + '...'
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
    def plan_response_strategy(user_message, profile_data, tasks_data, lang='ru'):
        """Оценка ситуации — целостная картина, не шаблон.
        
        Собирает контекст для AI: кто человек, что происходит, что важно.
        AI сам решает что делать на основе этой картины.
        """
        emotion = CognitiveEngine.detect_emotion(user_message)
        is_active = CognitiveEngine.detect_active_work(user_message)
        
        # --- Тон ---
        if lang == 'en':
            if emotion in ('tired', 'sad', 'anxious'):
                tone = 'soft but professional'
            elif emotion == 'frustrated':
                tone = 'constructive — help solve'
            elif emotion == 'excited':
                tone = 'energetic'
            else:
                tone = 'professional'
        else:
            if emotion in ('tired', 'sad', 'anxious'):
                tone = 'мягкий но деловой'
            elif emotion == 'frustrated':
                tone = 'конструктивный — помоги решить'
            elif emotion == 'excited':
                tone = 'энергичный'
            else:
                tone = 'деловой'
        
        # --- Собираем факты о ситуации ---
        facts = []
        
        # Что знаем
        if profile_data:
            known = {k: v for k, v in profile_data.items() 
                     if v and k in ('position', 'company', 'city', 'goals', 'skills', 'interests')}
            missing = [k for k in ('position', 'goals', 'skills') if k not in known]
            if missing:
                _lbl = "Unknown" if lang == 'en' else "Неизвестно"
                facts.append(f"{_lbl}: {', '.join(missing)}")
        else:
            facts.append("Profile empty" if lang == 'en' else "Профиль пуст")
        
        # Задачи
        if tasks_data:
            _lbl = "Tasks" if lang == 'en' else "Задач"
            facts.append(f"{_lbl}: {len(tasks_data)}")
        
        # Активная работа
        if is_active:
            facts.append("Working now" if lang == 'en' else "Работает сейчас")
        
        _default = 'normal conversation' if lang == 'en' else 'обычный разговор'
        situation = '; '.join(facts) if facts else _default
        
        return {
            'tone': tone,
            'why': situation,
        }

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
            'human_like': 'high' if len(response.split()) < 100 else 'medium'
        }
        
        logger.info(f"[REFLECTION] {reflection}")
        return reflection
