#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Automatic post generation service - creates daily progress posts and birthday posts
"""

import asyncio
import logging
from datetime import datetime, timedelta
import pytz
import random
import aiohttp

from models import Session, User, UserProfile, Task, Post
from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
from ai_integration.memory import decrypt_data

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def _generate_text_with_ai(prompt: str) -> str:
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
            {"role": "system", "content": "You are a ghostwriter. Write short, authentic social media posts in first person. Voice: candid, natural, human — the kind of thing someone actually posts, not a press release. Output only the post text, nothing else."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.85,
        "max_tokens": 400
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=30)) as response:
            if response.status == 200:
                result = await response.json()
                return result['choices'][0]['message']['content'].strip()
            else:
                error_text = await response.text()
                raise Exception(f"AI API error: {response.status} {error_text[:200]}")


async def generate_progress_post(user_id, session):
    """Generate daily progress post using AI"""
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return None

        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if not profile:
            return None

        # Timezone & time-of-day
        user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.timezone('Europe/Moscow')
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

        # Last 2 posts to avoid repetition
        recent_posts = session.query(Post).filter(
            Post.user_id == user.id
        ).order_by(Post.created_at.desc()).limit(2).all()
        recent_snippets = [p.content[:80] for p in recent_posts if p.content]

        # Profile info
        user_name = user.first_name if user.first_name else (user.username or 'Пользователь')
        user_city = profile.city or ''
        user_interests = profile.interests or ''
        user_skills = profile.skills or ''
        user_goals = profile.goals or ''

        # Random tone variation to keep posts diverse
        tone = random.choice([
            "без прикрас, честно — как в чате другу",
            "немного устал, но доволен — рефлексивно",
            "энергично, с лёгким юмором",
            "спокойно и по-деловому",
            "с конкретикой и чуть личным",
        ])

        # Build prompt — pre-compute optional lines (no backslash in f-string expressions for Python <3.12)
        city_part = f", {user_city}" if user_city else ""
        skills_line = f"\n- Сфера / навыки: {user_skills}" if user_skills else ""
        interests_line = f"\n- Интересы: {user_interests}" if user_interests else ""
        goals_line = f"\n- Цели: {user_goals}" if user_goals else ""

        context = f"""Ты — ghostwriter. Пишешь короткий пост в соцсеть от лица реального человека. Стиль: {tone}. Не пиши "ghostwriter", не пиши от своего имени.

О человеке:
- Имя: {user_name}{city_part}{skills_line}{interests_line}{goals_line}
- Сейчас: {time_of_day}
"""

        if completed_today:
            context += f"\nЗакрыто сегодня ({len(completed_today)}):\n"
            for t in completed_today[:5]:
                desc = decrypt_data(t.description) if t.description else t.title
                notes = f" — {t.completion_notes[:60]}" if t.completion_notes else ""
                context += f"- {desc}{notes}\n"
        else:
            context += "\nЗакрытых задач сегодня нет.\n"

        if active_tasks:
            context += f"\nВ работе сейчас:\n"
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

        if recent_snippets:
            context += f"\nПредыдущие посты (НЕ повторяй тему и структуру):\n"
            for s in recent_snippets:
                context += f"- «{s}…»\n"

        context += f"""
Напиши пост. Правила:
— от первого лица, 3-4 предложения, максимум 500 символов
— живой разговорный текст, без буллетов и нумерации
— можно 1 эмодзи если уместно, без хештегов, без CTA
— {"основывайся ТОЛЬКО на реально закрытых задачах выше, не придумывай" if completed_today else "задач сегодня не было — напиши честно: размышление, пауза, планы — но НЕ придумывай несуществующие дела"}
— не начинай с имени пользователя, не заканчивай вопросом к аудитории

Только текст поста:"""

        # Call AI
        try:
            response = await _generate_text_with_ai(context)

            if response and len(response) > 20:
                post_content = response.strip().strip('"').strip("'")
                logger.info(f"[AUTO POST] Generated post for {user_id} (tone={tone}): {post_content[:100]}")
                return post_content
            else:
                logger.warning(f"AI response too short for {user_id}, using fallback")
                return generate_simple_fallback(completed_today, active_tasks, overdue_tasks)

        except Exception as ai_error:
            logger.error(f"AI generation failed for {user_id}: {ai_error}")
            return generate_simple_fallback(completed_today, active_tasks, overdue_tasks)

    except Exception as e:
        logger.error(f"Error generating progress post for user {user_id}: {e}")
        return None


def generate_simple_fallback(completed_tasks, pending_tasks, overdue_tasks):
    """Generate simple fallback message when AI fails"""
    if completed_tasks:
        if len(completed_tasks) >= 3:
            return f"Сегодня продуктивный день выдался - закрыл {len(completed_tasks)} задач. Приятно видеть прогресс!"
        else:
            raw_desc = decrypt_data(completed_tasks[0].description) if completed_tasks[0].description else completed_tasks[0].title
            task_desc = raw_desc[:50] + "..." if len(raw_desc) > 50 else raw_desc
            return f"Сегодня справился с '{task_desc}'. Маленькие шаги к большим целям."
    elif pending_tasks:
        return f"Сегодня в работе {len(pending_tasks)} задач. Шаг за шагом продвигаюсь вперед."
    else:
        return "Сегодня планирую задачи на завтра. Главное - не терять фокус."


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
        
        context = f"""Создай живой, естественный пост от лица пользователя {user_name}, который только что провел исследование рынка/темы.

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
1. Пиши от первого лица: "Изучал тему...", "Обнаружил интересное...", "Оказывается..."
2. Упомяни 1-2 самых интересных находки, но естественно
3. Покажи своё отношение - удивление, энтузиазм, или скептицизм
4. Добавь личный контекст: зачем изучал, что планируешь с этим делать
5. Максимум 3-4 предложения
6. БЕЗ эмодзи, БЕЗ хештегов, БЕЗ призывов к действию
7. Стиль - естественный разговорный, как будто делишься находкой с друзьями

