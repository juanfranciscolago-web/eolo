# Inventario de Strategies — Pure Isolation Readiness Assessment
_Generado: 2026-05-12T10:12:35_  
_Backup: `backups/20260512_094949`_

Para cada strategy detectada en V1 / V2 / Crypto: conteos de eventos, P&L como opener vs closer, métricas cruzadas y clasificación automática (complete / entry_only / exit_only / minimal / system).

Reusa el pipeline FIFO de `dual_attribution_analysis.py`. Las strategies se normalizan quitando el prefijo `consensus:` (crypto). La clasificación `system` se aplica si CUALQUIERA de las raw strategies asociadas matchea con patrones de cierre forzado (`RISK_WATCHDOG`, `CLOSE_ALL`, `SL/TP`, `auto_close`, `claude_*`).

## Resumen Ejecutivo

| Métrica | V1 Stock | V2 Options | Crypto |
|---|---|---|---|
| Total strategies únicas | 42 | 14 | 33 |
| 🟢 Complete | 11 | 4 | 15 |
| 🟡 Entry-only | 10 | 5 | 7 |
| 🔴 Exit-only | 7 | 1 | 3 |
| ⚪ Minimal | 12 | 1 | 6 |
| ⚫ System (no son strats) | 2 | 3 | 2 |

---

## Bot: V1 Stock

### Strategies clasificadas

| Strategy | Type | Opens | Closes | P&L (opener) | P&L (closer) | Intra | Open ratio |
|---|---|---|---|---|---|---|---|
| `TEST` | 🟢 complete | 27 | 27 | $+0.00 | $+0.00 | 27($+0.00) | 0.50 |
| `HH_LL` | 🟢 complete | 17 | 10 | $-1.45 | $-5.05 | 2($-3.16) | 0.63 |
| `OBV_MTF` | 🟢 complete | 15 | 3 | $+10.88 | $+4.20 | 0($+0.00) | 0.83 |
| `BOLLINGER` | 🟢 complete | 11 | 32 | $+61.19 | $+56.34 | 2($+0.00) | 0.26 |
| `EMA_3_8` | 🟢 complete | 10 | 4 | $+2.48 | $-1.77 | 0($+0.00) | 0.71 |
| `DONCHIAN_TURTLE` | 🟢 complete | 8 | 2 | $-10.38 | $-7.66 | 0($+0.00) | 0.80 |
| `VELA_PIVOT` | 🟢 complete | 7 | 3 | $+37.16 | $+0.00 | 0($+0.00) | 0.70 |
| `RSI_SMA200` | 🟢 complete | 7 | 13 | $+7.47 | $-1.88 | 0($+0.00) | 0.35 |
| `MACD_BB` | 🟢 complete | 5 | 1 | $+2.17 | $+0.00 | 0($+0.00) | 0.83 |
| `TSV` | 🟢 complete | 5 | 6 | $-12.66 | $+131.82 | 0($+0.00) | 0.45 |
| `BOLLINGER_RSI_SENSITIVE` | 🟢 complete | 4 | 20 | $-0.87 | $+6.24 | 0($+0.00) | 0.17 |
| `SQUEEZE` | 🟡 entry_only | 102 | 2 | $+110.76 | $+3.08 | 1($+2.28) | 0.98 |
| `VOLUME_BREAKOUT` | 🟡 entry_only | 29 | 2 | $+117.69 | $-1.15 | 0($+0.00) | 0.94 |
| `EMA` | 🟡 entry_only | 24 | 3 | $-6.39 | $+0.00 | 3($+0.00) | 0.89 |
| `VOL_REVERSAL_BAR` | 🟡 entry_only | 24 | 2 | $+127.63 | $-0.28 | 1($-0.16) | 0.92 |
| `VW_MACD` | 🟡 entry_only | 21 | 1 | $-13.19 | $+3.88 | 0($+0.00) | 0.95 |
| `SUPERTREND` | 🟡 entry_only | 7 | 0 | $+12.51 | $+0.00 | 0($+0.00) | 1.00 |
| `MACD_ACCEL` | 🟡 entry_only | 7 | 0 | $+1.56 | $+0.00 | 0($+0.00) | 1.00 |
| `VWAP_MOMENTUM` | 🟡 entry_only | 6 | 1 | $+1.35 | $+0.23 | 0($+0.00) | 0.86 |
| `MOMENTUM_SCORE` | 🟡 entry_only | 6 | 0 | $-3.03 | $+0.00 | 0($+0.00) | 1.00 |
| `COMBO7_CAMPBELL` | 🟡 entry_only | 5 | 0 | $+4.50 | $+0.00 | 0($+0.00) | 1.00 |
| `RVOL_BREAKOUT` | 🔴 exit_only | 11 | 65 | $+0.26 | $+118.82 | 1($-1.34) | 0.14 |
| `STOP_RUN` | 🔴 exit_only | 4 | 26 | $-6.22 | $+43.22 | 1($-0.69) | 0.13 |
| `VWAP+RSI` | 🔴 exit_only | 1 | 22 | $-0.30 | $+5.22 | 0($+0.00) | 0.04 |
| `OPENING_DRIVE` | 🔴 exit_only | 0 | 12 | $+0.00 | $-2.48 | 0($+0.00) | 0.00 |
| `VWAP_ZSCORE` | 🔴 exit_only | 0 | 35 | $+0.00 | $-25.20 | 0($+0.00) | 0.00 |
| `TICK_TRIN_FADE` | 🔴 exit_only | 0 | 16 | $+0.00 | $-5.08 | 0($+0.00) | 0.00 |
| `ANCHOR_VWAP` | 🔴 exit_only | 0 | 11 | $+0.00 | $+5.35 | 0($+0.00) | 0.00 |
| `NET_BSV` | ⚪ minimal | 4 | 0 | $-0.79 | $+0.00 | 0($+0.00) | 1.00 |
| `BUY_PRESSURE` | ⚪ minimal | 4 | 0 | $-4.85 | $+0.00 | 0($+0.00) | 1.00 |
| `ORB_V3` | ⚪ minimal | 4 | 2 | $-0.74 | $-0.89 | 0($+0.00) | 0.67 |
| `SELL_PRESSURE` | ⚪ minimal | 3 | 0 | $+0.47 | $+0.00 | 0($+0.00) | 1.00 |
| `EMA_8_21` | ⚪ minimal | 3 | 0 | $+28.50 | $+0.00 | 0($+0.00) | 1.00 |
| `ORB` | ⚪ minimal | 2 | 2 | $+0.00 | $+0.00 | 2($+0.00) | 0.50 |
| `GAP` | ⚪ minimal | 2 | 2 | $+0.00 | $+0.00 | 2($+0.00) | 0.50 |
| `VWAP_RSI` | ⚪ minimal | 2 | 2 | $+0.00 | $+0.00 | 2($+0.00) | 0.50 |
| `EMA_TSI` | ⚪ minimal | 2 | 2 | $-0.47 | $-10.85 | 0($+0.00) | 0.50 |
| `HA_CLOUD` | ⚪ minimal | 1 | 4 | $+2.64 | $-1.20 | 0($+0.00) | 0.20 |
| `COMBO3_NINO_SQUEEZE` | ⚪ minimal | 1 | 0 | $+1.22 | $+0.00 | 0($+0.00) | 1.00 |
| `VIX_MEAN_REV` | ⚪ minimal | 0 | 3 | $+0.00 | $+0.03 | 0($+0.00) | 0.00 |
| `CLOSE_ALL` | ⚫ system | 0 | 35 | $+0.00 | $+135.20 | 0($+0.00) | 0.00 |
| `RISK_WATCHDOG` | ⚫ system | 0 | 7 | $+0.00 | $+18.96 | 0($+0.00) | 0.00 |

