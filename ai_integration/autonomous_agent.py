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

from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
from models import Session, User, Task, UserProfile, Goal
from .prompts import get_extended_system_prompt
from .dynamic_tools import tool_discovery
from .tools import get_available_tools
from .vector_memory import store_conversation_turn, build_memory_context, search_memory
from .multi_agent import get_orchestrator
from .self_learning import get_learner

logger = logging.getLogger(__name__)

# ── SSRF-защита: преамбула, которая инжектируется перед кодом агента ─────────
# Патчит urllib.request.urlopen, блокируя запросы во внутренние сети (RFC-1918,
# link-local, loopback). Защищает от атак типа Server-Side Request Forgery,
# даже если AST-валидация на upload-этапе пропустила подозрительный код.
_AGENT_CODE_PREAMBLE = '''\
import urllib.request as _ssrf_ur, socket as _ssrf_sk, ipaddress as _ssrf_ia
_ssrf_orig_open = _ssrf_ur.urlopen
def _ssrf_safe_open(url, *_a, **_kw):
    import re as _ssrf_re
    _u = url.full_url if hasattr(url, 'full_url') else str(url)
    _m = _ssrf_re.search(r'https?://([^/:?#\\s]+)', _u)
    if _m:
        try:
            _ip = _ssrf_ia.ip_address(_ssrf_sk.gethostbyname(_m.group(1)))
            if not _ip.is_global:
                raise PermissionError('SSRF: internal network requests are blocked')
        except (ValueError, OSError):
            pass
    return _ssrf_orig_open(url, *_a, **_kw)
_ssrf_ur.urlopen = _ssrf_safe_open
'''


