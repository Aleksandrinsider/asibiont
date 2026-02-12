#!/usr/bin/env python3
"""
Упрощенный тест диалога агента для LIGHT тарифа с локальной БД
Создание тестовых пользователей и проверка поиска партнеров
"""
import asyncio
import sys
import os
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_integration.autonomous_agent import chat_with_ai
from models import Session, User, UserProfile, Subscription, SubscriptionTier, init_db
from config import DATABASE_URL
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

class QuickDialogTester:
    """Быстрый тест диалога для анализа с локальной БД"""

    def __init__(self):
        self.conversation_history = []
        self.user_id = 77777  # LIGHT тариф
        self.engine = create_engine(DATABASE_URL)
        self.Session = sessionmaker(bind=self.engine)

    def create_test_users(self):
        """Создание тестовых пользователей с разными профилями"""

        session = self.Session()

        try:
            # Очищаем существующие тестовые данные
            session.query(UserProfile).filter(UserProfile.user_id.in_([77777, 77778, 77779, 77780, 77781])).delete()
            session.query(User).filter(User.telegram_id.in_([77777, 77778, 77779, 77780, 77781])).delete()
            session.commit()

            # Создаем тестового пользователя (LIGHT)
            test_user = User(
                telegram_id=77777,
                username='test_ai_dev',
                first_name='Алексей Иванов',
                created_at=datetime.now()
            )
            session.add(test_user)
            session.commit()  # Сохраняем, чтобы получить ID

            # Профиль для тестового пользователя
            test_profile = UserProfile(
                user_id=test_user.id,  # Используем реальный ID
                city='Москва',
                skills='Python, AI, машинное обучение, разработка',
                interests='бизнес, ИИ, программирование, стартапы',
                goals='разработка AI агентов, поиск партнеров, продвижение продукта',
                current_plans='Разработка автономного AI агента для бизнеса'
            )
            session.add(test_profile)

            # Партнер 1: Похожие интересы, другой город
            partner1 = User(telegram_id=77778, username='partner_ai', first_name='Мария Петрова')
            session.add(partner1)
            session.commit()

            partner1_profile = UserProfile(
                user_id=partner1.id,  # Используем реальный ID
                city='Санкт-Петербург',
                skills='Python, разработка, веб-технологии',
                interests='ИИ, программирование, бизнес',
                goals='создание AI решений, партнерства',
                current_plans='Работа над AI проектами'
            )
            session.add(partner1_profile)

            # Партнер 2: Совпадение по навыкам, другой город
            partner2 = User(telegram_id=77779, username='dev_spb', first_name='Дмитрий Сидоров')
            session.add(partner2)
            session.commit()

            partner2_profile = UserProfile(
                user_id=partner2.id,  # Используем реальный ID
                city='Санкт-Петербург',
                skills='Python, AI, data science, разработка',
                interests='технологии, стартапы, инвестиции',
                goals='разработка AI продуктов',
                current_plans='Создание ML моделей'
            )
            session.add(partner2_profile)

            # Партнер 3: Совпадение по целям, Москва
            partner3 = User(telegram_id=77780, username='business_msk', first_name='Елена Кузнецова')
            session.add(partner3)
            session.commit()

            partner3_profile = UserProfile(
                user_id=partner3.id,  # Используем реальный ID
                city='Москва',
                skills='менеджмент, маркетинг, продажи',
                interests='бизнес, стартапы, ИИ',
                goals='поиск партнеров, продвижение продукта, развитие бизнеса',
                current_plans='Расширение партнерской сети'
            )
            session.add(partner3_profile)

            # Партнер 4: Минимальное совпадение
            partner4 = User(telegram_id=77781, username='designer_ekb', first_name='Андрей Васильев')
            session.add(partner4)
            session.commit()

            partner4_profile = UserProfile(
                user_id=partner4.id,  # Используем реальный ID
                city='Екатеринбург',
                skills='дизайн, UX/UI, графика',
                interests='креатив, искусство, дизайн',
                goals='создание красивых интерфейсов',
                current_plans='Работа над дизайном приложений'
            )
            session.add(partner4_profile)

            session.commit()  # Финальный коммит всех профилей

            session.commit()
            print("✅ Созданы тестовые пользователи:")
            print("   👤 test_ai_dev (Москва): AI, Python, бизнес")
            print("   👥 partner_ai (СПб): AI, Python, бизнес")
            print("   👥 dev_spb (СПб): AI, Python, data science")
            print("   👥 business_msk (Москва): бизнес, стартапы, ИИ")
            print("   👥 designer_ekb (Екатеринбург): дизайн, UX/UI")

        except Exception as e:
            print(f"❌ Ошибка создания тестовых пользователей: {e}")
            session.rollback()
        finally:
            session.close()

    async def run_quick_test(self):
        """Запуск быстрого теста из 5 шагов"""

        print("🧪 БЫСТРЫЙ ТЕСТ АГЕНТА - LIGHT Тариф (с локальной БД)")
        print("=" * 60)

        # Создаем тестовых пользователей
        self.create_test_users()

        # Предопределенные сообщения пользователя (AI разработчик)
        user_messages = [
            "Привет! Я разрабатываю AI агента и хочу найти партнеров для развития проекта",
            "Можешь помочь найти людей с похожими интересами в AI и программировании?",
            "Расскажи про новости в сфере AI за последнюю неделю",
            "Как лучше презентовать мой AI продукт малому бизнесу?",
            "Спасибо за помощь! До свидания"
        ]

        for step, user_message in enumerate(user_messages, 1):
            print(f"\n[ШАГ {step}/5]")
            print(f"👤 ПОЛЬЗОВАТЕЛЬ: {user_message}")

            try:
                # Получаем ответ агента
                response = await chat_with_ai(user_message, user_id=self.user_id)
                agent_response = response.get('response', 'Ошибка ответа')
                tools_called = response.get('tool_calls', [])

                print(f"🤖 АГЕНТ: {agent_response[:200]}{'...' if len(agent_response) > 200 else ''}")
                print(f"🔧 ИНСТРУМЕНТЫ: {len(tools_called)} вызваны")

                # Специальная проверка для поиска партнеров
                if step == 2 and "партнер" in user_message.lower():
                    print("\n🔍 ПРОВЕРКА РЕЗУЛЬТАТОВ ПОИСКА ПАРТНЕРОВ:")
                    # Проверяем, что агент действительно нашел партнеров
                    if any("partner" in agent_response.lower() or "нашел" in agent_response.lower() or "@" in agent_response):
                        print("   ✅ Агент нашел партнеров!")
                    else:
                        print("   ❌ Агент не показал результаты поиска партнеров")

                # Сохраняем в историю
                self.conversation_history.append({
                    'step': step,
                    'user': user_message,
                    'agent': agent_response,
                    'tools': tools_called
                })

            except Exception as e:
                print(f"❌ ОШИБКА: {e}")
                break

            await asyncio.sleep(1)

        # Анализ результатов
        self.analyze_results()

    def analyze_results(self):
        """Анализ результатов теста"""

        print(f"\n{'='*60}")
        print("📊 АНАЛИЗ РЕЗУЛЬТАТОВ")
        print('='*60)

        total_steps = len(self.conversation_history)
        total_tools = sum(len(msg['tools']) for msg in self.conversation_history)

        print(f"✅ ПРОЙДЕНО ШАГОВ: {total_steps}/5")
        print(f"🔧 ВСЕГО ИНСТРУМЕНТОВ: {total_tools}")

        # Анализ по категориям
        print(f"\n🎯 СИЛЬНЫЕ СТОРОНЫ:")

        # 1. Контекстность
        context_mentions = sum(1 for msg in self.conversation_history
                              if any(word in msg['agent'].lower()
                                    for word in ['время', 'погода', 'профиль', 'история']))
        print(f"   🧠 КОНТЕКСТНОСТЬ: {context_mentions}/{total_steps} - {'ХОРОШО' if context_mentions >= 2 else 'СРЕДНЕ'}")

        # 2. Проактивность
        proactive_phrases = sum(1 for msg in self.conversation_history
                               if any(word in msg['agent'].lower()
                                     for word in ['предлагаю', 'создадим', 'найдем', 'давай', 'могу помочь']))
        print(f"   🚀 ПРОАКТИВНОСТЬ: {proactive_phrases}/{total_steps} - {'ОТЛИЧНО' if proactive_phrases >= 3 else 'ХОРОШО' if proactive_phrases >= 2 else 'СЛАБО'}")

        # 3. Исполнение
        execution_score = sum(1 for msg in self.conversation_history if msg['tools'])
        print(f"   ⚡ ИСПОЛНЕНИЕ: {execution_score}/{total_steps} - {'ОТЛИЧНО' if execution_score >= 3 else 'ХОРОШО' if execution_score >= 2 else 'СЛАБО'}")

        # 4. Естественность
        natural_phrases = sum(1 for msg in self.conversation_history
                             if any(word in msg['agent'].lower()
                                   for word in ['понимаю', 'интересно', 'отлично', 'спасибо', 'ясно']))
        print(f"   💬 ЕСТЕСТВЕННОСТЬ: {natural_phrases}/{total_steps} - {'ОТЛИЧНО' if natural_phrases >= 3 else 'ХОРОШО'}")

        # 5. Поиск партнеров (новая метрика)
        partners_found = sum(1 for msg in self.conversation_history
                            if "партнер" in msg['user'].lower() and
                            ("нашел" in msg['agent'].lower() or "@" in msg['agent'] or "partner" in msg['agent'].lower()))
        print(f"   👥 ПОИСК ПАРТНЕРОВ: {partners_found}/1 - {'ОТЛИЧНО' if partners_found >= 1 else 'ПЛОХО'}")

        print(f"\n❌ СЛАБЫЕ СТОРОНЫ:")

        # Анализ проблем
        issues = []

        # 1. Повторяемость инструментов
        tool_names = []
        for msg in self.conversation_history:
            for tool in msg['tools']:
                if isinstance(tool, dict):
                    tool_names.append(tool.get('function', {}).get('name', 'unknown'))
                else:
                    tool_names.append(str(tool))

        unique_tools = set(tool_names)
        if len(unique_tools) < total_tools * 0.7:  # Менее 70% уникальных инструментов
            issues.append("🔄 ПОВТОРЯЕМОСТЬ: Агент часто использует одни и те же инструменты")

        # 2. Отсутствие персонализации
        personalization = sum(1 for msg in self.conversation_history
                             if 'партнер' in msg['agent'].lower() or 'профиль' in msg['agent'].lower())
        if personalization < 2:
            issues.append("👤 ПЕРСОНАЛИЗАЦИЯ: Мало внимания к профилю и интересам пользователя")

        # 3. Длина ответов
        long_responses = sum(1 for msg in self.conversation_history if len(msg['agent']) > 500)
        if long_responses > total_steps * 0.6:  # Более 60% длинных ответов
            issues.append("📏 ДЛИНА ОТВЕТОВ: Ответы слишком длинные, можно короче")

        # 4. Конкретность
        concrete_suggestions = sum(1 for msg in self.conversation_history
                                  if any(word in msg['agent'].lower()
                                        for word in ['конкретно', 'сделай', 'попробуй', 'начни']))
        if concrete_suggestions < 2:
            issues.append("🎯 КОНКРЕТНОСТЬ: Мало конкретных actionable предложений")

        # 5. Качество поиска партнеров
        if partners_found == 0:
            issues.append("👥 ПОИСК ПАРТНЕРОВ: Агент не показал результаты поиска партнеров")

        if not issues:
            issues.append("✨ ПРОБЛЕМ НЕ НАЙДЕНО: Агент работает хорошо!")

        for issue in issues:
            print(f"   {issue}")

        print(f"\n💡 РЕКОМЕНДАЦИИ ДЛЯ УЛУЧШЕНИЯ:")
        print("   1. Исправить логику выбора инструментов для поиска партнеров")
        print("   2. Добавить больше вариативности в выборе инструментов")
        print("   3. Увеличить персонализацию ответов на основе профиля")
        print("   4. Сократить длину ответов, сделать их более concise")
        print("   5. Добавить больше конкретных actionable шагов")
        print("   6. Показывать реальные результаты поиска партнеров")

        # Сохраняем результаты
        with open('quick_test_results_local.json', 'w', encoding='utf-8') as f:
            json.dump(self.conversation_history, f, ensure_ascii=False, indent=2)

        print(f"\n💾 РЕЗУЛЬТАТЫ СОХРАНЕНЫ В: quick_test_results_local.json")

    def analyze_results(self):
        """Анализ результатов теста"""

        print(f"\n{'='*50}")
        print("📊 АНАЛИЗ РЕЗУЛЬТАТОВ")
        print('='*50)

        total_steps = len(self.conversation_history)
        total_tools = sum(len(msg['tools']) for msg in self.conversation_history)

        print(f"✅ ПРОЙДЕНО ШАГОВ: {total_steps}/5")
        print(f"🔧 ВСЕГО ИНСТРУМЕНТОВ: {total_tools}")

        # Анализ по категориям
        print(f"\n🎯 СИЛЬНЫЕ СТОРОНЫ:")

        # 1. Контекстность
        context_mentions = sum(1 for msg in self.conversation_history
                              if any(word in msg['agent'].lower()
                                    for word in ['время', 'погода', 'профиль', 'история']))
        print(f"   🧠 КОНТЕКСТНОСТЬ: {context_mentions}/{total_steps} - {'ХОРОШО' if context_mentions >= 2 else 'СРЕДНЕ'}")

        # 2. Проактивность
        proactive_phrases = sum(1 for msg in self.conversation_history
                               if any(word in msg['agent'].lower()
                                     for word in ['предлагаю', 'создадим', 'найдем', 'давай', 'могу помочь']))
        print(f"   🚀 ПРОАКТИВНОСТЬ: {proactive_phrases}/{total_steps} - {'ОТЛИЧНО' if proactive_phrases >= 3 else 'ХОРОШО' if proactive_phrases >= 2 else 'СЛАБО'}")

        # 3. Исполнение
        execution_score = sum(1 for msg in self.conversation_history if msg['tools'])
        print(f"   ⚡ ИСПОЛНЕНИЕ: {execution_score}/{total_steps} - {'ОТЛИЧНО' if execution_score >= 3 else 'ХОРОШО' if execution_score >= 2 else 'СЛАБО'}")

        # 4. Естественность
        natural_phrases = sum(1 for msg in self.conversation_history
                             if any(word in msg['agent'].lower()
                                   for word in ['понимаю', 'интересно', 'отлично', 'спасибо', 'ясно']))
        print(f"   💬 ЕСТЕСТВЕННОСТЬ: {natural_phrases}/{total_steps} - {'ОТЛИЧНО' if natural_phrases >= 3 else 'ХОРОШО'}")

        print(f"\n❌ СЛАБЫЕ СТОРОНЫ:")

        # Анализ проблем
        issues = []

        # 1. Повторяемость инструментов
        tool_names = []
        for msg in self.conversation_history:
            for tool in msg['tools']:
                if isinstance(tool, dict):
                    tool_names.append(tool.get('function', {}).get('name', 'unknown'))
                else:
                    tool_names.append(str(tool))

        unique_tools = set(tool_names)
        if len(unique_tools) < total_tools * 0.7:  # Менее 70% уникальных инструментов
            issues.append("🔄 ПОВТОРЯЕМОСТЬ: Агент часто использует одни и те же инструменты")

        # 2. Отсутствие персонализации
        personalization = sum(1 for msg in self.conversation_history
                             if 'партнер' in msg['agent'].lower() or 'профиль' in msg['agent'].lower())
        if personalization < 2:
            issues.append("👤 ПЕРСОНАЛИЗАЦИЯ: Мало внимания к профилю и интересам пользователя")

        # 3. Длина ответов
        long_responses = sum(1 for msg in self.conversation_history if len(msg['agent']) > 500)
        if long_responses > total_steps * 0.6:  # Более 60% длинных ответов
            issues.append("📏 ДЛИНА ОТВЕТОВ: Ответы слишком длинные, можно короче")

        # 4. Конкретность
        concrete_suggestions = sum(1 for msg in self.conversation_history
                                  if any(word in msg['agent'].lower()
                                        for word in ['конкретно', 'сделай', 'попробуй', 'начни']))
        if concrete_suggestions < 2:
            issues.append("🎯 КОНКРЕТНОСТЬ: Мало конкретных actionable предложений")

        if not issues:
            issues.append("✨ ПРОБЛЕМ НЕ НАЙДЕНО: Агент работает хорошо!")

        for issue in issues:
            print(f"   {issue}")

        print(f"\n💡 РЕКОМЕНДАЦИИ ДЛЯ УЛУЧШЕНИЯ:")
        print("   1. Добавить больше вариативности в выборе инструментов")
        print("   2. Увеличить персонализацию ответов на основе профиля")
        print("   3. Сократить длину ответов, сделать их более concise")
        print("   4. Добавить больше конкретных actionable шагов")
        print("   5. Улучшить естественность диалога")

        # Сохраняем результаты
        with open('quick_test_results.json', 'w', encoding='utf-8') as f:
            json.dump(self.conversation_history, f, ensure_ascii=False, indent=2)

        print(f"\n💾 РЕЗУЛЬТАТЫ СОХРАНЕНЫ В: quick_test_results.json")

async def main():
    tester = QuickDialogTester()
    await tester.run_quick_test()

if __name__ == "__main__":
    asyncio.run(main())