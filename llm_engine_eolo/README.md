# EOLO Crop LLM Engine

Servicio LLM que toma decisiones de trading de Theta Harvest basadas en el Knowledge Base de Juan.

## 📋 Estado del proyecto

- **Versión:** 0.1.0
- **Modo:** Paper Trading EXCLUSIVAMENTE
- **Modelo:** Claude Sonnet 4.6 (`claude-sonnet-4-5-20250929`)
- **KB cargado:** v0.9 — 6 casos + 43 reglas (2 axiomas, 1 prohibitiva, 6 maestras, 6 protocolo, 28 tácticas)

## 🏗️ Arquitectura

```
EOLO CROP (Cloud Run existente)
   │
   │ schwab-py → build MarketSnapshot
   │
   ▼
LLM ENGINE SERVICE (Cloud Run nuevo)
   │
   │ POST /decide
   │
   ▼
Claude Sonnet 4.6 ← KB Excel v0.9
   │
   │ JSON decision
   │
   ▼
Safety rails + Decision parser
   │
   ▼
Return to Eolo Crop → execute paper trade
```

## 📁 Estructura

```
llm_engine_eolo/
├── llm_engine/                       # Código del servicio
│   ├── __init__.py
│   ├── service.py                    # FastAPI app + endpoints
│   ├── kb_loader.py                  # Carga del Excel KB
│   ├── market_snapshot.py            # Pydantic model + formatter
│   ├── prompt_builder.py             # System + user prompts
│   ├── decision_parser.py            # JSON parsing + safety rails
│   └── market_data_collector.py      # Indicadores técnicos (referencia)
├── kb/
│   └── EOLO_ThetaHarvest_v0.9.xlsx   # Knowledge Base de Juan
├── tests/
│   └── test_llm_engine.py            # Unit tests
├── llm_client.py                     # Cliente para Eolo Crop
├── Dockerfile
├── requirements.txt
├── deploy.sh                         # Deploy a Cloud Run
└── .env.example
```

## 🚀 Quickstart

### 1. Setup local

```bash
git clone <este repo>
cd llm_engine_eolo
pip install -r requirements.txt
cp .env.example .env
# Editar .env con tu ANTHROPIC_API_KEY
```

### 2. Correr tests

```bash
python tests/test_llm_engine.py
```

Debería mostrar:
```
✅ KB loaded: {'total_rules': 43, ...}
✅ VIX spike override: [...]
✅ Low confidence override
✅ IC sequential accepted
✅ Markdown wrapper parsed correctly
✅ Prompts built: system=... chars, user=... chars
🎉 All tests passed!
```

### 3. Correr el servicio local

```bash
uvicorn llm_engine.service:app --reload --port 8080
```

Visitar `http://localhost:8080/docs` para Swagger UI.

### 4. Test manual con curl

```bash
curl http://localhost:8080/health

curl http://localhost:8080/kb_stats

# POST /decide requiere un MarketSnapshot completo
curl -X POST http://localhost:8080/decide \
  -H "Content-Type: application/json" \
  -d @example_snapshot.json
```

### 5. Deploy a Cloud Run

```bash
bash deploy.sh
```

### 6. Integrar a Eolo Crop

Copiar `llm_client.py` al repo de Eolo Crop. En tu loop principal:

```python
from llm_client import LLMEngineClient

client = LLMEngineClient(service_url=os.getenv("LLM_SERVICE_URL"))

# Cada iteración del bot:
snapshot = build_market_snapshot()  # función a implementar
decision = client.decide(snapshot)

if decision["verdict"] == "SELL_PUT" and decision["confidence"] >= 7:
    execute_paper_trade(decision)
```

## 📊 Endpoints

### `GET /health`
Health check. Sin autenticación.

```json
{
  "status": "healthy",
  "kb_loaded": true,
  "paper_trading_only": true,
  "model": "claude-sonnet-4-5-20250929"
}
```

### `GET /kb_stats`
Stats del KB.

```json
{
  "total_rules": 43,
  "rules_by_tier": {
    "AXIOMA": 2,
    "PROHIBITIVA": 1,
    "MAESTRA": 6,
    "PROTOCOLO": 6,
    "TACTICAL": 28
  },
  "total_cases": 6,
  "gold_cases": 0
}
```

### `POST /decide`
Endpoint principal. Recibe `MarketSnapshot`, retorna `Decision`.

