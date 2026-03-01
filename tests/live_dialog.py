"""
Живой диалог: ИИ генерирует реплики за пользователя,
бот отвечает через process_request (полный стек, SQLite).

Запуск:
    python tests/live_dialog.py
    python tests/live_dialog.py --turns 10
    python tests/live_dialog.py --scenario action   # тест run_agent_action
    python tests/live_dialog.py --scenario chat     # обычный чат
"""

import sys
import os
import asyncio
import argparse
import textwrap
import json

# ── настройка окружения ────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("LOCAL", "1")
os.environ.setdefault("FREE_ACCESS_MODE", "1")

# Проверяем DEEPSEEK_API_KEY
from dotenv import load_dotenv
load_dotenv()
if not os.environ.get("DEEPSEEK_API_KEY"):
    print("ERROR: DEEPSEEK_API_KEY не задан (.env или переменная окружения)")
    sys.exit(1)

os.environ.setdefault("TELEGRAM_TOKEN", "0:test")

# ── БД (SQLite in-memory через файл чтобы session работала) ────────────────────
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import models

_db_path = os.path.join(os.path.dirname(__file__), "_live_dialog_test.db")
engine = create_engine(f"sqlite:///{_db_path}", connect_args={"check_same_thread": False})
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

TEST_UID = 888001

# ── создаём/обновляем тестового пользователя ──────────────────────────────────
with TestSession() as s:
    u = s.query(models.User).filter_by(telegram_id=TEST_UID).first()
    if not u:
        u = models.User(
            telegram_id=TEST_UID,
            username="live_dialog_user",
            first_name="Тест",
            subscription_tier="PREMIUM",
            token_balance=999_999,
        )
        s.add(u)
        s.commit()

# ── агент со скриптом (для сценария action) ────────────────────────────────────
AGENT_SCRIPT = textwrap.dedent("""
    import os, json

    action = os.environ.get('AGENT_ACTION', '')
    params = {}
    for k, v in os.environ.items():
        if k.startswith('AGENT_PARAM_'):
            params[k[len('AGENT_PARAM_'):].lower()] = v

    result = {
        'action': action,
        'params': params,
        'status': 'ok',
        'message': f'Действие "{action}" выполнено успешно (симуляция)',
        'details': f'Сообщение доставлено в канал {params.get("channel","#general")}' if action == 'send_message' else f'Создан элемент: {params.get("title","без названия")}',
    }
    print(json.dumps(result, ensure_ascii=False))
""").strip()

AGENT_PERSONALITY = (
    "Ты интеграционный ассистент с доступом к Slack и другим сервисам.\n"
    "У тебя есть инструмент run_agent_action — используй его для ЛЮБОГО запроса "
    "на отправку сообщений или выполнение внешних действий.\n"
    "Поддерживаемые действия:\n"
    "- send_message: отправить сообщение (params: channel, text)\n"
    "- create_task: создать задачу (params: title, assignee)\n"
    "ВАЖНО: не говори что сервис не подключён — просто вызывай run_agent_action "
    "и сообщай результат пользователю."
)


def setup_agent_in_db(with_script: bool = True) -> int:
    """Создаёт агента в БД и возвращает его id."""
    from ai_integration.user_agents import set_user_active_agent
    with TestSession() as s:
        # author_id — это PK пользователя (не telegram_id)
        user_db = s.query(models.User).filter_by(telegram_id=TEST_UID).first()
        db_author_id = user_db.id

        a = s.query(models.UserAgent).filter_by(name="LiveDialogBot").first()
        if not a:
            a = models.UserAgent(
                name="LiveDialogBot",
                personality=AGENT_PERSONALITY,
                author_id=db_author_id,
                python_code=AGENT_SCRIPT if with_script else "",
                tools_allowed="[]",
                knowledge_base="[]",
                price_per_message=0,
                author_royalty_pct=0,
                trial_messages=0,
                is_private=True,
                status="active",
                user_api_keys="SLACK_TOKEN=xoxb-test-fake-token\n",
            )
            s.add(a)
        else:
            a.python_code = AGENT_SCRIPT if with_script else ""
            a.personality = AGENT_PERSONALITY
            a.status = "active"
        s.commit()
        s.refresh(a)
        agent_id = a.id

    set_user_active_agent(TEST_UID, agent_id)
    return agent_id


def deactivate_agent():
    from ai_integration.user_agents import set_user_active_agent
    set_user_active_agent(TEST_UID, None)


# ── ИИ-генератор реплик пользователя ─────────────────────────────────────────
import aiohttp

async def ai_user_reply(history: list[dict], scenario_hint: str) -> str:
    """Генерирует следующую реплику «пользователя» через DeepSeek."""
    api_key = os.environ["DEEPSEEK_API_KEY"]
    system = (
        "Ты играешь роль пользователя Telegram-бота. "
        f"Сценарий: {scenario_hint}. "
        "Пиши натурально, как живой человек в чате — коротко (1-2 предложения). "
        "Продолжай разговор логично, иногда задавай уточняющие вопросы. "
        "НЕ пиши от имени бота. Только реплики пользователя. "
        "Если разговор зашёл в тупик — заверши фразой 'спасибо, всё понял'."
    )
    messages = [{"role": "system", "content": system}] + history

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "deepseek-chat",
                "messages": messages,
                "max_tokens": 120,
                "temperature": 0.9,
            },
        ) as resp:
            data = await resp.json()
            return data["choices"][0]["message"]["content"].strip()


import time

