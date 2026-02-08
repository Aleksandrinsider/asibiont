"""
AI Marketing Agent - автоматическая генерация маркетингового контента
"""

import logging
import json
from datetime import datetime, timedelta, timezone
from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
import aiohttp

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


async def research_topic(query, depth="balanced", user_id=None, session=None):
    """
    Глубокий анализ темы через веб-поиск + AI
    
    Args:
        query: Тема для исследования
        depth: Глубина анализа ("quick", "balanced", "deep")
        user_id: ID пользователя
    
    Returns:
        dict с анализом рынка/темы
    """
    from config import SERPER_API_KEY
    
    logger.info(f"[RESEARCH] Analyzing '{query}' with depth={depth}")
    
    # Определяем количество результатов
    num_results = {"quick": 5, "balanced": 10, "deep": 15}.get(depth, 10)
    
    try:
        # Шаг 1: Веб-поиск через Serper
        search_results = []
        
        if SERPER_API_KEY:
            try:
                async with aiohttp.ClientSession() as http_session:
                    async with http_session.post(
                        'https://google.serper.dev/search',
                        headers={
                            'X-API-KEY': SERPER_API_KEY,
                            'Content-Type': 'application/json'
                        },
                        json={
                            "q": query,
                            "num": num_results,
                            "gl": "ru",  # Russian results
                            "hl": "ru"
                        }
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            
                            # Извлекаем результаты
                            for item in data.get('organic', [])[:num_results]:
                                search_results.append({
                                    "title": item.get('title'),
                                    "snippet": item.get('snippet'),
                                    "link": item.get('link')
                                })
                            
                            logger.info(f"[RESEARCH] Found {len(search_results)} results")
                        else:
                            logger.warning(f"[RESEARCH] Serper API error: {response.status}")
                            
            except Exception as e:
                logger.error(f"[RESEARCH] Serper error: {e}")
        
        # Шаг 2: AI анализ результатов
        if search_results:
            # Формируем контекст для AI
            context = "\n\n".join([
                f"**{r['title']}**\n{r['snippet']}\nИсточник: {r['link']}"
                for r in search_results[:10]
            ])
            
            prompt = f"""Проанализируй информацию по теме: "{query}"

ДАННЫЕ ИЗ ПОИСКА:
{context}

Создай детальный анализ в формате JSON:
{{
    "summary": "краткое резюме (2-3 предложения)",
    "key_insights": ["инсайт 1", "инсайт 2", "инсайт 3"],
    "opportunities": ["возможность 1", "возможность 2"],
    "competitors": ["конкурент 1", "конкурент 2"] (если найдены),
    "trends": ["тренд 1", "тренд 2"],
    "actionable_steps": ["шаг 1", "шаг 2", "шаг 3"],
    "sources": ["главный источник 1", "главный источник 2"]
}}

Фокус: ПРАКТИЧЕСКИЕ выводы и КОНКРЕТНЫЕ рекомендации."""
            
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
                            {"role": "system", "content": "Ты эксперт по market research и competitive intelligence."},
                            {"role": "user", "content": prompt}
                        ],
                        "temperature": 0.5,
                        "max_tokens": 2000
                    }
                ) as response:
                    result = await response.json()
                    content = result['choices'][0]['message']['content']
                    
                    # Парсим JSON
                    try:
                        start = content.find('{')
                        end = content.rfind('}') + 1
                        if start != -1 and end > start:
                            analysis = json.loads(content[start:end])
                        else:
                            analysis = {"summary": content}
                        
                        # Формируем ответ
                        summary = f"🔍 Анализ по теме: {query}\n\n"
                        
                        if analysis.get('summary'):
                            summary += f"📊 Резюме:\n{analysis['summary']}\n\n"
                        
                        if analysis.get('key_insights'):
                            summary += f"💡 Ключевые инсайты:\n"
                            for insight in analysis['key_insights'][:3]:
                                summary += f"• {insight}\n"
                            summary += "\n"
                        
                        if analysis.get('opportunities'):
                            summary += f"🎯 Возможности:\n"
                            for opp in analysis['opportunities'][:2]:
                                summary += f"• {opp}\n"
                            summary += "\n"
                        
                        if analysis.get('actionable_steps'):
                            summary += f"✅ Рекомендации:\n"
                            for i, step in enumerate(analysis['actionable_steps'][:3], 1):
                                summary += f"{i}. {step}\n"
                        
                        return {
                            "success": True,
                            "analysis": analysis,
                            "sources": search_results[:5],
                            "message": summary
                        }
                        
                    except json.JSONDecodeError:
                        return {
                            "success": True,
                            "analysis": {"summary": content},
                            "sources": search_results[:5],
                            "message": f"🔍 Анализ:\n\n{content[:500]}..."
                        }
        else:
            # Fallback: только AI без веб-поиска
            prompt = f"""Проанализируй тему: "{query}"

Создай краткий анализ на основе твоих знаний:
- Общий обзор
- Ключевые моменты
- Рекомендации (3 шага)

Формат: структурированный текст."""
            
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
                            {"role": "system", "content": "Ты эксперт-аналитик."},
                            {"role": "user", "content": prompt}
                        ],
                        "temperature": 0.6,
                        "max_tokens": 1000
                    }
                ) as response:
                    result = await response.json()
                    content = result['choices'][0]['message']['content']
                    
                    return {
                        "success": True,
                        "analysis": {"summary": content},
                        "sources": [],
                        "message": f"🔍 Анализ (базовые знания):\n\n{content[:500]}...\n\n⚠️ Для глубокого анализа нужен доступ к веб-поиску"
                    }
                    
    except Exception as e:
        logger.error(f"[RESEARCH] Error: {e}")
        return {
            "success": False,
            "error": str(e),
            "message": f"❌ Ошибка: {str(e)[:100]}"
        }


async def publish_to_telegram(content, user_id=None, session=None):
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
    
    user = session.query(User).filter_by(id=user_id).first()
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
            async with http_session.post(
                f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage',
                json={
                    'chat_id': channel,
                    'text': post_text,
                    'parse_mode': 'Markdown'
                }
            ) as response:
                result = await response.json()
                
                if result.get('ok'):
                    logger.info(f"[PUBLISH] Successfully published to {channel}")
                    
                    # Создаем задачу-отчет об успешной публикации
                    if user_id and session:
                        from models import Task
                        report_task = Task(
                            user_id=user_id,
                            title=f"✅ Пост опубликован в {channel}",
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
