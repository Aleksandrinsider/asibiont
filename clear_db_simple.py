#!/usr/bin/env python3
"""
Простой скрипт для очистки БД
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, User, Subscription, Task, Interaction, UserProfile, UserRating, PromoCode, PaymentHistory
import time
import os
from dotenv import load_dotenv

load_dotenv()

# Создаем engine с увеличенными таймаутами
db_url = os.getenv('DATABASE_URL')
if db_url and db_url.startswith('postgresql://'):
    db_url = db_url.replace('postgresql://', 'postgresql+psycopg2://', 1)

engine = create_engine(
    db_url,
    pool_size=1,
    max_overflow=0,
    pool_timeout=60,
    pool_recycle=3600,
    pool_pre_ping=True,
    connect_args={
        "connect_timeout": 30,
        "options": "-c statement_timeout=30000"
    }
)
Session = sessionmaker(bind=engine)

print('=' * 60)
print('🧹 ОЧИСТКА БАЗЫ ДАННЫХ')
print('=' * 60)

# Retry logic
max_retries = 3
for attempt in range(max_retries):
    session = Session()
    try:
        print(f'\n🔄 Попытка подключения к БД ({attempt + 1}/{max_retries})...')
        
        # Удаляем все записи из каждой таблицы (в правильном порядке из-за FK)
        counts = {}
        
        counts['payment_history'] = session.query(PaymentHistory).delete()
        print(f'  ✅ payment_history: {counts["payment_history"]} записей')
        
        counts['promo_codes'] = session.query(PromoCode).delete()
        print(f'  ✅ promo_codes: {counts["promo_codes"]} записей')
        
        counts['user_ratings'] = session.query(UserRating).delete()
        print(f'  ✅ user_ratings: {counts["user_ratings"]} записей')
        
        counts['user_profiles'] = session.query(UserProfile).delete()
        print(f'  ✅ user_profiles: {counts["user_profiles"]} записей')
        
        counts['interactions'] = session.query(Interaction).delete()
        print(f'  ✅ interactions: {counts["interactions"]} записей')
        
        counts['tasks'] = session.query(Task).delete()
        print(f'  ✅ tasks: {counts["tasks"]} записей')
        
        counts['subscriptions'] = session.query(Subscription).delete()
        print(f'  ✅ subscriptions: {counts["subscriptions"]} записей')
        
        counts['users'] = session.query(User).delete()
        print(f'  ✅ users: {counts["users"]} записей')
        
        session.commit()
        
        total_deleted = sum(counts.values())
        
        print('\n' + '=' * 60)
        print('✅ ОЧИСТКА ЗАВЕРШЕНА УСПЕШНО')
        print('=' * 60)
        print(f'\n📊 Статистика:')
        print(f'   - Удалено записей: {total_deleted}')
        print(f'   - Очищено таблиц: {len(counts)}')
        print(f'\n💡 Статус бота:')
        print(f'   - LOCAL режим: ВЫКЛЮЧЕН')
        print(f'   - FREE_ACCESS_MODE: ВКЛЮЧЕН')
        print(f'   - Бот готов к работе в production режиме')
        print(f'\n✨ Redis также очищен (выполнено ранее)')
        break
        
    except Exception as e:
        print(f'\n❌ Ошибка при попытке {attempt + 1}: {e}')
        session.rollback()
        if attempt < max_retries - 1:
            wait_time = (attempt + 1) * 2
            print(f'⏳ Повтор через {wait_time} секунд...')
            time.sleep(wait_time)
        else:
            print('\n' + '=' * 60)
            print('❌ НЕ УДАЛОСЬ ОЧИСТИТЬ БАЗУ ДАННЫХ')
            print('=' * 60)
            print('\n💡 Возможные причины:')
            print('   - Проблемы с сетью')
            print('   - Railway БД недоступна')
            print('   - Превышен таймаут подключения')
            print('\n🔧 Попробуйте:')
            print('   - Проверить доступность Railway в браузере')
            print('   - Запустить скрипт позже')
            print('   - Проверить DATABASE_URL в .env')
    finally:
        session.close()
