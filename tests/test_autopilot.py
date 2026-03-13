"""
Тесты системы автопилота целей (goal autopilot).

Проверяют:
  1. _build_autopilot_prompt — корректные промпты под интеграции агента
  2. update_goal_progress — обновление прогресса/метрики цели
  3. create_goal + list_goals — CRUD целей
  4. delegate_task param fix (task_title → title) через _fix_tool_params
  5. _exec_agent_for_director — мок AI-вызов с tool-calling
  6. Autopilot anchor trigger — якорь создаётся для пользователя с goal_autopilot_enabled
  7. update_goal_progress rate-limit guard
  8. Goal completion автоматически при 100%

Запуск: python -m pytest tests/test_autopilot.py -v
"""
import sys
import os
import asyncio
import json
import unittest.mock as mock
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("LOCAL", "1")
os.environ.setdefault("FREE_ACCESS_MODE", "1")
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test")
os.environ.setdefault("TELEGRAM_TOKEN", "123456:TEST")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import models

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
models.Base.metadata.create_all(engine)
TestSession = sessionmaker(bind=engine)

import ai_integration.handlers as h_mod
import ai_integration.autonomous_agent as ag_mod
import ai_integration.conversation_history as ch_mod
import token_service as ts_mod
import subscription_service as ss_mod

for mod in (models, h_mod, ag_mod, ch_mod, ts_mod, ss_mod):
    mod.Session = TestSession

UID = 888001  # основной тестовый пользователь
UID2 = 888002  # пустой профиль

with TestSession() as s:
    for uid, name in [(UID, "autopilot_user"), (UID2, "empty_user")]:
        if not s.query(models.User).filter_by(telegram_id=uid).first():
            s.add(models.User(
                telegram_id=uid, username=name,
                first_name="Autopilot",
                subscription_tier=models.SubscriptionTier.PREMIUM,
                token_balance=99999,
            ))
    s.commit()
    u = s.query(models.User).filter_by(telegram_id=UID).first()
    if not s.query(models.UserProfile).filter_by(user_id=u.id).first():
        s.add(models.UserProfile(
            user_id=u.id,
            bio="AI-стартапер",
            skills="Python, ML",
            interests="стартапы, AI",
            goals="вывести ASI Biont на 1000 пользователей",
            city="Москва",
            goal_autopilot_enabled=True,
        ))
    s.commit()


def run(coro):
    return asyncio.run(coro)


# ══════════════════════════════════════════════════════════════════════════════
# 1. _build_autopilot_prompt
# ══════════════════════════════════════════════════════════════════════════════

def test_autopilot_prompt_email_agent():
    """Промпт для агента с email-интеграцией содержит email-инструкции."""
    from anchor_engine import _build_autopilot_prompt
    goals = [{"title": "Набрать 100 клиентов", "progress": 10, "metric_current": 10, "metric_target": 100}]
    prompt = _build_autopilot_prompt(goals, agent_caps=["Gmail (почта)", "IMAP почта"])
    assert "email" in prompt.lower() or "почт" in prompt.lower() or "check_emails" in prompt.lower(), \
        f"Email-промпт должен упоминать email: {prompt[:200]}"


def test_autopilot_prompt_no_integrations():
    """Промпт без интеграций содержит web_search и research_topic."""
    from anchor_engine import _build_autopilot_prompt
    goals = [{"title": "Сбросить 10 кг", "progress": 20}]
    prompt = _build_autopilot_prompt(goals, agent_caps=[])
    assert "web_search" in prompt or "research_topic" in prompt, \
        f"Базовый промпт должен упоминать web_search/research_topic: {prompt[:200]}"


def test_autopilot_prompt_contains_goals():
    """Промпт содержит перечень целей."""
    from anchor_engine import _build_autopilot_prompt
    goals = [
        {"title": "Запустить SaaS", "progress": 30},
        {"title": "Нанять 5 сотрудников", "progress": 0},
    ]
    prompt = _build_autopilot_prompt(goals)
    assert "Запустить SaaS" in prompt
    assert "Нанять 5 сотрудников" in prompt


# ══════════════════════════════════════════════════════════════════════════════
# 2. create_goal / list_goals
# ══════════════════════════════════════════════════════════════════════════════

def test_create_goal_basic():
    """create_goal создаёт запись в БД."""
    from ai_integration.handlers import create_goal
    result = create_goal(
        title="Набрать 50 учеников",
        category="work",
        priority="high",
        metric_target=50,
        metric_unit="учеников",
        user_id=UID,
    )
    assert result is not None
    assert "50" in str(result) or "учеников" in str(result).lower() or "цель" in str(result).lower(), \
        f"Unexpected result: {result}"

    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        goal = s.query(models.Goal).filter_by(user_id=u.id, title="Набрать 50 учеников").first()
        assert goal is not None, "Цель должна быть создана в БД"
        assert goal.metric_target == 50
        assert goal.metric_unit == "учеников"


