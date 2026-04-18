"""
AI Marketing Agent - автоматическая генерация маркетингового контента
"""

import asyncio
import logging
import json
from datetime import datetime, timedelta, timezone
from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL
import aiohttp
from ai_integration.utils import _safe_http
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

    # DDG: исследуем конкурентов и боли аудитории для data-driven контента
    competitor_ctx = ""
    pain_ctx = ""
    trend_ctx = ""
    try:
        from .api_client import get_api_client
        api = get_api_client()

        import asyncio as _aio_mg
        # Параллельные DDG-запросы — экономим ~5-10 сек vs последовательных
        _q1 = api.duckduckgo_search(f'{product_name} альтернативы конкуренты отзывы', num=3, cache_ttl=7200)
        _q2 = api.duckduckgo_search(f'{target_audience} проблемы жалобы сложности', num=3, cache_ttl=7200)
        _q3 = api.duckduckgo_search(f'{target_audience} тренды {datetime.now().strftime("%Y")}', num=3, cache_ttl=7200)
        try:
            competitors, pains, trends = await _aio_mg.wait_for(
                _aio_mg.gather(_q1, _q2, _q3, return_exceptions=True),
                timeout=10.0,
            )
        except _aio_mg.TimeoutError:
            competitors = pains = trends = None
        if isinstance(competitors, Exception):
            competitors = None
        if isinstance(pains, Exception):
            pains = None
        if isinstance(trends, Exception):
            trends = None
        # 1. Конкуренты/альтернативы
        if competitors:
            lines = [f"- {r.get('title', '')}: {r.get('snippet', '')[:120]}" for r in competitors[:3]]
            competitor_ctx = "\n\nКОНКУРЕНТЫ И РЫНОК (реальные данные из сети):\n" + "\n".join(lines)
        # 2. Боли аудитории
        if pains:
            lines = [f"- {r.get('title', '')}: {r.get('snippet', '')[:120]}" for r in pains[:3]]
            pain_ctx = "\n\nБОЛИ АУДИТОРИИ (реальные данные из сети):\n" + "\n".join(lines)
        # 3. Актуальные тренды
        if trends:
            lines = [f"- {r.get('title', '')}: {r.get('snippet', '')[:120]}" for r in trends[:3]]
            trend_ctx = "\n\nАКТУАЛЬНЫЕ ТРЕНДЫ:\n" + "\n".join(lines)

    except Exception as e:
        logger.debug(f"[MARKETING] DDG research failed (non-critical): {e}")

    prompt = f"""Создай мощный маркетинговый пост для {platform}.

ПРОДУКТ: {product_name}
АУДИТОРИЯ: {target_audience}
ЦЕЛЬ: {goal}
{competitor_ctx}{pain_ctx}{trend_ctx}

Требования:
1. Цепляющий заголовок (до 10 слов)
2. Текст 150-200 слов:
   - Начни с боли/проблемы аудитории (используй РЕАЛЬНЫЕ данные из раздела "БОЛИ АУДИТОРИИ" выше)
   - Покажи решение через продукт
   - Отстройся от конкурентов (используй данные из раздела "КОНКУРЕНТЫ")
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
        async with _safe_http() as http_session:
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
                            "title": "Новый пост",
                            "text": content,
                            "hashtags": ["#маркетинг", "#AI"],
                            "cta": "Попробуй — это работает",
                            "best_time": "18:00-20:00"
                        }
                    
                    logger.info(f"[MARKETING] Generated content: {generated['title']}")
                    return {
                        "success": True,
                        "content": generated,
                        "message": f"Создан пост для {platform}:\n\n{generated['title']}\n\n{generated['text'][:150]}...\n\nХэштеги: {' '.join(generated['hashtags'][:3])}\nЛучшее время: {generated.get('best_time', '18:00-20:00')}"
                    }
                    
                except json.JSONDecodeError as e:
                    logger.error(f"[MARKETING] JSON parse error: {e}")
                    return {
                        "success": True,
                        "content": {"title": "Новый пост", "text": content},
                        "message": f"Пост готов:\n\n{content[:200]}..."
                    }
                    
    except Exception as e:
        logger.error(f"[MARKETING] Error generating content: {e}")
        return {
            "success": False,
            "error": str(e),
            "message": f"Не получилось сгенерировать контент: {str(e)[:100]}"
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
            'full': {'num_results': 5, 'max_tokens': 600, 'cache_ttl': 3600},
            'deep': {'num_results': 10, 'max_tokens': 1000, 'cache_ttl': 7200},
        }
        config = depth_config.get(depth, depth_config['full'])
        
        # Загружаем профиль пользователя для адаптивного контекста
        _user_context_str = ""
        if user_id:
            try:
                from models import User as _RU, UserProfile as _RUP
                _r_session = session
                _r_close = False
                if _r_session is None:
                    from models import Session as _RS
                    _r_session = _RS()
                    _r_close = True
                _r_user = _r_session.query(_RU).filter_by(telegram_id=user_id).first()
                if _r_user:
                    _r_profile = _r_session.query(_RUP).filter_by(user_id=_r_user.id).first()
                    if _r_profile:
                        _ctx_parts = []
                        if _r_profile.goals:
                            _ctx_parts.append(f"Цели: {_r_profile.goals[:150]}")
                        if _r_profile.interests:
                            _ctx_parts.append(f"Интересы: {_r_profile.interests[:150]}")
                        if _r_profile.skills:
                            _ctx_parts.append(f"Навыки: {_r_profile.skills[:100]}")
                        if _r_profile.bio:
                            _ctx_parts.append(f"Профиль: {_r_profile.bio[:100]}")
                        if _ctx_parts:
                            _user_context_str = "\nКОНТЕКСТ ПОЛЬЗОВАТЕЛЯ: " + "; ".join(_ctx_parts) + "\nУчитывай цели и интересы пользователя при анализе — делай выводы релевантными для него.\n"
                if _r_close:
                    _r_session.close()
            except Exception as _uce:
                logger.warning(f"[RESEARCH] Failed to load user context: {_uce}")

        # Поиск + AI-анализ через единый клиент
        prompt = f"""Комплексный анализ темы: "{{query}}"
{_user_context_str}
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

        _research_timeout = 20.0 if depth == 'basic' else 30.0
        try:
            result = await asyncio.wait_for(
                api.search_and_analyze(
                    query=query,
                    num_results=config['num_results'],
                    analysis_prompt=prompt,
                    max_tokens=config['max_tokens'],
                    cache_ttl=config['cache_ttl']
                ),
                timeout=_research_timeout
            )
        except asyncio.TimeoutError:
            logger.warning(f"[RESEARCH] search_and_analyze timeout ({_research_timeout}s) for '{query}'")
            return {
                'success': False,
                'error': 'timeout',
                'message': f'Исследование по теме «{query}» занимает слишком долго. Попробуй использовать quick_topic_search для быстрого ответа.',
            }
        
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
            
            summary = ". ".join(parts)

            # Убираем прямые URL из текста (DuckDuckGo иногда даёт нерелевантные ссылки)
            import re as _re
            summary = _re.sub(r'https?://[^\s,;]+', '', summary).strip()
            summary = _re.sub(r'\s{2,}', ' ', summary)

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
            "message": f"Не удалось провести анализ: {str(e)[:100]}"
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
            "message": "Не указан пользователь — не могу опубликовать"
        }
    
    # user_id это telegram_id
    user = session.query(User).filter_by(telegram_id=user_id).first()
    if not user:
        return {
            "success": False,
            "error": "User not found",
            "message": "Не нахожу пользователя — попробуй /start"
        }
    
    if not user.telegram_channel:
        from config import TELEGRAM_BOT_USERNAME
        bot_username = TELEGRAM_BOT_USERNAME.replace('@', '')
        return {
            "success": False,
            "error": "Telegram channel not configured",
            "message": (
                "Telegram-канал не настроен — пост не опубликован.\n"
                "📋 Чтобы настроить: Дашборд → Профиль → укажи @username канала → "
                f"добавь @{bot_username} как администратора → Сохрани.\n"
                "💡 Чтобы не потерять контент: вызови create_post с тем же текстом — "
                "сохранит пост в системе. После настройки канала пост можно опубликовать вручную."
            )
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

    # Sanitize token hallucinations (AI иногда пишет "1000+500" вместо "1500")
    from ai_integration.conversation_history import sanitize_token_hallucinations
    post_text = sanitize_token_hallucinations(post_text)
    
    # Отправляем через Telegram Bot API
    try:
        channel = user.telegram_channel
        # Убедимся что ID канала начинается с @  если это username
        if not channel.startswith('-') and not channel.startswith('@'):
            channel = f"@{channel}"

        async with _safe_http() as http_session:
            if image_url:
                # Публикуем фото с подписью
                tg_method = 'sendPhoto'
                caption_text = post_text[:1024]  # Telegram caption limit
                tg_payload = {
                    'chat_id': channel,
                    'photo': image_url,
                    'caption': caption_text,
                    'parse_mode': 'Markdown'
                }
            else:
                tg_method = 'sendMessage'
                tg_payload = {
                    'chat_id': channel,
                    'text': post_text[:4096],  # Telegram message limit
                    'parse_mode': 'Markdown'
                }

            async with http_session.post(
                f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/{tg_method}',
                json=tg_payload
            ) as response:
                result = await response.json()

                # Retry без parse_mode при ошибке парсинга Markdown
                if not result.get('ok'):
                    err_desc = result.get('description', '')
                    if "can't parse entities" in err_desc.lower():
                        logger.warning(f"[PUBLISH] Markdown parse error, retrying without parse_mode")
                        tg_payload.pop('parse_mode', None)
                        async with http_session.post(
                            f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/{tg_method}',
                            json=tg_payload
                        ) as retry_resp:
                            result = await retry_resp.json()

                if result.get('ok'):
                    # Если текст не влез в caption (>1024) — допосылаем остаток отдельным сообщением
                    if image_url and len(post_text) > 1024:
                        remaining_text = post_text[1024:]
                        try:
                            _cont_payload = {
                                'chat_id': channel,
                                'text': remaining_text[:4096],
                            }
                            async with http_session.post(
                                f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage',
                                json=_cont_payload
                            ) as _cont_resp:
                                _cont_result = await _cont_resp.json()
                                if not _cont_result.get('ok'):
                                    logger.warning(f"[PUBLISH] Failed to send continuation text: {_cont_result}")
                        except Exception as _ce:
                            logger.warning(f"[PUBLISH] Continuation text error: {_ce}")

                    logger.info(f"[PUBLISH] Successfully published to {channel}")

                    # Создаем задачу-отчет об успешной публикации (с защитой от дублей)
                    if user_id and session:
                        from models import Task
                        img_note = " (с изображением)" if image_url else ""
                        _pub_title = f"Пост опубликован в {channel}{img_note}"
                        # Дедупликация: не создаём если такой отчёт уже есть за последние 2 часа
                        _cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
                        _existing_pub = session.query(Task).filter(
                            Task.user_id == user.id,
                            Task.title.like(f'%Пост опубликован в {channel}%'),
                            Task.status == 'completed',
                            Task.created_at >= _cutoff,
                        ).first()
                        if not _existing_pub:
                            report_task = Task(
                                user_id=user.id,
                                title=_pub_title,
                                description=f"Контент:\n{post_text[:200]}...",
                                status='completed',
                                source='agent',
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
                        error_desc = f"""Бот не добавлен в канал или не является админом.

📋 Сделай так:
1. Открой канал {channel}
2. Название канала → Администраторы
3. «Добавить администратора»
4. Найди @{bot_username}
5. Дай право «Публикация сообщений»
6. Сохрани

После этого скажи «опубликуй в канал»"""
                    elif 'chat_id' in error_desc:
                        error_desc = f"""Неверный формат ID канала.

✅ Правильные форматы:
- Публичный: @your_channel
- Приватный: -1001234567890

💡 Как узнать ID приватного канала:
1. Перешли сообщение из канала боту @userinfobot
2. Он покажет ID в формате -100...
3. Укажи этот ID в профиле Dashboard"""
                    
                    return {
                        "success": False,
                        "error": error_desc,
                        "message": f"Не получилось опубликовать: {error_desc}"
                    }
                    
    except Exception as e:
        logger.error(f"[PUBLISH] Error publishing to Telegram: {e}")
        return {
            "success": False,
            "error": str(e),
            "message": f"Не удалось опубликовать: {str(e)[:100]}"
        }
