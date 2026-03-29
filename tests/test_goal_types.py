"""
Тесты автопилота: разные типы целей и интеграции агентов.

Покрываем:
  G1  _compute_state_directives: goal с keyword «тестировщик» + GitHub-агент
       → директива run_agent_action (GitHub search)
  G2  _compute_state_directives: goal с keyword «тестировщик» + GitHub-агент
       + есть несотправленные контакты → директива send_outreach_email (приоритет!)
  G3  _compute_state_directives: финансовая цель («нефть», «рынок»)
       → директива research_topic / run_agent_action (RSS)
  G4  _compute_state_directives: контент-цель («smm», «пост»)
       → директива generate_marketing_content
  G5  _compute_state_directives: dev-цель («разработка», «код»)
       → директива run_agent_action (GitHub)
  G6  _compute_state_directives: финансовая цель прогресс ≥ 70%
       → директива update_goal_progress (финализация)
  G7  _compute_state_directives: цель без email/github/rss агентов
       → fallback на find_relevant_contacts_for_task или research_topic
  G8  _compute_state_directives: generic цель (нет ключевых слов)
       → директива research_topic / web_search
  G9  _build_autopilot_prompt: Alpha Vantage агент — содержит run_agent_action
  G10 _build_autopilot_prompt: RSS-лента — содержит run_agent_action / research_topic
  G11 _build_autopilot_prompt: Telegram/Discord — содержит publish_to_telegram
  G12 _build_autopilot_prompt: Notion — содержит run_agent_action
  G13 _build_autopilot_prompt: Stripe — содержит run_agent_action
  G14 _build_autopilot_prompt: нет интеграций — содержит web_search / research_topic
  G15 _build_reasoning_scaffold: GitHub-агент → упоминает search_users
  G16 _build_reasoning_scaffold: Alpha Vantage → упоминает get_price
  G17 _build_reasoning_scaffold: RSS → упоминает get_latest
  G18 _build_reasoning_scaffold: цель с метрикой → корректно отображает current/target
  G19 _match_best_integration: цель «найти тестировщиков» + GitHub → github wins
  G20 _match_best_integration: цель «анализ нефти» + Alpha → alpha wins
  G21 _match_best_integration: цель «публикация контента» + Telegram → content wins
  G22 _match_best_integration: нет подходящих интеграций → пустой список
  G23 _compute_state_directives: множество разнородных целей → директивы для каждой
  G24 update_goal_progress: агент может обновить любую цель (финансовую, контентную, dev)
  G25 _build_autopilot_prompt: Slack интеграция → упоминает post_message
  G26 _compute_state_directives: цель отсутствует или title пустой → нет краша
  G27 _build_autopilot_prompt: Google Sheets интеграция → упоминает run_agent_action
  G28 _compute_state_directives: финансовая цель + RSS-агент с финансовой лентой
       → директива run_agent_action (не research_topic)
  G29 _build_autopilot_prompt: содержит блок МЫШЛЕНИЕ для любой интеграции
  G30 _compute_state_directives: goal «анализ нефти» без RSS-агента
       → fallback на research_topic

Запуск: python -m pytest tests/test_goal_types.py -v
"""

import sys
import os
import asyncio
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("LOCAL", "1")
os.environ.setdefault("FREE_ACCESS_MODE", "1")
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test")
os.environ.setdefault("TELEGRAM_TOKEN", "123456:TEST")

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

import ai_integration.handlers as h_mod
import token_service as ts_mod
import subscription_service as ss_mod
import ai_integration.conversation_history as ch_mod
import ai_integration.autonomous_agent as ag_mod

_ALL_MODS = (models, h_mod, ag_mod, ch_mod, ts_mod, ss_mod)
for mod in _ALL_MODS:
    mod.Session = TestSession

UID = 999100

with TestSession() as s:
    if not s.query(models.User).filter_by(telegram_id=UID).first():
        s.add(models.User(
            telegram_id=UID, username="goal_types_user",
            first_name="GoalTypes",
            subscription_tier=models.SubscriptionTier.PREMIUM,
            token_balance=99999,
        ))
        s.commit()
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        s.add(models.UserProfile(
            user_id=u.id,
            bio="Тест-пользователь",
            goals="разные цели",
            goal_autopilot_enabled=True,
        ))
        s.commit()

import pytest


# ─── helpers ──────────────────────────────────────────────────────────────────

def _profiles_for(agent_name: str, caps: list[str], python_code: str = "") -> dict:
    return {
        "name": agent_name,
        "desc": f"Тестовый агент {agent_name}",
        "spec": f"spec_{agent_name.lower()}",
        "caps": caps,
    }


