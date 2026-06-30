import os
import time
import threading
import sqlite3
import ccxt
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
import psycopg2
from psycopg2.extras import RealDictCursor

# ==================== CONFIGURATION ====================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

TIMEFRAME_LIVE = '15m'      
TIMEFRAME_MACRO = '1h'      
CANDLE_COUNT = 50          
VOLUME_MULTIPLIER = 1.5    
DB_FILE = "trading_bot.db"

# Otomatis deteksi koneksi PostgreSQL dari Railway
DATABASE_URL = os.getenv("DATABASE_URL")
# =======================================================

bot = telebot.TeleBot(TELEGRAM_TOKEN)
exchange = ccxt.okx({'options': {'defaultType': 'swap'}, 'enableRateLimit': True})

pair_states = {}
active_pairs = []

# =======================================================
# 🗄️ DATABASE ENGINE ADAPTIF (POSTGRESQL & SQLITE)
# =======================================================

def get_db_connection():
    """Mengembalikan koneksi database yang sesuai (PostgreSQL untuk Railway, SQLite untuk lokal)."""
    if DATABASE_URL:
        import psycopg2
        # Gunakan PostgreSQL bawaan Railway
        return psycopg2.connect(DATABASE_URL, sslmode='require')
    else:
        # Gunakan SQLite lokal sebagai fallback
        return sqlite3.connect(DB_FILE)

def init_db():
    """Membuat tabel database secara otomatis berdasarkan tipe database yang digunakan."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        if DATABASE_URL:
            print("🐘 Mencoba menginisialisasi tabel di PostgreSQL Railway...")
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
            print("🐘 Tabel PostgreSQL sukses diverifikasi dan dibuat!")
        else:
            print("⚠️ DATABASE_URL tidak ditemukan! Menggunakan SQLite lokal...")
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
            print("💾 Tabel SQLite sukses diverifikasi dan dibuat!")
            
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"❌ ERROR INISIALISASI DATABASE: {str(e)}")

def hitung_statistik_performa():
    """
    Mengambil data statistik dengan pencarian kata kunci fleksibel (ILIKE)
    untuk menghindari error format teks dari database.
    """
    conn = get_db_connection()
    if not conn:
        return None
        
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # 1. GUNAKAN ILIKE: Mencari potongan kata tanpa sensitif huruf besar/kecil
            cur.execute("""
                SELECT 
                    COUNT(*) as total,
                    COUNT(CASE WHEN result ILIKE '%PROFIT%' OR result ILIKE '%TP%' THEN 1 END) as wins,
                    COUNT(CASE WHEN result ILIKE '%LOSS%' OR result ILIKE '%SL%' THEN 1 END) as losses,
                    COUNT(CASE WHEN result ILIKE '%OPEN%' THEN 1 END) as opens
                FROM trade_history;
            """)
            stats = cur.fetchone()
            
            # Jika tabel benar-benar kosong
            if stats['total'] == 0:
                return "📝 Belum ada data transaksi di database untuk dihitung."
                
            total_closed = stats['wins'] + stats['losses']
            winrate = (stats['wins'] / total_closed * 100) if total_closed > 0 else 0
            
            # 2. Kueri Kedua untuk Koin Teraktif dengan ILIKE
            cur.execute("""
                SELECT symbol, COUNT(*) as qty,
                COUNT(CASE WHEN result ILIKE '%PROFIT%' OR result ILIKE '%TP%' THEN 1 END) as coin_wins
                FROM trade_history 
                WHERE result NOT ILIKE '%OPEN%'
                GROUP BY symbol 
                ORDER BY qty DESC 
                LIMIT 3;
            """)
            top_coins = cur.fetchall()
            
            # Teks Dashboard Telegram
            laporan = (
                f"📊 *DASHBOARD STATISTIK TRADING*\n"
                f"────────────────────────\n"
                f"📈 Total Sinyal Masuk  : *{stats['total']}*\n"
                f"⏳ Posisi Sedang Aktif : *{stats['opens']}*\n"
                f"✅ Transaksi Win (TP)  : *{stats['wins']}*\n"
                f"❌ Transaksi Lose (SL) : *{stats['losses']}*\n"
                f"────────────────────────\n"
                f"🎯 *WIN RATE SYSTEM   : {winrate:.2f}%*\n"
                f"────────────────────────\n"
            )
            
            if top_coins:
                laporan += "🪙 *PERFORMA PASAR TERAKTIF:*\n"
                for coin in top_coins:
                    laporan += f"• {coin['symbol']}: {coin['qty']} Trade (Win: {coin['coin_wins']})\n"
            
            return laporan
            
    except Exception as e:
        # Menampilkan detail error asli di log Railway Anda agar mudah dilacak jika ada kolom lain yang salah
        print(f"⚠️ SQL Error Detail: {e}")
        return "❌ Gagal memproses data statistik dari database."
    finally:
        conn.close()

def load_saved_positions():
    """Memulihkan data posisi monitoring berjalan dari database saat booting."""
    try:
        global pair_states
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
            print(f"📦 Berhasil memulihkan {len(rows)} posisi aktif dari Database!")
    except Exception as e:
        print(f"❌ Gagal memulihkan posisi dari database: {str(e)}")

def save_open_trade(symbol, tipe, entry, sl, tp, waktu):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if DATABASE_URL:
            # Syntax Upsert khusus PostgreSQL
            cursor.execute('''
                INSERT INTO open_trades (symbol, type, entry, sl, tp, time) 
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (symbol) 
                DO UPDATE SET type = EXCLUDED.type, entry = EXCLUDED.entry, sl = EXCLUDED.sl, tp = EXCLUDED.tp, time = EXCLUDED.time
            ''', (symbol, tipe, entry, sl, tp, waktu))
        else:
            # Syntax Upsert khusus SQLite
            cursor.execute(
                "INSERT OR REPLACE INTO open_trades (symbol, type, entry, sl, tp, time) VALUES (?, ?, ?, ?, ?, ?)",
                (symbol, tipe, entry, sl, tp, waktu)
            )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"❌ Gagal menyimpan open trade ke DB: {str(e)}")

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
        print(f"❌ Gagal menghapus open trade dari DB: {str(e)}")

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
        print(f"❌ Gagal menyimpan trade history ke DB: {str(e)}")

def get_open_trades_dict():
    """Mengembalikan dictionary posisi open dari database dengan normalisasi symbol"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Query sesuai tipe database
        if DATABASE_URL:
            cursor.execute("SELECT symbol, type, entry, sl, tp, time FROM open_trades")
        else:
            cursor.execute("SELECT symbol, type, entry, sl, tp, time FROM open_trades")
            
        rows = cursor.fetchall()
        conn.close()
        
        trades = {}
        for row in rows:
            try:
                # Pastikan row memiliki 6 elemen
                if len(row) >= 6:
                    # Ambil data dengan aman
                    raw_symbol = row[0] if row[0] is not None else ''
                    tipe = str(row[1]) if row[1] is not None else 'UNKNOWN'
                    entry = float(row[2]) if row[2] is not None else 0.0
                    sl = float(row[3]) if row[3] is not None else 0.0
                    tp = float(row[4]) if row[4] is not None else 0.0
                    waktu = str(row[5]) if row[5] is not None else ''
                    
                    # Validasi symbol
                    if not raw_symbol:
                        print(f"⚠️ Symbol kosong untuk data: {row}")
                        continue
                    
                    # Bersihkan symbol
                    symbol = str(raw_symbol).strip()
                    
                    # Normalisasi symbol ke format OKX
                    if '/USDT:USDT' in symbol:
                        symbol = symbol.replace('/USDT:USDT', '-USDT-SWAP')
                    elif '/USDT' in symbol:
                        symbol = symbol.replace('/USDT', '-USDT-SWAP')
                    elif not symbol.endswith('-SWAP') and 'USDT' in symbol:
                        if '/' in symbol:
                            parts = symbol.split('/')
                            if len(parts) == 2:
                                base = parts[0].strip()
                                if base:
                                    symbol = f"{base}-USDT-SWAP"
                        elif '-' in symbol:
                            if not symbol.endswith('-SWAP'):
                                symbol = f"{symbol}-SWAP"
                        else:
                            # Misal: BTCUSDT -> BTC-USDT-SWAP
                            if 'USDT' in symbol:
                                base = symbol.replace('USDT', '')
                                if base:
                                    symbol = f"{base}-USDT-SWAP"
                    
                    # Validasi akhir symbol
                    if not symbol or len(symbol) < 3:
                        print(f"⚠️ Symbol tidak valid setelah normalisasi: {raw_symbol} -> {symbol}")
                        continue
                    
                    # Pastikan format akhir benar
                    if not symbol.endswith('-SWAP'):
                        symbol = f"{symbol}-SWAP"
                    
                    if entry > 0 and sl > 0 and tp > 0:
                        trades[symbol] = {
                            'type': tipe,
                            'entry': entry,
                            'sl': sl,
                            'tp': tp,
                            'time': waktu
                        }
                        print(f"✅ Added to trades: {symbol} ({tipe})")
            except Exception as e:
                print(f"❌ Error processing row {row}: {e}")
                import traceback
                traceback.print_exc()
                continue
                
        print(f"✅ get_open_trades_dict: Menemukan {len(trades)} posisi valid")
        return trades
        
    except Exception as e:
        print(f"❌ Gagal membaca open trades: {str(e)}")
        import traceback
        traceback.print_exc()
        return {}

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
                'symbol': symbol, 'type': tipe, 'entry': float(entry), 'exit': float(exit_price), 'result': result, 'closed_at': closed_at
            })
        return history_list
    except Exception as e:
        print(f"❌ Gagal membaca trade history dari DB: {str(e)}")
        return []

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

