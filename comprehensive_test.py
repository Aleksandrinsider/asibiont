# Расширенный тест агента с реальным сценарием взаимодействия
import asyncio
from ai_integration.chat import chat_with_ai

async def comprehensive_agent_test():
    print('🧪 ПОЛНОЕ ТЕСТИРОВАНИЕ АГЕНТА')
    print('=' * 60)

    # Используем реальный user_id из логов (1001)
    real_user_id = 1001

    print('\n🎭 СИМУЛЯЦИЯ РЕАЛЬНОГО ДИАЛОГА')
    print('Пользователь: Создай задачу проверить почту')

    # Шаг 1: Создание задачи без времени
    step1 = await chat_with_ai('Создай задачу проверить почту', user_id=real_user_id)
    print(f'Агент: {step1}')

    # Шаг 2: Указываем время
    print('\nПользователь: через 5 минут')
    step2 = await chat_with_ai('через 5 минут', user_id=real_user_id)
    print(f'Агент: {step2}')

    # Шаг 3: Даем информацию о профиле
    print('\nПользователь: я директор в ASI Biont, компания занимается разработками в области ИИ')
    step3 = await chat_with_ai('я директор в ASI Biont, компания занимается разработками в области ИИ', user_id=real_user_id)
    print(f'Агент: {step3}')

    # Шаг 4: Подтверждаем обновление профиля
    print('\nПользователь: да обнови')
    step4 = await chat_with_ai('да обнови', user_id=real_user_id)
    print(f'Агент: {step4}')

    # Шаг 5: Просим показать задачи
    print('\nПользователь: покажи мои задачи')
    step5 = await chat_with_ai('покажи мои задачи', user_id=real_user_id)
    print(f'Агент: {step5}')

    # Шаг 6: Завершаем задачу
    print('\nПользователь: я проверил почту')
    step6 = await chat_with_ai('я проверил почту', user_id=real_user_id)
    print(f'Агент: {step6}')

    print('\n' + '=' * 60)
    print('📊 АНАЛИЗ РЕЗУЛЬТАТОВ:')

    # Анализ всех ответов
    all_responses = [step1, step2, step3, step4, step5, step6]

    # 1. Проверка длины ответов
    lengths = [len(r) for r in all_responses]
    avg_length = sum(lengths) / len(lengths)
    print(f'Средняя длина ответа: {avg_length:.0f} символов')

    if avg_length > 800:
        print('❌ Ответы слишком длинные')
    elif avg_length < 50:
        print('❌ Ответы слишком короткие')
    else:
        print('✅ Длина ответов оптимальная')

# 2. Проверка форматирования - более точная проверка
    all_text = ''.join(all_responses)
    
    # Проверяем на явные маркеры списков
    explicit_lists = ['1.', '2.', '3.', '4.', '5.', '6.', '7.', '8.', '9.', '0.']
    explicit_markers = ['-', '*', '•']
    
    has_explicit_numbers = any(marker in all_text for marker in explicit_lists)
    has_explicit_markers = any(marker in all_text for marker in explicit_markers)
    
    # Проверяем на паттерны типа "Что хочешь сделать сейчас: создать задачу... или найти партнера..."
    has_colon_enumeration = ':' in all_text and ('или' in all_text or 'либо' in all_text)
    
    has_lists = has_explicit_numbers or has_explicit_markers or has_colon_enumeration
    
    print(f'Явные маркеры списков (1., -, *): {"❌ ДА" if (has_explicit_numbers or has_explicit_markers) else "✅ НЕТ"}')
    print(f'Перечисления через двоеточие: {"❌ ДА" if has_colon_enumeration else "✅ НЕТ"}')
    print(f'Общий результат списков: {"❌ ДА" if has_lists else "✅ НЕТ"}')

    # 3. Проверка соблюдения требований
    profile_updates = sum(1 for r in all_responses if 'обновляю' in r.lower() or 'обновил' in r.lower())
    print(f'Автоматических обновлений профиля: {profile_updates}')

    if profile_updates > 0:
        print('❌ Агент обновляет профиль автоматически')
    else:
        print('✅ Агент НЕ обновляет профиль автоматически')

    # 4. Проверка количества абзацев
    paragraphs = sum(r.count('\n\n') + 1 for r in all_responses) / len(all_responses)
    print(f'Среднее количество абзацев: {paragraphs:.1f}')

    if paragraphs > 4:
        print('❌ Слишком много абзацев')
    else:
        print('✅ Количество абзацев в норме')

    print('\n🏁 ТЕСТИРОВАНИЕ ЗАВЕРШЕНО')

if __name__ == "__main__":
    asyncio.run(comprehensive_agent_test())