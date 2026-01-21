"""
Комплексное тестирование агента в боевом режиме с Railway БД
Симулирует реальные запросы пользователя и проверяет ответы агента
"""
import os
import sys
import asyncio
from datetime import datetime, timedelta
import pytz

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

# Set environment to use Railway database
os.environ['LOCAL'] = '0'

from models import Session, User, UserProfile, Task, Interaction
from ai_integration.chat import chat_with_ai
from sqlalchemy import or_

class AIUserSimulator:
    """Генератор ответов пользователя через ИИ"""
    
    def __init__(self):
        self.conversation_history = []
    
    def generate_user_response(self, agent_message, scenario):
        """Генерирует правдоподобный ответ пользователя на основе контекста"""
        
        # Простые паттерны для имитации пользователя
        responses = {
            'task_creation': [
                "создай задачу позвонить клиенту завтра в 15:00",
                "добавь напоминание встреча с партнером послезавтра в 10:00",
                "нужно подготовить отчет к пятнице"
            ],
            'task_listing': [
                "покажи мои задачи",
                "что у меня запланировано",
                "какие задачи активные"
            ],
            'delegation': [
                "делегируй задачу 'подготовить презентацию' @snowboarder_max",
                "поручи отчет @boxing_pro к понедельнику",
            ],
            'contact_search': [
                "найди контакты со спортом",
                "кто может помочь с дизайном",
                "покажи партнеров из москвы"
            ],
            'profile_update': [
                "мои навыки: программирование, управление проектами",
                "моя цель - развить бизнес в IT",
                "я увлекаюсь спортом и путешествиями"
            ],
            'greeting': [
                "привет",
                "здравствуй",
                "добрый день"
            ],
            'task_completion': [
                "я выполнил задачу 'позвонить клиенту'",
                "задача готова",
                "сделал отчет"
            ]
        }
        
        return responses.get(scenario, ["ok"])[0]


