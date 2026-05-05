# CROP Dashboard — Bugs Pendientes

## Estado al 5 de mayo 2026

Los 5 bugs documentados en este archivo (Greeks empty, P&L chart empty,
Performance table empty, Heatmap empty, Paper Trades empty) tienen el
mismo root cause y fueron corregidos hoy con 2 commits.

### Root cause real

**Bug doble:** mismatch de collection Firestore + mismatch de schema.

1. **Collection mismatch** — el dashboard estaba leyendo de
   `eolo-options-state` y `eolo-options-config` (colecciones de V2),
   no de `eolo-crop-state` y `eolo-crop-config` que es donde el bot
   CROP escribe. Arrastre del fork v2 → CROP.

2. **Schema mismatch** — el JS lee `state.pnl.open_list` y
   `state.pnl.closed`, pero `_calc_pnl()` en CROP devolvía listas
   vacías porque trabaja sobre `paper_trades` (BUY_TO_OPEN/
   SELL_TO_CLOSE) que CROP nunca usa. Los datos reales de Theta
   Harvest estaban en `_theta_positions` y `_theta_closed_positions`
   pero no se exponían a `state.pnl`.

### Fixes aplicados

- **`b2d12f0` fix(crop-dashboard)**: corregir colecciones Firestore.
  `STATE_COLL`, `CONFIG_COLL`, nueva `TRADES_COLL` apuntando a
  `eolo-crop-*`. Más 7 docstrings stale.

- **`7b53538` fix(crop)**: nueva helper
  `_calc_theta_pnl_for_dashboard()` que construye `open_list` y
  `closed` desde theta data con el schema exacto que el JS espera.
  `_write_state` usa esta helper en lugar de `_calc_pnl()`.
  `_theta_monitor_loop` ahora persiste greeks (delta/theta/gamma/vega)
  del short leg en `pos`.

### Status post-fix

Los 5 bugs están corregidos en código pero **no validados en
producción** porque el servicio Cloud Run del dashboard de CROP no
existe todavía. Deploy diferido a una sesión futura que incluya:

- Crear `cloudbuild.yaml` correcto para el dashboard (el actual es
  copy/paste de v2 y deploya a `eolo-options-dashboard`)
- Crear el servicio `eolo-crop-dashboard` en Cloud Run
- Configurar IAM, dominio, autenticación

### Limitación conocida

`state.pnl.closed` es in-memory only (sourced from
`_theta_closed_positions`). Tras restart del bot empieza vacío hasta
que se cierre un nuevo trade. El histórico real persiste en Firestore
`eolo-crop-trades/{YYYY-MM-DD}`. Si post-restart visibility se vuelve
necesidad, hidratar `_theta_closed_positions` desde Firestore en
startup.

---

## Histórico (para referencia)

Los 5 bugs originalmente documentados — todos resueltos por los
commits de arriba:

- Bug #1: Greeks vacíos (Delta / Theta netos)
- Bug #2: Curva P&L Intradía vacía
- Bug #3: Tabla Performance P&L por DTE / Tranche vacía
- Bug #4: Heatmap P&L vacío
- Bug #5: Paper Trades sin filas

---

## Nota: IV Surface eliminada intencionalmente

`iv_surfaces` fue removido del dashboard (HTML + CSS + JS) porque
CROP no escribe ese campo en `/api/state`. Las secciones eliminadas
son: `📈 IV Surface — Term Structure por Ticker` y
`🔥 Heatmap IV`. Si en el futuro el bot comienza a exponer datos de
IV, se puede restaurar desde git history.
