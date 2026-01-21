"""
Быстрый тест агента - проверка основных функций без async
"""
import os
import sys

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

# Set environment to use Railway database
os.environ['LOCAL'] = '0'

from models import Session, User, UserProfile, Task, Post
from sqlalchemy import or_, func
from datetime import datetime, timedelta
import pytz

def test_database_state():
    """Проверка текущего состояния базы данных"""
    session = Session()
    
    try:
        user = session.query(User).filter(
            or_(
                User.username == 'aleksandrinsider',
                User.username == '@aleksandrinsider'
            )
        ).first()
        
        if not user:
            print("❌ Пользователь не найден")
            return
        
        print("="*80)
        print("ПРОВЕРКА СОСТОЯНИЯ БД ДЛЯ @aleksandrinsider")
        print("="*80)
        
        # 1. Профиль
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        print(f"\n📋 ПРОФИЛЬ:")
        print(f"   Город: {profile.city}")
        print(f"   Компания: {profile.company}")
        print(f"   Должность: {profile.position}")
        print(f"   Интересы: {profile.interests}")
        print(f"   Навыки: {profile.skills or 'НЕ ЗАПОЛНЕНО'}")
        print(f"   Цели: {profile.goals or 'НЕ ЗАПОЛНЕНО'}")
        
        # 2. Задачи
        tasks_total = session.query(Task).filter_by(user_id=user.id).count()
        tasks_active = session.query(Task).filter(
            Task.user_id == user.id,
            Task.status.in_(['active', 'pending', 'in_progress'])
        ).count()
        tasks_completed = session.query(Task).filter_by(
            user_id=user.id, 
            status='completed'
        ).count()
        
        print(f"\n📝 ЗАДАЧИ:")
        print(f"   Всего: {tasks_total}")
        print(f"   Активных: {tasks_active}")
        print(f"   Завершенных: {tasks_completed}")
        
        # Последние задачи
        recent_tasks = session.query(Task).filter_by(
            user_id=user.id
        ).order_by(Task.created_at.desc()).limit(5).all()
        
        if recent_tasks:
            print(f"\n   Последние 5 задач:")
            for task in recent_tasks:
                status_emoji = {
                    'completed': '✅',
                    'pending': '⏳',
                    'active': '🔄',
                    'in_progress': '🔄'
                }.get(task.status, '❓')
                print(f"   {status_emoji} {task.title} (статус: {task.status})")
        
        # 3. Посты
        posts_total = session.query(Post).filter_by(user_id=user.id).count()
        print(f"\n📰 ПОСТЫ:")
        print(f"   Всего постов: {posts_total}")
        
        recent_posts = session.query(Post).filter_by(
            user_id=user.id
        ).order_by(Post.created_at.desc()).limit(3).all()
        
        if recent_posts:
            print(f"\n   Последние посты:")
            for post in recent_posts:
                print(f"   - {post.content[:80]}...")
        
        # 4. Избранные контакты
        import json
        favorite_contacts = []
        if profile.favorite_contacts:
            try:
                favorite_contacts = json.loads(profile.favorite_contacts)
            except:
                pass
        
        print(f"\n⭐ ИЗБРАННЫЕ КОНТАКТЫ: {len(favorite_contacts)}")
        
        # 5. Статистика по всей БД
        print(f"\n📊 ОБЩАЯ СТАТИСТИКА БД:")
        total_users = session.query(User).count()
        total_profiles = session.query(UserProfile).count()
        total_tasks_db = session.query(Task).count()
        total_posts = session.query(Post).count()
        
        print(f"   Всего пользователей: {total_users}")
        print(f"   Профилей: {total_profiles}")
        print(f"   Задач в системе: {total_tasks_db}")
        print(f"   Постов в системе: {total_posts}")
        
        # 6. Проверка других пользователей для контактов
        print(f"\n👥 ДОСТУПНЫЕ КОНТАКТЫ:")
        
        other_users_with_sport = session.query(User, UserProfile).join(
            UserProfile, User.id == UserProfile.user_id
        ).filter(
            User.id != user.id,
            UserProfile.interests.ilike('%спорт%')
        ).limit(10).all()
        
        print(f"   Пользователей с интересом 'спорт': {len(other_users_with_sport)}")
        for other_user, other_profile in other_users_with_sport[:5]:
            print(f"   - @{other_user.username}: {other_profile.interests}")
        
        # 7. Проверка делегирования
        delegated_to_me = session.query(Task).filter(
            or_(
                Task.delegated_to_username == user.username,
                Task.delegated_to_username == f'@{user.username}'
            ),
            Task.status != 'deleted'
        ).count()
        
        delegated_by_me = session.query(Task).filter(
            Task.user_id == user.id,
            Task.delegated_to_username.isnot(None),
            Task.status != 'deleted'
        ).count()
        
        print(f"\n🔄 ДЕЛЕГИРОВАНИЕ:")
        print(f"   Делегировано мне: {delegated_to_me}")
        print(f"   Делегировано мной: {delegated_by_me}")
        
        # 8. Проверка на критичные проблемы
        print(f"\n🔍 ПРОВЕРКА КРИТИЧНЫХ ПРОБЛЕМ:")
        
        issues = []
        
        if not profile.skills:
            issues.append("⚠️ Навыки не заполнены - рекомендации будут менее точными")
        
        if not profile.goals:
            issues.append("⚠️ Цели не заполнены - проактивные сообщения будут менее полезными")
        
        if tasks_active == 0:
            issues.append("ℹ️ Нет активных задач")
        
        if len(favorite_contacts) == 0:
            issues.append("ℹ️ Нет избранных контактов - лента новостей будет пустой")
        
        if issues:
            for issue in issues:
                print(f"   {issue}")
        else:
            print(f"   ✅ Критичных проблем не обнаружено!")
        
    finally:
        session.close()


