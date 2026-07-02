import os
import time
import threading
import sqlite3
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
import psycopg2
from psycopg2.extras import RealDictCursor
import requests
import json

# ==================== CONFIGURATION ====================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

TIMEFRAME_LIVE = '15m'      
TIMEFRAME_MACRO = '1h'      
CANDLE_COUNT = 50          
VOLUME_MULTIPLIER = 1.5    
DB_FILE = "trading_bot.db"

DATABASE_URL = os.getenv("DATABASE_URL")
# =======================================================

bot = telebot.TeleBot(TELEGRAM_TOKEN)

pair_states = {}
active_pairs = []

# =======================================================
# 🗄️ DATABASE ENGINE
# =======================================================

def get_db_connection():
    if DATABASE_URL:
        return psycopg2.connect(DATABASE_URL, sslmode='require')
    else:
        return sqlite3.connect(DB_FILE)

def init_db():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        if DATABASE_URL:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS open_trades (
                    symbol VARCHAR(50) PRIMARY KEY,
                    type VARCHAR(10),
                    entry DOUBLE PRECISION,
                    sl DOUBLE PRECISION,
                    tp DOUBLE PRECISION,
                    time VARCHAR(20)
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS trade_history (
                    id SERIAL PRIMARY KEY,
                    symbol VARCHAR(50),
                    type VARCHAR(10),
                    entry DOUBLE PRECISION,
                    exit DOUBLE PRECISION,
                    result VARCHAR(10),
                    closed_at VARCHAR(20)
                )
            ''')
        else:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS open_trades (
                    symbol TEXT PRIMARY KEY,
                    type TEXT,
                    entry REAL,
                    sl REAL,
                    tp REAL,
                    time TEXT
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS trade_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    type TEXT,
                    entry REAL,
                    exit REAL,
                    result TEXT,
                    closed_at TEXT
                )
            ''')
        conn.commit()
        conn.close()
        print("✅ Database initialized")
    except Exception as e:
        print(f"❌ DB Error: {e}")

def get_open_trades_dict():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT symbol, type, entry, sl, tp, time FROM open_trades")
        rows = cursor.fetchall()
        conn.close()
        
        trades = {}
        for row in rows:
            if len(row) >= 6:
                raw_symbol = str(row[0]) if row[0] is not None else ''
                tipe = str(row[1]) if row[1] is not None else 'UNKNOWN'
                entry = float(row[2]) if row[2] is not None else 0.0
                sl = float(row[3]) if row[3] is not None else 0.0
                tp = float(row[4]) if row[4] is not None else 0.0
                waktu = str(row[5]) if row[5] is not None else ''
                
                if not raw_symbol or entry <= 0 or sl <= 0 or tp <= 0:
                    continue
                
                symbol = raw_symbol.strip()
                if '/' in symbol:
                    parts = symbol.split('/')
                    if len(parts) >= 2 and parts[0]:
                        symbol = f"{parts[0].strip()}-USDT-SWAP"
                elif '-USDT' in symbol and not symbol.endswith('-SWAP'):
                    symbol = f"{symbol}-SWAP"
                elif 'USDT' in symbol and '-' not in symbol:
                    base = symbol.replace('USDT', '').strip()
                    symbol = f"{base}-USDT-SWAP" if base else f"{symbol}-USDT-SWAP"
                elif not symbol.endswith('-SWAP'):
                    symbol = f"{symbol}-USDT-SWAP"
                
                if symbol and len(symbol) > 3:
                    trades[symbol] = {'type': tipe, 'entry': entry, 'sl': sl, 'tp': tp, 'time': waktu}
        return trades
    except Exception as e:
        print(f"❌ Error: {e}")
        return {}

def save_open_trade(symbol, tipe, entry, sl, tp, waktu):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if DATABASE_URL:
            cursor.execute('''
                INSERT INTO open_trades (symbol, type, entry, sl, tp, time) 
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (symbol) 
                DO UPDATE SET type = EXCLUDED.type, entry = EXCLUDED.entry, sl = EXCLUDED.sl, tp = EXCLUDED.tp, time = EXCLUDED.time
            ''', (symbol, tipe, entry, sl, tp, waktu))
        else:
            cursor.execute(
                "INSERT OR REPLACE INTO open_trades (symbol, type, entry, sl, tp, time) VALUES (?, ?, ?, ?, ?, ?)",
                (symbol, tipe, entry, sl, tp, waktu)
            )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"❌ Save error: {e}")

