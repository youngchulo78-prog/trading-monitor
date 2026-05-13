"""
Trading Monitor 24/7 v2.0 — XAUUSD, BTCUSD, EURUSD, GBPUSD, EURJPY, US100, US30
- Señales originales mantenidas
- Reversión alcista/bajista con TPs en Fibonacci
- Pullback a EMA50
- Gráfico limpio con panel multi-timeframe 1H/4H/1D
"""

from __future__ import annotations
import requests
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
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
SYMBOLS = ["XAU/USD", "BTC/USD", "EUR/USD", "GBP/USD", "NASDAQ100", "EUR/JPY", "DJ30"]

OUTPUTSIZE = 220
# ─────────────────────────────────────────────────────────────────────────────

sent_signals: Dict[str, str] = {}


def fetch_ohlcv(symbol: str, interval: str = "1h") -> Optional[pd.DataFrame]:
    url = "https://api.twelvedata.com/time_series"
    params = {"symbol": symbol, "interval": interval,
              "outputsize": OUTPUTSIZE, "apikey": TWELVE_DATA_KEY, "order": "ASC"}
    try:
        r    = requests.get(url, params=params, timeout=15)
        data = r.json()
        if data.get("status") == "error":
            log.warning(f"{symbol} {interval}: {data.get('message')}")
            return None
        df = pd.DataFrame(data["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.set_index("datetime")
        for col in ["open", "high", "low", "close"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col])
        return df
    except Exception as e:
        log.error(f"{symbol} {interval}: {e}")
        return None


def calc_ema(s: pd.Series, p: int) -> pd.Series:
    return s.ewm(span=p, adjust=False).mean()


def calc_rsi(s: pd.Series, p: int = 14) -> pd.Series:
    d    = s.diff()
    gain = d.clip(lower=0).rolling(p).mean()
    loss = (-d.clip(upper=0)).rolling(p).mean()
    rs   = gain / loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def calc_fibonacci(df: pd.DataFrame) -> dict:
    sh  = df["high"].iloc[-100:].max()
    sl  = df["low"].iloc[-100:].min()
    rng = sh - sl
    return {"high": sh, "low": sl,
            "f236": sh - rng*0.236, "f382": sh - rng*0.382,
            "f500": sh - rng*0.500, "f618": sh - rng*0.618,
            "f786": sh - rng*0.786}


def get_timeframe_trends(symbol: str) -> dict:
    trends = {}
    for interval in ["5min", "15min", "1h", "4h", "1day"]:
        df = fetch_ohlcv(symbol, interval)
        if df is None or len(df) < 50:
            trends[interval] = "neutral"
            continue
        n    = min(200, len(df))
        e50  = calc_ema(df["close"], 50).iloc[-1]
        e200 = calc_ema(df["close"], n).iloc[-1]
        p    = df["close"].iloc[-1]
        if e50 > e200 and p > e50:
            trends[interval] = "bullish"
        elif e50 < e200 and p < e50:
            trends[interval] = "bearish"
        else:
            trends[interval] = "neutral"
        time.sleep(1)
    return trends


def check_signals(symbol: str, df: pd.DataFrame) -> List[dict]:
    signals = []
    close   = df["close"]
    high    = df["high"]
    low     = df["low"]
    open_   = df["open"]

    e50    = calc_ema(close, 50)
    e200   = calc_ema(close, 200)
    rsi    = calc_rsi(close)
    fib    = calc_fibonacci(df)

    p         = close.iloc[-1]
    ev50      = e50.iloc[-1]
    ev200     = e200.iloc[-1]
    e50_prev  = e50.iloc[-2]
    e200_prev = e200.iloc[-2]
    rsi_val   = rsi.iloc[-1]
    dist_pct  = (p - ev50) / ev50 * 100
    roc5      = (p - close.iloc[-6]) / close.iloc[-6] * 100 if len(close) >= 6 else 0
    avg_body  = abs(close - open_).rolling(14).mean().iloc[-1]
    body_curr = close.iloc[-1] - open_.iloc[-1]
    body_prev = close.iloc[-2] - open_.iloc[-2]
    golden    = ev50 > ev200
    death     = ev50 < ev200

    base = {"symbol": symbol, "price": p, "e50": ev50, "e200": ev200, "fib": fib, "rsi": rsi_val}

    # ── Originales ───────────────────────────────────────────────────────────
    if ev50 > ev200 and e50_prev <= e200_prev:
        signals.append({**base, "type": "BUY_GOLDEN_CROSS"})
    if ev50 < ev200 and e50_prev >= e200_prev:
        signals.append({**base, "type": "SELL_DEATH_CROSS"})
    if golden and fib["f618"] <= p <= fib["f382"]:
        signals.append({**base, "type": "BUY_FIB_ZONE"})
    if p < ev50 and dist_pct < -0.3 and roc5 < -0.5:
        signals.append({**base, "type": "SELL_BELOW_EMA50"})
    if "XAU" in symbol and p <= 4641 and golden:
        signals.append({**base, "type": "XAUUSD_SPECIAL_BUY"})

    h20 = high.iloc[-21:-1].max()
    l20 = low.iloc[-21:-1].min()
    bs  = abs(close.iloc[-1] - open_.iloc[-1])
    if p > h20 and bs > avg_body*1.5 and close.iloc[-1] > open_.iloc[-1]:
        signals.append({**base, "type": "BREAKOUT_BUY"})
    if p < l20 and bs > avg_body*1.5 and close.iloc[-1] < open_.iloc[-1]:
        signals.append({**base, "type": "BREAKOUT_SELL"})
    if dist_pct > 1.5 and roc5 > 0.8 and golden:
        signals.append({**base, "type": "STRONG_BULL_TREND"})
    if dist_pct < -1.5 and roc5 < -0.8:
        signals.append({**base, "type": "STRONG_BEAR_TREND"})

    # ── Nuevas señales ───────────────────────────────────────────────────────
    bear_engulf = body_curr < 0 and body_prev > 0 and abs(body_curr) > abs(body_prev)*0.8
    bull_engulf = body_curr > 0 and body_prev < 0 and abs(body_curr) > abs(body_prev)*0.8

    if dist_pct > 2.0 and rsi_val > 70 and bear_engulf:
        signals.append({**base, "type": "REVERSAL_SELL"})
    if dist_pct < -2.0 and rsi_val < 30 and bull_engulf:
        signals.append({**base, "type": "REVERSAL_BUY"})

    touched = low.iloc[-1] <= ev50 <= high.iloc[-1]
    if golden and touched and close.iloc[-1] > open_.iloc[-1]:
        signals.append({**base, "type": "PULLBACK_BUY"})
    if death and touched and close.iloc[-1] < open_.iloc[-1]:
        signals.append({**base, "type": "PULLBACK_SELL"})

    return signals



def calc_signal_strength(sig: dict, trends: dict = {}) -> tuple:
    """Calcula la fuerza de la señal de 1 a 5 estrellas."""
    t       = sig["type"]
    rsi     = sig["rsi"]
    price   = sig["price"]
    e50     = sig["e50"]
    dist    = abs((price - e50) / e50 * 100)
    points  = 0

    # RSI extremo
    if rsi > 75 or rsi < 25:
        points += 2
    elif rsi > 70 or rsi < 30:
        points += 1

    # Tipo de señal
    if t in ["REVERSAL_BUY", "REVERSAL_SELL"]:
        points += 2  # Señal más confiable
    elif t in ["BUY_GOLDEN_CROSS", "SELL_DEATH_CROSS"]:
        points += 2
    elif t in ["PULLBACK_BUY", "PULLBACK_SELL"]:
        points += 1
    elif t in ["BREAKOUT_BUY", "BREAKOUT_SELL"]:
        points += 1

    # Multi-timeframe alineado
    is_bull = "BUY" in t or "BULL" in t
    aligned = sum(1 for v in trends.values() if (v == "bullish" and is_bull) or (v == "bearish" and not is_bull))
    if aligned >= 4:
        points += 2
    elif aligned >= 3:
        points += 1

    # Sobreextensión
    if dist > 3.0:
        points += 1

    # Calcular estrellas (max 5)
    stars = min(5, max(1, round(points / 2)))
    star_str = "⭐" * stars

    if stars >= 5:
        label = "MUY FUERTE"
    elif stars >= 4:
        label = "FUERTE"
    elif stars >= 3:
        label = "MODERADA"
    elif stars >= 2:
        label = "DEBIL"
    else:
        label = "MUY DEBIL"

    return star_str, label, stars


def build_caption(sig: dict, trends: dict = {}) -> str:
    t   = sig["type"]
    sym = sig["symbol"].replace("/", "")
    p   = sig["price"]
    e50 = sig["e50"]
    e200= sig["e200"]
    fib = sig["fib"]
    rsi = sig["rsi"]
    atr = p * 0.005
    rng = fib["high"] - fib["low"]

    if t == "REVERSAL_SELL":
        tp1 = round(fib["high"] - rng*0.382, 2)
        tp2 = round(fib["high"] - rng*0.500, 2)
        tp3 = round(fib["high"] - rng*0.618, 2)
        sl  = round(p + atr*2, 2)
        stars, label, _ = calc_signal_strength(sig, trends)
        return (f"🔴 {sym} — REVERSIÓN BAJISTA\n"
                f"Fuerza: {stars} {label}\n"
                f"RSI: {rsi:.0f} | Engulfing bajista | Sobreextendido\n"
                f"Precio: {p:.2f} | EMA50: {e50:.2f}\n\n"
                f"Entrada: {p:.2f} | SL: {sl}\n"
                f"TP1: {tp1} (Fib 38.2%)\nTP2: {tp2} (Fib 50%)\nTP3: {tp3} (Fib 61.8%)")

    if t == "REVERSAL_BUY":
        tp1 = round(fib["low"] + rng*0.382, 2)
        tp2 = round(fib["low"] + rng*0.500, 2)
        tp3 = round(fib["low"] + rng*0.618, 2)
        sl  = round(p - atr*2, 2)
        stars, label, _ = calc_signal_strength(sig, trends)
        return (f"🟢 {sym} — REVERSIÓN ALCISTA\n"
                f"Fuerza: {stars} {label}\n"
                f"RSI: {rsi:.0f} | Engulfing alcista | Sobreextendido\n"
                f"Precio: {p:.2f} | EMA50: {e50:.2f}\n\n"
                f"Entrada: {p:.2f} | SL: {sl}\n"
                f"TP1: {tp1} (Fib 38.2%)\nTP2: {tp2} (Fib 50%)\nTP3: {tp3} (Fib 61.8%)")

    if t == "PULLBACK_BUY":
        sl = round(e50 - atr*1.5, 2)
        stars, label, _ = calc_signal_strength(sig, trends)
        return (f"🟢 {sym} — PULLBACK BUY\n"
                f"Fuerza: {stars} {label}\n"
                f"Rebote en EMA50 | RSI: {rsi:.0f}\n"
                f"Precio: {p:.2f} | EMA50: {e50:.2f}\n\n"
                f"Entrada: {p:.2f} | SL: {sl}\n"
                f"TP1: {round(p+atr*2,2)} | TP2: {round(fib['f236'],2)} | TP3: {round(fib['high'],2)}")

    if t == "PULLBACK_SELL":
        sl = round(e50 + atr*1.5, 2)
        stars, label, _ = calc_signal_strength(sig, trends)
        return (f"🔴 {sym} — PULLBACK SELL\n"
                f"Fuerza: {stars} {label}\n"
                f"Rechazo en EMA50 | RSI: {rsi:.0f}\n"
                f"Precio: {p:.2f} | EMA50: {e50:.2f}\n\n"
                f"Entrada: {p:.2f} | SL: {sl}\n"
                f"TP1: {round(p-atr*2,2)} | TP2: {round(fib['f786'],2)} | TP3: {round(fib['low'],2)}")

    if t == "BUY_GOLDEN_CROSS":
        sl = round(e50 - atr, 2)
        return (f"🟢 {sym} — BUY (Golden Cross)\nPrecio: {p:.2f} | RSI: {rsi:.0f}\n"
                f"EMA50: {e50:.2f} | EMA200: {e200:.2f}\n\n"
                f"Entrada: {p:.2f} | SL: {sl}\n"
                f"TP1: {round(p+atr*2,2)} | TP2: {round(p+atr*4,2)} | TP3: {round(p+atr*7,2)}")

    if t == "SELL_DEATH_CROSS":
        sl = round(e50 + atr, 2)
        return (f"🔴 {sym} — SELL (Death Cross)\nPrecio: {p:.2f} | RSI: {rsi:.0f}\n"
                f"EMA50: {e50:.2f} | EMA200: {e200:.2f}\n\n"
                f"Entrada: {p:.2f} | SL: {sl}\n"
                f"TP1: {round(fib['f382'],2)} | TP2: {round(fib['f500'],2)} | TP3: {round(fib['f618'],2)}")

    if t == "BUY_FIB_ZONE":
        sl = round(fib["f618"] - atr, 2)
        return (f"🟢 {sym} — BUY Zona Fibonacci\nPrecio: {p:.2f} | RSI: {rsi:.0f}\n\n"
                f"Entrada: {p:.2f} | SL: {sl}\n"
                f"TP1: {round(fib['f236'],2)} | TP2: {round(fib['high'],2)} | TP3: {round(fib['high']+atr*3,2)}")

    if t == "SELL_BELOW_EMA50":
        sl = round(e50 + atr, 2)
        return (f"🔴 {sym} — SELL bajo EMA50\nPrecio: {p:.2f} | RSI: {rsi:.0f}\n\n"
                f"Entrada: {p:.2f} | SL: {sl}\n"
                f"TP1: {round(p-atr*2,2)} | TP2: {round(p-atr*4,2)} | TP3: {round(p-atr*7,2)}")

    if t == "XAUUSD_SPECIAL_BUY":
        return (f"🟡 {sym} — ALERTA ESPECIAL BUY\nPrecio: {p:.2f} | RSI: {rsi:.0f}\n\n"
                f"Entrada: ~4,635 | SL: 4,615\nTP1: 4,700 | TP2: 4,760 | TP3: 4,850")

    if t == "BREAKOUT_BUY":
        sl = round(p - atr*1.5, 2)
        return (f"🚀 {sym} — BREAKOUT BUY\nRompe máximo 20 barras | RSI: {rsi:.0f}\n\n"
                f"Entrada: {p:.2f} | SL: {sl}\n"
                f"TP1: {round(p+atr*2,2)} | TP2: {round(p+atr*4,2)} | TP3: {round(p+atr*7,2)}")

    if t == "BREAKOUT_SELL":
        sl = round(p + atr*1.5, 2)
        return (f"🔴 {sym} — BREAKOUT SELL\nRompe mínimo 20 barras | RSI: {rsi:.0f}\n\n"
                f"Entrada: {p:.2f} | SL: {sl}\n"
                f"TP1: {round(p-atr*2,2)} | TP2: {round(p-atr*4,2)} | TP3: {round(p-atr*7,2)}")

    if t == "STRONG_BULL_TREND":
        sl = round(e50 - atr, 2)
        return (f"📈 {sym} — TENDENCIA ALCISTA FUERTE\nPrecio: {p:.2f} | RSI: {rsi:.0f}\n\n"
                f"Entrada: {p:.2f} | SL: {sl}\n"
                f"TP1: {round(p+atr*2,2)} | TP2: {round(p+atr*4,2)} | TP3: {round(p+atr*7,2)}")

    if t == "STRONG_BEAR_TREND":
        sl = round(e50 + atr, 2)
        return (f"📉 {sym} — TENDENCIA BAJISTA FUERTE\nPrecio: {p:.2f} | RSI: {rsi:.0f}\n\n"
                f"Entrada: {p:.2f} | SL: {sl}\n"
                f"TP1: {round(p-atr*2,2)} | TP2: {round(p-atr*4,2)} | TP3: {round(p-atr*7,2)}")

    return f"⚡ {sym} — {t}\nPrecio: {p:.2f}"


def generate_chart(symbol: str, df: pd.DataFrame, sig: dict, trends: dict) -> bytes:
    BG    = "#0d1117"
    BG2   = "#161b22"
    GREEN = "#26a69a"
    RED   = "#ef5350"
    BLUE  = "#2962ff"
    ORNG  = "#ff9800"
    PURP  = "#9c27b0"
    TEXT  = "#c9d1d9"
    GRID  = "#21262d"

    close  = df["close"].iloc[-60:]
    open_  = df["open"].iloc[-60:]
    high   = df["high"].iloc[-60:]
    low    = df["low"].iloc[-60:]
    ema50  = calc_ema(df["close"], 50).iloc[-60:]
    ema200 = calc_ema(df["close"], 200).iloc[-60:]
    rsi_s  = calc_rsi(df["close"]).iloc[-60:]
    fib    = sig["fib"]

    fig = plt.figure(figsize=(14, 9), facecolor=BG)
    gs  = gridspec.GridSpec(3, 2, figure=fig,
                            height_ratios=[5, 1.5, 1],
                            width_ratios=[4, 1],
                            hspace=0.06, wspace=0.04)

    ax_p = fig.add_subplot(gs[0, 0])
    ax_r = fig.add_subplot(gs[1, 0], sharex=ax_p)
    ax_s = fig.add_subplot(gs[2, 0], sharex=ax_p)
    ax_t = fig.add_subplot(gs[:, 1])

    for ax in [ax_p, ax_r, ax_s, ax_t]:
        ax.set_facecolor(BG2)
        ax.tick_params(colors=TEXT, labelsize=7)
        for sp in ax.spines.values():
            sp.set_edgecolor(GRID)

    # Velas
    idx = list(range(len(close)))
    for i, (c, o, h, l) in enumerate(zip(close, open_, high, low)):
        col = GREEN if c >= o else RED
        ax_p.plot([i, i], [l, h], color=col, linewidth=0.7, alpha=0.5)
        ax_p.fill_between([i-0.35, i+0.35], [min(c,o)]*2, [max(c,o)]*2, color=col, alpha=0.85)

    ax_p.plot(idx, ema50.values,  color=ORNG, linewidth=1.5, label="EMA50",  zorder=3)
    ax_p.plot(idx, ema200.values, color=PURP, linewidth=1.5, label="EMA200", zorder=3)
    ax_p.axhline(fib["f382"], color=GREEN, linestyle="--", linewidth=0.7, alpha=0.5)
    ax_p.axhline(fib["f618"], color=RED,   linestyle="--", linewidth=0.7, alpha=0.5)
    ax_p.axhspan(fib["f618"], fib["f382"], alpha=0.04, color=GREEN)

    lp = close.iloc[-1]
    ax_p.axhline(lp, color=TEXT, linewidth=0.4, alpha=0.4)
    ax_p.text(len(idx)-0.5, lp, f" {lp:.2f}", color=TEXT, fontsize=7, va="center")

    is_bull = "BUY" in sig["type"] or "BULL" in sig["type"]
    tc = GREEN if is_bull else RED
    ax_p.set_title(f"{symbol.replace('/', '')} 1H — {sig['type'].replace('_',' ')}",
                   color=tc, fontsize=10, fontweight="bold", pad=5, loc="left")
    ax_p.legend(loc="upper left", facecolor=BG, edgecolor=GRID, labelcolor=TEXT, fontsize=7)
    ax_p.grid(color=GRID, linewidth=0.3, alpha=0.7)
    ax_p.set_ylabel("Precio", color=TEXT, fontsize=8)
    plt.setp(ax_p.get_xticklabels(), visible=False)

    # RSI
    ax_r.plot(idx, rsi_s.values, color=BLUE, linewidth=1.1)
    ax_r.axhline(70, color=RED,   linewidth=0.5, linestyle="--", alpha=0.7)
    ax_r.axhline(30, color=GREEN, linewidth=0.5, linestyle="--", alpha=0.7)
    ax_r.axhspan(70, 100, alpha=0.05, color=RED)
    ax_r.axhspan(0,  30,  alpha=0.05, color=GREEN)
    ax_r.set_ylim(0, 100)
    ax_r.set_ylabel("RSI", color=TEXT, fontsize=8)
    ax_r.grid(color=GRID, linewidth=0.3, alpha=0.7)
    rv = rsi_s.iloc[-1]
    ax_r.text(len(idx)-0.5, rv, f" {rv:.0f}", color=BLUE, fontsize=7, va="center")
    plt.setp(ax_r.get_xticklabels(), visible=False)

    # Señal
    ax_s.axis("off")
    ax_s.set_facecolor(BG2)
    stxt  = "▲ BUY" if is_bull else "▼ SELL"
    scol  = GREEN if is_bull else RED
    dist  = (lp - ema50.iloc[-1]) / ema50.iloc[-1] * 100
    ax_s.text(0.02, 0.5, f"RSI: {rv:.0f}", color=BLUE, fontsize=9, va="center", transform=ax_s.transAxes)
    ax_s.text(0.25, 0.5, stxt, color=scol, fontsize=13, fontweight="bold", va="center", transform=ax_s.transAxes)
    ax_s.text(0.55, 0.5, f"Dist EMA50: {dist:+.2f}%", color=TEXT, fontsize=8, va="center", transform=ax_s.transAxes)

    # Panel multi-timeframe
    ax_t.axis("off")
    tf_map = {"5min": "5M", "15min": "15M", "1h": "1H", "4h": "4H", "1day": "1D"}
    tc_map = {"bullish": GREEN, "bearish": RED, "neutral": "#888888"}
    tt_map = {"bullish": "● BULLISH", "bearish": "● BEARISH", "neutral": "● NEUTRAL"}

    ax_t.text(0.5, 0.97, "TIMEFRAME", color=TEXT, fontsize=9, fontweight="bold",
              ha="center", va="top", transform=ax_t.transAxes)
    ax_t.text(0.5, 0.91, "TREND", color="#888888", fontsize=8,
              ha="center", va="top", transform=ax_t.transAxes)

    for (iv, lbl), yp in zip(tf_map.items(), [0.83, 0.73, 0.63, 0.53, 0.43]):
        trend = trends.get(iv, "neutral")
        ax_t.text(0.1, yp, lbl, color=TEXT, fontsize=10, fontweight="bold",
                  va="center", transform=ax_t.transAxes)
        ax_t.text(0.42, yp, tt_map[trend], color=tc_map[trend], fontsize=8,
                  va="center", transform=ax_t.transAxes)

    bull_n = sum(1 for v in trends.values() if v == "bullish")
    bear_n = sum(1 for v in trends.values() if v == "bearish")

    ax_t.text(0.5, 0.32, "─────────", color=GRID, ha="center", transform=ax_t.transAxes)
    ax_t.text(0.5, 0.25, f"Bull: {bull_n}/5", color=GREEN, fontsize=10,
              ha="center", va="top", transform=ax_t.transAxes)
    ax_t.text(0.5, 0.17, f"Bear: {bear_n}/5", color=RED, fontsize=10,
              ha="center", va="top", transform=ax_t.transAxes)

    dom = "ALCISTA" if bull_n > bear_n else ("BAJISTA" if bear_n > bull_n else "NEUTRAL")
    dc  = GREEN if bull_n > bear_n else (RED if bear_n > bull_n else "#888888")
    ax_t.text(0.5, 0.07, dom, color=dc, fontsize=12, fontweight="bold",
              ha="center", va="bottom", transform=ax_t.transAxes)

    now = datetime.now(timezone.utc).strftime("%d/%m %H:%M UTC")
    fig.text(0.01, 0.01, now, color="#444444", fontsize=7)

    buf = io.BytesIO()
    plt.tight_layout(pad=0.5)
    plt.savefig(buf, format="png", dpi=130, bbox_inches="tight", facecolor=BG, edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def send_telegram_photo(caption: str, img_bytes: bytes, symbol: str):
    url   = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    files = {"photo": (f"{symbol.replace('/', '')}.png", img_bytes, "image/png")}
    data  = {"chat_id": TELEGRAM_CHATID, "caption": caption}
    try:
        r = requests.post(url, data=data, files=files, timeout=20)
        if r.json().get("ok"):
            log.info(f"Telegram OK: {caption[:40]}")
        else:
            log.warning(f"Telegram error: {r.text[:100]}")
    except Exception as e:
        log.error(f"send_photo error: {e}")


def send_telegram_text(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, data={"chat_id": TELEGRAM_CHATID, "text": text}, timeout=15)
        log.info(f"Telegram text status={r.status_code}")
    except Exception as e:
        log.error(f"send_text error: {e}")


def send_heartbeat():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    send_telegram_text(
        f"💓 MONITOR ACTIVO — {now}\n"
        f"Monitoreando: XAUUSD, BTCUSD, EURUSD, GBPUSD\n"
        f"Señales: Cross, Reversión, Pullback, Breakout\n"
        f"Railway 24/7 ✅"
    )


def run_monitor():
    log.info("=== Ciclo iniciado ===")
    for symbol in SYMBOLS:
        df = fetch_ohlcv(symbol, "1h")
        if df is None or len(df) < 210:
            log.warning(f"{symbol}: datos insuficientes")
            continue

        signals = check_signals(symbol, df)

        trends = {}
        if signals:
            log.info(f"{symbol}: obteniendo multi-timeframe...")
            trends = get_timeframe_trends(symbol)

        for sig in signals:
            key = f"{symbol}_{sig['type']}"
            bt  = df.index[-1].strftime("%Y%m%d%H")
            if sent_signals.get(key) == bt:
                continue
            sent_signals[key] = bt
            caption   = build_caption(sig, trends)
            img_bytes = generate_chart(symbol, df, sig, trends)
            send_telegram_photo(caption, img_bytes, symbol)
            log.info(f"SEÑAL: {symbol} {sig['type']} @ {sig['price']:.2f}")
            time.sleep(2)

        if not signals:
            log.info(f"{symbol}: sin señales")
        time.sleep(3)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/test":
            try:
                r    = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                                     data={"chat_id": TELEGRAM_CHATID, "text": "✅ Monitor v2.0 OK"}, timeout=15)
                body = f"status={r.status_code}".encode()
            except Exception as e:
                body = str(e).encode()
            self.send_response(200); self.end_headers(); self.wfile.write(body)
        else:
            self.send_response(200); self.end_headers(); self.wfile.write(b"Monitor v2.0 OK")

    def log_message(self, *args): pass


def start_health_server():
    port   = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info(f"Health server puerto {port}")


if __name__ == "__main__":
    print("[INICIO] Trading Monitor v2.0", flush=True)
    send_telegram_text(
        "🚀 TRADING MONITOR v2.0 ONLINE\n\n"
        "✅ Golden/Death Cross\n"
        "✅ Zona Fibonacci\n"
        "✅ Breakout 20 barras\n"
        "✅ Tendencia fuerte\n"
        "🆕 Reversión alcista/bajista (RSI + Engulfing)\n"
        "🆕 TPs en niveles Fibonacci\n"
        "🆕 Pullback a EMA50\n"
        "🆕 Panel multi-timeframe 1H/4H/1D\n"
        "🆕 Gráfico con velas limpias\n\n"
        "Railway 24/7 ✅"
    )
    start_health_server()
    send_heartbeat()
    run_monitor()
    schedule.every(15).minutes.do(run_monitor)
    schedule.every(1).hours.do(send_heartbeat)
    while True:
        schedule.run_pending()
        time.sleep(30)
