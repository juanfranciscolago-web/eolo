# Quant Data API — Evaluación para Bot CROP

**Fecha:** 2026-05-31
**Source:** https://quantdata.us/api + https://quantdata.us/api/docs
**Pregunta:** ¿Vale la pena pagar Quant Data ($1500/año) vs free sources?
**Verdict:** **🟢 SÍ vale la pena.** Llena gaps críticos del snapshot (GEX real, dark pool, sweep/block tape) que NO se obtienen gratis. ROI esperado claro.

---

## TL;DR

| Item | Detalle |
|---|---|
| **Costo** | $124.99/mo annual ($1500/año) o $149.99/mo monthly |
| **Endpoints** | 30 (23 options + 6 equities + 1 news) |
| **Rate limit** | 240 req/min (más que suficiente — bot usa ~50/día) |
| **Historical** | 365+ días (viable backtest engine) |
| **Coverage** | All US exchange-licensed |
| **Restricción** | Personal use only (non-pro) — si monetizás bot, requires enterprise |
| **MCP support** | ✅ Nativo — Claude puede llamar tools directo |
| **Recomendación** | **Trial 1 mes ($150)** + integrar 3-5 endpoints críticos + medir |
| **ROI esperado** | Si Sharpe sube +0.3 → $1500 se paga con capital mínimo |

---

## Por qué es perfecto para Theta Harvest SPY

El bot opera SPY 0-4 DTE, donde **GEX positioning + dark pool + institutional sweeps** dominan el flow intradía. Hoy el snapshot tiene:

- ✅ VIX level + velocity
- ✅ RSI, MACD, EMAs, ATR
- ✅ BVP/SVP (volume profile retail)
- ✅ Sector data (Sprint 12)
- ❌ **GEX regime — hoy "unknown" en cases SILVER**
- ❌ Put/call skew real
- ❌ Max pain
- ❌ Dark pool flow
- ❌ Sweep/block tape
- ❌ IV rank/percentile

Quant Data llena los 6 gaps.

---

## Endpoints más relevantes (priorizados)

### TIER 1 — Críticos para Theta Harvest

| Endpoint | Qué da | Reglas KB nuevas |
|---|---|---|
| **`/v1/options/tool/exposure-by-strike`** | GEX + DEX por strike SPY en tiempo real | "GEX > $X B + low VIX → GOLDEN TICKET extended", "Negative GEX zone → defensive" |
| **`/v1/options/tool/max-pain`** | Max pain strike actual del SPY | "Price <2% del max pain → mean revert probable", "Max pain shift entre días → momentum" |
| **`/v1/options/tool/iv-rank`** | IV percentile rolling window | "IV rank < 20 → premium pobre, skip", "IV rank > 80 → premium ideal" |
| **`/v1/options/tool/net-drift`** | Net call/put premium drift intraday | "Net call > +$5M en 30min → bullish, no SELL_CALL", "Net put < -$5M → bearish, no SELL_PUT" |

**Estos 4 endpoints solos justifican el costo.**

### TIER 2 — Muy útiles

| Endpoint | Qué da | Reglas KB |
|---|---|---|
| `/v1/options/tool/heat-map` | Exposure grid expiration×strike | Visualizar dealer pinning |
| `/v1/options/tool/volatility-skew` | IV surface complete | "Put skew steep > X → tail risk, cap confidence" |
| `/v1/options/tool/order-flow-consolidated` | Sweeps + blocks tape | "Sweep volume > $10M side X → institutional aggression" |
| `/v1/equities/tool/dark-flow` | Off-exchange notional + count | "Dark flow > 40% del day volume → institutional accumulation" |

### TIER 3 — Nice to have

- `/v1/options/tool/term-structure` — IV term structure
- `/v1/options/tool/gainers-losers` — premium por ticker
- `/v1/equities/tool/dark-pool-levels` — dark pool levels aggregated
- `/v1/options/tool/volatility-drift` — realized vs implied vol drift

---

## Comparación con free sources

(Reference: `docs/DATA_SOURCES_FREE.md`)