async def test_agent_comprehensive():
    """Комплексное тестирование агента"""
    
    session = Session()
    simulator = AIUserSimulator()
    
    try:
        # Найти тестового пользователя
        user = session.query(User).filter(
            or_(
                User.username == 'aleksandrinsider',
                User.username == '@aleksandrinsider'
            )
        ).first()
        
        if not user:
            print("❌ Пользователь не найден!")
            return
        
        print("="*80)
        print("КОМПЛЕКСНОЕ ТЕСТИРОВАНИЕ АГЕНТА В БОЕВОМ РЕЖИМЕ")
        print("="*80)
        print(f"\nПользователь: @{user.username} (ID: {user.id})")
        print(f"Время теста: {datetime.now(pytz.UTC).strftime('%Y-%m-%d %H:%M:%S')} UTC\n")
        
        # Тестовые сценарии
        test_scenarios = [
            {
                'name': 'ПРИВЕТСТВИЕ',
                'user_input': 'привет, как дела?',
                'expected_keywords': ['привет', 'помочь', 'задач'],
                'check_actions': ['greeting_response']
            },
            {
                'name': 'СОЗДАНИЕ ЗАДАЧИ',
                'user_input': 'создай задачу позвонить партнеру завтра в 14:00',
                'expected_keywords': ['задач', 'создал', 'напомн'],
                'check_actions': ['add_task']
            },
            {
                'name': 'ПРОСМОТР ЗАДАЧ',
                'user_input': 'покажи мои активные задачи',
                'expected_keywords': ['задач'],
                'check_actions': ['list_tasks']
            },
            {
                'name': 'ПОИСК КОНТАКТОВ ПО ИНТЕРЕСАМ',
                'user_input': 'найди контакты кто интересуется спортом',
                'expected_keywords': ['контакт', 'спорт'],
                'check_actions': ['find_partners']
            },
            {
                'name': 'ОБНОВЛЕНИЕ ПРОФИЛЯ - НАВЫКИ',
                'user_input': 'мои навыки: управление проектами, аналитика',
                'expected_keywords': ['навык', 'профил'],
                'check_actions': ['update_profile']
            },
            {
                'name': 'ОБНОВЛЕНИЕ ПРОФИЛЯ - ЦЕЛИ',
                'user_input': 'моя цель - развить бизнес',
                'expected_keywords': ['цель'],
                'check_actions': ['update_profile']
            },
            {
                'name': 'ДЕЛЕГИРОВАНИЕ ЗАДАЧИ',
                'user_input': 'делегируй задачу подготовить отчет @snowboarder_max к пятнице',
                'expected_keywords': ['делегирова', 'задач', '@snowboarder_max'],
                'check_actions': ['add_task']  # Делегирование создает задачу
            },
            {
                'name': 'ЗАПРОС РЕКОМЕНДАЦИЙ',
                'user_input': 'посоветуй кто может помочь с маркетингом',
                'expected_keywords': ['контакт', 'маркетинг'],
                'check_actions': ['find_partners']
            },
            {
                'name': 'ПРОСЬБА О ПОМОЩИ',
                'user_input': 'у меня много задач, не знаю с чего начать',
                'expected_keywords': ['задач', 'помо'],
                'check_actions': ['list_tasks']
            },
            {
                'name': 'ЗАВЕРШЕНИЕ ЗАДАЧИ',
                'user_input': 'я выполнил задачу позвонить партнеру',
                'expected_keywords': ['выполн', 'завершен', 'готов'],
                'check_actions': ['complete_task']
            }
        ]
        
        results = {
            'passed': 0,
            'failed': 0,
            'errors': []
        }
        
        for i, scenario in enumerate(test_scenarios, 1):
            print(f"\n{'='*80}")
            print(f"ТЕСТ {i}/{len(test_scenarios)}: {scenario['name']}")
            print(f"{'='*80}")
            print(f"\n👤 ПОЛЬЗОВАТЕЛЬ: {scenario['user_input']}")
            
            try:
                # Вызов агента
                response, context_data = await chat_with_ai(
                    user_id=user.telegram_id,
                    message=scenario['user_input'],
                    db_session=session
                )
                
                print(f"\n🤖 АГЕНТ: {response[:500]}{'...' if len(response) > 500 else ''}")
                
                # Проверка ответа
                response_lower = response.lower()
                keywords_found = []
                keywords_missing = []
                
                for keyword in scenario['expected_keywords']:
                    if keyword.lower() in response_lower:
                        keywords_found.append(keyword)
                    else:
                        keywords_missing.append(keyword)
                
                # Проверка действий
                actions_performed = []
                if context_data and 'tool_calls' in context_data:
                    for tool_call in context_data['tool_calls']:
                        if 'function' in tool_call:
                            actions_performed.append(tool_call['function']['name'])
                
                # Результат теста
                test_passed = len(keywords_found) > 0
                
                print(f"\n📊 РЕЗУЛЬТАТ:")
                print(f"   Найденные ключевые слова: {', '.join(keywords_found) if keywords_found else 'НЕТ'}")
                if keywords_missing:
                    print(f"   Отсутствующие ключевые слова: {', '.join(keywords_missing)}")
                if actions_performed:
                    print(f"   Выполненные действия: {', '.join(actions_performed)}")
                else:
                    print(f"   Выполненные действия: НЕТ")
                
                if test_passed:
                    print(f"   ✅ ТЕСТ ПРОЙДЕН")
                    results['passed'] += 1
                else:
                    print(f"   ❌ ТЕСТ ПРОВАЛЕН")
                    results['failed'] += 1
                    results['errors'].append({
                        'scenario': scenario['name'],
                        'reason': f"Не найдены ключевые слова: {', '.join(keywords_missing)}"
                    })
                
                # Пауза между запросами
                await asyncio.sleep(1)
                
            except Exception as e:
                print(f"\n❌ ОШИБКА: {e}")
                results['failed'] += 1
                results['errors'].append({
                    'scenario': scenario['name'],
                    'reason': str(e)
                })
                import traceback
                traceback.print_exc()
        
        # Итоговая статистика
        print(f"\n{'='*80}")
        print("ИТОГОВАЯ СТАТИСТИКА")
        print(f"{'='*80}")
        print(f"\n✅ Успешно: {results['passed']}/{len(test_scenarios)}")
        print(f"❌ Провалено: {results['failed']}/{len(test_scenarios)}")
        print(f"📈 Процент успеха: {(results['passed']/len(test_scenarios)*100):.1f}%")
        
        if results['errors']:
            print(f"\n⚠️ ОШИБКИ:")
            for error in results['errors']:
                print(f"   - {error['scenario']}: {error['reason']}")
        
        # Проверка базы данных
        print(f"\n{'='*80}")
        print("ПРОВЕРКА БАЗЫ ДАННЫХ")
        print(f"{'='*80}")
        
        # Проверка созданных задач
        recent_tasks = session.query(Task).filter(
            Task.user_id == user.id,
            Task.created_at >= datetime.now(pytz.UTC) - timedelta(minutes=10)
        ).all()
        
        print(f"\n📝 Задачи созданные за последние 10 минут: {len(recent_tasks)}")
        for task in recent_tasks[:5]:
            print(f"   - {task.title} (статус: {task.status})")
        
        # Проверка interactions
        recent_interactions = session.query(Interaction).filter(
            Interaction.user_id == user.id,
            Interaction.created_at >= datetime.now(pytz.UTC) - timedelta(minutes=10)
        ).count()
        
        print(f"\n💬 Взаимодействий за последние 10 минут: {recent_interactions}")
        
        # Проверка профиля
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile:
            print(f"\n👤 ПРОФИЛЬ:")
            print(f"   Навыки: {profile.skills or 'НЕ ЗАПОЛНЕНО'}")
            print(f"   Цели: {profile.goals or 'НЕ ЗАПОЛНЕНО'}")
            print(f"   Интересы: {profile.interests or 'НЕ ЗАПОЛНЕНО'}")
        
    finally:
        session.close()


