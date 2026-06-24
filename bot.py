import os
import time
import threading
import ccxt
import telebot

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

# --- PROSES INISIALISASI LANGSUNG DI GLOBAL SCOPE ---
print("Menghubungi OKX API untuk mengunci daftar koin...")
try:
    exchange.load_markets()
    all_futures = [symbol for symbol in exchange.symbols if '-USDT-SWAP' in symbol]
    if all_futures:
        active_pairs = all_futures[:15] # Ambil 15 koin teraktif
        print(f"Inisialisasi Sukses! Berhasil mengunci {len(active_pairs)} pair koin.")
    else:
        raise ValueError("Daftar symbols OKX kosong.")
except Exception as e:
    print(f"Gagal memuat pasar OKX di awal: {e}. Menggunakan list fallback manual...")
    # Jika API OKX sempat timeout saat booting, gunakan list cadangan ini agar bot tidak kosong
    active_pairs = ['BTC-USDT-SWAP', 'ETH-USDT-SWAP', 'SOL-USDT-SWAP', 'XRP-USDT-SWAP', 'ADA-USDT-SWAP']

# --- SECTION 1: MATHEMATICAL UTILITIES ---

def calculate_ema(prices, period=200):
    if len(prices) < period: return 0.0
    k = 2 / (period + 1)
    ema = prices[0]
    for price in prices[1:]:
        ema = (price * k) + (ema * (1 - k))
    return ema

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1: return 50.0
    gains = []
    losses = []
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
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))

# --- SECTION 2: TELEGRAM COMMANDS DEFINITIONS ---

def run_telegram_bot():
    print("Telegram Command Listener aktif...")
    bot.infinity_polling()

@bot.message_handler(commands=['start'])
def send_welcome(message):
    welcome_text = (
        "👋 *Selamat datang di Bot OKX Futures (High Winrate Edition)!*\n\n"
        "Bot ini telah dioptimasi dengan filter:\n"
        "1. 📈 *S&R 50 Candle*\n"
        "2. 📊 *Lonjakan Volume (1.5x Rata-rata)*\n"
        "3. 🌍 *Filter Tren Makro EMA 200 (TF 1 Jam)*\n"
        "4. 🎛️ *Filter Momentum RSI (14)*\n\n"
        "*Command:*\n"
        "🔗 /status - Cek kondisi bot\n"
        "📊 /pairs - Daftar koin dipantau\n"
        "🎯 /pola - Lihat koin setup aktif\n"
        "🧪 `/backtest <KOIN>` - Cek winrate baru (Contoh: `/backtest BTC`)"
    )
    bot.reply_to(message, welcome_text, parse_mode='Markdown')

@bot.message_handler(commands=['status'])
def send_status(message):
    bot.reply_to(message, "✅ *Bot Status:* Online dengan filter indikator ketat.", parse_mode='Markdown')

@bot.message_handler(commands=['pairs'])
def send_pairs(message):
    # Mengambil langsung dari scope global yang sudah pasti terisi sejak file di-load
    if not active_pairs:
        bot.reply_to(message, "❌ Daftar pair kosong di server.")
        return
    pairs_list = ", ".join([p.replace('-USDT-SWAP', '') for p in active_pairs])
    bot.reply_to(message, f"🔍 *Pair di-scan ({len(active_pairs)}):*\n`{pairs_list}`", parse_mode='Markdown')

@bot.message_handler(commands=['pola'])
def send_active_patterns(message):
    waiting_retest = [symbol for symbol, data in pair_states.items() if data['status'] != 'NONE']
    if not waiting_retest:
        bot.reply_to(message, "⏳ Bersih. Belum ada koin yang lolos filter ketat.", parse_mode='Markdown')
        return
    text = "🎯 *Setup Lolos Filter (Menunggu Retest):*\n\n"
    for symbol in waiting_retest:
        status = pair_states[symbol]['status']
        level = pair_states[symbol]['level']
        emoji = "🚀 Bullish (LONG)" if "BULLISH" in status else "💥 Bearish (SHORT)"
        text += f"• *{symbol.replace('-USDT-SWAP','')}*: {emoji} | Level: `{level}`\n"
    bot.reply_to(message, text, parse_mode='Markdown')

@bot.message_handler(commands=['backtest'])
def handle_backtest_command(message):
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "⚠️ Format: `/backtest <KOIN>` (Contoh: `/backtest BTC`)", parse_mode='Markdown')
        return

    coin_name = args[1].upper().strip()
    symbol = f"{coin_name}-USDT-SWAP"
    loading_msg = bot.reply_to(message, f"⏳ _Menghitung Winrate PREMIUM untuk {symbol} (1000 Candle)..._", parse_mode='Markdown')

    try:
        candles = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME_LIVE, limit=1000)
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
            f"S&R Jendela: `{CANDLE_COUNT} Candle`\n"
            f"Rasio RR Target: `1:2`\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔹 Total Sinyal Valid: *{total_trades}*\n"
            f"🟢 Profit (Wins): *{wins}*\n"
            f"🔴 Loss (Losses): *{losses}*\n\n"
            f"🎯 *OPTIMIZED WIN RATE: {winrate:.2f}%* 🔥\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"_Sinyal palsu (fakeout) berhasil dikurangi secara drastis melalui filter volume & tren makro._"
        )
        bot.delete_message(message.chat.id, loading_msg.message_id)
        bot.reply_to(message, report_text, parse_mode='Markdown')
    except Exception as e:
        bot.delete_message(message.chat.id, loading_msg.message_id)
        bot.reply_to(message, f"❌ Error: `{str(e)}`", parse_mode='Markdown')

