import os
import time
import threading
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
# =======================================================

bot = telebot.TeleBot(TELEGRAM_TOKEN)
exchange = ccxt.okx({'options': {'defaultType': 'swap'}, 'enableRateLimit': True})

pair_states = {}
active_pairs = []

# --- WADAH MONITORING POSISI & HISTORY ---
open_trades = {}    
trade_history = []  

# --- PROSES INISIALISASI OTOMATIS BERDASARKAN VOLUME TERBESAR ---
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
    print(f"Gagal memuat pasar OKX: {e}. Menggunakan list fallback 50 koin...")
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

# --- UTILITIES ---
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

# =======================================================
# 🌐 KEYBOARD GENERATORS (REPLY & INLINE)
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
        InlineKeyboardButton("💎 Koin Majors (Bluechip)", callback_data="cat_majors"),
        InlineKeyboardButton("🌐 Layer 1 Ecosystem", callback_data="cat_l1"),
        InlineKeyboardButton("🚀 Meme & Alts Populer", callback_data="cat_memes")
    )
    return markup

# =======================================================
# 📑 TELEGRAM TEXT COMMANDS DEFINITIONS
# =======================================================

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    welcome_text = (
        "👋 *Selamat datang di Dashboard OKX Futures Pro Engine!*\n\n"
        "Gunakan tombol *Reply Keyboard* di bagian bawah layar Anda untuk bernavigasi dengan cepat dan praktis."
    )
    bot.send_message(message.chat.id, welcome_text, parse_mode='Markdown', reply_markup=main_menu_keyboard())

@bot.message_handler(commands=['backtest'])
def handle_backtest_command(message):
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "⚠️ Format: `/backtest <KOIN>` (Contoh: `/backtest BTC`)", parse_mode='Markdown')
        return

    coin_name = args[1].upper().strip()
    symbol = f"{coin_name}-USDT-SWAP"
    
    # Kirim pesan loading awal
    loading_msg = bot.reply_to(message, f"⏳ _Menghitung Winrate premium untuk {symbol}..._", parse_mode='Markdown')

    try:
        # KITA UBAH LIMIT MENJADI 300 AGAR API OKX TIDAK TIMEOUT / REJECT
        candles = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME_LIVE, limit=300)
        
        if not candles or len(candles) < CANDLE_COUNT:
            bot.reply_to(message, f"❌ Data transaksi historis untuk `{symbol}` tidak mencukupi di OKX.", parse_mode='Markdown')
            return

        total_trades, wins, losses = 0, 0, 0
        state = 'NONE'
        trigger_level, sl_level, tp_level = 0.0, 0.0, 0.0

        for i in range(CANDLE_COUNT + 2, len(candles)):
            current_high, current_low, current_close = candles[i][2], candles[i][3], candles[i][4]
            prev_close = candles[i-1][4]
            
            hist_candles = candles[i - CANDLE_COUNT - 2 : i - 1]
            resistance = max([c[2] for c in hist_candles])
            support = min([c[3] for c in hist_candles])
            
            avg_volume = sum([c[5] for c in hist_candles]) / len(hist_candles)
            breakout_volume = candles[i-1][5]
            volume_valid = breakout_volume > (avg_volume * VOLUME_MULTIPLIER)
            
            local_closes = [c[4] for c in candles[:i]]
            current_ema200_macro = calculate_ema(local_closes, period=200) 
            current_rsi = calculate_rsi(local_closes, period=14)

            if state == 'NONE':
                if prev_close > resistance and volume_valid and current_close > current_ema200_macro and current_rsi < 70:
                    state = 'BREAKOUT_BULLISH'
                    trigger_level = resistance
                elif prev_close < support and volume_valid and current_close < current_ema200_macro and current_rsi > 30:
                    state = 'BREAKOUT_BEARISH'
                    trigger_level = support

            elif state == 'BREAKOUT_BULLISH':
                if current_low <= trigger_level * 1.001 and current_close > trigger_level:
                    state = 'IN_LONG'
                    sl_level = support
                    risk = current_close - sl_level
                    if risk <= 0: risk = current_close * 0.005
                    tp_level = current_close + (risk * 2)
                    total_trades += 1
                elif current_close < trigger_level * 0.995:
                    state = 'NONE'

            elif state == 'BREAKOUT_BEARISH':
                if current_high >= trigger_level * 0.999 and current_close < trigger_level:
                    state = 'IN_SHORT'
                    sl_level = resistance
                    risk = sl_level - current_close
                    if risk <= 0: risk = current_close * 0.005
                    tp_level = current_close - (risk * 2)
                    total_trades += 1
                elif current_close > trigger_level * 1.005:
                    state = 'NONE'

            elif state == 'IN_LONG':
                if current_low <= sl_level: losses += 1; state = 'NONE'
                elif current_high >= tp_level: wins += 1; state = 'NONE'

            elif state == 'IN_SHORT':
                if current_high >= sl_level: losses += 1; state = 'NONE'
                elif current_low <= tp_level: wins += 1; state = 'NONE'

        winrate = (wins / total_trades * 100) if total_trades > 0 else 0.0
        
        report_text = (
            f"📊 *LAPORAN WINRATE (PREMIUM FILTER)*\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"Aset: `{symbol}` | TF: `{TIMEFRAME_LIVE}`\n"
            f"🔹 Total Sinyal Valid: *{total_trades}*\n"
            f"🟢 Profit (Wins): *{wins}* | 🔴 Loss: *{losses}*\n\n"
            f"🎯 *OPTIMIZED WIN RATE: {winrate:.2f}%* 🔥"
        )
        
        # Hapus pesan loading dengan aman
        try:
            bot.delete_message(message.chat.id, loading_msg.message_id)
        except:
            pass
            
        bot.reply_to(message, report_text, parse_mode='Markdown')

    except Exception as e:
        # Jika terjadi error API, kirimkan detail error asli tanpa crash teks
        error_string = str(e)
        bot.reply_to(message, f"❌ *Gagal memproses backtest.*\nDetail Kendala: `{error_string}`", parse_mode='Markdown')
    
