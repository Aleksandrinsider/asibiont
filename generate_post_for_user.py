#!/usr/bin/env python3
"""Generate and create a post for a specific user"""

import os
os.environ['LOCAL'] = '1'

import asyncio
import sys
from models import Session, User, Post, Task
from datetime import datetime
import pytz

# Import AI integration
from ai_integration.chat import chat_with_ai

async def generate_and_create_post(username):
    """Generate a post using AI and save it to the database"""
    session = Session()
    try:
        # Find user
        user = session.query(User).filter_by(username=username).first()
        if not user:
            print(f"❌ Пользователь {username} не найден")
            return
        
        print(f"✅ Найден пользователь: {user.username} (ID: {user.id})")
        
        # Get task stats for context
        total_tasks = session.query(Task).filter_by(user_id=user.id).count()
        completed_tasks = session.query(Task).filter_by(user_id=user.id, status='completed').count()
        
        # Get user profile for personalization
        from models import UserProfile
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
        
        # Generate post content using AI
        print("\n🤖 Генерирую пост с помощью AI...")
        
        prompt = f"""Создай короткий интересный пост для ленты новостей от первого лица.

Контекст:
- Всего задач: {total_tasks}
- Выполнено: {completed_tasks}{profile_info}

Требования:
- От первого лица (я, мне)
- Неформальный стиль
- 1-2 предложения
- Можно про продуктивность, планы или мысли
- Добавь 1-2 эмодзи
- БЕЗ упоминания "создал пост" или "опубликовал"
- Просто текст поста

Примеры:
"Сегодня закрыл 5 дел из списка 💪 Чувствую себя продуктивным!"
"Планирую большой проект на эту неделю 🚀 Кто тоже любит амбициозные цели?"
"Иногда лучше сделать меньше, но качественно 🎯 Согласны?"

Напиши только текст поста, без пояснений:"""
        
        # Use AI to generate post
        response = await chat_with_ai(
            message=prompt,
            user_id=user.telegram_id,
            db_session=session
        )
        
        # Extract content from response
        if isinstance(response, dict):
            content = response.get('response', '').strip()
        else:
            content = str(response).strip()
        
        if not content or len(content) < 10:
            print("❌ AI не сгенерировал валидный контент")
            return
        
        # Remove system messages from content
        if 'создал пост' in content.lower() or 'опубликовал' in content.lower():
            # Try to extract just the post content
            lines = content.split('\n')
            for line in lines:
                if len(line) > 20 and not any(word in line.lower() for word in ['создал', 'опубликовал', 'пост', 'лента']):
                    content = line.strip()
                    break
        
        print(f"\n📝 Сгенерированный контент:")
        print(f"   {content}")
        
        # Create post
        now = datetime.now(pytz.UTC)
        post = Post(
            user_id=user.id,
            content=content,
            created_at=now
        )
        
        session.add(post)
        session.commit()
        
        print(f"\n✅ Пост создан! ID: {post.id}")
        print(f"   Автор: {user.username}")
        print(f"   Время: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        
        return post
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        session.rollback()
    finally:
        session.close()


if __name__ == '__main__':
    username = 'aleksandrinsider'
    if len(sys.argv) > 1:
        username = sys.argv[1]
    
    print(f"🚀 Генерирую пост для пользователя: {username}\n")
    asyncio.run(generate_and_create_post(username))
