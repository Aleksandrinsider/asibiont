from .base_command import BaseCommand
from .. import handlers
from ..parsers import extract_task_details
from ..responses import generate_response

class CreateTaskCommand(BaseCommand):
    async def execute(self, user, db_session):
        user_id = user.telegram_id
        # Use AI-extracted parameters if available and valid, otherwise extract from message
        title = self.params.get('title')
        reminder_time = self.params.get('reminder_time')
        description = self.params.get('description', '')

        # Check if AI parameters are valid (not test/default values)
        ai_params_valid = (
            title and
            title.strip() and
            len(title.strip()) > 2 and
            title.lower() not in ['тестовая задача', 'test task', 'test'] and
            not title.startswith('Тест')
        )

        if not ai_params_valid:
            # Fallback to local extraction method
            print(f"[CREATE_TASK] AI params invalid ('{title}'), using local parser for: {self.message}")
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

