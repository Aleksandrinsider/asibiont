"""
Endpoint для добавления тестовых пользователей через веб-интерфейс
Добавьте этот код в main.py и зайдите на /admin/add-test-users?secret=aj00yr34Pmg9YM8gWSggYoCMSG8t1a6ahntl4OJyPcw
"""

from aiohttp import web
from datetime import datetime, timezone, timedelta
from models import Session, User, UserProfile, Subscription, SubscriptionTier
import os

async def add_test_users_endpoint(request):
    """Endpoint для добавления тестовых пользователей"""
    
    # Проверка admin secret
    secret = request.query.get('secret', '')
    admin_secret = os.getenv('ADMIN_SECRET', 'aj00yr34Pmg9YM8gWSggYoCMSG8t1a6ahntl4OJyPcw')
    
    if secret != admin_secret:
        return web.json_response({'error': 'Unauthorized'}, status=403)
    
    session = Session()
    
    try:
        # Данные пользователей
        sport_users = [
            {'username': 'sport_alex', 'telegram_id': 1000001, 'interests': 'футбол, баскетбол, волейбол', 'tier': SubscriptionTier.LIGHT},
            {'username': 'sport_maria', 'telegram_id': 1000002, 'interests': 'бег, йога, пилатес', 'tier': SubscriptionTier.STANDARD},
            {'username': 'sport_ivan', 'telegram_id': 1000003, 'interests': 'теннис, плавание, велоспорт', 'tier': SubscriptionTier.PREMIUM},
            {'username': 'sport_olga', 'telegram_id': 1000004, 'interests': 'фитнес, кроссфит, бодибилдинг', 'tier': SubscriptionTier.LIGHT},
            {'username': 'sport_dmitry', 'telegram_id': 1000005, 'interests': 'хоккей, биатлон, лыжи', 'tier': SubscriptionTier.STANDARD},
        ]
        
        business_users = [
            {'username': 'biz_anna', 'telegram_id': 2000001, 'interests': 'стартапы, маркетинг, продажи', 'tier': SubscriptionTier.PREMIUM},
            {'username': 'biz_sergey', 'telegram_id': 2000002, 'interests': 'инвестиции, финансы, криптовалюта', 'tier': SubscriptionTier.LIGHT},
            {'username': 'biz_elena', 'telegram_id': 2000003, 'interests': 'управление проектами, agile, scrum', 'tier': SubscriptionTier.STANDARD},
            {'username': 'biz_maxim', 'telegram_id': 2000004, 'interests': 'e-commerce, онлайн-торговля, логистика', 'tier': SubscriptionTier.PREMIUM},
            {'username': 'biz_victoria', 'telegram_id': 2000005, 'interests': 'HR, рекрутинг, обучение персонала', 'tier': SubscriptionTier.LIGHT},
        ]
        
        all_users = sport_users + business_users
        
        added = []
        skipped = []
        
        for user_data in all_users:
            # Проверяем существует ли пользователь
            existing = session.query(User).filter_by(telegram_id=user_data['telegram_id']).first()
            if existing:
                skipped.append(user_data['username'])
                continue
            
            # Создаем пользователя
            user = User(
                telegram_id=user_data['telegram_id'],
                username=user_data['username'],
                subscription_tier=user_data['tier'],
                created_at=datetime.now(timezone.utc)
            )
            session.add(user)
            session.flush()
            
            # Создаем профиль
            profile = UserProfile(
                user_id=user.id,
                interests=user_data['interests'],
                skills='',
                goals='',
                created_at=datetime.now(timezone.utc)
            )
            session.add(profile)
            
            # Создаем подписку
            end_date = datetime.now(timezone.utc) + timedelta(days=365)
            subscription = Subscription(
                user_id=user.id,
                telegram_id=user_data['telegram_id'],
                telegram_username=user_data['username'],
                username=user_data['username'],
                status='active',
                plan='yearly',
                tier=user_data['tier'],
                start_date=datetime.now(timezone.utc),
                end_date=end_date,
                login_count=1,
                created_at=datetime.now(timezone.utc)
            )
            session.add(subscription)
            
            added.append(f"@{user_data['username']} ({user_data['tier'].value})")
        
        session.commit()
        
        total = session.query(User).count()
        
        return web.json_response({
            'success': True,
            'added': added,
            'skipped': skipped,
            'total_users': total
        })
        
    except Exception as e:
        session.rollback()
        return web.json_response({
            'success': False,
            'error': str(e)
        }, status=500)
    finally:
        session.close()


# Добавьте этот роут в main.py:
# app.router.add_get('/admin/add-test-users', add_test_users_endpoint)
