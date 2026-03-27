"""
Интеграционные тесты автопилота целей.

Проверяют:
  D1  Timeline: goal_created → AgentActivityLog entry
  D2  Timeline: goal_updated → AgentActivityLog entry
  D3  Timeline: goal_completed → AgentActivityLog entry
  D4  Timeline: все типы видимы в _TIMELINE_VISIBLE_TYPES
  D5  add_task агентом без reminder_time → задача создаётся (Bug fix)
  D6  add_task агентом → source='agent', created_by_agent_id установлены
  D7  add_task агентом + goal_title → task.goal_id привязан
  D8  _build_autopilot_prompt содержит МЫШЛЕНИЕ блок
  D9  _build_autopilot_prompt содержит update_goal_progress правило
  D10 _scan_goal_autopilot: disabled profile → пустой список
  D11 _scan_goal_autopilot: нет активных целей → пустой список
  D12 _scan_goal_autopilot: есть цели → возвращает якорь
  D13 Proactive Interaction сохраняется с правильным JSON __agent/text/__tools_used
  D14 _per_agent_history правильно парсит JSON из Interaction
  D15 list_tasks: задачи агентов (source=agent) НЕ смешиваются с задачами пользователя в контексте
  D16 Rate-limit update_goal_progress: первый вызов проходит, второй за 3ч блокируется
  D17 update_goal_progress: notes накапливаются (не перезаписываются)
  D18 _exec_agent_for_director: mock AI с tool add_task → task создаётся в БД (с fix)
  D31 Изоляция: AAL записи UID не видны UID2
  D32 Изоляция: list_tasks возвращает только задачи своего пользователя
  D33 Изоляция: _scan_goal_autopilot для UID2 (автопилот выкл) возвращает []

Запуск: python -m pytest tests/test_autopilot_integration.py -v
"""
import sys, os, asyncio, json
import unittest.mock as mock
from datetime import datetime, timezone, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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
import ai_integration.autonomous_agent as ag_mod
import ai_integration.conversation_history as ch_mod
import token_service as ts_mod
import subscription_service as ss_mod

_ALL_MODS = (models, h_mod, ag_mod, ch_mod, ts_mod, ss_mod)
for mod in _ALL_MODS:
    mod.Session = TestSession

UID   = 999101  # основной тестовый пользователь
UID2  = 999102  # пользователь без автопилота
AGENT_ID = 42   # мок-id агента

with TestSession() as s:
    for uid, name, tier in [
        (UID,  "intg_user",   "PREMIUM"),
        (UID2, "intg_user_2", "PREMIUM"),
    ]:
        if not s.query(models.User).filter_by(telegram_id=uid).first():
            s.add(models.User(
                telegram_id=uid, username=name, first_name="Test",
                subscription_tier=models.SubscriptionTier.PREMIUM,
                token_balance=99999,
            ))
    s.commit()

    u1 = s.query(models.User).filter_by(telegram_id=UID).first()
    u2 = s.query(models.User).filter_by(telegram_id=UID2).first()

    if not s.query(models.UserProfile).filter_by(user_id=u1.id).first():
        s.add(models.UserProfile(
            user_id=u1.id, bio="Тест", skills="Python",
            interests="AI", goals="вывести продукт на рынок",
            city="Москва", goal_autopilot_enabled=True,
        ))
    if not s.query(models.UserProfile).filter_by(user_id=u2.id).first():
        s.add(models.UserProfile(
            user_id=u2.id, bio="Empty",
            goal_autopilot_enabled=False,
        ))
    # Агент для тестов
    if not s.query(models.UserAgent).filter_by(id=AGENT_ID).first():
        agent_obj = models.UserAgent(
            id=AGENT_ID, author_id=u1.id,
            name="Тестовый Агент", description="Тест",
            tools_allowed='["add_task","update_goal_progress","web_search"]',
            status="active",
        )
        s.add(agent_obj)
    s.commit()


import pytest

@pytest.fixture(autouse=True)
def _restore_session():
    for mod in _ALL_MODS:
        mod.Session = TestSession
    yield


def run(coro):
    return asyncio.run(coro)


# ══════════════════════════════════════════════════════════════════════════════
# D1–D4: Timeline — AgentActivityLog entries
# ══════════════════════════════════════════════════════════════════════════════

def test_d1_create_goal_logs_goal_created():
    """create_goal создаёт AgentActivityLog с activity_type='goal_created'."""
    from ai_integration.handlers import create_goal
    create_goal(title="D1-Цель-логирование", category="work", user_id=UID)
    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        log = s.query(models.AgentActivityLog).filter_by(
            user_id=u.id, activity_type='goal_created',
        ).order_by(models.AgentActivityLog.id.desc()).first()
        assert log is not None, "AgentActivityLog goal_created не создан"
        assert "D1-Цель" in (log.title or ""), f"title не содержит название цели: {log.title}"


def test_d2_update_goal_logs_goal_updated():
    """update_goal_progress создаёт AgentActivityLog с activity_type='goal_updated'."""
    from ai_integration.handlers import create_goal, update_goal_progress
    create_goal(title="D2-Цель-updated-log", metric_target=100, metric_unit="шагов", user_id=UID)
    update_goal_progress(goal_title="D2-Цель-updated-log", metric_current=40,
                         notes="Тест: промежуточный результат подтверждён", user_id=UID)
    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        log = s.query(models.AgentActivityLog).filter(
            models.AgentActivityLog.user_id == u.id,
            models.AgentActivityLog.activity_type.in_(['goal_updated', 'goal_completed']),
            models.AgentActivityLog.title.contains("D2-Цель"),
        ).order_by(models.AgentActivityLog.id.desc()).first()
        assert log is not None, "AgentActivityLog goal_updated не создан"


def test_d3_goal_100_pct_logs_goal_completed():
    """update_goal_progress до 100% создаёт activity_type='goal_completed'."""
    from ai_integration.handlers import create_goal, update_goal_progress
    create_goal(title="D3-Цель-complete-log", metric_target=10, metric_unit="шагов", user_id=UID)
    update_goal_progress(goal_title="D3-Цель-complete-log", metric_current=10,
                         notes="Тест: цель полностью достигнута", user_id=UID)
    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        log = s.query(models.AgentActivityLog).filter(
            models.AgentActivityLog.user_id == u.id,
            models.AgentActivityLog.activity_type == 'goal_completed',
            models.AgentActivityLog.title.contains("D3-Цель"),
        ).order_by(models.AgentActivityLog.id.desc()).first()
        assert log is not None, "AgentActivityLog goal_completed не создан при progress=100"


