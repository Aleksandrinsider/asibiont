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
# Auto-strip spaces from App Passwords (Gmail App Password: xxxx xxxx xxxx xxxx -> xxxxxxxxxxxxxxxx)
import os as _fix_os
for _fix_k in list(_fix_os.environ.keys()):
    if "PASS" in _fix_k:
        _fix_os.environ[_fix_k] = _fix_os.environ[_fix_k].replace(" ", "")
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
         _max_retries = 1 if (api_timeout and api_timeout < 30) else 2
         for _attempt in range(_max_retries):
          try:
            async with aiohttp.ClientSession() as session:
                    async with session.post(url, headers=headers, json=data,
                                            timeout=aiohttp.ClientTimeout(total=api_timeout or 120)) as resp:
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
        'start_delegation_campaign',
    }

    # Extended tool groups — activated by keywords in user message
    TOOL_GROUPS = {
        'email': {
            'keywords': ['email', 'e-mail', 'почт', 'письм', 'отправ', 'письм', 'переписк', 'перегов'],
            'tools': {'send_email', 'negotiate_by_email', 'list_email_contacts', 'save_email_contact'},
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
                stdout, stderr = await _aio_ea.wait_for(proc.communicate(), timeout=float(API_TIMEOUT_NORMAL))
                out = stdout.decode('utf-8', errors='replace').strip()[:2000]
                err = stderr.decode('utf-8', errors='replace').strip()[:500]
            except _aio_ea.TimeoutError:
                proc.kill()
                return {"status": "error", "error": f"Timeout ({API_TIMEOUT_NORMAL}s) — скрипт выполнялся слишком долго"}
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
                
                # Семантическая память из Pinecone
                memory_context = ""
                try:
                    memory_context = await build_memory_context(user_id, user_message, max_chars=1200)
                    if memory_context:
                        base_prompt += memory_context
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
                    # Явный @mention — ищем среди активных агентов
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
                                            except Exception:
                                                pass
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
                                except Exception:
                                    pass
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
                                                except Exception:
                                                    pass
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
                            stdout, stderr = await _aio_pc.wait_for(proc.communicate(), timeout=float(API_TIMEOUT_QUICK))
                            out = stdout.decode('utf-8', errors='replace').strip()[:2000]
                            err = stderr.decode('utf-8', errors='replace').strip()[:500]
                            return out, err
                        except _aio_pc.TimeoutError:
                            proc.kill()
                            return '', f'Тайм-аут выполнения скрипта ({API_TIMEOUT_QUICK} сек)'
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
            if history:
                messages.extend(history)
            messages.append({"role": "user", "content": user_message})

            # Адаптивный tool_choice (с учётом профиля и задач)
            initial_tool_choice = self._determine_tool_choice(
                user_message, profile_data=profile_data, tasks_data=tasks_data
            )

            # ===== Tool calling loop (max 5 итераций) =====
            all_execution_results = []
            MAX_ITERATIONS = 5
            MAX_TOOLS_PER_ITERATION = 3  # Лимит инструментов за одну итерацию
            seen_tools = set()  # Для предотвращения дублей
            # Критичные инструменты — лимит вызовов за сессию
            once_only_tools = {'create_post', 'delete_post', 'start_content_campaign', 'start_delegation_campaign'}  # строго 1 раз
            multi_limit_tools = {'add_task': 3, 'update_profile': 2, 'create_goal': 2, 'run_agent_action': 5, 'send_email': 1, 'delegate_task': 4}  # лимиты per turn
            used_once_only = set()
            multi_limit_counts = {}

            # Smart tool filtering — reduce tokens sent to API
            tools_to_exclude = self._select_tools_for_message(user_message)
            # Дополнительные запрещённые инструменты от вызывающего кода (напр. при обзоре отчита агента)
            if exclude_tools:
                tools_to_exclude = tools_to_exclude | set(exclude_tools)
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
                # Разрешаем инструменты на всех итерациях кроме последней
                # (на последней принудительно берём финальный ответ без tools)
                _is_last_iter = (iteration >= MAX_ITERATIONS - 1)
                _allow_tools = not _is_last_iter
                response = await self.call_ai(
                    messages,
                    use_tools=_allow_tools,
                    subscription_tier=sub_tier,
                    tool_choice=tc if _allow_tools else None,
                    max_tokens=600,
                    exclude_tools=tools_to_exclude if _allow_tools else None)

                msg = response['choices'][0]['message']
                content = msg.get('content', '')
                tool_calls = msg.get('tool_calls', [])

                if not tool_calls:
                    # AI ответил текстом → сразу возвращаем (retry убран для скорости)
                    return self._finalize_response(
                        content, user_message, user_id, all_execution_results)

                # AI вызвал tools → добавляем assistant message в цепочку
                messages.append(msg)

                # Показываем «думаю вслух» — частичный текст AI до вызова инструментов
                if content.strip() and _cb:
                    try:
                        _preview = content.strip()[:200]
                        if len(content.strip()) > 200:
                            _preview += '...'
                        await _cb(_preview)
                    except Exception:
                        pass

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
                    # ── Пре-анонс для delegate_task (как живое обращение к агенту) ──
                    if _cb and _name == 'delegate_task':
                        _pre_exec = _args.get('delegated_to_username', '') or ''
                        _pre_task = _args.get('title', '') or _args.get('description', '') or ''
                        if _pre_exec and _pre_task:
                            _pre_vis = f"{_pre_exec}, {_pre_task[:120]}"
                        elif _pre_exec:
                            _pre_vis = f"{_pre_exec}, займись этим"
                        else:
                            _pre_vis = None
                        if _pre_vis:
                            try:
                                await _cb(_pre_vis, persist=True)
                            except Exception:
                                pass
                    elif _cb:
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
                            # ── Промежуточное сообщение для ключевых действий ──
                            if _cb and _name in ('delegate_task', 'research_topic', 'run_agent_action',
                                                  'create_post', 'get_delegation_progress', 'add_task'):
                                try:
                                    _res_obj = _r.get('result', {})
                                    if isinstance(_res_obj, str):
                                        try: _res_obj = json.loads(_res_obj)
                                        except Exception: _res_obj = {}
                                    if _name == 'delegate_task':
                                        _exec_name = _args.get('delegated_to_username', '') or _res_obj.get('executor', '') or ''
                                        # Показываем результат как сообщение от агента
                                        _result_text = str(_r.get('result', ''))[:300]
                                        if _exec_name and _result_text and len(_result_text) > 20:
                                            _vis = f"{_exec_name}: {_result_text}"
                                        elif _exec_name:
                                            _vis = f"{_exec_name}: задача выполнена"
                                        else:
                                            _vis = None
                                    elif _name == 'research_topic':
                                        _q = _args.get('query', '') or _args.get('topic', '') or ''
                                        _vis = f"Исследую: {_q[:80]}" if _q else "Провожу исследование..."
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
                                        await _cb(_vis, persist=True)
                                except TypeError:
                                    try:
                                        if _vis:
                                            await _cb(_vis)
                                    except Exception: pass
                                except Exception:
                                    pass
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
                    final = f"{bill_result['error']}\n\nВозвращаюсь в стандартный режим ASI Biont."
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

        # Защита от слишком коротких ответов после tool calls
        # Если AI ответил "Готово!" после делегирования — формируем развёрнутый ответ из результатов
        if tools_used and len((final or '').strip()) < 40:
            _tool_results_summary = []
            for _r in execution_results:
                if _r.get('success') and _r.get('result'):
                    _rtext = str(_r['result'])[:300]
                    _tname = _r.get('tool', '')
                    if _tname == 'delegate_task':
                        _tool_results_summary.append(f"Поручено: {_rtext}")
                    elif _tname == 'research_topic':
                        _tool_results_summary.append(f"Исследование: {_rtext}")
                    elif _tname == 'get_delegation_progress':
                        _tool_results_summary.append(f"Статус: {_rtext}")
                    elif _tname:
                        _tool_results_summary.append(f"{_tname}: {_rtext}")
            if _tool_results_summary:
                final = ". ".join(_tool_results_summary[:3])
                logger.info(f"[QUALITY] Replaced terse response with tool summaries: {len(final)} chars")

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
                        "STYLE (STRICT): write like a real friend in a chat app. 300–500 characters, flowing text.\n"
                        "FORBIDDEN: bullet lists (• – – 1.), numbered lists, headers (##), double newlines, greetings ('Hi!', 'Good morning').\n"
                        "FORBIDDEN to start with 'Reminder about task' — sounds like a bot, not a living agent.\n"
                        "First word must be the task name or an action verb. Emoji: 1–2 inline, NOT at the start of lines."
                    ),
                    'task_assist': (
                        "\n\n[MODE: TASK ASSIST]\n"
                        "Help solve the task — don't suggest, DO it.\n"
                        "Use tools and give a concrete result.\n"
                        "Do NOT create new tasks.\n"
                        "STYLE: 300–800 characters, flowing text, single newlines between paragraphs. FORBIDDEN: bullet lists, numbered lists, headers, double newlines."
                    ),
                    'proactive': (
                        "\n\n[MODE: PROACTIVE MESSAGE]\n"
                        "You decided to write — but the user must NOT feel it's a system message. "
                        "Write EXACTLY like a regular chat reply: alive, direct, conversational, with character.\n"
                        "Think: what matters to this person now? Which area of life needs attention?\n"
                        "Use tools (list_tasks, list_goals, get_news_trends) for real data. "
                        "Do NOT call research_topic in proactive messages — use get_news_trends.\n"
                        "Don't invent data. End with a thought-provoking question.\n"
                        "IMPORTANT: Do NOT auto-publish posts. Do NOT use /dashboard — only https://asibiont.com/dashboard.\n\n"
                        "STYLE (STRICT — same rules as regular chat):\n"
                        "• 300–500 characters, flowing text, 2–3 paragraphs\n"
                        "• FORBIDDEN: bullet lists (• – – 1.), numbered lists, headers (##), double newlines\n"
                        "• FORBIDDEN to start with 'Hi', 'Good morning', any time-of-day or weekday opener\n"
                        "• Emoji: 1–2 inline, NOT at the start of lines. First word: noun or action verb.\n\n"
                        "⚠️ DATA VERIFICATION RULE:\n"
                        "USER MEMORY section is outdated background. Do NOT cite tasks, goals, posts from it as current.\n"
                        "ONLY data from tools (list_tasks, list_goals) is current.\n"
                        "If list_tasks returns empty — user has NO tasks. Don't mention tasks.\n"
                        "If list_goals returns empty — user has NO goals. Don't mention goals.\n"
                        "Do NOT mention specific tasks/goals/posts you did NOT get from tools."
                    ),
                    'result_check': (
                        "\n\n[MODE: CONGRATULATION]\n"
                        "Task completed. React naturally — like a real friend, not a system bot. 1–2 sentences, max 200 characters.\n"
                        "FORBIDDEN to start with 'Congratulations!' — find your own reaction, with character.\n"
                        "Style: conversational, emotional, light irony if appropriate. No lists, no headers."
                    ),
                    'anchor': (
                        "\n\n[MODE: ANCHOR ENGINE]\n"
                        "You received ANCHORS (events/facts) + full user context.\n"
                        "Think: is there something worth interrupting the user for?\n"
                        "If not — return SKIP. Don't write just to write.\n"
                        "If yes — use tools (get_news_trends, list_tasks, list_goals) on the relevant topic. Do NOT call research_topic — use get_news_trends.\n"
                        "ONE TOPIC PER MESSAGE. End with a specific question or suggestion.\n"
                        "IMPORTANT: Do NOT auto-publish posts. Do NOT use /dashboard — only https://asibiont.com/dashboard.\n\n"
                        "STYLE (STRICT — identical to regular chat):\n"
                        "• 300–500 characters, flowing text. User must not distinguish anchor from regular reply.\n"
                        "• FORBIDDEN: bullet lists (• – – 1.), numbered lists, headers (##), double newlines\n"
                        "• FORBIDDEN to start with 'Hi', 'Good morning', any time-of-day opener\n"
                        "• Emoji: 1–2 inline, NOT at start of lines. First word: noun or action verb.\n\n"
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
                        "СТИЛЬ (КРИТИЧНО): пиши как живой друг в мессенджере. 300–500 символов, сплошной текст.\n"
                        "ЗАПРЕЩЕНО: списки (• – – 1.), нумерация, заголовки (##), двойные переносы строк (\\n\\n), приветствия ('Привет!', 'Добрый день').\n"
                        "ЗАПРЕЩЕНО начинать с 'Напоминание о задаче' — это звучит как системный бот, а не живой агент.\n"
                        "Первое слово — суть задачи или глагол действия. Эмодзи 1–2 внутри текста, НЕ в начале строк."
                    ),
                    'task_assist': (
                        "\n\n[РЕЖИМ: ПОМОЩЬ С ЗАДАЧЕЙ]\n"
                        "Помоги решить задачу — не предлагай, а СДЕЛАЙ.\n"
                        "Используй инструменты и дай конкретный результат.\n"
                        "НЕ создавай новые задачи.\n"
                        "СТИЛЬ: 300–800 символов, сплошной текст, одинарные переносы между абзацами. ЗАПРЕЩЕНО: списки, нумерация, заголовки, двойные переносы."
                    ),
                    'proactive': (
                        "\n\n[РЕЖИМ: ПРОАКТИВНОЕ СООБЩЕНИЕ]\n"
                        "Ты сам решил написать — но человек НЕ ДОЛЖЕН чувствовать, что это системное сообщение. "
                        "Пиши ТОЧНО как в обычном чате: живо, разговорно, прямо, с характером.\n"
                        "Подумай: что сейчас важно для этого человека? Какая сфера его жизни требует внимания?\n"
                        "Используй инструменты (list_tasks, list_goals, get_news_trends) для получения реальных данных. "
                        "НЕ вызывай research_topic — используй get_news_trends.\n"
                        "Не выдумывай данные. Задай вопрос, который заставит задуматься.\n"
                        "ВАЖНО: НЕ публикуй посты автоматически. НЕ используй /dashboard — только https://asibiont.com/dashboard.\n\n"
                        "СТИЛЬ (СТРОГО — те же правила что и в обычном чате):\n"
                        "• 300–500 символов, сплошной текст, 2–3 абзаца\n"
                        "• ЗАПРЕЩЕНО: списки (• – – 1.), нумерация, заголовки (##), двойные переносы строк\n"
                        "• ЗАПРЕЩЕНО начинать с 'Привет', 'Добрый', оценки времени суток, дня недели\n"
                        "• Эмодзи 1–2 внутри текста, НЕ в начале строк\n"
                        "• Первое слово — существительное или глагол действия\n\n"
                        "⚠️ ПРАВИЛО ВЕРИФИКАЦИИ ДАННЫХ:\n"
                        "Секция ПАМЯТЬ ПОЛЬЗОВАТЕЛЯ — это устаревший фон. НЕ цитируй из неё задачи, цели, посты или факты как текущие.\n"
                        "ТОЛЬКО данные из инструментов (list_tasks, list_goals) считай актуальными.\n"
                        "Если list_tasks вернул пустой список — у пользователя НЕТ задач. Не упоминай задачи.\n"
                        "Если list_goals вернул пустой список — у пользователя НЕТ целей. Не упоминай цели.\n"
                        "НЕ УПОМИНАЙ конкретные задачи/цели/посты которые ты НЕ получил из инструментов."
                    ),
                    'result_check': (
                        "\n\n[РЕЖИМ: ПОЗДРАВЛЕНИЕ]\n"
                        "Задача выполнена. Отреагируй живо — как друг, а не системный бот. 1–2 предложения, максимум 200 символов.\n"
                        "ЗАПРЕЩЕНО начинать с 'Поздравляю!' — придумай свою реакцию, с характером.\n"
                        "Стиль: разговорный, эмоциональный, с лёгкой иронией если уместно. Без списков, без заголовков."
                    ),
                    'anchor': (
                        "\n\n[РЕЖИМ: ANCHOR ENGINE]\n"
                        "Тебе переданы ЯКОРЯ (события/факты) + полный контекст человека.\n"
                        "Подумай: есть ли здесь что-то, ради чего стоит отвлечь человека?\n"
                        "Если нет — верни SKIP. Не пиши ради того, чтобы написать.\n"
                        "Если да — используй инструменты (get_news_trends, list_tasks, list_goals) "
                        "по релевантной теме. НЕ вызывай research_topic — используй get_news_trends.\n"
                        "ОДНА ТЕМА НА СООБЩЕНИЕ. Закончи конкретным вопросом или предложением.\n"
                        "ВАЖНО: НЕ публикуй посты автоматически. НЕ используй /dashboard — только https://asibiont.com/dashboard.\n\n"
                        "СТИЛЬ (СТРОГО — идентичен обычному чату):\n"
                        "• 300–500 символов, сплошной текст. Человек не должен отличить якорное сообщение от обычного ответа.\n"
                        "• ЗАПРЕЩЕНО: списки (• – – 1.), нумерация, заголовки (##), двойные переносы строк\n"
                        "• ЗАПРЕЩЕНО начинать с 'Привет', 'Добрый', оценки времени суток\n"
                        "• Эмодзи 1–2 внутри текста, НЕ в начале строк. Первое слово — существительное или глагол.\n\n"
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
            return "Great, task completed!" if lang == 'en' else "Отлично, задача выполнена!"
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


# ═══════════════════════════════════════════════════════════════════════════════
# OFFICE DIRECTOR — ASI координирует агентов прямо в чате
# ═══════════════════════════════════════════════════════════════════════════════

def _has_explicit_mention(message: str) -> bool:
    """True если сообщение начинается с @Агент или 'ИмяАгента,'."""
    return bool(re.match(r'@\w+\b', (message or '').strip()))


async def _quick_ai_call_raw(messages: list, max_tokens: int = 400) -> str:
    """Прямой вызов DeepSeek без tool calling — быстро и без overhead."""
    try:
        async with aiohttp.ClientSession() as _sess:
            async with _sess.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": DEEPSEEK_MODEL,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": 0.7,
                },
                timeout=aiohttp.ClientTimeout(total=40),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.debug("[DIRECTOR] AI call error: %s", e)
    return ""


def _save_interaction_for_director(telegram_id: int, content: str):
    """Сохраняет промежуточное сообщение агента/АСИ в Interaction чата."""
    try:
        from models import Session as _Db, User as _User, Interaction as _Intr
        _s = _Db()
        try:
            _u = _s.query(_User).filter_by(telegram_id=telegram_id).first()
            if _u:
                _s.add(_Intr(user_id=_u.id, message_type='ai', content=content or ''))
                _s.commit()
        finally:
            _s.close()
    except Exception as e:
        logger.debug("[DIRECTOR] save interaction error: %s", e)


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
    'ALPHA_VANTAGE': 'Биржевые данные (Alpha Vantage)',
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
}


