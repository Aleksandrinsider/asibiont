"""
Создание нескольких тестовых постов от @climber_alex
"""
from models import Session, User, Post
from datetime import datetime, timedelta

session = Session()

# Найти или создать пользователя @climber_alex
climber = session.query(User).filter_by(username='climber_alex').first()

if not climber:
    print("Пользователь @climber_alex не найден. Создаём...")
    climber = User(
        telegram_id=999999001,  # Тестовый ID
        username='climber_alex',
        first_name='Alex'
    )
    session.add(climber)
    session.commit()
    print(f"Создан пользователь: @{climber.username} (ID: {climber.id})")
else:
    print(f"Найден пользователь: @{climber.username} (ID: {climber.id})")

# Создать несколько тестовых постов
posts_content = [
    "Только что завершил восхождение на Эльбрус! Невероятные впечатления, погода была идеальная. Кто планирует туда в этом году? 🏔️",
    "Важное напоминание: всегда проверяйте прогноз погоды перед выходом в горы. Вчера спасли группу, которая попала в грозу на высоте.",
    "Кто знает хорошие маршруты для скалолазания в Крыму? Планирую поездку на следующей неделе.",
    "Новый маршрут на Казбек открыт! Сложность 5B, занимает 2 дня. Кто со мной?",
]

created_posts = []
for i, content in enumerate(posts_content):
    # Создаём посты с разницей во времени
    created_at = datetime.now() - timedelta(minutes=(len(posts_content) - i - 1) * 15)
    
    post = Post(
        user_id=climber.id,
        content=content,
        created_at=created_at
    )
    session.add(post)
    created_posts.append(post)

session.commit()

print(f"\nСоздано {len(created_posts)} постов:")
for post in created_posts:
    print(f"- ID {post.id}: {post.content[:50]}... ({post.created_at.strftime('%H:%M')})")

session.close()
print("\nГотово!")
