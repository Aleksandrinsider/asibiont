# -*- coding: utf-8 -*-
"""Инициализация БД с новыми полями"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Устанавливаем локальный режим
os.environ['LOCAL'] = '1'

from models import Base, engine

print("Создание таблиц...")
print(f"База данных: {engine.url}")
print(f"Таблицы для создания: {list(Base.metadata.tables.keys())}")
Base.metadata.create_all(engine)
print("Готово!")