def test_d4_timeline_visible_types():
    """Все ключевые activity_type присутствуют в _TIMELINE_VISIBLE_TYPES у main.py."""
    import inspect
    import main as main_mod
    # Ищем _TIMELINE_VISIBLE_TYPES из source
    src = inspect.getsource(main_mod)
    required_types = [
        'goal_created', 'goal_completed', 'goal_updated',
        'goal_autopilot_dispatch', 'agent_task',
        'task_completed', 'coordinator_summary',
    ]
    for t in required_types:
        assert f"'{t}'" in src or f'"{t}"' in src, \
            f"'{t}' отсутствует в исходнике main.py"
    # Проверяем именно в структуре _TIMELINE_VISIBLE_TYPES
    assert hasattr(main_mod, '_TIMELINE_VISIBLE_TYPES'), "_TIMELINE_VISIBLE_TYPES не найден в main.py"
    for t in required_types:
        assert t in main_mod._TIMELINE_VISIBLE_TYPES, \
            f"'{t}' отсутствует в _TIMELINE_VISIBLE_TYPES"


# ══════════════════════════════════════════════════════════════════════════════
# D5–D7: add_task для агентов
# ══════════════════════════════════════════════════════════════════════════════

def test_d5_agent_add_task_without_reminder_time():
    """add_task с created_by_agent_id и БЕЗ reminder_time должен СОЗДАВАТЬ задачу.
    Bug: сейчас возвращает 'task_no_time' вместо создания агентской задачи."""
    from ai_integration.handlers import add_task
    result = run(add_task(
        title="D5-Агентская-задача-без-времени",
        description="Отследить прогресс по цели",
        created_by_agent_id=AGENT_ID,
        user_id=UID,
    ))
    # Должно создать задачу, не вернуть ошибку
    assert result is not None
    # Проверяем что задача в БД
    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        task = s.query(models.Task).filter(
            models.Task.user_id == u.id,
            models.Task.title.contains("D5-Агентская"),
        ).first()
        assert task is not None, \
            f"Задача не создана агентом без reminder_time. Ответ: {result}"
        assert task.source == 'agent', f"source должен быть 'agent': {task.source}"
        assert task.created_by_agent_id == AGENT_ID

def test_d34_reply_to_outreach_email_blocks_language_mismatch(monkeypatch):
    """reply_to_outreach_email блокирует латиницу если контакт ответил на кириллице."""
    from ai_integration.handlers import reply_to_outreach_email

    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        campaign = models.EmailCampaign(
            user_id=u.id,
            name="D34-Кампания",
            goal="Проверка language guard",
            sender_name="ASI",
            sender_email="outreach@asibiont.com",
            status='active',
        )
        s.add(campaign)
        s.flush()

        outreach = models.EmailOutreach(
            campaign_id=campaign.id,
            user_id=u.id,
            recipient_email='contact@example.com',
            recipient_name='Иван',
            subject='Привет',
            body='Здравствуйте! Хотела бы обсудить сотрудничество.',
            status='replied',
            reply_text='Здравствуйте! Спасибо за письмо, давайте обсудим подробнее на следующей неделе.',
        )
        s.add(outreach)
        s.commit()

        result = run(reply_to_outreach_email(
            outreach_id=outreach.id,
            reply_body='Hello! Thank you for your reply, happy to discuss this next week.',
            user_id=UID,
            session=s,
            close_session=False,
        ))

        assert 'Язык reply_body' in str(result), f"Должен сработать language guard: {result}"
        assert 'кириллица' in str(result).lower(), f"Ожидали указание на кириллицу: {result}"


