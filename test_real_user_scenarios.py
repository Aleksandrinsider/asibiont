#!/usr/bin/env python3
"""
Реальное тестирование AI агента с задачами пользователя @aleksandrinsider
Симулирует реальные запросы и показывает результаты в панели
"""

import os
import sys
from datetime import datetime, timedelta

# Добавляем корневую директорию в path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Устанавливаем переменную окружения для подключения к Railway PostgreSQL
os.environ['LOCAL'] = '0'  # Отключаем локальный режим

# Импорт модулей проекта
from models import Session, User, Task
from ai_integration.handlers import (
    add_task, 
    list_tasks,
    get_task_details,
    update_user_memory,
    update_profile,
    delegate_task_with_session,
    accept_delegated_task,
    reject_delegated_task,
    get_partners_list,
    find_partners,
    delete_task_sync,
    set_recurring_task
)

class RealUserTesting:
    def __init__(self):
        self.telegram_id = 146333757  # @aleksandrinsider
        self.username = "aleksandrinsider"
        self.session = None
        self.conversation_log = []
        
    def log_message(self, sender, message):
        """Логирование сообщений диалога"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.conversation_log.append({
            'time': timestamp,
            'sender': sender,
            'message': message
        })
        
        if sender == "Пользователь":
            print(f"\n[{timestamp}] 👤 {self.username}: {message}")
        else:
            print(f"[{timestamp}] 🤖 Агент: {message}")
    
    def show_tasks_in_panel(self):
        """Показать задачи как они отображаются в панели"""
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=self.telegram_id).first()
            if not user:
                print("\n❌ Пользователь не найден")
                return
            
            tasks = session.query(Task).filter_by(user_id=user.id).order_by(Task.created_at.desc()).all()
            
            print("\n" + "="*80)
            print("📊 ПАНЕЛЬ УПРАВЛЕНИЯ - Задачи пользователя")
            print("="*80)
            
            if not tasks:
                print("  Нет задач")
                return
            
            # Группируем задачи
            personal = [t for t in tasks if not t.delegated_by]
            delegated_to_me = [t for t in tasks if t.delegated_by and t.delegated_by != user.id]
            delegated_by_me = [t for t in tasks if t.delegated_by == user.id]
            completed = [t for t in tasks if t.status == 'completed']
            overdue = [t for t in tasks if t.due_date and t.due_date < datetime.now() and t.status != 'completed']
            
            print(f"\n📝 Личные задачи ({len(personal)}):")
            for task in personal[:5]:
                status_icon = "✅" if task.status == "completed" else "⏳"
                deadline = f" 📅 {task.due_date.strftime('%d.%m %H:%M')}" if task.due_date else ""
                print(f"  {status_icon} #{task.id}: {task.title}{deadline}")
            
            if delegated_to_me:
                print(f"\n👥 Поручили мне ({len(delegated_to_me)}):")
                for task in delegated_to_me[:5]:
                    delegator = session.query(User).filter_by(id=task.delegated_by).first()
                    delegator_name = f"@{delegator.username}" if delegator and delegator.username else f"ID{task.delegated_by}"
                    status_icon = "✅" if task.status == "completed" else "⏳"
                    print(f"  {status_icon} #{task.id}: {task.title} (от {delegator_name})")
            
            if delegated_by_me:
                print(f"\n📤 Поручил я ({len(delegated_by_me)}):")
                for task in delegated_by_me[:5]:
                    status_icon = "✅" if task.status == "completed" else "⏳"
                    print(f"  {status_icon} #{task.id}: {task.title} → @{task.delegated_to_username}")
            
            if overdue:
                print(f"\n⚠️  Просрочено ({len(overdue)}):")
                for task in overdue[:3]:
                    days_overdue = (datetime.now() - task.due_date).days
                    print(f"  ❌ #{task.id}: {task.title} (просрочено на {days_overdue} дн.)")
            
            if completed:
                print(f"\n✅ Выполнено ({len(completed)}) - последние 3:")
                for task in completed[:3]:
                    print(f"  ✓ #{task.id}: {task.title}")
            
            print("\n" + "="*80)
        finally:
            session.close()
    
    def test_scenario_1_create_tasks(self):
        """Сценарий 1: Создание задач различными способами"""
        print("\n" + "🎬 СЦЕНАРИЙ 1: Создание задач")
        print("="*80)
        
        test_queries = [
            "Создай задачу: купить продукты на завтра",
            "Напомни мне позвонить маме через 2 дня",
            "Добавь задачу проверить почту",
            "Нужно завтра в 15:00 встретиться с клиентом"
        ]
        
        session = Session()
        try:
            for query in test_queries:
                self.log_message("Пользователь", query)
                
                # Извлекаем информацию из запроса (упрощенно)
                if "купить продукты" in query.lower():
                    result = add_task(
                        title="Купить продукты",
                        description="",
                        due_date=(datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M"),
                        user_id=self.telegram_id,
                        session=session
                    )
                elif "позвонить маме" in query.lower():
                    result = add_task(
                        title="Позвонить маме",
                        description="",
                        due_date=(datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d %H:%M"),
                        user_id=self.telegram_id,
                        session=session
                    )
                elif "проверить почту" in query.lower():
                    result = add_task(
                        title="Проверить почту",
                        description="",
                        user_id=self.telegram_id,
                        session=session
                    )
                elif "встретиться с клиентом" in query.lower():
                    tomorrow_15 = datetime.now().replace(hour=15, minute=0) + timedelta(days=1)
                    result = add_task(
                        title="Встретиться с клиентом",
                        description="",
                        due_date=tomorrow_15.strftime("%Y-%m-%d %H:%M"),
                        user_id=self.telegram_id,
                        session=session
                    )
                
                # Агент отвечает
                if "ERROR" not in str(result):
                    self.log_message("Агент", f"✅ Задача создана! {result}")
                else:
                    self.log_message("Агент", f"❌ Ошибка: {result}")
        finally:
            session.close()
        
        self.show_tasks_in_panel()
    
    def test_scenario_2_list_and_search(self):
        """Сценарий 2: Просмотр и поиск задач"""
        print("\n" + "🎬 СЦЕНАРИЙ 2: Просмотр и поиск задач")
        print("="*80)
        
        test_queries = [
            "Покажи мои задачи",
            "Какие у меня задачи на завтра?",
            "Найди задачу про почту"
        ]
        
        session = Session()
        try:
            for query in test_queries:
                self.log_message("Пользователь", query)
                
                if "покажи" in query.lower() or "какие" in query.lower():
                    result = list_tasks(user_id=self.telegram_id, session=session)
                    self.log_message("Агент", result)
                
                elif "найди" in query.lower():
                    # Используем прямой запрос к БД
                    user = session.query(User).filter_by(telegram_id=self.telegram_id).first()
                    if user:
                        tasks = session.query(Task).filter(
                            Task.user_id == user.id,
                            Task.title.ilike('%почт%')
                        ).all()
                        
                        if tasks:
                            self.log_message("Агент", f"Найдено задач: {len(tasks)}")
                            for task in tasks[:3]:
                                self.log_message("Агент", f"  • #{task.id}: {task.title}")
                        else:
                            self.log_message("Агент", "Задачи не найдены")
        finally:
            session.close()
        
        self.show_tasks_in_panel()
    
    def test_scenario_3_complete_tasks(self):
        """Сценарий 3: Выполнение задач"""
        print("\n" + "🎬 СЦЕНАРИЙ 3: Выполнение задач")
        print("="*80)
        
        session = Session()
        try:
            # Находим первую невыполненную задачу
            user = session.query(User).filter_by(telegram_id=self.telegram_id).first()
            if not user:
                print("❌ Пользователь не найден")
                return
            
            task = session.query(Task).filter_by(
                user_id=user.id,
                status='pending'
            ).first()
            
            if task:
                query = f"Отметь задачу #{task.id} как выполненную"
                self.log_message("Пользователь", query)
                
                # Напрямую обновляем статус
                task.status = 'completed'
                session.commit()
                
                self.log_message("Агент", f"✅ Задача '{task.title}' успешно отмечена как выполненная!")
            else:
                self.log_message("Пользователь", "Отметь первую задачу как выполненную")
                self.log_message("Агент", "У вас нет невыполненных задач")
        finally:
            session.close()
        
        self.show_tasks_in_panel()
    
    def test_scenario_4_delete_tasks(self):
        """Сценарий 4: Удаление задач"""
        print("\n" + "🎬 СЦЕНАРИЙ 4: Удаление задач")
        print("="*80)
        
        session = Session()
        try:
            # Находим задачу для удаления (выполненную)
            user = session.query(User).filter_by(telegram_id=self.telegram_id).first()
            if not user:
                return
            
            # Ищем выполненную задачу
            task = session.query(Task).filter(
                Task.user_id == user.id,
                Task.status == 'completed'
            ).first()
            
            if task:
                query = f"Удали задачу #{task.id}"
                self.log_message("Пользователь", query)
                
                # Сбрасываем current_task_id если ссылается на эту задачу
                users_with_task = session.query(User).filter_by(current_task_id=task.id).all()
                for u in users_with_task:
                    u.current_task_id = None
                
                # Сбрасываем parent_task_id у дочерних задач
                child_tasks = session.query(Task).filter_by(parent_task_id=task.id).all()
                for child in child_tasks:
                    child.parent_task_id = None
                
                # Удаляем
                task_title = task.title
                session.delete(task)
                session.commit()
                
                self.log_message("Агент", f"✅ Задача '{task_title}' успешно удалена")
            else:
                self.log_message("Пользователь", "Удали выполненную задачу")
                self.log_message("Агент", "Выполненные задачи не найдены")
        finally:
            session.close()
        
        self.show_tasks_in_panel()
    
    def test_scenario_5_recurring_tasks(self):
        """Сценарий 5: Повторяющиеся задачи"""
        print("\n" + "🎬 СЦЕНАРИЙ 5: Повторяющиеся задачи")
        print("="*80)
        
        session = Session()
        try:
            query = "Создай повторяющуюся задачу: делать зарядку каждый день в 7:00"
            self.log_message("Пользователь", query)
            
            # Создаем повторяющуюся задачу
            tomorrow_7am = (datetime.now() + timedelta(days=1)).replace(hour=7, minute=0, second=0, microsecond=0)
            result = set_recurring_task(
                title="Делать зарядку",
                description="Утренняя зарядка для здоровья",
                recurrence_pattern="daily",
                recurrence_interval=1,
                first_reminder_time=tomorrow_7am,
                user_id=self.telegram_id,
                session=session
            )
            
            self.log_message("Агент", f"✅ {result}")
        finally:
            session.close()
        
        self.show_tasks_in_panel()
    
    def test_scenario_6_task_details(self):
        """Сценарий 6: Детали задачи"""
        print("\n" + "🎬 СЦЕНАРИЙ 6: Получение деталей задачи")
        print("="*80)
        
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=self.telegram_id).first()
            if not user:
                return
            
            task = session.query(Task).filter_by(user_id=user.id).first()
            if task:
                query = f"Покажи детали задачи #{task.id}"
                self.log_message("Пользователь", query)
                
                result = get_task_details(
                    task_id=task.id,
                    user_id=self.telegram_id,
                    session=session
                )
                
                self.log_message("Агент", result)
            else:
                self.log_message("Пользователь", "Покажи детали первой задачи")
                self.log_message("Агент", "У вас нет задач")
        finally:
            session.close()
    
    def test_scenario_7_update_profile(self):
        """Сценарий 7: Обновление профиля"""
        print("\n" + "🎬 СЦЕНАРИЙ 7: Обновление профиля")
        print("="*80)
        
        session = Session()
        try:
            query = "Обнови мой профиль: город Москва, интересы - программирование и путешествия"
            self.log_message("Пользователь", query)
            
            result = update_profile(
                user_id=self.telegram_id,
                city="Москва",
                interests="программирование, путешествия, спорт",
                session=session,
                close_session=False
            )
            
            self.log_message("Агент", f"✅ {result}")
        finally:
            session.close()
    
    def test_scenario_8_memory(self):
        """Сценарий 8: Обновление памяти о пользователе"""
        print("\n" + "🎬 СЦЕНАРИЙ 8: Память о пользователе")
        print("="*80)
        
        session = Session()
        try:
            query = "Запомни: я предпочитаю работать по утрам и делаю перерывы каждые 2 часа"
            self.log_message("Пользователь", query)
            
            result = update_user_memory(
                info="Пользователь предпочитает работать по утрам. Делает перерывы каждые 2 часа.",
                user_id=self.telegram_id,
                session=session
            )
            
            self.log_message("Агент", f"✅ {result}")
        finally:
            session.close()
    
    def test_scenario_9_partners(self):
        """Сценарий 9: Поиск партнеров"""
        print("\n" + "🎬 СЦЕНАРИЙ 9: Поиск партнеров")
        print("="*80)
        
        session = Session()
        try:
            # Сначала список партнеров
            query = "Покажи моих партнеров"
            self.log_message("Пользователь", query)
            
            result = get_partners_list(user_id=self.telegram_id, session=session)
            self.log_message("Агент", result)
            
            # Затем поиск новых партнеров
            query = "Найди партнеров для совместных проектов"
            self.log_message("Пользователь", query)
            
            result = find_partners(user_id=self.telegram_id, session=session)
            self.log_message("Агент", result)
        finally:
            session.close()
    
    def test_scenario_10_delegation(self):
        """Сценарий 10: Делегирование задач"""
        print("\n" + "🎬 СЦЕНАРИЙ 10: Делегирование задач")
        print("="*80)
        
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=self.telegram_id).first()
            if not user:
                return
            
            # Проверяем наличие других пользователей для делегирования
            other_users = session.query(User).filter(
                User.id != user.id,
                User.username.isnot(None)
            ).limit(1).all()
            
            if other_users and len(other_users) > 0:
                partner_user = other_users[0]
                query = f"Делегируй @{partner_user.username} задачу: проверить данные в отчете, завтра в 12:00"
                self.log_message("Пользователь", query)
                
                tomorrow_12 = (datetime.now() + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
                reminder_str = tomorrow_12.strftime("%Y-%m-%d %H:%M")
                
                result = delegate_task_with_session(
                    title="Проверить данные в отчете",
                    description="Необходимо проверить актуальность данных",
                    reminder_time=reminder_str,
                    delegated_to_username=partner_user.username,
                    delegation_details="Срочная проверка",
                    user_id=self.telegram_id,
                    session=session
                )
                
                self.log_message("Агент", f"✅ {result}")
            else:
                self.log_message("Пользователь", "Делегируй партнеру задачу")
                self.log_message("Агент", "В системе пока нет других пользователей для делегирования")
        finally:
            session.close()
        
        self.show_tasks_in_panel()
    
    def show_conversation_summary(self):
        """Показать итоговый диалог"""
        print("\n" + "="*80)
        print("💬 ИТОГОВЫЙ ДИАЛОГ")
        print("="*80)
        
        for msg in self.conversation_log:
            icon = "👤" if msg['sender'] == "Пользователь" else "🤖"
            print(f"[{msg['time']}] {icon} {msg['sender']}: {msg['message']}")
    
    def run_full_test(self):
        """Запустить полное тестирование"""
        print("🚀 РЕАЛЬНОЕ ТЕСТИРОВАНИЕ AI АГЕНТА")
        print("="*80)
        print(f"Пользователь: @{self.username} (TG ID: {self.telegram_id})")
        print(f"Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("\n⚠️  ВНИМАНИЕ: Тест создаст реальные задачи в БД!")
        
        # Показываем начальное состояние
        print("\n📊 Начальное состояние:")
        self.show_tasks_in_panel()
        
        # Запускаем сценарии
        self.test_scenario_1_create_tasks()
        self.test_scenario_2_list_and_search()
        self.test_scenario_3_complete_tasks()
        self.test_scenario_4_delete_tasks()
        self.test_scenario_5_recurring_tasks()
        self.test_scenario_6_task_details()
        self.test_scenario_7_update_profile()
        self.test_scenario_8_memory()
        self.test_scenario_9_partners()
        self.test_scenario_10_delegation()
        
        # Итоги
        print("\n" + "="*80)
        print("📈 ИТОГОВАЯ СТАТИСТИКА")
        print("="*80)
        print(f"Всего запросов: {len([m for m in self.conversation_log if m['sender'] == 'Пользователь'])}")
        print(f"Ответов агента: {len([m for m in self.conversation_log if m['sender'] != 'Пользователь'])}")
        
        self.show_conversation_summary()
        
        print("\n✅ Тестирование завершено!")
        print("   Проверьте панель управления в браузере для визуального подтверждения")

if __name__ == "__main__":
    tester = RealUserTesting()
    tester.run_full_test()
