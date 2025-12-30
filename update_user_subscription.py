from models import Session, Subscription

def update_subscription_status(user_id, new_status):
    session = Session()
    try:
        # Найти подписку для пользователя с заданным user_id
        subscription = session.query(Subscription).filter_by(user_id=user_id).first()
        if subscription:
            subscription.status = new_status
            session.commit()
            print(f"Статус подписки для пользователя с ID {user_id} успешно изменен на '{new_status}'.")
        else:
            print(f"Подписка для пользователя с ID {user_id} не найдена.")
    except Exception as e:
        session.rollback()
        print(f"Ошибка при обновлении подписки: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    # Изменить статус подписки пользователя с ID=1 на 'active'
    update_subscription_status(1, 'active')