import json
import logging
import asyncio
import os
import tempfile
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

router = Router()
from ai_integration import chat_with_ai
from models import Session, User, Subscription
from config import WEBHOOK_URL
from config import WEB_APP_URL, FREE_ACCESS_MODE
from timezonefinder import TimezoneFinder

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

PREMIUM_DESCRIPTION = """🎯 Закрытое сообщество от лаборатории искусственного интеллекта ASI Biont с живым поиском единомышленников через ваши текущие дела: система анализирует задачи и цели, находя партнеров в вашем городе. AI-агент запоминает контекст вашей жизни, связывает задачи, понимает голосовые сообщения и предлагает умные шаги с напоминаниями. Панель управления объединяет всё: задачи, партнеры, встречи, историю взаимодействий и статистику.

3000₽/месяц — доступ к экосистеме + AI-инструменты

✓ Живой поиск людей через ваши дела
Система анализирует ваши задачи и цели, находя тех, кто занят похожим прямо сейчас в вашем городе. Не статичный профиль, а профиль, который обновляется с вашими делами. Здесь знакомства превращаются в проекты, дружбу и реальные результаты.

✓ AI-агент, который понимает контекст вашей жизни
Агент помнит всё, связывает задачи, предлагает следующие шаги. Понимает голосовые сообщения — просто говорите в микрофон. Умные напоминания учитывают дедлайны.

✓ Панель управления всей экосистемой
Все задачи, партнёры, встречи — в одном месте. Отслеживайте свои дедлайны, делегируйте задачи через чат с AI. Видите историю взаимодействий с партнёрами, статистику выполненных задач.

✓ Надёжная конфиденциальность
Никто не видит ваши реальные задачи. Система работает с ключевыми словами — "стартап", "маркетинг", "бег" — для поиска партнёров. End-to-end шифрование всех данных. Ваши планы, переписки, контакты доступны только вам. Полный контроль над тем, кто и что о вас знает.

Станьте частью сообщества созидателей — /subscribe

Поддержка: @aleksandrinsider"""

logger = logging.getLogger(__name__)

# Global Redis client
redis_client = None


async def init_redis(client):
    global redis_client
    redis_client = client


@router.message(Command("start"))
async def start_handler(message: Message):
    user_id = message.from_user.id

    # Check for duplicate message processing
    message_key = f"processed_message:{message.message_id}"
    if redis_client:
        if await redis_client.exists(message_key):
            logger.info(f"Duplicate /start message {message.message_id} ignored")
            return
        await redis_client.set(message_key, "1", ex=3600)

    # Create user if doesn't exist
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(telegram_id=user_id, username=message.from_user.username)
        session.add(user)
        session.commit()
        logger.info(f"Created new user {user_id}")
    session.close()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Открыть веб-версию", web_app=WebAppInfo(url=f"{WEB_APP_URL}/dashboard"))]
    ])
    await message.bot.send_message(message.chat.id, PREMIUM_DESCRIPTION, reply_markup=keyboard)


@router.message(Command("update_profile"))
async def update_profile_handler(message: Message):
    user_id = message.from_user.id
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(telegram_id=user_id, username=message.from_user.username)
        session.add(user)
        session.commit()
    subscription = session.query(Subscription).filter_by(user_id=user.id).first()
    if not subscription or subscription.status != 'active':
        await message.bot.send_message(message.chat.id, PREMIUM_DESCRIPTION)
        session.close()
        return
    session.close()
    # Отправить запрос в ИИ
    text = message.text.replace("/update_profile", "").strip()
    if text:
        prompt = f"Обнови мой профиль: {text}"
    else:
        prompt = "Помоги обновить профиль"
    try:
        if redis_client:
            context_data = redis_client.get(f"context:{user_id}")
            if context_data:
                if isinstance(context_data, bytes):
                    context = json.loads(context_data.decode('utf-8'))
                else:
                    context = json.loads(context_data)
        else:
            context = []
    except Exception:
        context = []
    response = await chat_with_ai(prompt, context, user_id)
    await message.bot.send_message(message.chat.id, response)
    # Сохранить контекст
    context.append({"user": prompt, "agent": response})
    if len(context) > 10:
        context = context[-10:]
    try:
        if redis_client:
            redis_client.set(f"context:{user_id}", json.dumps(context).encode('utf-8'))
    except Exception:
        pass


