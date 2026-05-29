# ============================================================
#  TradeLogger — Sprint 9: registro estructurado de trades.
#
#  Cierra el loop:
#    KB → decision (LLM/rule-based) → trade → outcome → análisis → ajuste KB
#
#  ALCANCE:
#  - Collection nueva `eolo-crop-trades` (NO toca la legacy
#    `eolo-crop-theta-trades` — coexisten sin interferir).
#  - Shape enriquecido con decision_source, decision_meta, setup
#    completo y outcome auto-clasificado en case_quality.
#  - KB-specific fields (tacit_rules_applied, similar_case_used) se
#    extraen del response del LLM Engine cuando viene. Puede estar
#    [] / None si el LLM no los emite en una respuesta dada (confidence
#    baja, setup ambiguo, fallback path). Wiring en
#    crop_main._record_trade_open_sprint9 (Sprint 10).
#
#  USO:
#    logger_obj = TradeLogger(project_id="eolo-schwab-agent",
#                             bot_revision="...", main_sha="...")
#    trade_id = logger_obj.record_trade_open(
#        ticker="SPY", decision_source="LLM",
#        decision_meta={...}, setup={...})
#    # ... más tarde, al cerrar:
#    logger_obj.record_trade_close(trade_id, outcome={...})
#
#  Tolerancia a fallos: cualquier excepción de Firestore se loguea
#  como warning y NO propaga — no romper el flow del bot por un
#  problema de telemetría.
# ============================================================
import uuid
from datetime import datetime
from typing import Optional, Dict, Any
from zoneinfo import ZoneInfo

from loguru import logger

FIRESTORE_COLLECTION = "eolo-crop-trades"
ET = ZoneInfo("America/New_York")

# Enum string para decision_source. Mantenemos strings (no Enum) por
# simplicidad y para que sean queryables directamente en Firestore.
VALID_DECISION_SOURCES = {
    "RULE_BASED",
    "LLM_HAIKU",
    "LLM_SONNET",
    "LLM_OVERRIDE",   # LLM forzó override sobre el spread_type del sector
    "LLM",            # genérico cuando el routing entre HAIKU/SONNET no es claro
}

# Quality buckets para auto-classify. GOLD = casos a feedback al KB.
QUALITY_GOLD   = "GOLD"
QUALITY_SILVER = "SILVER"
QUALITY_BRONZE = "BRONZE"


class TradeLogger:
    """Registra trades en Firestore con schema estructurado Sprint 9."""

    def __init__(
        self,
        project_id: Optional[str] = None,
        bot_revision: str = "unknown",
        main_sha: str = "unknown",
        kb_version: str = "v1.2",
        client=None,
    ):
        """
        project_id: GCP project (None → default ADC). En tests inyectar
                    `client=MagicMock()` para evitar conexión real.
        bot_revision / main_sha / kb_version: metadata para reproducibilidad.
        """
        if client is not None:
            self._client = client
        else:
            from google.cloud import firestore as _fs
            self._client = (
                _fs.Client(project=project_id) if project_id else _fs.Client()
            )
        self._collection = self._client.collection(FIRESTORE_COLLECTION)
        self._meta = {
            "bot_revision": bot_revision,
            "main_sha":     main_sha,
            "kb_version":   kb_version,
        }

    # ── Public API ─────────────────────────────────────────

    def record_trade_open(
        self,
        ticker: str,
        decision_source: str,
        decision_meta: Dict[str, Any],
        setup: Dict[str, Any],
        decision_id: Optional[str] = None,
    ) -> str:
        """Crea el doc del trade abierto. Retorna trade_id (UUID v4).

        decision_source: 1 de VALID_DECISION_SOURCES. Si llega valor inválido,
            se loguea warning y guarda igual (no bloqueamos al caller).
        decision_id: opcional, cross-link al sistema legacy de decisiones.
        """
        if decision_source not in VALID_DECISION_SOURCES:
            logger.warning(
                f"[trade_logger] decision_source '{decision_source}' fuera del set "
                f"esperado {VALID_DECISION_SOURCES} — guardando igual"
            )
        trade_id = str(uuid.uuid4())
        doc = {
            "trade_id":         trade_id,
            "decision_id":      decision_id,
            "timestamp_open":   datetime.now(ET).isoformat(),
            "timestamp_close":  None,
            "ticker":           ticker,
            "decision_source":  decision_source,
            "decision_meta":    dict(decision_meta) if decision_meta else {},
            "setup":            dict(setup) if setup else {},
            "outcome":          None,
            "case_quality":     None,
            "lesson_learned":   None,
            "meta":             dict(self._meta),
        }
        try:
            self._collection.document(trade_id).set(doc)
            logger.info(
                f"[trade_logger] OPEN trade_id={trade_id} ticker={ticker} "
                f"source={decision_source}"
            )
        except Exception as e:
            logger.warning(
                f"[trade_logger] OPEN write failed ticker={ticker}: {e}"
            )
        return trade_id

    def record_trade_close(
        self,
        trade_id: str,
        outcome: Dict[str, Any],
    ) -> Optional[str]:
        """Actualiza doc con outcome + auto-classify. Retorna case_quality.

        Si trade_id es None/empty, no-op (el caller no abrió un Sprint 9 trade).
        Si la escritura falla, loguea warning y retorna None.
        """
        if not trade_id:
            return None
        case_quality = self._classify_quality(outcome or {})
        update = {
            "timestamp_close": datetime.now(ET).isoformat(),
            "outcome":         dict(outcome) if outcome else {},
            "case_quality":    case_quality,
        }
        try:
            # Usamos set merge=True por si el doc no existía (race con un
            # OPEN fallido) — preferimos un close con quality que un drop.
            self._collection.document(trade_id).set(update, merge=True)
            pnl_pct = (outcome or {}).get("pnl_pct")
            pnl_str = f"{pnl_pct:.1f}%" if isinstance(pnl_pct, (int, float)) else "n/a"
            logger.info(
                f"[trade_logger] CLOSE trade_id={trade_id} "
                f"pnl={pnl_str} quality={case_quality}"
            )
        except Exception as e:
            logger.warning(
                f"[trade_logger] CLOSE write failed trade_id={trade_id}: {e}"
            )
            return None
        return case_quality

    # ── Quality classification ─────────────────────────────

    @staticmethod
    def _classify_quality(outcome: Dict[str, Any]) -> str:
        """Heurística inicial para case_quality.

        Reglas (revisables a medida que tengamos data real):
          GOLD   — pnl_pct >= 50 AND exit_reason == "profit_target_hit"
                   AND sin safety_overrides
          SILVER — pnl_pct >= 25  (incluso si vino vía safety override)
                   OR pnl_pct >= 50 con safety_overrides
          BRONZE — resto (incluye pnl < 25, stop_loss, EOD_forced_close)
        """
        try:
            pnl = float(outcome.get("pnl_pct") or 0)
        except (TypeError, ValueError):
            pnl = 0.0
        exit_reason = outcome.get("exit_reason") or ""
        safety_overrides = outcome.get("safety_overrides") or []
        has_overrides = bool(safety_overrides)

        if pnl >= 50.0 and exit_reason == "profit_target_hit" and not has_overrides:
            return QUALITY_GOLD
        if pnl >= 25.0:
            return QUALITY_SILVER
        if pnl >= 50.0 and has_overrides:
            # outranking: caso fuerte pero con override → degradar a SILVER
            return QUALITY_SILVER
        return QUALITY_BRONZE
