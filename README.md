# 📊 Trading Monitor — Alertas TradingView → Telegram 24/7

Recibe alertas de TradingView en Telegram con imagen del gráfico, funcionando 24/7 en la nube gratis.

---

## 🗂 Archivos del proyecto

```
trading-monitor/
├── app.py            ← Servidor principal
├── requirements.txt  ← Librerías necesarias
├── Procfile          ← Instrucciones para Railway
└── README.md         ← Este archivo
```

---

## 🤖 PASO 1 — Crear tu Bot de Telegram

1. Abre Telegram y busca **@BotFather**
2. Escribe `/newbot`
3. Ponle un nombre (ej: `Mi Trading Bot`)
4. Ponle un usuario (ej: `mitrading_bot`)
5. BotFather te da un **TOKEN** → guárdalo ✅

**Obtener tu Chat ID:**
1. Busca **@userinfobot** en Telegram
2. Escribe `/start`
3. Te da tu **ID numérico** → guárdalo ✅

---

## 🐙 PASO 2 — Subir a GitHub

1. Ve a [github.com/new](https://github.com/new)
2. Nombre del repo: `trading-monitor`
3. Público → **Create repository**
4. Clic en **"uploading an existing file"**
5. Arrastra los 4 archivos de esta carpeta
6. Clic en **"Commit changes"**

---

## 🚂 PASO 3 — Deploy en Railway

1. Ve a [railway.app](https://railway.app)
2. **Start a New Project** → **Deploy from GitHub repo**
3. Selecciona `trading-monitor`
4. Railway detecta Python automáticamente

**⚠️ IMPORTANTE — Variables de entorno:**
1. En Railway, ve a tu proyecto → **Variables**
2. Añade estas dos variables:

| Variable | Valor |
|----------|-------|
| `TELEGRAM_TOKEN` | El token de @BotFather |
| `TELEGRAM_CHAT_ID` | Tu ID numérico |

5. Railway redeploya automáticamente

**Obtén tu URL pública:**
- En Railway → Settings → **Domains** → Generate Domain
- Será algo como: `https://trading-monitor-xxxx.railway.app`

---

## ✅ PASO 4 — Probar que funciona

Abre en tu navegador:
```
https://TU-URL.railway.app/test
```

Si recibes un mensaje en Telegram → ¡Todo funciona! 🎉

---

## 📡 PASO 5 — Configurar alertas en TradingView

1. En TradingView, crea una alerta (icono del reloj ⏰)
2. En **"Webhook URL"** pon:
   ```
   https://TU-URL.railway.app/webhook
   ```
3. En el mensaje de la alerta, pon este JSON:
   ```json
   {
     "action": "{{strategy.order.action}}",
     "symbol": "{{ticker}}",
     "price": "{{close}}",
     "interval": "{{interval}}",
     "strategy": "Mi Estrategia",
     "message": "{{strategy.order.comment}}"
   }
   ```
4. Guarda la alerta ✅

---

## 📱 Ejemplo de mensaje que recibirás

```
🟢📈 ALERTA DE TRADING 🟢📈
━━━━━━━━━━━━━━━━━━━━
📊 Par: XAUUSD
🎯 Señal: COMPRA (BUY)
💰 Precio: 3241.50
⏱ Temporalidad: 15
🤖 Estrategia: Mi Estrategia
━━━━━━━━━━━━━━━━━━━━
🕐 07/05/2026 18:30:00
```
Con imagen del gráfico adjunta 📸

---

## 🆓 Coste

- **Railway:** $5 crédito gratis/mes (~$0.10-0.15/mes de consumo) = **gratis**
- **Telegram:** Gratis
- **TradingView:** Necesitas plan que incluya webhooks (Essential o superior)

---

## ❓ Problemas comunes

| Problema | Solución |
|----------|----------|
| No llega el mensaje | Verifica TOKEN y CHAT_ID en Variables |
| Error 401 | El TOKEN está mal copiado |
| No llega la imagen | Normal, se envía solo el texto como respaldo |
| TradingView no manda | Verifica que la URL del webhook es correcta |
