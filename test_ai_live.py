import os
os.environ["FREE_ACCESS_MODE"] = "1"

import asyncio
import logging
import sys
import aiohttp
from datetime import datetime
from ai_integration.chat import chat_with_ai
from models import Session, User, Task, UserProfile, init_db, SubscriptionTier
import traceback

# Настройка кодировки для Windows
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

# Настройка логирования - только критичные ошибки
logging.basicConfig(
    level=logging.ERROR,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Функции для тестирования
FUNCTIONS = [
    "add_task",
    "list_tasks", 
    "complete_task",
    "delete_task",
    "reschedule_task",
    "update_profile",
    "find_partners",
    "find_relevant_contacts_for_task",
    "set_recurring_task",
    "get_task_details",
    "update_user_memory",
    "delegate_task",
    "delete_all_tasks"
]

tested_functions = set()

async def generate_user_message(history, tested_funcs, iteration, has_tasks):
    """Генерирует естественное сообщение пользователя через AI"""
    
    untested = [f for f in FUNCTIONS if f not in tested_funcs]
    
    # Умная логика: что можно делать на текущем этапе
    if iteration == 1:
        # Первое сообщение - всегда приветствие
        return "Привет! Что ты умеешь?"
    
    # Определяем что можно тестировать сейчас
    allowed_funcs = []
    
    # Всегда доступны
    always_available = ['add_task', 'list_tasks', 'update_profile', 'find_partners', 
                        'update_user_memory', 'set_recurring_task', 'find_relevant_contacts_for_task']
    
    # Доступны только если есть задачи
    if has_tasks:
        task_dependent = ['complete_task', 'delete_task', 'reschedule_task', 
                         'get_task_details', 'delegate_task', 'delete_all_tasks']
        allowed_funcs = [f for f in untested if f in always_available or f in task_dependent]
    else:
        allowed_funcs = [f for f in untested if f in always_available]
    
    # Если нет доступных - создаем задачу
    if not allowed_funcs:
        return "Добавь задачу: позвонить маме завтра в 15:00"
    
    # Приоритизируем непротестированные функции
    # Избегаем повторов - если find_partners уже протестирован, предпочитаем другие
    priority_order = []
    if 'complete_task' in allowed_funcs:
        priority_order.append('complete_task')
    if 'reschedule_task' in allowed_funcs:
        priority_order.append('reschedule_task')
    if 'delete_task' in allowed_funcs:
        priority_order.append('delete_task')
    if 'get_task_details' in allowed_funcs:
        priority_order.append('get_task_details')
    if 'delegate_task' in allowed_funcs:
        priority_order.append('delegate_task')
    if 'find_relevant_contacts_for_task' in allowed_funcs:
        priority_order.append('find_relevant_contacts_for_task')
    
    # Если есть приоритетные - используем их
    if priority_order:
        allowed_funcs = priority_order[:3]  # Топ-3 приоритетных
    
    context_str = "".join([f"{m['role']}: {m['content'][:100]}\n" for m in history[-6:]])
    tested_str = ', '.join(tested_funcs) if tested_funcs else 'Ничего'
    allowed_str = ', '.join(allowed_funcs[:5])
    
    prompt = f"""Ты симулируешь РЕАЛЬНОГО пользователя Telegram-бота для задач.

КОНТЕКСТ ДИАЛОГА:
{context_str}

УЖЕ ПРОТЕСТИРОВАНО ({len(tested_funcs)} из {len(FUNCTIONS)}):
{tested_str}

СЕЙЧАС НУЖНО ПРОТЕСТИРОВАТЬ (В ПРИОРИТЕТЕ): {allowed_str}

═══════════════════════════════════════════════════════════════════════════════
ТВОЯ ЗАДАЧА: Напиши ТОЧНОЕ сообщение для ПЕРВОЙ функции из приоритета!
═══════════════════════════════════════════════════════════════════════════════

ШАБЛОНЫ (ИСПОЛЬЗУЙ ТОЧНО ЭТИ СЛОВА):

complete_task:
  "Завершил задачу 'подготовить презентацию'"
  "Выполнил задачу 'позвонить маме'"
  "Готово с презентацией"

delete_task:
  "Удали задачу 'подготовить презентацию'"
  "Убери задачу 'позвонить маме'"

reschedule_task:
  "Перенеси задачу 'подготовить презентацию' на послезавтра в 16:00"
  "Отложи 'позвонить маме' на понедельник в 10 утра"

get_task_details:
  "Покажи детали задачи 'подготовить презентацию'"
  "Расскажи подробнее про задачу 'позвонить маме'"

delegate_task:
  "Делегируй задачу 'подготовить презентацию' пользователю @test_user1"
  "Поручи задачу 'отчет' @test_main"

delete_all_tasks:
  "Удали все мои задачи"
  "Очисти все задачи"

set_recurring_task:
  "Напоминай зарядку каждый день в 7 утра"
  "Каждую среду в 19:00 напоминай про встречу"

find_partners:
  "Найди единомышленников для бега"
  "Найди партнеров для стартапа"

⚠️ КРИТИЧНО: 
- Для complete_task/delete_task/reschedule_task/get_task_details используй название существующей задачи (подготовить презентацию, позвонить маме)
- Для delegate_task обязательно укажи @username (например @test_user1, @test_main)
- НЕ придумывай новые задачи, используй те что уже созданы в диалоге!

ОТВЕЧАЙ ТОЛЬКО ТЕКСТОМ СООБЩЕНИЯ, БЕЗ ПОЯСНЕНИЙ!"""

    try:
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            return "Покажи задачи"
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.deepseek.com/v1/chat/completions",  # ИСПРАВЛЕНО: добавлен /v1/
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "deepseek-chat",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.8,
                    "max_tokens": 100
                },
                timeout=aiohttp.ClientTimeout(total=15)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return data["choices"][0]["message"]["content"].strip().strip('"\'')
                else:
                    return "Покажи задачи"
    except Exception as e:
        logger.error(f"Ошибка генерации: {e}")
        fallbacks = ["Покажи задачи", "Готово", "Я из Москвы"]
        return fallbacks[iteration % len(fallbacks)]

