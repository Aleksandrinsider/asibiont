"""
Premium Scheduler - планировщик для фоновых задач Premium автоматизации

Запускает регулярные проверки:
- Утренний и вечерний сбор инсайтов
- Real-time обработка событий
- Еженедельная аналитика
"""

import logging
import asyncio
from typing import Optional
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

from models import Session, User
from ai_integration.premium_simple import collect_premium_insights

logger = logging.getLogger(__name__)

# Глобальный scheduler
PREMIUM_SCHEDULER: Optional[AsyncIOScheduler] = None


def start_premium_scheduler():
    """Запускает фоновый scheduler для Premium автоматизации"""
    
    global PREMIUM_SCHEDULER
    
    if PREMIUM_SCHEDULER and PREMIUM_SCHEDULER.running:
        logger.warning("[PREMIUM_SCHEDULER] Already running")
        return
    
    PREMIUM_SCHEDULER = AsyncIOScheduler(timezone=pytz.UTC)  # type: ignore
    
    # Утренний сбор инсайтов (9:00 UTC = 12:00 MSK для большинства)
    PREMIUM_SCHEDULER.add_job(
        morning_insights_job,
        CronTrigger(hour=6, minute=0),  # 9:00 MSK
        id='premium_morning_insights',
        name='Premium Morning Insights',
        replace_existing=True
    )
    
    # Вечерний сбор инсайтов (20:00 UTC = 23:00 MSK)
    PREMIUM_SCHEDULER.add_job(
        evening_insights_job,
        CronTrigger(hour=17, minute=0),  # 20:00 MSK
        id='premium_evening_insights',
        name='Premium Evening Insights',
        replace_existing=True
    )
    
    # Еженедельная аналитика (понедельник 10:00 MSK)
    PREMIUM_SCHEDULER.add_job(
        weekly_analytics_job,
        CronTrigger(day_of_week='mon', hour=7, minute=0),  # Понедельник 10:00 MSK
        id='premium_weekly_analytics',
        name='Premium Weekly Analytics',
        replace_existing=True
    )
    
    PREMIUM_SCHEDULER.start()
    logger.info("[PREMIUM_SCHEDULER] Started successfully")


def stop_premium_scheduler():
    """Останавливает scheduler"""
    
    global PREMIUM_SCHEDULER
    
    if PREMIUM_SCHEDULER and PREMIUM_SCHEDULER.running:
        PREMIUM_SCHEDULER.shutdown()
        logger.info("[PREMIUM_SCHEDULER] Stopped")


async def morning_insights_job():
    """Утренний сбор инсайтов для всех Premium пользователей"""
    
    logger.info("[PREMIUM_SCHEDULER] Running morning insights job")
    
    session = Session()
    try:
        # Получаем всех Premium пользователей
        from models import SubscriptionTier
        premium_users = session.query(User).filter(
            User.subscription_tier == SubscriptionTier.PREMIUM
        ).all()
        
        logger.info(f"[PREMIUM_SCHEDULER] Found {len(premium_users)} Premium users")
        
        # Собираем инсайты для каждого
        for user in premium_users:
            try:
                report = await collect_premium_insights(user.telegram_id, mode='collect')
                logger.info(f"[PREMIUM_SCHEDULER] Collected {report.get('insights_collected', 0)} insights for user {user.telegram_id}")
            except Exception as e:
                logger.error(f"[PREMIUM_SCHEDULER] Error collecting insights for {user.telegram_id}: {e}")
        
        logger.info("[PREMIUM_SCHEDULER] Morning insights job completed")
        
    except Exception as e:
        logger.error(f"[PREMIUM_SCHEDULER] Error in morning job: {e}")
    finally:
        session.close()


async def evening_insights_job():
    """Вечерний сбор инсайтов для всех Premium пользователей"""
    
    logger.info("[PREMIUM_SCHEDULER] Running evening insights job")
    
    session = Session()
    try:
        # Получаем всех Premium пользователей
        from models import SubscriptionTier
        premium_users = session.query(User).filter(
            User.subscription_tier == SubscriptionTier.PREMIUM
        ).all()
        
        logger.info(f"[PREMIUM_SCHEDULER] Found {len(premium_users)} Premium users")
        
        # Собираем инсайты для каждого
        for user in premium_users:
            try:
                report = await collect_premium_insights(user.telegram_id, mode='collect')
                logger.info(f"[PREMIUM_SCHEDULER] Collected {report.get('insights_collected', 0)} insights for user {user.telegram_id}")
            except Exception as e:
                logger.error(f"[PREMIUM_SCHEDULER] Error collecting insights for {user.telegram_id}: {e}")
        
        logger.info("[PREMIUM_SCHEDULER] Evening insights job completed")
        
    except Exception as e:
        logger.error(f"[PREMIUM_SCHEDULER] Error in evening job: {e}")
    finally:
        session.close()


