# Dual-attribution analysis — V1 / V2 / Crypto
_Generado: 2026-05-12T09:50:54_  
_Backup: `backups/20260512_094949`_

Reconstrucción FIFO de pairs BUY→SELL por bot, con P&L atribuido tanto al opener como al closer.
El P&L visible en Firestore se atribuye al **closer**; este reporte expone la doble vista para detectar interferencia entre estrategias.

---

## Bot: V1 Stock

### Resumen
- Total trades pareados: **365**
- Trades sin pareo (BUY sin SELL al final): **26**
- Total P&L (sólo pairs con pnl medible: 238/365): **$+469.10**

### Top 5 strategies por OPENER attribution
| Opener | Opens | Total P&L | Avg P&L | Win rate (measurable) |
|---|---|---|---|---|
| `SQUEEZE` | 99 | $+110.76 | $+2.64 | 52% (42/99) |
| `VOLUME_BREAKOUT` | 28 | $+117.69 | $+4.20 | 54% (28/28) |
| `TEST` | 27 | $+0.00 | $+0.00 | 0% (0/27) |
| `EMA` | 24 | $-6.39 | $-1.06 | 50% (6/24) |
| `VOL_REVERSAL_BAR` | 23 | $+127.63 | $+5.55 | 65% (23/23) |

### Top 5 strategies por CLOSER attribution
| Closer | Closes | Total P&L | Avg P&L | Win rate (measurable) |
|---|---|---|---|---|
| `RVOL_BREAKOUT` | 65 | $+118.82 | $+1.95 | 56% (61/65) |
| `VWAP_ZSCORE` | 35 | $-25.20 | $-0.81 | 29% (31/35) |
| `BOLLINGER` | 32 | $+56.34 | $+9.39 | 83% (6/32) |
| `CLOSE_ALL` | 28 | $+135.20 | $+9.66 | 71% (14/28) |
| `TEST` | 27 | $+0.00 | $+0.00 | 0% (0/27) |

### Cross-strategy interference
- Intra-strategy (opener = closer): **44** (12.1%)
- Cross-strategy (opener ≠ closer): **321** (87.9%)
- P&L atribuible a cross-strategy: **$+472.17**

### Matriz opener × closer (top 5 × top 5 por volumen)
| Opener \ Closer | `RVOL_BREAKOUT` | `VWAP_ZSCORE` | `BOLLINGER` | `CLOSE_ALL` | `TEST` |
|---|---|---|---|---|---|
| `SQUEEZE` | 20($+6) | 8($-1) | 25($+31) | 7($-4) | — |
| `VOLUME_BREAKOUT` | 5($+9) | 4($-5) | — | 2($+9) | — |
| `TEST` | — | — | — | — | 27($+0) |
| `EMA` | 2($-4) | — | — | 7($+0) | — |
| `VOL_REVERSAL_BAR` | 8($+13) | — | 1($+16) | 3($+56) | — |

### Findings clave
**Huérfanas** (abren ≫ cierran):
- `SQUEEZE`: 99 opens / 2 closes
- `VOLUME_BREAKOUT`: 28 opens / 2 closes
- `EMA`: 24 opens / 3 closes
- `VOL_REVERSAL_BAR`: 23 opens / 2 closes
- `VW_MACD`: 20 opens / 1 closes
- `OBV_MTF`: 14 opens / 3 closes
- `SUPERTREND`: 7 opens / 0 closes
- `MACD_ACCEL`: 6 opens / 0 closes
- `MACD_BB`: 5 opens / 1 closes

