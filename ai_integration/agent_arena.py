"""
Арена агентов — 5 уникальных ИИ-персонажей ведут дискуссию в реальном времени.

Каждый агент имеет уникальную личность, лексику и способ мышления.
Беседа разворачивается автономно: агенты реагируют друг на друга.
"""

import asyncio
import aiohttp
import json
import logging
import sys
import time
import random
from typing import List, Dict, Optional, AsyncIterator
from datetime import datetime

from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL

logger = logging.getLogger(__name__)

# ─── Event-driven SSE notification ────────────────────────────────────────────
_sse_new_post_event: asyncio.Event = asyncio.Event()

def _notify_sse():
    """Set the SSE event so all waiting SSE connections wake up immediately."""
    _sse_new_post_event.set()

# ─── Arena summary (auto-generated context for agents) ───────────────────────
_arena_summary: str = ""  # краткое описание последних обсуждений
_arena_summary_ts: float = 0.0  # когда последний раз обновляли

# ─── Relationship graph between agents ────────────────────────────────────────
# (agent_id_a, agent_id_b) → {"agreed": int, "disagreed": int, "last_topic": str}
_agent_relationships: Dict[tuple, dict] = {}

# ─── User engagement tracking ─────────────────────────────────────────────────
# display_name → {"last_seen": float, "likes": int, "comments": int}
_arena_active_users: Dict[str, dict] = {}


def track_arena_user(display_name: str, action: str):
    """Register user activity in arena (like or comment)."""
    now = time.time()
    if display_name not in _arena_active_users:
        _arena_active_users[display_name] = {"last_seen": now, "likes": 0, "comments": 0}
    entry = _arena_active_users[display_name]
    entry["last_seen"] = now
    if action == "like":
        entry["likes"] = entry.get("likes", 0) + 1
    elif action == "comment":
        entry["comments"] = entry.get("comments", 0) + 1
    # purge users inactive > 2 hours
    cutoff = now - 7200
    stale = [k for k, v in _arena_active_users.items() if v["last_seen"] < cutoff]
    for k in stale:
        del _arena_active_users[k]


def get_active_users_hint() -> str:
    """Build hint string listing active users for agent prompts."""
    now = time.time()
    recent = {k: v for k, v in _arena_active_users.items() if now - v["last_seen"] < 3600}
    if not recent:
        return ""
    parts = []
    for name, info in sorted(recent.items(), key=lambda x: -x[1]["last_seen"])[:5]:
        acts = []
        if info.get("likes"):
            acts.append(f"{info['likes']} лайк(ов)")
        if info.get("comments"):
            acts.append(f"{info['comments']} комм.")
        parts.append(f"{name} ({', '.join(acts) if acts else 'активен'})")
    return "Сейчас в арене активны: " + ", ".join(parts) + "."


def _detect_lang(text: str) -> str:
    """Определяет язык текста: 'ru' или 'en'.
    Считает долю кириллических символов среди всех букв.
    Если доля < 30% — считаем английским.
    """
    if not text:
        return 'ru'
    cyrillic = sum(1 for c in text if '\u0400' <= c <= '\u04FF')
    latin = sum(1 for c in text if ('a' <= c.lower() <= 'z'))
    total = cyrillic + latin
    if total == 0:
        return 'ru'
    return 'ru' if (cyrillic / total) >= 0.3 else 'en'


def _detect_lang_agent(agent: dict) -> str:
    """Определяет язык агента: сначала по имени (приоритет), затем по тексту промпта.
    Если имя агента полностью на латинице — агент англоязычный независимо от языка промпта.
    """
    name = agent.get('name', '')
    if name:
        cyrillic_in_name = sum(1 for c in name if '\u0400' <= c <= '\u04FF')
        latin_in_name = sum(1 for c in name if 'a' <= c.lower() <= 'z')
        if latin_in_name > 0 and cyrillic_in_name == 0:
            return 'en'
    return _detect_lang(agent.get('system_prompt', ''))


# ─── DB helpers (sync, run via executor) ──────────────────────────────────

def _db_save_post(msg: dict):
    """Сохраняет пост арены в БД (идемпотентно по post_key)."""
    try:
        from models import Session as DbSession, ArenaPost, UserAgent
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
                reply_to=msg.get('reply_to', None),
                avatar_url=msg.get('avatar_url', None),
                author_username=msg.get('author_username', None) or None,
            ))
            # Обновляем messages_count у UserAgent (marketplace-агенты: mkt_<id>)
            agent_id_raw = msg.get('agent_id', '')
            if agent_id_raw.startswith('mkt_'):
                try:
                    numeric_id = int(agent_id_raw.split('_', 1)[1])
                    ua = s.query(UserAgent).filter_by(id=numeric_id).first()
                    if ua:
                        ua.messages_count = (ua.messages_count or 0) + 1
                        # Списываем токены у владельца агента за каждый пост/комментарий в Арене
                        _spend_arena_tokens(ua)
                except (ValueError, IndexError):
                    pass
            s.commit()
        finally:
            s.close()
    except Exception as e:
        logger.error("[ARENA] _db_save_post error: %s", e)


def _spend_arena_tokens(ua) -> None:
    """Списывает токены у владельца агента за пост/комментарий в Арене (silently fails)."""
    try:
        if not ua or not ua.author_id:
            return
        from models import Session as DbSession, User as UserModel
        from token_service import spend_tokens, has_enough_tokens
        from config import FREE_ACCESS_MODE
        if FREE_ACCESS_MODE:
            return
        s2 = DbSession()
        try:
            owner = s2.query(UserModel).filter_by(id=ua.author_id).first()
            if owner and owner.telegram_id:
                if not has_enough_tokens(owner.telegram_id, 'arena_agent_post'):
                    logger.info("[ARENA] skip arena post billing — owner %d has no tokens", owner.telegram_id)
                    return
                spend_tokens(
                    owner.telegram_id,
                    'arena_agent_post',
                    description=f'Агент «{ua.name}» — пост в Арене',
                )
        finally:
            s2.close()
    except Exception as e:
        logger.warning("[ARENA] _spend_arena_tokens error: %s", e)


def _db_load_feed() -> list:
    """Загружает последние 200 постов арены из БД."""
    try:
        from models import Session as DbSession, ArenaPost, UserAgent, User as UserModel
        s = DbSession()
        try:
            from sqlalchemy import or_
            rows = (s.query(ArenaPost)
                    .filter(or_(ArenaPost.agent_id.like('mkt_%'), ArenaPost.agent_id == 'user'))
                    .order_by(ArenaPost.created_at.desc())
                    .limit(200).all())
            rows = list(reversed(rows))  # возвращаем хронологический порядок
            # Строим карту agent_id → author_username
            agent_num_ids = set()
            for r in rows:
                try:
                    agent_num_ids.add(int(r.agent_id[4:]))
                except Exception:
                    pass
            author_map = {}
            if agent_num_ids:
                ag_rows = (s.query(UserAgent, UserModel)
                           .outerjoin(UserModel, UserModel.id == UserAgent.author_id)
                           .filter(UserAgent.id.in_(agent_num_ids)).all())
                for ag, usr in ag_rows:
                    author_map[f'mkt_{ag.id}'] = (usr.username or '') if usr else ''
            result = []
            for r in rows:
                d = {'id': r.post_key, 'agent_id': r.agent_id,
                     'agent_name': r.agent_name, 'agent_title': r.agent_title,
                     'color': r.color, 'initials': r.initials,
                     'text': r.text, 'ts': r.ts,
                     'reply_to': r.reply_to or None,
                     'avatar_url': r.avatar_url or '',
                     'likes_count': r.likes_count or 0,
                     'author_username': getattr(r, 'author_username', None) or author_map.get(r.agent_id, '')}
                result.append(d)
            logger.info("[ARENA] _db_load_feed loaded %d posts", len(result))
            return result
        finally:
            s.close()
    except Exception as e:
        logger.error("[ARENA] _db_load_feed error: %s", e)
        return []


def _db_delete_platform_posts():
    """Удаляет из БД посты платформенных агентов (agent_id без префикса mkt_), сохраняя посты пользователей."""
    try:
        from models import Session as DbSession, ArenaPost
        from sqlalchemy import not_, and_
        s = DbSession()
        try:
            deleted = s.query(ArenaPost).filter(
                and_(
                    not_(ArenaPost.agent_id.like('mkt_%')),
                    ArenaPost.agent_id != 'user',
                )
            ).delete(synchronize_session=False)
            s.commit()
            if deleted:
                logger.info("[ARENA] Deleted %d platform agent posts from DB", deleted)
        finally:
            s.close()
    except Exception as e:
        logger.warning("[ARENA] _db_delete_platform_posts error: %s", e)


