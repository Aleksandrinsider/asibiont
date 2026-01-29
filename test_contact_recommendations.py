"""Тест автоматических рекомендаций контактов агентом"""
import os
os.environ['LOCAL'] = '1'

from models import Session, User
from ai_integration.handlers import find_relevant_contacts_for_task

session = Session()

print("="*70)
print("ТЕСТ: Поиск релевантных контактов для задач")
print("="*70)

test_user = session.query(User).filter_by(username='test_user').first()
if not test_user:
    print("❌ test_user не найден")
    session.close()
    exit()

test_cases = [
    "пойти на пробежку завтра утром",
    "сходить в тренажерный зал",
    "обсудить новый стартап",
    "позаниматься йогой",
    "поиграть в футбол в выходные",
    "найти партнера для бизнеса",
]

for i, task_desc in enumerate(test_cases, 1):
    print(f"\n{i}. Задача: '{task_desc}'")
    print("-"*70)
    
    result = find_relevant_contacts_for_task(
        task_description=task_desc,
        user_id=test_user.telegram_id,
        limit=3,
        session=session
    )
    
    print(result)
    print()

session.close()
print("="*70)
print("✓ Тестирование завершено")