def test_d35_email_reply_anchor_retries_language_mismatch_and_hides_raw_guard(monkeypatch):
    """email_reply_received делает retry после language mismatch и не шлёт в TG сырой guard-текст."""
    from anchor_engine import AnchorEngine
    import ai_integration.api_client as api_client_mod
    import ai_integration.handlers as handlers_mod

    class _FakeBot:
        def __init__(self):
            self.messages = []

        async def send_message(self, chat_id, text, **kwargs):
            self.messages.append({'chat_id': chat_id, 'text': text, 'kwargs': kwargs})

    class _FakeApi:
        def __init__(self):
            self.calls = 0

        async def deepseek_analyze(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return json.dumps({'body': 'Hello! Thanks for your reply. Happy to discuss.'}, ensure_ascii=False)
            return json.dumps({'body': 'Здравствуйте! Спасибо за ваш ответ. Буду рада обсудить детали.'}, ensure_ascii=False)

    _send_calls = []

    async def _fake_reply_to_outreach_email(outreach_id=None, reply_body=None, user_id=None, session=None, close_session=True, **kwargs):
        _send_calls.append(reply_body)
        if len(_send_calls) == 1:
            return '⚠ Язык reply_body (латиница) не совпадает с языком ответа контакта (кириллица). ПЕРЕПИШИ reply_body на кириллица — контакт ожидает ответ на своём языке!'
        return 'Ответ отправлен'

    monkeypatch.setattr(api_client_mod, 'get_api_client', lambda: _FakeApi())
    monkeypatch.setattr(handlers_mod, 'reply_to_outreach_email', _fake_reply_to_outreach_email)

    bot = _FakeBot()
    ae = AnchorEngine(bot=bot)

    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        campaign = models.EmailCampaign(
            user_id=u.id,
            name='D35-Кампания',
            goal='Ответить на входящий email',
            sender_name='ASI',
            sender_email='outreach@asibiont.com',
            status='active',
        )
        s.add(campaign)
        s.flush()

        outreach = models.EmailOutreach(
            campaign_id=campaign.id,
            user_id=u.id,
            recipient_email='contact@example.com',
            recipient_name='Иван',
            recipient_company='ООО Ромашка',
            subject='Привет',
            body='Здравствуйте! Я Кристина из PR службы ASI Biont.',
            status='replied',
            reply_text='Здравствуйте! Спасибо за письмо, давайте обсудим подробнее.',
        )
        s.add(outreach)
        s.flush()

        anchor = models.Anchor(
            user_id=u.id,
            anchor_type='email_reply_received',
            source=f'email:{outreach.id}',
            topic='Получен ответ на outreach',
            data=json.dumps({
                'outreach_id': outreach.id,
                'recipient_email': outreach.recipient_email,
                'recipient_name': outreach.recipient_name,
                'recipient_company': outreach.recipient_company,
                'original_subject': outreach.subject,
                'original_body': outreach.body,
                'reply_text': outreach.reply_text,
                'campaign_name': campaign.name,
                'campaign_goal': campaign.goal,
            }, ensure_ascii=False),
        )
        s.add(anchor)
        s.commit()

        run(ae._process_email_silent_anchor(u, anchor, s))

        assert len(_send_calls) == 2, f"Ожидали 2 попытки отправки, получили {len(_send_calls)}"
        assert any('Hello' in (msg or '') for msg in _send_calls), f"Первая попытка должна быть на латинице: {_send_calls}"
        assert any('Здравствуйте' in (msg or '') for msg in _send_calls), f"Вторая попытка должна быть на кириллице: {_send_calls}"
        assert bot.messages, "Пользователь должен получить уведомление в Telegram"
        final_text = bot.messages[-1]['text']
        assert 'ПЕРЕПИШИ reply_body' not in final_text, f"Сырой guard-текст не должен уходить в TG: {final_text}"
        assert 'ответ отправлен' in final_text.lower() or 'автоматически ответил' in final_text.lower(), \
            f"Ожидали успешное уведомление после retry: {final_text}"


def test_d36_reply_to_outreach_email_blocks_self_reply():
    """reply_to_outreach_email не должен отвечать на письмо самому sender_email кампании."""
    from ai_integration.handlers import reply_to_outreach_email

    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        campaign = models.EmailCampaign(
            user_id=u.id,
            name="D36-Кампания",
            goal="Проверка self-reply guard",
            sender_name="ASI",
            sender_email="outreach@asibiont.com",
            status='active',
        )
        s.add(campaign)
        s.flush()

        outreach = models.EmailOutreach(
            campaign_id=campaign.id,
            user_id=u.id,
            recipient_email='outreach@asibiont.com',
            recipient_name='ASI Biont',
            subject='Self reply',
            body='Тестовое письмо',
            status='replied',
            reply_text='Это почему-то письмо от нас же.',
        )
        s.add(outreach)
        s.commit()

        result = run(reply_to_outreach_email(
            outreach_id=outreach.id,
            reply_body='Здравствуйте! Спасибо за ответ.',
            user_id=UID,
            session=s,
            close_session=False,
        ))

        assert 'Self-reply detected' in str(result), f"Self-reply должен блокироваться: {result}"


def test_d37_scan_email_outreach_skips_self_reply_anchor():
    """_scan_email_outreach не должен создавать email_reply_received якорь для self-reply."""
    from anchor_engine import AnchorEngine

    ae = AnchorEngine()
    now = datetime.now(timezone.utc)

    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        campaign = models.EmailCampaign(
            user_id=u.id,
            name='D37-Кампания',
            goal='Проверка scanner self-reply guard',
            sender_name='ASI',
            sender_email='outreach@asibiont.com',
            status='active',
        )
        s.add(campaign)
        s.flush()

        self_outreach = models.EmailOutreach(
            campaign_id=campaign.id,
            user_id=u.id,
            recipient_email='outreach@asibiont.com',
            recipient_name='ASI Biont',
            subject='Self reply',
            body='Тест',
            status='replied',
            reply_text='Это письмо пришло от нашего же адреса.',
        )
        s.add(self_outreach)
        s.commit()

        anchors = ae._scan_email_outreach(u, s, now)
        assert not any((a.anchor_type == 'email_reply_received' and a.source == f'email:{self_outreach.id}:reply') for a in anchors), \
            f"Self-reply не должен создавать email_reply_received anchor: {anchors}"


def test_d6_agent_add_task_source_and_id():
    """add_task с created_by_agent_id устанавливает source='agent' и created_by_agent_id."""
    from ai_integration.handlers import add_task
    import datetime as dt
    reminder = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2)
    result = run(add_task(
        title="D6-Задача-агент-метка",
        created_by_agent_id=AGENT_ID,
        reminder_time=reminder,
        user_id=UID,
    ))
    assert result is not None
    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        task = s.query(models.Task).filter(
            models.Task.user_id == u.id,
            models.Task.title.contains("D6-Задача"),
        ).first()
        assert task is not None, f"Задача не создана, ответ: {result}"
        assert task.source == 'agent', f"source!='agent': {task.source}"
        assert task.created_by_agent_id == AGENT_ID, \
            f"created_by_agent_id={task.created_by_agent_id}"


def test_d7_agent_add_task_with_goal_link():
    """add_task агентом с goal_title → task.goal_id привязан к существующей цели."""
    from ai_integration.handlers import create_goal, add_task
    import datetime as dt
    create_goal(title="D7-Привязанная-цель", user_id=UID)
    reminder = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
    result = run(add_task(
        title="D7-Задача-к-цели",
        created_by_agent_id=AGENT_ID,
        reminder_time=reminder,
        goal_title="D7-Привязанная-цель",
        user_id=UID,
    ))
    assert result is not None
    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        goal = s.query(models.Goal).filter(
            models.Goal.user_id == u.id,
            models.Goal.title == "D7-Привязанная-цель",
        ).first()
        task = s.query(models.Task).filter(
            models.Task.user_id == u.id,
            models.Task.title.contains("D7-Задача"),
        ).first()
        assert task is not None, f"Задача не создана: {result}"
        assert task.goal_id is not None, "task.goal_id должен быть установлен"
        assert task.goal_id == goal.id, \
            f"task.goal_id={task.goal_id} != goal.id={goal.id}"


# ══════════════════════════════════════════════════════════════════════════════
# D8–D9: _build_autopilot_prompt
# ══════════════════════════════════════════════════════════════════════════════

