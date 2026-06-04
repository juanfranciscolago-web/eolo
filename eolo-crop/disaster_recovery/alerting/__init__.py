"""Structured logging wrapper for DR alerts.

Code path crítico llama `alert_critical(category, reason, **context)`:
- Emite structured log severity=CRITICAL con jsonPayload (Cloud Run picks up).
- Cloud Monitoring alert policy (ver alert_policy.yaml) captura y notifica.

Para activar:
1. Crear notification channel (email/SMS).
2. Editar alert_policy.yaml con channel ID.
3. gcloud alpha monitoring policies create --policy-from-file=alert_policy.yaml.
"""
import json
import logging
import sys

_logger = logging.getLogger("dr_alerting")


def alert_critical(category: str, reason: str, **context) -> None:
    """Emit a CRITICAL severity log that triggers Cloud Monitoring alert."""
    payload = {
        "severity":   "CRITICAL",
        "category":   category,
        "reason":     reason,
        "context":    context,
    }
    print(json.dumps(payload), file=sys.stderr, flush=True)
    _logger.critical(f"DR_ALERT category={category} reason={reason} context={context}")
