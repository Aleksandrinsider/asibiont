import datetime
import logging
import enum
import json
import os
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, Text, ForeignKey, Enum, UniqueConstraint, BigInteger, Float, text, Index
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from config import DATABASE_URL

logger = logging.getLogger(__name__)
Base = declarative_base()


class SubscriptionTier(enum.Enum):
    LIGHT = 'LIGHT'      # 3000 RUB/month
    STANDARD = 'STANDARD'  # 9000 RUB/month
    PREMIUM = 'PREMIUM'    # 27000 RUB/month


class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)  # Индекс для быстрого поиска по telegram_id
    username = Column(String(255), index=True)  # Индекс для поиска по username
    first_name = Column(String(255))
    photo_url = Column(String(500))  # Telegram profile photo URL
    custom_avatar = Column(Text)  # Custom avatar uploaded by user (base64 data URI)
    memory = Column(Text)  # Long-term memory for user info
    long_term_memory = Column(Text)  # JSON with project history, preferences, patterns
    timezone = Column(String(50), default='Europe/Moscow')
    do_not_disturb_until = Column(DateTime)
    pending_action = Column(Text)  # JSON for pending interactions
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), index=True)  # Индекс для сортировки пользователей
    updated_at = Column(
        DateTime, default=lambda: datetime.datetime.now(
            datetime.timezone.utc), onupdate=lambda: datetime.datetime.now(
            datetime.timezone.utc))
    subscription_tier = Column(Enum(SubscriptionTier), default=SubscriptionTier.LIGHT, index=True)  # Индекс для фильтрации по подписке
    average_rating = Column(Integer, default=0)  # Average rating from other users (0-10)
    rating_count = Column(Integer, default=0)  # Number of ratings received (synced from UserProfile)
    history_cleared_at = Column(DateTime)  # When user cleared chat history
    conversation_state = Column(String(100), default='normal')  # Current conversation state
    pending_task_data = Column(Text)  # JSON for pending task creation data
    last_interaction_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), index=True)  # Индекс для активных пользователей
    conversation_context = Column(Text)  # JSON array of recent messages for context
    current_task_id = Column(Integer, ForeignKey('tasks.id'))  # Currently discussed task
    referral_balance = Column(Integer, default=0)  # Referral earnings in kopecks
    referrer_id = Column(Integer, ForeignKey('users.id'))  # User who referred this user
    telegram_channel = Column(String(255))  # Telegram channel username or ID for auto-posting (e.g., @my_channel or -1001234567890)
    discord_webhook = Column(String(500))  # Discord webhook URL for auto-posting (e.g., https://discord.com/api/webhooks/...)
    discord_server_name = Column(String(255))  # Discord server name (fetched from webhook)
    discord_guild_id = Column(String(64))  # Discord guild ID (for link)
    discord_channel_id = Column(String(64))  # Discord channel ID (for link)
    token_balance = Column(Integer, default=0)  # Баланс токенов (1 токен = 1 рубль)
    tokens_spent = Column(Integer, default=0)  # Всего потрачено токенов
    language = Column(String(5), default='ru')  # User language: 'ru' or 'en'
    platform = Column(String(20), default='telegram')  # 'telegram' or 'discord'
    discord_id = Column(BigInteger, unique=True, nullable=True, index=True)  # Discord user ID
    discord_username = Column(String(255), nullable=True)  # Discord username for display
    email = Column(String(255), unique=True, nullable=True, index=True)  # Email for web login
    password_hash = Column(String(500), nullable=True)  # PBKDF2 hash for email login
    phone = Column(String(20), nullable=True, index=True)  # Phone number

    current_task = relationship("Task", foreign_keys=[current_task_id])


class Task(Base):
    __tablename__ = 'tasks'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)  # Индекс для быстрого поиска задач пользователя
    title = Column(String(255), nullable=False)
    description = Column(Text)
    due_date = Column(DateTime)
    status = Column(String(50), default='pending', index=True)  # Индекс для фильтрации по статусу
    reminder_time = Column(DateTime, index=True)  # Индекс для поиска по времени напоминания
    reminder_sent = Column(Boolean, default=False)
    followup_reminder_sent = Column(Boolean, default=False)  # Повторное напоминание через 15 минут
    result_check_sent = Column(Boolean, default=False)
    estimated_duration = Column(Integer)  # in minutes
    delegated_by = Column(Integer, ForeignKey('users.id'))  # User who delegated the task
    delegated_to_username = Column(String(255), index=True)  # Индекс для поиска делегированных задач
    delegation_status = Column(String(50), default=None, index=True)  # Индекс для статуса делегирования
    delegation_details = Column(Text)  # Additional details about delegation
    delegation_campaign_id = Column(Integer, nullable=True)  # Link to DelegationCampaign if from campaign
    completion_notes = Column(Text)  # Notes about task completion/result
    actual_completion_time = Column(DateTime)  # When task was actually completed
    skipped_reason = Column(String(255))  # Reason if task was skipped/cancelled
    overdue_reminders_sent = Column(Integer, default=0)  # Number of overdue reminders sent
    recommendations = Column(Text)  # JSON array of AI-generated recommendations
    is_recurring = Column(Boolean, default=False, index=True)  # Индекс для повторяющихся задач
    recurrence_pattern = Column(String(50))  # daily, weekly, monthly, yearly
    recurrence_interval = Column(Integer, default=1)  # Every N days/weeks/months
    recurrence_end_date = Column(DateTime)  # When to stop recurring
    parent_task_id = Column(Integer, ForeignKey('tasks.id'))  # For recurring task instances
    pending_delegator_report = Column(BigInteger)  # Telegram ID of delegator waiting for completion report
    goal_id = Column(Integer, ForeignKey('goals.id'))  # Link to goal this task contributes to
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), index=True)  # Индекс для сортировки по дате создания

    user = relationship("User", backref="tasks", foreign_keys=[user_id])
    goal = relationship("Goal", backref="tasks")


