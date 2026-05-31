# GOLD CASE 002 — TR-Juan-014 THETA DECAY BASE

## Meta

- **case_quality:** GOLD
- **case_id:** 2026-05-20_SPY_2m_gold_002
- **rule_demonstrated:** TR-Juan-014 ⭐ MAESTRA — "Cualquier posición short premium abierta → theta decay SIEMPRE a favor del seller. Salario base del Theta Harvest"
- **rag_tags:** spy;sell_put;moderate_vix;theta_base;maestra;chop_setup;0dte;tr_juan_014;range_bound;defensive_delta
- **author:** Juan + Claude (curated post-mortem)
- **review_status:** GOLD — caso paradigmático para diferenciar "theta puro" de "trade direccional disfrazado"

---

## A. Identification

| Field | Value |
|---|---|
| **case_id** | 2026-05-20_SPY_2m_gold_002 |
| **ticker** | SPY |
| **date** | 2026-05-20 |
| **time_analysis** | 11:48 ET |
| **timeframe** | Multi: 30m + 2m |
| **chart_filename** | GOLD_002_SPY_30m.png + GOLD_002_SPY_2m.png |
| **session_label** | mid_morning_to_lunch (transición a chop lateral) |

## B. Macro Context

| Field | Value |
|---|---|
| **vix_level** | 17.85 |
| **vix_velocity_24h_pct** | +0.45 (plano, 24h prior 17.77) |
| **spy_trend_daily** | range (consolidación tras pullback de la semana previa) |
| **day_of_week** | Tuesday |
| **days_to_next_macro** | 8 (FOMC minutes Mayo 28) |
| **next_macro_event** | FOMC_minutes |
| **days_to_ticker_earnings** | NA (ETF) |
| **gex_regime** | flip_zone (~$0.4B, near zero — sin amortiguador estructural fuerte) |
| **session_news** | Sin catalysts. Reportes earnings mid-cap mixtos pre-market sin impact en SPY. Sesión típica martes "nada pasa" |

## C. Chart Reading

| Field | Value |
|---|---|
| **trend_intraday** | range (oscilación 749.20-750.85 desde 10:45 ET) |
| **ema_state** | tangled (EMAs 8/21 entrelazadas, sin stack claro) |
| **ema_slope** | flat |
| **rsi_value** | 49.2 |
| **rsi_zone** | neutral |
| **macd_state** | neutral (cruces falsos múltiples última hora) |
| **macd_hist** | -0.02 (oscilando entre +0.05 y -0.05 — ruido puro) |
| **bvp_pct** | 49.8 |
| **svp_pct** | 50.2 |
| **volume_pressure_state** | balanced (literalmente 50/50, sin convicción) |
| **fractal_cci_value** | -12 |
| **fractal_cci_zone** | neutral |
| **atr_value** | 0.24 (ATR 2m muy bajo dentro del rango — chop confirmado) |
| **session_high_dist_pct** | -0.18 (precio actual 0.18% bajo el high 750.85) |
| **session_low_dist_pct** | +0.04 (precio 0.04% sobre el low 749.20 — cerca del bottom del range) |
| **active_signals** | Ninguna direccional. Solo "range_chop" filter activo |
| **price_action_pattern** | chop (velas pequeñas, mechas largas, sin direccionalidad) |
| **key_levels_visible** | PDH 751.40, PDC 750.10, PDL 748.55; Session High 750.85 (resistencia testeada 4x); Session Low 749.20 (soporte testeado 3x); Range central 750.00 |
| **cvd_data_source** | tos_bvp_svp |

## D. Claude Decision

| Field | Value |
|---|---|
| **c_action** | SELL_PUT |
| **c_strike_target** | 745 (~0.6% OTM bajo spot 749.50) |
| **c_delta_target** | 0.10 |
| **c_dte_target** | 0 (vence hoy 16:00 ET, 4h12min hasta close) |
| **c_confidence** | 6 |
| **c_main_reason** | Setup ordinario — no hay edge direccional. Operamos PURO theta: VIX moderado paga prima decente + chop lateral + strike 1% bajo el range bottom = improbable touch. No esperar profit espectacular, esperar que el tiempo trabaje |
| **c_secondary_reasons** | RSI 49 sin overbought ni oversold (neutralidad pura); volume balanced 50/50 confirma chop; ATR 0.24 sub-promedio; range testeado 4 veces tope + 3 veces piso sin break — high probability de continuación del range; strike 745 a -0.6% del range bottom requeriría break + extensión adicional |
| **c_exit_triggers** | (1) Captura ≥60% del crédito → close. (2) Tiempo: 14:45 ET hard close (15min antes del cierre, evitar gamma flip). (3) SPY break <749.00 con volumen >1.3x promedio → close defensivo. (4) VIX spike >+10% → close inmediato |
| **c_abort_triggers** | (1) Range break <749.00 antes de entrada → buscar setup direccional o esperar nuevo range. (2) VIX velocity >+5% intraday → no entrada. (3) RSI <30 (improbable hoy pero hard floor) → no entrada |
| **c_unknowns** | Si el chop persiste o quiebra direccional en últimas 2 horas (gamma intensifica). Liquidez del strike 745 en 0DTE — bid-ask spread podría comer parte del crédito |

