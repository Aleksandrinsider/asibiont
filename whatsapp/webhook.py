"""
WhatsApp Cloud API webhook handler for aiohttp.

Meta sends incoming messages via POST /webhook/whatsapp.
Verification handshake via GET /webhook/whatsapp.

Docs: https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks
"""

import hashlib
import hmac
import json
import logging
import asyncio
from aiohttp import web

from config import (
    WHATSAPP_VERIFY_TOKEN,
    WHATSAPP_APP_SECRET,
    WHATSAPP_ENABLED,
)

logger = logging.getLogger(__name__)

# ─── Deduplication cache (same pattern as Telegram handler) ──────
_wa_message_cache: dict[str, float] = {}
_WA_CACHE_MAX = 500


def _phone_to_pseudo_tid(phone: str) -> int:
    """
    Convert WhatsApp phone (E.164 digits) to a pseudo-telegram_id.
    Uses negative value to never collide with real Telegram IDs (positive).
    Example: '79031234567' → -79031234567
    """
    digits = ''.join(c for c in phone if c.isdigit())
    return -int(digits)


def _verify_signature(payload: bytes, signature: str) -> bool:
    """Verify X-Hub-Signature-256 from Meta."""
    if not WHATSAPP_APP_SECRET:
        return True  # Skip verification if secret not configured
    expected = hmac.new(
        WHATSAPP_APP_SECRET.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)


# ═══════════════════════════════════════════════════════════════════
#  GET /webhook/whatsapp — Meta verification handshake
# ═══════════════════════════════════════════════════════════════════
async def whatsapp_webhook_verify(request: web.Request) -> web.Response:
    """
    WhatsApp Cloud API webhook verification.
    Meta sends: hub.mode=subscribe, hub.verify_token=<token>, hub.challenge=<challenge>
    We must return the challenge value if the token matches.
    """
    mode = request.query.get("hub.mode")
    token = request.query.get("hub.verify_token")
    challenge = request.query.get("hub.challenge")

    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        logger.info("[WA] Webhook verified successfully")
        return web.Response(text=challenge, content_type="text/plain")

    logger.warning(f"[WA] Webhook verification failed: mode={mode}, token={token}")
    return web.Response(status=403, text="Forbidden")


# ═══════════════════════════════════════════════════════════════════
#  POST /webhook/whatsapp — incoming messages from Meta
# ═══════════════════════════════════════════════════════════════════
async def whatsapp_webhook_handler(request: web.Request) -> web.Response:
    """
    Handle incoming WhatsApp messages.
    We always respond 200 immediately (Meta retries on failure).
    Actual processing happens in background task.
    """
    if not WHATSAPP_ENABLED:
        return web.Response(status=200, text="OK")

    # Verify signature
    raw_body = await request.read()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if WHATSAPP_APP_SECRET and not _verify_signature(raw_body, signature):
        logger.warning("[WA] Invalid signature, rejecting")
        return web.Response(status=403, text="Invalid signature")

    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError:
        return web.Response(status=400, text="Bad JSON")

    # Meta sends various event types; we only care about messages
    # Structure: body.entry[].changes[].value.messages[]
    entries = body.get("entry", [])
    for entry in entries:
        for change in entry.get("changes", []):
            value = change.get("value", {})
            messages = value.get("messages", [])
            contacts = value.get("contacts", [])

            for msg in messages:
                # Launch processing in background so we return 200 fast
                asyncio.create_task(_process_wa_message(msg, contacts, value))

    return web.Response(status=200, text="OK")


async def _process_wa_message(msg: dict, contacts: list, value: dict):
    """Process a single incoming WhatsApp message."""
    import time as time_module

    msg_id = msg.get("id", "")
    msg_type = msg.get("type", "")
    phone = msg.get("from", "")
    timestamp = msg.get("timestamp", "")

    # Deduplication
    global _wa_message_cache
    now = time_module.time()
    _wa_message_cache = {k: v for k, v in _wa_message_cache.items() if now - v < 120}
    if len(_wa_message_cache) > _WA_CACHE_MAX:
        _wa_message_cache.clear()
    if msg_id in _wa_message_cache:
        logger.info(f"[WA] Duplicate message {msg_id}, skipping")
        return
    _wa_message_cache[msg_id] = now

    # We only handle text messages for now
    if msg_type != "text":
        logger.info(f"[WA] Ignoring non-text message type={msg_type} from {phone}")
        # Mark as read anyway
        try:
            from .client import whatsapp_client
            await whatsapp_client.mark_read(msg_id)
        except Exception:
            pass
        return

    text = msg.get("text", {}).get("body", "").strip()
    if not text:
        return

    # Get sender's display name from contacts
    sender_name = ""
    for c in contacts:
        if c.get("wa_id") == phone:
            sender_name = c.get("profile", {}).get("name", "")
            break

    logger.info(f"[WA] Message from {phone} ({sender_name}): {text[:100]}")

    # Mark as read
    try:
        from .client import whatsapp_client
        await whatsapp_client.mark_read(msg_id)
    except Exception as e:
        logger.warning(f"[WA] Failed to mark read: {e}")

    # Process through the shared AI pipeline
    await _handle_wa_text(phone, sender_name, text)


