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
from timezonefinder import TimezoneFinder
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

Не чат-бот, а AI-агент. Сам ведёт задачи, находит исполнителей, делегирует и постит контент в Telegram. Без промптов — просто скажите, что нужно. 1 500 токенов при регистрации.

Задачи и цели
«Завтра в 9 созвон с инвестором» — он поставит напоминание. «Сдать отчёт в пятницу» — зафиксирует дедлайн и проконтролирует. Вы говорите — он действует.

Готовые связи
«Нужен дизайнер для проекта» — покажет подходящих людей из сети. «Кто разбирается в финтехе?» — найдёт по навыкам и городу. Не открытый каталог, а закрытое сообщество.

Делегирование
«Поручи @ivan подготовить отчёт до среды» — задача уйдёт исполнителю, дедлайн встанет на контроль. Агент отследит сроки и напомнит обеим сторонам.

Автопилот 24/7
Вы спите — он публикует в канал по расписанию. Вы на встрече — он отправляет утреннюю сводку. Просрочена задача — напомнит сам, без вашего участия.

Точечные возможности
Новый участник с нужными навыками в вашем городе — агент сообщит. Контакт обновил профиль — вы узнаете первым. Не шум, а релевантные сигналы.

Бот + дашборд
В кармане — Telegram-бот для команд голосом и текстом. На экране — дашборд с задачами, контактами и лентой. Единая база, два интерфейса.

━━━━━━━━━━━━━━━━━━━━

Пополните баланс токенов
Все функции открыты для всех. Платите только за использование. 1 токен = 1 рубль.
При регистрации — 1 500 бесплатных токенов.

Стартовый — 1 500 руб.
1 500 токенов. Для знакомства с AI-партнёром.
«Напомни завтра в 9 про созвон» — он помнит. «Найди дизайнера» — находит. Все функции открыты с первого токена.

Оптимальный — 5 000 руб. ВЫГОДНЫЙ
5 500 токенов (+10%). Самый популярный выбор.
+500 токенов сверху. Вы на встрече — он делегирует задачи. Вы спите — постит в канал. Автопилот, который не выключается.

Максимальный — 15 000 руб.
18 000 токенов (+20%). Максимальная выгода за токен.
+3 000 токенов сверху. Контент, делегирование, алерты, инсайты — всё на автопилоте. Вы растёте, пока AI работает.

Пополнить баланс: /buy
Баланс: /balance
Поддержка: @aleksandrinsider""",

    'en': """AIs answer. ASI Biont — acts!

Not a chatbot, but an AI agent. It manages tasks, finds the right people, delegates, and posts content to Telegram. No prompts needed — just tell it what you need. 1,500 free tokens on signup.

Tasks & Goals
"Call with investor tomorrow at 9" — it sets a reminder. "Report due Friday" — it locks the deadline and follows up. You speak — it acts.

Ready Connections
"Need a designer for a project" — it shows matching people from the network. "Who knows fintech?" — finds by skills and city. Not an open directory — a closed community.

Delegation
"Assign @ivan the report by Wednesday" — the task goes to the assignee, deadline goes on track. The agent monitors deadlines and reminds both sides.

Autopilot 24/7
You sleep — it posts to your channel on schedule. You're in a meeting — it sends a morning briefing. Task overdue — it reminds you on its own.

Pinpoint Capabilities
A new member with the right skills in your city — the agent notifies you. A contact updated their profile — you learn first. Not noise, but relevant signals.

Bot + Dashboard
In your pocket — a Telegram bot for voice and text commands. On screen — a dashboard with tasks, contacts, and feed. One database, two interfaces.

━━━━━━━━━━━━━━━━━━━━

Top Up Your Token Balance
All features are open to everyone. Pay only for usage. 1 token = 1₽.
On signup — 1,500 free tokens.

Starter — 1,500₽
1,500 tokens. Get to know your AI partner.
"Remind me about the call tomorrow at 9" — it remembers. "Find a designer" — it finds. All features unlocked from the first token.

Optimal — 5,000₽ BEST VALUE
5,500 tokens (+10%). Most popular choice.
+500 tokens on top. You're in a meeting — it delegates tasks. You sleep — it posts to your channel. Autopilot that never stops.

