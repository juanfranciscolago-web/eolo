# DR Alerting Setup

## Quick start (one-time)

```bash
# 1. Create email notification channel
gcloud alpha monitoring channels create \
  --type=email \
  --display-name="Juan email" \
  --channel-labels=email_address=juanfranciscolago@gmail.com

# Note the channel name returned (projects/.../notificationChannels/XXX)

# 2. Edit alert_policy.yaml — replace PLACEHOLDER_EMAIL_CHANNEL_ID with XXX

# 3. Apply policy
gcloud alpha monitoring policies create --policy-from-file=alert_policy.yaml
```

## How alerts fire

- Any code calling `disaster_recovery.alerting.alert_critical(category, reason, **ctx)` emits a CRITICAL log.
- Cloud Monitoring policy matches CRITICAL logs from `eolo-bot-crop`, `llm-engine-service`, and the DR Cloud Function.
- Email arrives within ~1 min.

## Categories pattern

| Category | When to fire |
|---|---|
| `dr_position_close_failed` | Auto-close N retries fail |
| `dr_token_expired` | Schwab refresh_token < 24h to expiry |
| `dr_circuit_breaker` | max_daily_loss / max_position_size breached |
| `dr_engine_unreachable` | Engine /decide consecutive failures > threshold |
| `dr_kill_switch_activated` | Manual kill switch set in eolo-config/risk_limits |