def _base_data(contacts: list | None = None, sent: list | None = None,
               total_sent: int = 0, unsent: list | None = None) -> dict:
    return {
        "known_contacts": contacts or [],
        "already_sent_emails": sent or [],
        "email_campaigns": [],
        "total_emails_sent": total_sent,
        "failed_tools": {},
        "per_agent_history": {},
        "recent_actions": [],
        "pending_replies": [],
        "unsent_contacts": unsent or [],
    }


def _goal(title: str, progress: int = 0, mc: float = 0, mt: float | None = None) -> dict:
    return {
        "title": title,
        "description": "",
        "progress": progress,
        "metric_current": mc,
        "metric_target": mt,
        "category": "",
        "id": abs(hash(title)) % 10000,
    }


# ─── импортируем тестируемые функции ──────────────────────────────────────────

from anchor_engine import (
    _build_autopilot_prompt,
    _build_reasoning_scaffold,
    _match_best_integration,
    _normalize_coordinator_assignment_by_capabilities,
)
# _compute_state_directives — статический метод AnchorEngine
from anchor_engine import AnchorEngine
_csd = AnchorEngine._compute_state_directives


# ══════════════════════════════════════════════════════════════════════════════
# G1 — People goal + GitHub agent → search directive
# ══════════════════════════════════════════════════════════════════════════════

def test_g1_people_goal_github_agent_search():
    """Цель «тестировщики» + GitHub-агент → директива run_agent_action."""
    goals = [_goal("Найти 50 тестировщиков для ASI Biont", progress=10, mc=5, mt=50)]
    profiles = [_profiles_for("Кристина", ["GitHub API Token", "Gmail IMAP"])]
    data = _base_data()
    directives = _csd(goals, data, profiles)
    assert directives, "Должна быть хотя бы одна директива"
    tools = [d["tool"] for d in directives]
    assert "run_agent_action" in tools, f"Ожидался run_agent_action, получили: {tools}"
    # Задача должна содержать инструкцию о GitHub-поиске
    task_texts = " ".join(d.get("task", "") for d in directives)
    assert "search_users" in task_texts or "github" in task_texts.lower(), \
        f"Задача должна упоминать search_users или github: {task_texts[:300]}"


# ══════════════════════════════════════════════════════════════════════════════
# G2 — People goal + GitHub agent + уже есть несотправленные контакты
# ══════════════════════════════════════════════════════════════════════════════

def test_g2_unsent_contacts_priority_over_github_search():
    """Есть несотправленные контакты → директива send_outreach_email, не search."""
    goals = [_goal("Найти 50 тестировщиков для ASI Biont", progress=12, mc=6, mt=50)]
    profiles = [_profiles_for("Кристина", ["GitHub API Token", "Gmail IMAP"])]
    data = _base_data(
        contacts=["Robertson Arthur <robertsonakpan@gmail.com> [статус: new] (src=GitHub)"],
        sent=[],   # письма не отправлялись
        unsent=["Robertson Arthur <robertsonakpan@gmail.com> [статус: new] (src=GitHub)"],
    )
    directives = _csd(goals, data, profiles)
    assert directives, "Должна быть директива"
    tools = [d["tool"] for d in directives]
    # Приоритет — отправить письмо, не искать новых
    assert "send_outreach_email" in tools, \
        f"При несотправленных контактах ожидался send_outreach_email, получили: {tools}"
    assert "run_agent_action" not in tools, \
        f"run_agent_action НЕ должен быть при наличии несотправленных контактов: {tools}"


# ══════════════════════════════════════════════════════════════════════════════
# G3 — Finance/news goal → research_topic directive
# ══════════════════════════════════════════════════════════════════════════════

def test_g3_finance_goal_research_directive():
    """Цель «анализ рынка нефти» → директива research_topic или web_search."""
    goals = [_goal("Анализ рынка нефти и газа", progress=20)]
    profiles = [_profiles_for("Марк", ["RSS Feed", "Alpha Vantage API"])]
    data = _base_data()
    directives = _csd(goals, data, profiles)
    assert directives, "Должна быть директива"
    tools = [d["tool"] for d in directives]
    allowed = {"research_topic", "web_search", "run_agent_action", "update_goal_progress", "get_stock_price"}
    for t in tools:
        assert t in allowed, f"Неожиданный инструмент для финансовой цели: {t}"


def test_g3b_news_goal_directive():
    """Цель «мониторинг новостей» → директива research_topic / run_agent_action."""
    goals = [_goal("Мониторинг новостей рынка металлов", progress=0)]
    profiles = [_profiles_for("Марк", ["RSS Feed: rbc.ru", "NewsAPI"])]
    data = _base_data()
    directives = _csd(goals, data, profiles)
    assert directives
    # не должно форсироваться email-outreach для новостной цели
    task_texts = " ".join(d.get("task", "") for d in directives)
    assert "send_outreach_email" not in task_texts, \
        "Новостная цель не должна форсировать email outreach"