Maximum — 15,000₽
18,000 tokens (+20%). Best value per token.
+3,000 tokens on top. Content, delegation, alerts, insights — all on autopilot. You grow while AI works.

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
            welcome_text = desc + f"\n\n🎁 You've received {FREE_TOKENS_ON_SIGNUP} free tokens! Just write to me."
        else:
            welcome_text = desc + f"\n\n🎁 Тебе начислено {FREE_TOKENS_ON_SIGNUP} бесплатных токенов! Просто напиши мне."
    else:
        if lang == 'en':
            welcome_text = desc + f"\n\n💰 Your balance: {balance} tokens"
        else:
            welcome_text = desc + f"\n\n💰 Твой баланс: {balance} токенов"
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
        text = "📦 Token packages (1 token = 1₽):\n\n"
        for key, pkg in TOKEN_PACKAGES.items():
            text += f"• {pkg['label']}\n"
        text += "\nChoose a package:"
    else:
        text = "📦 Пакеты токенов (1 токен = 1₽):\n\n"
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
        err_msg = "⚠️ Payment error. Try again later.\nSupport: @aleksandrinsider" if lang == 'en' else "⚠️ Ошибка создания платежа. Попробуйте позже.\nПоддержка: @aleksandrinsider"
        await message.bot.send_message(message.chat.id, text + "\n\n" + err_msg)


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

            if not FREE_ACCESS_MODE and not has_enough_tokens(user_id, 'message'):
                await message.bot.send_message(message.chat.id, insufficient_balance_message(user_id, 'message'))
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
                                await process_text_message(user_id, text, message, None)
                                return
                            else:
                                err = "Couldn't recognize voice message. Try sending text." if get_user_lang(user_id) == 'en' else "Не удалось распознать голосовое сообщение. Попробуйте отправить текст."
                                await message.bot.send_message(
                                    message.chat.id, err
                                )
                                return
                        finally:
                            # Удаляем временный файл
                            os.unlink(tmp_file_path)
                    else:
                        logger.error(f"[VOICE] Failed to download voice file: {resp.status}")
                        err = "Error downloading voice message." if get_user_lang(user_id) == 'en' else "Ошибка при скачивании голосового сообщения."
                        await message.bot.send_message(message.chat.id, err)
                        return
        except Exception as e:
            logger.error(f"[VOICE] Error processing voice message: {e}", exc_info=True)
            err = "An error occurred processing the voice message." if get_user_lang(user_id) == 'en' else "Произошла ошибка при обработке голосового сообщения."
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
                await message.bot.send_message(message.chat.id, "📄 Файл слишком большой (макс 5 МБ). Отправь файл поменьше.")
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