### Findings por type

#### Entry-only (necesitan SELL signal)

#### `SQUEEZE`
- Volumen: **102 opens** · P&L (como opener): $+110.76 · win rate: 52% (42/102 medibles)
- Cuándo abre: _TODO: investigar código de la strategy_
- Quién la cierra hoy (top 3):
  - `BOLLINGER`: 25 cierres ($+30.74)
  - `RVOL_BREAKOUT`: 20 cierres ($+6.25)
  - `VWAP+RSI`: 18 cierres ($+0.00)
- Propuesta de SELL signal: _TODO: pendiente revisión humana_

#### `VOLUME_BREAKOUT`
- Volumen: **29 opens** · P&L (como opener): $+117.69 · win rate: 54% (28/29 medibles)
- Cuándo abre: _TODO: investigar código de la strategy_
- Quién la cierra hoy (top 3):
  - `RVOL_BREAKOUT`: 5 cierres ($+8.90)
  - `STOP_RUN`: 5 cierres ($-3.03)
  - `BOLLINGER_RSI_SENSITIVE`: 5 cierres ($+3.74)
- Propuesta de SELL signal: _TODO: pendiente revisión humana_

#### `EMA`
- Volumen: **24 opens** · P&L (como opener): $-6.39 · win rate: 50% (6/24 medibles)
- Cuándo abre: _TODO: investigar código de la strategy_
- Quién la cierra hoy (top 3):
  - `CLOSE_ALL`: 7 cierres ($+0.00)
  - `HH_LL`: 5 cierres ($+0.00)
  - `STOP_RUN`: 3 cierres ($-0.64)
- Propuesta de SELL signal: _TODO: pendiente revisión humana_

#### `VOL_REVERSAL_BAR`
- Volumen: **24 opens** · P&L (como opener): $+127.63 · win rate: 65% (23/24 medibles)
- Cuándo abre: _TODO: investigar código de la strategy_
- Quién la cierra hoy (top 3):
  - `RVOL_BREAKOUT`: 8 cierres ($+13.36)
  - `CLOSE_ALL`: 3 cierres ($+56.36)
  - `STOP_RUN`: 3 cierres ($+42.91)
- Propuesta de SELL signal: _TODO: pendiente revisión humana_

#### `VW_MACD`
- Volumen: **21 opens** · P&L (como opener): $-13.19 · win rate: 50% (20/21 medibles)
- Cuándo abre: _TODO: investigar código de la strategy_
- Quién la cierra hoy (top 3):
  - `RVOL_BREAKOUT`: 7 cierres ($+3.75)
  - `VIX_MEAN_REV`: 3 cierres ($+0.03)
  - `VWAP_ZSCORE`: 3 cierres ($-18.03)
- Propuesta de SELL signal: _TODO: pendiente revisión humana_

#### `SUPERTREND`
- Volumen: **7 opens** · P&L (como opener): $+12.51 · win rate: 50% (2/7 medibles)
- Cuándo abre: _TODO: investigar código de la strategy_
- Quién la cierra hoy (top 3):
  - `RVOL_BREAKOUT`: 3 cierres ($-3.42)
  - `HA_CLOUD`: 1 cierres ($+0.00)
  - `MACD_BB`: 1 cierres ($+0.00)
- Propuesta de SELL signal: _TODO: pendiente revisión humana_

#### `MACD_ACCEL`
- Volumen: **7 opens** · P&L (como opener): $+1.56 · win rate: 83% (6/7 medibles)
- Cuándo abre: _TODO: investigar código de la strategy_
- Quién la cierra hoy (top 3):
  - `BOLLINGER_RSI_SENSITIVE`: 2 cierres ($+0.98)
  - `RVOL_BREAKOUT`: 1 cierres ($-0.39)
  - `VWAP_ZSCORE`: 1 cierres ($+0.50)
- Propuesta de SELL signal: _TODO: pendiente revisión humana_

#### `VWAP_MOMENTUM`
- Volumen: **6 opens** · P&L (como opener): $+1.35 · win rate: 50% (2/6 medibles)
- Cuándo abre: _TODO: investigar código de la strategy_
- Quién la cierra hoy (top 3):
  - `OPENING_DRIVE`: 1 cierres ($+1.35)
  - `TICK_TRIN_FADE`: 1 cierres ($+0.00)
- Propuesta de SELL signal: _TODO: pendiente revisión humana_

#### `MOMENTUM_SCORE`
- Volumen: **6 opens** · P&L (como opener): $-3.03 · win rate: 0% (4/6 medibles)
- Cuándo abre: _TODO: investigar código de la strategy_
- Quién la cierra hoy (top 3):
  - `TICK_TRIN_FADE`: 3 cierres ($-3.03)
  - `CLOSE_ALL`: 1 cierres ($+0.00)
- Propuesta de SELL signal: _TODO: pendiente revisión humana_

#### `COMBO7_CAMPBELL`
- Volumen: **5 opens** · P&L (como opener): $+4.50 · win rate: 100% (1/5 medibles)
- Cuándo abre: _TODO: investigar código de la strategy_
- Quién la cierra hoy (top 3):
  - `RVOL_BREAKOUT`: 1 cierres ($+4.50)
- Propuesta de SELL signal: _TODO: pendiente revisión humana_


#### Exit-only (necesitan BUY signal)

#### `RVOL_BREAKOUT`
- Volumen: **65 closes** · P&L (como closer): $+118.82 · win rate: 56% (61/65 medibles)
- Cuándo cierra: _TODO: investigar código de la strategy_
- Qué cierra hoy (top 3 openers):
  - `SQUEEZE`: 20 pairs ($+6.25)
  - `VOL_REVERSAL_BAR`: 8 pairs ($+13.36)
  - `VW_MACD`: 7 pairs ($+3.75)