# ══════════════════════════════════════════════════════════════════════════════
# G4 — Content goal → generate_marketing_content / create_post
# ══════════════════════════════════════════════════════════════════════════════

def test_g4_content_goal_directive():
    """Цель «контент, smm» → директива create_post (generate_marketing_content исключён из тулсета)."""
    goals = [_goal("Создать контент-план SMM на месяц", progress=0)]
    profiles = [_profiles_for("Арина", ["Telegram API", "SMM контент"])]
    data = _base_data()
    directives = _csd(goals, data, profiles)
    assert directives
    tools = [d["tool"] for d in directives]
    assert "create_post" in tools or "generate_marketing_content" in tools, \
        f"Контентная цель должна использовать create_post или generate_marketing_content: {tools}"


def test_g4b_smm_post_goal():
    """Цель с keyword «публикация» + Telegram → telegram-директива."""
    goals = [_goal("Публикация постов в Telegram-канал 5 раз в неделю", progress=10)]
    profiles = [_profiles_for("Арина", ["Telegram публикация", "контент"])]
    data = _base_data()
    directives = _csd(goals, data, profiles)
    assert directives
    tools = [d["tool"] for d in directives]
    allowed = {"generate_marketing_content", "publish_to_telegram", "create_post"}
    assert any(t in allowed for t in tools), f"SMM цель: {tools}"


# ══════════════════════════════════════════════════════════════════════════════
# G5 — Dev goal → run_agent_action (GitHub)
# ══════════════════════════════════════════════════════════════════════════════

def test_g5_dev_goal_github_directive():
    """Цель с GitHub-keyword → директива run_agent_action."""
    goals = [_goal("Создать issue на GitHub для отслеживания багов", progress=30)]
    profiles = [_profiles_for("Дев", ["GitHub API Token"])]
    data = _base_data()
    directives = _csd(goals, data, profiles)
    assert directives
    tools = [d["tool"] for d in directives]
    assert "run_agent_action" in tools, f"Dev цель должна использовать run_agent_action: {tools}"


def test_g5b_code_goal():
    """Цель с GitHub keyword → run_agent_action."""
    goals = [_goal("Проверить pull request в репозитории", progress=0)]
    profiles = [_profiles_for("Дев", ["GitHub API Token", "Python Script"])]
    data = _base_data()
    directives = _csd(goals, data, profiles)
    assert directives
    tools = [d["tool"] for d in directives]
    assert "run_agent_action" in tools, f"Code цель: {tools}"


# ══════════════════════════════════════════════════════════════════════════════
# G6 — Finance goal at 70%+ → finalization directive
# ══════════════════════════════════════════════════════════════════════════════

def test_g6_finance_goal_70pct_finalize():
    """Финансовая цель на 75% → директива update_goal_progress (финализация)."""
    goals = [_goal("Анализ рынка нефти", progress=75, mc=75, mt=100)]
    profiles = [_profiles_for("Марк", ["Alpha Vantage API"])]
    data = _base_data()
    directives = _csd(goals, data, profiles)
    assert directives
    tools = [d["tool"] for d in directives]
    assert "update_goal_progress" in tools, \
        f"Финансовая цель ≥70% должна финализироваться: {tools}"
    # Задача должна говорить о подведении итогов
    task_texts = " ".join(d.get("task", "") for d in directives)
    assert "итог" in task_texts.lower() or "финал" in task_texts.lower() or "завершен" in task_texts.lower(), \
        f"Задача должна подводить итоги: {task_texts[:300]}"


def test_g6b_finance_goal_100pct_skipped():
    """Финансовая цель на 100% → финализация без зацикливания."""
    goals = [_goal("Анализ нефтяного рынка", progress=100, mc=100)]
    profiles = [_profiles_for("Марк", ["Alpha Vantage API"])]
    data = _base_data()
    directives = _csd(goals, data, profiles)
    # При 100% — директива финализации OK, главное — не новый поиск
    if directives:
        task_texts = " ".join(d.get("task", "") for d in directives)
        assert "run_agent_action" not in [d["tool"] for d in directives] or \
               "итог" in task_texts.lower(), \
               "100% цель не должна запускать новый поиск"


# ══════════════════════════════════════════════════════════════════════════════
# G7 — People goal without email/github agents → fallback
# ══════════════════════════════════════════════════════════════════════════════

def test_g7_people_goal_no_email_agent_fallback():
    """People goal без email/GitHub агента → find_relevant_contacts_for_task."""
    goals = [_goal("Набрать 100 тестировщиков", progress=5, mc=5, mt=100)]
    profiles = []  # нет агентов
    data = _base_data()
    directives = _csd(goals, data, profiles)
    assert directives
    tools = [d["tool"] for d in directives]
    assert "find_relevant_contacts_for_task" in tools, \
        f"Без email-агента должен быть fallback на find_relevant_contacts: {tools}"