| Field | Free source | Quant Data | Verdict |
|---|---|---|---|
| **GEX** | ❌ No disponible | ✅ Real exchange-licensed | 🏆 **Quant Data único** |
| **Put/Call ratio** | ⚠️ CBOE HTML scraping | ✅ Detailed por strike + intraday | Quant Data mucho mejor |
| **Max Pain** | ❌ Calculable manualmente (complejo) | ✅ Direct endpoint | 🏆 **Quant Data único** |
| **IV Rank** | ⚠️ Calculable con historia | ✅ Pre-computed | Quant Data más rápido |
| **Vol skew** | ⚠️ Calculable | ✅ Surface complete | Quant Data más rico |
| **Macro calendar** | ✅ FRED gratis | ❌ No incluido | 🏆 FRED gratis |
| **VIX term structure** | ✅ CBOE/Yahoo | ❌ Diferente focus | 🏆 Free suficiente |
| **Dark pool flow** | ❌ No free | ✅ Real | 🏆 **Quant Data único** |
| **Sweeps/blocks** | ❌ No free | ✅ Tape real-time | 🏆 **Quant Data único** |
| **News sentiment** | ⚠️ AlphaVantage 25/día | ✅ Pre-tagged | Quant Data más práctico |

**Conclusión:** Quant Data es **único** para 4-5 fields críticos. Free sources cubren los otros (macro, VIX term, sentiment basic).

**Stack óptimo combinado:**
- Free: FRED macro + Yahoo VIX9D + CBOE term structure ($0)
- Paid: Quant Data ($1500/año) para GEX + dark pool + sweeps + max pain + IV rank
- **Total: $1500/año** para snapshot completo

---

## MCP support — game changer

Quant Data tiene **MCP server nativo**. Esto significa:

```
Hoy: Bot CROP → Quant Data REST API → snapshot enrichment → LLM prompt
Con MCP: LLM Engine → Quant Data MCP tools → directo en runtime
```

Para el caso del bot, ambos approaches funcionan, pero MCP simplifica desarrollo:

**Approach A (REST integration en snapshot):**
- Modificar `eolo-crop/llm_gate/snapshot.py` para fetch Quant Data fields antes de generar snapshot
- Pre-compute + inyectar al prompt
- ~3-4h de código

**Approach B (MCP tool en LLM Engine):**
- Configurar LLM Engine para usar Quant Data MCP server
- El LLM decide qué endpoint llamar runtime ("¿necesito ver GEX? llamo exposure-by-strike")
- Más flexible pero más costoso (LLM rouns extra)
- ~2h setup + ongoing token cost increase

**Mi recomendación:** Approach A para el bot trading (pre-compute) + Approach B para análisis ad-hoc con Claude desktop.

---

## Plan de trial (Mes 1, $150)

### Semana 1 — Setup + integrate Tier 1

```bash
# 1. Signup monthly ($149.99) — cancel anytime
# 2. Generate API key desde dashboard
# 3. Guardar en Secret Manager
gcloud secrets create quantdata-api-key --data-file=- <<< "qd_XXXXX"

# 4. Crear módulo nuevo:
# eolo-crop/llm_gate/external_data_quantdata.py
# Con: get_gex(), get_max_pain(), get_iv_rank(), get_net_drift()
# Cache aggressive (5-10 min refresh)
# Defensive: fetch fail → None, snapshot sigue sin GEX
```

### Semana 2 — Integrar al snapshot

```python
# snapshot.py — agregar fields
snapshot["gex_regime"] = qd_data.get("gex_regime")  # "positive_high" / "positive_low" / "flip_zone" / "negative"
snapshot["max_pain"] = qd_data.get("max_pain_strike")
snapshot["iv_rank"] = qd_data.get("iv_rank_30d")
snapshot["net_call_premium_30m"] = qd_data.get("net_call_drift_30m")
snapshot["net_put_premium_30m"] = qd_data.get("net_put_drift_30m")
```

### Semana 3 — KB updates

Agregar 5-8 reglas TACTICAL_PLUS nuevas que usen los fields:

```
TR-Juan-NNN: gex_regime = "positive_high" AND vix < 18 → SELL premium A+ confidence 9
TR-Juan-NNN: gex_regime = "negative" → cap confidence 6, defensive sizing
TR-Juan-NNN: price within 2% del max_pain AND time > 14:00 ET → mean revert probable
TR-Juan-NNN: iv_rank < 20 → premium poor, skip
TR-Juan-NNN: net_call_drift_30m > $5M intraday → no SELL_CALL hasta drift cool down
...
```

### Semana 4 — Métricas + decisión

Comparar performance:
- Mes 0 (baseline): KB v1.2 sin Quant Data
- Mes 1: KB con Quant Data fields + reglas

Métricas a comparar:
- Sharpe ratio LLM-SPY
- Win rate trades >confidence 7
- % verdicts no-WAIT
- Avg PnL per trade

