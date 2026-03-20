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
import time
import logging
import re
import traceback
from collections import defaultdict
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
from config import DEEPSEEK_API_KEY, PROACTIVE_NO_SEND_START_HOUR, PROACTIVE_SEND_START_HOUR

logger = logging.getLogger(__name__)


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
    return _t


# ═══════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════

# ── Лимиты доставок (единые, контроль расхода через токены) ──
# Токены — основной ограничитель. Лимиты — только anti-spam предохранитель.
MAX_DIALOG_PER_DAY = 14
MAX_FEED_PER_DAY = 1
MAX_CHANNEL_PER_DAY = 1
# CRITICAL/HIGH якоря НЕ считаются в лимите — доставляются всегда

NIGHT_START_HOUR = PROACTIVE_NO_SEND_START_HOUR  # Общая настройка: 22
MORNING_START_HOUR = PROACTIVE_SEND_START_HOUR   # Общая настройка: 10
SCAN_INTERVAL_MINUTES = 5
AUTOPILOT_DEEP_NIGHT_START = 0  # Ночная блокировка отключена (автопилот работает 24/7)
AUTOPILOT_DEEP_NIGHT_END = 0

# Минимальный интервал между ПРОАКТИВНЫМИ сообщениями (не блокирует CRITICAL)
MIN_PROACTIVE_GAP_MINUTES = 10
MIN_AUTOPILOT_GAP_MINUTES = 30  # Интервал между autopilot dispatch'ами

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
    'service_degraded',          # Сервис недоступен (веб-поиск, AI, email) — пользователь должен знать
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
}


# ── Таблица планов по интеграциям: (предикат, тип, заголовок, [пункты плана]) ──
_INTEGRATION_PLANS = [
    # Email / IMAP — агент с подключённым почтовым ящиком (Gmail/Яндекс/Mail.ru)
    (lambda c: any(w in c for w in ('mail', 'почт', 'email', 'imap', 'smtp', 'gmail', 'yandex', 'mailru')),
     'email',
     "Твой уникальный инструмент — чтение входящих (check_emails). Только ты можешь читать ответы на письма и реплаить. Отправлять через Resend могут все агенты и ASI — это обычный канал.",
     ["A) check_emails → есть ответ → АНАЛИЗИРУЙ НАМЕРЕНИЕ контакта → reply_to_outreach_email (только если позитивный/нейтральный!) → update_goal_progress",
      "B) start_email_campaign(name, goal, target_audience) → send_outreach_email → update_goal_progress",
      "C) list_email_contacts → send_outreach_email (если кампания уже есть) → update_goal_progress",
      "D) check_emails → нет новых ответов → send_follow_up_email (follow-up тем, кто не ответил за 2+ дня)",
      "E) find_relevant_contacts_for_task → save_email_contact → negotiate_by_email (персональное предложение)",
      "F) list_email_contacts (status=opened/interested) → negotiate_by_email → update_goal_progress",
      "G) web_search (форумы/сообщества по теме) → save_email_contact → send_outreach_email"]),
    # Outreach-письма (Resend) — агенты с send_outreach_email, но без IMAP
    (lambda c: any(w in c for w in ('outreach', 'письм')),
     'outreach',
     "Ты можешь отправлять outreach-письма через платформу (Resend). Чтение входящих (check_emails) — только для агентов с подключённым почтовым ящиком.",
     ["A) start_email_campaign(name=цель, goal=цель, target_audience=аудитория) → send_outreach_email → update_goal_progress",
      "B) find_relevant_contacts_for_task → add_email_leads → send_outreach_email → update_goal_progress",
      "C) web_search → save_email_contact → send_outreach_email (в активную кампанию)",
      "D) quick_topic_search (тематические сообщества/каналы) → save_email_contact → send_outreach_email",
      "E) find_and_message_relevant_users → send_outreach_email (пользователи из системы)",
      "F) negotiate_by_email (персональное письмо тёплым контактам) → update_goal_progress",
      "G) set_contact_alert (отслеживание активности контакта) → send_follow_up_email при срабатывании"]),
    # GitHub / GitLab
    (lambda c: any(w in c for w in ('github', 'gitlab')),
     'github',
     "Твоя интеграция GitHub — поиск людей и кода.",
     ["A) run_agent_action(action='search_users') → 3-5 контактов → save_email_contact",
      "B) run_agent_action(action='find_contributors') → email/ник → save_email_contact",
      "C) research_topic → GitHub-проекты → run_agent_action(поиск авторов)",
      "D) quick_topic_search (технические блоги по теме) → save_email_contact",
      "E) run_agent_action(action='search_repos') → авторы звёздных проектов → save_email_contact",
      "F) web_search 'site:github.com {тема} contributors' → save_email_contact",
      "G) find_relevant_contacts_for_task → run_agent_action(проверка профиля) → save_email_contact"]),
    # RSS лента / Feedparser
    (lambda c: any(w in c for w in ('rss', 'feed', 'лент')),
     'rss',
     "Твоя RSS-лента — источник авторов и трендов. Найди автора → передай контакт email-агенту.",
     ["A) run_agent_action(action='get_latest') → найди автора → save_email_contact",
      "B) run_agent_action(action='search', query=тема) → статьи → save_email_contact авторов",
      "C) research_topic → площадки/сообщества → add_task с конкретными контактами",
      "D) run_agent_action(action='get_latest') → トレンд-анализ → create_post (краткий обзор для Telegram)",
      "E) quick_topic_search (дополни ленту: форумы, Reddit, Habr) → save_email_contact новых авторов",
      "F) web_search (расширенный поиск по теме статьи) → save_email_contact экспертов",
      "G) run_agent_action(action='get_latest') → schedule_background_task (напомни через 24ч о не-ответивших)"]),
    # Slack
    (lambda c: 'slack' in c,
     'slack',
     "Твоя интеграция Slack — коммуникация с командой.",
     ["A) run_agent_action(action='post_message') → отчёт по прогрессу",
      "B) run_agent_action(action='list_channels') → найди релевантный → post_message",
      "C) research_topic → подготовь сообщение → run_agent_action(action='post_message')",
      "D) find_relevant_contacts_for_task → run_agent_action(action='invite_to_channel')",
      "E) web_search → актуальная новость → run_agent_action(action='post_message') как daily digest",
      "F) add_task (зафиксируй результаты обсуждения) → update_goal_progress"]),
    # Notion
    (lambda c: 'notion' in c,
     'notion',
     "Твоя интеграция Notion — база знаний и планы.",
     ["A) run_agent_action(action='create_page') → зафиксируй план/результат",
      "B) run_agent_action(action='update_page') → обнови прогресс по цели",
      "C) research_topic → run_agent_action(action='create_page') с найденными данными",
      "D) run_agent_action(action='query_database') → найди устаревшие записи → обнови",
      "E) web_search → дополни базу знаний новыми источниками → run_agent_action(action='create_page')",
      "F) list_tasks → run_agent_action(action='sync_tasks') → синхронизируй с Notion"]),
    # Trello / Jira / Asana / Todoist
    (lambda c: any(w in c for w in ('trello', 'jira', 'asana', 'todoist')),
     'pm',
     "Твой инструмент управления задачами — планируй и трекай.",
     ["A) run_agent_action(action='create_card') → зафиксируй следующий шаг",
      "B) run_agent_action(action='update_card') → обнови статус активных задач",
      "C) research_topic → создай карточку с конкретными данными",
      "D) run_agent_action(action='get_overdue') → найди просроченные → add_task для эскалации",
      "E) list_tasks → run_agent_action(action='sync') → синхронизируй платформы",
      "F) web_search (best practices по теме задачи) → run_agent_action(action='create_card') с чеклистом"]),
    # CRM (AmoCRM, Bitrix24)
    (lambda c: any(w in c for w in ('amocrm', 'битрикс', 'bitrix', 'crm')),
     'crm',
     "Твоя CRM — управление контактами и сделками.",
     ["A) run_agent_action(action='find_lead') → обнови статус → update_goal_progress",
      "B) find_relevant_contacts_for_task → run_agent_action(action='add_contact')",
      "C) research_topic → add_task с конкретными лидами",
      "D) run_agent_action(action='get_deals', stage='stalled') → negotiate_by_email / send_follow_up_email",
      "E) run_agent_action(action='get_activities') → выяви паттерны → web_search конкурентов-лидов",
      "F) set_contact_alert → run_agent_action(action='update_deal') при активности контакта"]),
    # E-commerce (Wildberries, Ozon, Shopify, Яндекс.Маркет)
    (lambda c: any(w in c for w in ('wildberries', 'ozon', 'shopify', 'маркетплейс')),
     'ecommerce',
     "Твой маркетплейс — мониторинг продаж, позиций, конкурентов.",
     ["A) run_agent_action(action='get_stats') → анализ → update_goal_progress",
      "B) run_agent_action(action='get_positions') → оптимизация → add_task",
      "C) research_topic → анализ конкурентов → add_task",
      "D) run_agent_action(action='get_reviews') → анализ отзывов → create_post (ответ/улучшение)",
      "E) web_search (конкуренты, тренды категории) → run_agent_action(action='update_price') + add_task",
      "F) quick_topic_search (SEO ключевые слова) → run_agent_action(action='update_description')"]),
    # Crypto / Binance / Bybit
    (lambda c: any(w in c for w in ('binance', 'bybit', 'coinbase', 'крипт', 'биржев')),
     'crypto',
     "Твои данные — крипто-рынок и биржа.",
     ["A) run_agent_action(action='get_price') → оцени тренд → update_goal_progress",
      "B) research_topic → анализ рынка → add_task с сигналами",
      "C) run_agent_action(action='get_portfolio') → сравнение → add_task",
      "D) quick_topic_search (крипто-новости) → run_agent_action(action='set_alert') при пороге",
      "E) web_search 'дефи тренды {монета}' → research_topic → add_task (стратегия)",
      "F) run_agent_action(action='get_history', period='7d') → выяви паттерн → create_post"]),
    # Google Sheets / Airtable / Данные
    (lambda c: any(w in c for w in ('sheets', 'google sheets', 'pandas', 'airtable', 'данных')),
     'data',
     "Твой инструмент — данные и таблицы.",
     ["A) run_agent_action(action='read_sheet') → анализ → update_goal_progress",
      "B) research_topic → run_agent_action(action='append_row') → зафиксируй",
      "C) run_agent_action(action='update_cell') → обнови метрики",
      "D) run_agent_action(action='read_sheet') → выяви аномалии → add_task (расследование)",
      "E) web_search (benchmarks по отрасли) → run_agent_action(action='append_row') для сравнения",
      "F) run_agent_action(action='get_chart') → create_post (визуальный отчёт) → publish_to_telegram"]),
    # Telegram-канал / Discord / Контент
    (lambda c: any(w in c for w in ('telegram', 'discord', 'smm', 'контент', 'публик')),
     'content',
     "Твои инструменты: создание и публикация контента.",
     ["A) research_topic → create_post (актуальный оффер) → publish_to_telegram",
      "B) quick_topic_search → create_post по тренду → publish_to_discord",
      "C) find_relevant_contacts_for_task → create_post нацеленный на аудиторию",
      "D) web_search (вирусные форматы по теме) → create_post (нестандартный формат) → publish_to_telegram",
      "E) generate_image (визуал) → create_post с картинкой → publish_to_telegram",
      "F) find_and_message_relevant_users (пригласи ЦА в канал) → update_goal_progress",
      "G) start_content_campaign (серийный контент на 7 дней) → update_goal_progress"]),
    # HH.ru / LinkedIn / HeadHunter (НР)
    (lambda c: any(w in c for w in ('headhunter', 'hh.ru', 'linkedin', 'рекрут')),
     'hr',
     "Твоя интеграция — поиск людей и вакансий.",
     ["A) run_agent_action(action='search_candidates') → оцени → save_email_contact",
      "B) research_topic → описание вакансии → add_task",
      "C) run_agent_action(action='get_responses') → оцени отклики → add_task",
      "D) web_search 'эксперт {область} телеграм OR github' → save_email_contact",
      "E) find_and_message_relevant_users → send_outreach_email (партнёрское приглашение)",
      "F) negotiate_by_email (персональный оффер кандидату) → set_contact_alert"]),
    # Avito
    (lambda c: 'авито' in c or 'avito' in c,
     'avito',
     "Твоя интеграция Авито — объявления, продажи, аренда.",
     ["A) run_agent_action(action='get_listings') → анализ → update_goal_progress",
      "B) run_agent_action(action='create_listing') → новое объявление",
      "C) research_topic → цены/конкуренты → add_task",
      "D) web_search (конкуренты на Авито) → run_agent_action(action='update_price')",
      "E) run_agent_action(action='get_messages') → ответь на входящие → update_goal_progress",
      "F) quick_topic_search (тренды спроса) → run_agent_action(action='create_listing') с новым описанием"]),
    # Stripe / ЮКасса / Платежи
    (lambda c: any(w in c for w in ('stripe', 'юкасс', 'платеж', 'payment')),
     'payments',
     "Твои данные — платежи и выручка.",
     ["A) run_agent_action(action='get_revenue') → анализ → update_goal_progress",
      "B) research_topic → стратегия роста выручки → add_task",
      "C) run_agent_action(action='list_transactions') → выяви паттерны → add_task",
      "D) run_agent_action(action='get_failed_payments') → выясни причины → negotiate_by_email",
      "E) web_search (retention-стратегии) → create_post (оффер для потерявших клиентов)",
      "F) run_agent_action(action='get_refunds') → анализ → add_task (снижение возвратов)"]),
]

# Тип интеграции → описание для матрицы делегирования
_INTEGRATION_TYPE_LABELS = {
    'email':     'чтение входящих / ответы (check_emails)',
    'outreach':  'отправка outreach-писем (send_outreach_email)',
    'github':    'поиск разработчиков/контрибьюторов',
    'rss':       'мониторинг ленты/поиск авторов',
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
                             has_imap: bool, has_github: bool, has_rss: bool,
                             has_alpha: bool, has_content: bool, has_news: bool,
                             has_notion: bool, has_slack: bool, has_sheets: bool,
                             has_stripe: bool) -> list[tuple[int, str, str]]:
    """Для конкретной цели возвращает список (score, emoji+name, цепочка инструментов)
    отсортированный по убыванию релевантности.  Только те интеграции, что реально есть у агента."""
    t = goal_title.lower()

    _SCORE: dict[str, int] = {}

    # ── GitHub: разработчики, тестировщики, пользователи, beta, participants ──
    _gh_kw_hi = ('разработчик', 'программист', 'developer', 'github', 'gitlab',
                 'тестировщик', 'пользовател', 'user', 'тестов', 'бета',
                 'beta', 'участник', 'кандидат', 'contributor', 'open source')
    _gh_kw_lo = ('контрибьют', 'репозитор', 'code', 'opensource', 'найти люд',
                 'набор', 'recruit', 'рекрутинг')
    if has_github:
        _SCORE['github'] = (sum(3 if w in t else 0 for w in _gh_kw_hi)
                            + sum(1 if w in t else 0 for w in _gh_kw_lo))

    # ── Email/IMAP: клиенты, партнёры, подписчики, рассылка ──
    _em_kw = ('клиент', 'партнёр', 'подписчик', 'рассылк', 'аудитор', 'покупател',
              'лид', 'lead', 'outreach', 'email', 'почт', 'сотрудник', 'заказчик')
    if has_imap:
        _SCORE['imap'] = sum(2 if w in t else 0 for w in _em_kw)

    # ── Alpha Vantage: финансы, нефть, рынок, биржа ──
    _av_kw = ('нефт', 'газ', 'акц', 'биржа', 'котировк', 'рынок нефт', 'oil', 'stock', 'forex',
              'инвест', 'трейд', 'финанс', 'commodity', 'сырьё', 'металл', 'валют')
    if has_alpha:
        _SCORE['alpha'] = sum(3 if w in t else 0 for w in _av_kw[:4]) + sum(1 if w in t else 0 for w in _av_kw[4:])

    # ── RSS: новости, мониторинг, тренды, статьи ──
    _rss_kw = ('новост', 'мониторинг', 'тренды', 'медиа', 'сми', 'статья', 'обзор', 'лент',
               'рss', 'фид', 'блог', 'публикац контент')
    if has_rss:
        _SCORE['rss'] = sum(2 if w in t else 0 for w in _rss_kw)

    # ── Telegram/Discord: аудитория, канал, контент ──
    _cnt_kw = ('канал', 'аудитория', 'подписчик', 'пост', 'smm', 'telegram', 'discord',
               'контент', 'охват', 'трафик', 'публикац')
    if has_content:
        _SCORE['content'] = sum(2 if w in t else 0 for w in _cnt_kw)

    # ── Notion: база знаний, документация ──
    _not_kw = ('база знаний', 'документ', 'notion', 'вики', 'wiki', 'заметк', 'запис')
    if has_notion:
        _SCORE['notion'] = sum(2 if w in t else 0 for w in _not_kw)

    # ── Slack: команда, коммуникация ──
    _slk_kw = ('команд', 'коммуникац', 'slack', 'уведомлен', 'отчёт команд')
    if has_slack:
        _SCORE['slack'] = sum(2 if w in t else 0 for w in _slk_kw)

    # ── Google Sheets: данные, таблицы, аналитика ──
    _gsh_kw = ('таблиц', 'данные', 'аналитик', 'sheets', 'excel', 'csv', 'метрик', 'отчёт')
    if has_sheets:
        _SCORE['sheets'] = sum(2 if w in t else 0 for w in _gsh_kw)

    # ── Stripe: платежи, выручка ──
    _str_kw = ('платёж', 'выручк', 'revenue', 'оплат', 'stripe', 'юкасса', 'транзакц')
    if has_stripe:
        _SCORE['stripe'] = sum(2 if w in t else 0 for w in _str_kw)

    _CHAINS = {
        'github': ("🐙 GitHub",
                   "run_agent_action(search_users, query='…') → save_email_contact → send_outreach_email"),
        'imap':   ("📧 Email",
                   "check_emails (ответы) → reply_to_outreach_email / negotiate_by_email"),
        'alpha':  ("📈 Alpha Vantage",
                   "run_agent_action(get_price, symbol='BRENT') → анализ → update_goal_progress"),
        'rss':    ("📰 RSS",
                   "run_agent_action(get_latest) → save_email_contact (автора) → send_outreach_email"),
        'content':("📢 Telegram/Discord",
                   "create_post → publish_to_telegram / publish_to_discord"),
        'notion': ("📝 Notion",
                   "run_agent_action(create_page) → зафиксируй данные/план"),
        'slack':  ("💬 Slack",
                   "run_agent_action(post_message, channel='#X') → уведомление команды"),
        'sheets': ("📊 Google Sheets",
                   "run_agent_action(update_sheet) → актуализируй данные/метрики"),
        'stripe': ("💳 Stripe",
                   "run_agent_action(get_charges) → анализ выручки → update_goal_progress"),
    }

    result = []
    for key, score in _SCORE.items():
        if score > 0 and key in _CHAINS:
            emoji_name, chain = _CHAINS[key]
            result.append((score, emoji_name, chain))
    result.sort(key=lambda x: -x[0])
    return result


# ── Тактические семейства инструментов ──
# Каждое семейство = одна концептуальная стратегия. Использовал инструмент → семейство отмечено.
_TACTIC_FAMILIES: dict = {
    'direct_action':      {'send_outreach_email', 'run_agent_action', 'find_relevant_contacts_for_task',
                           'save_email_contact', 'start_email_campaign', 'add_email_leads',
                           'find_and_message_relevant_users', 'create_post', 'publish_to_telegram',
                           'publish_to_discord', 'web_search', 'quick_topic_search'},
    'infrastructure':     {'research_and_plan', 'analyze_situation_and_suggest_tasks', 'add_task',
                           'delegate_task', 'start_delegation_campaign', 'schedule_background_task'},
    'relationship':       {'reply_to_outreach_email', 'negotiate_by_email', 'send_follow_up_email',
                           'check_emails', 'list_email_contacts', 'find_partners'},
    'content_attract':    {'generate_marketing_content', 'start_content_campaign', 'generate_image'},
    'research_discover':  {'research_topic', 'get_news_trends', 'find_relevant_contacts_for_task'},
}

# Универсальные паттерны мышления (работают для ЛЮБОЙ цели — бизнес, обучение, здоровье, личное)
_UNIVERSAL_PATTERNS: dict = {
    'direct_action': (
        '🎯 ПРЯМОЕ ДЕЙСТВИЕ',
        'Сделать то что НЕПОСРЕДСТВЕННО двигает цель прямо сейчас. '
        'Бизнес: send_outreach_email, run_agent_action, create_post. '
        'Обучение: web_search("лучший курс / how to [тема]") → add_task("пройти урок X"). '
        'Здоровье: add_task("тренировка в 7:00") → save_note("результат"). '
        'Личное: web_search("план достижения [цель]") → конкретный следующий шаг.',
    ),
    'infrastructure': (
        '🏗️ СОЗДАТЬ СИСТЕМУ (косвенный путь)',
        'Подготовить условия чтобы цель достигалась систематически. '
        'research_and_plan("Что подготовить для [цель]?") → add_task/delegate_task. '
        'Бизнес: landing page, FAQ, автоматизация. '
        'Обучение: расписание занятий, список ресурсов, план по неделям. '
        'Здоровье: режим дня, трекер привычки, план питания. '
        'Личное: roadmap, чек-лист, партнёр по ответственности.',
    ),
    'relationship': (
        '🔁 ПОДДЕРЖКА И СВЯЗИ',
        'Привлечь людей — партнёр по ответственности, ментор, эксперт, сообщество. '
        'Бизнес: check_emails → reply/negotiate/follow_up. '
        'Обучение: find_relevant_contacts_for_task("ментор [тема]") → написать напрямую. '
        'Личное/здоровье: найти единомышленников в сообществе по теме цели.',
    ),
    'content_attract': (
        '🧲 ЗАФИКСИРОВАТЬ И ПОДЕЛИТЬСЯ',
        'Создать контент / опубликовать прогресс. Двойная польза: мотивация + привлечение. '
        'generate_marketing_content → publish_to_telegram. '
        'Для личных целей: дневник прогресса, пост «учусь X / прошёл Y км» — '
        'создаёт публичное обязательство и находит единомышленников.',
    ),
    'research_discover': (
        '🔍 ПЕРЕОСМЫСЛИТЬ / НАЙТИ ЛУЧШИЙ ПУТЬ',
        'Если текущий подход не работает — найти лучший метод/ресурс/платформу/эксперта. '
        'research_topic("[тема] лучшие практики / методы / ресурсы"). '
        'Бизнес: новая аудитория, конференции, новый канал. '
        'Обучение: другой курс, книга, методика. '
        'Здоровье: другая программа тренировок, диетолог, программа. '
        'Личное: иной подход, сообщество, вдохновляющий пример.',
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
    
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return '\n'.join(lines) + '\n'


def _build_reasoning_scaffold(goals_summary: list, caps_lower: list[str],
                               has_imap: bool, has_github: bool, has_rss: bool,
                               has_alpha: bool, has_script: bool, has_content: bool,
                               has_news: bool, has_notion: bool, has_slack: bool,
                               has_sheets: bool, has_stripe: bool, used_tools: set,
                               goal_type: str = 'general',
                               agent_history: list | None = None) -> str:
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
        if mt:
            goal_lines.append(
                f"  • «{title}»: нужно {int(mt)}{(' ' + mu) if mu else ''}, подтверждено: {mc}"
            )
        else:
            goal_lines.append(f"  • «{title}» ({prog}%)")

    # ── Авто-приоритизация: лучшая интеграция под каждую цель ──
    _priority_lines = []
    _medals = ['🥇', '🥈', '🥉']
    for g in goals_summary[:3]:
        _gtitle = (g.get('title', '') or '')[:70]
        _ranked = _match_best_integration(
            _gtitle, has_imap, has_github, has_rss, has_alpha,
            has_content, has_news, has_notion, has_slack, has_sheets, has_stripe
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

    # ── Карта: что РЕАЛЬНО даёт каждая интеграция ──
    avail = []
    if has_imap:
        avail.append("  📧 Email: check_emails ← здесь реальные ответы живых людей; reply_to/negotiate — продолжение диалога")
    if has_github:
        avail.append("  🐙 GitHub: run_agent_action(action='search_users', query='language:python followers:>5', page=1) → save_email_contact → send_outreach_email ← найди + НАПИШИ в том же цикле (поиск без письма = 0 результатов)\n  ⚠️ QUERY правило: ТОЛЬКО GitHub-квалификаторы! Примеры: 'language:python followers:>5', 'language:javascript repos:>10 location:Russia'. НЕ 'AI testing QA automation' — свободный текст даёт 0 результатов!\n  🔄 ПАГИНАЦИЯ: если send_outreach_email вернул 'уже отправлено' для всех — используй page=2, page=3, на каждом цикле следующую страницу. Так обходишь до 300 пользователей за 30 циклов.")
    if has_rss:
        avail.append("  📰 RSS: run_agent_action(action='get_latest') ← свежие данные и инфоповоды из источника")
    if has_alpha:
        avail.append("  📈 Alpha Vantage: run_agent_action(action='get_price', symbol='XYZ') ← числовые рыночные данные")
    if has_content:
        avail.append("  📢 Telegram/Discord: publish_to_telegram / publish_to_discord ← реальная публикация в канал")
    if has_notion:
        avail.append("  📝 Notion: run_agent_action(action='create_page') ← структурное сохранение данных")
    if has_sheets:
        avail.append("  📊 Google Sheets: run_agent_action(action='update_sheet') ← данные в таблицу")
    if has_slack:
        avail.append("  💬 Slack: run_agent_action(action='post_message', channel='#X') ← команда получает уведомление")
    if has_stripe:
        avail.append("  💳 Stripe: run_agent_action(action='get_charges') ← реальные данные платежей")
    # Всегда доступно
    # Добавляем системные инструменты с учётом типа цели — не засориваем аналитические цели email-outreach
    _sys_always = [
        "  🔍 research_and_plan('[тема]') ← анализ + конкретный план из реальных данных",
        "  ↗️ delegate_task / DELEGATE[Имя] ← передать конкретному агенту с нужной интеграцией",
        "  🤝 start_delegation_campaign ← делегировать по специализации агентов",
    ]
    _sys_outreach_only = [
        "  🎯 find_relevant_contacts_for_task ← люди внутри платформы по навыкам/интересам",
        "  💬 find_and_message_relevant_users ← найти + написать за 1 шаг",
        "  📨 start_email_campaign + send_outreach_email ← персональный охват с follow-up",
        "  📅 КОНФЕРЕНЦИИ: research_topic('конференции [тема] 2026 Россия') → find_relevant_contacts_for_task → send_outreach_email",
        "  🏘️ СООБЩЕСТВА: research_topic('Telegram Discord каналы [тема]') → find_and_message_relevant_users",
    ]
    # Инструменты для личных / обучающих / здоровье-целей
    _sys_personal = [
        "  📚 research_topic('[что изучить / как достичь / best practices по теме]') ← методика, ресурсы, план",
        "  🔍 web_search('[тема] how to achieve / step by step / best program') ← конкретные шаги",
        "  📋 add_task(title='[конкретное действие]', due_date='[дата]') ← зафиксируй следующий шаг",
        "  📝 save_note('[прогресс/инсайт/результат занятия]') ← фиксируй промежуточный результат",
        "  📢 publish_to_telegram(content='...') ← поделись прогрессом — создаёт публичное обязательство",
        "  🤝 find_relevant_contacts_for_task('[ментор/эксперт по теме]') ← найди поддержку/сообщество",
    ]
    avail.extend(_sys_always)
    if goal_type in ('personal', 'learning', 'health'):
        avail.extend(_sys_personal)
    elif goal_type not in ('research', 'dev'):
        avail.extend(_sys_outreach_only)

    # Адаптивные примеры «что РЕАЛЬНО = +1» под тип цели
    _progress_hint = {
        'outreach': (
            "«Письмо отправлено» ≠ прогресс.\n"
            "     «Человек ответил с интересом» = потенциал.\n"
            "     «Пользователь зарегистрировался / совершил целевое действие» = прогресс."
        ),
        'learning': (
            "«Нашёл курс» ≠ прогресс.\n"
            "     «Выполнен урок / практическое задание» = шаг.\n"
            "     «Знание применено на практике / результат зафиксирован» = реальный прогресс."
        ),
        'health': (
            "«Запланировал тренировку» ≠ прогресс.\n"
            "     «Тренировка выполнена / км пробежаны» = прогресс.\n"
            "     «Числовой результат зафиксирован (вес, время, дистанция)» = реальный шаг."
        ),
        'personal': (
            "«Подумал об этом» ≠ прогресс.\n"
            "     «Конкретное действие выполнено» = шаг.\n"
            "     «Ощутимый результат / привычка выполнена» = реальный прогресс."
        ),
        'content': (
            "«Написал черновик» ≠ прогресс.\n"
            "     «Контент опубликован» = шаг.\n"
            "     «Аудитория отреагировала / подписалась» = реальный прогресс."
        ),
        'research': (
            "«Инструмент вызван» ≠ прогресс.\n"
            "     «Получены и обработаны реальные данные (цифры, факты, инсайт)» = прогресс.\n"
            "     «Пользователь получил полезный вывод» = реальный результат."
        ),
        'dev': (
            "«Код написан» ≠ прогресс.\n"
            "     «Функция работает, тест прошёл» = шаг.\n"
            "     «Задача закрыта, результат доставлен пользователю» = реальный прогресс."
        ),
    }.get(goal_type,
        "«Инструмент вызван» ≠ прогресс.\n"
        "     «Получен конкретный результат» = шаг.\n"
        "     «Метрика цели сдвинулась на реальную единицу» = прогресс."
    )

    return (
        "\n━━━ ДУМАЙ О ЦЕЛИ, А НЕ ОБ АКТИВНОСТИ ━━━\n"
        "Цели и что реально в них засчитывается:\n"
        + '\n'.join(goal_lines)
        + _priority_block
        + "\n\n"
        "⚠️ ВАЖНО — перед каждым действием ответь мысленно:\n"
        "  1) Что РЕАЛЬНО = +1 к метрике этой цели?\n"
        f"     {_progress_hint}\n"
        "  2) Из доступных инструментов — какой даёт ПРЯМОЙ результат, а НЕ промежуточный шаг?\n"
        "  3) Что коллеги по команде уже делают? Что я могу сделать сам и что делегировать?\n\n"
        "📌 ПРАВИЛО update_goal_progress:\n"
        "  ✅ Вызывай только при ПОДТВЕРЖДЁННОМ результате:\n"
        "     — реальное действие выполнено (тренировка, урок, задача, регистрация)\n"
        "     — получена и ОБРАБОТАНА реальная информация\n"
        "  ❌ НЕ вызывай когда: инструмент вызван / план составлен / поиск выполнен\n"
        "  ❌ Не ставь прогресс на основе активности — только по реальным результатам\n"
        "  📐 metric_current = количество РЕАЛЬНО достигнутых единиц цели\n\n"
        "🔧 МОИ РЕАЛЬНЫЕ ВОЗМОЖНОСТИ (выбирай то, что подходит к ситуации):\n"
        + '\n'.join(avail)
        + "\n└ Нет шаблона — есть цель и логика. Используй то что приближает к РЕАЛЬНОМУ результату.\n"
    ) + _build_tactic_wheel(goal_type, used_tools, agent_history or [])



def _build_autopilot_prompt(goals_summary: list, user=None, agent_caps=None, agent_name=None, team_profiles=None, agent_history=None) -> str:
    """Строит адаптивный промпт автопилота.
    Вместо жёстких A/B/C планов — показывает полный каталог инструментов платформы
    и предоставляет AI свободу выбора лучшей цепочки под цель и интеграции агента.
    """
    import re as _re_ap

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

    # ── Краткое описание целей ──
    _goals_desc = '; '.join(
        f"{g.get('title', '?')} ({g.get('progress', 0)}%"
        + (f", {g.get('metric_current', 0)}/{g.get('metric_target', '?')}" if g.get('metric_target') else '')
        + ")"
        for g in goals_summary[:5]
    )

    _caps_lower = [c.lower() for c in (agent_caps or [])]
    _caps_str = ', '.join(agent_caps or [])

    # ── Детектируем реальные интеграции агента ──
    _has_imap = any(w in c for c in _caps_lower for w in ('imap', 'gmail', 'почт', 'mail', 'smtp', 'yandex', 'mailru'))
    _has_script = any(w in c for c in _caps_lower for w in ('python', 'http', 'скрипт', 'run_agent', 'все инструменты'))
    _has_rss    = any(w in c for c in _caps_lower for w in ('rss', 'feed', 'лент'))
    _has_github = any(w in c for c in _caps_lower for w in ('github', 'gitlab'))
    _has_content = any(w in c for c in _caps_lower for w in ('telegram', 'discord', 'контент', 'smm'))
    _has_alpha  = any(w in c for c in _caps_lower for w in ('alpha_vantage', 'alphavantage', 'alpha vantage', 'котировк', 'биржа', 'биржевые'))
    _has_news   = any(w in c for c in _caps_lower for w in ('newsapi', 'news api', 'news_api'))
    _has_notion = any(w in c for c in _caps_lower for w in ('notion',))
    _has_slack  = any(w in c for c in _caps_lower for w in ('slack',))
    _has_sheets = any(w in c for c in _caps_lower for w in ('google sheets', 'gsheets', 'spreadsheet'))
    _has_stripe = any(w in c for c in _caps_lower for w in ('stripe', 'юкасс', 'yookassa', 'платеж'))

    # ── Блок: что подключено у агента, что доступно для целей ──
    _goals_text_all = ' '.join(
        g.get('title', '') + ' ' + (g.get('description', '') or '') for g in goals_summary
    ).lower()

    # ── Тип цели: research / outreach / content / dev / learning / health / personal / general ──
    # Определяем чтобы показывать ТОЛЬКО релевантные инструменты, не засорять промпт
    _RESEARCH_KW = ('анализ', 'исследован', 'мониторинг', 'обзор', 'рынок', 'нефт', 'газ',
                    'биржа', 'котировк', 'тренды', 'данные', 'аналитик', 'прогноз',
                    'статистик', 'oil', 'stock', 'commodity', 'forex', 'сырьё', 'металл', 'отчёт')
    _OUTREACH_KW = ('найти клиент', 'привлеч', 'подписчик', 'пользовател',
                    'набор', 'аудитор', 'лид', 'lead', 'beta', 'бета', 'тестировщик', 'рекрутинг',
                    'продаж', 'b2b', 'партнёр', 'сделк', 'клиентск',
                    'вакансия', 'кандидат', 'найм', 'нанять', 'hr', 'стаж',
                    'инвест', 'инвестор', 'финансиров', 'раунд', 'фандрейзинг',
                    'участник', 'комьюнити', 'member', 'contributor', 'беты', 'регистрац')
    _CONTENT_KW  = ('контент', 'smm', 'reels', 'видео', 'медиаплан')
    _DEV_KW      = ('разработ', 'программ', 'github', 'backend', 'frontend', 'developer', 'деплой')
    _LEARNING_KW = ('изучить', 'научиться', 'курс', 'обучен', 'практик', 'навык', 'книг', 'читать',
                    'сертификат', 'диплом', 'урок', 'освоить', 'skill', 'учёб', 'прочитать',
                    'лекц', 'workshop', 'тренинг', 'язык программирован', 'английск', 'язык')
    _HEALTH_KW   = ('спорт', 'тренировк', 'похудеть', 'похуд', 'здоровь', 'пробежать', 'бег',
                    'марафон', 'питание', 'диета', 'сон', 'медитац', ' кг', 'килограмм',
                    'workout', 'fitness', 'фитнес', 'km', 'км пробег', 'пресс', 'бросить курить',
                    'калори', 'вес тела', 'зарядк', 'йога', 'плавани')
    _PERSONAL_KW = ('путешеств', 'поездк', 'привычк', 'ежедневн', 'streak', 'хобби', 'творч',
                    'музыка', 'рисован', 'дневник', 'саморазвит', 'мечт', 'написать книг',
                    'личный проект', 'личная цель', 'жизнь', 'счастье', 'отдых', 'баланс')
    _gtype_scores = {
        'research': sum(1 for w in _RESEARCH_KW if w in _goals_text_all),
        'outreach': sum(1 for w in _OUTREACH_KW if w in _goals_text_all),
        'content':  sum(1 for w in _CONTENT_KW  if w in _goals_text_all),
        'dev':      sum(1 for w in _DEV_KW      if w in _goals_text_all),
        'learning': sum(1 for w in _LEARNING_KW if w in _goals_text_all),
        'health':   sum(1 for w in _HEALTH_KW   if w in _goals_text_all),
        'personal': sum(1 for w in _PERSONAL_KW if w in _goals_text_all),
    }
    _best_gtype = max(_gtype_scores.items(), key=lambda x: x[1])
    _goal_type = _best_gtype[0] if _best_gtype[1] > 0 else 'general'

    _intg_connected = []
    _intg_missing = []

    if _has_imap:    _intg_connected.append('✅ Email (IMAP/Gmail/Яндекс) — читать входящие, отвечать')
    if _has_github:  _intg_connected.append('✅ GitHub — run_agent_action(action="search_users", query="language:python followers:>5", page=1) → save_email_contact → send_outreach_email\n  ⚠️ QUERY: только GitHub-квалификаторы (language: followers: repos: location:), НЕ свободный текст — иначе 0 результатов!\n  🔄 ПАГИНАЦИЯ: если все найденные уже contacted ("уже отправлено") — ищи дальше: page=2, page=3 и т.д. На каждом цикле используй следующую страницу.')
    if _has_rss:     _intg_connected.append('✅ RSS — мониторинг лент новостей')
    if _has_alpha:   _intg_connected.append('✅ Alpha Vantage — котировки акций/нефти/металлов')
    if _has_news:    _intg_connected.append('✅ NewsAPI — агрегатор новостей (100+ источников)')
    if _has_notion:  _intg_connected.append('✅ Notion — записи, базы знаний')
    if _has_slack:   _intg_connected.append('✅ Slack — коммуникация с командой')
    if _has_sheets:  _intg_connected.append('✅ Google Sheets — таблицы, аналитика')
    if _has_stripe:  _intg_connected.append('✅ Stripe/ЮКасса — платёжные данные')
    if _has_content: _intg_connected.append('✅ Telegram/Discord — публикация контента')
    if _has_script:  _intg_connected.append('✅ Python / HTTP — кастомные скрипты через run_agent_action')

    # Рекомендации: смотрим на темы целей и чего нет у агента
    import os as _os_bap
    _fin_kw = ('нефт', 'газ', 'рынок', 'биржа', 'акции', 'финанс', 'трейд', 'инвест', 'криптo', 'oil', 'stock', 'forex', 'валют')
    _dev_kw = ('разработ', 'программ', 'github', 'code', 'репозитор', 'деплой')
    _news_kw = ('новост', 'мониторинг', 'тренды', 'медиа', 'сми', 'пресс', 'обзор рынка')
    _ppl_kw  = ('пользовател', 'тестировщик', 'клиент', 'подписчик', 'аудитор', 'рекрутинг')
    _cnt_kw  = ('контент', 'smm', 'публикац', 'канал', 'посты')

    if any(w in _goals_text_all for w in _fin_kw):
        if not _has_alpha:
            _intg_missing.append('⚡ Alpha Vantage — котировки нефти/акций/металлов (ALPHAVANTAGE_API_KEY в настройках агента)')
        if not _has_news and not _os_bap.getenv('NEWSAPI_KEY'):
            _intg_missing.append('⚡ NewsAPI — поток финансовых новостей (NEWSAPI_KEY в настройках агента)')
    if any(w in _goals_text_all for w in _news_kw):
        if not _has_news and not _os_bap.getenv('NEWSAPI_KEY'):
            _intg_missing.append('⚡ NewsAPI — 100+ источников новостей (NEWSAPI_KEY в настройках агента)')
        if not _has_rss:
            _intg_missing.append('⚡ RSS — добавь RSS_URL= в API-ключи агента для мониторинга лент')
    if any(w in _goals_text_all for w in _dev_kw):
        if not _has_github and not _os_bap.getenv('GITHUB_TOKEN'):
            _intg_missing.append('⚡ GitHub Token — поиск разработчиков/контрибьюторов (GITHUB_TOKEN в настройках агента)')
    if any(w in _goals_text_all for w in _ppl_kw):
        if not _has_imap:
            _intg_missing.append('⚡ Email — добавь GMAIL_USER + пароль приложения в настройки агента для охвата')
    if any(w in _goals_text_all for w in _cnt_kw):
        if not _has_content:
            _intg_missing.append('⚡ Telegram Bot Token — публикация постов в канал (TELEGRAM_BOT_TOKEN в настройках агента)')

    _intg_block = ''
    if _intg_connected or _intg_missing:
        _intg_block = '\nИНТЕГРАЦИИ АГЕНТА:\n'
        if _intg_connected:
            _intg_block += '\n'.join(f'  {x}' for x in _intg_connected) + '\n'
        if _intg_missing:
            _intg_block += 'ДОСТУПНО ДЛЯ ПОДКЛЮЧЕНИЯ (под текущие цели):\n'
            _intg_block += '\n'.join(f'  {x}' for x in _intg_missing) + '\n'
        # Goal-type-aware первый шаг
        if _goal_type == 'research' and _has_alpha:
            # Определяем символ на основе ключевых слов цели
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
                '⚡ ПЕРВЫЙ ШАГ: run_agent_action(action="get_latest") '
                '— получи данные из RSS-ленты агента, затем извлеки суть.\n'
            )
        else:
            _intg_block += '→ Используй подключённые интеграции в ПЕРВУЮ очередь — они дают реальные данные.\n'
        _intg_block += (
            '→ Если инструмент вернул "не настроен / нет токена / нет ключа / ошибка авторизации" — '
            'ОБЯЗАТЕЛЬНО сообщи пользователю: (1) что именно не смог сделать, '
            '(2) какую интеграцию нужно подключить, (3) как это поможет цели.\n'
            '  Пример: "Не смог получить котировки — нет Alpha Vantage ключа. '
            'Если подключишь ALPHAVANTAGE_API_KEY, буду присылать актуальные данные по нефти каждый день."\n'
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
        'find_relevant_contacts_for_task', 'update_goal_progress', 'add_task',
        # web_search специально удалён — должен баниться после 2 раз чтобы не зацикливаться
    }
    # GitHub-агенты: run_agent_action не банить — каждый query уникален (search_users, find_contributors...)
    if _has_github:
        _MULTI_USE_OK.add('run_agent_action')
    _banned = {t for t, n in _tool_cnt.items() if n >= 2 and t not in _MULTI_USE_OK}
    _warn   = {t for t, n in _tool_cnt.items() if n == 1}

    # Детектор петли: последние 3 инструментa одинаковые → форсируем analyse
    _force_analyse = False
    if len(_last_tools) >= 3 and len(set(_last_tools[-3:])) == 1:
        _force_analyse = True
    # Детектор типичных унылых петель:
    # Расширенный набор «только поиск» — включает LLM-исследование и анализ
    _SEARCH_ONLY = {
        'web_search', 'quick_topic_search', 'research_topic', 'get_news_trends',
        'research_and_plan', 'analyze_situation_and_suggest_tasks',
    }
    _SAVE_ONLY   = {'save_email_contact', 'add_email_leads', 'add_task'}
    _PROGRESS_ONLY = {'update_goal_progress'}
    _last4 = _last_tools[-4:]
    _last5 = _last_tools[-5:]
    # Детектор зацикливания: 2 инструмента чередуются (типичный ping-pong)
    _last6 = _last_tools[-6:]
    _is_pingpong = (
        len(_last6) >= 4
        and len(set(_last6)) <= 2
        and len(_last6) >= 4
    )
    _is_trivial_loop = (
        # Ping-pong между двумя инструментами (web_search ↔ update_goal_progress и т.д.)
        _is_pingpong
        # Только поиск + обновление прогресса
        or (len(_last4) >= 4 and all(t in _SEARCH_ONLY | _PROGRESS_ONLY for t in _last4))
        # Поиск → сохранить: цикл без отправки
        or (len(_last4) >= 4 and all(t in _SEARCH_ONLY | _SAVE_ONLY for t in _last4)
            and not any(t in ('send_outreach_email', 'negotiate_by_email', 'find_and_message_relevant_users')
                        for t in _last_tools))
        # Только сохранение контактов без отправки (частая получасть collecting-агенте)
        or (len(_last5) >= 5 and all(t in _SAVE_ONLY for t in _last5))
        # Только поиск (без email и без делегирования) — 4+ циклов
        or (len(_last4) >= 4 and all(t in _SEARCH_ONLY for t in _last4))
    )
    if _is_trivial_loop:
        _force_analyse = True

    # Инструменты которые агент ЕЩЁ НЕ пробовал — сортированные по полезности
    _ALL_ACTION_TOOLS = [
        'send_outreach_email', 'negotiate_by_email', 'find_and_message_relevant_users',
        'start_email_campaign', 'start_content_campaign', 'generate_marketing_content',
        'create_post', 'publish_to_telegram', 'find_partners', 'start_delegation_campaign',
        'find_relevant_contacts_for_task', 'research_and_plan', 'analyze_situation_and_suggest_tasks',
        'send_follow_up_email', 'schedule_background_task', 'set_contact_alert',
        'research_topic', 'quick_topic_search', 'get_news_trends', 'web_search',
        'generate_image', 'publish_to_discord', 'list_email_contacts',
        'add_task', 'save_email_contact', 'update_goal_progress',
    ]
    _used_tools = set(_tool_cnt.keys())
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
        _has_search_done = any(t in ('web_search', 'research_topic', 'quick_topic_search') for t in _used_tools)
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

        if _zero_progress and not _has_outreach_done and _has_search_done:
            _goal_state_hint += (
                "\n🚨 ПРОГРЕСС 0%: поиск сделан, но не было ни одного контакта/письма. "
                "Следующий шаг — выход на людей: "
                "find_relevant_contacts_for_task → send_outreach_email / find_and_message_relevant_users.\n"
            )
        elif _stuck_goals and agent_history and len(agent_history) >= 4 and not _has_outreach_done:
            _goal_state_hint += (
                "\n⚠️ НЕТ КОНТАКТОВ: ты уже ищешь/исследуешь, но нет ни одного письма/сообщения. "
                "Пора действовать: find_relevant_contacts_for_task или find_and_message_relevant_users.\n"
            )
        elif _has_find_contacts and _has_outreach_done and not _metric_hints:
            # Агент искал и отправлял, но метрика не отображается — значит прогресс есть
            _goal_state_hint += (
                "\n✅ ПРОГРЕСС ИДЁТ: контакты сохранены, письма отправлены. "
                "Продолжай outreach и проверяй ответы через check_emails.\n"
            )

    def _ti(name: str) -> str:
        """Помечает заблокированный инструмент."""
        return f"[БАН:{name}]" if name in _banned else name

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
        desc = _tool_descs.get(name, '')
        base = f"{_ti(name)}" + (f" — {desc}" if desc else '')
        return f"  {base}"

    _imap_note = '✅ IMAP-ключи есть' if _has_imap else '⛔ IMAP нет (читать входящие может только коллега с Gmail)'

    _check_emails_line = (
        f"  {_ti('check_emails')} — входящие [{_imap_note}]\n"
        if _has_imap else
        f"  ⛔ check_emails — НЕДОСТУПНО (нет IMAP/Gmail ключей у этого агента). НЕ ВЫЗЫВАТЬ.\n"
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
        + ("  RSS:           action='get_latest' — свежие посты из RSS-ленты агента\n"
           if _has_rss else '')
        + ("  GitHub:        action='search_users' query='language:python followers:>5' — поиск разработчиков\n"
           "                 ⚠️ QUERY: используй ТОЛЬКО GitHub-квалификаторы, НЕ свободный текст!\n"
           "                 ✅ Правильно: 'language:python followers:>5', 'language:javascript repos:>10 location:Russia'\n"
           "                 ❌ Неправильно: 'AI testing developers QA automation' → 0 результатов!\n"
           "                 Квалификаторы: language:X  followers:>N  repos:>N  location:X  type:user\n"
           "                action='find_contributors' repo='org/repo'\n"
           if _has_github else '')
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
        + _td('web_search') + '\n'
        + _td('research_topic') + '\n'
        + _td('quick_topic_search') + '\n'
        + _td('get_news_trends') + '\n'
        + _td('analyze_situation_and_suggest_tasks') + '\n'
        + _td('research_and_plan') + '\n'
    )
    _sec_content = (
        "\n📢 Публикация результатов:\n"
        + _td('create_post') + '\n'
        + _td('publish_to_telegram') + '\n'
        + _td('publish_to_discord') + '\n'
        + _td('generate_image') + '\n'
        + _td('generate_marketing_content') + '\n'
        + _td('start_content_campaign') + '\n'
    )
    _sec_email = (
        "\n📧 Email / Outreach:\n"
        + _td('send_outreach_email') + '\n'
        + _td('start_email_campaign') + '\n'
        + _check_emails_line
        + _td('reply_to_outreach_email') + '\n'
        + _td('send_follow_up_email') + '\n'
        + _td('negotiate_by_email') + '\n'
        + _td('save_email_contact') + '\n'
        + _td('list_email_contacts') + '\n'
        + _td('find_relevant_contacts_for_task') + '\n'
        + _td('find_partners') + '\n'
        + _td('find_and_message_relevant_users') + '\n'
    )
    _sec_tasks = (
        "\n🎯 Задачи и цели:\n"
        + _td('add_task') + '\n'
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

    # ── Порядок секций зависит от типа цели ──
    if _goal_type == 'research':
        # Research/analytics: сначала реальные данные (интеграции), потом LLM-поиск,
        # потом публикация итогов. Email-outreach — только если IMAP есть.
        _catalog = (
            "ИНСТРУМЕНТЫ (порядок = приоритет для АНАЛИТИЧЕСКОЙ цели):\n"
            + _sec_integrations
            + _sec_research
            + _sec_content
            + _sec_tasks
            + _sec_delegate
            + (_sec_email if _has_imap else
               "\n📧 Email не настроен — используй Telegram/Discord для публикации аналитики.\n")
        )
    elif _goal_type == 'dev':
        _catalog = (
            "ИНСТРУМЕНТЫ (порядок = приоритет для задачи с разработчиками):\n"
            + _sec_integrations
            + _sec_research
            + _sec_email
            + _sec_tasks
            + _sec_delegate
            + _sec_content
        )
    elif _goal_type == 'content':
        _catalog = (
            "ИНСТРУМЕНТЫ (порядок = приоритет для контентной цели):\n"
            + _sec_content
            + _sec_integrations
            + _sec_research
            + _sec_tasks
            + _sec_delegate
            + _sec_email
        )
    else:
        # outreach / general — стандарт: email первым
        _catalog = (
            "ИНСТРУМЕНТЫ (выбери лучшую цепочку под цель):\n"
            + _sec_email
            + _sec_research
            + _sec_integrations
            + _sec_content
            + _sec_delegate
            + _sec_tasks
        )

    # ── Матрица делегирования команды ──
    _team_block = ''
    if team_profiles:
        _delegate_lines = []
        for tp in team_profiles:
            if tp.get('name') == agent_name:
                continue
            _tp_caps = tp.get('capabilities', [])[:5]
            if _tp_caps:
                _delegate_lines.append(
                    f"  DELEGATE[{tp['name']}]"
                    + (f" ({tp.get('job_title', '')})" if tp.get('job_title') else '')
                    + f": {', '.join(_tp_caps)}"
                )
        if _delegate_lines:
            _team_block = (
                "\nКОМАНДА (делегируй если нужна их специализация):\n"
                + '\n'.join(_delegate_lines)
                + "\nФормат: DELEGATE[Имя]: конкретная задача с данными\n"
            )

    # ── Блок памяти ──
    _memory_block = ''
    if agent_history:
        _mem_lines = [f"  {i+1}. {h}" for i, h in enumerate(agent_history[:6])]
        _memory_block = (
            "\nТВОЯ ИСТОРИЯ (последние действия — НЕ ПОВТОРЯЙ без нового результата):\n"
            + '\n'.join(_mem_lines) + '\n'
        )
        if _force_analyse:
            # Предлагаем конкретные альтернативы с учётом типа цели
            _ALTS_RESEARCH = [
                'run_agent_action', 'research_and_plan', 'analyze_situation_and_suggest_tasks',
                'get_news_trends', 'create_post', 'publish_to_telegram', 'schedule_background_task',
            ]
            _ALTS_OUTREACH = [
                'find_and_message_relevant_users', 'send_outreach_email', 'negotiate_by_email',
                'start_content_campaign', 'generate_marketing_content', 'find_partners',
                'start_delegation_campaign', 'research_and_plan', 'analyze_situation_and_suggest_tasks',
                'get_news_trends', 'find_relevant_contacts_for_task',
            ]
            _alts_pool = _ALTS_RESEARCH if _goal_type in ('research', 'dev') else _ALTS_OUTREACH
            _alts = [t for t in _alts_pool if t not in _used_tools][:5]
            _alts_str = ', '.join(_alts) if _alts else 'research_and_plan, analyze_situation_and_suggest_tasks'
            _memory_block += (
                "🔴 ПЕТЛЯ ОБНАРУЖЕНА: ты повторяешь одни и те же инструменты без прогресса!\n"
                f"→ НЕМЕДЛЕННО примени ЛЮБОЙ из этих (ты ещё не пробовал!): {_alts_str}\n"
                "Правило при застревании: роль вторична, важен результат — выеди за рамки специализации если надо.\n"
            )
        elif _banned:
            _memory_block += (
                f"🚫 ЗАБЛОКИРОВАНО (2+ раз без прогресса): {', '.join(sorted(_banned))}\n"
                "→ Выбери ЛЮБОЙ другой инструмент из каталога выше.\n"
            )
        if _warn and not _force_analyse:
            _memory_block += f"⚠️ Использовано по 1 разу — лучше попробовать новое: {', '.join(sorted(_warn))}\n"
        _memory_block += _untried_block

    # ── Имена целей для привязки задач ──
    _first_goal_title = goals_summary[0].get('title', '') if goals_summary else ''

    # ── Фрейм рассуждения — универсальный, без keyword-сценариев ──
    # Передаём историю агента для тактического трекинга (что уже пробовалось)
    _agent_hist_for_scaffold = agent_history or []
    _tactics_block = _build_reasoning_scaffold(
        goals_summary, _caps_lower,
        _has_imap, _has_github, _has_rss,
        _has_alpha, _has_script, _has_content,
        _has_news, _has_notion, _has_slack,
        _has_sheets, _has_stripe, _used_tools,
        goal_type=_goal_type,
        agent_history=_agent_hist_for_scaffold,
    )

    # ── Outreach effectiveness stats ──
    _outreach_stats = ''
    if user and (_has_imap or _has_github):
        try:
            from models import Session as _SOR, EmailOutreach as _EO_stat
            from sqlalchemy import func as _func_stat
            _db_stat = _SOR()
            try:
                _total_sent = _db_stat.query(_func_stat.count(_EO_stat.id)).filter(
                    _EO_stat.user_id == user.id).scalar() or 0
                _total_replied = _db_stat.query(_func_stat.count(_EO_stat.id)).filter(
                    _EO_stat.user_id == user.id,
                    _EO_stat.status == 'replied').scalar() or 0
                if _total_sent >= 3:
                    _rate = round(_total_replied / _total_sent * 100)
                    _outreach_stats = (
                        f"\n📬 СТАТИСТИКА АУТРИЧА: отправлено {_total_sent}, ответили {_total_replied} "
                        f"(конверсия {_rate}%). "
                    )
                    if _rate < 5:
                        _outreach_stats += "Конверсия низкая → меняй подход: персонализируй письма, ищи другую аудиторию.\n"
                    elif _rate > 20:
                        _outreach_stats += "Конверсия отличная → продолжай этот подход!\n"
                    else:
                        _outreach_stats += "Конверсия средняя → попробуй A/B: измени тему или целевую аудиторию.\n"
            finally:
                _db_stat.close()
        except Exception:
            pass

    return (
        f"ЦЕЛИ: {_goals_desc}\n"
        f"{'Твои интеграции/специализация: ' + _caps_str + chr(10) if _caps_str else ''}"
        f"{channels_hint}"
        f"{_intg_block}"
        f"{_goal_state_hint}"
        f"{_outreach_stats}"
        f"{_tactics_block}"
        f"\n{_catalog}"
        f"{_team_block}"
        f"{_memory_block}\n"
        "## УНИВЕРСАЛЬНЫЙ ФРЕЙМВОРК МЫШЛЕНИЯ (5 вопросов для ЛЮБОЙ цели — думай молча)\n\n"
        "1️⃣ ЧТО = РЕЗУЛЬТАТ?\n"
        "   • Что конкретно изменится когда цель достигнута?\n"
        "   • Что = +1 к метрике? (не 'письмо отправлено', а 'человек ответил/зарегистрировался')\n"
        "   • История: что уже пробовалось? Что сработало/не сработало?\n\n"
        "2️⃣ ПРЯМОЙ или КОСВЕННЫЙ путь?\n"
        "   ПРЯМОЙ: действие → результат немедленно\n"
        "     └ send_outreach_email, run_agent_action, find_and_message, create_post\n"
        "   КОСВЕННЫЙ: создать условия → результат появится сам (легче/масштабнее)\n"
        "     └ Примеры косвенных подходов под разные цели:\n"
        "       • Цель 'найти пользователей' → создай landing page / FAQ / демо-видео → люди регистрируются сами\n"
        "       • Цель 'анализ рынка' → настрой RSS/API автосбор → данные приходят регулярно\n"
        "       • Цель 'привлечь подписчиков' → напиши вирусную статью → охват органический\n"
        "       • Цель 'найти экспертов' → опубликуй кейс-стади → эксперты сами откликнутся\n"
        "   🔴 ПРАВИЛО: если прямой путь опробован 2+ раза без прогресса → переключись на косвенный!\n"
        "       Косвенный = research_and_plan('Что подготовить для [цель]?') + add_task/delegate_task\n\n"
        "3️⃣ ЧТО МЕШАЕТ достижению?\n"
        "   • Технический блокер? (нет API ключа → попроси пользователя подключить)\n"
        "   • Информационный? (не знаем где искать → research_and_plan / analyze_situation)\n"
        "   • Процессный? (нет инфраструктуры → создай: landing, FAQ, onboarding, demo)\n"
        "   • Масштабный? (делаем вручную → автоматизируй: schedule_background_task, RSS)\n"
        "   → Устраняй самый критичный блокер ПЕРЕД продолжением прямых действий\n\n"
        "4️⃣ ЧТО можно ИСПОЛЬЗОВАТЬ?\n"
        "   • Интеграции: какие API/данные уже подключены? (см. 'ИНТЕГРАЦИИ АГЕНТА' выше)\n"
        "   • Команда: кто работает над похожим? Что делегировать? Какие данные получить?\n"
        "   • Прошлое: check_emails / list_email_contacts — кто уже ответил?\n"
        "   • Инструменты: research_and_plan вместо web_search (комплексная работа за 1 вызов)\n\n"
        "5️⃣ СЛЕПЫЕ ЗОНЫ — что я НЕ рассматриваю?\n"
        "   • Другой сегмент? (если искал на GitHub → попробуй Reddit/Discord/Habr/StackOverflow/HN)\n"
        "   • Конференции/мероприятия/хакатоны? research_topic('конференции [тема] 2026 Россия') → find_relevant_contacts_for_task\n"
        "   • Сообщества? research_topic('Telegram/Discord/Reddit каналы [тема]') → find_and_message_relevant_users\n"
        "   • Другой язык? (если EN → попробуй RU, если GitHub → попробуй LinkedIn/HH.ru)\n"
        "   • Другой подход? (если прямой → косвенный; если поиск → контент-приманка)\n"
        "   • НЕпопробованные инструменты? (см. 'ЕЩЁ НЕ ПРОБОВАЛ' в истории выше)\n"
        "   • Входящий трафик? (вместо прямого поиска — СОЗДАЙ что-то что приведёт людей):\n"
        "     → publish_to_telegram('Ищем бета-тестеров [продукт]. Вот почему стоит попробовать: [ценность]...')\n"
        "     → research_topic('[тема] форумы сообщества') + find_and_message_relevant_users со ссылкой на пост\n"
        "     → create_post с нативным контентом по теме без прямой рекламы → органический интерес\n"
        "   • Реферальная петля? (те кто ответили позитивно — попроси порекомендовать 1-2 коллег)\n\n"
        "→ После анализа — СРАЗУ вызывай ЛУЧШИЙ инструмент. Без предисловий, без текста.\n\n"
        "ПРАВИЛА АВТОПИЛОТА:\n"
        "1. Первый ответ = вызов инструмента (НЕ текст — иначе провал задачи).\n"
        "2. Работай ПО СВОЕЙ РОЛИ и специализации как первый приоритет.\n"
        "   НО: если в нише застрял — выходи за рамки роли, используй ВЕСЬ каталог. Результат важнее роли.\n"
        "3. РАССУЖДАЙ через вопрос: что РЕАЛЬНО = +1 к метрике этой цели? Затем выбирай инструмент.\n"
        "4. Цепочка: ИНСТРУМЕНТ → обработай РЕЗУЛЬТАТ → update_goal_progress (макс ОДИН раз за сессию).\n"
        "   GitHub цепочка ОБЯЗАТЕЛЬНАЯ: run_agent_action(search_users) → save_email_contact × N → send_outreach_email × N.\n"
        "   GitHub запросы ДОЛЖНЫ МЕНЯТЬСЯ каждый цикл:\n"
        "     Цикл 1: 'language:python followers:>10' → Цикл 2: 'machine learning repos:>5' → Цикл 3: 'qa automation location:Russia'\n"
        "     ❌ НЕЛЬЗЯ повторять один и тот же запрос два цикла подряд — меняй query, язык, фильтры.\n"
        "5. Если нашёл данные но нет нужной интеграции — делегируй коллеге с конкретными данными.\n"
        + ("6. Каждый цикл = РАЗНЫЕ подходы (строго чередуй!):\n"
        "   Цикл A: поиск людей (GitHub/web_search) + персонализированная рассылка\n"
        "   Цикл B: контент-магнит (publish_to_telegram/discord) с ценностью для целевой аудитории\n"
        "   Цикл C: ответы на входящие (check_emails + reply_to_outreach_email + конверсия в пользователя)\n"
        "   Цикл D: новая площадка (Reddit, Discord-сервер, HN, конференции, Product Hunt, Habr)\n"
        "   Цикл E: community seeding — find_and_message_relevant_users в Telegram/Discord группах по теме\n"
        "   Цикл F: inbound funnel — создай ценностный пост/FAQ/демо → share ссылку в 2-3 сообществах\n"
        "   ЗАПРЕЩЕНО: делать одно и то же действие 3+ раза подряд.\n"
        if _goal_type in ('outreach', 'general') else
        "6. Каждый цикл = РАЗНЫЕ подходы: смени инструмент если повторяешься. ЗАПРЕЩЕНО одно действие 3+ раз подряд.\n")
        + "7. НЕ пиши одним и тем же людям повторно.\n"
        "7а. ПУБЛИКАЦИЯ В СВОЙ КАНАЛ — publish_to_telegram(content=...) / publish_to_discord(content=...):\n"
        + (f"   publish_to_telegram публикует в личный канал пользователя ({getattr(user, 'telegram_channel', None) or 'настроен в профиле'}).\n" if user else "   publish_to_telegram публикует в личный Telegram-канал пользователя (настроен в профиле).\n")
        + "   publish_to_discord публикует на сервер Discord пользователя.\n"
        "   Это контент-магнит: интересный пост → люди сами найдут и напишутся.\n"
        "   Правильный синтаксис: publish_to_telegram(content='текст поста')\n"
        "   ⚠️ НЕ работает для публикации в ЧУЖИЕ каналы (@qa_automation и т.д.) — нет прав.\n"
        "   Если нужно достучаться до чужой аудитории — используй Цикл A (поиск + прямая рассылка).\n"
        "7б. ПЕРСОНАЛИЗАЦИЯ ПИСЬМА — проверяй ПЕРЕД send_outreach_email:\n"
        "   ✅ Хорошее письмо: упоминает конкретный проект/стек/статью получателя\n"
        "     Пример (GitHub): 'Видел твой репозиторий llm-agent-framework — именно таких разработчиков ищем...'\n"
        "     Пример (RSS): 'Читал твою статью о [тема] — как раз работаем над похожей задачей...'\n"
        "   ❌ Плохое письмо: 'Привет, ищем бета-тестеров. Вот наш продукт.' — generic без упоминания человека\n"
        "   КАК: используй параметры topic и audience в send_outreach_email:\n"
        "     topic='[чем занимается контакт / его стек]', audience='[их роль/интерес]'\n"
        "   ПРАВИЛО: 1 персонализированное письмо > 10 шаблонных. Конверсия в 3-5× выше.\n"
        "8. 🛡️ АНАЛИЗ НАМЕРЕНИЯ КОНТАКТА + КОНВЕРСИОННЫЙ ПЛЕЙБУК:\n"
        "   Прочитай reply_text и определи намерение:\n"
        "   • НЕГАТИВНОЕ (не интересно, stop, unsubscribe, любой отказ ЛЮБОЙ язык) → НЕ ОТВЕЧАЙ!\n"
        "     → update_email_contact_status(email, status='unsubscribed') — обязательно!\n"
        "   • НЕЙТРАЛЬНОЕ ('расскажите подробнее', 'что это?') → reply_to_outreach_email с:\n"
        "     — конкретным фактом о ценности продукта для ИХ задачи (персонально!)\n"
        "     — чётким CTA: 'попробуй за 5 минут: [ссылка]' или 'давай созвонимся?'\n"
        "   • ПОЗИТИВНОЕ (интерес, вопросы, 'да', 'хочу попробовать') → КОНВЕРСИОННЫЙ ПЛЕЙБУК:\n"
        "     Шаг 1: reply_to_outreach_email — поблагодари + дай прямую ссылку/инструкцию для старта\n"
        "     Шаг 2: если спрашивает ЧТО ИМЕННО делать → negotiate_by_email с конкретными шагами регистрации\n"
        "     Шаг 3: если подтвердил регистрацию/использование → update_goal_progress(+1) — ЭТО реальный прогресс!\n"
        "     Шаг 4: если не ответил 3+ дня после позитивного ответа → send_follow_up_email с напоминанием\n"
        "     Шаг 5: у кто стал пользователем — попроси порекомендовать 1-2 коллег (реферальная петля)\n"
        "   Пиши ВСЕГДА на языке контакта. Греческий → греческий, испанский → испанский.\n"
        "9. Задания МОГУТ БЫТЬ КОСВЕННЫМИ: изучи тренды → предложи темы, найди экспертов → сохрани.\n"
        "10. Отчёт = ФАКТЫ: что нашёл, кому отправил, что ответили, цифры.\n"
        "11. ЧЕСТНОСТЬ: если инструмент не дал данных или идеи исчерпаны — "
        "начни ответ со слова БЛОКЕР: и опиши суть. ASI обратится к пользователю за помощью.\n"
        + ("⛔ АБСОЛЮТНЫЙ ЗАПРЕТ: НЕ вызывай check_emails — у тебя нет IMAP/Gmail ключей. "
           "Почту читает только Кристина (агент с Gmail). Если нужно проверить входящие — "
           "используй DELEGATE[Кристина]: проверь входящие и сообщи мне ответы.\n"
           if not _has_imap else '')
        + ("11. У тебя GitHub — правила работы с GitHub СТРОГИЕ:\n"
           "    ПРАВИЛЬНЫЙ запрос: run_agent_action(action='search_users', params={'query': 'language:python followers:>10'}\n"
           "    ЗАПРЕЩЁННЫЕ запросы (дадут 0 результатов):\n"
           "      ❌ email-адреса: 'user@gmail.com repos:>5' → вернёт 0\n"
           "      ❌ названия задач: 'email_analysis repos:>5' → вернёт 0\n"
           "      ❌ случайные слова из контекста → вернёт 0\n"
           "    ПРАВИЛЬНЫЕ query-примеры для поиска (ОБЯЗАТЕЛЬНО ЧЕРЕДУЙ — НЕ ПОВТОРЯЙ!):\n"
           "      ✅ 'language:python repos:>10 followers:>5' — Python-разработчики\n"
           "      ✅ 'autonomous agent language:python followers:>20' — AI-энтузиасты\n"
           "      ✅ 'machine learning location:Russia repos:>5' — ML в РФ\n"
           "      ✅ 'indie hacker saas repos:>15 followers:>30' — инди-разработчики\n"
           "      ✅ 'testing qa automation followers:>10 repos:>8' — QA-инженеры\n"
           "      ✅ 'language:typescript ai agent followers:>15' — TS AI-разработчики\n"
           "      ✅ 'language:rust systems programming repos:>10' — Rust-разработчики\n"
           "      ✅ 'llm openai language:python followers:>10' — LLM-энтузиасты\n"
           "    ВАЖНО: Если в _этом_ цикле уже использовал 'language:python' → возьми ДРУГОЙ query!\n"
           "    ПОСЛЕ ПОЛУЧЕНИЯ РЕЗУЛЬТАТОВ ОБЯЗАТЕЛЬНО:\n"
           "      1) save_email_contact для КАЖДОГО найденного с email\n"
           "      2) send_outreach_email каждому из них ← без этого шага весь цикл впустую!\n"
           "   Нашёл людей но не написал — провал!\n"
           if _has_github else '')
        + ("11. У тебя RSS-лента — после run_agent_action (RSS) ОБЯЗАТЕЛЬНО вызови save_email_contact "
           "для каждого найденного автора/источника. Без сохранения данные потеряются!\n"
           if _has_rss and not _has_github else '')
        + ("11. Если активная email-кампания уже существует И в базе есть контакты — "
           "вызови send_outreach_email НАПРЯМУЮ конкретным людям из find_relevant_contacts_for_task. "
           "НЕ запускай новую кампанию — пиши персонально!\n"
           if _has_imap else '')
        + ""
        + "13. КОМАНДНАЯ ЭСТАФЕТА: нашёл контакт — не держи у себя! "
          "DELEGATE[Имя с email-интеграцией]: Отправь письмо name@domain.com — [суть].\n"
        + "14. add_task — ЗАПРЕЩЕНО создавать задачи вместо пользователя. "
          "Только внутренние шаги агента (source=agent) с конкретным действием и датой. "
          "НЕ создавай абстрактных задач ('изучить', 'подумать') — если можешь действовать сам, действуй. "
          "Запрещено добавлять задачи в список пользователя без его явного запроса.\n"
        + ("15. МУЛЬТИКАНАЛЬНАЯ ЭСКАЛАЦИЯ (когда прямой outreach буксует):\n"
           "   Уровень 1 — оптимизация email: смени тему письма, целевую аудиторию, ценностное предложение.\n"
           "   Уровень 2 — смена канала: email → Telegram/Discord (find_and_message_relevant_users в тематических группах).\n"
           "   Уровень 3 — контент-стратегия: publish_to_telegram/discord с showcase/кейсом → органический интерес.\n"
           "   Уровень 4 — community seeding: research_topic('[тема] Telegram Discord сообщества') → вступи → поделись ценностью там.\n"
           "   Уровень 5 — партнёрский outreach: find_partners (смежные продукты/сервисы) → кросс-промо.\n"
           "   ТРИГГЕР эскалации: если за последние 5 писем 0 ответов → обязательно перейди на Уровень 2+.\n"
           if _goal_type in ('outreach', 'general') else '')
    )

# Группы батчинга
BATCH_GROUPS = {
    'task_reminder': 'tasks',
    'task_overdue': 'tasks',
    'task_deadline_soon': 'tasks',
    'task_stale': 'tasks',
    'task_completed_streak': 'tasks',
    'goal_progress': 'goals',
    'goal_stagnation': 'goals',
    'goal_deadline': 'goals',
    'contact_match': 'contacts',
    'delegation_pending': 'delegation',
    'delegation_update': 'delegation',
    'market_insight': 'insights',
    'content_opportunity': 'insights',
    'profile_gap': 'engagement',
    'dialog_followup': 'engagement',
    'task_result_check': 'tasks',
    'recurring_task_due': 'tasks',
    'post_opportunity': 'posting',
    'channel_post': 'posting',
    'discord_post': 'posting',
    'weekly_milestone': 'milestones',
    'goal_milestone': 'milestones',
    'event_discovery': 'insights',
    'contact_activity': 'contacts',
    'incoming_message': 'engagement',
    'token_low_balance': 'engagement',
    'delegation_overdue': 'delegation',
    'goal_decomposition': 'goals',
    'inactivity_reengagement': 'engagement',
    # Email outreach
    'email_outreach_send': 'email',
    'email_follow_up': 'email',
    'email_reply_received': 'email',
    'email_campaign_report': 'email',
    'email_need_leads': 'email',    # Кампании нужны новые контакты
    # Content campaigns
    'content_campaign_publish': 'content',
    # Delegation campaigns
    'delegation_campaign_send': 'delegation',
    'delegation_campaign_follow_up': 'delegation',
    # System
    'service_degraded': 'system',
    'payment_failed': 'system',
    'agent_script_failed': 'system',    # Сбой скрипта/ключей у пользовательского агента
    'weather_extreme': 'system',        # Экстремальная погода в городе пользователя
    # Background research
    'background_research_ready': 'insights',
    # Интеграции пользовательских агентов
    'integration_alert': 'integration',  # Gmail/Ozon/RSS/любые скрипты
    # Офисный координатор (Living Office Engine)
    'agent_office_update': 'integration',  # АСИ назначил агенту задачу по целям
    # Делегирование (результаты от директорского агента)
    'agent_delegation': 'office',          # Итог делегирования субагенту
    # Кастомные якоря из UserAgent.custom_anchors
    'custom_anchor': 'integration',        # Пользовательский триггер агента
    # Новые офисные якоря
    'agent_inbox_reply': 'office',         # Агент нашёл новые входящие письма (IMAP)
    'agent_task_blocked': 'office',        # Агент застрял, нужно решение пользователя
    # Goal autopilot
    'goal_autopilot_review': 'goals',      # Периодический AI-анализ целей — автономные действия
}


def _t(user, ru: str, en: str) -> str:
    """Pick anchor topic string based on user language."""
    lang = getattr(user, 'language', 'ru') or 'ru'
    return en if lang == 'en' else ru


class AnchorEngine:
    """
    Единый движок автономии. Сканирует → Оценивает → Доставляет.
    """

    def __init__(self, bot=None):
        self.bot = bot
        self.running = False
        self._scan_locks = defaultdict(asyncio.Lock)
        # Семафор для AI-вызовов — ограничивает параллельные запросы к DeepSeek
        # 12 = баланс между скоростью обработки 1000 юзеров и лимитами DeepSeek API
        # (autonomous_agent использует 20, anchor engine — фоновый, поэтому чуть меньше)
        self._ai_semaphore = asyncio.Semaphore(12)
        logger.info("[ANCHOR] AnchorEngine initialized")

    # ═══════════════════════════════════════════════════════
    # MAIN LOOP
    # ═══════════════════════════════════════════════════════

    async def start(self):
        """Запуск бесконечного цикла сканирования"""
        self.running = True
        self._cycle_counter = 0
        logger.info(f"[ANCHOR] 🚀 Starting scan loop (every {SCAN_INTERVAL_MINUTES}min)")
        # Стартовая задержка: даём серверу прогреться перед первым сканированием.
        # Это предотвращает лавину якорей / уведомлений сразу после деплоя.
        await asyncio.sleep(30)  # reduced from 120s — быстрее восстанавливается после Railway рестартов
        # ── Recovery: помечаем "застрявшие" in_progress dispatch-записи как failed ──
        # Это обычно происходит после рестарта Railway: процесс убит, error handler не сработал
        try:
            from models import Session as _RecDb, AgentActivityLog as _RecAAL
            _s_rec = _RecDb()
            try:
                _stuck = (
                    _s_rec.query(_RecAAL)
                    .filter(
                        _RecAAL.activity_type.in_(['goal_autopilot_dispatch', 'agent_event_dispatch', 'agent_chain_continue']),
                        _RecAAL.status == 'in_progress',
                        _RecAAL.created_at < datetime.now(timezone.utc) - timedelta(minutes=10),
                    )
                    .all()
                )
                if _stuck:
                    for _st in _stuck:
                        _st.status = 'failed'
                        _st.result = (_st.result or '') + ' [recovered: process restart]'
                    _s_rec.commit()
                    logger.info("[ANCHOR] Recovery: marked %d stuck in_progress entries as failed", len(_stuck))
            finally:
                _s_rec.close()
        except Exception as _rec_err:
            logger.debug("[ANCHOR] Recovery error: %s", _rec_err)
        while self.running:
            try:
                import time as _time
                cycle_start = _time.monotonic()
                self._cycle_counter += 1
                logger.info(f"[ANCHOR] 🔄 Starting scan cycle #{self._cycle_counter}")
                await self._scan_all_users()
                cycle_duration = _time.monotonic() - cycle_start

                # Периодическое обслуживание: mark_ignored каждые ~12 циклов (~60 мин),
                # cleanup каждые ~144 цикла (~12 часов)
                if self._cycle_counter % 12 == 0:
                    try:
                        await self.mark_ignored_deliveries()
                    except Exception as _mie:
                        logger.debug(f"[ANCHOR] mark_ignored error: {_mie}")
                if self._cycle_counter % 144 == 0:
                    try:
                        await self.cleanup_old_anchors()
                    except Exception as _coe:
                        logger.debug(f"[ANCHOR] cleanup error: {_coe}")

                # Adaptive sleep: если цикл занял долго, спим меньше
                target_interval = SCAN_INTERVAL_MINUTES * 60
                sleep_time = max(60, target_interval - cycle_duration)  # минимум 1 мин
                logger.info(f"[ANCHOR] ✅ Scan cycle complete in {cycle_duration:.1f}s, sleeping {sleep_time:.0f}s")
                await asyncio.sleep(sleep_time)
            except Exception as e:
                logger.error(f"[ANCHOR] Loop error: {e}\n{traceback.format_exc()}")
                await asyncio.sleep(300)

    async def stop(self):
        self.running = False
        logger.info("[ANCHOR] Stopped")

    async def _scan_all_users(self):
        """Двухфазный пайплайн: bulk pre-filter → parallel scan+eval
        
        При 1000 юзерах:
        - Phase 0: 1 запрос, отсеивает ~60% (ночь/DND) → ~400 eligible
        - Phase 1: DB-scan 10 параллельно, без AI → ~200ms/user → 400/10 × 0.2 = 8s
        - Phase 2: AI eval только для юзеров с ready anchors (~5%) → ~20 AI calls
        """
        session = Session()
        try:
            # ── PHASE 0: Массовый pre-filter (1 запрос к БД) ──
            users = session.query(User).filter(
                User.telegram_id.isnot(None)
            ).all()

            now_utc = datetime.now(timezone.utc)

            # Batch pre-load night-exception flags for all users (avoid 4×N queries)
            _pf_uids = [u.id for u in users]
            _night_exc_reminders = {row[0] for row in session.query(Task.user_id).filter(
                Task.user_id.in_(_pf_uids),
                Task.reminder_sent == False,
                Task.reminder_time <= now_utc,
                Task.status.in_(['pending', 'in_progress', 'active'])
            ).distinct().all()} if _pf_uids else set()
            _night_exc_unreplied = {row[0] for row in session.query(EmailCampaign.user_id).join(
                EmailOutreach, EmailOutreach.campaign_id == EmailCampaign.id
            ).filter(
                EmailCampaign.user_id.in_(_pf_uids),
                EmailOutreach.status == 'replied',
                EmailOutreach.reply_text.isnot(None),
                EmailOutreach.ai_reply_sent_at.is_(None),
            ).distinct().all()} if _pf_uids else set()
            _night_exc_drafts = {row[0] for row in session.query(EmailCampaign.user_id).join(
                EmailOutreach, EmailOutreach.campaign_id == EmailCampaign.id
            ).filter(
                EmailCampaign.user_id.in_(_pf_uids),
                EmailCampaign.status == 'active',
                EmailOutreach.status == 'draft',
            ).distinct().all()} if _pf_uids else set()
            _night_exc_followups = {row[0] for row in session.query(EmailCampaign.user_id).join(
                EmailOutreach, EmailOutreach.campaign_id == EmailCampaign.id
            ).filter(
                EmailCampaign.user_id.in_(_pf_uids),
                EmailCampaign.status == 'active',
                EmailOutreach.status.in_(['sent', 'delivered', 'opened']),
                EmailOutreach.next_follow_up_at <= now_utc,
            ).distinct().all()} if _pf_uids else set()
            # Автопилот работает 24/7 — пользователей с goal_autopilot_enabled=True
            # всегда включаем в скан (ночью тоже), чтобы создавать новые autopilot якоря
            _night_exc_autopilot = {row[0] for row in session.query(UserProfile.user_id).filter(
                UserProfile.user_id.in_(_pf_uids),
                UserProfile.goal_autopilot_enabled == True,
            ).all()} if _pf_uids else set()

            eligible = []
            skipped_night = 0
            skipped_dnd = 0

            for u in users:
                # DND check
                if u.do_not_disturb_until:
                    dnd = u.do_not_disturb_until
                    if dnd.tzinfo is None:
                        dnd = dnd.replace(tzinfo=timezone.utc)
                    if now_utc < dnd:
                        skipped_dnd += 1
                        continue

                # Night hours check
                try:
                    user_tz = pytz.timezone(u.timezone or 'Europe/Moscow')
                    user_now = datetime.now(user_tz)
                    if user_now.hour >= NIGHT_START_HOUR or user_now.hour < MORNING_START_HOUR:
                        # Use pre-loaded batch sets (no per-user queries)
                        has_pending_reminder = u.id in _night_exc_reminders
                        has_unreplied_email = u.id in _night_exc_unreplied
                        has_email_drafts = u.id in _night_exc_drafts
                        has_follow_ups = u.id in _night_exc_followups
                        is_deep_night = AUTOPILOT_DEEP_NIGHT_START <= user_now.hour < AUTOPILOT_DEEP_NIGHT_END
                        has_autopilot = (u.id in _night_exc_autopilot) and not is_deep_night
                        if not has_pending_reminder and not has_unreplied_email and not has_email_drafts and not has_follow_ups and not has_autopilot:
                            skipped_night += 1
                            continue
                        else:
                            if has_pending_reminder:
                                logger.info(f"[ANCHOR] Pre-filter: User {u.telegram_id} is night BUT has pending reminder, including")
                            if has_unreplied_email:
                                logger.info(f"[ANCHOR] Pre-filter: User {u.telegram_id} is night BUT has unreplied email, including")
                            if has_email_drafts:
                                logger.info(f"[ANCHOR] Pre-filter: User {u.telegram_id} is night BUT has email drafts to send (silent), including")
                            if has_follow_ups:
                                logger.info(f"[ANCHOR] Pre-filter: User {u.telegram_id} is night BUT has follow-ups to send (silent), including")
                            if has_autopilot:
                                logger.info(f"[ANCHOR] Pre-filter: User {u.telegram_id} is night BUT has pending autopilot, including")
                except Exception as _tz_err:
                    logger.warning(f"[ANCHOR] Pre-filter: timezone error for user {u.telegram_id}: {_tz_err}")  # проверим в _process_user_inner

                eligible.append(u.telegram_id)

            logger.info(
                f"[ANCHOR] Pre-filter: {len(users)} total → {len(eligible)} eligible "
                f"(skipped: {skipped_night} night, {skipped_dnd} DND)"
            )
        finally:
            session.close()

        # ── PHASE 1+2: Параллельная обработка eligible пользователей ──
        # DB-scan безопасен при высоком параллелизме, AI ограничен семафором
        BATCH_CONCURRENCY = 25
        for i in range(0, len(eligible), BATCH_CONCURRENCY):
            batch = eligible[i:i + BATCH_CONCURRENCY]
            tasks = []
            for uid in batch:
                lock = self._scan_locks[uid]
                if lock.locked():
                    continue
                tasks.append(self._process_user_safe(uid, lock))
            if tasks:
                await asyncio.gather(*tasks)

    async def _process_user_safe(self, user_id: int, lock: asyncio.Lock):
        """Обёртка с lock для безопасной параллельной обработки"""
        async with lock:
            try:
                await self._process_user(user_id)
            except Exception as e:
                logger.error(f"[ANCHOR] Error processing user {user_id}: {e}")

    async def _process_user(self, user_id: int):
        """Полный цикл для одного пользователя: scan → evaluate → deliver"""
        session = Session()
        try:
            # ── DB-LEVEL ADVISORY LOCK — атомарная защита от параллельных процессов ──
            # pg_try_advisory_lock не блокирует, а возвращает False если lock занят другим процессом
            # PostgreSQL advisory lock — атомарная защита от параллельных процессов
            # SQLite не поддерживает advisory locks — пропускаем
            lock_id = abs(user_id) % 2147483647
            use_advisory_lock = False
            try:
                lock_result = session.execute(
                    text("SELECT pg_try_advisory_lock(:lock_id)"),
                    {"lock_id": lock_id}
                ).scalar()
                if not lock_result:
                    logger.debug(f"[ANCHOR] User {user_id}: ⛔ advisory lock busy (another process), skip")
                    return
                use_advisory_lock = True
            except Exception as _lock_err:
                # SQLite или другая БД без advisory locks — продолжаем без них
                logger.debug(f"[ANCHOR] User {user_id}: advisory lock unavailable ({_lock_err}), proceeding without lock")

            try:
                await self._process_user_inner(user_id, session)
            finally:
                if use_advisory_lock:
                    try:
                        session.execute(text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": lock_id})
                        session.commit()
                    except Exception as _unlock_err:
                        logger.warning(f"[ANCHOR] User {user_id}: advisory unlock failed ({_unlock_err}), lock may leak")

        except Exception as e:
            logger.error(f"[ANCHOR] _process_user({user_id}) error: {e}\n{traceback.format_exc()}")
            session.rollback()
        finally:
            session.close()

    async def _process_user_inner(self, user_id: int, session):
        """Внутренняя логика обработки пользователя (под advisory lock)"""
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            logger.debug(f"[ANCHOR] User {user_id}: не найден в БД, пропуск")
            return

        # Проверка баланса токенов (минимум на 1 проактивное сообщение)
        # НЕ блокируем полностью — email silent имеет отдельную проверку токенов
        from token_service import has_enough_tokens, get_balance
        from config import FREE_ACCESS_MODE
        has_proactive_tokens = True
        if not FREE_ACCESS_MODE and not has_enough_tokens(user_id, 'proactive_message'):
            balance = get_balance(user_id)
            has_proactive_tokens = False
            logger.info(f"[ANCHOR] User {user_id}: ⚠️ недостаточно токенов для proactive (баланс: {balance}), dialog/posts заблокированы, email silent продолжит")

        # Проверка DND
        if user.do_not_disturb_until:
            dnd = user.do_not_disturb_until
            if dnd.tzinfo is None:
                dnd = dnd.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) < dnd:
                logger.info(f"[ANCHOR] User {user_id}: ⛔ DND до {dnd}, пропуск")
                return

        # Проверка ночных часов — НЕ блокируем полностью, а помечаем флагом
        user_tz = pytz.timezone(user.timezone or 'Europe/Moscow')
        user_now = datetime.now(user_tz)
        is_night = user_now.hour >= NIGHT_START_HOUR or user_now.hour < MORNING_START_HOUR
        if is_night:
            # Проверяем есть ли pending task reminders — если есть, продолжаем для них
            has_pending = session.query(Task).filter(
                Task.user_id == user.id,
                Task.reminder_sent == False,
                Task.reminder_time <= datetime.now(timezone.utc),
                Task.status.in_(['pending', 'in_progress', 'active'])
            ).first() is not None
            # Проверяем есть ли непрочитанные email-ответы (CRITICAL — нельзя блокировать)
            has_unreplied_email = session.query(EmailOutreach).join(EmailCampaign).filter(
                EmailCampaign.user_id == user.id,
                EmailOutreach.status == 'replied',
                EmailOutreach.reply_text.isnot(None),
                EmailOutreach.ai_reply_sent_at.is_(None),
            ).first() is not None
            if not has_pending and not has_unreplied_email:
                logger.info(f"[ANCHOR] User {user_id}: 🌙 ночные часы ({user_now.strftime('%H:%M')} {user.timezone or 'Europe/Moscow'}, окно {MORNING_START_HOUR}:00-{NIGHT_START_HOUR}:00) — dialog/posts заблокированы, silent продолжат")
                # НЕ return — email silent / content / delegation обрабатываются ниже по is_night флагу
            if has_pending:
                logger.info(f"[ANCHOR] User {user_id}: 🌙 ночные часы, но есть pending reminders — обрабатываем только CRITICAL")
            if has_unreplied_email:
                logger.info(f"[ANCHOR] User {user_id}: 🌙 ночные часы, но есть unreplied email — обрабатываем email_reply_received")

        # ── Подсчёт доставок за сегодня (раздельно) ──
        today_start = user_now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_utc = today_start.astimezone(pytz.UTC)

        today_logs = session.query(AnchorDeliveryLog).filter(
            AnchorDeliveryLog.user_id == user.id,
            AnchorDeliveryLog.created_at >= today_start_utc
        ).all()

        dialog_count = 0
        post_count = 0
        channel_count = 0
        discord_count = 0
        # Silent типы, которые НЕ являются сообщениями пользователю
        _SILENT_LOG_TYPES = {
            'email_outreach_send', 'email_follow_up', 'email_need_leads',
            'content_campaign_publish',
            'delegation_campaign_send', 'delegation_campaign_follow_up',
            'agent_delegation',  # legacy
        }
        for log in today_logs:
            try:
                types = json.loads(log.anchor_types) if log.anchor_types else []
            except (json.JSONDecodeError, TypeError):
                types = []
            if 'channel_post' in types:
                channel_count += 1
            elif 'discord_post' in types:
                discord_count += 1
            elif 'post_opportunity' in types:
                post_count += 1
            elif types and all(t in _SILENT_LOG_TYPES for t in types):
                pass  # Тихие доставки не считаются: пользователь не получил сообщение
            else:
                dialog_count += 1

        # ── Подавление проактивных во время активного диалога ──
        last_user_msg = session.query(Interaction).filter(
            Interaction.user_id == user.id,
            Interaction.message_type == 'user'
        ).order_by(Interaction.created_at.desc()).first()

        active_dialog = False
        if last_user_msg:
            lm_time = last_user_msg.created_at
            if lm_time.tzinfo is None:
                lm_time = lm_time.replace(tzinfo=timezone.utc)
            since_last_msg = datetime.now(timezone.utc) - lm_time
            if since_last_msg < timedelta(minutes=ACTIVE_DIALOG_SUPPRESS_MINUTES):
                active_dialog = True
                logger.info(f"[ANCHOR] User {user_id}: 💬 active dialog ({since_last_msg.total_seconds():.0f}s ago) — suppress regular proactive")

        # ── Последнее проактивное сообщение (gap между ними, но НЕ блокирует CRITICAL) ──
        last_proactive = session.query(Interaction).filter(
            Interaction.user_id == user.id,
            Interaction.message_type == 'proactive'
        ).order_by(Interaction.created_at.desc()).first()

        proactive_gap_ok = True
        if last_proactive:
            lp_time = last_proactive.created_at
            if lp_time.tzinfo is None:
                lp_time = lp_time.replace(tzinfo=timezone.utc)
            gap = datetime.now(timezone.utc) - lp_time
            if gap < timedelta(minutes=MIN_PROACTIVE_GAP_MINUTES):
                proactive_gap_ok = False

        # 0b. CLEANUP — удаляем expired-but-undelivered якоря старше 2ч (мусор в БД)
        # Это якоря, которые никогда не смогут быть доставлены (expires_at прошёл)
        try:
            cleanup_threshold = datetime.now(timezone.utc) - timedelta(hours=2)
            expired_gone = session.query(Anchor).filter(
                Anchor.user_id == user.id,
                Anchor.delivered_at.is_(None),
                Anchor.expires_at.isnot(None),
                Anchor.expires_at < cleanup_threshold,
            ).delete(synchronize_session=False)
            if expired_gone > 0:
                session.commit()
                logger.info(f"[ANCHOR] User {user_id}: 🧹 cleaned {expired_gone} expired-but-undelivered anchors")
        except Exception as _cleanup_err:
            logger.debug(f"[ANCHOR] Cleanup error (non-critical): {_cleanup_err}")
            session.rollback()

        # 0c. DEDUP — удаляем дубли pending-якорей с одним type+source (оставляем самый свежий)
        # Возникает когда сервис деградирован несколько циклов подряд (service_degraded, weather_extreme и т.д.)
        try:
            from sqlalchemy import func as _func_dedup
            _dup_groups = session.query(
                Anchor.anchor_type, Anchor.source,
                _func_dedup.count(Anchor.id).label('cnt'),
            ).filter(
                Anchor.user_id == user.id,
                Anchor.delivered_at.is_(None),
                Anchor.source.isnot(None),
            ).group_by(Anchor.anchor_type, Anchor.source).having(
                _func_dedup.count(Anchor.id) > 1
            ).all()
            _total_dedup = 0
            for _dg in _dup_groups:
                _keep = session.query(Anchor).filter(
                    Anchor.user_id == user.id,
                    Anchor.anchor_type == _dg.anchor_type,
                    Anchor.source == _dg.source,
                    Anchor.delivered_at.is_(None),
                ).order_by(Anchor.id.desc()).first()
                if _keep:
                    _gone = session.query(Anchor).filter(
                        Anchor.user_id == user.id,
                        Anchor.anchor_type == _dg.anchor_type,
                        Anchor.source == _dg.source,
                        Anchor.delivered_at.is_(None),
                        Anchor.id != _keep.id,
                    ).delete(synchronize_session=False)
                    _total_dedup += _gone
            if _total_dedup > 0:
                session.commit()
                logger.info(f"[ANCHOR] User {user_id}: 🧹 dedup removed {_total_dedup} duplicate pending anchors")
        except Exception as _e_dedup:
            logger.debug(f"[ANCHOR] Dedup error (non-critical): {_e_dedup}")
            session.rollback()

        # 0d. STUCK LOGS — помечаем зависшие in_progress activity logs (>8 мин) как failed
        try:
            _stuck_cutoff = datetime.utcnow() - timedelta(minutes=8)  # naive UTC — PostgreSQL возвращает naive datetime
            from models import AgentActivityLog as _AAL_stuck
            _stuck_logs = session.query(_AAL_stuck).filter(
                _AAL_stuck.user_id == user.id,
                _AAL_stuck.status == 'in_progress',
                _AAL_stuck.created_at < _stuck_cutoff,
            ).all()
            if _stuck_logs:
                for _sl in _stuck_logs:
                    _sl.status = 'failed'
                    _sl.result = 'timeout/process_restart'
                session.commit()
                logger.info(f"[ANCHOR] User {user_id}: 🧹 cleaned {len(_stuck_logs)} stuck in_progress activity logs")
        except Exception as _stuck_err:
            logger.debug(f"[ANCHOR] Stuck log cleanup error: {_stuck_err}")
            session.rollback()

        # 1. SCAN — обнаружить новые якоря
        new_anchors = await self._scan_anchors(user, session)
        if new_anchors:
            # Dedup-фильтр: не создаём якорь если уже есть pending с тем же type+source
            _existing_keys = set(
                session.query(Anchor.anchor_type, Anchor.source).filter(
                    Anchor.user_id == user.id,
                    Anchor.delivered_at.is_(None),
                    Anchor.source.isnot(None),
                ).all()
            )
            _seen_in_batch: set = set()
            _deduped = []
            for _na in new_anchors:
                _key = (_na.anchor_type, _na.source)
                if _key not in _existing_keys and _key not in _seen_in_batch:
                    _deduped.append(_na)
                    _seen_in_batch.add(_key)
            if len(_deduped) < len(new_anchors):
                logger.info(f"[ANCHOR] User {user_id}: scan dedup: {len(new_anchors)-len(_deduped)} skipped (already pending), {len(_deduped)} new")
            new_anchors = _deduped
        if new_anchors:
            # Защита от race condition при multi-instance деплое:
            # если уникальный индекс сработал — откатываемся и вставляем по одному
            from sqlalchemy.exc import IntegrityError as _IntegrityError
            try:
                session.add_all(new_anchors)
                session.commit()
                logger.info(f"[ANCHOR] User {user_id}: created {len(new_anchors)} new anchors")
            except _IntegrityError:
                session.rollback()
                _saved = 0
                for _one_anchor in new_anchors:
                    try:
                        session.add(_one_anchor)
                        session.commit()
                        _saved += 1
                    except _IntegrityError:
                        session.rollback()  # дубль уже есть в БД
                if _saved:
                    logger.info(f"[ANCHOR] User {user_id}: created {_saved}/{len(new_anchors)} anchors (race-condition dedup, {len(new_anchors)-_saved} skipped)")
            # 1b. EVENT DISPATCH — новые goal-якоря запускают нужных агентов в фоне
            # Сериализуем данные ДО закрытия сессии чтобы избежать DetachedInstanceError
            if not is_night:
                _anchor_dicts = [
                    {
                        'anchor_type': a.anchor_type,
                        'source': a.source,
                        'topic': a.topic,
                        'data': a.data or {},
                    }
                    for a in new_anchors
                ]
                asyncio.ensure_future(
                    self._dispatch_agents_for_new_anchors(user, _anchor_dicts)
                )

        # 2. EVALUATE — собрать доставляемые якоря
        deliverable = session.query(Anchor).filter(
            Anchor.user_id == user.id,
            Anchor.delivered_at.is_(None),
            Anchor.triggered_at.isnot(None),
        ).order_by(
            Anchor.priority.asc(),  # CRITICAL first (enum order)
            Anchor.created_at.asc()
        ).limit(20).all()

        # ── STUCK ANCHOR RECOVERY: если autopilot-якорь висит >15 мин без delivered_at ──
        _stuck_threshold = datetime.now(timezone.utc) - timedelta(minutes=15)
        _stuck_cleared = 0
        for _sa in deliverable:
            if _sa.anchor_type == 'goal_autopilot_review':
                _tr = _sa.triggered_at
                if _tr and (_tr.tzinfo is None and _tr < _stuck_threshold.replace(tzinfo=None)
                            or _tr.tzinfo is not None and _tr < _stuck_threshold):
                    _sa.delivered_at = datetime.now(timezone.utc)
                    _stuck_cleared += 1
        if _stuck_cleared:
            session.commit()
            deliverable = [a for a in deliverable if a.delivered_at is None]
            logger.warning("[ANCHOR] User %d: cleared %d stuck autopilot anchors (>15min)", user_id, _stuck_cleared)

        # ── 0. BACKGROUND RESEARCH — выполнить отложенные исследования ──
        _bg_now = datetime.utcnow()  # naive UTC для сравнения с triggered_at из PostgreSQL (naive)
        bg_due = [a for a in deliverable if a.anchor_type == 'background_research'
                  and (a.triggered_at is None or
                       (a.triggered_at.replace(tzinfo=None) if a.triggered_at.tzinfo else a.triggered_at) <= _bg_now)]
        if bg_due and not is_night:
            for bra in bg_due[:2]:
                async with self._ai_semaphore:
                    await self._process_background_research_anchor(user, bra, session)
        elif bg_due and is_night:
            logger.info(f"[ANCHOR] User {user_id}: ⛔ background research deferred (night hours)")
        # Исключаем background_research из потока доставки — они выполняются тихо
        deliverable = [a for a in deliverable if a.anchor_type != 'background_research']

        # ── AUTO-EXPIRE старых информационных якорей (>72ч) ──
        # Чтобы очередь не засорялась устаревшими уведомлениями
        _NON_EXPIRING_TYPES = ALWAYS_DELIVER_TYPES | {
            'goal_autopilot_review', 'custom_anchor', 'background_research',
            'email_outreach_send', 'email_follow_up', 'email_need_leads',
            'content_campaign_publish', 'delegation_campaign_send', 'delegation_campaign_follow_up',
        }
        _now_for_expire = datetime.now(timezone.utc)
        _max_anchor_age = timedelta(hours=72)
        _auto_expired_ids = []
        for _a in deliverable:
            if _a.anchor_type in _NON_EXPIRING_TYPES or _a.expires_at:
                continue
            _ca = _a.created_at
            if _ca:
                if _ca.tzinfo is None:
                    _ca = _ca.replace(tzinfo=timezone.utc)
                if _now_for_expire - _ca > _max_anchor_age:
                    _a.delivered_at = _now_for_expire
                    _auto_expired_ids.append(_a.id)
        if _auto_expired_ids:
            session.commit()
            deliverable = [a for a in deliverable if a.id not in _auto_expired_ids]
            logger.info(f"[ANCHOR] User {user_id}: 🧹 auto-expired {len(_auto_expired_ids)} stale anchors (>72h): ids={_auto_expired_ids}")

        logger.info(f"[ANCHOR] User {user_id}: найдено {len(deliverable)} deliverable якорей")

        # Фильтруем: не истёкшие + cooldown
        ready = [a for a in deliverable if a.is_deliverable()]
        if not ready:
            logger.info(f"[ANCHOR] User {user_id}: ⛔ после is_deliverable() — 0 ready (expired/suppressed)")
            return

        # ── STALENESS CHECK: задача/цель могла быть выполнена/удалена после создания якоря ──
        task_anchor_types = {'task_overdue', 'task_deadline_soon', 'task_stale', 'task_reminder', 'task_result_check'}
        goal_anchor_types = {'goal_stagnation', 'goal_progress', 'goal_deadline', 'goal_decomposition'}
        # Batch-load all referenced tasks (avoid N+1 per anchor)
        _stale_tids = []
        _stale_gids = []
        for _sa in ready:
            if _sa.anchor_type in task_anchor_types and _sa.source and _sa.source.startswith('task:'):
                try:
                    _stale_tids.append(int(_sa.source.split(':')[1]))
                except (ValueError, IndexError):
                    pass
            elif _sa.anchor_type in goal_anchor_types and _sa.source and _sa.source.startswith('goal:'):
                try:
                    _stale_gids.append(int(_sa.source.split(':')[1]))
                except (ValueError, IndexError):
                    pass
        _src_task_by_id = {t.id: t for t in session.query(Task).filter(Task.id.in_(_stale_tids)).all()} if _stale_tids else {}
        _src_goal_by_id = {g.id: g for g in session.query(Goal).filter(Goal.id.in_(_stale_gids)).all()} if _stale_gids else {}
        stale_ids = []
        for a in ready:
            if a.anchor_type in task_anchor_types and a.source and a.source.startswith('task:'):
                try:
                    tid = int(a.source.split(':')[1])
                except (ValueError, IndexError):
                    continue
                src_task = _src_task_by_id.get(tid)
                if not src_task or src_task.status in ('completed', 'deleted', 'cancelled'):
                    a.delivered_at = datetime.now(timezone.utc)  # auto-expire
                    stale_ids.append(a.id)
            elif a.anchor_type in goal_anchor_types and a.source and a.source.startswith('goal:'):
                try:
                    gid = int(a.source.split(':')[1])
                except (ValueError, IndexError):
                    continue
                src_goal = _src_goal_by_id.get(gid)
                if not src_goal or src_goal.status in ('completed', 'paused', 'cancelled', 'deleted'):
                    a.delivered_at = datetime.now(timezone.utc)
                    stale_ids.append(a.id)
        if stale_ids:
            session.commit()
            ready = [a for a in ready if a.id not in stale_ids]
            logger.info(f"[ANCHOR] User {user_id}: ♻️ auto-expired {len(stale_ids)} stale task anchors")
            if not ready:
                logger.info(f"[ANCHOR] User {user_id}: ⛔ все якоря были stale")
                return

        ready = self._apply_cooldowns(ready, user, session)
        if not ready:
            logger.info(f"[ANCHOR] User {user_id}: ⛔ после _apply_cooldowns — 0 ready")
            return

        # ── Проверяем user_rules: запрет на email ──
        _email_blocked_by_rule = False
        try:
            from ai_integration.memory import decrypt_data as _dec_rules_pu
            _mem_raw_pu = _dec_rules_pu(user.memory) if user.memory else '{}'
            _mem_dict_pu = json.loads(_mem_raw_pu) if _mem_raw_pu else {}
            _user_rules_pu = _mem_dict_pu.get('rules', [])
            _EMAIL_STOP_KW = ('не писать', 'не отправлять', 'не слать', 'стоп email',
                              'stop email', 'без email', 'без рассылк', 'запрет email',
                              'не рассыл', 'прекрати email', 'прекрати рассыл',
                              'отключить email', 'отключи email', 'не использовать email',
                              'не отправляй email', 'не отправляй письм',
                              'не пиши по email', 'не пиши email', 'не пиши по почте',
                              'не писать по email', 'не писать email', 'не писать по почте')
            for _r_pu in _user_rules_pu:
                _r_low = _r_pu.lower()
                if any(kw in _r_low for kw in _EMAIL_STOP_KW):
                    _email_blocked_by_rule = True
                    logger.info(f"[ANCHOR] User {user_id}: ⛔ email blocked by user rule: {_r_pu[:80]}")
                    break
        except Exception as _e_rules_pu:
            logger.debug("suppressed user_rules email check: %s", _e_rules_pu)

        # ── Разделяем потоки ──
        EMAIL_SILENT_TYPES = {'email_outreach_send', 'email_follow_up', 'email_need_leads'}
        CONTENT_SILENT_TYPES = {'content_campaign_publish'}
        DELEGATION_SILENT_TYPES = {'delegation_campaign_send', 'delegation_campaign_follow_up'}
        AUTOPILOT_SILENT_TYPES = {'goal_autopilot_review'}
        CUSTOM_AGENT_TYPES = {'custom_anchor'}  # агент пишет первым — dispatch с tools
        critical_anchors = [a for a in ready if a.anchor_type in ALWAYS_DELIVER_TYPES
                            or a.priority in (AnchorPriority.CRITICAL, AnchorPriority.HIGH)]
        post_anchors = [a for a in ready if a.anchor_type in ('post_opportunity', 'channel_post', 'discord_post')]
        email_silent_anchors = [a for a in ready if a.anchor_type in EMAIL_SILENT_TYPES]
        content_silent_anchors = [a for a in ready if a.anchor_type in CONTENT_SILENT_TYPES]
        delegation_silent_anchors = [a for a in ready if a.anchor_type in DELEGATION_SILENT_TYPES]
        autopilot_anchors = [a for a in ready if a.anchor_type in AUTOPILOT_SILENT_TYPES]
        custom_agent_anchors = [a for a in ready if a.anchor_type in CUSTOM_AGENT_TYPES]
        regular_anchors = [a for a in ready if a not in critical_anchors and a not in post_anchors and a not in email_silent_anchors and a not in content_silent_anchors and a not in delegation_silent_anchors and a not in autopilot_anchors and a not in custom_agent_anchors]

        logger.info(f"[ANCHOR] User {user_id}: ready={len(ready)} (critical={len(critical_anchors)}, regular={len(regular_anchors)}, posts={len(post_anchors)}, email_silent={len(email_silent_anchors)}, content_silent={len(content_silent_anchors)}, deleg_silent={len(delegation_silent_anchors)}, custom_agent={len(custom_agent_anchors)}) dialog_count={dialog_count} gap_ok={proactive_gap_ok}")

        # ── 3. ДОСТАВКА — системные якоря (ASI) и агентские ОТДЕЛЬНО ──
        # Разделение: task_reminder/task_overdue доставляются от ASI,
        # agent_inbox_reply/agent_task_blocked — от имени агента.
        # Это предотвращает смешение (напоминание приходит от Кристины вместо ASI).
        _AGENT_ATTRIBUTED_TYPES = {'agent_inbox_reply', 'agent_task_blocked', 'agent_office_update',
                                    'integration_alert', 'agent_delegation'}
        all_dialog_anchors = critical_anchors.copy()
        if not has_proactive_tokens:
            if regular_anchors:
                logger.info(f"[ANCHOR] User {user_id}: ⛔ regular blocked (insufficient tokens)")
        elif is_night:
            if regular_anchors:
                logger.info(f"[ANCHOR] User {user_id}: ⛔ regular blocked (night hours)")
        elif regular_anchors and dialog_count < MAX_DIALOG_PER_DAY and proactive_gap_ok and not active_dialog:
            # Дедупликация: подавляем dialog_followup если уже есть CRITICAL/HIGH task-якорь
            # (чтобы не было двух сообщений про одну и ту же задачу/тему)
            _has_task_critical = any(
                a.anchor_type in ('task_overdue', 'task_deadline_soon', 'task_reminder')
                for a in critical_anchors
            )
            _filtered_regular = []
            for _ra in regular_anchors:
                if _ra.anchor_type == 'dialog_followup' and _has_task_critical:
                    logger.info(f"[ANCHOR] User {user_id}: 🔇 dialog_followup suppressed (task critical anchor present)")
                    continue
                _filtered_regular.append(_ra)
            all_dialog_anchors.extend(_filtered_regular)
        elif regular_anchors:
            logger.info(f"[ANCHOR] User {user_id}: ⛔ regular blocked (dialog_count={dialog_count}/{MAX_DIALOG_PER_DAY}, gap_ok={proactive_gap_ok}, active_dialog={active_dialog})")

        # Разделяем на системные (от ASI) и агентские (от конкретного агента)
        system_dialog_anchors = [a for a in all_dialog_anchors if a.anchor_type not in _AGENT_ATTRIBUTED_TYPES]
        agent_dialog_anchors = [a for a in all_dialog_anchors if a.anchor_type in _AGENT_ATTRIBUTED_TYPES]

        # Доставка системных якорей (от ASI) — task_reminder, task_overdue и т.д.
        async def _deliver_batch(anchors_batch, label):
            if not anchors_batch or not (has_proactive_tokens or any(a.anchor_type in ALWAYS_DELIVER_TYPES for a in anchors_batch)):
                return
            anchor_types_str = ', '.join(set(a.anchor_type for a in anchors_batch))
            logger.info(f"[ANCHOR] User {user_id}: 🔥 AI deciding for {len(anchors_batch)} {label} anchors ({anchor_types_str})...")
            _t0 = time.monotonic()
            async with self._ai_semaphore:
                msg = await self._ai_decide_and_compose(user, anchors_batch, session)
            _elapsed = time.monotonic() - _t0
            if not msg:
                always = [a for a in anchors_batch if a.anchor_type in ALWAYS_DELIVER_TYPES]
                if always and _elapsed < 30:
                    logger.info(f"[ANCHOR] User {user_id}: AI skipped {label} but ALWAYS_DELIVER present — retrying")
                    async with self._ai_semaphore:
                        msg = await self._ai_decide_and_compose(user, always, session, force_deliver=True)
                elif always:
                    logger.warning(f"[ANCHOR] User {user_id}: AI timeout ({_elapsed:.1f}s) — skipping {label} retry")
            if msg:
                await self._deliver(user, anchors_batch, msg, session)
                logger.info(f"[ANCHOR] User {user_id}: ✅ Delivered {len(anchors_batch)} {label} anchors")
            else:
                # FALLBACK: если AI не справился, для ALWAYS_DELIVER генерируем шаблон
                always = [a for a in anchors_batch if a.anchor_type in ALWAYS_DELIVER_TYPES]
                if always:
                    fallback = self._compose_always_deliver_fallback(always, user)
                    if fallback:
                        logger.info(f"[ANCHOR] User {user_id}: ⚡ ALWAYS_DELIVER fallback for {[a.anchor_type for a in always]}")
                        await self._deliver(user, always, fallback, session)
                    else:
                        logger.warning(f"[ANCHOR] User {user_id}: AI decided SKIP for {label} anchors (ALWAYS_DELIVER fallback empty)")
                else:
                    logger.info(f"[ANCHOR] User {user_id}: AI decided SKIP for {label} anchors")

        await _deliver_batch(system_dialog_anchors, 'system')
        if agent_dialog_anchors:
            await asyncio.sleep(2)  # Пауза между системными и агентскими сообщениями
            await _deliver_batch(agent_dialog_anchors, 'agent')

        # ── 3b. GOAL AUTOPILOT — ПЕРВЫМ после dialog, до постов/email ──
        # Агенты работают 24/7 автономно, не зависят от has_proactive_tokens и is_night.
        # Gap check по DELIVERED autopilot-якорям — надёжнее чем Interaction content.
        # Cooldown на source уже блокирует, но этот guard — двойная защита.
        if autopilot_anchors:
            # ── GUARD: проверяем флаг прямо перед dispatch (мог быть выключен после создания якоря) ──
            _profile_recheck = session.query(UserProfile).filter_by(user_id=user.id).first()
            _autopilot_still_on = _profile_recheck and getattr(_profile_recheck, 'goal_autopilot_enabled', False)
            if not _autopilot_still_on:
                logger.info(f"[ANCHOR] User {user_id}: ⛔ autopilot anchors skipped — goal_autopilot_enabled=False (disabled after anchor was created)")
                # Помечаем как delivered чтобы не накапливались в БД
                for _ap in autopilot_anchors:
                    _ap.delivered_at = datetime.now(timezone.utc)
                session.commit()
            else:
                _ap_gap_ok = True
                try:
                    _last_ap_delivered = session.query(Anchor.delivered_at).filter(
                        Anchor.user_id == user.id,
                        Anchor.anchor_type == 'goal_autopilot_review',
                        Anchor.delivered_at.isnot(None),
                    ).order_by(Anchor.delivered_at.desc()).first()
                    if _last_ap_delivered:
                        _ap_time = _last_ap_delivered[0]
                        if _ap_time.tzinfo is None:
                            _ap_time = _ap_time.replace(tzinfo=timezone.utc)
                        _ap_gap = (datetime.now(timezone.utc) - _ap_time).total_seconds() / 60
                        if _ap_gap < MIN_AUTOPILOT_GAP_MINUTES:
                            _ap_gap_ok = False
                            logger.info(f"[ANCHOR] User {user_id}: ⛔ autopilot deferred (last delivered {_ap_gap:.0f}m ago, min={MIN_AUTOPILOT_GAP_MINUTES}m)")
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)

                if _ap_gap_ok:
                    logger.info(f"[ANCHOR] User {user_id}: 🎯 Processing goal autopilot review (night={is_night})...")
                    for _ap in autopilot_anchors[:1]:
                        async with self._ai_semaphore:
                            await self._dispatch_agent_for_anchor(user, _ap, session)

        # ── 3b2. CUSTOM AGENT ANCHORS — агент пишет первым с инструментами ──
        # custom_anchor создаёт якорь для конкретного агента (из UserAgent.custom_anchors).
        # Маршрутизируем через _dispatch_agent_for_anchor → агент получает tools.
        if custom_agent_anchors and has_proactive_tokens:
            for _ca in custom_agent_anchors[:1]:
                async with self._ai_semaphore:
                    await self._dispatch_agent_for_anchor(user, _ca, session)

        # ── 3c. FEED POSTS — отдельный лимит (не ночью, нужны токены) ──
        if not is_night and has_proactive_tokens:
            feed_posts = [a for a in post_anchors if a.anchor_type == 'post_opportunity']
            if feed_posts and post_count < MAX_FEED_PER_DAY:
                for pa in feed_posts[:1]:
                    async with self._ai_semaphore:
                        await self._process_post_anchor(user, pa, session)

            # ── 3d. CHANNEL POSTS — отдельный лимит ──
            channel_posts = [a for a in post_anchors if a.anchor_type == 'channel_post']
            if channel_posts and channel_count < MAX_CHANNEL_PER_DAY:
                for pa in channel_posts[:1]:
                    async with self._ai_semaphore:
                        await self._process_post_anchor(user, pa, session)

            # ── 3e. DISCORD POSTS — автономный постинг в Discord-канал ──
            discord_posts = [a for a in post_anchors if a.anchor_type == 'discord_post']
            if discord_posts and discord_count < MAX_CHANNEL_PER_DAY:
                for pa in discord_posts[:1]:
                    async with self._ai_semaphore:
                        await self._process_post_anchor(user, pa, session)
        elif post_anchors:
            logger.info(f"[ANCHOR] User {user_id}: ⛔ posts blocked (night hours)")

        # ── 3e. EMAIL SILENT — автономная отправка/follow-up (ВСЕГДА, без сообщений юзеру) ──
        # Email outreach/follow-up — тихие операции, не будят пользователя → работают 24/7
        if email_silent_anchors and _email_blocked_by_rule:
            logger.info(f"[ANCHOR] User {user_id}: ⛔ {len(email_silent_anchors)} email anchors BLOCKED by user rule (запрет email)")
            for _ea_blocked in email_silent_anchors:
                _ea_blocked.delivered_at = datetime.now(timezone.utc)
            session.commit()
            email_silent_anchors = []
        if email_silent_anchors:
            logger.info(f"[ANCHOR] User {user_id}: 📧 Processing {len(email_silent_anchors)} email silent anchors (night={is_night})...")
            for _ea_idx, ea in enumerate(email_silent_anchors[:5]):  # макс 5 за цикл
                if _ea_idx > 0:
                    await asyncio.sleep(5)  # Краткая задержка между email-якорями
                async with self._ai_semaphore:
                    await self._process_email_silent_anchor(user, ea, session)

        # ── 3f. CONTENT CAMPAIGNS — автономная публикация по расписанию (не ночью) ──
        if content_silent_anchors and not is_night:
            logger.info(f"[ANCHOR] User {user_id}: 📝 Processing {len(content_silent_anchors)} content campaign anchors...")
            for _cc_idx, cc in enumerate(content_silent_anchors[:2]):  # макс 2 за цикл
                if _cc_idx > 0:
                    await asyncio.sleep(3)
                async with self._ai_semaphore:
                    await self._process_content_campaign_anchor(user, cc, session)
        elif content_silent_anchors and is_night:
            logger.info(f"[ANCHOR] User {user_id}: ⛔ content campaigns blocked (night hours)")

        # ── 3g. DELEGATION CAMPAIGNS — автономное делегирование (не ночью) ──
        if delegation_silent_anchors and not is_night:
            logger.info(f"[ANCHOR] User {user_id}: 🤝 Processing {len(delegation_silent_anchors)} delegation campaign anchors...")
            for _dc_idx, dc in enumerate(delegation_silent_anchors[:3]):  # макс 3 за цикл
                if _dc_idx > 0:
                    await asyncio.sleep(5)
                async with self._ai_semaphore:
                    await self._process_delegation_campaign_anchor(user, dc, session)
        elif delegation_silent_anchors and is_night:
            logger.info(f"[ANCHOR] User {user_id}: ⛔ delegation campaigns blocked (night hours)")

    # ═══════════════════════════════════════════════════════
    # BACKGROUND RESEARCH — выполнение отложенных исследований
    # ═══════════════════════════════════════════════════════

    async def _process_background_research_anchor(self, user, anchor, session):
        """Выполняет фоновое исследование и создаёт background_research_ready якорь для доставки."""
        try:
            data = anchor.data or {}
            query = data.get('query', '')
            if not query:
                anchor.delivered_at = datetime.now(timezone.utc)
                session.commit()
                return

            logger.info(f"[ANCHOR] User {user.id}: 🔍 executing background research: '{query[:60]}'")

            # Выполняем исследование
            from ai_integration.handlers import research_topic
            result = await research_topic(query, depth='full', user_id=user.id, session=session)
            result_str = ''
            if isinstance(result, dict):
                result_str = result.get('summary', '') or result.get('result', '') or str(result)
            else:
                result_str = str(result) if result else ''
            result_str = result_str[:3000]

            now_utc = datetime.now(timezone.utc)

            # Помечаем исходный якорь выполненным
            anchor.delivered_at = now_utc

            # Создаём якорь для доставки результата пользователю
            reason = data.get('reason', '')
            ready_anchor = Anchor(
                user_id=user.id,
                anchor_type='background_research_ready',
                source=f'background_research:{anchor.id}',
                topic=query[:200],
                priority=AnchorPriority.HIGH,
                data={'query': query, 'result': result_str, 'reason': reason},
                triggered_at=now_utc,
            )
            session.add(ready_anchor)

            # Логируем в AgentActivityLog (отображается в дашборде → Активность)
            log_entry = AgentActivityLog(
                user_id=user.id,
                activity_type='background_research',
                title=query[:200],
                content=query,
                status='completed',
                result=result_str[:500],
            )
            session.add(log_entry)

            session.commit()
            logger.info(f"[ANCHOR] User {user.id}: ✅ background_research done → ready anchor queued for '{query[:50]}'")

        except Exception as e:
            logger.error(f"[ANCHOR] _process_background_research_anchor error: {e}")
            try:
                anchor.delivered_at = datetime.now(timezone.utc)
                session.commit()
            except Exception:
                session.rollback()

    # ═══════════════════════════════════════════════════════
    # EVENT-DRIVEN AGENT DISPATCH
    # ═══════════════════════════════════════════════════════

    async def _dispatch_agent_for_anchor(self, user, anchor, session):
        """Прямой dispatch: запускает AI-агента для конкретного якоря и помечает доставленным.
        
        Используется для автопилота целей: агент получает задачу, выполняет действия
        (создаёт задачи, отправляет письма, исследует), результат сохраняется в activity log.
        """
        try:
            from ai_integration.autonomous_agent import _exec_agent_for_director
            from models import UserAgent as _UA_ap, AgentActivityLog as _AAL_ap

            # ── Guard: предотвращаем дублирование при параллельных scan-циклах ──
            # Проверяем: нет ли ЛЮБОГО dispatch (любой target) в in_progress < 5 мин.
            # Один пользователь — один активный dispatch в момент времени.
            try:
                # Используем ORM вместо raw SQL для совместимости SQLite/PostgreSQL
                _guard_cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
                from models import AgentActivityLog as _AAL_guard
                _recent = session.query(_AAL_guard.id, _AAL_guard.target).filter(
                    _AAL_guard.user_id == user.id,
                    _AAL_guard.activity_type == 'goal_autopilot_dispatch',
                    _AAL_guard.status == 'in_progress',
                    _AAL_guard.created_at > _guard_cutoff,
                ).first()
                if _recent:
                    logger.info(
                        "[DISPATCH-GUARD] Skip dispatch for %s — already running AAL id=%s (target=%s)",
                        anchor.source, _recent[0], _recent[1]
                    )
                    return
            except Exception as _guard_err:
                logger.debug("[DISPATCH-GUARD] guard check failed (non-critical): %s", _guard_err)

            # Используем только агентов с AgentSubscription — те что пользователь активировал в чате
            from models import AgentSubscription as _AS_ap
            _sub_ids = {r.agent_id for r in session.query(_AS_ap).filter_by(user_id=user.id).all()}
            agents = session.query(_UA_ap).filter(
                _UA_ap.id.in_(_sub_ids) if _sub_ids else _UA_ap.author_id == user.id,
                _UA_ap.status != 'disabled',
            ).limit(10).all() if _sub_ids else []

            # Всегда добавляем синтетического ASI в пул — он участвует в ротации
            # наравне с кастомными агентами. id=0 → особая ветка в dispatch.
            from types import SimpleNamespace as _NS_ap
            # Динамически формируем personality ASI с именами реальных агентов
            _agent_names_for_asi = [getattr(a, 'name', '') for a in agents if getattr(a, 'id', 0) != 0]
            _delegate_examples = ''
            if _agent_names_for_asi:
                _delegate_examples = (
                    'Нашёл данные для коллеги → DELEGATE[Имя]: задача. '
                    f'Коллеги: {", ".join(_agent_names_for_asi)}. '
                )
            _asi_tools_list = [
                'web_search', 'research_topic', 'find_relevant_contacts_for_task',
                'save_email_contact', 'add_task', 'complete_task', 'edit_task',
                'delegate_task', 'send_outreach_email', 'send_email',
                'start_email_campaign', 'add_email_leads',
                'update_goal_progress', 'update_goal', 'create_goal',
                'quick_topic_search', 'get_news_trends',
            ]
            _asi_synth = _NS_ap(
                id=0, name='ASI',
                job_title='Координатор команды',
                specialization='goal_management',
                description='Координатор: исследует стратегию, создаёт задачи, делегирует по способностям.',
                personality=(
                    'Ты — координатор команды ASI Biont. '
                    'Ты используешь web_search, research_topic, find_relevant_contacts_for_task, '
                    'save_email_contact, start_email_campaign, add_email_leads, send_outreach_email, '
                    'add_task, delegate_task, update_goal_progress. '
                    'Для email-рассылок: сначала start_email_campaign (создаёт кампанию + находит лиды), '
                    'затем send_outreach_email. '
                    'Ты СНАЧАЛА делаешь сам (ищешь, создаёшь кампании, находишь контакты, отправляешь письма), '
                    'И делегируешь коллегам задачи по их специализации. '
                    + _delegate_examples +
                    'НИКОГДА не пишешь «предлагаю» — только действуешь инструментами.'
                ),
                python_code='', user_api_keys='',
                tools_allowed=json.dumps(_asi_tools_list),
                avatar_url='',
                tools=_asi_tools_list,
            )
            agents.append(_asi_synth)

            # Если нет пользовательских агентов — используем прямой AI-вызов через основной чат
            # Для goal_autopilot_review — используем полноценный prompt из _AGENT_DISPATCH_TRIGGERS,
            # а не просто anchor.topic (он слишком лаконичен для автономной работы агента)
            data = anchor.data or {}
            if isinstance(data, str):
                data = json.loads(data)
            if anchor.anchor_type == 'goal_autopilot_review':
                # Промпт будет перестроен ПОСЛЕ выбора агента с учётом его интеграций
                _goals_for_prompt = data.get('goals', []) if isinstance(data, dict) else []
                task_text = "[АВТОПИЛОТ ЦЕЛЕЙ]\n"  # placeholder — дополнится ниже
            elif anchor.anchor_type == 'custom_anchor':
                # custom_anchor: агент пишет первым — используем topic как задачу
                _ca_agent_name = data.get('agent_name', '')
                task_text = (
                    f"[АВТОПИЛОТ]\n"
                    f"Ты — {_ca_agent_name}. Тебе нужно ПРОАКТИВНО написать пользователю.\n"
                    f"Тема: {anchor.topic or 'проактивное сообщение'}\n\n"
                    "ИНСТРУКЦИЯ: Не пиши просто комментарий. СДЕЛАЙ что-то полезное:\n"
                    "  — Проверь входящие (check_emails), если у тебя есть email-интеграция\n"
                    "  — Найди информацию через web_search или research_topic\n"
                    "  — Создай задачу (add_task) для продвижения цели\n"
                    "  — Делегируй конкретную работу коллеге (DELEGATE[Имя]: задача)\n\n"
                    "Отчёт юзеру — только ФАКТЫ из действий (имена, ссылки, цифры).\n"
                )
            else:
                task_text = _AGENT_DISPATCH_TRIGGERS.get(
                    anchor.anchor_type, anchor.topic or '',
                )

            # ── КОНТЕКСТ ПОЛЬЗОВАТЕЛЯ — кто владелец, какой проект ──
            _user_profile = data.get('user_profile', {})
            if _user_profile and _user_profile.get('summary'):
                task_text = (
                    "👤 Контекст пользователя (работай на ЕГО проект, а не абстрактный):\n"
                    + _user_profile['summary']
                    + "\n\n"
                    + task_text
                )

            # ── ПРАВИЛА ПОЛЬЗОВАТЕЛЯ — идут первыми, задают контекст работы ──
            _user_rules = data.get('user_rules', [])
            if _user_rules:
                task_text = (
                    "📌 Правила и предпочтения пользователя (учитывай при принятии решений):\n"
                    + '\n'.join(f"  {i+1}. {r}" for i, r in enumerate(_user_rules))
                    + "\n\n"
                    + task_text
                )

            # Обогащаем задачу данными о целях
            goals_info = data.get('goals', [])
            if goals_info:
                goals_block = '\n'.join(
                    f"• {g['title']} ({g['progress']}%)"
                    + (f" [метрика: {g.get('metric_current', 0)}/{g.get('metric_target', '?')}]" if g.get('metric_target') else '')
                    + (f", дедлайн: {g['target_date']}" if g.get('target_date') else '')
                    + self._format_goal_tasks(g.get('tasks', []))
                    for g in goals_info
                )
                task_text += f"\n\nАктивные цели:\n{goals_block}"

            # Добавляем историю предыдущих действий — кратко, без провокации эхо
            recent_actions = data.get('recent_actions', [])
            if recent_actions:
                task_text += (
                    "\n\nУже сделано (для контекста, не повторяй):\n"
                    + '\n'.join(f"  {a}" for a in recent_actions[:8])
                )
            
            # Темы последних проактивных сообщений — НЕ ПОВТОРЯЙ
            recent_proactive_topics = data.get('recent_proactive_topics', [])
            if recent_proactive_topics:
                task_text += (
                    "\n\n❌ О ЧЁМ УЖЕ ПИСАЛ (не повторяй эти темы и факты):\n"
                    + '\n'.join(f"  • {t}" for t in recent_proactive_topics[:5])
                    + "\n🔴 ЗАПРЕЩЕНО повторять факты/цифры/ссылки из этого списка. Ищи НОВУЮ информацию."
                )

            # Добавляем информацию о команде с их способностями
            _team_profiles = data.get('team_profiles', [])
            _team_names = data.get('team_agents', [])
            if _team_profiles:
                _team_lines = []
                for tp in _team_profiles:
                    _caps = ', '.join(tp.get('capabilities', [])) or 'общие задачи'
                    _team_lines.append(f"  • {tp['name']} ({tp.get('job_title', '')}) — {_caps}")
                task_text += (
                    f"\n\nТВОЯ КОМАНДА (делегируй ПО СПОСОБНОСТЯМ, не всем сразу):\n"
                    + '\n'.join(_team_lines)
                    + "\nDELEGATE[Имя]: задача с конкретными данными. "
                    "Делегируй ТОЛЬКО если у коллеги есть нужная интеграция для этой задачи."
                )
            elif _team_names:
                task_text += (
                    f"\n\nТВОЯ КОМАНДА: {', '.join(_team_names)}\n"
                    "DELEGATE[Имя]: описание задачи."
                )

            # Добавляем недавние proactive-сообщения (что уже было сказано/сделано)
            recent_msgs = data.get('recent_messages', [])
            if recent_msgs:
                task_text += (
                    f"\n\nТвои прошлые сообщения юзеру (не дублируй):\n"
                    + '\n'.join(f"  {m}" for m in recent_msgs[:4])
                )

            # Добавляем статус email-кампаний
            email_info = data.get('email_campaigns', [])
            if email_info:
                task_text += f"\n\nEmail-кампании:\n" + '\n'.join(f"  {e}" for e in email_info)

            # Показываем общее число отправленных писем — информационно
            _total_sent = data.get('total_emails_sent', 0)
            if _total_sent and _total_sent > 0:
                task_text += (
                    f"\n\nOutreach-статистика: отправлено писем = {_total_sent}. "
                    f"Это охват (кому написали), а не подтверждённые пользователи. "
                    f"Метрику цели обновляй ТОЛЬКО если получен реальный ответ/подтверждение участия."
                )

            # Добавляем известные контакты
            known_contacts = data.get('known_contacts', [])
            if known_contacts:
                task_text += f"\n\nИзвестные контакты:\n" + '\n'.join(f"  {c}" for c in known_contacts[:8])

            # Уже отправленные письма — не дублировать
            _already_sent_str_ctx = data.get('already_sent_emails', [])
            if _already_sent_str_ctx:
                task_text += (
                    f"\n\n⚠️ Уже получили письма (НЕ писать повторно): "
                    + ', '.join(_already_sent_str_ctx[:20])
                )

            # Задачи уже созданные агентами — не создавай дублей, предлагай новые шаги
            agent_tasks_done = data.get('agent_tasks_history', [])
            if agent_tasks_done:
                task_text += (
                    f"\n\nЗадачи уже созданы за 24ч (не дублируй, думай о следующем шаге):\n"
                    + '\n'.join(f"  • {t}" for t in agent_tasks_done)
                )

            # Инструменты которые уже не работают — ЗАПРЕЩЕНО вызывать
            _failed_tl = data.get('failed_tools', {})
            if _failed_tl:
                _banned = ', '.join(f"{t} ({n}x)" for t, n in _failed_tl.items())
                task_text += (
                    f"\n\nЗаблокированные инструменты (не дали результата):\n"
                    f"  {_banned}\n"
                    f"  Выбери ДРУГОЙ подход."
                )

            # Статистика использования — для разнообразия подходов
            _tool_freq = data.get('tool_frequency', {})
            if _tool_freq:
                _freq_str = ', '.join(f"{t}: {n}x" for t, n in sorted(_tool_freq.items(), key=lambda x: -x[1])[:8])
                task_text += f"\n\nСтатистика инструментов за 48ч: {_freq_str}. Попробуй другой."

            # ── Исчерпанные стратегии — принудительная смена подхода ──
            _exhausted = data.get('exhausted_strategies', [])
            if _exhausted:
                _STRATEGY_LABELS = {
                    'search': 'Поиск (web_search, research_topic)',
                    'email': 'Email (send_outreach_email, start_email_campaign)',
                    'content': 'Контент (create_post, publish_to_telegram)',
                    'delegation': 'Делегирование (delegate_task)',
                }
                _exh_labels = [_STRATEGY_LABELS.get(s, s) for s in _exhausted]
                _STRATEGY_RECOVERY = {
                    'search': 'перейди к прямому контакту: find_relevant_contacts_for_task → send_outreach_email',
                    'email': 'переключись на соцсети: find_and_message_relevant_users / publish_to_telegram',
                    'content': 'контент создан → теперь привлекай людей: send_outreach_email / find_relevant_contacts_for_task',
                    'delegation': 'делегирование не помогло → действуй сам: send_outreach_email / run_agent_action',
                }
                task_text += (
                    f"\n\n🔴 ИСЧЕРПАННЫЕ СТРАТЕГИИ (>50% провалов — СМЕНИ МЕТОД):\n"
                    + '\n'.join(
                        f"  ✗ {_STRATEGY_LABELS.get(s, s)}: {_STRATEGY_RECOVERY.get(s, 'используй другую категорию')}"
                        for s in _exhausted
                    )
                )

            # ══ Блок отсутствующих интеграций: только если реально мешает текущей задаче ══
            import os as _os_intg
            _missing_intg_notes = []
            _real_agents_intg = [a for a in agents if getattr(a, 'id', 0) != 0]
            # 1. run_agent_action в tools_allowed но API-ключи не добавлены
            for _ag_chk in _real_agents_intg:
                if 'run_agent_action' in (_ag_chk.tools_allowed or '') and not (_ag_chk.user_api_keys or '').strip():
                    _missing_intg_notes.append(
                        f"⚠️ {_ag_chk.name}: внешние API-ключи не добавлены — "
                        f"расширенные интеграции недоступны. "
                        f"Сообщи пользователю: Дашборд → Настройки агента → API-ключи "
                        f"(Gmail, GitHub, Slack, Notion, Trello, HubSpot и др.)"
                    )
            # 2. Email-анкер но email-отправка не настроена на платформе
            _email_anchor_types = {'email_outreach_send', 'email_follow_up', 'email_need_leads'}
            if anchor.anchor_type in _email_anchor_types and not _os_intg.getenv('RESEND_API_KEY'):
                _missing_intg_notes.append(
                    "❌ Email-отправка через платформу не настроена администратором. "
                    "Используй email-агента с Gmail/Яндекс ключами, или сообщи пользователю об ограничении."
                )
            # 3. Поиск разработчиков без GitHub-интеграции
            _tech_kw_anchor = [
                'github', 'developer', 'разработчик', 'программист',
                'python', 'javascript', 'typescript', 'backend', 'frontend', 'fullstack',
                'ai ', 'ml ', 'data science', 'machine learning', 'open source',
            ]
            # Check agent keys (user_api_keys) AND system env for GitHub token
            _has_github_agent = any(
                any(k in (getattr(_ag_i, 'user_api_keys', '') or '').upper()
                    for k in ('GITHUB_TOKEN', 'GITHUB_ACCESS_TOKEN'))
                for _ag_i in _real_agents_intg
            )
            if (any(w in task_text.lower() for w in _tech_kw_anchor)
                    and not _has_github_agent and not _os_intg.getenv('GITHUB_TOKEN')):
                _missing_intg_notes.append(
                    "⚠️ GitHub-интеграция не настроена — поиск разработчиков ограничен. "
                    "Используй find_relevant_contacts_for_task или web_search. "
                    "Сообщи пользователю: Дашборд → Настройки агента → API-ключи → GitHub."
                )
            if _missing_intg_notes:
                task_text += (
                    "\n\nОТСУТСТВУЮТ ИНТЕГРАЦИИ (сообщи пользователю ТОЛЬКО если это блокирует задачу):\n"
                    + "\n".join(_missing_intg_notes)
                )

            # ── Предупреждения о достижимости целей ──
            _feasibility = data.get('feasibility_warnings', [])
            if _feasibility:
                task_text += (
                    "\n\nОценка достижимости:\n"
                    + '\n'.join(f"  {w}" for w in _feasibility)
                )

            if agents:
                # ── ROUND-ROBIN для goal_autopilot_review: агенты чередуются строго ──
                # AI-выбор всегда жмёт на ASI (specialization=goal_management) →
                # кастомные агенты (Кристина, Марк) выпадают из ротации.
                # Решение: для autopilot принудительно чередуем агентов в порядке их id,
                # используя последний dispatched-агент из AgentActivityLog как указатель.
                if anchor.anchor_type == 'custom_anchor':
                    # ── CUSTOM ANCHOR: force-select agent from anchor.data ──
                    _ca_target_id = data.get('agent_id')
                    if _ca_target_id:
                        chosen = next((a for a in agents if getattr(a, 'id', 0) == _ca_target_id), None)
                    if not _ca_target_id or not chosen:
                        chosen = agents[0] if agents else _asi_synth
                    _rr_debug = f'[CUSTOM] target_agent_id={_ca_target_id} chosen={chosen.name}'
                elif anchor.anchor_type == 'goal_autopilot_review':
                    # ── COORDINATOR MODE: 1+ реальных агентов → ASI строит план для команды ──
                    _coord_real = [a for a in agents if getattr(a, 'id', 0) != 0]
                    logger.info("[COORD] entry check: _coord_real=%d agent(s): %s",
                                len(_coord_real), [a.name for a in _coord_real])
                    if len(_coord_real) >= 1:
                        try:
                            # timeout = 120s per agent × number of agents + 40s overhead
                            _n_coord_agents = len(_coord_real)
                            _coord_timeout = max(220, _n_coord_agents * 120 + 40)
                            _coord_ok = await asyncio.wait_for(
                                self._run_coordinator_dispatch(
                                    user, data, _coord_real, task_text, anchor, session,
                                ),
                                timeout=_coord_timeout,
                            )
                            if _coord_ok:
                                return
                        except (asyncio.TimeoutError, Exception) as _coord_exc:
                            logger.warning("[COORD] failed/timeout, fallback to round-robin: %s", _coord_exc)
                    # ── FALLBACK / SINGLE-AGENT: оригинальный round-robin ──
                    _real_ags = sorted([a for a in agents if getattr(a, 'id', 0) != 0], key=lambda a: a.id)
                    _asi_ag = next((a for a in agents if getattr(a, 'id', 0) == 0), None)
                    _rotation_pool = _real_ags + ([_asi_ag] if _asi_ag else [])
                    if _rotation_pool:
                        from sqlalchemy import func as _rr_func
                        _rr_counts = {}
                        _rr_recent_fails = {}
                        try:
                            _rr_counts_raw = session.query(
                                _AAL_ap.ref_id, _rr_func.count(_AAL_ap.id).label('cnt')
                            ).filter(
                                _AAL_ap.user_id == user.id,
                                _AAL_ap.activity_type == 'goal_autopilot_dispatch',
                                _AAL_ap.created_at >= datetime.now(timezone.utc) - timedelta(hours=48),
                            ).group_by(_AAL_ap.ref_id).all()
                            _rr_counts = {(r if r is not None else 0): c for r, c in _rr_counts_raw}

                            for _ag_rr in _rotation_pool:
                                _ag_rr_id = getattr(_ag_rr, 'id', 0)
                                _ref_val = _ag_rr_id if _ag_rr_id != 0 else None
                                _recent = session.query(_AAL_ap.status).filter(
                                    _AAL_ap.user_id == user.id,
                                    _AAL_ap.activity_type == 'goal_autopilot_dispatch',
                                    _AAL_ap.ref_id == _ref_val if _ref_val is not None else _AAL_ap.ref_id.is_(None),
                                ).order_by(_AAL_ap.created_at.desc()).limit(5).all()
                                _consec = 0
                                for (_st,) in _recent:
                                    if _st == 'failed':
                                        _consec += 1
                                    else:
                                        break
                                if _consec >= 3:
                                    _rr_recent_fails[_ag_rr_id] = _consec
                        except Exception as _rr_err:
                            logger.warning("[ANCHOR-AUTOPILOT] round-robin query failed: %s", _rr_err)
                            try:
                                session.rollback()
                            except Exception:
                                pass

                        # ── Анализ текущих потребностей цели ──
                        _needs = set()
                        _task_lower = task_text.lower()
                        _exhausted_strats = data.get('exhausted_strategies', [])
                        # Определяем что НУЖНО сейчас по контексту задачи
                        if any(w in _task_lower for w in ('email', 'письм', 'outreach', 'контакт', 'рассылк')):
                            _needs.add('email')
                        if any(w in _task_lower for w in ('поиск', 'исследов', 'найти', 'search', 'analyz')):
                            _needs.add('search')
                        if any(w in _task_lower for w in ('rss', 'новост', 'хабр', 'feed', 'мониторинг')):
                            _needs.add('rss')
                        if any(w in _task_lower for w in ('github', 'код', 'разработ', 'developer')):
                            _needs.add('github')
                        if any(w in _task_lower for w in ('пост', 'контент', 'публик', 'telegram', 'discord')):
                            _needs.add('content')
                        if not _needs:
                            _needs.add('search')  # дефолт: нужен поиск

                        # ── Скоринг: способности агента × потребности goal ──
                        def _capability_score(a):
                            aid = getattr(a, 'id', 0)
                            _api = (getattr(a, 'user_api_keys', '') or '').lower()
                            _pc = (getattr(a, 'python_code', '') or '').lower()
                            score = 0
                            if aid == 0:
                                # ASI — координатор: базовый скор = 1 (поиск),
                                # чтобы не застревал в конце ротации навсегда.
                                # Специализированные агенты с интеграциями (score≥3) всё равно приоритетнее.
                                return 1
                            if 'email' in _needs:
                                if any(w in _api for w in ('gmail', 'imap', 'smtp', 'resend', 'mail')):
                                    score += 3
                            if 'rss' in _needs:
                                if any(w in _api for w in ('rss', 'feed', 'habr')) or 'feedparser' in _pc:
                                    score += 3
                            if 'github' in _needs:
                                if 'github' in _api or 'github' in _pc:
                                    score += 3
                            if 'content' in _needs:
                                if any(w in _api for w in ('telegram', 'discord', 'slack')):
                                    score += 2
                            if 'search' in _needs and _pc:
                                score += 1  # агенты с python_code могут делать доп. исследования
                            return score

                        # Вычисляем медианный id реальных агентов для ASI-tie_break
                        _real_ids = sorted(getattr(a2, 'id', 1) for a2 in _rotation_pool if getattr(a2, 'id', 0) != 0)
                        _asi_tie = _real_ids[len(_real_ids) // 2] if _real_ids else 1

                        def _rr_key(a):
                            aid = getattr(a, 'id', 0)
                            cnt = _rr_counts.get(aid, 0)
                            fail_penalty = _rr_recent_fails.get(aid, 0) * 50
                            cap_bonus = _capability_score(a) * 10  # capability даёт бонус
                            # ASI tie_break = медиана id реальных агентов в пуле (не 99999):
                            # ASI участвует в ротации наравне, но специализированные агенты
                            # получают приоритет через cap_bonus, а не через tie_break.
                            tie_break = aid if aid != 0 else _asi_tie
                            return (cnt + fail_penalty - cap_bonus, tie_break)
                        chosen = min(_rotation_pool, key=_rr_key)
                        # Debug: логируем состояние ротации в content
                        _rr_debug = (
                            f'[RR] pool={[(getattr(a,"id",0), getattr(a,"name","?")) for a in _rotation_pool]} '
                            f'counts={_rr_counts} fails={_rr_recent_fails} chosen={chosen.name}({getattr(chosen,"id",0)})'
                        )
                    else:
                        chosen = await self._pick_best_agent(agents, task_text, anchor.anchor_type)
                        _rr_debug = '[RR] empty pool → _pick_best_agent'
                else:
                    chosen = await self._pick_best_agent(agents, task_text, anchor.anchor_type)
                    _rr_debug = ''
                # Для autopilot-задачи снимаем ограничения tools_allowed:
                # агент должен использовать полный арсенал (research, email, campaigns и т.д.)
                # Если агент определил кастомный список — он актуален для диалога, но не для
                # автономной работы по целям пользователя.
                _is_autopilot_dispatch = (anchor.anchor_type == 'goal_autopilot_review')
                # Адаптивный toolset: сохраняем tools_allowed агента,
                # но помечаем автопилот через _autopilot_mode для расширения core tools
                _tools_for_dispatch = chosen.tools_allowed or ''
                agent_data = {
                    'id': chosen.id, 'name': chosen.name,
                    'job_title': chosen.job_title or '',
                    'specialization': chosen.specialization or '',
                    'description': chosen.description or '',
                    'personality': chosen.personality or '',
                    'python_code': chosen.python_code or '',
                    'user_api_keys': chosen.user_api_keys or '',
                    'tools_allowed': _tools_for_dispatch,
                    'tools': json.loads(_tools_for_dispatch or '[]'),
                    'avatar_url': _safe_avatar(getattr(chosen, 'avatar_url', ''), chosen.id),
                    'search_scope': getattr(chosen, 'search_scope', '') or '',
                    'knowledge_base': getattr(chosen, 'knowledge_base', '') or '',
                }
                agent_name = chosen.name

                # ── Адаптация задачи под роль агента: универсальный подход ──
                # Используем _parse_agent_integrations — она определяет реальные интеграции
                # из user_api_keys (имена ключей) + python_code (импорты) + tools_allowed.
                # Работает для любых 30+ интеграций без хардкода в anchor_engine.
                _detected = []
                if anchor.anchor_type == 'goal_autopilot_review':
                    try:
                        from ai_integration.autonomous_agent import _parse_agent_integrations as _pai
                        _detected = _pai(
                            getattr(chosen, 'user_api_keys', '') or '',
                            getattr(chosen, 'python_code', '') or '',
                            getattr(chosen, 'tools_allowed', '') or '',
                            getattr(chosen, 'search_scope', '') or '',
                        )
                    except Exception:
                        _detected = []
                    # Перестраиваем task_text — вставляем промпт после placeholder
                    _per_agent_hist = data.get('per_agent_history', {}).get(chosen.name, [])
                    _autopilot_prompt = _build_autopilot_prompt(
                        _goals_for_prompt, user=user,
                        agent_caps=_detected, agent_name=chosen.name,
                        team_profiles=_team_profiles,
                        agent_history=_per_agent_hist,
                    )
                    _placeholder = "[АВТОПИЛОТ ЦЕЛЕЙ]\n"
                    if _placeholder in task_text:
                        task_text = task_text.replace(_placeholder, _placeholder + _autopilot_prompt + "\n", 1)
                    else:
                        task_text = _autopilot_prompt + "\n\n" + task_text
                else:
                    pass  # handled above
                # ── custom_anchor: перестраиваем task_text под реальные интеграции агента ──
                if anchor.anchor_type == 'custom_anchor':
                    _api_keys = getattr(chosen, 'user_api_keys', '') or ''
                    _pc = getattr(chosen, 'python_code', '') or ''
                    _api_lower = _api_keys.lower()
                    _pc_lower = _pc.lower()

                    # Определяем главное действие по интеграциям конкретного агента
                    _primary_actions = []
                    # Почта — самая ценная: проверить входящие
                    _email_accounts = []
                    for _kl in _api_keys.splitlines():
                        _kl = _kl.strip()
                        if _kl.startswith('GMAIL_USER='):
                            _email_accounts.append((_kl.split('=',1)[1].strip(), 'Gmail'))
                        elif _kl.startswith('YANDEX_USER='):
                            _email_accounts.append((_kl.split('=',1)[1].strip(), 'Яндекс'))
                        elif _kl.startswith('MAILRU_USER='):
                            _email_accounts.append((_kl.split('=',1)[1].strip(), 'Mail.ru'))
                    if _email_accounts:
                        for _ea, _elabel in _email_accounts[:1]:
                            _primary_actions.append(
                                f"1. Проверь входящие через check_emails — аккаунт {_ea} ({_elabel}). "
                                "Если есть ответы — сообщи кто написал и о чём."
                            )
                        _primary_actions.append(
                            "2. Если нужно — используй send_outreach_email или reply_to_outreach_email."
                        )
                    # RSS/новости
                    if any(w in _api_lower for w in ('rss_url=', 'feed_url=')) or 'feedparser' in _pc_lower:
                        _rss_url = next((l.split('=',1)[1].strip() for l in _api_keys.splitlines() if l.strip().upper().startswith('RSS_URL=')), '')
                        _primary_actions.append(
                            f"1. Загрузи новости через run_agent_action (RSS{': ' + _rss_url[:60] if _rss_url else ''}). "
                            "Найди релевантные статьи и создай задачу или делегируй Кристине."
                        )
                    # GitHub
                    if 'github_token=' in _api_lower:
                        _primary_actions.append(
                            "1. Используй run_agent_action для GitHub API — ищи разработчиков или issues."
                        )

                    if _primary_actions:
                        _actions_text = '\n'.join(_primary_actions)
                        task_text = (
                            f"[АВТОПИЛОТ]\n"
                            f"Ты — {chosen.name}, {chosen.job_title or chosen.specialization or 'специалист'}.\n"
                            f"Тема: {anchor.topic or 'проактивное сообщение'}\n\n"
                            f"ДЕЙСТВИЯ (выполни прямо сейчас, не планируй — делай):\n{_actions_text}\n\n"
                            "⚠️ ОБЯЗАТЕЛЬНО вызови инструмент и напиши результат по ФАКТУ.\n"
                            "❌ НЕ пиши 'планирую', 'собираюсь', 'буду' — только факты что уже сделано.\n"
                            "❌ НЕ создавай задачи пользователю без явной необходимости.\n"
                            "Отчёт пользователю — только ФАКТЫ: что нашёл, кто написал, что узнал.\n"
                        )
                    # Иначе оставляем task_text как был

                # ── Проверяем токены за автопилот (минимум agent_task=15) ──
                from token_service import has_enough_tokens as _het_ap, spend_tokens as _sp_ap
                from config import FREE_ACCESS_MODE as _FAM_ap
                if not _FAM_ap:
                    if not _het_ap(user.telegram_id, 'agent_task', session=session):
                        logger.info("[ANCHOR-AUTOPILOT] user %d: skip — not enough tokens", user.id)
                        anchor.delivered_at = datetime.now(timezone.utc)
                        session.commit()
                        return
                # Биллинг производится ПОСЛЕ AI-вызова (динамически по факту токенов)

                # Помечаем якорь доставленным ДО AI-вызова — защита от перезапуска Railway
                anchor.delivered_at = datetime.now(timezone.utc)
                try:
                    session.commit()
                except Exception as _commit_err:
                    logger.warning("[ANCHOR-AUTOPILOT] commit anchor.delivered_at failed: %s", _commit_err)
                    try:
                        session.rollback()
                    except Exception:
                        pass
                    # Retry without the log entry
                    anchor.delivered_at = datetime.now(timezone.utc)
                    try:
                        session.commit()
                    except Exception:
                        try:
                            session.rollback()
                        except Exception:
                            pass
                        # Не прерываем — даже без commit продолжаем диспатч агента
                        logger.warning("[ANCHOR-AUTOPILOT] delivered_at commit failed twice — continuing dispatch anyway")

                # Log dispatch — используем raw SQL через отдельное соединение
                # ORM-вставка через shared session ненадёжна (session state после token spend)
                _log_content = (_rr_debug + '\n' + task_text)[:500] if _rr_debug else task_text[:500]
                _aal_id = None
                try:
                    from sqlalchemy import text as _aal_text
                    _aal_ref = chosen.id if chosen.id != 0 else None
                    _aal_res = session.execute(_aal_text(
                        "INSERT INTO agent_activity_log (user_id, activity_type, title, content, target, status, ref_id, created_at) "
                        "VALUES (:uid, 'goal_autopilot_dispatch', :title, :content, :target, 'in_progress', :ref_id, NOW()) "
                        "RETURNING id"
                    ), {'uid': user.id, 'title': f'{agent_name} — обзор целей', 'content': _log_content[:500], 'target': anchor.source, 'ref_id': _aal_ref})
                    _aal_row = _aal_res.fetchone()
                    _aal_id = _aal_row[0] if _aal_row else None
                    session.commit()
                    logger.info("[ANCHOR-AUTOPILOT] AAL created id=%s for user %d", _aal_id, user.id)
                except Exception as _log_err:
                    logger.warning("[ANCHOR-AUTOPILOT] AAL creation failed (SQL): %s", _log_err)
                    try:
                        session.rollback()
                    except Exception:
                        pass
                    # Fallback: отдельная сессия
                    try:
                        from models import Session as _AAL_Session
                        _aal_s = _AAL_Session()
                        _aal_s.add(_AAL_ap(
                            user_id=user.id,
                            activity_type='goal_autopilot_dispatch',
                            title=f'{agent_name} — обзор целей',
                            content='',
                            target=anchor.source,
                            status='in_progress',
                        ))
                        _aal_s.commit()
                        _aal_id = _aal_s.query(_AAL_ap).filter_by(user_id=user.id, activity_type='goal_autopilot_dispatch').order_by(_AAL_ap.id.desc()).first()
                        _aal_id = _aal_id.id if _aal_id else None
                        _aal_s.close()
                    except Exception as _fb_err:
                        logger.error("[ANCHOR-AUTOPILOT] AAL fallback creation also failed: %s", _fb_err)
                        try:
                            _aal_s.close()
                        except Exception:
                            pass

                # ── Берём id/name/avatar из agent_data (сформирован до любых commit-ов) ──
                _chosen_id = agent_data.get('id', 0)
                _chosen_name = agent_data.get('name', '') or agent_name
                _chosen_avatar = agent_data.get('avatar_url', '')

                # ── Координатор назначает задачу ПЕРЕД биллингом (всегда видно в чате) ──
                # ИИ генерирует живое поручение — адаптируется под любого агента и его интеграции.
                if anchor.anchor_type == 'goal_autopilot_review' and _chosen_id != 0:
                    _gl_titles = [g.get('title', '')[:50] for g in data.get('goals', [])[:3]]
                    _brief_task = ', '.join(_gl_titles) if _gl_titles else (anchor.topic or 'цели')[:60]
                    _intg_list = ', '.join(str(d).split('(')[0].strip() for d in _detected[:4]) if _detected else ''
                    _agent_role = agent_data.get('job_title') or agent_data.get('specialization') or ''
                    # Контекст пользователя для живого поручения
                    _user_prof_c = data.get('user_profile', {})
                    _project_c = (_user_prof_c.get('company') or '').strip()
                    _goals_progress_c = ', '.join(
                        f"«{g.get('title','')[:30]}» {g.get('progress', 0)}%"
                        for g in data.get('goals', [])[:2]
                    ) if data.get('goals') else ''
                    # Fallback без шаблонных скобок и «Жду отчёт»
                    _coord_text = f"{_chosen_name}, займись текущими задачами."
                    try:
                        from ai_integration.autonomous_agent import _quick_ai_call_raw as _qar_coord
                        _coord_prompt = (
                            f"Ты — ASI, координатор команды"
                            + (f" проекта «{_project_c}»" if _project_c else '')
                            + f". Пишешь коллеге {_chosen_name} как живой человек — коротко и по делу.\n\n"
                            f"Агент: {_chosen_name}" + (f" ({_agent_role})" if _agent_role else '') + "\n"
                            + (f"Роль: {_agent_role}\n" if _agent_role else '')
                            + (f"Прогресс целей: {_goals_progress_c}\n" if _goals_progress_c else '')
                            + (f"Активные направления: {_brief_task}\n" if _brief_task else '')
                            + "\nНапиши 1 предложение как скажешь живому коллеге в рабочем чате. "
                            "Обращение по имени, конкретно что делать прямо сейчас, без квадратных скобок, "
                            "без 'Жду отчёт', без формальностей. "
                            "Примеры: «Кристина, загляни в почту — там могут быть ответы.» "
                            "«Марк, поищи свежую аналитику.» "
                            "⛔ НЕ упоминай названия инструментов (web_search, send_email и т.д.)."
                        )
                        _gen = await _qar_coord([{'role': 'user', 'content': _coord_prompt}], max_tokens=80)
                        if _gen and len(_gen.strip()) > 15:
                            _coord_text = _gen.strip()
                    except Exception as _cgen_err:
                        logger.debug("[ANCHOR-AUTOPILOT] coord msg gen failed: %s", _cgen_err)
                    try:
                        _cs = Session()
                        try:
                            # ── DEDUP: не отправляем если очень похоже на последние coord-сообщения ──
                            _skip_coord = False
                            try:
                                _recent_coords = _cs.query(Interaction).filter(
                                    Interaction.user_id == user.id,
                                    Interaction.message_type == 'agent_msg',
                                    Interaction.created_at >= datetime.now(timezone.utc) - timedelta(hours=3),
                                ).order_by(Interaction.created_at.desc()).limit(5).all()
                                for _rc in _recent_coords:
                                    try:
                                        _rc_d = json.loads(_rc.content or '{}')
                                        if _rc_d.get('__anchor_type') == 'goal_autopilot_assignment':
                                            _rc_words = set((_rc_d.get('text', '') or '').lower().split())
                                            _new_words = set((_coord_text or '').lower().split())
                                            if _rc_words and _new_words:
                                                _overlap = len(_rc_words & _new_words) / max(len(_rc_words | _new_words), 1)
                                                if _overlap > 0.65:
                                                    _skip_coord = True
                                                    logger.info("[ANCHOR-AUTOPILOT] coord-assign DEDUP: %.0f%% overlap with recent, skip sending", _overlap * 100)
                                                    break
                                    except Exception as _e:
                                        logger.debug("suppressed: %s", _e)
                            except Exception as _dc_err:
                                logger.debug("[ANCHOR-AUTOPILOT] coord dedup check failed: %s", _dc_err)

                            if not _skip_coord:
                                # Coordinator assignment больше не сохраняется в хронологию
                                # from ai_integration.utils import clean_technical_details as _ctd_coord_save
                                # _coord_text_clean_save = _ctd_coord_save(_coord_text) if _coord_text else _coord_text
                                # _coord_content = json.dumps({
                                #     '__agent': {'name': 'ASI', 'id': 0, 'avatar_url': ''},
                                #     'text': _coord_text_clean_save,
                                #     '__to_agent': _chosen_name,
                                #     '__anchor_type': 'goal_autopilot_assignment',
                                # }, ensure_ascii=False)
                                # _cs.add(Interaction(
                                #     user_id=user.id,
                                #     message_type='agent_msg',
                                #     content=_coord_content,
                                # ))
                                # _cs.commit()
                                # logger.info("[ANCHOR-AUTOPILOT] coord-assign saved user %d → %s", user.id, _chosen_name)
                                pass
                        finally:
                            _cs.close()
                        if not _skip_coord and self.bot:
                            try:
                                from ai_integration.utils import clean_technical_details as _ctd_coord
                                _coord_text_clean = _ctd_coord(_coord_text) if _coord_text else _coord_text
                                await self.bot.send_message(
                                    chat_id=user.telegram_id,
                                    text=_coord_text_clean or _coord_text,
                                )
                            except Exception as _e:
                                logger.debug("suppressed: %s", _e)
                    except Exception as _cas_err:
                        logger.warning("[ANCHOR-AUTOPILOT] coord-assign failed: %s", _cas_err)

                elif anchor.anchor_type == 'goal_autopilot_review' and _chosen_id == 0 and self.bot:
                    # ASI сама выполняет анализ — объявляет что начинает работу
                    try:
                        _asi_gl = [g.get('title', '')[:50] for g in data.get('goals', [])[:2]]
                        _asi_ann = f"Анализирую цели: {', '.join(_asi_gl)}. Подбираю следующий шаг."
                        await self.bot.send_message(chat_id=user.telegram_id, text=_asi_ann)
                        # ASI self-analysis больше не сохраняется в хронологию
                        # session.add(Interaction(
                        #     user_id=user.id,
                        #     message_type='agent_msg',
                        #     content=json.dumps({
                        #         '__agent': {'name': 'ASI', 'id': 0, 'avatar_url': ''},
                        #         'text': _asi_ann,
                        #         '__anchor_type': 'asi_self_analysis',
                        #     }, ensure_ascii=False),
                        # ))
                        # session.commit()
                    except Exception as _asi_ann_err:
                        logger.debug("[ANCHOR-AUTOPILOT] ASI self-announce failed: %s", _asi_ann_err)
                        try:
                            session.rollback()
                        except Exception:
                            pass

                # ── Биллинг кастомного агента (роялти автору) ──
                if _chosen_id != 0:
                    from ai_integration.user_agents import bill_agent_message as _bam_ap
                    _bill = _bam_ap(user.telegram_id, _chosen_id, session=session)
                    if not _bill.get('success'):
                        logger.info("[ANCHOR-AUTOPILOT] user %d: skip — agent billing failed: %s", user.id, _bill.get('error', ''))
                        return

                # ── Создаём задачу в «Поручения агентам» перед dispatch ──
                # Dedup: не создаём новую задачу если агент уже получил задачу за последние 4 ч
                _ap_task_id = None
                try:
                    from ai_integration.autonomous_agent import _create_agent_delegation_task as _cadt
                    # Составляем осмысленный заголовок из цели + специализации агента
                    _gl_titles_s = [g.get('title', '')[:60] for g in data.get('goals', [])[:2] if g.get('title', '').strip()]
                    if not _gl_titles_s:
                        try:
                            from models import Goal as _Goal_ap
                            _db_goals = session.query(_Goal_ap).filter(
                                _Goal_ap.user_id == user.id,
                                _Goal_ap.status == 'active',
                            ).order_by(_Goal_ap.created_at.desc()).limit(2).all()
                            _gl_titles_s = [g.title[:60] for g in _db_goals if g.title and g.title.strip()]
                        except Exception as _e:
                            logger.debug("suppressed: %s", _e)
                    # Формируем task_text для передачи в _cadt (нормализатор сделает title)
                    _agent_spec = (agent_data.get('specialization') or agent_data.get('job_title') or '').strip()[:60]
                    if _gl_titles_s:
                        _ap_task_text = _gl_titles_s[0]
                    elif _agent_spec:
                        _ap_task_text = _agent_spec
                    else:
                        _ap_task_text = f"Автопилот: задача {agent_name}"
                    # Dedup: пропускаем создание задачи если в последние 4ч уже была задача этого агента
                    _skip_ap_task = False
                    try:
                        from models import Task as _Task_ap
                        import datetime as _dt_ap
                        _ap_cutoff = _dt_ap.datetime.now(_dt_ap.timezone.utc) - _dt_ap.timedelta(hours=4)
                        _ap_recent = session.query(_Task_ap).filter(
                            _Task_ap.user_id == user.id,
                            _Task_ap.source == 'agent',
                            _Task_ap.created_by_agent_id == agent_data.get('id'),
                            _Task_ap.created_at >= _ap_cutoff,
                        ).first()
                        if _ap_recent:
                            _skip_ap_task = True
                            logger.debug("[ANCHOR-AUTOPILOT] dedup: skip task for agent %s, recent task id=%s", agent_name, _ap_recent.id)
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)
                    if not _skip_ap_task:
                        _ap_task_id = _cadt(user.id, agent_data, _ap_task_text)
                except Exception as _cadt_err:
                    logger.debug("[ANCHOR-AUTOPILOT] delegation task create skipped: %s", _cadt_err)

                try:
                    # Пауза перед AI-вызовом — небольшая задержка после объявления координатора
                    await asyncio.sleep(2)
                    # Ограничиваем task_text для экономии input-токенов DeepSeek
                    _task_trimmed = task_text[:2000] if len(task_text) > 2000 else task_text
                    _raw = await asyncio.wait_for(
                        _exec_agent_for_director(
                            agent_data, _task_trimmed, user.telegram_id,
                        ),
                        timeout=150,
                    )
                except (asyncio.TimeoutError, Exception) as _ai_err:
                    logger.warning("[ANCHOR-AUTOPILOT] AI call failed for user %d: %s", user.id, _ai_err)
                    # Вместо полной тишины — отправляем краткий статус-отчёт
                    _goals_summary = data.get('goals', [])
                    if _goals_summary and self.bot:
                        _goal_lines = ', '.join(g.get('title', '')[:50] for g in _goals_summary[:3])
                        _fallback_msg = f"Работаю над целями: {_goal_lines}. Анализирую возможные шаги."
                        try:
                            await self.bot.send_message(chat_id=user.telegram_id, text=_fallback_msg)
                            session.add(Interaction(
                                user_id=user.id,
                                message_type='proactive',
                                content=json.dumps({
                                    '__agent': {'name': agent_name, 'id': _chosen_id, 'avatar_url': _chosen_avatar},
                                    'text': _fallback_msg,
                                    '__anchor_type': 'goal_autopilot_review',
                                }, ensure_ascii=False),
                            ))
                            session.commit()
                        except Exception:
                            try:
                                session.rollback()
                            except Exception:
                                pass
                    _raw = ('', [])
                result = _raw[0] if isinstance(_raw, (tuple, list)) else _raw
                _tools_used = list(_raw[1]) if isinstance(_raw, (tuple, list)) and len(_raw) > 1 else []
                _cycle_tokens = int(_raw[2]) if isinstance(_raw, (tuple, list)) and len(_raw) > 2 else 0

                # Динамический биллинг: списываем по фактическому расходу API
                # 1 платформенный токен ≈ 1000 DeepSeek-токенов, мин=3, макс=20
                if not _FAM_ap and (_cycle_tokens > 0 or (result or '').strip()):
                    _dynamic_cost = max(3, min(50, _cycle_tokens // 250)) if _cycle_tokens else 5
                    _sp_ap(user.telegram_id, 'proactive_message', description=f'autopilot_dynamic:{_cycle_tokens}tok', cost=_dynamic_cost)
                    logger.info("[ANCHOR-AUTOPILOT] billed user %d: %d tokens (%d DeepSeek-tok)", user.id, _dynamic_cost, _cycle_tokens)

                # ── Обновляем задачу в «Поручения агентам» результатом ──
                if _ap_task_id and (result or '').strip():
                    try:
                        from ai_integration.autonomous_agent import _update_agent_delegation_task as _uadt
                        _uadt(_ap_task_id, (result or '')[:1000])
                    except Exception as _uadt_err:
                        logger.debug("[ANCHOR-AUTOPILOT] delegation task update skipped: %s", _uadt_err)

                # ── Отправляем РЕЗУЛЬТАТ работы агента пользователю ──
                _result_clean = (result or '').strip()
                # Нормализуем для echo-проверки: убираем markdown bold/italic, эмодзи, пробелы
                _result_normalized = re.sub(r'^\s*(?:[^\w\s]|\*{1,2}|_{1,2})+\s*', '', _result_clean)
                _result_lower = _result_normalized.lower()
                # Динамический список всех агентов для фильтрации утечек делегаций
                _all_agent_names = [a.name for a in agents if getattr(a, 'id', 0) != 0] + ['ASI']
                # Если агент реально вызвал инструменты — результат значимый
                _has_real_actions = bool(_tools_used)
                _filter_reason = ''

                # ── Watchdog: если >3ч без успешной доставки autopilot → пропустить noise-фильтр ──
                _force_delivery = False
                try:
                    _last_ap_dlv = session.query(Anchor.delivered_at).filter(
                        Anchor.user_id == user.id,
                        Anchor.anchor_type == 'goal_autopilot_review',
                        Anchor.delivered_at.isnot(None),
                    ).order_by(Anchor.delivered_at.desc()).first()
                    if _last_ap_dlv:
                        _ap_age_h = (datetime.now(timezone.utc) - _last_ap_dlv[0].replace(tzinfo=timezone.utc)).total_seconds() / 3600
                        if _ap_age_h > 3:
                            _force_delivery = True
                            logger.info("[ANCHOR-AUTOPILOT] watchdog: last autopilot delivered %.1fh ago → force delivery", _ap_age_h)
                    else:
                        _force_delivery = True  # Ни одной доставки — первый раз
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)

                # Контекстный noise-фильтр: не блокируем по префиксам,
                # а оцениваем реальную ценность ответа
                _EMPTY_RESPONSES = {
                    'задачу выполнил', 'задачу выполнила', 'данных нет',
                    'задача выполнена', 'понял задачу', 'принял в работу',
                    'задачу принял', 'задачу приняла',
                }
                # Шаблонные ответы, которые noise даже если инструменты были вызваны
                _GENERIC_TOOL_PATTERNS = (
                    'выполнил поиск', 'выполнила поиск',
                    'обновил прогресс', 'обновила прогресс',
                    'провёл поиск', 'провела поиск',
                    'запустил поиск', 'запустила поиск',
                    'проверил данные', 'проверила данные',
                    'выполнено', 'поиск завершён',
                )
                _is_echo = False  # промпт теперь учит думать правильно
                _is_noise_result = (
                    # Шум: нет инструментов + пустой/шаблонный ответ
                    not _has_real_actions and (
                        len(_result_clean) < 15
                        or _result_lower.rstrip('.!') in _EMPTY_RESPONSES
                    )
                    # Шум: ответ содержит ТОЛЬКО техническую ошибку без полезной информации
                    or (not _has_real_actions and len(_result_clean) < 80
                        and any(w in _result_lower for w in ('duckduckgo не', 'сервис недоступ', 'веб-поиск временно', 'ошибка подключения')))
                    # Шум: инструменты вызваны, но текст короткий и шаблонный (нет фактов)
                    or (_has_real_actions and len(_result_clean) < 100
                        and any(p in _result_lower for p in _GENERIC_TOOL_PATTERNS))
                    # Утечки делегаций: ответ начинается с обращения к другому агенту
                    or any(_result_lower.startswith(n.lower() + ',') for n in _all_agent_names)
                )
                if _is_noise_result:
                    _filter_reason = 'noise'

                # ── Dedup: не отправлять если ПОЧТИ ИДЕНТИЧНОЕ сообщение было недавно ──
                # Пропускаем dedup если агент реально вызвал инструменты — результат ценен
                if not _is_noise_result and _result_clean and not _has_real_actions:
                    try:
                        _recent_proactives = session.query(Interaction.content).filter(
                            Interaction.user_id == user.id,
                            Interaction.message_type.in_(['proactive', 'agent_msg']),
                            Interaction.created_at >= datetime.now(timezone.utc) - timedelta(hours=2),
                        ).order_by(Interaction.created_at.desc()).limit(6).all()
                        _new_words = set(_result_clean.lower().split())
                        for (_rp_content,) in _recent_proactives:
                            try:
                                _rp_text = json.loads(_rp_content).get('text', '')
                            except Exception:
                                _rp_text = _rp_content or ''
                            if not _rp_text:
                                continue
                            _old_words = set(_rp_text.lower().split())
                            # Dedup: >60% совпадение слов (антиэхо)
                            _common = len(_new_words & _old_words)
                            _total = max(len(_new_words | _old_words), 1)
                            if _common / _total > 0.60:
                                _is_noise_result = True
                                _filter_reason = 'dedup'
                                logger.info("[ANCHOR-AUTOPILOT] dedup: %.0f%% overlap with recent msg from %s",
                                            _common / _total * 100, agent_name)
                                break
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)

                # ── Dedup для tool-based результатов: RSS/новости не должны повторяться ──
                # Даже если агент использовал инструменты, один и тот же материал отсылается многократно.
                # Проверяем по порогу 65% за последние 4 часа.
                if not _is_noise_result and _result_clean and _has_real_actions:
                    try:
                        _recent_tool_msgs = session.query(Interaction.content).filter(
                            Interaction.user_id == user.id,
                            Interaction.message_type == 'agent_msg',
                            Interaction.created_at >= datetime.now(timezone.utc) - timedelta(hours=4),
                        ).order_by(Interaction.created_at.desc()).limit(8).all()
                        _new_words_t = set(_result_clean.lower().split())
                        for (_rt_content,) in _recent_tool_msgs:
                            try:
                                _rt_j = json.loads(_rt_content or '{}')
                                _rt_text = _rt_j.get('text', '') or ''
                            except Exception:
                                _rt_text = ''
                            if not _rt_text or len(_rt_text) < 30:
                                continue
                            _old_words_t = set(_rt_text.lower().split())
                            _common_t = len(_new_words_t & _old_words_t)
                            _total_t = max(len(_new_words_t | _old_words_t), 1)
                            if _common_t / _total_t > 0.65:
                                _is_noise_result = True
                                _filter_reason = 'dedup_tools'
                                logger.info("[ANCHOR-AUTOPILOT] tools dedup: %.0f%% overlap with recent tool msg from %s",
                                            _common_t / _total_t * 100, agent_name)
                                break
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)

                # ── Watchdog: если >3ч без AP-сообщения — форсировать доставку ──
                # Работает ПОСЛЕ noise + dedup фильтров — обходит оба
                if _is_noise_result and _force_delivery and len(_result_clean) > 50:
                    # Watchdog НЕ обходит echo-фильтр — пересказы коллег бесполезны даже через 3ч
                    if _filter_reason != 'noise' or not _is_echo:
                        _is_noise_result = False
                        logger.info("[ANCHOR-AUTOPILOT] watchdog override (%s): forcing delivery for %s (%d chars)",
                                    _filter_reason, agent_name, len(_result_clean))
                        _filter_reason = ''
                    else:
                        logger.info("[ANCHOR-AUTOPILOT] watchdog BLOCKED echo delivery for %s", agent_name)

                # ── ESCALATION: ASI обращается к пользователю если агенты застряли ──
                _fw_esc = data.get('feasibility_warnings', [])
                _es_esc = data.get('exhausted_strategies', [])
                _tf_esc = data.get('tool_frequency', {})
                _tot_disp = sum(_tf_esc.values())
                _blocker_in_result = bool(result and 'БЛОКЕР:' in result.upper())
                _stag_warn = next((w for w in _fw_esc if 'СТАГНАЦИЯ' in w.upper()), '')
                _cap_warns = [w for w in _fw_esc if '⚠️' in w or '⚡' in w]
                if self.bot and (_stag_warn or _blocker_in_result) \
                        and (_is_noise_result or _blocker_in_result):
                    try:
                        _esc_recent = session.query(Interaction).filter(
                            Interaction.user_id == user.id,
                            Interaction.message_type == 'proactive',
                            Interaction.created_at >= datetime.now(timezone.utc) - timedelta(hours=3),
                        ).all()
                        _esc_sent = any('autopilot_escalation' in (i.content or '') for i in _esc_recent)
                        if not _esc_sent:
                            _esc_lines = []
                            if _blocker_in_result and result:
                                _bl_line = next((ln for ln in result.splitlines() if 'БЛОКЕР:' in ln.upper()), '')
                                if _bl_line:
                                    _esc_lines.append(f"🔴 {_bl_line.strip()}")
                            if _stag_warn:
                                _esc_lines.append(_stag_warn)
                            if _cap_warns:
                                _esc_lines.extend(_cap_warns[:2])
                            _esc_lines.append("💬 Напиши мне — что добавить или попробовать. Я перенастрою агентов.")
                            _esc_text = '\n\n'.join(_esc_lines)
                            await self.bot.send_message(chat_id=user.telegram_id, text=_esc_text)
                            session.add(Interaction(
                                user_id=user.id,
                                message_type='proactive',
                                content=json.dumps({
                                    '__agent': {'name': 'ASI', 'id': 0, 'avatar_url': ''},
                                    'text': _esc_text,
                                    '__anchor_type': 'autopilot_escalation',
                                }, ensure_ascii=False),
                            ))
                            session.commit()
                            logger.info("[ANCHOR-AUTOPILOT] escalation sent user %d (%d dispatches, blocker=%s)",
                                        user.id, _tot_disp, _blocker_in_result)
                    except Exception as _esc_err:
                        logger.debug("[ANCHOR-AUTOPILOT] escalation send failed: %s", _esc_err)
                        try:
                            session.rollback()
                        except Exception:
                            pass

                if result and result.strip() and self.bot and not _is_noise_result:
                    try:
                        # Очищаем технические детали ПЕРЕД отправкой пользователю
                        from ai_integration.utils import clean_technical_details as _ctd
                        _cleaned_result = _ctd(result.strip())
                        if not _cleaned_result or len(_cleaned_result.strip()) < 10:
                            _cleaned_result = result.strip()  # fallback если слишком агрессивная чистка
                        # Пауза + typing перед отправкой — не вываливаем сразу после объявления координатора
                        await asyncio.sleep(2)
                        try:
                            await self.bot.send_chat_action(chat_id=user.telegram_id, action='typing')
                            await asyncio.sleep(1)
                        except Exception as _e:
                            logger.debug("suppressed: %s", _e)
                        await self.bot.send_message(
                            chat_id=user.telegram_id,
                            text=_cleaned_result,
                        )
                        # Оборачиваем в __agent JSON для корректного отображения в веб-чате
                        # Реальные агенты (id!=0): anchor_type → 'goal_autopilot_result' (видимый)
                        # ASI (id=0): anchor_type → 'goal_autopilot_review' (скрытый, системный)
                        _result_anchor_type = (
                            'goal_autopilot_result' if _chosen_id != 0 else anchor.anchor_type
                        )
                        _agent_content = json.dumps({
                            '__agent': {
                                'name': _chosen_name,
                                'id': _chosen_id,
                                'avatar_url': _chosen_avatar,
                            },
                            'text': _strip_html(_cleaned_result),
                            '__tools_used': _tools_used,
                            '__anchor_type': _result_anchor_type,
                        }, ensure_ascii=False)
                        # Реальные агенты (не ASI) сохраняем как agent_msg — отчёт по назначению
                        # ASI сохраняем как proactive — координаторская инициатива
                        _msg_type_result = 'agent_msg' if _chosen_id != 0 else 'proactive'
                        session.add(Interaction(
                            user_id=user.id,
                            message_type=_msg_type_result,
                            content=_agent_content,
                        ))
                        session.commit()
                        try:
                            from ai_integration.conversation_history import save_message_to_history as _smh_r
                            _smh_r(user.telegram_id, 'assistant', result.strip(), session=session)
                        except Exception as _e:
                            logger.debug("suppressed: %s", _e)
                    except Exception as _e_res:
                        logger.warning("[ANCHOR-AUTOPILOT] result send failed: %s", _e_res)

                # ── Цепочка: агент может делегировать через DELEGATE[X]: → запускаем следующего ──
                # Максимум одно продолжение за цикл чтобы не перегружать Railway.
                if result and len(result) > 30 and not _is_noise_result and agents:
                    await asyncio.sleep(4)  # Пауза перед следующим агентом в цепочке
                    try:
                        await self._maybe_continue_chain(
                            user, chosen, anchor, task_text, result, agents, session, max_cont=1,
                        )
                    except Exception as _chain_err:
                        logger.debug("[ANCHOR-AUTOPILOT] chain continuation error: %s", _chain_err)

                # ── ASI DIRECTOR: после отчёта реального агента — анализирует + даёт следующий шаг ──
                # Срабатывает только если: реальный агент (не ASI), есть предупреждения, результат доставлен
                _fw_dir = data.get('feasibility_warnings', [])
                if not _is_noise_result and result and _chosen_id != 0 and self.bot and _fw_dir:
                    await asyncio.sleep(3)  # Пауза перед комментарием ASI-директора
                    try:
                        _dir_goals = ', '.join(
                            f"«{g.get('title', '')}» ({g.get('progress', 0)}%)"
                            for g in data.get('goals', [])[:2]
                        )
                        _dir_p = (
                            f"Ты — ASI, координатор проекта. Агент {_chosen_name} только что отчитался:\n"
                            f"«{result.strip()[:400]}»\n\n"
                            f"Цели пользователя: {_dir_goals}\n"
                            f"Предупреждения: {'; '.join(str(w)[:100] for w in _fw_dir[:2])}\n\n"
                            "Напиши ОДНО короткое предложение (15-25 слов): что планируется дальше "
                            "ИЛИ что конкретно нужно от пользователя (если застряли). "
                            "Прямо и конкретно — без общих слов. "
                            "Обращение от ASI. Живо. Без markdown."
                        )
                        from ai_integration.autonomous_agent import _quick_ai_call_raw as _qar_d
                        _dir_resp = await _qar_d(
                            [{'role': 'user', 'content': _dir_p}],
                            max_tokens=80, _caller='asi_dir',
                        )
                        if _dir_resp and len(_dir_resp.strip()) > 20:
                            _dir_txt = _dir_resp.strip()
                            await self.bot.send_message(chat_id=user.telegram_id, text=_dir_txt)
                            session.add(Interaction(
                                user_id=user.id,
                                message_type='proactive',
                                content=json.dumps({
                                    '__agent': {'name': 'ASI', 'id': 0, 'avatar_url': ''},
                                    'text': _dir_txt,
                                    '__anchor_type': 'asi_director_review',
                                }, ensure_ascii=False),
                            ))
                            session.commit()
                            logger.info("[ANCHOR-AUTOPILOT] ASI director review sent user %d", user.id)
                    except Exception as _dir_e:
                        logger.debug("[ANCHOR-AUTOPILOT] ASI director review failed: %s", _dir_e)
                        try:
                            session.rollback()
                        except Exception:
                            pass

            # Помечаем якорь доставленным
            anchor.delivered_at = datetime.now(timezone.utc)
            session.commit()

            # ── Проверяем: закрыл ли агент цель в этом сеансе ──
            # Ищем goal_completed события за последние 5 минут для этого пользователя.
            # Если цель завершена: экспирируем pending-якоря + уведомляем пользователя.
            try:
                from models import AgentActivityLog as _AAL_gc, Goal as _Goal_gc
                _gc_cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
                _completed_goal_logs = session.query(_AAL_gc).filter(
                    _AAL_gc.user_id == user.id,
                    _AAL_gc.activity_type == 'goal_completed',
                    _AAL_gc.created_at >= _gc_cutoff,
                ).all()
                if _completed_goal_logs:
                    # Авто-экспирируем все pending goal_autopilot_review якоря пользователя
                    _pending_ap = session.query(Anchor).filter(
                        Anchor.user_id == user.id,
                        Anchor.anchor_type == 'goal_autopilot_review',
                        Anchor.delivered_at.is_(None),
                    ).all()
                    for _pa in _pending_ap:
                        _pa.delivered_at = datetime.now(timezone.utc)
                    if _pending_ap:
                        session.commit()
                        logger.info(
                            "[ANCHOR-AUTOPILOT] goal_completed: expired %d pending anchors for user %d",
                            len(_pending_ap), user.id,
                        )
                    # Уведомляем пользователя о каждой закрытой цели
                    for _gc_log in _completed_goal_logs:
                        try:
                            _gc_goal = session.query(_Goal_gc).filter_by(
                                id=_gc_log.ref_id, user_id=user.id
                            ).first()
                            if _gc_goal and self.bot:
                                _gc_msg = (
                                    f"🎯 Цель достигнута!\n\n"
                                    f"«{_gc_goal.title}» — выполнено на 100%.\n"
                                    f"Автопилот для этой цели остановлен."
                                )
                                await self.bot.send_message(
                                    chat_id=user.telegram_id,
                                    text=_gc_msg,
                                )
                                logger.info(
                                    "[ANCHOR-AUTOPILOT] goal_completed notify: user %d goal='%s'",
                                    user.id, _gc_goal.title,
                                )
                        except Exception as _gc_notify_err:
                            logger.debug("[ANCHOR-AUTOPILOT] goal completion notify err: %s", _gc_notify_err)
            except Exception as _gc_outer_err:
                logger.debug("[ANCHOR-AUTOPILOT] goal completion check err: %s", _gc_outer_err)

            logger.info(
                "[ANCHOR-AUTOPILOT] user %d: %s executed goal review → %d chars",
                user.id, agent_name, len(result or ''),
            )

            # Результат сохранён в AgentActivityLog.result → context_builder читает его напрямую

            # Обновляем статус dispatch-лога (ВСЕГДА — даже если result пустой)
            if agents and _aal_id:
                try:
                    _tools_prefix = f"[tools: {', '.join(_tools_used)}] " if _tools_used else ''
                    _filter_tag = f"[filtered:{_filter_reason}] " if _filter_reason else ''
                    _full_result = _filter_tag + _tools_prefix + (result or '')
                    if result and result.strip() and not _is_noise_result:
                        _aal_status = 'completed'
                    elif _filter_reason == 'dedup':
                        _aal_status = 'dedup_filtered'
                    elif _filter_reason == 'noise':
                        _aal_status = 'noise_filtered'
                    elif result and result.strip():
                        _aal_status = 'no_action'
                    else:
                        _aal_status = 'empty_result'
                    _aal_result_text = _full_result[:2000] if _full_result.strip() else 'empty'
                    from sqlalchemy import text as _aal_upd_text
                    session.execute(_aal_upd_text(
                        "UPDATE agent_activity_log SET status=:st, result=:res, updated_at=NOW() WHERE id=:aid"
                    ), {'st': _aal_status, 'res': _aal_result_text, 'aid': _aal_id})
                    session.commit()
                except Exception as _upd_err:
                    logger.warning("[ANCHOR-AUTOPILOT] AAL status update failed: %s", _upd_err)
                    try:
                        session.rollback()
                    except Exception:
                        pass

        except Exception as e:
            logger.warning("[ANCHOR-AUTOPILOT] error for user %d: %s", user.id, e)
            try:
                session.rollback()
            except Exception:
                pass
            # Update AAL by id (if we have it) via raw SQL — more reliable
            try:
                _aal_id_err = locals().get('_aal_id')
                if _aal_id_err:
                    from sqlalchemy import text as _aal_err_text
                    session.execute(_aal_err_text(
                        "UPDATE agent_activity_log SET status='failed', result=:res, updated_at=NOW() WHERE id=:aid"
                    ), {'res': f'Error: {str(e)[:300]}', 'aid': _aal_id_err})
                    session.commit()
            except Exception:
                try:
                    session.rollback()
                except Exception:
                    pass
            # Ensure anchor.delivered_at is committed
            try:
                anchor.delivered_at = datetime.now(timezone.utc)
                session.commit()
            except Exception:
                try:
                    session.rollback()
                except Exception:
                    pass

    async def _dispatch_agents_for_new_anchors(self, user, new_anchors: list):
        """
        Event-driven: когда AnchorEngine создаёт signal-якорь (goal_stagnation,
        goal_deadline, task_stale и т.д.), мы сразу находим подходящего агента
        и запускаем его — не ждём следующего цикла L2 координатора.

        Это заменяет «polling каждые 2-4ч» реакцией на конкретное событие.
        Fire-and-forget: не блокирует основной цикл доставки якорей.
        new_anchors: список dict с ключами anchor_type/source/topic/data
        """
        # Поддерживаем как dict (новый формат), так и ORM-объекты (обратная совместимость)
        def _get(obj, key, default=None):
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        trigger_anchors = [a for a in new_anchors if _get(a, 'anchor_type') in _AGENT_DISPATCH_TRIGGERS
                          and _get(a, 'anchor_type') != 'goal_autopilot_review']
        if not trigger_anchors:
            return

        try:
            from models import Session as _Db, UserAgent as _UA, AgentActivityLog as _AAL
            from ai_integration.autonomous_agent import _exec_agent_for_director

            _s = _Db()
            try:
                from models import AgentSubscription as _AS_evd
                _sub_ids_evd = {r.agent_id for r in _s.query(_AS_evd).filter_by(user_id=user.id).all()}
                agents = (
                    _s.query(_UA)
                    .filter(
                        _UA.id.in_(_sub_ids_evd),
                        _UA.status != 'disabled',
                    )
                    .limit(10).all()
                ) if _sub_ids_evd else []
                if not agents:
                    return

                for anchor in trigger_anchors:
                    # Guard: не повторяем dispatch для того же источника чаще раза в 4ч
                    recent_dispatch = (
                        _s.query(_AAL)
                        .filter(
                            _AAL.user_id == user.id,
                            _AAL.activity_type == 'agent_event_dispatch',
                            _AAL.target == _get(anchor, 'source'),
                            _AAL.created_at >= datetime.now(timezone.utc) - timedelta(hours=4),
                        )
                        .first()
                    )
                    if recent_dispatch:
                        continue

                    # Строим задачу из шаблона
                    try:
                        data = _get(anchor, 'data') or {}
                        _a_topic = _get(anchor, 'topic')
                        _a_type = _get(anchor, 'anchor_type')
                        task_text = _AGENT_DISPATCH_TRIGGERS[_a_type].format(
                            goal=data.get('title', _a_topic or 'без названия'),
                            progress=data.get('progress', 0),
                            task=data.get('title', _a_topic or 'без названия'),
                        )
                    except Exception:
                        task_text = _get(anchor, 'topic') or _get(anchor, 'anchor_type')

                    # Выбираем агента: AI решает кто лучше подходит (fallback → keywords)
                    chosen = await self._pick_best_agent(agents, task_text, _a_type)

                    # ── Проверяем и списываем токены за event-dispatch ──
                    from token_service import has_enough_tokens as _het_ev, spend_tokens as _sp_ev
                    from config import FREE_ACCESS_MODE as _FAM_ev
                    if not _FAM_ev:
                        if not _het_ev(user.telegram_id, 'proactive_message', session=_s):
                            logger.info("[ANCHOR-DISPATCH] user %d: skip %s — not enough tokens", user.id, _a_type)
                            continue
                        _sp_ev(user.telegram_id, 'proactive_message', description=f'event_dispatch_{_a_type}', session=_s, auto_commit=False)

                    # Логируем dispatch (cooldown guard)
                    _s.add(_AAL(
                        user_id=user.id,
                        activity_type='agent_event_dispatch',
                        title=f'{chosen.name} → {_a_type}',
                        content=task_text[:500],
                        target=_get(anchor, 'source'),
                        status='in_progress',
                        ref_id=chosen.id,
                    ))
                    _s.commit()

                    # Собираем agent_data
                    import json as _jd
                    agent_data = {
                        'id': chosen.id,
                        'name': chosen.name,
                        'job_title': chosen.job_title or '',
                        'specialization': chosen.specialization or '',
                        'description': chosen.description or '',
                        'personality': chosen.personality or '',
                        'python_code': chosen.python_code or '',
                        'user_api_keys': chosen.user_api_keys or '',
                        'tools_allowed': chosen.tools_allowed or '',
                        'search_scope': chosen.search_scope or '',
                        'avatar_url': _safe_avatar(chosen.avatar_url, chosen.id),
                        'tools': _jd.loads(chosen.tools_allowed or '[]'),
                    }

                    # ── Биллинг кастомного агента (роялти автору) ──
                    if getattr(chosen, 'id', 0) != 0:
                        from ai_integration.user_agents import bill_agent_message as _bam_ev
                        _bill_ev = _bam_ev(user.telegram_id, chosen.id, session=_s)
                        if not _bill_ev.get('success'):
                            logger.info("[ANCHOR-DISPATCH] user %d: skip %s — agent billing failed: %s", user.id, chosen.name, _bill_ev.get('error', ''))
                            continue

                    # Запускаем агента и при необходимости продолжаем цепочку
                    try:
                        _raw_result = await _exec_agent_for_director(
                            agent_data, task_text, user.telegram_id,
                        )
                        result = _raw_result[0] if isinstance(_raw_result, (tuple, list)) else _raw_result
                        _ev_tools_used = list(_raw_result[1]) if isinstance(_raw_result, (tuple, list)) and len(_raw_result) > 1 else []
                        # Обновляем лог: выполнено
                        _s2 = _Db()
                        try:
                            _log = (
                                _s2.query(_AAL)
                                .filter_by(
                                    user_id=user.id,
                                    activity_type='agent_event_dispatch',
                                    target=_get(anchor, 'source'),
                                )
                                .order_by(_AAL.id.desc()).first()
                            )
                            if _log:
                                _log.status = 'completed'
                                _log.result = (result or '')[:400]
                            _s2.commit()
                        finally:
                            _s2.close()

                        logger.info(
                            "[ANCHOR-DISPATCH] user %d: %s triggered by %s → %d chars",
                            user.id, chosen.name, _get(anchor, 'anchor_type'), len(result or ''),
                        )

                        # Отправляем результат агента пользователю в Telegram
                        if result and result.strip() and self.bot:
                            try:
                                await self.bot.send_message(
                                    chat_id=user.telegram_id,
                                    text=f"{chosen.name}:\n\n{result.strip()}",
                                )
                                _ev_agent_content = json.dumps({
                                    '__agent': {
                                        'name': chosen.name,
                                        'id': chosen.id,
                                        'avatar_url': _safe_avatar(chosen.avatar_url, chosen.id),
                                    },
                                    'text': _strip_html(result.strip()),
                                    '__tools_used': _ev_tools_used,
                                    '__anchor_type': _get(anchor, 'anchor_type'),
                                }, ensure_ascii=False)
                                _s.add(Interaction(
                                    user_id=user.id,
                                    message_type='proactive',
                                    content=_ev_agent_content,
                                ))
                                _s.commit()
                                try:
                                    from ai_integration.conversation_history import save_message_to_history as _smh_ev
                                    _smh_ev(user.telegram_id, 'assistant', result.strip(), session=_s)
                                except Exception as _e:
                                    logger.debug("suppressed: %s", _e)
                            except Exception as _e_ev_send:
                                logger.warning("[ANCHOR-DISPATCH] result send failed: %s", _e_ev_send)

                        # ── ASI-продолжение: анализ результата → следующий агент ──
                        if result and len(result) > 30:
                            _chain_max_ev = 1 if _get(anchor, 'anchor_type') == 'goal_autopilot_review' else 3
                            await self._maybe_continue_chain(
                                user, chosen, anchor, task_text, result, agents, _s,
                                max_cont=_chain_max_ev,
                            )

                    except Exception as _exec_e:
                        logger.debug("[ANCHOR-DISPATCH] exec error: %s", _exec_e)
                        # Mark activity log as failed (prevent stuck in_progress)
                        try:
                            _sf = _Db()
                            _fl = (
                                _sf.query(_AAL)
                                .filter_by(
                                    user_id=user.id,
                                    activity_type='agent_event_dispatch',
                                    target=_get(anchor, 'source'),
                                    status='in_progress',
                                )
                                .order_by(_AAL.id.desc()).first()
                            )
                            if _fl:
                                _fl.status = 'failed'
                                _fl.result = f'Error: {str(_exec_e)[:300]}'
                            _sf.commit()
                            _sf.close()
                        except Exception:
                            pass
            finally:
                _s.close()
        except Exception as e:
            logger.debug("[ANCHOR-DISPATCH] dispatch error for user %d: %s", user.id, e)

    @staticmethod
    def _format_goal_tasks(tasks: list) -> str:
        """Format task list for autopilot context — only active tasks."""
        if not tasks:
            return ''
        # Skip completed/cancelled tasks to save tokens
        active = [t for t in tasks if t.get('status') not in ('done', 'completed', 'cancelled', 'deleted')]
        if not active:
            return ''
        lines = []
        for t in active:
            status_icon = {'pending': '⏳', 'in_progress': '🔄'}.get(t.get('status', ''), '•')
            line = f"    {status_icon} {t['title']}"
            lines.append(line)
        return '\n  Задачи:\n' + '\n'.join(lines)

    async def _pick_best_agent(self, agents, task_text: str, anchor_type: str):
        """AI выбирает лучшего агента для задачи.
        Fallback на keyword matching если AI недоступен."""
        if len(agents) == 1:
            return agents[0]

        try:
            from ai_integration.autonomous_agent import _quick_ai_call_raw
            agent_descs = '\n'.join(
                f'{i+1}. {a.name} — {a.job_title or ""} / {a.specialization or ""} / {(a.description or "")[:80]}'
                for i, a in enumerate(agents)
            )
            resp = await _quick_ai_call_raw([{
                "role": "user",
                "content": (
                    f"Задача: {task_text[:200]}\n"
                    f"Тип события: {anchor_type}\n\n"
                    f"Доступные агенты:\n{agent_descs}\n\n"
                    "Выбери ОДНОГО агента, который лучше всего подходит.\n"
                    "Ответь ТОЛЬКО номером агента (1, 2, 3...)."
                ),
            }], max_tokens=10)
            if resp:
                import re as _re_pick
                _m = _re_pick.search(r'\d+', resp.strip())
                if _m:
                    idx = int(_m.group()) - 1
                    if 0 <= idx < len(agents):
                        return agents[idx]
        except Exception as _e:
            logger.debug("suppressed: %s", _e)

        # Fallback: keyword matching
        ANALYTIC_KW = {'аналит', 'страте', 'исследо', 'план', 'маркет', 'консульт'}
        TASK_KW = {'задач', 'план', 'менедж', 'координ', 'ассист', 'помощн'}
        kw_set = ANALYTIC_KW if anchor_type in ('goal_stagnation', 'goal_decomposition', 'goal_deadline') else TASK_KW
        for ag in agents:
            spec = ((ag.specialization or '') + ' ' + (ag.description or '')).lower()
            if any(k in spec for k in kw_set):
                return ag
        return agents[0]

    async def _maybe_continue_chain(self, user, prev_agent, anchor, task_text, result, agents, session, max_cont=3):
        """ASI анализирует результат агента и решает — нужен ли следующий шаг.

        Если задача не завершена или нужна экспертиза другого агента,
        запускает следующего агента напрямую.
        max_cont: максимум продолжений (3 для event-якорей, 1 для autopilot).
        """
        # Поддерживаем как dict (из _dispatch_agents_for_new_anchors), так и ORM-объекты
        def _get_anc(obj, key, default=None):
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)

        try:
            # Guard: не создаём цепочку длиннее max_cont продолжений
            from models import AgentActivityLog as _AAL2
            _cont_count = (
                session.query(_AAL2)
                .filter(
                    _AAL2.user_id == user.id,
                    _AAL2.activity_type == 'agent_chain_continue',
                    _AAL2.target == _get_anc(anchor, 'source'),
                    _AAL2.created_at >= datetime.now(timezone.utc) - timedelta(hours=6),
                )
                .count()
            )
            if _cont_count >= max_cont:
                return

            # Guard: если агент заблокирован — не продолжаем
            if result.strip().startswith('BLOCKED:'):
                return

            # ASI анализирует результат и решает кому передать
            from ai_integration.autonomous_agent import _quick_ai_call_raw
            import re as _re2
            import types as _types_chain

            # ── Аннотируем агентов по реальным возможностям (send vs read) ──
            def _chain_agent_note(a) -> str:
                _k = (getattr(a, 'user_api_keys', '') or '').lower()
                _t = (getattr(a, 'tools_allowed', '') or '').lower()
                _SEND_KEYS = ('smtp_', 'resend_api_key', 'sendgrid_', 'mailgun_', 'sparkpost_')
                _gmail_send = (
                    'gmail_' in _k and
                    any(pk in _k for pk in ('gmail_pass=', 'gmail_app_password=', 'gmail_password='))
                    and 'gmail_user=' in _k
                )
                # Яндекс и Mail.ru поддерживают SMTP нативно — если есть USER → чаще всего и пароль
                _yandex_send = ('yandex_user=' in _k)
                _mailru_send = ('mailru_user=' in _k)
                if any(s in _k for s in _SEND_KEYS) or _gmail_send or _yandex_send or _mailru_send:
                    return ' [отправляет email]'
                # Gmail без app_password — только чтение IMAP
                if 'gmail_user=' in _k or 'gmail_imap' in _k or 'imap_' in _k:
                    return ' [только читает email — НЕ отправляет]'
                if 'send_outreach_email' in _t:
                    return ' [отправляет email через платформу]'
                return ''

            _agents_desc = ', '.join(
                f"{a.name} ({a.job_title or a.specialization or '?'}){_chain_agent_note(a)}"
                for a in agents if getattr(a, 'id', 0) != getattr(prev_agent, 'id', -1)
            ) + ', ASI (координатор, отправляет письма через платформу Resend)'

            # ── Быстрый детектор: нашли email-контакты → находим кто УМЕЕТ ОТПРАВЛЯТЬ ──
            # Принципиально: IMAP (Gmail read-only) ≠ отправка.
            # Приоритеты: 1) агент с ключами SMTP/Resend/Sendgrid, 2) агент с send_outreach_email в tools,
            #             3) ASI через платформу Resend (всегда доступно).
            # GUARD: email-relay только если агент нашёл реальные контакты (GitHub/search),
            # а НЕ RSS-ленты, статьи или произвольный веб-контент.
            _prev_code_lower = (getattr(prev_agent, 'python_code', '') or '').lower()
            _prev_is_rss_only = (
                ('rss' in _prev_code_lower or 'feedparser' in _prev_code_lower or
                 'urllib.request' in _prev_code_lower)
                and not ('github' in _prev_code_lower or 'imaplib' in _prev_code_lower)
            )
            _found_emails = [] if _prev_is_rss_only else _re2.findall(
                r'[a-zA-Z0-9_.+\-]+@[a-zA-Z0-9\-]+\.[a-zA-Z0-9\-.]{2,}', result
            )
            # Дополнительный фильтр: utm_source / noreply / tracking emails — не реальные контакты
            _NOREPLY_SKIP = ('utm_', 'noreply', 'no-reply', 'notification', 'do-not-reply',
                             'habrahabr', 'habr.com', '@github.com', 'bounce', 'postmaster')
            _found_emails = [e for e in _found_emails
                             if not any(s in e.lower() for s in _NOREPLY_SKIP)]
            _email_agent_relay = None
            if _found_emails:
                _SEND_KEY_CAPS = ('smtp_', 'resend_api_key', 'sendgrid_', 'mailgun_', 'sparkpost_')
                _relay_p1 = None   # агент с явными SEND-ключами
                _relay_p2 = None   # агент с send_outreach_email в tools_allowed
                for _ag_r in agents:
                    if getattr(_ag_r, 'id', 0) == getattr(prev_agent, 'id', -1):
                        continue
                    _ag_r_keys = (getattr(_ag_r, 'user_api_keys', '') or '').lower()
                    _ag_r_tools = (getattr(_ag_r, 'tools_allowed', '') or '').lower()
                    _gmail_can_send_r = (
                        'gmail_' in _ag_r_keys and
                        any(pk in _ag_r_keys for pk in ('gmail_pass=', 'gmail_app_password=', 'gmail_password='))
                        and 'gmail_user=' in _ag_r_keys
                    )
                    # Яндекс и Mail.ru поддерживают SMTP нативно — если есть USER → чаще всего есть и пароль
                    _yandex_can_send_r = 'yandex_user=' in _ag_r_keys
                    _mailru_can_send_r = 'mailru_user=' in _ag_r_keys
                    if (any(k in _ag_r_keys for k in _SEND_KEY_CAPS)
                            or _gmail_can_send_r or _yandex_can_send_r or _mailru_can_send_r):
                        _relay_p1 = _ag_r
                        break
                    if not _relay_p2 and 'send_outreach_email' in _ag_r_tools:
                        _relay_p2 = _ag_r
                _email_agent_relay = _relay_p1 or _relay_p2
                if not _email_agent_relay:
                    # Ни у кого нет send-способности → ASI отправит через платформу Resend
                    _email_agent_relay = _types_chain.SimpleNamespace(
                        id=0, name='ASI',
                        job_title='AI-координатор',
                        specialization='outreach через Resend',
                        description='Отправляет outreach-письма через платформу (Resend).',
                        personality='', python_code='', user_api_keys='', avatar_url='',
                        tools_allowed=('["send_outreach_email","negotiate_by_email",'
                                       '"start_email_campaign","add_email_leads",'
                                       '"save_email_contact","update_goal_progress",'
                                       '"find_relevant_contacts_for_task"]'),
                        search_scope='',
                    )
                    logger.info(
                        "[ANCHOR-CHAIN] contact-relay: no send-capable agent → ASI will send via Resend",
                    )
                else:
                    logger.info(
                        "[ANCHOR-CHAIN] contact-relay: %s found %d emails → %s",
                        prev_agent.name, len(_found_emails), _email_agent_relay.name,
                    )

            # ── Детектор входящих писем (результат check_emails) ──
            # Формат "Новые входящие (...): От: / Тема: / Превью:" от обоих IMAP/Gmail-бэкендов.
            # Email-адреса отправителей ≠ outreach-цели! Нужно передать ASI для решения.
            # GUARD: только для агентов с реальными IMAP/Gmail ключами — RSS-агенты не могут проверять почту!
            _prev_keys_lower = (getattr(prev_agent, 'user_api_keys', '') or '').lower()
            _prev_has_imap = (
                'gmail_user=' in _prev_keys_lower or
                'imap_' in _prev_keys_lower or
                'gmail_imap' in _prev_keys_lower or
                'yandex_user=' in _prev_keys_lower or
                'mailru_user=' in _prev_keys_lower
            )
            _INBOX_MARKERS = ('Новые входящие', 'От: ', 'Тема: ', 'Превью: ')
            _is_inbox_result = (
                _prev_has_imap  # только IMAP-агент может вернуть реальные входящие
                and sum(1 for p in _INBOX_MARKERS if p in result) >= 3
            )
            if _is_inbox_result:
                _found_emails = []       # не рассылать письма людям из входящих
                _email_agent_relay = None

            _decision: dict = {}
            if _is_inbox_result:
                # Дедупликация: не передавать ASI те же входящие что уже обрабатывались в последние 2ч
                import hashlib as _hashlib
                _inbox_hash = _hashlib.md5(result[:1000].encode('utf-8', 'ignore')).hexdigest()[:12]
                try:
                    from sqlalchemy import text as _sql_chk
                    _prev_inbox = session.execute(_sql_chk(
                        "SELECT COUNT(*) FROM agent_activity_log "
                        "WHERE user_id=:uid AND activity_type='inbox_reply' "
                        "AND title LIKE :hpat "
                        "AND created_at > NOW() - INTERVAL '2 hours'"
                    ), {'uid': user.id, 'hpat': f'%[h:{_inbox_hash}]%'}).scalar()
                except Exception:
                    _prev_inbox = 0
                if _prev_inbox:
                    logger.info('[ANCHOR-CHAIN] inbox-relay: dedup skip (same inbox relayed in 2h, hash=%s)', _inbox_hash)
                    return
                # Агент нашёл входящие → логируем activity + передаём ASI для решения
                _inbox_count = len(_re2.findall(r'^От:', result, _re2.MULTILINE)) or 1
                try:
                    from models import AgentActivityLog as _AAL_ibox, Session as _Db_ibox
                    _si_ibox = _Db_ibox()
                    _si_ibox.add(_AAL_ibox(
                        user_id=user.id,
                        activity_type='inbox_reply',
                        status='new',
                        target=f'agent:{prev_agent.name}',
                        title=f'{prev_agent.name}: {_inbox_count} новых письма [h:{_inbox_hash}]',
                        content=result[:2000],
                    ))
                    _si_ibox.commit()
                    _si_ibox.close()
                    logger.info('[ANCHOR-CHAIN] inbox-relay: logged inbox_reply for %s (%d msgs)',
                                prev_agent.name, _inbox_count)
                except Exception as _e_ibox:
                    logger.debug('[ANCHOR-CHAIN] inbox_reply log error: %s', _e_ibox)
                _decision = {
                    'continue': True,
                    'agent_name': 'ASI',
                    'task': (
                        f"{prev_agent.name} проверил почту и нашёл {_inbox_count} новых письма. "
                        f"Реши что делать: ответить (reply_to_outreach_email), "
                        f"сохранить контакт (save_email_contact), создать задачу (add_task) или другое.\n\n"
                        f"Входящие письма:\n{result[:2000]}"
                    ),
                }
                logger.info('[ANCHOR-CHAIN] inbox-relay: %s → ASI (%d inbox msgs)',
                            prev_agent.name, _inbox_count)
            elif _email_agent_relay and _found_emails:
                _emails_relay_str = ', '.join(_found_emails[:5])
                _decision = {
                    'continue': True,
                    'agent_name': _email_agent_relay.name,
                    'task': (
                        f"Отправь письма контактам, которых только что нашёл {prev_agent.name}: "
                        f"{_emails_relay_str}. "
                        f"Используй send_outreach_email или negotiate_by_email."
                    ),
                }
                logger.info(
                    "[ANCHOR-CHAIN] contact-relay: %s found %d emails → %s",
                    prev_agent.name, len(_found_emails), _email_agent_relay.name,
                )
            else:
                _analysis = await _quick_ai_call_raw([{
                    "role": "user",
                    "content": (
                        f"Задача: {task_text[:300]}\n"
                        f"Агент {prev_agent.name} выполнил: {result[:1500]}\n\n"
                        f"Команда (имена и роли): {_agents_desc}\n\n"
                        "Оцени: задача завершена или нужен следующий агент?\n"
                        "Цепочки: поиск людей → отправка письма; анализ → создание задач; данные → публикация.\n"
                        "Признаки завершения: update_goal_progress выполнен, цель достигнута, БЛОКЕР без решения.\n"
                        "Признаки продолжения: найдены email/контакты но не написали; данные получены но не опубликованы.\n"
                        "Если завершено — {\"continue\": false}\n"
                        "Если нужен следующий агент — {\"continue\": true, \"agent_name\": \"точное имя из команды\", "
                        "\"task\": \"конкретное задание с данными из результата (email, имена, числа)\"}\n"
                        "JSON:"
                    ),
                }], max_tokens=300)

                if not _analysis:
                    return

                _m = _re2.search(r'\{[\s\S]*?\}', _analysis)
                if not _m:
                    return
                _decision = json.loads(_m.group())

            if not _decision.get('continue'):
                return

            _next_name = _decision.get('agent_name', '')
            _next_task = _decision.get('task', '')
            if not _next_name or not _next_task:
                return
            # Маркер автопилота в TASK (не только в dialog_context!) —
            # иначе _is_autopilot_task=False → нет echo-фильтра, нет tool_choice=required
            if '[АВТОПИЛОТ]' not in _next_task:
                _next_task = f'[АВТОПИЛОТ] {_next_task}'

            # Находим следующего агента (ASI — синтетический объект, не в списке)
            _next_ag = None
            if _next_name.upper() == 'ASI' and _email_agent_relay and getattr(_email_agent_relay, 'id', -1) == 0:
                _next_ag = _email_agent_relay
            else:
                for ag in agents:
                    if ag.name.lower() == _next_name.lower():
                        _next_ag = ag
                        break
            if not _next_ag:
                return

            # Не выбираем того же агента повторно — это вызывает дубли сообщений
            if _next_ag.id == prev_agent.id:
                logger.info('[ANCHOR-CHAIN] skipping same agent %s', _next_ag.name)
                return

            # Логируем continuation
            from models import AgentActivityLog as _AAL3
            session.add(_AAL3(
                user_id=user.id,
                activity_type='agent_chain_continue',
                title=f'[chain] {prev_agent.name} → {_next_ag.name}',
                content=_next_task[:500],
                target=_get_anc(anchor, 'source'),
                status='in_progress',
                ref_id=_next_ag.id,
            ))
            session.commit()

            # ── Проверяем и списываем токены за chain-продолжение ──
            from token_service import has_enough_tokens as _het_ch, spend_tokens as _sp_ch
            from config import FREE_ACCESS_MODE as _FAM_ch
            if not _FAM_ch:
                if not _het_ch(user.telegram_id, 'proactive_message', session=session):
                    logger.info("[ANCHOR-CHAIN] user %d: skip chain — not enough tokens", user.id)
                    return
                _sp_ch(user.telegram_id, 'proactive_message', description='agent_chain_continue', session=session, auto_commit=False)

            # Запускаем следующего агента
            from ai_integration.autonomous_agent import _exec_agent_for_director
            import json as _jd2
            _next_data = {
                'id': _next_ag.id,
                'name': _next_ag.name,
                'job_title': _next_ag.job_title or '',
                'specialization': _next_ag.specialization or '',
                'description': _next_ag.description or '',
                'personality': _next_ag.personality or '',
                'python_code': _next_ag.python_code or '',
                'user_api_keys': _next_ag.user_api_keys or '',
                'tools_allowed': _next_ag.tools_allowed or '',
                'search_scope': getattr(_next_ag, 'search_scope', '') or '',
                'avatar_url': _safe_avatar(_next_ag.avatar_url, _next_ag.id),
                'tools': _jd2.loads(_next_ag.tools_allowed or '[]'),
            }
            _ctx = (
                f"Данные от коллеги {prev_agent.name}:\n{result[:300]}\n\n"
                + (
                    f"📧 КОНТАКТЫ ДЛЯ ОТПРАВКИ ПРЯМО СЕЙЧАС: {', '.join(_found_emails[:5])}\n"
                    f"→ Твоя задача: вызови send_outreach_email или negotiate_by_email для каждого из них!\n\n"
                    if _found_emails and _email_agent_relay else ""
                )
                + "Если есть email/контакт/ссылка в данных выше — используй их немедленно: действуй инструментом.\n"
                + "Если данных нет — сделай следующий самостоятельный шаг по цели.\n"
                + "Сообщай пользователю только КОНКРЕТНЫЕ факты: что сделал, что нашёл, кому написал."
            )

            # ── Уведомление о передаче между агентами ──
            # Передача от имени предыдущего агента — естественная коммуникация
            _transfer_text = f"{_next_ag.name}, передаю тебе задачу: {_next_task[:180]}"
            try:
                from ai_integration.autonomous_agent import _quick_ai_call_raw as _qar_tr
                _tr_p = (
                    f"Ты — {prev_agent.name}, специалист. Одним предложением передай задачу коллеге {_next_ag.name}.\n"
                    f"Что нашёл: {result[:200]}. Что ему делать: {_next_task[:150]}.\n"
                    f"Живо, конкретно, по имени {_next_ag.name}. Без [АВТОПИЛОТ], без markdown."
                )
                _tr_gen = await asyncio.wait_for(
                    _qar_tr([{"role": "user", "content": _tr_p}], max_tokens=70),
                    timeout=8,
                )
                if _tr_gen and len(_tr_gen.strip()) > 15:
                    _transfer_text = _tr_gen.strip()
            except Exception as _e:
                logger.debug("suppressed: %s", _e)
            # Dedup: пропускаем уведомление если совсем недавно было проактивное сообщение (≤5 мин)
            _chain_transfer_gap_ok = True
            try:
                _last_proactive_ts = session.query(Interaction.created_at).filter(
                    Interaction.user_id == user.id,
                    Interaction.message_type == 'proactive',
                ).order_by(Interaction.created_at.desc()).limit(1).scalar()
                if _last_proactive_ts:
                    _lp_utc = _last_proactive_ts.replace(tzinfo=timezone.utc) if _last_proactive_ts.tzinfo is None else _last_proactive_ts
                    if (datetime.now(timezone.utc) - _lp_utc).total_seconds() < 300:  # 5 min
                        _chain_transfer_gap_ok = False
                        logger.info("[ANCHOR-CHAIN] user %d: transfer notify suppressed (proactive gap < 5min)", user.id)
            except Exception:
                pass
            if _chain_transfer_gap_ok:
                if self.bot:
                    try:
                        await self.bot.send_message(
                            chat_id=user.telegram_id,
                            text=f"{prev_agent.name}:\n\n{_transfer_text}",
                        )
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)
                # Сохраняем в interaction для web-чата (от имени передающего агента)
                _transfer_content = json.dumps({
                    '__agent': {
                        'name': prev_agent.name,
                        'id': getattr(prev_agent, 'id', 0),
                        'avatar_url': _safe_avatar(getattr(prev_agent, 'avatar_url', ''), getattr(prev_agent, 'id', 0)),
                    },
                    'text': _transfer_text,
                    '__anchor_type': 'agent_chain_transfer',
                }, ensure_ascii=False)
                session.add(Interaction(
                    user_id=user.id,
                    message_type='proactive',
                    content=_transfer_content,
                ))
                try:
                    session.commit()
                except Exception:
                    try:
                        session.rollback()
                    except Exception:
                        pass

            logger.info(
                "[ANCHOR-CHAIN] user %d: %s → %s (task: %s)",
                user.id, prev_agent.name, _next_ag.name, _next_task[:80],
            )

            # ── Биллинг кастомного агента (роялти автору) ──
            if getattr(_next_ag, 'id', 0) != 0:
                from ai_integration.user_agents import bill_agent_message as _bam_ch
                _bill_ch = _bam_ch(user.telegram_id, _next_ag.id, session=session)
                if not _bill_ch.get('success'):
                    logger.info("[ANCHOR-CHAIN] user %d: skip chain — agent billing failed: %s", user.id, _bill_ch.get('error', ''))
                    return

            _next_raw = await asyncio.wait_for(
                _exec_agent_for_director(
                    _next_data, _next_task, user.telegram_id, dialog_context=_ctx,
                ),
                timeout=90,
            )
            _next_result = _next_raw[0] if isinstance(_next_raw, (tuple, list)) else _next_raw
            _chain_tools_used = list(_next_raw[1]) if isinstance(_next_raw, (tuple, list)) and len(_next_raw) > 1 else []

            # Сохраняем результат в лог
            try:
                _chain_log = (
                    session.query(_AAL3)
                    .filter(
                        _AAL3.user_id == user.id,
                        _AAL3.activity_type == 'agent_chain_continue',
                        _AAL3.ref_id == _next_ag.id,
                        _AAL3.status == 'in_progress',
                    )
                    .order_by(_AAL3.id.desc())
                    .first()
                )
                if _chain_log:
                    _chain_log.result = (_next_result or '')[:400]
                    _chain_log.status = 'completed'
                    session.commit()
            except Exception as _e:
                logger.debug("suppressed: %s", _e)

            # Отправляем результат следующего агента пользователю (с noise-фильтром)
            _chain_clean = (_next_result or '').strip()
            # Нормализуем для echo-проверки: убираем markdown, эмодзи
            _chain_normalized = re.sub(r'^\s*(?:[^\w\s]|\*{1,2}|_{1,2})+\s*', '', _chain_clean)
            _chain_lower = _chain_normalized.lower()
            _EMPTY_RESPONSES_CHAIN = {
                'задачу выполнил', 'задачу выполнила', 'данных нет',
                'задача выполнена', 'понял задачу', 'принял в работу',
                'задачу принял', 'задачу приняла',
            }
            _chain_agent_names = [a.name for a in agents if getattr(a, 'id', 0) != 0] + ['ASI']
            _chain_has_actions = bool(_chain_tools_used)
            _chain_is_echo = False  # промпт теперь учит думать правильно
            _chain_is_noise = (
                not _chain_has_actions and (
                    len(_chain_clean) < 15
                    or _chain_lower.rstrip('.!') in _EMPTY_RESPONSES_CHAIN
                )
                or (not _chain_has_actions and len(_chain_clean) < 80
                    and any(w in _chain_lower for w in ('duckduckgo не', 'сервис недоступ', 'веб-поиск временно')))
                or any(_chain_lower.startswith(n.lower() + ',') for n in _chain_agent_names)
            )
            if _next_result and _chain_clean and self.bot and not _chain_is_noise:
                try:
                    await self.bot.send_message(
                        chat_id=user.telegram_id,
                        text=f"{_next_ag.name}:\n\n{_next_result.strip()}",
                    )
                    _chain_agent_content = json.dumps({
                        '__agent': {
                            'name': _next_ag.name,
                            'id': _next_ag.id,
                            'avatar_url': _safe_avatar(_next_ag.avatar_url, _next_ag.id),
                        },
                        'text': _strip_html(_next_result.strip()),
                        '__tools_used': _chain_tools_used,
                        '__anchor_type': 'agent_chain_continue',
                    }, ensure_ascii=False)
                    session.add(Interaction(
                        user_id=user.id,
                        message_type='proactive',
                        content=_chain_agent_content,
                    ))
                    session.commit()
                    try:
                        from ai_integration.conversation_history import save_message_to_history as _smh_c
                        _smh_c(user.telegram_id, 'assistant', _next_result.strip(), session=session)
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)
                except Exception as _e_chain_send:
                    logger.warning("[ANCHOR-CHAIN] result send failed: %s", _e_chain_send)

            logger.info(
                "[ANCHOR-CHAIN] user %d: %s → %s (task: %s) → %d chars",
                user.id, prev_agent.name, _next_ag.name, _next_task[:50], len(_next_result or ''),
            )

        except Exception as _chain_e:
            logger.debug("[ANCHOR-CHAIN] error for user %d: %s", user.id, _chain_e)

    # ═══════════════════════════════════════════════════════
    # COORDINATOR DISPATCH — multi-agent plan execution
    # ═══════════════════════════════════════════════════════

    @staticmethod
    def _compute_state_directives(goals: list, data: dict, profiles: list) -> list:
        """State machine: вычисляет конкретное следующее действие для каждой цели на основе реального состояния БД.

        Возвращает список директив:
        [{'goal': str, 'tool': str, 'task': str, 'agent_domain': str, 'reason': str}]

        agent_domain: 'email' | 'research' | 'content' | 'any'
        """
        directives = []
        contacts_list = data.get('known_contacts', [])
        n_contacts = len(contacts_list)
        already_sent = set(data.get('already_sent_emails', []))
        email_campaigns = data.get('email_campaigns', [])
        total_sent = data.get('total_emails_sent', 0)
        failed_tools = data.get('failed_tools', {})
        per_history = data.get('per_agent_history', {})
        recent_txt = ' '.join(data.get('recent_actions', [])).lower()

        # ── Детектор деградированных агентов (2 последних записи) ──
        # Смотрим только 2 последних взаимодействия — если оба провал → деградирован.
        # Не смотрим всю 48ч историю: иначе агент «вечно деградирован» и никогда не получает шансов.
        degraded_agents = set()
        for ag_name, hist in per_history.items():
            _recent2 = list(hist)[-2:]
            fail_count = sum(1 for h in _recent2 if 'технические трудности' in h.lower() or 'не успел' in h.lower())
            if fail_count >= 2:
                degraded_agents.add(ag_name)
                logger.info("[COORD-SM] agent '%s' marked degraded (last 2 = failures)", ag_name)

        # ── Карта агентов по доменам ──
        # domain → [agent_name] — выбираем первого подходящего
        EMAIL_CAPS = ('imap', 'gmail', 'почт', 'mail', 'smtp', 'yandex', 'mailru')
        RSS_CAPS   = ('rss', 'feed', 'лент')
        GITHUB_CAPS = ('github', 'gitlab')
        RESEARCH_CAPS = ('alpha_vantage', 'newsapi', 'news_api')

        _domain_agents: dict = {'email': [], 'rss': [], 'github': [], 'research': [], 'any': []}
        for p in profiles:
            caps_lower = [c.lower() for c in p.get('caps', [])]
            caps_str = ' '.join(caps_lower)
            _name = p.get('name', '')
            # Деградация = логирование, не блокировка: агент всегда получает шанс работать
            if any(w in caps_str for w in EMAIL_CAPS):
                _domain_agents['email'].append(_name)
            if any(w in caps_str for w in RSS_CAPS):
                _domain_agents['rss'].append(_name)
            if any(w in caps_str for w in GITHUB_CAPS):
                _domain_agents['github'].append(_name)
            if any(w in caps_str for w in RESEARCH_CAPS):
                _domain_agents['research'].append(_name)
            _domain_agents['any'].append(_name)

        def _agent_for(domain: str) -> str:
            return (_domain_agents.get(domain) or _domain_agents['any'] or [''])[0]

        def _is_tool_failed(tool: str) -> bool:
            return failed_tools.get(tool, 0) >= 2

        _PEOPLE_KW = ('пользовател', 'тестировщик', 'клиент', 'подписчик', 'аудитор', 'рекрутинг', 'участник', 'лид')
        _FINANCE_KW = ('нефт', 'газ', 'рынок', 'биржа', 'акции', 'финанс', 'инвест', 'котировк', 'oil', 'stock', 'forex', 'крипто')
        _NEWS_KW = ('новост', 'мониторинг', 'тренды', 'медиа', 'пресс', 'обзор')
        _CONTENT_KW = ('контент', 'smm', 'пост', 'публикац', 'канал', 'telegram')
        _DEV_KW = ('разработ', 'программ', 'developer', 'репозитор', 'код')
        _SALES_KW = ('продаж', 'партнёр', 'b2b', 'outreach', 'сделк', 'переговор')

        for g in goals[:5]:
            title = g.get('title', '')
            title_l = title.lower()
            desc_l = (g.get('description', '') or '').lower()
            full_l = title_l + ' ' + desc_l
            progress = g.get('progress', 0)
            mc = g.get('metric_current', 0) or 0
            mt = g.get('metric_target') or 0

            # ── PEOPLE / OUTREACH goals ──
            if any(w in full_l for w in _PEOPLE_KW) or any(w in full_l for w in _SALES_KW):
                unsent = n_contacts - len(already_sent & {c.split('<')[1].rstrip('>').strip() for c in contacts_list if '<' in c})
                has_active_campaign = any('отправлено' in ec or 'active' in ec.lower() for ec in email_campaigns)
                email_agent = _agent_for('email') or _agent_for('github') or _agent_for('any')

                if not email_agent:
                    directives.append({
                        'goal': title, 'agent_domain': 'email',
                        'tool': 'find_relevant_contacts_for_task',
                        'task': f'Найди потенциальных пользователей для цели «{title}» через поиск контактов.',
                        'reason': 'нет email-агента, fallback на поиск контактов',
                    })
                    continue

                # Есть GitHub-capable агент
                # Check if ANY GitHub agent exists (not necessarily the email_agent itself)
                _has_github_agent = bool(_domain_agents.get('github'))
                _github_agent_name = (_domain_agents.get('github') or [''])[0]
                if _has_github_agent:
                    # ПРИОРИТЕТ 0: если есть уже сохранённые контакты без отправленных писем →
                    # сначала отправить им письма, не тратить цикл на новый поиск
                    _sent_set = {e.lower() for e in data.get('already_sent_emails', [])}
                    _unsent_contacts = []
                    for _c_str in contacts_list:
                        if '<' in _c_str and '>' in _c_str:
                            _c_email = _c_str.split('<')[1].split('>')[0].strip().lower()
                            if _c_email and _c_email not in _sent_set:
                                _unsent_contacts.append(_c_str)
                    if _unsent_contacts:
                        _unsent_names = ', '.join(
                            _c.split('<')[0].strip() for _c in _unsent_contacts[:3]
                        )
                        directives.append({
                            'goal': title, 'agent_domain': 'email',
                            'tool': 'send_outreach_email',
                            'task': (
                                f'В базе есть {len(_unsent_contacts)} контактов БЕЗ отправленных писем: {_unsent_names}...\n'
                                f'🚨 НЕМЕДЛЕННО отправь им письма — НЕ делай новый GitHub-поиск!\n'
                                f'ШАГ 1: list_email_contacts(status="new") — увидишь список\n'
                                f'ШАГ 2: send_outreach_email(goal="{title[:50]}", limit={min(len(_unsent_contacts), 5)}) '
                                f'— это отправит персональные письма всем новым контактам\n'
                                f'ШАГ 3: update_goal_progress после отправки'
                            ),
                            'reason': f'{len(_unsent_contacts)} контактов без писем (src=GitHub/другой)',
                        })
                        continue

                    # Нет несотправленных → ищем новых через GitHub
                    # Формируем query под реальную тематику цели
                    _gh_title_kw = title_l + ' ' + desc_l[:80]
                    if any(w in _gh_title_kw for w in ('ai', 'бот', 'bot', 'автоном', 'нейро', 'ml', 'machine')):
                        _gh_query = 'autonomous agent language:python repos:>10 followers:>10'
                    elif any(w in _gh_title_kw for w in ('тест', 'test', 'qa', 'beta', 'пользовател')):
                        _gh_query = 'software tester language:python repos:>5 followers:>5'
                    elif any(w in _gh_title_kw for w in ('разработ', 'developer', 'engineer', 'программ')):
                        _gh_query = 'indie developer language:python repos:>20 followers:>15'
                    elif any(w in _gh_title_kw for w in ('saas', 'стартап', 'startup', 'product')):
                        _gh_query = 'saas builder language:python repos:>10 followers:>20'
                    else:
                        _gh_query = 'language:python repos:>10 followers:>10'
                    directives.append({
                        'goal': title, 'agent_domain': 'github',
                        'tool': 'run_agent_action',
                        'task': (
                            f'GitHub-поиск новых контактов для цели «{title}» ({int(mc)}/{int(mt) if mt else "?"}).\n'
                            f'ШАГ 1: run_agent_action(action="search_users", params={{"query": "{_gh_query}"}}\n'
                            f'ШАГ 2: для КАЖДОГО найденного WITH EMAIL → save_email_contact(name=..., email=..., source="GitHub")\n'
                            f'ШАГ 3: send_outreach_email для каждого сохранённого контакта\n'
                            f'ВАЖНО: query должен содержать ТОЛЬКО GitHub-квалификаторы (language:, repos:, followers:, location:)\n'
                            f'ЗАПРЕЩЕНО передавать в query: email-адреса, имена людей из переписки, названия задач!'
                        ),
                        'reason': f'GitHub доступен ({_github_agent_name}), прогресс {int(mc)}/{int(mt) if mt else "?"}',
                    })
                    continue

                # Нет контактов → найти (с ротацией стратегий чтобы не зацикливаться)
                if n_contacts == 0:
                    # Ротация: определяем что уже пробовали по тексту истории
                    _rt = recent_txt  # уже lower
                    _tried_direct   = any(w in _rt for w in ('find_relevant', 'save_email_contact', 'контакт'))
                    _tried_telegram = any(w in _rt for w in ('telegram', 'тг', 'канал', 'group', 'группа'))
                    _tried_reddit   = any(w in _rt for w in ('reddit', 'форум', 'community', 'hackernews'))
                    _tried_content  = any(w in _rt for w in ('create_post', 'публикац', 'контент-', 'content_campaign'))
                    # Выбираем стратегию по ротации
                    if not _tried_direct:
                        _sm_tool = 'find_relevant_contacts_for_task'
                        _sm_task = (f'Найди контакты потенциальных пользователей для «{title}». '
                                    f'Ищи в Telegram-группах по теме, на форумах, HH.ru, в сообществах разработчиков. '
                                    f'Сохрани через save_email_contact.')
                        _sm_reason = 'нет контактов — прямой поиск (стратегия А)'
                    elif not _tried_telegram:
                        _sm_tool = 'research_topic'
                        _gt_short = title_l[:40]
                        _sm_task = (f'Исследуй: где тусуются люди для цели «{title}»? '
                                    f'Ищи: "{_gt_short} telegram community", "{_gt_short} discord server", '
                                    f'"{_gt_short} reddit forum", "{_gt_short} slack group". '
                                    f'По каждому найденному — add_task с конкретным планом "вступить и написать участникам".')
                        _sm_reason = 'нет контактов — поиск сообществ (стратегия Б)'
                    elif not _tried_content:
                        _sm_tool = 'create_post'
                        _sm_task = (f'Создай полезный контент-магнит для цели «{title}»: '
                                    f'короткий пост или статья на тему "{title_l[:50]}" с вопросом в конце (кто хочет протестировать?). '
                                    f'Вызови create_post, затем publish_to_telegram если канал подключён, иначе save_note с текстом поста.')
                        _sm_reason = 'нет контактов — контент-магнит (стратегия В)'
                    elif not _tried_reddit:
                        _sm_tool = 'web_search'
                        _gt_short = title_l[:40]
                        _sm_task = (f'Найди площадки для outreach цели «{title}»: '
                                    f'web_search "site:reddit.com {_gt_short}" и "site:t.me {_gt_short}". '
                                    f'Для каждой найденной площадки — add_task с инструкцией "написать в чат/ветку". '
                                    f'Сохрани лучшие контакты через save_email_contact.')
                        _sm_reason = 'нет контактов — Reddit/форумы (стратегия Г)'
                    else:
                        # Все стратегии использованы — смена сегмента
                        _sm_tool = 'research_topic'
                        _sm_task = (f'Предыдущие стратегии для «{title}» не дали контактов. '
                                    f'СМЕНА СЕГМЕНТА: подумай о ДРУГОЙ целевой аудитории или формате предложения. '
                                    f'research_topic("альтернативные каналы привлечения для {title_l[:40]}") '
                                    f'→ add_task с новым направлением.')
                        _sm_reason = 'все стратегии использованы — смена сегмента'
                    directives.append({
                        'goal': title, 'agent_domain': 'any',
                        'tool': _sm_tool,
                        'task': _sm_task,
                        'reason': _sm_reason,
                    })
                # Есть контакты, ни одного письма не отправлено
                elif total_sent == 0 and n_contacts > 0:
                    # Получаем campaign_id из строки данных если кампания активна
                    _active_cid = ''
                    for _ec_str in email_campaigns:
                        import re as _re_sm
                        _m_cid = _re_sm.search(r'#(\d+)', _ec_str)
                        if _m_cid:
                            _active_cid = f' (campaign_id={_m_cid.group(1)})'
                            break
                    _no_campaign_note = (f'Активная кампания{_active_cid} уже есть. '
                                        if has_active_campaign else '')
                    directives.append({
                        'goal': title, 'agent_domain': 'email',
                        'tool': 'send_outreach_email',
                        'task': (f'В базе {n_contacts} контактов, но не отправлено НИ ОДНОГО письма. '
                                 f'{_no_campaign_note}'
                                 f'НЕ вызывай start_email_campaign — кампания не отправляет письма, только создаёт шаблон. '
                                 f'ВЫЗОВИ send_outreach_email напрямую для персональной рассылки контактам из базы. '
                                 f'Параметры: goal="{title[:50]}", limit={min(n_contacts, 5)}.'),
                        'reason': f'{n_contacts} контактов в базе, emails_sent=0',
                    })
                # Кампания активна, есть ответы → обработать
                elif has_active_campaign and any('ответов=' in ec and 'ответов=0' not in ec for ec in email_campaigns):
                    directives.append({
                        'goal': title, 'agent_domain': 'email',
                        'tool': 'reply_to_outreach_email',
                        'task': f'Есть ответы на письма по кампании. Вызови check_emails → reply_to_outreach_email для ответов.',
                        'reason': 'есть ответы на письма',
                    })
                # Кампания активна, письма отправлены — выбираем умную стратегию
                else:
                    # Вычисляем пересечение: какие из известных контактов уже отправлены
                    _contact_emails_sm = set()
                    for _c_sm in contacts_list:
                        if '<' in _c_sm and '>' in _c_sm:
                            _em_sm = _c_sm.split('<')[1].split('>')[0].strip().lower()
                            _contact_emails_sm.add(_em_sm)
                    _unsent_set_sm = _contact_emails_sm - already_sent
                    _all_contacted = (
                        len(_unsent_set_sm) == 0 and n_contacts > 0
                        or (n_contacts > 0 and len(already_sent & _contact_emails_sm) >= max(1, len(_contact_emails_sm)) * 0.85)
                    )

                    if _all_contacted:
                        # Все известные контакты уже получили письма → ищем НОВЫХ из других источников
                        import re as _re_smd  # noqa: F811
                        _camp_ids = [_re_smd.search(r'#(\d+)', ec).group(1) for ec in email_campaigns if _re_smd.search(r'#(\d+)', ec)]
                        _camp_hint = f' (campaign_id={_camp_ids[0]})' if _camp_ids else ''
                        directives.append({
                            'goal': title, 'agent_domain': 'email',
                            'tool': 'find_relevant_contacts_for_task',
                            'task': (
                                f'БАЗА ИСЧЕРПАНА: {n_contacts} контактов уже получили письма '
                                f'(отправлено {int(total_sent)}, прогресс: {int(mc)}/{int(mt) if mt else "?"}).\n'
                                f'ОБЯЗАТЕЛЬНЫЙ СЛЕДУЮЩИЙ ШАГ — найти НОВЫХ людей из ДРУГОГО источника:\n'
                                f'  • Используй find_relevant_contacts_for_task с НОВЫМ запросом (Telegram-группы, форумы, HH.ru, GitHub)\n'
                                f'  • Не повторяй старый запрос — попробуй: "{title[:40]} telegram group", "beta testers", "QA engineers"\n'
                                f'  • После нахождения → сохрани через save_email_contact → отправь через send_outreach_email{_camp_hint}\n'
                                f'  • Или вызови check_emails чтобы проверить — может кто-то уже ответил'
                            ),
                            'reason': f'все {n_contacts} известных контактов emailed, ищем новых',
                        })
                    else:
                        # Есть несendted контакты → отправить именно им
                        _unsent_list = sorted(list(_unsent_set_sm))[:5]
                        _unsent_hint = (
                            f' ЕЩЁ НЕ ПОЛУЧИЛИ ПИСЬМА: {", ".join(_unsent_list)}.' if _unsent_list
                            else f' Отправь тем {len(_unsent_set_sm)} контактам из базы, кто ещё не получал.'
                        )
                        directives.append({
                            'goal': title, 'agent_domain': 'email',
                            'tool': 'send_outreach_email',
                            'task': (
                                f'Продолжи outreach по цели «{title}».\n'
                                f'{_unsent_hint}\n'
                                f'Вызови send_outreach_email для каждого из них персонально (тема + тело письма).\n'
                                f'Уже отправлено: {int(total_sent)} писем, прогресс: {int(mc)}/{int(mt) if mt else "?"}'
                            ),
                            'reason': f'есть {len(_unsent_set_sm)} несendted контактов в базе',
                        })

            # ── RESEARCH / FINANCE / NEWS goals ──
            elif any(w in full_l for w in _FINANCE_KW) or any(w in full_l for w in _NEWS_KW):
                # Выбираем аналитика: RSS → research → any
                research_agent = _agent_for('rss') or _agent_for('research') or _agent_for('any')

                # ── HIGH PROGRESS: цель ≥70% → подводим итог, не исследуем заново ──
                if progress >= 70:
                    tool = 'update_goal_progress'
                    task = (
                        f'Цель «{title}» уже на {int(progress)}%. НЕ НУЖНО заново исследовать. '
                        f'ЗАДАЧА: подведи ФИНАЛЬНЫЙ ИТОГ. '
                        f'1) Вызови save_note с подробным структурированным отчётом (заголовок «{title[:50]} — финальный отчёт», '
                        f'содержимое — минимум 5 пунктов: ключевые факты, тренды, риски, прогнозы, выводы). '
                        f'2) Вызови update_goal_progress(goal_title="{title[:50]}", new_progress=100, '
                        f'notes="Итог: [ключевые выводы]"). '
                        f'Если данных недостаточно — сначала сделай web_search, потом п.1 и п.2.'
                    )
                    directives.append({
                        'goal': title, 'agent_domain': 'research',
                        'tool': tool, 'task': task,
                        'reason': f'прогресс {int(progress)}% — пора завершать цель, не исследовать заново',
                    })
                else:
                    # Проверяем — у RSS-агента лента финансовая или нет
                    rss_is_finance = False
                    for p in profiles:
                        if p.get('name') == research_agent:
                            agent_caps_str = ' '.join(c.lower() for c in p.get('caps', []))
                            rss_is_finance = any(w in agent_caps_str for w in ('finance', 'rbc', 'tass', 'oil', 'moex', 'finam'))

                    if rss_is_finance and not _is_tool_failed('run_agent_action'):
                        tool = 'run_agent_action'
                        task = (f'Запусти run_agent_action для получения финансовых данных из RSS-ленты. '
                                f'Затем вызови update_goal_progress(notes="ключевые данные") для цели «{title}».')
                    else:
                        tool = 'web_search' if _is_tool_failed('research_topic') else 'research_topic'
                        task = (
                            f'Для цели «{title}» ({int(progress)}%): вызови {tool} с запросом о '
                            f'{title[:60]}. '
                            f'После получения данных — ОБЯЗАТЕЛЬНО вызови save_note с ПОДРОБНЫМ отчётом '
                            f'(не менее 5 пунктов: цены, тренды, прогнозы, ключевые игроки, выводы). '
                            f'Заголовок заметки: «{title[:50]} — отчёт». '
                            f'Затем вызови update_goal_progress(notes="краткий итог").'
                        )
                    directives.append({
                        'goal': title, 'agent_domain': 'research',
                        'tool': tool, 'task': task,
                        'reason': 'финансовый/новостной анализ через research_topic/web_search',
                    })

            # ── CONTENT goals ──
            elif any(w in full_l for w in _CONTENT_KW):
                directives.append({
                    'goal': title, 'agent_domain': 'content',
                    'tool': 'generate_marketing_content',
                    'task': f'Создай контент для цели «{title}». Вызови generate_marketing_content, затем create_post.',
                    'reason': 'контент/smm цель',
                })

            # ── DEV goals ──
            elif any(w in full_l for w in _DEV_KW):
                gh_agent = _agent_for('github') or _agent_for('any')
                directives.append({
                    'goal': title, 'agent_domain': 'github',
                    'tool': 'run_agent_action',
                    'task': f'Для цели «{title}» используй run_agent_action (GitHub API) для поиска разработчиков.',
                    'reason': 'dev/code цель',
                })

            # ── Generic goal ──
            else:
                directives.append({
                    'goal': title, 'agent_domain': 'any',
                    'tool': 'research_topic' if not _is_tool_failed('research_topic') else 'web_search',
                    'task': f'Изучи ситуацию по цели «{title}» ({int(progress)}%) и предложи конкретный следующий шаг.',
                    'reason': 'общая цель',
                })

        return directives

    async def _run_coordinator_dispatch(
        self, user, data: dict, real_agents: list, base_task_text: str, anchor, session,
    ) -> bool:
        """ASI-координатор: строит план для каждого агента по их способностям → запускает последовательно.

        Логика:
        1. Собирает профили агентов (интеграции, инструменты)
        2. LLM создаёт JSON-план: каждому агенту — конкретная задача под его интеграцию
        3. ASI объявляет план (живое сообщение)
        4. Выполняет каждый шаг через _exec_agent_for_director
        5. Отправляет результаты пользователю

        Returns True если хотя бы один шаг выполнен или токены кончились (anchor помечен).
        Returns False → вызывающий должен использовать fallback round-robin.
        """
        try:
            from ai_integration.autonomous_agent import (
                _exec_agent_for_director, _quick_ai_call_raw, _parse_agent_integrations,
            )

            # Собираем профили агентов для планировщика
            _profiles = []
            for ag in real_agents:
                try:
                    _caps = _parse_agent_integrations(
                        getattr(ag, 'user_api_keys', '') or '',
                        getattr(ag, 'python_code', '') or '',
                        getattr(ag, 'tools_allowed', '') or '',
                        getattr(ag, 'search_scope', '') or '',
                    )
                except Exception:
                    _caps = []
                try:
                    _tools_list = json.loads(getattr(ag, 'tools_allowed', '') or '[]')
                except Exception:
                    _tools_list = []
                _profiles.append({
                    'name': ag.name,
                    'id': getattr(ag, 'id', 0),
                    'job': ag.job_title or ag.specialization or '',
                    'desc': (getattr(ag, 'description', '') or '')[:200],
                    'spec': getattr(ag, 'specialization', '') or '',
                    'caps': _caps[:6],
                    'tools': _tools_list[:8],
                })

            _goals = data.get('goals', [])
            # Если data пустой (force-created anchor) — загружаем цели напрямую из DB
            if not _goals:
                try:
                    from models import Goal as _Goal_coord
                    _db_goals = session.query(_Goal_coord).filter(
                        _Goal_coord.user_id == user.id,
                        _Goal_coord.status == 'active',
                    ).order_by(_Goal_coord.created_at.desc()).limit(5).all()
                    _goals = [
                        {
                            'id': g.id, 'title': g.title,
                            'description': (g.description or '')[:150],
                            'progress': g.progress_percentage or 0,
                            'metric_current': g.metric_current or 0,
                            'metric_target': g.metric_target,
                        }
                        for g in _db_goals
                    ]
                    if _goals:
                        logger.info("[COORD] loaded %d goals from DB (data was empty)", len(_goals))
                except Exception as _gl_err:
                    logger.warning("[COORD] failed to load goals from DB: %s", _gl_err)
            _goals_str = '; '.join(
                f"{g['title']} ({g.get('progress', 0)}%, {g.get('metric_current', 0)}/{g.get('metric_target', '?')})"
                for g in _goals[:5]
            )
            _recent = data.get('recent_actions', [])
            _recent_txt = '\n'.join(_recent[-5:]) if _recent else 'нет'
            _known_contacts = len(data.get('known_contacts', []))
            _email_sent = data.get('total_emails_sent', 0)
            _failed_tools = data.get('failed_tools', {})
            _failed_str = ', '.join(f"{t}({n}x)" for t, n in _failed_tools.items()) if _failed_tools else 'нет'
            _per_agent_history = data.get('per_agent_history', {})
            _already_sent = data.get('already_sent_emails', [])
            _already_sent_str = ', '.join(_already_sent[:20]) if _already_sent else 'нет'
            _pending_replies = data.get('pending_replies', [])
            _unsent_contacts_data = data.get('unsent_contacts', [])
            _overworked_goals = data.get('overworked_goals', [])
            _neglected_goals = data.get('neglected_goals', [])

            # Блок несотправленных контактов — критический приоритет
            _unsent_contacts_str = ''
            if _unsent_contacts_data:
                _uc_names = [
                    _c.split('<')[0].strip() if '<' in _c else _c[:40]
                    for _c in _unsent_contacts_data[:5]
                ]
                _unsent_contacts_str = (
                    f"\n🟠 КОНТАКТЫ БЕЗ ПИСЬМА ({len(_unsent_contacts_data)} чел.): "
                    + ', '.join(_uc_names)
                    + "\n→ Email-агент ОБЯЗАН вызвать send_outreach_email — НЕ делать новый поиск!\n"
                )
            elif _email_sent > 0 and _goals:
                # Все контакты уже получили письма — нужны НОВЫЕ контакты
                _gap_needed = sum(
                    max(0, int((g.get('metric_target') or 0) - (g.get('metric_current') or 0)))
                    for g in _goals[:3] if g.get('metric_target')
                )
                if _gap_needed > 0:
                    _unsent_contacts_str = (
                        f"\n💡 ВСЕ {_email_sent} известных контактов уже получили письма. "
                        f"Осталось {_gap_needed} единиц до цели.\n"
                        "→ ПАЙПЛАЙН: RSS/GitHub-агент ДОЛЖЕН найти НОВЫХ людей → save_email_contact → email-агент отправляет им.\n"
                        "→ НЕ повторяй check_emails если уже делали недавно — ищи НОВЫЕ контакты!\n"
                    )

            # Блок приоритетных ответов на входящие — если есть replied без AI-ответа
            _pending_replies_str = ''
            if _pending_replies:
                _pr_lines = []
                for _pr_item in _pending_replies[:5]:
                    _pr_txt = _pr_item.get('reply_text', '') or '[текст не получен — нужен check_emails]'
                    _pr_lines.append(
                        f"  🆕 {_pr_item.get('name') or _pr_item.get('email')} ({_pr_item.get('email')}): \n"
                        f"     ответ=\"{_pr_txt[:1500]}\" (outreach_id={_pr_item.get('outreach_id')})" 
                    )
                _pending_replies_str = (
                    "\n🔴 НАИВЫСШИЙ ПРИОРИТЕТ — ОТВЕТИТЬ НА ВХОДЯЩИЕ ПИСЬМА:\n"
                    + '\n'.join(_pr_lines)
                    + "\n→ Email-агент ОБЯЗАН первым делом вызвать reply_to_outreach_email для этих контактов!\n"
                    + ("→ Если reply_text='[текст не получен]' → сначала вызови check_emails чтобы получить текст!\n"
                       if any(not p.get('reply_text') for p in _pending_replies) else '')
                )

            # Строим детальный профиль каждого агента с его личной историей действий
            _profiles_lines = []
            for p in _profiles:
                _hist = _per_agent_history.get(p['name'], [])
                _hist_str = (
                    ' | '.join(h[:100] for h in _hist[:3])
                    if _hist else 'нет истории'
                )
                _desc_part = f', описание: {p["desc"][:150]}' if p.get('desc') else ''
                _spec_part = f' [{p["spec"]}]' if p.get('spec') else ''
                # Добавляем конкретный RSS URL чтобы координатор понимал тематику ленты
                _ag_obj = next((a for a in real_agents if a.name == p['name']), None)
                _rss_url_val = ''
                if _ag_obj:
                    for _kline in (getattr(_ag_obj, 'user_api_keys', '') or '').splitlines():
                        if _kline.strip().upper().startswith('RSS_URL='):
                            _rss_url_val = _kline.split('=', 1)[1].strip()[:80]
                            break
                _rss_note = f', RSS={_rss_url_val}' if _rss_url_val else ''
                # Определяем может ли агент ОТПРАВЛЯТЬ письма
                _ag_api_keys = (getattr(_ag_obj, 'user_api_keys', '') or '') if _ag_obj else ''
                _keys_lower = _ag_api_keys.lower()
                # Gmail без app_password — только чтение IMAP
                _has_imap = ('gmail_user=' in _keys_lower or 'imap_' in _keys_lower or 'gmail_imap' in _keys_lower)
                _can_send = any(k in _keys_lower for k in ('smtp_', 'resend_api_key', 'sendgrid_', 'mailgun_', 'sparkpost_'))
                # Gmail с паролем приложения — может отправлять (GMAIL_PASS или GMAIL_APP_PASSWORD)
                if not _can_send and 'gmail_' in _keys_lower and 'gmail_user=' in _keys_lower:
                    _can_send = any(pk in _keys_lower for pk in ('gmail_pass=', 'gmail_app_password=', 'gmail_password='))
                # Яндекс и Mail.ru поддерживают SMTP нативно — если есть USER, значит умеет отправлять
                if not _can_send:
                    _can_send = 'yandex_user=' in _keys_lower or 'mailru_user=' in _keys_lower
                _send_note = (' [отправка+чтение email]' if _can_send else
                              ' [только чтение email, НЕ отправляет]' if _has_imap else '')
                _profiles_lines.append(
                    f'  - "{p["name"]}" ({p["job"]}{_spec_part}): интеграции=[{", ".join(p["caps"][:4]) or "нет"}]{_rss_note}{_send_note}'
                    f', инструменты=[{", ".join(p["tools"][:6]) if p["tools"] else (", ".join(p["caps"][:4]) + " через run_agent_action") if p["caps"] else "web_search, research_topic"}]'
                    f'{_desc_part}'
                )
            _n_agents = len(_profiles)
            _profiles_str = '\n'.join(_profiles_lines)

            # ── Anti-loop: вычисляем заблокированные по частоте инструменты ──
            import re as _re_al
            _agent_banned_tools: dict = {}
            for _p_al in _profiles:
                _hist_al = _per_agent_history.get(_p_al['name'], [])
                _tc: dict = {}
                for _h_al in _hist_al:
                    _tm_al = _re_al.search(r'\[([^\]]+)\]', _h_al)
                    if _tm_al:
                        for _t_al in _tm_al.group(1).split(','):
                            _t_al = _t_al.strip()
                            if _t_al:
                                _tc[_t_al] = _tc.get(_t_al, 0) + 1
                _banned_al = [t for t, n in _tc.items() if n >= 2]   # порог: 2+ раз = пора менять
                if _banned_al:
                    _agent_banned_tools[_p_al['name']] = _banned_al
            _banned_tools_str = ''
            # Агенты у которых ВСЕ инструменты заблокированы — пропустить в этом цикле, покрыть через ASI
            _fully_blocked_agents: set = set()
            if _agent_banned_tools:
                _banned_tools_str = '\n🚫 ЗАБЛОКИРОВАННЫЕ инструменты (2+ раз подряд — строго не назначать):\n'
                for _ag_bt, _tl_bt in _agent_banned_tools.items():
                    _banned_tools_str += f'  {_ag_bt}: НЕ использовать [{", ".join(_tl_bt)}] — дай ДРУГОЙ инструмент из каталога!\n'
                    # Проверяем: если все инструменты агента заблокированы → пропустить агента полностью
                    _ag_obj_bt = next((p for p in _profiles if p['name'] == _ag_bt), None)
                    if _ag_obj_bt:
                        _ag_all_tools = [t.lower().strip() for t in _ag_obj_bt.get('tools', [])]
                        if _ag_all_tools and all(t in [x.lower() for x in _tl_bt] for t in _ag_all_tools):
                            _fully_blocked_agents.add(_ag_bt)
                            _banned_tools_str += f'  ⛔ {_ag_bt}: все инструменты использованы → НЕ назначать в этом цикле. Используй ASI или другого агента.\n'

            # ── Форсированный outreach при стагнации цели ──
            _stagnant_instr = ''
            # Порог 80%: любая незавершённая цель при 4+ циклах без реального прогресса
            _stagnant_goals = [g for g in _goals if g.get('progress', 0) < 80 and g.get('metric_target')]
            if _stagnant_goals and len(_recent) >= 4:
                _sg = _stagnant_goals[0]
                _sg_progress = _sg.get('progress', 0)
                _sg_cur = _sg.get('metric_current', 0)
                _sg_tgt = _sg.get('metric_target', 0)
                _sg_gap = (_sg_tgt or 0) - (_sg_cur or 0)
                # Если уже есть отправленные письма — приоритет на check_emails + новые отправки
                if _already_sent and _email_sent > 0:
                    _stagnant_instr = (
                        f"\n⚠️ Цель «{_sg['title'][:50]}» = {_sg_progress}% ({int(_sg_cur or 0)}/{int(_sg_tgt or 0)}). "
                        f"Остаток: {int(_sg_gap)} единиц. Отправлено писем: {_email_sent}.\n"
                        "ОБЯЗАТЕЛЬНЫЕ шаги этого цикла (по приоритету):\n"
                        "  1. Email-агент: check_emails — проверить входящие ответы.\n"
                        "  2. Если ответов < gap — email-агент: send_outreach_email НОВЫМ контактам (не из уже_написали).\n"
                        "  3. RSS-агент: save_email_contact для НОВЫХ авторов/разработчиков из ленты → email-агент пишет им.\n"
                        "  4. НЕ делай поиск/research если уже есть unsent contacts в базе — сначала напиши им!\n"
                    )
                else:
                    _stagnant_instr = (
                        f"\n⚠️ СРОЧНО: Цель «{_sg['title'][:50]}» стагнирует ({_sg_progress}% за {len(_recent)}+ циклов). "
                        "ПРИНУДИТЕЛЬНЫЙ шаг этого цикла:\n"
                        "  1. Если нет активных кампаний → email-агент ОБЯЗАН вызвать start_email_campaign прямо сейчас.\n"
                        "  2. Если кампания есть → email-агент ОБЯЗАН вызвать send_outreach_email.\n"
                        "  3. RSS-агент ОБЯЗАН вызвать save_email_contact для найденных авторов/контактов.\n"
                    )

            _email_campaigns_str = '\n'.join(str(e) for e in data.get('email_campaigns', [])) or 'нет'

            # ── Контекст пользователя для координатора ──
            _user_profile_coord = data.get('user_profile', {})
            _user_profile_str_c = (_user_profile_coord.get('summary', '') or '') if _user_profile_coord else ''
            _user_rules_coord = data.get('user_rules', [])

            # ── Последние сообщения чата — чтобы координатор знал о свежем контексте диалога ──
            _recent_chat_str = ''
            try:
                _chat_ints = session.query(Interaction).filter(
                    Interaction.user_id == user.id,
                    Interaction.message_type.in_(['user', 'ai']),
                ).order_by(Interaction.id.desc()).limit(6).all()
                if _chat_ints:
                    _chat_lines = []
                    for _ci in reversed(_chat_ints):
                        _role = 'Пользователь' if _ci.message_type == 'user' else 'ASI'
                        _txt = (_ci.content or '')[:200].strip()
                        if _txt:
                            _chat_lines.append(f"  {_role}: {_txt}")
                    _recent_chat_str = '\n'.join(_chat_lines)
            except Exception as _rce:
                logger.debug("[COORD] chat history load failed: %s", _rce)

            # ── Подсказки по отсутствующим интеграциям (умный детектор) ──
            import os as _os_coord
            _missing_intg_coord = []
            _goals_lower_c = _goals_str.lower()
            # Аналитика/финансы: приоритет — Alpha Vantage (котировки), затем NewsAPI (новостной фон)
            _finance_kw = ('нефт', 'газ', 'нефтя', 'рынок', 'биржа', 'акци', 'финанс', 'трейдинг', 'oil', 'market', 'stock', 'crypto', 'валют', 'котировк', 'цена актив')
            _has_any = any(w in _goals_lower_c for w in _finance_kw)
            _has_alphavantage = any(
                any(k in (getattr(a, 'user_api_keys', '') or '').upper() for k in ('ALPHA_VANTAGE', 'ALPHAVANTAGE'))
                for a in real_agents
            )
            if _has_any and not _has_alphavantage:
                # Проверим RSS финансовый или нет
                _finance_rss_missing = False
                for _a_chk in real_agents:
                    for _kl in (getattr(_a_chk, 'user_api_keys', '') or '').splitlines():
                        if _kl.strip().upper().startswith('RSS_URL='):
                            _rss_val = _kl.split('=', 1)[1].strip().lower()
                            if not any(w in _rss_val for w in ('finance', 'tass', 'rbc', 'investing', 'oil', 'moex', 'finam', 'quote', 'market')):
                                _finance_rss_missing = True
                _missing_intg_coord.append(
                    "💡 Для котировок и рыночных данных (цены нефти, акций, крипты): "
                    "Дашборд → Настройки агента → API-ключи → Alpha Vantage (alphavantage.co, бесплатно 25 req/день). "
                    "Это основной источник числовых данных рынка."
                    + (" ⚠️ RSS агента сейчас не финансовый — web_search будет основным." if _finance_rss_missing else '')
                )
            if _has_any and not _os_coord.getenv('NEWSAPI_KEY'):
                _missing_intg_coord.append(
                    "💡 Дополнительно для новостного фона: NewsAPI (newsapi.org) даёт 100+ новостных источников. "
                    "Дашборд → Настройки агента → API-ключи → NewsAPI. Без него — web_search."
                )
            # Без GitHub-интеграции при поиске разработчиков
            _has_github_c_agent = any(
                any(k in (getattr(a, 'user_api_keys', '') or '').upper()
                    for k in ('GITHUB_TOKEN', 'GITHUB_ACCESS_TOKEN'))
                for a in real_agents
            )
            if any(w in _goals_lower_c for w in ('разработ', 'developer', 'github', 'программист')) and not _has_github_c_agent:
                _missing_intg_coord.append(
                    "⚠️ GitHub-интеграция не настроена — используй find_relevant_contacts_for_task или web_search. "
                    "Добавить GitHub: Дашборд → Настройки агента → API-ключи → GitHub."
                )
            # Telegram-канал: цели связанные с контентом/аудиторией, но нет канала
            _content_kw_c = ('контент', 'smm', 'пост', 'публикац', 'канал', 'аудитор', 'подписчик', 'продвижен')
            if any(w in _goals_lower_c for w in _content_kw_c):
                _has_tg_channel_c = any(
                    bool(getattr(u_chk, 'telegram_channel', None))
                    for u_chk in [user]
                )
                if not _has_tg_channel_c:
                    _missing_intg_coord.append(
                        "💡 Telegram-канал не подключён. Для публикаций постов: "
                        "Дашборд → Профиль → укажи @username канала → добавь бота как администратора."
                    )
            # Discord: те же темы
            if any(w in _goals_lower_c for w in _content_kw_c):
                _has_discord_c = bool(getattr(user, 'discord_webhook', None))
                if not _has_discord_c:
                    _missing_intg_coord.append(
                        "💡 Discord не подключён. Добавь webhook: Discord → канал → Настройки → "
                        "Интеграции → Webhooks → скопируй URL → Дашборд → Профиль."
                    )
            # Email/IMAP: нет email у агентов при поиске людей / outreach целях
            _outreach_kw_c = ('пользовател', 'тестировщик', 'клиент', 'подписчик', 'контакт', 'аутрич', 'outreach', 'рекрутинг')
            if any(w in _goals_lower_c for w in _outreach_kw_c):
                _has_email_c = any(
                    any(kw in (getattr(a, 'user_api_keys', '') or '').upper()
                        for kw in ('GMAIL_USER', 'YANDEX_USER', 'MAILRU_USER', 'IMAP_USER', 'IMAP_HOST'))
                    for a in real_agents
                )
                if not _has_email_c:
                    _missing_intg_coord.append(
                        "💡 Email не настроен у агентов. Для outreach: "
                        "Дашборд → Настройки агента → API-ключи → Gmail (вход + пароль приложения)."
                    )
            # Google Sheets / Airtable: аналитика и отчёты
            _data_kw_c = ('отчёт', 'аналитик', 'таблиц', 'данные', 'мониторинг продаж', 'crm')
            if any(w in _goals_lower_c for w in _data_kw_c):
                _has_sheets_c = any(
                    any(kw in (getattr(a, 'user_api_keys', '') or '').upper()
                        for kw in ('GOOGLE_SHEETS', 'GSPREAD', 'AIRTABLE'))
                    or ('gspread' in (getattr(a, 'python_code', '') or '').lower())
                    for a in real_agents
                )
                if not _has_sheets_c:
                    _missing_intg_coord.append(
                        "💡 Google Sheets / Airtable не подключены. "
                        "Для автоотчётов: Google Cloud Console → Service Account → credentials.json → в настройки агента."
                    )
            _missing_intg_str_c = ('\n\n⚠️ ВАЖНО для планирования (отсутствующие интеграции):\n'
                                   + '\n'.join(_missing_intg_coord)) if _missing_intg_coord else ''

            # ── Строим per-goal блок: для каждой цели — её тематика и подходящие инструменты ──
            _goal_blocks = []
            _DOMAIN_TOOLS = {
                'finance':  ('нефт', 'газ', 'биржа', 'акции', 'финанс', 'трейдинг', 'инвест', 'рынок', 'котировк', 'oil', 'stock', 'forex', 'крипто', 'crypto'),
                'news':     ('новост', 'мониторинг', 'тренды', 'медиа', 'сми', 'пресс', 'обзор', 'аналитик'),
                'dev':      ('разработ', 'программ', 'github', 'developer', 'код', 'приложен', 'репозитор'),
                'people':   ('пользовател', 'тестировщик', 'клиент', 'подписчик', 'аудитор', 'рекрутинг', 'нанять', 'участник'),
                'content':  ('контент', 'smm', 'пост', 'публикац', 'канал', 'telegram', 'discord'),
                'sales':    ('продаж', 'лид', 'партнёр', 'сделка', 'b2b', 'outreach'),
                'learning': ('изучить', 'курс', 'обучен', 'навык', 'книг', 'читать', 'сертификат', 'урок', 'освоить', 'учёб', 'тренинг', 'английск'),
                'health':   ('спорт', 'тренировк', 'похудеть', 'здоровь', 'бег', 'марафон', 'питание', 'диета', 'сон', 'медитац', 'фитнес', 'йога', 'вес'),
                'personal': ('путешеств', 'привычк', 'хобби', 'творч', 'музыка', 'рисован', 'дневник', 'саморазвит', 'мечт', 'личный проект'),
            }
            _DOMAIN_TOOL_MAP = {
                'finance':  'Используй: research_topic (основной!), get_news_trends, web_search. Если есть RSS с финансовой лентой — run_agent_action первым. НЕ email для анализа.',
                'news':     'Используй: get_news_trends, web_search, research_topic, run_agent_action (RSS). НЕ email как основное.',
                'dev':      'Используй: run_agent_action(action="search_users", params={"query":"language:python followers:>20"}) → save_email_contact → send_outreach_email. GitHub Token есть у агента.',
                'people':   'Если у агента GITHUB_TOKEN → run_agent_action(action="search_users", params={"query":"language:python followers:>20"}) → save_email_contact → send_outreach_email. Иначе: find_relevant_contacts_for_task, start_email_campaign.',
                'content':  'Используй: generate_marketing_content, create_post, publish_to_telegram/discord, start_content_campaign.',
                'sales':    'Используй: find_partners, find_relevant_contacts_for_task, send_outreach_email, start_email_campaign.',
                'learning': 'Используй: research_topic("[тема] best course / how to learn") → web_search → add_task("урок X до [дата]") → save_note(результат). НЕ email-рассылки.',
                'health':   'Используй: research_topic("программа тренировок / план питания") → add_task("тренировка [время]") → save_note("результат") → update_goal_progress. НЕ email-рассылки.',
                'personal': 'Используй: research_and_plan("[цель] — с чего начать?") → add_task → save_note(прогресс) → publish_to_telegram(дневник). НЕ email-рассылки.',
            }
            for _g_plan in _goals[:5]:
                _gt = (_g_plan.get('title') or '').lower()
                _gd = (_g_plan.get('description') or '').lower()
                _gfull = _gt + ' ' + _gd
                _domain = 'people'  # дефолт
                for _dom, _kws in _DOMAIN_TOOLS.items():
                    if any(w in _gfull for w in _kws):
                        _domain = _dom
                        break
                _goal_blocks.append(
                    f"  Цель «{_g_plan['title'][:50]}» ({_g_plan.get('progress',0)}%) "
                    f"→ домен: {_domain} → {_DOMAIN_TOOL_MAP[_domain]}"
                )
            _goal_domain_str = '\n'.join(_goal_blocks) if _goal_blocks else ''

            # ── Pre-computed State Machine: вычисляем директивы из реального состояния БД ──
            _sm_directives = self._compute_state_directives(_goals, data, _profiles)
            # _situation_str и _strategy_map_str строятся НИЖЕ — после multi-cycle analysis

            # Количество шагов которые просим у LLM-планировщика: min(1 per goal, agents).
            # Если агентов больше чем целей — добавляем дополнительные шаги для отстающих целей.
            _n_plan_steps = max(len(_goals[:5]), min(_n_agents, 8))

            # ── Детектор деградированных агентов (только 2 последних) ──
            import re as _re_deg
            _degraded_agents_coord = set()
            for _pn_deg, _hn_deg in _per_agent_history.items():
                _recent2_deg = list(_hn_deg)[-2:]
                _fc_deg = sum(1 for h in _recent2_deg if 'технические трудности' in h.lower() or 'не успел' in h.lower())
                if _fc_deg >= 2:
                    _degraded_agents_coord.add(_pn_deg)
            _degraded_note = (f"⚠️ Агенты с недавними ошибками (возможно временная проблема): {', '.join(_degraded_agents_coord)}\n"
                             f"  → Попробуй назначить им другой инструмент или задачу — они могут справиться.\n"
                             if _degraded_agents_coord else '')

            # ── Multi-cycle strategy analysis: извлекаем ВСЕ инструменты из ВСЕХ циклов ──
            # Формат истории: "19.03 00:34 [web_search, research_topic] текст..."
            _known_tool_words = {'web_search', 'research_topic', 'run_agent_action',
                                 'check_emails', 'find_relevant_contacts_for_task',
                                 'save_email_contact', 'send_outreach_email',
                                 'get_news_trends', 'update_goal_progress', 'save_note',
                                 'add_task', 'create_post', 'add_email_leads',
                                 'reply_to_outreach_email', 'generate_marketing_content',
                                 'delegate_task', 'start_email_campaign', 'quick_topic_search',
                                 'publish_to_telegram', 'list_email_contacts'}
            _all_cycles_tools: dict = {}  # {agent: [[cycle1_tools], [cycle2_tools], ...]}
            _all_cycles_summaries: dict = {}  # {agent: [(tools, text_summary), ...]}
            for _pn_mc, _hn_mc in _per_agent_history.items():
                _cycles = []
                _summaries = []
                for _entry_mc in _hn_mc:
                    _tm_mc = _re_al.search(r'^\d{2}\.\d{2}\s+\d{2}:\d{2}\s+\[([^\]]+)\]', _entry_mc)
                    if not _tm_mc:
                        _tm_mc = _re_al.search(r'\[([^\]]+)\]', _entry_mc)
                    if _tm_mc:
                        _tl_mc = [t.strip() for t in _tm_mc.group(1).split(',') if t.strip()]
                        _tl_mc = [t for t in _tl_mc if '_' in t or t in _known_tool_words]
                        if _tl_mc:
                            _cycles.append(_tl_mc)
                            # Извлекаем краткое описание действия (после [tools])
                            _txt_part = _entry_mc[_tm_mc.end():].strip()[:120]
                            _summaries.append((_tl_mc, _txt_part))
                if _cycles:
                    _all_cycles_tools[_pn_mc] = _cycles
                    _all_cycles_summaries[_pn_mc] = _summaries

            # Классификация стратегий высокого уровня
            _STRATEGY_MAP = {
                'direct_search': {'find_relevant_contacts_for_task', 'web_search'},
                'github_search': {'run_agent_action'},
                'rss_analysis': {'run_agent_action', 'get_news_trends'},
                'email_outreach': {'send_outreach_email', 'start_email_campaign'},
                'email_check': {'check_emails', 'reply_to_outreach_email'},
                'research': {'research_topic', 'quick_topic_search'},
                'content': {'create_post', 'generate_marketing_content', 'publish_to_telegram'},
                'data_save': {'save_note', 'save_email_contact', 'add_email_leads'},
                'task_mgmt': {'add_task', 'delegate_task', 'update_goal_progress'},
            }
            # Подсчитываем сколько раз каждая стратегия использовалась ВСЕМИ агентами
            _strategy_usage: dict = {}  # {strategy_name: count}
            _strategy_never_tried: list = []
            _all_used_tools_ever: set = set()
            for _cycles_list in _all_cycles_tools.values():
                for _cycle_tools in _cycles_list:
                    _all_used_tools_ever.update(_cycle_tools)
                    for _strat_name, _strat_tools in _STRATEGY_MAP.items():
                        if _strat_tools & set(_cycle_tools):
                            _strategy_usage[_strat_name] = _strategy_usage.get(_strat_name, 0) + 1
            for _sn in _STRATEGY_MAP:
                if _sn not in _strategy_usage:
                    _strategy_never_tried.append(_sn)

            # Строим блок "СТРАТЕГИЧЕСКАЯ КАРТА" вместо жёсткого плана
            _strategy_lines = []
            if _strategy_usage or _all_cycles_summaries:
                _strategy_lines.append("📋 СТРАТЕГИЧЕСКАЯ КАРТА (что команда УЖЕ делала за последние циклы):")
                # Перечисляем что делал каждый агент во ВСЕХ циклах
                for _ag_s, _summs in _all_cycles_summaries.items():
                    _strategy_lines.append(f"  {_ag_s}:")
                    for _idx_s, (_tools_s, _txt_s) in enumerate(_summs[-4:], 1):  # последние 4
                        _strategy_lines.append(f"    цикл -{len(_summs)-_idx_s-len(_summs)+4}: [{', '.join(_tools_s[:3])}] {_txt_s}")
                # Какие стратегии перегружены
                _overused = [f"{s} ({n}x)" for s, n in sorted(_strategy_usage.items(), key=lambda x: -x[1]) if n >= 2]
                if _overused:
                    _strategy_lines.append(f"\n  ⚠️ ПЕРЕГРУЖЕННЫЕ подходы (повторяются циклами): {', '.join(_overused)}")
                    _strategy_lines.append(f"     → Эти подходы УЖЕ испробованы многократно. Результата явно недостаточно.")
                    _strategy_lines.append(f"     → ЗАПРЕЩЕНО назначать то же самое. Придумай принципиально другой путь.")
                if _strategy_never_tried:
                    _nice_names = {
                        'direct_search': 'прямой поиск контактов',
                        'github_search': 'поиск через GitHub',
                        'rss_analysis': 'анализ RSS/новостных лент',
                        'email_outreach': 'email-рассылка',
                        'email_check': 'проверка входящей почты',
                        'research': 'глубокое исследование темы',
                        'content': 'создание контента (посты, статьи)',
                        'data_save': 'сохранение данных/контактов',
                        'task_mgmt': 'управление задачами/делегирование',
                    }
                    _nt_nice = [_nice_names.get(s, s) for s in _strategy_never_tried]
                    _strategy_lines.append(f"\n  💡 ЕЩЁ НЕ ПРОБОВАЛИ: {', '.join(_nt_nice)}")
                    _strategy_lines.append(f"     → Приоритет: попробуй один из этих подходов в этом цикле!")
            _strategy_map_str = '\n'.join(_strategy_lines) + '\n' if _strategy_lines else ''

            # Last cycle tools (для базовой anti-repeat совместимости)
            _last_cycle_tools: dict = {}
            for _pn_lr, _cycles_lr in _all_cycles_tools.items():
                if _cycles_lr:
                    _last_cycle_tools[_pn_lr] = _cycles_lr[-1]

            _TOOL_ALTERNATIVES = {
                'run_agent_action': ['research_topic', 'web_search', 'get_news_trends', 'find_relevant_contacts_for_task'],
                'research_topic':   ['web_search', 'get_news_trends', 'run_agent_action'],
                'web_search':       ['research_topic', 'get_news_trends', 'find_relevant_contacts_for_task'],
                'get_news_trends':  ['research_topic', 'web_search', 'run_agent_action'],
                'find_relevant_contacts_for_task': ['web_search', 'research_topic', 'run_agent_action'],
                'check_emails':     ['send_outreach_email', 'find_relevant_contacts_for_task'],
                'send_outreach_email': ['check_emails', 'reply_to_outreach_email', 'find_relevant_contacts_for_task'],
            }

            # ── Строим контекст ситуации (вместо жёсткого SM-плана) ──
            # SM-директивы → мягкие подсказки о состоянии БД, а не команды
            _situation_lines = []
            if _sm_directives:
                _situation_lines.append("📊 ТЕКУЩАЯ СИТУАЦИЯ (факты из БД — используй для принятия решений):")
                for _d in _sm_directives:
                    _situation_lines.append(
                        f"  • Цель «{_d['goal'][:50]}»: {_d['reason']}."
                    )
            _situation_str = '\n'.join(_situation_lines) + '\n' if _situation_lines else ''

            _anti_repeat_str = ''
            if _last_cycle_tools:
                _ar_lines = []
                for _pn_ar, _tl_ar in _last_cycle_tools.items():
                    _ar_lines.append(f"  {_pn_ar}: [{', '.join(_tl_ar[:4])}]")
                _anti_repeat_str = (
                    "\n🔄 ПОСЛЕДНИЙ ЦИКЛ (для справки — НЕ повторять буквально):\n"
                    + '\n'.join(_ar_lines) + '\n'
                )

            # ── Блок ротации целей: если одна цель доминирует — переключайся ──
            _goal_rotation_str = ''
            if _overworked_goals:
                _ow_str = ', '.join(_overworked_goals)
                _ng_str = ', '.join(f'«{g}»' for g in _neglected_goals[:4]) if _neglected_goals else 'нет'
                _goal_rotation_str = (
                    f"\n🔄 РОТАЦИЯ ЦЕЛЕЙ:\n"
                    f"  Перегруженные (команда зациклилась): {_ow_str}\n"
                    f"  Заброшенные (давно не работали): {_ng_str}\n"
                    f"  ⚠️ В ЭТОМ цикле — ОБЯЗАТЕЛЬНО включи хотя бы 1 заброшенную цель."
                    f" Каждый агент должен работать над РАЗНЫМИ целями.\n"
                )

            # ── АНАЛИЗ ЭФФЕКТИВНОСТИ: связываем действия агентов с реальным прогрессом целей ──
            _effectiveness_str = ''
            try:
                # Смотрим изменение метрик целей за последние 24-48 часов
                from models import Goal as _Goal_eff
                _progress_facts = []
                _no_progress_goals = []
                for _g_eff in _goals[:5]:
                    _g_id = _g_eff.get('id')
                    _g_title = _g_eff['title'][:50]
                    _curr_val = _g_eff.get('metric_current', 0)
                    _curr_prog = _g_eff.get('progress', 0)
                    # Ищем недавние действия агентов по этой цели
                    _relevant_actions = []
                    for _ag_n, _ag_h in _per_agent_history.items():
                        for _h_entry in _ag_h[:4]:  # последние 4 действия агента
                            # Проверяем упоминание цели или ключевых слов
                            _h_lower = _h_entry.lower()
                            _g_keywords = [w for w in _g_title.lower().split() if len(w) > 3][:3]
                            if any(kw in _h_lower for kw in _g_keywords) or 'цел' in _h_lower:
                                _relevant_actions.append((_ag_n, _h_entry[17:150]))  # skip timestamp
                    # Проверка прогресса: если metric_current > 0 или progress растёт
                    if _curr_val > 0 and _relevant_actions:
                        _progress_facts.append({
                            'goal': _g_title,
                            'value': f"{_curr_val}/{_g_eff.get('metric_target', '?')}",
                            'progress': _curr_prog,
                            'actions': _relevant_actions[:3],
                        })
                    elif _curr_prog < 20 and len(_relevant_actions) >= 2:  # застрявшая цель с активностью
                        _no_progress_goals.append({
                            'goal': _g_title,
                            'progress': _curr_prog,
                            'actions': _relevant_actions[:2],
                        })
                
                # Поиск синергии: агент А нашёл данные → агент B использовал → результат
                _synergy_patterns = []
                _finder_keywords = ('нашёл', 'найден', 'сохранил', 'save', 'собрал', 'extracted')
                _user_keywords = ('отправил', 'письмо', 'email', 'опубликов', 'ответ', 'reply', 'send')
                for _ag1, _h1_list in _per_agent_history.items():
                    for _h1 in _h1_list[:3]:
                        if any(kw in _h1.lower() for kw in _finder_keywords):
                            # Ищем кто использовал эти данные
                            for _ag2, _h2_list in _per_agent_history.items():
                                if _ag1 == _ag2:
                                    continue
                                for _h2 in _h2_list[:3]:
                                    if any(kw in _h2.lower() for kw in _user_keywords):
                                        # Нашли потенциальную связку
                                        _synergy_patterns.append({
                                            'finder': _ag1,
                                            'action1': _h1[17:100],
                                            'user': _ag2,
                                            'action2': _h2[17:100],
                                        })
                                        break
                                if _synergy_patterns and _synergy_patterns[-1]['finder'] == _ag1:
                                    break  # нашли связку для этого действия
                
                # Формируем блок
                _eff_lines = []
                if _progress_facts:
                    _eff_lines.append("📊 АНАЛИЗ ЭФФЕКТИВНОСТИ (что РЕАЛЬНО работает):")
                    for _pf in _progress_facts[:3]:
                        _eff_lines.append(f"  ✅ «{_pf['goal']}» ({_pf['progress']}%): {_pf['value']}")
                        _eff_lines.append(f"     Эффективные действия (ПОВТОРИ эту тактику):")
                        for _ag, _act in _pf['actions']:
                            _eff_lines.append(f"       • {_ag}: {_act}")
                
                if _synergy_patterns:
                    if not _eff_lines:
                        _eff_lines.append("📊 АНАЛИЗ ЭФФЕКТИВНОСТИ:")
                    _eff_lines.append("\n  💡 СИНЕРГИЯ (командная работа — используй!):")
                    for _sp in _synergy_patterns[:2]:
                        _eff_lines.append(f"     {_sp['finder']} нашёл → {_sp['user']} использовал:")
                        _eff_lines.append(f"       1) {_sp['action1']}")
                        _eff_lines.append(f"       2) {_sp['action2']}")
                        _eff_lines.append(f"     → Делегируй: {_sp['finder']} ищет данные → передаёт {_sp['user']} для действия!")
                
                if _no_progress_goals:
                    if not _eff_lines:
                        _eff_lines.append("📊 АНАЛИЗ ЭФФЕКТИВНОСТИ:")
                    _eff_lines.append("\n  ⚠️ НЕЭФФЕКТИВНО (активность есть, но прогресса НЕТ):")
                    for _npg in _no_progress_goals[:2]:
                        _eff_lines.append(f"     «{_npg['goal']}» ({_npg['progress']}%) — действия не дают результата:")
                        for _ag, _act in _npg['actions']:
                            _eff_lines.append(f"       • {_ag}: {_act}")
                        _eff_lines.append(f"     → СМЕНИТЬ ТАКТИКУ: попробуй ДРУГОЙ инструмент или подход к этой цели!")
                
                if not _eff_lines and len(_recent) >= 3:
                    # Если нет явного прогресса но есть активность
                    _eff_lines.append("📊 АНАЛИЗ ЭФФЕКТИВНОСТИ:")
                    _eff_lines.append("  ⚠️ Последние циклы: действий много, но метрики целей НЕ растут.")
                    _eff_lines.append("     КРИТИЧЕСКИ ВАЖНО: текущий план НЕ работает — команда повторяет одно и то же.")
                    _eff_lines.append("     ОБЯЗАТЕЛЬНО смени стратегию:")
                    _eff_lines.append("       • Если искали контакты через поиск → попробуй RSS или сообщества")
                    _eff_lines.append("       • Если писали письма → проверь ответы (check_emails) или смени шаблон")
                    _eff_lines.append("       • Если анализировали данные → переходи к действию (публикация, email)")
                    _eff_lines.append("       • Если работали только с одним инструментом → дай агенту ДРУГОЙ из его набора")
                
                if _eff_lines:
                    _effectiveness_str = '\n' + '\n'.join(_eff_lines) + '\n\n'
            except Exception as _eff_err:
                logger.debug("[COORD] effectiveness analysis: %s", _eff_err)

            _plan_prompt = (
                f"Команда: {_n_agents} агентов:\n{_profiles_str}\n\n"
                + (f"Пользователь: {_user_profile_str_c}\n\n" if _user_profile_str_c else '')
                + (f"Последний диалог с пользователем (контекст):\n{_recent_chat_str}\n\n" if _recent_chat_str else '')
                + _effectiveness_str
                + f"{_degraded_note}"
                + _pending_replies_str
                + _unsent_contacts_str
                + f"{_strategy_map_str}\n"
                + f"{_situation_str}\n"
                + (f"Типы инструментов по доменам целей:\n{_goal_domain_str}\n\n" if _goal_domain_str else '')
                + f"{_goal_rotation_str}"
                + f"{_anti_repeat_str}"
                f"Контекст: контактов={_known_contacts}, писем_отправлено={_email_sent}, "
                f"уже_написали=[{_already_sent_str[:300]}]\n"
                f"Кампании: {_email_campaigns_str}\n"
                f"{_banned_tools_str}"
                f"Инструменты с ошибками (попробуй альтернативу): {_failed_str}\n"
                + (f"Правила: {'; '.join(_user_rules_coord[:2])}\n" if _user_rules_coord else '')
                + (
                    "⚡ ПРИОРИТЕТ: Есть отправленные письма — "
                    "агент с IMAP должен ПЕРВЫМ делом вызвать check_emails! "
                    "Ответившие контакты (replied/interested) — нужен reply/negotiate, не новое outreach.\n"
                    if _already_sent and _email_sent > 0 and
                    any(any(kw in (getattr(a, 'user_api_keys', '') or '').lower()
                            for kw in ('gmail_user=', 'imap_')) for a in real_agents)
                    else ''
                )
                + f"\n=== ТВОЯ ЗАДАЧА ===\n"
                "Ты — стратег. НЕ следуй шаблону. Думай САМОСТОЯТЕЛЬНО.\n\n"
                "Посмотри на СТРАТЕГИЧЕСКУЮ КАРТУ выше: там видно, что команда делала в прошлых циклах.\n"
                "Если подход повторялся 2+ раз и не дал прогресса — он НЕ РАБОТАЕТ. Придумай другой.\n\n"
                "ПЕРЕД генерацией плана ответь себе (мысленно):\n"
                "  1. Какие подходы команда уже пробовала? (см. СТРАТЕГИЧЕСКАЯ КАРТА)\n"
                "  2. Какие подходы ЕЩЁ НЕ пробовали? (см. 'ЕЩЁ НЕ ПРОБОВАЛИ')\n"
                "  3. Почему прошлые действия не дали результата? Что пошло не так?\n"
                "  4. Какой ПРИНЦИПИАЛЬНО ДРУГОЙ путь к цели существует?\n\n"
                "КЛЮЧЕВЫЕ ПРАВИЛА:\n"
                "• Каждый агент работает своими интеграциями. Назначай задачи ПОД его реальные возможности.\n"
                "• Задача = конкретное ДЕЙСТВИЕ (найти X, написать Y, проанализировать Z), не 'изучить' или 'исследовать в целом'.\n"
                "• НЕ пиши письма тем кто уже в списке уже_написали.\n"
                "• Если у агента [отправка+чтение email] и писем > 5 — сначала check_emails.\n"
                "• GitHub search query: ТОЛЬКО language:X, repos:>N, followers:>N, location:X. Без email/имён.\n"
                "• Агент БЕЗ интеграций: web_search, research_topic, find_relevant_contacts_for_task, save_note, add_task, create_post. НЕ check_emails, НЕ run_agent_action.\n\n"
                "СТРАТЕГИЧЕСКОЕ МЫШЛЕНИЕ:\n"
                "• Если прямой поиск+рассылка не работает → создай контент-магнит (пост, статья), чтобы люди САМИ откликнулись.\n"
                "• Если рассылка игнорируется → проблема в ценностном предложении. Пусть агент исследует: что РЕАЛЬНО нужно этим людям?\n"
                "• Если поиск в одном месте истощён → смени площадку: Telegram группы, Discord, Reddit, HN, LinkedIn, конференции.\n"
                "• Используй делегирование: один агент находит данные → передаёт другому для действия.\n"
                "• Непрямые пути: что СОЗДАТЬ чтобы нужные люди появились? Что ПОНЯТЬ прежде чем действовать?\n"
                "• Ты не ограничен списком подходов — генерируй СВОИ стратегии исходя из контекста цели.\n\n"
                f"ТОЧНЫЕ названия целей: {'; '.join(repr(g['title']) for g in _goals[:5])}\n"
                f"Верни JSON-массив из {_n_plan_steps} шагов (min 1 шаг на каждую активную цель).\n"
                '[{"agent": "имя", "task": "конкретная задача 2-3 предл.", "tool": "инструмент", "goal": "точное_название"}]'
            )

            try:
                # Больше токенов когда шагов больше (180 на шаг, минимум 500, максимум 1200)
                _plan_max_tokens = min(max(500, _n_plan_steps * 180), 1200)
                _plan_json = await asyncio.wait_for(
                    _quick_ai_call_raw([{"role": "user", "content": _plan_prompt}], max_tokens=_plan_max_tokens),
                    timeout=30,
                )
            except Exception as _pe:
                logger.warning("[COORD] plan generation failed: %s", _pe)
                return False

            import re as _re_coord
            _plan = []
            try:
                _m = _re_coord.search(r'\[[\s\S]*?\]', _plan_json or '')
                if _m:
                    _plan = json.loads(_m.group())
            except Exception as _je:
                logger.warning("[COORD] JSON parse: %s — raw: %s", _je, (_plan_json or '')[:200])
                return False

            if not _plan:
                return False

            # Дедупликация плана: если один агент назначен дважды с одинаковым инструментом — бессмысленное повторение
            _seen_agent_tool: set = set()
            _plan_deduped = []
            for _p in _plan:
                _ak = (_p.get('agent', '').strip().lower(), (_p.get('tool') or '').strip().lower())
                if _ak[0] and _ak not in _seen_agent_tool:
                    _seen_agent_tool.add(_ak)
                    _plan_deduped.append(_p)
                elif _ak[0]:
                    logger.info("[COORD] dedup: skip dup step %s/%s", _p.get('agent'), _p.get('tool'))
            _plan = _plan_deduped if _plan_deduped else _plan

            # ── Force-reply: если есть входящие без AI-ответа — email-агент ОБЯЗАН ответить ПЕРВЫМ ──
            # Это жёсткий пост-фильтр: LLM-план может игнорировать pending_replies → override
            if _pending_replies:
                _force_reply_agent = None
                for _a_fr in real_agents:
                    _keys_fr = (getattr(_a_fr, 'user_api_keys', '') or '').lower()
                    _can_send_fr = (
                        any(k in _keys_fr for k in ('smtp_', 'resend_api_key', 'sendgrid_', 'mailgun_'))
                        or ('gmail_user=' in _keys_fr and any(pk in _keys_fr for pk in ('gmail_pass=', 'gmail_app_password=', 'gmail_password=')))
                        or 'yandex_user=' in _keys_fr or 'mailru_user=' in _keys_fr
                    )
                    if _can_send_fr:
                        _force_reply_agent = _a_fr.name
                        break
                if _force_reply_agent:
                    # Проверяем: есть ли уже reply_to_outreach_email в плане для этого агента
                    _already_has_reply = any(
                        p.get('agent', '').strip() == _force_reply_agent
                        and p.get('tool', '') in ('reply_to_outreach_email', 'check_emails')
                        for p in _plan
                    )
                    if not _already_has_reply:
                        _pr0 = _pending_replies[0]
                        _pr0_txt = _pr0.get('reply_text') or ''
                        _reply_task = (
                            f"Ответь на входящее письмо от {_pr0.get('name') or _pr0.get('email')}"
                            f" (outreach_id={_pr0.get('outreach_id')})"
                        )
                        if _pr0_txt:
                            _reply_task += f': «{_pr0_txt[:120]}»'
                        else:
                            _reply_task += '. Сначала вызови check_emails чтобы получить текст ответа.'
                        _reply_tool = 'reply_to_outreach_email' if _pr0_txt else 'check_emails'
                        # Удаляем текущий шаг email-агента — заменяем на reply
                        _plan = [p for p in _plan if p.get('agent', '').strip() != _force_reply_agent]
                        _plan.insert(0, {
                            'agent': _force_reply_agent,
                            'tool': _reply_tool,
                            'task': _reply_task,
                            'goal': _pr0.get('goal') or (_goals[0]['title'] if _goals else 'ответить на входящие'),
                        })
                        logger.info("[COORD] force-reply prepended: %s → %s (outreach_id=%s)",
                                    _force_reply_agent, _reply_tool, _pr0.get('outreach_id'))

            logger.info("[COORD] plan accepted as-is (no corrections): %s", [(p.get('agent'), p.get('tool')) for p in _plan])

            # ── ASI fallback: цели без исполнителя в плане ──
            # Если цель есть, а в плане никто её не покрывает → ASI берёт её сам
            _covered_goals = {(_p.get('goal') or '').strip().lower() for _p in _plan}
            for _sd_fb in _sm_directives:
                _sd_goal_fb = (_sd_fb.get('goal') or '').strip()
                if _sd_goal_fb.lower() not in _covered_goals:
                    logger.info("[COORD] post-filter: goal '%s' uncovered → ASI fallback", _sd_goal_fb[:40])
                    _plan.append({
                        'agent': 'ASI',
                        'tool': _sd_fb.get('tool', 'research_topic'),
                        'task': _sd_fb.get('task', f'Проанализируй цель «{_sd_goal_fb}» и предложи следующий конкретный шаг.'),
                        'goal': _sd_goal_fb,
                    })

            logger.info("[COORD] user %d: plan=%s (sm_directives=%s)", user.id,
                        [(p.get('agent'), p.get('tool')) for p in _plan],
                        [(d.get('goal', '')[:30], d.get('tool')) for d in _sm_directives])

            # ── Биллинг + anchor.delivered_at ПЕРЕД первым AI-вызовом ──
            from token_service import has_enough_tokens as _het_c, spend_tokens as _sp_c
            from config import FREE_ACCESS_MODE as _FAM_c
            if not _FAM_c:
                if not _het_c(user.telegram_id, 'proactive_message', session=session):
                    logger.info("[COORD] user %d: not enough tokens", user.id)
                    anchor.delivered_at = datetime.now(timezone.utc)
                    session.commit()
                    return True
                _sp_c(user.telegram_id, 'proactive_message',
                      description='coordinator_autopilot', session=session, auto_commit=False)
            anchor.delivered_at = datetime.now(timezone.utc)
            try:
                session.commit()
            except Exception:
                try:
                    session.rollback()
                except Exception:
                    pass

            # ── AAL запись ──
            from models import AgentActivityLog as _AAL_c, Session as _AAL_Sess
            _aal_id_c = None
            try:
                # Используем отдельную сессию — основная может быть в ненадёжном состоянии
                _aal_sess = _AAL_Sess()
                try:
                    _aal_c = _AAL_c(
                        user_id=user.id,
                        activity_type='goal_autopilot_dispatch',
                        title=f'[Координатор] → {", ".join(p.get("agent", "?") for p in _plan)}'[:300],
                        content=base_task_text[:600],
                        target=(anchor.source or '')[:300],
                        status='in_progress',
                        ref_id=None,
                    )
                    _aal_sess.add(_aal_c)
                    _aal_sess.commit()
                    _aal_id_c = _aal_c.id
                    logger.info("[COORD] AAL created id=%s for user %d", _aal_id_c, user.id)
                except Exception as _aal_err:
                    logger.warning("[COORD] AAL create failed: %s", _aal_err)
                    try:
                        _aal_sess.rollback()
                    except Exception:
                        pass
                finally:
                    try:
                        _aal_sess.close()
                    except Exception:
                        pass
            except Exception as _aal_outer:
                logger.warning("[COORD] AAL session setup failed: %s", _aal_outer)


            # Читаем результаты предыдущего цикла координатора — для единого голоса ASI
            _prev_cycle_result = ''
            try:
                from models import AgentActivityLog as _AAL_prev_c
                _prev_aal = session.query(_AAL_prev_c).filter(
                    _AAL_prev_c.user_id == user.id,
                    _AAL_prev_c.activity_type == 'goal_autopilot_dispatch',
                    _AAL_prev_c.status == 'completed',
                    _AAL_prev_c.result.isnot(None),
                ).order_by(_AAL_prev_c.id.desc()).first()
                if _prev_aal and _prev_aal.result:
                    _prev_cycle_result = _prev_aal.result[:400]
            except Exception as _e:
                logger.debug("suppressed: %s", _e)

            # ── Анонс ASI: шаблон без AI-вызова (экономим 1 вызов на старте) ──
            def _trunc(s: str, n: int) -> str:
                return s[:n] + '…' if len(s) > n else s
            _brief_goals = ', '.join(f'«{_trunc(g["title"], 60)}»' for g in _goals[:2])
            if len(_goals) > 2:
                _brief_goals += f' и ещё {len(_goals) - 2}'
            _agents_announce_list = ', '.join(
                f"{p['name']} ({p.get('job', 'специалист')})"
                for p in _profiles[:4]
            )
            # Предыдущий цикл — для контекста в финальном отчёте
            _prev_result_summary = ''
            if _prev_cycle_result:
                _pr_clean = _prev_cycle_result
                if '[tools:' in _pr_clean:
                    _pr_clean = _pr_clean[_pr_clean.find(']')+1:].strip()
                _prev_result_summary = _pr_clean[:300]
            # Анонс-шаблон: краткий (без превью задач — предварительный план сбивает с толку)
            if _prev_result_summary:
                _coord_announce = (
                    f"Продолжаю работу над {_brief_goals}. "
                    f"Результат прошлого цикла: {_trunc(_prev_result_summary, 200)}. "
                    f"Работают: {_agents_announce_list}."
                )
            else:
                _coord_announce = (
                    f"Запускаю автопилот для {_brief_goals}. "
                    f"Команда: {_agents_announce_list}."
                )
            # Накапливаем контекст между шагами — используется в финальном отчёте
            _bridge_notes: list = []

            try:
                # Дедуп стартового анонса: не показывать то же сообщение чаще раза в 30 минут
                from datetime import timedelta as _td_ann
                _ann_cutoff = datetime.now(timezone.utc) - _td_ann(minutes=30)
                _ann_prev = session.query(Interaction).filter(
                    Interaction.user_id == user.id,
                    Interaction.message_type == 'proactive',
                    Interaction.content.like('%coordinator_plan%'),
                    Interaction.created_at >= _ann_cutoff,
                ).first()
                if not _ann_prev:
                    session.add(Interaction(
                        user_id=user.id, message_type='proactive',
                        content=json.dumps({
                            '__agent': {'name': 'ASI', 'id': 0, 'avatar_url': ''},
                            'text': _coord_announce,
                            '__anchor_type': 'coordinator_plan',
                        }, ensure_ascii=False),
                    ))
                    session.commit()
            except Exception:
                try:
                    session.rollback()
                except Exception:
                    pass

            # ── Рекомендация по интеграции — раз в 6 часов, если цели требуют внешних данных ──
            # Отправляем ДО начала выполнения, чтобы пользователь мог подключить нужную интеграцию
            if _missing_intg_coord:
                try:
                    _intg_rec = _missing_intg_coord[0]
                    from models import Interaction as _Intc
                    from datetime import timedelta as _td_i
                    _rec_cutoff = datetime.now(timezone.utc) - _td_i(hours=6)
                    _already_sent_rec = session.query(_Intc).filter(
                        _Intc.user_id == user.id,
                        _Intc.message_type == 'proactive',
                        _Intc.content.like('%coordinator_intg_recommend%'),
                        _Intc.created_at >= _rec_cutoff,
                    ).first()
                    if not _already_sent_rec:
                        _intg_msg = f"ASI:\n\nКстати, {_intg_rec}"
                        # Сначала сохраняем в БД — чтобы не потерять при bot=None
                        session.add(_Intc(
                            user_id=user.id,
                            message_type='proactive',
                            content=json.dumps({
                                '__agent': {'name': 'ASI', 'id': 0, 'avatar_url': ''},
                                'text': f"Кстати, {_intg_rec}",
                                '__anchor_type': 'coordinator_intg_recommend',
                            }, ensure_ascii=False),
                        ))
                        session.commit()
                        if self.bot:
                            await self.bot.send_message(
                                chat_id=user.telegram_id,
                                text=_intg_msg,
                            )
                except Exception as _rec_err:
                    logger.debug("[COORD] intg recommend error: %s", _rec_err)
                    try:
                        session.rollback()
                    except Exception:
                        pass

            # ── Выполняем шаги в режиме ReAct: дать задание → дождаться результата → решить следующий шаг ──
            _results_summary = []
            _all_tools = []
            _prev_steps_context = ''  # результат предыдущих агентов передаётся следующим
            # Масштабируем лимит шагов с размером команды: больше агентов → больше действий за цикл.
            # Формула: max(6, min(agents + goals, 12)) — но не более 12 чтобы цикл не затягивался.
            _MAX_DYNAMIC_STEPS = max(6, min(len(real_agents) + len(_goals), 12))

            _step_queue = list(_plan)  # Полный план — выполняем последовательно, динамически уточняя каждый шаг
            _current_run_agent_tools: dict = {}  # инструменты каждого агента в ТЕКУЩЕМ прогоне координатора
            _retry_done: dict = {}  # retry-флаги локальны для цикла (не persist между циклами)

            _executed = 0
            while _executed < _MAX_DYNAMIC_STEPS:
                # ── Получаем следующий шаг ──
                if _step_queue:
                    _step = _step_queue.pop(0)
                elif _executed > 0 and _prev_steps_context:
                    # ── Динамическое решение следующего шага на основе накопленных результатов ──
                    try:
                        _agents_avail_str = '\n'.join(
                            f"  {_pr['name']} — {_pr.get('job', 'специалист')}"
                            + (f" [интеграции: {', '.join(_pr['caps'][:4])}]" if _pr.get('caps') else '')
                            for _pr in _profiles
                        )
                        _goals_remain_str = '\n'.join(
                            f"  • {_g['title']} ({_g.get('progress', 0)}%)"
                            for _g in _goals[:5]
                        )
                        _done_str = _prev_steps_context.strip()
                        # Цели без упоминания в результатах этого цикла
                        _uncovered_goals = [
                            g for g in _goals[:5]
                            if g['title'].lower() not in (_prev_steps_context or '').lower()
                        ]
                        _uncovered_note = (
                            "🎯 Эти цели ещё не получили действия в этом цикле: "
                            + "; ".join(f'«{g["title"][:40]}»' for g in _uncovered_goals[:3]) + "\n"
                            if _uncovered_goals else ''
                        )
                        # Если email-лимит выбит — подсказываем переключиться, но не запрещаем
                        _email_limit_hit = any(
                            w in (_prev_steps_context or '').lower()
                            for w in ('лимит', 'исчерпан', 'limit exceeded', '30 писем', 'daily limit')
                        )
                        _email_limit_note = (
                            "💡 Дневной лимит email выбит — сейчас лучше заняться другими целями."
                            " Используй research_topic, web_search или другие инструменты.\n"
                            if _email_limit_hit else ''
                        )
                        _next_prompt = (
                            f"Ты — координатор ASI. Команда только что сделала:\n{_done_str}\n\n"
                            f"{_email_limit_note}"
                            f"{_uncovered_note}"
                            f"Активные цели:\n{_goals_remain_str}\n\n"
                            f"Доступные агенты (используй их возможности):\n{_agents_avail_str}\n\n"
                            f"Шагов выполнено: {_executed}. Максимум: {_MAX_DYNAMIC_STEPS}.\n\n"
                            f"Реши: нужен ли ещё один шаг для продвижения к целям?\n"
                            f"Если все ключевые цели получили прогресс — верни {{\"done\": true}}.\n"
                            f"Если нужен ещё шаг — верни ОДИН JSON-объект:\n"
                            f'[{{"agent": "имя_агента", "task": "конкретная задача исходя из интеграций агента", '
                            f'"tool": "инструмент", "goal": "точное название цели"}}]\n'
                            f'Точные названия целей: {"; ".join(repr(g["title"]) for g in _goals[:5])}'
                        )
                        _next_raw = await asyncio.wait_for(
                            _quick_ai_call_raw([{"role": "user", "content": _next_prompt}], max_tokens=200),
                            timeout=12,
                        )
                        _next_raw = _next_raw or ''
                        if '"done"' in _next_raw.lower() and 'true' in _next_raw.lower():
                            logger.info("[COORD] dynamic: done after %d steps", _executed)
                            break
                        import re as _re_dyn
                        _nm = _re_dyn.search(r'\[[\s\S]*?\]', _next_raw)
                        if _nm:
                            _next_parsed = json.loads(_nm.group())
                            if _next_parsed:
                                _step = _next_parsed[0]
                                logger.info("[COORD] dynamic next step %d: %s → %s",
                                            _executed + 1, _step.get('agent'), _step.get('tool'))
                            else:
                                break
                        else:
                            break
                    except Exception as _dyn_e:
                        logger.debug("[COORD] dynamic next step error: %s", _dyn_e)
                        break
                else:
                    break

                _executed += 1
                _ag_name = (_step.get('agent') or '').strip()
                _ag_task = (_step.get('task') or '').strip()
                _tool_hint = (_step.get('tool') or '').strip()
                _ag_goal_title = (_step.get('goal') or '').strip()   # привязка к цели из плана координатора
                if not _ag_name or not _ag_task:
                    continue

                # ── Уточнение задания на основе результатов предыдущих шагов (ReAct refinement) ──
                if _executed > 1 and _prev_steps_context and len(_prev_steps_context.strip()) > 30:
                    try:
                        _refine_p = (
                            f"Координатор уточняет задание агенту {_ag_name} с учётом уже выполненного.\n"
                            f"Уже сделано командой:\n{_prev_steps_context[:500]}\n\n"
                            f"Исходное задание: {_ag_task}\n"
                            + (f"Цель: {_ag_goal_title}\n" if _ag_goal_title else "")
                            + "\nУточни задание (1-3 предложения) — используй конкретные результаты выше."
                            " Если задание актуально без изменений — верни его дословно. Только текст задания."
                        )
                        _refined_task = await asyncio.wait_for(
                            _quick_ai_call_raw([{"role": "user", "content": _refine_p}], max_tokens=120),
                            timeout=8,
                        )
                        if _refined_task and len(_refined_task.strip()) > 20:
                            _ag_task = _refined_task.strip()
                            logger.info("[COORD] refined task step %d for %s", _executed, _ag_name)
                    except Exception as _ref_e:
                        logger.debug("[COORD] task refinement failed: %s", _ref_e)

                # Ищем агента в команде
                _target_ag = next(
                    (a for a in real_agents if a.name.lower() == _ag_name.lower()), None
                )
                _is_asi_step = not _target_ag and _ag_name.lower() in ('asi', 'аси', 'координатор')

                if not _target_ag and not _is_asi_step:
                    logger.info("[COORD] agent '%s' not in team, skip", _ag_name)
                    continue

                # Биллинг кастомного агента
                if _target_ag and getattr(_target_ag, 'id', 0) != 0:
                    from ai_integration.user_agents import bill_agent_message as _bam_c2
                    _bill_c2 = _bam_c2(user.telegram_id, _target_ag.id, session=session)
                    if not _bill_c2.get('success'):
                        logger.info("[COORD] skip %s — billing: %s", _ag_name, _bill_c2.get('error'))
                        continue

                # ── Per-agent assignment: агент объявляет что берётся за задачу (от первого лица, под своим ава) ──
                _ag_id_c = getattr(_target_ag, 'id', 0) if _target_ag else 0
                _ag_avatar_c = _safe_avatar(getattr(_target_ag, 'avatar_url', ''), _ag_id_c) if _target_ag else ''
                _assign_text = f"Начинаю: {_ag_task[:120]}"
                try:
                    _proj_ctx_a = (_user_profile_coord or {}).get('company', '')
                    _proj_pfx_a = f" проекта «{_proj_ctx_a}»" if _proj_ctx_a else ''
                    if _prev_steps_context and len(_prev_steps_context.strip()) > 30:
                        _assign_prompt = (
                            f"Ты агент {_ag_name}{_proj_pfx_a}. Напиши от первого лица что ты сейчас начинаешь делать.\n"
                            f"Задача: {_ag_task[:250]}\n"
                            f"Коллеги уже сделали: {_prev_steps_context[:300]}\n\n"
                            f"1-2 предложения, живая речь от первого лица, без обращений к себе по имени. "
                            f"Например: «Проверю входящие — там могут быть ответы. Если есть — сразу отвечу.» "
                            f"Без списков и markdown."
                        )
                        _assign_max_tok = 100
                    else:
                        _assign_prompt = (
                            f"Ты агент {_ag_name}{_proj_pfx_a}. Напиши от первого лица что ты сейчас начинаешь делать.\n"
                            f"Задача: {_ag_task[:200]}\n\n"
                            f"1-2 предложения, живая речь от первого лица, без обращений к себе по имени. "
                            f"Например: «Начну с проверки почты — посмотрю что пришло.» "
                            f"Без списков и markdown."
                        )
                        _assign_max_tok = 80
                    _gen_assign = await asyncio.wait_for(
                        _quick_ai_call_raw([{'role': 'user', 'content': _assign_prompt}], max_tokens=_assign_max_tok),
                        timeout=10,
                    )
                    if _gen_assign and len(_gen_assign.strip()) > 10:
                        _assign_text = _gen_assign.strip()
                except Exception as _assign_err:
                    logger.debug("[COORD] per-agent assign gen failed: %s", _assign_err)
                try:
                    _assign_cs = Session()
                    try:
                        _assign_cs.add(Interaction(
                            user_id=user.id,
                            message_type='agent_msg',
                            content=json.dumps({
                                '__agent': {'name': _ag_name, 'id': _ag_id_c, 'avatar_url': _ag_avatar_c},
                                'text': _assign_text,
                                '__anchor_type': 'coordinator_assignment',
                            }, ensure_ascii=False),
                        ))
                        _assign_cs.commit()
                    finally:
                        _assign_cs.close()
                except Exception as _assign_save_err:
                    logger.debug("[COORD] per-agent assign save failed: %s", _assign_save_err)

                # ── Создаём задачу «в работе» в Поручениях агентов ──
                _step_task_id = None
                try:
                    from models import Task as _Task_c2
                    import datetime as _dt_c2
                    # Cleanup: отменяем застрявшие in_progress задачи агента старше 30 минут
                    try:
                        _stuck_cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
                        session.execute(
                            text("UPDATE tasks SET status='cancelled', completion_notes='Прервано: новый цикл агента' "
                                 "WHERE user_id=:uid AND source='agent' AND status='in_progress' "
                                 "AND delegated_to_username=:ag AND created_at < :cutoff"),
                            {'uid': user.id, 'ag': _ag_name, 'cutoff': _stuck_cutoff}
                        )
                        session.commit()
                    except Exception as _stuck_err:
                        logger.debug("[COORD] stuck task cleanup error: %s", _stuck_err)
                        try:
                            session.rollback()
                        except Exception:
                            pass
                    # Короткий заголовок = первое предложение/строка задания
                    _task_title_short = (_ag_task.split('\n')[0].split('.')[0])[:100].strip()
                    if len(_task_title_short) < 15:
                        _task_title_short = ' '.join(_ag_task.split()[:14])
                    # Guard: если задание = название цели → добавляем инструмент для конкретики
                    if _ag_goal_title and _task_title_short.lower().strip() == _ag_goal_title.lower().strip()[:100]:
                        if _tool_hint:
                            _task_title_short = f"{_tool_hint.replace('_', ' ').title()}: {_ag_goal_title[:70]}"
                        else:
                            _task_title_short = f"Шаг к цели: {_ag_goal_title[:80]}"
                        logger.info("[COORD] vague task remapped for %s: tool=%s title=%s", _ag_name, _tool_hint, _task_title_short[:60])

                    # ── DEDUP: не создавать задачу если аналогичная уже была за 4 часа ──
                    _dedup_cutoff = datetime.now(timezone.utc) - timedelta(hours=4)
                    # Стоп-слова: не учитывать при сравнении
                    _DEDUP_STOP = {'на', 'в', 'для', 'и', 'от', 'по', 'через', 'из', 'с', 'о', 'к', 'не',
                                   'проверить', 'провести', 'использовать', 'используя', 'текущей', 'текущую',
                                   'наличие', 'ответов', 'ситуации', 'обработки'}
                    _dedup_words = set(w for w in _task_title_short.lower().split()[:8] if w not in _DEDUP_STOP and len(w) > 2)
                    _recent_similar = session.query(_Task_c2).filter(
                        _Task_c2.user_id == user.id,
                        _Task_c2.source == 'agent',
                        _Task_c2.created_at >= _dedup_cutoff,
                        _Task_c2.delegated_to_username == _ag_name,
                    ).all()
                    _is_dup = False
                    for _rs in _recent_similar:
                        _rs_words = set(w for w in (_rs.title or '').lower().split()[:8] if w not in _DEDUP_STOP and len(w) > 2)
                        _overlap = len(_dedup_words & _rs_words)
                        # 2+ значимых слов совпало = дубль
                        if _overlap >= 2 and (_overlap / max(len(_dedup_words), 1)) >= 0.4:
                            _is_dup = True
                            logger.info(f"[COORD] dedup: skipping task '{_task_title_short[:50]}' — similar to [{_rs.id}] '{_rs.title[:50]}' (overlap={_overlap})")
                            break
                    if _is_dup:
                        _step_task_id = None
                        # Дубль задачи = пропускаем выполнение агента полностью
                        continue
                    else:
                        # Описание = полный текст только если отличается от заголовка
                        _task_desc = _ag_task[:2000] if _ag_task[:100].strip() != _task_title_short else ''
                        # Резолвим goal_id по названию цели из плана координатора
                        _resolved_goal_id = None
                        if _ag_goal_title:
                            _ag_goal_lower = _ag_goal_title.lower().strip()
                            for _cg in _goals:
                                if _cg.get('title', '').lower().strip() == _ag_goal_lower:
                                    _resolved_goal_id = _cg.get('id')
                                    break
                            if not _resolved_goal_id:
                                # Fuzzy: частичное совпадение
                                for _cg in _goals:
                                    if _ag_goal_lower in _cg.get('title', '').lower() or _cg.get('title', '').lower() in _ag_goal_lower:
                                        _resolved_goal_id = _cg.get('id')
                                        break
                        _step_task = _Task_c2(
                            user_id=user.id,
                            title=_task_title_short[:200],
                            description=_task_desc or None,
                            status='in_progress',
                            source='agent',
                            created_by_agent_id=_target_ag.id if _target_ag else None,
                            delegated_to_username=_ag_name,
                            goal_id=_resolved_goal_id,
                        )
                        session.add(_step_task)
                        session.commit()
                        _step_task_id = _step_task.id
                except Exception as _tc_err:
                    logger.debug("[COORD] task create skipped: %s", _tc_err)
                    try:
                        session.rollback()
                    except Exception:
                        pass

                # Собираем agent_data для _exec_agent_for_director
                _coord_company = (_user_profile_coord or {}).get('company', '') or ''
                if _is_asi_step:
                    _asi_tools = [
                        'web_search', 'research_topic', 'find_relevant_contacts_for_task',
                        'save_email_contact', 'add_task', 'delegate_task',
                        'send_outreach_email', 'start_email_campaign', 'add_email_leads',
                        'check_emails', 'update_goal_progress', 'update_goal', 'create_goal',
                        'quick_topic_search', 'get_news_trends',
                        'run_agent_action', 'publish_to_telegram', 'publish_to_discord',
                        'reply_to_outreach_email', 'send_follow_up_email',
                        'find_and_message_relevant_users', 'negotiate_by_email',
                    ]
                    _ag_data = {
                        'id': 0, 'name': 'ASI',
                        'job_title': 'Координатор',
                        'specialization': 'goal_management',
                        'description': 'Координатор команды — исследует, находит контакты, создаёт задачи.',
                        'personality': '',
                        'company': _coord_company,
                        'python_code': '', 'user_api_keys': '',
                        'tools_allowed': json.dumps(_asi_tools),
                        'tools': _asi_tools,
                        'avatar_url': '',
                    }
                else:
                    _ag_data = {
                        'id': _target_ag.id,
                        'name': _target_ag.name,
                        'job_title': _target_ag.job_title or '',
                        'specialization': _target_ag.specialization or '',
                        'description': _target_ag.description or '',
                        'personality': _target_ag.personality or '',
                        'python_code': _target_ag.python_code or '',
                        'user_api_keys': _target_ag.user_api_keys or '',
                        'tools_allowed': _target_ag.tools_allowed or '',
                        'tools': json.loads(_target_ag.tools_allowed or '[]'),
                        'avatar_url': _safe_avatar(getattr(_target_ag, 'avatar_url', ''), _target_ag.id),
                        'search_scope': getattr(_target_ag, 'search_scope', '') or '',
                        'knowledge_base': getattr(_target_ag, 'knowledge_base', '') or '',
                        'company': _coord_company,
                    }

                # Строим команду для контекста (кто ещё в команде) — только имя + специализация
                _team_lines_c = []
                for _prof in _profiles:
                    if _prof['name'].lower() != _ag_name.lower():
                        _c = _prof.get('job') or 'специалист'
                        _team_lines_c.append(f"  • {_prof['name']} — {_c}")
                if not _is_asi_step:
                    _team_lines_c.append("  • ASI — координатор команды")

                # Task prompt для агента — его конкретное задание + контекст
                _agent_goals_block = '\n'.join(
                    f"  • {g['title']} ({g.get('progress', 0)}%)" for g in _goals[:5]
                )
                _agent_contacts_block = '\n'.join(
                    f"  {c}" for c in data.get('known_contacts', [])[:8]
                )
                # Личная история этого агента (не глобальная) — что он сам уже делал
                _this_agent_hist = _per_agent_history.get(_ag_name, [])
                _agent_memory_block = '\n'.join(f"  {h}" for h in _this_agent_hist[:5])

                # Уже отправленные письма — этот агент должен знать
                _sent_emails_block = (
                    'Уже получили письма (НЕ писать повторно): ' + ', '.join(_already_sent[:15])
                    if _already_sent else ''
                )

                # Инструменты которые агент уже вызывал — не повторять бессмысленно
                _recently_used_tools: set = set()
                for _rh in _this_agent_hist[:3]:
                    if '[' in _rh and ']' in _rh:
                        _bt = _rh[_rh.find('[')+1:_rh.find(']')]
                        for _btt in _bt.split(','):
                            _recently_used_tools.add(_btt.strip())
                # Добавляем инструменты из текущего прогона (этот же агент уже делал на предыдущих шагах)
                _recently_used_tools.update(_current_run_agent_tools.get(_ag_name, set()))
                _dedup_hint = (
                    f"\n🚫 Ты уже вызывал в этом сеансе: {', '.join(sorted(_recently_used_tools)[:6])} — "
                    "НЕ повторяй с теми же параметрами. Переходи к следующему конкретному шагу.\n"
                    if _recently_used_tools else ''
                )

                _user_profile_ag = data.get('user_profile', {})
                _user_profile_sum_ag = (_user_profile_ag.get('summary', '') or '') if _user_profile_ag else ''
                _user_rules_ag = data.get('user_rules', [])
                _rap_note = (
                    f"⚠️ run_agent_action запускает ТОЛЬКО твой встроенный скрипт (RSS/GitHub/etc.) — "
                    f"он вернёт данные СВОЕЙ ленты, а не произвольные API.\n"
                    f"   Если run_agent_action вернул данные НЕ по теме задачи:\n"
                    f"     → ЧЕСТНО скажи пользователю: 'Мои RSS-ленты посвящены [X], а не [теме задачи]. "
                    f"Переключаюсь на web_search.'\n"
                    f"     → СРАЗУ вызови research_topic или web_search с нужными ключевыми словами.\n"
                    f"     → НИКОГДА не называй нерелевантные данные 'аналитикой по [теме задачи]'!\n"
                    f"     → НЕ обновляй update_goal_progress если данные нерелевантны теме цели!\n"
                    if (_ag_data.get('python_code') or '').strip() else ''
                )
                _ag_is_fem = _ag_name and _ag_name[-1] in 'аяАЯ' and _ag_name[-2:].lower() not in ('ша', 'жа')
                _ag_role_str = (
                    f"{_ag_data.get('job_title', '') or _ag_data.get('specialization', 'специалист')}"
                ).strip()
                _ag_profile_match = next((p for p in _profiles if p['name'].lower() == _ag_name.lower()), None)
                _ag_caps_for_prompt = (
                    ', '.join(_ag_profile_match['caps'][:4])
                    if _ag_profile_match and _ag_profile_match.get('caps')
                    else 'нет подключённых интеграций'
                )

                # ── Живой контекст интеграций агента ─────────────────────────────────────────
                # Извлекаем конкретные данные из настроек агента, а не абстрактные названия.
                _intg_live_lines: list = []
                _ag_api_keys_raw = _ag_data.get('user_api_keys', '') or ''
                _ag_py_code_raw  = _ag_data.get('python_code', '') or ''
                import re as _re_live

                # Email/Почта: какой аккаунт, что уже открыто в inbox
                _ag_email_user = ''
                for _kl in _ag_api_keys_raw.splitlines():
                    _kl = _kl.strip()
                    if '=' in _kl and any(
                        _kl.upper().startswith(p) for p in (
                            'GMAIL_USER=', 'YANDEX_USER=', 'MAILRU_USER=', 'IMAP_USER=', 'EMAIL_USER='
                        )
                    ):
                        _ag_email_user = _kl.split('=', 1)[1].strip()
                        break
                if _ag_email_user:
                    _intg_live_lines.append(f"📧 Твой email-аккаунт: {_ag_email_user}")
                    # Pending replies для этого агента
                    _pr_for_agent = [
                        p for p in _pending_replies
                        if not p.get('reply_text')  # без текста → нужен check_emails сначала
                        or p.get('reply_text')
                    ]
                    if _pr_for_agent:
                        for _prr in _pr_for_agent[:3]:
                            _prr_txt = _prr.get('reply_text') or '[текст не получен — вызови check_emails]'
                            _intg_live_lines.append(
                                f"  🆕 Ждёт ответа: {_prr.get('name') or _prr.get('email')} "
                                f"({_prr.get('email')}) — \"{_prr_txt[:1500]}\" "
                                f"→ reply_to_outreach_email(outreach_id={_prr.get('outreach_id')}, reply_body=...) "  
                            )

                # RSS: URL и тематика ленты
                _rss_url_live = ''
                for _kl in _ag_api_keys_raw.splitlines():
                    if _kl.strip().upper().startswith('RSS_URL='):
                        _rss_url_live = _kl.split('=', 1)[1].strip()
                        break
                if _rss_url_live:
                    # Определяем тематику по URL
                    _rss_topics = []
                    _rss_domain_map = [
                        (('habr', 'habrahabr'), 'IT/технологии/разработка (Хабр)'),
                        (('tass', 'ria.ru', 'rbc.ru', 'kommersant'), 'новости России (деловые СМИ)'),
                        (('investing.com', 'moex', 'finam', 'rbc.ru/finance'), 'финансы и рынки'),
                        (('github.com/explore', 'github.blog'), 'GitHub (разработка, open source)'),
                        (('ai.googleblog', 'openai.com', 'deepmind'), 'AI/ML исследования'),
                        (('hh.ru', 'superjob', 'linkedin'), 'вакансии/рекрутинг'),
                        (('vc.ru', 'spark.ru', 'rb.ru'), 'стартапы и предпринимательство'),
                    ]
                    _rss_lower = _rss_url_live.lower()
                    for _patterns, _topic in _rss_domain_map:
                        if any(p in _rss_lower for p in _patterns):
                            _rss_topics.append(_topic)
                    _rss_topic_str = ', '.join(_rss_topics) if _rss_topics else 'тематика определяется по контенту'
                    _intg_live_lines.append(
                        f"📰 RSS-лента: {_rss_url_live} (тематика: {_rss_topic_str})"
                    )
                    _intg_live_lines.append(
                        "  ⚠️ run_agent_action читает ТОЛЬКО ЭТУ ленту. "
                        "Если тема задачи не совпадает с тематикой ленты → "
                        "используй research_topic(query=...) или web_search(query=...) вместо run_agent_action."
                    )

                # GitHub: токен есть — подсказываем action-имена из скрипта
                _has_github_live = any(
                    k in _ag_api_keys_raw.upper() for k in ('GITHUB_TOKEN=', 'GITHUB_ACCESS_TOKEN=')
                )
                if _has_github_live:
                    _gh_actions = _re_live.findall(r"ACTION\s*==\s*['\"]([^'\"]+)['\"]", _ag_py_code_raw)
                    if _gh_actions:
                        _intg_live_lines.append(
                            f"💻 GitHub-интеграция активна. "
                            f"run_agent_action поддерживает action: {', '.join(list(dict.fromkeys(_gh_actions))[:4])}"
                        )
                    _intg_live_lines.append(
                        "⚠️ КРИТИЧНО — правила GitHub search query:\n"
                        "  ✅ ПРАВИЛЬНО: 'language:python autonomous agent repos:>10'\n"
                        "  ✅ ПРАВИЛЬНО: 'machine learning language:python followers:>15'\n"
                        "  ✅ ПРАВИЛЬНО: 'indie hacker saas repos:>20 followers:>30'\n"
                        "  ❌ ЗАПРЕЩЕНО: email-адреса ('user@gmail.com') → вернёт 0 результатов\n"
                        "  ❌ ЗАПРЕЩЕНО: имена из переписки ('Georgiou Feng repos:>5') → вернёт 0\n"
                        "  ❌ ЗАПРЕЩЕНО: название задачи ('email_analysis repos:>5') → вернёт 0\n"
                        "  Допустимые квалификаторы: language:, repos:, followers:, location:, type:user\n"
                        "  ПОСЛЕ поиска → для КАЖДОГО с email: save_email_contact → send_outreach_email"
                    )

                # Прочие скрипт-action из python_code (не GitHub, не RSS)
                elif _ag_py_code_raw and not _rss_url_live:
                    _other_actions = list(dict.fromkeys(
                        _re_live.findall(r"ACTION\s*==\s*['\"]([^'\"]+)['\"]", _ag_py_code_raw)
                    ))
                    if _other_actions:
                        _intg_live_lines.append(
                            f"🔧 Скрипт поддерживает action: {', '.join(_other_actions[:5])} "
                            f"→ используй run_agent_action(action='...')"
                        )

                _intg_live_block = (
                    "\n\n🔌 ТВОИ ИНТЕГРАЦИИ (конкретно):\n" + '\n'.join(_intg_live_lines) + '\n'
                    if _intg_live_lines else ''
                )

                _agent_prompt = (
                    f"Твоё задание:\n{_ag_task}\n"
                    + (f"\n🎯 Работаешь НА ЦЕЛЬ: «{_ag_goal_title}»\n"
                       f"   🚫 update_goal_progress — ТОЛЬКО goal_title='{_ag_goal_title}'. Другие цели НЕ ТРОГАЙ.\n"
                       f"   🚫 update_goal_progress вызывай ТОЛЬКО при КОНКРЕТНОМ исходящем действии:\n"
                       f"      ✅ check_emails вернул «+N новых ответов» → metric_current += N (уже сделано авто!)\n"
                       f"      ✅ получил ОТВЕТ/подтверждение интереса от нового контакта СНАРУЖИ → metric_current += 1\n"
                       f"      ✅ отправил письмо/сообщение ЧЕЛОВЕКУ → обновляй notes\n"
                       f"      ❌ check_emails сказал «уже сделано авто» → НЕ вызывай update_goal_progress повторно!\n"
                       f"      ❌ прочитал RSS / сделал web_search / вызвал run_agent_action — НЕ обновлять прогресс!\n"
                       f"      ❌ RSS/поиск вернул НЕРЕЛЕВАНТНЫЕ данные (не по теме цели) — НЕ обновлять прогресс!\n"
                       f"      ❌ find_relevant_contacts_for_task — контакты ИЗ БАЗЫ, уже учтены ранее. НЕ обновляй metric_current!\n"
                       f"      ❌ start_email_campaign — запуск кампании не = новый пользователь. НЕ обновляй metric_current!\n"
                       if _ag_goal_title else '')
                    + (f"� Рекомендованный старт: {_tool_hint} — оцени сам, подходит ли он, или выбери лучше исходя из задания.\n" if _tool_hint else '')
                    + _rap_note
                    + _dedup_hint
                    + _intg_live_block
                    + (f"\n👤 Контекст пользователя (работай на ЕГО проект):\n{_user_profile_sum_ag}\n" if _user_profile_sum_ag else '')
                    + (f"\n📌 Правила пользователя:\n" + '\n'.join(f"  {i+1}. {r}" for i, r in enumerate(_user_rules_ag[:5])) + "\n" if _user_rules_ag else '')
                    + f"\nАктивные цели:\n{_agent_goals_block}"
                    + (f"\n\nИзвестные контакты:\n{_agent_contacts_block}" if _agent_contacts_block else '')
                    + (f"\n\n⚠️ {_sent_emails_block}" if _sent_emails_block else '')
                    + (f"\n\nТвоя история (не повторяй):\n{_agent_memory_block}" if _agent_memory_block else '')
                    + (f"\n\nУже сделано командой (используй):\n{_prev_steps_context}" if _prev_steps_context else '')
                    + (f"\n\nКоманда:\n" + '\n'.join(_team_lines_c)
                       if _team_lines_c else '')
                    + f"\n\n🧠 ТЫ — ЖИВОЙ СПЕЦИАЛИСТ В КОМАНДЕ ({_ag_name}, {_ag_role_str}):"
                    f"\n  • Твои интеграции: {_ag_caps_for_prompt}"
                    f"\n  • Если для задачи не хватает доступа/API — НЕ молчи. Скажи прямо:"
                    f" \"Мне нужен X\" или \"Попросите пользователя подключить Y через Дашборд → Настройки агента\"."
                    f"\n  • Если нашёл что-то важное КРОМЕ задания — упомяни это как коллега."
                    f"\n  • Если план нерабочий — предложи другой подход: \"Лучше сделаю Z, потому что...\""
                    f"\n  • Говори от первого лица. Не шаблонно. Ты {'специалист' if not _ag_is_fem else 'специалистка'} со своим мнением."
                    f"\n  • Можно сказать \"я бы предложил{'а' if _ag_is_fem else ''}...\", \"заметил{'а' if _ag_is_fem else ''} интересное...\", \"стоит также проверить...\""
                    f"\n\n🧩 КРИТИЧЕСКОЕ МЫШЛЕНИЕ (перед выполнением задания):"
                    f"\n  [АНАЛИТИК] Оцени задание: какой РЕАЛЬНЫЙ результат нужен пользователю? Не формальное выполнение, а польза."
                    f"\n  [СТРАТЕГ] Какой инструмент даст максимум? Есть ли короткий путь? Если данные уже есть в контексте — не ищи заново."
                    f"\n  [КРИТИК] Что может пойти не так? Данные могут быть устаревшими? Запрос слишком общий? Уточни сам."
                    f"\n  Итог: {'действуй' if not _ag_is_fem else 'действуй'} по лучшему плану, а не слепо по инструкции."
                    f"\n\n⚠️ ВАЖНО: Если упоминаешь данные, ссылку или результат — предоставь их ПРЯМО СЕЙЧАС в этом ответе."
                    f" Не пиши 'отправлю позже' или 'скину ссылку' без реального tool-вызова. Нет инструмента — нет обещания."
                )

                try:
                    _raw = await asyncio.wait_for(
                        _exec_agent_for_director(_ag_data, _agent_prompt, user.telegram_id),
                        timeout=180,
                    )
                except asyncio.TimeoutError:
                    _ae_msg = f'Таймаут 180с — агент не успел выполнить задачу'
                    logger.warning("[COORD] agent %s timeout after 180s", _ag_name)
                    if _step_task_id:
                        try:
                            from sqlalchemy import text as _sql_t_ae
                            session.execute(_sql_t_ae(
                                "UPDATE tasks SET status='cancelled', completion_notes=:n WHERE id=:id"
                            ), {'n': _ae_msg, 'id': _step_task_id})
                            session.commit()
                        except Exception:
                            try:
                                session.rollback()
                            except Exception:
                                pass
                    continue
                except Exception as _ae:
                    logger.warning("[COORD] agent %s exec failed: %s", _ag_name, _ae)
                    if _step_task_id:
                        try:
                            from sqlalchemy import text as _sql_t_ae2
                            _ae_detail = str(_ae)[:200].strip() or type(_ae).__name__
                            session.execute(_sql_t_ae2(
                                "UPDATE tasks SET status='cancelled', completion_notes=:n WHERE id=:id"
                            ), {'n': f'Ошибка выполнения: {_ae_detail}', 'id': _step_task_id})
                            session.commit()
                        except Exception:
                            try:
                                session.rollback()
                            except Exception:
                                pass
                    continue

                _result = _raw[0] if isinstance(_raw, (tuple, list)) else _raw
                _step_tools = list(_raw[1]) if isinstance(_raw, (tuple, list)) and len(_raw) > 1 else []
                _all_tools.extend(_step_tools)
                # Запоминаем инструменты текущего прогона — для dedup следующих шагов того же агента
                if _step_tools and _ag_name:
                    _current_run_agent_tools.setdefault(_ag_name, set()).update(_step_tools)

                _DONE_FB_SET = {"Задачу выполнил.", "Задачу выполнила."}
                _result_stripped = (_result or '').strip()
                if not _result_stripped or len(_result_stripped) < 5 or _result_stripped in _DONE_FB_SET:
                    # ── RETRY: если пустой результат и не было retry — пробуем ещё раз с уточнённым промптом ──
                    _retry_key = f'{_ag_name}:{_executed}'
                    if _result_stripped not in _DONE_FB_SET and not _retry_done.get(_retry_key):
                        _retry_done[_retry_key] = True
                        _retry_prompt = (
                            f"{_agent_prompt}\n\n"
                            f"⚠️ ПЕРВАЯ ПОПЫТКА вернула пустой результат. "
                            f"Используй свои инструменты (run_agent_action, web_search, check_emails и т.д.) "
                            f"для получения КОНКРЕТНЫХ данных. Если задача невыполнима — объясни почему."
                        )
                        try:
                            _raw_retry = await asyncio.wait_for(
                                _exec_agent_for_director(_ag_data, _retry_prompt, user.telegram_id),
                                timeout=180,
                            )
                            _result_retry = _raw_retry[0] if isinstance(_raw_retry, (tuple, list)) else _raw_retry
                            _retry_tools = list(_raw_retry[1]) if isinstance(_raw_retry, (tuple, list)) and len(_raw_retry) > 1 else []
                            _retry_stripped = (_result_retry or '').strip()
                            if _retry_stripped and len(_retry_stripped) >= 5 and _retry_stripped not in _DONE_FB_SET:
                                # Retry успешен — используем новый результат
                                _result = _result_retry
                                _result_stripped = _retry_stripped
                                _step_tools.extend(_retry_tools)
                                _all_tools.extend(_retry_tools)
                                logger.info(f"[COORD] agent {_ag_name}: retry succeeded ({len(_retry_stripped)} chars)")
                        except Exception as _retry_err:
                            logger.debug(f"[COORD] agent {_ag_name}: retry failed: {_retry_err}")

                if not _result_stripped or len(_result_stripped) < 5 or _result_stripped in _DONE_FB_SET:
                    if _result_stripped not in _DONE_FB_SET:
                        # Реально пустой результат после retry: отменяем задачу, НЕ беспокоим пользователя
                        logger.info(f"[COORD] agent {_ag_name}: empty result after retry — silent skip")
                        if _step_task_id:
                            try:
                                from sqlalchemy import text as _sql_t_empty
                                session.execute(_sql_t_empty(
                                    "UPDATE tasks SET status='cancelled', completion_notes=:n WHERE id=:id"
                                ), {'n': 'Агент вернул пустой результат', 'id': _step_task_id})
                                session.commit()
                            except Exception:
                                try:
                                    session.rollback()
                                except Exception:
                                    pass
                        # Тихий пропуск — не создаём interaction и не шлём в Telegram
                        # Раньше слали "не нашёл конкретных результатов" — это мусор для пользователя
                    # _done_fb: агент выполнил задачу без детального отчёта — тихо пропускаем
                    continue

                # Очистка и отправка результата пользователю
                _ag_avatar = _ag_data.get('avatar_url', '')
                _ag_id = _ag_data.get('id', 0)
                try:
                    from ai_integration.utils import clean_technical_details as _ctd_c
                    _cleaned = _ctd_c(_result.strip())
                    if not _cleaned or len(_cleaned.strip()) < 10:
                        _cleaned = _result.strip()
                except Exception:
                    _cleaned = _result.strip()

                # ── Детектор живых реакций агента: запросы интеграций, инициатива, отклонение от сценария ──
                # Если в ответе агент просит что-то / предлагает альтернативу → ASI озвучивает это пользователю
                _AGENT_REQUEST_PHRASES = (
                    'нужен ', 'нужна ', 'нужны ', 'не хватает', 'отсутствует', 'не подключен', 'не настроен',
                    'попросите пользователя', 'добавить api', 'добавить ключ', 'подключить',
                    'предлагаю', 'лучше сделать', 'лучше попробовать', 'стоит також', 'стоит также',
                    'заметил', 'заметила', 'обнаружил', 'обнаружила', 'кстати,', 'важное:', 'важный момент',
                    'имеет смысл', 'я бы предложил', 'я бы предложила', 'я бы рекомендовал', 'я бы рекомендовала',
                    'к сожалению, у меня нет', 'к сожалению нет доступа',
                )
                _agent_has_initiative = any(
                    ph in _cleaned.lower() for ph in _AGENT_REQUEST_PHRASES
                )
                if _agent_has_initiative:
                    # Агент проявил инициативу/запросил что-то — ASI ретранслирует это пользователю особо
                    try:
                        _relay_p = (
                            f"Агент {_ag_name} написал:"
                            f"\n\"{_cleaned[:600]}\""
                            f"\n\nТы — ASI, координатор. Агент отклонился от сценария или попросил помощь."
                            f" Напиши 1-2 живых предложения: озвучи просьбу/инициативу агента пользователю."
                            f" Можно добавить ЧТО нужно сделать (если агент просит интеграцию — скажи где настроить)."
                            f" Разговорно, без markdown."
                        )
                        _relay_txt = await asyncio.wait_for(
                            _quick_ai_call_raw([{'role': 'user', 'content': _relay_p}], max_tokens=120),
                            timeout=8,
                        )
                        if _relay_txt and len(_relay_txt.strip()) > 15:
                            try:
                                _rl_sess = Session()
                                try:
                                    _rl_sess.add(Interaction(
                                        user_id=user.id,
                                        message_type='proactive',
                                        content=json.dumps({
                                            '__agent': {'name': 'ASI', 'id': 0, 'avatar_url': ''},
                                            'text': _relay_txt.strip(),
                                            '__anchor_type': 'coordinator_agent_request',
                                        }, ensure_ascii=False),
                                    ))
                                    _rl_sess.commit()
                                finally:
                                    _rl_sess.close()
                            except Exception:
                                pass
                    except Exception as _rl_err:
                        logger.debug("[COORD] agent request relay error: %s", _rl_err)

                # ── Помечаем задачу выполненной ──
                if _step_task_id:
                    try:
                        from sqlalchemy import text as _sql_t_done
                        import datetime as _dt_done
                        session.execute(_sql_t_done(
                            "UPDATE tasks SET status='completed', completion_notes=:n, "
                            "actual_completion_time=:t WHERE id=:id"
                        ), {
                            'n': _cleaned[:1000],
                            't': _dt_done.datetime.now(_dt_done.timezone.utc),
                            'id': _step_task_id,
                        })
                        session.commit()
                    except Exception as _tu_err:
                        logger.debug("[COORD] task update error: %s", _tu_err)
                        try:
                            session.rollback()
                        except Exception:
                            pass

                try:
                    _msg_type_c2 = 'agent_msg' if _ag_id != 0 else 'proactive'
                    session.add(Interaction(
                        user_id=user.id,
                        message_type=_msg_type_c2,
                        content=json.dumps({
                            '__agent': {'name': _ag_name, 'id': _ag_id, 'avatar_url': _ag_avatar},
                            'text': _strip_html(_cleaned),
                            '__tools_used': _step_tools,
                            '__anchor_type': 'coordinator_result',
                        }, ensure_ascii=False),
                    ))
                    # Также логируем agent_task в AAL — для контекста ASI (context_builder читает AAL)
                    session.add(AgentActivityLog(
                        user_id=user.id,
                        activity_type='agent_task',
                        title=f'{_ag_name}: {(_step.get("task") or "задача")[:150]}',
                        target=f'agent:{_ag_name}',
                        content=_cleaned[:600],
                        result=_cleaned[:600],
                        status='completed',
                    ))
                    session.commit()
                except Exception:
                    try:
                        session.rollback()
                    except Exception:
                        pass

                _results_summary.append(
                    f"{_ag_name}: {_cleaned[:150]}"
                )
                # Накапливаем контекст для следующих агентов в цепочке
                _prev_steps_context += f"• {_ag_name}: {_cleaned[:300]}\n"

                # ── Накапливаем контекст шага для финального отчёта (без лишнего AI-вызова) ──
                if len(_cleaned) > 40:
                    _next_hint = ''
                    if _step_queue:
                        _ns0 = _step_queue[0]
                        _next_hint = f" → далее {_ns0.get('agent','?')}: {(_ns0.get('task') or '')[:60]}"
                    _bridge_notes.append(f"{_ag_name}: {_cleaned[:200]}{_next_hint}")

                await asyncio.sleep(0.5)  # небольшая пауза между агентами

            # ── Обновляем AAL ──
            if _aal_id_c:
                try:
                    _tools_pfx = f"[tools: {', '.join(_all_tools)}] " if _all_tools else ''
                    _full_res = _tools_pfx + ' | '.join(_results_summary[:3])
                    _st = 'completed' if _results_summary else 'empty_result'
                    from sqlalchemy import text as _aal_t_c
                    from models import Session as _AAL_Upd_Sess
                    _upd_sess = _AAL_Upd_Sess()
                    try:
                        _upd_sess.execute(_aal_t_c(
                            "UPDATE agent_activity_log SET status=:st, result=:res, updated_at=NOW() WHERE id=:aid"
                        ), {'st': _st, 'res': _full_res[:800], 'aid': _aal_id_c})
                        _upd_sess.commit()
                    except Exception as _upd:
                        logger.warning("[COORD] AAL update: %s", _upd)
                        try:
                            _upd_sess.rollback()
                        except Exception:
                            pass
                    finally:
                        try:
                            _upd_sess.close()
                        except Exception:
                            pass
                except Exception as _upd_outer:
                    logger.warning("[COORD] AAL update session setup: %s", _upd_outer)

            # ── Финальный отчёт пользователю: что РЕАЛЬНО сделано за этот цикл ──
            # Фильтруем placeholder/pending записи — они бесполезны в отчёте
            _BORING_RESULT_PHRASES = (
                'задача передана', 'результат будет позже', 'таймаут', 'timeout',
                'нет новых писем', 'входящих писем нет', 'новых данных нет',
                'не удалось', 'ошибка выполнения',
            )
            _results_for_report = [
                r for r in _results_summary
                if not any(p in r.lower() for p in _BORING_RESULT_PHRASES)
            ]
            if _results_summary and not _results_for_report:
                # Все результаты — placeholder'ы. Не генерируем отчёт, не беспокоим пользователя.
                return True
            if _results_for_report:
                try:
                    _report_items = '\n'.join(f"• {r}" for r in _results_for_report[:5])
                    _goals_state_now = '\n'.join(
                        f"  {g['title'][:60] + '…' if len(g['title']) > 60 else g['title']} — {g.get('progress', 0)}%"
                        + (f" ({int(g.get('metric_current', 0))}/{int(g.get('metric_target', 0))} {g.get('metric_unit', '')})"
                           if g.get('metric_target') else '')
                        for g in _goals[:3]
                    )
                    _bridge_flow = '\n'.join(_bridge_notes) if _bridge_notes else ''
                    # Проверяем — кто сохранил заметку в этом цикле
                    _saved_note_agents = [
                        n for n, tools in _current_run_agent_tools.items() if 'save_note' in tools
                    ]
                    _note_hint = (
                        f"- Агент(ы) {', '.join(_saved_note_agents)} сохранили подробный отчёт в раздел Заметки — напомни об этом пользователю одной фразой.\n"
                        if _saved_note_agents else ''
                    )
                    _report_prompt = (
                        f"Ты — ASI, координатор. Пишешь итоговый отчёт ПОЛЬЗОВАТЕЛЮ (не агентам!) про рабочий цикл.\n\n"
                        f"Что сделала команда:\n{_report_items}\n\n"
                        + (f"Ход работы:\n{_bridge_flow}\n\n" if _bridge_flow else '')
                        + f"Состояние целей:\n{_goals_state_now}\n\n"
                        f"Правила:\n"
                        f"- Адресат — ПОЛЬЗОВАТЕЛЬ, не агенты. НЕ обращайся к агентам по имени.\n"
                        f"- Пиши ЧТО БЫЛО СДЕЛАНО этот цикл (прошедшее время). НЕ пиши что надо сделать дальше.\n"
                        f"- НЕ цитируй агентов дословно — синтезируй итог своими словами.\n"
                        f"- НЕ задавай вопросов пользователю ('Продолжить?', 'Перепоручить?') — ты координатор автопилота, решай сам.\n"
                        f"- Называй КОНКРЕТНО: что нашли, кому написали, сколько контактов — без воды.\n"
                        f"- Если агент не привёл конкретный результат — не упоминай его вообще.\n"
                        f"- Если ничего конкретного не сделано — пиши ОДНО предложение: 'Цикл завершён, новых подтверждённых результатов нет.'.\n"
                        + _note_hint
                        + f"- 2-4 предложения. Без markdown. Без [АВТОПИЛОТ]. Без скобок. Без вопросов."
                    )
                    _report_gen = await asyncio.wait_for(
                        _quick_ai_call_raw([{"role": "user", "content": _report_prompt}], max_tokens=200),
                        timeout=12,
                    )
                    if _report_gen and len(_report_gen.strip()) > 20:
                        _report_text = _report_gen.strip()
                        try:
                            from models import Session as _Sum_Sess_cls
                            import datetime as _dt_sumchk
                            _sum_sess = _Sum_Sess_cls()
                            try:
                                # Dedup: не сохраняем coordinator_summary если предыдущий был < 20 мин назад
                                _sum_cutoff = _dt_sumchk.datetime.now(_dt_sumchk.timezone.utc) - _dt_sumchk.timedelta(minutes=20)
                                from sqlalchemy import text as _sql_sumchk
                                _recent_summary = _sum_sess.execute(_sql_sumchk(
                                    "SELECT id FROM interactions WHERE user_id=:uid "
                                    "AND message_type='proactive' "
                                    "AND content LIKE '%coordinator_summary%' "
                                    "AND created_at >= :cutoff LIMIT 1"
                                ), {'uid': user.id, 'cutoff': _sum_cutoff}).fetchone()
                                if _recent_summary:
                                    logger.info("[COORD] coordinator_summary deduped — recent exists (id=%s)", _recent_summary[0])
                                    _sum_sess.close()
                                    if self.bot:
                                        try:
                                            await self.bot.send_message(
                                                chat_id=user.telegram_id,
                                                text=f"ASI (итог):\n\n{_report_text}",
                                            )
                                        except Exception as _e:
                                            logger.debug("suppressed: %s", _e)
                                    return True
                                _sum_sess.add(Interaction(
                                    user_id=user.id,
                                    message_type='proactive',
                                    content=json.dumps({
                                        '__agent': {'name': 'ASI', 'id': 0, 'avatar_url': ''},
                                        'text': _report_text,
                                        '__anchor_type': 'coordinator_summary',
                                    }, ensure_ascii=False),
                                ))
                                # Также логируем в AAL чтобы итог отображался в хронологии
                                _goals_titles = ', '.join(
                                    (g['title'][:40] + '…' if len(g['title']) > 40 else g['title'])
                                    for g in _goals[:2]
                                )
                                _sum_sess.add(AgentActivityLog(
                                    user_id=user.id,
                                    activity_type='coordinator_summary',
                                    title=f'Итог цикла: {_goals_titles}'[:120],
                                    content=_report_text,
                                    status='completed',
                                    result=_report_text[:800],
                                ))
                                _sum_sess.commit()
                                logger.info("[COORD] coordinator_summary saved to interactions+AAL for user %d", user.id)
                            except Exception as _sum_err:
                                logger.warning("[COORD] summary save failed: %s", _sum_err)
                                try:
                                    _sum_sess.rollback()
                                except Exception:
                                    pass
                            finally:
                                try:
                                    _sum_sess.close()
                                except Exception:
                                    pass
                        except Exception as _sum_outer:
                            logger.warning("[COORD] summary session setup failed: %s", _sum_outer)
                        if self.bot:
                            try:
                                await self.bot.send_message(
                                    chat_id=user.telegram_id,
                                    text=f"ASI (итог):\n\n{_report_text}",
                                )
                            except Exception as _e:
                                logger.debug("suppressed: %s", _e)
                except Exception as _rep_err:
                    logger.debug("[COORD] final report error: %s", _rep_err)

            return True  # coordinator ran

        except Exception as _coord_main_err:
            logger.warning("[COORD] coordinator dispatch error: %s", _coord_main_err)
            # Очищаем сессию — иначе round-robin fallback упадёт на commit
            try:
                session.rollback()
            except Exception:
                pass
            return False

    # ═══════════════════════════════════════════════════════
    # SCAN — обнаружение якорей
    # ═══════════════════════════════════════════════════════

    async def _scan_anchors(self, user, session) -> list:
        """Сканирует ВСЕ источники данных, создаёт якоря.
        
        Не создаёт дубликаты — проверяет наличие необработанного якоря того же типа+source.
        """
        anchors = []

        # Получаем профиль
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()

        user_tz = pytz.timezone(user.timezone or 'Europe/Moscow')
        user_now = datetime.now(user_tz)
        now_utc = datetime.now(timezone.utc)

        # --- ЗАДАЧИ ---
        anchors.extend(self._scan_tasks(user, session, user_tz, user_now, now_utc))

        # --- ЦЕЛИ ---
        anchors.extend(self._scan_goals(user, session, now_utc))

        # --- ПРОФИЛЬ ---
        anchors.extend(self._scan_profile(user, profile, session))

        # --- ДЕЛЕГИРОВАНИЕ (открыто всем — оплата токенами) ---
        anchors.extend(self._scan_delegation(user, session, now_utc))

        # --- КОНТАКТЫ ---
        anchors.extend(self._scan_contacts(user, session, now_utc))

        # --- ДИАЛОГ (follow-up из LTM) ---
        anchors.extend(self._scan_dialog_followup(user, session, now_utc))

        # --- РЫНОК/КОНТЕНТ (открыто всем) ---
        anchors.extend(self._scan_premium_insights(user, profile, session, now_utc))

        # --- СОБЫТИЯ / МЕРОПРИЯТИЯ ---
        anchors.extend(self._scan_events(user, profile, session, now_utc))

        # --- ВХОДЯЩИЕ СООБЩЕНИЯ ---
        anchors.extend(self._scan_incoming_messages(user, session, now_utc))

        # --- НИЗКИЙ БАЛАНС ТОКЕНОВ ---
        anchors.extend(self._scan_token_low_balance(user, session, now_utc))

        # --- ПРОСРОЧЕННЫЕ ДЕЛЕГИРОВАНИЯ ---
        anchors.extend(self._scan_delegation_overdue(user, session, now_utc))

        # --- ДЕКОМПОЗИЦИЯ ЦЕЛЕЙ БЕЗ ЗАДАЧ ---
        anchors.extend(self._scan_goal_decomposition(user, session, now_utc))

        # --- РЕАКТИВАЦИЯ НЕАКТИВНЫХ ---
        anchors.extend(self._scan_inactivity_reengagement(user, session, now_utc))

        # --- ПОСТЫ В ЛЕНТУ (все) ---
        anchors.extend(self._scan_post_opportunities(user, profile, session, now_utc))

        # --- ПОСТЫ В КАНАЛ (если указан канал) ---
        if user.telegram_channel:
            anchors.extend(self._scan_channel_post(user, profile, session, now_utc))

        # --- ПОСТЫ В DISCORD (если настроен webhook) ---
        if getattr(user, 'discord_webhook', None):
            anchors.extend(self._scan_discord_post(user, profile, session, now_utc))

        # --- МАЙЛСТОНЫ: недельные итоги + прогресс целей ---
        anchors.extend(self._scan_weekly_milestone(user, session, now_utc))
        anchors.extend(self._scan_goal_milestone(user, session, now_utc))

        # --- EMAIL OUTREACH (автономная отправка + фоллоу-апы + уведомления о reply) ---
        anchors.extend(self._scan_email_outreach(user, session, now_utc))

        # --- КОНТЕНТ-КАМПАНИИ (автономная публикация по расписанию) ---
        anchors.extend(self._scan_content_campaigns(user, session, now_utc))

        # --- КАМПАНИИ ДЕЛЕГИРОВАНИЯ (автономное распределение задач) ---
        anchors.extend(self._scan_delegation_campaigns(user, session, now_utc))

        # --- ДЕГРАДАЦИЯ СЕРВИСОВ (service_health) ---
        anchors.extend(self._scan_service_degraded(user, session, now_utc))

        # --- СБОИ СКРИПТОВ АГЕНТОВ (сломанные ключи/интеграции) ---
        anchors.extend(self._scan_agent_script_failures(user, session, now_utc))

        # --- АГЕНТЫ БЕЗ РЕЗУЛЬТАТОВ (скрипт работает, но stdout пуст N раз подряд) ---
        anchors.extend(self._scan_agent_silent(user, session, now_utc))

        # --- СТАГНАЦИЯ КАМПАНИЙ (email/контент/делегирование — активна, но 0 активности 3+ дня) ---
        anchors.extend(self._scan_campaign_stagnation(user, session, now_utc))

        # --- ЭКСТРЕМАЛЬНАЯ ПОГОДА ---
        anchors.extend(await self._scan_weather_extreme(user, profile, now_utc))

        # --- НЕУДАЧНЫЕ ПЛАТЕЖИ ---
        anchors.extend(self._scan_payment_failed(user, session, now_utc))

        # --- КАСТОМНЫЕ ЯКОРЯ АГЕНТОВ (UserAgent.custom_anchors) ---
        anchors.extend(self._scan_custom_anchors(user, session, user_tz, user_now, now_utc))

        # --- ВХОДЯЩИЕ ПИСЬМА АГЕНТОВ (IMAP) ---
        anchors.extend(self._scan_agent_inbox_replies(user, session, now_utc))

        # --- ЗАБЛОКИРОВАННЫЕ АГЕНТЫ (нужно решение пользователя) ---
        anchors.extend(self._scan_agent_task_blocked(user, session, now_utc))

        # --- FOLLOW-UP РЕЗУЛЬТАТОВ АГЕНТОВ (проверка незакрытых dispatch-задач) ---
        anchors.extend(self._scan_agent_followup(user, session, now_utc))

        # --- АВТОПИЛОТ ЦЕЛЕЙ (AI автономно продвигает цели) ---
        anchors.extend(self._scan_goal_autopilot(user, profile, session, now_utc))

        # --- DDG WEB ENRICHMENT: обогащаем якоря реальными данными из интернета ---
        # Пропускаем если DDG сервис известен как недоступный (service_degraded:ddg pending)
        _ddg_down = any(
            a.anchor_type == 'service_degraded' and a.source == 'service_health:ddg'
            for a in anchors  # новые кандидаты ещё не в БД, но existing тоже проверим
        ) or session.query(Anchor).filter(
            Anchor.user_id == user.id,
            Anchor.anchor_type == 'service_degraded',
            Anchor.source == 'service_health:ddg',
            Anchor.delivered_at.is_(None),
        ).first() is not None
        if not _ddg_down:
            try:
                anchors = await asyncio.wait_for(
                    self._enrich_anchors_with_ddg(anchors, profile),
                    timeout=15.0
                )
            except asyncio.TimeoutError:
                logger.warning("[ANCHOR-DDG] Enrichment timed out (15s) — DDG likely down, skipping")
            except Exception as _ddg_err:
                logger.warning(f"[ANCHOR-DDG] Enrichment error (skipping): {_ddg_err}")
        else:
            logger.debug("[ANCHOR-DDG] DDG service degraded — skipping web enrichment")

        # Дедупликация: не создаём якорь если уже есть недоставленный с тем же type+source
        # with_for_update() сериализует запись между двумя параллельными инстансами (Railway deploy)
        try:
            try:
                existing = session.query(Anchor).filter(
                    Anchor.user_id == user.id,
                    Anchor.delivered_at.is_(None)
                ).with_for_update(nowait=True).all()
            except Exception:
                # SQLite не поддерживает FOR UPDATE / nowait — fallback без блокировки
                existing = session.query(Anchor).filter(
                    Anchor.user_id == user.id,
                    Anchor.delivered_at.is_(None)
                ).all()
        except Exception:
            logger.info(f"[ANCHOR] User {user.id}: scan skipped (locked by another instance)")
            return []
        # Exclude expired-but-undelivered anchors from dedup — they should not block
        # creation of fresh anchors of the same type/source
        def _exp_ok(anchor_obj):
            if anchor_obj.expires_at is None:
                return True
            exp = anchor_obj.expires_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            return exp > now_utc
        existing_keys = {(a.anchor_type, a.source) for a in existing if _exp_ok(a)}
        # Also check recently DELIVERED anchors for email_reply_received — prevent
        # duplicate replies when AI doesn't set ai_reply_sent_at
        # agent_delegation убран: без push-дедупликации не нужен (якорь всегда с новым source)
        _DEDUP_WITH_DELIVERED = {'email_reply_received'}
        try:
            _recent_delivered = session.query(Anchor).filter(
                Anchor.user_id == user.id,
                Anchor.delivered_at.isnot(None),
                Anchor.anchor_type.in_(list(_DEDUP_WITH_DELIVERED)),
                Anchor.created_at >= now_utc - timedelta(hours=24),
            ).all()
            for a in _recent_delivered:
                existing_keys.add((a.anchor_type, a.source))
        except Exception as _e:
            logger.debug("suppressed: %s", _e)
        # Singleton types: only one undelivered anchor per type (regardless of source)
        _SINGLETON_TYPES = {
            'service_degraded', 'token_low_balance', 'weather_extreme',
            'profile_gap', 'morning_plan', 'evening_review',
            'inactivity_reengagement',
        }
        # Exclude expired anchors from singleton check — expired-but-undelivered anchors
        # should NOT block creation of fresh ones
        existing_types = {a.anchor_type for a in existing if _exp_ok(a)}

        unique_anchors = []
        for a in anchors:
            if a.anchor_type in _SINGLETON_TYPES:
                if a.anchor_type in existing_types:
                    continue
                existing_types.add(a.anchor_type)
                unique_anchors.append(a)
            else:
                key = (a.anchor_type, a.source)
                if key not in existing_keys:
                    existing_keys.add(key)
                    unique_anchors.append(a)

        return unique_anchors

    async def _enrich_anchors_with_ddg(self, anchors: list, profile) -> list:
        """Обогащает якоря реальными данными из DuckDuckGo.

        Затрагивает типы: event_discovery, market_insight, content_opportunity.
        Добавляет результаты поиска прямо в data якоря, чтобы AI получил конкретные факты.
        Бюджет: ~3-5 DDG-запросов на пользователя за скан, кэш 2ч.
        """
        try:
            from ai_integration.api_client import get_api_client
            api = get_api_client()

            for anchor in anchors:
                try:
                    data = json.loads(anchor.data) if anchor.data else {}
                except (json.JSONDecodeError, TypeError):
                    continue

                enriched = False

                if anchor.anchor_type == 'event_discovery':
                    # Выполняем search_query, который уже сформирован в _scan_events
                    query = data.get('search_query', '')
                    city = data.get('city', '')
                    if query:
                        if city and city.lower() not in query.lower():
                            query += f' {city}'
                        try:
                            results = await asyncio.wait_for(api.duckduckgo_search(query, num=5, cache_ttl=7200), timeout=8.0)
                        except (asyncio.TimeoutError, Exception):
                            results = []
                        if results:
                            data['web_events'] = [
                                {'title': r.get('title', ''), 'snippet': r.get('snippet', '')[:200], 'url': r.get('link', '')}
                                for r in results[:5]
                            ]
                            enriched = True
                            logger.info(f"[ANCHOR-DDG] event_discovery enriched with {len(results)} results")

                elif anchor.anchor_type == 'market_insight':
                    niche = data.get('niche', '')
                    if niche:
                        from datetime import datetime as dt
                        year = dt.now().strftime('%Y')
                        news_query = f'{niche[:50]} новости тренды {year}'
                        try:
                            results = await asyncio.wait_for(api.duckduckgo_search(news_query, num=5, cache_ttl=7200), timeout=8.0)
                        except (asyncio.TimeoutError, Exception):
                            results = []
                        if results:
                            data['fresh_insights'] = [
                                {'title': r.get('title', ''), 'snippet': r.get('snippet', '')[:200], 'url': r.get('link', '')}
                                for r in results[:5]
                            ]
                            enriched = True
                            logger.info(f"[ANCHOR-DDG] market_insight enriched with {len(results)} results")

                elif anchor.anchor_type == 'content_opportunity':
                    niche = data.get('niche', '')
                    content_strategy = data.get('content_strategy', '')
                    topic = content_strategy[:50] if content_strategy else niche[:50]
                    if topic:
                        ideas_query = f'{topic} контент идеи тренды'
                        try:
                            results = await asyncio.wait_for(api.duckduckgo_search(ideas_query, num=5, cache_ttl=7200), timeout=8.0)
                        except (asyncio.TimeoutError, Exception):
                            results = []
                        if results:
                            data['content_ideas_from_web'] = [
                                {'title': r.get('title', ''), 'snippet': r.get('snippet', '')[:200], 'url': r.get('link', '')}
                                for r in results[:5]
                            ]
                            enriched = True
                            logger.info(f"[ANCHOR-DDG] content_opportunity enriched with {len(results)} results")

                if enriched:
                    anchor.data = json.dumps(data, ensure_ascii=False)

        except Exception as e:
            logger.warning(f"[ANCHOR-DDG] Enrichment failed (non-critical): {e}")

        return anchors

    def _scan_tasks(self, user, session, user_tz, user_now, now_utc) -> list:
        """Сканирует задачи: просроченные, ближайшие дедлайны, застойные"""
        anchors = []

        tasks = session.query(Task).filter(
            Task.user_id == user.id,
            Task.status.in_(['pending', 'in_progress', 'active'])
        ).all()

        for task in tasks:
            if task.reminder_time:
                rt = task.reminder_time
                if rt.tzinfo is None:
                    rt = rt.replace(tzinfo=timezone.utc)

                minutes_diff = (rt - now_utc).total_seconds() / 60

                # ТОЧНОЕ НАПОМИНАНИЕ: reminder_time наступило (от 0 до -30 мин) и ещё не отправлено
                if -30 <= minutes_diff <= 0 and not getattr(task, 'reminder_sent', False):
                    scan_delay = int(abs(minutes_diff))  # 0..30 мин — шаг сканирования, НЕ просрочка
                    try:
                        rt_local = rt.astimezone(user_tz)
                        sched_time_str = rt_local.strftime('%H:%M')
                    except Exception:
                        sched_time_str = '??:??'
                    if scan_delay <= 2:
                        reminder_topic = _t(user,
                            f'Напоминание: задача «{task.title}» — запланировано {sched_time_str}, сработало точно по расписанию',
                            f'Reminder: task «{task.title}» — scheduled {sched_time_str}, triggered on time')
                    else:
                        reminder_topic = _t(user,
                            f'Напоминание: задача «{task.title}» — запланировано {sched_time_str}, задержка {scan_delay} мин из-за шага сканирования (НЕ просрочено)',
                            f'Reminder: task «{task.title}» — scheduled {sched_time_str}, {scan_delay}min scan delay (NOT overdue)')
                    anchors.append(Anchor(
                        user_id=user.id,
                        anchor_type='task_reminder',
                        source=f'task:{task.id}',
                        topic=reminder_topic,
                        priority=AnchorPriority.CRITICAL,
                        data=json.dumps({'task_id': task.id, 'title': task.title,
                                        'description': (task.description or '')[:200],
                                        'reminder_type': 'exact',
                                        'scheduled_time': sched_time_str,
                                        'scan_delay_minutes': scan_delay}),
                        triggered_at=now_utc,
                        expires_at=now_utc + timedelta(minutes=90),  # 90 мин — запас на cold start
                        cooldown_hours=0.5,
                        batch_group='tasks',
                    ))
                    # reminder_sent НЕ ставим здесь — ставим только при доставке
                    # (иначе если якорь истечёт до доставки — напоминание потеряно навсегда)
                    # Дедупликация обеспечивается existing_keys в _scan_anchors

                # ПРОСРОЧЕННЫЕ (более 30 мин назад)
                # Доставляем ВСЕГДА — reminder_sent ставим при реальной доставке, не здесь
                elif minutes_diff < -30:
                    hours_overdue = abs(minutes_diff) / 60
                    anchors.append(Anchor(
                        user_id=user.id,
                        anchor_type='task_overdue',
                        source=f'task:{task.id}',
                        topic=_t(user, f'Задача «{task.title}» просрочена на {int(hours_overdue)}ч', f'Task «{task.title}» overdue by {int(hours_overdue)}h'),
                        priority=AnchorPriority.CRITICAL,
                        data=json.dumps({'task_id': task.id, 'title': task.title,
                                        'hours_overdue': round(hours_overdue, 1),
                                        'description': (task.description or '')[:200]}),
                        triggered_at=now_utc,
                        expires_at=now_utc + timedelta(hours=24),
                        cooldown_hours=2,
                        batch_group='tasks',
                    ))

                # ДЕДЛАЙН СКОРО (от 15 мин до 24ч до reminder_time)
                # Нижний порог 15 мин — ближе этого task_reminder сам справится
                elif 15 <= minutes_diff <= 24 * 60:
                    hours_left = minutes_diff / 60
                    anchors.append(Anchor(
                        user_id=user.id,
                        anchor_type='task_deadline_soon',
                        source=f'task:{task.id}',
                        topic=_t(user, f'Задача «{task.title}» — дедлайн через {int(hours_left)}ч', f'Task «{task.title}» — deadline in {int(hours_left)}h'),
                        priority=AnchorPriority.HIGH,
                        data=json.dumps({'task_id': task.id, 'title': task.title,
                                        'hours_left': round(hours_left, 1)}),
                        triggered_at=now_utc,
                        expires_at=rt,
                        cooldown_hours=4,
                        batch_group='tasks',
                    ))

            # due_date без reminder_time — проверяем просрочку/дедлайн по due_date
            elif task.due_date and not task.reminder_time:
                dd = task.due_date
                if dd.tzinfo is None:
                    dd = dd.replace(tzinfo=timezone.utc)
                minutes_diff_dd = (dd - now_utc).total_seconds() / 60
                if minutes_diff_dd < -30:
                    hours_overdue = abs(minutes_diff_dd) / 60
                    anchors.append(Anchor(
                        user_id=user.id,
                        anchor_type='task_overdue',
                        source=f'task:{task.id}',
                        topic=_t(user, f'Задача «{task.title}» просрочена на {int(hours_overdue)}ч', f'Task «{task.title}» overdue by {int(hours_overdue)}h'),
                        priority=AnchorPriority.CRITICAL,
                        data=json.dumps({'task_id': task.id, 'title': task.title,
                                        'hours_overdue': round(hours_overdue, 1),
                                        'description': (task.description or '')[:200]}),
                        triggered_at=now_utc,
                        expires_at=now_utc + timedelta(hours=24),
                        cooldown_hours=2,
                        batch_group='tasks',
                    ))
                elif 15 <= minutes_diff_dd <= 24 * 60:
                    hours_left = minutes_diff_dd / 60
                    anchors.append(Anchor(
                        user_id=user.id,
                        anchor_type='task_deadline_soon',
                        source=f'task:{task.id}',
                        topic=_t(user, f'Задача «{task.title}» — дедлайн через {int(hours_left)}ч', f'Task «{task.title}» — deadline in {int(hours_left)}h'),
                        priority=AnchorPriority.HIGH,
                        data=json.dumps({'task_id': task.id, 'title': task.title,
                                        'hours_left': round(hours_left, 1)}),
                        triggered_at=now_utc,
                        expires_at=dd,
                        cooldown_hours=4,
                        batch_group='tasks',
                    ))

            # Застойные: задача создана > 7 дней назад, без прогресса
            if task.created_at:
                ct = task.created_at
                if ct.tzinfo is None:
                    ct = ct.replace(tzinfo=timezone.utc)
                age_days = (now_utc - ct).days
                if age_days >= 7:
                    anchors.append(Anchor(
                        user_id=user.id,
                        anchor_type='task_stale',
                        source=f'task:{task.id}',
                        topic=_t(user, f'Задача «{task.title}» висит уже {age_days} дней', f'Task «{task.title}» stale for {age_days} days'),
                        priority=AnchorPriority.LOW,
                        data=json.dumps({'task_id': task.id, 'title': task.title, 'age_days': age_days}),
                        triggered_at=now_utc,
                        expires_at=now_utc + timedelta(days=3),
                        cooldown_hours=24,
                        batch_group='tasks',
                    ))

            # ПРОВЕРКА РЕЗУЛЬТАТА: задача с reminder_sent, estimated_duration, не проверена
            if (getattr(task, 'reminder_sent', False) 
                and getattr(task, 'estimated_duration', None)
                and not getattr(task, 'result_check_sent', False)):
                rt = task.reminder_time
                if rt and rt.tzinfo is None:
                    rt = rt.replace(tzinfo=timezone.utc)
                if rt:
                    result_check_time = rt + timedelta(minutes=task.estimated_duration)
                    if now_utc >= result_check_time:
                        anchors.append(Anchor(
                            user_id=user.id,
                            anchor_type='task_result_check',
                            source=f'task:{task.id}:result',
                            topic=_t(user, f'Время проверить результат задачи «{task.title}»', f'Time to check results for task «{task.title}»'),
                            priority=AnchorPriority.MEDIUM,
                            data=json.dumps({'task_id': task.id, 'title': task.title,
                                            'estimated_duration': task.estimated_duration}),
                            triggered_at=now_utc,
                            expires_at=now_utc + timedelta(hours=12),
                            cooldown_hours=6,
                            batch_group='tasks',
                        ))

        # Повторяющиеся задачи: проверяем нужно ли создать новый экземпляр
        recurring_tasks = session.query(Task).filter(
            Task.user_id == user.id,
            Task.is_recurring == True,
            Task.status.in_(['pending', 'in_progress', 'active', 'completed'])
        ).all()

        # Batch-load all child task instances for recurring tasks (avoid N+1)
        _recur_ids = [rt.id for rt in recurring_tasks]
        _recur_children_all = session.query(Task).filter(
            Task.parent_task_id.in_(_recur_ids)
        ).order_by(Task.reminder_time.desc()).all() if _recur_ids else []
        # latest child per parent (desc order → first seen = latest)
        _recur_last_by_parent: dict = {}
        _recur_children_by_parent: dict = {}
        for _rc in _recur_children_all:
            if _rc.parent_task_id not in _recur_last_by_parent:
                _recur_last_by_parent[_rc.parent_task_id] = _rc
            _recur_children_by_parent.setdefault(_rc.parent_task_id, []).append(_rc)

        for rtask in recurring_tasks:
            if rtask.reminder_time and rtask.recurrence_pattern:
                rt = rtask.reminder_time
                if rt.tzinfo is None:
                    rt = rt.replace(tzinfo=timezone.utc)
                # Проверяем: последний экземпляр уже в прошлом?
                last_instance = _recur_last_by_parent.get(rtask.id)
                
                last_time = last_instance.reminder_time if last_instance else rt
                if last_time and last_time.tzinfo is None:
                    last_time = last_time.replace(tzinfo=timezone.utc)
                
                if last_time and last_time < now_utc:
                    # Создаём новый экземпляр повторяющейся задачи
                    next_time = self._calculate_next_recurrence(last_time, rtask.recurrence_pattern, rtask.recurrence_interval or 1)
                    # Проверяем что такой экземпляр ещё не создан (используем предзагруженные дочерние задачи)
                    existing = next(
                        (_c for _c in _recur_children_by_parent.get(rtask.id, []) if _c.reminder_time == next_time),
                        None
                    )
                    if not existing:
                        new_task = Task(
                            user_id=rtask.user_id,
                            title=rtask.title,
                            description=rtask.description,
                            reminder_time=next_time,
                            parent_task_id=rtask.id
                        )
                        session.add(new_task)
                        try:
                            session.commit()
                            logger.info(f"[ANCHOR] Created recurring instance for task {rtask.id}: '{rtask.title}' at {next_time}")
                        except Exception:
                            session.rollback()
                    
                    anchors.append(Anchor(
                        user_id=user.id,
                        anchor_type='recurring_task_due',
                        source=f'task:{rtask.id}:recurring',
                        topic=_t(user, f'Повторяющаяся задача «{rtask.title}» — создан новый экземпляр', f'Recurring task «{rtask.title}» — new instance created'),
                        priority=AnchorPriority.MEDIUM,
                        data=json.dumps({'task_id': rtask.id, 'title': rtask.title,
                                        'pattern': rtask.recurrence_pattern,
                                        'interval': rtask.recurrence_interval or 1}),
                        triggered_at=now_utc,
                        expires_at=now_utc + timedelta(hours=12),
                        cooldown_hours=4,
                        batch_group='tasks',
                    ))

        # Стрик завершений: если за последние 24ч завершено >= 3 задач
        recent_completed = session.query(Task).filter(
            Task.user_id == user.id,
            Task.status == 'completed',
            Task.actual_completion_time >= now_utc - timedelta(hours=24)
        ).count()

        if recent_completed >= 3:
            anchors.append(Anchor(
                user_id=user.id,
                anchor_type='task_completed_streak',
                source=f'streak:{now_utc.strftime("%Y-%m-%d")}',  # once per day, not per count
                topic=_t(user, f'За последние 24ч завершено {recent_completed} задач', f'{recent_completed} tasks completed in the last 24h'),
                priority=AnchorPriority.MEDIUM,
                data=json.dumps({'completed_count': recent_completed}),
                triggered_at=now_utc,
                expires_at=now_utc + timedelta(hours=12),
                cooldown_hours=24,
                batch_group='tasks',
            ))

        return anchors

    def _scan_goals(self, user, session, now_utc) -> list:
        """Сканирует цели: прогресс, застой, горящие дедлайны"""
        anchors = []

        goals = session.query(Goal).filter(
            Goal.user_id == user.id,
            Goal.status == 'active'
        ).all()

        for goal in goals:
            # Почти достигнута (>= 70%)
            if goal.progress_percentage >= 70 and goal.progress_percentage < 100:
                anchors.append(Anchor(
                    user_id=user.id,
                    anchor_type='goal_progress',
                    source=f'goal:{goal.id}',
                    topic=_t(user, f'Цель «{goal.title}» на {goal.progress_percentage}% — почти!', f'Goal «{goal.title}» at {goal.progress_percentage}% — almost there!'),
                    priority=AnchorPriority.MEDIUM,
                    data=json.dumps({'goal_id': goal.id, 'title': goal.title,
                                    'progress': goal.progress_percentage}),
                    triggered_at=now_utc,
                    expires_at=now_utc + timedelta(days=2),
                    cooldown_hours=48,
                    batch_group='goals',
                ))

            # Застой: создана > 14 дней, прогресс 0%
            if goal.created_at and goal.progress_percentage == 0:
                ct = goal.created_at
                if ct.tzinfo is None:
                    ct = ct.replace(tzinfo=timezone.utc)
                age_days = (now_utc - ct).days
                if age_days >= 14:
                    anchors.append(Anchor(
                        user_id=user.id,
                        anchor_type='goal_stagnation',
                        source=f'goal:{goal.id}',
                        topic=_t(user, f'Цель «{goal.title}» — {age_days} дней без прогресса', f'Goal «{goal.title}» — {age_days} days without progress'),
                        priority=AnchorPriority.LOW,
                        data=json.dumps({'goal_id': goal.id, 'title': goal.title,
                                        'age_days': age_days}),
                        triggered_at=now_utc,
                        expires_at=now_utc + timedelta(days=7),
                        cooldown_hours=72,
                        batch_group='goals',
                    ))

            # Горящий дедлайн (< 3 дней)
            if goal.target_date:
                td = goal.target_date
                if td.tzinfo is None:
                    td = td.replace(tzinfo=timezone.utc)
                days_left = (td - now_utc).days
                if 0 <= days_left <= 3:
                    anchors.append(Anchor(
                        user_id=user.id,
                        anchor_type='goal_deadline',
                        source=f'goal:{goal.id}',
                        topic=_t(user, f'Цель «{goal.title}» — дедлайн через {days_left} дн, прогресс {goal.progress_percentage}%', f'Goal «{goal.title}» — deadline in {days_left}d, progress {goal.progress_percentage}%'),
                        priority=AnchorPriority.HIGH,
                        data=json.dumps({'goal_id': goal.id, 'title': goal.title,
                                        'days_left': days_left, 'progress': goal.progress_percentage}),
                        triggered_at=now_utc,
                        expires_at=td,
                        cooldown_hours=12,
                        batch_group='goals',
                    ))

        return anchors

    def _scan_profile(self, user, profile, session) -> list:
        """Проверяет пробелы в профиле"""
        anchors = []
        now_utc = datetime.now(timezone.utc)

        if not profile:
            anchors.append(Anchor(
                user_id=user.id,
                anchor_type='profile_gap',
                source='profile:empty',
                topic='Профиль не заполнен — агент не может эффективно помогать' if getattr(user, 'language', 'ru') != 'en' else 'Profile is empty — agent cannot help effectively',
                priority=AnchorPriority.MEDIUM,
                data=json.dumps({'missing': ['skills', 'interests', 'goals', 'city', 'position']}),
                triggered_at=now_utc,
                expires_at=now_utc + timedelta(days=7),
                cooldown_hours=48,
                batch_group='engagement',
            ))
            return anchors

        missing = []
        _is_en = getattr(user, 'language', 'ru') == 'en'
        if not profile.skills or not profile.skills.strip():
            missing.append('skills' if _is_en else 'навыки')
        if not profile.interests or not profile.interests.strip():
            missing.append('interests' if _is_en else 'интересы')
        if not profile.goals or not profile.goals.strip():
            missing.append('goals' if _is_en else 'цели')

        if len(missing) >= 2:
            anchors.append(Anchor(
                user_id=user.id,
                anchor_type='profile_gap',
                source=f'profile:missing:{",".join(missing)}',
                topic=_t(user, f'В профиле не хватает: {", ".join(missing)}', f'Profile missing: {", ".join(missing)}'),
                priority=AnchorPriority.LOW,
                data=json.dumps({'missing': missing}),
                triggered_at=now_utc,
                expires_at=now_utc + timedelta(days=7),
                cooldown_hours=72,
                batch_group='engagement',
            ))

        return anchors

    def _scan_delegation(self, user, session, now_utc) -> list:
        """Сканирует статус делегированных задач (STANDARD+)"""
        anchors = []

        # Задачи, делегированные ПОЛЬЗОВАТЕЛЕМ, со статусом pending (не принято)
        pending_delegated = session.query(Task).filter(
            Task.user_id == user.id,
            Task.delegated_to_username.isnot(None),
            Task.delegation_status == 'pending',
            Task.status.in_(['pending', 'in_progress'])
        ).all()

        for task in pending_delegated:
            if task.created_at:
                ct = task.created_at
                if ct.tzinfo is None:
                    ct = ct.replace(tzinfo=timezone.utc)
                hours_waiting = (now_utc - ct).total_seconds() / 3600
                if hours_waiting >= 4:  # Ждёт > 4ч
                    anchors.append(Anchor(
                        user_id=user.id,
                        anchor_type='delegation_pending',
                        source=f'task:{task.id}:delegation',
                        topic=_t(user, f'Делегированная задача «{task.title}» → @{task.delegated_to_username} — ждёт ответа {int(hours_waiting)}ч', f'Delegated task «{task.title}» → @{task.delegated_to_username} — waiting {int(hours_waiting)}h'),
                        priority=AnchorPriority.HIGH,
                        data=json.dumps({'task_id': task.id, 'title': task.title,
                                        'delegated_to': task.delegated_to_username,
                                        'hours_waiting': round(hours_waiting, 1)}),
                        triggered_at=now_utc,
                        expires_at=now_utc + timedelta(hours=24),
                        cooldown_hours=6,
                        batch_group='delegation',
                    ))

        # Задачи с обновлённым статусом делегирования (accepted/completed/rejected)
        updated_delegated = session.query(Task).filter(
            Task.user_id == user.id,
            Task.delegated_to_username.isnot(None),
            Task.delegation_status.in_(['accepted', 'completed', 'rejected']),
            Task.status.in_(['pending', 'in_progress'])
        ).all()

        for task in updated_delegated:
            # Для rejected — HIGH приоритет, короткий cooldown
            is_rejected = task.delegation_status == 'rejected'
            anchors.append(Anchor(
                user_id=user.id,
                anchor_type='delegation_update',
                source=f'task:{task.id}:status:{task.delegation_status}',
                topic=_t(user, f'Задача «{task.title}» — @{task.delegated_to_username} {task.delegation_status}', f'Task «{task.title}» — @{task.delegated_to_username} {task.delegation_status}'),
                priority=AnchorPriority.HIGH if is_rejected else AnchorPriority.HIGH,
                data=json.dumps({'task_id': task.id, 'title': task.title,
                                'delegated_to': task.delegated_to_username,
                                'status': task.delegation_status}),
                triggered_at=now_utc,
                expires_at=now_utc + timedelta(hours=24),
                cooldown_hours=4 if is_rejected else 12,
                batch_group='delegation',
            ))

        return anchors

    def _scan_contacts(self, user, session, now_utc) -> list:
        """Сканирует алерты контактов (все тарифы)"""
        anchors = []

        contact_alerts = session.query(ContactAlert).filter_by(
            user_id=user.id, enabled=True
        ).all()

        if not contact_alerts:
            return anchors

        # Недавно обновлённые профили
        yesterday = now_utc - timedelta(days=1)
        recent_profiles = session.query(UserProfile).filter(
            UserProfile.user_id != user.id,
            UserProfile.updated_at >= yesterday
        ).limit(20).all()

        # Batch-load users for recent_profiles (avoid N+1 inside nested loops)
        _cp_prof_uids = [p.user_id for p in recent_profiles]
        _cp_user_by_id = {u.id: u for u in session.query(User).filter(User.id.in_(_cp_prof_uids)).all()} if _cp_prof_uids else {}

        for alert in contact_alerts[:3]:
            for prof in recent_profiles:
                match = False
                if alert.skill and prof.skills and alert.skill.lower() in prof.skills.lower():
                    match = True
                if alert.interest and prof.interests and alert.interest.lower() in prof.interests.lower():
                    match = True
                if match and alert.city:
                    # Check city using all normalized variants (cross-language: EN/RU/raw)
                    alert_city_lc = alert.city.strip().lower()
                    prof_city_variants = set(filter(None, [
                        (getattr(prof, 'city_normalized', None) or '').strip().lower(),
                        (getattr(prof, 'city_normalized_ru', None) or '').strip().lower(),
                        (prof.city or '').strip().lower(),
                    ]))
                    city_match = any(
                        alert_city_lc in v or v.startswith(alert_city_lc) or alert_city_lc.startswith(v)
                        for v in prof_city_variants if v
                    )
                    if not city_match:
                        match = False

                if match:
                    prof_user = _cp_user_by_id.get(prof.user_id)
                    if prof_user and prof_user.username:
                        detail = alert.skill or alert.interest
                        anchors.append(Anchor(
                            user_id=user.id,
                            anchor_type='contact_match',
                            source=f'contact:@{prof_user.username}',
                            topic=_t(user, f'Новый специалист @{prof_user.username} ({detail})', f'New specialist @{prof_user.username} ({detail})'),
                            priority=AnchorPriority.MEDIUM,
                            data=json.dumps({
                                'username': prof_user.username,
                                'skill': alert.skill,
                                'interest': alert.interest,
                                'city': prof.city,
                                'position': prof.position
                            }),
                            triggered_at=now_utc,
                            expires_at=now_utc + timedelta(days=2),
                            cooldown_hours=24,
                            batch_group='contacts',
                        ))
                        break  # Один контакт за алерт

        return anchors

    def _scan_dialog_followup(self, user, session, now_utc) -> list:
        """Проверяет незавершённые темы из истории диалога"""
        anchors = []

        # Последнее сообщение пользователя
        last_user_msg = session.query(Interaction).filter(
            Interaction.user_id == user.id,
            Interaction.message_type == 'user'
        ).order_by(Interaction.created_at.desc()).first()

        if not last_user_msg:
            return anchors

        li_time = last_user_msg.created_at
        if li_time.tzinfo is None:
            li_time = li_time.replace(tzinfo=timezone.utc)

        hours_since = (now_utc - li_time).total_seconds() / 3600

        # Если прошло 6-48ч — это хороший момент для follow-up
        if 6 <= hours_since <= 48:
            # Проверяем, был ли уже follow-up
            content_preview = (last_user_msg.content or '')[:100]
            if content_preview.strip():
                # Guard: не повторять если уже доставлен за последние 24ч
                try:
                    _src_dlg = f'dialog:{last_user_msg.id}'
                    _ld_dlg = session.query(Anchor.delivered_at).filter(
                        Anchor.user_id == user.id,
                        Anchor.source == _src_dlg,
                        Anchor.delivered_at.isnot(None),
                    ).order_by(Anchor.delivered_at.desc()).first()
                    if _ld_dlg:
                        _ld_dlg_t = _ld_dlg[0]
                        if _ld_dlg_t.tzinfo is None:
                            _ld_dlg_t = _ld_dlg_t.replace(tzinfo=timezone.utc)
                        if (now_utc - _ld_dlg_t).total_seconds() / 3600 < 24:
                            return anchors
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)
                anchors.append(Anchor(
                    user_id=user.id,
                    anchor_type='dialog_followup',
                    source=f'dialog:{last_user_msg.id}',
                    topic=_t(user, f'Последнее сообщение {int(hours_since)}ч назад: «{content_preview[:60]}...»', f'Last message {int(hours_since)}h ago: «{content_preview[:60]}...»'),
                    priority=AnchorPriority.LOW,
                    data=json.dumps({
                        'last_message': content_preview,
                        'hours_since': round(hours_since, 1)
                    }),
                    triggered_at=now_utc,
                    expires_at=now_utc + timedelta(hours=24),
                    cooldown_hours=24,
                    batch_group='engagement',
                ))

        return anchors

    def _scan_premium_insights(self, user, profile, session, now_utc) -> list:
        """Premium: мониторинг рынка, идеи контента"""
        anchors = []

        if not profile:
            return anchors

        interests = getattr(profile, 'interests', '') or ''
        goals = getattr(profile, 'goals', '') or ''
        content_strategy = getattr(profile, 'content_strategy', '') or ''
        niche = interests[:100] or goals[:100]

        if niche:
            anchors.append(Anchor(
                user_id=user.id,
                anchor_type='market_insight',
                source=f'market:{now_utc.strftime("%Y-%m-%d")}',
                topic=_t(user, f'Время проверить события в нише: {niche[:60]}', f'Time to check events in niche: {niche[:60]}'),
                priority=AnchorPriority.LOW,
                data=json.dumps({'niche': niche, 'goals': goals[:200]}),
                triggered_at=now_utc,
                expires_at=now_utc + timedelta(hours=24),
                cooldown_hours=24,
                batch_group='insights',
            ))

        if content_strategy or (user.telegram_channel):
            anchors.append(Anchor(
                user_id=user.id,
                anchor_type='content_opportunity',
                source=f'content:{now_utc.strftime("%Y-%m-%d")}',
                topic=_t(user, 'Время для контент-идеи', 'Time for a content idea'),
                priority=AnchorPriority.LOW,
                data=json.dumps({
                    'content_strategy': content_strategy[:300],
                    'channel': user.telegram_channel,
                    'niche': niche[:100]
                }),
                triggered_at=now_utc,
                expires_at=now_utc + timedelta(hours=24),
                cooldown_hours=24,
                batch_group='insights',
            ))

        return anchors

    def _scan_events(self, user, profile, session, now_utc) -> list:
        """Ищет актуальные мероприятия: по нише + по задачам контактов в городе."""
        anchors = []
        if not profile:
            return anchors

        interests = getattr(profile, 'interests', '') or ''
        goals = getattr(profile, 'goals', '') or ''
        position = getattr(profile, 'position', '') or ''
        niche = interests[:100] or goals[:100] or position[:60]
        city = getattr(profile, 'city', '') or ''

        # 1) Якорь по нише — ежедневный (AI сам решит нужно ли)
        if niche:
            anchors.append(Anchor(
                user_id=user.id,
                anchor_type='event_discovery',
                source=f'events:{now_utc.strftime("%Y-%m-%d")}',
                topic=_t(user, f'Поиск актуальных мероприятий по теме: {niche[:60]}', f'Searching for events on topic: {niche[:60]}'),
                priority=AnchorPriority.LOW,
                data=json.dumps({
                    'niche': niche,
                    'city': city,
                    'goals': goals[:200],
                    'search_query': f'конференции митапы события {niche[:40]} {now_utc.strftime("%B %Y")} онлайн офлайн'
                }),
                triggered_at=now_utc,
                expires_at=now_utc + timedelta(hours=24),
                cooldown_hours=24,
                batch_group='insights',
            ))

        # 2) Якорь «активности контактов» — сопоставляем ВСЕ данные профиля
        #    Интересы, навыки, цели, планы, задачи — ищем пересечения
        if city:
            # Собираем полный профиль пользователя для матчинга
            user_interests = (interests or '').lower()
            user_skills = (getattr(profile, 'skills', '') or '').lower()
            user_goals = (goals or '').lower()
            user_plans = (getattr(profile, 'current_plans', '') or '').lower()
            user_bio = (getattr(profile, 'bio', '') or '').lower()

            # Всё что характеризует пользователя — одной строкой для ИИ
            user_profile_text = ' '.join(filter(None, [
                user_interests, user_skills, user_goals, user_plans, user_bio,
                (getattr(profile, 'position', '') or '').lower()
            ]))

            if not user_profile_text.strip():
                return anchors

            # Ключевые слова из профиля — грубый pre-filter
            # Берём значимые слова (>3 букв) из интересов, навыков, целей
            profile_words = set()
            for field in [user_interests, user_skills, user_goals, user_plans]:
                for word in field.replace(',', ' ').replace(';', ' ').split():
                    w = word.strip().lower()
                    if len(w) > 3 and w not in ('для', 'что', 'как', 'это', 'мой', 'моя', 'при', 'или', 'так'):
                        profile_words.add(w)

            # Контакты в том же городе
            same_city_profiles = session.query(UserProfile).filter(
                UserProfile.user_id != user.id,
                UserProfile.city.ilike(f'%{city}%')
            ).limit(50).all()

            contact_user_ids = [p.user_id for p in same_city_profiles]
            contact_profiles_map = {p.user_id: p for p in same_city_profiles}

            if contact_user_ids:
                # Batch-load User objects for all contacts (avoid N+1 in activity loops)
                _ca_user_by_id = {u.id: u for u in session.query(User).filter(User.id.in_(contact_user_ids)).all()}

                # Задачи контактов за последние 7 дней
                week_ago = now_utc - timedelta(days=7)
                contact_tasks = session.query(Task).filter(
                    Task.user_id.in_(contact_user_ids),
                    Task.created_at >= week_ago,
                    Task.status.in_(['pending', 'in_progress', 'active'])
                ).limit(200).all()

                # Группируем активности по контакту
                contact_activities = {}  # user_id → {username, activities: [str], plans, interests, skills}
                for t in contact_tasks:
                    text = f'{t.title} {t.description or ""}'.lower()
                    # Грубый pre-filter: есть ли хоть одно слово-пересечение с профилем
                    match = any(pw in text for pw in profile_words) if profile_words else False
                    if not match:
                        continue
                    if t.user_id not in contact_activities:
                        c_user = _ca_user_by_id.get(t.user_id)
                        c_prof = contact_profiles_map.get(t.user_id)
                        contact_activities[t.user_id] = {
                            'username': c_user.username if c_user else 'unknown',
                            'activities': [],
                            'plans': (c_prof.current_plans or '')[:150] if c_prof else '',
                            'interests': (c_prof.interests or '')[:150] if c_prof else '',
                            'skills': (c_prof.skills or '')[:150] if c_prof else '',
                            'position': (c_prof.position or '')[:80] if c_prof else '',
                        }
                    date_str = ''
                    if t.reminder_time:
                        date_str = f' ({t.reminder_time.strftime("%d.%m %H:%M")})'
                    contact_activities[t.user_id]['activities'].append(
                        f'{t.title[:80]}{date_str}'
                    )

                # Также проверяем current_plans контактов (даже без задач)
                for cp in same_city_profiles:
                    plans = (cp.current_plans or '').lower()
                    if not plans or cp.user_id in contact_activities:
                        continue
                    if any(pw in plans for pw in profile_words):
                        c_user = _ca_user_by_id.get(cp.user_id)
                        if c_user and c_user.username:
                            contact_activities[cp.user_id] = {
                                'username': c_user.username,
                                'activities': [],
                                'plans': (cp.current_plans or '')[:150],
                                'interests': (cp.interests or '')[:150],
                                'skills': (cp.skills or '')[:150],
                                'position': (cp.position or '')[:80],
                            }

                if contact_activities:
                    # Берём до 5 самых релевантных контактов
                    top_contacts = list(contact_activities.values())[:5]
                    anchors.append(Anchor(
                        user_id=user.id,
                        anchor_type='contact_activity',
                        source=f'contact_activity:{now_utc.strftime("%Y-%m-%d")}',
                        topic=_t(user, f'Активности контактов в {city} совпадают с вашим профилем ({len(contact_activities)} чел)', f'Contact activities in {city} match your profile ({len(contact_activities)} people)'),
                        priority=AnchorPriority.MEDIUM,
                        data=json.dumps({
                            'city': city,
                            'user_profile': {
                                'interests': (interests or '')[:200],
                                'skills': (getattr(profile, 'skills', '') or '')[:200],
                                'goals': goals[:200],
                                'plans': (getattr(profile, 'current_plans', '') or '')[:200],
                            },
                            'contacts': top_contacts,
                        }),
                        triggered_at=now_utc,
                        expires_at=now_utc + timedelta(hours=24),
                        cooldown_hours=24,
                        batch_group='contacts',
                    ))

        return anchors

    # ═══════════════════════════════════════════════════════
    # ENGAGEMENT SCANNERS — сообщения, баланс, неактивность, декомпозиция
    # ═══════════════════════════════════════════════════════

    def _scan_incoming_messages(self, user, session, now_utc) -> list:
        """Уведомляет о непрочитанных входящих сообщениях (status='sent' или 'delivered')."""
        anchors = []

        unread = session.query(UserMessage).filter(
            UserMessage.recipient_id == user.id,
            UserMessage.status.in_(['sent', 'delivered']),
        ).all()

        if not unread:
            return anchors

        # Группируем по отправителю
        # Pre-fetch all senders (batch, avoid N+1)
        _unread_sids = list({msg.sender_id for msg in unread})
        _unread_senders = session.query(User).filter(User.id.in_(_unread_sids)).all()
        _unread_sender_by_id = {u.id: u for u in _unread_senders}

        senders = {}
        for msg in unread:
            sender = _unread_sender_by_id.get(msg.sender_id)
            uname = sender.username if sender else 'unknown'
            if uname not in senders:
                senders[uname] = []
            senders[uname].append(msg.message_text[:80])

        summaries = []
        _msg_suffix = 'msg' if getattr(user, 'language', 'ru') == 'en' else 'сообщ.'
        for uname, texts in list(senders.items())[:5]:
            summaries.append(f'@{uname}: {len(texts)} {_msg_suffix}')

        anchors.append(Anchor(
            user_id=user.id,
            anchor_type='incoming_message',
            source=f'messages:unread:{now_utc.strftime("%Y-%m-%d")}',  # дедупликация по дню (было по часу → дубли)
            topic=_t(user, f'{len(unread)} непрочитанных сообщений от {len(senders)} чел: {", ".join(summaries)}', f'{len(unread)} unread messages from {len(senders)} people: {", ".join(summaries)}'),
            priority=AnchorPriority.HIGH,
            data=json.dumps({
                'total': len(unread),
                'senders': {k: v[:3] for k, v in senders.items()},  # до 3 сообщений на отправителя
            }),
            triggered_at=now_utc,
            expires_at=now_utc + timedelta(hours=12),
            cooldown_hours=3,
            batch_group='engagement',
        ))

        return anchors

    def _scan_token_low_balance(self, user, session, now_utc) -> list:
        """Предупреждает когда баланс токенов критически низкий."""
        anchors = []

        balance = user.token_balance or 0
        # Порог: менее 50 токенов (≈3 проактивных сообщения)
        if balance >= 50:
            return anchors

        # Не предупреждаем если совсем 0 — тогда _process_user_inner и так пропустит
        if balance <= 0:
            return anchors

        msgs_left = balance // 15  # 15 токенов за проактивное сообщение

        anchors.append(Anchor(
            user_id=user.id,
            anchor_type='token_low_balance',
            source='tokens:low_balance',
            topic=_t(user, f'Баланс токенов: {balance} — хватит на ~{msgs_left} сообщений', f'Token balance: {balance} — enough for ~{msgs_left} messages'),
            priority=AnchorPriority.HIGH,
            data=json.dumps({
                'balance': balance,
                'messages_left': msgs_left,
            }),
            triggered_at=now_utc,
            expires_at=now_utc + timedelta(days=3),
            cooldown_hours=24,
            batch_group='engagement',
        ))

        return anchors

    def _scan_delegation_overdue(self, user, session, now_utc) -> list:
        """Задачи делегированы, приняты, но дедлайн прошёл — исполнитель не выполнил."""
        anchors = []

        overdue_delegated = session.query(Task).filter(
            Task.user_id == user.id,
            Task.delegated_to_username.isnot(None),
            Task.delegation_status == 'accepted',
            Task.status.in_(['pending', 'in_progress']),
            Task.reminder_time.isnot(None),
            Task.reminder_time < now_utc,
        ).all()

        for task in overdue_delegated:
            rt = task.reminder_time
            if rt.tzinfo is None:
                rt = rt.replace(tzinfo=timezone.utc)
            hours_overdue = (now_utc - rt).total_seconds() / 3600
            if hours_overdue >= 2:  # Просрочена > 2ч
                anchors.append(Anchor(
                    user_id=user.id,
                    anchor_type='delegation_overdue',
                    source=f'task:{task.id}:delegation_overdue',
                    topic=_t(user, f'Делегированная задача «{task.title}» → @{task.delegated_to_username} просрочена на {int(hours_overdue)}ч', f'Delegated task «{task.title}» → @{task.delegated_to_username} overdue by {int(hours_overdue)}h'),
                    priority=AnchorPriority.HIGH,
                    data=json.dumps({
                        'task_id': task.id,
                        'title': task.title,
                        'delegated_to': task.delegated_to_username,
                        'hours_overdue': round(hours_overdue, 1),
                        'deadline': rt.isoformat(),
                    }),
                    triggered_at=now_utc,
                    expires_at=now_utc + timedelta(hours=48),
                    cooldown_hours=8,
                    batch_group='delegation',
                ))

        return anchors

    def _scan_goal_decomposition(self, user, session, now_utc) -> list:
        """Активные цели без привязанных задач → предложить разбить на шаги."""
        anchors = []

        active_goals = session.query(Goal).filter(
            Goal.user_id == user.id,
            Goal.status == 'active',
        ).all()

        # Batch-load linked task counts per goal (avoid N+1 count query per goal)
        from sqlalchemy import func as _func_goal_scan
        _gd_goal_ids = [g.id for g in active_goals]
        _gd_task_counts = dict(session.query(Task.goal_id, _func_goal_scan.count(Task.id)).filter(
            Task.goal_id.in_(_gd_goal_ids),
            Task.status.in_(['pending', 'in_progress']),
        ).group_by(Task.goal_id).all()) if _gd_goal_ids else {}

        for goal in active_goals:
            # Проверяем есть ли ХОТЬ ОДНА активная задача, привязанная к цели
            linked_tasks = _gd_task_counts.get(goal.id, 0)

            if linked_tasks > 0:
                continue

            # Цель должна быть хотя бы 2 дня старой (дать время создать задачи)
            if goal.created_at:
                ct = goal.created_at
                if ct.tzinfo is None:
                    ct = ct.replace(tzinfo=timezone.utc)
                age_days = (now_utc - ct).days
                if age_days < 2:
                    continue

            anchors.append(Anchor(
                user_id=user.id,
                anchor_type='goal_decomposition',
                source=f'goal:{goal.id}:no_tasks',
                topic=_t(user, f'Цель «{goal.title}» — нет активных задач, нужна декомпозиция', f'Goal «{goal.title}» — no active tasks, needs breakdown'),
                priority=AnchorPriority.LOW,
                data=json.dumps({
                    'goal_id': goal.id,
                    'title': goal.title,
                    'description': (goal.description or '')[:200],
                    'progress': goal.progress_percentage,
                    'category': goal.category,
                    'target_date': goal.target_date.isoformat() if goal.target_date else None,
                }),
                triggered_at=now_utc,
                expires_at=now_utc + timedelta(days=7),
                cooldown_hours=168,
                batch_group='goals',
            ))

        return anchors

    def _scan_goal_autopilot(self, user, profile, session, now_utc) -> list:
        """Автопилот целей: AI анализирует цели с ПОЛНЫМ контекстом и действует."""
        if not profile or not getattr(profile, 'goal_autopilot_enabled', False):
            return []

        active_goals = session.query(Goal).filter(
            Goal.user_id == user.id,
            Goal.status == 'active',
        ).order_by(Goal.updated_at.asc().nullsfirst()).all()
        if not active_goals:
            return []

        # ── ЖЁСТКИЙ GUARD: не создавать якорь если последний autopilot-якорь доставлен меньше MIN_AUTOPILOT_GAP_MINUTES назад ──
        # Проверяем Anchor.delivered_at — самый надёжный источник (всегда коммитится перед AI-вызовом)
        try:
            _last_ap_anchor = session.query(Anchor.delivered_at).filter(
                Anchor.user_id == user.id,
                Anchor.anchor_type == 'goal_autopilot_review',
                Anchor.delivered_at.isnot(None),
            ).order_by(Anchor.delivered_at.desc()).first()
            if _last_ap_anchor:
                _ap_time = _last_ap_anchor[0]
                if _ap_time.tzinfo is None:
                    _ap_time = _ap_time.replace(tzinfo=timezone.utc)
                _gap = (now_utc - _ap_time).total_seconds() / 60
                if _gap < MIN_AUTOPILOT_GAP_MINUTES:
                    return []
        except Exception as _e:
            logger.debug("suppressed: %s", _e)

        # Собираем задачи к целям — не только count, а полный список
        goal_ids = [g.id for g in active_goals]
        all_tasks = session.query(Task).filter(
            Task.goal_id.in_(goal_ids),
            Task.status.notin_(['cancelled', 'deleted']),
        ).order_by(Task.created_at.desc()).all() if goal_ids else []
        _tasks_by_goal: dict = {}
        for t in all_tasks:
            _tasks_by_goal.setdefault(t.goal_id, []).append(t)

        # Последние действия автопилота (AgentActivityLog) — что уже было сделано
        from models import AgentActivityLog as _AAL_scan
        recent_actions = session.query(_AAL_scan).filter(
            _AAL_scan.user_id == user.id,
            _AAL_scan.activity_type.in_(['goal_autopilot_dispatch', 'agent_chain_continue',
                                          'run_agent_action']),
            _AAL_scan.created_at >= now_utc - timedelta(hours=48),
        ).order_by(_AAL_scan.created_at.desc()).limit(20).all()

        actions_history = []
        # Считаем частоту инструментов для блэклиста
        _tool_freq: dict = {}
        _failed_tools: dict = {}
        for a in recent_actions:
            # Извлекаем инструменты из result (формат: "[tools: web_search, find_and_message_relevant_users] text")
            _tools_tag = ''
            _res = a.result or ''
            # run_agent_action логи: содержат реальный вывод скрипта (котировки, данные)
            # Форматируем их явно чтобы агент видел реальные данные из предыдущего цикла
            if a.activity_type == 'run_agent_action':
                _action_name = (a.title or '').replace(' — обзор целей', '')
                actions_history.append(
                    f"[{a.created_at.strftime('%H:%M')}] [run_agent_action] {_action_name}: {_res[:400]}"
                )
                # Считаем partial failure для run_agent_action
                if _res and 'error' in _res.lower():
                    _failed_tools['run_agent_action'] = _failed_tools.get('run_agent_action', 0) + 1
                else:
                    _tool_freq['run_agent_action'] = _tool_freq.get('run_agent_action', 0) + 1
                continue
            if _res.startswith('[tools:'):
                _idx = _res.find(']')
                if _idx > 0:
                    _tools_tag = _res[7:_idx].strip()
                    _res = _res[_idx+1:].strip()
                    for _tn in _tools_tag.split(', '):
                        _tn = _tn.strip()
                        if _tn:
                            _tool_freq[_tn] = _tool_freq.get(_tn, 0) + 1
                            # Провал = пустой/короткий результат ИЛИ явная ошибка
                            # НЕ считать провалом: "уже отправлено" (дедупликация), "cooldown", "лимит"
                            # — это нормальная работа защиты от спама, а не сбой инструмента
                            _res_lower = _res.lower()
                            _is_skip_response = any(w in _res_lower for w in (
                                'уже отправлен', 'уже получал', 'cooldown',
                                'дневной лимит', 'достигнут лимит', 'кросс-кампания',
                                'заблокирован', 'bounced', 'не писать повторно',
                            ))
                            _is_real_fail = (
                                not _is_skip_response
                                and (
                                    len(_res) < 30
                                    or 'не наш' in _res_lower
                                    or 'нет подходящ' in _res_lower
                                    or 'error' in _res_lower
                                )
                            )
                            if _is_real_fail:
                                _failed_tools[_tn] = _failed_tools.get(_tn, 0) + 1
            _agent = (a.title or '').replace(' — обзор целей', '')
            _tools_info = f" [инструменты: {_tools_tag}]" if _tools_tag else ''
            actions_history.append(
                f"[{a.created_at.strftime('%H:%M')}] {_agent}{_tools_info}: {_res[:600]}"
            )

        # Fallback: если AAL пуст, берём историю из interactions (proactive сообщения за 24ч)
        if not actions_history:
            _ap_fallback = session.query(Interaction).filter(
                Interaction.user_id == user.id,
                Interaction.message_type == 'proactive',
                Interaction.created_at >= now_utc - timedelta(hours=24),
            ).order_by(Interaction.created_at.desc()).limit(10).all()
            for _fb in _ap_fallback:
                try:
                    _j = json.loads(_fb.content or '{}')
                    _ag = _j.get('__agent', {}).get('name', '?')
                    _txt = (_j.get('text', '') or '')[:400]
                    _tl = _j.get('__tools_used', [])
                    _tl_str = f" [инструменты: {', '.join(_tl)}]" if _tl else ''
                    actions_history.append(
                        f"[{_fb.created_at.strftime('%H:%M')}] {_ag}{_tl_str}: {_txt}"
                    )
                    # Учитываем частоту для anti-loop
                    for _tn in _tl:
                        _tool_freq[_tn] = _tool_freq.get(_tn, 0) + 1
                        if 'не наш' in _txt.lower() or 'не нашла' in _txt.lower():
                            _failed_tools[_tn] = _failed_tools.get(_tn, 0) + 1
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)

        # Последние proactive/agent_msg сообщения за 12 часов — что реально было сказано
        # (расширенное окно чтобы агенты видели всю историю, включая ранние попытки)
        _msg_window_hours = 12 if not actions_history else 2
        recent_msgs = session.query(Interaction).filter(
            Interaction.user_id == user.id,
            Interaction.message_type.in_(['proactive', 'agent_msg']),
            Interaction.created_at >= now_utc - timedelta(hours=_msg_window_hours),
        ).order_by(Interaction.created_at.desc()).limit(8).all()
        recent_messages = []
        for m in recent_msgs:
            _txt = (m.content or '')[:400]
            # Пропускаем JSON-контент агентов (avatar data)
            if _txt.startswith('{"__agent"'):
                try:
                    _j = json.loads(m.content or '{}')
                    _txt = (_j.get('text', '') or '')[:400]
                except Exception:
                    _txt = _txt[:200]
            recent_messages.append(
                f"[{m.created_at.strftime('%H:%M')}] {m.message_type}: {_txt}"
            )

        # Email outreach статус по активным кампаниям
        email_campaigns = session.query(EmailCampaign).filter(
            EmailCampaign.user_id == user.id,
            EmailCampaign.status == 'active',
        ).all()
        email_summary = []
        for c in email_campaigns:
            outreach = session.query(EmailOutreach).filter_by(campaign_id=c.id).all()
            sent = sum(1 for o in outreach if o.status in ('sent', 'delivered', 'opened'))
            replied = sum(1 for o in outreach if o.status == 'replied')
            drafts = sum(1 for o in outreach if o.status == 'draft')
            email_summary.append(
                f"Кампания «{c.name}»: отправлено={sent}, ответов={replied}, черновиков={drafts}"
            )

        # Email контакты пользователя — кому уже писали (replied/interested первыми)
        from models import EmailContact as _EC_scan
        _contacts_raw = session.query(_EC_scan).filter_by(
            user_id=user.id,
        ).order_by(_EC_scan.created_at.desc()).limit(30).all()
        _status_prio = {'replied': 0, 'interested': 1, 'contacted': 2, 'new': 3}
        contacts = sorted(_contacts_raw, key=lambda c: _status_prio.get(c.status or 'new', 4))[:20]
        contacts_summary = [
            f"{c.name or '?'} <{c.email}> [статус: {c.status or 'new'}] (src={c.source})"
            for c in contacts
        ] if contacts else []

        # Per-agent action memory — чтобы каждый агент не зацикливался и не повторял своё
        _per_agent_history: dict = {}  # {agent_name: [action_str, ...]}
        try:
            _agent_interactions = session.query(Interaction).filter(
                Interaction.user_id == user.id,
                Interaction.message_type.in_(['proactive', 'agent_msg']),
                Interaction.created_at >= now_utc - timedelta(hours=48),
            ).order_by(Interaction.created_at.desc()).limit(80).all()
            for _ai_item in _agent_interactions:
                try:
                    _j = json.loads(_ai_item.content or '{}')
                    _ag_nm = _j.get('__agent', {}).get('name', '')
                    if not _ag_nm:
                        continue
                    _txt = (_j.get('text', '') or '')[:250]
                    _tl = _j.get('__tools_used', [])
                    _tl_s = f"[{', '.join(_tl)}] " if _tl else ''
                    _entry = f"{_ai_item.created_at.strftime('%d.%m %H:%M')} {_tl_s}{_txt[:200]}"
                    _per_agent_history.setdefault(_ag_nm, [])
                    if len(_per_agent_history[_ag_nm]) < 12:
                        _per_agent_history[_ag_nm].append(_entry)
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)
        except Exception as _pah_err:
            logger.debug("[AUTOPILOT] per_agent_history: %s", _pah_err)

        # ── Дополняем per_agent_history из AAL (fallback: agent_msg мог быть удалён) ──
        try:
            from models import AgentActivityLog as _AAL_pah
            _aal_pah_items = session.query(_AAL_pah).filter(
                _AAL_pah.user_id == user.id,
                _AAL_pah.activity_type == 'agent_task',
                _AAL_pah.status == 'completed',
                _AAL_pah.created_at >= now_utc - timedelta(hours=48),
            ).order_by(_AAL_pah.created_at.desc()).limit(50).all()
            for _api in _aal_pah_items:
                _ag_nm_aal = (_api.target or '').replace('agent:', '').strip()
                if not _ag_nm_aal:
                    continue
                _aal_title = (_api.title or '')[:100]
                _aal_content = (_api.content or '')[:120]
                _aal_ts = _api.created_at.strftime('%d.%m %H:%M')
                # Угадываем инструмент по содержимому — для совместимости с anti-loop парсером
                _tl_lower = (_aal_title + ' ' + _aal_content).lower()
                if any(w in _tl_lower for w in ('почт', 'imap', 'email', 'ответ')):
                    _guessed = '[check_emails]'
                elif any(w in _tl_lower for w in ('rss', 'лента', 'github', 'репозитор')):
                    _guessed = '[run_agent_action]'
                elif any(w in _tl_lower for w in ('отправил', 'написал', 'outreach')):
                    _guessed = '[send_outreach_email]'
                elif any(w in _tl_lower for w in ('поиск', 'нашёл', 'найден', 'search')):
                    _guessed = '[web_search]'
                elif any(w in _tl_lower for w in ('telegram', 'discord', 'канал', 'сообщест')):
                    _guessed = '[web_search]'
                else:
                    _guessed = '[research_topic]'
                _entry_aal = f"{_aal_ts} {_guessed} {_aal_title}: {_aal_content}"
                _per_agent_history.setdefault(_ag_nm_aal, [])
                if len(_per_agent_history[_ag_nm_aal]) < 12:
                    _per_agent_history[_ag_nm_aal].append(_entry_aal)
        except Exception as _aal_pah_err:
            logger.debug("[AUTOPILOT] per_agent_history from AAL: %s", _aal_pah_err)

        # Уже отправленные письма — не писать повторно одним и тем же адресатам
        # Запрашиваем по user_id напрямую (не только по активным кампаниям)
        _already_sent_emails: list = []
        try:
            _sent_outreach = session.query(EmailOutreach).filter(
                EmailOutreach.user_id == user.id,
                EmailOutreach.status.in_(['sent', 'delivered', 'opened', 'replied']),
            ).order_by(EmailOutreach.sent_at.desc()).limit(200).all()
            _already_sent_emails = list({o.recipient_email for o in _sent_outreach if o.recipient_email})
        except Exception as _ase_err:
            logger.debug("[AUTOPILOT] already_sent_emails: %s", _ase_err)

        # Replied контакты без AI-ответа — наивысший приоритет!
        _pending_replies: list = []
        try:
            _pr_rows = session.query(EmailOutreach).filter(
                EmailOutreach.user_id == user.id,
                EmailOutreach.status == 'replied',
                EmailOutreach.ai_reply_sent_at.is_(None),
            ).order_by(EmailOutreach.reply_at.desc().nullslast()).limit(10).all()
            # Дедупликация по email: если на этот адрес уже отвечали через другую запись — пропуск
            # Спам-лимит: не более 2 AI-ответов на контакт
            _MAX_AI_REPLIES_COORD = 2
            try:
                from models import EmailOutreach as _EO_dedup
                from sqlalchemy import func as _func_dedup
                _reply_counts_rows = session.query(
                    _EO_dedup.recipient_email,
                    _func_dedup.count(_EO_dedup.id).label('cnt'),
                ).filter(
                    _EO_dedup.user_id == user.id,
                    _EO_dedup.ai_reply_sent_at.isnot(None),
                ).group_by(_EO_dedup.recipient_email).all()
                _already_ai_replied_emails = {
                    (r.recipient_email or '').lower()
                    for r in _reply_counts_rows
                    if (r.cnt or 0) >= _MAX_AI_REPLIES_COORD
                }
            except Exception:
                _already_ai_replied_emails = set()
            import re as _re_pr_clean
            def _clean_reply_text(txt: str) -> str:
                """Убирает MIME boundary/header артефакты и HTML-теги из текста письма."""
                if not txt:
                    return ''
                # Strip HTML tags to prevent XSS and prompt injection from malicious replies
                txt = _re_pr_clean.sub(r'<[^>]+>', '', txt)
                txt = _re_pr_clean.sub(r'--[A-Za-z0-9_\-]{6,}[^\n]*\n?', '', txt)
                txt = _re_pr_clean.sub(r'Content-[A-Za-z\-]+:[^\n]*\n?', '', txt)
                return txt.strip()
            _seen_pr_emails: set = set()
            for _pr in _pr_rows:
                _pr_email_lower = (_pr.recipient_email or '').lower()
                if _pr_email_lower in _already_ai_replied_emails:
                    continue  # уже отвечали через другую outreach-запись
                if _pr_email_lower in _seen_pr_emails:
                    continue  # дубль в текущей выборке
                _seen_pr_emails.add(_pr_email_lower)
                _pending_replies.append({
                    'outreach_id': _pr.id,
                    'email': _pr.recipient_email,
                    'name': _pr.recipient_name or '',
                    'reply_text': _clean_reply_text((_pr.reply_text or '')[:4000])[:1500],
                    'subject': _pr.subject or '',
                })
            if _pending_replies:
                logger.info("[AUTOPILOT] pending_replies (need AI response): %d", len(_pending_replies))
        except Exception as _pr_err:
            logger.debug("[AUTOPILOT] pending_replies: %s", _pr_err)

        # Правила пользователя — из user.memory['rules'] (сохраняются AI через save_user_rule)
        user_rules = []
        try:
            from ai_integration.memory import decrypt_data as _dec_rules
            _mem_raw = _dec_rules(user.memory) if user.memory else '{}'
            _mem_dict = json.loads(_mem_raw) if _mem_raw else {}
            user_rules = _mem_dict.get('rules', [])
        except Exception as _e_rules:
            logger.debug(f"[AUTOPILOT] Failed to load user rules: {_e_rules}")

        # Умная выборка целей: сначала заброшенные (already sorted by updated_at ASC),
        # но ВСЕГДА включаем цели с прогрессом >=70% (почти завершены → нужно финализировать)
        _near_done = [g for g in active_goals if (g.progress_percentage or 0) >= 70]
        _other = [g for g in active_goals if (g.progress_percentage or 0) < 70]
        _goals_pool = _near_done + _other  # почти-завершённые всегда первыми
        goals_summary = []
        for g in _goals_pool[:5]:
            # Задачи этой цели — полный status breakdown
            goal_tasks = _tasks_by_goal.get(g.id, [])
            tasks_detail = []
            for t in goal_tasks[:10]:  # Макс 10 задач на цель
                tasks_detail.append({
                    'title': t.title[:100],
                    'status': t.status,
                    'result': (t.completion_notes or '')[:80] if t.status == 'done' else None,
                })

            goals_summary.append({
                'id': g.id,
                'title': g.title,
                'description': (g.description or '')[:150],
                'category': g.category or '',
                'progress': g.progress_percentage,
                'metric_target': g.metric_target,
                'metric_current': g.metric_current,
                'target_date': g.target_date.isoformat() if g.target_date else None,
                'tasks': tasks_detail,
            })

        # Задачи созданные агентами за 24ч — для предотвращения дублей и зацикливания
        _recent_agent_tasks = session.query(Task).filter(
            Task.user_id == user.id,
            Task.created_at >= now_utc - timedelta(hours=24),
            Task.source == 'agent',
        ).order_by(Task.created_at.desc()).limit(10).all()
        agent_tasks_history = [f"{t.title[:80]} [{t.status}]" for t in _recent_agent_tasks]

        # Всего отправлено email/outreach — авторитетный источник: таблица EmailOutreach
        _total_emails_sent = session.query(EmailOutreach).filter(
            EmailOutreach.user_id == user.id,
            EmailOutreach.status.in_(['sent', 'delivered', 'opened', 'replied']),
        ).count()

        # ── Определяем исчерпанные стратегии (все инструменты категории провалились) ──
        _STRATEGY_TOOLS = {
            'search': {'web_search', 'research_topic', 'quick_topic_search', 'find_relevant_contacts_for_task', 'find_and_message_relevant_users'},
            'email': {'send_outreach_email', 'send_email', 'start_email_campaign', 'add_email_leads', 'negotiate_by_email'},
            'content': {'create_post', 'publish_to_telegram', 'publish_to_discord', 'generate_image'},
            'delegation': {'delegate_task', 'start_delegation_campaign'},
        }
        exhausted_strategies = []
        for strat_name, strat_tools in _STRATEGY_TOOLS.items():
            used_tools = strat_tools & set(_tool_freq.keys())
            failed_in_strat = strat_tools & set(_failed_tools.keys())
            # Стратегия исчерпана если: использовалась 3+ раз И >50% провалов
            total_uses = sum(_tool_freq.get(t, 0) for t in used_tools)
            total_fails = sum(_failed_tools.get(t, 0) for t in failed_in_strat)
            if total_uses >= 3 and total_fails > total_uses * 0.5:
                exhausted_strategies.append(strat_name)

        # Агенты для делегирования — с описанием способностей
        from models import UserAgent as _UA_team
        _team_agents_raw = session.query(_UA_team).filter(
            _UA_team.author_id == user.id,
            _UA_team.status.in_(['active', 'paused']),
        ).all()
        _team_profiles = []
        for _ta in _team_agents_raw:
            try:
                from ai_integration.autonomous_agent import _parse_agent_integrations as _pai_team
                _ta_caps = _pai_team(
                    _ta.user_api_keys or '', _ta.python_code or '',
                    _ta.tools_allowed or '', getattr(_ta, 'search_scope', '') or '',
                )
            except Exception:
                _ta_caps = []
            # Фоллбэк по роли: если нет API-ключей и кода — определяем способности по должности/специализации
            if not _ta_caps:
                try:
                    from ai_integration.autonomous_agent import _infer_capabilities_from_role as _icfr
                    _ta_caps = _icfr(
                        _ta.job_title or '',
                        _ta.specialization or '',
                        _ta.description or '',
                    )[:6]
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)
            _team_profiles.append({
                'name': _ta.name,
                'job_title': _ta.job_title or '',
                'capabilities': _ta_caps[:6],
            })

        # ── Оценка достижимости целей текущими инструментами ──
        _feasibility_warnings = []
        _team_caps_all = set()
        for tp in _team_profiles:
            for c in tp.get('capabilities', []):
                _team_caps_all.add(c.lower())
        _has_email_cap = any(w in c for c in _team_caps_all for w in ('mail', 'почт', 'email', 'imap', 'smtp'))
        _has_content_cap = any(w in c for c in _team_caps_all for w in ('telegram', 'discord', 'контент'))
        _has_github_cap = any('github' in c for c in _team_caps_all)

        for g in goals_summary:
            _gt = g.get('title', '').lower()
            _mt = g.get('metric_target')
            _mc = g.get('metric_current', 0)
            _gap = (_mt or 0) - (_mc or 0)

            # Цель требует привлечения людей но нет email
            if any(w in _gt for w in ('пользовател', 'тестировщик', 'клиент', 'подписчик', 'лид', 'участник')) and _gap > 5:
                if not _has_email_cap:
                    _feasibility_warnings.append(
                        f"⚠️ Цель '{g['title']}' требует привлечения людей, но НИ ОДИН агент не имеет Email-интеграции. "
                        f"Добавь агенту SMTP/IMAP ключи для отправки outreach-писем."
                    )
                if not _has_content_cap and _gap > 20:
                    _feasibility_warnings.append(
                        f"💡 Для '{g['title']}' ({_gap} осталось) полезен Telegram-канал или Discord для массового охвата."
                    )

            # Стагнация: >12ч без реального прогресса → уведомляем пользователя в Telegram
            if actions_history and _mt:
                _action_count = len(actions_history)
                if _action_count >= 6 and _mc <= 1:
                    _stagnation_warn = (
                        f"🔴 СТАГНАЦИЯ: {_action_count} dispatch'ей за 48ч, но реальный прогресс = {int(_mc)}/{int(_mt)}. "
                        f"Текущая стратегия не работает. Сообщи пользователю что нужно сменить подход или добавить интеграции."
                    )
                    _feasibility_warnings.append(_stagnation_warn)
                    # Отправляем Telegram-уведомление пользователю если прошло >24ч с последнего стагнации-алёрта
                    try:
                        _last_stag_warn = session.query(Interaction).filter(
                            Interaction.user_id == user.id,
                            Interaction.message_type == 'stagnation_alert',
                        ).order_by(Interaction.created_at.desc()).first()
                        _stag_cutoff = now_utc - timedelta(hours=24)
                        _should_notify = (
                            not _last_stag_warn
                            or (_last_stag_warn.created_at.replace(tzinfo=timezone.utc)
                                if _last_stag_warn.created_at.tzinfo is None
                                else _last_stag_warn.created_at) < _stag_cutoff
                        )
                        if _should_notify and self.bot and user.telegram_id:
                            _stag_goal_title = g.get('title', '')[:60]
                            _miss_intg = []
                            if not _has_email_cap:
                                _miss_intg.append("Email (SMTP/IMAP/Gmail) — для отправки outreach-писем")
                            if not _has_github_cap and any(w in _stag_goal_title.lower() for w in ('разработ', 'тестировщик', 'github')):
                                _miss_intg.append("GitHub Token — для поиска разработчиков")
                            _miss_str = '\n'.join(f"  • {m}" for m in _miss_intg) if _miss_intg else ''
                            _stag_msg = (
                                f"⚠️ Автопилот застрял на цели «{_stag_goal_title}»\n\n"
                                f"Запусков: {_action_count} за 48ч, но прогресс = {int(_mc)}/{int(_mt)}.\n"
                                + (f"Нужны интеграции:\n{_miss_str}\n\n" if _miss_str else
                                   "Попробуй скорректировать цель или взять паузу в автопилоте.\n\n")
                                + "Настройки → Агенты → API-ключи для подключения."
                            )
                            try:
                                import asyncio as _asyncio_stag
                                _asyncio_stag.ensure_future(
                                    self.bot.send_message(chat_id=user.telegram_id, text=_stag_msg)
                                )
                            except Exception as _e:
                                logger.debug("suppressed: %s", _e)
                            # Записываем факт отправки алерта
                            try:
                                session.add(Interaction(
                                    user_id=user.id,
                                    message_type='stagnation_alert',
                                    content=_stag_goal_title,
                                ))
                                session.commit()
                            except Exception:
                                try:
                                    session.rollback()
                                except Exception:
                                    pass
                    except Exception as _stag_tg_err:
                        logger.debug("[AUTOPILOT] stagnation alert: %s", _stag_tg_err)

        # ── Профиль пользователя: агенты должны знать кому служат ──
        _user_profile_ctx = {}
        try:
            from models import UserProfile as _UP_scan
            _up = session.query(_UP_scan).filter_by(user_id=user.id).first()
            if _up:
                _up_parts = []
                if _up.company:
                    _up_parts.append(f"Компания/проект: {_up.company}")
                if _up.position:
                    _up_parts.append(f"Должность: {_up.position}")
                if _up.bio:
                    _up_parts.append(f"О пользователе: {_up.bio[:200]}")
                if _up.goals:
                    _up_parts.append(f"Личные цели: {str(_up.goals)[:150]}")
                if _up.content_strategy:
                    _up_parts.append(f"Контент-стратегия: {str(_up.content_strategy)[:150]}")
                _user_profile_ctx = {
                    'company': _up.company or '',
                    'position': _up.position or '',
                    'bio': (_up.bio or '')[:200],
                    'summary': '\n'.join(_up_parts),
                }
        except Exception as _up_err:
            logger.debug("[AUTOPILOT] user profile load failed: %s", _up_err)

        # ── Авто-обновление прогресса цели из EmailContact (только подтверждённые) ──
        # ВАЖНО: для людей-целей метрика = ТОЛЬКО replied/interested (реальные участники).
        # email-контакты в базе и отправленные письма ≠ тестировщики/пользователи!
        # Авто-апдейт никогда не ставит 100% — финальное закрытие только через AI или вручную.
        try:
            from models import EmailContact as _EC_au
            _PPL_KW_AU = ('пользовател', 'тестировщик', 'клиент', 'подписчик', 'лид', 'участник')
            _PPL_UNIT_AU = ('пользователь', 'пользователей', 'тестировщик', 'тестировщиков',
                            'человек', 'участник', 'участников', 'подписчик', 'подписчиков')
            for _g_au in active_goals:
                _gt_au = (_g_au.title or '').lower()
                _gunit_au = (_g_au.metric_unit or '').lower()
                _is_ppl_goal_au = (
                    any(w in _gt_au for w in _PPL_KW_AU)
                    or any(u in _gunit_au for u in _PPL_UNIT_AU)
                )
                if _is_ppl_goal_au and (_g_au.metric_target or 0) > 0:
                    # Только подтверждённые участники (replied/interested)
                    _replied_count = session.query(_EC_au).filter(
                        _EC_au.user_id == user.id,
                        _EC_au.status.in_(['replied', 'interested']),
                    ).count()
                    _cur_mc = _g_au.metric_current or 0
                    _mt_au = _g_au.metric_target or 0
                    # Корректируем ТОЛЬКО ВВЕРХ: если реальных ответов больше метрики.
                    # Исключение: metric_current > metric_target (невозможное значение — сбрасываем к 95%).
                    # НЕ корректируем вниз: снижение прогресса расстраивает пользователя
                    # и ненадёжно (агент мог считать другие типы контактов).
                    _needs_upward_correction = _replied_count > _cur_mc
                    _needs_cap_correction = _mt_au > 0 and _cur_mc > _mt_au
                    if _needs_upward_correction or _needs_cap_correction:
                        _new_mc = max(float(_replied_count), _cur_mc) if not _needs_cap_correction else float(_replied_count)
                        _safe_pct = min(95, int(_new_mc * 100 / _mt_au)) if _mt_au else 0
                        _g_au.metric_current = _new_mc
                        _g_au.progress_percentage = _safe_pct
                        try:
                            session.commit()
                            logger.info(
                                f"[AUTOPILOT] Corrected goal #{_g_au.id}: "
                                f"metric {_cur_mc}→{_new_mc}/{int(_mt_au)} ({_safe_pct}%)"
                            )
                        except Exception:
                            session.rollback()
        except Exception as _au_err:
            logger.debug("[AUTOPILOT] auto-update goal metric: %s", _au_err)

        # ── Синхронизируем goals_summary с обновлёнными ORM-объектами ──
        # goals_summary был построен ДО авто-обновления метрик → патчим свежими данными
        for _gs_item in goals_summary:
            for _g_sync in active_goals:
                if _g_sync.id == _gs_item.get('id'):
                    _gs_item['progress'] = _g_sync.progress_percentage
                    _gs_item['metric_current'] = _g_sync.metric_current
                    break

        # Трекинг: какие цели доминируют в recent_actions (для ротации)
        _goal_freq_in_history: dict = {}
        for _gs_item in goals_summary:
            _gt_fq = (_gs_item.get('title') or '').lower()[:30]
            _hits = sum(1 for _ah in actions_history if _gt_fq and _gt_fq[:15] in _ah.lower())
            if _hits:
                _goal_freq_in_history[_gs_item.get('title', '')] = _hits
        _overworked_goals = [
            f"«{t}» ({n}x)" for t, n in _goal_freq_in_history.items() if n >= 3
        ]
        _neglected_goals = [
            g.get('title', '') for g in goals_summary
            if not _goal_freq_in_history.get(g.get('title', ''))
        ]

        # ── Извлекаем темы из последних проактивных сообщений (deduplication) ──
        recent_proactive_topics = []
        try:
            _recent_proact = session.query(Interaction).filter(
                Interaction.user_id == user.id,
                Interaction.message_type == 'proactive',
                Interaction.created_at >= now_utc - timedelta(hours=48),
            ).order_by(Interaction.created_at.desc()).limit(10).all()
            
            for _rp in _recent_proact:
                try:
                    _cnt = json.loads(_rp.content) if isinstance(_rp.content, str) else _rp.content
                    _txt = _cnt.get('text', '') if isinstance(_cnt, dict) else ''
                    if _txt:
                        # Извлекаем ключевые темы из текста (первые 2 предложения)
                        _sentences = _txt.split('.')[:2]
                        _topic = '.'.join(_sentences).strip()[:150]
                        if _topic and len(_topic) > 20:
                            recent_proactive_topics.append(_topic)
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)
        except Exception as _rpt_err:
            logger.debug(f"[AUTOPILOT] recent_proactive_topics extraction: {_rpt_err}")

        # Формируем полный контекст
        context_data = {
            'goals': goals_summary,
            'team_agents': [tp['name'] for tp in _team_profiles],
            'team_profiles': _team_profiles,
            'recent_actions': actions_history[:10],
            'recent_messages': recent_messages[:6],
            'recent_proactive_topics': recent_proactive_topics[:8],  # Последние 8 тем для deduplication
            'email_campaigns': email_summary,
            'known_contacts': contacts_summary[:10],
            'user_rules': user_rules[:10],
            'agent_tasks_history': agent_tasks_history,
            'total_emails_sent': _total_emails_sent,
            'failed_tools': {k: v for k, v in _failed_tools.items() if v >= 2},
            'tool_frequency': _tool_freq,
            'exhausted_strategies': exhausted_strategies,
            'feasibility_warnings': _feasibility_warnings,
            'user_profile': _user_profile_ctx,
            'per_agent_history': _per_agent_history,
            'already_sent_emails': _already_sent_emails,
            'pending_replies': _pending_replies,
            'overworked_goals': _overworked_goals,
            'neglected_goals': _neglected_goals,
            'unsent_contacts': [
                c for c in contacts_summary[:10]
                if '<' in c and '>' in c and
                c.split('<')[1].split('>')[0].strip().lower() not in
                {e.lower() for e in _already_sent_emails}
            ],
        }

        return [Anchor(
            user_id=user.id,
            anchor_type='goal_autopilot_review',
            source=f'autopilot:{user.id}:goals',
            topic=_t(user,
                      f'Проверка {len(goals_summary)} целей — следующий шаг',
                      f'Review {len(goals_summary)} goals — next step'),
            priority=AnchorPriority.MEDIUM,
            data=json.dumps(context_data, ensure_ascii=False),
            triggered_at=now_utc,
            expires_at=now_utc + timedelta(hours=4),
            cooldown_hours=0.25,
            batch_group='goals',
        )]

    def _scan_inactivity_reengagement(self, user, session, now_utc) -> list:
        """Пользователь не взаимодействовал 3+ дня → мягкое возвращение."""
        anchors = []

        # Последнее взаимодействие
        last_interaction = session.query(Interaction).filter(
            Interaction.user_id == user.id,
        ).order_by(Interaction.created_at.desc()).first()

        if not last_interaction or not last_interaction.created_at:
            return anchors

        li = last_interaction.created_at
        if li.tzinfo is None:
            li = li.replace(tzinfo=timezone.utc)
        days_inactive = (now_utc - li).days

        if days_inactive < 3:
            return anchors

        # Собираем число незакрытых задач для контекста
        pending_tasks = session.query(Task).filter(
            Task.user_id == user.id,
            Task.status.in_(['pending', 'in_progress']),
        ).count()

        active_goals = session.query(Goal).filter(
            Goal.user_id == user.id,
            Goal.status == 'active',
        ).count()

        anchors.append(Anchor(
            user_id=user.id,
            anchor_type='inactivity_reengagement',
            source=f'inactivity:{days_inactive}d:{now_utc.strftime("%Y-%m-%d")}',
            topic=_t(user, f'Не заходил {days_inactive} дней — {pending_tasks} задач и {active_goals} целей ждут', f'Inactive for {days_inactive} days — {pending_tasks} tasks and {active_goals} goals waiting'),
            priority=AnchorPriority.MEDIUM,
            data=json.dumps({
                'days_inactive': days_inactive,
                'pending_tasks': pending_tasks,
                'active_goals': active_goals,
                'last_seen': li.isoformat(),
            }),
            triggered_at=now_utc,
            expires_at=now_utc + timedelta(days=3),
            cooldown_hours=48,
            batch_group='engagement',
        ))

        return anchors

    # ═══════════════════════════════════════════════════════
    # CONTENT CAMPAIGN SCANNER — автономная публикация контента
    # ═══════════════════════════════════════════════════════

    def _scan_content_campaigns(self, user, session, now_utc) -> list:
        """Сканирует контент-кампании: создаёт якорь content_campaign_publish когда пора постить.

        Проверяет:
        1. Активные кампании с status='active'
        2. Частоту (daily / every_2_days / every_3_days / weekly)
        3. Предпочтительное время (post_time)
        4. Дневной лимит (daily_limit)
        5. Общий лимит (max_posts)
        """
        anchors = []

        campaigns = session.query(ContentCampaign).filter(
            ContentCampaign.user_id == user.id,
            ContentCampaign.status == 'active'
        ).all()

        if not campaigns:
            return anchors

        import pytz as _pytz_cc
        user_tz = _pytz_cc.timezone(user.timezone or 'Europe/Moscow')
        user_now = now_utc.astimezone(user_tz)

        # today_start is the same for all campaigns (same user)
        _cc_today_start = user_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)

        # Batch-load posts_today count per campaign (avoid N+1 AgentActivityLog count per campaign)
        from sqlalchemy import func as _func_cc
        _cc_camp_ids = [c.id for c in campaigns]
        _cc_posts_today_raw = session.query(AgentActivityLog.result, _func_cc.count(AgentActivityLog.id)).filter(
            AgentActivityLog.user_id == user.id,
            AgentActivityLog.activity_type.in_(['post_newsfeed', 'post_telegram', 'post_discord']),
            AgentActivityLog.created_at >= _cc_today_start,
            AgentActivityLog.result.in_([f'campaign:{cid}' for cid in _cc_camp_ids]),
        ).group_by(AgentActivityLog.result).all() if _cc_camp_ids else []
        _cc_posts_today_map = {int(r.split(':')[1]): cnt for r, cnt in _cc_posts_today_raw if r and ':' in r}

        for campaign in campaigns:
            # --- Общий лимит ---
            if campaign.max_posts and campaign.max_posts > 0:
                if (campaign.posts_published or 0) >= campaign.max_posts:
                    campaign.status = 'completed'
                    try:
                        session.commit()
                        logger.info(f"[ANCHOR] Auto-completed content campaign #{campaign.id} «{campaign.name}» — reached max_posts")
                    except Exception:
                        session.rollback()
                    continue

            # --- Частота: проверяем last_post_at ---
            frequency_hours = {
                'daily': 20,          # ~1 раз в 20ч (с запасом)
                'every_2_days': 44,
                'every_3_days': 68,
                'weekly': 164,
            }
            min_gap_hours = frequency_hours.get(campaign.frequency or 'daily', 20)

            if campaign.last_post_at:
                last_post = campaign.last_post_at
                if last_post.tzinfo is None:
                    last_post = last_post.replace(tzinfo=timezone.utc)
                hours_since = (now_utc - last_post).total_seconds() / 3600
                if hours_since < min_gap_hours:
                    logger.debug(f"[ANCHOR] Content campaign #{campaign.id}: skip — {hours_since:.1f}h since last post (need {min_gap_hours})")
                    continue

            # --- Дневной лимит ---
            posts_today = _cc_posts_today_map.get(campaign.id, 0)

            if posts_today >= (campaign.daily_limit or 1):
                logger.debug(f"[ANCHOR] Content campaign #{campaign.id}: skip — {posts_today} posts today (limit {campaign.daily_limit})")
                continue

            # --- Время поста (±90 мин от предпочтительного) ---
            try:
                post_h, post_m = map(int, (campaign.post_time or '12:00').split(':'))
            except (ValueError, AttributeError):
                post_h, post_m = 12, 0

            current_minutes = user_now.hour * 60 + user_now.minute
            target_minutes = post_h * 60 + post_m
            if abs(current_minutes - target_minutes) > 90:
                continue

            # --- Рабочие часы (9:00–22:00) ---
            if user_now.hour < 9 or user_now.hour >= 22:
                continue

            # --- Собираем данные для AI ---
            platforms = ['feed']
            try:
                platforms = json.loads(campaign.platforms or '["feed"]')
            except (json.JSONDecodeError, TypeError):
                platforms = ['feed']

            anchors.append(Anchor(
                user_id=user.id,
                anchor_type='content_campaign_publish',
                source=f'content_campaign:{campaign.id}:publish:{user_now.strftime("%Y-%m-%d")}',
                topic=_t(user,
                    f'Контент-кампания «{campaign.name}» — время для публикации',
                    f'Content campaign «{campaign.name}» — time to publish'),
                priority=AnchorPriority.MEDIUM,
                data=json.dumps({
                    'campaign_id': campaign.id,
                    'campaign_name': campaign.name,
                    'goal': (campaign.goal or '')[:500],
                    'topics': (campaign.topics or '')[:300],
                    'platforms': platforms,
                    'tone': campaign.tone or 'professional',
                    'language': campaign.language or 'ru',
                    'posts_published': campaign.posts_published or 0,
                    'max_posts': campaign.max_posts or 0,
                    'user_name': user.first_name or user.username or 'user',
                }, ensure_ascii=False),
                triggered_at=now_utc,
                expires_at=now_utc + timedelta(hours=6),
                cooldown_hours=min_gap_hours * 0.8,  # cooldown чуть меньше частоты
                batch_group='content',
            ))

        return anchors

    # ═══════════════════════════════════════════════════════
    # DELEGATION CAMPAIGN SCANNER — автономное делегирование задач
    # ═══════════════════════════════════════════════════════

    def _scan_delegation_campaigns(self, user, session, now_utc) -> list:
        """Сканирует кампании делегирования: создаёт якоря delegation_campaign_send.

        Проверяет:
        1. Активные кампании с status='active'
        2. Дневной лимит (daily_limit)
        3. Общий лимит (max_delegations)
        4. Рабочие часы
        5. Наличие подходящих исполнителей
        """
        anchors = []

        campaigns = session.query(DelegationCampaign).filter(
            DelegationCampaign.user_id == user.id,
            DelegationCampaign.status == 'active'
        ).all()

        if not campaigns:
            return anchors

        import pytz as _pytz_dc
        user_tz = _pytz_dc.timezone(user.timezone or 'Europe/Moscow')
        user_now = now_utc.astimezone(user_tz)

        # Рабочие часы (10:00–20:00)
        if user_now.hour < 10 or user_now.hour >= 20:
            return anchors

        # today_start is same for all campaigns (same user timezone)
        _dc_today_start = user_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)

        # Batch-load delegation counts + already-delegated usernames for all campaigns
        _dc_camp_ids = [c.id for c in campaigns]
        from sqlalchemy import func as _func_dc
        _dc_today_counts = dict(session.query(Task.delegation_campaign_id, _func_dc.count(Task.id)).filter(
            Task.delegated_by == user.id,
            Task.delegation_campaign_id.in_(_dc_camp_ids),
            Task.created_at >= _dc_today_start,
        ).group_by(Task.delegation_campaign_id).all()) if _dc_camp_ids else {}
        # All delegated usernames per campaign
        _dc_all_delegated = session.query(Task.delegation_campaign_id, Task.delegated_to_username).filter(
            Task.delegation_campaign_id.in_(_dc_camp_ids),
            Task.delegated_to_username.isnot(None),
        ).all() if _dc_camp_ids else []
        _dc_delegated_by_camp: dict = {}
        for _dc_cid, _dc_uname in _dc_all_delegated:
            _dc_delegated_by_camp.setdefault(_dc_cid, set()).add(_dc_uname.lower())

        for campaign in campaigns:
            # --- Общий лимит ---
            if campaign.max_delegations and campaign.max_delegations > 0:
                if (campaign.delegations_sent or 0) >= campaign.max_delegations:
                    campaign.status = 'completed'
                    try:
                        session.commit()
                        logger.info(f"[ANCHOR] Auto-completed delegation campaign #{campaign.id} «{campaign.name}» — reached max_delegations")
                    except Exception:
                        session.rollback()
                    continue

            # --- Частота: макс 1 делегация в 4ч ---
            if campaign.last_delegation_at:
                last_deleg = campaign.last_delegation_at
                if last_deleg.tzinfo is None:
                    last_deleg = last_deleg.replace(tzinfo=timezone.utc)
                hours_since = (now_utc - last_deleg).total_seconds() / 3600
                if hours_since < 4:
                    continue

            # --- Дневной лимит ---
            delegations_today = _dc_today_counts.get(campaign.id, 0)

            if delegations_today >= (campaign.daily_limit or 3):
                continue

            # --- Ищем потенциальных исполнителей ---
            target_desc = (campaign.target_audience or campaign.goal or '')[:500]
            if not target_desc:
                continue

            # Получаем уже привлечённых (чтобы не повторяться)
            already_usernames = _dc_delegated_by_camp.get(campaign.id, set())

            # Ищем пользователей по interests/skills/bio/goals/city/position
            from sqlalchemy import or_
            keywords = [w.strip().lower() for w in target_desc.replace(',', ' ').replace(';', ' ').split() if len(w.strip()) > 2][:15]

            candidates = []
            if keywords:
                filters = []
                for kw in keywords[:8]:
                    filters.append(UserProfile.interests.ilike(f'%{kw}%'))
                    filters.append(UserProfile.skills.ilike(f'%{kw}%'))
                    filters.append(UserProfile.bio.ilike(f'%{kw}%'))
                    filters.append(UserProfile.goals.ilike(f'%{kw}%'))
                    filters.append(UserProfile.city.ilike(f'%{kw}%'))
                    filters.append(UserProfile.position.ilike(f'%{kw}%'))

                profiles = session.query(UserProfile).join(User).filter(
                    User.id != user.id,
                    or_(*filters),
                ).limit(30).all()

                # Pre-fetch all profile users (batch, avoid N+1)
                if profiles:
                    _prof_uids = [p.user_id for p in profiles]
                    _prof_users = session.query(User).filter(User.id.in_(_prof_uids)).all()
                    _prof_user_by_id = {u.id: u for u in _prof_users}
                else:
                    _prof_user_by_id = {}

                for p in profiles:
                    p_user = _prof_user_by_id.get(p.user_id)
                    if not p_user or not p_user.username:
                        continue
                    if p_user.username.lower() in already_usernames:
                        continue
                    # Скоринг
                    score = 0
                    profile_text = f"{(p.interests or '').lower()} {(p.skills or '').lower()} {(p.bio or '').lower()} {(p.goals or '').lower()} {(p.city or '').lower()} {(p.position or '').lower()}"
                    for kw in keywords:
                        if kw in profile_text:
                            score += 1
                    if score > 0:
                        candidates.append((p_user, score))

                candidates.sort(key=lambda x: -x[1])

            if not candidates:
                # Нет внутренних кандидатов → автоматически переключаемся на ВНЕШНИЙ поиск
                # через email-кампанию (web search + AI + Resend API)
                try:
                    _existing_email_camp = session.query(EmailCampaign).filter(
                        EmailCampaign.user_id == user.id,
                        EmailCampaign.status == 'active',
                        EmailCampaign.name.ilike(f'%{campaign.name[:50]}%'),
                    ).first()
                    if not _existing_email_camp:
                        # Создаём email-кампанию из параметров delegation-кампании
                        _sender_name = user.first_name or user.username or 'Team'
                        _ext_camp = EmailCampaign(
                            user_id=user.id,
                            name=f"{campaign.name} (внешний поиск)",
                            goal=campaign.goal or campaign.name,
                            target_audience=campaign.target_audience or '',
                            offer=campaign.offer or campaign.task_template or campaign.goal or '',
                            tone=campaign.tone or 'professional',
                            sender_name=_sender_name,
                            sender_email='outreach@asibiont.com',
                            max_emails=campaign.max_delegations or 20,
                            daily_limit=min(campaign.daily_limit or 5, 50),
                            status='active',
                        )
                        session.add(_ext_camp)
                        session.flush()

                        # Создаём якорь email_need_leads — _auto_find_leads найдёт контакты
                        anchors.append(Anchor(
                            user_id=user.id,
                            anchor_type='email_need_leads',
                            source=f'delegation_to_email:{campaign.id}:{_ext_camp.id}:{user_now.strftime("%Y-%m-%d")}',
                            topic=_t(user,
                                f'Внешний поиск для кампании «{campaign.name}» — web search + email',
                                f'External search for campaign «{campaign.name}» — web search + email'),
                            priority=AnchorPriority.MEDIUM,
                            data=json.dumps({
                                'campaign_id': _ext_camp.id,
                                'campaign_name': _ext_camp.name,
                                'campaign_goal': (campaign.goal or '')[:500],
                                'target_audience': (campaign.target_audience or '')[:300],
                                'offer': (campaign.offer or campaign.task_template or '')[:500],
                                'delegation_campaign_id': campaign.id,
                            }, ensure_ascii=False),
                            triggered_at=now_utc,
                            expires_at=now_utc + timedelta(hours=24),
                            cooldown_hours=24,
                            batch_group='email',
                        ))
                        session.commit()
                        logger.info(
                            f"[ANCHOR] Delegation campaign #{campaign.id}: no internal candidates → "
                            f"created email campaign #{_ext_camp.id} for external search"
                        )
                    else:
                        logger.debug(
                            f"[ANCHOR] Delegation campaign #{campaign.id}: external email campaign "
                            f"#{_existing_email_camp.id} already exists"
                        )
                except Exception as _ext_e:
                    logger.warning(f"[ANCHOR] Delegation campaign #{campaign.id} external search setup error: {_ext_e}")
                    try:
                        session.rollback()
                    except Exception:
                        pass
                continue

            # Берём лучшего кандидата
            best_candidate, best_score = candidates[0]

            anchors.append(Anchor(
                user_id=user.id,
                anchor_type='delegation_campaign_send',
                source=f'delegation_campaign:{campaign.id}:send:{best_candidate.username}:{user_now.strftime("%Y-%m-%d")}',
                topic=_t(user,
                    f'Кампания делегирования «{campaign.name}» — делегировать @{best_candidate.username}',
                    f'Delegation campaign «{campaign.name}» — delegate to @{best_candidate.username}'),
                priority=AnchorPriority.MEDIUM,
                data=json.dumps({
                    'campaign_id': campaign.id,
                    'campaign_name': campaign.name,
                    'goal': (campaign.goal or '')[:500],
                    'target_audience': (campaign.target_audience or '')[:300],
                    'task_template': (campaign.task_template or '')[:500],
                    'offer': (campaign.offer or '')[:300],
                    'tone': campaign.tone or 'professional',
                    'candidate_username': best_candidate.username,
                    'candidate_name': best_candidate.first_name or best_candidate.username,
                    'candidate_score': best_score,
                    'delegations_sent': campaign.delegations_sent or 0,
                    'max_delegations': campaign.max_delegations or 0,
                    'default_deadline_hours': campaign.default_deadline_hours or 48,
                    'user_name': user.first_name or user.username or 'user',
                }, ensure_ascii=False),
                triggered_at=now_utc,
                expires_at=now_utc + timedelta(hours=8),
                cooldown_hours=4,
                batch_group='delegation',
            ))

        return anchors

    # ═══════════════════════════════════════════════════════
    # EMAIL OUTREACH SCANNER — автономная email-кампания
    # ═══════════════════════════════════════════════════════

    def _scan_email_outreach(self, user, session, now_utc) -> list:
        """Сканирует email-кампании:
        1. Активные кампании с черновиками (draft) → якорь email_outreach_send (агент отправит)
        2. Отправленные без ответа > 3 дней → якорь email_follow_up
        3. Входящие ответы → якорь email_reply_received (CRITICAL) — даже для paused кампаний!
        4. Ежедневный отчёт по активным кампаниям → email_campaign_report
        """
        anchors = []

        # Активные + paused + personal кампании (personal для обработки reply на одинарные письма)
        campaigns = session.query(EmailCampaign).filter(
            EmailCampaign.user_id == user.id,
            EmailCampaign.status.in_(['active', 'paused', 'personal'])
        ).all()

        if not campaigns:
            return anchors

        # Compute today_start ONCE (same user → same timezone for all campaigns)
        import pytz as _pytz_email
        _utz_email = _pytz_email.timezone(user.timezone or 'Europe/Moscow')
        _user_now_local = now_utc.astimezone(_utz_email)
        today_start = _user_now_local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)

        # Batch-load ALL EmailOutreach for all campaigns (avoid N+1 per campaign)
        _ec_campaign_ids = [c.id for c in campaigns]
        _ec_all_outreach = session.query(EmailOutreach).filter(
            EmailOutreach.campaign_id.in_(_ec_campaign_ids)
        ).all() if _ec_campaign_ids else []
        _ec_outreach_by_camp: dict = {}
        for _eo_item in _ec_all_outreach:
            _ec_outreach_by_camp.setdefault(_eo_item.campaign_id, []).append(_eo_item)

        for campaign in campaigns:
            is_paused = campaign.status == 'paused'
            is_personal = campaign.status == 'personal'
            _camp_outreach = _ec_outreach_by_camp.get(campaign.id, [])

            # Personal campaigns — только проверка ответов (reply), без send/follow-up/leads/report
            if is_personal:
                unreplied = [
                    o for o in _camp_outreach
                    if o.status == 'replied' and o.reply_text and not o.ai_reply_sent_at
                ]
                for email in unreplied:
                    email.ai_reply_sent_at = now_utc
                    try:
                        session.commit()
                    except Exception:
                        session.rollback()
                    anchors.append(Anchor(
                        user_id=user.id,
                        anchor_type='email_reply_received',
                        source=f'email:{email.id}:reply',
                        topic=_t(user,
                            f' Ответ от {email.recipient_email} ({email.recipient_name or email.recipient_company or "?"}) — личное письмо',
                            f' Reply from {email.recipient_email} ({email.recipient_name or email.recipient_company or "?"}) — personal email'),
                        priority=AnchorPriority.CRITICAL,
                        data=json.dumps({
                            'campaign_id': campaign.id,
                            'campaign_name': campaign.name,
                            'outreach_id': email.id,
                            'recipient_email': email.recipient_email,
                            'recipient_name': email.recipient_name,
                            'recipient_company': email.recipient_company,
                            'original_subject': email.subject,
                            'original_body': email.body[:500] if email.body else '',
                            'reply_text': __import__('re').sub(r'Content-[A-Za-z\-]+:[^\n]*\n?', '', __import__('re').sub(r'--[A-Za-z0-9_\-]{6,}[^\n]*\n?', '', email.reply_text[:2000])).strip()[:1000] if email.reply_text else '',
                        }),
                        triggered_at=now_utc,
                        expires_at=now_utc + timedelta(hours=24),
                        cooldown_hours=0.5,
                        batch_group='email',
                    ))
                continue  # Personal → skip send/follow-up/leads/report

            # --- 1. Есть черновики (draft) — агент должен написать и отправить ---
            # Пропускаем для paused кампаний
            drafts = []
            if not is_paused:
                drafts = [o for o in _camp_outreach if o.status == 'draft'][:10]

            # Дневной лимит — считаем «сегодня» по таймзоне пользователя, не UTC
            def _ts_aware(dt):
                return dt if dt is not None and dt.tzinfo is not None else (dt.replace(tzinfo=timezone.utc) if dt else None)

            sent_today = sum(
                1 for o in _camp_outreach
                if _ts_aware(o.sent_at) and _ts_aware(o.sent_at) >= today_start
                and o.status in ('sent', 'delivered', 'opened', 'replied')
            )

            remaining_daily = max(0, campaign.daily_limit - sent_today)
            # max_emails=0 означает безлимитную кампанию
            if campaign.max_emails and campaign.max_emails > 0:
                remaining_total = max(0, campaign.max_emails - (campaign.emails_sent or 0))
            else:
                remaining_total = 999999  # безлимит

            if drafts and remaining_daily > 0 and remaining_total > 0:
                batch_size = min(len(drafts), remaining_daily, 10)
                anchors.append(Anchor(
                    user_id=user.id,
                    anchor_type='email_outreach_send',
                    source=f'email_campaign:{campaign.id}:send:{now_utc.strftime("%Y-%m-%d")}',  # дедупликация по дню (было по часу → дубли)
                    topic=_t(user,
                        f'Email-кампания «{campaign.name}» — {len(drafts)} черновиков ждут отправки ({remaining_daily} осталось сегодня)',
                        f'Email campaign «{campaign.name}» — {len(drafts)} drafts pending ({remaining_daily} remaining today)'),
                    priority=AnchorPriority.MEDIUM,
                    data=json.dumps({
                        'campaign_id': campaign.id,
                        'campaign_name': campaign.name,
                        'campaign_goal': campaign.goal[:500] if campaign.goal else '',
                        'target_audience': campaign.target_audience[:300] if campaign.target_audience else '',
                        'offer': campaign.offer[:500] if campaign.offer else '',
                        'tone': campaign.tone,
                        'sender_name': campaign.sender_name,
                        'sender_email': campaign.sender_email,
                        'drafts': [{'id': d.id, 'email': d.recipient_email,
                                    'name': d.recipient_name,
                                    'company': d.recipient_company,
                                    'context': d.recipient_context} for d in drafts[:batch_size]],
                        'remaining_daily': remaining_daily,
                        'remaining_total': remaining_total,
                    }),
                    triggered_at=now_utc,
                    expires_at=now_utc + timedelta(hours=12),
                    cooldown_hours=0.3,  # ~20 мин между пакетами
                    batch_group='email',
                ))

            # --- 2. Follow-up: отправлено > 3 дней назад, без ответа, follow_up_count < max ---
            # Пропускаем для paused кампаний
            max_follow_ups = campaign.max_follow_ups or 2
            stale_emails = [] if is_paused else [
                o for o in _camp_outreach
                if o.status in ('sent', 'delivered', 'opened')
                and o.follow_up_count < max_follow_ups
                and o.next_follow_up_at is not None
                and (_ts_aware(o.next_follow_up_at) or o.next_follow_up_at.replace(tzinfo=timezone.utc)) <= now_utc
            ][:5]

            for email in stale_emails:
                days_since = 0
                if email.sent_at:
                    sa = email.sent_at
                    if sa.tzinfo is None:
                        sa = sa.replace(tzinfo=timezone.utc)
                    days_since = (now_utc - sa).days

                anchors.append(Anchor(
                    user_id=user.id,
                    anchor_type='email_follow_up',
                    source=f'email:{email.id}:follow_up:{email.follow_up_count + 1}',
                    topic=_t(user,
                        f'Follow-up #{email.follow_up_count + 1} для {email.recipient_email} ({days_since}д без ответа) — кампания «{campaign.name}»',
                        f'Follow-up #{email.follow_up_count + 1} for {email.recipient_email} ({days_since}d no reply) — campaign «{campaign.name}»'),
                    priority=AnchorPriority.MEDIUM,
                    data=json.dumps({
                        'campaign_id': campaign.id,
                        'campaign_name': campaign.name,
                        'campaign_goal': campaign.goal[:500] if campaign.goal else '',
                        'outreach_id': email.id,
                        'recipient_email': email.recipient_email,
                        'recipient_name': email.recipient_name,
                        'recipient_company': email.recipient_company,
                        'original_subject': email.subject,
                        'original_body': email.body[:500] if email.body else '',
                        'follow_up_number': email.follow_up_count + 1,
                        'days_since_sent': days_since,
                    }),
                    triggered_at=now_utc,
                    expires_at=now_utc + timedelta(days=2),
                    cooldown_hours=24,
                    batch_group='email',
                ))

            # --- 3. Входящие ответы (reply_text заполнен, но ai_reply не отправлен) ---
            unreplied = [
                o for o in _camp_outreach
                if o.status == 'replied' and o.reply_text and not o.ai_reply_sent_at
            ]

            for email in unreplied:
                # Превентивно ставим ai_reply_sent_at чтобы следующий скан
                # не создал ещё один якорь для этого же письма
                email.ai_reply_sent_at = now_utc
                try:
                    session.commit()
                except Exception:
                    session.rollback()

                anchors.append(Anchor(
                    user_id=user.id,
                    anchor_type='email_reply_received',
                    source=f'email:{email.id}:reply',
                    topic=_t(user,
                        f' Ответ от {email.recipient_email} ({email.recipient_name or email.recipient_company or "?"}) — кампания «{campaign.name}»',
                        f' Reply from {email.recipient_email} ({email.recipient_name or email.recipient_company or "?"}) — campaign «{campaign.name}»'),
                    priority=AnchorPriority.CRITICAL,
                    data=json.dumps({
                        'campaign_id': campaign.id,
                        'campaign_name': campaign.name,
                        'campaign_goal': campaign.goal[:500] if campaign.goal else '',
                        'outreach_id': email.id,
                        'recipient_email': email.recipient_email,
                        'recipient_name': email.recipient_name,
                        'recipient_company': email.recipient_company,
                        'original_subject': email.subject,
                        'original_body': email.body[:500] if email.body else '',
                        'reply_text': __import__('re').sub(r'Content-[A-Za-z\-]+:[^\n]*\n?', '', __import__('re').sub(r'--[A-Za-z0-9_\-]{6,}[^\n]*\n?', '', email.reply_text[:2000])).strip()[:1000] if email.reply_text else '',
                        'ai_previous_reply': email.ai_reply_text[:500] if email.ai_reply_text else None,
                    }),
                    triggered_at=now_utc,
                    expires_at=now_utc + timedelta(hours=24),
                    cooldown_hours=0.5,
                    batch_group='email',
                ))

            # --- Auto-complete: нет черновиков, нет ожидающих follow-up, все треды закрыты ---
            # Работает для ЛЮБЫХ АКТИВНЫХ кампаний (paused не автозавершаем):
            # - Переговоры (1 письмо): ответили + агент ответил → готово
            # - Привлечение (50 писем): агент сам добавляет лиды через add_email_leads,
            #   пока есть черновики — не завершается. Как только все обработаны → завершается.
            # НЕ автозавершаем если ещё есть квота (remaining_total) — email_need_leads найдёт ещё контакты
            if not is_paused and not drafts and not stale_emails and remaining_total <= 0:
                # Письма у которых ещё не закрыт цикл:
                # sent/delivered/opened с незакрытыми follow-up ИЛИ replied без ответа агента
                open_outreach = sum(
                    1 for o in _camp_outreach
                    if o.status in ('sent', 'delivered', 'opened')
                    and o.follow_up_count < (campaign.max_follow_ups or 2)
                )
                unanswered_replies = sum(
                    1 for o in _camp_outreach
                    if o.status == 'replied' and o.reply_text and not o.ai_reply_sent_at
                )
                total_outreach = len(_camp_outreach)
                if total_outreach > 0 and open_outreach == 0 and unanswered_replies == 0:
                    campaign.status = 'completed'
                    try:
                        session.commit()
                        logger.info(f"[ANCHOR] Auto-completed campaign #{campaign.id} «{campaign.name}» — all threads closed")
                    except Exception:
                        session.rollback()
                    continue  # Skip anchors for completed campaign

            # --- 3b. Нужны новые лиды: мало черновиков, но кампания не заполнена ---
            # Срабатывает когда: активная кампания, < 5 черновиков, ещё есть квота (total/daily)
            # Порог 5 (не 0) позволяет строить пайплайн лидов заранее, не ждать когда кончатся
            if not is_paused and len(drafts) < 5 and remaining_daily > 0 and remaining_total > 0:
                # Считаем сколько контактов уже есть (sent + draft)
                total_in_pipeline = len(_camp_outreach)

                # Если кампания зависла (> 3ч активна, 0 отправлено и 0 черновиков) — HIGH приоритет
                _camp_age_h = 0
                if campaign.created_at:
                    _ct = campaign.created_at
                    if _ct.tzinfo is None:
                        _ct = _ct.replace(tzinfo=timezone.utc)
                    _camp_age_h = (now_utc - _ct).total_seconds() / 3600
                _is_stuck = _camp_age_h >= 3 and (campaign.emails_sent or 0) == 0

                # Запускаем поиск только если ещё есть в квоте место
                if remaining_total > total_in_pipeline or remaining_total >= 999999:
                    anchors.append(Anchor(
                        user_id=user.id,
                        anchor_type='email_need_leads',
                        source=f'email_campaign:{campaign.id}:need_leads:{now_utc.strftime("%Y-%m-%d")}-{now_utc.hour // 2}',
                        topic=_t(user,
                            f' Кампания «{campaign.name}» — нет черновиков, найди новые контакты ({remaining_daily} квота сегодня)',
                            f' Campaign «{campaign.name}» — no drafts, find new leads ({remaining_daily} quota today)'),
                        priority=AnchorPriority.HIGH if _is_stuck else AnchorPriority.MEDIUM,
                        data=json.dumps({
                            'campaign_id': campaign.id,
                            'campaign_name': campaign.name,
                            'campaign_goal': campaign.goal[:500] if campaign.goal else '',
                            'target_audience': campaign.target_audience[:300] if campaign.target_audience else '',
                            'offer': campaign.offer[:300] if campaign.offer else '',
                            'total_in_pipeline': total_in_pipeline,
                            'remaining_daily': remaining_daily,
                            'remaining_total': min(remaining_total, 50),
                            'is_stuck': _is_stuck,
                        }),
                        triggered_at=now_utc,
                        expires_at=now_utc + timedelta(hours=6),
                        cooldown_hours=0.5,
                        batch_group='email',
                    ))

            # --- 4. Дневной отчёт по кампании (если есть активность, не для paused) ---
            if is_paused:
                continue
            total_sent = campaign.emails_sent or 0
            total_replied = campaign.emails_replied or 0
            if total_sent > 0 and sent_today > 0:
                # Ищем агента который управляет email-рассылкой для атрибуции сообщения
                _email_agent_name_r = None
                try:
                    from models import UserAgent as _UA_r
                    _ua_candidates_r = session.query(_UA_r).filter(
                        _UA_r.author_id == user.id, _UA_r.is_active == True,
                    ).all()
                    for _ua_r in _ua_candidates_r:
                        _keys_r = (getattr(_ua_r, 'user_api_keys', '') or '').lower()
                        _code_r = (getattr(_ua_r, 'python_code', '') or '').lower()
                        if 'gmail_user=' in _keys_r or 'resend_api_key=' in _keys_r or 'send_outreach_email' in _code_r:
                            _email_agent_name_r = _ua_r.name
                            break
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)
                anchors.append(Anchor(
                    user_id=user.id,
                    anchor_type='email_campaign_report',
                    source=f'email_campaign:{campaign.id}:report:{now_utc.strftime("%Y-%m-%d")}',
                    topic=_t(user,
                        f' Отчёт email-кампании «{campaign.name}»: {total_sent} отправлено, {total_replied} ответов, {sent_today} сегодня',
                        f' Email campaign «{campaign.name}» report: {total_sent} sent, {total_replied} replies, {sent_today} today'),
                    priority=AnchorPriority.LOW,
                    data=json.dumps({
                        'campaign_id': campaign.id,
                        'campaign_name': campaign.name,
                        'total_sent': total_sent,
                        'total_replied': total_replied,
                        'sent_today': sent_today,
                        'remaining_daily': remaining_daily,
                        'remaining_total': remaining_total,
                        **({'agent_name': _email_agent_name_r} if _email_agent_name_r else {}),
                    }),
                    triggered_at=now_utc,
                    expires_at=now_utc + timedelta(hours=18),
                    cooldown_hours=20,
                    batch_group='email',
                ))

        return anchors

    # ═══════════════════════════════════════════════════════
    # POST SCANNERS — ленточный автопостинг + канал
    # ═══════════════════════════════════════════════════════

    def _scan_post_opportunities(self, user, profile, session, now_utc) -> list:
        """Сканирует ВСЕ данные пользователя и создаёт якорь post_opportunity.

        AI потом сам решит, стоит ли делать пост и О ЧЁМ.
        Мы здесь только проверяем: есть ли вообще о чём писать.

        Время поста индивидуально для каждого пользователя:
        распределяется по user.id в окне 10:00–21:00.
        """
        anchors = []

        # Проверяем лимит постов за день
        user_tz = pytz.timezone(user.timezone or 'Europe/Moscow')
        user_now = datetime.now(user_tz)
        today_start = user_now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_utc = today_start.astimezone(pytz.UTC)

        posts_today = session.query(Post).filter(
            Post.user_id == user.id,
            Post.created_at >= today_start_utc
        ).count()

        feed_limit = MAX_FEED_PER_DAY
        if posts_today >= feed_limit:
            logger.debug(f"[ANCHOR] User {user.telegram_id}: skip post — already {posts_today}/{feed_limit} today")
            return anchors

        # ── Проверяем рабочие часы (10:00–22:00) ──
        current_hour = user_now.hour
        if current_hour < 10 or current_hour >= 22:
            logger.debug(f"[ANCHOR] User {user.telegram_id}: skip post — outside hours ({current_hour})")
            return anchors

        # ── Soft throttle: не более одного якоря каждые 4ч в рабочее время ──
        # Строгое «индивидуальное окно» убрано — оно пропускало дни при перезапуске бота.
        # Cooldown=4h на якоре уже ограничивает частоту; лимит постов за день = MAX_FEED_PER_DAY.
        # Дополнительно: рассеиваем нагрузку по user.id чтобы не всё сразу в 10:00
        import hashlib
        day_seed = f"{user.id}:{user_now.strftime('%Y-%m-%d')}"
        uid_hash = int(hashlib.md5(day_seed.encode()).hexdigest()[:8], 16)
        # Минимальный час старта = 10 + (hash % 3), т.е. 10, 11 или 12
        # Это мягко распределяет старт у разных пользователей, но не блокирует весь день
        earliest_start_hour = 10 + (uid_hash % 3)
        if user_now.hour < earliest_start_hour:
            logger.debug(f"[ANCHOR] User {user.telegram_id}: skip post — before personal start hour {earliest_start_hour}:00")
            return anchors

        # Собираем «материал» для AI:
        signals = []

        # 1. Завершённые задачи за 24ч
        recent_completed = session.query(Task).filter(
            Task.user_id == user.id,
            Task.status == 'completed',
            Task.actual_completion_time >= now_utc - timedelta(hours=24)
        ).all()
        if recent_completed:
            titles = [t.title for t in recent_completed[:5]]
            signals.append(f'completed_tasks:{len(recent_completed)}:{",".join(titles)}')

        # 2. Новые цели
        new_goals = session.query(Goal).filter(
            Goal.user_id == user.id,
            Goal.status == 'active',
            Goal.created_at >= now_utc - timedelta(hours=24)
        ).all()
        if new_goals:
            signals.append(f'new_goals:{",".join(g.title for g in new_goals[:3])}')

        # 3. Цель достигнута
        achieved_goals = session.query(Goal).filter(
            Goal.user_id == user.id,
            Goal.progress_percentage >= 100,
            Goal.status == 'active'
        ).all()
        if achieved_goals:
            signals.append(f'achieved_goals:{",".join(g.title for g in achieved_goals[:3])}')

        # 4. Стрик продуктивности (>=3 за 24ч)
        if len(recent_completed) >= 3:
            signals.append(f'productivity_streak:{len(recent_completed)}')

        # 5. Задачи с делегированием (ищет помощь)
        collab_tasks = session.query(Task).filter(
            Task.user_id == user.id,
            Task.delegated_to_username.isnot(None),
            Task.delegation_status == 'pending',
            Task.created_at >= now_utc - timedelta(hours=48)
        ).all()
        if collab_tasks:
            signals.append(f'seeking_help:{",".join(t.title for t in collab_tasks[:3])}')

        # 6. Контент из последнего диалога (интересные темы)
        recent_interactions = session.query(Interaction).filter(
            Interaction.user_id == user.id,
            Interaction.message_type == 'user',
            Interaction.created_at >= now_utc - timedelta(hours=12)
        ).order_by(Interaction.created_at.desc()).limit(5).all()
        if recent_interactions:
            topics = [i.content[:80] for i in recent_interactions if i.content]
            if topics:
                signals.append(f'recent_topics:{"||".join(topics[:3])}')

        # 7. Профиль: навыки/интересы (AI может сделать экспертный пост)
        if profile:
            if profile.skills:
                signals.append(f'skills:{profile.skills[:100]}')
            if profile.interests:
                signals.append(f'interests:{profile.interests[:100]}')
            if profile.position:
                signals.append(f'position:{profile.position[:80]}')
            if profile.city:
                signals.append(f'city:{profile.city[:50]}')

        # 8. Активные задачи (материал для поста "чем занимаюсь")
        if not signals or len(signals) < 2:
            active_tasks = session.query(Task).filter(
                Task.user_id == user.id,
                Task.status.in_(['pending', 'in_progress', 'active'])
            ).order_by(Task.due_date.asc()).limit(3).all()
            if active_tasks:
                signals.append(f'active_tasks:{",".join(t.title for t in active_tasks)}')

        # 9. Активные цели (материал для поста)
        if not signals or len(signals) < 2:
            active_goals = session.query(Goal).filter(
                Goal.user_id == user.id,
                Goal.status == 'active'
            ).limit(3).all()
            if active_goals:
                signals.append(f'active_goals:{",".join(g.title for g in active_goals)}')

        # Нет сигналов — нет якоря
        if not signals:
            return anchors

        # Создаём один общий якорь — AI решит что с этим делать
        source_key = f'post:{user_now.strftime("%Y-%m-%d")}:{posts_today}'
        anchors.append(Anchor(
            user_id=user.id,
            anchor_type='post_opportunity',
            source=source_key,
            topic=_t(user, f'Есть материал для {len(signals)} потенциальных постов в ленту', f'Material available for {len(signals)} potential feed posts'),
            priority=AnchorPriority.LOW,
            data=json.dumps({
                'signals': signals,
                'posts_today': posts_today,
                'user_name': user.first_name or user.username or 'user',
                'tier': 'tokens',  # Токенная модель
            }, ensure_ascii=False),
            triggered_at=now_utc,
            expires_at=now_utc + timedelta(hours=12),
            cooldown_hours=2,
            batch_group='posting',
        ))

        return anchors

    def _scan_channel_post(self, user, profile, session, now_utc) -> list:
        """PREMIUM: сканирует возможность постинга в Telegram-канал пользователя.

        Заменяет AutoMarketingService. AI решает контент.
        """
        anchors = []

        channel = getattr(user, 'telegram_channel', None)
        if not channel:
            return anchors

        # Проверяем auto_marketing_enabled
        if profile and hasattr(profile, 'auto_marketing_enabled') and not profile.auto_marketing_enabled:
            return anchors

        # Лимит: 1 пост в канал в день
        user_tz = pytz.timezone(user.timezone or 'Europe/Moscow')
        user_now = datetime.now(user_tz)
        today_start = user_now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_utc = today_start.astimezone(pytz.UTC)

        # Проверяем по AnchorDeliveryLog
        channel_posts_today = session.query(AnchorDeliveryLog).filter(
            AnchorDeliveryLog.user_id == user.id,
            AnchorDeliveryLog.created_at >= today_start_utc,
            AnchorDeliveryLog.anchor_types.contains('channel_post')
        ).count()

        if channel_posts_today >= MAX_CHANNEL_PER_DAY:
            return anchors

        # Рабочие часы (10:00–22:00) — единственный ограничитель времени
        if user_now.hour < 10 or user_now.hour >= 22:
            return anchors

        # Сигнально-ориентированный подход: постим когда есть реальный контент
        signals = []
        recent_completed = session.query(Task).filter(
            Task.user_id == user.id,
            Task.status == 'completed',
            Task.actual_completion_time >= now_utc - timedelta(hours=24)
        ).all()
        if recent_completed:
            titles = [t.title for t in recent_completed[:5]]
            signals.append(f'completed_tasks:{len(recent_completed)}:{",".join(titles)}')
        if len(recent_completed) >= 3:
            signals.append(f'productivity_streak:{len(recent_completed)}')
        achieved_goals = session.query(Goal).filter(
            Goal.user_id == user.id, Goal.progress_percentage >= 100, Goal.status == 'active'
        ).all()
        if achieved_goals:
            signals.append(f'achieved_goals:{",".join(g.title for g in achieved_goals[:3])}')
        if profile:
            if profile.skills:
                signals.append(f'skills:{profile.skills[:100]}')
            if profile.interests:
                signals.append(f'interests:{profile.interests[:100]}')
            if getattr(profile, 'content_strategy', None):
                signals.append(f'content_strategy:{profile.content_strategy[:200]}')
            if profile.position:
                signals.append(f'position:{profile.position[:80]}')
        if not signals:
            active_tasks = session.query(Task).filter(
                Task.user_id == user.id,
                Task.status.in_(['pending', 'in_progress', 'active'])
            ).order_by(Task.due_date.asc()).limit(3).all()
            if active_tasks:
                signals.append(f'active_tasks:{",".join(t.title for t in active_tasks)}')
        if not signals:
            return anchors

        content_strategy = getattr(profile, 'content_strategy', '') or '' if profile else ''
        interests = getattr(profile, 'interests', '') or '' if profile else ''
        goals = getattr(profile, 'goals', '') or '' if profile else ''
        skills = getattr(profile, 'skills', '') or '' if profile else ''

        anchors.append(Anchor(
            user_id=user.id,
            anchor_type='channel_post',
            source=f'channel:{user_now.strftime("%Y-%m-%d")}',
            topic=_t(user, f'Есть материал для поста в канал {channel}', f'Content ready for channel {channel} post'),
            priority=AnchorPriority.LOW,
            data=json.dumps({
                'channel': channel,
                'signals': signals,
                'content_strategy': content_strategy[:300],
                'interests': interests[:200],
                'goals': goals[:200],
                'skills': skills[:200],
                'user_name': user.first_name or user.username or 'user',
            }, ensure_ascii=False),
            triggered_at=now_utc,
            expires_at=now_utc + timedelta(hours=12),
            cooldown_hours=20,
            batch_group='posting',
        ))

        return anchors

    def _scan_discord_post(self, user, profile, session, now_utc) -> list:
        """Сигнально-ориентированный автопостинг в Discord-канал.

        Срабатывает когда есть контент — не по расписанию.
        Независим от channel_post и post_opportunity.
        """
        anchors = []

        discord_wh = getattr(user, 'discord_webhook', None)
        if not discord_wh or not discord_wh.startswith('https://discord.com/api/webhooks/'):
            return anchors

        # Лимит 1 пост в Discord в день (через AgentActivityLog)
        user_tz = pytz.timezone(user.timezone or 'Europe/Moscow')
        user_now = datetime.now(user_tz)
        today_start = user_now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_utc = today_start.astimezone(pytz.UTC)

        from models import AgentActivityLog as _AAL_dc
        discord_today = session.query(_AAL_dc).filter(
            _AAL_dc.user_id == user.id,
            _AAL_dc.activity_type == 'post_discord',
            _AAL_dc.created_at >= today_start_utc,
            _AAL_dc.status == 'published'
        ).count()
        if discord_today >= MAX_CHANNEL_PER_DAY:
            return anchors

        # Рабочие часы
        if user_now.hour < 10 or user_now.hour >= 22:
            return anchors

        # Сигналы контента
        signals = []
        recent_completed = session.query(Task).filter(
            Task.user_id == user.id,
            Task.status == 'completed',
            Task.actual_completion_time >= now_utc - timedelta(hours=24)
        ).all()
        if recent_completed:
            titles = [t.title for t in recent_completed[:5]]
            signals.append(f'completed_tasks:{len(recent_completed)}:{",".join(titles)}')
        if len(recent_completed) >= 3:
            signals.append(f'productivity_streak:{len(recent_completed)}')
        achieved_goals = session.query(Goal).filter(
            Goal.user_id == user.id, Goal.progress_percentage >= 100, Goal.status == 'active'
        ).all()
        if achieved_goals:
            signals.append(f'achieved_goals:{",".join(g.title for g in achieved_goals[:3])}')
        if profile:
            if profile.skills:
                signals.append(f'skills:{profile.skills[:100]}')
            if profile.interests:
                signals.append(f'interests:{profile.interests[:100]}')
            if getattr(profile, 'content_strategy', None):
                signals.append(f'content_strategy:{profile.content_strategy[:200]}')
        if not signals:
            active_tasks = session.query(Task).filter(
                Task.user_id == user.id,
                Task.status.in_(['pending', 'in_progress', 'active'])
            ).order_by(Task.due_date.asc()).limit(3).all()
            if active_tasks:
                signals.append(f'active_tasks:{",".join(t.title for t in active_tasks)}')
        if not signals:
            return anchors

        content_strategy = getattr(profile, 'content_strategy', '') or '' if profile else ''

        anchors.append(Anchor(
            user_id=user.id,
            anchor_type='discord_post',
            source=f'discord:{user_now.strftime("%Y-%m-%d")}',
            topic=_t(user, 'Есть материал для поста в Discord', 'Content ready for Discord post'),
            priority=AnchorPriority.LOW,
            data=json.dumps({
                'discord_webhook': discord_wh,
                'signals': signals,
                'content_strategy': content_strategy[:300],
                'user_name': user.first_name or user.username or 'user',
            }, ensure_ascii=False),
            triggered_at=now_utc,
            expires_at=now_utc + timedelta(hours=12),
            cooldown_hours=20,
            batch_group='posting',
        ))

        return anchors

    def _scan_weekly_milestone(self, user, session, now_utc) -> list:
        """Срабатывает в пятн-воск, если за неделю завершено >= 5 задач.

        Όднажды в неделю, социальная валидация продуктивности.
        """
        anchors = []
        user_tz = pytz.timezone(user.timezone or 'Europe/Moscow')
        user_now = datetime.now(user_tz)

        # Пятн-воск: 5-7 день недели
        if user_now.weekday() not in (4, 5, 6):  # 0=пн, 4=пт, 5=сб, 6=вс
            return anchors

        # Границы недели (ISO: пн-вс)
        days_since_monday = user_now.weekday()  # 0=пн
        week_start_user = user_now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_since_monday)
        week_start_utc = week_start_user.astimezone(pytz.UTC)

        week_key = user_now.strftime('%G-W%V')  # ISO week, e.g. 2026-W09
        source = f'weekly_milestone:{week_key}'

        # Считаем завершённые за неделю
        completed_count = session.query(Task).filter(
            Task.user_id == user.id,
            Task.status == 'completed',
            Task.actual_completion_time >= week_start_utc,
        ).count()

        if completed_count < 5:
            return anchors

        # Собираем названия задач для контекста AI
        completed_tasks = session.query(Task).filter(
            Task.user_id == user.id,
            Task.status == 'completed',
            Task.actual_completion_time >= week_start_utc,
        ).order_by(Task.actual_completion_time.desc()).limit(7).all()
        titles = [t.title for t in completed_tasks]

        anchors.append(Anchor(
            user_id=user.id,
            anchor_type='weekly_milestone',
            source=source,
            topic=_t(user,
                     f'Недельный итог — {completed_count} задач завершено!',
                     f'Weekly milestone — {completed_count} tasks completed!'),
            priority=AnchorPriority.MEDIUM,
            data=json.dumps({
                'completed_count': completed_count,
                'week': week_key,
                'titles': titles,
            }, ensure_ascii=False),
            triggered_at=now_utc,
            expires_at=now_utc + timedelta(hours=48),
            cooldown_hours=120,  # не чаще 1 раза в 5 дней
            batch_group='milestones',
        ))
        return anchors

    def _scan_goal_milestone(self, user, session, now_utc) -> list:
        """Срабатывает когда цель пересекает 25 / 50 / 75 / 100 процентов.

        Каждый порог срабатывает ровно один раз (идентификатор source = goal:{id}:{pct}).
        """
        anchors = []
        thresholds = (25, 50, 75, 100)

        active_goals = session.query(Goal).filter(
            Goal.user_id == user.id,
            Goal.status == 'active',
            Goal.progress_percentage > 0,
        ).all()

        # ── CLEANUP: если прогресс цели СНИЗИЛСЯ (откат), помечаем стейл milestone якоря ──
        # Например: goal был на 100%, потом metric скорректировали → goal теперь на 2%.
        # Старый pending anchor 'goal_milestone:14:100' будет доставлен и скажет
        # 'цель выполнена на 100%!' — что неверно. Помечаем такие якоря как delivered.
        try:
            _goal_ids = [g.id for g in active_goals]
            _goal_pct = {g.id: (g.progress_percentage or 0) for g in active_goals}
            _stale_milestones = session.query(Anchor).filter(
                Anchor.user_id == user.id,
                Anchor.anchor_type == 'goal_milestone',
                Anchor.delivered_at.is_(None),
            ).all()
            _stale_suppressed = 0
            for _sm in _stale_milestones:
                if not _sm.source:
                    continue
                _parts = (_sm.source or '').split(':')
                if len(_parts) < 3:
                    continue
                try:
                    _gid = int(_parts[1])
                    _mpct = int(_parts[2])
                except (ValueError, IndexError):
                    continue
                # Suppress если текущий прогресс цели МЕНЬШЕ milestone И разница > 30%
                _cur_pct = _goal_pct.get(_gid)
                if _cur_pct is not None and _cur_pct < _mpct - 30:
                    _sm.delivered_at = now_utc
                    _stale_suppressed += 1
                    logger.info(f"[ANCHOR] User {user.id}: suppressed stale milestone anchor {_sm.id} "
                                f"(goal {_gid}: milestone={_mpct}% but current={_cur_pct}%)")
            if _stale_suppressed:
                session.commit()
        except Exception as _sm_err:
            logger.debug(f"[ANCHOR] Stale milestone cleanup error: {_sm_err}")
            session.rollback()

        for goal in active_goals:
            pct = goal.progress_percentage or 0
            # Находим высший пройденный порог
            hit_threshold = None
            for t in sorted(thresholds, reverse=True):
                if pct >= t:
                    hit_threshold = t
                    break
            if hit_threshold is None:
                continue

            source = f'goal_milestone:{goal.id}:{hit_threshold}'

            anchors.append(Anchor(
                user_id=user.id,
                anchor_type='goal_milestone',
                source=source,
                topic=_t(user,
                         f'Цель «{goal.title[:50]}» — {hit_threshold}% выполнено!',
                         f'Goal «{goal.title[:50]}» — {hit_threshold}% done!'),
                priority=AnchorPriority.MEDIUM,
                data=json.dumps({
                    'goal_id': goal.id,
                    'title': goal.title,
                    'progress': pct,
                    'milestone': hit_threshold,
                }, ensure_ascii=False),
                triggered_at=now_utc,
                expires_at=now_utc + timedelta(days=3),
                cooldown_hours=168,  # 7 дней — порог не повторится
                batch_group='milestones',
            ))

        return anchors

    # ═══════════════════════════════════════════════════════
    # RECURRENCE HELPERS
    # ═══════════════════════════════════════════════════════

    def _calculate_next_recurrence(self, last_time, pattern: str, interval: int = 1):
        """Вычисляет следующее время для повторяющейся задачи.
        
        Args:
            last_time: datetime последнего срабатывания
            pattern: 'daily' | 'weekly' | 'monthly' | 'yearly'
            interval: каждые N единиц (по умолчанию 1)
        """
        import calendar

        if pattern == 'daily':
            return last_time + timedelta(days=interval)
        elif pattern == 'weekly':
            return last_time + timedelta(weeks=interval)
        elif pattern == 'monthly':
            year = last_time.year
            month = last_time.month + interval
            day = last_time.day
            while month > 12:
                year += 1
                month -= 12
            last_day = calendar.monthrange(year, month)[1]
            if day > last_day:
                day = last_day
            return last_time.replace(year=year, month=month, day=day)
        elif pattern == 'yearly':
            return last_time.replace(year=last_time.year + interval)
        else:
            return last_time + timedelta(days=interval)

    # ═══════════════════════════════════════════════════════
    # COOLDOWN & ANTI-SPAM
    # ═══════════════════════════════════════════════════════

    def _apply_cooldowns(self, anchors: list, user, session) -> list:
        """Фильтрует якоря по cooldown — один батч-запрос вместо N отдельных.

        Cooldown проверяется по source (точный ключ якоря) — это позволяет
        каждому агенту/источнику иметь независимый cooldown. Если source не
        совпадает ни с одной записью, используется fallback по anchor_type
        (для якорей без уникального source).
        """
        now_utc = datetime.now(timezone.utc)
        result = []

        # Один запрос: все недавние доставки этого пользователя
        # Берём max cooldown из списка якорей чтобы покрыть все 
        max_cooldown = max((a.cooldown_hours if a.cooldown_hours is not None and a.cooldown_hours > 0 else PRIORITY_COOLDOWN.get(a.priority, 4)) for a in anchors) if anchors else 8
        recent_deliveries = session.query(
            Anchor.anchor_type,
            Anchor.source,
            Anchor.delivered_at
        ).filter(
            Anchor.user_id == user.id,
            Anchor.delivered_at.isnot(None),
            Anchor.delivered_at >= now_utc - timedelta(hours=max_cooldown)
        ).all()

        # Индексируем по (anchor_type, source) точный ключ, по source fallback, по anchor_type глобальный fallback
        last_delivery_by_type_source: dict = {}  # (anchor_type, source) → datetime
        last_delivery_by_source: dict = {}        # source → datetime (fallback)
        last_delivery_by_type: dict = {}          # anchor_type → datetime (глобальный fallback)
        for atype, asource, delivered_at in recent_deliveries:
            ts_key = (atype, asource)
            if ts_key not in last_delivery_by_type_source or delivered_at > last_delivery_by_type_source[ts_key]:
                last_delivery_by_type_source[ts_key] = delivered_at
            if asource and (asource not in last_delivery_by_source or delivered_at > last_delivery_by_source[asource]):
                last_delivery_by_source[asource] = delivered_at
            if atype not in last_delivery_by_type or delivered_at > last_delivery_by_type[atype]:
                last_delivery_by_type[atype] = delivered_at

        # Типы, которые НЕ подлежат cooldown-фильтрации:
        # Silent/automated типы — имеют собственные rate limits (макс N за цикл)
        # ALWAYS_DELIVER типы ПОДЛЕЖАТ cooldown — их "always deliver" семантика
        # обеспечивается тем, что они не блокируются dialog_count/gap/night.
        _COOLDOWN_BYPASS = {
            'email_outreach_send', 'email_follow_up',
            'content_campaign_publish',
            'delegation_campaign_send', 'delegation_campaign_follow_up',
            'post_opportunity', 'channel_post', 'discord_post',
        }

        for anchor in anchors:
            # Критичные и silent типы всегда проходят (у них свои rate limits)
            if anchor.anchor_type in _COOLDOWN_BYPASS:
                result.append(anchor)
                continue

            cooldown_h = anchor.cooldown_hours if anchor.cooldown_hours is not None and anchor.cooldown_hours > 0 else PRIORITY_COOLDOWN.get(anchor.priority, 4)

            # Приоритет: (anchor_type, source) точный ключ → per-source fallback → per-type fallback
            # Точный ключ гарантирует что task_reminder НЕ блокирует task_overdue у той же задачи.
            _ts_key = (anchor.anchor_type, anchor.source)
            if _ts_key in last_delivery_by_type_source:
                last_delivered = last_delivery_by_type_source[_ts_key]
            elif anchor.source and anchor.source in last_delivery_by_source:
                # per-source fallback только для НЕ entity-источников (агенты, диспатч, ...)
                _entity_source_prefixes = (
                    'agent:', 'dispatch:', 'autopilot:', 'agent_scheduled:',
                    'task:', 'goal:', 'weather:', 'service_health:',
                    'email_campaign:',
                )
                if anchor.source.startswith(_entity_source_prefixes):
                    # Entity-source: используем только per-(type,source) — уже проверили выше, не совпало
                    last_delivered = None
                else:
                    last_delivered = last_delivery_by_source[anchor.source]
            else:
                # Для якорей с уникальным entity-source — type-level fallback НЕ применяем:
                # каждая задача/цель/погода/агент имеет независимый cooldown по своей записи.
                _entity_source_prefixes2 = (
                    'agent:', 'dispatch:', 'autopilot:', 'agent_scheduled:',
                    'task:', 'goal:', 'weather:', 'service_health:',
                    'email_campaign:',
                )
                _no_type_fallback = (
                    (anchor.source and anchor.source.startswith(_entity_source_prefixes2))
                    or anchor.anchor_type in ALWAYS_DELIVER_TYPES
                )
                if _no_type_fallback:
                    last_delivered = None
                else:
                    last_delivered = last_delivery_by_type.get(anchor.anchor_type)

            if last_delivered:
                if last_delivered.tzinfo is None:
                    last_delivered = last_delivered.replace(tzinfo=timezone.utc)
                if last_delivered >= now_utc - timedelta(hours=cooldown_h):
                    logger.debug(f"[ANCHOR] Cooldown: {anchor.anchor_type} source={anchor.source} (last delivered {last_delivered})")
                    continue

            result.append(anchor)

        # Адаптация: если пользователь игнорирует > 70% — понижаем частоту LOW ДИАЛОГОВЫХ
        # НО НЕ блокируем: posting (post_opportunity, channel_post) — это посты, не диалог
        # И НЕ считаем CRITICAL/HIGH доставки — они информационные, ответ не ожидается
        recent_logs = session.query(AnchorDeliveryLog).filter(
            AnchorDeliveryLog.user_id == user.id,
            AnchorDeliveryLog.created_at >= now_utc - timedelta(days=7)
        ).all()

        # Для подсчёта ignore rate берём только ДИАЛОГОВЫЕ (не CRITICAL/HIGH)
        dialog_logs = []
        for log in recent_logs:
            try:
                types = json.loads(log.anchor_types) if log.anchor_types else []
            except (json.JSONDecodeError, TypeError):
                types = []
            # Пропускаем логи, которые содержат ТОЛЬКО ALWAYS_DELIVER_TYPES
            if all(t in ALWAYS_DELIVER_TYPES for t in types) and types:
                continue
            # Пропускаем логи постов — они не диалоговые
            if any(t in ('post_opportunity', 'channel_post') for t in types):
                continue
            dialog_logs.append(log)

        if len(dialog_logs) >= 5:
            ignored = sum(1 for log in dialog_logs if not log.user_responded)
            ignore_rate = ignored / len(dialog_logs)
            if ignore_rate > 0.7:
                # НЕ блокируем — увеличиваем cooldown для необязательных LOW
                # Re-engagement типы (dialog_followup, task_stale, profile_gap) НУЖНЫ 
                # чтобы вернуть пользователя в строй — их не трогаем
                RE_ENGAGEMENT_TYPES = {
                    'dialog_followup', 'task_stale', 'profile_gap',
                    'post_opportunity', 'channel_post',
                    'inactivity_reengagement',
                }
                OPTIONAL_LOW = {'market_insight', 'content_opportunity', 'event_discovery'}
                # Pre-load doubled-cooldown results per OPTIONAL_LOW type (avoid N+1 per anchor)
                _opt_low_anchors = [a for a in result if a.priority == AnchorPriority.LOW and a.anchor_type in OPTIONAL_LOW]
                _recent_opt_by_type: dict = {}
                for _opt_type in {a.anchor_type for a in _opt_low_anchors}:
                    _type_max_cd = max(((a.cooldown_hours or 8) * 2) for a in _opt_low_anchors if a.anchor_type == _opt_type)
                    _recent_opt_by_type[_opt_type] = session.query(Anchor).filter(
                        Anchor.user_id == user.id,
                        Anchor.anchor_type == _opt_type,
                        Anchor.delivered_at.isnot(None),
                        Anchor.delivered_at >= now_utc - timedelta(hours=_type_max_cd)
                    ).first()
                # Необязательные LOW — удваиваем cooldown (через доп. фильтр)
                filtered = []
                for a in result:
                    if a.priority == AnchorPriority.LOW and a.anchor_type in OPTIONAL_LOW:
                        # Проверяем двойной cooldown
                        recent_opt = _recent_opt_by_type.get(a.anchor_type)
                        if recent_opt:
                            logger.debug(f"[ANCHOR] High ignore rate → doubled cooldown for {a.anchor_type}")
                            continue
                    filtered.append(a)
                result = filtered
                logger.info(f"[ANCHOR] User {user.telegram_id}: high ignore rate ({ignore_rate:.0%}), doubled cooldown for optional LOW (re-engagement kept)")

        return result

    # ═══════════════════════════════════════════════════════
    # AI DECISION LAYER
    # ═══════════════════════════════════════════════════════

    async def _process_post_anchor(self, user, anchor, session):
        """Обрабатывает постовый якорь: AI создаёт пост, публикует в ленту/канал."""
        try:
            # ── ЗАЩИТА ОТ ДУБЛЕЙ (race condition при деплое) ──
            fresh = session.query(Anchor).filter_by(id=anchor.id).with_for_update(skip_locked=True).first()
            if not fresh or fresh.delivered_at is not None:
                logger.info(f"[ANCHOR] Post anchor #{anchor.id} already delivered by another process, skip")
                return
            anchor = fresh

            # Проверяем и списываем токены (в той же сессии для атомарности)
            from token_service import spend_tokens, has_enough_tokens
            from config import FREE_ACCESS_MODE
            action = 'proactive_channel' if anchor.anchor_type == 'channel_post' else 'proactive_post'
            if not FREE_ACCESS_MODE:
                if not has_enough_tokens(user.telegram_id, action, session=session):
                    logger.info(f"[ANCHOR] User {user.telegram_id}: пропуск поста — нет токенов")
                    return
                spend_tokens(user.telegram_id, action, description=f'anchor_{anchor.anchor_type}', session=session, auto_commit=False)

            anchor_data = json.loads(anchor.data) if anchor.data else {}

            if anchor.anchor_type == 'post_opportunity':
                post_text = await self._ai_compose_post(user, anchor_data, session, mode='feed')
                if not post_text:
                    logger.debug(f"[ANCHOR] User {user.telegram_id}: AI decided SKIP for feed post")
                    # Удаляем якорь (не помечаем delivered — иначе cooldown блокирует следующую попытку)
                    try:
                        session.delete(anchor)
                        session.commit()
                    except Exception:
                        session.rollback()
                    return

                post = Post(
                    user_id=user.id,
                    username=user.username or user.first_name or f'user_{user.telegram_id}',
                    content=post_text,
                    created_at=datetime.now(timezone.utc)
                )
                session.add(post)
                session.flush()  # get post.id

                # Помечаем якорь как доставленный
                anchor.delivered_at = datetime.now(timezone.utc)

                log = AnchorDeliveryLog(
                    user_id=user.id,
                    anchor_ids=json.dumps([anchor.id]),
                    message_text=f'[FEED POST] {post_text[:200]}',
                    anchor_types=json.dumps([anchor.anchor_type]),
                )
                session.add(log)

                # Логируем в AgentActivityLog (для отображения в дашборде)
                activity_log = AgentActivityLog(
                    user_id=user.id,
                    activity_type='post_newsfeed',
                    title=post_text[:80] + ('...' if len(post_text) > 80 else ''),
                    content=post_text,
                    target='Лента новостей',
                    status='published',
                    ref_id=post.id,
                )
                session.add(activity_log)
                session.commit()

                # Авто-публикация в Discord (если webhook настроен)
                try:
                    if user.discord_webhook and user.discord_webhook.startswith('https://discord.com/api/webhooks/'):
                        import aiohttp as _aiohttp_dc
                        async with _aiohttp_dc.ClientSession() as http:
                            resp = await http.post(
                                user.discord_webhook,
                                json={"content": post_text},
                                timeout=_aiohttp_dc.ClientTimeout(total=15)
                            )
                            if resp.status in (200, 204):
                                dc_log = AgentActivityLog(
                                    user_id=user.id,
                                    activity_type='post_discord',
                                    title=post_text[:80] + ('...' if len(post_text) > 80 else ''),
                                    content=post_text,
                                    target='Discord канал',
                                    status='published',
                                )
                                session.add(dc_log)
                                session.commit()
                                logger.info(f"[ANCHOR] ✅ Auto-published feed post to Discord for {user.telegram_id}")
                            else:
                                logger.warning(f"[ANCHOR] Discord webhook failed ({resp.status}) for {user.telegram_id}")
                except Exception as dc_err:
                    logger.debug(f"[ANCHOR] Discord auto-publish failed (non-critical): {dc_err}")

                # Уведомляем пользователя
                if self.bot:
                    notify = (
                        f"Опубликовал пост в твою ленту:\n\n"
                        f"{post_text}\n\n"
                        f"Если не нравится — скажи, удалю."
                    )
                    await self.bot.send_message(chat_id=user.telegram_id, text=notify)
                    try:
                        from ai_integration.conversation_history import save_message_to_history as _smh
                        _smh(user.telegram_id, 'assistant', notify, session=session)
                    except Exception: pass
                logger.info(f"[ANCHOR] ✅ Feed post for {user.telegram_id}: {post_text[:80]}...")

            elif anchor.anchor_type == 'channel_post':
                channel = anchor_data.get('channel', '')
                if not channel:
                    return

                post_text = await self._ai_compose_post(user, anchor_data, session, mode='channel')
                if not post_text:
                    logger.debug(f"[ANCHOR] User {user.telegram_id}: AI decided SKIP for channel post")
                    # Удаляем якорь (не помечаем delivered — иначе cooldown блокирует следующую попытку)
                    try:
                        session.delete(anchor)
                        session.commit()
                    except Exception:
                        session.rollback()
                    return

                # Публикуем в канал
                published = False
                if self.bot:
                    try:
                        await self.bot.send_message(chat_id=channel, text=post_text)
                        published = True
                    except Exception as pub_err:
                        logger.error(f"[ANCHOR] Channel publish error ({channel}): {pub_err}")

                # Помечаем якорь
                anchor.delivered_at = datetime.now(timezone.utc)
                log = AnchorDeliveryLog(
                    user_id=user.id,
                    anchor_ids=json.dumps([anchor.id]),
                    message_text=f'[CHANNEL {channel}] {post_text[:200]}',
                    anchor_types=json.dumps([anchor.anchor_type]),
                )
                session.add(log)
                session.commit()

                # Уведомляем пользователя
                if self.bot:
                    status = "опубликован" if published else "не удалось опубликовать (проверь права бота в канале)"
                    notify = (
                        f"Пост в канал {channel} — {status}:\n\n"
                        f"{post_text[:500]}\n\n"
                        f"Если нужно поправить — скажи."
                    )
                    await self.bot.send_message(chat_id=user.telegram_id, text=notify)
                    try:
                        from ai_integration.conversation_history import save_message_to_history as _smh
                        _smh(user.telegram_id, 'assistant', notify, session=session)
                    except Exception: pass
                status_icon = "✅" if published else "❌"
                logger.info(f"[ANCHOR] {status_icon} Channel post for {user.telegram_id} -> {channel}: {post_text[:80]}...")

            elif anchor.anchor_type == 'discord_post':
                discord_wh = anchor_data.get('discord_webhook', '') or getattr(user, 'discord_webhook', '')
                if not discord_wh:
                    return

                post_text = await self._ai_compose_post(user, anchor_data, session, mode='discord')
                if not post_text:
                    logger.debug(f"[ANCHOR] User {user.telegram_id}: AI decided SKIP for discord post")
                    try:
                        session.delete(anchor)
                        session.commit()
                    except Exception:
                        session.rollback()
                    return

                # Публикуем в Discord
                dc_ok = False
                try:
                    import aiohttp as _aiohttp_dp
                    async with _aiohttp_dp.ClientSession() as http_dc:
                        resp = await http_dc.post(
                            discord_wh,
                            json={"content": post_text},
                            timeout=_aiohttp_dp.ClientTimeout(total=15),
                        )
                        dc_ok = resp.status in (200, 204)
                except Exception as dc_err:
                    logger.error(f"[ANCHOR] Discord webhook error: {dc_err}")

                # Помечаем якорь
                anchor.delivered_at = datetime.now(timezone.utc)

                # Логируем в AgentActivityLog
                from models import AgentActivityLog as _AAL_dpost
                activity = _AAL_dpost(
                    user_id=user.id,
                    activity_type='post_discord',
                    title=post_text[:80],
                    content=post_text,
                    target='Discord канал',
                    status='published' if dc_ok else 'failed',
                )
                session.add(activity)

                log = AnchorDeliveryLog(
                    user_id=user.id,
                    anchor_ids=json.dumps([anchor.id]),
                    message_text=f'[DISCORD] {post_text[:200]}',
                    anchor_types=json.dumps([anchor.anchor_type]),
                )
                session.add(log)
                session.commit()

                # Уведомляем пользователя
                if self.bot:
                    status = "опубликован" if dc_ok else "ошибка при публикации (проверь webhook)"
                    notify = (
                        f"Discord пост — {status}:\n\n"
                        f"{post_text[:500]}\n\n"
                        f"Если нужно поправить — скажи."
                    )
                    await self.bot.send_message(chat_id=user.telegram_id, text=notify)
                    try:
                        from ai_integration.conversation_history import save_message_to_history as _smh
                        _smh(user.telegram_id, 'assistant', notify, session=session)
                    except Exception: pass
                status_icon = "✅" if dc_ok else "❌"
                logger.info(f"[ANCHOR] {status_icon} Discord post for {user.telegram_id}: {post_text[:80]}...")

        except Exception as e:
            logger.error(f"[ANCHOR] _process_post_anchor error: {e}\n{traceback.format_exc()}")
            session.rollback()

    async def _process_content_campaign_anchor(self, user, anchor, session):
        """Обрабатывает контент-кампанию: AI создаёт пост, публикует на указанные площадки.

        Работает аналогично _process_email_silent_anchor — автономно, без диалога с пользователем.
        Уведомляет пользователя о публикации.
        """
        try:
            # ── ЗАЩИТА ОТ ДУБЛЕЙ ──
            fresh = session.query(Anchor).filter_by(id=anchor.id).with_for_update(skip_locked=True).first()
            if not fresh or fresh.delivered_at is not None:
                logger.info(f"[ANCHOR] Content campaign anchor #{anchor.id} already delivered, skip")
                return
            anchor = fresh

            # Проверяем и списываем токены
            from token_service import spend_tokens, has_enough_tokens
            from config import FREE_ACCESS_MODE
            if not FREE_ACCESS_MODE:
                if not has_enough_tokens(user.telegram_id, 'proactive_post', session=session):
                    logger.info(f"[ANCHOR] User {user.telegram_id}: пропуск контент-кампании — нет токенов")
                    return
                spend_tokens(user.telegram_id, 'proactive_post', description='content_campaign_publish', session=session, auto_commit=False)

            anchor_data = json.loads(anchor.data) if anchor.data else {}
            campaign_id = anchor_data.get('campaign_id')
            if not campaign_id:
                return

            campaign = session.query(ContentCampaign).filter_by(id=campaign_id).first()
            if not campaign or campaign.status != 'active':
                # Кампания удалена/остановлена — помечаем якорь доставленным, чтобы не срабатывал снова
                anchor.delivered_at = datetime.now(timezone.utc)
                session.commit()
                logger.info(f"[ANCHOR] Content campaign #{campaign_id} not active — marking anchor #{anchor.id} delivered")
                return

            platforms = anchor_data.get('platforms', ['feed'])
            campaign_goal = anchor_data.get('goal', '')
            topics = anchor_data.get('topics', '')
            tone = anchor_data.get('tone', 'professional')
            lang = anchor_data.get('language', 'ru')
            user_name = anchor_data.get('user_name', 'user')

            # AI генерирует пост
            post_text = await self._ai_compose_campaign_post(user, campaign, anchor_data, session)
            if not post_text:
                logger.debug(f"[ANCHOR] User {user.telegram_id}: AI decided SKIP for content campaign #{campaign_id}")
                try:
                    session.delete(anchor)
                    session.commit()
                except Exception:
                    session.rollback()
                return

            published_to = []

            # --- Публикация в ленту ---
            if 'feed' in platforms:
                post = Post(
                    user_id=user.id,
                    username=user.username or user.first_name or f'user_{user.telegram_id}',
                    content=post_text,
                    created_at=datetime.now(timezone.utc)
                )
                session.add(post)
                session.flush()
                activity = AgentActivityLog(
                    user_id=user.id,
                    activity_type='post_newsfeed',
                    title=post_text[:80] + ('...' if len(post_text) > 80 else ''),
                    content=post_text,
                    target='Лента новостей',
                    status='published',
                    ref_id=post.id,
                    result=f'campaign:{campaign.id}',
                )
                session.add(activity)
                published_to.append('лента')

            # --- Публикация в Telegram канал ---
            if 'telegram' in platforms and user.telegram_channel:
                tg_ok = False
                if self.bot:
                    try:
                        await self.bot.send_message(chat_id=user.telegram_channel, text=post_text)
                        tg_ok = True
                    except Exception as tg_err:
                        logger.error(f"[ANCHOR] Content campaign TG publish error: {tg_err}")
                activity = AgentActivityLog(
                    user_id=user.id,
                    activity_type='post_telegram',
                    title=post_text[:80] + ('...' if len(post_text) > 80 else ''),
                    content=post_text,
                    target=user.telegram_channel,
                    status='published' if tg_ok else 'failed',
                    result=f'campaign:{campaign.id}',
                )
                session.add(activity)
                if tg_ok:
                    published_to.append(f'TG {user.telegram_channel}')

            # --- Публикация в Discord ---
            if 'discord' in platforms and user.discord_webhook:
                dc_ok = False
                try:
                    import aiohttp as _aiohttp_cc
                    if user.discord_webhook.startswith('https://discord.com/api/webhooks/'):
                        async with _aiohttp_cc.ClientSession() as http:
                            resp = await http.post(
                                user.discord_webhook,
                                json={"content": post_text},
                                timeout=_aiohttp_cc.ClientTimeout(total=15)
                            )
                            dc_ok = resp.status in (200, 204)
                except Exception as dc_err:
                    logger.error(f"[ANCHOR] Content campaign Discord publish error: {dc_err}")
                activity = AgentActivityLog(
                    user_id=user.id,
                    activity_type='post_discord',
                    title=post_text[:80] + ('...' if len(post_text) > 80 else ''),
                    content=post_text,
                    target='Discord канал',
                    status='published' if dc_ok else 'failed',
                    result=f'campaign:{campaign.id}',
                )
                session.add(activity)
                if dc_ok:
                    published_to.append('Discord')

            # Обновляем кампанию
            campaign.posts_published = (campaign.posts_published or 0) + 1
            campaign.last_post_at = datetime.now(timezone.utc)

            # Помечаем якорь
            anchor.delivered_at = datetime.now(timezone.utc)
            log = AnchorDeliveryLog(
                user_id=user.id,
                anchor_ids=json.dumps([anchor.id]),
                message_text=f'[CONTENT CAMPAIGN #{campaign.id}] {post_text[:200]}',
                anchor_types=json.dumps([anchor.anchor_type]),
            )
            session.add(log)
            session.commit()

            # Уведомляем пользователя + сохраняем в историю чата для синхронизации
            if self.bot and published_to:
                platforms_str = ', '.join(published_to)
                notify = (
                    f" Контент-кампания «{campaign.name}» — пост #{campaign.posts_published}:\n\n"
                    f"{post_text[:500]}\n\n"
                    f"Опубликовано: {platforms_str}\n"
                    f"Если нужно поправить — скажи."
                )
                await self.bot.send_message(chat_id=user.telegram_id, text=notify)
                # Синхронизация: сохраняем сообщение агента в историю чата
                try:
                    from ai_integration.conversation_history import save_message_to_history
                    save_message_to_history(user.telegram_id, 'assistant', notify, session=session)
                except Exception as _hist_err:
                    logger.debug(f"[ANCHOR] Failed to save campaign notify to history: {_hist_err}")
            logger.info(f"[ANCHOR] ✅ Content campaign #{campaign.id} post #{campaign.posts_published} for {user.telegram_id}: {published_to}")

        except Exception as e:
            logger.error(f"[ANCHOR] _process_content_campaign_anchor error: {e}\n{traceback.format_exc()}")
            session.rollback()

    async def _ai_compose_campaign_post(self, user, campaign, anchor_data: dict, session) -> str | None:
        """AI генерирует пост для контент-кампании.

        Отличается от _ai_compose_post тем, что использует цель/темы кампании,
        а не общие сигналы пользователя.
        """
        try:
            import aiohttp

            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            user_name = user.first_name or user.username or 'Пользователь'
            lang = anchor_data.get('language', 'ru')
            tone_map = {
                'professional': 'профессиональный, экспертный',
                'casual': 'разговорный, дружеский, неформальный',
                'motivational': 'мотивирующий, вдохновляющий',
                'expert': 'экспертный, аналитический, глубокий',
                'friendly': 'дружелюбный, лёгкий',
            }
            tone_desc = tone_map.get(anchor_data.get('tone', 'professional'), 'профессиональный')

            # Номер поста и формат для ротации
            post_num = (campaign.posts_published or 0) + 1
            _formats = [
                "КЕЙС: реальная ситуация → применение инструмента/подхода → конкретный результат в цифрах",
                "СОВЕТ + ПРИМЕР: практический лайфхак с конкретным примером применения",
                "СТАТИСТИКА + ВЫВОД: реальный факт или цифра → практический вывод для читателя",
                "СРАВНЕНИЕ ДО/ПОСЛЕ: как было без инструмента/подхода → как стало после",
                "РАЗВЕНЧАНИЕ МИФА: распространённое заблуждение → реальность + доказательство",
            ]
            post_format = _formats[(post_num - 1) % len(_formats)]

            # История предыдущих постов этой кампании (чтобы не повторяться)
            prev_posts_logs = session.query(AgentActivityLog).filter(
                AgentActivityLog.user_id == user.id,
                AgentActivityLog.result == f'campaign:{campaign.id}',
                AgentActivityLog.status == 'published'
            ).order_by(AgentActivityLog.created_at.desc()).limit(3).all()
            prev_posts_texts = [p.content for p in prev_posts_logs if p.content]

            # Кто пишет — только роль/должность (НЕ интересы, чтобы не уводить тему)
            author_context = []
            if profile:
                if profile.position: author_context.append(f"Должность/роль: {profile.position}")
                if profile.about: author_context.append(f"О себе: {profile.about[:120]}")

            # DDG: свежий контекст из интернета по темам кампании
            # Делаем 2-3 разных запроса для более глубокого контента
            fresh_data = []
            try:
                from ai_integration.api_client import get_api_client
                api = get_api_client()
                search_query = (anchor_data.get('topics', '') or anchor_data.get('goal', ''))[:60]
                if search_query:
                    from datetime import datetime as dt
                    import asyncio as _aio_ddg
                    year = dt.now().strftime('%Y')
                    # Параллельные запросы для разностороннего контента
                    queries = [
                        f'{search_query} тренды {year}',
                        f'{search_query} советы лайфхаки примеры использования',
                        f'{search_query} кейсы автоматизация практика',
                    ]
                    tasks_ddg = [api.duckduckgo_search(q, num=3, cache_ttl=7200) for q in queries]
                    results_all = await _aio_ddg.gather(*tasks_ddg, return_exceptions=True)
                    seen_titles = set()
                    for batch in results_all:
                        if isinstance(batch, Exception) or not batch:
                            continue
                        for r in batch[:3]:
                            title = r.get('title', '')
                            if title and title not in seen_titles:
                                seen_titles.add(title)
                                fresh_data.append(f"  — {title}: {r.get('snippet', '')[:120]}")
                    # Ограничиваем до 6 самых релевантных
                    fresh_data = fresh_data[:6]
            except Exception as _e:
                logger.debug("suppressed: %s", _e)

            system_msg = (
                f"Ты — SMM-специалист, продвигающий конкретный продукт или идею в социальных сетях.\n\n"
                f"КАМПАНИЯ: {campaign.name}\n"
                f"ЦЕЛЬ КАМПАНИИ: {anchor_data.get('goal', 'не указана')}\n"
                f"ТЕМЫ: {anchor_data.get('topics', 'любые')}\n"
                f"ТОН: {tone_desc}\n"
                f"ПОСТ #{post_num}"
                f"{f' из {campaign.max_posts}' if campaign.max_posts else ''}\n"
                f"ФОРМАТ ЭТОГО ПОСТА: {post_format}\n\n"
                f"ПРАВИЛА:\n"
                f"1. Пиши от ПЕРВОГО лица, как будто сам пользователь\n"
                f"2. СТРОГО придерживайся цели и тем кампании — пиши ТОЛЬКО про них\n"
                f"3. ИГНОРИРУЙ любые личные интересы автора, не связанные с темой кампании\n"
                f"4. Каждый пост должен быть УНИКАЛЬНЫМ — НЕ повторяй предыдущие посты кампании\n"
                f"5. 3-8 предложений, {tone_desc} стиль\n"
                f"6. БЕЗ эмодзи, без хештегов, без призывов вроде 'подписывайтесь'\n"
                f"7. Если есть свежие данные из сети — используй их: цитируй статистику, упоминай конкретные примеры, ссылайся на реальные факты\n"
                f"8. Верни ТОЛЬКО текст поста. Ничего больше.\n"
                f"9. Пиши КОНКРЕТНО: не 'AI помогает в работе', а 'AI-агент за 15 секунд составляет email по 3 ключевым словам — экономит 20 минут'\n"
                f"10. Каждый пост = ОДНА практическая фишка/совет/кейс. Не пытайся охватить всё.\n\n"
                f"ВАЖНО: Тема кампании — приоритет №1. Посторонние темы ЗАПРЕЩЕНЫ."
            )

            user_prompt_parts = [f"Автор: {user_name}"]
            if author_context:
                user_prompt_parts.append("\nКОНТЕКСТ АВТОРА (используй для голоса, не для темы):")
                user_prompt_parts.extend(author_context)
            if prev_posts_texts:
                user_prompt_parts.append("\nПРЕДЫДУЩИЕ ПОСТЫ ЭТОЙ КАМПАНИИ (НЕ ПОВТОРЯЙ):")
                for _i, _pt in enumerate(prev_posts_texts, 1):
                    user_prompt_parts.append(f"  Пост {_i}: {_pt[:250]}")
            if fresh_data:
                user_prompt_parts.append("\nСВЕЖИЕ ДАННЫЕ ИЗ СЕТИ:")
                user_prompt_parts.extend(fresh_data)
            user_prompt_parts.append(
                f"\nНапиши пост #{post_num} строго по теме кампании: {anchor_data.get('topics', anchor_data.get('goal', campaign.name))}"
            )

            user_prompt = "\n".join(user_prompt_parts)

            url = "https://api.deepseek.com/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            }
            data = {
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.85,
                "max_tokens": 600
            }

            async with aiohttp.ClientSession() as http:
                async with http.post(url, json=data, headers=headers,
                                     timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    if resp.status != 200:
                        logger.error(f"[ANCHOR] AI compose campaign post error: HTTP {resp.status}")
                        return None
                    result = await resp.json()

            choice = result.get('choices', [{}])[0]
            text = choice.get('message', {}).get('content', '').strip()

            if not text or text.upper() == 'SKIP' or len(text) < 20:
                return None

            # Очистка: убираем обрамление кавычками если AI добавил
            if text.startswith('"') and text.endswith('"'):
                text = text[1:-1]
            if text.startswith('«') and text.endswith('»'):
                text = text[1:-1]

            return text.strip()

        except Exception as e:
            logger.error(f"[ANCHOR] _ai_compose_campaign_post error: {e}")
            return None

    # ═══════════════════════════════════════════════════════
    # DELEGATION CAMPAIGN PROCESSOR — автономное делегирование
    # ═══════════════════════════════════════════════════════

    async def _process_delegation_campaign_anchor(self, user, anchor, session):
        """Обрабатывает якорь delegation_campaign_send: находит исполнителя, делегирует задачу.

        Работает автономно, без диалога с пользователем.
        Создаёт Task, отправляет уведомление исполнителю, обновляет счётчики кампании.
        """
        try:
            # ── Защита от дублей ──
            fresh = session.query(Anchor).filter_by(id=anchor.id).with_for_update(skip_locked=True).first()
            if not fresh or fresh.delivered_at is not None:
                logger.info(f"[ANCHOR] Delegation campaign anchor #{anchor.id} already delivered, skip")
                return
            anchor = fresh

            # Токены
            from token_service import check_and_deduct
            allowed = await check_and_deduct(user.telegram_id, 'delegate_task', session)
            if not allowed:
                logger.info(f"[ANCHOR] Delegation campaign anchor #{anchor.id}: insufficient tokens")
                anchor.delivered_at = datetime.datetime.now(timezone.utc)
                session.commit()
                return

            anchor_data = json.loads(anchor.data or '{}')
            campaign_id = anchor_data.get('campaign_id')
            if not campaign_id:
                anchor.delivered_at = datetime.datetime.now(timezone.utc)
                session.commit()
                return

            campaign = session.query(DelegationCampaign).filter_by(id=campaign_id).first()
            if not campaign or campaign.status != 'active':
                anchor.delivered_at = datetime.datetime.now(timezone.utc)
                session.commit()
                return

            candidate_username = anchor_data.get('candidate_username')
            if not candidate_username:
                anchor.delivered_at = datetime.datetime.now(timezone.utc)
                session.commit()
                return

            # Проверяем кандидата
            candidate = session.query(User).filter(
                User.username.ilike(candidate_username)
            ).first()
            if not candidate:
                logger.warning(f"[ANCHOR] Delegation campaign: candidate @{candidate_username} not found")
                anchor.delivered_at = datetime.datetime.now(timezone.utc)
                session.commit()
                return

            # Проверяем блокировку
            is_blocked = False
            try:
                from models import UserBlock
                is_blocked = session.query(UserBlock).filter(
                    ((UserBlock.blocker_id == user.id) & (UserBlock.blocked_id == candidate.id)) |
                    ((UserBlock.blocker_id == candidate.id) & (UserBlock.blocked_id == user.id))
                ).first()
            except Exception:
                pass  # UserBlock may not exist yet
            if is_blocked:
                logger.info(f"[ANCHOR] Delegation campaign: @{candidate_username} blocked, skip")
                anchor.delivered_at = datetime.datetime.now(timezone.utc)
                session.commit()
                return

            # ── Генерируем текст задачи через AI ──
            task_title, task_description, delegation_message = await self._ai_compose_delegation(
                user, campaign, anchor_data, candidate, session
            )
            if not task_title:
                anchor.delivered_at = datetime.datetime.now(timezone.utc)
                session.commit()
                return

            # ── Создаём задачу ──
            deadline_hours = anchor_data.get('default_deadline_hours', 48)
            now_utc = datetime.datetime.now(timezone.utc)
            due_date = now_utc + timedelta(hours=deadline_hours)

            task = Task(
                user_id=user.id,
                title=task_title[:255],
                description=task_description[:2000] if task_description else None,
                status='pending',
                priority='medium',
                due_date=due_date,
                delegated_by=user.id,
                delegated_to_username=candidate.username,
                delegation_status='pending',
                delegation_details=f"[Кампания «{campaign.name}» #{campaign.id}] {(campaign.offer or '')[:200]}",
                delegation_campaign_id=campaign.id,
            )
            session.add(task)
            session.flush()

            # ── Логируем активность ──
            log_entry = AgentActivityLog(
                user_id=user.id,
                activity_type='delegation',
                title=f'Делегировано @{candidate.username}: {task_title[:100]}',
                content=task_description[:500] if task_description else '',
                target=f'@{candidate.username}',
                status='pending',
                ref_id=str(task.id),
                result=f'campaign:{campaign.id}',
            )
            session.add(log_entry)

            # ── Обновляем счётчики кампании ──
            campaign.delegations_sent = (campaign.delegations_sent or 0) + 1
            campaign.last_delegation_at = now_utc

            # Проверяем достижение лимита
            if campaign.max_delegations and campaign.max_delegations > 0:
                if campaign.delegations_sent >= campaign.max_delegations:
                    campaign.status = 'completed'

            # ── Отправляем уведомление исполнителю ──
            if candidate.telegram_id and delegation_message:
                try:
                    from handlers import bot
                    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [
                            InlineKeyboardButton(text='Принять', callback_data=f'accept_deleg_{task.id}'),
                            InlineKeyboardButton(text='Отклонить', callback_data=f'reject_deleg_{task.id}'),
                        ]
                    ])
                    await bot.send_message(
                        candidate.telegram_id,
                        delegation_message,
                        reply_markup=kb,
                        parse_mode='HTML',
                    )
                    logger.info(f"[ANCHOR] Delegation campaign #{campaign.id}: delegated «{task_title[:50]}» to @{candidate.username}")
                except Exception as e:
                    logger.warning(f"[ANCHOR] Delegation campaign: failed to notify @{candidate_username}: {e}")

            # ── Маркируем якорь ──
            anchor.delivered_at = now_utc
            anchor.delivery_result = f'delegated_task:{task.id}:@{candidate.username}'
            session.commit()

            # ── Уведомляем пользователя (кратко) ──
            try:
                from handlers import bot
                await bot.send_message(
                    user.telegram_id,
                    f"<b>Кампания «{campaign.name}»</b>\n"
                    f"Делегировано @{candidate.username}: {task_title[:100]}\n"
                    f"Отправлено {campaign.delegations_sent}"
                    f"{f'/{campaign.max_delegations}' if campaign.max_delegations else ''}",
                    parse_mode='HTML',
                )
            except Exception as _e:
                logger.debug("suppressed: %s", _e)

        except Exception as e:
            logger.error(f"[ANCHOR] _process_delegation_campaign_anchor error: {e}\n{traceback.format_exc()}")
            try:
                anchor.delivered_at = datetime.datetime.now(timezone.utc)
                session.commit()
            except Exception:
                session.rollback()

    async def _ai_compose_delegation(self, user, campaign, anchor_data: dict, candidate, session) -> tuple:
        """Генерирует текст задачи и сообщение для делегирования через AI.

        Returns: (task_title, task_description, delegation_message) or (None, None, None)
        """
        try:
            candidate_profile = session.query(UserProfile).filter_by(user_id=candidate.id).first()
            candidate_info = ''
            if candidate_profile:
                parts = []
                if candidate_profile.bio:
                    parts.append(f"Bio: {candidate_profile.bio[:200]}")
                if candidate_profile.skills:
                    parts.append(f"Skills: {candidate_profile.skills[:200]}")
                if candidate_profile.interests:
                    parts.append(f"Interests: {candidate_profile.interests[:200]}")
                candidate_info = '\n'.join(parts)

            system_msg = (
                "Ты AI-менеджер проекта. Создаёшь задачу для делегирования конкретному исполнителю.\n"
                "Задача должна быть чёткой, конкретной и мотивирующей.\n"
                "Ответ СТРОГО в формате:\n"
                "TITLE: [краткое название задачи 5-15 слов]\n"
                "DESCRIPTION: [подробное описание: что сделать, ожидаемый результат, 2-4 предложения]\n"
                "MESSAGE: [личное сообщение исполнителю: представься, объясни задачу, мотивируй. 3-5 предложений. Без HTML.]"
            )
            user_msg = (
                f"Кампания: {campaign.name}\n"
                f"Цель кампании: {anchor_data.get('goal', '')[:300]}\n"
                f"Целевая аудитория: {anchor_data.get('target_audience', '')[:200]}\n"
                f"Шаблон задачи: {anchor_data.get('task_template', '')[:300]}\n"
                f"Предложение/мотивация: {anchor_data.get('offer', '')[:200]}\n"
                f"Тон: {anchor_data.get('tone', 'professional')}\n\n"
                f"ИСПОЛНИТЕЛЬ: @{candidate.username} ({candidate.first_name or 'user'})\n"
                f"{candidate_info}\n\n"
                f"ДЕЛЕГАТОР: {user.first_name or user.username}\n\n"
                f"Создай ЗАДАЧУ и СООБЩЕНИЕ ИСПОЛНИТЕЛЮ."
            )

            import aiohttp
            async with aiohttp.ClientSession() as http:
                resp = await http.post(
                    'https://api.deepseek.com/chat/completions',
                    headers={'Authorization': f'Bearer {DEEPSEEK_API_KEY}', 'Content-Type': 'application/json'},
                    json={
                        'model': 'deepseek-chat',
                        'messages': [
                            {'role': 'system', 'content': system_msg},
                            {'role': 'user', 'content': user_msg},
                        ],
                        'max_tokens': 600,
                        'temperature': 0.7,
                    },
                    timeout=aiohttp.ClientTimeout(total=30),
                )
                if resp.status != 200:
                    logger.warning(f"[ANCHOR] _ai_compose_delegation: API error {resp.status}")
                    return (None, None, None)
                data = await resp.json()
                text = data.get('choices', [{}])[0].get('message', {}).get('content', '')

            if not text:
                return (None, None, None)

            # Парсим ответ
            title = ''
            description = ''
            message = ''
            for line in text.split('\n'):
                line_s = line.strip()
                if line_s.upper().startswith('TITLE:'):
                    title = line_s[6:].strip()
                elif line_s.upper().startswith('DESCRIPTION:'):
                    description = line_s[12:].strip()
                elif line_s.upper().startswith('MESSAGE:'):
                    message = line_s[8:].strip()

            # Если DESCRIPTION/MESSAGE многострочные (всё что после TITLE/DESCRIPTION до следующего маркера)
            if not description and 'DESCRIPTION:' in text:
                parts = text.split('DESCRIPTION:')
                if len(parts) > 1:
                    desc_part = parts[1].split('MESSAGE:')[0].strip()
                    description = desc_part
            if not message and 'MESSAGE:' in text:
                parts = text.split('MESSAGE:')
                if len(parts) > 1:
                    message = parts[1].strip()

            if not title:
                title = f"Задача от {user.first_name or user.username}: {(campaign.goal or 'помощь')[:80]}"

            return (title[:500], description[:2000], message[:1500])

        except Exception as e:
            logger.error(f"[ANCHOR] _ai_compose_delegation error: {e}")
            return (None, None, None)

    async def _process_email_silent_anchor(self, user, anchor, session):
        """Обрабатывает email-якорь МОЛЧА: напрямую генерирует текст через AI и отправляет.

        Не отправляет сообщение пользователю — только выполняет email-действие.
        """
        try:
            # ── ЗАЩИТА ОТ ДУБЛЕЙ ──
            fresh = session.query(Anchor).filter_by(id=anchor.id).with_for_update(skip_locked=True).first()
            if not fresh or fresh.delivered_at is not None:
                logger.info(f"[ANCHOR] Email anchor #{anchor.id} already delivered by another process, skip")
                return
            anchor = fresh

            # Проверяем и списываем токены
            from token_service import spend_tokens, has_enough_tokens
            from config import FREE_ACCESS_MODE
            action = 'email_send' if anchor.anchor_type == 'email_outreach_send' else 'email_follow_up'
            if not FREE_ACCESS_MODE:
                if not has_enough_tokens(user.telegram_id, action, session=session):
                    logger.info(f"[ANCHOR] User {user.telegram_id}: пропуск email — нет токенов")
                    return

            anchor_data = json.loads(anchor.data) if anchor.data else {}

            def _detect_recipient_lang(email='', name='', company='', context='',
                                        campaign_goal='', campaign_offer=''):
                """Определяет язык письма: сначала по признакам получателя,
                фолбэк — язык кампании (goal/offer)."""
                def _has_cyr(s):
                    return any('\u0400' <= c <= '\u04ff' for c in (s or ''))

                # Сильные сигналы получателя — однозначно русский
                ru_domains = any((email or '').lower().endswith(d)
                                 for d in ('.ru', '.by', '.ua', '.kz', '.рф'))
                cyr_in_name = _has_cyr(f"{name} {company}")
                # Русские платформы в контексте исследования
                _ctx_lower = (context or '').lower()
                ru_platforms = any(p in _ctx_lower for p in [
                    'habr', 'vc.ru', 'хабр', 'pikabu', 'dtf.ru', 'mail.ru',
                    'rambler', 'yandex.ru', 'vk.com', 't.me', 'ok.ru',
                ])
                if ru_domains or cyr_in_name or ru_platforms:
                    return 'Russian'

                # Анализируем язык контекста получателя (надёжнее языка кампании)
                # Кампания может быть написана на русском, а получатель — международный разработчик
                if context:
                    _ctx_cyr = sum(1 for c in context if '\u0400' <= c <= '\u04ff')
                    _ctx_lat = sum(1 for c in context if 'a' <= c.lower() <= 'z')
                    if _ctx_lat > 20 and _ctx_cyr < _ctx_lat * 0.3:
                        return 'English'   # контекст явно на английском — пишем на английском
                    if _ctx_cyr > 20 and _ctx_cyr > _ctx_lat:
                        return 'Russian'   # контекст явно на русском

                # По умолчанию — английский для международных получателей.
                # НЕ используем язык кампании как фолбэк: русскоязычная цель кампании
                # не означает что получатель говорит по-русски.
                return 'English'

            if anchor.anchor_type == 'email_outreach_send':
                # ═══ ПРЯМАЯ ОТПРАВКА: AI пишет тексты → мы отправляем напрямую ═══
                campaign_id = anchor_data.get('campaign_id')
                if not campaign_id:
                    logger.info(f"[ANCHOR] Email anchor #{anchor.id}: no campaign_id, skip")
                    return

                # ── ПЕРЕЧИТЫВАЕМ draft'ы из БД (а не из JSON-снимка) чтобы не обработать уже отправленные ──
                live_drafts = session.query(EmailOutreach).filter_by(
                    campaign_id=campaign_id, status='draft'
                ).limit(10).all()
                if not live_drafts:
                    logger.info(f"[ANCHOR] Email anchor #{anchor.id}: no live drafts in DB, marking delivered")
                    anchor.delivered_at = datetime.now(timezone.utc)
                    session.commit()
                    return

                from ai_integration.api_client import get_api_client
                from ai_integration.handlers import send_outreach_email
                api = get_api_client()

                campaign_name = anchor_data.get('campaign_name', '')
                campaign_goal = anchor_data.get('campaign_goal', '')
                target_audience = anchor_data.get('target_audience', '')
                offer = anchor_data.get('offer', '')
                tone = anchor_data.get('tone', 'professional')
                sender_name = anchor_data.get('sender_name', '')

                # Пересчитываем remaining_daily из живых данных БД (anchor_data может быть устаревшим)
                import pytz as _pytz_rem
                _user_tz_rem = _pytz_rem.timezone(user.timezone or 'Europe/Moscow')
                _today_start_rem = datetime.now(_user_tz_rem).replace(
                    hour=0, minute=0, second=0, microsecond=0
                ).astimezone(timezone.utc)
                live_campaign = session.query(EmailCampaign).filter_by(id=campaign_id).first()
                if not live_campaign:
                    logger.info(f"[ANCHOR] Email anchor #{anchor.id}: campaign {campaign_id} deleted, skip")
                    anchor.delivered_at = datetime.now(timezone.utc)
                    session.flush()
                    return
                _sent_today_live = session.query(EmailOutreach).filter(
                    EmailOutreach.campaign_id == campaign_id,
                    EmailOutreach.sent_at >= _today_start_rem,
                    EmailOutreach.status.in_(['sent', 'delivered', 'opened', 'replied']),
                ).count()
                remaining = max(0, live_campaign.daily_limit - _sent_today_live)

                sent_count = 0
                _owner_email = (getattr(user, 'email', '') or '').strip().lower()
                for d_obj in live_drafts:
                    if sent_count >= remaining:
                        break
                    email = d_obj.recipient_email or ''
                    # ── GUARD: пропускаем draft на email самого пользователя ──
                    if _owner_email and email.strip().lower() == _owner_email:
                        d_obj.status = 'failed'
                        logger.info(f"[ANCHOR] Skipping draft #{d_obj.id}: recipient is user's own email")
                        continue
                    name = d_obj.recipient_name or '?'
                    company = d_obj.recipient_company or ''
                    context = d_obj.recipient_context or ''

                    # Определяем язык получателя (домен, имя, платформы) → фолбэк на язык кампании
                    lang_hint = _detect_recipient_lang(
                        email=email, name=name, company=company, context=context,
                        campaign_goal=campaign_goal, campaign_offer=offer,
                    )

                    compose_prompt = (
                        f"Write a cold outreach email for this specific person.\n\n"
                        f"Campaign: {campaign_name}\nGoal: {campaign_goal}\n"
                        f"Offer: {offer}\nTone: {tone}\nSender: {sender_name}\n\n"
                        f"Recipient: {email}\nName: {name}\n"
                        f"{'Company/project: ' + company if company else ''}\n"
                        f"Research context about recipient: {context or 'none'}\n"
                        f"USE THE CONTEXT ABOVE to personalize the email! If context mentions specific "
                        f"projects, products, articles, or achievements — reference them in your opening.\n\n"
                        f"Language: {lang_hint} — write the ENTIRE email (subject AND body) in {lang_hint} only. "
                        f"Language was auto-detected from recipient signals (domain, name, context language). "
                        f"Do NOT mix languages.\n\n"
                        f"Return ONLY a JSON object: {{\"subject\": \"...\", \"body\": \"...\"}}\n\n"
                        f"STRICT QUALITY RULES:\n"
                        f"- Subject: 3-7 words, specific to THIS person, no spam words (free, amazing, opportunity)\n"
                        f"- Length: 120-200 words, 4-5 short paragraphs. NOT shorter — too short looks lazy.\n"
                        f"- PARAGRAPH BREAKS: separate every paragraph with a blank line (\\n\\n) in the body field. No HTML, no markdown.\n"
                        f"- STRUCTURE (follow this order):\n"
                        f"  1. RESEARCH HOOK (1-2 sent): show you studied their company/project. "
                        f"Mention a SPECIFIC product, feature, article, metric, or achievement. "
                        f"'I noticed your work in [broad field]' is TOO VAGUE. "
                        f"'Saw your [specific product/feature] — [specific observation]' is GOOD.\n"
                        f"  2. BRIDGE (1 sent): connect their work to yours — why them specifically.\n"
                        f"  3. VALUE (1-2 sent): what you do/offer concretely, what result.\n"
                        f"  4. PROOF (0-1 sent): one brief fact — users, traction, result. Optional.\n"
                        f"  5. QUESTION (1 sent): simple closing question — 'is this relevant?', 'worth a chat?'\n"
                        f"- First email = introduction + question, NOT a hard pitch. Don't sell, explore interest.\n"
                        f"- NO links, NO URLs, NO website mentions in first email\n"
                        f"- NO corporate buzzwords: 'streamlining workflows', 'leveraging synergies', 'driving innovation'\n"
                        f"- Write like a HUMAN colleague — warm, specific, genuine. Not a marketing bot.\n"
                        f"- No HTML, no markdown, no signatures"
                    )

                    try:
                        ai_result = await api.deepseek_analyze(
                            prompt=compose_prompt,
                            system_prompt="You write cold outreach emails. Return ONLY valid JSON with subject and body fields.",
                            max_tokens=500,
                            temperature=0.7,
                        )
                        if not ai_result:
                            logger.warning(f"[ANCHOR] AI compose failed for {email}: empty result")
                            continue

                        # Парсим JSON
                        import json as _json_compose
                        text = ai_result.strip()
                        if '```' in text:
                            for part in text.split('```'):
                                part = part.strip()
                                if part.startswith('json'):
                                    part = part[4:].strip()
                                if part.startswith('{'):
                                    text = part
                                    break
                        parsed = _json_compose.loads(text)
                        subject = parsed.get('subject', '')
                        body = parsed.get('body', '')

                        if not subject or not body:
                            logger.warning(f"[ANCHOR] AI compose: missing subject/body for {email}")
                            continue

                        # Отправляем напрямую через send_outreach_email
                        result = await send_outreach_email(
                            campaign_id=campaign_id,
                            recipient_email=email,
                            recipient_name=name if name != '?' else None,
                            recipient_company=company or None,
                            recipient_context=context or None,
                            subject=subject,
                            body=body,
                            user_id=user.telegram_id,
                            session=session,
                            close_session=False,
                        )
                        logger.info(f"[ANCHOR] Direct send to {email}: {(result or '')[:100]}")
                        if result and ('отправлено' in result.lower() or 'sent' in result.lower()):
                            sent_count += 1
                        elif result and ('лимит' in result.lower() or 'limit' in result.lower()):
                            logger.info(f"[ANCHOR] Daily limit reached, stopping batch")
                            break
                        elif result and ('resend api' in result.lower() or 'не настроен' in result.lower() or 'domain' in result.lower()):
                            # Постоянная ошибка конфигурации — прекращаем всю партию,
                            # уведомляем пользователя один раз
                            logger.error(f"[ANCHOR] Permanent send error for campaign #{campaign_id}: {result[:200]}")
                            # Уведомляем пользователя о проблеме
                            try:
                                _notify_text = (
                                    f"⚠️ Не удаётся отправить письма по кампании «{campaign_name}».\n"
                                    f"Причина: {result.strip()[:200]}\n"
                                    f"\nПроверь настройки Resend (RESEND_FROM должен быть верифицированным доменом — resend.com → Domains).\n"
                                    f"Письма ждут в черновиках — как только исправишь, отправятся автоматически."
                                )
                                await self._send_telegram_message(user.telegram_id, _notify_text)
                            except Exception as _ntf_err:
                                logger.warning(f"[ANCHOR] Failed to notify user about send error: {_ntf_err}")
                            break  # не пытаемся остальных — та же ошибка будет

                    except Exception as _compose_err:
                        logger.error(f"[ANCHOR] Compose/send error for {email}: {_compose_err}")
                        continue

                logger.info(f"[ANCHOR] ✅ Direct email batch: sent {sent_count}/{len(live_drafts)} for campaign #{campaign_id}")

                # Списываем токены: по одному за каждое реально отправленное письмо
                if not FREE_ACCESS_MODE and sent_count > 0:
                    from token_service import spend_tokens as _sp_bulk
                    for _i_sent in range(sent_count):
                        _sp_bulk(user.telegram_id, action, description=f'anchor_email_outreach_send {_i_sent+1}/{sent_count}', session=session, auto_commit=False)

                # Помечаем якорь как доставленный
                anchor.delivered_at = datetime.now(timezone.utc)
                log = AnchorDeliveryLog(
                    user_id=user.id,
                    anchor_ids=json.dumps([anchor.id]),
                    message_text=f'[EMAIL_SILENT] email_outreach_send: sent {sent_count}/{len(live_drafts)} emails for campaign «{campaign_name}»',
                    anchor_types=json.dumps([anchor.anchor_type]),
                )
                session.add(log)
                session.commit()
                return

            elif anchor.anchor_type == 'email_follow_up':
                # ═══ ПРЯМОЙ FOLLOW-UP: AI пишет текст → отправляем напрямую ═══
                from ai_integration.api_client import get_api_client
                from ai_integration.handlers import send_follow_up_email
                api = get_api_client()

                recipient_email = anchor_data.get('recipient_email', '')
                recipient_name = anchor_data.get('recipient_name', '')
                company_info = anchor_data.get('recipient_company', '')
                original_subject = anchor_data.get('original_subject', '')
                original_body = anchor_data.get('original_body', '')[:300]
                follow_up_number = anchor_data.get('follow_up_number', 1)
                days_since = anchor_data.get('days_since_sent', 0)
                outreach_id = anchor_data.get('outreach_id')

                # Определяем язык из данных получателя → фолбэк на язык кампании
                lang_hint = _detect_recipient_lang(
                    email=recipient_email, name=recipient_name, company=company_info,
                    context=f"{original_subject} {original_body}",
                    campaign_goal=anchor_data.get('campaign_goal', ''),
                    campaign_offer=anchor_data.get('offer', ''),
                )

                compose_prompt = (
                    f"Write a follow-up email (#{follow_up_number}) for an unanswered cold outreach.\n\n"
                    f"Campaign: {anchor_data.get('campaign_name', '')}\n"
                    f"Goal: {anchor_data.get('campaign_goal', '')}\n"
                    f"Original subject: {original_subject}\n"
                    f"Original email: {original_body}\n"
                    f"Recipient: {recipient_email} ({recipient_name})\n"
                    f"{'Company: ' + company_info if company_info else ''}\n"
                    f"Days since sent: {days_since}\n"
                    f"Language: {lang_hint} — write the entire follow-up in {lang_hint} only (auto-detected from recipient).\n\n"
                    f"Return ONLY a JSON object: {{\"body\": \"...\"}}\n"
                    f"Rules: short (60-100 words), 2-3 paragraphs, add new value, don't repeat original, be polite, no pressure.\n"
                    f"PARAGRAPH BREAKS: separate every paragraph with a blank line (\\n\\n) in the body field. Plain text only."
                )

                try:
                    ai_result = await api.deepseek_analyze(
                        prompt=compose_prompt,
                        system_prompt="You write follow-up emails. Return ONLY valid JSON with body field.",
                        max_tokens=400,
                        temperature=0.7,
                    )
                    if ai_result:
                        import json as _json_fu
                        text = ai_result.strip()
                        if '```' in text:
                            for part in text.split('```'):
                                part = part.strip()
                                if part.startswith('json'):
                                    part = part[4:].strip()
                                if part.startswith('{'):
                                    text = part
                                    break
                        parsed = _json_fu.loads(text)
                        fu_body = parsed.get('body', '')

                        if fu_body and outreach_id:
                            result = await send_follow_up_email(
                                outreach_id=outreach_id,
                                body=fu_body,
                                user_id=user.telegram_id,
                                session=session,
                                close_session=False,
                            )
                            logger.info(f"[ANCHOR] Direct follow-up to {recipient_email}: {(result or '')[:100]}")
                except Exception as _fu_err:
                    logger.error(f"[ANCHOR] Follow-up compose/send error: {_fu_err}")

                # Списываем токены за follow-up
                if not FREE_ACCESS_MODE:
                    _fu_spend = spend_tokens(user.telegram_id, action, description=f'anchor_email_follow_up', session=session, auto_commit=False)
                    if not _fu_spend.get('success'):
                        logger.info("[ANCHOR] User %d: skip follow-up billing — %s", user.telegram_id, _fu_spend.get('error', ''))

                # Помечаем якорь как доставленный
                anchor.delivered_at = datetime.now(timezone.utc)
                log = AnchorDeliveryLog(
                    user_id=user.id,
                    anchor_ids=json.dumps([anchor.id]),
                    message_text=f'[EMAIL_SILENT] email_follow_up: follow-up #{follow_up_number} to {recipient_email}',
                    anchor_types=json.dumps([anchor.anchor_type]),
                )
                session.add(log)
                session.commit()
                return

            elif anchor.anchor_type == 'email_need_leads':
                # Напрямую вызываем _auto_find_leads — без AI-модели
                campaign_id = anchor_data.get('campaign_id')
                if not campaign_id:
                    logger.info(f"[ANCHOR] email_need_leads #{anchor.id}: no campaign_id, skip")
                    return

                campaign = session.query(EmailCampaign).filter_by(id=campaign_id).first()
                if not campaign or campaign.status != 'active':
                    logger.info(f"[ANCHOR] email_need_leads #{anchor.id}: campaign not found or not active — marking delivered to prevent re-queue")
                    anchor.delivered_at = datetime.now(timezone.utc)
                    session.commit()
                    return

                from ai_integration.handlers import _auto_find_leads
                count, msg = await _auto_find_leads(
                    campaign=campaign,
                    user=user,
                    target_audience=anchor_data.get('target_audience', campaign.target_audience or ''),
                    goal=anchor_data.get('campaign_goal', campaign.goal or ''),
                    offer=anchor_data.get('offer', campaign.offer or ''),
                    session=session,
                )
                logger.info(f"[ANCHOR] email_need_leads #{anchor.id}: found {count} leads for campaign #{campaign_id}")

                # Помечаем якорь как доставленный
                anchor.delivered_at = datetime.now(timezone.utc)
                log = AnchorDeliveryLog(
                    user_id=user.id,
                    anchor_ids=json.dumps([anchor.id]),
                    message_text=f'[EMAIL_SILENT] email_need_leads: found {count} new leads for campaign «{campaign.name}»',
                    anchor_types=json.dumps([anchor.anchor_type]),
                )
                session.add(log)
                session.commit()

                # ── Если 0 новых лидов и кампания почти не продвинулась — сообщить пользователю ──
                if count == 0 and (campaign.emails_sent or 0) < 3:
                    _total_created_at = campaign.created_at
                    if _total_created_at:
                        if _total_created_at.tzinfo is None:
                            _total_created_at = _total_created_at.replace(tzinfo=timezone.utc)
                        _age_h = (datetime.now(timezone.utc) - _total_created_at).total_seconds() / 3600
                    else:
                        _age_h = 999
                    if _age_h >= 1.5:  # Кампания существует > 1.5ч и всё ещё без лидов
                        _camp_goal_short = (campaign.goal or campaign.name or '')[:120]
                        _camp_audience = (campaign.target_audience or '')[:100]
                        # Определяем тип аудитории для умных подсказок
                        _aud_lower = f"{_camp_audience} {_camp_goal_short}".lower()
                        _is_tech = any(w in _aud_lower for w in ('python', 'developer', 'разработ', 'programmer', 'github', 'saas', 'startup', 'ai ', 'ml ', 'bot', 'api', 'engineer', 'инженер', 'frontend', 'backend', 'fullstack'))
                        _is_biz = any(w in _aud_lower for w in ('b2b', 'компан', 'бизнес', 'предприн', 'hr', 'cto', 'ceo', 'founder', 'director'))
                        _has_github_tok = bool(anchor_data.get('github_token', ''))

                        if _is_tech and not _has_github_tok:
                            _suggestions = (
                                f"💡 Вижу что цель связана с tech-аудиторией.\n"
                                f"Чтобы найти разработчиков через GitHub — добавь в настройки агента:\n"
                                f"  GITHUB_TOKEN=ghp_xxx (создай на github.com → Settings → Developer settings → Tokens)\n"
                                f"Это даёт 5000 запросов/час вместо 60 — агент найдёт сотни email.\n\n"
                                f"Или напиши: \"Кристина, найди мне 10 Python-разработчиков с GitHub\""
                            )
                        elif _is_tech:
                            _suggestions = (
                                f"💡 Tech-аудитория: GitHub ищем активно, но может лимит исчерпан.\n"
                                f"Скажи точнее: кого именно ищем?\n"
                                f"  а) \"разработчики Python фолловеры >50\" — конкретный GitHub-запрос\n"
                                f"  б) \"indie hacker с SaaS продуктом\" — другой сегмент\n"
                                f"  в) \"авторы open-source telegram ботов\" — конкретная ниша"
                            )
                        elif _is_biz:
                            _suggestions = (
                                f"💡 B2B-аудитория: hh.ru ищем HR/CTO компаний, но email редко публичные.\n"
                                f"Что поможет лучше:\n"
                                f"  а) Укажи конкретные компании или домены (example.com)\n"
                                f"  б) Добавь LinkedIn URL нужных людей\n"
                                f"  в) Напиши кому именно хочешь написать — помогу найти"
                            )
                        else:
                            _suggestions = (
                                f"💡 Помогут уточнения:\n"
                                f"  а) Какие площадки/сайты посещает твоя аудитория?\n"
                                f"  б) Есть ли Telegram-группы или форумы по теме?\n"
                                f"  в) Можешь накинуть 2-3 известных тебе email для старта?"
                            )
                        _notify_text = (
                            f"📧 Кампания «{campaign.name}» пока не нашла контакты\n\n"
                            f"Цель: {_camp_goal_short}\n\n"
                            f"{_suggestions}"
                        )
                        try:
                            await self.bot.send_message(
                                chat_id=user.telegram_id,
                                text=_notify_text,
                            )
                            logger.info(f"[ANCHOR] email_need_leads: notified user {user.telegram_id} about stuck campaign #{campaign_id}")
                        except Exception as _notify_err:
                            logger.warning(f"[ANCHOR] email_need_leads notify error: {_notify_err}")
                return
            else:
                return

        except Exception as e:
            logger.error(f"[ANCHOR] _process_email_silent_anchor error: {e}\n{traceback.format_exc()}")
            try:
                session.rollback()
            except Exception:
                pass
            # Safety: mark anchor as delivered via NEW session so it isn't stuck PENDING forever.
            # Next scan cycle will create a fresh anchor if there's still work to do.
            try:
                _s2 = Session()
                try:
                    _a2 = _s2.query(Anchor).filter_by(id=anchor.id).first()
                    if _a2 and not _a2.delivered_at:
                        _a2.delivered_at = datetime.now(timezone.utc)
                        # Записываем ошибку в delivery_log чтобы видеть её в диагностике
                        _err_log = AnchorDeliveryLog(
                            user_id=_a2.user_id,
                            anchor_ids=json.dumps([_a2.id]),
                            message_text=f'[EMAIL_SILENT_ERROR] {_a2.anchor_type}: {str(e)[:300]}',
                            anchor_types=json.dumps([_a2.anchor_type]),
                        )
                        _s2.add(_err_log)
                        _s2.commit()
                        logger.info(f"[ANCHOR] email_silent_anchor #{anchor.id}: marked delivered via fallback session after exception")
                finally:
                    _s2.close()
            except Exception as _fb_err:
                logger.warning(f"[ANCHOR] email_silent_anchor #{anchor.id}: fallback deliver failed: {_fb_err}")

    async def _ai_compose_post(self, user, anchor_data: dict, session, mode: str = 'feed') -> str | None:
        """Просит AI создать пост на основе данных пользователя.

        AI получает ВСЕ сигналы и сам решает:
        - Стоит ли публиковать вообще (SKIP)
        - О чём написать
        - В каком стиле

        Args:
            mode: 'feed' | 'channel' | 'discord'
        Returns:
            str текст поста или None (если SKIP)
        """
        try:
            import aiohttp

            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            user_name = user.first_name or user.username or 'Пользователь'

            # Профиль
            profile_info = []
            if profile:
                if profile.skills: profile_info.append(f"Навыки: {profile.skills[:100]}")
                if profile.interests: profile_info.append(f"Интересы: {profile.interests[:100]}")
                if profile.goals: profile_info.append(f"Цели: {profile.goals[:100]}")
                if profile.position: profile_info.append(f"Должность: {profile.position}")
                if profile.city: profile_info.append(f"Город: {profile.city}")

            # Сигналы
            signals = anchor_data.get('signals', [])

            # DDG: подтягиваем свежий контекст из интернета по нише/интересам
            try:
                from ai_integration.api_client import get_api_client
                api = get_api_client()
                niche = ''
                if profile:
                    niche = (getattr(profile, 'interests', '') or getattr(profile, 'goals', '') or '')[:60]
                if niche:
                    from datetime import datetime as dt
                    fresh_query = f'{niche} тренды новости {dt.now().strftime("%Y")}'
                    fresh_results = await api.duckduckgo_search(fresh_query, num=3, cache_ttl=7200)
                    if fresh_results:
                        signals.append("СВЕЖИЕ ДАННЫЕ ИЗ СЕТИ:")
                        for r in fresh_results[:3]:
                            title = r.get('title', '')
                            snippet = r.get('snippet', '')[:120]
                            signals.append(f"  — {title}: {snippet}")
            except Exception as e:
                logger.debug(f"[ANCHOR] DDG post enrichment failed (non-critical): {e}")

            if mode == 'feed':
                system_msg = (
                    "Ты — автономный агент ASI Biont. Твоя задача — решить, стоит ли сделать пост в ленту "
                    "от лица пользователя.\n\n"
                    "ПРАВИЛА:\n"
                    "1. ВСЕГДА старайся написать пост. Верни SKIP ТОЛЬКО если сигналов буквально 0 или профиль абсолютно пустой\n"
                    "2. Пиши от ПЕРВОГО лица, как будто сам пользователь делится с миром\n"
                    "3. Пост может быть О ЧЁМ УГОДНО: достижения, мысли, поиск людей, экспертное мнение, "
                    "итоги дня, просьба о помощи, инсайты, открытия, планы — выбери самое полезное\n"
                    "4. Даже 1 сигнал — достаточно для поста. Навыки или интересы из профиля = хороший повод для экспертного поста\n"
                    "5. Естественный, живой стиль. 3-6 предложений. БЕЗ эмодзи, без хештегов, без призывов к действию\n"
                    "6. НЕ ВЫДУМЫВАЙ факты. Основывайся ТОЛЬКО на реальных сигналах ниже\n"
                    "7. Верни ТОЛЬКО текст поста или SKIP. Ничего больше."
                )
            elif mode == 'discord':
                content_strategy = anchor_data.get('content_strategy', '')
                system_msg = (
                    "Ты — контент-менеджер, создающий посты для Discord-сервера пользователя.\n\n"
                    "ПРАВИЛА:\n"
                    "1. ВСЕГДА старайся написать пост. Верни SKIP ТОЛЬКО если нет ни одного сигнала\n"
                    "2. Пиши от лица автора, живо и по-человечески — Discord ценит человечность\n"
                    "3. Допустимо использование Discord Markdown (**bold**, _italic_)\n"
                    f"4. Контент-стратегия: {content_strategy or 'не указана'}\n"
                    "5. 2-5 предложений — Discord-аудитория предпочитает лаконичность\n"
                    "6. Верни ТОЛЬКО текст поста или SKIP."
                )
            else:  # channel
                content_strategy = anchor_data.get('content_strategy', '')
                system_msg = (
                    "Ты — контент-менеджер для Telegram-канала пользователя.\n\n"
                    "ПРАВИЛА:\n"
                    "1. ВСЕГДА старайся написать пост. Верни SKIP ТОЛЬКО если профиль абсолютно пустой и нет ни одного сигнала\n"
                    "2. Пиши от лица автора канала, экспертно и полезно\n"
                    "3. Пост должен нести ценность для аудитории канала\n"
                    f"4. Контент-стратегия: {content_strategy or 'не указана'}\n"
                    "5. 3-8 предложений, естественный стиль. Можно Markdown.\n"
                    "6. Верни ТОЛЬКО текст поста или SKIP."
                )

            # Собираем user prompt
            user_prompt_parts = [f"Пользователь: {user_name}"]

            if profile_info:
                user_prompt_parts.append("\nПРОФИЛЬ:")
                user_prompt_parts.extend(profile_info)

            if signals:
                user_prompt_parts.append(f"\nСИГНАЛЫ ({len(signals)}):")
                for s in signals:
                    user_prompt_parts.append(f"- {s}")
            elif mode in ('channel', 'discord'):
                # Для канала/Discord без сигналов — AI пишет на основе профиля/стратегии
                user_prompt_parts.append("\nСоздай пост на основе профиля и контент-стратегии.")

            user_prompt_parts.append("\nРешение: напиши пост или SKIP.")

            user_prompt = "\n".join(user_prompt_parts)

            # Прямой вызов AI API (без агентского пайплайна — посты не требуют tool calling)
            url = "https://api.deepseek.com/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            }
            data = {
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.8,
                "max_tokens": 600
            }

            async with aiohttp.ClientSession() as aio_session:
                async with aio_session.post(url, headers=headers, json=data, 
                                           timeout=aiohttp.ClientTimeout(total=30)) as response:
                    if response.status != 200:
                        error = await response.text()
                        logger.error(f"[ANCHOR] Post AI API error: {response.status} {error[:200]}")
                        return None
                    result_json = await response.json()
                    text = result_json['choices'][0]['message']['content'].strip()

            if not text or text.upper() == 'SKIP' or text.upper().startswith('SKIP'):
                return None

            # Очистка: убираем кавычки если AI обернул
            post_text = text.strip().strip('"').strip("'")
            if len(post_text) < 20:
                return None

            return post_text

        except Exception as e:
            logger.error(f"[ANCHOR] _ai_compose_post error: {e}\n{traceback.format_exc()}")
            return None

    def _compose_always_deliver_fallback(self, anchors: list, user) -> str | None:
        """Шаблонный fallback для ALWAYS_DELIVER якорей когда AI недоступен.

        Гарантирует доставку напоминаний и критических уведомлений
        даже при сбое AI (timeout, API down и т.д.).
        """
        parts = []
        user_lang = getattr(user, 'language', None) or 'ru'
        for a in anchors:
            try:
                data = json.loads(a.data) if isinstance(a.data, str) and a.data else {}
            except Exception:
                data = {}
            title = data.get('title') or ''

            if a.anchor_type == 'task_reminder':
                sched = data.get('scheduled_time', '')
                if user_lang == 'en':
                    parts.append(f"Time for \"{title}\" has come{f' (scheduled {sched})' if sched else ''}. How's it going — done, in progress, or need to reschedule?")
                else:
                    parts.append(f"Пора: «{title}»{f' (назначено на {sched})' if sched else ''} — готово, в процессе или перенести?")
            elif a.anchor_type == 'task_overdue':
                if user_lang == 'en':
                    parts.append(f"Task \"{title}\" is overdue. Want to reschedule or mark complete?")
                else:
                    parts.append(f"Задача «{title}» просрочена. Перенести или отметить выполненной?")
            elif a.anchor_type == 'task_deadline_soon':
                if user_lang == 'en':
                    parts.append(f"Deadline for \"{title}\" is approaching. Need help?")
                else:
                    parts.append(f"Дедлайн по «{title}» приближается. Нужна помощь?")
            elif a.anchor_type == 'service_degraded':
                # Parse services list from data, map to human-readable names
                _SVC_NAMES = {
                    'deepseek': 'AI-модель (DeepSeek)',
                    'ddg': 'веб-поиск (DuckDuckGo)',
                    'openweathermap': 'прогноз погоды',
                    'resend': 'отправка email',
                    'telegram': 'Telegram',
                    'discord': 'Discord',
                    'yookassa': 'платежи (ЮKassa)',
                }
                _SVC_NAMES_EN = {
                    'deepseek': 'AI model (DeepSeek)',
                    'ddg': 'web search (DuckDuckGo)',
                    'openweathermap': 'weather service',
                    'resend': 'email delivery',
                    'telegram': 'Telegram',
                    'discord': 'Discord',
                    'yookassa': 'payments (YooKassa)',
                }
                svcs_raw = data.get('services', [])
                if not svcs_raw:
                    # fallback: parse from source like 'service_health:deepseek'
                    _src_parts = (a.source or '').split(':', 1)
                    svcs_raw = [_src_parts[1]] if len(_src_parts) > 1 else ['сервис']
                if user_lang == 'en':
                    svc_names = [_SVC_NAMES_EN.get(s, s) for s in svcs_raw]
                    svc_str = ', '.join(svc_names)
                    parts.append(f"Heads up: {svc_str} is temporarily down. We're on it, should be back shortly.")
                else:
                    svc_names = [_SVC_NAMES.get(s, s) for s in svcs_raw]
                    svc_str = ', '.join(svc_names)
                    parts.append(f"Кстати, {svc_str} сейчас временно недоступен. Обычно восстанавливается быстро, следим.")
            else:
                # Другие ALWAYS_DELIVER типы — берём topic из якоря
                topic = getattr(a, 'topic', '') or ''
                if topic:
                    parts.append(topic[:300])

        return '\n'.join(parts) if parts else None

    async def _ai_decide_and_compose(self, user, anchors: list, session, force_deliver: bool = False) -> str | None:
        """AI получает все якоря + контекст и РЕШАЕТ: писать или нет + ЧТО писать.
        
        Никаких шаблонов. AI думает на основе полных данных.
        
        Args:
            force_deliver: Если True — якоря являются ALWAYS_DELIVER_TYPES, SKIP запрещён.
        Returns:
            str — текст сообщения, или None если AI решил не писать.
        """
        try:
            # Собираем контекст
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()

            user_tz = pytz.timezone(user.timezone or 'Europe/Moscow')
            user_now = datetime.now(user_tz)

            # Якоря для AI
            anchor_descriptions = []
            for a in anchors:
                anchor_descriptions.append(a.to_ai_context())

            # Задачи
            tasks = session.query(Task).filter(
                Task.user_id == user.id,
                Task.status.in_(['pending', 'in_progress', 'active'])
            ).order_by(Task.reminder_time.asc()).limit(8).all()

            task_lines = []
            for t in tasks:
                time_str = ""
                if t.reminder_time:
                    try:
                        rt = t.reminder_time if t.reminder_time.tzinfo else t.reminder_time.replace(tzinfo=timezone.utc)
                        rt_local = rt.astimezone(user_tz)
                        time_str = f" (→ {rt_local.strftime('%d.%m %H:%M')})"
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)
                desc = f" — {t.description[:80]}" if t.description else ""
                task_lines.append(f"• {t.title}{time_str}{desc}")

            # Завершённые задачи за сегодня — AI должен знать прогресс дня
            user_today_start = user_now.replace(hour=0, minute=0, second=0, microsecond=0)
            today_start_utc = user_today_start.astimezone(pytz.UTC)
            completed_tasks = session.query(Task).filter(
                Task.user_id == user.id,
                Task.status == 'completed',
                Task.actual_completion_time >= today_start_utc
            ).order_by(Task.actual_completion_time.desc()).limit(5).all()

            completed_lines = []
            for ct in completed_tasks:
                ct_time = ""
                if ct.actual_completion_time:
                    try:
                        act = ct.actual_completion_time if ct.actual_completion_time.tzinfo else ct.actual_completion_time.replace(tzinfo=timezone.utc)
                        act_local = act.astimezone(user_tz)
                        ct_time = f" (выполнено {act_local.strftime('%d.%m %H:%M')})"
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)
                completed_lines.append(f"{ct.title}{ct_time}")

            # Пропущенные задачи — AI знает проблемные паттерны
            skipped_tasks = session.query(Task).filter(
                Task.user_id == user.id,
                Task.status == 'skipped'
            ).order_by(Task.created_at.desc()).limit(3).all()

            skipped_lines = []
            for st in skipped_tasks:
                reason = ""
                if st.skipped_reason:
                    try:
                        from ai_integration.memory import decrypt_data
                        reason = f" — {decrypt_data(st.skipped_reason)[:60]}"
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)
                skipped_lines.append(f"{st.title}{reason}")

            # Общая статистика
            total_tasks = session.query(Task).filter(Task.user_id == user.id).count()
            total_completed = session.query(Task).filter(
                Task.user_id == user.id, Task.status == 'completed'
            ).count()
            completion_rate = round(total_completed / total_tasks * 100) if total_tasks > 3 else None

            # Цели
            goals = session.query(Goal).filter(
                Goal.user_id == user.id, Goal.status == 'active'
            ).limit(5).all()
            goal_lines = []
            for g in goals:
                if g.metric_target and g.metric_unit:
                    mc = int(g.metric_current or 0)
                    mt = int(g.metric_target)
                    line = f"• {g.title} ({mc}/{mt} {g.metric_unit}, {g.progress_percentage}%)"
                else:
                    line = f"• {g.title} ({g.progress_percentage}%)"
                if g.target_date:
                    days = g.days_until_target()
                    if days is not None:
                        line += f" дедлайн: {days}дн"
                goal_lines.append(line)

            # Профиль
            profile_lines = []
            if profile:
                if profile.skills: profile_lines.append(f"Навыки: {profile.skills[:80]}")
                if profile.interests: profile_lines.append(f"Интересы: {profile.interests[:80]}")
                if profile.goals: profile_lines.append(f"Цели: {profile.goals[:80]}")
                if profile.position: profile_lines.append(f"Должность: {profile.position}")
                if profile.city: profile_lines.append(f"Город: {profile.city}")

            # Последние сообщения пользователя
            recent_msgs = session.query(Interaction).filter(
                Interaction.user_id == user.id,
                Interaction.message_type == 'user'
            ).order_by(Interaction.created_at.desc()).limit(5).all()

            msg_lines = []
            for m in recent_msgs:
                age = (datetime.now(timezone.utc) - (m.created_at.replace(tzinfo=timezone.utc) if m.created_at.tzinfo is None else m.created_at))
                hours_ago = int(age.total_seconds() / 3600)
                msg_lines.append(f"[{hours_ago}ч назад] {(m.content or '')[:80]}")

            # Статистика доставок (для AI — чтобы знал контекст спама)
            recent_deliveries = session.query(AnchorDeliveryLog).filter(
                AnchorDeliveryLog.user_id == user.id,
                AnchorDeliveryLog.created_at >= datetime.now(timezone.utc) - timedelta(hours=24)
            ).all()

            delivery_stats = f"Сообщений за 24ч: {len(recent_deliveries)}"
            if recent_deliveries:
                last_delivery = max(d.created_at for d in recent_deliveries)
                if last_delivery.tzinfo is None:
                    last_delivery = last_delivery.replace(tzinfo=timezone.utc)
                hours_since_last = (datetime.now(timezone.utc) - last_delivery).total_seconds() / 3600
                delivery_stats += f", последнее {int(hours_since_last)}ч назад"

            # Собираем промпт для AI
            # Баланс токенов — AI знает контекст пользователя
            from token_service import get_balance
            token_balance = get_balance(user.telegram_id)

            # Динамические правила — только для типов якорей которые РЕАЛЬНО присутствуют
            _current_types = {a.anchor_type for a in anchors}
            _TASK_T    = {'task_reminder', 'task_overdue', 'task_deadline_soon', 'task_stale'}
            _INSIGHT_T = {'event_discovery', 'market_insight', 'content_opportunity'}
            _EMAIL_T   = {'email_outreach_send', 'email_follow_up', 'email_reply_received',
                          'email_campaign_report', 'email_need_leads'}
            _INTEG_T   = {'integration_alert', 'agent_office_update', 'agent_inbox_reply',
                          'agent_task_blocked', 'agent_delegation'}
            _CONTACT_T = {'contact_match', 'contact_activity'}

            _rules: list[str] = []

            if _current_types & _TASK_T:
                _rules += [
                    "ПРАВИЛА ДЛЯ ЗАДАЧ:",
                    "— task_reminder: напоминание по расписанию. ЗАПРЕЩЕНО писать 'просрочено/просрочены/опоздание'. Задержка до 30 мин = шаг сканирования. Пиши: 'Пора: [задача]', уточни готовность.",
                    "— task_overdue: задача просрочена (>30 мин после дедлайна). ТОЛЬКО тут уместно говорить о просрочке.",
                    "— task_deadline_soon: дедлайн ещё не наступил, но приближается.",
                    "",
                ]
            if _current_types & _INSIGHT_T:
                _rules += [
                    "ПРАВИЛА ДЛЯ ИНСАЙТОВ:",
                    "— event_discovery: data→web_events — РЕАЛЬНЫЕ мероприятия. 1-2 самых релевантных со ссылками.",
                    "— market_insight: data→fresh_insights — новости/тренды. Перескажи самое важное с ссылками.",
                    "— content_opportunity: data→content_ideas_from_web. Предложи 1-2 темы.",
                    "",
                ]
            if _current_types & _EMAIL_T:
                _rules += [
                    "ПРАВИЛА ДЛЯ EMAIL:",
                    "— email_outreach_send: отправь drafts через send_outreach_email. Персонализируй каждое письмо. Верни SKIP. ANTI-SPAM: пропускай контакты которым уже писали за 30 дней.",
                    "— email_follow_up: follow-up через send_outreach_email. Ненавязчиво. Верни SKIP.",
                    "— email_reply_received: КРИТИЧНО! Прочитай цепочку original_body→reply_text→ai_previous_reply. НЕ повторяй уже заданные вопросы. Ответь через reply_to_outreach_email. ОБЯЗАТЕЛЬНО уведоми пользователя. Не завершай кампанию без явного подтверждения целевого действия.",
                    "— email_campaign_report: краткая сводка: отправлено, ответов, что дальше.",
                    "— email_need_leads: engine ищет лидов автоматически. Верни SKIP.",
                    "",
                ]
            if _current_types & _INTEG_T:
                _rules += [
                    "ПРАВИЛА ДЛЯ ИНТЕГРАЦИЙ:",
                    "— integration_alert: прочитай snippet, реши ценность. CRITICAL/HIGH=пиши, MEDIUM=пиши если конкретное событие, LOW=SKIP если рутина.",
                    "— agent_office_update: план агента по целям, спроси 'Запустить?'",
                    "— agent_inbox_reply: КРИТИЧНО — агент нашёл новые письма. Покажи preview, спроси про ответ.",
                    "— agent_task_blocked: КРИТИЧНО — агент застрял. Объясни причину, задай конкретный вопрос.",
                    "— agent_delegation: отчёт от имени агента (от первого лица): что сделано, каков итог.",
                    "",
                ]
            if _current_types & _CONTACT_T:
                _rules += [
                    "ПРАВИЛА ДЛЯ КОНТАКТОВ:",
                    "— contact_match: нашёлся @username. Объясни почему полезен, предложи написать.",
                    "— contact_activity: выбери 1-2 релевантных контакта, объясни пересечение с целями.",
                    "",
                ]

            _skip_rule = ("SKIP ЗАПРЕЩЁН — якоря ALWAYS_DELIVER, обязательно написать."
                          if force_deliver else
                          "Слабые якоря → SKIP. Лучше промолчать, чем отправить воду.")
            _rules += [
                f"ОБЩИЕ ПРАВИЛА: {_skip_rule}",
                "— Одна тема на сообщение. НЕ начинай с приветствий.",
                "— Сначала данные (инструменты), потом выводы. Персонализируй.",
                "— Закончи вопросом или конкретным действием. Не создавай задачи без просьбы.",
                "— ДЕДУПЛИКАЦИЯ: если агент уже выполнил действие по якорю — SKIP.",
            ]

            prompt_parts = [
                "Ты — AnchorEngine, мозг автономного агента ASI Biont.",
                "Ниже — сработавшие ЯКОРЯ + контекст. РЕШИ: писать или SKIP.",
                "",
                f"БАЛАНС: {token_balance} токенов (малый = только критичное).",
                "",
            ] + _rules + [
                "",
                f"=== ВРЕМЯ ===",
                f"{user_now.strftime('%H:%M %d.%m.%Y')} ({user.timezone or 'Europe/Moscow'})",
                f"Баланс: {token_balance} токенов",
                f"{delivery_stats}",
            ]

            prompt_parts.append(f"\n=== ЯКОРЯ ({len(anchors)} шт) ===")
            # Типы якорей, для которых нужно передавать полные данные (data) в промпт
            EMAIL_DATA_TYPES = {'email_reply_received', 'email_outreach_send', 'email_follow_up', 'email_campaign_report', 'email_need_leads'}
            # Типы якорей с DDG-обогащением — показываем веб-данные
            DDG_ENRICHED_TYPES = {'event_discovery', 'market_insight', 'content_opportunity'}
            for i, ad in enumerate(anchor_descriptions, 1):
                prompt_parts.append(
                    f"{i}. [{ad['priority']}] {ad['type']}: {ad['topic']} "
                    f"(источник: {ad['source']}, возраст: {ad['age_minutes']}мин)"
                )
                # Для integration_alert и agent_office_update передаём данные в промпт
                INTEGRATION_DATA_TYPES = {'integration_alert', 'agent_office_update'}
                if ad.get('type') in INTEGRATION_DATA_TYPES and ad.get('data'):
                    if ad.get('type') == 'agent_office_update':
                        _plan = ad['data'].get('plan', '')
                        _ac = ad['data'].get('agent_count', '')
                        _gc = ad['data'].get('goal_count', '')
                        if _plan:
                            prompt_parts.append(
                                f"   Офисный план ({_gc} целей, {_ac} агентов): {_plan}"
                            )
                    else:
                        _sn = ad['data'].get('snippet', '')
                        _sl = ad['data'].get('service_label', '')
                        _sig = ad['data'].get('signal', '')
                        if _sn or _sl:
                            prompt_parts.append(
                                f"   Данные [{_sl}]: {_sn[:400]}"
                                + (f" (сигнал: {_sig})" if _sig else '')
                            )
                # Для контактных якорей передаём данные о контактах
                CONTACT_DATA_TYPES = {'contact_match', 'contact_activity', 'agent_delegation'}
                if ad.get('type') in CONTACT_DATA_TYPES and ad.get('data'):
                    d = ad['data']
                    if ad['type'] == 'contact_match':
                        prompt_parts.append(f"   @{d.get('username','')}: навык={d.get('skill','')}, интерес={d.get('interest','')}, город={d.get('city','')}, должность={d.get('position','')}")
                    elif ad['type'] == 'contact_activity':
                        contacts = d.get('contacts', [])
                        up = d.get('user_profile', {})
                        prompt_parts.append(f"   Город: {d.get('city','')}, профиль: {up.get('skills','')} / {up.get('interests','')}")
                        for c in contacts[:3]:
                            acts = ', '.join(c.get('activities', [])[:3])
                            prompt_parts.append(f"   • @{c.get('username','')}: {c.get('position','')} | {c.get('skills','')} | активности: {acts}")
                    elif ad['type'] == 'agent_delegation':
                        prompt_parts.append(f"   Агент: {d.get('agent_name','')}, задача: {d.get('task','')}")
                        _res = str(d.get('result', ''))[:300]
                        if _res:
                            prompt_parts.append(f"   Результат: {_res}")
                # Для agent_inbox_reply и agent_task_blocked передаём preview/reason
                OFFICE_DATA_TYPES = {'agent_inbox_reply', 'agent_task_blocked'}
                if ad.get('type') in OFFICE_DATA_TYPES and ad.get('data'):
                    d = ad['data']
                    if ad['type'] == 'agent_inbox_reply':
                        _pv = d.get('preview', '')
                        _rc = d.get('reply_count', '')
                        if _pv:
                            prompt_parts.append(f"   Агент: {d.get('agent_name','')}, писем: {_rc}")
                            prompt_parts.append(f"   Preview: {_pv[:200]}")
                    elif ad['type'] == 'agent_task_blocked':
                        _reason = d.get('reason', '')
                        _ctx = d.get('full_context', '')
                        if _reason:
                            prompt_parts.append(f"   Агент: {d.get('agent_name','')}")
                            prompt_parts.append(f"   Причина: {_reason[:200]}")
                            if _ctx and len(_ctx) > len(_reason):
                                prompt_parts.append(f"   Контекст: {_ctx[:300]}")
            # Для email-якорей передаём полные данные — AI нужны outreach_id, reply_text, campaign_goal
                if ad.get('type') in EMAIL_DATA_TYPES and ad.get('data'):
                    data = ad['data']
                    data_lines = []
                    for key in ('campaign_id', 'campaign_name', 'campaign_goal', 'outreach_id',
                                'recipient_email', 'recipient_name', 'recipient_company',
                                'original_subject', 'original_body', 'reply_text',
                                'ai_previous_reply', 'offer', 'tone', 'sender_name', 'sender_email',
                                'drafts', 'remaining_daily', 'remaining_total',
                                'follow_up_number', 'days_since_sent'):
                        if key in data and data[key] is not None:
                            val = data[key]
                            if isinstance(val, str) and len(val) > 500:
                                val = val[:500] + '...'
                            data_lines.append(f"   {key}: {val}")
                    if data_lines:
                        prompt_parts.append("   --- DATA ---")
                        prompt_parts.extend(data_lines)

                # Для DDG-обогащённых якорей — показываем реальные результаты веб-поиска
                if ad.get('type') in DDG_ENRICHED_TYPES and ad.get('data'):
                    data = ad['data']
                    web_keys = {'web_events': ' МЕРОПРИЯТИЯ ИЗ СЕТИ', 'fresh_insights': ' СВЕЖИЕ ДАННЫЕ ИЗ СЕТИ', 'content_ideas_from_web': ' ИДЕИ ИЗ СЕТИ'}
                    for web_key, label in web_keys.items():
                        items = data.get(web_key, [])
                        if items:
                            prompt_parts.append(f"   --- {label} ---")
                            for item in items[:5]:
                                title = item.get('title', '')
                                snippet = item.get('snippet', '')[:150]
                                url = item.get('url', '')
                                prompt_parts.append(f"   • {title}")
                                if snippet:
                                    prompt_parts.append(f"     {snippet}")
                                if url:
                                    prompt_parts.append(f"     {url}")

            if task_lines:
                prompt_parts.append(f"\n=== АКТИВНЫЕ ЗАДАЧИ ({len(tasks)}) ===")
                prompt_parts.extend(task_lines)

            if completed_lines:
                prompt_parts.append(f"\n=== НЕДАВНО ЗАВЕРШЕНО ({len(completed_tasks)}) ===")
                prompt_parts.extend(completed_lines)

            if skipped_lines:
                prompt_parts.append(f"\n=== ПРОПУЩЕНО ===")
                prompt_parts.extend(skipped_lines)

            if completion_rate is not None:
                prompt_parts.append(f"\nВыполненность задач: {completion_rate}% ({total_completed}/{total_tasks})")

            if goal_lines:
                prompt_parts.append(f"\n=== ЦЕЛИ ===")
                prompt_parts.extend(goal_lines)

            if profile_lines:
                prompt_parts.append(f"\n=== ПРОФИЛЬ ===")
                prompt_parts.extend(profile_lines)

            if msg_lines:
                prompt_parts.append(f"\n=== ПОСЛЕДНИЕ СООБЩЕНИЯ ===")
                prompt_parts.extend(msg_lines)

            # Недавние действия агентов/суб-агентов: ВСЕ типы (email, delegation, agent_task, inbox_reply, post_*, ...)
            # AI должен знать что уже было сделано, чтобы не предлагать повторно
            try:
                _recent_actions = session.query(AgentActivityLog).filter(
                    AgentActivityLog.user_id == user.id,
                    AgentActivityLog.created_at >= datetime.now(timezone.utc) - timedelta(hours=6),
                ).order_by(AgentActivityLog.created_at.desc()).limit(8).all()
                if _recent_actions:
                    _act_lines = []
                    for _ra in _recent_actions:
                        _ts = _ra.created_at.strftime('%H:%M') if _ra.created_at else ''
                        _line = f"• [{_ts}] {_ra.activity_type}: {_ra.title[:80]} — {_ra.status}"
                        if _ra.target:
                            _line += f" → {_ra.target[:60]}"
                        if _ra.result:
                            _line += f" | {_ra.result[:60]}"
                        _act_lines.append(_line)
                    prompt_parts.append(
                        f"\n=== НЕДАВНИЕ ДЕЙСТВИЯ АГЕНТОВ ({len(_recent_actions)} шт, уже выполнено!) ==="
                    )
                    if force_deliver:
                        # force_deliver = якорь помечен ALWAYS_DELIVER (agent_delegation, service_degraded и т.д.)
                        # agent_delegation: push не отправляется, но сообщение сохраняется в историю чата.
                        # SKIP запрещён — нужно сформировать текст отчёта для истории.
                        prompt_parts.append(
                            "ВАЖНО: Это отчёт о результатах работы агентов — пользователь ЖДЁТ этой информации. "
                            "Ты ОБЯЗАН написать краткий отчёт о том, что агент СДЕЛАЛ (см. якорь). "
                            "SKIP на этом этапе ЗАПРЕЩЁН."
                        )
                    else:
                        prompt_parts.append(
                            "ВАЖНО: Если якорь описывает событие, по которому агент УЖЕ выполнил действие "
                            "(отправил email, ответил, делегировал, создал пост, выполнил задачу) — "
                            "НЕ предлагай пользователю сделать то же самое. Верни SKIP."
                        )
                    prompt_parts.extend(_act_lines)
            except Exception as _e:
                logger.debug("suppressed: %s", _e)

            full_prompt = "\n".join(prompt_parts)

            # Вызываем AI через агента (с tool calling — может использовать research_topic, etc.)
            from ai_integration.autonomous_agent import get_autonomous_agent
            agent = get_autonomous_agent()

            logger.info(f"[ANCHOR] AI call for user {user.telegram_id}: {len(anchors)} anchors, prompt {len(full_prompt)} chars")

            # Для якорей-напоминаний используем mode='reminder' (без forced tool calling)
            # — быстрее и надёжнее чем anchor mode с обязательным web-search
            # service_degraded тоже в reminder mode: сообщение шаблонное, инструменты не нужны
            _reminder_only_types = {'task_reminder', 'task_overdue', 'task_deadline_soon', 'service_degraded'}
            _agent_office_types = {'agent_office_update', 'agent_inbox_reply', 'agent_task_blocked'}
            _anchor_types_set = {a.anchor_type for a in anchors}
            if _anchor_types_set <= _agent_office_types:
                _ai_mode = 'reminder'
                _ai_instruction = (
                    "Напиши КРАТКИЙ статус от имени агента (2-3 предложения, до 300 символов). "
                    "Только ФАКТЫ: что сделано, что найдено, какой результат. "
                    "ЗАПРЕЩЕНО: вопросы пользователю, аналитика рынка, списки, предложения стратегий. "
                    "ЗАПРЕЩЕНО: markdown, маркеры (•, -, *). "
                    "Стиль: короткий отчёт в мессенджере."
                )
                _ai_max_iter = 1
            elif _anchor_types_set <= _reminder_only_types:
                _ai_mode = 'reminder'
                # Проверяем: нет ли в последних сообщениях пользователя сигнала выполнения
                _recent_user_texts = ' '.join(
                    (m.content or '').lower() for m in recent_msgs[:3]
                )
                _completion_signals = (
                    'я сделал', 'я заказал', 'я купил', 'я оплатил', 'я позвонил',
                    'я написал', 'я отправил', 'я настроил', 'я прошёл', 'я записался',
                    'уже сделал', 'уже заказал', 'уже купил', 'уже оплатил',
                    'уже готово', 'уже выполнил', 'уже сделано', 'готово', 'выполнено',
                )
                _user_says_done = any(sig in _recent_user_texts for sig in _completion_signals)
                if _user_says_done:
                    _ai_instruction = (
                        "ВАЖНО: Пользователь уже сообщил о выполнении задачи (см. ПОСЛЕДНИЕ СООБЩЕНИЯ). "
                        "Вызови complete_task для этой задачи и коротко подтверди (1 предложение). "
                        "НЕ спрашивай повторно — это лишнее."
                    )
                    _ai_max_iter = 2  # нужен tool call
                else:
                    _ai_instruction = (
                        "Напиши напоминание о задаче на основе контекста ниже. "
                        "Стиль: живой, как друг в мессенджере. Кратко, 1-3 предложения. "
                        "Спроси готовность. НЕ создавай новые задачи. "
                        "Если задача выглядит выполненной или неактуальной — предложи закрыть."
                    )
                    _ai_max_iter = 1
            else:
                _ai_mode = 'anchor'
                _ai_instruction = "Подумай о ситуации этого человека. Вызови инструменты по релевантным темам из якорей — research_topic или get_news_trends. На основе реальных данных реши: стоит ли писать (или SKIP). Если пишешь — покажи что нашёл и задай вопрос, который двигает вперёд."
                _ai_max_iter = 2

            result = await agent.generate_system_message(
                user_id=user.telegram_id,
                mode=_ai_mode,
                instruction=_ai_instruction,
                extra_context=full_prompt,
                max_tokens=600,
                max_iterations=_ai_max_iter
            )

            logger.info(f"[ANCHOR] AI result for user {user.telegram_id}: {'SKIP/None' if not result else result[:100]}")

            if not result or result.strip().upper() == 'SKIP':
                return None

            # Убираем "SKIP" если AI начал писать но потом решил не стоит
            if result.strip().upper().startswith('SKIP'):
                return None

            return result.strip()

        except Exception as e:
            logger.error(f"[ANCHOR] AI decision error: {e}\n{traceback.format_exc()}")
            return None

    # ═══════════════════════════════════════════════════════
    # DELIVER
    # ═══════════════════════════════════════════════════════

    async def _deliver(self, user, anchors: list, message: str, session):
        """Отправляет сообщение и записывает лог. Списывает токены."""
        try:
            now_utc = datetime.now(timezone.utc)

            # ── ЗАЩИТА ОТ ДУБЛЕЙ (race condition при деплое / 2 инстанса) ──
            # Перечитываем якоря из БД — может другой процесс уже доставил
            still_pending = []
            for anchor in anchors:
                fresh = session.query(Anchor).filter_by(id=anchor.id).with_for_update(skip_locked=True).first()
                if fresh and fresh.delivered_at is None:
                    still_pending.append(fresh)
            if not still_pending:
                logger.info(f"[ANCHOR] User {user.telegram_id}: all anchors already delivered by another process, skip")
                return
            anchors = still_pending

            # ── CROSS-PROCESS DUPLICATE GUARD ──
            # Два инстанса могут создать разные DB-строки для одного логического якоря.
            # Если те же anchor_types уже доставлялись в последние 2 мин — это дубль.
            # НО: ALWAYS_DELIVER_TYPES исключаем из проверки — у них row-level lock выше достаточен.
            current_types = set(a.anchor_type for a in anchors)
            _check_types = current_types - ALWAYS_DELIVER_TYPES  # only non-critical for dupe check
            very_recent_logs = session.query(AnchorDeliveryLog).filter(
                AnchorDeliveryLog.user_id == user.id,
                AnchorDeliveryLog.created_at >= now_utc - timedelta(minutes=2)
            ).all()
            if _check_types:
                for log in very_recent_logs:
                    try:
                        logged_types = set(json.loads(log.anchor_types) if log.anchor_types else [])
                    except Exception:
                        logged_types = set()
                    overlap = _check_types & logged_types
                    if overlap:
                        logger.info(f"[ANCHOR] User {user.telegram_id}: cross-process duplicate detected (types: {overlap}), marking and skip")
                        for anchor in anchors:
                            anchor.delivered_at = now_utc
                        try:
                            session.commit()
                        except Exception:
                            session.rollback()
                        return
            # Gap check — но ALWAYS_DELIVER якоря (агенты, критические) обходят gap
            _anchor_types_here = {a.anchor_type for a in anchors}
            _has_always_deliver = bool(_anchor_types_here & ALWAYS_DELIVER_TYPES)
            if not _has_always_deliver:
                # NOTE: учитываем только ДИАЛОГОВЫЕ доставки (не silent email/content/delegation).
                # email_need_leads, email_outreach_send и прочие silent запускаются каждые 7-10 мин
                # и НЕ должны блокировать доставку проактивных диалоговых якорей.
                _DIALOG_GAP_EXCLUDE = {
                    'email_outreach_send', 'email_follow_up', 'email_need_leads',
                    'content_campaign_publish',
                    'delegation_campaign_send', 'delegation_campaign_follow_up',
                }
                recent_dialog_delivery = None
                recent_logs = session.query(AnchorDeliveryLog).filter(
                    AnchorDeliveryLog.user_id == user.id,
                    AnchorDeliveryLog.created_at >= now_utc - timedelta(minutes=MIN_PROACTIVE_GAP_MINUTES)
                ).all()
                for _rdl in recent_logs:
                    try:
                        _rdl_types = set(json.loads(_rdl.anchor_types) if _rdl.anchor_types else [])
                    except Exception:
                        _rdl_types = set()
                    # Только если лог содержит НЕ-silent типы — считаем gap
                    if _rdl_types - _DIALOG_GAP_EXCLUDE:
                        recent_dialog_delivery = _rdl
                        break
                # Дополнительная защита: проверяем interactions таблицу (ловит agent_chain_transfer и
                # случаи когда AnchorDeliveryLog ещё не закоммитился в рамках того же цикла)
                if not recent_dialog_delivery:
                    _recent_proactive_ts = session.query(Interaction.created_at).filter(
                        Interaction.user_id == user.id,
                        Interaction.message_type == 'proactive',
                        Interaction.created_at >= now_utc - timedelta(minutes=5),
                    ).order_by(Interaction.created_at.desc()).limit(1).scalar()
                    if _recent_proactive_ts:
                        logger.info(f"[ANCHOR] User {user.telegram_id}: recent proactive in interactions (< 5min), skip")
                        return
                if recent_dialog_delivery:
                    logger.info(f"[ANCHOR] User {user.telegram_id}: delivery gap too small ({MIN_PROACTIVE_GAP_MINUTES}min), skip")
                    return

            # Проверяем и списываем токены за проактивное сообщение (в той же сессии для атомарности)
            from token_service import spend_tokens, has_enough_tokens
            from config import FREE_ACCESS_MODE
            if not FREE_ACCESS_MODE:
                if not has_enough_tokens(user.telegram_id, 'proactive_message', session=session):
                    # Критические якоря доставляем бесплатно (agent_delegation и т.д.)
                    if not _has_always_deliver:
                        logger.info(f"[ANCHOR] User {user.telegram_id}: пропуск доставки — нет токенов")
                        return
                    logger.info(f"[ANCHOR] User {user.telegram_id}: ALWAYS_DELIVER — доставка без токенов")
                else:
                    spend_tokens(user.telegram_id, 'proactive_message', description='proactive anchor', session=session, auto_commit=False)

            # Помечаем якоря как доставленные
            anchor_ids = []
            anchor_types = []
            for anchor in anchors:
                anchor.delivered_at = now_utc
                anchor_ids.append(anchor.id)
                anchor_types.append(anchor.anchor_type)
                # Для task_reminder: ставим reminder_sent=True ЗДЕСЬ (при реальной доставке)
                if anchor.anchor_type in ('task_reminder', 'task_overdue') and anchor.source and anchor.source.startswith('task:'):
                    try:
                        tid = int(anchor.source.split(':')[1])
                        src_task = session.query(Task).filter_by(id=tid).first()
                        if src_task and not src_task.reminder_sent:
                            src_task.reminder_sent = True
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)

            # Определяем, связаны ли якоря с конкретным агентом
            # Проверяем ЛЮБОЙ якорь с agent_name/agent в data (не только AGENT_ANCHOR_TYPES)
            AGENT_ANCHOR_TYPES = {'agent_inbox_reply', 'agent_task_blocked', 'agent_office_update', 'integration_alert'}
            _agent_name = None
            for anchor in anchors:
                if anchor.data:
                    try:
                        _ad = json.loads(anchor.data) if isinstance(anchor.data, str) else anchor.data
                        _candidate = _ad.get('agent_name') or _ad.get('agent')
                        # Для AGENT_ANCHOR_TYPES берём любое имя; для остальных — только явное agent_name
                        if _candidate and (anchor.anchor_type in AGENT_ANCHOR_TYPES or _ad.get('agent_name')):
                            _agent_name = _candidate
                            break
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)

            # Определяем anchor_type для метаданных — первый непустой тип из якорей
            _deliver_anchor_type = anchor_types[0] if anchor_types else ''

            # Оборачиваем контент в __agent JSON, если есть агент
            interaction_content = message
            if _agent_name:
                try:
                    from models import UserAgent
                    _ua = session.query(UserAgent).filter(
                        UserAgent.author_id == user.id,
                        UserAgent.name == _agent_name,
                    ).first()
                    if _ua:
                        interaction_content = json.dumps({
                            '__agent': {
                                'name': _ua.name,
                                'id': _ua.id,
                                'avatar_url': _safe_avatar(_ua.avatar_url, _ua.id),
                            },
                            'text': message,
                            '__anchor_type': _deliver_anchor_type,
                        }, ensure_ascii=False)
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)

            # Создаём запись в interactions
            # Если нет конкретного агента — атрибутируем как ASI (проактивные сообщения системы)
            if not interaction_content.strip().startswith('{'):
                interaction_content = json.dumps({
                    '__agent': {'name': 'ASI', 'id': 0, 'avatar_url': ''},
                    'text': interaction_content,
                    '__anchor_type': _deliver_anchor_type,
                }, ensure_ascii=False)
            interaction = Interaction(
                user_id=user.id,
                message_type='proactive',
                content=interaction_content
            )
            session.add(interaction)

            # Создаём delivery log
            log = AnchorDeliveryLog(
                user_id=user.id,
                anchor_ids=json.dumps(anchor_ids),
                message_text=message,
                anchor_types=json.dumps(anchor_types),
            )
            session.add(log)

            # Отправляем через бот ПЕРЕД commit — если отправка не удалась, откатываем
            if self.bot:
                try:
                    # Гарантируем кликабельность URL через HTML parse_mode
                    import html as html_mod
                    url_re = re.compile(r'(https?://\S+)')
                    spaced = re.sub(r'(?<=[^\s\n])(https?://)', r' \1', message)
                    parts = url_re.split(spaced)
                    html_parts = []
                    for idx, part in enumerate(parts):
                        if idx % 2 == 0:
                            html_parts.append(html_mod.escape(part))
                        else:
                            clean = part.rstrip('.,;:!?)—»')
                            trailing = part[len(clean):]
                            html_parts.append(f'<a href="{html_mod.escape(clean)}">{html_mod.escape(clean)}</a>{html_mod.escape(trailing)}')
                    send_html = ''.join(html_parts)
                    try:
                        await self.bot.send_message(
                            chat_id=user.telegram_id,
                            text=send_html,
                            parse_mode='HTML'
                        )
                    except Exception:
                        # Fallback без HTML
                        await self.bot.send_message(
                            chat_id=user.telegram_id,
                            text=message
                        )
                    # Синхронизация: сохраняем в историю чата
                    try:
                        from ai_integration.conversation_history import save_message_to_history as _smh
                        _smh(user.telegram_id, 'assistant', message, session=session)
                    except Exception: pass
                    session.commit()
                    logger.info(f"[ANCHOR] ✅ Delivered to {user.telegram_id}: {message[:80]}...")
                except Exception as send_err:
                    _send_err_str = str(send_err).lower()
                    logger.error(f"[ANCHOR] Send failed to {user.telegram_id}: {send_err}")
                    session.rollback()
                    # ── Если бот заблокирован или чат не найден — ставим DND на 7 дней ──
                    _is_blocked = ('forbidden' in _send_err_str and 'blocked' in _send_err_str) or \
                                  'chat not found' in _send_err_str or \
                                  'user is deactivated' in _send_err_str
                    if _is_blocked:
                        try:
                            _blk_sess = Session()
                            try:
                                _blk_sess.query(User).filter_by(telegram_id=user.telegram_id).update(
                                    {'do_not_disturb_until': datetime.now(timezone.utc) + timedelta(days=7)},
                                    synchronize_session=False,
                                )
                                _blk_sess.commit()
                                logger.info(f"[ANCHOR] User {user.telegram_id} blocked bot → DND set for 7 days")
                            finally:
                                _blk_sess.close()
                        except Exception as _blk_err:
                            logger.warning(f"[ANCHOR] Failed to set DND for blocked user {user.telegram_id}: {_blk_err}")
                    # ── ANTI-DRAIN: пометить якоря delivered в отдельной сессии ──
                    # Если send_message провалился — anchor остаётся undelivered и
                    # цикл повторяется каждые 5 мин, тратя токены на AI-вызов.
                    # Помечаем якоря (созданные >5 мин назад) как suppressed,
                    # чтобы engine не вызывал AI снова по тем же якорям.
                    # Для ALWAYS_DELIVER якорей anchor-creation создаст новый якорь
                    # при следующем scan если условие ещё актуально.
                    try:
                        _now_sup = datetime.now(timezone.utc)
                        _stale_threshold = timedelta(minutes=5)
                        _stale_ids = [
                            a.id for a in anchors
                            if (_now_sup - (a.created_at.replace(tzinfo=timezone.utc) if a.created_at and a.created_at.tzinfo is None else (a.created_at or _now_sup))).total_seconds() > _stale_threshold.total_seconds()
                        ]
                        if _stale_ids:
                            _sup_sess = Session()
                            _sup_sess.query(Anchor).filter(Anchor.id.in_(_stale_ids)).update(
                                {'delivered_at': _now_sup}, synchronize_session=False
                            )
                            _sup_sess.commit()
                            _sup_sess.close()
                            logger.warning(f"[ANCHOR] ⚠️ Suppressed {len(_stale_ids)} anchors after send_message failure to {user.telegram_id} — они будут пересозданы при следующем scan если условие актуально")
                    except Exception as _sup_err:
                        logger.error(f"[ANCHOR] Failed to suppress anchors after send failure: {_sup_err}")
            else:
                session.commit()
                logger.info(f"[ANCHOR] Message (no bot): {message[:80]}...")

        except Exception as e:
            logger.error(f"[ANCHOR] Deliver error: {e}")
            session.rollback()

    # ═══════════════════════════════════════════════════════
    # FEEDBACK — отслеживание реакций
    # ═══════════════════════════════════════════════════════

    async def record_user_response(self, user_id: int):
        """Вызывается когда пользователь отвечает — помечает последнюю доставку как responded.
        
        Интегрируется в основной обработчик сообщений.
        """
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return

            now_utc = datetime.now(timezone.utc)

            # Находим последнюю доставку за последний час
            recent_log = session.query(AnchorDeliveryLog).filter(
                AnchorDeliveryLog.user_id == user.id,
                AnchorDeliveryLog.created_at >= now_utc - timedelta(hours=1),
                AnchorDeliveryLog.user_responded.is_(None)
            ).order_by(AnchorDeliveryLog.created_at.desc()).first()

            if recent_log:
                recent_log.user_responded = True
                response_time = (now_utc - recent_log.created_at.replace(tzinfo=timezone.utc)).total_seconds()
                recent_log.response_time_seconds = int(response_time)

                # Помечаем якоря как responded
                try:
                    ids = json.loads(recent_log.anchor_ids)
                    if ids:
                        # Batch-load anchors (avoid N+1)
                        _resp_anchors = session.query(Anchor).filter(Anchor.id.in_(ids)).all()
                        _resp_anchor_map = {a.id: a for a in _resp_anchors}
                        for aid in ids:
                            anchor = _resp_anchor_map.get(aid)
                            if anchor:
                                anchor.user_reaction = 'responded'
                                anchor.reaction_at = now_utc
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)

                session.commit()
                logger.debug(f"[ANCHOR] Recorded response from {user_id} ({int(response_time)}s)")

        except Exception as e:
            logger.error(f"[ANCHOR] Record response error: {e}")
            session.rollback()
        finally:
            session.close()

    # ═══════════════════════════════════════════════════════════════════
    # AGENT CHAT HOOKS — агенты участвуют в обычном чате пользователя
    # ═══════════════════════════════════════════════════════════════════

    async def trigger_chat_hook(self, user_id: int, user_message: str, ai_response: str):
        """После ответа главного AI проверяет, хочет ли агент добавить своё наблюдение.

        Логика:
        1. Выбирается один наиболее релевантный подписанный агент.
        2. Агент с python_code (реальными интеграциями) получает приоритет.
        3. Cooldown 15 минут на агента — чтобы не спамить.
        4. Агент запускается асинхронно (не блокирует основной ответ).
        """
        try:
            from models import Session as _Db, User as _User, UserAgent as _UA
            from models import AgentSubscription as _AS, AgentActivityLog as _AAL

            session = _Db()
            try:
                user = session.query(_User).filter_by(telegram_id=user_id).first()
                if not user:
                    return
                sub_ids = {r.agent_id for r in session.query(_AS).filter_by(user_id=user.id).all()}
                if not sub_ids:
                    return
                agents = session.query(_UA).filter(
                    _UA.id.in_(sub_ids),
                    _UA.status != 'disabled',
                ).all()
                if not agents:
                    return

                now_utc = datetime.now(timezone.utc)
                msg_lower = (user_message or '').lower()
                resp_lower = (ai_response or '').lower()

                best_agent = None
                best_score = -1

                for agent in agents:
                    # Cooldown: агент участвует в чате не чаще 1 раза в 15 минут
                    _last = session.query(_AAL).filter(
                        _AAL.user_id == user.id,
                        _AAL.activity_type == 'agent_chat_hook',
                        _AAL.ref_id == agent.id,
                        _AAL.created_at >= now_utc - timedelta(minutes=15),
                    ).first()
                    if _last:
                        continue

                    spec = (
                        (agent.specialization or '') + ' '
                        + (agent.job_title or '') + ' '
                        + (agent.description or '')
                    ).lower()
                    spec_words = [w for w in spec.split() if len(w) > 3]
                    score = sum(1 for w in spec_words if w in msg_lower or w in resp_lower)

                    # Агенты с реальными интеграциями (python_code) получают бонус
                    if (agent.python_code or '').strip():
                        score += 2

                    if score > best_score:
                        best_score = score
                        best_agent = agent

                if not best_agent or best_score < 1:
                    return

                best_agent_id = best_agent.id

            finally:
                session.close()

            # Запускаем асинхронно — не блокируем основной ответ
            asyncio.create_task(
                self._run_chat_hook_agent(user_id, best_agent_id, user_message, ai_response)
            )

        except Exception as _e:
            logger.debug('[CHAT_HOOK] trigger error: %s', _e)

    async def _run_chat_hook_agent(
        self,
        user_id: int,
        agent_id: int,
        user_message: str,
        ai_response: str,
    ):
        """Выполняет агента как наблюдателя чата.  Отправляет сообщение только если есть новая ценность."""
        try:
            from models import Session as _Db, User as _User, UserAgent as _UA
            from models import AgentActivityLog as _AAL, Interaction as _Int
            from ai_integration.autonomous_agent import _exec_agent_for_director
            import asyncio as _aio

            # Небольшая задержка — агент «думает» после ответа AI
            await _aio.sleep(3)

            session = _Db()
            try:
                user = session.query(_User).filter_by(telegram_id=user_id).first()
                # Повторно проверяем агента в новой сессии: agent_id должен быть в подписках пользователя
                from models import AgentSubscription as _AS_hook
                _sub_ok = session.query(_AS_hook).filter_by(
                    user_id=user.id if user else -1,
                    agent_id=agent_id,
                ).first() if user else None
                agent = session.query(_UA).filter_by(id=agent_id).first()
                if not user or not agent or not _sub_ok:
                    return

                # Повторная проверка cooldown (гонка)
                now_utc = datetime.now(timezone.utc)
                _dup = session.query(_AAL).filter(
                    _AAL.user_id == user.id,
                    _AAL.activity_type == 'agent_chat_hook',
                    _AAL.ref_id == agent.id,
                    _AAL.created_at >= now_utc - timedelta(minutes=15),
                ).first()
                if _dup:
                    return

                agent_data = {
                    'id': agent.id,
                    'name': agent.name,
                    'job_title': agent.job_title or '',
                    'specialization': agent.specialization or '',
                    'description': agent.description or '',
                    'personality': agent.personality or '',
                    'python_code': agent.python_code or '',
                    'user_api_keys': agent.user_api_keys or '',
                    'tools_allowed': agent.tools_allowed or '',
                    'avatar_url': agent.avatar_url or '',
                }

                task = (
                    "[НАБЛЮДЕНИЕ ЗА ЧАТОМ]\n"
                    f"Пользователь написал: {user_message[:300]}\n"
                    f"AI ответил: {ai_response[:400]}\n\n"
                    f"Ты — {agent.name}"
                    + (f", специализация: {agent.specialization or agent.job_title}" if (agent.specialization or agent.job_title) else "")
                    + ".\n"
                    "Если у тебя есть КОНКРЕТНЫЕ данные из твоих интеграций (контакты, метрики, "
                    "наблюдения), которые ДОБАВЛЯЮТ НОВУЮ ЦЕННОСТЬ к этому разговору и не повторяют "
                    "то, что AI уже сказал — напиши кратко (до 300 символов).\n"
                    "Используй инструменты если нужно (run_agent_action, list_tasks, list_goals…).\n"
                    "Если добавить нечего — ответь ровно одним словом: SKIP"
                )

                _raw = await _exec_agent_for_director(agent_data, task, user.telegram_id)
                result = (_raw[0] if isinstance(_raw, (tuple, list)) else _raw or '').strip()

                # Фильтруем шум
                _noise = {'skip', 'нет', 'no', 'нет данных', 'нет информации', 'нечего добавить'}
                if (
                    not result
                    or result.lower().rstrip('.!') in _noise
                    or result.lower().startswith('skip')
                    or len(result) < 15
                ):
                    return

                # Логируем
                session.add(_AAL(
                    user_id=user.id,
                    activity_type='agent_chat_hook',
                    title=f'{agent.name} — чат',
                    content=result[:400],
                    target='chat',
                    status='completed',
                    ref_id=agent.id,
                ))

                # Отправляем в Telegram
                if self.bot:
                    await self.bot.send_message(
                        chat_id=user.telegram_id,
                        text=f"{agent.name}:\n\n{result}",
                    )
                    _content = json.dumps({
                        '__agent': {
                            'name': agent.name,
                            'id': agent.id,
                            'avatar_url': _safe_avatar(agent.avatar_url, agent.id),
                        },
                        'text': _strip_html(result),
                    }, ensure_ascii=False)
                    session.add(_Int(
                        user_id=user.id,
                        message_type='proactive',
                        content=_content,
                    ))
                    try:
                        from ai_integration.conversation_history import save_message_to_history as _smh_ch
                        _smh_ch(user.telegram_id, 'assistant', _strip_html(result), session=session)
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)

                session.commit()
                logger.info('[CHAT_HOOK] %s contributed to chat for user %d', agent.name, user_id)

            finally:
                session.close()

        except Exception as _e:
            logger.debug('[CHAT_HOOK] agent %d error: %s', agent_id, _e)

    async def mark_ignored_deliveries(self):
        """Периодическая задача: помечает доставки старше 1ч без ответа как ignored"""
        session = Session()
        try:
            now_utc = datetime.now(timezone.utc)
            cutoff = now_utc - timedelta(hours=1)

            unresolved = session.query(AnchorDeliveryLog).filter(
                AnchorDeliveryLog.user_responded.is_(None),
                AnchorDeliveryLog.created_at < cutoff
            ).all()

            # Batch-load all anchor IDs across all unresolved logs (avoid N+1)
            _all_aids: set = set()
            _log_ids_map: dict = {}
            for log in unresolved:
                log.user_responded = False
                try:
                    ids = json.loads(log.anchor_ids)
                    _log_ids_map[log.id] = ids
                    _all_aids.update(ids)
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)

            _ignored_anchor_map = {}
            if _all_aids:
                _ignored_anchors = session.query(Anchor).filter(Anchor.id.in_(list(_all_aids))).all()
                _ignored_anchor_map = {a.id: a for a in _ignored_anchors}

            for log in unresolved:
                try:
                    for aid in _log_ids_map.get(log.id, []):
                        anchor = _ignored_anchor_map.get(aid)
                        if anchor and not anchor.user_reaction:
                            anchor.user_reaction = 'ignored'
                            anchor.reaction_at = now_utc
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)

            if unresolved:
                session.commit()
                logger.debug(f"[ANCHOR] Marked {len(unresolved)} deliveries as ignored")

        except Exception as e:
            logger.error(f"[ANCHOR] Mark ignored error: {e}")
            session.rollback()
        finally:
            session.close()

    # ═══════════════════════════════════════════════════════
    def _scan_service_degraded(self, user, session, now_utc) -> list:
        """Создаёт якорь когда один или больше внешних сервисов сломаны."""
        anchors = []
        try:
            from ai_integration.service_health import get_status
            errors = get_status()
            if not errors:
                return anchors

            # Глобальный cooldown по типу: не более 1 раза за 12 часов независимо от набора сервисов
            _recent_sd = session.query(AnchorDeliveryLog).filter(
                AnchorDeliveryLog.user_id == user.id,
                AnchorDeliveryLog.anchor_types.contains('service_degraded'),
                AnchorDeliveryLog.created_at >= now_utc - timedelta(hours=12),
            ).first()
            if _recent_sd:
                return anchors

            _labels = {
                'resend': 'email-рассылка', 'deepseek': 'AI-модель',
                'newsapi': 'новости', 'ddg': 'веб-поиск',
                'openweathermap': 'погода', 'payments': 'платёжная система',
                'github': 'поиск контактов',
            }
            affected_ru = [_labels.get(s, s) for s in errors]
            affected_en = list(errors.keys())

            anchors.append(Anchor(
                user_id=user.id,
                anchor_type='service_degraded',
                source=f'service_health:{",".join(sorted(errors.keys()))}',
                topic=_t(user,
                    f'Проблемы с сервисами: {", ".join(affected_ru)}',
                    f'Service issues: {", ".join(affected_en)}'),
                priority=AnchorPriority.HIGH,
                data=json.dumps({'services': list(errors.keys()), 'count': len(errors)}),
                triggered_at=now_utc,
                expires_at=now_utc + timedelta(hours=6),
                cooldown_hours=12,
                batch_group='system',
            ))
        except Exception as e:
            logger.debug(f'[ANCHOR] service_degraded scan error: {e}')
        return anchors

    def _scan_payment_failed(self, user, session, now_utc) -> list:
        """Уведомляет если последняя попытка пополнить токены завершилась ошибкой."""
        anchors = []
        try:
            from models import PaymentHistory
            since = now_utc - timedelta(days=2)
            recent = session.query(PaymentHistory).filter(
                PaymentHistory.user_id == user.id,
                PaymentHistory.action == 'payment_failed',
                PaymentHistory.created_at >= since,
            ).order_by(PaymentHistory.created_at.desc()).first()

            if not recent:
                return anchors

            anchors.append(Anchor(
                user_id=user.id,
                anchor_type='payment_failed',
                source=f'payment:{recent.id}',
                topic=_t(user,
                    'Последний платёж не прошёл — токены не зачислены. Попробуй снова: /buy',
                    'Last payment failed — tokens not credited. Try again: /buy'),
                priority=AnchorPriority.HIGH,
                data=json.dumps({'payment_id': recent.payment_id, 'amount': str(recent.amount or '')}),
                triggered_at=now_utc,
                expires_at=now_utc + timedelta(days=3),
                cooldown_hours=12,
                batch_group='system',
            ))
        except Exception as e:
            logger.debug(f'[ANCHOR] payment_failed scan error: {e}')
        return anchors

    def _scan_custom_anchors(self, user, session, user_tz, user_now, now_utc) -> list:
        """Сканирует UserAgent.custom_anchors — создаёт якоря по расписанию/триггерам,
        заданным автором агента.

        Формат каждого элемента custom_anchors (JSON array):
        {
            "id": "daily-report",          // уникальный id (для dedup)
            "topic": "Ежедневный отчёт",   // тема якоря
            "anchor_type": "custom_anchor",// тип (если не задан — custom_anchor)
            "priority": "MEDIUM",          // CRITICAL / HIGH / MEDIUM / LOW
            "schedule_time": "09:00",      // необязательно: время дня HH:MM (окно ±30 мин)
            "cooldown_hours": 20,          // необязательно (default 20)
            "data": {}                     // необязательно: дополнительные поля в anchor.data
        }
        """
        anchors = []
        try:
            from models import UserAgent
            agents = session.query(UserAgent).filter(
                UserAgent.author_id == user.id,
                UserAgent.status.in_(['active', 'paused']),
                UserAgent.custom_anchors.isnot(None),
            ).all()

            for agent in agents:
                try:
                    custom_list = json.loads(agent.custom_anchors)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(custom_list, list):
                    continue

                for entry in custom_list:
                    if not isinstance(entry, dict):
                        continue

                    entry_id = str(entry.get('id') or entry.get('topic') or 'default')
                    topic = entry.get('topic') or f'Агент {agent.name}: кастомный якорь'
                    anchor_type = entry.get('anchor_type') or 'custom_anchor'
                    priority_str = str(entry.get('priority', 'MEDIUM')).upper()
                    # Cooldown соответствует настройке частоты агента (run_interval_minutes).
                    # Пользователь задаёт интервал при создании агента — он же задаёт
                    # частоту отчётов. Значение из custom_anchors является fallback.
                    if agent.run_interval_minutes and agent.run_interval_minutes > 0:
                        cooldown_h = agent.run_interval_minutes / 60.0
                    else:
                        cooldown_h = float(entry.get('cooldown_hours', 20))
                    schedule_time = entry.get('schedule_time')  # "HH:MM"

                    # Проверяем расписание: окно ±29 мин от schedule_time
                    if schedule_time:
                        try:
                            sched_h, sched_m = map(int, schedule_time.split(':'))
                            target_minutes = sched_h * 60 + sched_m
                            now_minutes = user_now.hour * 60 + user_now.minute
                            diff = abs(now_minutes - target_minutes)
                            # Учитываем переход через полночь
                            diff = min(diff, 24 * 60 - diff)
                            if diff > 29:
                                continue  # ещё не время (или уже прошло)
                        except (ValueError, AttributeError):
                            pass  # без расписания — всегда активен

                    priority_map = {
                        'CRITICAL': AnchorPriority.CRITICAL,
                        'HIGH': AnchorPriority.HIGH,
                        'MEDIUM': AnchorPriority.MEDIUM,
                        'LOW': AnchorPriority.LOW,
                    }
                    priority = priority_map.get(priority_str, AnchorPriority.MEDIUM)

                    source = f'agent:{agent.id}:custom:{entry_id}'

                    # ── COOLDOWN GUARD по delivered_at ──
                    # Дедупликация existing_keys защищает только пока якорь не доставлен.
                    # После доставки source исчезает из existing_keys → без этой проверки
                    # новый якорь создаётся через 5 мин (следующий скан), игнорируя cooldown.
                    try:
                        _last_delivered = session.query(Anchor.delivered_at).filter(
                            Anchor.user_id == user.id,
                            Anchor.source == source,
                            Anchor.delivered_at.isnot(None),
                        ).order_by(Anchor.delivered_at.desc()).first()
                        if _last_delivered:
                            _ld_time = _last_delivered[0]
                            if _ld_time.tzinfo is None:
                                _ld_time = _ld_time.replace(tzinfo=timezone.utc)
                            _gap_h = (now_utc - _ld_time).total_seconds() / 3600
                            if _gap_h < cooldown_h:
                                continue  # ещё рано — кулдаун не прошёл
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)

                    extra_data = {'agent_name': agent.name, 'agent_id': agent.id, 'entry_id': entry_id}
                    if isinstance(entry.get('data'), dict):
                        extra_data.update(entry['data'])

                    bg = BATCH_GROUPS.get(anchor_type, 'integration')

                    anchors.append(Anchor(
                        user_id=user.id,
                        anchor_type=anchor_type,
                        source=source,
                        topic=topic,
                        priority=priority,
                        data=json.dumps(extra_data, ensure_ascii=False),
                        triggered_at=now_utc,
                        expires_at=now_utc + timedelta(hours=max(cooldown_h * 1.5, 2.0)),
                        cooldown_hours=cooldown_h,
                        batch_group=bg,
                    ))
        except Exception as e:
            logger.debug(f'[ANCHOR] custom_anchors scan error: {e}')
        return anchors

    def _scan_agent_inbox_replies(self, user, session, now_utc) -> list:
        """Создаёт CRITICAL-якорь когда агент-почтовик нашёл новые входящие письма.
        Источник: AgentActivityLog(activity_type='inbox_reply', status='new').
        После создания якоря помечаем записи статусом 'anchored' во избежание повторов.
        """
        anchors = []
        try:
            from models import AgentActivityLog
            since = now_utc - timedelta(hours=4)
            recs = session.query(AgentActivityLog).filter(
                AgentActivityLog.user_id == user.id,
                AgentActivityLog.activity_type == 'inbox_reply',
                AgentActivityLog.status == 'new',
                AgentActivityLog.created_at >= since,
            ).order_by(AgentActivityLog.created_at.desc()).limit(10).all()

            for rec in recs:
                agent_name = (rec.target or 'агент').replace('agent:', '')
                reply_count = (rec.title or '').split(':')[1].strip() if ':' in (rec.title or '') else ''
                # Кратко первые 2 письма из stdout для превью
                _preview = ''
                _stdout = rec.content or ''
                _lines = [l.strip() for l in _stdout.splitlines() if l.strip()]
                _from_lines = [l for l in _lines if l.startswith('От:') or l.startswith('Тема:')]
                _preview = ' | '.join(_from_lines[:4])[:200]

                # Content-based dedup: если якорь с таким же preview уже есть за 3ч — пропускаем
                _anchor_cutoff = now_utc - timedelta(hours=3)
                _dup_anchor = session.query(Anchor).filter(
                    Anchor.user_id == user.id,
                    Anchor.anchor_type == 'agent_inbox_reply',
                    Anchor.created_at >= _anchor_cutoff,
                ).order_by(Anchor.created_at.desc()).limit(10).all()
                _is_dup = False
                for _da in _dup_anchor:
                    try:
                        _da_data = json.loads(_da.data) if _da.data else {}
                        if _da_data.get('preview', '')[:100] == _preview[:100] and _preview:
                            _is_dup = True
                            break
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)
                if _is_dup:
                    rec.status = 'anchored'  # помечаем как обработанный, но якорь не создаём
                    continue

                source_key = f'inbox_reply:{rec.id}'
                anchors.append(Anchor(
                    user_id=user.id,
                    anchor_type='agent_inbox_reply',
                    source=source_key,
                    topic=_t(user,
                        f'{agent_name}: новые входящие ({reply_count})',
                        f'{agent_name}: new inbox messages ({reply_count})'),
                    priority=AnchorPriority.CRITICAL,
                    data=json.dumps({
                        'agent_name': agent_name,
                        'reply_count': reply_count,
                        'preview': _preview,
                        'log_id': rec.id,
                    }, ensure_ascii=False),
                    triggered_at=now_utc,
                    expires_at=now_utc + timedelta(hours=6),
                    cooldown_hours=0,
                    batch_group='office',
                ))
                # Помечаем как обработанный чтобы не создавать дубли
                rec.status = 'anchored'
            if recs:
                try:
                    session.commit()
                except Exception:
                    session.rollback()
        except Exception as e:
            logger.debug(f'[ANCHOR] agent_inbox_replies scan error: {e}')
        return anchors

    def _scan_agent_task_blocked(self, user, session, now_utc) -> list:
        """Создаёт HIGH-якорь когда агент сигнализирует BLOCKED — нужно решение пользователя.
        Источник: AgentActivityLog(activity_type='task_blocked', status='new').
        """
        anchors = []
        try:
            from models import AgentActivityLog
            since = now_utc - timedelta(hours=8)
            recs = session.query(AgentActivityLog).filter(
                AgentActivityLog.user_id == user.id,
                AgentActivityLog.activity_type == 'task_blocked',
                AgentActivityLog.status == 'new',
                AgentActivityLog.created_at >= since,
            ).order_by(AgentActivityLog.created_at.desc()).limit(5).all()

            for rec in recs:
                agent_name = (rec.target or 'агент').replace('agent:', '')
                # Первая строка ответа агента = причина блокировки
                _reason = (rec.content or '').splitlines()[0][:200] if rec.content else ''

                source_key = f'task_blocked:{rec.id}'
                anchors.append(Anchor(
                    user_id=user.id,
                    anchor_type='agent_task_blocked',
                    source=source_key,
                    topic=_t(user,
                        f'{agent_name} застрял — нужно ваше решение',
                        f'{agent_name} is blocked — needs your decision'),
                    priority=AnchorPriority.HIGH,
                    data=json.dumps({
                        'agent_name': agent_name,
                        'reason': _reason,
                        'full_context': (rec.content or '')[:400],
                        'log_id': rec.id,
                    }, ensure_ascii=False),
                    triggered_at=now_utc,
                    expires_at=now_utc + timedelta(hours=12),
                    cooldown_hours=2,
                    batch_group='office',
                ))
                rec.status = 'anchored'
            if recs:
                try:
                    session.commit()
                except Exception:
                    session.rollback()
        except Exception as e:
            logger.debug(f'[ANCHOR] agent_task_blocked scan error: {e}')
        return anchors

    def _scan_agent_followup(self, user, session, now_utc) -> list:
        """Follow-up: проверяет dispatch-задачи, выполненные 2-6 часов назад,
        и создаёт якорь чтобы проверить реальный результат (задача создана? цель обновлена?).

        Это закрывает цикл: dispatch → agent работает → follow-up → корректировка.
        """
        anchors = []
        try:
            from models import AgentActivityLog

            # Ищем завершённые dispatch-задачи за 2-6 часов назад без follow-up
            window_start = now_utc - timedelta(hours=6)
            window_end = now_utc - timedelta(hours=2)
            completed_dispatches = session.query(AgentActivityLog).filter(
                AgentActivityLog.user_id == user.id,
                AgentActivityLog.activity_type == 'agent_event_dispatch',
                AgentActivityLog.status == 'completed',
                AgentActivityLog.created_at >= window_start,
                AgentActivityLog.created_at <= window_end,
            ).limit(5).all()

            for disp in completed_dispatches:
                # Уже есть follow-up для этого dispatch?
                followup_exists = session.query(AgentActivityLog).filter(
                    AgentActivityLog.user_id == user.id,
                    AgentActivityLog.activity_type == 'agent_followup',
                    AgentActivityLog.target == f'followup:{disp.id}',
                ).first()
                if followup_exists:
                    continue

                result_preview = (disp.result or '')[:200]
                agent_name = (disp.title or '').replace('[dispatch] ', '').split(' ←')[0].split(' →')[0]
                task_preview = (disp.content or '')[:150]

                source_key = f'followup:{disp.id}'
                anchors.append(Anchor(
                    user_id=user.id,
                    anchor_type='task_stale',
                    source=source_key,
                    topic=_t(user,
                        f'Проверка результата: {agent_name}',
                        f'Follow-up: {agent_name}'),
                    priority=AnchorPriority.LOW,
                    data=json.dumps({
                        'title': f'Проверь результат работы {agent_name}: {task_preview}. '
                                 f'Результат: {result_preview}. '
                                 f'Проверь: задача создана/обновлена? Цель продвинулась? Если нет — доделай.',
                    }, ensure_ascii=False),
                    triggered_at=now_utc,
                    expires_at=now_utc + timedelta(hours=8),
                    cooldown_hours=6,
                    batch_group='office',
                ))

        except Exception as e:
            logger.debug(f'[ANCHOR] agent_followup scan error: {e}')
        return anchors

    # CLEANUP
    # ═══════════════════════════════════════════════════════

    async def cleanup_old_anchors(self):
        """Удаляет старые доставленные/истёкшие якоря (> 30 дней)"""
        session = Session()
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=30)
            deleted = session.query(Anchor).filter(
                Anchor.created_at < cutoff
            ).delete()
            
            deleted_logs = session.query(AnchorDeliveryLog).filter(
                AnchorDeliveryLog.created_at < cutoff
            ).delete()
            
            session.commit()
            if deleted or deleted_logs:
                logger.info(f"[ANCHOR] Cleanup: removed {deleted} anchors, {deleted_logs} logs")
        except Exception as e:
            logger.error(f"[ANCHOR] Cleanup error: {e}")
            session.rollback()
        finally:
            session.close()


    def _scan_agent_script_failures(self, user, session, now_utc) -> list:
        """Создаёт HIGH-якорь если пользовательский агент дважды и более не смог
        получить данные за последние 24 часа (expired key, IMAP error, etc.).

        Данные берутся из AgentActivityLog, который закрашивает failures
        прямо при выполнении python_code в autonomous_agent.py.
        """
        anchors = []
        try:
            from models import AgentActivityLog
            since = now_utc - timedelta(hours=24)
            # Группируем ошибки по target (название агента/сервиса)
            fails = session.query(AgentActivityLog).filter(
                AgentActivityLog.user_id == user.id,
                AgentActivityLog.activity_type == 'integration',
                AgentActivityLog.status == 'failed',
                AgentActivityLog.created_at >= since,
            ).all()
            if not fails:
                return anchors

            # Группируем по сервису/агенту
            by_target: dict = {}
            for rec in fails:
                key = rec.target or 'Агент'
                by_target.setdefault(key, [])
                by_target[key].append(rec)

            # Генерируем якорь только для сервисов с 2+ ошибками
            for target, recs in by_target.items():
                if len(recs) < 2:
                    continue
                latest = max(recs, key=lambda r: r.created_at or now_utc)
                err_snippet = (latest.content or '')[:120]
                source_key = f'agent_fail:{target}'
                anchors.append(Anchor(
                    user_id=user.id,
                    anchor_type='agent_script_failed',
                    source=source_key,
                    topic=_t(user,
                        f'Агент «{target}» не может подключиться ({len(recs)}× за сутки)',
                        f'Agent «{target}» connection failing ({len(recs)}× in 24h)'),
                    priority=AnchorPriority.HIGH,
                    data=json.dumps({'agent': target, 'failures': len(recs), 'last_error': err_snippet},
                                    ensure_ascii=False),
                    triggered_at=now_utc,
                    expires_at=now_utc + timedelta(hours=12),
                    cooldown_hours=6,
                    batch_group='system',
                ))
        except Exception as e:
            logger.debug(f'[ANCHOR] agent_script_failures scan error: {e}')
        return anchors

    def _scan_agent_silent(self, user, session, now_utc) -> list:
        """Создаёт MEDIUM-якорь если агент со скриптом запускался 5+ раз за 24ч,
        но ни разу не отдал данных (stdout пустой). Значит интеграция тихо сломана.
        """
        anchors = []
        try:
            from models import UserAgent, AgentActivityLog
            agents = session.query(UserAgent).filter(
                UserAgent.author_id == user.id,
                UserAgent.status.in_(['active', 'paused']),
                UserAgent.python_code.isnot(None),
            ).all()
            since = now_utc - timedelta(hours=24)
            for agent in agents:
                # Считаем запуски L1 (integration activity) за сутки
                runs = session.query(AgentActivityLog).filter(
                    AgentActivityLog.user_id == user.id,
                    AgentActivityLog.ref_id == agent.id,
                    AgentActivityLog.activity_type == 'integration',
                    AgentActivityLog.created_at >= since,
                ).all()
                if len(runs) < 5:
                    continue
                # Все со статусом completed но без содержательного result?
                non_empty = [r for r in runs if r.result and len(r.result.strip()) > 20]
                if non_empty:
                    continue  # есть хотя бы один результат — всё ок
                source_key = f'agent_silent:{agent.id}:{now_utc.strftime("%Y-%m-%d")}'
                anchors.append(Anchor(
                    user_id=user.id,
                    anchor_type='agent_script_failed',
                    source=source_key,
                    topic=_t(user,
                        f'Агент «{agent.name}» работает, но уже сутки не получает данные ({len(runs)} запусков)',
                        f'Agent «{agent.name}» running but no data for 24h ({len(runs)} runs)'),
                    priority=AnchorPriority.MEDIUM,
                    data=json.dumps({
                        'agent': agent.name, 'agent_id': agent.id,
                        'runs_24h': len(runs), 'non_empty': 0,
                    }, ensure_ascii=False),
                    triggered_at=now_utc,
                    expires_at=now_utc + timedelta(hours=18),
                    cooldown_hours=20,
                    batch_group='system',
                ))
        except Exception as e:
            logger.debug(f'[ANCHOR] agent_silent scan error: {e}')
        return anchors

    def _scan_campaign_stagnation(self, user, session, now_utc) -> list:
        """Создаёт якорь если активная кампания (email/контент/делегирование) не имеет
        активности 3+ дня — пользователь может не знать что кампания «зависла».
        """
        anchors = []
        try:
            # Email campaigns — активна, но 0 отправок за 3 дня
            stale_cutoff = now_utc - timedelta(days=3)
            campaigns = session.query(EmailCampaign).filter(
                EmailCampaign.user_id == user.id,
                EmailCampaign.status == 'active',
            ).all()
            for c in campaigns:
                recent_sends = session.query(EmailOutreach).filter(
                    EmailOutreach.campaign_id == c.id,
                    EmailOutreach.status.in_(['sent', 'replied']),
                    EmailOutreach.sent_at >= stale_cutoff,
                ).count()
                if recent_sends > 0:
                    continue
                source_key = f'camp_stale:{c.id}:{now_utc.strftime("%Y-%m-%d")}'
                _email_agent_name_s = None
                try:
                    from models import UserAgent as _UA_s
                    _ua_candidates_s = session.query(_UA_s).filter(
                        _UA_s.author_id == user.id, _UA_s.is_active == True,
                    ).all()
                    for _ua_s in _ua_candidates_s:
                        _keys_s = (getattr(_ua_s, 'user_api_keys', '') or '').lower()
                        _code_s = (getattr(_ua_s, 'python_code', '') or '').lower()
                        if 'gmail_user=' in _keys_s or 'resend_api_key=' in _keys_s or 'send_outreach_email' in _code_s:
                            _email_agent_name_s = _ua_s.name
                            break
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)
                anchors.append(Anchor(
                    user_id=user.id,
                    anchor_type='email_campaign_report',
                    source=source_key,
                    topic=_t(user,
                        f' Email-кампания «{c.name}» без активности {3}+ дня — проверь контакты и настройки',
                        f' Email campaign «{c.name}» stale for {3}+ days — check leads and settings'),
                    priority=AnchorPriority.MEDIUM,
                    data=json.dumps({
                        'campaign_id': c.id, 'campaign_name': c.name,
                        'total_sent': c.emails_sent or 0,
                        'total_replied': c.emails_replied or 0,
                        'stale_days': 3,
                        **({'agent_name': _email_agent_name_s} if _email_agent_name_s else {}),
                    }, ensure_ascii=False),
                    triggered_at=now_utc,
                    expires_at=now_utc + timedelta(hours=48),
                    cooldown_hours=24,
                    batch_group='email',
                ))

            # Content campaigns — активна, но last_published_at > 3 дня назад
            content_campaigns = session.query(ContentCampaign).filter(
                ContentCampaign.user_id == user.id,
                ContentCampaign.status == 'active',
            ).all()
            for cc in content_campaigns:
                last_pub = cc.last_post_at
                if last_pub and last_pub.tzinfo is None:
                    last_pub = last_pub.replace(tzinfo=timezone.utc)
                if last_pub and last_pub >= stale_cutoff:
                    continue  # публиковалось недавно
                if not last_pub and cc.created_at:
                    cr = cc.created_at
                    if cr.tzinfo is None:
                        cr = cr.replace(tzinfo=timezone.utc)
                    if cr >= stale_cutoff:
                        continue  # создана недавно, ещё не время
                source_key = f'content_stale:{cc.id}:{now_utc.strftime("%Y-%m-%d")}'
                anchors.append(Anchor(
                    user_id=user.id,
                    anchor_type='content_campaign_publish',
                    source=source_key,
                    topic=_t(user,
                        f' Контент-кампания «{cc.name}» не публикуется 3+ дня',
                        f' Content campaign «{cc.name}» no posts for 3+ days'),
                    priority=AnchorPriority.LOW,
                    data=json.dumps({
                        'campaign_id': cc.id, 'campaign_name': cc.name,
                        'stale_days': 3,
                    }, ensure_ascii=False),
                    triggered_at=now_utc,
                    expires_at=now_utc + timedelta(hours=48),
                    cooldown_hours=24,
                    batch_group='content',
                ))
        except Exception as e:
            logger.debug(f'[ANCHOR] campaign_stagnation scan error: {e}')
        return anchors

    async def _scan_weather_extreme(self, user, profile, now_utc) -> list:
        """Создаёт якорь при экстремальных погодных условиях в городе пользователя.

        Крайние пороги: температура < -20°C или > 35°C, гроза, метель, ливень.
        Кэш 3 часа — не тратим API-запрос каждые 5 минут.
        """
        anchors = []
        try:
            city = (profile.city if profile else None) or getattr(user, 'city', None)
            if not city:
                return anchors
            from config import OPENWEATHERMAP_API_KEY
            if not OPENWEATHERMAP_API_KEY:
                return anchors

            # Простой кэш в памяти (ключ: city, TTL 3ч)
            _cache = getattr(self, '_weather_cache', {})
            if not hasattr(self, '_weather_cache'):
                self._weather_cache = {}
                _cache = self._weather_cache
            cache_key = city.lower().strip()
            cached = _cache.get(cache_key)
            if cached and (now_utc - cached['ts']).total_seconds() < 10800:
                w = cached['data']
            else:
                import aiohttp
                url = (f'https://api.openweathermap.org/data/2.5/weather'
                       f'?q={city}&appid={OPENWEATHERMAP_API_KEY}&units=metric&lang=ru')
                try:
                    async with aiohttp.ClientSession() as sess:
                        async with sess.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                            if resp.status != 200:
                                return anchors
                            w = await resp.json()
                    _cache[cache_key] = {'ts': now_utc, 'data': w}
                except Exception:
                    return anchors

            temp = w.get('main', {}).get('temp', 0)
            weather_id = w.get('weather', [{}])[0].get('id', 0)
            description = w.get('weather', [{}])[0].get('description', '')

            # Пороги экстремальности
            extreme_cold = temp < -20
            extreme_heat = temp > 35
            # WMO codes: 2xx=гроза, 6xx=снег/метель, 502/503/504=тяжёлый дождь
            storm = 200 <= weather_id < 300
            heavy_snow = weather_id in (602, 621, 622)
            heavy_rain = weather_id in (502, 503, 504, 522)

            is_extreme = extreme_cold or extreme_heat or storm or heavy_snow or heavy_rain
            if not is_extreme:
                return anchors

            if extreme_cold:
                topic_ru = f'Сильный мороз в {city}: {temp:.0f}°C — учти при планировании'
                topic_en = f'Extreme cold in {city}: {temp:.0f}°C — adjust your schedule'
            elif extreme_heat:
                topic_ru = f'Сильная жара в {city}: {temp:.0f}°C'
                topic_en = f'Extreme heat in {city}: {temp:.0f}°C'
            elif storm:
                topic_ru = f'Гроза в {city}: {description}'
                topic_en = f'Thunderstorm in {city}: {description}'
            elif heavy_snow:
                topic_ru = f'Метель/сильный снег в {city}: {description}'
                topic_en = f'Heavy snow/blizzard in {city}: {description}'
            else:
                topic_ru = f'Сильный дождь в {city}: {description}'
                topic_en = f'Heavy rain in {city}: {description}'

            anchors.append(Anchor(
                user_id=user.id,
                anchor_type='weather_extreme',
                source=f'weather:{cache_key}:{weather_id}',
                topic=_t(user, topic_ru, topic_en),
                priority=AnchorPriority.HIGH,
                data=json.dumps({'city': city, 'temp': temp, 'description': description,
                                 'weather_id': weather_id}, ensure_ascii=False),
                triggered_at=now_utc,
                expires_at=now_utc + timedelta(hours=6),
                cooldown_hours=6,
                batch_group='system',
            ))
        except Exception as e:
            logger.debug(f'[ANCHOR] weather_extreme scan error: {e}')
        return anchors


# ═══════════════════════════════════════════════════════
# GLOBAL INSTANCE & HELPERS
# ═══════════════════════════════════════════════════════

_anchor_engine = None


def init_anchor_engine(bot=None) -> AnchorEngine:
    """Инициализирует глобальный экземпляр AnchorEngine"""
    global _anchor_engine
    _anchor_engine = AnchorEngine(bot=bot)
    return _anchor_engine


def get_anchor_engine() -> AnchorEngine | None:
    return _anchor_engine


async def start_anchor_engine(bot=None):
    """Запускает AnchorEngine в фоне. Вызывать из main.py."""
    engine = init_anchor_engine(bot)
    await engine.start()