@bot.message_handler(func=lambda msg: True)
def handle_reply_keyboard(message):
    text = message.text
    
    if text == "🔍 Pantauan Koin":
        bot.send_message(
            message.chat.id, 
            "📂 *Silakan pilih kategori koin yang ingin Anda pantau:*", 
            parse_mode='Markdown', 
            reply_markup=pairs_category_keyboard()
        )
    elif text == "🎯 Setup Aktif (/pola)":
        send_active_patterns(message)
    elif text == "📊 Posisi Open":
        send_open_positions(message)
    elif text == "📜 Histori Trade":
        send_trade_history(message)
    elif text == "🤖 Status Sistem":
        bot.reply_to(message, f"✅ *Bot Status:* Online.\n🎯 *Engine:* Memantau {len(active_pairs)} koin dengan volume tertinggi secara live.", parse_mode='Markdown')

# --- REFACTORING UTILITY FUNCTIONS FOR COMMANDS ---

def send_active_patterns(message):
    waiting_retest = [symbol for symbol, data in pair_states.items() if data['status'] in ['BREAKOUT_BULLISH', 'BREAKOUT_BEARISH']]
    if not waiting_retest:
        bot.send_message(message.chat.id, "⏳ *Bersih.* Belum ada koin baru yang masuk radar breakout/menunggu retest.", parse_mode='Markdown')
        return
    text = "🎯 *Setup Menunggu Retest:*\n\n"
    for symbol in waiting_retest:
        status = pair_states[symbol]['status']
        level = pair_states[symbol]['level']
        emoji = "🚀 Bullish (LONG)" if "BULLISH" in status else "💥 Bearish (SHORT)"
        text += f"• *{symbol.replace('-USDT-SWAP','')}*: {emoji} | Level Key: `{level}`\n"
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

def send_open_positions(message):
    if not open_trades:
        bot.send_message(message.chat.id, "📭 *Tidak ada posisi trading yang aktif saat ini.*", parse_mode='Markdown')
        return
    text = "📊 *DAFTAR POSISI YANG SEDANG OPEN:*\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n"
    for symbol, data in open_trades.items():
        coin = symbol.replace('-USDT-SWAP', '')
        tipe = "🟢 LONG" if data['type'] == 'LONG' else "🔴 SHORT"
        text += f"• *{coin}* ({tipe})\n  📥 Entry: `{data['entry']:.4f}`\n  🛑 SL: `{data['sl']:.4f}` | 🎯 TP: `{data['tp']:.4f}`\n━━━━━━━━━━━━━━━━━━━━━\n"
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

