"""
MVP: Autonomous Marketing Agent для Premium пользователей

Автоматизирует маркетинг на автопилоте:
1. Анализирует профиль пользователя (продукты, аудитория)
2. Автоматически исследует актуальные темы
3. Генерирует маркетинговый контент
4. Публикует в Telegram канал по расписанию
"""

import asyncio
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import pytz
from models import Session, User, UserProfile
from ai_integration.autonomous_agent import HybridAutonomousAgent
from ai_integration.marketing_agent import research_topic, generate_marketing_content, publish_to_telegram

logger = logging.getLogger(__name__)


class AutonomousMarketingAgentMVP(HybridAutonomousAgent):
    """
    MVP версия агента для автоматического маркетинга
    
    Работа на автопилоте:
    - Анализирует профиль пользователя
    - Исследует актуальные темы в нише
    - Генерирует контент
    - Публикует посты
    """
    
    def __init__(self):
        super().__init__()
        self.specialization = "autonomous_marketing"
        logger.info("[AUTO_MARKETING] Initialized AutonomousMarketingAgentMVP")
    
    async def analyze_user_marketing_profile(self, premium_user_id: int) -> Optional[Dict]:
        """
        Анализирует маркетинговый профиль Premium пользователя
        
        Извлекает:
        - Продукт/услуга
        - Целевая аудитория
        - Платформа (telegram_channel)
        - Темы интересов
        
        Returns:
            Dict: Маркетинговый профиль или None
        """
        
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=premium_user_id).first()
            if not user:
                logger.error(f"[AUTO_MARKETING] User {premium_user_id} not found")
                return None
            
            # Проверяем что Premium
            if not user.subscription_tier or user.subscription_tier.value != 'PREMIUM':
                logger.warning(f"[AUTO_MARKETING] User {premium_user_id} is not Premium")
                return None
            
            # Проверяем настроен ли telegram_channel
            if not user.telegram_channel:
                logger.warning(f"[AUTO_MARKETING] User {premium_user_id} has no telegram_channel configured")
                return None
            
            profile = session.query(UserProfile).filter_by(user_id=user.id).first()
            
            # Используем AI для анализа профиля и извлечения маркетинговой информации
            system_prompt = """Ты агент для автоматизации маркетинга.

Проанализируй информацию о пользователе и извлеки маркетинговый профиль.

Верни JSON:
{
  "product_name": "название продукта/услуги или бизнеса",
  "target_audience": "целевая аудитория (возраст, интересы, боли)",
  "platform": "telegram",
  "niche_keywords": ["5-10 ключевых слов для исследования тем"],
  "content_tone": "деловой|дружелюбный|экспертный",
  "posting_frequency": "1|2|3 (постов в день)",
  "has_enough_data": true/false
}

Если данных недостаточно для маркетинга - верни {"has_enough_data": false}."""

            user_context = f"""Информация о пользователе:
Имя: {user.username or 'не указано'}
Цели: {profile.goals if profile else 'не указаны'}
Интересы: {profile.interests if profile else 'не указаны'}
Навыки: {profile.skills if profile else 'не указаны'}
О себе: {profile.bio if profile else 'не указано'}
Telegram канал: {user.telegram_channel}"""

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_context}
            ]
            
            response = await self.call_ai(messages, use_tools=False, temperature=0.3)
            content = response['choices'][0]['message']['content']
            
            # Парсим JSON
            import json
            if '```json' in content:
                content = content.split('```json')[1].split('```')[0]
            elif '```' in content:
                content = content.split('```')[1].split('```')[0]
            
            marketing_profile = json.loads(content.strip())
            
            if not marketing_profile.get('has_enough_data', False):
                logger.warning(f"[AUTO_MARKETING] Insufficient data for user {premium_user_id}")
                return None
            
            # Добавляем telegram_channel
            marketing_profile['telegram_channel'] = user.telegram_channel
            marketing_profile['user_id'] = premium_user_id
            
            logger.info(f"[AUTO_MARKETING] Marketing profile created for user {premium_user_id}: {marketing_profile.get('product_name')}")
            return marketing_profile
            
        except Exception as e:
            logger.error(f"[AUTO_MARKETING] Error analyzing profile: {e}")
            return None
        finally:
            session.close()
    
    async def find_trending_topics(self, marketing_profile: Dict) -> List[str]:
        """
        Находит актуальные темы для контента
        
        Args:
            marketing_profile: Маркетинговый профиль пользователя
        
        Returns:
            List[str]: Список тем для постов
        """
        
        try:
            niche_keywords = marketing_profile.get('niche_keywords', [])
            if not niche_keywords:
                logger.warning("[AUTO_MARKETING] No niche keywords found")
                return []
            
            # Исследуем первые 3 ключевых слова
            topics = []
            for keyword in niche_keywords[:3]:
                try:
                    # Используем research_topic из marketing_agent
                    research_result = await research_topic(
                        query=keyword,
                        depth='quick',
                        user_id=marketing_profile['user_id'],
                        session=None
                    )
                    
                    if research_result and 'trends' in research_result:
                        topics.extend([f"{keyword}: {trend}" for trend in research_result['trends'][:2]])
                    
                except Exception as e:
                    logger.error(f"[AUTO_MARKETING] Research failed for {keyword}: {e}")
                    continue
            
            logger.info(f"[AUTO_MARKETING] Found {len(topics)} trending topics")
            return topics[:5]  # Максимум 5 тем
            
        except Exception as e:
            logger.error(f"[AUTO_MARKETING] Error finding trending topics: {e}")
            return []
    
    async def generate_and_publish_post(self, marketing_profile: Dict, topic: str) -> bool:
        """
        Генерирует и публикует пост
        
        Args:
            marketing_profile: Маркетинговый профиль
            topic: Тема для поста
        
        Returns:
            bool: True если успешно опубликовано
        """
        
        try:
            user_id = marketing_profile['user_id']
            
            # Генерируем контент
            logger.info(f"[AUTO_MARKETING] Generating content for topic: {topic}")
            content = await generate_marketing_content(
                product_name=marketing_profile['product_name'],
                target_audience=marketing_profile['target_audience'],
                platform='telegram',
                goal=f"пост на тему: {topic}",
                user_id=user_id,
                session=None
            )
            
            if not content or 'text' not in content:
                logger.error("[AUTO_MARKETING] Content generation failed")
                return False
            
            # Форматируем пост
            post_text = f"**{content.get('title', '')}**\n\n{content['text']}\n\n{content.get('hashtags', '')}\n\n{content.get('cta', '')}"
            
            # Публикуем
            logger.info(f"[AUTO_MARKETING] Publishing post to channel")
            result = await publish_to_telegram(
                content=post_text,
                user_id=user_id,
                session=None
            )
            
            if result and result.get('success'):
                logger.info(f"[AUTO_MARKETING] ✅ Post published successfully for user {user_id}")
                return True
            else:
                logger.error(f"[AUTO_MARKETING] Publishing failed: {result}")
                return False
                
        except Exception as e:
            logger.error(f"[AUTO_MARKETING] Error generating/publishing post: {e}")
            return False
    
    async def run_autonomous_marketing_cycle(self, premium_user_id: int) -> Dict:
        """
        Запускает полный цикл автономного маркетинга
        
        Этапы:
        1. Анализ профиля
        2. Поиск актуальных тем
        3. Генерация контента
        4. Публикация
        
        Returns:
            Dict: Отчёт о выполнении
        """
        
        logger.info(f"[AUTO_MARKETING] 🚀 Starting autonomous marketing cycle for user {premium_user_id}")
        
        report = {
            'user_id': premium_user_id,
            'timestamp': datetime.now().isoformat(),
            'status': 'started',
            'posts_published': 0,
            'errors': []
        }
        
        try:
            # 1. Анализируем профиль
            marketing_profile = await self.analyze_user_marketing_profile(premium_user_id)
            if not marketing_profile:
                report['status'] = 'failed'
                report['errors'].append('Failed to analyze marketing profile or insufficient data')
                logger.warning(f"[AUTO_MARKETING] ❌ Cannot proceed without marketing profile")
                return report
            
            # 2. Находим актуальные темы
            topics = await self.find_trending_topics(marketing_profile)
            if not topics:
                # Фоллбек: создаем общий пост о продукте
                topics = [f"Общий пост о {marketing_profile['product_name']}"]
            
            # 3. Генерируем и публикуем посты
            posts_to_create = min(len(topics), int(marketing_profile.get('posting_frequency', 1)))
            
            for i, topic in enumerate(topics[:posts_to_create]):
                logger.info(f"[AUTO_MARKETING] Processing topic {i+1}/{posts_to_create}: {topic}")
                
                success = await self.generate_and_publish_post(marketing_profile, topic)
                
                if success:
                    report['posts_published'] += 1
                else:
                    report['errors'].append(f"Failed to publish post on topic: {topic}")
                
                # Пауза между постами (если несколько)
                if i < posts_to_create - 1:
                    await asyncio.sleep(60)  # 1 минута между постами
            
            # Финальный статус
            if report['posts_published'] > 0:
                report['status'] = 'success'
                logger.info(f"[AUTO_MARKETING] ✅ Cycle completed: {report['posts_published']} posts published")
            else:
                report['status'] = 'failed'
                logger.error(f"[AUTO_MARKETING] ❌ Cycle failed: no posts published")
            
        except Exception as e:
            report['status'] = 'error'
            report['errors'].append(str(e))
            logger.error(f"[AUTO_MARKETING] ❌ Cycle error: {e}")
        
        return report
    
    async def schedule_daily_marketing(self, premium_user_id: int, time_str: str = "09:00") -> bool:
        """
        Планирует ежедневный автоматический маркетинг
        
        Args:
            premium_user_id: ID Premium пользователя
            time_str: Время запуска (HH:MM)
        
        Returns:
            bool: True если запланировано успешно
        """
        
        # TODO: Интеграция с reminder_service или отдельный scheduler
        # Пока заглушка для будущей интеграции
        logger.info(f"[AUTO_MARKETING] Scheduling daily marketing for user {premium_user_id} at {time_str}")
        return True


async def test_autonomous_marketing():
    """Тест автономного маркетинга"""
    agent = AutonomousMarketingAgentMVP()
    
    # Замените на реальный Premium user_id для теста
    test_user_id = 123456789  
    
    report = await agent.run_autonomous_marketing_cycle(test_user_id)
    
    print("\n" + "="*60)
    print("ОТЧЁТ ОБ АВТОНОМНОМ МАРКЕТИНГЕ")
    print("="*60)
    print(f"User ID: {report['user_id']}")
    print(f"Статус: {report['status']}")
    print(f"Опубликовано постов: {report['posts_published']}")
    if report['errors']:
        print(f"Ошибки: {', '.join(report['errors'])}")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(test_autonomous_marketing())
