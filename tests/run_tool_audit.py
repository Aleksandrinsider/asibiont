"""
Direct tool audit — runs without pytest.
Tests: handler presence, no-None returns, required fields, system prompt, time parser.
Usage: python tests/run_tool_audit.py
"""
import sys, os, asyncio, inspect

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Must be set BEFORE importing config/models
os.environ['LOCAL'] = '1'
os.environ['FREE_ACCESS_MODE'] = '1'
os.environ.setdefault('DEEPSEEK_API_KEY', 'sk-test-audit')
os.environ.setdefault('BOT_TOKEN', '123456:TEST')

# ── colours ──────────────────────────────────────────────────────────
OK  = "\033[92m[OK]\033[0m"
ERR = "\033[91m[FAIL]\033[0m"
results = []

def preview(val):
    return str(val or "")[:100]

def report(label, ok, msg=""):
    sym = OK if ok else ERR
    line = f"  {sym} {label}"
    if msg:
        line += f"  ->  {msg[:120]}"
    print(line)
    results.append((label, ok))

# ── fresh in-memory SQLite (avoids local.db stale ULTRA enum) ────────
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, User, UserProfile, Task, Goal

test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
Base.metadata.create_all(test_engine)
TestSession = sessionmaker(bind=test_engine)
session = TestSession()

# Patch models.Session AND handlers.Session (handlers imports Session directly)
import models
import ai_integration.handlers as handlers
models.Session = TestSession
handlers.Session = TestSession

TEST_UID = 999992
user_obj = User(telegram_id=TEST_UID, username="audit_bot", first_name="Audit",
                language="ru", subscription_tier="PREMIUM")
session.add(user_obj)
session.flush()
session.add(UserProfile(user_id=user_obj.id, bio="Autotest", skills="Python, AI"))
session.commit()

# ════════════════════════════════════════════════════════════════════
# 1. TOOL REGISTRY
# ════════════════════════════════════════════════════════════════════
print("\n--- 1. Tool registry ---")
from ai_integration.tools import TOOLS, EXCLUDED_TOOLS

active_tools = [t for t in TOOLS if t["function"]["name"] not in EXCLUDED_TOOLS]
print(f"  Total tools: {len(TOOLS)}, excluded: {len(EXCLUDED_TOOLS)}, active: {len(active_tools)}")

missing_handlers = []
no_description   = []
for tool in active_tools:
    fn   = tool["function"]
    name = fn["name"]
    if not getattr(handlers, name, None):
        missing_handlers.append(name)
    if not fn.get("description", "").strip():
        no_description.append(name)

report("All active tools have handlers",
       not missing_handlers,
       "MISSING: " + str(missing_handlers) if missing_handlers else "")
report("All active tools have descriptions",
       not no_description,
       "NO DESC: " + str(no_description) if no_description else "")

names = [t["function"]["name"] for t in TOOLS]
dupes = [n for n in set(names) if names.count(n) > 1]
report("No duplicate tool names", not dupes, str(dupes) if dupes else "")

# ════════════════════════════════════════════════════════════════════
# 2. CORE TOOL HANDLERS
# ════════════════════════════════════════════════════════════════════
print("\n--- 2. Core tool handlers ---")

async def call(name, **extra):
    func = getattr(handlers, name, None)
    if not func:
        return None, "handler not found"
    sig = inspect.signature(func)
    kwargs = {"user_id": TEST_UID, **extra}
    if "session" in sig.parameters:
        kwargs["session"] = session
    if "close_session" in sig.parameters:
        kwargs["close_session"] = False
    try:
        if asyncio.iscoroutinefunction(func):
            r = await func(**kwargs)
        else:
            r = func(**kwargs)
        session.commit()
        return r, None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"

