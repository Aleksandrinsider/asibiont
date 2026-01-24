# Memory management functions: no encryption

import logging

logger = logging.getLogger(__name__)


def encrypt_data(data):
    """Return data as is (no encryption)"""
    return data


def decrypt_data(data):
    """Return data as is (no decryption)"""
    return data
    return data


def update_user_memory(info, user_id=None):
    """Update user memory with new information"""
    from models import Session, User

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if user:
            # Decrypt existing memory
            existing_decrypted = ""
            if user.memory:
                try:
                    existing_decrypted = decrypt_data(user.memory)
                except Exception:
                    existing_decrypted = ""
            # Add new information
            if existing_decrypted:
                existing_decrypted += "\n" + info
            else:
                existing_decrypted = info
            # Encrypt and save
            encrypted = encrypt_data(existing_decrypted)
            user.memory = encrypted
            session.commit()
            result = "Сохранена информация."
        else:
            result = "Пользователь не найден."
        return result
    finally:
        session.close()
