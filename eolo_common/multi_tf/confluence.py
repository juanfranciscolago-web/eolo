# ============================================================
#  ConfluenceFilter — reducer multi-TF → señal única
#
#  Problema: con multi-TF una misma estrategia puede dar
#  BUY en 5m, HOLD en 15m, SELL en 60m. Si ejecutamos cada
#  señal individualmente, ruido total.
#
#  Solución: el orquestador corre la estrategia en cada TF del
#  array active_timeframes, registra cada señal acá, y al final
#  del pase consulta evaluate() para obtener la señal consolidada.
#
#  Reglas configurables:
#    - confluence_mode=False       → passthrough (cada TF ejecuta su señal)
#    - confluence_mode=True,
#      confluence_min_agree=K      → sólo ejecuta si ≥K TFs coinciden
#                                     en la misma dirección (BUY o SELL).
#                                     Los HOLD no cuentan como voto.
#
#  Uso típico (en el orquestador):
#    cf = ConfluenceFilter(mode=True, min_agree=2)
#    for tf in [1, 5, 15, 30]:
#        md = BufferMarketData(buffer, frequency=tf)
#        for ticker in tickers:
#            result = strategy.analyze(md, ticker)
#            cf.register(ticker, strategy_name, tf, result["signal"])
#    for (ticker, strategy), sig in cf.consolidate().items():
#        ...  # sig es "BUY" | "SELL" | "HOLD"
# ============================================================
from typing import Optional


class ConfluenceFilter:
    """
    Reducer de señales multi-TF.

    mode=False → passthrough: consolidate() retorna todas las señales
                 tal cual (BUY y SELL de cada TF se ejecutan por separado).
    mode=True  → agregación: si ≥min_agree TFs coinciden en BUY, BUY; idem SELL;
                 sino HOLD. Evita ejecutar cuando los TFs no están alineados.
    """

    def __init__(self, mode: bool = False, min_agree: int = 2):
        self.mode      = bool(mode)
        self.min_agree = max(1, int(min_agree))
        # Registros: (ticker, strategy) → list[(tf, signal)]
        self._signals: dict[tuple[str, str], list[tuple[int, str]]] = {}

    # ── Ingesta ─────────────────────────────────────────

    def register(self, ticker: str, strategy: str, tf: int,
                 signal: Optional[str]) -> None:
        """Registra una señal. signal ∈ {BUY, SELL, HOLD, None}."""
        if not signal:
            return
        sig = signal.upper()
        if sig not in ("BUY", "SELL", "HOLD"):
            return
        key = (ticker.upper(), strategy)
        self._signals.setdefault(key, []).append((int(tf), sig))

    def reset(self) -> None:
        self._signals.clear()

    # ── Consolidación ───────────────────────────────────

    def consolidate(self) -> dict[tuple[str, str], str]:
        """
        Devuelve {(ticker, strategy): final_signal}.
        final_signal es "BUY" | "SELL" | "HOLD".
        """
        out: dict[tuple[str, str], str] = {}

        for key, sigs in self._signals.items():
            if not sigs:
                continue

            if not self.mode:
                # Passthrough: si CUALQUIER TF pide BUY/SELL → lo pasamos.
                # Precedencia: SELL > BUY > HOLD (cerrar es prioridad).
                actions = {s for _, s in sigs}
                if "SELL" in actions:
                    out[key] = "SELL"
                elif "BUY" in actions:
                    out[key] = "BUY"
                else:
                    out[key] = "HOLD"
                continue

            # mode=True: contar votos por dirección
            buys  = sum(1 for _, s in sigs if s == "BUY")
            sells = sum(1 for _, s in sigs if s == "SELL")

            # SELL tiene prioridad (cerrar siempre que haya señal de salida)
            if sells >= self.min_agree:
                out[key] = "SELL"
            elif buys >= self.min_agree:
                out[key] = "BUY"
            else:
                out[key] = "HOLD"

        return out

    # ── Debug / telemetry ──────────────────────────────

    def snapshot(self) -> dict[str, dict]:
        """
        Dump de estado para el state.json del bot.
        Formato:
          {
            "mode": True,
            "min_agree": 2,
            "signals": {"TICKER|strategy": [[1,"BUY"],[5,"BUY"],[15,"HOLD"]]}
          }
        """
        return {
            "mode":      self.mode,
            "min_agree": self.min_agree,
            "signals": {
                f"{t}|{s}": [[tf, sig] for tf, sig in sigs]
                for (t, s), sigs in self._signals.items()
            },
        }
