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
active_pairs = []

# --- WADAH MONITORING POSISI & HISTORY ---
open_trades = {}    # Menyimpan trade yang sedang berjalan
trade_history = []  # Menyimpan riwayat trade yang sudah closed (TP/SL)

# --- PROSES INISIALISASI OTOMATIS BERDASARKAN VOLUME TERBESAR ---
print("Menghubungi OKX API untuk mengambil 50 koin dengan volume terbesar...")
try:
    exchange.load_markets()
    
    # 1. Saring hanya koin USDT-SWAP (Futures) yang aktif
    futures_markets = [
        market for market in exchange.markets.values() 
        if market['swap'] and market['linear'] and market['settle'] == 'USDT' and market['active']
    ]
    
    # 2. Urutkan koin berdasarkan volume 24 jam (vol24h) dari terbesar ke terkecil
    futures_markets.sort(
        key=lambda x: float(x['info'].get('vol24h', 0)) if 'info' in x else 0, 
        reverse=True
    )
    
    # 3. Ambil 50 koin teratas dari hasil urutan volume terbanyak
    active_pairs = [market['symbol'] for market in futures_markets[:50]]
    
    if active_pairs:
        print(f"🔥 Sukses mengunci {len(active_pairs)} koin dengan VOLUME TERBESAR di OKX!")
    else:
        raise ValueError("Gagal menyaring data koin.")

except Exception as e:
    print(f"Gagal mengambil urutan volume dari OKX: {e}. Menggunakan list fallback 50 koin terpopuler...")
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
        "👋 *Selamat datang di Bot OKX Futures (High Winrate Edition)?*\n\n"
        "Bot ini telah dioptimasi dengan filter ketat.\n\n"
        "*Command Utama:*\n"
        "🔗 /status - Cek kondisi bot\n"
        "📊 /pairs - Daftar 50 koin volume tertinggi\n"
        "🎯 /pola - Lihat koin setup aktif\n"
        "💼 /open - Posisi trading berjalan (floating)\n"
        "📜 /history - Histori koin yang hit TP/SL\n"
        "🧪 `/backtest <KOIN>` - Jalankan uji winrate"
    )
    bot.reply_to(message, welcome_text, parse_mode='Markdown')

@bot.message_handler(commands=['status'])
def send_status(message):
    bot.reply_to(message, "✅ *Bot Status:* Online dengan pemantauan 50 koin volume teraktif.", parse_mode='Markdown')

@bot.message_handler(commands=['pairs'])
def send_pairs(message):
    if not active_pairs:
        bot.reply_to(message, "❌ Daftar pair kosong di server.")
        return
    pairs_list = ", ".join([p.replace('-USDT-SWAP', '') for p in active_pairs])
    bot.reply_to(message, f"🔍 *Pair Terpantau (Volume Teratas) ({len(active_pairs)}):*\n`{pairs_list}`", parse_mode='Markdown')

@bot.message_handler(commands=['pola'])
def send_active_patterns(message):
    waiting_retest = [symbol for symbol, data in pair_states.items() if data['status'] in ['BREAKOUT_BULLISH', 'BREAKOUT_BEARISH']]
    if not waiting_retest:
        bot.reply_to(message, "⏳ Bersih. Belum ada koin baru yang menunggu retest.", parse_mode='Markdown')
        return
    text = "🎯 *Setup Menunggu Retest:*\n\n"
    for symbol in waiting_retest:
        status = pair_states[symbol]['status']
        level = pair_states[symbol]['level']
        emoji = "🚀 Bullish (LONG)" if "BULLISH" in status else "💥 Bearish (SHORT)"
        text += f"• *{symbol.replace('-USDT-SWAP','')}*: {emoji} | Level Key: `{level}`\n"
    bot.reply_to(message, text, parse_mode='Markdown')

@bot.message_handler(commands=['open'])
def send_open_positions(message):
    if not open_trades:
        bot.reply_to(message, "📭 *Tidak ada posisi trading yang aktif saat ini.*", parse_mode='Markdown')
        return
    text = "📊 *DAFTAR POSISI YANG SEDANG OPEN:*\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n"
    for symbol, data in open_trades.items():
        coin = symbol.replace('-USDT-SWAP', '')
        tipe = "🟢 LONG" if data['type'] == 'LONG' else "🔴 SHORT"
        text += (
            f"• *{coin}* ({tipe})\n"
            f"  📥 Entry: `{data['entry']:.4f}`\n"
            f"  🛑 SL: `{data['sl']:.4f}` | 🎯 TP: `{data['tp']:.4f}`\n"
            f"  ⏰ Jam Entry: _{data['time']}_\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
        )
    bot.reply_to(message, text, parse_mode='Markdown')

