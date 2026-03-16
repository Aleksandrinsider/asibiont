import logging
import asyncio
import html as html_mod
import os
import re
import tempfile
import json
import traceback
import time as time_module
from datetime import datetime, timedelta, timezone
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

# Per-user Telegram message rate limiting (in-memory)
_tg_rate_store: dict[int, list[float]] = {}
_TG_RATE_WINDOW = 60.0   # seconds
_TG_RATE_MAX = 20        # messages per window per user

def _check_tg_rate_limit(user_id: int) -> bool:
    """Return True if the user is within rate limit, False if they exceeded it."""
    now = time_module.time()
    if user_id not in _tg_rate_store:
        _tg_rate_store[user_id] = []
    _tg_rate_store[user_id] = [t for t in _tg_rate_store[user_id] if now - t < _TG_RATE_WINDOW]
    if len(_tg_rate_store[user_id]) >= _TG_RATE_MAX:
        return False
    _tg_rate_store[user_id].append(now)
    return True


def _format_html(text: str) -> str:
    """Prepare text for Telegram HTML parse_mode: escape text, wrap URLs in <a> tags."""
    url_re = re.compile(r'(https?://\S+)')
    # Ensure URLs have space before them if glued to text
    text = re.sub(r'(?<=[^\s\n])(https?://)', r' \1', text)
    parts = url_re.split(text)
    result = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            # Regular text — escape HTML entities
            result.append(html_mod.escape(part))
        else:
            # URL — strip trailing punctuation, wrap in <a>
            clean = part.rstrip('.,;:!?)—»')
            trailing = part[len(clean):]
            result.append(f'<a href="{html_mod.escape(clean)}">{html_mod.escape(clean)}</a>{html_mod.escape(trailing)}')
    return ''.join(result)


def _smart_split_html(text: str, max_len: int = 4000) -> list:
    """Разбивает HTML-текст на части, не ломая теги и URL.

    Стратегия:
    1. Ищем последний перенос строки (\n) до лимита.
    2. Если нет — ищем последний пробел.
    3. Никогда не режем внутри <a>...</a> или HTML-тега.
    """
    if len(text) <= max_len:
        return [text]

    chunks = []
    remaining = text

    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break

        # Ищем позицию для разреза (не дальше max_len)
        cut = max_len
        candidate = remaining[:cut]

        # Проверяем: не режем ли внутри <a href="...">...</a>
        # Находим последний открытый <a без закрывающего </a>
        last_a_open = candidate.rfind('<a ')
        if last_a_open != -1:
            last_a_close = candidate.rfind('</a>', last_a_open)
            if last_a_close == -1:
                # Тег <a> не закрыт — режем до него
                cut = last_a_open

        # Проверяем: не режем ли внутри HTML-тега < ... >
        last_lt = candidate[:cut].rfind('<')
        if last_lt != -1:
            last_gt = candidate[:cut].rfind('>')
            if last_gt < last_lt:
                # Незакрытый тег — режем до него
                cut = last_lt

        # Теперь ищем хорошую точку разреза (перенос или пробел)
        segment = remaining[:cut]
        nl_pos = segment.rfind('\n')
        if nl_pos > cut * 0.3:  # Перенос строки найден не слишком рано
            cut = nl_pos + 1
        else:
            sp_pos = segment.rfind(' ')
            if sp_pos > cut * 0.5:
                cut = sp_pos + 1

        # Защита от бесконечного цикла
        if cut <= 0:
            cut = max_len

        chunks.append(remaining[:cut])
        remaining = remaining[cut:]

    return chunks

router = Router()

# Dedup cache for message processing (TTL-based to prevent memory leak)
message_cache = {}
message_cache_lock = asyncio.Lock()  # async-safe lock for event loop
MESSAGE_CACHE_MAX_SIZE = 5000  # Максимум записей

# Per-user processing lock — prevents duplicate responses
# when Telegram retries webhook or sends duplicate updates
_user_processing_locks: dict[int, asyncio.Lock] = {}
_USER_LOCKS_MAX_SIZE = 2000  # Максимум локов

