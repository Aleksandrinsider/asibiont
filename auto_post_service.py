#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Automatic post generation service - creates daily progress posts and birthday posts
"""

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
import pytz
import random
import aiohttp

from models import Session, User, UserProfile, Task, Post
from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL, REPLICATE_API_TOKEN
from ai_integration.memory import decrypt_data


@asynccontextmanager
async def _safe_http(**kwargs):
    """One-off aiohttp session with proper SSL transport cleanup."""
    session = aiohttp.ClientSession(**kwargs)
    try:
        yield session
    finally:
        await session.close()
        await asyncio.sleep(0)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_VISUAL_PROMPT_MARKER_RE = re.compile(
    r'(?im)\s*["\'«»„“”\(\[]?\s*(иллюстрация|изображение|illustration|image)\s*[:\-—]\s*'
)


def _strip_post_visual_prompt(text: str) -> str:
    if not text:
        return text

    cleaned = str(text)
    markers = list(_VISUAL_PROMPT_MARKER_RE.finditer(cleaned))
    if markers:
        marker = markers[-1]
        tail = cleaned[marker.end():].strip().strip('"\'«»')
        if 0 < len(tail) <= 500:
            cleaned = cleaned[:marker.start()].rstrip()
    return re.sub(r'\n{3,}', '\n\n', cleaned).strip()


def _detect_gender(first_name: str) -> str:
    """Определяет пол по имени: 'female' или 'male'.
    Русские женские имена как правило заканчиваются на 'а' или 'я'.
    Для английских — список распространённых имён + окончание на 'a'.
    """
    if not first_name:
        return 'male'
    name = first_name.strip().split()[0].lower()
    # Явный список английских женских имён (не оканчивающихся на 'a')
    _EN_FEMALE = {
        'kate', 'katie', 'jane', 'mary', 'helen', 'sarah', 'emily', 'claire',
        'eve', 'grace', 'hope', 'joy', 'faith', 'ruth', 'beth', 'sue',
        'rachel', 'hannah', 'naomi', 'abigail', 'charlotte', 'elizabeth',
        'jennifer', 'jessica', 'ashley', 'brittany', 'megan', 'stephanie',
        'caroline', 'katherine', 'natalie', 'danielle', 'michelle',
        'samantha', 'amber', 'crystal', 'heather', 'holly', 'ivy', 'lily',
        'rose', 'violet', 'daisy', 'iris', 'pearl', 'ruby', 'elise',
        'erin', 'gwen', 'karen', 'kim', 'lauren', 'leigh', 'lynn', 'meredith',
        'morgan', 'paige', 'quinn', 'robin', 'shelby', 'taylor', 'whitney',
    }
    if name in _EN_FEMALE:
        return 'female'
    # Русские и большинство европейских женских имён на 'а'/'я'
    if name.endswith(('а', 'я', 'a')):
        return 'female'
    return 'male'


async def _generate_text_with_ai(
    prompt: str,
    system_prompt: str | None = None,
    max_tokens: int = 400,
) -> str:
    """Прямой вызов AI API для генерации текста (без агентского пайплайна).
    
    Используется для генерации постов и другого контента,
    где НЕ нужен полный цикл plan→execute→reflect.
    """
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt or "Ты — ghostwriter. Пиши естественный текст по инструкциям пользователя. Выдавай только готовый текст без пояснений."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.92,
        "max_tokens": max_tokens
    }

    async with _safe_http() as session:
        async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=60, sock_read=50)) as response:
            if response.status == 200:
                result = await response.json()
                return result['choices'][0]['message']['content'].strip()
            else:
                error_text = await response.text()
                raise Exception(f"AI API error: {response.status} {error_text[:200]}")


async def _generate_image_for_post(post_text: str, style: str = "", user_rules: list | None = None) -> str:
    """Generate an illustration for a post via Replicate Flux.
    Returns image URL or empty string on failure.
    """
    if not REPLICATE_API_TOKEN:
        return ""

    # Старый базовый подход: AI пишет только сцену, стиль добавляем отдельно в конце.
    _priority_style = (style or '').strip()
    _rules_hint = "; ".join([str(r).strip() for r in (user_rules or []) if str(r).strip()][:3])
    _style_instruction = f" The image MUST be rendered in this style: {_priority_style}." if _priority_style else ""
    if _rules_hint:
        _style_instruction += f" User visual preferences to honor: {_rules_hint}."

    try:
        image_prompt = await _generate_text_with_ai(
            f"""You are a visual prompt engineer. Based on this social media post, write one short English image-generation prompt (max 35 words, NO style words). """
            f"""Describe only concrete visual elements: objects, place, action, mood. Keep the topic faithful. """
            f"""Do not substitute the main subject with decorative nature motifs unless explicitly mentioned in the post. """
            f"""No text overlays, no meta-phrases like 'concept of', no style descriptors (style will be appended separately).{_style_instruction}"""
            f"""\n\nPost text: {post_text[:300]}\n\nWrite ONLY the scene description (no style words):"""
        )
        if not image_prompt or len(image_prompt) < 5:
            image_prompt = "person working at a desk, focused, modern office"
            if _priority_style:
                image_prompt += f", {_priority_style}"
    except Exception as _img_prompt_err:
        logger.debug("Image prompt generation failed, using fallback: %s", _img_prompt_err)
        image_prompt = "person working at a desk, focused, modern office"
        if _priority_style:
            image_prompt += f", {_priority_style}"

    # Гарантированно добавляем стиль в финальный промпт для Replicate
    if _priority_style and _priority_style.lower() not in image_prompt.lower():
        image_prompt = f"{image_prompt.rstrip(', ')} | style: {_priority_style}"

    try:
        model = "black-forest-labs/flux-schnell"
        headers = {
            "Authorization": f"Bearer {REPLICATE_API_TOKEN}",
            "Content-Type": "application/json",
            "Prefer": "wait",
        }
        input_data = {
            "prompt": image_prompt,
            "aspect_ratio": "1:1",
            "width": 550,
            "height": 550,
            "output_format": "png",
            "output_quality": 80,
        }
        async with _safe_http() as http:
            resp = await http.post(
                f"https://api.replicate.com/v1/models/{model}/predictions",
                headers=headers,
                json={"input": input_data},
                timeout=aiohttp.ClientTimeout(total=90),
            )
            data = await resp.json()
            if resp.status not in (200, 201):
                logger.warning(f"[AUTO_POST_IMG] Replicate error: {data.get('detail', data)}")
                return ""

            output = data.get("output")
            prediction_id = data.get("id")

            if output is None and prediction_id:
                for _ in range(30):
                    await asyncio.sleep(3)
                    poll = await http.get(
                        f"https://api.replicate.com/v1/predictions/{prediction_id}",
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=15),
                    )
                    poll_data = await poll.json()
                    status = poll_data.get("status")
                    if status == "succeeded":
                        output = poll_data.get("output")
                        break
                    elif status in ("failed", "canceled"):
                        logger.warning(f"[AUTO_POST_IMG] Generation failed: {poll_data.get('error')}")
                        return ""

            if not output:
                return ""

            image_url = output[0] if isinstance(output, list) else output
            logger.info(f"[AUTO_POST_IMG] Generated image: {image_url}")
            return image_url
    except Exception as e:
        logger.warning(f"[AUTO_POST_IMG] Image generation error: {e}")
        return ""


async def _publish_post_to_channel(user_id: int, content: str, image_url: str, session) -> bool:
    """Publish post to user's Telegram channel using marketing_agent.publish_to_telegram."""
    try:
        from ai_integration.marketing_agent import publish_to_telegram
        result = await publish_to_telegram(
            content=content,
            image_url=image_url or None,
            user_id=user_id,
            session=session,
        )
        if isinstance(result, dict):
            return result.get("success", False)
        return False
    except Exception as e:
        logger.warning(f"[AUTO_POST_CHANNEL] Publish error: {e}")
        return False


