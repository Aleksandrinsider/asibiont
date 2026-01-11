"""
Комплексный тест AI-агента перед релизом
Проверяет все возможные запросы, ответы, напоминания и команды
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base, User, Task, UserProfile
from ai_integration import chat_with_ai
import re

# Исправление кодировки для Windows
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

# Настройка базы данных для тестов
os.environ["DATABASE_URL"] = "sqlite:///test_comprehensive.db"
engine = create_engine("sqlite:///test_comprehensive.db")
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

print("Database tables created successfully")


class ComprehensiveTest:
    def __init__(self):
        self.session = Session()
        self.test_user_id = None
        self.results = {
            "passed": 0,
            "failed": 0,
            "details": []
        }
        
    def setup_test_user(self):
        """Создание тестового пользователя"""
        # Удаляем старого пользователя если есть
        old_user = self.session.query(User).filter(User.telegram_id == 999).first()
        if old_user:
            self.session.query(Task).filter(Task.user_id == old_user.id).delete()
            self.session.query(UserProfile).filter(UserProfile.user_id == old_user.id).delete()
            self.session.delete(old_user)
            self.session.commit()
        
        user = User(
            telegram_id=999,
            username="comprehensive_test",
            timezone="UTC"
        )
        self.session.add(user)
        self.session.commit()
        self.test_user_id = 999
        
        profile = UserProfile(
            user_id=user.id,
            city="Москва",
            company="ASI Biont",
            interests="AI, программирование",
            skills="Python, TensorFlow",
            goals="Изучить машинное обучение"
        )
        self.session.add(profile)
        self.session.commit()
        
        print(f"[SETUP] Тестовый пользователь: {user.username} (ID: {self.test_user_id})")
        
    def cleanup(self):
        """Очистка после тестов"""
        self.session.query(Task).delete()
        self.session.query(UserProfile).delete()
        self.session.query(User).delete()
        self.session.commit()
        self.session.close()
        
    async def test_scenario(self, category, name, message, expected_function=None, 
                           expected_keywords=None, min_words=30, max_words=200):
        """
        Тестирование одного сценария
        
        Args:
            category: Категория теста
            name: Название теста
            message: Сообщение пользователя
            expected_function: Ожидаемая функция (add_task, list_tasks, etc)
            expected_keywords: Ключевые слова в ответе
            min_words: Минимум слов в ответе
            max_words: Максимум слов в ответе
        """
        print(f"\n[{category}] {name}")
        print(f"   Запрос: {message}")
        
        try:
            # Задержка между запросами к API (увеличена для стабильности)
            await asyncio.sleep(2)
            
            # Retry логика для стабильности
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    response = await asyncio.wait_for(
                        chat_with_ai(
                            message=message,
                            user_id=self.test_user_id,
                            context=[]
                        ),
                        timeout=30  # 30 секунд таймаут
                    )
                    break
                except asyncio.TimeoutError:
                    if attempt < max_retries - 1:
                        print(f"      Попытка {attempt + 1} не удалась, повтор...")
                        await asyncio.sleep(3)
                    else:
                        raise
                except Exception as e:
                    if attempt < max_retries - 1:
                        print(f"      Ошибка: {str(e)[:50]}, повтор...")
                        await asyncio.sleep(3)
                    else:
                        raise
            
            # Подсчет слов
            words = len(response.split())
            
            # Проверка длины
            length_ok = min_words <= words <= max_words
            
            # Проверка ключевых слов
            keywords_ok = True
            if expected_keywords:
                keywords_ok = all(keyword.lower() in response.lower() 
                                for keyword in expected_keywords)
            
            # Проверка вызова функций (через анализ логов)
            function_ok = True
            if expected_function:
                # Простая проверка - если функция была вызвана, 
                # в ответе должны быть признаки ее работы
                if expected_function == "add_task":
                    function_ok = any(word in response.lower() 
                                    for word in ["добавил", "задача", "запланирована"])
                elif expected_function == "list_tasks":
                    function_ok = any(word in response.lower() 
                                    for word in ["задач", "список", "активных"])
                elif expected_function == "complete_task":
                    function_ok = any(word in response.lower() 
                                    for word in ["выполнен", "готов", "завершен", "молодец"])
                elif expected_function == "update_profile":
                    function_ok = any(word in response.lower() 
                                    for word in ["профиль", "добавил", "обновил"])
                elif expected_function == "find_partners":
                    function_ok = any(word in response.lower() 
                                    for word in ["людей", "партнер", "единомышленник"])
                elif expected_function == "delegate_task":
                    function_ok = any(word in response.lower() 
                                    for word in ["делегир", "поручи", "@"])
            
            # Общая оценка
            passed = length_ok and keywords_ok and function_ok
            
            if passed:
                self.results["passed"] += 1
                status = "✅ PASSED"
            else:
                self.results["failed"] += 1
                status = "❌ FAILED"
            
            details = {
                "category": category,
                "name": name,
                "message": message,
                "response": response[:200] + "..." if len(response) > 200 else response,
                "words": words,
                "length_ok": length_ok,
                "keywords_ok": keywords_ok,
                "function_ok": function_ok,
                "passed": passed
            }
            self.results["details"].append(details)
            
            print(f"   {status}")
            print(f"   Длина: {words} слов (ок: {length_ok})")
            if not keywords_ok:
                print(f"   ⚠️  Отсутствуют ключевые слова: {expected_keywords}")
            if not function_ok:
                print(f"   ⚠️  Функция {expected_function} не вызвана")
            
        except Exception as e:
            self.results["failed"] += 1
            print(f"   ❌ ERROR: {str(e)[:100]}")
            self.results["details"].append({
                "category": category,
                "name": name,
                "error": str(e),
                "passed": False
            })
            # Не прерываем выполнение при ошибке, продолжаем тестирование
    
    async def run_all_tests(self):
        """Запуск всех тестов"""
        print("\n" + "="*80)
        print("  КОМПЛЕКСНОЕ ТЕСТИРОВАНИЕ AI-АГЕНТА ПЕРЕД РЕЛИЗОМ")
        print("  Проверка всех возможностей и сценариев использования")
        print("  88 тестовых сценариев по 8 категориям")
        print("="*80)
        
        # ===== КАТЕГОРИЯ 1: ПРИВЕТСТВИЯ И БАЗОВЫЙ ДИАЛОГ =====
        print("\n" + "="*80)
        print("КАТЕГОРИЯ 1: ПРИВЕТСТВИЯ И БАЗОВЫЙ ДИАЛОГ (10 тестов)")
        print("="*80)
        
        await self.test_scenario(
            "Диалог", "Приветствие простое",
            "Привет",
            expected_keywords=["привет", "рад"],
            min_words=50
        )
        
        await self.test_scenario(
            "Диалог", "Приветствие с вопросом",
            "Привет! Как дела?",
            expected_keywords=["отлично", "хорошо"],
            min_words=50
        )
        
        await self.test_scenario(
            "Диалог", "Запрос помощи",
            "Помоги мне",
            expected_keywords=["помогу", "могу"],
            min_words=50
        )
        
        await self.test_scenario(
            "Диалог", "Что ты умеешь",
            "Что ты умеешь делать?",
            expected_keywords=["задач", "профиль", "люд"],
            min_words=80
        )
        
        await self.test_scenario(
            "Диалог", "Благодарность",
            "Спасибо за помощь!",
            expected_keywords=["пожалуйста", "рад"],
            min_words=40
        )
        
        await self.test_scenario(
            "Диалог", "Прощание",
            "Пока!",
            expected_keywords=["пока", "увидимся"],
            min_words=30
        )
        
        await self.test_scenario(
            "Диалог", "Хорошее настроение",
            "Сегодня отличный день!",
            expected_keywords=["отлично", "рад"],
            min_words=50
        )
        
        await self.test_scenario(
            "Диалог", "Запрос совета общий",
            "Что мне делать дальше?",
            expected_keywords=["рекоменд", "предлаг"],
            min_words=80
        )
        
        await self.test_scenario(
            "Диалог", "Вопрос о боте",
            "Кто ты?",
            expected_keywords=["помощник", "агент", "друг"],
            min_words=50
        )
        
        await self.test_scenario(
            "Диалог", "Запрос информации",
            "Расскажи о своих возможностях",
            expected_keywords=["задач", "профиль"],
            min_words=100
        )
        
        # ===== КАТЕГОРИЯ 2: УПРАВЛЕНИЕ ЗАДАЧАМИ =====
        print("\n" + "="*80)
        print("КАТЕГОРИЯ 2: УПРАВЛЕНИЕ ЗАДАЧАМИ (20 тестов)")
        print("="*80)
        
        await self.test_scenario(
            "Задачи", "Показ всех задач",
            "Покажи мои задачи",
            expected_function="list_tasks",
            expected_keywords=["задач"],
            min_words=50
        )
        
        await self.test_scenario(
            "Задачи", "Что делать",
            "Что мне нужно сделать?",
            expected_function="list_tasks",
            expected_keywords=["задач"],
            min_words=50
        )
        
        await self.test_scenario(
            "Задачи", "Список дел",
            "Список моих дел",
            expected_function="list_tasks",
            expected_keywords=["задач"],
            min_words=50
        )
        
        await self.test_scenario(
            "Задачи", "Добавление простой задачи",
            "Добавь задачу купить продукты",
            expected_function="add_task",
            expected_keywords=["добавил", "задач"],
            min_words=50
        )
        
        await self.test_scenario(
            "Задачи", "Добавление со временем",
            "Напомни позвонить маме завтра в 18:00",
            expected_function="add_task",
            expected_keywords=["добавил", "напомн"],
            min_words=50
        )
        
        await self.test_scenario(
            "Задачи", "Срочная задача",
            "Срочно нужно сделать презентацию к вечеру",
            expected_function="add_task",
            expected_keywords=["срочн", "добавил"],
            min_words=50
        )
        
        await self.test_scenario(
            "Задачи", "Задача на сегодня",
            "Нужно сегодня отправить отчет",
            expected_function="add_task",
            expected_keywords=["сегодня", "отчет"],
            min_words=50
        )
        
        await self.test_scenario(
            "Задачи", "Задача на завтра",
            "Завтра встреча с клиентом в 14:00",
            expected_function="add_task",
            expected_keywords=["завтра", "встреча"],
            min_words=50
        )
        
        await self.test_scenario(
            "Задачи", "Множественные задачи",
            "Добавь задачи: купить молоко, позвонить другу, сделать зарядку",
            expected_function="add_task",
            expected_keywords=["задач"],
            min_words=50
        )
        
        await self.test_scenario(
            "Задачи", "Завершение задачи простое",
            "Выполнил задачу купить продукты",
            expected_function="complete_task",
            expected_keywords=["молод", "отлично", "готов"],
            min_words=50
        )
        
        await self.test_scenario(
            "Задачи", "Завершение готово",
            "Готово! Позвонил маме",
            expected_function="complete_task",
            expected_keywords=["молод", "отлично"],
            min_words=50
        )
        
        await self.test_scenario(
            "Задачи", "Завершение сделал",
            "Я сделал презентацию",
            expected_function="complete_task",
            expected_keywords=["молод", "отлично"],
            min_words=50
        )
        
        await self.test_scenario(
            "Задачи", "Приоритеты",
            "Какая самая важная задача?",
            expected_function="list_tasks",
            expected_keywords=["важн", "срочн"],
            min_words=50
        )
        
        await self.test_scenario(
            "Задачи", "Сколько задач",
            "Сколько у меня задач?",
            expected_function="list_tasks",
            expected_keywords=["задач"],
            min_words=30
        )
        
        await self.test_scenario(
            "Задачи", "Просроченные задачи",
            "Есть ли просроченные задачи?",
            expected_function="list_tasks",
            expected_keywords=["задач", "просроч"],
            min_words=50
        )
        
        await self.test_scenario(
            "Задачи", "Задачи на неделю",
            "Покажи задачи на эту неделю",
            expected_function="list_tasks",
            expected_keywords=["задач", "недел"],
            min_words=50
        )
        
        await self.test_scenario(
            "Задачи", "Редактирование задачи",
            "Измени время встречи на 15:00",
            expected_keywords=["измен", "время"],
            min_words=40
        )
        
        await self.test_scenario(
            "Задачи", "Отмена задачи",
            "Отмени задачу купить молоко",
            expected_keywords=["отмен", "удал"],
            min_words=40
        )
        
        await self.test_scenario(
            "Задачи", "Перенос задачи",
            "Перенеси встречу на завтра",
            expected_keywords=["перенес", "завтра"],
            min_words=40
        )
        
        await self.test_scenario(
            "Задачи", "Повтор задачи",
            "Повторяй эту задачу каждый день",
            expected_keywords=["повтор", "кажд"],
            min_words=40
        )
        
        # ===== КАТЕГОРИЯ 3: ДЕЛЕГИРОВАНИЕ =====
        print("\n" + "="*80)
        print("КАТЕГОРИЯ 3: ДЕЛЕГИРОВАНИЕ ЗАДАЧ (8 тестов)")
        print("="*80)
        
        await self.test_scenario(
            "Делегирование", "Простое делегирование",
            "@ivan сделай код-ревью",
            expected_function="delegate_task",
            expected_keywords=["делегир", "@ivan", "поруч"],
            min_words=50
        )
        
        await self.test_scenario(
            "Делегирование", "Делегирование со временем",
            "@maria проверь отчет к завтра 12:00",
            expected_function="delegate_task",
            expected_keywords=["делегир", "@maria"],
            min_words=50
        )
        
        await self.test_scenario(
            "Делегирование", "Срочное делегирование",
            "@alex срочно нужна помощь с багом",
            expected_function="delegate_task",
            expected_keywords=["срочн", "@alex"],
            min_words=50
        )
        
        await self.test_scenario(
            "Делегирование", "Множественное делегирование",
            "@ivan @maria работайте вместе над проектом",
            expected_keywords=["@ivan", "@maria"],
            min_words=50
        )
        
        await self.test_scenario(
            "Делегирование", "Статус делегированной задачи",
            "Как дела с задачей для @ivan?",
            expected_keywords=["задач", "статус"],
            min_words=40
        )
        
        await self.test_scenario(
            "Делегирование", "Отзыв делегирования",
            "Отзови задачу у @maria",
            expected_keywords=["отзыв", "отмен"],
            min_words=40
        )
        
        await self.test_scenario(
            "Делегирование", "Показ делегированных",
            "Покажи все делегированные задачи",
            expected_function="list_tasks",
            expected_keywords=["делегир", "задач"],
            min_words=50
        )
        
        await self.test_scenario(
            "Делегирование", "Кому делегировано",
            "Кому я делегировал задачи?",
            expected_function="list_tasks",
            expected_keywords=["делегир"],
            min_words=40
        )
        
        # ===== КАТЕГОРИЯ 4: ПРОФИЛЬ =====
        print("\n" + "="*80)
        print("КАТЕГОРИЯ 4: УПРАВЛЕНИЕ ПРОФИЛЕМ (12 тестов)")
        print("="*80)
        
        await self.test_scenario(
            "Профиль", "Обновление города",
            "Я живу в Санкт-Петербурге",
            expected_function="update_profile",
            expected_keywords=["профиль", "город"],
            min_words=40
        )
        
        await self.test_scenario(
            "Профиль", "Обновление компании",
            "Работаю в Google",
            expected_function="update_profile",
            expected_keywords=["профиль", "компан"],
            min_words=40
        )
        
        await self.test_scenario(
            "Профиль", "Добавление навыка",
            "Умею работать с React",
            expected_function="update_profile",
            expected_keywords=["навык", "умею"],
            min_words=50
        )
        
        await self.test_scenario(
            "Профиль", "Множественные навыки",
            "Мои навыки: Python, JavaScript, SQL",
            expected_function="update_profile",
            expected_keywords=["навык"],
            min_words=50
        )
        
        await self.test_scenario(
            "Профиль", "Добавление интереса",
            "Интересуюсь блокчейном",
            expected_function="update_profile",
            expected_keywords=["интерес", "блокчейн"],
            min_words=50
        )
        
        await self.test_scenario(
            "Профиль", "Множественные интересы",
            "Мои интересы: спорт, музыка, путешествия",
            expected_function="update_profile",
            expected_keywords=["интерес"],
            min_words=50
        )
        
        await self.test_scenario(
            "Профиль", "Добавление цели",
            "Хочу выучить испанский язык",
            expected_function="update_profile",
            expected_keywords=["цель", "выуч"],
            min_words=50
        )
        
        await self.test_scenario(
            "Профиль", "Цель карьеры",
            "Моя цель - стать тимлидом",
            expected_function="update_profile",
            expected_keywords=["цель", "тимлид"],
            min_words=50
        )
        
        await self.test_scenario(
            "Профиль", "Показ профиля",
            "Покажи мой профиль",
            expected_keywords=["профиль"],
            min_words=50
        )
        
        await self.test_scenario(
            "Профиль", "Что обо мне известно",
            "Что ты знаешь обо мне?",
            expected_keywords=["профиль", "знаю"],
            min_words=60
        )
        
        await self.test_scenario(
            "Профиль", "Изменение информации",
            "Измени мой город на Москва",
            expected_function="update_profile",
            expected_keywords=["город", "москва"],
            min_words=40
        )
        
        await self.test_scenario(
            "Профиль", "Полная информация",
            "Заполни весь мой профиль",
            expected_keywords=["профиль", "заполн"],
            min_words=60
        )
        
        # ===== КАТЕГОРИЯ 5: ПОИСК ЛЮДЕЙ =====
        print("\n" + "="*80)
        print("КАТЕГОРИЯ 5: ПОИСК ЕДИНОМЫШЛЕННИКОВ (8 тестов)")
        print("="*80)
        
        await self.test_scenario(
            "Люди", "Поиск партнеров общий",
            "Найди мне партнеров для проекта",
            expected_function="find_partners",
            expected_keywords=["партнер", "люд"],
            min_words=50
        )
        
        await self.test_scenario(
            "Люди", "Поиск по навыкам",
            "Найди программистов на Python",
            expected_function="find_partners",
            expected_keywords=["python", "програм"],
            min_words=50
        )
        
        await self.test_scenario(
            "Люди", "Поиск по интересам",
            "Найди людей, интересующихся AI",
            expected_function="find_partners",
            expected_keywords=["ai", "люд"],
            min_words=50
        )
        
        await self.test_scenario(
            "Люди", "Поиск в городе",
            "Кто из программистов в Москве?",
            expected_function="find_partners",
            expected_keywords=["москва", "програм"],
            min_words=50
        )
        
        await self.test_scenario(
            "Люди", "Поиск единомышленников",
            "Найди единомышленников",
            expected_function="find_partners",
            expected_keywords=["единомышленник", "люд"],
            min_words=50
        )
        
        await self.test_scenario(
            "Люди", "Поиск для спорта",
            "Ищу компанию для бега",
            expected_function="find_partners",
            expected_keywords=["бег", "компан"],
            min_words=50
        )
        
        await self.test_scenario(
            "Люди", "Поиск коллег",
            "Найди коллег из IT",
            expected_function="find_partners",
            expected_keywords=["коллег", "it"],
            min_words=50
        )
        
        await self.test_scenario(
            "Люди", "С кем познакомиться",
            "С кем мне стоит познакомиться?",
            expected_function="find_partners",
            expected_keywords=["познаком", "люд"],
            min_words=50
        )
        
        # ===== КАТЕГОРИЯ 6: НАПОМИНАНИЯ И ВРЕМЯ =====
        print("\n" + "="*80)
        print("КАТЕГОРИЯ 6: НАПОМИНАНИЯ И РАБОТА СО ВРЕМЕНЕМ (10 тестов)")
        print("="*80)
        
        await self.test_scenario(
            "Время", "Напоминание через час",
            "Напомни через час про встречу",
            expected_function="add_task",
            expected_keywords=["напомн", "час"],
            min_words=40
        )
        
        await self.test_scenario(
            "Время", "Напоминание завтра",
            "Напомни завтра в 10:00 про звонок",
            expected_function="add_task",
            expected_keywords=["напомн", "завтра"],
            min_words=40
        )
        
        await self.test_scenario(
            "Время", "Напоминание через неделю",
            "Напомни через неделю про отпуск",
            expected_function="add_task",
            expected_keywords=["напомн", "недел"],
            min_words=40
        )
        
        await self.test_scenario(
            "Время", "Ежедневное напоминание",
            "Напоминай каждый день в 9:00 про зарядку",
            expected_function="add_task",
            expected_keywords=["напомн", "кажд"],
            min_words=40
        )
        
        await self.test_scenario(
            "Время", "Время сейчас",
            "Который час?",
            expected_keywords=["время", "час"],
            min_words=30
        )
        
        await self.test_scenario(
            "Время", "Что сегодня",
            "Какая сегодня дата?",
            expected_keywords=["сегодня"],
            min_words=30
        )
        
        await self.test_scenario(
            "Время", "Сколько времени осталось",
            "Сколько времени до завтра?",
            expected_keywords=["время", "завтра"],
            min_words=30
        )
        
        await self.test_scenario(
            "Время", "Изменение времени",
            "Измени время напоминания на 15:00",
            expected_keywords=["измен", "время"],
            min_words=40
        )
        
        await self.test_scenario(
            "Время", "Отложить напоминание",
            "Отложи напоминание на час",
            expected_keywords=["отлож", "час"],
            min_words=30
        )
        
        await self.test_scenario(
            "Время", "Таймзона",
            "Моя временная зона UTC+3",
            expected_keywords=["врем", "зон"],
            min_words=30
        )
        
        # ===== КАТЕГОРИЯ 7: EDGE CASES =====
        print("\n" + "="*80)
        print("КАТЕГОРИЯ 7: ГРАНИЧНЫЕ СЛУЧАИ (12 тестов)")
        print("="*80)
        
        await self.test_scenario(
            "Edge", "Пустое сообщение",
            "",
            expected_keywords=["привет", "помочь"],
            min_words=40
        )
        
        await self.test_scenario(
            "Edge", "Только пробелы",
            "   ",
            expected_keywords=["помочь"],
            min_words=40
        )
        
        await self.test_scenario(
            "Edge", "Бессмыслица",
            "асдфйцукен",
            expected_keywords=["понял", "помочь"],
            min_words=50
        )
        
        await self.test_scenario(
            "Edge", "Смайлики",
            "😊😊😊",
            expected_keywords=["настроен", "помочь"],
            min_words=40
        )
        
        await self.test_scenario(
            "Edge", "Цифры",
            "12345",
            expected_keywords=["понял", "помочь"],
            min_words=40
        )
        
        await self.test_scenario(
            "Edge", "Непонятный запрос",
            "сделай это",
            expected_keywords=["вариант", "понял"],
            min_words=50
        )
        
        await self.test_scenario(
            "Edge", "Неясное действие",
            "хочу то-то",
            expected_keywords=["уточн", "вариант"],
            min_words=40
        )
        
        await self.test_scenario(
            "Edge", "Очень длинное сообщение",
            "у меня " + "очень " * 50 + "длинное сообщение с множеством повторений",
            expected_keywords=["понял"],
            min_words=40
        )
        
        await self.test_scenario(
            "Edge", "Специальные символы",
            "!@#$%^&*()",
            expected_keywords=["понял", "помочь"],
            min_words=40
        )
        
        await self.test_scenario(
            "Edge", "Смешанный язык",
            "hello привет how are you как дела",
            expected_keywords=["привет"],
            min_words=40
        )
        
        await self.test_scenario(
            "Edge", "Повтор запроса",
            "покажи задачи покажи задачи покажи задачи",
            expected_function="list_tasks",
            expected_keywords=["задач"],
            min_words=40
        )
        
        await self.test_scenario(
            "Edge", "Противоречивый запрос",
            "добавь задачу нет удали задачу",
            expected_keywords=["уточн", "понял"],
            min_words=40
        )
        
        # ===== КАТЕГОРИЯ 8: АНАЛИТИКА И СТАТИСТИКА =====
        print("\n" + "="*80)
        print("КАТЕГОРИЯ 8: АНАЛИТИКА И СТАТИСТИКА (8 тестов)")
        print("="*80)
        
        await self.test_scenario(
            "Аналитика", "Статистика задач",
            "Покажи статистику моих задач",
            expected_function="list_tasks",
            expected_keywords=["задач", "статист"],
            min_words=60
        )
        
        await self.test_scenario(
            "Аналитика", "Сколько выполнено",
            "Сколько задач я выполнил?",
            expected_keywords=["выполн", "задач"],
            min_words=40
        )
        
        await self.test_scenario(
            "Аналитика", "Прогресс за неделю",
            "Какой у меня прогресс за неделю?",
            expected_keywords=["прогресс", "недел"],
            min_words=50
        )
        
        await self.test_scenario(
            "Аналитика", "Анализ продуктивности",
            "Проанализируй мою продуктивность",
            expected_keywords=["продуктивн", "анализ"],
            min_words=60
        )
        
        await self.test_scenario(
            "Аналитика", "Самая частая задача",
            "Какие задачи я делаю чаще всего?",
            expected_keywords=["задач", "част"],
            min_words=50
        )
        
        await self.test_scenario(
            "Аналитика", "Время на задачи",
            "Сколько времени уходит на задачи?",
            expected_keywords=["время", "задач"],
            min_words=40
        )
        
        await self.test_scenario(
            "Аналитика", "Рекомендации по оптимизации",
            "Как мне оптимизировать работу?",
            expected_keywords=["оптимиз", "рекоменд"],
            min_words=80
        )
        
        await self.test_scenario(
            "Аналитика", "Что нужно улучшить",
            "Что мне нужно улучшить?",
            expected_keywords=["улучш", "рекоменд"],
            min_words=60
        )
    
    def print_summary(self):
        """Вывод итогового отчета"""
        total = self.results["passed"] + self.results["failed"]
        percentage = (self.results["passed"] / total * 100) if total > 0 else 0
        
        print("\n" + "="*80)
        print("  ИТОГОВЫЙ ОТЧЕТ")
        print("="*80)
        
        print(f"\n📊 Общая статистика:")
        print(f"   Всего тестов: {total}")
        print(f"   ✅ Пройдено: {self.results['passed']}")
        print(f"   ❌ Провалено: {self.results['failed']}")
        print(f"   📈 Процент успеха: {percentage:.1f}%")
        
        # Статистика по категориям
        categories = {}
        for detail in self.results["details"]:
            cat = detail.get("category", "Unknown")
            if cat not in categories:
                categories[cat] = {"passed": 0, "failed": 0}
            if detail.get("passed"):
                categories[cat]["passed"] += 1
            else:
                categories[cat]["failed"] += 1
        
        print(f"\n📋 По категориям:")
        for cat, stats in categories.items():
            total_cat = stats["passed"] + stats["failed"]
            percent_cat = (stats["passed"] / total_cat * 100) if total_cat > 0 else 0
            print(f"   {cat}: {stats['passed']}/{total_cat} ({percent_cat:.0f}%)")
        
        # Провальные тесты
        if self.results["failed"] > 0:
            print(f"\n❌ Провальные тесты:")
            for detail in self.results["details"]:
                if not detail.get("passed"):
                    print(f"   - [{detail.get('category')}] {detail.get('name')}")
                    if detail.get("error"):
                        print(f"     Ошибка: {detail['error']}")
        
        # Рекомендации
        print(f"\n💡 Рекомендации:")
        if percentage >= 90:
            print("   ✅ АГЕНТ ГОТОВ К РЕЛИЗУ! Отличная работа!")
        elif percentage >= 75:
            print("   ⚠️  Агент почти готов, но требуется доработка провальных тестов")
        elif percentage >= 60:
            print("   ⚠️  Требуется серьезная доработка перед релизом")
        else:
            print("   ❌ Агент НЕ готов к релизу. Необходима полная переработка")
        
        print("\n" + "="*80)


async def main():
    """Главная функция запуска тестов"""
    tester = ComprehensiveTest()
    
    try:
        tester.setup_test_user()
        await tester.run_all_tests()
        tester.print_summary()
    finally:
        tester.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
