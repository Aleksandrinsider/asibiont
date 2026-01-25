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
        
        # Get user profile for personalization
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        profile_info = ""
        if profile:
            profile_parts = []
            if profile.skills:
                profile_parts.append(f"Навыки: {profile.skills}")
            if profile.interests:
                profile_parts.append(f"Интересы: {profile.interests}")
            if profile.goals:
                profile_parts.append(f"Цели: {profile.goals}")
            if profile.company:
                profile_parts.append(f"Компания: {profile.company}")
            if profile.position:
                profile_parts.append(f"Должность: {profile.position}")
            if profile.city:
                profile_parts.append(f"Город: {profile.city}")
            if profile_parts:
                profile_info = f"\n\nМой профиль:\n{'; '.join(profile_parts)}"
        
        # Generate post using AI
        prompt = f"""Напиши короткий пост от первого лица о моём прогрессе за сегодня. 

Статистика:
- Создано задач: {total_today}
- Выполнено задач: {completed_today}
- Не выполнено в срок: {pending_today}{profile_info}

Требования:
- Пиши от первого лица (я, мне, мой)
- Будь честным - если не сделал ничего, так и скажи
- Без конкретных названий задач
- ОБЯЗАТЕЛЬНО упомяни 1-2 элемента из профиля (интересы/навыки/цели) чтобы другие пользователи заинтересовались
- НЕ упоминай конкретное время (не пиши "сейчас 08:45" и т.п.)
- Добавь что-то мотивирующее или вопрос для вовлечения других
- Максимум 2-3 предложения
- Неформальный стиль
- Используй эмодзи где уместно

Примеры стиля:
- "Сегодня закрыл 5 задач из 7, неплохо! 💪 Осталось доделать пару мелочей. Как у вас день прошёл?"
- "Признаюсь честно - сегодня не самый продуктивный день, выполнил только 2 задачи из 6 😅 Завтра возьму реванш! У кого похожая ситуация?"
- "Ничего не успел сегодня, зато придумал план на завтра! Иногда планирование важнее суеты. Согласны?"
"""
        
        # Use direct AI call without task management context
        import requests
        from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
        
        try:
            response = requests.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": DEEPSEEK_MODEL,
                    "messages": [
                        {
                            "role": "system",
                            "content": "Ты пишешь короткие посты от первого лица для ленты новостей. Стиль неформальный, используешь эмодзи. БЕЗ технических вопросов, БЕЗ упоминания времени, только контент поста."
                        },
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ],
                    "temperature": 0.7,
                    "max_tokens": 200
                },
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                post_content = result['choices'][0]['message']['content'].strip()
            else:
                logger.error(f"DeepSeek API error: {response.status_code}")
                post_content = None
                
        except Exception as api_error:
            logger.error(f"Error calling DeepSeek API: {api_error}")
            post_content = None
        
        # Clean up AI response
        if post_content:
            # Remove any system messages or markers
            post_content = post_content.strip()
            # Remove questions about time/tasks
            if "На какое время" in post_content or "поставить задачу" in post_content:
                post_content = None
            # Limit length
            elif len(post_content) > 300:
                post_content = post_content[:297] + "..."
        
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
                    notification_text = f"📝 Я автоматически создал пост о вашем прогрессе:\n\n{content}\n\n💡 Поделитесь своими результатами с другими участниками ASI Biont!\n\n🌐 Используйте веб-панель для большего: https://asibiont.ru\n→ Редактируйте посты\n→ Находите контакты по интересам\n→ Управляйте задачами визуально"
                    
                    await bot.send_message(
                        chat_id=user_id,
                        text=notification_text,
                        disable_web_page_preview=True
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
                
                # Create progress posts 2 times per day at random times
                # Morning: 9:00-12:00, Evening: 17:00-21:00
                current_hour = user_now.hour
                
                today_start = user_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(pytz.UTC)
                
                # Count existing posts today
                posts_today = session.query(Post).filter(
                    Post.user_id == user.id,
                    Post.created_at >= today_start
                ).count()
                
                # Allow up to 2 posts per day
                if posts_today < 2:
                    # Morning slot (9-12) or Evening slot (17-21)
                    is_morning_slot = 9 <= current_hour <= 12
                    is_evening_slot = 17 <= current_hour <= 21
                    
                    if is_morning_slot or is_evening_slot:
                        # Check if already posted in this time slot today
                        if is_morning_slot:
                            slot_start = user_now.replace(hour=9, minute=0, second=0, microsecond=0).astimezone(pytz.UTC)
                            slot_end = user_now.replace(hour=12, minute=59, second=59, microsecond=999999).astimezone(pytz.UTC)
                        else:
                            slot_start = user_now.replace(hour=17, minute=0, second=0, microsecond=0).astimezone(pytz.UTC)
                            slot_end = user_now.replace(hour=21, minute=59, second=59, microsecond=999999).astimezone(pytz.UTC)
                        
                        existing_post_in_slot = session.query(Post).filter(
                            Post.user_id == user.id,
                            Post.created_at >= slot_start,
                            Post.created_at <= slot_end
                        ).first()
                        
                        if not existing_post_in_slot:
                            # Random chance to post (25% chance per check for more frequency)
                            if random.random() < 0.25:
                                logger.info(f"Creating progress post for user {user.telegram_id} ({'morning' if is_morning_slot else 'evening'} slot)")
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