class Interaction(Base):
    __tablename__ = 'interactions'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)  # Индекс для поиска взаимодействий пользователя
    message_type = Column(String(50), index=True)  # Индекс для фильтрации по типу сообщения
    content = Column(Text)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), index=True)  # Индекс для сортировки по времени

    user = relationship("User", backref="interactions")


class Note(Base):
    __tablename__ = 'notes'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    title = Column(String(200), nullable=True)
    content = Column(Text, nullable=False)
    source = Column(String(20), default='manual')  # 'manual' or 'chat'
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), index=True)

    user = relationship("User", backref="notes")


class UserProfile(Base):
    __tablename__ = 'user_profiles'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, unique=True)
    skills = Column(Text)  # JSON or comma-separated skills
    interests = Column(Text)  # JSON or comma-separated interests
    goals = Column(Text)  # User's goals
    contact_info = Column(String(255))  # Telegram username or other contact
    city = Column(String(100))  # City for location-based matching
    country = Column(String(100))  # Country
    birthdate = Column(String(10))  # Date of birth in DD.MM.YYYY format
    zodiac_sign = Column(String(20))  # Zodiac sign (auto-calculated)
    company = Column(String(255))  # Company name
    position = Column(String(255))  # Job position
    bio = Column(Text)  # Short bio/description (2-3 sentences about user)
    languages = Column(String(500))  # Languages: Русский (родной), English (C1), Español (A2)
    current_plans = Column(Text)  # Current plans or events, e.g., "Сегодня иду в кино, завтра на выставку"
    current_time = Column(String(10))  # User's current time in HH:MM format, for relative time calculations
    total_tasks_created = Column(Integer, default=0)  # Total tasks created
    completed_tasks = Column(Integer, default=0)  # Total completed tasks
    skipped_tasks = Column(Integer, default=0)  # Tasks marked as skipped or overdue
    average_completion_time = Column(Integer, default=0)  # Average time to complete tasks in minutes
    last_activity = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))  # Last interaction time
    updated_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    average_rating = Column(Integer, default=0)  # Average rating from other users (0-10)
    rating_count = Column(Integer, default=0)  # Number of ratings received
    favorite_contacts = Column(Text)  # JSON array of favorite contact usernames
    blocked_contacts = Column(Text)  # JSON array of blocked contact usernames
    interaction_count = Column(Integer, default=0)  # Total interactions with AI
    pending_premium_recommendations = Column(Text)  # JSON array of Premium recommendations to mention in dialogue
    content_strategy = Column(Text)  # User's content strategy: what they want to post about, target audience, goals
    auto_marketing_enabled = Column(Boolean, default=True)  # Enable/disable autonomous marketing (Premium)
    auto_delegation_enabled = Column(Boolean, default=True)  # Enable/disable autonomous delegation (Premium)
    auto_post_time = Column(String(5), default='12:00')  # Preferred time for auto-posting in HH:MM format (Premium)
    status_text = Column(String(100))  # User status: 'Инвестор', 'Ищу работу', 'Ищу партнёра', etc.

    # Normalized (English) versions of profile fields for cross-language matching
    skills_normalized = Column(Text)
    interests_normalized = Column(Text)
    goals_normalized = Column(Text)
    city_normalized = Column(String(100))
    country_normalized = Column(String(100))
    company_normalized = Column(String(255))
    position_normalized = Column(String(255))
    bio_normalized = Column(Text)
    status_text_normalized = Column(String(100))
    current_plans_normalized = Column(Text)

    # Normalized (Russian) versions for displaying to RU users
    skills_normalized_ru = Column(Text)
    interests_normalized_ru = Column(Text)
    goals_normalized_ru = Column(Text)
    city_normalized_ru = Column(String(100))
    country_normalized_ru = Column(String(100))
    company_normalized_ru = Column(String(255))
    position_normalized_ru = Column(String(255))
    bio_normalized_ru = Column(Text)
    status_text_normalized_ru = Column(String(100))
    current_plans_normalized_ru = Column(Text)

    user = relationship("User", backref="profile")