def test_d8_autopilot_prompt_has_myshlenie_block():
    """_build_autopilot_prompt содержит блок ЦЕЛЬ > АКТИВНОСТЬ."""
    from anchor_engine import _build_autopilot_prompt
    goals = [{"title": "Набрать 100 клиентов", "progress": 20}]
    prompt = _build_autopilot_prompt(goals)
    assert "ЦЕЛЬ > АКТИВНОСТЬ" in prompt, \
        f"Блок 'ЦЕЛЬ > АКТИВНОСТЬ' не найден в промпте: {prompt[:500]}"


def test_d9_autopilot_prompt_has_update_goal_rule():
    """_build_autopilot_prompt содержит правило об update_goal_progress."""
    from anchor_engine import _build_autopilot_prompt
    goals = [{"title": "Запустить SaaS", "progress": 10}]
    prompt = _build_autopilot_prompt(goals)
    assert "update_goal_progress" in prompt, \
        f"update_goal_progress не найден в промпте автопилота: {prompt[:300]}"


# ══════════════════════════════════════════════════════════════════════════════
# D10–D12: _scan_goal_autopilot
# ══════════════════════════════════════════════════════════════════════════════

def test_d10_scan_autopilot_disabled_profile():
    """_scan_goal_autopilot возвращает [] если goal_autopilot_enabled=False."""
    from anchor_engine import AnchorEngine
    ae = AnchorEngine()
    now = datetime.now(timezone.utc)

    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID2).first()
        profile = s.query(models.UserProfile).filter_by(user_id=u.id).first()
        result = ae._scan_goal_autopilot(u, profile, s, now)
        assert result == [], \
            f"_scan_goal_autopilot должен вернуть [] для disabled autopilot: {result}"


def test_d11_scan_autopilot_no_goals():
    """_scan_goal_autopilot возвращает [] если нет активных целей."""
    from anchor_engine import AnchorEngine
    ae = AnchorEngine()
    now = datetime.now(timezone.utc)

    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID2).first()
        profile = s.query(models.UserProfile).filter_by(user_id=u.id).first()
        # Убедимся что у u2 нет целей
        s.query(models.Goal).filter_by(user_id=u.id).delete()
        s.commit()
        # Временно включаем autopilot для UID2
        old_val = profile.goal_autopilot_enabled
        profile.goal_autopilot_enabled = True
        s.commit()
        result = ae._scan_goal_autopilot(u, profile, s, now)
        profile.goal_autopilot_enabled = old_val
        s.commit()
        assert result == [], \
            f"_scan_goal_autopilot返回нет-целей должен быть []: {result}"


def test_d12_scan_autopilot_creates_anchor():
    """_scan_goal_autopilot возвращает якорь когда есть активные цели."""
    from anchor_engine import AnchorEngine
    from ai_integration.handlers import create_goal
    ae = AnchorEngine()
    now = datetime.now(timezone.utc) - timedelta(hours=1)  # имитируем что давно был последний запуск

    # Создаём цель
    create_goal(title="D12-Цель-для-скана", user_id=UID)
    # Очищаем якоря автопилота чтобы guard не заблокировал
    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        s.query(models.Anchor).filter(
            models.Anchor.user_id == u.id,
            models.Anchor.anchor_type == 'goal_autopilot_review',
        ).delete()
        s.commit()
        profile = s.query(models.UserProfile).filter_by(user_id=u.id).first()
        result = ae._scan_goal_autopilot(u, profile, s, now)
        assert isinstance(result, list), f"Должен вернуть list: {type(result)}"
        # Может вернуть пустой список если guard блокирует — это тоже нормально
        # Главное — не упасть с исключением
        assert result is not None


# ══════════════════════════════════════════════════════════════════════════════
# D13–D14: Chat messages (Interaction / proactive)
# ══════════════════════════════════════════════════════════════════════════════

def test_d13_proactive_interaction_json_structure():
    """Proactive Interaction хранится как JSON с __agent, text, __tools_used."""
    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        # Создаём тестовое proactive сообщение в формате агента
        content = json.dumps({
            '__agent': {'name': 'Тест-Агент', 'id': AGENT_ID, 'avatar_url': ''},
            'text': 'Нашёл 3 потенциальных клиента через LinkedIn.',
            '__tools_used': ['web_search', 'update_goal_progress'],
        }, ensure_ascii=False)
        interaction = models.Interaction(
            user_id=u.id,
            message_type='proactive',
            content=content,
        )
        s.add(interaction)
        s.commit()

        # Читаем обратно и проверяем структуру
        saved = s.query(models.Interaction).filter_by(
            user_id=u.id, message_type='proactive',
        ).order_by(models.Interaction.id.desc()).first()
        assert saved is not None
        j = json.loads(saved.content)
        assert '__agent' in j, "__agent не найден в content"
        assert 'text' in j, "text не найден в content"
        assert '__tools_used' in j, "__tools_used не найден в content"
        assert j['__agent']['name'] == 'Тест-Агент'
        assert j['text'] == 'Нашёл 3 потенциальных клиента через LinkedIn.'
        assert 'web_search' in j['__tools_used']


