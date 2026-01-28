import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

# App settings first
PORT = int(os.getenv("PORT", 8080))
LOCAL = os.getenv("LOCAL", "False").lower() in ("true", "1", "yes")  # Allow override for local testing
FREE_ACCESS_MODE = LOCAL or os.getenv("FREE_ACCESS_MODE", "False").lower() in ("true", "1", "yes")  # Free access in local mode
USE_OPTIMIZED_PROMPT = os.getenv("USE_OPTIMIZED_PROMPT", "True").lower() in ("true", "1", "yes")
CURRENT_DATE_STR = os.getenv("CURRENT_DATE")
if CURRENT_DATE_STR:
    CURRENT_DATE = datetime.fromisoformat(CURRENT_DATE_STR)
else:
    CURRENT_DATE = datetime.now()

# Database
if LOCAL:
    db_path = os.path.join(os.path.dirname(__file__), "local.db")
    DATABASE_URL = f"sqlite:///{db_path}"  # Use SQLite for local development with absolute path
else:
    DATABASE_URL = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")
    if not DATABASE_URL:
        raise ValueError("DATABASE_PUBLIC_URL or DATABASE_URL is required in .env file")

# AI Model Configuration
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")  # Fast chat model for production
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
if not DEEPSEEK_API_KEY:
    raise ValueError("DEEPSEEK_API_KEY is required")

# AI Optimization Settings
AI_CACHE_ENABLED = os.getenv("AI_CACHE_ENABLED", "False").lower() in ("true", "1", "yes")
AI_MAX_TOKENS_RESPONSE = int(os.getenv("AI_MAX_TOKENS_RESPONSE", "1000"))
AI_MAX_TOKENS_ANALYSIS = int(os.getenv("AI_MAX_TOKENS_ANALYSIS", "500"))
AI_TEMPERATURE_LOW = float(os.getenv("AI_TEMPERATURE_LOW", "0.1"))  # For factual tasks
AI_TEMPERATURE_HIGH = float(os.getenv("AI_TEMPERATURE_HIGH", "0.7"))  # For creative tasks

# Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN and not LOCAL:
    raise ValueError("TELEGRAM_TOKEN is required in production mode")

TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "Asibiont_bot")
if not TELEGRAM_BOT_USERNAME:
    TELEGRAM_BOT_USERNAME = "Asibiont_bot"

# Developer notifications
# Set DEVELOPER_CHAT_ID to your Telegram user ID to receive error notifications
# You can find your user ID by messaging @userinfobot in Telegram
DEVELOPER_CHAT_ID = os.getenv("DEVELOPER_CHAT_ID", "123456789")  # Replace with actual developer telegram_id

# WEBHOOK_URL теперь хардкодится в main.py для Railway subdomain
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://task-production-1d10.up.railway.app/webhook")

WEB_APP_URL = os.getenv("WEB_APP_URL", "https://asibiont.ru")

# Payments
YOOKASSA_WEBHOOK_URL = os.getenv("YOOKASSA_WEBHOOK_URL")
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY")

# Validate Yookassa configuration if not in local mode
if not LOCAL and (not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET_KEY):
    raise ValueError("YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY are required in production mode")

# Security - no encryption needed (backward compatibility)
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "dummy_key_not_used")  # Legacy compatibility
SESSION_SECRET = os.getenv("SESSION_SECRET")
if not SESSION_SECRET:
    raise ValueError("SESSION_SECRET is required for production")

# Reminder settings
DAILY_REPORT_HOUR = int(os.getenv("DAILY_REPORT_HOUR", 22))
PROACTIVE_CHECK_INTERVAL_MINUTES = int(os.getenv("PROACTIVE_CHECK_INTERVAL_MINUTES", 120))  # Каждые 2 часа вместо 30 минут
OVERDUE_CHECK_INTERVAL_MINUTES = int(os.getenv("OVERDUE_CHECK_INTERVAL_MINUTES", 60))  # Каждый час вместо 15 минут
PROACTIVE_CHECK_AHEAD_MINUTES = int(os.getenv("PROACTIVE_CHECK_AHEAD_MINUTES", 60))
LAST_INTERACTION_THRESHOLD_MINUTES = int(os.getenv("LAST_INTERACTION_THRESHOLD_MINUTES", 15))
DEFAULT_TASK_REMINDER_HOURS = int(os.getenv("DEFAULT_TASK_REMINDER_HOURS", 1))

# Proactive messaging restrictions
PROACTIVE_NO_SEND_START_HOUR = int(os.getenv("PROACTIVE_NO_SEND_START_HOUR", 22))  # Start hour for no-send period (22:00)
PROACTIVE_SEND_START_HOUR = int(os.getenv("PROACTIVE_SEND_START_HOUR", 10))  # Start hour for send period (10:00)
PROACTIVE_NO_SEND_END_HOUR = int(os.getenv("PROACTIVE_NO_SEND_END_HOUR", 10))    # End hour for no-send period (10:00)
PROACTIVE_CHECK_INTERVAL_WITH_TASKS_MINUTES = int(os.getenv("PROACTIVE_CHECK_INTERVAL_WITH_TASKS_MINUTES", 360))  # Каждые 6 часов если есть задачи (было 180)
PROACTIVE_CHECK_INTERVAL_NO_TASKS_MINUTES = int(os.getenv("PROACTIVE_CHECK_INTERVAL_NO_TASKS_MINUTES", 360))  # Каждые 6 часов если нет задач (только днем)

# Subscription descriptions
PREMIUM_DESCRIPTION = """
🤖 ASI Biont - ИИ-агент для управления задачами

Я ваш персональный ИИ-ассистент для эффективного управления задачами и временем.

✨ Возможности:
• Создание и управление задачами через естественный язык
• Умные напоминания и дедлайны
• Делегирование задач партнерам
• Анализ прогресса и рекомендации
• Интеграция с календарем и уведомлениями

Для доступа ко всем функциям нужна активная подписка.
Выберите тариф в веб-приложении или используйте команду /subscription
"""
