#!/usr/bin/env python3
# ============================================================
#  test_macro_quotes.py
#
#  Verifica que la cuenta Schwab del bot puede cotizar los 5
#  símbolos macro necesarios para las estrategias Nivel 2:
#
#    $VIX.X   (CBOE Volatility Index)
#    $VIX9D.X (9-day VIX)
#    $VIX3M.X (3-month VIX)
#    $TICK    (NYSE TICK breadth)
#    $TRIN    (Arms index)
#
#  Requisitos:
#    - Variables/creds para Schwab (token en Firestore, mismo
#      setup que usa marketdata.MarketData)
#    - Correr desde el root del repo `eolo/` para que funcione
#      el import de marketdata:
#         cd eolo/ && python tools/test_macro_quotes.py
#
#  Output:
#    - Tabla por símbolo: [OK price] / [EMPTY] / [HTTP xxx]
#    - Lista de aliases con los que hay que probar si alguno
#      no responde con la forma canónica.
# ============================================================
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import requests
from loguru import logger

from marketdata import MarketData  # noqa: E402


# Lista de (nombre_lógico, [variantes a probar]).
# Schwab a veces tolera variantes con/sin punto o sin sufijo.
CANDIDATES = [
    ("VIX",   ["$VIX.X",   "$VIX",   "VIX",   "VIX.X"]),
    ("VIX9D", ["$VIX9D.X", "$VIX9D", "VIX9D"]),
    ("VIX3M", ["$VIX3M.X", "$VIX3M", "VIX3M"]),
    ("TICK",  ["$TICK",    "$TICK.US", "TICK"]),
    ("TRIN",  ["$TRIN",    "$TRIN.US", "TRIN"]),
]


def _fetch_one(md: MarketData, symbol: str):
    """
    Devuelve (status, payload). status ∈ {"OK", "EMPTY", f"HTTP {code}", "EXC"}.
    """
    md.refresh_access_token()
    md.headers = {"Authorization": f"Bearer {md.access_token}"}
    url    = f"{md.base_url}/quotes"
    params = {"symbols": symbol, "fields": "quote", "indicative": False}
    try:
        resp = requests.get(url, headers=md.headers, params=params, timeout=6)
    except Exception as e:
        return "EXC", str(e)
    if resp.status_code != 200:
        return f"HTTP {resp.status_code}", resp.text[:200]
    data = resp.json() or {}
    if not data:
        return "EMPTY", "respuesta vacía"
    # Schwab devuelve { "$VIX.X": {...} }
    for key, entry in data.items():
        quote = (entry or {}).get("quote") or {}
        price = (
            quote.get("lastPrice")
            or quote.get("mark")
            or quote.get("closePrice")
        )
        if price is not None:
            return "OK", {
                "returned_key": key,
                "price": float(price),
                "bid":   quote.get("bidPrice"),
                "ask":   quote.get("askPrice"),
                "close": quote.get("closePrice"),
            }
    return "EMPTY", data


def main():
    logger.info("=== test_macro_quotes.py ===")
    logger.info("Verificando cotizaciones Schwab para símbolos macro...")

    md = MarketData()
    logger.info(f"MarketData instanciado. base_url={md.base_url}")

    summary = []
    for name, variants in CANDIDATES:
        found_variant = None
        last_detail = None
        for v in variants:
            status, detail = _fetch_one(md, v)
            last_detail = (v, status, detail)
            if status == "OK":
                found_variant = (v, detail)
                break
        if found_variant:
            v, detail = found_variant
            summary.append((name, "OK", v, detail))
            logger.info(
                f"[{name}] ✅ {v} → price={detail['price']} "
                f"(bid={detail.get('bid')} ask={detail.get('ask')})"
            )
        else:
            v, status, detail = last_detail
            summary.append((name, status, v, detail))
            logger.warning(f"[{name}] ❌ intentos fallidos. último={v} status={status} detail={detail}")

    # ── Reporte final ────────────────────────────────────
    print("\n" + "=" * 64)
    print(f"{'macro':<8} {'estado':<8} {'símbolo usado':<14} {'precio':>10}")
    print("-" * 64)
    for name, status, sym, detail in summary:
        if status == "OK":
            print(f"{name:<8} {'OK':<8} {sym:<14} {detail['price']:>10.2f}")
        else:
            print(f"{name:<8} {status:<8} {sym:<14} {'-':>10}")
    print("=" * 64)

    ok_count = sum(1 for _, s, _, _ in summary if s == "OK")
    print(f"\n{ok_count}/5 símbolos OK.")

    # Recomendación
    print("\nRecomendación para la config de MacroFeeds:")
    print("  (edita eolo_common/macro/symbols.py si algún alias reemplaza al default)\n")
    for name, status, sym, detail in summary:
        if status == "OK":
            print(f"  MACRO_SYMBOLS['{name}']['schwab'] = \"{sym}\"")
        else:
            print(f"  # {name}: NO DISPONIBLE — las estrategias Nivel 2 que lo usen quedan en HOLD")


if __name__ == "__main__":
    main()