async def test_dashboard_buttons():
    """Тестирование кнопок дашборда через API"""
    
    print(f"\n{'='*80}")
    print("ТЕСТИРОВАНИЕ КНОПОК ДАШБОРДА")
    print(f"{'='*80}")
    
    import aiohttp
    
    # Проверяем доступность API endpoints
    base_url = "http://localhost:8080"  # Локальный тест
    
    endpoints_to_test = [
        {'method': 'GET', 'path': '/api/tasks', 'name': 'Получение задач'},
        {'method': 'GET', 'path': '/api/partners', 'name': 'Получение контактов'},
        {'method': 'GET', 'path': '/api/feed', 'name': 'Получение ленты новостей'},
        {'method': 'GET', 'path': '/api/profile', 'name': 'Получение профиля'},
    ]
    
    print("\n⚠️ ВНИМАНИЕ: Для тестирования API нужно запустить сервер")
    print("   Запустите: python main.py")
    print("   Затем запустите этот тест снова\n")
    
    # Пока просто выводим что нужно протестировать
    print("📋 Endpoints для тестирования:")
    for endpoint in endpoints_to_test:
        print(f"   - {endpoint['method']} {endpoint['path']}: {endpoint['name']}")


if __name__ == "__main__":
    print("\n" + "="*80)
    print("ЗАПУСК КОМПЛЕКСНОГО ТЕСТИРОВАНИЯ")
    print("="*80)
    
    # Тест агента
    asyncio.run(test_agent_comprehensive())
    
    # Тест кнопок дашборда
    asyncio.run(test_dashboard_buttons())
    
    print("\n" + "="*80)
    print("ТЕСТИРОВАНИЕ ЗАВЕРШЕНО")
    print("="*80)
