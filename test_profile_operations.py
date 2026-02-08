"""Тест операций с профилем: извлечение, изменение, удаление данных"""
import asyncio
import sys
import os
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ai_integration.chat import chat_with_ai
from models import Session, User, UserProfile, Base, engine

async def test_profile_operations():
    """Тестирует извлечение, изменение и удаление данных профиля"""
    Base.metadata.create_all(bind=engine)
    
    session = Session()
    try:
        # Очистка тестового пользователя
        test_user_id = 555555
        user = session.query(User).filter_by(telegram_id=test_user_id).first()
        if user:
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            if profile:
                session.delete(profile)
            session.delete(user)
            session.commit()
        
        # Создаём пользователя
        user = User(telegram_id=test_user_id, username="test_profile")
        session.add(user)
        session.flush()
        
        # Начальный профиль с минимальными данными
        profile = UserProfile(
            user_id=user.id,
            interests="Python, ИИ",
            city="Москва"
        )
        session.add(profile)
        session.commit()
        
        print("=" * 80)
        print("[ТЕСТ ОПЕРАЦИЙ С ПРОФИЛЕМ]")
        print("=" * 80)
        
        # ========== ТЕСТ 1: ИЗВЛЕЧЕНИЕ ДАННЫХ ==========
        print("\n" + "=" * 80)
        print("[ТЕСТ 1] ИЗВЛЕЧЕНИЕ: агент использует данные из профиля")
        print("=" * 80)
        print(f"\n📋 ТЕКУЩИЙ ПРОФИЛЬ:")
        print(f"   Интересы: {profile.interests}")
        print(f"   Город: {profile.city}")
        print(f"   Компания: {profile.company or '(не указана)'}")
        print(f"   Навыки: {profile.skills or '(не указаны)'}")
        
        print(f"\n[USER] Привет, с чем можешь помочь?\n")
        
        response1 = await chat_with_ai(
            message="Привет, с чем можешь помочь?",
            user_id=test_user_id
        )
        
        print(f"[BOT] {response1.get('response', '')}\n")
        
        # Проверка использования данных
        resp_text = response1.get('response', '')
        checks = []
        
        if "Python" in resp_text or "ИИ" in resp_text or "интерес" in resp_text.lower():
            checks.append("✅ Использовал интересы из профиля")
        else:
            checks.append("❌ НЕ использовал интересы")
        
        if "Москв" in resp_text:
            checks.append("✅ Упомянул город")
        else:
            checks.append("⚠️ Не упомянул город (может быть нормально)")
        
        print("📊 АНАЛИЗ ИЗВЛЕЧЕНИЯ:")
        for check in checks:
            print(f"   {check}")
        
        # ========== ТЕСТ 2: ИЗМЕНЕНИЕ ДАННЫХ ==========
        print("\n" + "=" * 80)
        print("[ТЕСТ 2] ИЗМЕНЕНИЕ: агент обновляет профиль на основе диалога")
        print("=" * 80)
        
        # 2.1: Добавление компании
        print(f"\n[USER] Я основатель стартапа TechAI\n")
        
        response2 = await chat_with_ai(
            message="Я основатель стартапа TechAI",
            user_id=test_user_id
        )
        
        print(f"[BOT] {response2.get('response', '')}\n")
        
        # Проверяем обновление профиля
        session.expire_all()  # Перечитываем данные из БД
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        
        print("📊 ПРОВЕРКА ОБНОВЛЕНИЯ КОМПАНИИ:")
        if profile.company and "TechAI" in profile.company:
            print(f"   ✅ Компания обновлена: '{profile.company}'")
        else:
            print(f"   ❌ Компания НЕ обновлена (текущее: '{profile.company}')")
        
        # 2.2: Добавление навыков
        print(f"\n[USER] Умею программировать на JavaScript и делать ML-модели\n")
        
        response3 = await chat_with_ai(
            message="Умею программировать на JavaScript и делать ML-модели",
            user_id=test_user_id
        )
        
        print(f"[BOT] {response3.get('response', '')}\n")
        
        session.expire_all()
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        
        print("📊 ПРОВЕРКА ОБНОВЛЕНИЯ НАВЫКОВ:")
        if profile.skills and ("JavaScript" in profile.skills or "ML" in profile.skills):
            print(f"   ✅ Навыки обновлены: '{profile.skills}'")
        else:
            print(f"   ❌ Навыки НЕ обновлены (текущее: '{profile.skills}')")
        
        # 2.3: Изменение города
        print(f"\n[USER] Переехал в Петербург\n")
        
        response4 = await chat_with_ai(
            message="Переехал в Петербург",
            user_id=test_user_id
        )
        
        print(f"[BOT] {response4.get('response', '')}\n")
        
        session.expire_all()
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        
        print("📊 ПРОВЕРКА ИЗМЕНЕНИЯ ГОРОДА:")
        if profile.city and "Петербург" in profile.city:
            print(f"   ✅ Город изменён: '{profile.city}'")
        else:
            print(f"   ⚠️ Город не изменён (текущее: '{profile.city}')")
            print(f"   Возможно агент не распознал как изменение города")
        
        # 2.4: Добавление новых интересов
        print(f"\n[USER] Интересуюсь блокчейном и криптовалютами\n")
        
        response5 = await chat_with_ai(
            message="Интересуюсь блокчейном и криптовалютами",
            user_id=test_user_id
        )
        
        print(f"[BOT] {response5.get('response', '')}\n")
        
        session.expire_all()
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        
        print("📊 ПРОВЕРКА РАСШИРЕНИЯ ИНТЕРЕСОВ:")
        if profile.interests:
            if "блокчейн" in profile.interests.lower() or "крипто" in profile.interests.lower():
                print(f"   ✅ Интересы расширены: '{profile.interests}'")
            else:
                print(f"   ❌ Новые интересы НЕ добавлены")
                print(f"   Текущие: '{profile.interests}'")
        
        # ========== ТЕСТ 3: ПРОВЕРКА ТЕКУЩЕГО ПРОФИЛЯ ==========
        print("\n" + "=" * 80)
        print("[ТЕСТ 3] ПРОВЕРКА: агент видит обновлённый профиль")
        print("=" * 80)
        
        session.expire_all()
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        
        print(f"\n📋 ФИНАЛЬНЫЙ ПРОФИЛЬ:")
        print(f"   Интересы: {profile.interests}")
        print(f"   Город: {profile.city}")
        print(f"   Компания: {profile.company}")
        print(f"   Навыки: {profile.skills}")
        print(f"   Цели: {profile.goals or '(не указаны)'}")
        
        print(f"\n[USER] Расскажи что ты знаешь обо мне\n")
        
        response6 = await chat_with_ai(
            message="Расскажи что ты знаешь обо мне",
            user_id=test_user_id
        )
        
        print(f"[BOT] {response6.get('response', '')}\n")
        
        resp_text6 = response6.get('response', '')
        
        print("📊 ПРОВЕРКА ИСПОЛЬЗОВАНИЯ ОБНОВЛЁННЫХ ДАННЫХ:")
        profile_checks = []
        
        if profile.company and profile.company in resp_text6:
            profile_checks.append(f"✅ Упомянул компанию: {profile.company}")
        else:
            profile_checks.append(f"❌ НЕ упомянул компанию")
        
        if profile.skills and any(skill in resp_text6 for skill in ["JavaScript", "ML", "навык"]):
            profile_checks.append(f"✅ Упомянул навыки")
        else:
            profile_checks.append(f"❌ НЕ упомянул навыки")
        
        if profile.city and profile.city in resp_text6:
            profile_checks.append(f"✅ Упомянул город: {profile.city}")
        else:
            profile_checks.append(f"⚠️ Не упомянул город")
        
        for check in profile_checks:
            print(f"   {check}")
        
        # ========== ИТОГОВАЯ ОЦЕНКА ==========
        print("\n" + "=" * 80)
        print("ИТОГОВАЯ ОЦЕНКА")
        print("=" * 80)
        
        total_score = 0
        max_score = 6
        
        # Извлечение (1 балл)
        if any("✅" in c for c in checks):
            total_score += 1
            print("✅ Извлечение: использует данные из профиля")
        else:
            print("❌ Извлечение: НЕ использует данные")
        
        # Изменение: компания (1 балл)
        session.expire_all()
        profile = session.query(UserProfile).filter_by(user_id=user.id).first()
        if profile.company and "TechAI" in profile.company:
            total_score += 1
            print("✅ Изменение (компания): обновляет профиль")
        else:
            print("❌ Изменение (компания): НЕ обновляет")
        
        # Изменение: навыки (1 балл)
        if profile.skills and ("JavaScript" in profile.skills or "ML" in profile.skills):
            total_score += 1
            print("✅ Изменение (навыки): обновляет профиль")
        else:
            print("❌ Изменение (навыки): НЕ обновляет")
        
        # Изменение: город (1 балл)
        if profile.city and "Петербург" in profile.city:
            total_score += 1
            print("✅ Изменение (город): обновляет профиль")
        else:
            print("⚠️ Изменение (город): не обновил (возможна доработка)")
        
        # Изменение: интересы (1 балл)
        if profile.interests and ("блокчейн" in profile.interests.lower() or "крипто" in profile.interests.lower()):
            total_score += 1
            print("✅ Изменение (интересы): расширяет список")
        else:
            print("❌ Изменение (интересы): НЕ расширяет")
        
        # Использование обновлённых данных (1 балл)
        if sum(1 for c in profile_checks if "✅" in c) >= 2:
            total_score += 1
            print("✅ Проверка: использует обновлённый профиль")
        else:
            print("❌ Проверка: НЕ использует обновлённый профиль")
        
        print(f"\n🎯 ИТОГО: {total_score}/{max_score} ({int(total_score/max_score*100)}%)")
        
        if total_score >= 5:
            print("✅ ОТЛИЧНО - агент эффективно работает с профилем!")
        elif total_score >= 3:
            print("⚠️ ХОРОШО - но есть что улучшить")
        else:
            print("❌ ПЛОХО - агент плохо работает с профилем")
        
    finally:
        # Очистка
        try:
            session.delete(profile)
            session.delete(user)
            session.commit()
        except:
            pass
        session.close()

if __name__ == "__main__":
    asyncio.run(test_profile_operations())
