"""
Скрипт для создания тестовых постов от разных пользователей
"""
import datetime
from models import Session, Post, User

def create_test_posts():
    session = Session()
    
    try:
        # Получаем тестовых пользователей
        users = session.query(User).filter(User.telegram_id.in_([1001, 1002, 1003, 1004, 1005])).all()
        
        if not users:
            print("Тестовые пользователи не найдены. Сначала запустите create_test_users.py")
            return
        
        # Посты от разных пользователей
        test_posts = [
            {
                'telegram_id': 1001,
                'content': 'Сегодня отличный день! Только что завершил важный проект по внедрению AI-решения для автоматизации бизнес-процессов. Результаты превзошли все ожидания 🚀',
                'days_ago': 0,
                'hours_ago': 2
            },
            {
                'telegram_id': 1002,
                'content': 'Делюсь опытом: недавно протестировал новый фреймворк для машинного обучения. Скорость обучения моделей увеличилась на 40%! Кто-нибудь уже пробовал?',
                'days_ago': 0,
                'hours_ago': 5
            },
            {
                'telegram_id': 1003,
                'content': 'Организуем встречу предпринимателей в сфере AI на следующей неделе. Будем обсуждать тренды 2026 года и перспективные направления. Кто заинтересован - пишите в личку!',
                'days_ago': 1,
                'hours_ago': 3
            },
            {
                'telegram_id': 1001,
                'content': 'Прочитал отличную статью о нейросетях в медицине. Технологии развиваются невероятно быстро. Через 5 лет мир будет совсем другим.',
                'days_ago': 1,
                'hours_ago': 8
            },
            {
                'telegram_id': 1004,
                'content': 'Ищу партнера для стартапа в области EdTech. Есть проработанная идея и первые инвестиции. Нужен технический со-основатель с опытом в разработке.',
                'days_ago': 2,
                'hours_ago': 1
            },
            {
                'telegram_id': 1002,
                'content': 'Завтра выступаю на конференции "Цифровая трансформация 2026". Расскажу о кейсе внедрения AI в логистике. Приходите, будет интересно!',
                'days_ago': 2,
                'hours_ago': 6
            },
            {
                'telegram_id': 1005,
                'content': 'Команда растёт! Сегодня провели onboarding для трёх новых разработчиков. Офис превращается в настоящий tech hub 💻',
                'days_ago': 3,
                'hours_ago': 4
            },
            {
                'telegram_id': 1003,
                'content': 'Размышления о балансе работы и личной жизни. Важно помнить, что успех в бизнесе не должен идти в ущерб здоровью и семье. Берегите себя!',
                'days_ago': 3,
                'hours_ago': 10
            }
        ]
        
        # Создаем посты
        created_count = 0
        for post_data in test_posts:
            user = next((u for u in users if u.telegram_id == post_data['telegram_id']), None)
            if not user:
                continue
            
            # Проверяем, нет ли уже такого поста
            existing = session.query(Post).filter_by(
                user_id=user.id,
                content=post_data['content']
            ).first()
            
            if existing:
                print(f"Пост от пользователя {post_data['telegram_id']} уже существует, пропускаем")
                continue
            
            # Вычисляем время создания
            created_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
                days=post_data['days_ago'],
                hours=post_data['hours_ago']
            )
            
            post = Post(
                user_id=user.id,
                content=post_data['content'],
                created_at=created_at
            )
            session.add(post)
            created_count += 1
            print(f"Создан пост от пользователя {post_data['telegram_id']}")
        
        session.commit()
        print(f"\n✅ Успешно создано {created_count} тестовых постов!")
        
    except Exception as e:
        print(f"❌ Ошибка при создании постов: {e}")
        session.rollback()
    finally:
        session.close()

if __name__ == '__main__':
    create_test_posts()
