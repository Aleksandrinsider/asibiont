"""
Живой тест-диалог: 5 фаз — разнообразные запросы для проверки всех возможностей.

Фазы:
  1. Задачи и планирование (3 шага) — создание задач, список, управление
  2. Исследование и аналитика (3 шага) — web search, research, анализ
  3. Контент и публикации (2 шага) — написание постов, редактура
  4. Агенты и делегирование (3 шага) — прямое обращение, делегирование, командная работа
  5. Голосовые запросы и edge-cases (3 шага) — короткие реплики, неясные запросы, итоги

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
    # ── Фаза 1: Задачи и планирование ──
    ("1.1 Создание задачи",
     "Запиши задачу: подготовить презентацию для инвесторов до пятницы",
     None),

    ("1.2 Список задач",
     "Покажи все мои задачи",
     None),

    ("1.3 Создание цели",
     "Создай цель — выйти на 100 платящих клиентов за 3 месяца",
     None),

    # ── Фаза 2: Исследование и аналитика ──
    ("2.1 Поиск информации",
     "Найди информацию о трендах в AI SaaS в 2026 году",
     None),

    ("2.2 Исследование конкурентов",
     "Исследуй конкурентов в нише AI-ассистентов для бизнеса — кто лидеры, какие фичи",
     None),

    ("2.3 Реакция на исследование",
     None,
     "Задай уточняющий вопрос по результатам исследования или попроси копнуть глубже в конкретном направлении"),

    # ── Фаза 3: Контент и публикации ──
    ("3.1 Написание поста",
     "Напиши пост для телеграм-канала про то как AI меняет продуктивность стартапов",
     None),

    ("3.2 Обновление профиля",
     "Обнови мой профиль — добавь навык 'управление продуктом' и интерес 'нейросети'",
     None),

    # ── Фаза 4: Агенты и делегирование ──
    ("4.1 Делегирование Кристине",
     "Пусть Кристина придумает 5 идей для привлечения пользователей через Instagram",
     None),

    ("4.2 Прямое обращение к Марку",
     "@Марк, проанализируй юнит-экономику: средний чек 500р, CAC предполагаю 300р, LTV нужно рассчитать",
     None),

    ("4.3 Командная задача",
     None,
     "Попроси подвести итоги работы команды агентов или дать им новое общее задание"),

    # ── Фаза 5: Edge cases и итоги ──
    ("5.1 Короткая реплика",
     "спс",
     None),

    ("5.2 Неявный запрос",
     "Что-то чувствую что мы буксуем, нужно ускориться",
     None),

    ("5.3 Итоги дня",
     "Подведи итоги — что сделано, какие задачи висят, что делать завтра",
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
        "delegate_task", "готово",
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
    print(f"  LIVE DIALOGUE TEST — {total_steps} шагов, 5 фаз")
    print(f"  1:Задачи | 2:Исследование | 3:Контент | 4:Агенты | 5:Edge")
    print(f"{'='*60}{RESET}\n")

    history: list[dict] = []
    results: list[dict] = []       # [{step, label, user_msg, bot_reply, agents, delegated, time_s, error}]

    for step_idx, (label, fixed_msg, hint) in enumerate(SCENARIO, 1):
        phase = label[0]  # '1', '2', '3', '4', '5'
        phase_names = {'1': 'ЗАДАЧИ И ПЛАНИРОВАНИЕ', '2': 'ИССЛЕДОВАНИЕ И АНАЛИТИКА', '3': 'КОНТЕНТ И ПУБЛИКАЦИИ', '4': 'АГЕНТЫ И ДЕЛЕГИРОВАНИЕ', '5': 'EDGE CASES И ИТОГИ'}

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
        # Сохраняем сообщение пользователя в Interaction (как в боевом потоке)
        try:
            with TestSession() as _is:
                _iu = _is.query(models.User).filter_by(telegram_id=TEST_TG_ID).first()
                if _iu:
                    _is.add(models.Interaction(user_id=_iu.id, message_type='user', content=user_msg))
                    _is.commit()
        except Exception:
            pass

        t0 = time.time()
        error = False
        # Mock progress_callback для сбора промежуточных сообщений (диалог директора)
        _intermediate_messages = []
        async def _test_progress_callback(text, *, persist=False):
            if persist and text:
                _intermediate_messages.append(text)
        try:
            bot_raw = await ag_mod.chat_with_ai(
                message=user_msg,
                user_id=TEST_TG_ID,
                progress_callback=_test_progress_callback,
            )
        except Exception as e:
            bot_raw = f"[ОШИБКА: {e}]"
            error = True
        elapsed = time.time() - t0

        if not bot_raw:
            bot_reply = "(нет ответа)"
            error = True
        elif isinstance(bot_raw, dict):
            bot_reply = bot_raw.get("response") or ""
            # Если ответ пустой (agent_handled) — берём последний ответ агента из БД
            if not bot_reply.strip():
                try:
                    with TestSession() as _rs:
                        _ru = _rs.query(models.User).filter_by(telegram_id=TEST_TG_ID).first()
                        if _ru:
                            last_ai = _rs.query(models.Interaction).filter(
                                models.Interaction.user_id == _ru.id,
                                models.Interaction.message_type == 'ai'
                            ).order_by(models.Interaction.id.desc()).first()
                            if last_ai and last_ai.content:
                                import json as _rjson
                                try:
                                    _jdata = _rjson.loads(last_ai.content)
                                    if isinstance(_jdata, dict) and 'text' in _jdata:
                                        bot_reply = _jdata['text']
                                    else:
                                        bot_reply = last_ai.content
                                except (ValueError, KeyError):
                                    bot_reply = last_ai.content
                except Exception:
                    pass
            if not bot_reply.strip():
                bot_reply = "(нет ответа)"
                error = True
        elif isinstance(bot_raw, list):
            bot_reply = " | ".join(str(x) for x in bot_raw if x)
        else:
            bot_reply = str(bot_raw)
        bot_reply = bot_reply.strip()

        # Сохраняем ответ бота в Interaction (если не пустой и не сохранён директором)
        if bot_reply and bot_reply != "(нет ответа)":
            try:
                with TestSession() as _as:
                    _au = _as.query(models.User).filter_by(telegram_id=TEST_TG_ID).first()
                    if _au:
                        _as.add(models.Interaction(user_id=_au.id, message_type='ai', content=bot_reply[:500]))
                        _as.commit()
            except Exception:
                pass

        # Определяем упоминания агентов и делегирования
        _all_text = bot_reply + ' ' + ' '.join(_intermediate_messages)
        mentioned_agents = _extract_agent_mentions(_all_text)
        was_delegated = _detect_delegation(_all_text) or len(_intermediate_messages) > 0

        # Выводим промежуточные сообщения (диалог директора)
        if _intermediate_messages:
            for _im in _intermediate_messages:
                _emoji = _im[:2] if _im and _im[0] in '🔄📋' else '  '
                _text = _im[2:].strip() if _emoji.strip() else _im
                print(f"{CYAN}  {_emoji} {_text[:300]}{RESET}")

        # Выводим финальный ответ
        agent_badge = ""
        if mentioned_agents:
            agent_badge = f" {CYAN}[агенты: {', '.join(mentioned_agents)}]{RESET}"
        if was_delegated:
            agent_badge += f" {YELLOW}[делегирование ×{len(_intermediate_messages)}]{RESET}"

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
            "intermediate_count": len(_intermediate_messages),
            "time_s": elapsed,
            "error": error,
        })

        await asyncio.sleep(0.3)

    # ── Проверяем что записалось в БД ─────────────────────────────────────────
    db_stats = {}
    with TestSession() as s:
        uid = s.query(models.User.id).filter_by(telegram_id=TEST_TG_ID).scalar()
        db_stats["tasks"] = s.query(models.Task).filter_by(user_id=uid).count()
        db_stats["interactions"] = s.query(models.Interaction).filter_by(user_id=uid).count()
        try:
            from models import AgentActivityLog
            db_stats["agent_activity"] = s.query(AgentActivityLog).filter_by(user_id=uid).count()
            # Детализация по типам активности
            _activities = s.query(AgentActivityLog).filter_by(user_id=uid).all()
            db_stats["activity_types"] = {}
            for _a in _activities:
                _at = getattr(_a, 'activity_type', 'unknown')
                db_stats["activity_types"][_at] = db_stats["activity_types"].get(_at, 0) + 1
        except Exception:
            db_stats["agent_activity"] = 0
            db_stats["activity_types"] = {}

        # Проверяем Interaction записи агентов (__agent JSON)
        _agent_interactions = 0
        _agent_names_in_db = set()
        try:
            _all_ai = s.query(models.Interaction).filter(
                models.Interaction.user_id == uid,
                models.Interaction.message_type == 'ai'
            ).all()
            for _inter in _all_ai:
                try:
                    _jd = json.loads(_inter.content)
                    if isinstance(_jd, dict) and '__agent' in _jd:
                        _agent_interactions += 1
                        _agent_names_in_db.add(_jd['__agent'].get('name', ''))
                except (ValueError, TypeError, KeyError):
                    pass
        except Exception:
            pass
        db_stats["agent_interactions"] = _agent_interactions
        db_stats["agent_names_in_db"] = _agent_names_in_db

    # ── Итоги ─────────────────────────────────────────────────────────────────
    print(f"\n{BOLD}{'='*60}")
    print("  ИТОГИ")
    print(f"{'='*60}{RESET}")

    errors = sum(1 for r in results if r["error"])
    empties = sum(1 for r in results if r["bot_reply"] == "(нет ответа)")
    delegations = sum(1 for r in results if r["delegated"])
    agent_steps = sum(1 for r in results if r["agents"])
    total_intermediates = sum(r.get("intermediate_count", 0) for r in results)
    avg_time = sum(r["time_s"] for r in results) / len(results) if results else 0
    max_time = max((r["time_s"] for r in results), default=0)
    slow_steps = sum(1 for r in results if r["time_s"] > 60)

    print(f"  Шагов:                {total_steps}")
    print(f"  Ошибок:               {errors}")
    print(f"  Пустых ответов:       {empties}")
    print(f"  Делегирований:        {delegations}")
    print(f"  Промежуточных сообщ:  {total_intermediates}")
    print(f"  Упоминаний агентов:   {agent_steps}")
    print(f"  Среднее время ответа: {avg_time:.1f}s")
    print(f"  Макс. время ответа:   {max_time:.1f}s")
    print(f"  Шаги >60с:            {slow_steps}")

    print(f"\n  {BOLD}БД:{RESET}")
    print(f"  Задач создано:        {db_stats['tasks']}")
    print(f"  Сообщений в истории:  {db_stats['interactions']}")
    print(f"  Активность агентов:   {db_stats['agent_activity']}")
    if db_stats.get("activity_types"):
        for _atype, _acnt in sorted(db_stats["activity_types"].items()):
            print(f"    └ {_atype}: {_acnt}")
    print(f"  Агенты в Interaction: {db_stats['agent_interactions']} записей ({', '.join(db_stats['agent_names_in_db']) or 'нет'})")

    # ── Оценка по фазам ──────────────────────────────────────────────────────
    print(f"\n  {BOLD}ОЦЕНКА ПО ФАЗАМ:{RESET}")

    phase1 = [r for r in results if r["label"].startswith("1.")]
    phase2 = [r for r in results if r["label"].startswith("2.")]
    phase3 = [r for r in results if r["label"].startswith("3.")]
    phase4 = [r for r in results if r["label"].startswith("4.")]
    phase5 = [r for r in results if r["label"].startswith("5.")]

    p1_ok = all(not r["error"] for r in phase1)
    p1_fast = all(r["time_s"] < 30 for r in phase1)
    p2_ok = all(not r["error"] for r in phase2)
    p3_ok = all(not r["error"] for r in phase3)
    p4_delegated = sum(1 for r in phase4 if r["delegated"])
    p4_agents = set()
    for r in phase4:
        p4_agents.update(r["agents"])
    p5_ok = all(not r["error"] for r in phase5)

    # Проверяем что субагенты реально работали (записи в DB)
    p4_subagent_db = len(db_stats.get("agent_names_in_db", set())) > 0

    status = lambda ok: f"{GREEN}OK{RESET}" if ok else f"{RED}FAIL{RESET}"

    print(f"  Фаза 1 (задачи):     {status(p1_ok)} — {'без ошибок' if p1_ok else 'есть ошибки'}")
    _p1_times = ', '.join(f'{r["time_s"]:.1f}s' for r in phase1)
    print(f"    Скорость (<30с):    {status(p1_fast)} — {_p1_times}")
    print(f"  Фаза 2 (исследов.):  {status(p2_ok)} — {'без ошибок' if p2_ok else 'есть ошибки'}")
    _p2_times = ', '.join(f'{r["time_s"]:.1f}s' for r in phase2)
    print(f"    Скорость:           {_p2_times}")
    print(f"  Фаза 3 (контент):    {status(p3_ok)} — {'без ошибок' if p3_ok else 'есть ошибки'}")
    print(f"  Фаза 4 (агенты):     {status(p4_delegated >= 1)} — {p4_delegated} делегирований, агенты: {', '.join(p4_agents) or 'нет'}")
    print(f"    Агенты в БД:        {status(p4_subagent_db)} — {', '.join(db_stats.get('agent_names_in_db', set())) or 'нет записей'}")
    print(f"  Фаза 5 (edge):       {status(p5_ok)} — {'без ошибок' if p5_ok else 'есть ошибки'}")

    # Проверка тайминга: ни один шаг не должен превышать 120с
    no_timeout = all(r["time_s"] < 120 for r in results)
    print(f"\n  {BOLD}ТАЙМИНГ:{RESET}")
    print(f"  Нет таймаутов (>120с): {status(no_timeout)}")
    if not no_timeout:
        for r in results:
            if r["time_s"] > 120:
                print(f"    {RED}✗ {r['label']}: {r['time_s']:.1f}s{RESET}")

    all_ok = p1_ok and p2_ok and p3_ok and p4_delegated >= 1 and p5_ok and errors == 0 and no_timeout
    if all_ok:
        print(f"\n  {GREEN}{BOLD}>>> Все фазы пройдены успешно{RESET}")
    else:
        issues = []
        if not p1_ok:
            issues.append("ошибки в задачах/планировании")
        if not p2_ok:
            issues.append("ошибки в исследовании")
        if not p3_ok:
            issues.append("ошибки в контенте")
        if p4_delegated < 1:
            issues.append(f"мало делегирований ({p4_delegated}/1+)")
        if not p5_ok:
            issues.append("ошибки в edge cases")
        if not no_timeout:
            issues.append("есть шаги >120с")
        print(f"\n  {YELLOW}!!! Требует внимания: {'; '.join(issues)}{RESET}")

    print()


if __name__ == "__main__":
    asyncio.run(run_dialogue())