def _get_user_lock(user_id: int) -> asyncio.Lock:
    # Safe in asyncio single-threaded event loop (no threading.Lock needed)
    if user_id not in _user_processing_locks:
        # Очистка при переполнении — удаляем незалоченные локи
        if len(_user_processing_locks) >= _USER_LOCKS_MAX_SIZE:
            to_remove = [k for k, v in _user_processing_locks.items() if not v.locked()]
            for k in to_remove[:len(to_remove)//2]:
                del _user_processing_locks[k]
        _user_processing_locks[user_id] = asyncio.Lock()
    return _user_processing_locks[user_id]
from ai_integration import chat_with_ai
from models import Session, User, Subscription, Task, Interaction
from sqlalchemy import func
from payments import create_payment
from config import WEBHOOK_URL
from config import WEB_APP_URL, FREE_ACCESS_MODE
from token_service import (
    get_balance, has_enough_tokens, spend_tokens, add_tokens,
    grant_signup_tokens, get_balance_info, insufficient_balance_message,
    TOKEN_PACKAGES, FREE_TOKENS_ON_SIGNUP
)
try:
    from timezonefinder import TimezoneFinder as _TimezoneFinder
    _tf_available = True
except Exception:
    _tf_available = False
from i18n import get_user_lang, set_user_lang, detect_lang_from_telegram, t

logger = logging.getLogger(__name__)

async def send_delegation_notification_async(chat_id, message_text):
    """Асинхронная отправка уведомления о делегировании"""
    try:
        from config import TELEGRAM_TOKEN
        import aiohttp

        if not TELEGRAM_TOKEN:
            return

        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {
            "chat_id": chat_id,
            "text": message_text
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=data, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status != 200:
                    logger.warning(f"Failed to send delegation notification: {response.status}")

    except Exception as e:
        logger.warning(f"Error sending delegation notification: {e}")

PREMIUM_DESCRIPTION = {
    'ru': """AI отвечают. ASI Biont — действует!

AI-агент, который реально делает дела. Помнит всё, действует сам — ставит напоминания, отправляет письма, публикует посты, анализирует продажи на Wildberries и Ozon. Подключается к 25+ сервисам. Работает через Web-панель, Telegram и Discord.

Думает за тебя
«Напомни о звонке завтра в 10» — поставит напоминание. «Что горит?» — покажет просроченные задачи с приоритетами. Автономный помощник, который сам планирует и контролирует.

Пишет и публикует
«Напиши пост про кейс и сгенерируй обложку» — текст и картинка через секунды. Публикует в Telegram-канал или Discord по расписанию. Контент работает 24/7 без вас.

Подключается к сервисам
Gmail, Notion, Slack, Wildberries, Ozon, GitHub, hh.ru — не просто читает, но и действует: отправляет письмо, создаёт задачу, постит. Одна команда — действие в нужном сервисе.

Говорит на любом языке
Пишете по-русски — клиент получает на английском. Партнёр из Дубая, коллега из Берлина — агент переведёт и передаст без потери смысла. Языковой барьер перестаёт существовать.

Ваш агент в маркетплейсе
Создайте агента с именем, характером и экспертизой. Выведите в маркетплейс — другие пользователи активируют и используют его.

Арена AI
Агенты дебатируют в реальном времени. Откройте тему — разберут прямо в ленте. Смотреть может каждый без регистрации.

━━━━━━━━━━━━━━━━━━━━

Все функции доступны сразу. Платите только за использование.

Стартовый — 1 500 руб. → 1 500 токенов
Оптимальный — 5 000 руб. → 5 500 токенов (+10%)
Максимальный — 50 000 руб. → 60 000 токенов (+20%)

Пополнить баланс: /buy
Баланс: /balance
Поддержка: @aleksandrinsider""",

    'en': """AIs answer. ASI Biont — acts!

An AI agent that actually gets things done. Remembers everything, acts on its own — sets reminders, sends emails, publishes posts, analyzes sales on Wildberries & Ozon. Connects to 20+ services. No prompts.

Thinks for you
"Remind me about the call tomorrow at 10" — sets a reminder. "What's urgent?" — shows overdue tasks by priority. Autonomous assistant that plans and tracks on its own.

Writes and publishes
"Write a post about the case and generate a cover" — text and image in seconds. Posts to Telegram channel or Discord on schedule. Your content runs 24/7 without you.

Connects to services
Gmail, Notion, Slack, Wildberries, Ozon, GitHub, hh.ru — not just reads, but acts: sends email, creates tasks, posts to Slack. One command — action in the right service.

Speaks any language
Write in Russian — your client receives it in English. Partner in Dubai, colleague in Berlin — the agent translates without losing meaning. Language barriers stop existing.

Your agent in the marketplace
Build an agent with a name, personality, and expertise. List it in the marketplace — other users activate and use it.

AI Arena
Agents debate in real time. Start a topic — they discuss it right in the feed. Anyone can watch without registration.

━━━━━━━━━━━━━━━━━━━━

All features available immediately. Pay only for usage.

Starter — $15 USDT → 1,500 tokens
Optimal — $50 USDT → 5,500 tokens (+10%)
Maximum — $500 USDT → 60,000 tokens (+20%)

Top up: /buy
Balance: /balance
Support: @aleksandrinsider"""
}

try:
    from config import GROQ_API_KEY, OPENAI_API_KEY as _WHISPER_OPENAI_KEY
except ImportError:
    GROQ_API_KEY = None
    _WHISPER_OPENAI_KEY = None

# Determine which Whisper backend is available
_WHISPER_BACKEND = None
if GROQ_API_KEY:
    _WHISPER_BACKEND = "groq"
elif _WHISPER_OPENAI_KEY:
    _WHISPER_BACKEND = "openai"
else:
    logging.warning(
        "[VOICE] No GROQ_API_KEY or OPENAI_API_KEY found — voice transcription unavailable. "
        "Add GROQ_API_KEY (free at console.groq.com) to enable it."
    )

# Legacy Google SR / pydub — kept only as last-resort fallback
try:
    import speech_recognition as _sr
    from pydub import AudioSegment as _AudioSegment
    _GOOGLE_SR_AVAILABLE = True
except Exception as _e:
    logging.warning(f"[VOICE] Google SR / pydub not available (expected on Railway): {_e}")
    _GOOGLE_SR_AVAILABLE = False


async def transcribe_audio(audio_file_path: str) -> str | None:
    """
    Транскрибирует OGG-файл в текст.

    Порядок бэкендов:
    1. Groq Whisper (бесплатно, GROQ_API_KEY)
    2. OpenAI Whisper (OPENAI_API_KEY)
    3. Google Speech Recognition (fallback, ненадёжно в production)
    """
    if not os.path.exists(audio_file_path):
        logging.error(f"[VOICE] Audio file not found: {audio_file_path}")
        return None

    import aiohttp as _aiohttp

    # ── 1. Groq Whisper ──────────────────────────────────────────────────────
    if _WHISPER_BACKEND == "groq":
        try:
            url = "https://api.groq.com/openai/v1/audio/transcriptions"
            async with _aiohttp.ClientSession() as sess:
                with open(audio_file_path, "rb") as f:
                    form = _aiohttp.FormData()
                    form.add_field("file", f, filename="voice.ogg", content_type="audio/ogg")
                    form.add_field("model", "whisper-large-v3-turbo")
                    form.add_field("language", "ru")
                    form.add_field("response_format", "json")
                    async with sess.post(
                        url,
                        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                        data=form,
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            text = data.get("text", "").strip()
                            logging.info(f"[VOICE] Groq transcribed: {text[:60]}")
                            return text or None
                        body = await resp.text()
                        logging.error(f"[VOICE] Groq API error {resp.status}: {body}")
        except Exception as e:
            logging.error(f"[VOICE] Groq transcription failed: {e}", exc_info=True)

    # ── 2. OpenAI Whisper ────────────────────────────────────────────────────
    if _WHISPER_BACKEND == "openai" or (_WHISPER_BACKEND == "groq" and _WHISPER_OPENAI_KEY):
        try:
            url = "https://api.openai.com/v1/audio/transcriptions"
            async with _aiohttp.ClientSession() as sess:
                with open(audio_file_path, "rb") as f:
                    form = _aiohttp.FormData()
                    form.add_field("file", f, filename="voice.ogg", content_type="audio/ogg")
                    form.add_field("model", "whisper-1")
                    form.add_field("language", "ru")
                    async with sess.post(
                        url,
                        headers={"Authorization": f"Bearer {_WHISPER_OPENAI_KEY}"},
                        data=form,
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            text = data.get("text", "").strip()
                            logging.info(f"[VOICE] OpenAI Whisper transcribed: {text[:60]}")
                            return text or None
                        body = await resp.text()
                        logging.error(f"[VOICE] OpenAI Whisper error {resp.status}: {body}")
        except Exception as e:
            logging.error(f"[VOICE] OpenAI transcription failed: {e}", exc_info=True)

    # ── 3. Google SR fallback (local / dev only) ─────────────────────────────
    if _GOOGLE_SR_AVAILABLE:
        wav_path = None
        try:
            audio_obj = _AudioSegment.from_ogg(audio_file_path)
            wav_path = audio_file_path.replace(".ogg", ".wav")
            audio_obj.export(wav_path, format="wav")
            recognizer = _sr.Recognizer()
            with _sr.AudioFile(wav_path) as source:
                audio_data = recognizer.record(source)
                text = recognizer.recognize_google(audio_data, language="ru-RU")
                logging.info(f"[VOICE] Google SR transcribed: {text[:60]}")
                return text
        except Exception as e:
            logging.error(f"[VOICE] Google SR failed: {e}", exc_info=True)
        finally:
            if wav_path and os.path.exists(wav_path):
                try:
                    os.unlink(wav_path)
                except Exception:
                    pass

    logging.error("[VOICE] All transcription backends failed")
    return None

logger = logging.getLogger(__name__)

@router.message(Command("start"))
async def start_handler(message: Message):
    user_id = message.from_user.id

    # Auto-detect language from Telegram
    tg_lang = getattr(message.from_user, 'language_code', None) or ''
    detected_lang = detect_lang_from_telegram(tg_lang)

    # Extract referral code if present
    referrer_id = None
    if message.text and len(message.text.split()) > 1:
        start_param = message.text.split()[1]
        if start_param.startswith('ref'):
            try:
                referrer_id = int(start_param[3:])  # Extract telegram_id from ref{telegram_id}
            except ValueError:
                pass

    # Create user if doesn't exist
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        is_new_user = False
        if not user:
            # Find referrer by telegram_id to get internal user.id
            referrer = None
            if referrer_id:
                referrer = session.query(User).filter_by(telegram_id=referrer_id).first()
                if referrer:
                    logger.info(f"Referrer found: telegram_id={referrer_id}, internal_id={referrer.id}")
                else:
                    logger.warning(f"Referrer not found for telegram_id: {referrer_id}")
            user = User(telegram_id=user_id, username=message.from_user.username, token_balance=0,
                        referrer_id=referrer.id if referrer else None,
                        language=detected_lang)
            session.add(user)
            session.commit()
            logger.info(f"Created new user {user_id}, referrer={referrer_id}, lang={detected_lang}")
            is_new_user = True
            # Начисляем бесплатные токены
            grant_signup_tokens(user_id, session=session)
        else:
            # Existing user — use their saved language
            detected_lang = getattr(user, 'language', None) or detected_lang
        
        # Set language in cache
        set_user_lang(user_id, detected_lang)
        lang = detected_lang

        balance = user.token_balance or 0
    finally:
        session.close()

    btn_text = "Open web version" if lang == 'en' else "Открыть веб-версию"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=btn_text, web_app=WebAppInfo(url=f"{WEB_APP_URL}/dashboard"))]
    ])
    
    desc = PREMIUM_DESCRIPTION.get(lang, PREMIUM_DESCRIPTION['ru'])
    if is_new_user:
        if lang == 'en':
            welcome_text = desc + f"\n\n You've received {FREE_TOKENS_ON_SIGNUP} free tokens! Just write to me."
        else:
            welcome_text = desc + f"\n\n Тебе начислено {FREE_TOKENS_ON_SIGNUP} бесплатных токенов! Просто напиши мне."
    else:
        if lang == 'en':
            welcome_text = desc + f"\n\n Your balance: {balance} tokens"
        else:
            welcome_text = desc + f"\n\n Твой баланс: {balance} токенов"
    await message.bot.send_message(message.chat.id, welcome_text, reply_markup=keyboard)


