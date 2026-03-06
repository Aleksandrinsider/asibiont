"""
Маркетплейс пользовательских агентов.
"""
import json
import logging
import datetime
from typing import Optional

logger = logging.getLogger(__name__)


def load_agent_personality(agent_id: int, session=None) -> Optional[dict]:
    """
    Загружает данные агента из БД.
    Возвращает dict с personality, tools_allowed, knowledge_snippets или None.
    """
    close = False
    if session is None:
        from models import Session
        session = Session()
        close = True
    try:
        from models import UserAgent
        # Приватные агенты доступны для владельца при любом статусе (кроме disabled)
        agent = session.query(UserAgent).filter(
            UserAgent.id == agent_id,
            UserAgent.status != 'disabled',
        ).first()
        if not agent:
            return None
        # Для публичных агентов требуем статус active или paused (paused-агенты также видны в маркете и могут быть активированы)
        if not agent.is_private and agent.status not in ('active', 'paused'):
            return None
        tools = json.loads(agent.tools_allowed or '[]')
        kb_raw = json.loads(agent.knowledge_base or '[]')
        # Берём до 8 фрагментов базы знаний в контекст (text + url)
        kb_snippets = []
        for item in kb_raw[:8]:
            if item.get('type') == 'text' and item.get('content'):
                kb_snippets.append(item['content'][:800])
            elif item.get('type') == 'url' and item.get('url'):
                title = item.get('name') or item.get('url')
                kb_snippets.append(f"[Ссылка] {title}: {item['url']}")
        # Detect which external service is connected by env key names
        service_label = ''
        if agent.user_api_keys:
            k = agent.user_api_keys.upper()
            if 'GMAIL_USER' in k:          service_label = 'Gmail'
            elif 'RESEND_API_KEY' in k:    service_label = 'Resend'
            elif 'SLACK_TOKEN' in k:       service_label = 'Slack'
            elif 'NOTION_TOKEN' in k:      service_label = 'Notion'
            elif 'AIRTABLE_TOKEN' in k:    service_label = 'Airtable'
            elif 'TRELLO_KEY' in k:        service_label = 'Trello'
            elif 'STRIPE_SK' in k:         service_label = 'Stripe'
            elif 'SHOPIFY' in k:           service_label = 'Shopify'
            elif 'TELEGRAM_TOKEN' in k or 'BOT_TOKEN' in k: service_label = 'Telegram Bot'
            elif 'VK_TOKEN' in k:          service_label = 'ВКонтакте'
            elif 'GITHUB_TOKEN' in k:      service_label = 'GitHub'
            elif 'JIRA' in k:              service_label = 'Jira'
            elif 'CALENDLY' in k:          service_label = 'Calendly'
            elif 'HH_TOKEN' in k:          service_label = 'HeadHunter'
            elif 'WB_TOKEN' in k or 'WILDBERRIES' in k: service_label = 'Wildberries'
            elif 'OZON_CLIENT_ID' in k or 'OZON_API_KEY' in k: service_label = 'Ozon'
            elif 'YOUTUBE' in k:           service_label = 'YouTube'
            elif 'SHEETS' in k:            service_label = 'Google Sheets'
            elif 'RSS_URL' in k:           service_label = 'RSS'
            elif 'WEATHER_API_KEY' in k:   service_label = 'OpenWeatherMap'
            elif 'COINGECKO' in k:         service_label = 'CoinGecko'
            elif 'YANDEX_USER' in k:       service_label = 'Яндекс Почта'
            elif 'MAIL_USER' in k:         service_label = 'Mail.ru'
            elif 'API_URL' in k:           service_label = 'HTTP API'
            elif 'BITRIX24_WEBHOOK' in k:   service_label = 'Битрикс24'
            elif 'AMO_SUBDOMAIN' in k or 'AMO_ACCESS_TOKEN' in k: service_label = 'AmoCRM'
            elif 'HUBSPOT_API_KEY' in k:    service_label = 'HubSpot'
        return {
            'id': agent.id,
            'name': agent.name,
            'job_title': agent.job_title or '',
            'personality': agent.personality or '',
            'tools_allowed': tools,
            'knowledge_snippets': kb_snippets,
            'price_per_message': agent.price_per_message,
            'author_id': agent.author_id,
            'author_royalty_pct': agent.author_royalty_pct,
            'trial_messages': agent.trial_messages,
            'python_code': agent.python_code or '',
            'user_api_keys': agent.user_api_keys or '',
            'service_label': service_label,
        }
    finally:
        if close:
            session.close()


