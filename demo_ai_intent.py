import asyncio
from improved_prompts_final import ai_classify_intent
from config import DEEPSEEK_API_KEY

async def demo_improvements():
    print('🚀 Демонстрация AI-классификации намерений')
    print('=' * 50)

    test_cases = [
        ('Напомни забронировать столик в ресторане на вечер', 'add_task'),
        ('Готово с отчетом по продажам', 'complete_task'),
        ('Какие задачи на сегодня?', 'list_tasks'),
        ('Расскажи анекдот', 'chat'),
        ('Я из Санкт-Петербурга, работаю дизайнером', 'update_profile'),
        ('Передай @alex проверить код', 'delegate_task')
    ]

    for message, expected in test_cases:
        intent = await ai_classify_intent(message, api_key=DEEPSEEK_API_KEY)
        status = '✅' if intent['type'] == expected else '❌'
        print(f'{status} \"{message[:30]}...\" → {intent["type"]} (уверенность: {intent["confidence"]:.2f})')

if __name__ == "__main__":
    asyncio.run(demo_improvements())