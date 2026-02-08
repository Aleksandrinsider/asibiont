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
                    
                    # Сохраняем в БД если нужно
                    if user_id and session:
                        from models import User, Task
                        user = session.query(User).filter_by(telegram_id=user_id).first()
                        if user:
                            # Создаем задачу на публикацию
                            task = Task(
                                user_id=user.id,
                                title=f"Опубликовать пост: {generated['title'][:50]}",
                                description=f"Platform: {platform}\n\n{generated['text'][:200]}...",
                                status='pending',
                                created_at=datetime.now(timezone.utc)
                            )
                            session.add(task)
                            session.commit()
                            logger.info(f"[MARKETING] Created task for posting")
                    
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


async def create_content_calendar(goal, duration_days=7, niche="стартапы", user_id=None, session=None):
    """
    Создание контент-календаря на N дней
    
    Args:
        goal: Цель контента (привлечение клиентов, продажи, бренд)
        duration_days: Количество дней
        niche: Ниша/тематика
        user_id: ID пользователя
    
    Returns:
        dict с календарем постов
    """
    
    logger.info(f"[CALENDAR] Creating {duration_days}-day calendar for {niche}")
    
    prompt = f"""Создай контент-план на {duration_days} дней для {niche}.

ЦЕЛЬ: {goal}
НИША: {niche}

Для каждого дня создай:
1. Тему поста
2. Тип контента (образовательный/кейс/продающий/развлекательный)
3. Ключевое сообщение
4. Рекомендуемую платформу
5. Лучшее время публикации

Формат JSON:
{{
    "calendar": [
        {{
            "day": 1,
            "date": "описание даты",
            "topic": "тема",
            "type": "тип",
            "message": "ключевое сообщение", 
            "platform": "telegram/vk/instagram",
            "time": "16:00"
        }},
        ...
    ],
    "strategy": "общая стратегия на период"
}}

Баланс типов контента: 40% образовательный, 30% кейсы, 20% продающий, 10% развлекательный."""

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
                        {"role": "system", "content": "Ты контент-стратег. Создаешь продуманные контент-планы."},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.7,
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
                        calendar_data = json.loads(content[start:end])
                    else:
                        calendar_data = {"calendar": [], "strategy": content}
                    
                    # Создаем задачи в календаре пользователя
                    if user_id and session and calendar_data.get('calendar'):
                        from models import User, Task
                        user = session.query(User).filter_by(telegram_id=user_id).first()
                        if user:
                            base_date = datetime.now(timezone.utc)
                            for item in calendar_data['calendar'][:5]:  # Максимум 5 задач
                                task_date = base_date + timedelta(days=item.get('day', 1) - 1)
                                task = Task(
                                    user_id=user.id,
                                    title=f"Пост #{item.get('day')}: {item.get('topic', 'Контент')[:50]}",
                                    description=f"Тип: {item.get('type')}\nСообщение: {item.get('message', '')}\nПлатформа: {item.get('platform')}",
                                    reminder_time=task_date.replace(hour=int(item.get('time', '16:00').split(':')[0]), minute=0),
                                    status='pending',
                                    created_at=datetime.now(timezone.utc)
                                )
                                session.add(task)
                            session.commit()
                            logger.info(f"[CALENDAR] Created {len(calendar_data['calendar'][:5])} tasks")
                    
                    summary = f"✅ Создан контент-план на {duration_days} дней:\n\n"
                    summary += f"📋 Стратегия: {calendar_data.get('strategy', 'Контент-микс')[:150]}...\n\n"
                    summary += f"📅 Запланировано постов: {len(calendar_data.get('calendar', []))}\n"
                    
                    if calendar_data.get('calendar'):
                        summary += "\nПервые 3 поста:\n"
                        for item in calendar_data['calendar'][:3]:
                            summary += f"▫️ День {item.get('day')}: {item.get('topic')} ({item.get('type')})\n"
                    
                    return {
                        "success": True,
                        "calendar": calendar_data,
                        "message": summary
                    }
                    
                except json.JSONDecodeError:
                    return {
                        "success": True,
                        "calendar": {"strategy": content},
                        "message": f"✅ План создан:\n\n{content[:300]}..."
                    }
                    
    except Exception as e:
        logger.error(f"[CALENDAR] Error: {e}")
        return {
            "success": False,
            "error": str(e),
            "message": f"❌ Ошибка: {str(e)[:100]}"
        }


