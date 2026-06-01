# Data Sources Gratis — Evaluación para snapshot enrichment

**Fecha:** 2026-05-31
**Objetivo:** Identificar qué data sources gratis integrar al snapshot del bot CROP para desbloquear nuevas reglas KB y mejorar autonomía del LLM.
**Scope actual del bot:** SPY only (LLM gate). Otros tickers rule-based.

---

## TL;DR — Ranking final (1 = top priority)

| # | Source | Free tier | Setup | ROI esperado | Reglas KB desbloqueadas |
|---|---|---|---|---|---|
| 1 | **CBOE Put/Call ratio** | ∞ (HTML scrape) | 30 min | 🟢 ALTO | 4-6 reglas sentiment contrarian |
| 2 | **FRED (macro calendar)** | 120 calls/min | 1h | 🟢 ALTO | 5-8 reglas pre-FOMC/CPI/NFP avoidance |
| 3 | **CBOE VIX term structure** | ∞ (HTML/CSV) | 1h | 🟢 ALTO | 3-5 reglas backwardation/contango |
| 4 | **Yahoo Finance unofficial API** | ∞ (sin key) | 1h | 🟡 MEDIO | Sector rotation + cross-asset correlations |
| 5 | **AlphaVantage** | 25/día gratis | 30 min | 🟡 MEDIO | News sentiment cuantificado |
| 6 | **Fear & Greed Index** | ∞ (scraping) | 30 min | 🟡 MEDIO | 2-3 reglas extremos sentiment |
| 7 | **Polygon free tier** | 5 calls/min | 2h | 🟡 BAJO | Redundante con Schwab para SPY |
| 8 | **Tradier sandbox** | ∞ paper | 1-2h | 🟡 BAJO | Backup quotes/chains |
| 9 | **Quiver Quant** | 1 endpoint free | 30 min | 🔴 BAJO | Politicians trades (raro disparen SPY) |
| 10 | **NewsAPI** | 100/día | 1h | 🔴 BAJO | News raw (Anthropic ya razona bien sobre eventos) |

**Recomendación:** Integrar #1, #2, #3 primero (Top 3 = ~3h trabajo, ~12-19 reglas KB nuevas).

---

## #1 — CBOE Put/Call Ratio (RECOMENDADO #1)

### Qué es
Ratio de volumen de PUTs vs CALLs tradeados en CBOE en el día. Cuando >1.0 = más miedo (más PUTs). Cuando <0.7 = complacencia (más CALLs).

### Por qué importa
- **Contrarian signal histórica:** ratio > 1.2 → bullish reversal probable. Ratio < 0.5 → bearish reversal.
- **Complemento perfecto del VIX:** VIX mide vol implícita, P/C mide flow real
- **No requiere account paid de Schwab/OptionMetrics**

### Cómo obtenerlo gratis
```python
# Scraping de CBOE público
import urllib.request
URL = "https://markets.cboe.com/us/options/market_statistics/"
# O endpoint JSON-like:
URL_DATA = "https://cdn.cboe.com/api/global/delayed_quotes/symbol_summary.json?symbol=SPX"
```

Alternativa: Yahoo `^CPC` ticker tiene daily P/C ratio.

### Setup
- 30 min de código (scraper + cache + add a snapshot)
- Cache 5-10 min (P/C cambia lento intraday)

### Reglas KB desbloqueadas
1. TR-Juan-NNN: P/C > 1.2 + RSI oversold → SELL_PUT más agresivo
2. TR-Juan-NNN: P/C < 0.5 + RSI overbought → SELL_CALL high confidence
3. TR-Juan-NNN: P/C extremo (>1.5 o <0.4) → reduce position size (régimen tail)
4. TR-Juan-NNN: P/C diverge de VIX → señal de mispricing

### ROI estimado
🟢 ALTO. Free, fácil, complementa VIX, desbloquea contrarian setups.

---

## #2 — FRED Macro Calendar (RECOMENDADO #2)

### Qué es
Federal Reserve Economic Data — API oficial con eventos macro: FOMC dates, CPI release, NFP, GDP, etc.

