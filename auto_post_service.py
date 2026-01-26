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
        
        # Count tasks
        total_today = session.query(Task).filter(
            Task.user_id == user.id,
            Task.created_at >= today_start
        ).count()
        
        completed_today = session.query(Task).filter(
            Task.user_id == user.id,
            Task.status == 'completed',
            Task.actual_completion_time >= today_start
        ).count()
        
        pending_today = session.query(Task).filter(
            Task.user_id == user.id,
            Task.status == 'pending',
            Task.reminder_time >= today_start,
            Task.reminder_time < now_utc
        ).count()
        
        # Generate simple progress post using template
        if completed_today > 0:
            if completed_today == 1:
                achievement_text = f"Сегодня я выполнил {completed_today} задачу"
            elif 2 <= completed_today <= 4:
                achievement_text = f"Сегодня я выполнил {completed_today} задачи"
            else:
                achievement_text = f"Сегодня я выполнил {completed_today} задач"
            
            if pending_today > 0:
                advice_text = f"Есть {pending_today} просроченных задач - стоит пересмотреть приоритеты."
            else:
                advice_text = "Отличная работа! Продолжай в том же духе."
            
            post_content = f"[Автопост] {achievement_text} из {total_today} созданных сегодня.\n\n{advice_text}\n\n#продуктивность #asibiont"
        else:
            if total_today > 0:
                post_content = f"[Автопост] Сегодня создал {total_today} задач. Время браться за работу!\n\n#планирование #asibiont"
            else:
                post_content = f"[Автопост] Доброе утро! Сегодня отличный день для новых начинаний.\n\n#мотивация #asibiont"
        
        logger.info(f"[AUTO POST] Generated simple progress post: {post_content}")
        return post_content
        
    except Exception as e:
        logger.error(f"Error generating progress post for user {user_id}: {e}")
        return None


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
