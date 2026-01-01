"""
Проверка DATABASE_URL connection
"""
import os
if 'LOCAL' in os.environ:
    del os.environ['LOCAL']

from config import DATABASE_URL
from models import Session, User, engine
from sqlalchemy import text

print("="*60)
print("ПРОВЕРКА ПОДКЛЮЧЕНИЯ К БД")
print("="*60)

print(f"\nDATABASE_URL: {DATABASE_URL[:50]}...")
print(f"Engine: {engine.url}")

session = Session()
try:
    # Выполнить реальный запрос к БД
    result = session.execute(text("SELECT version()"))
    version = result.scalar()
    print(f"\nPostgreSQL Version: {version[:80]}...")
    
    # Проверить всех пользователей
    users = session.query(User).all()
    print(f"\nВсего пользователей в БД: {len(users)}")
    for user in users:
        print(f"  ID={user.id}, telegram_id={user.telegram_id}, username={user.username}")
    
    # Проверить конкретного пользователя
    user = session.query(User).filter_by(telegram_id=146333757).first()
    if user:
        print(f"\nПользователь 146333757:")
        print(f"  Database ID: {user.id}")
        print(f"  Username: {user.username}")
        print(f"  Created: {user.created_at}")
    else:
        print("\n❌ Пользователь 146333757 НЕ НАЙДЕН!")
        
finally:
    session.close()

print("\n" + "="*60)
print("Если Database ID != 1, значит скрипт подключается не к той БД!")
print("="*60)
