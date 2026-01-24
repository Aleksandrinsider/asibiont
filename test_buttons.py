#!/usr/bin/env python3
"""Тест для проверки работы кнопок Telegram бота."""

import os
import asyncio
import logging
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

# Настройка для тестирования
os.environ['LOCAL'] = '1'
os.environ['DB_URL'] = 'sqlite:///test_buttons_clean.db'

from models import init_db, Session, User, Task
from handlers import (
    complete_task_callback, skip_task_callback, delete_task_callback,
    confirm_done_callback, mark_incomplete_callback
)

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def create_test_data():
    """Создание тестовых данных."""
    # Инициализация БД
    init_db()
    
    session = Session()
    try:
        # Проверяем, есть ли уже тестовый пользователь
        existing_user = session.query(User).filter_by(telegram_id=123456789).first()
        if existing_user:
            # Удаляем старые тестовые задачи
            session.query(Task).filter_by(user_id=existing_user.id).delete()
            session.delete(existing_user)
            session.commit()
        
        # Создание тестового пользователя
        user = User(
            telegram_id=123456789,
            username="test_user",
            first_name="Test User",
            timezone='Europe/Moscow'
        )
        session.add(user)
        session.commit()
        
        # Создание тестовых задач
        tasks = [
            Task(
                user_id=user.id,  # Используем внутренний ID, а не telegram_id
                title="Проверить почту",
                description="Важное дело",
                scheduled_time=datetime.now() + timedelta(minutes=1),
                status='pending'
            ),
            Task(
                user_id=user.id,
                title="Купить продукты", 
                description="Молоко, хлеб",
                scheduled_time=datetime.now() + timedelta(hours=2),
                status='pending'
            )
        ]
        
        for task in tasks:
            session.add(task)
        
        session.commit()
        
        logger.info(f"Создан пользователь: {user.username}")
        logger.info(f"Создано задач: {len(tasks)}")
        
        return user, tasks
        
    except Exception as e:
        session.rollback()
        logger.error(f"Ошибка при создании тестовых данных: {e}")
        raise
    finally:
        session.close()

async def test_callback_handlers():
    """Тест callback'ов кнопок."""
    print("=== ТЕСТ: Callback обработчики кнопок ===")
    
    # Создание тестовых данных
    user, tasks = await create_test_data()
    task = tasks[0]
    
    # Мокаем callback query
    callback = MagicMock()
    callback.from_user.id = user.telegram_id
    callback.data = f"complete_{task.id}"
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()
    
    print(f"Тестируем задачу: {task.title} (ID: {task.id})")
    
    # Тест 1: Завершение задачи
    print("\n1. Тест завершения задачи через кнопку")
    try:
        await complete_task_callback(callback)
        
        # Проверяем изменение в БД
        session = Session()
        try:
            updated_task = session.query(Task).filter_by(id=task.id).first()
            if updated_task and updated_task.status == 'completed':
                print("✅ Задача успешно завершена через callback")
            else:
                print("❌ Задача не была завершена")
        finally:
            session.close()
            
    except Exception as e:
        print(f"❌ Ошибка при завершении задачи: {e}")
    
    # Тест 2: Пропуск задачи
    print("\n2. Тест пропуска задачи")
    task2 = tasks[1]
    callback.data = f"skip_{task2.id}"
    
    try:
        await skip_task_callback(callback)
        
        session = Session()
        try:
            updated_task = session.query(Task).filter_by(id=task2.id).first()
            if updated_task and updated_task.scheduled_time > datetime.now():
                print("✅ Задача успешно отложена")
            else:
                print("❌ Задача не была отложена")
        finally:
            session.close()
            
    except Exception as e:
        print(f"❌ Ошибка при пропуске задачи: {e}")
    
    # Тест 3: Удаление задачи
    print("\n3. Тест удаления задачи")
    callback.data = f"delete_{task2.id}"
    
    try:
        await delete_task_callback(callback)
        
        session = Session()
        try:
            deleted_task = session.query(Task).filter_by(id=task2.id).first()
            if not deleted_task:
                print("✅ Задача успешно удалена")
            else:
                print("❌ Задача не была удалена")
        finally:
            session.close()
            
    except Exception as e:
        print(f"❌ Ошибка при удалении задачи: {e}")

def test_inline_keyboards():
    """Тест создания inline клавиатур."""
    print("\n=== ТЕСТ: Inline клавиатуры ===")
    
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
        
        return True
        
    except Exception as e:
        print(f"❌ Ошибка при создании клавиатуры: {e}")
        return False

async def main():
    """Основная функция тестирования."""
    print("🚀 Запуск тестов кнопок Telegram бота")
    
    # Тест 1: Inline клавиатуры
    keyboard_ok = test_inline_keyboards()
    
    # Тест 2: Callback обработчики
    if keyboard_ok:
        await test_callback_handlers()
    
    print("\n✨ Тесты завершены")

if __name__ == "__main__":
    asyncio.run(main())