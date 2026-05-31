# UP-2.1 — LLM Scope Framework de Decisión

**Fecha:** 2026-05-31 (Domingo cierre)
**Backlog ref:** UP-2.1 MEDIA — "Decisión LLM scope (SPY only vs todas)"
**Estado:** Framework listo. **Discusión real post data lunes 1-jun**.
**Decisión:** Pendiente Juan + data del primer mes de Sprint 21.

---

## TL;DR

Hoy el LLM evalúa solo SPY. Los otros 3 tickers (QQQ, IWM, TQQQ) operan rule-based. Tres opciones a evaluar después del primer mes de prod data:

| Opción | Tickers en LLM | Esfuerzo | Risk | Cost incremento |
|---|---|---|---|---|
| **A. Status quo** | SPY | 0h | 🟢 Baseline | $0 |
| **B. Full LLM** | SPY + QQQ + IWM + TQQQ | 3-4h KB + 1-2h scan | 🟡 MEDIO | ~4x ($0.36 → $1.44/día estimado) |
| **C. SPY + QQQ** | SPY + QQQ | 2-3h KB + 1h scan | 🟢 BAJO | ~2x ($0.36 → $0.72/día) |

**Decision criteria recomendado:** Esperar 4 semanas de prod data. Comparar performance LLM-SPY vs rule-based-QQQ/IWM/TQQQ. Si LLM gana >5pp en Sharpe → expandir.

---

## Estado actual (verificado en código)

### Tickers configurados

`eolo-crop/theta_harvest/theta_harvest_strategy.py:43-...`:

```python
TICKER_CONFIG: dict[str, dict] = {
    "SPY":  {"spread_width": 5.0, "delta_min_abs": 0.15, "delta_max_abs": 0.30, "min_credit": 0.40, "max_dte": 4},
    "QQQ":  {...},
    "IWM":  {...},
    "TQQQ": {...},
}
```

### LLM gate hoy

`eolo-crop/llm_gate/integration.py:65-81`:

```python
# Rule 0 (4.D HZ-2): LLM scope = SPY only. KB diseñado para SPY/VIX
if ticker.upper() != "SPY":
    return False, f"non_spy_ticker_llm_scope_spy_only: {ticker}"
```

- LLM evalúa **solo SPY**
- QQQ/IWM/TQQQ van por rule-based puro (skip LLM call)
- KB v1.2 (61 reglas) está diseñado para SPY/VIX context

### Data del bot

- 4 tickers operan simultáneamente
- SPY: LLM gate + scan + open
- QQQ/IWM/TQQQ: rule-based scan + open (sin LLM consult)
- Trade volume típico: ~5-15 trades/día spread across 4 tickers

### Cost LLM hoy

Pre-Sprint 21: no trackeado.
Post-Sprint 21 (LIVE desde 10:06 ET 31-may): empieza a trackearse desde mañana.
Estimación pre-Sprint 21:
- ~10 LLM calls/día (SPY only)
- ~30% Sonnet, 70% Haiku-skip
- Sonnet: ~$0.015/call. Haiku: ~$0.0015/call
- Total: ~3 × $0.015 + 7 × $0.0015 = $0.045 + $0.011 = ~**$0.056/día**
- Mensual: ~**$1.70/mes**

