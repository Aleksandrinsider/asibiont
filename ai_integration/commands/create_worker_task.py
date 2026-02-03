from .base_command import BaseCommand
from models import Session, Task, User, SubscriptionTier
from reminder_service import REMINDER_SERVICE
from datetime import datetime, timedelta
import logging
import asyncio
import requests
import aiohttp
import json
from subscription_service import check_subscription
from config import OPENWEATHERMAP_API_KEY, ALPHA_VANTAGE_API_KEY, DEEPSEEK_API_KEY, DEEPSEEK_MODEL
from ai_integration.prompts import get_extended_system_prompt

logger = logging.getLogger(__name__)

class CreateWorkerTaskCommand(BaseCommand):
    async def execute(self, user_id, db_session):
        try:
            task_description = self.params.get('task_description', '')
            interval_minutes = self.params.get('interval_minutes', 1440)  # Минимальный интервал 24 часа
            action = self.params.get('action', '')
            threshold = self.params.get('threshold', 0)
            city = self.params.get('city', 'Moscow')  # Город по умолчанию
            weather_condition = self.params.get('weather_condition', '')  # Условие погоды
            asset_type = self.params.get('asset_type', 'gold')  # Тип актива: metal, currency, commodity (stocks disabled)
            symbol = self.params.get('symbol', 'GOLD')  # Символ актива
            analysis_type = self.params.get('analysis_type', 'technical_analysis')  # Тип анализа: technical_analysis
            response_style = self.params.get('response_style', 'formal')  # Стиль ответа: formal, conversational

            # Проверяем тариф - только PREMIUM
            user = db_session.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return "Пользователь не найден"
            
            if user.subscription_tier != SubscriptionTier.PREMIUM:
                return "Функция фоновых задач доступна только на тарифе PREMIUM. Обновите подписку для использования этой возможности."

            # Проверяем минимальный интервал - не чаще раза в час
            if interval_minutes < 60:
                interval_minutes = 60
                logger.info(f"Adjusted interval to minimum 60 minutes for user {user_id}")

            # Для PREMIUM пользователей нет ограничения на количество worker задач

            # Создаем задачу в БД для отслеживания
            worker_task = Task(
                title=f"Worker: {task_description}",
                description=f"Автоматическая задача: {action}, тип актива: {asset_type}, символ: {symbol}, анализ: {analysis_type}, стиль ответа: {response_style}, интервал {interval_minutes} мин, порог {threshold}, город {city}, условие {weather_condition}",
                user_id=user.id,
                status='active',
                created_at=datetime.now(),
                reminder_time=None  # Worker не имеет фиксированного времени
            )
            db_session.add(worker_task)
            db_session.commit()

            # Добавляем периодическую задачу в scheduler
            if REMINDER_SERVICE:
                job_id = f"worker_{worker_task.id}_{user_id}"
                REMINDER_SERVICE.scheduler.add_job(
                    self._execute_worker_action,
                    trigger="interval",
                    minutes=interval_minutes,
                    id=job_id,
                    args=[user_id, action, threshold, worker_task.id, city, weather_condition, asset_type, symbol, analysis_type, response_style],
                    replace_existing=True
                )
                logger.info(f"Worker task created: {job_id}")

            return f"Автоматическая задача создана: {task_description}. Будет выполняться каждые {interval_minutes} минут (минимум раз в час)."

        except Exception as e:
            logger.error(f"Error creating worker task: {e}")
            return f"Ошибка при создании фоновой задачи: {str(e)}"

    async def _execute_worker_action(self, user_id, action, threshold, task_id, city='Moscow', weather_condition='', asset_type='gold', symbol='GOLD', analysis_type='technical_analysis', response_style='formal'):
        try:
            if action == 'monitor_asset':
                await self._monitor_asset(user_id, threshold, task_id, asset_type, symbol, analysis_type, response_style)
            elif action == 'monitor_weather':
                await self._monitor_weather(user_id, threshold, task_id, city, weather_condition)
            # Можно добавить другие действия
        except Exception as e:
            logger.error(f"Error executing worker action {action}: {e}")

    async def _monitor_asset(self, user_id, threshold, task_id, asset_type, symbol, analysis_type='technical_analysis', response_style='formal'):
        try:
            current_price = None
            asset_name = symbol

            if asset_type == 'metal':