async def process_text_message(user_id, text, message, state):
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
        
        async def progress_callback(text):
            """Отправляет или обновляет прогресс-сообщение в Telegram"""
            display_text = text or ('Processing...' if lang == 'en' else 'Обрабатываю...')
            try:
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
        try:
            result = await chat_with_ai(text, context=context, user_id=user_id, db_session=db_session, progress_callback=progress_callback)
            response_text = result.get('response', '') if isinstance(result, dict) else str(result)
            logger.info(f"[PTM] Step 3a: got response, len={len(response_text)}")
            
            # Удаляем прогресс-сообщение перед финальным ответом
            if _progress_state['last_msg_id']:
                try:
                    await bot.delete_message(chat_id, _progress_state['last_msg_id'])
                except Exception:
                    pass
            
            # Защита от пустого ответа
            if not response_text or not response_text.strip():
                response_text = "Done! What's next?" if lang == 'en' else "Готово! Что дальше?"
            
            # Гарантируем кликабельность ссылок в Telegram (HTML parse_mode)
            response_html = _format_html(response_text)

            # Разбиваем длинный ответ на части (Telegram лимит 4096)
            if len(response_html) > 4000:
                for i in range(0, len(response_html), 4000):
                    chunk = response_html[i:i+4000]
                    try:
                        await message.bot.send_message(message.chat.id, chunk, parse_mode='HTML')
                    except Exception:
                        await message.bot.send_message(message.chat.id, response_text[i:i+4000])
            else:
                try:
                    await message.bot.send_message(message.chat.id, response_html, parse_mode='HTML')
                except Exception as html_err:
                    # Fallback without HTML if formatting breaks
                    logger.warning(f"[PTM] HTML send failed, falling back to plain text: {html_err}")
                    await message.bot.send_message(message.chat.id, response_text)
            
            # Списываем токены за сообщение
            if not FREE_ACCESS_MODE:
                spend_tokens(user_id, 'message', description=text[:100])
        except Exception as e:
            logger.error(f"Error in autonomous chat for user {user_id}: {e}", exc_info=True)
            try:
                err_msg = "Sorry, an error occurred while processing your message." if lang == 'en' else "Извините, произошла ошибка при обработке сообщения."
                await message.bot.send_message(message.chat.id, err_msg)
            except Exception:
                logger.error(f"Failed to send error message to user {user_id}")
            response_text = ""
        finally:
            db_session.close()
        
        # СОХРАНЯЕМ ОТВЕТ AI
        logger.info(f"[PTM] Step 4: saving AI response")
        try:
            session = Session()
            try:
                user = session.query(User).filter_by(telegram_id=user_id).first()
                if user:
                    if response_text and response_text.strip():
                        interaction = Interaction(user_id=user.id, message_type='ai', content=response_text.strip())
                    else:
                        fallback_content = "Sorry, could not generate a response." if lang == 'en' else "Извините, не удалось сгенерировать ответ."
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
    except Exception as e:
        logger.error(f"Error in process_text_message for user {user_id}: {e}", exc_info=True)
        import traceback
        error_detail = traceback.format_exc()
        logger.error(f"FULL TRACEBACK: {error_detail}")
        try:
            # Показываем детали ошибки для отладки
            short_error = str(e)[:300]
            if lang == 'en':
                err_text = f"⚠️ Error: {short_error}\n\nPlease try again."
            else:
                err_text = f"⚠️ Ошибка: {short_error}\n\nПопробуй написать ещё раз."
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
        tf = TimezoneFinder()
        timezone_str = tf.timezone_at(lng=lon, lat=lat)
        
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
                        logger.info(f"[GEO] Updated city for user {user_id}: {old_city} → {city_name}")
                    else:
                        profile = UserProfile(user_id=user.id, city=city_name)
                        session.add(profile)
                
                session.commit()
                
                # 4. Формируем ответ
                response_parts = []
                if timezone_str:
                    response_parts.append(f"🕐 Часовой пояс: {timezone_str}")
                if city_name:
                    response_parts.append(f"📍 Город: {city_name}")
                
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
                        nearby_lines = []
                        for p in nearby_profiles:
                            partner_user = session.query(User).filter_by(id=p.user_id).first()
                            if partner_user and partner_user.username:
                                info = []
                                if p.skills:
                                    info.append(p.skills[:50])
                                if p.interests:
                                    info.append(p.interests[:50])
                                detail = f" — {', '.join(info)}" if info else ""
                                nearby_lines.append(f"• @{partner_user.username}{detail}")
                        
                        if nearby_lines:
                            nearby_text = f"\n\n👥 Люди рядом ({city_name}):\n" + "\n".join(nearby_lines)
                            nearby_text += "\n\n💡 Напиши «найди партнёра для [задача]» — подберу лучших."
                
                await message.bot.send_message(
                    message.chat.id, 
                    f"📍 Локация обновлена!\n{response_text}{nearby_text}"
                )
            else:
                await message.bot.send_message(message.chat.id, "Пользователь не найден.")
        except Exception as e:
            logger.error(f"[GEO] Error processing location: {e}", exc_info=True)
            await message.bot.send_message(message.chat.id, "Ошибка при обработке геолокации. Попробуйте позже.")
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
            header_by = "📤 YOUR DELEGATED TASKS:" if lang == 'en' else "📤 ВАШИ ДЕЛЕГИРОВАННЫЕ ЗАДАЧИ:"
            report.append(header_by)
            for task in delegated_by_user[:10]:
                status_emoji = {
                    None: "⏳", "pending": "⏳", "accepted": "✅", "rejected": "❌", "completed": "🎉"
                }.get(task.delegation_status, "❓")

                status_text = status_texts_by.get(lang, status_texts_by['ru']).get(task.delegation_status, unknown_status)

                report.append(f"{status_emoji} '{task.title}' → @{task.delegated_to_username}")
                report.append(f"   {lbl_status}: {status_text}")

                if task.completion_notes:
                    report.append(f"   {lbl_result}: {task.completion_notes[:100]}...")

                if task.due_date:
                    report.append(f"   {lbl_deadline}: {task.due_date.strftime('%d.%m.%Y %H:%M')}")

                report.append("")

        if delegated_to_user:
            header_to = "📥 TASKS DELEGATED TO YOU:" if lang == 'en' else "📥 ЗАДАЧИ, ДЕЛЕГИРОВАННЫЕ ВАМ:"
            report.append(header_to)
            for task in delegated_to_user[:10]:
                delegator = session.query(User).filter_by(id=task.delegated_by).first()
                unknown_lbl = "unknown" if lang == 'en' else "неизвестный"
                delegator_name = f"@{delegator.username}" if delegator and delegator.username else unknown_lbl

                status_emoji = {
                    "pending": "⏳", "accepted": "✅", "rejected": "❌", "completed": "🎉"
                }.get(task.delegation_status, "❓")

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
            no_tasks = "You have no delegated tasks." if lang == 'en' else "У вас нет делегированных задач."
            report.append(no_tasks)

        if should_close:
            session.close()

        sep = '\n'
        return f"DELEGATION_REPORT: {sep.join(report)}"

    except Exception as e:
        logger.error(f"Error getting delegation progress for user {user_id}: {e}")
        if should_close:
            session.close()
        err_prefix = "Error getting delegation report" if lang == 'en' else "Ошибка при получении отчета о делегировании"
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
        msg = "Please register first by sending /start" if lang == 'en' else "Сначала зарегистрируйтесь, отправив /start"
        await message.bot.send_message(message.chat.id, msg)
        session.close()
        return
    session.close()

    # Generate dashboard URL
    base_url = WEBHOOK_URL.replace("/webhook", "")
    dashboard_url = f"{base_url}/dashboard?telegram_id={user_id}"
    
    btn_text = "🌐 Open web version" if lang == 'en' else "🌐 Открыть веб-версию"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=btn_text, web_app=WebAppInfo(url=dashboard_url))]
    ])
    
    dash_label = "🌐 Your personal dashboard" if lang == 'en' else "🌐 Ваш личный дашборд"
    await message.bot.send_message(
        message.chat.id, 
        f"{dash_label}:\n{dashboard_url}", 
        reply_markup=keyboard
    )