### Por qué importa
- Sprint snapshot actual tiene `days_to_next_macro` = "unknown - Juan to confirm" en cases SILVER
- Pre-FOMC: vol crush en últimas horas → setup PUTs especiales
- Post-FOMC: vol expansion → wait until smoke clears
- Pre-CPI: gap risk overnight
- NFP Fridays: morning chop antes de decisión direccional

### Cómo obtenerlo
```python
# FRED API gratis con API key (registrarse, instantáneo)
import urllib.request, json
FRED_KEY = "tu_key"
URL = f"https://api.stlouisfed.org/fred/releases?api_key={FRED_KEY}&file_type=json"

# Para next FOMC dates:
URL_FOMC = f"https://api.stlouisfed.org/fred/release/dates?release_id=101&api_key={FRED_KEY}&file_type=json"
```

### Setup
- 1h: API key + script que cachea release dates + agrega `days_to_next_macro` real al snapshot
- Cache diario (refresh once per day)

### Reglas KB desbloqueadas
1. TR-Juan-NNN: days_to_FOMC ≤ 2 → reduce position size 50%
2. TR-Juan-NNN: días post-FOMC < 1 → no operar primeras 2h (vol settle)
3. TR-Juan-NNN: days_to_CPI ≤ 1 → no abrir new spreads, hold existing
4. TR-Juan-NNN: NFP day pre-open → cap confidence 6/10 hasta 10:30 ET
5. TR-Juan-NNN: día earnings macro (FOMC + CPI + NFP) → modo defensivo
6. TR-Juan-NNN: días "quietos" (>7d hasta próximo macro) → setups normales
7. TR-Juan-NNN: pre-3-day weekend → close 0DTE Thursday
8. TR-Juan-NNN: post-3-day weekend Tuesday → vol gap risk

### ROI estimado
🟢 ALTO. Hoy el bot vuela ciego sobre macro context. Esto es uno de los gaps más grandes del snapshot.

---

## #3 — CBOE VIX Term Structure (RECOMENDADO #3)

### Qué es
Curva de futuros del VIX: VX1, VX2, VX3, VX4. Cuando VX1 > VX2 (backwardation) = stress. Cuando VX1 < VX2 (contango) = calma.

### Por qué importa
- **Contango stable = theta harvest paradise** — toda la curva alcista lenta
- **Backwardation = stress mode** — pausa o reduce size
- **Curva pendiente positiva fuerte > +5%** = complacency, esperar normalización
- **Curva flat** = transición, alta incertidumbre

### Cómo obtenerlo
```python
# CBOE CSV gratis
URL = "https://www.cboe.com/us/futures/market_statistics/historical_data/"
# O Yahoo tickers: ^VIX, VX=F (front month), VX1, VX2 etc.

import urllib.request, csv
# Daily download
URL_VIX = "https://query1.finance.yahoo.com/v7/finance/download/^VIX?interval=1d&events=history"
URL_VX = "https://query1.finance.yahoo.com/v7/finance/download/^VIX9D?interval=1d&events=history"
# Calcular VX9D / VIX ratio → indicador short-term stress
```

### Setup
- 1h: scraper o Yahoo download + ratio calc + add a snapshot
- Cache 5 min intraday

### Reglas KB desbloqueadas
1. TR-Juan-NNN: contango fuerte (VX2/VX1 > 1.05) → high confidence en SELL premium
2. TR-Juan-NNN: backwardation (VX1 > VX2) → defensive, reduce size 50%
3. TR-Juan-NNN: VX9D / VIX > 1.10 → stress short-term, wait
4. TR-Juan-NNN: VX9D / VIX < 0.85 → calm regime, theta optimal
5. TR-Juan-NNN: term structure flattening rápido → transición, cap confidence

### ROI estimado
🟢 ALTO. La term structure es uno de los predictores históricos más sólidos de vol regime.

---

## #4 — Yahoo Finance unofficial API (Considerar)

### Qué es
`query1.finance.yahoo.com` y `query2.finance.yahoo.com` — endpoints públicos no documentados pero estables hace 10+ años.

