from .base_command import BaseCommand
from .. import handlers

class EditTaskCommand(BaseCommand):
    async def execute(self, user, db_session):`n        user_id = user.telegram_id
        # Extract task title and new values from message
        # This is a complex command that needs parsing
        # For now, use the message as task_title and assume basic editing
        task_title = self.params.get('task_title', self.message)
        
        # Try to extract description from message if not provided by AI
        description = self.params.get('description')
        if not description:
            message_lower = self.message.lower()
            # Look for "добавь описание" pattern
            if 'добавь описание' in message_lower:
                desc_start = message_lower.find('добавь описание')
                if desc_start >= 0:
                    desc_part = self.message[desc_start + len('добавь описание'):].strip()
                    # Remove quotes if present
                    if desc_part.startswith("'") and desc_part.endswith("'"):
                        desc_part = desc_part[1:-1]
                    elif desc_part.startswith('"') and desc_part.endswith('"'):
                        desc_part = desc_part[1:-1]
                    description = desc_part
        
        # Call handler with basic parameters
        result = handlers.edit_task(
            task_title=task_title,
            description=description,
            user_id=user_id,
            session=db_session
        )

        return result