- Propuesta de BUY signal: _TODO: pendiente revisión humana_

#### `VWAP_ZSCORE`
- Volumen: **35 closes** · P&L (como closer): $-25.20 · win rate: 29% (31/35 medibles)
- Cuándo cierra: _TODO: investigar código de la strategy_
- Qué cierra hoy (top 3 openers):
  - `SQUEEZE`: 8 pairs ($-0.88)
  - `RVOL_BREAKOUT`: 5 pairs ($+1.44)
  - `OBV_MTF`: 5 pairs ($-5.81)
- Propuesta de BUY signal: _TODO: pendiente revisión humana_

#### `STOP_RUN`
- Volumen: **26 closes** · P&L (como closer): $+43.22 · win rate: 52% (23/26 medibles)
- Cuándo cierra: _TODO: investigar código de la strategy_
- Qué cierra hoy (top 3 openers):
  - `SQUEEZE`: 7 pairs ($-4.37)
  - `VOLUME_BREAKOUT`: 5 pairs ($-3.03)
  - `EMA`: 3 pairs ($-0.64)
- Propuesta de BUY signal: _TODO: pendiente revisión humana_

#### `VWAP+RSI`
- Volumen: **22 closes** · P&L (como closer): $+5.22 · win rate: 100% (3/22 medibles)
- Cuándo cierra: _TODO: investigar código de la strategy_
- Qué cierra hoy (top 3 openers):
  - `SQUEEZE`: 18 pairs ($+0.00)
  - `VELA_PIVOT`: 1 pairs ($+0.00)
  - `EMA_3_8`: 1 pairs ($+0.16)
- Propuesta de BUY signal: _TODO: pendiente revisión humana_

#### `TICK_TRIN_FADE`
- Volumen: **16 closes** · P&L (como closer): $-5.08 · win rate: 31% (16/16 medibles)
- Cuándo cierra: _TODO: investigar código de la strategy_
- Qué cierra hoy (top 3 openers):
  - `HH_LL`: 4 pairs ($-0.53)
  - `MOMENTUM_SCORE`: 3 pairs ($-3.03)
  - `RVOL_BREAKOUT`: 2 pairs ($-1.47)
- Propuesta de BUY signal: _TODO: pendiente revisión humana_

#### `OPENING_DRIVE`
- Volumen: **12 closes** · P&L (como closer): $-2.48 · win rate: 45% (11/12 medibles)
- Cuándo cierra: _TODO: investigar código de la strategy_
- Qué cierra hoy (top 3 openers):
  - `MACD_BB`: 4 pairs ($+0.07)
  - `SQUEEZE`: 2 pairs ($-1.54)
  - `SUPERTREND`: 1 pairs ($+0.00)
- Propuesta de BUY signal: _TODO: pendiente revisión humana_

#### `ANCHOR_VWAP`
- Volumen: **11 closes** · P&L (como closer): $+5.35 · win rate: 64% (11/11 medibles)
- Cuándo cierra: _TODO: investigar código de la strategy_
- Qué cierra hoy (top 3 openers):
  - `VOLUME_BREAKOUT`: 4 pairs ($+4.66)
  - `BOLLINGER`: 2 pairs ($+2.26)
  - `RVOL_BREAKOUT`: 1 pairs ($+0.98)
- Propuesta de BUY signal: _TODO: pendiente revisión humana_


#### Complete (revisar performance)

#### `TEST` — verdict: **optimize**
- Opens: 27 · P&L (opener): $+0.00 · avg $+0.00
- Closes: 27 · P&L (closer): $+0.00 · avg $+0.00
- Intra (opener=closer): 27 pairs · P&L: $+0.00
- Cross: 0 como opener · 0 como closer

#### `HH_LL` — verdict: **optimize**
- Opens: 17 · P&L (opener): $-1.45 · avg $-0.16
- Closes: 10 · P&L (closer): $-5.05 · avg $-1.68
- Intra (opener=closer): 2 pairs · P&L: $-3.16
- Cross: 15 como opener · 7 como closer

#### `OBV_MTF` — verdict: **no-intra-data**
- Opens: 15 · P&L (opener): $+10.88 · avg $+0.78
- Closes: 3 · P&L (closer): $+4.20 · avg $+1.40
- Intra (opener=closer): 0 pairs · P&L: $+0.00
- Cross: 14 como opener · 3 como closer

#### `BOLLINGER` — verdict: **optimize**
- Opens: 11 · P&L (opener): $+61.19 · avg $+7.65
- Closes: 32 · P&L (closer): $+56.34 · avg $+9.39
- Intra (opener=closer): 2 pairs · P&L: $+0.00
- Cross: 8 como opener · 30 como closer

#### `EMA_3_8` — verdict: **no-intra-data**
- Opens: 10 · P&L (opener): $+2.48 · avg $+0.31
- Closes: 4 · P&L (closer): $-1.77 · avg $-0.44
- Intra (opener=closer): 0 pairs · P&L: $+0.00
- Cross: 8 como opener · 4 como closer

#### `DONCHIAN_TURTLE` — verdict: **no-intra-data**
- Opens: 8 · P&L (opener): $-10.38 · avg $-1.48
- Closes: 2 · P&L (closer): $-7.66 · avg $-3.83
- Intra (opener=closer): 0 pairs · P&L: $+0.00
- Cross: 7 como opener · 2 como closer

#### `VELA_PIVOT` — verdict: **no-intra-data**
- Opens: 7 · P&L (opener): $+37.16 · avg $+9.29
- Closes: 3 · P&L (closer): $+0.00 · avg $+0.00
- Intra (opener=closer): 0 pairs · P&L: $+0.00
- Cross: 7 como opener · 3 como closer

#### `RSI_SMA200` — verdict: **no-intra-data**
- Opens: 7 · P&L (opener): $+7.47 · avg $+1.24
- Closes: 13 · P&L (closer): $-1.88 · avg $-0.63
- Intra (opener=closer): 0 pairs · P&L: $+0.00
- Cross: 6 como opener · 8 como closer

#### `MACD_BB` — verdict: **no-intra-data**
- Opens: 5 · P&L (opener): $+2.17 · avg $+0.43
- Closes: 1 · P&L (closer): $+0.00 · avg $+0.00
- Intra (opener=closer): 0 pairs · P&L: $+0.00
- Cross: 5 como opener · 1 como closer

#### `TSV` — verdict: **no-intra-data**
- Opens: 5 · P&L (opener): $-12.66 · avg $-2.53
- Closes: 6 · P&L (closer): $+131.82 · avg $+21.97
- Intra (opener=closer): 0 pairs · P&L: $+0.00
- Cross: 5 como opener · 6 como closer

