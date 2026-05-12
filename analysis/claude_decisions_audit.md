# Claude Decisions Audit — Pre F3 Cleanup
_Generated: 2026-05-12T19:02:06.271869+00:00_

Audit cuantitativo del track record de Claude decisions antes del cleanup F3.
Objetivo: informar decisión post-F4 sobre si Claude debe ser tratado como safety net.

## Resumen ejecutivo

- **Total decisions auditadas:** 13641
- **V2 Claude trades:** 264, P&L $+252.00
- **Crypto Claude trades:** 327, P&L $-113.79
- **Crypto API cost (estimated):** $27.16

## 1. Counts agregados

| Collection | Containers | Sub-docs | Pattern |
|---|---|---|---|
| `eolo-claude-bot-decisions-v2` | 15 | 6182 | containers + subcollection |
| `eolo-claude-decisions-v2` | 18 | 6525 | containers + subcollection |
| `eolo-crypto-claude-decisions` | 0 | 934 | flat |

## 2. V2 Mispricing Decisions (`eolo-claude-decisions-v2`)

Total: **6525** decisions

### By Confidence (genera strategy=claude_high/medium en trades)
- `HIGH`: 3853
- `MEDIUM`: 1601
- `LOW`: 1071

### By Action
- `BUY`: 4315
- `HOLD`: 1176
- `SELL_TO_CLOSE`: 1034

### By Mispricing Type
- `None`: 2362
- `BSM_MISPRICING`: 2110
- `PUT_CALL_PARITY`: 1277
- `IV_SKEW_JUMP`: 468
- `BUTTERFLY_ARBITRAGE`: 266
- `CALENDAR_IV_GAP`: 40
- `CALENDAR_IV_SPREAD`: 2

### Top 15 Tickers
- `QQQ`: 992
- `NVDA`: 978
- `AAPL`: 944
- `IWM`: 927
- `SPY`: 846
- `MSFT`: 777
- `TSLA`: 640
- `TQQQ`: 421

## 3. V2 General Bot Decisions (`eolo-claude-bot-decisions-v2`)

Total: **6182** decisions, **840** errors (13.6%)

### By Signal
- `HOLD`: 6182

### Confidence Stats (numeric)
- Count: 6182, Avg: 0.113, Range: [0.0, 1.0]

### Top 10 Strategy Used
- `data_integrity_check`: 2822
- `error`: 840
- `data_validation_gate`: 452
- `data-quality gate / no-trade filter`: 450
- `data-quality gate / sanity check`: 183
- `data-integrity-check / no-trade`: 166
- `data_integrity_check + risk_management`: 120
- `data-integrity-check / market-hours-filter`: 96
- `data_validation_safeguard`: 82
- `data-quality gate / market-hours filter`: 80

## 4. Crypto Claude Decisions (`eolo-crypto-claude-decisions`)

Total: **934** decisions
Total API cost (estimated): **$27.16**

### By Action
- `BUY`: 424
- `HOLD`: 415
- `SELL`: 95

### By Confidence
- `0.72`: 350
- `0.55`: 154
- `0.45`: 144
- `0.35`: 105
- `0.65`: 60
- `0.63`: 48
- `0.67`: 48
- `0.58`: 9
- `0.68`: 7
- `0.62`: 4
- `0.78`: 2
- `0.42`: 1
- `0.92`: 1
- `0.52`: 1

### Top 15 Symbols
- `None`: 415
- `SOLUSDT`: 205
- `TRXUSDT`: 77
- `XRPUSDT`: 56
- `BTCUSDT`: 49
- `ETHUSDT`: 45
- `LINKUSDT`: 30
- `ADAUSDT`: 22
- `DOGEUSDT`: 19
- `AVAXUSDT`: 9
- `BNBUSDT`: 5
- `HIGHUSDT`: 2

### By Mode
- `TESTNET`: 934

## 5. Date Ranges

| Collection | Oldest | Newest | Days |
|---|---|---|---|
| `eolo-claude-bot-decisions-v2` | 2026-04-17 | 2026-05-12 | 15 |
| `eolo-claude-decisions-v2` | 2026-04-17 | 2026-05-12 | 18 |
| `eolo-crypto-claude-decisions` | 2026-04-18 | 2026-05-11 | 8 |

## 6. Decisions → Trades Match + P&L (CRITICAL)

### V2 (eolo-options-trades, strategy starts with `claude_`)

| Strategy | Trades | BUY_TO_OPEN | SELL_TO_CLOSE | P&L Total | Avg P&L | Wins | Losses | Win Rate |
|---|---|---|---|---|---|---|---|---|
| `claude_high` | 83 | 8 | 75 | $+531.00 | $+7.27 | 23 | 8 | 74.2% |
| `claude_medium` | 181 | 35 | 146 | $-279.00 | $-2.11 | 18 | 7 | 72.0% |

### Crypto (eolo-crypto-trades, strategy starts with `claude_`)

| Strategy | Trades | BUY | SELL | P&L Total | Avg P&L | Wins | Losses | Win Rate |
|---|---|---|---|---|---|---|---|---|
| `claude_bot` | 327 | 0 | 0 | $-113.79 | $-0.35 | 15 | 57 | 20.8% |

## 7. Recomendaciones cuantitativas

- ✅ V2 claude_high: P&L $+531.00, WR 74.2% — CANDIDATO a considerar como safety net post-observación
- ❌ V2 claude_medium: P&L $-279.00, WR 72.0% — CONFIRMAR como strategy normal (no safety net)
- ❌ Crypto claude_bot: P&L $-113.79, WR 20.8% — CONFIRMAR como strategy normal (no safety net)

### Criterios de evaluación
- ✅ CANDIDATO safety net: P&L > $0, win rate > 50%, ≥ 20 trades
- ❌ NO safety net: P&L < $0 o win rate < 40%
- 🟡 Zona gris: intermedio
- ⚠️ Insuficiente data: < 20 trades

## 8. Próximos pasos post-F4

1. Observar 1 semana con Pure Isolation estricto (claude como strategy normal)
2. Re-correr este audit con data post-Pure-Isolation
3. Comparar P&L y win rate con baseline pre-cleanup (este reporte)
4. Si claude_* variants mantienen track record positivo: considerar agregar a SAFETY_NETS
5. Si confirman track record negativo o cross-attribution era artificial: confirmar diseño actual