**Decisión renovar/cancelar:**
- Si Sharpe mejora ≥0.2 → renovar annual ($1500)
- Si mejora marginal o no mejora → cancel, mantener free stack

---

## Análisis ROI

### Costo

| Plan | Costo |
|---|---|
| Trial mes 1 | $150 |
| Annual si renueva | $1500 |
| **Break-even** | Mejora de Sharpe que genere $1500 con capital current |

### Beneficio estimado (escenarios)

| Escenario | Sharpe delta | Capital | PnL extra/año | Net (post $1500) |
|---|---|---|---|---|
| Optimista | +0.5 | $50K | +$5K | **+$3.5K** ✅ |
| Realista | +0.3 | $50K | +$3K | **+$1.5K** ✅ |
| Conservador | +0.15 | $50K | +$1.5K | **break-even** ⚠️ |
| Pesimista | +0 (no mejora) | $50K | $0 | **-$1500** ❌ |

**Trial 1 mes ($150) es low-risk way de probar antes de annual commit.**

---

## Riesgos identificados

| Riesgo | Severidad | Mitigación |
|---|---|---|
| Datos Quant Data son retail-grade (delay?) | 🟡 MED | Verificar latency en docs; spec dice "real-time" |
| GEX cálculo varía por provider | 🟢 BAJO | Validar GEX vs SpotGamma/Squeezemetrics gratis dashboards |
| API caída → bot pierde data | 🟡 MED | Defensive: fetch fail → None, snapshot sin field, log warning |
| MCP integration breaking changes | 🟢 BAJO | Approach A (REST) no depende de MCP |
| "Personal use only" si monetizás bot | 🔴 ALTO si vendés bot | Enterprise pricing necesario; no aplica si solo Juan usa |
| Quantdata desaparece como company | 🟡 MED | Bot sigue funcional sin esos fields (defensive design) |

---

## Comparación rápida con alternativas paid

| Provider | Cost/mo | Fields equivalentes | Verdict |
|---|---|---|---|
| **Quant Data** | $125 | GEX + dark pool + sweeps + max pain + IV rank | 🏆 Top ROI |
| SpotGamma | $XX/mo | GEX-focused, sin dark pool tape | Más caro, menos endpoints |
| Squeezemetrics | Free dashboard + API paid | GEX-focused | Free dashboard ok, API caro |
| OptionMetrics | $XX/yr (enterprise) | Historical options, sin real-time tape | No real-time fit |
| Polygon premium | $30/mo | OHLC, no GEX | No fit |
| Unusual Whales | $50-100/mo | Sweeps focus, sin GEX | Parcial |

**Quant Data es la mejor combinación price/coverage para este caso de uso.**

---

## Pasos concretos para empezar HOY

```bash
# 1. Crear account trial (5 min)
open https://quantdata.us/api
# Click "Get API Key" → register → plan Monthly $149.99 → checkout

# 2. Generate API key desde dashboard
# Save: qd_XXXXXXXXXX

# 3. Test desde shell (1 min)
curl -X POST https://api.quantdata.us/v1/options/tool/max-pain \
  -H "Authorization: Bearer qd_XXXXX" \
  -H "Content-Type: application/json" \
  -d '{"sessionDate": "2026-05-30", "filter": {"ticker": "SPY"}}' \
  | python3 -m json.tool

# 4. Si responde 200 con data → integration viable
# 5. Si necesitás MCP, configurar en Claude desktop
```

---

## Recomendación final

### **PROBAR TRIAL 1 MES ($150)**

Razones:
1. Llena gaps únicos no disponibles free (GEX, dark pool, sweeps)
2. MCP support = futuro-proof para Claude integration
3. Bajo risk monetario ($150 cancelable)
4. Si funciona, ROI claro con capital >$25K
5. Si no funciona, perdés $150 + 5h de integración

### Acción concreta

**Esta semana:**
- Signup trial monthly
- Test API key con curl simple (max-pain SPY)
- Verificar que responde data real time

**Próxima semana (post deploy lunes estabilizado):**
- Integrar `external_data_quantdata.py` con 4 endpoints Tier 1
- Agregar 5 reglas KB que usen GEX + max_pain
- Deploy bot CROP + LLM Engine

**Mes 1 cierre:**
- Comparar métricas vs baseline
- Decisión: renovar annual ($1500) o cancelar

---

## Plan ejecución detallado si avanzamos

