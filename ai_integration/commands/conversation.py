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
        
        # Get user timezone and current time
        user = db_session.query(User).filter_by(telegram_id=user_id).first()
        user_timezone = user.timezone if user and user.timezone else 'Europe/Moscow'
        
        try:
            tz = pytz.timezone(user_timezone)
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
                
            print(f"DEBUG: user_timezone={user_timezone}, user_now={user_now}, current_time_str={current_time_str}, time_of_day={time_of_day}")
                
        except Exception as e:
            # Fallback to Moscow time
            moscow_tz = pytz.timezone('Europe/Moscow')
            user_now = datetime.now(moscow_tz)
            current_time_str = user_now.strftime('%H:%M')
            current_date_str = user_now.strftime('%d.%m.%Y')
            time_of_day = "время"  # Generic fallback
            print(f"DEBUG FALLBACK: current_time_str={current_time_str}, time_of_day={time_of_day}")
        
        # Get user context for personalized response
        context = get_context_from_db(user_id, limit=5)
        
        # Create a conversational prompt with time awareness
        conversation_prompt = f"""Ты - ASI Biont, дружелюбный AI-помощник для управления задачами.

⏰ ТЕКУЩЕЕ ВРЕМЯ: {current_time_str} ({time_of_day}) | 📅 СЕГОДНЯ: {current_date_str}

ОБЯЗАТЕЛЬНО ИСПОЛЬЗУЙ УКАЗАННОЕ ТЕКУЩЕЕ ВРЕМЯ В СВОИХ ОТВЕТАХ! НЕ ПРИДУМЫВАЙ ДРУГОЕ ВРЕМЯ!

ПОЛЬЗОВАТЕЛЬ: {self.message}

Это ОБЩИЙ РАЗГОВОР или ПРИВЕТСТВИЕ. НЕ ИСПОЛЬЗУЙ ИНСТРУМЕНТЫ! Просто ответь естественно и дружелюбно.

Правила:
- На приветствия отвечай приветствием с учетом времени суток
- На "кто ты" расскажи кратко о себе
- На "что ты умеешь" перечисли основные возможности
- Будь краток и дружелюбен
- НЕ предлагай создать задачи
- НЕ вызывай инструменты
- Учитывай время суток в ответах (утро/день/вечер/ночь)
- Разделяй предложения на абзацы для лучшей читаемости
- Используй переносы строк между логическими блоками
- ОБЯЗАТЕЛЬНО УПОМИНАЙ ТЕКУЩЕЕ ВРЕМЯ В ОТВЕТЕ, ЕСЛИ ЭТО ПРИВЕТСТВИЕ

Ответь на сообщение пользователя:"""

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
                            greeting = f"Привет! 😊 Смотрю, сейчас {current_time_str} ({time_of_day})."
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
                            return f"Приятно пообщаться! 😊 Сейчас {current_time_str} ({time_of_day}). Чем могу помочь с задачами или поиском единомышленников?"
        
        except Exception as e:
            # Final fallback
            return "Привет! Я здесь, чтобы помочь с задачами и найти тебе единомышленников. Что планируешь?"