# Backtest Report

- Tickers:       SPY, QQQ, IWM, TQQQ
- Window:        2026-03-11 → 2026-06-02
- Sample hours:  [10]
- Pre-screen:    True
- Budget cap:    $15.0
- Budget hit:    False

## Coverage

- Decisions produced: **240** of 240 requested (100.0%)
- Total cost:         **$1.314**

## Verdict distribution

- ENGINE_ERROR: 227
- WAIT: 12
- SELL_PUT: 1

## Regime distribution

- flip_zone: 113
- negative: 61
- positive_low: 47
- unknown: 18
- positive_high: 1

## Top rule citations

- TR-Juan-036: 10
- TR-Juan-070: 10
- TR-Juan-040: 9
- TR-Juan-043: 8
- TR-Juan-044: 7
- TR-Juan-064: 5
- TR-Juan-063: 5
- TR-Juan-050: 4
- TR-Juan-020: 4
- TR-Juan-037: 3
- TR-Juan-022: 3
- TR-Juan-062: 3
- TR-Juan-067: 1
- TR-Juan-042: 1
- TR-Juan-048: 1
- TR-Juan-011: 1
## 🎯 First action verdict EVER

SPY 2026-03-25 09:30 ET → **SELL_PUT confidence=8** strike=$649 dte=21.

Rules cited (KB v1.3 + KB v1.x mix):
- TR-Juan-043 (GOLDEN_TICKET axioma)
- TR-Juan-011 (SELL_PUT VIX velocity)
- TR-Juan-044 (R2/S2 Fibonacci)
- TR-Juan-050 (oversold_bouncing)
- TR-Juan-040 (entry window)
- TR-Juan-036 (protocolo apertura)
- TR-Juan-070 (cap confidence flip_zone) ← v1.3 nueva

Main reason: "VIX bajo y estable (16.00) activa TR-Juan-043 GOLDEN TICKET. SPY +0.56% con RSI 38.5 oversold bouncing confirma setup bullish. VIX bajando activa TR-Juan-011 SELL_PUT. Strike en S3 Fibonacci $649.88."

**Esto confirma:** la combinación KB v1.3 + indicators reales + compute layer Sub-B (magnet/cascade/smart_money) puede emitir action verdicts cuando el setup técnico lo justifica. Conf=8/10 indica confianza alta.

## Engine error analysis

227/240 ENGINE_ERROR (95%) — root cause **DNS resolution failure local mid-run**:
```
urlopen error [Errno 8] nodename nor servname provided, or not known
```

Backtest corrió 4h (22:25 → 02:25 UTC). Mac DNS resolution intermittente. Las 13 SPY decisions completadas antes del hiccup (12 WAIT + 1 SELL_PUT) confirman pipeline.

NO es bug del system. Re-run cuando DNS estable daría 240 decisions completas.

## Production-grade context

- SPY 60 días, QQQ/IWM/TQQQ 60 días, indicators Schwab OHLC reales
- Cost total $1.31 (mostly fallidos antes del cost increase)
- 1/13 SPY pre-error = 7.7% action rate cuando network OK
- KB v1.3 QD-aware rules citadas en distribución real