async def suggest_growth_hacks(niche, current_users=0, goal_users=100, user_id=None, session=None):
    """
    AI генерирует конкретные growth hacks для привлечения
    
    Args:
        niche: Ниша/область
        current_users: Текущее количество пользователей
        goal_users: Целевое количество
        user_id: ID пользователя
    
    Returns:
        dict с growth hacks
    """
    
    logger.info(f"[GROWTH] Generating hacks for {niche}: {current_users} -> {goal_users}")
    
    prompt = f"""Ты эксперт по growth hacking. Предложи 5 КОНКРЕТНЫХ стратегий для привлечения пользователей.

НИША: {niche}
СЕЙЧАС: {current_users} пользователей
ЦЕЛЬ: {goal_users} пользователей

Для каждой стратегии укажи:
1. Название стратегии
2. Описание (2-3 предложения)
3. Конкретные шаги (3-5 пунктов)
4. Ожидаемый эффект (реалистично)
5. Сложность (низкая/средняя/высокая)
6. Стоимость (бесплатно/дешево/дорого)

Формат JSON:
{{
    "hacks": [
        {{
            "name": "название",
            "description": "описание",
            "steps": ["шаг 1", "шаг 2", ...],
            "expected_effect": "+X пользователей",
            "difficulty": "низкая",
            "cost": "бесплатно"
        }},
        ...
    ],
    "priority": "с чего начать в первую очередь"
}}

Фокус на БЫСТРЫХ и ИЗМЕРИМЫХ результатах."""

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
                        {"role": "system", "content": "Ты growth hacker с опытом масштабирования стартапов 0->1000 пользователей."},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.7,
                    "max_tokens": 2000
                }
            ) as response:
                result = await response.json()
                content = result['choices'][0]['message']['content']
                
                try:
                    start = content.find('{')
                    end = content.rfind('}') + 1
                    if start != -1 and end > start:
                        hacks_data = json.loads(content[start:end])
                    else:
                        hacks_data = {"hacks": [], "priority": content}
                    
                    # Создаем задачи для топ-3 хаков
                    if user_id and session and hacks_data.get('hacks'):
                        from models import User, Task
                        user = session.query(User).filter_by(telegram_id=user_id).first()
                        if user:
                            for i, hack in enumerate(hacks_data['hacks'][:3], 1):
                                steps_text = "\n".join([f"{j}. {step}" for j, step in enumerate(hack.get('steps', [])[:5], 1)])
                                task = Task(
                                    user_id=user.id,
                                    title=f"Growth Hack #{i}: {hack.get('name', 'Стратегия')[:50]}",
                                    description=f"{hack.get('description', '')}\n\nШаги:\n{steps_text}\n\nЭффект: {hack.get('expected_effect')}\nСложность: {hack.get('difficulty')}",
                                    status='pending',
                                    created_at=datetime.now(timezone.utc)
                                )
                                session.add(task)
                            session.commit()
                            logger.info(f"[GROWTH] Created {min(3, len(hacks_data['hacks']))} growth tasks")
                    
                    summary = f"🚀 Growth Hacks для {niche}:\n\n"
                    summary += f"📊 Цель: {current_users} → {goal_users} пользователей\n\n"
                    
                    if hacks_data.get('hacks'):
                        summary += "Топ-3 стратегии:\n\n"
                        for i, hack in enumerate(hacks_data['hacks'][:3], 1):
                            summary += f"{i}. **{hack.get('name')}**\n"
                            summary += f"   {hack.get('description', '')[:100]}...\n"
                            summary += f"   💰 {hack.get('cost')} | ⚡ {hack.get('difficulty')} | 📈 {hack.get('expected_effect')}\n\n"
                    
                    if hacks_data.get('priority'):
                        summary += f"\n💡 Приоритет: {hacks_data['priority'][:150]}..."
                    
                    return {
                        "success": True,
                        "hacks": hacks_data,
                        "message": summary
                    }
                    
                except json.JSONDecodeError:
                    return {
                        "success": True,
                        "hacks": {"priority": content},
                        "message": f"🚀 Стратегии роста:\n\n{content[:400]}..."
                    }
                    
    except Exception as e:
        logger.error(f"[GROWTH] Error: {e}")
        return {
            "success": False,
            "error": str(e),
            "message": f"❌ Ошибка: {str(e)[:100]}"
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
                        
                        # Создаем задачи для топ-3 actionable steps
                        if user_id and session and analysis.get('actionable_steps'):
                            from models import User, Task
                            user = session.query(User).filter_by(telegram_id=user_id).first()
                            if user:
                                for i, step in enumerate(analysis['actionable_steps'][:3], 1):
                                    task = Task(
                                        user_id=user.id,
                                        title=f"Исследование: {step[:50]}",
                                        description=f"По теме: {query}\n\n{step}",
                                        status='pending',
                                        created_at=datetime.now(timezone.utc)
                                    )
                                    session.add(task)
                                session.commit()
                                logger.info(f"[RESEARCH] Created {min(3, len(analysis['actionable_steps']))} tasks")
                        
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
        return {
            "success": False,
            "error": "Telegram channel not configured",
            "message": "❌ Telegram канал не настроен в профиле. Укажите ID или @username канала в настройках."
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
                    if 'bot is not a member' in error_desc or 'chat not found' in error_desc:
                        error_desc = "Бот не является админом канала. Добавьте бота в канал и сделайте его администратором."
                    elif 'chat_id' in error_desc:
                        error_desc = "Неверный ID канала. Используйте формат @channel или -1001234567890"
                    
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
