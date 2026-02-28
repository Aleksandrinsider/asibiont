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
        
        # analyze_context_with_ai / find_relevant_users_for_goals были удалены
        # Возвращаем пустой результат без ошибки
        return {
            "premium_user": premium_user.username,
            "trigger": "realtime",
            "task_id": task_id,
            "items_analyzed": 0,
            "relevant_users_found": 0,
            "recommendations_saved": 0,
            "status": "skipped_no_analyzer"
        }
        
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






