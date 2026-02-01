import logging
import asyncio
import os
import tempfile
import json
from datetime import datetime, timedelta, timezone
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

router = Router()
from ai_integration import chat_with_ai
from ai_integration.router import CommandRouter
from models import Session, User, Subscription, Task
from config import WEBHOOK_URL
from config import WEB_APP_URL, FREE_ACCESS_MODE
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

PREMIUM_DESCRIPTION = """
ASI Biont - лаборатория ИИ

Начните использовать ASI Biont за несколько минут: просто опишите свои текущие задачи в чате с AI-агентом, и платформа автоматически создаст профиль, найдёт релевантных людей и предложит первые шаги. Интуитивный интерфейс в Telegram и веб-панель — всё необходимое под рукой для продуктивной работы и общения.

Предиктивная аналитика: AI прогнозирует риски и предлагает решения до того, как проблемы возникнут.

Поиск партнёров: Найдите единомышленников в вашем городе для совместных проектов и целей.

Интеллектуальные рекомендации: Получите персонализированные советы по оптимизации задач с учётом всех факторов.

Умные напоминания: Автоматические уведомления о дедлайнах и прогрессе для своевременного выполнения.

Делегирование задач: Поручите выполнение с AI-контролем и отслеживанием прогресса.

Голосовое управление: Диктуйте задачи и общайтесь с AI голосом для естественного взаимодействия.

Память и контекст: AI сохраняет вашу историю для более точного понимания и предложений.

Веб-дашборд: Просматривайте все данные в удобном интерфейсе с аналитикой и статистикой.

Telegram-интеграция: Полный функционал в мессенджере для работы в любом месте.

Выберите тариф в веб панели пользователя https://asibiont.ru или используйте команду /subscription

Есть вопросы? Поддержка: @aleksandrinsider
"""

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
        user = User(telegram_id=user_id, username=message.from_user.username)
        session.add(user)
        session.commit()
        logger.info(f"Created new user {user_id}")
        is_new_user = True
    
    session.close()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Открыть веб-версию", web_app=WebAppInfo(url=f"{WEB_APP_URL}/dashboard"))]
    ])
    
    welcome_text = PREMIUM_DESCRIPTION + "\n\n💡 Есть промокод? Используйте команду /promo <КОД>"
    await message.bot.send_message(message.chat.id, welcome_text, reply_markup=keyboard)


