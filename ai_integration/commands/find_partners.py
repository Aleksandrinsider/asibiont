from .base_command import BaseCommand
from .. import handlers
from ..responses import generate_response

class FindPartnersCommand(BaseCommand):
    async def execute(self, user_id, db_session):
        # For find_partners, we don't need complex parsing
        # The handler will use user's profile to find matches

        # Call the handler
        result = handlers.find_partners(
            user_id=user_id,
            session=db_session
        )

        # Generate response
        return await generate_response('partners_found', message=result)