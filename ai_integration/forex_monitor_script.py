# Forex Monitor — run_agent_action скрипт для форекс-агента
# Действия:
#   check_all       — котировки акций/сырья/валют (как было)
#   monitor_forex   — сканирование популярных пар на аномалии (НОВОЕ)
#   monitor_stocks  — сканирование акций на аномалии (НОВОЕ)

import os as _os
ACTION = _os.environ.get('AGENT_ACTION', 'check_all')
ACTION_PARAMS = _os.environ.get('AGENT_ACTION_PARAMS', '{}')

# === Настройки (можно переопределить через переменные окружения) ===
# Популярные валютные пары для мониторинга
POPULAR_PAIRS = _os.environ.get('FX_PAIRS',
    'EUR/USD,GBP/USD,USD/JPY,USD/CHF,AUD/USD,NZD/USD,USD/CAD,'
    'EUR/JPY,GBP/JPY,EUR/GBP'
).split(',')
# Популярные акции для мониторинга
POPULAR_STOCKS = _os.environ.get('FX_STOCKS',
    'AAPL,MSFT,GOOGL,AMZN,NVDA,META,TSLA,JPM,SPY,QQQ'
).split(',')
# Пороги аномалий
VOLUME_THRESHOLD = float(_os.environ.get('VOLUME_THRESHOLD', '2.0'))  # во сколько раз выше среднего
RSI_OVERSOLD = float(_os.environ.get('RSI_OVERSOLD', '30'))           # RSI ниже = перепроданность
RSI_OVERBOUGHT = float(_os.environ.get('RSI_OVERBOUGHT', '70'))       # RSI выше = перекупленность
ATR_THRESHOLD = float(_os.environ.get('ATR_THRESHOLD', '1.5'))        # ATR расширение во сколько раз


# ──────────────────────────────────────────────────────────────
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ──────────────────────────────────────────────────────────────

def _http_get(url: str) -> dict:
    """GET запрос к API, возвращает словарь."""
    import urllib.request, json
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def _rsi(prices: list[float], period: int = 14) -> float | None:
    """Расчёт RSI(14) по ценам закрытия."""
    if len(prices) < period + 1:
        return None
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        diff = prices[i] - prices[i-1]
        if diff >= 0:
            gains += diff
        else:
            losses += abs(diff)
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    for i in range(period + 1, len(prices)):
        diff = prices[i] - prices[i-1]
        if diff >= 0:
            avg_gain = (avg_gain * (period - 1) + diff) / period
            avg_loss = (avg_loss * (period - 1)) / period
        else:
            avg_gain = (avg_gain * (period - 1)) / period
            avg_loss = (avg_loss * (period - 1) + abs(diff)) / period
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 1)


def _sma(data: list, period: int) -> list | None:
    """Простая скользящая средняя."""
    if len(data) < period:
        return None
    return [sum(data[i-period:i]) / period for i in range(period, len(data) + 1)]


def _atr(bars: list[dict], period: int = 14) -> float | None:
    """Средний истинный диапазон (ATR). bars — список OHLCV словарей."""
    if len(bars) < period + 1:
        return None
    tr_values = []
    for i in range(1, len(bars)):
        high = float(bars[i]['high'])
        low = float(bars[i]['low'])
        prev_close = float(bars[i-1]['close'])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_values.append(tr)
    if len(tr_values) < period:
        return None
    # SMA для ATR
    atr_val = sum(tr_values[:period]) / period
    return round(atr_val, 4)


def _detect_divergence(prices: list[float], volumes: list[float], lookback: int = 10) -> str | None:
    """Детект дивергенции цена/объём на последних N свечах."""
    if len(prices) < lookback or len(volumes) < lookback:
        return None
    price_trend = prices[-1] - prices[-lookback]
    vol_trend = sum(volumes[-lookback:]) / lookback - sum(volumes[-lookback*2:-lookback]) / lookback
    if price_trend > 0 and vol_trend < -sum(volumes[-lookback*2:-lookback]) / lookback * 0.2:
        return '🐻 Медвежья дивергенция: цена растёт, объём падает'
    if price_trend < 0 and vol_trend > -sum(volumes[-lookback*2:-lookback]) / lookback * 0.2:
        return '🐂 Бычья дивергенция: цена падает, объём растёт'
    return None


