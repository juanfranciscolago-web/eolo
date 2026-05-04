# ============================================================
#  EOLO v2 — Smoke Test End-to-End
#
#  Simula un ciclo completo de análisis sin conectarse a Schwab
#  ni ejecutar órdenes reales. Usa datos sintéticos realistas.
#
#  Módulos testeados:
#    1. BufferMarketData     → convierte buffer a DataFrame
#    2. Estrategias v1       → señales técnicas sobre datos de buffer
#    3. IVSurface            → superficie de volatilidad
#    4. MispricingScanner    → detección de anomalías
#    5. OptionsBrain (mock)  → prompt que vería Claude
#    6. OptionsTrader        → construcción de símbolo OCC
#
#  Uso:
#    python smoke_test.py
#    python smoke_test.py --call-claude   (llama Claude real si hay API key)
# ============================================================
import sys
import os
import math
import time
import random
import json
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, ".."))
sys.path.insert(0, os.path.join(BASE_DIR, "..", "Bot"))

# ── Mock de dependencias de nube para el smoke test ───────
# helpers.py importa google.cloud al nivel del módulo;
# en el smoke test no hay credenciales GCP, así que mockeamos.
from unittest.mock import MagicMock

def _mock_get_access_token():
    return "SMOKE_TEST_FAKE_TOKEN"

_mock_helpers = MagicMock()
_mock_helpers.get_access_token     = _mock_get_access_token
_mock_helpers.retrieve_firestore_value = MagicMock(return_value=None)
_mock_helpers.store_firestore_value    = MagicMock()

sys.modules.setdefault("google",                     MagicMock())
sys.modules.setdefault("google.cloud",               MagicMock())
sys.modules.setdefault("google.cloud.secretmanager", MagicMock())
sys.modules.setdefault("google.cloud.firestore",     MagicMock())
sys.modules.setdefault("helpers",                    _mock_helpers)
sys.modules.setdefault("secret_stuff",               MagicMock())

from loguru import logger
logger.remove()
logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}", level="DEBUG")

CALL_CLAUDE = "--call-claude" in sys.argv
TICKER      = "SOXL"
S           = 32.40    # precio simulado del subyacente


# ══════════════════════════════════════════════════════════════
#  1. DATOS SINTÉTICOS
# ══════════════════════════════════════════════════════════════

def make_candle_buffer(ticker: str, n: int = 100, base_price: float = 32.0) -> list:
    """
    Genera N velas de 1min sintéticas con tendencia alcista suave
    y ruido realista, en el formato del WebSocket de Schwab.
    """
    candles = []
    price   = base_price
    ts_ms   = int((datetime.now(timezone.utc) - timedelta(minutes=n)).timestamp() * 1000)

    for i in range(n):
        # Random walk con drift positivo leve
        change = random.gauss(0.02, 0.25)
        open_  = price
        close  = round(price + change, 4)
        high   = round(max(open_, close) + abs(random.gauss(0, 0.1)), 4)
        low    = round(min(open_, close) - abs(random.gauss(0, 0.1)), 4)
        vol    = int(random.gauss(50000, 15000))

        candles.append({
            "symbol":    ticker,
            "type":      "candle",
            "time":      ts_ms + i * 60_000,   # cada minuto
            "open":      open_,
            "high":      high,
            "low":       low,
            "close":     close,
            "volume":    max(vol, 1000),
            "chart_day": 0,
        })
        price = close

    logger.info(f"[SMOKE] Buffer generado: {n} velas | {ticker} | "
                f"inicio=${base_price:.2f} → fin=${price:.2f}")
    return candles


