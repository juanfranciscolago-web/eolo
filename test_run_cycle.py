"""Ejecuta UN run_cycle completo igual que el bot en Cloud Run.

Esto replica lo que hace bot_main.main() pero solo una vez, con TF=1m.
Vemos si las estrategias devuelven BUY/SELL/HOLD y si hay errores.
"""
import sys, os
sys.path.insert(0, "Bot")
os.environ.setdefault("GCP_PROJECT", "eolo-schwab-agent")

from bot_main import run_cycle, get_global_settings
from marketdata import MarketData
import bot_trader as trader

# ── Forzamos PAPER para no abrir trades reales desde el test ──
trader.PAPER_TRADING = True
print(f"PAPER_TRADING forzado a: {trader.PAPER_TRADING}\n")

settings = get_global_settings()
# TF=1m — el más rápido, el que corre en cada tick
settings["_macro_feeds"] = None  # no levantar macro polling

md = MarketData()
md.frequency = 1

print("=" * 76)
print("  LANZANDO run_cycle(tf=1m) CON DATOS EN VIVO")
print("=" * 76)

try:
    run_cycle(md, settings, timeframe=1)
    print("\n✅ run_cycle terminó sin excepción")
except Exception as e:
    import traceback
    print(f"\n❌ run_cycle LANZÓ: {type(e).__name__}: {e}")
    traceback.print_exc()
