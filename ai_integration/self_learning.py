"""
Самообучение — feedback loop для улучшения агента.

Что делает:
1. Анализирует каждый ответ на качество
2. Собирает паттерны: что работает, что нет
3. Адаптирует поведение под пользователя
4. Хранит preference model в БД
5. Распознаёт повторяющиеся темы (пользователь спрашивает одно и то же)
6. Извлекает ключевые факты из диалогов

Метрики качества:
- response_length: средняя длина ответа
- tools_per_turn: сколько tools используется
- user_satisfaction: на основе реакции (если есть)
- pattern_success_rate: % успешных паттернов
- topic_repeat_count: сколько раз пользователь возвращался к теме
"""

import json
import logging
import re
from datetime import datetime, timezone
from collections import defaultdict

logger = logging.getLogger(__name__)


class SelfLearner:
    """Самообучающийся модуль агента с персистентностью в БД."""

    _SAVE_EVERY_N_TURNS = 5  # сохранять в БД каждые N обменов

    # Темы для распознавания повторяющихся запросов
    _TOPIC_KEYWORDS: dict[str, list[str]] = {
        'финансы_инвестиции': ['инвестици', 'акци', 'облигаци', 'портфель', 'диверсифициру', 'вложени'],
        'криптовалюта': ['биткоин', 'крипт', 'eth', 'btc', 'токен', 'defi', 'binance', 'арбитраж'],
        'нефть_сырье': ['нефт', 'brent', 'wti', 'золот', 'сырь', 'commodit'],
        'стартап_бизнес': ['стартап', 'бизнес', 'запуск', 'компани', 'продукт', 'монетизаци'],
        'маркетинг_продажи': ['маркетинг', 'продаж', 'воронк', 'лид', 'конверси', 'таргет'],

        'здоровье_фитнес': ['здоровь', 'фитнес', 'тренировк', 'спорт', 'бег', 'диет', 'питани'],
        'обучение_развитие': ['учит', 'курс', 'обучени', 'книг', 'развити', 'навык'],
        'карьера_работа': ['карьер', 'работ', 'резюме', 'собеседовани', 'увольнени', 'повышени'],
        'психология_мотивация': ['психолог', 'мотиваци', 'выгорани', 'депресси', 'тревог', 'стресс'],
        'отношения_общение': ['отношени', 'общени', 'друз', 'семь', 'любов', 'конфликт'],
        'путешествия': ['путешестви', 'отпуск', 'поездк', 'билет', 'отель', 'виза'],
        'технологии_ai': ['нейросет', 'ai', 'ии', 'chatgpt', 'ml', 'gpt', 'агент'],
        'недвижимость': ['квартир', 'дом', 'недвижим', 'ипотек', 'аренд'],
        'авто': ['машин', 'авто', 'покупк', 'страховк', 'ремонт'],
    }

    def __init__(self):
        # Метрики по пользователям
        self.user_metrics = defaultdict(lambda: {
            'total_turns': 0,
            'total_response_length': 0,
            'tools_used_count': 0,
            'tools_histogram': defaultdict(int),
            'tool_success': defaultdict(int),   # tool_name → кол-во успешных вызовов
            'tool_fail': defaultdict(int),       # tool_name → кол-во ошибок
            'tool_error_messages': defaultdict(list),  # tool_name → последние ошибки
            'successful_patterns': [],
            'failed_patterns': [],
            'preferences': {},
            'last_emotions': [],
            'conversation_style': 'normal',  # brief/normal/detailed
            # User Model (новое)
            'domains_mentioned': defaultdict(int),  # domain → count
            'topic_repeat_count': defaultdict(int),  # topic → how many times re-asked
            'last_topic_mentions': {},  # topic → last timestamp
            'key_facts': [],  # важные факты о пользователе
            'avg_user_message_len': 0,
            'preferred_tools_by_domain': defaultdict(lambda: defaultdict(int)),
            'tool_fail_reasons': defaultdict(lambda: defaultdict(int)),  # tool → reason → count
        })

        # Глобальные паттерны (что работает для всех)
        self.global_patterns = {
            'best_tools_by_intent': {},
            'avg_response_length': 300,
            'common_issues': [],
        }

        # Трекер: сколько turns с последнего сохранения (per user)
        self._unsaved_turns: dict[int, int] = defaultdict(int)
        # Кеш загруженных из БД пользователей (чтобы не грузить повторно)
        self._loaded_users: set[int] = set()

    def _load_from_db(self, user_id: int):
        """Загрузить метрики из БД при первом обращении."""
        if user_id in self._loaded_users:
            return
        self._loaded_users.add(user_id)
        try:
            from models import Session as _SL, User as _UL
            _s = _SL()
            try:
                _u = _s.query(_UL).filter_by(telegram_id=user_id).first()
                if _u and _u.long_term_memory:
                    _data = json.loads(_u.long_term_memory)
                    _sl = _data.get('self_learning')
                    if _sl and isinstance(_sl, dict):
                        m = self.user_metrics[user_id]
                        m['total_turns'] = _sl.get('total_turns', 0)
                        m['total_response_length'] = _sl.get('total_response_length', 0)
                        m['tools_used_count'] = _sl.get('tools_used_count', 0)
                        m['tools_histogram'] = defaultdict(int, _sl.get('tools_histogram', {}))
                        m['successful_patterns'] = _sl.get('successful_patterns', [])[-20:]
                        m['failed_patterns'] = _sl.get('failed_patterns', [])[-10:]
                        m['preferences'] = _sl.get('preferences', {})
                        m['tool_success'] = defaultdict(int, _sl.get('tool_success', {}))
                        m['tool_fail'] = defaultdict(int, _sl.get('tool_fail', {}))
                        m['tool_error_messages'] = defaultdict(list, {k: v[-5:] for k, v in (_sl.get('tool_error_messages', {}) or {}).items()})
                        m['last_emotions'] = _sl.get('last_emotions', [])[-10:]
                        m['conversation_style'] = _sl.get('conversation_style', 'normal')
                        m['domains_mentioned'] = defaultdict(int, _sl.get('domains_mentioned', {}))
                        m['topic_repeat_count'] = defaultdict(int, _sl.get('topic_repeat_count', {}))
                        m['last_topic_mentions'] = _sl.get('last_topic_mentions', {})
                        m['key_facts'] = _sl.get('key_facts', [])[-15:]
                        m['avg_user_message_len'] = _sl.get('avg_user_message_len', 0)
                        m['preferred_tools_by_domain'] = defaultdict(
                            lambda: defaultdict(int),
                            {k: defaultdict(int, v) for k, v in (_sl.get('preferred_tools_by_domain', {}) or {}).items()}
                        )
                        m['tool_fail_reasons'] = defaultdict(
                            lambda: defaultdict(int),
                            {k: defaultdict(int, v) for k, v in (_sl.get('tool_fail_reasons', {}) or {}).items()}
                        )
                        logger.info(f"[LEARN] Loaded {m['total_turns']} turns from DB for user {user_id}")
            finally:
                _s.close()
        except Exception as e:
            logger.debug(f"[LEARN] DB load failed for user {user_id}: {e}")

    def _save_to_db(self, user_id: int):
        """Сохранить метрики в long_term_memory (поле self_learning)."""
        try:
            from models import Session as _SS, User as _US
            _s = _SS()
            try:
                _u = _s.query(_US).filter_by(telegram_id=user_id).first()
                if not _u:
                    return
                _existing = {}
                if _u.long_term_memory:
                    try:
                        _existing = json.loads(_u.long_term_memory)
                    except Exception:
                        _existing = {}
                m = self.user_metrics[user_id]
                _existing['self_learning'] = {
                    'total_turns': m['total_turns'],
                    'total_response_length': m['total_response_length'],
                    'tools_used_count': m['tools_used_count'],
                    'tools_histogram': dict(m['tools_histogram']),
                    'tool_success': dict(m['tool_success']),
                    'tool_fail': dict(m['tool_fail']),
                    'tool_error_messages': {k: list(v)[-5:] for k, v in m['tool_error_messages'].items()},
                    'successful_patterns': m['successful_patterns'][-20:],
                    'failed_patterns': m['failed_patterns'][-10:],
                    'preferences': m['preferences'],
                    'last_emotions': m['last_emotions'][-10:],
                    'conversation_style': m['conversation_style'],
                    'domains_mentioned': dict(m['domains_mentioned']),
                    'topic_repeat_count': dict(m['topic_repeat_count']),
                    'last_topic_mentions': m['last_topic_mentions'],
                    'key_facts': m['key_facts'][-15:],
                    'avg_user_message_len': m['avg_user_message_len'],
                    'preferred_tools_by_domain': {k: dict(v) for k, v in m['preferred_tools_by_domain'].items()},
                    'tool_fail_reasons': {k: dict(v) for k, v in m['tool_fail_reasons'].items()},
                    'saved_at': datetime.now(timezone.utc).isoformat(),
                }
                _u.long_term_memory = json.dumps(_existing, ensure_ascii=False, default=str)
                _s.commit()
                logger.info(f"[LEARN] Saved metrics to DB for user {user_id} ({m['total_turns']} turns)")
            finally:
                _s.close()
        except Exception as e:
            logger.debug(f"[LEARN] DB save failed for user {user_id}: {e}")

    def _classify_domain(self, text: str) -> list[str]:
        """Определяет какие домены (темы) упоминаются в тексте."""
        text_lower = text.lower()
        found = []
        for domain, keywords in self._TOPIC_KEYWORDS.items():
            if any(kw in text_lower for kw in keywords):
                found.append(domain)
        return found

    def _detect_repeated_topic(self, user_id: int, user_message: str) -> list[str]:
        """Проверяет, возвращается ли пользователь к уже обсуждавшимся темам.

        Returns:
            Список тем, которые пользователь уже поднимал ранее (>1 раза).
        """
        metrics = self.user_metrics[user_id]
        domains = self._classify_domain(user_message)
        repeated = []
        for domain in domains:
            metrics['domains_mentioned'][domain] += 1
            count = metrics['domains_mentioned'][domain]
            if count >= 3:
                repeated.append(domain)
            metrics['last_topic_mentions'][domain] = datetime.now(timezone.utc).isoformat()
        return repeated

    def _extract_key_facts(self, user_message: str, response: str, intent: str = None) -> list[str]:
        """Извлекает ключевые факты из обмена.

        Пытается найти: решения, цели, предпочтения, инсайты пользователя.
        Returns: список строк-фактов.
        """
        facts = []
        msg_lower = user_message.lower()

        # Решения ('давай', 'начнём', 'сделаем')
        if any(w in msg_lower for w in ['давай', 'начн', 'сделаем', 'попробу', 'согласен', 'ok,', 'okay']):
            facts.append(f"User decision: {user_message[:120]}")

        # Цели ('хочу', 'планирую', 'мечтаю')
        if any(w in msg_lower for w in ['хочу', 'планирую', 'мечтаю', 'моя цель', 'моя задача']):
            facts.append(f"User goal/desire: {user_message[:120]}")

        # Предпочтения ('лучше', 'нравится', 'предпочитаю', 'не люблю')
        if any(w in msg_lower for w in ['лучше', 'нравится', 'предпочитаю', 'не люблю', 'не нравится']):
            facts.append(f"User preference: {user_message[:120]}")

        # Инсайты/открытия
        if any(w in msg_lower for w in ['понял', 'осознал', 'вот в чём дело', 'теперь ясно', 'до меня дошло']):
            facts.append(f"User insight: {user_message[:120]}")

        # Достижения ('сделал', 'готово', 'закончил')
        if any(w in msg_lower for w in ['сделал', 'готово', 'закончил', 'выполнил', 'достиг']):
            # Проверяем что это не про тулзы, а про реальные достижения
            if not any(t in msg_lower for t in ['тулз', 'инструмент', 'tool', 'task']):
                facts.append(f"User achievement: {user_message[:120]}")

        return facts

    def record_turn(self, user_id, user_message, response, tools_used,
                     emotion=None, intent=None, issues=None):
        """Записывает один обмен для обучения.

        Args:
            user_id: Telegram ID
            user_message: Сообщение пользователя
            response: Ответ агента
            tools_used: Список использованных инструментов
            emotion: Определённая эмоция
            intent: Определённое намерение
            issues: Проблемы найденные когнитивным движком
        """
        metrics = self.user_metrics[user_id]

        # Загружаем из БД при первом обращении
        self._load_from_db(user_id)
        metrics = self.user_metrics[user_id]

        # Базовые метрики
        metrics['total_turns'] += 1
        metrics['total_response_length'] += len(response)
        metrics['tools_used_count'] += len(tools_used)

        for tool in tools_used:
            metrics['tools_histogram'][tool] += 1

        # Средняя длина сообщения пользователя
        metrics['avg_user_message_len'] = (
            (metrics['avg_user_message_len'] * (metrics['total_turns'] - 1) + len(user_message))
            // metrics['total_turns']
        )

        # Эмоциональная история (последние 10)
        if emotion and emotion != 'neutral':
            metrics['last_emotions'].append({
                'emotion': emotion,
                'timestamp': datetime.now(timezone.utc).isoformat()
            })
            metrics['last_emotions'] = metrics['last_emotions'][-10:]

        # Распознавание доменов и повторяющихся тем
        repeated = self._detect_repeated_topic(user_id, user_message)
        for domain in repeated:
            metrics['topic_repeat_count'][domain] += 1

        # Извлечение ключевых фактов
        new_facts = self._extract_key_facts(user_message, response, intent)
        for fact in new_facts:
            if fact not in metrics['key_facts']:
                metrics['key_facts'].append(fact)
                metrics['key_facts'] = metrics['key_facts'][-15:]

        # Предпочитаемые инструменты по доменам
        domains = self._classify_domain(user_message)
        for domain in domains:
            for tool in tools_used:
                metrics['preferred_tools_by_domain'][domain][tool] += 1

        # Паттерны успеха/неудачи
        pattern = {
            'intent': intent,
            'emotion': emotion,
            'tools': tools_used,
            'response_len': len(response),
            'had_issues': bool(issues),
            'timestamp': datetime.now(timezone.utc).isoformat()
        }

        if not issues:
            metrics['successful_patterns'].append(pattern)
            metrics['successful_patterns'] = metrics['successful_patterns'][-20:]
        else:
            pattern['issues'] = issues
            metrics['failed_patterns'].append(pattern)
            metrics['failed_patterns'] = metrics['failed_patterns'][-10:]

        # Адаптация стиля
        self._adapt_style(user_id, user_message, response)

        # Обновление глобальных паттернов
        self._update_global_patterns(intent, tools_used, issues)

        # Батчевое сохранение: каждые _SAVE_EVERY_N_TURNS обменов
        self._unsaved_turns[user_id] += 1
        if self._unsaved_turns[user_id] >= self._SAVE_EVERY_N_TURNS:
            self._save_to_db(user_id)
            self._unsaved_turns[user_id] = 0

        logger.info(f"[LEARN] Recorded turn for user {user_id}: "
                     f"turns={metrics['total_turns']}, "
                     f"avg_len={metrics['total_response_length'] // max(metrics['total_turns'], 1)}")

    def record_tool_result(self, user_id: int, tool_name: str, success: bool, result_str: str = '') -> None:
        """Записывает результат вызова инструмента — обновляет tool_success / tool_fail.

        Вызывается из autonomous_agent после каждого tool call.
        Данные накапливаются и инъектируются в system_prompt через get_user_model_hint().
        """
        self._load_from_db(user_id)
        m = self.user_metrics[user_id]

        # Семантическая неудача: инструмент не поднял исключение, но вернул строку с ошибкой
        if success and result_str:
            _r_low = result_str.lower()
            _FAIL_SIGNALS = (
                '"error"', 'traceback', 'exception', 'forbidden', 'unauthorized',
                '403', '404', '429', 'timeout', 'timed out',
            )
            if any(s in _r_low for s in _FAIL_SIGNALS):
                success = False

        if success:
            m['tool_success'][tool_name] += 1
        else:
            m['tool_fail'][tool_name] += 1
            # ── Circuit Breaker: фиксируем неудачу инструмента ──
            try:
                from ai_integration.channel_optimizer import AdaptiveCircuitBreaker
                import asyncio
                _cb_self = AdaptiveCircuitBreaker(user_id)
                try:
                    asyncio.get_event_loop().create_task(
                        _cb_self.record_failure(f'tool:{tool_name}', context=result_str[:200])
                    )
                except RuntimeError:
                    pass  # нет event loop (синхронный контекст)
            except Exception as _cbs_e:
                logger.debug('[CB] record_tool_result skip: %s', _cbs_e)
            # Извлекаем причину ошибки
            if result_str:
                reason = result_str[:120].strip()
                m['tool_fail_reasons'][tool_name][reason] += 1
                # Храним последние сообщения об ошибках
                err_list = m['tool_error_messages'][tool_name]
                err_list.append(reason)
                m['tool_error_messages'][tool_name] = err_list[-5:]

        # Батчевое сохранение через общий счётчик
        self._unsaved_turns[user_id] += 1
        if self._unsaved_turns[user_id] >= self._SAVE_EVERY_N_TURNS:
            self._save_to_db(user_id)
            self._unsaved_turns[user_id] = 0

    def _adapt_style(self, user_id, user_message, response):
        """Адаптирует стиль общения под пользователя."""
        metrics = self.user_metrics[user_id]
        avg_user_len = len(user_message)

        if avg_user_len < 20:
            metrics['conversation_style'] = 'brief'
        elif avg_user_len > 100:
            metrics['conversation_style'] = 'detailed'
        else:
            metrics['conversation_style'] = 'normal'

    def _update_global_patterns(self, intent, tools_used, issues):
        """Обновляет глобальные паттерны из отдельных обменов."""
        if intent and tools_used and not issues:
            if intent not in self.global_patterns['best_tools_by_intent']:
                self.global_patterns['best_tools_by_intent'][intent] = defaultdict(int)
            for tool in tools_used:
                self.global_patterns['best_tools_by_intent'][intent][tool] += 1

    def get_user_preferences(self, user_id):
        """Возвращает предпочтения пользователя для инъекции в промпт.

        Returns: str — блок для системного промпта.
        """
        self._load_from_db(user_id)
        metrics = self.user_metrics[user_id]

        if metrics['total_turns'] < 3:
            return ""

        prefs = []

        # Стиль общения
        style = metrics['conversation_style']
        if style == 'brief':
            prefs.append("Пользователь предпочитает КРАТКИЕ ответы (1-2 предложения).")
        elif style == 'detailed':
            prefs.append("Пользователь любит РАЗВЁРНУТЫЕ ответы с деталями.")

        # Любимые инструменты
        top_tools = sorted(metrics['tools_histogram'].items(),
                          key=lambda x: x[1], reverse=True)[:3]
        if top_tools:
            tools_str = ', '.join(f"{t[0]}({t[1]}x)" for t in top_tools)
            prefs.append(f"Чаще всего полезны: {tools_str}")

        # Эмоциональный профиль
        if metrics['last_emotions']:
            emotion_counts = defaultdict(int)
            for e in metrics['last_emotions']:
                emotion_counts[e['emotion']] += 1
            dominant = max(emotion_counts, key=emotion_counts.get)
            prefs.append(f"Типичная эмоция: {dominant}")

        # Средняя длина ответа
        avg_len = metrics['total_response_length'] // max(metrics['total_turns'], 1)
        if avg_len < 200:
            prefs.append("Предпочитает короткие ответы (<200 символов).")

        if not prefs:
            return ""

        return "\n\n[АДАПТАЦИЯ К ПОЛЬЗОВАТЕЛЮ]\n" + "\n".join(prefs)

    def get_user_model_hint(self, user_id: int) -> str:
        """Собирает комплексную модель пользователя для инъекции в контекст.

        Включает: стиль, тренды настроения, повторяющиеся темы,
        эффективность инструментов, ключевые факты.

        Returns: str — блок для динамического контекста или пустая строка.
        """
        self._load_from_db(user_id)
        m = self.user_metrics[user_id]

        if m['total_turns'] < 5:
            return ""  # Недостаточно данных

        parts = []

        # ── Стиль общения ──
        style = m['conversation_style']
        avg_user_len = m['avg_user_message_len']
        if style == 'brief':
            parts.append("🗣 Пользователь пишет коротко — отвечай по делу, без воды.")
        elif style == 'detailed':
            parts.append("🗣 Пользователь пишет развёрнуто — можно давать детальные ответы.")
        else:
            parts.append(f"🗣 Средняя длина сообщений пользователя: ~{avg_user_len} символов.")

        # ── Повторяющиеся темы (пользователь несколько раз спрашивал одно и то же) ──
        repeated = []
        for domain, count in m['topic_repeat_count'].items():
            if count >= 2:
                repeated.append(f"{domain.replace('_', ' ')} ({count} раз)")
        if repeated:
            parts.append(f"🔄 Возвращается к темам: {', '.join(repeated)} — будь готов предложить новое, не повторяй прошлые советы.")

        # ── Эмоциональный тренд ──
        emotions = m['last_emotions']
        if len(emotions) >= 3:
            recent_emotions = [e['emotion'] for e in emotions[-3:]]
            negative = {'tired', 'sad', 'frustrated', 'anxious', 'angry', 'upset'}
            positive = {'excited', 'happy', 'motivated', 'grateful', 'inspired'}
            neg_count = sum(1 for e in recent_emotions if e in negative)
            pos_count = sum(1 for e in recent_emotions if e in positive)
            if neg_count >= 2:
                parts.append("⚠ Последние сообщения с негативным оттенком — будь мягче, поддержи.")
            elif pos_count >= 2:
                parts.append("🚀 Пользователь на подъёме — предлагай амбициозные шаги.")

        # ── Ключевые факты ──
        if m['key_facts']:
            # Берём только самые свежие, не более 3
            for fact in m['key_facts'][-3:]:
                parts.append(f"📌 {fact[:120]}")

        # ── Эффективность инструментов ──
        tool_hint = self.get_tool_effectiveness_hint(user_id)
        if tool_hint:
            parts.append(tool_hint)

        # ── Предпочитаемые инструменты по текущим доменам ──
        # Определяем какие домены активны сейчас
        if m['domains_mentioned']:
            top_domains = sorted(m['domains_mentioned'].items(), key=lambda x: -x[1])[:2]
            for domain, count in top_domains:
                tools_for_domain = m['preferred_tools_by_domain'].get(domain, {})
                if tools_for_domain:
                    top_tools = sorted(tools_for_domain.items(), key=lambda x: -x[1])[:3]
                    tool_str = ', '.join(f"{t[0]}" for t in top_tools)
                    parts.append(f"🎯 Для «{domain.replace('_', ' ')}» обычно используешь: {tool_str}")

        if not parts:
            return ""

        return "\n\n[ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ]\n" + "\n".join(parts)

    def get_emotional_trend(self, user_id):
        """Определяет эмоциональный тренд пользователя.

        Returns: str — описание тренда или пустая строка.
        """
        metrics = self.user_metrics[user_id]
        emotions = metrics['last_emotions']

        if len(emotions) < 3:
            return ""

        # Последние 3 эмоции
        recent = [e['emotion'] for e in emotions[-3:]]

        # Все негативные?
        negative = {'tired', 'sad', 'frustrated', 'anxious'}
        if all(e in negative for e in recent):
            return "⚠️ ТРЕНД: Пользователь несколько раз подряд в негативном состоянии. Будь особенно внимателен."

        # Все позитивные?
        positive = {'excited'}
        if all(e in positive for e in recent):
            return "🚀 ТРЕНД: Пользователь на подъёме! Предлагай амбициозные шаги."

        return ""

    def suggest_proactive_action(self, user_id, profile_data):
        """Предлагает проактивное действие на основе обученных паттернов.

        Returns: str — предложение для инъекции или пустая строка.
        """
        metrics = self.user_metrics[user_id]

        if metrics['total_turns'] < 5:
            return ""

        # Если пользователь часто ищет информацию → предложи research
        if metrics['tools_histogram'].get('research_topic', 0) > 2:
            interests = profile_data.get('interests', '')
            if interests:
                return f"Проактивность: пользователь часто ищет информацию. Предложи свежие тренды по: {interests}"

        # Если часто создаёт задачи → предложи цель
        if metrics['tools_histogram'].get('add_task', 0) > 3:
            return "Проактивность: пользователь активно ведёт задачи. Предложи создать ЦЕЛЬ для группировки."

        return ""

    def get_tool_effectiveness_hint(self, user_id: int) -> str:
        """Возвращает подсказку об эффективности инструментов.

        Показывает AI какие инструменты работают надёжно, а какие часто ломаются.
        Учитывает типичные причины ошибок.
        """
        self._load_from_db(user_id)
        metrics = self.user_metrics[user_id]
        succ = metrics.get('tool_success', {})
        fail = metrics.get('tool_fail', {})
        fail_reasons = metrics.get('tool_fail_reasons', {})
        all_tools = set(succ.keys()) | set(fail.keys())
        if not all_tools:
            return ""
        lines = []
        for t in sorted(all_tools, key=lambda x: succ.get(x, 0) + fail.get(x, 0), reverse=True)[:8]:
            s = succ.get(t, 0)
            f = fail.get(t, 0)
            total = s + f
            if total < 2:
                continue
            rate = round(s / total * 100)
            if rate < 60:
                # Добавляем типичную причину ошибки
                reasons = fail_reasons.get(t, {})
                top_reason = max(reasons, key=reasons.get) if reasons else ''
                reason_hint = f" (часто: {top_reason[:80]})" if top_reason else ""
                lines.append(f"⚠ {t}: {rate}% успехов ({s}/{total}){reason_hint}")
            elif rate >= 90 and total >= 5:
                lines.append(f"✅ {t}: отлично работает ({total} вызовов)")
        if not lines:
            return ""
        return "\n[ЭФФЕКТИВНОСТЬ ИНСТРУМЕНТОВ]\n" + "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# ГЛОБАЛЬНЫЙ ЭКЗЕМПЛЯР
# ═══════════════════════════════════════════════════════════════

_learner = None

def get_learner():
    global _learner
    if _learner is None:
        _learner = SelfLearner()
    return _learner
