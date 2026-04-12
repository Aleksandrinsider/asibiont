#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AnchorEngine — единая событийная система автономного агента.

Заменяет:
- timer-based проактивные сообщения (chat.py _build_situation_prompt, 15+ типов)
- contact_alerts_service.py
- auto_post_service.py (триггеры)
- context_builder.py алерты

Принцип работы:
1. SCAN  — каждые 15-30 мин сканирует ВСЕ источники данных, создаёт якоря
2. EVALUATE — AI получает сработавшие якоря + полный контекст, РЕШАЕТ писать или нет
3. DELIVER — отправляет ОДНО сообщение (не шаблон — AI пишет с нуля)
4. FEEDBACK — отслеживает реакцию пользователя, адаптирует частоту

Антиспам (живая динамика, НЕ блокировка):
- CRITICAL/HIGH: доставляются ВСЕГДА (кроме DND/ночь), не считаются в лимите
- MEDIUM: обычный cooldown 3ч, лимит 6 диалогов/день
- LOW: cooldown 8ч, отключаются при ignore rate >70%
- Посты в ленту: отдельный лимит 2/день
- Посты в канал: отдельный лимит 1/день
- Min gap 10 мин между проактивными (но не для CRITICAL)
- DND, ночные часы — единственный полный блок
- Макс 6 диалоговых + 2 feed + 1 channel = 9 касаний/день
"""

import asyncio
import json
import os
import time
import logging
import re
import traceback
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import pytz

from sqlalchemy import text

from models import (
    Session, User, UserProfile, Task, Goal, Interaction, Post,
    Anchor, AnchorDeliveryLog, AnchorPriority,
    ActivityAlert, ContactAlert, UserMessage,
    EmailCampaign, EmailOutreach, ContentCampaign,
    DelegationCampaign, AgentActivityLog,
)
from config import DEEPSEEK_API_KEY, DEFAULT_OUTREACH_EMAIL, PROACTIVE_NO_SEND_START_HOUR, PROACTIVE_SEND_START_HOUR, redact_email

logger = logging.getLogger(__name__)

# Telegram message limit
_TG_MAX_LEN = 4096


# ═══════════════════════════════════════════════════════
# USER-LEVEL CHANNEL ENRICHMENT
# ═══════════════════════════════════════════════════════
# Добавляет каналы пользователя (Discord, Telegram) к agent_caps.
# Эти каналы хранятся на уровне users, а не user_agents,
# поэтому _parse_agent_integrations() их не видит.

def _enrich_caps_with_user_channels(caps: list, user=None) -> list:
    """Дополняет список интеграций агента user-level каналами.
    
    Для добавления нового канала: добавить поле в models.User + строку ниже.
    """
    if not user:
        return caps
    enriched = set(caps)
    # (атрибут модели User, label для интеграции)
    _USER_CHANNELS = [
        ('discord_webhook', 'Discord (канал пользователя)'),
        ('telegram_channel', 'Telegram-канал (канал пользователя)'),
    ]
    for attr, label in _USER_CHANNELS:
        if getattr(user, attr, None):
            enriched.add(label)
    return sorted(enriched)


# ═══════════════════════════════════════════════════════
# UNIFIED CAPABILITY CLASSIFICATION
# ═══════════════════════════════════════════════════════
# Единая классификация возможностей агента.
# Принимает output _parse_agent_integrations() и возвращает структурированный dict.
# Используется во ВСЕХ промптах: автопилот, координатор, план координатора.

# Маппинг: подстроки в label (lowercase) → категория
_CAP_CATEGORY_MAP: list[tuple[tuple[str, ...], str]] = [
    # Email / почта (включая Outlook/MailRu/Yandex)
    (('mail', 'почт', 'email', 'imap', 'smtp', 'gmail', 'yandex', 'mailru', 'resend', 'sendgrid', 'mailgun', 'sparkpost', 'outlook', 'microsoft outlook', 'ms_outlook'), 'email'),
    # GitHub/GitLab
    (('github', 'gitlab'), 'git'),
    # RSS
    (('rss', 'feed', 'лент'), 'rss'),
    # Telegram
    (('telegram',), 'telegram'),
    # Discord
    (('discord',), 'discord'),
    # CRM
    (('crm', 'amocrm', 'битрикс', 'bitrix', 'hubspot', 'salesforce'), 'crm'),
    # Маркетплейсы
    (('ozon', 'wildberries', 'shopify', 'авито', 'avito', 'маркетплейс', 'яндекс.маркет'), 'marketplace'),
    # Проджект-менеджмент
    (('jira', 'trello', 'asana', 'clickup', 'linear', 'todoist'), 'pm'),
    # Notion / Wiki
    (('notion',), 'notion'),
    # Google Sheets / Таблицы
    (('sheets', 'gsheets', 'spreadsheet', 'google sheets'), 'sheets'),
    # Крипто-биржи
    (('binance', 'bybit', 'coinbase', 'крипт'), 'crypto'),
    # Финансовые данные
    (('alpha vantage', 'alphavantage', 'биржевые', 'котировк', 'finnhub', 'polygon.io', 'polygon (биржев', 'twelve data', 'twelvedata', 'yahoo finance', 'financial modeling'), 'finance'),
    # Новостные API
    (('newsapi', 'тасс'), 'news'),
    # Slack
    (('slack',), 'slack'),
    # Соцсети
    (('twitter', 'x.com', 'instagram', 'linkedin', 'youtube', 'вконтакт', 'vk'), 'social'),
    # Платежи
    (('stripe', 'юкасс', 'yookassa', 'платеж'), 'payments'),
    # Календарь / Встречи
    (('calendar', 'календар', 'zoom', 'google calendar'), 'calendar'),
    # Телефония / Звонки / WhatsApp
    (('twilio', 'sms', 'звонк', 'call', 'sipuni', 'voip', 'voximplant', 'phone', 'whatsapp', 'waba'), 'calls'),
    # Python / HTTP / Custom
    (('python', 'http', 'скрипт', 'run_agent', 'все инструменты', 'custom api'), 'script'),
    # Генерация изображений
    (('replicate', 'генерация изображен', 'изображен'), 'image_gen'),
    # Хранилища
    (('s3', 'amazon', 'azure', 'google drive', 'яндекс.диск'), 'storage'),
    # Аналитика
    (('google analytics', 'яндекс.метрик', 'ga4'), 'analytics'),
    # Мессенджеры / Команда
    (('ms teams', 'microsoft teams', 'microsoft graph'), 'ms_teams'),
    # Webhook / Автоматизация
    (('webhook', 'n8n', 'zapier', 'make'), 'automation'),
    # БД (включает Firebase/Firestore/Pinecone)
    (('postgresql', 'mysql', 'mongodb', 'sqlite', 'redis', 'firebase', 'firestore', 'pinecone', 'векторн'), 'database'),
    # HR / Биржи труда
    (('hh.ru', 'headhunter', 'superjob', 'hh_query', 'hh_area'), 'hr'),
    # Реклама
    (('яндекс.директ', 'yandex_direct', 'direct_token', 'yandex direct'), 'advertising'),
    # Web Scraping
    (('playwright', 'selenium', 'scrape_url', 'scraping', 'браузерн'), 'scraping'),
    # AI/LLM API (внешние)
    (('openai_api', 'openai_key', 'openai', 'gemini', 'anthropic', 'claude', 'gpt', 'deepseek'), 'ai_api'),
    # Airtable (расширение категории sheets)
    (('airtable',), 'sheets'),
    # CoinGecko (крипто-данные)
    (('coingecko', 'coingeck'), 'crypto'),
    # МойСклад (маркетплейс/склад)
    (('мойсклад', 'moysklad'), 'marketplace'),
    # Calendly (встречи/расписание)
    (('calendly',), 'calendar'),
    # Figma (дизайн/прототипы — через HTTP API)
    (('figma',), 'script'),
    # OpenWeatherMap и другие погодные / IoT data APIs
    (('openweather', 'погода (openweather', 'погода', 'weather api', 'openweathermap'), 'script'),
    # ClickUp, Linear — PM-трекеры (могут распознаваться из python)
    (('clickup', 'click up'), 'pm'),
    (('linear app', ' linear '), 'pm'),
    # Дополнительные интеграции
    (('1с', '1c', 'onec'), 'marketplace'),
    (('тинькофф', 'tinkoff'), 'finance'),
    (('сдэк', 'cdek', 'sdek'), 'marketplace'),
    (('aviasales',), 'script'),
    (('tutu',), 'script'),
    # Анализ данных (pandas и т.п.)
    (('pandas', 'анализ данных'), 'analytics'),
    # Google API (общий, без конкретного сервиса)
    (('google api',), 'script'),
]

# Русские названия для категорий (для вывода в промптах)
_CAP_CATEGORY_NAMES: dict[str, str] = {
    'email': 'Email', 'git': 'GitHub/GitLab', 'rss': 'RSS', 'telegram': 'Telegram',
    'discord': 'Discord', 'crm': 'CRM', 'marketplace': 'Маркетплейс', 'pm': 'Трекер задач',
    'notion': 'Notion', 'sheets': 'Google Sheets', 'crypto': 'Крипто-биржа',
    'finance': 'Финансовые данные', 'news': 'Новости', 'slack': 'Slack',
    'social': 'Соцсети', 'payments': 'Платежи', 'calendar': 'Календарь/Zoom',
    'calls': 'Звонки/SMS', 'script': 'Скрипт агента', 'image_gen': 'Генерация изображений',
    'storage': 'Облачное хранилище', 'analytics': 'Аналитика', 'ms_teams': 'MS Teams',
    'automation': 'Webhook/Автоматизация', 'database': 'БД',
    'hr': 'HR / Работа', 'advertising': 'Реклама', 'scraping': 'Web Scraping', 'ai_api': 'AI/LLM API',
}

# Инструменты по категории (для координатора)
_CAP_TOOL_HINTS: dict[str, str] = {
    'email': 'check_emails, send_outreach_email, reply_to_outreach_email, find_relevant_contacts_for_task',
    'git': 'run_agent_action(action="create_issue", params={"title":"...","body":"..."}) или http_api_request(url="https://api.github.com/repos/OWNER/REPO/issues", method="POST", auth_key="GITHUB_TOKEN")',
    'rss': 'run_agent_action(точное action-имя RSS-скрипта агента), get_news_trends, create_post',
    'telegram': 'publish_to_telegram, create_post',
    'discord': 'publish_to_discord',
    'crm': 'run_agent_action(action="create_lead"|"update_lead"|"get_pipelines"|"search_contacts"|"link_contact"|"get_contacts") — создание, ВЕДЕНИЕ и закрытие сделок, связывание контактов, движение по воронке',
    'marketplace': 'run_agent_action(action="get_products"|"get_orders"|"update_stock"|"update_price"|"get_reviews") — мониторинг товаров, заказов, остатков, цен, отзывов и ВЕДЕНИЕ ассортимента',
    'pm': 'run_agent_action(action="create_issue"|"update_issue"|"get_issues"|"create_card"|"move_card"|"get_board") — создание, ТРЕКИНГ и обновление задач/карточек по статусам',
    'notion': 'run_agent_action(action="add_page"|"update_page"|"query_db"|"get_page") — создание, ОБНОВЛЕНИЕ и поиск страниц/баз',
    'sheets': 'run_agent_action(action="append_row"|"update_cell"|"get_range"|"find_row") — чтение, запись, обновление и поиск данных в таблицах',
    'crypto': 'run_agent_action или http_api_request(url="https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd")',
    'finance': 'get_stock_price(symbol, data_type) — Alpha Vantage; run_agent_action(action="get_price"|"get_quote"|"get_volume") — Finnhub/Polygon.io/Twelve Data; http_api_request — любой финансовый API',
    'news': 'run_agent_action(action="get_news", query="..."), get_news_trends',
    'slack': 'run_agent_action(action="send_message") или http_api_request(url="https://slack.com/api/chat.postMessage", method="POST", auth_key="SLACK_TOKEN")',
    'social': 'run_agent_action(action="post_wall") или http_api_request — VK, Twitter и др. по REST API',
    'payments': 'run_agent_action(action="create_payment_link") или http_api_request — Stripe, YooKassa и др. по REST API',
    'calendar': 'run_agent_action(action="create_event"|"get_events"|"update_event"|"delete_event") — создание, просмотр, обновление и отмена событий/встреч',
    # WhatsApp: run_agent_action(action="send_message"), Twilio SMS: action="send_sms", телефония: action="send"
    'calls': 'run_agent_action(action="send_message"|"send_sms"|"send") или http_api_request — WhatsApp/Twilio/телефония по REST API',
    'script': 'run_agent_action(action="ДЕЙСТВИЕ") — конкретные action-имена см. в профиле агента',
    'image_gen': 'generate_image(prompt="...")',
    'storage': 'run_agent_action(action="upload"|"download"|"list_files") или http_api_request — S3/GCS/Dropbox по REST API',
    'analytics': 'run_agent_action(action="get_metrics"|"get_report") или http_api_request — Google Analytics, Metrica и др.',
    'ms_teams': 'run_agent_action(action="send_message") или http_api_request(url="https://graph.microsoft.com/v1.0/...", auth_key="MS_GRAPH_TOKEN")',
    'automation': 'run_agent_action(action="trigger") или http_api_request — Zapier/Make/n8n webhooks',
    'database': 'run_agent_action(action="query"|"insert") или http_api_request — REST API баз данных (Supabase, Airtable и др.)',
    'hr': 'run_agent_action(action="search_vacancies"|"get_resumes"|"update_status"|"get_candidates") — поиск, трекинг кандидатов по стадиям, обновление статусов',
    'advertising': 'run_agent_action(action="get_campaigns"|"update_campaign"|"get_stats"|"create_campaign") — создание, мониторинг и оптимизация рекламных кампаний',
    'scraping': 'run_agent_action — скрейпит URL по CSS-селектору',
    'ai_api': 'run_agent_action(action="ask"|"analyze") или http_api_request — любой AI API (OpenAI, Anthropic и др.)',
}

# ── Универсальная модель: единый источник правды для целей × интеграций ──

# Ключевые слова цели → категории интеграций (scoring categories)
_GOAL_KW: dict[str, tuple[str, ...]] = {
    'email': ('email', 'письм', 'outreach', 'рассылк'),
    'github': ('github', 'код', 'разработ', 'developer', 'репозитор', 'программист'),
    'rss': ('rss', 'новост', 'хабр', 'feed', 'мониторинг'),
    'content': ('пост', 'контент', 'публик', 'telegram', 'discord', 'smm', 'канал', 'подписчик'),
    'finance': ('нефт', 'газ', 'биржа', 'акци', 'финанс', 'трейдинг', 'инвест', 'рынок', 'котировк', 'крипт'),
    'crm': ('crm', 'клиент', 'воронк', 'лид', 'сделк', 'продаж'),
    'marketplace': ('маркетплейс', 'ozon', 'wildberries', 'товар', 'магазин'),
    'project': ('проект', 'kanban', 'спринт'),
    'analytics': ('аналитик', 'отчёт', 'таблиц', 'данные', 'метрик', 'дашборд'),
    'hr': ('найм', 'вакансия', 'резюме', 'рекрутинг', 'кандидат', 'собеседов'),
}

# Маппинг: интеграция агента (CAP category) → scoring category
_CAP_TO_SCORE: dict[str, str] = {
    'email': 'email', 'git': 'github', 'rss': 'rss',
    'telegram': 'content', 'discord': 'content', 'slack': 'content',
    'social': 'content', 'ms_teams': 'content',
    'crm': 'crm', 'marketplace': 'marketplace',
    'pm': 'project', 'notion': 'project',
    'sheets': 'analytics', 'analytics': 'analytics',
    'crypto': 'finance', 'finance': 'finance',
    'news': 'rss', 'payments': 'finance',
    'calendar': 'project', 'calls': 'content',
    'script': 'search', 'image_gen': 'content',
    'storage': 'analytics', 'automation': 'project',
    'database': 'analytics', 'hr': 'hr',
    'advertising': 'content', 'scraping': 'search',
    'ai_api': 'search',
}

# Шаблоны действий по категории интеграции (scoring category).
# {name} = имя агента, {goal} = название цели
# ВАЖНО: эти шаблоны проходят через sanitize_live_team_chat_text, который
# переводит tool names (check_emails→"проверка почты") — НЕ используй snake_case tool names!
_CAT_ACTIONS: dict[str, list[str]] = {
    'email': [
        '{name}, проверь почту — если кто-то ответил на наши письма, напиши содержательный ответ с конкретным предложением о сотрудничестве. Если контакты молчат 2+ дня — отправь follow-up с новым аргументом (не повторяй прошлое письмо).',
        '{name}, найди 2-3 новых контакта по «{goal}», сохрани их и каждому отправь персональное письмо с упоминанием их проекта/публикации.',
        '{name}, через поиск найди 3 email специалистов по теме «{goal}» — ищи на GitHub, в публикациях, среди спикеров конференций. Сохрани контакты.',
    ],
    'content': [
        '{name}, создай пост по теме «{goal}» — конкретный факт или инсайт, без воды.',
        '{name}, найди 3 свежих тренда по «{goal}» и подготовь контент-план на неделю.',
        '{name}, проверь какие посты по «{goal}» набрали больше охвата — адаптируй стратегию.',
    ],
    'github': [
        '{name}, найди на GitHub 3-5 активных разработчиков по теме «{goal}» — с email в профиле. Сохрани контакты и отправь каждому письмо.',
        '{name}, найди на GitHub авторов популярных репозиториев по «{goal}» (>50 звёзд) — сохрани их контакты и напиши каждому персональное письмо с упоминанием их проекта.',
        '{name}, найди на GitHub контрибьюторов открытых проектов по «{goal}» — кто активно коммитит, у кого есть публичный email. Сохрани контакты.',
    ],
    'rss': [
        '{name}, проверь свою RSS-ленту — найди 3 свежих материала по «{goal}» и зафиксируй ключевые выводы.',
        '{name}, собери дайджест по теме «{goal}» из своей RSS-ленты и создай пост с обзором.',
    ],
    'crm': [
        '{name}, проверь воронку по «{goal}» — переведи зависшие >3 дней лиды на следующий этап или зафиксируй блокер.',
        '{name}, обнови статусы сделок по «{goal}» и зафиксируй конкретные следующие шаги для каждой.',
        '{name}, свяжи новые контакты со сделками: создай лиды, привяжи контакты, проверь pipeline.',
    ],
    'finance': [
        '{name}, проверь актуальные рыночные данные по «{goal}» и зафиксируй изменения за последнюю неделю.',
        '{name}, проанализируй финансовые показатели по «{goal}» — выдели 3 ключевых тренда.',
    ],
    'analytics': [
        '{name}, проанализируй данные по «{goal}» — выдели 3 метрики, которые изменились за неделю.',
        '{name}, создай краткий отчёт по прогрессу «{goal}»: что сделано, что блокирует, план на неделю.',
    ],
    'marketplace': [
        '{name}, проверь позиции, отзывы и остатки по «{goal}» — если есть негатив, предложи ответ.',
        '{name}, проанализируй конкурентов по «{goal}» — цены, рейтинги, ассортимент. Результат: таблица сравнения.',
        '{name}, обнови цены/остатки по товарам «{goal}» если данные устарели более чем на 3 дня.',
    ],
    'project': [
        '{name}, зафиксируй текущий статус «{goal}»: что сделано (с цифрами), что блокирует, следующий конкретный шаг.',
        '{name}, создай задачу с конкретным шагом по «{goal}», дедлайном и ответственным.',
        '{name}, проверь зависшие >2 дней задачи — обнови статус, раздели крупные на подзадачи.',
    ],
    'hr': [
        '{name}, найди 3 новых кандидата по «{goal}» — с контактами. Отправь каждому персональное сообщение.',
        '{name}, подготовь обзор рынка кандидатов по «{goal}» — уровень зарплат, доступность, ключевые площадки.',
        '{name}, проверь воронку кандидатов — кто ждёт ответа >2 дней? Напиши follow-up или обнови статус.',
    ],
    'search': [
        '{name}, исследуй тему «{goal}» через research_topic — найди 3 конкретных инсайта с именами и ссылками, сохрани через save_note.',
        '{name}, найди лидеров мнений по «{goal}» через web_search — кто публикуется, где выступает. Сохрани имена и контакты через save_note.',
    ],
}

_UNIVERSAL_ACTIONS: list[str] = [
    '{name}, исследуй конкурентов по «{goal}» через web_search — кто лидер, какие методы используют. Сохрани через save_note: 3 конкретных тактики которые можно применить у нас.',
    '{name}, найди 3 свежих публикации или события по «{goal}» через research_topic за последний месяц. Для каждой — имя автора, ссылка, ключевой вывод. Сохрани через save_note.',
    '{name}, найди новый канал продвижения для «{goal}» через web_search который ещё не пробовали. Сохрани через save_note: название канала + план из 3 конкретных шагов.',
]


def _agent_score_cats(agent_raw_cats: set) -> set:
    """Переводит CAP-категории агента в scoring-категории."""
    return {_CAP_TO_SCORE.get(c, c) for c in agent_raw_cats}


def _goal_needs(goal_text: str) -> set:
    """Определяет какие scoring-категории нужны для цели."""
    gt = goal_text.lower()
    needs = set()
    for cat, kws in _GOAL_KW.items():
        if any(w in gt for w in kws):
            needs.add(cat)
    if any(w in gt for w in ('тестировщик', 'пользовател', 'участник', 'контакт', 'людей', 'человек')):
        needs.update(('github', 'email', 'hr'))
    if any(w in gt for w in ('поиск', 'исследов', 'найти', 'search', 'analyz')):
        needs.add('search')
    if not needs:
        needs.add('search')
    return needs


def _build_fb_strategies(agent_name: str, goal_label: str,
                         agent_cats: set, goal_text: str,
                         recent_texts: list[str] | None = None) -> list[str]:
    """Универсальный построитель fallback-стратегий.

    Не знает о «типах целей». Строит стратегии по пересечению
    scoring-категорий цели × интеграций агента.
    recent_texts — тексты недавних назначений; стратегии, похожие на них, отфильтровываются.
    """
    import random as _rnd
    _sc = _agent_score_cats(agent_cats)
    _needs = _goal_needs(goal_text)

    # Приоритет: категории, которые И цель требует, И агент имеет
    _matched = _needs & _sc
    _other = _sc - _needs

    strategies: list[str] = []
    # 1) Действия по совпавшим категориям
    for cat in sorted(_matched):
        for tpl in _CAT_ACTIONS.get(cat, []):
            strategies.append(tpl.format(name=agent_name, goal=goal_label))
    # 2) Добивка из других категорий агента (по 1 действию)
    if len(strategies) < 3:
        for cat in sorted(_other):
            for tpl in _CAT_ACTIONS.get(cat, [])[:1]:
                strategies.append(tpl.format(name=agent_name, goal=goal_label))
    # 3) Универсальный fallback
    if not strategies:
        strategies = [t.format(name=agent_name, goal=goal_label) for t in _UNIVERSAL_ACTIONS]

    # ── Фильтр: убираем стратегии, похожие на недавние назначения ──
    if recent_texts:
        _recent_lower = [t.lower() for t in recent_texts if t]
        _filtered = []
        for s in strategies:
            s_lower = s.lower()
            s_words = {w for w in s_lower.split() if len(w) > 3}
            _is_dup = False
            for rt in _recent_lower:
                rt_words = {w for w in rt.split() if len(w) > 3}
                if s_words and rt_words:
                    _ovlp = len(s_words & rt_words) / max(min(len(s_words), len(rt_words)), 1)
                    if _ovlp > 0.45:
                        _is_dup = True
                        break
            if not _is_dup:
                _filtered.append(s)
        if _filtered:
            strategies = _filtered

    _rnd.shuffle(strategies)
    return strategies[:3]


def _classify_agent_caps(detected_labels: list[str]) -> dict:
    """Классифицирует output _parse_agent_integrations() в структурированный словарь.

    Returns:
        {
            'categories': set of category keys (e.g. {'email', 'git', 'rss'}),
            'labels': original detected_labels,
            'labels_str': comma-joined labels,
            'categories_str': comma-joined category names,
            'tool_hints': dict mapping categories → tool hint strings,
        }
    """
    cats: set[str] = set()
    labels_lower = [str(l).lower() for l in (detected_labels or [])]

    for kws, cat in _CAP_CATEGORY_MAP:
        if any(kw in label for label in labels_lower for kw in kws):
            cats.add(cat)

    # Channels from user (telegram_channel, discord_webhook) — добавляются caller'ом отдельно
    tool_hints = {c: _CAP_TOOL_HINTS.get(c, '') for c in cats if c in _CAP_TOOL_HINTS}

    return {
        'categories': cats,
        'labels': detected_labels or [],
        'labels_str': ', '.join(detected_labels[:8]) if detected_labels else '',
        'categories_str': ', '.join(_CAP_CATEGORY_NAMES.get(c, c) for c in sorted(cats)),
        'tool_hints': tool_hints,
    }


def _extract_python_actions(python_code: str | None) -> list[str]:
    if not python_code:
        return []
    actions: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"ACTION\s*==\s*['\"]([^'\"]+)['\"]", python_code):
        action = (match.group(1) or '').strip()
        action_lower = action.lower()
        if action and action_lower not in seen:
            actions.append(action)
            seen.add(action_lower)
    for match in re.finditer(r"ACTION\s+in\s*\(([^)]+)\)", python_code):
        for part in match.group(1).split(','):
            action = part.strip().strip("'\" ")
            action_lower = action.lower()
            if action and action_lower not in seen:
                actions.append(action)
                seen.add(action_lower)
    return actions


def _tokenize_semantic_text(text_value: str) -> set[str]:
    return {
        token for token in re.findall(r'[a-zA-Zа-яА-Я0-9_]{3,}', (text_value or '').lower())
        if len(token) >= 3
    }


def _build_capability_profiles(detected_labels: list[str] | None,
                               python_code: str | None = None) -> list[dict]:
    caps = _classify_agent_caps(detected_labels or [])
    labels = detected_labels or []
    labels_lower = [str(label).lower() for label in labels]
    py_actions = _extract_python_actions(python_code)
    profiles: list[dict] = []
    matched_labels: set[str] = set()

    for category in sorted(caps.get('categories', set())):
        category_name = _CAP_CATEGORY_NAMES.get(category, category)
        tool_hint = _CAP_TOOL_HINTS.get(category, '')
        category_keywords = next((kws for kws, cat in _CAP_CATEGORY_MAP if cat == category), tuple())
        related_labels = [label for label, low in zip(labels, labels_lower) if any(kw in low for kw in category_keywords)]
        matched_labels.update(related_labels)
        related_actions = [
            action for action in py_actions
            if any(kw in action.lower() for kw in category_keywords)
        ]
        route = tool_hint or (
            f"run_agent_action(action=\"{related_actions[0]}\")"
            if related_actions else
            'run_agent_action(точное action-имя из профиля агента)'
        )
        score_text = ' '.join(filter(None, [category_name, tool_hint, ' '.join(related_labels), ' '.join(related_actions)]))
        profiles.append({
            'key': category,
            'display': category_name,
            'route': route,
            'score_text': score_text,
            'matched_labels': related_labels,
            'actions': related_actions,
        })

    for label in labels:
        if label in matched_labels:
            continue
        profiles.append({
            'key': f'label:{label.lower()}',
            'display': label,
            'route': 'run_agent_action(точное action-имя из профиля агента)',
            'score_text': label,
            'matched_labels': [label],
            'actions': [],
        })

    if py_actions:
        profiles.append({
            'key': 'custom_actions',
            'display': 'Кастомные action',
            'route': 'run_agent_action(action="...") по точным action-именам скрипта',
            'score_text': ' '.join(py_actions),
            'matched_labels': [],
            'actions': py_actions,
        })

    return profiles


def _rank_goal_capabilities(goal_text: str,
                            detected_labels: list[str] | None = None,
                            python_code: str | None = None) -> list[tuple[float, str, str, str]]:
    goal_tokens = _tokenize_semantic_text(goal_text)
    if not goal_tokens:
        return []

    ranked: list[tuple[float, str, str, str]] = []
    for profile in _build_capability_profiles(detected_labels or [], python_code=python_code):
        profile_tokens = _tokenize_semantic_text(profile.get('score_text', ''))
        if not profile_tokens:
            continue
        overlap = len(goal_tokens & profile_tokens)
        coverage = overlap / max(1, len(goal_tokens))
        precision = overlap / max(1, len(profile_tokens))
        score = (overlap * 2.0) + (coverage * 2.5) + (precision * 1.5)
        if profile.get('matched_labels'):
            score += 0.25 * len(profile['matched_labels'])
        if profile.get('actions'):
            score += min(1.0, 0.15 * len(profile['actions']))
        if score > 0:
            ranked.append((score, profile['display'], profile['route'], profile['key']))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    return ranked


def _normalize_coordinator_assignment_by_capabilities(
    tool: str,
    task: str,
    categories: set[str] | None,
    has_user_tg_channel: bool = False,
    has_user_discord_webhook: bool = False,
) -> tuple[str, str, str]:
    """Нормализует назначение координатора под реальные возможности агента.

    Возвращает (tool, task, note). note пустой если изменений нет.
    """
    cats = set(categories or set())
    tool_norm = (tool or '').strip().lower()
    task_norm = (task or '').strip()
    task_l = task_norm.lower()

    # publish_* без канала пользователя/агента → переводим в подготовку контента
    if tool_norm == 'publish_to_telegram' and ('telegram' not in cats and not has_user_tg_channel):
        return (
            'create_post',
            (task_norm + ' Канал Telegram не подключён: подготовь контент и передай пользователю через send_message_to_user.').strip(),
            'publish_to_telegram недоступен без Telegram-канала/бота',
        )
    if tool_norm == 'publish_to_discord' and ('discord' not in cats and not has_user_discord_webhook):
        return (
            'create_post',
            (task_norm + ' Discord webhook не подключён: подготовь контент и передай пользователю через send_message_to_user.').strip(),
            'publish_to_discord недоступен без Discord webhook',
        )

    # Внешние Telegram-чаты/группы недоступны без userbot (его нет в платформе)
    _tg_platform_words = ('telegram', 'тг', 'телеграм')
    _tg_external_words = (
        'сообществ', 'community',
        'вступ', 'join', 'мониторить',
        'чужой', 'чужих', 'чужие', 'внешн',
        'бизнес-чат', 'пообщ', 'общайся', 'чатов', 'чатах',
    )
    # Отдельно: 'чат'/'групп' ложно совпадают с "опубликуй в Telegram-чат пользователя"
    # → считаем внешним только если нет publish_to_telegram в tool
    _asks_external_tg = (
        any(w in task_l for w in _tg_platform_words)
        and any(w in task_l for w in _tg_external_words)
        and 'publish_to_telegram' not in tool_norm
        and not (has_user_tg_channel and ('опублик' in task_l or 'пост' in task_l))
    )
    if _asks_external_tg:
        return (
            'web_search',
            (
                'Внешние Telegram-чаты/группы недоступны в платформе. '
                'Сделай web_search по нишевым сообществам и публичным контактам, '
                'собери релевантные точки входа и сформируй план outreach через доступные каналы '
                '(email, контент в канале пользователя, DELEGATE агенту с нужной интеграцией).'
            ),
            'внешние Telegram-чаты недоступны, задача переведена в выполнимый формат',
        )

    # Safety net: DM через мессенджеры/соцсети — платформенное ограничение (нет userbot)
    # Минимальный набор — основная логика через рассуждение ИИ в карточке НЕДОСТУПНО
    _dm_indicators = ('написать @', 'отправить @', 'dm ', 'личное сообщение')
    _messaging_platforms = ('telegram', 'телеграм', 'тг', 'whatsapp', 'вотсап',
                            'instagram', 'инстаграм', 'вконтакте', 'vk.com')
    if any(w in task_l for w in _dm_indicators) and any(p in task_l for p in _messaging_platforms):
        return (
            'send_outreach_email' if 'email' in cats else 'web_search',
            'Личные сообщения через мессенджеры/соцсети недоступны (нет userbot). '
            + ('Найди email контакта через web_search и используй send_outreach_email.' if 'email' in cats else
               'Найди email контакта через web_search и сохрани через save_note.'),
            'DM через мессенджер невозможен — safety net',
        )

    return tool_norm, task_norm, ''


def _build_capability_card(caps: dict, agent_name: str, user=None) -> str:
    """Генерирует текстовый блок возможностей агента для любого промпта.

    Используется в:
    - _build_autopilot_prompt (промпт самого агента)
    - _coord_prompt (поручение координатора агенту)
    - _cap_rules_lines (план координатора)
    """
    cats = caps.get('categories', set())
    labels = caps.get('labels', [])
    if not labels and not cats:
        return ''

    lines = []
    for cat in sorted(cats):
        name = _CAP_CATEGORY_NAMES.get(cat, cat)
        hint = _CAP_TOOL_HINTS.get(cat, '')
        lines.append(f'  ✅ {name}: {hint}' if hint else f'  ✅ {name}')

    # Показываем Custom API метки по-отдельности (интеграции не из стандартного списка)
    _known_label_kws = set()
    for kws, _ in _CAP_CATEGORY_MAP:
        _known_label_kws.update(kws)
    for lbl in labels:
        lbl_l = str(lbl).lower()
        if lbl_l.startswith('custom api:'):
            lines.append(f'  ✅ {lbl}: run_agent_action(action="run") или HTTP-запрос')
        elif not any(kw in lbl_l for kw in _known_label_kws) and 'инструменты' not in lbl_l:
            # Нераспознанный лейбл из пользовательского кода — показываем явно
            lines.append(f'  ✅ {lbl}')

    # Всегда доступные
    lines.append('  ✅ web_search, research_topic, create_goal — всегда доступны')

    result = '\nИНТЕГРАЦИИ АГЕНТА (используй ТОЛЬКО из этого списка):\n'
    result += '\n'.join(lines) + '\n'
    result += (
        '→ Нет в списке = сначала используй web_search/research_topic. '
        'Если без конкретной интеграции задача невыполнима — подскажи пользователю что подключить в дашборде.\n'
    )
    return result


# ═══════════════════════════════════════════════════════
# FINE-GRAINED CATALOG TYPE DETECTION
# ═══════════════════════════════════════════════════════
# Маппинг подстрок в label (output _parse_agent_integrations) → тип каталога
# Используется для проактивного советника по интеграциям (_FULL_CATALOG)
_CATALOG_TYPE_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (('яндекс.метрик', 'yandex_metrika', 'metrika', 'яндекс метрик', 'api-metrika'), 'analytics'),
    (('google analytics', 'ga4', 'gtag'), 'analytics'),
    (('gmail', 'imap', 'smtp', 'яндекс почт', 'mail.ru', 'email', 'почта', 'resend', 'sendgrid', 'mailgun', 'sparkpost'), 'email'),
    (('github', 'gitlab'), 'github'),
    (('rss', 'лента', 'feed'), 'rss'),
    (('alpha vantage', 'alphavantage', 'биржевые'), 'alphavantage'),
    (('newsapi', 'новости (news',), 'newsapi'),
    (('notion',), 'notion'),
    (('slack',), 'slack'),
    (('trello', 'jira', 'asana', 'todoist', 'clickup', 'linear'), 'pm'),
    (('amocrm', 'битрикс', 'hubspot', 'salesforce', 'crm'), 'crm'),
    (('wildberries', 'ozon', 'shopify', 'маркетплейс', 'яндекс.маркет'), 'ecommerce'),
    (('binance', 'bybit', 'coinbase', 'крипт'), 'crypto'),
    (('google sheets', 'gspread', 'spreadsheet', 'airtable', 'таблиц'), 'sheets'),
    (('stripe', 'юкасса', 'yookassa', 'платеж'), 'payments'),
    (('telegram',), 'tg_channel'),
    (('discord',), 'discord'),
    (('whatsapp', 'waba', '360dialog'), 'whatsapp'),
    (('calendar', 'календар', 'calendly'), 'calendar'),
    (('sms', 'smsc', 'unisender'), 'sms'),
    (('вконтакте', 'vk'), 'vk'),
    (('авито', 'avito', 'циан', 'cian'), 'avito'),
    (('youtube',), 'youtube'),
    (('linkedin',), 'linkedin'),
    (('google maps', '2gis', 'gmaps'), 'maps'),
    (('typeform', 'tally', 'google forms'), 'forms'),
    # Новые типы
    (('hh.ru', 'headhunter', 'superjob', 'hh_query'), 'hr'),
    (('openai', 'gemini', 'anthropic', 'claude', 'gpt'), 'ai_api'),
    (('firebase', 'firestore'), 'firebase'),
    (('playwright', 'selenium', 'scraping'), 'scraping'),
    (('яндекс.директ', 'yandex_direct', 'direct_token'), 'advertising'),
    (('airtable',), 'airtable'),
    (('coingecko', 'coingeсko'), 'coingecko'),
    (('мойсклад', 'moysklad'), 'moysklad'),
    (('calendly',), 'calendly'),
]


def _detect_catalog_types(detected_labels: list[str]) -> set[str]:
    """Определяет fine-grained типы каталога из лейблов _parse_agent_integrations.

    В отличие от _classify_agent_caps (coarse categories для промптов),
    здесь возвращаются точные типы для фильтрации _FULL_CATALOG.
    """
    types: set[str] = set()
    labels_lower = [str(l).lower() for l in (detected_labels or [])]
    for kws, ctype in _CATALOG_TYPE_KEYWORDS:
        if any(kw in label for label in labels_lower for kw in kws):
            types.add(ctype)
    return types


def _strip_md(text: str) -> str:
    """Strip markdown formatting: **bold**, ##headers, bullet lists."""
    if not text:
        return text or ''
    import re as _re_md
    t = _re_md.sub(r'\*{1,2}([^*]+)\*{1,2}', r'\1', text)
    t = _re_md.sub(r'^\s*#{1,4}\s*', '', t, flags=_re_md.MULTILINE)
    t = _re_md.sub(r'^\s*[•\-\*]\s+', '', t, flags=_re_md.MULTILINE)
    t = _re_md.sub(r'^\s*\d+[.)\]]\s+', '', t, flags=_re_md.MULTILINE)
    return t


