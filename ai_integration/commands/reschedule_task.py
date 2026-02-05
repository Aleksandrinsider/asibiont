from .base_command import BaseCommand
from .. import handlers
from ..parsers import extract_task_details
from ..responses import generate_response

class RescheduleTaskCommand(BaseCommand):
    async def execute(self, user, db_session):`n        user_id = user.telegram_id
        # Extract task title and new time from message
        message_lower = self.message.lower()

        # Simple parsing for reschedule commands
        import re

        # Patterns for extracting task title and new time
        # "перенеси задачу про молоко на завтра в 10:00"
        # "измени время зарядки на 9:00"

        task_title = None
        new_time = None

        # Try to extract task title (words after "задачу про", "задачи", etc.)
        title_patterns = [
            r'задачу\s+(?:про\s+)?([^\s]+(?:\s+[^\s]+)*?)\s+на',
            r'задачи\s+([^\s]+(?:\s+[^\s]+)*?)\s+на',
            r'задач[уи]\s+([^\s]+(?:\s+[^\s]+)*?)\s+на'
        ]

        for pattern in title_patterns:
            match = re.search(pattern, message_lower)
            if match:
                task_title = match.group(1).strip()
                break

        # Try to extract new time (words after "на")
        time_patterns = [
            r'на\s+([^\s]+(?:\s+[^\s]+)*?)(?:\s|$)',
        ]

        for pattern in time_patterns:
            match = re.search(pattern, message_lower)
            if match:
                new_time = match.group(1).strip()
                break

        if not task_title or not new_time:
            # Fallback to AI extraction
            try:
                from ..tools import TOOLS
                reschedule_tool = next((t for t in TOOLS if t['function']['name'] == 'reschedule_task'), None)
                if reschedule_tool and self.params:
                    task_title = self.params.get('task_title')
                    new_time = self.params.get('new_time')
            except:
                pass

        if not task_title or not new_time:
            return "Не удалось распознать задачу или новое время. Попробуйте: 'перенеси задачу про молоко на завтра в 10:00'"

        # Call the handler
        result = await handlers.reschedule_task(
            task_title=task_title,
            new_time=new_time,
            user_id=user_id,
            session=db_session
        )

        # Generate response
        return await generate_response('task_rescheduled', message=result)
