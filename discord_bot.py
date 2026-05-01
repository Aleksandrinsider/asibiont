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
from contextlib import asynccontextmanager
from typing import Optional
from config import normalize_name

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _safe_http(**kwargs):
    session = aiohttp.ClientSession(**kwargs)
    try:
        yield session
    finally:
        await session.close()
        await asyncio.sleep(0)


_discord_bot = None
_discord_task: Optional[asyncio.Task] = None
_processed_msg_ids: set[int] = set()       # dedup guard
_DEDUP_MAX = 500                           # ring-buffer size
_processing_lock: asyncio.Lock | None = None  # prevents parallel duplicate processing


async def start_discord_bot():
    """Start the Discord bot as a background asyncio task."""
    global _discord_bot, _discord_task

    # Guard: don't start a second instance if already running
    if _discord_bot is not None and not _discord_bot.is_closed():
        logger.info("Discord bot already running, skipping duplicate start")
        return
    if _discord_task is not None and not _discord_task.done():
        logger.info("Discord bot task already active, skipping duplicate start")
        return

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
        # Suppress "PyNaCl is not installed, voice will NOT be supported" — voice not used
        import warnings
        warnings.filterwarnings('ignore', message='PyNaCl')
        logging.getLogger('discord.client').addFilter(
            lambda r: 'PyNaCl' not in r.getMessage()
        )
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
        global _processing_lock
        if _processing_lock is None:
            _processing_lock = asyncio.Lock()

        # Ignore self
        if message.author == bot.user:
            return

        # Only handle DMs
        if not isinstance(message.channel, discord.DMChannel):
            return

        # ── Dedup guard (reconnect / gateway replay) ──
        # Use lock to prevent two coroutines processing the same message.id simultaneously
        async with _processing_lock:
            if message.id in _processed_msg_ids:
                logger.debug(f"[DISCORD] Skipping duplicate msg {message.id}")
                return
            _processed_msg_ids.add(message.id)
            if len(_processed_msg_ids) > _DEDUP_MAX:
                to_drop = sorted(_processed_msg_ids)[:_DEDUP_MAX // 2]
                _processed_msg_ids.difference_update(to_drop)

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
        _retry = 0
        while True:
            try:
                await bot.start(DISCORD_BOT_TOKEN)
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                _retry += 1
                _is_timeout = isinstance(e, (asyncio.TimeoutError, aiohttp.ClientError, TimeoutError))
                if _is_timeout and _retry <= 5:
                    _delay = min(30, 2 * _retry)
                    logger.warning(f"Discord bot transient network error (retry {_retry}/5 in {_delay}s): {e}")
                    try:
                        if not bot.is_closed():
                            await bot.close()
                    except Exception:
                        pass
                    await asyncio.sleep(_delay)
                    continue
                logger.error(f"Discord bot error: {e}", exc_info=True)
                try:
                    if not bot.is_closed():
                        await bot.close()
                except Exception:
                    pass
                return

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


async def send_discord_dm(discord_id: int, text: str) -> bool:
    """Send a DM to a Discord user by their discord_id.

    Returns True on success, False on failure.
    Used by reminder_service to push reminders/proactive messages.
    """
    if not _discord_bot or _discord_bot.is_closed():
        return False
    try:
        user = await _discord_bot.fetch_user(discord_id)
        for chunk in _split_message(text):
            await user.send(chunk)
        logger.info(f"[DISCORD] DM sent to discord_id={discord_id}")
        return True
    except Exception as e:
        logger.warning(f"[DISCORD] Failed to send DM to discord_id={discord_id}: {e}")
        return False


async def discord_oauth_callback(request):
    """
    aiohttp route: GET /auth/discord
    Handles Discord OAuth2 callback.
    - If user is already logged in (session) → link Discord to existing account.
    - If not logged in (state=login) → login/register via Discord.
    """
    from aiohttp import web
    from aiohttp_session import get_session
    import urllib.parse

    code = request.rel_url.query.get('code')
    state = request.rel_url.query.get('state', '')  # 'login' or telegram_id

    if not code:
        return web.Response(text="Missing OAuth code", status=400)

    try:
        from config import DISCORD_CLIENT_ID, DISCORD_CLIENT_SECRET, WEB_APP_URL
        redirect_uri = f"{WEB_APP_URL}/auth/discord"

        # Exchange code for token
        async with _safe_http() as http:
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
                logger.error(f"Discord OAuth token exchange failed: {token_data}")
                return web.Response(text="OAuth token exchange failed", status=400)

            # Get Discord user info
            user_resp = await http.get(
                "https://discord.com/api/users/@me",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            discord_profile = await user_resp.json()
            discord_id = int(discord_profile["id"])
            discord_username = discord_profile.get("username", "")
            discord_display = discord_profile.get("global_name") or discord_username
            discord_avatar = discord_profile.get("avatar")

        session = await get_session(request)
        existing_user_id = session.get("user_id")

        # ── LOGIN / REGISTER via Discord ──
        if not existing_user_id and (state == 'login' or state == ''):
            from models import Session as DBSession, User, Subscription
            from token_service import grant_signup_tokens
            import time as _time

            pseudo_telegram_id = -discord_id
            db = DBSession()
            try:
                user = db.query(User).filter_by(discord_id=discord_id).first()
                if not user:
                    user = db.query(User).filter_by(telegram_id=pseudo_telegram_id).first()

                is_new = False
                if not user:
                    # Build avatar URL
                    avatar_url = None
                    if discord_avatar:
                        avatar_url = f"https://cdn.discordapp.com/avatars/{discord_id}/{discord_avatar}.png?size=256"

                    # Detect timezone from IP
                    ip_address = request.headers.get('X-Forwarded-For', request.remote or '').split(',')[0].strip()
                    timezone = 'UTC'
                    city = None
                    try:
                        from main import get_timezone_from_ip
                        timezone, city = await get_timezone_from_ip(ip_address)
                    except Exception:
                        pass

                    user = User(
                        telegram_id=pseudo_telegram_id,
                        discord_id=discord_id,
                        username=discord_username,
                        discord_username=discord_username,
                        first_name=normalize_name(discord_display),
                        photo_url=avatar_url,
                        platform='discord',
                        language='en',
                        timezone=timezone,
                    )
                    db.add(user)
                    db.commit()
                    db.refresh(user)

                    # Grant signup tokens
                    try:
                        grant_signup_tokens(pseudo_telegram_id, session=db)
                    except Exception as e:
                        logger.error(f"Discord signup tokens error: {e}")

                    # Create profile with city
                    if city:
                        from models import UserProfile
                        profile = UserProfile(user_id=user.id, city=city, contact_info=f"discord_{discord_id}")
                        db.add(profile)
                        db.commit()

                    is_new = True
                    logger.info(f"[DISCORD] New user registered via web: discord_id={discord_id}, username={discord_username}")
                else:
                    # Update existing user info
                    if discord_avatar:
                        user.photo_url = f"https://cdn.discordapp.com/avatars/{discord_id}/{discord_avatar}.png?size=256"
                    if discord_display:
                        user.first_name = normalize_name(discord_display)
                    user.discord_id = discord_id
                    user.discord_username = discord_username
                    db.commit()

                # Set session
                session['user_id'] = user.telegram_id
                logger.info(f"[DISCORD] Session set with user_id={user.telegram_id} (discord login)")

                # Increment login count
                sub = db.query(Subscription).filter_by(user_id=user.id).first()
                if sub:
                    sub.login_count += 1
                    db.commit()

            finally:
                db.close()

            return web.HTTPFound('/dashboard')

        # ── LINK Discord to existing logged-in user ──
        from models import Session as DBSession, User, Interaction
        db = DBSession()
        try:
            user = db.query(User).filter_by(telegram_id=existing_user_id).first()
            if user:
                # Check if there's a separate Discord-only account that should be merged
                discord_only_user = db.query(User).filter_by(discord_id=discord_id).first()
                if discord_only_user and discord_only_user.id != user.id:
                    # Merge: move all interactions from Discord account to TG account
                    db.query(Interaction).filter_by(user_id=discord_only_user.id).update(
                        {Interaction.user_id: user.id}, synchronize_session=False
                    )
                    # Transfer token balance if any
                    if discord_only_user.token_balance and discord_only_user.token_balance > 0:
                        user.token_balance = (user.token_balance or 0) + discord_only_user.token_balance
                    # Delete the duplicate Discord-only account
                    db.delete(discord_only_user)
                    logger.info(f"Merged Discord-only account (id={discord_only_user.id}) into TG account (id={user.id})")

                user.discord_id = discord_id
                user.discord_username = discord_username
                db.commit()
                logger.info(f"Linked discord_id={discord_id} ({discord_username}) to user telegram_id={existing_user_id}")
        finally:
            db.close()

        return web.HTTPFound('/dashboard')

    except Exception as e:
        logger.error(f"Discord OAuth error: {e}", exc_info=True)
        return web.Response(text="OAuth error", status=500)


async def discord_login_redirect(request):
    """
    aiohttp route: GET /discord/login
    Redirects user to Discord OAuth2 authorization page for login.
    """
    from aiohttp import web
    from config import DISCORD_CLIENT_ID, WEB_APP_URL
    import urllib.parse

    redirect_uri = urllib.parse.quote(f"{WEB_APP_URL}/auth/discord", safe='')
    oauth_url = (
        f"https://discord.com/oauth2/authorize"
        f"?client_id={DISCORD_CLIENT_ID}"
        f"&response_type=code"
        f"&redirect_uri={redirect_uri}"
        f"&scope=identify"
        f"&state=login"
    )
    return web.HTTPFound(oauth_url)


async def discord_link_redirect(request):
    """
    aiohttp route: GET /discord/link
    Redirects logged-in user to Discord OAuth2 to link their Discord account.
    """
    from aiohttp import web
    from aiohttp_session import get_session
    from config import DISCORD_CLIENT_ID, WEB_APP_URL
    import urllib.parse

    session = await get_session(request)
    if not session.get('user_id'):
        return web.HTTPFound('/')

    redirect_uri = urllib.parse.quote(f"{WEB_APP_URL}/auth/discord", safe='')
    oauth_url = (
        f"https://discord.com/oauth2/authorize"
        f"?client_id={DISCORD_CLIENT_ID}"
        f"&response_type=code"
        f"&redirect_uri={redirect_uri}"
        f"&scope=identify"
        f"&state=link"
    )
    return web.HTTPFound(oauth_url)


# ─── Internal helpers ──────────────────────────────────────────────────────────

async def _handle_discord_message(discord_user_id: int, author, text: str) -> str:
    """Find/create user and call AI agent."""
    from models import Session as DBSession, User, Interaction
    from ai_integration import chat_with_ai
    import datetime

    db = DBSession()
    try:
        # 1) Lookup by discord_id (covers both linked TG users and pure Discord users)
        user = db.query(User).filter_by(discord_id=discord_user_id).first()

        if not user:
            # 2) Legacy lookup by pseudo telegram_id
            pseudo_telegram_id = -discord_user_id
            user = db.query(User).filter_by(telegram_id=pseudo_telegram_id).first()

        if not user:
            # Build Discord avatar URL
            avatar_url = None
            if author.avatar:
                avatar_url = str(author.avatar.url)

            # Register new Discord user
            pseudo_telegram_id = -discord_user_id
            user = User(
                telegram_id=pseudo_telegram_id,
                discord_id=discord_user_id,
                username=str(author),
                first_name=normalize_name(author.display_name),
                photo_url=avatar_url,
                platform='discord',
                language='en',
                token_balance=1500,  # Welcome tokens
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            logger.info(f"[DISCORD] New user registered: discord_id={discord_user_id}, avatar={'yes' if avatar_url else 'no'}")
        else:
            # Update avatar on every message (keep it fresh, like TG does on login)
            if author.avatar:
                new_avatar = str(author.avatar.url)
                if user.photo_url != new_avatar:
                    user.photo_url = new_avatar
                    db.commit()

        # The telegram_id to pass to AI (may be real TG id if account is linked)
        ai_user_id = user.telegram_id

        # Check token balance
        from token_service import has_enough_tokens, spend_tokens, insufficient_balance_message
        from config import FREE_ACCESS_MODE
        if not FREE_ACCESS_MODE and not has_enough_tokens(ai_user_id, 'message'):
            return insufficient_balance_message(ai_user_id, 'message')

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
            user_id=ai_user_id,
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

        # Deduct message tokens
        if not FREE_ACCESS_MODE:
            spend_tokens(ai_user_id, 'message', description=text[:100])

        return response

    except Exception as e:
        logger.error(f"[DISCORD] Error handling message from {discord_user_id}: {e}", exc_info=True)
        return "Sorry, an error occurred. Please try again."
    finally:
        db.close()


def _split_message(text: str, max_len: int = 1990) -> list[str]:
    """Split a long message into chunks respecting Discord's 2000-char limit.
    
    Tries to split at paragraph breaks, then newlines, then sentence ends,
    then spaces — falling back to hard cut only as last resort.
    """
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        # Try split points in priority order
        cut = -1
        segment = text[:max_len]
        # 1) double newline (paragraph break)
        idx = segment.rfind('\n\n')
        if idx > max_len // 3:
            cut = idx + 2
        # 2) single newline
        if cut < 0:
            idx = segment.rfind('\n')
            if idx > max_len // 3:
                cut = idx + 1
        # 3) sentence end (. ! ?)
        if cut < 0:
            for sep in ('. ', '! ', '? '):
                idx = segment.rfind(sep)
                if idx > max_len // 3:
                    cut = max(cut, idx + len(sep))
        # 4) space
        if cut < 0:
            idx = segment.rfind(' ')
            if idx > max_len // 4:
                cut = idx + 1
        # 5) hard cut
        if cut < 0:
            cut = max_len

        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    return chunks
