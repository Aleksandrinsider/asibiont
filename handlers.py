import json
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from ai_integration import chat_with_ai
from models import Session, User, UserProfile, Subscription
import os

router = Router()

if os.getenv("LOCAL") == "1":
    # Для локального тестирования использовать dict вместо Redis
    context_store = {}
else:
    import redis
    from config import REDIS_URL
    r = redis.from_url(REDIS_URL)

@router.message(Command("start"))
async def start_handler(message: Message):
    await message.bot.send_message(message.chat.id, "🌟 Интеллектуальная платформа, разработанная лабораторией ИИ EREBUS AI, для эффективного управления задачами и построения мощного сообщества единомышленников.\n\nС премиум-подпиской за 3000 рублей в месяц вы получаете эксклюзивный доступ к:\n\n🔹 **Интеллектуальной ИИ-системе**: Автоматизированное планирование задач, умные напоминания и персональная мотивация для достижения целей.\n🔹 **Управлению задачами**: Создание, редактирование, завершение задач с приоритетами, дедлайнами и аналитикой прогресса.\n🔹 **Умным напоминаниям**: Автоматические напоминания о задачах, проверка результатов и мотивационные сообщения.\n🔹 **Сообществу лидеров**: Поиск и подключение к единомышленникам по интересам, целям и проектам — от дизайна и программирования до спорта и бизнеса.\n🔹 **Совместным возможностям**: Рекомендации по событиям, коллаборациям и проектам, которые меняют жизнь.\n🔹 **Персонализированным инструментам**: Управление профилем, сохранение личной информации и адаптация под ваши нужды.\n\nПрисоединяйтесь к сообществу амбициозных профессионалов, где каждый шаг ведет к успеху. Оформите подписку /subscribe и начните трансформировать свою продуктивность! 🚀\n\nПо вопросам поддержки обращайтесь: @aleksandrinsider")

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
        await message.bot.send_message(message.chat.id, "🌟 Интеллектуальная платформа, разработанная лабораторией ИИ EREBUS AI, для эффективного управления задачами и построения мощного сообщества единомышленников.\n\nС премиум-подпиской за 3000 рублей в месяц вы получаете эксклюзивный доступ к:\n\n🔹 **Интеллектуальной ИИ-системе**: Автоматизированное планирование задач, умные напоминания и персональная мотивация для достижения целей.\n🔹 **Управлению задачами**: Создание, редактирование, завершение задач с приоритетами, дедлайнами и аналитикой прогресса.\n🔹 **Умным напоминаниям**: Автоматические напоминания о задачах, проверка результатов и мотивационные сообщения.\n🔹 **Сообществу лидеров**: Поиск и подключение к единомышленникам по интересам, целям и проектам — от дизайна и программирования до спорта и бизнеса.\n🔹 **Совместным возможностям**: Рекомендации по событиям, коллаборациям и проектам, которые меняют жизнь.\n🔹 **Персонализированным инструментам**: Управление профилем, сохранение личной информации и адаптация под ваши нужды.\n\nПрисоединяйтесь к сообществу амбициозных профессионалов, где каждый шаг ведет к успеху. Оформите подписку /subscribe и начните трансформировать свою продуктивность! 🚀\n\nПо вопросам поддержки обращайтесь: @aleksandrinsider")
        session.close()
        return
    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    if not profile:
        profile = UserProfile(user_id=user.id)
        session.add(profile)
    # Предполагаем, что сообщение содержит данные в формате: навыки: ..., интересы: ..., цели: ...
    text = message.text.replace("/update_profile", "").strip()
    if text:
        parts = text.split(",")
        if len(parts) >= 3:
            profile.skills = parts[0].strip()
            profile.interests = parts[1].strip()
            profile.goals = parts[2].strip()
            profile.contact_info = message.from_user.username or str(user_id)
            session.commit()
            await message.bot.send_message(message.chat.id, "Профиль обновлён!")
        else:
            await message.bot.send_message(message.chat.id, "Формат: /update_profile навыки, интересы, цели")
    else:
        await message.bot.send_message(message.chat.id, "Введите данные: /update_profile навыки, интересы, цели")
    session.close()

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
        await message.bot.send_message(message.chat.id, "🌟 Интеллектуальная платформа, разработанная лабораторией ИИ EREBUS AI, для эффективного управления задачами и построения мощного сообщества единомышленников.\n\nС премиум-подпиской за 3000 рублей в месяц вы получаете эксклюзивный доступ к:\n\n🔹 **Интеллектуальной ИИ-системе**: Автоматизированное планирование задач, умные напоминания и персональная мотивация для достижения целей.\n🔹 **Управлению задачами**: Создание, редактирование, завершение задач с приоритетами, дедлайнами и аналитикой прогресса.\n🔹 **Умным напоминаниям**: Автоматические напоминания о задачах, проверка результатов и мотивационные сообщения.\n🔹 **Сообществу лидеров**: Поиск и подключение к единомышленникам по интересам, целям и проектам — от дизайна и программирования до спорта и бизнеса.\n🔹 **Совместным возможностям**: Рекомендации по событиям, коллаборациям и проектам, которые меняют жизнь.\n🔹 **Персонализированным инструментам**: Управление профилем, сохранение личной информации и адаптация под ваши нужды.\n\nПрисоединяйтесь к сообществу амбициозных профессионалов, где каждый шаг ведет к успеху. Оформите подписку /subscribe и начните трансформировать свою продуктивность! 🚀\n\nПо вопросам поддержки обращайтесь: @aleksandrinsider")
        session.close()
        return
    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    if not profile:
        await message.bot.send_message(message.chat.id, "Сначала обновите профиль с /update_profile")
        session.close()
        return
    # Получить все профили кроме своего
    profiles = session.query(UserProfile).filter(UserProfile.user_id != user.id).all()
    partners = []
    for p in profiles:
        # Простая логика: если навыки или интересы совпадают
        if profile.skills and p.skills and any(skill in p.skills for skill in profile.skills.split(",")):
            partners.append(p)
        elif profile.interests and p.interests and any(interest in p.interests for interest in profile.interests.split(",")):
            partners.append(p)
    if partners:
        response = "Возможные партнёры:\n"
        for p in partners[:5]:  # Ограничить 5
            response += f"- @{p.contact_info} (навыки: {p.skills})\n"
        await message.bot.send_message(message.chat.id, response)
    else:
        await message.bot.send_message(message.chat.id, "Партнёры не найдены. Попробуйте обновить профиль.")
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
    # Создать платеж на 3000 RUB
    from payments import create_payment
    payment_url = create_payment(3000, "Подписка на премиум-функции бота (месяц)", user_id)
    await message.bot.send_message(message.chat.id, f"Оплатить с помощью ЮКАССА, СБЕР или БАНКОВСКОЙ КАРТЫ: {payment_url}\nПосле оплаты вы мгновенно присоединитесь к сообществу, где успех — это норма. Добро пожаловать в EREBUS AI! 🚀")
    session.close()

