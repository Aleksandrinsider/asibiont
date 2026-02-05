import re

with open('ai_integration/utils.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Найдем и исправим строки с нулевым отступом, которые должны иметь отступ
lines = content.split('\n')
for i in range(len(lines)):
    line = lines[i]
    stripped = line.strip()
    if stripped and not line.startswith(' ') and not line.startswith('\t') and not stripped.startswith('#') and not stripped.startswith('def ') and not stripped.startswith('class ') and not stripped.startswith('"""') and not stripped.startswith("'''"):
        # Найдем отступ предыдущей строки
        for j in range(i-1, -1, -1):
            prev_line = lines[j].strip()
            if prev_line and not prev_line.startswith('#'):
                # Возьмем отступ предыдущей строки
                prev_indent = lines[j][:len(lines[j]) - len(lines[j].lstrip())]
                lines[i] = prev_indent + stripped
                break

content = '\n'.join(lines)
with open('ai_integration/utils.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Fixed indentation')