import requests
from config import DEEPSEEK_API_KEY

def chat_with_ai(message, context=None):
    url = "https://api.deepseek.com/v1/chat/completions"  # Предполагаемый URL, нужно проверить
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "deepseek-chat",  # Предполагаемая модель
        "messages": [{"role": "user", "content": message}]
    }
    if context:
        data["messages"].insert(0, {"role": "system", "content": context})

    response = requests.post(url, headers=headers, json=data)
    if response.status_code == 200:
        return response.json()["choices"][0]["message"]["content"]
    else:
        return "Извините, не могу ответить сейчас."