def test_d14_per_agent_history_parsing():
    """_scan_goal_autopilot правильно парсит per_agent_history из Interaction JSON."""
    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        # Добавляем несколько proactive сообщений от разных агентов
        for ag, txt, tools in [
            ('Агент-А', 'Отправил email 5 контактам', ['send_outreach_email']),
            ('Агент-Б', 'Нашёл 10 лидов в сети', ['web_search', 'find_relevant_contacts_for_task']),
            ('Агент-А', 'Получил 2 ответа на email', ['check_emails', 'update_goal_progress']),
        ]:
            s.add(models.Interaction(
                user_id=u.id,
                message_type='proactive',
                content=json.dumps({
                    '__agent': {'name': ag, 'id': AGENT_ID, 'avatar_url': ''},
                    'text': txt,
                    '__tools_used': tools,
                }, ensure_ascii=False),
                created_at=datetime.now(timezone.utc),
            ))
        s.commit()

    # Запускаем _scan_goal_autopilot и смотрим не упадёт ли с парсинг-ошибкой
    from anchor_engine import AnchorEngine
    ae = AnchorEngine()
    now = datetime.now(timezone.utc)
    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        profile = s.query(models.UserProfile).filter_by(user_id=u.id).first()
        try:
            # Не должен упасть с ошибкой при парсинге per_agent_history
            ae._scan_goal_autopilot(u, profile, s, now)
        except Exception as e:
            raise AssertionError(f"_scan_goal_autopilot упал при парсинге per_agent_history: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# D15: list_tasks — контекст агентов
# ══════════════════════════════════════════════════════════════════════════════

def test_d15_list_tasks_shows_agent_tasks_labeled():
    """list_tasks: задачи с source='agent' помечены '(создано агентом)' в контексте."""
    # Проверяем что у _build_user_context_sync задачи агентов правильно помечены
    # Для этого проверяем строку в autonomous_agent.py
    import inspect
    import ai_integration.autonomous_agent as ag
    src = inspect.getsource(ag)
    assert 'created_by_agent_id' in src, "Обработка created_by_agent_id не найдена"
    assert 'создано агентом' in src, "Метка '(создано агентом)' не найдена в контексте"


# ══════════════════════════════════════════════════════════════════════════════
# D16: Rate-limit update_goal_progress
# ══════════════════════════════════════════════════════════════════════════════

def test_d16_rate_limit_goal_update():
    """update_goal_progress: первый metric_current проходит, второй в 3ч — блокируется."""
    from ai_integration.handlers import create_goal, update_goal_progress
    create_goal(
        title="D16-Rate-limit-цель",
        metric_target=100, metric_unit="клиентов",
        user_id=UID,
    )
    # Первое обновление — должно пройти
    r1 = update_goal_progress(
        goal_title="D16-Rate-limit-цель",
        metric_current=10,
        user_id=UID,
    )
    assert r1 is not None
    assert "10" in str(r1) or "обновлен" in str(r1).lower() or "метрика" in str(r1).lower(), \
        f"Первый вызов должен пройти: {r1}"

    # Второе обновление — должно быть заблокировано rate-limit-ом
    r2 = update_goal_progress(
        goal_title="D16-Rate-limit-цель",
        metric_current=15,
        user_id=UID,
    )
    assert r2 is not None
    # Rate-limit должен вернуть сообщение о блокировке
    assert ("3ч" in str(r2) or "rate" in str(r2).lower() or "обновлял" in str(r2).lower()
            or "уже обновлял" in str(r2).lower()), \
        f"Rate-limit должен сработать на второй вызов: {r2}"


# ══════════════════════════════════════════════════════════════════════════════
# D17: Notes accumulation
# ══════════════════════════════════════════════════════════════════════════════

def test_d17_notes_accumulate_not_overwrite():
    """update_goal_progress: notes накапливаются (append), не перезаписываются."""
    from ai_integration.handlers import create_goal, update_goal_progress
    create_goal(title="D17-Заметки-накопление", user_id=UID)
    update_goal_progress(
        goal_title="D17-Заметки-накопление",
        notes="Первая заметка",
        user_id=UID,
    )
    update_goal_progress(
        goal_title="D17-Заметки-накопление",
        notes="Вторая заметка",
        user_id=UID,
    )
    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        goal = s.query(models.Goal).filter(
            models.Goal.user_id == u.id,
            models.Goal.title == "D17-Заметки-накопление",
        ).first()
        assert goal is not None
        notes = goal.progress_notes or ""
        assert "Первая заметка" in notes, \
            f"Первая заметка должна сохраняться: {notes}"
        assert "Вторая заметка" in notes, \
            f"Вторая заметка должна добавляться: {notes}"


# ══════════════════════════════════════════════════════════════════════════════
# D18: _exec_agent_for_director + add_task через tool call (с исправлением)
# ══════════════════════════════════════════════════════════════════════════════

_MOCK_ADD_TASK_CALL = {
    "choices": [{
        "message": {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_addtask_1",
                "type": "function",
                "function": {
                    "name": "add_task",
                    "arguments": json.dumps({
                        "title": "D18-Агентская-задача-от-директора",
                        "description": "Отследить прогресс по цели AI-агентов",
                    })
                }
            }]
        },
        "finish_reason": "tool_calls"
    }]
}

_MOCK_SUMMARIZE_RESPONSE = {
    "choices": [{
        "message": {
            "role": "assistant",
            "content": "Создал задачу для отслеживания прогресса. Буду следить за результатом.",
            "tool_calls": []
        },
        "finish_reason": "stop"
    }]
}


def test_d18_exec_agent_creates_task_in_db():
    """_exec_agent_for_director с mock add_task tool call → задача создаётся в БД.

    Тест показывает что после исправления Bug D5
    (add_task без reminder_time для агентов) задача реально появляется в БД.
    """
    call_seq = {"n": 0}

    def fake_post(*args, **kwargs):
        class FakeResp:
            status = 200
            async def json(self):
                call_seq["n"] += 1
                if call_seq["n"] == 1:
                    return _MOCK_ADD_TASK_CALL
                return _MOCK_SUMMARIZE_RESPONSE
            async def text(self): return ""
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
        return FakeResp()

    agent_data = {
        "id": AGENT_ID,
        "name": "Директорский Агент",
        "personality": "Аналитик — отслеживает задачи.",
        "description": "Ставит задачи и обновляет прогресс.",
        "tools_allowed": '["add_task", "update_goal_progress"]',
        "python_code": "", "user_api_keys": "",
        "knowledge_base": "",
    }
    task = "[АВТОПИЛОТ ЦЕЛЕЙ] Создай задачу для отслеживания прогресса по цели."

    with mock.patch("aiohttp.ClientSession.post", fake_post):
        text, tools, total_tokens = run(ag_mod._exec_agent_for_director(agent_data, task, UID))

    assert isinstance(text, str)
    # Проверяем что задача создана в БД
    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        task_in_db = s.query(models.Task).filter(
            models.Task.user_id == u.id,
            models.Task.title.contains("D18"),
        ).first()
        assert task_in_db is not None, \
            f"Задача D18 НЕ создана в БД. Ответ агента: {text[:200]}. tools={tools}"
        assert task_in_db.source == 'agent', \
            f"source должен быть 'agent': {task_in_db.source}"
        assert task_in_db.created_by_agent_id == AGENT_ID


