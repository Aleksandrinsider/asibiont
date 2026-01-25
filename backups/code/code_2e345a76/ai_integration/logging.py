"""
Логирование работы AI агента для анализа и улучшения качества обслуживания
Оптимизированная версия с асинхронным логированием и кэшированием
"""
import json
import time
import asyncio
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import AgentLog, AgentMetrics, User
import logging
from config import DATABASE_URL

logger = logging.getLogger(__name__)


class AsyncAgentLogger:
    """Оптимизированный класс для асинхронного логирования работы AI агента"""

    def __init__(self, db_url: str = None):
        self.db_url = db_url or DATABASE_URL
        self._engine = None
        self._session_factory = None
        self._init_db_connection()

        # Кэш для метрик (обновляем каждые 5 минут)
        self.metrics_cache = {}
        self.cache_ttl = 300  # 5 минут
        self.last_cache_update = {}

        # Очередь для асинхронного логирования
        self.log_queue = asyncio.Queue(maxsize=1000)  # Ограничение очереди
        self.processing_thread = None
        self.is_running = False

        # Настройки производительности
        self.max_logs_per_user_per_hour = 50  # Ограничение логов на пользователя в час
        self.user_log_counts = {}  # Кэш счетчиков

    def _init_db_connection(self):
        """Инициализация подключения к БД"""
        if not self._engine:
            self._engine = create_engine(
                self.db_url,
                pool_size=2,  # Маленький пул для фонового логирования
                max_overflow=2,
                pool_timeout=30,
                pool_recycle=3600,
                pool_pre_ping=True
            )
            self._session_factory = sessionmaker(bind=self._engine)

    def start_background_processing(self):
        """Запуск фоновой обработки логов"""
        if self.is_running:
            return

        self.is_running = True
        self.processing_thread = threading.Thread(
            target=self._background_log_processor,
            daemon=True,
            name="AgentLogger"
        )
        self.processing_thread.start()
        logger.info("[AGENT LOGGER] Background processing started")

    def stop_background_processing(self):
        """Остановка фоновой обработки"""
        self.is_running = False
        if self.processing_thread:
            self.processing_thread.join(timeout=5)
        logger.info("[AGENT LOGGER] Background processing stopped")

    def _background_log_processor(self):
        """Фоновая обработка очереди логов"""
        while self.is_running:
            try:
                # Обработка пакета логов (до 10 за раз)
                logs_batch = []
                for _ in range(min(10, self.log_queue.qsize())):
                    try:
                        log_data = self.log_queue.get_nowait()
                        logs_batch.append(log_data)
                    except asyncio.QueueEmpty:
                        break

                if logs_batch:
                    self._process_logs_batch(logs_batch)

                # Небольшая пауза чтобы не перегружать CPU
                time.sleep(0.1)

            except Exception as e:
                logger.error(f"[AGENT LOGGER] Error in background processor: {e}")
                time.sleep(1)  # Пауза при ошибке

    def _process_logs_batch(self, logs_batch: List[Dict]):
        """Обработка пакета логов"""
        db = None
        try:
            db = self._session_factory()

            for log_data in logs_batch:
                try:
                    # Создание записи лога
                    log_entry = AgentLog(**log_data)
                    db.add(log_entry)

                    # Обновление метрик (только для важных взаимодействий)
                    if log_data.get('message_type') in ['conversation', 'reminder', 'proactive']:
                        self._update_user_metrics_cached(db, log_data['user_id'])

                except Exception as e:
                    logger.error(f"[AGENT LOGGER] Error processing log entry: {e}")

            db.commit()
            logger.debug(f"[AGENT LOGGER] Processed {len(logs_batch)} log entries")

        except Exception as e:
            logger.error(f"[AGENT LOGGER] Error processing logs batch: {e}")
            if db:
                db.rollback()
        finally:
            if db:
                db.close()

    def _check_rate_limit(self, user_id: int) -> bool:
        """Проверка ограничения частоты логирования для пользователя"""
        now = time.time()
        user_key = f"user_{user_id}"

        # Очистка старых записей (старше часа)
        current_hour = int(now // 3600)
        if user_key in self.user_log_counts:
            last_hour = self.user_log_counts[user_key].get('hour', 0)
            if last_hour != current_hour:
                self.user_log_counts[user_key] = {'count': 0, 'hour': current_hour}

        # Проверка лимита
        if user_key not in self.user_log_counts:
            self.user_log_counts[user_key] = {'count': 0, 'hour': current_hour}

        if self.user_log_counts[user_key]['count'] >= self.max_logs_per_user_per_hour:
            return False  # Превышен лимит

        self.user_log_counts[user_key]['count'] += 1
        return True

    async def log_interaction_async(self,
                                   user_id: int,
                                   message_type: str,
                                   user_message: str,
                                   agent_response: str,
                                   tools_used: List[str] = None,
                                   task_actions: List[Dict] = None,
                                   response_time_ms: int = None,
                                   detected_intent: str = None,
                                   conversation_context: str = None) -> bool:
        """
        Асинхронное логирование взаимодействия (не блокирует основной поток)

        Returns:
            bool: True если лог добавлен в очередь, False если превышен лимит
        """
        # Проверка лимита частоты
        if not self._check_rate_limit(user_id):
            logger.debug(f"[AGENT LOGGER] Rate limit exceeded for user {user_id}")
            return False

        try:
            # Подготовка данных для логирования
            log_data = {
                'user_id': user_id,
                'timestamp': datetime.now(),
                'message_type': message_type,
                'user_message': user_message[:500],  # Ограничение длины
                'agent_response': agent_response[:1000],  # Ограничение длины
                'tools_used': json.dumps(tools_used or []),
                'task_actions': json.dumps(task_actions or []),
                'response_time_ms': response_time_ms,
                'detected_intent': detected_intent,
                'conversation_context': conversation_context[:200] if conversation_context else None,
                'response_quality': self._assess_response_quality(agent_response)
            }

            # Попытка добавить в очередь (не блокировать если очередь полная)
            try:
                self.log_queue.put_nowait(log_data)
                return True
            except asyncio.QueueFull:
                logger.warning("[AGENT LOGGER] Log queue is full, dropping log entry")
                return False

        except Exception as e:
            logger.error(f"[AGENT LOGGER] Error preparing log data: {e}")
            return False

    def log_interaction_sync(self,
                            user_id: int,
                            message_type: str,
                            user_message: str,
                            agent_response: str,
                            tools_used: List[str] = None,
                            task_actions: List[Dict] = None,
                            response_time_ms: int = None,
                            detected_intent: str = None,
                            conversation_context: str = None) -> AgentLog:
        """
        Синхронное логирование (для случаев когда нужно вернуть объект сразу)
        Использовать только для критичных случаев!
        """
        try:
            db = self._session_factory()
            log_entry = AgentLog(
                user_id=user_id,
                message_type=message_type,
                user_message=user_message[:500],
                agent_response=agent_response[:1000],
                tools_used=json.dumps(tools_used or []),
                task_actions=json.dumps(task_actions or []),
                response_time_ms=response_time_ms,
                detected_intent=detected_intent,
                conversation_context=conversation_context[:200] if conversation_context else None,
                response_quality=self._assess_response_quality(agent_response)
            )

            db.add(log_entry)
            db.commit()
            db.refresh(log_entry)

            # Обновление метрик
            self._update_user_metrics_cached(db, user_id)

            return log_entry

        except Exception as e:
            logger.error(f"[AGENT LOGGER] Error in sync logging: {e}")
            if 'db' in locals():
                db.rollback()
            return None
        finally:
            if 'db' in locals():
                db.close()

    def _update_user_metrics_cached(self, db: Session, user_id: int):
        """Обновление метрик пользователя с кэшированием"""
        cache_key = f"metrics_{user_id}"
        now = time.time()

        # Проверка кэша
        if (cache_key in self.last_cache_update and
            now - self.last_cache_update[cache_key] < self.cache_ttl):
            return  # Кэш еще актуален

        try:
            # Получаем текущие метрики за сегодня
            today = datetime.now().date()
            metrics = db.query(AgentMetrics).filter(
                AgentMetrics.user_id == user_id,
                AgentMetrics.date >= today
            ).first()

            if not metrics:
                metrics = AgentMetrics(user_id=user_id, date=datetime.now())
                db.add(metrics)

            # Получаем статистику за последний час для обновления
            one_hour_ago = datetime.now() - timedelta(hours=1)
            recent_logs = db.query(AgentLog).filter(
                AgentLog.user_id == user_id,
                AgentLog.timestamp >= one_hour_ago
            ).all()

            # Обновляем только счетчики (не пересчитываем все)
            for log in recent_logs:
                if log.tools_used:
                    tools = json.loads(log.tools_used)
                    metrics.tools_used_count += len(tools)

                if log.task_actions:
                    actions = json.loads(log.task_actions)
                    for action in actions:
                        if action.get('type') == 'created':
                            metrics.tasks_created += 1
                        elif action.get('type') == 'completed':
                            metrics.tasks_completed += 1

            # Обновляем средние оценки (только если есть новые логи)
            if recent_logs:
                qualities = [log.response_quality for log in recent_logs if log.response_quality]
                if qualities:
                    # Взвешенное среднее с предыдущими данными
                    total_logs = metrics.total_interactions + len(recent_logs)
                    current_avg = metrics.avg_response_quality or 0
                    new_avg = sum(qualities) / len(qualities)

                    metrics.avg_response_quality = (current_avg * metrics.total_interactions + new_avg * len(qualities)) / total_logs

            metrics.total_interactions += len(recent_logs)
            db.commit()

            # Обновляем кэш
            self.last_cache_update[cache_key] = now

        except Exception as e:
            logger.error(f"[AGENT LOGGER] Error updating cached metrics: {e}")
            db.rollback()

    def _assess_response_quality(self, response: str) -> int:
        """Оптимизированная оценка качества ответа"""
        if not response or len(response.strip()) < 5:
            return 1

        score = 5  # Базовая оценка
        response_lower = response.lower()

        # Быстрая проверка положительных факторов
        if len(response) > 100:
            score += 1
        if any(word in response_lower for word in ['конкретно', 'точнее', 'давай', 'предлагаю']):
            score += 1
        if '?' in response:
            score += 1
        if any(emoji in response for emoji in ['✅', '📅', '⏰', '🎯']):
            score += 0.5

        # Отрицательные факторы
        if len(response) < 20:
            score -= 2
        if response.count('!') > 3:
            score -= 0.5

        return max(1, min(10, int(score)))

    def get_performance_metrics(self, user_id: int, days: int = 30, use_cache: bool = True) -> Dict[str, Any]:
        """Получение метрик производительности с кэшированием"""
        cache_key = f"perf_metrics_{user_id}_{days}"
        now = time.time()

        # Проверка кэша
        if use_cache and cache_key in self.metrics_cache:
            cached_data, cache_time = self.metrics_cache[cache_key]
            if now - cache_time < self.cache_ttl:
                return cached_data

        try:
            db = self._session_factory()
            since_date = datetime.now() - timedelta(days=days)
            logs = db.query(AgentLog).filter(
                AgentLog.user_id == user_id,
                AgentLog.timestamp >= since_date
            ).all()

            if not logs:
                result = {}
            else:
                # Быстрый расчет метрик
                total_interactions = len(logs)
                avg_quality = sum(log.response_quality or 0 for log in logs) / total_interactions
                avg_response_time = sum(log.response_time_ms or 0 for log in logs) / total_interactions

                # Подсчет инструментов
                tools_count = {}
                for log in logs:
                    if log.tools_used:
                        tools = json.loads(log.tools_used)
                        for tool in tools:
                            tools_count[tool] = tools_count.get(tool, 0) + 1

                result = {
                    'total_interactions': total_interactions,
                    'avg_response_quality': round(avg_quality, 1),
                    'avg_response_time_ms': round(avg_response_time, 0),
                    'most_used_tools': sorted(tools_count.items(), key=lambda x: x[1], reverse=True)[:5],
                    'period_days': days
                }

            # Кэширование результата
            if use_cache:
                self.metrics_cache[cache_key] = (result, now)

            return result

        except Exception as e:
            logger.error(f"[AGENT LOGGER] Error getting performance metrics: {e}")
            return {}
        finally:
            if 'db' in locals():
                db.close()

    def get_recent_logs(self, user_id: int, limit: int = 10) -> List[Dict]:
        """Получение недавних логов пользователя (оптимизированная версия)"""
        try:
            db = self._session_factory()
            logs = db.query(AgentLog).filter(
                AgentLog.user_id == user_id
            ).order_by(AgentLog.timestamp.desc()).limit(limit).all()

            # Преобразование в словари для быстрого доступа
            result = []
            for log in logs:
                result.append({
                    'id': log.id,
                    'timestamp': log.timestamp.isoformat(),
                    'message_type': log.message_type,
                    'user_message': log.user_message,
                    'agent_response': log.agent_response,
                    'response_quality': log.response_quality,
                    'tools_used': json.loads(log.tools_used) if log.tools_used else [],
                    'response_time_ms': log.response_time_ms
                })

            return result

        except Exception as e:
            logger.error(f"[AGENT LOGGER] Error getting recent logs: {e}")
            return []
        finally:
            if 'db' in locals():
                db.close()

    def cleanup_old_logs(self, days_to_keep: int = 90):
        """Очистка старых логов для оптимизации БД"""
        try:
            db = self._session_factory()
            cutoff_date = datetime.now() - timedelta(days=days_to_keep)

            # Удаление старых логов
            deleted_logs = db.query(AgentLog).filter(
                AgentLog.timestamp < cutoff_date
            ).delete()

            # Удаление старых метрик (оставляем последние 30 дней)
            deleted_metrics = db.query(AgentMetrics).filter(
                AgentMetrics.date < (datetime.now() - timedelta(days=30)).date()
            ).delete()

            db.commit()

            logger.info(f"[AGENT LOGGER] Cleaned up {deleted_logs} old logs and {deleted_metrics} old metrics")

        except Exception as e:
            logger.error(f"[AGENT LOGGER] Error cleaning up old logs: {e}")
            db.rollback()
        finally:
            if 'db' in locals():
                db.close()


# Глобальный оптимизированный логгер
_async_logger = None

def get_async_agent_logger() -> AsyncAgentLogger:
    """Получение экземпляра асинхронного логгера агента"""
    global _async_logger
    if _async_logger is None:
        _async_logger = AsyncAgentLogger()
        _async_logger.start_background_processing()
    return _async_logger

# Backward compatibility
class AgentLogger(AsyncAgentLogger):
    """Обертка для обратной совместимости"""

    def __init__(self, db: Session):
        super().__init__(db_url=None)
        self.db = db  # Для обратной совместимости

    def log_interaction(self, *args, **kwargs):
        """Синхронное логирование для обратной совместимости"""
        return self.log_interaction_sync(*args, **kwargs)

def get_agent_logger(db: Session) -> AgentLogger:
    """Получение экземпляра логгера агента (обратная совместимость)"""
    return AgentLogger(db)