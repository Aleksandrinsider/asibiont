import os
os.environ['LOCAL'] = '1'

from ai_integration.handlers import get_partners_list
from models import User, UserProfile, Subscription, SessionLocal

session = SessionLocal()

# Проверяем для user 1 (test1, LIGHT, спорт)
user = session.query(User).filter_by(id=1).first()
print(f"Testing for User {user.id} (@{user.username})")

partners = get_partners_list(1, session)
print(f"\nНайдено партнеров: {len(partners)}")

for p in partners:
    partner_user = session.query(User).filter_by(id=p.user_id).first()
    print(f"\n--- Partner: @{partner_user.username if partner_user else 'unknown'} ---")
    print(f"  user_id: {p.user_id}")
    print(f"  interests: {p.interests}")
    print(f"  common_interests: {p.common_interests}")
    print(f"  common_skills: {p.common_skills}")
    print(f"  common_goals: {p.common_goals}")
    print(f"  common_tasks: {getattr(p, 'common_tasks', 'N/A')}")
    print(f"  city: {p.city}")
    print(f"  average_rating: {getattr(p, 'average_rating', 0)}")
    print(f"  contact_info: {p.contact_info}")

session.close()
