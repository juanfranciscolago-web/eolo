"""Cost optimizer for backtest engine (Sprint S5).

Estrategia: usar /pre_decide (Haiku) como pre-screen, escalar a /decide
(Sonnet) sólo cuando el pre-screen detecta setup interesante.

Sin optimizer (60d × 1 ticker × 1 snap/día × ~$0.10): $6
Con optimizer (~10% escala a Sonnet): 60d × $0.001 + 6d × $0.10 = $0.66
Para 4 tickers × 60d × 3 snaps/día: $72 → ~$8.
"""
from __future__ import annotations
from typing import Optional
import json
import urllib.request


# Cost per call (USD). Approximate, updated from
# stats.llm_metrics.cost_estimate_usd in /api/state.
HAIKU_USD_PER_CALL  = 0.001
SONNET_USD_PER_CALL = 0.10


def http_post_json(url: str, body: dict, token: str, timeout_s: int = 120) -> dict:
    """Tiny urllib POST helper. Returns parsed JSON dict or raises."""
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.load(resp)


def haiku_prescreen(
    snapshot: dict,
    engine_url: str,
    auth_token: str,
    confidence_threshold: int = 7,
) -> dict:
    """Pre-screen with Haiku via engine /pre_decide.

    Returns {
        "should_call_sonnet": bool,
        "haiku_confidence":   int,
        "reason":             str,
        "cost_usd":           float,
    }.
    """
    try:
        resp = http_post_json(f"{engine_url}/pre_decide", snapshot, auth_token, timeout_s=60)
        conf = int(resp.get("haiku_confidence") or 0)
        should_full = bool(resp.get("should_call_full"))
        # Escalate to Sonnet only if Haiku said full OR Haiku confidence in skip is low.
        should_call_sonnet = should_full or conf < confidence_threshold
        return {
            "should_call_sonnet": should_call_sonnet,
            "haiku_confidence":   conf,
            "reason":             (resp.get("reason") or "")[:200],
            "cost_usd":           HAIKU_USD_PER_CALL,
        }
    except Exception as e:
        # Fail open: si Haiku falla, call Sonnet (default conservative).
        return {
            "should_call_sonnet": True,
            "haiku_confidence":   0,
            "reason":             f"prescreen_error: {str(e)[:120]}",
            "cost_usd":           0.0,
        }


def backtest_call_with_cost_control(
    snapshot: dict,
    engine_url: str,
    auth_token: str,
    use_prescreen: bool = True,
    force_sonnet: bool = False,
) -> dict:
    """Run one /decide call with optional Haiku→Sonnet escalation.

    Returns {
        "verdict":   str,
        "decision":  dict,    # full /decide response if Sonnet called
        "tier":      "haiku" | "sonnet",
        "cost_usd":  float,
        "skipped":   bool,    # True if Haiku skipped Sonnet
        "haiku_meta": dict,   # only if prescreen ran
    }.
    """
    cost = 0.0
    haiku_meta = None

    if use_prescreen and not force_sonnet:
        haiku_meta = haiku_prescreen(snapshot, engine_url, auth_token)
        cost += haiku_meta["cost_usd"]
        if not haiku_meta["should_call_sonnet"]:
            return {
                "verdict":    "SKIPPED_BY_HAIKU",
                "decision":   None,
                "tier":       "haiku",
                "cost_usd":   cost,
                "skipped":    True,
                "haiku_meta": haiku_meta,
            }

    try:
        decision = http_post_json(f"{engine_url}/decide", snapshot, auth_token, timeout_s=180)
        cost += SONNET_USD_PER_CALL
        return {
            "verdict":    decision.get("verdict", "UNKNOWN"),
            "decision":   decision,
            "tier":       "sonnet",
            "cost_usd":   cost,
            "skipped":    False,
            "haiku_meta": haiku_meta,
        }
    except Exception as e:
        return {
            "verdict":    "ENGINE_ERROR",
            "decision":   {"error": str(e)[:300]},
            "tier":       "sonnet",
            "cost_usd":   cost,
            "skipped":    False,
            "haiku_meta": haiku_meta,
        }
