# V1 RETEST PLAN — 2026-06-05

**Decisión de Juan (05-jun):** re-test selectivo de V1, 4 semanas, n≥30 por estrategia,
metodología CI95 del Master Recap. Crypto y V2/CROP quedan como están.
**Base:** docs/ANALISIS_REINSERCION_ESTRATEGIAS_20260605.md.

---

## 1. Pre-requisitos de código — HECHOS (pendiente deploy)

| Fix | Archivo | Qué cambia |
|---|---|---|
| **Wrapper direccional** (causa WR 0% _LONG / SHORTs huérfanos) | `eolo_common/strategies_v3/strategies.py` | La señal opuesta ya no se traga como HOLD: pasa marcada `exit_only=True` (es la señal de SALIDA) |
| **Dispatcher** | `Bot/bot_strategies_v3_dispatcher.py` | Propaga `exit_only` al result |
| **Trader** | `Bot/bot_trader.py` | `exit_only` solo CIERRA posiciones, nunca abre. Heurística por sufijo `_LONG`/`_SHORT` cubre el path confluencia |
| **PnL=null** (38.5% SELLs BOLLINGER) | `Bot/bot_trader.py` | `_recover_entry_price()`: si el entry se perdió (restart), lo recupera del doc de trades del día en Firestore |
| **auto_close retry silencioso** | `Bot/bot_main.py` | `auto_close_done_date` se marca solo si el cierre NO falló — reintenta el próximo ciclo |
| **CLOSE_ALL solo cerraba LONGs** | `Bot/bot_main.py` | Ahora también cubre SHORTs (BUY_TO_COVER) e itera TODAS las posiciones, incluidas huérfanas fuera de config |
| **Daily-cap bloqueaba covers** | `Bot/bot_main.py` | Un BUY que cubre un SHORT es un cierre — ya no se suprime con cap activo |

**Tests:** `tests/test_retest_fixes.py` — 13 passed (wrapper, transiciones del trader, guards en source).

## 2. Setup del re-test (corre Juan en su Mac)

```bash
cd ~/PycharmProjects/eolo
git pull origin main

# 1. Ver plan (dry-run, no escribe nada):
python3 tools/v1_retest_setup.py

# 2. Aplicar (backup + reactivar + cohort marker):
python3 tools/v1_retest_setup.py --apply --include-directional

# 3. Deploy del bot V1 con los fixes:
#    (mismo pipeline de siempre — cloudbuild-eolo-bot.yaml)
gcloud builds submit --config cloudbuild-eolo-bot.yaml --project eolo-schwab-agent
```

## 3. Qué se reinserta

**Por performance con datos corruptos** (veredicto inválido — re-test legítimo):
bollinger, rvol_breakout, anchor_vwap, tick_trin_fade, opening_drive,
bollinger_rsi_sensitive, ema_8_21, tsv.

**Por bug arquitectural ya fixeado** (`--include-directional`):
vwap_momentum_long, net_bsv_long, donchian_turtle_long, vw_macd_short,
ha_cloud_short, ema_short, ema_tsi_short, ema_3_8_short, xom_30m_short.

**Las 12 "activas" siguen ON** — sus métricas históricas también quedan invalidadas;
el re-test las revalida en paralelo sin tocarlas.

## 4. Reglas del cohort RETEST_V1_2026H1

- **Corte:** 2026-06-04 15:44 UTC (deploy del candle fix). Nada anterior cuenta.
- **Ventana:** 05-jun → **03-jul-2026** (~4 semanas, ~20 sesiones).
- **Umbral de veredicto:** n ≥ 30 trades por estrategia. Con menos n: extender, no concluir.
- **Metodología:** expectancy + CI95 + cell-level por ticker (la del Master Recap 6-may —
  la metodología era buena; el input era el problema).
- **Marker:** `eolo-config/retest_v1` (lo escribe el setup script) para que el análisis
  filtre el cohort automáticamente.
- **Reemplaza** la re-evaluación que estaba agendada para ~17-jun sobre datos viejos.

## 5. Verificación post-deploy (primera sesión)

1. Logs: aparecen señales `[V3/<X>_LONG] ... SELL ✅ ... [LONG-only] exit` → el exit fluye.
2. `[CANDLE_FRESH]` / pushes con ts reciente — el feed sigue sano.
3. Un ciclo completo sin SHORTs nuevos no solicitados (exit_only no abre).
4. SELLs con `pnl_usd` poblado (no null) — recovery activo.
5. A los 2-3 días: ratio opens/closes por estrategia ≈ balanceado (no más huérfanos).

## 6. Riesgos

- Las keys de Firestore pueden diferir de los nombres asumidos — el script lista
  missing + candidatos similares en dry-run antes de tocar nada.
- ConfluenceFilter puede consolidar BUY de apertura y BUY de cover de estrategias
  distintas; la heurística por sufijo en el trader acota el daño (no abre, solo cierra).
- Si el re-test confirma que una removida era realmente mala CON datos sanos,
  se re-apaga con evidencia definitiva — eso también es un resultado valioso.
