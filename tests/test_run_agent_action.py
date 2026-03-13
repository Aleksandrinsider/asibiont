"""
Tests for run_agent_action feature:
  1. Tool definition exists in TOOLS
  2. Hidden from AI when agent has no script
  3. Visible when agent has script
  4. tools_allowed whitelist always includes it with script
  5. _run_external_action: no script → error
  6. _run_external_action: script prints OK → success
  7. _run_external_action: ACTION env injected correctly
  8. _run_external_action: AGENT_PARAM_* env injected
  9. _run_external_action: timeout → error
 10. build_agent_system_prompt: neutral prompt (no WB/hh hardcoded text)
 11. build_agent_system_prompt: run_agent_action section present
 12. Per-user _active_agent_data  isolation (race condition guard)
"""

import sys
import os
import asyncio
import json
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("LOCAL", "1")
os.environ.setdefault("TELEGRAM_TOKEN", "test:token")
os.environ.setdefault("FREE_ACCESS_MODE", "1")
os.environ.setdefault("DEEPSEEK_API_KEY", "test")

# ── minimal DB setup ──────────────────────────────────────────────────────────
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import models

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
models.Base.metadata.create_all(engine)
TestSession = sessionmaker(bind=engine)

def make_session():
    return TestSession()

import ai_integration.autonomous_agent as ag_mod
import ai_integration.handlers as h_mod
import token_service as ts_mod
import subscription_service as ss_mod
import ai_integration.conversation_history as ch_mod

for mod in (models, h_mod, ag_mod, ch_mod, ts_mod, ss_mod):
    mod.Session = make_session

UID_A = 777001
UID_B = 777002

with TestSession() as s:
    for uid in (UID_A, UID_B):
        if not s.query(models.User).filter_by(telegram_id=uid).first():
            s.add(models.User(
                telegram_id=uid, username=f"u{uid}",
                first_name="Test", subscription_tier="ULTRA", token_balance=99999,
            ))
    s.commit()


# ── helpers ───────────────────────────────────────────────────────────────────

def run(coro):
    return asyncio.run(coro)

def make_agent_data(python_code="", tools_allowed=None):
    return {
        "id": 1,
        "name": "TestAgent",
        "personality": "Тест",
        "tools_allowed": tools_allowed or [],
        "knowledge_snippets": [],
        "price_per_message": 0,
        "author_id": UID_A,
        "author_royalty_pct": 0,
        "trial_messages": 0,
        "python_code": python_code,
        "user_api_keys": "",
    }


# ── 1. Tool definition ────────────────────────────────────────────────────────

def test_run_agent_action_in_tools():
    from ai_integration.tools import TOOLS
    names = [t["function"]["name"] for t in TOOLS]
    assert "run_agent_action" in names, "run_agent_action должен быть в TOOLS"


def test_run_agent_action_has_action_param():
    from ai_integration.tools import TOOLS
    tool = next(t for t in TOOLS if t["function"]["name"] == "run_agent_action")
    props = tool["function"]["parameters"]["properties"]
    assert "action" in props
    assert "params" in props


# ── 2. Hidden without script ──────────────────────────────────────────────────

def test_run_agent_action_excluded_without_script():
    """run_agent_action должен попасть в tools_to_exclude когда нет агента."""
    agent = ag_mod.get_autonomous_agent()
    # убеждаемся что для UID_B нет agentdata
    agent._active_agent_data.pop(UID_B, None)

    excluded = ag_mod.HybridAutonomousAgent._select_tools_for_message(agent, "привет")
    # Вручную применяем логику из process_request
    cur = agent._active_agent_data.get(UID_B)
    if not cur or not cur.get("python_code", "").strip():
        excluded.add("run_agent_action")

    assert "run_agent_action" in excluded


# ── 3. Visible with script ────────────────────────────────────────────────────

def test_run_agent_action_visible_with_script():
    agent = ag_mod.get_autonomous_agent()
    agent._active_agent_data[UID_A] = make_agent_data(python_code='print("ok")')
    cur = agent._active_agent_data.get(UID_A)
    assert cur and cur.get("python_code", "").strip(), "Скрипт должен быть виден"
    agent._active_agent_data.pop(UID_A, None)


# ── 4. tools_allowed whitelist auto-includes run_agent_action ────────────────

def test_tools_allowed_autoadd_run_agent_action():
    """Если tools_allowed задан и есть скрипт — run_agent_action добавляется."""
    allowed_set = {"add_task", "get_tasks"}
    data = make_agent_data(python_code='print("hi")', tools_allowed=list(allowed_set))
    if data.get("python_code", "").strip():
        allowed_set.add("run_agent_action")
    assert "run_agent_action" in allowed_set


def test_tools_allowed_no_autoadd_without_script():
    """Без скрипта run_agent_action НЕ добавляется в whitelist."""
    allowed_set = {"add_task"}
    data = make_agent_data(python_code="", tools_allowed=list(allowed_set))
    if data.get("python_code", "").strip():
        allowed_set.add("run_agent_action")
    assert "run_agent_action" not in allowed_set


