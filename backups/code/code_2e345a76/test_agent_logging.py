"""
Тесты для системы логирования работы AI агента
"""
import pytest
import asyncio
from datetime import datetime, timezone
from models import Session, AgentLog, AgentMetrics
from ai_integration.logging import AgentLogger, get_agent_logger


class TestAgentLogging:
    """Тесты для логирования работы агента"""

    def setup_method(self):
        """Настройка перед каждым тестом"""
        self.db = Session()
        self.logger = AgentLogger(self.db)
        # Очистка тестовых данных
        self.db.query(AgentLog).delete()
        self.db.query(AgentMetrics).delete()
        self.db.commit()

    def teardown_method(self):
        """Очистка после каждого теста"""
        self.db.query(AgentLog).delete()
        self.db.query(AgentMetrics).delete()
        self.db.commit()
        self.db.close()

    def test_log_interaction_basic(self):
        """Тест базового логирования взаимодействия"""
        user_id = 12345
        message_type = "conversation"
        user_message = "Привет, как дела?"
        agent_response = "Привет! У меня всё отлично, спасибо!"

        log_entry = self.logger.log_interaction(
            user_id=user_id,
            message_type=message_type,
            user_message=user_message,
            agent_response=agent_response
        )

        assert log_entry is not None
        assert log_entry.user_id == user_id
        assert log_entry.message_type == message_type
        assert log_entry.user_message == user_message
        assert log_entry.agent_response == agent_response
        assert log_entry.response_quality > 0  # Автоматическая оценка качества

    def test_log_interaction_with_tools(self):
        """Тест логирования с использованием инструментов"""
        user_id = 12345
        tools_used = ["add_task", "list_tasks"]
        task_actions = [
            {"type": "created", "function": "add_task"},
            {"type": "listed", "function": "list_tasks"}
        ]

        log_entry = self.logger.log_interaction(
            user_id=user_id,
            message_type="conversation",
            user_message="Создай задачу",
            agent_response="Задача создана!",
            tools_used=tools_used,
            task_actions=task_actions,
            response_time_ms=1500
        )

        assert log_entry is not None
        assert "add_task" in log_entry.tools_used
        assert "list_tasks" in log_entry.tools_used
        assert log_entry.response_time_ms == 1500

    def test_response_quality_assessment(self):
        """Тест автоматической оценки качества ответа"""
        # Хороший ответ
        good_quality = self.logger._assess_response_quality(
            "Отлично! Я создал задачу 'Позвонить маме' на завтра в 10:00. "
            "Учитывая поздний час, рекомендую не звонить слишком поздно. "
            "Как насчет созвониться утром?"
        )
        assert good_quality >= 7

        # Плохой ответ
        bad_quality = self.logger._assess_response_quality("Ок")
        assert bad_quality <= 3

        # Пустой ответ
        empty_quality = self.logger._assess_response_quality("")
        assert empty_quality == 1

    def test_get_user_logs(self):
        """Тест получения логов пользователя"""
        user_id = 12345

        # Создаем несколько логов
        for i in range(3):
            self.logger.log_interaction(
                user_id=user_id,
                message_type="conversation",
                user_message=f"Сообщение {i}",
                agent_response=f"Ответ {i}"
            )

        logs = self.logger.get_user_logs(user_id, days=1)
        assert len(logs) == 3

        # Проверяем, что все логи присутствуют (порядок может быть любым из-за одинаковых timestamp)
        messages = [log.user_message for log in logs]
        assert "Сообщение 0" in messages
        assert "Сообщение 1" in messages
        assert "Сообщение 2" in messages

    def test_performance_metrics(self):
        """Тест получения метрик производительности"""
        user_id = 12345

        # Создаем тестовые логи с разными типами взаимодействий
        interactions = [
            {
                "message_type": "conversation",
                "tools_used": ["add_task"],
                "task_actions": [{"type": "created"}],
                "response_time_ms": 1000,
                "response_quality": 8
            },
            {
                "message_type": "reminder",
                "tools_used": ["complete_task"],
                "task_actions": [{"type": "completed"}],
                "response_time_ms": 800,
                "response_quality": 9
            },
            {
                "message_type": "conversation",
                "tools_used": ["list_tasks"],
                "task_actions": [],
                "response_time_ms": 1200,
                "response_quality": 7
            }
        ]

        for interaction in interactions:
            log_entry = self.logger.log_interaction(
                user_id=user_id,
                message_type=interaction["message_type"],
                user_message="Тестовое сообщение",
                agent_response="Тестовый ответ",
                tools_used=interaction["tools_used"],
                task_actions=interaction["task_actions"],
                response_time_ms=interaction["response_time_ms"]
            )
            # Устанавливаем качество ответа вручную для теста
            log_entry.response_quality = interaction["response_quality"]
            self.db.commit()

        metrics = self.logger.get_performance_metrics(user_id, days=1)

        assert metrics["total_interactions"] == 3
        assert metrics["avg_response_quality"] == 8.0  # Среднее из 8,9,7
        tool_names = [tool[0] for tool in metrics["most_used_tools"]]
        assert "add_task" in tool_names
        assert "complete_task" in tool_names
        assert "list_tasks" in tool_names
        assert metrics["task_actions_summary"]["created"] == 1
        assert metrics["task_actions_summary"]["completed"] == 1

    def test_analyze_patterns(self):
        """Тест анализа паттернов поведения"""
        user_id = 12345

        # Создаем логи с разными временными паттернами
        base_time = datetime.now(timezone.utc)

        # Имитируем активность в разное время
        time_slots = [6, 9, 12, 15, 18, 21]  # Разные часы дня

        for hour in time_slots:
            # Создаем лог с определенным временем
            log = AgentLog(
                user_id=user_id,
                timestamp=base_time.replace(hour=hour),
                message_type="conversation",
                user_message="Тест",
                agent_response="Ответ",
                response_quality=8
            )
            self.db.add(log)

        self.db.commit()

        patterns = self.logger.analyze_patterns(user_id)

        assert "peak_hours" in patterns
        assert "common_themes" in patterns
        assert "response_effectiveness" in patterns

        # Проверяем, что пиковые часы найдены
        peak_hours = patterns["peak_hours"]
        assert len(peak_hours) > 0
        assert all(hour["count"] >= 0 for hour in peak_hours)

    def test_user_metrics_update(self):
        """Тест обновления агрегированных метрик пользователя"""
        user_id = 12345

        # Создаем логи
        self.logger.log_interaction(
            user_id=user_id,
            message_type="conversation",
            user_message="Создай задачу",
            agent_response="Задача создана",
            tools_used=["add_task"],
            task_actions=[{"type": "created"}]
        )

        # Проверяем метрики
        metrics = self.db.query(AgentMetrics).filter_by(user_id=user_id).first()
        assert metrics is not None
        assert metrics.total_interactions == 1
        assert metrics.tools_used_count == 1
        assert metrics.tasks_created == 1

    def test_add_user_feedback(self):
        """Тест добавления обратной связи от пользователя"""
        # Создаем лог
        log_entry = self.logger.log_interaction(
            user_id=12345,
            message_type="conversation",
            user_message="Тест",
            agent_response="Ответ"
        )

        # Добавляем feedback
        self.logger.add_user_feedback(
            log_id=log_entry.id,
            feedback="Очень полезно!",
            rating=9
        )

        # Проверяем
        updated_log = self.db.query(AgentLog).filter_by(id=log_entry.id).first()
        assert updated_log.user_feedback == "Очень полезно!"
        assert updated_log.user_satisfaction == 9


if __name__ == "__main__":
    pytest.main([__file__, "-v"])