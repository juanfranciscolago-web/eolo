#!/usr/bin/env python3
# ============================================================
#  EOLO — Test de Estrategias (Smoke Test)
#
#  Fuerza señales BUY en todas las estrategias al precio real,
#  espera 1 minuto, y fuerza señales SELL.
#  Verifica que el pipeline completo funcione:
#    señal → trader.execute() → Firestore → Dashboard
#
#  Uso:
#    python test_strategies.py           → testea todas las estrategias
#    python test_strategies.py ema       → solo EMA
#    python test_strategies.py gap       → solo GAP
#    python test_strategies.py vwap      → solo VWAP+RSI
#    python test_strategies.py bollinger → solo Bollinger
#    python test_strategies.py orb       → solo ORB
# ============================================================
import sys
import time
from datetime import datetime
import pytz

from marketdata import MarketData
import bot_trader as trader
from secret_stuff import project_id

EASTERN      = pytz.timezone("America/New_York")
WAIT_SECONDS = 60    # 1 minuto entre BUY y SELL

# ── Un ticker representativo por estrategia ────────────────
# (evita conflictos de posición entre estrategias que comparten tickers)
STRATEGY_TICKERS = {
    "EMA":       ("SPY",  "EMA"),
    "GAP":       ("QQQ",  "GAP"),
    "VWAP_RSI":  ("SOXL", "VWAP_RSI"),
    "BOLLINGER": ("TSLL", "BOLLINGER"),
    "ORB":       ("NVDL", "ORB"),
}

# Filtro por argumento de línea de comandos
def get_strategies_to_test() -> dict:
    if len(sys.argv) < 2:
        return STRATEGY_TICKERS
    arg = sys.argv[1].lower()
    mapping = {
        "ema":       "EMA",
        "gap":       "GAP",
        "vwap":      "VWAP_RSI",
        "bollinger": "BOLLINGER",
        "orb":       "ORB",
    }
    key = mapping.get(arg)
    if not key:
        print(f"⚠️  Estrategia desconocida: '{arg}'. Opciones: ema, gap, vwap, bollinger, orb")
        sys.exit(1)
    return {key: STRATEGY_TICKERS[key]}


def build_signal(signal: str, ticker: str, price: float, strategy: str, budget: float) -> dict:
    """Construye un resultado de señal idéntico al que generaría una estrategia real."""
    return {
        "ticker":   ticker,
        "signal":   signal,
        "price":    price,
        "strategy": strategy,
        "_budget":  budget,
    }


def print_header(strategies: dict):
    now = datetime.now(EASTERN).strftime("%Y-%m-%d %H:%M:%S ET")
    strats = ", ".join(strategies.keys())
    print("\n" + "="*65)
    print("  🧪 EOLO — TEST DE ESTRATEGIAS (pipeline completo)")
    print(f"  {now}")
    print(f"  Estrategias : {strats}")
    print(f"  Modo        : 📄 PAPER")
    print(f"  Ciclo       : BUY → {WAIT_SECONDS}s → SELL")
    print("="*65 + "\n")


