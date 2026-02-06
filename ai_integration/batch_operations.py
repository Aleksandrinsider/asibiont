"""
Batch Operations Agent - массовые операции с задачами через гибридную архитектуру

Примеры использования:
- "Удали все завершённые задачи"
- "Перенеси все задачи с 'работа' на следующую неделю"
- "Отметь как выполненные все задачи старше 30 дней"
- "Делегируй все задачи проекта X пользователю @vasya"
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import pytz
from models import Session, User, Task
from .autonomous_agent import HybridAutonomousAgent

logger = logging.getLogger(__name__)


class BatchOperationsAgent(HybridAutonomousAgent):
    """
    Специализированный агент для массовых операций с задачами
    
    Поддерживаемые операции:
    - delete_all: Удаление задач по фильтру
    - complete_all: Завершение задач по фильтру
    - reschedule_all: Перенос задач по фильтру
    - delegate_all: Делегирование задач по фильтру
    """
    
    def __init__(self):
        super().__init__()
        self.specialization = "batch_operations"
        logger.info("[BATCH_AGENT] Initialized BatchOperationsAgent")
    
    async def plan_batch_operation(self, user_message: str, user_id: int) -> Dict[str, Any]:
        """
        ШАГ 1: AI планирует batch операцию
        
        Определяет:
        - Тип операции (delete/complete/reschedule/delegate)
        - Фильтры (status, keywords, date_range)
        - Нужно ли подтверждение
        
        Returns:
            dict: План операции с actions
        """
        
        # Получаем информацию о пользователе
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return {
                    "intent": "error",
                    "actions": [],
                    "response_strategy": "user_not_found"
                }
            
            # Запрашиваем у AI понимание batch команды
            system_prompt = f"""Ты агент для массовых операций с задачами.

КОНТЕКСТ:
Сейчас {datetime.now(pytz.UTC).strftime('%Y-%m-%d %H:%M')}
Пользователь: {user.username or 'пользователь'}

ЗАДАЧА: Распознай массовую операцию в сообщении пользователя.

ТИПЫ ОПЕРАЦИЙ:
1. delete_all - удаление задач
2. complete_all - отметить задачи выполненными
3. reschedule_all - перенести задачи на другое время
4. delegate_all - делегировать задачи другому пользователю

ФИЛЬТРЫ (извлеки из сообщения):
- status: pending, active, completed, overdue
- keywords: список ключевых слов для поиска в названии
- date_range: older_than_days, newer_than_days, specific_date
- priority: high, medium, low

БЕЗОПАСНОСТЬ:
- Для delete_all ВСЕГДА требуй подтверждения
- Для других операций - подтверждение если задач >5

