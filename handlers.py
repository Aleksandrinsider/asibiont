import logging
import asyncio
import os
import tempfile
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
            "text": message_text,
            "parse_mode": "HTML"
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=data, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status != 200:
                    logger.warning(f"Failed to send delegation notification: {response.status}")

    except Exception as e:
        logger.warning(f"Error sending delegation notification: {e}")

PREMIUM_DESCRIPTION = """
🤖 ASI Biont - ИИ-агент для управления задачами

Я ваш персональный ИИ-ассистент для эффективного управления задачами и временем.

✨ Возможности:
• Создание и управление задачами через естественный язык
• Умные напоминания и дедлайны
• Делегирование задач партнерам
• Анализ прогресса и рекомендации
• Интеграция с календарем и уведомлениями

Для доступа ко всем функциям нужна активная подписка.
Выберите тариф в веб-приложении или используйте команду /subscription
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
    context = []  # Simplified: no context in bot
    response = await chat_with_ai(prompt, context, user_id)
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
        response = await chat_with_ai("Найди партнеров", context, user_id)
        await message.bot.send_message(message.chat.id, response)
    except Exception as e:
        logger.error(f"Error in find_partners_handler: {e}")
        await message.bot.send_message(message.chat.id, "Произошла ошибка при поиске партнеров.")


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
Есть промокод? Введите его на сайте

Выберите тариф для оплаты через ЮКАССА:"""
    
    await message.bot.send_message(message.chat.id, tiers_description)
    
    # Создать платежи для всех тарифов
    from payments import create_payment
    light_url = create_payment(3000, "Подписка LIGHT (месяц)", user_id)
    standard_url = create_payment(9000, "Подписка STANDARD (месяц)", user_id)
    premium_url = create_payment(27000, "Подписка PREMIUM (месяц)", user_id)
    
    payment_message = f"""LIGHT (3000₽/мес):
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

        context = []  # Simplified: no context in bot
        
        # Use new command-based architecture
        router = CommandRouter()
        command = router.route(text)
        
        # Create session for command execution
        db_session = Session()
        try:
            result = await command.execute(user_id, db_session)
            await message.bot.send_message(message.chat.id, result)
        except Exception as e:
            logger.error(f"Error executing command for user {user_id}: {e}", exc_info=True)
            await message.bot.send_message(message.chat.id, "Извините, произошла ошибка при обработке команды.")
        finally:
            db_session.close()
        # Записать взаимодействие для проактивных проверок
        session = Session()
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if user:
            from models import Interaction
            interaction = Interaction(user_id=user.id, message_type='user', content=text)
            session.add(interaction)
            if response and response.strip():
                interaction = Interaction(user_id=user.id, message_type='ai', content=response.strip())
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
                interest_to_remove = interests[1:]
                if profile.interests:
                    current_interests = [i.strip() for i in profile.interests.split(',')]
                    if interest_to_remove in current_interests:
                        current_interests.remove(interest_to_remove)
                        profile.interests = ', '.join(current_interests)
            elif interests == '':
                # Полная очистка
                profile.interests = None
            else:
                # Добавление интереса
                if profile.interests:
                    current_interests = [i.strip() for i in profile.interests.split(',')]
                    new_interests = [i.strip() for i in interests.split(',')]
                    for interest in new_interests:
                        if interest and interest not in current_interests:
                            current_interests.append(interest)
                    profile.interests = ', '.join(current_interests)
                else:
                    profile.interests = interests

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
                    notification_text = f"📥 Вам делегирована задача!\n\n"
                    notification_text += f"📋 Задача: {title}\n"
                    notification_text += f"👤 От: @{user.username or user.first_name or 'пользователь'}\n"
                    if description:
                        notification_text += f"📝 Описание: {description[:200]}...\n"
                    if reminder_time:
                        task_time = task.reminder_time.strftime('%d.%m.%Y %H:%M') if task.reminder_time else "не указан"
                        notification_text += f"⏰ Дедлайн: {task_time}\n"
                    notification_text += f"\nИспользуйте команды:\n"
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


