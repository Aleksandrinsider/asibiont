#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Automatic post generation service - creates daily progress posts and birthday posts
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta
import pytz
import random
import json

from models import Session, User, UserProfile, Task, Post
from sqlalchemy import func, and_
from ai_integration.chat import chat_with_ai

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def calculate_zodiac_sign(birthdate_str):
    """Calculate zodiac sign from birthdate (DD.MM.YYYY)"""
    try:
        day, month, year = map(int, birthdate_str.split('.'))
        
        zodiac_dates = [
            (20, 'Козерог', 'Водолей'), (19, 'Водолей', 'Рыбы'),
            (21, 'Рыбы', 'Овен'), (20, 'Овен', 'Телец'),
            (21, 'Телец', 'Близнецы'), (21, 'Близнецы', 'Рак'),
            (23, 'Рак', 'Лев'), (23, 'Лев', 'Дева'),
            (23, 'Дева', 'Весы'), (23, 'Весы', 'Скорпион'),
            (22, 'Скорпион', 'Стрелец'), (22, 'Стрелец', 'Козерог')
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
        
        # Generate post using AI
        prompt = f"""Напиши короткий пост от первого лица о моём прогрессе за сегодня. 

Статистика:
- Создано задач: {total_today}
- Выполнено задач: {completed_today}
- Не выполнено в срок: {pending_today}

Требования:
- Пиши от первого лица (я, мне, мой)
- Будь честным - если не сделал ничего, так и скажи
- Без конкретных названий задач
- Добавь что-то мотивирующее или вопрос для вовлечения других
- Максимум 2-3 предложения
- Неформальный стиль
- Используй эмодзи где уместно

Примеры стиля:
- "Сегодня закрыл 5 задач из 7, неплохо! 💪 Осталось доделать пару мелочей. Как у вас день прошёл?"
- "Признаюсь честно - сегодня не самый продуктивный день, выполнил только 2 задачи из 6 😅 Завтра возьму реванш! У кого похожая ситуация?"
- "Ничего не успел сегодня, зато придумал план на завтра! Иногда планирование важнее суеты. Согласны?"
"""
        
        post_content = await chat_with_ai(prompt, user_id=user_id, db_session=session)
        
        # Clean up AI response
        if post_content:
            # Remove any system messages or markers
            post_content = post_content.strip()
            # Limit length
            if len(post_content) > 300:
                post_content = post_content[:297] + "..."
        
        return post_content
        
    except Exception as e:
        logger.error(f"Error generating progress post for user {user_id}: {e}")
        return None


async def generate_birthday_post(user_id, session):
    """Generate birthday post"""
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return None
        
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if not profile or not profile.birthdate:
            return None
        
        # Calculate age if possible
        try:
            day, month, year = map(int, profile.birthdate.split('.'))
            age = datetime.now().year - year
            age_text = f"{age} лет" if age > 0 else ""
        except:
            age_text = ""
        
        zodiac = profile.zodiac_sign or ""
        
        prompt = f"""Напиши короткий пост от первого лица о том, что сегодня мой день рождения.

Информация:
- Возраст: {age_text}
- Знак зодиака: {zodiac}

Требования:
- От первого лица
- Радостное настроение
- Можно пошутить про возраст
- Пригласи людей отметить вместе (виртуально)
- 2-3 предложения максимум
- Используй эмодзи

Примеры стиля:
- "Сегодня мой день рождения! 🎉 Ещё на год старше и мудрее (надеюсь 😄). Кто присоединится к виртуальному празднованию?"
- "🎂 День рождения! Официально стал на год опытнее. Принимаю поздравления и советы как прожить этот год ещё круче!"
"""
        
        post_content = await chat_with_ai(prompt, user_id=user_id, db_session=session)
        
        if post_content:
            post_content = post_content.strip()
            if len(post_content) > 300:
                post_content = post_content[:297] + "..."
        
        return post_content
        
    except Exception as e:
        logger.error(f"Error generating birthday post for user {user_id}: {e}")
        return None


async def create_auto_post(user_id, content, session, notify=True):
    """Create a post in the database and optionally notify user"""
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return False
        
        post = Post(
            user_id=user.id,
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
                    # Determine post type
                    is_birthday = 'день рождения' in content.lower() or 'birthday' in content.lower()
                    
                    if is_birthday:
                        notification_text = f"🎂 Я создал поздравительный пост в ленту от вашего имени:\n\n{content}\n\n✨ Пост опубликован и виден вашим контактам!"
                    else:
                        notification_text = f"📝 Я автоматически создал пост о вашем прогрессе:\n\n{content}\n\n💡 Это помогает поддерживать активность в ленте. Вы можете отредактировать или удалить пост в веб-панели."
                    
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
                
                # Check if it's birthday
                if profile.birthdate:
                    try:
                        day, month, _ = map(int, profile.birthdate.split('.'))
                        if user_now.day == day and user_now.month == month:
                            # Check if birthday post already created today
                            today_start = user_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.UTC)
                            existing_post = session.query(Post).filter(
                                Post.user_id == user.id,
                                Post.created_at >= today_start,
                                Post.content.like('%день рождения%')
                            ).first()
                            
                            if not existing_post:
                                logger.info(f"Creating birthday post for user {user.telegram_id}")
                                content = await generate_birthday_post(user.telegram_id, session)
                                if content:
                                    await create_auto_post(user.telegram_id, content, session)
                                continue  # Skip progress post on birthday
                    except:
                        pass
                
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