class Goal(Base):
    __tablename__ = 'goals'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text)
    status = Column(String(50), default='active')  # active, completed, paused, cancelled
    priority = Column(String(20), default='medium')  # low, medium, high, critical
    category = Column(String(100))  # work, personal, health, learning, etc.
    target_date = Column(DateTime)  # When goal should be achieved
    completed_at = Column(DateTime)  # When goal was actually completed
    progress_percentage = Column(Integer, default=0)  # 0-100
    progress_notes = Column(Text)  # Notes about progress
    related_tasks = Column(Text)  # JSON array of related task IDs
    success_criteria = Column(Text)  # How to measure success
    # Метрики цели — измеримый прогресс
    metric_unit = Column(String(100))  # единица измерения: 'учеников', 'кг', 'руб', 'км', 'подписчиков'
    metric_target = Column(Float)  # целевое значение: 50, 10, 1000000
    metric_current = Column(Float, default=0)  # текущее значение: 12, 3, 250000
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), onupdate=lambda: datetime.datetime.now(datetime.timezone.utc))

    user = relationship("User", backref="goals")

    def is_overdue(self):
        """Check if goal is overdue"""
        if self.target_date and self.status == 'active':
            target = self.target_date
            if target.tzinfo is None:
                target = target.replace(tzinfo=datetime.timezone.utc)
            return datetime.datetime.now(datetime.timezone.utc) > target
        return False

    def days_until_target(self):
        """Calculate days until target date"""
        if self.target_date:
            target = self.target_date
            if target.tzinfo is None:
                target = target.replace(tzinfo=datetime.timezone.utc)
            delta = target - datetime.datetime.now(datetime.timezone.utc)
            return delta.days
        return None


class UserRating(Base):
    __tablename__ = 'user_ratings'

    id = Column(Integer, primary_key=True)
    rater_user_id = Column(Integer, ForeignKey('users.id'), nullable=False)  # User who gives the rating
    rated_user_id = Column(Integer, ForeignKey('users.id'), nullable=False)  # User who receives the rating
    rating = Column(Integer, nullable=False)  # Rating value 1-10
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    rater = relationship("User", foreign_keys=[rater_user_id])
    rated_user = relationship("User", foreign_keys=[rated_user_id])

    __table_args__ = (
        UniqueConstraint('rater_user_id', 'rated_user_id', name='unique_user_rating'),
    )


class Subscription(Base):
    __tablename__ = 'subscriptions'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, unique=True)
    telegram_id = Column(BigInteger, nullable=False)  # Telegram ID for quick access
    telegram_username = Column(String(100))  # Telegram username for identification
    username = Column(String(255))  # Username for quick access
    status = Column(String(50), default='inactive')  # active, inactive, expired
    plan = Column(String(50), default='monthly')  # monthly, yearly, etc.
    tier = Column(Enum(SubscriptionTier), default=SubscriptionTier.LIGHT)  # Subscription tier
    start_date = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    end_date = Column(DateTime)
    login_count = Column(Integer, default=0)  # Number of logins
    subscriber_number = Column(Integer, unique=True)  # Subscriber number
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    user = relationship("User", backref="subscription")


class PaymentHistory(Base):
    """История всех изменений подписок и платежей для защиты от потери данных"""
    __tablename__ = 'payment_history'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    telegram_username = Column(String(100))
    action = Column(String(50), nullable=False)  # payment, tier_change, subscription_activated, promo_used, etc.
    tier = Column(Enum(SubscriptionTier), nullable=False)  # Tier at the time of action
    amount = Column(String(20))  # Payment amount if applicable
    payment_id = Column(String(100))  # External payment system ID (Yookassa, etc.)
    duration_days = Column(Integer)  # Duration of subscription
    start_date = Column(DateTime)  # Subscription start date
    end_date = Column(DateTime)  # Subscription end date
    details = Column(Text)  # JSON with additional details
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    user = relationship("User", backref="payment_history")


class Post(Base):
    """User posts for news feed"""
    __tablename__ = 'posts'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    username = Column(String(255))  # Denormalized username for easy viewing
    content = Column(Text, nullable=False)  # Post content
    image_url = Column(Text, nullable=True)  # Optional image (base64 data URL)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    user = relationship("User", backref="posts")


