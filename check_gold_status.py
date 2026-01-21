"""
Проверка статуса Gold пользователей и делегированных задач
"""
import os
os.environ['LOCAL'] = '1'
from models import Base, User, Task, Session, SubscriptionTier

def check_status():
    session = Session()
    try:
        # Проверяем aleksandrinsider
        main_user = session.query(User).filter(User.username.ilike('aleksandrinsider')).first()
        
        if main_user:
            print(f"\n=== Пользователь aleksandrinsider ===")
            print(f"ID: {main_user.id}")
            print(f"Telegram ID: {main_user.telegram_id}")
            print(f"Username: {main_user.username}")
            print(f"Subscription Tier: {main_user.subscription_tier.value if main_user.subscription_tier else 'None'}")
        else:
            print("\n❌ Пользователь aleksandrinsider не найден!")
            return
        
        # Проверяем других Gold пользователей
        gold_users = session.query(User).filter(
            User.subscription_tier == SubscriptionTier.GOLD,
            User.id != main_user.id
        ).all()
        
        print(f"\n=== Другие Gold пользователи ===")
        if gold_users:
            for u in gold_users:
                print(f"  - @{u.username}: tier={u.subscription_tier.value}")
        else:
            print("  ❌ Нет других Gold пользователей!")
        
        # Проверяем делегированные задачи
        delegated_tasks = session.query(Task).filter(
            Task.delegated_to_username.isnot(None),
            Task.delegated_to_username.ilike('aleksandrinsider')
        ).all()
        
        print(f"\n=== Делегированные задачи для aleksandrinsider ===")
        if delegated_tasks:
            for task in delegated_tasks:
                delegator = session.query(User).filter_by(id=task.user_id).first()
                delegator_name = delegator.username if delegator and delegator.username else 'Unknown'
                
                print(f"\nЗадача #{task.id}: '{task.title}'")
                print(f"  От: @{delegator_name}")
                print(f"  Status: {task.status}")
                print(f"  Delegation Status: {task.delegation_status}")
                print(f"  Reminder Time: {task.reminder_time}")
        else:
            print("  ❌ Нет делегированных задач!")
            
        print("\n" + "="*60)
        print("\n✅ ВЫВОДЫ:")
        
        # Проверка 1: Gold пользователи для "Премиум статус"
        if main_user.subscription_tier and main_user.subscription_tier.value == 'GOLD':
            if gold_users:
                print("✅ У вас Gold тариф и есть другие Gold пользователи")
                print("   → Кнопка 'Премиум статус' должна показывать контакты")
            else:
                print("⚠️  У вас Gold тариф, но нет других Gold пользователей")
                print("   → Создайте тестовых Gold пользователей для проверки")
        else:
            print("❌ У вас не Gold тариф")
            print("   → Обновите тариф на Gold для доступа к 'Премиум статус'")
        
        # Проверка 2: Делегированные задачи
        pending_delegated = [t for t in delegated_tasks if t.delegation_status == 'pending']
        if pending_delegated:
            print(f"\n✅ Найдено {len(pending_delegated)} задач в ожидании подтверждения")
            print("   → Их статус будет отображаться как 'Ожидает подтверждения'")
        else:
            print("\n⚠️  Нет задач в ожидании подтверждения")
            
    finally:
        session.close()

if __name__ == '__main__':
    print("Проверка статуса...")
    check_status()