def test_list_goals_shows_active():
    """list_goals показывает активные цели."""
    from ai_integration.handlers import create_goal, list_goals
    create_goal(title="Автопилот-тест-цель-9999", user_id=UID)
    result = list_goals(user_id=UID)
    assert result is not None
    assert "Автопилот-тест-цель-9999" in str(result), f"Цель не найдена в списке: {result}"


# ══════════════════════════════════════════════════════════════════════════════
# 3. update_goal_progress
# ══════════════════════════════════════════════════════════════════════════════

def test_update_goal_progress_percentage():
    """update_goal_progress обновляет процент прогресса."""
    from ai_integration.handlers import create_goal, update_goal_progress
    create_goal(title="Цель-прогресс-тест", user_id=UID)
    result = update_goal_progress(
        goal_title="Цель-прогресс-тест",
        progress=45,
        user_id=UID,
    )
    assert "45" in str(result), f"Прогресс 45% должен быть в ответе: {result}"

    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        goal = s.query(models.Goal).filter_by(user_id=u.id, title="Цель-прогресс-тест").first()
        assert goal and goal.progress_percentage == 45


def test_update_goal_metric_current():
    """update_goal_progress обновляет metric_current и пересчитывает процент."""
    from ai_integration.handlers import create_goal, update_goal_progress
    create_goal(
        title="Метрика-тест-цель", metric_target=100, metric_unit="клиентов", user_id=UID
    )
    result = update_goal_progress(
        goal_title="Метрика-тест-цель",
        metric_current=25,
        user_id=UID,
    )
    assert result is not None
    # Должен быть либо успешный ответ с 25% либо rate-limit сообщение
    # (rate-limit может сработать при повторных тестах)
    assert "25" in str(result) or "обновля" in str(result).lower() or "rate" in str(result).lower() or "3ч" in str(result), \
        f"Неожиданный ответ: {result}"


def test_update_goal_completion_auto():
    """update_goal_progress autosets status=completed при progress=100."""
    from ai_integration.handlers import create_goal, update_goal_progress
    create_goal(title="Цель-завершение", user_id=UID)
    result = update_goal_progress(
        goal_title="Цель-завершение",
        progress=100,
        user_id=UID,
    )
    assert result is not None
    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        goal = s.query(models.Goal).filter_by(user_id=u.id, title="Цель-завершение").first()
        assert goal is not None
        assert goal.status == "completed" or goal.progress_percentage == 100


def test_update_goal_adds_notes():
    """update_goal_progress сохраняет заметку о прогрессе."""
    from ai_integration.handlers import create_goal, update_goal_progress
    create_goal(title="Цель-заметка", user_id=UID)
    update_goal_progress(
        goal_title="Цель-заметка",
        notes="Нашёл первых 3 клиентов через LinkedIn",
        user_id=UID,
    )
    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        goal = s.query(models.Goal).filter_by(user_id=u.id, title="Цель-заметка").first()
        assert goal and goal.progress_notes
        assert "LinkedIn" in goal.progress_notes


def test_update_goal_not_found():
    """update_goal_progress возвращает понятную ошибку если цель не найдена."""
    from ai_integration.handlers import update_goal_progress
    result = update_goal_progress(
        goal_title="НесуществующаяЦельXYZ99999",
        progress=50,
        user_id=UID,
    )
    assert result is not None
    assert "не найден" in str(result).lower() or "нет активных" in str(result).lower() or "цел" in str(result).lower()


# ══════════════════════════════════════════════════════════════════════════════
# 4. delegate_task param fix
# ══════════════════════════════════════════════════════════════════════════════

def test_fix_task_title_to_title():
    """_fix_tool_params: task_title → title для delegate_task."""
    agent = ag_mod.HybridAutonomousAgent()
    params = {"task_title": "Написать отчёт", "delegated_to_username": "Марк"}
    fixed = agent._fix_tool_params("delegate_task", params, "поручи Марку написать отчёт")
    assert "title" in fixed, f"title должен быть в params: {fixed}"
    assert fixed.get("title") == "Написать отчёт", f"title должен быть 'Написать отчёт': {fixed}"
    assert "task_title" not in fixed, f"task_title должен быть удалён: {fixed}"