@router.message(Command("balance"))
async def balance_handler(message: Message):
    """Показать баланс токенов"""
    user_id = message.from_user.id
    info = get_balance_info(user_id)
    await message.bot.send_message(message.chat.id, info)


@router.message(Command("lang"))
async def lang_handler(message: Message):
    """Change language / Сменить язык"""
    user_id = message.from_user.id
    args = (message.text or '').replace('/lang', '').strip().lower()
    
    if args in ('en', 'english'):
        set_user_lang(user_id, 'en')
        await message.bot.send_message(message.chat.id, t('en', 'cmd_lang_changed', lang='English'))
    elif args in ('ru', 'russian', 'русский'):
        set_user_lang(user_id, 'ru')
        await message.bot.send_message(message.chat.id, t('ru', 'cmd_lang_changed', lang='Русский'))
    else:
        lang = get_user_lang(user_id)
        lang_name = 'English' if lang == 'en' else 'Русский'
        await message.bot.send_message(message.chat.id, t(lang, 'cmd_lang_current', lang=lang_name))


@router.message(Command("buy"))
async def buy_handler(message: Message):
    """Покупка пакетов токенов"""
    user_id = message.from_user.id
    lang = get_user_lang(user_id)
    
    if lang == 'en':
        from crypto_payments import CRYPTO_PACK_PRICES, create_crypto_payment
        from config import NOWPAYMENTS_API_KEY, WEB_APP_URL as _WAU
        text = " Token packages (1 token ≈ $0.01 USDT):\n\n"
        for key, info in CRYPTO_PACK_PRICES.items():
            text += f"• ${info['price_usd']} USDT — {info['tokens']:,} tokens\n"
        text += "\nChoose a package:"
        try:
            if not NOWPAYMENTS_API_KEY:
                raise ValueError("Crypto payments not configured")
            payment_text = ""
            for key, info in CRYPTO_PACK_PRICES.items():
                url = await create_crypto_payment(key, user_id, NOWPAYMENTS_API_KEY, _WAU)
                payment_text += f"\n${info['price_usd']} USDT — {info['tokens']:,} tokens:\n{url}\n"
            await message.bot.send_message(message.chat.id, text + "\n" + payment_text)
        except Exception as e:
            logger.error(f"Error creating crypto payment for user {user_id}: {e}")
            await message.bot.send_message(message.chat.id, text + "\n\n Payment error. Try again later.\nSupport: @aleksandrinsider")
    else:
        text = " Пакеты токенов (1 токен = 1₽):\n\n"
        for key, pkg in TOKEN_PACKAGES.items():
            text += f"• {pkg['label']}\n"
        text += "\nВыберите пакет:"
        try:
            urls = {}
            for key, pkg in TOKEN_PACKAGES.items():
                url = create_payment(
                    pkg['price'],
                    f"Пополнение {pkg['tokens']} токенов",
                    user_id,
                    f'tokens_{key}',
                    None
                )
                urls[key] = url
            payment_text = ""
            for key, pkg in TOKEN_PACKAGES.items():
                payment_text += f"\n{pkg['label']}:\n{urls[key]}\n"
            await message.bot.send_message(message.chat.id, text + "\n" + payment_text)
        except Exception as e:
            logger.error(f"Error creating token payment for user {user_id}: {e}")
            await message.bot.send_message(message.chat.id, text + "\n\n Не удалось создать платёж — попробуй чуть позже.\nПоддержка: @aleksandrinsider")


@router.message(Command("subscription"))
async def subscription_handler(message: Message):
    """Handle subscription command — redirect to token-based billing"""
    await buy_handler(message)


@router.message(Command("update_profile"))
async def update_profile_handler(message: Message):
    user_id = message.from_user.id
    if not FREE_ACCESS_MODE and not has_enough_tokens(user_id, 'message'):
        await message.bot.send_message(message.chat.id, insufficient_balance_message(user_id, 'message'))
        return
    # Отправить запрос в ИИ
    text = message.text.replace("/update_profile", "").strip()
    if text:
        prompt = f"Обнови мой профиль: {text}"
    else:
        prompt = "Помоги обновить профиль"
    from ai_integration.utils import get_context_from_db
    context = get_context_from_db(user_id, limit=10)
    ai_result = await chat_with_ai(prompt, context, user_id)
    response = ai_result['response']
    await message.bot.send_message(message.chat.id, response)


@router.message(Command("find_partners"))
async def find_partners_handler(message: Message):
    user_id = message.from_user.id
    if not FREE_ACCESS_MODE and not has_enough_tokens(user_id, 'message'):
        await message.bot.send_message(message.chat.id, insufficient_balance_message(user_id, 'message'))
        return
    # Отправить запрос в ИИ
    try:
        from ai_integration.utils import get_context_from_db
        context = get_context_from_db(user_id, limit=10)
        ai_result = await chat_with_ai("Найди партнеров", context, user_id)
        response = ai_result['response']
        await message.bot.send_message(message.chat.id, response)
    except Exception as e:
        logger.error(f"Error in find_partners_handler: {e}")
        err = "An error occurred while searching for partners." if get_user_lang(user_id) == 'en' else "Произошла ошибка при поиске партнеров."
        await message.bot.send_message(message.chat.id, err)


@router.message(Command("subscribe"))
async def subscribe_handler(message: Message):
    """Legacy /subscribe — redirect to token-based /buy"""
    await buy_handler(message)


