"""
Самообучение — feedback loop для улучшения агента.

Что делает:
1. Анализирует каждый ответ на качество
2. Собирает паттерны: что работает, что нет
3. Адаптирует поведение под пользователя
4. Хранит preference model в БД

Метрики качества:
- response_length: средняя длина ответа
- tools_per_turn: сколько tools используется
- user_satisfaction: на основе реакции (если есть)
- pattern_success_rate: % успешных паттернов
"""

import json
import logging
from datetime import datetime, timezone
from collections import defaultdict

logger = logging.getLogger(__name__)


class SelfLearner:
    """Самообучающийся модуль агента."""
    
    def __init__(self):
        # Метрики по пользователям
        self.user_metrics = defaultdict(lambda: {
            'total_turns': 0,
            'total_response_length': 0,
            'tools_used_count': 0,
            'tools_histogram': defaultdict(int),
            'successful_patterns': [],
            'failed_patterns': [],
            'preferences': {},
            'last_emotions': [],
            'conversation_style': 'normal',  # brief/normal/detailed
        })
        
        # Глобальные паттерны (что работает для всех)
        self.global_patterns = {
            'best_tools_by_intent': {},
            'avg_response_length': 300,
            'common_issues': [],
        }
    
    def record_turn(self, user_id, user_message, response, tools_used, 
                     emotion=None, intent=None, issues=None):
        """Записывает один обмен для обучения.
        
        Args:
            user_id: Telegram ID
            user_message: Сообщение пользователя
            response: Ответ агента
            tools_used: Список использованных инструментов
            emotion: Определённая эмоция
            intent: Определённое намерение
            issues: Проблемы найденные когнитивным движком
        """
        metrics = self.user_metrics[user_id]
        
        # Базовые метрики
        metrics['total_turns'] += 1
        metrics['total_response_length'] += len(response)
        metrics['tools_used_count'] += len(tools_used)
        
        for tool in tools_used:
            metrics['tools_histogram'][tool] += 1
        
        # Эмоциональная история (последние 10)
        if emotion and emotion != 'neutral':
            metrics['last_emotions'].append({
                'emotion': emotion,
                'timestamp': datetime.now(timezone.utc).isoformat()
            })
            metrics['last_emotions'] = metrics['last_emotions'][-10:]
        
        # Паттерны успеха/неудачи
        pattern = {
            'intent': intent,
            'emotion': emotion,
            'tools': tools_used,
            'response_len': len(response),
            'had_issues': bool(issues),
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        if not issues:
            metrics['successful_patterns'].append(pattern)
            metrics['successful_patterns'] = metrics['successful_patterns'][-20:]
        else:
            pattern['issues'] = issues
            metrics['failed_patterns'].append(pattern)
            metrics['failed_patterns'] = metrics['failed_patterns'][-10:]
        
        # Адаптация стиля
        self._adapt_style(user_id, user_message, response)
        
        # Обновление глобальных паттернов
        self._update_global_patterns(intent, tools_used, issues)
        
        logger.info(f"[LEARN] Recorded turn for user {user_id}: "
                     f"turns={metrics['total_turns']}, "
                     f"avg_len={metrics['total_response_length'] // max(metrics['total_turns'], 1)}")
    
    def _adapt_style(self, user_id, user_message, response):
        """Адаптирует стиль общения под пользователя.
        
        Если пользователь пишет коротко — отвечай коротко.
        Если развёрнуто — можно подробнее.
        """
        metrics = self.user_metrics[user_id]
        avg_user_len = len(user_message)
        
        if avg_user_len < 20:
            metrics['conversation_style'] = 'brief'
        elif avg_user_len > 100:
            metrics['conversation_style'] = 'detailed'
        else:
            metrics['conversation_style'] = 'normal'
    
    def _update_global_patterns(self, intent, tools_used, issues):
        """Обновляет глобальные паттерны из отдельных обменов."""
        if intent and tools_used and not issues:
            if intent not in self.global_patterns['best_tools_by_intent']:
                self.global_patterns['best_tools_by_intent'][intent] = defaultdict(int)
            for tool in tools_used:
                self.global_patterns['best_tools_by_intent'][intent][tool] += 1
    
    def get_user_preferences(self, user_id):
        """Возвращает предпочтения пользователя для инъекции в промпт.
        
        Returns: str — блок для системного промпта.
        """
        metrics = self.user_metrics[user_id]
        
        if metrics['total_turns'] < 3:
            return ""  # Недостаточно данных
        
        prefs = []
        
        # Стиль общения
        style = metrics['conversation_style']
        if style == 'brief':
            prefs.append("Пользователь предпочитает КРАТКИЕ ответы (1-2 предложения).")
        elif style == 'detailed':
            prefs.append("Пользователь любит РАЗВЁРНУТЫЕ ответы с деталями.")
        
        # Любимые инструменты
        top_tools = sorted(metrics['tools_histogram'].items(), 
                          key=lambda x: x[1], reverse=True)[:3]
        if top_tools:
            tools_str = ', '.join(f"{t[0]}({t[1]}x)" for t in top_tools)
            prefs.append(f"Чаще всего полезны: {tools_str}")
        
        # Эмоциональный профиль
        if metrics['last_emotions']:
            emotion_counts = defaultdict(int)
            for e in metrics['last_emotions']:
                emotion_counts[e['emotion']] += 1
            dominant = max(emotion_counts, key=emotion_counts.get)
            prefs.append(f"Типичная эмоция: {dominant}")
        
        # Средняя длина ответа
        avg_len = metrics['total_response_length'] // max(metrics['total_turns'], 1)
        if avg_len < 200:
            prefs.append("Предпочитает короткие ответы (<200 символов).")
        
        if not prefs:
            return ""
        
        return "\n\n[АДАПТАЦИЯ К ПОЛЬЗОВАТЕЛЮ]\n" + "\n".join(prefs)
    
    def get_emotional_trend(self, user_id):
        """Определяет эмоциональный тренд пользователя.
        
        Returns: str — описание тренда или пустая строка.
        """
        metrics = self.user_metrics[user_id]
        emotions = metrics['last_emotions']
        
        if len(emotions) < 3:
            return ""
        
        # Последние 3 эмоции
        recent = [e['emotion'] for e in emotions[-3:]]
        
        # Все негативные?
        negative = {'tired', 'sad', 'frustrated', 'anxious'}
        if all(e in negative for e in recent):
            return "⚠️ ТРЕНД: Пользователь несколько раз подряд в негативном состоянии. Будь особенно внимателен."
        
        # Все позитивные?
        positive = {'excited'}
        if all(e in positive for e in recent):
            return "🚀 ТРЕНД: Пользователь на подъёме! Предлагай амбициозные шаги."
        
        return ""
    
    def suggest_proactive_action(self, user_id, profile_data):
        """Предлагает проактивное действие на основе обученных паттернов.
        
        Returns: str — предложение для инъекции или пустая строка.
        """
        metrics = self.user_metrics[user_id]
        
        if metrics['total_turns'] < 5:
            return ""
        
        # Если пользователь часто ищет информацию → предложи research
        if metrics['tools_histogram'].get('research_topic', 0) > 2:
            interests = profile_data.get('interests', '')
            if interests:
                return f"Проактивность: пользователь часто ищет информацию. Предложи свежие тренды по: {interests}"
        
        # Если часто создаёт задачи → предложи цель
        if metrics['tools_histogram'].get('add_task', 0) > 3:
            return "Проактивность: пользователь активно ведёт задачи. Предложи создать ЦЕЛЬ для группировки."
        
        return ""


# ═══════════════════════════════════════════════════════════════
# ГЛОБАЛЬНЫЙ ЭКЗЕМПЛЯР
# ═══════════════════════════════════════════════════════════════

_learner = None

def get_learner():
    global _learner
    if _learner is None:
        _learner = SelfLearner()
    return _learner