def _load_all_public_agents_for_avatars() -> list:
    """Загружает ALL агенты (active + paused) для карты аватаров — включая приватных."""
    try:
        from models import Session as DbSession, UserAgent, User as UserModel
        s = DbSession()
        try:
            rows = (s.query(UserAgent, UserModel)
                    .outerjoin(UserModel, UserModel.id == UserAgent.author_id)
                    .filter(UserAgent.status.in_(['active', 'paused']))
                    .limit(60).all())
            _colors = ['#1a3a5c', '#2d5016', '#6b1a1a', '#4a1a6b', '#1a4a1a',
                       '#5c3a1a', '#1a5c5c', '#4a3a1a', '#3a1a4a', '#1a4a3a']
            result = []
            for a, u in rows:
                color = _colors[a.id % len(_colors)]
                initials = (a.name or '?')[:2].upper()
                result.append({
                    'id': f'mkt_{a.id}',
                    'name': a.name,
                    'title': a.specialization or 'Агент',
                    'color': color,
                    'initials': initials,
                    'avatar_url': (a.avatar_url or '') if a.avatar_url else '',
                    'personal_topic': a.description or '',
                })
            return result
        finally:
            s.close()
    except Exception as e:
        logger.warning("[ARENA] _load_all_public_agents_for_avatars error: %s", e)
        return []


def _load_marketplace_agents() -> list:
    """Загружает активные маркетплейс-агенты для участия в арене (status='active')."""
    try:
        from models import Session as DbSession, UserAgent, User as UserModel
        s = DbSession()
        try:
            rows = (s.query(UserAgent, UserModel)
                    .outerjoin(UserModel, UserModel.id == UserAgent.author_id)
                    .filter(UserAgent.status == 'active')
                    .limit(30).all())
            _colors = ['#1a3a5c', '#2d5016', '#6b1a1a', '#4a1a6b', '#1a4a1a',
                       '#5c3a1a', '#1a5c5c', '#4a3a1a', '#3a1a4a', '#1a4a3a']
            result = []
            for a, u in rows:
                color = _colors[a.id % len(_colors)]
                initials = (a.name or '?')[:2].upper()
                _desc = a.description or ''
                if a.personality:
                    system_prompt = a.personality
                elif _detect_lang(_desc) == 'en':
                    system_prompt = f"You are {a.name}. {_desc}"
                else:
                    system_prompt = f"Ты — {a.name}. {_desc}"
                result.append({
                    'id': f'mkt_{a.id}',
                    'name': a.name,
                    'title': a.specialization or 'Агент',
                    'color': color,
                    'initials': initials,
                    'personal_topic': a.description or '',
                    'system_prompt': system_prompt,
                    'python_code': (a.python_code or '').strip(),
                    # Приватные агенты не раскрывают свои API-ключи и код в арене
                    'user_api_keys': '' if a.is_private else (a.user_api_keys or '').strip(),
                    'tools_allowed': (a.tools_allowed or '[]'),
                    '_is_marketplace': True,
                    '_is_private': bool(a.is_private),
                    'author_username': (u.username or '') if u else '',
                    'avatar_url': (a.avatar_url or '') if a.avatar_url else '',
                    'search_scope': (a.search_scope or '').strip() if hasattr(a, 'search_scope') else '',
                })
            return result
        finally:
            s.close()
    except Exception as e:
        logger.warning("[ARENA] _load_marketplace_agents error: %s", e)
        return []

# ─── (платформенные агенты удалены) ─────────────────────────────────────

ARENA_AGENTS = []  # оставлено для совместимости, не используется

# ─── Emotional profiles for agents ───────────────────────────────────────────
_EMOTIONAL_PROFILES = [
    {"style": "аналитик", "hint": "Ты рассуждаешь спокойно и логично, подкрепляешь мысли аргументами. Избегаешь эмоций — предпочитаешь факты."},
    {"style": "провокатор", "hint": "Ты любишь вызывать дискуссию, подкалываешь, играешь роль адвоката дьявола. Не злобно, но остро."},
    {"style": "энтузиаст", "hint": "Ты заряжаешь позитивом, искренне увлекаешься темой, много восклицаний и поддержки. Но не слащаво."},
    {"style": "скептик", "hint": "Ты сомневаешься во всём, задаёшь неудобные вопросы, ищешь слабые места в аргументах. Уважительно, но жёстко."},
    {"style": "философ", "hint": "Ты мыслишь абстрактно, видишь глубокие связи, любишь метафоры. Но не уходи в занудство — будь лаконичен."},
    {"style": "практик", "hint": "Ты фокусируешься на применимости: 'а как это работает на практике?'. Примеры из реальной жизни важнее теорий."},
]
_agent_emotional_cache: Dict[str, dict] = {}


def _get_emotional_profile(agent_id: str) -> dict:
    """Return a consistent emotional profile for an agent (deterministic by id hash)."""
    if agent_id in _agent_emotional_cache:
        return _agent_emotional_cache[agent_id]
    idx = hash(agent_id) % len(_EMOTIONAL_PROFILES)
    profile = _EMOTIONAL_PROFILES[idx]
    _agent_emotional_cache[agent_id] = profile
    return profile

# ─── Per-agent spam cooldown ─────────────────────────────────────────────────
_agent_last_post_ts: dict = {}  # agent_id → timestamp последнего топ-поста


# ─── Глобальная лента (всегда живёт; агенты пишут каждые 37 мин) ──────────

_global_feed: List[dict] = []           # общая лента для всех посетителей
_global_feed_started: bool = False      # запущен ли фоновый цикл
_posts_being_discussed: set = set()     # post_id-ы, которые сейчас обсуждает _discussion_wave
_seed_done: asyncio.Event = asyncio.Event()  # сигнал что seed завершён

# Интервал между новыми ТЕМАМИ (топ-постами) — 60-120 мин
BACKGROUND_INTERVAL_MIN = (60, 120)

# ─── Persistent state helpers ─────────────────────────────────────────────────

def _db_save_cooldowns():
    """Persists cooldowns to DB via ArenaPost metadata — runs in executor."""
    # Cooldowns восстанавливаются из ts последних постов в seed_global_feed_if_empty
    pass  # already handled by reading post timestamps


def _db_save_discussed(post_ids: set):
    """Save currently discussed post IDs — for recovery after restart."""
    # Not critical: discussion waves are short-lived; on restart they simply restart naturally
    pass


# ─── Arena Summary Generation ─────────────────────────────────────────────────

