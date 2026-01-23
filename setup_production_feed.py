"""
Скрипт для настройки ленты в production:
1. Добавляет test_sport_10 в избранное aleksandrinsider
2. Создает тестовые посты от test_sport_10
"""
import os
from models import Session, User, UserProfile, Post
from datetime import datetime, timezone

def main():
    session = Session()
    try:
        # Находим пользователей
        aleksandr = session.query(User).filter_by(username='aleksandrinsider').first()
        test_sport = session.query(User).filter_by(username='test_sport_10').first()
        
        if not aleksandr:
            print("❌ aleksandrinsider не найден")
            return
        
        if not test_sport:
            print("❌ test_sport_10 не найден")
            return
        
        print(f"✅ aleksandrinsider ID: {aleksandr.id}")
        print(f"✅ test_sport_10 ID: {test_sport.id}")
        
        # Получаем/создаем профиль aleksandrinsider
        profile = session.query(UserProfile).filter_by(user_id=aleksandr.id).first()
        if not profile:
            profile = UserProfile(user_id=aleksandr.id)
            session.add(profile)
            session.flush()
        
        # Добавляем test_sport_10 в избранное
        import json
        current_favorites = json.loads(profile.favorite_contacts or '[]')
        if 'test_sport_10' not in current_favorites:
            current_favorites.append('test_sport_10')
            profile.favorite_contacts = json.dumps(current_favorites)
            session.commit()
            print(f"✅ test_sport_10 добавлен в избранное aleksandrinsider")
        else:
            print(f"ℹ️ test_sport_10 уже в избранном")
        
        # Проверяем существующие посты
        existing_posts = session.query(Post).filter_by(user_id=test_sport.id).count()
        print(f"ℹ️ Текущее количество постов test_sport_10: {existing_posts}")
        
        # Создаем посты если их меньше 9
        posts_to_create = [
            {
                'content': '🎯 Сегодня пробежал личный рекорд - 10км за 45 минут! Чувствую себя отлично, тренировки дают результат. #бег #спорт #здоровье',
                'created_at': datetime.now(timezone.utc)
            },
            {
                'content': '☕️ Утренняя пробежка + кофе = идеальное начало дня. Кто со мной на завтра в 7:00? #утро #пробежка #мотивация',
                'created_at': datetime.now(timezone.utc)
            }
        ]
        
        created_count = 0
        for post_data in posts_to_create:
            if existing_posts + created_count >= 9:
                break
            
            new_post = Post(
                user_id=test_sport.id,
                content=post_data['content'],
                created_at=post_data['created_at']
            )
            session.add(new_post)
            created_count += 1
        
        if created_count > 0:
            session.commit()
            print(f"✅ Создано {created_count} постов от test_sport_10")
        else:
            print(f"ℹ️ Посты не созданы (уже достаточно)")
        
        # Итоговая статистика
        total_posts = session.query(Post).filter_by(user_id=test_sport.id).count()
        print(f"\n📊 Итого постов test_sport_10: {total_posts}")
        print(f"📊 Избранные aleksandrinsider: {profile.favorite_contacts}")
        
    except Exception as e:
        session.rollback()
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
    finally:
        session.close()

if __name__ == '__main__':
    main()
