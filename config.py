import os
import logging
from dotenv import load_dotenv
from datetime import datetime, timezone

load_dotenv()

logger = logging.getLogger(__name__)

# Timezone settings
TIMEZONE = timezone.utc

# App settings first
PORT = int(os.getenv("PORT", 8080))
LOCAL = os.getenv("LOCAL", "0").lower() in ("true", "1", "yes")  # Production by default
FREE_ACCESS_MODE = os.getenv("FREE_ACCESS_MODE", "0").lower() in ("true", "1", "yes")  # For testing
USE_OPTIMIZED_PROMPT = os.getenv("USE_OPTIMIZED_PROMPT", "True").lower() in ("true", "1", "yes")
CURRENT_DATE_STR = os.getenv("CURRENT_DATE")
if CURRENT_DATE_STR:
    CURRENT_DATE = datetime.fromisoformat(CURRENT_DATE_STR)
else:
    CURRENT_DATE = None  # None = use real current time

# Database
if LOCAL:
    db_path = os.path.join(os.path.dirname(__file__), "local.db")
    DATABASE_URL = f"sqlite:///{db_path}"  # Use SQLite for local development with absolute path
else:
    # Railway internal network (postgres.railway.internal) is unreliable, prefer public URL
    DATABASE_URL = os.getenv("DATABASE_URL")
    DATABASE_PUBLIC_URL = os.getenv("DATABASE_PUBLIC_URL")
    
    # Use public URL if internal URL contains railway.internal (unreachable)
    if DATABASE_URL and "railway.internal" in DATABASE_URL and DATABASE_PUBLIC_URL:
        DATABASE_URL = DATABASE_PUBLIC_URL
    elif not DATABASE_URL:
        DATABASE_URL = DATABASE_PUBLIC_URL
    
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL or DATABASE_PUBLIC_URL is required in production mode")

# AI Model Configuration — DeepSeek V3 0324 (latest)
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")  # V3 latest (auto-updated by DeepSeek)
DEEPSEEK_REASONER_MODEL = os.getenv("DEEPSEEK_REASONER_MODEL", "deepseek-reasoner")  # R1 for deep reasoning
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
if not DEEPSEEK_API_KEY:
    raise ValueError("DEEPSEEK_API_KEY is required")

# Voice Transcription API keys (optional — used for Whisper-based voice recognition)
# Priority: Groq (free) → OpenAI (paid) → Google SR (fallback, may be unreliable)
GROQ_API_KEY = os.getenv("GROQ_API_KEY")   # Free Whisper via api.groq.com (recommended)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # OpenAI Whisper whisper-1 model

# Web Search API (Serper)
SERPER_API_KEY = os.getenv("SERPER_API_KEY")
if not SERPER_API_KEY and not LOCAL:
    logger.warning("SERPER_API_KEY not set - web search features will be limited")

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

TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "asibiont_bot")
if not TELEGRAM_BOT_USERNAME:
    TELEGRAM_BOT_USERNAME = "asibiont_bot"

# Replicate (Image Generation)
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN")
if not REPLICATE_API_TOKEN and not LOCAL:
    logger.warning("REPLICATE_API_TOKEN not set - image generation will be unavailable")

# Discord
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_ENABLED = bool(DISCORD_BOT_TOKEN)
if not DISCORD_ENABLED and not LOCAL:
    logger.warning("Discord not configured — set DISCORD_BOT_TOKEN to enable")

# Developer notifications
# Set DEVELOPER_CHAT_ID to your Telegram user ID to receive error notifications
# You can find your user ID by messaging @userinfobot in Telegram
DEVELOPER_CHAT_ID = os.getenv("DEVELOPER_CHAT_ID", "")  # Set your Telegram user ID via env var

# WEBHOOK_URL теперь хардкодится в main.py для Railway subdomain
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://asibiont.com/webhook")

WEB_APP_URL = os.getenv("WEB_APP_URL", "https://asibiont.com")

# Payments
YOOKASSA_WEBHOOK_URL = os.getenv("YOOKASSA_WEBHOOK_URL")
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY")

# NowPayments (crypto — international)
NOWPAYMENTS_API_KEY = os.getenv("NOWPAYMENTS_API_KEY")
NOWPAYMENTS_IPN_SECRET = os.getenv("NOWPAYMENTS_IPN_SECRET")

# Validate Yookassa configuration if not in local mode
if not LOCAL and (not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET_KEY):
    raise ValueError("YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY are required in production mode")

# Security
SESSION_SECRET = os.getenv("SESSION_SECRET")
if not SESSION_SECRET and not LOCAL:
    raise ValueError("SESSION_SECRET is required for production")
if not SESSION_SECRET:
    SESSION_SECRET = "local-dev-secret-key-not-for-production"

