# Análisis de reinserción de estrategias post CANDLE-BUFFER-FIX — 2026-06-05

**Pregunta de Juan:** el bug de datos resuelto el 04-jun existía "desde el comienzo de las operaciones".
Las estrategias que sacamos de V1 / Options / Crypto, ¿fueron evaluadas con datos corruptos?
¿Vale la pena reinsertarlas y re-validar?

**Fuentes:** EOLO_Master_Recap_6Mayo2026_v2.docx, EOLO_V1_Auditoria_Completa_v2-0 (17-may),
git history, código de los 4 bots, DIAGNOSE_MULTIBOT_20260604.md.

---

## 1. Alcance real del bug — NO afectó a todos los bots por igual

El bug (Schwab `/pricehistory` con `periodType=day + period=N` devuelve solo la última sesión
COMPLETA, nunca el intraday en curso) afectaba **solo a los bots que usan REST pricehistory
de Schwab para candles intraday**:

| Bot | Fuente de candles | ¿Afectado? | Desde |
|---|---|---|---|
| **eolo-bot (V1)** | REST pricehistory `period=N` | **SÍ — TODA su vida operativa** | El pattern roto está en `Bot/marketdata.py` desde el backup inicial **15-abr** (verificado: `git log -S "periodType"` → 8c167ef). Fix: `502f3b6` 04-jun |
| **eolo-bot-crop** | REST polling `period=1` | **SÍ** — desde su arranque 05-may | Fix: `1659adb` 04-jun. RSI 2m stuck 19h+ confirmado |
| **eolo-options (V2)** | **Streaming websocket** CHART_EQUITY (buffer push) | **NO** — candles llegaban en vivo. Su único uso de pricehistory (`pivot_analysis.py`) pide el OHLC del día *anterior* a propósito (uso correcto) | — |
| **eolo-crypto** | **Binance klines** (testnet) | **NO** — API distinta, sin relación con Schwab | Sus bugs propios (vw_macd signature, volume_reversal_bar adapter) se fixearon el 04-may, ANTES del análisis que apagó estrategias |
| **eolo-soxx3x** | REST pricehistory | SÍ | Fix incluido; diagnose 04-jun: startDate avanza OK |

**Implicación crítica para V1:** entre el 15-abr y el 04-jun el bot calculó RSI/EMA/ATR/VWAP
sobre la sesión cerrada del día ANTERIOR mientras ejecutaba a precios reales del día en curso.
Las señales eran, en la práctica, ruido desfasado ~19 horas. **Esto invalida las métricas
de TODAS las estrategias V1 del período — las apagadas Y las ganadoras.**

---

## 2. Respuestas a las 5 preguntas

### 2.1 ¿Las estrategias removidas podrían haber tenido otro resultado?

**V1 — depende de POR QUÉ se removió cada una:**

| Grupo | Estrategias | ¿Resultado afectado por el bug de datos? |
|---|---|---|
| **Bug arquitectural** (dispatcher `_directional_wrapper` filtra SELL→HOLD, 0 exits) | VWAP_MOMENTUM_LONG, NET_BSV_LONG, DONCHIAN_TURTLE_LONG | **NO.** Su WR 0% era estructural (0 wins porque nunca cerraban). Con datos perfectos hubieran fallado igual. NO reinsertar hasta fixear el wrapper |
| **SHORTs huérfanos** (sin BUY_TO_COVER) | VW_MACD, XOM_30M, HA_CLOUD, EMA, EMA_TSI, EMA_3_8_SHORT (variantes short) | **NO.** Mismo caso: bug de wrapper, no de datos |
| **Performance con señales corruptas** | BOLLINGER (blowup -$172k), carroñeras (RVOL_BREAKOUT, ANCHOR_VWAP, TICK_TRIN_FADE, OPENING_DRIVE, BOLLINGER_RSI_SENSITIVE), EMA_8_21, TSV | **SÍ — plausible.** Sus señales se calcularon sobre candles de ayer. El veredicto "mala estrategia" no es distinguible de "estrategia con datos rotos". Candidatas legítimas a re-test (BOLLINGER además tenía PnL=null en 38.5% de SELLs — bug contable aparte) |

**Crypto — NO.** Las 11 apagadas el 05-may (squeeze, ema_tsi, hh_ll, macd_bb, donchian_turtle,
macd_accel, net_bsv, buy_pressure, sell_pressure, ema_3_8, vwap_momentum) se evaluaron con
datos de Binance, no afectados por este bug. El análisis estadístico (CI95, n grandes,
-13,720 USDT = 98.7% de las pérdidas) **sigue siendo válido. No reinsertar.**
Excepciones ya conocidas: rsi_sma200 (edge real en ETH/BTC, requiere whitelist) y las 2
"investigar" (volume_breakout, ema_8_21) — eso no cambia.

**Options (V2) — NO.** CLAUDE_MEDIUM se deshabilitó por policy y THETA_HARVEST se movió a CROP
por diseño. Además V2 usa streaming, no el endpoint roto. Nada que reinsertar por este motivo.

### 2.2 ¿Es válido y lógico reinsertarlas?