def test_api_endpoints_readiness():
    """Проверка готовности API endpoints"""
    
    print(f"\n{'='*80}")
    print("ПРОВЕРКА API ENDPOINTS")
    print(f"{'='*80}")
    
    print("\n📋 Endpoints которые должны работать:")
    endpoints = [
        "GET /api/tasks - Получение задач",
        "POST /api/tasks - Создание задачи",
        "PUT /api/tasks/{id} - Обновление задачи",
        "DELETE /api/tasks/{id} - Удаление задачи",
        "GET /api/partners - Получение контактов",
        "GET /api/feed - Получение ленты новостей",
        "POST /api/feed - Создание поста",
        "GET /api/profile - Получение профиля",
        "PUT /api/profile - Обновление профиля",
        "POST /api/chat - Отправка сообщения агенту",
    ]
    
    for endpoint in endpoints:
        print(f"   - {endpoint}")
    
    print(f"\n⚠️ Для тестирования API запустите сервер:")
    print(f"   python main.py")
    print(f"\n   Затем протестируйте через браузер или curl:")
    print(f"   curl http://localhost:8080/api/tasks")


def main():
    print("\n" + "="*80)
    print("БЫСТРАЯ ПРОВЕРКА АГЕНТА И БД")
    print("="*80)
    print(f"\nВремя: {datetime.now(pytz.UTC).strftime('%Y-%m-%d %H:%M:%S')} UTC\n")
    
    test_database_state()
    test_api_endpoints_readiness()
    
    print("\n" + "="*80)
    print("ПРОВЕРКА ЗАВЕРШЕНА")
    print("="*80)
    
    print(f"\n💡 РЕКОМЕНДАЦИИ ДЛЯ ПРОДАКШЕНА:")
    print(f"   1. Заполните навыки и цели в профиле")
    print(f"   2. Создайте несколько тестовых задач")
    print(f"   3. Добавьте избранные контакты")
    print(f"   4. Запустите main.py и протестируйте через дашборд")
    print(f"   5. Проверьте работу агента через /api/chat")


if __name__ == "__main__":
    main()