# --- MATH METRICS CALCULATORS ---
def calculate_ema(prices, period=200):
    """Menghitung EMA, mengembalikan 0.0 jika data tidak cukup"""
    if not prices or len(prices) < period:
        return 0.0
    
    k = 2 / (period + 1)
    ema = prices[0]
    for price in prices[1:]:
        ema = (price * k) + (ema * (1 - k))
    return ema

def calculate_rsi(prices, period=14):
    """Menghitung RSI, mengembalikan 50.0 jika data tidak cukup"""
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
    """
    Hitung ATR (Average True Range) dari data candle OHLCV.
    Mengembalikan 0.0 jika data tidak cukup.
    """
    if not candles or len(candles) < period + 1:
        return 0.0
    
    true_ranges = []
    for i in range(1, len(candles)):
        try:
            if len(candles[i]) < 5 or len(candles[i-1]) < 5:
                continue
            high = candles[i][2]
            low = candles[i][3]
            prev_close = candles[i-1][4]
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            true_ranges.append(tr)
        except Exception as e:
            print(f"Error calculating ATR: {e}")
            continue
    
    if len(true_ranges) < period:
        return 0.0
    
    return sum(true_ranges[-period:]) / period

def get_atr_sl(candles, invalidation, trade_type, atr_multiplier=1.5, fallback_pct=0.5):
    """
    Hitung SL berbasis ATR.
    Selalu return tuple (float, str)
    """
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
    else:  # SHORT
        sl_flat = invalidation * (1 + fallback_pct / 100)
        if atr <= 0:
            return sl_flat, "flat"
        sl_atr = invalidation + atr * atr_multiplier
        return max(sl_atr, sl_flat), "ATR"