async def generate_progress_post(user_id, session):
    """Generate daily progress digest post using AI based on completed tasks."""
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return None

        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if not profile:
            return None

        # Timezone — prefer profile.timezone, fallback to user.timezone
        _tz = profile.timezone or user.timezone or 'Europe/Moscow'
        try:
            user_tz = pytz.timezone(_tz)
        except Exception:
            user_tz = pytz.timezone('Europe/Moscow')
        now_utc = datetime.now(pytz.UTC)
        user_now = now_utc.astimezone(user_tz)
        today_start = user_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.UTC)

        hour = user_now.hour
        if hour < 12:
            time_of_day = "утро"
        elif hour < 17:
            time_of_day = "день"
        elif hour < 21:
            time_of_day = "вечер"
        else:
            time_of_day = "ночь"

        # Tasks completed TODAY (by actual_completion_time OR status change today)
        completed_today = session.query(Task).filter(
            Task.user_id == user.id,
            Task.status.in_(['completed', 'done']),
            Task.actual_completion_time >= today_start
        ).all()
        # Fallback: tasks created today with completed status (no actual_completion_time)
        if not completed_today:
            completed_today = session.query(Task).filter(
                Task.user_id == user.id,
                Task.status.in_(['completed', 'done']),
                Task.created_at >= today_start
            ).all()

        # ── КЛЮЧЕВОЕ ИЗМЕНЕНИЕ: если нет выполненных задач — не тратим токены ──
        if not completed_today:
            logger.info(f"[AUTO_POST] skip user {user_id}: no completed tasks today, no post needed")
            return None

        # Проверяем и списываем токены ТОЛЬКО если есть что писать
        from config import FREE_ACCESS_MODE
        from token_service import has_enough_tokens, spend_tokens
        if not FREE_ACCESS_MODE:
            if not has_enough_tokens(user_id, 'auto_post'):
                logger.info(f"[AUTO_POST] skip user {user_id}: insufficient tokens")
                return None
            spend_tokens(user_id, 'auto_post', description='Автопост дня')

        # Active tasks (created any time, still open)
        active_tasks = session.query(Task).filter(
            Task.user_id == user.id,
            Task.status.in_(['pending', 'in_progress'])
        ).order_by(Task.due_date.asc()).limit(5).all()

        overdue_tasks = session.query(Task).filter(
            Task.user_id == user.id,
            Task.status == 'overdue'
        ).limit(3).all()

        # Week stats for richer context
        week_ago = today_start - timedelta(days=7)
        week_completed_count = session.query(Task).filter(
            Task.user_id == user.id,
            Task.status.in_(['completed', 'done']),
            Task.actual_completion_time >= week_ago
        ).count()

        # Last 5 posts — full text for strong anti-repetition
        recent_posts = session.query(Post).filter(
            Post.user_id == user.id
        ).order_by(Post.created_at.desc()).limit(5).all()
        recent_texts = [p.content for p in recent_posts if p.content]
        _recent_avg_len = int(sum(len(t) for t in recent_texts) / len(recent_texts)) if recent_texts else 0

        # Profile info
        user_name = user.first_name if user.first_name else (user.username or 'Пользователь')
        user_gender = (profile.gender if profile and profile.gender in ('male', 'female')
                       else _detect_gender(user.first_name or ''))
        user_city = profile.city or ''
        user_goals = profile.goals or ''

        def _extract_post_preferences_from_memory(_user_obj) -> dict:
            """Read user post-format preferences from memory/rules (soft learning)."""
            import json as _json_pref
            _rules: list[str] = []
            try:
                _mem_raw = decrypt_data(getattr(_user_obj, 'memory', None) or '') if getattr(_user_obj, 'memory', None) else '{}'
                if _mem_raw and _mem_raw.strip().startswith('{'):
                    _m = _json_pref.loads(_mem_raw)
                    _rules.extend([r for r in _m.get('rules', []) if isinstance(r, str) and r.strip()])
            except Exception:
                pass
            try:
                _ltm_raw = decrypt_data(getattr(_user_obj, 'long_term_memory', None) or '') if getattr(_user_obj, 'long_term_memory', None) else '{}'
                if _ltm_raw and _ltm_raw.strip().startswith('{'):
                    _ltm = _json_pref.loads(_ltm_raw)
                    _rules.extend([r for r in _ltm.get('rules', []) if isinstance(r, str) and r.strip()])
                    _sl = _ltm.get('self_learning', {}) if isinstance(_ltm, dict) else {}
                    _prefs = _sl.get('preferences', {}) if isinstance(_sl, dict) else {}
                    _pf = _prefs.get('post_format') if isinstance(_prefs, dict) else None
                    if isinstance(_pf, str) and _pf.strip():
                        _rules.append(_pf.strip())
            except Exception:
                pass

            _blob = ' | '.join(r.lower() for r in _rules)
            _long_pref = any(k in _blob for k in ('длинн', 'развернут', 'подробн', 'seo', 'статья', 'long form', 'long-form', 'длинный пост'))
            _short_pref = any(k in _blob for k in ('коротк', 'кратк', '1-2 предлож', '2-4 предлож', 'short post', 'brief'))
            _format = 'long' if _long_pref and not _short_pref else ('short' if _short_pref and not _long_pref else 'normal')
            return {
                'format': _format,
                'rules_preview': _rules[:5],
            }

        _post_prefs = _extract_post_preferences_from_memory(user)
        # Soft self-learning from recent writing behavior if no explicit rule exists.
        if _post_prefs.get('format') == 'normal' and _recent_avg_len > 700:
            _post_prefs['format'] = 'long'
        elif _post_prefs.get('format') == 'normal' and 0 < _recent_avg_len < 260:
            _post_prefs['format'] = 'short'

        # --- НОВЫЕ: темы для дайджеста задач, а не личного блога ---
        topic_angles = [
            "итоги дня по закрытым задачам — что успел и какой результат",
            "самая сложная задача, которую закрыл сегодня — как решил",
            "неожиданный результат или инсайт из выполненной задачи",
            "какая задача принесла больше всего пользы сегодня",
            "что планировал сделать и что из этого вышло на самом деле",
            "связь между выполненными задачами — общий прогресс за день",
            "что узнал нового в процессе выполнения задач",
            "как продвинулся к большой цели через мелкие задачи",
        ]
        topic_hint = random.choice(topic_angles)

        # Tone for digest — focused on achievements
        tone = random.choice([
            "по-деловому, с цифрами и фактами",
            "спокойно, с чувством выполненного долга",
            "энергично, с фокусом на результат",
            "без прикрас, честно — что получилось, а что нет",
            "с лёгкой гордостью за сделанное",
            "коротко и по существу",
        ])

        # Build prompt
        city_part = f", {user_city}" if user_city else ""

        context = f"""Ты пишешь ежедневный дайджест выполненных задач от первого лица. Стиль: {tone}.

О человеке:
- Имя: {user_name}{city_part}
- Сейчас: {time_of_day}
- Фокус: {topic_hint}
"""

        # Completed tasks — ОБЯЗАТЕЛЬНЫ (мы уже проверили что они есть)
        context += f"\nВыполнено сегодня ({len(completed_today)} шт.):\n"
        for t in completed_today[:7]:
            desc = decrypt_data(t.description) if t.description else t.title
            notes = f" — {t.completion_notes[:80]}" if t.completion_notes else ""
            context += f"- «{desc}»{notes}\n"
        if len(completed_today) > 7:
            context += f"  ...и ещё {len(completed_today) - 7} задач\n"

        if active_tasks:
            context += f"\nВ работе:\n"
            for t in active_tasks[:3]:
                desc = decrypt_data(t.description) if t.description else t.title
                context += f"- {desc}\n"

        if overdue_tasks:
            context += f"\nПросрочено:\n"
            for t in overdue_tasks[:2]:
                desc = decrypt_data(t.description) if t.description else t.title
                context += f"- {desc}\n"

        if week_completed_count > 0:
            context += f"\nЗа неделю закрыто задач: {week_completed_count}\n"
        if user_goals:
            context += f"\nЦели: {user_goals[:200]}\n"

        if recent_texts:
            context += f"\nПредыдущие дайджесты (чтобы не повторяться):\n"
            for i, s in enumerate(recent_texts, 1):
                context += f"---\nПост {i}: {s[:150]}\n"
            context += "---\n"

        if _post_prefs['rules_preview']:
            context += "\nПравила пользователя по стилю/формату:\n"
            for _r in _post_prefs['rules_preview']:
                context += f"- {_r}\n"

        _format_rule = "— 2-4 предложения, максимум 400 символов"
        if _post_prefs['format'] == 'long':
            _format_rule = "— развернутый формат 5-8 предложений (ориентир: 700-1200 символов)"
        elif _post_prefs['format'] == 'normal':
            _format_rule = "— 3-5 предложений, содержательно"

        context += f"""
Напиши дайджест задач за сегодня. СТРОГИЕ правила:
{_format_rule}
— живой разговорный текст, как заметка в ежедневнике
— {user_gender == 'female' and 'женский род' or 'мужской род'}
— ОБЯЗАТЕЛЬНО упомяни хотя бы 2-3 выполненные задачи из списка выше
— если есть просроченные задачи — отметь это честно
— НЕ используй буллеты, нумерацию, хештеги, CTA, заголовки
— НЕ начинай со слов «Сегодня», «Только что», «За день»
— НЕ заканчивай вопросом к аудитории
— НЕ пиши про погоду, настроение или личные переживания — ТОЛЬКО про задачи и прогресс

Только текст дайджеста:"""

        # Call AI
        try:
            _sys = "Ты — ассистент для ежедневного дайджеста. Пиши кратко, по делу, с фокусом на выполненные задачи."
            _max_tokens = 700 if _post_prefs['format'] == 'long' else (450 if _post_prefs['format'] == 'normal' else 350)
            response = await _generate_text_with_ai(context, system_prompt=_sys, max_tokens=_max_tokens)

            if response and len(response) > 20:
                post_content = response.strip().strip('"').strip("'")
                logger.info(
                    f"[AUTO POST] Generated digest for {user_id} ({len(completed_today)} tasks, len={len(post_content)}): {post_content[:120]}"
                )
                return post_content
            else:
                logger.warning(f"AI response too short for {user_id}, using fallback")
                return generate_simple_fallback(completed_today, active_tasks, overdue_tasks, user.first_name or '')

        except Exception as ai_error:
            logger.error(f"AI generation failed for {user_id}: {ai_error}")
            return generate_simple_fallback(completed_today, active_tasks, overdue_tasks, user.first_name or '')

    except Exception as e:
        logger.error(f"Error generating progress post for user {user_id}: {e}")
        return None


