"""
Тесты адаптивного роутинга (_office_director_chat, action='adaptive').
8 сценариев: базовый поток, ранний финализ, ПЕРЕДАЮ-stripping,
max-steps guard, агент не найден, mission brief anchor,
первый director_message, обрезка контекста.

Запуск: python tests/test_adaptive_routing.py
"""

import sys, os, asyncio, warnings, re as _re, json as _json
warnings.filterwarnings('ignore')
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("LOCAL", "1")
os.environ.setdefault("FREE_ACCESS_MODE", "1")
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test")
os.environ.setdefault("BOT_TOKEN", "123456:TEST")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import models
import ai_integration.autonomous_agent as ag_mod

# ── in-memory DB ──────────────────────────────────────────────────────────────
engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
models.Base.metadata.create_all(engine)
TestSession = sessionmaker(bind=engine)

import ai_integration.conversation_history as ch_mod
import token_service as ts_mod
for mod in (models, ag_mod, ch_mod, ts_mod):
    mod.Session = TestSession

import datetime

with TestSession() as s:
    u = models.User(telegram_id=777001, username="adp_test", first_name="Test",
                    subscription_tier="PREMIUM", token_balance=99999,
                    created_at=datetime.datetime.utcnow())
    s.add(u)
    s.flush()
    s.add(models.UserProfile(user_id=u.id, bio="Тест", skills="Python",
                             interests="AI", goals="тест"))
    for name, desc in [
        ("Аналитик",   "Анализирует данные и рынки"),
        ("Маркетолог", "Продвигает продукты, пишет стратегии"),
        ("Разработчик","Пишет код и автоматизирует задачи"),
        ("Дизайнер",   "Создаёт визуальные материалы и UX"),
        ("Копирайтер", "Пишет тексты и контент-план"),
    ]:
        s.add(models.UserAgent(author_id=u.id, name=name, description=desc,
                               tools_allowed='["web_search"]', status="active",
                               personality=f"Специалист: {name}",
                               created_at=datetime.datetime.utcnow()))
    s.commit()

TEST_UID = 777001
A1, A2, A3 = "Аналитик", "Маркетолог", "Разработчик"

# ── utils ─────────────────────────────────────────────────────────────────────
OK = "\033[92m✅\033[0m"
ER = "\033[91m❌\033[0m"
results = []

def report(label, ok, msg=""):
    print(f"  {OK if ok else ER} {label}" + (f"  →  {str(msg)[:130]}" if msg else ""))
    results.append((label, ok))

def mk_d(**kw):
    return _json.dumps({"action": "adaptive", **kw}, ensure_ascii=False)

def mk_r(**kw):
    return _json.dumps(kw, ensure_ascii=False)

from contextlib import contextmanager

@contextmanager
def patch(obj, name, replacement):
    orig = getattr(obj, name)
    setattr(obj, name, replacement)
    try:
        yield orig
    finally:
        setattr(obj, name, orig)


# ═══════════════════════════════════════════════════════════════════════════════
# С1: Базовый двухагентный поток
# ═══════════════════════════════════════════════════════════════════════════════
print("\n\033[1m══ С1: Базовый двухагентный поток ══\033[0m")

