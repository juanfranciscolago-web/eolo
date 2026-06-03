"""Sub-C MEGATERMINATOR: real 60d backtest handler triggered Sunday 18:00 ET.

Reemplaza el handler placeholder previo. Invoca backtest/runner.run_backtest
sobre los últimos 60 días weekday con tickers core, persiste a Firestore.
"""
from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
import logging
import subprocess
import sys
import os

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _get_auth_token(engine_url: str) -> Optional[str]:
    """gcloud identity token con audiences fallback."""
    try:
        return subprocess.check_output(
            ["gcloud", "auth", "print-identity-token", "--audiences", engine_url],
            text=True, stderr=subprocess.PIPE,
        ).strip()
    except Exception:
        try:
            return subprocess.check_output(
                ["gcloud", "auth", "print-identity-token"], text=True,
            ).strip()
        except Exception as e:
            logger.error(f"[weekly_review] auth token failed: {e}")
            return None


def run_weekly_backtest(
    tickers: Optional[list[str]] = None,
    days_back: int = 60,
    budget_cap_usd: float = 12.0,
    engine_url: str = "https://llm-engine-service-nmjz4iwcea-uc.a.run.app",
    sample_hours: Optional[list[int]] = None,
) -> dict:
    """Run 60d backtest. Returns summary dict.

    Persiste a Firestore weekly_reviews/{week_iso}/results y deja markdown en
    /tmp/weekly_review_{date}.md.
    """
    from backtest.runner import run_backtest

    tickers = tickers or ["SPY", "QQQ", "IWM"]
    sample_hours = sample_hours or [10, 14]

    today = date.today()
    end = today
    start = today - timedelta(days=days_back)

    auth_token = _get_auth_token(engine_url)
    if not auth_token:
        return {"status": "ERROR_NO_AUTH_TOKEN"}

    output_dir = f"/tmp/weekly_review_{today.isoformat()}"
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    summary = run_backtest(
        tickers=tickers,
        start_date=start,
        end_date=end,
        auth_token=auth_token,
        sample_hours=sample_hours,
        engine_url=engine_url,
        use_prescreen=True,
        budget_cap_usd=budget_cap_usd,
        output_dir=output_dir,
    )

    _persist_to_firestore(today, summary)
    return summary


def _persist_to_firestore(today: date, summary: dict) -> bool:
    """Write weekly review summary to Firestore."""
    try:
        from google.cloud import firestore as _fs
        db = _fs.Client()
        iso_year, iso_week, _ = today.isocalendar()
        week_id = f"{iso_year}-W{iso_week:02d}"
        db.collection("weekly_reviews").document(week_id).set({
            "ran_at":        datetime.now(timezone.utc).isoformat(),
            "date":          today.isoformat(),
            "summary":       summary,
        })
        return True
    except Exception as e:
        logger.warning(f"[weekly_review] firestore write failed: {e}")
        return False


def fetch_latest_weekly_review() -> Optional[dict]:
    """Read most recent weekly review doc from Firestore."""
    try:
        from google.cloud import firestore as _fs
        db = _fs.Client()
        docs = list(db.collection("weekly_reviews").order_by(
            "ran_at", direction=_fs.Query.DESCENDING
        ).limit(1).stream())
        if not docs:
            return None
        return docs[0].to_dict()
    except Exception as e:
        logger.warning(f"[weekly_review] firestore read failed: {e}")
        return None