@router.message(Command("find_partners"))
async def find_partners_handler(message: Message):
    user_id = message.from_user.id
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(telegram_id=user_id, username=message.from_user.username)
        session.add(user)
        session.commit()
    subscription = session.query(Subscription).filter_by(user_id=user.id).first()
    if not subscription or subscription.status != 'active':
        await message.bot.send_message(message.chat.id, PREMIUM_DESCRIPTION)
        session.close()
        return
    session.close()
    # Отправить запрос в ИИ
    try:
        if redis_client:
            context_data = redis_client.get(f"context:{user_id}")
            if context_data:
                if isinstance(context_data, bytes):
                    context = json.loads(context_data.decode('utf-8'))
                else:
                    context = json.loads(context_data)
        else:
            context = []
    except Exception:
        context = []
    response = await chat_with_ai("Найди партнеров", context, user_id)
    await message.bot.send_message(message.chat.id, response)
    # Сохранить контекст
    context.append({"user": "Найди партнеров", "agent": response})
    if len(context) > 10:
        context = context[-10:]
    try:
        if redis_client:
            redis_client.set(f"context:{user_id}", json.dumps(context).encode('utf-8'))
    except Exception:
        pass


@router.message(Command("subscribe"))
async def subscribe_handler(message: Message):
    user_id = message.from_user.id
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(telegram_id=user_id, username=message.from_user.username)
        session.add(user)
        session.commit()
    subscription = session.query(Subscription).filter_by(user_id=user.id).first()
    if subscription and subscription.status == 'active':
        await message.bot.send_message(message.chat.id, "У вас уже активная подписка!")
        session.close()
        return
    # Создать платеж на 3000 RUB
    from payments import create_payment
    payment_url = create_payment(3000, "Подписка на премиум-функции бота (месяц)", user_id)
    await message.bot.send_message(message.chat.id, f"Оплатите подписку удобным способом:\n\nСсылка на оплату (ЮКАССА, СБЕР или банковская карта): {payment_url}\n\nПосле оплаты подписка активируется мгновенно — никаких задержек!")
    session.close()


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
            # Проверка подписки
            session = Session()
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                user = User(telegram_id=user_id, username=message.from_user.username)
                session.add(user)
                session.commit()
            subscription = session.query(Subscription).filter_by(user_id=user.id).first()
            session.close()

            if not FREE_ACCESS_MODE and (not subscription or subscription.status != 'active'):
                await message.bot.send_message(message.chat.id, PREMIUM_DESCRIPTION)
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

    # Обработка текстовых сообщений
    if message.text:
        await process_text_message(user_id, message.text, message, None)
    else:
        # Обработка других типов сообщений (геолокация и т.д.)
        await process_other_message(user_id, message, None)


