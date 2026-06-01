# Pivot Analysis Research — NO_TRADE thresholds review

**Generado:** 2026-06-01 (lunes) ~12:00 ET
**Branch:** `research/pivot-no-trade-analysis` (off `b43db56` main)
**Status:** investigación · NO modifica código
**Author:** Claude + Juan

---

## 1. Resumen ejecutivo

El sistema de pivots clasifica cada ticker en una de 4 risk zones según la distancia del precio actual al PP (promedio de Standard + Camarilla + Fibonacci):

| Zone | Threshold `dist_pp_pct` | Delta target | Behaviour |
|---|---|---|---|
| `VERY_LOW` | ≥ 0.80% | Δ 0.10–0.16 | Entrar muy OTM |
| `LOW` | 0.51–0.80% | Δ 0.16–0.22 | Entrar conservador |
| `MID` | 0.25–0.51% | Δ 0.22–0.30 | Entrar normal |
| `NO_TRADE` | < 0.25% | Δ 0.00–0.00 | **No entrar** |

**Hallazgo central:** SPY está en `NO_TRADE` **toda la mañana de hoy (lunes 1-jun)** porque cerró el viernes 30-may muy cerca del PP que se calcula con ese mismo viernes. Esto bloquea cualquier entrada Theta Harvest en SPY durante la ventana de entry (9:30–12:00 ET).

El patrón observado en los últimos 3 días hábiles sugiere que **`NO_TRADE` afecta a SPY/QQQ ~30-50% de las primeras horas del día** en mercados ranged. El threshold `0.25%` puede ser demasiado restrictivo para tickers de baja volatilidad relativa (SPY < QQQ < IWM en ATR%/price).

---

## 2. Cómo funciona el sistema actual

### 2.1 Cálculo de pivots (3 sistemas + promedio)

A partir del OHLC del día previo (vía Schwab `/pricehistory`), se calculan tres sistemas:

- **Standard** (línea 333): `PP = (H+L+C)/3`, niveles ±RNG/múltiplos
- **Camarilla** (línea 348): anclado al close, `PP = (H+L+C)/3`, niveles `C ± RNG·1.1/N` con N ∈ {12, 6, 4, 2}
- **Fibonacci** (línea 368): `PP = (H+L+C)/3`, niveles `PP ± RNG·{0.382, 0.618, 1.0, 1.618}`

Los tres se promedian por nivel (`_average_levels`, línea 388) en una estructura `AveragedPivotLevels`.

### 2.2 Cálculo del risk zone

`AveragedPivotLevels.zone_for_price(price)` (línea 147):

```python
dist_pct = abs(price - self.pp) / self.pp * 100
if   dist_pct >= 0.80: return "VERY_LOW"
elif dist_pct >= 0.51: return "LOW"
elif dist_pct >= 0.25: return "MID"
else:                  return "NO_TRADE"
```

### 2.3 ATR gate (modificador secundario)

`ATRContext.is_extended(price)` (línea 188): si `|price - prev_close| > 2·ATR_day` → mercado extendido → **degrada el risk un nivel**:

```python
remap = {"MID": "LOW", "LOW": "VERY_LOW",
         "VERY_LOW": "VERY_LOW", "NO_TRADE": "NO_TRADE"}
```

Nota: `NO_TRADE` **no se modifica** ni siquiera por ATR gate — es bloqueo duro.

### 2.4 Cache (clave de la dinámica)

`_theta_get_pivot` (`crop_main.py:1041` ó alrededor) **cachea por (ticker, date)** → se calcula 1 sola vez por día por ticker, y el resultado queda fijo hasta el día siguiente o restart del container. Como `price` cambia constantemente pero `PP` queda fijo, el `consensus_risk` **sí cambia intra-día** porque se calcula en `_compute()` cada vez que se reinstancia el `PivotAnalysisResult`. En el flow de producción, sin embargo, el cache evita reinstanciar — por lo que el risk consultado por el bot es **el del momento de cálculo**, no el del momento de decisión.

⚠️ **Subtlety:** este detalle merece confirmación. Si el bot consulta `result.consensus_risk` directamente (atributo congelado), entonces el risk es fijo desde el primer cálculo del día. Si consulta `result.averaged.zone_for_price(current_price)`, entonces actualiza con cada nuevo precio. Recomiendo auditar el call site del bot antes de actuar sobre los thresholds.

---

## 3. Distribución empírica observada

### 3.1 Logs procesados

Fuente: Cloud Logging, filtro `textPayload=~"PivotAnalysis.*risk="`, freshness 7d (limitada por restarts y caches del bot).

Días con data efectiva en ventana de entry (09:30–12:00 ET):

