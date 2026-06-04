"""CLI para revisar feedback chat artifacts (Wave 2 OMNI-SPRINT).

Uso:
  python3 tools/review_feedback.py [--date YYYY-MM-DD] [--auto-yes]

Lista artifacts del día (rule_proposals, case_upgrades, lessons_learned, qa_tickets).
Permite approve/reject/skip interactivo.
- Approve → Firestore approved_{type}/{date}/items/
- Reject  → Firestore rejected_{type}/{date}/items/
"""
from __future__ import annotations
import argparse
import sys
from datetime import datetime, timezone


def main() -> int:
    parser = argparse.ArgumentParser(description="Review feedback artifacts")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD (default: today UTC)")
    parser.add_argument("--auto-yes", action="store_true", help="auto-approve all (testing)")
    args = parser.parse_args()

    date_str = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        from google.cloud import firestore
        db = firestore.Client()
    except Exception as e:
        print(f"Firestore init failed: {e}")
        return 1

    colls = ["rule_proposals", "case_upgrades", "lessons_learned", "qa_tickets"]
    all_items: list[dict] = []
    for coll in colls:
        try:
            docs = db.collection(coll).document(date_str).collection("items").stream()
            for d in docs:
                data = d.to_dict() or {}
                data["_id"] = d.id
                data["_coll"] = coll
                all_items.append(data)
        except Exception:
            pass

    print(f"\n=== Feedback Artifacts {date_str} ===")
    print(f"Total: {len(all_items)} artifacts\n")

    if not all_items:
        print("No artifacts to review.")
        return 0

    decisions: list[tuple[str, dict]] = []
    for i, item in enumerate(all_items, 1):
        print(f"\n--- Artifact {i}/{len(all_items)} [{item['_coll']}] ---")
        for k, v in item.items():
            if k in ("_id", "_coll"):
                continue
            print(f"  {k}: {str(v)[:300]}")

        if args.auto_yes:
            choice = "a"
        else:
            try:
                choice = input("\n[a]pprove / [r]eject / [s]kip / [q]uit: ").lower().strip()
            except (EOFError, KeyboardInterrupt):
                print("\nAborted")
                break

        if choice == "q":
            break
        elif choice == "a":
            target_coll = f"approved_{item['_coll']}"
            db.collection(target_coll).document(date_str).collection("items").document(item["_id"]).set({
                **item,
                "action": "approve",
                "by": "juan",
                "at": datetime.now(timezone.utc).isoformat(),
            })
            decisions.append(("approved", item))
            print(f"  ✓ Approved → {target_coll}/{date_str}/items/{item['_id']}")
        elif choice == "r":
            db.collection(f"rejected_{item['_coll']}").document(date_str).collection("items").document(item["_id"]).set({
                **item,
                "action": "reject",
                "by": "juan",
                "at": datetime.now(timezone.utc).isoformat(),
            })
            decisions.append(("rejected", item))
            print("  ✗ Rejected")
        else:
            print("  → Skipped")

    print("\n=== Summary ===")
    print(f"Approved: {sum(1 for s, _ in decisions if s == 'approved')}")
    print(f"Rejected: {sum(1 for s, _ in decisions if s == 'rejected')}")
    print(f"Skipped:  {len(all_items) - len(decisions)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