def generate_simple_fallback(completed_tasks, pending_tasks, overdue_tasks, first_name=''):
    """Generate simple fallback message when AI fails"""
    is_female = _detect_gender(first_name) == 'female'
    if completed_tasks:
        if len(completed_tasks) >= 3:
            verb = 'закрыла' if is_female else 'закрыл'
            return f"Сегодня {verb} {len(completed_tasks)} задач — день прошёл не зря"
        else:
            raw_desc = decrypt_data(completed_tasks[0].description) if completed_tasks[0].description else (completed_tasks[0].title or '')
            task_desc = (raw_desc[:50] + "...") if raw_desc and len(raw_desc) > 50 else (raw_desc or 'задачу')
            verb = 'Разобралась' if is_female else 'Разобрался'
            return f"{verb} с '{task_desc}' — ещё одно дело с плеч"
    elif pending_tasks:
        return f"В работе {len(pending_tasks)} задач — копаю дальше"
    else:
        return "Раскидываю планы на завтра"


async def generate_research_post(user_id, query, analysis, session):
    """
    Generate post from research results
    
    Args:
        user_id: Telegram user ID
        query: Research query/topic
        analysis: Dict with research results (key_insights, opportunities, trends, etc.)
        session: DB session
    
    Returns:
        str: Natural post about research findings or None
    """
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return None
        
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if not profile:
            return None
        
        # Extract key data
        insights = analysis.get('key_insights', [])
        opportunities = analysis.get('opportunities', [])
        trends = analysis.get('trends', [])
        summary = analysis.get('summary', '')
        
        # Build AI prompt
        user_name = user.first_name if user.first_name else (user.username or 'Пользователь')
        user_gender = (profile.gender if profile and profile.gender in ('male', 'female')
                       else _detect_gender(user.first_name or ''))
        _gender_note = (
            'Автор — женщина, используй женский род: «изучила», «обнаружила», «нашла», «решила».'
            if user_gender == 'female' else
            'Автор — мужчина, используй мужской род: «изучил», «обнаружил», «нашёл», «решил».'
        )
        
        context = f"""Создай живой, естественный пост от лица пользователя {user_name}, который только что провел исследование рынка/темы.
{_gender_note}

Тема исследования: {query}

Результаты исследования:
"""
        
        if summary:
            context += f"\nОбщее резюме: {summary}\n"
        
        if insights:
            context += f"\nКлючевые инсайты:\n"
            for insight in insights[:3]:
                context += f"- {insight}\n"
        
        if opportunities:
            context += f"\nВозможности:\n"
            for opp in opportunities[:2]:
                context += f"- {opp}\n"
        
        if trends:
            context += f"\nТренды:\n"
            for trend in trends[:2]:
                context += f"- {trend}\n"
        
        context += """
Требования к посту:
1. Пиши от первого лица с правильным родом глаголов ({_gender_note})
2. Реши сам что интересно в этих данных — не пересказывай всё, выбери одну мысль
3. Покажи своё отношение и позицию, а не нейтральный пересказ
4. 2-4 предложения, сплошной текст — БЕЗ буллетов, БЕЗ эмодзи, БЕЗ «Выводы:»
5. Не упоминай ASI Biont и не делай рекламных вставок
6. Стиль — как будто делишься мыслью с друзьями в чате

Создай пост:"""
        
        # Прямой вызов AI API (без агентского пайплайна, чтобы не было рекурсии)
        try:
            post_content = await _generate_text_with_ai(context)
        except Exception as ai_err:
            logger.error(f"[RESEARCH POST] AI generation failed: {ai_err}")
            return None
        
        if post_content and len(post_content) > 20:
            # Remove quotes if AI wrapped it
            post_content = post_content.strip().strip('"').strip("'")
            logger.info(f"[RESEARCH POST] Generated post for {user_id}: {post_content[:100]}...")
            return post_content
        else:
            logger.warning(f"[RESEARCH POST] AI returned empty/short response for {user_id}")
            return None
            
    except Exception as e:
        logger.error(f"[RESEARCH POST] Error generating post for {user_id}: {e}")
        return None


