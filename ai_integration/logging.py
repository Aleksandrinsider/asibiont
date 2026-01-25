"""
Логирование работы AI агента для анализа и улучшения качества обслуживания
"""
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from sqlalchemy.orm import Session
from models import AgentLog, AgentMetrics, User
import logging

logger = logging.getLogger(__name__)


class AgentLogger:
    """Класс для логирования работы AI агента"""

    def __init__(self, db: Session):
        self.db = db

    def log_interaction(self,
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
        Логирование взаимодействия с пользователем

        Args:
            user_id: ID пользователя
            message_type: Тип сообщения ('reminder', 'proactive', etc.)
            user_message: Сообщение пользователя
            agent_response: Ответ агента
            tools_used: Список использованных инструментов
            task_actions: Действия с задачами
            response_time_ms: Время генерации ответа
            detected_intent: Распознанное намерение
            conversation_context: Контекст разговора
        """
        try:
            log_entry = AgentLog(
                user_id=user_id,
                message_type=message_type,
                user_message=user_message,
                agent_response=agent_response,
                tools_used=json.dumps(tools_used or []),
                task_actions=json.dumps(task_actions or []),
                response_time_ms=response_time_ms,
                detected_intent=detected_intent,
                conversation_context=conversation_context,
                response_quality=self._assess_response_quality(agent_response)
            )

            self.db.add(log_entry)
            self.db.commit()
            self.db.refresh(log_entry)

            # Обновляем метрики пользователя
            self._update_user_metrics(user_id)

            logger.info(f"Logged interaction for user {user_id}, type: {message_type}")
            return log_entry

        except Exception as e:
            logger.error(f"Failed to log interaction: {e}")
            self.db.rollback()
            return None

    def add_user_feedback(self, log_id: int, feedback: str, rating: int = None):
        """Добавление обратной связи от пользователя"""
        try:
            log_entry = self.db.query(AgentLog).filter(AgentLog.id == log_id).first()
            if log_entry:
                log_entry.user_feedback = feedback
                if rating:
                    log_entry.user_satisfaction = rating
                self.db.commit()
                logger.info(f"Added feedback to log {log_id}")
        except Exception as e:
            logger.error(f"Failed to add feedback: {e}")
            self.db.rollback()

    def get_user_logs(self, user_id: int, days: int = 7) -> List[AgentLog]:
        """Получение логов пользователя за период"""
        try:
            since_date = datetime.now() - timedelta(days=days)
            return self.db.query(AgentLog).filter(
                AgentLog.user_id == user_id,
                AgentLog.timestamp >= since_date
            ).order_by(AgentLog.timestamp.desc()).all()
        except Exception as e:
            logger.error(f"Failed to get user logs: {e}")
            return []

    def get_performance_metrics(self, user_id: int, days: int = 30) -> Dict[str, Any]:
        """Получение метрик производительности агента для пользователя"""
        try:
            since_date = datetime.now() - timedelta(days=days)
            logs = self.db.query(AgentLog).filter(
                AgentLog.user_id == user_id,
                AgentLog.timestamp >= since_date
            ).all()

            if not logs:
                return {}

            # Анализ логов
            total_interactions = len(logs)
            avg_quality = sum(log.response_quality or 0 for log in logs) / total_interactions
            avg_response_time = sum(log.response_time_ms or 0 for log in logs) / total_interactions

            # Анализ инструментов
            tools_count = {}
            intents_count = {}
            task_actions_count = {'created': 0, 'completed': 0, 'updated': 0, 'deleted': 0}

            for log in logs:
                # Подсчет инструментов
                if log.tools_used:
                    tools = json.loads(log.tools_used)
                    for tool in tools:
                        tools_count[tool] = tools_count.get(tool, 0) + 1

                # Подсчет намерений
                if log.detected_intent:
                    intents_count[log.detected_intent] = intents_count.get(log.detected_intent, 0) + 1

                # Подсчет действий с задачами
                if log.task_actions:
                    actions = json.loads(log.task_actions)
                    for action in actions:
                        action_type = action.get('type', '')
                        if action_type in task_actions_count:
                            task_actions_count[action_type] += 1

            return {
                'total_interactions': total_interactions,
                'avg_response_quality': round(avg_quality, 1),
                'avg_response_time_ms': round(avg_response_time, 0),
                'most_used_tools': sorted(tools_count.items(), key=lambda x: x[1], reverse=True)[:5],
                'common_intents': sorted(intents_count.items(), key=lambda x: x[1], reverse=True)[:5],
                'task_actions_summary': task_actions_count,
                'period_days': days
            }

        except Exception as e:
            logger.error(f"Failed to get performance metrics: {e}")
            return {}

    def analyze_patterns(self, user_id: int) -> Dict[str, Any]:
        """Анализ паттернов поведения пользователя и агента"""
        try:
            logs = self.get_user_logs(user_id, days=30)

            if not logs:
                return {}

            patterns = {
                'peak_hours': self._analyze_peak_hours(logs),
                'common_themes': self._analyze_common_themes(logs),
                'response_effectiveness': self._analyze_response_effectiveness(logs),
                'learning_opportunities': self._identify_learning_opportunities(logs)
            }

            return patterns

        except Exception as e:
            logger.error(f"Failed to analyze patterns: {e}")
            return {}

    def _update_user_metrics(self, user_id: int):
        """Обновление агрегированных метрик пользователя"""
        try:
            # Получаем текущие метрики за сегодня
            today = datetime.now().date()
            metrics = self.db.query(AgentMetrics).filter(
                AgentMetrics.user_id == user_id,
                AgentMetrics.date >= today
            ).first()

            if not metrics:
                metrics = AgentMetrics(user_id=user_id, date=datetime.now())
                self.db.add(metrics)

            # Обновляем счетчики
            logs_today = self.db.query(AgentLog).filter(
                AgentLog.user_id == user_id,
                AgentLog.timestamp >= today
            ).all()

            metrics.total_interactions = len(logs_today)
            metrics.tools_used_count = sum(len(json.loads(log.tools_used or '[]')) for log in logs_today)

            # Подсчет действий с задачами
            task_actions = []
            for log in logs_today:
                if log.task_actions:
                    task_actions.extend(json.loads(log.task_actions))

            metrics.tasks_created = sum(1 for action in task_actions if action.get('type') == 'created')
            metrics.tasks_completed = sum(1 for action in task_actions if action.get('type') == 'completed')

            # Средние оценки
            qualities = [log.response_quality for log in logs_today if log.response_quality]
            if qualities:
                metrics.avg_response_quality = sum(qualities) // len(qualities)

            helpfulness = [log.helpfulness_score for log in logs_today if log.helpfulness_score]
            if helpfulness:
                metrics.avg_helpfulness = sum(helpfulness) // len(helpfulness)

            self.db.commit()

        except Exception as e:
            logger.error(f"Failed to update user metrics: {e}")
            self.db.rollback()

    def _assess_response_quality(self, response: str) -> int:
        """Автоматическая оценка качества ответа (1-10)"""
        if not response:
            return 1

        score = 5  # Базовая оценка

        # Положительные факторы
        if len(response) > 100:  # Подробный ответ
            score += 1
        if any(word in response.lower() for word in ['конкретно', 'точнее', 'давай', 'предлагаю']):
            score += 1  # Живой стиль общения
        if '?' in response:  # Вовлечение в диалог
            score += 1
        if any(emoji in response for emoji in ['✅', '📅', '⏰', '🎯']):
            score += 0.5  # Уместное использование эмодзи

        # Отрицательные факторы
        if len(response) < 20:  # Слишком короткий
            score -= 2
        if response.count('!') > 3:  # Переизбыток восклицаний
            score -= 0.5

        return max(1, min(10, int(score)))

    def _analyze_peak_hours(self, logs: List[AgentLog]) -> List[Dict]:
        """Анализ пиковых часов активности"""
        hour_counts = {}
        for log in logs:
            hour = log.timestamp.hour
            hour_counts[hour] = hour_counts.get(hour, 0) + 1

        return sorted(
            [{'hour': hour, 'count': count} for hour, count in hour_counts.items()],
            key=lambda x: x['count'],
            reverse=True
        )[:3]

    def _analyze_common_themes(self, logs: List[AgentLog]) -> List[str]:
        """Анализ наиболее частых тем разговоров"""
        themes = []
        for log in logs:
            if log.conversation_context:
                themes.extend(log.conversation_context.split(','))

        theme_counts = {}
        for theme in themes:
            theme = theme.strip().lower()
            if theme:
                theme_counts[theme] = theme_counts.get(theme, 0) + 1

        return sorted(theme_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    def _analyze_response_effectiveness(self, logs: List[AgentLog]) -> Dict[str, float]:
        """Анализ эффективности ответов"""
        total_logs = len(logs)
        if total_logs == 0:
            return {}

        high_quality = sum(1 for log in logs if (log.response_quality or 0) >= 7)
        with_feedback = sum(1 for log in logs if log.user_feedback)
        with_satisfaction = sum(1 for log in logs if log.user_satisfaction)

        return {
            'high_quality_responses_percent': (high_quality / total_logs) * 100,
            'responses_with_feedback_percent': (with_feedback / total_logs) * 100,
            'responses_with_satisfaction_percent': (with_satisfaction / total_logs) * 100,
            'avg_satisfaction': sum(log.user_satisfaction or 0 for log in logs) / max(with_satisfaction, 1)
        }

    def _identify_learning_opportunities(self, logs: List[AgentLog]) -> List[str]:
        """Идентификация возможностей для улучшения"""
        opportunities = []

        # Анализ коротких ответов
        short_responses = [log for log in logs if len(log.agent_response or '') < 100]
        if len(short_responses) > len(logs) * 0.3:
            opportunities.append("Увеличить подробность ответов - много коротких ответов")

        # Анализ отсутствия вовлечения
        no_questions = [log for log in logs if '?' not in (log.agent_response or '')]
        if len(no_questions) > len(logs) * 0.5:
            opportunities.append("Увеличить вовлечение пользователя - задавать больше вопросов")

        # Анализ качества
        low_quality = [log for log in logs if (log.response_quality or 5) < 5]
        if len(low_quality) > len(logs) * 0.2:
            opportunities.append("Улучшить качество ответов - много низкооцененных ответов")

        return opportunities


# Глобальный логгер для удобства использования
_agent_logger = None

def get_agent_logger(db: Session) -> AgentLogger:
    """Получение экземпляра логгера агента"""
    global _agent_logger
    if _agent_logger is None or _agent_logger.db != db:
        _agent_logger = AgentLogger(db)
    return _agent_logger