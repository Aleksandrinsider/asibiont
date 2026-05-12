"""
Полный форекс-анализ: мультитаймфрейм OHLCV + тиковый объём + тех. индикаторы + AI-вердикт.

Использует:
- Alpha Vantage (FX_DAILY / FX_INTRADAY) для OHLCV с тиковым объёмом
- ExchangeRate-API для текущего курса
- Локальные расчёты: RSI, MACD, Bollinger Bands, SMA, ATR, объемные метрики
"""

import logging
import urllib.request as _urllib_req
import json as _json
import math as _math

logger = logging.getLogger(__name__)


async def analyze_forex(instrument: str = "EUR/USD",
                        timeframe: str = "daily",
                        days: int = 30,
                        user_id: int = None,
                        session=None) -> str:
    """Полный анализ форекс пары: мультитаймфрейм OHLCV, тиковый объём,
    технические индикаторы (RSI, MACD, Bollinger Bands, SMA, ATR),
    анализ объёма, дивергенций + AI-вердикт.

    Требует ALPHAVANTAGE_API_KEY и EXCHANGERATE_API_KEY в API-ключах агента.
    """
    if not user_id:
        return "❌ Не указан user_id"

    # Нормализуем пару
    _raw = instrument.strip().upper().replace('_', '/')
    if '/' not in _raw:
        return f"❌ Неверный формат пары: {instrument}. Используйте EUR/USD, USD/RUB и т.д."
    _base_cur, _target_cur = _raw.split('/', 1)
    _pair_display = f"{_base_cur}/{_target_cur}"

    # Маппинг таймфреймов в Alpha Vantage
    _tf_map = {
        '1min': '1min', '5min': '5min', '15min': '15min',
        '30min': '30min', '60min': '60min', '1h': '60min',
        '4h': '60min', 'daily': 'daily', '1d': 'daily',
        'weekly': 'weekly', '1w': 'weekly', 'monthly': 'monthly', '1mo': 'monthly',
    }
    _av_interval = _tf_map.get(timeframe.lower().replace(' ', ''), 'daily')

    # Загружаем оба ключа
    _av_key = None
    _er_key = None
    try:
        from models import UserAgent as _UA_fa, User as _User_fa
        from models import Session as _SessionLocal_fa
        _db_sess = session
        _close_sess = False
        if _db_sess is None:
            _db_sess = _SessionLocal_fa()
            _close_sess = True
        try:
            _db_user = _db_sess.query(_User_fa).filter_by(telegram_id=user_id).first()
            _db_user_id = _db_user.id if _db_user else None
            if _db_user_id:
                from ai_integration.autonomous_agent import _decrypt_keys as _dk_fa
                for _ag in _db_sess.query(_UA_fa).filter(
                    _UA_fa.author_id == _db_user_id,
                    _UA_fa.user_api_keys.isnot(None),
                ).all():
                    _decrypted = _dk_fa(_ag.user_api_keys or '')
                    for _line in _decrypted.splitlines():
                        _line = _line.strip()
                        if not _av_key and (_line.startswith('ALPHAVANTAGE_API_KEY=') or _line.startswith('ALPHA_VANTAGE_API_KEY=')):
                            _v = _line.split('=', 1)[1].strip()
                            if _v and len(_v) > 4 and _v.lower() not in ('none', 'null', 'your_key_here', 'xxx', '...'):
                                _av_key = _v
                        if not _er_key and (_line.startswith('EXCHANGERATE_API_KEY=') or _line.startswith('EXCHANGE_RATE_API_KEY=')):
                            _v = _line.split('=', 1)[1].strip()
                            if _v and len(_v) > 8 and _v.lower() not in ('none', 'null', 'your_key', 'xxx', '...'):
                                _er_key = _v
                    if _av_key and _er_key:
                        break
        finally:
            if _close_sess:
                _db_sess.close()
    except Exception as _e:
        logger.warning(f"[ANALYZE_FOREX] Key lookup error: {_e}")

    def _http_get(url: str) -> dict:
        req = _urllib_req.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with _urllib_req.urlopen(req, timeout=20) as r:
            return _json.loads(r.read().decode())

    def _av_limit(d: dict) -> str | None:
        info = d.get("Information") or d.get("Note") or d.get("Error Message")
        if not info:
            return None
        il = info.lower()
        if "rate limit" in il or "per day" in il or "standard api" in il:
            return "⏳ Лимит Alpha Vantage исчерпан (25 запросов/день). Сбрасывается в 00:00 UTC."
        if "invalid api key" in il:
            return "❌ Неверный ALPHAVANTAGE_API_KEY."
        return f"⚠️ Alpha Vantage: {info[:120]}"

    def _sma(data, period):
        if len(data) < period:
            return None
        return [sum(data[i-period:i]) / period for i in range(period, len(data) + 1)]

    def _ema(data, period):
        if len(data) < period:
            return []
        multiplier = 2 / (period + 1)
        result = [sum(data[:period]) / period]
        for i in range(period, len(data)):
            result.append((data[i] - result[-1]) * multiplier + result[-1])
        return result

    result_lines = []

    # ── Блок 1: текущий курс (ExchangeRate-API) ──
    if not _er_key:
        result_lines.append("⚠️ **Текущий курс недоступен**: EXCHANGERATE_API_KEY не настроен.\nПолучи бесплатный ключ (1500 req/мес, 170+ валют) на exchangerate-api.com:\nEXCHANGERATE_API_KEY=твой_ключ")
    else:
        try:
            _url = f"https://v6.exchangerate-api.com/v6/{_er_key}/pair/{_base_cur}/{_target_cur}"
            data = _http_get(_url)
            if data.get('result') == 'success':
                _rate = data.get('conversion_rate')
                _update = data.get('time_last_update_utc', '')[:22]
                if _rate is not None:
                    result_lines.append(f"💱 **Текущий курс**: 1 {_base_cur} = **{_rate} {_target_cur}**")
                    if _update:
                        result_lines.append(f"   🕐 Обновление: {_update}")
            elif data.get('result') == 'error':
                _etype = data.get('error-type', 'unknown')
                if _etype == 'quota-reached':
                    result_lines.append("⏳ Лимит ExchangeRate-API исчерпан (1500/мес).")
                else:
                    result_lines.append(f"❌ ExchangeRate-API: {_etype}")
        except Exception as e:
            logger.warning(f"[ANALYZE_FOREX] ExchangeRate error: {e}")

    # ── Блок 2: OHLCV + тиковый объём (Alpha Vantage) ──
    _ohlcv = []
    if not _av_key:
        result_lines.append("\n📊 **Исторические данные недоступны**: ALPHAVANTAGE_API_KEY не настроен.\nПолучи бесплатный ключ (25 запросов/день) на alphavantage.co:\nALPHAVANTAGE_API_KEY=твой_ключ")
    else:
        # Выбираем функцию Alpha Vantage
        if _av_interval == 'daily':
            _av_func = 'FX_DAILY'
            _av_data_key = 'Time Series FX (Daily)'
        elif _av_interval in ('weekly', 'monthly'):
            _av_func = 'FX_WEEKLY' if _av_interval == 'weekly' else 'FX_MONTHLY'
            _av_data_key = 'Time Series FX (Weekly)' if _av_interval == 'weekly' else 'Time Series FX (Monthly)'
        else:
            _av_func = 'FX_INTRADAY'
            _av_data_key = f'Time Series FX ({_av_interval.upper()})'

        _outputsize = 'compact' if days <= 30 else 'full'
        _params = {
            'function': _av_func,
            'from_symbol': _base_cur,
            'to_symbol': _target_cur,
            'outputsize': _outputsize,
        }
        if _av_func == 'FX_INTRADAY':
            _params['interval'] = _av_interval

        try:
            _ohlcv_url = '&'.join(f'{k}={v}' for k, v in _params.items())
            _ohlcv_raw = _http_get(f"https://www.alphavantage.co/query?{_ohlcv_url}&apikey={_av_key}")
            _rl = _av_limit(_ohlcv_raw)
            if _rl:
                result_lines.append(f"\n{_rl}")
            else:
                _ts = _ohlcv_raw.get(_av_data_key, {})
                if not _ts:
                    result_lines.append(f"\n❌ Нет данных по {_pair_display} для таймфрейма {timeframe}. Попробуйте другой таймфрейм (daily/weekly).")
                else:
                    _sorted_dates = sorted(_ts.keys(), reverse=True)[:days]
                    for _d in _sorted_dates:
                        _entry = _ts[_d]
                        _ohlcv.append({
                            'date': _d,
                            'open': float(_entry.get('1. open', 0)),
                            'high': float(_entry.get('2. high', 0)),
                            'low': float(_entry.get('3. low', 0)),
                            'close': float(_entry.get('4. close', 0)),
                            'volume': float(_entry.get('5. volume', 0)),
                        })

                    if _ohlcv:
                        _current = _ohlcv[0]
                        _oldest = _ohlcv[-1]
                        _change = _current['close'] - _oldest['close']
                        _change_pct = (_change / _oldest['close']) * 100 if _oldest['close'] else 0
                        _high_ever = max(o['high'] for o in _ohlcv)
                        _low_ever = min(o['low'] for o in _ohlcv)

                        result_lines.append(f"\n📈 **{_pair_display} — {len(_ohlcv)} периодов ({timeframe})**")
                        result_lines.append(f"   ▸ Текущее закрытие: **{_current['close']:.5f}**")
                        result_lines.append(f"   ▸ Изменение за период: {_change:+.5f} ({_change_pct:+.2f}%)")
                        result_lines.append(f"   ▸ Максимум: {_high_ever:.5f} | Минимум: {_low_ever:.5f}")
                        _avg_vol = sum(o['volume'] for o in _ohlcv) / len(_ohlcv)
                        _current_vol = _current['volume']
                        result_lines.append(f"   ▸ Средний тиковый объём: {_avg_vol:.0f} | Текущий: **{_current_vol:.0f}**")
                    else:
                        result_lines.append(f"\n❌ Нет данных по {_pair_display} за выбранный период.")
        except Exception as e:
            logger.error(f"[ANALYZE_FOREX] Alpha Vantage error: {e}")
            result_lines.append(f"\n❌ Ошибка получения исторических данных: {str(e)[:100]}")

    # ── Блок 3: технические индикаторы ──
    if _ohlcv and len(_ohlcv) >= 15:
        _closes = [o['close'] for o in _ohlcv]
        _highs = [o['high'] for o in _ohlcv]
        _lows = [o['low'] for o in _ohlcv]
        _volumes = [o['volume'] for o in _ohlcv]
        _cur_close = _closes[0]

        _ta_lines = ["\n📊 **Технические индикаторы:**"]

        # RSI(14)
        try:
            _av_sym = f"{_base_cur}{_target_cur}"
            if _av_func == 'FX_INTRADAY':
                _rsi_params = {'function': 'RSI', 'symbol': _av_sym, 'interval': _av_interval,
                               'time_period': '14', 'series_type': 'close'}
            else:
                _rsi_params = {'function': 'RSI', 'symbol': _av_sym, 'interval': 'daily',
                               'time_period': '14', 'series_type': 'close'}
            _d = _http_get(f"https://www.alphavantage.co/query?{'&'.join(f'{k}={v}' for k,v in _rsi_params.items())}&apikey={_av_key}")
            if not _av_limit(_d):
                _rsi_data = _d.get('Technical Analysis: RSI', {})
                if _rsi_data:
                    _ld = sorted(_rsi_data.keys(), reverse=True)[0]
                    _rsi = float(_rsi_data[_ld]['RSI'])
                    _zone = "🟢 перепроданность" if _rsi < 30 else ("🔴 перекупленность" if _rsi > 70 else "🟡 нейтральная зона")
                    _ta_lines.append(f"  • RSI(14): **{_rsi:.1f}** — {_zone} ({_ld})")
        except Exception as e:
            logger.warning(f"[ANALYZE_FOREX] RSI error: {e}")

        # MACD(12,26,9)
        try:
            if _av_func == 'FX_INTRADAY':
                _macd_params = {'function': 'MACD', 'symbol': _av_sym, 'interval': _av_interval,
                                'series_type': 'close', 'fastperiod': '12', 'slowperiod': '26', 'signalperiod': '9'}
            else:
                _macd_params = {'function': 'MACD', 'symbol': _av_sym, 'interval': 'daily',
                                'series_type': 'close', 'fastperiod': '12', 'slowperiod': '26', 'signalperiod': '9'}
            _d = _http_get(f"https://www.alphavantage.co/query?{'&'.join(f'{k}={v}' for k,v in _macd_params.items())}&apikey={_av_key}")
            if not _av_limit(_d):
                _macd_data = _d.get('Technical Analysis: MACD', {})
                if _macd_data:
                    _ld = sorted(_macd_data.keys(), reverse=True)[0]
                    _m = float(_macd_data[_ld]['MACD'])
                    _s = float(_macd_data[_ld]['MACD_Signal'])
                    _h = float(_macd_data[_ld]['MACD_Hist'])
                    _dir = "▲ бычий" if _m > _s else "▼ медвежий"
                    _ta_lines.append(f"  • MACD(12,26,9): MACD={_m:.5f} / сигнал={_s:.5f} / гистограмма={_h:.5f} — {_dir} ({_ld})")
        except Exception as e:
            logger.warning(f"[ANALYZE_FOREX] MACD error: {e}")

        # Bollinger Bands (20,2) — расчёт локально
        if len(_closes) >= 20:
            try:
                _bb_period = 20
                _bb_sma_vals = _sma(_closes, _bb_period)
                if _bb_sma_vals:
                    _bb_mid = _bb_sma_vals[-1]
                    _recent = _closes[-_bb_period:]
                    _mean = sum(_recent) / _bb_period
                    _variance = sum((x - _mean) ** 2 for x in _recent) / _bb_period
                    _stddev = _math.sqrt(_variance)
                    _bb_upper = _bb_mid + 2 * _stddev
                    _bb_lower = _bb_mid - 2 * _stddev
                    _bb_pos = "🔝 выше верхней" if _cur_close > _bb_upper else ("🔻 ниже нижней" if _cur_close < _bb_lower else "📍 внутри канала")
                    _ta_lines.append(f"  • Bollinger Bands(20,2): **верх={_bb_upper:.5f}** / сред={_bb_mid:.5f} / **ниж={_bb_lower:.5f}** — цена {_bb_pos}")
            except Exception as e:
                logger.warning(f"[ANALYZE_FOREX] BB error: {e}")

        # SMA(50) и SMA(200)
        if len(_closes) >= 50:
            _sma50_vals = _sma(_closes, 50)
            if _sma50_vals:
                _last_sma50 = _sma50_vals[-1]
                _above_below = "🔺 цена выше SMA(50)" if _cur_close > _last_sma50 else "🔻 цена ниже SMA(50)"
                _ta_lines.append(f"  • SMA(50): {_last_sma50:.5f} — {_above_below}")
        if len(_closes) >= 200:
            _sma200_vals = _sma(_closes, 200)
            if _sma200_vals:
                _last_sma200 = _sma200_vals[-1]
                _above_below = "🔺 цена выше SMA(200)" if _cur_close > _last_sma200 else "🔻 цена ниже SMA(200)"
                _ta_lines.append(f"  • SMA(200): {_last_sma200:.5f} — {_above_below}")

        # EMA(20) и EMA(50) — расчёт локально + пересечение
        _ema20_vals = _ema(_closes, 20)
        _ema50_vals = _ema(_closes, 50) if len(_closes) >= 50 else []
        if _ema20_vals:
            _e20 = _ema20_vals[-1]
            _ta_lines.append(f"  • EMA(20): {_e20:.5f}")
        if _ema50_vals:
            _e50 = _ema50_vals[-1]
            _gc_dc = "☀️ золотое сечение (EMA20 > EMA50 — бычий тренд)" if _e20 > _e50 else "🌧️ крест смерти (EMA20 < EMA50 — медвежий тренд)"
            _ta_lines.append(f"  • EMA(50): {_e50:.5f} — {_gc_dc}")

        # ATR(14) — волатильность
        if len(_ohlcv) >= 15:
            _tr_values = []
            for i in range(1, min(15, len(_ohlcv))):
                _h = _ohlcv[i]['high']
                _l = _ohlcv[i]['low']
                _pc = _ohlcv[i - 1]['close']
                _tr = max(_h - _l, abs(_h - _pc), abs(_l - _pc))
                _tr_values.append(_tr)
            if _tr_values:
                _atr14 = sum(_tr_values) / len(_tr_values)
                _atr_pct = (_atr14 / _cur_close) * 100 if _cur_close else 0
                _volatility = "⚡ Высокая" if _atr_pct > 2 else ("💤 Низкая" if _atr_pct < 0.5 else "📊 Умеренная")
                _ta_lines.append(f"  • ATR(14): {_atr14:.5f} ({_atr_pct:.2f}% от цены) — {_volatility} волатильность")

        if len(_ta_lines) > 1:
            result_lines.extend(_ta_lines)

    # ── Блок 4: анализ тикового объёма ──
    if _ohlcv and len(_ohlcv) >= 10:
        _vol_lines = ["\n📊 **Анализ тикового объёма:**"]
        _volumes = [o['volume'] for o in _ohlcv]
        _avg_vol = sum(_volumes) / len(_volumes)
        _recent_vol = sum(_volumes[-5:]) / 5 if len(_volumes) >= 5 else _avg_vol

        # Тренд объёма
        _vol_trend = "📈 растёт" if _recent_vol > _avg_vol * 1.1 else ("📉 падает" if _recent_vol < _avg_vol * 0.9 else "➡️ стабильный")
        _vol_lines.append(f"  • Средний объём: {_avg_vol:.0f} | Последние 5: {_recent_vol:.0f} ({_vol_trend})")

        # Аномалии объёма (всплески)
        _anomaly_count = 0
        for i in range(min(5, len(_volumes))):
            if abs(i) <= len(_volumes) and _volumes[i] > _avg_vol * 2:
                _anomaly_count += 1
        if _anomaly_count > 0:
            _vol_lines.append(f"  ⚡ Аномалии объёма (x2 от среднего): {_anomaly_count} из последних 5 свечей")
        _vol_ratio = _volumes[0] / _avg_vol if _avg_vol else 1
        if _vol_ratio > 2:
            _vol_lines.append(f"  🚨 Всплеск объёма — возможен вход крупного игрока ({_vol_ratio:.1f}x от среднего)")
        elif _vol_ratio > 1.5:
            _vol_lines.append(f"  🟡 Повышенный объём — подтверждение движения ({_vol_ratio:.1f}x от среднего)")

        # Дивергенция цены и объёма
        if len(_ohlcv) >= 10:
            _price_trend = _ohlcv[0]['close'] - _ohlcv[min(9, len(_ohlcv)-1)]['close']
            _vol_trend_val = _recent_vol - _avg_vol
            if _price_trend > 0 and _vol_trend_val < -_avg_vol * 0.1:
                _vol_lines.append("  ⚠️ **Медвежья дивергенция**: цена растёт, но объём падает — тренд слабеет")
            elif _price_trend < 0 and _vol_trend_val > _avg_vol * 0.1:
                _vol_lines.append("  ⚠️ **Бычья дивергенция**: цена падает, но объём растёт — возможен разворот вверх")
            else:
                _vol_lines.append("  ✅ Дивергенций не обнаружено")

        # Volume Profile (POC — зона максимального объёма)
        if len(_ohlcv) >= 20:
            _min_price = min(o['low'] for o in _ohlcv)
            _max_price = max(o['high'] for o in _ohlcv)
            _price_range = _max_price - _min_price
            if _price_range > 0:
                _zones = 10
                _zone_size = _price_range / _zones
                _zone_volumes = [0.0] * _zones
                for o in _ohlcv:
                    _mid = (o['high'] + o['low']) / 2
                    _idx = min(_zones - 1, int((_mid - _min_price) / _zone_size))
                    _zone_volumes[_idx] += o['volume']
                _max_vol_zone = max(_zone_volumes)
                if _max_vol_zone > 0:
                    _poc_idx = _zone_volumes.index(_max_vol_zone)
                    _poc_low = _min_price + _poc_idx * _zone_size
                    _poc_high = _poc_low + _zone_size
                    _cur_price = _ohlcv[0]['close']
                    _poc_pos = "📉 цена ниже POC — сопротивление сверху" if _cur_price < _poc_low else ("📈 цена выше POC — поддержка снизу" if _cur_price > _poc_high else "↔️ цена внутри POC — консолидация")
                    _vol_lines.append(f"  🎯 **POC (зона макс. объёма)**: {_poc_low:.5f} — {_poc_high:.5f} | {_poc_pos}")

        result_lines.extend(_vol_lines)

    # ── Блок 5: AI-вердикт ──
    if _ohlcv and len(_ohlcv) >= 20:
        _signals_bull = 0
        _signals_bear = 0
        _signals_neutral = 0

        # RSI сигнал
        try:
            if 'rsi' in locals() or '_rsi' in dir():
                pass
            # Переиспользуем уже загруженный RSI
            if '_rsi_data' in dir() and _rsi_data:
                _ld = sorted(_rsi_data.keys(), reverse=True)[0]
                _rsi_val = float(_rsi_data[_ld]['RSI'])
                if _rsi_val < 35:
                    _signals_bull += 1
                elif _rsi_val > 65:
                    _signals_bear += 1
                else:
                    _signals_neutral += 1
        except:
            pass

        # MACD сигнал
        try:
            if '_m' in dir() and '_s' in dir():
                if _m < _s:  # медвежий
                    _signals_bear += 1
                else:
                    _signals_bull += 1
        except:
            pass

        # Bollinger сигнал
        try:
            if _cur_close < _bb_lower:
                _signals_bull += 1  # oversold
            elif _cur_close > _bb_upper:
                _signals_bear += 1  # overbought
            else:
                _signals_neutral += 1
        except:
            pass

        # EMA crossover
        try:
            if '_e20' in dir() and '_e50' in dir():
                if _e20 > _e50:
                    _signals_bull += 1
                else:
                    _signals_bear += 1
        except:
            pass

        # Volume divergence
        try:
            if '_price_trend' in dir() and '_vol_trend_val' in dir():
                if _price_trend > 0 and _vol_trend_val < -_avg_vol * 0.1:
                    _signals_bear += 1
                elif _price_trend < 0 and _vol_trend_val > _avg_vol * 0.1:
                    _signals_bull += 1
                else:
                    _signals_neutral += 1
        except:
            pass

        # SMA50 position
        try:
            if '_last_sma50' in dir() and _cur_close > _last_sma50:
                _signals_bull += 1
        except:
            pass

        _total = _signals_bull + _signals_bear + _signals_neutral
        if _total > 0:
            _bull_pct = (_signals_bull / _total) * 100
            _bear_pct = (_signals_bear / _total) * 100

            result_lines.append("\n🧠 **AI-вердикт:**")
            result_lines.append(f"  • Бычьих сигналов: {_signals_bull}/{_total} | Медвежьих: {_signals_bear}/{_total} | Нейтральных: {_signals_neutral}/{_total}")

            if _bull_pct >= 60:
                _verdict = "🟢 **БЫЧИЙ НАСТРОЙ** — преобладают бычьи сигналы, вероятность роста выше"
            elif _bear_pct >= 60:
                _verdict = "🔴 **МЕДВЕЖИЙ НАСТРОЙ** — преобладают медвежьи сигналы, вероятность снижения выше"
            else:
                _verdict = "🟡 **НЕЙТРАЛЬНО** — сигналы смешанные, рынок не определился. Рекомендуется дождаться подтверждения"
            result_lines.append(f"  ▸ {_verdict}")

        # Дополнительные сигналы по объёму
        if _vol_ratio > 1.5 and _change > 0:
            result_lines.append("  💡 Рост на повышенном объёме — тренд подтверждён")
        elif _vol_ratio > 1.5 and _change < 0:
            result_lines.append("  💡 Падение на повышенном объёме — медвежий тренд подтверждён")

    # ── Footer ──
    result_lines.append("\n" + "─" * 40)
    result_lines.append("⚠️ Анализ на основе тикового объёма Alpha Vantage (FX_DAILY/FX_INTRADAY).")
    result_lines.append("В форекс нет единого биржевого объёма — тиковый объём отражает активность,")
    result_lines.append("но не реальные денежные потоки. Не является инвестиционной рекомендацией.")

    return '\n'.join(result_lines)