def send_trade_history(message):
    if not trade_history:
        bot.send_message(message.chat.id, "📜 *Belum ada histori transaksi yang terselesaikan.*", parse_mode='Markdown')
        return
    recent_history = trade_history[-10:]
    text = f"📜 *RIWAYAT TRANSAKSI TERAKHIR ({len(recent_history)}):*\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n"
    for data in reversed(recent_history):
        coin = data['symbol'].replace('-USDT-SWAP', '')
        hasil = "✅ TAKE PROFIT" if data['result'] == 'TP' else "❌ STOP LOSS"
        text += f"• *{coin}* | {hasil}\n  ↕️ Tipe: `{data['type']}`\n  📥 Entry: `{data['entry']:.4f}` | 🚪 Exit: `{data['exit']:.4f}`\n━━━━━━━━━━━━━━━━━━━━━\n"
    bot.send_message(message.chat.id, text, parse_mode='Markdown')

# =======================================================
# 🔄 CALLBACK QUERY HANDLER FOR INLINE KEYBOARD SUB-MENU
# =======================================================

@bot.callback_query_handler(func=lambda call: call.data.startswith('cat_'))
def handle_category_selection(call):
    category = call.data
    
    if category == "cat_top50":
        pairs_list = ", ".join([p.replace('-USDT-SWAP', '') for p in active_pairs])
        text = f"🔥 *Top 50 Koin Volume Tertinggi OKX (Scanner Live):*\n\n`{pairs_list}`"
    elif category == "cat_majors":
        text = "💎 *Koin Kategori Majors (Market Cap Besar):*\n\n`BTC, ETH, SOL, XRP, ADA, LTC, LINK, DOT`"
    elif category == "cat_l1":
        text = "🌐 *Koin Kategori Layer 1 Ecosystem:*\n\n`AVAX, ATOM, NEAR, FTM, SUI, APT, INJ, SEI`"
    elif category == "cat_memes":
        text = "🚀 *Koin Kategori Meme & Alts Populer:*\n\n`DOGE, SHIB, PEPE, WIF, BONK, FLOKI, GALA, OP, ARB`"
        
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=text,
        parse_mode='Markdown',
        reply_markup=pairs_category_keyboard()
    )
    bot.answer_callback_query(call.id)

# --- LIVE MARKET SCANNER & MAIN ---

