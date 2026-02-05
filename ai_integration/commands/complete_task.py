from .base_command import BaseCommand
from .. import handlers

class CompleteTaskCommand(BaseCommand):
    async def execute(self, user, db_session):
        user_id = user.telegram_id
        # Extract task title from message more intelligently
        task_title = self.params.get('task_title')
        
        # If no task_title from AI, try to extract from message
        if not task_title:
            message_lower = self.message.lower()
            # Remove completion keywords
            completion_words = ['готово', 'сделал', 'сделала', 'выполнил', 'выполнила', 'завершил', 'завершила', 'закончил', 'закончила', 'готов', 'готова', 'закрыл', 'закрыла', 'уже сделал', 'уже выполнил', 'уже завершил']
            for word in completion_words:
                message_lower = message_lower.replace(word, '').strip()
            
            # Use the cleaned message as task title
            task_title = message_lower.strip()
        
        # If still no task_title, use the whole message
        if not task_title:
            task_title = self.message

        # Call handler
        result = await handlers.complete_task(
            task_title=task_title,
            user_id=user_id,
            session=db_session
        )

        return result

