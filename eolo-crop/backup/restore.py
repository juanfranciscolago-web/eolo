"""Restore CLI — Master Plan v2.1 sec 18.4.

Usage (run from eolo-crop/):
    python3 -m backup.restore --type kb --date 2026-05-28
    python3 -m backup.restore --type decisions --start 2026-05-28T00:00:00Z --end 2026-05-28T23:59:59Z
"""
import argparse
import gzip
import sys
from datetime import datetime
from pathlib import Path

from google.cloud import firestore

_PROJECT_ID = "eolo-schwab-agent"
_BACKUP_DB = "eolo-backups"


def _client() -> firestore.Client:
    return firestore.Client(project=_PROJECT_ID, database=_BACKUP_DB)


def restore_kb(date: str, out_path: Path) -> int:
    doc = _client().collection("kb_snapshots").document(date).get()
    if not doc.exists:
        print(f"ERROR: no snapshot for {date}", file=sys.stderr)
        return 1
    data = doc.to_dict()
    decompressed = gzip.decompress(data["content_gzip"])
    out_path.write_bytes(decompressed)
    print(f"Restored {data['filename']} ({len(decompressed)} bytes) to {out_path}")
    return 0


def restore_decisions(start: str, end: str) -> int:
    start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
    date_str = start_dt.strftime("%Y-%m-%d")
    items = (
        _client()
        .collection("decisions")
        .document(date_str)
        .collection("items")
        .stream()
    )
    count = 0
    for item in items:
        print(f"  {item.id}: {item.to_dict().get('verdict', '?')}")
        count += 1
    print(f"Total: {count} decisions for {date_str}")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--type", required=True, choices=["kb", "decisions", "trades", "full"])
    parser.add_argument("--date")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    if args.type == "kb":
        out = Path(args.out) if args.out else Path(f"restored_kb_{args.date}.xlsx")
        return restore_kb(args.date, out)
    elif args.type == "decisions":
        return restore_decisions(args.start, args.end)
    print(f"Type {args.type} not yet implemented")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
