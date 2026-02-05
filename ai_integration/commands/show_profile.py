from .base_command import BaseCommand
from .. import handlers
from ..responses import generate_response

class ShowProfileCommand(BaseCommand):
    async def execute(self, user, db_session):
        user_id = user.telegram_id

        # Получаем информацию о профиле
        profile_info = handlers.show_profile(user_id, session=db_session, close_session=False)

        # Генерируем ответ с помощью AI
        response = await generate_response(
            f"Пользователь запросил показать свой профиль. Вот информация из профиля:\n\n{profile_info}\n\nСделай краткий, дружелюбный комментарий к этой информации.",
            user_id,
            db_session,
            context_type="profile_view"
        )

        return response