# FASE 2 CLEANUP — Plan Ejecutable

**Generado:** 11-may-2026 ~17:50 ART
**Producido por:** F2.1 identificación exhaustiva (read-only)
**Branch:** `docs/notas-v3-8`
**Objetivo:** Apagar Claude brain en CROP, mantener theta_harvest 100% funcional

---

## TL;DR

- **1 solo archivo importa Claude** en `eolo-crop/`: `crop_main.py`
- **Carpeta `eolo-crop/claude/`** tiene 2 archivos, 798 líneas, archivar
- **`AutoRouter`** NO lo usa theta → archivar también
- **`OptionsTrader`** NO depende de Claude, PERO tiene tagging Claude-biased (Bug AC.1)
- **2 de 3 call sites de `execute_decision`** son theta_harvest → MANTENER
- **1 call site** es Claude → eliminar
- **`smoke_test.py`** también importa Claude pero es script de test, no producción

---

## 1. Imports a eliminar en `crop_main.py`

| Línea | Código | Acción |
|---|---|---|
| 55 | `from claude.options_brain  import OptionsBrain` | **ELIMINAR** |
| 56 | `from claude.claude_bot     import ClaudeBotEngine` | **ELIMINAR** |
| 84 | `from eolo_common.routing import AutoRouter as _AutoRouter` | **ELIMINAR** |

---

## 2. Instanciaciones a eliminar (`crop_main.py`)

| Línea | Código | Acción |
|---|---|---|
| 316 | `self.brain         = OptionsBrain()` | **ELIMINAR** |
| 319 | `self._auto_router  = _AutoRouter(bot_id="crop", update_interval_min=30)` | **ELIMINAR** |
| 325 | `self.claude_bot = ClaudeBotEngine(paper_mode=True)` | **ELIMINAR** |
| 328 | `self.claude_bot = None` (fallback) | **ELIMINAR** (el if/else completo) |

---

## 3. Estado interno `_claude_bot_*` a eliminar

Bloque ~L473-482 (estado de Claude Bot):

```python
473: self._claude_bot_interval:   int  = 60
474: self._claude_bot_last_tick:  float = 0.0
475: self._claude_bot_decision:   dict | None = None
476: self._claude_bot_history:    list[dict]  = []
478: self._claude_bot_budget:     float = 500.0
482: self._claude_bot_positions:  list[dict] = []
```

**TODOS ELIMINAR** (10 líneas aprox con comments).

También L362: `self._claude_gate_enabled: bool = os.environ.get("CLAUDE_GATE", "1") != "0"` → **ELIMINAR** (env var sigue seteado en Cloud Run service template, sin efecto post-cleanup).

---

## 4. Loops async en `gather_tasks` (L570-585)

Ver L574:
```python
self._claude_bot_loop(),
```

**ELIMINAR de la lista** que se pasa a `asyncio.gather(*gather_tasks)`.

---

## 5. AutoRouter uses (`crop_main.py`)

| Línea | Código | Acción |
|---|---|---|
| 757 | `if self._auto_router.should_update():` | **ELIMINAR** bloque entero (probablemente ~10 líneas) |
| 763 | `_toggles = self._auto_router.update(...)` | **ELIMINAR** |

Theta no usa AutoRouter → safe to remove.

---

## 6. Brain.analyze() call (`crop_main.py`)

L888:
```python
decision = await self.brain.analyze(
```

Dentro de `_run_analysis_cycle()` (L799+). **ELIMINAR** el bloque que va desde el setup del prompt hasta la llamada a `_execute_decision(decision)` que sigue.

⚠️ **CUIDADO**: `_run_analysis_cycle` es llamado en gather_tasks (L795: `await self._run_analysis_cycle(ticker, chain)`). Si la función queda vacía después del cleanup, decidir si eliminar la función entera o dejarla como no-op.

---

## 7. Definiciones de loops/helpers Claude

| Función | Línea | Acción |
|---|---|---|
| `async def _run_analysis_cycle(self, ticker, chain)` | 799 | **REVISAR**: probablemente eliminar (era Claude-only). Verificar que no contenga setup de chains o macro feeds compartidos |
| `async def _execute_decision(self, decision)` | 1584 | **ELIMINAR** (helper Claude. Las llamadas theta van directo via `self.trader.execute_decision`) |
| `async def _claude_bot_loop(self)` | 1832 | **ELIMINAR** completo (~30-60 líneas) |
| `async def _claude_bot_tick(self)` | ~1854 (callee) | **ELIMINAR** función entera + sus helpers |
| `_build_claude_snapshot` (probable, ~L1995) | "Arma el dict de entrada para ClaudeBotEngine.decide()" | **ELIMINAR** |