# ── Tool-name / tech-noise sanitizer for user-facing messages ──
_TOOL_NAMES_RE = None

def _sanitize_proactive_text(text: str, is_fem: bool = False) -> str:
    """Remove tool names, tech noise from user-facing proactive messages."""
    if not text:
        return text or ''
    import re as _re_san
    global _TOOL_NAMES_RE
    if _TOOL_NAMES_RE is None:
        _tool_names = [
            'web_search', 'research_topic', 'save_note', 'save_email_contact',
            'find_relevant_contacts_for_task', 'send_outreach_email', 'check_emails',
            'reply_to_outreach_email', 'negotiate_by_email', 'update_goal_progress',
            'run_agent_action', 'create_post', 'send_message_to_user',
            'get_news_trends', 'get_rss_feed', 'delegate_to_agent',
            'get_metrics', 'search_contacts', 'get_funnel_data',
        ]
        _pattern = r'\b(?:(?:через|с помощью|используя|действие)\s+)?(?:' + '|'.join(_re_san.escape(t) for t in _tool_names) + r')\b'
        _TOOL_NAMES_RE = _re_san.compile(_pattern, _re_san.IGNORECASE)
    t = _TOOL_NAMES_RE.sub('', text)
    # Translated tool names in Russian
    t = _re_san.sub(r'\b(?:через\s+)?(?:действие агента|сохранение контакта|поиск контактов|отправка письма)\b', '', t, flags=_re_san.IGNORECASE)
    # "исследование темы через исследование темы" → "исследование темы"
    t = _re_san.sub(r'(исследование темы)(?:\s+через\s+исследование темы)+', r'\1', t, flags=_re_san.IGNORECASE)
    # Clean up empty backtick pairs left after tool name removal (`` → nothing)
    t = _re_san.sub(r'`\s*`', '', t)
    # Clean up dangling prepositions/words left after tool name removal
    t = _re_san.sub(r'\b(?:через|с помощью|используя|действие)\s*[.,;!?]', '.', t, flags=_re_san.IGNORECASE)
    t = _re_san.sub(r'\b(?:через|с помощью|используя|действие)\s*$', '', t, flags=_re_san.IGNORECASE)
    t = _re_san.sub(r'\bРезультат\s*[.,;!?]?\s*$', '', t, flags=_re_san.IGNORECASE)
    t = _re_san.sub(r'\bЗафикси\s*[.,;!?]?\s*$', '', t, flags=_re_san.IGNORECASE)
    # Clean up leftover artifacts: double spaces, dangling dashes/commas
    t = _re_san.sub(r'\s+—\s+—', ' —', t)
    t = _re_san.sub(r'\s{2,}', ' ', t)
    t = _re_san.sub(r'\s+([.,;!?])', r'\1', t)
    # Sanitize hallucinated token amounts ("1000+500", "бесплатных токенов" etc.)
    try:
        from ai_integration.conversation_history import sanitize_token_hallucinations
        t = sanitize_token_hallucinations(t)
    except Exception:
        pass
    # Neutralize feminine verb forms (agent persona leak: "я проверила" → "я проверил")
    # Пропускаем для женских агентов — у них женский род правильный
    if not is_fem:
        _fem_verbs = (
            r'нашла|проверила|отправила|сделала|написала|создала|удалила|обновила|'
            r'загрузила|подготовила|исследовала|проанализировала|собрала|завершила|'
            r'добавила|получила|увидела|поняла|решила|опубликовала|выполнила|'
            r'запустила|выявила|провела|выяснила|изучила|составила|обнаружила|'
            r'сохранила|определила|подключила|настроила|протестировала|разработала'
        )
        t = _re_san.sub(
            r'\b(' + _fem_verbs + r')\b',
            lambda m: _re_san.sub(r'ла$', 'л', m.group(1)),
            t
        )
    return t.strip()