class PostLike(Base):
    """Likes on posts"""
    __tablename__ = 'post_likes'

    id = Column(Integer, primary_key=True)
    post_id = Column(Integer, ForeignKey('posts.id', ondelete='CASCADE'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    post = relationship("Post", backref="likes")
    user = relationship("User", backref="post_likes")

    # Unique constraint: один пользователь может поставить только один лайк посту
    __table_args__ = (
        UniqueConstraint('post_id', 'user_id', name='unique_post_like'),
    )


class Comment(Base):
    """Comments on posts"""
    __tablename__ = 'comments'

    id = Column(Integer, primary_key=True)
    post_id = Column(Integer, ForeignKey('posts.id', ondelete='CASCADE'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    username = Column(String(255))  # Denormalized username for easy viewing
    content = Column(Text, nullable=False)  # Comment content
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    post = relationship("Post", backref="comments")
    user = relationship("User", backref="comments")


class PostView(Base):
    """Tracks which posts user has viewed"""
    __tablename__ = 'post_views'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    post_id = Column(Integer, ForeignKey('posts.id', ondelete='CASCADE'), nullable=False)
    viewed_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    user = relationship("User", backref="post_views")
    post = relationship("Post", backref="views")

    # Unique constraint: один пользователь может просмотреть пост только один раз
    __table_args__ = (
        UniqueConstraint('user_id', 'post_id', name='unique_post_view'),
    )


class ActivityAlert(Base):
    """Premium: Alerts for activities of other users"""
    __tablename__ = 'activity_alerts'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    activity_type = Column(String(100), nullable=False)  # 'пробежка', 'митап по AI', etc.
    keywords = Column(Text, nullable=False)  # JSON array of keywords
    location = Column(String(100))  # City filter
    frequency = Column(String(20), default='any')  # 'any', 'regular', 'one_time'
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    last_triggered_at = Column(DateTime)  # Last time alert was triggered

    user = relationship("User", backref="activity_alerts")


class ContactAlert(Base):
    """Premium: Alerts for new users with specific skills/interests"""
    __tablename__ = 'contact_alerts'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    skill = Column(String(100))  # Skill to search for
    interest = Column(String(100))  # Interest to search for
    city = Column(String(100))  # City filter
    position = Column(String(100))  # Position/role filter
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    last_triggered_at = Column(DateTime)  # Last time alert was triggered

    user = relationship("User", backref="contact_alerts")


class AnchorPriority(enum.Enum):
    CRITICAL = 'CRITICAL'    # Просроченные задачи, горящие дедлайны — доставка в течение 30 мин
    HIGH = 'HIGH'            # Дедлайн <24ч, обновления делегирования — батч каждые 2ч
    MEDIUM = 'MEDIUM'        # Инсайты, контакты, мониторинг рынка — батч каждые 4ч
    LOW = 'LOW'              # Погода, контент-идеи, общая вовлечённость — макс 1/день


class Anchor(Base):
    """
    Якорь — событие или факт, обнаруженный AnchorEngine.
    
    Заменяет: проактивные сообщения (15+ типов), contact_alerts, auto_post triggers.
    AI получает все сработавшие якоря + полный контекст и САМИ РЕШАЕТ писать или нет.
    """
    __tablename__ = 'anchors'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)

    # Что за якорь
    anchor_type = Column(String(50), nullable=False, index=True)
    # Типы: task_overdue, task_deadline_soon, task_stale, task_completed_streak,
    #        goal_progress, goal_stagnation, goal_deadline,
    #        contact_match, contact_online,
    #        profile_gap, dialog_followup,
    #        delegation_pending, delegation_update,
    #        market_insight, content_opportunity,
    #        weather_activity, morning_plan, evening_review

    source = Column(String(100))       # Откуда: 'task:42', 'goal:7', 'contact:@username', 'ltm', 'api:weather'
    topic = Column(String(500))        # Человекочитаемое описание: "Задача 'Звонок клиенту' просрочена на 2ч"
    priority = Column(Enum(AnchorPriority), default=AnchorPriority.MEDIUM, index=True)
    data = Column(Text)                # JSON с деталями для AI: {task_id, hours_overdue, ...}

    # Жизненный цикл
    created_at = Column(DateTime, default=datetime.datetime.now(datetime.timezone.utc), index=True)
    triggered_at = Column(DateTime)     # Когда сработал (факт подтвердился)
    delivered_at = Column(DateTime)     # Когда был доставлен пользователю
    expires_at = Column(DateTime)       # Когда якорь теряет актуальность

    # Реакция пользователя (feedback loop)
    user_reaction = Column(String(20))  # responded, ignored, dismissed
    reaction_at = Column(DateTime)

    # Антиспам
    cooldown_hours = Column(Float, default=4)    # Минимум часов между повторными якорями этого типа
    suppress_until = Column(DateTime)              # Подавлен до (если пользователь отклонил)
    batch_group = Column(String(50))               # Группа для батчинга: 'tasks', 'contacts', 'insights'

    user = relationship("User", backref="anchors")

    # Составные индексы для частых запросов AnchorEngine
    __table_args__ = (
        Index('ix_anchors_user_delivered', 'user_id', 'delivered_at'),  # deliverable lookup
        Index('ix_anchors_user_type_delivered', 'user_id', 'anchor_type', 'delivered_at'),  # cooldown check
    )

    def is_expired(self):
        if self.expires_at:
            now = datetime.datetime.now(datetime.timezone.utc)
            exp = self.expires_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=datetime.timezone.utc)
            return now > exp
        return False

    def is_deliverable(self):
        """Можно ли доставить - не доставлен, не истёк, не подавлен"""
        if self.delivered_at:
            return False
        if self.is_expired():
            return False
        if self.suppress_until:
            now = datetime.datetime.now(datetime.timezone.utc)
            sup = self.suppress_until
            if sup.tzinfo is None:
                sup = sup.replace(tzinfo=datetime.timezone.utc)
            if now < sup:
                return False
        return True

    def to_ai_context(self):
        """Сериализация для передачи в AI контекст"""
        return {
            'type': self.anchor_type,
            'topic': self.topic,
            'priority': self.priority.value if self.priority else 'MEDIUM',
            'source': self.source,
            'data': json.loads(self.data) if self.data else {},
            'age_minutes': int((datetime.datetime.now(datetime.timezone.utc) - 
                               (self.created_at.replace(tzinfo=datetime.timezone.utc) if self.created_at.tzinfo is None else self.created_at)
                               ).total_seconds() / 60) if self.created_at else 0
        }


class PushSubscription(Base):
    """Web Push subscriptions for browser notifications"""
    __tablename__ = 'push_subscriptions'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    endpoint = Column(Text, nullable=False)
    keys_p256dh = Column(String(500), nullable=False)
    keys_auth = Column(String(500), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    user = relationship("User", backref="push_subscriptions")


class TokenTransaction(Base):
    """Транзакции токенов — полная история начислений и списаний"""
    __tablename__ = 'token_transactions'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    amount = Column(Integer, nullable=False)       # + начисление, - списание
    action = Column(String(100), nullable=False)   # Тип действия (message, add_task, purchase, signup...) 
    description = Column(Text)                     # Описание
    balance_after = Column(Integer)                 # Баланс после транзакции
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), index=True)

    user = relationship("User", backref="token_transactions")


class AnchorDeliveryLog(Base):
    """Лог доставок — для аналитики и антиспама"""
    __tablename__ = 'anchor_delivery_log'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    anchor_ids = Column(Text)          # JSON array: [1, 5, 12] — какие якоря вошли в сообщение
    message_text = Column(Text)        # Что отправили
    anchor_types = Column(Text)        # JSON: ['task_overdue', 'goal_progress'] — для статистики
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), index=True)
    user_responded = Column(Boolean)   # True если пользователь ответил в течение часа
    response_time_seconds = Column(Integer)  # Время до ответа

    user = relationship("User", backref="anchor_logs")


