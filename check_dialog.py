import json

with open('conversation_log_light.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

print('Пример диалога LIGHT:')
for i, msg in enumerate(data[:3]):
    print(f'\nШаг {i+1}:')
    print(f'👤: {msg["user"]}')
    print(f'🤖: {msg["agent"][:200]}...')
    print(f'🔧: {msg["tools"]}')