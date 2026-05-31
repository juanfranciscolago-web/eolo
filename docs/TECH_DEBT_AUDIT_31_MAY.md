# Tech Debt Audit — TD-15 / TD-17 / TD-21

**Fecha:** 2026-05-31 (Domingo cierre)
**Files auditados:** `eolo-crop/llm_gate/snapshot.py` (LOC referenciadas)
**Resultado:** **3/3 RESOLVED-AS-DESIGN.** Ninguna bloquea Sprint 18 ni operación normal.

---

## TL;DR

| TD | Categoría | Trigger | Bloqueante? | Acción recomendada |
|---|---|---|---|---|
| **TD-15** | Daily indicators warm-up | daily_buffer None o <14 candles | ❌ NO | WONTFIX — documentar |
| **TD-17** | MACD 15m warm-up | buffer 15m <30 candles | ❌ NO | WONTFIX — documentar |
| **TD-21** | 2m/15m partial window | CandleBuffer cold start | ❌ NO | WONTFIX — documentar |

**Las 3 son fallback warm-up legítimos del stream feed.** El código tiene:
- Manejo graceful (neutrales no-sesgados)
- Warn-once para evitar spam de logs
- Comments explícitos de "CUÁNDO INDICA BUG" para distinguir warm-up legítimo de error real
- Triggers documentados

**Recomendación:** marcar las 3 como WONTFIX en backlog. Actualizar `PROJECT_STATE.md` para reflejar que tech debt residual está cerrado al ser "design intencional, no debt operativo".

---

## TD-15 — BVP/SVP rolling + daily indicators

### Estado actual

```python
def _apply_daily_indicators(snapshot, ticker, daily_buffer, pivot_result):
    if daily_buffer is None:
        _apply_daily_defaults(snapshot, pivot_result)  # graceful fallback
        return
    df = daily_buffer.as_df_1min(ticker)
    if df is None or len(df) < 14:
        _apply_daily_defaults(snapshot, pivot_result)  # graceful fallback
        return
    # ... real indicators ...
```

`_apply_daily_defaults` setea:
- `atr_daily` = `pivot_result.atr.atr_day` (mejor approx que existe sin buffer)
- `rsi_daily` = 50 (neutral)
- `ema_*_daily` = 0.0 (LLM detecta "indicators no disponibles")
- `adr_daily` = 0.0

### ¿Cuándo trigger?

- Cold boot del bot antes de daily_buffer backfill
- Test/dev env sin daily_buffer provisto
- Bug en accessor `as_df_1min` (defensive logger.warning)

### ¿Bloqueante para Sprint 18?

**NO.** Sprint 18 toca KB v1.3 (reglas), no snapshot generation. Defaults conservadores (RSI=50, EMA=0) tampoco sesgan KB queries — el LLM evalúa el snapshot tal cual lo recibe.

### ¿Workaround aceptable?

**Sí.** Sprint 6 ya resolvió el caso normal (daily_buffer poblado). Los defaults son safe net.

### Decisión

**WONTFIX.** Marcar como "implementación intencional graceful warm-up". Actualizar PROJECT_STATE.md.

Caso futuro de refactor: si pasa a tener bug real (defaults activándose con buffer poblado), tratarlo como bug nuevo de `_resample_to_df` o `as_df_1min`, NO como TD-15.

---

## TD-17 — MACD 15m pocos candles

### Estado actual

```python
_MACD_MIN_CANDLES = 30  # min para MACD razonable
_MACD_15M_WARNED = {}    # one-shot por ticker

if len(df_15m) >= _MACD_MIN_CANDLES:
    snapshot["macd_line_15m"], snapshot["macd_signal_15m"], snapshot["macd_histogram_15m"] = calculate_macd(df_15m["close"])
else:
    if not _MACD_15M_WARNED.get(ticker):
        logger.warning(f"[snapshot] {ticker} MACD 15m: buffer={len(df_15m)} < 30 ...")
        _MACD_15M_WARNED[ticker] = True  # warn-once
    snapshot["macd_line_15m"] = 0.0
    snapshot["macd_signal_15m"] = 0.0
    snapshot["macd_histogram_15m"] = 0.0
```

### ¿Cuándo trigger?

- Cold boot del bot, hasta que el buffer 15m acumule 30 bars
- ~7.5h de market time (30 × 15min) desde cold start si tickers NO pre-warmed
- Si tickers pre-warmed: minutos

### ¿Bloqueante para Sprint 18?

**NO.** El LLM y rule-based downstream detectan macd=0 como "señal no disponible" (vs cualquier valor numérico real que dispararía cruces alcistas/bajistas falsos).

### ¿Workaround aceptable?

**Sí.** Warn-once previene log spam. Defaults 0.0 son "honest signal of absence". Mejor que valores estimados que sesgarían decisiones.

### Decisión

**WONTFIX.** Solo afecta cold start. Re-warmup automático.

Mejora futura opcional: tracking de cuando el buffer alcanza 30 candles (log info "MACD 15m READY"). Útil para observability pero no urgente.