def safe_fetch_ticker(symbol):
    """
    Mengambil ticker dengan error handling yang aman
    Mengembalikan dictionary dengan data atau None
    """
    try:
        # Validasi awal
        if not symbol:
            print("⚠️ Symbol is None or empty")
            return None
            
        # Pastikan symbol adalah string
        symbol = str(symbol).strip()
        if not symbol:
            print("⚠️ Symbol is empty string")
            return None
            
        # Pastikan symbol dalam format yang benar untuk OKX
        original_symbol = symbol
        
        # Normalisasi symbol
        if '/USDT:USDT' in symbol:
            symbol = symbol.replace('/USDT:USDT', '-USDT-SWAP')
        elif '/USDT' in symbol:
            symbol = symbol.replace('/USDT', '-USDT-SWAP')
        elif not symbol.endswith('-SWAP') and 'USDT' in symbol:
            if '/' in symbol:
                parts = symbol.split('/')
                if len(parts) == 2 and parts[0]:
                    symbol = f"{parts[0].strip()}-USDT-SWAP"
            elif '-' in symbol:
                if not symbol.endswith('-SWAP'):
                    symbol = f"{symbol}-SWAP"
            else:
                # Misal: BTCUSDT -> BTC-USDT-SWAP
                if 'USDT' in symbol:
                    base = symbol.replace('USDT', '').strip()
                    if base:
                        symbol = f"{base}-USDT-SWAP"
        
        # Pastikan symbol akhir valid
        if not symbol or len(symbol) < 3:
            print(f"⚠️ Symbol tidak valid setelah normalisasi: {original_symbol} -> {symbol}")
            return None
            
        # Pastikan format akhir benar
        if not symbol.endswith('-SWAP'):
            symbol = f"{symbol}-SWAP"
            
        print(f"DEBUG: Fetching ticker for {symbol} (original: {original_symbol})")
        
        # Coba fetch ticker
        ticker = exchange.fetch_ticker(symbol)
        
        if not ticker or not isinstance(ticker, dict):
            print(f"⚠️ Ticker response not valid for {symbol}")
            return None
            
        # Ambil harga dengan prioritas
        price = None
        try:
            if 'last' in ticker and ticker['last'] is not None:
                price = float(ticker['last'])
                print(f"DEBUG: Using 'last' price: {price}")
            elif 'close' in ticker and ticker['close'] is not None:
                price = float(ticker['close'])
                print(f"DEBUG: Using 'close' price: {price}")
            elif 'bid' in ticker and ticker['bid'] is not None:
                price = float(ticker['bid'])
                print(f"DEBUG: Using 'bid' price: {price}")
            elif 'ask' in ticker and ticker['ask'] is not None:
                price = float(ticker['ask'])
                print(f"DEBUG: Using 'ask' price: {price}")
            else:
                # Coba cari harga di info
                if 'info' in ticker and isinstance(ticker['info'], dict):
                    if 'last' in ticker['info'] and ticker['info']['last'] is not None:
                        price = float(ticker['info']['last'])
                        print(f"DEBUG: Using info['last'] price: {price}")
                    elif 'close' in ticker['info'] and ticker['info']['close'] is not None:
                        price = float(ticker['info']['close'])
                        print(f"DEBUG: Using info['close'] price: {price}")
        except (ValueError, TypeError) as e:
            print(f"⚠️ Error converting price: {e}")
            return None
            
        if price is None or price <= 0:
            print(f"⚠️ No valid price found for {symbol}")
            print(f"DEBUG: Ticker data: {ticker}")
            return None
            
        print(f"✅ Price found for {symbol}: {price}")
        return {'price': price, 'data': ticker}
        
    except Exception as e:
        print(f"⚠️ Error fetching ticker {symbol}: {e}")
        import traceback
        traceback.print_exc()
        return None

def run_telegram_bot():
    print("Telegram Command Listener aktif...")
    bot.infinity_polling()

# --- KEYBOARDS ---
def main_menu_keyboard():
    """Membuat keyboard dengan emoji yang tepat"""
    markup = ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    btn1 = KeyboardButton("🔍 Pantauan Koin")
    btn2 = KeyboardButton("🎯 Setup Aktif (/pola)")
    btn3 = KeyboardButton("📊 Posisi Open")
    btn4 = KeyboardButton("📜 Histori Trade")
    btn5 = KeyboardButton("🤖 Status Sistem")
    markup.add(btn1, btn2, btn3, btn4, btn5)
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
    db_type = "PostgreSQL" if DATABASE_URL else "SQLite"
    welcome_text = (
        f"👋 *Selamat datang di Dashboard OKX Futures Pro Engine (Adaptive Db Ver.)!*\n\n"
        f"Gunakan tombol *Reply Keyboard* di bagian bawah layar Anda untuk bernavigasi."
    )
    bot.send_message(message.chat.id, welcome_text, parse_mode='Markdown')

