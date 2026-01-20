"""
Создание 20 тестовых пользователей с разными тарифами, задачами и делегированием
"""
import datetime
from datetime import timedelta
from models import Session, User, UserProfile, Task, Subscription, SubscriptionTier
import random

def create_test_users():
    session = Session()
    
    # ID пользователя aleksandrinsider для делегирования
    main_user_id = 34
    main_user_telegram_id = 146333757
    
    # Спортивные интересы и цели
    sport_interests = [
        "бег, фитнес, здоровый образ жизни",
        "футбол, командные виды спорта, активный отдых",
        "йога, медитация, растяжка",
        "плавание, триатлон, выносливость",
        "бокс, единоборства, самооборона",
        "велоспорт, путешествия, природа",
        "теннис, ракетбол, игровые виды спорта",
        "кроссфит, силовые тренировки, gym",
        "танцы, аэробика, активный образ жизни",
        "скалолазание, экстремальный спорт, приключения",
        "баскетбол, командная работа, спорт",
        "волейбол, пляжный спорт, активный отдых",
        "бег на длинные дистанции, марафоны, выносливость",
        "гимнастика, акробатика, гибкость",
        "борьба, дзюдо, боевые искусства",
        "хоккей, зимние виды спорта, команда",
        "сноуборд, горные лыжи, экстрим",
        "серфинг, водные виды спорта, море",
        "пилатес, стретчинг, здоровая спина",
        "бадминтон, настольный теннис, ракеточный спорт"
    ]
    
    sport_goals = [
        "пробежать полумарафон за 3 месяца",
        "набрать мышечную массу и улучшить форму",
        "стать гибче и улучшить растяжку",
        "плавать 2 км без остановки",
        "выучить технику бокса и самообороны",
        "проехать 100 км на велосипеде",
        "выиграть турнир по теннису",
        "поднять свой рекорд в становой тяге",
        "освоить современные танцы",
        "покорить сложный скалодромный маршрут",
        "играть в баскетбол на любительском уровне",
        "организовать команду по волейболу",
        "пробежать марафон 42 км",
        "сесть на шпагат за 2 месяца",
        "получить черный пояс по дзюдо",
        "научиться кататься на коньках",
        "покататься на сноуборде в Альпах",
        "научиться серфингу на волнах",
        "избавиться от болей в спине",
        "тренироваться регулярно 5 раз в неделю"
    ]
    
    # Имена для пользователей
    usernames = [
        "sport_fan_2026", "fitness_guru", "yoga_master", "swim_champion",
        "boxing_pro", "cyclist_vlad", "tennis_player", "crossfit_beast",
        "dance_queen", "climber_alex", "basketball_star", "volleyball_king",
        "marathon_runner", "flexible_ninja", "judo_fighter", "hockey_player",
        "snowboarder_max", "surfer_dude", "pilates_expert", "badminton_ace"
    ]
    
    # Задачи связанные со спортом
    sport_tasks = [
        "пробежать 5 км утром",
        "записаться в спортзал",
        "найти партнера для тренировок",
        "купить новые кроссовки для бега",
        "составить план тренировок на месяц",
        "найти тренера по плаванию",
        "пойти на групповую тренировку",
        "подготовиться к соревнованиям",
        "восстановиться после травмы",
        "купить спортивное питание",
        "найти команду для игры в футбол",
        "организовать пробежку с друзьями",
        "растяжка после тренировки",
        "записаться на пробное занятие йогой",
        "найти зал для бокса рядом с домом",
        "купить велосипедный шлем",
        "пойти на теннисный корт",
        "найти спарринг-партнера",
        "организовать турнир по баскетболу",
        "купить абонемент в бассейн"
    ]
    
    # Задачи для делегирования мне
    delegate_to_me_tasks = [
        "организовать совместную пробежку в парке",
        "найти зал для совместных тренировок",
        "организовать спортивное мероприятие",
        "создать чат для спортсменов района",
        "найти тренера для группы"
    ]
    
    # Задачи которые я делегирую им
    delegate_from_me_tasks = [
        "провести пробную тренировку для новичков",
        "поделиться программой тренировок",
        "рассказать о питании для спортсменов",
        "помочь с техникой выполнения упражнений",
        "организовать групповую тренировку"
    ]
    
    tiers = [SubscriptionTier.BRONZE, SubscriptionTier.SILVER, SubscriptionTier.GOLD]
    
    created_users = []
    
    for i in range(20):
        # Создаем уникальный telegram_id
        telegram_id = 200000000 + i
        username = usernames[i]
        
        # Проверяем, существует ли пользователь
        existing = session.query(User).filter_by(telegram_id=telegram_id).first()
        if existing:
            print(f"Пользователь {username} уже существует, пропускаем")
            continue
        
        # Создаем пользователя
        tier = random.choice(tiers)
        user = User(
            telegram_id=telegram_id,
            username=username,
            subscription_tier=tier,
            created_at=datetime.datetime.now(datetime.timezone.utc)
        )
        session.add(user)
        session.flush()
        
        # Создаем подписку
        subscription = Subscription(
            user_id=user.id,
            telegram_id=telegram_id,
            telegram_username=username,
            status='active',
            tier=tier,
            plan='monthly',
            start_date=datetime.datetime.now(datetime.timezone.utc),
            end_date=datetime.datetime.now(datetime.timezone.utc) + timedelta(days=30)
        )
        session.add(subscription)
        
        # Создаем профиль
        profile = UserProfile(
            user_id=user.id,
            skills="спортивные тренировки, мотивация, здоровый образ жизни",
            interests=sport_interests[i],
            goals=sport_goals[i],
            city="Москва",
            bio=f"Увлекаюсь спортом и активным образом жизни. {sport_goals[i]}",
            total_tasks_created=random.randint(10, 50),
            completed_tasks=random.randint(5, 30),
            average_rating=random.randint(7, 10)
        )
        session.add(profile)
        
        # Создаем задачи для пользователя (2-4 задачи)
        num_tasks = random.randint(2, 4)
        for j in range(num_tasks):
            task_title = random.choice(sport_tasks)
            task = Task(
                user_id=user.id,
                title=task_title,
                description=f"Спортивная задача: {task_title}",
                status=random.choice(['pending', 'in_progress', 'completed']),
                created_at=datetime.datetime.now(datetime.timezone.utc)
            )
            session.add(task)
        
        # Некоторые пользователи делегируют задачи мне
        if i % 4 == 0:  # Каждый 4-й пользователь
            delegate_task = Task(
                user_id=user.id,
                title=random.choice(delegate_to_me_tasks),
                description="",
                status='pending',
                delegated_by=user.id,
                delegated_to_username='aleksandrinsider',
                delegation_status='pending',
                created_at=datetime.datetime.now(datetime.timezone.utc)
            )
            session.add(delegate_task)
            print(f"  ✓ {username} делегирует задачу вам")
        
        # Я делегирую задачи некоторым пользователям
        if i % 3 == 0:  # Каждый 3-й пользователь
            my_task = Task(
                user_id=main_user_id,
                title=random.choice(delegate_from_me_tasks),
                description="",
                status='pending',
                delegated_by=main_user_id,
                delegated_to_username=username,
                delegation_status='pending',
                created_at=datetime.datetime.now(datetime.timezone.utc)
            )
            session.add(my_task)
            print(f"  ✓ Вы делегируете задачу пользователю {username}")
        
        created_users.append(user)
        print(f"✓ Создан пользователь: {username} (tier: {tier.value})")
    
    try:
        session.commit()
        print(f"\n✅ Успешно создано {len(created_users)} тестовых пользователей!")
    except Exception as e:
        session.rollback()
        print(f"\n❌ Ошибка при создании пользователей: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    create_test_users()
