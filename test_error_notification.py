#!/usr/bin/env python3
"""
Тест отправки уведомлений об ошибках в Telegram
"""
import asyncio
import sys
import os

# Добавляем текущую директорию в путь
sys.path.insert(0, os.path.dirname(__file__))

from ai_integration.chat import send_error_notification_to_bot

async def test_error_notification():
    """Тестируем отправку уведомления об ошибке пользователю 146333757"""
    try:
        print("🧪 Тестируем отправку уведомления об ошибке...")

        # Тестовое сообщение об ошибке
        error_message = "Тестовое уведомление об ошибке - проверка работы системы уведомлений"
        user_id = 146333757  # ID разработчика
        error_details = "Test error details for notification system verification"

        # Отправляем уведомление
        await send_error_notification_to_bot(
            error_message=error_message,
            user_id=user_id,
            error_details=error_details,
            target_user_id=146333757
        )

        print("✅ Уведомление отправлено! Проверьте Telegram.")
        print(f"📱 Отправлено пользователю: {user_id}")
        print(f"💬 Сообщение: {error_message}")

    except Exception as e:
        print(f"❌ Ошибка при отправке уведомления: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_error_notification())