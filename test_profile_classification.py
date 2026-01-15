import asyncio
from improved_prompts_final import ai_classify_intent
from config import DEEPSEEK_API_KEY

async def test_profile_updates():
    print('🔍 ТЕСТИРОВАНИЕ КЛАССИФИКАЦИИ ОБНОВЛЕНИЯ ПРОФИЛЯ')
    print('=' * 50)

    test_messages = [
        'Мой город Москва',
        'Я разработчик',
        'Работаю в IT компании',
        'Я из Санкт-Петербурга',
        'Мои навыки - Python, JavaScript',
        'Интересуюсь машинным обучением',
        'Моя компания называется TechCorp',
    ]

    for msg in test_messages:
        intent = await ai_classify_intent(msg, api_key=DEEPSEEK_API_KEY)
        status = '✅' if intent['type'] == 'update_profile' else '❌'
        print(f'{status} \"{msg}\" → {intent["type"]} ({intent["confidence"]:.2f})')

if __name__ == "__main__":
    asyncio.run(test_profile_updates())