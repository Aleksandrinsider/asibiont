from models import Session, User, Post
from datetime import datetime
import pytz

session = Session()

# Найти test_sport_10
user = session.query(User).filter_by(username='test_sport_10').first()
if not user:
    print('User test_sport_10 not found')
    session.close()
    exit()

print(f'Found user: {user.username} (id: {user.id})')

# Создать 2 новых поста
moscow_tz = pytz.timezone('Europe/Moscow')
now = datetime.now(moscow_tz)

posts = [
    {
        'content': '🏃‍♂️ Сегодня утром пробежал 7 км за 38 минут! Темп улучшается, чувствую прилив энергии. Погода отличная, -5°C, снег хрустит под ногами. Кто еще бегает по утрам в Москве?',
        'created_at': now
    },
    {
        'content': '💪 План на неделю: понедельник и среда - бег 7-10 км, вторник и четверг - силовые в зале, пятница - плавание. Выходные - активный отдых на лыжах. Ищу компанию для лыжных прогулок в Подмосковье!',
        'created_at': now
    }
]

for post_data in posts:
    post = Post(
        user_id=user.id,
        content=post_data['content'],
        created_at=post_data['created_at']
    )
    session.add(post)
    print(f'Created post: {post_data["content"][:50]}...')

session.commit()
print(f'✅ Created {len(posts)} posts for {user.username}')
session.close()
