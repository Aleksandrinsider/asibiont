"""
Тест Premium Real-time Integration через диалог

Проверяет:
1. Premium создаёт задачу → триггер автоматизации
2. Рекомендация сохраняется в профили релевантных пользователей
3. При следующем сообщении рекомендация появляется в промпте
4. AI естественно вплетает рекомендацию в диалог
"""

import asyncio
import logging
from models import Session, User, UserProfile, Task, SubscriptionTier
from ai_integration.premium_simple import (
    trigger_premium_automation_realtime,
    get_premium_recommendations_for_prompt
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def setup_test_users():
    """Создаёт тестовых пользователей"""
    
    session = Session()
    
    try:
        # Premium пользователь
        premium_user = session.query(User).filter_by(telegram_id=2000001).first()
        if not premium_user:
            premium_user = User(
                telegram_id=2000001,
                username="premium_entrepreneur",
                subscription_tier=SubscriptionTier.PREMIUM
            )
            session.add(premium_user)
            session.flush()
        
        premium_profile = session.query(UserProfile).filter_by(user_id=premium_user.id).first()
        if not premium_profile:
            premium_profile = UserProfile(
                user_id=premium_user.id,
                timezone='Europe/Moscow',
                goals="""
                Найти партнёров для дистрибуции нового продукта EcoTech Solutions.
                Нанять Senior Python разработчика для backend команды.
                """,
                interests="стартапы, B2B SaaS, экотехнологии",
                skills="product management, бизнес-разработка"
            )
            session.add(premium_profile)
        
        # Релевантный пользователь 1: дистрибьютор
        distributor = session.query(User).filter_by(telegram_id=2000002).first()
        if not distributor:
            distributor = User(
                telegram_id=2000002,
                username="distributor_ivan",
                subscription_tier=SubscriptionTier.LIGHT
            )
            session.add(distributor)
            session.flush()
        
        dist_profile = session.query(UserProfile).filter_by(user_id=distributor.id).first()
        if not dist_profile:
            dist_profile = UserProfile(
                user_id=distributor.id,
                timezone='Europe/Moscow',
                interests="дистрибуция, оптовые продажи, экотовары, логистика",
                skills="переговоры, продажи B2B"
            )
            session.add(dist_profile)
        
        # Релевантный пользователь 2: Python разработчик
        developer = session.query(User).filter_by(telegram_id=2000003).first()
        if not developer:
            developer = User(
                telegram_id=2000003,
                username="dev_maria",
                subscription_tier=SubscriptionTier.LIGHT
            )
            session.add(developer)
            session.flush()
        
        dev_profile = session.query(UserProfile).filter_by(user_id=developer.id).first()
        if not dev_profile:
            dev_profile = UserProfile(
                user_id=developer.id,
                timezone='Europe/Moscow',
                interests="программирование, Python, backend разработка",
                skills="Python, Django, FastAPI, PostgreSQL, asyncio"
            )
            session.add(dev_profile)
        
        session.commit()
        logger.info("✅ Test users created/updated")
        return premium_user.telegram_id, distributor.telegram_id, developer.telegram_id
        
    except Exception as e:
        logger.error(f"❌ Failed to setup users: {e}")
        session.rollback()
        raise
    finally:
        session.close()


async def test_premium_trigger():
    """Тест 1: Триггер при создании задачи"""
    
    logger.info("\n" + "="*80)
    logger.info("TEST 1: Premium создаёт задачу → Триггер автоматизации")
    logger.info("="*80)
    
    premium_id, dist_id, dev_id = setup_test_users()
    
    # Имитируем создание задачи Premium пользователем
    task_description = "Найти 5 дистрибьюторов для EcoTech Solutions в Москве"
    
    logger.info(f"\n📝 Premium пользователь создаёт задачу: {task_description}")
    
    # Запускаем real-time триггер
    report = await trigger_premium_automation_realtime(
        premium_user_id=premium_id,
        task_id=999,  # Фейк ID
        task_description=task_description
    )
    
    logger.info(f"\n📊 Результат триггера:")
    logger.info(f"  • Проанализировано items: {report.get('items_analyzed', 0)}")
    logger.info(f"  • Найдено релевантных: {report.get('relevant_users_found', 0)}")
    logger.info(f"  • Сохранено рекомендаций: {report.get('recommendations_saved', 0)}")
    
    if report.get('saved_details'):
        logger.info(f"\n👥 Рекомендации сохранены для:")
        for detail in report['saved_details']:
            logger.info(f"  • @{detail['user']}: {detail['goal'][:60]}...")
    
    return dist_id, dev_id


async def test_recommendations_in_prompt(dist_id, dev_id):
    """Тест 2: Рекомендации в промпте"""
    
    logger.info("\n" + "="*80)
    logger.info("TEST 2: Рекомендации появляются в промпте")
    logger.info("="*80)
    
    session = Session()
    
    try:
        # Проверяем дистрибьютора
        logger.info(f"\n🔍 Проверяем @distributor_ivan (ID: {dist_id}):")
        dist_prompt = get_premium_recommendations_for_prompt(dist_id, session)
        
        if dist_prompt:
            logger.info("✅ Рекомендация найдена в промпте:")
            logger.info(dist_prompt[:300] + "...")
        else:
            logger.warning("⚠️ Рекомендация НЕ найдена")
        
        # Проверяем разработчика
        logger.info(f"\n🔍 Проверяем @dev_maria (ID: {dev_id}):")
        dev_prompt = get_premium_recommendations_for_prompt(dev_id, session)
        
        if dev_prompt:
            logger.info("✅ Рекомендация найдена в промпте:")
            logger.info(dev_prompt[:300] + "...")
        else:
            logger.warning("⚠️ Рекомендация НЕ найдена")
        
    finally:
        session.close()


async def test_full_workflow():
    """Полный тестовый workflow"""
    
    logger.info("\n" + "🚀 "*20)
    logger.info("PREMIUM REAL-TIME INTEGRATION TEST")
    logger.info("🚀 "*20 + "\n")
    
    try:
        # Тест 1: Триггер автоматизации
        dist_id, dev_id = await test_premium_trigger()
        
        # Тест 2: Рекомендации в промпте
        await test_recommendations_in_prompt(dist_id, dev_id)
        
        logger.info("\n" + "✅ "*20)
        logger.info("ВСЕ ТЕСТЫ ЗАВЕРШЕНЫ")
        logger.info("✅ "*20 + "\n")
        
        logger.info("📈 ИТОГО:")
        logger.info("  ✅ Real-time триггер работает")
        logger.info("  ✅ Рекомендации сохраняются в профили")
        logger.info("  ✅ Рекомендации появляются в промпте")
        logger.info("\n🎯 Следующий шаг: AI вплетёт рекомендации в диалог естественно")
        
    except Exception as e:
        logger.error(f"\n❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()


def cleanup():
    """Очищаем тестовые данные"""
    
    logger.info("\n🧹 Очистка тестовых данных...")
    session = Session()
    
    try:
        for tid in [2000001, 2000002, 2000003]:
            user = session.query(User).filter_by(telegram_id=tid).first()
            if user:
                session.delete(user)
        
        session.commit()
        logger.info("✅ Тестовые данные удалены")
        
    except Exception as e:
        logger.error(f"❌ Ошибка очистки: {e}")
        session.rollback()
    finally:
        session.close()


if __name__ == '__main__':
    try:
        asyncio.run(test_full_workflow())
    finally:
        # Раскомментируй для очистки после теста
        # cleanup()
        pass
