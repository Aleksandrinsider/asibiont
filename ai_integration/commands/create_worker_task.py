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
            asset_type = self.params.get('asset_type', 'gold')  # Тип актива: gold, currency, stock
            symbol = self.params.get('symbol', 'GOLD')  # Символ актива
            analysis_type = self.params.get('analysis_type', 'price_monitoring')  # Тип анализа: price_monitoring, technical_analysis, volume_analysis
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

    async def _execute_worker_action(self, user_id, action, threshold, task_id, city='Moscow', weather_condition='', asset_type='gold', symbol='GOLD', analysis_type='price_monitoring', response_style='formal'):
        try:
            if action == 'monitor_gold_market':
                await self._monitor_gold_market(user_id, threshold, task_id)
            elif action == 'monitor_asset':
                await self._monitor_asset(user_id, threshold, task_id, asset_type, symbol, analysis_type, response_style)
            elif action == 'monitor_weather':
                await self._monitor_weather(user_id, threshold, task_id, city, weather_condition)
            # Можно добавить другие действия
        except Exception as e:
            logger.error(f"Error executing worker action {action}: {e}")

    async def _monitor_gold_market(self, user_id, threshold, task_id):
        try:
            # Используем Alpha Vantage API для получения цены золота
            api_url = f"https://www.alphavantage.co/query?function=GOLD_SILVER_SPOT&symbol=GOLD&apikey={ALPHA_VANTAGE_API_KEY}"
            response = requests.get(api_url)
            if response.status_code == 200:
                data = response.json()
                # Alpha Vantage возвращает цену в формате "price": "2069.6627794950227"
                current_price = float(data.get('price', 0))  # Цена золота в USD за унцию
                if current_price and current_price < threshold:
                    # Отправляем уведомление пользователю
                    if REMINDER_SERVICE and REMINDER_SERVICE.bot:
                        message = f"🎉 Хорошая возможность для покупки золота! Текущая цена: ${current_price:.2f} за унцию, ниже порога ${threshold}"
                        await REMINDER_SERVICE.bot.send_message(chat_id=user_id, text=message)
                        logger.info(f"Gold market alert sent to user {user_id}: price {current_price}")
                    else:
                        logger.error("Bot not available for sending gold market alert")
            else:
                logger.warning(f"Failed to fetch gold price from Alpha Vantage: {response.status_code}, response: {response.text}")

        except Exception as e:
            logger.error(f"Error monitoring gold market: {e}")

    async def _monitor_asset(self, user_id, threshold, task_id, asset_type, symbol, analysis_type='price_monitoring', response_style='formal'):
        try:
            current_price = None
            asset_name = symbol

            if asset_type == 'metal':
                # Металлы: золото, серебро
                if symbol.upper() in ['GOLD', 'XAU']:
                    api_url = f"https://www.alphavantage.co/query?function=GOLD_SILVER_SPOT&symbol=GOLD&apikey={ALPHA_VANTAGE_API_KEY}"
                    asset_name = "золота"
                elif symbol.upper() in ['SILVER', 'XAG']:
                    api_url = f"https://www.alphavantage.co/query?function=GOLD_SILVER_SPOT&symbol=SILVER&apikey={ALPHA_VANTAGE_API_KEY}"
                    asset_name = "серебра"
                else:
                    logger.error(f"Unsupported metal symbol: {symbol}")
                    return

                response = requests.get(api_url)
                if response.status_code == 200:
                    data = response.json()
                    current_price = float(data.get('price', 0))

            elif asset_type == 'currency':
                # Валюты: пары типа USD/EUR
                if '/' in symbol:
                    from_curr, to_curr = symbol.split('/', 1)
                    api_url = f"https://www.alphavantage.co/query?function=CURRENCY_EXCHANGE_RATE&from_currency={from_curr}&to_currency={to_curr}&apikey={ALPHA_VANTAGE_API_KEY}"
                    asset_name = f"{from_curr}/{to_curr}"
                else:
                    logger.error(f"Invalid currency pair format: {symbol}. Use FROM/TO format.")
                    return

                response = requests.get(api_url)
                if response.status_code == 200:
                    data = response.json()
                    exchange_rate = data.get('Realtime Currency Exchange Rate', {})
                    current_price = float(exchange_rate.get('5. Exchange Rate', 0))

            elif asset_type == 'stock':
                # Акции: используем GLOBAL_QUOTE для текущей цены
                api_url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey={ALPHA_VANTAGE_API_KEY}"
                asset_name = f"акции {symbol}"

                response = requests.get(api_url)
                if response.status_code == 200:
                    data = response.json()
                    quote = data.get('Global Quote', {})
                    current_price = float(quote.get('05. price', 0))

            else:
                logger.error(f"Unsupported asset type: {asset_type}")
                return

            # Выполняем анализ в зависимости от типа
            if analysis_type == 'price_monitoring':
                # Простой мониторинг цены
                if current_price and current_price < threshold:
                    if REMINDER_SERVICE and REMINDER_SERVICE.bot:
                        if response_style == 'conversational':
                            message = await self._generate_ai_conversational_message(
                                analysis_type='price_monitoring',
                                asset_name=asset_name,
                                current_price=current_price,
                                signals=[],
                                recommendation="Возможность для покупки",
                                threshold=threshold
                            )
                        else:
                            message = f"🎉 Хорошая возможность для покупки {asset_name}! Текущая цена: ${current_price:.2f}, ниже порога ${threshold}"
                        await REMINDER_SERVICE.bot.send_message(chat_id=user_id, text=message)
                        logger.info(f"Asset alert sent to user {user_id}: {asset_name} price {current_price}")
                    else:
                        logger.error("Bot not available for sending asset alert")
                        
            elif analysis_type == 'technical_analysis':
                # Технический анализ с индикаторами
                indicators = await self._get_technical_indicators(symbol, 'daily', asset_type)
                news_data = await self._get_asset_news(symbol, limit=10)  # Получаем новости
                
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
                        
            elif analysis_type == 'volume_analysis':
                # Анализ объема торгов (для акций)
                if asset_type == 'stock':
                    indicators = await self._get_technical_indicators(symbol, 'daily', asset_type)
                    if 'volume' in indicators and 'price' in indicators:
                        volume = indicators['volume']
                        price = indicators['price']
                        
                        # Простая логика анализа объема
                        volume_threshold = 1000000  # Можно сделать настраиваемым
                        
                        if volume > volume_threshold:
                            if REMINDER_SERVICE and REMINDER_SERVICE.bot:
                                if response_style == 'conversational':
                                    message = await self._generate_ai_conversational_message(
                                        analysis_type='volume_analysis',
                                        asset_name=asset_name,
                                        current_price=price,
                                        signals=[f"Объем торгов: {volume:,}"],
                                        recommendation="Высокий объем - следите за движением цены",
                                        threshold=volume_threshold
                                    )
                                else:
                                    message = f"📊 Анализ объема {asset_name}:\n"
                                    message += f"Цена: ${price:.2f}\n"
                                    message += f"Объем: {volume:,}\n"
                                    message += f"🚨 Высокий объем торгов! Возможно значимое движение цены."
                                
                                await REMINDER_SERVICE.bot.send_message(chat_id=user_id, text=message)
                                logger.info(f"Volume analysis sent to user {user_id}: {asset_name}, volume: {volume}")
                            else:
                                logger.error("Bot not available for sending volume analysis")
                        else:
                            logger.info(f"Volume for {symbol} is normal: {volume}")
                    else:
                        logger.warning(f"Could not get volume data for {symbol}")
                else:
                    logger.warning(f"Volume analysis not supported for asset type: {asset_type}")

            if response.status_code != 200:
                logger.warning(f"Failed to fetch {asset_type} price for {symbol}: {response.status_code}, response: {response.text}")

        except Exception as e:
            logger.error(f"Error monitoring asset {asset_type} {symbol}: {e}")

    async def _get_technical_indicators(self, symbol, interval='daily', asset_type='stock'):
        """Получить технические индикаторы для актива"""
        try:
            indicators = {}
            
            # RSI (Relative Strength Index)
            rsi_url = f"https://www.alphavantage.co/query?function=RSI&symbol={symbol}&interval={interval}&time_period=14&series_type=close&apikey={ALPHA_VANTAGE_API_KEY}"
            rsi_response = requests.get(rsi_url)
            if rsi_response.status_code == 200:
                rsi_data = rsi_response.json()
                rsi_values = rsi_data.get('Technical Analysis: RSI', {})
                if rsi_values:
                    latest_date = max(rsi_values.keys())
                    indicators['rsi'] = float(rsi_values[latest_date]['RSI'])
            
            # MACD
            macd_url = f"https://www.alphavantage.co/query?function=MACD&symbol={symbol}&interval={interval}&series_type=close&apikey={ALPHA_VANTAGE_API_KEY}"
            macd_response = requests.get(macd_url)
            if macd_response.status_code == 200:
                macd_data = macd_response.json()
                macd_values = macd_data.get('Technical Analysis: MACD', {})
                if macd_values:
                    latest_date = max(macd_values.keys())
                    macd_info = macd_values[latest_date]
                    indicators['macd'] = float(macd_info['MACD'])
                    indicators['macd_signal'] = float(macd_info['MACD_Signal'])
                    indicators['macd_hist'] = float(macd_info['MACD_Hist'])
            
            # Bollinger Bands
            bb_url = f"https://www.alphavantage.co/query?function=BBANDS&symbol={symbol}&interval={interval}&time_period=20&series_type=close&apikey={ALPHA_VANTAGE_API_KEY}"
            bb_response = requests.get(bb_url)
            if bb_response.status_code == 200:
                bb_data = bb_response.json()
                bb_values = bb_data.get('Technical Analysis: BBANDS', {})
                if bb_values:
                    latest_date = max(bb_values.keys())
                    bb_info = bb_values[latest_date]
                    indicators['bb_upper'] = float(bb_info['Real Upper Band'])
                    indicators['bb_middle'] = float(bb_info['Real Middle Band'])
                    indicators['bb_lower'] = float(bb_info['Real Lower Band'])
            
            # Volume (для акций)
            if asset_type == 'stock':
                volume_url = f"https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&symbol={symbol}&apikey={ALPHA_VANTAGE_API_KEY}"
                volume_response = requests.get(volume_url)
                if volume_response.status_code == 200:
                    volume_data = volume_response.json()
                    daily_data = volume_data.get('Time Series (Daily)', {})
                    if daily_data:
                        latest_date = max(daily_data.keys())
                        day_data = daily_data[latest_date]
                        indicators['volume'] = int(day_data['5. volume'])
                        indicators['price'] = float(day_data['4. close'])
            
            return indicators
            
        except Exception as e:
            logger.error(f"Error getting technical indicators for {symbol}: {e}")
            return {}

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