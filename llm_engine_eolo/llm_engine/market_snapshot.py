"""
Market Snapshot - Formato estandar de datos del mercado.

Este es el "input" que recibe el LLM. Equivalente a las capturas
de chart que Juan le pasa manualmente durante el design del KB.

Eolo Crop debe llenar este snapshot con datos de Schwab API + calculos.
"""
from pydantic import BaseModel, Field
from typing import Literal, Optional
from datetime import datetime


class MarketSnapshot(BaseModel):
    """Snapshot completo del mercado en un momento dado."""

    # Identificacion
    timestamp: str = Field(..., description="ISO timestamp ET")
    ticker: str = Field(default="SPY")
    session_phase: str = Field(default="regular", description="premarket/open/regular/power_hour/close")

    # Precio actual
    price: float
    open_price: float
    high: float
    low: float
    prev_close: float

    # VIX (CRITICO para sistema de Juan)
    vix_level: float
    vix_velocity_30m_pct: float = Field(default=0.0, description="VIX % change last 30min")
    vix_velocity_1d_pct: float = Field(default=0.0, description="VIX % change last 1 day")
    vix_vs_prev_close_pct: float = Field(default=0.0)

    # Niveles dia anterior
    pdh: float = Field(..., description="Previous day high")
    pdl: float = Field(..., description="Previous day low")
    pdc: float = Field(..., description="Previous day close")

    # Fibonacci levels (calculados sobre open del dia)
    fib_r1: float = 0.0
    fib_r2: float = 0.0
    fib_r3: float = 0.0
    fib_s1: float = 0.0
    fib_s2: float = 0.0
    fib_s3: float = 0.0

    # VWAP + bandas (2m timeframe)
    vwap: float = 0.0
    vwap_upper_1sigma: float = 0.0
    vwap_upper_2sigma: float = 0.0
    vwap_lower_1sigma: float = 0.0
    vwap_lower_2sigma: float = 0.0

    # Momentum - multi timeframe
    rsi_2m: float
    rsi_15m: float
    rsi_daily: float

    # ATR - multi timeframe
    atr_2m: float
    atr_15m: float
    atr_daily: float
    adr_daily: float = Field(default=0.0, description="Average Daily Range")

    # EMAs (2m)
    ema_9_2m: float = 0.0
    ema_21_2m: float = 0.0
    ema_200_2m: float = 0.0

    # EMAs (15m)
    ema_9_15m: float = 0.0
    ema_21_15m: float = 0.0

    # EMAs (Daily)
    ema_9_daily: float = 0.0
    ema_21_daily: float = 0.0
    ema_50_daily: float = 0.0
    ema_200_daily: float = 0.0

    # MACD (15m)
    macd_histogram_15m: float = 0.0
    macd_signal_15m: float = 0.0
    macd_line_15m: float = 0.0

    # Volume pressure (2m)
    bvp_pct: float = Field(default=50.0, description="Buy Volume Pressure %")
    svp_pct: float = Field(default=50.0, description="Sell Volume Pressure %")
    volume_current_bar: float = 0.0
    volume_avg_20bar: float = 0.0

    # IV / Options context
    iv_rank_spy: Optional[float] = None
    iv_30d: Optional[float] = None

    # === QUANT DATA — Hotfix #95 (2026-06-01): close wire boundary ===
    # Bot CROP (snapshot.py:355-406) writes these from get_max_pain /
    # get_iv_rank / get_gex_regime / get_net_premium_drift; without these
    # declarations they were silently dropped at Pydantic boundary
    # (extra="ignore" default).
    max_pain_strike: Optional[float] = None
    max_pain_distance_pct: Optional[float] = None
    max_pain_expiry: Optional[str] = None
    iv_rank_call: Optional[float] = None
    iv_rank_put: Optional[float] = None
    gex_regime: Optional[str] = None
    gex_total: Optional[float] = None
    gex_max_call_strike: Optional[float] = None
    gex_max_put_strike: Optional[float] = None
    net_call_premium_drift: Optional[float] = None
    net_put_premium_drift: Optional[float] = None

    # === Sprint T1.A (2026-06-02): Tier S endpoints expansion ===
    # Volatility Drift (sec 5 Tier S #2, sec 6.2 VRP)
    vrp_value: Optional[float] = None
    vrp_iv_30d: Optional[float] = None
    vrp_arv_20d: Optional[float] = None
    vrp_percentile_252d: Optional[float] = None
    vrp_score: Optional[Literal["rich", "fair", "cheap"]] = None  # computed

    # Volatility Skew (sec 5 Tier S #4)
    put_skew_25d: Optional[float] = None
    call_skew_25d: Optional[float] = None
    atm_iv: Optional[float] = None

    # Term Structure (sec 5 Tier S #5)
    ts_iv_7d: Optional[float] = None
    ts_iv_30d: Optional[float] = None
    ts_iv_60d: Optional[float] = None
    term_slope_60d_7d: Optional[float] = None

    # Open Interest (sec 5 Tier S #6)
    oi_max_call_strike: Optional[float] = None
    oi_max_put_strike: Optional[float] = None

    # Max Pain trend (sec 5 Tier S #7 extension)
    max_pain_trend_7d: Optional[float] = None

    # Compute layer outputs (sec 6.1 - 6.2)
    gamma_regime_v2: Optional[Literal["long", "negative", "transition"]] = None
    gamma_zero_strike: Optional[float] = None

    # Macro context
    days_to_next_fomc: Optional[int] = None
    days_to_next_cpi: Optional[int] = None
    days_to_next_nfp: Optional[int] = None
    session_news: Optional[str] = None

    # Open positions (para que LLM decida CLOSE_POSITIONS)
    has_open_positions: bool = False
    open_positions_summary: Optional[str] = None

    def to_llm_format(self) -> str:
        """Formatea el snapshot para el LLM."""
        return f"""MARKET SNAPSHOT — {self.ticker} @ {self.timestamp}
Session phase: {self.session_phase}
═══════════════════════════════════════════════════════

PRICE ACTION
- Current: ${self.price:.2f}
- Open: ${self.open_price:.2f} | High: ${self.high:.2f} | Low: ${self.low:.2f}
- Prev close: ${self.prev_close:.2f}
- Day change: {((self.price - self.prev_close) / self.prev_close * 100):+.2f}%

VIX REGIME (CRITICAL)
- Level: {self.vix_level:.2f}
- Velocity 30m: {self.vix_velocity_30m_pct:+.2f}%
- Velocity 1d: {self.vix_velocity_1d_pct:+.2f}%
- vs Previous close: {self.vix_vs_prev_close_pct:+.2f}%
- Regime: {self._classify_vix_regime()}

PREVIOUS DAY LEVELS
- PDH: ${self.pdh:.2f} | PDC: ${self.pdc:.2f} | PDL: ${self.pdl:.2f}
- Distance to PDH: {((self.pdh - self.price) / self.price * 100):+.2f}%
- Distance to PDL: {((self.price - self.pdl) / self.price * 100):+.2f}%

FIBONACCI LEVELS (calculated on open ${self.open_price:.2f})
- R3: ${self.fib_r3:.2f} | R2: ${self.fib_r2:.2f} | R1: ${self.fib_r1:.2f}
- S1: ${self.fib_s1:.2f} | S2: ${self.fib_s2:.2f} | S3: ${self.fib_s3:.2f}

VWAP STRUCTURE (2m)
- VWAP: ${self.vwap:.2f}
- +2σ: ${self.vwap_upper_2sigma:.2f} | -2σ: ${self.vwap_lower_2sigma:.2f}
- +1σ: ${self.vwap_upper_1sigma:.2f} | -1σ: ${self.vwap_lower_1sigma:.2f}

MOMENTUM
- RSI 2m: {self.rsi_2m:.1f} | RSI 15m: {self.rsi_15m:.1f} | RSI Daily: {self.rsi_daily:.1f}
- ATR 2m: {self.atr_2m:.3f} | ATR 15m: {self.atr_15m:.3f} | ATR Daily: {self.atr_daily:.3f}
- ADR Daily: {self.adr_daily:.2f}%

EMAs (2m): 9=${self.ema_9_2m:.2f} | 21=${self.ema_21_2m:.2f}
EMAs (15m): 9=${self.ema_9_15m:.2f} | 21=${self.ema_21_15m:.2f}
EMAs (Daily): 9=${self.ema_9_daily:.2f} | 21=${self.ema_21_daily:.2f} | 200=${self.ema_200_daily:.2f}

MACD (15m): hist={self.macd_histogram_15m:+.4f} | line={self.macd_line_15m:+.4f} | signal={self.macd_signal_15m:+.4f}

VOLUME PRESSURE (2m)
- BVP: {self.bvp_pct:.1f}% | SVP: {self.svp_pct:.1f}%
- Current bar volume: {self.volume_current_bar:,.0f}
- 20-bar avg: {self.volume_avg_20bar:,.0f}

OPTIONS CONTEXT
- IV Rank SPY: {self.iv_rank_spy if self.iv_rank_spy is not None else 'N/A'}
- IV 30d: {self.iv_30d if self.iv_30d is not None else 'N/A'}

OPTIONS POSITIONING (Quant Data)
- Max Pain Strike: {self.max_pain_strike if self.max_pain_strike is not None else 'N/A'}
- Max Pain Distance: {self.max_pain_distance_pct if self.max_pain_distance_pct is not None else 'N/A'}%
- Max Pain Expiry: {self.max_pain_expiry if self.max_pain_expiry else 'N/A'}
- IV Rank Call: {self.iv_rank_call if self.iv_rank_call is not None else 'N/A'}
- IV Rank Put: {self.iv_rank_put if self.iv_rank_put is not None else 'N/A'}
- GEX Regime: {self.gex_regime if self.gex_regime else 'N/A'}
- GEX Total: {self.gex_total if self.gex_total is not None else 'N/A'}
- GEX Max Call Strike: {self.gex_max_call_strike if self.gex_max_call_strike is not None else 'N/A'}
- GEX Max Put Strike: {self.gex_max_put_strike if self.gex_max_put_strike is not None else 'N/A'}
- Net Premium Drift Call: {self.net_call_premium_drift if self.net_call_premium_drift is not None else 'N/A'}
- Net Premium Drift Put: {self.net_put_premium_drift if self.net_put_premium_drift is not None else 'N/A'}

OPTIONS POSITIONING ADVANCED (Quant Data Tier S)
- Gamma Regime: {self.gamma_regime_v2 if self.gamma_regime_v2 else 'N/A'} (gamma_zero strike: {self.gamma_zero_strike if self.gamma_zero_strike is not None else 'N/A'})
- VRP: {self.vrp_score if self.vrp_score else 'N/A'} (value: {self.vrp_value if self.vrp_value is not None else 'N/A'}, percentile 252d: {self.vrp_percentile_252d if self.vrp_percentile_252d is not None else 'N/A'})
- Put Skew 25Δ: {self.put_skew_25d if self.put_skew_25d is not None else 'N/A'}
- Call Skew 25Δ: {self.call_skew_25d if self.call_skew_25d is not None else 'N/A'}
- ATM IV: {self.atm_iv if self.atm_iv is not None else 'N/A'}
- Term Structure: 7d={self.ts_iv_7d if self.ts_iv_7d is not None else 'N/A'}, 30d={self.ts_iv_30d if self.ts_iv_30d is not None else 'N/A'}, 60d={self.ts_iv_60d if self.ts_iv_60d is not None else 'N/A'} (slope 60-7={self.term_slope_60d_7d if self.term_slope_60d_7d is not None else 'N/A'})
- OI Max Call Strike: {self.oi_max_call_strike if self.oi_max_call_strike is not None else 'N/A'}
- OI Max Put Strike: {self.oi_max_put_strike if self.oi_max_put_strike is not None else 'N/A'}
- Max Pain trend 7d: {self.max_pain_trend_7d if self.max_pain_trend_7d is not None else 'N/A'}

MACRO CONTEXT
- Days to FOMC: {self.days_to_next_fomc or 'N/A'}
- Days to CPI: {self.days_to_next_cpi or 'N/A'}
- Days to NFP: {self.days_to_next_nfp or 'N/A'}
- News: {self.session_news or 'None'}

OPEN POSITIONS
- Has positions: {self.has_open_positions}
- Summary: {self.open_positions_summary or 'None'}
"""

    def _classify_vix_regime(self) -> str:
        """Clasifica regimen VIX para el LLM."""
        v = self.vix_level
        vel = self.vix_velocity_30m_pct

        if v < 15:
            base = "VERY_LOW"
        elif v < 18:
            base = "LOW"
        elif v < 22:
            base = "MEDIUM"
        elif v < 30:
            base = "HIGH"
        else:
            base = "EXTREME"

        if vel > 5:
            return f"{base}_SPIKING_UP"
        if vel < -5:
            return f"{base}_SPIKING_DOWN"
        if abs(vel) > 2:
            return f"{base}_VOLATILE"
        return f"{base}_STABLE"

    def get_setup_keywords(self) -> list:
        """Genera keywords para buscar casos similares en KB."""
        keywords = []

        # VIX regime
        keywords.append(self._classify_vix_regime().lower())

        # RSI zones
        if self.rsi_2m > 70:
            keywords.append("rsi_overbought")
        elif self.rsi_2m < 30:
            keywords.append("rsi_oversold")

        # Trend
        if self.ema_9_2m > self.ema_21_2m:
            keywords.append("bullish_short_term")
        else:
            keywords.append("bearish_short_term")

        # VWAP position
        if self.price > self.vwap_upper_2sigma:
            keywords.append("above_vwap_2sigma")
        elif self.price < self.vwap_lower_2sigma:
            keywords.append("below_vwap_2sigma")

        # Distance to PDH/PDL
        dist_pdh = abs((self.pdh - self.price) / self.price * 100)
        dist_pdl = abs((self.price - self.pdl) / self.price * 100)
        if dist_pdh < 0.2:
            keywords.append("near_pdh")
        if dist_pdl < 0.2:
            keywords.append("near_pdl")

        return keywords
