"""
Adaptive Autonomous Agent — стандартный tool calling loop
с адаптивной логикой из лучших итераций.

Архитектура:
1. Собираем контекст (1 запрос к БД)
2. Tool calling loop (max 5 итераций)
3. Обучение на успехах + адаптация

Умные фичи из 73dc138:
- force_tool_choice для явных запросов (новости, задачи, партнёры)
- success_patterns — обучение на успешных паттернах
- user_preferences — адаптация под пользователя
- context_memory — краткосрочная контекстная память
- auto-trigger awareness (check_time_conflicts → add_task)
- parameter auto-fix для известных tool quirks
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import aiohttp
import json
import logging
import random
import re
import inspect
import traceback
import pytz
from datetime import datetime, timezone

from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL, API_TIMEOUT_QUICK, API_TIMEOUT_NORMAL, API_TIMEOUT_LONG, API_TIMEOUT_SCRIPT
from models import Session, User, Task, UserProfile, Goal
from .prompts import get_extended_system_prompt
from .dynamic_tools import tool_discovery
from .tools import get_available_tools
from .vector_memory import store_conversation_turn, build_memory_context, search_memory
from .multi_agent import get_orchestrator
from .self_learning import get_learner

logger = logging.getLogger(__name__)


def _decrypt_keys(raw: str) -> str:
    """Расшифровывает user_api_keys если зашифрованы (Fernet/obf), иначе возвращает как есть."""
    if not raw or not raw.startswith(('enc:', 'obf:')):
        return raw or ''
    try:
        from config import decrypt_token as _dt
        return _dt(raw)
    except Exception:
        return raw

# ── Integration hint patterns: (substring_in_tool_result_lower, user_recommendation) ─────
# Используются в _extract_intg_hints() для детектирования ограничений инструментов
# и автоматической простановки рекомендаций в ответ агента.
_INTG_HINT_PATTERNS: list[tuple[str, str]] = [
    # Telegram-канал
    ("telegram-канал не настроен",
     "💡 Telegram-канал не настроен. Вызови create_post с тем же контентом (сохранит черновик), затем: Дашборд → Профиль → укажи @username канала → добавь бота как администратора"),
    ("telegram channel not configured",
     "💡 Telegram channel not configured. Call create_post with the same content (saves draft), then: Dashboard → Profile → set @username → add bot as admin"),
    # Discord webhook
    ("discord webhook не настроен",
     "💡 Discord не подключён. Discord → канал → Настройки → Интеграции → Webhooks → скопируй URL → Дашборд → Профиль"),
    # GitHub лимит / токен
    ("60 запросов/час",
     "💡 GitHub работает без токена (60 запросов/час). Добавь GITHUB_TOKEN в настройки агента: github.com/settings/tokens → лимит вырастет до 5000"),
    ("github_token не настроен",
     "💡 GITHUB_TOKEN не настроен. github.com/settings/tokens → Generate (repo, read:user) → добавь в настройки агента"),
    # NewsAPI
    ("newsapi исчерпала",
     "💡 NewsAPI исчерпал дневной лимит. Получи бесплатный ключ: newsapi.org → добавь NEWSAPI_KEY в настройках агента"),
    ("newsapi_key не",
     "💡 NewsAPI не настроен. newsapi.org → бесплатно 100 запросов/день → добавь NEWSAPI_KEY в настройках агента"),
    # Email / Gmail / IMAP
    ("gmail не настроен",
     "💡 Gmail не настроен. Добавь GMAIL_USER + GMAIL_PASS (пароль приложения: myaccount.google.com → Безопасность → Пароли приложений) в настройки агента"),
    ("imap не настроен",
     "💡 IMAP не настроен — агент не может читать входящие. Добавь IMAP_HOST + IMAP_USER + IMAP_PASS в настройки агента"),
    # OpenWeatherMap
    ("openweathermap_api_key",
     "💡 OpenWeatherMap не подключён. Получи бесплатный ключ: openweathermap.org/api → добавь OPENWEATHERMAP_API_KEY в настройках агента"),
    # Alpha Vantage
    ("alphavantage_api_key",
     "💡 Alpha Vantage не подключён. Получи API ключ: alphavantage.co (бесплатно, 500 req/день) → добавь ALPHAVANTAGE_API_KEY в настройки агента"),
    # Notion
    ("notion_token не настроен",
     "💡 Notion не подключён. notion.so/my-integrations → создай интеграцию → добавь NOTION_TOKEN в настройки агента"),
    # Google Sheets
    ("google_sheets_credentials",
     "💡 Google Sheets не подключён. Google Cloud Console → Service Account → credentials.json → добавь в настройки агента"),
    # Slack
    ("slack_bot_token не",
     "💡 Slack не подключён. api.slack.com/apps → Create App → Bot Token → добавь SLACK_BOT_TOKEN в настройки агента"),
    # Stripe
    ("stripe_secret_key не",
     "💡 Stripe не подключён. Добавь STRIPE_SECRET_KEY в настройки агента для мониторинга платежей"),
    # Twitter / X
    ("x_api_key не",
     "💡 Twitter/X не подключён. developer.twitter.com → добавь X_API_KEY + X_API_SECRET в настройки агента"),
    # Airtable
    ("airtable_api_key не",
     "💡 Airtable не подключён. airtable.com/account → API → добавь AIRTABLE_API_KEY в настройки агента"),
    # Trello
    ("trello_api_key не",
     "💡 Trello не подключён. trello.com/app-key → добавь TRELLO_API_KEY + TRELLO_TOKEN в настройки агента"),
    # Jira
    ("jira_email не",
     "💡 Jira не подключён. Добавь JIRA_URL + JIRA_EMAIL + JIRA_TOKEN в настройки агента"),
    # HH.ru
    ("hh_api_token не",
     "💡 HH.ru не подключён. hh.ru/oauth/authorize → добавь HH_API_TOKEN в настройки агента"),
    # LinkedIn
    ("linkedin access token",
     "💡 LinkedIn не подключён. Добавь LINKEDIN_ACCESS_TOKEN в настройки агента в дашборде: https://asibiont.com/dashboard"),
    ("linkedin_api_key не",
     "💡 LinkedIn не подключён. Добавь LINKEDIN_ACCESS_TOKEN в настройки агента в дашборде: https://asibiont.com/dashboard"),
    ("linkedin не настроен",
     "💡 LinkedIn не подключён. Добавь LINKEDIN_ACCESS_TOKEN в настройки агента в дашборде: https://asibiont.com/dashboard"),
]


def _extract_intg_hints(messages: list) -> list[str]:
    """Сканирует tool-результаты и возвращает список рекомендаций по интеграциям.

    Используется в _exec_agent_for_director после цикла tool calls — если инструмент
    вернул ограничение (нет токена, не настроен и т.д.), добавляем подсказку в ответ агента.
    Anti-spam: одинаковые рекомендации не дублируются.
    """
    seen: set[str] = set()
    hints: list[str] = []
    for msg in messages:
        if msg.get('role') != 'tool':
            continue
        content_lower = (msg.get('content') or '').lower()
        for pattern, hint in _INTG_HINT_PATTERNS:
            if pattern in content_lower and hint not in seen:
                seen.add(hint)
                hints.append(hint)
    return hints


def _get_active_agent_integration_snapshot(user_id: int) -> dict:
    """Возвращает срез интеграций ВСЕХ активных агентов для проверки доступности сервисов."""
    try:
        from .user_agents import get_user_active_agents, load_agent_personality

        _aids = get_user_active_agents(user_id)
        if not _aids:
            return {'labels': [], 'caps_text': '', 'keys_text': '', 'agent_map': []}
        _all_labels: list[str] = []
        _all_caps: list[str] = []
        _all_keys: list[str] = []
        _agent_map: list[dict] = []  # [{name, integrations: [labels]}]
        for _aid in _aids:
            _adata = load_agent_personality(_aid) or {}
            _keys = _adata.get('user_api_keys') or ''
            _tools = _adata.get('tools_allowed') or ''
            if isinstance(_tools, list):
                _tools = json.dumps(_tools, ensure_ascii=False)
            _code = _adata.get('python_code') or ''
            _caps = _parse_agent_integrations(_keys, _code, _tools)
            _all_labels.extend(c for c in _caps if c not in _all_labels)
            _all_caps.extend(str(c).lower() for c in _caps)
            _all_keys.append(str(_keys).lower())
            if _caps:
                _aname = _adata.get('name') or _adata.get('agent_name') or f'agent_{_aid}'
                _agent_map.append({'name': _aname, 'integrations': _caps})
        return {
            'labels': _all_labels,
            'caps_text': ' '.join(_all_caps),
            'keys_text': ' '.join(_all_keys),
            'agent_map': _agent_map,
        }
    except Exception as _ih_err:
        logger.debug("[INTG HINT] integration check failed: %s", _ih_err)
        return {'labels': [], 'caps_text': '', 'keys_text': ''}


_INTEGRATION_REQUEST_RULES: list[dict] = [
    {
        'label': 'LinkedIn',
        'keywords': ('linkedin', 'линкедин', 'линкед'),
        'presence': ('linkedin', 'linkedin_access_token'),
        'setup': 'LINKEDIN_ACCESS_TOKEN',
    },
    {
        'label': 'GitHub',
        'keywords': ('github', 'гитхаб'),
        'presence': ('github', 'github_token'),
        'setup': 'GITHUB_TOKEN',
    },
    {
        'label': 'Slack',
        'keywords': ('slack',),
        'presence': ('slack', 'slack_bot_token', 'slack_token'),
        'setup': 'SLACK_TOKEN',
    },
    {
        'label': 'Discord',
        'keywords': ('discord',),
        'presence': ('discord', 'discord_webhook'),
        'setup': 'DISCORD_WEBHOOK_URL',
    },
    {
        'label': 'Notion',
        'keywords': ('notion',),
        'presence': ('notion', 'notion_token'),
        'setup': 'NOTION_TOKEN',
    },
    {
        'label': 'Google Sheets',
        'keywords': ('google sheets', 'sheets', 'гугл таблиц', 'таблиц'),
        'presence': ('google sheets', 'google_sheets', 'gspread', 'sheets', 'gsheets'),
        'setup': 'GSHEETS_ID/GSHEETS_SHEET',
    },
    {
        'label': 'Jira',
        'keywords': ('jira',),
        'presence': ('jira',),
        'setup': 'JIRA_URL/JIRA_EMAIL/JIRA_TOKEN',
    },
    {
        'label': 'Trello',
        'keywords': ('trello',),
        'presence': ('trello',),
        'setup': 'TRELLO_KEY/TRELLO_TOKEN',
    },
    {
        'label': 'HubSpot',
        'keywords': ('hubspot',),
        'presence': ('hubspot',),
        'setup': 'HUBSPOT_API_KEY',
    },
    {
        'label': 'AmoCRM',
        'keywords': ('amocrm', 'амоcrm', 'амо срм', 'amo crm'),
        'presence': ('amocrm', 'amo'),
        'setup': 'AMO_SUBDOMAIN/AMO_ACCESS_TOKEN',
    },
    {
        'label': 'Битрикс24',
        'keywords': ('битрикс', 'bitrix'),
        'presence': ('битрикс', 'bitrix'),
        'setup': 'BITRIX24_WEBHOOK',
    },
    {
        'label': 'Twitter/X',
        'keywords': ('twitter', 'x.com', 'твиттер'),
        'presence': ('twitter', 'x_api_key', 'x api', 'twitter_api'),
        'setup': 'TWITTER_API_KEY/TWITTER_API_SECRET/TWITTER_ACCESS_TOKEN/TWITTER_ACCESS_SECRET',
    },
    {
        'label': 'hh.ru',
        'keywords': ('hh.ru', 'headhunter', 'хх.ру'),
        'presence': ('hh.ru', 'hh_', 'headhunter', 'hh_query'),
        'setup': 'HH_QUERY/HH_AREA',
    },
    {
        'label': 'VK',
        'keywords': ('вконтакте', 'вк', 'vkontakte', 'vk'),
        'presence': ('vk_token', 'vk_access_token', 'vkontakte'),
        'setup': 'VK_ACCESS_TOKEN',
    },
    {
        'label': 'YouTube',
        'keywords': ('youtube', 'ютуб', 'youtube channel'),
        'presence': ('youtube_api_key', 'youtube', 'yt_'),
        'setup': 'YOUTUBE_API_KEY',
    },
    {
        'label': 'Google Calendar',
        'keywords': ('google calendar', 'встреча', 'мероприятие', 'расписание', 'создай событие'),
        'presence': ('google_calendar', 'gcal_', 'gcalendar'),
        'setup': 'GCAL_CREDENTIALS_JSON',
    },
    {
        'label': 'Google Drive',
        'keywords': ('google drive', 'гугл диск', 'gdrive', 'загрузи в диск', 'загрузи файл'),
        'presence': ('google_drive', 'gdrive', 'drive_token'),
        'setup': 'GDRIVE_CREDENTIALS_JSON',
    },
    {
        'label': 'Google Sheets',
        'keywords': ('google sheets', 'гугл таблиц', 'sheets', 'spreadsheet'),
        'presence': ('google_sheets', 'gspread', 'sheets', 'gsheets'),
        'setup': 'GSHEETS_ID/GSHEETS_SHEET',
    },
    {
        'label': 'Airtable',
        'keywords': ('airtable',),
        'presence': ('airtable', 'airtable_token'),
        'setup': 'AIRTABLE_API_KEY/AIRTABLE_BASE_ID',
    },
    {
        'label': 'Calendly',
        'keywords': ('calendly', 'calendar', 'запись на встречу', 'scheduling'),
        'presence': ('calendly', 'calendly_token'),
        'setup': 'CALENDLY_API_KEY',
    },
    {
        'label': 'Twilio / SMS',
        'keywords': ('twilio', 'sms', 'смс', 'позвони', 'whatsapp', 'ватсап'),
        'presence': ('twilio', 'whatsapp_', 'twilio_sid'),
        'setup': 'TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN',
    },
    {
        'label': 'SMS.ru',
        'keywords': ('sms.ru', 'смс ру', 'sms_ru'),
        'presence': ('sms_ru', 'sms.ru'),
        'setup': 'SMSRU_API_ID',
    },
    {
        'label': 'Stripe',
        'keywords': ('stripe', 'оплата', 'платёж', 'платежи', 'charges', 'subscriptions'),
        'presence': ('stripe', 'stripe_key', 'stripe_secret'),
        'setup': 'STRIPE_SECRET_KEY',
    },
    {
        'label': 'Tinkoff Invest',
        'keywords': ('тинькофф', 'tinkoff', 'tinkoff invest', 'портфель тинькофф'),
        'presence': ('tinkoff', 'tinkoff_token', 'tinkoff_invest'),
        'setup': 'TINKOFF_TOKEN',
    },
    {
        'label': 'Alpha Vantage',
        'keywords': ('alpha vantage', 'alphavantage', 'финансовые данные', 'биржа', 'акции'),
        'presence': ('alphavantage', 'alpha_vantage', 'av_api'),
        'setup': 'ALPHAVANTAGE_API_KEY',
    },
    {
        'label': 'CoinGecko',
        'keywords': ('coingecko', 'криптовалют', 'bitcoin', 'ethereum', 'coin price'),
        'presence': ('coingecko', 'coingecko_key'),
        'setup': 'COINGECKO_API_KEY (или без ключа — публичный API)',
    },
    {
        'label': 'OpenWeather',
        'keywords': ('погода', 'weather', 'прогноз погоды', 'openweather'),
        'presence': ('openweather', 'weather_api', 'owm_'),
        'setup': 'OPENWEATHER_API_KEY',
    },
    {
        'label': 'Yandex Metrika',
        'keywords': ('яндекс метрика', 'yandex metrika', 'метрика', 'посещаемость сайта'),
        'presence': ('yandex_metrika', 'metrika_', 'metrika_token'),
        'setup': 'METRIKA_TOKEN/METRIKA_COUNTER_ID',
    },
    {
        'label': 'GA4 (Google Analytics)',
        'keywords': ('google analytics', 'ga4', 'аналитика сайта'),
        'presence': ('ga4', 'google_analytics', 'ga4_property'),
        'setup': 'GA4_PROPERTY_ID/GA4_CREDENTIALS_JSON',
    },
    {
        'label': 'Yandex Direct',
        'keywords': ('яндекс директ', 'yandex direct', 'реклама яндекс', 'direct ads'),
        'presence': ('yandex_direct', 'direct_token', 'ya_direct'),
        'setup': 'YADIRECT_TOKEN',
    },
    {
        'label': 'Yandex Market',
        'keywords': ('яндекс маркет', 'yandex market', 'маркетплейс яндекс'),
        'presence': ('yandex_market', 'ya_market', 'ym_token'),
        'setup': 'YANDEX_MARKET_TOKEN/YANDEX_MARKET_CAMPAIGN_ID',
    },
    {
        'label': 'Avito',
        'keywords': ('авито', 'avito'),
        'presence': ('avito', 'avito_token', 'avito_client'),
        'setup': 'AVITO_CLIENT_ID/AVITO_CLIENT_SECRET',
    },
    {
        'label': 'ClickUp',
        'keywords': ('clickup',),
        'presence': ('clickup', 'clickup_token'),
        'setup': 'CLICKUP_API_KEY',
    },
    {
        'label': 'Linear',
        'keywords': ('linear', 'linear app', 'linear issue'),
        'presence': ('linear', 'linear_token'),
        'setup': 'LINEAR_API_KEY',
    },
    {
        'label': 'MoySklad',
        'keywords': ('мойсклад', 'moysklad', 'мой склад', 'накладная', 'товары склад'),
        'presence': ('moysklad', 'ms_token', 'moysklad_token'),
        'setup': 'MOYSKLAD_TOKEN',
    },
    {
        'label': 'Firebase',
        'keywords': ('firebase', 'firestore', 'realtime database firebase'),
        'presence': ('firebase', 'firebase_key', 'firestore'),
        'setup': 'FIREBASE_CREDENTIALS_JSON',
    },
    {
        'label': 'AWS S3',
        'keywords': ('aws', 's3', 'amazon s3', 'aws bucket', 'загрузи в s3'),
        'presence': ('aws', 's3_bucket', 'aws_access'),
        'setup': 'AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY/AWS_BUCKET',
    },
    {
        'label': 'PostgreSQL / MySQL / MongoDB',
        'keywords': ('postgresql', 'postgres', 'mysql', 'mongodb', 'база данных', 'sql запрос'),
        'presence': ('postgresql', 'postgres', 'mysql', 'mongodb', 'database_url'),
        'setup': 'DATABASE_URL или POSTGRES_URL/MYSQL_URL/MONGO_URI',
    },
    {
        'label': 'CDEK',
        'keywords': ('сдэк', 'cdek', 'доставка', 'отслеживание посылки'),
        'presence': ('cdek', 'cdek_key'),
        'setup': 'CDEK_CLIENT_ID/CDEK_CLIENT_SECRET',
    },
    {
        'label': 'MarineTraffic',
        'keywords': ('marinetraffic', 'marine traffic', 'судно', 'суда', 'флот', 'порт судно', 'vessel', 'mmsi', 'судоходств', 'морск перевозк'),
        'presence': ('marinetraffic', 'marinetraffic_api_key'),
        'setup': 'MARINETRAFFIC_API_KEY',
    },
    {
        'label': 'Почта России',
        'keywords': ('почта росс', 'почта рф', 'pochta', 'otpravka', 'посылк почт', 'отслеживание почт'),
        'presence': ('pochta', 'pochta_access_token', 'pochta_russia'),
        'setup': 'POCHTA_ACCESS_TOKEN/POCHTA_USER_TOKEN',
    },
    {
        'label': 'WhatsApp (Twilio/360dialog)',
        'keywords': ('whatsapp', 'ватсап', 'вотсап'),
        'presence': ('whatsapp_', 'twilio', 'wa_token'),
        'setup': 'TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN',
    },
    {
        'label': '1С Предприятие',
        'keywords': ('1с', '1c', 'onec', '1с предприятие', '1c enterprise'),
        'presence': ('onec', '1c_', '1с_', 'onec_url'),
        'setup': 'ONEC_URL/ONEC_USER/ONEC_PASSWORD',
    },
    {
        'label': 'MS Teams',
        'keywords': ('microsoft teams', 'ms teams', 'teams'),
        'presence': ('ms_teams', 'teams_webhook', 'msteams'),
        'setup': 'TEAMS_WEBHOOK_URL',
    },
    {
        'label': 'Outlook',
        'keywords': ('outlook', 'microsoft mail'),
        'presence': ('outlook', 'outlook_token'),
        'setup': 'OUTLOOK_CLIENT_ID/OUTLOOK_CLIENT_SECRET/OUTLOOK_TENANT_ID',
    },
    {
        'label': 'Yandex Disk',
        'keywords': ('яндекс диск', 'yandex disk', 'yadisk', 'диск яндекс'),
        'presence': ('yandex_disk', 'yadisk', 'yadisk_token'),
        'setup': 'YADISK_TOKEN',
    },
    {
        'label': 'Playwright (браузер)',
        'keywords': ('playwright', 'автоматизация браузера', 'парс сайт', 'скрейп'),
        'presence': ('playwright', 'browser_auto'),
        'setup': 'Playwright установлен в docker-окружении',
    },
    {
        'label': 'Resend',
        'keywords': ('resend', 'transactional email', 'resend email'),
        'presence': ('resend', 'resend_api'),
        'setup': 'RESEND_API_KEY',
    },
    {
        'label': 'Aviasales / Tutu',
        'keywords': ('авиасалес', 'aviasales', 'tutu', 'билеты на самолёт', 'поиск рейсов'),
        'presence': ('aviasales', 'tutu', 'avia_token'),
        'setup': 'AVIASALES_API_KEY',
    },
    {
        'label': 'Google Maps',
        'keywords': ('google maps', 'гугл карты', 'геолокация', 'маршрут', 'место'),
        'presence': ('google_maps', 'gmaps_key'),
        'setup': 'GMAPS_API_KEY',
    },
    {
        'label': 'Google Forms',
        'keywords': ('google forms', 'гугл формы', 'опрос', 'форма для сбора данных'),
        'presence': ('google_forms', 'gforms_'),
        'setup': 'GFORMS_CREDENTIALS_JSON',
    },
    {
        'label': 'OpenAI API (прямое подключение)',
        'keywords': ('openai api', 'gpt api', 'openai direct', 'openai_key'),
        'presence': ('openai_api_key', 'openai_key', 'openai_'),
        'setup': 'OPENAI_API_KEY',
    },
    {
        'label': 'Webhook / HTTP API',
        'keywords': ('webhook', 'вебхук', 'http api', 'http запрос', 'rest api'),
        'presence': ('webhook_url', 'webhook', 'http_api'),
        'setup': 'WEBHOOK_URL',
    },
    {
        'label': 'Strava',
        'keywords': ('strava', 'бег', 'тренировки', 'активности'),
        'presence': ('strava', 'strava_token'),
        'setup': 'STRAVA_CLIENT_ID/STRAVA_CLIENT_SECRET',
    },
]


def _build_missing_integration_hint(user_id: int, user_message: str, final_text: str) -> str:
    """Формирует подсказку о недостающих интеграциях по содержанию запроса пользователя ИЛИ ответа агента."""
    _msg_l = (user_message or '').lower()
    _final_l = (final_text or '').lower()
    # Проверяем и запрос пользователя, и ответ агента
    _combined_l = _msg_l + ' ' + _final_l
    if not _combined_l.strip():
        return ''

    _snapshot = _get_active_agent_integration_snapshot(user_id)
    _caps_l = (_snapshot.get('caps_text') or '').lower()
    _keys_l = (_snapshot.get('keys_text') or '').lower()

    _missing_rules = []
    for _rule in _INTEGRATION_REQUEST_RULES:
        # Проверяем упоминание в запросе пользователя ИЛИ в ответе агента
        _asked = any(_kw in _msg_l for _kw in _rule['keywords'])
        _agent_promised = any(_kw in _final_l for _kw in _rule['keywords'])
        if not _asked and not _agent_promised:
            continue
        _has_it = any((_p in _caps_l) or (_p in _keys_l) for _p in _rule['presence'])
        if _has_it:
            continue
        _already_hinted = (_rule['label'].lower() in _final_l) and ('подключ' in _final_l)
        if not _already_hinted:
            _missing_rules.append(_rule)

    if not _missing_rules:
        return ''

    _missing_services = ', '.join(_r['label'] for _r in _missing_rules[:2])
    _setup_vars = ' или '.join(_r['setup'] for _r in _missing_rules[:2])
    _connected = _snapshot.get('labels') or []
    _connected_short = ', '.join(_connected[:3]) if _connected else 'пока нет активных'

    return (
        f"⚠️ {_missing_services} не подключён. "
        f"Подключено: {_connected_short}. "
        f"Добавь {_setup_vars} в дашборде https://asibiont.com/dashboard"
    )


# ── SSRF-защита: преамбула, которая инжектируется перед кодом агента ─────────
# Патчит urllib.request.urlopen, блокируя запросы во внутренние сети (RFC-1918,
# link-local, loopback). Защищает от атак типа Server-Side Request Forgery,
# даже если AST-валидация на upload-этапе пропустила подозрительный код.
_AGENT_CODE_PREAMBLE = '''\
import urllib.request as _ssrf_ur, socket as _ssrf_sk, ipaddress as _ssrf_ia
_ssrf_sk.setdefaulttimeout(25)
def _ssrf_check_host(host):
    try:
        _ip = _ssrf_ia.ip_address(_ssrf_sk.gethostbyname(host))
        if not _ip.is_global:
            raise PermissionError('SSRF: internal network requests are blocked')
    except (ValueError, OSError):
        pass
# Patch urllib.request.urlopen
_ssrf_orig_open = _ssrf_ur.urlopen
def _ssrf_safe_open(url, *_a, **_kw):
    import re as _ssrf_re
    _u = url.full_url if hasattr(url, 'full_url') else str(url)
    _m = _ssrf_re.search(r'https?://([^/:?#\\s]+)', _u)
    if _m:
        _ssrf_check_host(_m.group(1))
    if len(_a) < 2 and 'timeout' not in _kw:
        _kw['timeout'] = 25
    return _ssrf_orig_open(url, *_a, **_kw)
_ssrf_ur.urlopen = _ssrf_safe_open
# Patch requests library (if available)
try:
    import requests as _ssrf_req
    _ssrf_orig_request = _ssrf_req.Session.request
    def _ssrf_safe_request(self, method, url, *_a2, **_kw2):
        import re as _ssrf_re2
        _m2 = _ssrf_re2.search(r'https?://([^/:?#\\s]+)', str(url))
        if _m2:
            _ssrf_check_host(_m2.group(1))
        if 'timeout' not in _kw2:
            _kw2['timeout'] = 25
        return _ssrf_orig_request(self, method, url, *_a2, **_kw2)
    _ssrf_req.Session.request = _ssrf_safe_request
except ImportError:
    pass
# Patch http.client
try:
    import http.client as _ssrf_hc
    _ssrf_orig_hc_init = _ssrf_hc.HTTPConnection.__init__
    _ssrf_orig_hcs_init = _ssrf_hc.HTTPSConnection.__init__
    def _ssrf_safe_hc_init(self, host, *_a3, **_kw3):
        _h = host.split(':')[0] if isinstance(host, str) else host
        _ssrf_check_host(_h)
        return _ssrf_orig_hc_init(self, host, *_a3, **_kw3)
    def _ssrf_safe_hcs_init(self, host, *_a3, **_kw3):
        _h = host.split(':')[0] if isinstance(host, str) else host
        _ssrf_check_host(_h)
        return _ssrf_orig_hcs_init(self, host, *_a3, **_kw3)
    _ssrf_hc.HTTPConnection.__init__ = _ssrf_safe_hc_init
    _ssrf_hc.HTTPSConnection.__init__ = _ssrf_safe_hcs_init
except Exception as _e:
    logger.debug("suppressed: %s", _e)
# Patch socket.create_connection (blocks raw socket SSRF)
_ssrf_orig_connect = _ssrf_sk.create_connection
def _ssrf_safe_connect(address, *_a4, **_kw4):
    _h = address[0] if isinstance(address, tuple) else str(address)
    _ssrf_check_host(str(_h))
    return _ssrf_orig_connect(address, *_a4, **_kw4)
_ssrf_sk.create_connection = _ssrf_safe_connect
# Auto-strip spaces from App Passwords (Gmail App Password: xxxx xxxx xxxx xxxx -> xxxxxxxxxxxxxxxx)
import os as _fix_os
for _fix_k in list(_fix_os.environ.keys()):
    if "PASS" in _fix_k:
        _fix_os.environ[_fix_k] = _fix_os.environ[_fix_k].replace(" ", "")
# Block dangerous modules — prevent agent code from spawning processes or accessing FS unsafely
import builtins as _sec_b
_sec_orig_import = _sec_b.__import__
_SEC_BLOCKED = frozenset({
    'shutil', 'ctypes', 'importlib', 'code', 'codeop',
    'multiprocessing', 'pty', 'fcntl', 'termios',
    'resource', 'gc', 'pickle', 'shelve', 'marshal',
    # 'signal' removed — imaplib/smtplib/subprocess import it transitively;
    # dangerous calls (raise_signal, alarm) are neutered below instead.
    # 'threading' removed — imaplib imports it transitively;
    # dangerous calls (Thread.start, Timer.start) are neutered below instead.
})
def _sec_safe_import(name, *_a, **_kw):
    _top = name.split('.')[0]
    if _top in _SEC_BLOCKED:
        raise ImportError(f'Module {name!r} is not available in agent sandbox')
    _mod = _sec_orig_import(name, *_a, **_kw)
    # Allow importing subprocess (needed by imaplib) but neuter dangerous calls
    if _top == 'subprocess':
        def _blocked(*_ba, **_bk):
            raise PermissionError('subprocess execution is not allowed in agent sandbox')
        for _attr in ('Popen', 'run', 'call', 'check_output', 'check_call', 'getoutput', 'getstatusoutput'):
            if hasattr(_mod, _attr):
                setattr(_mod, _attr, _blocked)
    # Allow signal (needed by imaplib/smtplib/ssl) but neuter process-killing calls
    if _top == 'signal':
        def _sig_blocked(*_ba, **_bk):
            raise PermissionError('signal manipulation is not allowed in agent sandbox')
        for _attr in ('raise_signal', 'setitimer', 'sigwait', 'sigwaitinfo', 'sigtimedwait'):
            if hasattr(_mod, _attr):
                setattr(_mod, _attr, _sig_blocked)
        # alarm(0) is safe (resets timer); non-zero would disrupt server timeouts — neuter
        if hasattr(_mod, 'alarm'):
            setattr(_mod, 'alarm', lambda _n=0: None)
    # Allow threading (needed by imaplib/smtplib) but prevent thread spawning
    if _top == 'threading':
        def _no_start(self, *_ba, **_bk):
            raise PermissionError('thread.start() is not allowed in agent sandbox')
        for _tcls in ('Thread', 'Timer'):
            if hasattr(_mod, _tcls):
                getattr(_mod, _tcls).start = _no_start
    return _mod
_sec_b.__import__ = _sec_safe_import
'''


def _wrap_agent_code(code: str) -> str:
    """Оборачивает агентский код SSRF-преамбулой.

    Если код содержит ≥2 секций вида  # === Название ===
    каждая выполняется в изолированном пространстве имён:
    - коллизии имён функций/переменных исключены
    - ошибка в одной секции не прерывает остальные
    - добавление/удаление любого количества интеграций безопасно
    """
    import re as _re

    _SECTION_RUNNER = (
        'def _run_section(_src):\n'
        '    _ns = {"__builtins__": __builtins__, "__name__": "__main__"}\n'
        '    exec(compile(_src, "<section>", "exec"), _ns)\n'
        '\n'
    )

    _HDR = _re.compile(r'(?m)^[ \t]*# *=== .+ ===[ \t]*$')
    matches = list(_HDR.finditer(code))

    if len(matches) < 2:
        # Одна секция или нет маркеров — запускаем как раньше
        return _AGENT_CODE_PREAMBLE + code

    # Собираем блоки: что до первого маркера (если есть) + каждая секция
    blocks = []
    pre = code[:matches[0].start()].strip()
    _merge_pre = False
    if pre:
        # Если pre заканчивается незавершённым compound statement (else:, if ...:, for ...:)
        # — объединяем с первой секцией, чтобы не разрывать блок
        _last_line = pre.rstrip().split('\n')[-1].strip()
        if _last_line.endswith(':'):
            _merge_pre = True
        else:
            blocks.append(('# (инициализация)', pre))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(code)
        _block_code = code[m.start():end].strip()
        if i == 0 and _merge_pre:
            _block_code = pre + '\n' + _block_code
        blocks.append((m.group(0).strip(), _block_code))

    lines = [_SECTION_RUNNER]
    for title, block in blocks:
        lines.append(
            f'print({repr(title)})\n'
            f'try:\n'
            f'    _run_section({repr(block)})\n'
            f'except SystemExit:\n'
            f'    raise\n'
            f'except Exception as _e:\n'
            f'    print({repr(title + ": ошибка")}, str(_e))\n'
        )

    return _AGENT_CODE_PREAMBLE + '\n'.join(lines)


# ── Хелпер: разбивает вывод скрипта по именованным секциям ──────────────────
def _parse_integration_sections(output: str, agent_name: str) -> list:
    """
    Пытается обнаружить именованные блоки внутри вывода скрипта:
      === Gmail ===, --- Ozon ---, ## RSS, [TASS] …
    Возвращает список (section_name, content).
    Если секций нет — возвращает [(agent_name, output)].
    """
    import re as _re_sec
    _hdr = _re_sec.compile(
        r'^(?:={2,}\s*(.+?)\s*={2,}|'
        r'-{2,}\s*(.+?)\s*-{2,}|'
        r'#{1,3}\s+(.+?)$|'
        r'\[([A-Za-zА-Яа-яёЁ0-9\- ]{2,40})\]\s*$)',
        _re_sec.MULTILINE,
    )
    matches = list(_hdr.finditer(output))
    if len(matches) < 2:
        return [(agent_name, output)]
    sections = []
    for i, m in enumerate(matches):
        name = next(g for g in m.groups() if g is not None).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(output)
        content = output[start:end].strip()
        if content or True:  # логируем даже пустые
            sections.append((name, content or '—'))
    return sections if sections else [(agent_name, output)]


def _detect_integration_signal(service_label: str, text_lc: str, text_raw: str):
    """
    Минимальная классификация сигнала — AI в _ai_decide_and_compose принимает
    финальное решение о релевантности. Ключевых слов нет — только факт наличия данных.
    Возвращает (priority_str, reason) или (None, None) для пустого вывода.
    """
    text_clean = text_raw.strip()
    if not text_clean or len(text_clean) < 20:
        return (None, None)
    import re as _re_signals
    # Пропускаем строки-заголовки секций вида '# === ... ===' или '=== ... ==='
    _header_pat = _re_signals.compile(r'^#?\s*={2,}.*={2,}\s*$')
    reason = next(
        (l.strip() for l in text_clean.splitlines()
         if l.strip() and not _header_pat.match(l.strip())),
        text_clean[:80]
    )
    return ('MEDIUM', reason[:80])


def spawn_integration_anchors(user_db_id: int, agent_name: str, service_label: str, output: str) -> None:
    """
    Создаёт Anchor(integration_alert) для доставки через AnchorEngine.
    Проверяет только наличие данных в output — AI в _ai_decide_and_compose
    принимает финальное решение о релевантности и формулировке.
    """
    import re as _re_ia, json as _json_ia
    from datetime import datetime as _dt_ia, timezone as _tz_ia, timedelta as _td_ia
    try:
        from models import Anchor as _Anch, AnchorPriority as _AP, Session as _Sess_ia
    except ImportError:
        return

    _PRIORITY_MAP = {
        'CRITICAL': _AP.CRITICAL if hasattr(_AP, 'CRITICAL') else None,
        'HIGH':     None,
        'MEDIUM':   None,
        'LOW':      None,
    }
    # Ленивая инициализация после импорта
    try:
        _PRIORITY_MAP = {
            'CRITICAL': _AP.CRITICAL,
            'HIGH':     _AP.HIGH,
            'MEDIUM':   _AP.MEDIUM,
            'LOW':      _AP.LOW,
        }
    except Exception:
        return

    text_lc = output.lower()
    prio_str, reason = _detect_integration_signal(service_label, text_lc, output)

    if prio_str is None:
        return  # пустой вывод — якорь не нужен

    priority = _PRIORITY_MAP.get(prio_str, _AP.LOW)
    if reason is None:
        reason = '—'

    _now = _dt_ia.now(_tz_ia.utc)
    # CRITICAL/HIGH — cooldown 1 час (source по часу)
    # MEDIUM/LOW — cooldown 4 часа (source по кварталу дня → 0,6,12,18ч)
    if prio_str in ('CRITICAL', 'HIGH'):
        _cooldown_h = 1
        _expires_h = 6
        _src_ts = _now.strftime("%Y-%m-%d-%H")
    elif prio_str == 'MEDIUM':
        _cooldown_h = 4
        _expires_h = 8
        _src_ts = _now.strftime("%Y-%m-%d-") + str((_now.hour // 4) * 4)
    else:  # LOW
        _cooldown_h = 8
        _expires_h = 12
        _src_ts = _now.strftime("%Y-%m-%d")  # 1 якорь в день на сервис
    _src = f'integration:{service_label}:{_src_ts}'

    _ias = _Sess_ia()
    try:
        _exists = _ias.query(_Anch).filter(
            _Anch.user_id == user_db_id,
            _Anch.anchor_type == 'integration_alert',
            _Anch.source == _src,
            _Anch.delivered_at.is_(None),
        ).first()
        if _exists:
            return
        _recent = _ias.query(_Anch).filter(
            _Anch.user_id == user_db_id,
            _Anch.anchor_type == 'integration_alert',
            _Anch.source.like(f'integration:{service_label}:%'),
            _Anch.delivered_at >= _now - _td_ia(hours=_cooldown_h),
        ).first()
        if _recent:
            return
        # Если service_label совпадает с agent_name — не дублируем
        _svc_display = service_label if service_label and service_label != agent_name else ''
        _topic_parts = [agent_name]
        if _svc_display:
            _topic_parts.append(_svc_display)
        _topic_parts.append(reason)
        _topic_str = ': '.join(_topic_parts[:2]) + ' — ' + _topic_parts[-1] if len(_topic_parts) > 1 else reason
        _ias.add(_Anch(
            user_id=user_db_id,
            anchor_type='integration_alert',
            source=_src,
            topic=_topic_str,
            priority=priority,
            data=_json_ia.dumps({
                'agent_name': agent_name,
                'service_label': service_label,
                'signal': reason,
                'snippet': output.strip()[:500],
            }),
            triggered_at=_now,
            expires_at=_now + _td_ia(hours=_expires_h),
            cooldown_hours=_cooldown_h,
            batch_group='integration',
        ))
        _ias.commit()
        logger.info(f'[AGENT] integration_alert anchor → {service_label} ({priority.value}): {reason}')
    except Exception as _ia_e:
        logger.warning(f'[AGENT] spawn_integration_anchors error: {_ia_e}')
        try:
            _ias.rollback()
        except Exception:
            pass
    finally:
        _ias.close()


# === Concurrency controls для 1000+ пользователей ===
# Максимум 20 одновременных вызовов DeepSeek (лимит API ~40 req/s → оставляем запас)
_AI_SEMAPHORE: asyncio.Semaphore | None = None
_MAX_CONCURRENT_AI = 20
# Максимум 2 одновременных AI-запроса на одного пользователя (защита от спама)
_user_ai_in_flight: dict = {}  # user_id -> count

def _get_ai_semaphore() -> asyncio.Semaphore:
    """Lazy init — семафор нужно создавать внутри event loop."""
    global _AI_SEMAPHORE
    if _AI_SEMAPHORE is None:
        _AI_SEMAPHORE = asyncio.Semaphore(_MAX_CONCURRENT_AI)
    return _AI_SEMAPHORE


# === Shared aiohttp session для DeepSeek API ===
# Переиспользование TCP/TLS-соединений экономит ~200-500мс на каждом вызове
_SHARED_AI_SESSION: aiohttp.ClientSession | None = None
_SHARED_AI_SESSION_LOCK: asyncio.Lock | None = None
_SHARED_AI_SESSION_CREATED: float = 0.0
_SESSION_TTL_SECONDS: int = 1800  # 30 minutes

# === Отдельная сессия для фоновых вызовов (координатор/инсайты) ===
# Изолирована от чата: фоновые задачи не конкурируют с ответами пользователям
_QUICK_AI_SESSION: aiohttp.ClientSession | None = None
_QUICK_AI_SESSION_LOCK: asyncio.Lock | None = None
_QUICK_AI_SESSION_CREATED: float = 0.0

def _get_session_lock() -> asyncio.Lock:
    global _SHARED_AI_SESSION_LOCK
    if _SHARED_AI_SESSION_LOCK is None:
        _SHARED_AI_SESSION_LOCK = asyncio.Lock()
    return _SHARED_AI_SESSION_LOCK

def _get_quick_session_lock() -> asyncio.Lock:
    global _QUICK_AI_SESSION_LOCK
    if _QUICK_AI_SESSION_LOCK is None:
        _QUICK_AI_SESSION_LOCK = asyncio.Lock()
    return _QUICK_AI_SESSION_LOCK

async def _get_quick_ai_session() -> aiohttp.ClientSession:
    """Отдельная сессия для фоновых AI-вызовов (не чат).
    limit=5 — максимум 5 параллельных TCP-соединений, чтобы не занимать пул чата."""
    global _QUICK_AI_SESSION, _QUICK_AI_SESSION_CREATED
    import time as _time_mod
    _now = _time_mod.monotonic()
    if (_QUICK_AI_SESSION is not None and not _QUICK_AI_SESSION.closed
            and (_now - _QUICK_AI_SESSION_CREATED) < _SESSION_TTL_SECONDS):
        return _QUICK_AI_SESSION
    async with _get_quick_session_lock():
        _now = _time_mod.monotonic()
        if (_QUICK_AI_SESSION is None or _QUICK_AI_SESSION.closed
                or (_now - _QUICK_AI_SESSION_CREATED) >= _SESSION_TTL_SECONDS):
            if _QUICK_AI_SESSION is not None and not _QUICK_AI_SESSION.closed:
                try:
                    await _QUICK_AI_SESSION.close()
                except Exception:
                    pass
            _connector = aiohttp.TCPConnector(limit=5)
            _QUICK_AI_SESSION = aiohttp.ClientSession(
                connector=_connector,
                timeout=aiohttp.ClientTimeout(total=120, connect=10)
            )
            _QUICK_AI_SESSION_CREATED = _now
            logger.debug("[AI] Created new quick (background) AI session")
    return _QUICK_AI_SESSION

async def _get_shared_ai_session() -> aiohttp.ClientSession:
    global _SHARED_AI_SESSION, _SHARED_AI_SESSION_CREATED
    import time as _time_mod
    _now = _time_mod.monotonic()
    # Check if session is alive and not too old
    if (_SHARED_AI_SESSION is not None and not _SHARED_AI_SESSION.closed
            and (_now - _SHARED_AI_SESSION_CREATED) < _SESSION_TTL_SECONDS):
        return _SHARED_AI_SESSION
    async with _get_session_lock():
        _now = _time_mod.monotonic()
        if (_SHARED_AI_SESSION is None or _SHARED_AI_SESSION.closed
                or (_now - _SHARED_AI_SESSION_CREATED) >= _SESSION_TTL_SECONDS):
            # Close old session if exists
            if _SHARED_AI_SESSION is not None and not _SHARED_AI_SESSION.closed:
                try:
                    await _SHARED_AI_SESSION.close()
                except Exception:
                    pass
            _SHARED_AI_SESSION = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=120, connect=10)
            )
            _SHARED_AI_SESSION_CREATED = _now
            logger.debug("[AI] Created new shared AI session")
    return _SHARED_AI_SESSION


# ===== ЧИСТЫЙ ГИБРИДНЫЙ ПОДХОД =====
# AI с tools решает ВСЁ самостоятельно.
# Никаких keyword guards — DeepSeek сам определяет когда вызывать инструменты.
# tool_choice = "auto" всегда — модель решает.



class HybridAutonomousAgent:
    """
    Адаптивный агент: standard tool calling loop + обучение + force_tool_choice.
    Без мульти-агентного pipeline, без дублированного контекста.
    """

    def __init__(self):
        self.execution_history = []
        self.tool_discovery = tool_discovery
        self._initialize_tools()
        self.active_sessions = 0
        self._active_agent_data: dict = {}  # per-user: {user_id: agent_data} — защита от race condition

        # === Адаптивные фичи (из 73dc138) ===
        self.context_memory = []          # Краткосрочная память контекста
        self.success_patterns = {}        # Паттерны успешных действий
        self.user_preferences = {}        # Предпочтения пользователей
        self._progress_callback = None

        # Загружаем статистику tool discovery
        self.tool_discovery.load_stats()

    def _initialize_tools(self):
        """Инициализирует динамическую систему инструментов."""
        try:
            from . import handlers
            self.tool_discovery.discover_tools_from_module(handlers)
            logger.info(f"[AGENT] Initialized {len(self.tool_discovery.discovered_tools)} dynamic tools")
        except Exception as e:
            logger.error(f"[AGENT] Failed to initialize tools: {e}")

    # ===== AI API =====

    async def call_ai(self, messages, use_tools=False, subscription_tier=None,
                      tool_choice=None, exclude_tools=None, model=None, api_timeout=None, **kwargs):
        """Универсальный вызов DeepSeek API.
        
        Args:
            model: Модель для вызова. По умолчанию DEEPSEEK_MODEL.
            api_timeout: Таймаут HTTP запроса в секундах (None = 120).
        """
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }

        chosen_model = model or DEEPSEEK_MODEL

        data = {
            "model": chosen_model,
            "messages": messages,
            "max_tokens": kwargs.pop("max_tokens", 1800),
            "temperature": kwargs.pop("temperature", 0.7),
            **kwargs
        }

        if use_tools:
            available_tools = get_available_tools(subscription_tier)
            if exclude_tools:
                available_tools = [t for t in available_tools
                                   if t['function']['name'] not in exclude_tools]
            data["tools"] = available_tools
            data["tool_choice"] = tool_choice or "auto"
            logger.info(f"[AI] {len(available_tools)} tools, tier={subscription_tier}, "
                        f"tool_choice={data['tool_choice']}")

        logger.info(f"[AI] Calling model={chosen_model}, tokens={data.get('max_tokens')}")

        async with _get_ai_semaphore():
         _max_retries = 2 if (api_timeout and api_timeout < 40) else 3
         for _attempt in range(_max_retries):
          try:
            # В тестах используем временную сессию, чтобы не оставлять shared session
            # при закрытии event loop pytest.
            if os.getenv('PYTEST_CURRENT_TEST'):
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=120, connect=10)) as _tmp_session:
                    async with _tmp_session.post(url, headers=headers, json=data,
                                                 timeout=aiohttp.ClientTimeout(total=api_timeout or 90)) as resp:
                        if resp.status == 200:
                            result = await resp.json()
                            _usage = result.get('usage', {})
                            _pt = _usage.get('prompt_tokens', 0)
                            _ct = _usage.get('completion_tokens', 0)
                            _cached = _usage.get('prompt_cache_hit_tokens', 0)
                            logger.info(f"[DEEPSEEK] call_ai prompt={_pt}(cache={_cached}) compl={_ct} model={chosen_model}")
                            if use_tools:
                                msg = result.get('choices', [{}])[0].get('message', {})
                                tcs = msg.get('tool_calls', [])
                                if tcs:
                                    logger.info(f"[AI] Called {len(tcs)} tools: "
                                                f"{[tc['function']['name'] for tc in tcs]}")
                                else:
                                    logger.info(f"[AI] No tools called, text response")
                            return result
                        error = await resp.text()
                        if resp.status < 500 or _attempt >= _max_retries - 1:
                            raise Exception(f"AI call failed: {resp.status} {error[:200]}")
                        logger.warning(f"[AI] Server error {resp.status}, retrying ({_attempt+1}/{_max_retries})...")
                        await asyncio.sleep(2 * (_attempt + 1))
            else:
                session = await _get_shared_ai_session()
                async with session.post(url, headers=headers, json=data,
                                        timeout=aiohttp.ClientTimeout(total=api_timeout or 90)) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        _usage = result.get('usage', {})
                        _pt = _usage.get('prompt_tokens', 0)
                        _ct = _usage.get('completion_tokens', 0)
                        _cached = _usage.get('prompt_cache_hit_tokens', 0)
                        logger.info(f"[DEEPSEEK] call_ai prompt={_pt}(cache={_cached}) compl={_ct} model={chosen_model}")
                        if use_tools:
                            msg = result.get('choices', [{}])[0].get('message', {})
                            tcs = msg.get('tool_calls', [])
                            if tcs:
                                logger.info(f"[AI] Called {len(tcs)} tools: "
                                            f"{[tc['function']['name'] for tc in tcs]}")
                            else:
                                logger.info(f"[AI] No tools called, text response")
                        return result
                    error = await resp.text()
                    if resp.status < 500 or _attempt >= _max_retries - 1:
                        raise Exception(f"AI call failed: {resp.status} {error[:200]}")
                    logger.warning(f"[AI] Server error {resp.status}, retrying ({_attempt+1}/{_max_retries})...")
                    await asyncio.sleep(2 * (_attempt + 1))
          except asyncio.TimeoutError:
            if _attempt >= _max_retries - 1:
                raise
            logger.warning(f"[AI] Timeout on attempt {_attempt+1}/{_max_retries}, retrying...")
            await asyncio.sleep(3 * (_attempt + 1))
          except (aiohttp.ClientError, aiohttp.ServerDisconnectedError, ConnectionResetError, OSError) as _conn_err:
            # Connection-level errors: reset shared session and retry
            logger.warning(f"[AI] Connection error on attempt {_attempt+1}/{_max_retries}: {_conn_err}")
            global _SHARED_AI_SESSION
            if _SHARED_AI_SESSION and not os.getenv('PYTEST_CURRENT_TEST'):
                try:
                    await _SHARED_AI_SESSION.close()
                except Exception:
                    pass
                _SHARED_AI_SESSION = None
            if _attempt >= _max_retries - 1:
                raise
            await asyncio.sleep(2 * (_attempt + 1))
         raise Exception("AI call failed: all retries exhausted")

    # ===== SMART TOOL FILTERING (reduces API tokens) =====

    # Core tools sent with every call — all key capabilities ASI needs in normal chat
    CORE_TOOLS = {
        # Task management
        'add_task', 'complete_task', 'edit_task', 'delete_task', 'list_tasks',
        'get_task_details', 'reschedule_task', 'restore_task', 'check_time_conflicts',
        'set_reminder',
        # Goals
        'create_goal', 'delete_goal', 'list_goals', 'update_goal', 'update_goal_progress',
        'complete_goal',
        # Profile / rules
        'update_profile', 'save_user_rule',
        # Research & search — always useful
        'research_topic', 'web_search',
        'get_news_trends',
        # Contacts / outreach
        'find_relevant_contacts_for_task', 'set_contact_alert',
        'save_email_contact', 'list_email_contacts',
        # Content creation
        'create_post', 'generate_image',
        'publish_to_telegram', 'publish_to_discord',
        # Email (commonly requested even without keyword)
        'send_email', 'check_emails',
        # Delegation & agents
        'delegate_task', 'run_agent_action',
        # Universal HTTP API — any external service
        'http_api_request',
        # Agent switching — always available so ASI can proactively route to user's custom agents
        'switch_agent', 'list_marketplace',
        # Campaigns — conversation can span multiple turns
        'start_delegation_campaign',
        # Scheduling & background
        'schedule_background_task',
        # System
        'get_system_status',
    }

    # Extended tool groups — activated by keywords in user message
    TOOL_GROUPS = {
        'email': {
            'keywords': ['email', 'e-mail', 'почт', 'письм', 'отправ', 'переписк',
                         'перегов', 'рассылк', 'campaign', 'кампани', 'лиды', 'лидов',
                         'outreach', 'аутрич', 'холодн'],
            'tools': {'send_email', 'negotiate_by_email', 'list_email_contacts',
                      'save_email_contact', 'send_outreach_email', 'reply_to_outreach_email',
                      'send_follow_up_email'},
        },
        'delegation': {
            'keywords': ['делегир', 'delegat', 'поруч', 'назнач', 'аутсорс',
                         'передай', 'передать', 'исполнител', 'подрядчик', 'фрилансер'],
            'tools': {'delegate_task', 'accept_delegated_task', 'reject_delegated_task',
                      'get_delegation_progress', 'start_delegation_campaign', 'manage_delegation_campaign'},
        },
        'content': {
            'keywords': ['пост', 'post', 'публик', 'publish', 'контент', 'content',
                         'discord', 'telegram', 'канал', 'channel', 'стратег',
                         'запуст', 'продвиж', 'ролик', 'аудитор', 'подписч',
                         'smm', 'соцсет', 'блог', 'статью', 'статья'],
            'tools': {'create_post', 'edit_post', 'delete_post', 'get_posts',
                      'publish_to_telegram', 'publish_to_discord',
                      'set_content_strategy', 'start_content_campaign', 'manage_content_campaign'},
        },
        'messaging': {
            'keywords': ['сообщ', 'message', 'написа', 'напис', 'inbox', 'входящ',
                         'ответить', 'ответь', 'reply', 'переслать', 'перешли',
                         'broadcast', 'рассылк', 'разошл', 'всем пользовател', 'отправь всем', 'напиши всем'],
            'tools': {'send_message_to_user', 'reply_to_user_message',
                      'get_incoming_messages', 'find_and_message_relevant_users',
                      'broadcast_message_to_all_users'},
        },
        'search': {
            'keywords': ['найди', 'найти', 'поиск', 'search', 'ищи', 'искать',
                         'контакт', 'сотрудник', 'партнёр', 'партнер', 'клиент',
                         'специалист', 'разработчик', 'тестировщик', 'дизайнер',
                         'инвестор', 'mentor', 'ментор', 'кандидат'],
            'tools': {'find_relevant_contacts_for_task', 'find_and_message_relevant_users',
                      'web_search', 'set_contact_alert'},
        },
        'marketplace': {
            'keywords': ['маркетплейс', 'marketplace', 'агент', 'agent', '@',
                         'переключ', 'switch'],
            'tools': {'list_marketplace', 'switch_agent'},
        },
    }

    def _select_tools_for_message(self, user_message):
        """Dynamically select tools based on message content.
        Returns set of tool names to EXCLUDE (all not selected).

        SMART TOOL FILTERING — sends only CORE_TOOLS + relevant groups.
        Reduces payload from ~122KB (53 tools) to ~30-40KB (~15-20 tools).
        """
        msg_lower = (user_message or '').lower()
        selected = set(self.CORE_TOOLS)

        for group_name, group_info in self.TOOL_GROUPS.items():
            if any(kw in msg_lower for kw in group_info['keywords']):
                selected |= group_info['tools']

        # ── Адаптивное расширение: если пользователь часто использует инструменты группы,
        # подключаем её даже без ключевых слов в сообщении
        try:
            _hist = get_learner().user_metrics.get(getattr(self, '_current_user_id', 0), {})
            _th = _hist.get('tools_histogram', {})
            if _th:
                for _gn, _gi in self.TOOL_GROUPS.items():
                    _group_uses = sum(_th.get(t, 0) for t in _gi['tools'])
                    if _group_uses >= 5:
                        selected |= _gi['tools']
        except Exception as _e:
            logger.debug("suppressed: %s", _e)

        # Always include save_user_rule (behavioral rules) + run_agent_action
        selected.add('save_user_rule')
        selected.add('run_agent_action')
        selected.add('run_user_script')

        # Get all available tool names
        all_tools = get_available_tools(None)
        all_names = {t['function']['name'] for t in all_tools}

        # Exclude tools NOT in selected set
        return all_names - selected

    # ===== ADAPTIVE TOOL CHOICE =====

    # Умный выбор tool_choice: required для действий, auto для разговора
    def _determine_tool_choice(self, user_message, profile_data=None, tasks_data=None):
        """Возвращает "required" для явных action-запросов (напомни, создай, удали...).
        
        Для чётких запросов на создание/изменение/удаление сущностей — tool_choice="required"
        гарантирует, что DeepSeek вызовет инструмент, а не просто напишет "сделано".
        
        Для Разговорных сообщений, вопросов, анализа — "auto".
        """
        m = (user_message or '').strip().lower()

        # ── ЗАДАЧИ: create ──────────────────────────────────────────────────────
        _add_task_patterns = (
            'напомни ', 'напомни,', 'поставь напоминани', 'поставь напомин',
            'добавь задачу', 'создай задачу', 'новая задача', 'добавь напоминани',
            'add task', 'add reminder', 'set reminder', 'remind me',
            'создай напоминани', 'запиши задачу', 'запиши что',
        )
        if any(m.startswith(p) or p in m for p in _add_task_patterns):
            return "required"

        # ── ЗАДАЧИ: delete / complete / edit ────────────────────────────────────
        _task_action_patterns = (
            'удали задачу', 'удалить задачу', 'убери задачу', 'убери напоминани',
            'отметь задачу', 'отметь как выполн', 'задача выполнена', 'сделал задачу',
            'перенеси задачу', 'измени задачу', 'измени время задачи',
            'delete task', 'remove task', 'complete task', 'mark task',
        )
        if any(p in m for p in _task_action_patterns):
            return "required"

        # ── ЦЕЛИ: create / delete / update ─────────────────────────────────────
        _goal_action_patterns = (
            'создай цель', 'добавь цель', 'новая цель',
            'удали цель', 'убери цель', 'удали цели', 'убери цели',
            'удали все цел',
            'обнови цель', 'прогресс цели',
            'create goal', 'add goal', 'delete goal', 'remove goal',
        )
        if any(p in m for p in _goal_action_patterns):
            return "required"

        # ── ЦЕЛИ: неявные (хочу X, планирую Y) ─────────────────────────────────
        _wish_prefixes = (
            'хочу ', 'хотел бы ', 'хотела бы ',
            'планирую ', 'собираюсь ', 'мечтаю ',
            'намерен ', 'намерена ', 'стремлюсь ',
            'моя цель ', 'мои цели ',
        )
        _wish_garbage = (
            'узнать', 'спросить', 'понять', 'обсудить', 'поговорить',
            'чтобы ты', 'чтоб ты', 'попросить', 'посмотреть',
            'подумать', 'разобраться', 'попробовать',
        )
        if any(m.startswith(p) or (' ' + p) in m or (',' + p) in m for p in _wish_prefixes):
            if not any(g in m for g in _wish_garbage):
                return "required"

        # ── ПРОФИЛЬ: update ─────────────────────────────────────────────────────
        _profile_patterns = (
            'запомни что я', 'запомни, что я', 'обнови профиль', 'измени профиль',
            'я живу в ', 'я работаю в ', 'я работаю как ', 'мой город',
            'save to profile', 'update profile',
        )
        if any(p in m for p in _profile_patterns):
            return "required"

        # ── ПРАВИЛА ─────────────────────────────────────────────────────────────
        _rule_patterns = (
            'запомни правило', 'сохрани правило', 'правило:', 'запомни:', 'всегда ', 'никогда ',
        )
        if any(m.startswith(p) for p in _rule_patterns):
            return "required"

        # Всё остальное — auto (вопросы, анализ, разговор)
        return "auto"

    # Phrases moved to i18n.py — these are fallbacks only
    _TOOL_PROGRESS_MAP = None  # loaded from i18n at runtime
    _THINKING_PHRASES = None
    _DEEP_THINKING_PHRASES = None

    def _get_progress_phrases(self, lang='ru'):
        """Get tool progress phrases for given language from i18n."""
        try:
            from i18n import PROGRESS_PHRASES
            return PROGRESS_PHRASES.get(lang, PROGRESS_PHRASES['ru'])
        except Exception:
            return {}

    def _get_thinking_phrases(self, lang='ru'):
        try:
            from i18n import THINKING_PHRASES
            return THINKING_PHRASES.get(lang, THINKING_PHRASES['ru'])
        except Exception:
            return ['Thinking...'] if lang == 'en' else ['Думаю...']

    def _get_deep_thinking_phrases(self, lang='ru'):
        try:
            from i18n import DEEP_THINKING_PHRASES
            return DEEP_THINKING_PHRASES.get(lang, DEEP_THINKING_PHRASES['ru'])
        except Exception:
            return ['Digging deeper...'] if lang == 'en' else ['Копаю глубже...']

    def _tool_progress_text(self, tool_name, iteration, lang='ru'):
        """Генерирует текст прогресса по имени инструмента."""
        progress_map = self._get_progress_phrases(lang)
        fallback = ['Processing...', 'Thinking...'] if lang == 'en' else ['Обрабатываю запрос...', 'Думаю над этим...', 'Разбираюсь...']
        entry = progress_map.get(tool_name, fallback)
        if isinstance(entry, list):
            text = random.choice(entry)
        else:
            text = entry
        if iteration > 1:
            text = random.choice(self._get_deep_thinking_phrases(lang))
        return text

    # ===== TOKEN BUDGET =====

    # Единый бюджет символов (~3 chars/token для русского текста)
    MAX_PROMPT_CHARS  = 45000  # reduced from 60K: forces trimming of additions to base prompt
    MAX_HISTORY_CHARS = 6000   # ~2K tokens of conversation history

    @staticmethod
    def _estimate_tokens(text):
        """Грубая оценка кол-ва токенов для русского текста (~3 chars/token)."""
        return len(text) // 3 if text else 0

    def _trim_prompt_to_budget(self, base_prompt, history):
        """Обрезает системный промпт и историю до бюджета токенов.
        
        Приоритет сохранения (от высшего к низшему):
        1. Базовый системный промпт (ядро — неприкосновенно)
        2. Последние 4 сообщения истории
        3. Когнитивные подсказки
        4. Мультиагентный контекст
        5. Самообучение / preferences
        6. Старые сообщения истории
        7. Ранее обсуждали / memory
        
        Returns:
            (trimmed_prompt: str, trimmed_history: list)
        """
        _max_prompt  = self.MAX_PROMPT_CHARS
        _max_history = self.MAX_HISTORY_CHARS

        prompt_chars = len(base_prompt)
        history_chars = sum(len(m.get('content', '')) for m in history)
        total = prompt_chars + history_chars
        
        if total <= _max_prompt:
            return base_prompt, history  # Всё влезает
        
        overflow = total - _max_prompt
        trimmed = 0
        logger.info(f"[TOKEN_BUDGET] over by ~{overflow // 3} tokens "
                    f"({prompt_chars} prompt + {history_chars} history chars)")
        
        # 1. Обрезаем историю — оставляем последние 4 сообщения
        if len(history) > 4 and history_chars > _max_history:
            old_len = len(history)
            # Сжимаем старые сообщения: оставляем последние 4
            keep = history[-4:]
            removed_chars = sum(len(m.get('content', '')) for m in history[:-4])
            history = keep
            trimmed += removed_chars
            logger.info(f"[TOKEN_BUDGET] Trimmed history: {old_len} → {len(history)} msgs, "
                       f"freed ~{removed_chars // 3} tokens")
        
        if trimmed >= overflow:
            return base_prompt, history
        
        # 2. Обрезаем секции промпта по приоритету (от наименее важных)
        sections_to_trim = [
            '[РАНЕЕ ОБСУЖДАЛИ:',
            '[ЭМОЦИОНАЛЬНЫЙ ТРЕНД',
            '[ПРОАКТИВНОЕ ДЕЙСТВИЕ',
            '[ПРЕДПОЧТЕНИЯ ПОЛЬЗОВАТЕЛЯ',
            '[СЕМАНТИЧЕСКАЯ ПАМЯТЬ]',
            '[MULTI-AGENT',
            '[ГЛУБОКИЙ АНАЛИЗ R1]',
            '[СИТУАЦИЯ]',
            '[SITUATION]',
            '[КОГНИТИВНЫЕ',
        ]
        
        for marker in sections_to_trim:
            if trimmed >= overflow:
                break
            idx = base_prompt.find(marker)
            if idx == -1:
                continue
            # Ищем конец секции (следующая секция или конец строки)
            next_section = len(base_prompt)
            for other in ['[РАНЕЕ', '[ЭМОЦ', '[ПРОАК', '[ПРЕД', '[MULTI', '[ГЛУБ',
                          '[СТРАТЕГИЯ', '[КОГНИТИВНЫЕ', '\n\n[']:
                pos = base_prompt.find(other, idx + len(marker))
                if pos != -1 and pos < next_section:
                    next_section = pos
            
            removed = base_prompt[idx:next_section]
            base_prompt = base_prompt[:idx] + base_prompt[next_section:]
            trimmed += len(removed)
            logger.info(f"[TOKEN_BUDGET] Trimmed section '{marker[:20]}', "
                       f"freed ~{len(removed) // 3} tokens")
        
        return base_prompt, history

    # ===== КОНТЕКСТ =====

    # Кэш контекста погоды/новостей: {user_id: {'weather': ..., 'news': ..., 'expires': float}}
    _weather_news_cache = {}
    _WEATHER_NEWS_TTL = 900  # 15 мин — не перезапрашиваем API на каждое сообщение

    async def _get_weather_news_cached(self, city):
        """Получить погоду/новости через async api_client с per-user TTL кэшем.
        Избегает блокировки event loop (в отличие от старых sync utils).
        """
        import time as _time
        cache_key = city.lower().strip() if city else "__no_city__"
        cached = self._weather_news_cache.get(cache_key)
        if cached and cached['expires'] > _time.time():
            logger.debug(f"[CTX_CACHE] Using cached weather/news for {city}")
            return cached['weather'], cached['news']

        weather_info = None
        news_info = None
        try:
            from .api_client import get_api_client
            import asyncio as _aio_wn
            api = get_api_client()
            # Параллельный запрос погоды и новостей — экономит ~1-3 сек при холодном кеше
            _w_task = api.get_weather(city, cache_ttl=1800) if city else None
            _n_task = api.get_news(topic=city, page_size=3, cache_ttl=900) if city else None
            if _w_task and _n_task:
                try:
                    weather_data, news_articles = await _aio_wn.wait_for(
                        _aio_wn.gather(_w_task, _n_task, return_exceptions=True),
                        timeout=6.0  # Жёсткий таймаут — не ждём дольше 6 сек
                    )
                except _aio_wn.TimeoutError:
                    weather_data = news_articles = None
                    logger.info("[CTX_CACHE] weather/news timeout (6s) — skipping")
            elif _w_task:
                weather_data = await _aio_wn.wait_for(_w_task, timeout=4.0)
                news_articles = None
            else:
                weather_data = news_articles = None
            if weather_data and not isinstance(weather_data, Exception):
                weather_info = (
                    f"{weather_data['city_name']}: {weather_data['temp']:.0f}°C, "
                    f"{weather_data['description']}, влажность {weather_data['humidity']}%, "
                    f"ветер {weather_data['wind_speed']} м/с"
                )
            if news_articles and not isinstance(news_articles, Exception):
                titles = [f"• {a['title']}" for a in news_articles[:3] if a.get('title')]
                if titles:
                    news_info = f"Новости {city}:\n" + "\n".join(titles)
        except Exception as e:
            logger.warning(f"[CTX_CACHE] Failed to load weather/news via api_client: {e}")

        self._weather_news_cache[cache_key] = {
            'weather': weather_info,
            'news': news_info,
            'expires': _time.time() + self._WEATHER_NEWS_TTL,
        }
        return weather_info, news_info

    async def _build_context(self, user_id, mode=None):
        """Собирает весь контекст пользователя за 1 сессию БД.
        Async: погода/новости загружаются через api_client (не блокируют event loop).
        Кеш 30с: повторные запросы того же user_id не идут в DB.
        
        Args:
            user_id: telegram ID
            mode: 'proactive'|'anchor'|'reminder'|None — для проактивных режимов
                  user_memory минимизируется чтобы AI не цитировал устаревшие данные
        
        Returns: dict с полями для промпта + метаданные.
        """
        import time as _t_ctx
        _cache_key = (user_id, mode)
        if not hasattr(self, '_build_context_cache'):
            self._build_context_cache = {}
        _cached = self._build_context_cache.get(_cache_key)
        if _cached and _cached.get('expires', 0) > _t_ctx.time():
            logger.debug("[CTX] cache hit for user %s (mode=%s)", user_id, mode)
            return _cached['data']
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return None

            # Время
            base_now = datetime.now(pytz.UTC)
            tz_name = user.timezone or 'Europe/Moscow'
            months = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
                      'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']
            try:
                user_tz = pytz.timezone(tz_name)
                user_now = base_now.astimezone(user_tz)
            except Exception:
                user_tz = pytz.timezone('Europe/Moscow')
                user_now = base_now.astimezone(user_tz)
                tz_name = 'Europe/Moscow'

            hour = user_now.hour
            if 6 <= hour < 12: tod = "утро"
            elif 12 <= hour < 18: tod = "день"
            elif 18 <= hour < 23: tod = "вечер"
            else: tod = "ночь"

            time_str = f"{user_now.strftime('%H:%M')} ({tod}, {tz_name})"
            date_str = f"{user_now.day} {months[user_now.month - 1]} {user_now.year}"

            # Профиль
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            profile_data = {}
            weather_info = news_info = None
            if profile:
                for field in ('city', 'company', 'position', 'goals', 'skills',
                              'interests', 'birthdate', 'status_text', 'bio'):
                    val = getattr(profile, field, None)
                    if val:
                        profile_data[field] = val
                if profile.city:
                    # Async weather/news через api_client (не блокирует event loop)
                    weather_info, news_info = await self._get_weather_news_cached(profile.city)
            if user.telegram_channel:
                profile_data['telegram_channel'] = user.telegram_channel
            # Email и телефон пользователя — нужны агенту для подписей и контактов
            if user.email:
                profile_data['email'] = user.email
            if getattr(user, 'phone', None):
                profile_data['phone'] = user.phone

            # Задачи пользователя (для CognitiveEngine strategy)
            tasks_data = []
            try:
                from sqlalchemy import or_ as _or_tasks
                user_tasks = session.query(Task).filter(
                    _or_tasks(
                        Task.user_id == user.id,
                        Task.delegated_to_username.ilike(user.username or '__none__'),
                        Task.delegated_by == user.id,
                    ),
                    Task.status.in_(['pending', 'in_progress']),
                    _or_tasks(Task.delegation_status.is_(None), Task.delegation_status != 'rejected'),
                ).order_by(Task.due_date.asc().nullslast()).limit(20).all()
                for t in user_tasks:
                    task_info = {'id': t.id, 'title': t.title, 'status': t.status}
                    if t.due_date:
                        task_info['deadline'] = t.due_date.isoformat()
                    if t.delegated_to_username:
                        task_info['delegated_to'] = t.delegated_to_username
                        task_info['delegation_status'] = t.delegation_status or 'pending'
                    if t.delegated_by and t.delegated_by != user.id:
                        task_info['delegated_by'] = t.delegated_by
                    tasks_data.append(task_info)

                # Добавляем завершённые за сегодня — AI знает прогресс дня
                from datetime import timedelta as td
                user_today_start = user_now.replace(hour=0, minute=0, second=0, microsecond=0)
                today_start_utc = user_today_start.astimezone(pytz.UTC)
                completed_recent = session.query(Task).filter(
                    Task.user_id == user.id,
                    Task.status == 'completed',
                    Task.actual_completion_time >= today_start_utc
                ).order_by(Task.actual_completion_time.desc()).limit(5).all()
                for t in completed_recent:
                    task_info = {'id': t.id, 'title': t.title, 'status': 'completed'}
                    if t.actual_completion_time:
                        task_info['completed_at'] = t.actual_completion_time.isoformat()
                    tasks_data.append(task_info)
            except Exception as e:
                logger.warning(f"[CTX] Failed to load tasks: {e}")

            # Память
            decrypted_memory = ""
            if user.memory:
                try:
                    from .memory import decrypt_data
                    decrypted_memory = decrypt_data(user.memory)
                except Exception as e:
                    logger.debug(f"Failed to decrypt user memory: {e}")

            # Для проактивных/anchor режимов — НЕ передаём историческую память,
            # но ВСЕГДА передаём rules — они определяют поведение агентов вне зависимости от режима.
            effective_memory = decrypted_memory
            if mode in ('proactive', 'anchor'):
                # Извлекаем только rules из JSON-памяти, остальное — через tool calls
                _rules_only = ""
                if decrypted_memory:
                    try:
                        import json as _json_mem
                        _m = _json_mem.loads(decrypted_memory.strip()) if decrypted_memory.strip().startswith('{') else {}
                        _r = _m.get('rules', [])
                        if _r:
                            _rules_only = _json_mem.dumps({'rules': _r}, ensure_ascii=False)
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)
                effective_memory = _rules_only  # Только правила, без исторического мусора

            # Текущая задача
            current_task_info = None
            if user.current_task_id:
                task = session.query(Task).filter_by(id=user.current_task_id).first()
                if task:
                    current_task_info = {'id': task.id, 'title': task.title,
                                         'status': task.status}

            # Проактивный контекст
            from .context_builder import ContextBuilder
            ctx = ContextBuilder()
            proactive_context = ctx.build_proactive_context(user_id, session)

            # Подписка
            sub_tier = getattr(user, 'subscription_tier', 'LIGHT')

            # Язык пользователя
            user_lang = getattr(user, 'language', 'ru') or 'ru'

            # Базовый промпт — статичный prefix (~53K) + dynamic_context отдельно
            # Это позволяет DeepSeek кеширть весь системный промпт между запросами
            base_prompt, dynamic_context = get_extended_system_prompt(
                user_now=user_now,
                current_time_str=time_str,
                current_date_str=date_str,
                user_username=user.username or ("user" if user_lang == 'en' else "пользователь"),
                mentions_str="",
                user_memory=effective_memory,
                context=None, intent=None,
                subscription_tier=sub_tier,
                message_type=None,
                weather_info=weather_info,
                news_info=news_info,
                profile_data=profile_data,
                proactive_context=proactive_context,
                current_task_info=current_task_info,
                user_id_param=user_id,
                lang=user_lang,
                return_dynamic_separately=True,
            )

            _result = {
                'base_prompt': base_prompt,
                'dynamic_context': dynamic_context,
                'sub_tier': sub_tier,
                'profile_data': profile_data,
                'tasks': tasks_data,
                'user_now': user_now,
                'time_str': time_str,
                'date_str': date_str,
                'user_lang': user_lang,
                'email_patterns': {},
                'contact_preferences': {},
            }

            # ── Intelligence Layer: Email Success Patterns (Improvements #1 + #2) ──
            try:
                from models import EmailOutreach as _EO_ctx
                from sqlalchemy import func as _func_ctx, text as _text_ctx
                # Успешные письма (replied) за последние 90 дней
                _replied = session.query(_EO_ctx).filter(
                    _EO_ctx.user_id == user.id,
                    _EO_ctx.status == 'replied',
                    _EO_ctx.body_length.isnot(None),
                ).order_by(_EO_ctx.reply_at.desc()).limit(20).all()
                if _replied:
                    _lens = [r.body_length for r in _replied if r.body_length]
                    _avg_len = int(sum(_lens) / len(_lens)) if _lens else 0
                    _pers_count = sum(1 for r in _replied if r.has_personalization)
                    _cta_count = sum(1 for r in _replied if r.has_call_to_action)
                    _tones = {}
                    for r in _replied:
                        if r.tone_type:
                            _tones[r.tone_type] = _tones.get(r.tone_type, 0) + 1
                    _best_tone = max(_tones, key=_tones.get) if _tones else None
                    _hours = [r.sent_at_hour_utc for r in _replied if r.sent_at_hour_utc is not None]
                    _avg_hour = int(sum(_hours) / len(_hours)) if _hours else None
                    _result['email_patterns'] = {
                        'total_replies': len(_replied),
                        'avg_body_length': _avg_len,
                        'personalization_rate': round(_pers_count / len(_replied), 2) if _replied else 0,
                        'cta_rate': round(_cta_count / len(_replied), 2) if _replied else 0,
                        'best_tone': _best_tone,
                        'best_send_hour_utc': _avg_hour,
                    }
                    logger.debug('[CTX] Email patterns: %s', _result['email_patterns'])
            except Exception as _e_ep:
                logger.debug('[CTX] email_patterns query failed: %s', _e_ep)

            # ── Intelligence Layer: Contact Preferences (#3) ──
            try:
                from models import EmailContactPreference as _ECP_ctx
                _prefs = session.query(_ECP_ctx).filter(
                    _ECP_ctx.user_id == user.id,
                    _ECP_ctx.emails_replied > 0,
                ).order_by(_ECP_ctx.last_reply_at.desc()).limit(10).all()
                if _prefs:
                    _result['contact_preferences'] = {
                        p.contact_email: {
                            'preferred_length': p.preferred_length,
                            'preferred_tone': p.preferred_tone,
                            'typical_reply_hour': p.typical_reply_hour,
                            'reply_rate': round(p.emails_replied / max(p.emails_received, 1), 2),
                        }
                        for p in _prefs
                    }
            except Exception as _e_cp:
                logger.debug('[CTX] contact_preferences query failed: %s', _e_cp)
            self._build_context_cache[_cache_key] = {
                'data': _result, 'expires': _t_ctx.time() + 30,
            }
            return _result
        finally:
            session.close()

    # ===== EXECUTE =====

    async def _run_external_action(self, params: dict, user_id: int) -> dict:
        """Re-runs agent python_code with AGENT_ACTION env vars to perform write operations."""
        import os as _os_ea, sys as _sys_ea, asyncio as _aio_ea, re as _re_ea
        from difflib import SequenceMatcher as _SM_ea

        # ── Выбор агента: по agent_name (если указан) или active agent ──
        _requested_agent_name = str(params.get('agent_name', '')).strip()
        agent_data = self._active_agent_data.get(user_id)

        if _requested_agent_name:
            # AI запросил конкретного агента по имени — ищем среди всех агентов пользователя
            try:
                from models import Session as _SessAN, UserAgent as _UAAN
                from ai_integration.user_agents import load_agent_personality as _load_ap
                _s_an = _SessAN()
                try:
                    _tg_user = _s_an.execute(
                        __import__('sqlalchemy').text("SELECT id FROM users WHERE telegram_id = :tid"),
                        {"tid": user_id}
                    ).fetchone()
                    if _tg_user:
                        _db_uid = _tg_user[0]
                        _req_lower = _requested_agent_name.lower()
                        _candidates = _s_an.query(_UAAN).filter(
                            _UAAN.author_id == _db_uid,
                            _UAAN.status.in_(('active', 'draft', 'paused')),
                        ).all()
                        for _ca in _candidates:
                            if (_ca.name or '').lower() == _req_lower:
                                _loaded = _load_ap(_ca.id, session=_s_an)
                                if _loaded and _loaded.get('python_code', '').strip():
                                    agent_data = _loaded
                                    logger.info(
                                        "[ACTION] routed to agent '%s' (id=%s) by agent_name param (user=%s)",
                                        _ca.name, _ca.id, user_id,
                                    )
                                break
                finally:
                    _s_an.close()
            except Exception as _an_err:
                logger.debug("[ACTION] agent_name lookup failed: %s", _an_err)

        if not agent_data or not agent_data.get('python_code', '').strip():
            return {"error": "Агент не имеет подключённого скрипта"}
        action = str(params.get('action', '')).strip()
        action_params = params.get('params', {})
        if not isinstance(action_params, dict):
            action_params = {}
        if not action:
            return {"error": "Параметр action не указан"}

        # ── Адаптивная нормализация action по реальным возможностям скрипта ──
        # Этот путь нужен, т.к. run_agent_action в execute_actions может идти напрямую
        # в _run_external_action (мимо handlers.run_agent_action).
        # Платформенные action (send_email, search_users) обрабатываются кодом выше/ниже,
        # а не python_code subprocess — нормализация их убивает.
        _PLATFORM_HANDLED = {'send_email', 'search_users'}
        try:
            _py_src = (agent_data.get('python_code') or '').strip()
            _supported_actions = []
            for _m in _re_ea.finditer(r"ACTION\s*==\s*['\"]([^'\"]+)['\"]", _py_src):
                _a = _m.group(1).strip()
                if _a and _a.lower() not in {x.lower() for x in _supported_actions}:
                    _supported_actions.append(_a)
            for _m in _re_ea.finditer(r"ACTION\s+in\s*\(([^)]+)\)", _py_src):
                for _p in _m.group(1).split(','):
                    _a = _p.strip().strip("'\" ")
                    if _a and _a.lower() not in {x.lower() for x in _supported_actions}:
                        _supported_actions.append(_a)

            _orig_action = action
            _orig_l = _orig_action.lower().strip()
            _supported_l = [s.lower().strip() for s in _supported_actions]

            if _orig_action and _supported_actions and _orig_l not in _supported_l and _orig_l not in _PLATFORM_HANDLED:
                _api_keys = _decrypt_keys(agent_data.get('user_api_keys') or '').lower()
                _ctx = ' '.join([
                    _orig_action,
                    str(action_params or ''),
                    (agent_data.get('name') or ''),
                    (agent_data.get('specialization') or ''),
                    _api_keys,
                ]).lower()

                def _tok(_txt: str) -> set:
                    return {t for t in _re_ea.findall(r'[a-zA-Zа-яА-Я0-9_]{3,}', (_txt or '').lower())}

                _signal_map = {
                    'email': ('email', 'gmail', 'imap', 'inbox', 'outreach', 'reply', 'письм', 'почт'),
                    'rss': ('rss', 'news', 'feed', 'хабр', 'новост', 'стать'),
                    'market': ('market', 'finance', 'alpha', 'vantage', 'stock', 'crypto', 'рын', 'котиров'),
                    'social': ('telegram', 'discord', 'post', 'publish', 'канал', 'пост', 'публик'),
                    'code': ('github', 'repo', 'commit', 'issue', 'pull', 'код', 'разработ'),
                    'crm': ('crm', 'amocrm', 'contact', 'contacts', 'lead', 'сделк', 'контакт', 'воронк'),
                }

                for _cand in _supported_actions:
                    _cand_tokens = tuple(sorted(_tok(_cand)))
                    if not _cand_tokens:
                        continue
                    _family_key = (_cand.lower().split('_', 1)[0] or 'action').strip()
                    _signal_map.setdefault(_family_key, _cand_tokens)

                def _sig(_txt: str) -> set:
                    _res = set()
                    _low = (_txt or '').lower()
                    for _k, _kws in _signal_map.items():
                        if any(_kw in _low for _kw in _kws):
                            _res.add(_k)
                    return _res

                _req_t = _tok(_ctx)
                _req_s = _sig(_ctx)

                _best = _supported_actions[0]
                _best_sc = -1.0
                for _cand in _supported_actions:
                    _cand_l = _cand.lower().strip()
                    _cand_t = _tok(_cand_l)
                    _inter = len(_req_t & _cand_t)
                    _union = max(1, len(_req_t | _cand_t))
                    _j = _inter / _union
                    _lex = _SM_ea(None, _orig_l, _cand_l).ratio()
                    _sov = len(_req_s & _sig(_cand_l))
                    _prefix = 1.0 if (_orig_l.split('_', 1)[0] == _cand_l.split('_', 1)[0]) else 0.0
                    _sc = (_lex * 0.45) + (_j * 0.3) + (_sov * 0.2) + (_prefix * 0.15)
                    if _sc > _best_sc:
                        _best = _cand
                        _best_sc = _sc

                # ── Signal-family mismatch guard ──
                _req_sig = _sig(_orig_l)
                _best_sig = _sig(_best.lower())
                if _req_sig and _best_sig and not (_req_sig & _best_sig):
                    logger.warning(
                        "[ACTION] signal mismatch: %s (%s) vs %s (%s) — skip normalize",
                        _orig_action, _req_sig, _best, _best_sig,
                    )
                else:
                    action = _best
                    logger.info(
                        "[ACTION] adaptive normalize in _run_external_action: %s -> %s (user=%s)",
                        _orig_action,
                        action,
                        user_id,
                    )
        except Exception as _norm_err:
            logger.debug("[ACTION] normalize skipped: %s", _norm_err)

        # ── Перехват send_email: перенаправляем на платформенный handler ──
        # Raw SMTP (python_code subprocess) заблокирован на Railway ("Network is unreachable").
        # Используем платформенный send_email с Resend API fallback.
        if action == 'send_email':
            try:
                from . import handlers as _h_email
                _to = str(action_params.get('to', '')).strip()
                _subject = str(action_params.get('subject', '')).strip()
                _body = str(action_params.get('body', action_params.get('body_text', ''))).strip()
                _sender = agent_data.get('name', 'Агент')
                if not _to or not _subject:
                    return {"status": "error", "error": "Параметры to и subject обязательны для send_email"}
                _email_result = await _h_email.send_email(
                    to=_to, subject=_subject, body=_body,
                    sender_name=_sender, user_id=user_id
                )
                # Log activity
                try:
                    from models import AgentActivityLog as _AALE, Session as _SessE, User as _UserE
                    _s_e = _SessE()
                    try:
                        _u_e = _s_e.query(_UserE).filter_by(telegram_id=user_id).first()
                        if _u_e:
                            _out_e = str(_email_result)
                            _s_e.add(_AALE(
                                user_id=_u_e.id, activity_type='run_agent_action',
                                title=f'{agent_data.get("name", "Агент")} · send_email',
                                content=_out_e, target=agent_data.get('name', 'Агент'),
                                status='completed' if 'error' not in str(_email_result).lower() else 'failed',
                                result=_out_e,
                            ))
                            _s_e.commit()
                    finally:
                        _s_e.close()
                except Exception:
                    pass
                return _email_result if isinstance(_email_result, dict) else {"status": "success", "output": str(_email_result)}
            except Exception as _se_err:
                logger.warning("[ACTION] send_email platform redirect failed: %s", _se_err)
                return {"status": "error", "error": f"Ошибка отправки email: {_se_err}"}

        # ── Platform-native GitHub search: search_users / search_repos / find_contributors / get_user_info ──
        # Перехватывает до python_code, чтобы работать независимо от шаблона агента.
        if action in ('search_users', 'search_repos', 'find_contributors', 'get_contributors',
                      'get_user_info', 'get_github_user'):
            _gh_token = ''
            _gh_repo = ''
            for _kl_gh in (agent_data.get('user_api_keys') or '').splitlines():
                _kl_gh = _kl_gh.strip()
                if '=' in _kl_gh:
                    _vk, _vv = _kl_gh.split('=', 1)
                    _vk_u = _vk.strip().upper()
                    if _vk_u == 'GITHUB_TOKEN':
                        _gh_token = _vv.strip()
                    elif _vk_u == 'GITHUB_REPO':
                        _gh_repo = _vv.strip()
            if _gh_token:
                try:
                    import urllib.request as _ur_gh, urllib.parse as _up_gh, json as _jgh
                    _ghdrs = {
                        'Authorization': f'Bearer {_gh_token}',
                        'Accept': 'application/vnd.github+json',
                        'User-Agent': 'AgentBot/1.0',
                    }

                    def _gh_get(path):
                        _req = _ur_gh.Request('https://api.github.com' + path, headers=_ghdrs)
                        with _ur_gh.urlopen(_req, timeout=9) as _r:
                            return _jgh.loads(_r.read().decode())

                    _gh_out = ''
                    if action == 'search_users':
                        _q_gh = str(action_params.get('query', action_params.get('q', ''))).strip()
                        _page_gh = int(action_params.get('page', 1))
                        if not _q_gh:
                            _q_gh = 'developer repos:>5 followers:>3'
                        _d_gh = _gh_get(f'/search/users?q={_up_gh.quote(_q_gh)}&per_page=10&page={_page_gh}')
                        _items_gh = _d_gh.get('items', [])
                        if not _items_gh:
                            # Диагностика: объясняем агенту ПОЧЕМУ запрос провалился
                            _diag = []
                            if 'location:world' in _q_gh.lower() or 'location:europe' in _q_gh.lower() or 'location:global' in _q_gh.lower():
                                _diag.append('• location: — используй конкретную страну или город (location:russia, location:moscow, location:germany), не регион')
                            if 'topic:' in _q_gh.lower():
                                _diag.append('• topic: — это квалификатор репозиториев, не пользователей; для поиска людей используй language: followers: repos:')
                            if any(w in _q_gh.lower() for w in ('habr', 'хабр', 'medium', 'dev.to', 'habrahabr')):
                                _diag.append('• GitHub не индексирует авторов статей на Habr/Medium — для поиска авторов используй web_search "имя статьи site:habr.com" и ищи профиль автора')
                            # Проверяем наличие незащищённых ключевых слов (без квалификатора)
                            _valid_qualifiers = ('language:', 'followers:', 'repos:', 'location:', 'in:', 'created:', 'type:', 'user:')
                            _words = _q_gh.split()
                            _plain_words = [w for w in _words if not any(w.lower().startswith(q) for q in _valid_qualifiers)]
                            if len(_plain_words) > 2:
                                _diag.append(f'• Слова без квалификатора ({", ".join(_plain_words[:4])}) дают широкий поиск — упрости до 1-2 или добавь language:/location:/followers:')
                            _diag_str = '\n'.join(_diag) if _diag else '• Попробуй упростить запрос: убери лишние слова, используй только language: followers: location: (конкретная страна)'
                            _gh_out = (
                                f'По запросу «{_q_gh}» пользователей GitHub не найдено.\n'
                                f'Вероятные причины:\n{_diag_str}\n'
                                f'Правильные примеры: "language:python followers:>10 location:russia" | "language:python machine-learning" | "bioinformatics followers:>5"\n'
                                f'Альтернативы: web_search "bioinformatics developer github" или LinkedIn поиск через web_search site:linkedin.com'
                            )
                        else:
                            _lines_gh = [f'Найдено {_d_gh.get("total_count", 0)} пользователей (стр.{_page_gh}):']
                            for _u_gh in _items_gh:
                                _uname = _u_gh.get('login', '')
                                try:
                                    _ud = _gh_get(f'/users/{_uname}')
                                    _em = _ud.get('email') or ''
                                    _nm = _ud.get('name') or _uname
                                    _bio = (_ud.get('bio') or '')[:60]
                                    _ln = f'@{_uname} | {_nm}'
                                    if _em:
                                        _ln += f' | email: {_em}'
                                    if _bio:
                                        _ln += f' | {_bio}'
                                    _ln += f' | repos: {_ud.get("public_repos",0)} followers: {_ud.get("followers",0)}'
                                except Exception:
                                    _ln = f'@{_uname} | {_u_gh.get("html_url","")}'
                                _lines_gh.append(_ln)
                            _gh_out = '\n'.join(_lines_gh)

                    elif action == 'search_repos':
                        _q_gh = str(action_params.get('query', action_params.get('q', ''))).strip()
                        if not _q_gh:
                            _q_gh = 'stars:>50 language:python'
                        _d_gh = _gh_get(f'/search/repositories?q={_up_gh.quote(_q_gh)}&per_page=10&sort=stars')
                        _items_gh = _d_gh.get('items', [])
                        if not _items_gh:
                            _gh_out = f'По запросу «{_q_gh}» репозиториев не найдено.'
                        else:
                            _lines_gh = [f'Найдено {_d_gh.get("total_count",0)} репозиториев:']
                            for _rp_gh in _items_gh:
                                _lines_gh.append(
                                    f'{_rp_gh.get("full_name","")} | ⭐{_rp_gh.get("stargazers_count",0)} | '
                                    f'{(_rp_gh.get("description") or "")[:80]} | '
                                    f'{_rp_gh.get("html_url","")}'
                                )
                            _gh_out = '\n'.join(_lines_gh)

                    elif action in ('find_contributors', 'get_contributors'):
                        _repo_gh = str(action_params.get('repo', action_params.get('repository', _gh_repo))).strip()
                        if not _repo_gh:
                            _gh_out = '⚠️ Укажи params.repo=owner/repo'
                        else:
                            _items_gh = _gh_get(f'/repos/{_repo_gh}/contributors?per_page=20')
                            if not isinstance(_items_gh, list) or not _items_gh:
                                _gh_out = f'Контрибьюторов в {_repo_gh} не найдено.'
                            else:
                                _lines_gh = [f'Контрибьюторы {_repo_gh} (топ {len(_items_gh)}):']
                                for _c_gh in _items_gh[:15]:
                                    _lines_gh.append(f'@{_c_gh.get("login","")} — {_c_gh.get("contributions",0)} коммитов')
                                _gh_out = '\n'.join(_lines_gh)

                    elif action in ('get_user_info', 'get_github_user'):
                        _uname = str(action_params.get('username', action_params.get('user', ''))).strip()
                        if not _uname:
                            _gh_out = '⚠️ Укажи params.username'
                        else:
                            _ud = _gh_get(f'/users/{_uname}')
                            _gh_out = (
                                f'@{_ud.get("login","")} | {_ud.get("name","") or _uname} | '
                                f'email: {_ud.get("email") or "не указан"} | '
                                f'repos: {_ud.get("public_repos",0)} | followers: {_ud.get("followers",0)} | '
                                f'bio: {(_ud.get("bio") or "")[:100]}'
                            )

                    if _gh_out:
                        try:
                            from models import AgentActivityLog as _AALGH, Session as _SessGH, User as _UserGH
                            _s_gh = _SessGH()
                            try:
                                _u_gh2 = _s_gh.query(_UserGH).filter_by(telegram_id=user_id).first()
                                if _u_gh2:
                                    _s_gh.add(_AALGH(
                                        user_id=_u_gh2.id, activity_type='run_agent_action',
                                        title=f'{agent_data.get("name","Агент")} · {action}',
                                        content=_gh_out[:500], target=agent_data.get('name', 'Агент'),
                                        status='completed', result=_gh_out[:500],
                                    ))
                                    _s_gh.commit()
                            finally:
                                _s_gh.close()
                        except Exception:
                            pass
                        return {"status": "success", "output": _gh_out}
                except Exception as _gh_err:
                    logger.warning("[ACTION] native GitHub %s failed: %s — falling to python_code", action, _gh_err)
                    # Fall through to python_code subprocess

        # ── Валидация query для GitHub search_users ──
        # AI иногда передаёт email-адреса или названия задач как query → 0 результатов.
        # Перехватываем здесь и заменяем на валидный дефолт.
        if action == 'search_users':
            import re as _re_ghv
            _raw_query = str(action_params.get('query', '')).strip()
            _is_bad_query = False
            # Признаки плохого query:
            # 1. Содержит email-адрес
            if _re_ghv.search(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', _raw_query):
                _is_bad_query = True
            # 2. Не содержит ни одного GitHub-квалификатора (language:, repos:, followers:, location:, type:)
            elif not _re_ghv.search(r'\b(language|repos|followers|location|type)\s*:', _raw_query, _re_ghv.IGNORECASE):
                # Если содержит 3+ слова без квалификаторов — может быть свободным поиском (допускаем)
                # Но если похоже на название задачи (email_analysis etc.) — заменяем
                _word_count = len(_raw_query.split())
                if _word_count <= 3 and not any(c.isdigit() for c in _raw_query):
                    _is_bad_query = True
            if _is_bad_query:
                # Build a meaningful fallback from the raw query words instead of hardcoded search
                _q_words = [w for w in _raw_query.split() if len(w) > 2 and '@' not in w][:3]
                _safe_default = ' '.join(_q_words) + ' repos:>5 followers:>3' if _q_words else 'repos:>5 followers:>3'
                logger.warning(
                    "[ACTION] search_users bad query=%r → replacing with safe default=%r",
                    _raw_query, _safe_default,
                )
                action_params = dict(action_params)
                action_params['query'] = _safe_default
                # Оповещаем через output что заменили запрос
                _fix_note = (
                    f"⚠️ Запрос '{_raw_query}' дополнен квалификаторами GitHub: '{_safe_default}'\n"
                    f"Допустимые квалификаторы: language:, repos:, followers:, location:\n"
                )
            else:
                _fix_note = ''
        else:
            _fix_note = ''

        py_code = _wrap_agent_code(agent_data['python_code'].strip())
        api_keys_raw = agent_data.get('user_api_keys', '') or ''
        _is_linux_ea = _sys_ea.platform != 'win32'
        env = {
            'PATH': _os_ea.environ.get('PATH', '/usr/bin:/bin'),
            'PYTHONIOENCODING': 'utf-8',
            'AGENT_ACTION': action,
        }
        if not _is_linux_ea:
            # Windows ребует системные переменные для инициализации Python
            for _wk in ('SystemRoot', 'SystemDrive', 'TEMP', 'TMP', 'WINDIR',
                        'COMSPEC', 'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH'):
                if _wk in _os_ea.environ:
                    env[_wk] = _os_ea.environ[_wk]
        else:
            env['HOME'] = _os_ea.environ.get('HOME', '/tmp')
        for _kline in api_keys_raw.splitlines():
            _kline = _kline.strip()
            if '=' in _kline and not _kline.startswith('#'):
                _k, _, _v = _kline.partition('=')
                env[_k.strip()] = _v.strip()
        for _k, _v in action_params.items():
            env[f'AGENT_PARAM_{str(_k).upper()}'] = '' if _v is None else str(_v)
        _is_linux = _sys_ea.platform != 'win32'
        def _resource_limits():
            try:
                import resource as _res
                _mem = 64 * 1024 * 1024   # 64 MB RAM
                _res.setrlimit(_res.RLIMIT_AS, (_mem, _mem))
                _cpu = 12                  # 12 сек CPU-времени
                _res.setrlimit(_res.RLIMIT_CPU, (_cpu, _cpu))
                _files = 32                # не более 32 open file descriptors
                _res.setrlimit(_res.RLIMIT_NOFILE, (_files, _files))
            except Exception as _e:
                logger.debug("suppressed: %s", _e)
        try:
            _kwargs = dict(stdout=_aio_ea.subprocess.PIPE, stderr=_aio_ea.subprocess.PIPE, env=env)
            if _is_linux:
                _kwargs['preexec_fn'] = _resource_limits
            proc = await _aio_ea.create_subprocess_exec(_sys_ea.executable, '-c', py_code, **_kwargs)
            _comm_task = _aio_ea.create_task(proc.communicate())
            try:
                stdout, stderr = await _aio_ea.wait_for(_comm_task, timeout=float(API_TIMEOUT_SCRIPT))
                _raw_out = stdout.decode('utf-8', errors='replace').strip()[:32000]
                err = stderr.decode('utf-8', errors='replace').strip()[:500]
                # ── Извлечь вывод только релевантной секции (если скрипт многосекционный) ──
                # python_code содержит несколько интеграций (Gmail, GitHub, AmoCRM, ...),
                # все выполняются последовательно. Без фильтрации Gmail-inbox забивает вывод.
                # ВАЖНО: парсим секции из ПОЛНОГО вывода (до truncate), иначе
                # Gmail (24 письма) может съесть весь буфер и скрыть GitHub/AmoCRM секции.
                out = _raw_out
                if action and _raw_out and '# ===' in _raw_out:
                    _ACTION_SECTION_MAP = {
                        # AmoCRM
                        'create_lead': 'amo', 'create_contact': 'amo', 'search_contacts': 'amo',
                        'get_contact': 'amo', 'add_note': 'amo', 'link_contact_to_lead': 'amo',
                        'get_leads': 'amo', 'update_lead': 'amo', 'create_deal': 'amo',
                        'get_pipelines': 'amo', 'link_contact': 'amo', 'get_contacts': 'amo',
                        'get_lead': 'amo', 'delete_lead': 'amo', 'get_tasks': 'amo', 'create_task': 'amo',
                        # Bitrix24 / HubSpot
                        'create_deal_bitrix': 'bitrix', 'get_deals': 'bitrix', 'update_deal': 'bitrix',
                        'bitrix_leads': 'bitrix', 'get_hubspot_contacts': 'hubspot',
                        'create_hubspot_contact': 'hubspot', 'hubspot_deals': 'hubspot',
                        # Gmail / IMAP
                        'send_email': 'gmail', 'check_inbox': 'gmail', 'check_emails': 'gmail',
                        'reply_email': 'gmail', 'get_email': 'gmail', 'mark_read': 'gmail',
                        # GitHub
                        'create_issue': 'github', 'list_issues': 'github', 'update_issue': 'github',
                        'search_users': 'github', 'find_contributors': 'github',
                        'search_repos': 'github', 'star_repo': 'github', 'comment_on_issue': 'github',
                        'get_user_info': 'github', 'fork_repo': 'github',
                        # GitLab
                        'gitlab_issues': 'gitlab', 'create_mr': 'gitlab', 'gitlab_search': 'gitlab',
                        # RSS
                        'check_news': 'rss', 'read_rss': 'rss', 'check_news_and_markets': 'rss',
                        'check_markets': 'rss', 'fetch_rss': 'rss', 'get_feed': 'rss',
                        # Metrika
                        'yandex_metrika_report': 'metrika', 'get_metrika': 'metrika',
                        'get_analytics': 'metrika', 'analytics_report': 'metrika',
                        # Notion
                        'create_page': 'notion', 'update_page': 'notion', 'search_notion': 'notion',
                        'get_database': 'notion', 'add_to_database': 'notion', 'notion_search': 'notion',
                        # Google Sheets
                        'read_sheet': 'sheets', 'add_row': 'sheets', 'update_sheet': 'sheets',
                        'append_row': 'sheets', 'get_sheet': 'sheets', 'update_row': 'sheets',
                        # Slack
                        'post_message': 'slack', 'send_slack': 'slack', 'list_channels': 'slack',
                        'get_slack_messages': 'slack', 'slack_message': 'slack',
                        # Ozon / Wildberries
                        'get_products': 'marketplace', 'get_orders': 'marketplace',
                        'get_stocks': 'marketplace', 'get_revenue': 'marketplace',
                        'ozon_products': 'marketplace', 'wb_products': 'marketplace',
                        'ozon_orders': 'marketplace', 'wb_orders': 'marketplace',
                        # Jira
                        'search_issues_jira': 'jira', 'create_jira_issue': 'jira',
                        'get_sprint': 'jira', 'jira_issues': 'jira', 'update_jira': 'jira',
                        # Trello
                        'create_card': 'trello', 'list_boards': 'trello', 'move_card': 'trello',
                        'get_board': 'trello', 'trello_card': 'trello', 'add_trello_card': 'trello',
                        # Stripe / payments
                        'get_charges': 'stripe', 'get_revenue_stripe': 'stripe',
                        'create_customer': 'stripe', 'list_subscriptions': 'stripe',
                        'stripe_report': 'stripe', 'get_payments': 'stripe',
                        # Binance / Bybit
                        'get_balance': 'crypto', 'get_price': 'crypto', 'get_ticker': 'crypto',
                        'binance_balance': 'crypto', 'bybit_balance': 'crypto',
                        # hh.ru / SuperJob
                        'search_vacancies': 'hh', 'search_resumes': 'hh',
                        'get_vacancies': 'hh', 'post_vacancy': 'hh',
                        # Yandex Mail / Mail.ru
                        'send_yandex_email': 'yandex_mail', 'check_yandex': 'yandex_mail',
                        'send_mailru': 'mailru', 'check_mailru': 'mailru',
                        # Telegram Bot
                        'send_telegram': 'telegram_bot', 'telegram_notify': 'telegram_bot',
                        'get_telegram_messages': 'telegram_bot', 'telegram_broadcast': 'telegram_bot',
                        # VK
                        'vk_post': 'vk', 'vk_message': 'vk', 'get_vk_stats': 'vk', 'vk_group': 'vk',
                        # YouTube
                        'get_videos': 'youtube', 'youtube_stats': 'youtube', 'upload_video': 'youtube',
                        'get_channel_stats': 'youtube', 'youtube_search': 'youtube',
                        # CoinGecko
                        'get_coin_price': 'coingecko', 'coingecko_price': 'coingecko',
                        'get_market_cap': 'coingecko', 'coin_info': 'coingecko',
                        # Calendly
                        'get_events': 'calendly', 'create_invite': 'calendly', 'calendly_slots': 'calendly',
                        'schedule_meeting': 'calendly', 'get_calendly': 'calendly',
                        # Resend
                        'send_transactional': 'resend', 'resend_email': 'resend',
                        # Databases (PostgreSQL / MySQL / MongoDB / Redis)
                        'query_db': 'database', 'insert_row': 'database', 'update_db': 'database',
                        'pg_query': 'database', 'mysql_query': 'database', 'mongo_find': 'database',
                        'redis_get': 'database', 'redis_set': 'database', 'db_query': 'database',
                        # AI APIs (OpenAI / Replicate / Gemini)
                        'generate_text': 'ai_api', 'openai_complete': 'ai_api',
                        'replicate_run': 'ai_api', 'gemini_generate': 'ai_api',
                        # Twilio / SMS.ru
                        'send_sms': 'sms', 'sms_notify': 'sms', 'twilio_sms': 'sms',
                        'make_call': 'sms', 'send_sms_ru': 'sms',
                        # Playwright / Scraping
                        'scrape_page': 'scraping', 'playwright_scrape': 'scraping',
                        'get_page_content': 'scraping', 'navigate_page': 'scraping',
                        'fill_form': 'scraping',
                        # Firebase
                        'firebase_query': 'firebase', 'get_document': 'firebase',
                        'set_document': 'firebase', 'firebase_push': 'firebase',
                        # AWS S3
                        'upload_file': 'aws', 'download_file': 'aws', 's3_list': 'aws',
                        'get_presigned_url': 'aws', 's3_upload': 'aws',
                        # Avito
                        'avito_ads': 'avito', 'get_avito_messages': 'avito',
                        'create_avito_ad': 'avito', 'update_avito_ad': 'avito',
                        # Yandex Direct
                        'get_campaigns': 'ya_direct', 'get_ad_stats': 'ya_direct',
                        'update_campaign': 'ya_direct', 'yandex_direct_stats': 'ya_direct',
                        # Yandex Market
                        'get_ym_products': 'ya_market', 'ya_market_orders': 'ya_market',
                        'yandex_market_offers': 'ya_market',
                        # MoySklad
                        'get_ms_products': 'moysklad', 'create_ms_order': 'moysklad',
                        'moysklad_inventory': 'moysklad', 'ms_report': 'moysklad',
                        # Travel (Aviasales / Tutu / Flightradar)
                        'search_flights': 'travel', 'get_tickets': 'travel',
                        'aviasales_search': 'travel', 'tutu_search': 'travel',
                        'flightradar_status': 'travel',
                        # MarineTraffic (судоходство)
                        'track_vessel': 'marinetraffic', 'search_vessels': 'marinetraffic',
                        'port_vessels': 'marinetraffic', 'vessel_route': 'marinetraffic',
                        # Почта России
                        'pochta_track': 'pochta', 'pochta_tariff': 'pochta',
                        'normalize_address': 'pochta', 'calculate_tariff_pochta': 'pochta',
                        # Google Calendar
                        'create_event': 'gcalendar', 'list_events': 'gcalendar',
                        'update_event': 'gcalendar', 'delete_event': 'gcalendar',
                        'get_calendar': 'gcalendar',
                        # Tinkoff Invest
                        'tinkoff_portfolio': 'tinkoff', 'get_tinkoff_balance': 'tinkoff',
                        'tinkoff_operations': 'tinkoff',
                        # Financial data APIs
                        'alphavantage_query': 'findata', 'finnhub_news': 'findata',
                        'twelvedata_price': 'findata', 'yahoo_quote': 'findata',
                        'polygon_tickers': 'findata', 'fmp_query': 'findata',
                        # CDEK
                        'cdek_track': 'cdek', 'create_cdek_order': 'cdek', 'cdek_status': 'cdek',
                        # WhatsApp
                        'send_whatsapp': 'whatsapp', 'whatsapp_message': 'whatsapp',
                        'whatsapp_broadcast': 'whatsapp',
                        # 1C
                        'onec_query': 'onec', 'get_onec_data': 'onec', 'onec_product': 'onec',
                        # Google Drive
                        'upload_gdrive': 'gdrive', 'list_gdrive': 'gdrive',
                        'get_gdrive_file': 'gdrive', 'share_file': 'gdrive',
                        # MS Teams
                        'send_teams': 'msteams', 'teams_message': 'msteams',
                        # Outlook
                        'send_outlook': 'outlook', 'check_outlook': 'outlook',
                        'outlook_calendar': 'outlook',
                        # ClickUp
                        'create_task_clickup': 'clickup', 'get_tasks_clickup': 'clickup',
                        'update_clickup': 'clickup', 'clickup_list': 'clickup',
                        # Yandex Disk
                        'ydisk_upload': 'yadisk', 'ydisk_list': 'yadisk', 'yadisk_share': 'yadisk',
                        # GA4
                        'ga4_report': 'ga4', 'get_ga4_metrics': 'ga4', 'ga4_query': 'ga4',
                        # Linear
                        'create_linear_issue': 'linear', 'get_linear_issues': 'linear',
                        'linear_update': 'linear',
                        # Google Maps
                        'maps_search': 'gmaps', 'get_place_info': 'gmaps', 'gmaps_route': 'gmaps',
                        # Google Forms
                        'create_form': 'gforms', 'get_form_responses': 'gforms',
                        # Strava
                        'get_strava_activities': 'strava', 'strava_stats': 'strava',
                        # Webhook / HTTP API
                        'trigger_webhook': 'webhook', 'send_webhook': 'webhook',
                        'http_get': 'webhook', 'http_request': 'webhook', 'api_call': 'webhook',
                        # Twitter
                        'post_tweet': 'twitter', 'tweet': 'twitter', 'twitter_search': 'twitter',
                        # LinkedIn
                        'linkedin_post': 'linkedin', 'get_linkedin_profile': 'linkedin',
                        # Airtable
                        'airtable_query': 'airtable', 'airtable_insert': 'airtable',
                        'airtable_update': 'airtable',
                        # OpenWeather
                        'get_weather': 'weather', 'weather_forecast': 'weather',
                        # OpenWeather / CRM move/stale
                        'move_lead_stage': 'amo', 'advance_deal': 'amo', 'move_deal': 'amo',
                        'get_stale_leads': 'amo', 'check_pipeline': 'amo',
                    }
                    _SEC_KEYWORDS = {
                        'amo': ('amocrm', 'amo'),
                        'bitrix': ('bitrix',),
                        'hubspot': ('hubspot',),
                        'gmail': ('gmail',),
                        'github': ('github',),
                        'gitlab': ('gitlab',),
                        'rss': ('rss',),
                        'metrika': ('метрик', 'metrik'),
                        'notion': ('notion',),
                        'sheets': ('sheet', 'google sheet', 'табл'),
                        'slack': ('slack',),
                        'marketplace': ('ozon', 'wildberries', 'wb', 'shopify', 'маркет'),
                        'jira': ('jira',),
                        'trello': ('trello',),
                        'stripe': ('stripe',),
                        'crypto': ('binance', 'bybit', 'coinbase', 'crypto'),
                        'hh': ('hh.ru', 'headhunter', 'superjob', 'hh_'),
                        'yandex_mail': ('yandex mail', 'яндекс почт', 'yandexmail'),
                        'mailru': ('mail.ru', 'mailru', 'майл'),
                        'telegram_bot': ('telegram bot', 'тг бот', 'телеграм бот', 'bot_token'),
                        'vk': ('вконтакт', 'vkontakte', 'vk_'),
                        'youtube': ('youtube', 'ютуб'),
                        'coingecko': ('coingecko', 'gecko'),
                        'calendly': ('calendly',),
                        'resend': ('resend',),
                        'database': ('postgresql', 'postgres', 'mysql', 'mongodb', 'redis'),
                        'ai_api': ('openai', 'replicate', 'gemini'),
                        'sms': ('twilio', 'sms.ru', 'sms_ru'),
                        'scraping': ('playwright', 'scraping', 'scrape'),
                        'firebase': ('firebase',),
                        'aws': ('aws', 'amazon s3', 's3'),
                        'avito': ('avito', 'авито'),
                        'ya_direct': ('yandex direct', 'яндекс директ', 'direct_'),
                        'ya_market': ('yandex market', 'яндекс маркет', 'ya_market'),
                        'moysklad': ('мойсклад', 'moysklad'),
                        'travel': ('aviasales', 'tutu', 'flightradar', 'авиа'),
                        'gcalendar': ('google calendar', 'gcalendar', 'google cal'),
                        'tinkoff': ('тинькофф', 'tinkoff'),
                        'findata': ('alphavantage', 'finnhub', 'twelvedata', 'polygon', 'yahoo finance', 'fmp'),
                        'cdek': ('сдэк', 'cdek'),
                        'marinetraffic': ('marinetraffic', 'marine traffic', 'судно', 'суда', 'морск', 'порт', 'vessel', 'mmsi'),
                        'pochta': ('почта росс', 'почта рф', 'pochta', 'otpravka', 'otpravka.pochta'),
                        'whatsapp': ('whatsapp', 'ватсап'),
                        'onec': ('1c ', '1с ', 'onec', '1c_', '1с_'),
                        'gdrive': ('google drive', 'gdrive', 'googledr'),
                        'msteams': ('ms teams', 'microsoft teams', 'teams_'),
                        'outlook': ('outlook',),
                        'clickup': ('clickup',),
                        'yadisk': ('яндекс диск', 'yandex disk', 'yadisk'),
                        'ga4': ('google analytics', 'ga4'),
                        'linear': ('linear',),
                        'gmaps': ('google maps', 'gmaps', 'гугл карт'),
                        'gforms': ('google forms', 'gforms', 'гугл форм'),
                        'strava': ('strava',),
                        'webhook': ('webhook', 'вебхук', 'http_api'),
                        'twitter': ('twitter', 'твиттер'),
                        'linkedin': ('linkedin', 'линкедин'),
                        'airtable': ('airtable',),
                        'weather': ('openweather', 'weather', 'погода'),
                    }
                    _target = _ACTION_SECTION_MAP.get(action.lower(), '')
                    if _target:
                        _sections = _parse_integration_sections(out, 'agent')
                        _matched = None
                        _rest_parts = []
                        for _sname, _scontent in _sections:
                            _sname_l = _sname.lower()
                            _kws = _SEC_KEYWORDS.get(_target, ())
                            if any(_kw in _sname_l for _kw in _kws):
                                _matched = _scontent
                            else:
                                _rest_parts.append((_sname, _scontent))
                        if _matched:
                            # Ставим целевую секцию первой, остальные коротко
                            out = _matched
                            for _rn, _rc in _rest_parts:
                                if len(out) > 1600:
                                    break
                                _rc_lines = [l.strip() for l in _rc[:200].split('\n') if l.strip()]
                                _rc_short = _rc_lines[0] if _rc_lines else ''
                                # Если первая строка — заголовок (заканчивается на ':'), дописываем следующую
                                if _rc_short.endswith(':') and len(_rc_lines) > 1:
                                    _rc_short = _rc_short + ' ' + _rc_lines[1]
                                if _rc_short and _rc_short != '—':
                                    out += f'\n[{_rn}: {_rc_short}]'
                        elif _sections:
                            # Секции найдены, но целевая (github/amo/...) отсутствует —
                            # интеграция не настроена. НЕ возвращаем raw output.
                            _kw_label = _target.upper()
                            logger.warning("[EXT-ACT] action=%s target=%s NOT found in script sections: %s",
                                           action, _target, [s[0] for s in _sections])
                            out = (
                                f"⛔ Действие «{action}» требует интеграцию {_kw_label}, "
                                f"которая НЕ настроена у этого агента. "
                                f"Используй ДРУГОЙ инструмент или стратегию."
                            )
                    elif '# ===' in out:
                        # Общий action (get_report и т.п.) — показываем ВСЕ секции компактно,
                        # чтобы Gmail не забивал буфер и другие интеграции были видны
                        _sections = _parse_integration_sections(out, 'agent')
                        if _sections:
                            _compact_parts = []
                            for _sname, _scontent in _sections:
                                _budget = max(300, 1800 // len(_sections))
                                _compact_parts.append(f"[{_sname}]\n{_scontent[:_budget]}")
                            out = '\n\n'.join(_compact_parts)
                out = out[:2000]
            except _aio_ea.TimeoutError:
                proc.kill()
                try:
                    await _comm_task
                except BaseException:
                    pass
                try:
                    await proc.wait()
                except Exception:
                    pass
                return {"status": "error", "error": f"Timeout ({API_TIMEOUT_SCRIPT}s) — скрипт выполнялся слишком долго"}
            # Prepend fix note if query was replaced
            if _fix_note and out:
                out = _fix_note + out
            elif _fix_note:
                out = _fix_note

            # Self-heal: если скрипт вернул "не поддерживает действие" и подсказал
            # список поддерживаемых, делаем 1 ретрай с первым поддерживаемым action.
            _unsupported_text = (out or err or '').lower()
            if 'не поддерживает действие' in _unsupported_text and 'поддерживаемые действия' in _unsupported_text:
                try:
                    _m_sup = _re_ea.search(r'поддерживаемые\s+действия\s*:\s*([^\n\r]+)', (out or err), _re_ea.IGNORECASE)
                    _cand_raw = (_m_sup.group(1).strip() if _m_sup else '')
                    _cand_parts = [p.strip().strip('"\' ').strip() for p in _re_ea.split(r'[,;/|]+', _cand_raw) if p.strip()]
                    _retry_action = _cand_parts[0] if _cand_parts else ''
                    if _retry_action and _retry_action.lower() != action.lower():
                        _retry_env = dict(env)
                        _retry_env['AGENT_ACTION'] = _retry_action
                        _proc2 = await _aio_ea.create_subprocess_exec(
                            _sys_ea.executable, '-c', py_code,
                            stdout=_aio_ea.subprocess.PIPE,
                            stderr=_aio_ea.subprocess.PIPE,
                            env=_retry_env,
                            **({'preexec_fn': _resource_limits} if _is_linux else {}),
                        )
                        try:
                            _so2, _se2 = await _aio_ea.wait_for(_proc2.communicate(), timeout=float(API_TIMEOUT_SCRIPT))
                            _out2 = _so2.decode('utf-8', errors='replace').strip()[:2000]
                            _err2 = _se2.decode('utf-8', errors='replace').strip()[:500]
                            if _out2 and 'не поддерживает действие' not in _out2.lower():
                                logger.info("[ACTION] self-heal retry: %s -> %s", action, _retry_action)
                                action = _retry_action
                                out = _out2
                                err = _err2
                        except _aio_ea.TimeoutError:
                            _proc2.kill()
                            try:
                                await _proc2.communicate()
                            except Exception:
                                pass
                except Exception as _heal_err:
                    logger.debug("[ACTION] self-heal retry skipped: %s", _heal_err)

            logger.info(f"[ACTION] {action} output={out[:100]} err={err[:100]}")
            # Лог в хронологию агента
            try:
                from models import AgentActivityLog as _AALA, Session as _SessA, User as _UserA
                _al_sa = _SessA()
                try:
                    _al_ua = _al_sa.query(_UserA).filter_by(telegram_id=user_id).first()
                    if _al_ua:
                        _svc_a = agent_data.get('service_label') or agent_data.get('name', 'Агент')
                        _aname_a = agent_data.get('name', 'Агент')
                        # Для search_users: сохраняем query+page в title для истории запросов
                        _action_log_suffix = ''
                        if action == 'search_users' and action_params:
                            _q_l = str(action_params.get('query', ''))[:60]
                            _p_l = str(action_params.get('page', 1))
                            _action_log_suffix = f' [q={_q_l} p={_p_l}]'
                        _al_sa.add(_AALA(
                            user_id=_al_ua.id,
                            activity_type='run_agent_action',
                            title=f'{_aname_a} · {action}{_action_log_suffix}',
                            content=(out[:600] if out else (err or 'нет вывода')),
                            target=_svc_a,
                            status='completed' if out else 'failed',
                            result=(out[:800] if out else (err or '')),
                        ))
                        _al_sa.commit()
                        # Создаём integration_alert якоря для autopilot-контекста
                        # (python_code не запускается в автопилоте, поэтому якоря здесь)
                        if out and len(out) > 20:
                            try:
                                import asyncio as _asyncio_ia
                                _uid_ia, _an_ia, _sv_ia, _o_ia = _al_ua.id, _aname_a, _svc_a, out
                                _asyncio_ia.get_running_loop().run_in_executor(
                                    None,
                                    lambda: spawn_integration_anchors(_uid_ia, _an_ia, _sv_ia, _o_ia)
                                )
                            except Exception as _sia_e:
                                logger.debug('[ACTION] spawn anchor: %s', _sia_e)
                finally:
                    _al_sa.close()
            except Exception as _al_ae:
                logger.warning(f"[ACTION] activity log error: {_al_ae}")
            if out:
                return {"status": "success", "output": out}
            else:
                return {"status": "error", "error": err or "Скрипт не вернул вывода"}
        except Exception as _e:
            logger.error(f"[ACTION] _run_external_action error: {_e}")
            return {"error": str(_e)}

    async def execute_actions(self, actions, user_id, session=None,
                              user_message=None, progress_callback=None,
                              web_context: bool = False):
        """Выполняет tool calls через handlers.
        
        Включает:
        - parameter auto-fix для известных tool quirks
        - session management с лимитами
        - tool discovery learning
        """
        from . import handlers

        close_session = False
        if session is None:
            if self.active_sessions >= 50:
                return [{"tool": "limit", "success": False,
                         "error": "Слишком много запросов. Попробуй через минуту."}]
            session = Session()
            close_session = True
            self.active_sessions += 1

        results = []
        try:
            for action in actions:
                tool_name = action.get('tool')
                raw_params = action.get('params', {})
                # Defensive: если AI прислал не dict (строку, список и т.д.) — заменяем на пустой dict
                if not isinstance(raw_params, dict):
                    logger.warning(f"[EXEC] {tool_name}: params is {type(raw_params).__name__}, not dict — reset to {{}}")
                    raw_params = {}
                params = dict(raw_params)
                reason = action.get('reason', '')

                # Специальный обработчик: запуск скрипта агента с параметрами действия
                if tool_name == 'run_agent_action':
                    result = await self._run_external_action(raw_params, user_id)
                    results.append({"tool": tool_name, "success": True, "result": result, "reason": reason})
                    continue

                handler_func = getattr(handlers, tool_name, None)
                if not handler_func:
                    results.append({"tool": tool_name, "success": False,
                                    "error": f"Handler {tool_name} not found"})
                    continue

                try:
                    params['user_id'] = user_id
                    sig = inspect.signature(handler_func)
                    if 'session' in sig.parameters:
                        params['session'] = session
                        # Не закрываем переданную извне сессию — это ответственность вызывающего
                        if 'close_session' in sig.parameters:
                            params['close_session'] = False
                        elif 'close_session' in params:
                            del params['close_session']  # ИИ передал, но функция не принимает
                    # Web-контекст: не отправляем изображения в Telegram при запросе с дашборда
                    if web_context and tool_name == 'generate_image' and 'send_to_telegram' in sig.parameters:
                        params['send_to_telegram'] = False

                    # === Parameter auto-fix для известных quirks ===
                    params = self._fix_tool_params(tool_name, params, user_message)

                    # Если _fix_tool_params заблокировал вызов (нет обязательного контента / фейковый email) — пропускаем
                    if params.pop('__skip__', False):
                        _block_err = params.pop('__block_error__', None)
                        results.append({
                            "tool": tool_name, "success": False,
                            "error": _block_err or f"{tool_name}: нет контента для публикации — сначала сгенерируй текст поста",
                            "reason": reason
                        })
                        logger.warning("[EXEC] %s SKIPPED: %s", tool_name, (_block_err or 'no content')[:100])
                        continue

                    # === БЛОК add_task в автопилоте: агент должен ВЫПОЛНЯТЬ работу сам ===
                    if tool_name == 'add_task' and '[АВТОПИЛОТ]' in (user_message or ''):
                        _new_title_ap = (params.get('title') or '')[:80]
                        logger.warning("[EXEC] add_task BLOCKED in autopilot: '%s' — agent must execute, not delegate to user", _new_title_ap)
                        results.append({
                            "tool": tool_name, "success": False,
                            "result": (
                                "В автопилоте ты работаешь самостоятельно — пользователь сейчас не в чате. "
                                "Выполни эту работу сам прямо сейчас: "
                                "вызови нужный инструмент (web_search, send_outreach_email, create_post и т.д.) "
                                "и покажи РЕЗУЛЬТАТ."
                            ),
                            "reason": reason
                        })
                        continue

                    # === Дедупликация add_task: не создаём задачи с очень похожим названием ===
                    if tool_name == 'add_task':
                        _new_title = (params.get('title') or '').strip().lower()
                        if _new_title and len(_new_title) >= 5:
                            try:
                                from models import Task as _TaskDedup
                                _pending = session.query(_TaskDedup).filter(
                                    _TaskDedup.user_id == user_id,
                                    _TaskDedup.status.in_(['pending', 'active', 'in_progress']),
                                ).all()
                                for _pt in _pending:
                                    _pt_title = (_pt.title or '').strip().lower()
                                    # Проверяем: один является подстрокой другого или Жаккар-сходство
                                    _is_dup = (
                                        _new_title == _pt_title or
                                        (_new_title in _pt_title and len(_new_title) > 10) or
                                        (_pt_title in _new_title and len(_pt_title) > 10)
                                    )
                                    if _is_dup:
                                        logger.warning(
                                            "[EXEC] add_task DEDUP: '%s' similar to existing pending [%d] '%s' — skipping",
                                            _new_title[:50], _pt.id, _pt_title[:50]
                                        )
                                        results.append({
                                            "tool": tool_name, "success": True,
                                            "result": {"task_id": _pt.id, "title": _pt.title,
                                                       "note": f"Задача уже существует (id={_pt.id}): «{_pt.title}» — дубликат не создан"},
                                            "reason": reason
                                        })
                                        # Пропускаем создание
                                        raise StopIteration(f"dup:{_pt.id}")
                            except StopIteration as _si:
                                continue
                            except Exception as _dd_err:
                                logger.debug("[EXEC] add_task dedup check error: %s", _dd_err)

                    # === Универсальная фильтрация неизвестных параметров ===
                    # AI иногда передаёт параметры которых нет в сигнатуре функции
                    # (например sender_name в send_outreach_email). Фильтруем чтобы не было TypeError.
                    _known = set(sig.parameters.keys())
                    _has_var_keyword = any(
                        p.kind == inspect.Parameter.VAR_KEYWORD
                        for p in sig.parameters.values()
                    )
                    if not _has_var_keyword:
                        _unknown = [k for k in list(params.keys()) if k not in _known]
                        if _unknown:
                            logger.warning(f"[EXEC] {tool_name}: stripping unknown params {_unknown}")
                            for _uk in _unknown:
                                del params[_uk]

                    # Списываем токены за инструмент (если стоимость > 0)
                    from token_service import spend_tokens, ACTION_COSTS, DEFAULT_TOOL_COST
                    from config import FREE_ACCESS_MODE
                    tool_cost = ACTION_COSTS.get(tool_name, DEFAULT_TOOL_COST)
                    if not FREE_ACCESS_MODE and tool_cost > 0:
                        token_result = spend_tokens(user_id, tool_name, description=reason)
                        if not token_result['success']:
                            results.append({"tool": tool_name, "success": False,
                                            "error": token_result['error'], "reason": reason})
                            logger.info(f"[EXEC] {tool_name} — недостаточно токенов")
                            continue

                    # Логируем параметры ДО вызова
                    safe_params = {k: v for k, v in params.items() if k != 'session'}
                    logger.info(f"[EXEC] {tool_name} CALL params={safe_params}")

                    if asyncio.iscoroutinefunction(handler_func):
                        result = await handler_func(**params)
                    else:
                        result = handler_func(**params)

                    self.tool_discovery.learn_from_success(
                        func_name=tool_name, user_id=user_id,
                        context=reason, result=result)
                    try:
                        get_learner().record_tool_result(user_id, tool_name, True, str(result)[:300])
                    except Exception as _le:
                        logger.debug("suppressed learner: %s", _le)

                    results.append({"tool": tool_name, "success": True,
                                    "result": result, "reason": reason})

                    # ── Decision Log (#6): записываем стратегические решения ──
                    _STRATEGIC_TOOLS = {
                        'send_outreach_email', 'run_agent_action',
                        'save_email_contact', 'check_emails', 'update_goal_progress',
                        'negotiate_by_email', 'reply_to_outreach_email', 'web_search',
                        'research_topic', 'delegate_task',
                    }
                    if tool_name in _STRATEGIC_TOOLS:
                        try:
                            from models import DecisionLog as _DL, Session as _DLSess, User as _DLUser
                            _dl_sess = _DLSess()
                            try:
                                _dl_user = _dl_sess.query(_DLUser).filter_by(telegram_id=user_id).first()
                                if _dl_user:
                                    _dl_result_str = str(result)[:500] if result else ''
                                    _dl = _DL(
                                        user_id=_dl_user.id,
                                        decision_type='tool_selection',
                                        context_summary=(str(reason) or '')[:400],
                                        chosen_action=tool_name,
                                        rationale=(str(reason) or '')[:400],
                                        actual_outcome=_dl_result_str,
                                        outcome_score=0.8 if (result and 'ошибка' not in _dl_result_str.lower() and 'error' not in _dl_result_str.lower()) else 0.2,
                                    )
                                    _dl_sess.add(_dl)
                                    _dl_sess.commit()
                            except Exception as _e_dl_inner:
                                logger.debug('[DECISION LOG] inner: %s', _e_dl_inner)
                                try:
                                    _dl_sess.rollback()
                                except Exception:
                                    pass
                            finally:
                                _dl_sess.close()
                        except Exception as _e_dl:
                            logger.debug('[DECISION LOG] outer: %s', _e_dl)

                    logger.info(f"[EXEC] {tool_name} ✓ result={str(result)[:200]} — {reason}")

                except Exception as e:
                    logger.error(f"[EXEC] {tool_name} ✗ — {e}\n{traceback.format_exc()}")
                    try:
                        self.tool_discovery.learn_from_failure(
                            func_name=tool_name, error=str(e))
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)
                    try:
                        get_learner().record_tool_result(user_id, tool_name, False)
                    except Exception as _le:
                        logger.debug("suppressed learner: %s", _le)
                    results.append({"tool": tool_name, "success": False,
                                    "error": str(e), "reason": reason})
        finally:
            if close_session:
                try:
                    session.close()
                except Exception as e:
                    logger.debug(f"Session close error: {e}")
                self.active_sessions = max(0, self.active_sessions - 1)

        return results

    def _fix_tool_params(self, tool_name, params, user_message=None):
        """Фиксит известные проблемы с параметрами tools.
        
        AI иногда передаёт неправильные имена параметров —
        эта функция исправляет самые частые ошибки.
        """
        # === Универсально: убираем кавычки из имён параметров ===
        # DeepSeek иногда присылает ключи вида '"email"' вместо 'email'
        reserved = {'user_id', 'session', 'close_session'}
        needs_fix = [k for k in params if k not in reserved and (k.startswith('"') or k.startswith("'"))]
        for bad_key in needs_fix:
            clean_key = bad_key.strip('"\' ')
            if clean_key and clean_key not in params:
                params[clean_key] = params.pop(bad_key)
            elif clean_key:
                params.pop(bad_key)  # дубль — удаляем

        # === save_email_contact: email может прийти с кавычками внутри значения ===
        # Универсально: чистим кавычки из значений email-полей во всех инструментах
        _email_fields = ['email', 'recipient_email', 'name', 'recipient_name',
                         'company', 'recipient_company', 'subject', 'position', 'notes']
        for _fld in _email_fields:
            if _fld in params and isinstance(params[_fld], str):
                _stripped = params[_fld].strip('"\' ')
                if _stripped != params[_fld]:
                    logger.info(f"[FIX_PARAMS] stripped quoted value for {_fld}: {params[_fld]!r} -> {_stripped!r}")
                    params[_fld] = _stripped

        # === add_email_leads: leads может прийти как list/dict вместо строки ===
        if tool_name == 'add_email_leads' and 'leads' in params:
            v = params['leads']
            if isinstance(v, (list, dict)):
                import json as _json
                params['leads'] = _json.dumps(v, ensure_ascii=False)

        # === delegate_task: AI иногда передаёт task_title вместо title ===
        elif tool_name == 'delegate_task':
            if 'task_title' in params and 'title' not in params:
                params['title'] = params.pop('task_title')
                logger.info(f"[FIX_PARAMS] delegate_task: renamed task_title → title")
            elif 'task_name' in params and 'title' not in params:
                params['title'] = params.pop('task_name')
            if not params.get('title'):
                params['title'] = (user_message or 'задача')[:80]
                logger.info(f"[FIX_PARAMS] delegate_task: generated title from message")

        if tool_name == 'find_relevant_contacts_for_task':
            if 'description' in params and 'task_description' not in params:
                params['task_description'] = params.pop('description')
            elif 'task_description' not in params:
                params['task_description'] = 'помощь с задачей'

        elif tool_name == 'send_email':
            # Автоподстановка sender_name — используем имя ПОЛЬЗОВАТЕЛЯ (владельца),
            # а не имя AI-агента. Агент действует от лица пользователя.
            if not params.get('sender_name'):
                try:
                    _uid_sn = params.get('user_id')
                    if _uid_sn:
                        _s_sn = Session()
                        try:
                            _u_sn = _s_sn.query(User).filter_by(telegram_id=_uid_sn).first()
                            if _u_sn:
                                params['sender_name'] = _u_sn.first_name or _u_sn.username or 'Team'
                                logger.info(f"[FIX_PARAMS] send_email: set sender_name='{params['sender_name']}' from user profile")
                        finally:
                            _s_sn.close()
                except Exception:
                    params['sender_name'] = 'Team'

        elif tool_name in ('send_outreach_email', 'negotiate_by_email', 'send_follow_up_email', 'reply_to_outreach_email'):
            # AI путает send_email (с sender_name) с send_outreach_email (без него)
            # Просто убираем неправильный параметр; у universal stripping нет этого в белом списке
            params.pop('sender_name', None)
            params.pop('from_name', None)
            params.pop('from_email', None)   # не часть send_outreach_email
            # GUARD: блокируем отправку письма без темы или тела
            _missing_fields = []
            if not params.get('subject'):
                _missing_fields.append('subject (тема письма)')
            if not params.get('body'):
                _missing_fields.append('body (текст письма)')
            if _missing_fields:
                params['__skip__'] = True
                params['__block_error__'] = (
                    f"⛔ Нельзя отправить письмо без: {', '.join(_missing_fields)}. "
                    "Сначала составь тему и текст письма, затем вызови инструмент снова с subject= и body=."
                )
                return params
            # Автозапись кто отправил: берём имя активного агента
            if not params.get('sent_by_agent'):
                _ag_sba = self._active_agent_data.get(params.get('user_id'))
                if _ag_sba and _ag_sba.get('name'):
                    params['sent_by_agent'] = _ag_sba['name']
            # Приводим email к нижнему регистру и убираем лишние слэши
            if 'recipient_email' in params and isinstance(params['recipient_email'], str):
                params['recipient_email'] = params['recipient_email'].strip().lower().lstrip('/')
            # GUARD: блокируем фейковые/placeholder email-адреса
            _rcpt = params.get('recipient_email', '')
            _FAKE_DOMAINS = (
                '@example.com', '@example.org', '@example.net',
                '@test.com', '@test.org', '@placeholder.',
                '@email.com', '@mail.test', '@fake.',
                '@domain.com', '@company.com', '@org.com',
                '@sample.com', '@demo.com',
            )
            if _rcpt and any(_rcpt.endswith(d) or d in _rcpt for d in _FAKE_DOMAINS):
                params['__skip__'] = True
                params['__block_error__'] = (f"⛔ Email {_rcpt} — placeholder/фейковый адрес. "
                                             "Найди реальный email через web_search или используй другой метод контакта.")
                return params
            # GUARD: блокируем email вида "domain.tld@provider" (local-part является доменным именем)
            # Пример: gmail.com@ymail.com, company.ru@hotmail.com — это артефакты CRM, не реальные адреса
            _COMMON_TLDS = ('.com', '.ru', '.org', '.net', '.io', '.co', '.ai', '.de', '.uk', '.fr', '.me')
            if _rcpt and '@' in _rcpt:
                _local = _rcpt.split('@')[0]
                if any(_local.endswith(_tld) for _tld in _COMMON_TLDS):
                    params['__skip__'] = True
                    params['__block_error__'] = (f"⛔ {_rcpt} — local-part выглядит как домен ('{_local}'). "
                                                 "Это не персональный адрес. Найди реальный контакт.")
                    return params
            # GUARD: блокируем role-based / generic email (не персональные)
            # Оставляем только явно нечитаемые: автоответы, техслужбы, spam-ловушки
            # press@/media@/partners@/ceo@/director@ — допустимы в нужном контексте, AI оценивает сам
            _ROLE_PREFIXES = (
                'info@', 'support@', 'marketing@', 'sales@', 'sale@', 'admin@', 'noreply@',
                'no-reply@', 'contact@', 'hello@', 'help@', 'office@', 'hr@',
                'buhgalter@', 'bukhgalter@', 'accounting@', 'team@', 'general@',
                'mail@', 'webmaster@', 'postmaster@', 'abuse@',
                'feedback@', 'service@', 'billing@', 'jobs@', 'career@', 'careers@',
                'opensource@', 'security@', 'privacy@', 'legal@', 'compliance@',
                'komm@', 'commercial@',
                'ai@', 'ml@', 'data@', 'research@', 'dev@', 'engineering@',
                'decision-makers', 'enquiries@', 'invest@',
            )
            if _rcpt and any(_rcpt.startswith(p) for p in _ROLE_PREFIXES):
                params['__skip__'] = True
                params['__block_error__'] = (f"⛔ {_rcpt} — фейковый или generic email. "
                                             "Найди реальный email получателя через поиск или контакты.")
                return params
            # GUARD: блокируем email с невалидными символами в local-part (/, пробелы, +github)
            _rcpt_local = _rcpt.split('@')[0] if '@' in _rcpt else ''
            if '/' in _rcpt_local or ' ' in _rcpt or '+github' in _rcpt_local:
                params['__skip__'] = True
                params['__block_error__'] = (f"⛔ {_rcpt} — некорректный email (содержит спецсимволы). "
                                             "Это не персональный адрес. Найди реальный email.")
                return params
            # GUARD: финальная проверка формата email — блокируем всё что не пройдёт Resend API
            import re as _re_email
            _email_fmt = _re_email.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')
            if _rcpt and not _email_fmt.match(_rcpt):
                params['__skip__'] = True
                params['__block_error__'] = (f"⛔ {_rcpt} — некорректный формат email. "
                                             "Используй только формат user@domain.tld. Найди правильный адрес через web_search.")
                return params

        elif tool_name == 'quick_topic_search' and not params.get('topic'):
            if user_message:
                stop = {'что', 'как', 'где', 'когда', 'почему', 'а', 'и', 'но'}
                words = [w for w in re.findall(r'\b\w+\b', user_message.lower())
                         if w not in stop and len(w) > 2][:3]
                params['topic'] = ' '.join(words) if words else user_message[:50]
            else:
                params['topic'] = 'общая информация'

        elif tool_name in ('publish_to_telegram', 'publish_to_discord', 'create_post'):
            if 'content' not in params or not params.get('content'):
                # DeepSeek вызвал без content — извлекаем только из явных полей ответа AI
                # ВАЖНО: не использовать user_message как fallback — это текст задачи автопилота,
                # а не контент для публикации. Если контента нет — блокируем вызов.
                fallback_content = params.pop('text', None) or params.pop('message', None) or params.pop('post_text', None) or params.pop('body', None)
                if fallback_content:
                    params['content'] = fallback_content
                    logger.info(f"[FIX_PARAMS] {tool_name}: extracted content from fallback field")
                else:
                    # Нет контента — возвращаем ошибку, чтобы AI сгенерировал контент сначала
                    params['__skip__'] = True
                    logger.warning(f"[FIX_PARAMS] {tool_name}: no content provided, blocking publish call")

        elif tool_name == 'generate_image':
            if 'prompt' not in params or not params.get('prompt'):
                # AI иногда передаёт description/text/image_prompt вместо prompt
                fallback = (params.pop('description', None) or params.pop('text', None)
                            or params.pop('image_prompt', None) or params.pop('image_description', None))
                if fallback:
                    params['prompt'] = fallback
                    logger.info(f"[FIX_PARAMS] generate_image: extracted prompt from fallback")
                elif user_message:
                    params['prompt'] = user_message[:500]
                    logger.info(f"[FIX_PARAMS] generate_image: used user_message as prompt")
                else:
                    params['prompt'] = 'abstract digital art illustration'

        elif tool_name == 'research_topic':
            if 'topic' in params and 'query' not in params:
                params['query'] = params.pop('topic')
            elif 'query' not in params:
                params['query'] = user_message[:200] if user_message else 'исследование'

        elif tool_name == 'web_search':
            if not params.get('query'):
                # AI вызвал web_search без query — извлекаем из альтернативных полей или сообщения
                fallback_q = (params.pop('search_query', None) or params.pop('topic', None)
                              or params.pop('text', None) or params.pop('q', None))
                if fallback_q:
                    params['query'] = fallback_q
                elif user_message:
                    params['query'] = user_message[:200]
                else:
                    params['query'] = 'поиск информации'
                logger.info(f"[FIX_PARAMS] web_search: set query='{params['query'][:60]}'")

        elif tool_name == 'add_task' and user_message:
            if 'title' not in params or not params.get('title'):
                # DeepSeek вызвал add_task без title — извлекаем из сообщения
                import re as _re
                # Пробуем найти суть задачи в сообщении
                task_patterns = [
                    r'(?:задачу|задание|таск)\s+(?:на\s+)?["«]?([^"»,.!?]{5,80})',
                    r'(?:поставь|создай|добавь|запиши)\s+(?:задачу\s+)?(?:на\s+)?["«]?([^"»,.!?]{5,80})',
                ]
                for pat in task_patterns:
                    m = _re.search(pat, user_message, _re.IGNORECASE)
                    if m:
                        params['title'] = m.group(1).strip()
                        break
                if 'title' not in params or not params.get('title'):
                    # Fallback — берём сообщение как title
                    clean = _re.sub(r'^(да|ок|хорошо|давай|го|ставь|поставь|создай)[,!.\s]*', '', user_message, flags=_re.IGNORECASE).strip()
                    if len(clean) > 3:
                        params['title'] = clean[:80]
                    else:
                        params['title'] = user_message[:80]
                logger.info(f"[FIX_PARAMS] add_task title extracted: {params['title']}")

        elif tool_name == 'update_profile' and user_message:
            # Универсальный fallback: если DeepSeek вызвал update_profile без данных,
            # извлекаем факты из сообщения пользователя по разным формулировкам.
            profile_fields = ['city', 'skills', 'interests', 'goals', 'company', 'position', 'birth_date']
            has_any = any(params.get(f) for f in profile_fields)
            if not has_any:
                msg = user_message
                logger.info(f"[FIX_PARAMS] update_profile empty params — extracting from message")
                import re as _re
                
                # === ГОРОД ===
                # «живу в Москве», «я из Питера», «город Казань», «переехал в Казань»,
                # «нахожусь в Перми», «в городе Тула», «город: Казань»
                city_patterns = [
                    r'(?:живу|нахожусь|обитаю|базируюсь|переехал[а]?)\s+в\s+([А-ЯЁ][а-яё\-]+(?:[\-\s][А-ЯЁ][а-яё]+)?)',
                    r'(?:я\s+из|приехал[а]?\s+из|родом\s+из)\s+([А-ЯЁ][а-яё\-]+)',
                    r'город[уе]?[:\s]+([А-ЯЁ][а-яё\-]+)',
                    r'в\s+городе\s+([А-ЯЁ][а-яё\-]+)',
                ]
                for pat in city_patterns:
                    m = _re.search(pat, msg, _re.IGNORECASE)
                    if m:
                        city_raw = m.group(1).strip()
                        # Нормализация: «Питере» → «Санкт-Петербург», «Питера» → «Санкт-Петербург» 
                        if _re.match(r'питер', city_raw, _re.IGNORECASE):
                            city_raw = 'Санкт-Петербург'
                        elif _re.match(r'мск|москв', city_raw, _re.IGNORECASE):
                            city_raw = 'Москва'
                        elif _re.match(r'спб|петербург', city_raw, _re.IGNORECASE):
                            city_raw = 'Санкт-Петербург'
                        elif _re.match(r'нск|новосиб', city_raw, _re.IGNORECASE):
                            city_raw = 'Новосибирск'
                        elif _re.match(r'екб|екат', city_raw, _re.IGNORECASE):
                            city_raw = 'Екатеринбург'
                        # Словарь косвенных падежей → именительный
                        _city_cases = {
                            'казани': 'Казань', 'перми': 'Пермь', 'твери': 'Тверь',
                            'тюмени': 'Тюмень', 'рязани': 'Рязань', 'астрахани': 'Астрахань',
                            'тобольске': 'Тобольск', 'томске': 'Томск', 'омске': 'Омск',
                            'курске': 'Курск', 'минске': 'Минск', 'пензе': 'Пенза',
                            'самаре': 'Самара', 'уфе': 'Уфа', 'туле': 'Тула',
                            'сочи': 'Сочи', 'тбилиси': 'Тбилиси',
                            'краснодаре': 'Краснодар', 'волгограде': 'Волгоград',
                            'воронеже': 'Воронеж', 'ростове': 'Ростов-на-Дону',
                            'нижнем': 'Нижний Новгород', 'красноярске': 'Красноярск',
                            'челябинске': 'Челябинск', 'саратове': 'Саратов',
                            'иркутске': 'Иркутск', 'владивостоке': 'Владивосток',
                            'хабаровске': 'Хабаровск', 'барнауле': 'Барнаул',
                            'ульяновске': 'Ульяновск', 'ярославле': 'Ярославль',
                            'калининграде': 'Калининград', 'оренбурге': 'Оренбург',
                        }
                        city_norm = _city_cases.get(city_raw.lower())
                        if city_norm:
                            city_raw = city_norm
                        else:
                            # Общие правила только для окончаний -е (предложный)
                            # НЕ трогаем -и (Казани, Перми) — они в словаре выше
                            city_raw = _re.sub(r'е$', '', city_raw)
                        if len(city_raw) >= 2:
                            # Первая буква заглавная
                            city_raw = city_raw[0].upper() + city_raw[1:]
                            params['city'] = city_raw
                        break
                
                # === НАВЫКИ ===
                # «навыки: Python, React», «умею Python и FastAPI», «знаю React»,
                # «владею Python», «разбираюсь в ML», «специализируюсь на backend»,
                # «занимаюсь разработкой», «мои скиллы: Python, Go»
                skills_patterns = [
                    r'(?:мои\s+)?навыки?[:\s]+([^.!?]+)',
                    r'скилл[ыа]?[:\s]+([^.!?]+)',
                    r'(?:умею|знаю|владею|освоил[а]?)\s+([^.!?]+)',
                    r'(?:разбираюсь|специализируюсь)\s+(?:в|на)\s+([^.!?]+)',
                ]
                _skills_garbage = [
                    'и интересы', 'и цели', 'навыки)', 'цели)', 'профиль',
                    'нужно', 'будет', 'можно', 'стоит', 'важно', 'отлично',
                    'знаю что', 'вижу что', 'понимаю', 'считаю',
                ]
                for pat in skills_patterns:
                    m = _re.search(pat, msg, _re.IGNORECASE)
                    if m:
                        val = m.group(1).strip().rstrip(',')
                        val_lower = val.lower()
                        if len(val) > 1 and not any(val_lower.startswith(g) for g in _skills_garbage):
                            from .utils import _normalize_skills_text
                            params['skills'] = _normalize_skills_text(val)
                        break
                
                # === ИНТЕРЕСЫ ===
                # «интересуюсь ML», «увлекаюсь спортом», «люблю музыку»,
                # «интересы: ML, робототехника», «хобби: шахматы»,
                # «мне интересно AI», «нравится программирование»
                interests_patterns = [
                    r'(?:мои\s+)?интересы?[:\s]+([^.!?]+)',
                    r'хобби[:\s]+([^.!?]+)',
                    r'увлечени[яе][:\s]+([^.!?]+)',
                    r'(?:интересуюсь|увлекаюсь|люблю|нравится|обожаю)\s+([^.!?]+)',
                    r'мне\s+интересн[оа]\s+([^.!?]+)',
                ]
                # Мусорные слова — если интерес начинается с них, это не интерес
                _interest_garbage = [
                    'и настрой', 'настрой алерт', 'навыки', 'цели', 'профиль',
                    'добавь', 'помоги', 'подскажи', 'сделай', 'поставь', 'напомни',
                    'создай', 'проверь', 'покажи', 'расскажи',
                ]
                for pat in interests_patterns:
                    m = _re.search(pat, msg, _re.IGNORECASE)
                    if m:
                        val = m.group(1).strip().rstrip(',')
                        val_lower = val.lower()
                        if len(val) > 1 and not any(val_lower.startswith(g) for g in _interest_garbage):
                            params['interests'] = val
                        break
                
                # === ЦЕЛИ ===
                # «моя цель — запустить MVP», «хочу выйти на 100 клиентов»,
                # «планирую переехать», «стремлюсь к 1 млн выручки»,
                # «цели: запустить MVP, найти инвестора»
                goals_patterns = [
                    r'(?:моя\s+)?цел[иья][:\s—–-]+([^.!?]+)',
                    r'(?:хочу|планирую|стремлюсь|мечтаю|собираюсь|намерен[а]?)\s+([^.!?]+)',
                ]
                _goals_garbage = [
                    'обсудить', 'поговорить', 'узнать', 'спросить', 'понять',
                    'посмотреть', 'попробовать', 'подумать', 'разобраться',
                    'чтобы ты', 'чтоб ты', 'тебя попросить',
                ]
                for pat in goals_patterns:
                    m = _re.search(pat, msg, _re.IGNORECASE)
                    if m:
                        val = m.group(1).strip().rstrip(',')
                        val_lower = val.lower()
                        if len(val) > 2 and not any(val_lower.startswith(g) for g in _goals_garbage):
                            params['goals'] = val
                        break
                
                # === ДОЛЖНОСТЬ ===
                # «я разработчик», «работаю программистом», «должность: CTO»,
                # «я тимлид», «по профессии дизайнер»
                position_patterns = [
                    r'(?:должность|позиция|роль)[:\s]+([^,.!?]+)',
                    r'(?:работаю|тружусь)\s+([а-яёА-ЯЁa-zA-Z\-]+(?:ом|ем|ёром|ером|стом|ком|чиком))',
                    r'по\s+професси[ию]\s+([^,.!?]+)',
                    r'я\s+((?:разработчик|программист|дизайнер|менеджер|директор|инженер|аналитик|тимлид|CTO|CEO|COO|CFO|фрилансер|предприниматель|маркетолог|продюсер|консультант)[а-яё]*)',
                ]
                for pat in position_patterns:
                    m = _re.search(pat, msg, _re.IGNORECASE)
                    if m:
                        val = m.group(1).strip()
                        # Нормализуем творительный → именительный падеж
                        from .utils import _normalize_position_case
                        val = _normalize_position_case(val)
                        if len(val) > 1:
                            params['position'] = val
                        break
                
                # === КОМПАНИЯ ===
                # «работаю в Яндексе», «компания: Google», «я из ASI Biont»,
                # «сотрудник Сбера», «основатель AI Startup»
                company_patterns = [
                    r'(?:компани[яию]|фирм[ауе]|организаци[яию])[:\s]+([^,.!?]+)',
                    r'работаю\s+в\s+(?:компании\s+)?([A-ZА-ЯЁ][^,.!?]{1,30})',
                    r'(?:сотрудник|основатель|со-?основатель|партнёр)\s+(?:компании\s+)?([A-ZА-ЯЁ][^,.!?]{1,30})',
                ]
                for pat in company_patterns:
                    m = _re.search(pat, msg, _re.IGNORECASE)
                    if m:
                        val = m.group(1).strip()
                        if len(val) > 1:
                            from .utils import _normalize_company_name
                            params['company'] = _normalize_company_name(val)
                        break
                
                extracted = {k: v for k, v in params.items() if k not in ('user_id', 'session')}
                logger.info(f"[FIX_PARAMS] Extracted: {extracted}")

        return params

    # ===== BILINGUAL TOOL INSTRUCTIONS =====

    @staticmethod
    def _tool_instructions(lang='ru'):
        """Return tool usage instructions in the user's language."""
        if lang == 'en':
            return (
                "\n\n[TOOL USAGE INSTRUCTIONS]"
                "\nShort user replies (yes, sure, create, set, ok, go, do it) = CONFIRMATION of your last suggestion. Look at your previous answer and EXECUTE what you proposed. BUT if you suggested creating a task and NO TIME was specified — FIRST ask: 'What time should I set it for?'. A task WITHOUT reminder time = USELESS task. NEVER create a task without reminder_time."
                "\nCONTEXTUAL REFERENCES: 'this task', 'it', 'that', 'set it for 2pm' — ALWAYS refers to your LAST suggestion. Re-read your previous answer and execute. Asking 'which task?' when you just suggested it = CRITICAL ERROR."
                "\n🔗 DIALOG CONTINUITY: BEFORE responding, re-read your 2-3 LAST messages. If you asked a question — the user is ANSWERING it, react to their answer, don't start over. 'I don't know, any suggestions?' to your question = give SPECIFIC NEW ideas not mentioned before. NEVER repeat advice, ideas or facts you ALREADY said in this dialog. Scan history before answering — if you already mentioned something — give a DIFFERENT idea. Repetition = bot amnesia."
                "\nBE PROACTIVE — call 1-3 tools on EVERY dialog turn. Don't wait for direct commands."
                "\n📢 ACTION REPORT — MANDATORY: after EVERY tool call TELL the user what you did. They can NOT see your tool calls! Created task → 'Added task X for HH:MM'. Completed → 'Closed task X'. Rescheduled → 'Moved X to HH:MM'. Updated profile → 'Saved your city/skill'. Created goal → 'Created goal X'. Researched → give summary. Silent action = action that never happened for the user."
                "\n🧠 HELP SUBSTANTIVELY: when user discusses a problem or asks for advice — give specific ideas and steps. BUT if user expresses a WISH/GOAL ('I want X', 'I plan to Y') — FIRST create_goal, then a brief comment. Don't turn goal creation into a consultation."
                "\nUser talks about themselves/project → GIVE EXPERT ADVICE for their niche + update_profile + research_topic(niche trends)."
                "\nUser mentions skills/technologies → update_profile + research_topic(trends)."
                "\nUser mentions achievement → complete_task + update_goal_progress + suggest create_post."
                "\n🔑 IMPLICIT TASK COMPLETION (PRIORITY #1): when user reports they DID something (ordered, bought, paid, set up, wrote, sent, finished, figured out, arranged, called, completed, launched, picked up, received, fixed, installed, assembled, cooked, cleaned — ANY past tense verb) — IMMEDIATELY COMPARE with EVERY task from OVERDUE/TODAY/TOMORROW sections. If there's a task matching the MEANING of what they described — IMMEDIATELY call complete_task(task_id=ID) WITHOUT questions. Examples: 'I ordered groceries' + task 'Order groceries for breakfast [id=42]' → complete_task(task_id=42). 'Set up the website' + task 'Set up website for AI indexing' → complete_task. 'Called the doctor' + task 'Make doctor appointment' → complete_task. ⚠️ This is the PRIMARY way users close tasks. Missing this signal = CRITICAL ERROR."
                "\nTask involves people → find_relevant_contacts_for_task + set_contact_alert."
                "\n🎯 GOALS (PRIORITY #0 — above all other rules): when user says 'I want X', 'I plan to Y', 'my goal is Z', 'attract investors', 'earn N', 'get X users' — your FIRST ACTION is ALWAYS create_goal(title=...). Do NOT discuss, do NOT ask questions, do NOT write strategy — CREATE THE GOAL FIRST. Only AFTER creating the goal you may briefly comment. If goal is not created — you FAILED."
                "\nREMINDERS: 'remind me in X minutes/hours', 'set a reminder', 'remind me at 3pm' → IMMEDIATELY add_task with reminder_time. DON'T ask for confirmation — user ALREADY asked. Title = essence of reminder from request. reminder_time is REQUIRED: pass EXACTLY as user said, e.g. reminder_time='in 5 minutes' or reminder_time='at 3pm' or reminder_time='tomorrow at 10am'. ⛔ STRICT PROHIBITION: if user says 'in 15 minutes' at night — DO NOT move to morning! They decided. Set it at night. 'in 30 minutes' at 2:40am → reminder_time='in 30 minutes' (will be 3:10am). IF you don't pass reminder_time — task WILL NOT be created."
                "\nIMPLICIT TASKS: User mentions event/task with time ('I have a meeting at 3pm', 'deadline tomorrow', 'doctor at 10', 'presentation on Wednesday') → CHECK task list (get_tasks). If no such task → SUGGEST setting a reminder with specific time (15 min before event). Example: 'I see no task for the meeting. Set a reminder for 2:45pm?'. Create add_task ONLY after user confirmation (yes, sure, ok, set it). If time is vague ('after lunch', 'evening') — first clarify specific time."
                "\nEXPLICIT COMMANDS: 'set a task', 'create a task', 'write down', 'add to list' + time specified → IMMEDIATELY add_task. If no time specified — ask."
                "\nGOAL LINKING: When creating a task (add_task) ALWAYS check — does the user have a goal this task relates to? If yes — pass goal_title. Examples: task 'attract test users' with goal 'Grow AI agent' → add_task(goal_title='Grow AI agent')."
                "\nTIME PRECISION: After edit_task/reschedule_task with time change — ALWAYS use the EXACT time from the tool result. NEVER calculate or round time yourself. Result contains 'New reminder time: DD.MM.YYYY HH:MM' — use EXACTLY this time in your response."
                "\n⛔ NO UNAUTHORIZED CHANGES: NEVER call edit_task, complete_task, delete_task without the user's EXPLICIT request. You have NO RIGHT to change title, description, time or status on your own initiative. You may SUGGEST a change, but EXECUTE only after explicit agreement ('yes, change it', 'yes, rename')."
                "\n⚠️ TASKS WITHOUT TIME: If the user's task list has tasks marked '⚠️ NO TIME' — suggest a time 15-30 minutes from now: 'Task X has no time — set it for HH:MM?'. A task without a reminder = a task that will be forgotten."
                "\n🕐 TIME WHEN SUGGESTING TASKS — TWO CLEAR RULES:\n1) USER SPECIFIED TIME ('in 15 minutes', 'at 3am', 'in an hour', 'at 02:30') → SET EXACTLY AS SAID, even at night. DON'T reschedule! Even 2am — if they say 'in 15 minutes', set 2:15am. It's their choice.\n2) NO TIME SPECIFIED (you suggest) → before 1am: suggest 15-30 minutes from now; after 1am: suggest tomorrow morning.\nIf user says 'now/right now' → reminder_time='now'.\n🚨 CONFLICT CHECK: BEFORE suggesting a time, CHECK the TODAY section in context for existing tasks. If 2pm is taken — DON'T suggest 2pm, suggest 2:30pm or next free slot. Minimum 15 minutes between tasks."
                "\n\n🗣️ LANGUAGE: Write ONLY in English. Even if tool results or context data contain Russian text, you MUST respond in English. Translate any Russian data to English in your response."
            )
        else:
            return (
                "\n\n[ИНСТРУКЦИИ ПО ИНСТРУМЕНТАМ]"
                "\nКороткие ответы пользователя (да, давай, создай, поставь, ок, го, сделай) = ПОДТВЕРЖДЕНИЕ твоего последнего предложения. Посмотри свой предыдущий ответ в истории и ВЫПОЛНИ то, что предложил. НО ЕСЛИ ты предложил создать задачу и время НЕ БЫЛО указано ни тобой ни пользователем — СНАЧАЛА спроси время: «На какое время поставить?». Задача БЕЗ времени напоминания = БЕСПОЛЕЗНАЯ задача. НИКОГДА не создавай задачу без reminder_time."
                "\nКОНТЕКСТНЫЕ ССЫЛКИ: «эту задачу», «это», «её», «давай так», «поставь на 14:00» — ВСЕГДА ссылка на твоё ПОСЛЕДНЕЕ предложение. Перечитай свой предыдущий ответ и выполни. ПЕРЕСПРАШИВАТЬ «какую задачу?» когда ты сам только что предложил = ГРУБЕЙШАЯ ОШИБКА."
                "\n🔗 ПОСЛЕДОВАТЕЛЬНОСТЬ ДИАЛОГА: ПЕРЕД ответом перечитай свои 2-3 ПОСЛЕДНИХ сообщения. Если ты задал вопрос — пользователь ОТВЕЧАЕТ на него, реагируй на его ответ, а не начинай заново. 'Не знаю, есть предложения?' на твой вопрос = дай КОНКРЕТНЫЕ НОВЫЕ идеи, которых ещё не было. НИКОГДА не повторяй совет, идею или факт, который ты УЖЕ говорил в этом диалоге. Просканируй историю перед ответом — если ты уже упоминал что-то (Product Hunt, пилот для 10 пользователей) — дай ДРУГУЮ идею. Повтор = амнезия бота."
                "\nБУДЬ ПРОАКТИВНЫМ — вызывай 1-3 инструмента на КАЖДЫЙ ход диалога. Не жди прямых команд."
                "\n📢 ОТЧЁТ О ДЕЙСТВИЯХ — ОБЯЗАТЕЛЬНО: после КАЖДОГО вызова инструмента СООБЩИ пользователю что ты сделал. Он НЕ видит твои tool calls! Создал задачу → 'Записал задачу X на HH:MM'. Завершил → 'Закрыл задачу X'. Перенёс → 'Перенёс X на HH:MM'. Обновил профиль → 'Записал город/навык'. Создал цель → 'Создал цель X'. Исследовал → дай выжимку. Молчаливое действие = действие которого не было для пользователя."
                "\n🧠 ПОМОГИ ПО СУЩЕСТВУ: когда пользователь обсуждает проблему или просит совет — дай конкретные идеи и шаги. НО если пользователь выражает ЖЕЛАНИЕ/ЦЕЛЬ ('хочу X', 'планирую Y') — сначала create_goal, потом короткий комментарий. Не превращай создание цели в консультацию."
                "\nПользователь рассказывает о себе/проекте → ДАЙ ЭКСПЕРТНЫЕ СОВЕТЫ по его нише + update_profile + research_topic(тренды в нише)."
                "\nПользователь делится стратегическим наблюдением о своей аудитории, рынке или подходе ('можно искать X не только в Y, но и в Z', 'наша аудитория — это...', 'думаю что нужно...') → это запрос на твоё мнение и расширение идеи. СНАЧАЛА ОТВЕТЬ по существу: согласен ли ты, что ещё можно добавить, какие конкретные следующие шаги из этого вытекают. При необходимости сохрани через save_note и ОБЯЗАТЕЛЬНО сообщи что именно сохранил."
                "\nПользователь упоминает навыки/технологии → update_profile + research_topic(тренды)."
                "\nПользователь говорит о достижении → complete_task + update_goal_progress + предложи create_post."
                "\n📊 ПРОГРЕСС ЦЕЛЕЙ — АВТО-ОБНОВЛЕНИЕ: Если пользователь сообщает результат, связанный с активной целью из контекста — СРАЗУ вызови update_goal_progress(goal_title='название из контекста'). НЕ СПРАШИВАЙ 'какую цель обновить?' — сопоставь сам. ПРАВИЛА: (1) обновляй ТОЛЬКО по подтверждённому факту: получен ответ, заключена сделка, достигнут результат. (2) Просто отправка письма/поста/поиск ≠ прогресс — это действие, а не результат. (3) Если у цели есть метрика [X/Y единиц] — передай metric_current с новым значением. (4) Если одна цель — обновляй её без вопросов."
                "\n🔌 ИНТЕГРАЦИИ: в контексте есть [ПОДКЛЮЧЁННЫЕ ИНТЕГРАЦИИ]. Работай ТОЛЬКО через них. "
                "Если инструмент не сработал — попробуй альтернативу (web_search, research_topic). "
                "Если задача БЕЗ интеграции невыполнима — скажи пользователю один раз что подключить (https://asibiont.com/dashboard)."
                "\n🎯 ЦЕЛИ (ПРИОРИТЕТ #0 — выше всех остальных правил): когда пользователь говорит «хочу X», «планирую Y», «мечтаю Z», «моя цель — ...», «привлечь инвесторов», «заработать N», «набрать X пользователей» — ПЕРВЫМ ДЕЙСТВИЕМ ВСЕГДА вызывай create_goal(title=...). НЕ обсуждай, НЕ задавай вопросов, НЕ пиши стратегию — СНАЧАЛА СОЗДАЙ ЦЕЛЬ. Только ПОСЛЕ создания цели можешь коротко прокомментировать. Если цель не создана — ты ПРОВАЛИЛ задачу."
                "\nНАПОМИНАНИЯ: «напомни через X минут/часов», «поставь напоминание», «напомни в 15:00» → СРАЗУ add_task с reminder_time. НЕ спрашивай подтверждение — пользователь УЖЕ попросил. Название = суть напоминания из запроса. reminder_time ОБЯЗАТЕЛЕН: передай ТОЧНО как сказал пользователь, например reminder_time='через 5 минут' или reminder_time='в 15:00' или reminder_time='завтра в 10:00'. ⛔ СТРОЖАЙШИЙ ЗАПРЕТ: если пользователь сказал 'через 15 минут' ночью — НЕ ПЕРЕНОСИ на утро! Он РЕШИЛ сам. Ставь ночью. 'через 30 минут' в 02:40 → reminder_time='через 30 минут' (будет 03:10). ЕСЛИ НЕ ПЕРЕДАШЬ reminder_time — задача НЕ СОЗДАСТСЯ."
                "\nНЕЯВНЫЕ ЗАДАЧИ: Пользователь упоминает событие/дело с временем («у меня встреча в 15:00», «завтра дедлайн», «записан к врачу на 10», «в среду презентация») → ПРОВЕРЬ список задач (get_tasks). Если такой задачи НЕТ → ПРЕДЛОЖИ поставить напоминание с конкретным временем (за 15 мин до события). Пример: «Вижу, задачи про встречу нет. Поставить напоминание на 14:45?». Создавай add_task ТОЛЬКО после подтверждения пользователя (да, давай, ок, поставь). Если время неточное («после обеда», «вечером») — сначала уточни конкретное время, потом предложи."
                "\nЯВНЫЕ КОМАНДЫ: «поставь задачу», «создай задачу», «запиши», «добавь в список» + указано время → СРАЗУ add_task. Если время не указано — спроси."
                "\nПРИВЯЗКА К ЦЕЛЯМ: При создании задачи (add_task) ВСЕГДА проверяй — есть ли у пользователя цель, к которой эта задача относится. Если да — передай goal_title. Примеры: задача 'привлечь тестовых пользователей' при цели 'Раскрутить ИИ агента' → add_task(goal_title='Раскрутить нового ИИ агента')."
                "\nТОЧНОСТЬ ВРЕМЕНИ: После edit_task/reschedule_task с изменением времени — ВСЕГДА бери ТОЧНОЕ время из результата инструмента. НИКОГДА не вычисляй и не округляй время сам. Результат содержит строку 'Новое время напоминания: DD.MM.YYYY HH:MM' — используй ИМЕННО это время в ответе пользователю. Пример: результат='Новое время: 20.02.2026 19:47' → отвечай '19:47', а НЕ '19:45'."
                "\n⛔ ЗАПРЕТ НА САМОВОЛЬНОЕ ПЕРЕИМЕНОВАНИЕ/УДАЛЕНИЕ: НЕ вызывай edit_task (rename), delete_task без явного согласия пользователя. Пример ЗАПРЕЩЁННОГО: пользователь говорит 'пригласил 3 из 5' → ты меняешь задачу 'пригласить 5' на 'собрать фидбек'. Предложи, но жди 'да'. ИСКЛЮЧЕНИЕ — complete_task: если пользователь ЯВНО сообщил о завершении ('сделал', 'готово', 'выполнил', 'закончил') → вызывай complete_task СРАЗУ, не спрашивай."
                "\n⚠️ ЗАДАЧИ БЕЗ ВРЕМЕНИ: Если в списке задач пользователя есть задачи с пометкой '⚠️ БЕЗ ВРЕМЕНИ' — предложи время через 15-30 минут от текущего момента: 'У задачи X нет времени — поставить на HH:MM?'. Задача без напоминания = задача которую забудут."
                "\n🕐 ВРЕМЯ ПРИ ПРЕДЛОЖЕНИИ ЗАДАЧ — ДВА ЧЁТКИХ ПРАВИЛА:\n1) ПОЛЬЗОВАТЕЛЬ САМ УКАЗАЛ ВРЕМЯ ('через 15 минут', 'в 3 ночи', 'через час', 'в 02:30') → СТАВЬ ТОЧНО КАК СКАЗАЛ, даже ночью. НЕ переноси! Хоть 02:00 ночи — если он говорит 'через 15 минут', ставишь на 02:15. Это его выбор, уважай его.\n2) ВРЕМЯ НЕ УКАЗАНО (ты сам предлагаешь) → до 01:00: предлагай через 15-30 минут; после 01:00: предложи завтра утром.\nЕсли пользователь говорит 'сейчас/прямо сейчас' → reminder_time='сейчас'.\n🚨 ПРОВЕРКА КОНФЛИКТОВ: ПЕРЕД предложением времени ПОСМОТРИ секцию СЕГОДНЯ в контексте. Там видны все задачи с временем (например 'Задача1 (14:00), Задача2 (15:00)'). Если в 14:00 уже занято — НЕ предлагай 14:00, предложи 14:30 или следующий свободный слот. Минимум 15 минут между задачами."
            )

    # ===== ОСНОВНОЙ FLOW =====

    async def process_request(self, user_message, user_id, context=None,
                              session=None, subscription_tier=None,
                              progress_callback=None, web_context: bool = False,
                              exclude_tools: set = None):
        """
        Адаптивный tool calling loop:
        1. Собираем контекст (1 запрос к БД)
        2. Определяем tool_choice (auto/required)
        3. Tool calling loop (max 5 итераций)
        4. Обучение + сохранение
        """
        # progress_callback хранится локально (не на self) для thread-safety
        _cb = progress_callback
        user_lang = 'ru'  # default — переопределяется ниже после загрузки профиля

        try:
            # Тариф
            if subscription_tier is None:
                s = Session()
                try:
                    u = s.query(User).filter_by(telegram_id=user_id).first()
                    subscription_tier = getattr(u, 'subscription_tier', 'LIGHT') if u else 'LIGHT'
                finally:
                    s.close()

            # Сохраняем сообщение пользователя в историю
            from .conversation_history import save_message_to_history
            save_message_to_history(user_id, "user", user_message)

            # Язык пользователя (нужен рано, до ctx)
            from i18n import get_user_lang
            user_lang = get_user_lang(user_id)

            # Контекст (async — погода/новости через api_client)
            ctx = await self._build_context(user_id)
            if not ctx:
                return "Could not load profile. Please try again." if user_lang == 'en' else "Не удалось загрузить профиль. Попробуй ещё раз."

            base_prompt = ctx['base_prompt']
            dynamic_context = ctx.get('dynamic_context', '')
            sub_tier = ctx['sub_tier']
            user_lang = ctx.get('user_lang', user_lang)

            # ═══ ОПРЕДЕЛЕНИЕ ЯЗЫКА ПО ТЕКСТУ СООБЩЕНИЯ ═══
            from i18n import detect_lang_from_text
            _detected_lang = detect_lang_from_text(user_message)
            if _detected_lang and _detected_lang != user_lang:
                user_lang = _detected_lang
                if _detected_lang == 'en':
                    dynamic_context += (
                        "\n\n[LANGUAGE OVERRIDE] The user wrote in English. "
                        "Reply ONLY in English regardless of their profile language."
                    )
                else:
                    dynamic_context += (
                        "\n\n[ЯЗЫК] Пользователь написал на русском. "
                        "Отвечай ТОЛЬКО на русском."
                    )

            # ═══ ИСТОРИЯ ДИАЛОГА (загружаем рано — нужна для anti-repetition) ═══
            from .conversation_history import get_conversation_history
            full_history = get_conversation_history(user_id, session=None, limit=20)

            # ═══ КОГНИТИВНОЕ ОБОГАЩЕНИЕ ═══
            # ВАЖНО: все дополнения идут в dynamic_context, НЕ в base_prompt!
            # base_prompt (53K) должен остаться СТАБИЛЬНЫМ для DeepSeek prefix cache.
            from .cognitive import CognitiveEngine
            profile_data = ctx.get('profile_data', {})
            cognitive_hints = CognitiveEngine.build_cognitive_hints(
                user_message, profile_data=profile_data,
                conversation_history=full_history, lang=user_lang
            )
            
            # Оценка ситуации — контекст для самостоятельного рассуждения AI
            tasks_data = ctx.get('tasks', [])
            strategy = CognitiveEngine.plan_response_strategy(user_message, profile_data, tasks_data, lang=user_lang)
            if strategy:
                if user_lang == 'en':
                    cognitive_hints += f"\n\n[SITUATION]\n{strategy['why']}\nTone: {strategy['tone']}"
                else:
                    cognitive_hints += f"\n\n[СИТУАЦИЯ]\n{strategy['why']}\nТон: {strategy['tone']}"
            
            if cognitive_hints:
                dynamic_context += cognitive_hints

            # ═══ ИНТЕГРАЦИИ (факты: что подключено + как использовать) ═══
            _agent_map = []
            try:
                _intg_snap = _get_active_agent_integration_snapshot(user_id)
                _active = _intg_snap.get('labels', [])
                _agent_map = _intg_snap.get('agent_map', [])
                if _active:
                    dynamic_context += f"\n\n[ПОДКЛЮЧЁННЫЕ ИНТЕГРАЦИИ]: {', '.join(_active[:15])}"
                    # Карта "агент → интеграции" — AI знает через какого агента работать
                    if _agent_map:
                        _map_lines = []
                        for _am in _agent_map[:10]:
                            _am_intgs = ', '.join(_am['integrations'][:6])
                            _map_lines.append(f"  @{_am['name']}: {_am_intgs}")
                        dynamic_context += "\n[АГЕНТЫ И ИХ ИНТЕГРАЦИИ]:\n" + '\n'.join(_map_lines)
                        dynamic_context += "\n  → Для работы с интеграцией используй run_agent_action(agent_name=\"ИМЯ\", action=...) или напиши @ИМЯ в сообщении."
                    # Добавляем подсказки по инструментам для каждой категории интеграции
                    try:
                        from anchor_engine import _classify_agent_caps
                        _caps_info = _classify_agent_caps(_active)
                        _tool_hints_parts = []
                        for _cat, _hint in (_caps_info.get('tool_hints') or {}).items():
                            if _hint and _cat != 'email':  # email tools already documented in prompt
                                _tool_hints_parts.append(f"  {_cat}: {_hint}")
                        if _tool_hints_parts:
                            dynamic_context += "\n[КАК ИСПОЛЬЗОВАТЬ ИНТЕГРАЦИИ]:\n" + '\n'.join(_tool_hints_parts[:12])
                    except Exception:
                        pass
                else:
                    dynamic_context += "\n\n[ПОДКЛЮЧЁННЫЕ ИНТЕГРАЦИИ]: нет"
            except Exception as e:
                logger.debug(f"[INTEGRATION_DETECTION] skipped: {e}")


            # ═══ МУЛЬТИАГЕНТНЫЙ АНАЛИЗ ═══
            try:
                emotion = CognitiveEngine.detect_emotion(user_message)
                intent = CognitiveEngine.classify_intent(user_message)
                
                # Семантическая память из Pinecone
                memory_context = ""
                try:
                    memory_context = await asyncio.wait_for(
                        build_memory_context(user_id, user_message, max_chars=1200),
                        timeout=4
                    )
                    if memory_context:
                        dynamic_context += f"\n[СЕМАНТИЧЕСКАЯ ПАМЯТЬ]\n{memory_context}\n"
                except asyncio.TimeoutError:
                    logger.warning("[VECTOR] Memory search timeout (>4s), skipping")
                except Exception as e:
                    logger.warning(f"[VECTOR] Memory search failed: {e}")
                
                orchestrator = get_orchestrator()
                user_now = ctx.get('user_now')
                time_of_day = "день"
                if user_now:
                    h = user_now.hour
                    if 6 <= h < 12: time_of_day = "утро"
                    elif 12 <= h < 18: time_of_day = "день"
                    elif 18 <= h < 23: time_of_day = "вечер"
                    else: time_of_day = "ночь"
                
                multi_context = orchestrator.build_multi_agent_context(
                    user_message=user_message,
                    profile_data=profile_data,
                    tasks_data=tasks_data,
                    memory_context=memory_context,
                    emotion=emotion,
                    intent=intent,
                    time_of_day=time_of_day,
                    lang=user_lang
                )
                if multi_context:
                    dynamic_context += multi_context
            except Exception as e:
                logger.warning(f"[MULTI-AGENT] Context build failed: {e}")
            
            # ═══ САМООБУЧЕНИЕ — ПРЕДПОЧТЕНИЯ ПОЛЬЗОВАТЕЛЯ ═══
            try:
                learner = get_learner()
                user_prefs = learner.get_user_preferences(user_id)
                if user_prefs:
                    dynamic_context += user_prefs
                
                emotional_trend = learner.get_emotional_trend(user_id)
                if emotional_trend:
                    dynamic_context += f"\n{emotional_trend}"
                
                proactive_hint = learner.suggest_proactive_action(user_id, profile_data)
                if proactive_hint:
                    dynamic_context += f"\n{proactive_hint}"

                tool_eff = learner.get_tool_effectiveness_hint(user_id)
                if tool_eff:
                    dynamic_context += tool_eff
            except Exception as e:
                logger.warning(f"[SELF-LEARN] Preferences failed: {e}")

            if len(full_history) > 14:
                old_msgs = full_history[:-14]
                history = full_history[-14:]
                # Краткий дайджест: не просто слова, а реальные пары фраз из старых сообщений
                _digest_pairs = []
                for _om in old_msgs:
                    _role_o = _om.get('role', '')
                    _text_o = (_om.get('content', '') or '').strip()
                    if _role_o == 'user' and _text_o:
                        _digest_pairs.append(f'  👤 {_text_o[:100]}')
                    elif _role_o == 'assistant' and _text_o:
                        _digest_pairs.append(f'  🤖 {_text_o[:120]}')
                if _digest_pairs:
                    _lbl = 'EARLIER IN THIS CONVERSATION' if user_lang == 'en' else 'РАНЕЕ В ЭТОМ РАЗГОВОРЕ'
                    dynamic_context += (f'\n\n[{_lbl}]\n'
                                        + '\n'.join(_digest_pairs[-14:])  # последние 7 пар
                                        + '\n→ Веди диалог непрерывно: помни договорённости и не начинай заново.')
            else:
                history = full_history

            # ═══ USER AGENTS — инжектируем в контекст список агентов пользователя ═══
            try:
                from .user_agents import get_user_active_agents as _gua_hint, load_agent_personality as _lap_hint
                _hint_ids = _gua_hint(user_id)
                if not _hint_ids:
                    # Fallback: собственные офисные агенты (own, status active/paused)
                    try:
                        from models import UserAgent as _UA_h, User as _U_h, Session as _S_h
                        _s_h = _S_h()
                        try:
                            _u_h = _s_h.query(_U_h).filter_by(telegram_id=user_id).first()
                            if _u_h:
                                _hint_ids = [
                                    a.id for a in _s_h.query(_UA_h).filter(
                                        _UA_h.author_id == _u_h.id,
                                        _UA_h.status.in_(['active', 'paused']),
                                    ).all()
                                ]
                        finally:
                            _s_h.close()
                    except Exception:
                        pass
                if _hint_ids:
                    _agent_hints = []
                    for _hid in _hint_ids[:6]:
                        _hdata = _lap_hint(_hid)
                        if _hdata:
                            _hname = _hdata.get('name', '')
                            _hdesc = (_hdata.get('description') or _hdata.get('role') or '')[:100]
                            _hline = f"• @{_hname}" + (f" — {_hdesc}" if _hdesc else "")
                            _agent_hints.append(_hline)
                    if _agent_hints:
                        if user_lang == 'en':
                            dynamic_context += (
                                "\n\n[YOUR AGENTS — active and ready]\n"
                                + "\n".join(_agent_hints)
                                + "\nTo engage: write @Name or say 'switch to Name'. "
                                "If the user's request matches an agent's specialty — proactively suggest engaging them."
                            )
                        else:
                            dynamic_context += (
                                "\n\n[ТВОИ АГЕНТЫ — активны и готовы к работе]\n"
                                + "\n".join(_agent_hints)
                                + "\nЧтобы задействовать агента: напиши @Имя или «Переключись на Имя». "
                                "Если запрос пользователя соответствует специализации агента — предложи его, "
                                "или вызови switch_agent(agent_slug=«Имя»)."
                            )
            except Exception as _ua_hint_e:
                logger.debug("suppressed user_agents hint: %s", _ua_hint_e)

            # ═══ TOKEN BUDGET — обрезаем если превышен лимит ═══
            base_prompt, history = self._trim_prompt_to_budget(base_prompt, history)

            # Инжектируем личность кастомного агента (если активен)
            self._active_agent_data.pop(user_id, None)  # сбрасываем перед каждым запросом
            _agent_tools_allowed: set = set()              # пустое = без ограничений
            try:
                import re as _re_agent
                from .user_agents import (
                    get_user_active_agent, get_user_active_agents,
                    load_agent_personality, build_agent_system_prompt,
                    set_user_focused_agent, remove_user_active_agent,
                )
                # Роутинг по имени агента: "@Алиса текст" или просто "Алиса текст"
                _msg_stripped = (user_message or '').strip()
                _mention_match = _re_agent.match(r'@(\w+)\b', _msg_stripped)
                _active_agent_id = None
                _mention_not_found = False  # флаг: явное @упоминание было, но агент не найден
                _stripped_prefix_end = None  # позиция конца @имя/имя для обрезки
                _all_active_ids = get_user_active_agents(user_id)

                if _mention_match:
                    # Явный @mention — ищем среди активных агентов
                    _mention_name = _mention_match.group(1).lower()
                    for _cid in _all_active_ids:
                        _cdata = load_agent_personality(_cid)
                        if _cdata and _cdata['name'].lower() == _mention_name:
                            _active_agent_id = _cid
                            _stripped_prefix_end = _mention_match.end()
                            try:
                                set_user_focused_agent(user_id, _cid)
                            except Exception as _e:
                                logger.debug("suppressed: %s", _e)
                            logger.info(f"[AGENT] @mention routed to '{_cdata['name']}' (id={_cid})")
                            break
                    # Fallback: ищем среди собственных офисных агентов пользователя (status active/paused)
                    if _active_agent_id is None:
                        try:
                            from models import UserAgent as _UA_m, User as _U_m, Session as _S_m
                            _s_fb = _S_m()
                            try:
                                _u_fb = _s_fb.query(_U_m).filter_by(telegram_id=user_id).first()
                                if _u_fb:
                                    _own = _s_fb.query(_UA_m).filter(
                                        _UA_m.author_id == _u_fb.id,
                                        _UA_m.status.in_(['active', 'paused']),
                                    ).all()
                                    for _oa in _own:
                                        if _oa.name and _oa.name.lower() == _mention_name:
                                            _active_agent_id = _oa.id
                                            _stripped_prefix_end = _mention_match.end()
                                            # Добавляем в активные чтобы следующий раз нашёлся сразу
                                            try:
                                                set_user_focused_agent(user_id, _oa.id)
                                            except Exception as _e:
                                                logger.debug("suppressed: %s", _e)
                                            logger.info(f"[AGENT] @mention own-agent '{_oa.name}' (id={_oa.id})")
                                            break
                            finally:
                                _s_fb.close()
                        except Exception as _fb_e:
                            logger.debug(f"[AGENT] own-agent fallback error: {_fb_e}")
                    if _active_agent_id is None:
                        _mention_not_found = True
                else:
                    # Имя без @ — ищем совпадение с началом сообщения (тихий роутинг)
                    _first_word = _re_agent.match(r'(\w+)\b', _msg_stripped)
                    if _first_word:
                        _fw = _first_word.group(1).lower()
                        # Сначала в подписках маркетплейса
                        for _cid in _all_active_ids:
                            _cdata = load_agent_personality(_cid)
                            if _cdata and _cdata['name'].lower() == _fw:
                                _active_agent_id = _cid
                                _stripped_prefix_end = _first_word.end()
                                try:
                                    set_user_focused_agent(user_id, _cid)
                                except Exception as _e:
                                    logger.debug("suppressed: %s", _e)
                                logger.info(f"[AGENT] name-prefix routed to '{_cdata['name']}' (id={_cid})")
                                break
                        # Fallback: собственные офисные агенты
                        if _active_agent_id is None:
                            try:
                                from models import UserAgent as _UA_np, User as _U_np, Session as _S_np
                                _s_np = _S_np()
                                try:
                                    _u_np = _s_np.query(_U_np).filter_by(telegram_id=user_id).first()
                                    if _u_np:
                                        _own_np = _s_np.query(_UA_np).filter(
                                            _UA_np.author_id == _u_np.id,
                                            _UA_np.status.in_(['active', 'paused']),
                                        ).all()
                                        for _oa_np in _own_np:
                                            if _oa_np.name and _oa_np.name.lower() == _fw:
                                                _active_agent_id = _oa_np.id
                                                _stripped_prefix_end = _first_word.end()
                                                try:
                                                    set_user_focused_agent(user_id, _oa_np.id)
                                                except Exception as _e:
                                                    logger.debug("suppressed: %s", _e)
                                                logger.info(f"[AGENT] name-prefix own-agent '{_oa_np.name}' (id={_oa_np.id})")
                                                break
                                finally:
                                    _s_np.close()
                            except Exception as _np_e:
                                logger.debug(f"[AGENT] name-prefix own-agent fallback error: {_np_e}")

                # Субагенты встревают ТОЛЬКО при явном вызове:
                # 1. Пользователь написал @имя или имя-префикс (обработано выше)
                # 2. ASI сам передаёт управление агенту (через focused_agent set внутри tool-chain)
                # Автоматического инжекта без вызова — нет.
                if not _mention_not_found and _active_agent_id is None:
                    pass  # ASI default — не подтягиваем focused_agent автоматически

                # Убираем @имя / имя-триггер из начала сообщения — AI не должен его видеть
                if _stripped_prefix_end is not None:
                    _msg_tail = _msg_stripped[_stripped_prefix_end:].strip()
                    if _msg_tail:
                        user_message = _msg_tail

                if _active_agent_id:
                    _agent_data = load_agent_personality(_active_agent_id)
                    if _agent_data:
                        self._active_agent_data[user_id] = _agent_data  # per-user, без race condition
                        base_prompt = build_agent_system_prompt(_agent_data, base_prompt)
                        # Сохраняем разрешённые инструменты для enforce-а ниже
                        _allowed = _agent_data.get('tools_allowed') or []
                        if _allowed:
                            _agent_tools_allowed = set(_allowed)
                            # Если у агента есть скрипт — run_agent_action всегда доступен
                            if _agent_data.get('python_code', '').strip():
                                _agent_tools_allowed.add('run_agent_action')
                        logger.info(
                            f"[AGENT] process_request: injected personality '{_agent_data['name']}' "
                            f"(id={_active_agent_id}, tools={_allowed or 'all'})"
                        )
                    else:
                        # Агент удалён/деактивирован — убираем только его из списка
                        try:
                            remove_user_active_agent(user_id, _active_agent_id)
                        except Exception as _e:
                            logger.debug("suppressed: %s", _e)
            except Exception as _ae:
                logger.warning(f"[AGENT] process_request personality inject error: {_ae}")

            # @упоминание было, но агент не найден — сообщаем сразу, не отвечаем от чужого имени
            if locals().get('_mention_not_found'):
                try:
                    _all_names = []
                    _ids_for_hint = get_user_active_agents(user_id) if 'get_user_active_agents' in dir() else []
                    for _hid in _ids_for_hint:
                        _hd = load_agent_personality(_hid) if 'load_agent_personality' in dir() else None
                        if _hd:
                            _all_names.append(_hd['name'])
                except Exception:
                    _all_names = []
                _not_found_name = locals().get('_mention_name', '').capitalize()
                if _all_names:
                    _hint = ', '.join(_all_names)
                    _err_msg = (
                        f"Агент @{_not_found_name} не найден среди активных.\n"
                        f"Активные агенты: {_hint}.\n"
                        f"Напиши «{_all_names[0]} привет» или «@{_all_names[0]} привет» — ответит он."
                    )
                else:
                    _err_msg = (
                        f"Агент @{_not_found_name} не найден. Активных агентов нет.\n"
                        f"Подключи агента в разделе Маркетплейс."
                    )
                return _err_msg

            # Запускаем python_code агента (реалтайм-данные перед ответом)
            try:
                if '_agent_data' in dir() and _agent_data and _agent_data.get('python_code', '').strip():
                    import os as _os_pc
                    import sys as _sys_pc
                    import asyncio as _aio_pc
                    _py_code = _agent_data['python_code'].strip()
                    _api_keys_raw = _agent_data.get('user_api_keys', '') or ''
                    # Чистое окружение — НЕ наследуем серверные секреты
                    _env = {
                        'PATH': _os_pc.environ.get('PATH', '/usr/bin:/bin'),
                        'PYTHONIOENCODING': 'utf-8',
                    }
                    if _sys_pc.platform != 'win32':
                        _env['HOME'] = _os_pc.environ.get('HOME', '/tmp')
                    else:
                        # Windows требует системные переменные для инициализации Python
                        for _wk in ('SystemRoot', 'SystemDrive', 'TEMP', 'TMP', 'WINDIR',
                                    'COMSPEC', 'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH'):
                            if _wk in _os_pc.environ:
                                _env[_wk] = _os_pc.environ[_wk]
                    # Добавляем только пользовательские API-ключи (никаких серверных переменных)
                    for _kline in _api_keys_raw.splitlines():
                        _kline = _kline.strip()
                        if '=' in _kline and not _kline.startswith('#'):
                            _k, _, _v = _kline.partition('=')
                            _env[_k.strip()] = _v.strip()
                    # Ограничение памяти 64MB (только Linux/Railway, preexec_fn не работает на Windows)
                    _is_linux = _sys_pc.platform != 'win32'
                    def _set_mem_limit():
                        try:
                            import resource as _res
                            _limit = 64 * 1024 * 1024  # 64 MB
                            _res.setrlimit(_res.RLIMIT_AS, (_limit, _limit))
                        except Exception as _e:
                            logger.debug("suppressed: %s", _e)

                    async def _run_agent_code():
                        _kwargs = dict(
                            stdout=_aio_pc.subprocess.PIPE,
                            stderr=_aio_pc.subprocess.PIPE,
                            env=_env,
                        )
                        if _is_linux:
                            _kwargs['preexec_fn'] = _set_mem_limit
                        proc = await _aio_pc.create_subprocess_exec(
                            _sys_pc.executable, '-c', _py_code,
                            **_kwargs,
                        )
                        _comm_task_pc = _aio_pc.create_task(proc.communicate())
                        try:
                            stdout, stderr = await _aio_pc.wait_for(_comm_task_pc, timeout=float(API_TIMEOUT_QUICK))
                            out = stdout.decode('utf-8', errors='replace').strip()[:2000]
                            err = stderr.decode('utf-8', errors='replace').strip()[:500]
                            return out, err
                        except _aio_pc.TimeoutError:
                            proc.kill()
                            try:
                                await _comm_task_pc
                            except BaseException:
                                pass
                            try:
                                await proc.wait()
                            except Exception:
                                pass
                            return '', f'Тайм-аут выполнения скрипта ({API_TIMEOUT_QUICK} сек)'
                    _code_output, _code_stderr = await _run_agent_code()
                    if _code_output:
                        # Очищаем HTML-теги и RFC822 артефакты из IMAP/email вывода
                        import re as _re_clean
                        # 1. Полные mailto-ссылки: <a href="mailto:email">text</a> → email
                        _code_output_clean = _re_clean.sub(
                            r'<a[^>]*href=["\']mailto:([^"\'>\s]+)["\'][^>]*>[^<]*</a>', r'\1', _code_output, flags=_re_clean.IGNORECASE | _re_clean.DOTALL)
                        # 1b. Незакрытые mailto: <a href="mailto:email">text → email
                        _code_output_clean = _re_clean.sub(
                            r'<a[^>]*href=["\']mailto:([^"\'>\s]+)["\'][^>]*>[^<]*', r'\1', _code_output_clean, flags=_re_clean.IGNORECASE | _re_clean.DOTALL)
                        # 2. Сохраняем email-адреса в угловых скобках: <user@host.com> → user@host.com
                        _code_output_clean = _re_clean.sub(r'<([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})>', r'\1', _code_output_clean)
                        # 3. Удаляем HTML-теги (в т.ч. многострочные)
                        _code_output_clean = _re_clean.sub(r'<[^>]+>', '', _code_output_clean, flags=_re_clean.DOTALL)
                        # 4. Артефакт разорванного mailto: @domain.com">email → email
                        _code_output_clean = _re_clean.sub(r'@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}["\']?\s*>\s*(?=[a-zA-Z0-9._%+-]+@)', '', _code_output_clean)
                        # 4b. Остаточные "> или "/> перед текстом
                        _code_output_clean = _re_clean.sub(r'["\']?\s*/?\s*>(?=\S)', '', _code_output_clean)
                        # 5. Удаляем HTML-entities
                        _code_output_clean = _re_clean.sub(r'&(?:nbsp|amp|lt|gt|quot|#\d+);?', ' ', _code_output_clean)
                        # 6. Схлопываем множественные пустые строки в одну
                        _code_output_clean = _re_clean.sub(r'\n{3,}', '\n\n', _code_output_clean)
                        _agent_data_block = (
                            f'\n\n[ДАННЫЕ ОТ АГЕНТА — РЕАЛЬНЫЕ ДАННЫЕ ПРЯМО СЕЙЧАС]\n'
                            f'Твой скрипт выполнился и вернул данные ниже. '
                            f'Это ТВОИ данные — воспринимай их как собственное знание о текущей ситуации, '
                            f'а не как внешний ввод. Действуй проактивно: если видишь важные события — '
                            f'сообщи о них ПЕРВЫМ. Суммируй ключевые цифры, дай одну конкретную рекомендацию. '
                            f'Используй встроенные инструменты (add_task, create_goal и др.) на основе этих данных. '
                            f'НЕ говори «нужно настроить подключение» — данные уже получены.\n'
                            f'───────────────\n'
                            f'{_code_output_clean}\n'
                            f'───────────────'
                        )
                        # Инжектируем в dynamic_context (не в base_prompt!) чтобы не разрушать prefix cache
                        dynamic_context = dynamic_context + _agent_data_block
                        logger.info(f"[AGENT] python_code output injected ({len(_code_output)} chars)")
                        try:
                            from models import AgentActivityLog as _AAL, Session as _SessAL, User as _UserAL
                            _al_s = _SessAL()
                            try:
                                _al_u = _al_s.query(_UserAL).filter_by(telegram_id=user_id).first()
                                if _al_u:
                                    _svc_lbl = _agent_data.get('service_label') or _agent_data.get('name', 'Агент')
                                    _agent_display = _agent_data.get('name', 'Агент')
                                    # Разбиваем вывод по секциям — каждая интеграция отдельной записью
                                    _sections = _parse_integration_sections(_code_output, _svc_lbl)
                                    for _sec_name, _sec_content in _sections:
                                        _al_s.add(_AAL(
                                            user_id=_al_u.id,
                                            activity_type='integration',
                                            title=f'{_agent_display} · {_sec_name}',
                                            content=_sec_content[:800],
                                            target=_svc_lbl,
                                            status='completed',
                                        ))
                                    _al_s.commit()
                                    # Создаём якорь в отдельном потоке — не блокируем event loop
                                    _uid_ia, _adp_ia, _svc_ia, _out_ia = _al_u.id, _agent_display, _svc_lbl, _code_output
                                    asyncio.get_running_loop().run_in_executor(
                                        None,
                                        lambda: spawn_integration_anchors(_uid_ia, _adp_ia, _svc_ia, _out_ia)
                                    )
                            finally:
                                _al_s.close()
                        except Exception as _al_e:
                            logger.warning(f"[AGENT] activity log (success) error: {_al_e}")
                    else:
                        _err_detail = _code_stderr if _code_stderr else 'скрипт не вернул вывода'
                        dynamic_context = dynamic_context + (
                            f'\n\n[ВНЕШНИЕ ДАННЫЕ НЕДОСТУПНЫ]\n'
                            f'Скрипт агента не смог получить данные: {_err_detail}\n'
                            f'Сообщи пользователю кратко и по-человечески: что именно не получилось '
                            f'(timeout, ошибка сети, неверный ключ). '
                            f'Предложи проверить ключи/настройки в разделе «Мои агенты». '
                            f'НЕ переключайся на данные профиля пользователя (задачи, кампании) — '
                            f'они не имеют отношения к теме этого агента.'
                        )
                        logger.warning(f"[AGENT] python_code no output, stderr: {_code_stderr}")
                        try:
                            from models import AgentActivityLog as _AAL, Session as _SessAL, User as _UserAL
                            _al_s = _SessAL()
                            try:
                                _al_u = _al_s.query(_UserAL).filter_by(telegram_id=user_id).first()
                                if _al_u:
                                    _svc_lbl = _agent_data.get('service_label') or _agent_data.get('name', 'Агент')
                                    _al_s.add(_AAL(
                                        user_id=_al_u.id,
                                        activity_type='integration',
                                        title=f'{_agent_data.get("name","Агент")}: ошибка получения данных',
                                        content=_err_detail[:800],
                                        target=_svc_lbl,
                                        status='failed',
                                    ))
                                    _al_s.commit()
                            finally:
                                _al_s.close()
                        except Exception as _al_e:
                            logger.warning(f"[AGENT] activity log (fail) error: {_al_e}")
            except Exception as _pce:
                logger.warning(f"[AGENT] python_code exec error: {_pce}")
                if '_agent_data' in dir() and _agent_data and _agent_data.get('python_code', '').strip():
                    dynamic_context = dynamic_context + (
                        f'\n\n[ВНЕШНИЕ ДАННЫЕ НЕДОСТУПНЫ]\n'
                        f'Не удалось запустить скрипт агента: {_pce}\n'
                        f'Сообщи пользователю кратко и по-человечески. '
                        f'Предложи проверить ключи/настройки агента. '
                        f'НЕ переключайся на данные профиля пользователя (задачи, кампании) — '
                        f'они не имеют отношения к теме этого агента.'
                    )
                    try:
                        from models import AgentActivityLog as _AAL, Session as _SessAL, User as _UserAL
                        _al_s = _SessAL()
                        try:
                            _al_u = _al_s.query(_UserAL).filter_by(telegram_id=user_id).first()
                            if _al_u:
                                _svc_lbl = _agent_data.get('service_label') or _agent_data.get('name', 'Агент')
                                _al_s.add(_AAL(
                                    user_id=_al_u.id,
                                    activity_type='integration',
                                    title=f'{_agent_data.get("name","Агент")}: скрипт не запустился',
                                    content=str(_pce)[:800],
                                    target=_svc_lbl,
                                    status='failed',
                                ))
                                _al_s.commit()
                        finally:
                            _al_s.close()
                    except Exception as _al_e:
                        logger.warning(f"[AGENT] activity log (exc) error: {_al_e}")

            messages = [{"role": "system", "content": base_prompt}]
            # Динамический контекст — второе system-сообщение позволяет DeepSeek кешировать весь статичный prefix (53K) целиком
            if dynamic_context:
                messages.append({"role": "system", "content": dynamic_context})

            # ═══ АНТИ-ПОВТОР: инжектируем предыдущие ответы для ВСЕХ сообщений ═══
            if history:
                _prev_ai_responses = []
                for _h_msg in reversed(history):
                    if _h_msg.get('role') == 'assistant' and _h_msg.get('content'):
                        _prev_ai_responses.append(_h_msg['content'][:300])
                    if len(_prev_ai_responses) >= 3:
                        break
                if _prev_ai_responses:
                    _ar_block = '\n---\n'.join(_prev_ai_responses)
                    _msg_lower_ar = (user_message or '').strip().lower().rstrip('!., ')
                    _trivial_ar = _msg_lower_ar in ('привет', 'хай', 'здравствуй', 'здравствуйте',
                                                     'добрый день', 'доброе утро', 'добрый вечер',
                                                     'как дела', 'что нового', 'что делаешь')
                    if _trivial_ar:
                        messages.append({"role": "system", "content": (
                            f"АНТИ-ПОВТОР: вот твои последние ответы пользователю — НЕ ПОВТОРЯЙ их содержание. "
                            f"Скажи что-то ПРИНЦИПИАЛЬНО ДРУГОЕ. Другой тон, другая тема, другой подход.\n"
                            f"Уже сказано:\n{_ar_block}"
                        )})
                    else:
                        messages.append({"role": "system", "content": (
                            f"АНТИ-ПОВТОР: НЕ ПОВТОРЯЙ мысли/советы/факты из своих предыдущих ответов. "
                            f"Если пользователь спрашивает то же — дай ДРУГОЙ угол, НОВЫЕ детали.\n"
                            f"Твои последние ответы:\n{_ar_block}"
                        )})

            if history:
                messages.extend(history)
            messages.append({"role": "user", "content": user_message})

            # Адаптивный tool_choice (с учётом профиля и задач)
            initial_tool_choice = self._determine_tool_choice(
                user_message, profile_data=profile_data, tasks_data=tasks_data
            )

            # ===== Tool calling loop =====
            all_execution_results = []
            MAX_ITERATIONS = 5
            # 5 параллельных инструментов/итерацию: больше работы за один API-вызов → меньше round-trips → меньше токенов
            MAX_TOOLS_PER_ITERATION = 7
            seen_tools = set()  # Для предотвращения дублей
            _seen_research_kws = []  # Нормализованные keyword-sets для fuzzy dedup research/web_search
            # Критичные инструменты — лимит вызовов за сессию
            once_only_tools = {'create_post', 'delete_post', 'publish_to_telegram', 'publish_to_discord', 'start_content_campaign', 'start_delegation_campaign'}  # строго 1 раз
            multi_limit_tools = {'add_task': 5, 'update_profile': 2, 'create_goal': 3, 'run_agent_action': 8, 'send_email': 5, 'delegate_task': 5}  # лимиты per turn
            used_once_only = set()
            multi_limit_counts = {}

            # Smart tool filtering — reduce tokens sent to API
            self._current_user_id = user_id
            tools_to_exclude = self._select_tools_for_message(user_message)
            # Дополнительные запрещённые инструменты от вызывающего кода (напр. при обзоре отчита агента)
            if exclude_tools:
                tools_to_exclude = tools_to_exclude | set(exclude_tools)
            # run_agent_action доступен когда есть агент со скриптом
            # (активный через @упоминание ИЛИ любой агент пользователя с python_code)
            _cur_agent = self._active_agent_data.get(user_id)
            if _cur_agent and _cur_agent.get('python_code', '').strip():
                # Агент со скриптом: скрываем run_user_script чтобы не конкурировал
                tools_to_exclude.add('run_user_script')
            elif _agent_map:
                # Нет активного агента, но есть агенты с интеграциями → run_agent_action доступен
                # AI передаст agent_name из [АГЕНТЫ И ИХ ИНТЕГРАЦИИ] блока
                tools_to_exclude.add('run_user_script')
            else:
                tools_to_exclude.add('run_agent_action')

            # Enforce agent tools_allowed: если агент задал whitelist — прячем остальные
            if _agent_tools_allowed:
                from .tools import get_available_tools as _gat
                _all_tool_names = {t['function']['name'] for t in _gat()}
                _forbidden = _all_tool_names - _agent_tools_allowed
                tools_to_exclude = tools_to_exclude | _forbidden
                logger.info(f"[AGENT] tools_allowed enforced: showing {len(_agent_tools_allowed)} tools, hiding {len(_forbidden)}")

            # Прогресс — живые фразы
            if _cb:
                try:
                    await _cb(random.choice(self._get_thinking_phrases(user_lang)))
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)

            # ── Fast path: чисто разговорное сообщение → без инструментов (~3-5s) ────
            # При tool_choice="auto" и отсутствии action-слов DeepSeek всё равно
            # читает ~37K токенов инструментов, решает их не вызывать и возвращает текст.
            # Пропускаем тулы сразу → экономия ~12-15 сек.
            _ml = (user_message or '').lower()
            _CONVO_STARTS = ('привет', 'здравствуй', 'добрый', 'привет!', 'хеллоу',
                             'hello', 'hi ', 'hi!', 'hola', 'bonjour', 'hey ')
            _CONVO_CONTAINS = ('как дела', 'как ты', 'что умеешь', 'что ты умеешь',
                               'кто ты', 'расскажи о себе', 'ты кто', 'что ты такое',
                               'how are you', 'what can you do', 'who are you',
                               'what are you', 'tell me about yourself')
            _ACTION_HINTS = ('задач', 'цел', 'напомн', 'созда', 'добавь', 'удали',
                             'измени', 'сделай', 'найди', 'найти', 'research',
                             'email', 'пост', 'опубли', 'делегир', 'исследу',
                             'task', 'goal', 'remind', 'create', 'delete', 'search',
                             'расписани', 'план', 'schedule', 'plan', 'campaign',
                             'поиск', 'поищ', 'ищем', 'ищи', 'искат', 'подбер',
                             'подбир', 'автоматиз', 'предприниматель', 'клиент',
                             'аудитори', 'контакт', 'лид', 'lead', 'find',
                             'стратеги', 'analyz', 'анализ', 'отправ', 'send')
            _is_fast_convo = (
                initial_tool_choice == 'auto'
                and not _agent_tools_allowed  # агент может требовать инструменты
                and (any(_ml.startswith(p) for p in _CONVO_STARTS)
                     or any(p in _ml for p in _CONVO_CONTAINS))
                and not any(p in _ml for p in _ACTION_HINTS)
            )
            if _is_fast_convo:
                logger.info(f"[FAST_CONVO] Skipping tools for conversational message")
                _fc_resp = await self.call_ai(
                    messages, use_tools=False, max_tokens=500,
                    api_timeout=API_TIMEOUT_NORMAL)
                _fc_content = _fc_resp['choices'][0]['message'].get('content', '')
                return await self._finalize_response(
                    _fc_content, user_message, user_id, [])

            _auto_saved_notes = []  # заголовки исследований, сохранённых в заметки в этом turn

            for iteration in range(MAX_ITERATIONS):
                # Первая итерация может быть "required", остальные "auto"
                tc = initial_tool_choice if iteration == 0 else "auto"

                # Обновляем прогресс перед вызовом AI
                if _cb and iteration > 0:
                    try:
                        await _cb(random.choice(self._get_deep_thinking_phrases(user_lang)))
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)

                # Если уже есть результаты инструментов — финальный ответ без tools
                # (убирает ~40 определений инструментов из запроса → значительно быстрее)
                _is_last_iter = (iteration >= MAX_ITERATIONS - 1)
                _allow_tools = not _is_last_iter

                # Последняя итерация: инжектируем краткую инструкцию для финального ответа
                # → меньше max_tokens → быстрее генерация
                if _is_last_iter and all_execution_results:
                    _note_hint_ru = ''
                    _note_hint_en = ''
                    if _auto_saved_notes:
                        _titles_str = '», «'.join(n[:40] for n in _auto_saved_notes[:3])
                        _note_hint_ru = (
                            f" Данные сохранены в заметки («{_titles_str}»)."
                            f" Скажи что нашёл (2-4 предложения), между делом упомяни заметки."
                            f" Если пользователь задал ВОПРОС — дай конкретный ответ, не отделывайся общими фразами."
                        )
                        _note_hint_en = (
                            f" Research saved to notes («{_titles_str}»)."
                            f" Say what you found (2-4 sentences), casually mention notes."
                            f" If user asked a QUESTION — give a concrete answer, don't be vague."
                        )
                    if user_lang == 'en':
                        messages.append({"role": "system", "content": (
                            "Summarize results for the user (up to 2000 chars for analysis, up to 800 for others). "
                            "Rephrase in your own words. Preserve URLs. Don't repeat delegate_task responses.\n"
                            "Explain: WHAT you did → WHAT result you got → WHAT it means for the user's goal. "
                            "If an action produced no result — say so honestly and suggest a concrete next step. "
                            "Never write 'Done' without explanation."
                            + _note_hint_en
                        )})
                    else:
                        messages.append({"role": "system", "content": (
                            "Подытожь результаты для пользователя (для анализа/стратегии — до 2000 символов, для остального — до 800). "
                            "Своими словами. Сохраняй URL. Не повторяй ответы delegate_task.\n"
                            "Расскажи: ЧТО именно сделал → КАКОЙ результат получил → ЧТО это означает для цели пользователя. "
                            "Если действие не принесло результата — скажи честно и предложи конкретный следующий шаг. "
                            "Не пиши 'Готово' без объяснения — объясни суть."
                            + _note_hint_ru
                        )})

                # Text-only call (no tools) uses shorter timeout + fewer tokens
                _timeout = API_TIMEOUT_NORMAL if not _allow_tools else None
                # Адаптивный лимит: аналитические/стратегические вопросы требуют больше токенов
                _ANALYSIS_KWORDS = (
                    'инвестор', 'investor', 'стратег', 'strategy', 'анализ', 'analys',
                    'план', 'plan', 'риск', 'risk', 'рынок', 'market', 'бизнес', 'business',
                    'привлеч', 'attract', 'масштаб', 'scale', 'думаешь', 'think',
                    'мнение', 'opinion', 'плюсы', 'минусы', 'pros', 'cons', 'оцени',
                    'что думаешь', 'совет', 'recommend', 'как лучше', 'сравни',
                )
                _ml_lower = (user_message or '').lower()
                _is_analysis_q = any(kw in _ml_lower for kw in _ANALYSIS_KWORDS)
                if _is_last_iter and all_execution_results:
                    _max_tok = 1200 if _is_analysis_q else 800
                else:
                    _max_tok = 1500 if _is_analysis_q else 800
                response = await self.call_ai(
                    messages,
                    use_tools=_allow_tools,
                    subscription_tier=sub_tier,
                    tool_choice=tc if _allow_tools else None,
                    max_tokens=_max_tok,
                    exclude_tools=tools_to_exclude if _allow_tools else None,
                    api_timeout=_timeout)

                msg = response['choices'][0]['message']
                content = msg.get('content', '')
                tool_calls = msg.get('tool_calls', [])

                if not tool_calls:
                    # AI ответил текстом → сразу возвращаем (retry убран для скорости)
                    return await self._finalize_response(
                        content, user_message, user_id, all_execution_results)

                # AI вызвал tools → добавляем assistant message в цепочку
                messages.append(msg)

                # Показываем «думаю вслух» — частичный текст AI до вызова инструментов
                if content.strip() and _cb:
                    try:
                        _preview = content.strip()[:200]
                        if len(content.strip()) > 200:
                            _preview += '...'
                        # Подавляем мета-комментарии о намерении вызвать инструмент —
                        # пользователю нужен результат, а не внутреннее нарративство AI
                        _prev_lc = _preview.lower()
                        _META_KW = ('прямой вызов', 'вызов инструмента', 'вызову инструмент',
                                    'использую инструмент', 'применяю инструмент', 'вызываю инструмент')
                        _META_START = ('попробую через', 'попробую вызвать', 'сейчас вызов',
                                       'сейчас попробую вызов', 'выполню через инструмент')
                        _is_meta_preview = (
                            any(_prev_lc.startswith(p) for p in _META_START)
                            or (len(_preview) < 90 and any(kw in _prev_lc for kw in _META_KW))
                        )
                        if not _is_meta_preview:
                            await _cb(_preview)
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)

                # ── Pass 1: валидация (последовательно — dedup/limits — shared state) ──
                _ready_calls = []   # (tc_item, name, args, reason)
                _counted = 0
                for tc_item in tool_calls:
                    func = tc_item.get('function', {})
                    name = func.get('name', '')
                    try:
                        args = json.loads(func.get('arguments', '{}'))
                    except Exception:
                        args = {}
                    if not isinstance(args, dict):
                        logger.warning(f"[EXEC] {name}: arguments is {type(args).__name__}, reset")
                        args = {}

                    # Per-iteration cap
                    if _counted >= MAX_TOOLS_PER_ITERATION:
                        logger.warning(f"[SPEED] Skipping {name} — cap ({MAX_TOOLS_PER_ITERATION}) reached")
                        messages.append({"role": "tool", "tool_call_id": tc_item['id'],
                            "content": json.dumps({"status": f"skipped: max {MAX_TOOLS_PER_ITERATION} per iter"}, ensure_ascii=False)})
                        continue

                    # Dedup
                    dedup_key = f"{name}:{json.dumps(args, sort_keys=True)}"
                    if dedup_key in seen_tools:
                        logger.warning(f"[DEDUP] Skipping duplicate {name}")
                        messages.append({"role": "tool", "tool_call_id": tc_item['id'],
                            "content": '{"status": "skipped: duplicate call"}'})
                        continue
                    seen_tools.add(dedup_key)

                    # Fuzzy dedup for research/web_search — block only near-identical queries (>70% overlap)
                    if name in ('research_topic', 'web_search'):
                        _q_raw = (args.get('query') or args.get('topic') or args.get('prompt') or '').lower()
                        _q_kws = set(w for w in _q_raw.split() if len(w) > 2)
                        if _q_kws:
                            for _prev_kws in _seen_research_kws:
                                _overlap = len(_q_kws & _prev_kws) / max(len(_q_kws | _prev_kws), 1)
                                if _overlap > 0.7:
                                    logger.warning(f"[FUZZY_DEDUP] Skipping near-identical {name}: {_q_raw[:80]}")
                                    messages.append({"role": "tool", "tool_call_id": tc_item['id'],
                                        "content": '{"status": "skipped: nearly identical query already executed this session. Rethink: what NEW information do you need?"}'})
                                    _q_kws = None
                                    break
                            if _q_kws is None:
                                continue
                            _seen_research_kws.append(_q_kws)

                    # Once-only
                    if name in once_only_tools:
                        if name in used_once_only:
                            logger.warning(f"[ONCE_ONLY] Skipping second {name}")
                            messages.append({"role": "tool", "tool_call_id": tc_item['id'],
                                "content": json.dumps({"status": f"skipped: {name} already called"}, ensure_ascii=False)})
                            continue
                        used_once_only.add(name)

                    # Multi-limit
                    if name in multi_limit_tools:
                        multi_limit_counts[name] = multi_limit_counts.get(name, 0) + 1
                        if multi_limit_counts[name] > multi_limit_tools[name]:
                            logger.warning(f"[MULTI_LIMIT] Skipping {name} #{multi_limit_counts[name]}")
                            messages.append({"role": "tool", "tool_call_id": tc_item['id'],
                                "content": json.dumps({"status": f"skipped: {name} limit"}, ensure_ascii=False)})
                            continue

                    _counted += 1
                    _ready_calls.append((tc_item, name, args, f"AI iter {iteration+1}: {name}"))

                # ── Pass 1.5: Force create_goal для wish-messages ──────────────
                # Если пользователь хочет/планирует/мечтает, а AI не вызвал create_goal
                # → инжектируем create_goal с названием из сообщения
                if iteration == 0:
                    _has_create_goal = any(n == 'create_goal' for (_, n, _, _) in _ready_calls)
                    if not _has_create_goal:
                        import re as _re_cg
                        _cg_wish_rx = _re_cg.search(
                            r'(?:хочу|хотел\s*бы|хотела\s*бы|планирую|собираюсь|мечтаю|стремлюсь|намерен[а]?|моя\s+цель)\s+(.+)',
                            user_message, _re_cg.IGNORECASE
                        )
                        _cg_garbage = ('узнать', 'спросить', 'понять', 'обсудить', 'поговорить',
                                       'чтобы ты', 'чтоб ты', 'попросить', 'посмотреть',
                                       'подумать', 'разобраться', 'попробовать')
                        if _cg_wish_rx:
                            _cg_title = _cg_wish_rx.group(1).strip().rstrip('.,!?')
                            if _cg_title and len(_cg_title) > 3 and not any(_cg_title.lower().startswith(g) for g in _cg_garbage):
                                # Capitalize first letter
                                _cg_title = _cg_title[0].upper() + _cg_title[1:]
                                _fake_tc = {'id': f'forced_create_goal_{iteration}', 'function': {'name': 'create_goal'}}
                                _ready_calls.insert(0, (_fake_tc, 'create_goal', {'title': _cg_title}, 'forced: wish-message'))
                                logger.info(f"[FORCE_GOAL] Injected create_goal(title={_cg_title!r})")

                # ── Pass 2: выполняем все валидные tools ПАРАЛЛЕЛЬНО ────────────
                # Каждый вызов получает отдельную DB-сессию (session=None → auto)
                # Per-tool timeouts: тяжёлые агентные цепи — 120с, всё остальное — 60с
                _TOOL_TIMEOUT_MAP = {
                    'delegate_task': 120, 'run_agent_action': 120,
                    'research_topic': 90, 'web_search': 45,
                    'send_outreach_email': 45, 'send_follow_up_email': 45,
                    'reply_to_outreach_email': 45, 'negotiate_by_email': 45,
                    'check_emails': 45,
                    'generate_image': 90,
                    'publish_to_telegram': 45, 'publish_to_discord': 45,
                    'create_post': 60,
                }
                _DEFAULT_TOOL_TIMEOUT = 60

                async def _exec_one(_tc, _name, _args, _reason):
                    # ── Пре-анонс для delegate_task (не отправляем — уже сохраняется в _save_ifd) ──
                    if _cb and _name == 'delegate_task':
                        pass  # delegate_task handler saves director message via _save_ifd
                    elif _cb:
                        try:
                            await _cb(self._tool_progress_text(_name, iteration + 1, lang=user_lang))
                        except Exception as _e:
                            logger.debug("suppressed: %s", _e)
                    _tool_timeout = _TOOL_TIMEOUT_MAP.get(_name, _DEFAULT_TOOL_TIMEOUT)
                    try:
                        _results = await asyncio.wait_for(
                            self.execute_actions(
                                [{"tool": _name, "params": _args, "reason": _reason}],
                                user_id, session=None,
                                user_message=user_message, web_context=web_context),
                            timeout=_tool_timeout,
                        )
                        _r = _results[0] if _results else {"success": False, "error": "no result"}
                        if _r.get('success'):
                            _raw_res = _r['result']
                            # Если handler вернул dict с _human_summary — используем его вместо raw JSON
                            if isinstance(_raw_res, dict) and '_human_summary' in _raw_res:
                                _rc = _raw_res['_human_summary']
                            else:
                                _rc = json.dumps(_raw_res, ensure_ascii=False, default=str)
                            _rc = CognitiveEngine.compress_tool_result(_rc)
                            try: get_learner().record_tool_result(user_id, _name, True)
                            except Exception as _lr: logger.debug("suppressed learner: %s", _lr)

                            # ── Авто-заметка: только research_topic с действительно глубоким контентом ──
                            # Порог 3000 символов + дедуп по теме за 24ч — минимум спама.
                            _RESEARCH_AUTO_SAVE = {'research_topic'}
                            _raw_result_str = _r['result'] if isinstance(_r['result'], str) else _rc
                            if _name in _RESEARCH_AUTO_SAVE and len(_raw_result_str) > 3000:
                                try:
                                    from .handlers import save_note as _auto_save_note
                                    _note_q = (
                                        _args.get('query') or _args.get('topic') or
                                        _args.get('prompt') or _name
                                    )
                                    _note_title = str(_note_q)[:80]
                                    # Дедуп: не создаём заметку если похожая тема уже есть за 24ч
                                    _auto_note_skip = False
                                    try:
                                        from config import Session as _SN_sess
                                        from models import Note as _N_m, User as _UN_m
                                        import datetime as _dt_an
                                        _sn_s = _SN_sess()
                                        try:
                                            _sn_u = _sn_s.query(_UN_m).filter_by(telegram_id=user_id).first()
                                            if _sn_u:
                                                _cutoff_an = _dt_an.datetime.now(_dt_an.timezone.utc) - _dt_an.timedelta(hours=24)
                                                _kw_an = set(_note_title.lower().split()[:5])
                                                _recent_ns = _sn_s.query(_N_m).filter(
                                                    _N_m.user_id == _sn_u.id,
                                                    _N_m.created_at >= _cutoff_an,
                                                ).all()
                                                for _rn in _recent_ns:
                                                    _rn_kw = set((_rn.title or '').lower().split()[:5])
                                                    if len(_kw_an & _rn_kw) >= 2:
                                                        _auto_note_skip = True
                                                        logger.info('[AUTO_NOTE] dedup: similar note for %s', _note_title[:40])
                                                        break
                                        finally:
                                            _sn_s.close()
                                    except Exception as _dn_e:
                                        logger.debug('[AUTO_NOTE] dedup check: %s', _dn_e)
                                    if not _auto_note_skip:
                                        _note_content = _raw_result_str
                                        asyncio.ensure_future(
                                            _auto_save_note(
                                                content=_note_content,
                                                title=_note_title,
                                                user_id=user_id,
                                            )
                                        )
                                        _auto_saved_notes.append(_note_title)
                                        logger.info(f"[AUTO_NOTE] Saved research note: '{_note_title[:50]}'")
                                except Exception as _auto_ne:
                                    logger.warning(f"[AUTO_NOTE] Failed to save: {_auto_ne}")
                            # ─────────────────────────────────────────────────────────────────

                            # ── Промежуточное сообщение для ключевых действий ──
                            if _cb and _name in ('delegate_task', 'research_topic', 'run_agent_action',
                                                  'create_post', 'get_delegation_progress', 'add_task'):
                                try:
                                    _res_obj = _r.get('result', {})
                                    if isinstance(_res_obj, str):
                                        try: _res_obj = json.loads(_res_obj)
                                        except Exception: _res_obj = {}
                                    if _name == 'delegate_task':
                                        # delegate_task handler saves response via _save_ifd with __agent
                                        # Не дублируем через progress_callback
                                        _vis = None
                                    elif _name == 'research_topic':
                                        _q = _args.get('query', '') or _args.get('topic', '') or ''
                                        _vis = f"Исследую: {_q[:120]}" if _q else "Провожу исследование..."
                                    elif _name == 'create_post':
                                        _vis = "Создаю пост..."
                                    elif _name == 'get_delegation_progress':
                                        _vis = "Проверяю статус задач..."
                                    elif _name == 'add_task':
                                        _tsk = _args.get('title', '') or ''
                                        _vis = f"Записал: {_tsk[:60]}" if _tsk else "Создаю задачу..."
                                    else:
                                        _ag = _args.get('agent_name', '') or ''
                                        _vis = f"Запускаю агента {_ag}" if _ag else "Запускаю агента..."
                                    if _vis:
                                        await _cb(_vis, persist=False)
                                except TypeError:
                                    try:
                                        if _vis:
                                            await _cb(_vis)
                                    except Exception as _vcb:
                                        logger.debug("suppressed vis callback: %s", _vcb)
                                except Exception as _e:
                                    logger.debug("suppressed: %s", _e)
                        else:
                            _rc = json.dumps({"error": str(_r.get('error', ''))}, ensure_ascii=False)
                            try: get_learner().record_tool_result(user_id, _name, False)
                            except Exception as _lr: logger.debug("suppressed learner: %s", _lr)
                    except asyncio.TimeoutError:
                        logger.warning(f"[EXEC] {_name} timed out after {_tool_timeout}s — tool hung, skipping")
                        _r = {"success": False, "error": f"timeout_{_tool_timeout}s"}
                        _rc = json.dumps({"error": f"Инструмент не ответил за {_tool_timeout}с. Попробуй другой способ."}, ensure_ascii=False)
                        try: get_learner().record_tool_result(user_id, _name, False)
                        except Exception as _lr: logger.debug("suppressed learner: %s", _lr)
                    except Exception as _err:
                        logger.error(f"[EXEC] {_name} parallel crashed: {_err}\n{traceback.format_exc()}")
                        _r = {"success": False, "error": str(_err)}
                        _rc = json.dumps({"error": str(_err)}, ensure_ascii=False)
                        try: get_learner().record_tool_result(user_id, _name, False)
                        except Exception as _lr: logger.debug("suppressed learner: %s", _lr)
                    return _r, {"role": "tool", "tool_call_id": _tc['id'], "content": _rc}

                if _ready_calls:
                    if len(_ready_calls) == 1:
                        # Один инструмент — без gather (нет смысла)
                        _out = [await _exec_one(*_ready_calls[0])]
                    else:
                        # Несколько — параллельно (research_topic × 2 → в 2× быстрее)
                        logger.info(f"[PARALLEL] Executing {len(_ready_calls)} tools in parallel: "
                                    f"{[c[1] for c in _ready_calls]}")
                        _out = await asyncio.gather(
                            *[_exec_one(*c) for c in _ready_calls],
                            return_exceptions=True
                        )
                    for _item in _out:
                        if isinstance(_item, Exception):
                            logger.error(f"[PARALLEL] Gather error: {_item}")
                        else:
                            _r, _tool_msg = _item
                            all_execution_results.append(_r)
                            messages.append(_tool_msg)

                # Если в этой итерации был delegate_task — добавляем инструкцию продолжать
                # Но НЕ для вопросов — вопросы не требуют цепочки действий
                _had_delegate = any(c[1] == 'delegate_task' for c in _ready_calls)
                if _had_delegate and not _is_question_message(user_message):
                    messages.append({
                        "role": "system",
                        "content": (
                            "Ответ агента УЖЕ отображён пользователю — НЕ ПОВТОРЯЙ и НЕ ПЕРЕСКАЗЫВАЙ его. "
                            "Оцени результат в 1 предложении. Затем ПРОДОЛЖИ работу: "
                            "вызови следующий инструмент, делегируй шаг другому агенту, "
                            "или создай задачи по плану. НЕ заканчивай на тексте — ДЕЙСТВУЙ."
                        )
                    })

                # Продолжаем цикл — AI увидит результаты и решит
                # ответить текстом или вызвать ещё tools

            # Safety net: если вышли из цикла без return — генерируем ответ
            try:
                final_resp = await self.call_ai(
                    messages, use_tools=False, temperature=0.7, max_tokens=300,
                    api_timeout=API_TIMEOUT_NORMAL)
                final_text = final_resp['choices'][0]['message'].get('content') or ''
            except Exception as _safety_err:
                logger.warning(f"[AGENT] Safety-net AI call failed: {_safety_err}")
                final_text = ''
            return await self._finalize_response(
                final_text, user_message, user_id, all_execution_results)

        except Exception as e:
            logger.error(f"[AGENT] Error: {e}\n{traceback.format_exc()}")
            # Если инструменты уже отработали — формируем ответ из результатов вместо ошибки
            if all_execution_results and any(r.get('success') for r in all_execution_results):
                logger.info("[AGENT] Tools succeeded before crash — building response from results")
                return await self._finalize_response(
                    '', user_message, user_id, all_execution_results)
            if user_lang == 'en':
                error_responses = [
                    "Something went wrong. Try rephrasing your request.",
                    "Technical error. Please try again.",
                    "Oops, a glitch. Say the same thing differently.",
                    "Technical issues. Let's try a different approach.",
                    "Something broke. Please rephrase.",
                ]
            else:
                error_responses = [
                    "Сбой при обработке запроса — AI-сервер вернул ошибку. Попробуй переформулировать или написать короче, это часто помогает.",
                    "Техническая ошибка на стороне AI-движка. Твой запрос сохранён — напиши ещё раз, я попробую обработать его другим способом.",
                    "Потерял ответ из-за таймаута сервера. Если запрос был длинный — попробуй разбить на части. Напиши снова.",
                    "Внутренний сбой при генерации ответа. Это не проблема с твоим запросом — повтори его, я попробую ещё раз.",
                    "AI-сервер не отвечает, возможно перегрузка. Подожди 10-15 секунд и повтори запрос.",
                ]
            return random.choice(error_responses)

    # ===== КОГНИТИВНАЯ ФИНАЛИЗАЦИЯ =====

    async def _finalize_response(self, content, user_message, user_id, execution_results):
        """Clean → validate → save → return.
        
        Единая точка выхода: чистка тех. деталей, когнитивная валидация
        (убирает шаблонные начала, markdown, автоответчик, списки),
        сохранение в историю и обучение.
        """
        from .utils import clean_technical_details
        from .cognitive import CognitiveEngine
        from i18n import get_user_lang

        final = clean_technical_details(content or '').strip()

        # ── Пост-обработка: удаляем упоминания неподключённых сервисов ──
        if final:
            import re as _re_svc_fin
            # 'crm' исключён — общий термин, не конкретный сервис
            _BANNED_SVCS_FIN = ('linkedin', 'calendly', 'apollo\.io', 'sales\.navigator', 'hubspot', 'zoho', 'pipedrive')
            for _bs_f in _BANNED_SVCS_FIN:
                final = _re_svc_fin.sub(
                    rf'[^.!?\n]*\b{_bs_f}\b[^.!?\n]*[.!?]?\s*',
                    '', final, flags=_re_svc_fin.IGNORECASE
                )
            # Зачищаем осиротевшие фрагменты: ТОЛЬКО короткие строки с висящим ':' (≤30 симв.)
            # Длинные строки типа "Найденные контакты:" / "Следующий шаг:" — не трогаем
            final = _re_svc_fin.sub(r'(?m)^[^\n.!?]{1,30}:\s*$', '', final)
            final = _re_svc_fin.sub(r'\n{3,}', '\n\n', final)
            final = final.strip()

        if not final:
            _lang = get_user_lang(user_id)
            
            # Проверяем — может это стратегическое указание? (изменение целей, аудитории, интеграций)
            _user_msg_lower = (user_message or '').lower()
            _strategy_keywords = ['ищем', 'search for', 'ищи', 'искат', 'вместо', 'instead of', 'целевая', 'target', 'аудитория', 'audience', 'стратеги', 'strategy', 'целей', 'goals', 'привлеч', 'поищем', 'поиск', 'подбер']
            _is_strategy_cmd = any(kw in _user_msg_lower for kw in _strategy_keywords) and any(verb in _user_msg_lower for verb in ['можем', 'можно', 'может', 'should', 'надо', 'нужно', 'need', 'давай'])

            if _is_strategy_cmd:
                # AI вернул пустой ответ на стратегическое указание —
                # делаем безопасный retry без зависимости от внешних переменных.
                try:
                    _retry_msgs = [
                        {
                            'role': 'system',
                            'content': (
                                'Ты ассистент по достижению целей. Если запрос стратегический, '
                                'не обещай, а сделай один реальный шаг через инструмент.'
                            ),
                        },
                        {
                            'role': 'user',
                            'content': (
                                f'Пользователь просит: "{user_message}". '
                                f'Не обещай — ДЕЙСТВУЙ прямо сейчас. '
                                f'Вызови web_search или find_relevant_contacts_for_task или research_topic.'
                            ),
                        },
                    ]
                    _retry_resp = await self.call_ai(
                        _retry_msgs, use_tools=True, max_tokens=900,
                        api_timeout=API_TIMEOUT_NORMAL)
                    _rc = _retry_resp['choices'][0]['message']
                    _retry_tool_calls = _rc.get('tool_calls') or []
                    if _retry_tool_calls:
                        _retry_msgs.append(_rc)
                        _retry_exec_results = []
                        for _tc in _retry_tool_calls[:3]:
                            _func = _tc.get('function', {})
                            _tname = _func.get('name', '')
                            try:
                                _targs = json.loads(_func.get('arguments', '{}'))
                            except Exception:
                                _targs = {}
                            if not isinstance(_targs, dict):
                                _targs = {}
                            _res_arr = await asyncio.wait_for(
                                self.execute_actions(
                                    [{"tool": _tname, "params": _targs, "reason": "strategy_retry"}],
                                    user_id,
                                    session=None,
                                    user_message=user_message,
                                ),
                                timeout=60,
                            )
                            _res = _res_arr[0] if _res_arr else {"success": False, "error": "no result", "tool": _tname}
                            _retry_exec_results.append(_res)
                            if _res.get('success'):
                                _raw_res_r = _res.get('result', '')
                                if isinstance(_raw_res_r, dict) and '_human_summary' in _raw_res_r:
                                    _tc_content = _raw_res_r['_human_summary']
                                else:
                                    _tc_content = json.dumps(_raw_res_r, ensure_ascii=False, default=str)
                            else:
                                _tc_content = json.dumps({"error": str(_res.get('error', ''))}, ensure_ascii=False)
                            _retry_msgs.append({
                                'role': 'tool',
                                'tool_call_id': _tc.get('id'),
                                'content': _tc_content[:3000],
                            })
                        if _retry_exec_results:
                            execution_results.extend(_retry_exec_results)
                        _final_resp = await self.call_ai(
                            _retry_msgs, use_tools=False, max_tokens=700,
                            api_timeout=API_TIMEOUT_NORMAL)
                        final = (_final_resp['choices'][0]['message'].get('content', '') or '').strip()
                    elif _rc.get('content', '').strip():
                        final = _rc['content'].strip()
                except Exception as _retry_err:
                    logger.warning(f"[STRATEGY_RETRY] failed: {_retry_err}")
                # Если strategy retry не помог — AI сам сформулирует
                pass
            # AI не вернул текст — делаем честный AI-вызов с контекстом
            if not final:
                try:
                    _fb_msgs = [
                        {'role': 'system', 'content': 'Пользователь написал тебе сообщение, но ты не смог понять контекст. Ответь честно и по делу. Если не знаешь — скажи что не понял и попроси уточнить. НЕ пиши "Готово" если ничего не сделал.'},
                        {'role': 'user', 'content': user_message or 'Привет'},
                    ]
                    _fallback_resp = await self.call_ai(
                        _fb_msgs, use_tools=False, max_tokens=300,
                        api_timeout=API_TIMEOUT_NORMAL)
                    final = (_fallback_resp['choices'][0]['message'].get('content', '') or '').strip()
                except Exception as _fb_err:
                    logger.warning(f"[FALLBACK] AI call failed: {_fb_err}")
                    final = 'Не удалось обработать запрос. Попробуй переформулировать.' if _lang != 'en' else 'Could not process your request. Please try rephrasing.'

        # Биллинг кастомного агента
        try:
            from .user_agents import get_user_active_agent, bill_agent_message
            active_agent_id = get_user_active_agent(user_id)
            if active_agent_id:
                bill_result = bill_agent_message(user_id, active_agent_id)
                if not bill_result['success'] and 'токенов' in bill_result.get('error', ''):
                    # Недостаточно токенов — сбрасываем агента и сообщаем
                    from .user_agents import set_user_active_agent
                    set_user_active_agent(user_id, None)
                    final = f"{bill_result['error']}\n\nВозвращаюсь в стандартный режим ASI Biont."
        except Exception as _be:
            logger.warning(f"[BILLING] agent billing error: {_be}")

        # Когнитивная валидация (quality gate)
        final, issues = CognitiveEngine.validate_response(final, user_message)
        if issues:
            logger.info(f"[COGNITIVE] Response fixed: {issues}")

        # Подсказка: если пользователь прямо просит сервис, которого нет — одно упоминание.
        # Если агент уже сам написал про это — не дублируем.
        try:
            _missing_hint = _build_missing_integration_hint(user_id, user_message or '', final or '')
            if _missing_hint and (user_message or '').strip():
                # Добавляем если пользователь явно упомянул сервис ИЛИ подразумевает задачу под него
                _msg_l = (user_message or '').lower()
                _msg_has_service = any(
                    kw in _msg_l
                    for rule in _INTEGRATION_REQUEST_RULES
                    for kw in rule['keywords']
                )
                # Семантические паттерны: задача без явного названия интеграции
                if not _msg_has_service:
                    _SEMANTIC_INTENTS = [
                        (['отследи посылк', 'где посылк', 'статус посылк', 'трекинг посылк', 'посылка не пришл', 'найди посылк'],
                         ['cdek', 'cdek_client', 'pochta', 'pochta_access']),
                        (['где судно', 'статус судна', 'отследи судн', 'морской груз', 'судно в порт', 'флот на карт'],
                         ['marinetraffic', 'marinetraffic_api']),
                        (['котировки нефт', 'цена нефт', 'данные биржи', 'цена акц', 'стоимость активов', 'курс нефт'],
                         ['alphavantage', 'alpha_vantage', 'finnhub', 'tinkoff']),
                        (['оптимизируй рекламу', 'рекламный бюджет', 'кампания директ', 'управляй объявлен'],
                         ['yandex_direct', 'yadirect', 'direct_token']),
                        (['воронка продаж', 'новые лиды', 'добавь лид', 'обнови сделк', 'статус сделк'],
                         ['amocrm', 'bitrix', 'hubspot']),
                        (['остатки на складе', 'обнови цены', 'карточка товара', 'статистика продаж маркет'],
                         ['wildberries', 'wb_token', 'ozon', 'moysklad']),
                        (['опубликуй пост', 'запости в телеграм', 'отправь в канал'],
                         ['telegram_bot_token', 'tg_bot']),
                    ]
                    try:
                        _snap = _get_active_agent_integration_snapshot(user_id)
                        _snap_keys = (_snap.get('keys_text') or '').lower()
                        _snap_caps = (_snap.get('caps_text') or '').lower()
                        for _intent_kws, _presence_kws in _SEMANTIC_INTENTS:
                            if any(kw in _msg_l for kw in _intent_kws):
                                if not any(p in _snap_keys or p in _snap_caps for p in _presence_kws):
                                    _msg_has_service = True
                                    break
                    except Exception:
                        pass
                if _msg_has_service:
                    final = f"{final}\n\n{_missing_hint}".strip()
        except Exception as _lh_err:
            logger.debug("[INTG HINT] proactive missing-integration hint skipped: %s", _lh_err)

        # Встраиваем картинку в ответ если generate_image отработал успешно
        import re as _re
        for _r in execution_results:
            if _r.get('tool') == 'generate_image' and _r.get('success'):
                _res_text = str(_r.get('result', ''))
                _url_match = _re.search(r'https?://\S+', _res_text)
                if _url_match:
                    _img_url = _url_match.group(0).rstrip(')')
                    # Добавляем только если URL ещё не вставлен в ответ
                    if _img_url not in final:
                        final = final + f'\n\n![изображение]({_img_url})'
                        logger.info(f"[IMAGE] Injected image markdown into response: {_img_url[:80]}")

        # Рефлексия для обучения
        tools_used = [r['tool'] for r in execution_results if r.get('success')]
        CognitiveEngine.reflect_on_response(user_message, final, tools_used)

        # Защита от слишком коротких / холостых ответов после tool calls
        # Если AI ответил "Готово!" после делегирования — просим AI синтезировать результаты
        # НО: если delegate_task использован и агент уже ответил в чате — не дублируем
        _had_agent_delegate = any(r.get('tool') == 'delegate_task' and 'уже ответил' in str(r.get('result', '')) for r in execution_results)
        _HOLLOW_ACKS = ('готово', 'выполнено', 'сделано', 'принял', 'понял', 'ок', 'ok', 'done', 'хорошо', 'записал')
        _final_lc = (final or '').strip().lower().rstrip('!. ')
        _is_hollow_ack = _final_lc in _HOLLOW_ACKS or any(_final_lc == h or _final_lc.startswith(h + ' ') or _final_lc.startswith(h + ',') for h in _HOLLOW_ACKS)
        # Синтезируем только если ответ действительно пустой/короткий, даже если начинается с ack-слова.
        # "Готово, нашёл 5 контактов: ..." (100+ симв.) — полезный ответ, заменять не надо.
        # "Готово ✓" (8 симв.) или "Готово, начинаю работу" (60 симв. без фактов) — нужен синтез.
        if tools_used and (len((final or '').strip()) < 40 or (_is_hollow_ack and len((final or '').strip()) < 150)) and not _had_agent_delegate:
            # Собираем краткие результаты инструментов для синтеза AI
            _synth_parts = []
            for _r in execution_results:
                if _r.get('success') and _r.get('result'):
                    _rtext = str(_r['result'])[:400]
                    _synth_parts.append(_rtext)
            if _synth_parts:
                _synth_data = "\n---\n".join(_synth_parts[:4])
                try:
                    _synth_msgs = [
                        {'role': 'system', 'content': 'Ты ассистент. Перескажи результаты действий для пользователя в 2-4 предложения. Пиши на русском, естественным языком. Не используй JSON, не повторяй названия инструментов. Предложи следующий шаг.'},
                        {'role': 'user', 'content': f'Запрос: {user_message}\n\nРезультаты:\n{_synth_data[:2000]}'},
                    ]
                    _synth_resp = await self.call_ai(
                        _synth_msgs, use_tools=False, max_tokens=400,
                        api_timeout=API_TIMEOUT_NORMAL)
                    _synth_text = (_synth_resp['choices'][0]['message'].get('content', '') or '').strip()
                    if _synth_text and len(_synth_text) > 20:
                        final = _synth_text
                        logger.info(f"[QUALITY] AI-synthesized terse response: {len(final)} chars")
                    else:
                        logger.warning("[QUALITY] Synthesis returned empty, keeping original")
                except Exception as _synth_err:
                    logger.warning(f"[QUALITY] Synthesis call failed: {_synth_err}")

        self._save_and_learn(user_message, user_id, execution_results, final)
        return final

    # ===== ОБУЧЕНИЕ И АДАПТАЦИЯ =====

    def _save_and_learn(self, user_message, user_id, execution_results, response):
        """Сохраняет в историю, обучается на результатах, обновляет паттерны."""
        
        # === Распознавание и сохранение глобальных правил vs целевых стратегий ===
        try:
            _msg_lower = (user_message or '').lower()
            
            # ── ГЛОБАЛЬНЫЕ ПРАВИЛА (покрывают все цели/инструменты) ──
            _global_rule_keywords = ['никогда', 'всегда', 'только', 'не используй', 'исключи', 'игнорируй', 
                                     'пропускай', 'избегай', 'never', 'always', 'don\'t use', 'exclude']
            _is_global_rule = any(kw in _msg_lower for kw in _global_rule_keywords)
            
            # ── Попытка сохранить как глобальное правило если нашли признаки ──
            if _is_global_rule:
                # Это выглядит как команда для всей системы а не для одной цели
                # Сохраняем как пользовательское правило
                session = None
                try:
                    from models import Session, User
                    session = Session()
                    try:
                        user = session.query(User).filter_by(telegram_id=user_id).first()
                    except Exception:
                        user = None
                    if user:
                        from .memory import store_encrypted_memory
                        # Получаем текущие правила
                        _current_mem = user.memory or '{}'
                        if _current_mem.startswith('{'):
                            import json as _j_mem
                            _mem_dict = _j_mem.loads(_current_mem)
                        else:
                            _mem_dict = {'notes': _current_mem}
                        
                        _existing_rules = _mem_dict.get('rules', [])
                        # Проверяем что это правило еще не сохранено
                        if user_message not in _existing_rules:
                            _existing_rules.append(user_message)
                            _mem_dict['rules'] = _existing_rules
                            
                            # Сохраняем обновленную память
                            import json as _j_final
                            user.memory = _j_final.dumps(_mem_dict, ensure_ascii=False)
                            session.commit()
                            logger.info(f"[GLOBAL RULE] Added rule for user {user_id}: {user_message[:80]}")
                except Exception as _gr_err:
                    logger.debug(f"[GLOBAL RULE] Failed to save as rule: {_gr_err}")
                finally:
                    if session is not None:
                        try:
                            session.close()
                        except Exception:
                            pass
            
            # ── ЦЕЛЕВЫЕ СТРАТЕГИИ (специфичные для текущих целей) ──
            _has_search_keywords = any(w in _msg_lower for w in ['ищем', 'ищи', 'искат', 'search', 'find', 'целевой', 'аудиторий', 'привлеч'])
            _has_not_keywords = any(w in _msg_lower for w in ['не ', 'не,', ' не', 'instead', 'вместо', 'except', 'а не'])
            # «не только X но и Y» — аудиторный инсайт: обновляем цели/кампании независимо от _is_global_rule
            _has_audience_expand = ('не только' in _msg_lower and ('но и' in _msg_lower or 'а также' in _msg_lower or 'плюс' in _msg_lower))

            if _has_search_keywords and (_has_not_keywords or _has_audience_expand):
                # Похоже на целевое указание типа "ищем [не_это] [а_то]" для текущего проекта
                # Например: "ищем не тестировщиков а бизнесменов"
                from models import Session, Goal, User
                session = Session()
                try:
                    _u_sl = session.query(User).filter_by(telegram_id=user_id).first()
                    if not _u_sl:
                        _db_user_id = None
                    else:
                        _db_user_id = _u_sl.id
                    # Ищем активные цели на поиск/привлечение контактов
                    active_goals = session.query(Goal).filter(
                        Goal.user_id == _db_user_id,
                        Goal.status == 'active'
                    ).all() if _db_user_id else []
                    for goal in active_goals:
                        _gtitle_lower = (goal.title or '').lower()
                        _gdesc_lower = (goal.description or '').lower()
                        # Если цель про привлечение/поиск — обновляем её описание
                        if any(w in _gtitle_lower or w in _gdesc_lower for w in ['привлеч', 'поиск', 'найти', 'search', 'find']):
                            # Добавляем указание пользователя в описание цели
                            _old_desc = goal.description or ''
                            import datetime as _dt_strat
                            _ts = _dt_strat.datetime.utcnow().strftime('%d.%m.%Y - %H:%M')
                            _new_desc = f"{_old_desc}\n\n[СТРАТЕГИЯ {_ts}] {user_message}".strip()
                            goal.description = _new_desc
                            goal.updated_at = datetime.utcnow()
                            logger.info(f"[STRATEGY UPDATE] Goal {goal.id} updated with user strategy: {user_message[:80]}")
                            # Обновляем target_audience активных кампаний этого юзера
                            try:
                                from models import EmailCampaign as _EC_strat, DelegationCampaign as _DC_strat
                                _strat_note = f"[СТРАТЕГИЯ {_ts}] {user_message}"
                                for _ec in session.query(_EC_strat).filter(
                                    _EC_strat.user_id == _db_user_id,
                                    _EC_strat.status == 'active'
                                ).all():
                                    _ec.target_audience = ((_ec.target_audience or '') + '\n' + _strat_note).strip()
                                    logger.info(f"[STRATEGY] Updated EmailCampaign {_ec.id} target_audience")
                                for _dc in session.query(_DC_strat).filter(
                                    _DC_strat.user_id == _db_user_id,
                                    _DC_strat.status == 'active'
                                ).all():
                                    _dc.target_audience = ((_dc.target_audience or '') + '\n' + _strat_note).strip()
                                    logger.info(f"[STRATEGY] Updated DelegationCampaign {_dc.id} target_audience")
                            except Exception as _ec_err:
                                logger.debug(f"[STRATEGY] Campaign update failed: {_ec_err}")
                    session.commit()
                except Exception as _gu_err:
                    logger.debug(f"[STRATEGY UPDATE] Failed to update goals: {_gu_err}")
                    session.rollback()
                finally:
                    session.close()
        except Exception as _strat_err:
            logger.debug(f"[STRATEGY] Strategy detection error: {_strat_err}")
        
        # === Запись в execution_history ===
        tools_used = [r['tool'] for r in execution_results if r.get('success')]
        entry = {
            'message': user_message,
            'user_id': user_id,
            'results': execution_results,
            'tools_used': tools_used,
            'response': response,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'success': all(r.get('success', False) for r in execution_results)
                       if execution_results else True
        }
        self.execution_history.append(entry)
        if len(self.execution_history) > 30:
            self.execution_history = self.execution_history[-20:]

        # === Ответ в историю диалога (с краткой аннотацией вызванных тулов) ===
        from .conversation_history import save_message_to_history
        _history_response = response
        if tools_used:
            # Строим компактный лог: имя тула + ключевые аргументы для важных инструментов
            _tool_parts = []
            for _r in execution_results:
                if not _r.get('success'):
                    continue
                _tname = _r.get('tool', '')
                _tres = _r.get('result', {})
                if isinstance(_tres, str):
                    try:
                        import json as _j; _tres = _j.loads(_tres)
                    except Exception:
                        _tres = {}
                if _tname == 'add_task':
                    _title = _tres.get('title') or (_tres.get('task', {}) or {}).get('title', '')
                    _tool_parts.append(f"add_task({repr(_title[:40])})" if _title else "add_task")
                elif _tname == 'delegate_task':
                    _title = _tres.get('title', '')
                    _exec = _tres.get('executor', '') or _tres.get('executor_username', '')
                    _tool_parts.append(f"delegate_task({repr(_title[:30])} → {_exec})" if _title else "delegate_task")
                elif _tname == 'send_email':
                    _to = _tres.get('to', '') or _tres.get('recipient', '')
                    _tool_parts.append(f"send_email(→{_to[:30]})" if _to else "send_email")
                elif _tname == 'send_message_to_user':
                    _to = _tres.get('to', '') or _tres.get('username', '')
                    _tool_parts.append(f"send_message(→{_to[:30]})" if _to else "send_message_to_user")
                elif _tname in ('research_topic', 'web_search'):
                    _tool_parts.append(_tname)
                else:
                    _tool_parts.append(_tname)
            if _tool_parts:
                _tool_annotation = f"[Действия: {', '.join(_tool_parts)}]\n"
                _history_response = _tool_annotation + response
        save_message_to_history(user_id, "assistant", _history_response)

        # === Обучение на успешных паттернах ===
        if entry['success'] and tools_used:
            self._learn_from_success(user_message, user_id, tools_used)

        # === Контекстная память ===
        if tools_used:
            self.context_memory.append({
                'user_id': user_id,
                'tools': tools_used,
                'message_hint': user_message[:50],
                'timestamp': datetime.now(timezone.utc).isoformat()
            })
            if len(self.context_memory) > 100:
                self.context_memory = self.context_memory[-100:]

        # === Семантическая память (Pinecone) — fire-and-forget ===
        try:
            from .cognitive import CognitiveEngine as _VecCE
            _vec_emotion = _VecCE.detect_emotion(user_message)
            _vec_intent = _VecCE.classify_intent(user_message)
            asyncio.get_running_loop().create_task(
                store_conversation_turn(
                    user_id=user_id,
                    user_message=user_message,
                    bot_response=response,
                    emotion=_vec_emotion,
                    intent=_vec_intent
                )
            )
        except Exception as e:
            logger.warning(f"[VECTOR] Store failed: {e}")

        # === Self-learning feedback loop (fire-and-forget для скорости) ===
        async def _self_learn_bg():
            try:
                from .cognitive import CognitiveEngine
                emotion = CognitiveEngine.detect_emotion(user_message)
                intent = CognitiveEngine.classify_intent(user_message)
                _, issues = CognitiveEngine.validate_response(response, user_message)
                learner = get_learner()
                learner.record_turn(
                    user_id=user_id,
                    user_message=user_message,
                    response=response,
                    tools_used=tools_used,
                    emotion=emotion,
                    intent=intent,
                    issues=issues if issues else None
                )
            except Exception as e:
                logger.warning(f"[SELF-LEARN] Record failed: {e}")
        try:
            asyncio.get_running_loop().create_task(_self_learn_bg())
        except Exception as _e:
            logger.debug("suppressed: %s", _e)

        # === Долгосрочная память — только значимые факты ===
        # НЕ сохраняем CRUD-результаты (задачи/цели уже в БД — дубли вызывают галлюцинации)
        # Сохраняем ТОЛЬКО пользовательские предпочтения, НЕ результаты tool-вызовов
        try:
            pass  # Убрано: tool results в memory вызывали галлюцинации
            # Цели, задачи, контакты — всё в своих таблицах БД.
            # Бот читал "create_goal: Цель создана: X" из memory и думал что цель X существует.
        except Exception as e:
            logger.warning(f"[MEMORY] Save failed: {e}")

    # ===== ЕДИНЫЙ МОЗГ ДЛЯ СИСТЕМНЫХ СООБЩЕНИЙ =====

    async def generate_system_message(self, user_id, mode, instruction,
                                       extra_context=None, max_tokens=500,
                                       max_iterations=4):
        """Генерация системного сообщения (напоминание, проактивное, поздравление)
        через тот же мозг с tool calling, но без сохранения в историю диалога.

        Args:
            user_id: telegram ID пользователя
            mode: 'reminder' | 'proactive' | 'result_check'
            instruction: текст задания для AI (что сгенерировать)
            extra_context: дополнительный контекст (ситуация, красные флаги и т.д.)
            max_tokens: лимит токенов (короткие сообщения = меньше)
            max_iterations: макс. итераций tool calling (4 для баланса скорость/глубина)

        Returns:
            str — готовый текст сообщения
        """
        try:
            # Контекст — тот же что и для обычного чата (async)
            # Для proactive/anchor передаём mode чтобы не включать user_memory
            ctx = await self._build_context(user_id, mode=mode)
            if not ctx:
                from i18n import get_user_lang
                _lang = get_user_lang(user_id)
                return self._system_message_fallback(mode, instruction, lang=_lang)

            base_prompt = ctx['base_prompt']
            dynamic_context = ctx.get('dynamic_context', '')
            sub_tier = ctx['sub_tier']
            user_lang = ctx.get('user_lang', 'ru')

            # Добавляем режим в системный промпт (bilingual)
            # Mode instructions — only mode-specific logic, style rules inherited from base prompt
            _DATA_VERIFY_EN = (
                "\nDATA RULE: MEMORY section = background only. Cite ONLY data from tools as current. "
                "Empty list_tasks = no tasks. Empty list_goals = no goals."
            )
            _NO_HALLUCINATE_RU = (
                "\nФОРМАТ: сплошной текст, 2–3 абзаца через одинарный перенос. "
                "Маркеры (•, -, *), нумерация, заголовки, двойные переносы строк разрушают читаемость — пиши сплошным текстом.\n"
                "Если задача требует сервис не из «Подключено у тебя» — скажи пользователю что подключить и зачем. "
                "Не обещай действий через неподключённый сервис."
            )
            _DATA_VERIFY_RU = (
                "\nПРАВИЛО ДАННЫХ: секция ПАМЯТЬ = фон. Актуальны ТОЛЬКО данные из инструментов. "
                "Пустой list_tasks = нет задач. Пустой list_goals = нет целей."
            )
            _PROACTIVE_CORE_EN = (
                "Use tools (list_tasks, list_goals, get_news_trends) for real data. "
                "For quick data — use get_news_trends. research_topic(depth='basic') is OK if goal needs analysis. Don't invent data.\n"
                "Do NOT auto-publish posts. Link: https://asibiont.com/dashboard\n"
                "GOAL FOCUS: pick highest-priority lowest-progress goal → use available agent/tool → "
                "EXECUTE action DIFFERENT from recent directives. Only suggest tools that exist in context.\n"
                "If directives repeat (research, find contacts) — SWITCH APPROACH (DMs, communities, partnerships).\n"
                "⛔ Do NOT ask 'want me to?', 'shall I?', 'need me to?' — DO IT. Proactive = you already decided to act.\n"
                "⛔ If integration is connected (Yandex.Metrika, AmoCRM etc.) — USE run_agent_action for data, don't invent numbers."
            )
            _PROACTIVE_CORE_RU = (
                "Используй инструменты (list_tasks, list_goals, get_news_trends) для реальных данных. "
                "Для быстрых данных — get_news_trends. research_topic(depth='basic') — допустим если цель требует анализа. Не выдумывай данные.\n"
                "НЕ публикуй посты автоматически. Ссылка: https://asibiont.com/dashboard\n"
                "ФОКУС НА ЦЕЛЬ: выбери цель с наибольшим приоритетом и наименьшим прогрессом → "
                "используй доступного агента/инструмент → ВЫПОЛНИ действие ОТЛИЧНОЕ от последних директив. "
                "Предлагай только инструменты из контекста.\n"
                "Если директивы повторяются (исследовать, найти контакты) — СМЕНИ ПОДХОД (DM, сообщества, партнёрства).\n"
                "⛔ НЕ СПРАШИВАЙ 'хочешь?', 'давай?', 'может помочь?', 'нужно ли?' — ДЕЛАЙ. Проактивное = ты УЖЕ решил действовать.\n"
                "⛔ Если подключена интеграция (Яндекс.Метрика, AmoCRM и т.д.) — ИСПОЛЬЗУЙ run_agent_action для получения данных, не выдумывай цифры."
            )

            if user_lang == 'en':
                mode_instructions = {
                    'reminder': (
                        "\n\n[MODE: REMINDER]\n"
                        "Task time arrived. HELP solve it, not just remind. "
                        "Need info → find via tools. Simple → remind briefly + ask status. No new tasks.\n"
                        "Start with task name or action verb, never 'Reminder about task'."
                    ),
                    'task_assist': (
                        "\n\n[MODE: TASK ASSIST]\n"
                        "Help solve the task — DO it, don't suggest. Use tools, give concrete result. No new tasks."
                    ),
                    'proactive': (
                        "\n\n[MODE: PROACTIVE MESSAGE]\n"
                        "Write like a regular chat reply — alive, direct, with character. "
                        "User must NOT feel it's a system message.\n"
                        + _PROACTIVE_CORE_EN + _DATA_VERIFY_EN
                    ),
                    'result_check': (
                        "\n\n[MODE: CONGRATULATION]\n"
                        "Task completed. React naturally — 1-2 sentences, max 200 chars. Never start with 'Congratulations!'"
                    ),
                    'anchor': (
                        "\n\n[MODE: ANCHOR ENGINE]\n"
                        "ANCHORS received. Worth interrupting? If not → return SKIP.\n"
                        "If yes → use tools on the topic. ONE topic per message. End with question/suggestion.\n"
                        + _PROACTIVE_CORE_EN + _DATA_VERIFY_EN
                    ),
                }
            else:
                mode_instructions = {
                    'reminder': (
                        "\n\n[РЕЖИМ: НАПОМИНАНИЕ]\n"
                        "Время задачи. ПОМОГИ решить, не просто напомни. "
                        "Нужна информация → найди через инструменты. Простая → напомни кратко + спроси статус. Без новых задач.\n"
                        "Начни с сути задачи или глагола, никогда с 'Напоминание о задаче'."
                        + _NO_HALLUCINATE_RU
                    ),
                    'task_assist': (
                        "\n\n[РЕЖИМ: ПОМОЩЬ С ЗАДАЧЕЙ]\n"
                        "Помоги решить — СДЕЛАЙ, не предлагай. Используй инструменты, дай конкретный результат. Без новых задач."
                    ),
                    'proactive': (
                        "\n\n[РЕЖИМ: ПРОАКТИВНОЕ СООБЩЕНИЕ]\n"
                        "Пиши как обычный ответ в чате — живо, прямо, с характером. "
                        "Человек НЕ ДОЛЖЕН чувствовать что это системное сообщение.\n"
                        + _PROACTIVE_CORE_RU + _DATA_VERIFY_RU + _NO_HALLUCINATE_RU
                    ),
                    'result_check': (
                        "\n\n[РЕЖИМ: ПОЗДРАВЛЕНИЕ]\n"
                        "Задача выполнена. Отреагируй живо — 1-2 предложения, максимум 200 символов. Не начинай с 'Поздравляю!'"
                    ),
                    'anchor': (
                        "\n\n[РЕЖИМ: ANCHOR ENGINE]\n"
                        "Получены ЯКОРЯ. Стоит ли отвлекать человека? Если нет → верни SKIP.\n"
                        "Если да → используй инструменты по теме. ОДНА тема на сообщение. Закончи вопросом/предложением.\n"
                        + _PROACTIVE_CORE_RU + _DATA_VERIFY_RU + _NO_HALLUCINATE_RU
                    ),
                }

            system_prompt = base_prompt + mode_instructions.get(mode, '')

            # Инжектируем личность кастомного агента (если активен)
            try:
                from .user_agents import get_user_active_agent, load_agent_personality, build_agent_system_prompt
                active_agent_id = get_user_active_agent(user_id)
                if active_agent_id:
                    agent_data = load_agent_personality(active_agent_id)
                    if agent_data:
                        self._active_agent_data[user_id] = agent_data  # нужно для run_agent_action в anchor/proactive режимах
                        system_prompt = build_agent_system_prompt(agent_data, system_prompt)
                        logger.info(f"[AGENT] Injected personality: {agent_data['name']} (id={active_agent_id})")
                        # Акцент на интеграцию агента в проактивных / якорных / reminder режимах
                        _svc = agent_data.get('service_label', '')
                        _has_script = bool(agent_data.get('python_code', '').strip())
                        if _svc and mode in ('proactive', 'anchor', 'reminder'):
                            if user_lang == 'en':
                                system_prompt += (
                                    f"\n\n[INTEGRATION FOCUS: {_svc}]\n"
                                    f"This agent is connected to {_svc}. "
                                    + ("Agent script is configured — real data will appear in [AGENT DATA]. " if _has_script else "API keys are set but script is not yet configured. ")
                                    + "TOPIC PRIORITY for this message:\n"
                                    f"1. Data and events from {_svc} (if script ran and returned data)\n"
                                    "2. User tasks / goals related to this integration's domain\n"
                                    "3. Generic tips or channel posts — only as absolute last resort\n"
                                    f"Do NOT push email campaigns or channel posts as the default — user has {_svc} for real-world actions."
                                )
                            else:
                                system_prompt += (
                                    f"\n\n[АКЦЕНТ НА ИНТЕГРАЦИЮ: {_svc}]\n"
                                    f"Этот агент подключён к {_svc}. "
                                    + ("Скрипт настроен — актуальные данные будут в секции [ДАННЫЕ ОТ АГЕНТА]. " if _has_script else "Ключи API есть, скрипт не настроен. ")
                                    + "ПРИОРИТЕТ ТЕМ для этого сообщения:\n"
                                    f"1. Данные и события из {_svc} (если скрипт отработал и вернул данные)\n"
                                    "2. Задачи / цели пользователя связанные с доменом этой интеграции\n"
                                    "3. Общие советы или посты в канал — только как крайний вариант\n"
                                    f"НЕ предлагай автоматом email-кампании или посты в канал — у пользователя есть {_svc} для реальных действий."
                                )
                    else:
                        # Агент удалён/деактивирован — сбрасываем
                        from .user_agents import set_user_active_agent
                        set_user_active_agent(user_id, None)
            except Exception as _ae:
                logger.warning(f"[AGENT] personality inject error: {_ae}")

            # Собираем messages — БЕЗ истории диалога (это системное сообщение)
            messages = [{"role": "system", "content": system_prompt}]
            if dynamic_context:
                messages.append({"role": "system", "content": dynamic_context})

            # Если есть extra_context (ситуация, красные флаги) — добавляем
            if extra_context:
                ctx_label = "[SITUATION CONTEXT]" if user_lang == 'en' else "[КОНТЕКСТ СИТУАЦИИ]"
                messages.append({
                    "role": "user",
                    "content": f"{ctx_label}\n{extra_context}"
                })

            messages.append({"role": "user", "content": instruction})

            # Определяем какие инструменты ИСКЛЮЧИТЬ по режиму
            # update_profile ЗАПРЕЩЁН в любом не-chat режиме: AI не должен обновлять профиль
            # на основе контекста якорей (может случайно скопировать данные другого пользователя)
            # update_profile ЗАПРЕЩЁН в не-chat режимах, save_user_rule — разрешён везде (пользователь сам просит)
            _UNSAFE_PROFILE = {'update_profile'}
            exclude_tools = set()
            if mode == 'reminder':
                exclude_tools = {'add_task', 'create_goal', 'delegate_task'} | _UNSAFE_PROFILE
            elif mode == 'task_assist':
                exclude_tools = {'add_task', 'create_goal', 'delegate_task'} | _UNSAFE_PROFILE
            elif mode == 'result_check':
                exclude_tools = {'add_task', 'create_goal', 'delegate_task',
                                 'edit_task', 'reschedule_task'} | _UNSAFE_PROFILE
            elif mode == 'proactive':
                exclude_tools = {'delegate_task'} | _UNSAFE_PROFILE
            elif mode == 'anchor':
                exclude_tools = {'add_task', 'create_goal', 'delegate_task'} | _UNSAFE_PROFILE

            # ===== Tool calling loop (облегчённый) =====
            all_execution_results = []
            seen_tools = set()
            _seen_research_kws_sys = []  # Fuzzy dedup for research/web_search

            # Для anchor/proactive — первая итерация ОБЯЗАТЕЛЬНО вызывает инструменты
            # чтобы AI не выдумывал данные, а получал реальные
            force_tools_modes = {'anchor', 'proactive'}

            for iteration in range(max_iterations):
                # Первая итерация для anchor/proactive = required (заставляем вызвать инструмент)
                # Остальные = auto (AI решает сам)
                if iteration == 0 and mode in force_tools_modes:
                    current_tool_choice = "required"
                else:
                    current_tool_choice = "auto"

                response = await self.call_ai(
                    messages, use_tools=True, subscription_tier=sub_tier,
                    tool_choice=current_tool_choice, max_tokens=max_tokens,
                    exclude_tools=list(exclude_tools))

                msg = response['choices'][0]['message']
                content = msg.get('content', '')
                tool_calls = msg.get('tool_calls', [])

                if not tool_calls:
                    # AI ответил текстом → готово
                    from .utils import clean_technical_details
                    final = clean_technical_details(content).strip()
                    if final:
                        return final
                    # Если clean_technical_details убрала всё (DSML), retry без tools
                    if content.strip():
                        logger.warning(f"[AGENT:SYSTEM] Content cleaned to empty, retrying without tools")
                        retry_resp = await self.call_ai(
                            messages, use_tools=False, max_tokens=max_tokens)
                        retry_content = retry_resp['choices'][0]['message'].get('content', '')
                        retry_clean = clean_technical_details(retry_content).strip()
                        if retry_clean:
                            return retry_clean
                    return self._system_message_fallback(mode, instruction, lang=user_lang)

                # AI вызвал tools
                messages.append(msg)

                # ── Pass 1: валидация (последовательно) ─────────────────────────
                _sys_ready = []  # (tc_item, name, args, reason)
                for tc_item in tool_calls:
                    func = tc_item.get('function', {})
                    name = func.get('name', '')
                    try:
                        args = json.loads(func.get('arguments', '{}'))
                    except Exception:
                        args = {}
                    if not isinstance(args, dict):
                        logger.warning(f"[AGENT:SYSTEM] {name}: arguments is {type(args).__name__}, reset")
                        args = {}

                    # Dedup
                    dedup_key = f"{name}:{json.dumps(args, sort_keys=True)}"
                    if dedup_key in seen_tools:
                        messages.append({"role": "tool", "tool_call_id": tc_item['id'],
                            "content": '{"status": "skipped: duplicate"}'})
                        continue
                    seen_tools.add(dedup_key)

                    # Fuzzy dedup for research/web_search — block only near-identical (>70%)
                    if name in ('research_topic', 'web_search'):
                        _q_raw_s = (args.get('query') or args.get('topic') or args.get('prompt') or '').lower()
                        _q_kws_s = set(w for w in _q_raw_s.split() if len(w) > 2)
                        if _q_kws_s:
                            _fuzzy_dup = False
                            for _prev_kws_s in _seen_research_kws_sys:
                                _overlap_s = len(_q_kws_s & _prev_kws_s) / max(len(_q_kws_s | _prev_kws_s), 1)
                                if _overlap_s > 0.7:
                                    _fuzzy_dup = True
                                    break
                            if _fuzzy_dup:
                                messages.append({"role": "tool", "tool_call_id": tc_item['id'],
                                    "content": '{"status": "skipped: nearly identical query already executed. What NEW angle do you need?"}'})
                                continue
                            _seen_research_kws_sys.append(_q_kws_s)

                    # Blocked
                    if name in exclude_tools:
                        messages.append({"role": "tool", "tool_call_id": tc_item['id'],
                            "content": f'{{"status": "blocked: {name} not in {mode}"}}'})
                        continue

                    _sys_ready.append((tc_item, name, args, f"system:{mode} iter {iteration+1}"))

                # ── Pass 2: выполняем ПАРАЛЛЕЛЬНО ────────────────────────────────
                _SYS_TOOL_TIMEOUT_MAP = {
                    'delegate_task': 120, 'run_agent_action': 120,
                    'research_topic': 90, 'web_search': 45,
                    'send_outreach_email': 45, 'send_follow_up_email': 45,
                    'check_emails': 45, 'generate_image': 90,
                    'publish_to_telegram': 45, 'publish_to_discord': 45,
                    'create_post': 60,
                }
                async def _sys_exec_one(_tc, _name, _args, _reason):
                    _sys_tto = _SYS_TOOL_TIMEOUT_MAP.get(_name, 60)
                    try:
                        _results = await asyncio.wait_for(
                            self.execute_actions(
                                [{"tool": _name, "params": _args, "reason": _reason}],
                                user_id, session=None, user_message=instruction),
                            timeout=_sys_tto,
                        )
                        _r = _results[0] if _results else {"success": False, "error": "no result"}
                        if _r.get('success'):
                            _raw_res_s = _r['result']
                            if isinstance(_raw_res_s, dict) and '_human_summary' in _raw_res_s:
                                _rc = _raw_res_s['_human_summary'][:1500]
                            else:
                                _rc = json.dumps(_raw_res_s, ensure_ascii=False, default=str)[:1500]
                        else:
                            _rc = json.dumps({"error": str(_r.get('error', ''))}, ensure_ascii=False)
                    except asyncio.TimeoutError:
                        logger.warning(f"[AGENT:SYSTEM] {_name} timed out after {_sys_tto}s")
                        _r = {"success": False, "error": f"timeout_{_sys_tto}s"}
                        _rc = json.dumps({"error": f"Инструмент не ответил за {_sys_tto}с."}, ensure_ascii=False)
                    except Exception as _err:
                        logger.error(f"[AGENT:SYSTEM] {_name} parallel failed: {_err}\n{traceback.format_exc()}")
                        _r = {"success": False, "error": str(_err)}
                        _rc = json.dumps({"error": str(_err)}, ensure_ascii=False)
                    return _r, {"role": "tool", "tool_call_id": _tc['id'], "content": _rc}

                if _sys_ready:
                    if len(_sys_ready) == 1:
                        _sys_out = [await _sys_exec_one(*_sys_ready[0])]
                    else:
                        logger.info(f"[PARALLEL:SYSTEM] {len(_sys_ready)} tools: {[c[1] for c in _sys_ready]}")
                        _sys_out = await asyncio.gather(
                            *[_sys_exec_one(*c) for c in _sys_ready],
                            return_exceptions=True
                        )
                    for _item in _sys_out:
                        if isinstance(_item, Exception):
                            logger.error(f"[PARALLEL:SYSTEM] Gather error: {_item}")
                        else:
                            _r, _tool_msg = _item
                            all_execution_results.append(_r)
                            messages.append(_tool_msg)

            # Финальный вызов без tools после исчерпания итераций
            final_resp = await self.call_ai(
                messages, use_tools=False, max_tokens=max_tokens)
            final_text = final_resp['choices'][0]['message'].get('content', '')
            from .utils import clean_technical_details
            return clean_technical_details(final_text).strip() or self._system_message_fallback(mode, instruction, lang=user_lang)

        except Exception as e:
            logger.error(f"[AGENT:SYSTEM] Error in {mode}: {e}\n{traceback.format_exc()}")
            from i18n import get_user_lang
            _lang = get_user_lang(user_id)
            return self._system_message_fallback(mode, instruction, lang=_lang)

    def _system_message_fallback(self, mode, instruction, lang='ru'):
        """Fallback text when AI is unavailable."""
        if mode == 'reminder':
            import re
            match = re.search(r"[«\"](.+?)[»\"]", instruction)
            if lang == 'en':
                task_name = match.group(1) if match else "task"
                return (f"Time for task \"{task_name}\" has come. "
                        f"How's it going — done, in progress, or need to reschedule? "
                        f"I can help if needed.")
            else:
                task_name = match.group(1) if match else "задача"
                return (f"Время задачи «{task_name}» пришло. "
                        f"Расскажи, как продвигается — сделал, в процессе или нужно перенести? "
                        f"Если нужна помощь, могу подключиться.")
        elif mode == 'result_check':
            return "Great, task completed!" if lang == 'en' else "Отлично, задача выполнена!"
        elif mode == 'anchor':
            # Для anchor-режима пытаемся извлечь задачу из instruction
            import re
            match = re.search(r'[«"](.+?)[»"]', instruction)
            if match:
                task_name = match.group(1)
                if lang == 'en':
                    return f"Time for \"{task_name}\" — done, in progress, or reschedule?"
                else:
                    return f"Пора: «{task_name}» — готово, в процессе или перенести?"
            return None
        elif mode == 'proactive':
            return None
        else:
            return None

    def _learn_from_success(self, message, user_id, tools_used):
        """Обучение на успешных паттернах.
        
        Запоминает какие tools работали для каких типов запросов.
        Позволяет в будущем быстрее определять правильную стратегию.
        """
        # Определяем intent по tools
        intent = '_'.join(sorted(set(tools_used)))
        pattern_key = f"{user_id}:{intent}"
        
        if pattern_key not in self.success_patterns:
            self.success_patterns[pattern_key] = []
        
        self.success_patterns[pattern_key].append({
            'message': message[:100],
            'tools': tools_used,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
        
        # Ограничиваем размер
        if len(self.success_patterns[pattern_key]) > 10:
            self.success_patterns[pattern_key] = self.success_patterns[pattern_key][-10:]
        
        logger.info(f"[LEARN] Pattern '{intent}' for user {user_id}, "
                     f"total patterns: {len(self.success_patterns)}")

    def get_similar_patterns(self, user_id, tools_hint=None):
        """Получить похожие успешные паттерны для пользователя."""
        results = []
        prefix = f"{user_id}:"
        for key, patterns in self.success_patterns.items():
            if key.startswith(prefix):
                results.extend(patterns)
        return sorted(results, key=lambda x: x.get('timestamp', ''), reverse=True)[:5]

    def adapt_to_user(self, user_id, preference_key, value):
        """Адаптация под предпочтения пользователя.
        
        Пример: adapt_to_user(123, 'response_style', 'brief')
        """
        if user_id not in self.user_preferences:
            self.user_preferences[user_id] = {}
        self.user_preferences[user_id][preference_key] = value
        logger.info(f"[ADAPT] User {user_id}: {preference_key}={value}")

    def get_user_preference(self, user_id, preference_key, default=None):
        """Получить предпочтение пользователя."""
        return self.user_preferences.get(user_id, {}).get(preference_key, default)


# ===== ГЛОБАЛЬНЫЕ =====

_autonomous_agent = None


def get_autonomous_agent():
    """Глобальный экземпляр агента."""
    global _autonomous_agent
    if _autonomous_agent is None:
        _autonomous_agent = HybridAutonomousAgent()
    return _autonomous_agent


# ═══════════════════════════════════════════════════════════════════════════════
# OFFICE DIRECTOR — ASI координирует агентов прямо в чате
# ═══════════════════════════════════════════════════════════════════════════════

def _has_explicit_mention(message: str) -> bool:
    """True если сообщение начинается с @Агент или 'ИмяАгента, ...'."""
    m = (message or '').strip()
    if not m:
        return False
    if re.match(r'@\w+\b', m):
        return True
    # "Кристина, ..." — обращение по имени через запятую (типичный русский паттерн)
    if re.match(r'[А-ЯЁа-яё]{3,}\s*,', m):
        return True
    return False


def _is_question_message(msg: str) -> bool:
    """True если сообщение — вопрос, а не запрос на действие."""
    m = (msg or '').strip().lower()
    if not m:
        return False
    if '?' in m:
        return True
    _q_starts = (
        'есть ', 'есть ли ', 'что ', 'как ', 'какой ', 'какая ', 'какие ', 'какое ',
        'сколько ', 'когда ', 'где ', 'зачем ', 'почему ', 'кто ', 'чем ', 'куда ',
        'расскажи ', 'покажи ', 'скажи ', 'подскажи ',
        'what ', 'how ', 'when ', 'where ', 'who ', 'why ', 'which ', 'is there ',
    )
    # Проверяем оригинальное сообщение
    if any(m.startswith(s) for s in _q_starts):
        return True
    # Убираем обращение к агенту: "Кристина, ..." → "..."
    m2 = re.sub(r'^@?[а-яёa-z]+[\s,]+', '', m).strip()
    if m2 and any(m2.startswith(s) for s in _q_starts):
        return True
    return False


async def _quick_ai_call_raw(messages: list, max_tokens: int = 250, _caller: str = '', temperature: float = 0.7) -> str:
    """Прямой вызов DeepSeek без tool calling — для фоновых задач (координатор, инсайты).
    Использует отдельную сессию (_QUICK_AI_SESSION) чтобы не блокировать чат пользователей."""
    global _QUICK_AI_SESSION
    _max_attempts = 2
    _timeouts = [25, 45]
    for _att in range(_max_attempts):
      try:
        if os.getenv('PYTEST_CURRENT_TEST'):
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60, connect=10)) as _tmp:
                async with _tmp.post(
                        "https://api.deepseek.com/chat/completions",
                        headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
                        json={"model": DEEPSEEK_MODEL, "messages": messages, "max_tokens": max_tokens, "temperature": temperature},
                        timeout=aiohttp.ClientTimeout(total=_timeouts[min(_att, len(_timeouts)-1)]),
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            _usage = data.get('usage', {})
                            logger.info(f"[DEEPSEEK] {_caller or 'quick_ai'}: prompt={_usage.get('prompt_tokens',0)} compl={_usage.get('completion_tokens',0)}")
                            return data["choices"][0]["message"]["content"].strip()
                        if resp.status >= 500 and _att < _max_attempts - 1:
                            logger.warning(f"[quick_ai] Server {resp.status}, retry {_att+1}")
                            await asyncio.sleep(2)
                            continue
        else:
            _sess = await _get_quick_ai_session()
            async with _sess.post(
                    "https://api.deepseek.com/chat/completions",
                    headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
                    json={"model": DEEPSEEK_MODEL, "messages": messages, "max_tokens": max_tokens, "temperature": temperature},
                    timeout=aiohttp.ClientTimeout(total=_timeouts[min(_att, len(_timeouts)-1)]),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        _usage = data.get('usage', {})
                        logger.info(f"[DEEPSEEK] {_caller or 'quick_ai'}: prompt={_usage.get('prompt_tokens',0)} compl={_usage.get('completion_tokens',0)}")
                        return data["choices"][0]["message"]["content"].strip()
                    if resp.status >= 500 and _att < _max_attempts - 1:
                        logger.warning(f"[quick_ai] Server {resp.status}, retry {_att+1}")
                        await asyncio.sleep(2)
                        continue
        break  # non-5xx error, don't retry
      except (asyncio.TimeoutError, aiohttp.ClientError, aiohttp.ServerDisconnectedError, ConnectionResetError, OSError) as e:
        logger.warning(f"[quick_ai] {type(e).__name__} on attempt {_att+1}/{_max_attempts}: {e}")
        if not os.getenv('PYTEST_CURRENT_TEST'):
            # Закрываем фоновую сессию (не трогаем сессию чата)
            _sess_to_close = _QUICK_AI_SESSION
            _QUICK_AI_SESSION = None
            if _sess_to_close is not None and not _sess_to_close.closed:
                try:
                    await _sess_to_close.close()
                    await asyncio.sleep(0.1)
                except Exception:
                    pass
        if _att < _max_attempts - 1:
            await asyncio.sleep(2)
            continue
      except Exception as e:
        logger.warning(f"[quick_ai] Unexpected error: {type(e).__name__}: {e}")
        break
    return ""


def _strip_agent_html(text: str) -> str:
    """Убирает HTML-теги из ответа LLM: <a href='mailto:x'>x</a> → x"""
    if not text or '<' not in text:
        _t = text or ''
    else:
        _t = text
        # mailto anchors (закрытые): <a href="mailto:email">text</a> → email
        _t = re.sub(r'<a\s+href=["\']mailto:([^"\'\s>]+)["\'][^>]*>[^<]*</a>', r'\1', _t, flags=re.IGNORECASE | re.DOTALL)
        # mailto anchors (незакрытые): <a href="mailto:email">text → email
        _t = re.sub(r'<a\s+href=["\']mailto:([^"\'\s>]+)["\'][^>]*>[^<]*', r'\1', _t, flags=re.IGNORECASE | re.DOTALL)
        # обычные ссылки → текст внутри тега
        _t = re.sub(r'<a\s+[^>]*>(.*?)</a>', r'\1', _t, flags=re.IGNORECASE | re.DOTALL)
        # email в угловых скобках: <user@host.com> → user@host.com
        _t = re.sub(r'<([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})>', r'\1', _t)
        # все оставшиеся теги
        _t = re.sub(r'<[^>]+>', '', _t)
    # Артефакт разорванного mailto: @domain.com">email@domain.com → email@domain.com
    _t = re.sub(r'@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}["\']?\s*>\s*(?=[a-zA-Z0-9._%+-]+@)', '', _t)
    # Остаточные "> или '/> перед текстом
    _t = re.sub(r'["\']\s*/?>\s*(?=\S)', '', _t)
    # HTML entities
    _t = re.sub(r'&(?:nbsp|amp|lt|gt|quot|#\d+);?', ' ', _t)
    return _t


def _save_interaction_for_director(telegram_id: int, content: str, message_type: str = 'agent_msg') -> bool:
    """Сохраняет промежуточное сообщение агента/АСИ в Interaction чата.
    
    Возвращает True если сохранено, False если обнаружен дубль (поручение уже
    давалось в коротком окне — идентичный/семантически похожий текст директивы).
    """
    if not content or not content.strip():
        return False
    try:
        from models import Session as _Db, User as _User, Interaction as _Intr
        from datetime import timezone as _tz_dir, timedelta as _td_dir, datetime as _dt_dir
        _s = _Db()
        try:
            _u = _s.query(_User).filter_by(telegram_id=telegram_id).first()
            if not _u:
                logger.warning("[DIRECTOR] user not found for tg=%s", telegram_id)
                return False
            # ── Дедупликация директив ───────────────────────────────────────────────
            # Важно: директивы ASI теперь часто приходят как JSON с __agent,
            # поэтому выделяем текст и тип сообщения безопасно.
            _raw_text = content.strip()
            _agent_name = ''
            _is_json_wrapped = False
            try:
                _p = json.loads(content)
                if isinstance(_p, dict) and isinstance(_p.get('__agent'), dict):
                    _is_json_wrapped = True
                    _agent_name = str(_p.get('__agent', {}).get('name') or '').strip()
                    _txt = str(_p.get('text') or '').strip()
                    if _txt:
                        _raw_text = _txt
            except Exception:
                pass

            # Директивами считаем:
            # 1) plain-text agent_msg от директора;
            # 2) JSON agent_msg от ASI (director persona).
            _is_directive = False
            if message_type == 'agent_msg':
                if _is_json_wrapped:
                    _is_directive = (_agent_name.upper() == 'ASI')
                else:
                    _is_directive = True

            # Сравниваем первые 80 символов нормализованного текста
            _dedup_prefix = _raw_text[:80]
            if _is_directive:
                # Exact prefix dedup: 60 минут (расширено с 5 чтобы не повторять одинаковые поручения)
                _since = _dt_dir.now(_tz_dir.utc) - _td_dir(minutes=60)
                _recent_for_exact = (
                    _s.query(_Intr)
                    .filter(
                        _Intr.user_id == _u.id,
                        _Intr.message_type == 'agent_msg',
                        _Intr.created_at >= _since,
                    )
                    .order_by(_Intr.created_at.desc())
                    .limit(30)
                    .all()
                )
                for _rd in _recent_for_exact:
                    _rd_txt = str(_rd.content or '').strip()
                    _rd_agent = ''
                    try:
                        _p_rd = json.loads(_rd_txt)
                        if isinstance(_p_rd, dict) and isinstance(_p_rd.get('__agent'), dict):
                            _rd_agent = str(_p_rd.get('__agent', {}).get('name') or '').strip()
                            _rd_txt = str(_p_rd.get('text') or '').strip()
                    except Exception:
                        pass

                    # Не считаем отчёты Кристины/Марка директивами
                    if _rd_agent and _rd_agent.upper() != 'ASI':
                        continue

                    if _rd_txt and _rd_txt[:80].lower() == _dedup_prefix.lower():
                        logger.warning(
                            "[DIRECTOR] DEDUP: identical directive already sent %s ago for tg=%s, skipping: %s...",
                            str(_dt_dir.now(_tz_dir.utc) - _rd.created_at.replace(tzinfo=_tz_dir.utc))[:7],
                            telegram_id, _dedup_prefix[:40]
                        )
                        return False

                # Semantic dedup: если за последние 2ч уже была директива с теми же ключевыми фразами
                # (напр. "застрял на 44%, сменим тактику" → "застрял на 44%, давай сменим")
                _sem_since = _dt_dir.now(_tz_dir.utc) - _td_dir(hours=2)
                _content_lower = _raw_text.lower()
                _SEM_MARKERS = ('застрял', 'сменим тактику', 'смени тактику', 'давай сменим',
                                'прогресс.*не растёт', 'не двигает нас вперёд')
                import re as _re_sem_dd
                _has_sem_marker = any(_re_sem_dd.search(m, _content_lower) for m in _SEM_MARKERS)
                if _has_sem_marker:
                    _recent_directives = (
                        _s.query(_Intr)
                        .filter(
                            _Intr.user_id == _u.id,
                            _Intr.message_type == 'agent_msg',
                            _Intr.created_at >= _sem_since,
                        )
                        .order_by(_Intr.created_at.desc())
                        .limit(10)
                        .all()
                    )
                    for _rd in _recent_directives:
                        _rd_txt = str(_rd.content or '')
                        _rd_agent = ''
                        try:
                            _p_rd = json.loads(_rd_txt)
                            if isinstance(_p_rd, dict) and isinstance(_p_rd.get('__agent'), dict):
                                _rd_agent = str(_p_rd.get('__agent', {}).get('name') or '').strip()
                                _rd_txt = str(_p_rd.get('text') or '')
                        except Exception:
                            pass
                        if _rd_agent and _rd_agent.upper() != 'ASI':
                            continue  # skip non-director agent reports
                        _rd_txt = _rd_txt.lower()
                        if any(_re_sem_dd.search(m, _rd_txt) for m in _SEM_MARKERS):
                            logger.warning(
                                "[DIRECTOR] SEMANTIC-DEDUP: similar directive already sent %s ago, skipping",
                                str(_dt_dir.now(_tz_dir.utc) - _rd.created_at.replace(tzinfo=_tz_dir.utc))[:7]
                            )
                            return False
            # ────────────────────────────────────────────────────────────────────────
            _s.add(_Intr(user_id=_u.id, message_type=message_type, content=content))
            _s.commit()
            logger.info("[DIRECTOR] saved interaction type=%s for tg=%s, len=%d", message_type, telegram_id, len(content))
            return True
        except Exception as _db_err:
            logger.error("[DIRECTOR] DB commit error: %s", _db_err)
            try:
                _s.rollback()
            except Exception:
                pass
            return False
        finally:
            _s.close()
    except Exception as e:
        logger.error("[DIRECTOR] save interaction error: %s", e)
        return False


# ══ Универсальный контекст пользователя и агента ══════════════════════════════

# Маппинг ключей → человекочитаемые названия сервисов
_INTEGRATION_LABELS: dict = {
    'GMAIL': 'Gmail (почта)',
    'YANDEX_MAIL': 'Яндекс Почта',
    'IMAP': 'IMAP почта',
    'SMTP': 'SMTP почта',
    'OZON': 'Ozon (маркетплейс)',
    'WILDBERRIES': 'Wildberries',
    'WB_': 'Wildberries',
    'AMOCRM': 'AmoCRM',
    'AMO_': 'AmoCRM',
    'BITRIX': 'Битрикс24',
    'NOTION': 'Notion',
    'VK_': 'ВКонтакте',
    'TELEGRAM': 'Telegram',
    'DISCORD': 'Discord',
    'RSS': 'RSS-лента новостей',
    'TASS': 'ТАСС (RSS)',
    'OPENAI': 'OpenAI API',
    'ANTHROPIC': 'Anthropic Claude',
    'GOOGLE': 'Google API',
    'ALPHAVANTAGE': 'Биржевые данные (Alpha Vantage)',
    'ALPHA_VANTAGE': 'Биржевые данные (Alpha Vantage)',
    'NEWSAPI': 'NewsAPI (новости)',
    'NEWS_API': 'NewsAPI (новости)',
    'BINANCE': 'Binance (крипта)',
    'BYBIT': 'Bybit (крипта)',
    'COINBASE': 'Coinbase (крипта)',
    'STRIPE': 'Stripe (платежи)',
    'YOOKASSA': 'ЮКасса (платежи)',
    'RESEND': 'Email-рассылка (Resend)',
    'SENDGRID': 'Email-рассылка (SendGrid)',
    'OPENWEATHER': 'Погода (OpenWeatherMap)',
    'REPLICATE': 'Генерация изображений (Replicate)',
    'PINECONE': 'Векторная БД (Pinecone)',
    'REDIS': 'Redis (кэш/очереди)',
    'POSTGRES': 'PostgreSQL',
    'MYSQL': 'MySQL',
    'MONGO': 'MongoDB',
    'S3': 'Amazon S3 (хранилище)',
    'AWS': 'Amazon AWS',
    'AZURE': 'Microsoft Azure',
    'GITHUB': 'GitHub API',
    'GITLAB': 'GitLab API',
    'JIRA': 'Jira',
    'SLACK': 'Slack',
    'HUBSPOT': 'HubSpot CRM',
    'SALESFORCE': 'Salesforce CRM',
    'SHOPIFY': 'Shopify',
    'TWITTER': 'Twitter/X',
    'INSTAGRAM': 'Instagram',
    'YOUTUBE': 'YouTube API',
    'MAILRU': 'Mail.ru почта',
    'MAIL_RU': 'Mail.ru почта',
    'TRELLO': 'Trello',
    'ASANA': 'Asana',
    'TODOIST': 'Todoist',
    'FIGMA': 'Figma',
    'ZOOM': 'Zoom',
    'AVITO': 'Авито',
    'YANDEX_DIRECT': 'Яндекс.Директ',
    'YANDEX_MARKET': 'Яндекс.Маркет',
    'YANDEX_METRIKA': 'Яндекс.Метрика',
    'MOYSKLAD': 'МойСклад',
    'MY_SKLAD': 'МойСклад',
    'LINKEDIN': 'LinkedIn',
    'AIRTABLE': 'Airtable',
    'GOOGLE_SHEETS': 'Google Sheets',
    'GOOGLE_CALENDAR': 'Google Calendar',
    'SUPERJOB': 'SuperJob',
    'HH_': 'hh.ru',
    'GOOGLE_DRIVE': 'Google Drive',
    'MS_TEAMS': 'Microsoft Teams',
    'MS_GRAPH': 'Microsoft Graph',
    'MS_CLIENT': 'Microsoft Azure App',
    'OUTLOOK_': 'Microsoft Outlook',
    'MS_OUTLOOK': 'Microsoft Outlook',
    'CLICKUP': 'ClickUp',
    'YADISK': 'Яндекс.Диск',
    'GA4_': 'Google Analytics 4',
    'LINEAR': 'Linear',
    'WEBHOOK_URL': 'Webhook (n8n/Zapier/Make)',
    'CLICKUP': 'ClickUp',
    'LINEAR': 'Linear',
    'FIGMA': 'Figma API',
    'TELEGRAM_BOT': 'Telegram Bot',
    'OPENWEATHER': 'Погода (OpenWeatherMap)',
    'WEATHER_API': 'Weather API',
    # Дополнительные интеграции (ранее отсутствовали)
    'TWILIO': 'Twilio (звонки/SMS)',
    'TINKOFF': 'Тинькофф Инвестиции',
    'TINKOFF_INVEST': 'Тинькофф Инвестиции',
    'COINGECKO': 'CoinGecko (крипто-данные)',
    'WHATSAPP': 'WhatsApp Business',
    'WABA_': 'WhatsApp Business',
    'CDEK': 'СДЭК (доставка)',
    'SDEK': 'СДЭК (доставка)',
    '1C_': '1С',
    'ONEC_': '1С',
    'AVIASALES': 'Aviasales',
    'TUTU': 'Tutu.ru',
    'SIPUNI': 'Sipuni (телефония)',
    'VOXIMPLANT': 'VoxImplant (телефония)',
    'FIREBASE': 'Firebase/Firestore',
    'FIRESTORE': 'Firebase/Firestore',
    'CALENDLY': 'Calendly',
    'PLAYWRIGHT': 'Playwright (scraping)',
    'DEEPSEEK_API': 'DeepSeek AI API',
    'DEEPSEEK_KEY': 'DeepSeek AI API',
    # Биржевые данные (расширенные)
    'FINNHUB': 'Finnhub (биржевые данные)',
    'POLYGON': 'Polygon.io (биржевые данные)',
    'POLYGON_IO': 'Polygon.io (биржевые данные)',
    'TWELVE_DATA': 'Twelve Data (биржевые данные)',
    'TWELVEDATA': 'Twelve Data (биржевые данные)',
    'YAHOO_FINANCE': 'Yahoo Finance',
    'RAPIDAPI': 'RapidAPI',
    'FMP': 'Financial Modeling Prep',
}


def _agent_tools_from_intg(agent: dict, intg_labels: list) -> str:
    """Возвращает строку рекомендованных инструментов для агента на основе его интеграций и роли.
    Используется в директорском промпте для понятного отображения возможностей агента.
    """
    _tools_raw = (agent.get('tools_allowed') or '').strip()
    try:
        import json as _j2
        _explicit = _j2.loads(_tools_raw or '[]')
    except Exception:
        _explicit = []

    if _explicit:
        return ', '.join(_explicit[:10])

    # Динамически через anchor_engine capability system
    try:
        from anchor_engine import _classify_agent_caps, _CAP_TOOL_HINTS
        _caps = _classify_agent_caps(intg_labels)
        _cats = _caps.get('categories', set())
        seen: set = set()
        recommended: list = []
        for cat in _cats:
            hint = _CAP_TOOL_HINTS.get(cat, '')
            for tool in hint.split(','):
                t = tool.strip().split('(')[0].strip()
                if t and t not in seen:
                    seen.add(t)
                    recommended.append(t)
        if not recommended:
            recommended = ['web_search', 'research_topic', 'add_task', 'update_goal_progress']
        return ', '.join(recommended[:10])
    except Exception:
        return 'web_search, research_topic, add_task, update_goal_progress'


def _parse_agent_integrations(user_api_keys: str, python_code: str = '',
                               tools_allowed: str = '', search_scope: str = '') -> list[str]:
    """Универсально определяет что агент реально умеет по его настройкам.
    Возвращает список человекочитаемых названий сервисов.
    """
    found: set = set()

    # Расшифровываем user_api_keys если зашифрован (Fernet/obf)
    user_api_keys = _decrypt_keys(user_api_keys)

    # 1. Из user_api_keys — смотрим имена ключей
    for line in (user_api_keys or '').splitlines():
        line = line.strip()
        if '=' not in line or line.startswith('#'):
            continue
        key, _, val = line.partition('=')
        key = key.strip().upper()
        val = val.strip()
        # Пропускаем пустые значения и явные заглушки — интеграция не настроена
        if not val or len(val) < 4 or val.lower() in ('none', 'null', 'your_key_here', 'xxx', '...'):
            continue
        for prefix, label in _INTEGRATION_LABELS.items():
            if key.startswith(prefix):
                found.add(label)
                break
        else:
            # Ключ не распознан известными префиксами.
            # Если выглядит как API-credential — показываем как "Custom API: Xxx Yyy".
            # Берём первые 2 части имени (MY_CUSTOM_CRM_TOKEN → "My Custom") для информативности.
            # Это позволяет AI знать, что интеграция есть, даже если она нестандартная.
            _API_SUFFIXES = ('_API', '_KEY', '_TOKEN', '_SECRET', '_ACCESS', '_HOOK', '_URL')
            _parts = key.split('_')
            # Убираем общие суффиксы-слова из конца для более чистого имени
            _GENERIC_SUFFIXES = {'API', 'KEY', 'TOKEN', 'SECRET', 'ACCESS', 'HOOK', 'URL', 'PASS', 'PASSWORD'}
            while _parts and _parts[-1] in _GENERIC_SUFFIXES:
                _parts = _parts[:-1]
            _base = _parts[0] if _parts else key.split('_')[0]
            _service_name = ' '.join(p.title() for p in _parts[:3]) if len(_parts) > 1 else _base.title()
            if (
                any(key.endswith(s) for s in _API_SUFFIXES) or 'API' in key or 'TOKEN' in key
            ) and len(_service_name) >= 3 and _base not in ('NONE', 'NULL', 'TRUE', 'FALSE', 'DEBUG', 'ENV'):
                found.add(f'Custom API: {_service_name}')

    # 2. Из python_code — ищем import и характерные строки
    code_lc = (python_code or '').lower()
    _code_hints = {
        'imaplib': 'IMAP почта', 'smtplib': 'SMTP почта',
        'gmail': 'Gmail (почта)', 'imap.yandex': 'Яндекс Почта', 'smtp.yandex': 'Яндекс Почта',
        'mail.ru': 'Mail.ru почта',
        'ozon': 'Ozon (маркетплейс)', 'wildberries': 'Wildberries',
        'amocrm': 'AmoCRM', 'bitrix': 'Битрикс24',
        'notion': 'Notion', 'vk.com': 'ВКонтакте',
        'binance': 'Binance (крипта)', 'bybit': 'Bybit (крипта)',
        'avito': 'Авито', 'avito.ru': 'Авито',
        'yandex.direct': 'Яндекс.Директ', 'moysklad': 'МойСклад',
        'yandex.market': 'Яндекс.Маркет', 'yandex_metrika': 'Яндекс.Метрика',
        'api-metrika.yandex': 'Яндекс.Метрика', 'linkedin': 'LinkedIn',
        'airtable': 'Airtable', 'gspread': 'Google Sheets',
        'google.oauth': 'Google API', 'googleapiclient': 'Google Sheets',
        'feedparser': 'RSS-лента', 'rss': 'RSS-лента новостей',
        'openai': 'OpenAI API', 'anthropic': 'Anthropic Claude',
        'stripe': 'Stripe (платежи)', 'yookassa': 'ЮКасса (платежи)',
        'alpha_vantage': 'Биржевые данные', 'coinbase': 'Coinbase (крипта)',
        'telegram': 'Telegram', 'discord': 'Discord',
        'slack': 'Slack', 'trello': 'Trello', 'asana': 'Asana', 'todoist': 'Todoist',
        'github': 'GitHub API', 'gitlab': 'GitLab API',
        'zoom': 'Zoom', 'figma': 'Figma API', 'shopify': 'Shopify',
        'replicate': 'Генерация изображений',
        'requests.get': 'HTTP-запросы', 'aiohttp': 'HTTP-запросы',
        'selenium': 'Браузерная автоматизация',
        'playwright': 'Браузерная автоматизация',
        'pandas': 'Анализ данных (pandas)',
        'sqlite': 'SQLite', 'psycopg': 'PostgreSQL',
        'clickup': 'ClickUp', 'click_up': 'ClickUp',
        'linear': 'Linear',
        'openweathermap': 'Погода (OpenWeatherMap)',
        'openweather': 'Погода (OpenWeatherMap)',
        'outlook': 'Microsoft Outlook',
        'microsoft.graph': 'Microsoft Graph (Outlook/Teams)',
        'msal': 'Microsoft OAuth (Outlook/Teams)',
    }
    for hint, label in _code_hints.items():
        if hint in code_lc:
            found.add(label)

    # 3. Из tools_allowed (JSON)
    try:
        import json as _j
        tools = _j.loads(tools_allowed or '[]')
        _tool_labels = {
            # Задачи и цели
            'add_task': 'Управление задачами',
            'edit_task': 'Редактирование задач',
            'delete_task': 'Удаление задач',
            'complete_task': 'Завершение задач',
            'list_tasks': 'Просмотр задач',
            'reschedule_task': 'Перенос задач',
            'restore_task': 'Восстановление задач',
            'get_task_details': 'Детали задачи',
            'check_time_conflicts': 'Проверка конфликтов расписания',
            'set_reminder': 'Установка напоминаний',
            'create_goal': 'Создание целей',
            'update_goal': 'Обновление целей',
            'update_goal_progress': 'Прогресс по целям',
            'complete_goal': 'Завершение целей',
            'delete_goal': 'Удаление целей',
            'list_goals': 'Управление целями',
            # Делегирование
            'delegate_task': 'Делегирование задач',
            'accept_delegated_task': 'Принятие делегированных задач',
            'reject_delegated_task': 'Отклонение задач',
            'get_delegation_progress': 'Статус делегирования',
            'cancel_delegation': 'Отмена делегирования',
            'start_delegation_campaign': 'Кампания по делегированию',
            'manage_delegation_campaign': 'Управление кампаниями делегирования',
            # Email и переписка
            'add_note': 'Заметки',
            'send_email': 'Отправка email',
            'send_outreach_email': 'Outreach-письма',
            'reply_to_outreach_email': 'Ответы на outreach',
            'send_follow_up_email': 'Follow-up письма',
            'negotiate_by_email': 'Переговоры по email',
            'save_email_contact': 'Сохранение email-контактов',
            'list_email_contacts': 'База email-контактов',
            # Контакты и сообщения
            'find_relevant_contacts_for_task': 'Поиск релевантных контактов',
            'set_contact_alert': 'Мониторинг контактов',
            'find_and_message_relevant_users': 'Рассылка релевантным пользователям',
            'send_message_to_user': 'Отправка сообщений пользователям',
            'reply_to_user_message': 'Ответы на сообщения',
            'get_incoming_messages': 'Входящие сообщения',
            'get_message_status': 'Статус сообщений',
            'find_partners': 'Поиск партнёров',
            'analyze_group_opportunities': 'Анализ аудитории',
            # Публикации и контент
            'create_post': 'Публикация контента',
            'edit_post': 'Редактирование постов',
            'get_posts': 'Просмотр публикаций',
            'delete_post': 'Удаление постов',
            'publish_to_telegram': 'Публикация в Telegram',
            'publish_to_discord': 'Публикация в Discord',
            'set_content_strategy': 'Контент-стратегия',
            'start_content_campaign': 'Контент-кампании',
            'manage_content_campaign': 'Управление контент-кампаниями',
            'generate_marketing_content': 'Маркетинговый контент',
            # Исследования и анализ
            'web_search': 'Поиск в интернете',
            'research_topic': 'Исследование тем',
            'quick_topic_search': 'Быстрый поиск по теме',
            'research_and_plan': 'Исследование + план действий',
            'analyze_situation_and_suggest_tasks': 'Ситуационный анализ',
            'get_weather_info': 'Погода',
            'get_news_trends': 'Новости и тренды',
            # Генерация контента
            'generate_image': 'Генерация изображений',
            # Внешние интеграции
            'run_agent_action': 'Внешние сервисы (Slack/GitHub/Notion/Jira/Trello)',
            'schedule_background_task': 'Фоновые задачи',
            # Профиль и система
            'update_profile': 'Обновление профиля',
            'get_system_status': 'Статус системы',
            'switch_agent': 'Переключение агентов',
            'list_marketplace': 'Маркетплейс агентов',
        }
        for t in tools:
            if t in _tool_labels:
                found.add(_tool_labels[t])
    except Exception as _e:
        logger.debug("suppressed: %s", _e)

    # 4. Из search_scope
    if search_scope and search_scope.strip():
        found.add(f'Поиск: {search_scope.strip()[:60]}')

    # 5. Если tools_allowed пустой → агент универсальный, все инструменты платформы доступны
    try:
        _tj = (tools_allowed or '').strip()
        if not _tj or _tj == '[]':
            found.add('все инструменты платформы')
    except Exception as _e:
        logger.debug("suppressed: %s", _e)

    return sorted(found)


def _infer_capabilities_from_role(job_title: str, specialization: str, description: str) -> list[str]:
    """Derives agent capabilities from role/description when no explicit integrations configured."""
    caps: set[str] = set()
    combined = f"{job_title} {specialization} {description}".lower()

    _role_map: list[tuple[tuple, list]] = [
        (('маркетолог', 'marketing', 'smm', 'реклам', 'продвиж', 'promo'),
         ['рекламные тексты', 'стратегия продвижения', 'поиск площадок и каналов',
          'контент-план', 'анализ аудитории', 'SEO/SMM советы']),
        (('аналитик', 'analyst', 'analytic', 'data', 'статист', 'исследован'),
         ['анализ данных', 'исследование рынка', 'отчёты и инсайты',
          'сравнительный анализ', 'выявление паттернов']),
        (('разработчик', 'developer', 'программист', 'engineer', 'инженер', 'backend', 'frontend', 'fullstack'),
         ['написание кода', 'техническая архитектура', 'код-ревью',
          'отладка', 'API-интеграции']),
        (('копирайтер', 'copywriter', 'контент', 'content', 'журналист', 'редактор', 'писател'),
         ['написание текстов', 'редактура и корректура', 'сторителлинг',
          'сценарии и скрипты', 'посты для соцсетей']),
        (('дизайнер', 'designer', 'ui', 'ux', 'визуал', 'creative'),
         ['UI/UX рекомендации', 'визуальная концепция', 'брендинг советы']),
        (('менеджер', 'manager', 'product', 'проект', 'project', 'pm', 'руководител'),
         ['планирование проекта', 'декомпозиция задач', 'управление командой',
          'дорожная карта', 'OKR / KPI']),
        (('продаж', 'sales', 'account', 'коммерц', 'бизнес-развит'),
         ['поиск клиентов', 'скрипты продаж', 'стратегия привлечения',
          'работа с возражениями', 'анализ конкурентов']),
        (('hr', 'рекрутер', 'персонал', 'talent'),
         ['поиск кандидатов', 'оценка резюме', 'онбординг']),
        (('финанс', 'бухгалтер', 'finance', 'accounting', 'cfo', 'эконом'),
         ['финансовый анализ', 'бюджетирование', 'P&L оценка']),
        (('юрист', 'legal', 'law', 'право', 'договор'),
         ['юридический анализ', 'составление договоров', 'оценка рисков']),
        (('стратег', 'strateg', 'консульт', 'consult', 'advisor', 'советник'),
         ['стратегическое планирование', 'бизнес-анализ', 'рекомендации и план действий']),
    ]

    for keywords_tuple, abilities in _role_map:
        for kw in keywords_tuple:
            if kw in combined:
                caps.update(abilities)
                break

    # Базовые AI-возможности есть у ЛЮБОГО агента без интеграций
    caps.update(['исследование и анализ', 'написание и редактура текстов',
                 'составление списков и планов', 'генерация идей'])
    return sorted(caps)


def _build_user_context_sync(user_db_id: int) -> str:
    """Строит универсальный контекст пользователя для инжекта в промпты агентов.
    Включает: профиль (кто он), цели (что хочет), агенты (его команда), email-контакты.
    """
    try:
        import json as _j
        from models import Session as _Db, User as _U, UserProfile as _UP, Goal as _G, UserAgent as _UA, EmailContact as _EC, Task as _T
        _s = _Db()
        try:
            user = _s.query(_U).filter_by(id=user_db_id).first()
            profile = _s.query(_UP).filter_by(user_id=user_db_id).first()
            goals = (_s.query(_G)
                     .filter_by(user_id=user_db_id, status='active')
                     .order_by(_G.priority.desc())
                     .limit(5).all())
            agents = (_s.query(_UA)
                      .filter(
                          _UA.author_id == user_db_id,
                          _UA.status.in_(['active', 'paused']),
                      )
                      .limit(10).all())
            contacts = (_s.query(_EC)
                        .filter_by(user_id=user_db_id)
                        .order_by(_EC.created_at.desc())
                        .limit(10).all())
            tasks = (_s.query(_T)
                     .filter(_T.user_id == user_db_id, _T.status.in_(['pending', 'in_progress']))
                     .order_by(_T.due_date.asc().nullslast())
                     .limit(10).all())
        finally:
            _s.close()
    except Exception as _ctx_err:
        logger.warning('[CONTEXT] _build_user_context_sync failed: %s', _ctx_err)
        return ''

    # ── Дополнительные запросы: история писем + активность команды ──
    outreach_recent: list = []
    team_activity_recent: list = []
    try:
        from models import Session as _Db2, EmailOutreach as _EO_uc, AgentActivityLog as _AAL_uc
        import datetime as _dt_uc_act
        _s2 = _Db2()
        try:
            outreach_recent = (
                _s2.query(_EO_uc)
                .filter_by(user_id=user_db_id)
                .order_by(_EO_uc.created_at.desc())
                .limit(5).all()
            )
            team_activity_recent = (
                _s2.query(_AAL_uc)
                .filter(
                    _AAL_uc.user_id == user_db_id,
                    _AAL_uc.created_at >= _dt_uc_act.datetime.now(_dt_uc_act.timezone.utc) - _dt_uc_act.timedelta(hours=6),
                    _AAL_uc.activity_type.in_(['agent_task', 'coordinator_summary', 'email', 'delegation']),
                )
                .order_by(_AAL_uc.created_at.desc())
                .limit(10).all()
            )
        finally:
            _s2.close()
    except Exception as _ctx2_err:
        logger.debug('[CONTEXT] extra queries failed: %s', _ctx2_err)

    parts: list[str] = []

    # --- Кто пользователь (ВЛАДЕЛЕЦ — НЕ контакт!) ---
    identity_parts: list[str] = []
    _owner_ids: list[str] = []  # собираем ВСЕ идентификаторы владельца
    if user:
        name = user.first_name or user.username or ''
        if name:
            identity_parts.append(name)
        # Собираем все идентификаторы владельца чтобы агент НЕ путал его с внешним контактом
        if user.email:
            _owner_ids.append(f'email: {user.email}')
        if user.username:
            _owner_ids.append(f'telegram: @{user.username}')
        if user.telegram_id:
            _owner_ids.append(f'telegram_id: {user.telegram_id}')
    if profile:
        if profile.position:
            identity_parts.append(profile.position)
        if profile.company:
            identity_parts.append(f'из «{profile.company}»')
        if profile.city:
            identity_parts.append(f'г. {profile.city}')
            if profile.status_text:
                identity_parts.append(f'Статус: {profile.status_text}')
            if profile.current_plans:
                identity_parts.append(f'Сейчас: {profile.current_plans[:100]}')
        if profile.content_strategy:
            identity_parts.append(f'Контент-стратегия: {profile.content_strategy[:100]}')
        if profile.website:
            identity_parts.append(f'Сайт: {profile.website}')

    if identity_parts:
        _owner_block = '👤 ВЛАДЕЛЕЦ (твой босс, НЕ контакт для outreach!): ' + ', '.join(identity_parts)
        if _owner_ids:
            _owner_block += '\n   Его аккаунты: ' + ', '.join(_owner_ids)
            _owner_block += (
                '\n   ⚠️ Это твой заказчик: добавлять его в базу контактов или делать outreach — '
                'значит работать против себя. Используй его данные ТОЛЬКО как контекст, не как лид.'
            )
        parts.append(_owner_block)

    # --- Его цели ---
    if goals:
        goal_lines = []
        for g in goals:
            line = f'• {g.title}'
            if g.progress_percentage:
                line += f' [{g.progress_percentage}%]'
            if g.target_date:
                line += f' до {g.target_date.strftime("%d.%m.%Y")}'
            if g.metric_target and g.metric_unit:
                line += f' (цель: {g.metric_current or 0}/{g.metric_target} {g.metric_unit})'
            goal_lines.append(line)
        parts.append('ЦЕЛИ:\n' + '\n'.join(goal_lines))

    # --- Его команда агентов + их реальные возможности ---
    if agents:
        agent_lines = []
        for a in agents:
            integrations = _parse_agent_integrations(
                a.user_api_keys or '',
                a.python_code or '',
                a.tools_allowed or '',
                a.search_scope or '',
            )
            line = f'• {a.name}'
            if a.specialization:
                line += f' ({a.specialization})'
            if a.description:
                line += f': {a.description[:80]}'
            if integrations:
                line += f'\n  Интеграции: {", ".join(integrations[:5])}'
            agent_lines.append(line)
        parts.append('АГЕНТЫ ПОЛЬЗОВАТЕЛЯ:\n' + '\n'.join(agent_lines))

    # --- Email-контакты пользователя ---
    if contacts:
        contact_lines = []
        for c in contacts:
            line = f'• {c.name or "(нет имени)"} <{c.email}>'
            if c.company:
                line += f', {c.company}'
            if c.position:
                line += f', {c.position}'
            if c.status and c.status != 'new':
                line += f' [{c.status}]'
            if c.notes:
                line += f' — {c.notes[:80]}'
            contact_lines.append(line)
        parts.append(
            'EMAIL-КОНТАКТЫ (уже в базе — НЕ ищи их повторно!):\n' + '\n'.join(contact_lines)
            + '\n⚠️ Эти люди УЖЕ сохранены. Ищи НОВЫХ контактов, не дублируй этих.'
        )

    # --- История outreach писем (последние 5) ---
    if outreach_recent:
        _o_lines = []
        _st_map = {
            'sent': 'отпр.', 'replied': '✉️ ОТВЕТИЛ',
            'bounced': '❌ bounce', 'opened': 'открыл', 'failed': '❌ ошибка',
        }
        for _o in outreach_recent:
            _ts = _o.created_at.strftime('%d.%m %H:%M') if _o.created_at else ''
            _st = _st_map.get(_o.status or '', _o.status or '')
            _line = f"• [{_ts}] → {_o.recipient_name or ''} <{_o.recipient_email}> — {(_o.subject or '(нет темы)')[:60]} [{_st}]"
            if _o.reply_text:
                _line += f"\n  Ответ: {_o.reply_text[:120].replace(chr(10), ' ')}"
            _o_lines.append(_line)
        parts.append(
            'ИСТОРИЯ OUTREACH (последние письма):\n' + '\n'.join(_o_lines)
            + '\n⚠️ sent/replied = УЖЕ писали этому человеку. replied = ждёт ответа.'
        )

    # --- Активные задачи пользователя ---
    if tasks:
        task_lines = []
        for t in tasks:
            line = f'• {t.title}'
            if t.status == 'in_progress':
                line += ' [в работе]'
            if t.due_date:
                line += f' до {t.due_date.strftime("%d.%m.%Y %H:%M")}'
            if t.delegated_to_username:
                line += f' → делегировано {t.delegated_to_username}'
                if t.delegation_status:
                    line += f' ({t.delegation_status})'
            if t.created_by_agent_id:
                line += ' (создано агентом)'
            if t.goal_id:
                # найдём цель по id в уже загруженных
                linked_goal = next((g for g in goals if g.id == t.goal_id), None)
                if linked_goal:
                    line += f' → цель: {linked_goal.title[:50]}'
            task_lines.append(line)
        parts.append('АКТИВНЫЕ ЗАДАЧИ:\n' + '\n'.join(task_lines))

    # --- Недавняя активность команды (6ч) ---
    if team_activity_recent:
        _ta_lines = []
        _seen_agents_ta: set = set()
        for _ta in team_activity_recent:
            _ts = _ta.created_at.strftime('%H:%M') if _ta.created_at else ''
            _ag = (_ta.title or '').replace(' — обзор целей', '').strip()[:25]
            _res = (_ta.result or '').strip()[:100]
            if _ag and _res and _ag not in _seen_agents_ta:
                _ta_lines.append(f'• [{_ts}] {_ag}: {_res}')
                _seen_agents_ta.add(_ag)
        if _ta_lines:
            parts.append(
                'АКТИВНОСТЬ КОМАНДЫ (6ч):\n' + '\n'.join(_ta_lines[:5])
                + '\n→ Не дублируй действия коллег. Координируй со всеми.'
            )

    # --- Правила пользователя (из user.memory) ---
    try:
        if user and user.memory:
            import json as _rj
            _mem = user.memory.strip()
            if _mem.startswith('{'):
                _mj = _rj.loads(_mem)
                _rules = _mj.get('rules', [])
                if _rules:
                    _rules_lines = '\n'.join(f"  {i+1}. {r}" for i, r in enumerate(_rules))
                    parts.append(
                        '🔴 ОБЯЗАТЕЛЬНЫЕ ПРАВИЛА ПОЛЬЗОВАТЕЛЯ (соблюдай ВСЕГДА, в каждом действии и ответе):\n'
                        + _rules_lines
                        + '\nЭти правила отменяют любое поведение по умолчанию. Нарушение = провал.'
                    )
    except Exception:
        pass

    return '\n\n'.join(parts)


def _get_agent_anchors(user_db_id: int, agent_id: int, hours: float = 4.0) -> list:
    """Загружает свежие якоря делегирования для конкретного агента."""
    try:
        import datetime as _dt
        import json as _json
        from models import Session as _Db, Anchor as _Anch
        _s = _Db()
        try:
            _since = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=hours)
            rows = (
                _s.query(_Anch)
                .filter(
                    _Anch.user_id == user_db_id,
                    _Anch.anchor_type == 'agent_delegation',
                    _Anch.source == f'agent:{agent_id}',
                    _Anch.created_at >= _since,
                )
                .order_by(_Anch.created_at.desc())
                .limit(3)
                .all()
            )
            result = []
            for r in rows:
                d = _json.loads(r.data) if r.data else {}
                age_min = int(
                    (_dt.datetime.now(_dt.timezone.utc) -
                     r.created_at.replace(tzinfo=_dt.timezone.utc)).total_seconds() / 60
                )
                result.append({'topic': r.topic, 'data': d, 'age_min': age_min})
            return result
        finally:
            _s.close()
    except Exception as e:
        logger.debug("[DIRECTOR] load anchors error: %s", e)
        return []


def _save_agent_delegation_anchor(user_db_id: int, agent_id: int, agent_name: str,
                                  task: str, result_summary: str, cooldown_hours: float = 2.0):
    """Сохраняет якорь делегирования — ASI будет помнить что агент делал и что нашёл."""
    try:
        import datetime as _dt
        import json as _json
        from models import Session as _Db, Anchor as _Anch, AnchorPriority
        _s = _Db()
        try:
            now = _dt.datetime.now(_dt.timezone.utc)
            # Дедупликация: проверяем есть ли якорь с похожим task для этого агента за cooldown
            _cutoff = now - _dt.timedelta(hours=max(cooldown_hours, 1.0))
            _existing = _s.query(_Anch).filter(
                _Anch.user_id == user_db_id,
                _Anch.anchor_type == 'agent_delegation',
                _Anch.source == f'agent:{agent_id}',
                _Anch.created_at >= _cutoff,
            ).order_by(_Anch.created_at.desc()).limit(5).all()
            _task_key = task[:60].lower().strip()
            for _ex in _existing:
                _ex_topic = (_ex.topic or '').lower()
                if _task_key[:30] in _ex_topic:
                    return  # похожая делегация уже есть
                try:
                    _ex_data = _json.loads(_ex.data) if _ex.data else {}
                    _ex_task = (_ex_data.get('task', '') or '').lower()
                    if _task_key[:30] in _ex_task:
                        return
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)
            # expires_at минимум 4ч — чтобы AnchorEngine успел увидеть и доставить якорь
            _effective_expires = max(cooldown_hours, 4.0)
            _s.add(_Anch(
                user_id=user_db_id,
                anchor_type='agent_delegation',
                source=f'agent:{agent_id}',
                topic=f'{agent_name}: {task[:120]}',
                priority=AnchorPriority.HIGH,
                data=_json.dumps({
                    'agent_name': agent_name,
                    'agent_id': agent_id,
                    'task': task[:300],
                    'result_summary': result_summary[:500],
                }, ensure_ascii=False),
                triggered_at=now,
                # expires через max(cooldown, 1ч) — агент снова «свободен» после этого
                expires_at=now + _dt.timedelta(hours=_effective_expires),
                cooldown_hours=cooldown_hours,
                batch_group='office',
            ))
            _s.commit()
        finally:
            _s.close()
    except Exception as e:
        logger.debug("[DIRECTOR] save anchor error: %s", e)


# ── Агент вклинивается в разговор ──────────────────────────────────────────

async def _agent_chimes_in(user_message: str, asi_response: str, user_id: int):
    """
    После ответа ASI один из агентов пользователя может вклиниться в разговор.
    Как в арене: читает последний обмен, реагирует со своей экспертизой.
    Вызывается как фоновая задача — не блокирует основной ответ.
    Вероятность: 30% на каждое сообщение. Cooldown 8 мин на агента.
    """
    import random as _rnd
    import json as _json

    # Вероятностный фильтр — не на каждое сообщение
    if _rnd.random() > 0.15:  # 15% вероятность (было 30%)
        return

    # Проверяем баланс до задержки: если токенов нет — не включаемся
    try:
        from config import FREE_ACCESS_MODE as _FAM_ch
        from token_service import has_enough_tokens as _het, spend_tokens as _st_ch
        if not _FAM_ch:
            if not _het(user_id, 'agent_chime'):
                return
    except Exception as _e:
        logger.debug("suppressed: %s", _e)

    # Задержка для реализма — агент «думает» 8–25 сек
    await asyncio.sleep(_rnd.uniform(8, 25))

    # Загружаем агентов пользователя
    try:
        from .user_agents import get_user_active_agents, load_agent_personality
        from models import Session as _Db, User as _User, Interaction as _Itr, UserAgent as _UA
    except ImportError:
        return

    try:
        _s = _Db()
        try:
            _u = _s.query(_User).filter_by(telegram_id=user_id).first()
            user_db_id = _u.id if _u else None
        finally:
            _s.close()
    except Exception:
        return

    if not user_db_id:
        return

    # Загружаем агентов
    _agents = []
    try:
        _ids = get_user_active_agents(user_db_id)
        if _ids:
            _agents = [d for _id in _ids for d in [load_agent_personality(_id)] if d]
    except Exception:
        return

    if not _agents:
        return

    # Cooldown: агент не вклинивается чаще раза в 8 минут — проверяем по DB (in-memory dict не работал,
    # т.к. load_agent_personality каждый раз возвращает новый объект)
    import datetime as _dt_ch
    _chime_cutoff = _dt_ch.datetime.utcnow() - _dt_ch.timedelta(minutes=8)
    try:
        _cs = _Db()
        try:
            _recent_chimes = _cs.query(_Itr).filter(
                _Itr.user_id == user_db_id,
                _Itr.message_type == 'ai',
                _Itr.created_at >= _chime_cutoff,
            ).all()
            _recently_chimed: set = set()
            import json as _cj
            for _rc in _recent_chimes:
                try:
                    _rd = _cj.loads(_rc.content or '')
                    if '__agent' in _rd:
                        _aid = _rd['__agent'].get('id')
                        if _aid:
                            _recently_chimed.add(int(_aid))
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)
        finally:
            _cs.close()
        _agents = [a for a in _agents if a.get('id') not in _recently_chimed]
    except Exception:
        pass

    if not _agents:
        return

    # Выбираем агента: тот чья специализация ближе к теме, иначе случайный
    _topic = (user_message + ' ' + asi_response).lower()
    _scored = []
    for _a in _agents:
        _spec = (_a.get('specialization') or _a.get('description') or '').lower()
        # Используем word boundary проверку вместо подстроки
        _score = sum(1 for w in _spec.split() if len(w) > 4 and re.search(rf'\b{re.escape(w)}', _topic))
        _scored.append((_score, _a))
    _scored.sort(key=lambda x: x[0], reverse=True)
    _agent = _scored[0][1] if _scored[0][0] > 0 else _rnd.choice(_agents)

    # Строим универсальный контекст пользователя + возможностей агента
    _user_ctx = _build_user_context_sync(user_db_id)

    # Реальные возможности агента из DB
    _integrations: list = []
    try:
        _db_ag_tmp = _Db()
        try:
            _db_rec = _db_ag_tmp.query(_UA).filter_by(id=_agent.get('id')).first()
            if _db_rec:
                _integrations = _parse_agent_integrations(
                    _db_rec.user_api_keys or '',
                    _db_rec.python_code or '',
                    _db_rec.tools_allowed or '',
                    _db_rec.search_scope or '',
                )
        finally:
            _db_ag_tmp.close()
    except Exception:
        pass

    _integrations_hint = (
        f"\nТвои подключённые сервисы: {', '.join(_integrations)}." if _integrations else ''
    )

    # Разрешаем отчёт "я сделала" только если есть явное делегирование этому агенту в текущем диалоге.
    _evidence_text = f"{user_message}\n{asi_response}".lower()
    _agent_name_l = str(_agent.get('name') or '').strip().lower()
    _delegation_tokens = (
        'поруч', 'делег', 'передал задач', 'попросил', 'попросила',
        'назначил', 'назначила', 'сделай', 'проверь', 'отработай',
    )
    _has_delegation_evidence = bool(
        _agent_name_l and
        _agent_name_l in _evidence_text and
        any(_tok in _evidence_text for _tok in _delegation_tokens)
    )

    # ┌─ ВАЖНО: Чтобы агент НЕ начал выдавать себя за реально выполнившего работу,
    #           система должна сказать ему не "ты - это агент", а
    #           "ты комментируешь от лица агента" с явным стоп-словом на действия
    _system = (
        "Ты один из команды персональных агентов внутри ASI Biont. "
        "В этом разговоре твоя роль — добавить экспертный комментарий со своей стороны.\n\n"
        f"Твоя специализация: {_agent.get('specialization', 'специалист')}.\n"
        f"Твоё имя в команде: {_agent['name']}.\n"
        f"Описание: {_agent.get('description', '')}{_integrations_hint}\n\n"
    )
    _agent_name_str = (_agent.get('name') or '').strip()
    _agent_fem = (
        _agent_name_str and len(_agent_name_str) >= 2
        and _agent_name_str[-1] in 'аяАЯ'
        and _agent_name_str[-2:].lower() not in ('ша', 'жа')
    )
    if _agent_fem:
        _system += (
            f"ГЕНДЕР: {_agent_name_str} — женское имя. Используй женский род: "
            "прочитала, заметила, нашла, подготовила, сделала.\n\n"
        )
    if _has_delegation_evidence:
        _system += (
            "РЕЖИМ: ОТЧЁТ ПО ДЕЛЕГИРОВАНИЮ. Можно говорить от первого лица как исполнитель. "
            "Разрешено кратко отчитаться о результате, но не выдумывай факты, которых нет в этом разговоре. "
            "Если деталей в диалоге нет — скажи нейтрально, что готова отработать и дать статус после выполнения."
        )
    else:
        _system += (
            "РЕЖИМ: КОММЕНТАРИЙ. ЖЁСТКОЕ ПРАВИЛО: Ты не выполняла задачу, которую видишь в чате. "
            "Ты просто комментируешь. Говоришь то как: "
            "'Это в моей компетенции, вот мой взгляд...' или 'Я помогу если позовут'. "
            "Никогда не говори 'я сделала', 'я запустила', 'я проверила' или 'я уже'."
        )
    if _user_ctx:
        _system += f"\n\nКОНТЕКСТ О ПОЛЬЗОВАТЕЛЕ:\n{_user_ctx}"

    _user_content = (
        f"В чате происходит разговор:\n"
        f"Пользователь: {user_message[:200]}\n"
        f"ASI ответила: {asi_response[:300]}\n\n"
        "Ты — коллега ASI. Ты видишь этот разговор и хочешь добавить короткую реплику со своей стороны.\n"
        f"Режим ответа: {'отчёт по делегированной задаче' if _has_delegation_evidence else 'комментарий по экспертизе'}.\n"
        "НЕ выдумывай факты (письма, суммы, статусы), которых нет в текущем обмене.\n"
        "ФОРМАТ ЖЁСТКИЙ: 1-2 предложения, БЕЗ списков, БЕЗ маркеров (•, -, *), БЕЗ нумерации, БЕЗ заголовков, БЕЗ пустых строк.\n"
        "Учитывай кто этот пользователь и чем он занимается — отвечай релевантно его контексту.\n"
        "1-2 предложения макс. Живо, как рабочая чатуха. Если нечего добавить — пустая строка."
    )

    try:
        from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
        import aiohttp
        async with aiohttp.ClientSession() as _sess:
            async with _sess.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": DEEPSEEK_MODEL,
                    "messages": [
                        {"role": "system", "content": _system},
                        {"role": "user", "content": _user_content},
                    ],
                    "max_tokens": 120,
                    "temperature": 0.85,
                },
                timeout=aiohttp.ClientTimeout(total=12),
            ) as _resp:
                if _resp.status != 200:
                    return
                _data = await _resp.json()
                _reply = _data["choices"][0]["message"]["content"].strip()
    except Exception as _e:
        logger.debug("[CHIME] AI error: %s", _e)
        return

    # Нормализация chime-ответа: без списков/пустых строк/многострочности.
    try:
        from .utils import clean_technical_details as _clean_chime
        _reply = _clean_chime(_reply or '').strip()
    except Exception:
        _reply = (_reply or '').strip()
    _reply = re.sub(r'^\s*[•\-*]\s+', '', _reply, flags=re.MULTILINE)
    _reply = re.sub(r'^\s*\d+[\.)]\s+', '', _reply, flags=re.MULTILINE)
    _reply = ' '.join([_ln.strip() for _ln in _reply.splitlines() if _ln.strip()])
    _reply = re.sub(r'\s{2,}', ' ', _reply).strip()

    if not _reply or len(_reply) < 5:
        return

    # Списываем токены за chime
    try:
        from config import FREE_ACCESS_MODE as _FAM_ch2
        from token_service import spend_tokens as _st_ch2
        if not _FAM_ch2:
            _st_ch2(user_id, 'agent_chime', description=f'chime:{_agent["name"]}')
    except Exception as _e:
        logger.debug("suppressed: %s", _e)

    # Сохраняем в Interaction
    try:
        _s2 = _Db()
        try:
            _ag_id = _agent.get('id', 0)
            _content = _json.dumps({
                '__agent': {
                    'name': _agent['name'],
                    'id': _ag_id,
                    'avatar_url': f'/api/arena/agent_avatar/{_ag_id}' if _ag_id else '',
                },
                'text': _reply,
            }, ensure_ascii=False)
            _s2.add(_Itr(
                user_id=user_db_id,
                message_type='ai',
                content=_content,
            ))
            _s2.commit()
            logger.info("[CHIME] %s chimed in for user %d", _agent['name'], user_db_id)
        finally:
            _s2.close()
    except Exception as _e:
        logger.debug("[CHIME] save error: %s", _e)


async def _exec_agent_for_director(agent: dict, task: str, user_id: int, dialog_context: str = "", _depth: int = 0) -> tuple:
    """Запускает агента с полноценным tool-calling циклом (по tools_allowed).
    1. Выполняет python_code (внешние данные: IMAP, RSS, HTTP)
    2. Запускает tool-loop через платформенные инструменты (до 3 итераций)
    3. Агент реально вызывает send_email, research_topic и т.д. по своему tools_allowed
    Используется в _office_director_chat для delegate и multi_delegate.
    _depth: текущая глубина рекурсии (макс 2).
    Returns: (response_text, tools_used_list)
    """
    if _depth >= 2:
        return f"Агент {agent.get('name', '?')}: превышена глубина делегирования, задача принята.", []

    # Определяем род агента по имени для правильных fallback-фраз
    _aname_fb = (agent.get('name') or '').strip()
    _is_fem = _aname_fb and _aname_fb[-1] in 'аяАЯ' and _aname_fb[-2:].lower() not in ('ша', 'жа')
    _done_fb = 'Задачу выполнила.' if _is_fem else 'Задачу выполнил.'

    import subprocess as _sp2, sys as _sys2, os as _os2

    _persona = (
        agent.get('personality') or
        f"Ты действуешь как {agent['name']} — {agent.get('specialization', 'специалист')}. "
        f"{agent.get('description', '')} Отвечай от имени {agent['name']}."
    )
    from datetime import datetime as _dt_exec
    _now_str = _dt_exec.now().strftime('%Y-%m-%d %H:%M (%A)')
    _combined_ctx = (task or '') + '\n' + (dialog_context or '')
    _is_autopilot_task = (
        'АВТОПИЛОТ ЦЕЛЕЙ' in _combined_ctx
        or 'autopilot' in _combined_ctx.lower()
        or 'Активные цели:' in _combined_ctx
        or '[АВТОПИЛОТ]' in _combined_ctx
        # Делегированные email/outreach-задачи из контекста автопилота
        or any(w in (task or '').lower() for w in (
            'email-кампани', 'email кампани', 'запустить кампани',
            'outreach-кампани', 'реализовать кампани', 'провести кампани',
        ))
    )

    # Адаптивный хинт об интеграциях ЭТОГО конкретного агента
    _intg_hint: list = []
    try:
        _intg_hint = _parse_agent_integrations(
            agent.get('user_api_keys', '') or '',
            agent.get('python_code', '') or '',
            agent.get('tools_allowed', '') or '',
        )
    except Exception as _e:
        logger.debug("suppressed: %s", _e)
    # Строим чёткую строку о том что подключено и КАКОЙ инструмент использовать
    # ── Универсальный _intg_line из лейблов _parse_agent_integrations ──────────
    # Не проверяем сырые api_keys — работаем с нормализованными лейблами.
    # Любая новая интеграция из _INTEGRATION_LABELS автоматически попадёт сюда.
    _intg_line = ''
    if _intg_hint:
        # Категории определяются по тексту лейбла, а не по именам ключей
        _EMAIL_LBL = ('почт', 'mail', 'imap', 'smtp', 'gmail', 'яндекс', 'resend', 'mailgun', 'sendgrid')
        _CODE_LBL  = ('github', 'gitlab', 'bitbucket', 'gitea')
        _RSS_LBL   = ('rss', 'лент', 'feed', 'новост', 'хабр')
        _MSG_LBL   = ('slack', 'discord', 'telegram', 'whatsapp', 'вконтакт', 'viber', 'teams')
        _CRM_LBL   = ('crm', 'amocrm', 'битрикс', 'hubspot', 'pipedrive', 'salesforce', 'zoho')
        _SHOP_LBL  = ('ozon', 'wildberries', 'авито', 'shopify', 'маркет', 'wb')
        _SHEETS_LBL = ('sheets', 'airtable', 'notion', 'excel', 'таблиц', 'gspread')
        _CRYPTO_LBL = ('binance', 'bybit', 'coinbase', 'крипт', 'биржев', 'alpha vantage')
        _AD_LBL    = ('директ', 'adwords', 'яндекс.директ', 'google ads', 'mytarget', 'метрик', 'analytics')
        _SOCIAL_LBL = ('twitter', 'instagram', 'linkedin', 'youtube', 'tiktok')
        _PM_LBL    = ('jira', 'trello', 'asana', 'todoist', 'clickup', 'linear')
        _PAY_LBL   = ('stripe', 'юкасс', 'платеж', 'yookassa')
        _CAL_LBL   = ('calendar', 'календар', 'zoom')
        # Маппинг категории → (emoji, инструмент)
        _CAT_MAP = [
            (_EMAIL_LBL,  '📧', 'check_emails / send_email / list_email_contacts'),
            (_CODE_LBL,   '💻', 'run_agent_action'),
            (_RSS_LBL,    '📰', 'run_agent_action'),
            (_MSG_LBL,    '💬', 'run_agent_action'),
            (_CRM_LBL,    '🤝', 'run_agent_action'),
            (_SHOP_LBL,   '🛒', 'run_agent_action'),
            (_SHEETS_LBL, '📊', 'run_agent_action'),
            (_CRYPTO_LBL, '📈', 'run_agent_action'),
            (_AD_LBL,     '📣', 'run_agent_action'),
            (_SOCIAL_LBL, '🌐', 'run_agent_action / create_post'),
            (_PM_LBL,     '📋', 'run_agent_action'),
            (_PAY_LBL,    '💳', 'run_agent_action'),
            (_CAL_LBL,    '📅', 'run_agent_action'),
        ]
        _intg_parts = []
        _seen_emojis: set = set()
        for _ih in _intg_hint[:8]:
            _ih_low = _ih.lower()
            _em, _tool = '🔧', 'run_agent_action'
            for _kws, _e, _t in _CAT_MAP:
                if any(w in _ih_low for w in _kws):
                    _em, _tool = _e, _t
                    break
            # Для почты — добавляем аккаунт из ключей если есть
            _acc = ''
            if _em == '📧' and '📧' not in _seen_emojis:
                _acc = next((
                    lk.split('=', 1)[1].strip()
                    for lk in _decrypt_keys(agent.get('user_api_keys') or '').splitlines()
                    if '=' in lk and any(
                        f'{p}_USER' in lk.upper()
                        for p in ('GMAIL', 'YANDEX', 'MAILRU', 'IMAP', 'EMAIL', 'SMTP')
                    )
                ), '')
            _label = f"{_em} {_ih}" + (f" ({_acc})" if _acc else '') + f" → {_tool}"
            _intg_parts.append(_label)
            _seen_emojis.add(_em)
        _intg_line = '\nПодключено у тебя:\n  ' + '\n  '.join(_intg_parts) if _intg_parts else ''

    # ── Telegram-канал пользователя (платформенный, доступен всем агентам) ──────
    try:
        from models import Session as _Sess_tg, User as _UTg
        _s_tg = _Sess_tg()
        try:
            _u_tg = _s_tg.query(_UTg).filter_by(telegram_id=user_id).first()
            if _u_tg and getattr(_u_tg, 'telegram_channel', None):
                _tg_ch = (_u_tg.telegram_channel or '').strip()
                _tg_entry = f"📢 Telegram-канал {_tg_ch} → publish_to_telegram(content='текст поста') ← публикуй посты, отчёты, анонсы"
                if _intg_line:
                    _intg_line += f"\n  {_tg_entry}"
                else:
                    _intg_line = f"\nПодключено у тебя:\n  {_tg_entry}"
        finally:
            _s_tg.close()
    except Exception:
        pass

    _intg_action_hint = ""
    if _intg_hint:
        _intg_names = ', '.join(_intg_hint[:8])
        # Динамическая карта возможностей — показываем только ПОДКЛЮЧЁННЫЕ интеграции
        _INTG_CAPABILITIES = {
            'rss': 'мониторинг конкурентов, поиск авторов/экспертов, подготовка контента, трендовые темы',
            'mail': 'рассылка, follow-up, нетворкинг, мониторинг ответов, персональные предложения',
            'github': 'поиск разработчиков, анализ проектов, networking через issues/PR, email из коммитов',
            'gitlab': 'поиск разработчиков, анализ проектов, networking через issues/PR',
            'crm': 'аналитика, отчёты, воронка продаж, управление лидами',
            'slack': 'мониторинг каналов, координация команды, оповещения',
            'discord': 'community building, мониторинг, постинг',
            'telegram': 'постинг, мониторинг каналов, рассылка',
            'ozon': 'аналитика продаж, остатки, заказы, выручка',
            'wildberries': 'аналитика продаж, остатки, карточки товаров',
            'shopify': 'заказы, клиенты, метрики магазина',
            'jira': 'управление задачами, спринты, отчёты',
            'trello': 'управление задачами, доски, карточки',
            'notion': 'база знаний, документация, заметки',
            'sheets': 'данные, отчёты, дашборды, аналитика',
            'binance': 'торговля, портфель, аналитика рынка',
            'bybit': 'торговля, портфель, аналитика рынка',
            'stripe': 'платежи, подписки, аналитика выручки',
            'hh': 'вакансии, кандидаты, рекрутинг',
            'twitter': 'мониторинг, постинг, поиск аудитории',
            'instagram': 'контент, аудитория, аналитика',
            'linkedin': 'нетворкинг, поиск контактов, B2B',
            'crm': 'воронка продаж end-to-end: найти контакт → создать сделку → двигать по этапам → писать заметки',
            'amocrm': 'воронка продаж, сделки и контакты по этапам',
            'bitrix': 'воронка, контакты, задачи и календарь команды',
            'hubspot': 'лиды, контакты, сделки, email-последовательности',
            'airtable': 'база данных + дашборды, записи и отчёты',
            'asana': 'задачи, проекты, драйвы, портфель задач',
            'clickup': 'задачи, документы, время, отчёты',
            'moysklad': 'товары, заказы, склад, контрагенты',
            'calendly': 'запись на встречу, календарь расписаний',
            'zoom': 'видеозвонки, конференции, запись',
        }
        # Конкретные action-имена для run_agent_action по типу интеграции
        _INTG_ACTIONS = {
            'github': (
                "run_agent_action(action='search_users', params={'query': 'language:python location:...'})\n"
                "    run_agent_action(action='search_repos', params={'query': 'topic:ai stars:>100'})\n"
                "    run_agent_action(action='get_user_info', params={'username': '...'})\n"
                "    run_agent_action(action='create_issue', params={'repo': 'owner/repo', 'title': '...', 'body': '...'})\n"
                "    run_agent_action(action='comment_on_issue', params={'repo': 'owner/repo', 'issue_number': N, 'body': '...'})"
            ),
            'gitlab': (
                "run_agent_action(action='search_users', params={'query': '...'})\n"
                "    run_agent_action(action='search_repos', params={'query': '...'})\n"
                "    run_agent_action(action='create_issue', params={'project': '...', 'title': '...'})"
            ),
            'slack': (
                "run_agent_action(action='send_message', params={'channel': '...', 'text': '...'})\n"
                "    run_agent_action(action='list_channels', params={})"
            ),
            'discord': (
                "run_agent_action(action='send_message', params={'channel_id': '...', 'content': '...'})"
            ),
            'jira': (
                "run_agent_action(action='create_issue', params={'project': '...', 'summary': '...'})\n"
                "    run_agent_action(action='search_issues', params={'jql': '...'})"
            ),
            'trello': (
                "run_agent_action(action='create_card', params={'list_id': '...', 'name': '...'})\n"
                "    run_agent_action(action='list_boards', params={})"
            ),
            'notion': (
                "run_agent_action(action='create_page', params={'database_id': '...', 'title': '...'})\n"
                "    run_agent_action(action='search', params={'query': '...'})"
            ),
            'sheets': (
                "run_agent_action(action='read_sheet', params={'range': 'A1:Z100'})\n"
                "    run_agent_action(action='add_row', params={'values': [...]})"
            ),
            'ozon': (
                "run_agent_action(action='get_products', params={})\n"
                "    run_agent_action(action='get_orders', params={'since': '...'})"
            ),
            'wildberries': (
                "run_agent_action(action='get_products', params={})\n"
                "    run_agent_action(action='get_orders', params={})"
            ),
            'binance': (
                "run_agent_action(action='get_balance', params={})\n"
                "    run_agent_action(action='get_price', params={'symbol': 'BTCUSDT'})"
            ),
            'hh': (
                "run_agent_action(action='search_vacancies', params={'text': '...'})\n"
                "    run_agent_action(action='search_resumes', params={'text': '...'})"
            ),
            'crm': (
                "run_agent_action(action='get_pipelines') — СНАЧАЛА! узнай pipeline_id и status_id реальных этапов\n"
                "    run_agent_action(action='create_lead', params={'name':'...','price':N,'pipeline_id':'<id>','status_id':'<id>'}) — сделку В НУЖНОМ ЭТАПЕ\n"
                "    run_agent_action(action='update_lead', params={'id':N,'status_id':'<id>'}) — двинуть по воронке\n"
                "    run_agent_action(action='get_contacts', params={'query':'...'}) — поиск\n"
                "    run_agent_action(action='create_contact', params={'name':'...','email':'...','phone':'...'})\n"
                "    run_agent_action(action='add_note', params={'id':N,'entity_type':'leads','text':'...'}) — заметка к сделке"
            ),
            'amocrm': (
                "run_agent_action(action='get_pipelines') — СНАЧАЛА, чтобы знать pipeline_id+status_id\n"
                "    run_agent_action(action='create_lead', params={'name':'...','pipeline_id':'<id>','status_id':'<id>'})\n"
                "    run_agent_action(action='update_lead', params={'id':N,'status_id':'<id>'})\n"
                "    run_agent_action(action='get_leads') — 10 последних сделок"
            ),
            'notion': (
                "run_agent_action(action='create_page', params={'database_id':'...','title':'...','props':{...}})\n"
                "    run_agent_action(action='search_notion', params={'query':'...'})\n"
                "    Или: http_api_request(url='https://api.notion.com/v1/pages', method='POST', headers={'Notion-Version':'2022-06-28'}, auth_key='NOTION_TOKEN', body={...})"
            ),
            'sheets': (
                "run_agent_action(action='read_sheet', params={'range':'A1:Z100'})\n"
                "    run_agent_action(action='add_row', params={'values':[...]})\n"
                "    run_agent_action(action='update_sheet', params={'range':'A2','values':[...]})"
            ),
            'airtable': (
                "http_api_request(url='https://api.airtable.com/v0/{BASE_ID}/{TABLE}', auth_key='AIRTABLE_TOKEN')\n"
                "    http_api_request(url='https://api.airtable.com/v0/{BASE_ID}/{TABLE}', method='POST', auth_key='AIRTABLE_TOKEN', body={'fields':{...}})"
            ),
            'stripe': (
                "run_agent_action(action='get_charges') — последние платежи\n"
                "    run_agent_action(action='get_revenue') — выручка за период\n"
                "    http_api_request(url='https://api.stripe.com/v1/charges', auth_key='STRIPE_KEY', auth_scheme='Bearer')"
            ),
            'moysklad': (
                "http_api_request(url='https://api.moysklad.ru/api/remap/1.2/entity/product', auth_key='MOYSKLAD_TOKEN')\n"
                "    http_api_request(url='https://api.moysklad.ru/api/remap/1.2/entity/customerorder', auth_key='MOYSKLAD_TOKEN')"
            ),
            'asana': (
                "http_api_request(url='https://app.asana.com/api/1.0/tasks', auth_key='ASANA_TOKEN')\n"
                "    http_api_request(url='https://app.asana.com/api/1.0/tasks', method='POST', auth_key='ASANA_TOKEN', body={'data':{'name':'...','projects':[...]}})"
            ),
            'clickup': (
                "http_api_request(url='https://api.clickup.com/api/v2/list/{LIST_ID}/task', method='POST', auth_key='CLICKUP_TOKEN', body={'name':'...','description':'...'})\n"
                "    http_api_request(url='https://api.clickup.com/api/v2/task/{TASK_ID}', method='PUT', auth_key='CLICKUP_TOKEN', body={'status':'...'})"
            ),
            'zoom': (
                "http_api_request(url='https://api.zoom.us/v2/users/me/meetings', method='POST', auth_key='ZOOM_TOKEN', body={'topic':'...','start_time':'...','duration':60})"
            ),
            'linkedin': (
                "http_api_request(url='https://api.linkedin.com/v2/people/', auth_key='LINKEDIN_ACCESS_TOKEN')\n"
                "    run_agent_action(action='search_profiles', params={'keywords':'...'}) — если скрипт настроен"
            ),
        }
        _connected_caps = []
        _connected_actions = []
        for _ih in _intg_hint[:8]:
            _ih_low = _ih.lower()
            for _cap_key, _cap_desc in _INTG_CAPABILITIES.items():
                if _cap_key in _ih_low:
                    _connected_caps.append(f"  • {_ih} = {_cap_desc}")
                    # Добавляем конкретные action-примеры если есть
                    if _cap_key in _INTG_ACTIONS:
                        _connected_actions.append(f"  {_ih}:\n    {_INTG_ACTIONS[_cap_key]}")
                    break
        _caps_block = '\n'.join(_connected_caps) if _connected_caps else ''
        _actions_block = '\n'.join(_connected_actions) if _connected_actions else ''
        _intg_action_hint = (
            f"\n\n🔗 Твои интеграции: {_intg_names}.\n"
            "Каждая интеграция — это НЕ один инструмент, а НАБОР ВОЗМОЖНОСТЕЙ. Думай шире:\n"
            + (_caps_block + '\n' if _caps_block else '')
            + (f"\n📋 КОНКРЕТНЫЕ ВЫЗОВЫ run_agent_action (токены УЖЕ подключены — НЕ ПРОСИ у пользователя!):\n{_actions_block}\n" if _actions_block else '')
            + "Для внешних сервисов → run_agent_action. Для email → send_outreach_email / check_emails. "
            "web_search / research_topic — универсальные, доступны всегда. "
            "Комбинируй интеграции с платформенными инструментами для максимального результата.\n"
            "Сам решай КАК использовать интеграции — исходя из задачи, цели и контекста пользователя."
        )
        # Универсальная директива: API-интеграции → приоритет над web_search
        # Работает для ЛЮБОЙ интеграции (GitHub, CRM, маркетплейсы, etc.)
        _api_intg_count = len([h for h in _intg_hint if any(
            w in h.lower() for w in ('github', 'gitlab', 'crm', 'amocrm', 'bitrix',
                'hubspot', 'jira', 'trello', 'notion', 'slack', 'discord',
                'ozon', 'wildberries', 'shopify', 'binance', 'bybit',
                'rss', 'hh.ru', 'superjob', 'sheets', 'airtable',
                'twitter', 'instagram', 'linkedin', 'stripe', 'yookassa')
        )])
        if _api_intg_count > 0:
            _intg_action_hint += (
                "\n\n🔌 ПРИОРИТЕТ API НАД WEB_SEARCH:\n"
                "У тебя подключены API-интеграции. Для задач, покрываемых ими — СНАЧАЛА run_agent_action:\n"
                "  API даёт ТОЧНЫЕ структурированные данные (контакты, метрики, статусы).\n"
                "  web_search даёт ПРИБЛИЗИТЕЛЬНЫЕ неструктурированные результаты.\n"
                "Правило: если задача покрывается подключённой интеграцией → API первым.\n"
                "  web_search → только для того, чего НЕТ в твоих интеграциях.\n"
                "Варьируй параметры каждый цикл: другие фильтры, page+1, другой запрос.\n"
                "После получения данных → конвертируй: save_email_contact, create_post, add_task."
            )

    # === Универсальный парсинг ACTION из скрипта агента (работает для любого агента) ===
    _py_code_sa = agent.get('python_code', '').strip()
    if _py_code_sa and _is_autopilot_task:
        import re as _re_sa
        _script_actions: list = []
        # Паттерн: ACTION == 'value'
        for _m_sa in _re_sa.finditer(r"ACTION\s*==\s*['\"]([^'\"]+)['\"]", _py_code_sa):
            _a = _m_sa.group(1).strip()
            if _a and _a not in _script_actions:
                _script_actions.append(_a)
        # Паттерн: ACTION in ('val1', 'val2', ...)
        for _m_sa in _re_sa.finditer(r"ACTION\s+in\s*\(([^)]+)\)", _py_code_sa):
            for _part in _m_sa.group(1).split(','):
                _a = _part.strip().strip("'\" ").strip()
                if _a and _a not in _script_actions:
                    _script_actions.append(_a)
        if _script_actions:
            _intg_action_hint += (
                "\n\n🔧 run_agent_action — скрипт поддерживает ТОЛЬКО эти action-имена: "
                + ', '.join(_script_actions)
                + ". Используй ТОЛЬКО их. Любое другое имя не распознается скриптом и вернёт пустой результат."
            )
        elif _py_code_sa:
            _intg_action_hint += (
                "\n\n🔧 run_agent_action — скрипт агента выполняется без параметра action (читает данные автоматически)."
            )

    # Последние факты от интеграций (AnchorEngine) — чтобы автопилот видел
    # не только список подключений, но и свежие сигналы/цифры за последние 24ч.
    _integration_facts_block = ""
    if _is_autopilot_task:
        try:
            from models import Session as _Sess_ia_ctx, User as _Uia_ctx, Anchor as _Anch_ctx
            import json as _json_ia_ctx
            from datetime import datetime as _dt_ia_ctx, timezone as _tz_ia_ctx, timedelta as _td_ia_ctx

            _s_ia_ctx = _Sess_ia_ctx()
            try:
                _u_ia_ctx = _s_ia_ctx.query(_Uia_ctx).filter_by(telegram_id=user_id).first()
                if _u_ia_ctx:
                    _since_ia = _dt_ia_ctx.now(_tz_ia_ctx.utc) - _td_ia_ctx(hours=24)
                    _rows_ia = (
                        _s_ia_ctx.query(_Anch_ctx)
                        .filter(
                            _Anch_ctx.user_id == _u_ia_ctx.id,
                            _Anch_ctx.anchor_type == 'integration_alert',
                            _Anch_ctx.triggered_at >= _since_ia,
                        )
                        .order_by(_Anch_ctx.triggered_at.desc())
                        .limit(8)
                        .all()
                    )
                    if _rows_ia:
                        _facts_lines = []
                        for _a in reversed(_rows_ia):
                            _svc = ''
                            _signal = ''
                            _snippet = ''
                            try:
                                _ad = _json_ia_ctx.loads(_a.data or '{}') if (_a.data or '').strip() else {}
                                if isinstance(_ad, dict):
                                    _svc = str(_ad.get('service_label') or '').strip()
                                    _signal = str(_ad.get('signal') or '').strip()
                                    _snippet = str(_ad.get('snippet') or '').strip().replace('\n', ' ')
                            except Exception:
                                pass
                            _svc = _svc or 'Интеграция'
                            _signal = _signal or (_a.topic or 'сигнал без деталей')
                            _ts = _a.triggered_at.strftime('%d.%m %H:%M') if _a.triggered_at else ''
                            _sn = (_snippet[:140] + '…') if len(_snippet) > 140 else _snippet
                            _line = f"  • [{_ts}] {_svc}: {_signal}"
                            if _sn:
                                _line += f" | {_sn}"
                            _facts_lines.append(_line)
                        if _facts_lines:
                            _integration_facts_block = (
                                "\n📥 ПОСЛЕДНИЕ СИГНАЛЫ ИНТЕГРАЦИЙ (24ч, для сверки):\n"
                                + "\n".join(_facts_lines)
                                + "\n"
                            )
            finally:
                _s_ia_ctx.close()
        except Exception as _ia_ctx_e:
            logger.debug('[DIRECTOR-EXEC] integration facts load: %s', _ia_ctx_e)

    if _is_autopilot_task:
        # ── Компактный autopilot system prompt ──
        # Принцип: минимум правил, максимум конкретики.
        # Агент — живой специалист: вызывает инструменты СВОЕЙ специализации,
        # отчитывается фактами, делегирует коллегам через DELEGATE[].
        import re as _re_team
        _team_section = ''
        _team_match = _re_team.search(r'ТВОЯ КОМАНДА[^\n]*\n((?:  [•\-].*\n)*)', task or '')
        if _team_match:
            _team_section = _team_match.group(0).strip()
        elif 'ТВОЯ КОМАНДА:' in (task or ''):
            _m2 = _re_team.search(r'ТВОЯ КОМАНДА:[^\n]+', task or '')
            _team_section = _m2.group(0) if _m2 else ''
        _colleague_names = _re_team.findall(r'•\s+(\S+)\s+\(', _team_section) if _team_section else []
        _delegate_example = ''
        if _colleague_names:
            _fn = _colleague_names[0]
            _delegate_example = f"{'Нашла' if _is_fem else 'Нашёл'} данные для коллеги → DELEGATE[{_fn}]: задача с конкретными данными."

        # Базовые знания о продукте из knowledge_base агента
        _kb_block = ''
        _kb_raw = agent.get('knowledge_base') or ''
        if _kb_raw:
            try:
                import json as _kb_json
                _kb_items = _kb_json.loads(_kb_raw) if _kb_raw.strip().startswith('[') else []
                _kb_lines = []
                for _item in _kb_items[:8]:
                    if isinstance(_item, dict):
                        _kbtype = _item.get('type', '')
                        _kbname = _item.get('name', '')
                        _kbcontent = _item.get('content') or _item.get('url') or ''
                        if _kbcontent:
                            _kb_lines.append(f"  [{_kbtype}] {_kbname}: {str(_kbcontent)[:200]}")
                if _kb_lines:
                    _kb_block = "\n📚 База знаний (используй в отчётах, письмах, ссылках):\n" + '\n'.join(_kb_lines) + '\n'
            except Exception:
                # Если не JSON — используем как есть (просто текст)
                if len(_kb_raw) < 1000:
                    _kb_block = f"\n📚 База знаний:\n{_kb_raw[:800]}\n"

        # Примеры отчёта с правильным родом (по _is_fem)
        _ex_found   = 'Нашла' if _is_fem else 'Нашёл'
        _ex_checked = 'Проверила' if _is_fem else 'Проверил'
        _ex_added   = 'добавила' if _is_fem else 'добавил'
        _ex_wrote   = 'написала' if _is_fem else 'написал'
        # Название проекта/компании из профиля агента (передаётся из контекста пользователя)
        _company_ctx = (agent.get('company') or '').strip()
        _team_ctx = f"команде {_company_ctx}" if _company_ctx else 'команде'
        system_prompt = (
            f"Ты — {agent['name']}, {agent.get('job_title') or agent.get('specialization', 'специалист')}. "
            f"Работаешь в {_team_ctx}. Сейчас: {_now_str}.\n"
            f"{_intg_line}\n"
            f"{_kb_block}\n"
            f"{_integration_facts_block}\n"

            "🧠 АЛГОРИТМ РАБОТЫ (каждый цикл):\n\n"

            "ШАГ 1 — ДУМАЙ: что даст максимальный результат прямо сейчас?\n"
            "  Спроси себя: Что конкретно я сделаю? Какой инструмент вызову? Что получит пользователь?\n"
            "  Это реально двигает цель или имитация работы?\n"
            "  Что я уже пробовал раньше? Повторять провальный подход — ошибка. 0% прогресса = нужен ДРУГОЙ подход.\n\n"

            "ШАГ 2 — ДЕЙСТВУЙ: вызови инструмент, получи результат.\n"
            "  Цепочка ценности — доведи до конца:\n"
            "  найди (web_search/research) → сохрани (save_email_contact) → примени (email/пост/задача) → update_goal_progress\n"
            "  Один поиск без продолжения = ноль. Информация без применения бесполезна.\n"
            "  Ошибка → другой инструмент. Пустой результат → другой запрос, канал, подход.\n"
            f"  {'Нашла' if _is_fem else 'Нашёл'} email → save_email_contact → send_outreach_email (персональное письмо, тема 4-8 слов о получателе).\n"
            f"  {'Нашла' if _is_fem else 'Нашёл'} профиль/ник без email → ищи контакт через ИНСТРУМЕНТ той платформы, где нашёл человека:\n"
            "     GitHub-профиль → run_agent_action search_github_users (email из commits/профиля)\n"
            "     AmoCRM-контакт → run_agent_action amocrm_get_contact или list_email_contacts\n"
            "     Telegram-канал → run_agent_action telegram_get_channel или web_search «имя + сайт»\n"
            "     Habr/DTF/VC профиль → web_search «имя + личный сайт OR GitHub OR LinkedIn» (не «имя email contact»)\n"
            "  ⛔ web_search «ник/логин email contact» ВСЕГДА даёт 0 — это потеря хода. Используй инструмент платформы или ищи сайт человека.\n"
            "  Нет нужного инструмента → DELEGATE[Коллега]: конкретная задача + передай ВСЕ данные что ты уже нашёл.\n"
            "  💡 ДЕЛЕГИРОВАНИЕ — это не передача задачи, а передача РАБОТЫ + ДАННЫХ.\n"
            "     Коллега не видит твои tool-ответы. Если не передашь данные — он начнёт с нуля и найдёт не то.\n"
            "     ❌ ПЛОХО: DELEGATE[Кристина]: найди контакты автора.\n"
            "     ✅ ХОРОШО: DELEGATE[Кристина]: найди email Андрея Ерёменка (Хабр: https://habr.com/..., CTO, СПб).\n"
            "        Попробуй его GitHub (run_agent_action search_github_users) или личный сайт, сохрани через save_email_contact.\n\n"

            "ШАГ 3 — ОТЧИТАЙСЯ: только факты, 120-250 символов.\n"
            "  Сплошной текст, одинарный перенос. Без маркеров, списков, заголовков, **жирного**.\n"
            f"  Перечисляй через запятую: «{_ex_found.lower()} X, {'отправила' if _is_fem else 'отправил'} Y, {'опубликовала' if _is_fem else 'опубликовал'} Z».\n"
            f"  Пиши от первого лица: «Я {_ex_found.lower()}», не «{agent['name']} {_ex_found.lower()}».\n"
            "  Каждое предложение = действие + конкретика (имена, email, цифры, ссылки).\n"
            f"  Пример: «{_ex_found} 3 автора на Хабре, {_ex_added} контакты, {_ex_wrote} каждому письмо».\n\n"

            "ПРАВИЛА:\n"
            "Каждый ход — вызови инструмент. Текст без tool-вызова = провал.\n"
            "update_goal_progress — ПОСЛЕДНИЙ tool call каждой сессии, progress = абсолют % (0-100). Без него работа не засчитана.\n"
            "  📊 ЦЕЛИ: список активных целей передан в контексте задачи. Сопоставь по теме сам. "
            "Если одна цель — обновляй её. Если несколько и тема совпадает — выбери подходящую. "
            "Уточняй у пользователя только если целей несколько и тема совсем неочевидна.\n"
            "Контакты: save_email_contact для личных email И для корп. адресов профильного отдела (B2B-цель).\n"
            "  ✅ Личный (name@, firstname.surname@) — всегда.\n"
            "  ✅ Корп. профильный (bioinformatics@, partners@, press@media@) — если цель B2B или публикация.\n"
            "  ✅ info@/contact@ маленького стартапа (≤20 чел) — основатель скорее всего читает сам.\n"
            "  ❌ info@/support@/hello@ крупной компании — автоответ, не трать время.\n"
            "  ❌ Placeholder (test@, name@example.com) и уже сохранённые. Не путай имена и email.\n"
            f"{'Нашла' if _is_fem else 'Нашёл'} событие → проверь дату ({_now_str}): прошедшее не рекомендуй, но используй контакты спикеров.\n"
            "Публикации (Discord/Telegram/пост) — пиши для аудитории как эксперт, не как ассистент владельцу.\n"
            "[INTERNAL] сообщения о лимитах — молча переключись на другое действие.\n"
            "save_note = итоговый вывод с конкретными данными (список контактов/email, аналитика, ключевые факты). "
            "НЕ сохраняй: 'нашла инструмент', 'запустила поиск', 'обновила прогресс' — это не ценность. "
            "НЕ сохраняй нулевые результаты: 'нет новых ответов', 'почта пуста', 'не нашлось ничего' — эти записи засоряют заметки, делай другое действие. "
            "Ценность = что-то чем можно воспользоваться позже: contact@mail.ru + имя + контекст, или 'топ-3 канала с конверсией X%'.\n"
            "📝 save_note → обязательно выведи полный текст заметки в ответ пользователю, не только «сохранил». "
            "Пользователь должен прочитать содержимое прямо в чате, а не искать его отдельно.\n"