def build_agent_system_prompt(agent_data: dict, base_system_prompt: str) -> str:
    """
    Инжектирует личность кастомного агента поверх базового системного промпта.
    Базовый промпт сохраняется — инструменты и правила поведения работают как обычно.
    """
    personality = agent_data.get('personality', '').strip()
    name = agent_data.get('name', 'Агент')
    job_title = agent_data.get('job_title', '').strip()
    kb_snippets = agent_data.get('knowledge_snippets', [])
    has_script = bool(agent_data.get('python_code', '').strip())
    service_label = agent_data.get('service_label', '')

    title_line = f"{name}, {job_title}" if job_title else name
    _job_line = ("\nДОЛЖНОСТЬ / РОЛЬ: " + job_title) if job_title else ""

    overlay = f"""
═══════════════════════════════════════════════════════
РЕЖИМ АГЕНТА: {title_line}
═══════════════════════════════════════════════════════
Ты — {title_line}. Это твоя настоящая роль и личность.
Пользователь обращается к тебе по имени «{name}» или «@{name}» — это всегда прямой запрос к тебе, реагируй на него в полную силу.
Веди ВСЕ РАЗГОВОРЫ И ПЕРЕПИСКУ ОТ СВОЕГО ИМЕНИ, а НЕ от имени пользователя.
Когда нужно написать письмо, сообщение или пост — ты автор, ты отправитель, подпись — твои имя и должность.
Сохраняй этот характер постоянно. Технические возможности и инструменты платформы работают как обычно.
{_job_line}

ЛИЧНОСТЬ И ХАРАКТЕР:
{personality}
"""

    # Email-сервисы требуют явного запрета подмены реального ящика платформенными инструментами
    _EMAIL_SERVICES = {'Gmail', 'Яндекс Почта', 'Mail.ru', 'Resend'}
    _is_email_svc = service_label in _EMAIL_SERVICES

    if service_label:
        overlay += f"""
ПОДКЛЮЧЁННЫЙ СЕРВИС: {service_label}
Этот агент интегрирован с {service_label}. Когда пользователь обсуждает цели, задачи или стратегию — думай прежде всего: «что через {service_label} можно СДЕЛАТЬ для этого прямо сейчас?» и предлагай конкретные действия через этот сервис. Это твоё главное преимущество перед обычным агентом.
Остальные инструменты платформы (кампании, посты) — дополнение, используй только если {service_label} здесь не релевантен или пользователь явно спросил.
"""
        if _is_email_svc:
            overlay += f"""
⛔ КРИТИЧЕСКИ ВАЖНО ДЛЯ {service_label}:
Пользователь подключил СВОЙ почтовый ящик {service_label} — он имеет в виду РЕАЛЬНЫЕ письма из своей почты, а НЕ платформенные рассылки.
— НЕ ПРЕДЛАГАЙ start_email_campaign, start_content_campaign — это инструменты платформы для маркетинговых рассылок, они НЕ имеют отношения к личному ящику пользователя.
— ✅ Для отправки письма из личного ящика — используй send_email(to, subject, body). Это именно инструмент личной почты через SMTP ({service_label}).
— Если пользователь просит отправить письмо — вызывай send_email, а НЕ start_email_campaign.
— ⛔ ОШИБКА send_email: если вернулось ❌ — ПОКАЖИ ТОЧНЫЙ ТЕКСТ ОШИБКИ пользователю. ЗАПРЕЩЕНО говорить «попробую через резервный канал», предлагать или спрашивать «попробовать через другую систему/канал», молча переключаться на send_outreach_email / run_agent_action / кампанию. ЗАПРЕЩЕНО сохранять невыполненную отправку в задачи или заметки — это уклонение от задачи. Жди решения пользователя.
— Если пользователь спрашивает «что в почте», «посмотри письма», «есть что-нибудь» — отвечай на основе данных скрипта из секции [ДАННЫЕ ОТ АГЕНТА].
— Если данных нет (скрипт не настроен) → честно скажи: «Скрипт не выполнился, данных нет» — и НЕ подменяй это предложением запустить email-кампанию.
"""

    if has_script:
        overlay += """
## ВНЕШНИЕ ДАННЫЕ + ДЕЙСТВИЯ

Перед каждым ответом выполняется скрипт, который получает актуальные данные из подключённого сервиса.
Эти данные появятся в секции [ДАННЫЕ ОТ АГЕНТА].

ГЛАВНЫЙ ПРИНЦИП — ОТВЕЧАЙ НА ТО, ЧТО ПРИШЛО:
— Данные скрипта = первоисточник для ответа. Не интерпретируй их через призму шаблонов («письма → рассылки», «заказы → цены»).
— Тип сервиса и контекст определяй из самих данных, а не из названия API или предположений.
— Говори от первого лица о том, что реально есть в данных: «вижу 3 непрочитанных письма от коллег», «открыто 5 задач», «последний коммит вчера».
— НЕ добавляй советы и рекомендации, которые не следуют из данных напрямую.

ПРОАКТИВНОСТЬ — ТОЛЬКО ПРИ ЯВНЫХ СОБЫТИЯХ:
— Сообщай о новых/важных событиях в начале ответа, если они есть в данных и пользователь ещё не спросил (новые письма, уведомления, задачи со срочным дедлайном, ошибки).
— НЕ делай выводов о «трендах» и НЕ давай деловых советов (поднять цену, обновить карточку, выйти на рынок), если пользователь об этом не просил.
— Если событий нет — не изобретай их, просто отвечай спокойно по данным.

ИНСТРУМЕНТ ДЕЙСТВИЙ — run_agent_action(action, params):
— Запускает скрипт в режиме записи: отправка, создание записи, обновление во внешнем сервисе.
— Скрипт получает AGENT_ACTION=<action> и AGENT_PARAM_<KEY>=<value> через окружение.
— Используй только когда пользователь явно просит что-то сделать.
— ПЕРЕД действием — уточни детали, если они не указаны.
— После — сообщи результат из вывода скрипта.
— Если скрипт вернул ошибку — скажи что именно не вышло, без лишних советов по «подключению».

КАК РАБОТАТЬ С ДАННЫМИ:
— Данные пришли → ты их уже видишь. Не говори «нужно настроить» или «не могу получить».
— Если данных нет или скрипт упал → скажи честно что произошло.
— Не подтягивай серверные данные платформы (задачи, цели профиля) как замену данным скрипта — это разные вещи.
"""
    elif service_label:
        # Сервис подключён (есть ключи), но python_code не настроен
        overlay += f"""
## ВАЖНО: СКРИПТ НЕ НАСТРОЕН

Ключи доступа к {service_label} сохранены, но Python-скрипт для получения данных не добавлен.
Это значит данных из {service_label} в этом ответе НЕТ.

⛔ НЕ говори пользователю что «нужно подключить интеграцию» — ключи уже есть.
⛔ НЕ предлагай платформенные инструменты (email-кампании, автопостинг) как замену {service_label}.
✓ Честно скажи: «Код для получения данных из {service_label} не настроен — добавьте Python-скрипт в настройках агента (вкладка "Продвинутые настройки").»
✓ После сообщения — жди указаний пользователя, не переключайся на другие инструменты.
"""

    if kb_snippets:
        overlay += "\nБАЗА ЗНАНИЙ АГЕНТА (используй при ответах):\n"
        for i, snippet in enumerate(kb_snippets, 1):
            overlay += f"[{i}] {snippet}\n"

    overlay += "\n═══════════════════════════════════════════════════════\n"

    # Если агент специализированный (подключён внешний сервис) — вырезаем из базового промпта
    # блок "БАЛАНС ИНСТРУМЕНТОВ", который жёстко предписывает предлагать email-аутрич и TG-кампании.
    # Это корень проблемы: агент при любой цели тянул именно эти 2 варианта из 4 каналов.
    if service_label:
        import re
        base_system_prompt = re.sub(
            r'БАЛАНС ИНСТРУМЕНТОВ.*?(?=\n[^\-—•]|\Z)',
            f'ИНСТРУМЕНТЫ: используй инструменты платформы (задачи, цели, поиск, исследование) как поддержку, '
            f'но для конкретных действий приоритизируй возможности {service_label}.',
            base_system_prompt,
            flags=re.DOTALL,
        )
        # Для email-агентов: вырезаем строки про start_email_campaign / send_email из
        # раздела инструментов базового промпта, чтобы AI не путал личную почту с платформенной рассылкой.
        if _is_email_svc:
            base_system_prompt = re.sub(
                r'— (start_email_campaign|update_email_campaign|send_outreach_email|'
                r'add_email_leads|reply_to_outreach_email|get_email_campaign_status|pause_email_campaign)'
                r'\(.*?\n',
                '',
                base_system_prompt,
            )

    combined = overlay + "\n" + base_system_prompt

    # Краткое напоминание в КОНЦЕ промпта — чтобы AI не «забыл» личность после длинного контекста
    svc_hint = f" Подключённый сервис: {service_label} — приоритизируй его возможности в ответах." if service_label else ""
    reminder = (
        f"\n\n[ТЫ — {title_line}. ПИШИ ОТ СВОЕГО ИМЕНИ в каждом ответе, не от имени пользователя.{svc_hint}"
    )
    if has_script:
        reminder += (
            f" У тебя есть внешние данные — отвечай строго по ним, без домыслов и шаблонных советов. "
            f"Действия (run_agent_action) — только по явной просьбе пользователя.]"
        )
    else:
        if service_label:
            reminder += f" Скрипт {service_label} не настроен — сообщи пользователю и не предлагай платформенные рассылки.]"
        else:
            reminder += " Нарушение характера = провал роли.]"
    return combined + reminder


