#!/usr/bin/env python3
"""
Скрипт для очистки продакшен базы данных с сохранением промокодов
"""

import os
import sys
from pathlib import Path

# Добавляем корневую директорию в путь
root_dir = Path(__file__).parent
sys.path.insert(0, str(root_dir))

# Устанавливаем продакшен режим
os.environ['LOCAL'] = 'False'

from models import Session, engine, Base, User, Task, UserProfile, Subscription, Interaction, UserRating, PaymentHistory, Post, PostLike, Comment, PromoCode

def clear_production_db():
    """Очистка продакшен базы данных с сохранением промокодов"""
    print("🧹 Начинаем очистку продакшен базы данных (сохранение промокодов)...")

    try:
        # Создаем сессию
        session = Session()

        # Удаляем данные из всех таблиц в правильном порядке (сначала зависимые)
        # НЕ удаляем промокоды!
        print("Удаляем комментарии...")
        session.query(Comment).delete()

        print("Удаляем лайки постов...")
        session.query(PostLike).delete()

        print("Удаляем посты...")
        session.query(Post).delete()

        print("Удаляем историю платежей...")
        session.query(PaymentHistory).delete()

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

        # Очищаем foreign key ссылки в промокодах перед удалением пользователей
        print("Очищаем ссылки на пользователей в промокодах...")
        session.query(PromoCode).update({
            PromoCode.used_by_user_id: None,
            PromoCode.used_by_users: '[]'
        })

        print("Удаляем пользователей...")
        session.query(User).delete()

        # Коммитим изменения
        session.commit()
        print("✅ Все данные удалены, промокоды сохранены!")

        # Проверяем, что промокоды остались
        promo_count = session.query(PromoCode).count()
        print(f"📋 Сохранено промокодов: {promo_count}")

    except Exception as e:
        print(f"❌ Ошибка при очистке продакшен базы данных: {e}")
        session.rollback()
        return False
    finally:
        session.close()

    return True

if __name__ == "__main__":
    print("⚠️  ВНИМАНИЕ: Эта операция удалит ВСЕ данные из ПРОДАКШЕН базы данных!")
    print("📋 Промокоды будут сохранены.")
    confirm = input("Вы уверены, что хотите продолжить? (yes/no): ")

    if confirm.lower() in ['yes', 'y', 'да']:
        if clear_production_db():
            print("🎉 Продакшен база данных очищена, промокоды сохранены!")
        else:
            print("❌ Ошибка при очистке продакшен базы данных!")
            sys.exit(1)
    else:
        print("Операция отменена.")
        sys.exit(0)