def delete_open_trade(symbol):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if DATABASE_URL:
            cursor.execute("DELETE FROM open_trades WHERE symbol = %s", (symbol,))
        else:
            cursor.execute("DELETE FROM open_trades WHERE symbol = ?", (symbol,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"❌ Delete error: {e}")

def insert_trade_history(symbol, tipe, entry, exit_price, result, closed_at):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if DATABASE_URL:
            cursor.execute('''
                INSERT INTO trade_history (symbol, type, entry, exit, result, closed_at) 
                VALUES (%s, %s, %s, %s, %s, %s)
            ''', (symbol, tipe, entry, exit_price, result, closed_at))
        else:
            cursor.execute('''
                INSERT INTO trade_history (symbol, type, entry, exit, result, closed_at) 
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (symbol, tipe, entry, exit_price, result, closed_at))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"❌ History error: {e}")

def get_recent_history(limit=10):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if DATABASE_URL:
            cursor.execute("SELECT symbol, type, entry, exit, result, closed_at FROM trade_history ORDER BY id DESC LIMIT %s", (limit,))
        else:
            cursor.execute("SELECT symbol, type, entry, exit, result, closed_at FROM trade_history ORDER BY id DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        conn.close()
        history_list = []
        for row in rows:
            symbol, tipe, entry, exit_price, result, closed_at = row
            history_list.append({
                'symbol': symbol, 'type': tipe, 'entry': float(entry), 
                'exit': float(exit_price), 'result': result, 'closed_at': closed_at
            })
        return history_list
    except Exception as e:
        print(f"❌ History read error: {e}")
        return []

def get_all_history():
    """Mengambil semua histori trade untuk perhitungan winrate"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if DATABASE_URL:
            cursor.execute("SELECT symbol, type, entry, exit, result, closed_at FROM trade_history ORDER BY id DESC")
        else:
            cursor.execute("SELECT symbol, type, entry, exit, result, closed_at FROM trade_history ORDER BY id DESC")
        rows = cursor.fetchall()
        conn.close()
        history_list = []
        for row in rows:
            symbol, tipe, entry, exit_price, result, closed_at = row
            history_list.append({
                'symbol': symbol, 'type': tipe, 'entry': float(entry), 
                'exit': float(exit_price), 'result': result, 'closed_at': closed_at
            })
        return history_list
    except Exception as e:
        print(f"❌ History read error: {e}")
        return []

# =======================================================
# 📊 OKX API DIRECT FETCH (TANPA CCXT)
# =======================================================

def fetch_ohlcv_from_okx(symbol, timeframe='15m', limit=100):
    """Fetch OHLCV data langsung dari OKX API"""
    try:
        if not symbol:
            return None
        
        symbol = str(symbol).strip()
        if not symbol:
            return None
        
        api_symbol = symbol
        if api_symbol.endswith('-SWAP'):
            api_symbol = api_symbol.replace('-SWAP', '')
        elif '/' in api_symbol:
            parts = api_symbol.split('/')
            if len(parts) >= 2:
                api_symbol = f"{parts[0].strip()}-{parts[1].strip().replace(':USDT', '')}"
        
        bar_map = {
            '1m': '1m', '3m': '3m', '5m': '5m', '15m': '15m', '30m': '30m',
            '1h': '1H', '2h': '2H', '4h': '4H', '6h': '6H', '12h': '12H',
            '1d': '1D', '1w': '1W', '1M': '1M'
        }
        bar = bar_map.get(timeframe, '15m')
        
        url = f"https://www.okx.com/api/v5/market/candles?instId={api_symbol}&bar={bar}&limit={limit}"
        
        response = requests.get(url, timeout=15)
        if response.status_code == 200:
            data = response.json()
            if data.get('code') == '0' and data.get('data'):
                candles = data['data']
                result = []
                for candle in candles:
                    if len(candle) >= 6:
                        ts = int(candle[0])
                        open_price = float(candle[1])
                        high = float(candle[2])
                        low = float(candle[3])
                        close = float(candle[4])
                        volume = float(candle[5])
                        result.append([ts, open_price, high, low, close, volume])
                result.reverse()
                return result
        return None
    except Exception as e:
        print(f"❌ fetch_ohlcv error: {e}")
        return None

def fetch_price_from_okx(symbol):
    """Fetch current price from OKX API"""
    try:
        if not symbol:
            return None
        symbol = str(symbol).strip()
        if not symbol:
            return None
        
        api_symbol = symbol
        if api_symbol.endswith('-SWAP'):
            api_symbol = api_symbol.replace('-SWAP', '')
        elif '/' in api_symbol:
            parts = api_symbol.split('/')
            if len(parts) >= 2:
                api_symbol = f"{parts[0].strip()}-{parts[1].strip().replace(':USDT', '')}"
        
        try:
            url = f"https://www.okx.com/api/v5/market/ticker?instId={api_symbol}"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == '0' and data.get('data'):
                    ticker_data = data['data'][0]
                    for field in ['last', 'close', 'bidPx', 'askPx']:
                        if field in ticker_data and ticker_data[field]:
                            try:
                                price = float(ticker_data[field])
                                if price > 0:
                                    return price
                            except:
                                continue
        except Exception as e:
            print(f"⚠️ Ticker API error: {e}")
        
        try:
            url = f"https://www.okx.com/api/v5/market/candles?instId={api_symbol}&bar=1m&limit=2"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get('code') == '0' and data.get('data'):
                    candles = data['data']
                    if candles and len(candles) > 0:
                        last_candle = candles[-1]
                        if len(last_candle) >= 5:
                            try:
                                price = float(last_candle[4])
                                if price > 0:
                                    return price
                            except:
                                pass
        except Exception as e:
            print(f"⚠️ OHLCV API error: {e}")
        
        return None
    except Exception as e:
        print(f"❌ fetch_price error: {e}")
        return None

# =======================================================
# 📈 INDICATORS
# =======================================================

def calculate_ema(prices, period=200):
    if not prices or len(prices) < period:
        return 0.0
    k = 2 / (period + 1)
    ema = prices[0]
    for price in prices[1:]:
        ema = (price * k) + (ema * (1 - k))
    return ema

def calculate_rsi(prices, period=14):
    if not prices or len(prices) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(prices)):
        change = prices[i] - prices[i-1]
        gains.append(change if change > 0 else 0.0)
        losses.append(abs(change) if change < 0 else 0.0)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    if avg_loss == 0:
        return 100.0
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + (avg_gain / avg_loss)))

def calculate_atr(candles, period=14):
    if not candles or len(candles) < period + 1:
        return 0.0
    true_ranges = []
    for i in range(1, len(candles)):
        try:
            high = candles[i][2]
            low = candles[i][3]
            prev_close = candles[i-1][4]
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            true_ranges.append(tr)
        except:
            continue
    if len(true_ranges) < period:
        return 0.0
    return sum(true_ranges[-period:]) / period

def get_atr_sl(candles, invalidation, trade_type, atr_multiplier=1.5, fallback_pct=0.5):
    if not candles or len(candles) < 15:
        fallback = invalidation * (1 - fallback_pct/100) if trade_type == 'LONG' else invalidation * (1 + fallback_pct/100)
        return fallback, "flat"
    atr = calculate_atr(candles, period=14)
    if trade_type == 'LONG':
        sl_flat = invalidation * (1 - fallback_pct / 100)
        if atr <= 0:
            return sl_flat, "flat"
        sl_atr = invalidation - atr * atr_multiplier
        return min(sl_atr, sl_flat), "ATR"
    else:
        sl_flat = invalidation * (1 + fallback_pct / 100)
        if atr <= 0:
            return sl_flat, "flat"
        sl_atr = invalidation + atr * atr_multiplier
        return max(sl_atr, sl_flat), "ATR"

# =======================================================
# 📊 WINRATE CALCULATOR
# =======================================================

def calculate_winrate():
    """Menghitung winrate dari semua histori trade yang sudah close"""
    history = get_all_history()
    
    if not history:
        return None, None, None, None
    
    total = len(history)
    wins = sum(1 for t in history if t['result'] in ['TP', 'WIN', 'PROFIT'])
    losses = sum(1 for t in history if t['result'] in ['SL', 'LOSS'])
    
    # Hitung profit/loss per trade
    total_profit = 0
    total_loss = 0
    profit_trades = []
    loss_trades = []
    
    for trade in history:
        if trade['type'] == 'LONG':
            pnl_pct = ((trade['exit'] - trade['entry']) / trade['entry']) * 100
        else:
            pnl_pct = ((trade['entry'] - trade['exit']) / trade['entry']) * 100
        
        if trade['result'] in ['TP', 'WIN', 'PROFIT']:
            total_profit += pnl_pct
            profit_trades.append(pnl_pct)
        else:
            total_loss += abs(pnl_pct)
            loss_trades.append(pnl_pct)
    
    winrate = (wins / total * 100) if total > 0 else 0
    
    # Hitung statistik tambahan
    avg_profit = total_profit / wins if wins > 0 else 0
    avg_loss = total_loss / losses if losses > 0 else 0
    profit_factor = total_profit / total_loss if total_loss > 0 else 0
    net_profit = total_profit - total_loss
    
    # Cari best dan worst trade
    best_trade = max(profit_trades) if profit_trades else 0
    worst_trade = min(loss_trades) if loss_trades else 0
    
    # Hitung per koin
    coin_stats = {}
    for trade in history:
        coin = trade['symbol'].replace('-USDT-SWAP', '').replace('-SWAP', '')
        if coin not in coin_stats:
            coin_stats[coin] = {'wins': 0, 'losses': 0, 'total': 0}
        coin_stats[coin]['total'] += 1
        if trade['result'] in ['TP', 'WIN', 'PROFIT']:
            coin_stats[coin]['wins'] += 1
        else:
            coin_stats[coin]['losses'] += 1
    
    # Sort coin by winrate
    for coin in coin_stats:
        coin_stats[coin]['winrate'] = (coin_stats[coin]['wins'] / coin_stats[coin]['total'] * 100) if coin_stats[coin]['total'] > 0 else 0
    
    top_coins = sorted(coin_stats.items(), key=lambda x: x[1]['wins'], reverse=True)[:5]
    
    return {
        'total': total,
        'wins': wins,
        'losses': losses,
        'winrate': winrate,
        'avg_profit': avg_profit,
        'avg_loss': avg_loss,
        'profit_factor': profit_factor,
        'net_profit': net_profit,
        'best_trade': best_trade,
        'worst_trade': worst_trade,
        'top_coins': top_coins
    }

# =======================================================
# 🤖 TELEGRAM BOT
# =======================================================

def main_menu_keyboard():
    markup = ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(
        KeyboardButton("🎯 Setup Aktif"),
        KeyboardButton("📊 Posisi Open"),
        KeyboardButton("📜 Histori Trade"),
        KeyboardButton("📈 Winrate"),
        KeyboardButton("🤖 Status Sistem")
    )
    return markup

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.send_message(message.chat.id, 
        "👋 *Selamat datang di Dashboard OKX Futures Pro Engine!*\n\n"
        "Gunakan tombol *Reply Keyboard* di bagian bawah layar.\n\n"
        "📌 *Fitur:*\n"
        "• 🎯 Setup Aktif - Lihat sinyal yang menunggu\n"
        "• 📊 Posisi Open - Lihat posisi aktif\n"
        "• 📜 Histori Trade - Riwayat transaksi\n"
        "• 📈 Winrate - Statistik akurasi sinyal\n"
        "• 🤖 Status Sistem - Info bot",
        parse_mode='Markdown')

@bot.message_handler(commands=['backtest_tf'])
def handle_backtest_tf_command(message):
    """Backtest dengan timeframe yang dipilih (3 Strategi)"""
    try:
        args = message.text.split()
        if len(args) < 3:
            bot.reply_to(message, 
                "⚠️ *Format:* `/backtest_tf <KOIN> <TIMEFRAME> [mode]`\n\n"
                "📌 *Contoh:*\n"
                "`/backtest_tf BTC 1h`\n"
                "`/backtest_tf PEPE 4h loose`\n\n"
                "📊 *Timeframe:* `1m,5m,15m,30m,1h,2h,4h,6h,12h,1d,1w`\n\n"
                "🎯 *Strategi:*\n"
                "• `breakout` - Breakout retest (default)\n"
                "• `pullback` - Pullback ke support/resistance\n"
                "• `bounce` - Support/Resistance bounce\n"
                "• `all` - Semua strategi digabung",
                parse_mode='Markdown'
            )
            return

        coin_name = args[1].upper().strip()
        timeframe = args[2].lower().strip()
        
        # Cek mode dan strategi
        mode = 'normal'
        strategy = 'breakout'
        
        if len(args) >= 4:
            if args[3].lower() in ['loose', 'normal', 'tight']:
                mode = args[3].lower()
                if len(args) >= 5:
                    strategy = args[4].lower()
            else:
                strategy = args[3].lower()
                if len(args) >= 5:
                    mode = args[4].lower()
        
        # Validasi timeframe
        valid_tf = ['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '12h', '1d', '1w']
        if timeframe not in valid_tf:
            bot.reply_to(message, f"❌ Timeframe `{timeframe}` tidak valid.", parse_mode='Markdown')
            return

        # Validasi strategi
        valid_strategies = ['breakout', 'pullback', 'bounce', 'all']
        if strategy not in valid_strategies:
            bot.reply_to(message, f"❌ Strategi `{strategy}` tidak valid. Gunakan: {', '.join(valid_strategies)}", parse_mode='Markdown')
            return

        symbol = f"{coin_name}-USDT-SWAP"
        loading_msg = bot.reply_to(message, 
            f"⏳ _Backtest {symbol} - {timeframe} | Mode: {mode} | Strategi: {strategy}..._", 
            parse_mode='Markdown'
        )

        # Set limit berdasarkan timeframe
        if timeframe in ['1m', '3m', '5m']:
            limit = 500
        elif timeframe in ['15m', '30m']:
            limit = 400
        else:
            limit = 300

        candles = fetch_ohlcv_from_okx(symbol, timeframe=timeframe, limit=limit)
        
        if not candles or len(candles) < 50:
            bot.edit_message_text(
                f"❌ Data tidak mencukupi untuk `{symbol}`.",
                chat_id=message.chat.id,
                message_id=loading_msg.message_id,
                parse_mode='Markdown'
            )
            return

        # ========== PARAMETER ==========
        if mode == 'loose':
            vol_multiplier = 1.2
            rsi_bullish = 70
            rsi_bearish = 30
            retest_range = 1.01
            rejection_ratio = 0.5
            rr_ratio = 1.5
            ema_period = 20
            min_candles = 15
        elif mode == 'tight':
            vol_multiplier = 2.0
            rsi_bullish = 40
            rsi_bearish = 60
            retest_range = 1.002
            rejection_ratio = 1.5
            rr_ratio = 2.5
            ema_period = 50
            min_candles = 30
        else:
            vol_multiplier = 1.5
            rsi_bullish = 55
            rsi_bearish = 45
            retest_range = 1.005
            rejection_ratio = 1.0
            rr_ratio = 2.0
            ema_period = 30
            min_candles = 20

        # ========== BACKTEST ENGINE ==========
        total_trades, wins, losses = 0, 0, 0
        trade_list = []
        signal_list = []
        strategy_stats = {'breakout': 0, 'pullback': 0, 'bounce': 0}
        strategy_wins = {'breakout': 0, 'pullback': 0, 'bounce': 0}

        start_idx = min(50, len(candles) // 4)

        for i in range(start_idx, len(candles)):
            current_candle = candles[i]
            prev_candle = candles[i-1]
            current_high, current_low, current_close = current_candle[2], current_candle[3], current_candle[4]
            current_open = current_candle[1]
            prev_close = prev_candle[4]
            
            # Histori candle
            hist_candles = candles[i - 30 : i - 1] if i >= 30 else candles[:i-1]
            if not hist_candles or len(hist_candles) < min_candles:
                continue
            
            # Level Support/Resistance
            resistance = max([c[2] for c in hist_candles])
            support = min([c[3] for c in hist_candles])
            range_size = resistance - support
            
            # Volume
            avg_volume = sum([c[5] for c in hist_candles]) / len(hist_candles)
            breakout_volume = candles[i-1][5]
            volume_valid = breakout_volume > (avg_volume * vol_multiplier)
            
            # Indikator
            local_closes = [c[4] for c in candles[:i]]
            current_ema = calculate_ema(local_closes, period=ema_period)
            current_rsi = calculate_rsi(local_closes, period=14)
            
            # Deteksi kondisi market
            is_ranging = range_size < (resistance * 0.02)  # Range < 2%
            is_trending = range_size >= (resistance * 0.03)  # Range >= 3%

            if not current_ema or not current_rsi:
                continue

            # ========================================
            # STRATEGI 1: BREAKOUT RETEST
            # ========================================
            if strategy in ['breakout', 'all']:
                state = 'NONE'
                trigger_level = 0.0
                sl_level, tp_level = 0.0, 0.0
                
                # Deteksi breakout
                if prev_close > resistance and volume_valid and current_close > current_ema and current_rsi < rsi_bullish:
                    trigger_level = resistance
                    # Cek retest di candle berikutnya
                    if i + 1 < len(candles):
                        next_candle = candles[i + 1]
                        next_low = next_candle[3]
                        next_close = next_candle[4]
                        next_open = next_candle[1]
                        
                        body_size = abs(next_close - next_open)
                        lower_wick = min(next_open, next_close) - next_low
                        
                        retest_touched = next_low <= trigger_level * retest_range
                        retest_held = next_close > trigger_level * (2 - retest_range)
                        rejection_valid = next_close > next_open and lower_wick > (body_size * rejection_ratio)
                        
                        if retest_touched and retest_held and rejection_valid:
                            entry = next_close
                            sl = support * 0.995
                            risk = entry - sl
                            if risk <= 0:
                                risk = entry * 0.005
                            tp = entry + (risk * rr_ratio)
                            
                            total_trades += 1
                            strategy_stats['breakout'] += 1
                            trade_list.append({
                                'entry': entry,
                                'sl': sl,
                                'tp': tp,
                                'type': 'LONG',
                                'strategy': 'breakout',
                                'time': i + 1
                            })
                            
                            # Simulasikan hasil
                            if next_candle[3] <= sl:
                                losses += 1
                            elif next_candle[2] >= tp:
                                wins += 1
                                strategy_wins['breakout'] += 1
                
                elif prev_close < support and volume_valid and current_close < current_ema and current_rsi > rsi_bearish:
                    trigger_level = support
                    if i + 1 < len(candles):
                        next_candle = candles[i + 1]
                        next_high = next_candle[2]
                        next_close = next_candle[4]
                        next_open = next_candle[1]
                        
                        body_size = abs(next_close - next_open)
                        upper_wick = next_high - max(next_open, next_close)
                        
                        retest_touched = next_high >= trigger_level * (2 - retest_range)
                        retest_held = next_close < trigger_level * retest_range
                        rejection_valid = next_close < next_open and upper_wick > (body_size * rejection_ratio)
                        
                        if retest_touched and retest_held and rejection_valid:
                            entry = next_close
                            sl = resistance * 1.005
                            risk = sl - entry
                            if risk <= 0:
                                risk = entry * 0.005
                            tp = entry - (risk * rr_ratio)
                            
                            total_trades += 1
                            strategy_stats['breakout'] += 1
                            trade_list.append({
                                'entry': entry,
                                'sl': sl,
                                'tp': tp,
                                'type': 'SHORT',
                                'strategy': 'breakout',
                                'time': i + 1
                            })

            # ========================================
            # STRATEGI 2: PULLBACK (Untuk Ranging Market)
            # ========================================
            if strategy in ['pullback', 'all']:
                # Harga di atas EMA (uptrend) dan pullback ke support
                if current_close > current_ema and current_low <= support * 1.005 and current_rsi < 50:
                    if i + 1 < len(candles):
                        next_candle = candles[i + 1]
                        next_close = next_candle[4]
                        next_open = next_candle[1]
                        
                        # Candle hijau = bounce
                        if next_close > next_open and next_close > current_close:
                            entry = next_close
                            sl = support * 0.99
                            risk = entry - sl
                            if risk <= 0:
                                risk = entry * 0.005
                            tp = entry + (risk * rr_ratio * 1.2)
                            
                            total_trades += 1
                            strategy_stats['pullback'] += 1
                            trade_list.append({
                                'entry': entry,
                                'sl': sl,
                                'tp': tp,
                                'type': 'LONG',
                                'strategy': 'pullback',
                                'time': i + 1
                            })
                
                # Harga di bawah EMA (downtrend) dan pullback ke resistance
                elif current_close < current_ema and current_high >= resistance * 0.995 and current_rsi > 50:
                    if i + 1 < len(candles):
                        next_candle = candles[i + 1]
                        next_close = next_candle[4]
                        next_open = next_candle[1]
                        
                        if next_close < next_open and next_close < current_close:
                            entry = next_close
                            sl = resistance * 1.01
                            risk = sl - entry
                            if risk <= 0:
                                risk = entry * 0.005
                            tp = entry - (risk * rr_ratio * 1.2)
                            
                            total_trades += 1
                            strategy_stats['pullback'] += 1
                            trade_list.append({
                                'entry': entry,
                                'sl': sl,
                                'tp': tp,
                                'type': 'SHORT',
                                'strategy': 'pullback',
                                'time': i + 1
                            })

            # ========================================
            # STRATEGI 3: SUPPORT/RESISTANCE BOUNCE (Untuk Ranging)
            # ========================================
            if strategy in ['bounce', 'all'] and is_ranging:
                # Bounce dari support
                if current_low <= support * 1.01 and current_close > current_open and current_rsi < 50:
                    if i + 1 < len(candles):
                        next_candle = candles[i + 1]
                        next_close = next_candle[4]
                        
                        if next_close > current_close:
                            entry = next_close
                            sl = support * 0.99
                            risk = entry - sl
                            if risk <= 0:
                                risk = entry * 0.005
                            tp = entry + (risk * rr_ratio)
                            
                            total_trades += 1
                            strategy_stats['bounce'] += 1
                            trade_list.append({
                                'entry': entry,
                                'sl': sl,
                                'tp': tp,
                                'type': 'LONG',
                                'strategy': 'bounce',
                                'time': i + 1
                            })
                
                # Bounce dari resistance
                elif current_high >= resistance * 0.99 and current_close < current_open and current_rsi > 50:
                    if i + 1 < len(candles):
                        next_candle = candles[i + 1]
                        next_close = next_candle[4]
                        
                        if next_close < current_close:
                            entry = next_close
                            sl = resistance * 1.01
                            risk = sl - entry
                            if risk <= 0:
                                risk = entry * 0.005
                            tp = entry - (risk * rr_ratio)
                            
                            total_trades += 1
                            strategy_stats['bounce'] += 1
                            trade_list.append({
                                'entry': entry,
                                'sl': sl,
                                'tp': tp,
                                'type': 'SHORT',
                                'strategy': 'bounce',
                                'time': i + 1
                            })

        # ========== HITUNG STATISTIK ==========
        winrate = (wins / total_trades * 100) if total_trades > 0 else 0.0
        
        total_profit = 0
        total_loss = 0
        for trade in trade_list:
            if trade['type'] == 'LONG':
                pnl = ((trade['tp'] - trade['entry']) / trade['entry']) * 100
            else:
                pnl = ((trade['entry'] - trade['tp']) / trade['entry']) * 100
            
            if pnl > 0:
                total_profit += pnl
            else:
                total_loss += abs(pnl)
        
        avg_profit = total_profit / wins if wins > 0 else 0
        avg_loss = total_loss / losses if losses > 0 else 0
        profit_factor = total_profit / total_loss if total_loss > 0 else 0

        # ========== BUAT LAPORAN ==========
        if total_trades > 0:
            report_text = (
                f"📊 *LAPORAN BACKTEST*\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"🔹 *Aset:* `{symbol}`\n"
                f"🔹 *Timeframe:* `{timeframe}`\n"
                f"🔹 *Mode:* `{mode}` | *Strategi:* `{strategy}`\n"
                f"🔹 *Periode:* {len(candles)} candle\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"📈 *Total Sinyal:* `{total_trades}`\n"
                f"🟢 *Win (TP):* `{wins}`\n"
                f"🔴 *Loss (SL):* `{losses}`\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"🎯 *WIN RATE:* **{winrate:.1f}%**\n\n"
                f"💰 *Rata-rata Profit:* `+{avg_profit:.2f}%`\n"
                f"💰 *Rata-rata Loss:* `{avg_loss:.2f}%`\n"
                f"📊 *Profit Factor:* `{profit_factor:.2f}`\n"
                f"📈 *Net Profit:* `{total_profit - total_loss:+.2f}%`\n"
            )
            
            # Statistik per strategi
            if strategy == 'all':
                report_text += f"\n📊 *Performa per Strategi:*\n"
                for s, count in strategy_stats.items():
                    win_count = strategy_wins.get(s, 0)
                    s_winrate = (win_count / count * 100) if count > 0 else 0
                    report_text += f"  • {s.capitalize()}: {count} sinyal ({s_winrate:.0f}% win)\n"
            
            # 5 Sinyal terakhir
            if trade_list:
                report_text += f"\n📋 *5 Sinyal Terakhir:*\n"
                for idx, trade in enumerate(trade_list[-5:], 1):
                    if trade['type'] == 'LONG':
                        pnl_pct = ((trade['tp'] - trade['entry']) / trade['entry']) * 100
                    else:
                        pnl_pct = ((trade['entry'] - trade['tp']) / trade['entry']) * 100
                    emoji = "🟢" if pnl_pct > 0 else "🔴"
                    report_text += (
                        f"{idx}. {trade['type']} [{trade['strategy']}] | "
                        f"Entry: `{trade['entry']:.4f}` | "
                        f"TP: `{trade['tp']:.4f}` | {emoji} {pnl_pct:+.1f}%\n"
                    )
            
            # Analisis
            report_text += f"\n━━━━━━━━━━━━━━━━━━━━━\n"
            if winrate >= 60:
                report_text += "✅ *Analisis:* WINRATE SANGAT BAGUS! Lanjutkan strategi ini."
            elif winrate >= 45:
                report_text += "📌 *Analisis:* WINRATE CUKUP BAIK. Bisa dioptimalkan lagi."
            elif winrate >= 30:
                report_text += "⚠️ *Analisis:* WINRATE RENDAH. Perlu filter tambahan."
            else:
                report_text += "❌ *Analisis:* WINRATE SANGAT RENDAH. Coba strategi lain."
                
        else:
            report_text = (
                f"📊 *LAPORAN BACKTEST*\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"🔹 *Aset:* `{symbol}`\n"
                f"🔹 *Timeframe:* `{timeframe}`\n"
                f"🔹 *Mode:* `{mode}` | *Strategi:* `{strategy}`\n"
                f"🔹 *Total candle:* {len(candles)}\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"❌ *Tidak ada sinyal valid*\n\n"
                f"💡 *Saran:*\n"
                f"• Coba strategi `pullback`: `/backtest_tf {coin_name} {timeframe} pullback`\n"
                f"• Coba strategi `bounce`: `/backtest_tf {coin_name} {timeframe} bounce`\n"
                f"• Coba semua strategi: `/backtest_tf {coin_name} {timeframe} all`\n"
                f"• Coba mode `loose`: `/backtest_tf {coin_name} {timeframe} loose`\n"
                f"• Coba timeframe lebih besar: `/backtest_tf {coin_name} 1h`"
            )
        
        try: 
            bot.delete_message(message.chat.id, loading_msg.message_id)
        except: 
            pass
            
        bot.reply_to(message, report_text, parse_mode='Markdown')

    except Exception as e:
        try: 
            bot.delete_message(message.chat.id, loading_msg.message_id)
        except: 
            pass
        bot.reply_to(message, f"❌ *Error Backtest:* `{str(e)}`", parse_mode='Markdown')
        import traceback
        traceback.print_exc()

@bot.message_handler(commands=['backtest_help'])
def backtest_help_command(message):
    """Help untuk command backtest"""
    help_text = (
        "📖 *PANDUAN BACKTEST*\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📌 *Cara Penggunaan:*\n"
        "`/backtest_tf <KOIN> <TIMEFRAME>`\n\n"
        "📊 *Contoh:*\n"
        "• `/backtest_tf BTC 1h` - Backtest BTC 1 jam\n"
        "• `/backtest_tf PEPE 15m` - Backtest PEPE 15 menit\n"
        "• `/backtest_tf ETH 4h` - Backtest ETH 4 jam\n\n"
        "⏰ *Timeframe Tersedia:*\n"
        "`1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d, 1w`\n\n"
        "💡 *Tips:*\n"
        "• Timeframe kecil (15m) = lebih banyak sinyal\n"
        "• Timeframe besar (4h) = sinyal lebih akurat\n"
        "• Coba berbagai pair untuk cari yang paling profit\n\n"
        "📈 *Parameter Backtest:*\n"
        "• Volume Multiplier: 1.5x\n"
        "• Risk Reward: 2:1\n"
        "• RSI Range: 40-60\n"
        "• EMA 50 sebagai trend filter"
    )
    bot.reply_to(message, help_text, parse_mode='Markdown')

@bot.message_handler(commands=['winrate'])
def winrate_command(message):
    """Command untuk melihat winrate"""
    send_winrate(message)

def send_winrate(message):
    """Mengirim laporan winrate"""
    try:
        loading_msg = bot.send_message(message.chat.id, "⏳ _Menghitung statistik..._", parse_mode='Markdown')
        
        stats = calculate_winrate()
        
        if not stats or stats['total'] == 0:
            bot.edit_message_text(
                "📊 *BELUM ADA DATA TRADE CLOSE*\n\n"
                "Belum ada histori trade yang sudah close.\n"
                "Tunggu hingga ada posisi yang mencapai TP atau SL.",
                chat_id=message.chat.id,
                message_id=loading_msg.message_id,
                parse_mode='Markdown'
            )
            return
        
        # Format laporan
        report = f"📊 *LAPORAN WINRATE*\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        report += f"📈 *Total Sinyal Close:* {stats['total']}\n"
        report += f"✅ *Win (TP):* {stats['wins']}\n"
        report += f"❌ *Loss (SL):* {stats['losses']}\n"
        report += f"━━━━━━━━━━━━━━━━━━━━━\n"
        report += f"🎯 *WIN RATE:* **{stats['winrate']:.2f}%**\n\n"
        
        report += f"💰 *Rata-rata Profit:* +{stats['avg_profit']:.2f}%\n"
        report += f"💰 *Rata-rata Loss:* {stats['avg_loss']:.2f}%\n"
        report += f"📊 *Profit Factor:* {stats['profit_factor']:.2f}\n"
        report += f"📈 *Net Profit:* {stats['net_profit']:+.2f}%\n\n"
        
        report += f"🏆 *Best Trade:* +{stats['best_trade']:.2f}%\n"
        report += f"📉 *Worst Trade:* {stats['worst_trade']:.2f}%\n\n"
        
        if stats['top_coins']:
            report += "🪙 *Top 5 Koin Terbaik:*\n"
            for coin, data in stats['top_coins']:
                report += f"• {coin}: {data['wins']}/{data['total']} ({data['winrate']:.1f}%)\n"
        
        try:
            bot.delete_message(message.chat.id, loading_msg.message_id)
        except:
            pass
        
        bot.send_message(message.chat.id, report, parse_mode='Markdown')
        
    except Exception as e:
        print(f"❌ Winrate error: {e}")
        bot.reply_to(message, f"❌ Error: `{str(e)}`", parse_mode='Markdown')

@bot.message_handler(commands=['test_api'])
def test_api_command(message):
    try:
        args = message.text.split()
        if len(args) < 2:
            bot.reply_to(message, "⚠️ Format: `/test_api <SYMBOL>`\nContoh: `/test_api BTC`", parse_mode='Markdown')
            return
        coin = args[1].upper().strip()
        symbols = [f"{coin}-USDT", f"{coin}/USDT:USDT", f"{coin}USDT", f"{coin}-USDT-SWAP"]
        results = [f"🔍 *TESTING API OKX UNTUK {coin}*\n━━━━━━━━━━━━━━━━━━━━━\n"]
        for sym in symbols:
            results.append(f"📊 *Symbol: {sym}*")
            price = fetch_price_from_okx(sym)
            if price and price > 0:
                results.append(f"  ✅ Price: `{price:.4f}`")
            else:
                results.append(f"  ❌ No price found")
            results.append("")
        bot.reply_to(message, "\n".join(results), parse_mode='Markdown')
    except Exception as e:
        bot.reply_to(message, f"❌ Error: `{str(e)}`", parse_mode='Markdown')

@bot.message_handler(func=lambda msg: True)
def handle_reply_keyboard(message):
    text = message.text
    if text == "🎯 Setup Aktif":
        send_active_patterns(message)
    elif text == "📊 Posisi Open":
        send_open_positions(message)
    elif text == "📜 Histori Trade":
        send_trade_history(message)
    elif text == "📈 Winrate":
        send_winrate(message)
    elif text == "🤖 Status Sistem":
        db_type = "PostgreSQL (Railway)" if DATABASE_URL else "SQLite (Fallback)"
        bot.reply_to(message, 
            f"✅ *Bot Status:* Online\n"
            f"📊 *Engine:* Memantau {len(active_pairs)} koin\n"
            f"🗄️ *Database:* {db_type}\n"
            f"🔄 *Mode:* Direct API (No CCXT)",
            parse_mode='Markdown')

def send_active_patterns(message):
    waiting_retest = [s for s, d in pair_states.items() if d['status'] in ['BREAKOUT_BULLISH', 'BREAKOUT_BEARISH']]
    if not waiting_retest:
        bot.send_message(message.chat.id, "⏳ *Bersih.* Tidak ada setup menunggu retest.", parse_mode='Markdown')
        return
    text = "🎯 *Setup Menunggu Retest:*\n━━━━━━━━━━━━━━━━━━━━━\n\n"
    for symbol in waiting_retest:
        status = pair_states[symbol]['status']
        level = pair_states[symbol]['level']
        emoji = "🚀 LONG" if "BULLISH" in status else "💥 SHORT"
        coin = symbol.replace('-USDT-SWAP', '').replace('-SWAP', '')
        text += f"• *{coin}*: {emoji} | Key: `{level:.4f}`\n"
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

def send_open_positions(message):
    try:
        loading_msg = bot.send_message(message.chat.id, "⏳ _Mengambil data..._", parse_mode='Markdown')
        open_trades = get_open_trades_dict()
        
        if not open_trades:
            bot.edit_message_text("📭 *Tidak ada posisi aktif.*", chat_id=message.chat.id, message_id=loading_msg.message_id, parse_mode='Markdown')
            return
        
        try:
            bot.delete_message(message.chat.id, loading_msg.message_id)
        except:
            pass
        
        messages = []
        current_msg = "📊 *POSISI OPEN:*\n━━━━━━━━━━━━━━━━━━━━━\n"
        has_data = False
        
        for symbol, data in open_trades.items():
            try:
                tipe = data.get('type', 'UNKNOWN')
                entry = float(data.get('entry', 0))
                sl = float(data.get('sl', 0))
                tp = float(data.get('tp', 0))
                
                if entry <= 0 or sl <= 0 or tp <= 0:
                    continue
                
                current_price = entry
                price = fetch_price_from_okx(symbol)
                if price and price > 0:
                    current_price = price
                
                if tipe.upper() == 'LONG':
                    pnl_pct = ((current_price - entry) / entry) * 100
                    emoji = "🟢 LONG"
                else:
                    pnl_pct = ((entry - current_price) / entry) * 100
                    emoji = "🔴 SHORT"
                
                pnl_status = f"✅ *+{pnl_pct:.2f}%*" if pnl_pct >= 0 else f"❌ *{pnl_pct:.2f}%*"
                coin = symbol.replace('-USDT-SWAP', '').replace('-SWAP', '')
                
                pos_text = (
                    f"• *{coin}* ({emoji})\n"
                    f"  📥 Entry: `{entry:.4f}`\n"
                    f"  ⚡ Current: `{current_price:.4f}`\n"
                    f"  🛑 SL: `{sl:.4f}` | 🎯 TP: `{tp:.4f}`\n"
                    f"  💰 PnL: {pnl_status}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                )
                
                if len(current_msg) + len(pos_text) > 4000:
                    messages.append(current_msg)
                    current_msg = "📊 *POSISI OPEN (LANJUTAN):*\n━━━━━━━━━━━━━━━━━━━━━\n"
                
                current_msg += pos_text
                has_data = True
                
            except Exception as e:
                print(f"Error processing {symbol}: {e}")
                continue
        
        if current_msg and current_msg != "📊 *POSISI OPEN:*\n━━━━━━━━━━━━━━━━━━━━━\n":
            messages.append(current_msg)
        
        if has_data:
            for msg in messages:
                bot.send_message(message.chat.id, msg, parse_mode='Markdown')
        else:
            bot.send_message(message.chat.id, "❌ *Tidak ada data posisi valid.*", parse_mode='Markdown')
            
    except Exception as e:
        bot.reply_to(message, f"❌ Error: `{str(e)}`", parse_mode='Markdown')

def send_trade_history(message):
    history = get_recent_history(10)
    if not history:
        bot.send_message(message.chat.id, "📜 *Belum ada histori transaksi.*", parse_mode='Markdown')
        return
    
    messages = []
    current_msg = f"📜 *HISTORI TERAKHIR ({len(history)}):*\n━━━━━━━━━━━━━━━━━━━━━\n"
    
    for data in history:
        try:
            coin = data['symbol'].replace('-USDT-SWAP', '').replace('-SWAP', '')
            tipe = data['type']
            entry = data['entry']
            exit_price = data['exit']
            
            if tipe == 'LONG':
                pnl_pct = ((exit_price - entry) / entry) * 100
            else:
                pnl_pct = ((entry - exit_price) / entry) * 100
            
            emoji = "🟢" if data['result'] in ['TP', 'WIN', 'PROFIT'] else "🔴"
            result_text = f"{emoji} *{data['result']}* ({pnl_pct:+.2f}%)"
            
            pos_text = (
                f"• *{coin}* {result_text}\n"
                f"  📥 Entry: `{entry:.4f}` | 🚪 Exit: `{exit_price:.4f}`\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
            )
            
            if len(current_msg) + len(pos_text) > 4000:
                messages.append(current_msg)
                current_msg = f"📜 *HISTORI (LANJUTAN):*\n━━━━━━━━━━━━━━━━━━━━━\n"
            
            current_msg += pos_text
            
        except Exception as e:
            print(f"Error: {e}")
            continue
    
    if current_msg and current_msg != f"📜 *HISTORI TERAKHIR ({len(history)}):*\n━━━━━━━━━━━━━━━━━━━━━\n":
        messages.append(current_msg)
    
    for msg in messages:
        bot.send_message(message.chat.id, msg, parse_mode='Markdown')

# =======================================================
# 🔍 SCANNER DENGAN FILTER TAMBAHAN
# =======================================================

def scan_breakout_retest(symbol):
    global pair_states
    try:
        if not symbol:
            return
        
        candles = fetch_ohlcv_from_okx(symbol, timeframe=TIMEFRAME_LIVE, limit=150)  # Tambah limit
        if not candles or len(candles) < CANDLE_COUNT + 50:
            return
        
        current_candle = candles[-1]
        prev_candle = candles[-2]
        current_close = current_candle[4]
        current_low = current_candle[3]
        current_high = current_candle[2]
        current_open = current_candle[1]
        prev_close = prev_candle[4]
        
        # ========== FILTER 1: CEK TREND JANGKA PANJANG (4H) ==========
        try:
            trend_candles = fetch_ohlcv_from_okx(symbol, timeframe='4h', limit=50)
            if trend_candles and len(trend_candles) > 30:
                trend_ema50 = calculate_ema([c[4] for c in trend_candles], period=50)
                current_trend_price = trend_candles[-1][4]
                trend_up = current_trend_price > trend_ema50
            else:
                trend_up = None
        except:
            trend_up = None
        
        # ========== FILTER 2: CEK VOLATILITAS ==========
        hist_candles = candles[-52:-2]
        if not hist_candles:
            return
        
        avg_body = sum([abs(c[1] - c[4]) for c in hist_candles[-10:]]) / 10
        current_body = abs(current_open - current_close)
        if current_body < avg_body * 0.4:  # Filter: body terlalu kecil
            return
        
        # ========== FILTER 3: TIME FILTER ==========
        current_hour = int(time.strftime("%H"))
        # Hindari jam volatil tinggi (market open)
        if current_hour in [7, 8, 9, 12, 13, 20, 21, 22]:
            return
        
        if symbol not in pair_states:
            pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}
        
        waktu = time.strftime("%H:%M:%S")
        open_trades = get_open_trades_dict()
        
        # ... (kode monitoring posisi yang sama) ...
        
        # ========== ANALISIS ==========
        resistance = max([c[2] for c in hist_candles])
        support = min([c[3] for c in hist_candles])
        avg_vol = sum([c[5] for c in hist_candles]) / len(hist_candles)
        
        # Volume breakout harus 2x lebih tinggi
        VOLUME_MULTIPLIER = 2.0  # Dari 1.5 ke 2.0
        vol_valid = prev_candle[5] > (avg_vol * VOLUME_MULTIPLIER)
        
        rsi = calculate_rsi([c[4] for c in candles], period=14)
        
        # Macro EMA
        macro_candles = fetch_ohlcv_from_okx(symbol, timeframe=TIMEFRAME_MACRO, limit=205)
        if not macro_candles:
            return
        macro_closes = [c[4] for c in macro_candles]
        ema200 = calculate_ema(macro_closes, period=200)
        
        # ========== BULLISH BREAKOUT DENGAN FILTER LEBIH KETAT ==========
        # Syarat: RSI < 40 (oversold), volume tinggi, harga di atas EMA200, dan trend naik
        if (prev_close > resistance and vol_valid and 
            current_close > ema200 and rsi < 40 and 
            (trend_up is None or trend_up)):
            
            if pair_states[symbol]['status'] != 'BREAKOUT_BULLISH':
                pair_states[symbol] = {'status': 'BREAKOUT_BULLISH', 'level': resistance, 'sl': support, 'tp': 0.0}
                bot.send_message(TELEGRAM_CHAT_ID, 
                    f"🚀 *BULLISH BREAKOUT (HIGH PROBABILITY)*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Pair: `{symbol.replace('-USDT-SWAP', '')}`\n"
                    f"📊 Level: `{resistance:.4f}`\n"
                    f"📈 RSI: {rsi:.1f} | Trend: {'✅ Up' if trend_up else '❌ Down'}\n"
                    f"⏳ *Menunggu konfirmasi retest...*",
                    parse_mode='Markdown')
        
        elif pair_states[symbol]['status'] == 'BREAKOUT_BULLISH':
            target = pair_states[symbol]['level']
            body = abs(current_close - current_open)
            lower_wick = min(current_open, current_close) - current_low
            
            # ========== RETEST DENGAN KONFIRMASI LEBIH KETAT ==========
            # Cek berapa kali harga menyentuh level dalam 5 candle terakhir
            retest_count = 0
            for i in range(-5, 0):
                if candles[i][3] <= target * 1.002:
                    retest_count += 1
            
            if (current_low <= target * 1.002 and 
                current_close > target * 0.998 and 
                current_close > current_open and 
                lower_wick > (body * 1.5) and  # Wick harus 1.5x body (dari 1.2)
                retest_count >= 2):  # Minimal 2 kali retest
                
                sl, method = get_atr_sl(candles, pair_states[symbol]['sl'], 'LONG')
                risk = current_close - sl
                if risk <= 0:
                    risk = current_close * 0.005
                tp = current_close + (risk * 2.5)  # Risk Reward 2.5:1 (dari 2:1)
                
                pair_states[symbol] = {'status': 'IN_LONG', 'level': target, 'sl': sl, 'tp': tp}
                save_open_trade(symbol, 'LONG', current_close, sl, tp, waktu)
                
                atr = calculate_atr(candles, period=14)
                atr_info = f"ATR14={atr:.4f}" if atr > 0 else "ATR=N/A"
                bot.send_message(TELEGRAM_CHAT_ID, 
                    f"🎯 *ENTRY LONG CONFIRMED (HIGH PROBABILITY)*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Pair: `{symbol.replace('-USDT-SWAP', '')}`\n"
                    f"📥 Entry: `{current_close:.4f}`\n"
                    f"🛑 SL: `{sl:.4f}` ({method})\n"
                    f"🎯 TP: `{tp:.4f}` (RR: 2.5:1)\n"
                    f"📊 {atr_info}\n"
                    f"🔄 Retest: {retest_count}x",
                    parse_mode='Markdown')
        
        # ========== BEARISH BREAKDOWN DENGAN FILTER LEBIH KETAT ==========
        # Syarat: RSI > 60 (overbought), volume tinggi, harga di bawah EMA200, dan trend turun
        elif (prev_close < support and vol_valid and 
              current_close < ema200 and rsi > 60 and 
              (trend_up is None or not trend_up)):
            
            if pair_states[symbol]['status'] != 'BREAKOUT_BEARISH':
                pair_states[symbol] = {'status': 'BREAKOUT_BEARISH', 'level': support, 'sl': resistance, 'tp': 0.0}
                bot.send_message(TELEGRAM_CHAT_ID, 
                    f"💥 *BEARISH BREAKDOWN (HIGH PROBABILITY)*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Pair: `{symbol.replace('-USDT-SWAP', '')}`\n"
                    f"📊 Level: `{support:.4f}`\n"
                    f"📉 RSI: {rsi:.1f} | Trend: {'✅ Down' if not trend_up else '❌ Up'}\n"
                    f"⏳ *Menunggu konfirmasi retest...*",
                    parse_mode='Markdown')
        
        elif pair_states[symbol]['status'] == 'BREAKOUT_BEARISH':
            target = pair_states[symbol]['level']
            body = abs(current_close - current_open)
            upper_wick = current_high - max(current_open, current_close)
            
            # Cek berapa kali harga menyentuh level dalam 5 candle terakhir
            retest_count = 0
            for i in range(-5, 0):
                if candles[i][2] >= target * 0.998:
                    retest_count += 1
            
            if (current_high >= target * 0.998 and 
                current_close < target * 1.002 and 
                current_close < current_open and 
                upper_wick > (body * 1.5) and
                retest_count >= 2):
                
                sl, method = get_atr_sl(candles, pair_states[symbol]['sl'], 'SHORT')
                risk = sl - current_close
                if risk <= 0:
                    risk = current_close * 0.005
                tp = current_close - (risk * 2.5)
                
                pair_states[symbol] = {'status': 'IN_SHORT', 'level': target, 'sl': sl, 'tp': tp}
                save_open_trade(symbol, 'SHORT', current_close, sl, tp, waktu)
                
                atr = calculate_atr(candles, period=14)
                atr_info = f"ATR14={atr:.4f}" if atr > 0 else "ATR=N/A"
                bot.send_message(TELEGRAM_CHAT_ID, 
                    f"🎯 *ENTRY SHORT CONFIRMED (HIGH PROBABILITY)*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"Pair: `{symbol.replace('-USDT-SWAP', '')}`\n"
                    f"📥 Entry: `{current_close:.4f}`\n"
                    f"🛑 SL: `{sl:.4f}` ({method})\n"
                    f"🎯 TP: `{tp:.4f}` (RR: 2.5:1)\n"
                    f"📊 {atr_info}\n"
                    f"🔄 Retest: {retest_count}x",
                    parse_mode='Markdown')
        
        # Reset jika breakout gagal
        if pair_states[symbol]['status'] == 'BREAKOUT_BULLISH':
            target = pair_states[symbol]['level']
            if current_close < target * 0.995:
                pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}
        elif pair_states[symbol]['status'] == 'BREAKOUT_BEARISH':
            target = pair_states[symbol]['level']
            if current_close > target * 1.005:
                pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}
                
    except Exception as e:
        print(f"Scan error {symbol}: {e}")
        if symbol in pair_states and pair_states[symbol]['status'] not in ['IN_LONG', 'IN_SHORT']:
            pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}

# =======================================================
# 🚀 MAIN
# =======================================================

def main():
    print("=" * 50)
    print("🚀 Starting OKX Bot (No CCXT - Direct API)")
    print("=" * 50)
    
    init_db()
    
    global active_pairs
    active_pairs = [
        'BTC-USDT-SWAP', 'ETH-USDT-SWAP', 'SOL-USDT-SWAP', 'XRP-USDT-SWAP', 'ADA-USDT-SWAP',
        'AVAX-USDT-SWAP', 'DOT-USDT-SWAP', 'DOGE-USDT-SWAP', 'SHIB-USDT-SWAP', 'LINK-USDT-SWAP',
        'NEAR-USDT-SWAP', 'MATIC-USDT-SWAP', 'LTC-USDT-SWAP', 'TRX-USDT-SWAP', 'UNI-USDT-SWAP',
        'APT-USDT-SWAP', 'OP-USDT-SWAP', 'ARB-USDT-SWAP', 'FIL-USDT-SWAP', 'ATOM-USDT-SWAP',
        'FTM-USDT-SWAP', 'INJ-USDT-SWAP', 'SUI-USDT-SWAP', 'RNDR-USDT-SWAP', 'GRT-USDT-SWAP',
        'ICP-USDT-SWAP', 'STX-USDT-SWAP', 'IMX-USDT-SWAP', 'GALA-USDT-SWAP', 'THETA-USDT-SWAP',
        'WIF-USDT-SWAP', 'PEPE-USDT-SWAP', 'BONK-USDT-SWAP', 'FLOKI-USDT-SWAP', 'TIA-USDT-SWAP',
        'SEI-USDT-SWAP', 'ORDI-USDT-SWAP', '1INCH-USDT-SWAP', 'AAVE-USDT-SWAP', 'ALGO-USDT-SWAP',
        'ANKR-USDT-SWAP', 'APE-USDT-SWAP', 'AXS-USDT-SWAP', 'BLUR-USDT-SWAP', 'COMP-USDT-SWAP',
        'CRV-USDT-SWAP', 'ENS-USDT-SWAP', 'EOS-USDT-SWAP', 'FLOW-USDT-SWAP', 'SAND-USDT-SWAP'
    ]
    print(f"📊 Monitoring {len(active_pairs)} pairs")
    
    # Load saved positions
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT symbol, type, sl, tp FROM open_trades")
        rows = cursor.fetchall()
        for row in rows:
            symbol, tipe, sl, tp = row
            pair_states[symbol] = {
                'status': 'IN_LONG' if tipe == 'LONG' else 'IN_SHORT',
                'level': 0.0,
                'sl': float(sl),
                'tp': float(tp)
            }
        conn.close()
        if rows:
            print(f"📦 Restored {len(rows)} positions")
    except Exception as e:
        print(f"❌ Restore error: {e}")
    
    # Start Telegram bot thread
    print("🤖 Starting Telegram bot...")
    tele_thread = threading.Thread(target=run_telegram_bot)
    tele_thread.daemon = True
    tele_thread.start()
    
    # Send startup message
    try:
        bot.send_message(TELEGRAM_CHAT_ID, 
            "🤖 *Bot OKX Engine Pro Aktif!* 🎉\n\n"
            "✅ Mode: Direct API (No CCXT)\n"
            f"📊 Monitoring: {len(active_pairs)} pairs\n"
            "💡 Gunakan menu di bawah untuk kontrol.",
            parse_mode='Markdown',
            reply_markup=main_menu_keyboard()
        )
    except Exception as e:
        print(f"❌ Startup message error: {e}")
    
    print("🔄 Starting scan loop...")
    while True:
        for symbol in active_pairs:
            try:
                scan_breakout_retest(symbol)
                time.sleep(1)
            except Exception as e:
                print(f"Loop error {symbol}: {e}")
        time.sleep(10)

def run_telegram_bot():
    print("✅ Telegram bot running...")
    bot.infinity_polling()

if __name__ == "__main__":
    main()