@router.message(Command("subscription"))
async def subscription_handler(message: Message):
    """Handle subscription command - show tariffs and payment links"""
    user_id = message.from_user.id
    session = Session()
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        user = User(telegram_id=user_id, username=message.from_user.username)
        session.add(user)
        session.commit()
    
    # Check for promo code in message
    promo_code = None
    text_parts = message.text.split()
    if len(text_parts) > 1:
        promo_code = text_parts[1].upper()
    
    # Handle promo code activation if 100% discount
    if promo_code:
        from models import PromoCode, Subscription, SubscriptionTier, PaymentHistory
        promo = session.query(PromoCode).filter_by(code=promo_code).first()
        if promo and promo.discount_percent == 100:
            # Check if user already used this promo code
            used_by_users = json.loads(promo.used_by_users or '[]')
            if user_id in used_by_users:
                await message.bot.send_message(message.chat.id, "Вы уже использовали этот промокод!")
                session.close()
                return
            
            # Check max uses
            if promo.max_uses and promo.used_count >= promo.max_uses:
                await message.bot.send_message(message.chat.id, "Промокод уже исчерпан!")
                session.close()
                return
            
            # Check expiration
            if promo.expires_at and promo.expires_at < datetime.now(timezone.utc):
                await message.bot.send_message(message.chat.id, "Промокод истек!")
                session.close()
                return
            
            # Activate subscription directly
            subscription = session.query(Subscription).filter_by(user_id=user.id).first()
            if not subscription:
                subscription = Subscription(user_id=user.id, telegram_username=user.username)
                session.add(subscription)
            
            subscription.status = 'active'
            subscription.start_date = datetime.now(timezone.utc)
            subscription.tier = promo.tier
            user.subscription_tier = promo.tier
            
            # Set end date
            now = datetime.now(timezone.utc)
            if subscription.end_date and subscription.end_date > now:
                subscription.end_date = subscription.end_date + timedelta(days=promo.duration_days)
            else:
                subscription.end_date = now + timedelta(days=promo.duration_days)
            
            # Mark promo code as used
            used_by_users.append(user_id)
            promo.used_by_users = json.dumps(used_by_users)
            promo.used_count += 1
            
            # Log to payment history
            payment_history = PaymentHistory(
                user_id=user.id,
                telegram_username=user.username,
                action='promo_used',
                tier=promo.tier,
                amount='0',
                duration_days=promo.duration_days,
                start_date=subscription.start_date,
                end_date=subscription.end_date,
                details=json.dumps({'promo_code': promo_code, 'discount_percent': 100})
            )
            session.add(payment_history)
            
            session.commit()
            
            from payments import get_tier_name
            tier_name = get_tier_name(promo.tier.value.lower())
            await message.bot.send_message(message.chat.id, f"🎉 Промокод {promo_code} активирован! Подписка {tier_name} активирована бесплатно на {promo.duration_days} дней!")
            session.close()
            return
    
    # Описание тарифов
    tiers_description = """Доступные тарифы подписки:

LIGHT — 3000₽/месяц
Для всех. AI-агент управляет вашими задачами, напоминает о важном, помогает находить единомышленников.

STANDARD — 9000₽/месяц (ПОПУЛЯРНЫЙ)
Для тех, кто ставит задачи другим. Делегируйте с автоматическим ИИ-контролем выполнения, управляйте проектами, получайте приоритет в сообществе.

PREMIUM — 27000₽/месяц
Для тех, кто стремится к элитным связям и взаимной видимости на высшем уровне. Полный доступ ко всем контактам, премиум-статус, VIP-поддержка.

Есть промокод? Введите его на сайте https://asibiont.ru

Выберите тариф для оплаты через ЮКАССА:"""
    
    await message.bot.send_message(message.chat.id, tiers_description)
    
    # Создать платежи для всех тарифов
    try:
        light_url = create_payment(3000, "Подписка LIGHT (месяц)", user_id, 'light', promo_code)
        standard_url = create_payment(9000, "Подписка STANDARD (месяц)", user_id, 'standard', promo_code)
        premium_url = create_payment(27000, "Подписка PREMIUM (месяц)", user_id, 'premium', promo_code)
        
        payment_message = f"""LIGHT (3000₽/мес):
{light_url}

STANDARD (9000₽/мес):
{standard_url}

PREMIUM (27000₽/мес):
{premium_url}"""
        
        await message.bot.send_message(message.chat.id, payment_message)
    except ValueError as e:
        await message.bot.send_message(message.chat.id, f"Ошибка с промокодом: {str(e)}")
    except Exception as e:
        logger.error(f"Error creating payments: {e}")
        await message.bot.send_message(message.chat.id, "Ошибка при создании платежей. Попробуйте позже.")
    
    session.close()


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
    context = []  # Simplified: no context in bot
    ai_result = await chat_with_ai(prompt, context, user_id)
    response = ai_result['response']
    await message.bot.send_message(message.chat.id, response)


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
        context = []  # Simplified: no context in bot
        ai_result = await chat_with_ai("Найди партнеров", context, user_id)
        response = ai_result['response']
        await message.bot.send_message(message.chat.id, response)
    except Exception as e:
        logger.error(f"Error in find_partners_handler: {e}")
        await message.bot.send_message(message.chat.id, "Произошла ошибка при поиске партнеров.")