class EmailContact(Base):
    """
    Справочник email-контактов пользователя.
    Централизованное хранение: добавлять вручную, из кампаний, из отчётов.
    """
    __tablename__ = 'email_contacts'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)

    email = Column(String(300), nullable=False)
    name = Column(String(200))
    company = Column(String(200))
    position = Column(String(200))
    notes = Column(Text)                     # Контекст от юзера или AI
    source = Column(String(50), default='manual')  # manual / campaign / import
    status = Column(String(50), default='new', index=True)  # new / contacted / replied / interested / unsubscribed / bounced
    last_contacted_at = Column(DateTime)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc),
                        onupdate=lambda: datetime.datetime.now(datetime.timezone.utc))

    user = relationship("User", backref="email_contacts")

    __table_args__ = (
        Index('ix_email_contacts_user_email', 'user_id', 'email', unique=True),
    )


class EmailCampaign(Base):
    """
    Email-кампания для автономного привлечения клиентов.

    Пользователь описывает цель (напр. "найти клиентов для AI-сервиса"),
    агент автономно ищет email-адреса, отправляет предложения через Resend API,
    отвечает на входящие письма в рамках заданной цели.
    """
    __tablename__ = 'email_campaigns'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)

    # Конфигурация кампании
    name = Column(String(300))              # "Привлечение клиентов для AI-сервиса"
    goal = Column(Text)                      # Подробная цель кампании (промпт для AI)
    target_audience = Column(Text)           # Описание целевой аудитории
    offer = Column(Text)                     # Что предлагаем (продукт/услуга/ценностное предложение)
    tone = Column(String(50), default='professional')  # professional, friendly, formal
    sender_name = Column(String(200))        # Имя отправителя
    sender_email = Column(String(200))       # Email (верифицирован в Resend)

    # Лимиты
    max_emails = Column(Integer, default=50)    # Макс. писем в кампании
    daily_limit = Column(Integer, default=10)   # Макс. писем в день
    max_follow_ups = Column(Integer, default=2) # Макс. фоллоу-апов на одно письмо

    # Статус
    status = Column(String(50), default='active', index=True)  # active, paused, completed, cancelled
    emails_sent = Column(Integer, default=0)
    emails_replied = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc),
                        onupdate=lambda: datetime.datetime.now(datetime.timezone.utc))

    user = relationship("User", backref="email_campaigns")


class EmailOutreach(Base):
    """
    Отдельное письмо в рамках email-кампании.

    Жизненный цикл: draft → sent → delivered → opened → replied / bounced / failed
    AI-агент может автоматически отвечать на reply в рамках цели кампании.
    """
    __tablename__ = 'email_outreach'

    id = Column(Integer, primary_key=True)
    campaign_id = Column(Integer, ForeignKey('email_campaigns.id'), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)

    # Получатель
    recipient_email = Column(String(300), nullable=False)
    recipient_name = Column(String(200))
    recipient_company = Column(String(200))
    recipient_context = Column(Text)         # Почему этот контакт релевантен

    # Контент письма
    subject = Column(String(500))
    body = Column(Text)                      # HTML тело письма

    # Статус и трекинг
    status = Column(String(50), default='draft', index=True)  # draft, sent, delivered, opened, replied, bounced, failed
    resend_id = Column(String(200))          # ID письма в Resend API

    # Ответы
    reply_text = Column(Text)                # Текст входящего ответа
    reply_at = Column(DateTime)
    ai_reply_text = Column(Text)             # Ответ агента
    ai_reply_sent_at = Column(DateTime)

    # Follow-up
    follow_up_count = Column(Integer, default=0)
    last_follow_up_at = Column(DateTime)
    next_follow_up_at = Column(DateTime)

    # Timestamps
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    sent_at = Column(DateTime)

    campaign = relationship("EmailCampaign", backref="emails")
    user = relationship("User", backref="email_outreach")

    __table_args__ = (
        Index('ix_email_outreach_campaign_status', 'campaign_id', 'status'),
        Index('ix_email_outreach_user_status', 'user_id', 'status'),
        Index('ix_email_outreach_campaign_recipient', 'campaign_id', 'recipient_email', unique=True),
    )


