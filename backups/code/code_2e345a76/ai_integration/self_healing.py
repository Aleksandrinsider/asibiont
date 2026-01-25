"""
Система самоисправления AI агента
Автоматическое обнаружение проблем и откат к рабочим версиям
"""
import json
import time
import asyncio
import threading
import subprocess
import os
import shutil
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from enum import Enum
import logging

from ai_integration.logging import get_async_agent_logger
from config import DATABASE_URL
from models import AgentLog, AgentMetrics

logger = logging.getLogger(__name__)


class ErrorSeverity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RecoveryAction(Enum):
    RESTART_SERVICE = "restart_service"
    ROLLBACK_CODE = "rollback_code"
    ROLLBACK_CONFIG = "rollback_config"
    SCALE_DOWN = "scale_down"
    ALERT_ADMIN = "alert_admin"


@dataclass
class ErrorPattern:
    """Паттерн ошибки для анализа"""
    error_type: str
    severity: ErrorSeverity
    frequency_threshold: int  # Количество ошибок за период
    time_window_minutes: int
    recovery_actions: List[RecoveryAction]
    description: str


@dataclass
class SystemSnapshot:
    """Снимок состояния системы для отката"""
    timestamp: datetime
    code_version: str
    config_hash: str
    metrics_snapshot: Dict[str, Any]
    is_working: bool = True


