"""
AI Marketing Agent - автоматическая генерация маркетингового контента
"""

import logging
import json
from datetime import datetime, timedelta, timezone
from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
import aiohttp
from models import Session, User

logger = logging.getLogger(__name__)


async def generate_marketing_content(product_name, target_audience, platform, goal="привлечение", user_id=None, session=None):
    """
    Генерация маркетингового контента с помощью AI
    
    Args:
        product_name: Название продукта/услуги
        target_audience: Целевая аудитория
        platform: Платформа (telegram, vk, instagram, twitter)
        goal: Цель (привлечение, удержание, продажа)
        user_id: ID пользователя
    
    Returns:
        dict с контентом: title, text, hashtags, cta
    """
    
    logger.info(f"[MARKETING] Generating content for {product_name} on {platform}")
    
    prompt = f"""Создай мощный маркетинговый пост для {platform}.

ПРОДУКТ: {product_name}
АУДИТОРИЯ: {target_audience}
ЦЕЛЬ: {goal}

Требования:
1. Цепляющий заголовок (до 10 слов)
2. Текст 150-200 слов:
   - Начни с боли/проблемы аудитории
   - Покажи решение через продукт
   - Добавь социальное доказательство (цифры если возможно)
   - Закончи сильным CTA
3. 5-7 релевантных хэштегов для {platform}
4. Конкретный призыв к действию

Формат JSON:
{{
    "title": "заголовок",
    "text": "основной текст",
    "hashtags": ["хэштег1", "хэштег2", ...],
    "cta": "призыв к действию",
    "best_time": "лучшее время для публикации"
}}

Пиши на русском, используй эмодзи умеренно."""

    try:
        async with aiohttp.ClientSession() as http_session:
            async with http_session.post(
                'https://api.deepseek.com/chat/completions',
                headers={
                    'Authorization': f'Bearer {DEEPSEEK_API_KEY}',
                    'Content-Type': 'application/json'
                },
                json={
                    "model": DEEPSEEK_MODEL,
                    "messages": [
                        {"role": "system", "content": "Ты профессиональный маркетолог и копирайтер. Создаешь вирусный контент."},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.8,
                    "max_tokens": 1000
                }
            ) as response:
                result = await response.json()
                
                content = result['choices'][0]['message']['content']
                
                # Извлекаем JSON
                try:
                    # Ищем JSON в тексте
                    start = content.find('{')
                    end = content.rfind('}') + 1
                    if start != -1 and end > start:
                        json_str = content[start:end]
                        generated = json.loads(json_str)
                    else:
                        # Fallback: создаем структуру вручную
                        generated = {
                            "title": "Контент сгенерирован",
                            "text": content,
                            "hashtags": ["#маркетинг", "#AI"],
                            "cta": "Попробуйте прямо сейчас!",
                            "best_time": "18:00-20:00"
                        }
                    
                    logger.info(f"[MARKETING] Generated content: {generated['title']}")
                    return {
                        "success": True,
                        "content": generated,
                        "message": f"✅ Создан пост для {platform}:\n\n📌 {generated['title']}\n\n{generated['text'][:150]}...\n\n🏷 Хэштеги: {' '.join(generated['hashtags'][:3])}\n⏰ Лучшее время: {generated.get('best_time', '18:00-20:00')}"
                    }
                    
                except json.JSONDecodeError as e:
                    logger.error(f"[MARKETING] JSON parse error: {e}")
                    return {
                        "success": True,
                        "content": {"title": "Пост создан", "text": content},
                        "message": f"✅ Контент создан:\n\n{content[:200]}..."
                    }
                    
    except Exception as e:
        logger.error(f"[MARKETING] Error generating content: {e}")
        return {
            "success": False,
            "error": str(e),
            "message": f"❌ Ошибка генерации: {str(e)[:100]}"
        }


async def research_topic(query, depth="full", user_id=None, session=None):
    """
    УНИВЕРСАЛЬНЫЙ ПОИСК И АНАЛИЗ
    Использует единый API-клиент с кэшированием и rate-limiting
    """
    from .api_client import get_api_client
    
    logger.info(f"[RESEARCH] Universal analysis for '{query}'")
    
    # Проверяем LTM-кэш для похожих запросов
    if user_id:
        try:
            from .memory import LongTermMemory
            ltm = LongTermMemory(user_id)
            cached_result = ltm.get_cached_search_result(query)
            if cached_result:
                logger.info(f"[RESEARCH] Using LTM cached result for user {user_id}")
                return {
                    "success": True,
                    "cached": True,
                    "analysis": {
                        "summary": cached_result['results'],
                        "key_insights": cached_result['insights']
                    },
                    "message": f"Кэшированные данные по теме {query}: {cached_result['results']}. Выводы: " + ", ".join(cached_result['insights'][:3])
                }
        except Exception as e:
            logger.warning(f"[RESEARCH] LTM cache check failed: {e}")
    
    try:
        api = get_api_client()
        
        # Настройки по depth
        depth_config = {
            'basic': {'num_results': 3, 'max_tokens': 300, 'cache_ttl': 1800},
            'full': {'num_results': 10, 'max_tokens': 600, 'cache_ttl': 3600},
            'deep': {'num_results': 15, 'max_tokens': 1000, 'cache_ttl': 7200},
        }
        config = depth_config.get(depth, depth_config['full'])
        
        # Поиск + AI-анализ через единый клиент
        prompt = f"""Комплексный анализ темы: "{{query}}"

ДАННЫЕ:
{{context}}

Проведи ГЛУБОКИЙ анализ в формате JSON:
{{
    "summary": "чёткое резюме: что это, текущее состояние, главные факты с цифрами",
    "key_insights": ["конкретный вывод 1 с данными", "вывод 2", "вывод 3"],
    "analysis": {{
        "trends": ["актуальный тренд 1 с примером", "тренд 2"],
        "opportunities": ["конкретная возможность 1", "возможность 2"],
        "risks": ["реальный риск 1", "риск 2"]
    }},
    "action_plan": ["конкретный шаг 1", "шаг 2", "шаг 3"],
    "sources": ["Название сайта — URL", "Название 2 — URL"]
}}

ВАЖНО: 
- Извлекай КОНКРЕТНЫЕ данные, цифры, названия, даты из источников. Не пиши общие фразы вроде "растущий рынок" — пиши "рынок $X в 2024, рост Y%".
- В "sources" указывай РЕАЛЬНЫЕ URL-адреса из предоставленных данных."""

        result = await api.search_and_analyze(
            query=query,
            num_results=config['num_results'],
            analysis_prompt=prompt,
            max_tokens=config['max_tokens'],
            cache_ttl=config['cache_ttl']
        )
        
        analysis = result.get('analysis')
        
        # Если анализ — dict, формируем ЧИСТЫЙ текст для AI (без форматирования)
        if isinstance(analysis, dict):
            # Возвращаем PLAIN TEXT — AI сам переработает в живой ответ
            parts = []
            
            if analysis.get('summary'):
                parts.append(analysis['summary'])
            
            if analysis.get('key_insights'):
                insights_text = ", ".join(analysis['key_insights'][:3])
                parts.append(f"Ключевые выводы: {insights_text}")
            
            if analysis.get('opportunities'):
                opps_text = ", ".join(
                    o if isinstance(o, str) else str(o)
                    for o in (analysis.get('opportunities') or [])[:2]
                )
                if opps_text:
                    parts.append(f"Возможности: {opps_text}")
            
            steps = analysis.get('actionable_steps') or analysis.get('action_plan', [])
            if steps:
                steps_text = ", ".join(steps[:3])
                parts.append(f"Рекомендации: {steps_text}")
            
            # Добавляем ссылки на источники из поисковых результатов
            search_results = result.get('results', [])
            if search_results:
                sources = []
                for r in search_results[:5]:
                    title = r.get('title', '')
                    link = r.get('link', '')
                    if link:
                        sources.append(f"{title}: {link}")
                if sources:
                    parts.append("Источники: " + ", ".join(sources))
            
            summary = ". ".join(parts)
            
            result['message'] = summary
        
        # Сохраняем в LTM
        if user_id and analysis:
            try:
                from .memory import LongTermMemory
                ltm = LongTermMemory(user_id)
                results_summary = analysis.get('summary', str(analysis))[:200] if isinstance(analysis, dict) else str(analysis)[:200]
                insights = analysis.get('key_insights', []) if isinstance(analysis, dict) else []
                ltm.save_search_query(query, results_summary, insights)
                logger.info(f"[RESEARCH] Saved search data to LTM for user {user_id}")
            except Exception as e:
                logger.warning(f"[RESEARCH] Failed to save to LTM: {e}")
        
        return result
                    
    except Exception as e:
        logger.error(f"[RESEARCH] Error: {e}")
        return {
            "success": False,
            "error": str(e),
            "message": f"❌ Ошибка: {str(e)[:100]}"
        }


async def publish_to_telegram(content, image_url=None, user_id=None, session=None):
    """
    Публикация контента в Telegram канал пользователя
    
    Args:
        content: Текст для публикации (может быть словарь с title, text, hashtags или просто строка)
        user_id: ID пользователя
        session: DB сессия
    
    Returns:
        dict с результатом публикации
    """
    from models import User
    from config import TELEGRAM_TOKEN
    
    logger.info(f"[PUBLISH] Publishing to Telegram for user {user_id}")
    
    # Получаем telegram_channel из профиля пользователя
    if not session or not user_id:
        return {
            "success": False,
            "error": "Требуется user_id и session",
            "message": "❌ Не указан пользователь для публикации"
        }
    
    # user_id это telegram_id
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        return {
            "success": False,
            "error": "User not found",
            "message": "❌ Пользователь не найден"
        }
    
    if not user.telegram_channel:
        from config import TELEGRAM_BOT_USERNAME
        bot_username = TELEGRAM_BOT_USERNAME.replace('@', '')
        return {
            "success": False,
            "error": "Telegram channel not configured",
            "message": f"""❌ Telegram канал не настроен.

📋 Как настроить:
1. Откройте веб-приложение (Dashboard)
2. Нажмите на свой аватар → Профиль
3. Укажите ID или @username вашего канала
4. Добавьте бота @{bot_username} в канал как администратора
5. Сохраните изменения

После этого можно публиковать посты командой 'опубликуй в канал'"""
        }
    
    # Формируем текст поста
    if isinstance(content, dict):
        # Если передан structured content от generate_marketing_content
        post_text = ""
        if content.get('title'):
            post_text += f"*{content['title']}*\n\n"
        if content.get('text'):
            post_text += content['text'] + "\n\n"
        if content.get('hashtags'):
            post_text += " ".join(content['hashtags']) + "\n\n"
        if content.get('cta'):
            post_text += f"👉 {content['cta']}"
    else:
        # Если передана простая строка
        post_text = content
    
    # Отправляем через Telegram Bot API
    try:
        channel = user.telegram_channel
        # Убедимся что ID канала начинается с @  если это username
        if not channel.startswith('-') and not channel.startswith('@'):
            channel = f"@{channel}"

        async with aiohttp.ClientSession() as http_session:
            if image_url:
                # Публикуем фото с подписью
                tg_method = 'sendPhoto'
                tg_payload = {
                    'chat_id': channel,
                    'photo': image_url,
                    'caption': post_text[:1024],  # Telegram caption limit
                    'parse_mode': 'Markdown'
                }
            else:
                tg_method = 'sendMessage'
                tg_payload = {
                    'chat_id': channel,
                    'text': post_text,
                    'parse_mode': 'Markdown'
                }

            async with http_session.post(
                f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/{tg_method}',
                json=tg_payload
            ) as response:
                result = await response.json()

                if result.get('ok'):
                    logger.info(f"[PUBLISH] Successfully published to {channel}")

                    # Создаем задачу-отчет об успешной публикации
                    if user_id and session:
                        from models import Task
                        img_note = " (с изображением)" if image_url else ""
                        report_task = Task(
                            user_id=user.id,
                            title=f"✅ Пост опубликован в {channel}{img_note}",
                            description=f"Контент:\n{post_text[:200]}...",
                            status='completed',
                            actual_completion_time=datetime.now(timezone.utc)
                        )
                        session.add(report_task)
                        session.commit()

                    return {
                        "success": True,
                        "channel": channel,
                        "message_id": result['result']['message_id'],
                        "message": f"✅ Пост успешно опубликован в {channel}!"
                    }
                else:
                    error_desc = result.get('description', 'Unknown error')
                    logger.error(f"[PUBLISH] Telegram API error: {error_desc}")
                    
                    # Подсказки для частых ошибок
                    from config import TELEGRAM_BOT_USERNAME
                    bot_username = TELEGRAM_BOT_USERNAME.replace('@', '')
                    
                    if 'bot is not a member' in error_desc or 'chat not found' in error_desc:
                        error_desc = f"""Бот не добавлен в канал или не является администратором.

📋 Инструкция:
1. Откройте свой Telegram канал ({channel})
2. Нажмите на название канала → Администраторы
3. Нажмите 'Добавить администратора'
4. Найдите @{bot_username}
5. Дайте права: 'Публикация сообщений'
6. Сохраните

После этого попробуйте снова: 'опубликуй в канал'"""
                    elif 'chat_id' in error_desc:
                        error_desc = f"""Неверный формат ID канала.

✅ Правильные форматы:
- Публичный канал: @your_channel
- Приватный канал: -1001234567890

💡 Как узнать ID приватного канала:
1. Перешлите любое сообщение из канала боту @userinfobot
2. Он покажет ID в формате -100...
3. Укажите этот ID в профиле Dashboard"""
                    
                    return {
                        "success": False,
                        "error": error_desc,
                        "message": f"❌ Не удалось опубликовать: {error_desc}"
                    }
                    
    except Exception as e:
        logger.error(f"[PUBLISH] Error publishing to Telegram: {e}")
        return {
            "success": False,
            "error": str(e),
            "message": f"❌ Ошибка публикации: {str(e)[:100]}"
        }