# ──────────────────────────────────────────────────────────────
# РЕЖИМ: MONITOR_FOREX — сканирование валютных пар
# ──────────────────────────────────────────────────────────────

def _scan_forex_pair(pair: str, api_key: str) -> dict | None:
    """Сканирует одну валютную пару на аномалии. Возвращает словарь с аномалиями или None."""
    if '/' not in pair:
        return None
    base, target = pair.upper().split('/', 1)
    av_sym = f"{base}{target}"

    # Запрашиваем FX_DAILY (100+ свечей за 1 запрос)
    url = (f'https://www.alphavantage.co/query?function=FX_DAILY&from_symbol={base}'
           f'&to_symbol={target}&outputsize=compact&apikey={api_key}')
    try:
        d = _http_get(url)
    except Exception as e:
        return {'pair': pair, 'error': f'Ошибка запроса: {e}'}

    if 'Information' in d:
        return {'pair': pair, 'error': 'Лимит API или неверный ключ'}
    if 'Error Message' in d:
        return {'pair': pair, 'error': d['Error Message']}

    ts = d.get('Time Series FX (Daily)', {})
    if not ts:
        return {'pair': pair, 'error': 'Нет данных'}

    # Парсим OHLCV
    dates = sorted(ts.keys(), reverse=True)  # от новых к старым
    closes, highs, lows, volumes = [], [], [], []
    bars = []
    for dt in dates:
        v = ts[dt]
        close = float(v['4. close'])
        high = float(v['2. high'])
        low = float(v['3. low'])
        vol = int(v['5. volume'])
        closes.append(close)
        highs.append(high)
        lows.append(low)
        volumes.append(vol)
        bars.append({'date': dt, 'high': high, 'low': low, 'close': close, 'volume': vol})

    if len(closes) < 20:
        return {'pair': pair, 'error': f'Мало данных: {len(closes)} свечей'}

    anomalies = []
    current_price = closes[0]
    current_volume = volumes[0]
    avg_volume = sum(volumes[1:21]) / min(20, len(volumes) - 1) if len(volumes) > 1 else volumes[0]

    # 1. Аномалия объёма
    vol_ratio = current_volume / avg_volume if avg_volume > 0 else 0
    if vol_ratio >= VOLUME_THRESHOLD:
        anomalies.append(f'📊 Аномальный объём: {vol_ratio:.1f}x от среднего ({current_volume:,} vs {avg_volume:,.0f})')

    # 2. RSI
    rsi_val = _rsi(closes[:30], 14)
    if rsi_val is not None:
        if rsi_val <= RSI_OVERSOLD:
            anomalies.append(f'🟢 RSI={rsi_val} — перепроданность (<={RSI_OVERSOLD})')
        elif rsi_val >= RSI_OVERBOUGHT:
            anomalies.append(f'🔴 RSI={rsi_val} — перекупленность (>={RSI_OVERBOUGHT})')

    # 3. ATR расширение
    atr_val = _atr(bars, 14)
    if atr_val is not None and len(closes) > 20:
        prev_bars = bars[1:15] if len(bars) > 15 else bars[1:]
        atr_prev = _atr(prev_bars, 14)
        if atr_prev and atr_prev > 0 and (atr_val / atr_prev) >= ATR_THRESHOLD:
            anomalies.append(f'📈 Волатильность выросла в {atr_val/atr_prev:.1f}x (ATR={atr_val:.5f})')

    # 4. SMA50 позиция (тренд)
    sma50 = _sma(closes, 50)
    sma20 = _sma(closes, 20)
    if sma50 and sma20:
        if closes[0] > sma20[-1] > sma50[-1]:
            anomalies.append('📈 Восходящий тренд (цена > SMA20 > SMA50)')
        elif closes[0] < sma20[-1] < sma50[-1]:
            anomalies.append('📉 Нисходящий тренд (цена < SMA20 < SMA50)')
        if sma20[-1] > sma50[-1] and sma20[-2] <= sma50[-2]:
            anomalies.append('🟢 SMA20 пересекла SMA50 вверх — бычий сигнал')
        elif sma20[-1] < sma50[-1] and sma20[-2] >= sma50[-2]:
            anomalies.append('🔴 SMA20 пересекла SMA50 вниз — медвежий сигнал')

    # 5. Дивергенция цена/объём
    div = _detect_divergence(closes, volumes)
    if div:
        anomalies.append(div)

    # 6. Сильное движение за день
    if len(closes) > 1:
        day_change_pct = round((closes[0] - closes[1]) / closes[1] * 100, 2)
        if abs(day_change_pct) >= 1.0:
            direction = '📈' if day_change_pct > 0 else '📉'
            anomalies.append(f'{direction} Сильное движение: {day_change_pct:+.2f}% за день')

    if not anomalies:
        return None  # ничего интересного

    return {
        'pair': pair,
        'price': current_price,
        'rsi': rsi_val,
        'volume_ratio': round(vol_ratio, 1),
        'day_change': round((closes[0] - closes[1]) / closes[1] * 100, 2) if len(closes) > 1 else 0,
        'anomalies': anomalies,
        'error': None,
    }