class SelfHealingAgent:
    """
    Система самоисправления агента
    Автоматически обнаруживает проблемы и выполняет восстановление
    """

    def __init__(self):
        self.logger = get_async_agent_logger()
        self.is_monitoring = False
        self.monitoring_thread = None

        # Настройки мониторинга
        self.monitoring_interval = 60  # Проверка каждые 60 секунд
        self.error_patterns = self._init_error_patterns()

        # История версий для отката
        self.version_history: List[SystemSnapshot] = []
        self.max_versions = 10  # Хранить последние 10 версий

        # Состояние системы
        self.last_health_check = datetime.now()
        self.consecutive_failures = 0
        self.max_consecutive_failures = 5

        # Пути для бэкапов
        self.backup_dir = "backups"
        self.code_backup_dir = os.path.join(self.backup_dir, "code")
        self.config_backup_dir = os.path.join(self.backup_dir, "config")

        # Создаем директории для бэкапов
        os.makedirs(self.code_backup_dir, exist_ok=True)
        os.makedirs(self.config_backup_dir, exist_ok=True)

        logger.info("[SELF-HEALING] Agent initialized")

    def _init_error_patterns(self) -> List[ErrorPattern]:
        """Инициализация паттернов ошибок для мониторинга"""
        return [
            ErrorPattern(
                error_type="response_timeout",
                severity=ErrorSeverity.HIGH,
                frequency_threshold=10,
                time_window_minutes=5,
                recovery_actions=[RecoveryAction.RESTART_SERVICE],
                description="Много таймаутов ответов"
            ),
            ErrorPattern(
                error_type="tool_execution_error",
                severity=ErrorSeverity.MEDIUM,
                frequency_threshold=5,
                time_window_minutes=10,
                recovery_actions=[RecoveryAction.ROLLBACK_CODE],
                description="Ошибки выполнения инструментов"
            ),
            ErrorPattern(
                error_type="database_connection_error",
                severity=ErrorSeverity.CRITICAL,
                frequency_threshold=3,
                time_window_minutes=2,
                recovery_actions=[RecoveryAction.RESTART_SERVICE, RecoveryAction.ALERT_ADMIN],
                description="Проблемы с подключением к БД"
            ),
            ErrorPattern(
                error_type="low_response_quality",
                severity=ErrorSeverity.MEDIUM,
                frequency_threshold=20,
                time_window_minutes=15,
                recovery_actions=[RecoveryAction.ROLLBACK_CONFIG],
                description="Низкое качество ответов"
            ),
            ErrorPattern(
                error_type="memory_leak",
                severity=ErrorSeverity.HIGH,
                frequency_threshold=1,
                time_window_minutes=30,
                recovery_actions=[RecoveryAction.RESTART_SERVICE],
                description="Подозрение на утечку памяти"
            )
        ]

    def start_monitoring(self):
        """Запуск мониторинга системы"""
        if self.is_monitoring:
            return

        self.is_monitoring = True
        self.monitoring_thread = threading.Thread(
            target=self._monitoring_loop,
            daemon=True,
            name="SelfHealingMonitor"
        )
        self.monitoring_thread.start()
        logger.info("[SELF-HEALING] Monitoring started")

    def stop_monitoring(self):
        """Остановка мониторинга"""
        self.is_monitoring = False
        if self.monitoring_thread:
            self.monitoring_thread.join(timeout=5)
        logger.info("[SELF-HEALING] Monitoring stopped")

    def _monitoring_loop(self):
        """Основной цикл мониторинга"""
        while self.is_monitoring:
            try:
                self._perform_health_check()
                self._analyze_error_patterns()
                self._cleanup_old_backups()
            except Exception as e:
                logger.error(f"[SELF-HEALING] Error in monitoring loop: {e}")

            time.sleep(self.monitoring_interval)

    def _perform_health_check(self):
        """Проверка здоровья системы"""
        try:
            # Проверяем основные метрики
            health_metrics = self._get_health_metrics()

            # Оцениваем состояние
            is_healthy = self._evaluate_system_health(health_metrics)

            if is_healthy:
                self.consecutive_failures = 0
                self.last_health_check = datetime.now()
            else:
                self.consecutive_failures += 1
                logger.warning(f"[SELF-HEALING] Health check failed. Consecutive failures: {self.consecutive_failures}")

                if self.consecutive_failures >= self.max_consecutive_failures:
                    logger.error("[SELF-HEALING] Too many consecutive failures, initiating emergency recovery")
                    self._emergency_recovery()

        except Exception as e:
            logger.error(f"[SELF-HEALING] Health check error: {e}")

    def _get_health_metrics(self) -> Dict[str, Any]:
        """Получение метрик здоровья системы"""
        try:
            # Получаем общие метрики за последний час
            metrics = self.logger.get_performance_metrics(user_id=0, days=1, use_cache=False)

            # Добавляем системные метрики
            system_metrics = {
                'cpu_usage': self._get_cpu_usage(),
                'memory_usage': self._get_memory_usage(),
                'db_connection_status': self._check_db_connection(),
                'queue_size': self._get_log_queue_size(),
                'error_rate': self._calculate_error_rate()
            }

            return {**metrics, **system_metrics}

        except Exception as e:
            logger.error(f"[SELF-HEALING] Error getting health metrics: {e}")
            return {}

    def _evaluate_system_health(self, metrics: Dict[str, Any]) -> bool:
        """Оценка здоровья системы на основе метрик"""
        try:
            # Критерии здоровья
            health_checks = []

            # Проверка качества ответов
            avg_quality = metrics.get('avg_response_quality', 0)
            health_checks.append(avg_quality >= 4)  # Минимум 4/10

            # Проверка времени ответа
            avg_response_time = metrics.get('avg_response_time_ms', 0)
            health_checks.append(avg_response_time <= 5000)  # Максимум 5 секунд

            # Проверка использования CPU
            cpu_usage = metrics.get('cpu_usage', 0)
            health_checks.append(cpu_usage <= 80)  # Максимум 80%

            # Проверка памяти
            memory_usage = metrics.get('memory_usage', 0)
            health_checks.append(memory_usage <= 85)  # Максимум 85%

            # Проверка подключения к БД
            db_status = metrics.get('db_connection_status', False)
            health_checks.append(db_status)

            # Проверка размера очереди логов
            queue_size = metrics.get('queue_size', 0)
            health_checks.append(queue_size <= 800)  # Максимум 80% от лимита

            # Общий результат
            healthy_checks = sum(health_checks)
            total_checks = len(health_checks)

            health_percentage = (healthy_checks / total_checks) * 100
            is_healthy = health_percentage >= 70  # Минимум 70% успешных проверок

            logger.debug(f"[SELF-HEALING] Health check: {healthy_checks}/{total_checks} ({health_percentage:.1f}%)")

            return is_healthy

        except Exception as e:
            logger.error(f"[SELF-HEALING] Error evaluating health: {e}")
            return False

    def _analyze_error_patterns(self):
        """Анализ паттернов ошибок"""
        try:
            for pattern in self.error_patterns:
                if self._check_error_pattern(pattern):
                    logger.warning(f"[SELF-HEALING] Error pattern detected: {pattern.description}")
                    self._execute_recovery_actions(pattern.recovery_actions)

        except Exception as e:
            logger.error(f"[SELF-HEALING] Error analyzing patterns: {e}")

    def _check_error_pattern(self, pattern: ErrorPattern) -> bool:
        """Проверка наличия паттерна ошибки"""
        try:
            # Получаем логи за период времени паттерна
            since_time = datetime.now() - timedelta(minutes=pattern.time_window_minutes)

            # Здесь нужно реализовать поиск ошибок по типу
            # Пока используем простую логику на основе метрик
            recent_metrics = self.logger.get_performance_metrics(user_id=0, days=1, use_cache=False)

            # Пример проверки для разных типов ошибок
            if pattern.error_type == "response_timeout":
                avg_time = recent_metrics.get('avg_response_time_ms', 0)
                return avg_time > 10000  # Таймаут > 10 секунд

            elif pattern.error_type == "low_response_quality":
                avg_quality = recent_metrics.get('avg_response_quality', 0)
                return avg_quality < 3  # Качество < 3/10

            elif pattern.error_type == "tool_execution_error":
                # Проверяем логи на ошибки инструментов
                return False  # Пока не реализовано

            elif pattern.error_type == "database_connection_error":
                db_status = self._check_db_connection()
                return not db_status

            return False

        except Exception as e:
            logger.error(f"[SELF-HEALING] Error checking pattern {pattern.error_type}: {e}")
            return False

    def _execute_recovery_actions(self, actions: List[RecoveryAction]):
        """Выполнение действий восстановления"""
        for action in actions:
            try:
                if action == RecoveryAction.RESTART_SERVICE:
                    self._restart_service()
                elif action == RecoveryAction.ROLLBACK_CODE:
                    self._rollback_code()
                elif action == RecoveryAction.ROLLBACK_CONFIG:
                    self._rollback_config()
                elif action == RecoveryAction.SCALE_DOWN:
                    self._scale_down()
                elif action == RecoveryAction.ALERT_ADMIN:
                    self._alert_admin()

                logger.info(f"[SELF-HEALING] Executed recovery action: {action.value}")

            except Exception as e:
                logger.error(f"[SELF-HEALING] Error executing action {action.value}: {e}")

    def create_system_snapshot(self, is_working: bool = True):
        """Создание снимка состояния системы"""
        try:
            snapshot = SystemSnapshot(
                timestamp=datetime.now(),
                code_version=self._get_code_version(),
                config_hash=self._get_config_hash(),
                metrics_snapshot=self._get_health_metrics(),
                is_working=is_working
            )

            self.version_history.append(snapshot)

            # Ограничиваем количество хранимых версий
            if len(self.version_history) > self.max_versions:
                self.version_history.pop(0)

            # Создаем бэкап кода и конфигурации
            if is_working:
                self._backup_code(snapshot)
                self._backup_config(snapshot)

            logger.info(f"[SELF-HEALING] System snapshot created: {snapshot.code_version}")

        except Exception as e:
            logger.error(f"[SELF-HEALING] Error creating snapshot: {e}")

    def _rollback_code(self):
        """Откат кода к предыдущей рабочей версии"""
        try:
            # Находим последнюю рабочую версию
            working_snapshots = [s for s in self.version_history if s.is_working]
            if not working_snapshots:
                logger.error("[SELF-HEALING] No working snapshots found for code rollback")
                return

            latest_working = working_snapshots[-1]
            self._restore_code_backup(latest_working)

            logger.info(f"[SELF-HEALING] Code rolled back to version: {latest_working.code_version}")

        except Exception as e:
            logger.error(f"[SELF-HEALING] Error rolling back code: {e}")

    def _rollback_config(self):
        """Откат конфигурации к предыдущей рабочей версии"""
        try:
            working_snapshots = [s for s in self.version_history if s.is_working]
            if not working_snapshots:
                logger.error("[SELF-HEALING] No working snapshots found for config rollback")
                return

            latest_working = working_snapshots[-1]
            self._restore_config_backup(latest_working)

            logger.info(f"[SELF-HEALING] Config rolled back to version: {latest_working.config_hash}")

        except Exception as e:
            logger.error(f"[SELF-HEALING] Error rolling back config: {e}")

    def _restart_service(self):
        """Перезапуск сервиса"""
        try:
            logger.info("[SELF-HEALING] Restarting service...")

            # Здесь можно добавить логику перезапуска через systemd/docker/k8s
            # Пока просто логируем
            logger.info("[SELF-HEALING] Service restart requested (manual intervention required)")

        except Exception as e:
            logger.error(f"[SELF-HEALING] Error restarting service: {e}")

    def _emergency_recovery(self):
        """Аварийное восстановление"""
        try:
            logger.error("[SELF-HEALING] EMERGENCY RECOVERY INITIATED")

            # Откатываем кода и конфигурации
            self._rollback_code()
            self._rollback_config()

            # Перезапускаем сервис
            self._restart_service()

            # Оповещаем администратора
            self._alert_admin()

        except Exception as e:
            logger.critical(f"[SELF-HEALING] Emergency recovery failed: {e}")

    def _alert_admin(self):
        """Оповещение администратора"""
        try:
            # Здесь можно добавить отправку уведомлений в Telegram/email/Slack
            logger.error("[SELF-HEALING] ADMIN ALERT: System requires attention")

            # Можно интегрировать с Telegram ботом для отправки сообщений
            # self._send_telegram_alert("System health degraded, recovery actions taken")

        except Exception as e:
            logger.error(f"[SELF-HEALING] Error alerting admin: {e}")

    # Вспомогательные методы
    def _get_cpu_usage(self) -> float:
        """Получение использования CPU"""
        try:
            import psutil
            return psutil.cpu_percent(interval=1)
        except:
            return 0.0

    def _get_memory_usage(self) -> float:
        """Получение использования памяти"""
        try:
            import psutil
            return psutil.virtual_memory().percent
        except:
            return 0.0

    def _check_db_connection(self) -> bool:
        """Проверка подключения к БД"""
        try:
            from sqlalchemy import create_engine
            engine = create_engine(DATABASE_URL)
            with engine.connect() as conn:
                conn.execute("SELECT 1")
            return True
        except:
            return False

    def _get_log_queue_size(self) -> int:
        """Получение размера очереди логов"""
        try:
            return self.logger.log_queue.qsize()
        except:
            return 0

    def _calculate_error_rate(self) -> float:
        """Расчет процента ошибок"""
        try:
            # Простая логика - можно улучшить
            return 0.0
        except:
            return 0.0

    def _get_code_version(self) -> str:
        """Получение версии кода (git hash или timestamp)"""
        try:
            result = subprocess.run(['git', 'rev-parse', 'HEAD'],
                                  capture_output=True, text=True, cwd='.')
            if result.returncode == 0:
                return result.stdout.strip()[:8]
            else:
                return datetime.now().strftime("%Y%m%d_%H%M%S")
        except:
            return datetime.now().strftime("%Y%m%d_%H%M%S")

    def _get_config_hash(self) -> str:
        """Получение хэша конфигурации"""
        try:
            import hashlib
            config_files = ['config.py', '.env']
            combined = ""
            for file in config_files:
                if os.path.exists(file):
                    with open(file, 'r', encoding='utf-8') as f:
                        combined += f.read()
            return hashlib.md5(combined.encode()).hexdigest()[:8]
        except:
            return "unknown"

    def _backup_code(self, snapshot: SystemSnapshot):
        """Создание бэкапа кода"""
        try:
            backup_path = os.path.join(self.code_backup_dir, f"code_{snapshot.code_version}")
            if os.path.exists(backup_path):
                shutil.rmtree(backup_path)
            shutil.copytree('.', backup_path, ignore=shutil.ignore_patterns(
                '__pycache__', '*.pyc', '.git', 'backups', 'local.db', '*.log'
            ))
        except Exception as e:
            logger.error(f"[SELF-HEALING] Error backing up code: {e}")

    def _backup_config(self, snapshot: SystemSnapshot):
        """Создание бэкапа конфигурации"""
        try:
            config_files = ['config.py', '.env', '.env.example']
            backup_path = os.path.join(self.config_backup_dir, f"config_{snapshot.config_hash}")

            os.makedirs(backup_path, exist_ok=True)
            for file in config_files:
                if os.path.exists(file):
                    shutil.copy2(file, backup_path)
        except Exception as e:
            logger.error(f"[SELF-HEALING] Error backing up config: {e}")

    def _restore_code_backup(self, snapshot: SystemSnapshot):
        """Восстановление бэкапа кода"""
        try:
            backup_path = os.path.join(self.code_backup_dir, f"code_{snapshot.code_version}")
            if not os.path.exists(backup_path):
                logger.error(f"[SELF-HEALING] Code backup not found: {backup_path}")
                return

            # Копируем файлы обратно (только .py файлы для безопасности)
            for root, dirs, files in os.walk(backup_path):
                for file in files:
                    if file.endswith('.py'):
                        src = os.path.join(root, file)
                        dst = os.path.join('.', file)
                        shutil.copy2(src, dst)

            logger.info(f"[SELF-HEALING] Code restored from backup: {snapshot.code_version}")

        except Exception as e:
            logger.error(f"[SELF-HEALING] Error restoring code backup: {e}")

    def _restore_config_backup(self, snapshot: SystemSnapshot):
        """Восстановление бэкапа конфигурации"""
        try:
            backup_path = os.path.join(self.config_backup_dir, f"config_{snapshot.config_hash}")
            if not os.path.exists(backup_path):
                logger.error(f"[SELF-HEALING] Config backup not found: {backup_path}")
                return

            # Восстанавливаем конфигурационные файлы
            config_files = ['config.py', '.env']
            for file in config_files:
                backup_file = os.path.join(backup_path, file)
                if os.path.exists(backup_file):
                    shutil.copy2(backup_file, file)

            logger.info(f"[SELF-HEALING] Config restored from backup: {snapshot.config_hash}")

        except Exception as e:
            logger.error(f"[SELF-HEALING] Error restoring config backup: {e}")

    def _scale_down(self):
        """Масштабирование вниз (уменьшение нагрузки)"""
        try:
            # Можно реализовать уменьшение количества воркеров,
            # отключение неважных функций и т.д.
            logger.info("[SELF-HEALING] Scaling down system load")
        except Exception as e:
            logger.error(f"[SELF-HEALING] Error scaling down: {e}")

    def _cleanup_old_backups(self):
        """Очистка старых бэкапов"""
        try:
            # Оставляем только последние 5 бэкапов
            max_backups = 5

            for backup_dir in [self.code_backup_dir, self.config_backup_dir]:
                if os.path.exists(backup_dir):
                    backups = sorted(os.listdir(backup_dir))
                    if len(backups) > max_backups:
                        to_remove = backups[:-max_backups]
                        for old_backup in to_remove:
                            shutil.rmtree(os.path.join(backup_dir, old_backup))

        except Exception as e:
            logger.error(f"[SELF-HEALING] Error cleaning up backups: {e}")


# Глобальный экземпляр системы самоисправления
_self_healing_agent = None

def get_self_healing_agent() -> SelfHealingAgent:
    """Получение экземпляра системы самоисправления"""
    global _self_healing_agent
    if _self_healing_agent is None:
        _self_healing_agent = SelfHealingAgent()
        _self_healing_agent.start_monitoring()
    return _self_healing_agent