def _is_delegation_message(text: str, agent_names: list) -> bool:
    """Check if message is agent-to-agent delegation (not meant for user)."""
    if not text or not agent_names:
        return False
    t = text.strip().lower()
    for name in agent_names:
        nl = name.lower()
        # "Марк, проанализируй..." / "ASI, отправь..."
        if t.startswith(nl + ',') or t.startswith(nl + ', '):
            return True
        # "Марк проанализируй" (без запятой, но повелительное наклонение сразу)
        _IMPERATIVE_VERBS = (
            'проанализируй', 'отправь', 'проверь', 'найди', 'подготовь',
            'используй', 'запусти', 'сделай', 'напиши', 'создай',
            'добавь', 'обнови', 'собери', 'изучи', 'помоги',
        )
        for verb in _IMPERATIVE_VERBS:
            if t.startswith(nl + ' ' + verb):
                return True
    return False


async def _safe_send(bot, chat_id: int, text: str):
    """Send a message, splitting into chunks if it exceeds Telegram's 4096 char limit."""
    if not text or not text.strip():
        return
    text = _strip_md(text).strip()
    if len(text) <= _TG_MAX_LEN:
        await bot.send_message(chat_id=chat_id, text=text)
        return
    # Split by double newlines first, then single newlines, then hard-cut
    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= _TG_MAX_LEN:
            chunks.append(remaining)
            break
        # Find best split point
        cut = _TG_MAX_LEN
        for sep in ('\n\n', '\n', '. ', ' '):
            pos = remaining.rfind(sep, 0, _TG_MAX_LEN)
            if pos > _TG_MAX_LEN // 3:
                cut = pos + len(sep)
                break
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    for chunk in chunks:
        if chunk.strip():
            await bot.send_message(chat_id=chat_id, text=chunk)


def _safe_avatar(url: str | None, agent_id: int | None = None) -> str:
    """Return avatar proxy URL. NEVER store raw base64 data URIs in DB interactions."""
    if agent_id:
        return f'/api/arena/agent_avatar/{agent_id}'
    return ''


def _strip_html(text: str) -> str:
    """Убирает HTML-теги из ответа LLM: <a href='mailto:x'>x</a> → x"""
    if not text or '<' not in text:
        _t = text or ''
    else:
        _t = text
        _t = re.sub(r'<a\s+href=["\']mailto:([^"\'\s>]+)["\'][^>]*>[^<]*</a>', r'\1', _t, flags=re.IGNORECASE | re.DOTALL)
        _t = re.sub(r'<a\s+href=["\']mailto:([^"\'\s>]+)["\'][^>]*>[^<]*', r'\1', _t, flags=re.IGNORECASE | re.DOTALL)
        _t = re.sub(r'<a\s+[^>]*>(.*?)</a>', r'\1', _t, flags=re.IGNORECASE | re.DOTALL)
        _t = re.sub(r'<([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})>', r'\1', _t)
        _t = re.sub(r'<[^>]+>', '', _t)
    _t = re.sub(r'@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}["\']?\s*>\s*(?=[a-zA-Z0-9._%+-]+@)', '', _t)
    _t = re.sub(r'["\']\s*/?>\s*(?=\S)', '', _t)
    _t = re.sub(r'&(?:nbsp|amp|lt|gt|quot|#\d+);?', ' ', _t)
    return _strip_md(_t)


# ═══════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════

# ── Лимиты доставок (единые, контроль расхода через токены) ──
# Токены — основной ограничитель. Лимиты — только anti-spam предохранитель.
MAX_DIALOG_PER_DAY = 12
MAX_AGENT_PERSONA_MSG_PER_DAY = int(os.getenv('MAX_AGENT_PERSONA_MSG_PER_DAY', '50'))
# Технические служебные сообщения не должны съедать лимит «живых» отчётов агента.
_AGENT_PERSONA_CAP_EXCLUDE_ANCHOR_TYPES = {
    'goal_autopilot_ack',
    'goal_autopilot_handoff',
    # Межагентское взаимодействие (ack/handoff/delegation) не считается — это служебные сообщения.
    # Реальные отчёты агентов (result) НЕ считаются — пользователь должен видеть каждый результат.
    'goal_autopilot_result',
    'coordinator_result',
    'agent_delegation',
    'agent_chain_continue',
    'agent_chain_transfer',
}
MAX_AUTOPILOT_MSG_PER_DAY = 500  # Лимит-предохранитель. Реальное ограничение — MIN_AUTOPILOT_GAP_MINUTES
MAX_FEED_PER_DAY = 1
MAX_CHANNEL_PER_DAY = 3  # постов в канал в день
# CRITICAL/HIGH якоря НЕ считаются в лимите — доставляются всегда

NIGHT_START_HOUR = PROACTIVE_NO_SEND_START_HOUR  # Общая настройка: 22
MORNING_START_HOUR = PROACTIVE_SEND_START_HOUR   # Общая настройка: 10
SCAN_INTERVAL_MINUTES = 5
AUTOPILOT_DEEP_NIGHT_START = 0  # Ночная блокировка отключена (автопилот работает 24/7)
AUTOPILOT_DEEP_NIGHT_END = 0

# Минимальный интервал между ПРОАКТИВНЫМИ сообщениями (не блокирует CRITICAL)
MIN_PROACTIVE_GAP_MINUTES = 30
MIN_AUTOPILOT_GAP_MINUTES = 15  # Интервал между autopilot dispatch'ами
REVIEW_SILENT_TYPES = {'goal_autopilot_review', 'chat_ai_review'}

# ── Cache for integration hypothesis (avoid extra AI call every coordinator cycle) ──
# Key: user_id, Value: (timestamp, hypothesis_str)
_IH_CACHE: dict = {}
_IH_CACHE_TTL = 6 * 3600  # 6 hours

# Если пользователь писал в последние N минут — НЕ отправлять проактивные (кроме CRITICAL)
ACTIVE_DIALOG_SUPPRESS_MINUTES = 3

# Cooldown по приоритету (часы)
PRIORITY_COOLDOWN = {
    AnchorPriority.CRITICAL: 0.5,   # 30 мин
    AnchorPriority.HIGH: 1.5,
    AnchorPriority.MEDIUM: 3,
    AnchorPriority.LOW: 4,
}

# Якоря, которые ВСЕГДА доставляются (кроме DND/ночь)
ALWAYS_DELIVER_TYPES = {
    'task_reminder',             # Точное напоминание по reminder_time
    'task_overdue',              # Просроченная задача — критично
    'task_deadline_soon',        # Дедлайн скоро — критично
    'delegation_update',         # Результат делегирования — пользователь ждёт
    'goal_deadline',             # Горящий дедлайн цели
    'incoming_message',          # Непрочитанные входящие сообщения
    'token_low_balance',         # Критически низкий баланс токенов
    'email_reply_received',      # Входящий ответ на email-кампанию — критически важно
    'payment_failed',            # Неудачная попытка пополнить токены
    'background_research_ready', # Фоновое исследование завершено — пользователь ждёт результат
    'agent_inbox_reply',         # Агент-почтовик нашёл новые входящие письма
    'agent_task_blocked',        # Агент застрял — нужно решение пользователя
    # service_degraded убран: информационный якорь, не критический.
    # Пользователю не нужно знать про временные сбои DDG — система справляется сама.
}

# Якоря, которые дополнительно ЗАПУСКАЮТ агента (event-driven dispatch)
# Ключ: тип якоря → шаблон задачи (плейсхолдеры: {goal}, {progress}, {task})
_AGENT_DISPATCH_TRIGGERS: dict[str, str] = {
    # ── Цели ──
    'goal_stagnation':    "Цель '{goal}' застряла на {progress}%. Проанализируй причины и предложи 2-3 конкретных действия чтобы сдвинуться с места. Используй свои интеграции.",
    'goal_decomposition': "Разбей цель '{goal}' на конкретные задачи на ближайшую неделю и создай их в системе.",
    'goal_deadline':      "До дедлайна цели '{goal}' остаётся мало времени. Определи что можно сделать прямо сейчас и действуй.",
    # ── Задачи ──
    'task_stale':         "Задача '{task}' давно не обновлялась. Проверь её статус, ускори или предложи делегировать.",
    'task_overdue':       "Задача '{task}' просрочена. Определи причину задержки и предложи план завершения или перенеси срок.",
    'task_deadline_soon': "До дедлайна задачи '{task}' осталось мало времени. Подготовь всё необходимое для завершения.",
    # ── Делегирование ──
    'delegation_update':  "Получен результат делегирования по задаче '{task}'. Проанализируй качество, добавь к задаче и предложи следующие шаги.",
    # ── Сервисы ──
    'service_degraded':   "Сервис деградирован: {task}. Проведи диагностику и предложи решение.",
    'agent_task_blocked': "Агент заблокирован на задаче '{task}'. Проанализируй причину блокировки и предложи решение.",
    # ── Кампании ──
    'campaign_stagnation': "Кампания '{task}' не показывает активности 3+ дня. Проанализируй эффективность и предложи корректировку.",
    # goal_autopilot_review: fallback — используется если _build_autopilot_prompt вернёт пустое
    'goal_autopilot_review': "Продвинь цель пользователя на один конкретный шаг вперёд. Анализ → выбор → ДЕЙСТВИЕ.",
    'chat_ai_review': "Продолжи анализ недавнего диалога с пользователем. Если есть полезный следующий шаг — сделай его инструментами или напиши короткое живое сообщение по делу без повторов.",
}


# Тип интеграции → описание для матрицы делегирования
_INTEGRATION_TYPE_LABELS = {
    'email':     'чтение входящих / ответы (check_emails)',
    'outreach':  'отправка outreach-писем (send_outreach_email)',
    'github':    'поиск разработчиков/контрибьюторов',
    'rss':       'аналитика трендов / контентные идеи для email-кампаний',
    'slack':     'коммуникация/рассылка в Slack',
    'notion':    'запись/обновление Notion',
    'pm':        'задачи в Trello/Jira',
    'crm':       'контакты/сделки в CRM',
    'ecommerce': 'мониторинг маркетплейса',
    'crypto':    'крипто-данные/биржа',
    'data':      'данные/таблицы',
    'content':   'публикация контента',
    'hr':        'поиск кандидатов/вакансий',
    'avito':     'объявления Авито',
    'payments':  'анализ платежей/выручки',
}


def _match_best_integration(goal_title: str,
                             has_imap: bool = False, has_github: bool = False, has_rss: bool = False,
                             has_alpha: bool = False, has_content: bool = False, has_news: bool = False,
                             has_notion: bool = False, has_slack: bool = False, has_sheets: bool = False,
                             has_stripe: bool = False,
                             caps_labels: list[str] | None = None,
                             python_code: str | None = None) -> list[tuple[float, str, str]]:
    """Ранжирует интеграции по релевантности цели.
    Использует _rank_goal_capabilities + keyword-boost для дифференциации скоров."""
    t = goal_title.lower()

    # Компактная таблица: category → (keywords_hi, keywords_lo)
    # hi = +3 за совпадение, lo = +1
    _KW_BOOST: dict[str, tuple[tuple, tuple]] = {
        'git':       (('разработчик', 'программист', 'developer', 'github', 'тестировщик', 'пользовател',
                       'beta', 'бета', 'участник', 'кандидат', 'contributor'), ('контрибьют', 'репозитор', 'code', 'recruit')),
        'email':     (('клиент', 'партнёр', 'подписчик', 'рассылк', 'аудитор', 'покупател', 'лид', 'lead',
                       'outreach', 'email', 'почт'), ()),
        'finance':   (('нефт', 'газ', 'акц', 'биржа', 'котировк', 'oil', 'stock'), ('инвест', 'трейд', 'финанс', 'валют')),
        'rss':       (('новост', 'мониторинг', 'тренды', 'медиа', 'сми', 'лент'), ('блог', 'обзор', 'фид')),
        'telegram':  (('канал', 'аудитория', 'подписчик', 'пост', 'smm', 'telegram', 'контент'), ('охват', 'трафик')),
        'discord':   (('discord',), ()),
        'notion':    (('база знаний', 'документ', 'notion', 'вики'), ('заметк',)),
        'slack':     (('команд', 'коммуникац', 'slack'), ('уведомлен',)),
        'sheets':    (('таблиц', 'данные', 'аналитик', 'sheets'), ('метрик', 'отчёт')),
        'payments':  (('платёж', 'выручк', 'revenue', 'оплат', 'stripe'), ('транзакц',)),
        'crm':       (('crm', 'amocrm', 'битрикс', 'клиент', 'сделк', 'лид'), ('контакт',)),
        'marketplace': (('wildberries', 'ozon', 'авито', 'маркетплейс', 'продаж'), ('товар', 'склад')),
        'crypto':    (('крипт', 'binance', 'bybit', 'биткоин', 'bitcoin'), ('монет', 'токен')),
        'hr':        (('вакансия', 'рекрутинг', 'найм', 'кандидат', 'hh.ru', 'headhunter'), ('резюме',)),
        'pm':        (('проект', 'задач', 'jira', 'trello', 'asana'), ('спринт', 'канбан')),
        'calendar':  (('встреч', 'календар', 'расписан', 'zoom'), ('событ',)),
        'calls':     (('звонк', 'обзвон', 'sms', 'twilio'), ('телефон',)),
        'social':    (('linkedin', 'instagram', 'twitter', 'вконтакт', 'соцсет'), ('профиль',)),
        'image_gen': (('изображен', 'картинк', 'генерац', 'replicate'), ('визуал',)),
    }

    # Получаем категории агента
    caps = _classify_agent_caps(caps_labels or [])
    agent_cats = caps.get('categories', set())

    # Скормим keyword boost
    boost: dict[str, float] = {}
    for cat in agent_cats:
        if cat in _KW_BOOST:
            hi, lo = _KW_BOOST[cat]
            score = sum(3.0 for w in hi if w in t) + sum(1.0 for w in lo if w in t)
            if score > 0:
                boost[cat] = score

    # Базовые результаты из _rank_goal_capabilities
    result = []
    for base_score, name, route, key in _rank_goal_capabilities(goal_title, caps_labels or [], python_code=python_code):
        if key == 'custom_actions' and not python_code:
            continue
        total = base_score + boost.get(key, 0)
        result.append((total, name, route))

    result.sort(key=lambda x: -x[0])
    return result