def scan_breakout_retest(symbol):
    global open_trades, trade_history, pair_states
    try:
        candles = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME_LIVE, limit=CANDLE_COUNT + 5)
        if len(candles) < CANDLE_COUNT: return

        current_candle, prev_candle = candles[-1], candles[-2]
        current_close, current_low, current_high, prev_close = current_candle[4], current_candle[3], current_candle[2], prev_candle[4]

        if symbol not in pair_states:
            pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}

        waktu_sekarang = time.strftime("%H:%M:%S")

        if pair_states[symbol]['status'] == 'IN_LONG':
            sl_level, tp_level = pair_states[symbol]['sl'], pair_states[symbol]['tp']
            if current_low <= sl_level:
                bot.send_message(TELEGRAM_CHAT_ID, f"🔴 *TRADE CLOSED (STOP LOSS)*\n\nPair: `{symbol}`\nHarga menyentuh SL di level: `{sl_level}`.", parse_mode='Markdown')
                if symbol in open_trades:
                    open_trades[symbol].update({'result': 'SL', 'exit': sl_level, 'closed_at': waktu_sekarang})
                    trade_history.append(open_trades[symbol])
                    del open_trades[symbol]
                pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}
                return
            elif current_high >= tp_level:
                bot.send_message(TELEGRAM_CHAT_ID, f"🟢 *TRADE CLOSED (TAKE PROFIT) 🔥*\n\nPair: `{symbol}`\nHarga menyentuh TP di level: `{tp_level}`!", parse_mode='Markdown')
                if symbol in open_trades:
                    open_trades[symbol].update({'result': 'TP', 'exit': tp_level, 'closed_at': waktu_sekarang})
                    trade_history.append(open_trades[symbol])
                    del open_trades[symbol]
                pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}
                return
            else: return

        if pair_states[symbol]['status'] == 'IN_SHORT':
            sl_level, tp_level = pair_states[symbol]['sl'], pair_states[symbol]['tp']
            if current_high >= sl_level:
                bot.send_message(TELEGRAM_CHAT_ID, f"🔴 *TRADE CLOSED (STOP LOSS)*\n\nPair: `{symbol}`\nHarga menyentuh SL di level: `{sl_level}`.", parse_mode='Markdown')
                if symbol in open_trades:
                    open_trades[symbol].update({'result': 'SL', 'exit': sl_level, 'closed_at': waktu_sekarang})
                    trade_history.append(open_trades[symbol])
                    del open_trades[symbol]
                pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}
                return
            elif current_low <= tp_level:
                bot.send_message(TELEGRAM_CHAT_ID, f"🟢 *TRADE CLOSED (TAKE PROFIT) 🔥*\n\nPair: `{symbol}`\nHarga menyentuh TP di level: `{tp_level}`!", parse_mode='Markdown')
                if symbol in open_trades:
                    open_trades[symbol].update({'result': 'TP', 'exit': tp_level, 'closed_at': waktu_sekarang})
                    trade_history.append(open_trades[symbol])
                    del open_trades[symbol]
                pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}
                return
            else: return

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
                bot.send_message(TELEGRAM_CHAT_ID, f"🚀 *VALID BULLISH BREAKOUT*\n\nPair: `{symbol}`\nLevel: {resistance}\n_Menunggu pantulan Retest..._", parse_mode='Markdown')

        elif pair_states[symbol]['status'] == 'BREAKOUT_BULLISH':
            target_res = pair_states[symbol]['level']
            if current_low <= target_res * 1.001 and current_close > target_res:
                stop_loss = pair_states[symbol]['sl']
                risk = current_close - stop_loss
                if risk <= 0: risk = current_close * 0.005
                take_profit = current_close + (risk * 2)
                
                pair_states[symbol] = {'status': 'IN_LONG', 'level': target_res, 'sl': stop_loss, 'tp': take_profit}
                open_trades[symbol] = {'symbol': symbol, 'type': 'LONG', 'entry': current_close, 'sl': stop_loss, 'tp': take_profit, 'time': waktu_sekarang}
                bot.send_message(TELEGRAM_CHAT_ID, f"🎯 *RETEST SUCCESS (ENTRY LONG)*\n\nPair: `{symbol}`\n📥 Entry: `{current_close:.4f}`\n🛑 SL: `{stop_loss:.4f}` | 🎯 TP: `{take_profit:.4f}`", parse_mode='Markdown')

        elif prev_close < support and volume_valid and current_close < ema200_macro and current_rsi > 30:
            if pair_states[symbol]['status'] != 'BREAKOUT_BEARISH':
                pair_states[symbol] = {'status': 'BREAKOUT_BEARISH', 'level': support, 'sl': resistance, 'tp': 0.0}
                bot.send_message(TELEGRAM_CHAT_ID, f"💥 *VALID BEARISH BREAKDOWN*\n\nPair: `{support}`\nLevel: {support}\n_Menunggu pantulan Retest..._", parse_mode='Markdown')

        elif pair_states[symbol]['status'] == 'BREAKOUT_BEARISH':
            target_sup = pair_states[symbol]['level']
            if current_high >= target_sup * 0.999 and current_close < target_sup:
                stop_loss = pair_states[symbol]['sl']
                risk = stop_loss - current_close
                if risk <= 0: risk = current_close * 0.005
                take_profit = current_close - (risk * 2)
                
                pair_states[symbol] = {'status': 'IN_SHORT', 'level': target_sup, 'sl': stop_loss, 'tp': take_profit}
                open_trades[symbol] = {'symbol': symbol, 'type': 'SHORT', 'entry': current_close, 'sl': stop_loss, 'tp': take_profit, 'time': waktu_sekarang}
                bot.send_message(TELEGRAM_CHAT_ID, f"🎯 *RETEST SUCCESS (ENTRY SHORT)*\n\nPair: `{symbol}`\n📥 Entry: `{current_close:.4f}`\n🛑 SL: `{stop_loss:.4f}` | 🎯 TP: `{take_profit:.4f}`", parse_mode='Markdown')

        if pair_states[symbol]['status'] == 'BREAKOUT_BULLISH' and current_close < target_res * 0.995:
            pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}
        elif pair_states[symbol]['status'] == 'BREAKOUT_BEARISH' and current_close > target_sup * 1.005:
            pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}

    except Exception as e:
        print(f"Error scan {symbol}: {e}")

def main():
    print("Memulai aplikasi...")
    tele_thread = threading.Thread(target=run_telegram_bot)
    tele_thread.daemon = True
    tele_thread.start()

    bot.send_message(TELEGRAM_CHAT_ID, f"🤖 *Bot OKX Engine Pro Aktif!* 🎉\n\nSemua modul backtest dan navigasi diperbarui.", parse_mode='Markdown', reply_markup=main_menu_keyboard())

    while True:
        for symbol in active_pairs:
            scan_breakout_retest(symbol)
            time.sleep(2)
        time.sleep(10)

if __name__ == "__main__":
    main()
