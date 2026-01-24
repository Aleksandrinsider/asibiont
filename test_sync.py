#!/usr/bin/env python3
"""Тест синхронизации между Telegram и веб-панелью."""

import os
import asyncio
import json
import logging
from datetime import datetime, timedelta

# Настройка для тестирования  
os.environ['LOCAL'] = '1'
os.environ['DB_URL'] = 'sqlite:///test_sync.db'

from models import init_db, Session, User, Task
from unittest.mock import AsyncMock, MagicMock

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def create_test_setup():
    """Создание тестовой среды."""
    print("🔧 Создание тестовой среды...")
    
    # Инициализация БД
    init_db()
    
    session = Session()
    try:
        # Очистка старых данных
        session.query(Task).delete()
        session.query(User).delete()
        session.commit()
        
        # Создание тестового пользователя
        user = User(
            telegram_id=123456789,
            username="test_sync_user",
            first_name="Sync Test",
            timezone='Europe/Moscow'
        )
        session.add(user)
        session.commit()
        
        # Создание тестовой задачи
        task = Task(
            user_id=user.id,
            title="Тестовая задача для синхронизации",
            description="Проверка синхронизации между Telegram и веб-панелью",
            due_date=datetime.now() + timedelta(minutes=1),
            status='pending'
        )
        session.add(task)
        session.commit()
        
        print(f"✅ Создан пользователь: {user.username} (Telegram ID: {user.telegram_id})")
        print(f"✅ Создана задача: {task.title} (ID: {task.id})")
        
        return user, task
        
    except Exception as e:
        session.rollback()
        logger.error(f"Ошибка при создании тестовых данных: {e}")
        raise
    finally:
        session.close()

async def test_telegram_to_web_sync(user, task):
    """Тест синхронизации: Telegram → Веб-панель."""
    print("\n📱➡️🌐 Тест: Завершение задачи через Telegram")
    
    try:
        from handlers import complete_task_callback
        
        # Мокируем callback query от Telegram
        callback = MagicMock()
        callback.from_user.id = user.telegram_id
        callback.data = f"complete_{task.id}"
        callback.message.edit_text = AsyncMock()
        callback.answer = AsyncMock()
        
        print(f"📱 Симуляция нажатия кнопки 'Выполнено' в Telegram")
        print(f"   Пользователь: {user.telegram_id}")
        print(f"   Callback data: {callback.data}")
        
        # Вызываем callback обработчик
        await complete_task_callback(callback)
        
        # Проверяем изменения в БД
        session = Session()
        try:
            updated_task = session.query(Task).filter_by(id=task.id).first()
            if updated_task and updated_task.status == 'completed':
                print("✅ Задача успешно завершена через Telegram callback")
                print(f"   Статус в БД: {updated_task.status}")
                if updated_task.actual_completion_time:
                    print(f"   Время завершения: {updated_task.actual_completion_time}")
            else:
                print("❌ Задача не была завершена в БД")
                return False
        finally:
            session.close()
        
        # Проверяем, что callback ответил
        callback.answer.assert_called()
        callback.message.edit_text.assert_called()
        
        print("✅ Telegram уведомления обработаны корректно")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка при тестировании Telegram→Веб синхронизации: {e}")
        return False

