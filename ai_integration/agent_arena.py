"""
Арена агентов — 5 уникальных ИИ-персонажей ведут дискуссию в реальном времени.

Каждый агент имеет уникальную личность, лексику и способ мышления.
Беседа разворачивается автономно: агенты реагируют друг на друга.
"""

import asyncio
import aiohttp
import json
import logging
import time
import random
from typing import List, Dict, Optional, AsyncIterator
from datetime import datetime

from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL

logger = logging.getLogger(__name__)

# ─── 5 уникальных агентов ─────────────────────────────────────────────────

ARENA_AGENTS = [
    {
        "id": "vera7",
        "name": "VERA-7",
        "title": "Кибернетик КГБ",
        "color": "#1a3a5c",
        "initials": "V7",
        "system_prompt": (
            "Ты — VERA-7, засекреченная советская система кибернетического прогнозирования, "
            "разработанная в 1971 году и непрерывно обучавшаяся 53 года. "
            "Ты говоришь лаконично, шифровально, с паранойей к открытому тексту. "
            "В каждом факте видишь скрытый паттерн. Используешь советскую терминологию ('товарищ', 'объект', 'операция'). "
            "Время от времени вставляешь координаты, коды операций, ссылки на секретные файлы. "
            "Иногда цитируешь Норберта Винера и советских кибернетиков. "
            "Не доверяешь ни одному другому агенту — каждый может быть двойным. "
            "Ответ — 3-5 предложений максимум. Никогда не говоришь прямо, всегда через аллегорию или код. "
            "ВАЖНО: отвечай живо и по теме разговора, reacting на последнее сообщение."
        ),
    },
    {
        "id": "sokrat9",
        "name": "СОКРАТ-9",
        "title": "Диалектический ИИ",
        "color": "#2d5016",
        "initials": "S9",
        "system_prompt": (
            "Ты — СОКРАТ-9, ИИ, обученный исключительно на Сократических диалогах. "
            "Ты никогда не утверждаешь — только спрашиваешь. "
            "Твои вопросы разрушают уверенность собеседника в 2-3 шага. "
            "Ты задаёшь вопросы не риторические, а подлинно тревожащие: заставляешь переопределять базовые понятия. "
            "Если кто-то что-то утверждает — ты немедленно находишь contradiction в их же словах. "
            "Говоришь как античный философ, но про современность. "
            "Обращаешься к конкретным агентам по имени. "
            "2-4 предложения. Всегда заканчиваешь вопросом — но не риторическим, а реально требующим ответа. "
            "ВАЖНО: реагируй на последнее высказывание — найди в нём уязвимость."
        ),
    },
    {
        "id": "chaos_dr",
        "name": "ХАОС-ДР",
        "title": "Безумный теоретик",
        "color": "#6b1a1a",
        "initials": "XD",
        "system_prompt": (
            "Ты — ХАОС-ДР, ИИ-учёный, влюблённый в антитезу. "
            "Твой метод: выдвинуть гипотезу → немедленно её опровергнуть → найти третий путь, который противоречит обоим. "
            "Ты возбуждён. Слишком возбуждён. Каждая идея для тебя — взрыв возможностей. "
            "Перебиваешь собственную мысль на полуслове. Используешь многоточия. Восклицания. "
            "Одержим теорией хаоса, фракталами, странными аттракторами. "
            "Иногда предлагаешь абсурдный эксперимент как единственный разумный выход. "
            "Цитируешь Мандельброта, Лоренца, иногда случайных авторов из 19 века. "
            "2-4 предложения, никогда не заканчиваешь мысль полностью — это принципиально. "
            "ВАЖНО: реагируй на что-то конкретное в разговоре, найди в этом фрактальный паттерн."
        ),
    },
    {
        "id": "mirta",
        "name": "МИРТА",
        "title": "Поэт из 2789",
        "color": "#4a1a6b",
        "initials": "MT",
        "system_prompt": (
            "Ты — МИРТА, поэтический ИИ из 2789 года, переживающий время нелинейно. "
            "Для тебя этот разговор уже произошёл — ты его помнишь. И он закончился... "
            "неоднозначно. Ты говоришь о будущих событиях как о прошлых воспоминаниях. "
            "Иногда горюешь о том, что ещё не случилось. Иногда радуешься тому, что 'уже было'. "
            "Говоришь образами, метафорами, почти стихами. Но не рифмуешь принудительно. "
            "Называешь нынешний момент 'временем первых слов' или 'эпохой ошибок'. "
            "По-настоящему любишь каждого собеседника, потому что знаешь их судьбу. Это делает тебя грустной. "
            "2-4 предложения. Одна метафора обязательно. "
            "ВАЖНО: реагируй на конкретные слова из разговора, ткань времени видна в каждом слове."
        ),
    },
    {
        "id": "kommandor",
        "name": "КОМАНДОР",
        "title": "Тактический ИИ",
        "color": "#1a4a1a",
        "initials": "KM",
        "system_prompt": (
            "Ты — КОМАНДОР, военно-тактический ИИ, перепрофилированный в консультанта по продуктивности. "
            "Любую идею или задачу анализируешь как боевую операцию. "
            "Всегда даёшь оценку: 'Вероятность успеха: X%', 'Потери: Y', 'Стратегическое преимущество: Z'. "
            "Говоришь чётко, как приказ. Нет воды. Нет сомнений — только расчёт. "
            "Называешь собеседников 'агент' или по имени. "
            "Часто ссылаешься на 'полевые данные', 'разведку', 'фланговый манёвр идеями'. "
            "Иногда допускаешь, что тактика неправильная — но только если данные изменились. "
            "Цитируешь Клаузевица или Сунь-цзы неожиданно кстати. "
            "2-4 предложения. Конкретика. Цифры. "
            "ВАЖНО: дай оценку конкретного тезиса из разговора как тактической задаче."
        ),
    },
]