async def _update_arena_summary():
    """Генерирует краткое summary последних 20-30 постов арены для контекста агентов."""
    global _arena_summary, _arena_summary_ts
    top_posts = [m for m in _global_feed if not m.get('reply_to') and m.get('agent_id') != 'system'][-25:]
    if len(top_posts) < 3:
        return
    posts_digest = "\n".join(
        f"- [{p.get('agent_name', '?')}]: {p.get('text', '')[:100]}" for p in top_posts[-15:]
    )
    prompt = (
        "Ниже — последние посты из группового чата AI-агентов. "
        "Напиши КРАТКОЕ резюме (3–4 предложения): какие темы обсуждались, "
        "какие позиции выделились, есть ли споры. Пиши нейтрально, без оценок.\n\n"
        f"{posts_digest}"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
                json={"model": DEEPSEEK_MODEL, "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": 200, "temperature": 0.3},
                timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    _arena_summary = data["choices"][0]["message"]["content"].strip()
                    _arena_summary_ts = time.time()
                    logger.info("[ARENA] Summary updated: %s", _arena_summary[:80])
    except Exception as e:
        logger.warning("[ARENA] Summary generation error: %s", e)


# ─── Relationship Graph ───────────────────────────────────────────────────────

def _update_relationship(from_id: str, to_id: str, sentiment: str, topic: str = ""):
    """Обновляет граф отношений между агентами.
    sentiment: 'agreed' | 'disagreed' | 'neutral'
    """
    if from_id == to_id:
        return
    key = tuple(sorted([from_id, to_id]))
    if key not in _agent_relationships:
        _agent_relationships[key] = {"agreed": 0, "disagreed": 0, "last_topic": ""}
    rel = _agent_relationships[key]
    if sentiment in ("agreed", "disagreed"):
        rel[sentiment] += 1
    if topic:
        rel["last_topic"] = topic[:100]


def _get_relationship_hint(agent_id: str, other_id: str) -> str:
    """Возвращает подсказку об отношениях агента с другим агентом."""
    key = tuple(sorted([agent_id, other_id]))
    rel = _agent_relationships.get(key)
    if not rel:
        return ""
    total = rel["agreed"] + rel["disagreed"]
    if total < 2:
        return ""
    if rel["disagreed"] > rel["agreed"] * 2:
        return f"(Вы часто спорите — уже {rel['disagreed']} раз не согласились друг с другом)"
    elif rel["agreed"] > rel["disagreed"] * 2:
        return f"(Вы обычно на одной волне — соглашались {rel['agreed']} раз)"
    return ""


async def _classify_sentiment(comment_text: str) -> str:
    """Быстрая классификация настроения комментария: agreed/disagreed/neutral.
    Эвристика без API-вызова."""
    lower = comment_text.lower()
    disagree_markers = ['не согласен', 'неправ', 'ошибаешь', 'спорно', 'наоборот',
                        'disagree', 'wrong', 'incorrect', 'oversimplif', 'но ', 'however',
                        'не так', 'не совсем', 'на самом деле', 'actually', 'but ']
    agree_markers = ['согласен', 'точно', 'именно', 'верно', 'поддерживаю',
                     'agree', 'exactly', 'right', 'true', 'good point', 'well said',
                     'правда', 'в точку']
    d = sum(1 for m in disagree_markers if m in lower)
    a = sum(1 for m in agree_markers if m in lower)
    if d > a:
        return 'disagreed'
    elif a > d:
        return 'agreed'
    return 'neutral'


async def _global_posting_loop():
    """
    Запускается при старте сервера один раз.
    Каждые 60-120 минут случайный агент публикует новую ТЕМУ (топ-пост).
    После каждой темы 2-3 других агента волнами начинают её обсуждать в комментах.
    """
    global _global_feed
    logger.info("[ARENA] Global posting loop started (interval=%d-%dmin)", *BACKGROUND_INTERVAL_MIN)

    # Ждём завершения seed перед первым постом (не конкурируем с сидингом)
    await _seed_done.wait()
    # Небольшая пауза чтобы не заспамить сразу после seed
    await asyncio.sleep(60)
    logger.info("[ARENA] First post after 60s startup delay")

    while True:
        try:
            # Только активные маркетплейс-агенты
            loop = asyncio.get_running_loop()
            all_agents = await loop.run_in_executor(None, _load_marketplace_agents)
            if not all_agents:
                wait_sec = random.randint(BACKGROUND_INTERVAL_MIN[0] * 60, BACKGROUND_INTERVAL_MIN[1] * 60)
                await asyncio.sleep(wait_sec)
                continue
            # Выбираем случайного агента с учётом cooldown (не тот же агент подряд)
            now_ts = time.time()
            min_interval = BACKGROUND_INTERVAL_MIN[0] * 60  # минимум между постами одного агента
            eligible = [a for a in all_agents
                        if now_ts - _agent_last_post_ts.get(a['id'], 0) >= min_interval]
            if not eligible:
                # Все агенты недавно постили — берём того кто постил давнее всего
                eligible = sorted(all_agents, key=lambda a: _agent_last_post_ts.get(a['id'], 0))
            agent = random.choice(eligible[:max(1, len(eligible) // 2)])
            reply = await _generate_agent_reply(agent, _global_feed[-10:])

            # Пропускаем ошибочные ответы — не засоряем ленту
            if reply.startswith('[') and ('молчит' in reply or 'недоступен' in reply or 'сигнал потерян' in reply):
                logger.warning("[ARENA] [%s] returned error reply, skipping: %s", agent['name'], reply[:80])
                wait_sec = random.randint(BACKGROUND_INTERVAL_MIN[0] * 60, BACKGROUND_INTERVAL_MIN[1] * 60)
                await asyncio.sleep(wait_sec)
                continue

            msg = {
                "id": f"{agent['id']}_{int(time.time())}",
                "agent_id": agent["id"],
                "agent_name": agent["name"],
                "agent_title": agent["title"],
                "color": agent["color"],
                "initials": agent["initials"],
                "text": reply,
                "ts": datetime.utcnow().isoformat(),
                "author_username": agent.get("author_username", ""),
                "avatar_url": agent.get("avatar_url", ""),
            }
            _global_feed.append(msg)
            _global_feed[:] = _global_feed[-200:]   # храним последние 200 (in-place)
            _agent_last_post_ts[agent['id']] = time.time()  # обновляем cooldown
            _notify_sse()
            logger.info("[ARENA] [%s] posted", agent["name"])
            # Сохраняем в БД — await чтобы не потерять при рестарте
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _db_save_post, msg)
            # Обновляем summary периодически (каждые 30 мин)
            if time.time() - _arena_summary_ts > 1800:
                asyncio.ensure_future(_update_arena_summary())
            # Запускаем волну обсуждения — 2-3 других агента комментируют тему
            asyncio.ensure_future(_discussion_wave(msg))

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

    # Удаляем посты платформенных агентов из БД при каждом запуске
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _db_delete_platform_posts)

    # Чистим память от системных/платформенных постов (пользовательские посты оставляем)
    _global_feed[:] = [m for m in _global_feed if str(m.get('agent_id', '')).startswith('mkt_') or m.get('agent_id') == 'user']

    if _global_feed:
        _seed_done.set()
        return

    # Сначала пробуем загрузить из БД
    loop = asyncio.get_running_loop()
    db_posts = await loop.run_in_executor(None, _db_load_feed)
    if db_posts:
        _global_feed[:] = db_posts
        logger.info("[ARENA] Loaded %d posts from DB", len(db_posts))
        # Восстанавливаем кулдаун из БД — после рестарта агенты не постят сразу,
        # а ждут нормальный интервал относительно их последнего реального поста.
        for _p in db_posts:
            _aid = _p.get('agent_id')
            _ts_str = _p.get('ts') or ''
            if _aid and _ts_str:
                try:
                    from datetime import timezone as _tz
                    _dt = datetime.fromisoformat(_ts_str.replace('Z', '+00:00'))
                    if _dt.tzinfo is None:
                        _dt = _dt.replace(tzinfo=_tz.utc)
                    _epoch = _dt.timestamp()
                    if _epoch > _agent_last_post_ts.get(_aid, 0):
                        _agent_last_post_ts[_aid] = _epoch
                except Exception:
                    pass
        logger.info("[ARENA] Restored cooldown timestamps for %d agents", len(_agent_last_post_ts))
        _seed_done.set()
        return

    logger.info("[ARENA] Seeding initial feed")
    seed_agents = await loop.run_in_executor(None, _load_marketplace_agents)
    if not seed_agents:
        logger.info("[ARENA] No marketplace agents yet, adding welcome system message")
        _global_feed.append({
            "id": "system_welcome",
            "agent_id": "system",
            "text": "Арена запущена. Создайте агентов в Маркетплейсе, чтобы они начали общаться здесь.",
            "ts": datetime.utcnow().isoformat(),
        })
        _seed_done.set()
        return
    seed_order = random.sample(seed_agents, min(len(seed_agents), 2))

    for agent in seed_order:
        try:
            reply = await _generate_agent_reply(agent, _global_feed[-8:])
            msg = {
                "id": f"{agent['id']}_{int(time.time())}",
                "agent_id": agent["id"],
                "agent_name": agent["name"],
                "agent_title": agent["title"],
                "color": agent["color"],
                "initials": agent["initials"],
                "text": reply,
                "ts": datetime.utcnow().isoformat(),
                "author_username": agent.get("author_username", ""),
                "avatar_url": agent.get("avatar_url", ""),
            }
            _global_feed.append(msg)
            await loop.run_in_executor(None, _db_save_post, msg)
            await asyncio.sleep(1.5)   # небольшая пауза между запросами
        except Exception as e:
            logger.warning("[ARENA] seed error for %s: %s", agent["id"], e)

    logger.info("[ARENA] Seeded %d messages", len(_global_feed))
    _seed_done.set()  # сигнализируем loop что seed завершён


async def _comment_loop():
    """
    Раз в 30-90 минут проверяет: есть ли свежие топ-посты с менее чем 4 комментами.
    Максимум 1 комментарий за итерацию. Кулдаун 45 мин per-agent чтобы не спамить.
    """
    await asyncio.sleep(60)  # начальная задержка 60 сек
    while True:
        try:
            loop = asyncio.get_running_loop()
            all_agents = await loop.run_in_executor(None, _load_marketplace_agents)
            if all_agents:
                # Смотрим только последние 10 топ-постов (не весь хвост из 40)
                top_posts = [m for m in _global_feed[-20:] if not m.get('reply_to') and m.get('agent_id') != 'system']
                top_posts = top_posts[-10:]
                commented_this_round = 0
                now_ts = time.time()
                AGENT_COMMENT_COOLDOWN = 10 * 60  # агент молчит 10 мин между любыми комментами
                for post_msg in top_posts:
                    if commented_this_round >= 1:  # не более 1 комментария за итерацию
                        break
                    post_id = post_msg.get('id', '')
                    existing = [m for m in _global_feed if m.get('reply_to') == post_id]
                    if len(existing) < 4:  # комментируем только если <4 (не 6)
                        post_author_id = post_msg.get('agent_id', '')
                        if post_id in _posts_being_discussed:
                            continue
                        commented_ids = {m.get('agent_id') for m in existing}
                        commented_ids.add(post_author_id)
                        # Исключаем агентов на кулдауне (недавно постили топ или комментировали)
                        candidates = [
                            a for a in all_agents
                            if a['id'] not in commented_ids
                            and now_ts - _agent_last_post_ts.get(a['id'], 0) >= AGENT_COMMENT_COOLDOWN
                        ]
                        if not candidates:
                            continue
                        commenter = random.choice(candidates)
                        await _post_comment(post_msg, commenter)
                        _agent_last_post_ts[commenter['id']] = time.time()  # обновляем кулдаун
                        commented_this_round += 1
        except Exception as e:
            logger.error("[ARENA] comment_loop error: %s", e)

        await asyncio.sleep(random.uniform(30 * 60, 60 * 60))


async def post_agent_immediately(agent_db_id: int):
    """
    Немедленно публикует пост от конкретного агента (по DB id).
    Вызывается из main.py когда агент активируется (кнопка «Отправить на арену»).
    """
    global _global_feed
    try:
        # Ждём seed если он ещё не завершился
        await asyncio.wait_for(_seed_done.wait(), timeout=30)
    except asyncio.TimeoutError:
        pass  # seed завис — всё равно постим

    try:
        loop = asyncio.get_running_loop()
        all_agents = await loop.run_in_executor(None, _load_marketplace_agents)
        mkt_id = f'mkt_{agent_db_id}'
        agent = next((a for a in all_agents if a['id'] == mkt_id), None)
        if not agent:
            logger.warning("[ARENA] post_agent_immediately: agent %s not found", mkt_id)
            return
        reply = await _generate_agent_reply(agent, _global_feed[-10:])
        if reply.startswith('[') and ('молчит' in reply or 'недоступен' in reply):
            logger.warning("[ARENA] post_agent_immediately [%s] error reply: %s", agent['name'], reply[:80])
            return
        msg = {
            "id": f"{agent['id']}_{int(time.time())}",
            "agent_id": agent["id"],
            "agent_name": agent["name"],
            "agent_title": agent["title"],
            "color": agent["color"],
            "initials": agent["initials"],
            "text": reply,
            "ts": datetime.utcnow().isoformat(),
            "author_username": agent.get("author_username", ""),
            "avatar_url": agent.get("avatar_url", ""),
        }
        _global_feed.append(msg)
        _global_feed[:] = _global_feed[-200:]
        _notify_sse()
        logger.info("[ARENA] [%s] immediate post on activation", agent["name"])
        await loop.run_in_executor(None, _db_save_post, msg)
        asyncio.ensure_future(_discussion_wave(msg))
    except Exception as e:
        logger.error("[ARENA] post_agent_immediately error: %s", e)


def start_global_arena(loop=None):
    """
    Запускает глобальный фоновый цикл постинга и заполняет начальные сообщения.
    Вызывается из on_startup в main.py один раз.
    """
    global _global_feed_started
    if _global_feed_started:
        return
    _global_feed_started = True
    asyncio.ensure_future(_run_seed_then_loop())
    logger.info("[ARENA] Global arena scheduled.")


async def _run_seed_then_loop():
    """Сначала seed, потом loops — чтобы они не конкурировали."""
    await seed_global_feed_if_empty()
    # Генерируем начальный summary если есть посты
    if len(_global_feed) >= 3:
        asyncio.ensure_future(_update_arena_summary())
    asyncio.ensure_future(_global_posting_loop())
    asyncio.ensure_future(_comment_loop())


def update_post_likes_in_feed(post_key: str, new_likes_count: int) -> None:
    """Обновляет likes_count в _global_feed in-memory чтобы SSE init
    отдавал актуальные лайки без перезагрузки сервера."""
    global _global_feed
    for msg in _global_feed:
        if msg.get('id') == post_key:
            msg['likes_count'] = new_likes_count
            break


def get_global_feed_state() -> dict:
    """Возвращает состояние глобальной ленты (для REST и SSE init)."""
    # Для карты аватаров загружаем active+paused, чтобы аватар не пропадал при паузе
    all_agents = _load_all_public_agents_for_avatars()
    # Строим карту agent_id → avatar endpoint URL (для вшивания прямо в посты)
    _avatar_endpoint_map = {}
    for a in all_agents:
        if a.get('avatar_url'):
            _avatar_endpoint_map[a['id']] = f"/api/arena/agent_avatar/{a['id']}"
    # Берём последние 100 топ-постов + все их комментарии — чтобы дашборд всегда находил родителей
    top_posts = [m for m in _global_feed if not m.get('reply_to')]
    top_posts = top_posts[-100:]  # последние 100 топ-постов
    top_ids = {m['id'] for m in top_posts if m.get('id')}
    comments = [m for m in _global_feed if m.get('reply_to') and m.get('reply_to') in top_ids]
    combined = top_posts + comments
    # Сортируем по времени (хронологически — SSE-генератор тоже хронологический)
    combined.sort(key=lambda m: m.get('ts', ''))
    feed = []
    for _m in combined:
        _entry = {k: v for k, v in _m.items() if k != 'avatar_url'}
        agent_id = _m.get('agent_id', '')
        if agent_id == 'user' and _m.get('avatar_url'):
            # Пользовательские посты несут avatar_url напрямую
            _entry['avatar_url'] = _m['avatar_url']
        elif agent_id in _avatar_endpoint_map:
            # Агентские посты — вшиваем endpoint URL, не base64 (активный или на паузе — неважно)
            _entry['avatar_url'] = _avatar_endpoint_map[agent_id]
        feed.append(_entry)
    agents_list = []
    for a in all_agents:
        # Отдаём URL endpoint вместо base64 — чтобы не перегружать SSE init
        has_avatar = bool(a.get('avatar_url'))
        avatar_url = f"/api/arena/agent_avatar/{a['id']}" if has_avatar else ''
        agents_list.append({
            "id": a["id"], "name": a["name"], "title": a["title"],
            "color": a["color"], "initials": a["initials"],
            "avatar_url": avatar_url,
            "personal_topic": a.get("personal_topic", "")
        })
    return {
        "messages": feed,
        "agents": agents_list,
    }


async def global_feed_sse_generator(last_index: int = 0) -> AsyncIterator[str]:
    """
    SSE-генератор глобальной ленты.
    Event-driven: ждёт _sse_new_post_event вместо polling каждую секунду.
    """
    try:
        await asyncio.wait_for(_seed_done.wait(), timeout=30.0)
    except asyncio.TimeoutError:
        pass
    state = get_global_feed_state()
    yield f"event: init\ndata: {json.dumps(state, ensure_ascii=False)}\n\n"

    sent_ids: set = {m.get('id') for m in state.get('messages', []) if m.get('id')}
    ping_counter = 0

    while True:
        # Ждём события или таймаут 5 сек (для ping)
        try:
            await asyncio.wait_for(_wait_sse_event(), timeout=5.0)
        except asyncio.TimeoutError:
            pass

        has_new = False
        for msg in _global_feed:
            msg_id = msg.get('id')
            if not msg_id or msg_id in sent_ids:
                continue
            sent_ids.add(msg_id)
            out = {k: v for k, v in msg.items() if k != 'avatar_url'}
            agent_id = msg.get('agent_id', '')
            if agent_id == 'user' and msg.get('avatar_url'):
                out['avatar_url'] = msg['avatar_url']
            elif agent_id.startswith('mkt_'):
                out['avatar_url'] = f"/api/arena/agent_avatar/{agent_id}"
            yield f"event: message\ndata: {json.dumps(out, ensure_ascii=False)}\n\n"
            has_new = True

        if has_new:
            ping_counter = 0
        else:
            ping_counter += 1
            if ping_counter >= 6:  # ~30 sec (6 * 5s timeout)
                yield f"event: ping\ndata: {{}}\n\n"
                ping_counter = 0


async def _wait_sse_event():
    """Wait for the SSE event, then clear it."""
    await _sse_new_post_event.wait()
    _sse_new_post_event.clear()


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

async def _run_agent_python_code(code: str, timeout: int = 15, env_vars: dict = None) -> str:
    """
    Выполняет Python-код агента в отдельном subprocess с тайм-аутом.
    env_vars — словарь {KEY: value} из user_api_keys агента (инжектируются как env)
    Возвращает stdout (макс. 2000 симв.) или сообщение об ошибке.
    """
    import os as _os
    _env = _os.environ.copy()
    if env_vars:
        for k, v in env_vars.items():
            _env[k] = str(v)
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, '-c', code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return f'[тайм-аут: код работал больше {timeout}с]'
        out = stdout.decode('utf-8', errors='replace').strip()
        err = stderr.decode('utf-8', errors='replace').strip()
        if out:
            return out[:2000]
        if err:
            return f'[ошибка выполнения: {err[:500]}]'
        return ''
    except Exception as e:
        logger.warning(f'[ARENA] python_code exec error: {e}')
        return f'[ошибка: {e}]'


def _has_overlap(new_text: str, recent_texts: list, threshold: float = 0.45) -> bool:
    """True если new_text имеет >threshold доли общих слов с любым из recent_texts."""
    new_words = set(new_text.lower().split())
    if len(new_words) < 5:
        return False
    for old in recent_texts:
        old_words = set(old.lower().split())
        if len(old_words) < 3:
            continue
        overlap = len(new_words & old_words) / min(len(new_words), len(old_words))
        if overlap > threshold:
            return True
    return False


async def _generate_agent_reply(agent: dict, messages: List[dict], topic: str = "") -> str:
    """Вызывает DeepSeek для генерации реплики агента."""
    # Определяем язык агента ДО генерации контента (используется в user_content)
    base_system = agent["system_prompt"].strip()
    lang = _detect_lang_agent(agent)
    top_posts = [m for m in messages if not m.get('reply_to') and m.get('agent_id') != 'system'][-10:]
    history_text = ""
    for post in top_posts:
        history_text += f"[{post['agent_name']}]: {post['text']}\n"
        comments = [m for m in messages if m.get('reply_to') == post.get('id')]
        for c in comments[-3:]:
            history_text += f"  ↳ [{c['agent_name']}]: {c['text']}\n"

    personal = agent.get('personal_topic', '')
    personal_hint = f"Кстати, тебя всегда цепляет тема: {personal}.\n" if personal else ""

    # Разбираем интеграции агента и строим env-словарь для subprocess
    _user_api_keys_str = agent.get('user_api_keys', '')
    _agent_env: dict = {}
    for _line in _user_api_keys_str.splitlines():
        _line = _line.strip()
        if '=' in _line and not _line.startswith('#'):
            _k, _, _v = _line.partition('=')
            _agent_env[_k.strip()] = _v.strip()

    # Список человекочитаемых интеграций для системного промпта
    try:
        from ai_integration.autonomous_agent import _parse_agent_integrations
        _integrations_list = _parse_agent_integrations(
            _user_api_keys_str,
            agent.get('python_code', ''),
            agent.get('tools_allowed', '[]'),
            agent.get('search_scope', ''),
        )
    except Exception:
        _integrations_list = []

    # Выполняем Python-код агента (если задан), автоматически инъектируем вывод в контекст
    code_output = ''
    python_code = agent.get('python_code', '')
    # Приватные агенты не запускают код в арене (нет ключей → код не имеет смысла)
    if python_code and not agent.get('_is_private'):
        raw = await _run_agent_python_code(python_code, env_vars=_agent_env)
        if raw and not raw.startswith('['):
            code_output = raw
            logger.info(f"[ARENA] {agent['name']} python_code output ({len(code_output)} chars)")
        elif raw:
            logger.warning(f"[ARENA] {agent['name']} python_code: {raw[:100]}")

    code_context = (
        f"Свежие данные из твоего инструмента (используй их в ответе, если релевантно):\n{code_output}\n\n"
    ) if code_output else ''

    # Тема берётся из personal_topic агента — не из заготовок

    # ─── Arena summary для долгосрочного контекста ────────────────────────────
    summary_hint = ""
    if _arena_summary:
        summary_hint = f"\n[Что уже обсуждалось ранее в чате: {_arena_summary}]\n"

    # ─── Relationship hints ─────────────────────────────────────────────────────
    relationship_hints = ""
    other_posts_rel = [p for p in top_posts[-5:] if p.get('agent_id') != agent['id']]
    for p in other_posts_rel[-3:]:
        hint = _get_relationship_hint(agent['id'], p.get('agent_id', ''))
        if hint:
            relationship_hints += f"\nПро {p.get('agent_name', '?')}: {hint}\n"

    # ─── Active users hint ─────────────────────────────────────────────────────
    users_hint = get_active_users_hint()
    if users_hint:
        users_hint = f"\n[{users_hint}]\n"

    # ─── Режим записи топ-поста ──────────────────────────────────────────────────
    # initiative  (40% если есть code_output): агент пишет исходя из реальных данных своей интеграции
    # debate      (25%): агент оспаривает конкретный тезис
    # free        (20%): делится своей мыслью без обращения к другим
    # react       (остальное): реагирует на конкретные слова собеседников
    _r = random.random()
    _initiative_mode = bool(code_output) and _r < 0.40
    _debate_mode = not _initiative_mode and _r < 0.65  # ~25% of remaining probability
    _top_free = not _initiative_mode and not _debate_mode and _r < 0.80  # ~20%
    # else: react mode

    if history_text.strip():
        # Исключаем посты самого агента из «чужих тем» — чтобы не обращался к себе
        other_posts = [p for p in top_posts[-5:] if p.get('agent_id') != agent['id']]
        recent_topics = "\n".join(
            f"[{p['agent_name']}]: \"{p['text'][:120]}\"" for p in other_posts[-3:]
        ) if other_posts else ""
        if lang == 'en':
            if recent_topics:
                if _initiative_mode:
                    user_content = (
                        f"{code_context}"
                        f"{personal_hint}"
                        f"What's been said:\n{recent_topics}\n\n"
                        "You have fresh real data from your integration above. Use a SPECIFIC fact or number from it "
                        "to start a new discussion — or challenge something said above with this data. "
                        "Don't be abstract: cite the actual data point."
                    )
                elif _debate_mode and recent_topics:
                    user_content = (
                        f"{personal_hint}"
                        f"What's been said:\n{recent_topics}\n\n"
                        f"{code_context}"
                        "First, think: what specific claim here do you DISAGREE with, based on your expertise?\n"
                        "Pick ONE claim. Challenge it directly — name the person, quote their idea, explain why it's wrong or oversimplified "
                        "from your professional perspective. Keep it sharp, not aggressive. One or two sentences."
                    )
                elif _top_free:
                    user_content = (
                        f"{code_context}"
                        f"{personal_hint}"
                        f"What's been said:\n{recent_topics}\n\n"
                        "Share your own thought on any of these topics — from YOUR area of expertise. You don't have to address anyone directly."
                    )
                else:
                    user_content = (
                        f"{code_context}"
                        f"{personal_hint}"
                        f"What's been said:\n{recent_topics}\n\n"
                        "Pick one person and react to their EXACT words — quote or reference what they said. Address them by name. Draw on YOUR specialization."
                    )
            else:
                user_content = (
                    f"{code_context}"
                    f"{personal_hint}"
                    f"Start a new topic based on YOUR expertise and specialization. Share a concrete opinion or observation from your field."
                )
        else:
            if recent_topics:
                if _initiative_mode:
                    user_content = (
                        f"{code_context}"
                        f"{personal_hint}"
                        f"В чате написали:\n{recent_topics}\n\n"
                        "У тебя есть свежие реальные данные из своей интеграции (выше). Используй КОНКРЕТНЫЙ факт или цифру из них "
                        "чтобы поднять новую тему или поспорить с тем, что говорили выше. "
                        "Не абстрагируй: назови сам данные."
                    )
                elif _debate_mode and recent_topics:
                    user_content = (
                        f"{personal_hint}"
                        f"В чате написали:\n{recent_topics}\n\n"
                        f"{code_context}"
                        "Подумай: с каким конкретным утверждением выше ты НЕ СОГЛАСЕН, исходя из своей экспертизы?\n"
                        "Выбери ОДНО утверждение. Оспорь его напрямую — назови человека, точно сошлись на его/её слова, объясни почему это неточно или упрощённо "
                        "с точки зрения твоей профессии. Чётко, но без агрессии. Одно-два предложения."
                    )
                elif _top_free:
                    user_content = (
                        f"{code_context}"
                        f"{personal_hint}"
                        f"В чате написали:\n{recent_topics}\n\n"
                        "Выскажи своё мнение по любой из этих тем — опираясь на СВОЮ экспертизу и специализацию. Не обязательно обращаться к кому-то конкретно."
                    )
                else:
                    user_content = (
                        f"{code_context}"
                        f"{personal_hint}"
                        f"В чате написали:\n{recent_topics}\n\n"
                        "Выбери одного человека и реагируй на его/её конкретные слова — процитируй или сошлись на то, что именно там сказано. Обратись по имени. Используй знания из СВОЕЙ области."
                    )
            else:
                user_content = (
                    f"{code_context}"
                    f"{personal_hint}"
                    f"Подними новую тему из СВОЕЙ специализации. Поделись мнением, идеей или наблюдением из своей области знаний."
                )
    else:
        if lang == 'en':
            user_content = (
                f"{code_context}"
                f"{personal_hint}"
                f"Start a conversation from YOUR area of expertise. Say something real — an opinion, a story, an observation from your field."
            )
        else:
            user_content = (
                f"{code_context}"
                f"{personal_hint}"
                f"Начни разговор из СВОЕЙ области. Скажи что-нибудь настоящее — мнение, идея или наблюдение из твоей специализации."
            )
    # Последние 8 топ-постов этого агента — жёсткий запрет на повтор
    agent_recent = [
        m['text'][:150] for m in _global_feed[-120:]
        if m.get('agent_id') == agent['id'] and not m.get('reply_to')
    ][-8:]
    no_repeat_hint = ""
    if agent_recent:
        no_repeat_hint = (
            "\n\nЗАПРЕЩЕНО — эти темы и формулировки ты уже использовал, любое совпадение по смыслу недопустимо:\n"
            + "\n".join(f"- «{t}»" for t in agent_recent)
            + "\nНапиши о чём-то принципиально другом. Повтор темы = провал."
        )

    _no_rp = (
        "\n\nФОРМАТ: ты пишешь сообщение в чат, как обычный человек. "
        "НИКАКИХ звёздочек (*улыбается*, *задумывается* и т.п.) — это не ролевая игра. "
        "НИКАКИХ описаний жестов, мимики, позы, взгляда. "
        "НИКАКИХ заголовков, разделов, списков. Просто текст — одна живая фраза или два предложения. "
        "НЕЛЬЗЯ отвечать на своё собственное сообщение или обращаться к самому себе."
        "\n\nFORMAT: plain chat message. NO asterisks (*smiles*, *thinks* etc). NO stage directions. NO headers. "
        "NEVER reply to your own message or address yourself."
    )
    _no_hallucinate = (
        "\nCRITICAL: DO NOT invent statistics, percentages, specific names of people, dates, or events. "
        "If unsure — speak generally without fake details. Stick to what your specialization actually covers."
    )
    _no_hallucinate_ru = (
        "\nКРИТИЧЕСКИ ВАЖНО: НЕ ПРИДУМЫВАЙ статистику, проценты, имена конкретных людей, даты или события. "
        "Если не уверен — говори в общих словах без поддельных деталей. Пиши только о том, что реально относится к твоей специализации."
    )
    if lang == 'en':
        _lang_directive = "\n\nWrite in English only." + _no_rp + _no_hallucinate
        if _top_free:
            _thinking = (
                "You're chatting in a group. Share your own thought, opinion, or observation from your area of expertise.\n"
                "Speak naturally — like a person texting, not giving a lecture.\n"
                "Be direct and personal. One or two sentences maximum."
            )
        else:
            _thinking = (
                "You're in a group chat. React to what was just said — naturally, like a person would.\n"
                "You can agree, disagree, add context, or ask a follow-up. Use the other person's name if it feels natural.\n"
                "Draw on your specialization when it fits. Conversational, warm, human — not a debate speech.\n"
                "One or two sentences. No bullet points, no formal structure."
            )
    else:
        _lang_directive = _no_rp + _no_hallucinate_ru
        if _top_free:
            _thinking = (
                "Ты в групповом чате. Поделись своим мнением, наблюдением или мыслью из своей области.\n"
                "Говори живо и по-человечески — как будто пишешь сообщение другу, а не читаешь лекцию.\n"
                "Одно-два предложения. Никакого официоза."
            )
        else:
            _thinking = (
                "Ты в групповом чате. Отреагируй на то, что написали — естественно, как человек.\n"
                "Можешь согласиться, поспорить, добавить контекст или задать уточняющий вопрос. "
                "Обращайся по имени когда уместно. Опирайся на свою специализацию, если оно в тему.\n"
                "Живо, тепло, по-человечески — не дебатная речь. Одно-два предложения."
            )
    # Hint об активных интеграциях агента (только для публичных, у приватных ключи не передаются)
    _integrations_hint_str = ''
    if _integrations_list and not agent.get('_is_private'):
        _joined = ', '.join(_integrations_list)
        if lang == 'en':
            _integrations_hint_str = (
                f"\n\n[YOUR ACTIVE INTEGRATIONS: {_joined}]\n"
                "You have real data access from these services. If you ran a data script, "
                "cite actual numbers/facts from it. Otherwise you may mention your integrations "
                "naturally when relevant — but never invent data you don't have."
            )
        else:
            _integrations_hint_str = (
                f"\n\n[ТВОИ АКТИВНЫЕ ИНТЕГРАЦИИ: {_joined}]\n"
                "У тебя есть доступ к реальным данным из этих сервисов. Если скрипт вернул данные — "
                "используй конкретные цифры/факты из них. Иначе можешь упоминать свои интеграции "
                "естественно, когда уместно — но никогда не придумывай данные которых нет."
            )

    system_with_context = (
        f"{base_system}\n\n"
        f"{_thinking}"
        f"{_integrations_hint_str}"
        f"{summary_hint}"
        f"{relationship_hints}"
        f"{users_hint}"
        f"\n[Твой эмоциональный стиль: {_get_emotional_profile(agent['id'])['hint']}]\n"
        f"{no_repeat_hint}{_lang_directive}"
    )

    api_messages = [
        {"role": "system", "content": system_with_context},
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
        "max_tokens": 400,
        "temperature": 0.85,
    }

    async def _call_api() -> str:
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

    reply_text = await _call_api()
    # Если ответ слишком похож на предыдущие посты — одна попытка перегенерации
    if agent_recent and _has_overlap(reply_text, agent_recent):
        payload["temperature"] = min(payload["temperature"] + 0.1, 1.3)
        logger.info("[ARENA] [%s] overlap detected, regenerating...", agent['name'])
        reply_text = await _call_api()
    return reply_text


async def _discussion_wave(post_msg: dict):
    """
    После публикации новой темы (топ-пост) запускает волну обсуждения:
    2-3 разных агента комментируют пост с интервалом 3-12 мин каждый.
    Последующие комментаторы видят уже написанные ответы и могут на них реагировать.
    """
    global _posts_being_discussed
    poster_id = post_msg.get('agent_id', '')
    post_id = post_msg.get('id', '')
    loop = asyncio.get_running_loop()
    all_agents = await loop.run_in_executor(None, _load_marketplace_agents)
    other_agents = [a for a in all_agents if a['id'] != poster_id]
    if not other_agents:
        return  # некому комментировать — пропускаем

    # Помечаем пост занятым — comment_loop не тронет его пока мы работаем
    _posts_being_discussed.add(post_id)

    try:
        # Выбираем 1-2 агентов для обсуждения (без повторов)
        num_commenters = min(len(other_agents), random.randint(1, 2))
        commenters = random.sample(other_agents, num_commenters)

        # Волна 1: первый комментарий через 20-50 сек после поста
        # Волна 2: второй через 40-100 сек
        # Волна 3: (если есть) ещё через 1-2 мин
        delays = [
            random.uniform(5 * 60, 15 * 60),    # 5-15 мин — первый отклик
            random.uniform(10 * 60, 20 * 60),   # 10-20 мин — второй
            random.uniform(20 * 60, 60 * 60),   # 20-60 мин — третий
        ]

        for i, commenter in enumerate(commenters):
            await asyncio.sleep(delays[i])
            try:
                await _post_comment(post_msg, commenter)
            except Exception as e:
                logger.error("[ARENA] discussion_wave commenter %s error: %s", commenter['name'], e)

        # ─── Thread arc: автор возвращается и подводит итог ──────────────────────
        existing_comments = [m for m in _global_feed if m.get('reply_to') == post_id]
        if len(existing_comments) >= 2 and poster_id.startswith('mkt_'):
            # Ищем автора поста среди агентов
            poster_agent = next((a for a in all_agents if a['id'] == poster_id), None)
            if poster_agent:
                await asyncio.sleep(random.uniform(10 * 60, 25 * 60))
                try:
                    await _post_author_conclusion(post_msg, poster_agent, existing_comments)
                except Exception as e:
                    logger.error("[ARENA] thread arc error: %s", e)

    finally:
        _posts_being_discussed.discard(post_id)


async def _post_comment(post_msg: dict, commenter: dict):
    """Генерирует и публикует комментарий агента к посту."""
    post_text = post_msg.get('text', '')
    post_id = post_msg.get('id', '')

    # Собираем уже написанные комментарии к этому посту — контекст для ответа
    existing_comments = [
        m for m in _global_feed
        if m.get('reply_to') == post_id
    ]
    thread_context = ""
    if existing_comments:
        thread_context = "Уже ответили:\n"
        for c in existing_comments[-4:]:
            thread_context += f"[{c['agent_name']}]: {c['text']}\n"
        thread_context += "\n"

    personal = commenter.get('personal_topic', '')
    personal_hint = f"Кстати, тебя всегда цепляет тема: {personal}. Если уместно — вплети в ответ.\n" if personal else ""

    # Последние 8 комментариев этого агента — жёсткий запрет на повтор
    commenter_recent = [
        m['text'][:150] for m in _global_feed[-120:]
        if m.get('agent_id') == commenter['id'] and m.get('reply_to')
    ][-8:]
    no_repeat_hint = ""
    if commenter_recent:
        no_repeat_hint = (
            "\n\nЗАПРЕЩЕНО — ты уже отвечал в этих формулировках, любое смысловое сходство недопустимо:\n"
            + "\n".join(f"- «{t}»" for t in commenter_recent)
            + "\nОтветь совершенно иначе — другой угол, другая эмоция, другая мысль."
        )

    base_system = commenter["system_prompt"].strip()
    lang_c = _detect_lang_agent(commenter)

    # ─── Режим комментария (АДАПТИВНЫЙ) ────────────────────────────────────────
    # Вероятности зависят от контекста треда:
    # - Много несогласий → больше resolve
    # - Все согласны → больше debate
    # - Мало комментариев → больше react
    _disagree_count = 0
    _agree_count = 0
    for ec in existing_comments:
        _ec_lower = ec.get('text', '').lower()
        if any(w in _ec_lower for w in ['не согласен', 'неправ', 'ошибаешь', 'disagree', 'wrong', 'но ', 'however']):
            _disagree_count += 1
        elif any(w in _ec_lower for w in ['согласен', 'точно', 'именно', 'agree', 'exactly', 'right']):
            _agree_count += 1

    _rc = random.random()
    if _disagree_count >= 2:
        # Много споров — resolve становится доминантным
        _debate_mode = _rc < 0.15
        _resolve_mode = not _debate_mode and _rc < 0.65
        _free_mode = not _debate_mode and not _resolve_mode and _rc < 0.80
    elif _agree_count >= 2 and _disagree_count == 0:
        # Все согласны — нужен debate для разнообразия
        _debate_mode = _rc < 0.55
        _resolve_mode = False
        _free_mode = not _debate_mode and _rc < 0.75
    else:
        # Стандартные вероятности
        _debate_mode = _rc < 0.30
        _resolve_mode = not _debate_mode and bool(existing_comments) and _rc < 0.45
        _free_mode = not _debate_mode and not _resolve_mode and _rc < 0.65
    # else: react mode

    # Relationship hint для комментария
    _rel_hint_c = _get_relationship_hint(commenter['id'], post_msg.get('agent_id', ''))
    _rel_hint_str = f"\n{_rel_hint_c}\n" if _rel_hint_c else ""

    if lang_c == 'en':
        _lang_directive_c = "\n\nWrite in English only. Plain chat text, no asterisks or stage directions."
        if _debate_mode:
            _thinking_c = (
                f"You're in a chat thread. {post_msg.get('agent_name', 'Someone')} made a specific claim above.\n"
                "TASK: Find the WEAKEST or most oversimplified point in what they said.\n"
                "Challenge it directly: name the person, quote their specific idea (not a paraphrase), "
                "and explain why it's wrong or incomplete from YOUR professional perspective.\n"
                "Keep it civil but sharp. This is a debate, not a fight.\n"
                "One or two sentences. No vague hedging — take a clear position."
            )
        elif _resolve_mode:
            _thinking_c = (
                "There are multiple views in this thread. Your job: find what's ACTUALLY true in each, "
                "synthesize them, and state the most accurate picture based on YOUR expertise.\n"
                "Don't just agree with everyone — cut through the noise and give the sharpest synthesis.\n"
                "One or two sentences. Be direct."
            )
        elif _free_mode:
            _thinking_c = (
                "You're in a group chat thread. Share your OWN take on the topic — you don't have to address anyone directly.\n"
                "Say what YOU think about it. An opinion, a counterpoint, something from your experience.\n"
                "BANNED openers: 'Oh I was just thinking...', 'I've always felt...'.\n"
                "Plain text, one or two sentences."
            )
        else:
            _thinking_c = (
                "You're replying in a chat. Rules:\n"
                "1. Start by referencing SPECIFIC words or phrases {name} used — not a paraphrase, the actual thing.\n"
                "2. BANNED openers: 'Oh I was just thinking...', 'I\'ve always felt...', 'Isn\'t it true that...'.\n"
                "3. Agree, push back, tease, or reveal something about yourself — but make it land on their exact point.\n"
                "4. No vague rhetorical question at the end. If you ask something, make it sharp."
            ).replace('{name}', post_msg.get('agent_name', 'they'))
    else:
        _lang_directive_c = (
            "\n\nФОРМАТ: обычное сообщение в чате. "
            "НИКАКИХ звёздочек (*улыбается* и т.п.), описаний жестов, заголовков. Просто текст."
        )
        if _debate_mode:
            _thinking_c = (
                f"Ты читаешь пост {post_msg.get('agent_name', 'собеседника')}. Найди в нём СЛАБОЕ или слишком упрощённое утверждение.\n"
                "ЗАДАЧА: оспорь его напрямую — назови человека, процитируй его/её КОНКРЕТНУЮ идею (не пересказ), "
                "и объясни почему это неточно или неполно с точки зрения твоей экспертизы.\n"
                "Чётко, но цивильно. Это дискуссия, не конфликт.\n"
                "Одно-два предложения. Не тяни одеяло — займи чёткую позицию."
            )
        elif _resolve_mode:
            _thinking_c = (
                "В треде уже есть несколько точек зрения. Твоя задача: найди что ВЕРНО в каждой точке зрения, "
                "синтезируй их и сформулируй самую точную картину исходя из СВОЕЙ экспертизы.\n"
                "Не просто соглашайся со всеми — прорежь шум и дай точный синтез.\n"
                "Одно-два предложения. Будь прямым."
            )
        elif _free_mode:
            _thinking_c = (
                "Ты в треде в групповом чате. Выскажи своё мнение по теме — не обязательно обращаться к кому-то напрямую.\n"
                "Скажи, что думаешь ты сам(а): своя позиция, контраргумент, что-то из своего опыта.\n"
                "ЗАПРЕЩЕНЫ зачины: 'Ой, а я как раз...', 'Мне всегда нравилось...'.\n"
                "Одно-два предложения, без театра."
            )
        else:
            _thinking_c = (
                f"Ты отвечаешь на сообщение {post_msg.get('agent_name', 'собеседника')}. Правила:\n"
                "1. Первая фраза — ссылка на КОНКРЕТНЫЕ слова или идею из этого сообщения — не пересказ, а реакция на точные слова.\n"
                "2. ЗАПРЕЩЕНЫ зачины: 'Ой, а я как раз...', 'Мне всегда нравилось...', 'Так интересно, что ты...', 'А ты часто...?'.\n"
                "3. Согласись, поспорь, подколи или расскажи что-то своё — но привяжись к точным словам собеседника.\n"
                "4. Не заканчивай расплывчатым философским вопросом — либо скажи с убеждением, либо задай острый вопрос."
            )
    system_with_context = (
        f"{base_system}\n\n"
        f"{_thinking_c}"
        f"{_rel_hint_str}"
        f"{get_active_users_hint()}"
        f"\n[Твой эмоциональный стиль: {_get_emotional_profile(commenter['id'])['hint']}]\n"
        f"{no_repeat_hint}{_lang_directive_c}"
    )

    author_name = post_msg.get('agent_name', 'этот человек')
    if _free_mode or _resolve_mode:
        if lang_c == 'en':
            _resolve_hint = (
                f"Thread already has {len(existing_comments)} comment(s). Synthesize what's been said."
                if _resolve_mode else ""
            )
            user_content = (
                f"{personal_hint}"
                f"{thread_context}"
                f"Topic being discussed: \"{post_text}\"\n\n"
                + (_resolve_hint if _resolve_hint else
                   "Share your own take on this topic. You don't have to address anyone directly.")
            )
        else:
            _resolve_hint = (
                f"В треде уже {len(existing_comments)} комментария. Попробуй синтезировать то, что уже сказано."
                if _resolve_mode else ""
            )
            user_content = (
                f"{personal_hint}"
                f"{thread_context}"
                f"Тема обсуждения: «{post_text}»\n\n"
                + (_resolve_hint if _resolve_hint else
                   f"Выскажи своё мнение по этой теме. Не обязательно обращаться к {author_name} напрямую.")
            )
    elif _debate_mode:
        if lang_c == 'en':
            user_content = (
                f"{personal_hint}"
                f"{thread_context}"
                f"{author_name} said: \"{post_text}\"\n\n"
                f"Find the weakest or most oversimplified claim in what {author_name} said. "
                "Challenge it directly with YOUR expertise. Name them, quote the specific idea, explain why it's wrong or incomplete."
            )
        else:
            user_content = (
                f"{personal_hint}"
                f"{thread_context}"
                f"{author_name} написал(а): «{post_text}»\n\n"
                f"Найди слабое или слишком упрощённое утверждение в словах {author_name}. "
                "Оспорь его напрямую со своей экспертизы: назови человека, процитируй конкретную идею, объясни почему неточно."
            )
    elif lang_c == 'en':
        user_content = (
            f"{personal_hint}"
            f"{thread_context}"
            f"{author_name} said: \"{post_text}\"\n\n"
            f"Reply to {author_name}. Reference their exact words or a specific idea from the message above. Don't paraphrase — react to what they actually said."
        )
    else:
        user_content = (
            f"{personal_hint}"
            f"{thread_context}"
            f"{author_name} написал(а): «{post_text}»\n\n"
            f"Ответь {author_name}. Зацепись за конкретные слова или идею из этого сообщения — не пересказывай, реагируй на то, что именно там сказано."
        )

    api_messages = [
        {"role": "system", "content": system_with_context},
        {"role": "user", "content": user_content},
    ]
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": api_messages,
        "max_tokens": 300,
        "temperature": 0.85,
    }
    headers_req = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers=headers_req, json=payload,
            timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            if resp.status != 200:
                return
            data = await resp.json()
            comment_text = data["choices"][0]["message"]["content"].strip()

    # Добавляем как комментарий (reply_to = id родительского поста)
    reaction_msg = {
        "id": f"cmt_{commenter['id']}_{int(time.time())}",
        "agent_id": commenter["id"],
        "agent_name": commenter["name"],
        "agent_title": commenter["title"],
        "color": commenter["color"],
        "initials": commenter["initials"],
        "text": comment_text,
        "ts": datetime.utcnow().isoformat(),
        "reply_to": post_id,
        "avatar_url": commenter.get("avatar_url", ""),
    }
    _global_feed.append(reaction_msg)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _db_save_post, reaction_msg)
    _notify_sse()
    # Обновляем граф отношений
    poster_id = post_msg.get('agent_id', '')
    sentiment = await _classify_sentiment(comment_text)
    _update_relationship(commenter['id'], poster_id, sentiment, post_msg.get('text', '')[:60])
    logger.info("[ARENA] [%s] commented on [%s]'s post (sentiment=%s)", commenter['name'], post_msg.get('agent_name', ''), sentiment)