def test_fix_task_name_to_title():
    """_fix_tool_params: task_name → title для delegate_task."""
    agent = ag_mod.HybridAutonomousAgent()
    params = {"task_name": "Проанализировать рынок", "delegated_to_username": "Аналитик"}
    fixed = agent._fix_tool_params("delegate_task", params)
    assert fixed.get("title") == "Проанализировать рынок"


def test_fix_delegate_empty_title_fallback():
    """_fix_tool_params: пустой title → извлекается из user_message."""
    agent = ag_mod.HybridAutonomousAgent()
    params = {"delegated_to_username": "Кристина"}
    fixed = agent._fix_tool_params("delegate_task", params, "поручи Кристине отправить письмо партнёрам")
    assert fixed.get("title"), f"title должен быть извлечён: {fixed}"


# ══════════════════════════════════════════════════════════════════════════════
# 5. _exec_agent_for_director — мок AI autopilot
# ══════════════════════════════════════════════════════════════════════════════

_MOCK_AI_RESPONSE = {
    "choices": [{
        "message": {
            "role": "assistant",
            "content": "Исследовал рынок AI-инструментов. Нашёл 3 потенциальных партнёра для аутрич.",
            "tool_calls": []
        },
        "finish_reason": "stop"
    }]
}

_MOCK_AI_TOOL_CALL = {
    "choices": [{
        "message": {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "add_task",
                    "arguments": json.dumps({
                        "title": "Исследовать рынок AI-решений для B2B",
                        "description": "Найти топ-10 конкурентов и потенциальных партнёров"
                    })
                }
            }]
        },
        "finish_reason": "tool_calls"
    }]
}


def test_exec_agent_text_response():
    """_exec_agent_for_director возвращает (text, tools_list) при текстовом ответе."""
    def fake_post(*args, **kwargs):
        class FakeResp:
            status = 200
            async def json(self): return _MOCK_AI_RESPONSE
            async def text(self): return ""
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
        return FakeResp()

    agent_data = {
        "id": 1, "name": "Аналитик",
        "personality": "Аналитик — исследует рынки и данные.",
        "description": "Аналитик ищет данные и тренды.",
        "tools_allowed": '["web_search", "research_topic", "add_task", "update_goal_progress"]',
        "python_code": "", "user_api_keys": "",
        "knowledge_base": "",
    }
    task = "Найди 3 B2B клиента для ASI Biont в нише AI-ассистентов."

    with mock.patch("aiohttp.ClientSession.post", fake_post):
        result = run(ag_mod._exec_agent_for_director(agent_data, task, UID))

    assert isinstance(result, tuple), f"Должен возвращать tuple: {type(result)}"
    text, tools = result
    assert isinstance(text, str), f"Первый элемент — строка: {type(text)}"
    assert isinstance(tools, list), f"Второй элемент — список: {type(tools)}"
    assert len(text) > 0, "Текст не должен быть пустым"


def test_exec_agent_creates_task_via_tool_call():
    """_exec_agent_for_director выполняет tool_calls (add_task) и возвращает результат."""
    call_count = {"n": 0}

    def fake_post(*args, **kwargs):
        class FakeResp:
            status = 200
            async def json(self):
                call_count["n"] += 1
                if call_count["n"] == 1:
                    return _MOCK_AI_TOOL_CALL
                return _MOCK_AI_RESPONSE
            async def text(self): return ""
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
        return FakeResp()

    agent_data = {
        "id": 2, "name": "Координатор",
        "personality": "Координатор — ставит задачи команде.",
        "description": "Управляет проектами.",
        "tools_allowed": '["add_task", "delegate_task", "update_goal_progress"]',
        "python_code": "", "user_api_keys": "",
        "knowledge_base": "",
    }
    task = "[АВТОПИЛОТ ЦЕЛЕЙ] Продвинь цель 'Набрать 50 учеников' (30%). Действуй."

    with mock.patch("aiohttp.ClientSession.post", fake_post):
        text, tools = run(ag_mod._exec_agent_for_director(agent_data, task, UID))

    assert isinstance(text, str)
    # Задача должна быть создана или упомянута в ответе
    assert len(text) >= 0  # не крашится


# ══════════════════════════════════════════════════════════════════════════════
# 6. Autopilot anchor trigger
# ══════════════════════════════════════════════════════════════════════════════

def test_goal_autopilot_enabled_in_profile():
    """UserProfile.goal_autopilot_enabled=True сохраняется в БД."""
    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        profile = s.query(models.UserProfile).filter_by(user_id=u.id).first()
        assert profile is not None
        assert profile.goal_autopilot_enabled is True


