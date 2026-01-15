import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from improved_prompts_final import get_optimized_prompt_final
from datetime import datetime, timezone
import pytz

def show_current_prompt():
    """Показывает текущий системный промпт агента"""

    # Имитируем типичные параметры
    user_now = datetime.now(pytz.UTC)
    current_time_str = user_now.strftime("%H:%M")
    current_date_str = f"{user_now.day} января {user_now.year}"
    user_username = "@testuser"
    mentions_str = "упоминаний нет"
    user_memory = "Профиль: Город: Москва, Компания: TechCorp, Должность: Разработчик | Сводка: всего активных задач 3"
    last_responses = ["Хорошо, отметил задачу как выполненную", "Добавил новую задачу в список"]

    prompt = get_optimized_prompt_final(
        user_now=user_now,
        current_time_str=current_time_str,
        current_date_str=current_date_str,
        user_username=user_username,
        mentions_str=mentions_str,
        user_memory=user_memory,
        last_responses=last_responses
    )

    print("🎯 ТЕКУЩИЙ СИСТЕМНЫЙ ПРОМПТ АГЕНТА")
    print("=" * 60)
    print(prompt)
    print("=" * 60)
    print(f"\n📊 Длина промпта: {len(prompt)} символов")

if __name__ == "__main__":
    show_current_prompt()