def make_option_chain(ticker: str, S: float) -> dict:
    """
    Genera una cadena de opciones sintética con 3 vencimientos
    y strikes ATM ±20%. Incluye Greeks y IV realistas.
    """
    today = datetime.now(timezone.utc).date()
    expirations_dte = [14, 21, 35]

    calls_map = {}
    puts_map  = {}
    exp_dates = []

    for dte in expirations_dte:
        exp_date = (today + timedelta(days=dte)).strftime("%Y-%m-%d")
        exp_dates.append(exp_date)
        calls_map[exp_date] = {}
        puts_map[exp_date]  = {}

        T    = dte / 365
        # IV base ATM con smile (más alta OTM)
        atm_iv_base = 0.75

        # Strikes: ATM ±20% en pasos de $1
        for k_offset in range(-6, 7):
            K = round(S + k_offset, 1)
            if K <= 0:
                continue

            moneyness = math.log(S / K)
            # Smile: IV sube para OTM (puts y calls)
            iv = atm_iv_base + 0.08 * moneyness**2 + 0.04 * abs(moneyness)
            iv = round(max(0.40, min(iv, 2.5)), 4)

            # Black-Scholes simplificado para precios
            from analysis.greeks import BSGreeks
            bs_c = BSGreeks(S=S, K=K, T=T, r=0.05, sigma=iv)
            bs_p = BSGreeks(S=S, K=K, T=T, r=0.05, sigma=iv)

            c_theo = round(bs_c.call_price(), 4)
            p_theo = round(bs_p.put_price(),  4)

            # Spread realista: más ancho en OTM
            spread_factor = 1 + 2 * abs(moneyness)
            c_spread = round(max(0.02, 0.03 * c_theo * spread_factor), 4)
            p_spread = round(max(0.02, 0.03 * p_theo * spread_factor), 4)

            c_bid = round(max(0.01, c_theo - c_spread / 2), 4)
            c_ask = round(c_theo + c_spread / 2, 4)
            p_bid = round(max(0.01, p_theo - p_spread / 2), 4)
            p_ask = round(p_theo + p_spread / 2, 4)

            base_contract = {
                "dte":    dte,
                "strike": K,
                "expiration": exp_date,
                "iv":     round(iv * 100, 2),   # en %
                "delta":  round(bs_c.delta("call"), 4),
                "gamma":  round(bs_c.gamma(), 6),
                "theta":  round(bs_c.theta("call"), 4),
                "vega":   round(bs_c.vega(), 4),
                "rho":    round(bs_c.rho("call"), 4),
                "oi":     random.randint(200, 5000),
                "volume": random.randint(50, 3000),
                "itm":    K < S,
                "multiplier": 100,
                "theo":   c_theo,
                "last":   round((c_bid + c_ask) / 2, 4),
                "mark":   round((c_bid + c_ask) / 2, 4),
                "description": f"{ticker} {exp_date} {K}C",
                "symbol": f"{ticker.ljust(6)}{(today + timedelta(days=dte)).strftime('%y%m%d')}C{int(K*1000):08d}",
            }

            calls_map[exp_date][str(K)] = {**base_contract, "bid": c_bid, "ask": c_ask}
            puts_map[exp_date][str(K)]  = {
                **base_contract,
                "bid": p_bid, "ask": p_ask,
                "delta": round(bs_c.delta("put"), 4),
                "theta": round(bs_c.theta("put"), 4),
                "rho":   round(bs_c.rho("put"), 4),
                "theo":  p_theo,
                "last":  round((p_bid + p_ask) / 2, 4),
                "mark":  round((p_bid + p_ask) / 2, 4),
                "itm":   K > S,
                "symbol": f"{ticker.ljust(6)}{(today + timedelta(days=dte)).strftime('%y%m%d')}P{int(K*1000):08d}",
                "description": f"{ticker} {exp_date} {K}P",
            }

    chain = {
        "ticker":      ticker,
        "ts":          time.time(),
        "underlying":  {
            "price":        S,
            "bid":          round(S - 0.02, 4),
            "ask":          round(S + 0.02, 4),
            "mark":         S,
            "volatility":   65.0,
            "iv_percentile": "HIGH",
        },
        "calls":       calls_map,
        "puts":        puts_map,
        "expirations": exp_dates,
        "status":      "SUCCESS",
    }

    logger.info(f"[SMOKE] Cadena generada: {ticker} @ ${S} | "
                f"{len(exp_dates)} vencimientos | {len(calls_map[exp_dates[0]])} strikes ATM")
    return chain