@router.message(Command("promo"))
async def promo_handler(message: Message):
    """Активация промокода"""
    user_id = message.from_user.id
    
    # Извлекаем промокод из команды
    args = message.text.split()
    if len(args) < 2:
        await message.answer(
            "📝 Использование: /promo <КОД>\n\n"
            "Например: /promo LIGHT1\n\n"
            "Доступные промокоды:\n"
            "• LIGHT1 - месяц подписки LIGHT\n"
            "• STD2026XPRO - месяц подписки STANDARD\n"
            "• PREM2026ELITE - месяц подписки PREMIUM\n"
            "• VIPACCESS2026 - год подписки PREMIUM (одноразовый)"
        )
        return
    
    promo_code = args[1].upper()
    
    session = Session()
    try:
        # Найти или создать пользователя
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            user = User(
                telegram_id=user_id,
                username=message.from_user.username,
                first_name=message.from_user.first_name
            )
            session.add(user)
            session.commit()
        
        # Проверить промокод
        from models import PromoCode, SubscriptionTier, PaymentHistory
        promo = session.query(PromoCode).filter_by(code=promo_code).first()
        
        if not promo:
            await message.answer("❌ Промокод не найден. Проверьте правильность ввода.")
            return
        
        # Проверка на использование
        used_by_users = json.loads(promo.used_by_users or '[]')
        if user.id in used_by_users:
            await message.answer("❌ Вы уже использовали этот промокод.")
            return
        
        # Проверка лимита использований
        if promo.max_uses and promo.used_count >= promo.max_uses:
            await message.answer("❌ Промокод уже исчерпан.")
            return
        
        # Проверка срока действия
        if promo.expires_at and promo.expires_at < datetime.now(timezone.utc):
            await message.answer("❌ Срок действия промокода истек.")
            return
        
        # Активировать подписку
        subscription = session.query(Subscription).filter_by(user_id=user.id).first()
        
        start_date = datetime.now(timezone.utc)
        end_date = start_date + timedelta(days=promo.duration_days)
        
        if not subscription:
            # Создать новую подписку
            subscription = Subscription(
                user_id=user.id,
                telegram_id=user_id,
                telegram_username=user.username,
                username=user.username,
                status='active',
                tier=promo.tier,
                start_date=start_date,
                end_date=end_date
            )
            session.add(subscription)
        else:
            # Обновить существующую подписку
            subscription.status = 'active'
            subscription.tier = promo.tier
            subscription.start_date = start_date
            subscription.end_date = end_date
        
        # Обновить тариф в User
        user.subscription_tier = promo.tier
        
        # Отметить использование промокода
        used_by_users.append(user.id)
        promo.used_by_users = json.dumps(used_by_users)
        promo.used_count += 1
        
        # Записать в историю платежей
        payment_history = PaymentHistory(
            user_id=user.id,
            telegram_username=user.username,
            action='promo_activated',
            tier=promo.tier,
            amount='0',
            duration_days=promo.duration_days,
            start_date=start_date,
            end_date=end_date,
            details=json.dumps({'promo_code': promo_code})
        )
        session.add(payment_history)
        
        session.commit()
        
        tier_names = {
            SubscriptionTier.LIGHT: '🥉 Бронза',
            SubscriptionTier.STANDARD: '🥈 Серебро',
            SubscriptionTier.PREMIUM: '🥇 Золото'
        }
        
        await message.answer(
            f"✅ Промокод активирован!\n\n"
            f"🎁 Тариф: {tier_names.get(promo.tier, promo.tier.value)}\n"
            f"📅 Действует до: {end_date.strftime('%d.%m.%Y %H:%M')}\n\n"
            f"Используйте /dashboard для просмотра подписки"
        )
        
    except Exception as e:
        session.rollback()
        logger.error(f"Error activating promo code: {e}")
        await message.answer("❌ Ошибка при активации промокода. Попробуйте позже.")
    finally:
        session.close()


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
    
    # Описание тарифов
    tiers_description = """Доступные тарифы подписки:

LIGHT — 3000₽/месяц
Для всех. AI-агент управляет вашими задачами, напоминает о важном, помогает находить единомышленников в вашем городе.

STANDARD — 9000₽/месяц (ПОПУЛЯРНЫЙ)
Для тех, кто ставит задачи другим. Делегируйте с автоматическим ИИ-контролем выполнения, управляйте проектами, получайте приоритет в сообществе.

PREMIUM — 27000₽/месяц
Для тех, кто стремится к элитным связям и взаимной видимости на высшем уровне. Полный доступ ко всем контактам, премиум-статус, VIP-поддержка.

Подробнее о тарифах: https://asibiont.ru/subscription-tiers
Есть промокод? Используйте команду /promo <КОД>

Выберите тариф для оплаты через ЮКАССА:"""
    
    await message.bot.send_message(message.chat.id, tiers_description)
    
    # Создать платежи для всех тарифов
    from payments import create_payment
    light_url = create_payment(3000, "Подписка LIGHT (месяц)", user_id)
    standard_url = create_payment(9000, "Подписка STANDARD (месяц)", user_id)
    premium_url = create_payment(27000, "Подписка PREMIUM (месяц)", user_id)
    
    payment_message = """LIGHT (3000₽/мес):
{light_url}

STANDARD (9000₽/мес):
{standard_url}

PREMIUM (27000₽/мес):
{premium_url}"""
    
    await message.bot.send_message(message.chat.id, payment_message)
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

    # Проверка на пустые сообщения
    if not text or text.strip() == "":
        logger.info(f"Empty message from user {user_id}, ignoring")
        return

    # Duplicate protection removed

    try:
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
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if user:
            from models import Interaction
            interaction = Interaction(user_id=user.id, message_type='user', content=text)
            session.add(interaction)
            session.commit()
        session.close()
        
        context = []  # Simplified: no context in bot
        
        # Use new command-based architecture
        router = CommandRouter()
        command = await router.route(text, user_id)
        
        # Create session for command execution
        db_session = Session()
        try:
            result = await command.execute(user_id, db_session)
            # Extract response text from result dict
            response_text = result.get('response', '') if isinstance(result, dict) else str(result)
            await message.bot.send_message(message.chat.id, response_text)
        except Exception as e:
            logger.error(f"Error executing command for user {user_id}: {e}", exc_info=True)
            await message.bot.send_message(message.chat.id, "Извините, произошла ошибка при обработке команды.")
            response_text = ""
        finally:
            db_session.close()
        
        # СОХРАНЯЕМ ОТВЕТ AI
        session = Session()
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


