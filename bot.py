import os
import time
import requests
import ccxt

# ==================== CONFIGURATION ====================
# Variabel rahasia diambil dari Environment Variables Railway
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

TIMEFRAME = '15m'      # Rentang waktu candle (bisa diganti '1h', '4h')
CANDLE_COUNT = 30     # Jumlah candle terdahulu untuk mencari Support/Resistance
# =======================================================

# Inisialisasi OKX Futures (Swap)
exchange = ccxt.okx({
    'options': {'defaultType': 'swap'},
    'enableRateLimit': True
})

# Dictionary untuk menyimpan status pair agar tidak mengirim spam notifikasi yang sama
# Struktur: { 'BTC-USDT-SWAP': {'status': 'NONE', 'level': 0.0} }
pair_states = {}

def send_telegram(message):
    """Fungsi untuk mengirim pesan ke Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload)
        return response.json()
    except Exception as e:
        print(f"Error kirim Telegram: {e}")
        return None

def get_active_pairs():
    """Mengambil maksimal 20 pair USDT terpopuler di OKX Futures untuk menghemat limit API"""
    try:
        exchange.load_markets()
        # Filter hanya pair yang berpasangan dengan USDT Swap
        all_futures = [symbol for symbol in exchange.symbols if '-USDT-SWAP' in symbol]
        # Kita batasi 15 pair teratas terlebih dahulu untuk uji coba awal agar tidak kena rate limit
        return all_futures[:15]
    except Exception as e:
        print(f"Gagal mengambil daftar pair dari OKX: {e}")
        return []

def scan_breakout_retest(symbol):
    """Logika mendeteksi Breakout & Retest"""
    try:
        # Ambil data candle (OHLCV)
        # c[0]=timestamp, c[1]=open, c[2]=high, c[3]=low, c[4]=close
        candles = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=CANDLE_COUNT + 2)
        if len(candles) < CANDLE_COUNT:
            return

        # Candle terakhir yang sedang berjalan (indeks -1)
        current_candle = candles[-1]
        current_close = current_candle[4]
        current_low = current_candle[3]
        current_high = current_candle[2]

        # Candle yang baru saja ditutup (indeks -2)
        prev_candle = candles[-2]
        prev_close = prev_candle[4]

        # Ambil data historis ke belakang sebelum candle -1 dan -2 untuk menentukan S&R
        historical_candles = candles[:-2]
        
        # Cari titik Resistance (High Tertinggi) dan Support (Low Terendah) historis
        resistance = max([c[2] for c in historical_candles])
        support = min([c[3] for c in historical_candles])

        # Inisialisasi status jika pair baru discan
        if symbol not in pair_states:
            pair_states[symbol] = {'status': 'NONE', 'level': 0.0}

        # --- 1. LOGIKA BULLISH (BREAKOUT & RETEST ATAS) ---
        # Deteksi Breakout: Candle sebelumnya ditutup DI ATAS resistance historis
        if prev_close > resistance and pair_states[symbol]['status'] != 'BREAKOUT_BULLISH':
            pair_states[symbol] = {'status': 'BREAKOUT_BULLISH', 'level': resistance}
            msg = f"🚀 *BREAKOUT BULLISH DETECTED*\n\nPair: `{symbol}`\nTF: {TIMEFRAME}\nBreakout di atas: {resistance}\nHarga Sekarang: {current_close}\n\n_Menunggu konfirmasi Retest..._"
            send_telegram(msg)
            print(f"[{symbol}] Breakout Bullish")

        # Deteksi Retest: Jika status sebelumnya sudah breakout, dan candle sekarang kembali menyentuh level resistance kuno
        elif pair_states[symbol]['status'] == 'BREAKOUT_BULLISH':
            target_res = pair_states[symbol]['level']
            # Harga low saat ini menyentuh atau sedikit menembus area resistance kuno (toleransi 0.1%)
            if current_low <= target_res * 1.001 and current_close > target_res:
                msg = f"🎯 *RETEST CONFIRMED (BUY/LONG)*\n\nPair: `{symbol}`\nTF: {TIMEFRAME}\nHarga retest ke level: {target_res}\nHarga Sekarang: {current_close}\n\n*Rekomendasi:* Cari konfirmasi candle rejection untuk entri LONG."
                send_telegram(msg)
                pair_states[symbol] = {'status': 'NONE', 'level': 0.0} # Reset status setelah retest terjadi
                print(f"[{symbol}] Retest Bullish Sukses")

        # --- 2. LOGIKA BEARISH (BREAKOUT & RETEST BAWAH) ---
        # Deteksi Breakdown: Candle sebelumnya ditutup DI BAWAH support historis
        elif prev_close < support and pair_states[symbol]['status'] != 'BREAKOUT_BEARISH':
            pair_states[symbol] = {'status': 'BREAKOUT_BEARISH', 'level': support}
            msg = f"💥 *BREAKDOWN BEARISH DETECTED*\n\nPair: `{symbol}`\nTF: {TIMEFRAME}\nBreakdown di bawah: {support}\nHarga Sekarang: {current_close}\n\n_Menunggu konfirmasi Retest..._"
            send_telegram(msg)
            print(f"[{symbol}] Breakdown Bearish")

        # Deteksi Retest Bearish: Jika harga naik kembali menyentuh support kuno lalu memantul turun
        elif pair_states[symbol]['status'] == 'BREAKOUT_BEARISH':
            target_sup = pair_states[symbol]['level']
            if current_high >= target_sup * 0.999 and current_close < target_sup:
                msg = f"🎯 *RETEST CONFIRMED (SELL/SHORT)*\n\nPair: `{symbol}`\nTF: {TIMEFRAME}\nHarga retest ke level: {target_sup}\nHarga Sekarang: {current_close}\n\n*Rekomendasi:* Cari konfirmasi candle rejection untuk entri SHORT."
                send_telegram(msg)
                pair_states[symbol] = {'status': 'NONE', 'level': 0.0} # Reset
                print(f"[{symbol}] Retest Bearish Sukses")

        # Reset status jika harga ternyata berbalik arah terlalu jauh menggagalkan setup (Fakeout)
        if pair_states[symbol]['status'] == 'BREAKOUT_BULLISH' and current_close < target_res * 0.995:
            pair_states[symbol] = {'status': 'NONE', 'level': 0.0}
        elif pair_states[symbol]['status'] == 'BREAKOUT_BEARISH' and current_close > target_sup * 1.005:
            pair_states[symbol] = {'status': 'NONE', 'level': 0.0}

    except Exception as e:
        print(f"Error scanning {symbol}: {e}")

def main():
    print("Bot Scanner OKX Futures dimulai...")
    send_telegram("🤖 *Bot Scanner OKX Breakout & Retest Aktif 24/7!*")
    
    # Ambil list pair di awal
    pairs = get_active_pairs()
    print(f"Memantau {len(pairs)} pair: {pairs}")

    while True:
        for symbol in pairs:
            scan_breakout_retest(symbol)
            time.sleep(2) # Jeda 2 detik per pair agar tidak terkena ban/IP block dari OKX
        
        print("Siklus scanning selesai. Menunggu 1 menit sebelum siklus berikutnya...")
        time.sleep(60) # Beri jeda 1 menit sebelum mengulang scan seluruh market dari awal

if __name__ == "__main__":
    main()
