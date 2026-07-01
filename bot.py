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
        
        # Normalize symbol
        api_symbol = symbol
        if api_symbol.endswith('-SWAP'):
            api_symbol = api_symbol.replace('-SWAP', '')
        elif '/' in api_symbol:
            parts = api_symbol.split('/')
            if len(parts) >= 2:
                api_symbol = f"{parts[0].strip()}-{parts[1].strip().replace(':USDT', '')}"
        
        # Map timeframe
        bar_map = {
            '1m': '1m', '3m': '3m', '5m': '5m', '15m': '15m', '30m': '30m',
            '1h': '1H', '2h': '2H', '4h': '4H', '6h': '6H', '12h': '12H',
            '1d': '1D', '1w': '1W', '1M': '1M'
        }
        bar = bar_map.get(timeframe, '15m')
        
        url = f"https://www.okx.com/api/v5/market/candles?instId={api_symbol}&bar={bar}&limit={limit}"
        print(f"DEBUG: Fetching OHLCV: {url}")
        
        response = requests.get(url, timeout=15)
        if response.status_code == 200:
            data = response.json()
            if data.get('code') == '0' and data.get('data'):
                candles = data['data']
                # Convert to ccxt format: [timestamp, open, high, low, close, volume]
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
                # Reverse to get ascending order (oldest first)
                result.reverse()
                print(f"✅ Got {len(result)} candles for {symbol}")
                return result
        else:
            print(f"⚠️ HTTP Error: {response.status_code}")
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
        
        # Try ticker endpoint
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
        
        # Try OHLCV endpoint
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
# 🤖 TELEGRAM BOT
# =======================================================

def main_menu_keyboard():
    markup = ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(
        KeyboardButton("🔍 Pantauan Koin"),
        KeyboardButton("🎯 Setup Aktif (/pola)"),
        KeyboardButton("📊 Posisi Open"),
        KeyboardButton("📜 Histori Trade"),
        KeyboardButton("🤖 Status Sistem")
    )
    return markup

def pairs_category_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🔥 Top 50 Vol Teraktif", callback_data="cat_top50"),
        InlineKeyboardButton("💎 Koin Majors", callback_data="cat_majors"),
        InlineKeyboardButton("🌐 Layer 1", callback_data="cat_l1"),
        InlineKeyboardButton("🚀 Meme & Alts", callback_data="cat_memes")
    )
    return markup

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.send_message(message.chat.id, 
        "👋 *Selamat datang di Dashboard OKX Futures Pro Engine!*\n\n"
        "Gunakan tombol *Reply Keyboard* di bagian bawah layar.",
        parse_mode='Markdown')

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

@bot.message_handler(commands=['test_ohlcv'])
def test_ohlcv_command(message):
    try:
        args = message.text.split()
        if len(args) < 2:
            bot.reply_to(message, "⚠️ Format: `/test_ohlcv <SYMBOL>`\nContoh: `/test_ohlcv BTC`", parse_mode='Markdown')
            return
        coin = args[1].upper().strip()
        symbol = f"{coin}-USDT-SWAP"
        
        bot.reply_to(message, f"⏳ _Mengambil data OHLCV untuk {symbol}..._", parse_mode='Markdown')
        
        candles = fetch_ohlcv_from_okx(symbol, timeframe='15m', limit=10)
        
        if candles and len(candles) > 0:
            text = f"📊 *OHLCV DATA UNTUK {coin}*\n━━━━━━━━━━━━━━━━━━━━━\n"
            for candle in candles[-5:]:  # Show last 5 candles
                ts = time.strftime('%H:%M', time.localtime(candle[0]/1000))
                text += f"`{ts} | O:{candle[1]:.2f} H:{candle[2]:.2f} L:{candle[3]:.2f} C:{candle[4]:.2f}`\n"
            bot.reply_to(message, text, parse_mode='Markdown')
        else:
            bot.reply_to(message, f"❌ *Gagal mengambil data OHLCV untuk {symbol}*", parse_mode='Markdown')
            
    except Exception as e:
        bot.reply_to(message, f"❌ Error: `{str(e)}`", parse_mode='Markdown')

