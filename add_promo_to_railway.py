"""
Скрипт для добавления промокодов напрямую в Railway PostgreSQL базу данных
"""
import datetime
import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Используем DATABASE_URL из переменных окружения (Railway)
DATABASE_URL = os.environ.get('DATABASE_URL')

if not DATABASE_URL:
    logger.error("DATABASE_URL не найден в переменных окружения!")
    logger.info("Установите переменную окружения DATABASE_URL с подключением к Railway PostgreSQL")
    exit(1)

# Исправляем URL для psycopg2
if DATABASE_URL.startswith('postgresql://'):
    DATABASE_URL = DATABASE_URL.replace('postgresql://', 'postgresql+psycopg2://', 1)

logger.info(f"Подключение к базе данных: {DATABASE_URL[:30]}...")

try:
    engine = create_engine(DATABASE_URL)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    # Проверяем подключение
    session.execute(text("SELECT 1"))
    logger.info("✓ Подключение к Railway PostgreSQL успешно!")
    
    # Промокоды для добавления
    promo_codes = [
        {
            'code': 'LIGHT1',
            'tier': 'LIGHT',
            'discount_percent': 100,
            'duration_days': 30,
            'expires_at': '2026-12-31 23:59:59+00',
            'max_uses': None
        },
        {
            'code': 'STD2026XPRO',
            'tier': 'STANDARD',
            'discount_percent': 100,
            'duration_days': 30,
            'expires_at': '2026-12-31 23:59:59+00',
            'max_uses': None
        },
        {
            'code': 'PREM2026ELITE',
            'tier': 'PREMIUM',
            'discount_percent': 100,
            'duration_days': 30,
            'expires_at': '2026-12-31 23:59:59+00',
            'max_uses': None
        },
        {
            'code': 'VIPACCESS2026',
            'tier': 'PREMIUM',
            'discount_percent': 100,
            'duration_days': 365,
            'expires_at': '2026-12-31 23:59:59+00',
            'max_uses': 1
        }
    ]
    
    print("\n" + "="*60)
    print("ДОБАВЛЕНИЕ ПРОМОКОДОВ В RAILWAY POSTGRESQL")
    print("="*60)
    
    for promo in promo_codes:
        # Проверяем, существует ли промокод
        result = session.execute(
            text("SELECT code FROM promo_codes WHERE code = :code"),
            {'code': promo['code']}
        ).fetchone()
        
        if result:
            logger.info(f"⚠️  Промокод {promo['code']} уже существует")
            continue
        
        # Добавляем промокод
        session.execute(
            text("""
                INSERT INTO promo_codes 
                (code, tier, discount_percent, duration_days, expires_at, max_uses, used_count, used_by_users, created_at)
                VALUES 
                (:code, :tier, :discount_percent, :duration_days, :expires_at, :max_uses, 0, '[]', NOW())
            """),
            {
                'code': promo['code'],
                'tier': promo['tier'],
                'discount_percent': promo['discount_percent'],
                'duration_days': promo['duration_days'],
                'expires_at': promo['expires_at'],
                'max_uses': promo['max_uses']
            }
        )
        logger.info(f"✓ Создан промокод: {promo['code']} ({promo['tier']}, {promo['duration_days']} дней)")
    
    session.commit()
    
    # Показываем все промокоды
    print("\n" + "="*60)
    print("ВСЕ ПРОМОКОДЫ В RAILWAY")
    print("="*60)
    
    result = session.execute(
        text("""
            SELECT code, tier, discount_percent, duration_days, max_uses, used_count 
            FROM promo_codes 
            ORDER BY created_at DESC
        """)
    ).fetchall()
    
    for row in result:
        max_uses_str = str(row[4]) if row[4] is not None else '∞'
        print(f"\nКод: {row[0]}")
        print(f"  Тариф: {row[1]}")
        print(f"  Скидка: {row[2]}%")
        print(f"  Длительность: {row[3]} дней")
        print(f"  Лимит: {max_uses_str}")
        print(f"  Использований: {row[5]}")
        print("-"*60)
    
    print("\n✅ Промокоды успешно добавлены в Railway PostgreSQL!")
    
except Exception as e:
    logger.error(f"Ошибка: {e}")
    session.rollback()
    raise
finally:
    session.close()