# Web Push (VAPID)
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
VAPID_EMAIL = os.getenv("VAPID_EMAIL", "mailto:admin@asibiont.com")

# SMTP (email sending) — Gmail with App Password
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 465))
SMTP_USER = os.getenv("SMTP_USER", "aleksandr.insider@gmail.com")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "ASI Biont <aleksandr.insider@gmail.com>")

# Resend.com HTTP Email API (fallback when SMTP ports are blocked)
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
# Full-access key for reading inbound/received emails (Resend Receiving API)
RESEND_RECEIVING_API_KEY = os.getenv("RESEND_RECEIVING_API_KEY", "") or RESEND_API_KEY

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

# Адаптивные интервалы проактивных сообщений (сокращённые):
# 0 задач: 2 часа (мотивация к планированию)
# 1-3 задачи: 2.5 часа
# 4-7 задач: 3 часа
# 8-12 задач: 3.5 часа
# 13+ задач: 4 часа
PROACTIVE_CHECK_INTERVAL_WITH_TASKS_MINUTES = int(os.getenv("PROACTIVE_CHECK_INTERVAL_WITH_TASKS_MINUTES", 180))  # 3ч базовый (fallback)
PROACTIVE_CHECK_INTERVAL_NO_TASKS_MINUTES = int(os.getenv("PROACTIVE_CHECK_INTERVAL_NO_TASKS_MINUTES", 120))  # 2ч если нет задач

# Subscription descriptions
PREMIUM_DESCRIPTION = """
🤖 ASI Biont — AI-ассистент полного цикла

Управляет задачами, находит партнёров, делегирует и автоматизирует. Проактивно предлагает связи, коллаборации и возможности роста.

✨ Возможности:
• Больше не забывайте важное — AI управляет задачами и напоминает вовремя
• Расширяйте профессиональную сеть — находите единомышленников для совместных целей
• Освободите время для главного — делегируйте рутину с AI-контролем
• Масштабируйте себя — AI выполняет задачи на автопилоте

Все функции открыты. Оплата токенами — 1 токен = 1 рубль.
Пополнить баланс: /buy
"""

# External APIs
OPENWEATHERMAP_API_KEY = os.getenv("OPENWEATHERMAP_API_KEY")
if not OPENWEATHERMAP_API_KEY:
    logger.warning("OPENWEATHERMAP_API_KEY not set - weather monitoring will not work")

NEWSAPI_API_KEY = os.getenv("NEWSAPI_API_KEY")
if not NEWSAPI_API_KEY:
    logger.warning("NEWSAPI_API_KEY not set - news integration will not work")

PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
if not PINECONE_API_KEY:
    logger.warning("PINECONE_API_KEY not set - vector memory will not work")

# Redis Configuration
REDIS_ENABLED = os.getenv("REDIS_ENABLED", "True").lower() in ("true", "1", "yes") and not LOCAL

if REDIS_ENABLED:
    # Check for Railway Redis variables first
    railway_host = os.getenv("REDIS_HOST")
    railway_port = os.getenv("REDIS_PORT")
    railway_password = os.getenv("REDIS_PASSWORD")

    if railway_host and railway_port and railway_password:
        # Railway Redis with individual variables
        REDIS_HOST = railway_host
        REDIS_PORT = int(railway_port)
        REDIS_USERNAME = ""  # Railway Redis doesn't use username
        REDIS_PASSWORD = railway_password
        logger.info("[CONFIG] Using Railway Redis (individual variables)")
    else:
        # Check for Railway Redis URL
        railway_redis_url = os.getenv("REDIS_URL") or os.getenv("RAILWAY_REDIS_URL")
        if railway_redis_url:
            # Parse Railway Redis URL: redis://username:password@host:port
            import re
            match = re.match(r'redis://([^:]+):([^@]+)@([^:]+):(\d+)', railway_redis_url)
            if match:
                REDIS_USERNAME, REDIS_PASSWORD, REDIS_HOST, REDIS_PORT = match.groups()
                REDIS_PORT = int(REDIS_PORT)
                logger.info("[CONFIG] Using Railway Redis (URL)")
            else:
                logger.warning("[CONFIG] Invalid Railway Redis URL format")
                REDIS_ENABLED = False
        else:
            # Use external Redis variables
            REDIS_HOST = os.getenv("REDIS_HOST", "")
            REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
            REDIS_USERNAME = os.getenv("REDIS_USERNAME", "default")
            REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
            logger.info("[CONFIG] Using external Redis")
else:
    REDIS_HOST = REDIS_PORT = REDIS_USERNAME = REDIS_PASSWORD = None
    logger.info("[CONFIG] Redis disabled")

# Redis client will be initialized in utils.py if enabled