(Estos números son estimación grosera. Validar con cost real post-deploy Sprint 21 + fix PR #28.)

---

## Opción A — Status quo (SPY only)

### Descripción

Mantener LLM scope = SPY. QQQ/IWM/TQQQ siguen rule-based.

### Argumentos a favor

1. **KB v1.2 optimizado para SPY/VIX.** 61 reglas centradas en SPY: TR-Juan-043 "VIX low + estable → GOLDEN TICKET SPY", TR-Juan-025 "Entry 9:30-11:00 SPY", etc.
2. **Cost LLM mínimo:** ~$1.70/mes (estimado).
3. **Latencia operacional baja:** rule-based QQQ/IWM/TQQQ son inmediatos, sin esperar Anthropic API.
4. **Diversificación natural:** dos enfoques (LLM SPY + rule-based otros) reducen single-point-of-failure cognitivo.
5. **TQQQ (3x leverage) tiene gamma profile distinto:** el LLM puede no transferir bien sus heurísticas de SPY a TQQQ.

### Argumentos en contra

1. **QQQ/IWM/TQQQ ciegos a contexto LLM:** no aprovechan inferencia.
2. **KB v1.3+ podría incluir reglas QQQ/IWM/TQQQ:** stale si no se opera con ellos.
3. **Inconsistencia operacional:** dashboard muestra trades de 4 tickers pero solo 1 con LLM reasoning.

### Cuándo elegir A

- LLM-SPY performance ≈ rule-based QQQ/IWM/TQQQ performance (similar Sharpe, similar drawdown)
- Cost-benefit no justifica expansión
- KB tier system funciona bien para SPY pero no extensible

---

## Opción B — Full LLM (SPY + QQQ + IWM + TQQQ)

### Descripción

Remover Rule 0 del `should_call_llm`. Todos los tickers van por LLM gate. KB se actualiza con reglas QQQ/IWM/TQQQ.

### Implementación

1. **`eolo-crop/llm_gate/integration.py:77`** — remover Rule 0 ("non_spy_ticker_llm_scope_spy_only"). LLM evalúa los 4 tickers.

2. **KB v1.3+:** agregar reglas específicas:
   - TR-Juan-NNN "QQQ tech-heavy, sensible a NVDA earnings"
   - TR-Juan-NNN "IWM small-cap, sensible a M2 / fed rate news"
   - TR-Juan-NNN "TQQQ 3x leverage — confidence cap 7/10, delta más conservadora"
   - TR-Juan-NNN "TQQQ gamma squeeze risk — exit antes que SPY/QQQ"

3. **Esfuerzo:**
   - KB additions: 3-4h (10-15 reglas nuevas + validación)
   - Scan refactor: minimal (ya soporta multi-ticker)
   - Test: 2-3h validación 1 semana en TESTNET o paper trading

### Argumentos a favor

1. **Inferencia consistente:** los 4 tickers se evalúan con misma metodología.
2. **KB consolidado:** una sola fuente de verdad para decisiones.
3. **Mejor para TQQQ:** el 3x leverage tiene matices que rule-based no captura bien (e.g. "TQQQ en VIX spike intraday acelera 3x → exit más temprano").

### Argumentos en contra

1. **Cost LLM ~4x:** $1.70 → ~$6.80/mes. Aún muy bajo en absoluto pero +300%.
2. **Latencia 4x:** cada ciclo scan llama LLM 4 veces. Si la entry window es 1-2 ciclos, esto importa.
3. **KB v1.3 risk:** agregar reglas QQQ/IWM/TQQQ puede degradar reasoning SPY (over-constrained context).
4. **TQQQ behaviour distinto:** el LLM puede sesgar TQQQ basado en SPY heuristics (gamma 3x amplifica errores).
5. **Cold start KB:** las reglas QQQ/IWM/TQQQ no tienen GOLD cases curados todavía (solo SPY).

### Cuándo elegir B

- LLM-SPY supera materialmente rule-based otros (Sharpe +0.3+, drawdown -2pp+)
- Cost incremento ($5/mes adicional) aceptable
- KB v1.3+ tiene capacity para reglas ticker-specific
- Dispuesto a aceptar 4 semanas de validación adicional

---

## Opción C — Híbrida (SPY + QQQ)

### Descripción

Expandir LLM solo a QQQ. IWM + TQQQ siguen rule-based.

### Justificación

- **QQQ es el más correlacionado con SPY:** mismo enfoque de KB v1.2 transfiere bien
- **IWM (small-cap) tiene drivers distintos:** macro news + M2 + fed rate → KB necesitaría rediseño significativo
- **TQQQ (3x leverage):** comportamiento gamma único, riesgo de transfer learning de SPY/QQQ → TQQQ

### Implementación

1. **`eolo-crop/llm_gate/integration.py:77`** — cambiar Rule 0 a:
   ```python
   if ticker.upper() not in {"SPY", "QQQ"}:
       return False, f"ticker_outside_llm_scope: {ticker}"
   ```

2. **KB v1.3:** agregar 3-5 reglas QQQ-specific (tech sensitivity, NVDA earnings, etc.)

3. **Esfuerzo:**
   - KB additions: 2-3h
   - Scan: minimal
   - Test: 1-2 semanas validación

### Argumentos a favor

1. **Trade-off balanced:** captura mayor ganancia LLM (QQQ) sin riesgo TQQQ.
2. **Cost moderado:** ~2x ($1.70 → $3.40/mes).
3. **KB extensión incremental:** menos riesgo de overconstraint que opción B.
4. **TQQQ aislado:** mantenemos rule-based donde gamma profile es único.

### Argumentos en contra

1. **Inconsistencia residual:** IWM + TQQQ siguen ciegos al LLM.
2. **Decisión arbitraria:** "por qué QQQ sí y IWM no" requiere justificación basada en performance real, no a priori.

### Cuándo elegir C

- LLM-SPY supera rule-based, pero diferencia es marginal (Sharpe +0.1-0.3)
- Querés expandir gradualmente
- TQQQ tiene comportamiento clara y rule-based funciona bien para él

---

## Decision criteria framework

### Métricas a comparar (post 4 semanas data)

| Métrica | LLM-SPY | RB-QQQ | RB-IWM | RB-TQQQ |
|---|---|---|---|---|
| **Sharpe ratio** | ? | ? | ? | ? |
| **Max drawdown** | ? | ? | ? | ? |
| **Win rate** | ? | ? | ? | ? |
| **Avg PnL per trade** | ? | ? | ? | ? |
| **Trades/día** | ? | ? | ? | ? |
| **Cost incurred** | $/día | $0 | $0 | $0 |

### Decision tree

```
1. Pre-condition: ≥4 semanas data post Sprint 21 deploy + ≥30 trades por ticker
2. LLM-SPY Sharpe vs avg(RB-otros) Sharpe?
   - Si LLM-SPY < RB-otros → Status quo A (LLM no agrega valor sobre rule-based)
   - Si LLM-SPY ≈ RB-otros → Status quo A (LLM iguala, no justifica cost incremento)
   - Si LLM-SPY > RB-otros por <0.3 → Opción C (expand a QQQ solo)
   - Si LLM-SPY > RB-otros por ≥0.3 → Opción B (full LLM)
3. Si elegimos B o C:
   - Validar 2 semanas en TESTNET (si existe) o paper trading
   - Si TESTNET no disponible: deploy con cap (max 1 trade/día en nuevo ticker primer week)
4. Re-evaluar mensual
```

### Pre-requisitos antes de decidir

- ✅ Sprint 21 LIVE (tokens trackeados)
- ✅ PR #28 fix Haiku cost (cost tracking real)
- ⏳ 4 semanas data en producción
- ⏳ Performance metrics dashboard (UP-2.2 LIVE post-deploy lunes)
- ⏳ KB v1.3 stable (Sprint 18 ejecutado si quisiéramos extensión)

---

## Recomendación inicial (subject to data)

**Hoy, sin data real, recomendaría Opción A (status quo).**

Razones:
1. KB v1.2 está diseñado para SPY/VIX — extenderlo requiere trabajo significativo (UP-1.1 Sprint 18 ya planeado)
2. Cost de LLM-SPY es trivial ($1.70/mes) — no hay presión presupuestaria
3. Rule-based QQQ/IWM/TQQQ tiene 4+ años de tuning empírico → baseline strong
4. Riesgo de degradar SPY al expandir KB es real (over-constrained context)

**Reconsiderar cuando:**

- 4 semanas de data muestren LLM-SPY beating rule-based otros en Sharpe
- Sprint 18 ejecute KB v1.3 con TACTICAL → TACTICAL_PLUS colapso (reduce noise)
- UP-1.3 fase 2 + 3 GOLD cases agreguen casos cross-ticker (TR-Juan-NNN GOLD QQQ tech)

---

## Plan próximas semanas

| Semana | Acción |
|---|---|
| Jun 1-7 | Deploy Sprint 21 fix (PR #28) + UP-2.2 dashboard. Empezar a recopilar data cost + decision_meta per trade |
| Jun 8-14 | Continuar data collection. Si UP-1.2 fase 2 disponible, comenzar a tunear KB v1.2 |
| Jun 15-21 | Sprint 18 ejecución (KB v1.3 colapsa TACTICAL). Re-baseline métricas LLM-SPY |
| Jun 22-30 | **4 semanas data acumulada → DECISIÓN UP-2.1.** Comparar métricas. Aplicar decision tree |
| Jul+ | Si elegimos B/C: implement, validate 2 semanas, monitor mensual |

---

## Para discutir lunes con Juan

**Preguntas concretas:**

1. ¿Tenés intuición sobre cuál opción te interesa más a priori (A/B/C), antes de ver data?
2. ¿Hay restricción presupuestaria de costo LLM? (hoy $1.70/mes, B sería ~$6.80/mes)
3. ¿Querés expand LLM antes o después de Sprint 18 (KB v1.3)?
4. ¿IWM (small-cap) tiene drivers distintos que querés capturar con LLM, o rule-based te alcanza?
5. ¿TQQQ 3x leverage justifica caps especiales (confidence ≤7) o preferís rule-based puro?

**Decisión final esperada:** después de discusión + 4 semanas de data, no esta noche.

---

**Framework completo. UP-2.1 backlog item evolucionado de "decisión pendiente" a "framework + decision tree listo, data collection en curso".**
