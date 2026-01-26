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

from models import Session, User, UserProfile, Task, Post
from ai_integration.chat import chat_with_ai

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def calculate_zodiac_sign(birthdate_str):
    """Calculate zodiac sign from birthdate (DD.MM.YYYY)"""
    try:
        day, month, year = map(int, birthdate_str.split('.'))
        
        zodiac_dates = [
            (20, '╨Ъ╨╛╨╖╨╡╤А╨╛╨│', '╨Т╨╛╨┤╨╛╨╗╨╡╨╣'), (19, '╨Т╨╛╨┤╨╛╨╗╨╡╨╣', '╨а╤Л╨▒╤Л'),
            (21, '╨а╤Л╨▒╤Л', '╨Ю╨▓╨╡╨╜'), (20, '╨Ю╨▓╨╡╨╜', '╨в╨╡╨╗╨╡╤Ж'),
            (21, '╨в╨╡╨╗╨╡╤Ж', '╨С╨╗╨╕╨╖╨╜╨╡╤Ж╤Л'), (21, '╨С╨╗╨╕╨╖╨╜╨╡╤Ж╤Л', '╨а╨░╨║'),
            (23, '╨а╨░╨║', '╨Ы╨╡╨▓'), (23, '╨Ы╨╡╨▓', '╨Ф╨╡╨▓╨░'),
            (23, '╨Ф╨╡╨▓╨░', '╨Т╨╡╤Б╤Л'), (23, '╨Т╨╡╤Б╤Л', '╨б╨║╨╛╤А╨┐╨╕╨╛╨╜'),
            (22, '╨б╨║╨╛╤А╨┐╨╕╨╛╨╜', '╨б╤В╤А╨╡╨╗╨╡╤Ж'), (22, '╨б╤В╤А╨╡╨╗╨╡╤Ж', '╨Ъ╨╛╨╖╨╡╤А╨╛╨│')
        ]
        
        cutoff_day, sign1, sign2 = zodiac_dates[month - 1]
        return sign2 if day >= cutoff_day else sign1
    except:
        return None


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
        user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
        now_utc = datetime.now(pytz.UTC)
        user_now = now_utc.astimezone(user_tz)
        today_start = user_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.UTC)
        
        # Get actual tasks with their details
        tasks_today = session.query(Task).filter(
            Task.user_id == user.id,
            Task.created_at >= today_start
        ).all()
        
        completed_tasks = [t for t in tasks_today if t.status == 'completed']
        pending_tasks = [t for t in tasks_today if t.status in ['pending', 'in_progress']]
        overdue_tasks = [t for t in tasks_today if t.overdue and t.status != 'completed']
        
        # Build context for AI
        context = f"""Создай живой, естественный пост от лица пользователя {profile.first_name or user.username} о его дне.

Информация о пользователе:
- Имя: {profile.first_name or user.username}
- Город: {profile.city}
- Интересы: {profile.interests}
- Навыки: {profile.skills}
- Цели: {profile.goals}

Задачи сегодня:
"""
        
        if completed_tasks:
            context += f"\nВыполнено ({len(completed_tasks)}):\n"
            for task in completed_tasks[:5]:  # Показываем до 5 задач
                context += f"- {task.description}\n"
        
        if pending_tasks:
            context += f"\nВ работе ({len(pending_tasks)}):\n"
            for task in pending_tasks[:3]:
                context += f"- {task.description}\n"
        
        if overdue_tasks:
            context += f"\nПросрочено ({len(overdue_tasks)}):\n"
            for task in overdue_tasks[:2]:
                context += f"- {task.description}\n"
        
        if not tasks_today:
            context += "\nСегодня задач не создавалось.\n"
        
        context += """
Требования к посту:
1. Пиши от первого лица, как будто сам пользователь делится своим днем
2. Упомяни 1-2 конкретные выполненные задачи (если есть), но естественно, в контексте
3. Покажи эмоции - радость от достижений, усталость, мотивацию или даже сомнения
4. Добавь личные детали: что помогло/помешало, какие мысли возникали
5. Если есть интересы/цели пользователя, можно связать задачи с ними
6. Если задач мало или нет - расскажи о планах, размышлениях, идеях
7. Максимум 3-4 предложения
8. БЕЗ эмодзи, БЕЗ хештегов, БЕЗ призывов к действию
9. Стиль - живой разговорный, как в обычной соцсети

Пример хорошего поста:
"Сегодня наконец-то доделал презентацию для клиента, над которой сидел всю неделю. Честно, думал не успею - вечером совещание накладывалось, но взял тайм-аут и сосредоточился. Теперь можно выдохнуть. Правда, статью для блога так и не начал писать - откладываю уже третий день, надо себя заставить."

Создай пост:"""
        
        # Use AI to generate natural post
        try:
            response = await chat_with_ai(
                user_id=user_id,
                message=context,
                bot=None,
                session=session
            )
            
            if response and len(response) > 20:
                # Clean up response - remove quotes if present
                post_content = response.strip().strip('"').strip("'")
                logger.info(f"[AUTO POST] Generated AI post for {user_id}: {post_content[:100]}")
                return post_content
            else:
                # Fallback to simple message
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
            task_desc = completed_tasks[0].description[:50] + "..." if len(completed_tasks[0].description) > 50 else completed_tasks[0].description
            return f"Сегодня справился с '{task_desc}'. Маленькие шаги к большим целям."
    elif pending_tasks:
        return f"Сегодня в работе {len(pending_tasks)} задач. Шаг за шагом продвигаюсь вперед."
    else:
        return "Сегодня планирую задачи на завтра. Главное - не терять фокус."


async def create_auto_post(user_id, content, session, notify=True):
    """Create a post in the database and optionally notify user"""
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
            username=user.username,
            content=content,
            created_at=datetime.now(pytz.UTC)
        )
        
        session.add(post)
        session.commit()
        
        logger.info(f"Auto-post created for user {user_id}")
        
        # Notify user about created post
        if notify:
            try:
                from main import bot
                if bot:
                    notification_text = f"🤖 Автопост опубликован!\n\n💡 Пост о вашем прогрессе создан и опубликован в ленте. Вы можете просмотреть его в веб-панели."
                    
                    await bot.send_message(
                        chat_id=user_id,
                        text=notification_text
                    )
                    logger.info(f"Notification sent to user {user_id} about auto-post")
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
                
                user_tz = pytz.timezone(user.timezone) if user.timezone else pytz.UTC
                user_now = datetime.now(pytz.UTC).astimezone(user_tz)
                
                # Create progress post at random time during the day
                # Only create if user has tasks and hasn't posted today
                current_hour = user_now.hour
                
                # Post between 12:00 and 22:00
                if 12 <= current_hour <= 22:
                    today_start = user_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.UTC)
                    
                    # Check if already posted today
                    existing_post = session.query(Post).filter(
                        Post.user_id == user.id,
                        Post.created_at >= today_start
                    ).first()
                    
                    if not existing_post:
                        # Random chance to post (20% chance per check)
                        if random.random() < 0.2:
                            logger.info(f"Creating progress post for user {user.telegram_id}")
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
        
        # Check every hour
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(run_service())
