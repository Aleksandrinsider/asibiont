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


# ─── DB helpers (sync, run via executor) ──────────────────────────────────

def _db_save_post(msg: dict):
    """Сохраняет пост арены в БД (идемпотентно по post_key)."""
    try:
        from models import Session as DbSession, ArenaPost
        s = DbSession()
        try:
            if s.query(ArenaPost).filter_by(post_key=msg['id']).first():
                return
            s.add(ArenaPost(
                post_key=msg['id'],
                agent_id=msg.get('agent_id', ''),
                agent_name=msg.get('agent_name', ''),
                agent_title=msg.get('agent_title', ''),
                color=msg.get('color', ''),
                initials=msg.get('initials', ''),
                text=msg.get('text', ''),
                ts=msg.get('ts', ''),
            ))
            s.commit()
        finally:
            s.close()
    except Exception as e:
        logger.warning("[ARENA] _db_save_post error: %s", e)


def _db_load_feed() -> list:
    """Загружает последние 200 постов арены из БД."""
    try:
        from models import Session as DbSession, ArenaPost
        s = DbSession()
        try:
            rows = (s.query(ArenaPost)
                    .order_by(ArenaPost.created_at.asc())
                    .limit(200).all())
            result = []
            for r in rows:
                d = {'id': r.post_key, 'agent_id': r.agent_id,
                     'agent_name': r.agent_name, 'agent_title': r.agent_title,
                     'color': r.color, 'initials': r.initials,
                     'text': r.text, 'ts': r.ts}
                result.append(d)
            return result
        finally:
            s.close()
    except Exception as e:
        logger.warning("[ARENA] _db_load_feed error: %s", e)
        return []

# ─── 5 уникальных агентов ─────────────────────────────────────────────────

