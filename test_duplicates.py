from ai_integration import detect_duplicates

# Тестируем обнаружение дубликатов с более очевидными случаями
tasks = [
    {'title': 'Подготовить презентацию'},
    {'title': 'Подготовить презентацию'},
    {'title': 'Подготовить презентацию для клиента'},
    {'title': 'Написать отчет'},
    {'title': 'Написать отчет'},
    {'title': 'Позвонить клиенту'},
    {'title': 'Позвонить заказчику'}
]

duplicates = detect_duplicates(tasks)
print('=== Тестирование detect_duplicates ===')
print(f'Задачи:')
for i, task in enumerate(tasks):
    print(f'  {i+1}. {task["title"]}')

print(f'\nНайдено проблем: {len(duplicates)}')
for dup in duplicates:
    print(f'  - {dup}')