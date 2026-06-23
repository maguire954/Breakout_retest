import ccxt

# ==================== CONFIGURATION ====================
SYMBOL = 'BTC-USDT-SWAP'  # Pair yang ingin di-backtest
TIMEFRAME = '15m'         # Timeframe
CANDLE_COUNT = 30         # Parameter S&R kita (30 candle mundur)
LIMIT_DATA = 1000         # Jumlah total candle historis (Maks OKX: 1000)
# =======================================================

exchange = ccxt.okx({'options': {'defaultType': 'swap'}})

def run_backtest_pure_python():
    print(f"Mengambil {LIMIT_DATA} candle historis untuk {SYMBOL} dari OKX...")
    try:
        # Data bawaan CCXT berupa List of Lists: 
        # [[timestamp, open, high, low, close, volume], ...]
        candles = exchange.fetch_ohlcv(SYMBOL, timeframe=TIMEFRAME, limit=LIMIT_DATA)
    except Exception as e:
        print(f"Gagal mengambil data dari OKX: {e}")
        return

    if len(candles) < LIMIT_DATA:
        print("Data candle tidak mencukupi.")
        return

    total_trades = 0
    wins = 0
    losses = 0
    
    state = 'NONE'  # NONE, BREAKOUT_BULLISH, BREAKOUT_BEARISH, IN_LONG, IN_SHORT
    trigger_level = 0.0
    sl_level = 0.0
    tp_level = 0.0

    print("Memulai simulasi backtest murni tanpa Pandas...")
    
    # Loop menggunakan indeks list Python biasa
    for i in range(CANDLE_COUNT + 2, len(candles)):
        current_candle = candles[i]
        current_high = current_candle[2]
        current_low = current_candle[3]
        current_close = current_candle[4]
        
        prev_candle = candles[i-1]
        prev_close = prev_candle[4]
        
        # Ambil potongan list historis ke belakang untuk mencari S&R
        hist_candles = candles[i - CANDLE_COUNT - 2 : i - 1]
        resistance = max([c[2] for c in hist_candles])
        support = min([c[3] for c in hist_candles])

        # --- JIKA SEDANG TIDAK DALAM TRADE ---
        if state == 'NONE':
            if prev_close > resistance:
                state = 'BREAKOUT_BULLISH'
                trigger_level = resistance
            elif prev_close < support:
                state = 'BREAKOUT_BEARISH'
                trigger_level = support

        # --- JIKA MENUNGGU RETEST BULLISH ---
        elif state == 'BREAKOUT_BULLISH':
            if current_low <= trigger_level * 1.001 and current_close > trigger_level:
                state = 'IN_LONG'
                sl_level = support
                risk = current_close - sl_level
                if risk <= 0: risk = current_close * 0.005 
                tp_level = current_close + (risk * 2) # RR 1:2
                total_trades += 1
            elif current_close < trigger_level * 0.995:
                state = 'NONE'

        # --- JIKA MENUNGGU RETEST BEARISH ---
        elif state == 'BREAKOUT_BEARISH':
            if current_high >= trigger_level * 0.999 and current_close < trigger_level:
                state = 'IN_SHORT'
                sl_level = resistance
                risk = sl_level - current_close
                if risk <= 0: risk = current_close * 0.005
                tp_level = current_close - (risk * 2) # RR 1:2
                total_trades += 1
            elif current_close > trigger_level * 1.005:
                state = 'NONE'

        # --- JIKA SEDANG BERJALAN DALAM POSISI LONG ---
        elif state == 'IN_LONG':
            if current_low <= sl_level:
                losses += 1
                state = 'NONE'
            elif current_high >= tp_level:
                wins += 1
                state = 'NONE'

        # --- JIKA SEDANG BERJALAN DALAM POSISI SHORT ---
        elif state == 'IN_SHORT':
            if current_high >= sl_level:
                losses += 1
                state = 'NONE'
            elif current_low <= tp_level:
                wins += 1
                state = 'NONE'

    # --- RINGKASAN HASIL PERHITUNGAN ---
    print("\n" + "="*40)
    print(f"📊 HASIL BACKTEST STRATEGI PADA {SYMBOL}")
    print(f"Timeframe: {TIMEFRAME} | Total Candle: {len(candles)}")
    print("="*40)
    print(f"Total Trades Terjadi : {total_trades}")
    print(f"🟢 Profit (Wins)     : {wins}")
    print(f"🔴 Loss (Losses)     : {losses}")
    
    if total_trades > 0:
        winrate = (wins / total_trades) * 100
        print(f"🎯 WIN RATE          : {winrate:.2f}%")
    else:
        print("🎯 WIN RATE          : 0% (Tidak ada entry valid)")
    print("="*40)

if __name__ == "__main__":
    run_backtest_pure_python()
