"""
Тест полной цепочки: «Привлеки 50 тестовых пользователей»
Директор → adaptive (агент 1 → роутинг → агент 2) → синтез → execute_next → process_request

Запуск: python tests/test_full_chain.py
"""

import sys, os, asyncio, warnings, re as _re, json as _json, datetime
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

# ── Пользователь с агентами, задачами и целями ─────────────────────────────
with TestSession() as s:
    u = models.User(telegram_id=888001, username="chain_test", first_name="Алексей",
                    subscription_tier="PREMIUM", token_balance=99999,
                    created_at=datetime.datetime.utcnow())
    s.add(u)
    s.flush()

    s.add(models.UserProfile(user_id=u.id, bio="Основатель AI-стартапа",
                             position="CEO", company="BiontAI",
                             skills="Python, AI", interests="AI, Growth",
                             goals="привлечь первых пользователей"))

    # Цель пользователя
    s.add(models.Goal(user_id=u.id, title="Привлечь 1000 пользователей",
                      status="active", priority=5,
                      metric_target=1000, metric_current=0, metric_unit="пользователей"))

    # Задача привязанная к цели
    s.flush()
    goal_id = s.query(models.Goal).filter_by(user_id=u.id).first().id
    s.add(models.Task(user_id=u.id, title="Написать пост для Product Hunt",
                      status="pending", goal_id=goal_id,
                      due_date=datetime.datetime.utcnow() + datetime.timedelta(days=3)))

    # Агенты
    for name, desc, spec in [
        ("Кристина", "Email-маркетолог, рассылки, IMAP/SMTP", "email, маркетинг"),
        ("Марк", "Исследователь рынков, RSS-аналитик", "аналитика, исследования"),
    ]:
        s.add(models.UserAgent(author_id=u.id, name=name, description=desc,
                               specialization=spec,
                               tools_allowed='["web_search","send_email"]',
                               status="active",
                               personality=f"Специалист: {name}",
                               created_at=datetime.datetime.utcnow()))
    s.commit()

TEST_UID = 888001
USER_MSG = "Привлеки 50 тестовых пользователей"

# ── utils ─────────────────────────────────────────────────────────────────────
OK = "\033[92m✅\033[0m"
ER = "\033[91m❌\033[0m"
results = []

def report(label, ok, msg=""):
    print(f"  {OK if ok else ER} {label}" + (f"  →  {str(msg)[:150]}" if msg else ""))
    results.append((label, ok))


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
# ЧАСТЬ 1: Контекст пользователя — задачи и цели загружены
# ═══════════════════════════════════════════════════════════════════════════════
print("\n\033[1;36m═══ ЧАСТЬ 1: Контекст пользователя ═══\033[0m")

with TestSession() as s:
    uid = s.query(models.User).filter_by(telegram_id=TEST_UID).first().id

ctx = ag_mod._build_user_context_sync(uid)
report("Цели загружены в контекст", "ЦЕЛИ:" in ctx and "1000 пользователей" in ctx, ctx[:200])
report("Активные задачи загружены", "АКТИВНЫЕ ЗАДАЧИ:" in ctx and "Product Hunt" in ctx, ctx[ctx.find("АКТИВНЫЕ"):ctx.find("АКТИВНЫЕ")+150] if "АКТИВНЫЕ" in ctx else ctx[-150:])
report("Агенты загружены", "Кристина" in ctx and "Марк" in ctx)
report("Профиль загружен", "CEO" in ctx or "BiontAI" in ctx)


# ═══════════════════════════════════════════════════════════════════════════════
# ЧАСТЬ 2: Директор → adaptive → 2 агента → синтез → execute_next
# ═══════════════════════════════════════════════════════════════════════════════
print("\n\033[1;36m═══ ЧАСТЬ 2: Полная цепочка директора ═══\033[0m")