def update_profile(skills=None, interests=None, goals=None, city=None, current_plans=None, current_time=None, timezone=None, company=None, bio=None, languages=None, position=None, user_id=None, session=None):
    """Обновить профиль пользователя"""
    try:
        if not session:
            session = Session()
            should_close = True
        else:
            should_close = False

        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            if should_close:
                session.close()
            return "❌ Пользователь не найден. Попробуйте перезапустить бота"

        # Получить или создать профиль
        from models import UserProfile
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if not profile:
            profile = UserProfile(user_id=user.id)
            session.add(profile)

        # Обновить поля профиля
        if city is not None:
            profile.city = city
        if company is not None:
            profile.company = company
        if position is not None:
            profile.position = position
        if bio is not None:
            profile.bio = bio
        if languages is not None:
            profile.languages = languages

        # Для полей-списков (skills, interests, goals) - добавляем к существующим
        if skills:
            if skills.startswith('-'):
                # Удаление навыка
                skill_to_remove = skills[1:]
                if profile.skills:
                    current_skills = [s.strip() for s in profile.skills.split(',')]
                    if skill_to_remove in current_skills:
                        current_skills.remove(skill_to_remove)
                        profile.skills = ', '.join(current_skills)
            elif skills == '':
                # Полная очистка
                profile.skills = None
            else:
                # Добавление навыка
                if profile.skills:
                    current_skills = [s.strip() for s in profile.skills.split(',')]
                    new_skills = [s.strip() for s in skills.split(',')]
                    for skill in new_skills:
                        if skill and skill not in current_skills:
                            current_skills.append(skill)
                    profile.skills = ', '.join(current_skills)
                else:
                    profile.skills = skills

        if interests:
            if interests.startswith('-'):
                # Удаление интереса
                interest_to_remove = interests[1:].strip().lower()
                if profile.interests:
                    current_interests = [i.strip() for i in profile.interests.split(',')]
                    # Удаляем с учётом регистра
                    current_interests = [i for i in current_interests if i.lower() != interest_to_remove]
                    profile.interests = ', '.join(current_interests)
            elif interests == '':
                # Полная очистка
                profile.interests = None
            else:
                # Добавление интереса
                if profile.interests:
                    current_interests = [i.strip() for i in profile.interests.split(',') if i.strip()]
                    new_interests = [i.strip() for i in interests.split(',') if i.strip()]
                    # Создаём set из нижнего регистра для проверки дубликатов
                    interests_lower = {i.lower() for i in current_interests}
                    for interest in new_interests:
                        if interest and interest.lower() not in interests_lower:
                            current_interests.append(interest)
                            interests_lower.add(interest.lower())
                    # Удаляем дубликаты (если они уже есть в БД)
                    seen = set()
                    unique_interests = []
                    for i in current_interests:
                        if i.lower() not in seen:
                            unique_interests.append(i)
                            seen.add(i.lower())
                    profile.interests = ', '.join(unique_interests)
                else:
                    # Удаляем дубликаты из новых интересов
                    new_interests = [i.strip() for i in interests.split(',') if i.strip()]
                    seen = set()
                    unique_interests = []
                    for i in new_interests:
                        if i.lower() not in seen:
                            unique_interests.append(i)
                            seen.add(i.lower())
                    profile.interests = ', '.join(unique_interests)

        if goals:
            if goals == '':
                profile.goals = None
            else:
                profile.goals = goals

        # Обновить timezone пользователя если передан
        if timezone:
            user.timezone = timezone

        session.commit()

        if should_close:
            session.close()

        return "Профиль успешно обновлен ✅"

    except Exception as e:
        logger.error(f"Error updating profile for user {user_id}: {e}")
        if should_close and 'session' in locals():
            session.close()
        return f"❌ Ошибка при обновлении профиля: {str(e)}"


def get_delegation_progress(user_id, session=None):
    """Получить отчет о статусе делегированных задач"""
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


