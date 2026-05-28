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
- Sprint 8.B (#2): price del underlying se movio >0.5% desde cached
- Sprint 8.B (#2): chain_ts del current > cached + 30s (chain refrescado)

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
# Sprint 8.B (#2): nuevos thresholds.
_PRICE_MOVE_INVALIDATE_PCT = 0.5      # % del cached_price que invalida
_CHAIN_MAX_AGE_SECONDS = 30.0         # delta de chain_ts que invalida


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

    def __init__(
        self,
        ttl_seconds: float = 30.0,
        price_move_pct: float = _PRICE_MOVE_INVALIDATE_PCT,
        chain_max_age_seconds: float = _CHAIN_MAX_AGE_SECONDS,
    ):
        self.ttl_seconds = ttl_seconds
        self.price_move_threshold_pct = float(price_move_pct)
        self.chain_max_age_seconds = float(chain_max_age_seconds)
        # key -> (timestamp, snapshot, decision, cached_price, cached_chain_ts)
        self._cache: Dict[str, Tuple[float, Dict[str, Any], Dict[str, Any], float, Optional[float]]] = {}
        self._lock = threading.Lock()
        self._stats = {
            "hits": 0,
            "misses": 0,
            "ttl_evictions": 0,
            "vix_invalidations": 0,
            "positions_invalidations": 0,
            "price_move_invalidations": 0,
            "chain_age_invalidations": 0,
        }

    def _cache_key(self, snapshot: Dict[str, Any]) -> str:
        """Build cache key del snapshot. Solo campos relevantes para identidad."""
        key_data = {f: snapshot.get(f) for f in _CACHE_KEY_FIELDS}
        key_str = json.dumps(key_data, sort_keys=True, default=str)
        return hashlib.md5(key_str.encode()).hexdigest()

    def get(
        self,
        snapshot: Dict[str, Any],
        current_chain_ts: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Devuelve decision cached si existe + valida + no invalidada.
        None si miss o invalidada.

        Sprint 8.B:
        - current_chain_ts: timestamp del chain actual. Si difiere del cached
          en >chain_max_age_seconds, MISS (chain refrescado). Default None
          desactiva el check.
        - price del underlying se extrae de snapshot["price"] y se compara
          contra el cached. Si difiere >price_move_threshold_pct, MISS.
        """
        key = self._cache_key(snapshot)
        now = time.time()
        ticker = snapshot.get("ticker")

        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._stats["misses"] += 1
                return None

            cached_ts, cached_snapshot, cached_decision, cached_price, cached_chain_ts = entry

            # TTL check
            age = now - cached_ts
            if age > self.ttl_seconds:
                self._stats["ttl_evictions"] += 1
                del self._cache[key]
                self._stats["misses"] += 1
                logger.debug(f"[cache] MISS ticker={ticker} reason=ttl age={age:.1f}s")
                return None

            # VIX velocity invalidation
            vel_now = float(snapshot.get("vix_velocity_30m_pct", 0.0))
            vel_cached = float(cached_snapshot.get("vix_velocity_30m_pct", 0.0))
            if abs(vel_now - vel_cached) > _VIX_VELOCITY_INVALIDATE_DELTA:
                self._stats["vix_invalidations"] += 1
                del self._cache[key]
                self._stats["misses"] += 1
                logger.debug(
                    f"[cache] MISS ticker={ticker} reason=vix "
                    f"delta={abs(vel_now-vel_cached):.2f}%"
                )
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
                logger.debug(f"[cache] MISS ticker={ticker} reason=positions")
                return None

            # Sprint 8.B: price move invalidation
            current_price = snapshot.get("price")
            if (
                current_price is not None
                and cached_price is not None
                and cached_price > 0
            ):
                move_pct = abs(float(current_price) - float(cached_price)) / float(cached_price) * 100.0
                if move_pct > self.price_move_threshold_pct:
                    self._stats["price_move_invalidations"] += 1
                    del self._cache[key]
                    self._stats["misses"] += 1
                    logger.debug(
                        f"[cache] MISS ticker={ticker} reason=price_move "
                        f"{cached_price:.2f}→{current_price:.2f} ({move_pct:.2f}%)"
                    )
                    return None

            # Sprint 8.B: chain age invalidation
            if (
                current_chain_ts is not None
                and cached_chain_ts is not None
                and (current_chain_ts - cached_chain_ts) > self.chain_max_age_seconds
            ):
                self._stats["chain_age_invalidations"] += 1
                del self._cache[key]
                self._stats["misses"] += 1
                logger.debug(
                    f"[cache] MISS ticker={ticker} reason=chain_age "
                    f"delta={current_chain_ts - cached_chain_ts:.1f}s"
                )
                return None

            # Hit valido
            self._stats["hits"] += 1
            logger.debug(
                f"[cache] HIT ticker={ticker} age={age:.1f}s"
            )
            # Devolver copia para evitar mutaciones
            return dict(cached_decision)

    def put(
        self,
        snapshot: Dict[str, Any],
        decision: Dict[str, Any],
        chain_ts: Optional[float] = None,
    ) -> None:
        """Guardar decision en cache.

        Sprint 8.B: chain_ts opcional para activar la invalidación por chain_age.
        El cached_price se extrae automáticamente de snapshot["price"].
        """
        key = self._cache_key(snapshot)
        cached_price = snapshot.get("price")
        try:
            cached_price = float(cached_price) if cached_price is not None else None
        except (TypeError, ValueError):
            cached_price = None
        with self._lock:
            self._cache[key] = (
                time.time(),
                dict(snapshot),
                dict(decision),
                cached_price,
                chain_ts,
            )
            logger.debug(
                f"[cache] PUT ticker={snapshot.get('ticker')} "
                f"verdict={decision.get('verdict')} price={cached_price}"
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
                "hit_rate_pct": round(hit_rate * 100, 2),
                "size": len(self._cache),
                "ttl_seconds": self.ttl_seconds,
                "price_move_threshold_pct": self.price_move_threshold_pct,
                "chain_max_age_seconds": self.chain_max_age_seconds,
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
