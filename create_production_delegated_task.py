#!/usr/bin/env python3
"""
Скрипт для создания тестовой делегированной задачи в production базе данных.
Запустите этот скрипт на Railway или в production среде.
"""

import os
import sys
from datetime import datetime, timezone

# Устанавливаем LOCAL=1 для использования production базы
os.environ['LOCAL'] = '1'

try:
    from models import Session, Task, User
    from ai_integration import encrypt_data
    print("✅ Импорт моделей прошел успешно")
except ImportError as e:
    print(f"❌ Ошибка импорта: {e}")
    print("Убедитесь, что скрипт запускается в правильной среде с установленными зависимостями")
    sys.exit(1)

def create_delegated_task_in_production():
    """Создает делегированную задачу в production базе"""

    session = Session()

    try:
        # Найдем пользователя, которому будем делегировать (aleksandrinsider)
        target_user = session.query(User).filter_by(username='aleksandrinsider').first()
        if not target_user:
            print("❌ Пользователь aleksandrinsider не найден в базе данных")
            print("Доступные пользователи:")
            users = session.query(User).limit(10).all()
            for user in users:
                print(f"  - {user.username} (ID: {user.id})")
            return

        # Найдем тестового пользователя, который будет делегировать
        delegator = session.query(User).filter_by(username='testuser1').first()
        if not delegator:
            # Попробуем найти другого тестового пользователя
            delegator = session.query(User).filter(User.username.like('test%')).first()
            if not delegator:
                print("❌ Тестовый пользователь не найден")
                print("Создам нового тестового пользователя...")

                # Создадим тестового пользователя
                delegator = User(
                    telegram_id=999999999,
                    username='testuser_delegator',
                    subscription_tier='GOLD'
                )
                session.add(delegator)
                session.commit()
                print(f"✅ Создан тестовый пользователь: {delegator.username} (ID: {delegator.id})")

        print(f"✅ Найден делегатор: {delegator.username} (ID: {delegator.id})")
        print(f"✅ Найден получатель: {target_user.username} (ID: {target_user.id})")

        # Проверим, нет ли уже такой задачи
        existing_task = session.query(Task).filter(
            Task.user_id == delegator.id,
            Task.delegated_to_username.ilike(target_user.username.replace('@', '')),
            Task.title.like('%тестовая делегированная%')
        ).first()

        if existing_task:
            print(f"⚠️  Такая задача уже существует (ID: {existing_task.id})")
            return

        # Создадим делегированную задачу
        task = Task(
            user_id=delegator.id,  # Кто делегирует
            title="Тестовая делегированная задача от Railway",
            description=encrypt_data("Это тестовая задача для проверки отображения контактов в разделе 'Делегируют мне' в production"),
            delegated_to_username=target_user.username.replace('@', ''),  # Кому делегируют
            delegation_status="accepted",  # Уже принята
            status="active",
            created_at=datetime.now(timezone.utc)
        )

        # Добавим время напоминания (через 1 час)
        reminder_time = datetime.now(timezone.utc)
        reminder_time = reminder_time.replace(hour=(reminder_time.hour + 1) % 24)
        task.reminder_time = reminder_time

        session.add(task)
        session.commit()

        print(f"✅ Создана задача ID {task.id}: '{task.title}'")
        print(f"   Делегирована от @{delegator.username} к @{target_user.username}")
        print(f"   Статус делегирования: {task.delegation_status}")
        print(f"   Время напоминания: {task.reminder_time}")

        # Проверим, что задача находится в поиске
        check_tasks = session.query(Task).filter(
            Task.delegated_to_username.ilike(target_user.username.replace('@', '')),
            Task.delegation_status.in_(['pending', 'accepted'])
        ).all()

        print(f"\n📋 Всего задач, делегированных {target_user.username}: {len(check_tasks)}")
        for t in check_tasks:
            del_user = session.query(User).filter_by(id=t.user_id).first()
            del_name = del_user.username if del_user else 'НЕ НАЙДЕН'
            is_self = ' (САМ СЕБЕ)' if del_user and del_user.id == target_user.id else ''
            print(f"  - '{t.title}' от {del_name}{is_self}")

        # Проверим, что контакт появится в "Делегируют мне"
        other_tasks = [t for t in check_tasks if t.user_id != target_user.id]
        if other_tasks:
            print(f"\n✅ Теперь {target_user.username} должен видеть контакты в разделе 'Делегируют мне'")
            unique_delegators = set()
            for t in other_tasks:
                del_user = session.query(User).filter_by(id=t.user_id).first()
                if del_user:
                    unique_delegators.add(del_user.username)
            print(f"   Контакты: {', '.join(unique_delegators)}")
        else:
            print(f"\n❌ Нет задач от других пользователей")

    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
    finally:
        session.close()

if __name__ == "__main__":
    print("🚀 Запуск создания тестовой делегированной задачи в production...")
    print(f"База данных: {os.getenv('DATABASE_PUBLIC_URL', 'не установлена')[:50]}...")
    print()

    create_delegated_task_in_production()

    print("\n✨ Скрипт завершен!")