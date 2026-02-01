from .base_command import BaseCommand
from .. import handlers

class GetTaskDetailsCommand(BaseCommand):
    async def execute(self, user_id, db_session):
        # Extract task title from message
        task_title = self.params.get('task_title', self.message)
        
        # For messages like "Покажи детали задачи про продукты", extract "продукты"
        if "про " in task_title:
            task_title = task_title.split("про ", 1)[1].strip()
        elif "задачи " in task_title:
            # Handle "Покажи детали задачи 'название'"
            parts = task_title.split("задачи ", 1)
            if len(parts) > 1:
                task_title = parts[1].strip().strip("'\"")
        
        # Call handler
        result = handlers.get_task_details(
            task_title=task_title,
            user_id=user_id,
            session=db_session
        )

        return result