@bot.message_handler(func=lambda msg: True)
def handle_reply_keyboard(message):
    text = message.text
    if text == "🔍 Pantauan Koin":
        bot.send_message(message.chat.id, "📂 *Pilih kategori:*", parse_mode='Markdown', reply_markup=pairs_category_keyboard())
    elif text == "🎯 Setup Aktif (/pola)":
        send_active_patterns(message)
    elif text == "📊 Posisi Open":
        send_open_positions(message)
    elif text == "📜 Histori Trade":
        send_trade_history(message)
    elif text == "🤖 Status Sistem":
        db_type = "PostgreSQL (Railway)" if DATABASE_URL else "SQLite (Fallback)"
        bot.reply_to(message, f"✅ *Bot Status:* Online.\n📊 *Engine:* Memantau {len(active_pairs)} koin.\n🗄️ *Database:* {db_type}", parse_mode='Markdown')

def send_active_patterns(message):
    waiting_retest = [s for s, d in pair_states.items() if d['status'] in ['BREAKOUT_BULLISH', 'BREAKOUT_BEARISH']]
    if not waiting_retest:
        bot.send_message(message.chat.id, "⏳ *Bersih.* Tidak ada setup.", parse_mode='Markdown')
        return
    text = "🎯 *Setup Menunggu Retest:*\n\n"
    for symbol in waiting_retest:
        status = pair_states[symbol]['status']
        level = pair_states[symbol]['level']
        emoji = "🚀 LONG" if "BULLISH" in status else "💥 SHORT"
        text += f"• *{symbol.replace('-USDT-SWAP','')}*: {emoji} | Key: `{level}`\n"
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
                
                # Get current price from OKX API
                current_price = entry
                price = fetch_price_from_okx(symbol)
                if price and price > 0:
                    current_price = price
                
                # Calculate PnL
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
        bot.send_message(message.chat.id, "📜 *Belum ada histori.*", parse_mode='Markdown')
        return
    
    messages = []
    current_msg = f"📜 *HISTORI ({len(history)}):*\n━━━━━━━━━━━━━━━━━━━━━\n"
    
    for data in history:
        try:
            coin = data['symbol'].replace('-USDT-SWAP', '')
            tipe = data['type']
            entry = data['entry']
            exit_price = data['exit']
            
            if tipe == 'LONG':
                pnl_pct = ((exit_price - entry) / entry) * 100
            else:
                pnl_pct = ((entry - exit_price) / entry) * 100
            
            emoji = "🟢" if data['result'] == 'TP' else "🔴"
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
    
    if current_msg and current_msg != f"📜 *HISTORI ({len(history)}):*\n━━━━━━━━━━━━━━━━━━━━━\n":
        messages.append(current_msg)
    
    for msg in messages:
        bot.send_message(message.chat.id, msg, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data.startswith('cat_'))
def handle_category_selection(call):
    if call.data == "cat_top50":
        pairs = ", ".join([p.replace('-USDT-SWAP', '') for p in active_pairs])
        text = f"🔥 *Top 50:*\n\n`{pairs}`"
    elif call.data == "cat_majors":
        text = "💎 *Majors:*\n\n`BTC, ETH, SOL, XRP, ADA, LTC, LINK, DOT`"
    elif call.data == "cat_l1":
        text = "🌐 *Layer 1:*\n\n`AVAX, ATOM, NEAR, FTM, SUI, APT, INJ, SEI`"
    elif call.data == "cat_memes":
        text = "🚀 *Meme & Alts:*\n\n`DOGE, SHIB, PEPE, WIF, BONK, FLOKI, GALA, OP, ARB`"
    bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode='Markdown', reply_markup=pairs_category_keyboard())
    bot.answer_callback_query(call.id)

# =======================================================
# 🔍 SCANNER (MENGGUNAKAN OKX API TANPA CCXT)
# =======================================================