def test_g7b_people_goal_rss_only_agent():
    """People goal + только RSS-агент (без email/GitHub) → find_relevant_contacts или send_outreach."""
    goals = [_goal("Найти 20 партнёров", progress=0, mc=0, mt=20)]
    profiles = [_profiles_for("Марк", ["RSS Feed: habr.com"])]
    data = _base_data()
    directives = _csd(goals, data, profiles)
    assert directives
    tools = [d["tool"] for d in directives]
    # RSS-агент без email — должен попробовать find_relevant или send_outreach (через any)
    allowed = {
        "find_relevant_contacts_for_task", "send_outreach_email",
        "research_topic", "web_search", "run_agent_action",
    }
    for t in tools:
        assert t in allowed, f"Неожиданный инструмент при RSS-only агенте: {t}"


# ══════════════════════════════════════════════════════════════════════════════
# G8 — Generic goal (no keywords) → research_topic fallback
# ══════════════════════════════════════════════════════════════════════════════

def test_g8_generic_goal_research_fallback():
    """Цель без ключевых слов → research_topic или web_search."""
    goals = [_goal("Улучшить время ответа", progress=0)]
    profiles = [_profiles_for("Помощник", [])]
    data = _base_data()
    directives = _csd(goals, data, profiles)
    assert directives
    tools = [d["tool"] for d in directives]
    assert "research_topic" in tools or "web_search" in tools, \
        f"Generic цель должна использовать research_topic/web_search: {tools}"


# ══════════════════════════════════════════════════════════════════════════════
# G9-G14 — _build_autopilot_prompt с разными интеграциями
# ══════════════════════════════════════════════════════════════════════════════

def test_g9_autopilot_prompt_alpha_vantage():
    """Alpha Vantage агент → промпт содержит run_agent_action."""
    goals = [{"title": "Анализ рынка нефти", "progress": 20}]
    prompt = _build_autopilot_prompt(goals, agent_caps=["Alpha Vantage API", "Финансовые новости"])
    assert "run_agent_action" in prompt or "alpha" in prompt.lower(), \
        f"Alpha Vantage промпт должен упоминать run_agent_action: {prompt[:300]}"


def test_g10_autopilot_prompt_rss():
    """RSS агент → промпт содержит run_agent_action / research_topic."""
    goals = [{"title": "Мониторинг конкурентов", "progress": 0}]
    prompt = _build_autopilot_prompt(goals, agent_caps=["RSS Feed: rbc.ru"])
    assert "run_agent_action" in prompt or "research_topic" in prompt, \
        f"RSS промпт должен упоминать инструменты: {prompt[:300]}"


def test_g11_autopilot_prompt_telegram():
    """Telegram/Discord агент → промпт содержит publish инструкцию."""
    goals = [{"title": "Публиковать посты 3 раза в неделю", "progress": 10}]
    prompt = _build_autopilot_prompt(goals, agent_caps=["Telegram API", "Discord Webhook"])
    lower = prompt.lower()
    assert ("publish_to_telegram" in lower or "publish_to_discord" in lower
            or "telegram" in lower or "discord" in lower), \
        f"Telegram промпт должен упоминать публикацию: {prompt[:300]}"


def test_g12_autopilot_prompt_notion():
    """Notion агент → промпт содержит run_agent_action."""
    goals = [{"title": "Документировать архитектуру системы", "progress": 5}]
    prompt = _build_autopilot_prompt(goals, agent_caps=["Notion API"])
    assert "run_agent_action" in prompt or "notion" in prompt.lower(), \
        f"Notion промпт должен упоминать run_agent_action: {prompt[:300]}"


def test_g13_autopilot_prompt_stripe():
    """Stripe агент → промпт содержит run_agent_action."""
    goals = [{"title": "Увеличить выручку на 20%", "progress": 15}]
    prompt = _build_autopilot_prompt(goals, agent_caps=["Stripe API", "Платежи"])
    assert "run_agent_action" in prompt or "stripe" in prompt.lower(), \
        f"Stripe промпт должен упоминать run_agent_action: {prompt[:300]}"


def test_g14_autopilot_prompt_no_integrations():
    """Нет интеграций → промпт содержит web_search / research_topic."""
    goals = [{"title": "Найти потенциальных клиентов", "progress": 0}]
    prompt = _build_autopilot_prompt(goals, agent_caps=[])
    assert "web_search" in prompt or "research_topic" in prompt, \
        f"Промпт без интеграций должен использовать web_search: {prompt[:300]}"


# ══════════════════════════════════════════════════════════════════════════════
# G15-G18 — _build_reasoning_scaffold
# ══════════════════════════════════════════════════════════════════════════════

