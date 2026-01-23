"""
Тест консистентности времени в AI-ответах
Проверяет, что AI видит правильное время с таймзоной в промпте
"""

import pytz
from datetime import datetime
from ai_integration.prompts import get_extended_system_prompt

def test_time_format_in_prompt():
    """Проверяем, что время передается с таймзоной"""
    
    # Тест 1: UTC таймзона
    user_now = datetime.now(pytz.UTC)
    current_time_str = f"{user_now.strftime('%H:%M')} (UTC)"
    current_date_str = user_now.strftime("%Y-%m-%d")
    
    prompt = get_extended_system_prompt(
        user_now=user_now,
        current_time_str=current_time_str,
        current_date_str=current_date_str,
        user_username="test_user",
        mentions_str="нет",
        user_memory="",
        subscription_tier="BRONZE"
    )
    
    # Проверяем что промпт содержит время с таймзоной
    assert "(UTC)" in prompt, "Промпт должен содержать UTC таймзону"
    assert current_time_str in prompt, f"Промпт должен содержать точное время: {current_time_str}"
    print(f"✅ UTC время в промпте: {current_time_str}")
    
    # Тест 2: Europe/Moscow таймзона
    moscow_tz = pytz.timezone('Europe/Moscow')
    moscow_now = datetime.now(moscow_tz)
    moscow_time_str = f"{moscow_now.strftime('%H:%M')} (Europe/Moscow)"
    moscow_date_str = moscow_now.strftime("%Y-%m-%d")
    
    prompt_moscow = get_extended_system_prompt(
        user_now=moscow_now,
        current_time_str=moscow_time_str,
        current_date_str=moscow_date_str,
        user_username="moscow_user",
        mentions_str="нет",
        user_memory="",
        subscription_tier="SILVER"
    )
    
    assert "(Europe/Moscow)" in prompt_moscow, "Промпт должен содержать Europe/Moscow таймзону"
    assert moscow_time_str in prompt_moscow, f"Промпт должен содержать московское время: {moscow_time_str}"
    print(f"✅ Московское время в промпте: {moscow_time_str}")
    
    # Тест 3: Проверяем что в промпте есть предупреждение о точности
    assert "ЛОКАЛЬНОЕ ВРЕМЯ ПОЛЬЗОВАТЕЛЯ" in prompt, "Должно быть указание что это локальное время"
    assert "НЕ ОКРУГЛЯЙ" in prompt, "Должно быть предупреждение не округлять время"
    print("✅ Предупреждения о точности времени присутствуют")
    
    # Тест 4: Проверяем формат даты
    assert current_date_str in prompt, f"Промпт должен содержать дату: {current_date_str}"
    print(f"✅ Дата в промпте: {current_date_str}")
    
    print("\n🎉 Все тесты пройдены! Время передается с таймзоной.")
    
    # Показываем пример части промпта с временем
    time_section = [line for line in prompt.split('\n') if 'ТЕКУЩЕЕ' in line or 'ВРЕМЯ' in line][:5]
    print("\n📝 Пример временной секции промпта:")
    for line in time_section:
        if line.strip():
            print(f"   {line}")

if __name__ == "__main__":
    test_time_format_in_prompt()