async def test_full_chain():
    routing_calls = [0]
    agent_calls = []
    interactions_saved = []
    mission_anchor_saved = [False]

    # Перехватываем _quick_ai_call_raw — имитируем LLM для каждого этапа
    async def mock_quick(msgs, max_tokens=300, **kw):
        c = msgs[-1]["content"] if msgs else ""

        # 1) Начальное решение директора: adaptive
        if "ЛОГИКА ПРИНЯТИЯ РЕШЕНИЯ" in c:
            # Проверяем что цели и задачи дошли до промпта
            has_goals = "1000 пользователей" in c or "ЦЕЛИ:" in c
            has_tasks = "Product Hunt" in c or "АКТИВНЫЕ ЗАДАЧИ:" in c
            has_email_instruction = "EMAIL И ПЕРЕПИСКА" in c
            report("Цели дошли до decision prompt", has_goals, c[:200])
            report("Задачи дошли до decision prompt", has_tasks)
            report("Email-инструкция в промпте", has_email_instruction)

            return _json.dumps({
                "action": "adaptive",
                "director_intro": "Отличная задача! Запускаю команду — сначала Марк исследует площадки, потом Кристина подготовит рассылку.",
                "mission_brief": "Привлечь 50 тестовых пользователей через исследование площадок и email-рассылку",
                "first_agent_name": "Марк",
                "first_agent_task": "Исследуй 10 площадок для привлечения тестовых пользователей AI-продукта. Оцени каждую по охвату.",
                "director_message": "Марк, нужен список площадок для привлечения тестеров — Product Hunt, Reddit, Hacker News и другие. Оцени каждую."
            }, ensure_ascii=False)

        # 2) Роутинговый промпт после первого агента
        if "Реши: миссия выполнена" in c or "Реши: миссия" in c:
            routing_calls[0] += 1
            # Проверяем что цели попали в routing prompt
            has_ctx_in_routing = "1000 пользователей" in c or "ЦЕЛИ:" in c or "КОНТЕКСТ О ПОЛЬЗОВАТЕЛЕ" in c
            report("Контекст пользователя в routing prompt", has_ctx_in_routing)

            if routing_calls[0] == 1:
                return _json.dumps({
                    "action": "next",
                    "agent_name": "Кристина",
                    "agent_task": "Подготовь email-шаблон для приглашения тестеров на основе найденных площадок",
                    "director_message": "Кристина, Марк нашёл площадки — подготовь email-шаблон для приглашения тестеров."
                }, ensure_ascii=False)
            return _json.dumps({"action": "finalize"}, ensure_ascii=False)

        # 3) Финальный синтез
        if "Команда агентов отработала" in c or "Подведи итог" in c:
            return "Марк нашёл 10 площадок, Кристина подготовила шаблон. Запускаю рассылку приглашений — первые 50 тестеров получат письма в течение часа."

        return '{"action":"self","team_hint":"—"}'

    # Мок агентского исполнения
    async def mock_exec(ag, task, user_id, dialog_context=""):
        agent_calls.append(ag["name"])
        if ag["name"] == "Марк":
            return (
                "Исследовал 10 площадок:\n"
                "1. Product Hunt — 15K AI-аудитория, бесплатно\n"
                "2. Reddit r/artificial — 2M подписчиков\n"
                "3. Hacker News — 500K visit/day\n"
                "4. BetaList — 50K подписчиков, $99\n"
                "5. IndieHackers — 100K community\n"
                "ПЕРЕДАЮ: Кристине для email-шаблона"
            )
        elif ag["name"] == "Кристина":
            return (
                "Подготовила email-шаблон приглашения:\n"
                "Тема: «Станьте одним из первых 50 тестеров BiontAI»\n"
                "Тело: Привет! Мы нашли вас на [площадка]...\n"
                "CTA: Получить бесплатный доступ → ссылка"
            )
        return "Результат готов."

    def mock_save_interaction(uid, text):
        interactions_saved.append(text)

    # Перехватываем _save_agent_delegation_anchor чтобы проверить mission anchor
    _orig_save_anchor = ag_mod._save_agent_delegation_anchor
    def mock_save_anchor(user_db_id, agent_id, agent_name, task, result_summary, cooldown_hours=2.0):
        if agent_name == "__mission__":
            mission_anchor_saved[0] = True
        return _orig_save_anchor(user_db_id, agent_id, agent_name, task, result_summary, cooldown_hours)

    with patch(ag_mod, "_quick_ai_call_raw", mock_quick), \
         patch(ag_mod, "_exec_agent_for_director", mock_exec), \
         patch(ag_mod, "_save_interaction_for_director", mock_save_interaction), \
         patch(ag_mod, "_save_agent_delegation_anchor", mock_save_anchor):

        result = await ag_mod._office_director_chat(USER_MSG, TEST_UID)

    # ── Проверки ──
    report("Директор вернул dict (не строку/None)", isinstance(result, dict), type(result).__name__)
    report("execute_next=True", isinstance(result, dict) and result.get('execute_next') is True)

    response_text = result.get('response', '') if isinstance(result, dict) else str(result or '')
    report("Синтез содержит действие (запускаю/рассылк)", 
           any(w in response_text.lower() for w in ['запускаю', 'рассылк', 'письм']),
           response_text[:120])
    report("Синтез НЕ спрашивает разрешение",
           not any(w in response_text.lower() for w in ['хочешь', 'запустить?', 'отправить?']),
           response_text[:120])

    report("Марк вызван первым", len(agent_calls) >= 1 and agent_calls[0] == "Марк",
           f"порядок: {agent_calls}")
    report("Кристина вызвана второй", len(agent_calls) >= 2 and agent_calls[1] == "Кристина",
           f"порядок: {agent_calls}")
    report("Роутинг сработал (≥1 вызов)", routing_calls[0] >= 1, f"routing: {routing_calls[0]}")

    report("Mission anchor сохранён", mission_anchor_saved[0])

    report("Director intro показан пользователю",
           any("Запускаю команду" in i for i in interactions_saved),
           str(interactions_saved[:2]))
    report("Director message Марку показан",
           any("Марк" in i and "площадк" in i for i in interactions_saved),
           str([i for i in interactions_saved if "Марк" in i][:1]))
    report("Director message Кристине показан",
           any("Кристина" in i for i in interactions_saved),
           str([i for i in interactions_saved if "Кристина" in i][:1]))

    report("ПЕРЕДАЮ не утекает в финальный синтез",
           "ПЕРЕДАЮ" not in response_text,
           "(чистый вывод)")

