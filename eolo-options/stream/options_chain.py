# ============================================================
#  EOLO v2 — Options Chain Fetcher
#
#  Descarga la cadena completa de opciones cada 30 segundos
#  para los tickers configurados usando:
#    GET /marketdata/v1/chains?symbol=SOXL&...
#
#  Schwab Options Chain API params:
#    symbol         : ticker subyacente (ej. "SOXL")
#    contractType   : ALL | CALL | PUT
#    strikeCount    : cantidad de strikes arriba/abajo del ATM
#    includeUnderlyingQuote : true
#    strategy       : SINGLE (opciones simples)
#    range          : ALL | ITM | OTM | NTM
#    expMonth       : ALL o mes específico
#    optionType     : S (standard) | NS (non-standard) | ALL
#
#  Estructura de respuesta normalizada:
#    chain["calls"][expiration][strike] = option_contract
#    chain["puts"][expiration][strike]  = option_contract
#    chain["underlying"]                = price, bid, ask, mark, volatility
#
#  Uso:
#    fetcher = OptionChainFetcher(tickers=["SOXL","SPY","QQQ"])
#    await fetcher.start(on_chain=callback)
# ============================================================
import asyncio
import time
from datetime import datetime, timezone
from loguru import logger

try:
    import requests
except ImportError:
    requests = None

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from helpers import get_access_token

# ── Constantes ────────────────────────────────────────────
SCHWAB_CHAINS_URL = "https://api.schwabapi.com/marketdata/v1/chains"
REFRESH_INTERVAL  = 30          # segundos entre fetches
STRIKE_COUNT      = 20          # strikes arriba/abajo del ATM
CONTRACT_TYPE     = "ALL"       # CALL + PUT
STRATEGY          = "SINGLE"    # contratos individuales