## E. Juan Input

| Field | Value |
|---|---|
| **j_action** | SELL_PUT (FULL AGREEMENT con caveat de expectativas) |
| **j_strike_target** | 745 (single leg, sin spread — capital eficiencia menor pero theta puro) |
| **j_delta_target** | 0.10 (Juan confirma: "en setup mediocre voy MÁS lejos del precio, no más cerca") |
| **j_dte_target** | 0 |
| **j_confidence** | 6 |
| **agreement_level** | FULL |
| **juan_saw_extra** | (1) El caso clave es la INTENCIÓN: este NO es un trade direccional disfrazado de theta. Es theta puro. Si esperás profit por movimiento, no tomes este trade — vas a estar mirando la pantalla obsesivamente sin razón. (2) "Salario base" significa que mientras la posición esté abierta, theta paga aunque sea poco. En setup mediocre, captura ~50% del crédito en ~3-4h es el resultado esperado, NO el 80% del GOLDEN TICKET. (3) El strike delta 0.10 (no 0.20) es el diferenciador: aceptás menos crédito a cambio de mucha más distancia al strike. En chop, la distancia gana sobre el crédito |
| **claude_overweighted** | Claude tiende a buscar confidence más alta (sube a 7 si ve macd neutral en lugar de bearish). En realidad este es setup confidence 6 sólido — ni 5 ni 7. NO inflar |
| **tacit_rules_applied** | TR-Juan-014 ⭐ MAESTRA (theta decay base — siempre paga); TR-Juan-022 (range-bound confirmado por 4 toques top + 3 piso); TR-Juan-031 (en setup mediocre ir delta 0.10, no 0.20 — distancia > crédito); TR-Juan-034 (no buscar setups perfectos los martes — martes son días de chop por convicción baja de participantes); TR-Juan-055 (close 15min pre-EOD en 0DTE para evitar gamma flip); TR-Juan-061 (60% capture target en setup mediocre, no 80%) |
| **notes** | CASO PARADIGMÁTICO DE LA MAESTRA. La intuición clave NO es técnica sino conceptual: aceptar que en setup mediocre el trade vale la pena SOLO si entendés que estás cobrando el salario base, no buscando alpha. La mayoría de traders sub-óptimos pierden plata aquí porque (a) entran esperando direccional, (b) cierran tarde porque "casi llega al target", (c) rolean para escapar pérdida pequeña y se exponen a riesgo overnight. Este caso muestra cómo Juan lo opera disciplinado: entry a delta 0.10, exit a 60%, sin emoción |

## F. Outcome

| Field | Value |
|---|---|
| **trade_executed** | YES (paper, 5 contratos single leg PUT 745) |
| **entry_price** | 0.18 (crédito por contrato — modesto, expected en delta 0.10 0DTE) |
| **exit_price** | 0.07 (recompra 14:40 ET, 61% capture) |
| **days_held** | 0 (2h52min reales) |
| **pnl_pct** | +61.1 (capture, $55 neto sobre $90 crédito inicial — 5 contratos) |
| **exit_reason** | profit_target (60% capture trigger gatillado a las 14:40 ET cuando precio comprimió a 0.07 con SPY oscilando aún en 749.40-750.20) |
| **lesson_learned** | LA MAESTRA EN ACCIÓN. Setup mediocre + intención correcta = profit modesto pero positivo. $55 neto en 2h52min = 19.1%/hora. No es el GOLDEN TICKET (23-25%/hora) pero ESTÁ EN EL RANGO ESPERADO de "salario base". Validates TR-Juan-014: con disciplina y delta 0.10, el chop te paga mientras el tiempo pasa |

## G. ML/LLM Features

### features_json

```json
{
  "setup_type": "theta_base_chop",
  "vix_regime": "moderate_stable",
  "vix_level_bucket": "moderate_15_20",
  "vix_velocity_bucket": "stable",
  "trend_alignment": "range_no_direction",
  "primary_signal": "range_consolidation_4top_3bottom",
  "secondary_signals": [
    "rsi_perfect_neutral_49",
    "volume_pressure_balanced_50_50",
    "atr_subpromedio",
    "ema_tangled",
    "macd_oscillating_noise"
  ],
  "entry_strategy": "single_leg_sell_put_far_otm_0dte",
  "exit_strategy": "profit_target_60pct_mediocre",
  "expected_capture_pct": 60,
  "expected_hours_to_target": 3.0,
  "maestra_invoked": "TR-Juan-014",
  "tacit_rules_count": 6,
  "confidence_score": 6,
  "case_paradigm": true,
  "rag_priority": "high",
  "intent_classification": "theta_pure_no_directional_bias",
  "antipattern_warning": "do_not_inflate_confidence_above_7"
}
```

