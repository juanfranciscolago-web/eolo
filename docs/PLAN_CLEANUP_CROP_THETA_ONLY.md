# Plan: Limpieza CROP → Theta Harvest Only

**Generado:** 11-may-2026 16:35 ART
**Decisión:** CROP debe ser theta_harvest only (decisión de Juan)
**Origen:** Investigación arquitectural reveló fork incompleto V2→CROP

---

## 1. Estado actual (problema)

CROP corre múltiples estrategias además de theta_harvest:

| Strategy | Open positions | Status |
|---|---|---|
| theta_harvest | 0 | ÚNICO QUE DEBE QUEDAR |
| claude_medium | 26 | A ELIMINAR |
| calendar_iv_gap | 1 | A ELIMINAR |
| (untagged CSPREAD) | 45 | A ELIMINAR |

Total: 72 posiciones, 0 de theta_harvest.

## 2. Whitelist (lo que queda)

- `stream.options_stream` (Schwab WebSocket)
- `stream.options_chain` (chain fetcher)
- `analysis.greeks` (Black-Scholes)
- `analysis.iv_surface` (IV surface)
- `theta_harvest.*` (la estrategia)
- Helpers de auth y Firestore

## 3. Blacklist (lo que sale)

- `claude.options_brain` (LLM decision engine)
- `claude.claude_bot` (engine LLM separado)
- `execution.options_trader.execute_decision` (executor Claude)
- `eolo_common.routing.AutoRouter`
- 45 strategies técnicas (señales para Claude brain)
- Todos los loops y handlers de Claude decisions

## 4. Riesgos identificados

### Riesgo 1 — Acoplamiento oculto
Posible: `OptionsTrader.execute_decision()` puede ser usado por theta_harvest también. Verificar antes de eliminar.

### Riesgo 2 — Schedulers Cloud
Cron V1+V2 disparan `/daily-open-reset`. Verificar que ese endpoint no llame a código a eliminar.

### Riesgo 3 — Estado huérfano Firestore
26 posiciones `claude_medium` + 45 CSPREAD viven en Firestore. ¿Quién las gestiona post-cleanup?

### Riesgo 4 — V2 paralelo (CRÍTICO)
`eolo-options/eolo_v2_main.py` (V2) sigue corriendo con todo el código. **¿V2 y CROP comparten cuenta Schwab?** Si sí, V2 sigue abriendo claude_medium aunque CROP esté limpio.

## 5. Pre-flight necesario antes de Fase 2

1. PnL por strategy últimos 30 días (¿cuál ganaba? ¿cuál perdía?)
2. Confirmar Schwab account: V2 vs CROP, ¿misma o distinta?
3. Mapeo acoplamiento: ¿OptionsTrader.execute_decision lo usa theta_harvest?
4. Backup completo: Firestore state + código + Cloud Run revision

## 6. Fases del plan

### FASE 1 — Auditoría (próxima sesión, 2-3h)

- Análisis PnL histórico por strategy
- Confirmación de account separation V2 vs CROP
- Mapeo de acoplamiento técnico
- Decisión final sobre posiciones existentes (dejar expirar vs cerrar)
- Backup completo pre-cambios

**Entregable:** doc con decisiones tomadas

### FASE 2 — Desconexión código (sesión dedicada, 4-6h)

#### F2.1 — eolo-crop/crop_main.py
- Eliminar imports de claude.*, execution.options_trader, eolo_common.routing
- Eliminar instanciaciones (self.trader, self.brain, self.claude_bot)
- Eliminar calls a execute_decision() (L1285, L1478, L1602)
- Eliminar todo código de Claude decision loops
- Esperado: crop_main.py pasa de 3729 a ~1500-2000 líneas

#### F2.2 — Carpetas
- eolo-crop/claude/ → archivar (no borrar todavía)
- eolo-crop/execution/ → mantener solo lo que usa theta_harvest
- eolo-crop/eolo_common/ → eliminar AutoRouter

#### F2.3 — eolo-crop/main.py (endpoints Flask)
- Eliminar endpoints solo de Claude/AutoRouter
- Mantener /api/state, /api/config, /dashboard

#### F2.4 — Firestore config
- Backup eolo-crop-config/settings completo
- strategies_enabled: 45 → vacío o solo theta
- Validar bot arranca con config nueva

### FASE 3 — Manejo posiciones existentes (1-2h)

**Recomendación:** dejar expirar naturalmente
- Todas son options con DTE corto
- Bajo riesgo operativo
- Cero acción manual en Schwab

**Alternativa:** cerrar manualmente las 26 claude_medium
- Si riesgo PnL es alto
- Decisión post-Fase 1 análisis PnL

### FASE 4 — Deploy + testing (1-2h)

- Deploy canary (10% traffic)
- Smoke test: bot arranca, conecta Schwab, theta_harvest scan
- Monitor 24h: solo theta_harvest abre, NO claude_medium
- PR review + merge a main

## 7. Cronograma

- **HOY (11-may):** Documentación plan completo (ESTE DOC)
- **Sesión 2 (12-may):** FASE 1 Auditoría (2-3h con cabeza fresca)
- **Sesión 3 (13-may):** FASE 2 Desconexión código parte 1 (3-4h)
- **Sesión 4 (14-may):** FASE 2 Desconexión código parte 2 (2-3h)
- **Sesión 5 (15-may):** FASE 3 + FASE 4 deploy (3-4h)

**Total estimado:** 12-18 horas en 4-5 sesiones.

## 8. Decisiones pendientes (requieren info adicional)

1. ¿V2 y CROP comparten cuenta Schwab? (operativa crítica)
2. ¿PnL por strategy de últimos 30 días? (cuál cerrar primero)
3. ¿Existe eolo_v2_main.py en producción activa o solo CROP es live?
4. ¿Plan para las 72 posiciones huérfanas? (expirar vs cerrar)

## 9. Bugs/findings relacionados

- Bug AD: CROP heredó multi-strategy de V2 sin desconectar
- Bug AC.1: 45 CSPREAD positions con strategy="" (tagging bug)
- H20: max_positions label engañoso
- H21: Bot multi-strategy NO documentado
- H22: 45 CSPREAD trades en 5 seg (misterio)
- H23: nominal_equity 4x más estricto que real

## 10. Impacto en Sprint S1 (entregado hoy)

Sprint S1 expone parámetros de theta_harvest como "del bot completo".
Post-cleanup esto SERÁ correcto (CROP = theta_harvest only).

**Acción inmediata sobre S1:**
- Deploy S1 tal cual está → válido post-cleanup
- Pre-cleanup, agregar disclaimer temporal: "Theta Harvest config (otros módulos serán removidos)"

## 11. Sprint S2 — Replanteado

S2 original (Risk Management) tiene sentido recién POST-cleanup.
Pre-cleanup, S2 mostraría datos confusos (cap mezclado entre strategies).

**Decisión:** S2 espera a FASE 4 completada.
