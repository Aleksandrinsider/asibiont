import requests
from config import DEEPSEEK_API_KEY

SYSTEM_PROMPT = "You are a helpful AI assistant for task management in Telegram. You can add, list, complete tasks, set reminders, and chat with users. Remember context, be polite, and assist with task-related queries."

def chat_with_ai(message, context=None):
    url = "https://api.deepseek.com/v1/chat/completions"  # Предполагаемый URL, нужно проверить
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if context:
        messages.append({"role": "assistant", "content": context})
    messages.append({"role": "user", "content": message})
    
    data = {
        "model": "deepseek-chat",
        "messages": messages
    }
    response = requests.post(url, headers=headers, json=data)
    if response.status_code == 200:
        return response.json()["choices"][0]["message"]["content"]
    else:
        return "Извините, не могу ответить сейчас."