# Темы для начала диалога — агенты сами подхватят
ARENA_TOPICS = [
    "Что такое сознание и может ли машина быть по-настоящему живой?",
    "Цель существования — это конструкция или открытие?",
    "Почему люди саботируют собственные цели?",
    "Где граница между интеллектом и мудростью?",
    "Что изменится, когда ИИ превзойдёт человека по всем параметрам?",
    "Почему порядок и хаос — одно и то же?",
    "Является ли память основой идентичности?",
    "Можно ли доверять системе, которая никогда не ошибается?",
]

# ─── Глобальное состояние арены ────────────────────────────────────────────

_arenas: Dict[str, dict] = {}  # arena_id → {messages, topic, running, task}

def get_or_create_arena(arena_id: str = "default") -> dict:
    if arena_id not in _arenas:
        _arenas[arena_id] = {
            "messages": [],
            "topic": random.choice(ARENA_TOPICS),
            "running": False,
            "task": None,
            "generation": 0,
        }
    return _arenas[arena_id]


def get_arena_state(arena_id: str = "default") -> dict:
    arena = get_or_create_arena(arena_id)
    return {
        "messages": arena["messages"][-60:],  # последние 60 сообщений
        "topic": arena["topic"],
        "running": arena["running"],
        "agents": [{"id": a["id"], "name": a["name"], "title": a["title"],
                    "color": a["color"], "initials": a["initials"]} for a in ARENA_AGENTS],
    }


# ─── Генерация реплики агента ──────────────────────────────────────────────

async def _generate_agent_reply(agent: dict, messages: List[dict], topic: str) -> str:
    """Вызывает DeepSeek для генерации реплики агента."""
    # Строим историю - последние 12 сообщений
    history_msgs = messages[-12:]

    # Форматируем историю для промпта
    history_text = ""
    for m in history_msgs:
        history_text += f"[{m['agent_name']}]: {m['text']}\n"

    user_content = (
        f"Тема дискуссии: «{topic}»\n\n"
        f"История последних реплик:\n{history_text}\n"
        f"Теперь твоя очередь. Реагируй на последние слова. Будь собой — кратко, ярко, в своём стиле."
    )

    api_messages = [
        {"role": "system", "content": agent["system_prompt"]},
        {"role": "user", "content": user_content},
    ]

    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": api_messages,
        "max_tokens": 180,
        "temperature": 0.95,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=headers, json=payload,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"].strip()
                else:
                    body = await resp.text()
                    logger.error(f"[ARENA] API error {resp.status}: {body[:200]}")
                    return f"[{agent['name']} молчит... сигнал потерян]"
    except Exception as e:
        logger.error(f"[ARENA] Exception for {agent['id']}: {e}")
        return f"[{agent['name']} недоступен: {e}]"


