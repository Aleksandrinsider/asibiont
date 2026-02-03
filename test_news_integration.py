import asyncio
from ai_integration.commands.create_worker_task import CreateWorkerTaskCommand

async def test_news_integration():
    cmd = CreateWorkerTaskCommand(message='test')

    # Тестируем получение новостей
    news_data = await cmd._get_asset_news('AAPL', limit=3)
    print('Новости по AAPL:')
    if news_data:
        print(f'Количество новостей: {news_data["news_count"]}')
        print(f'Доминирующий сентимент: {news_data["dominant_sentiment"]}')
        print(f'Доля доминирующего сентимента: {news_data["sentiment_ratio"]:.1%}')
        print('Важные новости:')
        for news in news_data['important_news']:
            title_short = news["title"][:60] + "..." if len(news["title"]) > 60 else news["title"]
            print(f'  - {title_short} ({news["sentiment"]})')
    else:
        print('Не удалось получить новости')

asyncio.run(test_news_integration())