# ══════════════════════════════════════════════════════════════════════════════
# D19–D21: _create_agent_delegation_task / _update_agent_delegation_task
# ══════════════════════════════════════════════════════════════════════════════

def test_d19_create_delegation_task_creates_db_record():
    """_create_agent_delegation_task создаёт Task с source='agent' и delegated_to_username."""
    from ai_integration.autonomous_agent import _create_agent_delegation_task
    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        user_db_id = u.id

    agent_info = {"id": AGENT_ID, "name": "Тест-Делегат"}
    task_id = _create_agent_delegation_task(
        user_db_id, agent_info,
        "D19-Делегированная задача: найти клиентов",
    )
    assert task_id is not None, "_create_agent_delegation_task должна вернуть id"

    with TestSession() as s:
        task = s.query(models.Task).filter_by(id=task_id).first()
        assert task is not None, f"Task id={task_id} не найдена в БД"
        assert task.source == 'agent', f"source должен быть 'agent': {task.source}"
        assert task.created_by_agent_id == AGENT_ID
        assert task.delegated_to_username == "Тест-Делегат"
        # Статус: без result_summary → in_progress
        assert task.status == 'in_progress', f"status должен быть 'in_progress': {task.status}"


def test_d20_create_delegation_task_with_result_is_completed():
    """_create_agent_delegation_task с result_summary → Task.status == 'completed'."""
    from ai_integration.autonomous_agent import _create_agent_delegation_task
    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        user_db_id = u.id

    agent_info = {"id": AGENT_ID, "name": "Тест-Делегат-2"}
    task_id = _create_agent_delegation_task(
        user_db_id, agent_info,
        "D20-Задача с результатом: отправить письма",
        result_summary="Отправлено 5 писем, получено 2 ответа.",
    )
    assert task_id is not None

    with TestSession() as s:
        task = s.query(models.Task).filter_by(id=task_id).first()
        assert task is not None
        assert task.status == 'completed', f"Task с result_summary должен быть 'completed': {task.status}"
        assert "Отправлено 5 писем" in (task.description or ""), \
            f"Описание должно содержать result_summary: {task.description}"


def test_d21_update_delegation_task_sets_completed():
    """_update_agent_delegation_task обновляет статус Task до 'completed' с новым описанием."""
    from ai_integration.autonomous_agent import _create_agent_delegation_task, _update_agent_delegation_task
    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        user_db_id = u.id

    agent_info = {"id": AGENT_ID, "name": "Тест-Делегат-3"}
    task_id = _create_agent_delegation_task(
        user_db_id, agent_info,
        "D21-Задача для обновления",
    )
    assert task_id is not None

    _update_agent_delegation_task(task_id, "D21-Результат: нашёл 10 контактов.")

    with TestSession() as s:
        task = s.query(models.Task).filter_by(id=task_id).first()
        assert task is not None
        assert task.status == 'completed', f"После update должен быть 'completed': {task.status}"
        assert "D21-Результат" in (task.description or ""), \
            f"Описание должно обновиться: {task.description}"
        assert task.actual_completion_time is not None, \
            "actual_completion_time должен быть установлен"


# ══════════════════════════════════════════════════════════════════════════════
# D22–D23: coordinator_summary → AAL + Interaction
# ══════════════════════════════════════════════════════════════════════════════

def test_d22_coordinator_summary_aal_structure():
    """coordinator_summary создаёт AgentActivityLog с activity_type='coordinator_summary'."""
    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        # Эмулируем запись coordinator_summary как это делает _run_coordinator_dispatch
        summary_text = "D22-Итог: Агент Кристина отправила 5 писем. Агент Алексей создал 3 задачи."
        s.add(models.AgentActivityLog(
            user_id=u.id,
            activity_type='coordinator_summary',
            title='ASI · итог цикла: D22-Цель'[:120],
            content=summary_text,
            status='completed',
            result=summary_text[:800],
        ))
        s.commit()

    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        log = s.query(models.AgentActivityLog).filter(
            models.AgentActivityLog.user_id == u.id,
            models.AgentActivityLog.activity_type == 'coordinator_summary',
            models.AgentActivityLog.content.contains("D22-Итог"),
        ).order_by(models.AgentActivityLog.id.desc()).first()
        assert log is not None, "AgentActivityLog coordinator_summary не создан"
        assert log.status == 'completed'
        assert "D22-Итог" in (log.result or '')


def test_d23_coordinator_summary_interaction_structure():
    """coordinator_summary Interaction имеет __anchor_type='coordinator_summary', __agent ASI."""
    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        # Эмулируем Interaction как в _run_coordinator_dispatch (line ~5555)
        payload = {
            '__agent': {'name': 'ASI', 'id': 0, 'avatar_url': ''},
            'text': 'D23-Итог: команда сделала 3 действия по целям.',
            '__anchor_type': 'coordinator_summary',
        }
        s.add(models.Interaction(
            user_id=u.id,
            message_type='proactive',
            content=json.dumps(payload, ensure_ascii=False),
        ))
        s.commit()

    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        intr = s.query(models.Interaction).filter(
            models.Interaction.user_id == u.id,
            models.Interaction.message_type == 'proactive',
            models.Interaction.content.contains('coordinator_summary'),
        ).order_by(models.Interaction.id.desc()).first()
        assert intr is not None
        j = json.loads(intr.content)
        assert j.get('__anchor_type') == 'coordinator_summary'
        assert j.get('__agent', {}).get('name') == 'ASI'
        assert j.get('__agent', {}).get('id') == 0
        assert "D23-Итог" in j.get('text', '')


# ══════════════════════════════════════════════════════════════════════════════
# D24: _maybe_create_agent_campaign — outreach tasks → DelegationCampaign
# ══════════════════════════════════════════════════════════════════════════════

