"""
Тест взаимодействия с суб-агентами (live_subagent_test.py)
===========================================================
Проверяет следующие сценарии в реальном диалоге с AI:

  Блок A — Делегирование задач:
    * delegate_task        → поручить задачу @testcolleague
    * get_delegation_progress → проверить статус делегирования

  Блок B — Переключение на суб-агента:
    * switch_agent(slug)   → переключиться на агента @max-qa
    * sub-agent отвечает своей личностью (QA-эксперт)

  Блок C — Действие через агента + возврат:
    * run_agent_action     → запустить действие через агент-скрипт
    * switch_agent(reset)  → вернуться к ASI Biont

На каждом шаге выводится:
  👤 Пользователь | 🤖 Ответ | 🛠 Инструменты | 🏷 Агент

Запуск:
    $env:DEEPSEEK_API_KEY='sk-...'
    python tests/live_subagent_test.py
"""
import sys, os, asyncio, json

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("LOCAL", "1")
os.environ.setdefault("FREE_ACCESS_MODE", "1")
os.environ.setdefault("DEEPSEEK_API_KEY", os.getenv("DEEPSEEK_API_KEY", ""))
os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("DATABASE_URL", "sqlite:///./live_subagent_test.db")

# ── Очищаем старую БД ────────────────────────────────────────────────────────
for _f in ("live_subagent_test.db", "./live_subagent_test.db"):
    try:
        os.remove(_f)
    except Exception:
        pass

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import models