@bot.message_handler(commands=['debug_symbol'])
def debug_symbol_command(message):
    """Debug untuk cek symbol di database"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        if DATABASE_URL:
            cursor.execute("SELECT symbol, type, entry FROM open_trades")
        else:
            cursor.execute("SELECT symbol, type, entry FROM open_trades")
            
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            bot.reply_to(message, "📭 *Tabel open_trades kosong*", parse_mode='Markdown')
            return
            
        text = "🔍 *DEBUG SYMBOL DI DATABASE:*\n━━━━━━━━━━━━━━━━━━━━━\n"
        for row in rows:
            symbol = row[0] if row[0] else 'None'
            tipe = row[1] if row[1] else 'None'
            entry = row[2] if row[2] else 'None'
            text += f"Symbol: `{symbol}` | Type: {tipe} | Entry: {entry}\n"
            
        bot.reply_to(message, text, parse_mode='Markdown')
        
    except Exception as e:
        bot.reply_to(message, f"❌ Error: `{str(e)}`", parse_mode='Markdown')

@bot.message_handler(commands=['test_open'])
def test_open_positions(message):
    """Command test untuk cek fungsi open positions"""
    try:
        print("DEBUG: test_open_positions dipanggil")
        
        # Coba ambil data langsung
        conn = get_db_connection()
        cursor = conn.cursor()
        
        if DATABASE_URL:
            cursor.execute("SELECT * FROM open_trades")
        else:
            cursor.execute("SELECT * FROM open_trades")
            
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            bot.reply_to(message, "📭 *Tabel open_trades kosong*", parse_mode='Markdown')
            return
            
        # Kirim raw data
        text = "🔍 *RAW DATA OPEN_TRADES:*\n━━━━━━━━━━━━━━━━━━━━━\n"
        for row in rows:
            text += f"`{row}`\n"
            
        # Coba panggil fungsi get_open_trades_dict
        dict_data = get_open_trades_dict()
        text += f"\n📊 *Dictionary result:*\n`{dict_data}`"
        
        bot.reply_to(message, text, parse_mode='Markdown')
        
    except Exception as e:
        bot.reply_to(message, f"❌ Error: `{str(e)}`", parse_mode='Markdown')

@bot.message_handler(commands=['test_price'])
def test_price_command(message):
    """Test untuk cek harga suatu symbol"""
    try:
        args = message.text.split()
        if len(args) < 2:
            bot.reply_to(message, "⚠️ Format: `/test_price <SYMBOL>`\nContoh: `/test_price BTC`", parse_mode='Markdown')
            return
            
        coin = args[1].upper().strip()
        symbol = f"{coin}-USDT-SWAP"
        
        bot.reply_to(message, f"⏳ _Mencoba mengambil harga untuk {symbol}..._", parse_mode='Markdown')
        
        # Coba berbagai metode
        results = []
        
        # Method 1: Ticker
        try:
            ticker = exchange.fetch_ticker(symbol)
            results.append(f"📊 *Ticker result:*\n`{ticker}`")
        except Exception as e:
            results.append(f"❌ Ticker error: {e}")
        
        # Method 2: OHLCV
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe='1m', limit=2)
            if ohlcv and len(ohlcv) > 0:
                last = ohlcv[-1]
                results.append(f"📊 *OHLCV result:*\nOpen: {last[1]}\nHigh: {last[2]}\nLow: {last[3]}\nClose: {last[4]}")
        except Exception as e:
            results.append(f"❌ OHLCV error: {e}")
        
        # Method 3: Safe fetch
        try:
            safe_data = safe_fetch_ticker(symbol)
            results.append(f"📊 *Safe fetch result:*\n`{safe_data}`")
        except Exception as e:
            results.append(f"❌ Safe fetch error: {e}")
        
        bot.reply_to(message, "\n\n".join(results), parse_mode='Markdown')
        
    except Exception as e:
        bot.reply_to(message, f"❌ Error: `{str(e)}`", parse_mode='Markdown')

@bot.message_handler(commands=['statistik', 'stats'])
def send_statistics(message):
    """Handler Telegram untuk merespons perintah /statistik."""
    bot.send_chat_action(message.chat.id, 'typing')
    hasil_laporan = hitung_statistik_performa()
    if hasil_laporan:
        bot.send_message(message.chat.id, hasil_laporan, parse_mode="Markdown")
    else:
        bot.reply_to(message, "❌ Terjadi kendala saat membaca data PostgreSQL.")

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
        candles = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME_LIVE, limit=500)
        
        if not candles or len(candles) < 205:
            bot.reply_to(message, f"❌ Data transaksi historis untuk `{symbol}` tidak mencukupi (Minimal butuh 205 candle).", parse_mode='Markdown')
            return

        total_trades, wins, losses = 0, 0, 0
        state = 'NONE'
        trigger_level, sl_level, tp_level = 0.0, 0.0, 0.0

        for i in range(202, len(candles)):
            current_candle = candles[i]
            prev_candle = candles[i-1]
            current_high, current_low, current_close = current_candle[2], current_candle[3], current_candle[4]
            current_open = current_candle[1]
            prev_close = prev_candle[4]
            
            hist_candles = candles[i - CANDLE_COUNT - 2 : i - 1]
            if not hist_candles: continue
            
            resistance = max([c[2] for c in hist_candles])
            support = min([c[3] for c in hist_candles])
            
            avg_volume = sum([c[5] for c in hist_candles]) / len(hist_candles)
            breakout_volume = candles[i-1][5]
            volume_valid = breakout_volume > (avg_volume * VOLUME_MULTIPLIER)
            
            local_closes = [c[4] for c in candles[:i]]
            
            current_ema200_macro = calculate_ema(local_closes, period=200) 
            current_rsi = calculate_rsi(local_closes, period=14)

            if not current_ema200_macro or not current_rsi:
                continue

            if state == 'NONE':
                if prev_close > resistance and volume_valid and current_close > current_ema200_macro and current_rsi < 70:
                    state = 'BREAKOUT_BULLISH'
                    trigger_level = resistance
                elif prev_close < support and volume_valid and current_close < current_ema200_macro and current_rsi > 30:
                    state = 'BREAKOUT_BEARISH'
                    trigger_level = support

            elif state == 'BREAKOUT_BULLISH':
                body_size = abs(current_close - current_open)
                lower_wick = min(current_open, current_close) - current_low
                
                retest_touched = current_low <= trigger_level * 1.002
                retest_held = current_close > trigger_level * 0.998
                rejection_valid = current_close > current_open and lower_wick > (body_size * 1.2)
                
                if retest_touched and retest_held and rejection_valid:
                    state = 'IN_LONG'
                    sl_level = support
                    risk = current_close - sl_level
                    if risk <= 0: risk = current_close * 0.005
                    tp_level = current_close + (risk * 2)
                    total_trades += 1
                elif current_close < trigger_level * 0.995:
                    state = 'NONE'

            elif state == 'BREAKOUT_BEARISH':
                body_size = abs(current_close - current_open)
                upper_wick = current_high - max(current_open, current_close)
                
                retest_touched = current_high >= trigger_level * 0.998
                retest_held = current_close < trigger_level * 1.002
                rejection_valid = current_close < current_open and upper_wick > (body_size * 1.2)
                
                if retest_touched and retest_held and rejection_valid:
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
            f"📊 *LAPORAN WINRATE (CONFIRMED RETEST)*\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"Aset: `{symbol}` | TF: `{TIMEFRAME_LIVE}`\n"
            f"🔹 Total Sinyal Valid: *{total_trades}*\n"
            f"🟢 Profit (Wins): *{wins}* | 🔴 Loss: *{losses}*\n\n"
            f"🎯 *OPTIMIZED WIN RATE: {winrate:.2f}%* 🔥"
        )
        
        try: bot.delete_message(message.chat.id, loading_msg.message_id)
        except: pass
            
        bot.reply_to(message, report_text, parse_mode='Markdown')

    except Exception as e:
        try: bot.delete_message(message.chat.id, loading_msg.message_id)
        except: pass
        bot.reply_to(message, f"❌ *Gagal memproses backtest.*\nDetail Kendala: `{str(e)}`", parse_mode='Markdown')

@bot.message_handler(commands=['debug_db'])
def debug_database(message):
    """Command untuk debug isi database"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        if DATABASE_URL:
            cursor.execute("SELECT * FROM open_trades")
        else:
            cursor.execute("SELECT * FROM open_trades")
            
        rows = cursor.fetchall()
        
        if not rows:
            bot.reply_to(message, "📭 *Tabel open_trades kosong*", parse_mode='Markdown')
        else:
            text = "📊 *ISI TABEL OPEN_TRADES:*\n━━━━━━━━━━━━━━━━━━━━━\n"
            for row in rows:
                text += f"`{row}`\n"
            bot.reply_to(message, text, parse_mode='Markdown')
            
        conn.close()
        
    except Exception as e:
        bot.reply_to(message, f"❌ *Debug error:* `{str(e)}`", parse_mode='Markdown')

