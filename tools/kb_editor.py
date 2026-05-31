"""KB Editor CLI (UP-1.2 phase 1, read-only).

Usage:
    python tools/kb_editor.py list-rules
    python tools/kb_editor.py next-id
    python tools/kb_editor.py show-rule TR-Juan-043
    python tools/kb_editor.py validate
    python tools/kb_editor.py list-cases
    python tools/kb_editor.py stats

All commands accept `--kb-path PATH` to target a non-default workbook.

Phase 1 is intentionally read-only — phase 2 will add `add-rule` / `edit-rule`,
Anthropic suggestion integration and git automation.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Iterator

from openpyxl.workbook.workbook import Workbook

# Ensure `tools` is importable when this file is invoked as a script from any cwd.
_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from tools import kb_schema as S  # noqa: E402
from tools import kb_validators as V  # noqa: E402


# ── Iterators ─────────────────────────────────────────────────────────────
def _iter_rule_rows(wb: Workbook) -> Iterator[tuple[int, str, tuple]]:
    """Yield (row_idx, canonical_rule_id, row_tuple) for every rule.

    Args:
        wb: Loaded workbook.

    Yields:
        (1-indexed row number, normalised TR-Juan-NNN, raw row tuple).

    Raises:
        KeyError: If the Decision_Rules sheet is missing.
    """
    if S.DECISION_RULES_SHEET not in wb.sheetnames:
        raise KeyError(f"Sheet '{S.DECISION_RULES_SHEET}' missing from workbook")
    ws = wb[S.DECISION_RULES_SHEET]
    for row_idx, row in enumerate(
        ws.iter_rows(min_row=S.DECISION_RULES_DATA_START_ROW, values_only=True),
        start=S.DECISION_RULES_DATA_START_ROW,
    ):
        if not row or row[0] is None:
            continue
        if not str(row[0]).strip().startswith("TR-"):
            continue
        rid = V.extract_rule_id(row[0])
        if rid is None:
            continue
        yield row_idx, rid, row


def _iter_case_rows(wb: Workbook) -> Iterator[tuple[int, str, dict[str, object]]]:
    """Yield (row_idx, case_id, {header: value}) for every case.

    Args:
        wb: Loaded workbook.

    Yields:
        (1-indexed row number, case_id, dict mapping header to cell value).

    Raises:
        KeyError: If the Cases sheet is missing.
    """
    if S.CASES_SHEET not in wb.sheetnames:
        raise KeyError(f"Sheet '{S.CASES_SHEET}' missing from workbook")
    ws = wb[S.CASES_SHEET]
    headers: list[str] = [
        (str(c.value).strip() if c.value is not None else "")
        for c in ws[S.CASES_HEADER_ROW]
    ]
    cid_idx = headers.index("case_id") if "case_id" in headers else 0
    for row_idx, row in enumerate(
        ws.iter_rows(min_row=S.CASES_DATA_START_ROW, values_only=True),
        start=S.CASES_DATA_START_ROW,
    ):
        if not row or cid_idx >= len(row) or row[cid_idx] is None:
            continue
        case_id = str(row[cid_idx]).strip()
        if not case_id:
            continue
        record = {
            h: row[i] if i < len(row) else None
            for i, h in enumerate(headers)
            if h
        }
        yield row_idx, case_id, record


# ── Commands ──────────────────────────────────────────────────────────────
def cmd_list_rules(wb: Workbook) -> int:
    """Print every Rule_ID with its tier, one per line.

    Args:
        wb: Loaded workbook.

    Returns:
        Process exit code (0 on success).
    """
    tier_col_idx = S.DECISION_RULES_COLS["tier"] - 1
    count = 0
    for _, rid, row in _iter_rule_rows(wb):
        tier = ""
        if len(row) > tier_col_idx and row[tier_col_idx] is not None:
            tier = str(row[tier_col_idx]).strip().upper()
        print(f"{rid}  [{tier or '?'}]")
        count += 1
    print(f"\nTotal: {count} rules")
    return 0


def cmd_next_id(wb: Workbook) -> int:
    """Print the next free TR-Juan-NNN id.

    The "next free" id is one greater than the largest numeric component
    currently present in the Decision_Rules sheet.

    Args:
        wb: Loaded workbook.

    Returns:
        0 on success.
    """
    max_num = 0
    for _, rid, _ in _iter_rule_rows(wb):
        m = S.RULE_ID_REGEX.search(rid)
        if m:
            max_num = max(max_num, int(m.group(1)))
    print(S.RULE_ID_FORMAT.format(num=max_num + 1))
    return 0


def cmd_show_rule(wb: Workbook, rule_id_arg: str) -> int:
    """Print every column of the rule whose canonical id matches `rule_id_arg`.

    Args:
        wb: Loaded workbook.
        rule_id_arg: The id to look up (any form containing TR-Juan-NNN).

    Returns:
        0 if found, 1 if no rule matches.
    """
    target = V.extract_rule_id(rule_id_arg)
    if target is None:
        print(f"ERROR: '{rule_id_arg}' is not a valid Rule_ID", file=sys.stderr)
        return 1

    for row_idx, rid, row in _iter_rule_rows(wb):
        if rid != target:
            continue
        print(f"Rule {rid} (row {row_idx})")
        print("─" * 60)
        for header, col_pos in S.DECISION_RULES_COLS.items():
            idx = col_pos - 1
            value = row[idx] if idx < len(row) else None
            display = "" if value is None else str(value)
            print(f"  {header:>22}: {display}")
        return 0

    print(f"ERROR: rule {target} not found", file=sys.stderr)
    return 1


def cmd_validate(wb: Workbook) -> int:
    """Run every validator and report the result.

    Args:
        wb: Loaded workbook.

    Returns:
        0 if every check passed, 1 otherwise.
    """
    report = V.validate_all(wb)
    total_errors = sum(len(errs) for errs in report.values())

    for check, errors in report.items():
        if errors:
            print(f"[FAIL] {check}: {len(errors)} issue(s)")
            for e in errors:
                print(f"   - {e}")
        else:
            print(f"[ OK ] {check}")

    print()
    if total_errors == 0:
        print("✓ All validations passed")
        return 0
    print(f"✗ {total_errors} validation issue(s) across {sum(1 for e in report.values() if e)} check(s)")
    return 1


def cmd_list_cases(wb: Workbook) -> int:
    """Print every case_id and its case_quality.

    Args:
        wb: Loaded workbook.

    Returns:
        0 on success.
    """
    count = 0
    for _, case_id, record in _iter_case_rows(wb):
        quality_raw = record.get("case_quality")
        quality = str(quality_raw).strip().upper() if quality_raw else "?"
        print(f"{case_id}  [{quality}]")
        count += 1
    print(f"\nTotal: {count} cases")
    return 0


def cmd_stats(wb: Workbook) -> int:
    """Print rules-per-tier and cases-per-quality counts.

    Args:
        wb: Loaded workbook.

    Returns:
        0 on success.
    """
    tier_col_idx = S.DECISION_RULES_COLS["tier"] - 1
    tier_counts: Counter[str] = Counter()
    rule_total = 0
    for _, _, row in _iter_rule_rows(wb):
        rule_total += 1
        tier = ""
        if len(row) > tier_col_idx and row[tier_col_idx] is not None:
            tier = str(row[tier_col_idx]).strip().upper()
        tier_counts[tier or "?"] += 1

    quality_counts: Counter[str] = Counter()
    case_total = 0
    for _, _, record in _iter_case_rows(wb):
        case_total += 1
        q_raw = record.get("case_quality")
        q = str(q_raw).strip().upper() if q_raw else "?"
        quality_counts[q] += 1

    print(f"Rules: {rule_total}")
    for tier in S.TIER_PRIORITY_ORDER:
        print(f"  {tier:>14}: {tier_counts.get(tier, 0)}")
    extras = sorted(set(tier_counts) - set(S.TIER_PRIORITY_ORDER))
    for tier in extras:
        print(f"  {tier:>14}: {tier_counts[tier]}  (unexpected)")

    print(f"\nCases: {case_total}")
    for q in ("GOLD", "SILVER", "BRONZE"):
        print(f"  {q:>14}: {quality_counts.get(q, 0)}")
    extras = sorted(set(quality_counts) - {"GOLD", "SILVER", "BRONZE"})
    for q in extras:
        print(f"  {q:>14}: {quality_counts[q]}  (unexpected)")
    return 0


# ── Entrypoint ────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    """Build the argparse CLI.

    Returns:
        Configured ArgumentParser with subcommands wired to handlers.
    """
    parser = argparse.ArgumentParser(
        prog="kb_editor",
        description="EOLO ThetaHarvest KB editor (UP-1.2 phase 1, read-only)",
    )
    parser.add_argument(
        "--kb-path",
        type=Path,
        default=S.DEFAULT_KB_PATH,
        help=f"Path to the KB .xlsx (default: {S.DEFAULT_KB_PATH})",
    )

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list-rules", help="List Rule_IDs with their tier")
    sub.add_parser("next-id", help="Print the next free TR-Juan-NNN")
    p_show = sub.add_parser("show-rule", help="Show a rule's full row")
    p_show.add_argument("rule_id", help="Rule id (e.g. TR-Juan-043)")
    sub.add_parser("validate", help="Run schema and integrity validators")
    sub.add_parser("list-cases", help="List case_ids with their case_quality")
    sub.add_parser("stats", help="Count rules per tier and cases per quality")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint.

    Args:
        argv: Optional argument list; defaults to sys.argv[1:].

    Returns:
        Process exit code.
    """
    args = build_parser().parse_args(argv)

    try:
        wb = V.load_kb(args.kb_path)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    handlers = {
        "list-rules": lambda: cmd_list_rules(wb),
        "next-id":    lambda: cmd_next_id(wb),
        "show-rule":  lambda: cmd_show_rule(wb, args.rule_id),
        "validate":   lambda: cmd_validate(wb),
        "list-cases": lambda: cmd_list_cases(wb),
        "stats":      lambda: cmd_stats(wb),
    }
    try:
        return handlers[args.command]()
    except KeyError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
