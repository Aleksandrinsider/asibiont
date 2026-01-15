from models import SessionLocal
from sqlalchemy import text
import os

def clear_all_data():
    db = SessionLocal()
    try:
        # Отключаем ограничения внешних ключей для PostgreSQL
        db.execute(text("SET session_replication_role = replica;"))
        db.commit()
        # Удаляем все данные из всех таблиц
        db.execute(text("DELETE FROM user_ratings"))
        db.execute(text("DELETE FROM interactions"))
        db.execute(text("DELETE FROM tasks"))
        db.execute(text("DELETE FROM user_profiles"))
        db.execute(text("DELETE FROM subscriptions"))
        db.execute(text("DELETE FROM users"))
        db.commit()
        db.execute(text("SET session_replication_role = DEFAULT;"))
        db.commit()
        print("All user and related data deleted from Railway DB.")
    except Exception as e:
        db.rollback()
        print(f"Error: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    clear_all_data()