#### `BOLLINGER_RSI_SENSITIVE` — verdict: **no-intra-data**
- Opens: 4 · P&L (opener): $-0.87 · avg $-0.87
- Closes: 20 · P&L (closer): $+6.24 · avg $+0.31
- Intra (opener=closer): 0 pairs · P&L: $+0.00
- Cross: 1 como opener · 20 como closer


### Distribución de cierres forzados

- System forced (RISK_WATCHDOG / CLOSE_ALL / SL / TP / auto-close): **35**
- Claude override (claude_bot / claude_high / claude_medium): **0**
- `<empty>` closer (strategy field vacío): **0**
- Total pairs: **365**

---

## Bot: V2 Options

### Strategies clasificadas

| Strategy | Type | Opens | Closes | P&L (opener) | P&L (closer) | Intra | Open ratio |
|---|---|---|---|---|---|---|---|
| `bsm_mispricing` | 🟢 complete | 619 | 144 | $-137.50 | $+108.50 | 139($+108.00) | 0.81 |
| `put_call_parity` | 🟢 complete | 314 | 88 | $+159.00 | $+35.00 | 86($+35.00) | 0.78 |
| `iv_skew_jump` | 🟢 complete | 99 | 19 | $+142.00 | $-41.00 | 9($+0.00) | 0.84 |
| `butterfly_arbitrage` | 🟢 complete | 27 | 6 | $+0.00 | $+0.00 | 6($+0.00) | 0.82 |
| `BSM_MISPRICING` | 🟡 entry_only | 371 | 0 | $-35.00 | $+0.00 | 0($+0.00) | 1.00 |
| `PUT_CALL_PARITY` | 🟡 entry_only | 283 | 0 | $+457.50 | $+0.00 | 0($+0.00) | 1.00 |
| `IV_SKEW_JUMP` | 🟡 entry_only | 81 | 0 | $-95.00 | $+0.00 | 0($+0.00) | 1.00 |
| `BUTTERFLY_ARBITRAGE` | 🟡 entry_only | 26 | 0 | $+0.00 | $+0.00 | 0($+0.00) | 1.00 |
| `calendar_iv_gap` | 🟡 entry_only | 10 | 0 | $+0.00 | $+0.00 | 0($+0.00) | 1.00 |
| `<empty>` | 🔴 exit_only | 0 | 298 | $+0.00 | $+273.50 | 0($+0.00) | 0.00 |
| `CALENDAR_IV_GAP` | ⚪ minimal | 4 | 0 | $+10.00 | $+0.00 | 0($+0.00) | 1.00 |
| `CLAUDE_MEDIUM` | ⚫ system | 54 | 0 | $+41.00 | $+0.00 | 0($+0.00) | 1.00 |
| `claude_medium` | ⚫ system | 35 | 144 | $-10.00 | $-359.00 | 14($+0.00) | 0.20 |
| `claude_high` | ⚫ system | 8 | 73 | $+0.00 | $+515.00 | 3($+0.00) | 0.10 |

### Findings por type

#### Entry-only (necesitan SELL signal)

#### `BSM_MISPRICING`
- Volumen: **371 opens** · P&L (como opener): $-35.00 · win rate: 29% (93/371 medibles)
- Cuándo abre: _TODO: investigar código de la strategy_
- Quién la cierra hoy (top 3):
  - `<empty>`: 85 cierres ($-42.00)
  - `bsm_mispricing`: 5 cierres ($+0.50)
  - `iv_skew_jump`: 3 cierres ($+0.00)
- Propuesta de SELL signal: _TODO: pendiente revisión humana_

#### `PUT_CALL_PARITY`
- Volumen: **283 opens** · P&L (como opener): $+457.50 · win rate: 11% (79/283 medibles)
- Cuándo abre: _TODO: investigar código de la strategy_
- Quién la cierra hoy (top 3):
  - `<empty>`: 78 cierres ($+457.50)
  - `claude_medium`: 3 cierres ($+0.00)
  - `put_call_parity`: 2 cierres ($+0.00)
- Propuesta de SELL signal: _TODO: pendiente revisión humana_

#### `IV_SKEW_JUMP`
- Volumen: **81 opens** · P&L (como opener): $-95.00 · win rate: 4% (45/81 medibles)
- Cuándo abre: _TODO: investigar código de la strategy_
- Quién la cierra hoy (top 3):
  - `<empty>`: 41 cierres ($-95.00)
  - `claude_medium`: 4 cierres ($+0.00)
- Propuesta de SELL signal: _TODO: pendiente revisión humana_

#### `BUTTERFLY_ARBITRAGE`
- Volumen: **26 opens** · P&L (como opener): $+0.00 · win rate: 0% (14/26 medibles)
- Cuándo abre: _TODO: investigar código de la strategy_
- Quién la cierra hoy (top 3):
  - `<empty>`: 13 cierres ($+0.00)
  - `claude_medium`: 1 cierres ($+0.00)
- Propuesta de SELL signal: _TODO: pendiente revisión humana_

#### `calendar_iv_gap`
- Volumen: **10 opens** · P&L (como opener): $+0.00 · win rate: 0% (3/10 medibles)
- Cuándo abre: _TODO: investigar código de la strategy_
- Quién la cierra hoy (top 3):
  - `<empty>`: 3 cierres ($+0.00)
  - `claude_medium`: 1 cierres ($+0.00)
- Propuesta de SELL signal: _TODO: pendiente revisión humana_


#### Exit-only (necesitan BUY signal)

#### `<empty>`
- Volumen: **298 closes** · P&L (como closer): $+273.50 · win rate: 16% (231/298 medibles)
- Cuándo cierra: _TODO: investigar código de la strategy_
- Qué cierra hoy (top 3 openers):
  - `BSM_MISPRICING`: 85 pairs ($-42.00)
  - `PUT_CALL_PARITY`: 78 pairs ($+457.50)
  - `IV_SKEW_JUMP`: 41 pairs ($-95.00)
- Propuesta de BUY signal: _TODO: pendiente revisión humana_


#### Complete (revisar performance)

#### `bsm_mispricing` — verdict: **keep**
- Opens: 619 · P&L (opener): $-137.50 · avg $-1.24
- Closes: 144 · P&L (closer): $+108.50 · avg $+2.41
- Intra (opener=closer): 139 pairs · P&L: $+108.00
- Cross: 97 como opener · 5 como closer

#### `put_call_parity` — verdict: **keep**
- Opens: 314 · P&L (opener): $+159.00 · avg $+0.89
- Closes: 88 · P&L (closer): $+35.00 · avg $+0.42
- Intra (opener=closer): 86 pairs · P&L: $+35.00
- Cross: 119 como opener · 2 como closer