Верни JSON:
{{
    "operation": "delete_all|complete_all|reschedule_all|delegate_all",
    "filters": {{
        "status": "...",
        "keywords": [...],
        "date_range": {{"older_than_days": 30}},
        "priority": "..."
    }},
    "params": {{"new_time": "...", "delegate_to": "..."}},
    "requires_confirmation": true/false,
    "reason": "объяснение почему выбрана эта операция"
}}"""

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ]
            
            # Вызываем AI для планирования
            response = await self.call_ai(messages, use_tools=False, temperature=0.3)
            content = response['choices'][0]['message']['content']
            
            # Парсим JSON из ответа
            import json
            try:
                # Извлекаем JSON из ответа (может быть обёрнут в markdown)
                if '```json' in content:
                    content = content.split('```json')[1].split('```')[0].strip()
                elif '```' in content:
                    content = content.split('```')[1].split('```')[0].strip()
                
                plan = json.loads(content)
                logger.info(f"[BATCH_AGENT] Parsed plan: {plan}")
                
                # Формируем action для execute_batch
                return {
                    "intent": "batch_operation",
                    "actions": [{
                        "tool": "execute_batch",
                        "params": {
                            "operation": plan.get('operation'),
                            "filters": plan.get('filters', {}),
                            "additional_params": plan.get('params', {}),
                            "user_id": user_id
                        },
                        "reason": plan.get('reason', 'Batch operation request'),
                        "requires_confirmation": plan.get('requires_confirmation', True)
                    }],
                    "response_strategy": "batch_result"
                }
                
            except json.JSONDecodeError as e:
                logger.error(f"[BATCH_AGENT] Failed to parse AI response as JSON: {e}")
                return {
                    "intent": "error",
                    "actions": [],
                    "response_strategy": "parse_error"
                }
        
        finally:
            session.close()
    
    async def execute_batch(self, operation: str, filters: Dict, additional_params: Dict, 
                           user_id: int, confirmed: bool = False) -> Dict[str, Any]:
        """
        ШАГ 2: Выполняет batch операцию
        
        Args:
            operation: Тип операции (delete_all, complete_all, etc)
            filters: Фильтры для поиска задач
            additional_params: Дополнительные параметры (new_time, delegate_to)
            user_id: ID пользователя
            confirmed: Подтверждена ли операция
        
        Returns:
            dict: Результат с статистикой
        """
        
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return {
                    "success": False,
                    "error": "Пользователь не найден"
                }
            
            # Находим задачи по фильтрам
            matching_tasks = self._find_tasks_by_filters(session, user, filters)
            
            logger.info(f"[BATCH_AGENT] Found {len(matching_tasks)} matching tasks for {operation}")
            
            # Если операция требует подтверждения и его нет - возвращаем для confirm
            if not confirmed and len(matching_tasks) > 0:
                return {
                    "success": False,
                    "requires_confirmation": True,
                    "task_count": len(matching_tasks),
                    "operation": operation,
                    "preview_tasks": [
                        {"id": t.id, "title": t.title, "status": t.status}
                        for t in matching_tasks[:5]  # Показываем первые 5
                    ]
                }
            
            # Выполняем операцию
            results = {
                "success": True,
                "operation": operation,
                "processed": 0,
                "successful": 0,
                "failed": 0,
                "errors": []
            }
            
            # Импортируем handlers
            from . import handlers
            
            for task in matching_tasks:
                results['processed'] += 1
                
                try:
                    if operation == 'delete_all':
                        await handlers.delete_task(
                            task_id=task.id,
                            user_id=user_id,
                            session=session,
                            close_session=False
                        )
                    
                    elif operation == 'complete_all':
                        await handlers.complete_task(
                            task_id=task.id,
                            user_id=user_id,
                            session=session,
                            close_session=False
                        )
                    
                    elif operation == 'reschedule_all':
                        new_time = additional_params.get('new_time', 'tomorrow')
                        await handlers.reschedule_task(
                            task_id=task.id,
                            new_time=new_time,
                            user_id=user_id,
                            session=session,
                            close_session=False
                        )
                    
                    elif operation == 'delegate_all':
                        delegate_to = additional_params.get('delegate_to')
                        if delegate_to:
                            await handlers.delegate_task(
                                task_id=task.id,
                                delegate_to=delegate_to,
                                user_id=user_id,
                                session=session,
                                close_session=False
                            )
                    
                    results['successful'] += 1
                    
                except Exception as e:
                    results['failed'] += 1
                    results['errors'].append({
                        "task_id": task.id,
                        "task_title": task.title,
                        "error": str(e)
                    })
                    logger.error(f"[BATCH_AGENT] Error processing task {task.id}: {e}")
            
            # Коммитим все изменения
            session.commit()
            
            return results
        
        except Exception as e:
            session.rollback()
            logger.error(f"[BATCH_AGENT] Fatal error in execute_batch: {e}")
            return {
                "success": False,
                "error": str(e)
            }
        
        finally:
            session.close()
    
    def _find_tasks_by_filters(self, session, user: User, filters: Dict) -> List[Task]:
        """
        Находит задачи по фильтрам
        
        Args:
            session: DB session
            user: Объект пользователя
            filters: Словарь фильтров
        
        Returns:
            List[Task]: Список подходящих задач
        """
        
        query = session.query(Task).filter_by(user_id=user.id)
        
        # Фильтр по статусу
        if 'status' in filters:
            status = filters['status']
            if status == 'overdue':
                # Просроченные задачи
                query = query.filter(
                    Task.status.in_(['pending', 'active']),
                    Task.due_date < datetime.now(pytz.UTC)
                )
            elif status:
                query = query.filter(Task.status == status)
        
        # Фильтр по ключевым словам
        if 'keywords' in filters and filters['keywords']:
            from sqlalchemy import or_
            keyword_filters = []
            for keyword in filters['keywords']:
                keyword_filters.append(Task.title.ilike(f'%{keyword}%'))
            query = query.filter(or_(*keyword_filters))
        
        # Фильтр по дате
        if 'date_range' in filters:
            date_filter = filters['date_range']
            
            if 'older_than_days' in date_filter:
                days = date_filter['older_than_days']
                cutoff_date = datetime.now(pytz.UTC) - timedelta(days=days)
                query = query.filter(Task.created_at < cutoff_date)
            
            elif 'newer_than_days' in date_filter:
                days = date_filter['newer_than_days']
                cutoff_date = datetime.now(pytz.UTC) - timedelta(days=days)
                query = query.filter(Task.created_at > cutoff_date)
        
        # Фильтр по приоритету (если поле существует)
        if 'priority' in filters and hasattr(Task, 'priority'):
            query = query.filter(Task.priority == filters['priority'])
        
        tasks = query.all()
        logger.info(f"[BATCH_AGENT] Filter query found {len(tasks)} tasks")
        
        return tasks
    
    async def format_batch_result(self, result: Dict, user_message: str, user_id: int) -> str:
        """
        ШАГ 3: Форматирует результат batch операции через AI
        
        Args:
            result: Результат выполнения
            user_message: Исходное сообщение пользователя
            user_id: ID пользователя
        
        Returns:
            str: Форматированный ответ для пользователя
        """
        
        # Если требуется подтверждение
        if result.get('requires_confirmation'):
            task_count = result['task_count']
            operation_names = {
                'delete_all': 'удаление',
                'complete_all': 'завершение',
                'reschedule_all': 'перенос',
                'delegate_all': 'делегирование'
            }
            operation_name = operation_names.get(result['operation'], result['operation'])
            
            preview = "\n".join([
                f"  {i+1}. {t['title']} ({t['status']})"
                for i, t in enumerate(result['preview_tasks'])
            ])
            
            return f"""⚠️ Подтверди массовую операцию

