import logging
import asyncio
import os
import tempfile
import json
import traceback
import time as time_module
import threading
from datetime import datetime, timedelta, timezone
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

router = Router()

# Dedup cache for message processing
message_cache = {}
message_cache_lock = threading.Lock()
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

PREMIUM_DESCRIPTION = """AI отвечают. ASI Biont — действует!

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
Поддержка: @aleksandrinsider"""

try:
    import speech_recognition as sr
    from pydub import AudioSegment
    VOICE_RECOGNITION_AVAILABLE = True
except Exception as e:
    logging.warning(f"Voice recognition not available: {e}")
    VOICE_RECOGNITION_AVAILABLE = False


def transcribe_audio_sync(audio_file_path):
    """
    Синхронная транскрибация аудио файла в текст.
    Использует speech_recognition с Google Speech Recognition.
    """
    if not VOICE_RECOGNITION_AVAILABLE:
        logging.error("Voice recognition libraries not available")
        return None

    wav_path = None
    try:
        # Проверяем существование файла
        if not os.path.exists(audio_file_path):
            logging.error(f"Audio file not found: {audio_file_path}")
            return None
        
        # Конвертируем OGG в WAV для SpeechRecognition
        audio = AudioSegment.from_ogg(audio_file_path)
        wav_path = audio_file_path.replace('.ogg', '.wav')
        audio.export(wav_path, format='wav')

        # Используем Google Speech Recognition (бесплатный, без API ключа)
        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as source:
            audio_data = recognizer.record(source)
            text = recognizer.recognize_google(audio_data, language='ru-RU')
            logging.info(f"Successfully transcribed: {text[:50]}...")
            return text

    except Exception as e:
        logging.error(f"Error transcribing audio: {e}", exc_info=True)
        return None
    finally:
        # Удаляем временный WAV файл
        if wav_path and os.path.exists(wav_path):
            try:
                os.unlink(wav_path)
            except Exception as e:
                logging.warning(f"Failed to remove temporary WAV file {wav_path}: {e}")
                pass


