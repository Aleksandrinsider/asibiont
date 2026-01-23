from models import Session, User, UserProfile, Post
import json

session = Session()

# Получаем пользователя
user = session.query(User).filter_by(username='aleksandrinsider').first()
profile = session.query(UserProfile).filter_by(user_id=user.id).first()

print(f'User ID: {user.id}')
print(f'Username: {user.username}')
print(f'Favorite contacts: {profile.favorite_contacts}')

# Получаем последние посты
posts = session.query(Post).order_by(Post.created_at.desc()).limit(10).all()
print(f'\nПоследние 10 постов:')
for p in posts:
    content_preview = p.content[:80].replace('\n', ' ') if len(p.content) > 80 else p.content.replace('\n', ' ')
    print(f'  ID={p.id}, user_id={p.user_id}, username={p.username}')
    print(f'    Содержание: {content_preview}...')

session.close()
