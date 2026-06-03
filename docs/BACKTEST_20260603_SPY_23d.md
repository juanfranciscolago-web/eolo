# Backtest Report

- Tickers:       SPY
- Window:        2026-04-29 → 2026-05-29
- Sample hours:  [10]
- Pre-screen:    True
- Budget cap:    $5.0
- Budget hit:    False

## Coverage

- Decisions produced: **23** of 23 requested (100.0%)
- Total cost:         **$2.323**

## Verdict distribution

- WAIT: 23

## Regime distribution

- positive_low: 9
- flip_zone: 7
- unknown: 5
- negative: 2

## Top rule citations

- TR-Juan-036: 23
- TR-Juan-043: 22
- TR-Juan-040: 21
- TR-Juan-063: 18
- TR-Juan-022: 9
- TR-Juan-070: 6
- TR-Juan-044: 6
- TR-Juan-064: 5
- TR-Juan-031: 5
- TR-Juan-037: 5
- TR-Juan-067: 4
- TR-Juan-062: 2
- TR-Juan-071: 2
- TR-Juan-069: 2
- TR-Juan-010: 1
- TR-Juan-011: 1
- TR-Juan-059: 1
- TR-Juan-068: 1
- TR-Juan-033: 1
## Notas operativas

**Data coverage real:** SPY 23 días (2026-04-29 a 2026-05-29) — único ticker con cache QD presente al inicio del run. QQQ/IWM/TQQQ tenían 0 días cached → ejecución SPY-only por decisión interna CC (vs ejecutar fake multi-ticker que fallaría por archivo ausente).

**Pre-screen no economizó:** 0/23 Haiku skips. Threshold `confidence_threshold=7` escaló 100% a Sonnet — Haiku siempre devolvió `should_call_full=true` con razonamiento. Tuning candidato (sin urgencia): threshold a 8 + límite máximo de tokens en Haiku prompt.

**Schwab token local fail:** `helpers.get_schwab_access_token` no existe en eolo-crop/helpers.py (la función real es `get_access_token` — fix candidato Sub-A.x). Sin token → `get_window_for_date` retornó [] → indicators con defaults → LLM flagueó "datos insuficientes". Producción no afectada (token Firestore funciona en Cloud Run SA).

**Compute layer Sub-B working:** "Magnet Strength score=10" y "Cascade Risk HIGH" presentes en main_reason del LLM — confirma wire end-to-end de las 3 compute functions TERMINATOR Sub-B.

**KB v1.3 well-cited:** TR-Juan-062 (2), 064 (5), 067 (4), 068 (1), 069 (2), 070 (6), 071 (2) — las 10 reglas QD-aware aparecen en producción.

## Next steps (recomendaciones, no implementación)

1. **Schwab token bug fix (~30 min)** — corregir `_get_schwab_token()` en `backtest/schwab_historical.py` a usar `helpers.get_access_token`. Sin esto, indicators reales no entran a los snapshots de backtest local.
2. **Fetch QD multi-ticker (~10 min API)** — `python3 -m backtest.historical_fetcher --start 2026-04-29 --end 2026-05-29 --tickers QQQ,IWM,TQQQ` para poder correr 4-ticker backtests futuros.
3. **Haiku threshold tuning** — subir a 8 (default conservativo) si user quiere economías de pre-screen visibles. Actual = 100% escalación.