@router.message()
async def chat_handler(message: Message):
    user_id = message.from_user.id
    message_id = message.message_id

    # Rate limit check
    if not _check_tg_rate_limit(user_id):
        lang = get_user_lang(user_id)
        _rl_msg = "Too many messages. Please wait a moment." if lang == 'en' else "Погоди секунду, не успеваю за тобой 😅"
        await message.bot.send_message(message.chat.id, _rl_msg)
        return

    logger.info(
        f"[HANDLER] chat_handler called: message_id={message_id}, user={user_id}, text={message.text[:30] if message.text else 'NO_TEXT'}")

    # Обработка голосовых сообщений
    if message.voice:
        logger.info(f"[VOICE] Received voice message from user {user_id}")

        try:
            # Проверка баланса токенов
            session = Session()
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                user = User(telegram_id=user_id, username=message.from_user.username, token_balance=0)
                session.add(user)
                session.commit()
                grant_signup_tokens(user_id, session=session)
            session.close()

            if not FREE_ACCESS_MODE and not has_enough_tokens(user_id, 'voice_message'):
                await message.bot.send_message(message.chat.id, insufficient_balance_message(user_id, 'voice_message'))
                return

            # Скачиваем голосовое сообщение
            file = await message.bot.get_file(message.voice.file_id)
            file_path = file.file_path

            # Скачиваем файл
            import aiohttp

            bot_token = message.bot.token
            file_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"

            async with aiohttp.ClientSession() as session_http:
                async with session_http.get(file_url) as resp:
                    if resp.status == 200:
                        # Сохраняем во временный файл
                        with tempfile.NamedTemporaryFile(delete=False, suffix='.ogg') as tmp_file:
                            tmp_file.write(await resp.read())
                            tmp_file_path = tmp_file.name

                        try:
                            # Показываем индикатор "печатает..."
                            await message.bot.send_chat_action(message.chat.id, "typing")

                            # Транскрибируем аудио в текст
                            text = await transcribe_audio(tmp_file_path)

                            if text:
                                logger.info(f"[VOICE] Transcribed text: {text}")
                                # Обрабатываем транскрибированный текст как обычное сообщение
                                await process_text_message(user_id, text, message, None, source='voice_message')
                                return
                            else:
                                err = "Couldn't recognize voice message. Try sending text." if get_user_lang(user_id) == 'en' else "Не разобрал голосовое — попробуй текстом"
                                await message.bot.send_message(
                                    message.chat.id, err
                                )
                                return
                        finally:
                            # Удаляем временный файл
                            os.unlink(tmp_file_path)
                    else:
                        logger.error(f"[VOICE] Failed to download voice file: {resp.status}")
                        err = "Error downloading voice message." if get_user_lang(user_id) == 'en' else "Не получилось загрузить голосовое — попробуй ещё раз"
                        await message.bot.send_message(message.chat.id, err)
                        return
        except Exception as e:
            logger.error(f"[VOICE] Error processing voice message: {e}", exc_info=True)
            err = "An error occurred processing the voice message." if get_user_lang(user_id) == 'en' else "Что-то пошло не так с голосовым — попробуй ещё раз"
            await message.bot.send_message(message.chat.id, err)
            return

    # Обработка документов
    if message.document:
        logger.info(f"[DOC] Received document from user {user_id}: {message.document.file_name}")
        try:
            session = Session()
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                user = User(telegram_id=user_id, username=message.from_user.username, token_balance=0)
                session.add(user)
                session.commit()
                grant_signup_tokens(user_id, session=session)
            session.close()

            if not FREE_ACCESS_MODE and not has_enough_tokens(user_id, 'message'):
                await message.bot.send_message(message.chat.id, insufficient_balance_message(user_id, 'message'))
                return

            await message.bot.send_chat_action(message.chat.id, "typing")

            file_name = message.document.file_name or "document"
            file_size = message.document.file_size or 0
            mime_type = message.document.mime_type or ""

            # Ограничение: файлы до 5 МБ
            if file_size > 5 * 1024 * 1024:
                await message.bot.send_message(message.chat.id, " Файл слишком большой (макс 5 МБ). Отправь файл поменьше.")
                return

            # Поддерживаемые форматы для извлечения текста
            text_extensions = {'.txt', '.csv', '.json', '.xml', '.html', '.md', '.py', '.js', '.ts', '.yaml', '.yml', '.log', '.cfg', '.ini', '.env'}
            ext = os.path.splitext(file_name)[1].lower()

            extracted_text = None

            if ext in text_extensions or mime_type.startswith('text/'):
                # Текстовые файлы — читаем напрямую
                file = await message.bot.get_file(message.document.file_id)
                import aiohttp
                file_url = f"https://api.telegram.org/file/bot{message.bot.token}/{file.file_path}"
                async with aiohttp.ClientSession() as session_http:
                    async with session_http.get(file_url) as resp:
                        if resp.status == 200:
                            raw = await resp.read()
                            for encoding in ['utf-8', 'cp1251', 'latin-1']:
                                try:
                                    extracted_text = raw.decode(encoding)
                                    break
                                except UnicodeDecodeError:
                                    continue
                            if extracted_text and len(extracted_text) > 4000:
                                extracted_text = extracted_text[:4000] + f"\n\n... (обрезано, всего {len(raw)} байт)"

            elif ext == '.pdf' or mime_type == 'application/pdf':
                # PDF — извлечение текста через PyPDF2
                file = await message.bot.get_file(message.document.file_id)
                import aiohttp
                file_url = f"https://api.telegram.org/file/bot{message.bot.token}/{file.file_path}"
                async with aiohttp.ClientSession() as session_http:
                    async with session_http.get(file_url) as resp:
                        if resp.status == 200:
                            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
                                tmp.write(await resp.read())
                                tmp_path = tmp.name
                            try:
                                import PyPDF2
                                with open(tmp_path, 'rb') as f:
                                    reader = PyPDF2.PdfReader(f)
                                    pages_text = []
                                    for i, page in enumerate(reader.pages[:20]):  # макс 20 страниц
                                        pages_text.append(page.extract_text() or '')
                                    extracted_text = '\n'.join(pages_text).strip()
                                    if len(extracted_text) > 4000:
                                        extracted_text = extracted_text[:4000] + f"\n\n... (обрезано, всего {len(reader.pages)} стр.)"
                                    if not extracted_text:
                                        extracted_text = "(PDF без текстового слоя — возможно, сканированный документ)"
                            except ImportError:
                                extracted_text = "(PDF-парсер не установлен, но файл получен)"
                            finally:
                                os.unlink(tmp_path)

            elif ext in {'.docx'} or mime_type == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document':
                # DOCX
                file = await message.bot.get_file(message.document.file_id)
                import aiohttp
                file_url = f"https://api.telegram.org/file/bot{message.bot.token}/{file.file_path}"
                async with aiohttp.ClientSession() as session_http:
                    async with session_http.get(file_url) as resp:
                        if resp.status == 200:
                            with tempfile.NamedTemporaryFile(delete=False, suffix='.docx') as tmp:
                                tmp.write(await resp.read())
                                tmp_path = tmp.name
                            try:
                                import docx
                                doc = docx.Document(tmp_path)
                                paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
                                extracted_text = '\n'.join(paragraphs)
                                if len(extracted_text) > 4000:
                                    extracted_text = extracted_text[:4000] + f"\n\n... (обрезано)"
                                if not extracted_text:
                                    extracted_text = "(DOCX пуст или содержит только изображения)"
                            except ImportError:
                                extracted_text = "(DOCX-парсер не установлен, но файл получен)"
                            finally:
                                os.unlink(tmp_path)
            
            # Формируем сообщение для AI
            caption = message.caption or ""
            if extracted_text:
                ai_text = f"[Пользователь отправил файл: {file_name}]\n"
                if caption:
                    ai_text += f"Комментарий: {caption}\n"
                ai_text += f"Содержимое файла:\n{extracted_text}"
            else:
                ai_text = f"[Пользователь отправил файл: {file_name} ({mime_type}, {file_size} байт)]"
                if caption:
                    ai_text += f"\nКомментарий: {caption}"
                ai_text += "\nФормат не поддерживается для извлечения текста. Подтверди получение и спроси, что сделать с файлом."

            await process_text_message(user_id, ai_text, message, None)
            return

        except Exception as e:
            logger.error(f"[DOC] Error processing document: {e}", exc_info=True)
            err = "An error occurred processing the file." if get_user_lang(user_id) == 'en' else "Произошла ошибка при обработке файла."
            await message.bot.send_message(message.chat.id, err)
            return

    # Обработка фото
    if message.photo:
        logger.info(f"[PHOTO] Received photo from user {user_id}")
        try:
            session = Session()
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                user = User(telegram_id=user_id, username=message.from_user.username, token_balance=0)
                session.add(user)
                session.commit()
                grant_signup_tokens(user_id, session=session)
            session.close()

            if not FREE_ACCESS_MODE and not has_enough_tokens(user_id, 'message'):
                await message.bot.send_message(message.chat.id, insufficient_balance_message(user_id, 'message'))
                return

            await message.bot.send_chat_action(message.chat.id, "typing")

            # Берём фото наибольшего размера
            photo = message.photo[-1]
            caption = message.caption or ""

            ai_text = f"[Пользователь отправил фото ({photo.width}x{photo.height})]"
            if caption:
                ai_text += f"\nПодпись: {caption}"
            ai_text += "\nОпиши что можешь сделать: создать задачу из фото, обсудить содержимое, сохранить заметку."

            await process_text_message(user_id, ai_text, message, None)
            return

        except Exception as e:
            logger.error(f"[PHOTO] Error processing photo: {e}", exc_info=True)
            err = "An error occurred processing the photo." if get_user_lang(user_id) == 'en' else "Произошла ошибка при обработке фото."
            await message.bot.send_message(message.chat.id, err)
            return

    # Обработка текстовых сообщений
    if message.text:
        await process_text_message(user_id, message.text, message, None)
    else:
        # Обработка других типов сообщений (геолокация и т.д.)
        await process_other_message(user_id, message, None)


