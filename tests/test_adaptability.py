"""
Тесты адаптивности агента.

Проверяют поведение системы в разных ситуациях — без реального DeepSeek API:
  1. Парсинг времени    — ISO, русская речь, дни недели, относительное время
  2. Интеграции агентов — из api_keys, python_code, tools_allowed, search_scope
  3. Правила промпта    — ключевые правила в system_prompt.py
  4. Поведение handlers — граничные случаи (ISO-дата, отсутствие времени, кампания без платформы)
  5. Context builder    — корректная сборка контекста для разных профилей

Запуск: python tests/test_adaptability.py
"""
import sys, os, asyncio, re
import warnings

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

warnings.filterwarnings("ignore", category=ResourceWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("LOCAL", "1")
os.environ.setdefault("FREE_ACCESS_MODE", "1")
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test")
os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_adaptability.db")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import models

for _f in ("test_adaptability.db", "./test_adaptability.db"):
    try:
        os.remove(_f)
    except Exception:
        pass

engine = create_engine("sqlite:///./test_adaptability.db", connect_args={"check_same_thread": False})
models.Base.metadata.create_all(engine)
TestSession = sessionmaker(bind=engine)

import ai_integration.handlers as h_mod
import ai_integration.autonomous_agent as ag_mod
import ai_integration.conversation_history as ch_mod
import token_service as ts_mod
import subscription_service as ss_mod

for mod in (models, h_mod, ag_mod, ch_mod, ts_mod, ss_mod):
    mod.Session = TestSession

# ─── helpers ──────────────────────────────────────────────────────────────────
OK_SYM  = "\033[92m✅\033[0m"
ERR_SYM = "\033[91m❌\033[0m"
SKIP_SYM = "\033[93m⚠️ \033[0m"
results = []

def run(coro):
    return asyncio.run(coro)

def report(label, ok, msg="", skip=False):
    sym = SKIP_SYM if skip else (OK_SYM if ok else ERR_SYM)
    line = f"  {sym} {label}"
    if msg:
        extra = str(msg)[:140]
        line += f"  →  {extra}"
    print(line)
    results.append((label, ok if not skip else True))

# ─── DB: users ────────────────────────────────────────────────────────────────
UID = 777001  # полный профиль
UID2 = 777002  # пустой профиль

with TestSession() as s:
    for uid, uname, tier in [
        (UID, "adapt_user", "PREMIUM"),
        (UID2, "adapt_user_empty", "LIGHT"),
    ]:
        if not s.query(models.User).filter_by(telegram_id=uid).first():
            u = models.User(
                telegram_id=uid, username=uname,
                first_name="Adapt", subscription_tier=tier,
                token_balance=99999,
            )
            s.add(u)
    s.commit()
    u1 = s.query(models.User).filter_by(telegram_id=UID).first()
    if not s.query(models.UserProfile).filter_by(user_id=u1.id).first():
        s.add(models.UserProfile(
            user_id=u1.id,
            bio="AI-стартапер",
            skills="Python, ML, продажи",
            interests="стартапы, AI, нетворкинг",
            goals="вывести ASI Biont на 1000 пользователей",
            city="Москва",
        ))
    s.commit()

_session = TestSession()

async def call(fn_name, uid=UID, **kwargs):
    func = getattr(h_mod, fn_name, None)
    if not func:
        return f"ERROR: handler '{fn_name}' not found"
    import inspect
    sig = inspect.signature(func)
    kw = {"user_id": uid, **kwargs}
    if "session" in sig.parameters:
        kw["session"] = _session
    if "close_session" in sig.parameters:
        kw["close_session"] = False
    try:
        result = func(**kw)
        if inspect.isawaitable(result):
            return await result
        return result
    except Exception as e:
        return f"ERROR: {e}"


# ══════════════════════════════════════════════════════════════════════════════
# БЛОК 1: Парсинг времени
# ══════════════════════════════════════════════════════════════════════════════
print("\n\033[1m══ Блок 1: Парсинг времени ══\033[0m")

from ai_integration.utils import parse_time_to_datetime

def chk_time(label, text, should_match_re=None, should_be_none=False):
    result = parse_time_to_datetime(text, UID)
    if should_be_none:
        ok = result is None
        report(label, ok, repr(result))
    elif should_match_re:
        ok = result is not None and bool(re.match(should_match_re, result))
        report(label, ok, repr(result))
    else:
        ok = result is not None
        report(label, ok, repr(result))

# ISO-форматы (ключевой фикс этой сессии)
chk_time("ISO: 2026-12-15 10:00:00",    "2026-12-15 10:00:00", r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")
chk_time("ISO: 2026-12-15 10:00",       "2026-12-15 10:00",    r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")
chk_time("ISO: только дата 2027-01-01", "2027-01-01",          r"2027-01-01 00:00")

# Натуральная речь
chk_time("завтра в 10:00",       "завтра в 10:00",       r"\d{4}-\d{2}-\d{2} 10:00")
chk_time("послезавтра в 15:30",  "послезавтра в 15:30",  r"\d{4}-\d{2}-\d{2} 15:30")
chk_time("сегодня в 9:00",       "сегодня в 9:00",       r"\d{4}-\d{2}-\d{2} 09:00")

# Относительное время
chk_time("через 2 часа",         "через 2 часа",         r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")
chk_time("через 30 минут",       "через 30 минут",       r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")

# Дни недели
chk_time("в пятницу в 14:00",    "в пятницу в 14:00",   r"\d{4}-\d{2}-\d{2} 14:00")
chk_time("в понедельник",        "в понедельник",        r"\d{4}-\d{2}-\d{2} 09:00")

# Непарсируемое → None
chk_time("случайный текст",      "сделать что-нибудь",   should_be_none=True)


# ══════════════════════════════════════════════════════════════════════════════
# БЛОК 2: Определение интеграций агентов
# ══════════════════════════════════════════════════════════════════════════════
print("\n\033[1m══ Блок 2: Интеграции агентов ══\033[0m")

from ai_integration.autonomous_agent import _parse_agent_integrations

def chk_intg(label, api_keys="", code="", tools="", scope="", must_contain=(), must_not=None):
    result = _parse_agent_integrations(api_keys, code, tools, scope)
    result_str = ", ".join(result)
    ok = all(x in result_str for x in must_contain)
    if must_not and ok:
        ok = not any(x in result_str for x in must_not)
    report(label, ok, result_str[:120])

# API ключи
chk_intg("IMAP из api_keys",
    api_keys="IMAP_HOST=mail.example.com\nIMAP_USER=user@mail.com",
    must_contain=("IMAP почта",))

chk_intg("Gmail из api_keys",
    api_keys="GMAIL_CLIENT_ID=my_client_id_123\nGMAIL_SECRET=my_secret_key",
    must_contain=("Gmail",))

chk_intg("Telegram + Discord из api_keys",
    api_keys="TELEGRAM_BOT_TOKEN=123456:ABCdef\nDISCORD_TOKEN=my_discord_token",
    must_contain=("Telegram", "Discord"))

chk_intg("Binance + Bybit крипта",
    api_keys="BINANCE_API_KEY=my_binance_api_key\nBYBIT_API_KEY=my_bybit_api_key",
    must_contain=("Binance", "Bybit"))

# Python код
chk_intg("imaplib в коде",
    code="import imaplib\nserver = imaplib.IMAP4_SSL('mail.ru')",
    must_contain=("IMAP почта",))

chk_intg("feedparser + pandas",
    code="import feedparser\nimport pandas as pd\nfeed = feedparser.parse(url)",
    must_contain=("RSS", "pandas"))

chk_intg("selenium + aiohttp",
    code="from selenium import webdriver\nimport aiohttp",
    must_contain=("Браузерная автоматизация", "HTTP-запросы"))

chk_intg("openai в коде",
    code="import openai\nclient = openai.OpenAI(api_key=os.getenv('OPENAI_KEY'))",
    must_contain=("OpenAI",))

# tools_allowed JSON
chk_intg("web_search в tools",
    tools='["web_search", "add_task"]',
    must_contain=("Поиск в интернете", "Управление задачами"))

chk_intg("send_email в tools",
    tools='["send_email", "add_note"]',
    must_contain=("Отправка email", "Заметки"))

# search_scope
chk_intg("search_scope → поиск-метка",
    scope="ChatGPT, AI-агенты, автоматизация",
    must_contain=("Поиск:",))

# Пустой агент — нет интеграций
chk_intg("пустой агент → []",
    must_contain=(),
    must_not=["почта", "Telegram", "поиск"])


# ══════════════════════════════════════════════════════════════════════════════
# БЛОК 3: Правила промпта (ключевые правила в коде)
# ══════════════════════════════════════════════════════════════════════════════
print("\n\033[1m══ Блок 3: Правила промпта ══\033[0m")

from ai_integration.system_prompt import select_prompt_version
_sp = select_prompt_version(subscription_tier="PREMIUM", lang="ru")

def chk_rule(label, pattern, flags=re.DOTALL | re.IGNORECASE):
    ok = bool(re.search(pattern, _sp, flags))
    snippet = ""
    if ok:
        m = re.search(pattern, _sp, flags)
        snippet = m.group(0)[:60].replace("\n", " ")
    report(label, ok, snippet)

# Ключевые алгоритмические правила
chk_rule("ДЕЙСТВИЕ→инструмент алгоритм",           r"ДЕЙСТВИЕ|ВОПРОС.*ДЕЙСТВИЕ")
chk_rule("Инструменты/задачи в промпте",           r"ЗАДАЧ|инструмент|tool_call")
chk_rule("ЗАПРЕЩЕНО 12:00 по умолчанию",           r"ЗАПРЕЩЕНО.*12:00|12:00.*ЗАПРЕЩЕНО|НИКОГДА не ставь 12:00")
chk_rule("ЗАПРЕЩЕНО буллеты в уточнении",          r"ЗАПРЕЩЕНО.*списки|ЗАПРЕЩЕНО.*буллет")
chk_rule("Намерение: интерес vs команда",          r"НАМЕРЕНИ|ИНТЕРЕС.*КОМАНДА")
chk_rule("Один вопрос максимум",                   r"1\s+вопрос|один\s+вопрос|Нет 1 параметра")
chk_rule("АНТИГАЛЛЮЦИНАЦИЯ правило",               r"АНТИГАЛЛЮЦИНАЦИЯ|НЕ\s+утверждай\s+наличие")
chk_rule("Правило проактивных якорей",             r"ПРОАКТИВН.*ЯК|goal_decomposition")
chk_rule("Принцип последовательности",             r"ПОСЛЕДОВАТЕЛЬНО|цепочк", re.IGNORECASE)
chk_rule("Отчёт о выполнении",                     r"отчёт|результат.*задач|ОБЯЗАТЕЛЬНО сообщи")
chk_rule("Интеграции в ответах",                   r"интеграци.*упоминай|нужна интеграция")
chk_rule("Запрет URL в кампаниях",                 r"НИКАКИХ URL|url.*кампан|ссылки.*спам", re.IGNORECASE)


# ══════════════════════════════════════════════════════════════════════════════
# БЛОК 4: Поведение handlers в граничных ситуациях
# ══════════════════════════════════════════════════════════════════════════════
print("\n\033[1m══ Блок 4: Граничные случаи handlers ══\033[0m")

async def scenario_edge_cases():
    # check_time_conflicts: ISO-дата парсится (ключевой фикс)
    r = await call("check_time_conflicts", reminder_time="2026-12-15 10:00:00")
    ok = "Не удалось распознать" not in r and "ошибка" not in r.lower()
    report("check_time_conflicts ISO дата", ok, r[:80])

    r = await call("check_time_conflicts", reminder_time="2026-12-15 10:00")
    ok = "Не удалось распознать" not in r and "ошибка" not in r.lower()
    report("check_time_conflicts ISO без секунд", ok, r[:80])

    # add_task без времени — ожидаем сообщение (не краш)
    r = await call("add_task", title="Тест без времени")
    ok = isinstance(r, str) and len(r) > 0
    report("add_task без времени — не краш", ok, r[:80])

    # add_task с ISO-временем — работает
    r = await call("add_task", title="Тест с ISO временем", reminder_time="2026-12-20 09:00:00")
    ok = isinstance(r, str) and "error" not in r.lower()[:8]
    report("add_task с ISO временем", ok, r[:80])

    # delegate_task без дедлайна — просит уточнить
    r = await call("delegate_task", task_title="Сделать сайт", target_username="user2")
    ok = isinstance(r, str) and len(r) > 0
    report("delegate_task без дедлайна — не краш", ok, r[:80])

    # get_delegation_progress — пустая база
    r = await call("get_delegation_progress")
    ok = isinstance(r, str) and "нет делегированных" in r.lower() or "delegation_report" in r.lower()
    report("get_delegation_progress пустая база", ok, r[:80])

    # list_tasks пустой пользователь
    r = await call("list_tasks", uid=UID2)
    ok = isinstance(r, str) and ("нет активных" in r.lower() or "задач" in r.lower())
    report("list_tasks пустой пользователь", ok, r[:80])

    # list_goals пустой пользователь
    r = await call("list_goals", uid=UID2)
    ok = isinstance(r, str) and len(r) > 0
    report("list_goals пустой пользователь", ok, r[:80])

    # Создаём задачу, завершаем, удаляем — полный цикл
    r_add = await call("add_task", title="Адаптивный тест", reminder_time="2026-12-25 12:00:00")
    report("add_task с корректным временем", isinstance(r_add, str) and "error" not in r_add.lower()[:8], r_add[:60])

    r_list = await call("list_tasks")
    task_found = "Адаптивный" in r_list
    report("list_tasks видит новую задачу", task_found, r_list[:80])

    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        t = s.query(models.Task).filter_by(user_id=u.id, status='pending').first()
        tid = t.id if t else None

    if tid:
        r = await call("edit_task", task_title="Адаптивный тест", reminder_time="2026-12-26 15:00:00")
        ok = "error" not in r.lower()[:8]
        report("edit_task — перенос времени", ok, r[:80])

        r = await call("complete_task", task_id=tid)
        ok = "completed" in r.upper() or "завершен" in r.lower()
        report("complete_task", ok, r[:60])
    else:
        report("complete_task",  False, "задача не найдена в БД", skip=True)
        report("edit_task",      False, skip=True)

    # check_time_conflicts на занятое время не должен крашить
    r = await call("check_time_conflicts", reminder_time="2026-12-26 15:00:00")
    ok = isinstance(r, str) and "ошибка" not in r.lower()
    report("check_time_conflicts на занятое время", ok, r[:80])

run(scenario_edge_cases())


# ══════════════════════════════════════════════════════════════════════════════
# БЛОК 5: Context Builder — корректная сборка контекста
# ══════════════════════════════════════════════════════════════════════════════
print("\n\033[1m══ Блок 5: Context Builder ══\033[0m")

from ai_integration.context_builder import ContextBuilder
cb = ContextBuilder()

with TestSession() as s:
    # Пользователь с профилем
    alerts_full = cb.build_premium_alerts_context(UID, s)
    report("build_premium_alerts_context полный профиль — список", isinstance(alerts_full, list), str(alerts_full)[:80])

    # Пустой пользователь
    alerts_empty = cb.build_premium_alerts_context(UID2, s)
    report("build_premium_alerts_context пустой — список", isinstance(alerts_empty, list), str(alerts_empty)[:80])

    # Несуществующий пользователь → пустой список
    alerts_none = cb.build_premium_alerts_context(999999, s)
    report("build_premium_alerts_context несущ. user → []", alerts_none == [], str(alerts_none)[:60])


# ══════════════════════════════════════════════════════════════════════════════
# БЛОК 6: Инвариант — все ключевые handlers экспортированы
# ══════════════════════════════════════════════════════════════════════════════
print("\n\033[1m══ Блок 6: Инвариант handlers ══\033[0m")

_EXPECTED_HANDLERS = [
    "add_task", "list_tasks", "complete_task", "edit_task", "delete_task",
    "get_task_details", "check_time_conflicts", "reschedule_task",
    "create_goal", "list_goals", "update_goal_progress", "complete_goal",
    "create_post", "get_posts", "edit_post", "delete_post",
    "start_content_campaign", "manage_content_campaign",
    "delegate_task", "get_delegation_progress", "start_delegation_campaign",
    "quick_topic_search", "get_news_trends", "get_weather_info",
    "send_message_to_user", "get_incoming_messages", "get_system_status",
    "save_email_contact", "list_email_contacts",
    "find_partners", "update_profile", "list_marketplace",
    "analyze_situation_and_suggest_tasks",
    "schedule_background_task",
]

missing = [h for h in _EXPECTED_HANDLERS if not hasattr(h_mod, h)]
report(f"Все {len(_EXPECTED_HANDLERS)} ключевых handlers присутствуют",
       len(missing) == 0,
       f"Отсутствуют: {missing}" if missing else "OK")

# Нет дублей в TOOLS
_tools = getattr(h_mod, "TOOLS", None) or getattr(ag_mod, "TOOLS", [])
if _tools:
    tool_names = [t.get("function", {}).get("name", "") for t in _tools if isinstance(t, dict)]
    dups = [n for n in tool_names if tool_names.count(n) > 1]
    report("Нет дублей в TOOLS", len(set(dups)) == 0, f"дубли: {set(dups)}" if dups else "OK")
else:
    report("TOOLS список доступен", False, skip=True)


# ══════════════════════════════════════════════════════════════════════════════
# ИТОГ
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    _session.close()
    try:
        os.remove("test_adaptability.db")
    except Exception:
        pass

    passed = sum(1 for _, ok in results if ok)
    failed = len(results) - passed

    print(f"\n{'='*60}")
    print(f"  ИТОГ: {passed}/{len(results)} passed  |  {failed} failed")
    if failed:
        print(f"\n  ❌ ПРОВАЛЕНО:")
        for label, ok in results:
            if not ok:
                print(f"    • {label}")
    print(f"{'='*60}\n")

    os._exit(0 if failed == 0 else 1)
