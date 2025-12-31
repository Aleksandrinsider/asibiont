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
WEB_APP_URL = os.getenv("WEB_APP_URL", "https://yourapp.railway.app")

# Payments
YOOKASSA_WEBHOOK_URL = os.getenv("YOOKASSA_WEBHOOK_URL")
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY")

# Security
ENCRYPTION_KEY = env.str("ENCRYPTION_KEY")
SESSION_SECRET = os.getenv("SESSION_SECRET", "default_secret_change_in_prod")

# App settings
PORT = int(os.getenv("PORT", 8000))
FREE_ACCESS_MODE = os.getenv("FREE_ACCESS_MODE", "False").lower() in ("true", "1", "yes")
CURRENT_DATE = os.getenv("CURRENT_DATE")
LOCAL = os.getenv("LOCAL", "0") == "1"

# Handle LOCAL database
if LOCAL and not DATABASE_URL:
    DATABASE_URL = "sqlite:///local.db"
