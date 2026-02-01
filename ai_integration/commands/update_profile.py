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

        # Interests - расширенные паттерны
        interest_keywords = ['люблю', 'интересует', 'увлекаюсь', 'хобби', 'интересуюсь', 'нравится', 'занимаюсь']
        for keyword in interest_keywords:
            if keyword in message_lower:
                idx = message_lower.find(keyword)
                if idx >= 0:
                    interest_part = message_lower[idx + len(keyword):].strip()
                    # Убираем лишние слова
                    stop_words = ['что', 'и', 'а', 'но', 'или', 'да', 'нет', 'может', 'просто']
                    words = [w for w in interest_part.split() if w not in stop_words][:4]
                    if words:
                        profile_data['interests'] = ' '.join(words)
                    break

        # Skills - расширенные паттерны
        skill_keywords = ['умею', 'знаю', 'могу', 'специалист', 'опыт в', 'работаю с', 'разбираюсь в', 'занимаюсь', 'разработал', 'создал', 'делаю']
        for keyword in skill_keywords:
            if keyword in message_lower:
                idx = message_lower.find(keyword)
                if idx >= 0:
                    skill_part = message_lower[idx + len(keyword):].strip()
                    # Убираем лишние слова
                    stop_words = ['что', 'и', 'а', 'но', 'или', 'да', 'нет', 'может', 'просто', 'очень', 'хорошо']
                    words = [w for w in skill_part.split() if w not in stop_words][:4]
                    if words:
                        profile_data['skills'] = ' '.join(words)
                    break

        # Goals - расширенные паттерны
        goal_keywords = ['хочу', 'планирую', 'мечтаю', 'цель', 'намерен', 'собираюсь', 'стремлюсь']
        for keyword in goal_keywords:
            if keyword in message_lower:
                idx = message_lower.find(keyword)
                if idx >= 0:
                    goal_part = message_lower[idx + len(keyword):].strip()
                    # Убираем лишние слова
                    stop_words = ['что', 'и', 'а', 'но', 'или', 'да', 'нет', 'может', 'просто', 'очень']
                    words = [w for w in goal_part.split() if w not in stop_words][:6]
                    if words:
                        profile_data['goals'] = ' '.join(words)
                    break

        # Priority: AI-extracted params from tool call
        if self.params:
            ai_data = {k: v for k, v in self.params.items() if v}
            # Merge AI data with local parsing (AI has priority)
            profile_data = {**profile_data, **ai_data}

        if not profile_data:
            return "Не удалось распознать информацию для обновления профиля. Попробуйте: 'я из Москвы, работаю в ASI Biont директором, люблю ИИ и книги'"

        # Call the handler
        result = handlers.update_profile(
            user_id=user_id,
            city=profile_data.get('city'),
            birth_date=profile_data.get('birth_date'),
            interests=profile_data.get('interests'),
            skills=profile_data.get('skills'),
            goals=profile_data.get('goals'),
            company=profile_data.get('company'),
            position=profile_data.get('position'),
            session=db_session
        )

        # Generate natural response without mentioning the action
        return result if result else "Профиль обновлен"