"""Create diverse test tasks for user 146333757 to test all dashboard tabs."""
import os
os.environ['LOCAL'] = '0'  # Use Railway DB

from models import Session, User, Task, TaskStatus, TaskPriority
from datetime import datetime, timedelta
import random

def create_test_tasks():
    """Create diverse test tasks for all dashboard tabs."""
    session = Session()

    try:
        # Find the user
        user = session.query(User).filter_by(telegram_id=146333757).first()
        if not user:
            print("❌ User not found")
            return

        print(f"✅ Creating test tasks for user: {user.username}")

        # Get some other users for delegation
        other_users = session.query(User).filter(User.id != user.id).limit(5).all()
        if not other_users:
            print("❌ No other users found for delegation")
            return

        now = datetime.now()
        tasks_data = []

        # 1. Личные задачи (Personal tasks) - created by user, assigned to self
        personal_tasks = [
            {"title": "Подготовить презентацию для клиента", "description": "Создать презентацию о наших услугах", "status": TaskStatus.PENDING, "priority": TaskPriority.HIGH, "due_date": now + timedelta(days=2), "assigned_to": user.id, "created_by": user.id},
            {"title": "Позвонить поставщику", "description": "Обсудить условия поставки", "status": TaskStatus.IN_PROGRESS, "priority": TaskPriority.MEDIUM, "due_date": now + timedelta(days=1), "assigned_to": user.id, "created_by": user.id},
            {"title": "Прочитать отчет", "description": "Изучить квартальный отчет компании", "status": TaskStatus.PENDING, "priority": TaskPriority.LOW, "due_date": now + timedelta(days=7), "assigned_to": user.id, "created_by": user.id},
            {"title": "Организовать встречу команды", "description": "Запланировать еженедельную встречу", "status": TaskStatus.COMPLETED, "priority": TaskPriority.MEDIUM, "due_date": now - timedelta(days=1), "assigned_to": user.id, "created_by": user.id},
            {"title": "Обновить резюме", "description": "Добавить новые навыки и достижения", "status": TaskStatus.PENDING, "priority": TaskPriority.LOW, "due_date": now + timedelta(days=14), "assigned_to": user.id, "created_by": user.id},
        ]

        # 2. Назначенные мне (Assigned to me) - created by others, assigned to user
        assigned_to_me = []
        for i, other_user in enumerate(other_users[:3]):
            assigned_to_me.append({
                "title": f"Проверить работу {other_user.username}", "description": f"Рассмотреть и утвердить задачу от {other_user.username}",
                "status": TaskStatus.PENDING if i < 2 else TaskStatus.COMPLETED,
                "priority": TaskPriority.HIGH if i == 0 else TaskPriority.MEDIUM,
                "due_date": now + timedelta(days=random.randint(1, 5)) if i < 2 else now - timedelta(days=1),
                "assigned_to": user.id, "created_by": other_user.id
            })

        # 3. Назначенные мной (Assigned by me) - created by user, assigned to others
        assigned_by_me = []
        for i, other_user in enumerate(other_users[:3]):
            assigned_by_me.append({
                "title": f"Подготовить отчет для {other_user.username}", "description": f"Создать детальный отчет по проекту {i+1}",
                "status": TaskStatus.IN_PROGRESS if i == 0 else TaskStatus.PENDING,
                "priority": TaskPriority.HIGH if i == 0 else TaskPriority.MEDIUM,
                "due_date": now + timedelta(days=random.randint(2, 7)),
                "assigned_to": other_user.id, "created_by": user.id
            })

        # 4. С отставанием (Overdue) - past due date, not completed
        overdue_tasks = [
            {"title": "Отправить налоговую декларацию", "description": "Подать декларацию в налоговую", "status": TaskStatus.PENDING, "priority": TaskPriority.HIGH, "due_date": now - timedelta(days=3), "assigned_to": user.id, "created_by": user.id},
            {"title": "Обновить ПО на сервере", "description": "Установить последние обновления безопасности", "status": TaskStatus.IN_PROGRESS, "priority": TaskPriority.MEDIUM, "due_date": now - timedelta(days=1), "assigned_to": user.id, "created_by": user.id},
            {"title": "Провести аудит проекта", "description": "Проверить качество кода и документации", "status": TaskStatus.PENDING, "priority": TaskPriority.HIGH, "due_date": now - timedelta(days=5), "assigned_to": user.id, "created_by": user.id},
        ]

        # 5. Выполненные (Completed) - completed tasks
        completed_tasks = [
            {"title": "Заказать канцтовары", "description": "Купить ручки, бумагу и маркеры", "status": TaskStatus.COMPLETED, "priority": TaskPriority.LOW, "due_date": now - timedelta(days=2), "assigned_to": user.id, "created_by": user.id},
            {"title": "Отправить приглашения", "description": "Разослать приглашения на конференцию", "status": TaskStatus.COMPLETED, "priority": TaskPriority.MEDIUM, "due_date": now - timedelta(days=7), "assigned_to": user.id, "created_by": user.id},
            {"title": "Создать бэкап базы данных", "description": "Сделать полную резервную копию", "status": TaskStatus.COMPLETED, "priority": TaskPriority.HIGH, "due_date": now - timedelta(days=1), "assigned_to": user.id, "created_by": user.id},
            {"title": "Провести обучение персонала", "description": "Организовать тренинг по новым процедурам", "status": TaskStatus.COMPLETED, "priority": TaskPriority.MEDIUM, "due_date": now - timedelta(days=3), "assigned_to": user.id, "created_by": user.id},
        ]

        # Combine all tasks
        all_tasks = personal_tasks + assigned_to_me + assigned_by_me + overdue_tasks + completed_tasks

        created_count = 0
        for task_data in all_tasks:
            try:
                task = Task(
                    title=task_data["title"],
                    description=task_data["description"],
                    status=task_data["status"],
                    priority=task_data["priority"],
                    due_date=task_data["due_date"],
                    assigned_to_id=task_data["assigned_to"],
                    created_by_id=task_data["created_by"],
                    created_at=now - timedelta(days=random.randint(0, 30))
                )
                session.add(task)
                created_count += 1
                print(f"✅ Created: {task_data['title']} ({task_data['status'].value})")
            except Exception as e:
                print(f"❌ Error creating task {task_data['title']}: {e}")
                continue

        session.commit()
        print(f"\n📊 Created {created_count} test tasks")

        # Summary
        print("\n📋 Task Summary:")
        print(f"  Личные задачи: {len(personal_tasks)}")
        print(f"  Назначенные мне: {len(assigned_to_me)}")
        print(f"  Назначенные мной: {len(assigned_by_me)}")
        print(f"  С отставанием: {len(overdue_tasks)}")
        print(f"  Выполненные: {len(completed_tasks)}")

    except Exception as e:
        print(f"❌ Error: {e}")
        session.rollback()
    finally:
        session.close()

if __name__ == '__main__':
    create_test_tasks()