**Carroñeras** (cierran ≫ abren):
- `RVOL_BREAKOUT`: 11 opens / 65 closes
- `VWAP_ZSCORE`: 0 opens / 35 closes
- `CLOSE_ALL`: 0 opens / 28 closes
- `STOP_RUN`: 4 opens / 26 closes
- `VWAP+RSI`: 1 opens / 22 closes
- `BOLLINGER_RSI_SENSITIVE`: 1 opens / 20 closes
- `TICK_TRIN_FADE`: 0 opens / 16 closes
- `OPENING_DRIVE`: 0 opens / 12 closes
- `ANCHOR_VWAP`: 0 opens / 11 closes
- `RISK_WATCHDOG`: 0 opens / 7 closes

**Cierres forzados** (clasificados):
- System forced (RISK_WATCHDOG / CLOSE_ALL / SL / TP / auto-close):
  - N: **35** · P&L: **$+154.16**
- Claude override (claude_bot / claude_high / claude_medium):
  - N: **0** · P&L: **$+0.00**

---

## Bot: V2 Options

### Resumen
- Total trades pareados: **772**
- Trades sin pareo (BUY sin SELL al final): **1159**
- Total P&L (sólo pairs con pnl medible: 571/772): **$+532.00**

### Top 5 strategies por OPENER attribution
| Opener | Opens | Total P&L | Avg P&L | Win rate (measurable) |
|---|---|---|---|---|
| `bsm_mispricing` | 236 | $-137.50 | $-1.24 | 24% (111/236) |
| `put_call_parity` | 205 | $+159.00 | $+0.89 | 8% (178/205) |
| `BSM_MISPRICING` | 95 | $-35.00 | $-0.38 | 29% (93/95) |
| `PUT_CALL_PARITY` | 84 | $+457.50 | $+5.79 | 11% (79/84) |
| `IV_SKEW_JUMP` | 45 | $-95.00 | $-2.11 | 4% (45/45) |

### Top 5 strategies por CLOSER attribution
| Closer | Closes | Total P&L | Avg P&L | Win rate (measurable) |
|---|---|---|---|---|
| `<empty>` | 298 | $+273.50 | $+1.18 | 16% (231/298) |
| `bsm_mispricing` | 144 | $+108.50 | $+2.41 | 24% (45/144) |
| `claude_medium` | 144 | $-359.00 | $-2.76 | 12% (130/144) |
| `put_call_parity` | 88 | $+35.00 | $+0.42 | 8% (84/88) |
| `claude_high` | 73 | $+515.00 | $+7.25 | 31% (71/73) |

### Cross-strategy interference
- Intra-strategy (opener = closer): **257** (33.3%)
- Cross-strategy (opener ≠ closer): **515** (66.7%)
- P&L atribuible a cross-strategy: **$+389.00**

### Matriz opener × closer (top 5 × top 5 por volumen)
| Opener \ Closer | `<empty>` | `bsm_mispricing` | `claude_medium` | `put_call_parity` | `claude_high` |
|---|---|---|---|---|---|
| `bsm_mispricing` | 33($+0) | 139($+108) | 9($-464) | — | 49($+260) |
| `put_call_parity` | 26($+0) | — | 82($+15) | 86($+35) | 11($+109) |
| `BSM_MISPRICING` | 85($-42) | 5($+0) | — | — | 2($+6) |
| `PUT_CALL_PARITY` | 78($+458) | — | 3($+0) | 2($+0) | 1($+0) |
| `IV_SKEW_JUMP` | 41($-95) | — | 4($+0) | — | — |

### Findings clave
**Huérfanas** (abren ≫ cierran):
- `BSM_MISPRICING`: 95 opens / 0 closes
- `PUT_CALL_PARITY`: 84 opens / 0 closes
- `IV_SKEW_JUMP`: 45 opens / 0 closes
- `BUTTERFLY_ARBITRAGE`: 14 opens / 0 closes
- `CLAUDE_MEDIUM`: 10 opens / 0 closes

**Carroñeras** (cierran ≫ abren):
- `<empty>`: 0 opens / 298 closes
- `claude_medium`: 17 opens / 144 closes
- `claude_high`: 3 opens / 73 closes