### Por qué útil
- Sector ETFs (XLK, XLF, XLV, XLE, etc.) para sector rotation
- Cross-asset (DXY, TLT, GLD, BTC) para correlaciones
- VIX9D, VIX (ya cubierto en #3)

### Setup
- 1h: wrapper Python con cache
- Endpoint: `/v8/finance/chart/{ticker}?range=1d&interval=5m`

### Reglas KB desbloqueadas
- Sector rotation strength (XLK / SPY ratio momentum)
- Risk-on/risk-off (TLT inverse + DXY)

### ROI estimado
🟡 MEDIO. Útil pero overlap con sector data ya en snapshot post Sprint 12.

---

## #5 — AlphaVantage (Considerar para news sentiment)

### Qué es
API de market data con free tier 25 calls/día.

### Por qué útil
- Endpoint `NEWS_SENTIMENT` con score cuantificado por noticia
- Útil para "noise level" del día

### Setup
- 30 min: API key + cache aggressive (25 calls = solo 1 vez/hora)

### Reglas KB desbloqueadas
- Sentiment extremo positivo intraday → contrarian SELL_CALL
- News overload (>10 articles trending) → defensive mode

### ROI estimado
🟡 MEDIO. Free tier muy limitado (25/día = ~1/hora). Útil pero no escalable.

---

## #6 — Fear & Greed Index (Considerar)

### Qué es
Índice CNN Money que agrega 7 metrics: VIX, put/call ratio, market momentum, etc. 0-100 scale.

### Por qué útil
- Single-number summary del sentiment
- Histórico publicado, scraping fácil

### Cómo
```python
URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
# JSON con histórico + current value
```

### Setup
- 30 min

### Reglas KB desbloqueadas
1. TR-Juan-NNN: F&G > 80 (extreme greed) → contrarian SELL_CALL
2. TR-Juan-NNN: F&G < 20 (extreme fear) → contrarian SELL_PUT high confidence
3. TR-Juan-NNN: F&G transición rápida (Δ > 15 en 1 sem) → vol expansion próxima

### ROI estimado
🟡 MEDIO. Redundante parcial con VIX + P/C ratio (componentes del F&G). Pero útil como agregado.

---

## #7 — Polygon free tier (NO PRIORITARIO)

### Qué es
Market data API. Free tier: 5 calls/min, end-of-day delay.

### Por qué NO prioritario
- Para SPY ya tenés Schwab REST polling (Sprint 5 Fix B) sin limits
- Free tier de Polygon es muy limitado
- Mejor pagar plan starter ($30/mes) si querés más data

### ROI
🟡 BAJO. Redundante.

---

## #8 — Tradier sandbox (NO PRIORITARIO)

### Qué es
Broker API con sandbox gratis. Tiene options chains, quotes, news.

### Por qué NO prioritario
- Schwab REST ya cubre options chains
- Sandbox no es real-time
- Útil como backup pero no como primary

### ROI
🟡 BAJO.

---

## #9 — Quiver Quant (NO RELEVANTE)

### Qué es
Politicians trades, government contracts, Wikipedia traffic data.

### Por qué NO relevante
- Useful para single-stock plays (insiders)
- SPY ETF es ETF de 500 stocks → no single-stock insider tiene impact
- Free tier muy limitado

### ROI
🔴 BAJO para SPY trading.

---

## #10 — NewsAPI (NO PRIORITARIO)

### Qué es
News aggregator API. Free: 100 requests/día.

### Por qué NO prioritario
- Anthropic Claude ya razona muy bien sobre eventos macro cuando los menciones en prompt
- Sin parsing structured, news raw es ruido
- Mejor usar AlphaVantage sentiment (#5) que tiene scoring

### ROI
🔴 BAJO.

---

## Plan de integración recomendado

### Sprint OBS-Data-1 (Semana 1, ~3h)

**Integrar Top 3 al snapshot:**

1. **CBOE Put/Call ratio** (30 min) → field `put_call_ratio: float`
2. **FRED macro calendar** (1h) → fields `days_to_next_macro_event: int`, `next_macro_event_type: str` (FOMC/CPI/NFP/GDP)
3. **VIX term structure** (1h) → fields `vix9d: float`, `vx2_vx1_ratio: float`, `term_structure_state: str` (contango/backwardation/flat)

**Output esperado:**
- 3 nuevos fields en snapshot.py
- ~12-19 reglas KB candidatas (escribirlas en Sprint 18 KB v1.3 o sprint dedicado)
- 1 nuevo módulo `eolo-crop/llm_gate/external_data.py` con caches + fetchers

### Sprint OBS-Data-2 (Semana 3, ~2h)

**Si Top 3 dan resultados:**

4. Yahoo Finance sector rotation
5. Fear & Greed Index agregador

### Validación

Comparar A/B:
- Mes 1 sin data adicional (current)
- Mes 2 con Top 3 data adicional
- Metric: LLM-SPY Sharpe + confidence calibration + % decisive (no WAIT)

Si Sharpe sube ≥0.2 → continuar agregando. Si no → revisar prompt + KB para que aproveche la data nueva.

---

## Implementación técnica — patrón sugerido

```python
# eolo-crop/llm_gate/external_data.py

import time
from typing import Optional
import urllib.request, json
from loguru import logger

_PCR_CACHE: dict = {"ts": 0, "value": None}
_PCR_TTL = 600  # 10 min

def get_put_call_ratio() -> Optional[float]:
    """Returns CBOE total P/C ratio. Cached 10min. None on failure."""
    global _PCR_CACHE
    now = time.time()
    if _PCR_CACHE["value"] is not None and (now - _PCR_CACHE["ts"]) < _PCR_TTL:
        return _PCR_CACHE["value"]
    try:
        # Fetch logic
        req = urllib.request.Request(
            "https://cdn.cboe.com/api/global/delayed_quotes/symbol_summary.json?symbol=PCALL",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        # parse logic...
        value = ...
        _PCR_CACHE = {"ts": now, "value": value}
        return value
    except Exception as e:
        logger.warning(f"[external_data] PCR fetch failed: {e}")
        return _PCR_CACHE.get("value")  # stale fallback


# Idem para FRED y VIX term structure
```

Y en `snapshot.py`:

```python
from llm_gate.external_data import get_put_call_ratio, get_macro_days, get_vix_term_structure

snapshot["put_call_ratio"] = get_put_call_ratio()
snapshot["days_to_next_macro"] = get_macro_days()
ts = get_vix_term_structure()
snapshot["vix9d"] = ts.get("vix9d")
snapshot["term_structure_state"] = ts.get("state")
```

Todos defensive: si fetch falla, return None / stale value, log warning. Nunca crashea el bot.

---

## Costo / esfuerzo summary

| Sprint | Items | Esfuerzo | Reglas KB nuevas | Cost mensual |
|---|---|---|---|---|
| OBS-Data-1 | CBOE P/C + FRED + VIX term | 3h | 12-19 | $0 |
| OBS-Data-2 | Yahoo + F&G | 2h | 5-8 | $0 |
| Total | 5 sources | 5h | 17-27 | **$0** |

**Comparación con paid:** OptionMetrics ($XX/mo), Polygon premium ($30/mo), CBOE pro ($XX/mo). Free covers ~70% del use case del Theta Harvest.

---

## Lo que NO recomiendo agregar (al menos por ahora)

- ❌ Twitter/X sentiment scraping (rate limits hostiles, calidad cuestionable)
- ❌ Reddit WSB sentiment (correlación spuria con SPY)
- ❌ On-chain data (BTC/ETH) (irrelevante para SPY directo)
- ❌ Forex pairs (DXY ya cubre suficiente)
- ❌ Commodities específicos (oro/petróleo agregaron poco para SPY históricamente)
- ❌ Earnings whispers (SPY ETF agregado, no aplica)

---

**Generado:** 2026-05-31
**Author:** Claude (audit basado en arquitectura snapshot actual + KB v1.2)
**Para implementación:** Sprint OBS-Data-1 entre semana, post Sprint 18 si quiere también nuevas reglas.
