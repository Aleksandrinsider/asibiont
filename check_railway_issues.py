"""
Скрипт для проверки всех проблем на Railway для пользователя @aleksandrinsider
"""
import os
import sys
import asyncio
from datetime import datetime
import pytz

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

# Set environment to use Railway database
os.environ['LOCAL'] = '0'

from models import Session
from models import User, UserProfile, Task, Post
from ai_integration.handlers import get_partners_list
from sqlalchemy import or_

async def check_user_data():
    """Проверка данных пользователя @aleksandrinsider"""
    session = Session()
    try:
        # 1. Найти пользователя
        user = session.query(User).filter(
            or_(
                User.username == 'aleksandrinsider',
                User.username == '@aleksandrinsider'
            )
        ).first()
        
        if not user:
            print("❌ ПРОБЛЕМА: Пользователь @aleksandrinsider не найден в БД!")
            return
        
        print(f"✅ Пользователь найден: {user.username} (ID: {user.id}, telegram_id: {user.telegram_id})")
        print(f"   Подписка: {user.subscription_tier}")
        print(f"   Рейтинг: {user.average_rating} ({user.rating_count} оценок)")
        
        # 2. Проверить профиль
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        
        if not profile:
            print("❌ ПРОБЛЕМА: Профиль пользователя не найден!")
            return
        
        print(f"\n📋 ПРОФИЛЬ:")
        print(f"   Город: {profile.city or 'НЕ ЗАПОЛНЕН'}")
        print(f"   Компания: {profile.company or 'НЕ ЗАПОЛНЕНА'}")
        print(f"   Должность: {profile.position or 'НЕ ЗАПОЛНЕНА'}")
        print(f"   Интересы: {profile.interests or 'НЕ ЗАПОЛНЕНЫ'}")
        print(f"   Навыки: {profile.skills or 'НЕ ЗАПОЛНЕНЫ'}")
        print(f"   Цели: {profile.goals or 'НЕ ЗАПОЛНЕНЫ'}")
        
        # 3. Проверить задачи
        tasks = session.query(Task).filter_by(user_id=user.id).all()
        print(f"\n📝 ЗАДАЧИ: {len(tasks)} всего")
        active_tasks = [t for t in tasks if t.status in ['active', 'pending', 'in_progress']]
        print(f"   Активных: {len(active_tasks)}")
        
        if active_tasks:
            print("\n   Активные задачи:")
            for task in active_tasks[:5]:
                print(f"   - {task.title} (статус: {task.status})")
        
        # 4. Проверить посты
        posts = session.query(Post).filter_by(user_id=user.id).all()
        print(f"\n📰 ПОСТЫ: {len(posts)} всего")
        if posts:
            print("   Последние посты:")
            for post in posts[:3]:
                print(f"   - {post.content[:50]}... (создан: {post.created_at})")
        
        # 5. Проверить избранные контакты
        import json
        favorite_contacts = []
        if profile.favorite_contacts:
            try:
                favorite_contacts = json.loads(profile.favorite_contacts)
            except:
                pass
        
        print(f"\n⭐ ИЗБРАННЫЕ КОНТАКТЫ: {len(favorite_contacts)}")
        if favorite_contacts:
            for contact_data in favorite_contacts[:5]:
                # favorite_contacts может содержать ID или username
                if isinstance(contact_data, int):
                    contact_user = session.query(User).filter_by(id=contact_data).first()
                    if contact_user:
                        print(f"   - @{contact_user.username}")
                elif isinstance(contact_data, str):
                    print(f"   - {contact_data} (сохранен как username, нужно исправить)")
                else:
                    print(f"   - {contact_data} (неизвестный формат)")
        
        # 6. Проверить рекомендуемые контакты
        print(f"\n🔍 ПРОВЕРКА РЕКОМЕНДУЕМЫХ КОНТАКТОВ:")
        try:
            partners = get_partners_list(user_id=user.id)
            print(f"   Найдено партнеров: {len(partners)}")
            
            if len(partners) == 0:
                print("   ❌ ПРОБЛЕМА: Рекомендуемые контакты пусты!")
                
                # Проверим есть ли вообще другие пользователи
                all_users = session.query(User).filter(User.id != user.id).count()
                print(f"   Всего других пользователей в БД: {all_users}")
                
                # Проверим есть ли профили у других пользователей
                all_profiles = session.query(UserProfile).filter(UserProfile.user_id != user.id).count()
                print(f"   Профилей других пользователей: {all_profiles}")
                
                # Проверим фильтры
                if profile.blocked_contacts:
                    blocked = json.loads(profile.blocked_contacts)
                    print(f"   Заблокированных контактов: {len(blocked)}")
                
            else:
                print(f"   ✅ Рекомендуемые контакты найдены:")
                for i, partner in enumerate(partners[:5], 1):
                    partner_user = session.query(User).filter_by(id=partner.user_id).first()
                    if partner_user:
                        print(f"   {i}. @{partner_user.username}")
                        if hasattr(partner, 'task_relevance') and partner.task_relevance:
                            print(f"      Релевантность: {partner.task_relevance} (score: {partner.task_relevance_score})")
                        if hasattr(partner, 'common_interests') and partner.common_interests:
                            print(f"      Общие интересы: {partner.common_interests}")
        
        except Exception as e:
            print(f"   ❌ ОШИБКА при получении партнеров: {e}")
            import traceback
            traceback.print_exc()
        
        # 7. Проверить посты в ленте
        print(f"\n📱 ПРОВЕРКА ЛЕНТЫ НОВОСТЕЙ:")
        
        # Получить ID избранных контактов
        favorite_user_ids = []
        if profile.favorite_contacts:
            try:
                favorite_user_ids = json.loads(profile.favorite_contacts)
            except:
                pass
        
        # Включить посты самого пользователя
        all_user_ids = favorite_user_ids + [user.id]
        
        print(f"   Показывать посты от: {len(all_user_ids)} пользователей")
        
        if all_user_ids:
            feed_posts = session.query(Post).filter(
                Post.user_id.in_(all_user_ids)
            ).order_by(Post.created_at.desc()).limit(10).all()
            
            print(f"   Найдено постов: {len(feed_posts)}")
            
            if len(feed_posts) == 0:
                print("   ❌ ПРОБЛЕМА: Лента новостей пуста!")
                print("   Проверяем посты избранных контактов:")
                for fav_id in favorite_user_ids[:3]:
                    fav_user = session.query(User).filter_by(id=fav_id).first()
                    if fav_user:
                        fav_posts = session.query(Post).filter_by(user_id=fav_user.id).count()
                        print(f"      @{fav_user.username}: {fav_posts} постов")
            else:
                print(f"   ✅ Посты в ленте:")
                for post in feed_posts[:3]:
                    post_author = session.query(User).filter_by(id=post.user_id).first()
                    print(f"   - @{post_author.username if post_author else 'unknown'}: {post.content[:50]}...")
        
        # 8. Проверить проблему с пропаданием контактов
        print(f"\n🔄 ДИАГНОСТИКА ПРОПАДАЮЩИХ КОНТАКТОВ:")
        
        # Проверим делегированные задачи
        username_variants = [f"@{user.username}", user.username]
        delegated_to_me = session.query(Task).filter(
            Task.delegated_to_username.in_(username_variants),
            Task.delegation_status.in_(['pending', 'accepted'])
        ).count()
        
        delegated_by_me = session.query(Task).filter(
            Task.user_id == user.id,
            Task.delegated_to_username.isnot(None),
            Task.delegation_status.in_(['pending', 'accepted'])
        ).count()
        
        print(f"   Делегировано мне: {delegated_to_me} задач")
        print(f"   Делегировано мной: {delegated_by_me} задач")
        
        # Проверим кеш
        print(f"\n   Возможные причины пропадания:")
        print(f"   1. Проблемы с Redis кешем (если используется)")
        print(f"   2. Фильтрация по subscription_tier")
        print(f"   3. Скрытые контакты (hide_contact в memory)")
        
        if user.memory:
            from ai_integration.memory import decrypt_data
            try:
                decrypted = decrypt_data(user.memory)
                if 'hide_contact' in decrypted:
                    print(f"   ⚠️ Найдены скрытые контакты в памяти")
            except:
                pass
        
        # 9. Рекомендации по исправлению
        print(f"\n💡 РЕКОМЕНДАЦИИ:")
        
        if len(partners) == 0:
            print("   1. Проверить фильтры в get_partners_list()")
            print("   2. Убедиться что есть другие пользователи с заполненными профилями")
            print("   3. Проверить subscription_tier совместимость")
        
        if len(feed_posts) == 0:
            print("   1. Добавить избранные контакты через API")
            print("   2. Создать посты от имени других пользователей")
        
        if not profile.city:
            print("   1. Заполнить город в профиле для лучшего поиска контактов")
        
        if not profile.interests:
            print("   1. Заполнить интересы для динамических рекомендаций")
        
    finally:
        session.close()

if __name__ == "__main__":
    print("="*80)
    print("ДИАГНОСТИКА ПРОБЛЕМ ПОЛЬЗОВАТЕЛЯ @aleksandrinsider")
    print("="*80)
    print(f"\nВремя проверки: {datetime.now(pytz.UTC).strftime('%Y-%m-%d %H:%M:%S')} UTC\n")
    
    asyncio.run(check_user_data())
    
    print("\n" + "="*80)
    print("ДИАГНОСТИКА ЗАВЕРШЕНА")
    print("="*80)