async def _handle_wa_text(phone: str, sender_name: str, text: str):
    """
    Bridge WhatsApp text message to the existing AI pipeline.
    Creates/finds user by phone, calls chat_with_ai, sends response back.
    """
    from models import Session, User, Interaction, UserProfile
    from ai_integration import chat_with_ai
    from token_service import has_enough_tokens, spend_tokens, grant_signup_tokens
    from config import FREE_ACCESS_MODE
    from i18n import get_user_lang, detect_lang_from_telegram, set_user_lang
    from .client import whatsapp_client

    pseudo_tid = _phone_to_pseudo_tid(phone)

    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=pseudo_tid).first()
        is_first_message = False

        if not user:
            # Also try finding by whatsapp_phone
            user = session.query(User).filter_by(whatsapp_phone=phone).first()

        if not user:
            # Auto-detect language from first message
            detected_lang = 'en'  # Default for WhatsApp international users
            # Simple heuristic: if message contains Cyrillic → Russian
            if any('\u0400' <= ch <= '\u04ff' for ch in text):
                detected_lang = 'ru'

            user = User(
                telegram_id=pseudo_tid,
                username=sender_name or f"wa_{phone[-4:]}",
                token_balance=0,
                platform='whatsapp',
                whatsapp_phone=phone,
                language=detected_lang,
            )
            session.add(user)
            session.commit()
            grant_signup_tokens(pseudo_tid, session=session)
            set_user_lang(pseudo_tid, detected_lang)
            is_first_message = True
        elif not user.whatsapp_phone:
            # Backfill phone for existing user
            user.whatsapp_phone = phone
            user.platform = 'whatsapp'
            session.commit()

        if not session.query(Interaction).filter_by(user_id=user.id).first():
            is_first_message = True
    finally:
        session.close()

    lang = get_user_lang(pseudo_tid)

    # Welcome message for first-time WhatsApp users
    if is_first_message:
        if lang == 'en':
            welcome = (
                "🤖 *ASI Biont* — your AI assistant for task management, networking & automation.\n\n"
                "✨ Just write what you need — I'll handle tasks, reminders, contacts and more.\n\n"
                "1,500 free tokens credited. Let's start!"
            )
        else:
            welcome = (
                "🤖 *ASI Biont* — AI-ассистент для управления задачами, нетворкинга и автоматизации.\n\n"
                "✨ Просто напиши что нужно — я займусь задачами, напоминаниями, контактами.\n\n"
                "1 500 бесплатных токенов начислено. Начнём!"
            )
        await whatsapp_client.send_text(phone, welcome)

    # Token balance check
    if not FREE_ACCESS_MODE and not has_enough_tokens(pseudo_tid, 'message'):
        from token_service import insufficient_balance_message
        await whatsapp_client.send_text(phone, insufficient_balance_message(pseudo_tid, 'message'))
        return

    # Handle special commands
    lower_text = text.lower().strip()

    if lower_text in ("очистить историю", "clear history"):
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=pseudo_tid).first()
            if user:
                from datetime import datetime, timezone
                user.history_cleared_at = datetime.now(timezone.utc)
                session.commit()
        finally:
            session.close()
        msg = "History cleared." if lang == 'en' else "История очищена."
        await whatsapp_client.send_text(phone, msg)
        return

    if lower_text in ("/lang en", "/lang ru"):
        new_lang = lower_text.split()[-1]
        set_user_lang(pseudo_tid, new_lang)
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=pseudo_tid).first()
            if user:
                user.language = new_lang
                session.commit()
        finally:
            session.close()
        confirm = "Language set to English 🇬🇧" if new_lang == 'en' else "Язык установлен: Русский 🇷🇺"
        await whatsapp_client.send_text(phone, confirm)
        return

    # Save user message
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=pseudo_tid).first()
        if user:
            interaction = Interaction(user_id=user.id, message_type='user', content=text)
            session.add(interaction)
            session.commit()
    except Exception as e:
        logger.error(f"[WA] Failed to save message from {phone}: {e}")
        try:
            session.rollback()
        except Exception:
            pass
    finally:
        session.close()

    # Progress callback for WhatsApp (send "typing" indicator)
    _progress_sent = {'sent': False}

    async def wa_progress_callback(progress_text):
        if not _progress_sent['sent']:
            # WhatsApp doesn't support editing messages, so just send once
            _progress_sent['sent'] = True
            # We skip progress messages on WhatsApp to avoid spam
            # (WhatsApp has no message editing like Telegram)
            pass

    # Call AI pipeline
    db_session = Session()
    response_text = ""
    try:
        result = await chat_with_ai(
            text,
            context=[],
            user_id=pseudo_tid,
            db_session=db_session,
            progress_callback=wa_progress_callback,
        )
        response_text = result.get('response', '') if isinstance(result, dict) else str(result)

        if not response_text or not response_text.strip():
            response_text = "Done! What's next?" if lang == 'en' else "Готово! Что дальше?"

        # Clean HTML/Telegram-specific formatting for WhatsApp
        clean_text = _strip_html(response_text)

        # Send response
        await whatsapp_client.send_text(phone, clean_text)

        # Charge tokens
        if not FREE_ACCESS_MODE:
            spend_tokens(pseudo_tid, 'message', description=text[:100])

    except Exception as e:
        logger.error(f"[WA] AI error for {phone}: {e}", exc_info=True)
        err = "Sorry, an error occurred. Please try again." if lang == 'en' else "Извините, произошла ошибка. Попробуйте ещё раз."
        await whatsapp_client.send_text(phone, err)
    finally:
        db_session.close()

    # Save AI response
    try:
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=pseudo_tid).first()
            if user and response_text:
                interaction = Interaction(user_id=user.id, message_type='ai', content=response_text.strip())
                session.add(interaction)
                session.commit()
        finally:
            session.close()
    except Exception as e:
        logger.error(f"[WA] Failed to save AI response for {phone}: {e}")


def _strip_html(text: str) -> str:
    """Remove HTML tags commonly used in Telegram formatting."""
    import re
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Convert HTML entities
    text = text.replace('&amp;', '&')
    text = text.replace('&lt;', '<')
    text = text.replace('&gt;', '>')
    text = text.replace('&quot;', '"')
    text = text.replace('&#39;', "'")
    return text