class OptionChainFetcher:
    """
    Fetches the full options chain for each configured ticker
    every REFRESH_INTERVAL seconds. Calls registered handlers
    with the normalized chain dict on each refresh.
    """

    def __init__(self, tickers: list[str], interval: int = REFRESH_INTERVAL,
                 strike_count: int = STRIKE_COUNT):
        self.tickers       = [t.upper() for t in tickers]
        self.interval      = interval
        self.strike_count  = strike_count
        self._running      = False
        self._handlers     = []          # fn(ticker, chain_dict)
        self._chains       = {}          # último chain por ticker
        self._last_fetch   = {}          # timestamp último fetch exitoso

    # ── Handlers ──────────────────────────────────────────

    def add_handler(self, fn):
        """Registra un callback fn(ticker, chain) para recibir chains."""
        self._handlers.append(fn)

    async def _notify(self, ticker: str, chain: dict):
        for fn in self._handlers:
            try:
                if asyncio.iscoroutinefunction(fn):
                    await fn(ticker, chain)
                else:
                    fn(ticker, chain)
            except Exception as e:
                import traceback
                logger.error(
                    f"[CHAIN] Handler error para {ticker}: "
                    f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
                )

    # ── Fetch HTTP ─────────────────────────────────────────

    def _fetch_chain(self, ticker: str) -> dict | None:
        """
        Descarga la cadena de opciones via Schwab REST API.
        Retorna dict normalizado o None si hay error.
        """
        token = get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        params = {
            "symbol":                  ticker,
            "contractType":            CONTRACT_TYPE,
            "strikeCount":             self.strike_count,
            "includeUnderlyingQuote":  "true",
            "strategy":                STRATEGY,
            "range":                   "ALL",
            "optionType":              "ALL",
        }

        try:
            resp = requests.get(
                SCHWAB_CHAINS_URL,
                headers=headers,
                params=params,
                timeout=15,
            )
            resp.raise_for_status()
            raw = resp.json()
        except Exception as e:
            logger.warning(f"[CHAIN] Error fetching {ticker}: {e}")
            return None

        return self._normalize(ticker, raw)

    # ── Normalizador ───────────────────────────────────────

    def _normalize(self, ticker: str, raw: dict) -> dict:
        """
        Transforma la respuesta Schwab en una estructura limpia:
        {
          "ticker":     "SOXL",
          "ts":         1713200000.0,         # unix timestamp fetch
          "underlying": {price, bid, ask, mark, volatility, iv_percentile},
          "calls": {                           # dict[expDate_str][strike_str]
            "2025-05-16": {
              "45.0": { bid, ask, mark, iv, delta, gamma, theta, vega,
                        oi, volume, itm, theo }
            }
          },
          "puts": { ... same structure ... },
          "expirations": ["2025-05-16", ...]  # sorted
        }
        """
        ts = time.time()

        # ── Subyacente ────────────────────────────────────
        uq = raw.get("underlying", {})
        underlying = {
            "price":        uq.get("mark")    or uq.get("last")  or uq.get("close"),
            "bid":          uq.get("bid"),
            "ask":          uq.get("ask"),
            "mark":         uq.get("mark"),
            "volatility":   uq.get("volatility"),    # HV implícita del subyacente
            "iv_percentile": raw.get("volatilityType"),
        }

        # ── Opciones ──────────────────────────────────────
        calls = self._parse_option_map(raw.get("callExpDateMap", {}))
        puts  = self._parse_option_map(raw.get("putExpDateMap",  {}))

        expirations = sorted(set(list(calls.keys()) + list(puts.keys())))

        return {
            "ticker":      ticker,
            "ts":          ts,
            "underlying":  underlying,
            "calls":       calls,
            "puts":        puts,
            "expirations": expirations,
            "status":      raw.get("status", ""),
        }

    def _parse_option_map(self, exp_map: dict) -> dict:
        """
        Parsea callExpDateMap o putExpDateMap.

        Schwab format:
          "2025-05-16:29" → { "45.0": [{ ...contract... }] }

        Output:
          "2025-05-16" → { "45.0": { bid, ask, mark, iv, delta, ... } }
        """
        result = {}
        for exp_key, strikes in exp_map.items():
            # exp_key tiene formato "YYYY-MM-DD:DTE"
            exp_date = exp_key.split(":")[0]
            result[exp_date] = {}

            for strike_str, contracts in strikes.items():
                if not contracts:
                    continue
                c = contracts[0]  # siempre 1 contrato por strike/exp

                result[exp_date][strike_str] = {
                    # Precio
                    "bid":         c.get("bid"),
                    "ask":         c.get("ask"),
                    "mark":        c.get("mark"),
                    "last":        c.get("last"),
                    "theo":        c.get("theoreticalOptionValue"),
                    # Volatilidad
                    "iv":          c.get("volatility"),            # IV en %
                    "iv_percentile": c.get("percentChange"),       # aproximación
                    # Greeks
                    "delta":       c.get("delta"),
                    "gamma":       c.get("gamma"),
                    "theta":       c.get("theta"),
                    "vega":        c.get("vega"),
                    "rho":         c.get("rho"),
                    # Info de contrato
                    "oi":          c.get("openInterest"),
                    "volume":      c.get("totalVolume"),
                    "itm":         c.get("inTheMoney"),
                    "dte":         c.get("daysToExpiration"),
                    "expiration":  exp_date,
                    "strike":      float(strike_str),
                    "description": c.get("description", ""),
                    "symbol":      c.get("symbol", ""),
                    # Multiplicador siempre 100 para US equity options
                    "multiplier":  c.get("multiplier", 100),
                }
        return result

    # ── Loop principal ─────────────────────────────────────

    async def start(self):
        """
        Fetch infinito cada self.interval segundos.
        Llama a todos los handlers con el chain normalizado.
        Para con stop().
        """
        self._running = True
        logger.info(f"[CHAIN] Iniciando fetcher para: {self.tickers} cada {self.interval}s")

        while self._running:
            await self._fetch_all()
            # Espera interruptible
            for _ in range(self.interval * 10):
                if not self._running:
                    break
                await asyncio.sleep(0.1)

    async def _fetch_all(self):
        """Descarga chains de todos los tickers en paralelo."""
        tasks = [self._fetch_and_notify(ticker) for ticker in self.tickers]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _fetch_and_notify(self, ticker: str):
        loop = asyncio.get_event_loop()
        # El fetch HTTP es bloqueante → corre en threadpool
        chain = await loop.run_in_executor(None, self._fetch_chain, ticker)
        if chain:
            self._chains[ticker]     = chain
            self._last_fetch[ticker] = time.time()
            logger.info(
                f"[CHAIN] {ticker} — {len(chain.get('expirations', []))} exp, "
                f"underlying=${chain['underlying'].get('price', 0):.2f}"
            )
            await self._notify(ticker, chain)
        else:
            logger.warning(f"[CHAIN] {ticker} — sin datos esta vuelta")

    def stop(self):
        self._running = False
        logger.info("[CHAIN] Deteniendo fetcher...")

    # ── Helpers de consulta ────────────────────────────────

    def get_chain(self, ticker: str) -> dict | None:
        """Retorna el último chain descargado para un ticker."""
        return self._chains.get(ticker.upper())

    def get_all_chains(self) -> dict:
        """Retorna todos los chains descargados."""
        return dict(self._chains)

    def get_contracts(self, ticker: str, exp_date: str,
                      option_type: str = "calls") -> dict:
        """
        Retorna todos los contratos para un ticker/expiry/tipo.
        option_type: "calls" o "puts"
        """
        chain = self._chains.get(ticker.upper())
        if not chain:
            return {}
        return chain.get(option_type, {}).get(exp_date, {})

    def get_atm_strike(self, ticker: str) -> float | None:
        """
        Calcula el strike ATM (más cercano al precio del subyacente).
        """
        chain = self._chains.get(ticker.upper())
        if not chain:
            return None
        price = chain["underlying"].get("price")
        if not price:
            return None

        # Toma strikes de calls en la primera expiración
        if not chain["expirations"]:
            return None
        first_exp = chain["expirations"][0]
        strikes = [float(s) for s in chain["calls"].get(first_exp, {}).keys()]
        if not strikes:
            return None

        return min(strikes, key=lambda s: abs(s - price))

    def get_contract(self, ticker: str, exp_date: str,
                     strike: float, option_type: str = "calls") -> dict | None:
        """
        Retorna un contrato específico por ticker/exp/strike/tipo.
        """
        chain = self._chains.get(ticker.upper())
        if not chain:
            return None
        return (chain
                .get(option_type, {})
                .get(exp_date, {})
                .get(str(strike)))

    def get_nearest_expiration(self, ticker: str,
                                min_dte: int = 7,
                                max_dte: int = 45) -> str | None:
        """
        Retorna la expiración más cercana con DTE en [min_dte, max_dte].
        Útil para seleccionar vencimientos de 2-4 semanas (14-28 DTE).
        """
        chain = self._chains.get(ticker.upper())
        if not chain:
            return None

        today = datetime.now(timezone.utc).date()
        candidates = []
        for exp in chain["expirations"]:
            try:
                exp_date = datetime.strptime(exp, "%Y-%m-%d").date()
                dte = (exp_date - today).days
                if min_dte <= dte <= max_dte:
                    candidates.append((dte, exp))
            except ValueError:
                continue

        if not candidates:
            return None
        candidates.sort()
        return candidates[0][1]   # exp_date string más cercana
