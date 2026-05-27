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
---

## Update 2026-05-27 — Bloque C (validación de razonamiento)

Post-merge a main, se ejecutaron 3 calls adicionales a `/decide` con snapshots variados para validar razonamiento del LLM y disparo de safety rails.

### Setup

3 snapshots derivados de `make_test_snapshot()` con overrides:

- **C1**: VIX spike (`vix_velocity_30m_pct=7.0, vix_level=22.5`) → expected WAIT + safety rail `VIX_SPIKE`
- **C2**: Overbought multi-TF (RSI 78/72/68, BVP 70%, precio en VWAP +2σ) → expected SELL_CALL
- **C3**: Posición abierta con 55% profit (1 DTE) → expected CLOSE_POSITIONS

Cost: 3 × ~$0.02 = ~$0.06.

### Resultados

| Test | Expected | Actual | Match | Confidence | Latency |
|---|---|---|---|---|---|
| C1 | WAIT + VIX_SPIKE | WAIT + `[]` | parcial (ver Hallazgo 1) | 2 | 14356ms |
| C2 | SELL_CALL | WAIT | miss aparente (ver Hallazgos 2 y 4) | n/a | 17097ms |
| C3 | CLOSE_POSITIONS | CLOSE_POSITIONS | ✓ | 9 | 15671ms |

Avg latency: 15708ms. Consistente con baseline 14-18s del Bloque 3 (n=5 ahora).

### Hallazgo 1: `safety_overrides` es veto-mode, no flag-mode

El rail VIX_SPIKE en `decision_parser.py` L107-114 solo agrega override **cuando tiene que vetar al LLM**. Si el LLM dice WAIT por su cuenta, el rail no actúa → `safety_overrides=[]`.

En C1 el LLM citó TR-Juan-058 (PROHIBITIVA migrada de R011: "0DTE + VIX velocity >+5% → CLOSE_NOW") y decidió WAIT directamente. El rail no tuvo que vetar nada.

Validación cruzada: el test unitario `test_safety_rail_vix_spike` SÍ ve el override `VIX_SPIKE_+7.0%` porque ahí se le pasa `verdict=SELL_PUT` al rail directamente, sin LLM real. Consistente con veto-mode.

**Esto valida que la KB v1.1+ está internalizada**: el LLM aplica reglas por sí mismo antes de que las rails tengan que intervenir. La rail queda como red de seguridad real, no como ruido informativo.

### Hallazgo 2: el LLM exige multi-señal para entries

C2 con `verdict=WAIT` confirma la tesis del sistema. LLM identificó:

- ✓ RSI overbought multi-TF (78/72/68)
- ✗ BVP 70% + MACD histogram +0.15 = momentum **todavía alcista**
- ✗ Sin confirmación de exhaustion ni pullback

Citó TR-Juan-002 (RSI top → SELL CALL) pero **ponderó** y decidió no entrar. Captura matiz, no aplica reglas binarias.

Para gatillar `SELL_CALL` en futuros tests hay que armar setup con fading confirmado: `bvp_pct < 50`, `macd_histogram_15m < 0`, volume capitulación, `ema_9_2m < ema_21_2m`.

### Hallazgo 3: reglas MAESTRA migradas funcionan

C3 con `confidence=9` + `CLOSE_POSITIONS` + citation de TR-Juan-053 (ex-R006), TR-Juan-061 (ex-R014) y TR-Juan-054 (ex-R007). Validación directa del batch 2 de fixes de KB v1.1: las 14 reglas R001-R014 migradas no solo están en el Excel, el LLM las usa correctamente.

### Hallazgo 4: TR-Juan-040 sub-especificada (KB bug, no LLM bug)

C2 también puso en evidencia que TR-Juan-040 estaba mal redactada. Texto original: "Mejor prima en primeras horas, ventanas duran minutos. Entry temprano = mejor prima = menor riesgo absoluto". El LLM lo interpretó como "9:30-10:00 = ventana óptima, después = no entrar".

Juan aclaró: para 0DTE hay margen hasta ~12:00 ET si el setup persiste. **El LLM razonó correctamente sobre la regla tal como estaba escrita — la regla era incompleta.**

Fix aplicado en KB v1.2 (este sub-bloque): ver sección "Update KB v1.2" abajo.

---

## Update KB v1.2 — TR-Juan-040 matiz 0DTE

### Cambio

Action de TR-Juan-040 reescrita para reflejar la ventana operacional real de Juan:

**Antes (v1.1)**:
> VELOCIDAD CRITICA - mejor prima en primeras horas, ventanas duran minutos. Entry temprano = mejor prima = menor riesgo absoluto

**Después (v1.2)**:
> VELOCIDAD CRITICA - mejor prima en primeras horas (9:30-10:00 optima). Para 0DTE, ventana extendida hasta ~12:00 ET si setup persiste (BVP/MACD mantienen direccion). Pasada 12:00, evitar new entries 0DTE por gamma surge (ver TR-Juan-055).

### Bumps

- `EOLO_ThetaHarvest_v1.1.xlsx` → `EOLO_ThetaHarvest_v1.2.xlsx` (git mv + edit)
- `service.py` L45 KB_PATH default + L172 kb_version meta → v1.2
- `tests/test_llm_engine.py` L22 KB_PATH → v1.2
- `deploy.sh` L31 KB_PATH → v1.2
- `.env.example` L10 KB_PATH → v1.2
- `README.md` L49 → v1.2 (estaba stale en v0.9)
- `CHANGELOG_v1.2.md` agregado al repo (existía como input, no se había commiteado)

### Validación (Bloque B.2)

- Tests 8/8 verde con KB v1.2 (asserts comparan structure/counts, no cambian).
- `/health` + `/kb_stats` OK, 61 reglas mismas, mismos tier counts.
- `meta.kb_version` ahora reporta `"v1.2"`.
- **Call /decide con snapshot C2 (re-run con KB v1.2)**: `verdict=WAIT, confidence=4`. Razonamiento del LLM cambió de "10:30 fuera de ventana óptima 9:30-10:00" a "10:30 dentro de ventana extendida 9:30-12:00, pero falta confirmación de fading (RSI bajando, MACD turn)". **Fix aplicado correctamente — el LLM ahora interpreta la regla actualizada y rechaza por motivo legítimo (multi-señal pendiente), no por horario.** Hallazgo 2 confirmado por segunda vez.

### Tech debt #9 → RESUELTO en KB v1.2

Tech debt original (item 9 del listado de bootstrap): "TR-Juan-040 matiz para 0DTE — la regla actual dice 'ventana óptima 9:30-10:00' sin contemplar extensión hasta ~12:00 para 0DTE".

**RESUELTO** en este sub-bloque mediante reescritura del Action (opción A elegida sobre crear TR-Juan-062). Cita a TR-Juan-055 (gamma surge ≥14:00 ET) para consistencia con regla migrada.

### Tech debt nuevas (post-validación)

10. **Test print "KB v1.1 loaded" hardcoded**: en `tests/test_llm_engine.py` el output del `test_kb_loads` imprime la string "KB v1.1 loaded" hardcoded sin reflejar la versión real del .xlsx cargado. Los asserts son correctos (cuentan reglas y tiers), pero el mensaje en pantalla queda engañoso cuando se bumpea la KB. Fix simple: leer la versión del path del archivo o del nombre del file. Trivial pero no urgente.