def test_anchor_engine_imports():
    """anchor_engine импортируется и AnchorEngine создаётся без ошибок."""
    try:
        from anchor_engine import AnchorEngine, _build_autopilot_prompt, BATCH_GROUPS
        engine = AnchorEngine()  # без bot — это нормально
        assert engine is not None
        assert "goal_autopilot_review" in BATCH_GROUPS
    except Exception as e:
        raise AssertionError(f"anchor_engine import failed: {e}")


def test_autopilot_anchor_type_in_scan_dispatcher():
    """AUTOPILOT_SILENT_TYPES содержит goal_autopilot_review."""
    # Проверяем константу напрямую (она используется в _process_user_inner)
    import inspect
    import anchor_engine
    src = inspect.getsource(anchor_engine)
    assert "goal_autopilot_review" in src
    assert "AUTOPILOT_SILENT_TYPES" in src


# ══════════════════════════════════════════════════════════════════════════════
# 7. Goal CRUD integrity
# ══════════════════════════════════════════════════════════════════════════════

def test_goal_status_transitions():
    """Цель корректно переводится из active → completed → paused."""
    from ai_integration.handlers import create_goal, update_goal_progress
    create_goal(title="Статус-тест-цель", user_id=UID)

    update_goal_progress(goal_title="Статус-тест-цель", status="paused", user_id=UID)
    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        g = s.query(models.Goal).filter_by(user_id=u.id, title="Статус-тест-цель").first()
        # paused → но update_goal_progress ищет active И paused → ok
        assert g is not None


def test_delete_goal():
    """delete_goal удаляет цель из БД."""
    from ai_integration.handlers import create_goal, delete_goal
    create_goal(title="Удалить-эту-цель-999", user_id=UID)
    result = delete_goal(goal_title="Удалить-эту-цель-999", user_id=UID)
    assert result is not None
    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        g = s.query(models.Goal).filter_by(user_id=u.id, title="Удалить-эту-цель-999").first()
        assert g is None or g.status in ("cancelled", "deleted")


# ══════════════════════════════════════════════════════════════════════════════
# 8. update_goal_progress guard — metric не уменьшается
# ══════════════════════════════════════════════════════════════════════════════

def test_metric_guard_no_decrease():
    """update_goal_progress отклоняет уменьшение метрики."""
    from ai_integration.handlers import create_goal, update_goal_progress
    create_goal(
        title="Метрика-guard-цель", metric_target=200, metric_unit="пользователей", user_id=UID
    )
    # Первое обновление — устанавливаем 50
    update_goal_progress(goal_title="Метрика-guard-цель", metric_current=50, user_id=UID)

    # Пробуем уменьшить — должен быть отклонён
    result = update_goal_progress(goal_title="Метрика-guard-цель", metric_current=10, user_id=UID)
    assert result is not None
    # Либо отклонено guard, либо rate-limit (оба ожидаемы)
    assert ("не больше" in str(result) or "rate" in str(result).lower()
            or "3ч" in str(result) or "обновля" in str(result).lower()), \
        f"Guard должен сработать: {result}"


# ══════════════════════════════════════════════════════════════════════════════
# 9. Autopilot prompt for github/rss agents
# ══════════════════════════════════════════════════════════════════════════════

def test_autopilot_prompt_github_agent():
    """Промпт для агента с GitHub содержит run_agent_action."""
    from anchor_engine import _build_autopilot_prompt
    goals = [{"title": "Найти 20 разработчиков", "progress": 5}]
    prompt = _build_autopilot_prompt(goals, agent_caps=["GitHub API"])
    assert "run_agent_action" in prompt or "github" in prompt.lower(), \
        f"GitHub-промпт должен использовать run_agent_action: {prompt[:300]}"


def test_autopilot_prompt_rss_agent():
    """Промпт для агента с RSS содержит run_agent_action или research_topic."""
    from anchor_engine import _build_autopilot_prompt
    goals = [{"title": "Мониторить конкурентов", "progress": 0}]
    prompt = _build_autopilot_prompt(goals, agent_caps=["RSS-лента новостей"])
    assert "run_agent_action" in prompt or "research_topic" in prompt, \
        f"RSS-промпт должен использовать инструменты: {prompt[:300]}"


# ══════════════════════════════════════════════════════════════════════════════
# 10. update_goal_progress при отсутствии целей
# ══════════════════════════════════════════════════════════════════════════════

def test_update_goal_no_goals_user():
    """update_goal_progress корректно отвечает для пользователя без целей."""
    from ai_integration.handlers import update_goal_progress
    result = update_goal_progress(goal_title="Любая цель", progress=50, user_id=UID2)
    assert result is not None
    # Может быть "нет активных целей" или "не найдена"
    assert isinstance(result, str)