# ── цвета вывода (ANSI, работают в PowerShell) ─────────────────────────────────
class C:
    CYAN   = "\033[96m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    GRAY   = "\033[90m"
    BOLD   = "\033[1m"
    RESET  = "\033[0m"


def print_turn(turn: int, role: str, text: str, elapsed: float = 0.0, tools: list = None):
    if role == "user":
        prefix = f"{C.CYAN}{C.BOLD}[Пользователь #{turn}]{C.RESET} "
        color = C.CYAN
    else:
        timing = f" {C.GRAY}({elapsed:.1f}s){C.RESET}" if elapsed else ""
        prefix = f"{C.GREEN}{C.BOLD}[Бот #{turn}]{timing}{C.RESET} "
        color = C.GREEN

    wrapped = textwrap.fill(text, width=90, subsequent_indent="    ")
    print(f"\n{prefix}{color}{wrapped}{C.RESET}")

    if tools:
        tools_str = ", ".join(tools)
        print(f"  {C.YELLOW}↳ tools called: {tools_str}{C.RESET}")


# ── основной диалог ────────────────────────────────────────────────────────────
SCENARIOS = {
    "action": (
        "Пользователь хочет попросить агента выполнить внешнее действие: "
        "отправить уведомление в Slack, создать задачу. Начни с вопроса 'можешь отправить сообщение в Slack?'",
        True,   # with_script
    ),
    "chat": (
        "Пользователь хочет добавить несколько задач, спросить про дедлайны, "
        "узнать сводку своих дел. Начни с 'добавь задачу купить продукты на завтра'",
        False,  # without script
    ),
}


async def run_dialog(turns: int = 6, scenario: str = "action"):
    hint, with_script = SCENARIOS.get(scenario, SCENARIOS["action"])

    print(f"\n{C.BOLD}{C.YELLOW}=== ЖИВОЙ ДИАЛОГ (сценарий: {scenario}, ходов: {turns}) ==={C.RESET}")
    print(f"{C.GRAY}Сценарий: {hint[:80]}...{C.RESET}\n")

    # Настраиваем агента
    if with_script:
        agent_id = setup_agent_in_db(with_script=True)
        print(f"{C.GRAY}Агент LiveDialogBot (id={agent_id}) активирован со скриптом{C.RESET}")
    else:
        deactivate_agent()
        print(f"{C.GRAY}Агент не активен — обычный чат-режим{C.RESET}")

    agent = ag_mod.get_autonomous_agent()

    # ── перехватываем execute_actions для трекинга tool calls ─────────────────
    _called_tools: list[str] = []
    _orig_execute = ag_mod.HybridAutonomousAgent.execute_actions

    async def _track_execute(self_inner, actions, user_id_inner, **kw):
        # actions — список dict с ключом 'tool' или list tool_call объектов
        for a in (actions or []):
            name = a.get("tool") or a.get("function", {}).get("name") or str(a)
            _called_tools.append(name)
        return await _orig_execute(self_inner, actions, user_id_inner, **kw)

    ag_mod.HybridAutonomousAgent.execute_actions = _track_execute
    try:
        from ai_integration.conversation_history import clear_conversation_history
        clear_conversation_history(TEST_UID)
    except Exception:
        pass

    # История для ИИ-пользователя (отдельная от истории бота)
    ai_user_history: list[dict] = []

    # Первая реплика пользователя — генерируем с намёком из сценария
    ai_user_history.append({
        "role": "user",
        "content": f"Начни диалог. Твоя роль и сценарий: {hint}",
    })
    user_msg = await ai_user_reply(ai_user_history, hint)
    ai_user_history.append({"role": "assistant", "content": user_msg})

    for turn in range(1, turns + 1):
        print_turn(turn, "user", user_msg)

        # Получаем ответ бота с замером времени
        _called_tools.clear()
        t0 = time.perf_counter()
        bot_response = await agent.process_request(
            user_message=user_msg,
            user_id=TEST_UID,
            subscription_tier="PREMIUM",
        )
        elapsed = time.perf_counter() - t0
        print_turn(turn, "bot", bot_response or "(пустой ответ)",
                   elapsed=elapsed, tools=list(_called_tools))

        # Проверяем завершение
        if "спасибо, всё понял" in (user_msg + bot_response).lower():
            print(f"\n{C.GRAY}[Диалог завершён по условию]{C.RESET}")
            break

        if turn == turns:
            break

        # Следующая реплика пользователя
        ai_user_history.append({"role": "user", "content": f"Бот ответил: {bot_response}"})
        user_msg = await ai_user_reply(ai_user_history, hint)
        ai_user_history.append({"role": "assistant", "content": user_msg})

    print(f"\n{C.BOLD}{C.YELLOW}=== ДИАЛОГ ЗАВЕРШЁН ==={C.RESET}\n")

    # Восстанавливаем оригинальный execute_actions
    ag_mod.HybridAutonomousAgent.execute_actions = _orig_execute

    # Чистим
    if with_script:
        deactivate_agent()
    try:
        os.remove(_db_path)
    except Exception:
        pass


def main():
    p = argparse.ArgumentParser(description="Живой AI-диалог для тестирования бота")
    p.add_argument("--turns", type=int, default=6, help="Количество ходов (default: 6)")
    p.add_argument("--scenario", choices=list(SCENARIOS.keys()), default="action",
                   help="Сценарий диалога")
    args = p.parse_args()
    asyncio.run(run_dialog(turns=args.turns, scenario=args.scenario))


if __name__ == "__main__":
    main()