- **2026-05-29 viernes** (3 capturas)
- **2026-06-01 lunes (hoy)** (2 capturas)

`2026-05-28 jueves` solo tiene captura a las 15:26 ET (fuera de ventana entry, post-restart vespertino) — informativa pero no comparable.

### 3.2 Tabla de risk zones observadas

| Fecha | Hora ET | SPY | QQQ | IWM | TQQQ |
|---|---|---|---|---|---|
| 2026-05-28 jue | 15:26 | LOW | VERY_LOW | LOW | VERY_LOW |
| 2026-05-29 vie | 10:00 | LOW | VERY_LOW | **NO_TRADE** | VERY_LOW |
| 2026-05-29 vie | 11:44 | MID | LOW | MID | VERY_LOW |
| 2026-05-29 vie | 13:58 | MID | MID | MID | VERY_LOW |
| 2026-06-01 lun | 09:52 | **NO_TRADE** | **NO_TRADE** | LOW | **NO_TRADE** |
| 2026-06-01 lun | 11:48 | **NO_TRADE** | MID | MID | VERY_LOW |

### 3.3 Conteo de NO_TRADE en ventana entry (capturas únicas)

| Ticker | Capturas en ventana entry | Veces NO_TRADE | % NO_TRADE |
|---|---|---|---|
| SPY | 4 (vie 10:00, 11:44 + lun 09:52, 11:48) | 2 | **50%** |
| QQQ | 4 | 1 | **25%** |
| IWM | 4 | 1 | **25%** |
| TQQQ | 4 | 1 | **25%** |

Sample size muy chico (4 capturas/ticker). Pero el patrón es consistente: **NO_TRADE temprano en la mañana** es frecuente, especialmente cuando el cierre previo está cerca del PP.

### 3.4 Resolución intra-día

Casos observados:

- **2026-05-29 IWM**: NO_TRADE a 10:00 ET → MID a 11:44 ET. El precio se movió de 290.76 a 290.05 (dist al PP=290.92 pasó de 0.055% a 0.299%).
- **2026-06-01 QQQ**: NO_TRADE a 09:52 ET → MID a 11:48 ET. Price subió 738.52 → 741.44 (dist 0.016% → 0.412%).
- **2026-06-01 SPY**: NO_TRADE persistente. Price 756.25 → 757.03 (dist 0.022% → 0.081%) — todavía no llega al threshold 0.25%.

**El factor crítico no es el ticker sino la magnitud del movimiento intra-día relativo al PP.** SPY hoy se movió 0.10% en 2 horas — insuficiente para salir de NO_TRADE.

---

## 4. Snapshot actual (lunes 1-jun ~12:00 ET)

Computado del último log + verificado contra `/api/state.theta.pivots`:

| Ticker | Price | PP | `dist_pp_pct` | Risk actual | Δ target | ATR | Sector |
|---|---|---|---|---|---|---|---|
| **SPY** | 757.03 | 756.42 | **0.081%** | `NO_TRADE` | 0.00–0.00 | 3.39 | neutral +0.02% |
| **QQQ** | 741.44 | 738.40 | 0.412% | `MID` | 0.22–0.30 | 6.38 | neutral +0.02% |
| **IWM** | 288.64 | 290.06 | **0.489%** | `MID` | 0.22–0.30 | 3.08 | neutral +0.02% |
| **TQQQ** | 85.63 | 84.60 | 1.218% | `VERY_LOW` | 0.10–0.16 | 2.17 | bullish +0.54% |

**Observación 1:** SPY está a **0.081% del PP** — necesita moverse otro 0.17% (a ~758.32 o ~754.52) para salir de NO_TRADE. Con ATR 3.39 (=0.45% del price), ese movimiento es posible pero requiere algo que rompa el chop matinal.

**Observación 2:** IWM está a **0.489% del PP**, apenas dentro de MID (threshold LOW = 0.51%). Cualquier oscilación menor lo flipea entre MID ↔ LOW intra-día — comportamiento que el bot probablemente no logguea como evento, pero que afecta el delta target.

---

## 5. ¿Es 0.25% demasiado restrictivo?

### 5.1 Marco de referencia: ATR diario relativo (ATR%/price)

Usando los OHLC del día previo (viernes 30-may para hoy 1-jun):

| Ticker | ATR_day | Price | **ATR/Price %** |
|---|---|---|---|
| SPY | 3.39 | 757 | 0.45% |
| QQQ | 6.38 | 741 | 0.86% |
| IWM | 3.08 | 289 | 1.07% |
| TQQQ | 2.17 | 86 | 2.52% |