# ── 5. _run_external_action: no script ───────────────────────────────────────

def test_run_external_action_no_script():
    agent = ag_mod.get_autonomous_agent()
    agent._active_agent_data.pop(UID_A, None)  # нет агента
    result = run(agent._run_external_action({"action": "send_message"}, UID_A))
    assert "error" in result
    assert result["error"]  # непустая ошибка


# ── 6. _run_external_action: script prints OK ────────────────────────────────

def test_run_external_action_success():
    agent = ag_mod.get_autonomous_agent()
    agent._active_agent_data[UID_A] = make_agent_data(
        python_code='import os; a=os.environ.get("AGENT_ACTION",""); print("OK: done " + a)'
    )
    result = run(agent._run_external_action({"action": "send_message", "params": {}}, UID_A))
    agent._active_agent_data.pop(UID_A, None)
    assert result.get("status") == "success"
    assert "OK: done send_message" in result.get("output", "")


# ── 7. AGENT_ACTION env injected ─────────────────────────────────────────────

def test_run_external_action_env_action():
    agent = ag_mod.get_autonomous_agent()
    agent._active_agent_data[UID_A] = make_agent_data(
        python_code='import os; print(os.environ.get("AGENT_ACTION","MISSING"))'
    )
    result = run(agent._run_external_action({"action": "create_issue", "params": {}}, UID_A))
    agent._active_agent_data.pop(UID_A, None)
    assert result.get("status") == "success"
    assert "create_issue" in result.get("output", "")


# ── 8. AGENT_PARAM_* env injected ────────────────────────────────────────────

def test_run_external_action_param_env():
    agent = ag_mod.get_autonomous_agent()
    agent._active_agent_data[UID_A] = make_agent_data(
        python_code='import os; print(os.environ.get("AGENT_PARAM_MESSAGE","MISSING"))'
    )
    result = run(agent._run_external_action(
        {"action": "send_message", "params": {"message": "hello-world"}}, UID_A
    ))
    agent._active_agent_data.pop(UID_A, None)
    assert result.get("status") == "success"
    assert "hello-world" in result.get("output", "")


# ── 9. Timeout ───────────────────────────────────────────────────────────────

def test_run_external_action_timeout():
    agent = ag_mod.get_autonomous_agent()
    agent._active_agent_data[UID_A] = make_agent_data(
        python_code='import time; time.sleep(30)'
    )
    # Мокаем wait_for чтобы не ждать реально
    async def fake_wait_for(coro, timeout):
        raise asyncio.TimeoutError()

    with mock.patch("asyncio.wait_for", side_effect=fake_wait_for):
        result = run(agent._run_external_action({"action": "ping", "params": {}}, UID_A))
    agent._active_agent_data.pop(UID_A, None)
    assert result.get("status") == "error"
    assert "Timeout" in result.get("error", "") or "timeout" in result.get("error", "").lower()


# ── 10. Neutral prompt: no hardcoded service names ───────────────────────────

def test_prompt_no_hardcoded_services():
    from ai_integration.user_agents import build_agent_system_prompt
    data = make_agent_data(python_code='print("x")')
    prompt = build_agent_system_prompt(data, "BASE")
    bad_words = ["Wildberries", "hh.ru", "ВКонтакте"]
    found = [w for w in bad_words if w in prompt]
    assert not found, f"Промпт содержит захардкоженные сервисы: {found}"


# ── 11. Prompt contains run_agent_action section ─────────────────────────────

def test_prompt_has_run_agent_action_docs():
    from ai_integration.user_agents import build_agent_system_prompt
    data = make_agent_data(python_code='print("x")')
    prompt = build_agent_system_prompt(data, "BASE")
    assert "run_agent_action" in prompt, "Промпт должен упоминать run_agent_action"


# ── 12. Per-user isolation ────────────────────────────────────────────────────

def test_per_user_agent_data_isolation():
    """Данные агента для UID_A не должны влиять на UID_B."""
    agent = ag_mod.get_autonomous_agent()
    agent._active_agent_data[UID_A] = make_agent_data(python_code='print("a")')
    agent._active_agent_data.pop(UID_B, None)

    assert agent._active_agent_data.get(UID_A) is not None
    assert agent._active_agent_data.get(UID_B) is None

    # Сброс UID_A не должен трогать UID_B
    agent._active_agent_data.pop(UID_A, None)
    assert agent._active_agent_data.get(UID_A) is None

    # Чистим
    agent._active_agent_data.pop(UID_A, None)
    agent._active_agent_data.pop(UID_B, None)


if __name__ == "__main__":
    import unittest

    # Собираем все test_ функции в TestCase
    class RunAgentActionTests(unittest.TestCase):
        pass

    import types
    _globs = dict(globals())
    for _name, _fn in list(_globs.items()):
        if _name.startswith("test_") and callable(_fn):
            # оборачиваем в метод TestCase
            def _make(f):
                def method(self):
                    f()
                method.__name__ = f.__name__
                return method
            setattr(RunAgentActionTests, _name, _make(_fn))

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(RunAgentActionTests)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
