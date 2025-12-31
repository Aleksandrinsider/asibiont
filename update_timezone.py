from models import Session, User
import sys

# Обновить timezone для пользователя
user_id = 146333757
timezone = 'Europe/Moscow'  # Или другой timezone

session = Session()
try:
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if user:
        user.timezone = timezone
        session.commit()
        print(f"Timezone для пользователя {user.username} обновлен на {timezone}")
    else:
        print(f"Пользователь с ID {user_id} не найден")
except Exception as e:
    print(f"Ошибка: {e}")
finally:
    session.close()