**Request body (mínimo requerido):**
```json
{
  "timestamp": "2026-05-27T10:30:00-04:00",
  "ticker": "SPY",
  "price": 750.00,
  "open_price": 750.07,
  "high": 752.13,
  "low": 749.27,
  "prev_close": 750.49,
  "vix_level": 17.05,
  "pdh": 752.13,
  "pdl": 748.37,
  "pdc": 750.49,
  "rsi_2m": 50.8,
  "rsi_15m": 55.0,
  "rsi_daily": 70.0,
  "atr_2m": 0.342,
  "atr_15m": 0.55,
  "atr_daily": 2.30
}
```

**Response:**
```json
{
  "verdict": "IRON_CONDOR_SEQUENTIAL",
  "confidence": 8,
  "strikes": {"put_strike": 745.0, "call_strike": 755.0},
  "deltas": {"put_delta": 0.15, "call_delta": 0.15},
  "dte_target": 1,
  "main_reason": "VIX estable + RSI 40-60 + range-bound day. Setup ideal IC sequential.",
  "tacit_rules_applied": ["TR-Juan-031", "TR-Juan-044", "TR-Juan-047"],
  "abort_triggers": ["VIX velocity > 5%"],
  "profit_target_pct": 55,
  "stop_loss_conditions": ["Fib break with VIX + Volume + RSI triple confirmation"],
  "similar_case_used": "2026-05-27_SPY_counterfactual_006",
  "warnings": [],
  "safety_overrides": [],
  "meta": {
    "request_id": "req_...",
    "latency_ms": 2340,
    "model": "claude-sonnet-4-5-20250929"
  }
}
```

## 🛡️ Safety Rails

El parser aplica overrides automáticos:

1. **Confidence < 6** → WAIT
2. **VIX velocity > +5%** → WAIT con warning
3. **IRON_CONDOR directo** → IRON_CONDOR_SEQUENTIAL (TR-Juan-047)
4. **Strike > 5% OTM** → warning
5. **DTE > 4** → clamped a 1 DTE
6. **Profit target fuera de 50-60** → clamped al rango
7. **Strikes missing** en verdict SELL_* → WAIT
8. **Parsing error** → WAIT seguro

## 📈 Roadmap

### v0.1 (HOY) - MVP
- ✅ FastAPI service
- ✅ KB loading from Excel
- ✅ Claude integration
- ✅ Safety rails
- ✅ Tests pasando
- ✅ Deploy a Cloud Run

### v0.2 (próximas 2 semanas)
- [ ] Integración real con schwab-py en Eolo Crop
- [ ] 30+ trades en paper money loggeados
- [ ] Dashboard simple para revisar decisiones
- [ ] Iteración del prompt según fallos

### v0.3 (mes siguiente)
- [ ] RAG con embeddings (en lugar de keyword matching)
- [ ] Auto-update del KB con casos nuevos
- [ ] Comparación A/B con reglas viejas
- [ ] Win rate tracking

### v1.0 (3 meses)
- [ ] Move to production con safeguards
- [ ] Multi-ticker support (QQQ, IWM)
- [ ] Layered architecture (Haiku pre-filter + Sonnet decisions)

## 🐛 Troubleshooting

**KB no carga:**
- Verificar `KB_PATH` está bien
- Verificar el Excel está incluido en el Docker image

**LLM responde con errores:**
- Check `ANTHROPIC_API_KEY` está en Secret Manager
- Check quota de Anthropic
- Ver logs en Cloud Run

**Decision parsing fails:**
- LLM puede estar devolviendo markdown wrapped JSON - el parser lo maneja
- Si confidence es 0 y verdict es WAIT → algo falló, ver logs

## 💰 Costos estimados

- **Cloud Run:** ~$5-10/mo (mostly idle, scales to 0)
- **Anthropic API:** ~$15-40/mo (Sonnet 4.6 @ $3/MTok input, $15/MTok output, ~1k decisions/day)
- **Total:** ~$20-50/mo

## 📝 Logs

Cada decisión se loggea con:
- `request_id`
- timestamp completo
- market snapshot
- raw LLM output (primeros 500 chars)
- decision parseada
- safety overrides aplicados
- latencias

Para grep:
```bash
gcloud logging read 'resource.labels.service_name="llm-engine-service" AND textPayload:"DECISION_LOG"' --limit 50
```