# Business logic functions for command handlers


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
                {"level": 2, "delay_hours": 48, "message": "⚠️ Задача просрочена! Требуется срочное принятие"},
                {"level": 3, "delay_hours": 72, "message": "🚨 КРИТИЧНО: Задача не принята в срок! Эскалация руководству"}
            ],
            "agent_monitoring": True,
            "notifications_sent": []
        }

        # Store control plan in task delegation_details (JSON field)
        task.delegation_details = json.dumps(control_plan)
        session.commit()

        # Send notification to executor (skip in test environments)
        notification_text = f"""🤖 Новая делегированная задача

📋 {task_title}
👤 От: Пользователь {delegator_id}
⏰ Дедлайн: {deadline_dt.strftime('%d.%m.%Y %H:%M') if deadline_dt else 'Не указан'}

{description if description else ''}

Используйте команды:
✅ /accept_{task.id} - принять задачу
❌ /reject_{task.id} - отклонить задачу"""

        try:
            # Schedule async notification
            asyncio.create_task(send_delegation_notification_async(executor.telegram_id, notification_text))
        except RuntimeError:
            # No event loop running (test environment)
            logger.info(f"Would send notification to executor {executor.username}: {notification_text[:100]}...")

        return f"Задача '{task_title}' успешно делегирована пользователю @{executor_username}"

    except Exception as e:
        if session:
            session.rollback()
        logger.error(f"Error delegating task: {e}")
        return f"Ошибка при делегировании задачи: {str(e)}"


