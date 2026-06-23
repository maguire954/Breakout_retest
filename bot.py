import os
import time
import threading
import ccxt
import telebot

# ==================== CONFIGURATION ====================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

TIMEFRAME = '15m'
CANDLE_COUNT = 30
# =======================================================

# Inisialisasi Bot Telegram & OKX
bot = telebot.TeleBot(TELEGRAM_TOKEN)
exchange = ccxt.okx({'options': {'defaultType': 'swap'}, 'enableRateLimit': True})

# State global untuk memantau status koin
pair_states = {}
active_pairs = []

# --- SECTION 1: TELEGRAM COMMANDS (Interactive) ---

@bot.message_handler(commands=['start'])
def send_welcome(message):
    welcome_text = (
        "👋 *Halo! Selamat datang di Bot Scanner OKX Futures.*\n\n"
        "Bot ini memantau strategi *Breakout & Retest* secara otomatis 24/7.\n\n"
        "*Command yang tersedia:*\n"
        "🔗 /status - Cek kondisi kesehatan bot\n"
        "📊 /pairs - Lihat daftar koin yang sedang di-scan\n"
        "🎯 /pola - Lihat koin yang sedang menunggu Retest"
    )
    bot.reply_to(message, welcome_text, parse_mode='Markdown')

@bot.message_handler(commands=['status'])
def send_status(message):
    bot.reply_to(message, "✅ *Bot Status:* Online dan sedang memantau OKX Futures.", parse_mode='Markdown')

@bot.message_handler(commands=['pairs'])
def send_pairs(message):
    if not active_pairs:
        bot.reply_to(message, "❌ Daftar pair belum dimuat dari OKX.")
        return
    pairs_list = ", ".join([p.replace('-USDT-SWAP', '') for p in active_pairs])
    bot.reply_to(message, f"🔍 *Pair yang di-scan ({len(active_pairs)}):*\n`{pairs_list}`", parse_mode='Markdown')

@bot.message_handler(commands=['pola'])
def send_active_patterns(message):
    waiting_retest = [symbol for symbol, data in pair_states.items() if data['status'] != 'NONE']
    
    if not waiting_retest:
        bot.reply_to(message, "⏳ Saat ini belum ada koin yang masuk setup (bersih).", parse_mode='Markdown')
        return
        
    text = "🎯 *Koin dalam pantauan Retest:*\n\n"
    for symbol in waiting_retest:
        status = pair_states[symbol]['status']
        level = pair_states[symbol]['level']
        emoji = "🚀 Bullish" if "BULLISH" in status else "💥 Bearish"
        text += f"• *{symbol.replace('-USDT-SWAP','')}*: {emoji} | Menunggu level: `{level}`\n"
        
    bot.reply_to(message, text, parse_mode='Markdown')

def run_telegram_bot():
    """Fungsi untuk menjalankan penerima command Telegram di thread terpisah"""
    print("Telegram Command Listener aktif...")
    bot.infinity_polling()

# --- SECTION 2: MARKET SCANNER LOGIC (OKX) ---

def get_active_pairs():
    global active_pairs
    try:
        exchange.load_markets()
        all_futures = [symbol for symbol in exchange.symbols if '-USDT-SWAP' in symbol]
        active_pairs = all_futures[:15] # Batasi 15 pair terpopuler untuk uji coba
    except Exception as e:
        print(f"Gagal mengambil pair OKX: {e}")

def scan_breakout_retest(symbol):
    try:
        candles = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=CANDLE_COUNT + 2)
        if len(candles) < CANDLE_COUNT: return

        current_candle, prev_candle = candles[-1], candles[-2]
        current_close, current_low, current_high = current_candle[4], current_candle[3], current_candle[2]
        prev_close = prev_candle[4]

        historical_candles = candles[:-2]
        resistance = max([c[2] for c in historical_candles])
        support = min([c[3] for c in historical_candles])

        if symbol not in pair_states:
            pair_states[symbol] = {'status': 'NONE', 'level': 0.0}

        # --- Logika Bullish ---
        if prev_close > resistance and pair_states[symbol]['status'] != 'BREAKOUT_BULLISH':
            pair_states[symbol] = {'status': 'BREAKOUT_BULLISH', 'level': resistance}
            bot.send_message(TELEGRAM_CHAT_ID, f"🚀 *BREAKOUT BULLISH*\nPair: `{symbol}`\nBreakout level: {resistance}\n_Menunggu Retest..._", parse_mode='Markdown')

        elif pair_states[symbol]['status'] == 'BREAKOUT_BULLISH':
            target_res = pair_states[symbol]['level']
            if current_low <= target_res * 1.001 and current_close > target_res:
                bot.send_message(TELEGRAM_CHAT_ID, f"🎯 *RETEST CONFIRMED (LONG)*\nPair: `{symbol}`\nLevel: {target_res}\nHarga Sekarang: {current_close}", parse_mode='Markdown')
                pair_states[symbol] = {'status': 'NONE', 'level': 0.0}

        # --- Logika Bearish ---
        elif prev_close < support and pair_states[symbol]['status'] != 'BREAKOUT_BEARISH':
            pair_states[symbol] = {'status': 'BREAKOUT_BEARISH', 'level': support}
            bot.send_message(TELEGRAM_CHAT_ID, f"💥 *BREAKDOWN BEARISH*\nPair: `{symbol}`\nBreakdown level: {support}\n_Menunggu Retest..._", parse_mode='Markdown')

        elif pair_states[symbol]['status'] == 'BREAKOUT_BEARISH':
            target_sup = pair_states[symbol]['level']
            if current_high >= target_sup * 0.999 and current_close < target_sup:
                bot.send_message(TELEGRAM_CHAT_ID, f"🎯 *RETEST CONFIRMED (SHORT)*\nPair: `{symbol}`\nLevel: {target_sup}\nHarga Sekarang: {current_close}", parse_mode='Markdown')
                pair_states[symbol] = {'status': 'NONE', 'level': 0.0}

        # Reset jika fakeout semenjana
        if pair_states[symbol]['status'] == 'BREAKOUT_BULLISH' and current_close < target_res * 0.995:
            pair_states[symbol] = {'status': 'NONE', 'level': 0.0}
        elif pair_states[symbol]['status'] == 'BREAKOUT_BEARISH' and current_close > target_sup * 1.005:
            pair_states[symbol] = {'status': 'NONE', 'level': 0.0}

    except Exception as e:
        print(f"Error scan {symbol}: {e}")

def main():
    print("Memulai aplikasi...")
    get_active_pairs()
    
    # Jalankan Telegram bot receiver di thread berbeda agar tidak menghentikan loop OKX
    tele_thread = threading.Thread(target=run_telegram_bot)
    tele_thread.daemon = True
    tele_thread.start()

    bot.send_message(TELEGRAM_CHAT_ID, "🤖 *Bot Scanner OKX + Command Interaktif Aktif!* 🎉", parse_mode='Markdown')

    while True:
        for symbol in active_pairs:
            scan_breakout_retest(symbol)
            time.sleep(2)
        time.sleep(60)

if __name__ == "__main__":
    main()