**Interpretación:** el threshold `NO_TRADE = 0.25% del PP` representa:
- **56% del ATR diario de SPY** — el SPY necesita moverse más del 50% de su ATR típico para entrar a MID. Eso es alto.
- **29% del ATR diario de QQQ** — más razonable.
- **23% del ATR diario de IWM** — adecuado.
- **10% del ATR diario de TQQQ** — siempre fuera de NO_TRADE.

**Conclusión cuantitativa:** el threshold parece **calibrado contra QQQ/IWM** y **demasiado conservador para SPY**.

### 5.2 ¿Hay justificación operativa para el threshold actual?

Argumentos a favor (preservar 0.25%):
1. **Pin risk:** cuando price ~ PP, el strike ATM tiene gamma máxima → 0DTE muy peligroso. NO_TRADE evita ese fanal.
2. **Pobre risk/reward en strikes cercanos:** prima credit en delta 0.40-0.50 (ATM) es alta pero la prob de touch también — payoff esperado negativo si stops disciplinados.
3. **Filosofía de Juan (TR-Juan-031):** "en setup mediocre ir delta 0.10, no 0.20 — distancia > crédito". Esto refuerza el caso para bloqueo cuando no hay distancia.

Argumentos en contra (relajar 0.25%):
1. **Tickers de baja vol relativa (SPY)** quedan bloqueados desproporcionadamente. Si el régimen de mercado es VIX bajo (TR-Juan-043 GOLDEN TICKET), perdemos las mejores oportunidades **precisamente porque** el precio no se mueve mucho.
2. **El cache fija el risk del día**: si SPY abre en NO_TRADE y permanece, el bot pierde TODAS las ventanas de entry hasta restart o día nuevo. **Costo de oportunidad alto en setups GOLDEN TICKET.**
3. **El LLM tiene contexto adicional** (sector direction, VIX velocity, GEX, range structure) que el threshold puro de pivots ignora. Hay setups con dist=0.20% del PP donde el LLM **sabe** que el precio va a alejarse en la próxima hora.

### 5.3 Hipótesis principal

**El threshold absoluto 0.25% del PP es subóptimo. La métrica correcta sería `dist_pp_pct / atr_day_pct`** — es decir, distancia normalizada por la volatilidad del ticker. Esto haría que SPY y TQQQ tengan thresholds equivalentes en términos de "cuán cerca estamos del PP **relativo a cuánto se suele mover**".

Ejemplo: si threshold = `0.30 × ATR/price`:
- SPY: 0.30 × 0.45% = **0.135% del PP**
- QQQ: 0.30 × 0.86% = **0.258% del PP** (cerca del actual 0.25%)
- IWM: 0.30 × 1.07% = **0.321% del PP**
- TQQQ: 0.30 × 2.52% = **0.756% del PP**

Con ese threshold, SPY hoy (dist=0.081%) seguiría en NO_TRADE, pero el margen sería más chico (0.135% vs 0.25%) — la primera hora menos restrictiva.

---

## 6. Tres propuestas para discutir

### Propuesta A — Mantener thresholds, agregar LLM override flag

**Cambio:** introducir flag `allow_no_trade_override` que el LLM puede setear en su decision. Si el LLM tiene confidence ≥8 y main_reason explicita justificación de por qué el setup vale a pesar de dist baja al PP, el `_run_theta_harvest` ignora el `NO_TRADE` y procede con delta `MID` (0.22–0.30).

**Pros:**
- Cero cambio en thresholds estáticos — preserva el behavior de RULE_BASED.
- Aprovecha el contexto rico del LLM (sector, VIX, GEX, range structure).
- Reversible y observable: cada override queda logueado con razón.

**Cons:**
- Cambio arquitectónico moderado: requiere campo nuevo en `Decision` schema (LLM Engine PR), wiring en `_record_trade_open_sprint9`, y guard en el flow de entry.
- Riesgo de over-trigger si el LLM no entiende bien por qué NO_TRADE existe (necesita prompt update con TR-Juan-031, TR-Juan-043).
- Mide el edge solo en setups LLM (RULE_BASED queda sin acceso).

**Esfuerzo:** ~1 sprint full (LLM Engine + bot + tests + prompt update).

### Propuesta B — Bajar `DIST_MID_PCT` de 0.25% a 0.15%

**Cambio:** en `pivot_analysis.py` líneas 59-62:

```python
DIST_VERY_LOW_PCT = 0.80
DIST_LOW_PCT      = 0.51
DIST_MID_PCT      = 0.15   # ← era 0.25
```

**Pros:**
- Cambio trivial (1 línea). Reversible en 1 commit.
- Habilita SPY mañanas tipo lunes 1-jun (dist=0.08% sigue en NO_TRADE, dist=0.15-0.25% pasa a MID).
- No requiere LLM ni cambio arquitectónico.

