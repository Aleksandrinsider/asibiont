message = 'Часовой пояс Europe/Moscow'
message_lower = message.lower().strip()
timezone_keywords = ["часовой пояс", "timezone", "временная зона"]

print('Message lower:', repr(message_lower))
print('Keywords:', timezone_keywords)

for keyword in timezone_keywords:
    contains = keyword in message_lower
    print(f'  "{keyword}" in message: {contains}')

any_match = any(keyword in message_lower for keyword in timezone_keywords)
print('Any match:', any_match)

# Проверяем регулярное выражение
import re
timezone_match = re.search(r'(Europe/\w+|UTC[+-]\d+|GMT[+-]\d+)', message_lower)
print('Regex match:', timezone_match is not None)
if timezone_match:
    print('Matched:', timezone_match.group(1))