def scan_breakout_retest(symbol):
    global pair_states
    try:
        if not symbol:
            return
        
        # Fetch candles langsung dari OKX API
        candles = fetch_ohlcv_from_okx(symbol, timeframe=TIMEFRAME_LIVE, limit=100)
        if not candles or len(candles) < CANDLE_COUNT:
            return
        
        current_candle = candles[-1]
        prev_candle = candles[-2]
        current_close = current_candle[4]
        current_low = current_candle[3]
        current_high = current_candle[2]
        current_open = current_candle[1]
        prev_close = prev_candle[4]
        
        if symbol not in pair_states:
            pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}
        
        waktu = time.strftime("%H:%M:%S")
        open_trades = get_open_trades_dict()
        
        # Check LONG position
        if pair_states[symbol]['status'] == 'IN_LONG':
            sl = pair_states[symbol]['sl']
            tp = pair_states[symbol]['tp']
            entry = open_trades[symbol]['entry'] if symbol in open_trades else current_close
            
            if current_low <= sl:
                pnl = ((sl - entry) / entry) * 100
                bot.send_message(TELEGRAM_CHAT_ID, f"🔴 *STOP LOSS*\n{symbol.replace('-USDT-SWAP', '')}\nEntry: {entry:.4f}\nExit: {sl:.4f}\nPnL: {pnl:.2f}%", parse_mode='Markdown')
                if symbol in open_trades:
                    insert_trade_history(symbol, 'LONG', entry, sl, 'SL', waktu)
                    delete_open_trade(symbol)
                pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}
                return
            elif current_high >= tp:
                pnl = ((tp - entry) / entry) * 100
                bot.send_message(TELEGRAM_CHAT_ID, f"🟢 *TAKE PROFIT*\n{symbol.replace('-USDT-SWAP', '')}\nEntry: {entry:.4f}\nExit: {tp:.4f}\nPnL: +{pnl:.2f}%", parse_mode='Markdown')
                if symbol in open_trades:
                    insert_trade_history(symbol, 'LONG', entry, tp, 'TP', waktu)
                    delete_open_trade(symbol)
                pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}
                return
            else:
                return
        
        # Check SHORT position
        if pair_states[symbol]['status'] == 'IN_SHORT':
            sl = pair_states[symbol]['sl']
            tp = pair_states[symbol]['tp']
            entry = open_trades[symbol]['entry'] if symbol in open_trades else current_close
            
            if current_high >= sl:
                pnl = ((entry - sl) / entry) * 100
                bot.send_message(TELEGRAM_CHAT_ID, f"🔴 *STOP LOSS*\n{symbol.replace('-USDT-SWAP', '')}\nEntry: {entry:.4f}\nExit: {sl:.4f}\nPnL: {pnl:.2f}%", parse_mode='Markdown')
                if symbol in open_trades:
                    insert_trade_history(symbol, 'SHORT', entry, sl, 'SL', waktu)
                    delete_open_trade(symbol)
                pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}
                return
            elif current_low <= tp:
                pnl = ((entry - tp) / entry) * 100
                bot.send_message(TELEGRAM_CHAT_ID, f"🟢 *TAKE PROFIT*\n{symbol.replace('-USDT-SWAP', '')}\nEntry: {entry:.4f}\nExit: {tp:.4f}\nPnL: +{pnl:.2f}%", parse_mode='Markdown')
                if symbol in open_trades:
                    insert_trade_history(symbol, 'SHORT', entry, tp, 'TP', waktu)
                    delete_open_trade(symbol)
                pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}
                return
            else:
                return
        
        # Analysis - Fetch macro candles
        macro_candles = fetch_ohlcv_from_okx(symbol, timeframe=TIMEFRAME_MACRO, limit=205)
        if not macro_candles:
            return
        
        macro_closes = [c[4] for c in macro_candles]
        ema200 = calculate_ema(macro_closes, period=200)
        
        hist_candles = candles[-52:-2]
        if not hist_candles:
            return
        
        resistance = max([c[2] for c in hist_candles])
        support = min([c[3] for c in hist_candles])
        avg_vol = sum([c[5] for c in hist_candles]) / len(hist_candles)
        vol_valid = prev_candle[5] > (avg_vol * VOLUME_MULTIPLIER)
        rsi = calculate_rsi([c[4] for c in candles], period=14)
        
        # BULLISH
        if prev_close > resistance and vol_valid and current_close > ema200 and rsi < 70:
            if pair_states[symbol]['status'] != 'BREAKOUT_BULLISH':
                pair_states[symbol] = {'status': 'BREAKOUT_BULLISH', 'level': resistance, 'sl': support, 'tp': 0.0}
                bot.send_message(TELEGRAM_CHAT_ID, f"🚀 *BULLISH BREAKOUT*\n{symbol.replace('-USDT-SWAP', '')}\nLevel: {resistance:.4f}\nWaiting retest...", parse_mode='Markdown')
        
        elif pair_states[symbol]['status'] == 'BREAKOUT_BULLISH':
            target = pair_states[symbol]['level']
            body = abs(current_close - current_open)
            lower_wick = min(current_open, current_close) - current_low
            
            if current_low <= target * 1.002 and current_close > target * 0.998 and current_close > current_open and lower_wick > (body * 1.2):
                sl, method = get_atr_sl(candles, pair_states[symbol]['sl'], 'LONG')
                risk = current_close - sl
                if risk <= 0:
                    risk = current_close * 0.005
                tp = current_close + (risk * 2)
                
                pair_states[symbol] = {'status': 'IN_LONG', 'level': target, 'sl': sl, 'tp': tp}
                save_open_trade(symbol, 'LONG', current_close, sl, tp, waktu)
                
                atr = calculate_atr(candles, period=14)
                atr_info = f"ATR14={atr:.4f}" if atr > 0 else "ATR=N/A"
                bot.send_message(TELEGRAM_CHAT_ID, f"🎯 *ENTRY LONG*\n{symbol.replace('-USDT-SWAP', '')}\nEntry: {current_close:.4f}\nSL: {sl:.4f} ({method})\nTP: {tp:.4f}\n{atr_info}", parse_mode='Markdown')
        
        # BEARISH
        elif prev_close < support and vol_valid and current_close < ema200 and rsi > 30:
            if pair_states[symbol]['status'] != 'BREAKOUT_BEARISH':
                pair_states[symbol] = {'status': 'BREAKOUT_BEARISH', 'level': support, 'sl': resistance, 'tp': 0.0}
                bot.send_message(TELEGRAM_CHAT_ID, f"💥 *BEARISH BREAKDOWN*\n{symbol.replace('-USDT-SWAP', '')}\nLevel: {support:.4f}\nWaiting retest...", parse_mode='Markdown')
        
        elif pair_states[symbol]['status'] == 'BREAKOUT_BEARISH':
            target = pair_states[symbol]['level']
            body = abs(current_close - current_open)
            upper_wick = current_high - max(current_open, current_close)
            
            if current_high >= target * 0.998 and current_close < target * 1.002 and current_close < current_open and upper_wick > (body * 1.2):
                sl, method = get_atr_sl(candles, pair_states[symbol]['sl'], 'SHORT')
                risk = sl - current_close
                if risk <= 0:
                    risk = current_close * 0.005
                tp = current_close - (risk * 2)
                
                pair_states[symbol] = {'status': 'IN_SHORT', 'level': target, 'sl': sl, 'tp': tp}
                save_open_trade(symbol, 'SHORT', current_close, sl, tp, waktu)
                
                atr = calculate_atr(candles, period=14)
                atr_info = f"ATR14={atr:.4f}" if atr > 0 else "ATR=N/A"
                bot.send_message(TELEGRAM_CHAT_ID, f"🎯 *ENTRY SHORT*\n{symbol.replace('-USDT-SWAP', '')}\nEntry: {current_close:.4f}\nSL: {sl:.4f} ({method})\nTP: {tp:.4f}\n{atr_info}", parse_mode='Markdown')
        
        # Reset if breakout fails
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
    
    # Set fallback pairs
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
    
    # Main loop
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
