"""
Скрипт для полного удаления пользователя @sportfan3 из продакшн базы данных
"""
from models import Session, User, UserProfile, Task, Subscription, PaymentHistory, Contact
from sqlalchemy import or_

session = Session()

try:
    print("=== Удаление @sportfan3 из базы данных ===")
    
    # Находим пользователя
    user = session.query(User).filter(or_(
        User.username == 'sportfan3',
        User.telegram_id == 333333
    )).first()
    
    if not user:
        print("❌ Пользователь @sportfan3 не найден")
        session.close()
        exit(0)
    
    user_id = user.id
    username = user.username
    print(f"✓ Найден пользователь: {username} (ID={user_id}, telegram_id={user.telegram_id})")
    
    # Удаляем все связанные записи
    deleted_counts = {}
    
    # 1. Удаляем профиль
    profile_count = session.query(UserProfile).filter(UserProfile.user_id == user_id).delete()
    deleted_counts['profiles'] = profile_count
    
    # 2. Удаляем задачи (созданные пользователем)
    tasks_created = session.query(Task).filter(Task.user_id == user_id).delete()
    deleted_counts['tasks_created'] = tasks_created
    
    # 3. Удаляем задачи (делегированные пользователю)
    tasks_delegated = session.query(Task).filter(Task.delegated_to == user_id).count()
    if tasks_delegated > 0:
        # Обнуляем делегирование
        session.query(Task).filter(Task.delegated_to == user_id).update({'delegated_to': None})
    deleted_counts['tasks_delegated'] = tasks_delegated
    
    # 4. Удаляем подписки
    subscriptions_count = session.query(Subscription).filter(Subscription.user_id == user_id).delete()
    deleted_counts['subscriptions'] = subscriptions_count
    
    # 5. Удаляем историю платежей
    payments_count = session.query(PaymentHistory).filter(PaymentHistory.user_id == user_id).delete()
    deleted_counts['payment_history'] = payments_count
    
    # 6. Удаляем контакты (где пользователь - владелец)
    contacts_owner = session.query(Contact).filter(Contact.user_id == user_id).delete()
    deleted_counts['contacts_as_owner'] = contacts_owner
    
    # 7. Удаляем контакты (где пользователь - контакт)
    contacts_partner = session.query(Contact).filter(Contact.partner_id == user_id).delete()
    deleted_counts['contacts_as_partner'] = contacts_partner
    
    # 8. Удаляем самого пользователя
    session.delete(user)
    
    # Коммитим все изменения
    session.commit()
    
    print("\n✅ Пользователь @sportfan3 полностью удален!")
    print("\nУдалено записей:")
    for table, count in deleted_counts.items():
        if count > 0:
            print(f"  - {table}: {count}")
    
except Exception as e:
    print(f"\n❌ Ошибка при удалении: {e}")
    session.rollback()
finally:
    session.close()
    print("\nГотово!")