**Para V1: sí, pero el planteo correcto es más amplio.** No es "reinsertar las removidas":
es **re-validar el universo completo de V1 con datos sanos**, porque los $711k de las
"ganadoras" salieron del mismo feed roto que condenó a las "perdedoras". Síntomas que la
propia auditoría ya marcó como sospechosos (MOMENTUM_SCORE WR 99.7%, HA_CLOUD WR 100%)
son consistentes con señales desfasadas + ejecución a precio real.

**Para crypto: no.** Evidencia válida, decisión correcta.
**Para V2: no aplica.**

### 2.3 Si reinsertamos, ¿las estrategias ACTUALES podrían dar datos erróneos?

Al revés: **las actuales ya dieron datos erróneos** — todo el histórico V1 pre-04-jun es
inservible como baseline, incluidas SQUEEZE, VOL_REVERSAL_BAR, EMA, etc.
**Post-fix los datos son sanos**: el diagnose del 04-jun (17:47–17:52 UTC, mercado abierto)
mostró 0% stuck en los 5 bots y divergencia señal/quote ≤ $0.20 en crop.
Condición para que el nuevo cohort sea limpio:

1. Cortar el dataset en el deploy del fix (04-jun). Nada anterior se mezcla con lo nuevo
   (idealmente flag `bug_flag=candle_stale_pre_20260604` en trades viejos, como pedía la auditoría).
2. Fixear ANTES los bugs no-de-datos conocidos de V1, o van a contaminar el re-test igual
   que antes: wrapper direccional (_LONG/_SHORT), PnL=null en SELLs (copiar quote_snapshot
   de options), auto_close retry silencioso.

### 2.4 ¿Cómo fue el trading ayer (04-jun) post-reparación?

Lo verificable desde el repo:

- **Pipeline de datos: sano.** Diagnose 04-jun con mercado abierto: crop 0/4 stuck
  (divergencia ≤ $0.20), eolo-bot 0/10, v2 0/5, crypto 1/9 (LINKUSDT, probable precio plano),
  soxx3x 0/3 con startDate avanzando.
- **Detalle de trades: no disponible desde acá.** Los sheets están desincronizados
  (v1 → 26-may, v2 → 21-abr, crypto → 26-abr) y el journal vive en Firestore.
  Pendiente además la regresión LLM post-t16 del 03-jun (0 llamadas LLM en crop;
  ver EOD_20260604.md §5) — si seguía activa ayer, crop operó sin gate LLM.
- Para ver el journal de ayer: `curl -H "Authorization: Bearer $(gcloud auth print-identity-token)" <CROP_URL>/journal/today` (o fecha 2026-06-04) y logs de decisiones.

### 2.5 ¿Recomendación: volver atrás y re-testear todas?

**Recomendación: re-test selectivo, no rollback global.**

| Bot | Acción |
|---|---|
| **V1** | **SÍ — re-test completo** (ver plan §3). Es el único bot donde el bug invalida la evidencia |
| **Crypto** | **NO re-testear las 11 apagadas.** Mantener. Ejecutar lo ya planeado: whitelist rsi_sma200 (ETH/BTC) y monitoreo volume_reversal_bar |
| **V2 / CROP** | **NO** por este motivo. CROP ya opera con datos sanos post-fix; continuar el plan v2.2 vigente |
| **soxx3x** | Datos ya sanos; si hay métricas históricas relevantes, aplicarles el mismo corte pre/post 04-jun |

---

## 3. Plan propuesto de re-validación V1 (fase única, ~3-4 semanas)

1. **Pre-requisitos de código** (1 sesión): fix wrapper direccional, quote_snapshot para
   PnL de SELLs, auto_close retry. Sin esto el re-test repite los falsos negativos.
2. **Reset de cohort**: counters paper desde cero, flag de trades pre-04-jun, doc de corte.
3. **Reinsertar el set completo razonable**: las 12 "activas" + BOLLINGER + carroñeras +
   EMA_8_21 + TSV. Las _LONG/_SHORT solo si el wrapper quedó fixeado.
4. **Ventana de observación**: n ≥ 30 trades por estrategia (umbral de la propia auditoría).
   A ~19 trades/día hacen falta varias semanas; evaluar subir frecuencia o universo de tickers.
5. **Veredicto con la misma metodología del Master Recap** (expectancy + CI95 + cell-level
   por ticker) — esa metodología demostró ser buena; el problema era el input.
6. La re-evaluación V1 ya estaba agendada para ~17-jun (fin de Pure Isolation ~12-jun).
   Este plan la reemplaza con base de datos limpia: **cohort nuevo 05-jun → ~03-jul**.

---

## 4. Conclusión ejecutiva

El bug valida la duda de Juan **solo para V1** (y crop, que ya opera post-fix): toda señal
intraday V1 desde el 15-abr se calculó sobre la sesión del día anterior. Las remociones por
performance en V1 son veredictos sobre datos rotos y deben re-testearse — pero también las
"ganadoras", cuyos +$711k paper son igual de inválidos. Las remociones de crypto y options
se hicieron con datos sanos y por causas ajenas al bug: se mantienen. Prioridad inmediata:
fixes arquitecturales V1 → reset cohort → re-test 4 semanas con la metodología CI95 existente.
