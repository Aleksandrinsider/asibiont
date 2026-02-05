
def get_filtered_news_for_user(user_id, session=None):
    """
    Получает отфильтрованные новости на основе интересов пользователя.
    Использует категоризацию новостей для избежания превышения API лимитов.

    Args:
        user_id: ID пользователя Telegram
        session: Сессия БД (опционально)

    Returns:
        Строка с отфильтрованными новостями или None
    """
    from models import Session, UserProfile

    if not user_id:
        return None

    # Получаем профиль пользователя
    if session is None:
        db_session = Session()
        close_session = True
    else:
        db_session = session
        close_session = False

    try:
        user_profile = db_session.query(UserProfile).filter_by(user_id=user_id).first()
        if not user_profile:
            return None

        # Определяем категории на основе профиля пользователя
        user_interests = (user_profile.interests or "").lower()
        user_skills = (user_profile.skills or "").lower()
        user_goals = (user_profile.goals or "").lower()
        user_company = (user_profile.company or "").lower()

        # Маппинг интересов/навыков на категории новостей
        category_mapping = {
            'politics': ['политика', 'власть', 'правительство', 'президент', 'выборы', 'закон', 'депутат'],
            'economy': ['экономика', 'бизнес', 'финансы', 'деньги', 'валюта', 'инфляция', 'кризис', 'банки', 'акции'],
            'technology': ['технологии', 'интернет', 'цифровизация', 'ии', 'ai', 'роботы', 'гаджеты', 'стартапы'],
            'sports': ['спорт', 'футбол', 'хоккей', 'олимпиада', 'чемпионат', 'тренер', 'спортсмен'],
            'science': ['наука', 'исследования', 'открытия', 'космос', 'медицина', 'здоровье', 'вакцина'],
            'culture': ['культура', 'искусство', 'театр', 'музыка', 'кино', 'литература', 'выставка'],
            'education': ['образование', 'школа', 'университет', 'обучение', 'курсы', 'студенты'],
            'real_estate': ['недвижимость', 'жилье', 'квартира', 'дом', 'ипотека', 'строительство']
        }

        # Определяем релевантные категории
        relevant_categories = []

        # Анализируем интересы
        for category, keywords in category_mapping.items():
            for keyword in keywords:
                if keyword in user_interests:
                    relevant_categories.append(category)
                    break

        # Анализируем навыки
        for category, keywords in category_mapping.items():
            for keyword in keywords:
                if keyword in user_skills:
                    if category not in relevant_categories:
                        relevant_categories.append(category)
                    break

        # Анализируем цели
        for category, keywords in category_mapping.items():
            for keyword in keywords:
                if keyword in user_goals:
                    if category not in relevant_categories:
                        relevant_categories.append(category)
                    break

        # Анализируем компанию (для бизнес новостей)
        if any(word in user_company for word in ['технологии', 'it', 'разработка', 'стартап']):
            if 'technology' not in relevant_categories:
                relevant_categories.append('technology')
        if any(word in user_company for word in ['банки', 'финансы', 'инвестиции']):
            if 'economy' not in relevant_categories:
                relevant_categories.append('economy')

        # Если нет релевантных категорий, используем общие новости
        if not relevant_categories:
            logger.info(f"[NEWS FILTER] No relevant categories for user {user_id}, using general news")
            return get_news_info()

        # Ограничиваем количество категорий в зависимости от подписки
        # Для FREE пользователей - максимум 1 категория
        # Для PREMIUM - максимум 3 категории
        from models import User
        user = db_session.query(User).filter_by(telegram_id=user_id).first()
        is_premium = user and user.subscription_tier == 'PREMIUM'

        if is_premium:
            max_categories = 3
        else:
            max_categories = 1

        selected_categories = relevant_categories[:max_categories]

        logger.info(f"[NEWS FILTER] User {user_id} relevant categories: {selected_categories}")

        # Получаем новости для каждой категории
        all_news = []
        for category in selected_categories:
            try:
                # Используем кеш новостей по категориям
                cache_key = f"news_category_{category}"
                ttl_seconds = 120 * 60  # 2 часа

                # Проверяем Redis кеш
                cached_data = _redis_get(cache_key)
                if cached_data:
                    logger.info(f"[NEWS FILTER] Using cached news for category {category}")
                    category_news = cached_data
                else:
                    # Получаем новости для категории
                    category_news = _load_news_for_category(category)
                    if category_news:
                        _redis_set(cache_key, category_news, ttl_seconds)

                if category_news:
                    all_news.append(category_news)

            except Exception as e:
                logger.error(f"[NEWS FILTER] Error getting news for category {category}: {e}")
                continue

        # Если нет новостей, возвращаем общие
        if not all_news:
            return get_news_info()

        # Объединяем новости из всех категорий
        combined_news = "\n\n".join(all_news)

        # Ограничиваем длину
        if len(combined_news) > 1000:
            combined_news = combined_news[:1000] + "..."

        return combined_news

    except Exception as e:
        logger.error(f"[NEWS FILTER] Error in get_filtered_news_for_user: {e}")
        return None
    finally:
        if close_session:
            db_session.close()


def _load_news_for_category(category):
    """
    Загружает новости для конкретной категории.
    """
    try:
        # Маппинг категорий на поисковые запросы
        category_queries = {
            'politics': 'политика Россия',
            'economy': 'экономика Россия бизнес',
            'technology': 'технологии Россия инновации',
            'sports': 'спорт Россия чемпионат',
            'science': 'наука Россия исследования',
            'culture': 'культура Россия искусство',
            'education': 'образование Россия школа университет',
            'real_estate': 'недвижимость Россия жилье'
        }

        query = category_queries.get(category, 'Россия')
        category_names = {
            'politics': 'Политика',
            'economy': 'Экономика',
            'technology': 'Технологии',
            'sports': 'Спорт',
            'science': 'Наука',
            'culture': 'Культура',
            'education': 'Образование',
            'real_estate': 'Недвижимость'
        }

        category_name = category_names.get(category, 'Новости')

        # Запрашиваем новости
        api_url = f"https://newsapi.org/v2/everything?q={query}&language=ru&sortBy=publishedAt&apiKey={NEWSAPI_API_KEY}&pageSize=3"
        response = requests.get(api_url, timeout=10)

        if response.status_code == 200:
            data = response.json()

            if data.get('status') == 'ok' and data.get('articles'):
                articles = data['articles']
                news_items = []

                for article in articles:
                    title = article.get('title', '').strip()
                    if title and title != '[Removed]':
                        news_items.append(f"• {title}")

                if news_items:
                    news_str = f"{category_name}:\n" + "\n".join(news_items)
                    return news_str

        return None

    except Exception as e:
        logger.error(f"[NEWS CATEGORY] Error loading news for {category}: {e}")
        return None