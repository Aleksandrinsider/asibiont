#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Automatic post generation service - creates daily progress posts and birthday posts
"""

import asyncio
import logging
from datetime import datetime
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
            {"role": "system", "content": "Ты - копирайтер. Пиши естественные посты от первого лица. Только текст поста, без пояснений."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.8,
        "max_tokens": 500
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
        
        # Get today's tasks stats
        user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.timezone('Europe/Moscow')
        now_utc = datetime.now(pytz.UTC)
        user_now = now_utc.astimezone(user_tz)
        today_start = user_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.UTC)
        
        # Get actual tasks with their details
        tasks_today = session.query(Task).filter(
            Task.user_id == user.id,
            Task.created_at >= today_start
        ).all()
        
        completed_tasks = [t for t in tasks_today if t.status == 'completed' or t.status == 'done']
        pending_tasks = [t for t in tasks_today if t.status in ['pending', 'in_progress']]
        overdue_tasks = [t for t in tasks_today if t.status == 'overdue']
        
        # Build context for AI
        user_name = user.first_name if user.first_name else (user.username or 'Пользователь')
        user_city = profile.city or 'не указан'
        user_interests = profile.interests or 'не указаны'
        user_skills = profile.skills or 'не указаны'
        user_goals = profile.goals or 'не указаны'
        
        context = f"""Создай живой, естественный пост от лица пользователя {user_name} о его дне.

Информация о пользователе:
- Имя: {user_name}
- Город: {user_city}
- Интересы: {user_interests}
- Навыки: {user_skills}
- Цели: {user_goals}

Задачи сегодня:
"""
        
        if completed_tasks:
            context += f"\nВыполнено ({len(completed_tasks)}):\n"
            for task in completed_tasks[:5]:  # Показываем до 5 задач
                desc = decrypt_data(task.description) if task.description else task.title
                context += f"- {desc}\n"
        
        if pending_tasks:
            context += f"\nВ работе ({len(pending_tasks)}):\n"
            for task in pending_tasks[:3]:
                desc = decrypt_data(task.description) if task.description else task.title
                context += f"- {desc}\n"
        
        if overdue_tasks:
            context += f"\nПросрочено ({len(overdue_tasks)}):\n"
            for task in overdue_tasks[:2]:
                desc = decrypt_data(task.description) if task.description else task.title
                context += f"- {desc}\n"
        
        if not tasks_today:
            context += "\nСегодня задач не создавалось и ничего не было выполнено.\n"
        
        has_completed = bool(completed_tasks)
        
        context += """
Требования к посту:
1. Пиши от первого лица, как будто сам пользователь делится своим днем
2. Упомяни 1-2 конкретные выполненные задачи (если есть), но естественно, в контексте
3. Покажи эмоции - радость от достижений, усталость, мотивацию или даже сомнения
4. Добавь личные детали: что помогло/помешало, какие мысли возникали
5. Если есть интересы/цели пользователя, можно связать задачи с ними"""
        
        if not has_completed:
            context += """
6. ВАЖНО: Сегодня НЕТ выполненных задач. НЕ ВЫДУМЫВАЙ достижения и действия! Честно напиши что сегодня был день без конкретных результатов. Можно упомянуть размышления, планы на завтра, или просто что взял паузу. Но НЕ придумывай что "сидел за кодом", "доделал проект" и т.д. если задач не было."""
        else:
            context += """
6. Основывайся ТОЛЬКО на реально выполненных задачах из списка выше. НЕ придумывай дополнительные достижения."""
        
        context += """
7. Максимум 3-4 предложения
8. БЕЗ эмодзи, БЕЗ хештегов, БЕЗ призывов к действию
9. Стиль - живой разговорный, как в обычной соцсети

Пример поста КОГДА ЕСТЬ выполненные задачи:
"Сегодня наконец-то доделал презентацию для клиента, над которой сидел всю неделю. Честно, думал не успею - вечером совещание накладывалось, но взял тайм-аут и сосредоточился. Теперь можно выдохнуть."

Пример поста КОГДА НЕТ выполненных задач:
"Сегодня как-то не задалось с продуктивностью — ничего конкретного не закрыл. Зато поймал себя на мысли, что давно откладываю структурирование идей по проекту. Завтра надо начать с этого, пока запал не пропал."

Создай пост:"""
        
        # Прямой вызов AI API для генерации поста (без полного агентского пайплайна)
        try:
            response = await _generate_text_with_ai(context)
            
            if response and len(response) > 20:
                # Clean up response - remove quotes if present
                post_content = response.strip().strip('"').strip("'")
                logger.info(f"[AUTO POST] Generated AI post for {user_id}: {post_content[:100]}")
                return post_content
            else:
                logger.warning(f"AI response too short for {user_id}, using fallback")
                return generate_simple_fallback(completed_tasks, pending_tasks, overdue_tasks)
                
        except Exception as ai_error:
            logger.error(f"AI generation failed for {user_id}: {ai_error}")
            return generate_simple_fallback(completed_tasks, pending_tasks, overdue_tasks)
        
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
