from .base_command import BaseCommand
from .. import handlers

class RejectDelegatedTaskCommand(BaseCommand):
    async def execute(self, user, db_session):`n        user_id = user.telegram_id
        # Extract task_id from params
        task_id = self.params.get('task_id')
        
        if not task_id:
            # Try to extract from message
            import re
            task_id_match = re.search(r'\b(\d+)\b', self.message)
            if task_id_match:
                task_id = int(task_id_match.group(1))
        
        if not task_id:
            return "Не удалось определить ID задачи для отклонения."
        
        # Call handler
        result = handlers.reject_delegated_task(task_id, user_id=user_id)
        
        return result
