import json

# Анализируем логи всех тарифов
tiers = ['light', 'standard']
for tier in tiers:
    try:
        with open(f'conversation_log_{tier}.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
            print(f'\n=== {tier.upper()} Тариф ===')
            print(f'Всего сообщений: {len(data)}')

            # Считаем инструменты
            total_tools = sum(len(msg.get('tools', [])) for msg in data)
            steps_with_tools = sum(1 for msg in data if msg.get('tools'))

            print(f'Вызовов инструментов: {total_tools}')
            print(f'Шагов с инструментами: {steps_with_tools}')

            # Анализируем качества
            business_focus = sum(1 for msg in data
                                if any(word in msg.get('agent', '').lower()
                                      for word in ['маркетинг', 'партнер', 'продвижени', 'рост', 'бизнес', 'клиент']))

            tech_expertise = sum(1 for msg in data
                                if any(word in msg.get('agent', '').lower()
                                      for word in ['ai', 'разработк', 'технолог', 'алгоритм', 'код']))

            proactivity = sum(1 for msg in data
                             if any(word in msg.get('agent', '').lower()
                                   for word in ['предлагаю', 'создадим', 'найдем', 'проанализируем', 'давай']))

            print(f'Бизнес-фокус: {business_focus}')
            print(f'Техническая экспертиза: {tech_expertise}')
            print(f'Проактивность: {proactivity}')
            print(f'Исполнение: {steps_with_tools}')

            # Итоговая оценка
            total_score = (business_focus + tech_expertise + proactivity + steps_with_tools) / 4
            print(f'ИТОГОВАЯ ОЦЕНКА: {total_score:.1f}/15')

    except FileNotFoundError:
        print(f'Файл conversation_log_{tier}.json не найден')