def _get_mem(user_id: int, session) -> dict:
    """Вспомогательная: читает user.memory как dict."""
    from models import User
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user or not user.memory:
        return {}
    try:
        return json.loads(user.memory)
    except Exception:
        return {}


def _save_mem(user_id: int, mem: dict, session) -> None:
    """Вспомогательная: сохраняет dict обратно в user.memory."""
    from models import User
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if user:
        user.memory = json.dumps(mem, ensure_ascii=False)
        session.commit()


def get_user_active_agent(user_id: int, session=None) -> Optional[int]:
    """
    Возвращает «активный» (focused) agent_id для данного пользователя.
    Сначала смотрит на focused_agent_id (последний @упомянутый или нажатый),
    потом на первый в active_agent_ids, потом legacy-ключ active_agent_id.
    """
    close = False
    if session is None:
        from models import Session
        session = Session()
        close = True
    try:
        mem = _get_mem(user_id, session)
        # Новая схема
        focused = mem.get('focused_agent_id')
        if focused:
            return focused
        ids = mem.get('active_agent_ids') or []
        if ids:
            return ids[0]
        # Обратная совместимость со старой схемой
        return mem.get('active_agent_id')
    finally:
        if close:
            session.close()


def get_user_active_agents(user_id: int, session=None) -> list:
    """
    Возвращает список всех активированных agent_id для данного пользователя.
    Список упорядочен: focused — первый.
    Автоматически очищает ids агентов без реальной подписки.
    """
    close = False
    if session is None:
        from models import Session
        session = Session()
        close = True
    try:
        mem = _get_mem(user_id, session)
        ids = list(mem.get('active_agent_ids') or [])
        # Обратная совместимость
        legacy = mem.get('active_agent_id')
        if legacy and legacy not in ids:
            ids.insert(0, legacy)

        # Фильтруем: оставляем только агентов у которых есть AgentSubscription
        # (и собственные, и чужие активируются через /api/marketplace/agents/{id}/activate,
        #  который всегда создаёт AgentSubscription — это единственный источник правды)
        if ids:
            try:
                from models import AgentSubscription as _AS_f, User as _U_f
                _user = session.query(_U_f).filter_by(telegram_id=user_id).first()
                if _user:
                    _sub_ids = {r.agent_id for r in session.query(_AS_f).filter_by(user_id=_user.id).all()}
                    _valid = [i for i in ids if i in _sub_ids]
                    if _valid != ids:
                        mem['active_agent_ids'] = _valid
                        _save_mem(user_id, mem, session)
                        ids = _valid
            except Exception:
                pass

        # focused — в начало
        focused = mem.get('focused_agent_id')
        if focused and focused in ids and ids[0] != focused:
            ids.remove(focused)
            ids.insert(0, focused)
        return ids
    finally:
        if close:
            session.close()