#### `iv_skew_jump` — verdict: **optimize**
- Opens: 99 · P&L (opener): $+142.00 · avg $+4.30
- Closes: 19 · P&L (closer): $-41.00 · avg $-4.10
- Intra (opener=closer): 9 pairs · P&L: $+0.00
- Cross: 32 como opener · 10 como closer

#### `butterfly_arbitrage` — verdict: **optimize**
- Opens: 27 · P&L (opener): $+0.00 · avg $+0.00
- Closes: 6 · P&L (closer): $+0.00 · avg $+0.00
- Intra (opener=closer): 6 pairs · P&L: $+0.00
- Cross: 10 como opener · 0 como closer


### Distribución de cierres forzados

- System forced (RISK_WATCHDOG / CLOSE_ALL / SL / TP / auto-close): **0**
- Claude override (claude_bot / claude_high / claude_medium): **217**
- `<empty>` closer (strategy field vacío): **298**
- Total pairs: **772**

---

## Bot: Crypto

### Strategies clasificadas

| Strategy | Type | Opens | Closes | P&L (opener) | P&L (closer) | Intra | Open ratio |
|---|---|---|---|---|---|---|---|
| `rsi_sma200` | 🟢 complete | 5070 | 8435 | $-2,693.64 | $-195.36 | 654($+1,217.40) | 0.38 |
| `squeeze` | 🟢 complete | 3865 | 3312 | $-4,019.94 | $-4,256.92 | 106($-447.74) | 0.54 |
| `ema_tsi` | 🟢 complete | 992 | 1665 | $-964.40 | $-3,079.44 | 114($-282.23) | 0.37 |
| `buy_pressure` | 🟢 complete | 813 | 223 | $-765.90 | $-161.87 | 7($-0.74) | 0.78 |
| `volume_reversal_bar` | 🟢 complete | 486 | 714 | $+41.31 | $-83.55 | 281($+0.40) | 0.41 |
| `macd_bb` | 🟢 complete | 465 | 376 | $-850.94 | $-2,337.77 | 10($-59.87) | 0.55 |
| `macd_accel` | 🟢 complete | 428 | 264 | $-167.27 | $-171.02 | 1($-2.65) | 0.62 |
| `donchian_turtle` | 🟢 complete | 345 | 305 | $-785.44 | $-23.25 | 1($-0.54) | 0.53 |
| `net_bsv` | 🟢 complete | 344 | 231 | $-174.03 | $-88.70 | 11($-4.56) | 0.60 |
| `sell_pressure` | 🟢 complete | 305 | 177 | $-215.52 | $-131.51 | 5($-2.27) | 0.63 |
| `ema_3_8` | 🟢 complete | 176 | 163 | $-123.83 | $-151.61 | 6($-15.55) | 0.52 |
| `vwap_momentum` | 🟢 complete | 170 | 103 | $-121.42 | $-109.05 | 4($-3.23) | 0.62 |
| `?` | 🟢 complete | 165 | 161 | $-428.66 | $-470.32 | 158($-443.42) | 0.51 |
| `volume_breakout` | 🟢 complete | 100 | 178 | $-207.07 | $+288.78 | 2($-0.97) | 0.36 |
| `ema_8_21` | 🟢 complete | 35 | 38 | $-23.99 | $-31.95 | 0($+0.00) | 0.48 |
| `rvol_breakout` | 🟡 entry_only | 1141 | 0 | $-410.48 | $+0.00 | 0($+0.00) | 1.00 |
| `tsv` | 🟡 entry_only | 933 | 0 | $-480.38 | $+0.00 | 0($+0.00) | 1.00 |
| `vw_macd` | 🟡 entry_only | 834 | 0 | $-882.30 | $+0.00 | 0($+0.00) | 1.00 |
| `obv_mtf` | 🟡 entry_only | 608 | 0 | $-194.53 | $+0.00 | 0($+0.00) | 1.00 |
| `vwap_zscore` | 🟡 entry_only | 186 | 0 | $-146.38 | $+0.00 | 0($+0.00) | 1.00 |
| `stop_run` | 🟡 entry_only | 126 | 0 | $-149.30 | $+0.00 | 0($+0.00) | 1.00 |
| `squeeze,hh_ll` | 🟡 entry_only | 10 | 1 | $+165.23 | $+0.00 | 0($+0.00) | 0.91 |
| `hh_ll` | 🔴 exit_only | 179 | 1571 | $+65.02 | $-2,930.30 | 72($-96.79) | 0.10 |
| `rsi_sma200,hh_ll` | 🔴 exit_only | 0 | 6 | $+0.00 | $+176.85 | 0($+0.00) | 0.00 |
| `manual_command` | 🔴 exit_only | 0 | 12 | $+0.00 | $+128.00 | 0($+0.00) | 0.00 |
| `squeeze,hh_ll,ema_tsi` | ⚪ minimal | 0 | 3 | $+0.00 | $+0.00 | 0($+0.00) | 0.00 |
| `squeeze,ema_tsi` | ⚪ minimal | 0 | 4 | $+0.00 | $+0.00 | 0($+0.00) | 0.00 |
| `macd_bb,hh_ll` | ⚪ minimal | 0 | 1 | $+0.00 | $-0.68 | 0($+0.00) | 0.00 |
| `macd_bb,squeeze,hh_ll,ema_tsi` | ⚪ minimal | 0 | 1 | $+0.00 | $+0.00 | 0($+0.00) | 0.00 |
| `macd_bb,ema_tsi` | ⚪ minimal | 0 | 1 | $+0.00 | $+3.90 | 0($+0.00) | 0.00 |
| `rsi_sma200,macd_bb` | ⚪ minimal | 0 | 1 | $+0.00 | $+0.00 | 0($+0.00) | 0.00 |
| `claude_bot` | ⚫ system | 251 | 76 | $-119.57 | $-113.79 | 27($-44.06) | 0.77 |
| `auto_close` | ⚫ system | 0 | 13 | $+0.00 | $+86.14 | 0($+0.00) | 0.00 |

### Findings por type

#### Entry-only (necesitan SELL signal)

#### `rvol_breakout`
- Volumen: **1141 opens** · P&L (como opener): $-410.48 · win rate: 33% (1141/1141 medibles)
- Cuándo abre: _TODO: investigar código de la strategy_
- Quién la cierra hoy (top 3):
  - `rsi_sma200`: 952 cierres ($-210.51)
  - `volume_reversal_bar`: 89 cierres ($-44.18)
  - `ema_tsi`: 81 cierres ($-212.56)