**Cons:**
- Aplica uniformemente a SPY/QQQ/IWM/TQQQ — sobre-relaja QQQ/IWM (que ya tenían threshold adecuado).
- Pin risk real: strikes a 0.15% del PP en 0DTE son virtualmente ATM. Riesgo de gamma blow-up.
- No tiene en cuenta volatilidad relativa del ticker.

**Esfuerzo:** ~1 hora (cambio + tests existentes + smoke deploy).

### Propuesta C — Override per-ticker con threshold ATR-normalizado

**Cambio:** reemplazar threshold absoluto por proporción del ATR diario por ticker.

```python
# Reemplaza zone_for_price para usar ATR-normalized threshold
ATR_THRESHOLD_NO_TRADE = 0.30  # 30% del ATR%
ATR_THRESHOLD_MID      = 0.60
ATR_THRESHOLD_LOW      = 1.00  # 100% = ATR completo
# VERY_LOW: > 1.00 × ATR%
```

Cada ticker resuelve `dist_pp_pct / (atr_day / price * 100)` y compara contra esos ratios.

**Pros:**
- Calibrado a la naturaleza de cada ticker. SPY y TQQQ se tratan equivalentemente en términos relativos.
- Más principled: la regla se vuelve "estás a más del 30% de tu ATR típico" en vez de "0.25% absoluto arbitrario".

**Cons:**
- Cambio matemático más profundo. Requiere recalibrar los 4 niveles (NO_TRADE/MID/LOW/VERY_LOW) y validar que delta targets sigan haciendo sentido.
- Los thresholds nuevos (0.30 × ATR%) son sugeridos teóricos — necesitan backtest contra outcomes históricos para validar.
- Sin backtest, riesgo de subutilizar/sobreutilizar tickers de forma no predecible.

**Esfuerzo:** ~1-2 sprints (rewrite zone_for_price + recalibrar thresholds + backtest + tests + deploy).

### Recomendación tentativa

Empezar con **propuesta B (DIST_MID_PCT = 0.15%)** como experimento controlado:
- Costo trivial, rollback trivial.
- 1-2 semanas de observación arroja data empírica real (cuántos trades adicionales se abren, cuál es su outcome distribution vs los MID/LOW históricos).
- Si el resultado es favorable, considerar A (override LLM) como capa adicional para casos edge.
- C queda para Q3 cuando haya backtester maduro.

⚠️ **No deploy hoy.** Esto es research — propuesta B como cambio futuro requiere review + smoke en pre-market + ventana de validación.

---

## 7. Open questions / próximos pasos

1. **Verificar call site del bot:** ¿el bot lee `result.consensus_risk` (frozen) o `result.averaged.zone_for_price(current_price)` (dynamic)? Esto cambia drásticamente cuánto tiempo SPY queda bloqueado en NO_TRADE.

2. **Ampliar el sample histórico:** los logs de gcloud tienen 30d retention default. Para sample de ~20 sesiones, exportar `gcloud logging read --freshness=30d` a CSV y procesar offline. Si el patrón "NO_TRADE 30-50% mornings" se confirma sobre 20 días, la decisión gana peso estadístico.

3. **Outcome de los trades NO ejecutados:** cuando el bot rechaza por NO_TRADE, ¿el setup hubiera sido profitable? Esto requiere un "counterfactual logger" que registre lo que el bot hubiera hecho. Sin esto, propuesta B se debate a ciegas.

4. **Diferenciar regímenes:** quizás NO_TRADE es correcto en regímenes VIX>20 (mercados violentos) pero subóptimo en VIX<14 (GOLDEN TICKET). Threshold dinámico por VIX podría ser un híbrido entre B y C.

5. **Interacción con LLM scope reciente:** PR #32 acaba de expandir el scope LLM a SPY+QQQ+IWM (antes solo SPY). Esto multiplica la importancia del NO_TRADE en QQQ e IWM. La data de los próximos 5 días con scope expandido será mucho más relevante que la histórica.

---

## 8. Files / refs

- `eolo-crop/theta_harvest/pivot_analysis.py:59-62` — thresholds
- `eolo-crop/theta_harvest/pivot_analysis.py:147-159` — `zone_for_price`
- `eolo-crop/theta_harvest/pivot_analysis.py:188-190` — `is_extended` (ATR gate)
- `eolo-crop/crop_main.py:1041` — `_theta_get_pivot` (cache)
- `eolo-crop/crop_main.py:1043-1056` — call site (verifica si lee `consensus_risk` directo)

**Logs source:** `gcloud logging read 'resource.type=cloud_run_revision AND resource.labels.service_name=eolo-bot-crop AND textPayload=~"PivotAnalysis.*risk="' --freshness=7d`