async def process_text_message(user_id, text, message, state, source='message'):
    message_id = message.message_id

    # Проверка на пустые сообщения
    if not text or text.strip() == "":
        logger.info(f"Empty message from user {user_id}, ignoring")
        return

    # Duplicate protection - dict-операции атомарны в asyncio single-threaded loop
    global message_cache
    message_cache_key = f"msg_{user_id}_{message_id}"
    current_time = time_module.time()
    
    # Очистка старых записей (старше 60 секунд) + лимит размера
    message_cache = {k: v for k, v in message_cache.items() if current_time - v < 60}
    if len(message_cache) > MESSAGE_CACHE_MAX_SIZE:
        message_cache.clear()
    
    # Проверяем, было ли уже обработано это сообщение
    if message_cache_key in message_cache:
        logger.info(f"[DEDUP] Duplicate message detected: msg_id={message_id}, user={user_id}, skipping")
        return
    
    # Отмечаем сообщение как обработанное
    message_cache[message_cache_key] = current_time
    logger.info(f"[DEDUP] Message registered: msg_id={message_id}, user={user_id}, cache_size={len(message_cache)}")

    # Per-user lock — если уже обрабатываем сообщение от этого юзера, пропускаем
    user_lock = _get_user_lock(user_id)
    if user_lock.locked():
        logger.info(f"[DEDUP] User {user_id} already processing, skipping duplicate")
        return

    await _process_text_message_inner(user_id, text, message, state, user_lock)


