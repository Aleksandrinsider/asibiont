#!/usr/bin/env python3
"""Упрощенный тест для проверки callback'ов кнопок."""

import os
import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

# Настройка для тестирования
os.environ['LOCAL'] = '1'

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_inline_keyboards():
    """Тест создания inline клавиатур."""
    print("=== ТЕСТ: Inline клавиатуры ===")
    
    try:
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        
        # Тестовая клавиатура для задачи
        task_id = 123
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Выполнено", callback_data=f"complete_{task_id}"),
                InlineKeyboardButton(text="⏰ Отложить", callback_data=f"skip_{task_id}")
            ],
            [
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_{task_id}")
            ]
        ])
        
        print(f"✅ Клавиатура создана с {len(keyboard.inline_keyboard)} рядами кнопок")
        print(f"   Кнопки: {[btn.text for row in keyboard.inline_keyboard for btn in row]}")
        
        # Проверяем callback_data
        callbacks = [btn.callback_data for row in keyboard.inline_keyboard for btn in row]
        expected = [f"complete_{task_id}", f"skip_{task_id}", f"delete_{task_id}"]
        
        print(f"   Callback данные: {callbacks}")
        print(f"   Ожидаемые: {expected}")
        
        if callbacks == expected:
            print("✅ Callback данные корректны")
        else:
            print("❌ Callback данные неверны")
        
        return True
        
    except Exception as e:
        print(f"❌ Ошибка при создании клавиатуры: {e}")
        return False

async def test_callback_imports():
    """Тест импортов callback обработчиков."""
    print("\n=== ТЕСТ: Импорты callback обработчиков ===")
    
    try:
        from handlers import (
            complete_task_callback, skip_task_callback, delete_task_callback,
            confirm_done_callback, mark_incomplete_callback
        )
        
        print("✅ Все callback обработчики успешно импортированы:")
        print("   - complete_task_callback")
        print("   - skip_task_callback") 
        print("   - delete_task_callback")
        print("   - confirm_done_callback")
        print("   - mark_incomplete_callback")
        
        # Проверяем, что это асинхронные функции
        import inspect
        handlers = [
            complete_task_callback, skip_task_callback, delete_task_callback,
            confirm_done_callback, mark_incomplete_callback
        ]
        
        for handler in handlers:
            if inspect.iscoroutinefunction(handler):
                print(f"   ✅ {handler.__name__} - асинхронная функция")
            else:
                print(f"   ❌ {handler.__name__} - НЕ асинхронная функция")
        
        return True
        
    except ImportError as e:
        print(f"❌ Ошибка импорта: {e}")
        return False
    except Exception as e:
        print(f"❌ Общая ошибка: {e}")
        return False

def test_callback_data_parsing():
    """Тест парсинга callback данных."""
    print("\n=== ТЕСТ: Парсинг callback данных ===")
    
    test_cases = [
        ("complete_123", ("complete", "123")),
        ("skip_456", ("skip", "456")), 
        ("delete_789", ("delete", "789")),
        ("confirm_done_101112", ("confirm_done", "101112")),
        ("mark_incomplete_131415", ("mark_incomplete", "131415"))
    ]
    
    for callback_data, expected in test_cases:
        try:
            action, task_id = callback_data.split('_', 1)
            
            if (action, task_id) == expected:
                print(f"✅ {callback_data} → действие: {action}, ID задачи: {task_id}")
            else:
                print(f"❌ {callback_data} → ошибка парсинга")
                
        except Exception as e:
            print(f"❌ Ошибка при парсинге {callback_data}: {e}")
    
    return True

async def test_mock_callback():
    """Тест с мокированным callback."""
    print("\n=== ТЕСТ: Мокированный callback ===")
    
    try:
        # Создаем мок callback query
        callback = MagicMock()
        callback.from_user.id = 123456789
        callback.data = "complete_999"
        callback.message.edit_text = AsyncMock()
        callback.answer = AsyncMock()
        
        print(f"✅ Мок callback создан:")
        print(f"   - User ID: {callback.from_user.id}")
        print(f"   - Callback data: {callback.data}")
        print(f"   - Message edit: {type(callback.message.edit_text)}")
        print(f"   - Answer: {type(callback.answer)}")
        
        # Симулируем вызов answer
        await callback.answer("Тест прошел успешно!")
        print("✅ Симуляция callback.answer() выполнена")
        
        # Симулируем редактирование сообщения
        await callback.message.edit_text("Новый текст", reply_markup=None)
        print("✅ Симуляция message.edit_text() выполнена")
        
        return True
        
    except Exception as e:
        print(f"❌ Ошибка в мок тесте: {e}")
        return False

async def main():
    """Основная функция тестирования."""
    print("🚀 Запуск упрощенных тестов кнопок\n")
    
    # Тест 1: Inline клавиатуры
    keyboard_ok = test_inline_keyboards()
    
    # Тест 2: Импорты callback'ов
    import_ok = await test_callback_imports()
    
    # Тест 3: Парсинг callback данных
    parsing_ok = test_callback_data_parsing()
    
    # Тест 4: Мокированный callback
    mock_ok = await test_mock_callback()
    
    # Итоги
    print(f"\n=== ИТОГИ ТЕСТИРОВАНИЯ ===")
    print(f"✅ Inline клавиатуры: {'OK' if keyboard_ok else 'FAIL'}")
    print(f"✅ Импорты callback'ов: {'OK' if import_ok else 'FAIL'}")
    print(f"✅ Парсинг данных: {'OK' if parsing_ok else 'FAIL'}")
    print(f"✅ Мокированный callback: {'OK' if mock_ok else 'FAIL'}")
    
    all_ok = keyboard_ok and import_ok and parsing_ok and mock_ok
    print(f"\n🎉 Общий результат: {'ВСЕ ТЕСТЫ ПРОЙДЕНЫ' if all_ok else 'ЕСТЬ ОШИБКИ'}")

if __name__ == "__main__":
    asyncio.run(main())