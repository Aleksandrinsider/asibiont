"""
Универсальная система поиска задач с поддержкой русских окончаний
"""
import logging
from typing import Optional, List
from models import Task, User
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def apply_stemming(text: str) -> str:
    """
    Применяет простое стемминг для русских слов.
    Удаляет типичные окончания (а, я, у, ю, ы, и).
    
    Args:
        text: Текст для обработки
        
    Returns:
        Текст с удалёнными окончаниями
    """
    if not text or len(text) < 3:
        return text
    
    # Удаляем последний символ если это типичное окончание
    if text[-1] in 'аяуюыи':
        return text[:-1]
    
    return text


def find_task_flexible(
    session: Session,
    user: User,
    task_id: Optional[int] = None,
    task_title: Optional[str] = None,
    include_completed: bool = False,
    include_delegated: bool = True
) -> Optional[Task]:
    """
    Гибкий поиск задачи с поддержкой русских окончаний.
    
    Args:
        session: SQLAlchemy сессия
        user: Объект пользователя
        task_id: ID задачи (приоритет)
        task_title: Название/ключевые слова для поиска
        include_completed: Искать ли завершенные задачи
        include_delegated: Искать ли делегированные задачи
        
    Returns:
        Найденная задача или None
    """
    # Поиск по ID (приоритет)
    if task_id:
        task = session.query(Task).filter_by(id=task_id, user_id=user.id).first()
        if task:
            logger.info(f"[FIND_TASK] Found by ID: {task_id}")
            return task
        
        # Проверить делегированные задачи
        if include_delegated and user.username:
            username_clean = user.username.replace('@', '').lower()
            task = session.query(Task).filter(
                Task.id == task_id,
                Task.delegated_to_username == username_clean,
                Task.delegation_status == "accepted"
            ).first()
            if task:
                logger.info(f"[FIND_TASK] Found delegated task by ID: {task_id}")
                return task
        
        logger.warning(f"[FIND_TASK] No task found with ID: {task_id}")
        return None
    
    # Поиск по названию
    if not task_title or not task_title.strip():
        return None
    
    search_keywords = task_title.lower().strip().split()
    logger.info(f"[FIND_TASK] Searching with keywords: {search_keywords}")
    
    # Получить все задачи пользователя
    query = session.query(Task).filter(Task.user_id == user.id)
    
    # Добавить делегированные задачи
    if include_delegated and user.username:
        username_clean = user.username.replace('@', '').lower()
        delegated_query = session.query(Task).filter(
            Task.delegated_to_username == username_clean,
            Task.delegation_status == "accepted"
        )
        tasks = query.all() + delegated_query.all()
    else:
        tasks = query.all()
    
    # Фильтр по статусу
    if not include_completed:
        tasks = [t for t in tasks if t.status != "completed"]
    
    logger.info(f"[FIND_TASK] Total tasks to search: {len(tasks)}")
    
    # Python-level поиск с stemming
    best_match = None
    best_score = 0
    
    for task in tasks:
        title_lower = task.title.lower()
        score = 0
        
        # Проверка каждого ключевого слова
        for keyword in search_keywords:
            # Прямое совпадение
            if keyword in title_lower:
                score += 2
                continue
            
            # Поиск с stemming
            keyword_stem = apply_stemming(keyword)
            if keyword_stem != keyword and keyword_stem in title_lower:
                score += 1.5
                continue
            
            # Проверка stemming для слов в названии
            title_words = title_lower.split()
            for word in title_words:
                word_stem = apply_stemming(word)
                if keyword == word_stem or keyword_stem == word_stem:
                    score += 1
                    break
        
        # Если найдено хотя бы одно совпадение
        if score > 0:
            logger.info(f"[FIND_TASK] Task '{task.title}' score: {score}")
            if score > best_score:
                best_score = score
                best_match = task
    
    if best_match:
        logger.info(f"[FIND_TASK] Best match: '{best_match.title}' (score: {best_score})")
    else:
        logger.warning(f"[FIND_TASK] No task found matching '{task_title}'")
        # Вывести все доступные задачи для отладки
        logger.info(f"[FIND_TASK] Available tasks: {[t.title for t in tasks[:5]]}")
    
    return best_match


def find_all_matching_tasks(
    session: Session,
    user: User,
    task_title: str,
    include_completed: bool = False,
    include_delegated: bool = True
) -> List[Task]:
    """
    Найти ВСЕ задачи, соответствующие запросу.
    Полезно для массовых операций (удалить все, завершить все).
    
    Args:
        session: SQLAlchemy сессия
        user: Объект пользователя
        task_title: Название/ключевые слова
        include_completed: Искать ли завершенные
        include_delegated: Искать ли делегированные
        
    Returns:
        Список найденных задач
    """
    if not task_title or not task_title.strip():
        return []
    
    search_keywords = task_title.lower().strip().split()
    
    # Получить все задачи
    query = session.query(Task).filter(Task.user_id == user.id)
    
    if include_delegated and user.username:
        username_clean = user.username.replace('@', '').lower()
        delegated_query = session.query(Task).filter(
            Task.delegated_to_username == username_clean,
            Task.delegation_status == "accepted"
        )
        tasks = query.all() + delegated_query.all()
    else:
        tasks = query.all()
    
    if not include_completed:
        tasks = [t for t in tasks if t.status != "completed"]
    
    # Поиск всех совпадений
    matches = []
    for task in tasks:
        title_lower = task.title.lower()
        matched = False
        
        for keyword in search_keywords:
            if keyword in title_lower:
                matched = True
                break
            
            keyword_stem = apply_stemming(keyword)
            if keyword_stem in title_lower:
                matched = True
                break
            
            # Stemming слов в названии
            title_words = title_lower.split()
            for word in title_words:
                word_stem = apply_stemming(word)
                if keyword == word_stem or keyword_stem == word_stem:
                    matched = True
                    break
            
            if matched:
                break
        
        if matched:
            matches.append(task)
    
    logger.info(f"[FIND_ALL] Found {len(matches)} matching tasks for '{task_title}'")
    return matches
