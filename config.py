import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

# App settings first
PORT = int(os.getenv("PORT", 8000))
LOCAL = os.getenv("LOCAL", "False").lower() in ("true", "1", "yes")
FREE_ACCESS_MODE = os.getenv("FREE_ACCESS_MODE", "False").lower() in ("true", "1", "yes")
CURRENT_DATE_STR = os.getenv("CURRENT_DATE")
if CURRENT_DATE_STR:
    CURRENT_DATE = datetime.fromisoformat(CURRENT_DATE_STR)
else:
    CURRENT_DATE = datetime.now()
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "your-secret-key-change-this")

# Database
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL is required")

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

TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "Asibiont_bot")
if not TELEGRAM_BOT_USERNAME:
    TELEGRAM_BOT_USERNAME = "Asibiont_bot"

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

SESSION_SECRET = os.getenv("SESSION_SECRET")
if not SESSION_SECRET:
    raise ValueError("SESSION_SECRET is required for production")