# ──────────────────────────────────────────────────────────────
# РЕЖИМ: MONITOR_STOCKS — сканирование акций
# ──────────────────────────────────────────────────────────────

def _scan_stock(symbol: str, api_key: str) -> dict | None:
    """Сканирует одну акцию на аномалии."""
    url = (f'https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&symbol={symbol}'
           f'&outputsize=compact&apikey={api_key}')
    try:
        d = _http_get(url)
    except Exception as e:
        return {'symbol': symbol, 'error': f'Ошибка запроса: {e}'}

    if 'Information' in d:
        return {'symbol': symbol, 'error': 'Лимит API'}
    if 'Error Message' in d:
        return {'symbol': symbol, 'error': d['Error Message']}

    ts = d.get('Time Series (Daily)', {})
    if not ts:
        return {'symbol': symbol, 'error': 'Нет данных'}

    dates = sorted(ts.keys(), reverse=True)
    closes, volumes, bars = [], [], []
    for dt in dates:
        v = ts[dt]
        close = float(v['4. close'])
        vol = int(v['5. volume'])
        closes.append(close)
        volumes.append(vol)
        bars.append({'date': dt, 'high': float(v['2. high']), 'low': float(v['3. low']),
                     'close': close, 'volume': vol})

    if len(closes) < 20:
        return {'symbol': symbol, 'error': 'Мало данных'}

    anomalies = []
    current_price = closes[0]
    avg_volume = sum(volumes[1:21]) / min(20, len(volumes) - 1) if len(volumes) > 1 else volumes[0]
    vol_ratio = current_volume / avg_volume if avg_volume > 0 else 0

    # Объём
    if vol_ratio >= VOLUME_THRESHOLD:
        anomalies.append(f'📊 Аномальный объём: {vol_ratio:.1f}x ({current_volume:,} vs {avg_volume:,.0f})')

    # RSI
    rsi_val = _rsi(closes[:30], 14)
    if rsi_val is not None:
        if rsi_val <= RSI_OVERSOLD:
            anomalies.append(f'🟢 RSI={rsi_val} — перепроданность')
        elif rsi_val >= RSI_OVERBOUGHT:
            anomalies.append(f'🔴 RSI={rsi_val} — перекупленность')

    # SMA50/200 — долгосрочный тренд
    sma50 = _sma(closes, 50)
    sma200 = _sma(closes, 200)
    if sma200 and sma50 and len(closes) > 200:
        if closes[0] > sma50[-1] > sma200[-1]:
            anomalies.append('📈 Долгосрочный восходящий тренд')
        elif closes[0] < sma50[-1] < sma200[-1]:
            anomalies.append('📉 Долгосрочный нисходящий тренд')

    # Дневное движение
    if len(closes) > 1:
        day_change = round((closes[0] - closes[1]) / closes[1] * 100, 2)
        if abs(day_change) >= 2.0:
            direction = '📈' if day_change > 0 else '📉'
            anomalies.append(f'{direction} Сильное движение: {day_change:+.2f}%')

    if not anomalies:
        return None

    return {
        'symbol': symbol,
        'price': current_price,
        'anomalies': anomalies,
    }


# ──────────────────────────────────────────────────────────────
# ОСНОВНЫЕ РЕЖИМЫ
# ──────────────────────────────────────────────────────────────

API_KEY = _os.environ.get('ALPHAVANTAGE_API_KEY', '')
ER_KEY = _os.environ.get('EXCHANGERATE_API_KEY', '')


