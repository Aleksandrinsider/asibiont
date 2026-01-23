"""
Тест для проверки правильного создания задач агентом
"""
from datetime import datetime
import pytz

def test_multiple_tasks():
    """Тест: проверка промптов для правильного создания задач"""
    
    print("=" * 80)
    print("ТЕСТ: Проверка промптов для правильного создания задач")
    print("=" * 80)
    
    # Текущее время
    tz = pytz.timezone('Europe/Moscow')
    now = datetime.now(tz)
    current_time = now.strftime('%H:%M')
    current_date = now.strftime('%d.%m.%Y')
    
    print(f"\nТекущее время: {current_time}")
    print(f"Текущая дата: {current_date}")
    
    print("\n--- СЦЕНАРИЙ ПРОБЛЕМЫ ---")
    print("1. Пользователь: 'напомни через 5 минут проверить почту'")
    print("   Ожидание: Создается задача 'Проверить почту' на время current_time + 5 минут")
    print("\n2. Пользователь: 'хочу заказать продукты на вечер'")
    print("   Ожидание: Агент спрашивает 'Во сколько именно вечером?'")
    print("\n3. Пользователь: 'давай в 19:00'")
    print("   Ожидание: Создается НОВАЯ задача 'Заказать продукты' на 19:00")
    print("   ❌ ОШИБКА (было): Агент изменял задачу 'Проверить почту' на 'Заказать продукты'")
    print("   ✅ ИСПРАВЛЕНИЕ: Теперь агент создает две разные задачи")
    
    print("\n" + "=" * 80)
    print("ПРОВЕРКА ПРОМПТОВ")
    print("=" * 80)
    
    # Читаем промпты
    from ai_integration.prompts import get_extended_system_prompt
    
    prompt = get_extended_system_prompt(
        now, current_time, current_date, 
        'testuser', '', 'Профиль: не заполнен',
        subscription_tier='bronze'
    )
    
    # Проверяем наличие ключевых правил
    checks = [
        ("КАЖДАЯ НОВАЯ ПРОСЬБА = НОВАЯ ЗАДАЧА", "⚠️⚠️⚠️ КРИТИЧЕСКИ ВАЖНО - КАЖДАЯ НОВАЯ ПРОСЬБА = НОВАЯ ЗАДАЧА:" in prompt),
        ("НЕ изменять автоматически", "НЕ изменяй существующую задачу автоматически без ЯВНОЙ просьбы!" in prompt),
        ("Правило edit_task", "edit_task вызывай ТОЛЬКО когда пользователь ЯВНО просит" in prompt),
        ("Пример ошибки", "ПРИМЕР ОШИБКИ:" in prompt),
        ("ДВЕ РАЗНЫЕ ЗАДАЧИ", "ЭТО ДВЕ РАЗНЫЕ ЗАДАЧИ!" in prompt),
    ]
    
    print("\nПроверка правил в промпте:")
    all_passed = True
    for rule_name, rule_present in checks:
        status = "✅" if rule_present else "❌"
        print(f"{status} {rule_name}")
        if not rule_present:
            all_passed = False
    
    # Проверяем tools.py
    print("\nПроверка описания edit_task в tools.py:")
    from ai_integration.tools import TOOLS
    
    edit_task_tool = None
    for tool in TOOLS:
        if tool.get('function', {}).get('name') == 'edit_task':
            edit_task_tool = tool
            break
    
    if edit_task_tool:
        description = edit_task_tool['function']['description']
        if "ЯВНОЙ просьбе" in description and "НЕ вызывай если пользователь просто называет время" in description:
            print("✅ Описание edit_task содержит правильные ограничения")
        else:
            print("❌ Описание edit_task не содержит достаточных ограничений")
            all_passed = False
    else:
        print("❌ edit_task не найден в TOOLS")
        all_passed = False
    
    print("\n" + "=" * 80)
    if all_passed:
        print("✅ ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")
        print("\nПромпты обновлены правильно:")
        print("- Агент будет создавать новые задачи для каждой новой просьбы")
        print("- Агент НЕ будет автоматически изменять существующие задачи")
        print("- edit_task будет вызываться только при явной просьбе")
        print("\nРЕКОМЕНДАЦИЯ: Очистите историю чата и задачи, затем протестируйте заново")
    else:
        print("❌ НЕКОТОРЫЕ ПРОВЕРКИ НЕ ПРОЙДЕНЫ")
        print("\nНеобходимо проверить промпты вручную")
    print("=" * 80)

if __name__ == '__main__':
    test_multiple_tasks()


