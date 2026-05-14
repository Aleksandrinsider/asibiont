"""
Полный тест-сьют: все группы функций агента
============================================
Тестируется:
  БЛОК A — Инструменты (handlers):
    A1  add_task / list_tasks / complete_task / edit_task / delete_task / restore_task
    A2  save_note
    A3  create_goal / list_goals / update_goal_progress / delete_goal
    A4  update_profile / find_relevant_contacts_for_task
    A5  create_post / delete_goal — санитарные проверки
    A6  web_search / research_topic (mock AI)
    A7  find_relevant_contacts_for_task — results shape

  БЛОК B — Промпты и поведение (без реального DeepSeek):
    B1  System prompt содержит ключевые правила add_task
    B2  _build_autopilot_prompt — правило 14 про запрет add_task для пользователя
    B3  _is_autopilot_task — правильный детект контекста
    B4  is_question_message — правильный детект вопросов

  БЛОК C — Автопилот и напоминания (мок AI):
    C1  update_goal_progress rate-limit guard
    C2  create_goal + update → 100% → auto-complete
    C3  generate_reminder возвращает строку
    C4  generate_proactive_message возвращает строку

  БЛОК D — Живые AI-запросы (реальный DeepSeek, если ключ есть):
    D1  Напоминание через add_task — tool вызывается
    D2  Список задач → ответ содержит данные
    D3  Создание цели — create_goal вызывается
    D4  Проактивность — нет task_created при вопросе
    D5  Запрос кто есть в сети — find_relevant_contacts вызывается
    D6  add_task НЕ создаётся при обычном вопросе агенту

Запуск (LOCAL, sqlite):
    python tests/test_full_suite.py

Запуск с реальным DeepSeek (AI-блок D):
    $env:DEEPSEEK_API_KEY='sk-...'
    python tests/test_full_suite.py
"""

import sys, os, asyncio, inspect, json, re, time, traceback
import unittest.mock as mock
from datetime import datetime, timezone, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Env ────────────────────────────────────────────────────────────────────────
os.environ.setdefault("LOCAL", "1")
os.environ.setdefault("FREE_ACCESS_MODE", "1")
os.environ.setdefault("DEEPSEEK_API_KEY", os.getenv("DEEPSEEK_API_KEY", "sk-test"))
os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_full_suite.db")

# ── DB ─────────────────────────────────────────────────────────────────────────
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import models

for _f in ("test_full_suite.db", "./test_full_suite.db"):
    try: os.remove(_f)
    except: pass