def make_quote(ticker: str, S: float) -> dict:
    return {
        "symbol": ticker, "last": S, "mark": S,
        "bid": round(S - 0.02, 4), "ask": round(S + 0.02, 4),
        "open": round(S - 0.5, 4), "high": round(S + 0.8, 4),
        "low":  round(S - 0.6, 4), "volume": 3_500_000,
    }


# ══════════════════════════════════════════════════════════════
#  2. TEST MÓDULOS
# ══════════════════════════════════════════════════════════════

def test_buffer_market_data(candle_buffer: list):
    print("\n" + "="*60)
    print("  [1/6] BufferMarketData")
    print("="*60)
    from buffer_market_data import BufferMarketData
    buffers = {TICKER: candle_buffer}
    md = BufferMarketData(buffers)
    df = md.get_price_history(TICKER, candles=50)
    assert df is not None and not df.empty, "DataFrame vacío"
    assert list(df.columns) == ["datetime", "open", "high", "low", "close", "volume"]
    print(f"  ✅ DataFrame OK | shape={df.shape} | última vela close={df['close'].iloc[-1]:.4f}")
    print(f"     Columnas: {list(df.columns)}")
    print(f"     Primeras filas:\n{df.tail(3).to_string(index=False)}")
    return md


def test_v1_signals(candle_buffer: list):
    print("\n" + "="*60)
    print("  [2/6] Señales Eolo v1 (13 estrategias)")
    print("="*60)
    from buffer_market_data import BufferMarketData
    buffers = {TICKER: candle_buffer}
    md = BufferMarketData(buffers)

    strategies_ok  = []
    strategies_err = []

    strat_modules = []
    try:
        import bot_vwap_rsi_strategy  as s1; strat_modules.append(("VWAP_RSI",   s1))
        import bot_bollinger_strategy as s2; strat_modules.append(("BOLLINGER",  s2))
        import bot_supertrend_strategy as s3; strat_modules.append(("SUPERTREND", s3))
        import bot_ha_cloud_strategy  as s4; strat_modules.append(("HA_CLOUD",   s4))
        import bot_squeeze_strategy   as s5; strat_modules.append(("SQUEEZE",    s5))
        import bot_macd_bb_strategy   as s6; strat_modules.append(("MACD_BB",    s6))
        import bot_ema_tsi_strategy   as s7; strat_modules.append(("EMA_TSI",    s7))
        import bot_vela_pivot_strategy as s8; strat_modules.append(("VELA_PIVOT", s8))
        import bot_strategy           as s9; strat_modules.append(("EMA",        s9))
        import bot_hh_ll_strategy     as s10; strat_modules.append(("HH_LL",     s10))
        import bot_gap_strategy       as s11; strat_modules.append(("GAP",       s11))
        import bot_rsi_sma200_strategy as s12; strat_modules.append(("RSI_SMA200", s12))
        import bot_orb_strategy       as s13; strat_modules.append(("ORB",       s13))
    except ImportError as e:
        print(f"  ⚠️  No se pudieron importar estrategias v1: {e}")
        return {}

    signals = {}
    for name, mod in strat_modules:
        try:
            if name == "EMA":
                result = mod.analyze(md, TICKER, use_sma200_filter=False)
            elif name == "ORB":
                result = mod.analyze(md, TICKER, None)
            else:
                result = mod.analyze(md, TICKER)

            sig = result.get("signal", "ERROR")
            if sig != "ERROR":
                signals[name] = {"signal": sig, "price": result.get("price")}
                icon = "🟢" if sig == "BUY" else "🔴" if sig == "SELL" else "⚪"
                print(f"  {icon} {name:12} → {sig}")
                strategies_ok.append(name)
            else:
                print(f"  ❌ {name:12} → ERROR")
                strategies_err.append(name)
        except Exception as e:
            print(f"  ❌ {name:12} → excepción: {e}")
            strategies_err.append(name)

    buy_count  = sum(1 for s in signals.values() if s["signal"] == "BUY")
    sell_count = sum(1 for s in signals.values() if s["signal"] == "SELL")
    hold_count = len(signals) - buy_count - sell_count
    print(f"\n  Resumen: ✅ {len(strategies_ok)} OK | ❌ {len(strategies_err)} errores")
    print(f"  Señales: BUY={buy_count} | SELL={sell_count} | HOLD={hold_count}")
    return signals


