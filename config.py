import os
from dotenv import load_dotenv

load_dotenv()

if os.getenv("LOCAL") == "1":
    DATABASE_URL = "sqlite:///local.db"
else:
    DATABASE_URL = os.getenv("DATABASE_URL")
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL is not set")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
REDIS_URL = os.getenv("REDIS_URL")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
YOOKASSA_WEBHOOK_URL = os.getenv("YOOKASSA_WEBHOOK_URL")
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY")