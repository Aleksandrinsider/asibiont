import requests
import json
from config import DEEPSEEK_API_KEY

def analyze_prompt_with_deepseek():
    # Read the prompts.py file
    with open('ai_integration/prompts.py', 'r', encoding='utf-8') as f:
        prompt_content = f.read()

    # Prepare the request to DeepSeek
    url = "https://api.deepseek.com/v1/chat/completions"  # Assuming this is the endpoint
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }

    data = {
        "model": "deepseek-chat",  # Or appropriate model
        "messages": [
            {
                "role": "user",
                "content": f"Проанализируй этот системный промпт для AI агента. Оцени его качество, структуру, потенциальные проблемы и предложения по улучшению. Промпт:\n\n{prompt_content}"
            }
        ],
        "max_tokens": 2000
    }

    # Send the request
    response = requests.post(url, headers=headers, json=data)

    if response.status_code == 200:
        result = response.json()
        analysis = result['choices'][0]['message']['content']
        print("Анализ промпта от DeepSeek:")
        print(analysis)
    else:
        print(f"Ошибка при запросе к DeepSeek: {response.status_code}")
        print(response.text)

if __name__ == "__main__":
    analyze_prompt_with_deepseek()