async def s1():
    rn, agent_calls, ixs = [0], [], []

    async def quick(msgs, max_tokens=300, **kw):
        c = msgs[-1]["content"] if msgs else ""
        if "Реши: миссия" in c:
            rn[0] += 1
            if rn[0] == 1:
                return mk_r(action="next", agent_name=A2,
                            agent_task="Стратегия на основе анализа",
                            director_message=f"{A2}, твой выход!")
            return mk_r(action="finalize")
        if "Команда агентов" in c:
            return "Анализ+стратегия готовы."
        return mk_d(director_intro="Запускаю команду.",
                    mission_brief="Анализ рынка и стратегия для AI-продукта",
                    first_agent_name=A1, first_agent_task="Проанализируй рынок AI",
                    director_message=f"{A1}, старт!")

    async def exe(ag, task, user_id, dialog_context=""):
        agent_calls.append(ag["name"])
        return f"Результат {ag['name']}: готово. ПЕРЕДАЮ: маркетологу"

    def save(uid, text):
        ixs.append(text)

    with patch(ag_mod, "_quick_ai_call_raw", quick), \
         patch(ag_mod, "_exec_agent_for_director", exe), \
         patch(ag_mod, "_save_interaction_for_director", save):
        result = await ag_mod._office_director_chat(
            "Нужен анализ рынка и стратегия для AI-стартапа", TEST_UID)

    plain_ixs = [i for i in ixs if not i.startswith('{')]
    report("Оба агента вызваны в порядке", agent_calls == [A1, A2],
           f"порядок: {agent_calls}")
    # После А1 → routing=1 (говорит next→А2), после А2 → routing=2 (говорит finalize)
    # А3 ещё остаётся, поэтому routing вызывается на шаге А2 тоже.
    # Но на абсолютно последнем шаге (index=3) или при пустом remaining — не вызывается.
    report("Роутинг вызван ≤ количества шагов", 0 < rn[0] <= 3,
           f"routing: {rn[0]}")
    report("director_message первого агента показан",
           any("старт" in i for i in plain_ixs), str(plain_ixs[:3]))
    report("director_message второго агента показан",
           any("твой выход" in i for i in plain_ixs), str(plain_ixs[:3]))
    report("Финальный результат получен", bool(result), result or "(None)")
    _result_str = result.get('response', '') if isinstance(result, dict) else (result or '')
    report("ПЕРЕДАЮ не утекает в финал", "ПЕРЕДАЮ" not in _result_str,
           _result_str[:80])

asyncio.run(s1())


# ═══════════════════════════════════════════════════════════════════════════════
# С2: Ранний финализ
# ═══════════════════════════════════════════════════════════════════════════════
print("\n\033[1m══ С2: Ранний финализ ══\033[0m")

async def s2():
    rn, ac = [0], []

    async def quick(msgs, max_tokens=300, **kw):
        c = msgs[-1]["content"] if msgs else ""
        if "Реши: миссия" in c:
            rn[0] += 1
            return mk_r(action="finalize")
        if "Команда агентов" in c:
            return "Одного хватило."
        return mk_d(mission_brief="Быстрый анализ конкурентов",
                    first_agent_name=A1, first_agent_task="Топ-3 конкурента")

    async def exe(ag, task, user_id, dialog_context=""):
        ac.append(ag["name"])
        return "Топ-3: ChatGPT, Claude, Gemini. ПЕРЕДАЮ: стоп."

    with patch(ag_mod, "_quick_ai_call_raw", quick), \
         patch(ag_mod, "_exec_agent_for_director", exe), \
         patch(ag_mod, "_save_interaction_for_director", lambda u, t: None):
        result = await ag_mod._office_director_chat("конкуренты", TEST_UID)

    report("Только 1 агент", len(ac) == 1, f"{ac}")
    report("Роутинг сделан 1 раз", rn[0] == 1, f"routing: {rn[0]}")
    report("Результат получен", bool(result), result or "(None)")
    _result_str2 = result.get('response', '') if isinstance(result, dict) else (result or '')
    report("ПЕРЕДАЮ убрано", "ПЕРЕДАЮ" not in _result_str2, _result_str2[:80])

asyncio.run(s2())


# ═══════════════════════════════════════════════════════════════════════════════
# С3: Агент не найден
# ═══════════════════════════════════════════════════════════════════════════════
print("\n\033[1m══ С3: Агент не найден (неверное имя) ══\033[0m")

async def s3():
    ac = []

    async def quick(msgs, max_tokens=300, **kw):
        return mk_d(mission_brief="тест", first_agent_name="НеСуществует_XYZ",
                    first_agent_task="сделай")

    async def exe(ag, task, user_id, dialog_context=""):
        ac.append(ag["name"])
        return "не должно"

    with patch(ag_mod, "_quick_ai_call_raw", quick), \
         patch(ag_mod, "_exec_agent_for_director", exe), \
         patch(ag_mod, "_save_interaction_for_director", lambda u, t: None):
        result = await ag_mod._office_director_chat("тест несущ. агента", TEST_UID)

    report("Реальный агент не вызван", len(ac) == 0, f"вызовы: {ac}")
    report("Возвращает None", result is None, repr(result))

asyncio.run(s3())


