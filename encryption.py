"""
Модуль для шифрования чувствительных данных пользователя
"""
from cryptography.fernet import Fernet
from config import Config
import base64
import hashlib
import logging

logger = logging.getLogger(__name__)


def get_encryption_key() -> bytes:
    """
    Генерирует ключ шифрования на основе ENCRYPTION_KEY из конфига
    """
    # Используем ENCRYPTION_KEY для шифрования
    secret = Config.ENCRYPTION_KEY
    if not secret:
        raise ValueError("ENCRYPTION_KEY is not set")
    
    # Если ключ уже в правильном формате, используем его
    try:
        return secret.encode()
    except:
        # Если не, генерируем из него
        key = hashlib.sha256(secret.encode()).digest()
        return base64.urlsafe_b64encode(key)


def encrypt_text(plain_text: str) -> str:
    """
    Шифрует текст
    
    Args:
        plain_text: Текст для шифрования
        
    Returns:
        Зашифрованный текст в виде строки
    """
    if not plain_text:
        return ""
    
    try:
        key = get_encryption_key()
        f = Fernet(key)
        encrypted = f.encrypt(plain_text.encode())
        return encrypted.decode()
    except Exception as e:
        logger.error(f"Encryption error: {e}")
        # В случае ошибки возвращаем исходный текст (не идеально, но не ломаем функционал)
        return plain_text


def decrypt_text(encrypted_text: str) -> str:
    """
    Расшифровывает текст
    
    Args:
        encrypted_text: Зашифрованный текст
        
    Returns:
        Расшифрованный текст
    """
    if not encrypted_text:
        return ""
    
    try:
        key = get_encryption_key()
        f = Fernet(key)
        decrypted = f.decrypt(encrypted_text.encode())
        return decrypted.decode()
    except Exception as e:
        logger.error(f"Decryption error: {e}")
        # Если не удалось расшифровать - возможно, это нешифрованные данные
        return encrypted_text
