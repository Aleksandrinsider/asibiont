"""
Утилиты для работы с контекстом задач
"""
import logging
import re
from typing import Optional
from models import User, Task, Session
from ai_integration.task_search import find_task_flexible

logger = logging.getLogger(__name__)


def update_user_current_task(user: User, task_title: str, session) -> Optional[Task]:
    """
    Обновляет current_task_id пользователя при упоминании задачи

    Args:
        user: Объект пользователя
        task_title: Название задачи для поиска или __CURRENT_TASK__ для использования текущей
        session: Сессия БД

    Returns:
        Найденная задача или None
    """
    try:
        if not task_title or not task_title.strip():
            return None

        # Специальный случай: ссылка на текущую задачу
        if task_title == "__CURRENT_TASK__":
            current_task = get_user_current_task(user)
            if current_task:
                logger.info(f"[CONTEXT] Using current task reference: {current_task.title}")
                return current_task
            else:
                logger.warning(f"[CONTEXT] No current task set for user {user.telegram_id}")
                return None

        # Ищем задачу по названию (включая выполненные для контекста)
        task = find_task_flexible(session, user, task_title=task_title.strip(), include_completed=True)

        if task:
            # Обновляем current_task_id пользователя
            # Setting current task for user
            user.current_task_id = task.id
            session.commit()
            # Current task set successfully
            logger.info(f"[CONTEXT] Updated current_task for user {user.telegram_id} to: {task.title} (ID: {task.id})")
            return task
        else:
            logger.warning(f"[CONTEXT] Task not found for update: '{task_title}'")
            return None

    except Exception as e:
        logger.error(f"[CONTEXT] Error updating current task: {e}")
        return None


def get_user_current_task(user: User, session=None) -> Optional[Task]:
    """
    Получает текущую задачу пользователя

    Args:
        user: Объект пользователя
        session: Сессия БД (если None, используется сессия из объекта user)

    Returns:
        Текущая задача или None
    """
    try:
        if not user.current_task_id:
            return None
        
        # Используем переданную сессию или создаём новую
        should_close = False
        if session is None:
            session = Session()
            should_close = True
        
        try:
            task = session.query(Task).filter_by(id=user.current_task_id).first()
            if task:
                return task
            else:
                # Задача больше не существует, сбрасываем
                user.current_task_id = None
                session.commit()
                logger.info(f"[CONTEXT] Cleared non-existent current_task for user {user.telegram_id}")
        finally:
            if should_close:
                session.close()
        return None
    except Exception as e:
        logger.error(f"[CONTEXT] Error getting current task: {e}")
        return None


def extract_task_reference_from_message(message: str) -> Optional[str]:
    """
    Извлекает ссылку на задачу из сообщения пользователя

    Args:
        message: Сообщение пользователя

    Returns:
        Название задачи или специальный маркер __CURRENT_TASK__ для местоимений
    """
    try:
        message_lower = message.lower().strip()
        original_message = message.strip()  # Сохраняем оригинальный регистр

        # 1. Сначала проверяем на местоимения (они имеют наивысший приоритет)
        current_task_pronouns = [
            "её", "ее", "её", "ее", "ней", "нею",  # her/it (feminine) - все формы
            "его", "его", "ним", "им",  # his/it (masculine) - все формы
            "это", "эту", "этот", "эта", "этим", "этом",  # this - все формы
            "ту", "ту", "тот", "та", "тем", "той",  # that - все формы
        ]

        for pronoun in current_task_pronouns:
            # Ищем только отдельные слова, не части слов
            import re
            if re.search(r'\b' + re.escape(pronoun) + r'\b', message_lower):
                logger.info(f"[CONTEXT] Detected current task pronoun: '{pronoun}' in message '{message_lower}'")
                return "__CURRENT_TASK__"

        # 2. Ищем прямые упоминания задач в кавычках
        quoted_tasks = re.findall(r'[""«»"]([^""«»"]+)[""«»"]', original_message)
        if quoted_tasks:
            task_title = quoted_tasks[0].strip()
            logger.info(f"[CONTEXT] Found quoted task reference: '{task_title}'")
            return task_title

        # 3. Ищем упоминания задач после ключевых слов
        task_keywords = ["задача", "задачи", "задачу", "задачей", "проект", "проекта", "проекту", "проектом", "дело", "дела", "делу", "делом"]
        for keyword in task_keywords:
            if keyword in message_lower:
                # Ищем точное совпадение слова с пробелами в оригинальном сообщении
                pattern = r'\b' + re.escape(keyword) + r'\s+(.+)'
                match = re.search(pattern, original_message, re.IGNORECASE)
                if match:
                    after_keyword = match.group(1).strip()
                    # Пропускаем если начинается с местоимения (уже обработано выше)
                    if after_keyword and not any(after_keyword.lower().startswith(p) for p in current_task_pronouns):
                        # Очищаем кавычки из начала и конца
                        after_keyword = after_keyword.strip("'\"«»")
                        # Убираем знаки препинания в конце
                        after_keyword = after_keyword.rstrip(".,!?;:")
                        # Еще раз очищаем кавычки (на случай вложенных)
                        after_keyword = after_keyword.strip("'\"«»")
                        # Берем первые несколько слов как название
                        words = after_keyword.split()[:3]  # Максимум 3 слова для точности
                        task_title = " ".join(words).strip(".,!?;:")
                        if task_title and len(task_title) > 1:  # Минимум 2 символа
                            logger.info(f"[CONTEXT] Found task reference after '{keyword}': '{task_title}'")
                            return task_title

        logger.debug(f"[CONTEXT] No task reference found in message: '{message[:50]}...'")
        return None

    except Exception as e:
        logger.error(f"[CONTEXT] Error extracting task reference: {e}")
        return None


def replace_pronouns_in_message(message: str, user_id: int, session) -> str:
    """
    Заменяет местоимения в сообщении на название текущей задачи пользователя
    
    Args:
        message: Исходное сообщение
        user_id: ID пользователя
        session: Сессия БД
        
    Returns:
        Сообщение с замененными местоимениями
    """
    try:
        from models import User
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return message
            
        current_task = get_user_current_task(user)
        if not current_task:
            return message
            
        # Список местоимений для замены
        current_task_pronouns = [
            "её", "ей", "ею", "ней", "эту", "эта", "это", "этот", "эти", "этих",
            "ту", "та", "то", "тот", "те", "тех", "его", "ему", "им", "нём",
            "её", "ей", "ею", "ней", "её", "ей", "ею", "ней"
        ]
        
        message_lower = message.lower()
        replaced = False
        
        for pronoun in current_task_pronouns:
            if re.search(r'\b' + re.escape(pronoun) + r'\b', message_lower):
                # Заменяем местоимение на название задачи
                message = re.sub(r'\b' + re.escape(pronoun) + r'\b', current_task.title, message, flags=re.IGNORECASE)
                replaced = True
                logger.info(f"[CONTEXT] Replaced pronoun '{pronoun}' with current task: '{current_task.title}'")
        
        if replaced:
            logger.info(f"[CONTEXT] Message after pronoun replacement: '{message}'")
        
        return message
        
    except Exception as e:
        logger.error(f"[CONTEXT] Error replacing pronouns in message: {e}")
        return message