---

## 8. Call sites de `execute_decision`

| Línea | Contexto | Acción |
|---|---|---|
| **1285** | `for signal in signals: decision = signal.to_decision(); order_id = await self.trader.execute_decision(decision)` | **MANTENER** (theta_harvest scan → open spread) |
| **1478** | `close_decision = {"action": "CLOSE_SPREAD", ...}; order_id = await self.trader.execute_decision(close_decision)` | **MANTENER** (theta_harvest monitor → close spread) |
| **1602** | `order_id = await self.trader.execute_decision(decision)` dentro de `_execute_decision()` helper | **ELIMINAR** (junto con el helper completo L1584-1610) |

---

## 9. Referencias dispersas a Claude/brain

| Línea | Contexto | Acción |
|---|---|---|
| 523 | comment: "al siguiente éxito de OptionsBrain (que resetea el contador)" | **Actualizar comment** o eliminar |
| 868 | `# 5b. Gate de costos Claude — solo llamar a OptionsBrain si hay algo` | **ELIMINAR** comment + bloque siguiente |
| 899 | `logger.error(f"[CROP] Error en OptionsBrain para {ticker}: {e}")` | **ELIMINAR** (parte del bloque brain.analyze) |
| 1861 | comment "cuenta hacia el mismo umbral compartido con OptionsBrain" | **Actualizar** |
| 1953 | comment "Persistir en Firestore (namespace separado de OptionsBrain)" | **Actualizar** |
| 2324 | comment "si trading_hours_enabled=False, las posiciones de OptionsBrain" | **Actualizar** |
| 2611 | `for obj in (self.brain, self.trader):` | **CAMBIAR** a `for obj in (self.trader,):` |
| 2751-2773 | `_poll_settings` handling de `claude_bot_*` config | **ELIMINAR** todo el bloque |
| 2831-2835 | `if hasattr(self, "brain") and hasattr(self.brain, "set_risk_defaults"):` | **ELIMINAR** bloque (defaults SL/TP eran para Claude) |
| 2951 | `"claude_calls": self.brain.call_count` | **ELIMINAR** field del state |
| 2972-2994 | state writer dict de `claude_bot` (~20 líneas) | **ELIMINAR** todo el bloque |

---

## 10. Carpeta `eolo-crop/claude/` — archivar

```
eolo-crop/claude/
├── claude_bot.py     (404 líneas, 17,382 bytes)
└── options_brain.py  (394 líneas, 15,911 bytes)
```

**Acción**: mover a `eolo-crop/_archive_claude/` con `git mv` para preservar historia.

```bash
mkdir -p eolo-crop/_archive_claude
git mv eolo-crop/claude/claude_bot.py eolo-crop/_archive_claude/
git mv eolo-crop/claude/options_brain.py eolo-crop/_archive_claude/
git mv eolo-crop/claude/__init__.py eolo-crop/_archive_claude/ 2>/dev/null
rmdir eolo-crop/claude 2>/dev/null
```

---

## 11. `eolo-crop/eolo_common/routing.py` — verificar

**Hallazgo**: theta_harvest NO usa AutoRouter (grep confirmado vacío en `theta_harvest/`).
**Único user**: `crop_main.py` L84, 319, 757, 763.

Post-cleanup → archivar:
```bash
git mv eolo-crop/eolo_common/routing.py eolo-crop/_archive_claude/routing.py
```

(Verificar primero que no haya otros archivos en `eolo_common/` que importen routing internamente)

---

## 12. ⚠️ BUG OptionsTrader L1098, L1111 — Claude-biased tagging

```python
# eolo-crop/execution/options_trader.py:1098
"""
Conveniencia: recibe el dict de decisión de OptionsBrain  ← DOCSTRING desactualizado
"""

# L1111
_raw_strat = mp_type if mp_type else f"claude_{confidence.lower()}" if confidence else "claude_bot"
```

**Problema**: cuando theta_harvest llama `execute_decision`, el executor tag la posición como `"claude_bot"` (porque no manda `confidence`). Esto explica algunos de los 45 untagged y mislabeling.

**Acción**: actualizar L1098 docstring y L1111 logic para soportar theta_harvest sin asumir Claude:
```python
_raw_strat = (
    mp_type if mp_type
    else f"claude_{confidence.lower()}" if confidence
    else decision.get("strategy", "theta_harvest")  # NEW: respect strategy field
)
```

**NO crítico** para el cleanup, pero arregla Bug AC.1 que mencionamos antes.

---

