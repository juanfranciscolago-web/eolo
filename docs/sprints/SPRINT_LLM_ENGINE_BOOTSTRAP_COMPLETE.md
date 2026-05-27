# Sprint — LLM Engine Bootstrap (v1.2)

- **Fecha cierre:** 2026-05-27
- **Autor:** Juan + Claude
- **Status:** DONE
- **Branch:** `feat/llm-engine-bootstrap`

## Scope

Bootstrap del LLM Engine v1.2 como microservicio FastAPI independiente, con KB v1.1
cargada al startup, endpoint `/decide` que llama a Anthropic Sonnet 4.6 y retorna
decisiones estructuradas con safety rails. Objetivo del sprint: dejar el servicio
en un estado donde un call real end-to-end (snapshot → prompts → LLM → JSON →
safety → response) funciona sin bugs, listo para integrarse con Eolo Crop en un
sprint posterior.

## Bloques completados

### Bloque 0+1 — Setup entorno + bootstrap código
- Python 3.11 instalado vía brew (3.14 default rompe numpy 1.26).
- venv creado en `llm_engine_eolo/.venv`.
- Paquete v1.2 desempaquetado desde handoff tar.gz.
- Commit: `10ff244 chore(llm-engine): bootstrap v1.2 from handoff tar.gz`.

### Bloque 2 — Tests + smoke FastAPI
- Suite local: **8/8 verde** (`pytest tests/test_llm_engine.py`).
- Smoke endpoints sin LLM:
  - `GET /health` → healthy, kb_loaded=true
  - `GET /kb_stats` → 61 reglas, 6 cases
  - `GET /docs` → swagger UI carga
  - `GET /openapi.json` → schema válido
  - `POST /decide` con `{}` → 422 listando 16 campos required missing (validación Pydantic OK)
- Commit: `94ca36f fix(llm-engine): pin httpx<0.28 to fix anthropic 0.39.0 proxies bug`.

### Bloque 3 — Call real a /decide con Anthropic Sonnet 4.6
- 2 calls reales a Sonnet 4.6 con `make_test_snapshot()` (SPY @ 750, VIX 17.05, RSI Daily 70).
- Ambos retornaron `verdict=WAIT`, `confidence=5`, `safety_overrides=[]`, con 7-8 reglas
  citadas (overlap fuerte: TR-Juan-022/036/037/040/042/043/051).
- KB v1.1 distribuída en 6 tiers: AXIOMA(2), PROHIBITIVA(5), PROTOCOLO(6), MAESTRA(11),
  TACTICAL(24), TACTICAL_PLUS(13).
- Las reglas citadas cubren AXIOMA + PROTOCOLO + MAESTRA + TACTICAL_PLUS (migración tier OK).
- `request_id`, `kb_version=v1.1`, `model=claude-sonnet-4-5-20250929` propagados en `meta`.

## Bugs encontrados durante el sprint

| # | Bug | Resolución |
|---|---|---|
| 1 | 7 hallazgos en KB v0.9 original | Resueltos por Juan en 2 batches (KB v1.1 + paquete v1.2) ANTES del bootstrap. Ver `CHANGELOG_v1.2.md` |
| 2 | `httpx>=0.28` incompatible con `anthropic==0.39.0` (proxies kwarg removido) | `httpx<0.28` pineado — commit 94ca36f |
| 3 | Python 3.14 (default brew) no compila numpy 1.26 | Instalado Python 3.11 via brew, venv apunta a ese binary |
| 4 | `requests` no estaba en venv para script de test ad-hoc | Reescrito el script con `httpx` (ya instalado como dep de anthropic) |

## Tests y smoke

- `pytest`: 8/8 verde.
- FastAPI smoke: 5 endpoints (`/health`, `/kb_stats`, `/docs`, `/openapi.json`,
  `/decide` con `{}` → 422).
- Call real `/decide` ×2: HTTP 200, decisiones coherentes y consistentes.

## KB v1.1 stats

- 61 reglas, 6 cases (0 gold cases todavía).
- Tier distribution: AXIOMA(2), PROHIBITIVA(5), PROTOCOLO(6), MAESTRA(11),
  TACTICAL(24), TACTICAL_PLUS(13).