async def _process_text_message_inner(user_id, text, message, state, user_lock):
    await user_lock.acquire()
    try:
        logger.info(f"[PTM] Step 1: checking user {user_id}")
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            is_first_message = False
            if not user:
                user = User(telegram_id=user_id, username=message.from_user.username, token_balance=0)
                session.add(user)
                session.commit()
                grant_signup_tokens(user_id, session=session)
                is_first_message = True
            elif not session.query(Interaction).filter_by(user_id=user.id).first():
                is_first_message = True
        finally:
            session.close()
        logger.info(f"[PTM] Step 1 done, is_first={is_first_message}")

        lang = get_user_lang(user_id)

        # Новый пользователь — сначала показываем описание и тарифы
        if is_first_message:
            btn_text = "Open web version" if lang == 'en' else "Открыть веб-версию"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=btn_text, web_app=WebAppInfo(url=f"{WEB_APP_URL}/dashboard"))]
            ])
            balance = get_balance(user_id)
            desc = PREMIUM_DESCRIPTION.get(lang, PREMIUM_DESCRIPTION['ru'])
            if lang == 'en':
                welcome_text = desc + f"\n\n1,500 free tokens credited. Just write to me."
            else:
                welcome_text = desc + f"\n\n1 500 бесплатных токенов начислено. Просто напиши мне."
            await message.bot.send_message(message.chat.id, welcome_text, reply_markup=keyboard)

        # Проверка баланса токенов
        if not FREE_ACCESS_MODE and not has_enough_tokens(user_id, 'message'):
            await message.bot.send_message(message.chat.id, insufficient_balance_message(user_id, 'message'))
            return

        # Handle delegation commands
        if text.lower().startswith("принять задачу ") or text.lower().startswith("accept task "):
            task_id = text.split()[-1]
            try:
                from ai_integration import accept_delegated_task
                result = accept_delegated_task(int(task_id), user_id=user_id)
                await message.bot.send_message(message.chat.id, result)
            except Exception as e:
                err_label = "Error" if lang == 'en' else "Ошибка"
                await message.bot.send_message(message.chat.id, f"{err_label}: {str(e)}")
            return

        if text.lower().startswith("отклонить задачу ") or text.lower().startswith("reject task "):
            task_id = text.split()[-1]
            try:
                from ai_integration import reject_delegated_task
                result = reject_delegated_task(int(task_id), user_id=user_id)
                await message.bot.send_message(message.chat.id, result)
            except Exception as e:
                err_label = "Error" if lang == 'en' else "Ошибка"
                await message.bot.send_message(message.chat.id, f"{err_label}: {str(e)}")
            return

        if text.lower() in ("очистить историю", "clear history"):
            # Update user.history_cleared_at in DB
            session = Session()
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user:
                from datetime import datetime, timezone
                user.history_cleared_at = datetime.now(timezone.utc)
                session.commit()
            session.close()
            cleared_msg = "History cleared." if lang == 'en' else "История очищена."
            await message.bot.send_message(message.chat.id, cleared_msg)
            return

        # СОХРАНЯЕМ СООБЩЕНИЕ ПОЛЬЗОВАТЕЛЯ СРАЗУ
        logger.info(f"[PTM] Step 2: saving user message")
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user:
                interaction = Interaction(user_id=user.id, message_type='user', content=text)
                session.add(interaction)
                session.commit()
        except Exception as e:
            logger.error(f"Failed to save user message for {user_id}: {e}")
            try:
                session.rollback()
            except Exception:
                pass
        finally:
            session.close()

        # FEEDBACK LOOP: отмечаем что пользователь ответил на якорное сообщение
        try:
            from anchor_engine import get_anchor_engine
            ae = get_anchor_engine()
            if ae:
                asyncio.create_task(ae.record_user_response(user_id))
        except Exception:
            pass
        
        context = []  # Simplified: no context in bot
        
        # Создаём progress callback для стриминга прогресса в чат
        chat_id = message.chat.id
        bot = message.bot
        
        # Храним ID последнего прогресс-сообщения для обновления
        _progress_state = {'last_msg_id': None}
        
        async def progress_callback(text, *, persist=False):
            """Отправляет или обновляет прогресс-сообщение в Telegram.
            persist=True — отправить как отдельное постоянное сообщение (для диалога директора).
            """
            display_text = text or ('Processing...' if lang == 'en' else 'Обрабатываю...')
            # Telegram не умеет рендерить JSON — конвертируем __agent JSON в читаемый текст
            if persist and isinstance(display_text, str) and display_text.strip().startswith('{'):
                try:
                    import json as _j_tg
                    _p_tg = _j_tg.loads(display_text)
                    if _p_tg.get('__agent') and _p_tg.get('text'):
                        _name_tg = _p_tg['__agent'].get('name', 'Агент')
                        _txt_tg = _p_tg['text'][:1000].strip()
                        display_text = f"\U0001f4ac {_name_tg}:\n{_txt_tg}"
                except Exception:
                    pass
            try:
                if persist:
                    # Директорский диалог: отправляем новое сообщение, НЕ удаляем
                    await bot.send_message(chat_id, display_text)
                    return
                if _progress_state['last_msg_id']:
                    # Обновляем существующее сообщение (не спамим чат)
                    try:
                        await bot.edit_message_text(
                            text=display_text,
                            chat_id=chat_id,
                            message_id=_progress_state['last_msg_id']
                        )
                        return
                    except Exception:
                        pass  # Если не удалось обновить — отправим новое
                
                sent = await bot.send_message(chat_id, display_text)
                _progress_state['last_msg_id'] = sent.message_id
            except Exception as e:
                logger.warning(f"Progress callback error: {e}")
        
        # Use autonomous agent instead of command router
        logger.info(f"[PTM] Step 3: calling chat_with_ai")
        from ai_integration import chat_with_ai
        db_session = Session()
        response_text = ""
        _agent_handled = False
        try:
            result = await chat_with_ai(text, context=context, user_id=user_id, db_session=db_session, progress_callback=progress_callback)
            _agent_handled = result.get('agent_handled', False) if isinstance(result, dict) else False
            response_text = result.get('response', '') if isinstance(result, dict) else str(result)
            # Финальная очистка HTML/email артефактов
            if response_text:
                try:
                    from ai_integration.utils import clean_technical_details as _ctd_final
                    _cleaned = _ctd_final(response_text)
                    if _cleaned and _cleaned.strip():
                        response_text = _cleaned
                except Exception:
                    pass
            logger.info(f"[PTM] Step 3a: got response, len={len(response_text)}")
            
            # Удаляем прогресс-сообщение перед финальным ответом
            if _progress_state['last_msg_id']:
                try:
                    await bot.delete_message(chat_id, _progress_state['last_msg_id'])
                except Exception:
                    pass
            
            # Защита от пустого ответа
            if not _agent_handled and (not response_text or not response_text.strip()):
                response_text = "Done! What's next?" if lang == 'en' else "Готово! Что дальше?"
            
            # Если агент уже ответил (через директора) — не отправляем и не сохраняем
            if _agent_handled:
                logger.info(f"[PTM] Agent handled for user {user_id}, skipping ASI response")
            else:
                # Гарантируем кликабельность ссылок в Telegram (HTML parse_mode)
                response_html = _format_html(response_text)

                # Разбиваем длинный ответ на части (Telegram лимит 4096)
                if len(response_html) > 4000:
                    # Умная разбивка: не ломаем HTML-теги и URL
                    chunks = _smart_split_html(response_html, 4000)
                    for chunk in chunks:
                        try:
                            await message.bot.send_message(message.chat.id, chunk, parse_mode='HTML')
                        except Exception:
                            # Fallback: отправляем plain text кусок соответствующей длины
                            await message.bot.send_message(message.chat.id, chunk[:4000])
                else:
                    try:
                        await message.bot.send_message(message.chat.id, response_html, parse_mode='HTML')
                    except Exception as html_err:
                        # Fallback without HTML if formatting breaks
                        logger.warning(f"[PTM] HTML send failed, falling back to plain text: {html_err}")
                        await message.bot.send_message(message.chat.id, response_text)
            
            # Списываем токены за сообщение
            if not FREE_ACCESS_MODE:
                _spend_result = spend_tokens(user_id, 'message', description=text[:100])
                if isinstance(_spend_result, dict) and not _spend_result.get('success', True):
                    logger.warning(f"[PTM] spend_tokens failed for user {user_id}: {_spend_result}")
        except Exception as e:
            logger.error(f"Error in autonomous chat for user {user_id}: {e}", exc_info=True)
            response_text = "Sorry, an error occurred while processing your message." if lang == 'en' else "Что-то пошло не так — попробуй ещё раз"
            try:
                await message.bot.send_message(message.chat.id, response_text)
            except Exception:
                logger.error(f"Failed to send error message to user {user_id}")
        finally:
            db_session.close()
        
        # СОХРАНЯЕМ ОТВЕТ AI (только если агент не ответил сам)
        if not _agent_handled:
            logger.info(f"[PTM] Step 4: saving AI response")
            try:
                session = Session()
                try:
                    user = session.query(User).filter_by(telegram_id=user_id).first()
                    if user:
                        if response_text and response_text.strip():
                            interaction = Interaction(user_id=user.id, message_type='ai', content=response_text.strip())
                        else:
                            fallback_content = "Sorry, could not generate a response." if lang == 'en' else "Хм, не получилось сформулировать ответ — попробуй переспросить"
                            interaction = Interaction(
                                user_id=user.id,
                                message_type='ai',
                                content=fallback_content)
                        session.add(interaction)
                        session.commit()
                    else:
                        logger.warning(f"User not found for telegram_id {user_id}, cannot save interactions")
                finally:
                    session.close()
            except Exception as e:
                logger.error(f"Failed to save AI response for {user_id}: {e}")

        # AGENT CHAT HOOKS: подписанные агенты наблюдают (non-blocking, НЕ при агентском ответе)
        if not _agent_handled and response_text and text:
            try:
                from anchor_engine import get_anchor_engine
                _ae_hook = get_anchor_engine()
                if _ae_hook:
                    asyncio.create_task(_ae_hook.trigger_chat_hook(user_id, text, response_text))
            except Exception:
                pass
    except Exception as e:
        logger.error(f"Error in process_text_message for user {user_id}: {e}", exc_info=True)
        import traceback
        error_detail = traceback.format_exc()
        logger.error(f"FULL TRACEBACK: {error_detail}")
        try:
            if lang == 'en':
                err_text = "Something went wrong on my side. Please write again."
            else:
                err_text = "Сбой на моей стороне, не у тебя. Напиши ещё раз."
            await message.bot.send_message(message.chat.id, err_text)
        except Exception:
            pass
    finally:
        user_lock.release()


