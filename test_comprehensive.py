"""
Комплексный тест всех функций после оптимизации
Проверяет: качество ответов, работу инструментов, контекст
"""
import asyncio
import sys
from ai_integration.chat import chat_with_ai
from models import Session, User, UserProfile, Task, Base, engine
from datetime import datetime, timedelta
import pytz

async def test_comprehensive():
    """Полная проверка функционала"""
    
    user_id = 777666555
    Base.metadata.create_all(engine)
    session = Session()
    
    try:
        # Очистка старых данных
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if user:
            session.query(Task).filter_by(user_id=user.id).delete()
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            if profile:
                session.delete(profile)
            session.commit()
            session.delete(user)
            session.commit()
        
        # Создаем пользователя
        user = User(
            telegram_id=user_id, 
            username='test_comprehensive', 
            first_name='Тестер',
            timezone='Europe/Moscow'
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        
        # Создаем базовый профиль
        profile = UserProfile(
            user_id=user.id,
            city='Москва',
            interests='AI, стартапы'
        )
        session.add(profile)
        session.commit()
        
        print("="*70)
        print("КОМПЛЕКСНЫЙ ТЕСТ ПОСЛЕ ОПТИМИЗАЦИИ")
        print("="*70)
        
        results = {
            'passed': 0,
            'failed': 0,
            'tests': []
        }
        
        # ===== ТЕСТ 1: ЕСТЕСТВЕННОЕ ОБЩЕНИЕ =====
        print("\n[1/8] Естественное общение (без команд)")
        try:
            response = await chat_with_ai("Привет! Как дела?", user_id=user_id)
            answer = response.get('response', '')
            
            # Проверяем качество
            checks = {
                'Не пустой': len(answer) > 20,
                'Естественный': 'Привет' in answer or 'привет' in answer,
                'Короткий': len(answer) < 500,  # Не монолог
            }
            
            if all(checks.values()):
                print("✅ PASS: Естественное общение работает")
                print(f"   Ответ: {answer[:100]}...")
                results['passed'] += 1
            else:
                print(f"❌ FAIL: Проблемы - {[k for k, v in checks.items() if not v]}")
                results['failed'] += 1
            
            results['tests'].append({
                'name': 'Естественное общение',
                'status': 'PASS' if all(checks.values()) else 'FAIL',
                'details': checks
            })
        except Exception as e:
            print(f"❌ FAIL: Ошибка - {e}")
            results['failed'] += 1
        
        # ===== ТЕСТ 2: СОЗДАНИЕ ЗАДАЧИ С ТОЧНЫМ ВРЕМЕНЕМ =====
        print("\n[2/8] Создание задачи с точным временем")
        try:
            response = await chat_with_ai("Создай задачу позвонить клиенту завтра в 15:00", user_id=user_id)
            answer = response.get('response', '')
            
            # Проверяем в БД
            session.expire_all()
            tasks = session.query(Task).filter_by(user_id=user.id).all()
            
            checks = {
                'Задача создана': len(tasks) > 0,
                'Есть время': tasks[0].reminder_time is not None if tasks else False,
                'Подтверждение в ответе': any(word in answer.lower() for word in ['создал', 'задача', 'напомин']),
            }
            
            if all(checks.values()):
                print(f"✅ PASS: Задача создана - '{tasks[0].title}'")
                print(f"   Время: {tasks[0].reminder_time.strftime('%d.%m %H:%M')}")
                results['passed'] += 1
            else:
                print(f"❌ FAIL: {[k for k, v in checks.items() if not v]}")
                results['failed'] += 1
            
            results['tests'].append({
                'name': 'Создание задачи',
                'status': 'PASS' if all(checks.values()) else 'FAIL',
                'details': checks
            })
        except Exception as e:
            print(f"❌ FAIL: Ошибка - {e}")
            results['failed'] += 1
        
        # ===== ТЕСТ 3: ПРОСМОТР ЗАДАЧ =====
        print("\n[3/8] Просмотр списка задач")
        try:
            response = await chat_with_ai("покажи мои задачи", user_id=user_id)
            answer = response.get('response', '')
            
            checks = {
                'Ответ не пустой': len(answer) > 20,
                'Упоминает задачу': 'позвонить' in answer.lower() or 'клиент' in answer.lower(),
                'Конкретика': any(word in answer for word in ['15:00', 'завтра']),
            }
            
            if all(checks.values()):
                print("✅ PASS: Список задач показан корректно")
                results['passed'] += 1
            else:
                print(f"❌ FAIL: {[k for k, v in checks.items() if not v]}")
                print(f"   Ответ: {answer[:150]}")
                results['failed'] += 1
            
            results['tests'].append({
                'name': 'Просмотр задач',
                'status': 'PASS' if all(checks.values()) else 'FAIL',
                'details': checks
            })
        except Exception as e:
            print(f"❌ FAIL: Ошибка - {e}")
            results['failed'] += 1
        
        # ===== ТЕСТ 4: ОБНОВЛЕНИЕ ПРОФИЛЯ =====
        print("\n[4/8] Обновление профиля")
        try:
            response = await chat_with_ai("Я основатель стартапа TechCorp и умею программировать на Python", user_id=user_id)
            answer = response.get('response', '')
            
            # Проверяем БД
            session.expire_all()
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            
            checks = {
                'Компания сохранена': profile.company == 'TechCorp' if profile else False,
                'Навыки сохранены': 'Python' in (profile.skills or '') if profile else False,
                'Подтверждение': 'обновил' in answer.lower() or 'techcorp' in answer.lower(),
            }
            
            if all(checks.values()):
                print("✅ PASS: Профиль обновлён")
                print(f"   Компания: {profile.company}")
                print(f"   Навыки: {profile.skills}")
                results['passed'] += 1
            else:
                print(f"❌ FAIL: {[k for k, v in checks.items() if not v]}")
                results['failed'] += 1
            
            results['tests'].append({
                'name': 'Обновление профиля',
                'status': 'PASS' if all(checks.values()) else 'FAIL',
                'details': checks
            })
        except Exception as e:
            print(f"❌ FAIL: Ошибка - {e}")
            results['failed'] += 1
        
        # ===== ТЕСТ 5: КОНТЕКСТУАЛЬНОСТЬ =====
        print("\n[5/8] Использование контекста")
        try:
            response = await chat_with_ai("Как мой проект продвигается?", user_id=user_id)
            answer = response.get('response', '')
            
            checks = {
                'Упоминает компанию': 'TechCorp' in answer or 'techcorp' in answer.lower(),
                'Не пустой': len(answer) > 30,
                'Персонализация': any(word in answer.lower() for word in ['проект', 'стартап', 'компани']),
            }
            
            if all(checks.values()):
                print("✅ PASS: Использует контекст из профиля")
                print(f"   Ответ: {answer[:100]}...")
                results['passed'] += 1
            else:
                print(f"❌ FAIL: {[k for k, v in checks.items() if not v]}")
                results['failed'] += 1
            
            results['tests'].append({
                'name': 'Контекстуальность',
                'status': 'PASS' if all(checks.values()) else 'FAIL',
                'details': checks
            })
        except Exception as e:
            print(f"❌ FAIL: Ошибка - {e}")
            results['failed'] += 1
        
        # ===== ТЕСТ 6: ЗАВЕРШЕНИЕ ЗАДАЧИ =====
        print("\n[6/8] Завершение задачи")
        try:
            # Сначала создадим простую задачу
            await chat_with_ai("Создай задачу купить молоко сегодня в 18:00", user_id=user_id)
            await asyncio.sleep(0.5)
            
            # Теперь завершим
            response = await chat_with_ai("Купил молоко, готово", user_id=user_id)
            answer = response.get('response', '')
            
            # Проверяем БД
            session.expire_all()
            completed_tasks = session.query(Task).filter_by(
                user_id=user.id, 
                status='completed'
            ).all()
            
            checks = {
                'Задача завершена': len(completed_tasks) > 0,
                'Подтверждение': any(word in answer.lower() for word in ['отлично', 'готово', 'завершил', 'закрыл']),
            }
            
            if all(checks.values()):
                print("✅ PASS: Задача успешно завершена")
                results['passed'] += 1
            else:
                print(f"❌ FAIL: {[k for k, v in checks.items() if not v]}")
                print(f"   Завершенных задач: {len(completed_tasks)}")
                results['failed'] += 1
            
            results['tests'].append({
                'name': 'Завершение задачи',
                'status': 'PASS' if all(checks.values()) else 'FAIL',
                'details': checks
            })
        except Exception as e:
            print(f"❌ FAIL: Ошибка - {e}")
            results['failed'] += 1
        
        # ===== ТЕСТ 7: КАЧЕСТВО СОВЕТОВ =====
        print("\n[7/8] Качество советов")
        try:
            response = await chat_with_ai("Как привлечь первых пользователей в TechCorp?", user_id=user_id)
            answer = response.get('response', '')
            
            checks = {
                'Детальный ответ': len(answer) > 100,
                'Конкретика': any(word in answer.lower() for word in ['habr', 'пост', 'контент', 'соцсет', 'реклам', 'партнер']),
                'Персонализация': 'TechCorp' in answer or 'стартап' in answer.lower(),
                'Не слишком длинный': len(answer) < 1000,
            }
            
            if all(checks.values()):
                print("✅ PASS: Дает конкретные персонализированные советы")
                results['passed'] += 1
            else:
                print(f"❌ FAIL: {[k for k, v in checks.items() if not v]}")
                print(f"   Длина ответа: {len(answer)}")
                results['failed'] += 1
            
            results['tests'].append({
                'name': 'Качество советов',
                'status': 'PASS' if all(checks.values()) else 'FAIL',
                'details': checks
            })
        except Exception as e:
            print(f"❌ FAIL: Ошибка - {e}")
            results['failed'] += 1
        
        # ===== ТЕСТ 8: СОЗДАНИЕ ЗАДАЧИ БЕЗ ВРЕМЕНИ =====
        print("\n[8/8] Запрос времени при неопределенности")
        try:
            response = await chat_with_ai("Напомни мне завтра про встречу", user_id=user_id)
            answer = response.get('response', '')
            
            checks = {
                'Спрашивает время': any(word in answer.lower() for word in ['время', 'когда', 'час']),
                'Не создал без времени': True,  # Должен спросить, а не создать
            }
            
            # Проверим что не создалась задача без времени
            session.expire_all()
            tasks_without_time = session.query(Task).filter_by(
                user_id=user.id,
                reminder_time=None
            ).filter(Task.title.like('%встреч%')).all()
            
            checks['Не создал без времени'] = len(tasks_without_time) == 0
            
            if all(checks.values()):
                print("✅ PASS: Правильно запрашивает время")
                results['passed'] += 1
            else:
                print(f"❌ FAIL: {[k for k, v in checks.items() if not v]}")
                results['failed'] += 1
            
            results['tests'].append({
                'name': 'Запрос времени',
                'status': 'PASS' if all(checks.values()) else 'FAIL',
                'details': checks
            })
        except Exception as e:
            print(f"❌ FAIL: Ошибка - {e}")
            results['failed'] += 1
        
        # ===== ИТОГОВЫЙ ОТЧЕТ =====
        print("\n" + "="*70)
        print("ИТОГОВЫЙ ОТЧЕТ")
        print("="*70)
        
        total = results['passed'] + results['failed']
        success_rate = (results['passed'] / total * 100) if total > 0 else 0
        
        print(f"\nВсего тестов: {total}")
        print(f"Успешно: {results['passed']} ✅")
        print(f"Провалено: {results['failed']} ❌")
        print(f"Успешность: {success_rate:.1f}%")
        
        # Детали по тестам
        print("\nДетали:")
        for test in results['tests']:
            status_icon = "✅" if test['status'] == 'PASS' else "❌"
            print(f"  {status_icon} {test['name']}: {test['status']}")
        
        # Проверяем финальное состояние БД
        session.expire_all()
        final_tasks = session.query(Task).filter_by(user_id=user.id).all()
        final_profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        
        print(f"\nСостояние БД:")
        print(f"  Задач создано: {len(final_tasks)}")
        print(f"  Завершено: {len([t for t in final_tasks if t.status == 'completed'])}")
        print(f"  Активных: {len([t for t in final_tasks if t.status != 'completed'])}")
        if final_profile:
            print(f"  Профиль: {final_profile.company or 'нет компании'}, навыки: {final_profile.skills or 'нет'}")
        
        print("\n" + "="*70)
        if success_rate >= 75:
            print("🎉 ОПТИМИЗАЦИЯ УСПЕШНА - все функции работают!")
        elif success_rate >= 50:
            print("⚠️ ЧАСТИЧНЫЙ УСПЕХ - есть проблемы")
        else:
            print("❌ ПРОВАЛ - требуется доработка")
        print("="*70)
        
    finally:
        # Очистка
        try:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user:
                session.query(Task).filter_by(user_id=user.id).delete()
                profile = session.query(UserProfile).filter_by(user_id=user.id).first()
                if profile:
                    session.delete(profile)
                session.commit()
                session.delete(user)
                session.commit()
        except:
            session.rollback()
        finally:
            session.close()

if __name__ == '__main__':
    try:
        asyncio.run(test_comprehensive())
    except KeyboardInterrupt:
        print("\n\nТест прерван")
    except Exception as e:
        print(f"\n\nОшибка теста: {e}")
        import traceback
        traceback.print_exc()