### Sprint OBS-Data-2 (Quant Data integration) — 5-6h total

**Fase A: Account + API key (15 min)**
- Signup monthly trial
- Save API key en Secret Manager

**Fase B: Módulo external_data_quantdata.py (2-3h)**
```python
# eolo-crop/llm_gate/external_data_quantdata.py

import os, time, json
import urllib.request
from typing import Optional
from loguru import logger

API_BASE = "https://api.quantdata.us/v1"
_API_KEY = None
_CACHE = {}  # {endpoint_key: {"ts": float, "value": dict}}
_TTL = 300  # 5 min cache

def _get_api_key() -> str:
    global _API_KEY
    if _API_KEY:
        return _API_KEY
    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        path = "projects/eolo-schwab-agent/secrets/quantdata-api-key/versions/latest"
        _API_KEY = client.access_secret_version(request={"name": path}).payload.data.decode("utf-8").strip()
    except Exception as e:
        logger.warning(f"[quantdata] secret manager failed: {e}")
        _API_KEY = os.environ.get("QUANTDATA_API_KEY", "")
    return _API_KEY


def _post(endpoint: str, body: dict, cache_key: str = "") -> Optional[dict]:
    now = time.time()
    if cache_key and cache_key in _CACHE:
        cached = _CACHE[cache_key]
        if (now - cached["ts"]) < _TTL:
            return cached["value"]
    api_key = _get_api_key()
    if not api_key:
        return None
    try:
        req = urllib.request.Request(
            f"{API_BASE}{endpoint}",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        if cache_key:
            _CACHE[cache_key] = {"ts": now, "value": data}
        return data
    except Exception as e:
        logger.warning(f"[quantdata] {endpoint} failed: {e}")
        return _CACHE.get(cache_key, {}).get("value")  # stale fallback


def get_max_pain(ticker: str = "SPY") -> Optional[float]:
    data = _post("/options/tool/max-pain", {"filter": {"ticker": ticker}}, cache_key=f"maxpain_{ticker}")
    if not data:
        return None
    # Parse según shape del response (validar con curl primero)
    return float(data.get("data", {}).get("maxPainStrike", 0))


def get_gex_regime(ticker: str = "SPY") -> Optional[dict]:
    """Returns {gex_total: float, regime: 'positive_high'/'positive_low'/'flip_zone'/'negative'}"""
    data = _post("/options/tool/exposure-by-strike", {"filter": {"ticker": ticker}}, cache_key=f"gex_{ticker}")
    if not data:
        return None
    # Aggregate + classify regime
    strikes = data.get("data", {})
    total_gex = sum(s.get("gex", 0) for s in strikes.values())
    if total_gex > 5e9:
        regime = "positive_high"
    elif total_gex > 1e9:
        regime = "positive_low"
    elif total_gex > -1e9:
        regime = "flip_zone"
    else:
        regime = "negative"
    return {"gex_total": total_gex, "regime": regime}


def get_iv_rank(ticker: str = "SPY") -> Optional[float]:
    data = _post("/options/tool/iv-rank", {"filter": {"ticker": ticker}}, cache_key=f"ivrank_{ticker}")
    if not data:
        return None
    return float(data.get("data", {}).get("ivRank", 0))


def get_net_premium_drift(ticker: str = "SPY") -> Optional[dict]:
    data = _post("/options/tool/net-drift", {"filter": {"ticker": ticker}}, cache_key=f"drift_{ticker}")
    if not data:
        return None
    # Aggregate latest bucket
    buckets = data.get("data", {})
    if not buckets:
        return None
    latest_ts = max(buckets.keys())
    return buckets[latest_ts]
```

**Fase C: Wire al snapshot (1h)**
```python
# snapshot.py
from llm_gate.external_data_quantdata import (
    get_max_pain, get_gex_regime, get_iv_rank, get_net_premium_drift
)

# Después de la sección sector data:
if ticker == "SPY":  # Tier 1 SPY only por ahora
    gex_data = get_gex_regime("SPY")
    if gex_data:
        snapshot["gex_regime"] = gex_data["regime"]
        snapshot["gex_total"] = gex_data["gex_total"]
    
    snapshot["max_pain_spy"] = get_max_pain("SPY")
    snapshot["iv_rank_spy"] = get_iv_rank("SPY")
    
    drift = get_net_premium_drift("SPY")
    if drift:
        snapshot["net_call_drift"] = drift.get("netCallPremium")
        snapshot["net_put_drift"] = drift.get("netPutPremium")
```

