"""
Trading Monitor 24/7 — XAUUSD, BTCUSD, EURUSD, GBPUSD
Corre cada 15 minutos usando Twelve Data API (gratis).
Envía alertas a Telegram con imagen del gráfico.

Setup:
1. pip install requests pandas matplotlib schedule
2. Registrarse gratis en https://twelvedata.com → obtener API key
3. Poner tu API key en TWELVE_DATA_KEY abajo
4. Subir a Replit/PythonAnywhere y ejecutar: python trading_monitor_24h.py
"""

from __future__ import annotations
import requests
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import schedule
import time
import io
import logging
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone
from typing import Optional, Dict, List

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger(__name__)

# ─── CONFIGURACIÓN ───────────────────────────────────────────────────────────
TWELVE_DATA_KEY = "543a4e7283e14a0bb52b21f6c2cf2d7b"
TELEGRAM_TOKEN  = "7910004144:AAGGLubMLgTjfmQbVrjcAVFPl5fnVMVzEu4"
TELEGRAM_CHATID = "8178693253"
SYMBOLS = ["XAU/USD", "BTC/USD", "EUR/USD", "GBP/USD"]
INTERVAL = "1h"
OUTPUTSIZE = 220   # suficiente para EMA200 + Fibonacci
# ─────────────────────────────────────────────────────────────────────────────

# Rastreo de señales ya enviadas para no repetir
sent_signals: Dict[str, str] = {}


def fetch_ohlcv(symbol: str) -> Optional[pd.DataFrame]:
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": INTERVAL,
        "outputsize": OUTPUTSIZE,
        "apikey": TWELVE_DATA_KEY,
        "order": "ASC",
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        data = r.json()
        if data.get("status") == "error":
            log.warning(f"{symbol}: API error — {data.get('message')}")
            return None
        df = pd.DataFrame(data["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.set_index("datetime")
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col])
        return df
    except Exception as e:
        log.error(f"{symbol}: fetch error — {e}")
        return None


def calc_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def calc_fibonacci(df: pd.DataFrame) -> dict:
    swing_high = df["high"].iloc[-200:].max()
    swing_low  = df["low"].iloc[-200:].min()
    rng = swing_high - swing_low
    return {
        "high":  swing_high,
        "low":   swing_low,
        "f236":  swing_high - rng * 0.236,
        "f382":  swing_high - rng * 0.382,
        "f500":  swing_high - rng * 0.500,
        "f618":  swing_high - rng * 0.618,
        "f786":  swing_high - rng * 0.786,
    }


def check_signals(symbol: str, df: pd.DataFrame) -> List[dict]:
    signals = []
    close   = df["close"]
    high    = df["high"]
    low     = df["low"]

    ema50  = calc_ema(close, 50)
    ema200 = calc_ema(close, 200)
    fib    = calc_fibonacci(df)

    price     = close.iloc[-1]
    e50       = ema50.iloc[-1]
    e200      = ema200.iloc[-1]
    e50_prev  = ema50.iloc[-2]
    e200_prev = ema200.iloc[-2]
    dist_pct  = (price - e50) / e50 * 100

    # Momentum ROC-5
    roc5 = (price - close.iloc[-6]) / close.iloc[-6] * 100 if len(close) >= 6 else 0

    # Candle body
    body     = abs(close.iloc[-1] - df["open"].iloc[-1])
    avg_body = abs(close - df["open"]).rolling(14).mean().iloc[-1]

    golden_cross = e50 > e200
    death_cross  = e50 < e200

    # Señal 1: GOLDEN CROSS nuevo
    if e50 > e200 and e50_prev <= e200_prev:
        signals.append({
            "type": "BUY_GOLDEN_CROSS",
            "symbol": symbol,
            "price": price,
            "e50": e50, "e200": e200, "fib": fib,
        })

    # Señal 2: DEATH CROSS nuevo
    if e50 < e200 and e50_prev >= e200_prev:
        signals.append({
            "type": "SELL_DEATH_CROSS",
            "symbol": symbol,
            "price": price,
            "e50": e50, "e200": e200, "fib": fib,
        })

    # Señal 3: BUY en zona Fibonacci 38.2-61.8% con Golden Cross
    in_fib_zone = fib["f618"] <= price <= fib["f382"]
    if golden_cross and in_fib_zone:
        signals.append({
            "type": "BUY_FIB_ZONE",
            "symbol": symbol,
            "price": price,
            "e50": e50, "e200": e200, "fib": fib,
        })

    # Señal 4: SELL caída agresiva bajo EMA50
    if price < e50 and dist_pct < -0.3 and roc5 < -0.5:
        signals.append({
            "type": "SELL_BELOW_EMA50",
            "symbol": symbol,
            "price": price,
            "e50": e50, "e200": e200, "fib": fib,
        })

    # Señal 5: ALERTA ESPECIAL XAUUSD precio <= 4641 con Golden Cross
    if "XAU" in symbol and price <= 4641 and golden_cross:
        signals.append({
            "type": "XAUUSD_SPECIAL_BUY",
            "symbol": symbol,
            "price": price,
            "e50": e50, "e200": e200, "fib": fib,
        })

    # Señal 6: BREAKOUT — rompe máximo/mínimo 20 barras con vela grande
    high20 = high.iloc[-21:-1].max()
    low20  = low.iloc[-21:-1].min()
    if price > high20 and body > avg_body * 1.5 and close.iloc[-1] > df["open"].iloc[-1]:
        signals.append({
            "type": "BREAKOUT_BUY",
            "symbol": symbol,
            "price": price,
            "e50": e50, "e200": e200, "fib": fib,
        })
    if price < low20 and body > avg_body * 1.5 and close.iloc[-1] < df["open"].iloc[-1]:
        signals.append({
            "type": "BREAKOUT_SELL",
            "symbol": symbol,
            "price": price,
            "e50": e50, "e200": e200, "fib": fib,
        })

    # Señal 7: TENDENCIA FUERTE > 1.5% con momentum
    if dist_pct > 1.5 and roc5 > 0.8 and golden_cross:
        signals.append({
            "type": "STRONG_BULL_TREND",
            "symbol": symbol,
            "price": price,
            "e50": e50, "e200": e200, "fib": fib,
        })
    if dist_pct < -1.5 and roc5 < -0.8:
        signals.append({
            "type": "STRONG_BEAR_TREND",
            "symbol": symbol,
            "price": price,
            "e50": e50, "e200": e200, "fib": fib,
        })

    return signals


