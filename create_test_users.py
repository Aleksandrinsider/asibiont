#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Создание 20 тестовых пользователей в Railway БД с задачами, разными тарифами и интересами"""

import os
import sys
from datetime import datetime, timedelta
import random

# Устанавливаем переменную окружения чтобы использовать Railway БД
os.environ['LOCAL'] = '0'

from models import Session, User, Task, UserProfile, SubscriptionTier
from ai_integration import encrypt_data
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Тарифы для распределения
TIERS = [SubscriptionTier.LIGHT, SubscriptionTier.STANDARD, SubscriptionTier.PREMIUM]

# Списки интересов (спорт будет у всех + дополнительные)
ADDITIONAL_INTERESTS = [
    "программирование",
    "искусственный интеллект",
    "путешествия",
    "музыка",
    "чтение",
    "кино",
    "фотография",
    "кулинария",
    "дизайн",
    "бизнес",
    "маркетинг",
    "психология",
    "языки",
    "медитация",
    "йога"
]

# Навыки
SKILLS = [
    "Python",
    "JavaScript",
    "React",
    "Node.js",
    "SQL",
    "Machine Learning",
    "Data Science",
    "UI/UX Design",
    "Project Management",
    "Marketing",
    "SMM",
    "Copywriting",
    "Video Editing",
    "3D Modeling",
    "Game Development"
]

# Города
CITIES = [
    "Москва",
    "Санкт-Петербург",
    "Новосибирск",
    "Екатеринбург",
    "Казань",
    "Нижний Новгород",
    "Челябинск",
    "Самара",
    "Омск",
    "Ростов-на-Дону"
]

# Компании
COMPANIES = [
    "Яндекс",
    "Сбер",
    "VK",
    "Тинькофф",
    "Ozon",
    "Wildberries",
    "МТС",
    "МегаФон",
    "Авито",
    "Kaspersky"
]

# Цели
GOALS = [
    "Развитие в IT",
    "Запуск стартапа",
    "Изучение английского языка",
    "Здоровый образ жизни",
    "Финансовая независимость",
    "Карьерный рост",
    "Путешествия по миру",
    "Создание пассивного дохода",
    "Изучение новых технологий",
    "Нетворкинг и связи"
]

# Примеры задач
TASK_TEMPLATES = [
    "Тренировка в зале",
    "Пробежка в парке",
    "Йога сессия",
    "Плавание в бассейне",
    "Футбол с друзьями",
    "Велопрогулка",
    "Утренняя зарядка",
    "Растяжка",
    "Встреча с клиентом",
    "Написать отчет",
    "Код ревью проекта",
    "Подготовка презентации",
    "Созвон с командой",
    "Обучающий курс",
    "Прочитать книгу",
    "Медитация",
    "Приготовить ужин",
    "Закупить продукты"
]

def create_test_users():
    """Создание 20 тестовых пользователей"""
    try:
        session = Session()
        
        # Проверяем что пользователь 146333757 существует
        preserved_user = session.query(User).filter_by(telegram_id=146333757).first()
        if not preserved_user:
            logger.warning("⚠️ Пользователь 146333757 не найден в БД")
        else:
            logger.info(f"✅ Пользователь 146333757 найден: @{preserved_user.username}")
        
        created_count = 0
        
        for i in range(1, 21):
            # Генерируем уникальный telegram_id
            telegram_id = 200000 + i
            
            # Проверяем что такого пользователя еще нет
            existing = session.query(User).filter_by(telegram_id=telegram_id).first()
            if existing:
                logger.info(f"Пропускаем {telegram_id} - уже существует")
                continue
            
            # Создаем пользователя
            first_name = f"Тест{i}"
            username = f"test_user_{i}"
            
            # Распределяем тарифы равномерно
            tier = TIERS[i % len(TIERS)]
            
            user = User(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
                subscription_tier=tier,
                photo_url=None
            )
            session.add(user)
            session.flush()  # Получаем ID пользователя
            
            # Создаем профиль
            # Спорт + 2-4 дополнительных интересов
            num_interests = random.randint(2, 4)
            interests = ["спорт"] + random.sample(ADDITIONAL_INTERESTS, num_interests)
            
            # 2-3 навыка
            num_skills = random.randint(2, 3)
            skills = random.sample(SKILLS, num_skills)
            
            # 1-2 цели
            num_goals = random.randint(1, 2)
            goals = random.sample(GOALS, num_goals)
            
            profile = UserProfile(
                user_id=user.id,
                city=random.choice(CITIES),
                company=random.choice(COMPANIES),
                position=f"{'Senior ' if i % 3 == 0 else ''}{'Middle ' if i % 3 == 1 else ''}Developer" if i % 2 == 0 else "Manager",
                interests=", ".join(interests),
                skills=", ".join(skills),
                goals=", ".join(goals),
                languages="Русский, Английский"
            )
            session.add(profile)
            
            # Создаем 3-7 задач для пользователя
            num_tasks = random.randint(3, 7)
            task_samples = random.sample(TASK_TEMPLATES, min(num_tasks, len(TASK_TEMPLATES)))
            
            for j, task_title in enumerate(task_samples):
                # Задачи на разные дни
                days_ahead = random.randint(0, 7)
                hours = random.randint(8, 20)
                minutes = random.choice([0, 15, 30, 45])
                reminder_time = datetime.now() + timedelta(days=days_ahead, hours=hours-datetime.now().hour, minutes=minutes-datetime.now().minute)
                
                # Описание задачи (опционально, шифруем)
                descriptions = [
                    "Важно не пропустить",
                    "Запланировано на сегодня",
                    "Высокий приоритет",
                    "Можно перенести",
                    "Напомнить заранее"
                ]
                
                description = random.choice(descriptions) if random.random() > 0.3 else None
                encrypted_description = encrypt_data(description) if description else None
                
                # Статус задачи (большинство pending, некоторые completed)
                status = "completed" if random.random() < 0.2 else "pending"
                
                task = Task(
                    user_id=user.id,
                    title=task_title,
                    description=encrypted_description,
                    reminder_time=reminder_time,
                    status=status,
                    estimated_duration=random.choice([15, 30, 45, 60, 90, 120]),
                    actual_completion_time=datetime.now() - timedelta(hours=random.randint(1, 48)) if status == "completed" else None
                )
                session.add(task)
            
            created_count += 1
            logger.info(f"✅ Создан пользователь {created_count}/20: @{username} ({tier.value}) - {len(task_samples)} задач")
        
        session.commit()
        logger.info(f"\n🎉 Успешно создано {created_count} пользователей с задачами, тарифами и интересами!")
        
        # Показываем статистику
        total_users = session.query(User).count()
        total_tasks = session.query(Task).count()
        total_profiles = session.query(UserProfile).count()
        
        logger.info(f"\n📊 Статистика БД:")
        logger.info(f"Всего пользователей: {total_users}")
        logger.info(f"Всего задач: {total_tasks}")
        logger.info(f"Всего профилей: {total_profiles}")
        
        # Показываем распределение по тарифам
        for tier in TIERS:
            count = session.query(User).filter_by(subscription_tier=tier).count()
            logger.info(f"Тариф {tier.value}: {count} пользователей")
        
        session.close()
        
    except Exception as e:
        logger.error(f"❌ Ошибка при создании пользователей: {e}", exc_info=True)
        session.rollback()
        session.close()
        sys.exit(1)

if __name__ == '__main__':
    confirm = input("🚀 Создать 20 тестовых пользователей в Railway БД?\nВведите 'YES' для подтверждения: ")
    if confirm == 'YES':
        create_test_users()
    else:
        logger.info("Операция отменена")