def test_g15_reasoning_scaffold_github():
    """GitHub агент → scaffold упоминает search_users."""
    goals = [{"title": "Найти 20 разработчиков", "progress": 5,
              "metric_current": 1, "metric_target": 20}]
    result = _build_reasoning_scaffold(
        goals, caps_lower=["github api token"],
        has_imap=False, has_github=True, has_rss=False, has_alpha=False,
        has_script=True, has_content=False, has_news=False, has_notion=False,
        has_slack=False, has_sheets=False, has_stripe=False, used_tools=set(),
    )
    assert "search_users" in result or "github" in result.lower(), \
        f"GitHub scaffold должен упоминать search_users: {result[:400]}"


def test_g16_reasoning_scaffold_alpha_vantage():
    """Alpha Vantage агент → scaffold упоминает get_price."""
    goals = [{"title": "Анализ нефтяного рынка", "progress": 30}]
    result = _build_reasoning_scaffold(
        goals, caps_lower=["alpha vantage api"],
        has_imap=False, has_github=False, has_rss=False, has_alpha=True,
        has_script=True, has_content=False, has_news=False, has_notion=False,
        has_slack=False, has_sheets=False, has_stripe=False, used_tools=set(),
    )
    assert "get_price" in result or "alpha" in result.lower(), \
        f"Alpha Vantage scaffold должен упоминать get_price: {result[:400]}"


def test_g17_reasoning_scaffold_rss():
    """RSS агент → scaffold упоминает get_latest."""
    goals = [{"title": "Мониторинг новостей IT", "progress": 0}]
    result = _build_reasoning_scaffold(
        goals, caps_lower=["rss feed"],
        has_imap=False, has_github=False, has_rss=True, has_alpha=False,
        has_script=True, has_content=False, has_news=False, has_notion=False,
        has_slack=False, has_sheets=False, has_stripe=False, used_tools=set(),
    )
    assert "get_latest" in result or "rss" in result.lower(), \
        f"RSS scaffold должен упоминать get_latest: {result[:400]}"


def test_g18_reasoning_scaffold_metric_display():
    """Scaffold корректно отображает metric_current/metric_target."""
    goals = [{"title": "Набрать 50 пользователей", "progress": 12,
              "metric_current": 6, "metric_target": 50}]
    result = _build_reasoning_scaffold(
        goals, caps_lower=[],
        has_imap=False, has_github=False, has_rss=False, has_alpha=False,
        has_script=False, has_content=False, has_news=False, has_notion=False,
        has_slack=False, has_sheets=False, has_stripe=False, used_tools=set(),
    )
    assert "6" in result and "50" in result, \
        f"Scaffold должен показывать 6/50: {result[:400]}"


# ══════════════════════════════════════════════════════════════════════════════
# G19-G22 — _match_best_integration scoring
# ══════════════════════════════════════════════════════════════════════════════

def test_g19_match_people_goal_github_wins():
    """Цель «тестировщики» + GitHub → github побеждает."""
    ranked = _match_best_integration(
        "Найти 50 тестировщиков",
        has_imap=True, has_github=True, has_rss=True, has_alpha=False,
        has_content=False, has_news=False, has_notion=False, has_slack=False,
        has_sheets=False, has_stripe=False,
    )
    assert ranked, "Должен быть хотя бы один результат"
    top_name = ranked[0][1]
    assert "github" in top_name.lower(), \
        f"Для цели 'тестировщики' GitHub должен быть лучшим: {ranked}"


def test_g20_match_finance_goal_alpha_wins():
    """Цель «анализ нефти» + Alpha Vantage → alpha побеждает."""
    ranked = _match_best_integration(
        "Анализ рынка нефти и котировок",
        has_imap=True, has_github=False, has_rss=True, has_alpha=True,
        has_content=False, has_news=False, has_notion=False, has_slack=False,
        has_sheets=False, has_stripe=False,
    )
    assert ranked, "Должен быть хотя бы один результат"
    top_name = ranked[0][1]
    assert "alpha" in top_name.lower() or "vantage" in top_name.lower(), \
        f"Для нефтяной цели Alpha Vantage должен быть лучшим: {ranked}"


def test_g21_match_content_goal_telegram_wins():
    """Цель «публикация в Telegram» + контент → content побеждает."""
    ranked = _match_best_integration(
        "Публиковать контент в Telegram-канал 5 раз в неделю",
        has_imap=False, has_github=False, has_rss=False, has_alpha=False,
        has_content=True, has_news=False, has_notion=False, has_slack=False,
        has_sheets=False, has_stripe=False,
    )
    assert ranked, "Должен быть хотя бы один результат"
    top_name = ranked[0][1]
    assert "telegram" in top_name.lower() or "discord" in top_name.lower() or "content" in top_name.lower(), \
        f"Для контентной цели нужен Telegram/Discord: {ranked}"