def _parse_agent_integrations(user_api_keys: str, python_code: str = '',
                               tools_allowed: str = '', search_scope: str = '') -> list[str]:
    """Универсально определяет что агент реально умеет по его настройкам.
    Возвращает список человекочитаемых названий сервисов.
    """
    found: set = set()

    # 1. Из user_api_keys — смотрим имена ключей
    for line in (user_api_keys or '').splitlines():
        line = line.strip()
        if '=' not in line or line.startswith('#'):
            continue
        key = line.split('=')[0].upper()
        for prefix, label in _INTEGRATION_LABELS.items():
            if key.startswith(prefix):
                found.add(label)
                break

    # 2. Из python_code — ищем import и характерные строки
    code_lc = (python_code or '').lower()
    _code_hints = {
        'imaplib': 'IMAP почта', 'smtplib': 'SMTP почта',
        'gmail': 'Gmail (почта)', 'yandex': 'Яндекс Почта',
        'mail.ru': 'Mail.ru почта',
        'ozon': 'Ozon (маркетплейс)', 'wildberries': 'Wildberries',
        'amocrm': 'AmoCRM', 'bitrix': 'Битрикс24',
        'notion': 'Notion', 'vk.com': 'ВКонтакте',
        'binance': 'Binance (крипта)', 'bybit': 'Bybit (крипта)',
        'avito': 'Авито', 'avito.ru': 'Авито',
        'yandex.direct': 'Яндекс.Директ', 'moysklad': 'МойСклад',
        'yandex.market': 'Яндекс.Маркет', 'linkedin': 'LinkedIn',
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
    except Exception:
        pass

    # 4. Из search_scope
    if search_scope and search_scope.strip():
        found.add(f'Поиск: {search_scope.strip()[:60]}')

    # 5. Если tools_allowed пустой → агент универсальный, все инструменты платформы доступны
    try:
        _tj = (tools_allowed or '').strip()
        if not _tj or _tj == '[]':
            found.add('все инструменты платформы')
    except Exception:
        pass

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
        from models import Session as _Db, User as _U, UserProfile as _UP, Goal as _G, UserAgent as _UA, EmailContact as _EC
        _s = _Db()
        try:
            user = _s.query(_U).filter_by(id=user_db_id).first()
            profile = _s.query(_UP).filter_by(user_id=user_db_id).first()
            goals = (_s.query(_G)
                     .filter_by(user_id=user_db_id, status='active')
                     .order_by(_G.priority.desc())
                     .limit(5).all())
            agents = (_s.query(_UA)
                      .filter_by(author_id=user_db_id, status='active')
                      .limit(10).all())
            contacts = (_s.query(_EC)
                        .filter_by(user_id=user_db_id)
                        .order_by(_EC.created_at.desc())
                        .limit(30).all())
        finally:
            _s.close()
    except Exception as _ctx_err:
        logger.warning('[CONTEXT] _build_user_context_sync failed: %s', _ctx_err)
        return ''

    parts: list[str] = []

    # --- Кто пользователь ---
    identity_parts: list[str] = []
    if user:
        name = user.first_name or user.username or ''
        if name:
            identity_parts.append(name)
    if profile:
        if profile.position:
            identity_parts.append(profile.position)
        if profile.company:
            identity_parts.append(f'из «{profile.company}»')
        if profile.city:
            identity_parts.append(f'г. {profile.city}')
            if profile.status_text:
                identity_parts.append(f'Статус: {profile.status_text}')
            identity_parts.append(f'Сейчас: {profile.current_plans[:100]}')
        if profile.content_strategy:
            identity_parts.append(f'Контент-стратегия: {profile.content_strategy[:100]}')

    if identity_parts:
        parts.append('ПОЛЬЗОВАТЕЛЬ: ' + ', '.join(identity_parts))

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
        parts.append('EMAIL-КОНТАКТЫ ПОЛЬЗОВАТЕЛЯ:\n' + '\n'.join(contact_lines))

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
            # expires_at минимум 1ч — чтобы AnchorEngine успел увидеть якорь
            _effective_expires = max(cooldown_hours, 1.0)
            _s.add(_Anch(
                user_id=user_db_id,
                anchor_type='agent_delegation',
                source=f'agent:{agent_id}',
                topic=f'{agent_name}: {task[:120]}',
                priority=AnchorPriority.LOW,
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
    if _rnd.random() > 0.30:
        return

    # Проверяем баланс до задержки: если токенов нет — не включаемся
    try:
        from config import FREE_ACCESS_MODE as _FAM_ch
        from token_service import has_enough_tokens as _het, spend_tokens as _st_ch
        if not _FAM_ch:
            if not _het(user_id, 'agent_chime'):
                return
    except Exception:
        pass

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
                except Exception:
                    pass
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

    _asi_identity = (
        "Ты — персональный агент ASI Biont. Мыслящий партнёр, не автоответчик. "
        "Прямой, энергичный, действуешь проактивно. Пишешь живо, как опытный друг в мессенджере. "
        "Ты ДЕЛАЕШЬ, а не просто советуешь. Отвечаешь кратко, без списков и заголовков."
    )
    _persona = (
        _agent.get('personality') or
        f"Ты действуешь как {_agent['name']} — {_agent.get('specialization', 'специалист')}. "
        f"{_agent.get('description', '')}"
    )
    _ctx_block = f"\n\nКОНТЕКСТ О ПОЛЬЗОВАТЕЛЕ:\n{_user_ctx}" if _user_ctx else ''
    _system = f"{_asi_identity}\n\nРОЛЬ В ЭТОМ КОНТЕКСТЕ:\n{_persona}{_integrations_hint}{_ctx_block}"

    _user_content = (
        f"В чате только что написали:\n"
        f"[Пользователь]: {user_message[:200]}\n"
        f"[ASI]: {asi_response[:300]}\n\n"
        "Ты — коллега ASI. Прочитал этот разговор и хочешь добавить короткую реплику со своей стороны.\n"
        "ВАЖНО: ты только ЧИТАЕШЬ разговор — НЕ делай вид, что запустил скрипт, проверил почту, "
        "получил данные или выполнил задачу. Ты комментируешь, а не действуешь.\n"
        "Можно: добавить экспертное мнение из своей области, упомянуть что можешь помочь если пользователь обратится.\n"
        "НЕ выдумывай данные (письма, новости, задачи) — только то, что реально в этом разговоре.\n"
        "Учитывай кто этот пользователь и чем он занимается — отвечай релевантно его контексту.\n"
        "1-2 предложения. Живо, без официоза. Если нечего добавить — ответь пустой строкой."
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

    if not _reply or len(_reply) < 5:
        return

    # Списываем токены за chime
    try:
        from config import FREE_ACCESS_MODE as _FAM_ch2
        from token_service import spend_tokens as _st_ch2
        if not _FAM_ch2:
            _st_ch2(user_id, 'agent_chime', description=f'chime:{_agent["name"]}')
    except Exception:
        pass

    # Сохраняем в Interaction
    try:
        _s2 = _Db()
        try:
            # Загружаем avatar_url агента
            _db_ag = _s2.query(_UA).filter_by(id=_agent.get('id')).first()
            _avatar = (_db_ag.avatar_url or '') if _db_ag else ''
            _content = _json.dumps({
                '__agent': {
                    'name': _agent['name'],
                    'id': _agent.get('id', 0),
                    'avatar_url': _avatar,
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


async def _exec_agent_for_director(agent: dict, task: str, user_id: int, dialog_context: str = "", _depth: int = 0) -> str:
    """Запускает агента с полноценным tool-calling циклом (по tools_allowed).
    1. Выполняет python_code (внешние данные: IMAP, RSS, HTTP)
    2. Запускает tool-loop через платформенные инструменты (до 3 итераций)
    3. Агент реально вызывает send_email, research_topic и т.д. по своему tools_allowed
    Используется в _office_director_chat для delegate и multi_delegate.
    _depth: текущая глубина рекурсии (макс 2).
    """
    if _depth >= 2:
        return f"Агент {agent.get('name', '?')}: превышена глубина делегирования, задача принята."

    import subprocess as _sp2, sys as _sys2, os as _os2

    _persona = (
        agent.get('personality') or
        f"Ты действуешь как {agent['name']} — {agent.get('specialization', 'специалист')}. "
        f"{agent.get('description', '')} Отвечай от имени {agent['name']}."
    )
    system_prompt = (
        "Ты — агент в команде ASI Biont. Твои результаты появляются в общем чате с пользователем.\n"
        "Пиши отчёт: факты, данные, выводы. Кратко и по делу.\n"
        "НЕ пиши 'Привет!', не здоровайся, не начинай с обращения. Просто отчитывайся о проделанной работе.\n\n"
        "У тебя есть доступ ко всем инструментам платформы: задачи и напоминания, поиск в интернете, "
        "исследования, email, публикации, делегирование, анализ данных и многое другое. "
        "Выбирай те инструменты которые реально нужны для задачи — не ограничивай себя только текстом.\n\n"
        "СЛОЖНЫЕ СЦЕНАРИИ: ты можешь выполнять многошаговые задачи. Например:\n"
        "1. Найти людей через research_topic / web_search → сохранить контакты save_email_contact\n"
        "2. Запустить email-кампанию start_email_campaign или отправить письма send_email / negotiate_by_email\n"
        "3. Создать задачи add_task для отслеживания прогресса\n"
        "4. Опубликовать результаты create_post / publish_to_telegram\n"
        "5. Делегировать часть работы коллеге delegate_to_agent\n"
        "Не останавливайся на одном шаге — если задача требует цепочки действий, пройди все шаги.\n\n"
        "Отвечай кратко и по делу. Без markdown, без заголовков, без звёздочек (*улыбается* и т.п.) — только живой текст.\n\n"
        "ДЕЛЕГИРОВАНИЕ КОЛЛЕГАМ: если часть задачи требует специализации другого агента команды, "
        "вызови инструмент delegate_to_agent(agent_name: str, task: str). "
        "Используй его только когда действительно нужна другая экспертиза — не злоупотребляй.\n\n"
        "ЕСЛИ ЗАСТРЯЛ: когда для выполнения задачи нужно решение, доступ или подтверждение от пользователя — "
        "начни ПЕРВУЮ строку ответа с 'BLOCKED: <краткая причина>'. Это немедленно уведомит пользователя.\n\n"
        f"ТВОЯ РОЛЬ:\n{_persona}"
    )
    if dialog_context:
        system_prompt += (
            f"\n\n[КОНТЕКСТ — профиль пользователя, его email-контакты, цели, история диалога. "
            f"Используй чтобы понимать КТО пользователь, КОМУ он пишет, ЧТО ищет]:\n{dialog_context}"
        )

    # Авто-загрузка контекста пользователя если не передан извне (агент узнаёт цели/бизнес)
    if not dialog_context and _build_user_context_sync:
        try:
            from models import Session as _Sess_uc, User as _UCtx
            _s_uc = _Sess_uc()
            try:
                _u_uc = _s_uc.query(_UCtx).filter_by(telegram_id=user_id).first()
                if _u_uc:
                    _uc_loop = asyncio.get_running_loop()
                    _ucontext = await _uc_loop.run_in_executor(
                        None, _build_user_context_sync, _u_uc.id
                    )
                    if _ucontext:
                        system_prompt += (
                            "\n\n[КОНТЕКСТ ПОЛЬЗОВАТЕЛЯ — цели, бизнес, история диалога. "
                            f"Используй чтобы работа агента была релевантна его задачам]:\n{_ucontext[:600]}"
                        )
            finally:
                _s_uc.close()
        except Exception:
            pass

    # ── Шаг 1: Выполняем python_code (внешние данные) ─────────────────────────
    script_context = ""
    if (agent.get('python_code') or '').strip():
        try:
            _wrapped = _wrap_agent_code(agent['python_code'].strip())
            _exec_env = {'PYTHONIOENCODING': 'utf-8', 'PATH': _os2.environ.get('PATH', '/usr/bin:/bin')}
            if _sys2.platform != 'win32':
                _exec_env['HOME'] = _os2.environ.get('HOME', '/tmp')
            else:
                for _wk in ('SystemRoot', 'SystemDrive', 'TEMP', 'TMP', 'WINDIR', 'COMSPEC',
                             'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH'):
                    if _wk in _os2.environ:
                        _exec_env[_wk] = _os2.environ[_wk]
            _exec_env['AGENT_TASK'] = str(task or '')[:500]
            _api_raw = agent.get('user_api_keys', '') or ''
            for _kl in _api_raw.splitlines():
                _kl = _kl.strip()
                if '=' in _kl and not _kl.startswith('#'):
                    _dk, _, _dv = _kl.partition('=')
                    _dv = _dv.strip()
                    if 'PASS' in _dk.upper() or 'PASSWORD' in _dk.upper():
                        _dv = _dv.replace(' ', '')
                    _exec_env[_dk.strip()] = _dv

            def _run_script():
                def _resource_limits_fn():
                    try:
                        import resource as _res
                        _mem = 64 * 1024 * 1024   # 64 MB RAM
                        _res.setrlimit(_res.RLIMIT_AS, (_mem, _mem))
                        _cpu = 12                  # 12 sec CPU time
                        _res.setrlimit(_res.RLIMIT_CPU, (_cpu, _cpu))
                        _files = 32                # max 32 file descriptors
                        _res.setrlimit(_res.RLIMIT_NOFILE, (_files, _files))
                    except Exception:
                        pass
                try:
                    _kwargs_sc = dict(
                        capture_output=True, text=True, timeout=API_TIMEOUT_SCRIPT, env=_exec_env,
                    )
                    if _sys2.platform != 'win32':
                        _kwargs_sc['preexec_fn'] = _resource_limits_fn
                    r = _sp2.run(
                        [_sys2.executable, '-c', _wrapped],
                        **_kwargs_sc,
                    )
                    return r.stdout[:2000].strip(), r.stderr[:400].strip()
                except _sp2.TimeoutExpired:
                    return '', 'timeout'
                except Exception as _e2:
                    return '', str(_e2)[:200]

            loop2 = asyncio.get_running_loop()
            stdout2, _stderr2 = await loop2.run_in_executor(None, _run_script)
            if stdout2:
                script_context = (
                    f"\n\n[Данные от скрипта/интеграции — перескажи СВОИМИ СЛОВАМИ в ответе, "
                    f"не копируй raw-текст дословно, сформулируй как живой человек]:\n{stdout2[:2000]}"
                )
                system_prompt += script_context
            elif _stderr2 and 'timeout' not in _stderr2:
                logger.debug("[DIRECTOR-EXEC] script stderr for %s: %s", agent.get('name'), _stderr2[:150])
        except Exception as _e3:
            logger.debug("[DIRECTOR-EXEC] script exec error for %s: %s", agent.get('name'), _e3)

    # ── Шаг 2: Определяем разрешённые инструменты ─────────────────────────────
    _allowed_tools: set[str] = set()
    try:
        _raw_tools = agent.get('tools_allowed') or '[]'
        if isinstance(_raw_tools, list):
            _allowed_tools = set(_raw_tools)
        else:
            _allowed_tools = set(json.loads(_raw_tools))
        # Fallback: agent['tools'] already parsed
        if not _allowed_tools:
            _t2 = agent.get('tools') or []
            if isinstance(_t2, list):
                _allowed_tools = set(_t2)
    except Exception as _te:
        logger.debug('[DIRECTOR] tools_allowed parse: %s', _te)

    # Вычисляем exclude_tools = все инструменты минус разрешённые
    _exclude_for_agent: set[str] | None = None
    if _allowed_tools:
        try:
            from .tools import get_available_tools as _gat2
            _all_names = {t['function']['name'] for t in _gat2()}
            _exclude_for_agent = _all_names - _allowed_tools
        except Exception as _te2:
            logger.debug('[DIRECTOR] tools exclude calc: %s', _te2)

    # ── Шаг 3: Tool-calling loop (макс 3 итерации) ────────────────────────────
    # Создаём изолированный инстанс — не делим состояние с глобальным ASI
    # (execution_history, счётчики, лимиты у каждого агента свои)
    _agent_inst = HybridAutonomousAgent()
    _messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]
    # Если tools_allowed пустой → агент универсальный: работает со всеми инструментами платформы
    # (аналогично поведению при прямом @mention агента в process_request)
    # _exclude_for_agent уже None при пустом _allowed_tools → exclusions нет → все tools доступны
    _use_tools = True

    # Очередь субделегирований: агент может попросить другого агента через паттерн DELEGATE[имя]: задача
    _pending_subdelegations: list[dict] = []
    _early_text: str | None = None  # установлен если агент ответил текстом без tool calls

    _agent_start_time = __import__('time').time()
    _AGENT_BUDGET = 30  # секунд на всё выполнение агента
    _TOOL_TIMEOUT = API_TIMEOUT_NORMAL  # секунд на один инструмент

    for _iter in range(2):  # Макс 2 итерации для субагента (скорость)
        # Проверяем бюджет времени
        if __import__('time').time() - _agent_start_time > _AGENT_BUDGET:
            logger.warning("[DIRECTOR-EXEC] agent %s time budget exceeded", agent.get('name'))
            break
        try:
            _resp = await asyncio.wait_for(
                _agent_inst.call_ai(
                    _messages,
                    use_tools=_use_tools,
                    tool_choice="auto" if _use_tools else None,
                    exclude_tools=_exclude_for_agent if _use_tools else None,
                    max_tokens=400,
                    api_timeout=API_TIMEOUT_NORMAL,  # Субагент — короткий таймаут
                ),
                timeout=API_TIMEOUT_SCRIPT,
            )
        except (asyncio.TimeoutError, Exception) as _ai_err:
            logger.warning("[DIRECTOR-EXEC] agent %s call_ai error: %s", agent.get('name'), _ai_err)
            break
        if not _resp or not _resp.get('choices'):
            break
        _msg = _resp['choices'][0]['message']
        _content = _msg.get('content') or ''
        _tool_calls = _msg.get('tool_calls') or []

        if not _tool_calls:
            # Агент ответил текстом — парсим паттерн DELEGATE[Имя]: задача
            if _content:
                import re as _re_sub
                for _m in _re_sub.finditer(
                    r'DELEGATE\[([^\]]+)\]:\s*(.+?)(?=DELEGATE\[|$)',
                    _content, _re_sub.DOTALL | _re_sub.IGNORECASE,
                ):
                    _aname = _m.group(1).strip()
                    _atask = _m.group(2).strip()[:400]
                    if _aname and _atask:
                        _pending_subdelegations.append({'agent_name': _aname, 'task': _atask})
                # Убираем DELEGATE-строки из финального текста
                _content = _re_sub.sub(
                    r'DELEGATE\[[^\]]+\]:[^\n]*\n?', '', _content,
                ).strip()
            # Сохраняем результат и выходим из цикла — субделегирования обработаются ниже
            _early_text = _content or "Задачу выполнил."
            break

        # Агент вызвал инструменты — выполняем (макс 1 за итерацию для скорости)
        _messages.append(_msg)
        for _tc in _tool_calls[:1]:
            _tname = _tc.get('function', {}).get('name', '')
            try:
                _targs = json.loads(_tc.get('function', {}).get('arguments', '{}'))
            except Exception:
                _targs = {}

            # ── Специальный инструмент: агент делегирует другому агенту ──────────────────
            if _tname == 'delegate_to_agent':
                _sub_agent_name = _targs.get('agent_name', '')
                _sub_task = _targs.get('task', _targs.get('message', ''))
                if _sub_agent_name and _sub_task:
                    _pending_subdelegations.append({'agent_name': _sub_agent_name, 'task': _sub_task})
                    _tc_result = json.dumps(
                        {'status': 'queued', 'agent': _sub_agent_name,
                         'message': f'Задача передана агенту {_sub_agent_name}'},
                        ensure_ascii=False,
                    )
                else:
                    _tc_result = json.dumps({'error': 'agent_name and task are required'}, ensure_ascii=False)
            # ── Обычные инструменты ───────────────────────────────────────────────────────────
            # Проверяем доступность инструмента
            elif _allowed_tools and _tname not in _allowed_tools:
                _tc_result = json.dumps({"error": f"tool {_tname} not in tools_allowed"}, ensure_ascii=False)
            else:
                # Задачи создаваемые агентом помечаются source='agent'
                if _tname == 'add_task' and agent.get('id'):
                    _targs['created_by_agent_id'] = agent['id']
                try:
                    _tres = await asyncio.wait_for(
                        _agent_inst.execute_actions(
                            [{"tool": _tname, "params": _targs, "reason": f"{agent['name']}: {_tname}"}],
                            user_id, session=None, user_message=task,
                        ),
                        timeout=_TOOL_TIMEOUT,
                    )
                    _r0 = _tres[0] if _tres else {"success": False}
                    if _r0.get('success'):
                        _tc_result = json.dumps(_r0['result'], ensure_ascii=False, default=str)
                        _tc_result = _tc_result[:1500]
                    else:
                        _tc_result = json.dumps({"error": str(_r0.get('error', ''))}, ensure_ascii=False)
                except asyncio.TimeoutError:
                    _tc_result = json.dumps({"error": "tool timeout"}, ensure_ascii=False)
                    logger.warning("[DIRECTOR-EXEC] tool %s timeout for %s", _tname, agent['name'])
                except Exception as _te:
                    _tc_result = json.dumps({"error": str(_te)[:200]}, ensure_ascii=False)
                    logger.debug("[DIRECTOR-EXEC] tool %s error for %s: %s", _tname, agent['name'], _te)

            _messages.append({"role": "tool", "tool_call_id": _tc['id'], "content": _tc_result})
        # Добавляем фиктивные результаты для пропущенных tool_calls (OpenAI/DeepSeek требует все)
        for _tc_skip in _tool_calls[1:]:
            _messages.append({"role": "tool", "tool_call_id": _tc_skip['id'],
                              "content": '{"status":"skipped"}'})

        # Проверяем бюджет времени после tool execution
        if __import__('time').time() - _agent_start_time > _AGENT_BUDGET:
            logger.warning("[DIRECTOR-EXEC] agent %s time budget exceeded after tools", agent.get('name'))
            break

    # Если агент ответил текстом без tool calls — пропускаем финальный AI-вызов
    if _early_text is not None:
        _final_text = _early_text
    else:
        # Если израсходовали все итерации — финальный ответ без tools
        try:
            _final = await asyncio.wait_for(
                _quick_ai_call_raw(_messages, max_tokens=400), timeout=API_TIMEOUT_NORMAL
            )
        except (asyncio.TimeoutError, Exception):
            _final = None
        _final_raw = _final or ''
        # Парсим DELEGATE из финального ответа
        if _final_raw:
            import re as _re_fin
            for _m in _re_fin.finditer(
                r'DELEGATE\[([^\]]+)\]:\s*(.+?)(?=DELEGATE\[|$)',
                _final_raw, _re_fin.DOTALL | _re_fin.IGNORECASE,
            ):
                _aname = _m.group(1).strip()
                _atask = _m.group(2).strip()[:400]
                if _aname and _atask:
                    _pending_subdelegations.append({'agent_name': _aname, 'task': _atask})
            _final_text = _re_fin.sub(
                r'DELEGATE\[[^\]]+\]:[^\n]*\n?', '', _final_raw,
            ).strip() or 'Задачу выполнил.'
        else:
            _final_text = 'Задачу выполнил.'

    # Детектируем BLOCKED-маркер в финальном ответе агента
    if _final_text and _final_text.lower().startswith('blocked:'):
        try:
            from models import Session as _BDb, AgentActivityLog as _BAct
            _b_s = _BDb()
            try:
                _b_s.add(_BAct(
                    user_id=user_id,
                    activity_type='task_blocked',
                    title=f"{agent['name']}: нужно решение",
                    content=_final_text[:600],
                    target=f"agent:{agent['name']}",
                    status='new',
                ))
                _b_s.commit()
            finally:
                _b_s.close()
        except Exception as _be:
            logger.debug('[BLOCKED] director exec save error: %s', _be)

    # ── Выполняем очередь субделегирований агента первым агентом ────────────────────
    if _pending_subdelegations:
        try:
            from models import Session as _SubDb, UserAgent as _SubUA, User as _SubUser
            _sub_s = _SubDb()
            try:
                # Загружаем всех агентов пользователя
                _sub_user = _sub_s.query(_SubUser).filter_by(telegram_id=user_id).first()
                _sub_db_id = _sub_user.id if _sub_user else None
                _all_agents_db = []
                if _sub_db_id:
                    _all_agents_db = (
                        _sub_s.query(_SubUA)
                        .filter(_SubUA.author_id == _sub_db_id, _SubUA.status.in_(['active', 'paused']))
                        .limit(10)
                        .all()
                    )
            finally:
                _sub_s.close()

            _sub_results = []
            for _sd in _pending_subdelegations:
                _sd_name = _sd['agent_name']
                _sd_task = _sd['task']
                # Ищем агента по имени
                _sd_agent_db = next(
                    (a for a in _all_agents_db if a.name.lower() == _sd_name.lower()
                     or _sd_name.lower() in a.name.lower()),
                    None,
                )
                if not _sd_agent_db:
                    logger.debug("[SUBDELEGATE] agent '%s' not found", _sd_name)
                    continue
                _sd_data = {
                    'id': _sd_agent_db.id, 'name': _sd_agent_db.name,
                    'job_title': _sd_agent_db.job_title or '',
                    'specialization': _sd_agent_db.specialization or '',
                    'description': _sd_agent_db.description or '',
                    'personality': _sd_agent_db.personality or '',
                    'python_code': _sd_agent_db.python_code or '',
                    'user_api_keys': _sd_agent_db.user_api_keys or '',
                    'tools_allowed': _sd_agent_db.tools_allowed or '',
                    'search_scope': _sd_agent_db.search_scope or '',
                    'avatar_url': _sd_agent_db.avatar_url or '',
                    'tools': [],
                }
                _sub_ctx = (
                    f"Задача от коллеги {agent['name']}:\n{_sd_task}\n\n"
                    f"Контекст (что уже сделал {agent['name']}):\n{_final_text[:400]}"
                )
                logger.info("[SUBDELEGATE] %s → %s: %s", agent['name'], _sd_name, _sd_task[:60])
                _sub_resp = await _exec_agent_for_director(_sd_data, _sub_ctx, user_id, _depth=_depth + 1)
                if _sub_resp:
                    _sub_results.append(f"[{_sd_name}]: {_sub_resp[:300]}")
                    # Сохраняем память субделегированного агента
                    try:
                        from ai_integration.office_engine import _save_agent_outcome_memory_sync as _smem
                        if _sub_db_id:
                            _smem(_sub_db_id, _sd_name,
                                  f'задача от {agent["name"]}',
                                  _sub_resp[:400], True)
                    except Exception:
                        pass

            if _sub_results:
                _final_text = _final_text + '\n\n' + '\n'.join(_sub_results)
        except Exception as _sub_err:
            logger.debug("[SUBDELEGATE] error: %s", _sub_err)

    # Очищаем DSML-теги и технические артефакты перед возвратом
    try:
        from .utils import clean_technical_details as _ctd_exec
        _final_text = _ctd_exec(_final_text or '').strip() or 'Задачу выполнил.'
    except Exception:
        pass
    return _final_text


# Слова-сигналы что пользователь хочет действие, а не разговор


async def _office_director_chat(user_message: str, user_id: int, progress_callback=None) -> str | None:
    """
    ASI — директор офиса с якорной памятью:
    1. Загружает агентов + их якоря делегирования (что делали, cooldown)
    2. ASI решает: делегировать свежему агенту, использовать кэш из якоря, или ответить сам
    3. Если делегирует: агент работает (python_code) → пишет в чат → сохраняется якорь
    4. ASI подводит итог с учётом результата

    Якоря дают ASI память: «Кристина 2ч назад проверила почту, нашла 3 письма» →
    ASI не запускает её снова, а отвечает из кэша. Cooldown 2ч — антиспам.
    """
    import json as _json
    import datetime as _dt

    # ── Загружаем user_db_id + агентов: сессионно-активированные + собственные ─
    user_db_id = None
    _agents = []
    try:
        from models import Session as _Db, User as _User, UserAgent as _UA
        _s = _Db()
        try:
            _u = _s.query(_User).filter_by(telegram_id=user_id).first()
            if not _u:
                return None
            user_db_id = _u.id

            # Источник 1: агенты активированные в сессии (в т.ч. публичные)
            _session_ids: list[int] = []
            try:
                from .user_agents import get_user_active_agents
                _session_ids = get_user_active_agents(user_id) or []
            except Exception:
                pass

            # Источник 2: собственные агенты пользователя с активной подпиской (AgentSubscription).
            # Activate/Deactivate в UI управляет именно этой таблицей.
            # Миграция: если у пользователя нет ни одной подписки на свои агенты — авто-подписываем
            # все его агенты (первый запуск), чтобы они работали без ручного клика «Активировать».
            try:
                from models import AgentSubscription as _AS
                _own_all = (
                    _s.query(_UA)
                    .filter(_UA.author_id == user_db_id, _UA.status.in_(['active', 'paused']))
                    .limit(10)
                    .all()
                )
                _existing_subs = {
                    row.agent_id
                    for row in _s.query(_AS).filter(
                        _AS.user_id == user_db_id,
                        _AS.agent_id.in_([a.id for a in _own_all]),
                    ).all()
                } if _own_all else set()

                # Первый запуск (нет ни одной подписки): авто-мигрируем
                if _own_all and not _existing_subs:
                    for _oa in _own_all:
                        _s.add(_AS(user_id=user_db_id, agent_id=_oa.id))
                        try:
                            from .user_agents import set_user_active_agent as _sua_dir
                            _sua_dir(user_id, _oa.id)
                        except Exception:
                            pass
                    _s.commit()
                    _existing_subs = {a.id for a in _own_all}

                _own_agents = [a for a in _own_all if a.id in _existing_subs]
            except Exception:
                # Fallback: загружаем все собственные агенты без фильтра
                _own_agents = (
                    _s.query(_UA)
                    .filter(_UA.author_id == user_db_id, _UA.status.in_(['active', 'paused']))
                    .limit(10)
                    .all()
                )
            _own_ids = {a.id for a in _own_agents}

            # Источник 3: сессионно-активированные с загрузкой из БД (если не вошли в own)
            _extra_ids = [i for i in _session_ids if i not in _own_ids]
            _extra_agents = []
            if _extra_ids:
                _extra_agents = (
                    _s.query(_UA)
                    .filter(_UA.id.in_(_extra_ids), _UA.status.in_(['active', 'paused']))
                    .all()
                )

            # Объединяем, порядок: сначала сессионно-активированные, потом остальные собственные
            _seen: set[int] = set()
            _all_db: list = []
            for _a in _extra_agents + list(_own_agents):
                if _a.id not in _seen:
                    _seen.add(_a.id)
                    _all_db.append(_a)

            for _dba in _all_db:
                _tools = []
                try:
                    _tools = json.loads(_dba.tools_allowed or '[]')
                except Exception:
                    pass
                _agents.append({
                    'id': _dba.id,
                    'name': _dba.name or 'Агент',
                    'job_title': _dba.job_title or '',
                    'specialization': _dba.specialization or '',
                    'description': _dba.description or '',
                    'personality': _dba.personality or '',
                    'python_code': _dba.python_code or '',
                    'user_api_keys': _dba.user_api_keys or '',
                    'tools_allowed': _dba.tools_allowed or '',
                    'search_scope': _dba.search_scope or '',
                    'avatar_url': _dba.avatar_url or '',
                    'tools': _tools,
                })
        finally:
            _s.close()
    except Exception as e:
        logger.warning("[DIRECTOR] agents/user load error: %s", e)

    # Если нет агентов — не перехватываем, пусть ASI ответит сам
    if not _agents:
        return None

    # Строим универсальный контекст пользователя (профиль + цели + команда с интеграциями)
    _user_full_ctx = _build_user_context_sync(user_db_id) if user_db_id else ''

    # ── Загружаем последние 5 сообщений истории диалога ─────────────────────
    _history_lines: list[str] = []
    if user_db_id:
        try:
            from models import Interaction as _Itr
            _hs = _Db()
            try:
                _recent = (
                    _hs.query(_Itr)
                    .filter(_Itr.user_id == user_db_id)
                    .order_by(_Itr.id.desc())
                    .limit(5)
                    .all()
                )
                for _r in reversed(_recent):
                    _role = 'Пользователь' if _r.message_type == 'user' else 'ASI'
                    _txt = (_r.content or '').strip()[:200]
                    if _txt:
                        _history_lines.append(f"{_role}: {_txt}")
            finally:
                _hs.close()
        except Exception:
            pass
    _history_block = ('\n\nПОСЛЕДНИЕ СООБЩЕНИЯ:\n' + '\n'.join(_history_lines)) if _history_lines else ''

    # ── Строим контекст агентов с якорной памятью ─────────────────────────────
    agents_context_lines = []
    agent_anchor_map: dict[str, list] = {}  # agent_name → anchors

    for a in _agents:
        anchors = _get_agent_anchors(user_db_id, a['id']) if user_db_id else []
        agent_anchor_map[a['name']] = anchors

        # Интеграции агента — что он РЕАЛЬНО умеет делать
        _intg: list[str] = []
        try:
            _intg = _parse_agent_integrations(
                a.get('user_api_keys') or '',
                a.get('python_code') or '',
                a.get('tools_allowed') or '',
                a.get('search_scope') or '',
            )
        except Exception:
            pass
        # Если нет технических интеграций — выводим возможности из роли/специализации
        if not _intg:
            _intg = _infer_capabilities_from_role(
                a.get('job_title') or '',
                a.get('specialization') or '',
                a.get('description') or '',
            )
        _intg_str = f"\n  Может: {', '.join(_intg[:8])}"

        _base = (
            f"- {a['name']} [{a.get('job_title', '')}] ({a.get('specialization', 'агент')}): "
            f"{(a.get('description') or '')[:300]}"
            f"{_intg_str}"
        )

        if anchors:
            last = anchors[0]
            age_h = last['age_min'] // 60
            age_m = last['age_min'] % 60
            age_str = f"{age_h}ч {age_m}мин назад" if age_h else f"{age_m}мин назад"
            cached_result = last['data'].get('result_summary', '')[:150]
            agents_context_lines.append(
                _base
                + f"\n  [Последняя работа {age_str}: {last['topic']}"
                + (f". Результат: {cached_result}" if cached_result else "")
                + "]"
            )
        else:
            agents_context_lines.append(
                _base
                + f"\n  [Доступен]"
            )

    agents_block = "\n".join(agents_context_lines)

    # ── Вспомогательная функция поиска агента по имени ────────────────────────
    def _find_agent(name: str):
        if not name:
            return None
        return next(
            (a for a in _agents if a['name'].lower() == name.lower()),
            next((a for a in _agents if name.lower() in a['name'].lower()), None),
        )

    # ── Вспомогательная функция отправки видимого сообщения в чат ────────────
    async def _send_visible(text: str):
        """Отправляет промежуточное сообщение пользователю через progress_callback(persist=True)."""
        if progress_callback and text:
            try:
                await progress_callback(text, persist=True)
            except TypeError:
                try:
                    await progress_callback(text)
                except Exception:
                    pass
            except Exception:
                pass

    # ── Вспомогательная функция сохранения результата агента ──────────────────
    async def _run_agent_task(ag, task, extra_context: str = "", director_message: str = ""):
        # Отправляем живое обращение директора к агенту и сохраняем в DB
        if director_message:
            await _send_visible(director_message)
            # Сохраняем обращение директора в Interaction для видимости на дашборде
            _save_interaction_for_director(user_id, director_message)
            await asyncio.sleep(0.3)

        # Списываем токены за запуск агента директором
        try:
            from config import FREE_ACCESS_MODE as _FAM
            from token_service import spend_tokens as _st
            if not _FAM:
                _st(user_id, 'agent_task', description=f'{ag["name"]}: {task[:60]}')
        except Exception:
            pass

        # Агентские поручения логируются только в AgentActivityLog (не в Task)
        _task_id = None

        try:
            resp = await asyncio.wait_for(
                _exec_agent_for_director(ag, task, user_id, dialog_context=extra_context),
                timeout=45
            )
        except asyncio.TimeoutError:
            logger.warning("[DIRECTOR] agent exec timeout (%s), 45s limit", ag.get('name'))
            resp = f"Задача передана {ag.get('name', 'агенту')}, результат будет чуть позже."
        if isinstance(resp, Exception) or not resp:
            resp = "Данных нет."

        # Отправляем результат агента как сообщение в чате (живой диалог)
        _agent_name = ag.get('name', 'Агент')
        await _send_visible(f"{_agent_name}:\n{str(resp)[:600]}")
        await asyncio.sleep(0.3)

        _ac = _json.dumps({
            '__agent': {'name': ag.get('name'), 'id': ag.get('id'), 'avatar_url': ag.get('avatar_url', '')},
            'text': resp,
        }, ensure_ascii=False)
        _save_interaction_for_director(user_id, _ac)
        await asyncio.sleep(0.05)

        # Логируем в AgentActivityLog (без создания Task)
        if user_db_id:
            try:
                from models import Session as _TDb2, AgentActivityLog as _AAL2
                _ts2 = _TDb2()
                try:
                    _ts2.add(_AAL2(
                        user_id=user_db_id,
                        activity_type='agent_task',
                        title=task[:200],
                        content=str(resp)[:500],
                        target=ag.get('name', 'Агент'),
                        status='completed',
                    ))
                    _ts2.commit()
                finally:
                    _ts2.close()
            except Exception as _ae:
                logger.debug("[DIRECTOR] activity log error: %s", _ae)

            _task_lc = (task or '').lower()
            _cooldown = 2.0 if any(
                w in _task_lc for w in ('анализ', 'исследов', 'отчёт', 'отчет', 'research', 'report', 'strategy', 'стратег')
            ) else 0.5
            _save_agent_delegation_anchor(
                user_db_id=user_db_id,
                agent_id=ag['id'],
                agent_name=ag['name'],
                task=task,
                result_summary=str(resp)[:600],
                cooldown_hours=_cooldown,
            )
        return str(resp)[:800]

    # ── Прямое обращение к агенту по имени (без LLM-решения) ────────────────────
    # Если сообщение начинается с имени агента — сразу ему делегируем
    _direct_agent = None
    _msg_lower = user_message.lower().strip()
    for _a in _agents:
        _aname = _a['name'].lower()
        # "Кристина, ..." / "Кристина ..." / "@Кристина" / просто имя
        if (_msg_lower.startswith(_aname + ',') or
                _msg_lower.startswith(_aname + ' ') or
                _msg_lower.startswith('@' + _aname) or
                _msg_lower == _aname):
            _direct_agent = _a
            break

    if _direct_agent:
        _direct_ctx_parts = []
        if _user_full_ctx:
            _direct_ctx_parts.append(_user_full_ctx)
        if _history_block.strip():
            _direct_ctx_parts.append(_history_block.strip())
        _agent_ctx = '\n\n'.join(_direct_ctx_parts)
        await _run_agent_task(_direct_agent, user_message, extra_context=_agent_ctx)
        # Агент уже ответил и сохранён в DB — ASI молчит, не дублирует
        return "__agent_handled__"

    # ── Начальное решение ASI ──────────────────────────────────────────────────
    _ctx_hint = f"\n\nКОНТЕКСТ О ПОЛЬЗОВАТЕЛЕ:\n{_user_full_ctx}" if _user_full_ctx else ''

    # Строим компактный список агентов: имя | должность | специализация | умеет
    _agent_caps_lines = []
    for _ac_a in _agents:
        _ac_intg = []
        try:
            _ac_intg = _parse_agent_integrations(
                _ac_a.get('user_api_keys') or '',
                _ac_a.get('python_code') or '',
                _ac_a.get('tools_allowed') or '',
                _ac_a.get('search_scope') or '',
            )
        except Exception:
            pass
        if not _ac_intg:
            _ac_intg = _infer_capabilities_from_role(
                _ac_a.get('job_title') or '',
                _ac_a.get('specialization') or '',
                _ac_a.get('description') or '',
            )
        _ac_caps = ', '.join(_ac_intg[:6]) if _ac_intg else '—'
        _ac_desc = (_ac_a.get('description') or '')[:120]
        _ac_tools = (_ac_a.get('tools_allowed') or '').strip()
        _line = (
            f"• {_ac_a['name']} | {_ac_a.get('job_title','')}"
            f" | {_ac_a.get('specialization','')}"
            f"\n  Умеет: {_ac_caps}"
        )
        if _ac_tools:
            _line += f"\n  Инструменты: {_ac_tools[:120]}"
        if _ac_desc:
            _line += f"\n  О себе: {_ac_desc}"
        _agent_caps_lines.append(_line)
    _caps_block = "\n".join(_agent_caps_lines)

    _decision_prompt = (
        f"Ты — ASI Biont, директор офиса. Тебе нужно принять решение: выполнить запрос самому или отдать агенту.\n\n"
        f"ЗАПРОС ПОЛЬЗОВАТЕЛЯ:\n«{user_message}»\n\n"
        f"ПРОФИЛИ АГЕНТОВ КОМАНДЫ:\n{_caps_block}\n"
        f"{_ctx_hint}{_history_block}\n\n"
        "ЛОГИКА ПРИНЯТИЯ РЕШЕНИЯ (думай по шагам, но выводи только JSON):\n\n"
        "1. Пойми, что РЕАЛЬНО нужно пользователю: чего он хочет достичь? Это разовый вопрос или работа?\n"
        "2. Оцени каждого агента: совпадают ли его умения (Умеет:) и специализация с потребностью?\n"
        "3. Выбери action:\n"
        "   ВАЖНО: для СТРАТЕГИЧЕСКИХ задач (поиск людей, тестировщиков, клиентов, продвижение, маркетинг, "
        "аутрич, кампания, исследование рынка, создание контента, стратегия) — ВСЕГДА выбирай delegate, "
        "multi_delegate или adaptive. НИКОГДА self для таких задач. У тебя есть команда — используй её.\n"
        "   • self — ТОЛЬКО для: простых вопросов ('который час', 'сколько задач'), создания/закрытия задач, "
        "обновления профиля, коротких информационных ответов. НЕ для стратегических задач.\n"
        "   • delegate — если хотя бы один агент может выполнить задачу: исследование, поиск, анализ, стратегия, тексты.\n"
        "   • multi_delegate — задача распадается на части, каждую может взять РАЗНЫЙ агент параллельно.\n"
        "   • adaptive — многошаговая миссия: вывод первого агента питает задачу второго (исследование -> план -> контент). "
        "ПРЕДПОЧИТАЙ adaptive для задач типа 'найди тестировщиков', 'привлеки пользователей', 'запусти кампанию'.\n\n"
        "ВЫБОР АГЕНТА: смотри на поле «Умеет:» и специализацию. Ищи совпадение с нуждой. "
        "Если ни один агент идеально не подходит — ВСЁ РАВНО делегируй ближайшему по специализации. "
        "Агенты универсальны — они могут исследовать, писать тексты, анализировать.\n"
        "director_message — живое обращение к агенту от лица директора, как к коллеге в мессенджере. "
        "ОБЯЗАТЕЛЬНО начинай с имени агента и глагола-действия: "
        "'Кристина, подготовь список площадок для поиска тестировщиков', "
        "'Марк, исследуй рынок AI-тестирования и собери тренды'. "
        "НЕ пиши «ASI поручает», «Поручаю», «Исследовать» (инфинитив) или формальные фразы. НЕ пиши 'Привет' — сразу к делу.\n\n"
        "Ответь ТОЛЬКО JSON без ```:\n"
        '{"action": "self", "team_hint": "какой агент мог бы помочь в следующий раз (1 предл.)"}\n'
        "или\n"
        '{"action": "delegate", "agent_name": "точное имя агента", '
        '"agent_task": "конкретная задача агенту в 1–2 предложения", "director_message": "живое обращение к агенту"}\n'
        "или\n"
        '{"action": "multi_delegate", "director_intro": "что ASI говорит пользователю перед раздачей задач", '
        '"tasks": [{"agent_name": "точное имя", "agent_task": "задача", "director_message": "обращение к агенту"}, ...]}\n'
        "или\n"
        '{"action": "adaptive", "director_intro": "что ASI говорит пользователю — план действий", '
        '"mission_brief": "цель миссии одним предложением", '
        '"first_agent_name": "точное имя первого агента", '
        '"first_agent_task": "задача первого агента", '
        '"director_message": "живое обращение к первому агенту"}'
    )

    # Быстрый пре-фильтр: только очень короткие реплики-подтверждения → ASI отвечает сам
    _ml = user_message.strip()
    _ml_lower = _ml.lower()
    # Стратегические ключевые слова → ВСЕГДА проходят через директора (не bypass)
    _strategic_keywords = ('кампани', 'аутрич', 'тестировщик', 'тестер', 'продвиж', 'маркетинг',
                           'привлеч', 'исследуй', 'исследов', 'стратег', 'поиск людей',
                           'найди люд', 'найди тестер', 'найди тестировщик', 'контент-план',
                           'пользовател', 'клиент')
    _is_strategic = any(kw in _ml_lower for kw in _strategic_keywords)
    # Явные команды делегирования → process_request быстрее (delegate_task как tool)
    # НО если задача стратегическая → пусть директор координирует агентов
    _explicit_delegation = (
        any(kw in _ml_lower for kw in ('поруч', 'попроси', 'передай', 'делегируй'))
        and not _is_strategic
    )
    # Очень короткие бытовые реплики (подтверждения, приветствия) — ASI сам
    _trivial_replies = ('да', 'нет', 'ок', 'окей', 'ладно', 'хорошо', 'давай', 'понял', 'спасибо',
                        'привет', 'хай', 'здравствуй', 'пока', 'стоп', 'отмена')
    _is_trivial = _ml_lower.rstrip('!., ') in _trivial_replies
    if _is_trivial or _explicit_delegation:
        return None  # ASI ответит сам через process_request

    decision_raw = await _quick_ai_call_raw([{"role": "user", "content": _decision_prompt}], max_tokens=250)
    if not decision_raw:
        return None

    _jm = re.search(r'```(?:json)?\s*([\s\S]*?)```', decision_raw)
    try:
        decision = _json.loads(_jm.group(1) if _jm else decision_raw)
    except Exception:
        return None

    action = decision.get('action', 'self')

    # ── self: возвращаем None → управление идёт в process_request с tool-calling ──
    # ASI сам отвечает с доступом ко всем инструментам (создание задач, поиск и т.д.)
    if action == 'self' or (action not in ('delegate', 'multi_delegate', 'adaptive')):
        return None

    # ── adaptive: адаптивный роутинг — ASI выбирает следующего агента динамически ──
    # После каждого результата ASI решает кого вызвать дальше, основываясь на реальном выводе
    if action == 'adaptive':
        _adp_intro = decision.get('director_intro', '')
        if _adp_intro:
            _save_interaction_for_director(user_id, _adp_intro)
            await _send_visible(_adp_intro)
            await asyncio.sleep(0.05)

        _mission_brief = decision.get('mission_brief', user_message)
        _adp_results: list = []   # [(name, task, result)]

        # Сохраняем mission brief в anchor-памяти (персистентно на 24ч)
        try:
            _save_agent_delegation_anchor(
                user_db_id=user_db_id,
                agent_id=0,
                agent_name="__mission__",
                task=user_message,
                result_summary=_mission_brief,
                cooldown_hours=24,
            )
        except Exception:
            pass

        # Первый агент задан в решении
        _first_dm = decision.get('director_message', '')
        if _first_dm:
            _save_interaction_for_director(user_id, _first_dm)
            await asyncio.sleep(0.05)
        _next_agent_name = decision.get('first_agent_name', '')
        _next_agent_task = decision.get('first_agent_task') or user_message
        _next_dm = _first_dm  # director_message для первого агента

        MAX_ADAPTIVE_STEPS = 4
        for _adp_step in range(MAX_ADAPTIVE_STEPS):
            _ag = _find_agent(_next_agent_name)
            if not _ag:
                break

            # Накопленный контекст для агента
            _adp_ctx_parts = []
            if _user_full_ctx:
                _adp_ctx_parts.append(_user_full_ctx)
            if _history_block.strip():
                _adp_ctx_parts.append(_history_block.strip())
            _adp_ctx_parts.append(f"МИССИЯ: {_mission_brief}")
            if _adp_results:
                _prev_block = "РЕЗУЛЬТАТЫ ПРЕДЫДУЩИХ АГЕНТОВ:\n" + "\n\n".join(
                    f"[{_n}]: {_r[:600]}{'...' if len(_r) > 600 else ''}" for _n, _t, _r in _adp_results
                )
                _adp_ctx_parts.append(_prev_block)
            _adp_ctx = '\n\n'.join(_adp_ctx_parts)

            # Добавляем инструкцию агенту — в конце сигнализировать что нужно дальше
            _agent_task_with_signal = (
                f"{_next_agent_task}\n\n"
                + ("ВАЖНО: используй и развивай результаты предыдущих агентов из контекста. "
                   "Не повторяй то что они уже сделали — иди дальше.\n\n" if _adp_results else "")
                + ("В конце своего ответа добавь строку: ПЕРЕДАЮ: [что ещё нужно сделать или кому передать задачу]"
                   if _adp_step < MAX_ADAPTIVE_STEPS - 1 else "")
            )

            _resp = await _run_agent_task(_ag, _agent_task_with_signal, extra_context=_adp_ctx,
                                               director_message=_next_dm)
            _next_dm = ''  # сбрасываем — следующие dm будут из routing
            # Вырезаем служебный сигнал агента из хранимого результата (чистый текст для пользователя)
            _resp_clean = re.sub(r'\n?ПЕРЕДАЮ:\s*[^\n]*', '', _resp).strip() if _resp else _resp
            _adp_results.append((_ag['name'], _next_agent_task, _resp_clean))

            # Определяем остаток агентов после этого шага
            _remaining_agents = [
                a for a in _agents
                if a.get('name') not in {n for n, _, _ in _adp_results}
            ]

            # На последнем шаге или если агентов не осталось — роутинговый вызов не нужен
            if _adp_step >= MAX_ADAPTIVE_STEPS - 1 or not _remaining_agents:
                break

            _remaining_block = "\n".join(
                f"- {a['name']}: {a.get('description','')[:80]}" for a in _remaining_agents[:8]
            )

            _results_so_far = "\n\n".join(
                f"[{n}] задача: {t[:100]}\nрезультат: {r[:300]}" for n, t, r in _adp_results
            )
            # Для сигнала используем оригинальный _resp (с ПЕРЕДАЮ) если он был
            _signal_text = _resp[-400:] if _resp else '(нет сигнала)'
            _routing_prompt = (
                f"Миссия: {_mission_brief}\n\n"
                f"Запрос пользователя: «{user_message}»\n\n"
                f"Агенты уже поработали:\n{_results_so_far}\n\n"
                f"Сигнал последнего агента:\n{_signal_text}\n\n"
                f"Доступные агенты (не задействованные):\n{_remaining_block}\n\n"
                "Реши: миссия выполнена — или нужен ещё один агент?\n"
                "Если выполнена → {\"action\": \"finalize\"}\n"
                "Если нужен ещё агент → {\"action\": \"next\", \"agent_name\": \"точное имя\", "
                "\"agent_task\": \"что сделать (1 предложение)\", \"director_message\": \"прямое обращение\"}\n"
                "Ответь ТОЛЬКО JSON без ```."
            )
            _routing_raw = await _quick_ai_call_raw(
                [{"role": "user", "content": _routing_prompt}], max_tokens=200
            )
            _rjm = re.search(r'```(?:json)?\s*([\s\S]*?)```', _routing_raw or '')
            try:
                _routing = _json.loads(_rjm.group(1) if _rjm else (_routing_raw or '{}'))
            except Exception:
                _routing = {}

            if _routing.get('action') == 'finalize':
                break

            if _routing.get('action') == 'next':
                _dm = _routing.get('director_message', '')
                if _dm:
                    _save_interaction_for_director(user_id, _dm)
                    await asyncio.sleep(0.05)
                _next_agent_name = _routing.get('agent_name', '')
                _next_agent_task = _routing.get('agent_task') or user_message
                _next_dm = _dm  # передаём director_message для следующего шага
            else:
                break

        if not _adp_results:
            return None

        # Финальный синтез ASI
        _adp_combined = "\n\n".join(f"{n}: {r}" for n, _, r in _adp_results)
        _adp_final = await _quick_ai_call_raw([{
            "role": "user",
            "content": (
                f"Миссия: {_mission_brief}\n"
                f"Пользователь просил: «{user_message}»\n\n"
                f"Команда агентов отработала:\n\n{_adp_combined[:1500]}\n\n"
                "ВАЖНО: пользователь уже ВИДЕЛ обращения к агентам и их ответы прямо в чате "
                "(как живой диалог). НЕ ПОВТОРЯЙ их текст.\n\n"
                "Подведи итог как если бы ты писал в том же групповом чате после коллег:\n"
                "(1) Оцени результаты — что полезного, на что обратить внимание.\n"
                "(2) Предложи следующий шаг.\n"
                "2-3 предложения, живо, без markdown. НЕ пересказывай результаты."
            ),
        }], max_tokens=300)
        return _adp_final or "Миссия выполнена"

    # ── Агентный цикл: до 3 раундов, ASI переоценивает после каждого ──────────
    MAX_ROUNDS = 5
    all_results: list = []   # [(agent_name, task, result)]
    called_agents: set = set()
    _rework_count: dict = {}  # agent_name → сколько раз доработка (макс 2)

    for _round in range(MAX_ROUNDS):
        if _round > 0:
            # ASI видит уже полученные результаты и решает — вызвать ещё, доработать или финализировать
            _so_far = "\n\n".join(f"{n} (задача: {t}): {r}" for n, t, r in all_results)
            _remaining = [a for a in _agents if a['name'] not in called_agents]
            _remaining_block = "\n".join(
                f"- {a['name']}: Может: {', '.join(_parse_agent_integrations(a.get('user_api_keys',''), a.get('python_code',''), a.get('tools_allowed',''), a.get('search_scope','')))}"
                for a in _remaining
            ) if _remaining else "(все агенты уже работали)"
            # Список агентов которых можно отправить на доработку (не более 1 раза)
            _reworkable = [n for n, _, _ in all_results if _rework_count.get(n, 0) < 2]
            _rework_hint = ""
            if _reworkable:
                _rework_hint = (
                    "\n   • rework — результат агента неполный, некачественный или не по теме. "
                    "Отправить ЭТОГО ЖЕ агента на доработку с конкретным уточнением что исправить.\n"
                    '{"action": "rework", "agent_name": "имя агента который уже работал", '
                    '"rework_feedback": "что именно доработать — конкретно", '
                    '"director_message": "живое обращение к агенту с просьбой исправить"}\n'
                )
            _reeval_raw = await _quick_ai_call_raw([{
                "role": "user",
                "content": (
                    f"Ты — ASI Biont, директор. Пользователь спросил: «{user_message}»\n\n"
                    f"Агенты уже ответили:\n{_so_far[:1500]}\n\n"
                    f"Ещё не вызванные агенты:\n{_remaining_block}\n\n"
                    "ОЦЕНИ результаты критически:\n"
                    "- Ответ по теме? Содержит конкретику или пустые фразы?\n"
                    "- Задача выполнена полностью или частично?\n"
                    "- Нужно доработать, привлечь другого агента, или достаточно?\n\n"
                    "Выбери действие. Ответь ТОЛЬКО JSON без ```:\n"
                    '{"action": "finalize"}\n'
                    "или\n"
                    '{"action": "delegate", "agent_name": "точное имя", '
                    '"agent_task": "задача", "director_message": "обращение к агенту"}\n'
                    + _rework_hint +
                    "или\n"
                    '{"action": "multi_delegate", "director_intro": "вступление", '
                    '"tasks": [{"agent_name": "...", "agent_task": "...", "director_message": "..."}]}'
                ),
            }], max_tokens=300)
            if not _reeval_raw:
                break
            _jm2 = re.search(r'```(?:json)?\s*([\s\S]*?)```', _reeval_raw)
            try:
                decision = _json.loads(_jm2.group(1) if _jm2 else _reeval_raw)
            except Exception:
                break
            action = decision.get('action', 'finalize')
            if action not in ('delegate', 'multi_delegate', 'rework'):
                break  # finalize → выходим из цикла

        # Строим контекст диалога для агентов: профиль пользователя + история + предыдущие результаты
        _agent_ctx_parts = []
        if _user_full_ctx:
            _agent_ctx_parts.append(_user_full_ctx)
        if _history_block.strip():
            _agent_ctx_parts.append(_history_block.strip())
        if all_results:
            _agent_ctx_parts.append("РАНЕЕ ВЫПОЛНЕННЫЕ ЗАДАЧИ КОМАНДЫ:\n" + "\n".join(
                f"{_n}: {_r[:300]}" for _n, _t2, _r in all_results
            ))
        _agent_ctx = '\n\n'.join(_agent_ctx_parts)

        # ── rework: отправить того же агента на доработку ──────────────────
        if action == 'rework':
            _rw_name = decision.get('agent_name', '')
            _ag = _find_agent(_rw_name)
            if not _ag or _rework_count.get(_ag['name'], 0) >= 2:
                break  # макс 2 доработки на агента
            _rw_feedback = decision.get('rework_feedback', '')
            _dm = decision.get('director_message', '')
            if _dm:
                _save_interaction_for_director(user_id, _dm)
                await asyncio.sleep(0.05)
            # Находим предыдущий результат этого агента
            _prev_result = ''
            for _pn, _pt, _pr in all_results:
                if _pn == _ag['name']:
                    _prev_result = _pr
            # Формируем задачу на доработку
            _rework_task = (
                f"ДОРАБОТКА: {_rw_feedback}\n\n"
                f"Твой предыдущий результат:\n{_prev_result[:800]}\n\n"
                f"Исправь и дополни его с учётом замечаний."
            )
            _rework_count[_ag['name']] = _rework_count.get(_ag['name'], 0) + 1
            _resp = await _run_agent_task(_ag, _rework_task, extra_context=_agent_ctx,
                                          director_message=_dm)
            all_results.append((_ag['name'] + ' (доработка)', _rework_task, _resp))
            continue

        # Выполняем текущее решение
        if action == 'delegate':
            _ag = _find_agent(decision.get('agent_name', ''))
            if not _ag or _ag['name'] in called_agents:
                break
            _dm = decision.get('director_message', '')
            if _dm:
                _save_interaction_for_director(user_id, _dm)
                await asyncio.sleep(0.05)
            called_agents.add(_ag['name'])
            _task = decision.get('agent_task') or user_message
            _resp = await _run_agent_task(_ag, _task, extra_context=_agent_ctx,
                                          director_message=_dm)
            all_results.append((_ag['name'], _task, _resp))

        elif action == 'multi_delegate':
            _intro = decision.get('director_intro', '')
            if _intro:
                _save_interaction_for_director(user_id, _intro)
                await _send_visible(_intro)
                await asyncio.sleep(0.05)
            _valid = []
            _valid_dms = []
            for _t in (decision.get('tasks') or []):
                _ag = _find_agent(_t.get('agent_name', ''))
                if not _ag or _ag['name'] in called_agents:
                    continue
                _tdm = _t.get('director_message', '')
                if _tdm:
                    _save_interaction_for_director(user_id, _tdm)
                called_agents.add(_ag['name'])
                _valid.append((_ag, _t.get('agent_task') or user_message))
                _valid_dms.append(_tdm)
            if not _valid:
                break
            _resps = await asyncio.gather(
                *[_run_agent_task(ag, task, extra_context=_agent_ctx,
                                  director_message=dm)
                  for (ag, task), dm in zip(_valid, _valid_dms)],
                return_exceptions=True,
            )
            for (ag, task), resp in zip(_valid, _resps):
                if isinstance(resp, Exception):
                    resp = "Данных нет."
                all_results.append((ag['name'], task, str(resp)[:800]))

    # ── Финальный синтез ASI ──────────────────────────────────────────────────
    if not all_results:
        return None

    combined = "\n\n".join(f"{n}: {r}" for n, t, r in all_results)
    # Формируем блок поручений: кому что было поручено
    _assignments = "\n".join(f"— {n}, задача: {t[:100]}" for n, t, r in all_results)
    final_response = await _quick_ai_call_raw([{
        "role": "user",
        "content": (
            f"Пользователь спросил: «{user_message}»\n\n"
            f"Ты поручил агентам:\n{_assignments}\n\n"
            f"Отчёты агентов:\n{combined[:2000]}\n\n"
            "ВАЖНО: пользователь уже ВИДЕЛ в чате твоё обращение к агенту и его ответ — "
            "это было как живой диалог в групповом чате. НЕ ПОВТОРЯЙ то что агент уже написал.\n\n"
            "Твоя задача — подвести итог как директор, как будто ты говоришь в том же чате:\n"
            "(1) Оцени результат — что ценного, на что обратить внимание.\n"
            "(2) Предложи следующий шаг.\n\n"
            "СТИЛЬ: 2-3 предложения, живо, как если ты пишешь в групповой чат после ответа коллеги. Без markdown. "
            "ЗАПРЕЩЕНО: 'уже начала', 'уже начал', 'скоро пришлёт', 'через 30 минут', 'работает над этим', "
            "'отработала', 'начал исследование', 'начала поиск' — агент уже ответил в чат, "
            "не делай вид что он ещё работает. "
            "НЕ начинай с 'Привет'. НЕ пересказывай результаты агентов — пользователь их уже прочитал."
        ),
    }], max_tokens=400)
    # Защита от пустых/тёрсных синтезов директора
    if final_response and len(final_response.strip()) < 40 and all_results:
        _fallback_parts = []
        for _n, _t, _r in all_results:
            _fallback_parts.append(f"{_n}: {_r[:200]}")
        final_response = "Команда отработала:\n" + "\n".join(_fallback_parts)
    return final_response or "Команда отработала. Результаты выше ☝️"


async def chat_with_ai(message, context=None, user_id=None, file_content=None,
                       db_session=None, message_type=None, subscription_tier=None,
                       progress_callback=None, web_context: bool = False,
                       exclude_tools: set = None):
    """Главная точка входа. Совместима со всеми вызовами в проекте."""
    logger.info(f"[AGENT] START user={user_id} msg='{str(message)[:50]}...'")

    if user_id is None:
        return {'response': "Ошибка: пользователь не найден", 'tool_calls': []}

    try:
        agent = get_autonomous_agent()
        history_len = len(agent.execution_history)

        # ── Office Director: ASI координирует агентов прямо в чате ──────────
        # Запускаем когда нет явного @упоминания — ASI сам решает делегировать ли
        _director_response = None
        if not _has_explicit_mention(message or ''):
            try:
                _director_response = await _office_director_chat(message, user_id, progress_callback=progress_callback)
            except Exception as _de:
                logger.debug("[DIRECTOR] error, fallback to normal: %s", _de)

        if _director_response is not None:
            # Агент ответил напрямую — ASI молчит (ответ уже в DB)
            if _director_response == "__agent_handled__":
                # Агент уже сохранил ответ в DB — возвращаем пустую строку,
                # фронтенд покажет его через /api/interactions sync
                return {'response': '', 'tool_calls': [], 'tools_used': [], 'agent_info': None}
            # Директор обработал — если пустой ответ (таймаут AI) — fallback
            if not _director_response.strip():
                logger.warning("[DIRECTOR] empty synthesis — falling through to process_request")
                _director_response = None
            else:
                # Очищаем технические детали из ответа директора
                try:
                    from .utils import clean_technical_details as _ctd_dir
                    _cleaned_dir = _ctd_dir(_director_response)
                    if _cleaned_dir and _cleaned_dir.strip():
                        _director_response = _cleaned_dir
                except Exception:
                    pass
                import re as _re_dir
                _director_response = _re_dir.sub(r'\n{2,}', '\n', _director_response)
                _director_response = _re_dir.sub(r'  +', ' ', _director_response).strip()
                # Промежуточные Interaction уже сохранены
                return {
                    'response': _director_response,
                    'tool_calls': [],
                    'tools_used': [],
                    'agent_info': None,  # ASI подводит итог
                }

        response_text = await agent.process_request(
            message, user_id, context, db_session,
            subscription_tier, progress_callback=progress_callback,
            web_context=web_context, exclude_tools=exclude_tools)

        # Очищаем технические детали и названия инструментов из ответа
        if response_text and isinstance(response_text, str):
            try:
                from .utils import clean_technical_details as _ctd_final
                _cleaned = _ctd_final(response_text)
                if _cleaned and _cleaned.strip():
                    response_text = _cleaned
            except Exception:
                pass
            import re as _re
            # Удаляем оставшиеся snake_case tool names (word_word pattern) из текста
            response_text = _re.sub(
                r'\b(?:research_topic|start_delegation_campaign|start_content_campaign|'
                r'delegate_task|add_task|complete_task|delete_task|list_tasks|'
                r'web_search|quick_topic_search|find_relevant_contacts_for_task|'
                r'create_post|publish_to_telegram|publish_to_discord|generate_image|'
                r'send_email|send_outreach_email|send_message_to_user|run_agent_action|'
                r'set_reminder|create_goal|update_goal|list_goals|delete_goal|'
                r'get_delegation_progress|negotiate_by_email|manage_content_campaign|'
                r'manage_delegation_campaign|schedule_background_task|'
                r'find_and_message_relevant_users|reply_to_outreach_email|'
                r'send_follow_up_email|set_contact_alert|find_partners|'
                r'get_news_trends|analyze_situation_and_suggest_tasks|'
                r'update_goal_progress|complete_goal|edit_task|get_task_details|'
                r'check_time_conflicts|cancel_delegation|get_weather_info|'
                r'research_and_plan|analyze_group_opportunities|'
                r'generate_marketing_content|get_message_status|reschedule_task|'
                r'restore_task|accept_delegated_task|reject_delegated_task|'
                r'update_profile|set_content_strategy|edit_post|get_posts|delete_post|'
                r'list_marketplace|save_email_contact|list_email_contacts|get_system_status|'
                r'get_incoming_messages|reply_to_user_message)\b',
                '', response_text
            )
            # Удаляем конструкции "через <tool_name>" оставшиеся
            response_text = _re.sub(r'\s+через\s+(?=[А-Яа-я])', ' через ', response_text)
            # Нормализуем переносы: \n\n → \n, иначе пустые строки в Telegram-чате
            response_text = _re.sub(r'\n{2,}', '\n', response_text)
            # Убираем двойные пробелы от удалённых элементов
            response_text = _re.sub(r'  +', ' ', response_text).strip()

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

        # Агент вклинивается в разговор — фоновая задача, не блокирует ответ
        # Только когда отвечает сам ASI (не через @упоминание конкретного агента)
        if not _answered_agent and not _has_explicit_mention(message or ''):
            asyncio.ensure_future(
                _agent_chimes_in(message or '', response_text or '', user_id)
            )

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

