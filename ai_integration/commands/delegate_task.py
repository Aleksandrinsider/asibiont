from .base_command import BaseCommand
from .. import handlers
from ..responses import generate_response

class DelegateTaskCommand(BaseCommand):
    async def execute(self, user, db_session):
        user_id = user.telegram_id
        # Парсинг команды делегирования
        message_lower = self.message.lower()

        # Извлечение информации о задаче и исполнителе
        task_title = None
        executor_username = None
        deadline = None

        # Найти упоминание пользователя (@username)
        import re
        username_match = re.search(r'@([a-zA-Z0-9_]+)', self.message)
        if username_match:
            executor_username = username_match.group(1)

        # Извлечь название задачи (все до @username)
        if executor_username:
            task_part = self.message.split(f'@{executor_username}')[0].strip()
            # Убрать слова-триггеры
            triggers = ['делегируй', 'поручи', 'передай', 'отправь']
            for trigger in triggers:
                if task_part.lower().startswith(trigger):
                    task_title = task_part[len(trigger):].strip()
                    break
            if not task_title:
                task_title = task_part
        else:
            # Попытаться найти задачу в существующем списке
            user_tasks = handlers.list_tasks(include_completed=False, user_id=user_id, session=db_session)
            # Это будет обработано AI если не найдено

        # Извлечь дедлайн
        deadline_keywords = ['дедлайн', 'срок', 'к', 'до']
        for keyword in deadline_keywords:
            if keyword in message_lower:
                deadline_part = message_lower.split(keyword)[1].strip()
                # Попробовать распарсить время
                try:
                    from ..time_parser import parse_time
                    from models import User
                    user = db_session.query(User).filter_by(id=user_id).first()
                    user_tz = user.timezone if user and user.timezone else 'UTC'
                    parsed_time = parse_time(deadline_part, user_tz)
                    if parsed_time:
                        deadline = parsed_time
                        break
                except:
                    pass

        if not task_title or not executor_username:
            # Fallback to AI extraction
            try:
                if self.params:
                    task_title = self.params.get('task_title', task_title)
                    executor_username = self.params.get('executor_username', executor_username)
                    deadline_str = self.params.get('deadline')
                    if deadline_str and not deadline:
                        from ..time_parser import parse_time
                        from models import User
                        user = db_session.query(User).filter_by(id=user_id).first()
                        user_tz = user.timezone if user and user.timezone else 'UTC'
                        deadline = parse_time(deadline_str, user_tz)
            except:
                pass

        if not task_title or not executor_username:
            return "Не удалось распознать задачу или исполнителя. Пример: 'Делегируй задачу подготовить отчет @username с дедлайном завтра в 15:00'"

        # Проверить существование пользователя-исполнителя
        from models import User
        executor = db_session.query(User).filter(User.username.ilike(executor_username)).first()
        if not executor:
            return f"Пользователь @{executor_username} не найден в системе"

        # Создать задачу с делегированием
        result = handlers.delegate_task(
            task_title=task_title,
            executor_username=executor_username,
            deadline=deadline,
            description=f"Делегировано @{executor_username}",
            delegator_id=user_id,
            session=db_session
        )

        if "SUCCESS" in result:
            response_msg = f"✅ Задача '{task_title}' делегирована @{executor_username}\n"
            response_msg += f"🤖 Агент будет контролировать выполнение и сообщать о прогрессе\n"
            if deadline:
                response_msg += f"⏰ Дедлайн: {deadline.strftime('%d.%m.%Y %H:%M')}"
        else:
            response_msg = result

        return await generate_response('task_delegated', message=response_msg)

