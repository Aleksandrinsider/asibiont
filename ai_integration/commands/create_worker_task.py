from .base_command import BaseCommand
from models import Session, Task, User, SubscriptionTier
from reminder_service import REMINDER_SERVICE
from datetime import datetime, timedelta
import logging
import asyncio
import requests
from subscription_service import check_subscription
from config import OPENWEATHERMAP_API_KEY

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

            # Проверяем тариф - только PREMIUM
            user = db_session.query(User).filter_by(telegram_id=user_id).first()
            if not user:
                return "Пользователь не найден"
            
            if user.subscription_tier != SubscriptionTier.PREMIUM:
                return "Функция фоновых задач доступна только на тарифе PREMIUM. Обновите подписку для использования этой возможности."

            # Проверяем минимальный интервал - не чаще раза в день
            if interval_minutes < 1440:
                interval_minutes = 1440
                logger.info(f"Adjusted interval to minimum 1440 minutes for user {user_id}")

            # Проверяем, что у пользователя нет уже worker'а
            existing_worker = db_session.query(Task).filter(
                Task.user_id == user.id,
                Task.title.like("Worker:%")
            ).first()
            
            if existing_worker:
                return "У вас уже настроена фоновая задача. Вы можете иметь только одну фоновую задачу. Удалите существующую перед созданием новой."

            # Создаем задачу в БД для отслеживания
            worker_task = Task(
                title=f"Worker: {task_description}",
                description=f"Фоновая задача: {action}, интервал {interval_minutes} мин, порог {threshold}, город {city}, условие {weather_condition}",
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
                    args=[user_id, action, threshold, worker_task.id, city, weather_condition],
                    replace_existing=True
                )
                logger.info(f"Worker task created: {job_id}")

            return f"Фоновая задача создана: {task_description}. Будет выполняться каждые {interval_minutes // 60} часов (минимум раз в день)."

        except Exception as e:
            logger.error(f"Error creating worker task: {e}")
            return f"Ошибка при создании фоновой задачи: {str(e)}"

    async def _execute_worker_action(self, user_id, action, threshold, task_id, city='Moscow', weather_condition=''):
        try:
            if action == 'monitor_gold_market':
                await self._monitor_gold_market(user_id, threshold, task_id)
            elif action == 'monitor_weather':
                await self._monitor_weather(user_id, threshold, task_id, city, weather_condition)
            # Можно добавить другие действия
        except Exception as e:
            logger.error(f"Error executing worker action {action}: {e}")

    async def _monitor_gold_market(self, user_id, threshold, task_id):
        try:
            # Используем API для получения цены золота
            # Пример: https://www.goldapi.io/ (нужен API key)
            # Или https://metals-api.com/ (бесплатный tier доступен)
            # Для демо используем placeholder
            api_url = "https://api.metals-api.com/v1/latest?access_key=YOUR_API_KEY&base=USD&symbols=XAU"
            response = requests.get(api_url)
            if response.status_code == 200:
                data = response.json()
                current_price = data.get('rates', {}).get('XAU', 0)  # Цена золота в USD за унцию
                if current_price and current_price < threshold:
                    # Отправляем уведомление пользователю
                    if REMINDER_SERVICE and REMINDER_SERVICE.bot:
                        message = f"🎉 Хорошая возможность для покупки золота! Текущая цена: ${current_price:.2f} за унцию, ниже порога ${threshold}"
                        await REMINDER_SERVICE.bot.send_message(chat_id=user_id, text=message)
                        logger.info(f"Gold market alert sent to user {user_id}: price {current_price}")
                    else:
                        logger.error("Bot not available for sending gold market alert")
            else:
                logger.warning(f"Failed to fetch gold price: {response.status_code}, response: {response.text}")

        except Exception as e:
            logger.error(f"Error monitoring gold market: {e}")

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