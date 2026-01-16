#!/usr/bin/env python3
"""
Скрипт для просмотра данных в Railway PostgreSQL базе данных
"""
import os
import sys
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import time

# Загружаем переменные окружения
load_dotenv()

# Читаем DATABASE_URL напрямую из .env файла
def get_database_url_from_env():
    """Читаем DATABASE_URL из .env файла, игнорируя системные переменные"""
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(env_path):
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith('DATABASE_URL='):
                    return line.split('=', 1)[1].strip()
    return None

DATABASE_URL = get_database_url_from_env()
if not DATABASE_URL:
    print("❌ DATABASE_URL не найден в .env файле")
    sys.exit(1)

print(f"🔗 DATABASE_URL из .env файла: {DATABASE_URL}")
print(f"🔗 Подключаемся к: {DATABASE_URL[:50]}...")
print("⏳ Пытаемся подключиться (таймаут 30 сек)...")

start_time = time.time()

try:
    # Создаем подключение с таймаутом
    engine = create_engine(
        DATABASE_URL,
        echo=False,
        connect_args={
            'connect_timeout': 30,
        }
    )

    # Тестируем подключение
    print("🔄 Создаем соединение...")
    with engine.connect() as conn:
        connect_time = time.time() - start_time
        print(f"✅ Соединение установлено за {connect_time:.2f} сек")
        result = conn.execute(text("SELECT version()"))
        version = result.fetchone()[0]
        print(f"✅ Подключение успешно! PostgreSQL версия: {version[:50]}...")

        # Получаем список таблиц
        result = conn.execute(text("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            ORDER BY table_name
        """))
        tables = result.fetchall()

        print(f"\n📋 Найдено таблиц: {len(tables)}")
        for table in tables:
            table_name = table[0]
            print(f"  - {table_name}")

            # Получаем количество записей в таблице
            try:
                count_result = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
                count = count_result.fetchone()[0]
                print(f"    Записей: {count}")

                if count > 0:
                    # Показываем первые 2 записи из таблицы
                    try:
                        data_result = conn.execute(text(f"SELECT * FROM {table_name} LIMIT 2"))
                        # Используем mappings() для получения данных в виде словарей
                        rows = data_result.mappings().all()
                        print(f"    Пример данных:")
                        for i, row in enumerate(rows):
                            print(f"      [{i+1}] {dict(row)}")
                    except Exception as e:
                        print(f"    ❌ Ошибка при получении данных: {e}")

            except Exception as e:
                print(f"    ❌ Ошибка при анализе таблицы: {e}")

            print()

except Exception as e:
    elapsed = time.time() - start_time
    print(f"❌ Ошибка подключения через {elapsed:.1f} сек: {e}")
    print("\n💡 Возможные причины:")
    print("  - Railway PostgreSQL требует VPN или специального подключения")
    print("  - Проверьте, что Railway сервис запущен")
    print("  - Возможно, нужно использовать Railway CLI или веб-интерфейс")
    print("\n🔧 Попробуйте:")
    print("  1. Открыть Railway dashboard -> PostgreSQL -> Connect")
    print("  2. Использовать Railway CLI: railway connect")
    print("  3. Проверить переменные в Railway Variables")
    sys.exit(1)

print("✅ Готово!")