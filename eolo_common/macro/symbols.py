"""Macro symbols y metadata."""
# Símbolos Schwab para índices macro.
#
# Verificado 2026-04-20 con test_macro_quotes.py contra la cuenta bot:
#   - $VIX    CBOE Volatility Index        → 200 OK
#   - $VIX9D  9-day variant                → 200 OK
#   - $VIX3M  3-month variant              → 200 OK
#   - $TICK   NYSE TICK breadth            → 200 OK
#   - $TRIN   NYSE Arms index              → 200 OK
#
# ⚠️  Los sufijos `.X` (ej. $VIX.X) devolvían EMPTY aunque la spec
# vieja los mencionaba. La cuenta bot actual responde SIEMPRE a la
# forma corta sin `.X`. Mantenemos `.X` en aliases por si el comportamiento
# cambia en el futuro.

MACRO_SYMBOLS = {
    "VIX":    {"schwab": "$VIX",   "aliases": ["VIX",   "$VIX.X",   "VIX.X"]},
    "VIX9D":  {"schwab": "$VIX9D", "aliases": ["VIX9D", "$VIX9D.X", "VIX9D.X"]},
    "VIX3M":  {"schwab": "$VIX3M", "aliases": ["VIX3M", "$VIX3M.X", "VIX3M.X"]},
    "TICK":   {"schwab": "$TICK",  "aliases": ["TICK",  "$TICK.US"]},
    "TRIN":   {"schwab": "$TRIN",  "aliases": ["TRIN",  "$TRIN.US"]},
}


def resolve_schwab(name: str) -> str:
    entry = MACRO_SYMBOLS.get(name.upper())
    if not entry:
        return name
    return entry["schwab"]