class ContentCampaign(Base):
    """
    Контент-кампания для автономной публикации постов в ленту / TG-канал / Discord.

    Аналог EmailCampaign, но для контента. Пользователь описывает стратегию,
    агент автономно генерирует и публикует посты по расписанию.
    """
    __tablename__ = 'content_campaigns'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)

    # Конфигурация
    name = Column(String(300))              # "Ежедневные посты про AI"
    goal = Column(Text)                      # Подробная цель/стратегия (промпт для AI)
    topics = Column(Text)                    # Темы для постов (через ; или свободный текст)
    platforms = Column(String(200), default='["feed"]')  # JSON: ["feed", "telegram", "discord"]
    tone = Column(String(50), default='professional')     # professional, casual, motivational, expert
    language = Column(String(10), default='ru')            # ru, en

    # Расписание
    frequency = Column(String(50), default='daily')        # daily, every_2_days, every_3_days, weekly
    post_time = Column(String(10), default='12:00')        # Preferred time HH:MM
    daily_limit = Column(Integer, default=1)               # Макс. постов в день

    # Лимиты
    max_posts = Column(Integer, default=0)                 # 0=unlimited
    posts_published = Column(Integer, default=0)           # Счётчик опубликованных

    # Статус
    status = Column(String(50), default='active', index=True)  # active, paused, completed, cancelled
    last_post_at = Column(DateTime)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc),
                        onupdate=lambda: datetime.datetime.now(datetime.timezone.utc))

    user = relationship("User", backref="content_campaigns")

    __table_args__ = (
        Index('ix_content_campaign_user_status', 'user_id', 'status'),
    )


class DelegationCampaign(Base):
    """
    Кампания массового делегирования — автономное распределение задач.

    Аналог EmailCampaign, но для делегирования внутри платформы.
    Пользователь описывает цель и целевую аудиторию, агент автономно
    находит подходящих исполнителей, делегирует задачи, отслеживает прогресс.
    """
    __tablename__ = 'delegation_campaigns'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)

    # Конфигурация
    name = Column(String(300))                    # "Найти тестировщиков для MVP"
    goal = Column(Text)                            # Подробная цель (промпт для AI)
    target_audience = Column(Text)                 # Кого ищем: навыки, интересы, город
    task_template = Column(Text)                   # Шаблон задачи для делегирования
    offer = Column(Text)                           # Что предлагаем исполнителю (мотивация)
    tone = Column(String(50), default='professional')

    # Лимиты
    max_delegations = Column(Integer, default=10)  # Макс. делегирований (0=unlimited)
    daily_limit = Column(Integer, default=3)       # Макс. делегирований в день
    max_follow_ups = Column(Integer, default=2)    # Макс. повторных обращений без ответа
    default_deadline_hours = Column(Integer, default=48)  # Дедлайн задачи (часов)

    # Счётчики
    delegations_sent = Column(Integer, default=0)
    delegations_accepted = Column(Integer, default=0)
    delegations_completed = Column(Integer, default=0)
    delegations_rejected = Column(Integer, default=0)

    # Статус
    status = Column(String(50), default='active', index=True)  # active, paused, completed, cancelled
    last_delegation_at = Column(DateTime)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc),
                        onupdate=lambda: datetime.datetime.now(datetime.timezone.utc))

    user = relationship("User", backref="delegation_campaigns")

    __table_args__ = (
        Index('ix_delegation_campaign_user_status', 'user_id', 'status'),
    )


class UserMessage(Base):
    """
    Сообщения между пользователями через AI-агента.
    Агент может отправить сообщение от имени одного пользователя другому —
    для согласования встреч, предложений по проекту, поиска единомышленников.
    """
    __tablename__ = 'user_messages'

    id = Column(Integer, primary_key=True)
    sender_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    recipient_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    
    # Контент
    message_text = Column(Text, nullable=False)          # Текст сообщения
    intent = Column(String(100))                          # Цель: meeting, collaboration, idea, project_invite, question
    context = Column(Text)                                # JSON: оригинальный запрос отправителя, задача, цель
    
    # Статус
    status = Column(String(50), default='sent', index=True)  # sent, delivered, read, replied, declined
    reply_text = Column(Text)                             # Ответ получателя
    replied_at = Column(DateTime)
    
    # Антиспам
    is_ai_generated = Column(Boolean, default=True)       # AI сгенерировал текст
    sender_approved = Column(Boolean, default=False)      # Отправитель подтвердил отправку (если нужно)
    
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), index=True)
    delivered_at = Column(DateTime)
    
    sender = relationship("User", foreign_keys=[sender_id], backref="sent_messages")
    recipient = relationship("User", foreign_keys=[recipient_id], backref="received_messages")


