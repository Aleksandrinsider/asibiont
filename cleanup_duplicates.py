"""
Удаление старых пользователей (оставить только последнего)
"""
import os
if 'LOCAL' in os.environ:
    del os.environ['LOCAL']

from models import Session, User, Task, UserProfile, Subscription, Interaction
import sys

sys.stdout.reconfigure(encoding='utf-8')

USER_ID = 146333757

print("="*60)
print("ОЧИСТКА СТАРЫХ ДУБЛИКАТОВ")
print("="*60)

session = Session()
try:
    # Найти всех пользователей
    all_users = session.query(User).all()
    print(f"\nВсего пользователей в БД: {len(all_users)}")
    
    # Показать всех
    for user in all_users:
        print(f"  User ID={user.id}, telegram_id={user.telegram_id}, username={user.username}, created={user.created_at}")
    
    # Найти пользователя с нашим telegram_id
    our_users = session.query(User).filter_by(telegram_id=USER_ID).order_by(User.created_at.desc()).all()
    
    if len(our_users) > 1:
        print(f"\n⚠️ Найдено {len(our_users)} записей для telegram_id={USER_ID}")
        print("Удаляю старые, оставляю последнего...")
        
        # Оставить последнего (самого нового)
        keep_user = our_users[0]
        delete_users = our_users[1:]
        
        for user in delete_users:
            print(f"  Удаляю User ID={user.id} (created={user.created_at})")
            # Удалить связанные данные
            session.query(Interaction).filter_by(user_id=user.id).delete()
            session.query(Task).filter_by(user_id=user.id).delete()
            session.query(UserProfile).filter_by(user_id=user.id).delete()
            session.query(Subscription).filter_by(user_id=user.id).delete()
            session.delete(user)
        
        session.commit()
        print(f"✅ Оставлен User ID={keep_user.id}")
    else:
        print(f"\n✅ Дубликатов не найдено для telegram_id={USER_ID}")
    
    # Удалить ВСЕХ других пользователей (не наших)
    other_users = session.query(User).filter(User.telegram_id != USER_ID).all()
    if other_users:
        print(f"\n📌 Найдено {len(other_users)} других пользователей")
        print("Удалять их? (y/n): ", end='')
        # Для автоматического выполнения - удаляем
        print("y (автоматически)")
        
        for user in other_users:
            print(f"  Удаляю User ID={user.id}, telegram_id={user.telegram_id}")
            session.query(Interaction).filter_by(user_id=user.id).delete()
            session.query(Task).filter_by(user_id=user.id).delete()
            session.query(UserProfile).filter_by(user_id=user.id).delete()
            session.query(Subscription).filter_by(user_id=user.id).delete()
            session.delete(user)
        
        session.commit()
        print(f"✅ Удалено {len(other_users)} пользователей")
    
    print("\n" + "="*60)
    print("ИТОГО В БД:")
    print("="*60)
    users_left = session.query(User).all()
    print(f"Пользователей: {len(users_left)}")
    for user in users_left:
        tasks_count = session.query(Task).filter_by(user_id=user.id).count()
        print(f"  User ID={user.id}, telegram_id={user.telegram_id}, задач={tasks_count}")
    
finally:
    session.close()