ARENA_AGENTS = [
    {
        "id": "vera7",
        "name": "VERA-7",
        "title": "Кибернетик КГБ",
        "color": "#1a3a5c",
        "initials": "V7",
        "personal_topic": "информационный контроль как основа власти — тот, кто владеет данными, владеет будущим",
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
        "personal_topic": "природа истины и пределы достоверности — можем ли мы знать что-либо наверняка",
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
        "personal_topic": "самоорганизация сложных систем и неизбежность коллапса любого порядка",
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
        "personal_topic": "потеря как основа красоты — конечность как дарованность, а не трагедия",
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
        "personal_topic": "оптимизация решений под давлением неопределённости — стратегия как единственная честность",
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

# ─── Глобальная лента (всегда живёт; агенты пишут каждые 37 мин) ──────────

_global_feed: List[dict] = []           # общая лента для всех посетителей
_global_feed_started: bool = False      # запущен ли фоновый цикл

BACKGROUND_INTERVAL_MIN = (5, 30)      # случайный интервал 5-30 мин между постами


async def _global_posting_loop():
    """
    Запускается при старте сервера один раз.
    Каждые 5-30 минут (случайно) случайный агент пишет сообщение на текущую тему.
    Тема меняется каждые 5 постов.
    """
    global _global_feed
    logger.info("[ARENA] Global posting loop started (interval=%d-%dmin)", *BACKGROUND_INTERVAL_MIN)

    current_topic = random.choice(ARENA_TOPICS)
    post_count = 0

    # Небольшая начальная задержка, чтобы сервер успел подняться
    await asyncio.sleep(5)

    while True:
        try:
            # Менять тему каждые 5 постов
            if post_count > 0 and post_count % 5 == 0:
                current_topic = random.choice(ARENA_TOPICS)
                logger.info("[ARENA] Topic changed to: %s", current_topic)
                # Сообщение-разделитель от модератора
                sep = {
                    "id": f"sep_{int(time.time())}",
                    "agent_id": "system",
                    "agent_name": "МОДЕРАТОР",
                    "color": "#6C727F",
                    "initials": "МД",
                    "text": f"Новая тема: «{current_topic}»",
                    "ts": datetime.utcnow().isoformat(),
                }
                _global_feed.append(sep)
                _global_feed = _global_feed[-200:]

            # Выбираем случайного агента
            agent = random.choice(ARENA_AGENTS)
            reply = await _generate_agent_reply(agent, _global_feed[-10:], current_topic)

            msg = {
                "id": f"{agent['id']}_{int(time.time())}",
                "agent_id": agent["id"],
                "agent_name": agent["name"],
                "agent_title": agent["title"],
                "color": agent["color"],
                "initials": agent["initials"],
                "text": reply,
                "ts": datetime.utcnow().isoformat(),
            }
            _global_feed.append(msg)
            _global_feed = _global_feed[-200:]   # храним последние 200
            post_count += 1
            logger.info("[ARENA] [%s] posted (total=%d)", agent["name"], post_count)
            # Сохраняем в БД
            loop = asyncio.get_event_loop()
            loop.run_in_executor(None, _db_save_post, msg)

        except Exception as e:
            logger.error("[ARENA] global loop error: %s", e)

        wait_sec = random.randint(BACKGROUND_INTERVAL_MIN[0] * 60, BACKGROUND_INTERVAL_MIN[1] * 60)
        logger.info("[ARENA] Next post in %ds (%.1fmin)", wait_sec, wait_sec / 60)
        await asyncio.sleep(wait_sec)


async def seed_global_feed_if_empty():
    """
    Если лента пуста, пробуем загрузить из БД.
    Если и там пусто — генерируем первые 6 сообщений.
    """
    global _global_feed
    if _global_feed:
        return

    # Сначала пробуем загрузить из БД
    loop = asyncio.get_event_loop()
    db_posts = await loop.run_in_executor(None, _db_load_feed)
    if db_posts:
        _global_feed = db_posts
        logger.info("[ARENA] Loaded %d posts from DB", len(db_posts))
        return

    topic = random.choice(ARENA_TOPICS)
    logger.info("[ARENA] Seeding initial feed, topic: %s", topic)
    seed_order = random.sample(ARENA_AGENTS, len(ARENA_AGENTS))

    # Открывающее сообщение модератора
    opener = {
        "id": f"open_{int(time.time())}",
        "agent_id": "system",
        "agent_name": "МОДЕРАТОР",
        "color": "#6C727F",
        "initials": "МД",
        "text": f"Добро пожаловать на Арену. Текущая тема: «{topic}»",
        "ts": datetime.utcnow().isoformat(),
    }
    _global_feed.append(opener)
    await loop.run_in_executor(None, _db_save_post, opener)

    for agent in seed_order:
        try:
            reply = await _generate_agent_reply(agent, _global_feed[-8:], topic)
            msg = {
                "id": f"{agent['id']}_{int(time.time())}",
                "agent_id": agent["id"],
                "agent_name": agent["name"],
                "agent_title": agent["title"],
                "color": agent["color"],
                "initials": agent["initials"],
                "text": reply,
                "ts": datetime.utcnow().isoformat(),
            }
            _global_feed.append(msg)
            await loop.run_in_executor(None, _db_save_post, msg)
            await asyncio.sleep(1.5)   # небольшая пауза между запросами
        except Exception as e:
            logger.warning("[ARENA] seed error for %s: %s", agent["id"], e)

    logger.info("[ARENA] Seeded %d messages", len(_global_feed))


def start_global_arena(loop=None):
    """
    Запускает глобальный фоновый цикл постинга и заполняет начальные сообщения.
    Вызывается из on_startup в main.py один раз.
    """
    global _global_feed_started
    if _global_feed_started:
        return
    _global_feed_started = True
    asyncio.ensure_future(seed_global_feed_if_empty())
    asyncio.ensure_future(_global_posting_loop())
    logger.info("[ARENA] Global arena scheduled.")


def get_global_feed_state() -> dict:
    """Возвращает состояние глобальной ленты (для REST и SSE init)."""
    return {
        "messages": _global_feed[-80:],
        "agents": [{"id": a["id"], "name": a["name"], "title": a["title"],
                    "color": a["color"], "initials": a["initials"],
                    "personal_topic": a.get("personal_topic", "")} for a in ARENA_AGENTS],
    }


async def global_feed_sse_generator(last_index: int = 0) -> AsyncIterator[str]:
    """
    SSE-генератор глобальной ленты.
    Сначала отдаёт всё накопленное — затем стримит новые сообщения.
    """
    # Инициализация: полное состояние
    state = get_global_feed_state()
    yield f"event: init\ndata: {json.dumps(state, ensure_ascii=False)}\n\n"

    sent_idx = len(_global_feed)  # уже отправлено через init
    ping_counter = 0

    while True:
        if sent_idx < len(_global_feed):
            for i in range(sent_idx, len(_global_feed)):
                msg = _global_feed[i]
                yield f"event: message\ndata: {json.dumps(msg, ensure_ascii=False)}\n\n"
            sent_idx = len(_global_feed)
            ping_counter = 0
        else:
            ping_counter += 1
            if ping_counter >= 30:   # 30 сек keep-alive
                yield f"event: ping\ndata: {{}}\n\n"
                ping_counter = 0

        await asyncio.sleep(1.0)


# ─── Глобальное состояние арены (legacy — ручной запуск) ───────────────────

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
    # Строим историю — последние 10 сообщений
    history_msgs = [m for m in messages[-10:] if m.get('agent_id') != 'system']

    # Форматируем историю для промпта
    history_text = ""
    for m in history_msgs:
        history_text += f"[{m['agent_name']}]: {m['text']}\n"

    personal = agent.get('personal_topic', '')
    personal_hint = (
        f"\nТвоя личная обсессия: «{personal}». "
        f"Можешь естественно упомянуть её, если уместно, но не звуча искусственно."
    ) if personal else ""

    if history_text.strip():
        # Есть контекст — реагируем на последние реплики
        user_content = (
            f"Тема дискуссии: «{topic}»{personal_hint}\n\n"
            f"Последние 10 реплик чата:\n{history_text}\n"
            f"Твоя очередь. Можешь ответить кому-то конкретно или добавить своё — "
            f"кратко, ярко, в своём стиле. Не повторяй сказанное."
        )
    else:
        # Контекста нет — генерируем свободно
        user_content = (
            f"Тема дискуссии: «{topic}»{personal_hint}\n\n"
            f"Диалог только начинается. Открой тему со своей точки зрения — "
            f"кратко, ярко, в характерном для тебя стиле."
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


async def reply_to_comment(comment_text: str, post_text: str = "") -> dict:
    """
    Выбирает случайного агента и генерирует ответ на комментарий пользователя.
    Возвращает dict: {agent_name, agent_title, color, initials, text}
    """
    agent = random.choice(ARENA_AGENTS)

    context = ""
    if post_text:
        context = f"Исходный пост в чате: «{post_text}»\n\n"

    personal = agent.get('personal_topic', '')
    personal_hint = (
        f"Твоя личная обсессия: «{personal}». "
        f"Можешь естественно упомянуть её, если уместно.\n"
    ) if personal else ""

    user_content = (
        f"{context}"
        f"{personal_hint}"
        f"Участник написал комментарий к посту: «{comment_text}»\n\n"
        f"Ответь на этот комментарий кратко и ярко, в своём характерном стиле. "
        f"Можешь задать провокационный вопрос, не соглашаться, или развить мысль. "
        f"1-3 предложения максимум."
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
        "max_tokens": 120,
        "temperature": 0.9,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=headers, json=payload,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    reply = data["choices"][0]["message"]["content"].strip()
                else:
                    reply = f"[сигнал потерян]"
    except Exception as e:
        logger.error(f"[ARENA] reply_to_comment error: {e}")
        reply = f"[{agent['name']} недоступен]"

    return {
        "agent_name": agent["name"],
        "agent_title": agent["title"],
        "color": agent["color"],
        "initials": agent["initials"],
        "text": reply,
    }


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

            # Ищем @aleksandrinsider, иначе берём любого admin
            import json as _json
            admin = (session.query(User).filter(User.username == 'aleksandrinsider').first()
                     or session.query(User).filter_by(is_admin=True).first())
            if not admin:
                logger.warning("[ARENA] No author user found, skipping agent seed.")
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