### rag_tags

`spy;sell_put;moderate_vix;theta_base;maestra;chop_setup;0dte;tr_juan_014;range_bound;defensive_delta;tuesday;intent_theta_pure;mediocre_setup`

### case_quality

**GOLD**

## H. Additional Fields

| Field | Value |
|---|---|
| **strategy_type** | SELL_PUT (single leg, no spread) |
| **gamma_risk_level** | MEDIUM (delta 0.10 mitiga pero 0DTE con GEX flip_zone = vigilar últimas 2h) |
| **time_to_planned_close_hrs** | 3.0 (target close 14:45 ET, hard stop 15:00 ET) |
| **credit_received_pct_target** | 60 (capturar 60% del crédito = $0.11 de los $0.18 — expectativa realista en setup mediocre) |
| **actual_pct_captured** | 61 |
| **hours_to_target** | 2.87 |
| **profit_per_hour** | 19.1 (% por hora — dentro del rango esperado para "salario base", inferior al GOLDEN TICKET 23%+) |

---

## Lesson Learned (extendido)

**¿Por qué este caso ES la MAESTRA TR-Juan-014 y no solo un trade mediocre cualquiera?**

La regla parece banal en su enunciado: "theta decay siempre paga al seller". Cualquier libro de opciones la menciona. Pero la MAESTRÍA está en la **disciplina de la intención** — operar este trade con la mentalidad correcta es el 80% del edge. Si la mentalidad es la equivocada, el mismo setup que aquí produce +61% se transforma en pérdida.

**El test conceptual: "¿Por qué tomás este trade?"**

Hay 3 respuestas posibles al snapshot 11:48 ET del 20-may:

- **(A) "Creo que SPY va a quedarse arriba de 745."** → respuesta de trader direccional. Si elegís esto, estás pensando que el strike "no va a ser tocado" como una predicción positiva. PROBLEMA: cuando SPY se acerca a 745.50, vas a entrar en pánico y cerrar prematuro porque tu tesis direccional está siendo amenazada. Tu mentalidad te traiciona aunque el setup sea correcto.

- **(B) "El range está bien definido, tiene 7 toques de confirmación, el break es improbable."** → respuesta de trader técnico. Mejor que (A) pero todavía estás operando una hipótesis (range hold), no theta puro. Cuando el range muestra incertidumbre intraday (señal falsa de break), vas a re-evaluar y posiblemente cerrar antes del target. Resultado: capturas menos prima de la posible.

- **(C) "Mientras la posición esté abierta y el strike no sea tocado, theta me paga. No espero direccional, no espero range hold como condición — espero pasaje del tiempo."** → respuesta de la MAESTRA. Aceptás que el strike PODRÍA ser tocado, pero (a) la probabilidad es baja por delta 0.10 (~10% prob touch implícita), (b) si pasa, lo manejás con exit defensivo a -1.5x crédito, (c) en el escenario "no pasa nada" (más probable), theta te paga sin requerir movimiento favorable.

**La diferencia operativa:**

Trader (A) y (B) van a estar mirando el chart cada 10 minutos, sobre-reaccionando a cada vela. Trader (C) entra, pone alerta a 60% capture y 14:45 ET, y se va a hacer otra cosa. El edge está en el COMPORTAMIENTO, no solo en el setup.

**¿Qué señales convergen para clasificarlo MAESTRA?**

- **VIX moderado 17-20:** paga prima decente sin sobre-pagar (en VIX bajo absoluto la prima es tan poca que no compensa el bid-ask; en VIX alto la prima es atractiva pero el riesgo de spike absorbe el edge).
- **VIX velocity ~0:** ausencia de vega risk. Nadie está corriendo a comprar seguros, nadie los está vendiendo en pánico.
- **RSI perfecto neutral 49.2:** literalmente equidistante de overbought y oversold. Es la zona donde el precio NO tiene tendencia a mean-reverse en ninguna dirección — chop puro.
- **Volume_pressure 49.8/50.2:** simétrico. Buyers y sellers en equilibrio total.
- **ATR 0.24 sub-promedio:** volatilidad realizada baja. El range no se va a expandir.
- **Range testeado 7 veces (4 top + 3 piso):** cada test sin break refuerza la resistencia/soporte. Prob de break en próximas 4h: estadísticamente ~20-25%.
- **GEX flip_zone:** este es el WARNING flag. En GOLDEN TICKET el GEX es positivo masivo; aquí es near-zero. NO hay airbag estructural. Por eso el delta debe ser MÁS conservador (0.10 vs 0.20).

