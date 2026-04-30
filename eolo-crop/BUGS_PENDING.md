# CROP Dashboard — Bugs Pendientes

## Bug #1: Greeks vacíos (Delta / Theta netos)

**Síntoma:** Las cards de Greeks muestran `–` aunque hay posiciones abiertas.

**Causa probable:** `updateGreeks()` lee `state.pnl.open_list` (array de posiciones con delta/theta individual por contrato). El endpoint `/api/state` no construye ese campo — las posiciones theta están en `state.theta.positions`, no en `state.pnl.open_list`.

**Fix requerido:** En `main.py → api_state()`, mapear `bot._theta_positions` → `state.pnl.open_list` con los campos `{symbol, delta, theta, contracts, net_credit, current_value, ...}` que espera `updateGreeks()`.

---

## Bug #2: Curva P&L Intradía vacía

**Síntoma:** El gráfico `pnl-chart` no muestra ningún punto.

**Causa probable:** `updatePnLChart()` lee `state.pnl.closed` (trades cerrados con `exit_ts`, `symbol`, `pnl`). El endpoint `/api/state` no expone `state.pnl.closed` — los trades cerrados de theta están en `bot._theta_pnl_history` pero con estructura diferente.

**Fix requerido:** En `api_state()`, construir `state.pnl.closed` desde `bot._theta_pnl_history` con la estructura `{symbol, pnl, exit_ts, net_credit, exit_value}`.

---

## Bug #3: Tabla Performance P&L por DTE / Tranche vacía

**Síntoma:** La tabla no muestra filas.

**Causa probable:** `updatePerformance()` agrupa por `dte_slot` + `tranche_id` sobre `[...openList, ...closed]`. Mismo root cause que Bug #1 y #2 — `open_list` y `closed` no poblados.

**Fix requerido:** Depende de fixes de Bug #1 + #2.

---

## Bug #4: Heatmap P&L vacío

**Síntoma:** El heatmap no muestra celdas coloreadas.

**Causa probable:** `updateHeatmapPnL()` lee `state.pnl.closed`. Mismo root cause que Bug #2.

**Fix requerido:** Depende de fix de Bug #2.

---

## Bug #5: Paper Trades sin filas

**Síntoma:** La tabla de Paper Trades aparece vacía.

**Causa probable:** `updateTrades()` lee `state.pnl.closed`. Mismo root cause que Bug #2.

**Fix requerido:** Depende de fix de Bug #2.

---

## Nota: IV Surface eliminada intencionalmente

`iv_surfaces` fue removido del dashboard (HTML + CSS + JS) porque CROP no escribe ese campo en `/api/state`. Las secciones eliminadas son: `📈 IV Surface — Term Structure por Ticker` y `🔥 Heatmap IV`. Si en el futuro el bot comienza a exponer datos de IV, se puede restaurar desde git history.
