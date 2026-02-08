"""
Просмотр всех пользователей в БД
"""
from models import Session, User
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

db = Session()
users = db.query(User).all()

logger.info(f"\n📊 Всего пользователей: {len(users)}\n")

for i, user in enumerate(users, 1):
    logger.info(f"{i}. Username: {user.username or 'НЕТ'}")
    logger.info(f"   First name: {user.first_name or 'НЕТ'}")
    logger.info(f"   Telegram ID: {user.telegram_id}")
    logger.info(f"   Photo URL: {'ЕСТЬ' if user.photo_url else 'НЕТ'}")
    logger.info("")

db.close()