📊 Операция: {operation_name}
📝 Найдено задач: {task_count}

Примеры задач:
{preview}
{"..." if task_count > 5 else ""}

Выполнить операцию? (Да/Нет)"""
        
        # Форматируем результат через AI для естественности
        if result.get('success'):
            system_prompt = f"""Сформируй дружелюбный ответ о результатах массовой операции.

РЕЗУЛЬТАТЫ:
- Обработано: {result['processed']}
- Успешно: {result['successful']}  
- Ошибки: {result['failed']}

ПРАВИЛА:
- Говори естественно, от первого лица
- Используй эмодзи для статистики
- Если были ошибки - кратко упомяни
- Завершай позитивно

Верни ТОЛЬКО текст ответа."""

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Запрос был: {user_message}"}
            ]
            
            response = await self.call_ai(messages, use_tools=False, temperature=0.7)
            content = response['choices'][0]['message']['content']
            
            return content.strip()
        
        else:
            return f"❌ Ошибка при выполнении операции: {result.get('error', 'Неизвестная ошибка')}"


# Интеграция в основной chat flow
async def handle_batch_operation(message: str, user_id: int) -> Dict[str, Any]:
    """
    Обработчик batch операций
    
    Используется в chat.py для маршрутизации batch команд
    """
    
    agent = BatchOperationsAgent()
    
    # 1. Планируем операцию
    plan = await agent.plan_batch_operation(message, user_id)
    
    if plan['intent'] == 'error':
        return {'response': "Не удалось распознать команду массовой операции"}
    
    # 2. Выполняем
    actions = plan.get('actions', [])
    if not actions:
        return {'response': "Нет действий для выполнения"}
    
    action = actions[0]
    params = action['params']
    
    result = await agent.execute_batch(
        operation=params['operation'],
        filters=params['filters'],
        additional_params=params.get('additional_params', {}),
        user_id=user_id,
        confirmed=False  # При первом вызове всегда без подтверждения
    )
    
    # 3. Форматируем ответ
    response = await agent.format_batch_result(result, message, user_id)
    
    return {
        'response': response,
        'batch_result': result
    }


def is_batch_operation(message: str) -> bool:
    """
    Проверяет, является ли сообщение batch командой
    
    Args:
        message: Текст сообщения
    
    Returns:
        bool: True если это batch команда
    """
    
    message_lower = message.lower()
    
    # Ключевые слова для batch операций
    batch_keywords = [
        'все задач', 'всех задач',
        'все дела', 'всех дел',
        'массово', 'пакетн',
        'завершённ', 'выполненн',
        'старше', 'новее',
        'с текстом', 'со словом',
        'проект', 'категори'
    ]
    
    # Операции
    operations = [
        'удал', 'удалить',
        'завершi', 'завершить', 'отметь',
        'перенес', 'перенести',
        'делегир'
    ]
    
    # Должно быть: операция + индикатор множественности
    has_operation = any(op in message_lower for op in operations)
    has_batch_keyword = any(kw in message_lower for kw in batch_keywords)
    
    return has_operation and has_batch_keyword
