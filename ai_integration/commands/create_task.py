from .base_command import BaseCommand
from .. import handlers
from ..parsers import extract_task_details
from ..responses import generate_response

class CreateTaskCommand(BaseCommand):
    async def execute(self, user_id, db_session):
        # Use AI-extracted parameters if available, otherwise extract from message
        if self.params.get('title'):
            title = self.params['title']
            reminder_time = self.params.get('reminder_time')
            description = self.params.get('description', '')
        else:
            # Fallback to old extraction method
            details = await extract_task_details(self.message, user_id)
            title = details['title']
            reminder_time = details['reminder_time']
            description = details.get('description', '')

        # Direct handler call
        result = handlers.add_task(
            title=title,
            description=description,
            reminder_time=reminder_time,
            user_id=user_id,
            session=db_session
        )

        # Generate response
        return await generate_response('task_created', message=result)