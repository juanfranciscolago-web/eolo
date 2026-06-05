#!/bin/bash
# Promote canary con warmup automático + verificación cache.
#
# Uso:
#   ./tools/promote_canary.sh <TAG> [WARMUP_SEC] [MAX_WAIT_SEC]
#
# Args:
#   TAG          — tag del canary (e.g. t23)
#   WARMUP_SEC   — warmup mínimo antes de check (default 300s = 5 min)
#   MAX_WAIT_SEC — timeout máximo total (default 900s = 15 min)
#
# Behavior:
# 1. Warm-up inicial fijo (WARMUP_SEC) — bot bootea + polling popula snapshots
# 2. Check periódico (cada 60s) que _last_snapshots tenga al menos 1 ticker
# 3. Si cache OK: promote a 100% + cleanup tag anterior
# 4. Si timeout: alerta + NO promote, exit code 1
#
# Stderr: alerts via stderr para visibility en logs
set -euo pipefail

TAG="${1:?Usage: promote_canary.sh <TAG> [WARMUP_SEC] [MAX_WAIT_SEC]}"
WARMUP_SEC="${2:-300}"
MAX_WAIT_SEC="${3:-900}"
PROJECT="eolo-schwab-agent"
REGION="us-east1"
SERVICE="eolo-bot-crop"

echo "=== promote_canary.sh ==="
echo "  TAG          = $TAG"
echo "  WARMUP_SEC   = $WARMUP_SEC"
echo "  MAX_WAIT_SEC = $MAX_WAIT_SEC"
echo "  SERVICE      = $SERVICE ($REGION / $PROJECT)"
echo "  Start time   = $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Validate tag exists (busca el url o revisionName en el traffic spec):
TAG_URL=$(gcloud run services describe "$SERVICE" --region="$REGION" --project="$PROJECT" \
  --format=json 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
for t in d.get('status', {}).get('traffic', []):
    if t.get('tag') == '$TAG':
        print(t.get('url', ''))
        break
")

if [ -z "$TAG_URL" ]; then
    echo "🚨 ERROR: Tag '$TAG' not found in service $SERVICE" >&2
    exit 1
fi
echo "📍 Tag URL: $TAG_URL"

TAG_REV=$(gcloud run services describe "$SERVICE" --region="$REGION" --project="$PROJECT" \
  --format=json 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
for t in d.get('status', {}).get('traffic', []):
    if t.get('tag') == '$TAG':
        print(t.get('revisionName', ''))
        break
")
echo "📍 Tag revision: $TAG_REV"

# Initial warmup:
echo "⏳ Initial warmup ${WARMUP_SEC}s (polling popula snapshots)..."
sleep "$WARMUP_SEC"

# Cache check loop:
TOKEN=$(gcloud auth print-identity-token)
ELAPSED=$WARMUP_SEC
CHECK_INTERVAL=60
CACHE_INFO=""

while [ "$ELAPSED" -lt "$MAX_WAIT_SEC" ]; do
    # Re-fetch token periodically (id tokens TTL ~60min, safe to re-fetch each iteration)
    TOKEN=$(gcloud auth print-identity-token 2>/dev/null)

    # Check if cache is populated via /api/state introspection:
    CACHE_INFO=$(curl -s -H "Authorization: Bearer $TOKEN" --max-time 15 "$TAG_URL/api/state" 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    snaps = d.get('_last_snapshots') or (d.get('stats') or {}).get('_last_snapshots') or {}
    if not snaps:
        quotes = d.get('quotes') or {}
        signals = d.get('signals') or {}
        if quotes or signals:
            print(f'PARTIAL:{len(quotes)}_quotes_{len(signals)}_signals')
        else:
            print('EMPTY')
    else:
        n = len(snaps) if isinstance(snaps, dict) else 0
        print(f'OK:{n}_tickers')
except Exception as e:
    print(f'ERROR:{str(e)[:60]}')
" 2>&1)

    echo "[t+${ELAPSED}s] Cache check: $CACHE_INFO"

    case "$CACHE_INFO" in
        OK:*)
            echo "✅ Cache populado — procediendo con promote"
            break
            ;;
        PARTIAL:*)
            echo "⚠️  Polling activo pero _last_snapshots aún no populado, esperando..."
            ;;
        EMPTY)
            echo "⚠️  Bot up pero sin data aún, esperando..."
            ;;
        ERROR:*)
            echo "⚠️  Health check failed: $CACHE_INFO"
            ;;
    esac

    if [ "$ELAPSED" -ge "$MAX_WAIT_SEC" ]; then
        break
    fi

    sleep "$CHECK_INTERVAL"
    ELAPSED=$((ELAPSED + CHECK_INTERVAL))
done

# Final check:
case "$CACHE_INFO" in
    OK:*)
        echo "🚀 Promoting $TAG → 100% traffic..."
        gcloud run services update-traffic "$SERVICE" --region="$REGION" --project="$PROJECT" \
            --to-tags="$TAG=100" 2>&1 | tail -5

        # Find previous tag(s) at 0% to cleanup:
        sleep 30
        PREV_TAGS=$(gcloud run services describe "$SERVICE" --region="$REGION" --project="$PROJECT" \
            --format=json 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
to_cleanup = []
for t in d.get('status', {}).get('traffic', []):
    tag = t.get('tag')
    pct = t.get('percent', 0) or 0
    if tag and tag != '$TAG' and pct == 0:
        to_cleanup.append(tag)
print(','.join(to_cleanup))
" 2>/dev/null)

        if [ -n "$PREV_TAGS" ]; then
            echo "🧹 Removing old tags: $PREV_TAGS"
            gcloud run services update-traffic "$SERVICE" --region="$REGION" --project="$PROJECT" \
                --remove-tags="$PREV_TAGS" 2>&1 | tail -3
        fi

        echo "✅ Promote complete: $TAG ($TAG_REV) @ 100% traffic"
        echo "  End time = $(date -u +%Y-%m-%dT%H:%M:%SZ)"
        exit 0
        ;;
    *)
        echo "🚨 TIMEOUT después de ${MAX_WAIT_SEC}s — cache nunca populó (último estado: $CACHE_INFO)" >&2
        echo "🚨 NO promoting. Tag $TAG sigue en 0% traffic." >&2
        echo "🚨 Investigación manual requerida." >&2
        exit 1
        ;;
esac