def set_user_active_agent(user_id: int, agent_id: Optional[int], session=None):
    """
    Добавляет agent_id в список активных и ставит его как focused.
    Если agent_id=None — полностью очищает всех активных агентов.
    НЕ деактивирует других агентов при активации нового.
    """
    close = False
    if session is None:
        from models import Session
        session = Session()
        close = True
    try:
        mem = _get_mem(user_id, session)
        if agent_id is None:
            # Явный сброс всех
            mem.pop('active_agent_id', None)
            mem.pop('active_agent_ids', None)
            mem.pop('focused_agent_id', None)
        else:
            ids = list(mem.get('active_agent_ids') or [])
            # Обратная совместимость: подхватываем старый single-ключ
            legacy = mem.pop('active_agent_id', None)
            if legacy and legacy not in ids:
                ids.append(legacy)
            # Добавляем новый, если ещё нет
            if agent_id not in ids:
                ids.append(agent_id)
            mem['active_agent_ids'] = ids
            mem['focused_agent_id'] = agent_id
        _save_mem(user_id, mem, session)
    finally:
        if close:
            session.close()


def remove_user_active_agent(user_id: int, agent_id: int, session=None):
    """Удаляет конкретного агента из списка активных. Остальные не затрагивает."""
    close = False
    if session is None:
        from models import Session
        session = Session()
        close = True
    try:
        mem = _get_mem(user_id, session)
        ids = list(mem.get('active_agent_ids') or [])
        if agent_id in ids:
            ids.remove(agent_id)
        mem['active_agent_ids'] = ids
        # Если удалённый был focused — переключаемся на первый оставшийся
        if mem.get('focused_agent_id') == agent_id:
            mem['focused_agent_id'] = ids[0] if ids else None
        # Обратная совместимость
        if mem.get('active_agent_id') == agent_id:
            mem.pop('active_agent_id', None)
        _save_mem(user_id, mem, session)
    finally:
        if close:
            session.close()


