#!/usr/bin/env python3
"""
Скрипт для просмотра данных в Railway PostgreSQL базе данных
"""
import os
import sys
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

# Используем Railway DATABASE_URL
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("❌ DATABASE_URL не найден в .env файле")
    sys.exit(1)

print(f"🔗 Подключаемся к: {DATABASE_URL[:50]}...")

try:
    # Создаем подключение
    engine = create_engine(DATABASE_URL, echo=False)

    # Тестируем подключение
    with engine.connect() as conn:
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

                # Показываем структуру таблицы
                columns_result = conn.execute(text(f"""
                    SELECT column_name, data_type, is_nullable
                    FROM information_schema.columns
                    WHERE table_name = '{table_name}'
                    ORDER BY ordinal_position
                """))
                columns = columns_result.fetchall()
                print(f"    Колонки: {len(columns)}")
                for col in columns[:5]:  # Показываем первые 5 колонок
                    print(f"      {col[0]} ({col[1]}) {'NULL' if col[2] == 'YES' else 'NOT NULL'}")
                if len(columns) > 5:
                    print(f"      ... и ещё {len(columns) - 5} колонок")

                # Показываем первые 3 записи из таблицы
                if count > 0:
                    try:
                        data_result = conn.execute(text(f"SELECT * FROM {table_name} LIMIT 3"))
                        rows = data_result.fetchall()
                        print(f"    Пример данных (первые {len(rows)} записей):")
                        for i, row in enumerate(rows):
                            print(f"      [{i+1}] {dict(row) if hasattr(row, '_mapping') else row}")
                    except Exception as e:
                        print(f"    ❌ Ошибка при получении данных: {e}")

            except Exception as e:
                print(f"    ❌ Ошибка при анализе таблицы: {e}")

            print()

except Exception as e:
    print(f"❌ Ошибка подключения: {e}")
    print("\n💡 Возможные причины:")
    print("  - Проверьте, что Railway сервис запущен")
    print("  - Проверьте переменные окружения в Railway dashboard")
    print("  - Убедитесь, что DATABASE_URL корректный")
    sys.exit(1)

print("✅ Готово!")