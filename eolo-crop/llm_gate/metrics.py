# ============================================================
#  LLMMetrics — Sprint 11: observabilidad operativa del LLM pipeline.
#
#  Counters in-memory thread-safe, expuestos vía
#  /api/state.stats.llm_metrics. Complemento de:
#   - Sprint 8.B cache stats (hit/miss + invalidations breakdown)
#   - Sprint 9+10 trade logging (Firestore eolo-crop-trades)
#
#  Métricas operativas en tiempo real para validar el sistema:
#   - total_calls / calls_per_hour
#   - verdicts dict (SELL_PUT / WAIT / CLOSE_POSITIONS / ...)
#   - pre_filter_skips por razón (outside_entry_window, vix_spike, etc.)
#   - latency_ms p50/p95/p99/avg (rolling window últimas N=100)
#   - decision_sources (LLM_HAIKU_PASS / LLM_SONNET_CONSULT / FALLBACK)
#   - cost_estimate_usd basado en token usage (estimación grosera)
#   - errors por tipo
#
#  Uso:
#    metrics = LLMMetrics()
#    metrics.record_pre_filter_skip("non_spy_ticker")
#    metrics.record_call(verdict="SELL_PUT", latency_ms=1850,
#                        decision_source="LLM_SONNET_CONSULT",
#                        input_tokens=2100, output_tokens=320, model="sonnet")
#    payload = metrics.stats()
# ============================================================
import time
import threading
from collections import defaultdict, deque
from typing import Any, Dict, Optional


# Pricing aproximado USD/1M tokens — actualizar según anthropic.com/pricing.
# Sonnet 4.x: $3 input / $15 output
# Haiku 4.x:  $0.8 input / $4 output
HAIKU_INPUT_PER_1M  = 0.8
HAIKU_OUTPUT_PER_1M = 4.0
SONNET_INPUT_PER_1M  = 3.0
SONNET_OUTPUT_PER_1M = 15.0


class LLMMetrics:
    """Counters in-memory thread-safe para observabilidad operativa."""

    def __init__(self, latency_window: int = 100):
        self._lock = threading.Lock()
        self._latency_window = int(latency_window)
        # reset() inicializa los counters bajo lock no es necesario en __init__
        # porque el objeto no está expuesto todavía.
        self._reset_unlocked()

    # ── Public API ─────────────────────────────────────────

    def reset(self) -> None:
        with self._lock:
            self._reset_unlocked()

    def record_call(
        self,
        verdict: Optional[str],
        latency_ms: float,
        decision_source: str = "UNKNOWN",
        input_tokens: int = 0,
        output_tokens: int = 0,
        model: str = "sonnet",
    ) -> None:
        """Registra una llamada al LLM (post-pre-filter, llegó a consult)."""
        try:
            latency = float(latency_ms)
        except (TypeError, ValueError):
            latency = 0.0
        cost = self._compute_cost(input_tokens, output_tokens, model)
        with self._lock:
            self._total_calls += 1
            if verdict:
                self._verdicts[verdict] += 1
            self._latencies_ms.append(latency)
            self._decision_sources[decision_source or "UNKNOWN"] += 1
            self._cost_estimate_usd += cost

    def record_pre_filter_skip(self, reason: str) -> None:
        """Registra skip por should_call_llm (no llegó a consult)."""
        if not reason:
            reason = "unspecified"
        with self._lock:
            self._pre_filter_skips[reason] += 1

    def record_error(self, error_type: str) -> None:
        """Registra error (timeout, API 5xx, parse fail, etc.)."""
        if not error_type:
            error_type = "unspecified"
        with self._lock:
            self._errors[error_type] += 1

    def stats(self) -> Dict[str, Any]:
        """Snapshot inmutable de los counters + agregados (p50/p95/p99)."""
        with self._lock:
            latencies = list(self._latencies_ms)
            verdicts = dict(self._verdicts)
            pre_skips = dict(self._pre_filter_skips)
            sources = dict(self._decision_sources)
            errors = dict(self._errors)
            total_calls = self._total_calls
            cost = self._cost_estimate_usd
            reset_at = self._last_reset_at

        # Cálculos fuera del lock (no tocan state mutable).
        if latencies:
            sorted_lat = sorted(latencies)
            n = len(sorted_lat)
            p50 = sorted_lat[int(n * 0.5)] if n > 0 else 0.0
            # p95: necesitamos ≥20 samples para que el percentil tenga sentido
            p95 = sorted_lat[int(n * 0.95)] if n >= 20 else sorted_lat[-1]
            p99 = sorted_lat[int(n * 0.99)] if n >= 100 else sorted_lat[-1]
            avg = sum(sorted_lat) / n
        else:
            p50 = p95 = p99 = avg = 0.0

        elapsed_hours = (time.time() - reset_at) / 3600.0
        calls_per_hour = (total_calls / elapsed_hours) if elapsed_hours > 0 else 0.0

        return {
            "total_calls":       total_calls,
            "calls_per_hour":    round(calls_per_hour, 2),
            "verdicts":          verdicts,
            "pre_filter_skips":  pre_skips,
            "decision_sources":  sources,
            "errors":            errors,
            "latency_ms": {
                "p50":         round(p50, 1),
                "p95":         round(p95, 1),
                "p99":         round(p99, 1),
                "avg":         round(avg, 1),
                "sample_size": len(latencies),
            },
            "cost_estimate_usd": round(cost, 4),
            "last_reset_at":     round(reset_at, 0),
            "elapsed_hours":     round(elapsed_hours, 2),
        }

    # ── Internals ──────────────────────────────────────────

    def _reset_unlocked(self) -> None:
        """Sólo llamar bajo `self._lock` (o desde __init__ pre-publicación)."""
        self._total_calls = 0
        self._pre_filter_skips: Dict[str, int] = defaultdict(int)
        self._verdicts: Dict[str, int] = defaultdict(int)
        self._latencies_ms: deque = deque(maxlen=self._latency_window)
        self._decision_sources: Dict[str, int] = defaultdict(int)
        self._cost_estimate_usd: float = 0.0
        self._last_reset_at: float = time.time()
        self._errors: Dict[str, int] = defaultdict(int)

    @staticmethod
    def _compute_cost(input_tokens: int, output_tokens: int, model: str) -> float:
        try:
            in_tok  = int(input_tokens or 0)
            out_tok = int(output_tokens or 0)
        except (TypeError, ValueError):
            return 0.0
        if (model or "").lower() == "haiku":
            in_rate, out_rate = HAIKU_INPUT_PER_1M, HAIKU_OUTPUT_PER_1M
        else:
            in_rate, out_rate = SONNET_INPUT_PER_1M, SONNET_OUTPUT_PER_1M
        return (in_tok / 1_000_000.0 * in_rate) + (out_tok / 1_000_000.0 * out_rate)