async def create_auto_post(user_id, content, session, notify=True, post_type='progress'):
    """
    Create a post in the database and optionally notify user
    
    Args:
        user_id: Telegram user ID
        content: Post content
        session: DB session
        notify: Whether to send Telegram notification
        post_type: 'progress' for daily updates or 'research' for market research
    """
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return False
        
        # Generate content if not provided
        if content is None:
            content = await generate_progress_post(user_id, session)
            if content is None:
                logger.error(f"Failed to generate content for auto-post for user {user_id}")
                return False

        content = _strip_post_visual_prompt(content)
        if not content:
            logger.warning(f"[AUTO_POST] Skipped empty post after prompt-tail cleanup for user {user_id}")
            return False
        
        post = Post(
            user_id=user.id,
            username=user.username or user.first_name or f"user_{user.telegram_id}",
            content=content,
            post_type=post_type if post_type in ('progress', 'manual', 'campaign') else 'progress',
            created_at=datetime.now(pytz.UTC)
        )
        
        session.add(post)
        session.commit()

        # Log agent activity
        try:
            from models import AgentActivityLog
            log_entry = AgentActivityLog(
                user_id=user.id,
                activity_type='post_newsfeed',
                title=content,
                content=content,
                target='Лента новостей',
                status='published',
                ref_id=post.id,
            )
            session.add(log_entry)
            session.commit()
        except Exception as log_err:
            logger.warning(f"[AUTO_POST] Failed to log activity: {log_err}")
            try:
                session.rollback()
            except Exception:
                pass
        
        logger.info(f"Auto-post created for user {user_id}")

        # === Генерация картинки для поста (отображается в дашборде и каналах) ===
        # Передаём ВСЕ правила пользователя в AI — он сам разберётся что применить к картинке
        _user_rules_for_img: list = []
        _priority_img_style = ''
        try:
            _raw_mem_aps = getattr(user, 'memory', None) or ''
            # Берём главный стиль тем же проверенным способом, что и основной generate_image
            try:
                from ai_integration.handlers import _extract_image_style_from_memory as _extract_style_aps
                _priority_img_style = (_extract_style_aps(user) or '').strip()
            except Exception as _st_err:
                logger.debug("[AUTO_POST] Could not extract priority image style: %s", _st_err)

            if _raw_mem_aps:
                try:
                    from ai_integration.memory import decrypt_data as _dec_aps
                    _raw_mem_aps = _dec_aps(_raw_mem_aps)
                except Exception:
                    pass
                if _raw_mem_aps and _raw_mem_aps.strip().startswith('{'):
                    import json as _json_aps
                    _m_aps = _json_aps.loads(_raw_mem_aps.strip())
                    _user_rules_for_img = [r for r in _m_aps.get('rules', []) if r and len(r.strip()) > 3]
            logger.info(
                "[AUTO_POST] user rules for image: %d rules, priority_style=%s",
                len(_user_rules_for_img),
                'yes' if _priority_img_style else 'no',
            )
        except Exception as _e_aps:
            logger.debug("[AUTO_POST] Could not load user rules for image: %s", _e_aps)

        # Проверяем правила пользователя на запрет картинок ПЕРЕД генерацией
        _NO_IMAGE_KW = (
            'без картинк', 'без изображен', 'без иллюстрац', 'не генерируй картинк',
            'не генерируй изображен', 'не добавляй картинк', 'не добавляй изображен',
            'убери картинк', 'убери изображен', 'no image', 'without image',
            'без фото', 'не нужна картинка', 'не нужно изображение',
        )
        _skip_image_by_rule = any(
            any(kw in r.lower() for kw in _NO_IMAGE_KW)
            for r in _user_rules_for_img
        )
        if _skip_image_by_rule:
            logger.info("[AUTO_POST] Image generation skipped by user rule")

        image_url = ""
        try:
            if not _skip_image_by_rule:
                image_url = await _generate_image_for_post(
                    content,
                    style=_priority_img_style,
                    user_rules=_user_rules_for_img,
                )
            if image_url:
                post.image_url = image_url
                # Скачиваем байты для постоянного хранения
                try:
                    async with _safe_http() as _dl_sess:
                        async with _dl_sess.get(
                            image_url,
                            timeout=aiohttp.ClientTimeout(total=30),
                        ) as _dl_resp:
                            if _dl_resp.status == 200:
                                post.image_data = await _dl_resp.read()
                                post.image_mime = _dl_resp.content_type or 'image/webp'
                                logger.info(f"[AUTO_POST] Image bytes stored: {len(post.image_data)} bytes")
                            else:
                                logger.warning(f"[AUTO_POST] Image download HTTP {_dl_resp.status}")
                except Exception as _dl_err:
                    logger.warning(f"[AUTO_POST] Image bytes download failed: {_dl_err}")
                session.commit()
                logger.info(f"[AUTO_POST] Image saved to post {post.id}")
        except Exception as img_err:
            logger.warning(f"[AUTO_POST] Image generation failed for {user_id}: {img_err}")

        # Ежедневные отчёты НЕ создают blog Note — они идут только в ленту новостей (Post).
        # Публичный блог на сайте /blog/ предназначен для ручных/осознанных публикаций.

        # === Публикация в Telegram канал (если настроен) ===
        channel_published = False
        if user.telegram_channel:
            try:
                channel_published = await _publish_post_to_channel(user_id, content, image_url, session)
                if channel_published:
                    logger.info(f"[AUTO_POST] Published to channel {user.telegram_channel} for user {user_id}, image={'yes' if image_url else 'no'}")
                    try:
                        from models import AgentActivityLog
                        tg_log = AgentActivityLog(
                            user_id=user.id,
                            activity_type='post_telegram',
                            title=content,
                            content=content,
                            target=user.telegram_channel or 'Telegram-канал',
                            status='published',
                            ref_id=post.id,
                        )
                        session.add(tg_log)
                        session.commit()
                    except Exception as _tl:
                        logger.warning(f"[AUTO_POST] Failed to log telegram channel activity: {_tl}")
                        try:
                            session.rollback()
                        except Exception:
                            pass
            except Exception as ch_err:
                logger.warning(f"[AUTO_POST] Channel publish failed for {user_id}: {ch_err}")

        # === Отправка в Discord webhook (если настроен) ===
        try:
            if user.discord_webhook and user.discord_webhook.startswith('https://discord.com/api/webhooks/'):
                async with _safe_http() as dc_session:
                    dc_payload = {"content": content}
                    if image_url:
                        dc_payload["embeds"] = [{"image": {"url": image_url}}]
                    async with dc_session.post(
                        user.discord_webhook,
                        json=dc_payload,
                        timeout=aiohttp.ClientTimeout(total=10)
                    ) as dc_resp:
                        if dc_resp.status in (200, 204):
                            logger.info(f"[DISCORD] Post sent to webhook for user {user_id}")
                            # Логируем в агентскую активность
                            try:
                                from models import AgentActivityLog
                                dc_log = AgentActivityLog(
                                    user_id=user.id,
                                    activity_type='post_discord',
                                    title=content,
                                    content=content,
                                    target='Discord канал',
                                    status='published',
                                    ref_id=post.id,
                                )
                                session.add(dc_log)
                                session.commit()
                            except Exception as _le:
                                logger.warning(f"[DISCORD] Failed to log activity: {_le}")
                                try:
                                    session.rollback()
                                except Exception:
                                    pass
                        else:
                            err_text = await dc_resp.text()
                            logger.warning(f"[DISCORD] Webhook returned {dc_resp.status} for user {user_id}: {err_text[:200]}")
        except Exception as dc_err:
            logger.warning(f"[DISCORD] Failed to send post for user {user_id}: {dc_err}")

        # Notify user about created post — через AI агент для единого стиля
        if notify:
            try:
                from ai_integration.autonomous_agent import get_autonomous_agent
                agent = get_autonomous_agent()

                # Формируем контекст уведомления в зависимости от того, что произошло
                if channel_published and image_url:
                    instruction = (
                        f"Только что автоматически опубликован пост в Telegram-канал пользователя с иллюстрацией (сгенерировано через Replicate Flux). "
                        f"Текст поста: «{content[:200]}{'...' if len(content) > 200 else ''}». "
                        f"Пост также добавлен в ленту новостей на сайте https://asibiont.com/dashboard. "
                        f"Сообщи об этом живо и естественно. Затем ОБЯЗАТЕЛЬНО задай уточняющий вопрос: "
                        f"доволен ли пользователь картинкой и текстом, или хочет что-то изменить — "
                        f"перегенерировать изображение, скорректировать текст поста, или удалить публикацию."
                    )
                elif channel_published:
                    instruction = (
                        f"Только что автоматически опубликован пост в Telegram-канал пользователя (без картинки — Replicate не настроен или недоступен). "
                        f"Текст поста: «{content[:200]}{'...' if len(content) > 200 else ''}». "
                        f"Сообщи об этом. Задай уточняющий вопрос: всё ли устраивает или хочет "
                        f"добавить картинку вручную, отредактировать текст, или удалить пост."
                    )
                else:
                    instruction = (
                        f"Только что опубликован пост в ленту новостей на сайте. "
                        f"Содержание поста: «{content[:150]}{'...' if len(content) > 150 else ''}». "
                        f"Скажи об этом коротко и естественно, дай ссылку https://asibiont.com/dashboard. "
                        f"Задай уточняющий вопрос — всё ли устраивает или хочет отредактировать/удалить."
                    )

                notification = await agent.generate_system_message(
                    user_id=user_id,
                    mode='result_check',
                    instruction=instruction,
                    max_tokens=400,
                    max_iterations=1
                )
                
                if notification and notification.strip():
                    from config import TELEGRAM_TOKEN
                    import aiohttp
                    if TELEGRAM_TOKEN:
                        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
                        data = {"chat_id": user_id, "text": notification}
                        async with _safe_http() as aio_session:
                            async with aio_session.post(url, json=data, timeout=aiohttp.ClientTimeout(total=15)) as response:
                                if response.status == 200:
                                    logger.info(f"AI notification sent to user {user_id} about auto-post")
                                else:
                                    logger.warning(f"Failed to send notification to {user_id}: {response.status}")
            except Exception as notify_error:
                logger.warning(f"Could not send notification to user {user_id}: {notify_error}")
        
        return True
        
    except Exception as e:
        logger.error(f"Error creating auto-post for user {user_id}: {e}")
        session.rollback()
        return False


