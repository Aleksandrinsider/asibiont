import html as html_mod
import re

def _format_html(text):
    url_re = re.compile(r'(https?://\S+)')
    text = re.sub(r'(?<=[^\s\n])(https?://)', r' \1', text)
    parts = url_re.split(text)
    result = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            result.append(html_mod.escape(part))
        else:
            clean = part.rstrip('.,;:!?)—»')
            trailing = part[len(clean):]
            result.append(f'<a href="{html_mod.escape(clean)}">{html_mod.escape(clean)}</a>{html_mod.escape(trailing)}')
    return ''.join(result)

# Test cases
tests = [
    'Ссылка на ленту: https://asibiont.com/dashboard — можешь перейти.',
    'Посмотри https://asibiont.com/dashboard и скажи что думаешь',
    'Вот:https://asibiont.com/dashboard',
    'Нет ссылок тут, только текст с < и > символами',
]

for t in tests:
    print(f'IN:  {t}')
    print(f'OUT: {_format_html(t)}')
    print()