# Металлы: основные металлы для экономии API запросов
                metal_symbols = {
                    'GOLD': 'золота',
                    'XAU': 'золота',
                    'SILVER': 'серебра',
                    'XAG': 'серебра',
                    'PLAT': 'платины',
                    'PLATINUM': 'платины',
                    'PALL': 'палладия',
                    'PALLADIUM': 'палладия'
                }
                
                symbol_upper = symbol.upper()
                if symbol_upper in metal_symbols:
                    asset_name = metal_symbols[symbol_upper]
                    # Используем стандартный API для технических индикаторов
                    api_url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol_upper}&apikey={ALPHA_VANTAGE_API_KEY}"
                else:
                    logger.error(f"Unsupported metal symbol: {symbol}. Supported: {list(metal_symbols.keys())}")
                    return

                response = requests.get(api_url)
                if response.status_code == 200:
                    data = response.json()
                    quote = data.get('Global Quote', {})
                    current_price = float(quote.get('05. price', 0))

            elif asset_type == 'commodity':
                # Товары: нефть WTI и Brent
                if symbol.upper() in ['WTI', 'BRENT']:
                    if symbol.upper() == 'WTI':
                        api_url = f"https://www.alphavantage.co/query?function=WTI&interval=monthly&apikey={ALPHA_VANTAGE_API_KEY}"
                        asset_name = "нефти WTI"
                    else:  # BRENT
                        api_url = f"https://www.alphavantage.co/query?function=BRENT&interval=monthly&apikey={ALPHA_VANTAGE_API_KEY}"
                        asset_name = "нефти Brent"
                else:
                    logger.error(f"Unsupported commodity: {symbol}. Supported: WTI, BRENT")
                    return

                response = requests.get(api_url)
                if response.status_code == 200:
                    data = response.json()
                    # Для нефти API возвращает данные в формате data['data'][0]['value']
                    if 'data' in data and len(data['data']) > 0:
                        current_price = float(data['data'][0]['value'])
                    else:
                        logger.error(f"Invalid oil price data format for {symbol}")
                        return

            elif asset_type == 'currency':
                # Валюты: основные пары форекс для экономии API
                forex_pairs = [
                    'EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'AUDUSD', 'USDCAD', 'NZDUSD', 'USDRUB'
                ]
                
                if '/' in symbol:
                    from_curr, to_curr = symbol.split('/', 1)
                    pair = from_curr + to_curr
                else:
                    pair = symbol.upper()
                    # Для EURUSD разделяем на EUR и USD
                    if len(pair) == 6:
                        from_curr = pair[:3]
                        to_curr = pair[3:]
                    else:
                        logger.error(f"Invalid currency pair format: {symbol}")
                        return
                
                if pair not in forex_pairs:
                    logger.error(f"Unsupported forex pair: {symbol}. Supported: {forex_pairs}")
                    return

                api_url = f"https://www.alphavantage.co/query?function=CURRENCY_EXCHANGE_RATE&from_currency={from_curr}&to_currency={to_curr}&apikey={ALPHA_VANTAGE_API_KEY}"
                asset_name = f"{from_curr}/{to_curr}"

                response = requests.get(api_url)
                if response.status_code == 200:
                    data = response.json()
                    exchange_rate = data.get('Realtime Currency Exchange Rate', {})
                    current_price = float(exchange_rate.get('5. Exchange Rate', 0))

            elif asset_type == 'stock':
                # Акции отключены для экономии API запросов (25 в день)
                logger.error("Stock monitoring is disabled to save API requests. Focus on metals, commodities, and currencies.")
                return

                response = requests.get(api_url)
                if response.status_code == 200:
                    data = response.json()
                    quote = data.get('Global Quote', {})
                    current_price = float(quote.get('05. price', 0))

            else:
                logger.error(f"Unsupported asset type: {asset_type}")
                return

            # Выполняем анализ в зависимости от типа
            if analysis_type == 'technical_analysis':
                # Технический анализ с индикаторами
                try:
                    indicators = await self._get_technical_indicators(symbol, 'daily', asset_type)
                    # Новости включены, но ограничены для экономии API (максимум 3 новости)
                    news_data = await self._get_asset_news(symbol, limit=3)
                    
                    if indicators:
                        signals, recommendation = await self._analyze_asset_signals(symbol, asset_type, indicators, news_data)
                        
                        if REMINDER_SERVICE and REMINDER_SERVICE.bot:
                            if response_style == 'conversational':
                                message = await self._generate_ai_conversational_message(
                                    analysis_type='technical_analysis',
                                    asset_name=asset_name,
                                    current_price=current_price,
                                    signals=signals,
                                    recommendation=recommendation,
                                    threshold=threshold
                                )
                            else:
                                message = f"📊 Технический анализ {asset_name}:\n"
                                message += f"Текущая цена: ${current_price:.2f}\n\n"
                                message += "📈 Индикаторы:\n"
                                for signal in signals:
                                    message += f"• {signal}\n"
                                message += f"\n🎯 Рекомендация: {recommendation}"
                            
                            await REMINDER_SERVICE.bot.send_message(chat_id=user_id, text=message)
                            logger.info(f"Technical analysis sent to user {user_id}: {asset_name}, recommendation: {recommendation}")
                        else:
                            logger.error("Bot not available for sending technical analysis")
                    else:
                        logger.warning(f"No indicators available for {symbol}, skipping technical analysis")
                        if REMINDER_SERVICE and REMINDER_SERVICE.bot:
                            message = f"⚠️ Технические индикаторы недоступны для {asset_name}. Возможно, данные временно недоступны."
                            await REMINDER_SERVICE.bot.send_message(chat_id=user_id, text=message)
                            
                except Exception as e:
                    logger.error(f"Error in technical analysis for {symbol}: {e}")
                    # Отправляем сообщение об ошибке
                    if REMINDER_SERVICE and REMINDER_SERVICE.bot:
                        message = f"⚠️ Не удалось выполнить технический анализ для {asset_name}. Возможно, данные недоступны для этого актива."
                        await REMINDER_SERVICE.bot.send_message(chat_id=user_id, text=message)
                        
            if response.status_code != 200:
                logger.warning(f"Failed to fetch {asset_type} price for {symbol}: {response.status_code}, response: {response.text}")

        except Exception as e:
            logger.error(f"Error monitoring asset {asset_type} {symbol}: {e}")

    async def _get_technical_indicators(self, symbol, interval='daily', asset_type='stock'):
        """Получить технические индикаторы для актива с оптимизацией API запросов"""
        try:
            indicators = {}

            # Получаем исторические данные за один запрос
            if asset_type == 'currency':
                # Для валют используем FX_DAILY для оптимизации
                if '/' in symbol:
                    from_curr, to_curr = symbol.split('/', 1)
                else:
                    # Для EURUSD разделяем на EUR и USD
                    if len(symbol) == 6:
                        from_curr = symbol[:3]
                        to_curr = symbol[3:]
                    else:
                        return None
                api_url = f"https://www.alphavantage.co/query?function=FX_DAILY&from_symbol={from_curr}&to_symbol={to_curr}&apikey={ALPHA_VANTAGE_API_KEY}&outputsize=compact"
                time_series_key = 'Time Series FX (Daily)'
            elif asset_type == 'commodity':
                # Для нефти используем специальный API для цены, но для технических индикаторов попробуем TIME_SERIES_DAILY
                if symbol.upper() == 'WTI':
                    api_url = f"https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&symbol=WTI&apikey={ALPHA_VANTAGE_API_KEY}&outputsize=compact"
                elif symbol.upper() == 'BRENT':
                    api_url = f"https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&symbol=BRENT&apikey={ALPHA_VANTAGE_API_KEY}&outputsize=compact"
                else:
                    return None
                time_series_key = 'Time Series (Daily)'
            else:
                # Для металлов используем TIME_SERIES_DAILY
                api_url = f"https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&symbol={symbol}&apikey={ALPHA_VANTAGE_API_KEY}&outputsize=compact"
                time_series_key = 'Time Series (Daily)'

            response = requests.get(api_url)
            if response.status_code != 200:
                logger.warning(f"Failed to fetch data for {symbol}: {response.status_code}")
                return None

            data = response.json()
            time_series = data.get(time_series_key, {})

            if not time_series:
                logger.warning(f"No time series data available for {symbol}")
                return None

            # Получаем последние 100 дней данных
            dates = sorted(time_series.keys(), reverse=True)[:100]
            prices = []
            volumes = []

            for date in dates:
                day_data = time_series[date]
                if asset_type == 'currency':
                    close_price = float(day_data.get('4. close', 0))
                else:
                    close_price = float(day_data.get('4. close', 0))
                    volume = float(day_data.get('5. volume', 0))
                    volumes.append(volume)
                prices.append(close_price)

            if not prices:
                return None

            # Текущая цена
            indicators['price'] = prices[0]

            # Рассчитываем базовые индикаторы локально для экономии API запросов
            # RSI (Relative Strength Index)
            if len(prices) >= 14:
                indicators['rsi'] = self._calculate_rsi(prices, 14)

            # SMA 20 и 50
            if len(prices) >= 50:
                indicators['sma_20'] = sum(prices[:20]) / 20
                indicators['sma_50'] = sum(prices[:50]) / 50

            # EMA 12 и 26
            if len(prices) >= 26:
                indicators['ema_12'] = self._calculate_ema(prices, 12)
                indicators['ema_26'] = self._calculate_ema(prices, 26)

            # MACD (упрощенная версия)
            if 'ema_12' in indicators and 'ema_26' in indicators:
                macd_line = indicators['ema_12'] - indicators['ema_26']
                indicators['macd'] = macd_line
                # Signal line (EMA 9 of MACD) - упрощенное приближение
                indicators['macd_signal'] = macd_line * 0.8
                indicators['macd_hist'] = macd_line - indicators['macd_signal']

            # Bollinger Bands
            if len(prices) >= 20:
                sma_20 = sum(prices[:20]) / 20
                variance = sum((p - sma_20) ** 2 for p in prices[:20]) / 20
                std_dev = variance ** 0.5
                indicators['bb_middle'] = sma_20
                indicators['bb_upper'] = sma_20 + (2 * std_dev)
                indicators['bb_lower'] = sma_20 - (2 * std_dev)

            # Volume (для акций)
            if volumes and asset_type == 'stock':
                indicators['volume'] = volumes[0]

            return indicators

        except Exception as e:
            logger.error(f"Error getting technical indicators for {symbol}: {e}")
            return None

    def _calculate_rsi(self, prices, period=14):
        """Рассчитать RSI локально"""
        if len(prices) < period + 1:
            return None

        gains = []
        losses = []

        for i in range(1, len(prices)):
            change = prices[i-1] - prices[i]
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))

        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        if avg_loss == 0:
            return 100

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def _calculate_ema(self, prices, period):
        """Рассчитать EMA локально"""
        if len(prices) < period:
            return None

        multiplier = 2 / (period + 1)
        ema = sum(prices[:period]) / period

        for price in prices[period:]:
            ema = (price * multiplier) + (ema * (1 - multiplier))

        return ema

    async def _get_asset_news(self, symbol, limit=5):
        """Получить новости по активу из Alpha Vantage"""
        try:
            url = f"https://www.alphavantage.co/query?function=NEWS_SENTIMENT&tickers={symbol}&apikey={ALPHA_VANTAGE_API_KEY}&limit={limit}"
            response = requests.get(url)

            if response.status_code == 200:
                data = response.json()
                news_feed = data.get('feed', [])

                news_summary = []
                sentiment_counts = {'positive': 0, 'negative': 0, 'neutral': 0}

                for news in news_feed[:limit]:  # Ограничиваем количеством
                    title = news.get('title', '')
                    source = news.get('source', '')
                    sentiment = news.get('overall_sentiment_label', 'neutral').lower()
                    relevance = 0

                    # Получаем релевантность для этого тикера
                    ticker_sentiment = news.get('ticker_sentiment', [])
                    for ts in ticker_sentiment:
                        if ts.get('ticker') == symbol.upper():
                            relevance = float(ts.get('relevance_score', 0))
                            break

                    # Считаем сентимент
                    if sentiment in sentiment_counts:
                        sentiment_counts[sentiment] += 1

                    # Добавляем важные новости (релевантность > 0.5)
                    if relevance > 0.5:
                        news_summary.append({
                            'title': title,
                            'source': source,
                            'sentiment': sentiment,
                            'relevance': relevance
                        })

                # Определяем общий сентимент
                total_news = sum(sentiment_counts.values())
                if total_news > 0:
                    dominant_sentiment = max(sentiment_counts, key=sentiment_counts.get)
                    sentiment_ratio = sentiment_counts[dominant_sentiment] / total_news
                else:
                    dominant_sentiment = 'neutral'
                    sentiment_ratio = 0

                return {
                    'news_count': len(news_summary),
                    'dominant_sentiment': dominant_sentiment,
                    'sentiment_ratio': sentiment_ratio,
                    'sentiment_counts': sentiment_counts,
                    'important_news': news_summary[:3]  # Только топ-3 новости
                }
            else:
                logger.warning(f"Failed to fetch news for {symbol}: {response.status_code}")
                return None

        except Exception as e:
            logger.error(f"Error fetching news for {symbol}: {e}")
            return None

    async def _analyze_asset_signals(self, symbol, asset_type, indicators, news_data=None):
        """Анализировать сигналы на основе технических индикаторов"""
        signals = []
        recommendation = "HOLD"
        
        try:
            # Анализ RSI
            if 'rsi' in indicators:
                rsi = indicators['rsi']
                if rsi > 70:
                    signals.append(f"RSI {rsi:.2f}: Перекупленность (сигнал на продажу)")
                    if recommendation == "HOLD":
                        recommendation = "SELL"
                elif rsi < 30:
                    signals.append(f"RSI {rsi:.2f}: Перепроданность (сигнал на покупку)")
                    if recommendation == "HOLD":
                        recommendation = "BUY"
                else:
                    signals.append(f"RSI {rsi:.2f}: Нейтральная зона")
            
            # Анализ MACD
            if 'macd' in indicators and 'macd_signal' in indicators and 'macd_hist' in indicators:
                macd = indicators['macd']
                signal = indicators['macd_signal']
                hist = indicators['macd_hist']
                
                if hist > 0 and macd > signal:
                    signals.append(f"MACD: Бычий сигнал (гистограмма положительная)")
                    if recommendation == "HOLD":
                        recommendation = "BUY"
                elif hist < 0 and macd < signal:
                    signals.append(f"MACD: Медвежий сигнал (гистограмма отрицательная)")
                    if recommendation == "HOLD":
                        recommendation = "SELL"
                else:
                    signals.append(f"MACD: Нейтральный сигнал")
            
            # Анализ Bollinger Bands
            if 'bb_upper' in indicators and 'bb_middle' in indicators and 'bb_lower' in indicators and 'price' in indicators:
                price = indicators['price']
                upper = indicators['bb_upper']
                middle = indicators['bb_middle']
                lower = indicators['bb_lower']
                
                if price > upper:
                    signals.append(f"Bollinger Bands: Цена выше верхней полосы (перекупленность)")
                    if recommendation == "HOLD":
                        recommendation = "SELL"
                elif price < lower:
                    signals.append(f"Bollinger Bands: Цена ниже нижней полосы (перепроданность)")
                    if recommendation == "HOLD":
                        recommendation = "BUY"
                else:
                    signals.append(f"Bollinger Bands: Цена в нормальном диапазоне")

            # Анализ SMA (Simple Moving Average)
            if 'sma_20' in indicators and 'sma_50' in indicators and 'price' in indicators:
                price = indicators['price']
                sma20 = indicators['sma_20']
                sma50 = indicators['sma_50']
                
                if price > sma20:
                    signals.append(f"SMA20: Цена выше скользящей средней (бычий тренд)")
                else:
                    signals.append(f"SMA20: Цена ниже скользящей средней (медвежий тренд)")
                
                if sma20 > sma50:
                    signals.append(f"SMA: Золотой крест (бычий тренд)")
                    if recommendation == "HOLD":
                        recommendation = "BUY"
                elif sma20 < sma50:
                    signals.append(f"SMA: Мертвый крест (медвежий тренд)")
                    if recommendation == "HOLD":
                        recommendation = "SELL"

            # Анализ EMA (Exponential Moving Average)
            if 'ema_12' in indicators and 'ema_26' in indicators:
                ema12 = indicators['ema_12']
                ema26 = indicators['ema_26']
                
                if ema12 > ema26:
                    signals.append(f"EMA: EMA12 выше EMA26 (бычий тренд)")
                    if recommendation == "HOLD":
                        recommendation = "BUY"
                else:
                    signals.append(f"EMA: EMA12 ниже EMA26 (медвежий тренд)")
                    if recommendation == "HOLD":
                        recommendation = "SELL"

            # Анализ Stochastic Oscillator - отключен для экономии API запросов

            # Анализ ADX - отключен для экономии API запросов

            # Анализ CCI - отключен для экономии API запросов

            # Анализ MFI - отключен для экономии API запросов

            # Анализ OBV - отключен для экономии API запросов

            # Анализ объема (для акций)
            if 'volume' in indicators:
                volume = indicators['volume']
                signals.append(f"Объем торгов: {volume:,}")
                
                # Здесь можно добавить сравнение с средним объемом
                # Для простоты просто отмечаем высокий объем
                if volume > 1000000:  # Пример порога
                    signals.append("Высокий объем торгов")
            
            # Анализ новостей
            if news_data:
                news_count = news_data.get('news_count', 0)
                dominant_sentiment = news_data.get('dominant_sentiment', 'neutral')
                sentiment_ratio = news_data.get('sentiment_ratio', 0)
                important_news = news_data.get('important_news', [])
                
                if news_count > 0:
                    signals.append(f"Новостей за период: {news_count}")
                    signals.append(f"Общий сентимент новостей: {dominant_sentiment} ({sentiment_ratio:.1%})")
                    
                    # Влияние сентимента на рекомендацию
                    if dominant_sentiment == 'positive' and sentiment_ratio > 0.6:
                        signals.append("Положительный новостной фон усиливает бычьи сигналы")
                        if recommendation == "HOLD":
                            recommendation = "BUY"
                    elif dominant_sentiment == 'negative' and sentiment_ratio > 0.6:
                        signals.append("Отрицательный новостной фон усиливает медвежьи сигналы")
                        if recommendation == "HOLD":
                            recommendation = "SELL"
                    
                    # Добавляем ключевые новости
                    for news in important_news[:2]:  # Максимум 2 новости в сигналах
                        title = news.get('title', '')[:80] + '...' if len(news.get('title', '')) > 80 else news.get('title', '')
                        sentiment = news.get('sentiment', 'neutral')
                        signals.append(f"Новость: {title} ({sentiment})")
                else:
                    signals.append("Новостей за период: нет значимых")
            
        except Exception as e:
            logger.error(f"Error analyzing signals for {symbol}: {e}")
            signals.append(f"Ошибка анализа: {e}")
        
        return signals, recommendation

    async def _generate_conversational_message(self, asset_name, current_price, signals, recommendation, analysis_type):
        """Генерирует естественное, разговорное сообщение с использованием AI промпта"""
        import random
        
        # Вводные фразы
        intros = [
            f"Смотрю на {asset_name}...",
            f"Анализирую {asset_name} сейчас.",
            f"Проверяю {asset_name} для тебя.",
            f"Изучаю ситуацию с {asset_name}.",
            f"Посмотрим на {asset_name}..."
        ]
        
        # Описания цены
        price_desc = f"Цена сейчас ${current_price:.2f}."
        
        # Разговорные описания сигналов
        conversational_signals = []
        for signal in signals:
            if 'RSI' in signal:
                # Формат: "RSI 75.23: Перекупленность (сигнал на продажу)"
                try:
                    rsi_part = signal.split('RSI ')[1]  # "75.23: Перекупленность (сигнал на продажу)"
                    rsi_value = rsi_part.split(':')[0]  # "75.23"
                    rsi_value = float(rsi_value)
                    
                    if 'Перекупленность' in signal:
                        conversational_signals.append(f"RSI на уровне {rsi_value:.0f} - это уже зона перекупленности")
                    elif 'Перепроданность' in signal:
                        conversational_signals.append(f"RSI {rsi_value:.0f} показывает перепроданность")
                    else:
                        conversational_signals.append(f"RSI держится на {rsi_value:.0f}")
                except (IndexError, ValueError):
                    conversational_signals.append(signal)  # Fallback to original signal
                    
            elif 'MACD' in signal:
                if 'Бычий сигнал' in signal:
                    conversational_signals.append("MACD дает бычий сигнал")
                elif 'Медвежий сигнал' in signal:
                    conversational_signals.append("MACD показывает медвежий тренд")
                else:
                    conversational_signals.append("MACD в нейтральной зоне")
                    
            elif 'Bollinger' in signal:
                if 'выше верхней' in signal:
                    conversational_signals.append("Цена ушла выше верхней полосы Боллинджера")
                elif 'ниже нижней' in signal:
                    conversational_signals.append("Цена опустилась ниже нижней полосы Боллинджера")
                else:
                    conversational_signals.append("Цена в нормальном диапазоне Боллинджера")
                    
            elif 'Объем' in signal:
                volume = signal.split(': ')[1]
                conversational_signals.append(f"Объем торгов сегодня {volume}")
            else:
                conversational_signals.append(signal.lower())
        
        # Разговорные рекомендации
        rec_descriptions = {
            'BUY': [
                "Похоже, хорошее время для покупки",
                "Вижу возможности для роста",
                "Рекомендую рассмотреть покупку",
                "Сигналы указывают на потенциал роста"
            ],
            'SELL': [
                "Лучше зафиксировать прибыль",
                "Пора подумать о продаже",
                "Сигналы показывают на снижение",
                "Рекомендую выходить из позиции"
            ],
            'HOLD': [
                "Лучше подождать развития ситуации",
                "Пока наблюдаем, ситуация неясная",
                "Рекомендую подождать лучших сигналов",
                "Стоит понаблюдать за развитием"
            ]
        }
        
        # Заключительные фразы
        conclusions = [
            "Это мой анализ на текущий момент.",
            "Конечно, рынок может измениться.",
            "Всегда стоит диверсифицировать риски.",
            "Рекомендую мониторить новости по этому активу.",
            "Это не финансовый совет, а технический анализ."
        ]
        
        # Собираем сообщение
        intro = random.choice(intros)
        signals_text = " ".join(conversational_signals)
        rec_text = random.choice(rec_descriptions.get(recommendation, ["Ситуация требует наблюдения"]))
        conclusion = random.choice(conclusions)
        
        if analysis_type == 'technical_analysis':
            message = f"{intro} {price_desc} {signals_text}. {rec_text}. {conclusion}"
        elif analysis_type == 'volume_analysis':
            message = f"{intro} {price_desc} {signals_text}. Высокий объем может указывать на важные движения."
        else:
            message = f"{intro} {price_desc} Цена ниже порога, так что решил сообщить."
        
        return message

    async def _generate_ai_conversational_message(self, asset_name, current_price, signals, recommendation, analysis_type):
        """Генерирует естественное, разговорное сообщение с использованием AI промпта"""
        try:
            # Создаем контекст для AI в том же формате, что используется в chat.py
            ai_context = f"WORKER_ASSET_ANALYSIS: {asset_name}, цена ${current_price:.2f}, сигналы: {', '.join(signals)}, рекомендация: {recommendation}, тип анализа: {analysis_type}"
            
            # Используем специализированный промпт для финансового анализа
            system_prompt = """Ты - ASI Biont, эксперт по финансовому анализу и инвестициям. Ты даешь профессиональные, но понятные рекомендации.

ОСОБЕННОСТИ ТВОЕГО АНАЛИЗА:
1. ТЕХНИЧЕСКИЙ АНАЛИЗ: Оценивай RSI, MACD, Bollinger Bands, объемы
2. НОВОСТНОЙ ФОН: Учитывай сентимент новостей и их влияние на рынок
3. РИСКИ: Всегда упоминай о рисках и важности диверсификации
4. КОНТЕКСТ: Анализируй рыночные условия и внешние факторы

СТИЛЬ: Профессиональный, но дружелюбный. Давай конкретные советы, но напоминай, что это не финансовый совет."""
            
            # Создаем инструкцию для генерации глубокого финансового анализа
            tool_context_msg = f"""ФИНАНСОВЫЙ АНАЛИЗ АКТИВА:
{ai_context}

ИНСТРУКЦИЯ ДЛЯ ПРОФЕССИОНАЛЬНОГО АНАЛИЗА:

1. ТЕХНИЧЕСКИЙ АНАЛИЗ:
   - Оцени RSI, MACD, Bollinger Bands и объемы
   - Объясни, что означают эти индикаторы
   - Свяжи технические сигналы с ценовым движением

2. НОВОСТНОЙ АНАЛИЗ:
   - Учти сентимент новостей и их влияние
   - Объясни, как новости могут влиять на цену
   - Упомяни ключевые новости если они есть

3. ОБЩАЯ РЕКОМЕНДАЦИЯ:
   - Дай взвешенную рекомендацию BUY/SELL/HOLD
   - Обосновывай рекомендацию фактами
   - Упомяни временной горизонт

4. РИСКИ И ПРЕДУПРЕЖДЕНИЯ:
   - Всегда напоминай о рисках
   - Говори о диверсификации портфеля
   - Подчеркивай, что это не финансовый совет

5. СТИЛЬ ОТВЕТА:
   - Профессиональный, но доступный язык
   - Используй аналогии для объяснения
   - Будь честен о неопределенностях рынка
   - Заканчивай практическими советами

⚠️ КРИТИЧНО: Анализируй ВСЕ предоставленные данные, не придумывай информацию!"""

            # Вызываем AI API
            async with aiohttp.ClientSession() as session:
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Проанализируй актив {asset_name}"},
                    {"role": "user", "content": tool_context_msg}
                ]
                
                data = {
                    "model": DEEPSEEK_MODEL,
                    "messages": messages,
                    "temperature": 0.8,
                    "max_tokens": 1000
                }
                
                headers = {
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json"
                }
                
                async with session.post(
                    "https://api.deepseek.com/v1/chat/completions",
                    json=data,
                    headers=headers
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        ai_message = result['choices'][0]['message']['content'].strip()
                        logger.info(f"Generated AI conversational message for {asset_name}: {ai_message[:100]}...")
                        return ai_message
                    else:
                        error_text = await response.text()
                        logger.error(f"AI API error: {response.status}, {error_text}")
                        
        except Exception as e:
            logger.error(f"Error generating AI conversational message: {e}")
        
        # Fallback: используем старую функцию
        return await self._generate_conversational_message(asset_name, current_price, signals, recommendation, analysis_type)

    async def _monitor_weather(self, user_id, threshold, task_id, city, weather_condition):
        try:
            # Получаем текущую погоду через OpenWeatherMap API
            api_url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={OPENWEATHERMAP_API_KEY}&units=metric&lang=ru"
            response = requests.get(api_url)
            
            if response.status_code == 200:
                data = response.json()
                temp = data['main']['temp']
                weather_desc = data['weather'][0]['description']
                humidity = data['main']['humidity']
                wind_speed = data['wind']['speed']
                
                # Проверяем условия для уведомления
                should_notify = False
                message_parts = []
                
                if threshold and temp < threshold:
                    should_notify = True
                    message_parts.append(f"Температура ниже {threshold}°C")
                
                if weather_condition and weather_condition.lower() in weather_desc.lower():
                    should_notify = True
                    message_parts.append(f"Погода: {weather_desc}")
                
                if should_notify:
                    # Отправляем уведомление пользователю
                    if REMINDER_SERVICE and REMINDER_SERVICE.bot:
                        condition_text = ", ".join(message_parts) if message_parts else "условия выполнены"
                        message = f"🌤️ Погода в {city}:\n" \
                                 f"🌡️ Температура: {temp}°C\n" \
                                 f"💧 Влажность: {humidity}%\n" \
                                 f"💨 Ветер: {wind_speed} м/с\n" \
                                 f"📝 {weather_desc}\n\n" \
                                 f"⚠️ {condition_text}"
                        await REMINDER_SERVICE.bot.send_message(chat_id=user_id, text=message)
                        logger.info(f"Weather alert sent to user {user_id} for {city}: {temp}°C, {weather_desc}")
                    else:
                        logger.error("Bot not available for sending weather alert")
            else:
                logger.warning(f"Failed to fetch weather for {city}: {response.status_code}, response: {response.text}")

        except Exception as e:
            logger.error(f"Error monitoring weather: {e}")