class AgentActivityLog(Base):
    """
    Лог всех автономных действий агента: делегирование, посты, рассылки, TG-канал, и т.д.
    Отображается в табе «Активность» на дашборде.
    """
    __tablename__ = 'agent_activity_log'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)

    # Тип действия
    activity_type = Column(String(50), nullable=False, index=True)
    # Типы: 'delegation' | 'post_newsfeed' | 'post_telegram' | 'user_message' | 'email' | 'post_discord' | 'other'

    # Заголовок / краткое описание (для строки в списке)
    title = Column(String(300), nullable=False)

    # Детали: текст поста, тело письма, текст сообщения и т.п.
    content = Column(Text)

    # Кому / куда (получатель делегирования, канал, email и т.д.)
    target = Column(String(300))

    # Статус
    status = Column(String(50), default='completed', index=True)
    # delegation: pending / accepted / rejected / cancelled
    # post_*: published / deleted
    # other: completed / failed

    # Ссылка на исходную запись (task.id, post.id, …)
    ref_id = Column(Integer)

    # Результат / ответ (для делегирования — ответ получателя; для рассылок — ответ)
    result = Column(Text)

    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), index=True)
    updated_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    user = relationship("User", backref="agent_activities")

    __table_args__ = (
        Index('ix_agent_activity_user_type', 'user_id', 'activity_type'),
    )


# ═══════════════════════════════════════════════════════
# MARKETPLACE: Пользовательские агенты и скрипты
# ═══════════════════════════════════════════════════════

class UserAgent(Base):
    """
    Пользовательский AI-агент — создаётся и публикуется пользователем в маркетплейсе.
    Автор задаёт личность, инструменты, цену; другие пользователи подписываются и общаются.
    """
    __tablename__ = 'user_agents'

    id = Column(Integer, primary_key=True)
    author_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)

    # Идентификация
    name = Column(String(100), nullable=False)            # "Крипто-аналитик Алекс"
    slug = Column(String(100), unique=True, index=True)   # "crypto-alex" — для @упоминания
    avatar_url = Column(Text)                              # URL или base64 аватарки
    description = Column(Text)                            # Публичное описание (2-4 предложения)
    specialization = Column(String(100))                  # marketing/legal/finance/dev/lifestyle/other

    # Характер и поведение
    personality = Column(Text)                            # System prompt от автора
    tools_allowed = Column(Text)                          # JSON array: ['add_task', 'research_topic', ...]
    knowledge_base = Column(Text)                         # JSON array: [{type, content/url, name}]
    custom_anchors = Column(Text)                         # JSON array: расписание, триггеры

    # Интеграции (зашифрованные ключи)
    integrations = Column(Text)                           # JSON: {service: key_encrypted}

    # Пользовательские API ключи (предоставляются автором агента)
    user_api_keys = Column(Text)                          # plaintext KEY=value lines

    # Python-код, выполняемый агентом перед генерацией ответа (для получения данных)
    python_code = Column(Text)                            # Пользовательский Python-скрипт (stdout → контекст ИИ)

    # Монетизация
    price_per_message = Column(Integer, default=5)        # Токенов за сообщение
    trial_messages = Column(Integer, default=3)           # Бесплатных сообщений для новых
    author_royalty_pct = Column(Integer, default=70)      # % автору (остальное платформе)
    is_adult = Column(Boolean, default=False)             # 18+ контент

    # Статус
    status = Column(String(20), default='draft', index=True)
    # draft → review → active → disabled
    moderation_note = Column(Text)                        # Причина отклонения

    # Статистика
    subscribers_count = Column(Integer, default=0, index=True)
    messages_count = Column(Integer, default=0)
    rating_sum = Column(Integer, default=0)
    rating_count = Column(Integer, default=0)

    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), index=True)
    updated_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc),
                        onupdate=lambda: datetime.datetime.now(datetime.timezone.utc))

    author = relationship("User", foreign_keys=[author_id], backref="created_agents")

    __table_args__ = (
        Index('ix_user_agent_status_subs', 'status', 'subscribers_count'),
    )


class AgentSubscription(Base):
    """Подписка пользователя на конкретного агента."""
    __tablename__ = 'agent_subscriptions'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    agent_id = Column(Integer, ForeignKey('user_agents.id'), nullable=False, index=True)
    trial_messages_used = Column(Integer, default=0)
    messages_count = Column(Integer, default=0)
    tokens_spent = Column(Integer, default=0)
    subscribed_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    last_message_at = Column(DateTime)

    user = relationship("User", backref="agent_subscriptions")
    agent = relationship("UserAgent", backref="subscriptions")

    __table_args__ = (UniqueConstraint('user_id', 'agent_id', name='uq_agent_subscription'),)


class AgentRun(Base):
    """Лог каждого сообщения к пользовательскому агенту (биллинг)."""
    __tablename__ = 'agent_runs'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    agent_id = Column(Integer, ForeignKey('user_agents.id'), nullable=False, index=True)
    tokens_charged = Column(Integer, default=0)   # Всего списано
    author_earnings = Column(Integer, default=0)  # Доля автора
    platform_earnings = Column(Integer, default=0)
    is_trial = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), index=True)

    user = relationship("User", backref="agent_runs")
    agent = relationship("UserAgent", backref="runs")


class AgentRating(Base):
    """Оценка агента конкретным пользователем (1–10)."""
    __tablename__ = 'agent_ratings'

    id = Column(Integer, primary_key=True)
    rater_user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    agent_id = Column(Integer, ForeignKey('user_agents.id'), nullable=False, index=True)
    rating = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    rater = relationship("User", foreign_keys=[rater_user_id])
    agent = relationship("UserAgent", foreign_keys=[agent_id])

    __table_args__ = (
        UniqueConstraint('rater_user_id', 'agent_id', name='uq_agent_rating'),
    )