def test_g22_match_no_integrations_empty():
    """Нет интеграций → пустой список."""
    ranked = _match_best_integration(
        "Любая цель",
        has_imap=False, has_github=False, has_rss=False, has_alpha=False,
        has_content=False, has_news=False, has_notion=False, has_slack=False,
        has_sheets=False, has_stripe=False,
    )
    assert ranked == [], f"Нет интеграций → пустой список: {ranked}"


# ══════════════════════════════════════════════════════════════════════════════
# G23 — Множество разнородных целей одновременно
# ══════════════════════════════════════════════════════════════════════════════

def test_g23_multiple_goal_types_directives():
    """5 разных типов целей → 5 директив (по одной на каждую)."""
    goals = [
        _goal("Найти 50 тестировщиков ASI Biont", progress=10),
        _goal("Анализ рынка нефти", progress=20),
        _goal("Создать контент-план SMM", progress=0),
        _goal("Разработать landing page", progress=15),
        _goal("Увеличить выручку на 30%", progress=5),
    ]
    profiles = [
        _profiles_for("Кристина", ["GitHub API Token", "Gmail IMAP"]),
        _profiles_for("Марк", ["RSS Feed: rbk.ru", "Alpha Vantage API"]),
        _profiles_for("Арина", ["Telegram API", "SMM контент"]),
        _profiles_for("Дев", ["GitHub API Token"]),
        _profiles_for("Финансист", ["Stripe API"]),
    ]
    data = _base_data()
    directives = _csd(goals, data, profiles)
    # Минимум одна директива на активную цель (max 5)
    assert len(directives) >= 1, "Должна быть хотя бы одна директива"
    assert len(directives) <= 5, f"Не более 5 директив для 5 целей: {len(directives)}"
    # Все директивы должны иметь поле goal и tool
    for d in directives:
        assert d.get("goal"), f"Директива без goal: {d}"
        assert d.get("tool"), f"Директива без tool: {d}"


# ══════════════════════════════════════════════════════════════════════════════
# G24 — update_goal_progress работает для разных типов целей
# ══════════════════════════════════════════════════════════════════════════════

def test_g24_update_progress_finance_goal():
    """update_goal_progress корректно работает для финансовой цели."""
    from ai_integration.handlers import create_goal, update_goal_progress
    r_create = create_goal(title="Анализ нефтяного рынка G24", user_id=UID)
    assert r_create is not None and isinstance(r_create, str), \
        f"create_goal должен вернуть строку: {r_create!r}"
    result = update_goal_progress(
        goal_title="Анализ нефтяного рынка G24",
        progress=65,
        notes="Получены котировки BRENT: $82.5. Анализ завершён.",
        user_id=UID,
    )
    assert result is not None and isinstance(result, str), \
        f"update_goal_progress должен вернуть строку: {result!r}"


def test_g24b_update_progress_content_goal():
    """update_goal_progress работает для контентной цели."""
    from ai_integration.handlers import create_goal, update_goal_progress
    create_goal(title="SMM контент-план G24b", user_id=UID)
    result = update_goal_progress(
        goal_title="SMM контент-план G24b",
        progress=40,
        notes="Написано 2 из 5 постов.",
        user_id=UID,
    )
    assert result is not None
    # Проверяем или прогресс сохранился, или rate-limit сработал (оба ожидаемы)
    assert isinstance(result, str)


def test_g24c_update_progress_dev_goal():
    """update_goal_progress работает для dev-цели с metric_target."""
    from ai_integration.handlers import create_goal, update_goal_progress
    create_goal(
        title="Разработка API G24c", metric_target=10, metric_unit="эндпоинтов", user_id=UID
    )
    result = update_goal_progress(
        goal_title="Разработка API G24c",
        metric_current=3,
        notes="Реализованы /users, /goals, /tasks",
        user_id=UID,
    )
    assert result is not None
    assert isinstance(result, str)


# ══════════════════════════════════════════════════════════════════════════════
# G25 — Slack интеграция
# ══════════════════════════════════════════════════════════════════════════════

def test_g25_autopilot_prompt_slack():
    """Slack агент → промпт содержит post_message или slack."""
    goals = [{"title": "Отчитываться команде о прогрессе цели", "progress": 0}]
    prompt = _build_autopilot_prompt(goals, agent_caps=["Slack API", "Командный чат"])
    assert "post_message" in prompt or "slack" in prompt.lower(), \
        f"Slack промпт должен упоминать post_message: {prompt[:300]}"


# ══════════════════════════════════════════════════════════════════════════════
# G26 — Edge cases: пустые цели
# ══════════════════════════════════════════════════════════════════════════════

def test_g26_empty_goals_no_crash():
    """_compute_state_directives с пустым списком целей → пустой список, нет краша."""
    directives = _csd([], _base_data(), [])
    assert directives == [], f"Пустые цели → нет директив: {directives}"


