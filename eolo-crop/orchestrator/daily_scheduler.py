"""Daily scheduler — orchestrator de 6 fases del playbook diario.

Master Plan v2.1 sec 8.

Fases:
1. Pre-market (08:00-09:30 ET): watchlist build
2. Open (09:30-10:30 ET): primer entry window
3. Mid-day (10:30-13:30 ET): ventana operativa principal
4. Afternoon (13:30-15:30 ET): monitor + ajustes defensivos
5. Power hour (15:30-16:00 ET): no nuevas entradas
6. Post-market (16:00-17:00 ET): daily journal + chat feedback

Skeleton — integración real con bot CROP en follow-up.
"""
import logging
from datetime import datetime
from typing import Callable, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")


class DailyScheduler:
    """APScheduler wrapper para las 6 fases del playbook diario.

    Usar APScheduler en producción; para tests/demo skeleton synchronous.
    """

    PHASES = [
        ("pre_market", "08:00", "09:30", "Build watchlist + IV rank screen + earnings blacklist"),
        ("open", "09:30", "10:30", "First entry window (no opera 9:30-9:45, ruido puro)"),
        ("mid_day", "10:30", "13:30", "Main entry window (sweet spot 11:30-13:30)"),
        ("afternoon", "13:30", "15:30", "Monitor + defensa de cortas en problemas"),
        ("power_hour", "15:30", "16:00", "No nuevas entradas (gamma squeeze risk)"),
        ("post_market", "16:00", "17:00", "Daily journal + chat feedback opening"),
    ]

    def __init__(self, callbacks: Optional[dict] = None):
        self.callbacks: dict[str, Callable] = callbacks or {}
        self._scheduler = None

    def _current_phase(self, now: Optional[datetime] = None) -> Optional[str]:
        """Return current phase name based on ET time."""
        now = now or datetime.now(ET)
        # Skip weekends
        if now.weekday() >= 5:
            return None
        cur_minutes = now.hour * 60 + now.minute
        for name, start, end, _ in self.PHASES:
            sh, sm = map(int, start.split(":"))
            eh, em = map(int, end.split(":"))
            if sh * 60 + sm <= cur_minutes < eh * 60 + em:
                return name
        return None

    def run_phase(self, phase_name: str) -> dict:
        """Execute the phase callback if registered. Returns status."""
        cb = self.callbacks.get(phase_name)
        if cb is None:
            return {"phase": phase_name, "status": "skipped_no_callback"}
        try:
            result = cb()
            return {"phase": phase_name, "status": "ok", "result": result}
        except Exception as e:
            logger.error(f"[scheduler] phase {phase_name} failed: {e}")
            return {"phase": phase_name, "status": "error", "error": str(e)[:200]}

    def start(self):
        """Start APScheduler with cron jobs for each phase."""
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.cron import CronTrigger
            self._scheduler = BackgroundScheduler(timezone=ET)
            for name, start, _, _ in self.PHASES:
                sh, sm = map(int, start.split(":"))
                self._scheduler.add_job(
                    self.run_phase,
                    CronTrigger(day_of_week="mon-fri", hour=sh, minute=sm, timezone=ET),
                    args=[name],
                    id=f"phase_{name}",
                )
            self._scheduler.start()
            logger.info("[scheduler] started")
        except ImportError:
            logger.warning("[scheduler] APScheduler not installed; running in skeleton mode")

    def stop(self):
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