@bot.message_handler(commands=['history'])
def send_trade_history(message):
    if not trade_history:
        bot.reply_to(message, "📜 *Belum ada histori transaksi yang terselesaikan.*", parse_mode='Markdown')
        return
    recent_history = trade_history[-10:]
    text = f"📜 *RIWAYAT TRANSAKSI TERAKHIR ({len(recent_history)}):*\n"
    text += "━━━━━━━━━━━━━━━━━━━━━\n"
    for data in reversed(recent_history):
        coin = data['symbol'].replace('-USDT-SWAP', '')
        hasil = "✅ TAKE PROFIT" if data['result'] == 'TP' else "❌ STOP LOSS"
        text += (
            f"• *{coin}* | {hasil}\n"
            f"  ↕️ Tipe: `{data['type']}`\n"
            f"  📥 Entry: `{data['entry']:.4f}` | 🚪 Exit: `{data['exit']:.4f}`\n"
            f"  ⏱️ Waktu Selesai: _{data['closed_at']}_\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
        )
    bot.reply_to(message, text, parse_mode='Markdown')

@bot.message_handler(commands=['backtest'])
def handle_backtest_command(message):
    args = message.text.split()
    if len(args) < 2:
        bot.reply_to(message, "⚠️ Format: `/backtest <KOIN>` (Contoh: `/backtest BTC`)", parse_mode='Markdown')
        return

    coin_name = args[1].upper().strip()
    symbol = f"{coin_name}-USDT-SWAP"
    loading_msg = bot.reply_to(message, f"⏳ _Menghitung Winrate premium untuk {symbol}..._", parse_mode='Markdown')

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
            f"🔹 Total Sinyal Valid: *{total_trades}*\n"
            f"🟢 Profit (Wins): *{wins}* | 🔴 Loss: *{losses}*\n\n"
            f"🎯 *OPTIMIZED WIN RATE: {winrate:.2f}%* 🔥"
        )
        bot.delete_message(message.chat.id, loading_msg.message_id)
        bot.reply_to(message, report_text, parse_mode='Markdown')
    except Exception as e:
        bot.delete_message(message.chat.id, loading_msg.message_id)
        bot.reply_to(message, f"❌ Error Backtest: `{str(e)}`", parse_mode='Markdown')

# --- SECTION 4: LIVE MARKET SCANNER ---

