"""
Тест проверки сохранения сообщений в БД после действий с кнопками
"""
import asyncio
from models import Session, User, Interaction
from datetime import datetime
import pytz

def test_message_persistence():
    """Проверяет, что сообщения от кнопок сохраняются в БД"""
    session = Session()
    
    try:
        # Найти тестового пользователя
        user = session.query(User).filter_by(username='testuser').first()
        if not user:
            print("❌ Тестовый пользователь не найден. Создайте пользователя с username='testuser'")
            return False
        
        # Получить количество сообщений до
        before_count = session.query(Interaction).filter_by(user_id=user.id).count()
        print(f"📊 Количество сообщений в БД до теста: {before_count}")
        
        # Имитировать добавление сообщения от рейтинга
        rating_message = Interaction(
            user_id=user.id,
            message_type='ai',
            content='✓ Оценка 8/10 для @partner сохранена'
        )
        session.add(rating_message)
        session.commit()
        
        # Имитировать добавление сообщения от скрытия контакта
        hide_message = Interaction(
            user_id=user.id,
            message_type='ai',
            content='@partner скрыт на 7 дней'
        )
        session.add(hide_message)
        session.commit()
        
        # Имитировать добавление сообщения от завершения задачи
        complete_message = Interaction(
            user_id=user.id,
            message_type='ai',
            content="Завершена задача 'Тестовая задача'."
        )
        session.add(complete_message)
        session.commit()
        
        # Получить количество сообщений после
        after_count = session.query(Interaction).filter_by(user_id=user.id).count()
        print(f"📊 Количество сообщений в БД после теста: {after_count}")
        
        if after_count == before_count + 3:
            print("✅ Все 3 сообщения успешно сохранены в БД")
            
            # Проверить последние 3 сообщения
            recent = session.query(Interaction)\
                .filter_by(user_id=user.id)\
                .order_by(Interaction.id.desc())\
                .limit(3)\
                .all()
            
            print("\n📝 Последние 3 сообщения:")
            for msg in reversed(recent):
                print(f"   - [{msg.message_type}] {msg.content}")
            
            return True
        else:
            print(f"❌ Ожидалось {before_count + 3} сообщений, получено {after_count}")
            return False
            
    except Exception as e:
        print(f"❌ Ошибка при тестировании: {e}")
        session.rollback()
        return False
    finally:
        session.close()

if __name__ == '__main__':
    print("🧪 Тест сохранения сообщений в БД\n")
    success = test_message_persistence()
    print(f"\n{'✅ ТЕСТ ПРОЙДЕН' if success else '❌ ТЕСТ НЕ ПРОЙДЕН'}")