async def process_text_message(user_id, text, message, state):
    message_id = message.message_id

    logger.info(f"[HANDLER START] Received message {message_id} from user {user_id}: {text[:50]}")

    try:
        # Check for duplicate message processing
        message_key = f"processed_message:{message_id}:{user_id}"
        if redis_client:
            logger.debug(f"[REDIS] Checking duplicate for key: {message_key}")
            is_duplicate = await redis_client.exists(message_key)
            if is_duplicate:
                logger.warning(
                    f"[DUPLICATE BLOCKED] Message {message_id} from user {user_id} IGNORED (already processed)")
                return
            # Set key with longer expiration
            await redis_client.set(message_key, "1", ex=3600)
            logger.info(f"[REDIS OK] Marked message {message_id} as processed, will respond")
        else:
            logger.warning(f"[NO REDIS] Cannot prevent duplicates for message {message_id}")

        session = Session()
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            user = User(telegram_id=user_id, username=message.from_user.username)
            session.add(user)
            session.commit()
        subscription = session.query(Subscription).filter_by(user_id=user.id).first()
        session.close()
        if not FREE_ACCESS_MODE and (not subscription or subscription.status != 'active'):
            await message.bot.send_message(message.chat.id, PREMIUM_DESCRIPTION)
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
            context = []
            if redis_client:
                try:
                    await redis_client.set(f"context:{user_id}", json.dumps(context).encode('utf-8'))
                except Exception as e:
                    logger.error(f"Error saving context to Redis: {e}")
            await message.bot.send_message(message.chat.id, "История очищена.")
            return

        context = []
        if redis_client:
            try:
                context_data = await redis_client.get(f"context:{user_id}")
                logger.debug(f"Redis get for user {user_id}: {context_data}")
                if context_data:
                    context = json.loads(context_data.decode('utf-8'))
                    logger.info(f"Loaded context for user {user_id}: {len(context)} messages")
                else:
                    context = []
                    logger.debug(f"No context found for user {user_id}")
            except Exception as e:
                logger.error(f"Error loading context from Redis: {e}", exc_info=True)
                context = []
        else:
            logger.warning("Redis client not initialized, context will not persist")
        response = await chat_with_ai(text, context, user_id)
        logger.debug(f"AI response generated for user {user_id}: '{response[:100]}...'")
        logger.debug(f"[HANDLER] Response from chat_with_ai: '{response[:200]}...'")
        # Сохранить контекст для продолжения
        context.append({"user": text, "agent": response})
        if len(context) > 10:
            context = context[-10:]  # Keep last 10 exchanges
        if redis_client:
            try:
                context_json = json.dumps(context).encode('utf-8')
                await redis_client.set(f"context:{user_id}", context_json)
                logger.info(f"Saved context for user {user_id}: {len(context)} messages")
            except Exception as e:
                logger.error(f"Error saving context to Redis: {e}", exc_info=True)
        else:
            logger.warning("Redis client not initialized, context not saved")

        if response and response.strip():
            try:
                logger.info(
                    f"[SENDING] Sending response to user {user_id}, chat {message.chat.id}, message_id={message_id}")
                await message.bot.send_message(message.chat.id, response.strip())
                logger.info(f"[SENT OK] Response sent successfully to user {user_id}")
            except Exception as e:
                logger.error(f"Error sending message to {message.chat.id}: {e}")
                await message.bot.send_message(message.chat.id, "Извините, произошла ошибка при отправке ответа.")
        else:
            logger.warning(f"Empty response from AI for user {user_id}, sending error message")
            await message.bot.send_message(message.chat.id, "Извините, не удалось сгенерировать ответ. Попробуйте переформулировать вопрос.")
        # Записать взаимодействие для проактивных проверок
        session = Session()
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if user:
            from models import Interaction
            logger.info(f"Saving user interaction: user_id={user.id}, content='{text[:50]}...'")
            interaction = Interaction(user_id=user.id, message_type='user', content=text)
            session.add(interaction)
            if response and response.strip():
                logger.info(f"Saving AI interaction: user_id={user.id}, content='{response.strip()[:50]}...'")
                interaction = Interaction(user_id=user.id, message_type='ai', content=response.strip())
            else:
                logger.info(f"Saving error AI interaction: user_id={user.id}")
                interaction = Interaction(
                    user_id=user.id,
                    message_type='ai',
                    content="Извините, не удалось сгенерировать ответ.")
            session.add(interaction)
            session.commit()
            logger.info(f"Interactions saved successfully for user {user.id}")
        else:
            logger.warning(f"User not found for telegram_id {user_id}, cannot save interactions")
        session.close()
    except Exception as e:
        logger.error(f"Error in process_text_message for user {user_id}: {e}", exc_info=True)
        await message.bot.send_message(message.chat.id, "Извините, произошла ошибка. Попробуйте позже.")


async def process_other_message(user_id, message, state):
    # Обработка геолокации
    if message.location:
        lat = message.location.latitude
        lon = message.location.longitude
        tf = TimezoneFinder()
        timezone_str = tf.timezone_at(lng=lon, lat=lat)
        if timezone_str:
            session = Session()
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user:
                user.timezone = timezone_str
                session.commit()
                await message.bot.send_message(message.chat.id, f"Ваш часовой пояс установлен на {timezone_str}. Теперь время будет рассчитываться автоматически!")
            session.close()
        else:
            await message.bot.send_message(message.chat.id, "Не удалось определить часовой пояс по вашим координатам. Попробуйте указать его вручную через диалог.")
        return


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
    await message.bot.send_message(message.chat.id, f"Ваш личный дашборд: {dashboard_url}")