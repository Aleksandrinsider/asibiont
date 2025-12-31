import os
from dotenv import load_dotenv

load_dotenv()

# App settings first
LOCAL = os.getenv("LOCAL", "0") == "1"
PORT = int(os.getenv("PORT", 8000))
FREE_ACCESS_MODE = os.getenv("FREE_ACCESS_MODE", "False").lower() in ("true", "1", "yes")
CURRENT_DATE = os.getenv("CURRENT_DATE")

# Database
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL and not LOCAL:
    raise ValueError("DATABASE_URL is required")
if LOCAL and not DATABASE_URL:
    DATABASE_URL = "sqlite:///local.db"

# AI
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
if not DEEPSEEK_API_KEY:
    raise ValueError("DEEPSEEK_API_KEY is required")

# Redis
REDIS_URL = os.getenv("REDIS_URL")
if not REDIS_URL and not LOCAL:
    raise ValueError("REDIS_URL is required")
if LOCAL and not REDIS_URL:
    REDIS_URL = "redis://localhost:6379"

# Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN is required")

TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME")
if not TELEGRAM_BOT_USERNAME:
    raise ValueError("TELEGRAM_BOT_USERNAME is required")

WEBHOOK_URL = os.getenv("WEBHOOK_URL")
if not WEBHOOK_URL and not LOCAL:
    raise ValueError("WEBHOOK_URL is required")
if LOCAL and not WEBHOOK_URL:
    WEBHOOK_URL = "http://localhost:8000"

WEB_APP_URL = os.getenv("WEB_APP_URL", "https://yourapp.railway.app")

# Payments
YOOKASSA_WEBHOOK_URL = os.getenv("YOOKASSA_WEBHOOK_URL")
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY")

# Security
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
if not ENCRYPTION_KEY:
    if LOCAL:
        # Generate a default key for local development
        from cryptography.fernet import Fernet
        ENCRYPTION_KEY = Fernet.generate_key().decode()
    else:
        raise ValueError("ENCRYPTION_KEY is required")

SESSION_SECRET = os.getenv("SESSION_SECRET")
if not SESSION_SECRET:
    if LOCAL:
        SESSION_SECRET = "local_dev_secret_insecure"
    else:
        raise ValueError("SESSION_SECRET is required for production")