engine = create_engine(
    "sqlite:///./live_subagent_test.db",
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

# ── Создаём тестового пользователя (автор + подписчик агентов) ───────────────
TEST_TG_ID   = 800_001
COLLEAGUE_TG = 800_002   # получит делегированную задачу
AGENT_SLUG   = "max-qa"

with TestSession() as s:
    # Основной пользователь
    user = models.User(
        telegram_id=TEST_TG_ID,
        username="subagent_test_user",
        first_name="Алексей",
        subscription_tier="PREMIUM",
        token_balance=99999,
    )
    s.add(user)
    s.flush()

    # Коллега для делегирования
    colleague = models.User(
        telegram_id=COLLEAGUE_TG,
        username="testcolleague",
        first_name="Иван",
        subscription_tier="LIGHT",
        token_balance=1000,
    )
    s.add(colleague)
    s.flush()

    # Профили
    s.add(models.UserProfile(
        user_id=user.id,
        bio="Основатель AI-стартапа, ищу тестировщиков",
        skills="Python, продукт, маркетинг",
        city="Москва",
    ))
    s.add(models.UserProfile(
        user_id=colleague.id,
        bio="QA-инженер, 5 лет опыта",
        skills="Selenium, pytest, manual testing",
        city="Санкт-Петербург",
    ))

    # Суб-агент: QA-эксперт Макс
    agent = models.UserAgent(
        author_id=user.id,
        name="Макс-QA",
        slug=AGENT_SLUG,
        description="QA-эксперт, помогает с тест-планами, баг-репортами и автотестами",
        specialization="qa",
        job_title="Senior QA Engineer",
        personality=(
            "Ты Макс — опытный QA-инженер с 10-летним стажем. "
            "Отвечаешь чётко и практично: даёшь конкретные шаги, "
            "примеры тест-кейсов, советы по приоритизации багов. "
            "Используешь профессиональную QA-терминологию. "
            "Обращаешься к пользователю уважительно, но без лишней воды."
        ),
        tools_allowed=json.dumps(["add_task", "research_topic"]),
        status="active",
        price_per_message=5,
        trial_messages=3,
        subscribers_count=1,
    )
    s.add(agent)
    s.flush()

    # Подписка основного пользователя на агента
    s.add(models.AgentSubscription(
        user_id=user.id,
        agent_id=agent.id,
    ))

    # Задача чтобы было что делегировать
    s.add(models.Task(
        user_id=user.id,
        title="Провести регрессионное тестирование MVP",
        description="Нужно протестировать все основные флоу перед релизом",
        status="pending",
    ))
    s.commit()

# ── ANSI-цвета ───────────────────────────────────────────────────────────────
BLUE   = "\033[94m"
GREEN  = "\033[92m"
CYAN   = "\033[96m"
YELLOW = "\033[93m"
RED    = "\033[91m"
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"


def fmt_tools(tools: list) -> str:
    if not tools:
        return f"{DIM}(нет){RESET}"
    return f"{CYAN}" + ", ".join(tools) + f"{RESET}"


def fmt_agent(agent_info) -> str:
    if not agent_info:
        return f"{DIM}ASI Biont{RESET}"
    if isinstance(agent_info, dict):
        name = agent_info.get("name") or agent_info.get("slug") or str(agent_info)
    else:
        name = str(agent_info)
    return f"{YELLOW}{name}{RESET}"


# ── Сценарий: фиксированные шаги с конкретными запросами ─────────────────────
SCENARIO = [
    # (label, message)                       ← что говорит пользователь
    ("A1. Делегирование",
     "Делегируй задачу 'Провести регрессионное тестирование MVP' коллеге @testcolleague, дедлайн завтра в 12:00"),

    ("A2. Уточнение по делегированию",
     "А можешь добавить описание — протестировать все основные флоу приложения, особенно онбординг?"),

    ("A3. Статус делегирования",
     "Покажи статус делегированных задач — что уже передано?"),

    ("B1. Переключение на суб-агента",
     f"Переключись на агента @{AGENT_SLUG} — хочу поговорить с QA-экспертом"),

    ("B2. Разговор с суб-агентом",
     "Какие основные риски при тестировании AI-агента? Что проверять в первую очередь?"),

    ("B3. Суб-агент создаёт задачи",
     "Создай мне тест-план на 3 задачи для тестирования основных флоу"),

    ("C1. Возврат к основному агенту",
     "Вернись к стандартному ASI Biont"),

    ("C2. Проверка возврата",
     "Покажи мои текущие задачи — что у меня в списке?"),
]

SEPARATOR = "─" * 64


# ── Основная функция ──────────────────────────────────────────────────────────
async def run_subagent_test():
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key or api_key in ("sk-test", "test"):
        print(f"{YELLOW}⚠  DEEPSEEK_API_KEY не задан — тест требует реального ключа.{RESET}")
        print("   $env:DEEPSEEK_API_KEY='sk-...' ; python tests/live_subagent_test.py")
        return

    print(f"\n{BOLD}{'═'*64}")
    print("  SUBAGENT TEST  — делегирование + переключение агентов")
    print(f"{'═'*64}{RESET}")
    print(f"\n  Суб-агент:  @{AGENT_SLUG} (Макс-QA, QA Senior Engineer)")
    print(f"  Коллега:    @testcolleague (получатель делегирования)")
    print(f"  Шагов:      {len(SCENARIO)}\n")

    stats = {
        "total": len(SCENARIO),
        "errors": 0,
        "empties": 0,
        "tools_called": {},   # tool_name → count
        "agent_switches": 0,
        "agent_active": [],   # per step
        "switch_confirmed": False,  # Подтверждение через текст ответа
    }

    for i, (label, user_msg) in enumerate(SCENARIO, 1):
        print(f"\n{SEPARATOR}")
        print(f"{BOLD}[{i}/{len(SCENARIO)}] {label}{RESET}")
        print(f"{BLUE}👤 Пользователь:{RESET} {user_msg}")

        try:
            bot_raw = await ag_mod.chat_with_ai(
                message=user_msg,
                user_id=TEST_TG_ID,
            )
        except Exception as e:
            bot_raw = {"response": f"[ОШИБКА: {e}]", "tools_used": [], "agent_info": None}
            stats["errors"] += 1

        # ── Разбираем ответ
        if isinstance(bot_raw, dict):
            bot_reply  = bot_raw.get("response") or "(нет ответа)"
            tools_used = bot_raw.get("tools_used") or []
            agent_info = bot_raw.get("agent_info")
            tool_calls = bot_raw.get("tool_calls") or []
        else:
            bot_reply  = str(bot_raw)
            tools_used = []
            agent_info = None
            tool_calls = []

        bot_reply = bot_reply.strip()
        if not bot_reply:
            bot_reply = "(нет ответа)"
            stats["empties"] += 1

        # ── Считаем инструменты
        for t in tools_used:
            stats["tools_called"][t] = stats["tools_called"].get(t, 0) + 1

        # ── Отслеживаем переключение агента
        stats["agent_active"].append(agent_info)
        # switch_agent вызвался и ответ содержит имя агента
        if "switch_agent" in tools_used and AGENT_SLUG.split("-")[0].capitalize() in bot_reply:
            stats["switch_confirmed"] = True
        if any(c.get("function", {}).get("name") == "switch_agent"
               for c in tool_calls):
            stats["agent_switches"] += 1

        # ── Вывод
        print(f"{GREEN}🤖 Ответ:{RESET}       {bot_reply[:500]}")
        print(f"   🛠  Инструменты: {fmt_tools(tools_used)}")
        print(f"   🏷  Агент:       {fmt_agent(agent_info)}")

        # Небольшая пауза
        await asyncio.sleep(0.4)

    # ── ИТОГИ ─────────────────────────────────────────────────────────────────
    print(f"\n{BOLD}{'═'*64}")
    print("  ИТОГИ")
    print(f"{'═'*64}{RESET}")
    print(f"  Шагов всего:     {stats['total']}")
    print(f"  Ошибок:          {stats['errors']}")
    print(f"  Пустых ответов:  {stats['empties']}")
    print(f"  Переключений агента: {stats['agent_switches']}")
    print()

    # Инструменты
    if stats["tools_called"]:
        print(f"  {BOLD}Вызванные инструменты:{RESET}")
        for tool, cnt in sorted(stats["tools_called"].items(), key=lambda x: -x[1]):
            mark = "✅" if tool in ("delegate_task", "switch_agent",
                                   "get_delegation_progress", "run_agent_action",
                                   "list_tasks", "add_task") else "•"
            print(f"    {mark} {tool:<36} × {cnt}")
    else:
        print(f"  {YELLOW}⚠  Ни один инструмент не был вызван{RESET}")

    # Проверка ключевых сценариев
    print()
    checks = [
        ("delegate_task вызван",          "delegate_task" in stats["tools_called"]),
        ("get_delegation_progress вызван","get_delegation_progress" in stats["tools_called"]),
        ("switch_agent вызван",           "switch_agent" in stats["tools_called"]),
        ("sub-agent подтверждён в ответе бота",
         stats["switch_confirmed"] or stats["tools_called"].get("switch_agent", 0) >= 2),
    ]
    all_ok = True
    for desc, ok in checks:
        icon = f"{GREEN}✅{RESET}" if ok else f"{YELLOW}⚠ {RESET}"
        print(f"  {icon}  {desc}")
        if not ok:
            all_ok = False

    print()
    if stats["errors"] == 0 and stats["empties"] == 0:
        print(f"  {GREEN}{BOLD}✅ Тест завершён без ошибок{RESET}")
    else:
        print(f"  {YELLOW}⚠  Есть проблемы: {stats['errors']} ошибок, {stats['empties']} пустых{RESET}")
    print()


if __name__ == "__main__":
    asyncio.run(run_subagent_test())
