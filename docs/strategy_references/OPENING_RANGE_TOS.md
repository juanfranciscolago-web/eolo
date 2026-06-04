# Opening Range Breakout — TOS Reference

Source: Juan ThinkOrSwim study, 2026-06-04 (sprint PIVOT-PLUS-OR).

## Lógica

- **Opening Range (OR)** = `high` / `low` de los primeros 6 minutos del session open (9:30-9:36 ET).
- **Niveles plotted en TOS:**
  - ORH (green dashed)
  - ORL (red dashed)
  - OR_mid (yellow dashed)
- **Fibonacci extensions** tanto arriba como abajo del OR:
  - 1.618, 2.236, 2.618 (incluidas en MarketSnapshot)
  - Extensiones adicionales del TOS original: 3.120, 3.770, 4.236, 5.120, 6.100, 7.870
- **Breakout indicator:** arrow up cuando close cruza ABOVE ORH (con MACD + RSI confirm).
- **Targets típicos:** 3× ATR(14) y 4× ATR(14) desde el close del breakout.

## Implementación en CROP

### Cálculo (engine-side via bot snapshot)
- `backtest/opening_range.py`: `compute_opening_range()` + `classify_or_state()`.
- Tests: `backtest/tests/test_opening_range.py` (9/9).

### Wire al snapshot
- `eolo-crop/llm_gate/snapshot.py:build_market_snapshot_from_crop`:
  - Lee raw candles del `candle_buffer` via `raw_candles(ticker)`.
  - Computa OR data si hay candles del session open window.
  - Clasifica state vs `price` actual.
  - Setea fields `or_high`, `or_low`, `or_mid`, `or_width`, `or_fib_up_1618`,
    `or_fib_up_2618`, `or_fib_down_1618`, `or_fib_down_2618`, `or_state`.

### Schema engine
- `llm_engine_eolo/llm_engine/market_snapshot.py:MarketSnapshot`:
  - Campos opcionales agregados (default None).
  - `to_llm_format()` incluye sección **OPENING RANGE (TR-Juan-077)** con los niveles.

### KB
- **TR-Juan-077 [TACTICAL_PLUS]** OPENING_RANGE_GATE — guidance sobre cómo usar
  el estado del OR para la selección de structure (IC simétrico vs scaling
  direccional) y para identificar breakout vs range trading.

## States del OR

| State | Trigger | Implicación operativa |
|---|---|---|
| `in_range` | `or_low ≤ price ≤ or_high` | Range trading mañana — IC simétrico favorable |
| `breakout_up` | `or_high < price ≤ or_fib_up_1618` | Bullish breakout, escalar PUT side en dips back |
| `deep_above` | `price > or_fib_up_1618` | Bullish extendido, target siguiente fib (2.236) |
| `breakout_down` | `or_fib_down_1618 ≤ price < or_low` | Bearish breakout, escalar CALL side en bounces |
| `deep_below` | `price < or_fib_down_1618` | Bearish extendido, target siguiente fib |
| `no_data` | Sin candles del OR window cargados | No usar OR para decisión |

## Código TOS de referencia

```thinkscript
# input bars_count = 6;
# input show_above = yes;
# input show_below = yes;
#
# def session_open = SecondsTillTime(0930);  # 9:30 AM ET
# def in_or = SecondsFromTime(0930) < bars_count * 60 AND SecondsTillTime(0936) > 0;
#
# def or_high = if in_or then HighestAll(if !IsNaN(high) and in_or then high else Double.NaN) else or_high[1];
# def or_low  = if in_or then LowestAll(if !IsNaN(low) and in_or then low else Double.NaN) else or_low[1];
# def or_mid  = (or_high + or_low) / 2;
# def or_width = or_high - or_low;
#
# plot ORH = or_high;       ORH.SetDefaultColor(Color.GREEN);  ORH.SetStyle(Curve.LONG_DASH);
# plot ORL = or_low;        ORL.SetDefaultColor(Color.RED);    ORL.SetStyle(Curve.LONG_DASH);
# plot ORMID = or_mid;      ORMID.SetDefaultColor(Color.YELLOW); ORMID.SetStyle(Curve.LONG_DASH);
#
# # Fibonacci extensions arriba
# plot FU_1618 = or_high + or_width * 1.618;
# plot FU_2236 = or_high + or_width * 2.236;
# plot FU_2618 = or_high + or_width * 2.618;
# # ... (3.120, 3.770, 4.236, 5.120, 6.100, 7.870)
#
# # Breakout signal con MACD + RSI confirm
# def macd_line = MACD().Value;
# def macd_signal = MACD().Avg;
# def rsi = RSI(length=14);
#
# def breakout_up_signal = close > or_high AND macd_line > macd_signal AND rsi > 50;
# def breakout_down_signal = close < or_low AND macd_line < macd_signal AND rsi < 50;
#
# # Targets desde breakout
# def atr14 = Average(TrueRange(high, close, low), 14);
# def target_3atr_up = close + 3 * atr14;
# def target_4atr_up = close + 4 * atr14;
```

> NB: el código TOS de Juan tiene niveles adicionales (3.120, 3.770, 4.236, 5.120,
> 6.100, 7.870). En MarketSnapshot solo exponemos 1.618 y 2.618 para mantener el
> prompt compacto. Niveles extras se pueden agregar si el LLM los pide.

## Casos validados

- **CASE-Juan-001 (Friday 2026-05-29 SPX EOM)** — Iron Condor intraday WIN, OR
  state range trading favoreció IC simétrico.
- **CASE-Juan-002 (Monday 2026-06-01 SPX)** — Pattern emergente: IC simétrico +
  scaling PUT side en pullback intraday. RSI doble cruce confirmó exit call side.
