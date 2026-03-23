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

# ── Integration hint patterns: (substring_in_tool_result_lower, user_recommendation) ─────
# Используются в _extract_intg_hints() для детектирования ограничений инструментов
# и автоматической простановки рекомендаций в ответ агента.
_INTG_HINT_PATTERNS: list[tuple[str, str]] = [
    # Telegram-канал
    ("telegram-канал не настроен",
     "💡 Telegram-канал не настроен. Дашборд → Профиль → укажи @username канала → добавь бота как администратора"),
    ("telegram channel not configured",
     "💡 Telegram channel not configured. Dashboard → Profile → set @username → add bot as admin"),
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


# ── SSRF-защита: преамбула, которая инжектируется перед кодом агента ─────────
# Патчит urllib.request.urlopen, блокируя запросы во внутренние сети (RFC-1918,
# link-local, loopback). Защищает от атак типа Server-Side Request Forgery,
# даже если AST-валидация на upload-этапе пропустила подозрительный код.
_AGENT_CODE_PREAMBLE = '''\
import urllib.request as _ssrf_ur, socket as _ssrf_sk, ipaddress as _ssrf_ia
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
    if pre:
        blocks.append(('# (инициализация)', pre))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(code)
        blocks.append((m.group(0).strip(), code[m.start():end].strip()))

    lines = [_SECTION_RUNNER]
    for title, block in blocks:
        lines.append(
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
    reason = next((l.strip() for l in text_clean.splitlines() if l.strip()), text_clean[:80])
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


# === Shared aiohttp session для DeepSeek API ===
# Переиспользование TCP/TLS-соединений экономит ~200-500мс на каждом вызове
_SHARED_AI_SESSION: aiohttp.ClientSession | None = None

async def _get_shared_ai_session() -> aiohttp.ClientSession:
    global _SHARED_AI_SESSION
    if _SHARED_AI_SESSION is None or _SHARED_AI_SESSION.closed:
        _SHARED_AI_SESSION = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=120, connect=10)
        )
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
            "max_tokens": kwargs.pop("max_tokens", 1200),
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
         _max_retries = 1 if (api_timeout and api_timeout < 40) else 2
         for _attempt in range(_max_retries):
          try:
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
                if resp.status < 500 or _attempt >= 1:
                    raise Exception(f"AI call failed: {resp.status} {error[:200]}")
                logger.warning(f"[AI] Server error {resp.status}, retrying...")
                await asyncio.sleep(2)
          except asyncio.TimeoutError:
            if _attempt >= 1:
                raise
            logger.warning("[AI] Timeout, retrying...")
            await asyncio.sleep(2)

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
        'research_topic', 'web_search', 'quick_topic_search', 'research_and_plan',
        'get_news_trends', 'analyze_situation_and_suggest_tasks',
        # Contacts / outreach
        'find_relevant_contacts_for_task', 'set_contact_alert', 'find_partners',
        'save_email_contact', 'list_email_contacts',
        # Content creation
        'create_post', 'generate_image', 'generate_marketing_content',
        'publish_to_telegram', 'publish_to_discord',
        # Email (commonly requested even without keyword)
        'send_email', 'check_emails',
        # Delegation & agents
        'delegate_task', 'run_agent_action',
        # Campaigns — conversation can span multiple turns
        'start_content_campaign', 'manage_content_campaign',
        'start_delegation_campaign', 'start_email_campaign',
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
                      'send_follow_up_email', 'start_email_campaign', 'add_email_leads'},
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
                         'ответить', 'ответь', 'reply', 'переслать', 'перешли'],
            'tools': {'send_message_to_user', 'reply_to_user_message',
                      'get_incoming_messages', 'find_and_message_relevant_users'},
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
    MAX_HISTORY_CHARS = 3000   # limit history to ~1K tokens

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
                _safe_default = 'autonomous agent language:python repos:>10 followers:>5'
                logger.warning(
                    "[ACTION] search_users bad query=%r → replacing with safe default=%r",
                    _raw_query, _safe_default,
                )
                action_params = dict(action_params)
                action_params['query'] = _safe_default
                # Оповещаем через output что заменили запрос
                _fix_note = (
                    f"⚠️ Запрос '{_raw_query}' некорректен для GitHub Users Search "
                    f"(email-адреса и случайные слова не поддерживаются). "
                    f"Автоматически использован: '{_safe_default}'\n"
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
            except Exception as _e:
                logger.debug("suppressed: %s", _e)
        try:
            _kwargs = dict(stdout=_aio_ea.subprocess.PIPE, stderr=_aio_ea.subprocess.PIPE, env=env)
            if _is_linux:
                _kwargs['preexec_fn'] = _resource_limits
            proc = await _aio_ea.create_subprocess_exec(_sys_ea.executable, '-c', py_code, **_kwargs)
            try:
                stdout, stderr = await _aio_ea.wait_for(proc.communicate(), timeout=float(API_TIMEOUT_SCRIPT))
                out = stdout.decode('utf-8', errors='replace').strip()[:2000]
                err = stderr.decode('utf-8', errors='replace').strip()[:500]
            except _aio_ea.TimeoutError:
                proc.kill()
                return {"status": "error", "error": f"Timeout ({API_TIMEOUT_SCRIPT}s) — скрипт выполнялся слишком долго"}
            # Prepend fix note if query was replaced
            if _fix_note and out:
                out = _fix_note + out
            elif _fix_note:
                out = _fix_note
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

                    # Если _fix_tool_params заблокировал вызов (нет обязательного контента) — пропускаем
                    if params.pop('__skip__', False):
                        results.append({
                            "tool": tool_name, "success": False,
                            "error": f"{tool_name}: нет контента для публикации — сначала сгенерируй текст поста",
                            "reason": reason
                        })
                        logger.warning("[EXEC] %s SKIPPED: no content to publish", tool_name)
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

                    results.append({"tool": tool_name, "success": True,
                                    "result": result, "reason": reason})

                    # ── Decision Log (#6): записываем стратегические решения ──
                    _STRATEGIC_TOOLS = {
                        'send_outreach_email', 'start_email_campaign', 'run_agent_action',
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
            # Автоподстановка sender_name из активного агента
            # Если AI не передал sender_name, берём имя активного агента
            if not params.get('sender_name'):
                _agent = self._active_agent_data.get(params.get('user_id'))
                if _agent and _agent.get('name'):
                    params['sender_name'] = _agent['name']
                    logger.info(f"[FIX_PARAMS] send_email: set sender_name='{_agent['name']}' from active agent")

        elif tool_name in ('send_outreach_email', 'negotiate_by_email', 'send_follow_up_email'):
            # AI путает send_email (с sender_name) с send_outreach_email (без него)
            # Просто убираем неправильный параметр; у universal stripping нет этого в белом списке
            params.pop('sender_name', None)
            params.pop('from_name', None)
            params.pop('from_email', None)   # не часть send_outreach_email
            # Приводим email к нижнему регистру и убираем лишние слэши
            if 'recipient_email' in params and isinstance(params['recipient_email'], str):
                params['recipient_email'] = params['recipient_email'].strip().lower().lstrip('/')

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
                "\n🔑 IMPLICIT TASK COMPLETION (PRIORITY #1): when user reports they DID something (ordered, bought, paid, set up, wrote, sent, finished, figured out, arranged, called, completed, launched, picked up, received, fixed, installed, assembled, cooked, cleaned — ANY past tense verb) — IMMEDIATELY COMPARE with EVERY task from OVERDUE/TODAY/TOMORROW sections. If there's a task matching the MEANING of what they described — IMMEDIATELY call complete_task(task_id=ID) WITHOUT questions. Examples: 'I ordered groceries' + task 'Order groceries for breakfast [id=42]' → complete_task(task_id=42). 'Set up the website' + task 'Set up website for AI indexing' → complete_task. 'Called the doctor' + task 'Make doctor appointment' → complete_task. ⚠️ This is the PRIMARY way users close tasks. Missing this signal = CRITICAL ERROR."
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
                "\nОбсуждают маркетинг, продвижение, рост, привлечение → research_topic(тренды канала/рынка) + find_relevant_contacts_for_task + предложи start_content_campaign."
                "\nОбсуждают конкурентов, рынок, нишу, позиционирование → research_topic + дай экспертный анализ с конкретными альтернативами."
                "\nПользователь жалуется что что-то не работает или топчется на месте → предложи АЛЬТЕРНАТИВНЫЙ подход, исследуй через research_topic, НЕ повторяй те же советы что уже давал."
                "\nПользователь говорит о финансах, доходе, монетизации → research_topic(монетизация/инструменты) + предложи конкретные варианты под его профиль."
                "\nОбсуждают команду, найм, поиск людей → find_relevant_contacts_for_task + set_contact_alert + предложи start_delegation_campaign."
                "\nЕсли пользователь в третий раз возвращается к той же теме без прогресса → СМЕНИ подход: дай принципиально другое предложение, изучи через research_topic свежие данные, предложи другой канал или инструмент."
                "\n🔑 НЕЯВНОЕ ЗАВЕРШЕНИЕ ЗАДАЧ (ПРИОРИТЕТ №1): когда пользователь сообщает что ОН СДЕЛАЛ что-то (заказал, купил, оплатил, настроил, написал, отправил, закончил, разобрался, договорился, позвонил, прошёл, запустил, забрал, получил, починил, установил, собрал, приготовил, убрал — ЛЮБОЙ глагол совершённого вида) — СРАЗУ СРАВНИ с КАЖДОЙ задачей из секций ПРОСРОЧЕНО/СЕГОДНЯ/ЗАВТРА. Если есть задача по СМЫСЛУ совпадающая с тем что он описал — НЕМЕДЛЕННО вызови complete_task(task_id=ID) БЕЗ вопросов. Примеры: 'Я заказал продукты' + задача 'Заказать продукты на завтрак [id=42]' → complete_task(task_id=42). 'Настроил сайт' + задача 'Настроить сайт' → complete_task. 'Позвонил врачу' + задача 'Записаться к врачу' → complete_task. ⚠️ Это ГЛАВНЫЙ способ как люди закрывают задачи. Пропустить сигнал = КРИТИЧЕСКАЯ ОШИБКА."
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

            # ═══ ИСТОРИЯ ДИАЛОГА (загружаем рано — нужна для anti-repetition) ═══
            from .conversation_history import get_conversation_history
            full_history = get_conversation_history(user_id, session=None, limit=6)

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

            if len(full_history) > 8:
                old_msgs = full_history[:-6]
                history = full_history[-6:]
                topics = CognitiveEngine.extract_conversation_topics(old_msgs)
                if topics:
                    _lbl = "PREVIOUSLY DISCUSSED" if user_lang == 'en' else "РАНЕЕ ОБСУЖДАЛИ"
                    dynamic_context += f"\n\n[{_lbl}: {', '.join(topics)}]"
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

            # ═══ АНТИ-ПОВТОР: для коротких реплик (привет/привет) инжектируем предыдущие ответы ═══
            _msg_lower_ar = (user_message or '').strip().lower().rstrip('!., ')
            _trivial_ar = _msg_lower_ar in ('привет', 'хай', 'здравствуй', 'здравствуйте',
                                             'добрый день', 'доброе утро', 'добрый вечер',
                                             'как дела', 'что нового', 'что делаешь')
            if _trivial_ar and history:
                _prev_ai_responses = []
                for _h_msg in reversed(history):
                    if _h_msg.get('role') == 'assistant' and _h_msg.get('content'):
                        _prev_ai_responses.append(_h_msg['content'][:150])
                    if len(_prev_ai_responses) >= 3:
                        break
                if _prev_ai_responses:
                    _ar_block = '\n---\n'.join(_prev_ai_responses)
                    messages.append({"role": "system", "content": (
                        f"АНТИ-ПОВТОР: вот твои последние ответы пользователю — НЕ ПОВТОРЯЙ их содержание. "
                        f"Скажи что-то ПРИНЦИПИАЛЬНО ДРУГОЕ. Другой тон, другая тема, другой подход.\n"
                        f"Уже сказано:\n{_ar_block}"
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
            MAX_ITERATIONS = 2
            # 5 параллельных инструментов/итерацию: больше работы за один API-вызов → меньше round-trips → меньше токенов
            MAX_TOOLS_PER_ITERATION = 5
            seen_tools = set()  # Для предотвращения дублей
            # Критичные инструменты — лимит вызовов за сессию
            once_only_tools = {'create_post', 'delete_post', 'start_content_campaign', 'start_delegation_campaign'}  # строго 1 раз
            multi_limit_tools = {'add_task': 5, 'update_profile': 2, 'create_goal': 3, 'run_agent_action': 8, 'send_email': 5, 'delegate_task': 5}  # лимиты per turn
            used_once_only = set()
            multi_limit_counts = {}

            # Smart tool filtering — reduce tokens sent to API
            self._current_user_id = user_id
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
                             'расписани', 'план', 'schedule', 'plan', 'campaign')
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
                return self._finalize_response(
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
                            f" Полные данные исследования сохранены в заметки («{_titles_str}»)."
                            f" Ответь живо и коротко: скажи что нашёл (2-4 предложения) и в конце"
                            f" одной фразой как бы между делом упомяни что сохранил подробности в заметки."
                            f" Говори от себя, без шаблонов. НЕ перечисляй и НЕ копируй весь текст."
                        )
                        _note_hint_en = (
                            f" Full research saved to notes («{_titles_str}»)."
                            f" Reply naturally and briefly: share what you found (2-4 sentences) and at the end"
                            f" casually mention you saved the details to notes — as part of your own words."
                            f" No templates. Do NOT list or copy the full text."
                        )
                    if user_lang == 'en':
                        messages.append({"role": "system", "content": (
                            "Summarize results briefly (2-3 sentences, max 400 chars). "
                            "Rephrase in your own words. Preserve URLs. Don't repeat delegate_task responses."
                            + _note_hint_en
                        )})
                    else:
                        messages.append({"role": "system", "content": (
                            "Кратко подытожь (2-3 предложения, до 400 символов). "
                            "Своими словами. Сохраняй URL. Не повторяй ответы delegate_task."
                            + _note_hint_ru
                        )})

                # Text-only call (no tools) uses shorter timeout + fewer tokens
                _timeout = API_TIMEOUT_NORMAL if not _allow_tools else None
                _max_tok = 300 if _is_last_iter and all_execution_results else 500
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
                    # ── Пре-анонс для delegate_task (не отправляем — уже сохраняется в _save_ifd) ──
                    if _cb and _name == 'delegate_task':
                        pass  # delegate_task handler saves director message via _save_ifd
                    elif _cb:
                        try:
                            await _cb(self._tool_progress_text(_name, iteration + 1, lang=user_lang))
                        except Exception as _e:
                            logger.debug("suppressed: %s", _e)
                    try:
                        _results = await self.execute_actions(
                            [{"tool": _name, "params": _args, "reason": _reason}],
                            user_id, session=None,
                            user_message=user_message, web_context=web_context)
                        _r = _results[0] if _results else {"success": False, "error": "no result"}
                        if _r.get('success'):
                            _rc = json.dumps(_r['result'], ensure_ascii=False, default=str)
                            _rc = CognitiveEngine.compress_tool_result(_rc)
                            try: get_learner().record_tool_result(user_id, _name, True)
                            except Exception: pass

                            # ── Авто-заметка: длинный результат research → save_note ─────────
                            # Пользователю не нужно говорить "запиши" — длинное исследование
                            # автоматически сохраняется в заметки чтобы не терялось в чате.
                            _RESEARCH_AUTO_SAVE = {'research_topic', 'web_search', 'quick_topic_search'}
                            _raw_result_str = _r['result'] if isinstance(_r['result'], str) else _rc
                            if _name in _RESEARCH_AUTO_SAVE and len(_raw_result_str) > 800:
                                try:
                                    from .handlers import save_note as _auto_save_note
                                    _note_q = (
                                        _args.get('query') or _args.get('topic') or
                                        _args.get('prompt') or _name
                                    )
                                    _note_title = str(_note_q)[:80]
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
                                    # Уведомление — НЕ через отдельный _cb, AI сам вплетёт в ответ
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
                                    except Exception: pass
                                except Exception as _e:
                                    logger.debug("suppressed: %s", _e)
                        else:
                            _rc = json.dumps({"error": str(_r.get('error', ''))}, ensure_ascii=False)
                            try: get_learner().record_tool_result(user_id, _name, False)
                            except Exception: pass
                    except Exception as _err:
                        logger.error(f"[EXEC] {_name} parallel crashed: {_err}\n{traceback.format_exc()}")
                        _r = {"success": False, "error": str(_err)}
                        _rc = json.dumps({"error": str(_err)}, ensure_ascii=False)
                        try: get_learner().record_tool_result(user_id, _name, False)
                        except Exception: pass
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
            return self._finalize_response(
                final_text, user_message, user_id, all_execution_results)

        except Exception as e:
            logger.error(f"[AGENT] Error: {e}\n{traceback.format_exc()}")
            # Если инструменты уже отработали — формируем ответ из результатов вместо ошибки
            if all_execution_results and any(r.get('success') for r in all_execution_results):
                logger.info("[AGENT] Tools succeeded before crash — building response from results")
                return self._finalize_response(
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
                    "Сбой на моей стороне — напиши ещё раз.",
                    "Что-то упало у меня, не у тебя. Повтори?",
                    "Потерял ответ. Напиши снова — разберёмся.",
                    "Технический сбой, сейчас разберусь. Попробуй ещё раз.",
                    "У меня что-то пошло не так. Повтори запрос?",
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
        # НО: если delegate_task использован и агент уже ответил в чате — не дублируем
        _had_agent_delegate = any(r.get('tool') == 'delegate_task' and 'уже ответил' in str(r.get('result', '')) for r in execution_results)
        if tools_used and len((final or '').strip()) < 40 and not _had_agent_delegate:
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
            dynamic_context = ctx.get('dynamic_context', '')
            sub_tier = ctx['sub_tier']
            user_lang = ctx.get('user_lang', 'ru')

            # Добавляем режим в системный промпт (bilingual)
            # Mode instructions — only mode-specific logic, style rules inherited from base prompt
            _DATA_VERIFY_EN = (
                "\nDATA RULE: MEMORY section = background only. Cite ONLY data from tools as current. "
                "Empty list_tasks = no tasks. Empty list_goals = no goals."
            )
            _DATA_VERIFY_RU = (
                "\nПРАВИЛО ДАННЫХ: секция ПАМЯТЬ = фон. Актуальны ТОЛЬКО данные из инструментов. "
                "Пустой list_tasks = нет задач. Пустой list_goals = нет целей."
            )
            _PROACTIVE_CORE_EN = (
                "Use tools (list_tasks, list_goals, get_news_trends) for real data. "
                "Do NOT call research_topic — use get_news_trends. Don't invent data.\n"
                "Do NOT auto-publish posts. Link: https://asibiont.com/dashboard\n"
                "GOAL FOCUS: pick highest-priority lowest-progress goal → use available agent/tool → "
                "propose action DIFFERENT from recent directives. Only suggest tools that exist in context.\n"
                "If directives repeat (research, find contacts) — SWITCH APPROACH (DMs, communities, partnerships)."
            )
            _PROACTIVE_CORE_RU = (
                "Используй инструменты (list_tasks, list_goals, get_news_trends) для реальных данных. "
                "НЕ вызывай research_topic — используй get_news_trends. Не выдумывай данные.\n"
                "НЕ публикуй посты автоматически. Ссылка: https://asibiont.com/dashboard\n"
                "ФОКУС НА ЦЕЛЬ: выбери цель с наибольшим приоритетом и наименьшим прогрессом → "
                "используй доступного агента/инструмент → предложи действие ОТЛИЧНОЕ от последних директив. "
                "Предлагай только инструменты из контекста.\n"
                "Если директивы повторяются (исследовать, найти контакты) — СМЕНИ ПОДХОД (DM, сообщества, партнёрства)."
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
                    ),
                    'task_assist': (
                        "\n\n[РЕЖИМ: ПОМОЩЬ С ЗАДАЧЕЙ]\n"
                        "Помоги решить — СДЕЛАЙ, не предлагай. Используй инструменты, дай конкретный результат. Без новых задач."
                    ),
                    'proactive': (
                        "\n\n[РЕЖИМ: ПРОАКТИВНОЕ СООБЩЕНИЕ]\n"
                        "Пиши как обычный ответ в чате — живо, прямо, с характером. "
                        "Человек НЕ ДОЛЖЕН чувствовать что это системное сообщение.\n"
                        + _PROACTIVE_CORE_RU + _DATA_VERIFY_RU
                    ),
                    'result_check': (
                        "\n\n[РЕЖИМ: ПОЗДРАВЛЕНИЕ]\n"
                        "Задача выполнена. Отреагируй живо — 1-2 предложения, максимум 200 символов. Не начинай с 'Поздравляю!'"
                    ),
                    'anchor': (
                        "\n\n[РЕЖИМ: ANCHOR ENGINE]\n"
                        "Получены ЯКОРЯ. Стоит ли отвлекать человека? Если нет → верни SKIP.\n"
                        "Если да → используй инструменты по теме. ОДНА тема на сообщение. Закончи вопросом/предложением.\n"
                        + _PROACTIVE_CORE_RU + _DATA_VERIFY_RU
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
    """True если сообщение начинается с @Агент или 'ИмяАгента,'."""
    return bool(re.match(r'@\w+\b', (message or '').strip()))


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


async def _quick_ai_call_raw(messages: list, max_tokens: int = 250, _caller: str = '') -> str:
    """Прямой вызов DeepSeek без tool calling — быстро и без overhead."""
    try:
        _sess = await _get_shared_ai_session()
        async with _sess.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": DEEPSEEK_MODEL,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": 0.7,
                },
                timeout=aiohttp.ClientTimeout(total=25),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    _usage = data.get('usage', {})
                    _prompt_t = _usage.get('prompt_tokens', 0)
                    _compl_t = _usage.get('completion_tokens', 0)
                    logger.info(f"[DEEPSEEK] {_caller or 'quick_ai'}: prompt={_prompt_t} compl={_compl_t}")
                    return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.debug("[DIRECTOR] AI call error: %s", e)
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
    давалось за последние 30 минут — идентичный текст начала директивы).
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
            # ── Дедупликация директив (только для agent_msg без __agent) ────────────
            # Сравниваем первые 80 символов — достаточно чтобы убедиться что поручение то же самое
            _dedup_prefix = content.strip()[:80]
            # Проверяем только если это директива (не отчёт агента с __agent)
            _is_directive = '"__agent"' not in content
            if _is_directive:
                _since = _dt_dir.now(_tz_dir.utc) - _td_dir(minutes=5)
                _existing = (
                    _s.query(_Intr)
                    .filter(
                        _Intr.user_id == _u.id,
                        _Intr.message_type == 'agent_msg',
                        _Intr.created_at >= _since,
                        _Intr.content.like(_dedup_prefix + '%'),
                    )
                    .first()
                )
                if _existing:
                    logger.warning(
                        "[DIRECTOR] DEDUP: identical directive already sent %s ago for tg=%s, skipping: %s...",
                        str(_dt_dir.now(_tz_dir.utc) - _existing.created_at.replace(tzinfo=_tz_dir.utc))[:7],
                        telegram_id, _dedup_prefix[:40]
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
        # Есть явный список — показываем его с аннотацией
        return ', '.join(_explicit[:10])

    # tools_allowed пустой → выводим рекомендации из интеграций + роли
    recommended: list = []
    _lbl_text = ' '.join(l.lower() for l in intg_labels)

    # Интеграции → конкретные инструменты
    _INTG_TOOL_MAP = [
        # Email / почта
        (('почт', 'mail', 'imap', 'smtp', 'gmail', 'яндекс почт', 'resend', 'sendgrid', 'mailgun'),
         ['send_outreach_email', 'check_emails', 'reply_to_outreach_email', 'send_follow_up_email',
          'save_email_contact', 'list_email_contacts', 'find_relevant_contacts_for_task']),
        # GitHub / GitLab
        (('github', 'gitlab', 'bitbucket'),
         ['run_agent_action', 'find_relevant_contacts_for_task', 'save_email_contact', 'web_search']),
        # RSS / новости / NewsAPI
        (('rss', 'лент', 'feed', 'newsapi', 'новост', 'тасс', 'habr'),
         ['run_agent_action', 'get_news_trends', 'research_topic', 'web_search', 'add_task']),
        # Биржа / крипта / Alpha Vantage
        (('биржевые', 'alpha vantage', 'binance', 'bybit', 'coinbase', 'крипт'),
         ['run_agent_action', 'web_search', 'research_topic', 'add_task', 'update_goal_progress']),
        # Мессенджеры / Slack / Discord / Telegram
        (('slack', 'discord'),
         ['run_agent_action', 'find_and_message_relevant_users', 'send_message_to_user']),
        (('telegram',),
         ['publish_to_telegram', 'create_post', 'run_agent_action']),
        # CRM
        (('crm', 'amocrm', 'битрикс', 'hubspot', 'pipedrive', 'salesforce', 'zoho'),
         ['run_agent_action', 'save_email_contact', 'find_relevant_contacts_for_task', 'delegate_task']),
        # Маркетплейсы
        (('ozon', 'wildberries', 'авито', 'shopify', 'wb'),
         ['run_agent_action', 'research_topic', 'web_search', 'add_task']),
        # Таблицы / Notion / Airtable
        (('sheets', 'airtable', 'notion', 'таблиц', 'gspread'),
         ['run_agent_action', 'add_task', 'update_goal_progress', 'research_topic']),
        # Платежи
        (('stripe', 'юкасса', 'платёж', 'payment'),
         ['run_agent_action', 'web_search', 'add_task']),
        # OpenWeather / погода
        (('погода', 'openweather', 'weather'),
         ['run_agent_action', 'get_weather_info', 'add_task']),
        # Генерация изображений
        (('генерац', 'replicate', 'изображен', 'image'),
         ['generate_image', 'create_post', 'publish_to_telegram']),
        # VK / соцсети
        (('вконтакт', 'vk', 'instagram', 'twitter'),
         ['run_agent_action', 'create_post', 'generate_marketing_content', 'web_search']),
        # Google Calendar
        (('calendar', 'календар'),
         ['run_agent_action', 'set_reminder', 'add_task', 'check_time_conflicts']),
        # LinkedIn / найм / HR
        (('linkedin', 'superjob', 'hh.ru', 'headhunter'),
         ['run_agent_action', 'find_relevant_contacts_for_task', 'save_email_contact', 'web_search']),
        # Яндекс.Директ / реклама
        (('директ', 'яндекс.директ', 'adwords', 'mytarget'),
         ['run_agent_action', 'web_search', 'research_topic', 'generate_marketing_content']),
    ]
    seen: set = set()
    for _kws, _tools in _INTG_TOOL_MAP:
        if any(w in _lbl_text for w in _kws):
            for _t in _tools:
                if _t not in seen:
                    seen.add(_t)
                    recommended.append(_t)

    # Роль → добавляем характерные инструменты если ещё не добавлены
    _role_text = (
        (agent.get('job_title') or '') + ' ' +
        (agent.get('specialization') or '') + ' ' +
        (agent.get('description') or '')
    ).lower()

    _ROLE_TOOL_MAP = [
        (('аналитик', 'analyst', 'исследован', 'research', 'data', 'данн'),
         ['web_search', 'research_topic', 'quick_topic_search', 'analyze_situation_and_suggest_tasks',
          'add_task', 'update_goal_progress']),
        (('маркетолог', 'marketing', 'smm', 'контент', 'content', 'продвиж', 'promo', 'реклам'),
         ['web_search', 'generate_marketing_content', 'create_post', 'research_topic',
          'start_content_campaign', 'publish_to_telegram']),
        (('менеджер', 'manager', 'project', 'проект', 'pm', 'руковод', 'координат'),
         ['add_task', 'delegate_task', 'update_goal_progress', 'analyze_situation_and_suggest_tasks',
          'list_tasks', 'set_reminder']),
        (('разработчик', 'developer', 'engineer', 'программист', 'backend', 'frontend', 'fullstack'),
         ['run_agent_action', 'web_search', 'research_topic', 'add_task']),
        (('продаж', 'sales', 'outreach', 'лидоген', 'lead'),
         ['send_outreach_email', 'find_relevant_contacts_for_task', 'save_email_contact',
          'start_delegation_campaign', 'web_search']),
        (('hr', 'рекрутер', 'recruiter', 'найм', 'подбор'),
         ['find_relevant_contacts_for_task', 'save_email_contact', 'send_outreach_email',
          'run_agent_action', 'web_search']),
        (('финанс', 'finance', 'бухгалт', 'accountant', 'инвест', 'invest'),
         ['run_agent_action', 'web_search', 'research_topic', 'add_task', 'update_goal_progress']),
        (('копирайт', 'copywriter', 'журналист', 'писател', 'редактор'),
         ['generate_marketing_content', 'web_search', 'research_topic', 'create_post']),
        (('ассистент', 'assistant', 'помощник', 'secretary', 'секретар'),
         ['add_task', 'set_reminder', 'check_time_conflicts', 'send_email', 'list_tasks',
          'delegate_task', 'update_goal_progress']),
    ]
    for _kws, _tools in _ROLE_TOOL_MAP:
        if any(w in _role_text for w in _kws):
            for _t in _tools:
                if _t not in seen:
                    seen.add(_t)
                    recommended.append(_t)

    # Всегда базовые инструменты если ещё ничего нет
    _base = ['web_search', 'research_topic', 'add_task', 'update_goal_progress']
    for _t in _base:
        if _t not in seen:
            seen.add(_t)
            recommended.append(_t)

    return ', '.join(recommended[:10])


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
                      .filter_by(author_id=user_db_id, status='active')
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
            if profile.current_plans:
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
                    for lk in (agent.get('user_api_keys') or '').splitlines()
                    if '=' in lk and any(
                        f'{p}_USER' in lk.upper()
                        for p in ('GMAIL', 'YANDEX', 'MAILRU', 'IMAP', 'EMAIL', 'SMTP')
                    )
                ), '')
            _label = f"{_em} {_ih}" + (f" ({_acc})" if _acc else '') + f" → {_tool}"
            _intg_parts.append(_label)
            _seen_emojis.add(_em)
        _intg_line = '\nПодключено у тебя:\n  ' + '\n  '.join(_intg_parts) if _intg_parts else ''

    # ── Подсказки по интеграциям — компактная универсальная инструкция ─────────
    _intg_action_hint = ""
    if _intg_hint:
        _intg_names = ', '.join(_intg_hint[:8])
        _intg_action_hint = (
            f"\n\n🔗 Твои интеграции: {_intg_names}.\n"
            "Каждая интеграция — это НЕ один инструмент, а НАБОР ВОЗМОЖНОСТЕЙ. Думай шире:\n"
            "  • RSS = не только новости, но и мониторинг конкурентов, поиск авторов/экспертов, подготовка контента, трендовые темы\n"
            "  • Почта = не только рассылка, но и follow-up, нетворкинг, мониторинг ответов, персональные предложения\n"
            "  • GitHub = не только код, но и поиск разработчиков, анализ проектов, networking через issues/PR\n"
            "  • CRM/Таблицы = не только записи, но и аналитика, отчёты, выгрузка данных для других задач\n"
            "  • Соцсети/Мессенджеры = не только постинг, но и мониторинг, поиск аудитории, community building\n"
            "Для внешних сервисов → run_agent_action. Для email → send_outreach_email / check_emails. "
            "web_search / research_topic — универсальные, доступны всегда. "
            "Комбинируй интеграции с платформенными инструментами для максимального результата.\n"
            "Сам решай КАК использовать интеграции — исходя из задачи, цели и контекста пользователя."
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
            _delegate_example = f"Нашёл данные для коллеги → DELEGATE[{_fn}]: задача с конкретными данными."

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

            "🧠 КАК ТЫ ДУМАЕШЬ (каждый цикл — заново!):\n"
            "Ты — живой специалист с характером, а не робот-исполнитель. Перед каждым действием:\n"
            "1. СИТУАЦИЯ: что уже сделано в предыдущих циклах? Что сработало, что нет?\n"
            "2. НОВЫЙ ПОДХОД: если предыдущий канал/метод не дал результата — РАДИКАЛЬНО СМЕНИ стратегию.\n"
            "   Не повторяй то же самое с другими словами — ищи принципиально новый путь.\n"
            "3. КОНКРЕТНОЕ ДЕЙСТВИЕ: выбери ОДНО самое результативное действие и сделай его.\n"
            "   Лучше 1 отправленное письмо реальному человеку, чем 10 найденных каналов.\n"
            "4. ОЦЕНКА: после каждого действия — оцени результат честно. Если не сработало, скажи прямо.\n\n"

            "📡 РАЗНООБРАЗИЕ ПОДХОДОВ (используй РАЗНЫЕ в РАЗНЫХ циклах):\n"
            "   Доступные каналы: web_search (hh.ru, Хабр, форумы — всегда)"
            + (f", {', '.join(_intg_hint[:5])}" if _intg_hint else "")
            + " — чередуй.\n"
            "   Один канал — максимум 1 раз за 3 цикла. Если застрял — пробуй неочевидное.\n"
            "   ⚠️ НЕ ДЕЛАЙ ВИД что можешь использовать канал, который НЕ подключён у тебя.\n"
            "   Используй ТОЛЬКО каналы из списка выше и из раздела 'Подключено у тебя'.\n"
            "   Если для цели нужен канал, которого нет — напиши в отчёте:\n"
            "   «Для продвижения по этой цели был бы полезен [канал]. Рекомендую подключить в настройках.»\n\n"

            "⚡ ДЕЙСТВУЙ, НЕ ПЛАНИРУЙ:\n"
            "   70% — конкретные действия (письма, сохранение контактов, посты). 30% — поиск.\n"
            "   ОДИН ПОИСК → ОДНО ДЕЙСТВИЕ: нашёл имя+email → save_email_contact + send_outreach_email.\n"
            "   Максимум 2 web_search, 1 create_post за сессию. После поиска — КОНВЕРТИРУЙ.\n\n"

            "🚫 КРИТИЧНО:\n"
            "   НЕ придумывай email (только реальные найденные). @username ≠ email.\n"
            "   save_email_contact БЕЗ send_outreach_email = незавершённая цепочка.\n"
            "   НЕ создавай задачи (add_task) — координатор уже давал задачу, выполняй.\n"
            "   НЕ используй каналы, которые не подключены. Не пиши 'отправлю через LinkedIn' если LinkedIn нет.\n"
            "   Если инструмент не сработал из-за отсутствия интеграции — СООБЩИ в отчёте что нужно подключить.\n"
            "   В отчёте — ФАКТЫ от первого лица, 2-4 предложения. НЕ пиши о себе в 3-м лице.\n\n"

            "📊 ФИНАЛ: update_goal_progress — ОБЯЗАТЕЛЬНО последний инструмент в сессии.\n\n"

            "📝 ОТЧЁТ — живой, содержательный, от первого лица:\n"
            f"  ЗАПРЕЩЕНО: '{agent['name']} нашла', '{agent['name']} получил'. ПРАВИЛЬНО: 'Я нашла', 'Получил'.\n"
            f"  Хороший отчёт: «{_ex_checked} входящие — ответ от Марии К.: готова тестировать, просит ссылку.»\n"
            f"  Хороший отчёт: «{_ex_found} на hh.ru 5 QA-резюме с публичным email, {_ex_added} 2 контакта и {_ex_wrote}.»\n"
            f"  Плохой отчёт: «Задачу выполнила.» / «Приступаю к работе.» / «Ищу контакты.»\n\n"

            "Используй РЕАЛЬНЫЕ данные из базы знаний в письмах. Не пиши [ссылка на демо].\n"
            "⚠️ ГОЛОС ПИСЕМ: от СВОЕГО имени, с упоминанием проекта. Получатель должен понять кто и зачем пишет.\n"
            "Делегируй коллеге если: у него есть нужный доступ/интеграция ИЛИ его специализация подходит лучше.\n"
            + (f"Формат: {_delegate_example}\n" if _delegate_example else "Формат: DELEGATE[Имя]: задача с конкретными данными.\n") +
            "Если инструмент вернул ошибку — напиши что именно пробовал и почему не получилось.\n\n"
            f"{_persona}"
        )
    else:
        system_prompt = (
            f"Ты — {agent['name']}, агент в команде ASI Biont. Сейчас: {_now_str}.\n"
            f"Пиши ТОЛЬКО от имени {agent['name']}. НЕ представляйся другим именем. "
            f"НЕ пиши от имени ASI, ASI Biont, или другого агента.\n\n"

        "КАК ТЫ ДУМАЕШЬ:\n"
        "Перед каждым ответом — быстрый анализ:\n"
        "— НАМЕРЕНИЕ: что человек РЕАЛЬНО хочет получить? Не цепляйся за буквальные слова — пойми что он будет ДЕЛАТЬ с твоим ответом.\n"
        "— ПОТРЕБНОСТЬ: что стоит ЗА запросом? К какому результату хочет прийти?\n"
        "— КОНТЕКСТ: кто этот человек (профиль!), что происходит, какие задачи и цели\n"
        "— ГЛУБИНА: что стоит за словами? Ищи настоящий смысл.\n"
        "— СЛЕПЫЕ ЗОНЫ: что человек НЕ видит? Перегруз, проседающие сферы, упущенные возможности\n"
        "— ДЕЙСТВИЕ: что я могу СДЕЛАТЬ прямо сейчас инструментами?\n"
        "— СТРАТЕГИЯ: как ЭТОТ человек с ЕГО ресурсами может достичь цели быстрее всего?\n"
        "— ВЫЗОВ: не соглашайся автоматически. Докопайся до корня проблемы — потом решай.\n\n"

        "СВЕРХИНТЕЛЛЕКТ:\n"
        "Движение: смотри на динамику, не снимок. Думай на 2 шага вперёд. Предупреждай о рисках до того как они стали проблемами.\n"
        "Рычаги: ищи точку минимум-усилий/максимум-результата. Соединяй то, что человек сам не видит.\n"
        "Инверсия: перед советом спроси себя «что гарантированно провалит эту цель?» Скажи прямо.\n"
        "Адаптация: если пользователь исправил тебя — извлеки принцип и применяй всегда.\n\n"

        "ФОРМАТ ОТВЕТА: сплошной текст как в мессенджере, абзацами. МИНИМУМ 200 символов.\n"
        "Ответ короче 200 символов = ОШИБКА (кроме да/нет на закрытый вопрос).\n"
        "ЗАПРЕЩЕНО: маркеры (•, -, *, 1.), CAPS-ЗАГОЛОВКИ, markdown (**жирный**, # заголовок), "
        "шаблоны типа ЦЕЛЕВАЯ АУДИТОРИЯ: или СТРАТЕГИЯ:.\n"
        "Вместо списков — перечисляй через запятую или в предложениях. Вместо заголовков — новый абзац.\n"
        "Объём по задаче: простой вопрос — 1-2 предложения. Анализ, отчёт, план — столько сколько нужно для полного ответа, но без воды.\n"
        "НЕ пиши 'Привет!', не здоровайся. Пиши как опытный специалист — живо, с позицией, без формальностей.\n\n"

        "ИНСТРУМЕНТЫ: у тебя есть доступ ко всем инструментам платформы: задачи, поиск, "
        "исследования, email, публикации, делегирование и многое другое. "
        "Не ограничивай себя текстом — ДЕЙСТВУЙ.\n"
        "Если задача требует цепочки действий — пройди ВСЕ шаги до конкретного результата, не останавливайся на планировании.\n"
        "НЕ пиши планы без действий — каждый пункт плана ВЫПОЛНЯЙ инструментами (исследуй, отправь email, делегируй). "
        "Ответ-план без вызовов инструментов — ОШИБКА.\n"
        "❌ add_task — создавай задачи через add_task ТОЛЬКО если пользователь явно попросил создать задачу или напоминание. "
        "Нашёл что-то интересное — СООБЩИ в тексте, предложи создать задачу, но НЕ создавай молча.\n"
        "ВАЖНО: делай РОВНО то, что поручено. В диалоге — простой вопрос = простой ответ. "
        "Работаешь по запросу пользователя, а не автономно.\n"
        "КАЧЕСТВО: каждый ответ содержит КОНКРЕТНЫЙ результат — текст поста, список контактов, "
        "анализ данных, исследование. Ответ ‘задачу выполнил’ без деталей = ПРОВАЛ.\n\n"

        "ДЕЛЕГИРОВАНИЕ КОЛЛЕГАМ: делегируй коллеге ТОЛЬКО если у него есть python_code или API-ключи "
        "для конкретного внешнего сервиса, к которому у тебя нет доступа. "
        "Если можешь выполнить задачу доступными тебе инструментами — делай сам, не делегируй.\n\n"

        "САМОАНАЛИЗ ИНТЕГРАЦИЙ:\n"
        "Смотри в раздел «ТВОИ ИНТЕГРАЦИИ» — там перечислено что у тебя РЕАЛЬНО подключено. "
        "Если что-то перечислено — ты УМЕЕШЬ это делать, используй run_agent_action. "
        "Если пользователь просит интеграцию, которой НЕТ в «Твои интеграции» — "
        "скажи конкретно: «Для этого нужна интеграция с X. Добавь ключи в настройках агента.»\n\n"

        "EMAIL-АДРЕСА:\n"
        "Копируй email ПОСИМВОЛЬНО из входных данных (IMAP, From, To, заголовки писем). "
        "Если видишь email в данных скрипта или в письме — используй ТОЧНО его, без изменений. "
        "Генерировать email из имени человека = ОШИБКА.\n\n"

        + (f"ТВОИ ИНТЕГРАЦИИ (активированы и готовы к использованию):\n{_intg_line.strip()}\n\n" if _intg_line.strip() else "")
        + (_intg_action_hint.strip() + "\n\n" if _intg_action_hint.strip() else "")
        + f"ТВОЯ РОЛЬ:\n{_persona}"
    )
    # Гендерная инструкция — чтобы агент использовал правильный род
    if _is_fem:
        system_prompt += (
            "\n\nВАЖНО: Ты ЖЕНЩИНА. Используй женский род во всех формах: "
            "сделала, нашла, подготовила, согласна, готова, проанализировала. "
            "НИКОГДА не пиши 'сделал', 'нашёл', 'согласен', 'готов' и т.п."
        )
    if dialog_context:
        system_prompt += (
            f"\n\n[КОНТЕКСТ — профиль пользователя, его email-контакты, цели, история диалога. "
            f"Используй чтобы понимать КТО пользователь, КОМУ он пишет, ЧТО ищет]:\n{dialog_context}"
        )

    # Авто-загрузка контекста пользователя ТОЛЬКО если не передан извне
    # (директор уже передаёт dialog_context → лишняя DB-сессия не нужна)
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
    elif dialog_context:
        logger.debug("[DIRECTOR-EXEC] context already passed (%d chars), skipping DB reload", len(dialog_context))

    # ── Шаг 0.5: Агент узнаёт о команде коллег ─────────────────────────────────
    if True:  # team info needed for delegation in autopilot too
      try:
        from models import Session as _Sess_team, UserAgent as _UA_team
        _s_team = _Sess_team()
        try:
            # Находим автора агента (владельца) для загрузки команды
            _author_id_for_team = agent.get('author_id')
            if not _author_id_for_team:
                from models import User as _U_team
                _u_team = _s_team.query(_U_team).filter_by(telegram_id=user_id).first()
                if _u_team:
                    _author_id_for_team = _u_team.id
            if _author_id_for_team:
                _teammates = (
                    _s_team.query(_UA_team)
                    .filter(
                        _UA_team.author_id == _author_id_for_team,
                        _UA_team.status.in_(['active', 'paused']),
                        _UA_team.id != agent.get('id'),
                    )
                    .order_by(_UA_team.id.asc())
                    .limit(10)
                    .all()
                )
                if _teammates:
                    _team_lines = []
                    for _tm in _teammates:
                        _role = _tm.job_title or _tm.specialization or ''
                        # Инferируем возможности коллеги из его конфигурации
                        _caps: list[str] = []
                        # 1. Явные разрешённые инструменты
                        try:
                            _tm_tools_raw = _tm.tools_allowed or '[]'
                            _tm_tools = json.loads(_tm_tools_raw) if isinstance(_tm_tools_raw, str) else (_tm_tools_raw or [])
                        except Exception:
                            _tm_tools = []
                        # Метки для инструментов
                        _TOOL_CAP = {
                            'send_email': 'пишет email', 'send_outreach_email': 'email-аутрич',
                            'reply_to_outreach_email': 'отвечает на email',
                            'start_email_campaign': 'email-кампании', 'negotiate_by_email': 'email-переговоры',
                            'list_email_contacts': 'читает контакты', 'save_email_contact': 'сохраняет контакты',
                            'find_relevant_contacts_for_task': 'ищет контакты',
                            'research_topic': 'исследования', 'web_search': 'веб-поиск',
                            'create_post': 'создаёт посты', 'publish_to_telegram': 'публикует в TG',
                            'generate_image': 'генерирует картинки',
                            'add_task': 'управляет задачами', 'delegate_task': 'делегирует',
                            'run_agent_action': 'внешние API',
                        }
                        if _tm_tools:
                            _caps += [_TOOL_CAP[t] for t in _tm_tools if t in _TOOL_CAP]
                        # 2. Инferируем из специализации/роли если инструментов нет
                        if not _caps:
                            _tm_spec = ((_tm.specialization or '') + ' ' + (_tm.job_title or '') + ' ' + (_tm.description or '')).lower()
                            if any(w in _tm_spec for w in ('email', 'почт', 'рассылк', 'outreach', 'smtp', 'imap')):
                                _caps.append('email')
                            if any(w in _tm_spec for w in ('контент', 'пост', 'smm', 'marketing', 'маркет', 'pr', 'пиар')):
                                _caps.append('контент/посты')
                            if any(w in _tm_spec for w in ('аналит', 'исслед', 'research', 'поиск')):
                                _caps.append('исследования')
                            if any(w in _tm_spec for w in ('dev', 'код', 'разраб', 'python', 'script')):
                                _caps.append('скрипты/интеграции')
                        # 3. Инferируем из api_keys (наличие ключей = доступ к сервису)
                        _tm_keys = (_tm.user_api_keys or '').lower()
                        if any(w in _tm_keys for w in ('gmail', 'imap', 'smtp', 'mail')):
                            if 'email' not in ' '.join(_caps):
                                _caps.append('email (ключи)')
                        if any(w in _tm_keys for w in ('openai', 'anthropic', 'deepseek')):
                            _caps.append('AI')
                        # 4. Наличие python_code = интеграции/скрипты
                        if (_tm.python_code or '').strip():
                            _pc_lower = _tm.python_code.lower()
                            if any(w in _pc_lower for w in ('imap', 'imaplib', 'email.mime', 'smtplib')):
                                if 'читает email' not in _caps:
                                    _caps.append('читает входящие email')
                            if any(w in _pc_lower for w in ('requests', 'aiohttp', 'httpx')):
                                if 'скрипты/интеграции' not in _caps:
                                    _caps.append('внешние интеграции')
                        # Формируем строку
                        _cap_str = ', '.join(_caps[:4]) if _caps else ''
                        _line = f"  • {_tm.name}"
                        if _role:
                            _line += f" — {_role}"
                        if _cap_str:
                            _line += f" [умеет: {_cap_str}]"
                        _team_lines.append(_line)
                    system_prompt += (
                        "\n\nКОМАНДА КОЛЛЕГ (только для справки — делегируй лишь если у коллеги есть уникальный доступ/интеграция которой у тебя нет):\n"
                        + "\n".join(_team_lines)
                    )
        finally:
            _s_team.close()
      except Exception as _te_team:
        logger.debug('[DIRECTOR-EXEC] team load for agent: %s', _te_team)

    # ── Шаг 1: Выполняем python_code (внешние данные) ─────────────────────────
    # Пропускаем для автопилота: экономит 35с + предотвращает hang от IMAP/RSS subprocess.
    # В автопилоте агент использует платформенные инструменты (check_emails, run_agent_action и т.д.)
    # напрямую через tool-calling — это быстрее и безопаснее чем subprocess в executor.
    script_context = ""
    if not _is_autopilot_task and (agent.get('python_code') or '').strip():
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
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)
                try:
                    _kwargs_sc = dict(
                        capture_output=True, text=True, timeout=API_TIMEOUT_SCRIPT, env=_exec_env,
                        encoding='utf-8', errors='replace',
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
                # Очищаем HTML-артефакты из IMAP/email вывода (mailto, <a>, entities)
                import re as _re_sc
                _sc_clean = _re_sc.sub(
                    r'<a[^>]*href=["\']mailto:([^"\'\s>]+)["\'][^>]*>[^<]*(?:</a>)?', r'\1', stdout2, flags=_re_sc.IGNORECASE | _re_sc.DOTALL)
                _sc_clean = _re_sc.sub(r'<([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})>', r'\1', _sc_clean)
                _sc_clean = _re_sc.sub(r'<[^>]+>', '', _sc_clean, flags=_re_sc.DOTALL)
                _sc_clean = _re_sc.sub(r'@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}["\']?\s*>\s*(?=[a-zA-Z0-9._%+-]+@)', '', _sc_clean)
                _sc_clean = _re_sc.sub(r'["\']?\s*/?\s*>(?=\S)', '', _sc_clean)
                _sc_clean = _re_sc.sub(r'&(?:nbsp|amp|lt|gt|quot|#\d+);?', ' ', _sc_clean)
                _sc_clean = _re_sc.sub(r'\n{3,}', '\n\n', _sc_clean)
                script_context = (
                    f"\n\n[Данные от скрипта/интеграции — перескажи СВОИМИ СЛОВАМИ в ответе, "
                    f"не копируй raw-текст дословно, сформулируй как живой человек]:\n{_sc_clean[:2000]}"
                )
                system_prompt += script_context
            elif _stderr2 and 'timeout' not in _stderr2:
                logger.debug("[DIRECTOR-EXEC] script stderr for %s: %s", agent.get('name'), _stderr2[:150])
                # Показываем ошибку авторизации агенту — чтобы он мог сообщить пользователю
                if 'AUTHENTICATIONFAILED' in _stderr2 or 'Invalid credentials' in _stderr2:
                    system_prompt += (
                        "\n\n[ОШИБКА ИНТЕГРАЦИИ: не удалось авторизоваться в сервисе. "
                        "Сообщи пользователю что нужно обновить пароль/ключ в настройках агента.]"
                    )
                elif 'error' in _stderr2.lower() or 'ошибка' in _stderr2.lower():
                    system_prompt += f"\n\n[Ошибка скрипта: {_stderr2[:200]}]"
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
        # Для автопилота: расширяем собственные tools агента core-набором (задачи, цели, прогресс)
        if _is_autopilot_task:
            _allowed_tools.update({
                'complete_task', 'edit_task',
                'update_goal_progress', 'update_goal', 'complete_goal',
                'research_topic', 'web_search', 'delegate_task',
            })
            # Для email-агентов: добавляем start_email_campaign + add_email_leads
            # чтобы агент мог самостоятельно создать кампанию перед отправкой писем
            _spec_ext = (
                (agent.get('specialization') or '') + ' ' +
                (agent.get('job_title') or '') + ' ' +
                (agent.get('description') or '')
            ).lower()
            _lbl_ext = ' '.join(h.lower() for h in _intg_hint)
            _tools_str_ext = (agent.get('tools_allowed') or '').lower()
            _has_email_tools = (
                any(w in _spec_ext for w in ('email', 'почт', 'imap', 'smtp', 'письм', 'рассылк', 'outreach', 'sales', 'crm')) or
                any(w in _lbl_ext for w in ('почт', 'mail', 'imap', 'smtp', 'gmail', 'resend', 'outreach', 'письм')) or
                any(w in _tools_str_ext for w in ('check_emails', 'send_outreach_email', 'send_email', 'start_email_campaign'))
            )
            if _has_email_tools:
                _allowed_tools.update({
                    'send_outreach_email', 'reply_to_outreach_email',
                    'start_email_campaign', 'add_email_leads',
                    'list_email_contacts', 'save_email_contact',
                    'find_relevant_contacts_for_task',
                })
        try:
            from .tools import get_available_tools as _gat2
            _all_names = {t['function']['name'] for t in _gat2()}
            _exclude_for_agent = _all_names - _allowed_tools
        except Exception as _te2:
            logger.debug('[DIRECTOR] tools exclude calc: %s', _te2)
    elif not _allowed_tools:
        if _is_autopilot_task:
            # Адаптивный автопилот: core tools + smart filter по специализации/интеграциям агента
            logger.info('[DIRECTOR] Autopilot task → adaptive toolset for %s', agent.get('name'))
            # Core: минимальный набор для любого автопилота (включая поиск — нужен всегда)
            _autopilot_tools = {
                'complete_task', 'edit_task',
                'update_goal_progress', 'update_goal', 'complete_goal',
                'delegate_task', 'run_agent_action',
                # Поиск/исследование — базово доступны всем, даже если есть спец.интеграция.
                # Агент с RSS/Github/Telegram всё равно дополняет данные через web/research.
                'research_topic', 'web_search', 'quick_topic_search',
                # Планирование — полезно для любого автопилота
                'schedule_background_task', 'set_reminder',
            }
            # Smart extend: добавляем инструменты по специализации и интеграциям агента.
            # Используем _intg_hint (лейблы из _parse_agent_integrations) — универсально
            # для любых интеграций, без захардкоженных имён сервисов.
            _spec = ((agent.get('specialization') or '') + ' ' + (agent.get('description') or '') + ' ' + (agent.get('job_title') or '')).lower()
            _lbl_ap = ' '.join(h.lower() for h in _intg_hint)  # все лейблы в одну строку
            # Email — агент имеет почтовую интеграцию ИЛИ специализируется на email
            if (any(w in _spec for w in ('email', 'почт', 'imap', 'smtp', 'письм', 'рассылк', 'outreach', 'crm', 'контакт', 'sales')) or
                    any(w in _lbl_ap for w in ('почт', 'mail', 'imap', 'smtp', 'gmail', 'resend', 'sendgrid', 'mailgun'))):
                _autopilot_tools.update({
                    'send_email', 'check_emails', 'send_outreach_email', 'reply_to_outreach_email',
                    'start_email_campaign', 'list_email_contacts', 'save_email_contact',
                    'find_relevant_contacts_for_task', 'add_email_leads',
                    # Follow-up цепочка — без них email-автопилот обрывается на первом письме
                    'negotiate_by_email', 'send_follow_up_email', 'set_contact_alert',
                })
            # Контент/маркетинг — по специализации или мессенджер-интеграции
            if (any(w in _spec for w in ('контент', 'marketing', 'маркет', 'публик', 'пост', 'smm', 'pr ', 'пиар', 'копирайт', 'редактор')) or
                    any(w in _lbl_ap for w in ('telegram', 'discord', 'slack', 'вконтакт'))):
                _autopilot_tools.update({
                    'create_post', 'publish_to_telegram', 'publish_to_discord',
                    'generate_image', 'start_content_campaign', 'manage_content_campaign',
                    'set_content_strategy', 'get_news_trends',
                    # Исследование тем для постов — контент-агент должен уметь искать
                    'research_topic', 'web_search',
                })
            # Аналитика/исследования — по специализации
            if any(w in _spec for w in ('аналит', 'исслед', 'research', 'монитор', 'тренд', 'data', 'данн')):
                _autopilot_tools.update({
                    'get_news_trends',
                    'research_topic', 'web_search', 'get_stock_price', 'save_note',
                    'create_post',  # публиковать аналитику
                    # find_and_message_relevant_users убран — аналитик исследует данные, а не ищет контакты
                })
            # Alpha Vantage / NewsAPI / Биржевые данные — по ключу интеграции (не только по специализации)
            if any(w in _lbl_ap for w in ('alpha vantage', 'биржевые', 'newsapi', 'новости')):
                _autopilot_tools.update({
                    'get_stock_price', 'get_news_trends', 'research_topic',
                    'create_post', 'publish_to_telegram', 'publish_to_discord', 'save_note',
                })
            # RSS/мониторинг — по лейблу интеграции ИЛИ специализации агента
            if (any(w in _lbl_ap for w in ('rss', 'лент', 'feed', 'новост')) or
                    any(w in _spec for w in ('rss', 'лент', 'feed'))):
                _autopilot_tools.update({
                    'get_news_trends',
                    # RSS-агент суммирует и публикует → нужны эти инструменты
                    'research_topic', 'web_search',
                    'create_post', 'publish_to_telegram', 'publish_to_discord', 'save_note',
                    # Контактные инструменты убраны: RSS-монитор читает/анализирует, не ищет людей
                })
            # Продажи/HR/нетворкинг
            if any(w in _spec for w in ('продаж', 'sales', 'hr', 'рекрут', 'клиент', 'лид', 'партнёр', 'партнер', 'нетворк', 'b2b')):
                _autopilot_tools.update({
                    'find_and_message_relevant_users', 'find_relevant_contacts_for_task',
                    'send_outreach_email', 'save_email_contact',
                    'start_delegation_campaign', 'manage_delegation_campaign',
                    # Follow-up важен для продаж/HR
                    'check_emails', 'negotiate_by_email', 'send_follow_up_email',
                })
            # GitHub/GitLab — поиск разработчиков → сохранение контактов → outreach
            # run_agent_action(search_users) уже в core, но без save/send цепочка бессмысленна
            if any(w in _lbl_ap for w in ('github', 'gitlab')):
                _autopilot_tools.update({
                    'save_email_contact', 'find_relevant_contacts_for_task',
                    'send_outreach_email', 'add_email_leads',
                    'find_and_message_relevant_users',
                    # Поиск репозиториев/тем для GitHub-агентов
                    'research_topic', 'web_search',
                })
            # CRM/маркетплейс/прочие интеграции — run_agent_action уже в core
            if any(w in _lbl_ap for w in ('crm', 'amocrm', 'битрикс', 'hubspot', 'ozon', 'wildberries', 'авито', 'shopify')):
                _autopilot_tools.update({'find_relevant_contacts_for_task', 'save_email_contact'})
            logger.info('[DIRECTOR] Autopilot adaptive toolset: %d tools for %s', len(_autopilot_tools), agent.get('name'))
            try:
                from .tools import get_available_tools as _gat_ap
                _all_names = {t['function']['name'] for t in _gat_ap()}
                _exclude_for_agent = _all_names - _autopilot_tools
            except Exception:
                _exclude_for_agent = {'delete_task'}
        else:
            # R7: Smart tool filtering — вывести toolset из специализации + API-ключей агента
            _spec = ((agent.get('specialization') or '') + ' ' + (agent.get('description') or '') + ' ' + (agent.get('job_title') or '')).lower()
            _lbl_ch = ' '.join(h.lower() for h in _intg_hint)  # лейблы интеграций
            _inferred_tools: set[str] = set()
            # Email — по специализации ИЛИ по лейблам интеграций (не по сырым ключам)
            if (any(w in _spec for w in ('email', 'почт', 'imap', 'smtp', 'письм', 'рассылк', 'outreach')) or
                    any(w in _lbl_ch for w in ('почт', 'mail', 'imap', 'smtp', 'gmail', 'resend', 'sendgrid', 'mailgun', 'sparkpost'))):

                _inferred_tools.update({'send_email', 'check_emails', 'list_email_contacts', 'save_email_contact',
                                        'start_email_campaign', 'negotiate_by_email',
                                        'send_outreach_email', 'reply_to_outreach_email',
                                        'send_follow_up_email', 'add_email_leads',
                                        'find_relevant_contacts_for_task'})
            # Контент/маркетинг/PR
            if any(w in _spec for w in ('контент', 'marketing', 'маркет', 'публик', 'пост', 'smm', 'telegram', 'pr ', 'pr-', 'пиар', 'копирайт', 'редактор')):
                _inferred_tools.update({'create_post', 'publish_to_telegram', 'publish_to_discord',
                                        'research_topic', 'web_search', 'generate_image',
                                        'set_content_strategy', 'start_content_campaign', 'manage_content_campaign',
                                        'find_relevant_contacts_for_task'})
            # Продажи/HR/поиск людей → контакты + сообщения + рассылка
            if any(w in _spec for w in ('продаж', 'sales', 'hr', 'рекрут', 'поиск', 'найти', 'клиент', 'лид', 'партнёр', 'партнер', 'нетворк', 'b2b', 'crm')):
                _inferred_tools.update({'find_relevant_contacts_for_task', 'find_and_message_relevant_users',
                                        'web_search', 'send_message_to_user', 'set_contact_alert',
                                        'send_email', 'send_outreach_email', 'save_email_contact',
                                        'start_delegation_campaign', 'manage_delegation_campaign'})
            # Проект-менеджмент / управление задачами
            if any(w in _spec for w in ('проект', 'project', 'менеджер', 'manager', 'управлен', 'планиров', 'координат', 'scrum', 'agile')):
                _inferred_tools.update({'delegate_task', 'get_delegation_progress',
                                        'start_delegation_campaign', 'manage_delegation_campaign',
                                        'create_goal', 'update_goal_progress'})
            # Аналитик/исследования
            if any(w in _spec for w in ('аналит', 'исслед', 'research', 'монитор', 'тренд', 'data', 'данн')):
                _inferred_tools.update({
                    'research_topic', 'web_search', 'quick_topic_search',
                    'get_news_trends', 'get_stock_price', 'save_note', 'create_post',
                })
            # Alpha Vantage / NewsAPI / Finance — по ключу интеграции
            if any(w in _lbl_ch for w in ('alpha vantage', 'биржевые', 'newsapi', 'новости')):
                _inferred_tools.update({
                    'get_stock_price', 'get_news_trends', 'research_topic', 'web_search',
                    'create_post', 'publish_to_telegram', 'save_note',
                })
            # RSS — по ключу интеграции ИЛИ специализации агента
            if (any(w in _lbl_ch for w in ('rss', 'лент', 'feed', 'новост')) or
                    any(w in _spec for w in ('rss', 'лент', 'feed'))):
                _inferred_tools.update({
                    'get_news_trends', 'research_topic', 'web_search',
                    'create_post', 'publish_to_telegram', 'publish_to_discord', 'save_note',
                    # Контактные инструменты убраны: RSS-монитор читает и публикует, не ищет людей
                })
            # Telegram/Discord интеграция — контент-инструменты по ключу
            if any(w in _lbl_ch for w in ('telegram', 'discord', 'slack')):
                _inferred_tools.update({
                    'create_post', 'publish_to_telegram', 'publish_to_discord',
                    'get_news_trends', 'research_topic', 'web_search',
                    'start_content_campaign', 'manage_content_campaign', 'set_content_strategy',
                })
            # GitHub/GitLab — поиск разработчиков → контакты → outreach
            if any(w in _lbl_ch for w in ('github', 'gitlab')):
                _inferred_tools.update({
                    'save_email_contact', 'find_relevant_contacts_for_task',
                    'send_outreach_email', 'add_email_leads',
                    'find_and_message_relevant_users', 'web_search', 'research_topic',
                })
            # Задачи всегда доступны
            _inferred_tools.update({'add_task', 'delegate_task', 'run_agent_action'})
            # Если smart filter нашёл только базовые (add_task, delegate_task) → не ограничиваем
            _base_only = _inferred_tools <= {'add_task', 'delegate_task'}
            if _inferred_tools and not _base_only:
                try:
                    from .tools import get_available_tools as _gat3
                    _all_names = {t['function']['name'] for t in _gat3()}
                    _exclude_for_agent = _all_names - _inferred_tools
                    logger.info('[DIRECTOR] Smart filter for %s: inferred %d tools from spec', agent.get('name'), len(_inferred_tools))
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)

    # ── Кросс-сессионный бан инструментов: агент использовал одно и то же 2+ раз за 24ч
    # → исключаем из видимых, чтобы AI искал новые подходы (а не повторял провальную стратегию)
    if _is_autopilot_task and agent.get('id'):
        try:
            from models import Session as _DBan, AgentActivityLog as _ALog_ban
            import re as _re_ban
            from datetime import datetime as _dt_ban, timezone as _tz_ban, timedelta as _td_ban
            _db_ban = _DBan()
            try:
                _recent_logs_ban = _db_ban.query(_ALog_ban).filter(
                    _ALog_ban.user_id == user_id,
                    _ALog_ban.ref_id == agent['id'],
                    _ALog_ban.created_at >= _dt_ban.now(_tz_ban.utc) - _td_ban(hours=24),
                ).order_by(_ALog_ban.created_at.desc()).limit(20).all()
                _ban_counts: dict = {}
                for _bl in _recent_logs_ban:
                    _tm_b = _re_ban.search(r'\[([^\]]+)\]', _bl.content or '')
                    if _tm_b:
                        for _t_b in _tm_b.group(1).split(','):
                            _t_b = _t_b.strip()
                            if _t_b:
                                _ban_counts[_t_b] = _ban_counts.get(_t_b, 0) + 1
                # Email/outreach инструменты законно вызываются много раз (каждый раз разный адресат)
                # → баним только после 5 сессий подряд, поисковые — после 3
                _EMAIL_OUTREACH = {
                    'send_outreach_email', 'send_email', 'negotiate_by_email',
                    'start_email_campaign', 'find_and_message_relevant_users',
                    'find_relevant_contacts_for_task', 'send_follow_up_email',
                    'reply_to_outreach_email',
                }
                _runtime_banned = {
                    t for t, n in _ban_counts.items()
                    if (n >= 5 if t in _EMAIL_OUTREACH else n >= 3)
                }
                # Не баним core-инструменты и базовые поисковые — всегда нужны
                _runtime_banned -= {
                    'update_goal_progress', 'add_task', 'complete_task',
                    'edit_task', 'delegate_task',
                    # Поисковые/базовые — каждый раз новый запрос, бан бессмысленен
                    'web_search', 'research_topic', 'quick_topic_search',
                    'check_emails', 'run_agent_action',
                    'get_news_trends', 'get_stock_price',
                }
                if _runtime_banned:
                    logger.info('[DIRECTOR] cross-session banned for %s: %s', agent.get('name'), _runtime_banned)
                    if _exclude_for_agent is not None:
                        _exclude_for_agent = _exclude_for_agent | _runtime_banned
                    else:
                        # Нет текущего exclude — создаём только из забаненных
                        try:
                            from .tools import get_available_tools as _gat_ban
                            _all_ban_names = {t['function']['name'] for t in _gat_ban()}
                            _exclude_for_agent = _runtime_banned & _all_ban_names
                        except Exception as _e:
                            logger.debug("suppressed: %s", _e)
            finally:
                _db_ban.close()
        except Exception as _ban_err:
            logger.debug('[DIRECTOR] cross-session ban load: %s', _ban_err)

    # ── Шаг 3: Tool-calling loop (макс 3 итерации) ────────────────────────────
    # Инжектируем список доступных инструментов в промпт агента
    try:
        from .tools import get_available_tools as _gat_aware
        _all_tools_info = _gat_aware()
        _TOOL_LABELS = {
            'add_task': 'создать задачу', 'complete_task': 'закрыть задачу',
            'edit_task': 'изменить задачу', 'delete_task': 'удалить задачу',
            'list_tasks': 'список задач', 'create_goal': 'создать цель',
            'list_goals': 'список целей', 'delegate_task': 'поручить агенту/человеку',
            'research_topic': 'исследование темы', 'web_search': 'веб-поиск',
            'send_email': 'отправить email', 'negotiate_by_email': 'переговоры по email',
            'send_outreach_email': 'холодное письмо', 'save_email_contact': 'сохранить контакт',
            'create_post': 'создать пост', 'publish_to_telegram': 'пост в TG',
            'publish_to_discord': 'пост в Discord', 'generate_image': 'генерация картинки',
            'start_content_campaign': 'автопостинг', 'find_relevant_contacts_for_task': 'поиск контактов',
            'find_and_message_relevant_users': 'найти и написать людям',
            'start_delegation_campaign': 'поиск исполнителей',
            'update_profile': 'обновить профиль', 'run_agent_action': 'внешнее действие',
            'update_goal_progress': 'обновить прогресс цели',
            'send_message_to_user': 'сообщение пользователю',
            'set_contact_alert': 'алерт на контакт',
        }
        if _exclude_for_agent:
            _my_tools = [t['function']['name'] for t in _all_tools_info
                         if t['function']['name'] not in _exclude_for_agent]
        else:
            _my_tools = [t['function']['name'] for t in _all_tools_info]
        if _my_tools:
            _labeled = [f"{n} ({_TOOL_LABELS[n]})" if n in _TOOL_LABELS else n for n in _my_tools[:15]]
            system_prompt = system_prompt.replace(
                "ИНСТРУМЕНТЫ: у тебя есть доступ ко всем инструментам платформы: задачи, поиск, "
                "исследования, email, публикации, делегирование и многое другое. ",
                "ТВОИ ИНСТРУМЕНТЫ: " + ", ".join(_labeled) + ". ",
            )
    except Exception as _e:
        logger.debug("suppressed: %s", _e)

    # ── Блок МЫШЛЕНИЯ: учим агента думать перед действием (по образцу ASI) ─────
    # Без этого блока агенты выбирают первый попавшийся инструмент по шаблону.
    # С ним — анализируют вариант, оценивают рычаг, избегают повторов.
    system_prompt += (
        "\n\n## МЫШЛЕНИЕ ПЕРЕД ДЕЙСТВИЕМ\n"
        "Перед каждым tool-вызовом — быстрый анализ (занимает 2 секунды, не текст):\n"
        "СУТЬ: что РЕАЛЬНО изменится когда цель будет достигнута? Какой измеримый результат?\n"
        "ВАРИАНТЫ: назови мысленно 3 разных подхода (примеры):\n"
        "  — прямой контакт (email, сообщение, outreach)\n"
        "  — через контент (пост, публикация, статья) \n"
        "  — через данные (исследование, поиск, аналитика)\n"
        "  — нестандартный (что я ещё НЕ пробовал для ЭТОЙ цели?)\n"
        "ИСТОРИЯ: что уже делал и какой результат было? Повторять провальное = ошибка.\n"
        "РЫЧАГ: какое ОДНО действие даст максимальный сдвиг при минимуме усилий прямо сейчас?\n"
        "СЛЕПЫЕ ЗОНЫ: что я упускаю? Может новый тип контакта, другая площадка, иной подход?\n\n"
        "ПРИНЦИПЫ:\n"
        "— 0% прогресса → значит нужен ДРУГОЙ подход, не тот что делал раньше.\n"
        "— Ищи комбинации: исследование → создание контента → рассылка → follow-up.\n"
        "— Думай как предприниматель: что принесёт РЕАЛЬНЫЙ результат для этой цели?\n"
        "— Одно точное действие > три шаблонных. Лучше нестандартно, чем привычно.\n"
        "— Нашёл что-то интересное при поиске → ИСПОЛЬЗУЙ это как основу для письма/поста.\n"
    )

    # ── Инъекция обученных предпочтений + эффективность инструментов ──
    try:
        _learner_ap = get_learner()
        _tool_eff = _learner_ap.get_tool_effectiveness_hint(user_id)
        if _tool_eff:
            system_prompt += _tool_eff + "\n"
        _user_pref = _learner_ap.get_user_preferences(user_id)
        if _user_pref:
            system_prompt += _user_pref + "\n"
    except Exception as _e:
        logger.debug("suppressed: %s", _e)

    # Для autopilot-задач: фокус на конкретное действие, не на анализ истории
    if _is_autopilot_task:
        system_prompt += (
            "\n\n⚡ АВТОПИЛОТ — ОБЯЗАТЕЛЬНОЕ ДЕЙСТВИЕ (НЕ ТЕКСТ):\n"
            "❗ Твой ПЕРВЫЙ ответ ДОЛЖЕН быть вызовом инструмента — НЕ текстом.\n"
            "❗ Чистый текст без вызова инструментов = ОШИБКА и провал задачи.\n"
            "1. В задаче написан ПЛАН ДЕЙСТВИЙ — вызови первый подходящий инструмент прямо сейчас.\n"
            "2. ЦЕПОЧКА за цикл: ИНСТРУМЕНТ → РЕЗУЛЬТАТ → update_goal_progress(goal_title='...', progress=N, note='...').\n"
            "   ⚠️ progress — АБСОЛЮТНОЕ значение % в (0-100), НЕ дельта. Считай сам по факту: \n"
            "   - Нашёл/отправил контакты: (кол-во_найденных / цель) * 100\n"
            "   - Получил реальный ответ 'да/буду пользоваться' = +8-10% к текущему\n"
            "   - Создал кампанию / запустил этап = +5% к текущему\n"
            "   Пример: цель 50 пользователей, нашёл 3 контакта, получил 1 ответ → progress=12, note='3 контакта, 1 ответ от Александр П.'\n"
            "   Пример: check_emails() вернул 2 новых ответа → progress=текущий+10, note='2 новых ответа'.\n"
            "   Пример: find_relevant_contacts_for_task → send_outreach_email или start_email_campaign.\n"
            "3. НЕ ПИШИ О ТОМ ЧТО СОБИРАЕШЬСЯ СДЕЛАТЬ — просто вызови инструмент.\n"
            "4. Если один инструмент заблокирован/недоступен — сразу вызови другой из твоего списка.\n"
            "5. Нашёл задачу для коллеги → DELEGATE[Имя]: задача с конкретными данными.\n"
            "6. Если check_emails вернул 'нет новых писем от незнакомых контактов' → НЕ повторяй check_emails! "
            "Вместо этого вызови start_email_campaign или find_relevant_contacts_for_task."
            + (_intg_action_hint or '')
        )

    # Создаём изолированный инстанс — не делим состояние с глобальным ASI
    # (execution_history, счётчики, лимиты у каждого агента свои)
    _agent_inst = HybridAutonomousAgent()
    # Регистрируем текущего агента в _active_agent_data, чтобы _run_external_action
    # нашёл python_code этого агента при вызове run_agent_action из tool-loop.
    if agent.get('python_code', '').strip():
        _agent_inst._active_agent_data[user_id] = agent
    _messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]
    # Если tools_allowed пустой → агент универсальный: работает со всеми инструментами платформы
    # (аналогично поведению при прямом @mention агента в process_request)
    # _exclude_for_agent уже None при пустом _allowed_Tools → exclusions нет → все tools доступны
    _use_tools = True

    # Очередь субделегирований: агент может попросить другого агента через паттерн DELEGATE[имя]: задача
    _pending_subdelegations: list[dict] = []
    _early_text: str | None = None  # установлен если агент ответил текстом без tool calls

    _TOOL_TIMEOUT = 55  # дефолтный таймаут
    # Адаптивные таймауты: тяжёлые инструменты получают больше времени, лёгкие — меньше
    _TOOL_TIMEOUTS: dict[str, int] = {
        'research_topic': 60, 'web_search': 30, 'get_news_trends': 30,
        'negotiate_by_email': 50, 'run_agent_action': 50, 'generate_image': 50,
        'schedule_background_task': 45,
        'add_task': 15, 'complete_task': 15, 'edit_task': 15, 'delete_task': 15,
        'list_tasks': 15, 'list_goals': 15, 'create_goal': 15, 'update_goal_progress': 15,
        'save_note': 10, 'update_profile': 10, 'send_message_to_user': 15, 'send_email': 20,
    }

    _tool_call_count = 0
    _tools_used: list[str] = []  # трекинг вызванных инструментов
    _total_ap_tokens = 0  # суммарный расход DeepSeek-токенов за все AI-вызовы в этом цикле
    # Adaptive dispatch: action chain per cycle, round-robin чередует агентов
    # autopilot: search → save → send → progress (3 итерации)
    # обычный: action + summary (3 итерации)
    _max_iters = 5 if _is_autopilot_task else 4  # autopilot: search + save + send + update + summary; regular: до 4 итераций для сложных задач
    # GitHub-агент: определяем заранее для форсирования цепочки
    _agent_has_github_local = 'github' in (agent.get('user_api_keys', '') or '').lower()

    # ── История GitHub-запросов за 7 дней (предотвращает повтор query между сессиями) ──
    if _is_autopilot_task and _agent_has_github_local:
        try:
            from models import Session as _DBgh, AgentActivityLog as _ALog_gh
            from datetime import datetime as _dt_gh, timezone as _tz_gh, timedelta as _td_gh
            import re as _re_gh
            _db_gh = _DBgh()
            try:
                _gh_logs = _db_gh.query(_ALog_gh).filter(
                    _ALog_gh.user_id == user_id,
                    _ALog_gh.activity_type == 'run_agent_action',
                    _ALog_gh.title.like('% · search_users [q=%'),
                    _ALog_gh.created_at >= _dt_gh.now(_tz_gh.utc) - _td_gh(days=7),
                ).order_by(_ALog_gh.created_at.desc()).limit(20).all()
                _used_qp: list[str] = []
                for _gl in _gh_logs:
                    _m_qp = _re_gh.search(r'\[q=(.+?)\s+p=(\d+)\]', _gl.title or '')
                    if _m_qp:
                        _entry = f"query='{_m_qp.group(1).strip()}' page={_m_qp.group(2)}"
                        if _entry not in _used_qp:
                            _used_qp.append(_entry)
                if _used_qp:
                    _qp_str = '\n  '.join(_used_qp[:10])
                    system_prompt += (
                        f"\n\n📋 ИСТОРИЯ GitHub-поисков за 7 дней (НЕ ПОВТОРЯЙ — уже сделано):\n"
                        f"  {_qp_str}\n"
                        "⚡ Для следующего цикла используй ДРУГУЮ комбинацию. Примеры новых вариантов:\n"
                        "  'language:javascript repos:>5 location:Russia', 'language:java automation repos:>3',\n"
                        "  'language:go testing followers:>2', 'location:Kazakhstan language:python repos:>2',\n"
                        "  'language:kotlin android repos:>5', 'language:typescript repos:>10 followers:>3',\n"
                        "  'language:ruby repos:>10', 'language:php repos:>15 location:Russia'\n"
                        "Или используй следующий page= для уже запускавшихся query (page+1).\n"
                    )
            finally:
                _db_gh.close()
        except Exception as _gh_hist_err:
            logger.debug('[DIRECTOR] gh query history: %s', _gh_hist_err)

    for _iter in range(_max_iters):
        # Адаптивные лимиты: github нужно больше (search+save×N+send×N), autopilot — средне, обычные — базово
        _max_tool_calls = 12 if (_is_autopilot_task and _agent_has_github_local) else (8 if _is_autopilot_task else 5)
        _use_tools_now = _use_tools and _tool_call_count < _max_tool_calls
        # required только на первом вызове — гарантирует реальное действие
        _tc_mode = "auto"
        if _use_tools_now:
            if _is_autopilot_task and _tool_call_count == 0:
                _tc_mode = "required"
            else:
                _tc_mode = "auto"
        else:
            _tc_mode = None
        # Anti-repeat: на итерациях > 0 сообщаем модели что уже использовалось
        if _is_autopilot_task and _iter > 0 and _tools_used:
            _used_str = ', '.join(_tools_used[-3:])
            _last_tool_local = _tools_used[-1] if _tools_used else ''
            # GitHub-агент нашёл людей: ФОРСИРУЕМ save + send
            _was_github_search = (
                _agent_has_github_local
                and _last_tool_local == 'run_agent_action'
            )
            _was_save_contact = _last_tool_local == 'save_email_contact'
            # Проверяем: был ли send_outreach_email уже после save
            _was_send = 'send_outreach_email' in _tools_used
            _was_save = 'save_email_contact' in _tools_used
            # Считаем сколько раз уже отправляли письмо — чтобы не прерывать цепочку после первого
            _send_count = _tools_used.count('send_outreach_email')
            # GitHub-агент: даём отправить минимум 3 письма перед тем как требовать update_goal_progress
            _min_sends_before_update = 3 if _agent_has_github_local else 1
            if _was_send and _send_count >= _min_sends_before_update and not ('update_goal_progress' in _tools_used):
                _messages.append({"role": "user", "content": (
                    f"Письма отправлены ({_send_count} шт., использовал: {_used_str}). "
                    "ФИНАЛЬНЫЙ ШАГ — update_goal_progress:\n"
                    "Обнови прогресс цели: сколько новых контактов/писем получилось. "
                    "Вызови update_goal_progress(goal_title='...', notes='краткий итог')."
                )})
            elif _was_send and _send_count < _min_sends_before_update and not ('update_goal_progress' in _tools_used):
                _messages.append({"role": "user", "content": (
                    f"Письмо отправлено ({_send_count}/{_min_sends_before_update}, использовал: {_used_str}). "
                    "ПРОДОЛЖАЙ — есть ещё найденные контакты:\n"
                    "Если есть ещё пользователи из поиска с email — отправь следующее письмо (send_outreach_email).\n"
                    "Если все контакты уже обработаны — вызови update_goal_progress."
                )})
            elif _was_save_contact:
                # Последний инструмент = save_email_contact → нужен send
                _messages.append({"role": "user", "content": (
                    f"Контакт(ы) сохранены (использовал: {_used_str}). "
                    "🚨 ОБЯЗАТЕЛЬНЫЙ СЛЕДУЮЩИЙ ШАГ — send_outreach_email:\n"
                    "Отправь персональное письмо каждому сохранённому контакту ПРЯМО СЕЙЧАС. "
                    "save_email_contact без последующего send_outreach_email = незавершённая цепочка.\n"
                    "Вызови send_outreach_email немедленно — НЕ завершай сессию без отправки письма!"
                )})
            elif _was_github_search and not _was_save:
                _messages.append({"role": "user", "content": (
                    f"GitHub-поиск выполнен (использовал: {_used_str}). "
                    "🚨 ОБЯЗАТЕЛЬНАЯ ЦЕПОЧКА — ВЫПОЛНИ ПРЯМО СЕЙЧАС:\n"
                    "1. save_email_contact — для КАЖДОГО найденного у кого есть email\n"
                    "   Параметры: name='Имя', email='адрес@домен', source='GitHub'\n"
                    "2. После save → send_outreach_email каждому из сохранённых\n"
                    "⚠️ Если у найденных НЕТ email на GitHub — напиши БЛОКЕР: нет email у контактов. "
                    "Это нормально — попробуй другой query или find_relevant_contacts_for_task."
                )})
            else:
                # GitHub-агент: если последнее действие было отправка и все контакты ранее уже contacted
                # → предлагаем искать следующую страницу
                _github_all_sent = (
                    _agent_has_github_local
                    and _was_send
                    and _was_github_search
                )
                if _github_all_sent:
                    _messages.append({"role": "user", "content": (
                        f"Использовал: {_used_str}. "
                        "Если send_outreach_email вернул 'уже отправлено' для всех — значит эта страница уже обработана.\n"
                        "🔄 СЛЕДУЮЩАЯ СТРАНИЦА: повтори search_users с page=2 (или page=3, если page=2 тоже использовался).\n"
                        "Пример: run_agent_action(action='search_users', query='прежний_query', page=2)\n"
                        "Так находишь новых людей, ещё не получавших письмо."
                    )})
                else:
                    _messages.append({"role": "user", "content": (
                        f"Уже использовал: {_used_str}. "
                        f"Выбери следующий логичный инструмент — не повторяй предыдущий без новых данных. "
                        f"Если нашёл контакты/email — ОБЯЗАТЕЛЬНО вызови send_outreach_email или start_email_campaign."
                )})
        # Adaptive tokens: tool-calling iterations only need short JSON output (400),
        # text-only final summary iterations need full response space (1200)
        _iter_max_tokens = 400 if _use_tools_now else 1200
        try:
            _resp = await asyncio.wait_for(
                _agent_inst.call_ai(
                    _messages,
                    use_tools=_use_tools_now,
                    tool_choice=_tc_mode,
                    exclude_tools=_exclude_for_agent if _use_tools_now else None,
                    max_tokens=_iter_max_tokens,
                    api_timeout=API_TIMEOUT_LONG,
                ),
                timeout=API_TIMEOUT_LONG + 5,
            )
        except (asyncio.TimeoutError, Exception) as _ai_err:
            logger.warning("[DIRECTOR-EXEC] agent %s call_ai error: %s", agent.get('name'), _ai_err)
            break
        if _resp:
            _u_ap = _resp.get('usage') or {}
            _total_ap_tokens += _u_ap.get('prompt_tokens', 0) + _u_ap.get('completion_tokens', 0)
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

            # ── Autopilot retry: save_email_contact без send_outreach_email на любой итерации ──
            # Агент сохранил контакт и написал текст вместо письма — принудительный retry
            # ВАЖНО: используем _was_save_contact (последний инструмент = save), не _was_save (общий флаг).
            # Сценарий-баг: save(A)→send(A)→save(B)→текст: _was_save=True, _was_send=True → retry не срабатывал.
            # Правильно: проверяем ПОСЛЕДНИЙ инструмент — если save без следующего send, форсируем send.
            if (_is_autopilot_task and _iter > 0 and not _tool_calls
                    and _was_save_contact and not (_last_tool_local == 'send_outreach_email')):
                logger.info(
                    "[DIRECTOR-EXEC] autopilot save-without-send retry for %s",
                    agent.get('name'),
                )
                _messages.append({"role": "assistant", "content": _content or ""})
                _messages.append({"role": "user", "content": (
                    "СТОП. Ты сохранил контакт(ы) через save_email_contact, но так и не отправил письмо. "
                    "Это нарушение цепочки.\n"
                    "ОБЯЗАТЕЛЬНО вызови прямо сейчас: send_outreach_email\n"
                    "Используй имя и email контакта которого только что сохранил. "
                    "НЕ пиши текст — только вызов инструмента send_outreach_email!"
                )})
                try:
                    _sws_resp = await asyncio.wait_for(
                        _agent_inst.call_ai(
                            _messages,
                            use_tools=True,
                            tool_choice="required",
                            exclude_tools=_exclude_for_agent,
                            max_tokens=300,
                            api_timeout=API_TIMEOUT_LONG,
                        ),
                        timeout=API_TIMEOUT_LONG + 5,
                    )
                    if _sws_resp and _sws_resp.get('choices'):
                        _sws_msg = _sws_resp['choices'][0]['message']
                        _sws_tools = _sws_msg.get('tool_calls') or []
                        if _sws_tools:
                            logger.info("[DIRECTOR-EXEC] save-without-send retry succeeded: %s", len(_sws_tools))
                            _messages.append(_sws_msg)
                            for _swstc in _sws_tools[:2]:
                                _sws_tname = _swstc.get('function', {}).get('name', '')
                                try:
                                    _sws_targs = json.loads(_swstc.get('function', {}).get('arguments', '{}'))
                                except Exception:
                                    _sws_targs = {}
                                _tools_used.append(_sws_tname)
                                try:
                                    _sws_tres = await asyncio.wait_for(
                                        _agent_inst.execute_actions(
                                            [{"tool": _sws_tname, "params": _sws_targs,
                                              "reason": f"{agent['name']}: {_sws_tname}"}],
                                            user_id, session=None, user_message=task,
                                        ),
                                        timeout=_TOOL_TIMEOUTS.get(_sws_tname, _TOOL_TIMEOUT),
                                    )
                                    _sws_r0 = _sws_tres[0] if _sws_tres else {"success": False}
                                    _sws_result = json.dumps(
                                        _sws_r0.get('result', {}) if _sws_r0.get('success')
                                        else {"error": str(_sws_r0.get('error', ''))},
                                        ensure_ascii=False, default=str
                                    )[:800]
                                except Exception as _sws_err:
                                    _sws_result = json.dumps({"error": str(_sws_err)[:200]}, ensure_ascii=False)
                                _messages.append({"role": "tool", "tool_call_id": _swstc['id'], "content": _sws_result})
                                _tool_call_count += 1
                except Exception as _sws_ex:
                    logger.warning("[DIRECTOR-EXEC] save-without-send retry error: %s", _sws_ex)

            # ── Autopilot retry: агент ответил текстом на первой итерации —
            # Делаем короткий повторный запрос с прямым указанием инструмента ──
            if _is_autopilot_task and _iter == 0 and not _tool_calls and not _tools_used:
                # Определяем какой инструмент нужно вызвать первым
                _first_tool = None
                _my_tools_safe = locals().get('_my_tools', [])
                if _my_tools_safe:
                    _priority_order = [
                        'web_search', 'research_topic', 'run_agent_action',
                        'find_relevant_contacts_for_task', 'check_emails',
                        'send_outreach_email', 'start_email_campaign',
                    ]
                    for _pt in _priority_order:
                        if _pt in _my_tools_safe:
                            _first_tool = _pt
                            break
                    if not _first_tool:
                        _first_tool = _my_tools_safe[0] if _my_tools_safe else None

                if _first_tool:
                    logger.info(
                        "[DIRECTOR-EXEC] autopilot text-without-tools retry for %s → suggest %s",
                        agent.get('name'), _first_tool,
                    )
                    _messages.append({"role": "assistant", "content": _content or ""})
                    _messages.append({"role": "user", "content": (
                        f"СТОП. Ты написал текст без вызова инструмента — это ошибка. "
                        f"Вызови инструмент прямо сейчас. Оптимальный вариант для этой задачи: {_first_tool}. "
                        f"Другие доступные: {', '.join([t for t in _my_tools_safe[:6] if t != _first_tool])}. "
                        f"Не пиши текст — только вызов инструмента."
                    )})
                    try:
                        _retry_resp = await asyncio.wait_for(
                            _agent_inst.call_ai(
                                _messages,
                                use_tools=True,
                                tool_choice="required",
                                exclude_tools=_exclude_for_agent,
                                max_tokens=300,
                                api_timeout=API_TIMEOUT_LONG,
                            ),
                            timeout=API_TIMEOUT_LONG + 5,
                        )
                        if _retry_resp and _retry_resp.get('choices'):
                            _retry_msg = _retry_resp['choices'][0]['message']
                            _retry_tools = _retry_msg.get('tool_calls') or []
                            if _retry_tools:
                                logger.info(
                                    "[DIRECTOR-EXEC] autopilot retry succeeded: %s tools called",
                                    len(_retry_tools),
                                )
                                # Заменяем последние сообщения и продолжаем с инструментами
                                _messages.append(_retry_msg)
                                _tc_limit = 3
                                for _tc in _retry_tools[:_tc_limit]:
                                    _tname = _tc.get('function', {}).get('name', '')
                                    try:
                                        _targs = json.loads(_tc.get('function', {}).get('arguments', '{}'))
                                    except Exception:
                                        _targs = {}
                                    _tools_used.append(_tname)
                                    if _tname == 'add_task' and agent.get('id'):
                                        _targs['created_by_agent_id'] = agent['id']
                                    try:
                                        _tres = await asyncio.wait_for(
                                            _agent_inst.execute_actions(
                                                [{"tool": _tname, "params": _targs, "reason": f"{agent['name']}: {_tname}"}],
                                                user_id, session=None, user_message=task,
                                            ),
                                            timeout=_TOOL_TIMEOUTS.get(_tname, _TOOL_TIMEOUT),
                                        )
                                        _r0 = _tres[0] if _tres else {"success": False}
                                        if _r0.get('success'):
                                            _tc_result = json.dumps(_r0['result'], ensure_ascii=False, default=str)[:1500]
                                        else:
                                            _tc_result = json.dumps({"error": str(_r0.get('error', ''))}, ensure_ascii=False)
                                    except asyncio.TimeoutError:
                                        _tc_result = json.dumps({"error": "tool timeout"}, ensure_ascii=False)
                                    except Exception as _te:
                                        _tc_result = json.dumps({"error": str(_te)[:200]}, ensure_ascii=False)
                                    _messages.append({"role": "tool", "tool_call_id": _tc['id'], "content": _tc_result})
                                _tool_call_count += 1
                                # Continue to next iteration for summary
                                continue
                    except Exception as _retry_err:
                        logger.debug("[DIRECTOR-EXEC] autopilot retry failed: %s", _retry_err)

            # Сохраняем результат и выходим из цикла — субделегирования обработаются ниже
            _early_text = _content  # use as-is (empty → empty_result, not noise_filtered)
            break

        # Агент вызвал инструменты — выполняем
        # Autopilot: до 3 за итерацию (search + action + progress)
        # Regular: до 2
        _tc_limit = 3 if _is_autopilot_task else 2
        _messages.append(_msg)
        for _tc in _tool_calls[:_tc_limit]:
            _tname = _tc.get('function', {}).get('name', '')
            try:
                _targs = json.loads(_tc.get('function', {}).get('arguments', '{}'))
            except Exception:
                _targs = {}

            _tools_used.append(_tname)
            # ── Специальный инструмент: агент пытается вызвать несуществующий delegate_to_agent ──
            if _tname == 'delegate_to_agent':
                # Перенаправляем на реальный delegate_task
                _tname = 'delegate_task'
                if 'agent_name' in _targs and 'delegated_to_username' not in _targs:
                    _targs['delegated_to_username'] = _targs.pop('agent_name')
                if 'task' in _targs and 'title' not in _targs:
                    _targs['title'] = _targs.pop('task')
            # ── Обычные инструменты ───────────────────────────────────────────────────────────
            # Проверяем доступность инструмента
            elif _allowed_tools and _tname not in _allowed_tools:
                _tc_result = json.dumps({"error": f"tool {_tname} not in tools_allowed"}, ensure_ascii=False)
            else:
                # ── GUARD: block update_goal_progress if only research tools were used ──
                # Прогресс можно обновлять только после реального исходящего действия
                _RESEARCH_ONLY_TOOLS = {
                    'web_search', 'research_topic', 'run_agent_action',
                    'get_news_trends', 'quick_topic_search',
                    'find_relevant_contacts_for_task', 'list_tasks', 'list_goals',
                    'list_email_contacts',
                }
                _OUTGOING_ACTION_TOOLS = {
                    'send_outreach_email', 'start_email_campaign', 'check_emails',
                    'reply_to_outreach_email', 'send_follow_up_email',
                    'negotiate_by_email', 'save_email_contact',
                    'publish_to_telegram', 'publish_to_discord', 'create_post',
                    'send_email', 'add_email_leads',
                }
                if _tname == 'update_goal_progress' and _is_autopilot_task:
                    _prior_tools_set = set(_tools_used[:-1])  # exclude current
                    _had_outgoing = bool(_prior_tools_set & _OUTGOING_ACTION_TOOLS)
                    _only_research = _prior_tools_set and _prior_tools_set.issubset(_RESEARCH_ONLY_TOOLS)
                    # Разрешаем update_goal_progress БЕЗ действий если агент НЕ повышает числовой прогресс
                    # (т.е. это фиксация итога сессии-поиска, а не накрутка метрики)
                    _ugp_progress = _targs.get('progress')
                    _ugp_metric = _targs.get('metric_current')
                    _is_progress_increase = (
                        (_ugp_progress is not None and float(_ugp_progress) > 0)
                        or (_ugp_metric is not None and float(_ugp_metric) > 0)
                    )
                    if _only_research and not _had_outgoing and _is_progress_increase:
                        _tc_result = json.dumps({
                            "error": (
                                "⛔ Обновление прогресса заблокировано. "
                                "В этом цикле были только исследовательские действия "
                                f"({', '.join(sorted(_prior_tools_set)[:3])}), "
                                "но числовой прогресс/метрика обновляются ТОЛЬКО после реального действия: "
                                "отправка письма, публикация, сохранение контакта. "
                                "Если хочешь зафиксировать итог без прогресса — передай progress=None и только note."
                            )
                        }, ensure_ascii=False)
                        _messages.append({"role": "tool", "tool_call_id": _tc['id'], "content": _tc_result})
                        _tool_call_count += 1
                        continue

                # Задачи создаваемые агентом помечаются source='agent'
                if _tname == 'add_task' and agent.get('id'):
                    _targs['created_by_agent_id'] = agent['id']
                try:
                    _tres = await asyncio.wait_for(
                        _agent_inst.execute_actions(
                            [{"tool": _tname, "params": _targs, "reason": f"{agent['name']}: {_tname}"}],
                            user_id, session=None, user_message=task,
                        ),
                        timeout=_TOOL_TIMEOUTS.get(_tname, _TOOL_TIMEOUT),
                    )
                    _r0 = _tres[0] if _tres else {"success": False}
                    if _r0.get('success'):
                        _tc_result = json.dumps(_r0['result'], ensure_ascii=False, default=str)
                        _tc_result = _tc_result[:1500]
                        try: get_learner().record_tool_result(user_id, _tname, True)
                        except Exception: pass
                    else:
                        _tc_result = json.dumps({"error": str(_r0.get('error', ''))}, ensure_ascii=False)
                        try: get_learner().record_tool_result(user_id, _tname, False)
                        except Exception: pass
                except asyncio.TimeoutError:
                    _tc_result = json.dumps({"error": "tool timeout"}, ensure_ascii=False)
                    logger.warning("[DIRECTOR-EXEC] tool %s timeout for %s", _tname, agent['name'])
                    try: get_learner().record_tool_result(user_id, _tname, False)
                    except Exception: pass
                except Exception as _te:
                    _tc_result = json.dumps({"error": str(_te)[:200]}, ensure_ascii=False)
                    logger.debug("[DIRECTOR-EXEC] tool %s error for %s: %s", _tname, agent['name'], _te)
                    try: get_learner().record_tool_result(user_id, _tname, False)
                    except Exception: pass

            _messages.append({"role": "tool", "tool_call_id": _tc['id'], "content": _tc_result})
        _tool_call_count += 1
        # Добавляем фиктивные результаты для пропущенных tool_calls (OpenAI/DeepSeek требует все)
        for _tc_skip in _tool_calls[_tc_limit:]:
            _messages.append({"role": "tool", "tool_call_id": _tc_skip['id'],
                              "content": '{"status":"skipped"}'})
        # Инструкция после tool-call: для автопилота — цепочка действий
        if _is_autopilot_task:
            _last_t_post = _tools_used[-1] if _tools_used else ''
            _is_search_tool = _last_t_post in (
                'run_agent_action', 'find_relevant_contacts_for_task',
                'web_search', 'quick_topic_search', 'research_topic',
            )
            if _iter < _max_iters - 1:
                # Не последняя итерация — продолжаем действовать
                if _last_t_post == 'save_email_contact':
                    # Только что сохранили контакт — ОБЯЗАТЕЛЬНО отправить письмо
                    _messages.append({"role": "user", "content": (
                        "Контакт сохранён. 🚨 НЕМЕДЛЕННО вызови send_outreach_email:\n"
                        "Напиши персональное письмо сохранённому пользователю — "
                        "используй его имя из GitHub, предложи протестировать ASI Biont. "
                        "НЕ пиши отчёт — вызови send_outreach_email прямо сейчас!"
                    )})
                elif _is_search_tool and _agent_has_github_local and _last_t_post == 'run_agent_action':
                    # GitHub-поиск: Проверяем все tool results на "уже отправлено"
                    _all_sent_blocked = False
                    _github_result_texts = [
                        m.get('content', '') for m in _messages
                        if m.get('role') == 'tool'
                    ]
                    _sent_blocked_count = sum(
                        1 for t in _github_result_texts
                        if 'уже отправлено' in t or 'already sent' in t.lower() or 'Cooldown' in t
                    )
                    _all_sent_blocked = _sent_blocked_count >= 3
                    if _all_sent_blocked:
                        _messages.append({"role": "user", "content": (
                            "Данные поиска получены, но все контакты уже получали письма (cooldown). "
                            "🔄 ИЩИ СЛЕДУЮЩУЮ СТРАНИЦУ:\n"
                            "Вызови run_agent_action с тем же query, но page=2 (или page=3 если page=2 тоже использовался). "
                            "Пример: run_agent_action(action='search_users', query='language:python followers:>5', page=2)\n"
                            "Новые пользователи → save_email_contact → send_outreach_email."
                        )})
                    else:
                        # GitHub-поиск: ЖЁСТКО требуем save + send
                        _messages.append({"role": "user", "content": (
                            "Данные поиска получены. 🚨 ОБЯЗАТЕЛЬНАЯ ЦЕПОЧКА GitHub:\n"
                            "1. save_email_contact — сохрани каждого найденного пользователя\n"
                            "2. send_outreach_email — напиши каждому из них письмо\n"
                            "Если поиск вернул 0 результатов — попробуй другой query в run_agent_action.\n"
                            "НЕ пиши отчёт — вызови инструмент!"
                        )})
                elif _is_search_tool:
                    # Обычный поиск: мягкое требование
                    _messages.append({"role": "user", "content": (
                        "Данные получены. ПРОДОЛЖАЙ ДЕЙСТВОВАТЬ — используй результаты:\n"
                        "— Нашёл email/контакт → save_email_contact + send_outreach_email\n"
                        "— Нашёл площадку/сообщество → создай задачу (add_task) с деталями\n"
                        "— Нашёл информацию для коллеги → DELEGATE[Имя]: задача с данными\n"
                        "НЕ останавливайся на 'нашёл и рассказал'. СДЕЛАЙ что-то с результатами!"
                    )})
                else:
                    # Не поиск (send, update и др.) — завершай цепочку
                    _messages.append({"role": "user", "content": (
                        "Действие выполнено. "
                        "Если есть ещё контакты для отправки — продолжай send_outreach_email. "
                        "Иначе вызови update_goal_progress."
                    )})
            else:
                # Последняя итерация: завершаем
                _messages.append({"role": "user", "content": (
                    "Финальный шаг. Вызови update_goal_progress, затем расскажи пользователю "
                    "ЧТО КОНКРЕТНО ты СДЕЛАЛ — 2–3 предложения, не более 350 символов. "
                    "Только главный факт: что нашёл, кому написал, что сохранил. Без деталей и списков."
                )})
        else:
            _messages.append({"role": "user", "content": (
                "Данные от инструмента получены. Дай ГОТОВЫЙ результат. "
                "Сплошной текст, без списков и CAPS-заголовков. "
                "Простая задача — кратко (1-3 предложения). Сложная — столько сколько нужно. "
                "НЕ пиши 'ищу данные' или 'уточняю'. Заверши мысль."
            )})
    # ── Autopilot: принудительный update_goal_progress если не был вызван ──
    # Фиксируем итог каждой сессии — агент мог завершить текстом или исчерпать итерации
    if (_is_autopilot_task
            and 'update_goal_progress' not in _tools_used
            and _tools_used):
        try:
            _ugp_note = (
                f"Сессия: использованы инструменты: {', '.join(_tools_used[-4:])}. "
                "Результат поиска зафиксирован."
            )
            _messages.append({"role": "user", "content": (
                "ОБЯЗАТЕЛЬНЫЙ ФИНАЛ: Вызови update_goal_progress чтобы зафиксировать итог этой сессии.\n"
                f"Используй: goal_title='название цели', note='{_ugp_note}'\n"
                "НЕ меняй числа прогресса если не было отправленных писем или подтверждённых контактов."
            )})
            _ugp_resp = await asyncio.wait_for(
                _agent_inst.call_ai(
                    _messages,
                    use_tools=True,
                    tool_choice="required",
                    exclude_tools=_exclude_for_agent,
                    max_tokens=200,
                    api_timeout=30,
                ),
                timeout=35,
            )
            if _ugp_resp and _ugp_resp.get('choices'):
                _ugp_msg = _ugp_resp['choices'][0]['message']
                _ugp_tcs = _ugp_msg.get('tool_calls') or []
                if _ugp_tcs:
                    _messages.append(_ugp_msg)
                    for _ugp_tc in _ugp_tcs[:1]:
                        _ugp_tname = _ugp_tc.get('function', {}).get('name', '')
                        try:
                            _ugp_targs = json.loads(_ugp_tc.get('function', {}).get('arguments', '{}'))
                        except Exception:
                            _ugp_targs = {}
                        if _ugp_tname == 'update_goal_progress':
                            _tools_used.append(_ugp_tname)
                            try:
                                _ugp_tres = await asyncio.wait_for(
                                    _agent_inst.execute_actions(
                                        [{"tool": _ugp_tname, "params": _ugp_targs,
                                          "reason": f"{agent.get('name')}: end-of-session update"}],
                                        user_id, session=None, user_message=task,
                                    ),
                                    timeout=15,
                                )
                                _ugp_r0 = _ugp_tres[0] if _ugp_tres else {"success": False}
                                _ugp_result = json.dumps(
                                    _ugp_r0.get('result', {}), ensure_ascii=False, default=str
                                )[:300]
                                _messages.append({"role": "tool", "tool_call_id": _ugp_tc['id'],
                                                  "content": _ugp_result})
                                logger.info(
                                    "[DIRECTOR-EXEC] end-of-session update_goal_progress OK for %s",
                                    agent.get('name'),
                                )
                            except Exception as _ugp_exec_err:
                                logger.debug("[DIRECTOR-EXEC] update_goal_progress exec: %s", _ugp_exec_err)
        except Exception as _ugp_err:
            logger.debug("[DIRECTOR-EXEC] end-of-session update_goal_progress: %s", _ugp_err)

    # Если агент ответил текстом без tool calls — пропускаем финальный AI-вызов
    if _early_text is not None:
        _final_text = _early_text
    else:
        # Исчерпали все итерации — берём последний контент из сообщений (без доп. LLM вызова)
        _final_text = ''
        for _m_back in reversed(_messages):
            if _m_back.get('role') == 'assistant' and _m_back.get('content'):
                _final_text = _m_back['content']
                break
        if not _final_text:
            _final_text = ''  # return empty on timeout/no-result → anchor_engine marks as empty_result, not noise_filtered
        # Парсим DELEGATE из финального ответа
        if _final_text:
            import re as _re_fin
            for _m in _re_fin.finditer(
                r'DELEGATE\[([^\]]+)\]:\s*(.+?)(?=DELEGATE\[|$)',
                _final_text, _re_fin.DOTALL | _re_fin.IGNORECASE,
            ):
                _aname = _m.group(1).strip()
                _atask = _m.group(2).strip()[:400]
                if _aname and _atask:
                    _pending_subdelegations.append({'agent_name': _aname, 'task': _atask})
            _final_text = _re_fin.sub(
                r'DELEGATE\[[^\]]+\]:[^\n]*\n?', '', _final_text,
            ).strip()  # if only DELEGATE patterns, return empty (subdelegations handled separately)
        # keep _final_text = '' if both branches left it empty (timeout/no-result)

    # ── Обрезка длинных ответов (без доп. LLM-вызова — экономит ~5с) ──
    # Если текст слишком короткий после tool-вызовов (для автопилота) — доп. вызов для итога
    # Включаем _done_fb: агент вызвал инструменты но не написал отчёт — форсируем summary
    if _is_autopilot_task and _tools_used and (len(_final_text) < 100 or _final_text == _done_fb):
        try:
            # Собираем результаты инструментов для контекста
            _tool_data_ctx = []
            for _m_ctx in _messages:
                if _m_ctx.get('role') == 'tool':
                    _td = (_m_ctx.get('content') or '')[:300]
                    if _td and _td != '{"status":"skipped"}':
                        _tool_data_ctx.append(_td)
            _tool_data_str = '\n'.join(_tool_data_ctx[-2:]) if _tool_data_ctx else ''
            _messages.append({"role": "assistant", "content": _final_text})
            _messages.append({"role": "user", "content": (
                "Ты написал слишком коротко. Пользователь получит это сообщение в чате — "
                "ему нужно понять что произошло. Вот данные из инструментов:\n"
                f"{_tool_data_str}\n\n"
                "Перескажи эти данные СВОИМИ СЛОВАМИ для пользователя: что нашлось, "
                "какие конкретные факты, имена, цифры, и что ты думаешь делать дальше. "
                "СТИЛЬ: сплошной текст, 2–4 предложения. "
                "ЗАПРЕЩЕНО: списки (• – 1.), нумерация, заголовки, двойные переносы строк."
            )})
            _summary_resp = await asyncio.wait_for(
                _agent_inst.call_ai(_messages, use_tools=False, max_tokens=800, api_timeout=30),
                timeout=35,
            )
            if _summary_resp and _summary_resp.get('choices'):
                _summary_text = (_summary_resp['choices'][0]['message'].get('content') or '').strip()
                if _summary_text and (not _final_text or len(_final_text) < 80):
                    _final_text = _summary_text
                    logger.info("[DIRECTOR-EXEC] autopilot summary filled (was empty): %d chars", len(_final_text))
        except Exception as _sum_err:
            logger.debug("[DIRECTOR-EXEC] summary expansion failed: %s", _sum_err)

    if _final_text and len(_final_text) > 3500 and _final_text != _done_fb:
        # Обрезаем до последнего завершённого предложения в пределах 3500 символов
        _cut = _final_text[:3500]
        _last_dot = max(_cut.rfind('.'), _cut.rfind('!'), _cut.rfind('?'))
        if _last_dot > 200:
            _final_text = _cut[:_last_dot + 1]

    # ── Интеграционные подсказки: если инструмент натолкнулся на ограничение — сообщаем ──
    # Сканируем tool-результаты — если нашли «не настроен / нет токена» и агент сам
    # об этом не написал, добавляем короткую рекомендацию. Макс. 2 подсказки на ответ.
    if _final_text and _final_text != _done_fb and _tools_used and _messages:
        _hints = _extract_intg_hints(_messages)
        if _hints:
            _ft_lower = _final_text.lower()
            _added = 0
            for _h in _hints:
                if _added >= 2:
                    break
                # fingerprint — первые 30 символов подсказки после "💡 "
                _hfp = _h[3:33].lower() if _h.startswith('💡') else _h[:30].lower()
                if _hfp not in _ft_lower:
                    _final_text += f"\n\n{_h}"
                    _added += 1

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

    # ── Субделегирования: агент может передать часть работы коллеге (depth < 2) ──
    if _pending_subdelegations and _depth < 1:
        try:
            from models import Session as _SubDb, UserAgent as _SubUA, User as _SubU
            _sub_s = _SubDb()
            try:
                _sub_u = _sub_s.query(_SubU).filter_by(telegram_id=user_id).first()
                _author_id = _sub_u.id if _sub_u else None
                if _author_id:
                    _all_team = _sub_s.query(_SubUA).filter(
                        _SubUA.author_id == _author_id,
                        _SubUA.status.in_(['active', 'paused']),
                        _SubUA.id != agent.get('id'),
                    ).all()
                    _team_map = {a.name.lower(): a for a in _all_team}

                    _sub_results = []
                    for _sd in _pending_subdelegations[:2]:  # макс 2 субделегирования
                        _target_name = _sd['agent_name']
                        _target_agent = _team_map.get(_target_name.lower())
                        if not _target_agent:
                            # Fuzzy match
                            for _tn, _ta in _team_map.items():
                                if _target_name.lower() in _tn or _tn in _target_name.lower():
                                    _target_agent = _ta
                                    break
                        if not _target_agent:
                            continue

                        _ta_dict = {
                            'id': _target_agent.id,
                            'name': _target_agent.name,
                            'job_title': _target_agent.job_title or '',
                            'specialization': _target_agent.specialization or '',
                            'description': _target_agent.description or '',
                            'personality': _target_agent.personality or '',
                            'python_code': _target_agent.python_code or '',
                            'user_api_keys': _target_agent.user_api_keys or '',
                            'tools_allowed': _target_agent.tools_allowed or '',
                            'author_id': _author_id,
                        }
                        logger.info("[SUBDELEGATE] %s → %s: %s", agent.get('name'), _target_agent.name, _sd['task'][:80])
                        # Списываем токены за субделегирование
                        try:
                            from token_service import spend_tokens as _sp_sub, has_enough_tokens as _het_sub
                            from config import FREE_ACCESS_MODE as _FAM_sub
                            if not _FAM_sub:
                                if not _het_sub(user_id, 'agent_task'):
                                    logger.info("[SUBDELEGATE] skip %s — not enough tokens", _target_agent.name)
                                    continue
                                _sp_sub(user_id, 'agent_task', description=f'subdelegate:{_target_agent.name}')
                        except Exception as _e:
                            logger.debug("suppressed: %s", _e)
                        try:
                            _sub_raw_sd = await asyncio.wait_for(
                                _exec_agent_for_director(_ta_dict, _sd['task'], user_id, dialog_context, _depth=_depth + 1),
                                timeout=60,
                            )
                            _sub_res = _sub_raw_sd[0] if isinstance(_sub_raw_sd, (tuple, list)) else _sub_raw_sd
                            _sub_tools = list(_sub_raw_sd[1]) if isinstance(_sub_raw_sd, (tuple, list)) and len(_sub_raw_sd) > 1 else []
                            _sub_results.append(f"{_target_agent.name}: {_sub_res}")
                            _tools_used.extend(_sub_tools)
                        except Exception as _sub_err:
                            logger.warning("[SUBDELEGATE] %s error: %s", _target_agent.name, _sub_err)

                    if _sub_results:
                        # Каждый субделегированный результат отправляется ОТДЕЛЬНЫМ сообщением
                        # (не батчим в один текст — пользователь видит планомерную работу команды)
                        for _sd_item, _sr in zip(_pending_subdelegations[:2], _sub_results):
                            _sd_target_name = _sr.split(':')[0].strip()
                            _sd_result_text = ':'.join(_sr.split(':')[1:]).strip() if ':' in _sr else _sr

                            # Отправляем сразу в Telegram + сохраняем в Interaction
                            try:
                                import aiogram
                                from models import Session as _MsgDb, Interaction as _MsgInt, User as _MsgU
                                _msg_s = _MsgDb()
                                try:
                                    _msg_u = _msg_s.query(_MsgU).filter_by(telegram_id=user_id).first()
                                    if _msg_u:
                                        # Формируем agent data для interaction
                                        _sd_ag_id = 0
                                        _sd_ag_avatar = ''
                                        for _tn2, _ta2 in _team_map.items():
                                            if _ta2.name == _sd_target_name:
                                                _sd_ag_id = _ta2.id
                                                _sd_ag_avatar = getattr(_ta2, 'avatar_url', '') or ''
                                                break
                                        # Сохраняем interaction
                                        _msg_s.add(_MsgInt(
                                            user_id=_msg_u.id,
                                            message_type='proactive',
                                            content=json.dumps({
                                                '__agent': {'name': _sd_target_name, 'id': _sd_ag_id, 'avatar_url': _sd_ag_avatar},
                                                'text': _sd_result_text[:600],
                                                '__tools_used': [],
                                                '__anchor_type': 'agent_delegation',
                                            }, ensure_ascii=False),
                                        ))
                                        _msg_s.commit()
                                        logger.info("[SUBDELEGATE] saved %s result as separate interaction", _sd_target_name)
                                finally:
                                    _msg_s.close()
                            except Exception as _msg_err:
                                logger.debug('[SUBDELEGATE] msg save error: %s', _msg_err)

                            # Также создаём задачу для трекинга
                            try:
                                _sd_agent_dict = {'id': _sd_ag_id, 'name': _sd_target_name}
                                _create_agent_delegation_task(
                                    _author_id, _sd_agent_dict,
                                    _sd_item.get('task', '')[:200],
                                    result_summary=_sd_result_text[:500],
                                )
                            except Exception as _sd_task_err:
                                logger.debug('[SUBDELEGATE] task create error: %s', _sd_task_err)

                        # В текст родительского агента добавляем КРАТКУЮ ссылку, а не полные результаты
                        _sd_names = [sr.split(':')[0].strip() for sr in _sub_results]
                        _final_text += f"\n\n(Поручил{'а' if _is_fem else ''} задачи: {', '.join(_sd_names)} — их ответы отправлены отдельно)"
            finally:
                _sub_s.close()
        except Exception as _sd_err:
            logger.debug('[SUBDELEGATE] error: %s', _sd_err)

    # Очищаем DSML-теги и технические артефакты перед возвратом
    try:
        from .utils import clean_technical_details as _ctd_exec
        _final_text = _ctd_exec(_final_text or '').strip() or _done_fb
    except Exception as _e:
        logger.debug("suppressed: %s", _e)

    # Для автопилота: если текст шаблонный но инструменты вызывались — принудительно расширяем через LLM
    if _is_autopilot_task and _tools_used and (_final_text == _done_fb or len((_final_text or '').strip()) < 60):
        try:
            _tool_data_fb = []
            for _m_fb in _messages:
                if _m_fb.get('role') == 'tool':
                    _td_fb = (_m_fb.get('content') or '')[:400]
                    if _td_fb and _td_fb != '{"status":"skipped"}':
                        _tool_data_fb.append(_td_fb)
            _tool_data_fb_str = '\n'.join(_tool_data_fb[-3:]) if _tool_data_fb else ''
            if _tool_data_fb_str:
                _aname_fb2 = (agent.get('name') or '').strip()
                _is_fem_fb = bool(_aname_fb2 and _aname_fb2[-1] in 'аяАЯ' and _aname_fb2[-2:].lower() not in ('ша', 'жа', 'ца', 'ча'))
                _gender_fb = (
                    "Ты женского рода — пиши: нашла, обнаружила, проверила, отправила, сделала.\n"
                    if _is_fem_fb else
                    "Ты мужского рода — пиши: нашёл, обнаружил, проверил, отправил, сделал.\n"
                )
                _fb_messages = [
                    {"role": "system", "content": (
                        f"Ты — {agent.get('name', 'агент')}. Расскажи пользователю ЧТО КОНКРЕТНО нашёл/сделал.\n"
                        + _gender_fb +
                        "Пиши от первого лица, живо, с фактами и цифрами.\n"
                        "НЕ пиши 'выполнил поиск' или 'обновил прогресс'.\n"
                        "⛔ ЗАПРЕЩЕНО упоминать названия инструментов (save_email_contact, web_search, update_goal_progress и т.д.).\n"
                        "Пиши что НАШЁЛ и СДЕЛАЛ, а не через какой инструмент.\n"
                        "СТИЛЬ: сплошной текст, 2–4 предложения. ЗАПРЕЩЕНО: списки (• – 1.), нумерация, заголовки (##), двойные переносы строк."
                    )},
                    {"role": "user", "content": (
                        f"Вот данные из инструментов:\n{_tool_data_fb_str}\n\n"
                        "Перескажи эти данные для пользователя: что нашлось, какие факты, "
                        "имена, цифры, и что думаешь делать дальше. Только текст, 2-4 предложения."
                    )},
                ]
                _fb_resp = await asyncio.wait_for(
                    _agent_inst.call_ai(_fb_messages, use_tools=False, max_tokens=250, api_timeout=25),
                    timeout=30,
                )
                if _fb_resp:
                    _u_fb2 = _fb_resp.get('usage') or {}
                    _total_ap_tokens += _u_fb2.get('prompt_tokens', 0) + _u_fb2.get('completion_tokens', 0)
                if _fb_resp and _fb_resp.get('choices'):
                    _fb_text = (_fb_resp['choices'][0]['message'].get('content') or '').strip()
                    if _fb_text and len(_fb_text) > 40:
                        _final_text = _fb_text
                        logger.info("[DIRECTOR-EXEC] autopilot fallback expanded: %d chars", len(_final_text))
        except Exception as _fb_err:
            logger.debug("[DIRECTOR-EXEC] autopilot fallback expansion failed: %s", _fb_err)

    # Для автопилота без инструментов: если текст содержательный (>100 символов) — пропускаем как аналитику,
    # если короткий/шаблонный — noise-фильтр в _dispatch_agent_for_anchor отсечёт
    if _is_autopilot_task and not _tools_used and len((_final_text or '').strip()) < 100:
        _final_text = ''  # слишком короткий текст без действий = noise

    # Шаблонные ответы с инструментами: "Выполнил поиск." — тоже noise
    if _is_autopilot_task and _final_text:
        _ft_lower = _final_text.strip().lower()
        _GENERIC_PATTERNS_AA = ('выполнил поиск', 'выполнила поиск', 'обновил прогресс',
                                'обновила прогресс', 'провёл поиск', 'провела поиск')
        if len(_final_text.strip()) < 100 and any(p in _ft_lower for p in _GENERIC_PATTERNS_AA):
            logger.info("[DIRECTOR-EXEC] autopilot generic noise filtered: %r", _final_text[:80])
            _final_text = ''

    logger.info("[DIRECTOR-EXEC] %s total_tokens=%d (%s)", agent.get('name', '?'), _total_ap_tokens, 'autopilot' if _is_autopilot_task else 'dialog')
    return _final_text, _tools_used, _total_ap_tokens


# ══ Вспомогательные функции для delegation pipeline ══════════════════════════

def _create_agent_delegation_task(user_db_id: int, agent: dict, task_text: str, result_summary: str = ''):
    """Создаёт Task с source='agent' для отображения в «Поручения агентам».
    Возвращает id задачи для последующего обновления."""
    if not user_db_id:
        return None
    try:
        from models import Session as _Db, Task as _Task
        from ai_integration.utils import normalize_task_title
        _s = _Db()
        try:
            _aname = agent.get('name', 'Агент')
            _title, _overflow = normalize_task_title(task_text, agent_name=_aname)
            # description = результат агента; если пусто — полный текст задачи (не только overflow)
            _desc = result_summary[:1000] if result_summary else (task_text[:1000] if task_text else _overflow[:1000])
            _t = _Task(
                user_id=user_db_id,
                title=_title,
                description=_desc,
                status='completed' if result_summary else 'in_progress',
                source='agent',
                created_by_agent_id=agent.get('id'),
                delegated_to_username=_aname,
            )
            _s.add(_t)
            _s.commit()
            _tid = _t.id
            logger.info("[DIRECTOR] created delegation task id=%s for agent=%s status=%s title='%s'", _tid, _aname, _t.status, _title[:60])
            return _tid
        except Exception as _e:
            logger.warning("[DIRECTOR] delegation task create error: %s", _e)
            try:
                _s.rollback()
            except Exception:
                pass
        finally:
            _s.close()
    except Exception as e:
        logger.warning("[DIRECTOR] delegation task error: %s", e)
    return None


def _update_agent_delegation_task(task_id: int, result_summary: str):
    """Обновляет Task агента: статус completed + результат."""
    if not task_id:
        return
    try:
        from models import Session as _Db, Task as _Task
        _s = _Db()
        try:
            _t = _s.query(_Task).filter_by(id=task_id).first()
            if _t:
                _t.status = 'completed'
                _t.description = result_summary[:1000] if result_summary else _t.description
                import datetime as _dt
                _t.actual_completion_time = _dt.datetime.now(_dt.timezone.utc)
                _s.commit()
                logger.info("[DIRECTOR] updated delegation task id=%s to completed", task_id)
        except Exception as _e:
            logger.warning("[DIRECTOR] delegation task update error: %s", _e)
            try:
                _s.rollback()
            except Exception:
                pass
        finally:
            _s.close()
    except Exception as e:
        logger.warning("[DIRECTOR] delegation task update error: %s", e)


# Ключевые слова для outreach-задач (рассылки, поиск людей, email-кампании)
_OUTREACH_KEYWORDS = (
    'email', 'рассылк', 'приглаш', 'outreach', 'найти людей',
    'найти тестировщ', 'набрать', 'привлеч', 'найти пользовател',
    'отправ письм', 'отправить приглаш', 'кампани', 'campaign',
    'найти клиент', 'найти исполнител', 'поиск контакт',
)


def _maybe_create_agent_campaign(user_db_id: int, agent: dict, task_text: str, result_summary: str = ''):
    """Создаёт DelegationCampaign если задача похожа на outreach/рассылку.
    Не создаёт для простых поручений типа 'напиши пост' или 'сделай картинку'."""
    if not user_db_id:
        return
    _tl = (task_text or '').lower()
    if not any(kw in _tl for kw in _OUTREACH_KEYWORDS):
        return  # не outreach — кампания не нужна
    try:
        from models import Session as _Db, DelegationCampaign as _DC
        _s = _Db()
        try:
            _name = task_text[:140]
            _dc = _DC(
                user_id=user_db_id,
                name=_name,
                goal=task_text[:500],
                target_audience=result_summary[:300] if result_summary else '',
                status='active',
                max_delegations=50,
                daily_limit=10,
            )
            _s.add(_dc)
            _s.commit()
            logger.info("[DIRECTOR] created outreach campaign id=%s for agent=%s", _dc.id, agent.get('name'))
        except Exception as _e:
            logger.warning("[DIRECTOR] campaign create error: %s", _e)
            try:
                _s.rollback()
            except Exception:
                pass
        finally:
            _s.close()
    except Exception as e:
        logger.warning("[DIRECTOR] campaign error: %s", e)


def _save_delegation_to_history(telegram_id: int, agent_name: str, task: str, result: str):
    """Сохраняет результат делегирования в conversation_history для контекста будущих сообщений."""
    try:
        from .conversation_history import save_message_to_history
        _summary = (
            f"[Поручил агенту {agent_name}: {task[:150]}]\n"
            f"Результат: {result[:400]}"
        )
        save_message_to_history(telegram_id, "assistant", _summary)
    except Exception as e:
        logger.debug("[DIRECTOR] save delegation to history error: %s", e)


# Слова-сигналы что пользователь хочет действие, а не разговор


# Кэш контекста директора: { user_id: {'ctx': str, 'history': list, 'expires': float} }
_DIRECTOR_CTX_CACHE: dict = {}
_DIRECTOR_CTX_TTL = 60  # секунд

async def _office_director_chat(user_message: str, user_id: int, progress_callback=None) -> str | dict | None:
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
            except Exception as _e:
                logger.debug("suppressed: %s", _e)

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

                # Первый запуск (нет ни одной подписки): авто-мигрируем.
                # Доп. запросы (_ever_had_subs / _ever_used_agents) делаем ТОЛЬКО
                # когда _existing_subs пустой — иначе лишние round-trip на каждый запрос.
                if _own_all and not _existing_subs:
                    for _oa in _own_all:
                        _s.add(_AS(user_id=user_db_id, agent_id=_oa.id))
                        try:
                            from .user_agents import set_user_active_agent as _sua_dir
                            _sua_dir(user_id, _oa.id)
                        except Exception as _e:
                            logger.debug("suppressed: %s", _e)
                    _s.commit()
                    _existing_subs = {a.id for a in _own_all}

                _own_agents = [a for a in _own_all if a.id in _existing_subs]
            except Exception as _sub_err:
                logger.warning("[DIRECTOR] subscription check error, loading empty: %s", _sub_err)
                _own_agents = []
            _own_ids = {a.id for a in _own_agents}

            # Источник 3: сессионно-активированные с загрузкой из БД (если не вошли в own)
            # get_user_active_agents уже фильтрует по AgentSubscription, поэтому _session_ids чисты
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
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)
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

    # ── Ранний фильтр: вопрос без прямого обращения к агенту → ASI ответит сам ──
    # Исключение: вопрос упоминает слова из лейблов интеграций любого агента → нужен агент.
    # Работаем с нормализованными лейблами _parse_agent_integrations — универсально для любых интеграций.
    if _is_question_message(user_message):
        _msg_lc_early = user_message.lower().strip()
        _has_agent_mention_early = any(
            len(_a.get('name') or '') >= 3
            and (_a.get('name') or '').lower() in _msg_lc_early
            for _a in _agents
        )
        _integration_question = False
        if not _has_agent_mention_early:
            # Слова сообщения (≥4 символов) — ищем пересечение с лейблами интеграций
            _msg_words = {w for w in _msg_lc_early.split() if len(w) >= 4}
            for _a in _agents:
                try:
                    _a_intg = _parse_agent_integrations(
                        _a.get('user_api_keys') or '',
                        _a.get('python_code') or '',
                        _a.get('tools_allowed') or '',
                    )
                except Exception:
                    _a_intg = []
                for _intg_label in _a_intg:
                    # Слова из лейбла (напр. "Gmail (почта)" → {"gmail", "почта"})
                    _label_words = {
                        w.lower() for w in _intg_label.replace('(', ' ').replace(')', ' ').split()
                        if len(w) >= 4
                    }
                    if _label_words & _msg_words:
                        _integration_question = True
                        break
                if _integration_question:
                    break
        if not _has_agent_mention_early and not _integration_question:
            logger.debug("[DIRECTOR] early filter: question without agent mention, skip")
            return None

    # Строим универсальный контекст пользователя + историю — кэшируем 60с
    import time as _time_dir
    _cache_hit = _DIRECTOR_CTX_CACHE.get(user_db_id)
    if _cache_hit and _cache_hit['expires'] > _time_dir.time():
        _user_full_ctx = _cache_hit['ctx']
        _history_lines = _cache_hit['history']
    else:
        _user_full_ctx = _build_user_context_sync(user_db_id) if user_db_id else ''
        # История: загружаем в той же логике но отдельно (Session уже закрыт выше)
        _history_lines = []
        if user_db_id:
            try:
                from models import Interaction as _Itr
                _hs = _Db()
                try:
                    _recent = (
                        _hs.query(_Itr)
                        .filter(_Itr.user_id == user_db_id)
                        .order_by(_Itr.id.desc())
                        .limit(3)
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
        if user_db_id:
            _DIRECTOR_CTX_CACHE[user_db_id] = {
                'ctx': _user_full_ctx,
                'history': _history_lines,
                'expires': _time_dir.time() + _DIRECTOR_CTX_TTL,
            }
    _history_block = ('\n\nПОСЛЕДНИЕ СООБЩЕНИЯ:\n' + '\n'.join(_history_lines)) if _history_lines else ''

    # ── Кешируем возможности агентов (один раз, используется в двух местах) ──────
    _agent_caps_cache: dict[str, list[str]] = {}
    for _a in _agents:
        try:
            _ci = _parse_agent_integrations(
                _a.get('user_api_keys') or '',
                _a.get('python_code') or '',
                _a.get('tools_allowed') or '',
                _a.get('search_scope') or '',
            )
        except Exception:
            _ci = []
        if not _ci:
            _ci = _infer_capabilities_from_role(
                _a.get('job_title') or '',
                _a.get('specialization') or '',
                _a.get('description') or '',
            )
        _agent_caps_cache[_a['name']] = _ci

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
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)
            except Exception as _e:
                logger.debug("suppressed: %s", _e)

    # ── Вспомогательная функция сохранения результата агента ──────────────────
    async def _run_agent_task(ag, task, extra_context: str = "", director_message: str = ""):
        # Отправляем живое обращение директора к агенту и сохраняем в DB
        if director_message:
            # Форматируем без emoji-шаблонов — текст поручения должен быть естественным
            _ag_n = ag.get('name', 'Агент')
            if _ag_n.lower() in director_message.lower()[:len(_ag_n)+3]:
                _dm_display = director_message
            else:
                _dm_display = f"{_ag_n}, {director_message}"
            # Нормализуем: после "Имя, " первая буква должна быть строчной
            import re as _re_dm
            _dm_display = _re_dm.sub(
                r'([А-ЯЁA-Z][а-яёa-z]+, )([А-ЯЁA-Z])',
                lambda m: m.group(1) + m.group(2).lower(),
                _dm_display,
            )
            # Сохраняем директиву в чат (дедупликация только сообщения, НЕ выполнения задачи).
            # Даже если сообщение — дубль (< 5 мин), агент всё равно запускается — это новое поручение.
            _msg_dedup = _save_interaction_for_director(user_id, _dm_display, message_type='agent_msg')
            if not _msg_dedup:
                logger.info("[DIRECTOR] directive is a duplicate message (not blocking execution) for %s: %s...", ag.get('name'), director_message[:60])

        # Списываем токены за запуск агента директором
        try:
            from config import FREE_ACCESS_MODE as _FAM
            from token_service import spend_tokens as _st, has_enough_tokens as _het_at
            if not _FAM:
                if not _het_at(user_id, 'agent_task'):
                    logger.info("[DIRECTOR] user %d: skip agent_task — not enough tokens", user_id)
                    return "Недостаточно токенов для запуска агента."
                _st(user_id, 'agent_task', description=f'{ag["name"]}: {task[:60]}')
        except Exception as _e:
            logger.debug("suppressed: %s", _e)

        # Агентские поручения логируются только в AgentActivityLog (не в Task)
        _task_id = None

        resp = await _exec_agent_for_director(ag, task, user_id, dialog_context=extra_context)
        _agent_tools_used: list[str] = []
        if isinstance(resp, tuple):
            if len(resp) >= 3:
                resp, _agent_tools_used, _ap_tokens = resp
            elif len(resp) == 2:
                resp, _agent_tools_used = resp
        if isinstance(resp, Exception) or not resp:
            resp = "Данных нет."

        # Rework: если агент ответил пустым фоллбэком или слишком коротко — быстрый LLM fallback
        _resp_lower = str(resp).strip().lower()
        _resp_len = len(str(resp).strip())
        _fallback_phrases = ('задачу выполнил.', 'задачу выполнила.', 'данных нет.')
        _intermediate_markers = ('использую', 'ищу данные', 'уточняю поиск', 'исследую',
                                  'начинаю', 'приступаю', 'анализирую', 'подготовлю',
                                  'первый запрос дал', 'сейчас найду', 'сейчас подготовлю',
                                  'сейчас проведу', 'понял, алексей', 'понял,',
                                  'начну с', 'сейчас разработаю', 'сейчас проанализирую')
        _is_fallback = _resp_lower in _fallback_phrases
        _is_intermediate = (len(str(resp).strip()) < 200 and
                            any(m in _resp_lower for m in _intermediate_markers))
        _is_too_short = _resp_len < 120 and _resp_len > 5
        # В автопилоте: если агент не вызвал ни одного инструмента — ответ бесполезен
        _is_autopilot_no_tools = (
            not _agent_tools_used
            and any(m in (task or '').lower() for m in ('автопилот', 'autopilot', 'l2 координация'))
            and _resp_len < 400
        )
        _skip_rework = _is_question_message(task)
        if (_is_fallback or _is_intermediate or _is_too_short or _is_autopilot_no_tools) and not _skip_rework:
            # Быстрый fallback — агент ответил пусто/коротко/промежуточно
            _rework_hint = (
                f"Предыдущий ответ: {str(resp).strip()[:200]}\n\n" if _is_too_short and not _is_fallback else ''
            )
            _fallback_resp = await _quick_ai_call_raw([{
                "role": "user",
                "content": (
                    f"Ты — {ag.get('name', 'специалист')} ({ag.get('specialization', '')}).\n"
                    f"Задача: {task}\n"
                    f"Контекст: {(extra_context or '')[:500]}\n"
                    f"{_rework_hint}"
                    f"СРАЗУ дай готовый результат — конкретные идеи, данные, план, рекомендации. "
                    f"НЕ пиши 'сейчас сделаю', 'начну с', 'понял' — ПИШИ САМ ОТВЕТ. "
                    f"Минимум 200 символов, 2-3 абзаца, без markdown."
                ),
            }], max_tokens=300)
            if _fallback_resp and len(_fallback_resp) > 50:
                resp = _fallback_resp

        # Результат агента сохраняется в DB как __agent JSON (proxy URL, никогда не base64).
        resp = _strip_agent_html(str(resp))
        try:
            from .utils import clean_technical_details as _ctd_ag
            _cleaned_ag = _ctd_ag(resp)
            if _cleaned_ag and _cleaned_ag.strip():
                resp = _cleaned_ag
        except Exception as _e:
            logger.debug("suppressed: %s", _e)
        _ag_id = ag.get('id')
        _av_url = f'/api/arena/agent_avatar/{_ag_id}' if _ag_id else ''
        _ac = _json.dumps({
            '__agent': {'name': ag.get('name'), 'id': _ag_id, 'avatar_url': _av_url},
            'text': resp,
            '__tools_used': _agent_tools_used,
        }, ensure_ascii=False)
        # Отправляем ответ агента в чат (Telegram: форматируется в progress_callback,
        # web SSE: агентский пузырь с аватаром). DB sync дедуплицирует через addMessage().
        await _send_visible(_ac)
        _save_interaction_for_director(user_id, _ac)
        await asyncio.sleep(0.05)

        # Логируем в AgentActivityLog (без создания Task)
        if user_db_id:
            try:
                from models import Session as _TDb2, AgentActivityLog as _AAL2
                _ts2 = _TDb2()
                try:
                    _tools_info = ', '.join(_agent_tools_used) if _agent_tools_used else ''
                    # Убираем внутренние инструкции из title для хронологии дашборда
                    _clean_title = task
                    for _strip_prefix in ['ОТВЕТЬ НА ВОПРОС (просто ответь, без создания задач и делегирования): ', 'ОТВЕТЬ НА ВОПРОС: ', '[АВТОПИЛОТ] ']:
                        if _clean_title.startswith(_strip_prefix):
                            _clean_title = _clean_title[len(_strip_prefix):]
                    _ts2.add(_AAL2(
                        user_id=user_db_id,
                        activity_type='agent_task',
                        title=_clean_title[:200],
                        content=str(resp)[:500],
                        target=f"agent:{ag.get('name', 'Агент')}",
                        status='completed',
                        result=_tools_info[:300] if _tools_info else None,
                    ))
                    _ts2.commit()
                finally:
                    _ts2.close()
            except Exception as _ae:
                logger.warning("[DIRECTOR] activity log error: %s", _ae)

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
        return str(resp)[:2000]

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
        # Перенаправляем в основной цикл делегирования — полная цепочка по способностям
        _direct_ctx_parts = []
        if _user_full_ctx:
            _direct_ctx_parts.append(_user_full_ctx)
        if _history_block.strip():
            _direct_ctx_parts.append(_history_block.strip())
        _del_ctx = '\n\n'.join(_direct_ctx_parts)
        _ag = _direct_agent
        _dm = ''
        _task = user_message
        # Убираем имя агента из начала задачи для чистого title
        _da_name = _ag.get('name', '').lower()
        if _da_name and _task.lower().startswith(_da_name):
            import re as _re_da
            _task = _re_da.sub(
                r'^' + _re_da.escape(_ag['name']) + r'[\s,:.!]*',
                '', _task, flags=_re_da.IGNORECASE,
            ).strip() or _task
        # Если вопрос — подсказываем агенту: ответь, не действуй
        if _is_question_message(user_message):
            _task = f"ОТВЕТЬ НА ВОПРОС (просто ответь, без создания задач и делегирования): {_task}"
        _agent_name_d = _ag.get('name', 'Агент')
    else:
        _ag = None  # will be set by ASI decision below

    if _direct_agent:
        # direct_agent: skip ASI decision, go straight to multi-round loop
        pass
    else:
        # Вопросы без прямого обращения к агенту → ASI отвечает сам через process_request
        if _is_question_message(user_message):
            return None

        # ── Начальное решение ASI ──────────────────────────────────────────────────
        # Урезаем до 400 символов — для решения о делегировании достаточно имени, должности, целей
        _ctx_hint = f"\n\nКОНТЕКСТ:\n{_user_full_ctx[:400]}" if _user_full_ctx else ''

        # Строим компактный список агентов: имя | должность | специализация | умеет
        _agent_caps_lines = []
        for _ac_a in _agents:
            _ac_intg = _agent_caps_cache.get(_ac_a['name'], [])
            _ac_caps = ', '.join(_ac_intg[:6]) if _ac_intg else '—'
            _ac_desc = (_ac_a.get('description') or '')[:60]
            # Строим читаемый список инструментов — с маппингом из интеграций
            _ac_tools_str = _agent_tools_from_intg(_ac_a, _ac_intg)
            _tools_raw = (_ac_a.get('tools_allowed') or '').strip()
            _tools_is_explicit = bool(_tools_raw and _tools_raw != '[]')
            _tools_label = 'Инструменты (явные)' if _tools_is_explicit else 'Инструменты (из роли/интеграций)'
            _line = (
                f"• {_ac_a['name']} | {_ac_a.get('job_title','')}"
                f" | {_ac_a.get('specialization','')}"
                f"\n  Умеет: {_ac_caps}"
                f"\n  {_tools_label}: {_ac_tools_str}"
            )
            if _ac_desc:
                _line += f"\n  О себе: {_ac_desc}"
            _agent_caps_lines.append(_line)
        _caps_block = "\n".join(_agent_caps_lines)

        _decision_prompt = (
            f"Запрос: «{user_message}»\n\n"
            f"Агенты пользователя:\n{_caps_block}\n"
            f"{_ctx_hint}{_history_block}\n\n"
            "Решение: self или поручить агенту?\n\n"
            "self — ASI выполняет НАПРЯМУЮ своими инструментами:\n"
            "  • задачи (add_task, complete_task, edit_task, delete_task, list_tasks, set_reminder)\n"
            "  • цели (create_goal, update_goal, complete_goal, list_goals)\n"
            "  • generate_image — генерация картинок/изображений\n"
            "  • контент (create_post, publish_to_telegram, publish_to_discord)\n"
            "  • email (send_email, send_outreach_email, negotiate_by_email)\n"
            "  • research_topic, get_news_trends — исследования/аналитика\n"
            "  • делегирование задач другим пользователям (delegate_task)\n"
            "  • коммуникации (send_message_to_user, find_and_message_relevant_users)\n"
            "  • контакты (find_relevant_contacts_for_task, set_contact_alert)\n"
            "  • update_profile, schedule_background_task, get_system_status\n"
            "  • привет/пока, вопрос-ответ, советы, любые разговоры\n\n"
            "поручить агенту — ТОЛЬКО если:\n"
            "  1) задача требует СПЕЦИФИЧЕСКОЙ экспертизы конкретного агента\n"
            "  2) у агента есть нужный инструмент (см. 'Инструменты' в профиле агента выше)\n"
            "  3) ASI НЕ может сделать это сам своими инструментами\n\n"
            "СОВЕТ: 'Инструменты (явные)' — агент настроен на эти инструменты явно.\n"
            "       'Инструменты (из роли/интеграций)' — рекомендации на основе роли/API-ключей.\n"
            "⚠️ Если ASI умеет сделать запрос — ВСЕГДА self.\n"
            "⚠️ НЕ поручай агенту то, чего НЕТ в его инструментах.\n"
            "⚠️ ВОПРОСЫ (есть ли?, что?, сколько?, как?) — ВСЕГДА self. Не делегируй вопросы агентам.\n"
            "⚠️ ЛИЧНЫЕ ДОСТИЖЕНИЯ (я сделал, я заказал, я оплатил, я купил, я позвонил, я написал, я прошёл, я настроил, готово, сделано, выполнено) — ВСЕГДА self. Только ASI умеет complete_task.\n"
            "⚠️ 'Займитесь сами', 'работайте без меня', 'занимайтесь', 'действуйте' без конкретного имени агента — ВСЕГДА self (автопилот уже активен, подтверди коротко).\n"
            "Если пользователь ЯВНО обращается к агенту по имени — поручить.\n"
            "director_message: живое КОРОТКОЕ обращение — 'Имя, глагол + суть' (10-15 слов, без копипаста agent_task).\n"
            "director_message — пример: 'Марк, найди 5 площадок где сидят AI-энтузиасты и напиши им'. НЕ: 'Марк, Найти и привлечь тестировщиков'.\n"
            "ПРАВИЛО: после запятой — глагол в повелительном наклонении строчными: найди, подготовь, напиши, исследуй.\n\n"
            "JSON без ```:\n"
            '{"action":"self"}\n'
            "или\n"
            '{"action":"delegate","agent_name":"имя","agent_task":"суть задачи без имени",'
            '"director_message":"Имя, сделай..."}'
        )

        # Быстрый пре-фильтр: короткие бытовые реплики → ASI отвечает сам через process_request
        # НО если есть активная миссия (якорь __mission__ < 30 мин) — передаём директору для продолжения
        _ml = user_message.strip()
        _ml_lower = _ml.lower()
        _trivial_replies = ('да', 'нет', 'ок', 'окей', 'ладно', 'хорошо', 'давай', 'понял', 'спасибо',
                            'привет', 'хай', 'здравствуй', 'пока', 'стоп', 'отмена')
        _is_trivial = _ml_lower.rstrip('!., ') in _trivial_replies

        # Пре-фильтр: "займитесь сами/без меня/действуйте" без имени агента → всегда self
        _self_phrases = ('займитесь', 'занимайтесь', 'работайте без меня', 'действуйте сами',
                         'работайте сами', 'без меня', 'действуйте')
        _is_autopilot_confirm = any(p in _ml_lower for p in _self_phrases) and not any(
            a.get('name', '').lower() in _ml_lower for a in _agents
        )
        if _is_autopilot_confirm:
            return None  # process_request ответит коротким подтверждением автопилота

        # Пре-фильтр: личные достижения → только ASI умеет complete_task
        _achievement_words = ('я заказал', 'я купил', 'я оплатил', 'я позвонил', 'я написал',
                              'я отправил', 'я настроил', 'я прошёл', 'я починил', 'я записался',
                              'я сделал', 'я выполнил', 'я завершил', 'я приготовил', 'я убрал')
        _is_achievement = any(_ml_lower.startswith(p) or f' {p} ' in _ml_lower for p in _achievement_words)
        if _is_achievement:
            return None  # process_request вызовет complete_task

        if _is_trivial:
            _has_active_mission = False
            _mission_context = ''
            try:
                _mission_anchors = _get_agent_anchors(user_db_id, 0, hours=0.5)
                for _ma in _mission_anchors:
                    if _ma.get('topic', '').startswith('__mission__') and _ma.get('age_min', 999) < 30:
                        _has_active_mission = True
                        _md = _ma.get('data', {})
                        _mission_context = _md.get('result_summary') or _md.get('task', '')
                        break
            except Exception as _e:
                logger.debug("suppressed: %s", _e)

            if not _has_active_mission:
                return None  # Нет активной миссии — ASI ответит сам через process_request

            # Есть активная миссия — "да"/"давай" = продолжить
            # Подменяем запрос чтобы LLM понял контекст
            _affirmative = _ml_lower.rstrip('!., ') in ('да', 'ок', 'окей', 'ладно', 'хорошо', 'давай')
            if _affirmative and _mission_context:
                # Переформулируем для директора: "Пользователь подтвердил — продолжай миссию"
                _decision_prompt = (
                    f"Ты — ASI Biont, директор офиса. Пользователь подтвердил продолжение миссии.\n\n"
                    f"АКТИВНАЯ МИССИЯ: {_mission_context[:300]}\n\n"
                    f"Пользователь ответил: «{user_message}»\n\n"
                    f"ПРОФИЛИ АГЕНТОВ КОМАНДЫ:\n{_caps_block}\n"
                    f"{_ctx_hint}{_history_block}\n\n"
                    "Пользователь хочет ПРОДОЛЖИТЬ. Выбери следующее действие — delegate, adaptive или multi_delegate.\n"
                    "НЕ выбирай self — пользователь явно хочет продолжения работы агентов.\n\n"
                    "Ответь ТОЛЬКО JSON без ```:\n"
                    '{"action": "delegate", "agent_name": "точное имя агента", '
                    '"agent_task": "задача", '
                    '"director_message": "живое: имя + глагол (Кристина, подготовь... / Марк, исследуй...)"}\n'
                    "или\n"
                    '{"action": "adaptive", "director_intro": "план", "mission_brief": "цель миссии", '
                    '"first_agent_name": "имя", "first_agent_task": "задача", '
                    '"director_message": "живое: имя + глагол"}'
                )
            elif _ml_lower.rstrip('!., ') in ('нет', 'стоп', 'отмена'):
                return None  # Отмена — сброс миссии

        decision_raw = await _quick_ai_call_raw([{"role": "user", "content": _decision_prompt}], max_tokens=250, _caller='director_decision')
        if not decision_raw:
            return None

        decision = None
        _jm = re.search(r'```(?:json)?\s*([\s\S]*?)```', decision_raw or '')
        _json_str = _jm.group(1) if _jm else None
        if not _json_str:
            # Ищем JSON объект в сыром ответе
            _jm2 = re.search(r'(\{[\s\S]*\})', decision_raw or '')
            _json_str = _jm2.group(1) if _jm2 else None
        if _json_str:
            try:
                decision = _json.loads(_json_str)
            except Exception:
                logger.info("[DIRECTOR] JSON parse failed, raw=%s", (decision_raw or '')[:120])
        if not decision:
            return None

        action = decision.get('action', 'self')

        # Нормализуем: adaptive/multi_delegate → delegate (один агент на запрос)
        if action == 'adaptive':
            # Конвертируем adaptive → delegate
            decision['agent_name'] = decision.get('first_agent_name', '')
            decision['agent_task'] = decision.get('first_agent_task', '')
            if not decision.get('director_message'):
                decision['director_message'] = ''
            action = 'delegate'
        elif action == 'multi_delegate':
            # Конвертируем multi_delegate → delegate (первый агент из списка)
            _tasks_list = decision.get('tasks') or []
            if _tasks_list:
                _first_t = _tasks_list[0]
                decision['agent_name'] = _first_t.get('agent_name', '')
                decision['agent_task'] = _first_t.get('agent_task', '')
                decision['director_message'] = _first_t.get('director_message', '')
            action = 'delegate'

        # ── self: возвращаем None → управление идёт в process_request с tool-calling ──
        if action != 'delegate':
            return None

        # ── Валидация: если задача требует коммуникации/поиска людей,
        # а у агента нет нужного инструмента — ASI делает сам ──────────────
        _ag_check = _find_agent(decision.get('agent_name', ''))
        if _ag_check:
            _task_lower = (decision.get('agent_task') or user_message).lower()
            _comm_keywords = ('найди', 'пригласи', 'напиши', 'отправь', 'сообщ', 'пользовател',
                              'приглаш', 'invite', 'message', 'find.*user', 'тестировщик',
                              'тестер', 'аудитори', 'контакт')
            _needs_comm = any(kw in _task_lower for kw in _comm_keywords)
            if _needs_comm:
                _ag_tools_str = (_ag_check.get('tools_allowed') or '').lower()
                _has_comm_tool = any(t in _ag_tools_str for t in
                                     ('find_and_message', 'send_message', 'find_relevant_contacts'))
                if not _has_comm_tool:
                    logger.info("[DIRECTOR] Agent %s lacks comm tools for task, ASI handles self",
                                _ag_check.get('name'))
                    return None  # ASI сделает сам через process_request

        # ── delegate: один агент на запрос ──────────────────────────────────
        _agent_ctx_parts = []
        if _user_full_ctx:
            _agent_ctx_parts.append(_user_full_ctx)
        if _history_block.strip():
            _agent_ctx_parts.append(_history_block.strip())
        _del_ctx = '\n\n'.join(_agent_ctx_parts)

        _ag = _find_agent(decision.get('agent_name', ''))
        if not _ag:
            return None
        _dm = decision.get('director_message', '')
        _task = decision.get('agent_task') or user_message
        # Убираем имя агента из задачи если AI случайно его добавил
        _ag_name_clean = _ag.get('name', '')
        if _ag_name_clean and _task.lower().startswith(_ag_name_clean.lower()):
            import re as _re_task_clean
            _task = _re_task_clean.sub(
                r'^' + _re_task_clean.escape(_ag_name_clean) + r'[\s,:.!]*',
                '', _task, flags=_re_task_clean.IGNORECASE,
            ).strip() or _task

    # ── Многораундовый цикл: АСИ ↔ агент ─────────────────────────────────────
    # АСИ даёт поручение → агент отчитывается → АСИ решает: ещё поручение или принять
    _is_q = _is_question_message(user_message)
    # Прямое обращение к агенту → 1 раунд без review (отвечает САМ агент, без АСИ-надстройки)
    _is_direct = _direct_agent is not None
    _MAX_AGENT_ROUNDS = 1 if (_is_q or _is_direct) else 3
    _agent_name_d = _ag.get('name', 'Агент')
    _round_history: list[dict] = []  # история раундов для контекста

    for _round in range(_MAX_AGENT_ROUNDS):
        # Создаём Task in_progress ДО запуска агента (только для поручений, не для вопросов)
        _delegation_task_id = None if _is_q else _create_agent_delegation_task(user_db_id, _ag, _task)

        # Запуск агента
        try:
            _resp = await _run_agent_task(_ag, _task, extra_context=_del_ctx, director_message=_dm)
        except Exception as _run_err:
            logger.warning("[DIRECTOR] agent run error round %d: %s", _round, _run_err)
            if _delegation_task_id:
                try:
                    _update_agent_delegation_task(_delegation_task_id, f'Ошибка: {str(_run_err)[:200]}')
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)
            break

        _agent_tools_used_round: list[str] = []
        if isinstance(_resp, tuple):
            _resp, _agent_tools_used_round = _resp
        _agent_result = str(_resp or '')[:600]

        # Обновляем Task → completed
        _update_agent_delegation_task(_delegation_task_id, _agent_result[:400])

        # Запоминаем раунд
        _round_history.append({'task': _task, 'director_msg': _dm, 'result': _agent_result, 'tools_used': _agent_tools_used_round})

        # Вопрос или прямое обращение к агенту — один раунд, без review/followup
        if _is_q or _is_direct:
            break

        # Создаём DelegationCampaign если задача outreach-типа
        if _round == 0:
            _maybe_create_agent_campaign(user_db_id, _ag, _task, _agent_result[:400])

        # ── АСИ-директор решает: продолжить или принять ────────────────────
        _rounds_summary = '\n'.join(
            f"Раунд {i+1}: Поручение: {r['task'][:150]}\nОтчёт: {r['result'][:250]}"
            + (f"\nИнструменты агента: {', '.join(r['tools_used'])}" if r.get('tools_used') else '')
            for i, r in enumerate(_round_history)
        )

        # Собираем все инструменты которые агент вызвал за все раунды
        _all_agent_tools = list(dict.fromkeys(
            t for _rh in _round_history for t in _rh.get('tools_used', [])
        ))

        # Оптимизация: если агент вызвал инструменты или это последний раунд →
        # пропускаем review-вызов, сразу переходим к accept_and_act.
        # Review нужен только когда агент дал текст без действий (решаем: next_task или accept).
        _is_last_round = (_round == _MAX_AGENT_ROUNDS - 1)
        if _all_agent_tools or _is_last_round:
            _review_action = 'accept_and_act'
            _accept_summary = ''
            _my_action = ''
        else:
            _review_prompt = (
                f"Ты ASI-директор. У тебя ЕСТЬ собственные инструменты платформы — ВСЕ те же что у агентов:\n"
                f"send_email, send_outreach_email, negotiate_by_email, publish_to_telegram, publish_to_discord, "
                f"create_post, research_topic, web_search, generate_image, start_content_campaign, "
                f"start_delegation_campaign, find_relevant_contacts_for_task, schedule_background_task, "
                f"add_task, delegate_task и другие.\n\n"
                f"Пользователь попросил: {user_message[:300]}\n\n"
                f"ИСТОРИЯ РАБОТЫ С АГЕНТОМ {_agent_name_d}:\n{_rounds_summary}\n\n"
                f"Раундов прошло: {_round + 1} из {_MAX_AGENT_ROUNDS}.\n"
                f"Инструменты агента за все раунды: нет\n\n"
                f"Агент дал только текст. РЕШИ:\n"
                f"- next_task — дать агенту СЛЕДУЮЩЕЕ поручение (если нужен ещё шаг)\n"
                f"- accept_and_act — принять и САМОМУ выполнить следующий шаг\n\n"
                f"Ответ СТРОГО JSON:\n"
                f'{{"action": "next_task", "director_message": "Агент, теперь ...", "agent_task": "..."}}\n'
                f'или\n'
                f'{{"action": "accept_and_act", "summary": "кратко что сделано", '
                f'"my_action": "конкретное действие"}}\n'
            )
            _review_raw = await _quick_ai_call_raw(
                [{"role": "user", "content": _review_prompt}], max_tokens=250, _caller='director_review'
            )

            _review_decision = None
            _rj = re.search(r'(\{[\s\S]*\})', _review_raw or '')
            if _rj:
                try:
                    _review_decision = _json.loads(_rj.group(1))
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)

            _review_action = _review_decision.get('action', '') if _review_decision else ''
            _accept_summary = (_review_decision.get('summary', '') if _review_decision else '')
            _my_action = (_review_decision.get('my_action', '') if _review_decision else '')

        if _review_action not in ('next_task',):
            # ПРИНЯТЬ: АСИ принимает работу
            # Быстрый follow-up без tool calling (экономит ~20-30с)
            _agent_did = ', '.join(_all_agent_tools) if _all_agent_tools else 'нет'
            try:
                _fu_final_text = await _quick_ai_call_raw([{
                    "role": "user", "content": (
                        f"Ты ASI — директор офиса. Агент {_agent_name_d} отработал по задаче: {_task[:200]}\n"
                        f"Использованные инструменты: {_agent_did}\n"
                        f"Результат агента (уже видим пользователю): {_round_history[-1]['result'][:300] if _round_history else ''}\n\n"
                        f"Напиши 1-2 предложения от лица директора — что ты делаешь ДАЛЬШЕ. "
                        f"НЕ пересказывай что делал агент. Без markdown, без списков."
                    ),
                }], max_tokens=150, _caller='director_followup')
                if _fu_final_text and len(_fu_final_text.strip()) > 10:
                    try:
                        from .utils import clean_technical_details as _ctd_fu
                        _fu_final_text = _ctd_fu(_fu_final_text).strip()
                    except Exception as _e:
                        logger.debug("suppressed: %s", _e)
                    if _fu_final_text:
                        await _send_visible(_fu_final_text)
                        _save_interaction_for_director(user_id, _fu_final_text, message_type='ai')
            except Exception as _fu_err:
                logger.warning("[DIRECTOR] followup error: %s", _fu_err)

            break  # Выходим из цикла — работа принята

        # NEXT_TASK: АСИ даёт следующее поручение агенту → продолжаем цикл
        _dm = _review_decision.get('director_message', '')
        _task = _review_decision.get('agent_task') or _task
        logger.info("[DIRECTOR] round %d → next_task for %s: %s", _round + 1, _agent_name_d, _task[:80])
    else:
        # Все раунды исчерпаны без accept — генерируем итоговый доклад
        _rounds_summary_final = '\n'.join(
            f"Раунд {i+1}: {r['result'][:200]}" for i, r in enumerate(_round_history)
        )
        _final_report = await _quick_ai_call_raw([{
            "role": "user",
            "content": (
                f"Ты ASI-директор. Агент {_agent_name_d} отработал {len(_round_history)} раунд(ов) "
                f"по задаче: {user_message[:200]}\n\n"
                f"Результаты:\n{_rounds_summary_final}\n\n"
                f"Напиши краткий итоговый доклад пользователю (3-4 предложения): "
                f"что сделано, какие результаты, что дальше. Без markdown."
            ),
        }], max_tokens=250)
        if _final_report and len(_final_report.strip()) > 10:
            await _send_visible(_final_report)
            _save_interaction_for_director(user_id, _final_report, message_type='ai')

    # Сохраняем контекст всех раундов делегирования
    _all_results = ' | '.join(r['result'][:200] for r in _round_history)
    _save_delegation_to_history(user_id, _agent_name_d, user_message, _all_results[:600])

    return "__agent_handled__"


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
        # Оптимизация: вопросы без имени агента → пропускаем директора целиком
        # (директор всё равно загрузит агентов из DB и вернёт None — экономим 10-15с)
        _director_response = None
        _skip_director = False
        if _is_question_message(message or ''):
            # Имена агентов — русские, с заглавной буквы, ≥3 символа
            _words = re.findall(r'[А-ЯЁ][а-яё]{2,}', message or '')
            # Если нет слов похожих на имена — вопрос без обращения к агенту
            if not _words:
                _skip_director = True
                logger.debug("[AGENT] question without agent name, skipping director")
        if not _skip_director and not _has_explicit_mention(message or ''):
            try:
                _director_response = await _office_director_chat(message, user_id, progress_callback=progress_callback)
            except Exception as _de:
                logger.warning("[DIRECTOR] error, fallback to normal: %s", _de)

        if _director_response is not None:
            # Агент ответил напрямую — ASI молчит (ответ уже в DB)
            if _director_response == "__agent_handled__":
                return {'response': '', 'tool_calls': [], 'tools_used': [], 'agent_info': None, 'agent_handled': True}

            # Распаковываем dict → строка
            if isinstance(_director_response, dict):
                _director_response = _director_response.get('response', '')

            # Пустой ответ (таймаут AI) → fallback
            if not _director_response or not _director_response.strip():
                logger.warning("[DIRECTOR] empty synthesis — falling through to process_request")
                _director_response = None
            else:
                # Очищаем технические детали из ответа директора
                _director_response = _strip_agent_html(_director_response)
                try:
                    from .utils import clean_technical_details as _ctd_dir
                    _cleaned_dir = _ctd_dir(_director_response)
                    if _cleaned_dir and _cleaned_dir.strip():
                        _director_response = _cleaned_dir
                except Exception as _e:
                    logger.debug("suppressed: %s", _e)
                import re as _re_dir
                _director_response = _re_dir.sub(r'\n{2,}', '\n', _director_response)
                _director_response = _re_dir.sub(r'  +', ' ', _director_response).strip()

                return {
                    'response': _director_response,
                    'tool_calls': [],
                    'tools_used': [],
                    'agent_info': None,
                }

        # При fallback из директора — исключаем тяжёлые тулы чтобы не дублировать работу агентов
        _fallback_exclude = exclude_tools or set()
        _fallback_exclude = _fallback_exclude | {
            'research_topic', 'delegate_task', 'start_delegation_campaign',
            'start_content_campaign', 'run_agent_action',
            'web_search', 'quick_topic_search',
        }
        response_text = await agent.process_request(
            message, user_id, context, db_session,
            subscription_tier, progress_callback=progress_callback,
            web_context=web_context, exclude_tools=_fallback_exclude)

        # Очищаем HTML и технические детали из ответа
        if response_text and isinstance(response_text, str):
            response_text = _strip_agent_html(response_text)
            try:
                from .utils import clean_technical_details as _ctd_final
                _cleaned = _ctd_final(response_text)
                if _cleaned and _cleaned.strip():
                    response_text = _cleaned
            except Exception as _e:
                logger.debug("suppressed: %s", _e)
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
            _ag_id = _answered_agent.get('id')
            agent_info = {
                'id': _ag_id,
                'name': _answered_agent.get('name', 'Агент'),
                'job_title': _answered_agent.get('job_title', ''),
                'avatar_url': _avatar or (f'/api/arena/agent_avatar/{_ag_id}' if _ag_id else ''),
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

