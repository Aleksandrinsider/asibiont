import asyncio
from demo.fully_autonomous_agent import FullyAutonomousAgent

async def test_specific_commands():
    agent = FullyAutonomousAgent()
    user_id = 123456789

    # Тестируем конкретные команды из списка
    test_commands = [
        'Создай задачу "протестировать новый агент" на сегодня в 16:00',
        'Заверши задачу "протестировать новый агент"',
        'Создай повторяющуюся задачу "ежедневная зарядка" каждый день в 8:00',
        'Перенеси задачу "ежедневная зарядка" на 9:00',
        'Найди контакты для задачи по разработке мобильного приложения',
        'Обнови мой профиль: добавь навык Python разработка',
        'Запомни что я люблю чай с лимоном',
        'Получи детали задачи "ежедневная зарядка"',
        'Удалить все задачи'
    ]

    for i, cmd in enumerate(test_commands, 1):
        print(f'\n{"="*60}')
        print(f'КОМАНДА {i}: {cmd}')
        print(f'{"="*60}')

        try:
            response = await agent.process_request(cmd, user_id)
            print(f'ОТВЕТ: {response[:200]}...' if len(response) > 200 else f'ОТВЕТ: {response}')
        except Exception as e:
            print(f'ОШИБКА: {e}')

        print(f'📊 История действий: {len(agent.execution_history)}')

if __name__ == "__main__":
    asyncio.run(test_specific_commands())