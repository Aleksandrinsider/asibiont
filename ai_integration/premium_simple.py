"""
Smart Automation - встраивается в текущую архитектуру

Логика: AI-агент отправляет полезные рекомендации 
релевантным людям, которые косвенно помогают достижению целей пользователя.
Доступно всем пользователям, оплата токенами.
"""

import logging
import json
from typing import Dict, List, Any, Optional, Union
from datetime import datetime, timedelta
import pytz
from models import Session, User, UserProfile, Task
from sqlalchemy.orm import Session as SessionType
from sqlalchemy import and_, or_

logger = logging.getLogger(__name__)

# Cooldown кэш: {premium_user_id: {task_id: last_trigger_time}}
_COOLDOWN_CACHE: Dict[int, Dict[int, datetime]] = {}

# Real-time insights кэш: {premium_user_id: {'insights': [...], 'timestamp': datetime}}
_INSIGHTS_CACHE: Dict[int, Dict[str, Any]] = {}
_INSIGHTS_CACHE_TTL_MINUTES = 10  # Кэш на 10 минут для тяжёлых операций


def _check_cooldown(premium_user_id: int, task_id: int, minutes: int = 30) -> bool:
    """Проверяет cooldown для задачи"""
    
    if premium_user_id not in _COOLDOWN_CACHE:
        return True
    
    if task_id not in _COOLDOWN_CACHE[premium_user_id]:
        return True
    
    last_trigger = _COOLDOWN_CACHE[premium_user_id][task_id]
    elapsed = datetime.now(pytz.UTC) - last_trigger
    
    return elapsed > timedelta(minutes=minutes)


def _update_cooldown(premium_user_id: int, task_id: int):
    """Обновляет cooldown для задачи"""
    
    if premium_user_id not in _COOLDOWN_CACHE:
        _COOLDOWN_CACHE[premium_user_id] = {}
    
    _COOLDOWN_CACHE[premium_user_id][task_id] = datetime.now(pytz.UTC)