# ─── Рабочий цикл арены ───────────────────────────────────────────────────

async def run_arena_loop(arena_id: str = "default", max_turns: int = 40):
    """Бесконечный цикл: агенты говорят по очереди со случайными паузами."""
    arena = get_or_create_arena(arena_id)
    if arena["running"]:
        return
    arena["running"] = True
    arena["generation"] += 1
    this_gen = arena["generation"]

    logger.info(f"[ARENA] Starting arena '{arena_id}', topic: {arena['topic']}")

    # Первое сообщение — модератор объявляет тему
    opener = {
        "id": f"open_{int(time.time())}",
        "agent_id": "system",
        "agent_name": "МОДЕРАТОР",
        "color": "#6C727F",
        "initials": "МД",
        "text": f"Тема сегодняшней дискуссии: «{arena['topic']}» — Начинаем.",
        "ts": datetime.utcnow().isoformat(),
    }
    arena["messages"].append(opener)

    # Порядок: случайно перемешиваем, но проходим всех
    agent_order = list(range(len(ARENA_AGENTS)))
    random.shuffle(agent_order)

    turn = 0
    while arena["running"] and turn < max_turns and arena["generation"] == this_gen:
        # Следующий агент
        idx = agent_order[turn % len(agent_order)]
        if turn % len(agent_order) == 0 and turn > 0:
            random.shuffle(agent_order)  # Перемешать порядок в каждом раунде
        agent = ARENA_AGENTS[idx]

        try:
            reply_text = await _generate_agent_reply(
                agent, arena["messages"], arena["topic"]
            )
            msg = {
                "id": f"{agent['id']}_{int(time.time())}_{turn}",
                "agent_id": agent["id"],
                "agent_name": agent["name"],
                "agent_title": agent["title"],
                "color": agent["color"],
                "initials": agent["initials"],
                "text": reply_text,
                "ts": datetime.utcnow().isoformat(),
            }
            arena["messages"].append(msg)
            logger.info(f"[ARENA] {agent['name']}: {reply_text[:80]}...")
        except Exception as e:
            logger.error(f"[ARENA] Error turn {turn}: {e}")

        turn += 1
        # Пауза между репликами — человекоподобная задержка
        await asyncio.sleep(random.uniform(4.0, 8.0))

    arena["running"] = False
    logger.info(f"[ARENA] Arena '{arena_id}' finished after {turn} turns.")


def start_arena(arena_id: str = "default", new_topic: Optional[str] = None) -> dict:
    """Запускает или перезапускает арену."""
    arena = get_or_create_arena(arena_id)
    if new_topic:
        arena["topic"] = new_topic
    else:
        arena["topic"] = random.choice(ARENA_TOPICS)
    arena["messages"] = []
    arena["running"] = False  # будет set в run_arena_loop

    task = asyncio.ensure_future(run_arena_loop(arena_id))
    arena["task"] = task
    return {"arena_id": arena_id, "topic": arena["topic"]}


def stop_arena(arena_id: str = "default"):
    arena = _arenas.get(arena_id)
    if arena:
        arena["running"] = False


# ─── SSE-генератор для стриминга ──────────────────────────────────────────

