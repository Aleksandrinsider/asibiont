from .base_command import BaseCommand
from ..chat import chat_with_ai  # Import existing chat processing
from ai_integration.utils import get_context_from_db  # Import context loading
from ai_integration.memory import decrypt_data
import pytz
from datetime import datetime
from models import User
import re

class ConversationCommand(BaseCommand):
    async def execute(self, user, db_session):
        """Handle conversational messages without tool calls"""
        # Get user timezone and message time FIRST
        user_timezone = user.timezone if user and user.timezone else 'Europe/Moscow'
        
        try:
            tz = pytz.timezone(user_timezone)
            # Use message time if available, otherwise current UTC time
            base_time = self.message_time if self.message_time else datetime.utcnow().replace(tzinfo=pytz.UTC)
            user_now = base_time.astimezone(tz)
                
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
                
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"[CONVERSATION] user_timezone={user_timezone}, message_time={self.message_time}, user_now={user_now}, current_time_str={current_time_str}, time_of_day={time_of_day}")
                
        except Exception as e:
            # Fallback to Moscow time
            moscow_tz = pytz.timezone('Europe/Moscow')
            base_time = self.message_time if self.message_time else datetime.utcnow().replace(tzinfo=pytz.UTC)
            user_now = base_time.astimezone(moscow_tz)
            current_time_str = user_now.strftime('%H:%M')
            current_date_str = user_now.strftime('%d.%m.%Y')
            time_of_day = "время"  # Generic fallback
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"[CONVERSATION FALLBACK] message_time={self.message_time}, current_time_str={current_time_str}, time_of_day={time_of_day}, error={e}")
        
        # For simple greetings, return a fixed response to avoid hallucinations
        message_lower = self.message.lower().strip()
        
        # Check if message starts with or contains simple greetings
        greeting_keywords = ['привет', 'hi', 'hello', 'здравствуй', 'hey', 'добрый', 'доброе', 'доброго']
        is_greeting = any(message_lower.startswith(greet) or greet in message_lower.split()[:2] for greet in greeting_keywords)
        
        if is_greeting and len(message_lower.split()) <= 3:  # Simple greeting, not complex message
            # Simple greeting - return fixed response
            print(f"[CONVERSATION] Fixed response for greeting: {self.message}")
            return f"Привет! 😊 Сейчас {current_time_str} ({time_of_day})."
        
        # For other conversation, use AI but with strict controls
        print(f"[CONVERSATION] Using AI for: {self.message}")
        
        # Get user memory
        user_memory = ""
        if user and user.memory:
            try:
                decrypted = decrypt_data(user.memory)
                user_memory = f"\nИнформация о пользователе: {decrypted}"
            except Exception as e:
                logger.warning(f"[CONVERSATION] Could not decrypt user memory: {e}")
                user_memory = ""
        
        # Get user context for personalized response (DISABLED for performance)
        # context = get_context_from_db(user.telegram_id, limit=3)  # Disabled to improve speed
        context = []  # Empty context for faster responses
        
        # Create a conversational prompt with time awareness and anti-hallucination rules
        conversation_prompt = f"""Ты - ASI Biont, дружелюбный AI-помощник для управления задачами.

Текущее время: {current_time_str} ({time_of_day})
Дата: {current_date_str}

{user_memory}

Правила для ответов:
1. Всегда используй ТОЛЬКО указанное время {current_time_str} ({time_of_day}) - не придумывай другое время
2. Используй ТОЛЬКО указанную дату {current_date_str} - не придумывай другие даты
3. Не выдумывай информацию о пользователе - используй только данные из раздела "Информация о пользователе"
4. Не упоминай задачи, контакты или профиль, если об этом не спрашивают
5. Отвечай кратко и по делу на вопрос пользователя
6. Будь дружелюбным и полезным

Сообщение пользователя: {self.message}

Ответь естественно и дружелюбно."""

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
                "temperature": 0.0,
                "max_tokens": 70  # Reduced from 100 for faster responses
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=8)) as response:
                    if response.status == 200:
                        result = await response.json()
                        content = result["choices"][0]["message"]["content"].strip()
                        # Remove formatting as per requirements
                        lines = content.split('\n')
                        cleaned_lines = []
                        for line in lines:
                            line = line.strip()
                            # Remove markdown formatting
                            line = re.sub(r'\*\*(.*?)\*\*', r'\1', line)  # Bold
                            line = re.sub(r'\*(.*?)\*', r'\1', line)  # Italic
                            line = re.sub(r'#{1,6}\s*', '', line)  # Headers
                            if line.startswith(('•', '-', '*', '1.', '2.', '3.', '4.', '5.', '6.', '7.', '8.', '9.')):
                                line = line[1:].strip()  # Remove list marker
                            cleaned_lines.append(line)
                        content = ' '.join(cleaned_lines).strip()
                        content = re.sub(r'\s+', ' ', content)  # Clean extra spaces
                        # Limit response length to 2-4 paragraphs
                        sentences = content.split('. ')
                        if len(sentences) > 10:  # Roughly limit to avoid too long
                            content = '. '.join(sentences[:10]) + '.'
                        return content
                        return content
                    else:
                        # Fallback responses (only for non-greeting/time messages)
                        if "кто ты" in self.message.lower() or "ты кто" in self.message.lower():
                            return "Я ASI Biont - умный AI-помощник, который помогает людям находить единомышленников через их дела и задачи.\n\nМогу создавать напоминания, искать партнеров для активностей и многое другое!"
                        elif "что ты умеешь" in self.message.lower():
                            return "Я помогаю управлять задачами, создавать напоминания, искать единомышленников для совместных активностей и организовывать расписание. Расскажи, что планируешь, и я помогу!"
                        else:
                            return f"Приятно пообщаться! 😊 Сообщение отправлено в {current_time_str} ({time_of_day}). Чем могу помочь с задачами или поиском единомышленников?"
        
        except Exception as e:
            # Final fallback
            return "Привет! Я здесь, чтобы помочь с задачами и найти тебе единомышленников. Что планируешь?"