async def run():
    # ── task CRUD ──────────────────────────────────────────────────
    r, e = await call("list_tasks")
    report("list_tasks -> str",     isinstance(r, str) and not e, e or preview(r))

    r, e = await call("add_task",   title="Audit task", description="autotest",
                      reminder_time="2026-12-31 10:00")
    report("add_task -> str",       isinstance(r, str) and not e, e or preview(r))

    session.expire_all()
    r2, e2 = await call("list_tasks")
    has_task = isinstance(r2, str) and "Audit" in r2
    report("list_tasks sees new task", has_task,
           "Not found, got: " + preview(r2) if not has_task else preview(r2))

    task = session.query(Task).filter(Task.status == 'pending').first()
    if task:
        r, e = await call("complete_task", task_id=task.id)
        report("complete_task -> str",  isinstance(r, str) and not e, e or preview(r))
    else:
        report("complete_task", False, "no pending task in DB")

    r, e = await call("add_task", title="Audit edit-me", description="edit",
                      reminder_time="2026-12-31 11:00")
    session.expire_all()
    task2 = session.query(Task).filter(Task.title.like("%edit-me%")).first()
    if task2:
        r, e = await call("edit_task", task_id=task2.id, title="Audit edited")
        report("edit_task -> str",   isinstance(r, str) and not e, e or preview(r))
        r, e = await call("delete_task", task_id=task2.id)
        report("delete_task -> str", isinstance(r, str) and not e, e or preview(r))
    else:
        report("edit_task",   False, "task not found after add")
        report("delete_task", False, "task not found after add")

    # ── goals CRUD ─────────────────────────────────────────────────
    r, e = await call("create_goal", title="Audit goal", description="test", target_date="2026-12-31")
    report("create_goal -> str",    isinstance(r, str) and not e, e or preview(r))
    report("create_goal not empty", bool(r and r.strip()), "empty!" if not (r and r.strip()) else "")

    r, e = await call("list_goals")
    report("list_goals -> str",     isinstance(r, str) and not e, e or preview(r))
    report("list_goals no crash",   not e, e or "")

    session.expire_all()
    goal = session.query(Goal).first()
    if goal:
        # update_goal_progress takes goal_title (str) and progress (int)
        r, e = await call("update_goal_progress",
                          goal_title=goal.title, progress=50, notes="test")
        report("update_goal_progress -> str", isinstance(r, str) and not e, e or preview(r))
    else:
        report("update_goal_progress", False, "no goal found in DB")

    # ── profile ────────────────────────────────────────────────────
    r, e = await call("update_profile", skills="Python, AI", interests="ML")
    report("update_profile -> str", isinstance(r, str) and not e, e or preview(r))

    # ── posts ──────────────────────────────────────────────────────
    r, e = await call("create_post", content="Audit test post content here")
    report("create_post -> str",    isinstance(r, str) and not e, e or preview(r))

    r, e = await call("get_posts")
    report("get_posts -> str",      isinstance(r, str) and not e, e or preview(r))

    # ── contacts ───────────────────────────────────────────────────
    r, e = await call("find_relevant_contacts_for_task",
                      task_description="iOS mobile app development", limit=3)
    report("find_relevant_contacts -> str", isinstance(r, str) and not e, e or preview(r))

    r, e = await call("set_contact_alert", skill="Python", enabled=True)
    report("set_contact_alert -> str",      isinstance(r, str) and not e, e or preview(r))

    # ── utility ────────────────────────────────────────────────────
    # check_time_conflicts takes reminder_time (str), no duration_minutes
    r, e = await call("check_time_conflicts", reminder_time="завтра в 10:00")
    report("check_time_conflicts -> str",  isinstance(r, str) and not e, e or preview(r))

    # get_system_status returns a dict
    r, e = await call("get_system_status")
    is_dict = isinstance(r, dict) and not e
    report("get_system_status -> dict",    is_dict,
           e or (("'overall' in result" if 'overall' in (r or {}) else "missing 'overall'") if is_dict else preview(r)))
    report("get_system_status.overall present", not e and isinstance(r, dict) and 'overall' in r,
           e or preview(r))

    r, e = await call("get_news_trends", topic="искусственный интеллект")
    report("get_news_trends -> str",       isinstance(r, str) and not e, e or preview(r))

asyncio.run(run())

# ════════════════════════════════════════════════════════════════════
# 3. TIME PARSER
# ════════════════════════════════════════════════════════════════════
print("\n--- 3. Time parser (offline fallback) ---")

async def run_time():
    from datetime import datetime
    import pytz
    from ai_integration.time_parser import parse_time_simple_fallback

    tz  = pytz.timezone("Europe/Moscow")
    now = datetime.now(tz)
    ru_cases = [
        ("завтра в 10:00",       True),
        ("послезавтра в 14:30",  True),
        ("через 2 часа",         True),
        ("сегодня вечером",      None),  # None acceptable
        ("каждый день",          None),
    ]
    for txt, expect in ru_cases:
        try:
            res = parse_time_simple_fallback(txt, now, lang="ru")
            if expect is True:
                ok = res is not None and hasattr(res, "hour")
            else:
                ok = True
            report("time_parser: '" + txt + "'", ok, str(res) if res else "None (ok for this case)")
        except Exception as ex:
            report("time_parser: '" + txt + "'", False, str(ex))

asyncio.run(run_time())

# ════════════════════════════════════════════════════════════════════
# 4. START_CONTENT_CAMPAIGN — post_time required
# ════════════════════════════════════════════════════════════════════
print("\n--- 4. start_content_campaign config ---")
found = next((t for t in TOOLS if t["function"]["name"] == "start_content_campaign"), None)
if found:
    req  = found["function"]["parameters"].get("required", [])
    desc = found["function"]["parameters"]["properties"].get("post_time", {}).get("description", "")
    report("post_time in required",        "post_time" in req,               str(req))
    report("post_time no default 12:00",   "По умолчанию 12:00" not in desc, desc[:80])
else:
    report("start_content_campaign in TOOLS", False, "not found")

# ════════════════════════════════════════════════════════════════════
# 5. SYSTEM PROMPT
# ════════════════════════════════════════════════════════════════════
print("\n--- 5. System prompt quality ---")
try:
    from ai_integration.system_prompt import select_prompt_version
    ru = select_prompt_version(lang="ru")
    en = select_prompt_version(lang="en")
    report("System prompt RU loaded",      bool(ru and len(ru) > 100))
    report("No forced URL dump (RU)",      "включай ВСЕ найденные URL" not in ru)
    report("No forced URL dump (EN)",      "include ALL URLs" not in en)
    report("No forced 12:00 (RU)",         "По умолчанию 12:00" not in ru)
except Exception as ex:
    report("system_prompt import", False, str(ex))

# ════════════════════════════════════════════════════════════════════
# 6. DISPATCH SAFETY
# ════════════════════════════════════════════════════════════════════
print("\n--- 6. Dispatch safety ---")
unknown = getattr(handlers, "nonexistent_tool_xyz", None)
report("Unknown tool -> None (no crash)", unknown is None)

# ════════════════════════════════════════════════════════════════════
# SUMMARY
# ════════════════════════════════════════════════════════════════════
session.close()
print("\n" + "="*55)
passed = sum(1 for _, ok in results if ok)
failed = [(lbl, ok) for lbl, ok in results if not ok]
print(f"  PASSED: {passed}/{len(results)}")
if failed:
    print(f"\n  FAILED:")
    for lbl, _ in failed:
        print(f"    - {lbl}")
print("="*55)
sys.exit(0 if not failed else 1)
