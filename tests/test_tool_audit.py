"""
Живой аудит tools: агент реально вызывает нужные инструменты?

Блок 1 - статика: все tools.py имеют handler в handlers.py
Блок 2 - живо: DeepSeek вызывает правильный tool по команде
"""
import sys
import os
import asyncio
import warnings
import datetime
warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["LOCAL"] = "1"
os.environ["FREE_ACCESS_MODE"] = "1"
os.environ.setdefault("BOT_TOKEN", "123456:TEST")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, User, UserProfile
import models
import ai_integration.handlers as handlers
import ai_integration.autonomous_agent as _agent_mod
import ai_integration.utils as _utils_mod
import ai_integration.task_context as _task_ctx_mod
import ai_integration.premium_simple as _premium_mod
import ai_integration.conversation_history as _conv_hist_mod

test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
Base.metadata.create_all(test_engine)
TestSession = sessionmaker(bind=test_engine)

# Патчим Session везде где он импортируется напрямую
models.Session = TestSession
handlers.Session = TestSession
_agent_mod.Session = TestSession      # from models import Session (module level)
_utils_mod.Session = TestSession      # from models import Session (module level)
_task_ctx_mod.Session = TestSession   # from models import Session (module level)
_premium_mod.Session = TestSession    # from models import Session (module level)
_conv_hist_mod.Session = TestSession  # conversation history

_s = TestSession()
TEST_UID = 999111
u = User(telegram_id=TEST_UID, username="audit_live", first_name="Audit",
         language="ru", subscription_tier="PREMIUM",
         created_at=datetime.datetime.utcnow())
_s.add(u)
_s.flush()
_s.add(UserProfile(user_id=u.id, bio="Autotest",
                   skills="Python, AI", interests="avtomatizatsiya",
                   goals="zapustit produkt"))
_s.commit()
_s.close()

# == BLOK 1: Statika ==
print("=" * 60)
print("BLOK 1: Audit tools -> handlers (statika)")
print("=" * 60)

from ai_integration.tools import get_available_tools

all_tools = get_available_tools(subscription_tier="PREMIUM")
tool_names = [t["function"]["name"] for t in all_tools]
SPECIAL = {"run_agent_action"}

missing, present = [], []
for name in tool_names:
    if name in SPECIAL:
        present.append((name, "special"))
        continue
    fn = getattr(handlers, name, None)
    if fn is None:
        missing.append(name)
    else:
        kind = "async" if asyncio.iscoroutinefunction(fn) else "sync "
        present.append((name, kind))

print(f"Vsego tools: {len(tool_names)}")
for name, kind in present:
    print(f"  OK [{kind}] {name}")
if missing:
    print(f"\n  MISSING handler ({len(missing)}):")
    for n in missing:
        print(f"    MISSING: {n}")
else:
    print(f"\n  Vse {len(present)} tools imeyut handler!")


# == BLOK 2: Zhivye testy ==
print("\n" + "=" * 60)
print("BLOK 2: Zhivye vyzovy cherez DeepSeek API")
print("=" * 60)

# expected может быть строкой или списком допустимых tools
LIVE_TESTS = [
    {"msg": "Добавь задачу 'Проверить деплой' на завтра в 11:00", "expected": "add_task", "desc": "add_task"},
    {"msg": "Покажи мои задачи", "expected": "list_tasks", "desc": "list_tasks"},
    {"msg": "Создай цель: запустить блог к июню 2026", "expected": "create_goal", "desc": "create_goal"},
    {"msg": "Покажи мои цели", "expected": "list_goals", "desc": "list_goals"},
    # research_topic — более мощный инструмент; агент может выбрать его вместо get_news_trends
    {"msg": "Найди свежие новости про автоматизацию бизнеса", "expected": ["get_news_trends", "research_topic", "web_search"], "desc": "get_news_trends"},
    # Обязательно вызывает update_profile
    {"msg": "Сохрани в мой профиль: город Москва, компания Tesla", "expected": "update_profile", "desc": "update_profile"},
    # web_search / research_topic — оба допустимы
    {"msg": "Найди информацию в интернете: кто такие AI-агенты", "expected": ["web_search", "research_topic"], "desc": "web_search"},
    # delete_task — задача создаётся выше в том же тесте
    {"msg": "Удали задачу 'Проверить деплой'", "expected": "delete_task", "desc": "delete_task"},
]


async def run_live_tests():
    from ai_integration.autonomous_agent import chat_with_ai
    live_results = []
    for t in LIVE_TESTS:
        print(f"\n  [{t['desc']}]  msg={t['msg'][:60]}")
        try:
            resp = await chat_with_ai(
                message=t["msg"],
                user_id=TEST_UID,
                subscription_tier="PREMIUM",
            )
            tools_used = list(resp.get("tools_used") or [])
            # Также проверим tool_calls для неуспешных вызовов
            all_called = []
            for tc in (resp.get("tool_calls") or []):
                n = (tc.get("function") or {}).get("name") or tc.get("tool")
                if n:
                    all_called.append(n)
            if not tools_used and all_called:
                tools_used = all_called

            hit = (t["expected"] in tools_used) if isinstance(t["expected"], str) else any(e in tools_used for e in t["expected"])
            # Для диагностики MISS — показать все результаты (включая failed)
            if not hit and t.get("desc") == "delete_task":
                print(f"    [DEBUG] full tool_calls from resp: {resp.get('tool_calls')}")
                print(f"    [DEBUG] tools_used: {resp.get('tools_used')}")
                # Достать полные результаты из агента
                from ai_integration.autonomous_agent import get_autonomous_agent
                ag = get_autonomous_agent()
                if ag.execution_history:
                    last_entry = ag.execution_history[-1]
                    for r in last_entry.get('results', []):
                        print(f"    [DEBUG] tool={r.get('tool')} success={r.get('success')} err={r.get('error','')}")
            status = "OK  " if hit else "MISS"
            text_preview = (resp.get("response") or "")[:120].replace("\n", " ")
            print(f"    [{status}] called: {tools_used}")
            print(f"    resp  : {text_preview}")
            live_results.append((t["desc"], t["expected"], tools_used, hit))
        except Exception as e:
            import traceback; traceback.print_exc()
            live_results.append((t["desc"], t["expected"], [], False))
    return live_results


results = asyncio.run(run_live_tests())

print("\n" + "=" * 60)
print("ITOG")
print("=" * 60)
ok = sum(1 for *_, hit in results if hit)
for desc, exp, called, hit in results:
    mark = "OK  " if hit else "MISS"
    print(f"  [{mark}] {desc}: expected={exp}, called={called}")

print(f"\nStatika : {len(present)}/{len(tool_names)} tools imeyut handler")
print(f"Zhivye  : {ok}/{len(results)} testov proshli")

os._exit(0)
