"""
WhatsApp Cloud API client — sending messages via Meta Graph API.

Docs: https://developers.facebook.com/docs/whatsapp/cloud-api/messages

Usage:
    from whatsapp.client import whatsapp_client
    await whatsapp_client.send_text(phone="1234567890", text="Hello!")
"""

import aiohttp
import logging
from config import WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v21.0"
BASE_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}/messages"


class WhatsAppClient:
    """Async client for WhatsApp Cloud API (send direction)."""

    def __init__(self):
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
                    "Content-Type": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    # ─── TEXT MESSAGE ─────────────────────────────────────────────
    async def send_text(self, phone: str, text: str, preview_url: bool = False) -> dict | None:
        """
        Send a plain-text message.
        `phone` must be in E.164 format without '+' (e.g. '79031234567').
        WhatsApp limits: 4096 chars per text message.
        """
        if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
            logger.warning("[WA] WhatsApp not configured, skipping send")
            return None

        # Split long messages (WA limit = 4096)
        chunks = self._split_text(text, max_len=4096)
        result = None
        for chunk in chunks:
            result = await self._post({
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": phone,
                "type": "text",
                "text": {
                    "preview_url": preview_url,
                    "body": chunk,
                },
            })
        return result

    # ─── REACTION ─────────────────────────────────────────────────
    async def send_reaction(self, phone: str, message_id: str, emoji: str) -> dict | None:
        """Send a reaction emoji to a message."""
        return await self._post({
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone,
            "type": "reaction",
            "reaction": {
                "message_id": message_id,
                "emoji": emoji,
            },
        })

    # ─── MARK AS READ ─────────────────────────────────────────────
    async def mark_read(self, message_id: str) -> dict | None:
        """Mark incoming message as read (double blue check)."""
        return await self._post({
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        })

    # ─── INTERACTIVE BUTTONS ──────────────────────────────────────
    async def send_buttons(self, phone: str, body_text: str, buttons: list[dict]) -> dict | None:
        """
        Send an interactive message with reply buttons (max 3).
        Each button: {"id": "btn_1", "title": "Accept"}
        """
        btn_rows = [{"type": "reply", "reply": b} for b in buttons[:3]]
        return await self._post({
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body_text},
                "action": {"buttons": btn_rows},
            },
        })

    # ─── INTERNAL ─────────────────────────────────────────────────
    async def _post(self, payload: dict) -> dict | None:
        session = await self._get_session()
        try:
            async with session.post(BASE_URL, json=payload) as resp:
                data = await resp.json()
                if resp.status != 200:
                    logger.error(f"[WA] API error {resp.status}: {data}")
                    return None
                return data
        except Exception as e:
            logger.error(f"[WA] Request failed: {e}")
            return None

    @staticmethod
    def _split_text(text: str, max_len: int = 4096) -> list[str]:
        """Split text into chunks respecting newlines."""
        if len(text) <= max_len:
            return [text]
        chunks = []
        while text:
            if len(text) <= max_len:
                chunks.append(text)
                break
            # Try to split at last newline before max_len
            split_at = text.rfind('\n', 0, max_len)
            if split_at == -1:
                split_at = max_len
            chunks.append(text[:split_at])
            text = text[split_at:].lstrip('\n')
        return chunks


# Singleton instance
whatsapp_client = WhatsAppClient()
