#!/usr/bin/env python3
"""
Diagnose cross-bot: is the stuck-candle-buffer signature global or specific to eolo-bot-crop?

Each bot exposes a different signal:

  - eolo-bot-crop      : HTTP /api/state — _last_snapshots[ticker].rsi_2m
  - eolo-bot           : log line "Fetched N <tf> candles for <TICKER>" — candle-fetch heartbeat
  - eolo-bot-v2        : log line "[BUFFER_MD] <TICKER> — N candles @ <tf>" — buffer size per ticker/tf
  - eolo-bot-crypto    : log line "[RSI_SMA200] <SYMBOL> ... RSI=<V>" — explicit RSI per symbol
  - eolo-bot-soxx3x    : Schwab API pricehistory URL with startDate/endDate — fetch heartbeat

Captures two windows (T0 and T1) separated by WAIT_SECONDS and diffs.

Usage:
    python tools/diagnose_all_bots.py [wait_seconds]  # default 300
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

WAIT_SECONDS = int(sys.argv[1]) if len(sys.argv) > 1 else 300
PROJECT = "eolo-schwab-agent"

CROP_URL = "https://eolo-bot-crop-nmjz4iwcea-ue.a.run.app"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def fmt_ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def gcloud_logs(service: str, since: datetime, pattern_re: str, limit: int = 1000) -> list[str]:
    """Pull text payload lines from a service since a given timestamp."""
    flt = (
        f'resource.type=cloud_run_revision AND '
        f'resource.labels.service_name={service} AND '
        f'timestamp>="{fmt_ts(since)}" AND '
        f'textPayload=~"{pattern_re}"'
    )
    cmd = [
        "gcloud", "logging", "read", flt,
        f"--project={PROJECT}",
        f"--limit={limit}",
        "--format=value(textPayload)",
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=60).decode("utf-8", "replace")
    except Exception as e:
        print(f"  [warn] gcloud logs failed for {service}: {e}")
        return []
    return [ln for ln in out.splitlines() if ln.strip()]


def gcloud_token() -> str:
    return subprocess.check_output(["gcloud", "auth", "print-identity-token"]).decode().strip()


def capture_crop_http() -> dict[str, dict]:
    """Return {ticker: {'signal_price': float, 'quote_last': float, 'divergence': float}}.

    Stuck-buffer fingerprint: signals[ticker][<strategy>].price (derived from the
    candle buffer) drifts away from quotes[ticker].last (live tape).
    """
    token = gcloud_token()
    import urllib.request
    req = urllib.request.Request(f"{CROP_URL}/api/state", headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            d = json.loads(resp.read().decode())
    except Exception as e:
        print(f"  [warn] crop HTTP failed: {e}")
        return {}
    signals = d.get("signals") or {}
    quotes = d.get("quotes") or {}
    out = {}
    for ticker, sig in signals.items():
        if not isinstance(sig, dict):
            continue
        spr = None
        for stratval in sig.values():
            if isinstance(stratval, dict) and "price" in stratval:
                spr = stratval["price"]
                break
        ql = (quotes.get(ticker) or {}).get("last")
        if spr is None or ql is None:
            continue
        try:
            spr_f = float(spr)
            ql_f = float(ql)
        except (TypeError, ValueError):
            continue
        out[ticker] = {
            "signal_price": spr_f,
            "quote_last": ql_f,
            "divergence": spr_f - ql_f,
        }
    return out


# --- log shape extractors ---

RE_EOLO_BOT_FETCH = re.compile(r"Fetched (\d+) (\d+)min candles for (\w+)")
RE_EOLO_BOT_V2 = re.compile(r"\[BUFFER_MD\] (\w+) — (\d+) candles @ (\d+)min")
RE_CRYPTO_RSI = re.compile(r"\[RSI_SMA200\] (\w+) .* RSI=([\d\.]+)")
RE_SOXX_URL = re.compile(r"symbol=(\w+).*startDate=(\d+)")


def capture_eolo_bot_logs(since: datetime) -> dict[str, dict]:
    """For eolo-bot: per-ticker latest fetch (count, tf, count of distinct fetches)."""
    lines = gcloud_logs("eolo-bot", since, "Fetched ", limit=2000)
    per = defaultdict(lambda: {"fetches": 0, "last_count": None, "last_tf": None})
    for ln in lines:
        m = RE_EOLO_BOT_FETCH.search(ln)
        if not m:
            continue
        n, tf, ticker = m.group(1), m.group(2), m.group(3)
        e = per[ticker]
        e["fetches"] += 1
        e["last_count"] = int(n)
        e["last_tf"] = int(tf)
    return dict(per)


def capture_eolo_bot_v2_logs(since: datetime) -> dict[str, dict]:
    """For eolo-bot-v2: per-(ticker, tf) last buffer size; collapsed to ticker w/ max-tf snapshot."""
    lines = gcloud_logs("eolo-bot-v2", since, r"\[BUFFER_MD\]", limit=2000)
    per = defaultdict(lambda: {"samples": 0, "last_count": None, "last_tf": None})
    for ln in lines:
        m = RE_EOLO_BOT_V2.search(ln)
        if not m:
            continue
        ticker, n, tf = m.group(1), int(m.group(2)), int(m.group(3))
        e = per[ticker]
        e["samples"] += 1
        e["last_count"] = n
        e["last_tf"] = tf
    return dict(per)


def capture_crypto_logs(since: datetime) -> dict[str, dict]:
    """For eolo-bot-crypto: per-symbol last RSI."""
    lines = gcloud_logs("eolo-bot-crypto", since, r"\[RSI_SMA200\]", limit=2000)
    per = defaultdict(lambda: {"samples": 0, "last_rsi": None})
    for ln in lines:
        m = RE_CRYPTO_RSI.search(ln)
        if not m:
            continue
        sym, rsi = m.group(1), float(m.group(2))
        e = per[sym]
        e["samples"] += 1
        e["last_rsi"] = rsi
    return dict(per)


def capture_soxx_logs(since: datetime) -> dict[str, dict]:
    """For eolo-bot-soxx3x: per-symbol number of pricehistory fetches + last startDate."""
    lines = gcloud_logs("eolo-bot-soxx3x", since, "pricehistory", limit=2000)
    per = defaultdict(lambda: {"fetches": 0, "last_start": None})
    for ln in lines:
        m = RE_SOXX_URL.search(ln)
        if not m:
            continue
        sym, st = m.group(1), int(m.group(2))
        e = per[sym]
        e["fetches"] += 1
        e["last_start"] = st
    return dict(per)


# --- diff/report logic ---

def diff_crop(t0: dict, t1: dict) -> tuple[int, int, list[str]]:
    """Stuck if (a) signal_price stays identical AND (b) divergence > $0.10."""
    rows = []
    stuck = total = 0
    for tk in sorted(set(t0) | set(t1)):
        a, b = t0.get(tk, {}), t1.get(tk, {})
        sp0, sp1 = a.get("signal_price"), b.get("signal_price")
        ql0, ql1 = a.get("quote_last"), b.get("quote_last")
        if sp0 is None or sp1 is None or ql0 is None or ql1 is None:
            rows.append(f"  {tk:<8} no data")
            continue
        d_sig = sp1 - sp0
        d_quote = ql1 - ql0
        div_t1 = sp1 - ql1
        total += 1
        # stuck: signal price barely moves AND meaningful divergence from live quote
        is_stuck = abs(d_sig) < 0.01 and abs(div_t1) > 0.10
        if is_stuck:
            stuck += 1
        mark = "STUCK 🚨" if is_stuck else "OK ✓"
        rows.append(
            f"  {tk:<6} sig {sp0:>8.2f}→{sp1:>8.2f} (Δ{d_sig:+6.2f})  "
            f"quote {ql0:>8.2f}→{ql1:>8.2f} (Δ{d_quote:+6.2f})  "
            f"divergence T1={div_t1:+7.2f}  {mark}"
        )
    return stuck, total, rows


def diff_count_signal(t0: dict, t1: dict, count_key: str, label_name: str) -> tuple[int, int, list[str]]:
    """For log-based bots: 'stuck' = same last_count AND zero new samples in T1 vs T0."""
    rows = []
    stuck = total = 0
    for tk in sorted(set(t0) | set(t1)):
        a, b = t0.get(tk, {}), t1.get(tk, {})
        n0 = a.get(count_key)
        n1 = b.get(count_key)
        s0 = a.get("samples", a.get("fetches", 0))
        s1 = b.get("samples", b.get("fetches", 0))
        total += 1
        is_stuck = False
        notes = []
        if s1 == 0:
            notes.append("no new logs in T1")
            is_stuck = True
        if n0 is not None and n1 is not None and n0 == n1 and n0 <= 2:
            notes.append(f"{label_name}={n1} (very low, unchanged)")
            is_stuck = True
        if is_stuck:
            stuck += 1
        mark = "STUCK 🚨" if is_stuck else "OK ✓"
        note_str = " | ".join(notes) if notes else ""
        rows.append(f"  {tk:<10} T0:{label_name}={n0} samples={s0:>3}   T1:{label_name}={n1} samples={s1:>3}  {mark} {note_str}")
    return stuck, total, rows


def diff_crypto(t0: dict, t1: dict) -> tuple[int, int, list[str]]:
    rows = []
    stuck = total = 0
    for sym in sorted(set(t0) | set(t1)):
        a, b = t0.get(sym, {}), t1.get(sym, {})
        r0, r1 = a.get("last_rsi"), b.get("last_rsi")
        s0, s1 = a.get("samples", 0), b.get("samples", 0)
        if r0 is None or r1 is None:
            rows.append(f"  {sym:<10} rsi=?")
            continue
        total += 1
        dr = r1 - r0
        is_stuck = abs(dr) < 0.01 and s1 > 0
        if s1 == 0:
            is_stuck = True
        if is_stuck:
            stuck += 1
        mark = "STUCK 🚨" if is_stuck else "OK ✓"
        rows.append(f"  {sym:<10} RSI {r0:6.2f} → {r1:6.2f} (Δ{dr:+6.2f}) samples T0={s0:>3} T1={s1:>3}  {mark}")
    return stuck, total, rows


# --- main ---

def main():
    print(f"=== MULTI-BOT-DIAGNOSE  (wait={WAIT_SECONDS}s) ===\n")

    # Time window for T0 = the 5 minutes BEFORE we start
    t0_window_start = now_utc() - timedelta(minutes=5)
    print(f"T0 wall: {fmt_ts(now_utc())}  (logs since {fmt_ts(t0_window_start)})")
    print("Capturing T0 snapshot per bot...\n")

    t0 = {
        "crop":  capture_crop_http(),
        "bot":   capture_eolo_bot_logs(t0_window_start),
        "v2":    capture_eolo_bot_v2_logs(t0_window_start),
        "crypto":capture_crypto_logs(t0_window_start),
        "soxx":  capture_soxx_logs(t0_window_start),
    }
    for name, d in t0.items():
        print(f"  T0 {name:<8} {len(d):>3} entities")

    print(f"\n=== Sleeping {WAIT_SECONDS}s ===")
    t1_window_start = now_utc()  # T1 log window starts now
    time.sleep(WAIT_SECONDS)

    print(f"\nT1 wall: {fmt_ts(now_utc())}  (logs since {fmt_ts(t1_window_start)})")
    print("Capturing T1 snapshot per bot...\n")

    t1 = {
        "crop":  capture_crop_http(),
        "bot":   capture_eolo_bot_logs(t1_window_start),
        "v2":    capture_eolo_bot_v2_logs(t1_window_start),
        "crypto":capture_crypto_logs(t1_window_start),
        "soxx":  capture_soxx_logs(t1_window_start),
    }
    for name, d in t1.items():
        print(f"  T1 {name:<8} {len(d):>3} entities")

    # --- diffs ---
    print("\n=== DIFF eolo-bot-crop  (signals.price vs quotes.last divergence) ===")
    s, t, rows = diff_crop(t0["crop"], t1["crop"])
    for r in rows: print(r)
    print(f"  → {s}/{t} stuck ({100*s/max(1,t):.0f}%)")

    print("\n=== DIFF eolo-bot         (log: 'Fetched N <tf>min candles for X') ===")
    s2, t2, rows = diff_count_signal(t0["bot"], t1["bot"], "last_count", "candles")
    for r in rows: print(r)
    print(f"  → {s2}/{t2} stuck ({100*s2/max(1,t2):.0f}%)")

    print("\n=== DIFF eolo-bot-v2      (log: '[BUFFER_MD] X — N candles @ <tf>min') ===")
    s3, t3, rows = diff_count_signal(t0["v2"], t1["v2"], "last_count", "buffer_size")
    for r in rows: print(r)
    print(f"  → {s3}/{t3} stuck ({100*s3/max(1,t3):.0f}%)")

    print("\n=== DIFF eolo-bot-crypto  (log: '[RSI_SMA200] X RSI=Y') ===")
    s4, t4, rows = diff_crypto(t0["crypto"], t1["crypto"])
    for r in rows: print(r)
    print(f"  → {s4}/{t4} stuck ({100*s4/max(1,t4):.0f}%)")

    print("\n=== DIFF eolo-bot-soxx3x  (log: Schwab pricehistory fetches) ===")
    rows = []
    stuck_sx = total_sx = 0
    for sym in sorted(set(t0["soxx"]) | set(t1["soxx"])):
        a, b = t0["soxx"].get(sym, {}), t1["soxx"].get(sym, {})
        f0, f1 = a.get("fetches", 0), b.get("fetches", 0)
        sd0, sd1 = a.get("last_start"), b.get("last_start")
        total_sx += 1
        advanced = (sd0 and sd1 and sd1 > sd0)
        is_stuck = (f1 == 0) or (not advanced)
        if is_stuck: stuck_sx += 1
        mark = "STUCK 🚨" if is_stuck else "OK ✓"
        rows.append(f"  {sym:<8} fetches T0={f0:>2} T1={f1:>2}  startDate advanced: {bool(advanced)}  {mark}")
    for r in rows: print(r)
    print(f"  → {stuck_sx}/{total_sx} stuck ({100*stuck_sx/max(1,total_sx):.0f}%)")

    # --- executive summary ---
    print("\n" + "=" * 72)
    print("EXECUTIVE SUMMARY")
    print("=" * 72)
    summary = [
        ("eolo-bot-crop  ", s,  t,  "HTTP /api/state RSI_2m"),
        ("eolo-bot       ", s2, t2, "candle-fetch heartbeat"),
        ("eolo-bot-v2    ", s3, t3, "BUFFER_MD size+freshness"),
        ("eolo-bot-crypto", s4, t4, "RSI_SMA200 per symbol"),
        ("eolo-bot-soxx3x", stuck_sx, total_sx, "Schwab pricehistory fetches"),
    ]
    for name, st, tot, sig in summary:
        rate = 100 * st / max(1, tot)
        verdict = "🚨 STUCK" if rate >= 80 else ("⚠️ PARTIAL" if rate >= 20 else "✅ OK")
        print(f"  {name}  {st}/{tot} stuck ({rate:>3.0f}%)  {verdict}    [signal: {sig}]")

    print("\nInterpretation:")
    total_stuck_bots = sum(1 for _, st, tot, _ in summary if tot > 0 and 100*st/tot >= 80)
    total_active = sum(1 for _, _, tot, _ in summary if tot > 0)
    if total_stuck_bots == 0:
        print("  → No bot shows global stuck-buffer signature; eolo-bot-crop fix likely isolated.")
    elif total_stuck_bots == 1 and t > 0 and (s/max(1,t) >= 0.8):
        print("  → ONLY eolo-bot-crop shows stuck buffer; bug is crop-specific.")
    elif total_stuck_bots == total_active:
        print("  → ALL bots show stuck signature; bug is in shared candle pipeline (eolo_common.*).")
    else:
        print(f"  → {total_stuck_bots}/{total_active} bots show stuck signature; mixed — review per-bot.")


if __name__ == "__main__":
    main()
