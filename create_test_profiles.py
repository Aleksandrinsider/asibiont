from models import Session, User, UserProfile

session = Session()

# Создать тестовых пользователей
user1 = User(telegram_id=4, username="testuser1")
user2 = User(telegram_id=12345, username="testuser2")
user3 = User(telegram_id=123456789, username="designer_alex")
session.add(user1)
session.add(user2)
session.add(user3)
session.commit()

# Профили
profile1 = UserProfile(user_id=user1.id, skills="дизайн, искусство", interests="креативность, технологии", goals="создать сайт", contact_info="@testuser1", city="Москва")
profile2 = UserProfile(user_id=user2.id, skills="программирование, маркетинг", interests="бизнес, стартапы", goals="найти партнеров", contact_info="@testuser2", city="Санкт-Петербург")
profile3 = UserProfile(user_id=user3.id, skills="дизайн, UI/UX", interests="веб-дизайн, мобильные приложения", goals="сотрудничество в проектах", contact_info="@designer_alex", city="Москва")
session.add(profile1)
session.add(profile2)
session.add(profile3)
session.commit()

session.close()
print("Тестовые профили созданы")