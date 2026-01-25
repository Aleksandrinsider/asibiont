#!/usr/bin/env python3
"""
Инициализация продакшен базы данных (PostgreSQL)
"""

import sys
import os
from pathlib import Path

# Добавляем корневую директорию в путь
root_dir = Path(__file__).parent
sys.path.insert(0, str(root_dir))

# Устанавливаем продакшен режим
os.environ['LOCAL'] = 'False'

from models import Base, engine

def init_production_db():
    """Инициализация таблиц в продакшен базе данных"""
    print("🚀 Инициализация продакшен базы данных...")
    print(f"База данных: {engine.url}")

    try:
        # Создаем все таблицы
        print(f"Создаем таблицы: {list(Base.metadata.tables.keys())}")
        Base.metadata.create_all(engine)
        print("✅ Таблицы успешно созданы в продакшен базе!")

    except Exception as e:
        print(f"❌ Ошибка при создании таблиц: {e}")
        return False

    return True

if __name__ == "__main__":
    if init_production_db():
        print("🎉 Продакшен база данных готова!")
    else:
        print("❌ Ошибка инициализации продакшен базы!")
        sys.exit(1)