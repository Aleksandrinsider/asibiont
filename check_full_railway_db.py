"""
Проверка всей структуры Railway БД
"""
import psycopg2
from psycopg2 import sql
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_URL = "postgresql://postgres:upZTbJrZvoxnoSPdUDaOwnLuOvnNSbML@nozomi.proxy.rlwy.net:52451/railway"

try:
    # Подключение
    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor()
    
    print("\n" + "="*70)
    print("ПРОВЕРКА RAILWAY POSTGRESQL БД")
    print("="*70)
    
    # 1. Список всех таблиц
    cursor.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public'
        ORDER BY table_name;
    """)
    tables = cursor.fetchall()
    
    print(f"\n📊 Всего таблиц: {len(tables)}")
    print("-"*70)
    for table in tables:
        print(f"  ✓ {table[0]}")
    
    # 2. Проверка таблицы promo_codes
    print("\n" + "="*70)
    print("ТАБЛИЦА promo_codes")
    print("="*70)
    
    if any(t[0] == 'promo_codes' for t in tables):
        # Структура таблицы
        cursor.execute("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'promo_codes'
            ORDER BY ordinal_position;
        """)
        columns = cursor.fetchall()
        
        print("\n📋 Структура таблицы:")
        for col in columns:
            nullable = "NULL" if col[2] == 'YES' else "NOT NULL"
            print(f"  {col[0]:<20} {col[1]:<20} {nullable}")
        
        # Содержимое таблицы
        cursor.execute("""
            SELECT code, tier, discount_percent, duration_days, 
                   max_uses, used_count, expires_at
            FROM promo_codes
            ORDER BY created_at;
        """)
        promos = cursor.fetchall()
        
        print(f"\n💳 Промокодов в таблице: {len(promos)}")
        print("-"*70)
        for p in promos:
            max_uses = str(p[4]) if p[4] is not None else '∞'
            print(f"\n  Код: {p[0]}")
            print(f"    Тариф: {p[1]}")
            print(f"    Скидка: {p[2]}%")
            print(f"    Длительность: {p[3]} дней")
            print(f"    Лимит: {max_uses}")
            print(f"    Использований: {p[5]}")
            print(f"    Истекает: {p[6]}")
    else:
        print("\n❌ Таблица promo_codes НЕ СУЩЕСТВУЕТ!")
    
    # 3. Статистика по основным таблицам
    print("\n" + "="*70)
    print("СТАТИСТИКА ПО ТАБЛИЦАМ")
    print("="*70)
    
    important_tables = ['users', 'subscriptions', 'tasks', 'promo_codes', 'payment_history']
    
    for table_name in important_tables:
        if any(t[0] == table_name for t in tables):
            cursor.execute(f"SELECT COUNT(*) FROM {table_name};")
            count = cursor.fetchone()[0]
            print(f"  {table_name:<20} {count:>6} записей")
        else:
            print(f"  {table_name:<20} не существует")
    
    # 4. Проверка subscriptions
    print("\n" + "="*70)
    print("АКТИВНЫЕ ПОДПИСКИ")
    print("="*70)
    
    if any(t[0] == 'subscriptions' for t in tables):
        cursor.execute("""
            SELECT telegram_username, tier, status, 
                   to_char(end_date, 'DD.MM.YYYY') as end_date
            FROM subscriptions
            WHERE status = 'active'
            ORDER BY end_date DESC
            LIMIT 10;
        """)
        subs = cursor.fetchall()
        
        if subs:
            print(f"\nНайдено активных подписок: {len(subs)}")
            for s in subs:
                print(f"  @{s[0] or 'unknown':<15} {s[1]:<10} до {s[3]}")
        else:
            print("\nАктивных подписок нет")
    
    print("\n" + "="*70)
    print("✅ ПРОВЕРКА ЗАВЕРШЕНА")
    print("="*70)
    
    cursor.close()
    conn.close()
    
except Exception as e:
    logger.error(f"❌ Ошибка подключения: {e}")
    raise