class UserScript(Base):
    """
    Пользовательский скрипт-модуль — Python-код, запускается в sandbox.
    Может вызываться агентом как инструмент.
    """
    __tablename__ = 'user_scripts'

    id = Column(Integer, primary_key=True)
    author_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)

    name = Column(String(100), nullable=False)
    slug = Column(String(100), unique=True, index=True)
    description = Column(Text)
    category = Column(String(50))              # analytics/content/outreach/finance/misc
    code = Column(Text, nullable=False)        # Python-код скрипта
    input_schema = Column(Text)               # JSON: параметры входа для ИИ
    output_description = Column(Text)         # Что возвращает (для tool description)

    price_per_run = Column(Integer, default=10)    # Токенов за запуск
    author_royalty_pct = Column(Integer, default=70)
    is_adult = Column(Boolean, default=False)

    status = Column(String(20), default='draft', index=True)
    moderation_note = Column(Text)

    installs_count = Column(Integer, default=0, index=True)
    runs_count = Column(Integer, default=0)

    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), index=True)
    updated_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc),
                        onupdate=lambda: datetime.datetime.now(datetime.timezone.utc))

    author = relationship("User", foreign_keys=[author_id], backref="created_scripts")

    __table_args__ = (
        Index('ix_user_script_status_installs', 'status', 'installs_count'),
    )


class ScriptInstall(Base):
    """Установка скрипта пользователем."""
    __tablename__ = 'script_installs'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    script_id = Column(Integer, ForeignKey('user_scripts.id'), nullable=False, index=True)
    runs_count = Column(Integer, default=0)
    tokens_spent = Column(Integer, default=0)
    installed_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))

    user = relationship("User", backref="script_installs")
    script = relationship("UserScript", backref="installs")

    __table_args__ = (UniqueConstraint('user_id', 'script_id', name='uq_script_install'),)


class ScriptRun(Base):
    """Лог каждого запуска скрипта (биллинг)."""
    __tablename__ = 'script_runs'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    script_id = Column(Integer, ForeignKey('user_scripts.id'), nullable=False, index=True)
    params_json = Column(Text)     # Входные параметры
    result_summary = Column(Text)  # Краткий результат (первые 500 символов)
    success = Column(Boolean, default=True)
    error_message = Column(Text)
    tokens_charged = Column(Integer, default=0)
    author_earnings = Column(Integer, default=0)
    platform_earnings = Column(Integer, default=0)
    execution_ms = Column(Integer, default=0)  # Время выполнения
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), index=True)

    user = relationship("User", backref="script_runs")
    script = relationship("UserScript", backref="runs")


class ArenaPost(Base):
    """Пост агента в глобальной арене — сохраняется в БД."""
    __tablename__ = 'arena_posts'

    id = Column(Integer, primary_key=True)
    post_key = Column(String(100), unique=True, index=True)  # e.g. "vera7_1234567890"
    agent_id = Column(String(50), index=True)                # slug агента или "system"
    agent_name = Column(String(100))
    agent_title = Column(String(200))
    color = Column(String(20))
    initials = Column(String(10))
    text = Column(Text, nullable=False)
    ts = Column(String(50))
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc), index=True)


class ArenaComment(Base):
    """Комментарий пользователя + ответ агента к посту в арене."""
    __tablename__ = 'arena_comments'

    id = Column(Integer, primary_key=True)
    post_key = Column(String(100), index=True)   # ссылка на ArenaPost.post_key
    user_text = Column(Text)
    agent_name = Column(String(100))
    agent_title = Column(String(200))
    color = Column(String(20))
    initials = Column(String(10))
    agent_text = Column(Text)
    ts = Column(String(50))
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(datetime.timezone.utc))


# Fix DATABASE_URL for psycopg2 compatibility
db_url = DATABASE_URL
if db_url and db_url.startswith('postgresql://'):
    db_url = db_url.replace('postgresql://', 'postgresql+psycopg2://', 1)

# Import psycopg2 to ensure the driver is available
try:
    import psycopg2
    psycopg2.extensions.register_type(psycopg2.extensions.UNICODE)
    psycopg2.extensions.register_type(psycopg2.extensions.UNICODEARRAY)
except ImportError:
    pass  # psycopg2 not available, perhaps using SQLite

# Connection pool configuration
connect_args = {}
if db_url and db_url.startswith('postgresql'):
    connect_args = {
        "connect_timeout": 30,
        "options": "-c statement_timeout=30000"  # 30 seconds
    }

engine = create_engine(
    db_url,
    pool_size=50,  # 50 permanent connections
    max_overflow=50,  # 50 overflow (max 100 total for 1000 users)
    pool_timeout=15,  # 15s timeout — faster fail under load
    pool_recycle=600,  # Recycle every 10 minutes
    pool_pre_ping=True,
    connect_args=connect_args,
    echo=False  # Disable SQL logging in production
)

def init_db():
    """Initialize database tables. Call this after ensuring DB is accessible."""
    try:
        Base.metadata.create_all(engine)
        logger.info("Database tables created successfully")
    except Exception as e:
        logger.error(f"Failed to create database tables: {e}")
        raise

# Create sessionmaker
Session = sessionmaker(bind=engine)