- Propuesta de SELL signal: _TODO: pendiente revisión humana_

#### `tsv`
- Volumen: **933 opens** · P&L (como opener): $-480.38 · win rate: 40% (933/933 medibles)
- Cuándo abre: _TODO: investigar código de la strategy_
- Quién la cierra hoy (top 3):
  - `rsi_sma200`: 424 cierres ($+402.70)
  - `ema_tsi`: 180 cierres ($-423.50)
  - `volume_reversal_bar`: 123 cierres ($+16.64)
- Propuesta de SELL signal: _TODO: pendiente revisión humana_

#### `vw_macd`
- Volumen: **834 opens** · P&L (como opener): $-882.30 · win rate: 30% (834/834 medibles)
- Cuándo abre: _TODO: investigar código de la strategy_
- Quién la cierra hoy (top 3):
  - `squeeze`: 287 cierres ($-638.39)
  - `hh_ll`: 236 cierres ($-421.17)
  - `rsi_sma200`: 111 cierres ($+416.90)
- Propuesta de SELL signal: _TODO: pendiente revisión humana_

#### `obv_mtf`
- Volumen: **608 opens** · P&L (como opener): $-194.53 · win rate: 36% (608/608 medibles)
- Cuándo abre: _TODO: investigar código de la strategy_
- Quién la cierra hoy (top 3):
  - `rsi_sma200`: 568 cierres ($-87.85)
  - `volume_reversal_bar`: 22 cierres ($-65.27)
  - `ema_tsi`: 12 cierres ($-31.88)
- Propuesta de SELL signal: _TODO: pendiente revisión humana_

#### `vwap_zscore`
- Volumen: **186 opens** · P&L (como opener): $-146.38 · win rate: 42% (186/186 medibles)
- Cuándo abre: _TODO: investigar código de la strategy_
- Quién la cierra hoy (top 3):
  - `squeeze`: 127 cierres ($-208.88)
  - `volume_reversal_bar`: 32 cierres ($+58.96)
  - `rsi_sma200`: 19 cierres ($+18.95)
- Propuesta de SELL signal: _TODO: pendiente revisión humana_

#### `stop_run`
- Volumen: **126 opens** · P&L (como opener): $-149.30 · win rate: 37% (126/126 medibles)
- Cuándo abre: _TODO: investigar código de la strategy_
- Quién la cierra hoy (top 3):
  - `volume_reversal_bar`: 32 cierres ($-126.54)
  - `rsi_sma200`: 25 cierres ($-39.18)
  - `squeeze`: 18 cierres ($-26.46)
- Propuesta de SELL signal: _TODO: pendiente revisión humana_

#### `squeeze,hh_ll`
- Volumen: **10 opens** · P&L (como opener): $+165.23 · win rate: 80% (10/10 medibles)
- Cuándo abre: _TODO: investigar código de la strategy_
- Quién la cierra hoy (top 3):
  - `rsi_sma200,hh_ll`: 6 cierres ($+176.85)
  - `?`: 2 cierres ($-14.84)
  - `macd_bb,ema_tsi`: 1 cierres ($+3.90)
- Propuesta de SELL signal: _TODO: pendiente revisión humana_


#### Exit-only (necesitan BUY signal)

#### `hh_ll`
- Volumen: **1571 closes** · P&L (como closer): $-2,930.30 · win rate: 20% (1571/1571 medibles)
- Cuándo cierra: _TODO: investigar código de la strategy_
- Qué cierra hoy (top 3 openers):
  - `squeeze`: 689 pairs ($-1,055.76)
  - `vw_macd`: 236 pairs ($-421.17)
  - `rsi_sma200`: 116 pairs ($-109.13)
- Propuesta de BUY signal: _TODO: pendiente revisión humana_

#### `manual_command`
- Volumen: **12 closes** · P&L (como closer): $+128.00 · win rate: 67% (12/12 medibles)
- Cuándo cierra: _TODO: investigar código de la strategy_
- Qué cierra hoy (top 3 openers):
  - `rsi_sma200`: 4 pairs ($+12.11)
  - `tsv`: 4 pairs ($+37.04)
  - `rvol_breakout`: 3 pairs ($+64.58)
- Propuesta de BUY signal: _TODO: pendiente revisión humana_

#### `rsi_sma200,hh_ll`
- Volumen: **6 closes** · P&L (como closer): $+176.85 · win rate: 100% (6/6 medibles)
- Cuándo cierra: _TODO: investigar código de la strategy_
- Qué cierra hoy (top 3 openers):
  - `squeeze,hh_ll`: 6 pairs ($+176.85)
- Propuesta de BUY signal: _TODO: pendiente revisión humana_


#### Complete (revisar performance)

#### `rsi_sma200` — verdict: **keep**
- Opens: 5070 · P&L (opener): $-2,693.64 · avg $-0.53
- Closes: 8435 · P&L (closer): $-195.36 · avg $-0.02
- Intra (opener=closer): 654 pairs · P&L: $+1,217.40
- Cross: 4415 como opener · 7781 como closer

#### `squeeze` — verdict: **kill**
- Opens: 3865 · P&L (opener): $-4,019.94 · avg $-1.04
- Closes: 3312 · P&L (closer): $-4,256.92 · avg $-1.29
- Intra (opener=closer): 106 pairs · P&L: $-447.74
- Cross: 3759 como opener · 3206 como closer

#### `ema_tsi` — verdict: **kill**
- Opens: 992 · P&L (opener): $-964.40 · avg $-0.97
- Closes: 1665 · P&L (closer): $-3,079.44 · avg $-1.85
- Intra (opener=closer): 114 pairs · P&L: $-282.23
- Cross: 878 como opener · 1551 como closer

#### `buy_pressure` — verdict: **optimize**
- Opens: 813 · P&L (opener): $-765.90 · avg $-0.94
- Closes: 223 · P&L (closer): $-161.87 · avg $-0.73
- Intra (opener=closer): 7 pairs · P&L: $-0.74
- Cross: 806 como opener · 216 como closer

#### `volume_reversal_bar` — verdict: **keep**
- Opens: 486 · P&L (opener): $+41.31 · avg $+0.08
- Closes: 714 · P&L (closer): $-83.55 · avg $-0.12
- Intra (opener=closer): 281 pairs · P&L: $+0.40
- Cross: 205 como opener · 433 como closer

#### `macd_bb` — verdict: **kill**
- Opens: 465 · P&L (opener): $-850.94 · avg $-1.83
- Closes: 376 · P&L (closer): $-2,337.77 · avg $-6.22
- Intra (opener=closer): 10 pairs · P&L: $-59.87
- Cross: 455 como opener · 366 como closer