# ═══════════════════════════════════════════════════════════════════════════════
# С4: Max steps guard
# ═══════════════════════════════════════════════════════════════════════════════
print("\n\033[1m══ С4: Max steps guard (4 шага максимум) ══\033[0m")

async def s4():
    rn, ac = [0], []
    ALL_AGENTS = [A1, A2, A3, "Дизайнер", "Копирайтер"]
    # цикл даёт next-agent без повторений первых 4 шагов

    async def quick(msgs, max_tokens=300, **kw):
        c = msgs[-1]["content"] if msgs else ""
        if "Реши: миссия" in c:
            rn[0] += 1
            i = rn[0]
            return mk_r(action="next",
                        agent_name=ALL_AGENTS[i % len(ALL_AGENTS)],
                        agent_task=f"шаг {i+1}", director_message=f"шаг {i+1}")
        if "Команда агентов" in c:
            return "Итог."
        return mk_d(mission_brief="бесконечная цепочка",
                    first_agent_name=A1, first_agent_task="шаг 1")

    async def exe(ag, task, user_id, dialog_context=""):
        ac.append(ag["name"])
        return f"шаг {len(ac)} выполнен. ПЕРЕДАЮ: следующему"

    with patch(ag_mod, "_quick_ai_call_raw", quick), \
         patch(ag_mod, "_exec_agent_for_director", exe), \
         patch(ag_mod, "_save_interaction_for_director", lambda u, t: None):
        result = await ag_mod._office_director_chat("бесконечная задача", TEST_UID)

    # При 5 доступных агентах и постоянном ответе "next" — ровно 4 вызова (MAX=4)
    report("Ровно 4 агента вызвано (MAX_ADAPTIVE_STEPS=4)", len(ac) == 4,
           f"вызовов: {len(ac)} {ac}")
    report("Роутинг не более 3 раз (не на последнем шаге)", rn[0] <= 3,
           f"routing: {rn[0]}")
    report("Финал получен", bool(result), result or "(None)")

asyncio.run(s4())


# ═══════════════════════════════════════════════════════════════════════════════
# С5: ПЕРЕДАЮ regex — unit-тест
# ═══════════════════════════════════════════════════════════════════════════════
print("\n\033[1m══ С5: Regex-обрезка ПЕРЕДАЮ ══\033[0m")

cases = [
    ("Нашёл 5 новостей.\nПЕРЕДАЮ: маркетологу", "Нашёл 5 новостей."),
    ("Готово.\n\nПЕРЕДАЮ: [передай дизайнеру]", "Готово."),
    ("Чистый текст",                             "Чистый текст"),
    ("ПЕРЕДАЮ: с самого начала",                 ""),
    ("Строка1\nПЕРЕДАЮ: сигнал\nСтрока2",       "Строка1\nСтрока2"),
    ("Два\nПЕРЕДАЮ: первый\nПЕРЕДАЮ: второй",   "Два"),
]

for raw, expected in cases:
    cleaned = _re.sub(r'\n?ПЕРЕДАЮ:\s*[^\n]*', '', raw).strip()
    ok = cleaned == expected
    report(f"  {repr(raw[:45])}", ok,
           f"→ {repr(cleaned)}" + (f" (exp={repr(expected)})" if not ok else ""))


# ═══════════════════════════════════════════════════════════════════════════════
# С6: Mission brief → anchor (cooldown=24ч)
# ═══════════════════════════════════════════════════════════════════════════════
print("\n\033[1m══ С6: Mission brief сохранён в anchor ══\033[0m")

async def s6():
    anchors = []

    def mock_anchor(user_db_id, agent_id, agent_name, task, result_summary,
                    cooldown_hours=2.0):
        anchors.append({"agent_name": agent_name, "result_summary": result_summary,
                        "cooldown_hours": cooldown_hours})

    async def quick(msgs, max_tokens=300, **kw):
        c = msgs[-1]["content"] if msgs else ""
        if "Реши: миссия" in c:
            return mk_r(action="finalize")
        if "Команда агентов" in c:
            return "Итог."
        return mk_d(mission_brief="Покорить мир через AI",
                    first_agent_name=A1, first_agent_task="Анализируй")

    async def exe(ag, task, user_id, dialog_context=""):
        return "анализ готов"

    with patch(ag_mod, "_quick_ai_call_raw", quick), \
         patch(ag_mod, "_exec_agent_for_director", exe), \
         patch(ag_mod, "_save_interaction_for_director", lambda u, t: None), \
         patch(ag_mod, "_save_agent_delegation_anchor", mock_anchor):
        await ag_mod._office_director_chat("большая миссия", TEST_UID)

    mission = next((a for a in anchors if a["agent_name"] == "__mission__"), None)
    report("Mission anchor создан", mission is not None, str(anchors))
    if mission:
        report("Mission brief в тексте", "Покорить мир" in str(mission.get("result_summary", "")),
               mission.get("result_summary", ""))
        report("cooldown_hours == 24", mission.get("cooldown_hours") == 24,
               str(mission.get("cooldown_hours")))

