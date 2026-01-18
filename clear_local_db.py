#!/usr/bin/env python3
"""
Простой скрипт для очистки локальной SQLite БД
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, User, Subscription, Task, Interaction, UserProfile, UserRating, PromoCode, PaymentHistory

# Локальная SQLite база
db_url = "sqlite:///local.db"

engine = create_engine(db_url)
Session = sessionmaker(bind=engine)

print('=' * 60)
print('🧹 ОЧИСТКА ЛОКАЛЬНОЙ БАЗЫ ДАННЫХ (SQLite)')
print('=' * 60)

session = Session()
try:
    print('\n🔄 Очистка таблиц...')
    
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
    
except Exception as e:
    print(f'\n❌ Ошибка: {e}')
    session.rollback()
finally:
    session.close()