async def weekly_analytics_job():
    """Еженедельная аналитика для Premium пользователей"""
    
    logger.info("[PREMIUM_SCHEDULER] Running weekly analytics job")
    
    session = Session()
    try:
        # Получаем всех Premium пользователей
        from models import SubscriptionTier
        premium_users = session.query(User).filter(
            User.subscription_tier == SubscriptionTier.PREMIUM
        ).all()
        
        logger.info(f"[PREMIUM_SCHEDULER] Found {len(premium_users)} Premium users for weekly analytics")
        
        # Для еженедельной аналитики добавляем дополнительный акцент на тренды
        for user in premium_users:
            try:
                # Обычный сбор инсайтов (включает тренды)
                report = await collect_premium_insights(user.telegram_id, mode='collect')
                logger.info(f"[PREMIUM_SCHEDULER] Weekly report for {user.telegram_id}: {report.get('breakdown', {})}")
            except Exception as e:
                logger.error(f"[PREMIUM_SCHEDULER] Error in weekly analytics for {user.telegram_id}: {e}")
        
        logger.info("[PREMIUM_SCHEDULER] Weekly analytics job completed")
        
    except Exception as e:
        logger.error(f"[PREMIUM_SCHEDULER] Error in weekly job: {e}")
    finally:
        session.close()


async def trigger_insights_for_premium_user(premium_user_id: int):
    """
    Триггерит немедленный сбор инсайтов для конкретного Premium пользователя
    
    Используется для:
    - Real-time событий (создал задачу → собрать market opportunities)
    - Ручного запуска (команда /premium_insights)
    
    Args:
        premium_user_id: Telegram ID Premium пользователя
    """
    
    logger.info(f"[PREMIUM_SCHEDULER] Triggering immediate insights for {premium_user_id}")
    
    try:
        report = await collect_premium_insights(premium_user_id, mode='collect')
        logger.info(f"[PREMIUM_SCHEDULER] Collected {report.get('insights_collected', 0)} insights")
        return report
    except Exception as e:
        logger.error(f"[PREMIUM_SCHEDULER] Error triggering insights: {e}")
        return {"error": str(e)}


# Интеграция с событиями

async def on_premium_task_created(premium_user_id: int, task_id: int, task_description: str):
    """
    Обработчик события: Premium пользователь создал задачу
    
    Триггерит:
    1. Real-time поиск релевантных людей (уже работает)
    2. Market opportunities анализ (добавляем сюда)
    
    Args:
        premium_user_id: Telegram ID Premium пользователя
        task_id: ID созданной задачи
        task_description: Описание задачи
    """
    
    logger.info(f"[PREMIUM_SCHEDULER] Premium task created event: user={premium_user_id}, task={task_id}")
    
    # Запускаем фоновый сбор market opportunities
    asyncio.create_task(_collect_market_opportunities_background(premium_user_id))


async def _collect_market_opportunities_background(premium_user_id: int):
    """Фоновый сбор market opportunities (не блокирует основной поток)"""
    
    try:
        from ai_integration.premium_simple import find_market_opportunities
        
        session = Session()
        try:
            insights = await find_market_opportunities(premium_user_id, session)
            
            if insights:
                # Сохраняем в профиль
                user = session.query(User).filter_by(telegram_id=premium_user_id).first()
                if user:
                    from models import UserProfile
                    import json
                    
                    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
                    if profile:
                        existing = []
                        if profile.pending_premium_recommendations:
                            try:
                                existing = json.loads(profile.pending_premium_recommendations)
                            except Exception:
                                pass
                        
                        existing.extend(insights)
                        profile.pending_premium_recommendations = json.dumps(existing[-20:], ensure_ascii=False)
                        session.commit()
                        
                        logger.info(f"[PREMIUM_SCHEDULER] Added {len(insights)} market opportunities for {premium_user_id}")
        finally:
            session.close()
            
    except Exception as e:
        logger.error(f"[PREMIUM_SCHEDULER] Error collecting market opportunities: {e}")


async def on_any_task_created(user_id: int, task_description: str):
    """
    Обработчик события: ЛЮБОЙ пользователь создал задачу
    
    Проверяет: может ли это быть интересно Premium пользователям?
    (для market opportunities анализа)
    
    Args:
        user_id: ID пользователя создавшего задачу
        task_description: Описание задачи
    """
    
    # Этот хендлер можно интегрировать в handlers.py
    # Пока оставляем placeholder
    pass