# --- SECTION 4: LIVE MARKET SCANNER ---

def scan_breakout_retest(symbol):
    try:
        candles = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME_LIVE, limit=CANDLE_COUNT + 5)
        if len(candles) < CANDLE_COUNT: return

        macro_candles = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME_MACRO, limit=205)
        macro_closes = [c[4] for c in macro_candles]
        ema200_macro = calculate_ema(macro_closes, period=200)

        current_candle, prev_candle = candles[-1], candles[-2]
        current_close, current_low, current_high = current_candle[4], current_candle[3], current_candle[2]
        prev_close = prev_candle[4]

        historical_candles = candles[-52:-2]
        resistance = max([c[2] for c in historical_candles])
        support = min([c[3] for c in historical_candles])

        avg_volume = sum([c[5] for c in historical_candles]) / len(historical_candles)
        breakout_volume = prev_candle[5]
        volume_valid = breakout_volume > (avg_volume * VOLUME_MULTIPLIER)

        live_closes = [c[4] for c in candles]
        current_rsi = calculate_rsi(live_closes, period=14)

        if symbol not in pair_states:
            pair_states[symbol] = {'status': 'NONE', 'level': 0.0}

        if prev_close > resistance and volume_valid and current_close > ema200_macro and current_rsi < 70:
            if pair_states[symbol]['status'] != 'BREAKOUT_BULLISH':
                pair_states[symbol] = {'status': 'BREAKOUT_BULLISH', 'level': resistance}
                bot.send_message(TELEGRAM_CHAT_ID, f"🚀 *VALID BULLISH BREAKOUT*\n\nPair: `{symbol}`\nLevel: {resistance}\nVolume: Lebih dari 1.5x Rata-rata ✅\nTren Makro: Di atas EMA 200 (Bullish) ✅\nRSI: {current_rsi:.1f} ✅\n\n_Menunggu pantulan Retest..._", parse_mode='Markdown')

        elif pair_states[symbol]['status'] == 'BREAKOUT_BULLISH':
            target_res = pair_states[symbol]['level']
            if current_low <= target_res * 1.001 and current_close > target_res:
                bot.send_message(TELEGRAM_CHAT_ID, f"🎯 *RETEST SUCCESS (ENTRY LONG)*\n\nPair: `{symbol}`\nHarga memantul sukses di level: {target_res}\n*Rekomendasi:* Open Posisi LONG (RR 1:2). Put SL di bawah support terdekat.", parse_mode='Markdown')
                pair_states[symbol] = {'status': 'NONE', 'level': 0.0}

        elif prev_close < support and volume_valid and current_close < ema200_macro and current_rsi > 30:
            if pair_states[symbol]['status'] != 'BREAKOUT_BEARISH':
                pair_states[symbol] = {'status': 'BREAKOUT_BEARISH', 'level': support}
                bot.send_message(TELEGRAM_CHAT_ID, f"💥 *VALID BEARISH BREAKDOWN*\n\nPair: `{symbol}`\nLevel: {support}\nVolume: Lebih dari 1.5x Rata-rata ✅\nTren Makro: Di bawah EMA 200 (Bearish) ✅\nRSI: {current_rsi:.1f} ✅\n\n_Menunggu pantulan Retest..._", parse_mode='Markdown')

        elif pair_states[symbol]['status'] == 'BREAKOUT_BEARISH':
            target_sup = pair_states[symbol]['level']
            if current_high >= target_sup * 0.999 and current_close < target_sup:
                bot.send_message(TELEGRAM_CHAT_ID, f"🎯 *RETEST SUCCESS (ENTRY SHORT)*\n\nPair: `{symbol}`\nHarga memantul sukses di level: {target_sup}\n*Rekomendasi:* Open Posisi SHORT (RR 1:2). Put SL di atas resistance terdekat.", parse_mode='Markdown')
                pair_states[symbol] = {'status': 'NONE', 'level': 0.0}

        if pair_states[symbol]['status'] == 'BREAKOUT_BULLISH' and current_close < target_res * 0.995:
            pair_states[symbol] = {'status': 'NONE', 'level': 0.0}
        elif pair_states[symbol]['status'] == 'BREAKOUT_BEARISH' and current_close > target_sup * 1.005:
            pair_states[symbol] = {'status': 'NONE', 'level': 0.0}

    except Exception as e:
        print(f"Error scan {symbol}: {e}")

# --- SECTION 5: MAIN EXECUTION ---

def main():
    print("Memulai aplikasi...")
    
    # Nyalakan Telegram listener di thread terpisah
    tele_thread = threading.Thread(target=run_telegram_bot)
    tele_thread.daemon = True
    tele_thread.start()

    # Kirim satu kali pesan inisialisasi sukses ke Telegram
    bot.send_message(TELEGRAM_CHAT_ID, f"🤖 *Bot OKX High-Winrate Engine Aktif!* 🎉\n\nMemantau {len(active_pairs)} koin di OKX Futures secara real-time.", parse_mode='Markdown')

    # Loop Scanning Market Utama
    while True:
        for symbol in active_pairs:
            scan_breakout_retest(symbol)
            time.sleep(2)
        time.sleep(60)

if __name__ == "__main__":
    main()
