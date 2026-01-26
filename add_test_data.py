import os
os.environ['LOCAL'] = '1'

from models import Session, User, Task, Post
from datetime import datetime, timedelta, timezone
import random

# Add diverse tasks and posts for test users
session = Session()
try:
    # Get all test users
    test_users = session.query(User).filter(User.telegram_id.between(1001, 1020)).all()
    print(f"Found {len(test_users)} test users")

    # Sample tasks data
    task_templates = [
        # Regular tasks
        ("Пробежка в парке", "Утренний бег 5 км", "completed", -2, None),
        ("Позвонить маме", "Проверить как дела", "pending", 1, None),
        ("Подготовить отчет", "Ежемесячный отчет по продажам", "completed", -1, None),
        ("Купить продукты", "Молоко, хлеб, овощи", "pending", 0, None),
        ("Изучить Python", "Пройти курс по основам Python", "pending", 7, None),
        ("Почистить квартиру", "Уборка всех помещений", "completed", -3, None),
        ("Встретиться с друзьями", "Вечерняя встреча в кафе", "pending", 2, None),
        ("Почитать книгу", "Глава из книги по психологии", "completed", -1, None),
        ("Сделать зарядку", "Утренний комплекс упражнений", "pending", 0, None),
        ("Написать статью", "Статья о продуктивности", "pending", 5, None),

        # Delegated tasks
        ("Подготовить презентацию", "Слайды для совещания", "pending", 3, "test2"),
        ("Заказать обед", "Бизнес-ланч на 5 человек", "completed", -1, "test3"),
        ("Проверить код", "Code review для нового модуля", "pending", 1, "test4"),
        ("Организовать встречу", "Совещание команды разработчиков", "completed", -2, "test5"),
        ("Составить смету", "Расчет стоимости проекта", "pending", 4, "test6"),
    ]

    # Post templates
    post_templates = [
        "Сегодня отличный день для спорта! Кто со мной на пробежку?",
        "Закончил читать интересную книгу по продуктивности. Рекомендую всем!",
        "Работаю над новым проектом. Вдохновение приходит в самые неожиданные моменты.",
        "Вчера был на конференции по технологиям. Много нового узнал!",
        "Делюсь рецептом полезного салата. Здоровое питание - залог энергии!",
        "Закончил марафон по изучению Python. Горжусь собой!",
        "Планирую поездку на выходных. Люблю активный отдых.",
        "Сегодня день продуктивности! Уже выполнил 5 задач из списка.",
        "Рекомендую всем заняться йогой. Отличная практика для тела и души.",
        "Работаю над улучшением своих навыков в программировании.",
        "Вчера был в спортзале. Чувствую себя отлично!",
        "Делюсь мыслями о важности планирования в жизни.",
        "Сегодня прекрасная погода для прогулки на свежем воздухе.",
        "Закончил проект, над которым работал несколько недель.",
        "Вдохновляюсь историями успеха других людей.",
        "Сегодня день саморазвития. Читаю, учусь, расту.",
        "Люблю сочетать работу с хобби. Нашел баланс!",
        "Делюсь советом: начните утро с зарядки - и день пройдет продуктивнее.",
        "Работаю над новым рецептом здорового питания.",
        "Вчера встретился с друзьями. Отличный вечер!",
    ]

    tasks_created = 0
    posts_created = 0

    for user in test_users:
        # Add 3-5 random tasks per user
        num_tasks = random.randint(3, 5)
        selected_tasks = random.sample(task_templates, num_tasks)

        for task_data in selected_tasks:
            title, description, status, days_offset, delegated_to = task_data

            # Calculate dates
            now = datetime.now(timezone.utc)
            if status == "completed":
                reminder_time = now + timedelta(days=days_offset, hours=random.randint(8, 18))
                actual_completion = reminder_time + timedelta(hours=random.randint(1, 24))
            else:
                reminder_time = now + timedelta(days=days_offset, hours=random.randint(8, 18))
                actual_completion = None

            # Create task
            task = Task(
                user_id=user.id,
                title=title,
                description=description,
                status=status,
                reminder_time=reminder_time,
                actual_completion_time=actual_completion if status == "completed" else None,
                delegated_to_username=delegated_to,
                delegation_status="pending" if delegated_to else None
            )
            session.add(task)
            tasks_created += 1

        # Add 1-2 posts per user
        num_posts = random.randint(1, 2)
        selected_posts = random.sample(post_templates, num_posts)

        for post_content in selected_posts:
            post = Post(
                user_id=user.id,
                username=user.username,
                content=post_content
            )
            session.add(post)
            posts_created += 1

    session.commit()
    print(f"Created {tasks_created} tasks and {posts_created} posts for test users")

except Exception as e:
    print(f"Error: {e}")
    session.rollback()
finally:
    session.close()