def scan_breakout_retest(symbol):
    global open_trades, trade_history, pair_states
    try:
        candles = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME_LIVE, limit=CANDLE_COUNT + 5)
        if len(candles) < CANDLE_COUNT: return

        current_candle, prev_candle = candles[-1], candles[-2]
        current_close = current_candle[4]
        current_low = current_candle[3]
        current_high = current_candle[2]
        prev_close = prev_candle[4]

        if symbol not in pair_states:
            pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}

        waktu_sekarang = time.strftime("%H:%M:%S")

        # ==================== VERIFIKASI SEWAKTU IN-TRADE LONG ====================
        if pair_states[symbol]['status'] == 'IN_LONG':
            sl_level = pair_states[symbol]['sl']
            tp_level = pair_states[symbol]['tp']
            
            if current_low <= sl_level:
                bot.send_message(TELEGRAM_CHAT_ID, f"🔴 *TRADE CLOSED (STOP LOSS)*\n\nPair: `{symbol}`\nHarga menyentuh SL di level: `{sl_level}`.", parse_mode='Markdown')
                if symbol in open_trades:
                    open_trades[symbol]['result'] = 'SL'
                    open_trades[symbol]['exit'] = sl_level
                    open_trades[symbol]['closed_at'] = waktu_sekarang
                    trade_history.append(open_trades[symbol])
                    del open_trades[symbol]
                pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}
                return
            elif current_high >= tp_level:
                bot.send_message(TELEGRAM_CHAT_ID, f"🟢 *TRADE CLOSED (TAKE PROFIT) 🔥*\n\nPair: `{symbol}`\nHarga menyentuh TP di level: `{tp_level}`!", parse_mode='Markdown')
                if symbol in open_trades:
                    open_trades[symbol]['result'] = 'TP'
                    open_trades[symbol]['exit'] = tp_level
                    open_trades[symbol]['closed_at'] = waktu_sekarang
                    trade_history.append(open_trades[symbol])
                    del open_trades[symbol]
                pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}
                return
            else:
                return 

        # ==================== VERIFIKASI SEWAKTU IN-TRADE SHORT ====================
        if pair_states[symbol]['status'] == 'IN_SHORT':
            sl_level = pair_states[symbol]['sl']
            tp_level = pair_states[symbol]['tp']
            
            if current_high >= sl_level:
                bot.send_message(TELEGRAM_CHAT_ID, f"🔴 *TRADE CLOSED (STOP LOSS)*\n\nPair: `{symbol}`\nHarga menyentuh SL di level: `{sl_level}`.", parse_mode='Markdown')
                if symbol in open_trades:
                    open_trades[symbol]['result'] = 'SL'
                    open_trades[symbol]['exit'] = sl_level
                    open_trades[symbol]['closed_at'] = waktu_sekarang
                    trade_history.append(open_trades[symbol])
                    del open_trades[symbol]
                pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}
                return
            elif current_low <= tp_level:
                bot.send_message(TELEGRAM_CHAT_ID, f"🟢 *TRADE CLOSED (TAKE PROFIT) 🔥*\n\nPair: `{symbol}`\nHarga menyentuh TP di level: `{tp_level}`!", parse_mode='Markdown')
                if symbol in open_trades:
                    open_trades[symbol]['result'] = 'TP'
                    open_trades[symbol]['exit'] = tp_level
                    open_trades[symbol]['closed_at'] = waktu_sekarang
                    trade_history.append(open_trades[symbol])
                    del open_trades[symbol]
                pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}
                return
            else:
                return 

        # ==================== LOGIKA PEMBACAAN DAN PANDUAN BREAKOUT ====================
        macro_candles = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME_MACRO, limit=205)
        macro_closes = [c[4] for c in macro_candles]
        ema200_macro = calculate_ema(macro_closes, period=200)

        historical_candles = candles[-52:-2]
        resistance = max([c[2] for c in historical_candles])
        support = min([c[3] for c in historical_candles])

        avg_volume = sum([c[5] for c in historical_candles]) / len(historical_candles)
        breakout_volume = prev_candle[5]
        volume_valid = breakout_volume > (avg_volume * VOLUME_MULTIPLIER)

        live_closes = [c[4] for c in candles]
        current_rsi = calculate_rsi(live_closes, period=14)

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
                open_trades[symbol] = {
                    'symbol': symbol, 'type': 'LONG', 'entry': current_close, 
                    'sl': stop_loss, 'tp': take_profit, 'time': waktu_sekarang
                }
                bot.send_message(TELEGRAM_CHAT_ID, f"🎯 *RETEST SUCCESS (ENTRY LONG)*\n\nPair: `{symbol}`\n📥 Entry: `{current_close:.4f}`\n🛑 SL: `{stop_loss:.4f}` | 🎯 TP: `{take_profit:.4f}`", parse_mode='Markdown')

        elif prev_close < support and volume_valid and current_close < ema200_macro and current_rsi > 30:
            if pair_states[symbol]['status'] != 'BREAKOUT_BEARISH':
                pair_states[symbol] = {'status': 'BREAKOUT_BEARISH', 'level': support, 'sl': resistance, 'tp': 0.0}
                bot.send_message(TELEGRAM_CHAT_ID, f"💥 *VALID BEARISH BREAKDOWN*\n\nPair: `{symbol}`\nLevel: {support}\n_Menunggu pantulan Retest..._", parse_mode='Markdown')

        elif pair_states[symbol]['status'] == 'BREAKOUT_BEARISH':
            target_sup = pair_states[symbol]['level']
            if current_high >= target_sup * 0.999 and current_close < target_sup:
                stop_loss = pair_states[symbol]['sl']
                risk = stop_loss - current_close
                if risk <= 0: risk = current_close * 0.005
                take_profit = current_close - (risk * 2)
                
                pair_states[symbol] = {'status': 'IN_SHORT', 'level': target_sup, 'sl': stop_loss, 'tp': take_profit}
                open_trades[symbol] = {
                    'symbol': symbol, 'type': 'SHORT', 'entry': current_close, 
                    'sl': stop_loss, 'tp': take_profit, 'time': waktu_sekarang
                }
                bot.send_message(TELEGRAM_CHAT_ID, f"🎯 *RETEST SUCCESS (ENTRY SHORT)*\n\nPair: `{symbol}`\n📥 Entry: `{current_close:.4f}`\n🛑 SL: `{stop_loss:.4f}` | 🎯 TP: `{take_profit:.4f}`", parse_mode='Markdown')

        # Batalkan breakout jika harga terlalu jauh menembus ke dalam kembali (invalid)
        if pair_states[symbol]['status'] == 'BREAKOUT_BULLISH' and current_close < target_res * 0.995:
            pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}
        elif pair_states[symbol]['status'] == 'BREAKOUT_BEARISH' and current_close > target_sup * 1.005:
            pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}

    except Exception as e:
        print(f"Error scan {symbol}: {e}")

# --- SECTION 5: MAIN EXECUTION ---

def main():
    print("Memulai aplikasi...")
    
    tele_thread = threading.Thread(target=run_telegram_bot)
    tele_thread.daemon = True
    tele_thread.start()

    bot.send_message(TELEGRAM_CHAT_ID, f"🤖 *Bot OKX Engine Aktif!* 🎉\n\nMemantau {len(active_pairs)} koin volume terbesar teratas secara real-time.", parse_mode='Markdown')

    while True:
        for symbol in active_pairs:
            scan_breakout_retest(symbol)
            time.sleep(2)  # Jeda aman API Rate limit OKX
        time.sleep(10)

if __name__ == "__main__":
    main()
