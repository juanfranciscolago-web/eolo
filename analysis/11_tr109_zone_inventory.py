"""TR-109 KB v1.10 — PASO 2 inventario de samples en zona específica.

Zona definida por Juan:
  - 25 < VIX_close < 30
  - velocity_day_over_day <= +5% (proxy de velocity_15m/30m intraday — sólo
    tenemos VIX diario via yfinance ^VIX)
  - |EMA9 - EMA21| / EMA21 >= 0.003 SOSTENIDO en >= 3 de últimas 5 sesiones
    (proxy de "3 de 5 velas" — daily, no intraday)

Limitaciones de datos:
  - vix_velocity_30m_pct en el engine es day-over-day proxy (snapshot_replay.py:206).
    Sólo computamos day-over-day; no podemos verificar velocity_15m intraday.
  - EMAs y "sostenido N velas" son DIARIAS, no 5m intraday.
  - Si N(daily) < 30, N(intraday-real) será todavía menor — gate del PASO 2 falla
    incluso siendo generosos.

Reporta: N total, breakdown ticker, rango fechas, distribución bull/bear.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from datetime import date, timedelta

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from backtest.indicators import ema  # noqa: E402


TICKERS = ["SPY", "QQQ", "IWM", "TQQQ"]  # SPX cache vacío

VIX_CACHE = REPO / "backtest" / "data" / "vix_history.json"
OHLC_PATH = lambda tk: REPO / "backtest" / "data" / f"schwab_ohlc_{tk}.json"

VIX_MIN, VIX_MAX = 25.0, 30.0
VELOCITY_MAX_PCT = 5.0          # day-over-day <= +5%
EMA_SEP_MIN = 0.003             # |EMA9-EMA21|/EMA21 >= 0.3%
SUSTAINED_MIN = 3               # 3 de últimas 5 sesiones


def load_vix() -> dict[str, float]:
    return json.loads(VIX_CACHE.read_text())


def load_ohlc(ticker: str) -> list[dict]:
    p = OHLC_PATH(ticker)
    if not p.exists():
        return []
    return json.loads(p.read_text()).get("candles", [])


def compute_ema_separation_series(candles: list[dict]) -> list[float]:
    """Returns |EMA9-EMA21|/EMA21 ratio aligned 1:1 with candles."""
    closes = [float(c["close"]) for c in candles]
    e9 = ema(closes, 9)
    e21 = ema(closes, 21)
    out = []
    for a, b in zip(e9, e21):
        if b == 0:
            out.append(0.0)
        else:
            out.append(abs(a - b) / abs(b))
    return out


def compute_direction_series(candles: list[dict]) -> list[int]:
    """Returns sign(EMA9-EMA21) aligned 1:1 with candles (1=bull, -1=bear, 0=flat)."""
    closes = [float(c["close"]) for c in candles]
    e9 = ema(closes, 9)
    e21 = ema(closes, 21)
    out = []
    for a, b in zip(e9, e21):
        if a > b:
            out.append(1)
        elif a < b:
            out.append(-1)
        else:
            out.append(0)
    return out


def main() -> int:
    vix = load_vix()
    sorted_vix_dates = sorted(vix.keys())
    vix_dates_set = set(sorted_vix_dates)
    print(f"VIX cache: {len(vix)} days, range {sorted_vix_dates[0]} -> {sorted_vix_dates[-1]}")

    all_hits: list[dict] = []
    per_ticker: dict[str, list[dict]] = {tk: [] for tk in TICKERS}

    for tk in TICKERS:
        candles = load_ohlc(tk)
        if not candles:
            print(f"  {tk}: no candles")
            continue

        ema_sep = compute_ema_separation_series(candles)
        direction = compute_direction_series(candles)

        for i, c in enumerate(candles):
            d_str = c["date"]
            # Need previous trading day VIX for velocity day-over-day.
            if d_str not in vix_dates_set:
                continue
            vix_today = vix[d_str]
            if not (VIX_MIN < vix_today < VIX_MAX):
                continue

            # Find previous available VIX date (skip weekends/holidays).
            idx = sorted_vix_dates.index(d_str)
            if idx == 0:
                continue
            d_prev = sorted_vix_dates[idx - 1]
            vix_prev = vix[d_prev]
            if vix_prev <= 0:
                continue
            velocity_pct = (vix_today - vix_prev) / vix_prev * 100.0
            if velocity_pct > VELOCITY_MAX_PCT:
                continue

            # Sustained EMA separation: last 5 candles, count how many >= 0.003.
            if i < 4:
                continue  # need 5-day window
            window = ema_sep[i - 4: i + 1]
            sustained = sum(1 for r in window if r >= EMA_SEP_MIN)
            if sustained < SUSTAINED_MIN:
                continue

            # Direction must be consistent in the same window (>= 3 of 5 same sign).
            dir_window = direction[i - 4: i + 1]
            bull_count = sum(1 for x in dir_window if x == 1)
            bear_count = sum(1 for x in dir_window if x == -1)
            consistent_bull = bull_count >= SUSTAINED_MIN
            consistent_bear = bear_count >= SUSTAINED_MIN
            if not (consistent_bull or consistent_bear):
                continue

            label = "bull" if consistent_bull else "bear"
            hit = {
                "ticker": tk,
                "date": d_str,
                "vix": round(vix_today, 2),
                "velocity_pct": round(velocity_pct, 2),
                "ema_sep": round(ema_sep[i], 4),
                "sustained_5": sustained,
                "direction": label,
            }
            all_hits.append(hit)
            per_ticker[tk].append(hit)

    print()
    print("=" * 72)
    print(f"TOTAL SAMPLES: {len(all_hits)}")
    print("=" * 72)
    print()
    print("By ticker:")
    for tk in TICKERS:
        n = len(per_ticker[tk])
        bull = sum(1 for h in per_ticker[tk] if h["direction"] == "bull")
        bear = n - bull
        print(f"  {tk:>4}: {n:>3} samples  (bull={bull}, bear={bear})")
    print()

    total_bull = sum(1 for h in all_hits if h["direction"] == "bull")
    total_bear = len(all_hits) - total_bull
    print(f"Direction split (total): bull={total_bull}, bear={total_bear}")

    if all_hits:
        dates = sorted({h["date"] for h in all_hits})
        print(f"Date range: {dates[0]} -> {dates[-1]} ({len(dates)} distinct dates)")

    print()
    print("Hits detail (sorted by date):")
    for h in sorted(all_hits, key=lambda x: (x["date"], x["ticker"])):
        print(f"  {h['date']} {h['ticker']:>4}  vix={h['vix']:>5.2f}  "
              f"vel={h['velocity_pct']:>+6.2f}%  ema_sep={h['ema_sep']:.4f}  "
              f"sust={h['sustained_5']}/5  dir={h['direction']}")

    print()
    if len(all_hits) >= 30:
        print(f"✓ GATE PASS: N={len(all_hits)} >= 30 → PASO 3 viable.")
        return 0
    else:
        print(f"✗ GATE FAIL: N={len(all_hits)} < 30 → PASO 3 NO viable.")
        print("  Recomendación: TR-109 queda como CANDIDATE indefinido en KB v1.10.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
