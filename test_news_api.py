import requests
from config import ALPHA_VANTAGE_API_KEY

# Проверяем какие эндпоинты доступны для новостей
print('Проверяем доступные эндпоинты Alpha Vantage...')

# Попробуем получить новости по AAPL
url = f'https://www.alphavantage.co/query?function=NEWS_SENTIMENT&tickers=AAPL&apikey={ALPHA_VANTAGE_API_KEY}&limit=5'
response = requests.get(url)

if response.status_code == 200:
    data = response.json()
    print('✅ API новостей работает!')
    print(f'Количество новостей: {len(data.get("feed", []))}')
    if data.get('feed'):
        print('Пример новости:')
        news = data['feed'][0]
        print(f'Заголовок: {news.get("title", "N/A")}')
        print(f'Источник: {news.get("source", "N/A")}')
        print(f'Сентимент: {news.get("overall_sentiment_label", "N/A")}')
        ticker_sentiment = news.get('ticker_sentiment', [])
        if ticker_sentiment:
            print(f'Релевантность: {ticker_sentiment[0].get("relevance_score", "N/A")}')
        else:
            print('Релевантность: N/A')
else:
    print(f'❌ Ошибка API: {response.status_code}')
    print(response.text)