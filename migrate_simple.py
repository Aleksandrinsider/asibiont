"""Простая миграция - добавление полей company и position"""
import os
os.environ['LOCAL'] = '1'

# Для SQLite можем просто пересоздать таблицы
from models import Base, engine
from sqlalchemy import text

print("Подключение к БД...")
print(f"Engine: {engine.url}")

try:
    # Для SQLite создаем колонки напрямую
    with engine.begin() as conn:
        try:
            conn.execute(text("ALTER TABLE user_profiles ADD COLUMN company VARCHAR(255)"))
            print("✓ Добавлена колонка company")
        except Exception as e:
            if "duplicate column" in str(e).lower() or "already exists" in str(e).lower():
                print("• Колонка company уже существует")
            else:
                print(f"Ошибка при добавлении company: {e}")
        
        try:
            conn.execute(text("ALTER TABLE user_profiles ADD COLUMN position VARCHAR(255)"))
            print("✓ Добавлена колонка position")
        except Exception as e:
            if "duplicate column" in str(e).lower() or "already exists" in str(e).lower():
                print("• Колонка position уже существует")
            else:
                print(f"Ошибка при добавлении position: {e}")
    
    print("\n✓ Миграция завершена!")
    
except Exception as e:
    print(f"✗ Ошибка: {e}")
finally:
    engine.dispose()