asyncio.run(test_full_chain())


# ═══════════════════════════════════════════════════════════════════════════════
# ЧАСТЬ 3: chat_with_ai → execute_next → process_request вызван
# ═══════════════════════════════════════════════════════════════════════════════
print("\n\033[1;36m═══ ЧАСТЬ 3: chat_with_ai → execute_next → process_request ═══\033[0m")

async def test_chat_with_ai_chain():
    process_request_called = [False]
    process_request_instruction = ['']
    director_synthesis = "Марк нашёл площадки, Кристина подготовила шаблон. Запускаю рассылку."

    # Мок _office_director_chat — возвращает dict с execute_next
    async def mock_director(msg, uid, progress_callback=None):
        return {"response": director_synthesis, "execute_next": True}

    # Мок process_request — проверяем что он вызван с правильной инструкцией
    agent = ag_mod.get_autonomous_agent()
    _orig_pr = agent.process_request

    async def mock_process_request(msg, user_id, context=None, db_session=None,
                                    subscription_tier=None, progress_callback=None,
                                    web_context=False, exclude_tools=None):
        process_request_called[0] = True
        process_request_instruction[0] = msg
        return "✅ Кампания рассылки запущена: 50 приглашений отправлено."

    def mock_has_mention(msg):
        return False

    with patch(ag_mod, "_office_director_chat", mock_director), \
         patch(ag_mod, "_has_explicit_mention", mock_has_mention), \
         patch(agent, "process_request", mock_process_request), \
         patch(ag_mod, "_save_interaction_for_director", lambda *a: None):

        result = await ag_mod.chat_with_ai(
            USER_MSG, user_id=TEST_UID,
            subscription_tier="PREMIUM"
        )

    report("chat_with_ai вернул ответ", bool(result and result.get('response')),
           result.get('response', '')[:120] if result else 'None')
    report("process_request ВЫЗВАН (execute_next сработал)",
           process_request_called[0])
    report("Инструкция содержит 'ВЫПОЛНИ'",
           "ВЫПОЛНИ" in process_request_instruction[0] or "вызови" in process_request_instruction[0].lower(),
           process_request_instruction[0][:120])
    report("Инструкция содержит ссылку на инструменты",
           any(t in process_request_instruction[0] for t in ['start_delegation_campaign', 'send_email', 'create_post']),
           process_request_instruction[0][:150])
    report("Финальный ответ включает синтез + execution",
           "Запускаю рассылку" in result.get('response', '') and "Кампания" in result.get('response', ''),
           result.get('response', '')[:150])