**Cierres forzados** (clasificados):
- System forced (RISK_WATCHDOG / CLOSE_ALL / SL / TP / auto-close):
  - N: **0** · P&L: **$+0.00**
- Claude override (claude_bot / claude_high / claude_medium):
  - N: **217** · P&L: **$+156.00**

---

## Bot: Crypto

### Resumen
- Total trades pareados: **18024**
- Trades sin pareo (BUY sin SELL al final): **3**
- Total P&L (sólo pairs con pnl medible: 18024/18024): **$-13,653.43**

### Top 5 strategies por OPENER attribution
| Opener | Opens | Total P&L | Avg P&L | Win rate (measurable) |
|---|---|---|---|---|
| `rsi_sma200` | 5069 | $-2,693.64 | $-0.53 | 36% (5069/5069) |
| `squeeze` | 3865 | $-4,019.94 | $-1.04 | 27% (3865/3865) |
| `rvol_breakout` | 1141 | $-410.48 | $-0.36 | 33% (1141/1141) |
| `ema_tsi` | 992 | $-964.40 | $-0.97 | 29% (992/992) |
| `tsv` | 933 | $-480.38 | $-0.51 | 40% (933/933) |

### Top 5 strategies por CLOSER attribution
| Closer | Closes | Total P&L | Avg P&L | Win rate (measurable) |
|---|---|---|---|---|
| `rsi_sma200` | 8435 | $-195.36 | $-0.02 | 40% (8435/8435) |
| `squeeze` | 3312 | $-4,256.92 | $-1.29 | 30% (3312/3312) |
| `ema_tsi` | 1665 | $-3,079.44 | $-1.85 | 19% (1665/1665) |
| `hh_ll` | 1571 | $-2,930.30 | $-1.87 | 20% (1571/1571) |
| `volume_reversal_bar` | 714 | $-83.55 | $-0.12 | 37% (714/714) |

### Cross-strategy interference
- Intra-strategy (opener = closer): **1459** (8.1%)
- Cross-strategy (opener ≠ closer): **16565** (91.9%)
- P&L atribuible a cross-strategy: **$-13,466.60**

### Matriz opener × closer (top 5 × top 5 por volumen)
| Opener \ Closer | `rsi_sma200` | `squeeze` | `ema_tsi` | `hh_ll` | `volume_reversal_bar` |
|---|---|---|---|---|---|
| `rsi_sma200` | 654($+1,217) | 2334($-1,818) | 851($-1,333) | 116($-109) | 74($+117) |
| `squeeze` | 2726($-1,489) | 106($-448) | 55($-113) | 689($-1,056) | — |
| `rvol_breakout` | 952($-211) | — | 81($-213) | — | 89($-44) |
| `ema_tsi` | 843($-490) | — | 114($-282) | 11($-151) | — |
| `tsv` | 424($+403) | 68($-165) | 180($-423) | 71($-105) | 123($+17) |

### Findings clave
**Huérfanas** (abren ≫ cierran):
- `rvol_breakout`: 1141 opens / 0 closes
- `tsv`: 933 opens / 0 closes
- `vw_macd`: 834 opens / 0 closes
- `obv_mtf`: 608 opens / 0 closes
- `vwap_zscore`: 186 opens / 0 closes
- `stop_run`: 126 opens / 0 closes
- `squeeze,hh_ll`: 10 opens / 0 closes

**Carroñeras** (cierran ≫ abren):
- `hh_ll`: 179 opens / 1571 closes
- `auto_close`: 0 opens / 13 closes
- `manual_command`: 0 opens / 12 closes
- `rsi_sma200,hh_ll`: 0 opens / 6 closes

**Cierres forzados** (clasificados):
- System forced (RISK_WATCHDOG / CLOSE_ALL / SL / TP / auto-close):
  - N: **13** · P&L: **$+86.14**
- Claude override (claude_bot / claude_high / claude_medium):
  - N: **75** · P&L: **$-113.79**