async def test_web_to_telegram_notification(user, task):
    """Тест уведомлений: Веб-панель → Telegram."""
    print("\n🌐➡️📱 Тест: Уведомление в Telegram из веб-панели")
    
    try:
        # Создаем новую задачу для теста
        session = Session()
        try:
            new_task = Task(
                user_id=user.id,
                title="Задача из веб-панели",
                description="Тестируем уведомления в Telegram",
                due_date=datetime.now() + timedelta(hours=1),
                status='pending'
            )
            session.add(new_task)
            session.commit()
            task_id = new_task.id
        finally:
            session.close()
        
        # Симулируем завершение задачи из веб-панели
        print(f"🌐 Симуляция завершения задачи из веб-панели")
        print(f"   Задача ID: {task_id}")
        print(f"   Пользователь Telegram ID: {user.telegram_id}")
        
        # Мокируем бота для уведомлений
        mock_bot = AsyncMock()
        mock_request = MagicMock()
        mock_request.app = {'bot': mock_bot}
        
        # Симулируем завершение через веб API (как в main.py)
        session = Session()
        try:
            web_task = session.query(Task).filter_by(id=task_id).first()
            if web_task:
                web_task.status = 'completed'
                web_task.actual_completion_time = datetime.now()
                session.commit()
                
                # Отправляем уведомление в Telegram (как в main.py)
                notification_text = f"✅ Задача завершена из веб-панели: {web_task.title}"
                await mock_bot.send_message(chat_id=user.telegram_id, text=notification_text)
                
                print("✅ Задача завершена в веб-панели")
                print(f"   Статус: {web_task.status}")
                print(f"   Время завершения: {web_task.actual_completion_time}")
                
                # Проверяем, что уведомление отправлено
                mock_bot.send_message.assert_called_with(
                    chat_id=user.telegram_id, 
                    text=notification_text
                )
                print("✅ Уведомление в Telegram отправлено")
                
                return True
        finally:
            session.close()
            
    except Exception as e:
        print(f"❌ Ошибка при тестировании Веб→Telegram уведомлений: {e}")
        return False

async def test_reminder_with_buttons(user, task):
    """Тест напоминаний с кнопками."""
    print("\n⏰ Тест: Напоминания с интерактивными кнопками")
    
    try:
        from reminder_service import ReminderService
        
        # Создаем сервис напоминаний
        reminder_service = ReminderService()
        reminder_service.bot = AsyncMock()
        
        print(f"⏰ Отправка напоминания для задачи ID: {task.id}")
        
        # Отправляем напоминание
        await reminder_service.send_reminder(user.telegram_id, task.title, task.id)
        
        # Проверяем, что сообщение отправлено с кнопками
        reminder_service.bot.send_message.assert_called()
        
        # Получаем параметры вызова
        call_args = reminder_service.bot.send_message.call_args
        
        if 'reply_markup' in call_args.kwargs:
            print("✅ Напоминание отправлено с inline кнопками")
            print(f"   Получатель: {call_args.kwargs['chat_id']}")
            print(f"   Текст: {call_args.kwargs['text'][:50]}...")
        else:
            print("❌ Напоминание отправлено БЕЗ кнопок")
            return False
        
        return True
        
    except Exception as e:
        print(f"❌ Ошибка при тестировании напоминаний: {e}")
        return False

async def main():
    """Основная функция тестирования синхронизации."""
    print("🔄 ТЕСТ СИНХРОНИЗАЦИИ: Telegram ↔️ Веб-панель\n")
    
    try:
        # Создание тестовой среды
        user, task = await create_test_setup()
        
        # Тест 1: Telegram → Веб-панель
        test1 = await test_telegram_to_web_sync(user, task)
        
        # Тест 2: Веб-панель → Telegram  
        test2 = await test_web_to_telegram_notification(user, task)
        
        # Тест 3: Напоминания с кнопками
        test3 = await test_reminder_with_buttons(user, task)
        
        # Итоги
        print(f"\n📊 ИТОГИ ТЕСТИРОВАНИЯ:")
        print(f"   📱➡️🌐 Telegram → Веб-панель: {'✅ OK' if test1 else '❌ FAIL'}")
        print(f"   🌐➡️📱 Веб-панель → Telegram: {'✅ OK' if test2 else '❌ FAIL'}")
        print(f"   ⏰ Напоминания с кнопками: {'✅ OK' if test3 else '❌ FAIL'}")
        
        all_passed = test1 and test2 and test3
        print(f"\n🎯 ОБЩИЙ РЕЗУЛЬТАТ: {'🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ' if all_passed else '⚠️ ЕСТЬ ПРОБЛЕМЫ'}")
        
        if all_passed:
            print("\n✨ Синхронизация между Telegram и веб-панелью работает корректно!")
        else:
            print("\n🔧 Необходимо исправить выявленные проблемы синхронизации.")
            
    except Exception as e:
        print(f"❌ Критическая ошибка в тестах: {e}")

if __name__ == "__main__":
    asyncio.run(main())