def build_caption(sig: dict) -> str:
    t     = sig["type"]
    sym   = sig["symbol"].replace("/", "")
    price = sig["price"]
    e50   = sig["e50"]
    e200  = sig["e200"]
    fib   = sig["fib"]

    # Calcular niveles de entrada/SL/TPs
    atr_est = price * 0.005  # ~0.5% como ATR aproximado

    if t == "XAUUSD_SPECIAL_BUY":
        return (
            f"🟡 {sym} — ALERTA ESPECIAL BUY\n"
            f"Precio: {price:.2f}\n"
            f"EMA50: {e50:.2f} | EMA200: {e200:.2f}\n\n"
            f"Entrada: ~4,635\nSL: 4,615\n"
            f"TP1: 4,700 | TP2: 4,760 | TP3: 4,850"
        )

    if t == "SELL_DEATH_CROSS":
        sl  = e50 + 26
        tp1, tp2, tp3 = 4600, 4560, 4500
        if "BTC" in sym:
            sl  = e50 + 150
            tp1 = round(price * 0.995, 0)
            tp2 = round(price * 0.990, 0)
            tp3 = round(price * 0.983, 0)
        return (
            f"🔴 {sym} — SELL (Death Cross)\n"
            f"Precio: {price:.2f}\n"
            f"EMA50: {e50:.2f} | EMA200: {e200:.2f}\n\n"
            f"Entrada: {price:.2f}\nSL: {sl:.2f}\n"
            f"TP1: {tp1} | TP2: {tp2} | TP3: {tp3}"
        )

    if t == "BUY_GOLDEN_CROSS":
        sl  = e50 - atr_est
        tp1 = round(price + atr_est * 2, 2)
        tp2 = round(price + atr_est * 4, 2)
        tp3 = round(price + atr_est * 7, 2)
        return (
            f"🟢 {sym} — BUY (Golden Cross)\n"
            f"Precio: {price:.2f}\n"
            f"EMA50: {e50:.2f} | EMA200: {e200:.2f}\n\n"
            f"Entrada: {price:.2f}\nSL: {sl:.2f}\n"
            f"TP1: {tp1} | TP2: {tp2} | TP3: {tp3}"
        )

    if t == "BUY_FIB_ZONE":
        sl  = round(fib["f618"] - atr_est, 2)
        tp1 = round(fib["f236"], 2)
        tp2 = round(fib["high"], 2)
        tp3 = round(fib["high"] + atr_est * 3, 2)
        return (
            f"🟢 {sym} — BUY (Zona Fibonacci)\n"
            f"Precio: {price:.2f} | Fib 38.2-61.8%\n"
            f"EMA50: {e50:.2f} | Golden Cross activo\n\n"
            f"Entrada: {price:.2f}\nSL: {sl:.2f}\n"
            f"TP1: {tp1} | TP2: {tp2} | TP3: {tp3}"
        )

    if t == "SELL_BELOW_EMA50":
        sl  = round(e50 + atr_est, 2)
        tp1 = round(price - atr_est * 2, 2)
        tp2 = round(price - atr_est * 4, 2)
        tp3 = round(price - atr_est * 7, 2)
        return (
            f"🔴 {sym} — SELL (Caída bajo EMA50)\n"
            f"Precio: {price:.2f}\n"
            f"EMA50: {e50:.2f} | Dist: {(price-e50)/e50*100:.2f}%\n\n"
            f"Entrada: {price:.2f}\nSL: {sl:.2f}\n"
            f"TP1: {tp1} | TP2: {tp2} | TP3: {tp3}"
        )

    if t == "BREAKOUT_BUY":
        sl  = round(price - atr_est * 1.5, 2)
        tp1 = round(price + atr_est * 2, 2)
        tp2 = round(price + atr_est * 4, 2)
        tp3 = round(price + atr_est * 7, 2)
        return (
            f"🚀 {sym} — BREAKOUT BUY\n"
            f"Precio: {price:.2f} (rompe máximo 20 barras)\n"
            f"EMA50: {e50:.2f} | EMA200: {e200:.2f}\n\n"
            f"Entrada: {price:.2f}\nSL: {sl:.2f}\n"
            f"TP1: {tp1} | TP2: {tp2} | TP3: {tp3}"
        )

    if t == "BREAKOUT_SELL":
        sl  = round(price + atr_est * 1.5, 2)
        tp1 = round(price - atr_est * 2, 2)
        tp2 = round(price - atr_est * 4, 2)
        tp3 = round(price - atr_est * 7, 2)
        return (
            f"🔴 {sym} — BREAKOUT SELL\n"
            f"Precio: {price:.2f} (rompe mínimo 20 barras)\n"
            f"EMA50: {e50:.2f} | EMA200: {e200:.2f}\n\n"
            f"Entrada: {price:.2f}\nSL: {sl:.2f}\n"
            f"TP1: {tp1} | TP2: {tp2} | TP3: {tp3}"
        )

    if t == "STRONG_BULL_TREND":
        sl  = round(e50 - atr_est, 2)
        tp1 = round(price + atr_est * 2, 2)
        tp2 = round(price + atr_est * 4, 2)
        tp3 = round(price + atr_est * 7, 2)
        return (
            f"📈 {sym} — TENDENCIA FUERTE ALCISTA\n"
            f"Precio: {price:.2f} | +{(price-e50)/e50*100:.2f}% sobre EMA50\n"
            f"EMA50: {e50:.2f} | Golden Cross activo\n\n"
            f"Entrada: {price:.2f}\nSL: {sl:.2f}\n"
            f"TP1: {tp1} | TP2: {tp2} | TP3: {tp3}"
        )

    if t == "STRONG_BEAR_TREND":
        sl  = round(e50 + atr_est, 2)
        tp1 = round(price - atr_est * 2, 2)
        tp2 = round(price - atr_est * 4, 2)
        tp3 = round(price - atr_est * 7, 2)
        return (
            f"📉 {sym} — TENDENCIA FUERTE BAJISTA\n"
            f"Precio: {price:.2f} | {(price-e50)/e50*100:.2f}% bajo EMA50\n"
            f"EMA50: {e50:.2f} | EMA200: {e200:.2f}\n\n"
            f"Entrada: {price:.2f}\nSL: {sl:.2f}\n"
            f"TP1: {tp1} | TP2: {tp2} | TP3: {tp3}"
        )

    return f"⚡ {sym} — {t}\nPrecio: {price:.2f}"