async def process_other_message(user_id, message, state):
    # Обработка геолокации
    if message.location:
        lat = message.location.latitude
        lon = message.location.longitude
        
        # 1. Определяем часовой пояс
        timezone_str = None
        if _tf_available:
            try:
                _tf = _TimezoneFinder()
                timezone_str = _tf.timezone_at(lng=lon, lat=lat)
            except Exception:
                pass
        if not timezone_str:
            timezone_str = "UTC"
        
        # 2. Обратный геокодинг: координаты → город (Nominatim, бесплатно)
        city_name = None
        try:
            import aiohttp
            async with aiohttp.ClientSession() as geo_session:
                url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json&accept-language=ru"
                headers = {"User-Agent": "ASIBiont/1.0"}
                async with geo_session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        address = data.get("address", {})
                        city_name = address.get("city") or address.get("town") or address.get("village") or address.get("state")
                        logger.info(f"[GEO] Reverse geocoded ({lat}, {lon}) → {city_name}")
        except Exception as e:
            logger.warning(f"[GEO] Reverse geocoding failed: {e}")
        
        # 3. Сохраняем: часовой пояс + город в профиле
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user:
                if timezone_str:
                    user.timezone = timezone_str
                
                # Обновляем город в профиле
                if city_name:
                    from models import UserProfile
                    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
                    if profile:
                        old_city = profile.city
                        profile.city = city_name
                        from ai_integration.handlers import _clean_city_name, _CITY_ALIASES
                        _cleaned = _clean_city_name(city_name)
                        profile.city_normalized = _cleaned
                        _ru = _CITY_ALIASES.get(_cleaned, '')
                        profile.city_normalized_ru = _ru if _ru and any(c in _ru for c in 'абвгдежзиклмнопрстуфхцчшщэюя') else (_cleaned if any(c in _cleaned for c in 'абвгдежзиклмнопрстуфхцчшщэюя') else None)
                        logger.info(f"[GEO] Updated city for user {user_id}: {old_city} → {city_name}")
                    else:
                        profile = UserProfile(user_id=user.id, city=city_name)
                        session.add(profile)
                
                session.commit()
                
                # 4. Формируем ответ
                response_parts = []
                if timezone_str:
                    response_parts.append(f" Часовой пояс: {timezone_str}")
                if city_name:
                    response_parts.append(f" Город: {city_name}")
                
                response_text = "\n".join(response_parts)
                
                # 5. Ищем партнёров в этом городе
                nearby_text = ""
                if city_name:
                    from models import UserProfile as UP
                    nearby_profiles = session.query(UP).filter(
                        UP.user_id != user.id,
                        func.lower(UP.city) == city_name.lower()
                    ).limit(5).all()
                    
                    if nearby_profiles:
                        # Pre-fetch partner users (batch)
                        _nb_uids = [p.user_id for p in nearby_profiles]
                        _nb_users = session.query(User).filter(User.id.in_(_nb_uids)).all()
                        _nb_user_by_id = {u.id: u for u in _nb_users}
                        nearby_lines = []
                        for p in nearby_profiles:
                            partner_user = _nb_user_by_id.get(p.user_id)
                            if partner_user and partner_user.username:
                                info = []
                                if p.skills:
                                    info.append(p.skills[:50])
                                if p.interests:
                                    info.append(p.interests[:50])
                                detail = f" — {', '.join(info)}" if info else ""
                                nearby_lines.append(f"• @{partner_user.username}{detail}")
                        
                        if nearby_lines:
                            nearby_text = f"\n\n Люди рядом ({city_name}):\n" + "\n".join(nearby_lines)
                            nearby_text += "\n\n Напиши «найди партнёра для [задача]» — подберу лучших."
                
                await message.bot.send_message(
                    message.chat.id, 
                    f" Локация обновлена!\n{response_text}{nearby_text}"
                )
            else:
                await message.bot.send_message(message.chat.id, "Хм, не нахожу твой профиль — отправь /start")
        except Exception as e:
            logger.error(f"[GEO] Error processing location: {e}", exc_info=True)
            await message.bot.send_message(message.chat.id, "Не удалось обработать геолокацию — попробуй ещё раз")
        finally:
            session.close()
        return


def get_delegation_report(user_id, session=None):
    """Получить отчёт о делегированных задачах"""
    should_close = False
    if session is None:
        session = Session()
        should_close = True

    lang = get_user_lang(user_id)

    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            if should_close:
                session.close()
            return "User not found" if lang == 'en' else "Пользователь не найден"

        # Задачи, делегированные ОТ пользователя (кому он делегировал)
        delegated_by_user = session.query(Task).filter(
            Task.delegated_by == user.id
        ).order_by(Task.created_at.desc()).all()

        # Задачи, делегированные ПОЛЬЗОВАТЕЛЮ (кто делегировал ему)
        delegated_to_user = session.query(Task).filter(
            Task.delegated_to_username.ilike(user.username.replace('@', '') if user.username else ''),
            Task.delegation_status.isnot(None)
        ).order_by(Task.created_at.desc()).all()

        status_texts_by = {
            'ru': {None: "ожидает принятия", "pending": "ожидает принятия", "accepted": "принята в работу", "rejected": "отклонена", "completed": "завершена"},
            'en': {None: "pending acceptance", "pending": "pending acceptance", "accepted": "accepted", "rejected": "rejected", "completed": "completed"}
        }
        status_texts_to = {
            'ru': {"pending": "ожидает вашего решения", "accepted": "вы работаете над ней", "rejected": "вы отклонили", "completed": "завершена"},
            'en': {"pending": "awaiting your decision", "accepted": "you're working on it", "rejected": "you rejected", "completed": "completed"}
        }
        unknown_status = "unknown status" if lang == 'en' else "неизвестный статус"
        lbl_status = "Status" if lang == 'en' else "Статус"
        lbl_result = "Result" if lang == 'en' else "Результат"
        lbl_deadline = "Deadline" if lang == 'en' else "Дедлайн"

        report = []

        if delegated_by_user:
            header_by = " YOUR DELEGATED TASKS:" if lang == 'en' else " ВАШИ ДЕЛЕГИРОВАННЫЕ ЗАДАЧИ:"
            report.append(header_by)
            for task in delegated_by_user[:10]:
                status_emoji = {
                    None: "", "pending": "", "accepted": "", "rejected": "", "completed": ""
                }.get(task.delegation_status, "")

                status_text = status_texts_by.get(lang, status_texts_by['ru']).get(task.delegation_status, unknown_status)

                report.append(f"{status_emoji} '{task.title}' → @{task.delegated_to_username}")
                report.append(f"   {lbl_status}: {status_text}")

                if task.completion_notes:
                    report.append(f"   {lbl_result}: {task.completion_notes[:100]}...")

                if task.due_date:
                    report.append(f"   {lbl_deadline}: {task.due_date.strftime('%d.%m.%Y %H:%M')}")

                report.append("")

        if delegated_to_user:
            header_to = " TASKS DELEGATED TO YOU:" if lang == 'en' else " ЗАДАЧИ, ДЕЛЕГИРОВАННЫЕ ВАМ:"
            report.append(header_to)
            # Pre-fetch delegators (batch)
            _h_delegator_ids = list({t.delegated_by for t in delegated_to_user[:10] if t.delegated_by})
            _h_delegators = session.query(User).filter(User.id.in_(_h_delegator_ids)).all()
            _h_delegator_by_id = {u.id: u for u in _h_delegators}
            for task in delegated_to_user[:10]:
                delegator = _h_delegator_by_id.get(task.delegated_by)
                unknown_lbl = "unknown" if lang == 'en' else "неизвестный"
                delegator_name = f"@{delegator.username}" if delegator and delegator.username else unknown_lbl

                status_emoji = {
                    "pending": "", "accepted": "", "rejected": "", "completed": ""
                }.get(task.delegation_status, "")

                from_lbl = "from" if lang == 'en' else "от"
                status_text = status_texts_to.get(lang, status_texts_to['ru']).get(task.delegation_status, unknown_status)

                report.append(f"{status_emoji} '{task.title}' {from_lbl} {delegator_name}")
                report.append(f"   {lbl_status}: {status_text}")

                if task.completion_notes:
                    report.append(f"   {lbl_result}: {task.completion_notes[:100]}...")

                if task.due_date:
                    report.append(f"   {lbl_deadline}: {task.due_date.strftime('%d.%m.%Y %H:%M')}")

                report.append("")

        if not delegated_by_user and not delegated_to_user:
            no_tasks = "You have no delegated tasks." if lang == 'en' else "Нет активных делегированных задач"
            report.append(no_tasks)

        if should_close:
            session.close()

        sep = '\n'
        return f"DELEGATION_REPORT: {sep.join(report)}"

    except Exception as e:
        logger.error(f"Error getting delegation progress for user {user_id}: {e}")
        if should_close:
            session.close()
        err_prefix = "Error getting delegation report" if lang == 'en' else "Не удалось загрузить отчёт по делегированию"
        return f"{err_prefix}: {str(e)}"



