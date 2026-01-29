import os
os.environ['LOCAL'] = '1'

from ai_integration.handlers import get_partners_list
from models import SessionLocal

session = SessionLocal()

# Проверяем для user 1 (test1, LIGHT, спорт)
print("Партнеры для User 1 (test1, LIGHT, спорт):")
partners = get_partners_list(1, session)
for p in partners:
    print(f"\n  Partner user_id={p.user_id}")
    print(f"    interests: {p.interests}")
    print(f"    common_interests: {p.common_interests}")
    print(f"    common_skills: {p.common_skills}")
    print(f"    common_goals: {p.common_goals}")

print("\n" + "="*50)
print("Партнеры для User 4 (test4, LIGHT, бизнес):")
partners = get_partners_list(4, session)
for p in partners:
    print(f"\n  Partner user_id={p.user_id}")
    print(f"    interests: {p.interests}")
    print(f"    common_interests: {p.common_interests}")
    print(f"    common_skills: {p.common_skills}")
    print(f"    common_goals: {p.common_goals}")

session.close()