# === РЕЖИМ: monitor_forex ===
if ACTION in ('monitor_forex', 'monitor_fx', 'scan_forex', 'check_anomalies', 'check_all'):
    print('=== 🔍 СКАНИРОВАНИЕ ВАЛЮТНЫХ ПАР НА АНОМАЛИИ ===', flush=True)
    if not API_KEY:
        print('❌ ALPHAVANTAGE_API_KEY не задан. Нужен для OHLCV данных.')
    else:
        pairs = [p.strip() for p in POPULAR_PAIRS if p.strip()]
        alerts = []
        errors = []
        scanned = 0

        for pair in pairs[:15]:  # максимум 15 пар (15 запросов из 25 в день)
            result = _scan_forex_pair(pair, API_KEY)
            scanned += 1
            if result is None:
                continue  # нет аномалий — пропускаем
            if result.get('error'):
                errors.append(result)
            else:
                alerts.append(result)

        print(f'\n📊 Проверено пар: {scanned}', flush=True)
        print(f'⚠️  Найдено аномалий: {len(alerts)}', flush=True)
        if errors:
            print(f'❌ Ошибок: {len(errors)}', flush=True)

        if alerts:
            print('\n' + '=' * 60, flush=True)
            print('⚠️  АНОМАЛИИ НА ВАЛЮТНОМ РЫНКЕ ⚠️', flush=True)
            print('=' * 60, flush=True)
            for a in alerts:
                print(f'\n🔹 {a["pair"]}  |  Цена: {a["price"]}  |  {a.get("day_change", 0):+.2f}%', flush=True)
                if a.get('rsi') is not None:
                    print(f'   RSI(14): {a["rsi"]}', flush=True)
                if a.get('volume_ratio'):
                    print(f'   Объём: {a["volume_ratio"]}x от среднего', flush=True)
                for note in a['anomalies']:
                    print(f'   • {note}', flush=True)
            print('\n' + '=' * 60, flush=True)
            print(f'Итого: {len(alerts)} пар с аномалиями', flush=True)
        else:
            print('\n✅ Все пары в норме. Аномалий не обнаружено.', flush=True)

    print('\n💡 Для детального анализа одной пары используй инструмент analyze_forex.', flush=True)


# === РЕЖИМ: monitor_stocks ===
if ACTION in ('monitor_stocks', 'scan_stocks', 'monitor_equities', 'check_all'):
    print('\n=== 🔍 СКАНИРОВАНИЕ АКЦИЙ НА АНОМАЛИИ ===', flush=True)
    if not API_KEY:
        print('❌ ALPHAVANTAGE_API_KEY не задан.')
    else:
        stocks = [s.strip().upper() for s in POPULAR_STOCKS if s.strip()]
        alerts = []
        errors = []

        for sym in stocks[:10]:
            result = _scan_stock(sym, API_KEY)
            if result is None:
                continue
            if result.get('error'):
                errors.append(result)
            else:
                alerts.append(result)

        print(f'📊 Проверено акций: {len(stocks[:10])}', flush=True)
        print(f'⚠️  Найдено аномалий: {len(alerts)}', flush=True)

        if alerts:
            print('\n' + '=' * 60, flush=True)
            print('⚠️  АНОМАЛИИ НА ФОНДОВОМ РЫНКЕ ⚠️', flush=True)
            print('=' * 60, flush=True)
            for a in alerts:
                print(f'\n🔹 {a["symbol"]}  |  ${a["price"]}', flush=True)
                for note in a['anomalies']:
                    print(f'   • {note}', flush=True)
            print('\n' + '=' * 60, flush=True)
        else:
            print('✅ Все акции в норме.', flush=True)


