# ============================================================
#  EOLO — Estrategia: Bollinger/Keltner Squeeze Release
#
#  Basado en Robot_Squeeze_UpSTUDY.ts y Robot_Squeeze_DNSTUDY.ts
#
#  Lógica:
#    El "Squeeze" ocurre cuando las Bollinger Bands (BB)
#    están DENTRO de los Keltner Channels (KC).
#    Cuando las BB salen del KC (release), el precio suele
#    moverse fuerte en la dirección de la ruptura.
#
#    BUY : BB sale del KC hacia arriba (expansión alcista)
#          Y close > KC_upper[1]  (precio confirma dirección)
#    SELL: close cae por debajo de KC_lower  (stop dinámico)
#          O BB se re-comprime dentro de KC (squeeze vuelve)
#
#  Indicadores:
#    BB: SMA(20) ± 2 × std(20)
#    KC: SMA(20) ± 1.5 × ATR(20)
#
#  Tickers recomendados: SOXL, TSLL, NVDL, TQQQ
#  Señales esperadas   : 1-3 por día (muy selectiva)
# ============================================================
import pandas as pd
from loguru import logger

STRATEGY_NAME = "SQUEEZE"
BB_PERIOD     = 20
BB_MULT       = 2.0
KC_PERIOD     = 20
KC_MULT       = 1.5
MIN_BARS      = 30


# ── Indicadores ───────────────────────────────────────────

def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # Bollinger Bands
    sma            = df["close"].rolling(BB_PERIOD).mean()
    std            = df["close"].rolling(BB_PERIOD).std()
    df["bb_upper"] = sma + BB_MULT * std
    df["bb_lower"] = sma - BB_MULT * std

    # ATR para Keltner
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(KC_PERIOD).mean()

    df["kc_upper"] = sma + KC_MULT * atr
    df["kc_lower"] = sma - KC_MULT * atr

    # Squeeze activo: BB dentro de KC
    df["squeeze_on"] = (df["bb_upper"] < df["kc_upper"]) & \
                       (df["bb_lower"] > df["kc_lower"])

    # Release alcista: BB sale del KC (ambas bandas)
    df["release_up"] = (df["bb_upper"] >= df["kc_upper"]) & \
                       (df["bb_lower"] <= df["kc_lower"])

    return df


# ── Señal ─────────────────────────────────────────────────

def detect_signal(df: pd.DataFrame, ticker: str) -> str:
    if len(df) < MIN_BARS:
        return "HOLD"

    curr = df.iloc[-1]
    prev = df.iloc[-2]

    close     = float(curr["close"])
    kc_upper  = float(curr["kc_upper"])
    kc_lower  = float(curr["kc_lower"])
    kc_prev_u = float(prev["kc_upper"])
    release   = bool(curr["release_up"])
    prev_rel  = bool(prev["release_up"])
    squeezed  = bool(prev["squeeze_on"])  # estaba en squeeze antes?

    if any(pd.isna(v) for v in [kc_upper, kc_lower, kc_prev_u]):
        return "HOLD"

    # ── SELL: precio cae bajo KC_lower (stop) ─────────────
    if close < kc_lower:
        logger.info(
            f"[SQUEEZE] {ticker} SELL ✅ — stop: close={close:.4f} < KC_lower={kc_lower:.4f}"
        )
        return "SELL"

    # ── BUY: squeeze se libera hacia arriba ───────────────
    # Condición: (esta barra O la anterior tuvieron release)
    # Y close > KC_upper[1] (precio confirma ruptura)
    if (release or prev_rel) and close > kc_prev_u:
        # Más señal si venía de squeeze
        if squeezed:
            logger.info(
                f"[SQUEEZE] {ticker} BUY ✅ — release desde squeeze | "
                f"close={close:.4f} > KC_upper_prev={kc_prev_u:.4f}"
            )
        else:
            logger.info(
                f"[SQUEEZE] {ticker} BUY ✅ — release alcista | "
                f"close={close:.4f} > KC_upper_prev={kc_prev_u:.4f}"
            )
        return "BUY"

    return "HOLD"


# ── Pipeline completo ─────────────────────────────────────

def analyze(market_data, ticker: str) -> dict:
    df = market_data.get_price_history(ticker, candles=0, days=1)

    if df is None or df.empty:
        logger.error(f"[SQUEEZE] Sin datos para {ticker}")
        return {"ticker": ticker, "signal": "ERROR", "strategy": STRATEGY_NAME,
                "price": None, "squeeze_on": None, "kc_upper": None}

    df     = calculate_indicators(df)
    signal = detect_signal(df, ticker)
    last   = df.iloc[-1]

    def safe(val):
        return round(float(val), 4) if not pd.isna(val) else None

    return {
        "ticker":      ticker,
        "signal":      signal,
        "strategy":    STRATEGY_NAME,
        "price":       safe(last["close"]),
        "bb_upper":    safe(last["bb_upper"]),
        "bb_lower":    safe(last["bb_lower"]),
        "kc_upper":    safe(last["kc_upper"]),
        "kc_lower":    safe(last["kc_lower"]),
        "squeeze_on":  bool(last["squeeze_on"]) if not pd.isna(last["squeeze_on"]) else None,
        "candle_time": str(last["datetime"]),
    }
