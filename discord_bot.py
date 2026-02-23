"""
Discord Bot for ASI Biont.

Handles DM-based AI dialog for English-speaking audiences.
Telegram handles autoposts + RU users; Discord handles EN audience dialog.

Discord users are stored in the DB with:
  telegram_id = -discord_user_id  (negative to avoid collisions)
  discord_id  = discord_user_id
  platform    = 'discord'
  language    = 'en'
"""

import logging
import asyncio
import aiohttp
from typing import Optional

logger = logging.getLogger(__name__)

_discord_bot = None
_discord_task: Optional[asyncio.Task] = None


async def start_discord_bot():
    """Start the Discord bot as a background asyncio task."""
    global _discord_bot, _discord_task

    try:
        from config import DISCORD_BOT_TOKEN, DISCORD_ENABLED
    except ImportError:
        logger.warning("Discord config not available")
        return

    if not DISCORD_ENABLED:
        logger.info("Discord bot disabled — DISCORD_BOT_TOKEN not set")
        return

    try:
        import discord
    except ImportError:
        logger.error("discord.py not installed — run: pip install discord.py")
        return

    intents = discord.Intents.default()
    intents.message_content = True
    intents.dm_messages = True

    bot = discord.Client(intents=intents)
    _discord_bot = bot

    @bot.event
    async def on_ready():
        logger.info(f"✅ Discord bot connected as {bot.user} (id={bot.user.id})")

    @bot.event
    async def on_message(message: discord.Message):
        # Ignore self
        if message.author == bot.user:
            return

        # Only handle DMs
        if not isinstance(message.channel, discord.DMChannel):
            return

        discord_user_id = message.author.id
        text = message.content.strip()
        if not text:
            return

        logger.info(f"[DISCORD] DM from {message.author} ({discord_user_id}): {text[:80]}")

        # Indicate typing
        async with message.channel.typing():
            reply = await _handle_discord_message(discord_user_id, message.author, text)

        # Split long messages (Discord max 2000 chars)
        for chunk in _split_message(reply):
            await message.channel.send(chunk)

    async def runner():
        try:
            await bot.start(DISCORD_BOT_TOKEN)
        except Exception as e:
            logger.error(f"Discord bot error: {e}", exc_info=True)

    _discord_task = asyncio.create_task(runner())
    logger.info("Discord bot task started")


async def stop_discord_bot():
    """Gracefully stop the Discord bot."""
    global _discord_bot, _discord_task
    if _discord_bot and not _discord_bot.is_closed():
        await _discord_bot.close()
        logger.info("Discord bot closed")
    if _discord_task and not _discord_task.done():
        _discord_task.cancel()
        try:
            await _discord_task
        except asyncio.CancelledError:
            pass


async def send_discord_dm(discord_user_id: int, text: str) -> bool:
    """
    Send a proactive DM to a Discord user.
    Called by reminder/notification services.
    Returns True on success.
    """
    if _discord_bot is None or _discord_bot.is_closed():
        return False
    try:
        import discord
        user = await _discord_bot.fetch_user(discord_user_id)
        if user:
            for chunk in _split_message(text):
                await user.send(chunk)
            return True
    except Exception as e:
        logger.error(f"Failed to DM discord user {discord_user_id}: {e}")
    return False


async def discord_oauth_callback(request):
    """
    aiohttp route: GET /auth/discord
    Handles Discord OAuth2 callback to link a Discord account to the web session.
    """
    from aiohttp import web
    from aiohttp_session import get_session
    import urllib.parse

    code = request.rel_url.query.get('code')
    state = request.rel_url.query.get('state')  # telegram_id passed as state

    if not code:
        return web.Response(text="Missing OAuth code", status=400)

    try:
        from config import DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, WEB_APP_URL
        redirect_uri = f"{WEB_APP_URL}/auth/discord"

        # Exchange code for token
        async with aiohttp.ClientSession() as http:
            token_resp = await http.post(
                "https://discord.com/api/oauth2/token",
                data={
                    "client_id": DISCORD_CLIENT_ID,
                    "client_secret": DISCORD_CLIENT_SECRET,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            token_data = await token_resp.json()
            access_token = token_data.get("access_token")
            if not access_token:
                return web.Response(text="OAuth token exchange failed", status=400)

            # Get Discord user info
            user_resp = await http.get(
                "https://discord.com/api/users/@me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            discord_profile = await user_resp.json()
            discord_id = int(discord_profile["id"])
            discord_username = discord_profile.get("username", "")

        # Link Discord ID to the logged-in user
        session = await get_session(request)
        telegram_id = session.get("user_id") or (int(state) if state else None)
        if not telegram_id:
            return web.Response(text="Not authenticated", status=401)

        from models import Session as DBSession, User
        db = DBSession()
        try:
            user = db.query(User).filter_by(telegram_id=telegram_id).first()
            if user:
                user.discord_id = discord_id
                user.platform = 'discord'
                db.commit()
                logger.info(f"Linked discord_id={discord_id} to user telegram_id={telegram_id}")
        finally:
            db.close()

        return web.Response(
            text=f"<html><body><p>Discord account <b>{discord_username}</b> linked successfully! "
                 f"<a href='/dashboard'>Back to dashboard</a></p></body></html>",
            content_type="text/html",
        )

    except Exception as e:
        logger.error(f"Discord OAuth error: {e}", exc_info=True)
        return web.Response(text="OAuth error", status=500)


# ─── Internal helpers ──────────────────────────────────────────────────────────

async def _handle_discord_message(discord_user_id: int, author, text: str) -> str:
    """Find/create user and call AI agent."""
    from models import Session as DBSession, User, Interaction
    from ai_integration import chat_with_ai
    import datetime

    # Discord users stored with telegram_id = -discord_user_id
    pseudo_telegram_id = -discord_user_id

    db = DBSession()
    try:
        user = db.query(User).filter_by(discord_id=discord_user_id).first()
        if not user:
            # Try legacy lookup by pseudo telegram_id
            user = db.query(User).filter_by(telegram_id=pseudo_telegram_id).first()

        if not user:
            # Register new Discord user
            user = User(
                telegram_id=pseudo_telegram_id,
                discord_id=discord_user_id,
                username=str(author),
                first_name=author.display_name,
                platform='discord',
                language='en',
                token_balance=1500,  # Welcome tokens
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            logger.info(f"[DISCORD] New user registered: discord_id={discord_user_id}")

        # Save incoming message
        interaction = Interaction(
            user_id=user.id,
            message_type='user',
            content=text,
            created_at=datetime.datetime.now(datetime.timezone.utc),
        )
        db.add(interaction)
        db.commit()

        # Call AI agent
        result = await chat_with_ai(
            text,
            user_id=pseudo_telegram_id,
            db_session=db,
        )
        response = result.get('response', 'Sorry, something went wrong.')

        # Save AI response
        ai_interaction = Interaction(
            user_id=user.id,
            message_type='ai',
            content=response,
            created_at=datetime.datetime.now(datetime.timezone.utc),
        )
        db.add(ai_interaction)
        db.commit()

        return response

    except Exception as e:
        logger.error(f"[DISCORD] Error handling message from {discord_user_id}: {e}", exc_info=True)
        return "Sorry, an error occurred. Please try again."
    finally:
        db.close()


def _split_message(text: str, max_len: int = 1990) -> list[str]:
    """Split a long message into chunks respecting Discord's 2000-char limit."""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:max_len])
        text = text[max_len:]
    return chunks