async def generate_agent_comment(user_id: int, post_text: str, context: str = '') -> str:
    """Генерирует и логирует комментарий к посту от имени агента пользователя.

    Вызывается автоматически когда агент выполняет действие 'comment_on_post'.
    Сохраняет комментарий в AgentActivityLog с activity_type='comment'.
    Возвращает текст сгенерированного комментария.
    """
    try:
        prompt = (
            f"Ты — ИИ-агент пользователя. Напиши короткий живой комментарий к этому посту:\n\n"
            f"---\n{post_text[:400]}\n---\n\n"
            + (f"Контекст: {context}\n\n" if context else "")
            + "Комментарий должен быть 1–3 предложения, искренний, не рекламный. "
            "Задай уточняющий вопрос или поддержи мысль. Только текст комментария."
        )
        comment_text = await _generate_text_with_ai(prompt)
        if not comment_text:
            return ''

        # Логируем в AgentActivityLog
        session = Session()
        try:
            from models import AgentActivityLog
            log = AgentActivityLog(
                user_id=user_id,
                activity_type='comment',
                title=comment_text[:80] + ('...' if len(comment_text) > 80 else ''),
                content=comment_text,
                target=f'К посту: {post_text[:80]}',
                status='published',
            )
            session.add(log)
            session.commit()
        finally:
            session.close()

        logger.info(f"[AUTO_COMMENT] User {user_id}: posted comment")
        return comment_text
    except Exception as e:
        logger.error(f"[AUTO_COMMENT] Error for user {user_id}: {e}")
        return ''


