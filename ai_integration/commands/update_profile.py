from .base_command import BaseCommand
from .. import handlers
from ..responses import generate_response

class UpdateProfileCommand(BaseCommand):
    async def execute(self, user, db_session):`n        user_id = user.telegram_id
        # Extract profile information from message
        message_lower = self.message.lower()

        # Simple parsing for profile updates
        profile_data = {}

        # City - расширенный список городов
        city_keywords = {
            'москв': 'Москва', 'москва': 'Москва', 'msk': 'Москва',
            'петербург': 'Санкт-Петербург', 'спб': 'Санкт-Петербург', 'питер': 'Санкт-Петербург',
            'екатеринбург': 'Екатеринбург', 'екб': 'Екатеринбург',
            'новосибирск': 'Новосибирск', 'нск': 'Новосибирск',
            'казан': 'Казань', 'казань': 'Казань',
            'нижний новгород': 'Нижний Новгород', 'нн': 'Нижний Новгород',
            'самара': 'Самара', 'самар': 'Самара',
            'омск': 'Омск', 'омск': 'Омск',
            'челябинск': 'Челябинск', 'челябинск': 'Челябинск',
            'ростов': 'Ростов-на-Дону', 'ростов-на-дону': 'Ростов-на-Дону',
            'уфа': 'Уфа', 'уф': 'Уфа',
            'волгоград': 'Волгоград', 'волгоград': 'Волгоград',
            'пермь': 'Пермь', 'перм': 'Пермь',
            'красноярск': 'Красноярск', 'красноярск': 'Красноярск',
            'воронеж': 'Воронеж', 'воронеж': 'Воронеж',
            'саратов': 'Саратов', 'саратов': 'Саратов',
            'краснодар': 'Краснодар', 'краснодар': 'Краснодар',
            'тольятти': 'Тольятти', 'тольятти': 'Тольятти',
            'барнаул': 'Барнаул', 'барнаул': 'Барнаул',
            'ижевск': 'Ижевск', 'ижевск': 'Ижевск',
            'ульяновск': 'Ульяновск', 'ульяновск': 'Ульяновск',
            'владивосток': 'Владивосток', 'владивосток': 'Владивосток'
        }
        
        for keyword, city_name in city_keywords.items():
            if keyword in message_lower:
                profile_data['city'] = city_name
                break

        # Company/Work - улучшенный парсинг
        company_keywords = ['работаю в', 'компания', 'фирма', 'работа', 'компанией']
        for keyword in company_keywords:
            if keyword in message_lower:
                # Extract company name after keyword
                idx = message_lower.find(keyword)
                if idx >= 0:
                    company_part = message_lower[idx + len(keyword):].strip()
                    # Take first meaningful words, but stop at position keywords
                    words = []
                    for word in company_part.split()[:4]:  # Max 4 words
                        # Stop if we hit position indicators
                        if word in ['программист', 'разработчик', 'дизайнер', 'менеджер', 'аналитик', 'тестировщик', 'инженер', 'директор', 'специалист']:
                            break
                        if len(word) > 2:  # Skip very short words
                            words.append(word)
                    if words:
                        profile_data['company'] = ' '.join(words).title()
                    break

        # Position - улучшенный парсинг
        position_keywords = ['должность', 'позиция', 'работаю', 'занимаюсь', 'я']
        position_indicators = ['программист', 'разработчик', 'дизайнер', 'менеджер', 'аналитик', 'тестировщик', 'инженер', 'директор', 'специалист', 'консультант', 'архитектор', 'администратор', 'координатор']
        
        for keyword in position_keywords:
            if keyword in message_lower:
                # Extract position after keyword
                idx = message_lower.find(keyword)
                if idx >= 0:
                    position_part = message_lower[idx + len(keyword):].strip()
                    # Look for position indicators
                    for indicator in position_indicators:
                        if indicator in position_part:
                            # Check if this is the main position (not preceded by other positions)
                            indicator_idx = position_part.find(indicator)
                            if indicator_idx >= 0:
                                # Take the position and maybe one word before
                                words_before = position_part[:indicator_idx].strip().split()
                                if words_before and len(words_before[-1]) > 2:
                                    profile_data['position'] = f"{words_before[-1]} {indicator}".title()
                                else:
                                    profile_data['position'] = indicator.title()
                                break
                    break

        # Interests - расширенные паттерны
        interest_keywords = ['люблю', 'интересует', 'интересуюсь', 'увлекаюсь', 'хобби', 'интерес', 'нравится', 'занимаюсь']
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
