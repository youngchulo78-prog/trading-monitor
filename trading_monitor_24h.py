"""
Trading Monitor 24/7 v3.0
Pares: XAUUSD, BTCUSD, EURUSD, GBPUSD, US100, EURJPY, US30
Velas: 30 minutos | Revision: cada 15 minutos
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
SYMBOLS  = ["XAU/USD", "BTC/USD", "EUR/USD", "GBP/USD", "NDX", "EUR/JPY", "DJI"]
INTERVAL = "30min"
OUTPUTSIZE = 220
# ─────────────────────────────────────────────────────────────────────────────

sent_signals: Dict[str, str] = {}


def fetch_ohlcv(symbol: str, interval: str = "30min") -> Optional[pd.DataFrame]:
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
        for col in ["open", "high", "low", "close", "volume"]:
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


def calc_volume_profile(df: pd.DataFrame, bins: int = 20) -> dict:
    """Calcula POC, VAH, VAL usando volume profile."""
    try:
        price_min = df["low"].min()
        price_max = df["high"].max()
        price_range = price_max - price_min
        bin_size = price_range / bins

        vol_by_level = {}
        for i in range(bins):
            level = price_min + i * bin_size
            mask = (df["low"] <= level + bin_size) & (df["high"] >= level)
            if "volume" in df.columns:
                vol = df.loc[mask, "volume"].sum()
            else:
                vol = mask.sum()
            vol_by_level[level + bin_size/2] = vol

        poc = max(vol_by_level, key=vol_by_level.get)
        total_vol = sum(vol_by_level.values())
        target_vol = total_vol * 0.70

        sorted_levels = sorted(vol_by_level.items(), key=lambda x: x[1], reverse=True)
        va_levels = []
        cumvol = 0
        for level, vol in sorted_levels:
            va_levels.append(level)
            cumvol += vol
            if cumvol >= target_vol:
                break

        vah = max(va_levels) if va_levels else price_max
        val = min(va_levels) if va_levels else price_min

        return {"poc": poc, "vah": vah, "val": val,
                "price_max": price_max, "price_min": price_min}
    except Exception as e:
        log.error(f"Volume profile error: {e}")
        return {"poc": 0, "vah": 0, "val": 0, "price_max": 0, "price_min": 0}


def calc_support_resistance(df: pd.DataFrame) -> dict:
    """Detecta zonas de soporte y resistencia automáticamente."""
    highs = df["high"].values
    lows  = df["low"].values
    n     = len(highs)

    resistance_levels = []
    support_levels    = []

    for i in range(2, n-2):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and \
           highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            resistance_levels.append(highs[i])
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and \
           lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            support_levels.append(lows[i])

    resistance_levels = sorted(set(resistance_levels), reverse=True)[:3]
    support_levels    = sorted(set(support_levels))[:3]

    return {
        "resistances": resistance_levels,
        "supports":    support_levels
    }


def calc_liquidity_levels(df: pd.DataFrame) -> dict:
    """Detecta niveles de liquidez (donde probablemente hay stops)."""
    recent_high = df["high"].iloc[-20:].max()
    recent_low  = df["low"].iloc[-20:].min()
    prev_high   = df["high"].iloc[-40:-20].max()
    prev_low    = df["low"].iloc[-40:-20].min()

    return {
        "recent_high": recent_high,
        "recent_low":  recent_low,
        "prev_high":   prev_high,
        "prev_low":    prev_low
    }


def get_timeframe_trends(symbol: str) -> dict:
    """Obtiene tendencia en 5M, 15M, 30M, 1H, 4H, 1D."""
    trends = {}
    intervals = [("5min", "5M"), ("15min", "15M"), ("30min", "30M"),
                 ("1h", "1H"), ("4h", "4H"), ("1day", "1D")]
    for interval, label in intervals:
        df = fetch_ohlcv(symbol, interval)
        if df is None or len(df) < 50:
            trends[label] = "neutral"
            continue
        n    = min(200, len(df))
        e50  = calc_ema(df["close"], 50).iloc[-1]
        e200 = calc_ema(df["close"], n).iloc[-1]
        p    = df["close"].iloc[-1]
        if e50 > e200 and p > e50:
            trends[label] = "bullish"
        elif e50 < e200 and p < e50:
            trends[label] = "bearish"
        else:
            trends[label] = "neutral"
        time.sleep(1)
    return trends


def check_signals(symbol: str, df: pd.DataFrame) -> List[dict]:
    signals = []
    close   = df["close"]
    high    = df["high"]
    low     = df["low"]
    open_   = df["open"]
    volume  = df["volume"] if "volume" in df.columns else pd.Series([1]*len(df))

    e50    = calc_ema(close, 50)
    e200   = calc_ema(close, 200)
    rsi    = calc_rsi(close)
    fib    = calc_fibonacci(df)
    vp     = calc_volume_profile(df)
    sr     = calc_support_resistance(df)
    liq    = calc_liquidity_levels(df)

    p         = close.iloc[-1]
    ev50      = e50.iloc[-1]
    ev200     = e200.iloc[-1]
    e50_prev  = e50.iloc[-2]
    e200_prev = e200.iloc[-2]
    rsi_val   = rsi.iloc[-1]
    dist_pct  = (p - ev50) / ev50 * 100
    roc5      = (p - close.iloc[-6]) / close.iloc[-6] * 100 if len(close) >= 6 else 0
    avg_body  = abs(close - open_).rolling(14).mean().iloc[-1]
    avg_vol   = volume.rolling(14).mean().iloc[-1]
    curr_vol  = volume.iloc[-1]
    vol_high  = curr_vol > avg_vol * 1.5
    body_curr = close.iloc[-1] - open_.iloc[-1]
    body_prev = close.iloc[-2] - open_.iloc[-2]
    golden    = ev50 > ev200
    death     = ev50 < ev200

    bear_engulf = body_curr < 0 and body_prev > 0 and abs(body_curr) > abs(body_prev)*0.8
    bull_engulf = body_curr > 0 and body_prev < 0 and abs(body_curr) > abs(body_prev)*0.8

    base = {"symbol": symbol, "price": p, "e50": ev50, "e200": ev200,
            "fib": fib, "rsi": rsi_val, "vp": vp, "sr": sr, "liq": liq,
            "vol_high": vol_high}

    # ── SEÑALES ORIGINALES ───────────────────────────────────────────────────
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

    # ── REVERSIÓN ────────────────────────────────────────────────────────────
    if dist_pct > 2.0 and rsi_val > 70 and bear_engulf:
        signals.append({**base, "type": "REVERSAL_SELL"})
    if dist_pct < -2.0 and rsi_val < 30 and bull_engulf:
        signals.append({**base, "type": "REVERSAL_BUY"})

    # ── PULLBACK ─────────────────────────────────────────────────────────────
    touched = low.iloc[-1] <= ev50 <= high.iloc[-1]
    if golden and touched and close.iloc[-1] > open_.iloc[-1]:
        signals.append({**base, "type": "PULLBACK_BUY"})
    if death and touched and close.iloc[-1] < open_.iloc[-1]:
        signals.append({**base, "type": "PULLBACK_SELL"})

    # ── ROMPIMIENTO DE ESTRUCTURA (con confirmación) ─────────────────────────
    struct_high = high.iloc[-10:-1].max()
    struct_low  = low.iloc[-10:-1].min()
    confirm_bull = close.iloc[-1] > open_.iloc[-1] and bs > avg_body * 1.2
    confirm_bear = close.iloc[-1] < open_.iloc[-1] and bs > avg_body * 1.2

    if p > struct_high and confirm_bull and vol_high:
        signals.append({**base, "type": "STRUCTURE_BREAK_BUY"})
    if p < struct_low and confirm_bear and vol_high:
        signals.append({**base, "type": "STRUCTURE_BREAK_SELL"})

    return signals


def build_caption(sig: dict, trends: dict = {}) -> str:
    t   = sig["type"]
    sym = sig["symbol"].replace("/", "")
    p   = sig["price"]
    e50 = sig["e50"]
    e200= sig["e200"]
    fib = sig["fib"]
    rsi = sig["rsi"]
    vp  = sig["vp"]
    sr  = sig["sr"]
    liq = sig["liq"]
    vol = sig["vol_high"]
    atr = p * 0.005
    rng = fib["high"] - fib["low"]

    # Fuerza de señal
    bull_count = sum(1 for v in trends.values() if v == "bullish")
    bear_count = sum(1 for v in trends.values() if v == "bearish")
    is_bull = "BUY" in t or "BULL" in t
    aligned = bull_count if is_bull else bear_count
    pts = 2
    pts += 2 if rsi > 75 or rsi < 25 else 1 if rsi > 70 or rsi < 30 else 0
    pts += 2 if aligned >= 5 else 1 if aligned >= 3 else 0
    pts += 1 if vol else 0
    stars = min(5, max(1, round(pts / 2.0)))
    star_str = "⭐" * stars
    strength = ["MUY DEBIL","DEBIL","MODERADA","FUERTE","MUY FUERTE"][stars-1]

    # Niveles Volume Profile
    poc_txt = f"\n📊 POC: {vp['poc']:.4f}" if vp['poc'] > 0 else ""
    vah_txt = f" | VAH: {vp['vah']:.4f}" if vp['vah'] > 0 else ""
    val_txt = f" | VAL: {vp['val']:.4f}" if vp['val'] > 0 else ""

    # Volumen
    vol_txt = "🔥 Volumen ALTO" if vol else "📉 Volumen bajo"

    if t == "REVERSAL_SELL":
        tp1 = round(fib["high"] - rng*0.382, 4)
        tp2 = round(fib["high"] - rng*0.500, 4)
        tp3 = round(fib["high"] - rng*0.618, 4)
        sl  = round(p + atr*2, 4)
        return (f"🔴 {sym} — ⚠️ REVERSIÓN BAJISTA\n"
                f"Precio AGOTADO — posible giro a la baja\n"
                f"Fuerza: {star_str} {strength}\n"
                f"RSI: {rsi:.0f} (sobrecompra) | Engulfing bajista\n"
                f"Precio: {p:.4f} alejado +{abs((p-e50)/e50*100):.1f}% de EMA50\n"
                f"{vol_txt}{poc_txt}{vah_txt}{val_txt}\n\n"
                f"Entrada: {p:.4f} | SL: {sl}\n"
                f"TP1: {tp1} (Fib 38.2%)\n"
                f"TP2: {tp2} (Fib 50%)\n"
                f"TP3: {tp3} (Fib 61.8%)")

    if t == "REVERSAL_BUY":
        tp1 = round(fib["low"] + rng*0.382, 4)
        tp2 = round(fib["low"] + rng*0.500, 4)
        tp3 = round(fib["low"] + rng*0.618, 4)
        sl  = round(p - atr*2, 4)
        return (f"🟢 {sym} — ⚠️ REVERSIÓN ALCISTA\n"
                f"Precio AGOTADO — posible giro al alza\n"
                f"Fuerza: {star_str} {strength}\n"
                f"RSI: {rsi:.0f} (sobreventa) | Engulfing alcista\n"
                f"Precio: {p:.4f} alejado -{abs((p-e50)/e50*100):.1f}% de EMA50\n"
                f"{vol_txt}{poc_txt}{vah_txt}{val_txt}\n\n"
                f"Entrada: {p:.4f} | SL: {sl}\n"
                f"TP1: {tp1} (Fib 38.2%)\n"
                f"TP2: {tp2} (Fib 50%)\n"
                f"TP3: {tp3} (Fib 61.8%)")

    if t == "STRUCTURE_BREAK_BUY":
        sl  = round(p - atr*2, 4)
        tp1 = round(p + atr*2, 4)
        tp2 = round(p + atr*4, 4)
        tp3 = round(p + atr*6, 4)
        return (f"🚀 {sym} — ROMPIMIENTO DE ESTRUCTURA BUY\n"
                f"Fuerza: {star_str} {strength}\n"
                f"Rompe estructura + vela confirmación + {vol_txt}\n"
                f"RSI: {rsi:.0f} | Precio: {p:.4f}\n"
                f"{poc_txt}{vah_txt}{val_txt}\n\n"
                f"Entrada: {p:.4f} | SL: {sl}\n"
                f"TP1: {tp1} | TP2: {tp2} | TP3: {tp3}")

    if t == "STRUCTURE_BREAK_SELL":
        sl  = round(p + atr*2, 4)
        tp1 = round(p - atr*2, 4)
        tp2 = round(p - atr*4, 4)
        tp3 = round(p - atr*6, 4)
        return (f"🔴 {sym} — ROMPIMIENTO DE ESTRUCTURA SELL\n"
                f"Fuerza: {star_str} {strength}\n"
                f"Rompe estructura + vela confirmación + {vol_txt}\n"
                f"RSI: {rsi:.0f} | Precio: {p:.4f}\n"
                f"{poc_txt}{vah_txt}{val_txt}\n\n"
                f"Entrada: {p:.4f} | SL: {sl}\n"
                f"TP1: {tp1} | TP2: {tp2} | TP3: {tp3}")

    if t == "PULLBACK_BUY":
        sl  = round(e50 - atr*1.5, 4)
        tp1 = round(p + atr*2, 4)
        tp2 = round(fib["f236"], 4)
        tp3 = round(fib["high"], 4)
        return (f"🟢 {sym} — PULLBACK BUY\n"
                f"Fuerza: {star_str} {strength}\n"
                f"Rebote en EMA50 | RSI: {rsi:.0f}\n"
                f"{vol_txt}{poc_txt}{vah_txt}{val_txt}\n\n"
                f"Entrada: {p:.4f} | SL: {sl}\n"
                f"TP1: {tp1} | TP2: {tp2} | TP3: {tp3}")

    if t == "PULLBACK_SELL":
        sl  = round(e50 + atr*1.5, 4)
        tp1 = round(p - atr*2, 4)
        tp2 = round(fib["f786"], 4)
        tp3 = round(fib["low"], 4)
        return (f"🔴 {sym} — PULLBACK SELL\n"
                f"Fuerza: {star_str} {strength}\n"
                f"Rechazo en EMA50 | RSI: {rsi:.0f}\n"
                f"{vol_txt}{poc_txt}{vah_txt}{val_txt}\n\n"
                f"Entrada: {p:.4f} | SL: {sl}\n"
                f"TP1: {tp1} | TP2: {tp2} | TP3: {tp3}")

    if t == "BUY_GOLDEN_CROSS":
        sl = round(e50 - atr, 4)
        return (f"🟢 {sym} — BUY (Golden Cross)\n"
                f"Fuerza: {star_str} {strength}\n"
                f"EMA50 cruza EMA200 al alza | RSI: {rsi:.0f}\n"
                f"{vol_txt}{poc_txt}{vah_txt}{val_txt}\n\n"
                f"Entrada: {p:.4f} | SL: {sl}\n"
                f"TP1: {round(p+atr*2,4)} | TP2: {round(p+atr*4,4)} | TP3: {round(p+atr*7,4)}")

    if t == "SELL_DEATH_CROSS":
        sl = round(e50 + atr, 4)
        return (f"🔴 {sym} — SELL (Death Cross)\n"
                f"Fuerza: {star_str} {strength}\n"
                f"EMA50 cruza EMA200 a la baja | RSI: {rsi:.0f}\n"
                f"{vol_txt}{poc_txt}{vah_txt}{val_txt}\n\n"
                f"Entrada: {p:.4f} | SL: {sl}\n"
                f"TP1: {round(fib['f382'],4)} | TP2: {round(fib['f500'],4)} | TP3: {round(fib['f618'],4)}")

    if t == "BUY_FIB_ZONE":
        sl = round(fib["f618"] - atr, 4)
        return (f"🟢 {sym} — BUY Zona Fibonacci\n"
                f"Fuerza: {star_str} {strength}\n"
                f"Precio en zona 38.2-61.8% | RSI: {rsi:.0f}\n"
                f"{vol_txt}{poc_txt}{vah_txt}{val_txt}\n\n"
                f"Entrada: {p:.4f} | SL: {sl}\n"
                f"TP1: {round(fib['f236'],4)} | TP2: {round(fib['high'],4)} | TP3: {round(fib['high']+atr*3,4)}")

    if t == "SELL_BELOW_EMA50":
        sl = round(e50 + atr, 4)
        return (f"🔴 {sym} — SELL bajo EMA50\n"
                f"Fuerza: {star_str} {strength}\n"
                f"Precio bajo EMA50 | RSI: {rsi:.0f}\n"
                f"{vol_txt}{poc_txt}{vah_txt}{val_txt}\n\n"
                f"Entrada: {p:.4f} | SL: {sl}\n"
                f"TP1: {round(p-atr*2,4)} | TP2: {round(p-atr*4,4)} | TP3: {round(p-atr*7,4)}")

    if t == "XAUUSD_SPECIAL_BUY":
        return (f"🟡 XAUUSD — ALERTA ESPECIAL BUY\n"
                f"Fuerza: {star_str} {strength}\n"
                f"Precio en zona especial | RSI: {rsi:.0f}\n"
                f"{vol_txt}{poc_txt}{vah_txt}{val_txt}\n\n"
                f"Entrada: ~4,635 | SL: 4,615\n"
                f"TP1: 4,700 | TP2: 4,760 | TP3: 4,850")

    if t == "BREAKOUT_BUY":
        sl = round(p - atr*1.5, 4)
        return (f"🚀 {sym} — BREAKOUT BUY\n"
                f"Fuerza: {star_str} {strength}\n"
                f"Rompe máximo 20 barras | RSI: {rsi:.0f}\n"
                f"{vol_txt}{poc_txt}{vah_txt}{val_txt}\n\n"
                f"Entrada: {p:.4f} | SL: {sl}\n"
                f"TP1: {round(p+atr*2,4)} | TP2: {round(p+atr*4,4)} | TP3: {round(p+atr*7,4)}")

    if t == "BREAKOUT_SELL":
        sl = round(p + atr*1.5, 4)
        return (f"🔴 {sym} — BREAKOUT SELL\n"
                f"Fuerza: {star_str} {strength}\n"
                f"Rompe mínimo 20 barras | RSI: {rsi:.0f}\n"
                f"{vol_txt}{poc_txt}{vah_txt}{val_txt}\n\n"
                f"Entrada: {p:.4f} | SL: {sl}\n"
                f"TP1: {round(p-atr*2,4)} | TP2: {round(p-atr*4,4)} | TP3: {round(p-atr*7,4)}")

    if t == "STRONG_BULL_TREND":
        sl = round(e50 - atr, 4)
        return (f"📈 {sym} — TENDENCIA ALCISTA FUERTE\n"
                f"Fuerza: {star_str} {strength}\n"
                f"+{dist_pct:.1f}% sobre EMA50 | RSI: {rsi:.0f}\n"
                f"{vol_txt}{poc_txt}{vah_txt}{val_txt}\n\n"
                f"Entrada: {p:.4f} | SL: {sl}\n"
                f"TP1: {round(p+atr*2,4)} | TP2: {round(p+atr*4,4)} | TP3: {round(p+atr*7,4)}")

    if t == "STRONG_BEAR_TREND":
        sl = round(e50 + atr, 4)
        return (f"📉 {sym} — TENDENCIA BAJISTA FUERTE\n"
                f"Fuerza: {star_str} {strength}\n"
                f"{dist_pct:.1f}% bajo EMA50 | RSI: {rsi:.0f}\n"
                f"{vol_txt}{poc_txt}{vah_txt}{val_txt}\n\n"
                f"Entrada: {p:.4f} | SL: {sl}\n"
                f"TP1: {round(p-atr*2,4)} | TP2: {round(p-atr*4,4)} | TP3: {round(p-atr*7,4)}")

    return f"⚡ {sym} — {t}\nPrecio: {p:.4f}"


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

    n      = min(60, len(df))
    close  = df["close"].iloc[-n:]
    open_  = df["open"].iloc[-n:]
    high   = df["high"].iloc[-n:]
    low    = df["low"].iloc[-n:]
    volume = df["volume"].iloc[-n:] if "volume" in df.columns else pd.Series([0]*n)
    ema50  = calc_ema(df["close"], 50).iloc[-n:]
    ema200 = calc_ema(df["close"], 200).iloc[-n:]
    rsi_s  = calc_rsi(df["close"]).iloc[-n:]
    fib    = sig["fib"]
    vp     = sig["vp"]

    fig = plt.figure(figsize=(14, 10), facecolor=BG)
    gs  = gridspec.GridSpec(4, 2, figure=fig,
                            height_ratios=[5, 1.5, 1, 1],
                            width_ratios=[4, 1],
                            hspace=0.06, wspace=0.04)

    ax_p = fig.add_subplot(gs[0, 0])
    ax_r = fig.add_subplot(gs[1, 0], sharex=ax_p)
    ax_v = fig.add_subplot(gs[2, 0], sharex=ax_p)
    ax_s = fig.add_subplot(gs[3, 0], sharex=ax_p)
    ax_t = fig.add_subplot(gs[:, 1])

    for ax in [ax_p, ax_r, ax_v, ax_s, ax_t]:
        ax.set_facecolor(BG2)
        ax.tick_params(colors=TEXT, labelsize=7)
        for sp in ax.spines.values():
            sp.set_edgecolor(GRID)

    idx = list(range(n))
    for i in range(n):
        c, o, h, l = close.iloc[i], open_.iloc[i], high.iloc[i], low.iloc[i]
        col = GREEN if c >= o else RED
        ax_p.plot([i, i], [l, h], color=col, linewidth=0.7, alpha=0.5)
        ax_p.fill_between([i-0.35, i+0.35], [min(c,o)]*2, [max(c,o)]*2, color=col, alpha=0.85)

    ax_p.plot(idx, ema50.values,  color=ORNG, linewidth=1.5, label="EMA50")
    ax_p.plot(idx, ema200.values, color=PURP, linewidth=1.5, label="EMA200")

    # Fibonacci
    ax_p.axhline(fib["f382"], color=GREEN, linestyle="--", linewidth=0.7, alpha=0.5)
    ax_p.axhline(fib["f618"], color=RED,   linestyle="--", linewidth=0.7, alpha=0.5)
    ax_p.axhspan(fib["f618"], fib["f382"], alpha=0.04, color=GREEN)

    # Volume Profile
    if vp["poc"] > 0:
        ax_p.axhline(vp["poc"], color="#ffeb3b", linestyle="-",  linewidth=1.0, alpha=0.8, label="POC")
        ax_p.axhline(vp["vah"], color="#ef5350", linestyle=":",  linewidth=0.8, alpha=0.6, label="VAH")
        ax_p.axhline(vp["val"], color="#26a69a", linestyle=":",  linewidth=0.8, alpha=0.6, label="VAL")

    # Señal en vela
    is_bull = "BUY" in sig["type"] or "BULL" in sig["type"]
    sig_col = GREEN if is_bull else RED
    atr_est = (high - low).mean()
    signal_idx = n - 1
    arrow_y = low.iloc[-1] - atr_est*0.5 if is_bull else high.iloc[-1] + atr_est*0.5
    ax_p.annotate("", xy=(signal_idx, low.iloc[-1] if is_bull else high.iloc[-1]),
                  xytext=(signal_idx, arrow_y),
                  arrowprops=dict(arrowstyle="-|>", color=sig_col, lw=2.5, mutation_scale=20))

    label_txt = "BUY" if is_bull else "SELL"
    ax_p.text(signal_idx, arrow_y - atr_est*0.3 if is_bull else arrow_y + atr_est*0.3,
              f"{label_txt}\n{close.iloc[-1]:.4f}",
              color="white", fontsize=8, fontweight="bold", ha="center",
              bbox=dict(boxstyle="round,pad=0.3", facecolor=sig_col, alpha=0.95), zorder=10)

    # SL y TPs
    atr = close.iloc[-1] * 0.005
    sl  = close.iloc[-1] - atr*2 if is_bull else close.iloc[-1] + atr*2
    tp1 = close.iloc[-1] + atr*2 if is_bull else close.iloc[-1] - atr*2
    tp2 = close.iloc[-1] + atr*4 if is_bull else close.iloc[-1] - atr*4
    tp3 = close.iloc[-1] + atr*6 if is_bull else close.iloc[-1] - atr*6

    ax_p.axhline(sl,  color=RED,   linewidth=0.8, linestyle="--", alpha=0.7)
    ax_p.axhline(tp1, color=GREEN, linewidth=0.8, linestyle="--", alpha=0.7)
    ax_p.axhline(tp2, color=GREEN, linewidth=0.7, linestyle="--", alpha=0.6)
    ax_p.axhline(tp3, color=GREEN, linewidth=0.6, linestyle="--", alpha=0.5)
    ax_p.text(1, sl,  f"SL",  color=RED,   fontsize=6, va="center")
    ax_p.text(1, tp1, f"TP1", color=GREEN, fontsize=6, va="center")
    ax_p.text(1, tp2, f"TP2", color=GREEN, fontsize=6, va="center")
    ax_p.text(1, tp3, f"TP3", color=GREEN, fontsize=6, va="center")

    ax_p.axhline(close.iloc[-1], color=TEXT, linewidth=0.4, alpha=0.3)
    ax_p.set_title(f"{symbol.replace('/', '')} 30M — {sig['type'].replace('_',' ')}",
                   color=sig_col, fontsize=10, fontweight="bold", pad=5, loc="left")
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
    ax_r.axvline(signal_idx, color=sig_col, linewidth=0.8, linestyle="--", alpha=0.5)
    ax_r.set_ylim(0, 100)
    ax_r.set_ylabel("RSI", color=TEXT, fontsize=8)
    ax_r.grid(color=GRID, linewidth=0.3, alpha=0.7)
    rv = rsi_s.iloc[-1]
    ax_r.text(n-0.5, rv, f" {rv:.0f}", color=BLUE, fontsize=7, va="center")
    plt.setp(ax_r.get_xticklabels(), visible=False)

    # Volumen
    for i in range(n):
        vol_col = GREEN if close.iloc[i] >= open_.iloc[i] else RED
        ax_v.bar(i, volume.iloc[i], color=vol_col, alpha=0.6, width=0.8)
    avg_vol = volume.mean()
    ax_v.axhline(avg_vol, color=TEXT, linewidth=0.5, linestyle="--", alpha=0.5)
    ax_v.set_ylabel("Vol", color=TEXT, fontsize=8)
    ax_v.grid(color=GRID, linewidth=0.3, alpha=0.5)
    plt.setp(ax_v.get_xticklabels(), visible=False)

    # Barra señal
    ax_s.axis("off")
    ax_s.set_facecolor(BG2)
    stxt = "▲ BUY" if is_bull else "▼ SELL"
    dist = (close.iloc[-1] - ema50.iloc[-1]) / ema50.iloc[-1] * 100
    ax_s.text(0.02, 0.5, f"RSI: {rv:.0f}", color=BLUE, fontsize=9, va="center", transform=ax_s.transAxes)
    ax_s.text(0.20, 0.5, stxt, color=sig_col, fontsize=13, fontweight="bold", va="center", transform=ax_s.transAxes)
    ax_s.text(0.42, 0.5, f"Dist EMA50: {dist:+.2f}%", color=TEXT, fontsize=8, va="center", transform=ax_s.transAxes)
    if vp["poc"] > 0:
        ax_s.text(0.68, 0.5, f"POC: {vp['poc']:.4f}", color="#ffeb3b", fontsize=8, va="center", transform=ax_s.transAxes)

    # Panel MTF
    ax_t.axis("off")
    tf_map = {"5M": "5M", "15M": "15M", "30M": "30M", "1H": "1H", "4H": "4H", "1D": "1D"}
    tc_map = {"bullish": GREEN, "bearish": RED, "neutral": "#888888"}
    tt_map = {"bullish": "● BULL", "bearish": "● BEAR", "neutral": "── NEU"}

    ax_t.text(0.5, 0.97, "TIMEFRAME", color=TEXT, fontsize=9, fontweight="bold",
              ha="center", va="top", transform=ax_t.transAxes)
    ax_t.text(0.5, 0.91, "TREND", color="#888888", fontsize=8,
              ha="center", va="top", transform=ax_t.transAxes)

    y_positions = [0.83, 0.74, 0.65, 0.56, 0.47, 0.38]
    for (lbl, _), yp in zip(tf_map.items(), y_positions):
        trend = trends.get(lbl, "neutral")
        ax_t.text(0.1, yp, lbl, color=TEXT, fontsize=10, fontweight="bold",
                  va="center", transform=ax_t.transAxes)
        ax_t.text(0.45, yp, tt_map[trend], color=tc_map[trend], fontsize=9,
                  va="center", transform=ax_t.transAxes)

    bull_n = sum(1 for v in trends.values() if v == "bullish")
    bear_n = sum(1 for v in trends.values() if v == "bearish")

    ax_t.text(0.5, 0.27, "---------", color=GRID, ha="center", transform=ax_t.transAxes)
    ax_t.text(0.5, 0.20, f"Bull: {bull_n}/6", color=GREEN, fontsize=10,
              ha="center", va="top", transform=ax_t.transAxes)
    ax_t.text(0.5, 0.12, f"Bear: {bear_n}/6", color=RED, fontsize=10,
              ha="center", va="top", transform=ax_t.transAxes)

    dom = "ALCISTA" if bull_n > bear_n else ("BAJISTA" if bear_n > bull_n else "NEUTRAL")
    dc  = GREEN if bull_n > bear_n else (RED if bear_n > bull_n else "#888888")
    ax_t.text(0.5, 0.04, dom, color=dc, fontsize=12, fontweight="bold",
              ha="center", va="bottom", transform=ax_t.transAxes)

    now = datetime.now(timezone.utc).strftime("%d/%m %H:%M UTC")
    fig.text(0.01, 0.01, now, color="#444444", fontsize=7)

    buf = io.BytesIO()
    plt.tight_layout(pad=0.5)
    plt.savefig(buf, format="png", dpi=120, bbox_inches="tight", facecolor=BG, edgecolor="none")
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
        f"Pares: XAUUSD, BTCUSD, EURUSD, GBPUSD, US100, EURJPY, US30\n"
        f"Velas: 30M | Revision: cada 15min\n"
        f"Señales: Cross, Reversion, Pullback, Breakout, Estructura\n"
        f"Railway 24/7 ✅"
    )


def run_monitor():
    log.info("=== Ciclo iniciado ===")
    for symbol in SYMBOLS:
        df = fetch_ohlcv(symbol, "30min")
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
            bt  = df.index[-1].strftime("%Y%m%d%H%M")
            if sent_signals.get(key) == bt:
                continue
            sent_signals[key] = bt
            caption   = build_caption(sig, trends)
            img_bytes = generate_chart(symbol, df, sig, trends)
            send_telegram_photo(caption, img_bytes, symbol)
            log.info(f"SEÑAL: {symbol} {sig['type']} @ {sig['price']:.4f}")
            time.sleep(2)

        if not signals:
            log.info(f"{symbol}: sin señales")
        time.sleep(3)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/test":
            try:
                r    = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                                     data={"chat_id": TELEGRAM_CHATID, "text": "✅ Monitor v3.0 OK"}, timeout=15)
                body = f"status={r.status_code}".encode()
            except Exception as e:
                body = str(e).encode()
            self.send_response(200); self.end_headers(); self.wfile.write(body)
        else:
            self.send_response(200); self.end_headers(); self.wfile.write(b"Monitor v3.0 OK")

    def log_message(self, *args): pass


def start_health_server():
    port   = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info(f"Health server puerto {port}")


if __name__ == "__main__":
    print("[INICIO] Trading Monitor v3.0", flush=True)
    send_telegram_text(
        "🚀 TRADING MONITOR v3.0 ONLINE\n\n"
        "Pares: XAUUSD, BTCUSD, EURUSD, GBPUSD\n"
        "US100, EURJPY, US30\n\n"
        "Señales:\n"
        "✅ Golden/Death Cross\n"
        "✅ Reversion alcista/bajista\n"
        "✅ Pullback a EMA50\n"
        "✅ Breakout 20 barras\n"
        "✅ Tendencia fuerte\n"
        "✅ Rompimiento de estructura\n"
        "✅ Volume Profile (POC/VAH/VAL)\n"
        "✅ Zonas soporte/resistencia\n"
        "✅ Niveles de liquidez\n"
        "✅ Panel 5M/15M/30M/1H/4H/1D\n\n"
        "Velas: 30M | Revision: 15min\n"
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