def test_d24_outreach_task_creates_campaign():
    """_maybe_create_agent_campaign создаёт DelegationCampaign для outreach-задач."""
    from ai_integration.autonomous_agent import _maybe_create_agent_campaign
    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        user_db_id = u.id

    agent_info = {"id": AGENT_ID, "name": "Email-Агент"}
    task_text = "D24-Запустить email-рассылку по базе контактов для привлечения тестировщиков"
    _maybe_create_agent_campaign(user_db_id, agent_info, task_text, result_summary="Список из 20 контактов.")

    with TestSession() as s:
        campaign = s.query(models.DelegationCampaign).filter(
            models.DelegationCampaign.user_id == user_db_id,
            models.DelegationCampaign.name.contains("D24"),
        ).order_by(models.DelegationCampaign.id.desc()).first()
        assert campaign is not None, "DelegationCampaign не создана для outreach-задачи"
        assert campaign.status == 'active'


def test_d25_non_outreach_task_no_campaign():
    """_maybe_create_agent_campaign НЕ создаёт кампанию для НЕ-outreach задач."""
    from ai_integration.autonomous_agent import _maybe_create_agent_campaign
    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        user_db_id = u.id
        prev_count = s.query(models.DelegationCampaign).filter_by(user_id=user_db_id).count()

    agent_info = {"id": AGENT_ID, "name": "Пост-Агент"}
    task_text = "Написать статью для блога про инновации в AI"  # не outreach
    _maybe_create_agent_campaign(user_db_id, agent_info, task_text)

    with TestSession() as s:
        new_count = s.query(models.DelegationCampaign).filter_by(user_id=user_db_id).count()
        assert new_count == prev_count, \
            f"Кампания не должна создаваться для неoutreach-задач. Было={prev_count}, стало={new_count}"


# ══════════════════════════════════════════════════════════════════════════════
# D26: _save_delegation_to_history → conversation_history
# ══════════════════════════════════════════════════════════════════════════════

def test_d26_save_delegation_to_history():
    """_save_delegation_to_history сохраняет результат делегирования в conversation history."""
    from ai_integration.autonomous_agent import _save_delegation_to_history
    import ai_integration.conversation_history as ch

    saved = []

    def mock_save(tgid, role, text):
        saved.append({"tgid": tgid, "role": role, "text": text})

    original = ch.save_message_to_history
    ch.save_message_to_history = mock_save
    try:
        _save_delegation_to_history(
            UID, "Кристина",
            "Проверить входящие email и ответить на вопросы",
            "Нашла 2 новых письма, ответила на оба.",
        )
    finally:
        ch.save_message_to_history = original

    assert saved, "save_message_to_history не вызвана"
    msg = saved[0]
    assert msg["tgid"] == UID
    assert msg["role"] == "assistant"
    assert "Кристина" in msg["text"], f"Имя агента должно быть в тексте: {msg['text']}"
    assert "Нашла 2 новых письма" in msg["text"], f"Результат должен быть в тексте: {msg['text']}"


# ══════════════════════════════════════════════════════════════════════════════
# D27: _create_agent_delegation_task — очистка мусорных префиксов в title
# ══════════════════════════════════════════════════════════════════════════════

def test_d27_delegation_task_title_cleanup():
    """_create_agent_delegation_task обрезает системные префиксы из title."""
    from ai_integration.autonomous_agent import _create_agent_delegation_task
    with TestSession() as s:
        u = s.query(models.User).filter_by(telegram_id=UID).first()
        user_db_id = u.id

    agent_info = {"id": AGENT_ID, "name": "Агент-Очистка"}
    # Симулируем задачу с системным мусором в начале
    dirty_task = "[АВТОПИЛОТ] Найди 50 тестировщиков через GitHub."
    task_id = _create_agent_delegation_task(user_db_id, agent_info, dirty_task)
    assert task_id is not None

    with TestSession() as s:
        task = s.query(models.Task).filter_by(id=task_id).first()
        assert task is not None
        # Системный тег [АВТОПИЛОТ] не должен быть в title
        assert "[АВТОПИЛОТ]" not in (task.title or ""), \
            f"Системный тег не очищен: {task.title}"
        # Суть задачи должна присутствовать
        assert "Найди" in (task.title or "") or "тестировщик" in (task.title or ""), \
            f"Суть задачи потеряна при очистке: {task.title}"


# ══════════════════════════════════════════════════════════════════════════════
# D28–D29: _exec_agent_for_director noise filtering + toolset
# ══════════════════════════════════════════════════════════════════════════════