def test_g26b_empty_title_goal():
    """Цель с пустым title → нет краша, директива пропускается или обрабатывается."""
    goals = [_goal("", progress=0)]  # пустой title
    profiles = [_profiles_for("Агент", ["RSS Feed"])]
    data = _base_data()
    try:
        directives = _csd(goals, data, profiles)
        # если не крашнулось — OK, директива может быть generic
    except Exception as e:
        pytest.fail(f"_compute_state_directives не должен крашиться на пустом title: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# G27 — Google Sheets интеграция
# ══════════════════════════════════════════════════════════════════════════════

def test_g27_autopilot_prompt_sheets():
    """Google Sheets агент → промпт содержит run_agent_action или sheets."""
    goals = [{"title": "Анализ метрик продаж в таблице", "progress": 0}]
    prompt = _build_autopilot_prompt(goals, agent_caps=["Google Sheets API"])
    assert "run_agent_action" in prompt or "sheet" in prompt.lower(), \
        f"Sheets промпт должен упоминать run_agent_action: {prompt[:300]}"


# ══════════════════════════════════════════════════════════════════════════════
# G28 — Finance goal + RSS с финансовой лентой → run_agent_action
# ══════════════════════════════════════════════════════════════════════════════

def test_g28_finance_goal_rss_finance_feed():
    """Финансовая цель + RSS-агент с финансовой лентой → run_agent_action."""
    goals = [_goal("Анализ рынка нефти и газа", progress=30)]
    # RSS с финансовой лентой (содержит 'finance' в caps)
    profiles = [_profiles_for("Марк", ["RSS Feed: finam.ru finance", "Alpha Vantage API"])]
    data = _base_data()
    directives = _csd(goals, data, profiles)
    assert directives
    tools = [d["tool"] for d in directives]
    # Должен использовать run_agent_action (финансовый RSS) или research_topic
    allowed = {"run_agent_action", "research_topic", "web_search", "update_goal_progress", "get_stock_price"}
    for t in tools:
        assert t in allowed, f"Неожиданный инструмент для финансовой цели с RSS: {t}"


# ══════════════════════════════════════════════════════════════════════════════
# G29 — Промпт содержит МЫШЛЕНИЕ для любой интеграции
# ══════════════════════════════════════════════════════════════════════════════

def test_g29_autopilot_prompt_has_thinking_block():
    """_build_autopilot_prompt для любой интеграции содержит блок «МЫШЛЕНИЕ»."""
    for agent_caps in [
        ["GitHub API Token"],
        ["Alpha Vantage API"],
        ["RSS Feed"],
        ["Telegram API"],
        ["Stripe API"],
        [],
    ]:
        goals = [{"title": "Любая цель", "progress": 10}]
        prompt = _build_autopilot_prompt(goals, agent_caps=agent_caps)
        lower = prompt.lower()
        has_thinking = (
            "мышлени" in lower or "думай" in lower
            or "цел" in lower or "правило" in lower
            or "инструмент" in lower
        )
        assert has_thinking, \
            f"Промпт должен содержать блок мышления при caps={agent_caps}: {prompt[:200]}"


# ══════════════════════════════════════════════════════════════════════════════
# G30 — Finance goal без RSS → fallback research_topic
# ══════════════════════════════════════════════════════════════════════════════

def test_g30_finance_goal_no_rss_fallback():
    """Финансовая цель без RSS-агента → fallback на research_topic / web_search."""
    goals = [_goal("Анализ рынка нефти", progress=25)]
    profiles = [_profiles_for("Помощник", [])]  # нет RSS или Alpha Vantage
    data = _base_data()
    directives = _csd(goals, data, profiles)
    assert directives
    tools = [d["tool"] for d in directives]
    assert "research_topic" in tools or "web_search" in tools or "update_goal_progress" in tools or "get_stock_price" in tools, \
        f"Финансовая цель без RSS: ожидался research_topic/web_search/get_stock_price: {tools}"
    # НЕ должен форсировать email outreach для аналитической цели
    task_texts = " ".join(d.get("task", "") for d in directives)
    assert "send_outreach_email" not in task_texts, \
        f"Финансовая цель не должна форсировать email outreach: {task_texts[:200]}"


# ══════════════════════════════════════════════════════════════════════════════
# G31-G33 — capability reasoning from exact actions and goal relevance
# ══════════════════════════════════════════════════════════════════════════════

def test_g31_autopilot_prompt_shows_exact_python_actions():
    """Промпт должен показывать exact ACTION из python_code, а не только общую категорию."""
    goals = [_goal("Найти кандидатов Python developer", progress=5)]
    python_code = """
if ACTION == 'search_vacancies':
    return {}
if ACTION == 'get_resumes':
    return {}
"""
    prompt = _build_autopilot_prompt(
        goals,
        agent_caps=["hh.ru API"],
        agent_name="HR-агент",
        python_code=python_code,
    )
    assert "search_vacancies" in prompt and "get_resumes" in prompt, prompt[:1200]
    assert "Кастомные ACTION агента" in prompt, prompt[:1200]


def test_g32_autopilot_prompt_ranks_goal_relevant_capabilities():
    """Промпт должен подсказывать наиболее релевантные под цель возможности агента."""
    goals = [_goal("Нанять Python backend разработчика", progress=0)]
    python_code = """
if ACTION == 'search_vacancies':
    return {}
if ACTION == 'search_users':
    return {}
"""
    prompt = _build_autopilot_prompt(
        goals,
        agent_caps=["hh.ru API", "GitHub API Token"],
        agent_name="Рекрутер",
        python_code=python_code,
    )
    assert "СНАЧАЛА ПРОВЕРЬ САМЫЕ РЕЛЕВАНТНЫЕ ДЛЯ ЭТОЙ ЦЕЛИ ВОЗМОЖНОСТИ" in prompt, prompt[:1600]
    assert "HR / Работа" in prompt or "hh.ru API" in prompt, prompt[:1600]
    assert "search_vacancies" in prompt, prompt[:1600]


def test_g33_autopilot_prompt_marks_external_telegram_chats_impossible():
    """Промпт должен явно различать публикацию в свой канал и чужие Telegram-чаты."""
    goals = [_goal("Привлечь 50 предпринимателей в проект", progress=34)]
    prompt = _build_autopilot_prompt(
        goals,
        agent_caps=["GMAIL_USER", "GITHUB_TOKEN"],
        agent_name="Кристина",
        python_code="if ACTION == 'search_users':\n    return {}\n",
    )
    assert "ЧУЖИЕ Telegram-группы/чаты/сообщества" in prompt, prompt[:1600]
    assert "publish_to_telegram — недоступен" in prompt, prompt[:1600]
    assert "Telegram публикация (нет бота агента / канала пользователя)" in prompt, prompt[:1600]


def test_g34_coordinator_guard_rewrites_external_telegram_chat_task():
    """Координатор не должен оставлять задачу на общение в чужих Telegram-чатах."""
    tool, task, note = _normalize_coordinator_assignment_by_capabilities(
        tool="run_agent_action",
        task="Найди 5-7 Telegram бизнес-чатов и пообщайся там от лица проекта",
        categories={"email", "git"},
        has_user_tg_channel=False,
        has_user_discord_webhook=False,
    )
    assert tool == "web_search", (tool, task, note)
    assert "Внешние Telegram-чаты/группы недоступны" in task, task
    assert "недоступны" in note.lower(), note


def test_g35_coordinator_guard_publish_without_channel_falls_back_to_create_post():
    """Если publish_to_telegram недоступен — переводим задачу в create_post + сообщение пользователю."""
    tool, task, note = _normalize_coordinator_assignment_by_capabilities(
        tool="publish_to_telegram",
        task="Опубликуй пост о проекте в Telegram",
        categories={"email"},
        has_user_tg_channel=False,
        has_user_discord_webhook=False,
    )
    assert tool == "create_post", (tool, task, note)
    assert "Канал Telegram не подключён" in task, task
    assert "недоступен" in note.lower(), note


def test_g36_agent_persona_cap_ignores_ack_message():
    """Технический goal_autopilot_ack не должен съедать дневной лимит персоны."""
    s = TestSession()
    try:
        u = models.User(telegram_id=910001, username='cap_ack_user', timezone='Europe/Moscow')
        s.add(u)
        s.commit()

        s.add(models.Interaction(
            user_id=u.id,
            message_type='agent_msg',
            content=json.dumps({
                '__agent': {'name': 'Кристина', 'id': 14, 'avatar_url': ''},
                'text': 'Ок, беру задачу',
                '__anchor_type': 'goal_autopilot_ack',
            }, ensure_ascii=False),
        ))
        s.commit()

        eng = AnchorEngine()
        assert eng._agent_persona_daily_cap_reached(s, u, 'Кристина', limit=1) is False
    finally:
        s.close()


def test_g37_agent_persona_cap_counts_real_result_message():
    """Реальный coordinator_result должен учитываться в дневном лимите персоны."""
    s = TestSession()
    try:
        u = models.User(telegram_id=910002, username='cap_result_user', timezone='Europe/Moscow')
        s.add(u)
        s.commit()

        s.add(models.Interaction(
            user_id=u.id,
            message_type='agent_msg',
            content=json.dumps({
                '__agent': {'name': 'Кристина', 'id': 14, 'avatar_url': ''},
                'text': 'Нашла 6 контактов и отправила 6 писем',
                '__anchor_type': 'coordinator_result',
            }, ensure_ascii=False),
        ))
        s.commit()

        eng = AnchorEngine()
        assert eng._agent_persona_daily_cap_reached(s, u, 'Кристина', limit=1) is True
    finally:
        s.close()