**¿Cuándo NO sería un trade MAESTRA aunque parezca chop?**

- **Range con <5 toques de confirmación:** insuficiente evidencia técnica. Esperar 1-2 más.
- **GEX negativo:** dealers cortos gamma amplifican movimientos. El chop puede romperse violentamente. NO entrar.
- **Día de macro event (FOMC <24h):** el chop puede ser la calma antes del catalyst. Vega risk asimétrico.
- **VIX subiendo aún si nivel moderado:** velocidad positiva indica fear acumulándose. Theta seller en peor posición.
- **Si el trader siente que "necesita un trade":** este NO es el setup para forzar entries. El edge es chico, requiere ejecutarlo limpio. Si forzás emocionalmente, perdés disciplina y rompés la regla de los 60%.

**Lección operativa para futuras decisiones:**

Cuando el LLM enfrente un snapshot con `vix_level ∈ [15, 20]` + `trend_intraday=range` + `volume_pressure_state=balanced` + `rsi_zone=neutral`, debe:

1. **NUNCA proponer confidence >7.** Este es setup de "salario base", no de alpha.
2. **Forzar delta ≤0.12** en lugar del 0.15-0.20 estándar.
3. **Target de capture 50-60%, NO 70-80%.** Expectativas alineadas con el setup.
4. **Hard time stop antes de gamma flip** (>30min antes de close en 0DTE).
5. **Explicitar en main_reason que el trade es "intencionalmente modesto"** — ayuda al usuario (Juan o paper trader) a NO inflar expectativas.

---

## RAG Retrieval Notes

**Por qué este caso debería emerger en RAG para queries tipo "moderate VIX + chop + SELL_PUT" o "low edge setup theta base":**

1. **Cobertura de la MAESTRA conceptual.** TR-Juan-014 es la regla que justifica ~40% de los trades del sistema. Cualquier query sobre "theta puro" o "setup mediocre" o "chop lateral" debe traer este caso como anchor.

2. **Contraste estructural con GOLD_001.** Este caso es el OPUESTO complementario del GOLDEN TICKET. Cuando el LLM recibe un snapshot, debería poder navegar entre los dos casos para decidir cuál es más representativo:
   - VIX <14 + estable + GEX positivo → cita GOLD_001
   - VIX 15-20 + range + GEX flip → cita GOLD_002
   - Setup ambiguo o mixto → cita ambos y muestra la dialéctica

3. **Antipattern explícito para confidence inflation.** El features_json incluye `antipattern_warning: do_not_inflate_confidence_above_7`. Cuando el LLM tienda a marcar setup chop como confidence 8-9 (error común: confundir baja volatilidad con alta certidumbre), este flag lo recalibra.

4. **Outcome modesto pero positivo es la enseñanza.** Si el LLM solo retornara casos con outcome espectacular, sesgaría hacia setups GOLDEN. Este caso enseña que **+61% capture en setup mediocre es éxito**, no failure. Recalibra el reward function implícito del modelo.

5. **profit_per_hour 19.1 como anchor del "buen suficiente".** En queries sobre evaluación de setup, el LLM puede usar este número como benchmark inferior de éxito en theta puro. Sub-19%/hora en setup chop indica error: o (a) entry tardía, (b) delta demasiado conservador, (c) exit tardía. Sobre-23%/hora en chop = sospechar que en realidad fue setup mejor que mediocre y no se aprovechó del todo.

6. **Antipattern detection para queries direccionales.** Cuando el LLM evalúa un setup donde el usuario pregunta "¿es buena entrada SELL_PUT acá?" y el snapshot muestra signals direccionales (RSI >65, EMA alcista clara, volume>60%) → este caso debería emerger como contraste advirtiendo: "Si tu intención es theta puro, este NO es el setup chop ideal. Si tu intención es direccional, no uses el modelo theta-base — calibra estrategia diferente". Esta meta-cognición es uno de los outputs más valiosos del caso.

**Cross-reference con otras reglas:**

- TR-Juan-022 (range confirmation) — este caso lo ilustra técnicamente.
- TR-Juan-031 (delta 0.10 en mediocre) — este caso lo ilustra operativamente.
- TR-Juan-055 (close 15min pre-EOD 0DTE) — este caso lo ilustra defensivamente.
- TR-Juan-061 (capture target ajustado al setup) — este caso lo ilustra con 60% target apropiado.

El caso es un hub de 4 reglas tácticas + 1 maestra. Alta densidad de aprendizaje por byte.