## 13. Validación post-cleanup

### Checks de sintaxis local
```bash
cd ~/PycharmProjects/eolo
python3 -c "import ast; ast.parse(open('eolo-crop/crop_main.py').read()); print('✅ Sintaxis OK')"
```

### Verificar imports residuales
```bash
grep -n "from claude\|import claude\|OptionsBrain\|ClaudeBotEngine\|AutoRouter\|self\.brain\|self\.claude_bot\|self\._auto_router" eolo-crop/crop_main.py
# Debe devolver 0 matches post-cleanup
```

### Smoke test local
```bash
# Stub flask + crop_main si no instalado
python3 -c "
import sys, types
sys.modules['flask'] = types.ModuleType('flask')
sys.path.insert(0, 'eolo-crop')
import main  # debe importar sin error de ModuleNotFoundError de claude
"
```

### Tests funcionales mínimos
1. `_run_theta_harvest()` corre sin error (mock chain)
2. `_theta_monitor_loop()` reconoce close conditions
3. `self.trader.execute_decision()` se llama desde theta paths
4. `/api/state.theta.*` se popula correctamente
5. `/api/state.claude` y `/api/state.claude_bot` ya NO existen
6. Dashboard render no crashea por falta de campos claude

### Deploy strategy
1. **Canary tag primero**: `update-traffic --update-tags=canary-theta-only=NEW_REV`
2. Smoke test canary URL
3. Logs canary 5 min sin errores
4. Verificar: NO `[CLAUDE_BOT_V2] Loop iniciado` en canary logs
5. **Promote**: `update-traffic --to-revisions=NEW_REV=100`

---

## 14. Resumen del scope total

| Categoría | Líneas a eliminar |
|---|---|
| Imports (3 lines) | 3 |
| Instanciaciones | 3-5 |
| Estado interno `_claude_bot_*` | ~10 |
| Loops async setup | 1-2 |
| AutoRouter uses (757, 763 + bloque) | ~15 |
| `brain.analyze()` + setup | ~30-50 |
| `_execute_decision()` helper | ~30 |
| `_claude_bot_loop()` | ~60 |
| `_claude_bot_tick()` + helpers | ~100 |
| `_build_claude_snapshot` | ~50 |
| `_poll_settings` Claude config (~L2751-2773) | ~25 |
| Brain set_risk_defaults (~L2831-2835) | ~5 |
| State writer Claude block (~L2972-2994) | ~25 |
| Misc comments/refs (10+) | ~10 |
| **Subtotal código crop_main.py** | **~370-400 líneas** |
| Carpeta `eolo-crop/claude/` (archivada, no borrada) | 798 líneas |
| Carpeta `eolo-crop/eolo_common/routing.py` (archivada) | ? líneas |
| **TOTAL bajado** | **~1200 líneas de código activo** |

**crop_main.py**: pasa de **3729 líneas → ~3329 líneas** (-11%).
**Reducción no tan drástica como esperábamos** porque el módulo theta_harvest local es grande (1500+ líneas).

---

## 15. Riesgos identificados (consolidado)

| # | Riesgo | Severidad | Mitigación |
|---|---|---|---|
| R1 | `_run_analysis_cycle` queda vacío o roto post-cleanup (mezcla logic) | ALTA | Revisar línea a línea su body, decidir si eliminar la función entera |
| R2 | `_poll_settings` se rompe si Firestore tiene campos Claude que el bot ya no maneja | MEDIA | Hacer el handling silencioso (ignore unknown keys) o eliminar campos del config |
| R3 | OptionsTrader.L1111 sigue tageando como "claude_bot" post-cleanup | BAJA | Fix incidental — incluir en cleanup |
| R4 | Imports residuales no detectados (rare, pero posible) | BAJA | Verificación post-cleanup con grep |
| R5 | smoke_test.py rompe (importa OptionsBrain) | BAJA | Update test o archive |
| R6 | Logs históricos referencian Claude → consultas analytics rompen | BAJA | Documentar que pre-cleanup era multi-strategy |
| R7 | gather_tasks await falla si lista queda con elementos None | BAJA | Verificar que la lista quede limpia post-edit |

---

## 16. Plan ejecutable mañana

### F2.2 — Imports + instanciaciones (1h)
1. Backup local: `cp eolo-crop/crop_main.py /tmp/crop_main_pre_F2.py`
2. Edit lines: 55, 56, 84, 316, 319, 325-328
3. Edit lines: 362, 473-482
4. Verify: `grep "OptionsBrain\|ClaudeBotEngine\|_AutoRouter" eolo-crop/crop_main.py | wc -l == 0`
5. Commit atomic: `feat(F2.2): remove Claude imports + instances`

