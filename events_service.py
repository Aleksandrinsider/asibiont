"""
Kudago Events Service - получение актуальных мероприятий в городе

API Documentation: https://kudago.com/public-api/v1.4/
"""

import logging
import aiohttp
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from config import KUDAGO_API_URL

logger = logging.getLogger(__name__)

# Маппинг городов на Kudago location slugs
CITY_MAPPING = {
    'москва': 'msk',
    'moscow': 'msk',
    'санкт-петербург': 'spb',
    'спб': 'spb',
    'saint-petersburg': 'spb',
    'petersburg': 'spb',
    'пермь': 'perm',
    'perm': 'perm',
    'екатеринбург': 'ekb',
    'екb': 'ekb',
    'yekaterinburg': 'ekb',
    'новосибирск': 'nsk',
    'novosibirsk': 'nsk',
    'казань': 'kzn',
    'kazan': 'kzn',
    'нижний новгород': 'nnov',
    'nizhny novgorod': 'nnov',
    'самара': 'samara',
    'samara': 'samara',
    'краснодар': 'krasnodar',
    'krasnodar': 'krasnodar',
    'красноярск': 'krasnoyarsk',
    'krasnoyarsk': 'krasnoyarsk',
    'воронеж': 'vrn',
    'voronezh': 'vrn',
}

# Маппинг категорий
CATEGORY_MAPPING = {
    'концерты': 'concert',
    'concerts': 'concert',
    'выставки': 'exhibition',
    'exhibitions': 'exhibition',
    'митапы': 'business',
    'meetups': 'business',
    'бизнес': 'business',
    'business': 'business',
    'спорт': 'sport',
    'sport': 'sport',
    'кино': 'cinema',
    'cinema': 'cinema',
    'театр': 'theater',
    'theater': 'theater',
    'образование': 'education',
    'education': 'education',
    'вечеринки': 'party',
    'parties': 'party',
    'квесты': 'quest',
    'quests': 'quest',
}


async def get_city_events(
    city: str,
    categories: Optional[List[str]] = None,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    price_range: Optional[str] = None,
    limit: int = 10
) -> Dict:
    """
    Получает актуальные мероприятия в городе через Kudago API
    
    Args:
        city: Название города (русское или английское)
        categories: Список категорий ['концерты', 'митапы', 'выставки']
        date_from: Начало периода (default: сегодня)
        date_to: Конец периода (default: через неделю)
        price_range: 'free' или 'paid' или None (все)
        limit: Максимум событий (default: 10)
    
    Returns:
        Dict с результатами: {'status': 'success', 'events': [...], 'count': N}
    """
    
    try:
        # Определяем location slug
        city_lower = city.lower().strip()
        location = CITY_MAPPING.get(city_lower)
        
        if not location:
            logger.warning(f"[EVENTS] Unknown city: {city}")
            return {
                'status': 'error',
                'message': f'Город "{city}" не поддерживается. Доступные: Москва, СПб, Пермь, Екатеринбург, Новосибирск, Казань, Нижний Новгород, Самара, Краснодар, Красноярск, Воронеж',
                'events': [],
                'count': 0
            }
        
        # Определяем временной диапазон
        if not date_from:
            date_from = datetime.now()
        if not date_to:
            date_to = date_from + timedelta(days=7)
        
        # Конвертируем в UNIX timestamp
        actual_since = int(date_from.timestamp())
        actual_until = int(date_to.timestamp())
        
        # Формируем параметры запроса
        params = {
            'location': location,
            'actual_since': actual_since,
            'actual_until': actual_until,
            'fields': 'id,title,place,dates,images,price,categories,description,site_url,age_restriction',
            'text_format': 'text',
            'expand': 'place',
            'page_size': limit,
            'order_by': 'publication_date',
        }
        
        # Добавляем фильтр по категориям
        if categories:
            category_slugs = []
            for cat in categories:
                cat_slug = CATEGORY_MAPPING.get(cat.lower().strip())
                if cat_slug:
                    category_slugs.append(cat_slug)
            
            if category_slugs:
                params['categories'] = ','.join(category_slugs)
        
        # Фильтр по цене
        if price_range == 'free':
            params['is_free'] = 'true'
        
        # Запрос к API
        url = f"{KUDAGO_API_URL}/events/"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as response:
                if response.status != 200:
                    logger.error(f"[EVENTS] Kudago API error: {response.status}")
                    return {
                        'status': 'error',
                        'message': f'Ошибка API Kudago: {response.status}',
                        'events': [],
                        'count': 0
                    }
                
                data = await response.json()
                results = data.get('results', [])
                
                # Форматируем результаты
                events = []
                for event in results[:limit]:
                    formatted_event = _format_event(event)
                    if formatted_event:
                        events.append(formatted_event)
                
                logger.info(f"[EVENTS] Found {len(events)} events in {city}")
                
                return {
                    'status': 'success',
                    'city': city,
                    'location': location,
                    'events': events,
                    'count': len(events),
                    'period': f"{date_from.strftime('%d.%m.%Y')} - {date_to.strftime('%d.%m.%Y')}"
                }
                
    except Exception as e:
        logger.error(f"[EVENTS] Error getting events: {e}", exc_info=True)
        return {
            'status': 'error',
            'message': f'Ошибка получения событий: {str(e)}',
            'events': [],
            'count': 0
        }


