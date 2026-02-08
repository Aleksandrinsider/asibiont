"""
Automated Marketing Service для Premium пользователей

Запускает автономный маркетинговый агент для Premium пользователей по расписанию.
Запускается как фоновый процесс вместе с ботом.
"""

import asyncio
import logging
from datetime import datetime, timedelta
import pytz
from models import Session, User, SubscriptionTier
from ai_integration.premium.autonomous_marketing_mvp import AutonomousMarketingAgentMVP

logger = logging.getLogger(__name__)


class AutoMarketingService:
    """
    Сервис для автоматического запуска маркетинга для Premium пользователей
    
    Работа:
    - Проверяет Premium пользователей каждые 6 часов
    - Запускает маркетинговый цикл для тех, у кого настроен telegram_channel
    - Логирует результаты
    """
    
    def __init__(self, bot=None, check_interval_hours=6):
        """
        Args:
            bot: Telegram bot instance для отправки уведомлений (опционально)
            check_interval_hours: Интервал проверки в часах (по умолчанию 6)
        """
        self.bot = bot
        self.check_interval_hours = check_interval_hours
        self.agent = AutonomousMarketingAgentMVP()
        self.running = False
        logger.info(f"[AUTO_MARKETING_SERVICE] Initialized with interval {check_interval_hours}h")
    
    async def get_premium_users_for_marketing(self):
        """
        Получает список Premium пользователей готовых для автомаркетинга
        
        Критерии:
        - Подписка PREMIUM
        - Настроен telegram_channel
        - Активная подписка
        
        Returns:
            List[int]: Список telegram_id пользователей
        """
        session = Session()
        try:
            from models import Subscription
            
            now = datetime.utcnow()
            
            # Находим активных Premium пользователей с telegram_channel
            premium_users = session.query(User).join(Subscription).filter(
                User.subscription_tier == SubscriptionTier.PREMIUM,
                User.telegram_channel.isnot(None),
                User.telegram_channel != '',
                Subscription.plan == 'premium',
                Subscription.end_date > now
            ).all()
            
            user_ids = [u.telegram_id for u in premium_users]
            
            logger.info(f"[AUTO_MARKETING_SERVICE] Found {len(user_ids)} Premium users ready for auto-marketing")
            return user_ids
            
        except Exception as e:
            logger.error(f"[AUTO_MARKETING_SERVICE] Error getting Premium users: {e}")
            return []
        finally:
            session.close()
    
    async def run_marketing_for_user(self, user_id: int):
        """
        Запускает маркетинговый цикл для конкретного пользователя
        
        Args:
            user_id: Telegram ID пользователя
        """
        try:
            logger.info(f"[AUTO_MARKETING_SERVICE] Starting marketing cycle for user {user_id}")
            
            # Проверяем включён ли автомаркетинг у пользователя
            session = Session()
            try:
                user = session.query(User).filter_by(telegram_id=user_id).first()
                if not user:
                    logger.warning(f"[AUTO_MARKETING_SERVICE] User {user_id} not found")
                    return {'status': 'error', 'reason': 'user_not_found'}
                
                profile = session.query(UserProfile).filter_by(user_id=user.id).first()
                if profile and not profile.auto_marketing_enabled:
                    logger.info(f"[AUTO_MARKETING_SERVICE] Auto-marketing disabled for user {user_id}")
                    return {'status': 'skipped', 'reason': 'auto_marketing_disabled'}
            finally:
                session.close()
            
            # Запускаем автономный маркетинг
            report = await self.agent.run_autonomous_marketing_cycle(user_id)
            
            # Логируем результат
            if report['status'] == 'success':
                logger.info(f"[AUTO_MARKETING_SERVICE] ✅ Success for user {user_id}: {report['posts_published']} posts published")
                
                # Отправляем уведомление пользователю (если есть бот)
                if self.bot and report['posts_published'] > 0:
                    try:
                        message = f"🤖 Автономный маркетинг завершён!\n\n✅ Опубликовано постов: {report['posts_published']}\n⏰ Следующий запуск через {self.check_interval_hours}ч"
                        await self.bot.send_message(user_id, message)
                    except Exception as e:
                        logger.warning(f"[AUTO_MARKETING_SERVICE] Could not send notification to {user_id}: {e}")
            else:
                logger.warning(f"[AUTO_MARKETING_SERVICE] ⚠️ Failed for user {user_id}: {report.get('errors', [])}")
            
            return report
            
        except Exception as e:
            logger.error(f"[AUTO_MARKETING_SERVICE] Error running marketing for user {user_id}: {e}")
            return {'status': 'error', 'user_id': user_id, 'errors': [str(e)]}
    
    async def run_marketing_cycle(self):
        """
        Основной цикл: находит Premium пользователей и запускает маркетинг
        """
        try:
            logger.info("[AUTO_MARKETING_SERVICE] 🚀 Starting marketing cycle")
            
            # Получаем Premium пользователей
            user_ids = await self.get_premium_users_for_marketing()
            
            if not user_ids:
                logger.info("[AUTO_MARKETING_SERVICE] No Premium users ready for marketing")
                return
            
            # Запускаем маркетинг для каждого пользователя
            reports = []
            for user_id in user_ids:
                try:
                    report = await self.run_marketing_for_user(user_id)
                    reports.append(report)
                    
                    # Пауза между пользователями (чтобы не перегрузить API)
                    await asyncio.sleep(120)  # 2 минуты между пользователями
                    
                except Exception as e:
                    logger.error(f"[AUTO_MARKETING_SERVICE] Failed for user {user_id}: {e}")
                    continue
            
            # Общая статистика
            successful = sum(1 for r in reports if r['status'] == 'success')
            total_posts = sum(r.get('posts_published', 0) for r in reports)
            
            logger.info(f"[AUTO_MARKETING_SERVICE] ✅ Cycle completed: {successful}/{len(user_ids)} users, {total_posts} posts total")
            
        except Exception as e:
            logger.error(f"[AUTO_MARKETING_SERVICE] Cycle error: {e}")
    
    async def schedule_loop(self):
        """
        Бесконечный цикл с периодическим запуском маркетинга
        """
        self.running = True
        logger.info(f"[AUTO_MARKETING_SERVICE] 🔄 Started scheduling loop (every {self.check_interval_hours}h)")
        
        while self.running:
            try:
                # Запускаем маркетинговый цикл
                await self.run_marketing_cycle()
                
                # Ждём до следующего запуска
                logger.info(f"[AUTO_MARKETING_SERVICE] 😴 Sleeping for {self.check_interval_hours}h until next cycle")
                await asyncio.sleep(self.check_interval_hours * 3600)
                
            except Exception as e:
                logger.error(f"[AUTO_MARKETING_SERVICE] Loop error: {e}")
                # При ошибке ждём 1 час и пробуем снова
                await asyncio.sleep(3600)
    
    def stop(self):
        """Останавливает сервис"""
        logger.info("[AUTO_MARKETING_SERVICE] Stopping service")
        self.running = False
    
    async def start(self):
        """Запускает сервис в фоне"""
        logger.info("[AUTO_MARKETING_SERVICE] Starting service")
        await self.schedule_loop()


# Глобальный экземпляр сервиса (инициализируется в main.py)
_marketing_service = None


def init_marketing_service(bot=None, check_interval_hours=6):
    """
    Инициализирует глобальный экземпляр сервиса
    
    Args:
        bot: Telegram bot instance
        check_interval_hours: Интервал проверки
    
    Returns:
        AutoMarketingService instance
    """
    global _marketing_service
    _marketing_service = AutoMarketingService(bot, check_interval_hours)
    return _marketing_service


def get_marketing_service():
    """Возвращает глобальный экземпляр сервиса"""
    return _marketing_service


async def start_marketing_service(bot=None, check_interval_hours=6):
    """
    Запускает сервис автомаркетинга
    
    Usage in main.py:
        import auto_marketing_service
        asyncio.create_task(auto_marketing_service.start_marketing_service(bot))
    """
    service = init_marketing_service(bot, check_interval_hours)
    await service.start()


# Тестирование
async def test_service():
    """Тест сервиса (запускает один цикл)"""
    service = AutoMarketingService(check_interval_hours=6)
    await service.run_marketing_cycle()


if __name__ == "__main__":
    print("🧪 Testing Auto Marketing Service...")
    asyncio.run(test_service())
