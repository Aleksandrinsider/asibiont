"""
Automated Marketing Service для Premium пользователей

Запускает автономный маркетинговый агент для Premium пользователей по расписанию.
Запускается как фоновый процесс вместе с ботом.
"""

import asyncio
import logging
from datetime import datetime, timedelta
import pytz
from models import Session, User, UserProfile

logger = logging.getLogger(__name__)


class AutoMarketingService:
    """
    Сервис для автоматического запуска маркетинга для Premium пользователей
    
    Работа:
    - Проверяет Premium пользователей каждые 30 минут
    - Запускает маркетинговый цикл для тех, у кого настроен telegram_channel
    - Постит один раз в день в указанное пользователем время
    - Логирует результаты
    """
    
    def __init__(self, bot=None, check_interval_minutes=30):
        """
        Args:
            bot: Telegram bot instance для отправки уведомлений (опционально)
            check_interval_minutes: Интервал проверки в минутах (по умолчанию 30)
        """
        self.bot = bot
        self.check_interval_minutes = check_interval_minutes
        self.running = False
        logger.info(f"[AUTO_MARKETING_SERVICE] Initialized with interval {check_interval_minutes}min")
    
    async def get_premium_users_for_marketing(self):
        """
        Получает список пользователей готовых для автомаркетинга
        
        Критерии:
        - Настроен telegram_channel
        - Достаточно токенов для proactive_channel (30 токенов)
        
        Returns:
            List[dict]: Список словарей с информацией о пользователях
        """
        session = Session()
        try:
            from models import Subscription, UserProfile
            from token_service import has_enough_tokens
            
            # Находим пользователей с telegram_channel и положительным балансом
            users_with_channel = session.query(User).outerjoin(UserProfile).filter(
                User.telegram_channel.isnot(None),
                User.telegram_channel != '',
                User.token_balance > 0
            ).all()
            
            users_data = []
            for user in users_with_channel:
                # Дополнительная проверка баланса для конкретного действия
                if not has_enough_tokens(user.telegram_id, 'proactive_channel', session=session):
                    continue
                    
                # Получаем предпочтительное время постинга
                post_time = '12:00'  # По умолчанию 12:00
                if user.profile and user.profile.auto_post_time:
                    post_time = user.profile.auto_post_time
                
                users_data.append({
                    'telegram_id': user.telegram_id,
                    'user_id': user.id,
                    'timezone': user.timezone or 'Europe/Moscow',
                    'post_time': post_time,
                    'channel': user.telegram_channel
                })
            
            logger.info(f"[AUTO_MARKETING_SERVICE] Found {len(users_data)} users ready for auto-marketing")
            return users_data
            
        except Exception as e:
            logger.error(f"[AUTO_MARKETING_SERVICE] Error getting Premium users: {e}")
            return []
        finally:
            session.close()
    
    async def run_marketing_for_user(self, user_data: dict):
        """
        Запускает маркетинговый цикл для конкретного пользователя
        
        Args:
            user_data: dict с данными пользователя
        """
        user_id = user_data['telegram_id']
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
            # Генерируем маркетинговый контент
            from ai_integration.marketing_agent import generate_marketing_content
            from ai_integration.handlers import publish_to_telegram
            
            # Получаем информацию о контент-стратегии пользователя
            content_strategy = profile.content_strategy if profile and profile.content_strategy else None
            
            # Базовые параметры для генерации контента
            product_name = "AI Агент для задач"
            target_audience = "предприниматели 25-40"
            platform = "telegram"
            
            if content_strategy:
                # Используем стратегию пользователя если есть
                import json
                try:
                    strategy = json.loads(content_strategy)
                    product_name = strategy.get('product', product_name)
                    target_audience = strategy.get('audience', target_audience)
                    platform = strategy.get('platform', platform)
                except (json.JSONDecodeError, TypeError, AttributeError) as e:
                    logger.debug(f"Failed to parse content_strategy: {e}")
            
            # Генерируем контент
            marketing_content = await generate_marketing_content(
                product_name=product_name,
                target_audience=target_audience,
                platform=platform,
                goal="привлечение",
                user_id=user_id,
                session=None
            )
            
            posts_published = 0
            
            # Публикуем если есть канал
            if user.telegram_channel and marketing_content:
                try:
                    result = await publish_to_telegram(
                        content=marketing_content,
                        channel=user.telegram_channel,
                        user_id=user_id,
                        session=None
                    )
                    if "успешно" in result.lower():
                        posts_published = 1
                        # Log agent activity for TG channel post
                        try:
                            from models import AgentActivityLog
                            session_log = Session()
                            try:
                                db_user = session_log.query(User).filter_by(telegram_id=user_id).first()
                                if db_user:
                                    short_title = marketing_content[:80] + ('...' if len(marketing_content) > 80 else '')
                                    log_entry = AgentActivityLog(
                                        user_id=db_user.id,
                                        activity_type='post_telegram',
                                        title=short_title,
                                        content=marketing_content,
                                        target=user.telegram_channel,
                                        status='published',
                                    )
                                    session_log.add(log_entry)
                                    session_log.commit()
                            finally:
                                session_log.close()
                        except Exception as log_err:
                            logger.warning(f"[AUTO_MARKETING_SERVICE] Failed to log TG activity: {log_err}")
                except Exception as e:
                    logger.error(f"[AUTO_MARKETING_SERVICE] Publish error: {e}")
            
            report = {
                'status': 'success',
                'posts_published': posts_published,
                'content': marketing_content
            }
            
            # Логируем результат
            if report['status'] == 'success':
                logger.info(f"[AUTO_MARKETING_SERVICE] ✅ Success for user {user_id}: {report['posts_published']} posts published")
                
                # Отправляем уведомление пользователю (если есть бот)
                if self.bot and report['posts_published'] > 0:
                    try:
                        message = f"🤖 Автономный маркетинг завершён!\n\n✅ Опубликовано постов: {report['posts_published']}\n⏰ Следующий пост завтра в {user_data['post_time']}"
                        await self.bot.send_message(user_id, message)
                    except Exception as e:
                        logger.warning(f"[AUTO_MARKETING_SERVICE] Could not send notification to {user_id}: {e}")
            else:
                logger.warning(f"[AUTO_MARKETING_SERVICE] ⚠️ Failed for user {user_id}: {report.get('errors', [])}")
            
            return report
            
        except Exception as e:
            logger.error(f"[AUTO_MARKETING_SERVICE] Error running marketing for user {user_id}: {e}")
            return {'status': 'error', 'user_id': user_id, 'errors': [str(e)]}
    
    async def _posted_today(self, user_data):
        """Проверяет, был ли уже пост от этого пользователя сегодня"""
        session = Session()
        try:
            from models import Post
            user_tz = pytz.timezone(user_data.get('timezone', 'Europe/Moscow'))
            now_user = datetime.now(pytz.UTC).astimezone(user_tz)
            today_start = now_user.replace(hour=0, minute=0, second=0, microsecond=0)
            today_start_utc = today_start.astimezone(pytz.UTC).replace(tzinfo=None)
            
            post_count = session.query(Post).filter(
                Post.user_id == user_data['user_id'],
                Post.created_at >= today_start_utc
            ).count()
            return post_count > 0
        except Exception as e:
            logger.error(f"[AUTO_MARKETING_SERVICE] Error checking posted_today: {e}")
            return False
        finally:
            session.close()

    async def should_post_now(self, user_data):
        """
        Проверяет, пора ли постить для данного пользователя
        
        Args:
            user_data: dict с данными пользователя
            
        Returns:
            bool: True если пора постить
        """
        try:
            # Получаем текущее время в timezone пользователя
            user_tz = pytz.timezone(user_data['timezone'])
            now_utc = datetime.now(pytz.UTC)
            now_user = now_utc.astimezone(user_tz)
            
            # Парсим желаемое время постинга
            post_hour, post_minute = map(int, user_data['post_time'].split(':'))
            
            # Проверяем, совпадает ли текущее время с желаемым (с погрешностью 30 минут)
            current_hour = now_user.hour
            current_minute = now_user.minute
            
            # Проверяем, находится ли текущее время в интервале [post_time - 15min, post_time + 15min]
            post_time_minutes = post_hour * 60 + post_minute
            current_time_minutes = current_hour * 60 + current_minute
            
            # Учитываем переход через полночь
            if abs(current_time_minutes - post_time_minutes) <= 15:
                return True
            
            # Также проверяем переход через полночь (если post_time близко к 00:00)
            if post_time_minutes <= 15:  # Если желаемое время 00:00-00:15
                if current_time_minutes >= 1435 or current_time_minutes <= 15:  # 23:45-00:15
                    return True
            
            return False
            
        except Exception as e:
            logger.error(f"[AUTO_MARKETING_SERVICE] Error checking post time for user {user_data['telegram_id']}: {e}")
            return False
    
    async def run_marketing_cycle(self):
        """
        Основной цикл: находит Premium пользователей и запускает маркетинг в нужное время
        """
        try:
            logger.info("[AUTO_MARKETING_SERVICE] 🚀 Starting marketing cycle")
            
            # Получаем Premium пользователей
            users_data = await self.get_premium_users_for_marketing()
            
            if not users_data:
                logger.info("[AUTO_MARKETING_SERVICE] No Premium users ready for marketing")
                return
            
            # Проверяем каждого пользователя и постим только в нужное время
            reports = []
            for user_data in users_data:
                try:
                    # Проверяем, пора ли постить для этого пользователя
                    if not await self.should_post_now(user_data):
                        logger.info(f"[AUTO_MARKETING_SERVICE] Skipping user {user_data['telegram_id']} - not post time yet")
                        continue
                    
                    # Проверяем, не постили ли уже сегодня
                    if await self._posted_today(user_data):
                        logger.info(f"[AUTO_MARKETING_SERVICE] Skipping user {user_data['telegram_id']} - already posted today")
                        continue
                    
                    report = await self.run_marketing_for_user(user_data)
                    reports.append(report)
                    
                    # Пауза между пользователями (чтобы не перегрузить API)
                    await asyncio.sleep(120)  # 2 минуты между пользователями
                    
                except Exception as e:
                    logger.error(f"[AUTO_MARKETING_SERVICE] Failed for user {user_data['telegram_id']}: {e}")
                    continue
            
            # Общая статистика
            successful = sum(1 for r in reports if r['status'] == 'success')
            total_posts = sum(r.get('posts_published', 0) for r in reports)
            
            logger.info(f"[AUTO_MARKETING_SERVICE] ✅ Cycle completed: {successful}/{len(users_data)} users, {total_posts} posts total")
            
        except Exception as e:
            logger.error(f"[AUTO_MARKETING_SERVICE] Cycle error: {e}")
    
    async def schedule_loop(self):
        """
        Бесконечный цикл с периодическим запуском маркетинга
        """
        self.running = True
        logger.info(f"[AUTO_MARKETING_SERVICE] 🔄 Started scheduling loop (every {self.check_interval_minutes}min)")
        
        while self.running:
            try:
                # Запускаем маркетинговый цикл
                await self.run_marketing_cycle()
                
                # Ждём до следующего запуска
                logger.info(f"[AUTO_MARKETING_SERVICE] 😴 Sleeping for {self.check_interval_minutes}min until next cycle")
                await asyncio.sleep(self.check_interval_minutes * 60)
                
            except Exception as e:
                logger.error(f"[AUTO_MARKETING_SERVICE] Loop error: {e}")
                # При ошибке ждём 5 минут и пробуем снова
                await asyncio.sleep(300)
    
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


def init_marketing_service(bot=None, check_interval_hours=6):  # noqa: check_interval_hours kept for backward compat
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


async def start_marketing_service(bot=None, check_interval_minutes=30):
    """
    Запускает сервис автомаркетинга
    
    Usage in main.py:
        import auto_marketing_service
        asyncio.create_task(auto_marketing_service.start_marketing_service(bot))
    """
    service = init_marketing_service(bot, check_interval_minutes)
    await service.start()


# Тестирование
async def test_service():
    """Тест сервиса (запускает один цикл)"""
    service = AutoMarketingService(check_interval_hours=6)
    await service.run_marketing_cycle()


if __name__ == "__main__":
    print("🧪 Testing Auto Marketing Service...")
    asyncio.run(test_service())