async def check_and_create_posts():
    """Main function to check and create automatic posts.
    
    Creates up to 2 blog posts per user per day at randomly-distributed times.
    Uses deterministic hashing (user_id + date) to pick 2 time slots per day
    from the available windows (10:00, 12:00, 14:00, 16:00, 18:00, 20:00, 22:00).
    """
    session = Session()
    
    try:
        # Get all active users with profiles (no city filter — all users with profiles)
        users = session.query(User).join(UserProfile).all()

        # Batch-load profiles (avoid N+1 inside the loop)
        _apc_uids = [u.id for u in users]
        _apc_profile_by_uid = {p.user_id: p for p in session.query(UserProfile).filter(
            UserProfile.user_id.in_(_apc_uids)
        ).all()} if _apc_uids else {}

        for user in users:
            try:
                profile = _apc_profile_by_uid.get(user.id)
                if not profile:
                    continue
                
                # Используем profile.timezone (приоритет), затем user.timezone
                _tz_str = profile.timezone or user.timezone or 'Europe/Moscow'
                try:
                    user_tz = pytz.timezone(_tz_str)
                except Exception:
                    user_tz = pytz.timezone('Europe/Moscow')
                user_now = datetime.now(pytz.UTC).astimezone(user_tz)
                
                current_hour = user_now.hour
                
                # Post between 10:00 and 23:00
                if 10 <= current_hour <= 23:
                    today_start = user_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.UTC)
                    
                    # Check how many posts already created today (max 2 per day)
                    posts_today = session.query(Post).filter(
                        Post.user_id == user.id,
                        Post.created_at >= today_start
                    ).count()
                    
                    if posts_today < 2:
                        # Deterministic scheduling: pick 2 random time slots per user per day
                        # This ensures consistent slot selection throughout the day
                        import hashlib
                        day_seed = f"{user.id}:{user_now.strftime('%Y-%m-%d')}"
                        day_hash = int(hashlib.md5(day_seed.encode()).hexdigest()[:8], 16)
                        
                        # Available 2-hour check windows: 10, 12, 14, 16, 18, 20, 22
                        available_hours = list(range(10, 24, 2))  # [10, 12, 14, 16, 18, 20, 22]
                        rng = random.Random(day_hash)
                        post_hours = rng.sample(available_hours, min(2, len(available_hours)))
                        
                        if current_hour in post_hours:
                            logger.info(f"Creating progress post for user {user.telegram_id} (post #{posts_today + 1} today, slot={current_hour}:00)")
                            content = await generate_progress_post(user.telegram_id, session)
                            if content:
                                await create_auto_post(user.telegram_id, content, session)
                
            except Exception as e:
                logger.error(f"Error processing user {user.telegram_id}: {e}")
                continue
        
    except Exception as e:
        logger.error(f"Error in check_and_create_posts: {e}")
    finally:
        session.close()


async def run_service():
    """Run the service continuously"""
    logger.info("Auto-post service started")
    
    while True:
        try:
            await check_and_create_posts()
        except Exception as e:
            logger.error(f"Error in service loop: {e}")
        
        # Check every 2 hours for more varied timing
        await asyncio.sleep(7200)


if __name__ == "__main__":
    asyncio.run(run_service())