def test_iv_surface(chain: dict):
    print("\n" + "="*60)
    print("  [3/6] IV Surface")
    print("="*60)
    from analysis.iv_surface import IVSurface
    surface = IVSurface.from_chain(chain)
    print(f"  Puntos calculados: {len(surface.points)}")
    if surface.atm_iv:
        print(f"  ATM IV          : {surface.atm_iv*100:.1f}%")
    if surface.skew_index is not None:
        desc = "put premium (bajista)" if surface.skew_index > 0 else "call skew (alcista)"
        print(f"  Skew index      : {surface.skew_index*100:.1f} pts → {desc}")
    if surface.term_slope is not None:
        desc = "backwardation" if surface.term_slope < 0 else "contango"
        print(f"  Term slope      : {surface.term_slope:.4f} ({desc})")
    ts = surface.get_term_structure()
    print(f"  Term structure:")
    for row in ts:
        print(f"    {row['expiration']} ({row['dte']} DTE) → IV={row['atm_iv']:.1f}%")
    assert surface.atm_iv is not None, "ATM IV no calculada"
    print("  ✅ IVSurface OK")
    return surface


def test_mispricing(chain: dict):
    print("\n" + "="*60)
    print("  [4/6] Mispricing Scanner")
    print("="*60)
    from analysis.mispricing import MispricingScanner
    scanner = MispricingScanner()
    alerts  = scanner.scan(chain)
    if alerts:
        print(f"  {len(alerts)} alertas detectadas:")
        for a in alerts[:5]:
            sev = a['severity']
            icon = "🔴" if sev == "HIGH" else "🟡" if sev == "MEDIUM" else "🔵"
            print(f"  {icon} [{sev}] {a['type']} | K={a.get('strike','')} "
                  f"exp={a.get('expiration','')} edge=${a.get('edge',0):.3f} → {a['action']}")
            print(f"       {a['description']}")
    else:
        print("  ✅ Sin anomalías detectadas (cadena sintética bien formada)")
    return alerts


def test_options_brain_prompt(chain: dict, surface, alerts: list, signals: dict, quote: dict):
    print("\n" + "="*60)
    print("  [5/6] OptionsBrain — prompt que vería Claude")
    print("="*60)
    from claude.options_brain import OptionsBrain

    # Instanciar sin API key real para capturar el prompt
    brain = OptionsBrain.__new__(OptionsBrain)
    brain._call_count = 0

    prompt = brain._build_prompt(
        ticker            = TICKER,
        quote             = quote,
        chain             = chain,
        surface           = surface,
        mispricing_alerts = alerts,
        open_positions    = [],
    )

    print(f"  Largo del prompt: {len(prompt)} caracteres")
    print("\n  ── Primeras 60 líneas ──")
    for line in prompt.split("\n")[:60]:
        print(f"  {line}")
    print("  ...")

    if CALL_CLAUDE:
        print("\n  ── Llamando Claude API ──")
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print("  ⚠️  ANTHROPIC_API_KEY no encontrada en entorno")
            return None
        import anthropic
        client   = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model      = "claude-sonnet-4-6",
            max_tokens = 1024,
            temperature= 0.1,
            messages   = [{"role": "user", "content": prompt}],
        )
        raw      = response.content[0].text
        decision = brain._parse_response(raw, TICKER)
        print(f"\n  ✅ Respuesta de Claude:")
        print(f"  {json.dumps(decision, indent=4, ensure_ascii=False)}")
        return decision
    else:
        print("\n  ℹ️  Usá --call-claude para llamar Claude real")
        print("  ✅ Prompt construido correctamente")
        return None


