import os
import time
import threading
import sqlite3
import ccxt
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

# ==================== CONFIGURATION ====================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

TIMEFRAME_LIVE = '15m'      
TIMEFRAME_MACRO = '1h'      
CANDLE_COUNT = 50          
VOLUME_MULTIPLIER = 1.5    
DB_FILE = "trading_bot.db"
# =======================================================

bot = telebot.TeleBot(TELEGRAM_TOKEN)
exchange = ccxt.okx({'options': {'defaultType': 'swap'}, 'enableRateLimit': True})

pair_states = {}
active_pairs = []

# =======================================================
# 🗄️ DATABASE SYSTEM DATABASE MANAGERS
# =======================================================

def init_db():
    """Membuat file database dan tabel yang dibutuhkan secara otomatis jika belum ada."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # Tabel untuk mengawal trade yang sedang berjalan (Open)
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
    # Tabel untuk merekam riwayat trade yang sudah selesai
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

def load_saved_positions():
    """Memulihkan kondisi monitoring pair_states dari database saat bot dinyalakan ulang."""
    global pair_states
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT symbol, type, sl, tp FROM open_trades")
    rows = cursor.fetchall()
    for row in rows:
        symbol, tipe, sl, tp = row
        pair_states[symbol] = {
            'status': 'IN_LONG' if tipe == 'LONG' else 'IN_SHORT',
            'level': 0.0, 
            'sl': sl,
            'tp': tp
        }
    conn.close()
    if rows:
        print(f"📦 Berhasil memulihkan {len(rows)} posisi aktif dari Database SQLite!")

def save_open_trade(symbol, tipe, entry, sl, tp, waktu):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO open_trades (symbol, type, entry, sl, tp, time) VALUES (?, ?, ?, ?, ?, ?)",
        (symbol, tipe, entry, sl, tp, waktu)
    )
    conn.commit()
    conn.close()

def delete_open_trade(symbol):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM open_trades WHERE symbol = ?", (symbol,))
    conn.commit()
    conn.close()

def insert_trade_history(symbol, tipe, entry, exit_price, result, closed_at):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO trade_history (symbol, type, entry, exit, result, closed_at) VALUES (?, ?, ?, ?, ?, ?)",
        (symbol, tipe, entry, exit_price, result, closed_at)
    )
    conn.commit()
    conn.close()

def get_open_trades_dict():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT symbol, type, entry, sl, tp, time FROM open_trades")
    rows = cursor.fetchall()
    conn.close()
    
    trades = {}
    for row in rows:
        symbol, tipe, entry, sl, tp, waktu = row
        trades[symbol] = {'type': tipe, 'entry': entry, 'sl': sl, 'tp': tp, 'time': waktu}
    return trades

def get_recent_history(limit=10):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT symbol, type, entry, exit, result, closed_at FROM trade_history ORDER BY id DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    
    history_list = []
    for row in rows:
        symbol, tipe, entry, exit_price, result, closed_at = row
        history_list.append({
            'symbol': symbol, 'type': tipe, 'entry': entry, 'exit': exit_price, 'result': result, 'closed_at': closed_at
        })
    return history_list

# =======================================================
# 🌐 ENGINE PRO INITIALIZATION
# =======================================================

print("Menghubungi OKX API...")
try:
    exchange.load_markets()
    futures_markets = [
        market for market in exchange.markets.values() 
        if market['swap'] and market['linear'] and market['settle'] == 'USDT' and market['active']
    ]
    futures_markets.sort(
        key=lambda x: float(x['info'].get('vol24h', 0)) if 'info' in x else 0, 
        reverse=True
    )
    active_pairs = [market['symbol'] for market in futures_markets[:50]]
    if active_pairs:
        print(f"🔥 Sukses mengunci {len(active_pairs)} koin dengan VOLUME TERBESAR di OKX!")
except Exception as e:
    print(f"Gagal memuat pasar OKX: {e}. Menggunakan list fallback...")
    active_pairs = [
        'BTC-USDT-SWAP', 'ETH-USDT-SWAP', 'SOL-USDT-SWAP', 'XRP-USDT-SWAP', 'ADA-USDT-SWAP',
        'AVAX-USDT-SWAP', 'DOT-USDT-SWAP', 'DOGE-USDT-SWAP', 'SHIB-USDT-SWAP', 'LINK-USDT-SWAP',
        'NEAR-USDT-SWAP', 'MATIC-USDT-SWAP', 'LTC-USDT-SWAP', 'TRX-USDT-SWAP', 'UNI-USDT-SWAP'
    ]

# --- MATH METRICS CALCULATORS ---
def calculate_ema(prices, period=200):
    if len(prices) < period: return 0.0
    k = 2 / (period + 1)
    ema = prices[0]
    for price in prices[1:]: ema = (price * k) + (ema * (1 - k))
    return ema

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1: return 50.0
    gains, losses = [], []
    for i in range(1, len(prices)):
        change = prices[i] - prices[i-1]
        gains.append(change if change > 0 else 0.0)
        losses.append(abs(change) if change < 0 else 0.0)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    if avg_loss == 0: return 100.0
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0: return 100.0
    return 100.0 - (100.0 / (1.0 + (avg_gain / avg_loss)))

def run_telegram_bot():
    print("Telegram Command Listener aktif...")
    bot.infinity_polling()

# --- KEYBOARDS ---
def main_menu_keyboard():
    markup = ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    markup.add(
        KeyboardButton("🔍 Pantauan Koin"), KeyboardButton("🎯 Setup Aktif (/pola)"),
        KeyboardButton("📊 Posisi Open"), KeyboardButton("📜 Histori Trade"),
        KeyboardButton("🤖 Status Sistem")
    )
    return markup

def pairs_category_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🔥 Top 50 Vol Teraktif", callback_data="cat_top50"),
        InlineKeyboardButton("💎 Koin Majors (Bluechip)", callback_data="cat_majors"),
        InlineKeyboardButton("🌐 Layer 1 Ecosystem", callback_data="cat_l1"),
        InlineKeyboardButton("🚀 Meme & Alts Populer", callback_data="cat_memes")
    )
    return markup

# --- TELEGRAM TEXT HANDLERS ---
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    welcome_text = (
        "👋 *Selamat datang di Dashboard OKX Futures Pro Engine (Database Ver.)!*\n\n"
        "Gunakan tombol *Reply Keyboard* di bagian bawah layar Anda untuk bernavigasi."
    )
    bot.send_message(message.chat.id, welcome_text, parse_mode='Markdown', reply_markup=main_menu_keyboard())

@bot.message_handler(func=lambda msg: True)
def handle_reply_keyboard(message):
    text = message.text
    if text == "🔍 Pantauan Koin":
        bot.send_message(message.chat.id, "📂 *Silakan pilih kategori koin:*", parse_mode='Markdown', reply_markup=pairs_category_keyboard())
    elif text == "🎯 Setup Aktif (/pola)":
        send_active_patterns(message)
    elif text == "📊 Posisi Open":
        send_open_positions(message)
    elif text == "📜 Histori Trade":
        send_trade_history(message)
    elif text == "🤖 Status Sistem":
        bot.reply_to(message, f"✅ *Bot Status:* Online.\n🎯 *Engine:* Memantau {len(active_pairs)} koin.\n🗄️ *Database:* SQLite Terkoneksi & Aman.", parse_mode='Markdown')

def send_active_patterns(message):
    waiting_retest = [symbol for symbol, data in pair_states.items() if data['status'] in ['BREAKOUT_BULLISH', 'BREAKOUT_BEARISH']]
    if not waiting_retest:
        bot.send_message(message.chat.id, "⏳ *Bersih.* Belum ada koin baru yang masuk radar breakout.", parse_mode='Markdown')
        return
    text = "🎯 *Setup Menunggu Retest:*\n\n"
    for symbol in waiting_retest:
        status = pair_states[symbol]['status']
        level = pair_states[symbol]['level']
        emoji = "🚀 Bullish (LONG)" if "BULLISH" in status else "💥 Bearish (SHORT)"
        text += f"• *{symbol.replace('-USDT-SWAP','')}*: {emoji} | Level Key: `{level}`\n"
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

def send_open_positions(message):
    open_trades = get_open_trades_dict()
    if not open_trades:
        bot.send_message(message.chat.id, "📭 *Tidak ada posisi trading yang aktif saat ini.*", parse_mode='Markdown')
        return
        
    loading_msg = bot.send_message(message.chat.id, "⏳ _Mengambil harga pasar terkini..._", parse_mode='Markdown')
    text = "📊 *DAFTAR POSISI YANG SEDANG OPEN:*\n━━━━━━━━━━━━━━━━━━━━━\n"
    
    for symbol, data in open_trades.items():
        coin = symbol.replace('-USDT-SWAP', '')
        tipe = data['type']
        entry_price = data['entry']
        
        current_price = entry_price
        try:
            ticker = exchange.fetch_ticker(symbol)
            if ticker and 'last' in ticker: current_price = float(ticker['last'])
        except: pass

        if tipe == 'LONG':
            pnl_nominal = current_price - entry_price
            pnl_percent = (pnl_nominal / entry_price) * 100
            tipe_emoji = "🟢 LONG"
        else:
            pnl_nominal = entry_price - current_price
            pnl_percent = (pnl_nominal / entry_price) * 100
            tipe_emoji = "🔴 SHORT"

        pnl_status = "```diff\n"
        if pnl_nominal >= 0:
            pnl_status += "+ Floating Profit: +" + "{:.2f}".format(pnl_percent) + "%\n"
        else:
            pnl_status += "- Floating Loss: " + "{:.2f}".format(pnl_percent) + "%\n"
        pnl_status += "```"

        text += (
            f"• *{coin}* ({tipe_emoji})\n"
            f"  📥 Entry: `{entry_price:.4f}`\n"
            f"  ⚡ Current: `{current_price:.4f}`\n"
            f"  🛑 SL: `{data['sl']:.4f}` | 🎯 TP: `{data['tp']:.4f}`\n"
            f"{pnl_status}\n━━━━━━━━━━━━━━━━━━━━━\n"
        )
        
    try: bot.delete_message(message.chat.id, loading_msg.message_id)
    except: pass
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

def send_trade_history(message):
    history = get_recent_history(10)
    if not history:
        bot.send_message(message.chat.id, "📜 *Belum ada histori transaksi di database.*", parse_mode='Markdown')
        return
    text = f"📜 *RIWAYAT TRANSAKSI TERAKHIR ({len(history)}):*\n━━━━━━━━━━━━━━━━━━━━━\n"
    for data in history:
        coin = data['symbol'].replace('-USDT-SWAP', '')
        hasil = "✅ TAKE PROFIT" if data['result'] == 'TP' else "❌ STOP LOSS"
        text += f"• *{coin}* | {hasil}\n  ↕️ Tipe: `{data['type']}`\n  📥 Entry: `{data['entry']:.4f}` | 🚪 Exit: `{data['exit']:.4f}`\n━━━━━━━━━━━━━━━━━━━━━\n"
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data.startswith('cat_'))
def handle_category_selection(call):
    category = call.data
    if category == "cat_top50":
        pairs_list = ", ".join([p.replace('-USDT-SWAP', '') for p in active_pairs])
        text = f"🔥 *Top 50 Koin Volume Teraktif OKX:*\n\n`{pairs_list}`"
    elif category == "cat_majors":
        text = "💎 *Koin Kategori Majors:*\n\n`BTC, ETH, SOL, XRP, ADA, LTC, LINK, DOT`"
    elif category == "cat_l1":
        text = "🌐 *Koin Kategori Layer 1 Ecosystem:*\n\n`AVAX, ATOM, NEAR, FTM, SUI, APT, INJ, SEI`"
    elif category == "cat_memes":
        text = "🚀 *Koin Kategori Meme & Alts:*\n\n`DOGE, SHIB, PEPE, WIF, BONK, FLOKI, GALA, OP, ARB`"
        
    bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text=text, parse_mode='Markdown', reply_markup=pairs_category_keyboard())
    bot.answer_callback_query(call.id)

# --- CORE MARKETS SCANNER ---
def scan_breakout_retest(symbol):
    global pair_states
    try:
        open_trades = get_open_trades_dict()
        candles = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME_LIVE, limit=100)
        if len(candles) < CANDLE_COUNT: return

        current_candle, prev_candle = candles[-1], candles[-2]
        current_close, current_low, current_high, prev_close = current_candle[4], current_candle[3], current_candle[2], prev_candle[4]
        current_open = current_candle[1]

        if symbol not in pair_states:
            pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}

        waktu_sekarang = time.strftime("%H:%M:%S")

        # Monitoring Jika Berstatus LONG
        if pair_states[symbol]['status'] == 'IN_LONG':
            sl_level, tp_level = pair_states[symbol]['sl'], pair_states[symbol]['tp']
            if current_low <= sl_level:
                bot.send_message(TELEGRAM_CHAT_ID, f"🔴 *TRADE CLOSED (STOP LOSS)*\n\nPair: `{symbol.replace('-USDT-SWAP', '')}`\nSL: `{sl_level}`.", parse_mode='Markdown')
                if symbol in open_trades:
                    insert_trade_history(symbol, 'LONG', open_trades[symbol]['entry'], sl_level, 'SL', waktu_sekarang)
                    delete_open_trade(symbol)
                pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}
                return
            elif current_high >= tp_level:
                bot.send_message(TELEGRAM_CHAT_ID, f"🟢 *TRADE CLOSED (TAKE PROFIT) 🔥*\n\nPair: `{symbol.replace('-USDT-SWAP', '')}`\nTP: `{tp_level}`!", parse_mode='Markdown')
                if symbol in open_trades:
                    insert_trade_history(symbol, 'LONG', open_trades[symbol]['entry'], tp_level, 'TP', waktu_sekarang)
                    delete_open_trade(symbol)
                pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}
                return
            else: return

        # Monitoring Jika Berstatus SHORT
        if pair_states[symbol]['status'] == 'IN_SHORT':
            sl_level, tp_level = pair_states[symbol]['sl'], pair_states[symbol]['tp']
            if current_high >= sl_level:
                bot.send_message(TELEGRAM_CHAT_ID, f"🔴 *TRADE CLOSED (STOP LOSS)*\n\nPair: `{symbol.replace('-USDT-SWAP', '')}`\nSL: `{sl_level}`.", parse_mode='Markdown')
                if symbol in open_trades:
                    insert_trade_history(symbol, 'SHORT', open_trades[symbol]['entry'], sl_level, 'SL', waktu_sekarang)
                    delete_open_trade(symbol)
                pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}
                return
            elif current_low <= tp_level:
                bot.send_message(TELEGRAM_CHAT_ID, f"🟢 *TRADE CLOSED (TAKE PROFIT) 🔥*\n\nPair: `{symbol.replace('-USDT-SWAP', '')}`\nTP: `{tp_level}`!", parse_mode='Markdown')
                if symbol in open_trades:
                    insert_trade_history(symbol, 'SHORT', open_trades[symbol]['entry'], tp_level, 'TP', waktu_sekarang)
                    delete_open_trade(symbol)
                pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}
                return
            else: return

        # Analisis Sinyal Teknis
        macro_candles = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME_MACRO, limit=205)
        macro_closes = [c[4] for c in macro_candles]
        ema200_macro = calculate_ema(macro_closes, period=200)

        historical_candles = candles[-52:-2]
        resistance, support = max([c[2] for c in historical_candles]), min([c[3] for c in historical_candles])
        avg_volume = sum([c[5] for c in historical_candles]) / len(historical_candles)
        volume_valid = prev_candle[5] > (avg_volume * VOLUME_MULTIPLIER)
        current_rsi = calculate_rsi([c[4] for c in candles], period=14)

        if prev_close > resistance and volume_valid and current_close > ema200_macro and current_rsi < 70:
            if pair_states[symbol]['status'] != 'BREAKOUT_BULLISH':
                pair_states[symbol] = {'status': 'BREAKOUT_BULLISH', 'level': resistance, 'sl': support, 'tp': 0.0}
                bot.send_message(TELEGRAM_CHAT_ID, f"🚀 *VALID BULLISH BREAKOUT*\n\nPair: `{symbol.replace('-USDT-SWAP', '')}`\nLevel: {resistance}\n_Menunggu konfirmasi pola pantulan Retest..._", parse_mode='Markdown')

        elif pair_states[symbol]['status'] == 'BREAKOUT_BULLISH':
            target_res = pair_states[symbol]['level']
            body_size = abs(current_close - current_open)
            lower_wick = min(current_open, current_close) - current_low
            
            retest_touched = current_low <= target_res * 1.002
            retest_held = current_close > target_res * 0.998
            rejection_confirmed = current_close > current_open and lower_wick > (body_size * 1.2)
            
            if retest_touched and retest_held and rejection_confirmed:
                stop_loss = pair_states[symbol]['sl']
                risk = current_close - stop_loss
                if risk <= 0: risk = current_close * 0.005
                take_profit = current_close + (risk * 2)
                
                pair_states[symbol] = {'status': 'IN_LONG', 'level': target_res, 'sl': stop_loss, 'tp': take_profit}
                save_open_trade(symbol, 'LONG', current_close, stop_loss, take_profit, waktu_sekarang)
                
                bot.send_message(TELEGRAM_CHAT_ID, f"🎯 *RETEST SUCCESS (ENTRY LONG)*\n\nPair: `{symbol.replace('-USDT-SWAP', '')}`\n📥 Entry: `{current_close:.4f}`\n🛑 SL: `{stop_loss:.4f}` | 🎯 TP: `{take_profit:.4f}`", parse_mode='Markdown')

        elif prev_close < support and volume_valid and current_close < ema200_macro and current_rsi > 30:
            if pair_states[symbol]['status'] != 'BREAKOUT_BEARISH':
                pair_states[symbol] = {'status': 'BREAKOUT_BEARISH', 'level': support, 'sl': resistance, 'tp': 0.0}
                bot.send_message(TELEGRAM_CHAT_ID, f"💥 *VALID BEARISH BREAKDOWN*\n\nPair: `{symbol.replace('-USDT-SWAP', '')}`\nLevel Broken: `{support}`\n_Menunggu konfirmasi pola pantulan Retest..._", parse_mode='Markdown')

        elif pair_states[symbol]['status'] == 'BREAKOUT_BEARISH':
            target_sup = pair_states[symbol]['level']
            body_size = abs(current_close - current_open)
            upper_wick = current_high - max(current_open, current_close)
            
            retest_touched = current_high >= target_sup * 0.998
            retest_held = current_close < target_sup * 1.002
            rejection_confirmed = current_close < current_open and upper_wick > (body_size * 1.2)
            
            if retest_touched and retest_held and rejection_confirmed:
                stop_loss = pair_states[symbol]['sl']
                risk = stop_loss - current_close
                if risk <= 0: risk = current_close * 0.005
                take_profit = current_close - (risk * 2)
                
                pair_states[symbol] = {'status': 'IN_SHORT', 'level': target_sup, 'sl': stop_loss, 'tp': take_profit}
                save_open_trade(symbol, 'SHORT', current_close, stop_loss, take_profit, waktu_sekarang)
                
                bot.send_message(TELEGRAM_CHAT_ID, f"🎯 *RETEST SUCCESS (ENTRY SHORT)*\n\nPair: `{symbol.replace('-USDT-SWAP', '')}`\n📥 Entry: `{current_close:.4f}`\n🛑 SL: `{stop_loss:.4f}` | 🎯 TP: `{take_profit:.4f}`", parse_mode='Markdown')

        if pair_states[symbol]['status'] == 'BREAKOUT_BULLISH' and current_close < target_res * 0.995:
            pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}
        elif pair_states[symbol]['status'] == 'BREAKOUT_BEARISH' and current_close > target_sup * 1.005:
            pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}

    except Exception as e:
        print(f"Error scan {symbol}: {e}")

def main():
    print("Memulai aplikasi...")
    init_db()               # Step 1: Jalankan Database SQLite
    load_saved_positions()  # Step 2: Pulihkan sisa trade lama jika bot sempat mati
    
    tele_thread = threading.Thread(target=run_telegram_bot)
    tele_thread.daemon = True
    tele_thread.start()

    bot.send_message(TELEGRAM_CHAT_ID, f"🤖 *Bot OKX Engine Pro v2.0 Aktif!* 🎉\n\nSistem database SQLite telah terhubung dengan sukses. Portofolio Anda kini aman dari ancaman restart server.", parse_mode='Markdown', reply_markup=main_menu_keyboard())

    while True:
        for symbol in active_pairs:
            scan_breakout_retest(symbol)
            time.sleep(2)
        time.sleep(10)

if __name__ == "__main__":
    main()
