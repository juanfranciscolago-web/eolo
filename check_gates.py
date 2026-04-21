"""Chequea los gates que pueden suprimir trades: daily_loss_cap, budget, active_tickers."""
import sys, os
sys.path.insert(0, "Bot")
os.environ.setdefault("GCP_PROJECT", "eolo-schwab-agent")

from bot_main import (
    compute_today_pnl,
    is_daily_loss_cap_hit,
    get_global_settings,
    get_active_strategies,
    get_active_tickers,
    TICKERS_EMA_GAP,
    TICKERS_LEVERAGED,
)

settings = get_global_settings()
print("=== settings globales ===")
print(f"  bot_active:            {settings.get('bot_active')}")
print(f"  close_all:             {settings.get('close_all')}")
print(f"  budget:                {settings.get('budget')}")
print(f"  daily_loss_cap_pct:    {settings.get('daily_loss_cap_pct')}")
print(f"  trading_hours_enabled: {settings.get('trading_hours_enabled')}")
print(f"  active_timeframes:     {settings.get('active_timeframes')}")
print(f"  confluence_mode:       {settings.get('confluence_mode')}")

print("\n=== PnL + cap ===")
try:
    pnl = compute_today_pnl()
    print(f"  compute_today_pnl():     {pnl}")
except Exception as e:
    print(f"  compute_today_pnl() ERROR: {type(e).__name__}: {e}")

try:
    cap_hit = is_daily_loss_cap_hit(settings)
    print(f"  is_daily_loss_cap_hit(): {cap_hit}")
except Exception as e:
    print(f"  is_daily_loss_cap_hit() ERROR: {type(e).__name__}: {e}")

print("\n=== tickers activos ===")
print(f"  TICKERS_EMA_GAP hardcoded:   {TICKERS_EMA_GAP}")
print(f"  TICKERS_LEVERAGED hardcoded: {TICKERS_LEVERAGED}")
print(f"  get_active_tickers(EMA_GAP): {get_active_tickers(TICKERS_EMA_GAP)}")
print(f"  get_active_tickers(LEV):     {get_active_tickers(TICKERS_LEVERAGED)}")

print("\n=== estrategias activas (sample de 5) ===")
strats = get_active_strategies()
on = sorted([k for k,v in strats.items() if v])
off = sorted([k for k,v in strats.items() if not v])
print(f"  ON  ({len(on)}): {on[:5]} ...")
print(f"  OFF ({len(off)}): {off}")
