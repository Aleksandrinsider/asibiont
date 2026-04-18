import hashlib
import hmac
import json
import uuid
import logging
import aiohttp
from contextlib import asynccontextmanager
import asyncio

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _safe_http(**kwargs):
    session = aiohttp.ClientSession(**kwargs)
    try:
        yield session
    finally:
        await session.close()
        await asyncio.sleep(0)


NOWPAYMENTS_API_URL = "https://api.nowpayments.io/v1"

# USD prices for international token packs
CRYPTO_PACK_PRICES = {
    'small':  {'price_usd': 15,  'tokens': 1500},
    'medium': {'price_usd': 50,  'tokens': 5500},
    'large':  {'price_usd': 500, 'tokens': 60000},
}


async def create_crypto_payment(pack: str, user_id: int, api_key: str, web_app_url: str) -> str:
    """Create NowPayments invoice and return payment URL"""
    pack_info = CRYPTO_PACK_PRICES[pack]
    order_id = f"{user_id}_{pack}_{uuid.uuid4().hex[:8]}"

    payload = {
        "price_amount": pack_info['price_usd'],
        "price_currency": "usd",
        "pay_currency": "usdttrc20",
        "order_id": order_id,
        "order_description": f"ASI Biont — {pack_info['tokens']} tokens",
        "ipn_callback_url": f"{web_app_url}/webhook/nowpayments",
        "success_url": f"{web_app_url}/dashboard",
        "cancel_url": f"{web_app_url}/subscription-tiers",
    }

    async with _safe_http() as session:
        async with session.post(
            f"{NOWPAYMENTS_API_URL}/invoice",
            json=payload,
            headers={"x-api-key": api_key, "Content-Type": "application/json"}
        ) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise ValueError(f"NowPayments API error {resp.status}: {data}")
            invoice_url = data.get("invoice_url")
            if not invoice_url:
                raise ValueError(f"No invoice_url in response: {data}")
            logger.info(f"[NOWPAYMENTS] Invoice created: order_id={order_id}")
            return invoice_url


def verify_nowpayments_signature(sorted_payload_json: str, signature: str, ipn_secret: str) -> bool:
    """Verify HMAC-SHA512 signature from NowPayments IPN webhook"""
    expected = hmac.new(
        ipn_secret.encode('utf-8'),
        sorted_payload_json.encode('utf-8'),
        hashlib.sha512
    ).hexdigest()
    return hmac.compare_digest(expected, signature.lower())
