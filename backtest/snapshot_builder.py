"""Transform cached QD raw responses into MarketSnapshot fields for backtest."""
import json
from pathlib import Path
from typing import Optional


def build_snapshot_dict_from_cache(cache_path: Path) -> Optional[dict]:
    """Parse cached JSON con _qd_* responses → snapshot dict con QD fields."""
    if not cache_path.exists():
        return None
    data = json.loads(cache_path.read_text())
    snap = {
        "ticker": data["ticker"],
        "timestamp": data["timestamp"],
    }
    raw = data.get("_raw", {})

    # max-pain — real QD shape: maxPainStrikePrice + stockPrice at top level,
    # data is per-strike call/put intrinsic values (no nesting by ticker).
    mp = raw.get("_qd_max-pain", {})
    if isinstance(mp, dict):
        if mp.get("maxPainStrikePrice") is not None:
            snap["max_pain_strike"] = mp["maxPainStrikePrice"]
            stock_px = mp.get("stockPrice")
            if stock_px and mp["maxPainStrikePrice"]:
                snap["max_pain_distance_pct"] = round(
                    (stock_px - mp["maxPainStrikePrice"]) / mp["maxPainStrikePrice"] * 100, 3
                )

    # iv-rank — real shape: data → DATE → contractTypeToIVData → CALL/PUT →
    # {lastIv, windowMaxIv, windowMinIv}. Tomar la última fecha (más reciente).
    iv = raw.get("_qd_iv-rank", {})
    if isinstance(iv, dict) and isinstance(iv.get("data"), dict) and iv["data"]:
        try:
            sorted_dates = sorted(iv["data"].keys())
            last_entry = iv["data"][sorted_dates[-1]]
            if isinstance(last_entry, dict):
                contract_iv = last_entry.get("contractTypeToIVData") or {}
                call_data = contract_iv.get("CALL") or {}
                put_data = contract_iv.get("PUT") or {}
                snap["iv_rank_call"] = _iv_rank_percentile(call_data)
                snap["iv_rank_put"] = _iv_rank_percentile(put_data)
        except Exception:
            pass

    # net-drift (extract latest net premiums)
    nd = raw.get("_qd_net-drift", {})
    if isinstance(nd, dict) and nd.get("data"):
        try:
            latest_ts = max(nd["data"].keys())
            entry = nd["data"][latest_ts]
            snap["net_call_premium_drift"] = entry.get("netCallPremium")
            snap["net_put_premium_drift"] = entry.get("netPutPremium")
        except Exception:
            pass

    # exposure-by-strike — real live shape (per external_data_quantdata.get_gex_regime):
    # data → TICKER → exposureMap → expiry → strike_str → {callExposure, putExposure}
    # Total gamma + max call/put strikes + regime classification (thresholds
    # heredados de external_data_quantdata, calibración pendiente sec 6.1).
    gex = raw.get("_qd_exposure-by-strike", {})
    if isinstance(gex, dict) and isinstance(gex.get("data"), dict):
        try:
            ticker_data = next(iter(gex["data"].values()), {})
            exposure_map = ticker_data.get("exposureMap", {}) if isinstance(ticker_data, dict) else {}
            total_call = 0.0
            total_put = 0.0
            max_call_strike = None
            max_call_value = 0.0
            max_put_strike = None
            max_put_value = 0.0
            for _expiry, strikes in exposure_map.items():
                if not isinstance(strikes, dict):
                    continue
                for strike_str, exposures in strikes.items():
                    try:
                        strike = float(strike_str)
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(exposures, dict):
                        continue
                    call_exp = float(exposures.get("callExposure") or 0)
                    put_exp = float(exposures.get("putExposure") or 0)
                    total_call += call_exp
                    total_put += put_exp
                    if call_exp > max_call_value:
                        max_call_value = call_exp
                        max_call_strike = strike
                    if abs(put_exp) > abs(max_put_value):
                        max_put_value = put_exp
                        max_put_strike = strike
            total_gex = total_call + total_put
            snap["gex_total"] = total_gex
            snap["gex_max_call_strike"] = max_call_strike
            snap["gex_max_put_strike"] = max_put_strike
            if max_call_strike and max_put_strike:
                snap["gamma_zero_strike"] = (max_call_strike + max_put_strike) / 2.0
            # Same thresholds as external_data_quantdata.get_gex_regime (placeholder).
            if total_gex > 5e6:
                snap["gex_regime"] = "positive_high"
            elif total_gex > 1e6:
                snap["gex_regime"] = "positive_low"
            elif total_gex > -1e6:
                snap["gex_regime"] = "flip_zone"
            else:
                snap["gex_regime"] = "negative"
        except Exception:
            pass

    # volatility-drift — real shape: data keyed by unix-ms ts, each entry has
    # {arv, iv, stockPrice}. VRP = iv - arv at latest ts. vrpPercentile252d
    # NOT returned by API in this query mode → vrp_score unavailable here
    # (deferred: requires lookback computation T7).
    vd = raw.get("_qd_volatility-drift", {})
    if isinstance(vd, dict) and isinstance(vd.get("data"), dict) and vd["data"]:
        try:
            latest_ts = max(vd["data"].keys(), key=lambda k: int(k))
            entry = vd["data"][latest_ts]
            iv = entry.get("iv")
            arv = entry.get("arv")
            if iv is not None:
                snap["vrp_iv_30d"] = iv
            if arv is not None:
                snap["vrp_arv_20d"] = arv
            if iv is not None and arv is not None:
                snap["vrp_value"] = iv - arv
        except Exception:
            pass

    # open-interest-by-strike — real shape: data keyed by strike (string), each
    # entry has {callOpenInterest, putOpenInterest}. Find strike with max OI.
    oi = raw.get("_qd_open-interest-by-strike", {})
    if isinstance(oi, dict) and isinstance(oi.get("data"), dict):
        try:
            max_call_oi = -1
            max_put_oi = -1
            max_call_strike = None
            max_put_strike = None
            for strike_str, vals in oi["data"].items():
                try:
                    strike = float(strike_str)
                except (TypeError, ValueError):
                    continue
                if not isinstance(vals, dict):
                    continue
                call_oi = vals.get("callOpenInterest") or 0
                put_oi = vals.get("putOpenInterest") or 0
                if call_oi > max_call_oi:
                    max_call_oi = call_oi
                    max_call_strike = strike
                if put_oi > max_put_oi:
                    max_put_oi = put_oi
                    max_put_strike = strike
            if max_call_strike is not None:
                snap["oi_max_call_strike"] = max_call_strike
            if max_put_strike is not None:
                snap["oi_max_put_strike"] = max_put_strike
        except Exception:
            pass

    return snap


def _iv_rank_percentile(iv_data: dict) -> Optional[float]:
    """Compute IV rank percentile from CALL/PUT data."""
    if not isinstance(iv_data, dict):
        return None
    last = iv_data.get("lastIv")
    high = iv_data.get("windowMaxIv")
    low = iv_data.get("windowMinIv")
    if None in (last, high, low) or high == low:
        return None
    return (last - low) / (high - low) * 100.0
