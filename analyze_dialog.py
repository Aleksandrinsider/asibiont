"""Анализ диалога из лога"""
import re
from collections import Counter

with open('test_agent_dialog.log', 'r', encoding='utf-8') as f:
    log = f.read()

# Все ответы агента
agent_lines = re.findall(r'\[AGENT (\d+)\] (.+?)(?=\n\d{4}-)', log, re.DOTALL)

print('=' * 60)
print('  ГЛУБОКИЙ АНАЛИЗ ДИАЛОГА (20 ходов)')
print('=' * 60)

# 1. Начала ответов
print('\n📝 НАЧАЛА ОТВЕТОВ:')
starts_2w = []
for turn, text in agent_lines:
    first_line = text.strip().split('\n')[0][:80]
    words = text.strip().split()[:3]
    start = ' '.join(words).lower().rstrip('!.,')
    starts_2w.append(start)
    print(f'  [{turn:>2}] {first_line}')

print('\n📊 ПОВТОРЯЮЩИЕСЯ НАЧАЛА:')
for s, c in Counter(starts_2w).most_common():
    if c >= 2:
        print(f'  ❌ "{s}" — {c}x')
no_repeats = all(c < 2 for c in Counter(starts_2w).values())
if no_repeats:
    print('  ✅ Все начала уникальные')

# 2. Bold markdown **
print('\n🔤 BOLD MARKDOWN (**):')
bold_total = 0
for turn, text in agent_lines:
    bolds = re.findall(r'\*\*[^*]+\*\*', text)
    if bolds:
        bold_total += 1
        examples = [b[:30] for b in bolds[:3]]
        print(f'  ❌ [{turn}] {len(bolds)} bold найдено: {examples}')
if bold_total == 0:
    print('  ✅ Нет bold markdown')
else:
    print(f'  ⚠️  {bold_total}/{len(agent_lines)} ответов с bold')

# 3. Длина ответов
print('\n📏 ДЛИНА ОТВЕТОВ:')
lengths = []
for turn, text in agent_lines:
    length = len(text.strip())
    lengths.append(length)
avg_len = sum(lengths) / len(lengths)
print(f'  Средняя: {avg_len:.0f} символов')
print(f'  Мин: {min(lengths)} / Макс: {max(lengths)}')
short = sum(1 for l in lengths if l < 50)
long = sum(1 for l in lengths if l > 1000)
print(f'  Слишком короткие (<50): {short}')
print(f'  Развёрнутые (>1000): {long}')

# 4. Запрещённые начала
print('\n🚫 ЗАПРЕЩЁННЫЕ НАЧАЛА:')
banned = ['отлично', 'класс', 'хорош', 'хороший', 'понял', 'готов', 'создал', 'добавил']
banned_count = 0
for turn, text in agent_lines:
    first_word = text.strip().split()[0].lower().rstrip('!.,') if text.strip() else ''
    for b in banned:
        if first_word.startswith(b):
            banned_count += 1
            print(f'  ❌ [{turn}] Начинается с "{first_word}"')
            break
if banned_count == 0:
    print('  ✅ Нет запрещённых начал')
else:
    print(f'  ⚠️  {banned_count} ответов с запрещёнными началами')

# 5. Эмодзи
print('\n😀 ЭМОДЗИ:')
emoji_pattern = re.compile(r'[\U0001F300-\U0001F9FF\u2600-\u27BF\u2700-\u27BF]')
emoji_counts = []
for turn, text in agent_lines:
    emojis = emoji_pattern.findall(text)
    emoji_counts.append(len(emojis))
avg_emoji = sum(emoji_counts) / len(emoji_counts)
no_emoji = sum(1 for c in emoji_counts if c == 0)
too_many = sum(1 for c in emoji_counts if c > 5)
print(f'  Среднее кол-во эмодзи: {avg_emoji:.1f}')
print(f'  Без эмодзи: {no_emoji}/{len(agent_lines)}')
print(f'  Много (>5): {too_many}/{len(agent_lines)}')

# 6. Пассивность
print('\n🔇 ПАССИВНЫЕ ОТВЕТЫ (предлагает но не делает):')
passive_phrases = ['могу ', 'я могу', 'могу помочь', 'могу подсказать', 'хочешь, я', 'если хочешь']
passive_count = 0
for turn, text in agent_lines:
    lower = text.lower()
    if any(p in lower for p in passive_phrases):
        passive_count += 1
        phrase = next(p for p in passive_phrases if p in lower)
        print(f'  ⚠️  [{turn}] содержит "{phrase}"')
if passive_count == 0:
    print('  ✅ Нет пассивных ответов')

# 7. Экспертность — цифры и конкретика
print('\n📊 ЭКСПЕРТНОСТЬ (цифры и конкретика):')
has_numbers = 0
for turn, text in agent_lines:
    nums = re.findall(r'\d+[%$€₽]|\$\d+|\d+\s*(%|процент|долл|руб)', text)
    if nums:
        has_numbers += 1
print(f'  Ответов с цифрами/метриками: {has_numbers}/{len(agent_lines)} ({100*has_numbers/len(agent_lines):.0f}%)')

# 8. research_topic
print('\n🔍 RESEARCH USAGE:')
research = re.findall(r"'research_topic'", log)
print(f'  Вызвано research_topic: {len(research)}x')
double_research = log.count("research_topic: 2x") + log.count("research_topic'] ") 
# Check CLEAN entries
clean_entries = re.findall(r'\[CLEAN\]', log)
print(f'  Очистка applied: {len(clean_entries)}x')

# 9. Итоговая таблица
print(f'\n{"="*60}')
print(f'  📊 СВОДКА')
print(f'{"="*60}')
checks = [
    ('Нет bold **', bold_total == 0),
    ('Нет запрещённых начал', banned_count <= 1),
    ('Разнообразные начала', no_repeats or sum(1 for c in Counter(starts_2w).values() if c >= 3) == 0),
    ('Есть эмодзи', avg_emoji >= 0.5),
    ('Не слишком много эмодзи', too_many <= 3),
    ('Экспертные ответы', has_numbers >= len(agent_lines) * 0.3),
    ('Нет пассивных', passive_count <= 2),
    ('Развёрнутые ответы', avg_len > 200),
]
score = 0
for label, passed in checks:
    icon = '✅' if passed else '❌'
    print(f'  {icon} {label}')
    if passed:
        score += 1
print(f'\n  🏆 {score}/{len(checks)} ({100*score/len(checks):.0f}%)')
