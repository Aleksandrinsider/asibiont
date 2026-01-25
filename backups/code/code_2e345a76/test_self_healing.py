"""
Тесты для системы самоисправления агента
"""
import pytest
import asyncio
import time
from datetime import datetime, timedelta
from ai_integration.self_healing import SelfHealingAgent, ErrorSeverity, RecoveryAction, get_self_healing_agent
from ai_integration.logging import get_async_agent_logger


class TestSelfHealingAgent:
    """Тесты системы самоисправления"""

    def setup_method(self):
        """Настройка перед каждым тестом"""
        self.agent = SelfHealingAgent()
        self.logger = get_async_agent_logger()

    def teardown_method(self):
        """Очистка после каждого теста"""
        self.agent.stop_monitoring()

    def test_initialization(self):
        """Тест инициализации агента"""
        assert self.agent.is_monitoring == False
        assert len(self.agent.version_history) == 0
        assert len(self.agent.error_patterns) == 5  # Должно быть 5 паттернов ошибок

    def test_error_patterns(self):
        """Тест паттернов ошибок"""
        patterns = self.agent.error_patterns

        # Проверяем наличие основных паттернов
        pattern_types = [p.error_type for p in patterns]
        assert "response_timeout" in pattern_types
        assert "database_connection_error" in pattern_types
        assert "low_response_quality" in pattern_types

        # Проверяем severity levels
        severity_levels = [p.severity for p in patterns]
        assert ErrorSeverity.CRITICAL in severity_levels
        assert ErrorSeverity.HIGH in severity_levels

    def test_system_snapshot_creation(self):
        """Тест создания снимков системы"""
        initial_count = len(self.agent.version_history)

        # Создаем снимок
        self.agent.create_system_snapshot(is_working=True)

        assert len(self.agent.version_history) == initial_count + 1

        snapshot = self.agent.version_history[-1]
        assert snapshot.is_working == True
        assert isinstance(snapshot.timestamp, datetime)
        assert snapshot.code_version is not None

    def test_health_check_calculation(self):
        """Тест расчета здоровья системы"""
        # Тест с хорошими метриками
        good_metrics = {
            'avg_response_quality': 8.0,
            'avg_response_time_ms': 1000,
            'cpu_usage': 50.0,
            'memory_usage': 60.0,
            'db_connection_status': True,
            'queue_size': 50
        }

        is_healthy = self.agent._evaluate_system_health(good_metrics)
        assert is_healthy == True

        # Тест с плохими метриками
        bad_metrics = {
            'avg_response_quality': 2.0,
            'avg_response_time_ms': 15000,
            'cpu_usage': 95.0,
            'memory_usage': 90.0,
            'db_connection_status': False,
            'queue_size': 900
        }

        is_healthy = self.agent._evaluate_system_health(bad_metrics)
        assert is_healthy == False

    def test_monitoring_start_stop(self):
        """Тест запуска и остановки мониторинга"""
        assert self.agent.is_monitoring == False

        self.agent.start_monitoring()
        assert self.agent.is_monitoring == True
        assert self.agent.monitoring_thread.is_alive()

        self.agent.stop_monitoring()
        assert self.agent.is_monitoring == False

    def test_backup_creation(self):
        """Тест создания бэкапов"""
        import os
        import tempfile
        import shutil

        # Создаем временную директорию для теста
        with tempfile.TemporaryDirectory() as temp_dir:
            # Меняем пути бэкапов для теста
            original_code_dir = self.agent.code_backup_dir
            original_config_dir = self.agent.config_backup_dir

            self.agent.code_backup_dir = os.path.join(temp_dir, "code_backups")
            self.agent.config_backup_dir = os.path.join(temp_dir, "config_backups")

            # Создаем тестовые файлы
            test_code_file = os.path.join(temp_dir, "test.py")
            with open(test_code_file, "w") as f:
                f.write("# Test code")

            test_config_file = os.path.join(temp_dir, "test_config.py")
            with open(test_config_file, "w") as f:
                f.write("# Test config")

            # Создаем снимок
            self.agent.create_system_snapshot(is_working=True)

            # Проверяем создание бэкапов
            assert os.path.exists(self.agent.code_backup_dir)
            assert os.path.exists(self.agent.config_backup_dir)

            # Восстанавливаем оригинальные пути
            self.agent.code_backup_dir = original_code_dir
            self.agent.config_backup_dir = original_config_dir

    def test_get_performance_metrics(self):
        """Тест получения метрик производительности"""
        # Создаем тестовые логи
        asyncio.run(self.logger.log_interaction_async(
            user_id=12345,
            message_type='test',
            user_message='Test message',
            agent_response='Test response',
            response_time_ms=1000
        ))

        # Ждем обработки
        time.sleep(2)

        # Получаем метрики
        metrics = self.agent.logger.get_performance_metrics(12345, days=1, use_cache=False)

        assert 'total_interactions' in metrics
        assert 'avg_response_quality' in metrics
        assert 'avg_response_time_ms' in metrics

    def test_singleton_pattern(self):
        """Тест паттерна singleton"""
        agent1 = get_self_healing_agent()
        agent2 = get_self_healing_agent()

        assert agent1 is agent2


if __name__ == "__main__":
    # Запуск простого тестирования
    agent = get_self_healing_agent()

    print("Testing Self-Healing Agent...")

    # Тест создания снимка
    print("Creating system snapshot...")
    agent.create_system_snapshot(is_working=True)
    print(f"Version history: {len(agent.version_history)} snapshots")

    # Тест метрик здоровья
    print("Testing health metrics...")
    health_metrics = agent._get_health_metrics()
    print(f"Health metrics: {health_metrics}")

    is_healthy = agent._evaluate_system_health(health_metrics)
    print(f"System healthy: {is_healthy}")

    # Тест запуска мониторинга
    print("Starting monitoring...")
    agent.start_monitoring()
    time.sleep(5)  # Ждем несколько секунд
    agent.stop_monitoring()
    print("Monitoring stopped")

    print("Self-Healing Agent test completed!")