def delegate_task(title, description, reminder_time, delegated_to_username, user_id, session=None, delegation_details=None):
    """Делегировать задачу другому пользователю"""
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

        # Проверить подписку (Standard/Premium)
        subscription = session.query(Subscription).filter_by(user_id=user.id).first()
        if not subscription or subscription.status != 'active':
            if should_close:
                session.close()
            return "DELEGATION_SUBSCRIPTION_REQUIRED: Делегирование задач доступно только на тарифах Standard и Premium. Обновите подписку: https://asibiont.ru/subscription_tiers"

        # Проверить, что пользователь пытается делегировать не себе
        if delegated_to_username.lower() == (user.username or "").lower().replace('@', ''):
            if should_close:
                session.close()
            return "SELF_DELEGATION_ERROR: Нельзя делегировать задачу самому себе"

        # Создать задачу
        from datetime import datetime
        task = Task(
            user_id=user.id,
            title=title,
            description=description,
            reminder_time=datetime.fromisoformat(reminder_time.replace('Z', '+00:00')) if isinstance(reminder_time, str) else reminder_time,
            delegated_by=user.id,
            delegated_to_username=delegated_to_username.replace('@', ''),  # Убрать @ если есть
            delegation_status='pending',
            delegation_details=delegation_details
        )

        session.add(task)
        
        # Создать план контроля для агента
        import json
        from datetime import datetime, timezone, timedelta
        
        control_plan = {
            'task_id': task.id,
            'executor_username': delegated_to_username,
            'user_id': user.id,
            'created_at': datetime.now(timezone.utc).isoformat(),
            'checkpoints': [
                {'type': 'start_notification', 'completed': True, 'timestamp': datetime.now(timezone.utc).isoformat()},
                {'type': 'progress_check', 'interval_hours': 4, 'next_check': (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()},
                {'type': 'deadline_warning', 'hours_before': 4, 'scheduled': None},
                {'type': 'final_check', 'scheduled': task.reminder_time.isoformat() if task.reminder_time else None}
            ],
            'escalation_level': 0,
            'last_contact': datetime.now(timezone.utc).isoformat(),
            'agent_controlled': True
        }
        
        task.delegation_details = json.dumps(control_plan)
        session.commit()

        # Отправить уведомление получателю (если бот может найти пользователя)
        try:
            recipient = session.query(User).filter(
                User.username.ilike(delegated_to_username.replace('@', ''))
            ).first()

            if recipient:
                # Импортировать здесь чтобы избежать циклических импортов
                from main import bot
                if bot:
                    notification_text = "📥 Вам делегирована задача!\n\n"
                    notification_text += f"📋 Задача: {title}\n"
                    notification_text += f"👤 От: @{user.username or user.first_name or 'пользователь'}\n"
                    if description:
                        notification_text += f"📝 Описание: {description[:200]}...\n"
                    if reminder_time:
                        task_time = task.reminder_time.strftime('%d.%m.%Y %H:%M') if task.reminder_time else "не указан"
                        notification_text += f"⏰ Дедлайн: {task_time}\n"
                    notification_text += "\nИспользуйте команды:\n"
                    notification_text += f"/accept_{task.id} - принять\n"
                    notification_text += f"/reject_{task.id} - отклонить"

                    # Отправка уведомления в фоне (асинхронно)
                    import asyncio
                    from config import TELEGRAM_TOKEN
                    if TELEGRAM_TOKEN:
                        asyncio.create_task(send_delegation_notification_async(recipient.telegram_id, notification_text))
        except Exception as e:
            logger.warning(f"Could not send delegation notification: {e}")

        if should_close:
            session.close()

        return f"TASK_DELEGATED_SUCCESS: Задача '{title}' успешно делегирована пользователю @{delegated_to_username}. Он получит уведомление и сможет принять или отклонить задачу."

    except Exception as e:
        logger.error(f"Error delegating task for user {user_id}: {e}")
        if should_close:
            session.close()
        return f"Ошибка при делегировании задачи: {str(e)}"


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

def add_task(title, description, reminder_time, user_id, session):
    """Add a new task for the user"""
    try:
        from models import Task, User
        from ai_integration.time_parser import parse_time
        
        user = session.query(User).filter_by(id=user_id).first()
        if not user:
            return "Пользователь не найден"
        
        # Parse reminder time
        parsed_time = parse_time(reminder_time, user.timezone if user.timezone else 'UTC')
        if not parsed_time:
            return f"Не удалось распознать время: {reminder_time}"
        
        # Create task
        task = Task(
            user_id=user_id,
            title=title,
            description=description,
            reminder_time=parsed_time,
            status='pending'
        )
        session.add(task)
        session.commit()
        
        return f"Задача '{title}' создана на {parsed_time.strftime('%d.%m.%Y %H:%M')}"
    
    except Exception as e:
        session.rollback()
        logger.error(f"Error adding task: {e}")
        return f"Ошибка при создании задачи: {str(e)}"


def reschedule_task(task_title, new_time, user_id, session):
    """Reschedule an existing task"""
    try:
        from models import Task, User
        from ai_integration.time_parser import parse_time
        
        user = session.query(User).filter_by(id=user_id).first()
        if not user:
            return "Пользователь не найден"
        
        # Find task by title keywords
        tasks = session.query(Task).filter(
            Task.user_id == user_id,
            Task.status == 'pending'
        ).all()
        
        # Find best match
        best_match = None
        for task in tasks:
            if task_title.lower() in task.title.lower():
                best_match = task
                break
        
        if not best_match:
            return f"Задача с ключевыми словами '{task_title}' не найдена"
        
        # Parse new time
        parsed_time = parse_time(new_time, user.timezone if user.timezone else 'UTC')
        if not parsed_time:
            return f"Не удалось распознать новое время: {new_time}"
        
        # Update task
        best_match.reminder_time = parsed_time
        session.commit()
        
        return f"Задача '{best_match.title}' перенесена на {parsed_time.strftime('%d.%m.%Y %H:%M')}"
    
    except Exception as e:
        session.rollback()
        logger.error(f"Error rescheduling task: {e}")
        return f"Ошибка при переносе задачи: {str(e)}"


def complete_task(task_title, completion_note, user_id, session):
    """Mark a task as completed"""
    try:
        from models import Task
        from datetime import datetime, timezone
        
        # Find task by title keywords
        tasks = session.query(Task).filter(
            Task.user_id == user_id,
            Task.status == 'pending'
        ).all()
        
        # Find best match
        best_match = None
        for task in tasks:
            if task_title.lower() in task.title.lower():
                best_match = task
                break
        
        if not best_match:
            return f"Задача с ключевыми словами '{task_title}' не найдена"
        
        # Complete task
        best_match.status = 'completed'
        best_match.actual_completion_time = datetime.now(timezone.utc)
        if completion_note:
            best_match.completion_notes = completion_note
        session.commit()
        
        return f"Задача '{best_match.title}' отмечена как выполненная"
    
    except Exception as e:
        session.rollback()
        logger.error(f"Error completing task: {e}")
        return f"Ошибка при завершении задачи: {str(e)}"


def list_tasks(include_completed, user_id, session):
    """List user's tasks"""
    try:
        from models import Task
        
        query = session.query(Task).filter(Task.user_id == user_id)
        if not include_completed:
            query = query.filter(Task.status == 'pending')
        
        tasks = query.order_by(Task.reminder_time).all()
        
        if not tasks:
            return "У вас нет задач" + (" (включая выполненные)" if include_completed else "")
        
        result = "Ваши задачи:\n\n"
        for i, task in enumerate(tasks, 1):
            status_icon = "✅" if task.status == 'completed' else "⏰"
            time_str = task.reminder_time.strftime('%d.%m.%Y %H:%M') if task.reminder_time else "без времени"
            result += f"{i}. {status_icon} {task.title}\n   ⏰ {time_str}\n"
            if task.description:
                result += f"   📝 {task.description[:100]}{'...' if len(task.description) > 100 else ''}\n"
            result += "\n"
        
        return result
    
    except Exception as e:
        logger.error(f"Error listing tasks: {e}")
        return f"Ошибка при получении списка задач: {str(e)}"


def delete_task(task_title, user_id, session):
    """Delete a task"""
    try:
        from models import Task
        
        # Find task by title keywords
        tasks = session.query(Task).filter(
            Task.user_id == user_id,
            Task.status == 'pending'
        ).all()
        
        # Find best match
        best_match = None
        for task in tasks:
            if task_title.lower() in task.title.lower():
                best_match = task
                break
        
        if not best_match:
            return f"Задача с ключевыми словами '{task_title}' не найдена"
        
        # Delete task
        session.delete(best_match)
        session.commit()
        
        return f"Задача '{best_match.title}' удалена"
    
    except Exception as e:
        session.rollback()
        logger.error(f"Error deleting task: {e}")
        return f"Ошибка при удалении задачи: {str(e)}"


def update_profile(user_id, city=None, interests=None, skills=None, goals=None, company=None, position=None, session=None):
    """Update user profile"""
    try:
        from models import UserProfile
        
        user_profile = session.query(UserProfile).filter_by(user_id=user_id).first()
        if not user_profile:
            user_profile = UserProfile(user_id=user_id)
            session.add(user_profile)
        
        # Update fields
        if city:
            user_profile.city = city
        if interests:
            user_profile.interests = interests
        if skills:
            user_profile.skills = skills
        if goals:
            user_profile.goals = goals
        if company:
            user_profile.company = company
        if position:
            user_profile.position = position
        
        session.commit()
        return "Профиль обновлен"
    
    except Exception as e:
        session.rollback()
        logger.error(f"Error updating profile: {e}")
        return f"Ошибка при обновлении профиля: {str(e)}"


def find_partners(user_id, session):
    """Find potential partners for collaboration"""
    try:
        from models import UserProfile, User
        
        user_profile = session.query(UserProfile).filter_by(user_id=user_id).first()
        if not user_profile:
            return "Сначала заполните профиль с навыками и интересами"
        
        # Find users with similar skills/interests
        similar_users = session.query(UserProfile).filter(
            UserProfile.user_id != user_id,
            UserProfile.skills.isnot(None),
            UserProfile.interests.isnot(None)
        ).all()
        
        if not similar_users:
            return "Пока нет пользователей с заполненными профилями для поиска партнеров"
        
        result = "Возможные партнеры для сотрудничества:\n\n"
        for profile in similar_users[:5]:  # Limit to 5
            user = session.query(User).filter_by(id=profile.user_id).first()
            if user:
                result += f"👤 @{user.username or user.first_name or 'пользователь'}\n"
                if profile.skills:
                    result += f"🛠 Навыки: {profile.skills[:100]}{'...' if len(profile.skills) > 100 else ''}\n"
                if profile.interests:
                    result += f"🎯 Интересы: {profile.interests[:100]}{'...' if len(profile.interests) > 100 else ''}\n"
                result += "\n"
        
        return result
    
    except Exception as e:
        logger.error(f"Error finding partners: {e}")
        return f"Ошибка при поиске партнеров: {str(e)}"


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


def accept_delegated_task(task_id, user_id, session=None):
    """Accept a delegated task"""
    try:
        from models import Task
        import json

        if not session:
            from models import Session
            session = Session()

        task = session.query(Task).filter(
            Task.id == task_id,
            Task.user_id == user_id,
            Task.delegated_by.isnot(None)
        ).first()

        if not task:
            return "Задача не найдена или не принадлежит вам"

        if task.status != 'pending':
            return "Задача уже обработана"

        # Update task status
        task.status = 'accepted'
        task.delegation_status = 'accepted'
        task.accepted_at = datetime.now()

        # Update control plan
        if task.delegation_details:
            control_plan = json.loads(task.delegation_details)
            for checkpoint in control_plan.get('checkpoints', []):
                if checkpoint['type'] == 'acceptance_check':
                    checkpoint['completed'] = True
                    checkpoint['completed_at'] = datetime.now().isoformat()
            task.delegation_details = json.dumps(control_plan)

        session.commit()

        # Notify delegator
        delegator_notification = f"""✅ Задача принята исполнителем

📋 {task.title}
👤 Исполнитель: Пользователь {user_id}
⏰ Принята: {datetime.now().strftime('%d.%m.%Y %H:%M')}"""

        if task.delegated_by:
            try:
                asyncio.create_task(send_delegation_notification_async(task.delegated_by, delegator_notification))
            except RuntimeError:
                logger.info(f"Would notify delegator {task.delegated_by}: Task accepted")

        return f"Задача '{task.title}' принята к исполнению"

    except Exception as e:
        if session:
            session.rollback()
        logger.error(f"Error accepting delegated task: {e}")
        return f"Ошибка при принятии задачи: {str(e)}"


def reject_delegated_task(task_id, user_id, reason=None, session=None):
    """Reject a delegated task"""
    try:
        from models import Task
        import json

        if not session:
            from models import Session
            session = Session()

        task = session.query(Task).filter(
            Task.id == task_id,
            Task.user_id == user_id,
            Task.delegated_by.isnot(None)
        ).first()

        if not task:
            return "Задача не найдена или не принадлежит вам"

        if task.status != 'pending':
            return "Задача уже обработана"

        # Update task status
        task.status = 'rejected'
        task.delegation_status = 'rejected'
        task.rejected_at = datetime.now()
        task.rejection_reason = reason

        # Update control plan
        if task.delegation_details:
            control_plan = json.loads(task.delegation_details)
            control_plan['rejected'] = True
            control_plan['rejection_reason'] = reason
            task.delegation_details = json.dumps(control_plan)

        session.commit()

        # Notify delegator
        delegator_notification = f"""❌ Задача отклонена исполнителем

📋 {task.title}
👤 Исполнитель: Пользователь {user_id}
📝 Причина: {reason or 'Не указана'}
⏰ Отклонена: {datetime.now().strftime('%d.%m.%Y %H:%M')}"""

        if task.delegated_by:
            try:
                asyncio.create_task(send_delegation_notification_async(task.delegated_by, delegator_notification))
            except RuntimeError:
                logger.info(f"Would notify delegator {task.delegated_by}: Task rejected")

        return f"Задача '{task.title}' отклонена"

    except Exception as e:
        if session:
            session.rollback()
        logger.error(f"Error rejecting delegated task: {e}")
        return f"Ошибка при отклонении задачи: {str(e)}"


def check_delegation_deadlines(session=None):
    """Check for overdue delegated tasks and send escalation notifications"""
    try:
        from models import Task
        import json
        from datetime import datetime

        if not session:
            from models import Session
            session = Session()

        # Find tasks with control plans
        tasks = session.query(Task).filter(
            Task.delegation_details.isnot(None),
            Task.status.in_(['pending', 'accepted'])
        ).all()

        notifications_sent = 0

        for task in tasks:
            try:
                control_plan = json.loads(task.delegation_details)
                if not control_plan.get('agent_monitoring', False):
                    continue

                now = datetime.now()
                checkpoints = control_plan.get('checkpoints', [])
                escalation_levels = control_plan.get('escalation_levels', [])

                for checkpoint in checkpoints:
                    if checkpoint.get('completed', False):
                        continue

                    scheduled_time = datetime.fromisoformat(checkpoint['scheduled_time'])
                    if now >= scheduled_time:
                        # Find appropriate escalation level
                        escalation_level = checkpoint.get('escalation_level', 0)
                        if escalation_level < len(escalation_levels):
                            level_info = escalation_levels[escalation_level]

                            # Send notification
                            if task.status == 'pending':
                                message = f"""🤖 Контроль делегированной задачи

📋 {task.title}
⚠️ {level_info['message']}

👤 Исполнитель: Пользователь {task.user_id}
⏰ Дедлайн: {control_plan.get('deadline', 'Не указан')}"""

                                asyncio.create_task(send_delegation_notification_async(
                                    task.user_id, message
                                ))

                                # Also notify delegator
                                delegator_message = f"""🤖 Эскалация делегированной задачи

📋 {task.title}
👤 Исполнитель: Пользователь {task.user_id}
⚠️ {level_info['message']}"""

                                if control_plan.get('delegator_id'):
                                    asyncio.create_task(send_delegation_notification_async(
                                        control_plan['delegator_id'], delegator_message
                                    ))

                            # Update checkpoint
                            checkpoint['escalation_level'] = escalation_level + 1
                            if escalation_level + 1 >= len(escalation_levels):
                                checkpoint['completed'] = True

                            notifications_sent += 1

                # Save updated control plan
                task.delegation_details = json.dumps(control_plan)

            except Exception as e:
                logger.error(f"Error processing task {task.id}: {e}")
                continue

        session.commit()
        logger.info(f"Sent {notifications_sent} delegation escalation notifications")

    except Exception as e:
        logger.error(f"Error checking delegation deadlines: {e}")


def update_task_progress(task_id, progress_percentage, notes=None, user_id=None, session=None):
    """Update progress on a delegated task"""
    try:
        from models import Task
        import json

        if not session:
            from models import Session
            session = Session()

        task = session.query(Task).filter(Task.id == task_id).first()
        if not task:
            return "Задача не найдена"

        # Check permissions (executor or delegator)
        if user_id and task.user_id != user_id and task.delegated_from != user_id:
            return "У вас нет прав на обновление этой задачи"

        # Update progress
        if 'progress' not in task.__dict__:
            # If no progress field, store in delegation_details
            if not task.delegation_details:
                task.delegation_details = '{}'
            details = json.loads(task.delegation_details)
            details['progress'] = progress_percentage
            details['progress_updated_at'] = datetime.now().isoformat()
            if notes:
                details['progress_notes'] = notes
            task.delegation_details = json.dumps(details)
        else:
            task.progress = progress_percentage
            task.progress_updated_at = datetime.now()
            if notes:
                task.progress_notes = notes

        session.commit()

        # Notify delegator if this is a delegated task
        if task.delegated_by and task.delegated_by != user_id:
            notification = f"""📊 Обновление прогресса задачи

📋 {task.title}
👤 Исполнитель: Пользователь {task.user_id}
📈 Прогресс: {progress_percentage}%

{notes if notes else ''}"""

            try:
                asyncio.create_task(send_delegation_notification_async(task.delegated_by, notification))
            except RuntimeError:
                logger.info(f"Would notify delegator {task.delegated_by}: Progress update {progress_percentage}%")

        return f"Прогресс задачи '{task.title}' обновлен: {progress_percentage}%"

    except Exception as e:
        if session:
            session.rollback()
        logger.error(f"Error updating task progress: {e}")
        return f"Ошибка при обновлении прогресса: {str(e)}"


