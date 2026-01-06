"""
Тестирование API endpoints
"""
import asyncio
import aiohttp
import json

async def test_api():
    """Тестировать API endpoints"""
    print("="*70)
    print("🧪 ТЕСТИРОВАНИЕ API ENDPOINTS")
    print("="*70)
    
    # Тест 1: Получить задачи
    print("\n" + "="*70)
    print("ТЕСТ 1: GET /api/tasks")
    print("="*70)
    
    async with aiohttp.ClientSession() as session:
        # Создаем fake cookie session
        cookies = {'session': 'fake_session_for_test'}
        
        async with session.get('http://localhost:8080/api/tasks', cookies=cookies) as resp:
            print(f"Status: {resp.status}")
            if resp.status == 200:
                data = await resp.json()
                print(f"Задач получено: {len(data.get('tasks', []))}")
                for task in data.get('tasks', []):
                    print(f"  - {task['title']}")
                    print(f"    is_delegated: {task.get('is_delegated')}")
                    print(f"    delegation_status: {task.get('delegation_status')}")
            else:
                print(f"Ошибка: {resp.status}")
                text = await resp.text()
                print(text)
    
    # Тест 2: Получить контакты
    print("\n" + "="*70)
    print("ТЕСТ 2: GET /api/partners")
    print("="*70)
    
    async with aiohttp.ClientSession() as session:
        cookies = {'session': 'fake_session_for_test'}
        
        async with session.get('http://localhost:8080/api/partners', cookies=cookies) as resp:
            print(f"Status: {resp.status}")
            if resp.status == 200:
                data = await resp.json()
                print(f"Контактов получено: {len(data.get('partners', []))}")
                for partner in data.get('partners', []):
                    print(f"  - @{partner['contact_info']} ({partner['type']})")
                    if partner.get('tasks'):
                        print(f"    Задачи: {partner['tasks']}")
            else:
                print(f"Ошибка: {resp.status}")
                text = await resp.text()
                print(text)

if __name__ == "__main__":
    print("⚠️  Убедитесь, что сервер запущен на localhost:8080")
    print("⚠️  Нажмите Ctrl+C если сервер не запущен\n")
    
    try:
        asyncio.run(test_api())
    except aiohttp.ClientConnectorError:
        print("\n❌ ОШИБКА: Сервер не запущен на localhost:8080")
        print("   Запустите сервер командой: python main.py")
    except Exception as e:
        print(f"\n❌ ОШИБКА: {e}")
