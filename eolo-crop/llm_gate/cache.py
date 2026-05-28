"""
DecisionCache — Cache de decisiones del LLM con TTL e invalidacion inteligente.

Reduce calls redundantes al LLM cuando el snapshot no cambio mucho entre
ciclos consecutivos del bot (loop 60s).

Cache key: hash de campos de identidad estable (ticker + session_phase).
Snapshots con misma identidad pero distinto VIX/positions van al mismo
key — el invalidator decide si re-llamar al LLM o reutilizar la cached.

Invalidacion automatica si:
- TTL expirado (default 30s)
- VIX velocity 30m cambia >2% (mercado en movimiento)
- has_open_positions cambia o open_positions_summary cambia
  (estado de cartera cambio — exits/cierres parciales)

Tech debt: con tech debt #18 (VIX velocity siempre 0), el primer
invalidator no se dispara nunca en v0.1. Esperamos VIX buffer en
v0.2 para que sea efectivo.
"""
import time
import hashlib
import json
import threading
import logging
from typing import Optional, Dict, Any, Tuple

logger = logging.getLogger(__name__)


_CACHE_KEY_FIELDS = ["ticker", "session_phase"]

_VIX_VELOCITY_INVALIDATE_DELTA = 2.0  # % cambio que invalida cache


class DecisionCache:
    """
    Cache thread-safe de decisiones del LLM.

    Use case:
        cache = DecisionCache(ttl_seconds=30.0)
        cached = cache.get(snapshot)
        if cached is None:
            decision = llm_client.consult(snapshot)
            cache.put(snapshot, decision)
        else:
            decision = cached
    """

    def __init__(self, ttl_seconds: float = 30.0):
        self.ttl_seconds = ttl_seconds
        self._cache: Dict[str, Tuple[float, Dict[str, Any], Dict[str, Any]]] = {}
        # key -> (timestamp, snapshot, decision)
        self._lock = threading.Lock()
        self._stats = {
            "hits": 0,
            "misses": 0,
            "ttl_evictions": 0,
            "vix_invalidations": 0,
            "positions_invalidations": 0,
        }

    def _cache_key(self, snapshot: Dict[str, Any]) -> str:
        """Build cache key del snapshot. Solo campos relevantes para identidad."""
        key_data = {f: snapshot.get(f) for f in _CACHE_KEY_FIELDS}
        key_str = json.dumps(key_data, sort_keys=True, default=str)
        return hashlib.md5(key_str.encode()).hexdigest()

    def get(self, snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Devuelve decision cached si existe + valida + no invalidada.
        None si miss o invalidada.
        """
        key = self._cache_key(snapshot)
        now = time.time()

        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._stats["misses"] += 1
                return None

            cached_ts, cached_snapshot, cached_decision = entry

            # TTL check
            age = now - cached_ts
            if age > self.ttl_seconds:
                self._stats["ttl_evictions"] += 1
                del self._cache[key]
                self._stats["misses"] += 1
                return None

            # VIX velocity invalidation
            vel_now = float(snapshot.get("vix_velocity_30m_pct", 0.0))
            vel_cached = float(cached_snapshot.get("vix_velocity_30m_pct", 0.0))
            if abs(vel_now - vel_cached) > _VIX_VELOCITY_INVALIDATE_DELTA:
                self._stats["vix_invalidations"] += 1
                del self._cache[key]
                self._stats["misses"] += 1
                return None

            # Open positions invalidation (bool + summary string)
            pos_now = snapshot.get("has_open_positions", False)
            pos_cached = cached_snapshot.get("has_open_positions", False)
            summary_now = snapshot.get("open_positions_summary", "") or ""
            summary_cached = cached_snapshot.get("open_positions_summary", "") or ""
            if pos_now != pos_cached or summary_now != summary_cached:
                self._stats["positions_invalidations"] += 1
                del self._cache[key]
                self._stats["misses"] += 1
                return None

            # Hit valido
            self._stats["hits"] += 1
            logger.debug(
                f"[cache] HIT ticker={snapshot.get('ticker')} age={age:.1f}s"
            )
            # Devolver copia para evitar mutaciones
            return dict(cached_decision)

    def put(self, snapshot: Dict[str, Any], decision: Dict[str, Any]) -> None:
        """Guardar decision en cache."""
        key = self._cache_key(snapshot)
        with self._lock:
            self._cache[key] = (time.time(), dict(snapshot), dict(decision))
            logger.debug(
                f"[cache] PUT ticker={snapshot.get('ticker')} "
                f"verdict={decision.get('verdict')}"
            )

    def stats(self) -> Dict[str, Any]:
        """Devuelve metricas del cache."""
        with self._lock:
            total = self._stats["hits"] + self._stats["misses"]
            hit_rate = (
                self._stats["hits"] / total if total > 0 else 0.0
            )
            return {
                **self._stats,
                "total": total,
                "hit_rate": round(hit_rate, 3),
                "size": len(self._cache),
                "ttl_seconds": self.ttl_seconds,
            }

    def clear(self) -> None:
        """Limpiar cache completo (util en tests)."""
        with self._lock:
            self._cache.clear()
            # Stats se mantienen (NO reset)

    def clear_stats(self) -> None:
        """Reset metricas (util en tests)."""
        with self._lock:
            for k in self._stats:
                self._stats[k] = 0
