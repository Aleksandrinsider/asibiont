from .base_command import BaseCommand
from ..chat import chat_with_ai  # Import existing chat processing
from ai_integration.utils import get_context_from_db  # Import context loading

class ConversationCommand(BaseCommand):
    async def execute(self, user_id, db_session):
        """Handle conversational messages without tool calls"""
        # For conversation, we need to respond naturally without calling tools
        # But we still want to use AI for natural responses
        
        # Get user context for personalized response
        context = get_context_from_db(user_id, limit=5)
        
        # Create a simple conversational prompt that prevents tool calls
        conversation_prompt = f"""Ты - ASI Biont, дружелюбный AI-помощник для управления задачами.

ПОЛЬЗОВАТЕЛЬ: {self.message}

Это ОБЩИЙ РАЗГОВОР или ПРИВЕТСТВИЕ. НЕ ИСПОЛЬЗУЙ ИНСТРУМЕНТЫ! Просто ответь естественно и дружелюбно.

Правила:
- На приветствия отвечай приветствием
- На "кто ты" расскажи кратко о себе
- На "что ты умеешь" перечисли основные возможности
- Будь краток и дружелюбен
- НЕ предлагай создать задачи
- НЕ вызывай инструменты

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
                            return "Привет! 😊 Я ASI Biont, твой AI-помощник для управления задачами и поиска единомышленников."
                        elif "кто ты" in self.message.lower() or "ты кто" in self.message.lower():
                            return "Я ASI Biont - умный AI-помощник, который помогает людям находить единомышленников через их дела и задачи. Могу создавать напоминания, искать партнеров для активностей и многое другое!"
                        elif "что ты умеешь" in self.message.lower():
                            return "Я умею: создавать задачи с напоминаниями, искать людей для совместных активностей, управлять твоим расписанием, и помогать находить единомышленников. Просто расскажи, что планируешь!"
                        else:
                            return "Приятно пообщаться! 😊 Чем могу помочь с задачами или поиском единомышленников?"
        
        except Exception as e:
            # Final fallback
            return "Привет! Я здесь, чтобы помочь с задачами и найти тебе единомышленников. Что планируешь?"