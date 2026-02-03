import asyncio
import sys
from ai_integration.commands.create_worker_task import CreateWorkerTaskCommand

async def test_news_integration():
    """Тестирование интеграции с новостями Alpha Vantage"""
    print("📰 Тестирование новостной интеграции Alpha Vantage")
    print("=" * 60)

    cmd = CreateWorkerTaskCommand(message='test')

    # Тестируем получение новостей для разных активов
    test_symbols = ['AAPL', 'TSLA', 'GOOGL']

    for symbol in test_symbols:
        print(f"\n📈 Тестируем новости по {symbol}:")
        print("-" * 40)

        try:
            news_data = await cmd._get_asset_news(symbol, limit=5)

            if news_data:
                print(f"✅ Новости получены успешно!")
                print(f"📊 Количество важных новостей: {news_data['news_count']}")
                print(f"📰 Всего новостей обработано: {news_data.get('total_news_processed', 'N/A')}")
                print(f"🎯 Доминирующий сентимент: {news_data['dominant_sentiment'].upper()}")
                print(f"📈 Доля доминирующего сентимента: {news_data['sentiment_ratio']:.1%}")
                print(f"📊 Средняя релевантность: {news_data.get('average_relevance', 0):.2f}")

                # Детальная статистика сентиментов
                sentiment_counts = news_data.get('sentiment_counts', {})
                print("📊 Распределение сентиментов:")
                for sentiment, count in sentiment_counts.items():
                    if count > 0:
                        percentage = count / sum(sentiment_counts.values()) * 100
                        print(f"  • {sentiment.capitalize()}: {count} ({percentage:.1f}%)")

                # Сообщение если новостей нет
                if news_data.get('message'):
                    print(f"💬 {news_data['message']}")

                # Важные новости
                important_news = news_data.get('important_news', [])
                if important_news:
                    print("\n📰 Важные новости:")
                    for i, news in enumerate(important_news, 1):
                        title = news.get('title', 'Без заголовка')
                        source = news.get('source', 'Неизвестный источник')
                        sentiment = news.get('sentiment', 'neutral')
                        relevance = news.get('relevance', 0)

                        # Сокращаем заголовок если слишком длинный
                        title_short = title[:80] + "..." if len(title) > 80 else title

                        print(f"  {i}. {title_short}")
                        print(f"     Источник: {source} | Сентимент: {sentiment} | Релевантность: {relevance:.2f}")
                else:
                    print("\n⚠️  Важных новостей не найдено (релевантность < 0.5)")
            else:
                print("❌ Не удалось получить новости")
                print("   Возможные причины:")
                print("   • Отсутствует API ключ ALPHA_VANTAGE_API_KEY")
                print("   • Превышен лимит запросов")
                print("   • Проблемы с сетевым подключением")

        except Exception as e:
            print(f"❌ Ошибка при получении новостей: {e}")
            print(f"   Тип ошибки: {type(e).__name__}")

        print()

    print("🎯 Тестирование завершено!")

if __name__ == "__main__":
    asyncio.run(test_news_integration())