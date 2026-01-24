#!/usr/bin/env python3
"""
Автоматический тестер веб-чата
Отправляет сообщения от имени пользователя и показывает ответы AI
"""

import asyncio
import aiohttp
import sys

async def send_message(message: str, telegram_id: int = 1001):
    """Отправить сообщение в чат и получить ответ"""
    url = "http://localhost:8080/api/chat"
    
    async with aiohttp.ClientSession() as session:
        payload = {
            "telegram_id": telegram_id,
            "message": message
        }
        
        print(f"\n📤 Отправляю: '{message}'")
        print("⏳ Ожидаю ответа...")
        
        try:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as response:
                if response.status == 200:
                    data = await response.json()
                    ai_response = data.get("response", "")
                    print(f"\n💬 AI ответил:")
                    print(f"{'='*60}")
                    print(ai_response)
                    print(f"{'='*60}\n")
                    return ai_response
                else:
                    error_text = await response.text()
                    print(f"❌ Ошибка {response.status}: {error_text}")
                    return None
        except asyncio.TimeoutError:
            print("⏱️ Таймаут ожидания ответа")
            return None
        except Exception as e:
            print(f"❌ Ошибка: {e}")
            return None

async def interactive_chat():
    """Интерактивный режим чата"""
    print("\n🤖 Интерактивный чат с AI")
    print("="*60)
    print("Введите 'exit' для выхода")
    print("="*60)
    
    while True:
        try:
            message = input("\n👤 Вы: ").strip()
            
            if message.lower() in ['exit', 'quit', 'выход']:
                print("👋 До свидания!")
                break
                
            if not message:
                continue
                
            await send_message(message)
            
        except KeyboardInterrupt:
            print("\n\n👋 До свидания!")
            break
        except Exception as e:
            print(f"❌ Ошибка: {e}")

async def automated_scenario():
    """Автоматический сценарий тестирования"""
    print("\n🎬 Запускаю автоматический сценарий...")
    print("="*60)
    
    scenarios = [
        "Привет! Как дела?",
        "Какие у меня задачи на сегодня?",
        "Напомни мне позвонить маме завтра в 14:00",
        "Сколько сейчас времени?",
        "Покажи мои контакты",
    ]
    
    for i, message in enumerate(scenarios, 1):
        print(f"\n[{i}/{len(scenarios)}]")
        await send_message(message)
        await asyncio.sleep(1)  # Небольшая задержка между сообщениями
    
    print("\n✅ Сценарий завершён!")

async def main():
    """Главная функция"""
    if len(sys.argv) > 1:
        mode = sys.argv[1]
        if mode == "auto":
            await automated_scenario()
        elif mode == "message":
            if len(sys.argv) > 2:
                message = " ".join(sys.argv[2:])
                await send_message(message)
            else:
                print("❌ Укажите сообщение: python test_web_chat.py message 'Привет'")
        else:
            print("❌ Неизвестный режим. Используйте:")
            print("  python test_web_chat.py           - интерактивный режим")
            print("  python test_web_chat.py auto      - автоматический сценарий")
            print("  python test_web_chat.py message 'текст' - отправить одно сообщение")
    else:
        await interactive_chat()

if __name__ == "__main__":
    asyncio.run(main())
