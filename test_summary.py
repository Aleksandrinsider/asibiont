from ai_integration import summarize_conversation

# Тестируем резюмирование разговора
conversation = [
    {'role': 'user', 'content': 'Привет! Хочу создать мобильное приложение для фитнеса'},
    {'role': 'assistant', 'content': 'Отлично! Расскажите подробнее о вашем проекте'},
    {'role': 'user', 'content': 'Это будет приложение для отслеживания тренировок с ИИ-анализом'},
    {'role': 'assistant', 'content': 'Звучит интересно! Какие у вас навыки?'},
    {'role': 'user', 'content': 'Я дизайнер, но нужен разработчик и маркетолог'},
    {'role': 'assistant', 'content': 'Давайте найдем вам команду'}
]

summary = summarize_conversation(conversation)
print('=== Тестирование summarize_conversation ===')
print(f'Оригинальный разговор ({len(conversation)} сообщений):')
for msg in conversation:
    print(f'  {msg["role"]}: {msg["content"]}')
print(f'\nРезюме: {summary}')