asyncio.run(s6())


# ═══════════════════════════════════════════════════════════════════════════════
# С7: Контекст предыдущих агентов обрезается (≤603 символа)
# ═══════════════════════════════════════════════════════════════════════════════
print("\n\033[1m══ С7: Обрезка контекста (≤600 символов на агента) ══\033[0m")

async def s7():
    rn, ctxs, called_n = [0], [], [0]
    LONG = "Z" * 1500

    async def quick(msgs, max_tokens=300, **kw):
        c = msgs[-1]["content"] if msgs else ""
        if "Реши: миссия" in c:
            rn[0] += 1
            if rn[0] == 1:
                return mk_r(action="next", agent_name=A2,
                            agent_task="продолжай", director_message=f"{A2} вперёд")
            return mk_r(action="finalize")
        if "Команда агентов" in c:
            return "Итог."
        return mk_d(mission_brief="тест обрезки",
                    first_agent_name=A1, first_agent_task="сгенерируй длинный ответ")

    async def exe(ag, task, user_id, dialog_context=""):
        called_n[0] += 1
        if dialog_context:
            ctxs.append(dialog_context)
        return LONG if called_n[0] == 1 else "второй агент"

    with patch(ag_mod, "_quick_ai_call_raw", quick), \
         patch(ag_mod, "_exec_agent_for_director", exe), \
         patch(ag_mod, "_save_interaction_for_director", lambda u, t: None):
        await ag_mod._office_director_chat("тест обрезки", TEST_UID)

    report("Второй агент получил контекст", len(ctxs) >= 2,
           f"кол-во ctxs: {len(ctxs)}")
    if len(ctxs) >= 2:
        z_runs = _re.findall(r'Z+', ctxs[1])
        max_z  = len(max(z_runs, key=len)) if z_runs else 0
        report("Блок предыдущего результата ≤603 символов", max_z <= 603,
               f"макс. блок Z в ctx: {max_z}")

asyncio.run(s7())


# ═══════════════════════════════════════════════════════════════════════════════
# С8: Простой запрос → action=self → None
# ═══════════════════════════════════════════════════════════════════════════════
print("\n\033[1m══ С8: Простой запрос → self → None ══\033[0m")

async def s8():
    ac = []

    async def quick(msgs, max_tokens=300, **kw):
        return _json.dumps({"action": "self", "team_hint": "нет"}, ensure_ascii=False)

    async def exe(ag, task, user_id, dialog_context=""):
        ac.append(ag["name"])
        return "не должно"

    with patch(ag_mod, "_quick_ai_call_raw", quick), \
         patch(ag_mod, "_exec_agent_for_director", exe), \
         patch(ag_mod, "_save_interaction_for_director", lambda u, t: None):
        result = await ag_mod._office_director_chat("привет", TEST_UID)

    report("Агент НЕ вызван", len(ac) == 0, f"вызовы: {ac}")
    report("Возвращает None", result is None, repr(result))

asyncio.run(s8())


# ═══════════════════════════════════════════════════════════════════════════════
# ИТОГ
# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("\n" + "━" * 55)
    total  = len(results)
    passed = sum(1 for _, ok in results if ok)
    failed = total - passed
    clr    = "\033[92m" if failed == 0 else "\033[91m"
    print(f"{clr}Результат: {passed}/{total} прошло, {failed} упало\033[0m")
    if failed:
        print("\nУпавшие:")
        for label, ok in results:
            if not ok:
                print(f"  {ER} {label}")
    print()
    sys.exit(0 if failed == 0 else 1)
