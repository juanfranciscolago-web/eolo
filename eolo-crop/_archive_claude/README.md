# Archive — Claude code (FASE 2 Cleanup)

Code archived during FASE 2 cleanup (CROP → theta_harvest only).

## Why archived (not deleted)

Code may be useful as reference for:
- Future re-implementation of Claude-driven options strategy
- Understanding original architecture decisions
- Migration to V2 multi-strategy bot

## Contents

- `claude/options_brain.py` — OptionsBrain LLM decision engine (394 lines)
- `claude/claude_bot.py` — ClaudeBotEngine alternative LLM path (404 lines)
- `smoke_test.py` — original smoke test (imports OptionsBrain)

## Restore instructions

```bash
git mv eolo-crop/_archive_claude/claude eolo-crop/claude
git mv eolo-crop/_archive_claude/smoke_test.py eolo-crop/smoke_test.py
# Then re-add imports + instantiations in crop_main.py
# Reference: tag pre-cleanup-crop-theta-only-20260511_1652
```

## Date archived

2026-05-11 (FASE 2 Cleanup CROP → theta_harvest only)

## Related docs

- docs/PLAN_CLEANUP_CROP_THETA_ONLY.md
- docs/FASE_2_CLEANUP_EJECUTABLE.md
- docs/notas_v3-8_2026-05-11.txt (sections 14-16)