### F2.3 — Loops + helpers (2-3h)
1. Eliminar `_claude_bot_loop()` (L1832+)
2. Eliminar `_claude_bot_tick()` + helpers
3. Eliminar `_execute_decision()` helper (L1584-1610) + call site L1602
4. Eliminar bloque brain.analyze en `_run_analysis_cycle` (L799+, careful)
5. Eliminar AutoRouter uses (L757, 763)
6. Eliminar `gather_tasks` self._claude_bot_loop (L574)
7. Verify: `grep "self\.brain\|self\.claude_bot\|self\._auto_router" eolo-crop/crop_main.py | wc -l == 0`
8. `python3 -c "import ast; ast.parse(open('crop_main.py').read())"` → OK
9. Commit atomic: `feat(F2.3): remove Claude loops + helpers`

### F2.4 — State writer + poll_settings (1h)
1. Eliminar `_poll_settings` bloque Claude (~L2751-2773)
2. Eliminar `set_risk_defaults` (~L2831-2835)
3. Eliminar state writer Claude (~L2972-2994)
4. Eliminar `_run_analysis_cycle` si queda vacío
5. Update comments con referencias a OptionsBrain
6. Verify: smoke test stub-flask
7. Commit: `feat(F2.4): remove Claude state + config handling`

### F2.5 — Archivar dirs (30 min)
1. `git mv eolo-crop/claude eolo-crop/_archive_claude`
2. `git mv eolo-crop/eolo_common/routing.py eolo-crop/_archive_claude/`
3. Verify: no Python imports rotos
4. Commit: `chore(F2.5): archive claude/ + routing.py`

### F2.6 — Bug AC.1 fix (30 min, OPCIONAL bonus)
1. Edit `eolo-crop/execution/options_trader.py` L1098 docstring
2. Edit L1111 lógica de tagging
3. Verify: theta_harvest tagging funciona
4. Commit: `fix(AC.1): respect strategy field in execute_decision`

### F2.7 — Smoke test local + deploy canary (1-2h)
1. Stub flask test pasa
2. Build container localmente (Cloud Build)
3. Deploy a Cloud Run con tag canary
4. Smoke test canary URL
5. Logs canary 5 min sin errores Claude
6. **Verificar n_open=0 mantained, no claude indicators in state**

### F2.8 — Promote + monitor (30 min + observación 24h)
1. `gcloud run services update-traffic ... --to-revisions=NEW=100`
2. Monitor logs 1h: theta_harvest scanning, no Claude
3. Monitor 24h: trades solo theta_harvest tagged
4. Si OK → cierre cleanup. Si rollback → vuelta a 00042-xsr

**Total estimado:** 6-9 horas en 1-2 sesiones.

---

## 17. Cosas a NO TOCAR

- `eolo-crop/execution/options_trader.py` (excepto L1098, L1111 — fix opcional)
- `eolo-crop/theta_harvest/*` (módulo entero)
- `eolo-crop/stream/*`
- `eolo-crop/analysis/*` (Greeks, IV surface)
- `eolo-crop/main.py` (Flask endpoints) — EXCEPTO los S1 strategy_params si están en el branch S1
- Firestore: `eolo-crop-config/settings` (sin cambios)
- Firestore: `eolo-crop-state/*` (sin cambios)

---

## 18. Definition of done

- [ ] `crop_main.py` sin imports/refs a Claude
- [ ] crop_main.py compila sin error (`ast.parse` OK)
- [ ] Carpeta `claude/` archivada
- [ ] routing.py archivada
- [ ] Smoke test local pasa
- [ ] Deploy canary OK
- [ ] Logs canary: 0 menciones `CLAUDE_BOT`, 0 menciones `OptionsBrain`
- [ ] state.json sin `claude_bot`, `claude_history`, `claude_last`, `claude_calls`
- [ ] 24h post-promote: solo trades theta_harvest
- [ ] crop_main.py final ~3329 líneas (-11%)
- [ ] Plan documentado se actualiza con findings reales post-cleanup

---

## 19. Rollback plan

Si algo rompe:
```bash
# Quick rollback Cloud Run traffic
gcloud run services update-traffic eolo-bot-crop \
  --region=us-east1 --project=eolo-schwab-agent \
  --to-revisions=eolo-bot-crop-00042-xsr=100

# Git rollback
git checkout pre-cleanup-crop-theta-only-20260511_1652
# o
git reset --hard pre-cleanup-crop-theta-only-20260511_1652
```

Tag y backup ya pusheados (verificado en sesión).