**Fase D: KB updates (1h)**
Agregar 5-8 reglas al Excel via tools/kb_editor.py (cuando UP-1.2 fase 2 esté listo) o manualmente.

**Fase E: Deploy + monitor (30 min)**
- Build + deploy bot CROP con cambios
- Verify logs: snapshot debe incluir nuevos fields
- 24h monitoring para asegurar cache + fetch funcionan

---

**Generado:** 2026-05-31
**Decisión:** TRIAL 1 mes recomendado. $150 low-risk para validar antes de annual $1500.
**Próximo paso:** Juan signup esta semana + integration próxima semana post deploy estabilizado.


## ADDENDUM 2026-06-01 noche — Sprint 5 backtest 365d UNBLOCKED

Verificación directa contra Quant Data API docs (https://quantdata.us/api/docs)
confirma que TODOS los 30 endpoints aceptan parámetro `sessionDate` para
historical replay dentro del lookback 365+ días. Ejemplo del doc oficial:

```bash
curl -X POST https://api.quantdata.us/v1/options/tool/net-drift \
  -H "Authorization: Bearer <KEY>" \
  -d '{"sessionDate": "2026-05-13", "filter": {"ticker": "AAPL"}}'
```

### Implicaciones Sprint 5 (backtest 365d KB v1.3)

| Calc | Valor |
|---|---|
| Tickers | 3 (SPY, QQQ, IWM) |
| Días | 365 |
| Endpoints relevantes | 4 (max_pain, iv_rank, gex_regime, net_drift) |
| Total calls | 4,380 |
| Wall-clock (240 req/min) | ~18 min |

Granularidad: nivel sesión (1 sesión = 1 día). Dentro de cada sesión, response
devuelve buckets time-keyed milisegundo (intraday detail dentro de la sesión).
Sufficient para Theta Harvest backtest (opera entradas en apertura + monitoring
cada 5 min — no necesita tick-level).

### Comparación con FlashAlpha (alternativa evaluada esta noche)

FlashAlpha Alpha tier ($1,499/mo) ofrecería 8 años (desde Apr-2018) + minute-level
granularity. Overkill para Sprint 5; lookback Quant Data de 1 año alcanza. Reserve
FlashAlpha para futuro si Sprint 18+ revela necesidad de backtest profundo (ej:
training ML model sobre patterns multi-año).

### Otros providers descartados

- **ORATS**: 25 años EOD depth, sin intraday → no sirve para Theta Harvest 0-3 DTE.
- **Polygon / ThetaData**: raw ticks pero analytics layer self-built (~6 meses pipeline).
- **FlashAlpha free**: 5 calls/día, insufficient.

### Acciones pendientes próxima sesión

1. **Smoke test**: query con sessionDate de hace 30 días → confirmar que trial cubre historical (probable, no verificado todavía).
2. **Pricing API tier real**: v3.quantdata.us/pricing no renderizó vía web-fetch (client-side). Necesita login para ver billing real post-trial.
3. **Decision 7.1 reconsiderada**: con backtest unblocked, considera extender Sprint 1 a 5-6 endpoints más (charm, vanna, dealer_positioning) ANTES de Sprint 5 — así el backtest los prueba juntos con los 4 actuales en una sola pasada.

### Tech debt detectado esta noche en cliente

- `external_data_quantdata.py` línea 22 docstring stale: afirma "No se modifica snapshot.py ni crop_main.py desde este módulo" pero el wire se ejecutó en OPS-3 (snapshot.py:355-406 importa y llama get_max_pain / get_iv_rank / get_gex_regime / get_net_premium_drift). Cosmético, no funcional. Candidato cleanup oportunista próxima sesión.
- Línea 9 docstring marca "net-drift TODO: validar response antes de wire a snapshot" — el wire se hizo igual, y resultaba ser parte del bug Pydantic boundary cerrado por hotfix #95. TODO obsoleto, remover.

### Conexión con tasks pendientes

- **Sprint UP-1.4** (próximo KB v1.3 unificado en roadmap v2.2): incorpora Fase D original del memo (5-8 reglas KB que usen GEX/max_pain/iv_rank/drift). Fase D nunca se ejecutó al cierre 31-may; la deuda persiste pero el wire ya está LIVE post-#95.
- **Task #94** (GEX thresholds vs SpotGamma calibración): conecta con riesgo explícito documentado en este memo ("GEX cálculo varía por provider"). Mantener post-7-días-data.
