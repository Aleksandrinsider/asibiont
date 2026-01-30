import logging
logging.basicConfig(level=logging.DEBUG, format='%(levelname)s - %(message)s')

from test_contacts_recommendation import *

print("Создаем тестовую БД...")
Base.metadata.create_all(engine)

print("Создаем тестовые данные...")
setup_test_data()

print("\n" + "="*80)
print("Проверяем get_partners_list для user_id=100")
print("="*80)

session = Session()
from ai_integration.handlers import get_partners_list, find_relevant_contacts_for_task

user = session.query(User).filter_by(telegram_id=100).first()
partners = get_partners_list(user_id=user.id, session=session)
print(f"\nOK get_partners_list returned {len(partners)} partners:")
for idx, p in enumerate(partners, 1):
    partner_user = session.query(User).filter_by(id=p.user_id).first()
    print(f"  {idx}. @{partner_user.username} - interests: {p.interests}, skills: {p.skills}")

print("\n" + "="*80)
print("Проверяем find_relevant_contacts_for_task для 'иду на пробежку'")
print("="*80)

result = find_relevant_contacts_for_task('иду на пробежку в парк', user_id=100, session=session)
print(f"\n📊 РЕЗУЛЬТАТ:\n{result}")

session.close()