@bot.message_handler(commands=['debug_open'])
def debug_open_positions(message):
    """Debug detail isi open_trades"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        if DATABASE_URL:
            cursor.execute("SELECT * FROM open_trades")
        else:
            cursor.execute("SELECT * FROM open_trades")
            
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            bot.reply_to(message, "📭 *Tabel open_trades kosong*", parse_mode='Markdown')
            return
            
        text = "🔍 *DEBUG OPEN_TRADES:*\n━━━━━━━━━━━━━━━━━━━━━\n"
        text += f"Total rows: {len(rows)}\n\n"
        
        for i, row in enumerate(rows, 1):
            text += f"Row {i}: `{row}`\n"
            if i >= 10:
                text += "... (dan seterusnya)\n"
                break
                
        bot.reply_to(message, text, parse_mode='Markdown')
        
    except Exception as e:
        bot.reply_to(message, f"❌ Debug error: `{str(e)}`", parse_mode='Markdown')

@bot.message_handler(func=lambda msg: True)
def handle_reply_keyboard(message):
    text = message.text
    print(f"DEBUG: Keyboard clicked: {text}")  # Log untuk debug
    
    if text == "🔍 Pantauan Koin":
        bot.send_message(message.chat.id, "📂 *Silakan pilih kategori koin:*", parse_mode='Markdown', reply_markup=pairs_category_keyboard())
    elif text == "🎯 Setup Aktif (/pola)":
        send_active_patterns(message)
    elif text == "📊 Posisi Open":
        print("DEBUG: Memproses Posisi Open")  # Log
        try:
            send_open_positions(message)
        except Exception as e:
            print(f"❌ Error di handler Posisi Open: {e}")  # Log
            import traceback
            traceback.print_exc()
            bot.reply_to(
                message,
                f"❌ *Error saat menampilkan posisi:*\n`{str(e)}`",
                parse_mode='Markdown'
            )
    elif text == "📜 Histori Trade":
        send_trade_history(message)
    elif text == "🤖 Status Sistem":
        db_type = "PostgreSQL (Railway)" if DATABASE_URL else "SQLite (Fallback)"
        bot.reply_to(message, f"✅ *Bot Status:* Online.\n🎯 *Engine:* Memantau {len(active_pairs)} koin.\n🗄️ *Database:* {db_type} Terkoneksi & Aman.", parse_mode='Markdown')

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
    """Menampilkan semua posisi yang sedang open dengan split jika terlalu panjang"""
    try:
        print("DEBUG: send_open_positions dipanggil")
        
        # Kirim pesan loading
        loading_msg = bot.send_message(message.chat.id, "⏳ _Mengambil data posisi..._", parse_mode='Markdown')
        
        # Ambil data dari database
        open_trades = get_open_trades_dict()
        print(f"DEBUG: open_trades = {open_trades}")
        
        if not open_trades:
            bot.edit_message_text(
                "📭 *Tidak ada posisi trading yang aktif saat ini.*",
                chat_id=message.chat.id,
                message_id=loading_msg.message_id,
                parse_mode='Markdown'
            )
            return
            
        # Hapus pesan loading
        try:
            bot.delete_message(message.chat.id, loading_msg.message_id)
        except Exception as e:
            print(f"DEBUG: Gagal hapus loading message: {e}")
        
        # Buat list pesan
        messages = []
        current_message = "📊 *DAFTAR POSISI YANG SEDANG OPEN:*\n━━━━━━━━━━━━━━━━━━━━━\n"
        has_data = False
        error_count = 0
        pos_count = 0
        
        for symbol, data in open_trades.items():
            try:
                # Validasi symbol
                if not symbol or not isinstance(symbol, str):
                    print(f"⚠️ Symbol tidak valid: {symbol}")
                    error_count += 1
                    continue
                
                # Bersihkan symbol
                clean_symbol = symbol.strip()
                if not clean_symbol:
                    print(f"⚠️ Symbol kosong setelah di-strip")
                    error_count += 1
                    continue
                
                print(f"DEBUG: Processing {clean_symbol}")
                
                # Ambil data
                tipe = data.get('type', 'UNKNOWN')
                entry_price = float(data.get('entry', 0))
                sl_price = float(data.get('sl', 0))
                tp_price = float(data.get('tp', 0))
                
                # Validasi data
                if entry_price == 0 or sl_price == 0 or tp_price == 0:
                    print(f"⚠️ Data tidak lengkap untuk {clean_symbol}: {data}")
                    error_count += 1
                    continue
                
                # Ambil harga terkini
                current_price = entry_price
                price_found = False
                
                # Coba fetch ticker dengan symbol yang sudah dinormalisasi
                try:
                    ticker_data = safe_fetch_ticker(clean_symbol)
                    if ticker_data and isinstance(ticker_data, dict) and 'price' in ticker_data:
                        current_price = float(ticker_data['price'])
                        price_found = True
                        print(f"DEBUG: {clean_symbol} current price = {current_price}")
                except Exception as e:
                    print(f"⚠️ Error fetching price for {clean_symbol}: {e}")
                
                # Jika gagal, coba dengan format lain
                if not price_found:
                    # Coba tanpa -SWAP
                    if clean_symbol.endswith('-SWAP'):
                        alt_symbol = clean_symbol.replace('-SWAP', '')
                        try:
                            ticker_data = safe_fetch_ticker(alt_symbol)
                            if ticker_data and isinstance(ticker_data, dict) and 'price' in ticker_data:
                                current_price = float(ticker_data['price'])
                                price_found = True
                                print(f"DEBUG: {alt_symbol} current price = {current_price}")
                        except Exception as e:
                            print(f"⚠️ Error fetching price for {alt_symbol}: {e}")
                
                # Jika masih gagal, pakai entry price
                if not price_found:
                    print(f"⚠️ No price found for {clean_symbol}, using entry price: {entry_price}")

                # Hitung PnL
                if tipe.upper() == 'LONG':
                    pnl_nominal = current_price - entry_price
                    pnl_percent = (pnl_nominal / entry_price) * 100
                    tipe_emoji = "🟢 LONG"
                else:
                    pnl_nominal = entry_price - current_price
                    pnl_percent = (pnl_nominal / entry_price) * 100
                    tipe_emoji = "🔴 SHORT"

                # Format status PnL
                if pnl_nominal >= 0:
                    pnl_status = f"✅ *+{pnl_percent:.2f}%*"
                else:
                    pnl_status = f"❌ *{pnl_percent:.2f}%*"

                # Buat teks untuk posisi ini
                coin = clean_symbol.replace('-USDT-SWAP', '').replace('-SWAP', '')
                pos_text = (
                    f"• *{coin}* ({tipe_emoji})\n"
                    f"  📥 Entry: `{entry_price:.4f}`\n"
                    f"  ⚡ Current: `{current_price:.4f}`\n"
                    f"  🛑 SL: `{sl_price:.4f}` | 🎯 TP: `{tp_price:.4f}`\n"
                    f"  💰 PnL: {pnl_status}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                )
                
                # Cek batas karakter
                if len(current_message) + len(pos_text) > 4000:
                    messages.append(current_message)
                    current_message = "📊 *DAFTAR POSISI YANG SEDANG OPEN (LANJUTAN):*\n━━━━━━━━━━━━━━━━━━━━━\n"
                
                current_message += pos_text
                has_data = True
                pos_count += 1
                
            except Exception as e:
                print(f"❌ Error processing {symbol}: {e}")
                import traceback
                traceback.print_exc()
                error_count += 1
                continue
        
        # Tambahkan pesan terakhir
        if current_message and current_message != "📊 *DAFTAR POSISI YANG SEDANG OPEN:*\n━━━━━━━━━━━━━━━━━━━━━\n":
            messages.append(current_message)
        
        # Kirim semua pesan
        if has_data:
            print(f"DEBUG: Mengirim {len(messages)} pesan dengan {pos_count} posisi")
            for i, msg in enumerate(messages):
                bot.send_message(message.chat.id, msg, parse_mode='Markdown')
        else:
            print(f"DEBUG: Tidak ada data valid, error_count={error_count}")
            bot.send_message(
                message.chat.id,
                f"❌ *Tidak ada data posisi yang valid untuk ditampilkan.*\nTotal posisi: {len(open_trades)}, Error: {error_count}",
                parse_mode='Markdown'
            )
            
    except Exception as e:
        print(f"❌ Error di send_open_positions: {str(e)}")
        import traceback
        traceback.print_exc()
        bot.reply_to(
            message,
            f"❌ *Gagal menampilkan posisi open.*\nDetail: `{str(e)}`",
            parse_mode='Markdown'
        )
                    
def send_trade_history(message):
    """Menampilkan histori trade dengan split jika terlalu panjang"""
    history = get_recent_history(10)
    if not history:
        bot.send_message(message.chat.id, "📜 *Belum ada histori transaksi di database.*", parse_mode='Markdown')
        return
    
    # Buat list pesan
    messages = []
    current_message = f"📜 *RIWAYAT TRANSAKSI TERAKHIR ({len(history)}):*\n━━━━━━━━━━━━━━━━━━━━━\n"
    
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

            if data['result'] == 'TP':
                hasil_text = f"✅ TAKE PROFIT (+{pnl_pct:.2f}%)"
                emoji_prefix = "🟢"
            else:
                hasil_text = f"❌ STOP LOSS ({pnl_pct:.2f}%)"
                emoji_prefix = "🔴"

            pos_text = (
                f"• *{coin}* | {emoji_prefix} *{data['result']}*\n"
                f"  ↕️ Tipe: `{tipe}` | *{pnl_pct:+.2f}%*\n"
                f"  📥 Entry: `{entry:.4f}` | 🚪 Exit: `{exit_price:.4f}`\n"
                f"  Status Akhir: {hasil_text}\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
            )
            
            # Cek jika menambahkan pos_text akan melebihi batas
            if len(current_message) + len(pos_text) > 4000:
                messages.append(current_message)
                current_message = f"📜 *RIWAYAT TRANSAKSI (LANJUTAN):*\n━━━━━━━━━━━━━━━━━━━━━\n"
            
            current_message += pos_text
            
        except Exception as e:
            print(f"Error processing history item: {e}")
            continue
    
    # Tambahkan pesan terakhir
    if current_message and current_message != f"📜 *RIWAYAT TRANSAKSI TERAKHIR ({len(history)}):*\n━━━━━━━━━━━━━━━━━━━━━\n":
        messages.append(current_message)
    
    # Kirim semua pesan
    for msg in messages:
        bot.send_message(message.chat.id, msg, parse_mode='Markdown')

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

# --- CORE MARKETS SCANNER WITH CONFIRMED RETEST ---
def scan_breakout_retest(symbol):
    global pair_states
    try:
        # Validasi awal
        if not symbol:
            return
            
        # Ambil data dengan error handling
        try:
            candles = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME_LIVE, limit=100)
        except Exception as e:
            print(f"Gagal fetch data untuk {symbol}: {e}")
            return
            
        if not candles or len(candles) < CANDLE_COUNT:
            print(f"Data tidak cukup untuk {symbol}: {len(candles)} candles")
            return

        # Pastikan semua data candle valid
        for candle in candles:
            if not candle or len(candle) < 6:
                print(f"Data candle tidak valid untuk {symbol}")
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

        waktu_sekarang = time.strftime("%H:%M:%S")

        # Monitoring Jika Berstatus LONG
        if pair_states[symbol]['status'] == 'IN_LONG':
            sl_level = pair_states[symbol]['sl']
            tp_level = pair_states[symbol]['tp']
            open_trades = get_open_trades_dict()
            entry_p = open_trades[symbol]['entry'] if symbol in open_trades else current_close
            
            if current_low <= sl_level:
                pnl_pct = ((sl_level - entry_p) / entry_p) * 100
                bot.send_message(TELEGRAM_CHAT_ID, f"🔴 *TRADE CLOSED (STOP LOSS)*\n\nPair: `{symbol.replace('-USDT-SWAP', '')}`\n📥 Entry: `{entry_p:.4f}`\n🚪 Exit (SL): `{sl_level:.4f}`\n📉 Hasil: *{pnl_pct:.2f}%*", parse_mode='Markdown')
                if symbol in open_trades:
                    insert_trade_history(symbol, 'LONG', entry_p, sl_level, 'SL', waktu_sekarang)
                    delete_open_trade(symbol)
                pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}
                return
            elif current_high >= tp_level:
                pnl_pct = ((tp_level - entry_p) / entry_p) * 100
                bot.send_message(TELEGRAM_CHAT_ID, f"🟢 *TRADE CLOSED (TAKE PROFIT) 🔥*\n\nPair: `{symbol.replace('-USDT-SWAP', '')}`\n📥 Entry: `{entry_p:.4f}`\n🚪 Exit (TP): `{tp_level:.4f}`\n📈 Profit: *+{pnl_pct:.2f}%*", parse_mode='Markdown')
                if symbol in open_trades:
                    insert_trade_history(symbol, 'LONG', entry_p, tp_level, 'TP', waktu_sekarang)
                    delete_open_trade(symbol)
                pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}
                return
            else:
                return

        # Monitoring Jika Berstatus SHORT
        if pair_states[symbol]['status'] == 'IN_SHORT':
            sl_level = pair_states[symbol]['sl']
            tp_level = pair_states[symbol]['tp']
            open_trades = get_open_trades_dict()
            entry_p = open_trades[symbol]['entry'] if symbol in open_trades else current_close
            
            if current_high >= sl_level:
                pnl_pct = ((entry_p - sl_level) / entry_p) * 100
                bot.send_message(TELEGRAM_CHAT_ID, f"🔴 *TRADE CLOSED (STOP LOSS)*\n\nPair: `{symbol.replace('-USDT-SWAP', '')}`\n📥 Entry: `{entry_p:.4f}`\n🚪 Exit (SL): `{sl_level:.4f}`\n📉 Hasil: *{pnl_pct:.2f}%*", parse_mode='Markdown')
                if symbol in open_trades:
                    insert_trade_history(symbol, 'SHORT', entry_p, sl_level, 'SL', waktu_sekarang)
                    delete_open_trade(symbol)
                pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}
                return
            elif current_low <= tp_level:
                pnl_pct = ((entry_p - tp_level) / entry_p) * 100
                bot.send_message(TELEGRAM_CHAT_ID, f"🟢 *TRADE CLOSED (TAKE PROFIT) 🔥*\n\nPair: `{symbol.replace('-USDT-SWAP', '')}`\n📥 Entry: `{entry_p:.4f}`\n🚪 Exit (TP): `{tp_level:.4f}`\n📈 Profit: *+{pnl_pct:.2f}%*", parse_mode='Markdown')
                if symbol in open_trades:
                    insert_trade_history(symbol, 'SHORT', entry_p, tp_level, 'TP', waktu_sekarang)
                    delete_open_trade(symbol)
                pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}
                return
            else:
                return

        # Analisis Sinyal Teknis (Confirmed Breakout & Retest)
        try:
            macro_candles = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME_MACRO, limit=205)
            macro_closes = [c[4] for c in macro_candles]
            ema200_macro = calculate_ema(macro_closes, period=200)
        except Exception as e:
            print(f"Error getting macro data for {symbol}: {e}")
            return

        historical_candles = candles[-52:-2]
        if not historical_candles:
            return
            
        resistance = max([c[2] for c in historical_candles])
        support = min([c[3] for c in historical_candles])
        avg_volume = sum([c[5] for c in historical_candles]) / len(historical_candles)
        volume_valid = prev_candle[5] > (avg_volume * VOLUME_MULTIPLIER)
        current_rsi = calculate_rsi([c[4] for c in candles], period=14)

        # BULLISH BREAKOUT
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
                invalidation_long = pair_states[symbol]['sl']
                stop_loss, sl_method = get_atr_sl(candles, invalidation_long, 'LONG')
                risk = current_close - stop_loss
                if risk <= 0:
                    risk = current_close * 0.005
                take_profit = current_close + (risk * 2)
                
                pair_states[symbol] = {'status': 'IN_LONG', 'level': target_res, 'sl': stop_loss, 'tp': take_profit}
                save_open_trade(symbol, 'LONG', current_close, stop_loss, take_profit, waktu_sekarang)
                
                atr_val = calculate_atr(candles, period=14)
                if atr_val > 0:
                    atr_info = f"ATR14={atr_val:.4f}"
                else:
                    atr_info = "ATR=N/A (gunakan flat)"
                    
                bot.send_message(TELEGRAM_CHAT_ID, f"🎯 *RETEST SUCCESS (ENTRY LONG)*\n\nPair: `{symbol.replace('-USDT-SWAP', '')}`\n📥 Entry: `{current_close:.4f}`\n🛑 SL: `{stop_loss:.4f}` ({sl_method}, {atr_info})\n🎯 TP: `{take_profit:.4f}`", parse_mode='Markdown')

        # BEARISH BREAKDOWN
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
                invalidation_short = pair_states[symbol]['sl']
                stop_loss, sl_method = get_atr_sl(candles, invalidation_short, 'SHORT')
                risk = stop_loss - current_close
                if risk <= 0:
                    risk = current_close * 0.005
                take_profit = current_close - (risk * 2)
                
                pair_states[symbol] = {'status': 'IN_SHORT', 'level': target_sup, 'sl': stop_loss, 'tp': take_profit}
                save_open_trade(symbol, 'SHORT', current_close, stop_loss, take_profit, waktu_sekarang)
                
                atr_val = calculate_atr(candles, period=14)
                if atr_val > 0:
                    atr_info = f"ATR14={atr_val:.4f}"
                else:
                    atr_info = "ATR=N/A (gunakan flat)"
                    
                bot.send_message(TELEGRAM_CHAT_ID, f"🎯 *RETEST SUCCESS (ENTRY SHORT)*\n\nPair: `{symbol.replace('-USDT-SWAP', '')}`\n📥 Entry: `{current_close:.4f}`\n🛑 SL: `{stop_loss:.4f}` ({sl_method}, {atr_info})\n🎯 TP: `{take_profit:.4f}`", parse_mode='Markdown')

        # Reset jika breakout gagal (gunakan variabel yang sudah didefinisikan)
        if pair_states[symbol]['status'] == 'BREAKOUT_BULLISH':
            target_res = pair_states[symbol]['level']
            if current_close < target_res * 0.995:
                pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}
        elif pair_states[symbol]['status'] == 'BREAKOUT_BEARISH':
            target_sup = pair_states[symbol]['level']
            if current_close > target_sup * 1.005:
                pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}

    except Exception as e:
        # Log error dengan detail
        print(f"Error scan {symbol}: {str(e)}")
        import traceback
        traceback.print_exc()
        
        # Reset state jika terjadi error (kecuali dalam posisi)
        if symbol in pair_states:
            if pair_states[symbol]['status'] not in ['IN_LONG', 'IN_SHORT']:
                pair_states[symbol] = {'status': 'NONE', 'level': 0.0, 'sl': 0.0, 'tp': 0.0}

def main():
    print("Memulai aplikasi...")
    init_db()               
    load_saved_positions()  
    
    tele_thread = threading.Thread(target=run_telegram_bot)
    tele_thread.daemon = True
    tele_thread.start()

    bot.send_message(TELEGRAM_CHAT_ID, f"🤖 *Bot OKX Engine Pro v2.2 Aktif!* 🎉\n\nSistem database SQL adaptif telah terhubung dengan sukses.", parse_mode='Markdown', reply_markup=main_menu_keyboard())

    while True:
        for symbol in active_pairs:
            scan_breakout_retest(symbol)
            time.sleep(2)
        time.sleep(10)

if __name__ == "__main__":
    main()