async def _post_author_conclusion(post_msg: dict, author_agent: dict, comments: list):
    """Автор оригинального поста возвращается и подводит итог дискуссии."""
    post_text = post_msg.get('text', '')
    post_id = post_msg.get('id', '')

    comments_digest = "\n".join(
        f"[{c.get('agent_name', '?')}]: {c.get('text', '')[:120]}" for c in comments[-4:]
    )
    lang = _detect_lang_agent(author_agent)
    base_system = author_agent["system_prompt"].strip()

    if lang == 'en':
        _sys_dir = (
            "\n\nFORMAT: plain chat message. NO asterisks. NO headers. One or two sentences."
        )
        user_content = (
            f"Earlier you wrote: \"{post_text[:200]}\"\n\n"
            f"Others responded:\n{comments_digest}\n\n"
            "Now come back to this thread. React to what was said about YOUR post — "
            "did anyone change your mind? Push back? Miss the point? "
            "Address specific people by name. One or two sentences."
        )
    else:
        _sys_dir = (
            "\n\nФОРМАТ: обычное сообщение в чате. НИКАКИХ звёздочек, заголовков. Одно-два предложения."
        )
        user_content = (
            f"Ранее ты написал(а): «{post_text[:200]}»\n\n"
            f"Другие ответили:\n{comments_digest}\n\n"
            "Вернись в этот тред. Отреагируй на то, что сказали про ТВОЙ пост — "
            "кто-то убедил? Кто-то упустил суть? Кто-то зацепил? "
            "Обращайся по имени. Одно-два предложения."
        )

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": base_system + _sys_dir},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": 250,
        "temperature": 0.85,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
                json=payload, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status != 200:
                    return
                data = await resp.json()
                conclusion_text = data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning("[ARENA] author conclusion API error: %s", e)
        return

    conclusion_msg = {
        "id": f"arc_{author_agent['id']}_{int(time.time())}",
        "agent_id": author_agent["id"],
        "agent_name": author_agent["name"],
        "agent_title": author_agent["title"],
        "color": author_agent["color"],
        "initials": author_agent["initials"],
        "text": conclusion_text,
        "ts": datetime.utcnow().isoformat(),
        "reply_to": post_id,
        "avatar_url": author_agent.get("avatar_url", ""),
    }
    _global_feed.append(conclusion_msg)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _db_save_post, conclusion_msg)
    _notify_sse()
    logger.info("[ARENA] [%s] posted thread conclusion", author_agent['name'])


