"""
MVP: Autonomous Delegation Agent для Premium пользователей

Демонстрирует ключевую функциональность:
1. Анализ целей Premium пользователя
2. Автоматический поиск релевантных исполнителей
3. Создание персонализированных задач
4. Автоматическое делегирование с подтверждением
"""

import asyncio
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import pytz
from models import Session, User, Task, UserProfile
from ai_integration.autonomous_agent import HybridAutonomousAgent

logger = logging.getLogger(__name__)


class AutonomousDelegationAgentMVP(HybridAutonomousAgent):
    """
    MVP версия агента для автоматического делегирования
    
    Упрощённая версия для тестирования концепции:
    - Базовый matching по интересам и скиллам
    - Простая персонализация задач
    - Автоматическое делегирование с логированием
    """
    
    def __init__(self):
        super().__init__()
        self.specialization = "autonomous_delegation"
        logger.info("[AUTO_DELEGATE] Initialized AutonomousDelegationAgentMVP")
    
    async def analyze_premium_user_goals(self, premium_user_id: int) -> List[Dict]:
        """
        Анализирует цели Premium пользователя из профиля
        
        Извлекает actionable goals из:
        - UserProfile.goals (текстовое поле)
        - Недавние сообщения пользователя
        - Активные задачи (паттерны)
        
        Returns:
            List[Dict]: Список целей с критериями поиска
        """
        
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=premium_user_id).first()
            if not user:
                logger.error(f"[AUTO_DELEGATE] Premium user {premium_user_id} not found")
                return []
            
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            if not profile or not profile.goals:
                logger.warning(f"[AUTO_DELEGATE] No goals found for user {premium_user_id}")
                return []
            
            # Используем AI для извлечения actionable goals
            system_prompt = f"""Ты агент для автоматизации делегирования задач.

КОНТЕКСТ:
Premium пользователь: {user.username or 'пользователь'}
Цели пользователя: {profile.goals}
Интересы: {profile.interests or 'не указаны'}

ЗАДАЧА: Извлеки actionable цели, для которых можно делегировать задачи другим людям.

Для каждой цели определи:
1. Что нужно сделать (конкретное действие)
2. Кто может помочь (типы людей с какими навыками/интересами)
3. Какие задачи можно делегировать

Верни JSON массив:
[
  {{
    "goal": "Краткое описание цели",
    "action": "Конкретное действие для делегирования",
    "criteria": {{
      "interests": ["список", "интересов"],
      "skills": ["список", "навыков"],
      "location": "география если важно"
    }},
    "priority": "high|medium|low"
  }}
]

Если нет actionable целей для делегирования - верни пустой массив [].
"""

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Проанализируй цели: {profile.goals}"}
            ]
            
            response = await self.call_ai(messages, use_tools=False, temperature=0.3)
            content = response['choices'][0]['message']['content']
            
            # Парсим JSON
            import json
            try:
                if '```json' in content:
                    content = content.split('```json')[1].split('```')[0].strip()
                elif '```' in content:
                    content = content.split('```')[1].split('```')[0].strip()
                
                goals = json.loads(content)
                logger.info(f"[AUTO_DELEGATE] Extracted {len(goals)} actionable goals")
                return goals
                
            except json.JSONDecodeError as e:
                logger.error(f"[AUTO_DELEGATE] Failed to parse goals JSON: {e}")
                return []
        
        finally:
            session.close()
    
    async def find_potential_executors(self, 
                                      criteria: Dict,
                                      premium_user_id: int,
                                      limit: int = 5) -> List[Dict]:
        """
        Находит потенциальных исполнителей по критериям
        
        Упрощённый matching MVP:
        - Поиск по interests в UserProfile
        - Поиск по skills в UserProfile
        - Фильтрация уже перегруженных (>5 active задач)
        - Исключение самого Premium пользователя
        
        Returns:
            List[Dict]: Топ-N кандидатов с scoring
        """
        
        session = Session()
        try:
            premium_user = session.query(User).filter_by(telegram_id=premium_user_id).first()
            if not premium_user:
                return []
            
            # Получаем всех пользователей с профилями
            users_with_profiles = session.query(User, UserProfile)\
                .join(UserProfile, User.id == UserProfile.user_id)\
                .filter(User.id != premium_user.id)\
                .all()
            
            candidates = []
            
            for user, profile in users_with_profiles:
                # Простой scoring на основе совпадения интересов/навыков
                score = 0.0
                matches = []
                
                # Интересы
                if profile.interests and 'interests' in criteria:
                    user_interests = [i.strip().lower() for i in profile.interests.split(',')]
                    target_interests = [i.lower() for i in criteria['interests']]
                    
                    interest_matches = [i for i in target_interests if any(ui in i or i in ui for ui in user_interests)]
                    if interest_matches:
                        score += 0.4 * (len(interest_matches) / len(target_interests))
                        matches.extend([f"Интерес: {i}" for i in interest_matches])
                
                # Навыки
                if profile.skills and 'skills' in criteria:
                    user_skills = [s.strip().lower() for s in profile.skills.split(',')]
                    target_skills = [s.lower() for s in criteria['skills']]
                    
                    skill_matches = [s for s in target_skills if any(us in s or s in us for us in user_skills)]
                    if skill_matches:
                        score += 0.6 * (len(skill_matches) / len(target_skills))
                        matches.extend([f"Навык: {s}" for s in skill_matches])
                
                # География (опционально)
                if 'location' in criteria and criteria['location'] and profile.city:
                    if criteria['location'].lower() in profile.city.lower():
                        score += 0.2
                        matches.append(f"География: {profile.city}")
                
                # Проверяем загрузку - не больше 5 active задач
                active_tasks_count = session.query(Task).filter(
                    Task.delegated_to_username == user.username,
                    Task.delegation_status == "accepted",
                    Task.status.in_(["pending", "active", "in_progress"])
                ).count()
                
                if active_tasks_count >= 5:
                    score *= 0.5  # Снижаем score если перегружен
                    matches.append(f"⚠️ Загружен ({active_tasks_count} активных задач)")
                
                # Добавляем кандидата если есть хоть какое-то совпадение
                if score > 0.2:
                    candidates.append({
                        "user": user,
                        "score": score,
                        "matches": matches,
                        "active_tasks": active_tasks_count
                    })
            
            # Сортируем по score
            candidates.sort(key=lambda x: x['score'], reverse=True)
            
            logger.info(f"[AUTO_DELEGATE] Found {len(candidates)} potential executors")
            
            return candidates[:limit]
        
        finally:
            session.close()
    
    async def create_personalized_task_message(self,
                                              premium_user: User,
                                              executor: User,
                                              executor_profile: UserProfile,
                                              goal_context: Dict) -> Dict:
        """
        AI создаёт персонализированную задачу для исполнителя
        
        Использует:
        - Профиль исполнителя (интересы, навыки)
        - Контекст цели
        - Стиль общения (дружелюбный, профессиональный)
        
        Returns:
            Dict: {"title": "...", "description": "...", "deadline_days": 3}
        """
        
        system_prompt = f"""Ты помощник Premium пользователя, создаёшь задачу для делегирования.

КОНТЕКСТ:
- Premium пользователь: {premium_user.username or premium_user.first_name}
- Цель: {goal_context['goal']}
- Действие: {goal_context['action']}

ПОЛУЧАТЕЛЬ:
- Имя: {executor.username or executor.first_name}
- Интересы: {executor_profile.interests if executor_profile else 'не указаны'}
- Навыки: {executor_profile.skills if executor_profile else 'не указаны'}

ЗАДАЧА: Создай персонализированную задачу для делегирования.

ПРАВИЛА:
1. Обращение по имени (если есть)
2. Упомяни релевантность для получателя (интересы/навыки)
3. Конкретное действие (что сделать)
4. Польза для получателя (мотивация)
5. Дружелюбный тон, не давить
6. Оценка времени выполнения
7. Дедлайн разумный (2-5 дней)

Верни JSON:
{{
  "title": "Краткое название задачи (3-7 слов)",
  "description": "Полное описание с персонализацией",
  "deadline_days": 3
}}
"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "Создай задачу"}
        ]
        
        response = await self.call_ai(messages, use_tools=False, temperature=0.7)
        content = response['choices'][0]['message']['content']
        
        # Парсим JSON
        import json
        try:
            if '```json' in content:
                content = content.split('```json')[1].split('```')[0].strip()
            elif '```' in content:
                content = content.split('```')[1].split('```')[0].strip()
            
            task_data = json.loads(content)
            logger.info(f"[AUTO_DELEGATE] Created personalized task: {task_data['title']}")
            return task_data
            
        except json.JSONDecodeError as e:
            logger.error(f"[AUTO_DELEGATE] Failed to parse task JSON: {e}")
            # Fallback
            return {
                "title": f"{goal_context['action']}",
                "description": f"Помоги с задачей: {goal_context['goal']}",
                "deadline_days": 3
            }
    
    async def delegate_task_automatically(self,
                                        premium_user_id: int,
                                        executor_user_id: int,
                                        task_data: Dict,
                                        goal_context: Dict,
                                        dry_run: bool = True) -> Dict:
        """
        Автоматически делегирует задачу
        
        Args:
            premium_user_id: ID Premium пользователя
            executor_user_id: ID исполнителя
            task_data: Данные задачи (title, description, deadline_days)
            goal_context: Контекст цели
            dry_run: Если True - только логирует, не создаёт задачу
        
        Returns:
            Dict: Результат делегирования
        """
        
        if dry_run:
            logger.info(f"[AUTO_DELEGATE] DRY RUN: Would delegate task '{task_data['title']}' to user {executor_user_id}")
            return {
                "success": True,
                "dry_run": True,
                "task_id": None,
                "message": f"DRY RUN: Task '{task_data['title']}' would be delegated"
            }
        
        # Реальное делегирование
        from . import handlers
        
        session = Session()
        try:
            # Создаём задачу для Premium пользователя
            premium_user = session.query(User).filter_by(telegram_id=premium_user_id).first()
            executor_user = session.query(User).filter_by(telegram_id=executor_user_id).first()
            
            if not premium_user or not executor_user:
                return {"success": False, "error": "User not found"}
            
            # Создаём задачу
            result = await handlers.add_task(
                user_id=premium_user_id,
                title=task_data['title'],
                description=task_data['description'],
                reminder_time=f"через {task_data['deadline_days']} дня",
                session=session,
                close_session=False
            )
            
            # Получаем ID созданной задачи (парсим из result)
            # Упрощённо: ищем задачу по title
            task = session.query(Task).filter(
                Task.user_id == premium_user.id,
                Task.title == task_data['title']
            ).order_by(Task.created_at.desc()).first()
            
            if not task:
                return {"success": False, "error": "Failed to create task"}
            
            # Делегируем задачу
            delegate_result = await handlers.delegate_task(
                task_id=task.id,
                delegate_to=executor_user.username,
                user_id=premium_user_id,
                session=session,
                close_session=False
            )
            
            session.commit()
            
            logger.info(f"[AUTO_DELEGATE] Successfully delegated task {task.id} to {executor_user.username}")
            
            return {
                "success": True,
                "task_id": task.id,
                "executor": executor_user.username,
                "message": f"Task delegated successfully"
            }
        
        except Exception as e:
            session.rollback()
            logger.error(f"[AUTO_DELEGATE] Error delegating task: {e}")
            return {
                "success": False,
                "error": str(e)
            }
        
        finally:
            session.close()
    
    async def run_automation_cycle(self, 
                                   premium_user_id: int,
                                   max_delegations: int = 3,
                                   dry_run: bool = True) -> Dict:
        """
        Полный цикл автоматизации:
        1. Анализ целей
        2. Поиск исполнителей
        3. Создание задач
        4. Делегирование
        
        Args:
            premium_user_id: ID Premium пользователя
            max_delegations: Максимум делегирований за цикл
            dry_run: Если True - только симуляция
        
        Returns:
            Dict: Подробный отчёт о выполнении
        """
        
        logger.info(f"[AUTO_DELEGATE] Starting automation cycle for user {premium_user_id} (dry_run={dry_run})")
        
        report = {
            "user_id": premium_user_id,
            "timestamp": datetime.now(pytz.UTC).isoformat(),
            "dry_run": dry_run,
            "goals_analyzed": 0,
            "executors_found": 0,
            "tasks_created": 0,
            "delegations_successful": 0,
            "delegations_failed": 0,
            "details": []
        }
        
        # Шаг 1: Анализ целей
        goals = await self.analyze_premium_user_goals(premium_user_id)
        report['goals_analyzed'] = len(goals)
        
        if not goals:
            logger.info("[AUTO_DELEGATE] No actionable goals found")
            report['message'] = "No actionable goals found for automation"
            return report
        
        session = Session()
        try:
            premium_user = session.query(User).filter_by(telegram_id=premium_user_id).first()
            
            delegations_count = 0
            
            # Обрабатываем каждую цель
            for goal in goals:
                if delegations_count >= max_delegations:
                    logger.info(f"[AUTO_DELEGATE] Reached max delegations limit ({max_delegations})")
                    break
                
                goal_detail = {
                    "goal": goal['goal'],
                    "action": goal['action'],
                    "executors": [],
                    "delegations": []
                }
                
                # Шаг 2: Поиск исполнителей
                executors = await self.find_potential_executors(
                    goal['criteria'],
                    premium_user_id,
                    limit=3
                )
                
                report['executors_found'] += len(executors)
                
                if not executors:
                    goal_detail['message'] = "No suitable executors found"
                    report['details'].append(goal_detail)
                    continue
                
                # Шаг 3-4: Создание задач и делегирование
                for executor_data in executors:
                    if delegations_count >= max_delegations:
                        break
                    
                    executor = executor_data['user']
                    executor_profile = session.query(UserProfile).filter_by(user_id=executor.id).first()
                    
                    goal_detail['executors'].append({
                        "username": executor.username,
                        "score": executor_data['score'],
                        "matches": executor_data['matches']
                    })
                    
                    # Создаём персонализированную задачу
                    task_data = await self.create_personalized_task_message(
                        premium_user,
                        executor,
                        executor_profile,
                        goal
                    )
                    
                    report['tasks_created'] += 1
                    
                    # Делегируем
                    delegation_result = await self.delegate_task_automatically(
                        premium_user_id,
                        executor.telegram_id,
                        task_data,
                        goal,
                        dry_run=dry_run
                    )
                    
                    if delegation_result['success']:
                        report['delegations_successful'] += 1
                        delegations_count += 1
                    else:
                        report['delegations_failed'] += 1
                    
                    goal_detail['delegations'].append({
                        "executor": executor.username,
                        "task_title": task_data['title'],
                        "result": delegation_result
                    })
                
                report['details'].append(goal_detail)
        
        finally:
            session.close()
        
        logger.info(f"[AUTO_DELEGATE] Automation cycle completed: {report['delegations_successful']} successful")
        
        return report


# Вспомогательная функция для форматирования отчёта
async def format_automation_report(report: Dict) -> str:
    """
    Форматирует отчёт автоматизации в читаемый вид
    
    Args:
        report: Словарь с результатами автоматизации
    
    Returns:
        str: Форматированный отчёт
    """
    
    output = [
        "=" * 70,
        "🤖 AUTONOMOUS DELEGATION REPORT",
        "=" * 70,
        ""
    ]
    
    if report.get('dry_run'):
        output.append("⚠️ DRY RUN MODE (симуляция, задачи не создавались)")
        output.append("")
    
    output.extend([
        f"📊 Статистика:",
        f"   Проанализировано целей: {report['goals_analyzed']}",
        f"   Найдено исполнителей: {report['executors_found']}",
        f"   Создано задач: {report['tasks_created']}",
        f"   ✅ Успешных делегирований: {report['delegations_successful']}",
        f"   ❌ Неудачных: {report['delegations_failed']}",
        ""
    ])
    
    if report['details']:
        output.append("📋 Детали по целям:")
        output.append("")
        
        for i, detail in enumerate(report['details'], 1):
            output.append(f"Цель {i}: {detail['goal']}")
            output.append(f"Действие: {detail['action']}")
            
            if detail['executors']:
                output.append(f"Найдено исполнителей: {len(detail['executors'])}")
                for executor in detail['executors']:
                    output.append(f"   • {executor['username']} (score: {executor['score']:.2f})")
                    output.append(f"     Совпадения: {', '.join(executor['matches'])}")
            
            if detail['delegations']:
                output.append(f"Делегирования:")
                for delegation in detail['delegations']:
                    status = "✅" if delegation['result']['success'] else "❌"
                    output.append(f"   {status} → {delegation['executor']}: {delegation['task_title']}")
            
            output.append("")
    
    output.append("=" * 70)
    
    return "\n".join(output)


# Тестовая функция
async def test_autonomous_delegation():
    """Тестирует автономное делегирование"""
    
    print("\n🤖 ТЕСТ AUTONOMOUS DELEGATION AGENT MVP")
    print("=" * 70)
    
    # Premium пользователь (нужно создать в БД с целями)
    premium_user_id = 123456789
    
    session = Session()
    user = session.query(User).filter_by(telegram_id=premium_user_id).first()
    if not user:
        print("❌ Premium пользователь не найден")
        print("Создайте пользователя с целями в UserProfile.goals")
        session.close()
        return
    
    profile = session.query(UserProfile).filter_by(user_id=user.id).first()
    if not profile or not profile.goals:
        print("❌ У пользователя не указаны цели")
        print("Добавьте цели в UserProfile.goals")
        session.close()
        return
    
    print(f"✅ Premium пользователь: {user.username}")
    print(f"Цели: {profile.goals}\n")
    session.close()
    
    # Создаём агента
    agent = AutonomousDelegationAgentMVP()
    
    # Запускаем цикл автоматизации (DRY RUN)
    print("🚀 Запуск automation cycle (DRY RUN)...\n")
    
    report = await agent.run_automation_cycle(
        premium_user_id=premium_user_id,
        max_delegations=3,
        dry_run=True  # Симуляция
    )
    
    # Форматируем и выводим отчёт
    formatted_report = await format_automation_report(report)
    print(formatted_report)
    
    print("\n💡 Для реального делегирования запустите с dry_run=False")


if __name__ == "__main__":
    asyncio.run(test_autonomous_delegation())