def test_occ_symbol():
    print("\n" + "="*60)
    print("  [6/6] OptionsTrader — símbolo OCC")
    print("="*60)
    from execution.options_trader import OptionsTrader
    trader = OptionsTrader.__new__(OptionsTrader)
    trader._account_id = None

    cases = [
        ("SOXL", "2025-05-16", "call", 45.0,   "SOXL  250516C00045000"),
        ("SPY",  "2025-06-20", "put",  520.0,   "SPY   250620P00520000"),
        ("QQQ",  "2025-05-30", "call", 450.5,   "QQQ   250530C00450500"),
        ("TSLL", "2025-04-25", "put",  3.5,     "TSLL  250425P00003500"),
    ]

    all_ok = True
    for ticker, exp, otype, strike, expected in cases:
        result = trader.build_occ_symbol(ticker, exp, otype, strike)
        ok = result == expected
        icon = "✅" if ok else "❌"
        print(f"  {icon} {ticker} {exp} {otype} K={strike} → {result}")
        if not ok:
            print(f"     esperado: {expected}")
            all_ok = False

    if all_ok:
        print("\n  ✅ Todos los símbolos OCC correctos")


# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════

def main():
    print("\n" + "█"*60)
    print("  EOLO v2 — SMOKE TEST END-TO-END")
    print(f"  Ticker: {TICKER} @ ${S} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if CALL_CLAUDE:
        print("  Modo: CALL CLAUDE REAL")
    else:
        print("  Modo: MOCK (sin llamadas externas)")
    print("█"*60)

    random.seed(42)   # reproducible

    # ── Generar datos sintéticos ───────────────────────────
    candle_buffer = make_candle_buffer(TICKER, n=100, base_price=S - 2.0)
    quote         = make_quote(TICKER, S)
    chain         = make_option_chain(TICKER, S)

    # ── Correr módulos en secuencia ────────────────────────
    errors = []

    try:
        test_buffer_market_data(candle_buffer)
    except Exception as e:
        errors.append(f"BufferMarketData: {e}")
        print(f"  ❌ FALLO: {e}")

    try:
        signals = test_v1_signals(candle_buffer)
    except Exception as e:
        errors.append(f"V1 Signals: {e}")
        signals = {}
        print(f"  ❌ FALLO: {e}")

    try:
        surface = test_iv_surface(chain)
    except Exception as e:
        errors.append(f"IVSurface: {e}")
        surface = None
        print(f"  ❌ FALLO: {e}")

    try:
        alerts = test_mispricing(chain)
    except Exception as e:
        errors.append(f"Mispricing: {e}")
        alerts = []
        print(f"  ❌ FALLO: {e}")

    try:
        test_options_brain_prompt(chain, surface, alerts, signals, quote)
    except Exception as e:
        errors.append(f"OptionsBrain: {e}")
        print(f"  ❌ FALLO: {e}")

    try:
        test_occ_symbol()
    except Exception as e:
        errors.append(f"OptionsTrader: {e}")
        print(f"  ❌ FALLO: {e}")

    # ── Resumen final ──────────────────────────────────────
    print("\n" + "█"*60)
    if not errors:
        print("  ✅ SMOKE TEST PASADO — todos los módulos OK")
    else:
        print(f"  ❌ SMOKE TEST FALLÓ — {len(errors)} errores:")
        for e in errors:
            print(f"     • {e}")
    print("█"*60 + "\n")
    return len(errors) == 0


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