def main():
    strategies = get_strategies_to_test()
    budget     = trader.get_trade_budget()

    print_header(strategies)

    # Cargar posiciones existentes (para no doble-comprar)
    trader.load_positions()

    market_data = MarketData()

    # ── Obtener precios actuales ────────────────────────────
    tickers    = [v[0] for v in strategies.values()]
    all_prices = market_data.get_quotes(tickers)

    if not all_prices:
        print("  ❌ No se pudo obtener precios. Verificá el token de Schwab.")
        return

    print("  Precios obtenidos:")
    for t, p in all_prices.items():
        print(f"    {t:<6} → ${p}")

    # ── FASE 1: BUY ─────────────────────────────────────────
    print(f"\n{'─'*65}")
    print("  ▶ FASE 1 — Enviando señales BUY\n")

    buy_data = {}   # ticker → (price, strategy, shares)

    for strat_name, (ticker, tag) in strategies.items():
        price = all_prices.get(ticker)
        if not price:
            print(f"  ❌ {strat_name} | {ticker}: sin precio de mercado")
            continue

        signal = build_signal("BUY", ticker, price, tag, budget)

        prev_pos = trader.positions.get(ticker)
        trader.execute(signal)
        new_pos  = trader.positions.get(ticker)

        shares = trader.calculate_shares(price, budget)
        buy_data[ticker] = (price, tag, shares)

        if prev_pos != "LONG" and new_pos == "LONG":
            print(f"  🟢 BUY   [{strat_name:<10}] {ticker:<6} @ ${price}  x{shares} acc  (${shares*price:.2f})")
        elif prev_pos == "LONG":
            print(f"  ⚠️  SKIP  [{strat_name:<10}] {ticker:<6} — ya estaba LONG")
        else:
            print(f"  ❌ ERROR [{strat_name:<10}] {ticker:<6} — execute() no cambió posición")

    print(f"\n  ✅ Fase 1 completa — revisá el dashboard en eolo-trades/{datetime.now(EASTERN).strftime('%Y-%m-%d')}")

    # ── Cuenta regresiva ────────────────────────────────────
    print(f"\n{'─'*65}")
    print(f"  ⏱  Esperando {WAIT_SECONDS} segundos...\n")
    for remaining in range(WAIT_SECONDS, 0, -10):
        now = datetime.now(EASTERN).strftime("%H:%M:%S")
        bar = "█" * ((WAIT_SECONDS - remaining) // 5) + "░" * (remaining // 5)
        print(f"  [{now} ET]  {bar}  {remaining}s restantes", flush=True)
        time.sleep(min(10, remaining))

    # ── FASE 2: SELL ────────────────────────────────────────
    print(f"\n{'─'*65}")
    print("  ▶ FASE 2 — Obteniendo precios actualizados y enviando SELL\n")

    sell_tickers = list(buy_data.keys())
    sell_prices  = market_data.get_quotes(sell_tickers)

    results = []

    for ticker, (buy_price, tag, shares) in buy_data.items():
        sell_price = sell_prices.get(ticker)
        if not sell_price:
            print(f"  ❌ {ticker}: sin precio de venta")
            continue

        signal = build_signal("SELL", ticker, sell_price, tag, budget)

        prev_pos = trader.positions.get(ticker)
        trader.execute(signal)
        new_pos  = trader.positions.get(ticker)

        pnl     = round((sell_price - buy_price) * shares, 4)
        pnl_pct = round((sell_price - buy_price) / buy_price * 100, 3) if buy_price else 0
        emoji   = "🟢" if pnl >= 0 else "🔴"

        if prev_pos == "LONG" and new_pos != "LONG":
            print(f"  {emoji} SELL [{tag:<10}] {ticker:<6} @ ${sell_price}  P&L: ${pnl:+.4f} ({pnl_pct:+.3f}%)")
            results.append((tag, ticker, buy_price, sell_price, shares, pnl, pnl_pct, "OK"))
        elif prev_pos != "LONG":
            print(f"  ⚠️  SKIP [{tag:<10}] {ticker:<6} — no estaba LONG al momento del SELL")
            results.append((tag, ticker, buy_price, sell_price, shares, pnl, pnl_pct, "SKIP"))
        else:
            print(f"  ❌ ERROR [{tag:<10}] {ticker:<6} — execute() no cerró posición")
            results.append((tag, ticker, buy_price, sell_price, shares, pnl, pnl_pct, "ERROR"))

    # ── Resumen ─────────────────────────────────────────────
    if not results:
        print("\n  ⚠️  No hay resultados — ninguna posición se abrió/cerró correctamente.")
        return

    total_pnl = sum(r[5] for r in results if r[7] == "OK")
    wins      = sum(1 for r in results if r[5] >= 0 and r[7] == "OK")
    losses    = sum(1 for r in results if r[5] < 0  and r[7] == "OK")
    ok_count  = sum(1 for r in results if r[7] == "OK")

    print(f"\n{'='*65}")
    print("  📊 RESUMEN DEL TEST")
    print(f"{'='*65}")
    print(f"  {'ESTRATEGIA':<12} {'TICKER':<6} {'COMPRA':>9} {'VENTA':>9} {'ACCIONES':>8} {'P&L $':>10} {'%':>8} {'EST':>5}")
    print("  " + "─"*59)
    for tag, ticker, bp, sp, sh, pnl, pct, status in results:
        marker = "▲" if pnl >= 0 else "▼"
        estat  = "✓" if status == "OK" else ("⏭" if status == "SKIP" else "✗")
        print(f"  {tag:<12} {ticker:<6} ${bp:>8} ${sp:>8} {sh:>8}  ${pnl:>+9.4f} {pct:>+7.3f}% {marker} {estat}")
    print("  " + "─"*59)
    print(f"  {'TOTAL P&L':<25} ${total_pnl:>+9.4f}   ({wins}✓  {losses}✗  de {ok_count} trades)")
    print(f"{'='*65}")
    print(f"\n  ✅ Test completo — {ok_count} pares BUY/SELL registrados en Firestore")
    print(f"  👉 Revisalos en el dashboard → eolo-trades/{datetime.now(EASTERN).strftime('%Y-%m-%d')}\n")


if __name__ == "__main__":
    main()
