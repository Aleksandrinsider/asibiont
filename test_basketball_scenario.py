import asyncio
from ai_integration.chat import chat_with_ai
import logging

logging.basicConfig(level=logging.INFO)

async def test_basketball_scenario():
    """Тест сценария пользователя с баскетболом"""
    message = 'напомни забрать сына с баскетбола'
    user_id = 123456789

    print('🧪 Тестирую сценарий: "напомни забрать сына с баскетбола"')
    print('=' * 60)

    try:
        result = await chat_with_ai(message, user_id=user_id)
        print(f'🤖 Ответ AI: {result[:300]}...')

        # Анализ ответа
        issues = []

        # Проверяем маркеры
        if 'NEED_TIME_FOR_TASK' in result:
            issues.append("Маркер NEED_TIME_FOR_TASK в ответе")

        # Проверяем шаблонные фразы
        if 'На какое время поставить задачу' in result:
            issues.append("Шаблонный вопрос о времени")

        # Проверяем естественность
        natural_time_questions = ['Во сколько', 'Когда', 'В какое время']
        has_natural_question = any(q.lower() in result.lower() for q in natural_time_questions)

        if not has_natural_question and 'НУЖНО_ВРЕМЯ_ДЛЯ_ЗАДАЧИ' in str(result):
            issues.append("Нет естественного вопроса о времени")

        # Результаты
        if issues:
            print(f'❌ Проблемы: {len(issues)}')
            for issue in issues:
                print(f'   - {issue}')
        else:
            print('✅ OK - естественный ответ')

        return result

    except Exception as e:
        print(f'❌ Ошибка: {e}')
        return None

if __name__ == "__main__":
    asyncio.run(test_basketball_scenario())