async def transcribe_audio(audio_file_path):
    """Асинхронная обёртка для транскрибации."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, transcribe_audio_sync, audio_file_path)

logger = logging.getLogger(__name__)

@router.message(Command("start"))
async def start_handler(message: Message):
    user_id = message.from_user.id

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
    user = session.query(User).filter_by(telegram_id=user_id).first()
    is_new_user = False
    if not user:
        user = User(telegram_id=user_id, username=message.from_user.username, token_balance=0)
        session.add(user)
        session.commit()
        logger.info(f"Created new user {user_id}")
        is_new_user = True
        # Начисляем бесплатные токены
        grant_signup_tokens(user_id, session=session)
    
    balance = user.token_balance or 0
    session.close()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Открыть веб-версию", web_app=WebAppInfo(url=f"{WEB_APP_URL}/dashboard"))]
    ])
    
    if is_new_user:
        welcome_text = PREMIUM_DESCRIPTION + f"\n\n🎁 Тебе начислено {FREE_TOKENS_ON_SIGNUP} бесплатных токенов! Просто напиши мне."
    else:
        welcome_text = PREMIUM_DESCRIPTION + f"\n\n💰 Твой баланс: {balance} токенов"
    await message.bot.send_message(message.chat.id, welcome_text, reply_markup=keyboard)


@router.message(Command("balance"))
async def balance_handler(message: Message):
    """Показать баланс токенов"""
    user_id = message.from_user.id
    info = get_balance_info(user_id)
    await message.bot.send_message(message.chat.id, info)


@router.message(Command("buy"))
async def buy_handler(message: Message):
    """Покупка пакетов токенов"""
    user_id = message.from_user.id
    
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
        await message.bot.send_message(message.chat.id, text + "\n\n⚠️ Ошибка создания платежа. Попробуйте позже.\nПоддержка: @aleksandrinsider")


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
        await message.bot.send_message(message.chat.id, "Произошла ошибка при поиске партнеров.")


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
                                await message.bot.send_message(
                                    message.chat.id,
                                    "Не удалось распознать голосовое сообщение. Попробуйте отправить текст."
                                )
                                return
                        finally:
                            # Удаляем временный файл
                            os.unlink(tmp_file_path)
                    else:
                        logger.error(f"[VOICE] Failed to download voice file: {resp.status}")
                        await message.bot.send_message(message.chat.id, "Ошибка при скачивании голосового сообщения.")
                        return
        except Exception as e:
            logger.error(f"[VOICE] Error processing voice message: {e}", exc_info=True)
            await message.bot.send_message(message.chat.id, "Произошла ошибка при обработке голосового сообщения.")
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
            await message.bot.send_message(message.chat.id, "Произошла ошибка при обработке файла.")
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
            await message.bot.send_message(message.chat.id, "Произошла ошибка при обработке фото.")
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

    # Duplicate protection - глобальный кеш с threading.Lock
    global message_cache, message_cache_lock
    message_cache_key = f"msg_{user_id}_{message_id}"
    current_time = time_module.time()
    
    with message_cache_lock:
        # Очистка старых записей (старше 60 секунд)
        message_cache = {k: v for k, v in message_cache.items() if current_time - v < 60}
        
        # Проверяем, было ли уже обработано это сообщение
        if message_cache_key in message_cache:
            logger.info(f"[DEDUP] Duplicate message detected: msg_id={message_id}, user={user_id}, skipping")
            return
        
        # Отмечаем сообщение как обработанное
        message_cache[message_cache_key] = current_time
        logger.info(f"[DEDUP] Message registered: msg_id={message_id}, user={user_id}, cache_size={len(message_cache)}")

    try:
        session = Session()
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
        session.close()

        # Новый пользователь — сначала показываем описание и тарифы
        if is_first_message:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Открыть веб-версию", web_app=WebAppInfo(url=f"{WEB_APP_URL}/dashboard"))]
            ])
            balance = get_balance(user_id)
            welcome_text = PREMIUM_DESCRIPTION + f"\n\n1 500 бесплатных токенов начислено. Просто напиши мне."
            await message.bot.send_message(message.chat.id, welcome_text, reply_markup=keyboard)

        # Проверка баланса токенов
        if not FREE_ACCESS_MODE and not has_enough_tokens(user_id, 'message'):
            await message.bot.send_message(message.chat.id, insufficient_balance_message(user_id, 'message'))
            return

        # Handle delegation commands
        if text.lower().startswith("принять задачу "):
            task_id = text.split()[-1]
            try:
                from ai_integration import accept_delegated_task
                result = accept_delegated_task(int(task_id), user_id=user_id)
                await message.bot.send_message(message.chat.id, result)
            except Exception as e:
                await message.bot.send_message(message.chat.id, f"Ошибка: {str(e)}")
            return

        if text.lower().startswith("отклонить задачу "):
            task_id = text.split()[-1]
            try:
                from ai_integration import reject_delegated_task
                result = reject_delegated_task(int(task_id), user_id=user_id)
                await message.bot.send_message(message.chat.id, result)
            except Exception as e:
                await message.bot.send_message(message.chat.id, f"Ошибка: {str(e)}")
            return

        if text.lower() == "очистить историю":
            # Update user.history_cleared_at in DB
            session = Session()
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user:
                from datetime import datetime, timezone
                user.history_cleared_at = datetime.now(timezone.utc)
                session.commit()
            session.close()
            await message.bot.send_message(message.chat.id, "История очищена.")
            return

        # СОХРАНЯЕМ СООБЩЕНИЕ ПОЛЬЗОВАТЕЛЯ СРАЗУ
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user:
                from models import Interaction
                interaction = Interaction(user_id=user.id, message_type='user', content=text)
                session.add(interaction)
                session.commit()
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
            try:
                if _progress_state['last_msg_id']:
                    # Обновляем существующее сообщение (не спамим чат)
                    try:
                        await bot.edit_message_text(
                            text=text,
                            chat_id=chat_id,
                            message_id=_progress_state['last_msg_id']
                        )
                        return
                    except Exception:
                        pass  # Если не удалось обновить — отправим новое
                
                sent = await bot.send_message(chat_id, text)
                _progress_state['last_msg_id'] = sent.message_id
            except Exception as e:
                logger.warning(f"Progress callback error: {e}")
        
        # Use autonomous agent instead of command router
        from ai_integration import chat_with_ai
        db_session = Session()
        response_text = ""
        try:
            result = await chat_with_ai(text, context=context, user_id=user_id, db_session=db_session, progress_callback=progress_callback)
            response_text = result.get('response', '') if isinstance(result, dict) else str(result)
            
            # Удаляем прогресс-сообщение перед финальным ответом
            if _progress_state['last_msg_id']:
                try:
                    await bot.delete_message(chat_id, _progress_state['last_msg_id'])
                except Exception:
                    pass
            
            # Защита от пустого ответа
            if not response_text or not response_text.strip():
                response_text = "Готово! Что дальше?"
            
            await message.bot.send_message(message.chat.id, response_text)
            
            # Списываем токены за сообщение
            if not FREE_ACCESS_MODE:
                spend_tokens(user_id, 'message', description=text[:100])
        except Exception as e:
            logger.error(f"Error in autonomous chat for user {user_id}: {e}", exc_info=True)
            await message.bot.send_message(message.chat.id, "Извините, произошла ошибка при обработке сообщения.")
            response_text = ""
        finally:
            db_session.close()
        
        # СОХРАНЯЕМ ОТВЕТ AI
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user:
                from models import Interaction
                if response_text and response_text.strip():
                    interaction = Interaction(user_id=user.id, message_type='ai', content=response_text.strip())
                else:
                    interaction = Interaction(
                        user_id=user.id,
                        message_type='ai',
                        content="Извините, не удалось сгенерировать ответ.")
                session.add(interaction)
                session.commit()
            else:
                logger.warning(f"User not found for telegram_id {user_id}, cannot save interactions")
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Error in process_text_message for user {user_id}: {e}", exc_info=True)
        try:
            await message.bot.send_message(message.chat.id, "Произошла техническая ошибка. Попробуй написать ещё раз.")
        except Exception:
            pass


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

    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            if should_close:
                session.close()
            return "Пользователь не найден"

        # Задачи, делегированные ОТ пользователя (кому он делегировал)
        delegated_by_user = session.query(Task).filter(
            Task.delegated_by == user.id
        ).order_by(Task.created_at.desc()).all()

        # Задачи, делегированные ПОЛЬЗОВАТЕЛЮ (кто делегировал ему)
        delegated_to_user = session.query(Task).filter(
            Task.delegated_to_username.ilike(user.username.replace('@', '') if user.username else ''),
            Task.delegation_status.isnot(None)
        ).order_by(Task.created_at.desc()).all()

        report = []

        if delegated_by_user:
            report.append("📤 ВАШИ ДЕЛЕГИРОВАННЫЕ ЗАДАЧИ:")
            for task in delegated_by_user[:10]:  # Ограничим 10 задачами
                status_emoji = {
                    None: "⏳",
                    "pending": "⏳",
                    "accepted": "✅",
                    "rejected": "❌",
                    "completed": "🎉"
                }.get(task.delegation_status, "❓")

                status_text = {
                    None: "ожидает принятия",
                    "pending": "ожидает принятия",
                    "accepted": "принята в работу",
                    "rejected": "отклонена",
                    "completed": "завершена"
                }.get(task.delegation_status, "неизвестный статус")

                report.append(f"{status_emoji} '{task.title}' → @{task.delegated_to_username}")
                report.append(f"   Статус: {status_text}")

                if task.completion_notes:
                    report.append(f"   Результат: {task.completion_notes[:100]}...")

                if task.due_date:
                    report.append(f"   Дедлайн: {task.due_date.strftime('%d.%m.%Y %H:%M')}")

                report.append("")  # Пустая строка между задачами

        if delegated_to_user:
            report.append("📥 ЗАДАЧИ, ДЕЛЕГИРОВАННЫЕ ВАМ:")
            for task in delegated_to_user[:10]:
                delegator = session.query(User).filter_by(id=task.delegated_by).first()
                delegator_name = f"@{delegator.username}" if delegator and delegator.username else "неизвестный"

                status_emoji = {
                    "pending": "⏳",
                    "accepted": "✅",
                    "rejected": "❌",
                    "completed": "🎉"
                }.get(task.delegation_status, "❓")

                status_text = {
                    "pending": "ожидает вашего решения",
                    "accepted": "вы работаете над ней",
                    "rejected": "вы отклонили",
                    "completed": "завершена"
                }.get(task.delegation_status, "неизвестный статус")

                report.append(f"{status_emoji} '{task.title}' от {delegator_name}")
                report.append(f"   Статус: {status_text}")

                if task.completion_notes:
                    report.append(f"   Результат: {task.completion_notes[:100]}...")

                if task.due_date:
                    report.append(f"   Дедлайн: {task.due_date.strftime('%d.%m.%Y %H:%M')}")

                report.append("")

        if not delegated_by_user and not delegated_to_user:
            report.append("У вас нет делегированных задач.")

        if should_close:
            session.close()

        return f"DELEGATION_REPORT: {report}"

    except Exception as e:
        logger.error(f"Error getting delegation progress for user {user_id}: {e}")
        if should_close:
            session.close()
        return f"Ошибка при получении отчета о делегировании: {str(e)}"



# NOTE: delegate_task для AI-агента  в ai_integration/handlers.py
# Дубль #1 удалён как мёртвый код

@router.message(Command("dashboard"))
async def dashboard_handler(message: Message):
    user_id = message.from_user.id
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        await message.bot.send_message(message.chat.id, "Сначала зарегистрируйтесь, отправив /start")
        session.close()
        return
    session.close()

    # Generate dashboard URL
    base_url = WEBHOOK_URL.replace("/webhook", "")
    dashboard_url = f"{base_url}/dashboard?telegram_id={user_id}"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 Открыть веб-версию", web_app=WebAppInfo(url=dashboard_url))]
    ])
    
    await message.bot.send_message(
        message.chat.id, 
        f"🌐 Ваш личный дашборд:\n{dashboard_url}", 
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


