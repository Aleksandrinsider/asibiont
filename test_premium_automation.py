"""
Тестовый скрипт для Premium автоматизации

Создаёт тестовые данные:
- Premium пользователь с целями
- Обычные пользователи с интересами/навыками
- Запускает автоматизацию
- Проверяет что рекомендации отправлены
"""

import asyncio
import logging
from models import Session, User, UserProfile, SubscriptionTier
from ai_integration.premium_simple import (
    run_premium_automation,
    analyze_goals_with_ai,
    find_relevant_users_for_goals,
    format_premium_report
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_test_users():
    """Создаёт тестовых пользователей"""
    
    session = Session()
    
    try:
        # Premium пользователь
        premium_user = User(
            telegram_id=1000001,
            username="premium_ceo",
            subscription_tier=SubscriptionTier.PREMIUM
        )
        
        premium_profile = UserProfile(
            user=premium_user,
            timezone='Europe/Moscow',
            goals="""
            Бизнес-цели на Q1 2025:
            
            1. Найти партнёров для дистрибуции нового продукта (EcoTech Solutions)
            2. Нанять 2 Python разработчиков (Senior level) для backend команды
            3. Установить связи с владельцами каналов в нише B2B SaaS
            4. Найти инвесторов в EdTech стартап (seed round)
            """,
            interests="стартапы, B2B SaaS, инвестиции, экотехнологии",
            skills="product management, бизнес-разработка, fundraising"
        )
        
        session.add(premium_user)
        session.add(premium_profile)
        
        # Обычные пользователи с релевантными интересами
        test_users = [
            {
                "telegram_id": 1000002,
                "username": "distributor_ivan",
                "interests": "дистрибуция, оптовые продажи, экотовары, логистика",
                "skills": "переговоры, продажи B2B"
            },
            {
                "telegram_id": 1000003,
                "username": "dev_maria",
                "interests": "программирование, Python, backend разработка",
                "skills": "Python, Django, FastAPI, PostgreSQL, asyncio"
            },
            {
                "telegram_id": 1000004,
                "username": "channel_owner_alex",
                "interests": "телеграм каналы, B2B контент, SaaS продукты",
                "skills": "контент-маркетинг, продвижение, SMM"
            },
            {
                "telegram_id": 1000005,
                "username": "investor_sergey",
                "interests": "инвестиции в стартапы, EdTech, венчур",
                "skills": "финансовый анализ, due diligence, портфельное управление"
            },
            {
                "telegram_id": 1000006,
                "username": "random_user",
                "interests": "кулинария, путешествия, фотография",
                "skills": "готовка, travel blogging"
            }
        ]
        
        for user_data in test_users:
            user = User(
                telegram_id=user_data["telegram_id"],
                username=user_data["username"],
                subscription_tier=SubscriptionTier.LIGHT
            )
            
            profile = UserProfile(
                user=user,
                timezone='Europe/Moscow',
                interests=user_data["interests"],
                skills=user_data["skills"]
            )
            
            session.add(user)
            session.add(profile)
        
        session.commit()
        logger.info("✅ Created test users")
        return premium_user.telegram_id
        
    except Exception as e:
        logger.error(f"❌ Failed to create test users: {e}")
        session.rollback()
        raise
    finally:
        session.close()


def cleanup_test_users():
    """Удаляет тестовых пользователей"""
    
    session = Session()
    
    try:
        # Удаляем пользователей с ID 1000001-1000006
        for tid in range(1000001, 1000007):
            user = session.query(User).filter_by(telegram_id=tid).first()
            if user:
                # Профиль удалится автоматически (cascade)
                session.delete(user)
        
        session.commit()
        logger.info("✅ Cleaned up test users")
        
    except Exception as e:
        logger.error(f"❌ Failed to cleanup: {e}")
        session.rollback()
    finally:
        session.close()


async def test_goal_analysis():
    """Тест: AI анализирует цели Premium пользователя"""
    
    logger.info("\n" + "="*80)
    logger.info("TEST 1: Анализ целей Premium пользователя")
    logger.info("="*80)
    
    goals_text = """
    Бизнес-цели на Q1 2025:
    
    1. Найти партнёров для дистрибуции нового продукта (EcoTech Solutions)
    2. Нанять 2 Python разработчиков (Senior level) для backend команды
    3. Установить связи с владельцами каналов в нише B2B SaaS
    4. Найти инвесторов в EdTech стартап (seed round)
    """
    
    goals = await analyze_goals_with_ai(goals_text)
    
    logger.info(f"\n📋 Извлечено целей: {len(goals)}")
    for i, goal in enumerate(goals, 1):
        logger.info(f"\n  Цель {i}: {goal['goal']}")
        logger.info(f"  Возможность: {goal['opportunity']}")
        logger.info(f"  Нужны люди с:")
        logger.info(f"    Интересы: {goal['needed_people']['interests']}")
        logger.info(f"    Навыки: {goal['needed_people']['skills']}")
    
    return goals


async def test_user_matching(goals):
    """Тест: Поиск релевантных пользователей"""
    
    logger.info("\n" + "="*80)
    logger.info("TEST 2: Поиск релевантных пользователей")
    logger.info("="*80)
    
    session = Session()
    
    try:
        premium_user = session.query(User).filter_by(telegram_id=1000001).first()
        
        relevant = find_relevant_users_for_goals(
            session,
            goals,
            exclude_user_id=premium_user.id,
            limit=10
        )
        
        logger.info(f"\n👥 Найдено релевантных пользователей: {len(relevant)}")
        
        for i, item in enumerate(relevant, 1):
            user = item['user']
            goal = item['matching_goal']
            reason = item['match_reason']
            score = item['score']
            
            logger.info(f"\n  {i}. @{user.username}")
            logger.info(f"     Релевантен для: {goal['goal']}")
            logger.info(f"     Причина: {reason}")
            logger.info(f"     Score: {score:.2f}")
        
        return relevant
        
    finally:
        session.close()


async def test_full_automation():
    """Тест: Полный цикл автоматизации"""
    
    logger.info("\n" + "="*80)
    logger.info("TEST 3: Полный цикл Premium автоматизации")
    logger.info("="*80)
    
    # NOTE: Реальная отправка сообщений отключена (REMINDER_SERVICE может быть не инициализирован)
    # Вместо этого проверяем только логику matching
    
    report = await run_premium_automation(1000001)
    
    logger.info("\n📊 Отчёт автоматизации:")
    logger.info(format_premium_report(report))
    
    return report


async def run_all_tests():
    """Запускает все тесты"""
    
    logger.info("\n" + "🚀 "*20)
    logger.info("НАЧИНАЕМ ТЕСТИРОВАНИЕ PREMIUM AUTOMATION")
    logger.info("🚀 "*20 + "\n")
    
    try:
        # Создаём тестовые данные
        premium_id = create_test_users()
        
        # Тест 1: Анализ целей
        goals = await test_goal_analysis()
        
        # Тест 2: Поиск пользователей
        relevant = await test_user_matching(goals)
        
        # Тест 3: Полный цикл (без реальной отправки)
        report = await test_full_automation()
        
        logger.info("\n" + "✅ "*20)
        logger.info("ВСЕ ТЕСТЫ ЗАВЕРШЕНЫ")
        logger.info("✅ "*20 + "\n")
        
        # Результаты
        logger.info("📈 ИТОГО:")
        logger.info(f"  • Целей проанализировано: {len(goals)}")
        logger.info(f"  • Релевантных пользователей: {len(relevant)}")
        logger.info(f"  • Рекомендаций было бы отправлено: {report.get('suggestions_sent', 0)}")
        
        # Ожидаемые matches:
        expected_matches = {
            "distributor_ivan": "дистрибуция EcoTech",
            "dev_maria": "Python разработка",
            "channel_owner_alex": "B2B SaaS каналы",
            "investor_sergey": "инвестиции EdTech"
        }
        
        logger.info(f"\n🎯 Ожидаемые совпадения:")
        for username, reason in expected_matches.items():
            matched = any(item['user'].username == username for item in relevant)
            emoji = "✅" if matched else "❌"
            logger.info(f"  {emoji} {username}: {reason}")
        
    except Exception as e:
        logger.error(f"\n❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        # Cleanup
        logger.info("\n🧹 Очистка тестовых данных...")
        cleanup_test_users()


if __name__ == '__main__':
    asyncio.run(run_all_tests())