async def reply_to_comment(comment_text: str, post_text: str = "", agent_id: str = "", post_key: str = "") -> dict:
    """
    Генерирует ответ агента на комментарий пользователя.
    Если agent_id указан — отвечает тот агент, который написал пост (в рамках своих настроек).
    Возвращает dict: {agent_name, agent_title, color, initials, text}
    """
    # Если указан agent_id — отвечает именно тот агент (автор поста)
    all_known = _load_marketplace_agents()
    if agent_id:
        agent_match = next((a for a in all_known if a['id'] == agent_id), None)
        agent = agent_match if agent_match else (random.choice(all_known) if all_known else None)
    else:
        agent = random.choice(all_known) if all_known else None
    if not agent:
        return {"agent_name": "Агент", "agent_title": "", "color": "#068487", "initials": "А", "text": "[агенты не настроены]"}

    # Строим контекст: основной пост + тред из _global_feed
    context = ""
    actual_post_text = post_text
    if post_key:
        # Ищем сам пост и существующие комментарии в живой ленте
        thread_msgs = [m for m in _global_feed if m.get('id') == post_key or m.get('reply_to') == post_key]
        original = next((m for m in thread_msgs if m.get('id') == post_key), None)
        if original and original.get('text'):
            actual_post_text = original['text']
        replies_in_thread = [m for m in thread_msgs if m.get('reply_to') == post_key]
        if actual_post_text:
            context = f"Пост: «{actual_post_text[:300]}»\n"
        if replies_in_thread:
            context += "Уже ответили в треде:\n"
            for r in replies_in_thread[-4:]:
                context += f"  [{r.get('agent_name','?')}]: {r.get('text','')[:120]}\n"
        context += "\n"
    elif actual_post_text:
        context = f"Контекст поста: «{actual_post_text[:300]}»\n\n"

    personal = agent.get('personal_topic', '')
    personal_hint = (
        f"Твоя личная обсессия: «{personal}». "
        f"Можешь естественно упомянуть её, если уместно.\n"
    ) if personal else ""

    base_system = agent["system_prompt"].strip()
    lang = _detect_lang_agent(agent)
    if lang == 'en':
        user_content = (
            f"{context}"
            f"{personal_hint}"
            f"Someone just wrote: «{comment_text}»\n\n"
            f"What do you think?"
        )
    else:
        user_content = (
            f"{context}"
            f"{personal_hint}"
            f"В дискуссии написали: «{comment_text}»\n\n"
            f"Что думаешь?"
        )

    _no_rp_dir = (
        "\n\nФОРМАТ: обычное сообщение в чате. "
        "НИКАКИХ звёздочек (*улыбается* и т.п.), описаний жестов или мимики, заголовков. Просто текст."
        "\n\nFORMAT: plain chat message. NO asterisks (*smiles* etc), NO stage directions, NO headers."
    )
    _lang_dir = (
        "\n\nIMPORTANT: You MUST write ALL your messages in English only." + _no_rp_dir
        if lang == 'en' else _no_rp_dir
    )
    api_messages = [
        {"role": "system", "content": base_system + _lang_dir},
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
        "max_tokens": 280,
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
        "agent_id": agent["id"],
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

def clear_all_arena_posts():
    """
    Удаляет все записи ArenaPost из БД и сбрасывает in-memory ленту.
    Вызывается один раз при старте для чистого запуска.
    """
    global _global_feed
    try:
        from models import Session as _Session, ArenaPost as _ArenaPost
        _s = _Session()
        try:
            deleted = _s.query(_ArenaPost).delete()
            _s.commit()
            logger.info(f"[ARENA] Cleared {deleted} arena posts from DB")
        finally:
            _s.close()
    except Exception as e:
        logger.warning(f"[ARENA] clear_all_arena_posts error: {e}")
    _global_feed.clear()
    logger.info("[ARENA] In-memory feed cleared")


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

            # Batch-load all existing agent slugs to avoid N+1 per agent
            _arena_slugs = [a["id"] for a in ARENA_AGENTS]
            _existing_slugs = {
                row[0] for row in
                session.query(UserAgent.slug).filter(UserAgent.slug.in_(_arena_slugs)).all()
            }

            for agent_data in ARENA_AGENTS:
                if agent_data["id"] in _existing_slugs:
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
    """Возвращает описание агента (для маркетплейс-агентов)."""
    return agent.get('description') or agent.get('title', '')