def _format_event(event: Dict) -> Optional[Dict]:
    """Форматирует событие для удобного отображения"""
    
    try:
        # Базовая информация
        formatted = {
            'id': event.get('id'),
            'title': event.get('title', 'Без названия'),
            'description': event.get('description', '')[:200] + '...' if event.get('description') else '',
        }
        
        # Место проведения
        place = event.get('place', {})
        if place:
            formatted['place'] = place.get('title', 'Не указано')
            formatted['address'] = place.get('address', '')
        else:
            formatted['place'] = 'Онлайн или не указано'
            formatted['address'] = ''
        
        # Даты
        dates = event.get('dates', [])
        if dates and len(dates) > 0:
            first_date = dates[0]
            start_timestamp = first_date.get('start')
            if start_timestamp:
                start_dt = datetime.fromtimestamp(start_timestamp)
                formatted['date'] = start_dt.strftime('%d.%m.%Y')
                formatted['time'] = start_dt.strftime('%H:%M')
                formatted['datetime'] = start_dt
            else:
                formatted['date'] = 'Дата не указана'
                formatted['time'] = ''
        else:
            formatted['date'] = 'Дата не указана'
            formatted['time'] = ''
        
        # Цена
        price = event.get('price', '')
        formatted['price'] = price if price else 'Бесплатно'
        formatted['is_free'] = event.get('is_free', False)
        
        # Категории
        categories = event.get('categories', [])
        formatted['categories'] = [cat for cat in categories if isinstance(cat, str)][:3]
        
        # Ссылка
        formatted['url'] = event.get('site_url', '')
        
        # Возрастное ограничение
        age_restriction = event.get('age_restriction')
        formatted['age_restriction'] = f"{age_restriction}+" if age_restriction else None
        
        # Изображение
        images = event.get('images', [])
        if images and len(images) > 0:
            formatted['image'] = images[0].get('image')
        
        return formatted
        
    except Exception as e:
        logger.error(f"[EVENTS] Error formatting event: {e}")
        return None


def format_events_for_chat(events_data: Dict) -> str:
    """Форматирует результат для отображения в чате"""
    
    if events_data['status'] != 'success':
        return events_data.get('message', 'Ошибка получения событий')
    
    events = events_data['events']
    count = events_data['count']
    
    if count == 0:
        return f"Не нашёл актуальных мероприятий в городе {events_data['city']} на ближайшую неделю."
    
    # Формируем текст
    lines = [f"Нашёл {count} мероприятий в городе {events_data['city']}:\n"]
    
    for i, event in enumerate(events, 1):
        lines.append(f"{i}. {event['title']}")
        
        if event['date'] != 'Дата не указана':
            date_str = f"{event['date']}"
            if event['time']:
                date_str += f" в {event['time']}"
            lines.append(f"   📅 {date_str}")
        
        if event['place'] and event['place'] != 'Онлайн или не указано':
            lines.append(f"   📍 {event['place']}")
        
        if event['price']:
            lines.append(f"   💰 {event['price']}")
        
        if event.get('age_restriction'):
            lines.append(f"   🔞 {event['age_restriction']}")
        
        if event['url']:
            lines.append(f"   🔗 {event['url']}")
        
        lines.append("")  # Пустая строка между событиями
    
    return "\n".join(lines)


# Тестирование
if __name__ == "__main__":
    import asyncio
    
    async def test():
        # Тест 1: Мероприятия в Перми на неделю
        result = await get_city_events('Пермь', categories=['митапы', 'бизнес'], limit=5)
        print(format_events_for_chat(result))
        print("\n" + "="*80 + "\n")
        
        # Тест 2: Бесплатные события в Москве
        result = await get_city_events('Москва', price_range='free', limit=5)
        print(format_events_for_chat(result))
    
    asyncio.run(test())
