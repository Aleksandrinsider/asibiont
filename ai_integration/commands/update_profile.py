from .base_command import BaseCommand
from .. import handlers
from ..responses import generate_response

class UpdateProfileCommand(BaseCommand):
    async def execute(self, user_id, db_session):
        # Extract profile information from message
        message_lower = self.message.lower()

        # Simple parsing for profile updates
        profile_data = {}

        # City
        if 'москв' in message_lower or 'москва' in message_lower:
            profile_data['city'] = 'Москва'
        elif 'петербург' in message_lower or 'спб' in message_lower:
            profile_data['city'] = 'Санкт-Петербург'
        elif 'екатеринбург' in message_lower or 'екб' in message_lower:
            profile_data['city'] = 'Екатеринбург'
        elif 'новосибирск' in message_lower or 'нск' in message_lower:
            profile_data['city'] = 'Новосибирск'

        # Company/Work
        company_keywords = ['работаю в', 'компания', 'фирма', 'работа']
        for keyword in company_keywords:
            if keyword in message_lower:
                # Extract company name after keyword
                idx = message_lower.find(keyword)
                if idx >= 0:
                    company_part = message_lower[idx + len(keyword):].strip()
                    # Take first meaningful words
                    words = company_part.split()[:3]  # Max 3 words
                    if words:
                        profile_data['company'] = ' '.join(words).title()
                    break

        # Position
        position_keywords = ['должность', 'позиция', 'работаю', 'занимаюсь', 'я']
        for keyword in position_keywords:
            if keyword in message_lower and ('программист' in message_lower or 'разработчик' in message_lower or 'дизайнер' in message_lower):
                # Extract position after keyword
                idx = message_lower.find(keyword)
                if idx >= 0:
                    position_part = message_lower[idx + len(keyword):].strip()
                    # Look for position indicators
                    pos_indicators = ['программист', 'разработчик', 'дизайнер', 'менеджер', 'аналитик', 'тестировщик']
                    for indicator in pos_indicators:
                        if indicator in position_part:
                            profile_data['position'] = indicator.title()
                            break
                    break
                    break

        # Interests
        interest_keywords = ['люблю', 'интересует', 'увлекаюсь', 'хобби']
        for keyword in interest_keywords:
            if keyword in message_lower:
                idx = message_lower.find(keyword)
                if idx >= 0:
                    interest_part = message_lower[idx + len(keyword):].strip()
                    words = interest_part.split()[:4]  # Max 4 words
                    if words:
                        profile_data['interests'] = ' '.join(words)
                    break

        # Skills
        skill_keywords = ['умею', 'знаю', 'специалист', 'опыт в']
        for keyword in skill_keywords:
            if keyword in message_lower:
                idx = message_lower.find(keyword)
                if idx >= 0:
                    skill_part = message_lower[idx + len(keyword):].strip()
                    words = skill_part.split()[:4]  # Max 4 words
                    if words:
                        profile_data['skills'] = ' '.join(words)
                    break

        # Goals
        goal_keywords = ['хочу', 'планирую', 'мечтаю', 'цель']
        for keyword in goal_keywords:
            if keyword in message_lower:
                idx = message_lower.find(keyword)
                if idx >= 0:
                    goal_part = message_lower[idx + len(keyword):].strip()
                    words = goal_part.split()[:6]  # Max 6 words
                    if words:
                        profile_data['goals'] = ' '.join(words)
                    break

        if not profile_data:
            # Fallback to AI extraction
            try:
                if self.params:
                    profile_data = {k: v for k, v in self.params.items() if v}
            except:
                pass

        if not profile_data:
            return "Не удалось распознать информацию для обновления профиля. Попробуйте: 'я из Москвы, работаю в IT, люблю программирование'"

        # Call the handler
        result = handlers.update_profile(
            user_id=user_id,
            city=profile_data.get('city'),
            interests=profile_data.get('interests'),
            skills=profile_data.get('skills'),
            goals=profile_data.get('goals'),
            company=profile_data.get('company'),
            position=profile_data.get('position'),
            session=db_session
        )

        # Generate response
        return await generate_response('profile_updated', message=result)