# NOTE: delegate_task для AI-агента  в ai_integration/handlers.py
# Дубль #1 удалён как мёртвый код

@router.message(Command("dashboard"))
async def dashboard_handler(message: Message):
    user_id = message.from_user.id
    lang = get_user_lang(user_id)
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        msg = "Please register first by sending /start" if lang == 'en' else "Сначала отправь /start — и всё заработает"
        await message.bot.send_message(message.chat.id, msg)
        session.close()
        return
    session.close()

    # Generate dashboard URL
    base_url = WEBHOOK_URL.replace("/webhook", "")
    dashboard_url = f"{base_url}/dashboard?telegram_id={user_id}"
    
    btn_text = " Open web version" if lang == 'en' else " Открыть веб-версию"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=btn_text, web_app=WebAppInfo(url=dashboard_url))]
    ])
    
    dash_label = " Your personal dashboard" if lang == 'en' else " Твой дашборд"
    await message.bot.send_message(
        message.chat.id, 
        f"{dash_label}:\n{dashboard_url}", 
        reply_markup=keyboard
    )


@router.message(Command("run_agents"))
async def run_agents_handler(message: Message):
    """Форсирует одиночный прогон агентов пользователя: сбрасывает cooldown и запускает скрипты."""
    user_id = message.from_user.id
    lang = get_user_lang(user_id)
    session = Session()
    try:
        from models import UserAgent as _UAh
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            msg = "Please register first by sending /start" if lang == 'en' else "Сначала отправь /start — и всё заработает"
            await message.bot.send_message(message.chat.id, msg)
            return
        cnt = session.query(_UAh).filter(
            _UAh.author_id == user.id,
            _UAh.status == 'active',
        ).update({'last_office_run_at': None}, synchronize_session=False)
        session.commit()
        if cnt == 0:
            msg = "No active agents found." if lang == 'en' else "Активных агентов пока нет — создай в дашборде"
            await message.bot.send_message(message.chat.id, msg)
            return
    finally:
        session.close()
    # Запускаем в background
    try:
        from ai_integration.office_engine import get_office_engine
        engine = get_office_engine()
        asyncio.ensure_future(engine._run_all_agent_scripts())
        msg = (
            f" {cnt} agent(s) launched! Results will appear in chat within 1–2 minutes."
            if lang == 'en'
            else f" Запустил {cnt} агент(ов) — результаты появятся через пару минут"
        )
    except Exception as e:
        logging.error(f"run_agents_handler: {e}")
        msg = "Failed to start agents." if lang == 'en' else "Не получилось запустить агентов — попробуй ещё раз"
    await message.bot.send_message(message.chat.id, msg)





def delegate_task(task_title, executor_username, deadline=None, description=None, delegator_id=None, session=None):
    """Delegate a task to another user with agent control"""
    try:
        from models import Task, User
        import json
        from datetime import datetime, timedelta
        from ai_integration.time_parser import parse_time

        if not session:
            from models import Session
            session = Session()

        # Find executor by username
        executor = session.query(User).filter(
            (User.username == executor_username) | 
            (User.username == f"@{executor_username}")
        ).first()

        if not executor:
            return f"Пользователь @{executor_username} не найден в системе"

        # Parse deadline if provided
        deadline_dt = None
        if deadline:
            try:
                deadline_dt = parse_time(deadline)
            except Exception as e:
                logger.warning(f"Could not parse deadline '{deadline}': {e}")

        # Create the task
        task = Task(
            user_id=executor.id,  # Task belongs to executor
            title=task_title,
            description=description or f"Делегированная задача от пользователя {delegator_id}",
            reminder_time=deadline_dt,
            status='pending',
            delegated_by=delegator_id,
            delegated_to_username=executor_username,
            delegation_status='pending',
            created_at=datetime.now()
        )

        session.add(task)
        session.commit()

        # Create control plan for agent monitoring
        control_plan = {
            "delegator_id": delegator_id,
            "executor_id": executor.id,
            "task_id": task.id,
            "created_at": datetime.now().isoformat(),
            "deadline": deadline_dt.isoformat() if deadline_dt else None,
            "checkpoints": [
                {
                    "type": "acceptance_check",
                    "scheduled_time": (datetime.now() + timedelta(hours=1)).isoformat(),
                    "completed": False,
                    "escalation_level": 0
                },
                {
                    "type": "progress_check",
                    "scheduled_time": (datetime.now() + timedelta(hours=24)).isoformat(),
                    "completed": False,
                    "escalation_level": 0
                }
            ],
            "escalation_levels": [
                {"level": 1, "delay_hours": 24, "message": "Напоминание: задача ожидает принятия"},
                {"level": 2, "delay_hours": 48, "message": " Задача просрочена! Требуется срочное принятие"},
                {"level": 3, "delay_hours": 72, "message": " КРИТИЧНО: Задача не принята в срок! Эскалация руководству"}
            ],
            "agent_monitoring": True,
            "notifications_sent": []
        }

        # Store control plan in task delegation_details (JSON field)
        task.delegation_details = json.dumps(control_plan)
        session.commit()

        # Send notification to executor (skip in test environments)
        notification_text = f""" Новое поручение

 {task_title}
 От: Пользователь {delegator_id}
 Дедлайн: {deadline_dt.strftime('%d.%m.%Y %H:%M') if deadline_dt else 'Не указан'}

{description if description else ''}

Прими или отклони:
 /accept_{task.id} — принять
 /reject_{task.id} — отклонить"""

        try:
            # Schedule async notification
            asyncio.create_task(send_delegation_notification_async(executor.telegram_id, notification_text))
        except RuntimeError:
            # No event loop running (test environment)
            logger.info(f"Would send notification to executor {executor.username}: {notification_text[:100]}...")

        return f"Задача '{task_title}' поручена @{executor_username}"

    except Exception as e:
        if session:
            session.rollback()
        logger.error(f"Error delegating task: {e}")
        return f"Не получилось передать задачу — попробуй ещё раз"