# === РЕЖИМ: alphavantage_query / get_stock_data (как было) ===
if ACTION in ('alphavantage_query', 'get_stock_data', 'check_all'):
    print('\n=== 📊 Alpha Vantage ===', flush=True)
    SYMBOLS = _os.environ.get('AV_SYMBOLS', 'AAPL,MSFT,GOOGL')

    # Сырьевые товары
    COMMODITIES = {'BRENT': 'BRENT', 'WTI': 'WTI', 'NATURAL_GAS': 'NATURAL_GAS',
                   'COPPER': 'COPPER', 'WHEAT': 'WHEAT', 'CORN': 'CORN', 'COTTON': 'COTTON',
                   'SUGAR': 'SUGAR', 'COFFEE': 'COFFEE', 'ALL_COMMODITIES': 'ALL_COMMODITIES'}

    def av_get(params):
        params['apikey'] = API_KEY
        url = 'https://www.alphavantage.co/query?' + '&'.join(k + '=' + str(v) for k, v in params.items())
        return _http_get(url)

    def fetch_commodity(sym):
        url = ('https://www.alphavantage.co/query?function=' + COMMODITIES[sym]
               + '&interval=monthly&apikey=' + API_KEY)
        try:
            d = _http_get(url)
        except Exception as e:
            print(sym + ': ошибка ' + str(e))
            return
        if 'Information' in d:
            print(sym + ': требует Premium-ключ')
            return
        data = d.get('data', [])
        if not data:
            print(sym + ': нет данных')
            return
        last = data[0]
        prev = data[1] if len(data) > 1 else last
        price = last.get('value', '?')
        date = last.get('date', '')[:10]
        try:
            chg = round(float(price) - float(prev.get('value', price)), 2)
            sign = '+' if chg >= 0 else ''
            print(sym + ': $' + str(price) + '  ' + sign + str(chg) + '  (' + date + ')')
        except Exception:
            print(sym + ': $' + str(price) + '  (' + date + ')')

    try:
        if not API_KEY:
            print('Ошибка: ALPHAVANTAGE_API_KEY не задан.')
        else:
            symbols = [s.strip().upper() for s in SYMBOLS.split(',') if s.strip()]
            if not symbols:
                symbols = ['AAPL']
            for sym in symbols[:8]:
                if sym in COMMODITIES:
                    fetch_commodity(sym)
                elif '/' in sym:
                    from_c, to_c = sym.split('/', 1)
                    d = av_get({'function': 'CURRENCY_EXCHANGE_RATE', 'from_currency': from_c, 'to_currency': to_c})
                    info = d.get('Realtime Currency Exchange Rate', {})
                    rate = info.get('5. Exchange Rate', '?')
                    refreshed = info.get('6. Last Refreshed', '')[:16]
                    print(sym + ': ' + str(rate) + '  (' + refreshed + ')')
                else:
                    d = av_get({'function': 'GLOBAL_QUOTE', 'symbol': sym})
                    q = d.get('Global Quote', {})
                    price = q.get('05. price', '?')
                    chg = q.get('09. change', '0')
                    chg_pct = q.get('10. change percent', '0%')
                    prev_c = q.get('08. previous close', '?')
                    direction = '+' if float(chg or 0) >= 0 else ''
                    print(sym + ': $' + str(price) + '  ' + direction + str(chg) + ' (' + str(chg_pct) + ')  prev $' + str(prev_c))
    except Exception as e:
        print('Ошибка Alpha Vantage: ' + str(e))


# === РЕЖИМ: get_rates / check_forex / get_exchange_rates / forex_rates ===
if ACTION in ('get_rates', 'check_forex', 'get_exchange_rates', 'forex_rates', 'check_all'):
    print('\n=== 💱 ExchangeRate-API (курсы валют) ===', flush=True)
    PAIRS = _os.environ.get('ER_PAIRS', 'EUR/USD,USD/RUB,GBP/USD,USD/JPY')

    def er_get_pair(base, target):
        url = 'https://v6.exchangerate-api.com/v6/' + ER_KEY + '/pair/' + base + '/' + target
        return _http_get(url)

    try:
        if not ER_KEY:
            print('Ошибка: EXCHANGERATE_API_KEY не задан.')
        else:
            pairs = [p.strip().upper() for p in PAIRS.split(',') if p.strip()]
            if not pairs:
                pairs = ['EUR/USD']
            for pair in pairs[:10]:
                if '/' not in pair:
                    print(pair + ': неверный формат, используй EUR/USD')
                    continue
                base, target = pair.split('/', 1)
                d = er_get_pair(base, target)
                if d.get('result') == 'error':
                    print(pair + ': ошибка — ' + d.get('error-type', '?'))
                else:
                    rate = d.get('conversion_rate', '?')
                    updated = d.get('time_last_update_utc', '')[:16]
                    print(pair + ': ' + str(rate) + '  (' + updated + ')')
    except Exception as e:
        print('Ошибка ExchangeRate-API: ' + str(e))
