"""
Добавление промокодов в Railway PostgreSQL
"""
import psycopg2
from psycopg2 import sql
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_URL = "postgresql://postgres:upZTbJrZvoxnoSPdUDaOwnLuOvnNSbML@nozomi.proxy.rlwy.net:52451/railway"

# Промокоды для добавления
PROMO_CODES = [
    ('LIGHT1', 'LIGHT', 100, 30, None, '2026-12-31 23:59:59'),
    ('STD2026XPRO', 'STANDARD', 100, 30, None, '2026-12-31 23:59:59'),
    ('PREM2026ELITE', 'PREMIUM', 100, 30, None, '2026-12-31 23:59:59'),
    ('VIPACCESS2026', 'PREMIUM', 100, 365, 1, '2026-12-31 23:59:59'),
]

try:
    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor()
    
    print("\n" + "="*70)
    print("ДОБАВЛЕНИЕ ПРОМОКОДОВ В RAILWAY")
    print("="*70)
    
    for code, tier, discount, days, max_uses, expires in PROMO_CODES:
        # Проверяем существование
        cursor.execute("SELECT code FROM promo_codes WHERE code = %s", (code,))
        exists = cursor.fetchone()
        
        if exists:
            logger.info(f"⚠️  {code} уже существует")
            continue
        
        # Добавляем промокод
        cursor.execute("""
            INSERT INTO promo_codes 
            (code, tier, discount_percent, duration_days, max_uses, 
             used_count, used_by_users, expires_at, created_at)
            VALUES 
            (%s, %s, %s, %s, %s, 0, '[]', %s, NOW())
        """, (code, tier, discount, days, max_uses, expires))
        
        max_uses_str = str(max_uses) if max_uses else '∞'
        logger.info(f"✓ Добавлен: {code} ({tier}, {days} дней, лимит: {max_uses_str})")
    
    conn.commit()
    
    # Проверяем результат
    print("\n" + "="*70)
    print("ПРОМОКОДЫ В БД")
    print("="*70)
    
    cursor.execute("""
        SELECT code, tier, discount_percent, duration_days, 
               COALESCE(max_uses::text, '∞') as max_uses, used_count
        FROM promo_codes
        ORDER BY created_at;
    """)
    
    promos = cursor.fetchall()
    
    for p in promos:
        print(f"\n  Код: {p[0]}")
        print(f"    Тариф: {p[1]}")
        print(f"    Скидка: {p[2]}%")
        print(f"    Длительность: {p[3]} дней")
        print(f"    Лимит: {p[4]}")
        print(f"    Использований: {p[5]}")
    
    print(f"\n{'='*70}")
    print(f"✅ Всего промокодов в БД: {len(promos)}")
    print("="*70)
    
    cursor.close()
    conn.close()
    
except Exception as e:
    logger.error(f"❌ Ошибка: {e}")
    if conn:
        conn.rollback()
    raise