@router.message()
async def chat_handler(message: Message):
    print(f"Received message from {message.from_user.id}: {message.text}")
    try:
        user_id = message.from_user.id
        session = Session()
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            user = User(telegram_id=user_id, username=message.from_user.username)
            session.add(user)
            session.commit()
        subscription = session.query(Subscription).filter_by(user_id=user.id).first()
        session.close()
        if not subscription or subscription.status != 'active':
            await message.bot.send_message(message.chat.id, "🌟 Интеллектуальная платформа, разработанная лабораторией ИИ EREBUS AI, для эффективного управления задачами и построения мощного сообщества единомышленников.\n\nС премиум-подпиской за 3000 рублей в месяц вы получаете эксклюзивный доступ к:\n\n🔹 **Интеллектуальной ИИ-системе**: Автоматизированное планирование задач, умные напоминания и персональная мотивация для достижения целей.\n🔹 **Управлению задачами**: Создание, редактирование, завершение задач с приоритетами, дедлайнами и аналитикой прогресса.\n🔹 **Умным напоминаниям**: Автоматические напоминания о задачах, проверка результатов и мотивационные сообщения.\n🔹 **Сообществу лидеров**: Поиск и подключение к единомышленникам по интересам, целям и проектам — от дизайна и программирования до спорта и бизнеса.\n🔹 **Совместным возможностям**: Рекомендации по событиям, коллаборациям и проектам, которые меняют жизнь.\n🔹 **Персонализированным инструментам**: Управление профилем, сохранение личной информации и адаптация под ваши нужды.\n\nПрисоединяйтесь к сообществу амбициозных профессионалов, где каждый шаг ведет к успеху. Оформите подписку /subscribe и начните трансформировать свою продуктивность! 🚀\n\nПо вопросам поддержки обращайтесь: @aleksandrinsider")
            return
        # Все сообщения обрабатываются через ИИ
        if os.getenv("LOCAL") == "1":
            context = context_store.get(f"context:{user_id}", [])
        else:
            try:
                context_data = r.get(f"context:{user_id}")
                if context_data:
                    context = json.loads(context_data.decode('utf-8'))
                else:
                    context = []
            except Exception as e:
                print(f"Error loading context from Redis: {e}")
                context = []
        response = chat_with_ai(message.text, context, user_id)
        print(f"Response: {response}")
        # Сохранить контекст для продолжения
        context.append({"user": message.text, "agent": response})
        if os.getenv("LOCAL") == "1":
            context_store[f"context:{user_id}"] = context
        else:
            try:
                r.set(f"context:{user_id}", json.dumps(context))
            except Exception as e:
                print(f"Error saving context to Redis: {e}")
        await message.bot.send_message(message.chat.id, response)
    except Exception as e:
        print(f"Error in chat_handler: {e}")
        await message.bot.send_message(message.chat.id, "Извините, произошла ошибка. Попробуйте позже.")