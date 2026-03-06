"""
Живой тест-диалог: 3 фазы — свободный разговор, командная работа с агентами,
прямое обращение к суб-агентам. AI генерирует реплики "пользователя".

Фазы:
  1. Свободный диалог (3 шага) — общение с ASI, задачи, вопросы
  2. Командная работа (4 шага) — делегирование Кристине и Марку, оценка результатов
  3. Прямое обращение (3 шага) — @Кристина напрямую, возврат к ASI

Запуск:
    python tests/live_dialogue_test.py
"""
import sys, os, asyncio, json, re, time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("LOCAL", "1")
os.environ.setdefault("FREE_ACCESS_MODE", "1")
os.environ.setdefault("DEEPSEEK_API_KEY", os.getenv("DEEPSEEK_API_KEY", ""))
os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("DATABASE_URL", "sqlite:///./live_dialogue_test.db")

# ── Очищаем старую БД ────────────────────────────────────────────────────────
for _f in ("live_dialogue_test.db", "./live_dialogue_test.db"):
    try:
        os.remove(_f)
    except Exception:
        pass

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import models

engine = create_engine(
    "sqlite:///./live_dialogue_test.db",
    connect_args={"check_same_thread": False},
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

# ── Создаём тестового пользователя ──────────────────────────────────────────
TEST_TG_ID = 777_001
with TestSession() as s:
    if not s.query(models.User).filter_by(telegram_id=TEST_TG_ID).first():
        user = models.User(
            telegram_id=TEST_TG_ID,
            username="live_test_user",
            first_name="Алексей",
            subscription_tier="PREMIUM",
            token_balance=99999,
        )
        s.add(user)
        s.flush()
        s.add(models.UserProfile(
            user_id=user.id,
            bio="Основатель AI-стартапа, ищу первых пользователей",
            skills="Python, продукт, маркетинг",
            interests="AI, стартапы, монетизация",
            city="Москва",
        ))
        s.add(models.Goal(
            user_id=user.id,
            title="Найти 50 тестовых пользователей для ASI Biont",
            description="Нужны реальные люди которые попробуют продукт",
            status="active",
        ))
        # Суб-агенты — проверяем командную работу
        s.add(models.UserAgent(
            author_id=user.id,
            name="Кристина",
            slug="test-kristina",
            job_title="Маркетолог и SMM-специалист",
            specialization="маркетинг",
            description="Занимается маркетингом, поиском аудитории, написанием постов и публикациями в соцсетях",
            personality=(
                "Ты Кристина — энергичный маркетолог. Пишешь живо и конкретно. "
                "Предлагаешь идеи для продвижения, пишешь тексты постов, "
                "анализируешь целевую аудиторию. Без воды — сразу к делу."
            ),
            status="active",
            tools_allowed='["add_task", "research_topic", "create_post"]',
        ))
        s.add(models.UserAgent(
            author_id=user.id,
            name="Марк",
            slug="test-mark",
            job_title="Аналитик и исследователь",
            specialization="аналитика",
            description="Проводит исследования рынка, анализирует данные, ищет информацию и готовит отчёты",
            personality=(
                "Ты Марк — вдумчивый аналитик. Даёшь структурированные ответы "
                "с данными и выводами. Всегда предлагаешь конкретные метрики "
                "и следующие шаги. Без лирики — факты и рекомендации."
            ),
            status="active",
            tools_allowed='["add_task", "research_topic"]',
        ))
        s.commit()

# ── Вспомогательный AI-вызов (генерируем сообщение пользователя) ─────────────
async def generate_user_message(history: list[dict], hint: str = "") -> str:
    """Использует DeepSeek для генерации следующего сообщения 'живого пользователя'."""
    from ai_integration.autonomous_agent import _quick_ai_call_raw

    history_text = "\n".join(
        f"{'Пользователь' if m['role'] == 'user' else 'Бот'}: {m['content'][:300]}"
        for m in history[-6:]
    )

    system = (
        "Ты играешь роль реального пользователя — основателя небольшого AI-стартапа, "
        "Алексей, 32 года, Москва. Ты общаешься с AI-ассистентом ASI Biont. "
        "У тебя есть команда агентов: Кристина (маркетолог) и Марк (аналитик). "
        "Отвечай на русском, коротко (1-2 предложения), живо, как в Telegram. "
        "Реагируй конкретно на последний ответ бота. "
        "НЕ придумывай данные которых нет — реагируй на то что реально сказал бот."
    )
    if hint:
        system += f"\n\nВАЖНО: В этой реплике ты должен: {hint}"

    prompt = (
        f"История диалога:\n{history_text}\n\n"
        "Напиши следующую реплику пользователя. "
        "Только текст сообщения, без кавычек и пояснений."
    )

    result = await _quick_ai_call_raw(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        max_tokens=150,
    )
    return (result or "окей, что дальше?").strip().strip('"').strip("'")


# ── Сценарий: 3 фазы ─────────────────────────────────────────────────────────
# Каждый элемент: (label, message | None, hint_for_ai)
# Если message=None → AI генерирует реплику с подсказкой hint
SCENARIO = [
    # ── Фаза 1: Свободный диалог (ASI сам) ──
    ("1.1 Знакомство",
     "Привет! Я запустил AI-продукт, нужно найти первых 50 тестировщиков. Что посоветуешь?",
     None),

    ("1.2 Уточнение",
     None,
     "Попроси конкретный план действий или задай уточняющий вопрос по совету бота"),

    ("1.3 Действие",
     None,
     "Попроси бота создать задачу или записать конкретный шаг из обсуждения"),

    # ── Фаза 2: Командная работа (делегирование агентам) ──
    ("2.1 Делегирование Кристине",
     "Поручи Кристине написать пост для привлечения первых тестировщиков нашего AI-продукта",
     None),

    ("2.2 Делегирование Марку",
     "Попроси Марка исследовать где искать тестировщиков для AI-продуктов — какие площадки и сообщества",
     None),

    ("2.3 Мульти-делегирование",
     "Поручи Кристине и Марку вместе подготовить стратегию выхода на Product Hunt",
     None),

    ("2.4 Оценка результатов",
     None,
     "Оцени что сделали агенты, попроси уточнить или доработать один из результатов"),

    # ── Фаза 3: Прямое обращение к агенту ──
    ("3.1 Обращение к Кристине",
     "@Кристина, а какой tone of voice лучше использовать для постов — формальный или дружеский?",
     None),

    ("3.2 Реакция на ответ агента",
     None,
     "Отреагируй на ответ Кристины — согласись или попроси другой вариант"),

    ("3.3 Возврат к ASI",
     "Подведи итоги — что мы сегодня сделали и какой следующий шаг?",
     None),
]

# ── ANSI-цвета ───────────────────────────────────────────────────────────────
BLUE   = "\033[94m"
GREEN  = "\033[92m"
CYAN   = "\033[96m"
YELLOW = "\033[93m"
RED    = "\033[91m"
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
SEPARATOR = "─" * 60


def _extract_agent_mentions(text: str) -> list[str]:
    """Извлекает имена агентов упомянутых в ответе."""
    agents = []
    for name in ("Кристина", "Марк", "кристина", "марк"):
        if name.lower() in text.lower():
            agents.append(name.capitalize())
    return list(set(agents))


def _detect_delegation(text: str) -> bool:
    """Определяет что в ответе было делегирование (агент выполнил задачу)."""
    markers = [
        "получил поручение", "поручение", "поручил",
        "[кристина]", "[марк]", "выполнено", "выполнил",
        "агент", "передано", "передал", "делегиров",
        "начнёт работать", "начнёт выполнять", "приступ",
        "результат", "отчёт",
    ]
    lower = text.lower()
    return any(m in lower for m in markers)


async def run_dialogue():
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key or api_key == "sk-test":
        print(f"{YELLOW}!  DEEPSEEK_API_KEY не задан — тест требует реального ключа.{RESET}")
        print("   Запусти: $env:DEEPSEEK_API_KEY='sk-...' ; python tests/live_dialogue_test.py")
        return

    total_steps = len(SCENARIO)
    print(f"\n{BOLD}{'='*60}")
    print(f"  LIVE DIALOGUE TEST — {total_steps} шагов, 3 фазы")
    print(f"  Фаза 1: Свободный диалог | Фаза 2: Команда | Фаза 3: Прямое")
    print(f"{'='*60}{RESET}\n")

    history: list[dict] = []
    results: list[dict] = []       # [{step, label, user_msg, bot_reply, agents, delegated, time_s, error}]

    for step_idx, (label, fixed_msg, hint) in enumerate(SCENARIO, 1):
        phase = label[0]  # '1', '2', '3'
        phase_names = {'1': 'СВОБОДНЫЙ ДИАЛОГ', '2': 'КОМАНДА АГЕНТОВ', '3': 'ПРЯМОЕ ОБРАЩЕНИЕ'}

        # Заголовок фазы при первом шаге
        if label.endswith('.1'):
            phase_name = phase_names.get(phase, '')
            print(f"\n{BOLD}{CYAN}  ── Фаза {phase}: {phase_name} {'─'*30}{RESET}")

        # ── Получаем сообщение пользователя ──────────────────────────────
        if fixed_msg:
            user_msg = fixed_msg
        else:
            print(f"  {DIM}[генерирую реплику...]{RESET}", end="\r", flush=True)
            try:
                user_msg = await generate_user_message(history, hint or "")
            except Exception as e:
                user_msg = f"ок, что дальше? (gen error: {e})"

        print(f"\n{SEPARATOR}")
        print(f"{BOLD}[{label}]{RESET}")
        print(f"{BLUE}  >>> Пользователь:{RESET} {user_msg}")

        history.append({"role": "user", "content": user_msg})

        # ── Получаем ответ бота ──────────────────────────────────────────
        t0 = time.time()
        error = False
        try:
            bot_raw = await ag_mod.chat_with_ai(
                message=user_msg,
                user_id=TEST_TG_ID,
            )
        except Exception as e:
            bot_raw = f"[ОШИБКА: {e}]"
            error = True
        elapsed = time.time() - t0

        if not bot_raw:
            bot_reply = "(нет ответа)"
            error = True
        elif isinstance(bot_raw, dict):
            bot_reply = bot_raw.get("response") or str(bot_raw)
        elif isinstance(bot_raw, list):
            bot_reply = " | ".join(str(x) for x in bot_raw if x)
        else:
            bot_reply = str(bot_raw)
        bot_reply = bot_reply.strip()

        # Определяем упоминания агентов и делегирования
        mentioned_agents = _extract_agent_mentions(bot_reply)
        was_delegated = _detect_delegation(bot_reply)

        # Выводим ответ
        agent_badge = ""
        if mentioned_agents:
            agent_badge = f" {CYAN}[агенты: {', '.join(mentioned_agents)}]{RESET}"
        if was_delegated:
            agent_badge += f" {YELLOW}[делегирование]{RESET}"

        print(f"{GREEN}  <<< ASI Biont:{RESET}  {bot_reply[:500]}")
        if len(bot_reply) > 500:
            print(f"      {DIM}... ещё {len(bot_reply)-500} символов{RESET}")
        print(f"  {DIM}({elapsed:.1f}s){agent_badge}{RESET}")

        history.append({"role": "assistant", "content": bot_reply})
        results.append({
            "step": step_idx,
            "label": label,
            "user_msg": user_msg,
            "bot_reply": bot_reply,
            "agents": mentioned_agents,
            "delegated": was_delegated,
            "time_s": elapsed,
            "error": error,
        })

        await asyncio.sleep(0.3)

    # ── Проверяем что записалось в БД ─────────────────────────────────────────
    db_stats = {}
    with TestSession() as s:
        db_stats["tasks"] = s.query(models.Task).filter_by(
            user_id=s.query(models.User.id).filter_by(telegram_id=TEST_TG_ID).scalar()
        ).count()
        uid = s.query(models.User.id).filter_by(telegram_id=TEST_TG_ID).scalar()
        db_stats["interactions"] = s.query(models.Interaction).filter_by(user_id=uid).count()
        try:
            from models import AgentActivityLog
            db_stats["agent_activity"] = s.query(AgentActivityLog).filter_by(user_id=uid).count()
        except Exception:
            db_stats["agent_activity"] = 0

    # ── Итоги ─────────────────────────────────────────────────────────────────
    print(f"\n{BOLD}{'='*60}")
    print("  ИТОГИ")
    print(f"{'='*60}{RESET}")

    errors = sum(1 for r in results if r["error"])
    empties = sum(1 for r in results if r["bot_reply"] == "(нет ответа)")
    delegations = sum(1 for r in results if r["delegated"])
    agent_steps = sum(1 for r in results if r["agents"])
    avg_time = sum(r["time_s"] for r in results) / len(results) if results else 0

    print(f"  Шагов:                {total_steps}")
    print(f"  Ошибок:               {errors}")
    print(f"  Пустых ответов:       {empties}")
    print(f"  Делегирований:        {delegations}")
    print(f"  Упоминаний агентов:   {agent_steps}")
    print(f"  Среднее время ответа: {avg_time:.1f}s")

    print(f"\n  {BOLD}БД:{RESET}")
    print(f"  Задач создано:        {db_stats['tasks']}")
    print(f"  Сообщений в истории:  {db_stats['interactions']}")
    print(f"  Активность агентов:   {db_stats['agent_activity']}")

    # ── Оценка по фазам ──────────────────────────────────────────────────────
    print(f"\n  {BOLD}ОЦЕНКА ПО ФАЗАМ:{RESET}")

    phase1 = [r for r in results if r["label"].startswith("1.")]
    phase2 = [r for r in results if r["label"].startswith("2.")]
    phase3 = [r for r in results if r["label"].startswith("3.")]

    p1_ok = all(not r["error"] for r in phase1)
    p2_delegated = sum(1 for r in phase2 if r["delegated"])
    p2_agents = set()
    for r in phase2:
        p2_agents.update(r["agents"])
    p3_agents = set()
    for r in phase3:
        p3_agents.update(r["agents"])

    status = lambda ok: f"{GREEN}OK{RESET}" if ok else f"{RED}FAIL{RESET}"

    print(f"  Фаза 1 (диалог):     {status(p1_ok)} — {'без ошибок' if p1_ok else 'есть ошибки'}")
    print(f"  Фаза 2 (команда):    {status(p2_delegated >= 2)} — {p2_delegated} делегирований, агенты: {', '.join(p2_agents) or 'нет'}")
    print(f"  Фаза 3 (прямое):     {status(bool(p3_agents))} — агенты: {', '.join(p3_agents) or 'нет'}")

    all_ok = p1_ok and p2_delegated >= 2 and bool(p3_agents) and errors == 0
    if all_ok:
        print(f"\n  {GREEN}{BOLD}>>> Все фазы пройдены успешно{RESET}")
    else:
        issues = []
        if not p1_ok:
            issues.append("ошибки в свободном диалоге")
        if p2_delegated < 2:
            issues.append(f"мало делегирований ({p2_delegated}/2+)")
        if not p3_agents:
            issues.append("прямое обращение не сработало")
        print(f"\n  {YELLOW}!!! Требует внимания: {'; '.join(issues)}{RESET}")

    print()


if __name__ == "__main__":
    asyncio.run(run_dialogue())
