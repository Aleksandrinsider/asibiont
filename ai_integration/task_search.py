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


def levenshtein_distance(s1: str, s2: str) -> int:
    """Вычисляет расстояние Левенштейна между двумя строками"""
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    
    return previous_row[-1]


def similarity_ratio(s1: str, s2: str) -> float:
    """Вычисляет коэффициент схожести двух строк (0.0 - 1.0)"""
    distance = levenshtein_distance(s1.lower(), s2.lower())
    max_len = max(len(s1), len(s2))
    if max_len == 0:
        return 1.0
    return 1.0 - (distance / max_len)


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
    
    # Убираем стоп-слова из поиска
    stop_words = {'и', 'в', 'на', 'с', 'для', 'от', 'по', 'к', 'о', 'об', 'из', 'над', 'про', 'через', 'без', 'до', 'после', 'перед'}
    search_keywords = [kw for kw in search_keywords if kw not in stop_words and len(kw) > 2]
    
    logger.info(f"[FIND_TASK] Search keywords (without stop words): {search_keywords}")
    
    if not search_keywords:
        logger.warning("[FIND_TASK] No meaningful keywords to search")
        return None
    
    # Python-level поиск с улучшенным алгоритмом
    candidates = []  # Список (задача, score)
    
    for task in tasks:
        title_lower = task.title.lower()
        title_words = [w for w in title_lower.split() if w not in stop_words and len(w) > 2]
        
        matched_keywords = 0
        fuzzy_matches = 0
        
        # Проверка каждого ключевого слова
        for keyword in search_keywords:
            keyword_stem = apply_stemming(keyword)
            
            # Прямое совпадение в названии
            if keyword in title_lower:
                matched_keywords += 2  # Повышенный вес для точного совпадения
                continue
            
            # Stemming совпадение
            if keyword_stem != keyword and keyword_stem in title_lower:
                matched_keywords += 1.5
                continue
            
            # Проверка по словам с fuzzy matching
            best_word_match = 0
            for word in title_words:
                word_stem = apply_stemming(word)
                
                # Точное совпадение stem
                if keyword == word_stem or keyword_stem == word_stem:
                    matched_keywords += 1.5
                    break
                
                # Частичное совпадение
                if keyword in word or word in keyword:
                    matched_keywords += 1.2
                    break
                
                # Fuzzy matching для близких слов (опечатки, однокоренные)
                similarity = similarity_ratio(keyword, word)
                # Пороги: 60%+ отличное совпадение, 40%+ допустимое для русского языка
                if similarity >= 0.6:  # Очень хорошее совпадение
                    best_word_match = max(best_word_match, similarity * 1.5)  # Повышенный вес
                elif similarity >= 0.4:  # Допустимое совпадение (звонок -> позвонить)
                    best_word_match = max(best_word_match, similarity)
            
            if best_word_match > 0:
                fuzzy_matches += best_word_match
        
        # Рассчитываем итоговый score
        total_score = matched_keywords + fuzzy_matches
        normalized_score = total_score / (len(search_keywords) * 2)  # Нормализуем к 0-1
        
        # Если score > 0.2 (20% совпадение для одного слова) - считаем кандидатом
        if normalized_score >= 0.2 or matched_keywords >= 1:
            logger.info(f"[FIND_TASK] Task '{task.title}' score: {normalized_score:.2f} (matched: {matched_keywords:.1f}, fuzzy: {fuzzy_matches:.2f})")
            candidates.append((task, normalized_score))
    
    # Сортируем кандидатов по score
    candidates.sort(key=lambda x: x[1], reverse=True)
    
    # Если найден только один кандидат - возвращаем его
    if len(candidates) == 1:
        logger.info(f"[FIND_TASK] ✅ Single match found: '{candidates[0][0].title}'")
        return candidates[0][0]
    
    # Если несколько кандидатов - берем лучший если он заметно лучше других
    if len(candidates) > 1:
        best_task, best_score = candidates[0]
        second_score = candidates[1][1] if len(candidates) > 1 else 0
        
        # Если лучший кандидат заметно лучше второго (разница > 20%) - возвращаем его
        if best_score - second_score > 0.2:
            logger.info(f"[FIND_TASK] ✅ Clear winner: '{best_task.title}' ({best_score:.2f} vs {second_score:.2f})")
            return best_task
        
        # Иначе возвращаем список для выбора
        logger.info(f"[FIND_TASK] ⚠️ Multiple similar matches found: {len(candidates)}")
        return candidates[0][0]  # Пока возвращаем лучший
    
    best_match = candidates[0][0] if candidates else None
    best_score = candidates[0][1] if candidates else 0
    
    if best_match:
        logger.info(f"[FIND_TASK] Best match: '{best_match.title}' (score: {best_score:.2f})")
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