#### `macd_accel` — verdict: **optimize**
- Opens: 428 · P&L (opener): $-167.27 · avg $-0.39
- Closes: 264 · P&L (closer): $-171.02 · avg $-0.65
- Intra (opener=closer): 1 pairs · P&L: $-2.65
- Cross: 427 como opener · 263 como closer

#### `donchian_turtle` — verdict: **optimize**
- Opens: 345 · P&L (opener): $-785.44 · avg $-2.28
- Closes: 305 · P&L (closer): $-23.25 · avg $-0.08
- Intra (opener=closer): 1 pairs · P&L: $-0.54
- Cross: 344 como opener · 304 como closer

#### `net_bsv` — verdict: **optimize**
- Opens: 344 · P&L (opener): $-174.03 · avg $-0.51
- Closes: 231 · P&L (closer): $-88.70 · avg $-0.38
- Intra (opener=closer): 11 pairs · P&L: $-4.56
- Cross: 333 como opener · 220 como closer

#### `sell_pressure` — verdict: **optimize**
- Opens: 305 · P&L (opener): $-215.52 · avg $-0.71
- Closes: 177 · P&L (closer): $-131.51 · avg $-0.74
- Intra (opener=closer): 5 pairs · P&L: $-2.27
- Cross: 300 como opener · 172 como closer

#### `ema_3_8` — verdict: **optimize**
- Opens: 176 · P&L (opener): $-123.83 · avg $-0.70
- Closes: 163 · P&L (closer): $-151.61 · avg $-0.93
- Intra (opener=closer): 6 pairs · P&L: $-15.55
- Cross: 170 como opener · 157 como closer

#### `vwap_momentum` — verdict: **optimize**
- Opens: 170 · P&L (opener): $-121.42 · avg $-0.71
- Closes: 103 · P&L (closer): $-109.05 · avg $-1.06
- Intra (opener=closer): 4 pairs · P&L: $-3.23
- Cross: 166 como opener · 99 como closer

#### `?` — verdict: **kill**
- Opens: 165 · P&L (opener): $-428.66 · avg $-2.60
- Closes: 161 · P&L (closer): $-470.32 · avg $-2.92
- Intra (opener=closer): 158 pairs · P&L: $-443.42
- Cross: 7 como opener · 3 como closer

#### `volume_breakout` — verdict: **optimize**
- Opens: 100 · P&L (opener): $-207.07 · avg $-2.07
- Closes: 178 · P&L (closer): $+288.78 · avg $+1.62
- Intra (opener=closer): 2 pairs · P&L: $-0.97
- Cross: 98 como opener · 176 como closer

#### `ema_8_21` — verdict: **no-intra-data**
- Opens: 35 · P&L (opener): $-23.99 · avg $-0.69
- Closes: 38 · P&L (closer): $-31.95 · avg $-0.84
- Intra (opener=closer): 0 pairs · P&L: $+0.00
- Cross: 35 como opener · 38 como closer


### Distribución de cierres forzados

- System forced (RISK_WATCHDOG / CLOSE_ALL / SL / TP / auto-close): **13**
- Claude override (claude_bot / claude_high / claude_medium): **75**
- `<empty>` closer (strategy field vacío): **0**
- Total pairs: **18024**

---


---

## DECISIONES PENDIENTES — Para que Juan marque

Por cada strategy, marcá la acción en la columna 'Decisión'.

Valores válidos:
- `keep_complete` — mantener tal como está (solo para complete con verdict=keep)
- `add_SELL` — implementar SELL signal nueva (para entry_only)
- `add_BUY` — implementar BUY signal nueva (para exit_only)
- `kill` — eliminar la strategy
- `merge:<otra>` — fusionar con otra strategy (ej: `merge:bsm_mispricing` para UPPERCASE)
- `investigate` — investigar antes de decidir
- `system` — preservar como cierre del sistema, no es strategy real


### V1 Stock

| Strategy | Type | Opens | Closes | Intra P&L | Decisión |
|---|---|---|---|---|---|
| `TEST` | 🟢 complete | 27 | 27 | $+0.00 | _____ |
| `HH_LL` | 🟢 complete | 17 | 10 | $-3.16 | _____ |
| `OBV_MTF` | 🟢 complete | 15 | 3 | $+0.00 | _____ |
| `BOLLINGER` | 🟢 complete | 11 | 32 | $+0.00 | _____ |
| `EMA_3_8` | 🟢 complete | 10 | 4 | $+0.00 | _____ |
| `DONCHIAN_TURTLE` | 🟢 complete | 8 | 2 | $+0.00 | _____ |
| `VELA_PIVOT` | 🟢 complete | 7 | 3 | $+0.00 | _____ |
| `RSI_SMA200` | 🟢 complete | 7 | 13 | $+0.00 | _____ |
| `MACD_BB` | 🟢 complete | 5 | 1 | $+0.00 | _____ |
| `TSV` | 🟢 complete | 5 | 6 | $+0.00 | _____ |
| `BOLLINGER_RSI_SENSITIVE` | 🟢 complete | 4 | 20 | $+0.00 | _____ |
| `SQUEEZE` | 🟡 entry_only | 102 | 2 | $+2.28 | _____ |
| `VOLUME_BREAKOUT` | 🟡 entry_only | 29 | 2 | $+0.00 | _____ |
| `EMA` | 🟡 entry_only | 24 | 3 | $+0.00 | _____ |
| `VOL_REVERSAL_BAR` | 🟡 entry_only | 24 | 2 | $-0.16 | _____ |
| `VW_MACD` | 🟡 entry_only | 21 | 1 | $+0.00 | _____ |
| `SUPERTREND` | 🟡 entry_only | 7 | 0 | $+0.00 | _____ |
| `MACD_ACCEL` | 🟡 entry_only | 7 | 0 | $+0.00 | _____ |
| `VWAP_MOMENTUM` | 🟡 entry_only | 6 | 1 | $+0.00 | _____ |
| `MOMENTUM_SCORE` | 🟡 entry_only | 6 | 0 | $+0.00 | _____ |
| `COMBO7_CAMPBELL` | 🟡 entry_only | 5 | 0 | $+0.00 | _____ |
| `RVOL_BREAKOUT` | 🔴 exit_only | 11 | 65 | $-1.34 | _____ |
| `STOP_RUN` | 🔴 exit_only | 4 | 26 | $-0.69 | _____ |
| `VWAP+RSI` | 🔴 exit_only | 1 | 22 | $+0.00 | _____ |
| `OPENING_DRIVE` | 🔴 exit_only | 0 | 12 | $+0.00 | _____ |
| `VWAP_ZSCORE` | 🔴 exit_only | 0 | 35 | $+0.00 | _____ |
| `TICK_TRIN_FADE` | 🔴 exit_only | 0 | 16 | $+0.00 | _____ |
| `ANCHOR_VWAP` | 🔴 exit_only | 0 | 11 | $+0.00 | _____ |
| `NET_BSV` | ⚪ minimal | 4 | 0 | $+0.00 | _____ |
| `BUY_PRESSURE` | ⚪ minimal | 4 | 0 | $+0.00 | _____ |
| `ORB_V3` | ⚪ minimal | 4 | 2 | $+0.00 | _____ |
| `SELL_PRESSURE` | ⚪ minimal | 3 | 0 | $+0.00 | _____ |
| `EMA_8_21` | ⚪ minimal | 3 | 0 | $+0.00 | _____ |
| `ORB` | ⚪ minimal | 2 | 2 | $+0.00 | _____ |
| `GAP` | ⚪ minimal | 2 | 2 | $+0.00 | _____ |
| `VWAP_RSI` | ⚪ minimal | 2 | 2 | $+0.00 | _____ |
| `EMA_TSI` | ⚪ minimal | 2 | 2 | $+0.00 | _____ |
| `HA_CLOUD` | ⚪ minimal | 1 | 4 | $+0.00 | _____ |
| `COMBO3_NINO_SQUEEZE` | ⚪ minimal | 1 | 0 | $+0.00 | _____ |
| `VIX_MEAN_REV` | ⚪ minimal | 0 | 3 | $+0.00 | _____ |
| `CLOSE_ALL` | ⚫ system | 0 | 35 | $+0.00 | _____ |
| `RISK_WATCHDOG` | ⚫ system | 0 | 7 | $+0.00 | _____ |

