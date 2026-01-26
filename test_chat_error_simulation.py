#!/usr/bin/env python3
"""
Тест симуляции ошибки в chat_with_ai для проверки уведомлений
"""
import asyncio
import sys
import os

# Добавляем текущую директорию в путь
sys.path.insert(0, os.path.dirname(__file__))

from ai_integration.chat import chat_with_ai
from models import init_db, get_db
from datetime import datetime, timezone

async def test_chat_error_simulation():
    """Тестируем симуляцию ошибки в chat_with_ai"""
    try:
        print("🧪 Тестируем симуляцию ошибки в chat_with_ai...")

        # Инициализируем базу данных
        init_db()
        db_session = next(get_db())

        # Тестовый пользователь
        user_id = 146333757

        # Сообщение, которое может вызвать ошибку (например, очень длинное)
        test_message = "Тестовое сообщение для проверки обработки ошибок в системе"

        print(f"📤 Отправляем тестовое сообщение пользователю {user_id}...")

        # Вызываем chat_with_ai - это должно работать нормально
        result = await chat_with_ai(
            message=test_message,
            user_id=user_id,
            db_session=db_session
        )

        print("✅ Сообщение обработано успешно"        print(f"📝 Ответ: {result[:100]}...")

        # Теперь давайте создадим искусственную ошибку для тестирования
        print("\n🧪 Теперь тестируем искусственную ошибку...")

        # Создадим мок-объект для симуляции сетевой ошибки
        import aiohttp
        from unittest.mock import patch, AsyncMock

        # Мокаем aiohttp.ClientSession.post чтобы он выбрасывал ошибку
        mock_response = AsyncMock()
        mock_response.status = 500
        mock_response.text = AsyncMock(return_value="Internal Server Error")

        mock_session = AsyncMock()
        mock_session.post = AsyncMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with patch('aiohttp.ClientSession', return_value=mock_session):
            print("🔥 Симулируем сетевую ошибку API...")
            try:
                result = await chat_with_ai(
                    message="Тест сбоя API",
                    user_id=user_id,
                    db_session=db_session
                )
                print("📝 Результат при ошибке:"                print(result)
            except Exception as e:
                print(f"⚠️ Исключение при симуляции: {e}")

        db_session.close()

    except Exception as e:
        print(f"❌ Ошибка в тесте: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_chat_error_simulation())