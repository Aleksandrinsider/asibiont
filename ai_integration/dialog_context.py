"""
Контекст диалога для запоминания последних действий
"""
import logging
from typing import Optional
from models import Task
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class DialogContext:
    """Контекст диалога пользователя для запоминания последних действий"""
    
    def __init__(self):
        self.last_mentioned_task: Optional[Task] = None
        self.last_action: Optional[str] = None
        self.last_result: Optional[str] = None
        self.last_update: Optional[datetime] = None
        self.context_timeout = timedelta(minutes=15)  # Контекст живет 15 минут
    
    def is_valid(self) -> bool:
        """Проверяет актуален ли контекст"""
        if not self.last_update:
            return False
        return datetime.now() - self.last_update < self.context_timeout
    
    def update(self, action: str, task: Optional[Task] = None, result: str = ""):
        """Обновляет контекст диалога"""
        self.last_action = action
        self.last_mentioned_task = task
        self.last_result = result
        self.last_update = datetime.now()
        
        if task:
            logger.info(f"[CONTEXT] Updated: action='{action}', task='{task.title}'")
        else:
            logger.info(f"[CONTEXT] Updated: action='{action}', no task")
    
    def get_last_task(self) -> Optional[Task]:
        """Возвращает последнюю упомянутую задачу если контекст актуален"""
        if self.is_valid() and self.last_mentioned_task:
            logger.info(f"[CONTEXT] Using last task: '{self.last_mentioned_task.title}'")
            return self.last_mentioned_task
        return None
    
    def clear(self):
        """Очищает контекст"""
        self.last_mentioned_task = None
        self.last_action = None
        self.last_result = None
        self.last_update = None
        logger.info("[CONTEXT] Cleared")


# Глобальный словарь контекстов для каждого пользователя
_user_contexts: dict[int, DialogContext] = {}
_MAX_CONTEXTS = 1000  # Лимит записей для предотвращения утечки памяти


def _cleanup_stale_contexts():
    """Удаляет устаревшие контексты для предотвращения утечки памяти"""
    if len(_user_contexts) <= _MAX_CONTEXTS:
        return
    stale = [uid for uid, ctx in _user_contexts.items() if not ctx.is_valid()]
    for uid in stale:
        del _user_contexts[uid]
    # Если после очистки все ещё слишком много — удаляем самые старые
    if len(_user_contexts) > _MAX_CONTEXTS:
        sorted_by_time = sorted(
            _user_contexts.items(),
            key=lambda x: x[1].last_update or datetime.min
        )
        for uid, _ in sorted_by_time[:len(_user_contexts) - _MAX_CONTEXTS]:
            del _user_contexts[uid]


def get_user_context(user_id: int) -> DialogContext:
    """Получает или создает контекст для пользователя"""
    _cleanup_stale_contexts()
    if user_id not in _user_contexts:
        _user_contexts[user_id] = DialogContext()
    return _user_contexts[user_id]


def resolve_task_reference(message: str, user_id: int, session) -> Optional[Task]:
    """
    Разрешает местоимения в контексте диалога.
    
    Примеры:
    - "Перенеси её на завтра" → возвращает последнюю упомянутую задачу
    - "Удали эту задачу" → возвращает последнюю упомянутую задачу
    - "Отметь ту выполненной" → возвращает последнюю упомянутую задачу
    """
    message_lower = message.lower()
    
    # Проверяем наличие местоимений
    pronouns = ['её', 'ее', 'эту', 'эта', 'ту', 'та', 'этой', 'той', 'это']
    has_pronoun = any(pronoun in message_lower for pronoun in pronouns)
    
    if not has_pronoun:
        return None
    
    # Получаем контекст пользователя
    context = get_user_context(user_id)
    last_task = context.get_last_task()
    
    if last_task:
        logger.info(f"[PRONOUN] Resolved '{message}' → task '{last_task.title}'")
        # Обновляем task из БД (может быть изменена)
        from models import Task as TaskModel
        refreshed_task = session.query(TaskModel).filter_by(id=last_task.id).first()
        return refreshed_task
    
    logger.warning(f"[PRONOUN] No context found for '{message}'")
    return None
