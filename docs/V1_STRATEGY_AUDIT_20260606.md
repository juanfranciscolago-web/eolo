# V1 STRATEGY AUDIT — inconsistencias estrategias × timeframes — 2026-06-06

Scan de `Bot/bot_main.py` (run_cycle), `Bot/strategy_router.py`,
`Bot/bot_strategies_v3_dispatcher.py`, `Bot/marketdata.py`, registry direccional
y `eolo-config/strategies`. Ordenado por severidad.

---

## Respuesta directa: ¿por qué el gate manda a HOLD?

En `run_cycle`, los bloques Tier 2 hacen:

```python
if should_run_strategy(name, ticker, timeframe):
    result = strat.analyze(...)
else:
    result = {"signal": "HOLD", "reason": "not_in_tier2_map"}
```

Es **intencional** (FASE 6 quería operar cada estrategia solo en su combo
ticker/TF óptimo). El gate no está roto; lo que estaba mal era el **contenido
del mapa** (apuntaba a JPM/MSFT/UNH/AMZN/XOM, fuera del universo V1). Se puede
corregir de dos formas: ampliar el mapa (ya hecho 06-jun) o sacar la estrategia
del gateo y dejarla correr en todos los TF como las suite (ver F3).

---

## HALLAZGOS

### H1 — [ALTA] Las variantes `_long`/`_short` son código muerto
`DIRECTIONAL_ENTRY_POINTS` se construye en el dispatcher pero **no se consume en
ningún lado** del repo (grep confirma 0 usos fuera de su definición). `run_cycle`
solo llama a las entradas **base** (`analyze_ema_3_8` → `_dispatch("EMA_3_8")`),
que son las funciones unificadas que emiten BUY y SELL.

Consecuencias:
- Las ~36 keys `_long`/`_short` en `eolo-config/strategies` **no hacen nada**.
- El wrapper-fix `exit_only` y la reinserción de `*_long` (commits de ayer)
  quedan **inertes** — están sobre un path que el bot vivo no ejecuta.
- Los "orphan SHORTs WR 0%" de la auditoría 17-may venían de un orquestador
  viejo (pre-Phase-A), no del `run_cycle` actual.

**Lo que SÍ funciona:** las estrategias base corren y emiten ambas direcciones;
con `allow_short_selling=True` un SELL desde FLAT abre SHORT. O sea el re-test
cubre long y short **por el path base**, no por las variantes direccionales.

**Fix propuesto:** decidir una de dos —
(a) **Limpiar**: borrar las keys `_long`/`_short` de Firestore y el registry
    direccional + revertir la complejidad `exit_only` (no aporta en el path vivo). O
(b) **Cablear**: invocar `DIRECTIONAL_ENTRY_POINTS` desde run_cycle si algún día
    se quiere el split direccional real.
Para el re-test, (a) es lo honesto: el path base ya cubre ambas direcciones.

### H2 — [ALTA] SMA200 es inalcanzable en timeframes intradía
`marketdata.get_price_history` auto-escala días por TF: 5m→2d, 15m→3d, 30m→5d,
60/240→10d. El máximo de velas intradía resulta ~65-78. Cualquier indicador de
**200 períodos no se puede calcular**:
- `rsi_sma200` y el `sma200_filter` de `ema_crossover` quedan degradados o en
  HOLD permanente en 5/15/30/60m (todos los TF activos).
**Fix:** para SMA200, fetchear en daily (1440) y mergear, o bajar el filtro a
SMA50/SMA100 intradía, o documentar que rsi_sma200 solo es válido en 1d.

### H3 — [MEDIA] `should_run_strategy`: solapamiento Tier 1 ∩ Tier 2
`stop_run` y `volume_reversal_bar` están en `TIER1_STRATEGIES` (return True
antes de mirar el mapa) **y** en `TIER2_STRATEGY_MAP`. El mapa para ellas es
**inalcanzable** → su routing Tier 2 nunca se aplica (corren en todo TF igual).
**Fix:** sacarlas de uno de los dos. Si querés routing fino, sacarlas de Tier 1;
si querés que corran en todo (mejor para juntar muestra), sacar su entrada del
mapa Tier 2 para que no confunda.

### H4 — [MEDIA] El mapa Tier 2 de `bollinger` es código muerto
El bloque clásico de `bollinger` en run_cycle corre sobre `tickers_leveraged`
**sin** llamar a `should_run_strategy`. Su entrada en `TIER2_STRATEGY_MAP`
(MSFT/JPM/QQQ/UNH...) nunca se consulta. Hay además dos conceptos de "bollinger":
el clásico (`bollinger_strategy`) y el del mapa Tier 2. **Fix:** unificar — o
gatear el bloque clásico, o borrar la entrada del mapa.

### H5 — [MEDIA] 5 estrategias Nivel 2 dependen de MacroFeeds (punto único de fallo)
`vix_mean_rev`, `vix_correlation`, `vix_squeeze`, `tick_trin_fade`,
`vrp_intraday` devuelven HOLD si `macro is None`. Si `start_macro_feeds` falla al
arranque (Schwab VIX/TICK/TRIN), las 5 quedan en HOLD **en silencio** todo el día
y nunca generan trades. **Fix:** loguear WARN visible cuando macro=None por más de
N ciclos y/o exponerlo en el monitor del cohort.

### H6 — [BAJA] Config-key ≠ label persistido (atribución)
Varias keys de config se persisten en `eolo-trades` con un label distinto:

| config key | label persistido |
|---|---|
| ema_crossover | EMA |
| gap_fade | GAP |
| vwap_rsi | VWAP+RSI |
| volume_reversal_bar | VOL_REVERSAL_BAR |
| spy_qqq_divergence | SPY_QQQ_DIV |
| macd_confluence_fase7a | MACD_CONFLUENCE |
| momentum_score_fase7a | MOMENTUM_SCORE |

El análisis del cohort agrupa por label persistido (consistente internamente),
pero cruzar config↔trades↔veredicto requiere esta tabla de mapeo. **Fix:** tabla
de alias en el evaluador, o normalizar label = config key en `_log_trade`.

### H7 — [INFO] `supertrend`/`macd_bb` calibradas para índices pero iteran leveraged
Sus bloques iteran `tickers_leveraged` (SOXL/TSLL/NVDL/TQQQ) pero la calibración
FASE 6 era para QQQ/SPY/UNH. Tras la ampliación 06-jun corren en leveraged @30m,
pero con params tuneados para índices. **Fix:** re-tunear para leveraged o
moverlas al universo índice si ese era el intent.

---

## Recomendación para el re-test

El re-test corre por el **path base**, que está sano: estrategias base en todos
los TF activos + `allow_short` para ambas direcciones. Antes de redeployar la
ampliación de routing conviene resolver, como mínimo:

- **H3 y H4** (limpieza barata, evita confusión en el análisis).
- **H6** (tabla de alias en `v1_retest_evaluate.py`, para leer veredictos claros).
- **H1**: decidir limpiar vs cablear las direccionales (afecta interpretación).
- **H2 y H5**: documentar/avisar — afectan qué estrategias realmente generan
  muestra (rsi_sma200 y las Nivel 2 podrían dar n≈0 y caer en INSUFFICIENT por
  diseño, no por falta de edge).

H7 y la decisión final de H1 son las únicas que requieren criterio tuyo.
