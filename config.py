from environs import Env

env = Env()
env.read_env()

# Database
DATABASE_URL = env.str("DATABASE_URL")

# AI
DEEPSEEK_API_KEY = env.str("DEEPSEEK_API_KEY")

# Redis
REDIS_URL = env.str("REDIS_URL")

# Telegram
TELEGRAM_TOKEN = env.str("TELEGRAM_TOKEN")
TELEGRAM_BOT_USERNAME = env.str("TELEGRAM_BOT_USERNAME")
WEBHOOK_URL = env.str("WEBHOOK_URL")
WEB_APP_URL = env.str("WEB_APP_URL", default="https://yourapp.railway.app")

# Payments
YOOKASSA_WEBHOOK_URL = env.str("YOOKASSA_WEBHOOK_URL", default=None)
YOOKASSA_SHOP_ID = env.str("YOOKASSA_SHOP_ID", default=None)
YOOKASSA_SECRET_KEY = env.str("YOOKASSA_SECRET_KEY", default=None)

# Security
ENCRYPTION_KEY = env.str("ENCRYPTION_KEY")
SESSION_SECRET = env.str("SESSION_SECRET", default="default_secret_change_in_prod")

# App settings
PORT = env.int("PORT", default=8000)
FREE_ACCESS_MODE = env.bool("FREE_ACCESS_MODE", default=False)
CURRENT_DATE = env.str("CURRENT_DATE", default=None)
LOCAL = env.bool("LOCAL", default=False)

# Handle LOCAL database
if LOCAL and not DATABASE_URL:
    DATABASE_URL = "sqlite:///local.db"
