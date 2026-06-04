# MULTI-BOT-DIAGNOSE — Resultado 2026-06-04

**Run:** `python3 tools/diagnose_all_bots.py 300` desde Mac de Juan, mercado abierto.
**Ventanas:** T0 17:47:42Z → T1 17:52:52Z (300s).
**Pregunta:** ¿el stuck candle buffer (CANDLE-BUFFER-FIX, commits `1659adb`/`502f3b6`) era global o solo eolo-bot-crop?

## Executive summary

| Bot | Stuck | Señal usada | Veredicto |
|---|---|---|---|
| eolo-bot-crop | 0/4 (0%) | HTTP /api/state — signals.price vs quotes.last | ✅ OK — fix validado |
| eolo-bot | 0/10 (0%) | log candle-fetch heartbeat | ✅ OK |
| eolo-bot-v2 | 0/5 (0%) | log BUFFER_MD size+freshness | ✅ OK |
| eolo-bot-crypto | 1/9 (11%) | log RSI_SMA200 per symbol | ✅ OK (ver nota LINK) |
| eolo-bot-soxx3x | 0/3 (0%) | Schwab pricehistory, startDate advanced=True | ✅ OK |

## Conclusión

**El bug era específico de eolo-bot-crop (y eolo-bot, ya parcheado en `502f3b6`), no global.**
El fix startDate/endDate explícitos funciona: crop muestra divergencia signal/quote ≤ 0.20 en
los 4 tickers, soxx3x avanza startDate correctamente, v2 renueva buffers.

## Pendientes menores

1. **LINKUSDT**: ΔRSI = 0.00 en 300s (26.20 → 26.20) con 5 samples por ventana.
   El bot SÍ fetchea (samples constantes) → probable precio plano, no buffer stuck.
   Verificar: re-correr solo crypto con ventana 600-900s; si RSI sigue clavado en 26.20
   exacto, revisar subscription/kline stream de LINKUSDT.
2. **TRXUSDT**: sin línea RSI_SMA200 en logs en ambas ventanas (`rsi=?`).
   Verificar si está en watchlist activa o si su stream está caído.

## Cierre

CANDLE-BUFFER-FIX se considera **cerrado** para crop/bot. No se requiere acción en v2,
crypto (salvo verificación LINK/TRX) ni soxx3x.