asyncio.run(test_chat_with_ai_chain())


# ═══════════════════════════════════════════════════════════════════════════════
# ЧАСТЬ 4: Follow-up «да» → продолжение миссии (НЕ bypass)
# ═══════════════════════════════════════════════════════════════════════════════
print("\n\033[1;36m═══ ЧАСТЬ 4: Follow-up «да» при активной миссии ═══\033[0m")

async def test_followup():
    # Сохраняем mission anchor (как если adaptive только что отработал)
    with TestSession() as s:
        uid = s.query(models.User).filter_by(telegram_id=TEST_UID).first().id

    ag_mod._save_agent_delegation_anchor(
        user_db_id=uid,
        agent_id=0,
        agent_name="__mission__",
        task="Привлеки 50 тестовых пользователей",
        result_summary="Привлечь 50 тестовых пользователей через площадки и email-рассылку",
        cooldown_hours=24,
    )

    all_prompts = []  # собираем ВСЕ вызовы LLM

    async def mock_quick(msgs, max_tokens=300, **kw):
        c = msgs[-1]["content"] if msgs else ""
        all_prompts.append(c)

        # Decision call (первый) — mission context prompt
        if "подтвердил продолжение миссии" in c or "АКТИВНАЯ МИССИЯ" in c:
            return _json.dumps({
                "action": "delegate",
                "agent_name": "Кристина",
                "agent_task": "Отправь первые 10 приглашений из подготовленного шаблона",
                "director_message": "Кристина, запускай первую волну — 10 приглашений!"
            }, ensure_ascii=False)

        # Synthesis after delegate
        if "Подведи итог" in c or "Оцени результат" in c or "Ты поручил агентам" in c:
            return "Кристина отправила 10 приглашений. Запускаю следующую волну."

        return '{"action":"self","team_hint":"—"}'

    async def mock_exec(ag, task, user_id, dialog_context=""):
        return "Отправлено 10 приглашений. Первые ответы ожидаются через 2 часа."

    with patch(ag_mod, "_quick_ai_call_raw", mock_quick), \
         patch(ag_mod, "_exec_agent_for_director", mock_exec), \
         patch(ag_mod, "_save_interaction_for_director", lambda *a: None):

        result = await ag_mod._office_director_chat("да", TEST_UID)

    # Главная проверка: «да» НЕ вернуло None (НЕ bypass)
    report("«да» НЕ bypass при активной миссии", result is not None,
           f"result type: {type(result).__name__}, val: {str(result)[:80]}")

    # Первый вызов LLM должен содержать mission context
    first_prompt = all_prompts[0] if all_prompts else ''
    report("Decision prompt содержит миссию",
           "АКТИВНАЯ МИССИЯ" in first_prompt or "подтвердил продолжение" in first_prompt,
           first_prompt[:150])
    report("Decision prompt НЕ позволяет self",
           "НЕ выбирай self" in first_prompt,
           first_prompt[:200])

asyncio.run(test_followup())


# ═══════════════════════════════════════════════════════════════════════════════
# ЧАСТЬ 5: «нет» при активной миссии → bypass (стоп)
# ═══════════════════════════════════════════════════════════════════════════════
print("\n\033[1;36m═══ ЧАСТЬ 5: «нет» при активной миссии → bypass ═══\033[0m")

async def test_followup_stop():
    result = await ag_mod._office_director_chat("нет", TEST_UID)
    report("«нет» → bypass (None)", result is None, f"result: {result}")

asyncio.run(test_followup_stop())


# ═══════════════════════════════════════════════════════════════════════════════
# ИТОГИ
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "═" * 60)
passed = sum(1 for _, ok in results if ok)
failed = sum(1 for _, ok in results if not ok)
total = len(results)
color = "\033[92m" if failed == 0 else "\033[91m"
print(f"{color}ИТОГО: {passed}/{total} пройдено, {failed} провалено\033[0m")
if failed:
    print("\n\033[91mПровалены:\033[0m")
    for label, ok in results:
        if not ok:
            print(f"  ❌ {label}")
print()

sys.exit(0 if failed == 0 else 1)
