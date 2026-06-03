"""Cloud Functions entry point — re-exports auto_close.disaster_recovery_handler.

Gen 2 Python runtime requires main.py at source root. The actual handler
logic lives in auto_close.py (also imported by the bot internally for
testing without deploy).
"""
from auto_close import disaster_recovery_handler  # noqa: F401
