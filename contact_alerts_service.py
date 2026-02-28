#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Contact Online Alerts Service для всех пользователей

Активно мониторит онлайн-статус контактов и отправляет алерты типа "@dima онлайн"
Запускается как фоновый процесс вместе с ботом.
Доступен на всех тарифах (LIGHT, STANDARD, PREMIUM).
"""

import asyncio
import logging
from datetime import datetime, timedelta
import pytz
from models import Session, User, UserProfile, ContactAlert

logger = logging.getLogger(__name__)


class ContactAlertsService:
    """
    Сервис для активных алертов о контактах онлайн (все тарифы)

    Работа:
    - Проверяет пользователей всех тарифов каждые 30 минут
    - Для каждого contact alert проверяет онлайн-статус контакта
    - Отправляет уведомление "@username онлайн" если контакт активен
    """

    def __init__(self, bot=None, check_interval_minutes=30):
        """
        Args:
            bot: Telegram bot instance для отправки уведомлений
            check_interval_minutes: Интервал проверки в минутах
        """
        self.bot = bot
        self.check_interval_minutes = check_interval_minutes
        self.running = False
        logger.info(f"[CONTACT_ALERTS] Initialized with interval {check_interval_minutes}min")

    async def get_active_contact_alerts(self):
        """
        Получает активные contact alerts Premium пользователей

        Returns:
            List[dict]: Список алертов с информацией о пользователе и критериях поиска
        """
        session = Session()
        try:
            # Получаем пользователей с активными contact alerts и положительным балансом
            alerts = session.query(ContactAlert).join(User).filter(
                ContactAlert.enabled == True,
                User.token_balance > 0
            ).all()

            result = []
            for alert in alerts:
                result.append({
                    'alert_id': alert.id,
                    'user_id': alert.user_id,
                    'telegram_id': alert.user.telegram_id,
                    'skill': alert.skill,
                    'interest': alert.interest,
                    'city': alert.city,
                    'position': alert.position,
                    'last_notified': alert.last_triggered_at
                })

            logger.info(f"[CONTACT_ALERTS] Found {len(result)} active contact alerts")
            return result

        except Exception as e:
            logger.error(f"[CONTACT_ALERTS] Error getting active alerts: {e}")
            return []
        finally:
            session.close()

    async def find_matching_online_contacts(self, alert_criteria):
        """
        Ищет контакты, соответствующие критериям и активные в последние 24 часа

        Args:
            alert_criteria: dict с критериями поиска

        Returns:
            List[dict]: Список найденных контактов
        """
        session = Session()
        try:
            # Ищем профили, соответствующие критериям
            query = session.query(UserProfile).join(User).filter(
                UserProfile.user_id != alert_criteria['user_id'],  # Не сам пользователь
                User.telegram_id.isnot(None)  # Есть telegram ID
            )

            # Фильтры по критериям
            if alert_criteria.get('skill'):
                skill = alert_criteria['skill'].lower()
                query = query.filter(UserProfile.skills.ilike(f'%{skill}%'))

            if alert_criteria.get('interest'):
                interest = alert_criteria['interest'].lower()
                query = query.filter(UserProfile.interests.ilike(f'%{interest}%'))

            if alert_criteria.get('city'):
                city = alert_criteria['city'].lower()
                query = query.filter(UserProfile.city.ilike(f'%{city}%'))

            if alert_criteria.get('position'):
                position = alert_criteria['position'].lower()
                query = query.filter(UserProfile.position.ilike(f'%{position}%'))

            # Только активные пользователи (были онлайн в последние 24 часа)
            yesterday = datetime.now(pytz.UTC) - timedelta(days=1)
            query = query.filter(User.last_interaction_at >= yesterday)

            profiles = query.limit(5).all()  # Максимум 5 контактов за раз

            result = []
            for profile in profiles:
                user = profile.user
                if user.username:  # Только если есть username
                    result.append({
                        'username': user.username,
                        'telegram_id': user.telegram_id,
                        'last_seen': user.last_interaction_at,
                        'city': profile.city,
                        'position': profile.position,
                        'skills': profile.skills
                    })

            return result

        except Exception as e:
            logger.error(f"[CONTACT_ALERTS] Error finding matching contacts: {e}")
            return []
        finally:
            session.close()

    async def send_contact_alert(self, user_telegram_id, contact_username, contact_info):
        """
        Отправляет алерт о контакте онлайн

        Args:
            user_telegram_id: ID пользователя для уведомления
            contact_username: Username контакта
            contact_info: Дополнительная информация о контакте
        """
        if not self.bot:
            logger.warning("[CONTACT_ALERTS] No bot instance, cannot send alert")
            return

        try:
            from i18n import get_user_lang
            lang = get_user_lang(user_telegram_id)

            # Получаем время последнего взаимодействия
            last_seen_hours = 0
            if contact_info.get('last_seen'):
                now = datetime.now(pytz.UTC)
                last_seen = contact_info['last_seen']
                if isinstance(last_seen, str):
                    last_seen = datetime.fromisoformat(last_seen.replace('Z', '+00:00'))
                last_seen_hours = int((now - last_seen).total_seconds() / 3600)

            # Формируем сообщение
            if lang == 'en':
                message = f"🔥 @{contact_username} is online"
                if last_seen_hours < 1:
                    message += " (active now)"
                elif last_seen_hours < 24:
                    message += f" (was {last_seen_hours}h ago)"

                profile_parts = []
                if contact_info.get('position'):
                    profile_parts.append(f"position: {contact_info['position']}")
                if contact_info.get('city'):
                    profile_parts.append(f"city: {contact_info['city']}")

                if profile_parts:
                    message += f"\n📋 {' • '.join(profile_parts)}"
                message += "\n\n💬 You can message them right now!"
            else:
                message = f"🔥 @{contact_username} онлайн"
                if last_seen_hours < 1:
                    message += " (активен сейчас)"
                elif last_seen_hours < 24:
                    message += f" (был {last_seen_hours}ч назад)"

                profile_parts = []
                if contact_info.get('position'):
                    profile_parts.append(f"должность: {contact_info['position']}")
                if contact_info.get('city'):
                    profile_parts.append(f"город: {contact_info['city']}")

                if profile_parts:
                    message += f"\n📋 {' • '.join(profile_parts)}"
                message += "\n\n💬 Можно написать прямо сейчас!"

            await self.bot.send_message(user_telegram_id, message)
            logger.info(f"[CONTACT_ALERTS] Sent alert to {user_telegram_id}: {message[:100]}...")

        except Exception as e:
            logger.error(f"[CONTACT_ALERTS] Failed to send alert to {user_telegram_id}: {e}")

    async def update_alert_timestamp(self, alert_id):
        """
        Обновляет timestamp последнего уведомления для алерта

        Args:
            alert_id: ID алерта
        """
        session = Session()
        try:
            alert = session.query(ContactAlert).filter_by(id=alert_id).first()
            if alert:
                alert.last_triggered_at = datetime.now(pytz.UTC)
                session.commit()
                logger.info(f"[CONTACT_ALERTS] Updated last_triggered_at for alert {alert_id}")
        except Exception as e:
            logger.error(f"[CONTACT_ALERTS] Error updating alert timestamp: {e}")
            session.rollback()
        finally:
            session.close()

    async def run_alerts_cycle(self):
        """
        Основной цикл: проверяет алерты и отправляет уведомления
        """
        try:
            logger.info("[CONTACT_ALERTS] 🚀 Starting alerts cycle")

            # Получаем активные алерты
            active_alerts = await self.get_active_contact_alerts()

            if not active_alerts:
                logger.info("[CONTACT_ALERTS] No active contact alerts")
                return

            # Обрабатываем каждый алерт
            alerts_sent = 0
            for alert in active_alerts:
                try:
                    # Проверяем, не отправляли ли уведомление слишком recently (минимум 4 часа)
                    if alert.get('last_notified'):
                        hours_since_last = (datetime.now(pytz.UTC) - alert['last_notified']).total_seconds() / 3600
                        if hours_since_last < 4:  # Не чаще чем раз в 4 часа
                            continue

                    # Ищем подходящие контакты онлайн
                    matching_contacts = await self.find_matching_online_contacts(alert)

                    if matching_contacts:
                        # Отправляем алерты (максимум 2 за цикл для одного пользователя)
                        sent_for_user = 0
                        for contact in matching_contacts[:2]:
                            if sent_for_user >= 2:
                                break

                            await self.send_contact_alert(
                                alert['telegram_id'],
                                contact['username'],
                                contact
                            )
                            sent_for_user += 1
                            alerts_sent += 1

                        # Обновляем timestamp алерта
                        if sent_for_user > 0:
                            await self.update_alert_timestamp(alert['alert_id'])

                    # Пауза между алертами
                    await asyncio.sleep(5)

                except Exception as e:
                    logger.error(f"[CONTACT_ALERTS] Error processing alert {alert.get('alert_id')}: {e}")
                    continue

            logger.info(f"[CONTACT_ALERTS] ✅ Cycle completed: {alerts_sent} alerts sent")

        except Exception as e:
            logger.error(f"[CONTACT_ALERTS] Cycle error: {e}")

    async def schedule_loop(self):
        """
        Бесконечный цикл с периодическими проверками
        """
        self.running = True
        logger.info(f"[CONTACT_ALERTS] 🔄 Started scheduling loop (every {self.check_interval_minutes}min)")

        while self.running:
            try:
                # Запускаем цикл алертов
                await self.run_alerts_cycle()

                # Ждём до следующей проверки
                logger.info(f"[CONTACT_ALERTS] 😴 Sleeping for {self.check_interval_minutes}min until next cycle")
                await asyncio.sleep(self.check_interval_minutes * 60)

            except Exception as e:
                logger.error(f"[CONTACT_ALERTS] Loop error: {e}")
                await asyncio.sleep(300)  # Ждём 5 минут при ошибке

    async def stop(self):
        """
        Останавливает сервис
        """
        logger.info("[CONTACT_ALERTS] Stopping service")
        self.running = False

    async def start(self):
        """
        Запускает сервис в фоне
        """
        logger.info("[CONTACT_ALERTS] Starting service")
        await self.schedule_loop()


# Глобальный экземпляр сервиса
_contact_alerts_service = None


async def test_service():
    """
    Тест сервиса (запускает один цикл)
    """
    service = ContactAlertsService(check_interval_minutes=30)
    await service.run_alerts_cycle()


if __name__ == "__main__":
    print("🧪 Testing Contact Alerts Service...")
    asyncio.run(test_service())