- Todas las rule references en cases validadas al cargar.

## Latencia baseline

| Call | HTTP latency | meta.latency_ms |
|---|---|---|
| 1 | 14437ms | 14425ms |
| 2 | 18399ms | 18382ms |

Ambos > 10s descartan la hipótesis de cold-start. La latencia es **estructural**
(prompt grande con 61 reglas + 6 cases + output JSON detallado). Para un loop de
2-5min sigue siendo viable, pero deja poco margen y motiva la consideración de un
approach por capas en v0.3 (ver tech debt).

## Tech debt (sin resolver, anotada para sprints futuros)

1. **Lockfile reproducible**: `requirements.txt` tiene transitive deps abiertas.
   Mover a `uv` o `pip-tools` para builds determinísticos.
2. **anthropic SDK 0.39.0 viejo**: evaluar bump a `>=0.40` cuando haya batería para
   revisar breaking changes.
3. **Inconsistencia pydantic vs safety rails**: campo `profit_target_pct` declarado
   `ge=40,le=80` en el modelo pero clampeado a 50-60 en safety rails. Decidir si la
   validación amplia es intencional o tightenar a 50-60 también.
4. **`kb_version` hardcoded** como `"v1.1"` en `service.py` — debería leerse de la
   KB misma o de config para evitar drift cuando se actualice.
5. **Import muerto**: `from pydantic import validator` en algún módulo (no usado tras
   migración a `field_validator`).
6. **Latencia 15-18s estructural**: considerar layered approach en v0.3 — Haiku
   pre-filter para detectar setups WAIT obvios (overbought + neutral) y reservar
   Sonnet solo para entries con confianza > umbral. Reduciría latencia media y costo.
7. **Margen de timeout Cloud Run**: latencia 15-18s estructural + Cloud Run timeout
   default 30s = solo ~12s de margen. Si Sonnet 4.6 degrada o el prompt crece (más
   casos GOLD), riesgo de 504. Considerar `--timeout 45s` o `60s` en `deploy.sh`,
   o mover el budget al cliente Eolo Crop.

## Salvavidas (rollback)

Si el bootstrap se considera no viable y hay que revertir todo:

```bash
git checkout main
git branch -D feat/llm-engine-bootstrap
# .env queda en disco (gitignored) — borrar manualmente si se quiere:
rm -f ~/PycharmProjects/eolo/llm_engine_eolo/.env
```

El estado pre-LLM queda restaurado sin afectar otras branches ni el repo remoto
(el branch en origin sigue existiendo para post-mortem pero deja de estar checked out).

## Next — Bloque 4

Integración del LLM Engine con **Eolo Crop**:
- Implementar `build_market_snapshot_from_schwab()` en `eolo-crop/` que produzca el
  `MarketSnapshot` pydantic en el shape exacto que `/decide` espera.
  - Los 16 campos required (no opcionales) del MarketSnapshot pydantic son:
    `timestamp`, `price`, `open_price`, `high`, `low`, `prev_close`, `vix_level`,
    `pdh`, `pdl`, `pdc`, `rsi_2m`, `rsi_15m`, `rsi_daily`, `atr_2m`, `atr_15m`,
    `atr_daily`. El resto (~40 campos: Fibonacci, VWAP, EMAs, MACD, BVP/SVP, etc.)
    son opcionales con default 0 — el LLM tiene menos contexto si faltan pero el
    servicio no rompe.
- Wiring del HTTP client (httpx, mismo timeout 30s) para llamar al engine desde el
  loop principal de Eolo Crop.
- Coordinación necesaria con `S3.1-A` en flight (ver memoria de sprint progress).
  Posiblemente vía `git worktree` para no bloquear esa branch.

## Commits del sprint en `feat/llm-engine-bootstrap`

```
94ca36f fix(llm-engine): pin httpx<0.28 to fix anthropic 0.39.0 proxies bug
10ff244 chore(llm-engine): bootstrap v1.2 from handoff tar.gz
```

Más el commit de este doc.
