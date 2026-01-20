# -*- coding: utf-8 -*-
"""Инициализация БД с новыми полями"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import Base, engine

print("Создание таблиц...")
Base.metadata.create_all(engine)
print("Готово!")
