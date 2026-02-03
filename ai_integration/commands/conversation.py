from .base_command import BaseCommand
from ..chat import chat_with_ai  # Import existing chat processing
from ai_integration.utils import get_context_from_db  # Import context loading
import pytz
from datetime import datetime
from models import User

class ConversationCommand(BaseCommand):
    async def execute(self, user_id, db_session):
        """Handle conversational messages without tool calls"""
        # For conversation, we need to respond naturally without calling tools
        # But we still want to use AI for natural responses
        
        # Get user timezone and message time
        user = db_session.query(User).filter_by(telegram_id=user_id).first()
        user_timezone = user.timezone if user and user.timezone else 'Europe/Moscow'
        
        try:
            tz = pytz.timezone(user_timezone)
            # Use message time instead of current time
            if self.message_time:
                # message.date is in UTC, convert to user timezone
                utc_time = self.message_time.replace(tzinfo=pytz.UTC)
                user_now = utc_time.astimezone(tz)
            else:
                # Fallback to current time if no message time
                user_now = datetime.now(tz)
                
            current_time_str = user_now.strftime('%H:%M')
            current_date_str = user_now.strftime('%d.%m.%Y')
            
            # Определяем время суток для более естественного ответа
            hour = user_now.hour
            if 6 <= hour < 12:
                time_of_day = "утро"
            elif 12 <= hour < 18:
                time_of_day = "день"
            elif 18 <= hour < 22:
                time_of_day = "вечер"
            else:
                time_of_day = "ночь"
                
            print(f"DEBUG: user_timezone={user_timezone}, message_time={self.message_time}, user_now={user_now}, current_time_str={current_time_str}, time_of_day={time_of_day}")
                
        except Exception as e:
            # Fallback to Moscow time
            moscow_tz = pytz.timezone('Europe/Moscow')
            if self.message_time:
                utc_time = self.message_time.replace(tzinfo=pytz.UTC)
                user_now = utc_time.astimezone(moscow_tz)
            else:
                user_now = datetime.now(moscow_tz)
            current_time_str = user_now.strftime('%H:%M')
            current_date_str = user_now.strftime('%d.%m.%Y')
            time_of_day = "время"  # Generic fallback
            print(f"DEBUG FALLBACK: message_time={self.message_time}, current_time_str={current_time_str}, time_of_day={time_of_day}, error={e}")
        
        # Get user context for personalized response
        context = get_context_from_db(user_id, limit=5)
        
        # Create a conversational prompt with time awareness
        conversation_prompt = f"""Ты - ASI Biont, дружелюбный AI-помощник для управления задачами.

Время отправки сообщения пользователем: {current_time_str} ({time_of_day})
Дата: {current_date_str}

ВАЖНО: Используй ТОЛЬКО указанное время отправки сообщения ({current_time_str}, {time_of_day}), не текущее время сервера или свое знание времени.

Сообщение пользователя: {self.message}

Это обычный разговор или приветствие. Ответь естественно и дружелюбно, без использования инструментов.

Рекомендации:
- Если это приветствие, поздоровайся с учётом времени отправки сообщения (сейчас {time_of_day}, {current_time_str})
- Если спрашивают о тебе, расскажи кратко о своих возможностях
- Будь кратким и дружелюбным
- Используй переносы строк для удобства чтения
- Не предлагай создать задачи в обычном разговоре"""

        try:
            # Use AI for natural conversation response
            import aiohttp
            from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
            
            url = "https://api.deepseek.com/v1/chat/completions"
            headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
            
            messages = [
                {"role": "system", "content": conversation_prompt}
            ]
            
            data = {
                "model": DEEPSEEK_MODEL,
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 100
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        result = await response.json()
                        content = result["choices"][0]["message"]["content"].strip()
                        return content
                    else:
                        # Fallback responses
                        if "привет" in self.message.lower() or "здравствуй" in self.message.lower():
                            greeting = f"Привет! 😊 Смотрю, сообщение отправлено в {current_time_str} ({time_of_day})."
                            if time_of_day == "утро":
                                return f"{greeting} Хорошего начала дня! Чем могу помочь с задачами?"
                            elif time_of_day == "день":
                                return f"{greeting} Как проходит день? Готов помочь с планированием!"
                            elif time_of_day == "вечер":
                                return f"{greeting} Добрый вечер! Что планируешь на вечер?"
                            else:
                                return f"{greeting} Поздний час, но я здесь, если нужна помощь!"
                        elif "кто ты" in self.message.lower() or "ты кто" in self.message.lower():
                            return "Я ASI Biont - умный AI-помощник, который помогает людям находить единомышленников через их дела и задачи.\n\nМогу создавать напоминания, искать партнеров для активностей и многое другое!"
                        elif "что ты умеешь" in self.message.lower():
                            return "Я умею:\n• Создавать задачи с напоминаниями\n• Искать людей для совместных активностей\n• Управлять твоим расписанием\n• Помогать находить единомышленников\n\nПросто расскажи, что планируешь!"
                        else:
                            return f"Приятно пообщаться! 😊 Сообщение отправлено в {current_time_str} ({time_of_day}). Чем могу помочь с задачами или поиском единомышленников?"
        
        except Exception as e:
            # Final fallback
            return "Привет! Я здесь, чтобы помочь с задачами и найти тебе единомышленников. Что планируешь?"