Примеры хороших постов:
"Сегодня копался в теме AI для малого бизнеса. Оказывается, 67% стартапов уже используют чат-ботов, но большинство просто отвечают на FAQ, не углубляясь. Думаю, можно сделать что-то умнее - интегрировать с CRM и автоматизировать продажи. Надо протестировать идею на паре клиентах."

"Решил изучить рынок доставки здорового питания в Москве. Интересно, что средний чек вырос с 800₽ до 1200₽ за год, но при этом конкуренция сумасшедшая - больше 50 игроков. Возможно, имеет смысл фокусироваться на узкой нише типа кето-диет или спортпита."

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
        
        post = Post(
            user_id=user.id,
            username=user.username or user.first_name or f"user_{user.telegram_id}",
            content=content,
            created_at=datetime.now(pytz.UTC)
        )
        
        session.add(post)
        session.commit()

        # Log agent activity
        try:
            from models import AgentActivityLog
            short_title = content[:80] + ('...' if len(content) > 80 else '')
            log_entry = AgentActivityLog(
                user_id=user.id,
                activity_type='post_newsfeed',
                title=short_title,
                content=content,
                target='Лента новостей',
                status='published',
                ref_id=post.id,
            )
            session.add(log_entry)
            session.commit()
        except Exception as log_err:
            logger.warning(f"[AUTO_POST] Failed to log activity: {log_err}")
        
        logger.info(f"Auto-post created for user {user_id}")

        # === Отправка в Discord webhook (если настроен) ===
        try:
            if user.discord_webhook and user.discord_webhook.startswith('https://discord.com/api/webhooks/'):
                async with aiohttp.ClientSession() as dc_session:
                    dc_payload = {"content": content}
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
                                    title=content[:80] + ('...' if len(content) > 80 else ''),
                                    content=content,
                                    target='Discord канал',
                                    status='published',
                                    ref_id=post.id,
                                )
                                session.add(dc_log)
                                session.commit()
                            except Exception as _le:
                                logger.warning(f"[DISCORD] Failed to log activity: {_le}")
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
                
                notification = await agent.generate_system_message(
                    user_id=user_id,
                    mode='result_check',
                    instruction=(
                        f"Только что опубликован пост в ленту новостей на сайте. "
                        f"Содержание поста: «{content[:150]}{'...' if len(content) > 150 else ''}». "
                        f"Скажи об этом коротко и естественно, дай ссылку https://asibiont.com/dashboard. "
                        f"Предложи отредактировать или удалить если что-то не так."
                    ),
                    max_tokens=400,
                    max_iterations=1
                )
                
                if notification and notification.strip():
                    from config import TELEGRAM_TOKEN
                    import aiohttp
                    if TELEGRAM_TOKEN:
                        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
                        data = {"chat_id": user_id, "text": notification}
                        async with aiohttp.ClientSession() as aio_session:
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


async def check_and_create_posts():
    """Main function to check and create automatic posts"""
    session = Session()
    
    try:
        # Get all active users with profiles
        users = session.query(User).join(UserProfile).filter(
            UserProfile.city.isnot(None)  # Only users who completed profile
        ).all()
        
        for user in users:
            try:
                profile = session.query(UserProfile).filter_by(user_id=user.id).first()
                if not profile:
                    continue
                
                user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.timezone('Europe/Moscow')
                user_now = datetime.now(pytz.UTC).astimezone(user_tz)
                
                # Create progress post at random time during the day
                # Only create if user has tasks and hasn't posted today
                current_hour = user_now.hour
                
                # Post between 10:00 and 23:00
                if 10 <= current_hour <= 23:
                    today_start = user_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.UTC)
                    
                    # Check how many posts already created today (allow up to 2 per day)
                    posts_today = session.query(Post).filter(
                        Post.user_id == user.id,
                        Post.created_at >= today_start
                    ).count()
                    
                    if posts_today < 2:
                        # Lower probability for more random timing (8% chance per check)
                        # This creates more varied posting times throughout the day
                        if random.random() < 0.08:
                            logger.info(f"Creating progress post for user {user.telegram_id} (post #{posts_today + 1} today)")
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