# ── Тактические семейства инструментов ──
# Каждое семейство = одна концептуальная стратегия. Использовал инструмент → семейство отмечено.
_TACTIC_FAMILIES: dict = {
    'direct_action':      {'send_outreach_email', 'run_agent_action', 'find_relevant_contacts_for_task',
                           'save_email_contact',
                           'find_and_message_relevant_users', 'create_post', 'publish_to_telegram',
                           'publish_to_discord', 'web_search'},
    'infrastructure':     {'research_topic',
                           'delegate_task', 'start_delegation_campaign', 'schedule_background_task'},
    'relationship':       {'reply_to_outreach_email', 'negotiate_by_email', 'send_follow_up_email',
                           'check_emails', 'list_email_contacts'},
    'content_attract':    {'create_post', 'generate_image', 'publish_to_telegram', 'publish_to_discord'},
    'research_discover':  {'research_topic', 'get_news_trends', 'find_relevant_contacts_for_task', 'web_search'},
}

# Универсальные паттерны мышления (работают для ЛЮБОЙ цели)
_UNIVERSAL_PATTERNS: dict = {
    'direct_action': (
        '🎯 ПРЯМОЕ ДЕЙСТВИЕ',
        'Сделать то что НЕПОСРЕДСТВЕННО двигает цель: outreach, поиск, публикация, конкретный шаг.',
    ),
    'infrastructure': (
        '🏗️ СОЗДАТЬ СИСТЕМУ',
        'research_topic → delegate_task. Расписание, план, автоматизация, материалы.',
    ),
    'relationship': (
        '🔁 СВЯЗИ И ПОДДЕРЖКА',
        'check_emails → reply/negotiate. Ментор, партнёр, сообщество по теме цели.',
    ),
    'content_attract': (
        '🧲 КОНТЕНТ-МАГНИТ',
        'create_post → publish в подходящие каналы (TG/Discord). При наличии контактов — подумай о email-рассылке. Публичный прогресс = мотивация + привлечение.',
    ),
    'research_discover': (
        '🔍 ПЕРЕОСМЫСЛИТЬ ПОДХОД',
        'research_topic/get_news_trends. Новый метод, платформа, аудитория, ресурс.',
    ),
}



def _build_tactic_wheel(goal_type: str, used_tools: set, agent_history: list) -> str:
    """Универсальное тактическое колесо — 5 паттернов мышления для ЛЮБОЙ цели.
    Не зависит от keywords, работает через понимание прямых vs косвенных путей.
    """
    # Определяем использованные паттерны по инструментам + текстовой истории
    h_text = ' '.join(agent_history or []).lower()
    used_patterns: set = set()
    
    for pattern, tools in _TACTIC_FAMILIES.items():
        if tools & used_tools:
            used_patterns.add(pattern)
    
    # Дополнительные сигналы из текста истории
    if any(w in h_text for w in ('send_outreach', 'search_users', 'find_and_message', 'email', 'web_search', 'create_post')):
        used_patterns.add('direct_action')
    if any(w in h_text for w in ('landing', 'faq', 'demo', 'подготов', 'инфраструктур', 'материал', 'документац')):
        used_patterns.add('infrastructure')
    if any(w in h_text for w in ('check_email', 'reply', 'negotiate', 'follow_up', 'partner')):
        used_patterns.add('relationship')
    if any(w in h_text for w in ('marketing_content', 'content_campaign', 'generate_image', 'контент')):
        used_patterns.add('content_attract')
    if any(w in h_text for w in ('research_and_plan', 'research_topic', 'get_news', 'переосмысл')):
        used_patterns.add('research_discover')

    untried = [k for k in _UNIVERSAL_PATTERNS if k not in used_patterns]
    tried = [k for k in _UNIVERSAL_PATTERNS if k in used_patterns]
    
    lines = ["\n━━━ 5 УНИВЕРСАЛЬНЫХ ПАТТЕРНОВ (работают для ЛЮБОЙ цели) ━━━"]
    for key, (name, explanation) in _UNIVERSAL_PATTERNS.items():
        if key in used_patterns:
            lines.append(f"  ✅ {name}")
        else:
            lines.append(f"  ◻️ {name}  ← НЕ ПРОБОВАЛ")
            lines.append(f"     {explanation}")

    if untried:
        first_untried_name = _UNIVERSAL_PATTERNS[untried[0]][0]
        lines.append(f"\n🔴 Попробуй непопробованный паттерн: {first_untried_name}")
        lines.append("   Особенно важно: если 'direct_action' опробован 2+ раза → переключись на 'infrastructure'")
    elif len(tried) == len(_UNIVERSAL_PATTERNS):
        lines.append("\n✅ Все паттерны опробованы! Масштабируй самый эффективный.")

    # ── Динамическая стратегия на основе истории результатов ──
    _h_combined = h_text
    _did_outreach = 'direct_action' in used_patterns
    _got_replies = any(w in _h_combined for w in ('ответил', 'replied', 'ответ получен', 'interested'))
    _got_blocks = any(w in _h_combined for w in ('cooldown', 'уже отправлено', 'already sent', 'лимит', 'ошибка'))
    if _did_outreach and not _got_replies and len(agent_history or []) >= 4:
        lines.append(
            "\n💡 СТРАТЕГИЯ: прямой аутрич без ответов 4+ циклов. AI рекомендует:\n"
            "  — Сменить целевую аудиторию (другой query, другая платформа)\n"
            "  — Создать контент (пост, статья) чтобы привлечь органически\n"
            "  — Переключиться на infrastructure: FAQ, landing, демо-материалы"
        )
    elif _did_outreach and _got_blocks:
        lines.append(
            "\n💡 СТРАТЕГИЯ: блокировки cooldown/дубли. Попробуй:\n"
            "  — Новая страница поиска (page=N+1)\n"
            "  — Другой query с другими фильтрами\n"
            "  — Косвенный подход: content_attract или infrastructure"
        )
    
    # ── Creative wild-card tactics — нестандартные ходы которые агент сам не выберет ──
    # Показываем ВСЕГДА, как источник вдохновения для выхода за рамки скриптов
    _CREATIVE_PIVOTS = [
        ("🤝 Партнёрство вместо продажи",
         "Найди 1 человека/компанию кто делает что-то смежное → предложи коллаборацию. "
         "Не 'купи', а 'давай сделаем вместе'. web_search('[ниша] партнёрство сотрудничество email')"),
        ("📊 Кейс с реальными числами",
         "Напиши конкретный мини-кейс: 'цель X → действие Y → результат Z'. "
         "create_post или publish_to_telegram. Цифры и конкретика привлекают сами."),
        ("❓ Открытый вопрос аудитории",
         "Задай публичный вопрос по теме цели: 'что мешает вам [проблема]?' "
         "→ publish_to_telegram. Люди отвечают на вопросы охотнее чем на рекламу."),
        ("🔍 Анализ топ-3 конкурентов",
         "web_search('топ [ниша] 2025 кейс результаты') → что делают лидеры? "
         "research_topic('[тема] лучшие практики') → возьми лучшее, улучши."),
        ("🏆 Признание чужого вклада",
         "Отметь публично реальное достижение кого-то из ниши: 'видел работу X — впечатляет'. "
         "Это создаёт goodwill и привлекает к тебе внимание без прямой рекламы."),
        ("🎯 Прицельный outreach к лидерам мнений",
         "Не массовая рассылка, а 1 письмо эксперту: 'читал вашу статью X, у нас похожий опыт'. "
         "Персонализация × авторитет = высокий отклик."),
        ("💡 Публичная демонстрация (Show, don't tell)",
         "Вместо описания продукта — покажи его в действии. "
         "Создай пост с GIF/скриншотом/числами → люди сами спросят 'как это работает?'"),
        ("🐙 GitHub как площадка для нетворкинга",
         "Не только ищи людей. search_repos по нише → create_issue с предложением коллаборации "
         "или comment_on_issue в активном проекте. Участие в обсуждениях = органическая видимость."),
        ("🔬 Исследование → Контент → Публикация",
         "web_search/research_topic → НЕ просто отчёт, а create_post с выводами → publish_to_telegram. "
         "Превращай каждое исследование в контент для аудитории."),
    ]
    lines.append("\n\n💡 ТВОРЧЕСКИЕ ТАКТИКИ (нестандартные ходы — не следуй правилам, думай):")
    for _name, _desc in _CREATIVE_PIVOTS:
        lines.append(f"  {_name}")
        lines.append(f"     {_desc}")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return '\n'.join(lines) + '\n'


def _smart_trunc(text: str, limit: int = 200) -> str:
    """Sentence-aware truncation: обрезает на границе предложения, не посреди слова."""
    if not text or len(text) <= limit:
        return text or ''
    # Ищем последнюю точку/!/? в пределах лимита
    _chunk = text[:limit]
    for _sep in ('.', '!', '?', '»', ')'):
        _pos = _chunk.rfind(_sep)
        if _pos > limit // 3:  # минимум треть текста сохраняем
            return _chunk[:_pos + 1]
    # Нет конца предложения — обрезаем по последнему пробелу
    _sp = _chunk.rfind(' ')
    if _sp > limit // 3:
        return _chunk[:_sp] + '…'
    return _chunk + '…'


def _finish_sentence(text: str) -> str:
    """Ensure text ends at a sentence boundary, not mid-word."""
    if not text:
        return text
    t = text.rstrip()
    if t[-1:] in '.!?»)':
        return t
    for sep in ('.', '!', '?', '»'):
        pos = t.rfind(sep)
        if pos > len(t) // 3:
            return t[:pos + 1]
    return t


def _build_recent_suggestion_guard(agent_history: list | None = None,
                                   team_history: dict | None = None) -> str:
    """Мягкая самопроверка на повторение одной и той же идеи.

    Не блокирует действие жёстко, а напоминает модели: если паттерн уже звучал
    сегодня, возвращаться к нему можно только при наличии нового факта.
    """
    _entries: list[str] = []
    for _item in (agent_history or [])[:10]:
        if _item:
            _entries.append(str(_item))
    for _agent_name, _hist in (team_history or {}).items():
        for _item in (_hist or [])[:6]:
            if _item:
                _entries.append(f"{_agent_name}: {_item}")

    if not _entries:
        return ''

    _patterns = {
        'Telegram-чаты/каналы': ('telegram', 'чат', 'чаты', 'канал', 'каналы', 'сообществ'),
        'GitHub / GitHub API': ('github', 'gitlab', 'repo', 'репозитор'),
        'Контакты внутри платформы': ('через платформу', 'активных пользователей', 'из сети', 'внутри платформы'),
        'Письма / outreach': ('email', 'письм', 'outreach', 'reply', 'follow-up', 'check_emails'),
        'Смена тактики': ('сменим тактику', 'смени тактику', 'другой подход', 'сменить подход'),
        'Поиск новых контактов': ('новых контактов', 'новых предпринимател', 'новых людей', 'новый сегмент'),
    }

    _counts: dict[str, int] = {}
    for _entry in _entries:
        _txt = str(_entry).lower()
        for _label, _keywords in _patterns.items():
            if any(_kw in _txt for _kw in _keywords):
                _counts[_label] = _counts.get(_label, 0) + 1

    _lines = [
        "\n🧠 АНТИ-ПОВТОР ИДЕЙ:",
        "  Если ту же идею уже предлагали сегодня — не повторяй её автоматически.",
        "  Максимум: 1 раз в 24ч на один и тот же паттерн.",
        "  Повтор допустим только если есть новый факт: другой сегмент, другой query, другая площадка, новый контакт или новый результат.",
    ]
    if _counts:
        _lines.append("  Недавние паттерны команды:")
        for _label, _count in sorted(_counts.items(), key=lambda x: (-x[1], x[0]))[:5]:
            _lines.append(f"    • {_label}: {_count}")
    _lines.append("  Перед новой рекомендацией ответь себе: что здесь реально нового по сравнению с прошлым разом?")
    return '\n'.join(_lines) + '\n'



# _build_personalized_strategy removed: AI determines strategy dynamically


def _build_tool_outcome_block(session, user_id: int, per_agent_history: dict) -> str:
    """Measure per-tool/approach OUTCOME effectiveness from actual Task results.

    Returns a compact block showing completion rates per approach category,
    with mandatory tactic-change recommendations for low-effectiveness tools.
    """
    try:
        from models import Task as _T_eff
        _cut = datetime.now(timezone.utc) - timedelta(hours=48)
        _tasks = session.query(_T_eff.title, _T_eff.status, _T_eff.delegated_to_username).filter(
            _T_eff.user_id == user_id,
            _T_eff.source == 'agent',
            _T_eff.created_at >= _cut,
        ).all()
        if not _tasks or len(_tasks) < 3:
            return ''

        # Base categories + dynamic from _CAP_CATEGORY_MAP
        _strat_kw: dict = {
            'Поиск контактов': ('web_search', 'найди контакт', 'найди email', 'find_relevant',
                                'search', 'поиск', 'save_email_contact'),
            'Email рассылка': ('send_outreach', 'email', 'письм', 'outreach', 'рассыл',
                               'start_email_campaign', 'send_follow_up'),
            'Проверка ответов': ('check_emails', 'проверь входящ', 'проверь почт',
                                 'reply_to', 'ответь на'),
            'Контент': ('create_post', 'publish', 'опубликуй', 'пост', 'статью', 'контент'),
            'Аналитика': ('проанализируй', 'анализ', 'research_topic', 'метрик', 'отчёт'),
        }
        # Dynamically add integration categories so any tool's effectiveness is tracked
        for _cap_kws_o, _cap_cat_o in _CAP_CATEGORY_MAP:
            _cap_name_o = _CAP_CATEGORY_NAMES.get(_cap_cat_o, _cap_cat_o)
            if _cap_name_o not in _strat_kw:
                _strat_kw[_cap_name_o] = tuple(_cap_kws_o[:4])

        _outcomes: dict = {}  # cat -> {completed, skipped, cancelled, total}
        for _title, _status, _agent in _tasks:
            _tl = (_title or '').lower()
            for _cat, _kws in _strat_kw.items():
                if any(kw in _tl for kw in _kws):
                    _o = _outcomes.setdefault(_cat, {'completed': 0, 'skipped': 0, 'cancelled': 0, 'total': 0})
                    _o['total'] += 1
                    if _status == 'completed':
                        _o['completed'] += 1
                    elif _status == 'skipped':
                        _o['skipped'] += 1
                    elif _status in ('cancelled', 'deleted'):
                        _o['cancelled'] += 1
                    break

        if not _outcomes:
            return ''

        _lines = ['🎯 КПД ТАКТИК (факты за 48ч — completion rate по реальным задачам):']
        _low_eff_cats = []
        for _cat, _o in sorted(_outcomes.items(), key=lambda x: -x[1]['total']):
            if _o['total'] < 2:
                continue
            _rate = round(_o['completed'] / _o['total'] * 100)
            _label = 'ВЫСОКИЙ' if _rate >= 60 else ('СРЕДНИЙ' if _rate >= 30 else 'НИЗКИЙ')
            _emoji = '✅' if _rate >= 60 else ('⚠️' if _rate >= 30 else '🔴')
            _lines.append(
                f'  {_emoji} {_cat}: {_o["total"]} задач → {_o["completed"]} завершены '
                f'({_rate}%) → {_label}'
            )
            if _o['cancelled'] > 0:
                _lines.append(f'      отменено/провалено: {_o["cancelled"]}')
            if _rate < 20 and _o['total'] >= 3:
                _low_eff_cats.append(_cat)

        if _low_eff_cats:
            _lines.append(f'\n  ⛔ ОБЯЗАТЕЛЬНАЯ СМЕНА ПОДХОДА для: {", ".join(_low_eff_cats)}')
            _lines.append('     КПД < 20% за 3+ задач = подход НЕ РАБОТАЕТ.')
            _lines.append('     → Используй ДРУГОЙ инструмент, ДРУГУЮ аудиторию или ДРУГОЙ формат.')
            _lines.append('     → Не повторяй ту же тактику — дай кардинально другую задачу.')

        return '\n' + '\n'.join(_lines) + '\n\n'
    except Exception:
        return ''