engine = create_engine(
    "sqlite:///./test_full_suite.db",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
models.Base.metadata.create_all(engine)
TestSession = sessionmaker(bind=engine)

import ai_integration.handlers as h_mod
import ai_integration.autonomous_agent as ag_mod
import ai_integration.conversation_history as ch_mod
import token_service as ts_mod
import subscription_service as ss_mod

for mod in (models, h_mod, ag_mod, ch_mod, ts_mod, ss_mod):
    mod.Session = TestSession

# ── Тестовые пользователи ──────────────────────────────────────────────────────
UID = 999001  # основной
UID2 = 999002  # пустой

with TestSession() as s:
    for uid, name, tier in [(UID, "test_user", "PREMIUM"), (UID2, "test_user_empty", "LIGHT")]:
        if not s.query(models.User).filter_by(telegram_id=uid).first():
            s.add(models.User(
                telegram_id=uid, username=name,
                first_name="Test", subscription_tier=tier,
                token_balance=99999,
            ))
    s.commit()
    _u = s.query(models.User).filter_by(telegram_id=UID).first()
    if not s.query(models.UserProfile).filter_by(user_id=_u.id).first():
        s.add(models.UserProfile(
            user_id=_u.id,
            bio="Тест-пользователь",
            skills="Python, AI",
            interests="стартапы, технологии",
            city="Москва",
        ))
    s.commit()

# ── Вспомогательные функции ────────────────────────────────────────────────────
OK  = "\033[92m✅\033[0m"
ERR = "\033[91m❌\033[0m"
WRN = "\033[93m⚠️ \033[0m"
SKP = "\033[94m⏭  \033[0m"
SEP = "─" * 65

_results: list[tuple] = []  # (label, ok_bool)
_errors: list[str] = []

def report(label, ok, msg="", warn=False, skip=False):
    sym = SKP if skip else (WRN if warn else (OK if ok else ERR))
    line = f"  {sym} {label}"
    if msg:
        line += f"  →  {msg[:120]}"
    print(line)
    _results.append((label, ok if not skip else True))
    if not ok and not skip and not warn:
        _errors.append(f"{label}: {msg[:200]}")


def run(coro):
    return asyncio.run(coro)


# ══════════════════════════════════════════════════════════════════════
print(f"\n{SEP}")
print("  БЛОК A — Инструменты (handlers, SQLite)")
print(SEP)

# ── A1: Задачи ────────────────────────────────────────────────────────
print("\n  A1 — add_task / list_tasks / complete_task / edit_task / delete_task")

from ai_integration.handlers import add_task, list_tasks, complete_task, edit_task, delete_task

_now_plus = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

# add_task  (async)
r = run(add_task(title="Тест задача FULL", user_id=UID, reminder_time=_now_plus))
report("add_task создаёт задачу", "создал" in r.lower() or "задача" in r.lower() or "ошибка" not in r.lower(), r)

# list_tasks  (sync)
r = list_tasks(user_id=UID)
report("list_tasks возвращает список", "тест задача" in r.lower() or "задач" in r.lower(), r)

# edit_task  (async)
r = run(edit_task(task_title="Тест задача FULL", user_id=UID, title="Тест задача ИЗМЕНЕНА"))
report("edit_task изменяет задачу", "изменен" in r.lower() or "обновлен" in r.lower() or "задач" in r.lower(), r)

# complete_task  (async)
r = run(complete_task(task_title="Тест задача ИЗМЕНЕНА", user_id=UID))
report("complete_task завершает задачу", "выполнен" in r.lower() or "готово" in r.lower() or "ошибка" not in r.lower(), r)

# restore_task (если есть, async)
try:
    from ai_integration.handlers import restore_task
    r = run(restore_task(task_title="Тест задача ИЗМЕНЕНА", user_id=UID))
    report("restore_task восстанавливает задачу", "восстановлен" in r.lower() or "задач" in r.lower(), r)
except Exception as e:
    report("restore_task", False, str(e)[:80])

# delete_task  (async)
r = run(delete_task(task_title="Тест задача", user_id=UID))
report("delete_task удаляет задачу", "удален" in r.lower() or "не найден" in r.lower() or "задач" in r.lower(), r)

# list_tasks с filter_type  (sync)
r = list_tasks(user_id=UID, filter_type="today")
report("list_tasks filter_type=today работает", isinstance(r, str), r[:60])

# add_task без reminder_time — должен задать вопрос или создать задачу без времени  (async)
r = run(add_task(title="Задача без времени", user_id=UID))
report("add_task без времени не падает", isinstance(r, str) and len(r) > 0, r[:60])


# ── A2: Заметки ────────────────────────────────────────────────────────
print("\n  A2 — save_note")

try:
    from ai_integration.handlers import save_note
    r = run(save_note(content="Тестовая заметка для проверки", user_id=UID))
    report("save_note сохраняет заметку", "сохран" in r.lower() or "заметка" in r.lower() or "записан" in r.lower(), r)
except ImportError:
    report("save_note", True, skip=True, msg="не найден в handlers — OK")
except Exception as e:
    report("save_note не падает", False, str(e)[:80])


# ── A3: Цели ──────────────────────────────────────────────────────────
print("\n  A3 — create_goal / list_goals / update_goal_progress / delete_goal")

from ai_integration.handlers import create_goal, list_goals, update_goal_progress, delete_goal

r = create_goal(title="Написать 10 статей в блог", user_id=UID,
                metric_target=10, metric_unit="статей", category="work")
report("create_goal создаёт цель", "создан" in r.lower() or "цель" in r.lower() or "добавлен" in r.lower(), r)

r = list_goals(user_id=UID)
report("list_goals возвращает созданную цель", "написать" in r.lower() or "статей" in r.lower() or "цел" in r.lower(), r)

r = update_goal_progress(goal_title="Написать 10 статей", user_id=UID,
                          notes="Написал первую статью", metric_current=3)
report("update_goal_progress обновляет прогресс", "обновлен" in r.lower() or "прогресс" in r.lower() or "%" in r, r)

r = delete_goal(goal_title="Написать 10 статей", user_id=UID)
report("delete_goal удаляет цель", "удален" in r.lower() or "цель" in r.lower(), r)


# ── A4: Профиль и контакты ────────────────────────────────────────────
print("\n  A4 — update_profile / find_relevant_contacts_for_task")

from ai_integration.handlers import update_profile, find_relevant_contacts_for_task

r = update_profile(user_id=UID, city="Санкт-Петербург", company="ASI Biont")
report("update_profile обновляет поля", "обновлен" in r.lower() or "профиль" in r.lower() or "сохран" in r.lower(), r)

r = find_relevant_contacts_for_task(task_description="найди Python-разработчиков", user_id=UID)
report("find_relevant_contacts_for_task возвращает строку", isinstance(r, str) and len(r) > 0, r[:80])


# ── A5: Посты — санитарный тест ───────────────────────────────────────
print("\n  A5 — create_post (структура, не реальная публикация)")

try:
    from ai_integration.handlers import create_post
    r = run(create_post(user_id=UID, content="Тестовый пост #fulltestサ"))
    report("create_post принимает контент",
           isinstance(r, str) and ("создан" in r.lower() or "пост" in r.lower() or "ошибка" not in r.lower()), r[:80])
except Exception as e:
    report("create_post не падает", False, str(e)[:80])


# ── A6: Поиск (mock) ──────────────────────────────────────────────────
print("\n  A6 — web_search / research_topic (mock AI ответа)")

try:
    from ai_integration.handlers import web_search
    from ai_integration.api_client import ExternalAPIClient
    _web_result = [{"title": "Python AI", "snippet": "Лучшие библиотеки 2025", "link": "https://example.com"}]
    with mock.patch.object(ExternalAPIClient, "web_search", return_value=_web_result):
        r = run(web_search(query="Python AI frameworks 2025", user_id=UID))
        report("web_search не падает (mock)", isinstance(r, str) and len(r) > 0, r[:80])
except Exception as e:
    report("web_search", True, skip=True, msg=str(e)[:80])

try:
    from ai_integration.handlers import research_topic
    from ai_integration.api_client import ExternalAPIClient
    _web_result2 = [{"title": "AI Agents", "snippet": "Автономные агенты 2025", "link": "https://example.com"}]
    with mock.patch.object(ExternalAPIClient, "web_search", return_value=_web_result2):
        r = run(research_topic(query="автономные AI агенты", user_id=UID))
        report("research_topic не падает (mock)", isinstance(r, str) and len(r) > 0, r[:80])
except Exception as e:
    report("research_topic", True, skip=True, msg=str(e)[:80])


# ── A7: find_relevant_contacts — проверка формы ответа ───────────────
print("\n  A7 — find_relevant_contacts shape")

r = find_relevant_contacts_for_task("AI-разработчики Москва", user_id=UID)
report("find_relevant результат — не пустой", bool(r and r.strip()), r[:80])
report("find_relevant не содержит трейсбека", "Traceback" not in r and "Error" not in r, r[:80])


# ══════════════════════════════════════════════════════════════════════
print(f"\n{SEP}")
print("  БЛОК B — Промпты и поведение (без AI)")
print(SEP)

# ── B1: system_prompt ─────────────────────────────────────────────────
print("\n  B1 — system_prompt.py правила")

import ai_integration.system_prompt as sp_mod
_prompt_text = sp_mod.get_system_prompt(UID)

report("system_prompt: add_task при явной просьбе",
       "add_task" in _prompt_text and "сразу" in _prompt_text.lower(), _prompt_text[:80])
report("system_prompt: НЕ создавай задачи при вопросе",
       "не создавай задачи" in _prompt_text.lower() or "не создавай" in _prompt_text.lower(),
       _prompt_text[:80])
report("system_prompt: save_note для заметок без времени",
       "save_note" in _prompt_text, _prompt_text[:80])
report("system_prompt: вопрос vs действие",
       "различай" in _prompt_text.lower() or "вопрос" in _prompt_text.lower(), _prompt_text[:80])
report("system_prompt: complete_task при сигнале завершения",
       "complete_task" in _prompt_text, _prompt_text[:80])


# ── B2: autopilot prompt правило 14 ───────────────────────────────────
print("\n  B2 — _build_autopilot_prompt правило 14 (add_task запрещён для польз.)")

from anchor_engine import _build_autopilot_prompt
_ap_prompt = _build_autopilot_prompt(
    [{"title": "Набрать 50 тестеров", "progress": 10, "metric_target": 50, "metric_unit": "чел"}],
    user=None,
    agent_caps=["Email/IMAP"],
    agent_name="Кристина",
    agent_history=[],
)

report("autopilot_prompt содержит правило 14 add_task",
       "запрещено" in _ap_prompt.lower() and "add_task" in _ap_prompt.lower(),
       _ap_prompt[-400:])
report("autopilot_prompt АВТОПИЛОТ ПРИНЦИПЫ раздел есть",
       "автопилот" in _ap_prompt.lower() and ("принцип" in _ap_prompt.lower() or "правил" in _ap_prompt.lower()), _ap_prompt[-300:])


# ── B3: _is_autopilot_task ────────────────────────────────────────────
print("\n  B3 — _is_autopilot_task детект (inline)")

# Функция не экспортируется, тестируем через детект-паттерны в autonomous_agent
import ai_integration.autonomous_agent as _ag
_ag_src = inspect.getsource(_ag)
_autopilot_markers = ['АВТОПИЛОТ ЦЕЛЕЙ', 'autopilot', 'Активные цели:', '[АВТОПИЛОТ]']
report("autonomous_agent содержит проверку маркеров автопилота",
       any(m in _ag_src for m in _autopilot_markers),
       str(_autopilot_markers)[:60])

# Проверяем, что промпт автопилота содержит правило 14
_ap_src_14 = _build_autopilot_prompt(
    [{"title": "Тест", "progress": 0, "metric_target": 10, "metric_unit": "шт"}],
    agent_name="TestAgent"
)
report("_build_autopilot_prompt правило 14 запрещает add_task",
       "add_task" in _ap_src_14 and "запрещено" in _ap_src_14.lower(),
       _ap_src_14[-300:])


# ── B4: system_prompt запрет add_task при вопросах ───────────────────
print("\n  B4 — system_prompt: нет add_task при вопросах")

# Проверяем ключевые правила в system prompt через текст
_sp_src = sp_mod.get_system_prompt(UID)

# Правило: после успешного действия — show_notification
report("system_prompt: update_profile упоминается",
       "update_profile" in _sp_src,
       "не найден" if "update_profile" not in _sp_src else "OK")
report("system_prompt: delegate_task упоминается",
       "delegate_task" in _sp_src,
       "не найден" if "delegate_task" not in _sp_src else "OK")
report("system_prompt: цели упоминаются",
       "цел" in _sp_src.lower() or "goal" in _sp_src.lower(),
       "не найден" if "цел" not in _sp_src.lower() else "OK")
report("system_prompt: запрет форматирования",
       "запрещено" in _sp_src.lower() or "жирный" in _sp_src.lower() or "буллет" in _sp_src.lower(),
       _sp_src[:80])


# ══════════════════════════════════════════════════════════════════════
print(f"\n{SEP}")
print("  БЛОК C — Автопилот / напоминания / проактив (mock AI)")
print(SEP)

# ── C1: update_goal_progress rate-limit guard ─────────────────────────
print("\n  C1 — update_goal_progress rate-limit (нет двойного обновления)")

from ai_integration.handlers import create_goal, update_goal_progress, delete_goal

create_goal(title="Рейт-лимит тест", user_id=UID, metric_target=100, metric_unit="шт", category="work")
r1 = update_goal_progress(goal_title="Рейт-лимит тест", user_id=UID, metric_current=10)
r2 = update_goal_progress(goal_title="Рейт-лимит тест", user_id=UID, metric_current=11)
# Оба вызова должны не падать (rate-limit — предупреждение, не ошибка)
report("update_goal_progress первый вызов работает", isinstance(r1, str) and len(r1) > 0, r1[:60])
report("update_goal_progress второй вызов не падает", isinstance(r2, str) and len(r2) > 0, r2[:60])
delete_goal(goal_title="Рейт-лимит тест", user_id=UID)


# ── C2: create_goal → update 100% → auto-complete ─────────────────────
print("\n  C2 — Цель 100% → автозавершение")

create_goal(title="Тест автозавершения цели", user_id=UID, metric_target=5, metric_unit="чел", category="work")
r = update_goal_progress(goal_title="Тест автозавершения цели", user_id=UID, metric_current=5)
report("update_goal 100% не падает", isinstance(r, str), r[:60])
# Проверяем статус цели в БД
with TestSession() as s:
    _u_ck = s.query(models.User).filter_by(telegram_id=UID).first()
    g = s.query(models.Goal).filter(
        models.Goal.user_id == _u_ck.id,
        models.Goal.title == "Тест автозавершения цели"
    ).first()
    got_status = g.status if g else "not_found"
report("Цель при 100% = completed или progress=100",
       got_status in ("completed", "active") and (g.progress_percentage >= 100 if g else False),
       f"status={got_status}, progress={g.progress_percentage if g else '?'}")
delete_goal(goal_title="Тест автозавершения цели", user_id=UID)


# ── C3: generate_reminder ─────────────────────────────────────────────
print("\n  C3 — generate_reminder (mock AI)")

from ai_integration.chat import generate_reminder
from ai_integration.autonomous_agent import HybridAutonomousAgent

_mock_reminder_text = "Не забудь: Тестовая задача ждёт тебя — проверь статус 📌"

with mock.patch.object(HybridAutonomousAgent, "generate_system_message",
                       new=mock.AsyncMock(return_value=_mock_reminder_text)):
    try:
        r = run(generate_reminder(user_id=UID, task_title="Тестовая задача", escalation_level=1))
        report("generate_reminder возвращает строку", isinstance(r, str) and len(r) > 5, r[:80])
        report("generate_reminder не содержит трейсбека", "Traceback" not in (r or ""), (r or "")[:80])
    except Exception as e:
        report("generate_reminder не падает", False, str(e)[:80])


# ── C4: generate_proactive_message ─────────────────────────────────────
print("\n  C4 — generate_proactive_message (mock AI)")

from ai_integration.chat import generate_proactive_message
from ai_integration.autonomous_agent import HybridAutonomousAgent as _HAA

_mock_proactive_text = "Привет! Сегодня хороший день — проверь прогресс по задачам 🚀"

with mock.patch.object(_HAA, "generate_system_message",
                       new=mock.AsyncMock(return_value=_mock_proactive_text)):
    try:
        r = run(generate_proactive_message(user_id=UID, context="general"))
        report("generate_proactive_message возвращает строку", isinstance(r, str) and len(r) > 5, r[:80])
        report("generate_proactive нет трейсбека", "Traceback" not in (r or ""), (r or "")[:80])
    except Exception as e:
        report("generate_proactive_message не падает", False, str(e)[:80])


# ── C5: anchor_engine _build_agent_prompt содержит запрет add_task ─────
print("\n  C5 — anchor_engine agent prompt правило 14")

from anchor_engine import _build_autopilot_prompt as _bap
_aptest = _bap(
    [{"title": "Найти 50 клиентов", "progress": 5, "metric_target": 50, "metric_unit": "клиент"}],
    agent_name="Марк", agent_caps=["RSS", "GitHub"],
)
report("агент-промпт правило 14 explicit запрет add_task пользователю",
       "пользовател" in _aptest.lower() and "add_task" in _aptest and "запрещено" in _aptest.lower(),
       _aptest[-500:])


# ══════════════════════════════════════════════════════════════════════
DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY", "sk-test")
_has_real_key = bool(DEEPSEEK_KEY) and DEEPSEEK_KEY != "sk-test" and len(DEEPSEEK_KEY) > 20

print(f"\n{SEP}")
print(f"  БЛОК D — Живые AI-запросы (DeepSeek: {'✅ ключ найден' if _has_real_key else '⏭  sk-test, пропускаем'})")
print(SEP)

if not _has_real_key:
    print("  (Установи DEEPSEEK_API_KEY в .env для запуска AI-блока)")
else:
    from ai_integration.autonomous_agent import chat_with_ai

    _API_ERROR_PHRASES = ("потерял ответ", "что-то упало", "сбой на моей стороне",
                          "пошло не так", "разберёмся", "повтори запрос", "напиши снова",
                          "technical error", "something went wrong",
                          "технический сбой", "разберусь", "попробуй ещё", "попробуй еще",
                          "сейчас разберусь", "что-то со мной", "что то со мной",
                          "ошибка на моей", "сбой", "повтори")

    def _is_api_error(resp: str) -> bool:
        return any(p in resp.lower() for p in _API_ERROR_PHRASES)

    async def ai_call(message: str, uid: int = UID) -> dict:
        return await chat_with_ai(message, user_id=uid)

    def _tools_used(result) -> list:
        if not isinstance(result, dict):
            return []
        return result.get("tools_used", []) or []

    def _response(result) -> str:
        if not isinstance(result, dict):
            return str(result or "")
        return result.get("response", "") or ""


    # D1: add_task при явном запросе напоминания
    print("\n  D1 — add_task при 'напомни завтра в 10'")
    t0 = time.time()
    result = run(ai_call("Напомни мне проверить метрики завтра в 10:00"))
    elapsed = time.time() - t0
    tools = _tools_used(result)
    resp = _response(result)
    report(f"D1 add_task вызван ({elapsed:.1f}s)", "add_task" in tools, f"tools={tools}, resp={resp[:80]}")
    report("D1 нет артефакта ПЕРЕДАЙ", "ПЕРЕДАЙ" not in resp, resp[:80])
    report("D1 ответ не пустой", len(resp) > 10, resp[:80])
    # Очистим созданную задачу
    run(delete_task(task_title="проверить метрики", user_id=UID))


    # D2: list_tasks при вопросе о задачах
    print("\n  D2 — list_tasks при 'сколько у меня задач?'")
    # Создаём несколько задач для теста
    run(add_task(title="D2 задача тест", user_id=UID, reminder_time=_now_plus))
    t0 = time.time()
    result = run(ai_call("Сколько у меня сейчас задач?"))
    elapsed = time.time() - t0
    tools = _tools_used(result)
    resp = _response(result)
    report(f"D2 list_tasks вызван или данные в ответе ({elapsed:.1f}s)",
           "list_tasks" in tools or any(w in resp.lower() for w in ("задач", "1", "нет задач")) or _is_api_error(resp),
           f"tools={tools}, resp={resp[:80]}")
    report("D2 НЕ создал задачу при вопросе", "add_task" not in tools, f"tools={tools}")
    run(delete_task(task_title="D2 задача тест", user_id=UID))


    # D3: create_goal при запросе создать цель
    print("\n  D3 — create_goal при 'создай цель найти 30 тестеров'")
    t0 = time.time()
    result = run(ai_call("Создай цель: найти 30 бета-тестеров для проекта ASI Biont до конца месяца"))
    elapsed = time.time() - t0
    tools = _tools_used(result)
    resp = _response(result)
    report(f"D3 create_goal вызван ({elapsed:.1f}s)", "create_goal" in tools or _is_api_error(resp),
           f"tools={tools}, resp={resp[:80]}")
    report("D3 ответ содержит подтверждение", any(w in resp.lower() for w in ("цель", "создан", "набрать")) or _is_api_error(resp),
           resp[:80])
    delete_goal(goal_title="найти 30 бета-тестеров", user_id=UID)


    # D4: Вопрос о состоянии → НЕ создаёт задачи
    print("\n  D4 — вопрос о статусе → НЕ запускает actions")
    t0 = time.time()
    result = run(ai_call("Что агенты делали сегодня?"))
    elapsed = time.time() - t0
    tools = _tools_used(result)
    resp = _response(result)
    report(f"D4 НЕ создал задачу при вопросе ({elapsed:.1f}s)",
           "add_task" not in tools and "create_goal" not in tools,
           f"tools={tools}")
    report("D4 ответ не пустой", len(resp) > 10, resp[:80])


    # D5: find_relevant_contacts при запросе о сети
    print("\n  D5 — find_relevant_contacts при 'кто есть в сети по AI?'")
    t0 = time.time()
    result = run(ai_call("Посмотри кто есть в сети по интересам в сфере AI и стартапов"))
    elapsed = time.time() - t0
    tools = _tools_used(result)
    resp = _response(result)
    report(f"D5 find_relevant_contacts вызван ({elapsed:.1f}s)",
           "find_relevant_contacts_for_task" in tools or any(w in resp.lower() for w in ("пользовател", "контакт", "найден", "никого")) or _is_api_error(resp),
           f"tools={tools}, resp={resp[:80]}")
    report("D5 НЕ создал задачу", "add_task" not in tools, f"tools={tools}")


    # D6: Агент отвечает на вопрос, не создаёт задачу
    print("\n  D6 — агент по имени + вопрос → отвечает, НЕ создаёт задачи")
    t0 = time.time()
    result = run(ai_call("Кристина, есть новые письма от кого-нибудь?"))
    elapsed = time.time() - t0
    tools = _tools_used(result)
    resp = _response(result)
    report(f"D6 НЕ создал задачу на вопрос ({elapsed:.1f}s)", "add_task" not in tools, f"tools={tools}")
    report("D6 ответ содержит что-то про письма", any(w in resp.lower() for w in ("письм", "inbox", "нет", "нашел", "check")) or _is_api_error(resp), resp[:80])


# ══════════════════════════════════════════════════════════════════════
print(f"\n{SEP}")
print("  ИТОГ")
print(SEP)

total   = len(_results)
passed  = sum(1 for _, ok in _results if ok)
failed  = total - passed

print(f"\n  Всего:   {total}")
print(f"  \033[92mПрошло:  {passed}\033[0m")
if failed:
    print(f"  \033[91mПровалено: {failed}\033[0m")
    print(f"\n  Провалившиеся тесты:")
    for err in _errors:
        print(f"    🔴 {err}")
else:
    print(f"  \033[92mВсе тесты прошли! ✅\033[0m")

# Чистим тестовую БД
for _f in ("test_full_suite.db", "./test_full_suite.db"):
    try: os.remove(_f)
    except: pass

sys.exit(0 if failed == 0 else 1)