---

## TD-21 — Ventana parcial 2m/15m

### Estado actual

```python
def _apply_2m_defaults(snapshot, price):
    """Defaults intencionales 2m — comportamiento de "ventana parcial"."""
    snapshot["rsi_2m"] = 50  # neutral
    snapshot["atr_2m"] = 0.0
    snapshot["ema_9_2m"] = price   # mejor approx sin histórico = price spot
    snapshot["ema_21_2m"] = price
    snapshot["vwap"] = price
    snapshot["vwap_upper_1sigma"] = price
    snapshot["vwap_upper_2sigma"] = price
    snapshot["vwap_lower_1sigma"] = price
    snapshot["vwap_lower_2sigma"] = price
    snapshot["bvp_pct"] = 50.0
    snapshot["svp_pct"] = 50.0
    snapshot["volume_current_bar"] = 0.0
    snapshot["volume_avg_20bar"] = 0.0
```

```python
def _apply_15m_defaults(snapshot):
    """A diferencia de _apply_2m_defaults, NO usamos price como aproximación
    para EMA_9 / EMA_21 — el LLM debe detectar la diferencia (ema=0 → flag
    "indicators no disponibles")."""
    snapshot["rsi_15m"] = 50
    snapshot["atr_15m"] = 0.0
    snapshot["ema_9_15m"] = 0.0
    snapshot["ema_21_15m"] = 0.0
    snapshot["macd_*_15m"] = 0.0
```

### Decisión arquitectónica documentada

- 2m: usa `price` como approx para EMA/VWAP — "mejor aproximación sin histórico es el precio spot"
- 15m: usa `0.0` para EMA — "el LLM debe detectar la diferencia, no tratar 'ema=price' como señal alcista"

### ¿Cuándo trigger?

- 2m: ~28 min market time desde cold start (14 bars × 2min)
- 15m: ~7.5h market time desde cold start (30 bars × 15min)
- Cualquiera de los dos: si `_resample_to_df` retorna None → BUG real, investigar

### ¿Bloqueante para Sprint 18?

**NO.** Los defaults son "honest absence signal" — el KB v1.3 puede evaluar reglas con awareness de "indicators no disponibles".

### ¿Workaround aceptable?

**Sí.** Comments explícitos de "CUÁNDO INDICA BUG" distinguen warm-up legítimo (esperar) de error real (investigar `_resample_to_df`).

### Decisión

**WONTFIX.** Diseño intencional. Documentado.

Mejora futura opcional: agregar flag `indicators_ready_2m: bool` y `indicators_ready_15m: bool` al snapshot para que el LLM sepa explícitamente cuándo confiar en cada timeframe. Útil para KB v1.3 si quisiéramos reglas tipo "si indicators_15m_ready → use MACD".

---

## Recomendación: Actualizar PROJECT_STATE.md

Cambiar la sección "Tech debts residuales" de:

```
| #15 BVP/SVP rolling 100min | ⏳ Parcial | snapshot.py:38 daily_buffer mejorado pero notes mencionan defaults |
| #17 MACD 15m pocos candles | ⏳ Pendiente | snapshot.py:298 — warn-once por ticker pero defaults |
| #21 Ventana parcial 2m/15m | ⏳ Pendiente | snapshot.py:334, 371 — defaults intencionales |
```

A:

```
| TD-15 Daily indicators warm-up | ✅ RESOLVED-AS-DESIGN | Graceful fallback documentado, audit 31-may |
| TD-17 MACD 15m warm-up | ✅ RESOLVED-AS-DESIGN | Warn-once + defaults 0.0, audit 31-may |
| TD-21 2m/15m partial window | ✅ RESOLVED-AS-DESIGN | Diseño intencional con "CUÁNDO INDICA BUG", audit 31-may |
```

**Pending tech debts ahora: 0.**

---

## Mejoras opcionales (no bloqueantes)

Si en algún Sprint futuro tocamos snapshot generation, estas son micro-mejoras de calidad:

1. **TD-15 mejora:** logger.info "[snapshot] {ticker} daily_buffer READY ({n} candles)" cuando llega a 14 candles. Útil para observability del warm-up.

2. **TD-17 mejora:** mismo patrón para MACD 15m READY al llegar a 30 candles.

3. **TD-21 mejora:** agregar `indicators_ready_2m: bool` y `indicators_ready_15m: bool` al snapshot. Permite reglas KB v1.3 tipo "if indicators_ready_15m AND macd_state = bullish_cross → ...".

4. **Refactor opcional:** centralizar todos los thresholds (`_MACD_MIN_CANDLES`, `14` para RSI/ATR daily, etc) en un módulo `snapshot_constants.py`. Hoy están dispersos.

Estas mejoras son de **calidad de iteración**, no de **funcionalidad**. Pueden vivir en un sprint dedicado a snapshot quality cuando tengamos tiempo.

---

**Audit completo. 3/3 WONTFIX con justificación. Backlog de tech debt en código = 0.**