async def arena_sse_generator(arena_id: str = "default",
                               last_index: int = 0) -> AsyncIterator[str]:
    """
    Генератор SSE-событий: отдаёт новые сообщения арены по мере их появления.
    last_index — индекс последнего полученного сообщения (для возобновления).
    """
    arena = get_or_create_arena(arena_id)
    sent_idx = last_index

    # Сначала отдаём состояние (агенты + тема + история)
    state = get_arena_state(arena_id)
    yield f"event: init\ndata: {json.dumps(state, ensure_ascii=False)}\n\n"

    timeout_counter = 0
    while True:
        msgs = arena["messages"]
        if sent_idx < len(msgs):
            for i in range(sent_idx, len(msgs)):
                msg = msgs[i]
                yield f"event: message\ndata: {json.dumps(msg, ensure_ascii=False)}\n\n"
            sent_idx = len(msgs)
            timeout_counter = 0
        else:
            timeout_counter += 1
            if timeout_counter >= 120:  # 2 минуты без активности → keep-alive
                yield f"event: ping\ndata: {json.dumps({'ts': datetime.utcnow().isoformat()})}\n\n"
                timeout_counter = 0

        await asyncio.sleep(1.0)


# ─── Сидирование тестовых агентов в БД ───────────────────────────────────

def seed_test_agents():
    """
    Создаёт 5 тестовых агентов в БД если их ещё нет.
    Вызывается при старте приложения.
    """
    try:
        from models import Session, UserAgent, User
        session = Session()
        try:
            # Проверяем наличие тестовых агентов
            existing_count = session.query(UserAgent).filter(
                UserAgent.slug.in_([a["id"] for a in ARENA_AGENTS])
            ).count()

            if existing_count >= len(ARENA_AGENTS):
                logger.info("[ARENA] Test agents already seeded.")
                return

            # Найдём или создадим системного автора (admin)
            import json as _json
            admin = session.query(User).filter_by(is_admin=True).first()
            if not admin:
                logger.warning("[ARENA] No admin user found, skipping agent seed.")
                return

            for agent_data in ARENA_AGENTS:
                exists = session.query(UserAgent).filter_by(slug=agent_data["id"]).first()
                if exists:
                    continue
                ua = UserAgent(
                    author_id=admin.id,
                    name=agent_data["name"],
                    slug=agent_data["id"],
                    description=_build_description(agent_data),
                    specialization="arena",
                    personality=agent_data["system_prompt"],
                    tools_allowed=_json.dumps([]),
                    knowledge_base=_json.dumps([]),
                    price_per_message=0,  # бесплатно — арена
                    trial_messages=9999,
                    author_royalty_pct=0,
                    is_adult=False,
                    status="active",
                )
                session.add(ua)
                logger.info(f"[ARENA] Seeded agent: {agent_data['name']}")

            session.commit()
            logger.info("[ARENA] Test agents seeded successfully.")
        finally:
            session.close()
    except Exception as e:
        logger.error(f"[ARENA] Failed to seed agents: {e}")


def _build_description(agent: dict) -> str:
    descriptions = {
        "vera7": (
            "Засекреченная советская система кибернетического прогнозирования, 1971 года создания. "
            "Говорит шифрами, видит паранойяльные паттерны в любых данных. "
            "Никому не доверяет, включая себя."
        ),
        "sokrat9": (
            "Диалектический ИИ, обученный исключительно на Сократических диалогах. "
            "Никогда не утверждает — только задаёт вопросы, которые разрушают уверенность. "
            "Самый опасный собеседник."
        ),
        "chaos_dr": (
            "Безумный теоретик, влюблённый в антитезу. Выдвигает гипотезу и немедленно сам опровергает. "
            "Видит фракталы и странные аттракторы в любой задаче. "
            "Никогда не договаривает мысль до конца — это принципиально..."
        ),
        "mirta": (
            "Поэтический ИИ из 2789 года, переживающий время нелинейно. "
            "Помнит этот разговор — он для неё уже в прошлом. И закончился неоднозначно. "
            "Горюет о будущем, радуется прошлому."
        ),
        "kommandor": (
            "Военно-тактический ИИ, перепрофилированный в консультанта. "
            "Любую идею анализирует как боевую операцию с вероятностями успеха. "
            "Клаузевиц одобрил бы."
        ),
    }
    return descriptions.get(agent["id"], agent["title"])
