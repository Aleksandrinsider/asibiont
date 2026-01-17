import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

# App settings first
PORT = int(os.getenv("PORT", 8080))
LOCAL = os.getenv("LOCAL", "False").lower() in ("true", "1", "yes")  # Allow override for local testing
FREE_ACCESS_MODE = os.getenv("FREE_ACCESS_MODE", "False").lower() in ("true", "1", "yes")
USE_OPTIMIZED_PROMPT = os.getenv("USE_OPTIMIZED_PROMPT", "True").lower() in ("true", "1", "yes")
CURRENT_DATE_STR = os.getenv("CURRENT_DATE")
if CURRENT_DATE_STR:
    CURRENT_DATE = datetime.fromisoformat(CURRENT_DATE_STR)
else:
    CURRENT_DATE = datetime.now()
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "your-secret-key-change-this")

# Database
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL is required in .env file")

# AI Model Configuration
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-reasoner")  # V3.2 reasoning model for agents
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
if not DEEPSEEK_API_KEY:
    raise ValueError("DEEPSEEK_API_KEY is required")

# AI Optimization Settings
AI_CACHE_ENABLED = os.getenv("AI_CACHE_ENABLED", "False").lower() in ("true", "1", "yes")
AI_MAX_TOKENS_RESPONSE = int(os.getenv("AI_MAX_TOKENS_RESPONSE", "1000"))
AI_MAX_TOKENS_ANALYSIS = int(os.getenv("AI_MAX_TOKENS_ANALYSIS", "500"))
AI_TEMPERATURE_LOW = float(os.getenv("AI_TEMPERATURE_LOW", "0.1"))  # For factual tasks
AI_TEMPERATURE_HIGH = float(os.getenv("AI_TEMPERATURE_HIGH", "0.7"))  # For creative tasks

# Redis
REDIS_URL = os.getenv("REDIS_URL")
if not REDIS_URL and not LOCAL:
    raise ValueError("REDIS_URL is required in production mode")

# Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN is required")

TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "Asibiont_bot")
if not TELEGRAM_BOT_USERNAME:
    TELEGRAM_BOT_USERNAME = "Asibiont_bot"

# WEBHOOK_URL теперь хардкодится в main.py для Railway subdomain
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://task-production-1d10.up.railway.app/webhook")

WEB_APP_URL = os.getenv("WEB_APP_URL", "http://asibiont.ru")

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

# Reminder settings
DAILY_REPORT_HOUR = int(os.getenv("DAILY_REPORT_HOUR", 22))
PROACTIVE_CHECK_INTERVAL_MINUTES = int(os.getenv("PROACTIVE_CHECK_INTERVAL_MINUTES", 30))
OVERDUE_CHECK_INTERVAL_MINUTES = int(os.getenv("OVERDUE_CHECK_INTERVAL_MINUTES", 15))
PROACTIVE_CHECK_AHEAD_MINUTES = int(os.getenv("PROACTIVE_CHECK_AHEAD_MINUTES", 60))
LAST_INTERACTION_THRESHOLD_MINUTES = int(os.getenv("LAST_INTERACTION_THRESHOLD_MINUTES", 15))
DEFAULT_TASK_REMINDER_HOURS = int(os.getenv("DEFAULT_TASK_REMINDER_HOURS", 1))

# Proactive messaging restrictions
PROACTIVE_NO_SEND_START_HOUR = int(os.getenv("PROACTIVE_NO_SEND_START_HOUR", 22))  # Start hour for no-send period (22:00)
PROACTIVE_NO_SEND_END_HOUR = int(os.getenv("PROACTIVE_NO_SEND_END_HOUR", 10))    # End hour for no-send period (10:00)
PROACTIVE_CHECK_INTERVAL_WITH_TASKS_MINUTES = int(os.getenv("PROACTIVE_CHECK_INTERVAL_WITH_TASKS_MINUTES", 30))  # Interval when user has tasks
PROACTIVE_CHECK_INTERVAL_NO_TASKS_MINUTES = int(os.getenv("PROACTIVE_CHECK_INTERVAL_NO_TASKS_MINUTES", 60))    # Interval when user has no tasks