def extract_tool_calls(response):
    """Извлекает вызовы функций из ответа"""
    if isinstance(response, dict):
        return response.get('tool_calls', [])
    return []

async def run_live_test():
    """Запускает живой тест диалога"""
    
    print("\n" + "="*80)
    print("🤖 ЖИВОЙ ТЕСТ AI-АГЕНТА")
    print("="*80 + "\n")
    
    # Инициализация БД
    try:
        init_db()
        print("✅ База данных готова\n")
    except Exception as e:
        print(f"❌ Ошибка БД: {e}")
        return
    
    # Создаем тестового пользователя
    db = Session()
    try:
        user = db.query(User).filter_by(telegram_id=999).first()
        if not user:
            user = User(
                telegram_id=999,
                username="live_test",
                first_name="Живой Тест",
                subscription_tier=SubscriptionTier.LIGHT,
                timezone="Europe/Moscow"
            )
            db.add(user)
            db.commit()
        
        profile = db.query(UserProfile).filter_by(user_id=user.id).first()
        if not profile:
            profile = UserProfile(user_id=user.id, interests="тестирование")
            db.add(profile)
            db.commit()
        
        print(f"✅ Пользователь готов (ID: {user.telegram_id})\n")
    except Exception as e:
        print(f"❌ Ошибка пользователя: {e}")
        return
    finally:
        db.close()
    
    history = []
    stats = {'total': 0, 'success': 0, 'errors': 0, 'tool_calls': 0}
    
    print("─"*80)
    print(f"🎯 ЦЕЛЬ: Протестировать {len(FUNCTIONS)} функций за 20 итераций")
    print(f"📝 МЕТОД: Живой диалог с AI-симуляцией пользователя")
    print("─"*80 + "\n")
    
    for i in range(1, 21):
        print("\n" + "="*80)
        print(f"💬 ИТЕРАЦИЯ {i}/20")
        print("="*80)
        
        stats['total'] += 1
        
        # Проверка завершения
        if len(tested_functions) == len(FUNCTIONS):
            print("\n🎉 ВСЕ ФУНКЦИИ ПРОТЕСТИРОВАНЫ!")
            break
        
        try:
            # Проверяем есть ли задачи
            check_db = Session()
            has_tasks = check_db.query(Task).filter_by(
                user_id=check_db.query(User).filter_by(telegram_id=999).first().id,
                status='active'
            ).count() > 0
            check_db.close()
            
            # Генерируем сообщение пользователя
            print("\n⏳ Генерирую сообщение...")
            user_msg = await generate_user_message(history, tested_functions, i, has_tasks)
            
            print(f"\n👤 ПОЛЬЗОВАТЕЛЬ: {user_msg}")
            history.append({"role": "user", "content": user_msg})
            
            # Вызываем агента
            print("\n🤖 АГЕНТ ОБРАБАТЫВАЕТ...\n")
            
            session = Session()
            try:
                response = await asyncio.wait_for(
                    chat_with_ai(
                        message=user_msg,
                        user_id=999,
                        context=history[-10:],
                        db_session=session
                    ),
                    timeout=90.0  # 90 секунд максимум для медленных API
                )
            finally:
                session.close()
            
            # Обработка ответа
            if isinstance(response, dict):
                agent_text = response.get('response', 'Нет ответа')
                tool_calls = response.get('tool_calls', [])
            elif isinstance(response, str):
                agent_text = response
                tool_calls = []
            else:
                agent_text = str(response)
                tool_calls = []
            
            print(f"💬 АГЕНТ: {agent_text}\n")
            
            # Показываем вызовы функций
            if tool_calls:
                print("⚙️  ВЫПОЛНЕННЫЕ ДЕЙСТВИЯ:")
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        func = tc.get('function', {})
                        if isinstance(func, dict):
                            func_name = func.get('name', 'unknown')
                            args = func.get('arguments', '{}')
                        else:
                            func_name = str(func)
                            args = '{}'
                        
                        print(f"   ✓ {func_name}")
                        
                        # Отмечаем функцию как протестированную
                        if func_name in FUNCTIONS:
                            if func_name not in tested_functions:
                                tested_functions.add(func_name)
                                print(f"      🆕 НОВАЯ ФУНКЦИЯ!")
                            stats['tool_calls'] += 1
                print()
            else:
                print("⚙️  Функции не вызывались\n")
            
            history.append({"role": "assistant", "content": agent_text})
            stats['success'] += 1
            
            # Прогресс
            remaining = len(FUNCTIONS) - len(tested_functions)
            print(f"📊 ПРОГРЕСС: {len(tested_functions)}/{len(FUNCTIONS)} функций ({remaining} осталось)")
            
            if remaining > 0:
                untested = [f for f in FUNCTIONS if f not in tested_functions]
                print(f"⏳ Осталось: {', '.join(untested[:5])}")
            
            await asyncio.sleep(1)
            
        except asyncio.TimeoutError:
            print("\n⏰ ТАЙМАУТ - агент завис (пропускаем)")
            stats['errors'] += 1
            
        except Exception as e:
            print(f"\n❌ ОШИБКА: {str(e)[:200]}")
            stats['errors'] += 1
    
    # Финальный отчет
    print("\n\n" + "="*80)
    print("📊 ИТОГОВЫЙ ОТЧЕТ")
    print("="*80)
    print(f"\nВсего итераций: {stats['total']}")
    print(f"✅ Успешных: {stats['success']}")
    print(f"❌ Ошибок: {stats['errors']}")
    print(f"⚙️  Всего вызовов функций: {stats['tool_calls']}")
    
    print(f"\n🎯 ФУНКЦИИ ({len(tested_functions)}/{len(FUNCTIONS)}):")
    
    if tested_functions:
        print("\n✅ ПРОТЕСТИРОВАНО:")
        for func in sorted(tested_functions):
            print(f"   ✓ {func}")
    
    untested = [f for f in FUNCTIONS if f not in tested_functions]
    if untested:
        print(f"\n❌ НЕ ПРОТЕСТИРОВАНО ({len(untested)}):")
        for func in sorted(untested):
            print(f"   ✗ {func}")
    
    print("\n" + "="*80)
    print("✨ ТЕСТ ЗАВЕРШЕН")
    print("="*80 + "\n")

if __name__ == "__main__":
    asyncio.run(run_live_test())