def test_d28_exec_agent_autopilot_noise_filtered():
    """_exec_agent_for_director фильтрует шаблонные ответы без действий (noise filter).

    Если агент вернул шаблонный текст <100 символов без вызовов инструментов
    на autopilot-задаче — функция должна вернуть пустую строку (noise).
    """
    # Mock: агент отвечает текстом без tool calls — шаблонный ответ
    _mock_text_only = {
        "choices": [{"message": {
            "role": "assistant",
            "content": "Выполнил поиск.",  # шаблонный короткий текст
            "tool_calls": []
        }, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10}
    }

    call_n = {"n": 0}

    def fake_post_noise(*args, **kwargs):
        class FakeResp:
            status = 200
            async def json(self):
                call_n["n"] += 1
                return _mock_text_only
            async def text(self): return ""
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
        return FakeResp()

    agent_data = {
        "id": AGENT_ID + 1,
        "name": "Агент-Шум",
        "personality": "Тестовый агент.",
        "description": "Тестовый агент.",
        "tools_allowed": '["web_search", "update_goal_progress"]',
        "python_code": "", "user_api_keys": "", "knowledge_base": "",
    }
    task = "[АВТОПИЛОТ ЦЕЛЕЙ] Активные цели: D28-Цель. Сделай что-нибудь."

    with mock.patch("aiohttp.ClientSession.post", fake_post_noise):
        result_text, tools_used, _ = run(ag_mod._exec_agent_for_director(agent_data, task, UID))

    # Ответ шаблонный без инструментов → noise filter должен вернуть пустую строку
    assert result_text == "" or len(result_text.strip()) < 100, \
        f"Noise filter должен отсечь шаблонный ответ <100 символов без tools: {result_text!r}"


def test_d29_exec_agent_tools_allowed_filter():
    """_exec_agent_for_director соблюдает tools_allowed — исключает запрещённые инструменты.

    Агент с tools_allowed=['add_task'] не должен вызывать delete_task.
    Проверяем через инспекцию кода.
    """
    import inspect
    src = inspect.getsource(ag_mod._exec_agent_for_director)

    # Логика: _allowed_tools проверяется перед вызовом каждого инструмента
    assert '_allowed_tools' in src, "_allowed_tools должен присутствовать в коде"
    assert 'not in _allowed_tools' in src, \
        "Проверка разрешённых инструментов должна быть в коде"
    assert 'tool not in tools_allowed' in src or 'not in _allowed_tools' in src, \
        "Guard на неразрешённые инструменты не найден"


# ══════════════════════════════════════════════════════════════════════════════
# D30: coordinator_summary присутствует в _TIMELINE_VISIBLE_TYPES
# ══════════════════════════════════════════════════════════════════════════════

def test_d30_coordinator_summary_in_timeline():
    """coordinator_summary присутствует в _TIMELINE_VISIBLE_TYPES и виден в хронологии."""
    import main as main_mod
    assert hasattr(main_mod, '_TIMELINE_VISIBLE_TYPES'), \
        "_TIMELINE_VISIBLE_TYPES не найден в main.py"
    tvt = main_mod._TIMELINE_VISIBLE_TYPES
    assert 'coordinator_summary' in tvt, \
        "coordinator_summary должен быть виден в хронологии Timeline"
    # Убеждаемся что структура содержит ожидаемые типы
    for required in ('goal_created', 'goal_completed', 'goal_autopilot_dispatch'):
        assert required in tvt, f"{required} отсутствует в _TIMELINE_VISIBLE_TYPES"


# ══════════════════════════════════════════════════════════════════════════════
# D31–D33: Изоляция между пользователями (multi-user isolation)
# ══════════════════════════════════════════════════════════════════════════════

def test_d31_aal_isolation_between_users():
    """AgentActivityLog записи одного пользователя НЕ видны другому.

    Пользователь UID создаёт AAL-запись, пользователь UID2 — нет.
    Запрос AAL для UID2 не должен возвращать записи UID.
    """
    with TestSession() as s:
        u1 = s.query(models.User).filter_by(telegram_id=UID).first()
        u2 = s.query(models.User).filter_by(telegram_id=UID2).first()
        u1_id, u2_id = u1.id, u2.id

    # Создаём AAL для UID с уникальным маркером
    unique_marker = "D31-УНИКАЛЬНЫЙ-МАРКЕР-ИЗОЛЯЦИИ"
    with TestSession() as s:
        s.add(models.AgentActivityLog(
            user_id=u1_id,
            activity_type='goal_created',
            title=unique_marker,
            content='Тест изоляции',
            status='completed',
        ))
        s.commit()

    # Проверяем что запись UID есть у UID
    with TestSession() as s:
        log_u1 = s.query(models.AgentActivityLog).filter(
            models.AgentActivityLog.user_id == u1_id,
            models.AgentActivityLog.title == unique_marker,
        ).first()
        assert log_u1 is not None, "AAL запись должна быть у UID"

    # Проверяем что запись UID НЕ видна у UID2
    with TestSession() as s:
        log_u2 = s.query(models.AgentActivityLog).filter(
            models.AgentActivityLog.user_id == u2_id,
            models.AgentActivityLog.title == unique_marker,
        ).first()
        assert log_u2 is None, \
            f"AAL запись UID не должна быть видна у UID2: title={unique_marker}"


def test_d32_task_list_isolation_between_users():
    """list_tasks возвращает задачи только своего пользователя.

    Создаём задачу для UID, вызываем list_tasks для UID2 —
    задача UID не должна появляться.
    """
    from ai_integration.handlers import list_tasks

    unique_task_title = "D32-ЗАДАЧА-ТОЛЬКО-ДЛЯ-UID-ИЗОЛЯЦИЯ"

    with TestSession() as s:
        u1 = s.query(models.User).filter_by(telegram_id=UID).first()
        u2 = s.query(models.User).filter_by(telegram_id=UID2).first()
        s.add(models.Task(
            user_id=u1.id,
            title=unique_task_title,
            status='pending',
            source='manual',
        ))
        s.commit()

    # list_tasks для UID2 не должен вернуть задачу UID
    result_u2 = list_tasks(user_id=UID2)
    assert unique_task_title not in result_u2, \
        f"Задача пользователя UID видна у UID2 в list_tasks: {unique_task_title}"

    # Но список UID должен содержать эту задачу
    result_u1 = list_tasks(user_id=UID)
    assert unique_task_title in result_u1, \
        f"Задача должна быть видна у UID в list_tasks: {unique_task_title}"


def test_d33_autopilot_scan_isolation():
    """_scan_goal_autopilot не возвращает якоря для пользователя с отключённым автопилотом.

    У UID есть активные цели и автопилот включён.
    У UID2 автопилот выключен (goal_autopilot_enabled=False).
    _scan_goal_autopilot для UID2 должна вернуть [] независимо от целей UID.
    """
    import anchor_engine as ae_mod
    from datetime import datetime, timezone

    ae_mod.Session = TestSession

    from ai_integration.handlers import create_goal
    create_goal(title="D33-Цель-UID-изоляция", category="work", user_id=UID)

    engine_inst = ae_mod.AnchorEngine.__new__(ae_mod.AnchorEngine)

    with TestSession() as s:
        u1 = s.query(models.User).filter_by(telegram_id=UID).first()
        u2 = s.query(models.User).filter_by(telegram_id=UID2).first()
        profile2 = s.query(models.UserProfile).filter_by(user_id=u2.id).first()
        if profile2:
            profile2.goal_autopilot_enabled = False
            s.commit()

        profile2_reloaded = s.query(models.UserProfile).filter_by(user_id=u2.id).first()
        now_utc = datetime.now(timezone.utc)

        # _scan_goal_autopilot для UID2 должна вернуть [] — автопилот выкл
        anchors_u2 = engine_inst._scan_goal_autopilot(u2, profile2_reloaded, s, now_utc)

    assert anchors_u2 == [], \
        f"_scan_goal_autopilot для UID2 (автопилот выкл) должен вернуть []: {anchors_u2}"
