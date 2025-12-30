from models import Session, Subscription

def update_subscription_status(user_id, new_status):
    db = Session()
    try:
        sub = db.query(Subscription).filter_by(user_id=user_id).first()
        if sub:
            sub.status = new_status
            db.commit()
            print(f"Subscription for user {user_id} updated to '{new_status}' successfully.")
        else:
            print(f"No subscription found for user {user_id}.")
    except Exception as e:
        print(f"Error updating subscription: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    update_subscription_status(1, 'inactive')