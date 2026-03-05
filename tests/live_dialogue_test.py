"""
Динамический тест-диалог: AI генерирует сообщения "живого пользователя",
бот отвечает через chat_with_ai — 10 шагов без заготовок.

Запуск:
    python tests/live_dialogue_test.py
"""
import sys, os, asyncio, json, re

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
        s.commit()

# ── Вспомогательный AI-вызов (генерируем сообщение пользователя) ─────────────
async def generate_user_message(history: list[dict]) -> str:
    """Использует DeepSeek для генерации следующего сообщения 'живого пользователя'."""
    from ai_integration.autonomous_agent import _quick_ai_call_raw

    history_text = "\n".join(
        f"{'Пользователь' if m['role'] == 'user' else 'Бот'}: {m['content'][:300]}"
        for m in history[-6:]  # последние 6 реплик для контекста
    )

    system = (
        "Ты играешь роль реального пользователя — основателя небольшого AI-стартапа, "
        "Алексей, 32 года, Москва. Ты общаешься с AI-ассистентом ASI Biont. "
        "Твоя цель — найти первых пользователей для своего продукта. "
        "Отвечай на русском, коротко (1-2 предложения), живо, как в Telegram. "
        "Не используй формальный язык. Реагируй конкретно на последний ответ бота. "
        "Можешь соглашаться, уточнять, просить сделать что-то, задавать вопросы, "
        "выражать сомнения или просить конкретики. Веди себя естественно — "
        "иногда кратко ('ок давай', 'а сколько это займёт?'), иногда чуть подробнее. "
        "НЕ придумывай данные которых нет — реагируй на то что реально сказал бот."
    )

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
        max_tokens=120,
    )
    return (result or "окей, что дальше?").strip().strip('"').strip("'")


# ── Основной диалог ──────────────────────────────────────────────────────────
STEPS = 10
SEPARATOR = "─" * 60
BLUE   = "\033[94m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


async def run_dialogue():
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key or api_key == "sk-test":
        print(f"{YELLOW}⚠  DEEPSEEK_API_KEY не задан — тест требует реального ключа.{RESET}")
        print("   Запусти: $env:DEEPSEEK_API_KEY='sk-...' ; python tests/live_dialogue_test.py")
        return

    print(f"\n{BOLD}{'═'*60}")
    print("  LIVE DIALOGUE TEST  — 10 шагов, AI генерирует пользователя")
    print(f"{'═'*60}{RESET}\n")

    history: list[dict] = []   # [{role, content}]
    bot_responses: list[str] = []

    # Стартовое сообщение — тоже генерируем
    first_user_msg = "Привет! Хочу найти первых тестировщиков для своего AI-продукта. С чего начать?"

    for step in range(1, STEPS + 1):
        # ── Получаем сообщение пользователя ──────────────────────────────
        if step == 1:
            user_msg = first_user_msg
        else:
            print(f"  {YELLOW}[генерирую реплику пользователя...]{RESET}   ", end="\r", flush=True)
            try:
                user_msg = await generate_user_message(history)
            except Exception as e:
                user_msg = f"ок, что дальше? (gen error: {e})"

        print(f"\n{SEPARATOR}")
        print(f"{BOLD}[Шаг {step}/{STEPS}]{RESET}")
        print(f"{BLUE}👤 Пользователь:{RESET} {user_msg}")

        history.append({"role": "user", "content": user_msg})

        # ── Получаем ответ бота ───────────────────────────────────────────
        try:
            bot_raw = await ag_mod.chat_with_ai(
                message=user_msg,
                user_id=TEST_TG_ID,
            )
        except Exception as e:
            bot_raw = f"[ОШИБКА: {e}]"

        if not bot_raw:
            bot_reply = "(нет ответа)"
        elif isinstance(bot_raw, dict):
            bot_reply = bot_raw.get("response") or str(bot_raw)
        elif isinstance(bot_raw, list):
            bot_reply = " | ".join(str(x) for x in bot_raw if x)
        else:
            bot_reply = str(bot_raw)
        bot_reply = bot_reply.strip()

        print(f"{GREEN}🤖 ASI Biont:{RESET}  {bot_reply}")

        history.append({"role": "assistant", "content": bot_reply})
        bot_responses.append(bot_reply)

        # Небольшая пауза чтобы не стрессовать API
        await asyncio.sleep(0.5)

    # ── Итоги ─────────────────────────────────────────────────────────────────
    print(f"\n{BOLD}{'═'*60}")
    print("  ИТОГИ")
    print(f"{'═'*60}{RESET}")

    errors   = sum(1 for r in bot_responses if r.startswith("[ОШИБКА"))
    empties  = sum(1 for r in bot_responses if r == "(нет ответа)")
    toolcall = sum(1 for r in bot_responses if any(
        w in r.lower() for w in ["задача", "создал", "записал", "добавил", "нашёл", "закрыл"]
    ))

    print(f"  Шагов:         {STEPS}")
    print(f"  Ошибок:        {errors}")
    print(f"  Пустых:        {empties}")
    print(f"  С действиями:  {toolcall}  (инструменты вызывались)")

    if errors == 0 and empties == 0:
        print(f"\n  {GREEN}{BOLD}✅ Диалог завершён без ошибок{RESET}")
    else:
        print(f"\n  {YELLOW}⚠  Есть {errors + empties} проблемных ответов{RESET}")

    print()


if __name__ == "__main__":
    asyncio.run(run_dialogue())
