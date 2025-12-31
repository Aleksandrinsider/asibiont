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
if not REDIS_URL:
    raise ValueError("REDIS_URL is required")

# Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN is required")

TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME")
if not TELEGRAM_BOT_USERNAME:
    raise ValueError("TELEGRAM_BOT_USERNAME is required")

WEBHOOK_URL = os.getenv("WEBHOOK_URL")
if not WEBHOOK_URL:
    raise ValueError("WEBHOOK_URL is required")

WEB_APP_URL = os.getenv("WEB_APP_URL", "https://yourapp.railway.app")

# Payments
YOOKASSA_WEBHOOK_URL = os.getenv("YOOKASSA_WEBHOOK_URL")
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY")

# Security
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
if not ENCRYPTION_KEY:
    raise ValueError("ENCRYPTION_KEY is required")

SESSION_SECRET = os.getenv("SESSION_SECRET", "default_secret_change_in_prod")