def generate_chart(symbol: str, df: pd.DataFrame, sig: dict) -> bytes:
    close  = df["close"].iloc[-60:]
    ema50  = calc_ema(df["close"], 50).iloc[-60:]
    ema200 = calc_ema(df["close"], 200).iloc[-60:]
    fib    = sig["fib"]

    fig, ax = plt.subplots(figsize=(12, 6), facecolor="#131722")
    ax.set_facecolor("#131722")

    ax.plot(close.index, close.values,    color="#2962FF", linewidth=1.5, label="Price")
    ax.plot(ema50.index, ema50.values,    color="#FF6D00", linewidth=1.2, label="EMA50")
    ax.plot(ema200.index, ema200.values,  color="#E040FB", linewidth=1.2, label="EMA200")

    # Fibonacci zones
    ax.axhline(fib["f382"], color="#26A69A", linestyle="--", linewidth=0.8, alpha=0.7, label="Fib 38.2%")
    ax.axhline(fib["f618"], color="#EF5350", linestyle="--", linewidth=0.8, alpha=0.7, label="Fib 61.8%")
    ax.axhspan(fib["f618"], fib["f382"], alpha=0.08, color="#26A69A")

    # Current price line
    ax.axhline(sig["price"], color="#FFEB3B", linestyle="-", linewidth=0.6, alpha=0.5)

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M"))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=6))
    plt.xticks(rotation=45, color="#B2B5BE", fontsize=7)
    plt.yticks(color="#B2B5BE", fontsize=8)
    ax.tick_params(colors="#B2B5BE")
    for spine in ax.spines.values():
        spine.set_edgecolor("#363A45")

    sig_type = sig["type"]
    color = "#26A69A" if "BUY" in sig_type or "BULL" in sig_type else "#EF5350"
    title = f"{symbol.replace('/', '')} 1H — {sig_type.replace('_', ' ')}"
    ax.set_title(title, color=color, fontsize=13, fontweight="bold", pad=10)
    ax.legend(loc="upper left", facecolor="#1E2130", edgecolor="#363A45",
              labelcolor="#B2B5BE", fontsize=8)
    ax.grid(color="#363A45", linewidth=0.4, alpha=0.6)

    buf = io.BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format="png", dpi=120, bbox_inches="tight",
                facecolor="#131722", edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def send_telegram_photo(caption: str, img_bytes: bytes, symbol: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    files = {"photo": (f"{symbol.replace('/', '')}_signal.png", img_bytes, "image/png")}
    data  = {"chat_id": TELEGRAM_CHATID, "caption": caption}
    try:
        r = requests.post(url, data=data, files=files, timeout=20)
        result = r.json()
        if result.get("ok"):
            log.info(f"Telegram enviado: {caption[:50]}")
        else:
            log.warning(f"Telegram error: {result}")
    except Exception as e:
        log.error(f"Telegram send error: {e}")


def send_telegram_text(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, data={"chat_id": TELEGRAM_CHATID, "text": text}, timeout=15)
        print(f"[TELEGRAM] status={r.status_code} body={r.text[:200]}", flush=True)
        log.info(f"Telegram text status={r.status_code}")
    except Exception as e:
        print(f"[TELEGRAM ERROR] {e}", flush=True)
        log.error(f"Telegram text error: {e}")


def send_heartbeat():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    send_telegram_text(
        f"MONITOR ACTIVO - {now}\n"
        f"Chequeando: XAUUSD, BTCUSD, EURUSD, GBPUSD cada 15 min\n"
        f"Railway corriendo 24/7"
    )
    log.info("Heartbeat enviado")


def run_monitor():
    log.info("=== Ciclo de monitoreo iniciado ===")
    for symbol in SYMBOLS:
        df = fetch_ohlcv(symbol)
        if df is None or len(df) < 210:
            log.warning(f"{symbol}: datos insuficientes, saltando")
            continue

        signals = check_signals(symbol, df)
        for sig in signals:
            key = f"{symbol}_{sig['type']}"
            # Evitar re-enviar la misma señal en la misma hora
            bar_time = df.index[-1].strftime("%Y%m%d%H")
            if sent_signals.get(key) == bar_time:
                continue
            sent_signals[key] = bar_time

            caption   = build_caption(sig)
            img_bytes = generate_chart(symbol, df, sig)
            send_telegram_photo(caption, img_bytes, symbol)
            log.info(f"SEÑAL: {symbol} {sig['type']} @ {sig['price']:.2f}")
            time.sleep(1)

        if not signals:
            log.info(f"{symbol}: sin señales")

        time.sleep(2)  # respetar rate limit API


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/test":
            # Dispara un mensaje de prueba a Telegram y devuelve el resultado
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            try:
                r = requests.post(url, data={"chat_id": TELEGRAM_CHATID, "text": "RAILWAY TEST OK"}, timeout=15)
                body = f"Telegram status={r.status_code} body={r.text[:300]}".encode()
            except Exception as e:
                body = f"Telegram ERROR: {e}".encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
    def log_message(self, *args):
        pass

def start_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info(f"Health server en puerto {port}")

# ─── MAIN ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("[INICIO] Trading Monitor arrancando...", flush=True)
    log.info("Trading Monitor 24/7 iniciado")
    log.info(f"Símbolos: {SYMBOLS}")
    log.info(f"Intervalo chequeo: cada 15 minutos")

    if TWELVE_DATA_KEY == "TU_API_KEY_AQUI":
        log.error("Debes poner tu API key de Twelve Data en TWELVE_DATA_KEY")
        exit(1)

    # Test de Telegram ANTES de todo lo demás
    print(f"[TELEGRAM TEST] Enviando prueba a chat_id={TELEGRAM_CHATID}...", flush=True)
    send_telegram_text("RAILWAY ONLINE - Monitor iniciado correctamente")

    # Servidor HTTP para Railway (requerido)
    start_health_server()

    # Ejecutar inmediatamente al arrancar
    send_heartbeat()
    run_monitor()

    # Programar cada 15 minutos
    schedule.every(15).minutes.do(run_monitor)
    # Heartbeat cada hora
    schedule.every(1).hours.do(send_heartbeat)
    while True:
        schedule.run_pending()
        time.sleep(30)
