"""Extended metrics for S5 backtest (rule citations, regime, verdict).

Builds on backtest/metrics.py basic functions. Aggregates from a list of
virtual decisions (output of backtest/runner.py).
"""
from __future__ import annotations
from collections import Counter
from typing import Iterable


def verdict_distribution(decisions: list[dict]) -> dict[str, int]:
    """Count verdicts across decisions. Includes SKIPPED_BY_HAIKU bucket."""
    return dict(Counter(d.get("verdict", "UNKNOWN") for d in decisions))


def regime_distribution(decisions: list[dict]) -> dict[str, int]:
    """Distribution by gamma regime if snapshot annotated.

    Looks at decision["snapshot"]["gex_regime"] (or "gamma_regime_v2" fallback).
    """
    counts: Counter = Counter()
    for d in decisions:
        snap = d.get("snapshot") or {}
        regime = snap.get("gex_regime") or snap.get("gamma_regime_v2") or "unknown"
        counts[regime] += 1
    return dict(counts)


def rule_citation_counts(decisions: list[dict]) -> dict[str, int]:
    """Count tacit_rules_applied citations across all decisions.

    Returns sorted desc by count.
    """
    counts: Counter = Counter()
    for d in decisions:
        decision_obj = d.get("decision") or {}
        if not isinstance(decision_obj, dict):
            continue
        rules = decision_obj.get("tacit_rules_applied") or []
        for r in rules:
            if isinstance(r, str):
                counts[r] += 1
    return dict(sorted(counts.items(), key=lambda x: -x[1]))


def total_cost_usd(decisions: list[dict]) -> float:
    """Sum cost_usd field across decisions."""
    return round(sum(float(d.get("cost_usd") or 0) for d in decisions), 4)


def coverage_pct(decisions: list[dict], requested_count: int) -> float:
    """Decisions actually produced vs requested (snapshots that returned None)."""
    if requested_count <= 0:
        return 0.0
    return round(len(decisions) / requested_count * 100, 1)


def aggregate(decisions: list[dict], requested_count: int) -> dict:
    """One-shot aggregation entry point."""
    return {
        "n_decisions":         len(decisions),
        "requested_count":     requested_count,
        "coverage_pct":        coverage_pct(decisions, requested_count),
        "verdicts":            verdict_distribution(decisions),
        "regimes":             regime_distribution(decisions),
        "rule_citations":      rule_citation_counts(decisions),
        "total_cost_usd":      total_cost_usd(decisions),
    }