async def trigger_premium_automation_realtime(premium_user_id: int, 
                                             task_id: Optional[int] = None,
                                             task_description: Optional[str] = None) -> Dict[str, Any]:
    """
    Real-time триггер Premium автоматизации
    
    Вызывается сразу после создания/обновления задачи Premium пользователем.
    Анализирует задачу и отправляет рекомендации релевантным людям.
    
    Args:
        premium_user_id: Telegram ID Premium пользователя
        task_id: ID созданной задачи (для cooldown)
        task_description: Описание задачи для анализа
    
    Returns:
        Dict: Отчёт о выполненной работе
    """
    
    logger.info(f"[PREMIUM_RT] Real-time trigger for user {premium_user_id}, task {task_id}")
    
    # Проверяем cooldown (не чаще 1 раза в 30 минут на задачу)
    if task_id and not _check_cooldown(premium_user_id, task_id, minutes=30):
        logger.info(f"[PREMIUM_RT] Cooldown active for task {task_id}, skipping")
        return {"status": "cooldown", "recommendations_saved": 0}
    
    session = Session()
    try:
        # Получаем Premium пользователя
        premium_user = session.query(User).filter_by(telegram_id=premium_user_id).first()
        if not premium_user:
            logger.error(f"[PREMIUM_RT] User {premium_user_id} not found")
            return {"error": "User not found"}
        
        # Собираем контекст для анализа
        premium_profile = session.query(UserProfile).filter_by(user_id=premium_user.id).first()
        goals_text = getattr(premium_profile, 'goals', None) if premium_profile else None
        
        # Получаем активные задачи
        user_id = getattr(premium_user, 'id', 0)
        active_tasks = session.query(Task).filter(
            and_(
                Task.user_id == user_id,
                Task.status.in_(['pending', 'in_progress'])
            )
        ).order_by(Task.created_at.desc()).limit(10).all()
        
        # Формируем контекст для AI
        context_parts = []
        
        if goals_text and str(goals_text).strip() not in ("None", ""):
            context_parts.append(f"ЦЕЛИ ПОЛЬЗОВАТЕЛЯ:\n{goals_text}")
        
        if task_description:
            context_parts.append(f"\nНОВАЯ ЗАДАЧА (только что создана):\n{task_description}")
        
        if active_tasks:
            tasks_text = "\n".join([f"- {t.description}" for t in active_tasks[:5]])
            context_parts.append(f"\nАКТИВНЫЕ ЗАДАЧИ:\n{tasks_text}")
        
        if not context_parts:
            logger.warning(f"[PREMIUM_RT] No context for user {premium_user_id}")
            return {"error": "No goals or tasks", "recommendations_saved": 0}
        
        full_context = "\n\n".join(context_parts)
        logger.info(f"[PREMIUM_RT] Context length: {len(full_context)} chars")
        
        # Анализируем через AI
        analysis = await analyze_context_with_ai(full_context)
        
        if not analysis:
            logger.warning("[PREMIUM_RT] Failed to analyze context")
            return {"error": "Failed to analyze", "recommendations_saved": 0}
        
        # Находим релевантных людей
        relevant_users = find_relevant_users_for_goals(
            session,
            analysis,
            exclude_user_id=user_id
        )
        
        logger.info(f"[PREMIUM_RT] Found {len(relevant_users)} relevant users")
        
        if not relevant_users:
            return {
                "items_analyzed": len(analysis),
                "relevant_users_found": 0,
                "recommendations_saved": 0
            }
        
        # Сохраняем рекомендации в профили (макс 3 за раз для real-time)
        saved_count = 0
        saved_details = []
        partners_found = []  # Для уведомления Premium
        
        for user_data in relevant_users[:3]:
            user = user_data['user']
            matching_goal = user_data['matching_goal']
            match_reason = user_data['match_reason']
            
            # Сохраняем рекомендацию партнёру
            recommendation_data = {
                "type": "task_created",
                "goal": matching_goal.get('goal', 'Unknown'),
                "opportunity": matching_goal.get('opportunity', matching_goal.get('goal', 'Unknown')),
                "match_reason": match_reason,
                "premium_user_id": premium_user_id,
                "premium_username": premium_user.username,
                "premium_interests": premium_profile.interests if premium_profile else None,
                "premium_skills": premium_profile.skills if premium_profile else None,
                "timestamp": datetime.now(pytz.UTC).isoformat(),
                "shown_count": 0,
                "max_shows": 3
            }
            success = manage_recommendations(user.telegram_id, 'save', recommendation_data, session)
            
            if success:
                saved_count += 1
                saved_details.append({
                    "user": user.username or f"User_{user.telegram_id}",
                    "goal": matching_goal['goal'],
                    "match_reason": match_reason
                })
                
                # Сохраняем уведомление Premium о найденном партнёре
                save_partner_notification_to_premium(
                    session=session,
                    premium_user_id=premium_user_id,
                    partner_username=user.username or f"User_{user.telegram_id}",
                    partner_telegram_id=user.telegram_id,
                    matching_goal=matching_goal,
                    match_reason=match_reason,
                    task_id=task_id
                )
                
                partners_found.append({
                    "username": user.username or f"User_{user.telegram_id}",
                    "telegram_id": user.telegram_id,
                    "match_reason": match_reason
                })
        
        # Обновляем cooldown
        if task_id:
            _update_cooldown(premium_user_id, task_id)
        
        report = {
            "premium_user": premium_user.username,
            "trigger": "realtime",
            "task_id": task_id,
            "items_analyzed": len(analysis),
            "relevant_users_found": len(relevant_users),
            "recommendations_saved": saved_count,
            "partners_notified_to_premium": len(partners_found),
            "saved_details": saved_details,
            "partners_found": partners_found,
            "timestamp": datetime.now(pytz.UTC).isoformat()
        }
        
        logger.info(f"[PREMIUM_RT] Completed: {saved_count} recommendations saved to profiles")
        return report
        
    except Exception as e:
        logger.error(f"[PREMIUM_RT] Error: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e), "suggestions_sent": 0}
    
    finally:
        session.close()


async def run_premium_automation(premium_user_id: int) -> Dict[str, Any]:
    """
    Batch автоматизация для Premium (deprecated, используйте trigger_premium_automation_realtime)
    
    Оставлено для совместимости. Анализирует все цели и задачи разом.
    
    Args:
        premium_user_id: Telegram ID Premium пользователя
    
    Returns:
        Dict: Отчёт о выполненной работе
    """
    
    logger.info(f"[PREMIUM_AUTO] Starting automation for user {premium_user_id}")
    
    session = Session()
    try:
        # Получаем Premium пользователя и его цели
        premium_user = session.query(User).filter_by(telegram_id=premium_user_id).first()
        if not premium_user:
            logger.error(f"[PREMIUM_AUTO] User {premium_user_id} not found")
            return {"error": "User not found"}
        
        premium_profile = session.query(UserProfile).filter_by(user_id=premium_user.id).first()
        goals_text = getattr(premium_profile, 'goals', None) if premium_profile else None
        
        if not goals_text or str(goals_text).strip() in ("None", ""):
            logger.warning(f"[PREMIUM_AUTO] No goals set for user {premium_user_id}")
            return {"error": "No goals set", "suggestions_sent": 0}
        
        logger.info(f"[PREMIUM_AUTO] Goals: {goals_text[:100]}...")
        
        # Анализируем цели через AI
        goals_analysis = await analyze_goals_with_ai(goals_text)
        
        if not goals_analysis:
            logger.warning("[PREMIUM_AUTO] Failed to analyze goals")
            return {"error": "Failed to analyze goals", "suggestions_sent": 0}
        
        # Находим релевантных людей
        user_id = getattr(premium_user, 'id', 0)
        relevant_users = find_relevant_users_for_goals(
            session, 
            goals_analysis,
            exclude_user_id=user_id
        )
        
        logger.info(f"[PREMIUM_AUTO] Found {len(relevant_users)} relevant users")
        
        if not relevant_users:
            return {
                "goals_analyzed": len(goals_analysis),
                "relevant_users_found": 0,
                "suggestions_sent": 0
            }
        
        # Отправляем рекомендации (через существующий механизм)
        sent_count = 0
        sent_details = []
        
        for user_data in relevant_users[:5]:  # Макс 5 за раз, чтобы не спамить
            user = user_data['user']
            matching_goal = user_data['matching_goal']
            match_reason = user_data['match_reason']
            
            # Генерируем и отправляем сообщение
            success = await send_suggestion_to_user(
                target_user_id=user.telegram_id,
                premium_goal=matching_goal,
                match_reason=match_reason
            )
            
            if success:
                sent_count += 1
                sent_details.append({
                    "user": user.username or f"User_{user.telegram_id}",
                    "goal": matching_goal['goal'],
                    "match_reason": match_reason
                })
        
        # Формируем отчёт
        report = {
            "premium_user": premium_user.username,
            "goals_analyzed": len(goals_analysis),
            "relevant_users_found": len(relevant_users),
            "suggestions_sent": sent_count,
            "sent_details": sent_details,
            "timestamp": datetime.now(pytz.UTC).isoformat()
        }
        
        logger.info(f"[PREMIUM_AUTO] Completed: {sent_count} suggestions sent")
        
        return report
    
    except Exception as e:
        logger.error(f"[PREMIUM_AUTO] Error in automation: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e), "suggestions_sent": 0}
    
    finally:
        session.close()


async def analyze_context_with_ai(context_text: str) -> List[Dict[str, Any]]:
    """
    AI анализирует контекст Premium пользователя (цели + задачи)
    
    Извлекает actionable items и критерии для поиска людей
    
    Args:
        context_text: Полный контекст (цели, задачи)
    
    Returns:
        List[Dict]: Список items с критериями
    """
    
    from .autonomous_agent import HybridAutonomousAgent
    
    agent = HybridAutonomousAgent()
    
    system_prompt = f"""Ты помощник Premium пользователя. Анализируешь его цели и задачи.

КОНТЕКСТ ПОЛЬЗОВАТЕЛЯ:
{context_text}

ЗАДАЧА: 
Извлеки actionable items (цели/задачи), для которых можно найти релевантных людей.

Для каждого item определи:
- Что нужно достичь
- Какие люди могут помочь (интересы, навыки, роль)

Верни JSON:
[
  {{
    "goal": "краткое описание цели/задачи",
    "opportunity": "что можно предложить людям как возможность",
    "needed_people": {{
      "interests": ["интерес1", "интерес2"],
      "skills": ["навык1", "навык2"],
      "role_description": "кто нужен"
    }}
  }}
]

Если нет actionable items - верни [].
"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Проанализируй контекст: {context_text[:500]}..."}
    ]
    
    try:
        response = await agent.call_ai(messages, use_tools=False, temperature=0.3)
        content = response['choices'][0]['message']['content']
        
        # Парсим JSON
        if '```json' in content:
            content = content.split('```json')[1].split('```')[0].strip()
        elif '```' in content:
            content = content.split('```')[1].split('```')[0].strip()
        
        items = json.loads(content)
        logger.info(f"[PREMIUM_AUTO] Parsed {len(items)} items from AI")
        return items
        
    except Exception as e:
        logger.error(f"[PREMIUM_AUTO] Failed to parse context: {e}")
        return []


async def analyze_goals_with_ai(goals_text: str) -> List[Dict[str, Any]]:
    """
    AI анализирует цели Premium пользователя (legacy wrapper)
    
    Args:
        goals_text: Текст целей из UserProfile.goals
    
    Returns:
        List[Dict]: Список целей с критериями
    """
    return await analyze_context_with_ai(goals_text)


def find_relevant_users_for_goals(session: SessionType, 
                                  goals: List[Dict[str, Any]],
                                  exclude_user_id: int,
                                  limit: int = 10) -> List[Dict[str, Any]]:
    """
    Находит пользователей релевантных для целей
    
    Простой matching по interests/skills в профиле
    
    Args:
        session: DB session
        goals: Список целей с критериями
        exclude_user_id: ID пользователя который не нужен (сам Premium)
        limit: Максимум результатов
    
    Returns:
        List[Dict]: Релевантные пользователи с reasoning
    """
    
    # Получаем всех пользователей с профилями
    users_with_profiles = session.query(User, UserProfile)\
        .join(UserProfile, User.id == UserProfile.user_id)\
        .filter(User.id != exclude_user_id)\
        .all()
    
    relevant = []
    
    for user, profile in users_with_profiles:
        for goal in goals:
            criteria = goal['needed_people']
            
            # Проверяем интересы
            if profile.interests and 'interests' in criteria:
                user_interests = [i.strip().lower() for i in profile.interests.split(',')]
                target_interests = [i.lower() for i in criteria['interests']]
                
                # Ищем совпадения
                matches = []
                for target in target_interests:
                    for user_int in user_interests:
                        if target in user_int or user_int in target:
                            matches.append(target)
                            break
                
                if matches:
                    relevant.append({
                        'user': user,
                        'matching_goal': goal,
                        'match_reason': f"Интересы: {', '.join(matches)}",
                        'score': len(matches) / len(target_interests)
                    })
                    break  # Не проверяем другие цели для этого пользователя
            
            # Проверяем навыки
            if profile.skills and 'skills' in criteria:
                user_skills = [s.strip().lower() for s in profile.skills.split(',')]
                target_skills = [s.lower() for s in criteria['skills']]
                
                skill_matches = []
                for target in target_skills:
                    for user_skill in user_skills:
                        if target in user_skill or user_skill in target:
                            skill_matches.append(target)
                            break
                
                if skill_matches:
                    relevant.append({
                        'user': user,
                        'matching_goal': goal,
                        'match_reason': f"Навыки: {', '.join(skill_matches)}",
                        'score': len(skill_matches) / len(target_skills)
                    })
                    break
    
    # Сортируем по score и убираем дубликаты
    seen_users = set()
    unique_relevant = []
    
    for item in sorted(relevant, key=lambda x: x['score'], reverse=True):
        if item['user'].id not in seen_users:
            seen_users.add(item['user'].id)
            unique_relevant.append(item)
    
    return unique_relevant[:limit]


def manage_recommendations(user_id: int, action: str, recommendation_data: Dict[str, Any] = None, session: SessionType = None) -> Union[bool, str, None]:
    """
    Универсальная функция управления рекомендациями в профиле пользователя

    Args:
        user_id: Telegram ID пользователя
        action: 'save' - сохранить рекомендацию, 'get' - получить для промпта, 'mark_shown' - отметить как показанную
        recommendation_data: Данные рекомендации (для action='save')
        session: DB session (опционально)

    Returns:
        bool для 'save', str для 'get', None для 'mark_shown'
    """

    close_session = False
    if session is None:
        session = Session()
        close_session = True

    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return False if action == 'save' else ("" if action == 'get' else None)

        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if not profile:
            if action == 'save':
                profile = UserProfile(user_id=user.id)
                session.add(profile)
            else:
                return False if action == 'save' else ("" if action == 'get' else None)

        if action == 'save':
            # Сохраняем рекомендацию
            existing = []
            if profile.pending_premium_recommendations:
                try:
                    existing = json.loads(profile.pending_premium_recommendations)
                    if not isinstance(existing, list):
                        existing = []
                except (json.JSONDecodeError, ValueError, TypeError):
                    existing = []

            # Добавляем новую (макс 20 рекомендаций)
            max_recs = 20
            existing.append(recommendation_data)
            existing = existing[-max_recs:]

            profile.pending_premium_recommendations = json.dumps(existing, ensure_ascii=False)
            session.commit()
            logger.info(f"[RECOMMENDATIONS] Saved recommendation to profile {user_id}")
            return True

        elif action == 'get':
            # Получаем рекомендации для промпта
            if not profile.pending_premium_recommendations:
                return ""

            try:
                recommendations = json.loads(profile.pending_premium_recommendations)
                if not isinstance(recommendations, list):
                    return ""
            except (json.JSONDecodeError, ValueError, TypeError):
                return ""

            # Фильтруем активные рекомендации
            active_collabs = [
                r for r in recommendations
                if r.get('type') == 'task_created'
                and r.get('shown_count', 0) < r.get('max_shows', 3)
            ]

            if not active_collabs:
                return ""

            # Форматируем для промпта
            collab_items = []
            for rec in active_collabs[:2]:  # Макс 2 коллаборации
                premium_user = rec.get('premium_username', 'пользователь')
                goal = rec.get('goal', 'проект')
                match_reason = rec.get('match_reason', '')
                premium_interests = rec.get('premium_interests', '')
                premium_skills = rec.get('premium_skills', '')

                collab_text = f"- Коллаборация: @{premium_user} работает над '{goal}'"

                if match_reason:
                    collab_text += f"\n  Релевантно: {match_reason}"

                context_parts = []
                if premium_interests:
                    context_parts.append(f"интересы: {premium_interests}")
                if premium_skills:
                    context_parts.append(f"навыки: {premium_skills}")

                if context_parts:
                    collab_text += f"\n  Контекст Premium: {', '.join(context_parts)}"

                collab_items.append(collab_text)

            if not collab_items:
                return ""

            prompt_section = "\n\n" + "="*50
            prompt_section += "\nВОЗМОЖНОСТИ КОЛЛАБОРАЦИЙ (упомяни ЕСТЕСТВЕННО если релевантно):\n\n"
            prompt_section += "\n\n".join(collab_items)
            prompt_section += "\n\nВАЖНО:"
            prompt_section += "\n- Упоминай ТОЛЬКО если пользователь говорит о похожих темах"
            prompt_section += "\n- Преподноси как взаимовыгодное сотрудничество, не как 'работу'"
            prompt_section += "\n- Короткое упоминание (1-2 предложения): 'Кстати, @user работает над похожим...'"
            prompt_section += "\n" + "="*50

            return prompt_section

        elif action == 'mark_shown':
            # Отмечаем рекомендации как показанные
            if not profile.pending_premium_recommendations:
                return

            try:
                recommendations = json.loads(profile.pending_premium_recommendations)
                if not isinstance(recommendations, list):
                    return
            except (json.JSONDecodeError, ValueError, TypeError):
                return

            # Увеличиваем shown_count
            for rec in recommendations:
                if rec.get('type') == 'task_created' and rec.get('shown_count', 0) < 3:
                    rec['shown_count'] = rec.get('shown_count', 0) + 1

            # Удаляем показанные 3+ раза
            active = [
                r for r in recommendations
                if (r.get('type') == 'task_created' and r.get('shown_count', 0) < 3) or
                   (r.get('type') in ['partner_found', 'partner_progress'])
            ]

            if active:
                profile.pending_premium_recommendations = json.dumps(active, ensure_ascii=False)
            else:
                profile.pending_premium_recommendations = None

            session.commit()
            logger.info(f"[RECOMMENDATIONS] Marked recommendations as shown for user {user_id}")

    except Exception as e:
        logger.error(f"[RECOMMENDATIONS] Error in manage_recommendations: {e}")
        if action == 'save':
            session.rollback()
            return False
    finally:
        if close_session:
            session.close()








def save_partner_notification_to_premium(session: SessionType,
                                         premium_user_id: int,
                                         partner_username: str,
                                         partner_telegram_id: int,
                                         matching_goal: Dict[str, Any],
                                         match_reason: str,
                                         task_id: Optional[int] = None) -> bool:
    """
    Сохраняет уведомление о найденном партнёре в профиль Premium-пользователя
    
    Premium увидит в своём промпте: "Найден релевантный партнёр @username для твоей цели X"
    
    Args:
        session: DB session
        premium_user_id: Telegram ID Premium пользователя
        partner_username: Username найденного партнёра
        partner_telegram_id: Telegram ID партнёра
        matching_goal: Цель для которой найден партнёр
        match_reason: Почему этот партнёр релевантен
        task_id: ID задачи которая инициировала поиск
    
    Returns:
        bool: True если успешно сохранено
    """
    
    try:
        # Получаем Premium пользователя
        premium_user = session.query(User).filter_by(telegram_id=premium_user_id).first()
        if not premium_user:
            logger.warning(f"[PREMIUM_AUTO] Premium user {premium_user_id} not found")
            return False
        
        # Получаем профиль
        profile = session.query(UserProfile).filter_by(user_id=premium_user.id).first()
        if not profile:
            profile = UserProfile(user_id=premium_user.id)
            session.add(profile)
        
        # Парсим существующие уведомления
        existing = []
        if profile.pending_premium_recommendations:
            try:
                existing = json.loads(profile.pending_premium_recommendations)
                if not isinstance(existing, list):
                    existing = []
            except (json.JSONDecodeError, ValueError, TypeError):
                existing = []
        
        # Добавляем уведомление о найденном партнёре
        notification = {
            "type": "partner_found",
            "partner_username": partner_username,
            "partner_id": partner_telegram_id,
            "goal": matching_goal.get('goal', 'Unknown'),
            "opportunity": matching_goal.get('opportunity', ''),
            "match_reason": match_reason,
            "task_id": task_id,
            "timestamp": datetime.now(pytz.UTC).isoformat(),
            "shown_count": 0,
            "max_shows": 3,
            "priority": "medium"
        }
        
        # Добавляем (макс 10 уведомлений)
        existing.append(notification)
        existing = existing[-10:]
        
        profile.pending_premium_recommendations = json.dumps(existing, ensure_ascii=False)
        session.commit()
        
        logger.info(f"[PREMIUM_AUTO] Saved partner notification to Premium {premium_user_id}: {partner_username}")
        return True
        
    except Exception as e:
        logger.error(f"[PREMIUM_AUTO] Failed to save partner notification: {e}")
        session.rollback()
        return False


def save_partner_progress_notification(session: SessionType,
                                       premium_user_id: int,
                                       partner_username: str,
                                       partner_telegram_id: int,
                                       action_type: str,
                                       task_title: str,
                                       original_goal: Optional[str] = None) -> bool:
    """
    Сохраняет уведомление Premium о прогрессе партнёра
    
    Premium увидит: "Партнёр @username начал работу над X" или "завершил задачу X"
    
    Args:
        session: DB session
        premium_user_id: Telegram ID Premium пользователя
        partner_username: Username партнёра
        partner_telegram_id: Telegram ID партнёра
        action_type: 'started' или 'completed'
        task_title: Название задачи партнёра
        original_goal: Исходная цель Premium к которой это относится
    
    Returns:
        bool: True если успешно
    """
    
    try:
        premium_user = session.query(User).filter_by(telegram_id=premium_user_id).first()
        if not premium_user:
            return False
        
        profile = session.query(UserProfile).filter_by(user_id=premium_user.id).first()
        if not profile:
            profile = UserProfile(user_id=premium_user.id)
            session.add(profile)
        
        existing = []
        if profile.pending_premium_recommendations:
            try:
                existing = json.loads(profile.pending_premium_recommendations)
                if not isinstance(existing, list):
                    existing = []
            except (json.JSONDecodeError, ValueError, TypeError):
                existing = []
        
        notification = {
            "type": "partner_progress",
            "partner_username": partner_username,
            "partner_id": partner_telegram_id,
            "action": action_type,  # 'started' or 'completed'
            "task_title": task_title,
            "original_goal": original_goal,
            "timestamp": datetime.now(pytz.UTC).isoformat(),
            "shown_count": 0,
            "max_shows": 2,
            "priority": "high" if action_type == 'completed' else "medium"
        }
        
        existing.append(notification)
        existing = existing[-10:]
        
        profile.pending_premium_recommendations = json.dumps(existing, ensure_ascii=False)
        session.commit()
        
        logger.info(f"[PREMIUM_PROGRESS] Saved progress notification to Premium {premium_user_id}: {partner_username} {action_type}")
        return True
        
    except Exception as e:
        logger.error(f"[PREMIUM_PROGRESS] Failed to save progress notification: {e}")
        session.rollback()
        return False



    """Устаревшая функция - используйте manage_recommendations с action='get'"""
    return manage_recommendations(user_id, 'get', session=session)


def _check_deadlines_and_stuck_quick(user: User, session: SessionType) -> List[Dict[str, Any]]:
    """
    Быстрая проверка дедлайнов и застопоренных задач (без async, < 0.2 сек)
    
    Args:
        user: User объект
        session: DB session
    
    Returns:
        List[Dict]: Инсайты deadline_alert и stuck_task
    """
    
    insights = []
    
    try:
        # Получаем активные задачи
        active_tasks = session.query(Task).filter(
            and_(
                Task.user_id == user.id,
                Task.status.in_(['pending', 'in_progress'])
            )
        ).all()
        
        now = datetime.now(pytz.UTC)
        
        for task in active_tasks:
            # Проверяем дедлайны
            if task.due_date:
                deadline_dt = task.due_date
                if not deadline_dt.tzinfo:
                    deadline_dt = pytz.UTC.localize(deadline_dt)
                
                days_left = (deadline_dt - now).days
                
                # Дедлайн близко (3 дня, 1 день)
                if days_left in [1, 3]:
                    insights.append({
                        "type": "deadline_alert",
                        "task_id": task.id,
                        "task_title": task.description[:50] if task.description else task.title[:50],
                        "days_left": days_left,
                        "priority": "high" if days_left == 1 else "medium"
                    })
            
            # Проверяем застопоренные (по created_at, так как updated_at нет)
            if task.created_at:
                created_dt = task.created_at
                if not created_dt.tzinfo:
                    created_dt = pytz.UTC.localize(created_dt)
                
                days_since_created = (now - created_dt).days
                
                # Задача создана > 5 дней назад и статус всё ещё pending
                if days_since_created >= 5 and task.status == 'pending':
                    insights.append({
                        "type": "stuck_task",
                        "task_id": task.id,
                        "task_title": task.description[:50] if task.description else task.title[:50],
                        "days_stuck": days_since_created,
                        "priority": "high" if days_since_created >= 7 else "medium"
                    })
        
        return insights
        
    except Exception as e:
        logger.error(f"[PREMIUM_RT] Error in quick checks: {e}")
        return []


def _get_cached_heavy_insights(user_id: int, session: SessionType) -> List[Dict[str, Any]]:
    """
    Получает тяжёлые инсайты (market, reverse, trends) с кэшированием 10 минут
    
    Args:
        user_id: Telegram ID пользователя
        session: DB session
    
    Returns:
        List[Dict]: Инсайты из кэша или свежесобранные
    """
    
    global _INSIGHTS_CACHE, _INSIGHTS_CACHE_TTL_MINUTES
    
    now = datetime.now(pytz.UTC)
    
    # Проверяем кэш
    if user_id in _INSIGHTS_CACHE:
        cached_data = _INSIGHTS_CACHE[user_id]
        cache_time = cached_data.get('timestamp')
        
        if cache_time and (now - cache_time).total_seconds() < _INSIGHTS_CACHE_TTL_MINUTES * 60:
            logger.info(f"[PREMIUM_RT] Using cached heavy insights for {user_id}")
            return cached_data.get('insights', [])
    
    # Кэш устарел или нет — собираем заново
    logger.info(f"[PREMIUM_RT] Collecting fresh heavy insights for {user_id}")
    
    insights = []
    
    # Market opportunities (тяжёлый - анализ всех задач за неделю)
    try:
        import asyncio
        market = asyncio.create_task(find_market_opportunities(user_id, session))
        # Не ждём результат, но можем попробовать быстро
    except Exception as e:
        logger.warning(f"Failed to create market opportunities task: {e}")
    
    # Reverse matching (средний - поиск по недавним задачам)
    try:
        import asyncio  
        reverse = asyncio.create_task(find_reverse_matching(user_id, session))
    except Exception as e:
        logger.warning(f"Failed to create reverse matching task: {e}")
    
    # Пока не блокируем — в следующий раз будет в кэше
    # Для первого вызова возвращаем пустой список, кэшируем на будущее
    
    # Сохраняем в кэш (даже пустой)
    _INSIGHTS_CACHE[user_id] = {
        'insights': insights,
        'timestamp': now
    }
    
    return insights


def _format_insight_for_prompt(insight: Dict[str,Any]) -> str:
    """
    Форматирует инсайт для промпта в зависимости от типа
    
    Args:
        insight: Словарь с данными инсайта
    
    Returns:
        str: Форматированная строка для промпта
    """
    
    insight_type = insight.get('type', 'unknown')
    
    try:
        if insight_type == 'task_created':
            # Старый формат (уже работает)
            opportunity = insight.get('opportunity', insight.get('premium_goal', ''))
            match_reason = insight.get('match_reason', '')
            return f"- {opportunity} (релевантно: {match_reason})"
        
        elif insight_type == 'deadline_alert':
            task_title = insight.get('task_title', 'задача')
            days_left = insight.get('days_left', 0)
            day_word = "день" if days_left == 1 else "дня"
            return f"- До дедлайна '{task_title}' осталось {days_left} {day_word}"
        
        elif insight_type == 'stuck_task':
            task_title = insight.get('task_title', 'задача')
            days_stuck = insight.get('days_stuck', 0)
            return f"- Задача '{task_title}' без прогресса {days_stuck} дней — возможно нужна помощь?"
        
        elif insight_type == 'market_opportunity':
            insight_text = insight.get('insight', '')
            relevance = insight.get('relevance', '')
            return f"- {insight_text} ({relevance})"
        
        elif insight_type == 'reverse_matching':
            opp_user = insight.get('opportunity_user', 'кто-то')
            opp_task = insight.get('opportunity_task', 'ищет помощь')
            why_you = insight.get('why_you', '')
            return f"- @{opp_user}: '{opp_task}' — {why_you}"
        
        elif insight_type == 'trend':
            trend_text = insight.get('trend', '')
            relevance = insight.get('relevance', '')
            return f"- Тренд: {trend_text} ({relevance})"
        
        elif insight_type == 'partner_found':
            partner = insight.get('partner_username', 'пользователь')
            goal = insight.get('goal', 'твоя цель')
            match_reason = insight.get('match_reason', '')
            return f"- Найден партнёр @{partner} для '{goal}' — {match_reason}"
        
        elif insight_type == 'partner_progress':
            partner = insight.get('partner_username', 'партнёр')
            action = insight.get('action', 'обновил')
            task = insight.get('task_title', 'задачу')
            
            if action == 'started':
                return f"- @{partner} начал работу: '{task}'"
            elif action == 'completed':
                return f"- @{partner} завершил задачу: '{task}' ✓"
            else:
                return f"- @{partner} обновил: '{task}'"
        
        else:
            # Неизвестный тип - пробуем generic формат
            return f"- {insight.get('opportunity', insight.get('insight', ''))}"
    
    except Exception as e:
        logger.error(f"[PREMIUM_AUTO] Error formatting insight {insight_type}: {e}")
        return ""



def format_premium_report(report: Dict[str, Any]) -> str:
    """
    Форматирует отчёт для отправки Premium пользователю
    
    Args:
        report: Словарь с результатами автоматизации
    
    Returns:
        str: Форматированный отчёт
    """
    
    if report.get('error'):
        return f"⚠️ Premium Automation\n\nПроизошла ошибка: {report['error']}"
    
    sent = report.get('suggestions_sent', 0)
    
    if sent == 0:
        return """🤖 Premium Automation Report

За последние 12 часов релевантных людей не найдено.

💡 Совет: Уточни цели в профиле для лучших результатов."""
    
    output = [
        "🤖 Premium Automation Report",
        "",
        "📊 За последние 12 часов:",
        f"• Проанализировано целей: {report.get('goals_analyzed', 0)}",
        f"• Найдено релевантных людей: {report.get('relevant_users_found', 0)}",
        f"• Отправлено рекомендаций: {sent}",
        ""
    ]
    
    if report.get('sent_details'):
        output.append("👥 Рекомендации отправлены:")
        for detail in report['sent_details'][:3]:  # Показываем первые 3
            output.append(f"• @{detail['user']}")
            output.append(f"  Цель: {detail['goal'][:50]}...")
        
        if len(report['sent_details']) > 3:
            output.append(f"• ... и ещё {len(report['sent_details']) - 3}")
        
        output.append("")
    
    output.extend([
        "💡 Твои цели были представлены релевантным людям",
        "как полезные возможности. Жди откликов! 🚀"
    ])
    
    return "\n".join(output)


# Интеграция в reminder_service.py
async def schedule_premium_automation(premium_user_id: int):
    """
    Настраивает автоматизацию для Premium пользователя
    
    Вызывается при активации Premium подписки.
    Добавляет фоновый job который запускается каждые 12 часов.
    
    Args:
        premium_user_id: Telegram ID Premium пользователя
    """
    
    from reminder_service import REMINDER_SERVICE
    
    if not REMINDER_SERVICE or not REMINDER_SERVICE.scheduler:
        logger.error("[PREMIUM_AUTO] REMINDER_SERVICE not initialized")
        return False
    
    # Создаём job
    async def _premium_job():
        """Фоновая функция для Premium автоматизации"""
        try:
            report = await run_premium_automation(premium_user_id)
            
            # Отправляем отчёт если были действия
            if report.get('suggestions_sent', 0) > 0 and REMINDER_SERVICE.bot:
                formatted = format_premium_report(report)
                await REMINDER_SERVICE.bot.send_message(
                    chat_id=premium_user_id,
                    text=formatted
                )
        except Exception as e:
            logger.error(f"[PREMIUM_AUTO] Job error for {premium_user_id}: {e}")
    
    # Добавляем в scheduler
    REMINDER_SERVICE.scheduler.add_job(
        _premium_job,
        'interval',
        hours=12,
        id=f"premium_auto_{premium_user_id}",
        replace_existing=True
    )
    
    logger.info(f"[PREMIUM_AUTO] Scheduled automation for user {premium_user_id}")
    return True


async def unschedule_premium_automation(premium_user_id: int):
    """
    Отключает автоматизацию для пользователя
    
    Вызывается при отмене Premium подписки
    
    Args:
        premium_user_id: Telegram ID пользователя
    """
    
    from reminder_service import REMINDER_SERVICE
    
    if not REMINDER_SERVICE or not REMINDER_SERVICE.scheduler:
        return
    
    job_id = f"premium_auto_{premium_user_id}"
    
    try:
        REMINDER_SERVICE.scheduler.remove_job(job_id)
        logger.info(f"[PREMIUM_AUTO] Unscheduled automation for {premium_user_id}")
    except Exception as e:
        logger.warning(f"[PREMIUM_AUTO] Failed to unschedule: {e}")


# ============================================================================
# РАСШИРЕННЫЕ ИНСАЙТЫ ДЛЯ PREMIUM
# ============================================================================

async def check_deadlines_and_stuck_tasks(premium_user_id: int, session: Optional[SessionType] = None) -> List[Dict[str, Any]]:
    """
    Проверяет дедлайны и застопоренные задачи Premium пользователя
    
    Создаёт инсайты типа:
    - deadline_alert: задача с близким дедлайном
    - stuck_task: задача без прогресса N дней
    
    Args:
        premium_user_id: Telegram ID Premium пользователя
        session: DB session (опционально)
    
    Returns:
        List[Dict]: Список инсайтов для сохранения в профиль
    """
    
    close_session = False
    if session is None:
        session = Session()
        close_session = True
    
    insights = []
    
    try:
        # Получаем пользователя
        premium_user = session.query(User).filter_by(telegram_id=premium_user_id).first()
        if not premium_user:
            return insights
        
        # Получаем активные задачи
        active_tasks = session.query(Task).filter(
            and_(
                Task.user_id == premium_user.id,
                Task.status.in_(['pending', 'in_progress'])
            )
        ).all()
        
        now = datetime.now(pytz.UTC)
        
        for task in active_tasks:
            # Проверяем дедлайны
            if task.due_date:
                deadline_dt = task.due_date
                if not deadline_dt.tzinfo:
                    deadline_dt = pytz.UTC.localize(deadline_dt)
                
                days_left = (deadline_dt - now).days
                
                # Дедлайн близко (3 дня, 1 день)
                if days_left in [1, 3]:
                    insights.append({
                        "type": "deadline_alert",
                        "task_id": task.id,
                        "task_title": task.description[:50],
                        "days_left": days_left,
                        "deadline": deadline_dt.isoformat(),
                        "shown_count": 0,
                        "priority": "high" if days_left == 1 else "medium",
                        "created_at": now.isoformat()
                    })
            
            # Проверяем застопоренные (без created_at изменений, так как updated_at нет)
            if task.created_at:
                created_dt = task.created_at
                if not created_dt.tzinfo:
                    created_dt = pytz.UTC.localize(created_dt)
                
                days_stuck = (now - created_dt).days
                
                # Застопорилась > 3 дней (по created_at, так как updated_at нет)
                if days_stuck >= 3:
                    insights.append({
                        "type": "stuck_task",
                        "task_id": task.id,
                        "task_title": task.description[:50],
                        "days_stuck": days_stuck,
                        "shown_count": 0,
                        "priority": "high" if days_stuck >= 5 else "medium",
                        "created_at": now.isoformat()
                    })
        
        logger.info(f"[PREMIUM_INSIGHTS] Found {len(insights)} deadline/stuck insights for {premium_user_id}")
        return insights
        
    except Exception as e:
        logger.error(f"[PREMIUM_INSIGHTS] Error checking deadlines: {e}")
        return []
    finally:
        if close_session:
            session.close()


async def find_market_opportunities(premium_user_id: int, session: Optional[SessionType] = None) -> List[Dict[str, Any]]:
    """
    Анализирует активность сообщества и находит market opportunities
    
    Смотрит что задачи/интересы других пользователей → релевантность для Premium
    
    Args:
        premium_user_id: Telegram ID Premium пользователя
        session: DB session (опционально)
    
    Returns:
        List[Dict]: Список market opportunity инсайтов
    """
    
    close_session = False
    if session is None:
        session = Session()
        close_session = True
    
    insights = []
    
    try:
        # Получаем Premium пользователя и его профиль
        premium_user = session.query(User).filter_by(telegram_id=premium_user_id).first()
        if not premium_user:
            return insights
        
        premium_profile = session.query(UserProfile).filter_by(user_id=premium_user.id).first()
        if not premium_profile or not premium_profile.interests:
            return insights
        
        premium_interests = [i.strip().lower() for i in premium_profile.interests.split(',')]
        
        # Анализируем недавно созданные задачи других (последние 7 дней)
        week_ago = datetime.now(pytz.UTC) - timedelta(days=7)
        
        recent_tasks = session.query(Task, User).join(User, Task.user_id == User.id).filter(
            and_(
                Task.user_id != premium_user.id,
                Task.created_at >= week_ago
            )
        ).all()
        
        # Группируем по ключевым словам
        keyword_counts = {}
        for task, user in recent_tasks:
            desc_lower = task.description.lower()
            for interest in premium_interests:
                if interest in desc_lower:
                    if interest not in keyword_counts:
                        keyword_counts[interest] = []
                    keyword_counts[interest].append({
                        'task': task,
                        'user': user
                    })
        
        # Создаём инсайты для популярных направлений
        for keyword, tasks in keyword_counts.items():
            if len(tasks) >= 3:  # Минимум 3 упоминания
                insights.append({
                    "type": "market_opportunity",
                    "keyword": keyword,
                    "count": len(tasks),
                    "insight": f"{len(tasks)} человек искали '{keyword}' за неделю",
                    "relevance": f"это пересекается с твоим интересом '{keyword}'",
                    "shown_count": 0,
                    "priority": "medium",
                    "created_at": datetime.now(pytz.UTC).isoformat()
                })
        
        logger.info(f"[PREMIUM_INSIGHTS] Found {len(insights)} market opportunities for {premium_user_id}")
        return insights[:5]  # Топ-5
        
    except Exception as e:
        logger.error(f"[PREMIUM_INSIGHTS] Error finding market opportunities: {e}")
        return []
    finally:
        if close_session:
            session.close()


async def find_reverse_matching(premium_user_id: int, session: Optional[SessionType] = None) -> List[Dict[str, Any]]:
    """
    Reverse matching: кто может получить пользу от помощи Premium?
    
    Находит людей которые ищут то, в чём Premium эксперт
    
    Args:
        premium_user_id: Telegram ID Premium пользователя
        session: DB session (опционально)
    
    Returns:
        List[Dict]: Список reverse matching инсайтов
    """
    
    close_session = False
    if session is None:
        session = Session()
        close_session = True
    
    insights = []
    
    try:
        # Получаем Premium пользователя
        premium_user = session.query(User).filter_by(telegram_id=premium_user_id).first()
        if not premium_user:
            return insights
        
        premium_profile = session.query(UserProfile).filter_by(user_id=premium_user.id).first()
        if not premium_profile:
            return insights
        
        # Собираем экспертизу Premium (навыки + интересы)
        premium_expertise = []
        if premium_profile.skills:
            premium_expertise.extend([s.strip().lower() for s in premium_profile.skills.split(',')])
        if premium_profile.interests:
            premium_expertise.extend([i.strip().lower() for i in premium_profile.interests.split(',')])
        
        if not premium_expertise:
            return insights
        
        # Ищем недавние задачи других где нужна эта экспертиза
        week_ago = datetime.now(pytz.UTC) - timedelta(days=7)
        
        recent_tasks = session.query(Task, User).join(User, Task.user_id == User.id).filter(
            and_(
                Task.user_id != premium_user.id,
                Task.created_at >= week_ago,
                Task.status.in_(['pending', 'in_progress'])
            )
        ).all()
        
        for task, user in recent_tasks:
            desc_lower = task.description.lower()
            
            # Ищем совпадения с экспертизой Premium
            matches = [exp for exp in premium_expertise if exp in desc_lower]
            
            if matches:
                insights.append({
                    "type": "reverse_matching",
                    "opportunity_user": user.username or f"User_{user.telegram_id}",
                    "opportunity_task": task.description[:60],
                    "your_expertise": matches[0],
                    "why_you": f"ты эксперт в '{matches[0]}'",
                    "shown_count": 0,
                    "priority": "medium",
                    "created_at": datetime.now(pytz.UTC).isoformat()
                })
        
        logger.info(f"[PREMIUM_INSIGHTS] Found {len(insights)} reverse matching opportunities for {premium_user_id}")
        return insights[:3]  # Топ-3
        
    except Exception as e:
        logger.error(f"[PREMIUM_INSIGHTS] Error finding reverse matching: {e}")
        return []
    finally:
        if close_session:
            session.close()


async def analyze_community_trends(premium_user_id: int, session: Optional[SessionType] = None) -> List[Dict[str, Any]]:
    """
    Анализирует долгосрочные тренды в сообществе
    
    Смотрит на активность за последние 2 недели, находит растущие темы
    
    Args:
        premium_user_id: Telegram ID Premium пользователя
        session: DB session (опционально)
    
    Returns:
        List[Dict]: Список trend инсайтов
    """
    
    close_session = False
    if session is None:
        session = Session()
        close_session = True
    
    insights = []
    
    try:
        # Получаем Premium пользователя
        premium_user = session.query(User).filter_by(telegram_id=premium_user_id).first()
        if not premium_user:
            return insights
        
        premium_profile = session.query(UserProfile).filter_by(user_id=premium_user.id).first()
        if not premium_profile or not premium_profile.goals:
            return insights
        
        goals_lower = premium_profile.goals.lower()
        
        # Анализируем задачи за 2 недели
        two_weeks_ago = datetime.now(pytz.UTC) - timedelta(days=14)
        
        all_tasks = session.query(Task).filter(
            and_(
                Task.user_id != premium_user.id,
                Task.created_at >= two_weeks_ago
            )
        ).all()
        
        # Извлекаем ключевые слова из задач
        keyword_frequency = {}
        for task in all_tasks:
            words = task.description.lower().split()
            for word in words:
                if len(word) > 4:  # Пропускаем короткие слова
                    keyword_frequency[word] = keyword_frequency.get(word, 0) + 1
        
        # Находим топ-трендов релевантных для целей Premium
        relevant_trends = []
        for word, count in keyword_frequency.items():
            if count >= 5 and word in goals_lower:  # Минимум 5 упоминаний + релевантность
                relevant_trends.append({
                    'word': word,
                    'count': count
                })
        
        # Сортируем по популярности
        relevant_trends.sort(key=lambda x: x['count'], reverse=True)
        
        # Создаём инсайты
        for trend in relevant_trends[:3]:  # Топ-3 тренда
            insights.append({
                "type": "trend",
                "trend_keyword": trend['word'],
                "mention_count": trend['count'],
                "trend": f"{trend['count']} упоминаний '{trend['word']}' за 2 недели",
                "relevance": f"релевантно твоим целям",
                "shown_count": 0,
                "priority": "low",
                "created_at": datetime.now(pytz.UTC).isoformat()
            })
        
        logger.info(f"[PREMIUM_INSIGHTS] Found {len(insights)} trends for {premium_user_id}")
        return insights
        
    except Exception as e:
        logger.error(f"[PREMIUM_INSIGHTS] Error analyzing trends: {e}")
        return []
    finally:
        if close_session:
            session.close()


async def collect_premium_insights(user_id: int, mode: str = 'collect', session: SessionType = None) -> Union[Dict[str, Any], str]:
    """
    Универсальная функция сбора Premium инсайтов

    Args:
        user_id: Telegram ID пользователя
        mode: 'collect' - собрать и сохранить, 'prompt' - собрать и отформатировать для промпта
        session: DB session (опционально)

    Returns:
        Для mode='collect': Dict с отчётом о собранных инсайтах
        Для mode='prompt': str с форматированным текстом для промпта
    """

    close_session = False
    if session is None:
        session = Session()
        close_session = True

    try:
        # Получаем пользователя
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            return {"error": "User not found"} if mode == 'collect' else ""

        # Собираем инсайты (доступно всем пользователям, оплата токенами)
        all_insights = []

        # Быстрые проверки (всегда актуальные)
        try:
            quick_insights = await _check_deadlines_and_stuck_quick(user, session)
            all_insights.extend(quick_insights)
        except Exception as e:
            logger.warning(f"[PREMIUM_INSIGHTS] Error in quick checks: {e}")

        # Тяжёлые анализы (с кэшированием)
        try:
            cached_insights = await _get_cached_heavy_insights(user_id, session)
            all_insights.extend(cached_insights)
        except Exception as e:
            logger.warning(f"[PREMIUM_INSIGHTS] Error in cached insights: {e}")

        # Загружаем сохранённые инсайты
        try:
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            if profile and profile.pending_premium_recommendations:
                saved = json.loads(profile.pending_premium_recommendations)
                if isinstance(saved, list):
                    saved_insights = [
                        r for r in saved
                        if r.get('type') in ['task_created', 'partner_found', 'partner_progress']
                        and r.get('shown_count', 0) < r.get('max_shows', 3)
                    ]
                    all_insights.extend(saved_insights)
        except Exception as e:
            logger.warning(f"[PREMIUM_INSIGHTS] Error loading saved insights: {e}")

        if mode == 'collect':
            # Режим сбора - сохраняем новые инсайты
            heavy_insights = []
            try:
                # Собираем тяжёлые инсайты для сохранения
                deadlines_stuck = await check_deadlines_and_stuck_tasks(user_id, session)
                market_ops = await find_market_opportunities(user_id, session)
                reverse = await find_reverse_matching(user_id, session)
                trends = await analyze_community_trends(user_id, session)
                heavy_insights = deadlines_stuck + market_ops + reverse + trends
            except Exception as e:
                logger.warning(f"[PREMIUM_INSIGHTS] Error collecting heavy insights: {e}")

            # Сохраняем в профиль
            if heavy_insights:
                if profile:
                    existing = []
                    if profile.pending_premium_recommendations:
                        try:
                            existing = json.loads(profile.pending_premium_recommendations)
                        except (json.JSONDecodeError, ValueError, TypeError):
                            existing = []

                    # Добавляем новые (избегаем дубликатов)
                    existing_keys = {(r.get('type'), r.get('task_id', 0)) for r in existing}
                    for insight in heavy_insights:
                        key = (insight['type'], insight.get('task_id', 0))
                        if key not in existing_keys:
                            existing.append(insight)

                    # Сохраняем (макс 20 инсайтов)
                    profile.pending_premium_recommendations = json.dumps(existing[-20:], ensure_ascii=False)
                    session.commit()

            return {
                "user_id": user_id,
                "insights_collected": len(heavy_insights),
                "breakdown": {
                    "deadlines_stuck": len(deadlines_stuck) if 'deadlines_stuck' in locals() else 0,
                    "market_opportunities": len(market_ops) if 'market_ops' in locals() else 0,
                    "reverse_matching": len(reverse) if 'reverse' in locals() else 0,
                    "trends": len(trends) if 'trends' in locals() else 0
                },
                "timestamp": datetime.now(pytz.UTC).isoformat()
            }

        elif mode == 'prompt':
            # Режим промпта - форматируем для AI
            if not all_insights:
                return ""

            # Группируем по приоритетам
            high_priority = []
            medium_priority = []
            low_priority = []

            for insight in all_insights:
                priority = insight.get('priority', 'medium')
                formatted = _format_insight_for_prompt(insight)
                if formatted:
                    if priority == 'high':
                        high_priority.append(formatted)
                    elif priority == 'low':
                        low_priority.append(formatted)
                    else:
                        medium_priority.append(formatted)

            # Формируем промпт секцию
            sections = []

            if high_priority:
                sections.append("🔴 СРОЧНО:")
                sections.extend(high_priority[:2])

            if medium_priority:
                sections.append("\n🟡 ВОЗМОЖНОСТИ:")
                sections.extend(medium_priority[:2])

            if low_priority:
                sections.append("\n🟢 ТРЕНДЫ:")
                sections.extend(low_priority[:1])

            if sections:
                prompt_section = "\n\n" + "="*50
                prompt_section += "\nПРЕМИУМ ИНСАЙТЫ (упомяни ЕСТЕСТВЕННО в зависимости от контекста диалога):\n\n"
                prompt_section += "\n".join(sections)
                prompt_section += "\n\nВАЖНО:"
                prompt_section += "\n- Упоминай ТОЛЬКО если релевантно текущей теме разговора"
                prompt_section += "\n- Вплетай естественно, как свою идею (не говори 'увидел в системе')"
                prompt_section += "\n- Дружелюбный тон, короткое упоминание (1-2 предложения)"
                prompt_section += "\n- Если не подходит момент - лучше пропусти"
                prompt_section += "\n- Приоритет: срочное → возможности → тренды"
                prompt_section += "\n" + "="*50

                return prompt_section

            return ""

    except Exception as e:
        logger.error(f"[PREMIUM_INSIGHTS] Error in collect_premium_insights: {e}")
        return {"error": str(e)} if mode == 'collect' else ""
    finally:
        if close_session:
            session.close()