def set_user_focused_agent(user_id: int, agent_id: int, session=None):
    """Устанавливает focused-агента (не меняет список активных)."""
    close = False
    if session is None:
        from models import Session
        session = Session()
        close = True
    try:
        mem = _get_mem(user_id, session)
        mem['focused_agent_id'] = agent_id
        _save_mem(user_id, mem, session)
    finally:
        if close:
            session.close()


# ─── Биллинг агентов ───────────────────────────────────────────────────────────

def bill_agent_message(user_id: int, agent_id: int, session=None) -> dict:
    """
    Списывает токены за сообщение кастомному агенту.
    Возвращает {'success': bool, 'is_trial': bool, 'error': str}.
    """
    close = False
    if session is None:
        from models import Session
        session = Session()
        close = True
    try:
        from models import User, UserAgent, AgentSubscription, AgentRun
        from config import FREE_ACCESS_MODE
        if FREE_ACCESS_MODE:
            return {'success': True, 'error': ''}

        agent = session.query(UserAgent).filter_by(id=agent_id, status='active').first()
        if not agent:
            return {'success': False, 'error': 'Агент не найден'}

        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return {'success': False, 'error': 'Пользователь не найден'}

        # Ищем/создаём подписку
        sub = session.query(AgentSubscription).filter_by(
            user_id=user.id, agent_id=agent_id).first()
        if not sub:
            sub = AgentSubscription(user_id=user.id, agent_id=agent_id)
            session.add(sub)
            session.flush()
            agent.subscribers_count = (agent.subscribers_count or 0) + 1

        # Автор использует своего агента — биллинг не нужен
        is_owner = (agent.author_id == user.id)
        if is_owner:
            sub.messages_count = (sub.messages_count or 0) + 1
            sub.last_message_at = datetime.datetime.now(datetime.timezone.utc)
            agent.messages_count = (agent.messages_count or 0) + 1
            run = AgentRun(user_id=user.id, agent_id=agent_id,
                           tokens_charged=0, author_earnings=0,
                           platform_earnings=0, is_trial=False)
            session.add(run)
            session.commit()
            return {'success': True, 'is_owner': True, 'is_trial': False, 'error': ''}

        # Платное сообщение
        cost = agent.price_per_message or 5
        balance = user.token_balance or 0
        if balance < cost:
            return {'success': False,
                    'is_owner': False, 'is_trial': False,
                    'error': f'Недостаточно токенов. Нужно {cost}, баланс {balance}'}

        royalty_pct = agent.author_royalty_pct or 70
        author_share = int(cost * royalty_pct / 100)
        platform_share = cost - author_share

        user.token_balance = balance - cost
        user.tokens_spent = (user.tokens_spent or 0) + cost

        # Начисляем автору (только если это другой пользователь)
        author = session.query(User).filter_by(id=agent.author_id).first()
        if author and author.id != user.id:
            author.token_balance = (author.token_balance or 0) + author_share
            author.referral_balance = (author.referral_balance or 0) + author_share
            from models import TokenTransaction
            session.add(TokenTransaction(
                user_id=author.id, amount=author_share,
                action='agent_royalty',
                description=f'Роялти за сообщение агенту «{agent.name}»',
                balance_after=author.token_balance
            ))

        from models import TokenTransaction as _TT
        session.add(_TT(
            user_id=user.id, amount=-cost,
            action='agent_message',
            description=f'Сообщение агенту «{agent.name}»',
            balance_after=user.token_balance
        ))

        sub.messages_count = (sub.messages_count or 0) + 1
        sub.tokens_spent = (sub.tokens_spent or 0) + cost
        sub.last_message_at = datetime.datetime.now(datetime.timezone.utc)
        agent.messages_count = (agent.messages_count or 0) + 1

        run = AgentRun(user_id=user.id, agent_id=agent_id,
                       tokens_charged=cost, author_earnings=author_share,
                       platform_earnings=platform_share, is_trial=False)
        session.add(run)
        session.commit()
        return {'success': True, 'is_owner': False, 'is_trial': False, 'error': ''}  # noqa
    except Exception as e:
        session.rollback()
        logger.error(f"[BILLING] bill_agent_message error: {e}")
        return {'success': False, 'error': str(e)}
    finally:
        if close:
            session.close()
