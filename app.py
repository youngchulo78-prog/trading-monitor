import os
import requests
from flask import Flask, request, jsonify
from datetime import datetime
import pytz

app = Flask(__name__)

# ── Configuración ──────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "TU_TOKEN_AQUI")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "TU_CHAT_ID_AQUI")

# URL del gráfico de TradingView (snapshot)
def get_chart_image_url(symbol="XAUUSD", interval="15"):
    """Genera URL de snapshot de TradingView para el símbolo dado."""
    return f"https://s3.tradingview.com/snapshots/x/{symbol.lower()}.png"

def send_telegram_message(text):
    """Envía mensaje de texto a Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    response = requests.post(url, json=payload, timeout=10)
    return response.json()

def send_telegram_photo(image_url, caption):
    """Envía foto con caption a Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "photo": image_url,
        "caption": caption,
        "parse_mode": "HTML"
    }
    response = requests.post(url, json=payload, timeout=10)
    return response.json()

def format_alert_message(data):
    """Formatea el mensaje de alerta con emojis y datos."""
    action = data.get("action", "").upper()
    symbol = data.get("symbol", "XAUUSD")
    price = data.get("price", "N/A")
    interval = data.get("interval", "")
    message = data.get("message", "")
    strategy = data.get("strategy", "")

    # Emoji según acción
    if "BUY" in action or "LONG" in action:
        emoji = "🟢📈"
        action_text = "COMPRA (BUY)"
    elif "SELL" in action or "SHORT" in action:
        emoji = "🔴📉"
        action_text = "VENTA (SELL)"
    elif "CLOSE" in action:
        emoji = "⚪🔒"
        action_text = "CIERRE"
    else:
        emoji = "⚡"
        action_text = action

    # Hora en zona horaria España/Madrid
    tz = pytz.timezone("Europe/Madrid")
    now = datetime.now(tz).strftime("%d/%m/%Y %H:%M:%S")

    text = (
        f"{emoji} <b>ALERTA DE TRADING</b> {emoji}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Par:</b> {symbol}\n"
        f"🎯 <b>Señal:</b> {action_text}\n"
        f"💰 <b>Precio:</b> {price}\n"
    )

    if interval:
        text += f"⏱ <b>Temporalidad:</b> {interval}\n"
    if strategy:
        text += f"🤖 <b>Estrategia:</b> {strategy}\n"
    if message:
        text += f"📝 <b>Nota:</b> {message}\n"

    text += (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 {now}"
    )

    return text, symbol

# ── Rutas ───────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    return jsonify({"status": "✅ Servidor activo", "mensaje": "Trading Monitor 24/7"}), 200

@app.route("/webhook", methods=["POST"])
def webhook():
    """Recibe alertas de TradingView y las manda a Telegram."""
    try:
        data = request.get_json(force=True)
        if not data:
            return jsonify({"error": "Sin datos JSON"}), 400

        print(f"[ALERTA RECIBIDA] {data}")

        # Formatear mensaje
        caption, symbol = format_alert_message(data)

        # Intentar enviar con imagen del gráfico
        chart_url = get_chart_image_url(symbol)
        result = send_telegram_photo(chart_url, caption)

        # Si falla la imagen, enviar solo texto
        if not result.get("ok"):
            print(f"[INFO] Enviando solo texto (imagen no disponible)")
            send_telegram_message(caption)

        return jsonify({"status": "ok", "enviado": True}), 200

    except Exception as e:
        print(f"[ERROR] {e}")
        # Intentar notificar el error
        try:
            send_telegram_message(f"⚠️ Error en servidor: {str(e)}")
        except:
            pass
        return jsonify({"error": str(e)}), 500

@app.route("/test", methods=["GET"])
def test():
    """Ruta de prueba para verificar que Telegram funciona."""
    result = send_telegram_message(
        "✅ <b>¡Servidor conectado!</b>\n"
        "Tu bot de alertas está funcionando correctamente 🚀"
    )
    if result.get("ok"):
        return jsonify({"status": "ok", "mensaje": "Mensaje de prueba enviado a Telegram"}), 200
    else:
        return jsonify({"status": "error", "detalle": result}), 500

# ── Inicio ──────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Servidor iniciado en puerto {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
