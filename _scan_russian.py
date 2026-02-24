import re

with open(r'ai_integration\handlers.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

russian_re = re.compile(r'[\u0400-\u04FF]')
results = []

for i, line in enumerate(lines, 1):
    stripped = line.strip()
    if not russian_re.search(stripped):
        continue
    # Skip pure comments
    if stripped.startswith('#'):
        continue
    # Skip logger lines
    if 'logger.' in stripped:
        continue
    # Skip docstrings
    if stripped.startswith('"""') or stripped.startswith("'''"):
        continue
    # Check for Russian text inside string literals (single or double quoted)
    has_string_russian = False
    # Find all string literals
    str_matches = re.findall(r'''(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')''', stripped)
    for s in str_matches:
        if russian_re.search(s):
            has_string_russian = True
            break
    if has_string_russian:
        results.append(f'L{i}: {stripped[:250]}')

with open('_scan_results.txt', 'w', encoding='utf-8') as out:
    out.write('\n'.join(results))
