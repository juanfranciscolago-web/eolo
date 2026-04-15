#!/usr/bin/env python3
# ============================================================
#  EOLO — Test Paper Trade
#  Compra 1 acción de cada ticker al precio real (quote),
#  espera 5 minutos, vende todo al precio real actualizado.
# ============================================================
import time
from datetime import datetime
import pytz

from marketdata import MarketData
from secret_stuff import project_id

EASTERN      = pytz.timezone("America/New_York")
WAIT_SECONDS = 300  # 5 minutos

ALL_TICKERS = [
    "SPY", "QQQ", "AAPL", "TSLA", "NVDA",
    "SOXL", "TSLL", "NVDL", "TQQQ",
]


def log_trade_to_firestore(action, ticker, shares, price, strategy="TEST"):
    """Guarda el trade en Firestore para que aparezca en el dashboard."""
    try:
        from google.cloud import firestore
        timestamp = datetime.now(EASTERN).strftime("%Y-%m-%d %H:%M:%S")
        today     = datetime.now(EASTERN).strftime("%Y-%m-%d")
        total_usd = round(shares * price, 2)
        db  = firestore.Client(project=project_id)
        doc = db.collection("eolo-trades").document(today)
        doc.set({
            f"{timestamp}_{ticker}_{action}": {
                "timestamp": timestamp,
                "mode":      "PAPER",
                "action":    action,
                "ticker":    ticker,
                "shares":    shares,
                "price":     price,
                "total_usd": total_usd,
                "strategy":  strategy,
            }
        }, merge=True)
    except Exception as e:
        print(f"  ⚠️  Firestore log fallo: {e}")


def main():
    print("\n" + "="*60)
    print("  🧪 EOLO — TEST PAPER TRADE (precios en tiempo real)")
    print(f"  {datetime.now(EASTERN).strftime('%Y-%m-%d %H:%M:%S ET')}")
    print("="*60)
    print(f"  Tickers  : {ALL_TICKERS}")
    print(f"  Modo     : 📄 PAPER")
    print(f"  Espera   : {WAIT_SECONDS // 60} minutos entre compra y venta")
    print("="*60 + "\n")

    market_data = MarketData()

    # ── FASE 1: Obtener precios actuales y BUY ─────────────
    print("▶ FASE 1 — Obteniendo cotizaciones en tiempo real...\n")
    buy_prices = market_data.get_quotes(ALL_TICKERS)

    if not buy_prices:
        print("  ❌ No se pudo obtener precios. Verificá el token de Schwab.")
        return

    print("  Precios de compra:")
    for ticker, price in buy_prices.items():
        log_trade_to_firestore("BUY", ticker, 1, price)
        print(f"  🟢 BUY  {ticker:<6} @ ${price}")

    print(f"\n  ✅ {len(buy_prices)} posiciones abiertas en PAPER")
    print(f"\n⏱  Esperando {WAIT_SECONDS // 60} minutos...\n")

    # Cuenta regresiva
    for remaining in range(WAIT_SECONDS, 0, -30):
        mins = remaining // 60
        secs = remaining % 60
        now  = datetime.now(EASTERN).strftime("%H:%M:%S")
        print(f"  [{now} ET] ⏳ {mins}:{secs:02d} restantes...", flush=True)
        time.sleep(min(30, remaining))

    # ── FASE 2: Obtener nuevos precios y SELL ──────────────
    print(f"\n▶ FASE 2 — Obteniendo cotizaciones actualizadas...\n")
    sell_prices = market_data.get_quotes(list(buy_prices.keys()))

    total_pnl = 0.0
    results   = []

    for ticker, buy_price in buy_prices.items():
        sell_price = sell_prices.get(ticker)
        if sell_price is None:
            print(f"  ❌ {ticker}: sin precio de venta")
            continue

        log_trade_to_firestore("SELL", ticker, 1, sell_price)

        pnl   = round(sell_price - buy_price, 4)
        pct   = round((pnl / buy_price) * 100, 3) if buy_price > 0 else 0
        emoji = "🟢" if pnl >= 0 else "🔴"
        total_pnl += pnl
        results.append((ticker, buy_price, sell_price, pnl, pct))
        print(f"  {emoji} SELL {ticker:<6} @ ${sell_price:<10}  P&L: ${pnl:+.4f} ({pct:+.3f}%)")

    # ── RESUMEN ────────────────────────────────────────────
    wins   = sum(1 for *_, pnl, _ in results if pnl >= 0)
    losses = sum(1 for *_, pnl, _ in results if pnl < 0)

    print("\n" + "="*60)
    print("  📊 RESUMEN DEL TEST")
    print("="*60)
    print(f"  {'TICKER':<8} {'COMPRA':>10} {'VENTA':>10} {'P&L $':>10} {'%':>8}")
    print("  " + "-"*52)
    for ticker, bp, sp, pnl, pct in sorted(results, key=lambda x: -x[3]):
        marker = "▲" if pnl >= 0 else "▼"
        print(f"  {ticker:<8} ${bp:>9} ${sp:>9} ${pnl:>+9.4f} {pct:>+7.3f}% {marker}")
    print("  " + "-"*52)
    print(f"  {'TOTAL P&L':<20} ${total_pnl:>+9.4f}   ({wins}✓ {losses}✗)")
    print("="*60)
    print(f"\n  ✅ Test completo — {len(results)} trades en PAPER guardados en Firestore")
    print(f"  👉 Verificalos en el dashboard bajo eolo-trades/{datetime.now(EASTERN).strftime('%Y-%m-%d')}\n")


if __name__ == "__main__":
    main()