def _build_reasoning_scaffold(goals_summary: list, caps_lower: list[str],
                               has_imap: bool, has_github: bool, has_rss: bool,
                               has_alpha: bool, has_script: bool, has_content: bool,
                               has_news: bool, has_notion: bool, has_slack: bool,
                               has_sheets: bool, has_stripe: bool, used_tools: set,
                               goal_type: str = 'general',
                               agent_history: list | None = None,
                               caps_labels: list[str] | None = None,
                               python_code: str | None = None) -> str:
    """Универсальный фрейм рассуждения — НЕ keyword-сценарии.
    Агент сам думает: что значит прогресс по ЭТОЙ цели? какие инструменты дают ПРЯМОЙ результат?
    Включает тактическую матрицу 5 подходов с трекингом что уже пробовалось.
    Работает для любой цели без перебора шаблонов.
    """
    if not goals_summary:
        return ''

    # ── Формулируем цели с метриками — конкретно и честно ──
    goal_lines = []
    for g in goals_summary[:3]:
        title = (g.get('title', '') or '')[:70]
        mc = int(g.get('metric_current', 0) or 0)
        mt = g.get('metric_target')
        prog = g.get('progress', 0) or 0
        mu = (g.get('metric_unit', '') or '').strip()
        # Goal age for context
        _age_suffix = ''
        _ca_gl = g.get('created_at')
        if _ca_gl:
            try:
                import datetime as _dt_gl
                if isinstance(_ca_gl, str):
                    _ca_gl = _dt_gl.datetime.fromisoformat(_ca_gl.replace('Z', '+00:00'))
                _age_d = (_dt_gl.datetime.now(_dt_gl.timezone.utc) - _ca_gl.replace(
                    tzinfo=_dt_gl.timezone.utc if _ca_gl.tzinfo is None else _ca_gl.tzinfo)).days
                if _age_d >= 2:
                    _age_suffix = f' [{_age_d}д]'
            except Exception:
                pass
        if mt:
            goal_lines.append(
                f"  • «{title}»{_age_suffix}: нужно {int(mt)}{(' ' + mu) if mu else ''}, подтверждено: {mc}. "
                f"Для +1: update_goal_progress(goal_title=\"{title[:120]}\", metric_current={mc + 1})"
            )
        else:
            goal_lines.append(f"  • «{title}»{_age_suffix} ({prog}%)")

    # ── Авто-приоритизация: лучшая интеграция под каждую цель ──
    _priority_lines = []
    _medals = ['🥇', '🥈', '🥉']
    for g in goals_summary[:3]:
        _gtitle = (g.get('title', '') or '')[:70]
        _ranked = _match_best_integration(
            _gtitle, has_imap, has_github, has_rss, has_alpha,
            has_content, has_news, has_notion, has_slack, has_sheets, has_stripe,
            caps_labels=caps_labels,
            python_code=python_code,
        )
        if _ranked:
            # Показываем топ-2 интеграции для цели
            _best_parts = []
            for _rank, (_score, _ename, _chain) in enumerate((_ranked[:2])):
                _medal = _medals[_rank] if _rank < len(_medals) else '  '
                _best_parts.append(f"    {_medal} {_ename}: {_chain}")
            _priority_lines.append(
                f"  «{_gtitle}»\n" + '\n'.join(_best_parts)
            )

    _priority_block = ''
    if _priority_lines:
        _priority_block = (
            "\n🧠 ЛУЧШИЕ ИНСТРУМЕНТЫ ПОД ТВОИ ЦЕЛИ (выбрано автоматически по теме):\n"
            + '\n'.join(_priority_lines)
            + "\n  └ Начни с 🥇, при необходимости подключай 🥈. Всё остальное — вспомогательное.\n"
        )

    # ── Карта: что РЕАЛЬНО даёт каждая интеграция (динамически из _CAP_TOOL_HINTS) ──
    avail = []
    _cap_cats = set()
    for profile in _build_capability_profiles(caps_labels or [], python_code):
        _key = profile.get('key', '')
        if _key in _cap_cats:
            continue
        _cap_cats.add(_key)
        _hint = _CAP_TOOL_HINTS.get(_key, '')
        if _hint:
            avail.append(f"  • {profile['display']}: {_hint}")

    _goal_text_all = ' '.join(
        (g.get('title', '') or '') + ' ' + (g.get('description', '') or '')
        for g in goals_summary[:3]
    )
    _dynamic_cap_rank = _rank_goal_capabilities(_goal_text_all, caps_labels or [], python_code=python_code)
    _dynamic_top_keys = [item[3] for item in _dynamic_cap_rank[:3]]

    # Системные инструменты — всегда доступны
    avail.extend([
        "  🔍 research_topic / web_search ← анализ + реальные данные",
        "  ↗️ delegate_task / DELEGATE[Имя] ← передать агенту с нужной интеграцией",
        "  🤝 find_relevant_contacts_for_task / find_and_message_relevant_users ← охват",
    ])

    # Адаптивный хинт — одна строка вместо развёрнутых примеров
    _progress_hint = {
        'outreach': "Письмо ≠ прогресс. Ответ с интересом = потенциал. Регистрация/целевое действие = +1.",
        'learning': "Нашёл курс ≠ прогресс. Выполнил урок/задание = шаг. Применил знание = +1.",
        'health': "Запланировал ≠ прогресс. Тренировка выполнена / км пробежаны = +1.",
        'personal': "Подумал ≠ прогресс. Конкретное действие выполнено / привычка закреплена = +1.",
        'content': "Черновик ≠ прогресс. Опубликовано = шаг. Аудитория отреагировала = +1.",
        'research': "Инструмент вызван ≠ прогресс. Данные обработаны и дан вывод = +1.",
        'dev': "Код написан ≠ прогресс. Тест прошёл, задача закрыта = +1.",
        'finance': "Прочитал статью ≠ прогресс. Сделка/вложение/бюджет обновлён = +1.",
        'travel': "Мечтал ≠ прогресс. Билет/виза/бронь/маршрут готов = +1.",
        'startup': "Идея ≠ прогресс. MVP/прототип/питч/клиент = +1.",
        'ecommerce': "Настроил ≠ прогресс. Заказ/продажа/отзыв = +1.",
        'legal': "Изучил ≠ прогресс. Документ подписан/подан = +1.",
        'hr': "Вакансия ≠ прогресс. Кандидат на собеседовании/нанят = +1.",
        'logistics': "Запланировал ≠ прогресс. Груз доставлен/отправлен = +1.",
    }.get(goal_type, "Инструмент вызван ≠ прогресс. Метрика цели сдвинулась = +1.")

    return (
        "\n━━━ ЦЕЛЬ > АКТИВНОСТЬ ━━━\n"
        + '\n'.join(goal_lines)
        + _priority_block + "\n\n"
        f"⚠️ Что = +1 к метрике? {_progress_hint}\n"
        "update_goal_progress: только при ПОДТВЕРЖДЁННОМ РЕЗУЛЬТАТЕ — ответ получен, сделка закрыта, контакт подтверждён. "
        "Отправка письма/поста/поиск = ДЕЙСТВИЕ, а НЕ результат. Не засчитывай как прогресс. "
        "metric_current = АБСОЛЮТНОЕ число (текущее + прирост).\n"
        "Пример: сейчас подтверждено 6 → нашёл 1 нового → metric_current=7.\n\n"
        "🔧 ДОСТУПНЫЕ ВОЗМОЖНОСТИ:\n"
        + '\n'.join(avail) + '\n'
    ) + _build_tactic_wheel(goal_type, used_tools, agent_history or [])



