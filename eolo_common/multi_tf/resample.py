# ============================================================
#  resample.py — convierte velas 1-min a TFs superiores
#
#  Usa pandas.resample con closed="right" y label="right" para
#  que la vela resampleada represente el BLOQUE que acaba de
#  cerrar (consistente con cómo Schwab/Binance marcan sus candles).
#
#  TFs soportados en minutos:
#    1  — passthrough
#    5, 15, 30
#    60    (1h)
#    240   (4h)
#    1440  (1d)   — el caller decide si resamplea o usa REST histórico
# ============================================================
from typing import Optional
import pandas as pd
from loguru import logger


DEFAULT_TIMEFRAMES: list[int] = [1, 5, 15, 30, 60, 240]
SUPPORTED_TIMEFRAMES: set[int] = {1, 5, 15, 30, 60, 240, 1440}


def resample_to_tf(df_1min: pd.DataFrame, tf: int) -> Optional[pd.DataFrame]:
    """
    Convierte un DataFrame de velas 1-min a un TF superior.

    df_1min: DataFrame con columnas [datetime, open, high, low, close, volume].
             El datetime debe ser tz-aware (UTC recomendado).
    tf:      entero en minutos. 1 retorna el DF tal cual.

    Retorna None si df_1min es None/empty o tf no soportado.
    """
    if df_1min is None or df_1min.empty:
        return None

    tf = int(tf)
    if tf not in SUPPORTED_TIMEFRAMES:
        logger.warning(f"[RESAMPLE] TF {tf} no soportado — return None")
        return None

    if tf == 1:
        # Passthrough (copy para que el caller pueda mutar sin afectar al buffer)
        return df_1min.copy()

    # Construir regla pandas
    if tf == 1440:
        rule = "1D"
    elif tf == 240:
        rule = "4h"
    elif tf == 60:
        rule = "1h"
    else:
        rule = f"{tf}min"

    cols_needed = {"datetime", "open", "high", "low", "close"}
    missing = cols_needed - set(df_1min.columns)
    if missing:
        logger.warning(f"[RESAMPLE] faltan columnas {missing} — return None")
        return None

    agg: dict = {
        "open":   "first",
        "high":   "max",
        "low":    "min",
        "close":  "last",
    }
    if "volume" in df_1min.columns:
        agg["volume"] = "sum"

    try:
        out = (df_1min.set_index("datetime")
                       .resample(rule, label="right", closed="right")
                       .agg(agg)
                       .dropna(subset=["close"])
                       .reset_index())
    except Exception as e:
        logger.error(f"[RESAMPLE] error TF={tf}: {e}")
        return None

    return out if not out.empty else None


def resample_many(df_1min: pd.DataFrame, tfs: list[int]) -> dict[int, pd.DataFrame]:
    """
    Conveniencia: resamplea a varios TFs a la vez.
    Retorna dict {tf: df}. Los TFs que retornan None se omiten.
    """
    out: dict[int, pd.DataFrame] = {}
    for tf in tfs:
        df_tf = resample_to_tf(df_1min, tf)
        if df_tf is not None and not df_tf.empty:
            out[int(tf)] = df_tf
    return out
