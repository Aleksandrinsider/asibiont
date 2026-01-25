#!/usr/bin/env python3
"""
Скрипт для полной очистки базы данных
"""

import os
import sys
from pathlib import Path

# Добавляем корневую директорию в путь
root_dir = Path(__file__).parent
sys.path.insert(0, str(root_dir))

from models import Session, engine, Base, User, Task, UserProfile, Subscription, Interaction, UserRating, SubscriptionTier, PromoCode, PaymentHistory, Post, PostLike, Comment

def clear_database():
    """Полная очистка всех данных из базы данных"""
    print("🧹 Начинаем полную очистку базы данных...")

    try:
        # Создаем сессию
        session = Session()

        # Удаляем данные из всех таблиц в правильном порядке (сначала зависимые)
        print("Удаляем комментарии...")
        session.query(Comment).delete()

        print("Удаляем лайки постов...")
        session.query(PostLike).delete()

        print("Удаляем посты...")
        session.query(Post).delete()

        print("Удаляем историю платежей...")
        session.query(PaymentHistory).delete()

        print("Удаляем промокоды...")
        session.query(PromoCode).delete()

        print("Удаляем рейтинги пользователей...")
        session.query(UserRating).delete()

        print("Удаляем взаимодействия...")
        session.query(Interaction).delete()

        print("Удаляем подписки...")
        session.query(Subscription).delete()

        print("Удаляем профили пользователей...")
        session.query(UserProfile).delete()

        print("Удаляем задачи...")
        session.query(Task).delete()

        print("Удаляем пользователей...")
        session.query(User).delete()

        # Коммитим изменения
        session.commit()
        print("✅ Все данные успешно удалены!")

        # Пересоздаем таблицы для чистоты
        print("🔄 Пересоздаем таблицы...")
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        print("✅ Таблицы пересозданы!")

    except Exception as e:
        print(f"❌ Ошибка при очистке базы данных: {e}")
        session.rollback()
        return False
    finally:
        session.close()

    return True

if __name__ == "__main__":
    print("⚠️  ВНИМАНИЕ: Эта операция удалит ВСЕ данные из базы данных!")
    confirm = input("Вы уверены, что хотите продолжить? (yes/no): ")

    if confirm.lower() in ['yes', 'y', 'да']:
        if clear_database():
            print("🎉 База данных полностью очищена!")
        else:
            print("❌ Ошибка при очистке базы данных!")
            sys.exit(1)
    else:
        print("Операция отменена.")
        sys.exit(0)