def _build_autopilot_prompt(goals_summary: list, user=None, agent_caps=None, agent_name=None, team_profiles=None, agent_history=None, team_history=None, python_code=None, vector_memory: str = '', integration_snapshots: list = None) -> str:
    """Строит адаптивный промпт автопилота.
    Вместо жёстких A/B/C планов — показывает полный каталог инструментов платформы
    и предоставляет AI свободу выбора лучшей цепочки под цель и интеграции агента.
    """
    import re as _re_ap

    # ── Правило пользователя: фокус на новых — извлекаем рано чтобы все блоки его видели ──
    _agent_new_users_focus = False
    if user:
        try:
            _raw_nuf = getattr(user, 'memory', None) or ''
            if _raw_nuf:
                try:
                    from ai_integration.memory import decrypt_data as _decrypt_nuf
                    _raw_nuf = _decrypt_nuf(_raw_nuf)
                except Exception:
                    pass
                try:
                    import json as _json_nuf
                    _nuf_m = (_json_nuf.loads(_raw_nuf.strip()) if _raw_nuf.strip().startswith('{') else {})
                    _nuf_rules = _nuf_m.get('rules', [])
                    _NUF_KW = ('новых пользовател', 'новых людей', 'не на действующ', 'не с текущей', 'приоритет — привлечение новых')
                    _agent_new_users_focus = any(any(kw in r.lower() for kw in _NUF_KW) for r in _nuf_rules)
                except Exception:
                    pass
        except Exception:
            pass

    # ── Каналы пользователя ──
    channels_hint = ""
    if user:
        _channels = []
        if getattr(user, 'telegram_channel', None):
            _channels.append("Telegram-канал")
        if getattr(user, 'discord_webhook', None):
            _channels.append("Discord")
        if getattr(user, 'email', None):
            _channels.append("Email")
        if _channels:
            channels_hint = f"Каналы пользователя: {', '.join(_channels)}.\n"

    # ── Авто-инференс метрик для целей без metric_target ──
    # Цель без метрики = слепое вождение. Подбираем разумный default по тексту цели.
    _METRIC_DEFAULTS = [
        # (ключевые_слова, metric_type, target, единица)
        (('клиент', 'пользовател', 'подписчик', 'лид', 'lead', 'beta', 'бета', 'регистрац'), 'contacts', 50, 'контактов'),
        (('партнёр', 'партнер', 'инвест', 'b2b', 'сделк'), 'contacts', 20, 'партнёров'),
        (('письм', 'email', 'outreach', 'рассыл'), 'emails_sent', 100, 'писем'),
        (('пост', 'контент', 'публикац', 'статья', 'статей'), 'posts', 30, 'публикаций'),
        (('просмотр', 'охват', 'view', 'impression', 'читател'), 'views', 1000, 'просмотров'),
        (('подписчик', 'follower'), 'subscribers', 200, 'подписчиков'),
        (('курс', 'урок', 'модул', 'занятие', 'тренировк'), 'lessons', 20, 'занятий'),
        (('книг', 'глав', 'страниц'), 'pages', 100, 'страниц'),
        (('км', 'километр', 'пробежать', 'марафон', 'спорт'), 'km', 42, 'км'),
        (('кг', 'килограмм', 'похуд', 'вес'), 'kg_lost', 5, 'кг'),
        (('репозитор', 'commit', 'github', 'deploy'), 'commits', 50, 'коммитов'),
        (('доход', 'выручк', 'заработ', 'revenue', 'продаж'), 'revenue', 100000, '₽'),
        # Новые доменные метрики
        (('вакансия', 'кандидат', 'найм', 'нанять', 'рекрутинг', 'резюме'), 'contacts', 10, 'кандидатов'),
        (('стартап', 'startup', 'mvp', 'питч', 'акселератор', 'запустить продукт'), 'tasks', 30, 'этапов'),
        (('логистик', 'доставк', 'отправк', 'трекинг', 'склад заказ'), 'tasks', 50, 'отправок'),
        (('закон', 'договор', 'документ', 'юридическ', 'лицензия', 'патент'), 'tasks', 10, 'документов'),
        (('путешеств', 'поездк', 'маршрут', 'отпуск', 'перелёт'), 'tasks', 5, 'маршрутов'),
    ]
    for _g in goals_summary:
        if not _g.get('metric_target'):
            _g_text = (_g.get('title', '') + ' ' + (_g.get('description', '') or '')).lower()
            for _kws, _mtype, _mtarget, _munit in _METRIC_DEFAULTS:
                if any(kw in _g_text for kw in _kws):
                    _g['_inferred_metric'] = f"{_mtype}:{_mtarget} {_munit}"
                    break

    # ── Краткое описание целей ──
    def _goal_metric_str(g):
        if g.get('metric_target'):
            return f", {g.get('metric_current', 0)}/{g.get('metric_target', '?')}"
        inf = g.get('_inferred_metric')
        if inf:
            return f", ориентир: {inf.split(':')[1] if ':' in inf else inf}"
        return ''

    _goals_desc = '; '.join(
        f"{g.get('title', '?')} ({g.get('progress', 0)}%"
        + _goal_metric_str(g)
        + ")"
        for g in goals_summary[:5]
    )

    _caps_lower = [c.lower() for c in (agent_caps or [])]
    _caps_str = ', '.join(agent_caps or [])

    # ── Классифицируем интеграции агента (универсально через _classify_agent_caps) ──
    _caps_classified = _classify_agent_caps(agent_caps or [])
    _caps_cats = _caps_classified['categories']

    # Shorthand-флаги для совместимости с нижележащим кодом (loop detector, tactic wheel)
    _has_imap    = 'email'       in _caps_cats
    _has_github  = 'git'         in _caps_cats
    _has_rss     = 'rss'         in _caps_cats
    _has_script  = 'script'      in _caps_cats
    _has_alpha   = 'finance'     in _caps_cats
    _has_news    = 'news'        in _caps_cats
    _has_notion  = 'notion'      in _caps_cats
    _has_slack   = 'slack'       in _caps_cats
    _has_sheets  = 'sheets'      in _caps_cats
    _has_stripe  = 'payments'    in _caps_cats
    _has_crm     = 'crm'         in _caps_cats
    _has_market  = 'marketplace' in _caps_cats
    _has_crypto  = 'crypto'      in _caps_cats
    _has_social  = 'social'      in _caps_cats
    _has_pm      = 'pm'          in _caps_cats
    _has_cal     = 'calendar'    in _caps_cats
    _has_calls   = 'calls'       in _caps_cats
    _has_content = (
        'telegram' in _caps_cats or 'discord' in _caps_cats
        or bool(getattr(user, 'telegram_channel', None))
        or bool(getattr(user, 'discord_webhook', None))
    )
    # Флаги публикации (разделяем: агентский токен vs канал пользователя)
    _agent_has_tg     = 'telegram' in _caps_cats
    _agent_has_discord = 'discord' in _caps_cats
    _user_tg_ch       = bool(getattr(user, 'telegram_channel', None))
    _user_discord_wh  = bool(getattr(user, 'discord_webhook', None))
    _py_actions = _extract_python_actions(python_code)
    _cap_profiles = _build_capability_profiles(agent_caps or [], python_code=python_code)

    # ── Карточка «ЧТО Я УМЕЮ» — reasoning-ready capability card ──
    # Агент должен ДУМАТЬ из интеграций, а не угадывать по описанию роли.
    # Блок строится универсально: из capability-profiles + точных ACTION из python_code.
    _aic_can: list = []
    _aic_cannot: list = []
    for _profile in _cap_profiles:
        _display = _profile.get('display', '')
        _route = _profile.get('route', '')
        _matched = _profile.get('matched_labels', [])[:2]
        if not _display:
            continue
        _line = _display
        if _matched:
            _line += f" (источник: {', '.join(_matched)})"
        if _route:
            _line += f": {_route}"
        _aic_can.append(_line)
    if _py_actions:
        _aic_can.append(
            "Кастомные ACTION агента: " + ', '.join(_py_actions[:12])
            + " → run_agent_action(action=<одно из этих имён>)"
        )
    # publish_to_telegram: доступно если есть бот агента ИЛИ канал пользователя
    if _agent_has_tg or _user_tg_ch:
        _aic_can.append(
            "📢 publish_to_telegram: постинг в ЛИЧНЫЙ КАНАЛ ПОЛЬЗОВАТЕЛЯ"
            + (" (канал настроен)" if _user_tg_ch else " (через бот агента)")
        )
    else:
        _aic_cannot.append("📢 Telegram публикация (нет бота агента / канала пользователя)")
    if _agent_has_discord or _user_discord_wh:
        _aic_can.append("📢 publish_to_discord: отправка webhook в Discord-сервер пользователя")
    _aic_can.append("🔍 web_search, research_topic — всегда доступны (но если есть API — используй API!)")
    # ── Явный перечень НЕнастроенных интеграций ──
    # AI должен ЧЁТКО видеть что подключено и что НЕТ — без угадывания.
    _IMPORTANT_CATS = [
        ('email', 'Email/IMAP/SMTP'), ('git', 'GitHub/GitLab'), ('crm', 'CRM (AmoCRM, HubSpot и др.)'),
        ('rss', 'RSS-ленты'), ('analytics', 'Яндекс.Метрика / GA'), ('finance', 'Биржевые данные'),
        ('notion', 'Notion'), ('sheets', 'Google Sheets'), ('slack', 'Slack'), ('discord', 'Discord'),
        ('calendar', 'Календарь'), ('payments', 'Платежи'), ('social', 'Соцсети'), ('crypto', 'Крипто-биржа'),
    ]
    _missing_integrations = []
    for _cat_key, _cat_label in _IMPORTANT_CATS:
        if _cat_key not in _caps_cats:
            _missing_integrations.append(_cat_label)
    if _missing_integrations:
        _aic_cannot.append("🔌 НЕ подключены: " + ', '.join(_missing_integrations))
    # Платформенные ограничения: одинаковы для ВСЕХ агентов, не зависят от интеграций
    # Платформенные ограничения: контекст для рассуждений, не запреты
    _aic_cannot.append(
        "Платформа не включает Telegram UserBot — участие в чужих группах/чатах технически невозможно"
    )
    _aic_cannot.append(
        "Нет клиента для чужих Discord/Slack-серверов (publish_to_discord = постинг через СВОЙ webhook)"
    )
    _aic_cannot.append("DM незнакомым в мессенджерах/соцсетях требует userbot-интеграцию, которой нет")
    _aic_cannot.append("Сайты за авторизацией — нет доступа к чужим аккаунтам")
    _agent_name_display = agent_name or "агент"

    # ── Блок: что подключено у агента, что доступно для целей ──
    _goals_text_all = ' '.join(
        g.get('title', '') + ' ' + (g.get('description', '') or '') for g in goals_summary
    ).lower()

    _goal_cap_rank = _rank_goal_capabilities(_goals_text_all, agent_caps or [], python_code=python_code)
    _goal_fit_lines = []
    for _score, _display, _route, _key in _goal_cap_rank[:4]:
        if _key == 'custom_actions':
            continue
        _goal_fit_lines.append(f"  ▶ {_display}: {_route}")
    _goal_fit_block = ''
    if _goal_fit_lines:
        _goal_fit_block = (
            "\nСНАЧАЛА ПРОВЕРЬ САМЫЕ РЕЛЕВАНТНЫЕ ДЛЯ ЭТОЙ ЦЕЛИ ВОЗМОЖНОСТИ:\n"
            + "\n".join(_goal_fit_lines)
            + "\n"
        )

    _agent_identity_block = (
        f"\n🎯 ЧТО Я УМЕЮ [{_agent_name_display}] — рассуждай отсюда перед каждым шагом:\n"
        "ДОСТУПНО (есть интеграция / инструмент):\n"
        + "\n".join(f"  ✅ {l}" for l in _aic_can)
        + _goal_fit_block
        + "\nКОНТЕКСТ ОГРАНИЧЕНИЙ (учитывай при планировании):\n"
        + "\n".join(f"  — {l}" for l in _aic_cannot)
        + "\n→ Перед каждым шагом спроси себя: через какой КОНКРЕТНЫЙ инструмент из ДОСТУПНО я это сделаю?\n"
        "→ Если задача требует недоступный канал — переключись на доступный канал с похожим результатом.\n"
        "→ НЕ ИЩИ сообщества/серверы/группы на платформах из «НЕ подключены» (Discord, Slack, Telegram-группы и т.д.) — "
        "ты не можешь туда вступить, написать или опубликовать. Не трать шаг на поиск того, "
        "чем не сможешь воспользоваться. Вместо этого используй ДОСТУПНЫЕ каналы: email, свой TG-канал, "
        "web_search для поиска контактов, GitHub issues/PR и т.д.\n"
    )

    # ── Тип цели: research / outreach / content / dev / learning / health / personal / general ──
    _RESEARCH_KW = ('анализ', 'исследован', 'мониторинг', 'обзор', 'рынок', 'нефт', 'газ',
                    'биржа', 'котировк', 'тренды', 'данные', 'аналитик', 'прогноз',
                    'статистик', 'oil', 'stock', 'commodity', 'forex', 'сырьё', 'металл', 'отчёт',
                    'analysis', 'research', 'monitoring', 'review', 'market', 'trend', 'data',
                    'analytics', 'forecast', 'statistics', 'report', 'survey', 'study')
    _OUTREACH_KW = ('найти клиент', 'привлеч', 'подписчик', 'пользовател',
                    'набор', 'аудитор', 'лид', 'lead', 'beta', 'бета', 'тестировщик', 'рекрутинг',
                    'продаж', 'b2b', 'партнёр', 'сделк', 'клиентск',
                    'вакансия', 'кандидат', 'найм', 'нанять', 'hr', 'стаж',
                    'инвест', 'инвестор', 'финансиров', 'раунд', 'фандрейзинг',
                    'участник', 'комьюнити', 'member', 'contributor', 'беты', 'регистрац',
                    'продвижен', 'маркетинг', 'реклам', 'раскрутк', 'охват', 'брендинг',
                    'популяризац', 'пиар', ' pr ', 'growth', 'запуск', 'launch', 'promotion',
                    'диджитал', 'digital', 'осведомлённост', 'осведомленност',
                    'find client', 'attract', 'subscriber', 'user acquisition', 'audience',
                    'sales', 'partner', 'deal', 'customer', 'outreach', 'recruit',
                    'investor', 'funding', 'fundraising', 'community', 'registration',
                    'marketing', 'advertising', 'branding', 'awareness')
    _CONTENT_KW  = ('контент', 'smm', 'reels', 'видео', 'медиаплан',
                    'content', 'video', 'media plan', 'blog', 'article', 'post', 'newsletter')
    _DEV_KW      = ('разработ', 'программ', 'github', 'backend', 'frontend', 'developer', 'деплой',
                    'develop', 'code', 'deploy', 'api', 'software', 'engineer', 'devops')
    _FINANCE_KW  = ('финанс', 'инвест', 'трейд', 'биржа', 'акции', 'облигац', 'крипт', 'доход', 'выручк',
                    'бюджет', 'налог', 'бухгалт', 'profit', 'revenue', 'trading', 'портфель',
                    'finance', 'invest', 'budget', 'tax', 'accounting', 'portfolio', 'crypto',
                    'income', 'expense', 'dividend')
    _ECOMM_KW    = ('магазин', 'товар', 'продукт', 'ozon', 'wildberries', 'shopify', 'авито',
                    'маркетплейс', 'каталог', 'sku', 'остатки', 'заказ', 'клиент-виз', 'выкуп',
                    'wb', 'ecommerce', 'e-commerce', 'интернет-магазин',
                    'store', 'product', 'marketplace', 'catalog', 'inventory', 'order', 'amazon', 'etsy')
    _LEARNING_KW = ('изучить', 'научиться', 'курс', 'обучен', 'практик', 'навык', 'книг', 'читать',
                    'сертификат', 'диплом', 'урок', 'освоить', 'skill', 'учёб', 'прочитать',
                    'лекц', 'workshop', 'тренинг', 'язык программирован', 'английск', 'язык',
                    'learn', 'study', 'course', 'training', 'education', 'certificate', 'lesson',
                    'tutorial', 'practice', 'knowledge')
    _HEALTH_KW   = ('спорт', 'тренировк', 'похудеть', 'похуд', 'здоровь', 'пробежать', 'бег',
                    'марафон', 'питание', 'диета', 'сон', 'медитац', ' кг', 'килограмм',
                    'workout', 'fitness', 'фитнес', 'km', 'км пробег', 'пресс', 'бросить курить',
                    'калори', 'вес тела', 'зарядк', 'йога', 'плавани',
                    'health', 'exercise', 'diet', 'sleep', 'meditation', 'weight loss',
                    'running', 'marathon', 'nutrition', 'gym', 'sport')
    _PERSONAL_KW = ('путешеств', 'поездк', 'привычк', 'ежедневн', 'streak', 'хобби', 'творч',
                    'музыка', 'рисован', 'дневник', 'саморазвит', 'мечт', 'написать книг',
                    'личный проект', 'личная цель', 'жизнь', 'счастье', 'отдых', 'баланс',
                    'habit', 'hobby', 'creative', 'music', 'drawing', 'journal', 'self-improvement',
                    'dream', 'personal goal', 'life', 'happiness', 'relaxation', 'balance')
    _LEGAL_KW    = ('юридическ', 'юрист', 'закон', 'договор', 'лицензия', 'патент',
                    'комплайенс', 'декларация', 'налоговая', 'судебн', 'иск', 'право',
                    'legal', 'lawyer', 'law', 'contract', 'license', 'patent', 'compliance',
                    'litigation', 'court', 'regulation')
    _TRAVEL_KW   = ('путешеств', 'поездк', 'отпуск', 'авиабилет', 'виза', 'маршрут путешеств',
                    'перелёт', 'турпоездк', 'aviasales', 'бронирован отел', 'эмиграция', 'релокация',
                    'travel', 'trip', 'vacation', 'flight', 'visa', 'hotel booking', 'emigration',
                    'relocation', 'tourism', 'destination')
    _STARTUP_KW  = ('стартап', 'startup', 'mvp', 'питч', 'акселератор',
                    'product market fit', 'фандрейзинг', 'валидация гипотез', 'раунд инвест',
                    'pitch', 'accelerator', 'hypothesis validation', 'seed round', 'venture')
    _LOGISTICS_KW = ('логистик', 'грузоперевозк', 'склад хранен', 'сдэк', 'моясклад',
                     'logistics', 'shipping', 'warehouse', 'supply chain', 'delivery', 'freight')
    _HR_KW       = ('найти сотрудник', 'нанять', 'рекрутинг', 'резюме кандидат',
                    'подбор персонал', 'hh.ru', 'headhunter', 'hr менедж', 'вакансия открыт',
                    'hire', 'recruit', 'resume', 'candidate', 'talent', 'staffing',
                    'job opening', 'interview', 'onboarding')
    _gtype_scores = {
        'research':  sum(1 for w in _RESEARCH_KW   if w in _goals_text_all),
        'outreach':  sum(1 for w in _OUTREACH_KW   if w in _goals_text_all),
        'content':   sum(1 for w in _CONTENT_KW    if w in _goals_text_all),
        'dev':       sum(1 for w in _DEV_KW        if w in _goals_text_all),
        'learning':  sum(1 for w in _LEARNING_KW   if w in _goals_text_all),
        'health':    sum(1 for w in _HEALTH_KW     if w in _goals_text_all),
        'personal':  sum(1 for w in _PERSONAL_KW   if w in _goals_text_all),
        'finance':   sum(1 for w in _FINANCE_KW    if w in _goals_text_all),
        'ecommerce': sum(1 for w in _ECOMM_KW      if w in _goals_text_all),
        'legal':     sum(1 for w in _LEGAL_KW      if w in _goals_text_all),
        'travel':    sum(1 for w in _TRAVEL_KW     if w in _goals_text_all),
        'startup':   sum(1 for w in _STARTUP_KW    if w in _goals_text_all),
        'logistics': sum(1 for w in _LOGISTICS_KW  if w in _goals_text_all),
        'hr':        sum(1 for w in _HR_KW         if w in _goals_text_all),
    }
    _best_gtype = max(_gtype_scores.items(), key=lambda x: x[1])
    _goal_type = _best_gtype[0] if _best_gtype[1] > 0 else 'general'

    # ── Scale-awareness: для целей с большой аудиторией ──
    # Если target >= 100 людей — email 1:1 математически не решит задачу.
    # Нужно учить агента думать о рычажных действиях (1 шаг → много людей).
    _scale_block = ''
    if _goal_type in ('outreach', 'general', 'startup', 'hr'):
        _large_targets = []
        for _g in goals_summary:
            _t = _g.get('metric_target') or 0
            _cur = _g.get('metric_current') or 0
            _remaining = _t - _cur
            _g_lower = (_g.get('title', '') + ' ' + (_g.get('description', '') or '')).lower()
            _is_people = any(w in _g_lower for w in ('пользовател', 'подписчик', 'клиент', 'участник', 'лид', 'регистрац', 'member', 'user', 'subscriber'))
            if _is_people and _remaining >= 100:
                _large_targets.append((_g.get('title', ''), _remaining, _t))
        if _large_targets:
            _lt_desc = '; '.join(f"«{t}»: осталось ~{r} из {total}" for t, r, total in _large_targets[:2])
            _scale_block = (
                f"\n\n📐 МАСШТАБ ЦЕЛИ: {_lt_desc}.\n"
                "Осмысли математику: если нужно 100+ человек, то:\n"
                "  • 1 email = 1 потенциальный человек (конверсия холодного email ~2-5%)\n"
                "  • 1 статья на Habr/dev.to = 1 000–50 000 просмотров\n"
                "  • 1 пост в тематическое сообщество = сотни целевых людей\n"
                "  • 1 Product Hunt/релиз = тысячи за день\n"
                "Для МАСШТАБА приоритет стратегии:\n"
                "  1️⃣ Создай КОНТЕНТ (create_post → publish_to_telegram) — органический рост\n"
                "  2️⃣ Выйди в СООБЩЕСТВА через организаторов/администраторов (web_search → email к ЛПР сообщества)\n"
                "  3️⃣ Email — для КЛЮЧЕВЫХ людей: лидеры мнений, инфлюенсеры, организаторы.\n"
                "     НЕ для массового 1:1 outreach — это не масштабируется.\n"
                "Думай: 'Какой 1 шаг сейчас охватит максимум целевых людей?'\n"
            )

    # ── Блок интеграций: универсально из _classify_agent_caps ──
    _intg_connected = []
    for _cat in sorted(_caps_cats):
        _cat_name = _CAP_CATEGORY_NAMES.get(_cat, _cat)
        _cat_hint = _CAP_TOOL_HINTS.get(_cat, '')
        # GitHub — расширенная подсказка с цепочками
        if _cat == 'git':
            _intg_connected.append(
                '✅ GitHub/GitLab — search_users(query="language:python followers:>5"), '
                'search_repos, list_issues, comment_on_issue, star_repo\n'
                '  🔗 Цепочки: search_users → save_email_contact → send_outreach_email | '
                'list_issues → comment_on_issue (нетворкинг) | search_repos → research_topic → create_post\n'
                '  🔄 Пагинация: page=2,3... если все contacted. Меняй query каждый цикл.'
            )
        elif _cat == 'email':
            _intg_connected.append(
                '✅ Email — check_emails (только этот агент может читать входящие!), '
                'reply_to_outreach_email, send_outreach_email, find_relevant_contacts_for_task\n'
                '  ⚠️ Если контакт предлагает созвон — НЕ назначай сам, сначала спроси пользователя: send_message_to_user'
            )
        elif _cat == 'crm':
            _intg_connected.append(
                '✅ CRM — run_agent_action(action="get_contacts", params={"query":"имя или email"}) — поиск контактов\n'
                '  ⛔ ВАЖНО: create_lead БЕЗ pipeline_id+status_id → сделка попадёт в «Неразобранное»! Сначала вызови get_pipelines!\n'
                '  run_agent_action(action="get_pipelines") — СНАЧАЛА получи реальные pipeline_id и status_id этапов\n'
                '  run_agent_action(action="create_lead", params={"name":"Название сделки", "price":5000, "pipeline_id":"<ID>", "status_id":"<ID>"}) — создать сделку В НУЖНОМ ЭТАПЕ\n'
                '  run_agent_action(action="update_lead", params={"id":12345, "status_id":142, "pipeline_id":789}) — передвинуть по воронке\n'
                '  run_agent_action(action="create_contact", params={"name":"Имя", "email":"...", "phone":"..."})\n'
                '  run_agent_action(action="link_contact", params={"lead_id":..., "contact_id":...}) — привязать контакт к сделке\n'
                '  run_agent_action(action="add_note", params={"id":12345, "entity_type":"leads", "text":"..."})\n'
                '  🔗 Цепочки: get_pipelines → create_lead(с pipeline_id+status_id) → link_contact | update_lead (двинуть по воронке)'
            )
        elif _cat == 'marketplace':
            _intg_connected.append(
                '✅ Маркетплейс — run_agent_action() без action → аналитика (продажи, товары, заказы)\n'
                '  WB: выручка и топ-артикулы за 7 дней | Ozon: выручка/заказы/возвраты за 30 дней\n'
                '  Shopify: последние 10 заказов | Яндекс.Маркет: первые 5 товаров\n'
                '  🔗 Цепочки: run_agent_action → save_note → send_message_to_user (алерт)\n'
                '  ⚠️ Только чтение. Для write-операций нужен кастомный скрипт агента.'
            )
        elif _cat == 'pm':
            _intg_connected.append(
                '✅ Трекер задач — run_agent_action() без action → список задач\n'
                '  Jira: action="create_issue", params={"summary":"...","description":"...","priority":"Medium"}\n'
                '  Trello: action="create_card", params={"name":"...","desc":"...","list_id":"..."}\n'
                '  ClickUp: action="create_task", params={"name":"...","description":"...","priority":3}\n'
                '  Linear: action="create_issue", params={"title":"...","description":"..."}\n'
                '  🔗 Цепочки: run_agent_action → delegate_task | create_issue → update_goal_progress\n'
                '  ⚠️ action-имена зависят от конкретного трекера! Используй точное имя.'
            )
        elif _cat == 'hr':
            _intg_connected.append(
                '✅ HR / Работа (hh.ru/SuperJob) — run_agent_action() без action → список вакансий по запросу\n'
                '  hh.ru: ищет вакансии по HH_QUERY в области HH_AREA (1=Москва, 2=СПб, 113=Россия)\n'
                '  SuperJob: последние 5 вакансий по запросу\n'
                '  🔗 Цепочки: run_agent_action → save_note → send_message_to_user\n'
                '  ⚠️ Только чтение (поиск). Для отклика на вакансию нужен кастомный скрипт.'
            )
        elif _cat == 'database':
            _intg_connected.append(
                '✅ БД — PostgreSQL: run_agent_action(action="query", params={"query":"SELECT ..."}) → до 20 строк\n'
                '  MongoDB: run_agent_action(action="insert", params={"document":{...}}) | без action → поиск\n'
                '  Firebase: run_agent_action(action="add", params={"fields":{...}}) | без action → листинг\n'
                '  🔗 Цепочки: query → research_topic (анализ) | insert/add → update_goal_progress\n'
                '  ⚠️ PostgreSQL: только SELECT (read-only). MongoDB/Firebase: insert/add + чтение.'
            )
        elif _cat == 'ai_api':
            _intg_connected.append(
                '✅ AI/LLM API — run_agent_action(action="ask"|"analyze", params={"prompt":"..."})\n'
                '  🔗 Цепочки: get_news → analyze → create_post | web_search → ask → publish_to_telegram\n'
                '  ⚠️ action-имена точно из python_code агента.'
            )
        elif _cat == 'analytics':
            # Ищем конкретные action-имена для аналитики из python_code
            _analytics_actions = [a for a in _py_actions if any(kw in a.lower() for kw in ('metric', 'metrik', 'analytic', 'report', 'stat', 'ga', 'counter'))]
            if _analytics_actions:
                _aa_str = ', '.join(f'"{a}"' for a in _analytics_actions[:3])
                _intg_connected.append(
                    f'✅ Аналитика (Яндекс.Метрика / GA) — run_agent_action(action={_aa_str})\n'
                    f'  🔗 Цепочки: run_agent_action → save_note → send_message_to_user\n'
                    f'  ✅ Интеграция ПОДКЛЮЧЕНА — используй для сбора данных о посещаемости, конверсиях, аудитории.'
                )
            else:
                _intg_connected.append(
                    '✅ Аналитика (Яндекс.Метрика / GA) — run_agent_action() без action → сводный отчёт\n'
                    '  run_agent_action(action="get_metrics", params={"period":"7d"}) — данные за период\n'
                    '  🔗 Цепочки: run_agent_action → save_note → send_message_to_user (алерт)\n'
                    '  ✅ Интеграция ПОДКЛЮЧЕНА — используй для аналитики трафика и аудитории.'
                )
        elif _cat_hint:
            _intg_connected.append(f'✅ {_cat_name}: {_cat_hint}')
        else:
            _intg_connected.append(f'✅ {_cat_name}')

    # ── Авто-обнаружение ACTION-хендлеров из python_code ──
    if _py_actions:
        _intg_connected.append(
            f'✅ Кастомные action (из скрипта агента): {", ".join(_py_actions[:12])}\n'
            f'  → Используй: run_agent_action(action="<одно из выше>", params={{...}})'
        )

    # ── Рекомендации: что подключить под текущие цели ──
    _intg_missing = []
    import os as _os_bap
    _fin_kw = ('нефт', 'газ', 'рынок', 'биржа', 'акции', 'финанс', 'трейд', 'инвест', 'криптo', 'oil', 'stock', 'forex', 'валют')
    _dev_kw = ('разработ', 'программ', 'github', 'code', 'репозитор', 'деплой')
    _news_kw = ('новост', 'мониторинг', 'тренды', 'медиа', 'сми', 'пресс', 'обзор рынка')
    _ppl_kw  = ('пользовател', 'тестировщик', 'клиент', 'подписчик', 'аудитор', 'рекрутинг')
    _hr_kw   = ('вакансия', 'кандидат', 'найм', 'нанять', 'рекрутинг', 'резюме', 'подбор персонал', 'hr менедж', 'найти сотрудник')
    _cnt_kw  = ('контент', 'smm', 'публикац', 'канал', 'посты')
    _team_kw = ('команд', 'сотрудник', 'коллег', 'менеджер', 'hr', 'координац')
    _proj_kw = ('проект', 'задач', 'sprint', 'kanban', 'agile', 'трекер', 'backlog')
    _doc_kw  = ('документ', 'wiki', 'база знаний', 'заметк', 'confluence', 'notion')
    _crm_kw  = ('продаж', 'лид', 'сделк', 'воронк', 'crm', 'клиентск')
    _ecom_kw = ('маркетплейс', 'wildberries', 'ozon', 'shopify', 'товар', 'карточк', 'остатк', 'склад', 'инвентар')
    _crypto_kw2 = ('биткоин', 'bitcoin', 'btc', 'eth', 'крипто', 'crypto', 'binance', 'bybit', 'трейдинг', 'coingecko')
    _data_kw = ('отчёт', 'аналитик', 'kpi', 'дашборд', 'таблиц', 'excel', 'sheets', 'данн')
    _adv_kw  = ('реклам', 'директ', 'контекстн', 'рекламн кампан', 'яндекс директ', 'cpc', 'cpm', 'объявлен')

    if any(w in _goals_text_all for w in _fin_kw):
        if 'finance' not in _caps_cats:
            _intg_missing.append('⚡ Alpha Vantage — котировки нефти/акций/металлов (ALPHAVANTAGE_API_KEY в настройках агента)')
        if 'news' not in _caps_cats and not _os_bap.getenv('NEWSAPI_KEY'):
            _intg_missing.append('⚡ NewsAPI — поток финансовых новостей (NEWSAPI_KEY в настройках агента)')
    if any(w in _goals_text_all for w in _news_kw):
        if 'news' not in _caps_cats and not _os_bap.getenv('NEWSAPI_KEY'):
            _intg_missing.append('⚡ NewsAPI — 100+ источников новостей (NEWSAPI_KEY в настройках агента)')
        if 'rss' not in _caps_cats:
            _intg_missing.append('⚡ RSS — добавь RSS_URL= в API-ключи агента для мониторинга лент')
    if any(w in _goals_text_all for w in _dev_kw):
        if 'git' not in _caps_cats and not _os_bap.getenv('GITHUB_TOKEN'):
            _intg_missing.append('⚡ GitHub Token — поиск разработчиков/контрибьюторов (GITHUB_TOKEN в дашборде: https://asibiont.com/dashboard)')
    if any(w in _goals_text_all for w in _ppl_kw):
        if 'email' not in _caps_cats:
            _intg_missing.append('⚡ Email — добавь GMAIL_USER + пароль приложения в дашборде: https://asibiont.com/dashboard')
    if any(w in _goals_text_all for w in _hr_kw):
        if 'hr' not in _caps_cats:
            _intg_missing.append('⚡ hh.ru / SuperJob — поиск кандидатов и резюме (HH_QUERY + HH_AREA в настройках агента)')
    if any(w in _goals_text_all for w in _cnt_kw):
        if not _has_content:
            _intg_missing.append('⚡ Telegram Bot Token — публикация постов в канал (TELEGRAM_BOT_TOKEN в дашборде: https://asibiont.com/dashboard)')
    if any(w in _goals_text_all for w in _team_kw):
        if 'slack' not in _caps_cats:
            _intg_missing.append('⚡ Slack — координация с командой (SLACK_BOT_TOKEN в настройках агента)')
    if any(w in _goals_text_all for w in _proj_kw):
        if 'pm' not in _caps_cats:
            _intg_missing.append('⚡ Trello/Jira/Asana — управление проектами и задачами (ключи в настройках агента)')
    if any(w in _goals_text_all for w in _doc_kw):
        if 'notion' not in _caps_cats:
            _intg_missing.append('⚡ Notion — база знаний и документация (NOTION_TOKEN в настройках агента)')
    if any(w in _goals_text_all for w in _crm_kw):
        if 'crm' not in _caps_cats:
            _intg_missing.append('⚡ CRM (AmoCRM/Bitrix24/HubSpot) — воронка продаж и лиды (ключи в настройках)')
    if any(w in _goals_text_all for w in _ecom_kw):
        if 'marketplace' not in _caps_cats:
            _intg_missing.append('⚡ Маркетплейс (WB/Ozon/МойСклад) — статистика продаж и остатков (API-ключ в настройках)')
    if any(w in _goals_text_all for w in _crypto_kw2):
        if 'crypto' not in _caps_cats:
            _intg_missing.append('⚡ Binance/Bybit/CoinGecko — криптовалютные данные и трейдинг (API-ключ в настройках)')
    if any(w in _goals_text_all for w in _data_kw):
        if 'sheets' not in _caps_cats:
            _intg_missing.append('⚡ Google Sheets / Airtable — автоматические отчёты и дашборды (ключи в настройках)')
    if any(w in _goals_text_all for w in _adv_kw):
        if 'advertising' not in _caps_cats:
            _intg_missing.append('⚡ Яндекс.Директ — управление рекламными кампаниями (YANDEX_DIRECT_TOKEN в настройках агента)')

    _intg_block = ''
    if _intg_connected or _intg_missing:
        _intg_block = '\nИНТЕГРАЦИИ АГЕНТА (используй ТОЛЬКО из этого списка):\n'
        if _intg_connected:
            _intg_block += '\n'.join(f'  {x}' for x in _intg_connected) + '\n'
        _intg_block += '  ✅ web_search, research_topic — всегда доступны\n'
        _intg_block += '→ Нет в списке = сначала попробуй web_search/research_topic. Если без интеграции задача невыполнима — скажи пользователю что подключить, один раз.\n'
        if _intg_missing:
            _intg_block += 'ДОСТУПНО ДЛЯ ПОДКЛЮЧЕНИЯ (под текущие цели):\n'
            _intg_block += '\n'.join(f'  {x}' for x in _intg_missing) + '\n'
        # Goal-type-aware первый шаг
        if _goal_type == 'research' and _has_alpha:
            _goals_text_lower = ' '.join(
                (g.get('title', '') + ' ' + (g.get('description', '') or '')).lower()
                for g in goals_summary
            )
            _OIL_KW = ('нефт', 'brent', 'wti', 'oil', 'газ', 'gas', 'котировк', 'баррель', 'crude')
            _CRYPTO_KW = ('биткоин', 'bitcoin', 'btc', 'eth', 'крипто', 'crypto')
            _STOCK_KW = ('акции', 'лукойл', 'газпром', 'sber', 'lkoh', 'gazp', 'фондов', 'moex')
            if any(w in _goals_text_lower for w in _OIL_KW):
                _symbol_hint = '"BRENT" или "WTI"'
            elif any(w in _goals_text_lower for w in _CRYPTO_KW):
                _symbol_hint = '"BTC" или "ETH"'
            elif any(w in _goals_text_lower for w in _STOCK_KW):
                _symbol_hint = '"LKOH.MCX" или "GAZP.MCX"'
            else:
                _symbol_hint = '"[тикер по теме цели]"'
            _intg_block += (
                f'⚡ ПЕРВЫЙ ШАГ: run_agent_action(action="get_price", symbol={_symbol_hint}) '
                '— получи реальные данные, затем обработай и опубликуй итог.\n'
            )
        elif _goal_type == 'research' and _has_news:
            _intg_block += (
                '⚡ ПЕРВЫЙ ШАГ: run_agent_action(action="get_news", query="[тема цели]") '
                '— получи свежие данные, затем сформируй аналитический вывод.\n'
            )
        elif _goal_type == 'research' and _has_rss:
            _intg_block += (
                '⚡ ПЕРВЫЙ ШАГ: run_agent_action(action="[ТОЧНОЕ_RSS_ACTION_ИЗ_СПИСКА_АГЕНТА]") '
                '— получи данные из RSS-ленты агента, затем извлеки суть.\n'
            )
        else:
            _intg_block += '→ Используй подключённые интеграции в ПЕРВУЮ очередь — они дают реальные данные.\n'
        _intg_block += (
            '→ Если инструмент вернул ошибку — попробуй альтернативу (web_search, research_topic). '
            'Если задачу НЕВОЗМОЖНО решить без этой интеграции — сообщи пользователю один раз: '
            'что именно не получилось и какую интеграцию подключить в https://asibiont.com/dashboard.\n'
        )

    # ── История инструментов → что запрещено/предупреждение ──
    _tool_cnt: dict = {}
    _last_tools: list = []   # последовательность инструментов (для детектора зацикливания)
    if agent_history:
        for _h in agent_history:
            _m = _re_ap.search(r'\[([^\]]+)\]', _h)
            if _m:
                for _t in _m.group(1).split(','):
                    _t = _t.strip()
                    if _t:
                        _tool_cnt[_t] = _tool_cnt.get(_t, 0) + 1
                        _last_tools.append(_t)
    # Инструменты которые ЗАКОННО вызываются много раз (каждый раз новый получатель/запрос)
    # Не банить по имени — банить по результату (это делается через _failed_tools в context_data)
    _MULTI_USE_OK = {
        'send_outreach_email', 'check_emails', 'reply_to_outreach_email',
        'send_follow_up_email', 'save_email_contact',
        'find_relevant_contacts_for_task', 'update_goal_progress',
    }
    # GitHub/RSS-агенты: run_agent_action не банить — каждый вызов уникален
    # (GitHub: разные query/pages; RSS: данные меняются каждый час)
    if _has_github or _has_rss:
        _MULTI_USE_OK.add('run_agent_action')
    # Не баним инструменты — даём ИИ контекст сколько раз уже использовал.
    # Бан блокировал web_search/research_topic/create_post после 2 вызовов,
    # хотя каждый вызов может быть с новым запросом/контентом.
    _banned: set = set()  # оставляем пустым — ИИ сам решает что использовать
    _tool_use_counts = {t: n for t, n in _tool_cnt.items() if n >= 2 and t not in _MULTI_USE_OK}
    _warn   = {t for t, n in _tool_cnt.items() if n == 1}

    # check_emails: мягкая подсказка при частом использовании
    _check_emails_overuse = ''
    _ce_count = _tool_cnt.get('check_emails', 0)
    if _ce_count >= 4:
        _check_emails_overuse = (
            f"\n📧 check_emails вызывался {_ce_count} раз за последние циклы. "
            "Может быть полезно сфокусироваться на новых контактах или контенте — "
            "новые письма появятся быстрее если есть новые отправки.\n"
        )

    # Детектор петли: последние 3 инструментa одинаковые → форсируем analyse
    _force_analyse = False
    _loop_context = ''  # Диагностика петли — показываем ИИ ПОЧЕМУ сработало
    if len(_last_tools) >= 3 and len(set(_last_tools[-3:])) == 1:
        _force_analyse = True
        _rep_tool = _last_tools[-1]
        _loop_context = (
            f"\n🔄 ДЕТЕКТОР ПЕТЛИ: инструмент «{_rep_tool}» вызван 3 раза подряд без смены подхода. "
            f"Это продуктивно или петля? Если каждый вызов давал НОВЫЕ данные — ты можешь продолжить, "
            f"но объясни что изменилось. Если результат одинаковый — СМЕНИ инструмент.\n"
        )
    # Детектор типичных унылых петель:
    # Расширенный набор «только поиск» — включает LLM-исследование и анализ
    _SEARCH_ONLY = {
        'web_search', 'research_topic', 'get_news_trends',
    }
    # Для RSS-агентов run_agent_action — основной рабочий инструмент, не признак петли
    _RSS_WORK_TOOLS = {'run_agent_action', 'research_topic', 'create_post',
                       'schedule_background_task', 'web_search'} if _has_rss else set()
    _SAVE_ONLY   = {'save_email_contact'}
    _PROGRESS_ONLY = {'update_goal_progress'}
    _last4 = _last_tools[-4:]
    _last5 = _last_tools[-5:]
    # Детектор зацикливания: 2 инструмента чередуются (типичный ping-pong)
    _last6 = _last_tools[-6:]
    _is_pingpong = (
        len(_last6) >= 4
        and len(set(_last6)) <= 2
        and len(_last6) >= 4
        # RSS ping-pong: run_agent_action ↔ research_topic это НОРМАЛЬНЫЙ рабочий цикл
        and not (_has_rss and set(_last6) <= _RSS_WORK_TOOLS)
    )
    _is_trivial_loop = (
        # Ping-pong между двумя инструментами (web_search ↔ update_goal_progress и т.д.)
        _is_pingpong
        # Только поиск + обновление прогресса
        or (len(_last4) >= 4 and all(t in _SEARCH_ONLY | _PROGRESS_ONLY for t in _last4))
        # Поиск → сохранить: цикл без отправки (НЕ для RSS — у него нет email-отправки)
        or (len(_last4) >= 4 and all(t in _SEARCH_ONLY | _SAVE_ONLY for t in _last4)
            and not any(t in ('send_outreach_email', 'negotiate_by_email', 'find_and_message_relevant_users')
                        for t in _last_tools)
            and not _has_rss)
        # Только сохранение контактов без отправки
        or (len(_last5) >= 5 and all(t in _SAVE_ONLY for t in _last5))
        # Только поиск (без email и без делегирования) — 4+ циклов (НЕ для RSS-агентов)
        or (len(_last4) >= 4 and all(t in _SEARCH_ONLY for t in _last4) and not _has_rss)
    )
    if _is_trivial_loop:
        _force_analyse = True
        # Диагностируем какой именно паттерн петли — ИИ получает контекст
        if _is_pingpong:
            _ping_tools = sorted(set(_last6))
            _loop_context += (
                f"\n🔄 ПАТТЕРН ПИНГ-ПОНГ: постоянно чередуешь {' ↔ '.join(_ping_tools[:2])}. "
                "Это не прогресс. Реши сам: какой ТРЕТИЙ инструмент даст реальный результат?\n"
            )
        elif all(t in _SEARCH_ONLY for t in _last4) and not _has_rss:
            _loop_context += (
                "\n🔄 ПАТТЕРН ПОИСК БЕЗ ДЕЙСТВИЯ: 4 цикла только поиск, ни одного контакта/письма/поста. "
                "Исследование закончено — пора действовать. Что ты реально можешь сделать с найденным?\n"
            )
        elif all(t in _SEARCH_ONLY | _SAVE_ONLY for t in _last4) and not _has_rss:
            _loop_context += (
                "\n🔄 ПАТТЕРН ПОИСК+СОХРАНЕНИЕ: контакты сохраняются, но письма не отправляются. "
                "Рекомендуется: send_outreach_email или negotiate_by_email тем кто уже в базе.\n"
            )

    # ── RSS-specific loop: агент читает RSS 3+ раз без публикации/делегирования ──
    _used_tools = set(_tool_cnt.keys())
    if _has_rss:
        _rss_reads = _tool_cnt.get('run_agent_action', 0)
        _has_rss_output = any(
            t in ('create_post', 'publish_to_telegram', 'publish_to_discord')
            for t in _used_tools
        )
        if _rss_reads >= 3 and not _has_rss_output:
            _loop_context += (
                f"\n📰 RSS-ПЕТЛЯ: ты вызвал run_agent_action {_rss_reads} раз, но ни разу не создал пост или задачу. "
                "RSS — это сырой материал, не самоцель. Прямо сейчас выбери один из вариантов:\n"
                "  A) create_post — напиши аналитику/инсайт по прочитанным статьям\n"
                "  B) research_topic — углуби знания по самой интересной теме и создай полезный summary\n"
                "Не читай RSS ещё раз пока не сделаешь что-то с уже прочитанным.\n"
            )

    # Инструменты которые агент ЕЩЁ НЕ пробовал — адаптированы под тип агента
    if _has_rss and not _has_imap and not _has_github:
        # RSS-only агент: только реально доступные инструменты
        _ALL_ACTION_TOOLS = [
            'run_agent_action', 'research_topic', 'web_search', 'create_post',
            'publish_to_telegram', 'schedule_background_task',
            'get_news_trends', 'update_goal_progress',
        ]
    else:
        _ALL_ACTION_TOOLS = [
            'send_outreach_email', 'negotiate_by_email', 'find_and_message_relevant_users',
            'create_post', 'publish_to_telegram', 'start_delegation_campaign',
            'find_relevant_contacts_for_task',
            'send_follow_up_email', 'schedule_background_task', 'set_contact_alert',
            'research_topic', 'get_news_trends', 'web_search',
            'generate_image', 'publish_to_discord', 'list_email_contacts',
            'save_email_contact', 'update_goal_progress',
        ]
    _untried = [t for t in _ALL_ACTION_TOOLS if t not in _used_tools and t not in _banned]
    _untried_block = ''
    if _untried and agent_history:  # показываем только если есть история (есть что менять)
        _untried_show = _untried[:8]
        _untried_block = (
            f"\n✨ ЕЩЁ НЕ ПРОБОВАЛ (0 раз за всё время): "
            + ', '.join(_untried_show)
            + ('...' if len(_untried) > 8 else '')
            + "\nСмело попробуй любой из них — разнообразие = результат.\n"
        )

    # Goal-state директива: умная подсказка о прогрессе и следующем шаге
    _goal_state_hint = ''
    if goals_summary:
        _zero_progress = [g for g in goals_summary if (g.get('progress', 0) or 0) == 0]
        _stuck_goals = [g for g in goals_summary if (g.get('progress', 0) or 0) < 10]
        _has_outreach_done = any(t in ('send_outreach_email', 'negotiate_by_email', 'find_and_message_relevant_users')
                                  for t in _used_tools)
        _has_search_done = any(t in ('web_search', 'research_topic', 'get_news_trends') for t in _used_tools)
        _has_find_contacts = 'find_relevant_contacts_for_task' in _used_tools or 'save_email_contact' in _used_tools

        # Показываем конкретную метрику вместо абстрактного "0%"
        _metric_hints = []
        for _g_sh in goals_summary:
            _mc_sh = _g_sh.get('metric_current', 0) or 0
            _mt_sh = _g_sh.get('metric_target')
            _prog_sh = _g_sh.get('progress', 0) or 0
            if _mt_sh and _mc_sh > 0:
                _metric_hints.append(
                    f"«{_g_sh.get('title','')[:40]}»: {int(_mc_sh)}/{int(_mt_sh)} ({int(_prog_sh)}%)"
                )

        if _metric_hints:
            _goal_state_hint = (
                f"\n📊 РЕАЛЬНЫЙ ПРОГРЕСС: {'; '.join(_metric_hints)}\n"
                "→ Метрика растёт ТОЛЬКО при реальном ответе/подтверждении интереса от человека снаружи.\n"
            )

        # Per-goal-type "next step" prompts instead of universal email push
        _next_step_by_type = {
            'outreach': (
                "\n🚨 ПРОГРЕСС 0%: поиск сделан, но не было ни одного контакта/письма. "
                "Следующий шаг — выход на НОВЫХ ВНЕШНИХ людей: "
                "web_search(ищи тех кого НЕТ в базе) → save_email_contact → send_outreach_email. "
                "⚠️ find_and_message_relevant_users — только ВНУТРИ платформы, не для поиска новых.\n"
            ),
            'general': (
                "\n🚨 ПРОГРЕСС 0%: поиск сделан, но не было ни одного контакта/письма. "
                "Следующий шаг — выход на НОВЫХ ВНЕШНИХ людей: "
                "web_search → save_email_contact → send_outreach_email или find_and_message_relevant_users.\n"
            ),
            'dev': (
                "\n🚨 ПРОГРЕСС 0%: исследование готово — пора СТРОИТЬ и ПОКАЗЫВАТЬ. "
                "Следующий шаг: create_issue/comment_on_issue (GitHub), create_post с кейсом, "
                "или DELEGATE[разработчик]: конкретная техническая задача с деталями.\n"
            ),
            'content': (
                "\n🚨 ПРОГРЕСС 0%: материал есть — пора ПУБЛИКОВАТЬ. "
                "Следующий шаг: create_post → publish_to_telegram. "
                "Контент без публикации не работает. Опубликуй прямо сейчас.\n"
            ),
            'startup': (
                "\n🚨 ПРОГРЕСС 0%: анализ готов — пора к ПЕРВЫМ ПОЛЬЗОВАТЕЛЯМ. "
                "Следующий шаг: web_search('[ниша] beta tester email') → save_email_contact → send_outreach_email, "
                "или create_post с описанием проблемы → publish_to_telegram.\n"
            ),
            'research': (
                "\n🚨 ПРОГРЕСС 0%: данные собраны — пора СИНТЕЗИРОВАТЬ и ПУБЛИКОВАТЬ. "
                "Следующий шаг: create_post с выводами → publish_to_telegram. "
                "Исследование без вывода = незавершённая задача.\n"
            ),
            'learning': (
                "\n🚨 ПРОГРЕСС 0%: информация найдена — пора ПРИМЕНЯТЬ. "
                "Следующий шаг: сделай первое упражнение/мини-проект по найденным материалам, "
                "или create_post с резюме урока → publish_to_telegram (обучение через преподавание).\n"
            ),
            'health': (
                "\n🚨 ПРОГРЕСС 0%: план есть — пора ДЕЛАТЬ. "
                "Следующий шаг: выполни первую тренировку и update_goal_progress с реальными данными. "
                "schedule_background_task — напоминание на следующий сеанс.\n"
            ),
            'finance': (
                "\n🚨 ПРОГРЕСС 0%: анализ готов — пора ДЕЙСТВОВАТЬ. "
                "Следующий шаг: update_goal_progress с реальными числами (баланс/сделка/доход), "
                "или web_search('[инструмент] как купить/вложить') → конкретный план действий.\n"
            ),
            'hr': (
                "\n🚨 ПРОГРЕСС 0%: поиск сделан — пора КОНТАКТИРОВАТЬ с кандидатами. "
                "Следующий шаг: web_search('hh.ru [роль] email') → save_email_contact → send_outreach_email, "
                "или find_relevant_contacts_for_task — кандидаты внутри платформы.\n"
            ),
            'travel': (
                "\n🚨 ПРОГРЕСС 0%: план есть — пора БРОНИРОВАТЬ. "
                "Следующий шаг: забронируй первый элемент (билет/отель) и update_goal_progress.\n"
            ),
            'ecommerce': (
                "\n🚨 ПРОГРЕСС 0%: анализ готов — пора ПРОДАВАТЬ. "
                "Следующий шаг: create_post с товаром → publish_to_telegram, "
                "или web_search('[ниша] поставщик email') → save_email_contact → send_outreach_email.\n"
            ),
        }

        if _zero_progress and not _has_outreach_done and _has_search_done:
            if _has_rss and not _has_imap and not _has_github:
                # RSS-only: не отправляет письма сам — делегирует
                _goal_state_hint += (
                    "\n📊 СТАТУС: поиск/анализ сделан. Твоя следующая задача — "
                    "DELEGATE[email-агент]: передай ключевые инсайты и идеи для outreach-писем.\n"
                )
            else:
                _goal_state_hint += _next_step_by_type.get(
                    _goal_type,
                    _next_step_by_type['general']
                )
        elif _stuck_goals and agent_history and len(agent_history) >= 4 and not _has_outreach_done:
            if _has_rss and not _has_imap and not _has_github:
                _goal_state_hint += (
                    "\n📡 АНАЛИТИК: ты мониторишь тренды — хорошо. Теперь передай лучшие инсайты "
                    "через DELEGATE[email-агент] чтобы команда использовала их в outreach.\n"
                )
            elif _has_imap:
                # Email-агент с IMAP — проверяем реальные результаты outreach
                try:
                    from models import EmailOutreach as _EO_gs, EmailContact as _EC_gs
                    from models import Session as _Session_gs
                    _sess_gs = _Session_gs()
                    try:
                        _gs_replied = _sess_gs.query(_EO_gs).filter(
                            _EO_gs.user_id == user.id,
                            _EO_gs.status == 'replied',
                        ).count() if user else 0
                        # Fallback: считаем через email_contacts.status если email_outreach не обновился
                        if _gs_replied == 0 and user:
                            _gs_replied = _sess_gs.query(_EC_gs).filter(
                                _EC_gs.user_id == user.id,
                                _EC_gs.status.in_(['replied', 'interested']),
                            ).count()
                    finally:
                        _sess_gs.close()
                    if _gs_replied > 0:
                        _g0t = goals_summary[0].get('title', '') if goals_summary else ''
                        _g0_target = goals_summary[0].get('metric_target') if goals_summary else None
                        _g0_mc = goals_summary[0].get('metric_current', 0) or 0
                        # Используем metric_current (реальные ответы), а не произвольный % через progress
                        # progress=N% без metric_current обходит guard и даёт ложный % относительно target
                        _goal_state_hint += (
                            f"\n📊 РЕАЛЬНЫЙ РЕЗУЛЬТАТ: {_gs_replied} человек ответили на outreach!\n"
                            f"→ Ответ на письмо ≠ подтверждённый интерес. Уточни с каждым готовность участвовать.\n"
                            f"→ Если подтверждение получено → update_goal_progress("
                            f"goal_title=\"{_g0t[:80]}\", metric_current={int(_g0_mc) + _gs_replied}, "
                            f"notes=\"{_gs_replied} откликнулись на outreach\" )\n"
                            f"→ С заинтересованными — продолжай диалог, отправляй ссылку https://asibiont.com.\n"
                        )
                    else:
                        _goal_state_hint += (
                            "\n⚠️ ПРОГРЕСС ЗАСТЫЛ: письма отправлены, но ответов нет. "
                            "Попробуй другую тему/аудиторию: web_search → save_email_contact → send_outreach_email.\n"
                        )
                except Exception:
                    _goal_state_hint += (
                        "\n⚠️ ПРОГРЕСС ЗАСТЫЛ: письма отправлены, но ответов нет пока. "
                        "Попробуй другую тему или аудиторию.\n"
                    )
            else:
                # Non-email goal types: push the right "next action" for this goal type
                _stuck_next = {
                    'outreach': (
                        "\n⚠️ НЕТ КОНТАКТОВ: ты уже ищешь/исследуешь, но нет ни одного письма/сообщения. "
                        "Пора действовать: найди НОВЫХ людей которых нет в системе — "
                        "web_search/GitHub search → save_email_contact → send_outreach_email. "
                        "Не предлагай людей уже из базы как новых лидов.\n"
                    ),
                    'general': (
                        "\n⚠️ НЕТ КОНТАКТОВ: ты уже ищешь, но нет ни одного контакта/действия. "
                        "Пора действовать: web_search → save_email_contact → send_outreach_email или "
                        "create_post → publish_to_telegram.\n"
                    ),
                    'dev': (
                        "\n⚠️ ПРОГРЕСС ЗАСТЫЛ: 4+ цикла исследования без реального шага. "
                        "Пора: create_issue, comment_on_issue, или create_post с конкретным техническим "
                        "выводом. DELEGATE[разработчик]: дай конкретную задачу с деталями.\n"
                    ),
                    'content': (
                        "\n⚠️ ПРОГРЕСС ЗАСТЫЛ: материал есть, но нет публикации. "
                        "Пора: create_post → publish_to_telegram прямо сейчас. Не откладывай.\n"
                    ),
                    'startup': (
                        "\n⚠️ ПРОГРЕСС ЗАСТЫЛ: исследование готово — нет выхода к пользователям. "
                        "Пора: web_search('[ниша] users email') → send_outreach_email, "
                        "или create_post с описанием MVP → publish_to_telegram.\n"
                    ),
                    'research': (
                        "\n⚠️ ПРОГРЕСС ЗАСТЫЛ: данные собраны но нет публикации/вывода. "
                        "Пора: create_post с ключевыми инсайтами → publish_to_telegram.\n"
                    ),
                    'learning': (
                        "\n⚠️ ПРОГРЕСС ЗАСТЫЛ: изучено, но нет практического шага. "
                        "Пора: сделай упражнение/мини-проект, или создай краткое резюме → "
                        "create_post → publish_to_telegram.\n"
                    ),
                    'health': (
                        "\n⚠️ ПРОГРЕСС ЗАСТЫЛ: план есть, но нет фактически выполненных тренировок. "
                        "Обнови: update_goal_progress с реальными данными. "
                        "schedule_background_task — напоминание на следующий сеанс.\n"
                    ),
                    'finance': (
                        "\n⚠️ ПРОГРЕСС ЗАСТЫЛ: анализ готов, но нет реального действия. "
                        "Пора: update_goal_progress с числами, или web_search конкретного шага.\n"
                    ),
                    'hr': (
                        "\n⚠️ ПРОГРЕСС ЗАСТЫЛ: поиск готов, но нет контакта с кандидатами. "
                        "Пора: send_outreach_email кандидатам, или find_and_message_relevant_users "
                        "для охвата внутри платформы.\n"
                    ),
                }
                _goal_state_hint += _stuck_next.get(_goal_type, _stuck_next.get('general', (
                    "\n⚠️ ПРОГРЕСС ЗАСТЫЛ: 4+ цикла без нового результата. "
                    "Сменить подход: другой канал, другая аудитория, или DELEGATE коллеге.\n"
                )))
        elif _has_find_contacts and _has_outreach_done and not _metric_hints:
            # Агент искал и отправлял, но метрика не отображается — значит прогресс есть
            _goal_state_hint += (
                "\n✅ ПРОГРЕСС ИДЁТ: контакты сохранены, письма отправлены. "
                "Продолжай outreach и проверяй ответы через check_emails.\n"
            )

    def _ti(name: str) -> str:
        """Показывает инструмент с количеством использований если уже применялся."""
        _cnt = _tool_use_counts.get(name)
        return f"{name} (×{_cnt})" if _cnt else name

    # ── Динамический каталог инструментов ──
    # Пытаемся получить описания из платформы; при ошибке — компактный встроенный список
    _tool_descs: dict = {}
    try:
        from ai_integration.tools import get_available_tools as _gat
        for _td in _gat(None):
            _fn = _td.get('function', {})
            _name = _fn.get('name', '')
            _desc = _fn.get('description', '')
            # Берём первое предложение (до первой точки/переноса) — кратко
            _short = _desc.split('.')[0].split('\n')[0][:80]
            _tool_descs[_name] = _short
    except Exception as _e:
        logger.debug("suppressed: %s", _e)

    def _td(name: str) -> str:
        # Пропускаем исключённые инструменты — не показываем агенту
        from ai_integration.tools import EXCLUDED_TOOLS as _ET
        if name in _ET:
            return ''
        if name == 'publish_to_telegram' and not (_agent_has_tg or _user_tg_ch):
            return '  ℹ️ publish_to_telegram — недоступен (нет Telegram-бота агента или канала пользователя)'
        if name == 'publish_to_discord' and not (_agent_has_discord or _user_discord_wh):
            return '  ℹ️ publish_to_discord — недоступен (нет Discord webhook/канала пользователя)'
        desc = _tool_descs.get(name, '')
        base = f"{_ti(name)}" + (f" — {desc}" if desc else '')
        return f"  {base}"

    _imap_note = '✅ IMAP-ключи есть' if _has_imap else 'ℹ️ IMAP не подключён (чтение входящих доступно коллеге с Gmail)'

    _check_emails_line = (
        f"  {_ti('check_emails')} — входящие [{_imap_note}]\n"
        if _has_imap else
        f"  ℹ️ check_emails — недоступен (нет IMAP/Gmail ключей). Делегируй коллеге с почтой.\n"
    )
    _script_note = '✅ python_code есть' if (_has_script or _has_rss or _has_github or _has_alpha or _has_news) else '⚠️ нужен python_code'

    # ── Секции каталога — собираем отдельно, порядок зависит от типа цели ──
    _sec_integrations = (
        "\n⚙️ Интеграции агента / run_agent_action:\n"
        + f"  {_ti('run_agent_action')} [{_script_note}] — вызывает API или Python-скрипт агента\n"
        + ("  Финансы/биржа: action='get_price' symbol='BRENT'|'WTI'|'GAZP.MCX'|'LKOH.MCX'\n"
           "                 action='get_news' — рыночные новости из Alpha Vantage\n"
           if _has_alpha else '')
        + ("  Новости:       action='get_news' query='oil market 2026' — NewsAPI 100+ источников\n"
           if _has_news and not _has_alpha else '')
        + ("  RSS:           action='[ТОЧНОЕ_RSS_ACTION_ИЗ_СПИСКА_АГЕНТА]' — свежие посты/сигналы из RSS-ленты агента\n"
              "                 ⚠️ Используй ТОЛЬКО точные action-имена из списка агента выше; НЕ выдумывай get_latest/search/read_rss_feed.\n"
           if _has_rss else '')
        + ("  GitHub:        action='search_users' query='language:python followers:>5' — поиск разработчиков\n"
           "                 ⚠️ QUERY: используй ТОЛЬКО GitHub-квалификаторы, НЕ свободный текст!\n"
           "                 ✅ Правильно: 'language:python followers:>5', 'language:javascript repos:>10 location:Russia'\n"
           "                 ❌ Неправильно: 'AI testing developers QA automation' → 0 результатов!\n"
           "                 Квалификаторы: language:X  followers:>N  repos:>N  location:X  type:user\n"
           "                action='find_contributors' repo='org/repo'\n"
           "                action='create_issue' title='...' body='...' — создать issue\n"
           "                action='list_issues' — список открытых issues\n"
           if _has_github else '')
        + ("  AmoCRM:        action='get_contacts' query='Иван' — поиск контакта в CRM\n"
           "                 action='get_pipelines' — воронки и status_id этапов (берёт ПЕРЕД create_lead!)\n"
           "                 action='create_lead' name='Сделка' price='1000' pipeline_id='...' status_id='...'\n"
           "                 action='update_lead' id='...' status_id='...' — продвинуть сделку по воронке\n"
           "                 action='create_contact' name='Имя' email='...' phone='...'\n"
           "                 action='link_contact' lead_id='...' contact_id='...'\n"
           "                 action='add_note' entity_type='contacts' id='...' text='...'\n"
           "                 action='get_leads' — последние 10 сделок\n"
           if _has_crm else '')
        + ("  Notion:        action='create_page' / 'update_page'\n" if _has_notion else '')
        + ("  Sheets:        action='update_sheet' / 'append_row'\n" if _has_sheets else '')
        + ("  Slack:         action='post_message' channel='#X'\n" if _has_slack else '')
        + ("  Stripe:        action='get_charges' / 'get_revenue'\n" if _has_stripe else '')
        + ("  (Нет API-ключей агента — run_agent_action недоступен)\n"
           if not any([_has_alpha, _has_news, _has_rss, _has_github, _has_notion,
                       _has_sheets, _has_slack, _has_stripe, _has_script]) else '')
    )
    _sec_research = (
        "\n🔍 Поиск и исследования (LLM + web):\n"
        "  ⚠️ Если у агента есть run_agent_action с нужным API (AmoCRM, GitHub, RSS) — используй ЕГО, а не web_search.\n"
        "     web_search = fallback, когда API нет или не хватает общей информации.\n"
        + _td('web_search') + '\n'
        + _td('research_topic') + '\n'
        + _td('get_news_trends') + '\n'
    )
    _sec_content = (
        "\n📢 Публикация результатов:\n"
        + _td('create_post') + '\n'
        + _td('publish_to_telegram') + '\n'
        + _td('publish_to_discord') + '\n'
        + _td('generate_image') + '\n'
    )
    _sec_email = (
        "\n📧 Email / Outreach:\n"
        + _td('send_outreach_email') + '\n'
        + _check_emails_line
        + _td('reply_to_outreach_email') + '\n'
        + _td('send_follow_up_email') + '\n'
        + _td('negotiate_by_email') + '\n'
        + _td('save_email_contact') + '\n'
        + _td('list_email_contacts') + '\n'
        + _td('find_relevant_contacts_for_task') + '\n'
        + _td('find_and_message_relevant_users') + '\n'
    )
    _sec_tasks = (
        "\n🎯 Задачи и цели:\n"
        + _td('update_goal_progress') + '\n'
        + _td('schedule_background_task') + '\n'
        + _td('set_reminder') + '\n'
    )
    _sec_delegate = (
        "\n🤝 Делегирование:\n"
        + _td('delegate_task') + '\n'
        + _td('start_delegation_campaign') + '\n'
        + _td('send_message_to_user') + '\n'
    )

    _dynamic_cap_rank = _rank_goal_capabilities(_goals_text_all, agent_caps or [], python_code=python_code)
    _dynamic_top_keys = [item[3] for item in _dynamic_cap_rank[:3]]

    # ── Каталог инструментов: reasoning-first, без жёстких per-type веток ──
    # ИИ видит все секции + один принцип выбора → сам решает с чего начать.
    # Правило: «что нужно ПРОИЗВЕСТИ из этой цели?»
    #   → данные/анализ       : начни с Интеграции + Поиск
    #   → охватить людей      : начни с Email/Outreach
    #   → видимость/аудитория : начни с Публикация
    #   → структура/план/трек : начни с Задачи
    # Единственный hard guardrail — личные/здоровье цели: без массовых рассылок.
    _catalog_guardrail = (
        "📌 [ЛИЧНАЯ/ЗДОРОВЬЕ/ОБУЧЕНИЕ цель] — массовые рассылки не помогут. "
        "Email только negotiate_by_email с конкретным ментором/специалистом по теме цели.\n"
        if _goal_type in ('learning', 'health', 'personal') else
        ("📧 Email не настроен — для распространения выводов используй публикацию или сохранение в базе знаний.\n"
         if _goal_type == 'research' and not _has_imap else "")
    )
    _catalog = (
        "ИНСТРУМЕНТЫ — выбирай по принципу «что нужно ПРОИЗВЕСТИ из этой цели?»:\n"
        "  → данные / анализ / мониторинг   : Интеграции + Поиск первыми\n"
        "  → охватить людей / контакты       : Email/Outreach первым\n"
        "  → видимость / аудитория / охват   : Публикация первой\n"
        "  → структура / план / трекинг      : Задачи первыми\n"
        "  → внешние API / реальные данные   : run_agent_action первым\n"
        "Все секции доступны — применяй нужные, игнорируй лишние.\n"
        + _catalog_guardrail
        + _sec_integrations
        + _sec_research
        + _sec_email
        + _sec_content
        + _sec_tasks
        + _sec_delegate
    )

    # ── Матрица умного выбора инструментов ──
    _tool_matrix = ''
    if _goal_type in ('learning', 'health', 'personal'):
        _tm_rows_p = [
            "  🔍 research_topic/web_search → исследуй тему цели: лучшие методики, ресурсы, чеклисты.",
            "  📝 save_note → ТОЛЬКО ценные выводы, уникальные данные. Не спамь заметками.",
            "  ✅ add_task → конкретные шаги для пользователя: 'Прочитать главу 3', 'Тренировка 30 мин'.",
            "  ⏰ set_reminder → регулярные напоминания для привычек и расписания.",
            "  📊 update_goal_progress → трекай прогресс: сколько сделано, что дальше.",
        ]
        if _has_content:
            _tm_rows_p.append(
                "  📢 create_post + publish → делись прогрессом/обзорами в канале пользователя."
            )
        _tm_rows_p.append(
            "  🔀 ЛУЧШИЕ КОМБО:\n"
            "    • Обучение: research_topic(тема) → save_note(конспект) → add_task(практика)\n"
            "    • Здоровье: web_search(программа) → save_note(план) → add_task(действие) → set_reminder\n"
            "    • Хобби: research_topic(техника) → save_note(заметки) → create_post(поделиться)\n"
            "    • Чтение: web_search(рекомендации) → save_note(список) → add_task(следующая книга)"
        )
        _tool_matrix = (
            "\n💡 КАК ВЫБРАТЬ ИНСТРУМЕНТ (для личной/обучающей цели):\n"
            + '\n'.join(_tm_rows_p)
            + "\n→ Главное: конкретный шаг вперёд к цели. Нет явного фаворита? Начни с research_topic.\n"
        )
    else:
        _tm_rows = [
            "  📧 Email-кампания → охватить 10+ человек, нужны прямые ответы/заявки/регистрации: "
            "find_relevant_contacts_for_task → save_email_contact → send_outreach_email.",

            (  "  📢 Контент-пост (publish_to_telegram / publish_to_discord) → узнаваемость, охват. "
               "⚠️ Только в ЛИЧНЫЙ КАНАЛ ПОЛЬЗОВАТЕЛЯ.\n  Комбо: пост → email заинтересовавшимся."
            ) if _has_content else (
               "  📢 Канал не настроен — create_post заготовит текст, send_message_to_user отправит пользователю."
            ),

            "  👥 find_and_message_relevant_users → быстрые первые пользователи/бета внутри платформы.",

            "  🔍 research_topic/web_search → лучший ПЕРВЫЙ ШАГ: найди ЦА, боли, конкурентов → пиши персонально.",
        ]
        if _has_github:
            _tm_rows.append(
                "  🐙 GitHub → разработчики по языку/стеку: search_users → save_email_contact → send_outreach_email."
            )
        _tm_rows.append(
            "  🔀 ЛУЧШИЕ КОМБО:\n"
            "    • Продвижение: research_topic(ЦА+боли) → email + Telegram-пост параллельно\n"
            