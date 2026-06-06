"""Schema constants for the EOLO ThetaHarvest KB (v1.8 Excel).

This module centralises sheet names, expected headers, valid enums and the
Rule_ID regex so that the editor and validators agree on a single source of
truth. Constants only — no logic.
"""
from __future__ import annotations

import re
from pathlib import Path

# Default KB location, resolved relative to the repo root (parent of `tools/`).
REPO_ROOT: Path = Path(__file__).resolve().parent.parent
DEFAULT_KB_PATH: Path = REPO_ROOT / "llm_engine_eolo" / "kb" / "EOLO_ThetaHarvest_v1.8.xlsx"

# All sheets we expect the v1.8 workbook to expose.
EXPECTED_SHEETS: list[str] = [
    "README",
    "Cases",
    "Field_Reference",
    "Decision_Rules",
    "Patterns_Library",
    "Glossary",
    "Success_Metrics",
    "Juan_Trading_Thesis",
]

# ── Decision_Rules sheet ───────────────────────────────────────────────────
DECISION_RULES_SHEET: str = "Decision_Rules"
DECISION_RULES_HEADER_ROW: int = 1
DECISION_RULES_DATA_START_ROW: int = 2

# Exact header strings in row 1 of Decision_Rules (order-sensitive).
DECISION_RULES_EXPECTED_HEADERS: list[str] = [
    "Rule_ID",
    "Trigger Conditions",
    "Action",
    "Confidence Required",
    "Source Cases",
    "Validated?",
    "Notes",
    "tier",
    "Deprecated_By",
]

# 1-indexed column positions for readability in the editor commands.
DECISION_RULES_COLS: dict[str, int] = {
    name: idx + 1 for idx, name in enumerate(DECISION_RULES_EXPECTED_HEADERS)
}

# Tier vocabulary — must match llm_engine_eolo/llm_engine/kb_loader.py:VALID_TIERS.
VALID_TIERS: frozenset[str] = frozenset(
    {"AXIOMA", "PROHIBITIVA", "MAESTRA", "PROTOCOLO", "TACTICAL_PLUS", "TACTICAL"}
)

# Display order for stats / priority sorting (high → low salience).
TIER_PRIORITY_ORDER: list[str] = [
    "AXIOMA",
    "PROHIBITIVA",
    "MAESTRA",
    "PROTOCOLO",
    "TACTICAL_PLUS",
    "TACTICAL",
]

# Matches "TR-Juan-NNN" anywhere in a string (tolerates suffixes like " ⭐" or
# " PROHIBITIVA"). Captures the 3-digit numeric component.
RULE_ID_REGEX: re.Pattern[str] = re.compile(r"TR-Juan-(\d{3})")
RULE_ID_FORMAT: str = "TR-Juan-{num:03d}"

# ── Cases sheet ────────────────────────────────────────────────────────────
CASES_SHEET: str = "Cases"
CASES_SECTION_BANNER_ROW: int = 1  # row 1 holds A./B./C. section banners
CASES_HEADER_ROW: int = 2          # row 2 holds the real field names
CASES_DATA_START_ROW: int = 4      # row 3 is description text; data begins at 4

# Minimum set of headers we rely on. The full sheet has ~72 columns grouped
# in sections A-H; the validator only asserts this required subset exists.
CASES_REQUIRED_HEADERS: frozenset[str] = frozenset(
    {"case_id", "ticker", "date", "case_quality", "tacit_rules_applied"}
)

# case_quality vocabulary observed in the v1.8 workbook.
VALID_CASE_QUALITY: frozenset[str] = frozenset({"GOLD", "SILVER", "BRONZE"})