### V2 Options

| Strategy | Type | Opens | Closes | Intra P&L | Decisión |
|---|---|---|---|---|---|
| `bsm_mispricing` | 🟢 complete | 619 | 144 | $+108.00 | _____ |
| `put_call_parity` | 🟢 complete | 314 | 88 | $+35.00 | _____ |
| `iv_skew_jump` | 🟢 complete | 99 | 19 | $+0.00 | _____ |
| `butterfly_arbitrage` | 🟢 complete | 27 | 6 | $+0.00 | _____ |
| `BSM_MISPRICING` | 🟡 entry_only | 371 | 0 | $+0.00 | _____ |
| `PUT_CALL_PARITY` | 🟡 entry_only | 283 | 0 | $+0.00 | _____ |
| `IV_SKEW_JUMP` | 🟡 entry_only | 81 | 0 | $+0.00 | _____ |
| `BUTTERFLY_ARBITRAGE` | 🟡 entry_only | 26 | 0 | $+0.00 | _____ |
| `calendar_iv_gap` | 🟡 entry_only | 10 | 0 | $+0.00 | _____ |
| `<empty>` | 🔴 exit_only | 0 | 298 | $+0.00 | _____ |
| `CALENDAR_IV_GAP` | ⚪ minimal | 4 | 0 | $+0.00 | _____ |
| `CLAUDE_MEDIUM` | ⚫ system | 54 | 0 | $+0.00 | _____ |
| `claude_medium` | ⚫ system | 35 | 144 | $+0.00 | _____ |
| `claude_high` | ⚫ system | 8 | 73 | $+0.00 | _____ |

### Crypto

| Strategy | Type | Opens | Closes | Intra P&L | Decisión |
|---|---|---|---|---|---|
| `rsi_sma200` | 🟢 complete | 5070 | 8435 | $+1,217.40 | _____ |
| `squeeze` | 🟢 complete | 3865 | 3312 | $-447.74 | _____ |
| `ema_tsi` | 🟢 complete | 992 | 1665 | $-282.23 | _____ |
| `buy_pressure` | 🟢 complete | 813 | 223 | $-0.74 | _____ |
| `volume_reversal_bar` | 🟢 complete | 486 | 714 | $+0.40 | _____ |
| `macd_bb` | 🟢 complete | 465 | 376 | $-59.87 | _____ |
| `macd_accel` | 🟢 complete | 428 | 264 | $-2.65 | _____ |
| `donchian_turtle` | 🟢 complete | 345 | 305 | $-0.54 | _____ |
| `net_bsv` | 🟢 complete | 344 | 231 | $-4.56 | _____ |
| `sell_pressure` | 🟢 complete | 305 | 177 | $-2.27 | _____ |
| `ema_3_8` | 🟢 complete | 176 | 163 | $-15.55 | _____ |
| `vwap_momentum` | 🟢 complete | 170 | 103 | $-3.23 | _____ |
| `?` | 🟢 complete | 165 | 161 | $-443.42 | _____ |
| `volume_breakout` | 🟢 complete | 100 | 178 | $-0.97 | _____ |
| `ema_8_21` | 🟢 complete | 35 | 38 | $+0.00 | _____ |
| `rvol_breakout` | 🟡 entry_only | 1141 | 0 | $+0.00 | _____ |
| `tsv` | 🟡 entry_only | 933 | 0 | $+0.00 | _____ |
| `vw_macd` | 🟡 entry_only | 834 | 0 | $+0.00 | _____ |
| `obv_mtf` | 🟡 entry_only | 608 | 0 | $+0.00 | _____ |
| `vwap_zscore` | 🟡 entry_only | 186 | 0 | $+0.00 | _____ |
| `stop_run` | 🟡 entry_only | 126 | 0 | $+0.00 | _____ |
| `squeeze,hh_ll` | 🟡 entry_only | 10 | 1 | $+0.00 | _____ |
| `hh_ll` | 🔴 exit_only | 179 | 1571 | $-96.79 | _____ |
| `rsi_sma200,hh_ll` | 🔴 exit_only | 0 | 6 | $+0.00 | _____ |
| `manual_command` | 🔴 exit_only | 0 | 12 | $+0.00 | _____ |
| `squeeze,hh_ll,ema_tsi` | ⚪ minimal | 0 | 3 | $+0.00 | _____ |
| `squeeze,ema_tsi` | ⚪ minimal | 0 | 4 | $+0.00 | _____ |
| `macd_bb,hh_ll` | ⚪ minimal | 0 | 1 | $+0.00 | _____ |
| `macd_bb,squeeze,hh_ll,ema_tsi` | ⚪ minimal | 0 | 1 | $+0.00 | _____ |
| `macd_bb,ema_tsi` | ⚪ minimal | 0 | 1 | $+0.00 | _____ |
| `rsi_sma200,macd_bb` | ⚪ minimal | 0 | 1 | $+0.00 | _____ |
| `claude_bot` | ⚫ system | 251 | 76 | $-44.06 | _____ |
| `auto_close` | ⚫ system | 0 | 13 | $+0.00 | _____ |