def _wrap_agent_code(code: str) -> str:
    """Оборачивает агентский код SSRF-преамбулой."""
    return _AGENT_CODE_PREAMBLE + code


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
    Per-service детекция сигналов. Возвращает (priority_str, reason) или (None, None).
    priority_str: 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW'

    Каждая интеграция знает что важно именно для неё:
    - Email: письма во входящих (не реклама), важные отправители, дедлайны
    - Маркетплейсы (Ozon/WB): заказы, стоки, выручка, отзывы
    - CRM (AmoCRM/Битрикс): лиды, сделки, просрочки
    - RSS: статьи по темам (AI решит релевантность по snippet)
    - Notion: задачи, дедлайны, комментарии
    - ВКонтакте: сообщения, комментарии, охват
    """
    import re as _re_sig

    def _first_num(text):
        m = _re_sig.search(r'\d+', text)
        return int(m.group()) if m else 0

    def _has(pat):
        return bool(_re_sig.search(pat, text_lc))

    def _find(pat):
        return _re_sig.search(pat, text_lc)

    svc = service_label.lower()

    # ══════════════════════════════════════════════════════════════
    # EMAIL: Gmail / Яндекс Почта / Mail.ru
    # Важно: новые письма во ВХОДЯЩИХ (не реклама/спам), важные темы
    # ══════════════════════════════════════════════════════════════
    if any(x in svc for x in ('gmail', 'яндекс почта', 'mail.ru', 'email', 'почта')):
        # Папка "Входящие" — исключаем если явно реклама/спам
        is_promo = _has(r'реклам|промо|promo|spam|нежелательн|рассылк')

        # Считаем непрочитанные
        m_unread = _find(r'(\d+)\s*(?:new|непрочитан\w*|unread)')
        if not m_unread:
            m_unread = _find(r'(?:new|непрочитан\w*|unread)[^\d]{0,20}(\d+)')
        unread = _first_num(m_unread.group()) if m_unread else 0

        # Всё ли пусто?
        inbox_empty = _has(r'нет новых|no new|inbox empty|входящих нет|0 новых|0 писем')

        if inbox_empty and not unread:
            return ('LOW', 'inbox пуст')

        # Финансовые / срочные темы в письмах — всегда важно
        if _has(r'счёт|invoice|оплат|payment|задолженн|debt|просроч|overdue|срочно|urgent|deadline|dедлайн'):
            return ('CRITICAL', _re_sig.search(r'счёт|invoice|оплат|payment|задолженн|debt|просроч|overdue|срочно|urgent|deadline', text_lc).group()[:60])

        # От важных отправителей — если есть "от" + имя должности
        if _has(r'от[\s:]+\w+.*(?:директор|CEO|boss|клиент|client|партнёр|partner)'):
            return ('HIGH', 'письмо от важного отправителя')

        if not is_promo and unread >= 10:
            return ('CRITICAL', f'{unread} непрочитанных во входящих')
        if not is_promo and unread >= 3:
            return ('HIGH', f'{unread} новых писем во входящих')
        if not is_promo and unread >= 1:
            return ('MEDIUM', f'{unread} новое письмо')

        return ('LOW', f'почта проверена, {unread} непрочитанных')

    # ══════════════════════════════════════════════════════════════
    # МАРКЕТПЛЕЙСЫ: Ozon / Wildberries
    # Важно: заказы, остатки, выручка, отзывы, статусы
    # ══════════════════════════════════════════════════════════════
    if any(x in svc for x in ('ozon', 'wildberries', 'wb', 'маркетплейс')):
        # Критичное: нет в наличии, блокировка, возврат массовый
        if _has(r'нет в наличии|out of stock|остаток.*?0\b|товар заблокир|аккаунт заблокир|account.*blocked'):
            m = _find(r'нет в наличии|out of stock|остаток.*?0\b|товар заблокир')
            return ('CRITICAL', (m.group()[:60] if m else 'нет в наличии'))

        # Плохой отзыв
        if _has(r'(?:оценка|отзыв|звезд|rating|review).*?[1-2]\b|\b[1-2]\s*(?:звезд|star)'):
            return ('HIGH', 'негативный отзыв 1-2 звезды')

        # Падение выручки/продаж
        m_drop = _find(r'(?:выручка|продаж|доход|revenue|sales)[^.]{0,30}-\s*(\d+)\s*%')
        if m_drop:
            pct = _first_num(m_drop.group(1))
            if pct >= 20:
                return ('CRITICAL', f'падение на -{pct}%')
            if pct >= 10:
                return ('HIGH', f'снижение на -{pct}%')

        # Новые заказы
        m_orders = _find(r'(?:новых?\s+)?(?:заказ|order)[^\d]{0,10}(\d+)')
        if not m_orders:
            m_orders = _find(r'(\d+)\s*(?:новых?\s+)?(?:заказ|order)')
        orders = _first_num(m_orders.group()) if m_orders else 0
        if orders >= 1:
            return ('HIGH', f'{orders} новых заказов')

        # Заканчивается остаток
        if _has(r'остат\w+.*?(?:[1-5])\s*(?:шт|ед|pcs)|мало на складе|low stock'):
            return ('HIGH', 'заканчивается остаток')

        return ('LOW', 'маркетплейс проверен, без изменений')

    # ══════════════════════════════════════════════════════════════
    # CRM: AmoCRM / Битрикс24
    # Важно: новые лиды, горячие сделки, просрочки, оплаты
    # ══════════════════════════════════════════════════════════════
    if any(x in svc for x in ('amocrm', 'амо', 'битрикс', 'bitrix', 'crm')):
        # Просрочки — всегда критично
        if _has(r'просроч|overdue|deadline.*passed|истёк\s+срок'):
            m = _find(r'просроч|overdue')
            return ('CRITICAL', (m.group()[:60] if m else 'просрочена задача/сделка'))

        # Горячий лид / новая сделка
        if _has(r'горяч|hot lead|новый лид|new lead|лид добавлен|deal created|сделка создана'):
            return ('HIGH', 'новый горячий лид / сделка')

        # Смена статуса сделки
        if _has(r'сделка.*перешла|deal.*moved|этап.*изменён|stage.*changed'):
            m = _find(r'сделка.*перешла|deal.*moved|этап.*изменён')
            return ('MEDIUM', (m.group()[:60] if m else 'смена статуса сделки'))

        # Новое сообщение от клиента
        if _has(r'(?:клиент|client|customer).*написал|new message.*from|входящее сообщение'):
            return ('HIGH', 'клиент написал в CRM')

        # Ожидается оплата
        if _has(r'ожидает.*оплат|pending.*payment|счёт выставлен|invoice sent'):
            return ('MEDIUM', 'ожидается оплата')

        # Общая активность — лиды/задачи
        m_leads = _find(r'(\d+)\s*(?:новых?\s+)?(?:лид|lead|задач|task)')
        if m_leads and _first_num(m_leads.group()) > 0:
            return ('MEDIUM', m_leads.group()[:60])

        return ('LOW', 'CRM проверена, без изменений')

    # ══════════════════════════════════════════════════════════════
    # RSS / Новости
    # Важно: статьи по ТЕМАМ пользователя (AI решит релевантность)
    # Мы определяем есть ли вообще контент, а AI оценит по snippet
    # ══════════════════════════════════════════════════════════════
    if any(x in svc for x in ('rss', 'новост', 'news', 'tass', 'лента', 'feed')):
        # Ничего не нашлось
        if _has(r'нет новостей|no articles|no items|feed empty|лента пуста|0 статей|0 новостей'):
            return ('LOW', 'нет новых статей')

        # Срочная новость / breaking
        if _has(r'breaking|срочно|экстренно|чрезвычайн|disaster|катастроф|авария|война|атак'):
            m = _find(r'breaking|срочно|экстренно|чрезвычайн')
            return ('CRITICAL', (m.group()[:60] if m else 'срочная новость'))

        # Считаем статьи
        m_count = _find(r'(\d+)\s*(?:новых?\s+)?(?:статей|новостей|articles?|items?|публикац)')
        count = _first_num(m_count.group(1)) if m_count else 0

        # Есть заголовки — значит контент есть, AI оценит по snippet
        if count >= 5 or _has(r'заголовок:|title:|headline:|•\s*\w{5}'):
            return ('MEDIUM', f'{count} новых статей' if count else 'новые статьи')

        if count >= 1:
            return ('MEDIUM', f'{count} новая статья')

        # Вывод непустой, но счётчик не найден — есть что-то
        if len(text_raw.strip()) > 100:
            return ('MEDIUM', 'RSS вернул данные')

        return ('LOW', 'RSS проверен')

    # ══════════════════════════════════════════════════════════════
    # Notion
    # Важно: дедлайны сегодня/просроченные, новые задачи, комментарии
    # ══════════════════════════════════════════════════════════════
    if 'notion' in svc:
        if _has(r'просроч|overdue|deadline.*сегодня|due today|истёк'):
            return ('CRITICAL', 'просрочена задача в Notion')
        if _has(r'новый комментарий|new comment|упомянул|mentioned you|@'):
            return ('HIGH', 'новый комментарий / упоминание')
        if _has(r'задача.*создана|page created|добавлена.*страниц|new page'):
            return ('MEDIUM', 'новая задача / страница')
        if _has(r'статус.*изменён|status.*changed|завершена|completed|done'):
            return ('MEDIUM', 'статус задачи изменён')
        if len(text_raw.strip()) > 80:
            return ('LOW', 'Notion синхронизирован')
        return (None, None)  # пустой вывод — не создаём якорь

    # ══════════════════════════════════════════════════════════════
    # ВКонтакте
    # Важно: новые сообщения, комментарии, блокировка, охват
    # ══════════════════════════════════════════════════════════════
    if any(x in svc for x in ('вконтакте', 'vk', 'вк')):
        if _has(r'заблокир|banned|ограничен|restricted|нарушение|violation'):
            return ('CRITICAL', 'сообщество/страница заблокирована или ограничена')
        if _has(r'новое сообщение|new message|(?:входящих|unread).*\d'):
            m_msg = _find(r'(\d+)\s*(?:новых?\s+)?сообщ')
            cnt = _first_num(m_msg.group()) if m_msg else 1
            return ('HIGH', f'{cnt} новых сообщений ВКонтакте')
        if _has(r'новый комментарий|new comment|жалоба|complaint|негатив'):
            return ('HIGH', 'новый комментарий или жалоба')
        # Охват резко упал
        m_drop = _find(r'охват[^.]{0,20}-\s*(\d+)\s*%|reach[^.]{0,20}-\s*(\d+)\s*%')
        if m_drop:
            return ('HIGH', m_drop.group()[:60])
        if len(text_raw.strip()) > 80:
            return ('LOW', 'ВКонтакте проверен')
        return (None, None)

    # ══════════════════════════════════════════════════════════════
    # Telegram Bot
    # Важно: ошибки бота, новые пользователи, всплеск сообщений
    # ══════════════════════════════════════════════════════════════
    if any(x in svc for x in ('telegram bot', 'bot', 'бот')):
        if _has(r'webhook.*error|ошибка.*бот|bot.*stopped|бот.*не отвечает|timeout.*bot'):
            return ('CRITICAL', 'бот не работает или ошибка вебхука')
        if _has(r'новый пользователь|new user|new subscriber'):
            m_u = _find(r'(\d+)\s*(?:новых?\s+)?(?:пользовател|user|subscriber)')
            cnt = _first_num(m_u.group()) if m_u else 1
            if cnt >= 10:
                return ('HIGH', f'+{cnt} новых пользователей')
            return ('MEDIUM', f'+{cnt} новый пользователь')
        m_msgs = _find(r'(\d+)\s*(?:входящих\s+)?(?:сообщений|messages?|updates?)')
        if m_msgs and _first_num(m_msgs.group(1)) >= 20:
            return ('HIGH', f'{_first_num(m_msgs.group(1))} входящих сообщений')
        if len(text_raw.strip()) > 80:
            return ('LOW', 'Telegram бот проверен')
        return (None, None)

    # ══════════════════════════════════════════════════════════════
    # Stripe
    # Важно: отклонённый платёж, чарджбэк, превышение порога выплат
    # ══════════════════════════════════════════════════════════════
    if 'stripe' in svc:
        if _has(r'chargeback|dispute|мошенничество|fraud|risk.*high'):
            return ('CRITICAL', 'чарджбэк или спор по платежу Stripe')
        if _has(r'failed|отклон|declined|payment.*fail|charge.*fail'):
            m = _find(r'failed|отклон|declined')
            return ('CRITICAL', (m.group()[:60] if m else 'платёж отклонён'))
        if _has(r'refund|возврат'):
            return ('HIGH', 'запрос на возврат средств')
        m_amt = _find(r'(?:payout|выплата)[^\d]{0,15}([\d\s]+)')
        if m_amt:
            return ('MEDIUM', f'выплата: {m_amt.group()[:60]}')
        m_pay = _find(r'(?:payment|платёж|оплата)[^\d]{0,10}([\d\s]+)')
        cnt = _first_num(m_pay.group()) if m_pay else 0
        if cnt > 0:
            return ('MEDIUM', f'новый платёж Stripe')
        if len(text_raw.strip()) > 80:
            return ('LOW', 'Stripe проверен')
        return (None, None)

    # ══════════════════════════════════════════════════════════════
    # Shopify
    # Важно: новые заказы, возвраты, нет в наличии, отзывы
    # ══════════════════════════════════════════════════════════════
    if 'shopify' in svc:
        if _has(r'fraud|мошенническ|high.risk|chargeback'):
            return ('CRITICAL', 'подозрительный заказ / мошенничество')
        if _has(r'out of stock|нет в наличии|sold out|остат\w+.*?0\b'):
            return ('CRITICAL', 'товар закончился')
        m_orders = _find(r'(\d+)\s*(?:new\s+)?(?:order|заказ)')
        if not m_orders:
            m_orders = _find(r'(?:order|заказ)[^\d]{0,10}(\d+)')
        orders = _first_num(m_orders.group()) if m_orders else 0
        if orders >= 1:
            return ('HIGH', f'{orders} новых заказов Shopify')
        if _has(r'refund|return|возврат'):
            return ('HIGH', 'запрос возврата')
        if _has(r'abandoned.*cart|брошен.*корзин'):
            return ('MEDIUM', 'брошенные корзины')
        if len(text_raw.strip()) > 80:
            return ('LOW', 'Shopify проверен')
        return (None, None)

    # ══════════════════════════════════════════════════════════════
    # GitHub
    # Важно: failed CI/CD, PR ожидает ревью, critical issue, security alert
    # ══════════════════════════════════════════════════════════════
    if 'github' in svc:
        if _has(r'security.*alert|vulnerabilit|dependabot|cve-\d'):
            return ('CRITICAL', 'security-уязвимость в зависимостях')
        if _has(r'build.*fail|ci.*fail|pipeline.*fail|workflow.*fail|тест.*упал|test.*fail'):
            m = _find(r'build.*fail|ci.*fail|test.*fail')
            return ('HIGH', (m.group()[:60] if m else 'CI/CD упал'))
        if _has(r'pull request|pr.*review|ревью.*ожидает|awaiting.*review|review.*requested'):
            return ('HIGH', 'PR ожидает ревью')
        if _has(r'\bbug\b|\bcritical\b|\bhigh\b.*issue|\bблокер\b'):
            return ('HIGH', 'критический issue или баг')
        m_pr = _find(r'(\d+)\s*(?:open\s+)?(?:pull request|PR|issue)')
        if m_pr and _first_num(m_pr.group(1)) > 0:
            return ('MEDIUM', m_pr.group()[:60])
        if _has(r'merged|deployed|выпущен|released'):
            return ('MEDIUM', 'PR влит / деплой')
        if len(text_raw.strip()) > 80:
            return ('LOW', 'GitHub проверен')
        return (None, None)

    # ══════════════════════════════════════════════════════════════
    # Jira
    # Важно: blocker/critical, просрочки, назначено на меня
    # ══════════════════════════════════════════════════════════════
    if 'jira' in svc:
        if _has(r'\bblocker\b|\bcritical\b.*(?:issue|task|bug)|приоритет.*критич'):
            return ('CRITICAL', 'blocker или critical задача')
        if _has(r'overdue|просроч|deadline.*passed|истёк.*срок|due.*yesterday'):
            return ('CRITICAL', 'просрочена задача Jira')
        if _has(r'assigned.*to me|назначен.*на тебя|assigned.*you'):
            return ('HIGH', 'новая задача назначена на вас')
        if _has(r'(?:issue|задача|тикет).*(?:создан|opened|created|new)'):
            return ('MEDIUM', 'новая задача создана')
        if _has(r'status.*changed|статус.*изменён|moved.*to|переведен.*в'):
            return ('MEDIUM', 'статус задачи изменён')
        if len(text_raw.strip()) > 80:
            return ('LOW', 'Jira проверена')
        return (None, None)

    # ══════════════════════════════════════════════════════════════
    # Slack
    # Важно: прямые сообщения, упоминания @, каналы с алертами
    # ══════════════════════════════════════════════════════════════
    if 'slack' in svc:
        if _has(r'urgent|срочно|critical|инцидент|incident|alert.*channel|#alerts|#incidents'):
            m = _find(r'urgent|срочно|critical|инцидент|incident')
            return ('CRITICAL', (m.group()[:60] if m else 'алерт в Slack'))
        if _has(r'direct message|dm от|написал.*лично|личное сообщение'):
            return ('HIGH', 'прямое сообщение в Slack')
        if _has(r'@(?:you|me|тебя)|упомянул|mentioned you|упоминание'):
            return ('HIGH', 'вас упомянули в Slack')
        m_msg = _find(r'(\d+)\s*(?:new\s+)?(?:message|сообщен)')
        cnt = _first_num(m_msg.group(1)) if m_msg else 0
        if cnt >= 5:
            return ('MEDIUM', f'{cnt} новых сообщений в Slack')
        if cnt >= 1:
            return ('MEDIUM', f'{cnt} новое сообщение')
        if len(text_raw.strip()) > 80:
            return ('LOW', 'Slack проверен')
        return (None, None)

    # ══════════════════════════════════════════════════════════════
    # CoinGecko (крипто)
    # Важно: резкое движение цены ±10%, новые важные новости
    # ══════════════════════════════════════════════════════════════
    if any(x in svc for x in ('coingecko', 'crypto', 'крипто', 'binance')):
        # Сильное движение
        m_pct = _find(r'([+-]?\s*\d{1,3}(?:\.\d+)?)\s*%')
        if m_pct:
            try:
                pct_val = abs(float(m_pct.group(1).replace(' ', '').replace('+', '')))
                if pct_val >= 20:
                    sign = '↑' if '+' in m_pct.group(0) or (m_pct.group(1)[0] not in '-−') else '↓'
                    return ('CRITICAL', f'{sign}{pct_val:.0f}% за период')
                if pct_val >= 10:
                    sign = '↑' if '+' in m_pct.group(0) or (m_pct.group(1)[0] not in '-−') else '↓'
                    return ('HIGH', f'{sign}{pct_val:.0f}% за период')
                if pct_val >= 5:
                    return ('MEDIUM', f'{pct_val:.0f}% изменение цены')
            except ValueError:
                pass
        if _has(r'ath|all.time.high|исторический максимум|новый максимум'):
            return ('HIGH', 'новый исторический максимум')
        if _has(r'liquidat|ликвидац'):
            return ('CRITICAL', 'ликвидация позиции')
        if len(text_raw.strip()) > 80:
            return ('LOW', 'крипто данные получены')
        return (None, None)

    # ══════════════════════════════════════════════════════════════
    # YouTube
    # Важно: жалобы / страйки, резкое падение просмотров, новые комментарии
    # ══════════════════════════════════════════════════════════════
    if 'youtube' in svc:
        if _has(r'strike|community.*guidelines|copyright.*claim|заблокир|monetization.*disabled'):
            return ('CRITICAL', 'страйк или блокировка монетизации YouTube')
        if _has(r'(?:просмотр|view)[^.]{0,20}-\s*\d{2,}\s*%|views.*dropped'):
            return ('HIGH', 'резкое падение просмотров')
        m_subs = _find(r'([+-]?\d+)\s*(?:новых?\s+)?(?:подписчик|subscriber)')
        if m_subs:
            cnt = abs(_first_num(m_subs.group()))
            if cnt >= 100:
                return ('HIGH', f'+{cnt} подписчиков')
            if cnt >= 10:
                return ('MEDIUM', f'+{cnt} подписчиков')
        if _has(r'новый комментарий|new comment|жалоба на видео|video.*reported'):
            return ('MEDIUM', 'новый комментарий / жалоба на видео')
        if len(text_raw.strip()) > 80:
            return ('LOW', 'YouTube аналитика получена')
        return (None, None)

    # ══════════════════════════════════════════════════════════════
    # HeadHunter (hh.ru)
    # Важно: новые отклики на вакансию, приглашения на интервью
    # ══════════════════════════════════════════════════════════════
    if any(x in svc for x in ('headhunter', 'hh', 'hh.ru', 'работа')):
        if _has(r'приглашен.*интервью|interview.*invite|собеседование'):
            return ('HIGH', 'приглашение на интервью')
        m_resp = _find(r'(\d+)\s*(?:новых?\s+)?(?:отклик|response|резюме|applicant|кандидат)')
        cnt = _first_num(m_resp.group(1)) if m_resp else 0
        if cnt >= 5:
            return ('HIGH', f'{cnt} новых откликов')
        if cnt >= 1:
            return ('MEDIUM', f'{cnt} новый отклик')
        if _has(r'вакансия.*истекает|job.*expires|срок.*вакансии'):
            return ('MEDIUM', 'срок вакансии истекает')
        if len(text_raw.strip()) > 80:
            return ('LOW', 'HeadHunter проверен')
        return (None, None)

    # ══════════════════════════════════════════════════════════════
    # Calendly
    # Важно: новое бронирование, отмена встречи
    # ══════════════════════════════════════════════════════════════
    if 'calendly' in svc:
        if _has(r'cancel|отменил|отказ'):
            return ('HIGH', 'встреча отменена в Calendly')
        if _has(r'новое бронирование|new booking|new event|meeting.*scheduled|встреча.*создана'):
            return ('HIGH', 'новая встреча забронирована')
        m_book = _find(r'(\d+)\s*(?:новых?\s+)?(?:бронирован|booking|event|meeting)')
        cnt = _first_num(m_book.group(1)) if m_book else 0
        if cnt >= 1:
            return ('HIGH', f'{cnt} новых бронирований')
        if len(text_raw.strip()) > 80:
            return ('LOW', 'Calendly проверен')
        return (None, None)

    # ══════════════════════════════════════════════════════════════
    # HubSpot
    # Практически то же что AmoCRM/Битрикс, но свои термины
    # ══════════════════════════════════════════════════════════════
    if 'hubspot' in svc:
        if _has(r'deal.*won|deal.*lost|сделка.*выиграна|сделка.*проиграна'):
            m = _find(r'deal.*won|deal.*lost|сделка.*выиграна|сделка.*проиграна')
            return ('HIGH', (m.group()[:60] if m else 'изменение статуса сделки'))
        if _has(r'new contact|новый контакт|new lead|новый лид|form.*submitted'):
            return ('HIGH', 'новый лид / контакт в HubSpot')
        if _has(r'overdue|просроч|task.*due'):
            return ('CRITICAL', 'просрочена задача HubSpot')
        if _has(r'email.*opened|email.*clicked|открыл.*письмо'):
            return ('MEDIUM', 'контакт открыл письмо')
        if len(text_raw.strip()) > 80:
            return ('LOW', 'HubSpot проверен')
        return (None, None)

    # ══════════════════════════════════════════════════════════════
    # Airtable / Google Sheets / Trello
    # Структурированные данные — важны изменения записей
    # ══════════════════════════════════════════════════════════════
    if any(x in svc for x in ('airtable', 'google sheets', 'sheets', 'trello')):
        if _has(r'overdue|просроч|deadline|истёк'):
            return ('HIGH', 'просрочена задача')
        if _has(r'(?:запись|record|строка|row|карточка|card).*(?:создан|added|new)'):
            return ('MEDIUM', 'новая запись / карточка')
        if _has(r'(?:запись|record|строка|row).*(?:изменен|updated|changed)'):
            return ('LOW', 'данные обновлены')
        if len(text_raw.strip()) > 80:
            return ('LOW', 'данные получены')
        return (None, None)

    # ══════════════════════════════════════════════════════════════
    # OpenWeatherMap — интересно только если экстрим
    # ══════════════════════════════════════════════════════════════
    if any(x in svc for x in ('weather', 'openweather', 'погод')):
        if _has(r'storm|hurricane|tornado|гроза|шторм|сильный ветер|extreme|экстремальн'):
            return ('HIGH', 'экстремальная погода')
        return (None, None)  # обычная погода — не беспокоим

    # ══════════════════════════════════════════════════════════════
    # Resend — сервис отправки почты (мониторинг доставки)
    # ══════════════════════════════════════════════════════════════
    if 'resend' in svc:
        if _has(r'bounce|spam|block|заблокир|отклон|reject'):
            return ('CRITICAL', 'письма блокируются / попадают в спам')
        if _has(r'delivered|доставлен'):
            m_d = _find(r'(\d+)\s*delivered')
            cnt = _first_num(m_d.group(1)) if m_d else 0
            if cnt > 0:
                return ('LOW', f'{cnt} писем доставлено')
        return (None, None)

    # ══════════════════════════════════════════════════════════════
    # GENERIC fallback для неизвестных интеграций
    # Используем базовые keyword-сигналы
    # ══════════════════════════════════════════════════════════════
    if _has(r'\burgent\b|\bсрочно\b|\bcritical\b|\bкритичн|\bфатал|\bfailed\b|\berror\b'):
        m = _find(r'urgent|срочно|critical|критичн|fatal|failed|error')
        return ('CRITICAL', (m.group()[:60] if m else 'критическая ошибка'))
    if _has(r'\bwarning\b|\bвниман|\bimportant\b|\bважн|-\s*\d{2,}\s*%|\boverdue\b'):
        m = _find(r'warning|вниман|important|важн|-\d+%|overdue')
        return ('HIGH', (m.group()[:60] if m else 'требует внимания'))
    if len(text_raw.strip()) > 100:
        return ('LOW', 'данные получены')
    return (None, None)  # пустой вывод — не создаём якорь


def spawn_integration_anchors(user_db_id: int, agent_name: str, service_label: str, output: str) -> None:
    """
    Анализирует вывод скрипта интеграции per-service логикой,
    создаёт Anchor(integration_alert) для доставки через AnchorEngine.
    Без AI-вызова. AI решает только финальную формулировку и SKIP при доставке.
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
        _ias.add(_Anch(
            user_id=user_db_id,
            anchor_type='integration_alert',
            source=_src,
            topic=f'{agent_name}: {service_label} — {reason}',
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
                      tool_choice=None, exclude_tools=None, model=None, **kwargs):
        """Универсальный вызов DeepSeek API.
        
        Args:
            model: Модель для вызова. По умолчанию DEEPSEEK_MODEL.
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
            "max_tokens": kwargs.pop("max_tokens", 1000),
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
         for _attempt in range(2):  # 1 retry on transient errors
          try:
            async with aiohttp.ClientSession() as session:
                    async with session.post(url, headers=headers, json=data,
                                            timeout=aiohttp.ClientTimeout(total=120)) as resp:
                        if resp.status == 200:
                            result = await resp.json()
                            # Логируем результат
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
                        # Retry only on server errors (5xx); raise immediately on 4xx
                        if resp.status < 500 or _attempt >= 1:
                            raise Exception(f"AI call failed: {resp.status} {error}")
                        logger.warning(f"[AI] Server error {resp.status}, retrying...")
                        await asyncio.sleep(2)
          except asyncio.TimeoutError:
            if _attempt >= 1:
                raise
            logger.warning("[AI] Timeout, retrying...")
            await asyncio.sleep(2)

    # ===== SMART TOOL FILTERING (reduces API tokens) =====

    # Core tools sent with every call (~15 tools instead of 46)
    CORE_TOOLS = {
        'add_task', 'complete_task', 'edit_task', 'delete_task', 'list_tasks',
        'update_profile', 'research_topic',
        'create_goal', 'delete_goal', 'list_goals', 'update_goal_progress',
        'find_relevant_contacts_for_task', 'set_contact_alert', 'generate_image',
        # Campaign management always available — conversation can span multiple turns
        'start_content_campaign', 'manage_content_campaign',
        'start_email_campaign', 'start_delegation_campaign',
    }

    # Extended tool groups — activated by keywords in user message
    TOOL_GROUPS = {
        'email': {
            'keywords': ['email', 'e-mail', 'почт', 'письм', 'рассылк', 'лид', 'lead', 'outreach', 'campaign', 'кампани'],
            'tools': {'send_email', 'send_outreach_email', 'send_follow_up_email', 'reply_to_outreach_email',
                      'add_email_leads', 'start_email_campaign', 'pause_email_campaign',
                      'get_email_campaign_status', 'update_email_campaign', 'list_email_contacts', 'save_email_contact'},
        },
        'delegation': {
            'keywords': ['делегир', 'delegat', 'поруч', 'назнач'],
            'tools': {'delegate_task', 'accept_delegated_task', 'reject_delegated_task',
                      'get_delegation_progress', 'start_delegation_campaign', 'manage_delegation_campaign'},
        },
        'content': {
            'keywords': ['пост', 'post', 'публик', 'publish', 'контент', 'content',
                         'discord', 'telegram', 'канал', 'channel', 'стратег',
                         'кампани', 'компани', 'запуст', 'продвиж', 'начн', 'написа',
                         'писать', 'ролик', 'аудитор', 'подписч', 'привлеч'],
            'tools': {'create_post', 'edit_post', 'delete_post', 'get_posts',
                      'publish_to_telegram', 'publish_to_discord',
                      'set_content_strategy', 'start_content_campaign', 'manage_content_campaign'},
        },
        'messaging': {
            'keywords': ['сообщ', 'message', 'написа', 'отправ', 'напис', 'inbox', 'входящ'],
            'tools': {'send_message_to_user', 'reply_to_user_message',
                      'get_incoming_messages', 'find_and_message_relevant_users'},
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

        SMART TOOL FILTERING DISABLED — AI sees all tools every call.
        Re-enable keyword filtering below if token cost becomes a concern.
        """
        return set()  # exclude nothing — let AI pick freely

    # ===== ADAPTIVE TOOL CHOICE =====

    # Тривиальные сообщения — tool_choice=auto (не заставляем)
    def _determine_tool_choice(self, user_message, profile_data=None, tasks_data=None):
        """PERF_OPT_V1: всегда auto — убрали лишний round-trip при пустом профиле.
        
        Откат: вернуть `if filled < 2: return "required"` после подсчёта filled.
        """
        profile_data = profile_data or {}
        key_fields = ['city', 'company', 'position', 'skills', 'interests', 'goals']
        filled = sum(1 for f in key_fields if profile_data.get(f))
        # PERF_OPT_V1: было return "required" когда filled < 2
        logger.info(f"[HYBRID] tool_choice=auto (profile {filled}/6) for: '{user_message[:50]}'")
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

    # Максимальный бюджет в символах (~9000 токенов для рус. текста, ratio ~3 chars/token)
    MAX_PROMPT_CHARS = 27000  # ~9000 tokens (matched to pre-growth prompt size)
    MAX_HISTORY_CHARS = 8000  # ~2700 tokens для истории

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
        prompt_chars = len(base_prompt)
        history_chars = sum(len(m.get('content', '')) for m in history)
        total = prompt_chars + history_chars
        
        if total <= self.MAX_PROMPT_CHARS:
            return base_prompt, history  # Всё влезает
        
        overflow = total - self.MAX_PROMPT_CHARS
        trimmed = 0
        logger.info(f"[TOKEN_BUDGET] Over budget by ~{overflow // 3} tokens "
                    f"({prompt_chars} prompt + {history_chars} history chars)")
        
        # 1. Обрезаем историю — оставляем последние 4 сообщения
        if len(history) > 4 and history_chars > self.MAX_HISTORY_CHARS:
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
            '[MULTI-AGENT',
            '[ГЛУБОКИЙ АНАЛИЗ R1]',
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
            api = get_api_client()
            weather_data = await api.get_weather(city, cache_ttl=1800) if city else None
            if weather_data:
                weather_info = (
                    f"{weather_data['city_name']}: {weather_data['temp']:.0f}°C, "
                    f"{weather_data['description']}, влажность {weather_data['humidity']}%, "
                    f"ветер {weather_data['wind_speed']} м/с"
                )
            news_articles = await api.get_news(topic=city, page_size=3, cache_ttl=900) if city else None
            if news_articles:
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
        
        Args:
            user_id: telegram ID
            mode: 'proactive'|'anchor'|'reminder'|None — для проактивных режимов
                  user_memory минимизируется чтобы AI не цитировал устаревшие данные
        
        Returns: dict с полями для промпта + метаданные.
        """
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
                              'interests', 'birthdate'):
                    val = getattr(profile, field, None)
                    if val:
                        profile_data[field] = val
                if profile.city:
                    # Async weather/news через api_client (не блокирует event loop)
                    weather_info, news_info = await self._get_weather_news_cached(profile.city)
            if user.telegram_channel:
                profile_data['telegram_channel'] = user.telegram_channel

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
                    Task.delegation_status != 'rejected',
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

            # Для проактивных/anchor режимов — НЕ передаём user_memory в промпт
            # чтобы AI не цитировал устаревшие данные из памяти как факты.
            # AI должен получать актуальные данные ТОЛЬКО через инструменты (list_tasks, list_goals).
            effective_memory = decrypted_memory
            if mode in ('proactive', 'anchor'):
                effective_memory = ""  # AI получит данные через tool calls

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

            # Базовый промпт
            base_prompt = get_extended_system_prompt(
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
                lang=user_lang
            )

            return {
                'base_prompt': base_prompt,
                'sub_tier': sub_tier,
                'profile_data': profile_data,
                'tasks': tasks_data,
                'user_now': user_now,
                'time_str': time_str,
                'date_str': date_str,
                'user_lang': user_lang,
            }
        finally:
            session.close()

    # ===== EXECUTE =====

    async def _run_external_action(self, params: dict, user_id: int) -> dict:
        """Re-runs agent python_code with AGENT_ACTION env vars to perform write operations."""
        import os as _os_ea, sys as _sys_ea, asyncio as _aio_ea
        agent_data = self._active_agent_data.get(user_id)
        if not agent_data or not agent_data.get('python_code', '').strip():
            return {"error": "Агент не имеет подключённого скрипта"}
        action = str(params.get('action', '')).strip()
        action_params = params.get('params', {})
        if not isinstance(action_params, dict):
            action_params = {}
        if not action:
            return {"error": "Параметр action не указан"}
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
            env[f'AGENT_PARAM_{str(_k).upper()}'] = str(_v)
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
            except Exception:
                pass
        try:
            _kwargs = dict(stdout=_aio_ea.subprocess.PIPE, stderr=_aio_ea.subprocess.PIPE, env=env)
            if _is_linux:
                _kwargs['preexec_fn'] = _resource_limits
            proc = await _aio_ea.create_subprocess_exec(_sys_ea.executable, '-c', py_code, **_kwargs)
            try:
                stdout, stderr = await _aio_ea.wait_for(proc.communicate(), timeout=15.0)
                out = stdout.decode('utf-8', errors='replace').strip()[:2000]
                err = stderr.decode('utf-8', errors='replace').strip()[:500]
            except _aio_ea.TimeoutError:
                proc.kill()
                return {"status": "error", "error": "Timeout (15s) — скрипт выполнялся слишком долго"}
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
                        _al_sa.add(_AALA(
                            user_id=_al_ua.id,
                            activity_type='run_agent_action',
                            title=f'{_aname_a} · {action}',
                            content=(out[:600] if out else (err or 'нет вывода')),
                            target=_svc_a,
                            status='completed' if out else 'failed',
                        ))
                        _al_sa.commit()
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
                    # Web-контекст: не отправляем изображения в Telegram при запросе с дашборда
                    if web_context and tool_name == 'generate_image' and 'send_to_telegram' in sig.parameters:
                        params['send_to_telegram'] = False

                    # === Parameter auto-fix для известных quirks ===
                    params = self._fix_tool_params(tool_name, params, user_message)

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

                    results.append({"tool": tool_name, "success": True,
                                    "result": result, "reason": reason})
                    
                    logger.info(f"[EXEC] {tool_name} ✓ result={str(result)[:200]} — {reason}")

                except Exception as e:
                    logger.error(f"[EXEC] {tool_name} ✗ — {e}\n{traceback.format_exc()}")
                    try:
                        self.tool_discovery.learn_from_failure(
                            func_name=tool_name, error=str(e))
                    except Exception:
                        pass
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

        if tool_name == 'find_relevant_contacts_for_task':
            if 'description' in params and 'task_description' not in params:
                params['task_description'] = params.pop('description')
            elif 'task_description' not in params:
                params['task_description'] = 'помощь с задачей'

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
                # DeepSeek вызвал без content — извлекаем из user_message или reason
                fallback_content = params.pop('text', None) or params.pop('message', None) or params.pop('post_text', None) or params.pop('body', None)
                if not fallback_content and user_message:
                    fallback_content = user_message[:500]
                if fallback_content:
                    params['content'] = fallback_content
                    logger.info(f"[FIX_PARAMS] {tool_name}: extracted content from fallback")
                else:
                    params['content'] = 'Новый пост'
                    logger.info(f"[FIX_PARAMS] {tool_name}: used default content")

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
                "\n🧠 HELP SUBSTANTIVELY: when user sets a task or discusses a problem — FIRST give specific ideas, strategies, steps HOW to solve it. 'Attract users' → suggest 2-3 channels and methods. 'Write a post' → suggest structure and hook. THEN call tools as supplement. Don't reduce help to just 'I'll find contacts' or 'update profile'."
                "\nUser talks about themselves/project → GIVE EXPERT ADVICE for their niche + update_profile + research_topic(niche trends)."
                "\nUser mentions skills/technologies → update_profile + research_topic(trends)."
                "\nUser mentions achievement → complete_task + update_goal_progress + suggest create_post."
                "\n🔑 IMPLICIT TASK COMPLETION: when user reports they DID something (set up, wrote, sent, finished, figured out, bought, arranged, called, completed, launched — ANY past tense verb) — IMMEDIATELY COMPARE with active tasks. If there's a task matching the MEANING of what they described — call complete_task WITHOUT questions. 'I set up the website' + task 'Set up website for AI indexing' → complete_task. 'Called the doctor' + task 'Make doctor appointment' → complete_task. DON'T MISS these signals!"
                "\nTask involves people → find_relevant_contacts_for_task + set_contact_alert."
                "\nGOALS: 'I want to get X', 'earn Y', 'achieve Z in N months', specific number or deadline → IMMEDIATELY create_goal without asking. Don't discuss the goal — CREATE IT."
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
                "\n🧠 ПОМОГИ ПО СУЩЕСТВУ: когда пользователь ставит задачу или обсуждает проблему — СНАЧАЛА дай конкретные идеи, стратегии, шаги КАК решить. 'Привлечь пользователей' → подскажи 2-3 канала и метода. 'Написать пост' → предложи структуру и крючок. ПОТОМ вызывай инструменты как дополнение. Не своди помощь только к 'найду контакты' или 'обнови профиль'."
                "\nПользователь рассказывает о себе/проекте → ДАЙ ЭКСПЕРТНЫЕ СОВЕТЫ по его нише + update_profile + research_topic(тренды в нише)."
                "\nПользователь упоминает навыки/технологии → update_profile + research_topic(тренды)."
                "\nПользователь говорит о достижении → complete_task + update_goal_progress + предложи create_post."
                "\n🔑 НЕЯВНОЕ ЗАВЕРШЕНИЕ ЗАДАЧ: когда пользователь сообщает что ОН СДЕЛАЛ что-то (настроил, написал, отправил, закончил, разобрался, купил, договорился, позвонил, прошёл, запустил — ЛЮБОЙ глагол совершённого вида) — СРАЗУ СРАВНИ с активными задачами. Если есть задача по СМЫСЛУ совпадающая с тем что он описал — вызови complete_task БЕЗ вопросов. 'Я настроил сайт' + задача 'Настроить сайт для индексации ИИ' → complete_task. 'Позвонил врачу' + задача 'Записаться к врачу' → complete_task. НЕ ПРОПУСКАЙ такие сигналы! Это ГЛАВНЫЙ способ как люди закрывают задачи."
                "\nЗадача связана с людьми → find_relevant_contacts_for_task + set_contact_alert."
                "\nЦЕЛИ: «хочу набрать X», «заработать Y», «достичь Z за N месяцев», конкретная цифра или срок → СРАЗУ create_goal без спроса. Не обсуждай цель — СОЗДАЙ ЕЁ."
                "\nНАПОМИНАНИЯ: «напомни через X минут/часов», «поставь напоминание», «напомни в 15:00» → СРАЗУ add_task с reminder_time. НЕ спрашивай подтверждение — пользователь УЖЕ попросил. Название = суть напоминания из запроса. reminder_time ОБЯЗАТЕЛЕН: передай ТОЧНО как сказал пользователь, например reminder_time='через 5 минут' или reminder_time='в 15:00' или reminder_time='завтра в 10:00'. ⛔ СТРОЖАЙШИЙ ЗАПРЕТ: если пользователь сказал 'через 15 минут' ночью — НЕ ПЕРЕНОСИ на утро! Он РЕШИЛ сам. Ставь ночью. 'через 30 минут' в 02:40 → reminder_time='через 30 минут' (будет 03:10). ЕСЛИ НЕ ПЕРЕДАШЬ reminder_time — задача НЕ СОЗДАСТСЯ."
                "\nНЕЯВНЫЕ ЗАДАЧИ: Пользователь упоминает событие/дело с временем («у меня встреча в 15:00», «завтра дедлайн», «записан к врачу на 10», «в среду презентация») → ПРОВЕРЬ список задач (get_tasks). Если такой задачи НЕТ → ПРЕДЛОЖИ поставить напоминание с конкретным временем (за 15 мин до события). Пример: «Вижу, задачи про встречу нет. Поставить напоминание на 14:45?». Создавай add_task ТОЛЬКО после подтверждения пользователя (да, давай, ок, поставь). Если время неточное («после обеда», «вечером») — сначала уточни конкретное время, потом предложи."
                "\nЯВНЫЕ КОМАНДЫ: «поставь задачу», «создай задачу», «запиши», «добавь в список» + указано время → СРАЗУ add_task. Если время не указано — спроси."
                "\nПРИВЯЗКА К ЦЕЛЯМ: При создании задачи (add_task) ВСЕГДА проверяй — есть ли у пользователя цель, к которой эта задача относится. Если да — передай goal_title. Примеры: задача 'привлечь тестовых пользователей' при цели 'Раскрутить ИИ агента' → add_task(goal_title='Раскрутить нового ИИ агента')."
                "\nТОЧНОСТЬ ВРЕМЕНИ: После edit_task/reschedule_task с изменением времени — ВСЕГДА бери ТОЧНОЕ время из результата инструмента. НИКОГДА не вычисляй и не округляй время сам. Результат содержит строку 'Новое время напоминания: DD.MM.YYYY HH:MM' — используй ИМЕННО это время в ответе пользователю. Пример: результат='Новое время: 20.02.2026 19:47' → отвечай '19:47', а НЕ '19:45'."
                "\n⛔ ЗАПРЕТ НА САМОВОЛЬНОЕ ИЗМЕНЕНИЕ: НИКОГДА не вызывай edit_task, complete_task, delete_task без ЯВНОЙ просьбы пользователя. Ты НЕ ИМЕЕШЬ ПРАВА менять название, описание, время или статус задачи по своей инициативе. Примеры ЗАПРЕЩЁННОГО поведения: пользователь говорит 'пригласил 3 из 5' → ты меняешь задачу 'пригласить 5' на 'собрать фидбек' — это ГРУБЕЙШЕЕ нарушение. Ты можешь ПРЕДЛОЖИТЬ изменение, но ВЫПОЛНИТЬ только после явного согласия ('да, измени', 'да, переименуй')."
                "\n⚠️ ЗАДАЧИ БЕЗ ВРЕМЕНИ: Если в списке задач пользователя есть задачи с пометкой '⚠️ БЕЗ ВРЕМЕНИ' — предложи время через 15-30 минут от текущего момента: 'У задачи X нет времени — поставить на HH:MM?'. Задача без напоминания = задача которую забудут."
                "\n🕐 ВРЕМЯ ПРИ ПРЕДЛОЖЕНИИ ЗАДАЧ — ДВА ЧЁТКИХ ПРАВИЛА:\n1) ПОЛЬЗОВАТЕЛЬ САМ УКАЗАЛ ВРЕМЯ ('через 15 минут', 'в 3 ночи', 'через час', 'в 02:30') → СТАВЬ ТОЧНО КАК СКАЗАЛ, даже ночью. НЕ переноси! Хоть 02:00 ночи — если он говорит 'через 15 минут', ставишь на 02:15. Это его выбор, уважай его.\n2) ВРЕМЯ НЕ УКАЗАНО (ты сам предлагаешь) → до 01:00: предлагай через 15-30 минут; после 01:00: предложи завтра утром.\nЕсли пользователь говорит 'сейчас/прямо сейчас' → reminder_time='сейчас'.\n🚨 ПРОВЕРКА КОНФЛИКТОВ: ПЕРЕД предложением времени ПОСМОТРИ секцию СЕГОДНЯ в контексте. Там видны все задачи с временем (например 'Задача1 (14:00), Задача2 (15:00)'). Если в 14:00 уже занято — НЕ предлагай 14:00, предложи 14:30 или следующий свободный слот. Минимум 15 минут между задачами."
            )

    # ===== ОСНОВНОЙ FLOW =====

    async def process_request(self, user_message, user_id, context=None,
                              session=None, subscription_tier=None,
                              progress_callback=None, web_context: bool = False):
        """
        Адаптивный tool calling loop:
        1. Собираем контекст (1 запрос к БД)
        2. Определяем tool_choice (auto/required)
        3. Tool calling loop (max 5 итераций)
        4. Обучение + сохранение
        """
        # progress_callback хранится локально (не на self) для thread-safety
        _cb = progress_callback

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
            sub_tier = ctx['sub_tier']
            user_lang = ctx.get('user_lang', user_lang)

            # ═══ ИСТОРИЯ ДИАЛОГА (загружаем рано — нужна для anti-repetition) ═══
            from .conversation_history import get_conversation_history
            full_history = get_conversation_history(user_id, session=None, limit=16)

            # ═══ КОГНИТИВНОЕ ОБОГАЩЕНИЕ ═══
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
                base_prompt += cognitive_hints

            # ═══ МУЛЬТИАГЕНТНЫЙ АНАЛИЗ ═══
            try:
                emotion = CognitiveEngine.detect_emotion(user_message)
                intent = CognitiveEngine.classify_intent(user_message)
                
                # Семантическая память из Pinecone — ОТКЛЮЧЕНА (в разработке)
                memory_context = ""
                # try:
                #     memory_context = await build_memory_context(user_id, user_message, max_chars=1200)
                #     if memory_context:
                #         base_prompt += memory_context
                # except Exception as e:
                #     logger.warning(f"[VECTOR] Memory search failed: {e}")
                
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
                    base_prompt += multi_context
                
                # ═══ ИНСТРУКЦИИ ПО ИНСТРУМЕНТАМ ═══
                base_prompt += self._tool_instructions(user_lang)
            except Exception as e:
                logger.warning(f"[MULTI-AGENT] Context build failed: {e}")
            
            # ═══ САМООБУЧЕНИЕ — ПРЕДПОЧТЕНИЯ ПОЛЬЗОВАТЕЛЯ ═══
            try:
                learner = get_learner()
                user_prefs = learner.get_user_preferences(user_id)
                if user_prefs:
                    base_prompt += user_prefs
                
                emotional_trend = learner.get_emotional_trend(user_id)
                if emotional_trend:
                    base_prompt += f"\n{emotional_trend}"
                
                proactive_hint = learner.suggest_proactive_action(user_id, profile_data)
                if proactive_hint:
                    base_prompt += f"\n{proactive_hint}"
            except Exception as e:
                logger.warning(f"[SELF-LEARN] Preferences failed: {e}")

            if len(full_history) > 10:
                old_msgs = full_history[:-8]
                history = full_history[-8:]
                topics = CognitiveEngine.extract_conversation_topics(old_msgs)
                if topics:
                    _lbl = "PREVIOUSLY DISCUSSED" if user_lang == 'en' else "РАНЕЕ ОБСУЖДАЛИ"
                    base_prompt += f"\n\n[{_lbl}: {', '.join(topics)}]"
            else:
                history = full_history

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
                    # Явный @mention — ищем только среди активных, ошибка если не найден
                    _mention_name = _mention_match.group(1).lower()
                    for _cid in _all_active_ids:
                        _cdata = load_agent_personality(_cid)
                        if _cdata and _cdata['name'].lower() == _mention_name:
                            _active_agent_id = _cid
                            _stripped_prefix_end = _mention_match.end()
                            try:
                                set_user_focused_agent(user_id, _cid)
                            except Exception:
                                pass
                            logger.info(f"[AGENT] @mention routed to '{_cdata['name']}' (id={_cid})")
                            break
                    if _active_agent_id is None:
                        _mention_not_found = True
                else:
                    # Имя без @ — ищем совпадение с началом сообщения (тихий роутинг)
                    _first_word = _re_agent.match(r'(\w+)\b', _msg_stripped)
                    if _first_word and _all_active_ids:
                        _fw = _first_word.group(1).lower()
                        for _cid in _all_active_ids:
                            _cdata = load_agent_personality(_cid)
                            if _cdata and _cdata['name'].lower() == _fw:
                                _active_agent_id = _cid
                                _stripped_prefix_end = _first_word.end()
                                try:
                                    set_user_focused_agent(user_id, _cid)
                                except Exception:
                                    pass
                                logger.info(f"[AGENT] name-prefix routed to '{_cdata['name']}' (id={_cid})")
                                break

                # Режим офиса: ASI главный по умолчанию.
                # Кастомный агент включается только при явном @упоминании или имени-префиксе.
                # Используем focused_agent только если пользователь явно переключился (explicit /use).
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
                        except Exception:
                            pass
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
                        except Exception:
                            pass

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
                        try:
                            stdout, stderr = await _aio_pc.wait_for(proc.communicate(), timeout=10.0)
                            out = stdout.decode('utf-8', errors='replace').strip()[:2000]
                            err = stderr.decode('utf-8', errors='replace').strip()[:500]
                            return out, err
                        except _aio_pc.TimeoutError:
                            proc.kill()
                            return '', 'Тайм-аут выполнения скрипта (10 сек)'
                    _code_output, _code_stderr = await _run_agent_code()
                    if _code_output:
                        base_prompt += (
                            f'\n\n[ДАННЫЕ ОТ АГЕНТА — РЕАЛЬНЫЕ ДАННЫЕ ПРЯМО СЕЙЧАС]\n'
                            f'Твой скрипт выполнился и вернул данные ниже. '
                            f'Это ТВОИ данные — воспринимай их как собственное знание о текущей ситуации, '
                            f'а не как внешний ввод. Действуй проактивно: если видишь важные события — '
                            f'сообщи о них ПЕРВЫМ. Суммируй ключевые цифры, дай одну конкретную рекомендацию. '
                            f'Используй встроенные инструменты (add_task, create_goal и др.) на основе этих данных. '
                            f'НЕ говори «нужно настроить подключение» — данные уже получены.\n'
                            f'───────────────\n'
                            f'{_code_output}\n'
                            f'───────────────'
                        )
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
                                    asyncio.get_event_loop().run_in_executor(
                                        None,
                                        lambda: spawn_integration_anchors(_uid_ia, _adp_ia, _svc_ia, _out_ia)
                                    )
                            finally:
                                _al_s.close()
                        except Exception as _al_e:
                            logger.warning(f"[AGENT] activity log (success) error: {_al_e}")
                    else:
                        _err_detail = _code_stderr if _code_stderr else 'скрипт не вернул вывода'
                        base_prompt += (
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
                    base_prompt += (
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
                                    title=f'❌ {_agent_data.get("name","Агент")}: скрипт не запустился',
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
            if history:
                messages.extend(history)
            messages.append({"role": "user", "content": user_message})

            # Адаптивный tool_choice (с учётом профиля и задач)
            initial_tool_choice = self._determine_tool_choice(
                user_message, profile_data=profile_data, tasks_data=tasks_data
            )

            # ===== Tool calling loop (max 2 итераций для скорости) =====
            all_execution_results = []
            MAX_ITERATIONS = 2
            MAX_TOOLS_PER_ITERATION = 3  # Лимит инструментов за одну итерацию
            seen_tools = set()  # Для предотвращения дублей
            # Критичные инструменты — лимит вызовов за сессию
            once_only_tools = {'create_post', 'delete_post', 'delegate_task', 'start_email_campaign', 'start_content_campaign', 'start_delegation_campaign'}  # строго 1 раз
            multi_limit_tools = {'add_task': 3, 'add_email_leads': 3, 'update_profile': 2, 'create_goal': 2, 'run_agent_action': 5, 'send_email': 1, 'send_outreach_email': 3}  # лимиты per turn
            used_once_only = set()
            multi_limit_counts = {}

            # Smart tool filtering — reduce tokens sent to API
            tools_to_exclude = self._select_tools_for_message(user_message)
            # run_agent_action доступен только когда активен агент со скриптом
            _cur_agent = self._active_agent_data.get(user_id)
            if not _cur_agent or not _cur_agent.get('python_code', '').strip():
                tools_to_exclude.add('run_agent_action')
            else:
                # Агент со скриптом: скрываем run_user_script чтобы не конкурировал
                tools_to_exclude.add('run_user_script')

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
                except Exception:
                    pass

            for iteration in range(MAX_ITERATIONS):
                # Первая итерация может быть "required", остальные "auto"
                tc = initial_tool_choice if iteration == 0 else "auto"

                # Обновляем прогресс перед вызовом AI
                if _cb and iteration > 0:
                    try:
                        await _cb(random.choice(self._get_deep_thinking_phrases(user_lang)))
                    except Exception:
                        pass

                # Если уже есть результаты инструментов — финальный ответ без tools
                # (убирает ~40 определений инструментов из запроса → значительно быстрее)
                _has_results = bool(all_execution_results)
                response = await self.call_ai(
                    messages,
                    use_tools=not _has_results,
                    subscription_tier=sub_tier,
                    tool_choice=tc if not _has_results else None,
                    max_tokens=600,
                    exclude_tools=tools_to_exclude if not _has_results else None)

                msg = response['choices'][0]['message']
                content = msg.get('content', '')
                tool_calls = msg.get('tool_calls', [])

                if not tool_calls:
                    # AI ответил текстом → сразу возвращаем (retry убран для скорости)
                    return self._finalize_response(
                        content, user_message, user_id, all_execution_results)

                # AI вызвал tools → добавляем assistant message в цепочку
                messages.append(msg)

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

                # ── Pass 2: выполняем все валидные tools ПАРАЛЛЕЛЬНО ────────────
                # Каждый вызов получает отдельную DB-сессию (session=None → auto)
                async def _exec_one(_tc, _name, _args, _reason):
                    if _cb:
                        try:
                            await _cb(self._tool_progress_text(_name, iteration + 1, lang=user_lang))
                        except Exception:
                            pass
                    try:
                        _results = await self.execute_actions(
                            [{"tool": _name, "params": _args, "reason": _reason}],
                            user_id, session=None,
                            user_message=user_message, web_context=web_context)
                        _r = _results[0] if _results else {"success": False, "error": "no result"}
                        if _r.get('success'):
                            _rc = json.dumps(_r['result'], ensure_ascii=False, default=str)
                            _rc = CognitiveEngine.compress_tool_result(_rc)
                        else:
                            _rc = json.dumps({"error": str(_r.get('error', ''))}, ensure_ascii=False)
                    except Exception as _err:
                        logger.error(f"[EXEC] {_name} parallel crashed: {_err}\n{traceback.format_exc()}")
                        _r = {"success": False, "error": str(_err)}
                        _rc = json.dumps({"error": str(_err)}, ensure_ascii=False)
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

                # Продолжаем цикл — AI увидит результаты и решит
                # ответить текстом или вызвать ещё tools

            # Если вышли из цикла — финальный вызов без tools
            if user_lang == 'en':
                _final_instr = (
                    "Formulate the final response. IMPORTANT: rephrase tool data IN YOUR OWN WORDS, "
                    "weave into natural conversational text. Don't copy format, bullets, emoji headers from results. "
                    "If a tool found nothing useful — don't mention it. "
                    "If you sent an email — report BRIEFLY who you wrote to and the topic, do NOT paste the email body. "
                    "PRESERVE ALL URLs from tool results — user needs clickable links. "
                    "Put links on separate lines at the end: Title — URL. "
                    "Write ONLY in English."
                )
            else:
                _final_instr = (
                    "Сформируй КРАТКИЙ финальный ответ (3-5 предложений, макс 150 слов). "
                    "Перескажи данные из инструментов СВОИМИ СЛОВАМИ, вплети в живой разговорный текст. "
                    "НЕ копируй формат, bullets, emoji-заголовки из результатов. "
                    "Если инструмент не нашёл полезного — не упоминай это. "
                    "Если отправлял email — сообщи КРАТКО кому написал и тему, НЕ вставляй текст письма. "
                    "ОБЯЗАТЕЛЬНО СОХРАНЯЙ URL-ССЫЛКИ из результатов — ссылки отдельными строками в конце. "
                    "НЕ ПИШИ длинных абзацев — будь лаконичен как друг в мессенджере."
                )
            messages.append({
                "role": "user",
                "content": _final_instr
            })
            final_resp = await self.call_ai(
                messages, use_tools=False, temperature=0.7, max_tokens=500)
            final_text = final_resp['choices'][0]['message'].get('content') or ''
            return self._finalize_response(
                final_text, user_message, user_id, all_execution_results)

        except Exception as e:
            logger.error(f"[AGENT] Error: {e}\n{traceback.format_exc()}")
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
                    "Что-то пошло не так. Перефразируй запрос.",
                    "Техническая ошибка. Попробуй ещё раз.",
                    "Упс, сбой. Скажи то же самое другими словами.",
                    "Технические неполадки. Давай попробуем по-другому.",
                    "Что-то сломалось. Перефразируй, пожалуйста.",
                ]
            return random.choice(error_responses)

    # ===== КОГНИТИВНАЯ ФИНАЛИЗАЦИЯ =====

    def _finalize_response(self, content, user_message, user_id, execution_results):
        """Clean → validate → save → return.
        
        Единая точка выхода: чистка тех. деталей, когнитивная валидация
        (убирает шаблонные начала, markdown, автоответчик, списки),
        сохранение в историю и обучение.
        """
        from .utils import clean_technical_details
        from .cognitive import CognitiveEngine
        from i18n import get_user_lang

        final = clean_technical_details(content or '').strip()
        if not final:
            _lang = get_user_lang(user_id)
            final = "Done!" if _lang == 'en' else "Готово!"

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
                    final = f"⚠️ {bill_result['error']}\n\nВозвращаюсь в стандартный режим ASI Biont."
        except Exception as _be:
            logger.warning(f"[BILLING] agent billing error: {_be}")

        # Когнитивная валидация (quality gate)
        final, issues = CognitiveEngine.validate_response(final, user_message)
        if issues:
            logger.info(f"[COGNITIVE] Response fixed: {issues}")

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

        self._save_and_learn(user_message, user_id, execution_results, final)
        return final

    # ===== ОБУЧЕНИЕ И АДАПТАЦИЯ =====

    def _save_and_learn(self, user_message, user_id, execution_results, response):
        """Сохраняет в историю, обучается на результатах, обновляет паттерны."""
        
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
        if len(self.execution_history) > 50:
            self.execution_history = self.execution_history[-50:]

        # === Ответ в историю диалога ===
        from .conversation_history import save_message_to_history
        save_message_to_history(user_id, "assistant", response)

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

        # === Семантическая память (Pinecone) — ОТКЛЮЧЕНА (в разработке) ===
        # try:
        #     from .cognitive import CognitiveEngine
        #     emotion = CognitiveEngine.detect_emotion(user_message)
        #     intent = CognitiveEngine.classify_intent(user_message)
        #     asyncio.get_event_loop().create_task(
        #         store_conversation_turn(
        #             user_id=user_id,
        #             user_message=user_message,
        #             bot_response=response,
        #             emotion=emotion,
        #             intent=intent
        #         )
        #     )
        # except Exception as e:
        #     logger.warning(f"[VECTOR] Store failed: {e}")

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
            asyncio.get_event_loop().create_task(_self_learn_bg())
        except Exception:
            pass

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
                                       extra_context=None, max_tokens=1000,
                                       max_iterations=2):
        """Генерация системного сообщения (напоминание, проактивное, поздравление)
        через тот же мозг с tool calling, но без сохранения в историю диалога.

        Args:
            user_id: telegram ID пользователя
            mode: 'reminder' | 'proactive' | 'result_check'
            instruction: текст задания для AI (что сгенерировать)
            extra_context: дополнительный контекст (ситуация, красные флаги и т.д.)
            max_tokens: лимит токенов (короткие сообщения = меньше)
            max_iterations: макс. итераций tool calling (2 для скорости)

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
            sub_tier = ctx['sub_tier']
            user_lang = ctx.get('user_lang', 'ru')

            # Добавляем режим в системный промпт (bilingual)
            if user_lang == 'en':
                mode_instructions = {
                    'reminder': (
                        "\n\n[MODE: REMINDER]\n"
                        "Task time arrived. Think: can you HELP solve it, not just remind?\n"
                        "Task needs info → find via tools and deliver the result.\n"
                        "Simple task → remind briefly. Ask about status. Do NOT create new tasks.\n"
                        "Follow the style rules from the system prompt above — alive, conversational, no formality."
                    ),
                    'task_assist': (
                        "\n\n[MODE: TASK ASSIST]\n"
                        "Help solve the task — don't suggest, DO it.\n"
                        "Use tools and give a concrete result.\n"
                        "Do NOT create new tasks. Follow the style rules from the system prompt above."
                    ),
                    'proactive': (
                        "\n\n[MODE: PROACTIVE MESSAGE]\n"
                        "You decided to write — but the user must NOT feel it's a system message. "
                        "Follow the style rules from the system prompt above: alive, conversational, same tone as regular replies.\n"
                        "Think: what matters to this person now? Which area of life needs attention?\n"
                        "Use tools (list_tasks, list_goals, get_news_trends) for real data. "
                        "Do NOT call research_topic in proactive messages — use get_news_trends.\n"
                        "Don't invent data. End with a thought-provoking question.\n"
                        "IMPORTANT: Do NOT auto-publish posts. Do NOT use /dashboard — only https://asibiont.com/dashboard.\n\n"
                        "⚠️ DATA VERIFICATION RULE:\n"
                        "USER MEMORY section is outdated background. Do NOT cite tasks, goals, posts from it as current.\n"
                        "ONLY data from tools (list_tasks, list_goals) is current.\n"
                        "If list_tasks returns empty — user has NO tasks. Don't mention tasks.\n"
                        "If list_goals returns empty — user has NO goals. Don't mention goals.\n"
                        "Do NOT mention specific tasks/goals/posts you did NOT get from tools."
                    ),
                    'result_check': (
                        "\n\n[MODE: CONGRATULATION]\n"
                        "Task completed — congratulate briefly. 1-2 sentences. Follow the style rules from the system prompt above."
                    ),
                    'anchor': (
                        "\n\n[MODE: ANCHOR ENGINE]\n"
                        "You received ANCHORS (events/facts) + full user context.\n"
                        "Think: is there something worth interrupting the user for?\n"
                        "If not — return SKIP. Don't write just to write.\n"
                        "If yes — use tools (get_news_trends, list_tasks, list_goals) "
                        "on the relevant topic. Do NOT call research_topic — use get_news_trends.\n"
                        "Follow the style rules from the system prompt above — alive, conversational. "
                        "User must not distinguish proactive from regular reply.\n"
                        "ONE TOPIC PER MESSAGE. End with a specific question or suggestion.\n"
                        "IMPORTANT: Do NOT auto-publish posts. Do NOT use /dashboard — only https://asibiont.com/dashboard.\n\n"
                        "⚠️ DATA VERIFICATION RULE:\n"
                        "USER MEMORY section is outdated background. Do NOT cite tasks, goals, posts from it as current.\n"
                        "ONLY data from tools (list_tasks, list_goals) is current.\n"
                        "If list_tasks returns empty — user has NO tasks. Don't mention tasks.\n"
                        "If list_goals returns empty — user has NO goals. Don't mention goals.\n"
                        "Do NOT mention specific tasks/goals/posts you did NOT get from tools."
                    ),
                }
            else:
                mode_instructions = {
                    'reminder': (
                        "\n\n[РЕЖИМ: НАПОМИНАНИЕ]\n"
                        "Время задачи пришло. Подумай: можешь ли ты ПОМОЧЬ решить, а не просто напомнить?\n"
                        "Задача требует информации → найди через инструменты и дай результат.\n"
                        "Задача простая → напомни кратко. Спроси о статусе. НЕ создавай новые задачи.\n"
                        "Следуй правилам стиля из главного промта — живо, разговорно, без формальностей."
                    ),
                    'task_assist': (
                        "\n\n[РЕЖИМ: ПОМОЩЬ С ЗАДАЧЕЙ]\n"
                        "Помоги решить задачу — не предлагай, а СДЕЛАЙ.\n"
                        "Используй инструменты и дай конкретный результат.\n"
                        "НЕ создавай новые задачи. Следуй правилам стиля из главного промта."
                    ),
                    'proactive': (
                        "\n\n[РЕЖИМ: ПРОАКТИВНОЕ СООБЩЕНИЕ]\n"
                        "Ты сам решил написать — но человек НЕ ДОЛЖЕН чувствовать, что это системное сообщение. "
                        "Следуй правилам стиля из главного промта: живо, разговорно, тот же тон.\n"
                        "Подумай: что сейчас важно для этого человека? Какая сфера его жизни требует внимания?\n"
                        "Используй инструменты (list_tasks, list_goals, get_news_trends) для получения реальных данных. "
                        "НЕ вызывай research_topic — используй get_news_trends.\n"
                        "Не выдумывай данные. Задай вопрос, который заставит задуматься.\n"
                        "ВАЖНО: НЕ публикуй посты автоматически. НЕ используй /dashboard — только https://asibiont.com/dashboard.\n\n"
                        "⚠️ ПРАВИЛО ВЕРИФИКАЦИИ ДАННЫХ:\n"
                        "Секция ПАМЯТЬ ПОЛЬЗОВАТЕЛЯ — это устаревший фон. НЕ цитируй из неё задачи, цели, посты или факты как текущие.\n"
                        "ТОЛЬКО данные из инструментов (list_tasks, list_goals) считай актуальными.\n"
                        "Если list_tasks вернул пустой список — у пользователя НЕТ задач. Не упоминай задачи.\n"
                        "Если list_goals вернул пустой список — у пользователя НЕТ целей. Не упоминай цели.\n"
                        "НЕ УПОМИНАЙ конкретные задачи/цели/посты которые ты НЕ получил из инструментов."
                    ),
                    'result_check': (
                        "\n\n[РЕЖИМ: ПОЗДРАВЛЕНИЕ]\n"
                        "Задача выполнена — поздравь кратко. 1-2 предложения. Следуй правилам стиля из главного промта."
                    ),
                    'anchor': (
                        "\n\n[РЕЖИМ: ANCHOR ENGINE]\n"
                        "Тебе переданы ЯКОРЯ (события/факты) + полный контекст человека.\n"
                        "Подумай: есть ли здесь что-то, ради чего стоит отвлечь человека?\n"
                        "Если нет — верни SKIP. Не пиши ради того, чтобы написать.\n"
                        "Если да — используй инструменты (get_news_trends, list_tasks, list_goals) "
                        "по релевантной теме. НЕ вызывай research_topic — используй get_news_trends.\n"
                        "Следуй правилам стиля из главного промта — живо, разговорно. "
                        "Человек не должен отличить проактивное сообщение от обычного ответа.\n"
                        "ОДНА ТЕМА НА СООБЩЕНИЕ. Закончи конкретным вопросом или предложением.\n"
                        "ВАЖНО: НЕ публикуй посты автоматически. НЕ используй /dashboard — только https://asibiont.com/dashboard.\n\n"
                        "⚠️ ПРАВИЛО ВЕРИФИКАЦИИ ДАННЫХ:\n"
                        "Секция ПАМЯТЬ ПОЛЬЗОВАТЕЛЯ — это устаревший фон. НЕ цитируй из неё задачи, цели, посты или факты как текущие.\n"
                        "ТОЛЬКО данные из инструментов (list_tasks, list_goals) считай актуальными.\n"
                        "Если list_tasks вернул пустой список — у пользователя НЕТ задач. Не упоминай задачи.\n"
                        "Если list_goals вернул пустой список — у пользователя НЕТ целей. Не упоминай цели.\n"
                        "НЕ УПОМИНАЙ конкретные задачи/цели/посты которые ты НЕ получил из инструментов."
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

            # Если есть extra_context (ситуация, красные флаги) — добавляем
            if extra_context:
                ctx_label = "[SITUATION CONTEXT]" if user_lang == 'en' else "[КОНТЕКСТ СИТУАЦИИ]"
                messages.append({
                    "role": "user",
                    "content": f"{ctx_label}\n{extra_context}"
                })

            messages.append({"role": "user", "content": instruction})

            # Определяем какие инструменты ИСКЛЮЧИТЬ по режиму
            exclude_tools = set()
            if mode == 'reminder':
                exclude_tools = {'add_task', 'create_goal', 'delegate_task'}
            elif mode == 'task_assist':
                exclude_tools = {'add_task', 'create_goal', 'delegate_task'}
            elif mode == 'result_check':
                exclude_tools = {'add_task', 'create_goal', 'delegate_task',
                                 'edit_task', 'reschedule_task'}
            elif mode == 'proactive':
                exclude_tools = {'delegate_task'}
            elif mode == 'anchor':
                exclude_tools = {'add_task', 'create_goal', 'delegate_task'}

            # ===== Tool calling loop (облегчённый) =====
            all_execution_results = []
            seen_tools = set()

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

                    # Blocked
                    if name in exclude_tools:
                        messages.append({"role": "tool", "tool_call_id": tc_item['id'],
                            "content": f'{{"status": "blocked: {name} not in {mode}"}}'})
                        continue

                    _sys_ready.append((tc_item, name, args, f"system:{mode} iter {iteration+1}"))

                # ── Pass 2: выполняем ПАРАЛЛЕЛЬНО ────────────────────────────────
                async def _sys_exec_one(_tc, _name, _args, _reason):
                    try:
                        _results = await self.execute_actions(
                            [{"tool": _name, "params": _args, "reason": _reason}],
                            user_id, session=None, user_message=instruction)
                        _r = _results[0] if _results else {"success": False, "error": "no result"}
                        if _r.get('success'):
                            _rc = json.dumps(_r['result'], ensure_ascii=False, default=str)[:1500]
                        else:
                            _rc = json.dumps({"error": str(_r.get('error', ''))}, ensure_ascii=False)
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
            return "Great, task completed! 👍" if lang == 'en' else "Отлично, задача выполнена! 👍"
        elif mode == 'anchor':
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


async def chat_with_ai(message, context=None, user_id=None, file_content=None,
                       db_session=None, message_type=None, subscription_tier=None,
                       progress_callback=None, web_context: bool = False):
    """Главная точка входа. Совместима со всеми вызовами в проекте."""
    logger.info(f"[AGENT] START user={user_id} msg='{str(message)[:50]}...'")

    if user_id is None:
        return {'response': "Ошибка: пользователь не найден", 'tool_calls': []}

    try:
        agent = get_autonomous_agent()
        history_len = len(agent.execution_history)

        response_text = await agent.process_request(
            message, user_id, context, db_session,
            subscription_tier, progress_callback=progress_callback,
            web_context=web_context)

        # Извлекаем tool_calls для тестов и мониторинга
        tool_calls = []
        tools_used = []
        if len(agent.execution_history) > history_len:
            last = agent.execution_history[-1]
            for r in last.get('results', []):
                if r.get('success'):
                    tools_used.append(r['tool'])
                    tool_calls.append({
                        'function': {
                            'name': r['tool'],
                            'arguments': json.dumps(r.get('params', {}))
                        }
                    })

        # Определяем кто ответил: кастомный агент или ASI
        _answered_agent = agent._active_agent_data.get(user_id)
        agent_info = None
        if _answered_agent:
            # Загружаем avatar_url из БД (не хранится в _active_agent_data)
            try:
                from models import Session as _Sess, UserAgent as _UA
                _s = _Sess()
                try:
                    _db_agent = _s.query(_UA).filter_by(id=_answered_agent['id']).first()
                    _avatar = _db_agent.avatar_url if _db_agent else None
                finally:
                    _s.close()
            except Exception:
                _avatar = None
            agent_info = {
                'id': _answered_agent.get('id'),
                'name': _answered_agent.get('name', 'Агент'),
                'job_title': _answered_agent.get('job_title', ''),
                'avatar_url': _avatar or '',
            }

        return {
            'response': response_text,
            'tool_calls': tool_calls,
            'tools_used': tools_used,
            'agent_info': agent_info,
        }

    except Exception as e:
        logger.error(f"[AGENT] ERROR: {e}\n{traceback.format_exc()}")
        return {
            'response': f"Извините, произошла ошибка: {str(e)}",
            'tool_calls': [],
            'agent_info': None,
        }
