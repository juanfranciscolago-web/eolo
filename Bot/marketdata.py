import os
import pandas
import requests
from loguru import logger

from helpers import retrieve_firestore_value, _invalidate_firestore_cache

from secret_stuff import (
    project_id,
    gcloud_project,
    collection_id,
    document_id,
    key,
)

project_id = project_id
os.environ["GCLOUD_PROJECT"] = gcloud_project


class MarketData:
    def __init__(self):
        self.access_token = None
        self.refresh_access_token()
        self.base_url  = "https://api.schwabapi.com/marketdata/v1"
        self.headers   = {"Authorization": f"Bearer {self.access_token}"}
        # Timeframe activo — se actualiza desde bot_main según Firestore
        # Valores válidos: 1, 5, 15, 30, 60, 240, 1440 (minutos)
        self.frequency = 1

    def refresh_access_token(self):
        self.access_token = retrieve_firestore_value(
            collection_id=collection_id,
            document_id=document_id,
            key=key,
        )

    def get_movers(self, exchange):
        """Available values : $DJI, $COMPX, $SPX.X, NYSE, NASDAQ,
        OTCBB, INDEX_ALL, EQUITY_ALL, OPTION_ALL, OPTION_PUT, OPTION_CALL"""
        response = requests.get(
            self.base_url + f"/movers/{exchange}", headers=self.headers
        )
        if response.status_code == 200:
            response_data = response.json()
            response_frame = pandas.DataFrame(response_data)
            logger.debug(response_frame)
            screeners_frame = pandas.json_normalize(
                response_frame["screeners"]
            )
            logger.debug(screeners_frame)
            return screeners_frame
        elif response.status_code == 201:
            logger.info(response.text)
            logger.info("New resource created.")
        else:
            logger.error(f"Error in response with message: {response.text}")
            logger.error(f"Error code: {response.status_code}")
            return None

    def get_price_history(self, symbol: str, candles: int = 50, days: int = 1,
                          frequency: int = None):
        """
        Fetches candles for a symbol via Schwab Market Data API.
        Returns a DataFrame with columns: datetime, open, high, low, close, volume
        Returns None on error. Auto-retries once si el token expira (401).

        frequency: minutos por vela. None = usa self.frequency (set desde Firestore).
                   Valores soportados: 1, 5, 15, 30 → Schwab nativo
                                       60, 240      → Schwab 30min + pandas resample
                                       1440         → Schwab daily
        days     : días de historia.
        candles  : cuántas velas finales devolver (tail). 0 = todas.
        """
        if frequency is None:
            frequency = self.frequency

        # ── Auto-scale días según el TF ──────────────────────
        # Antes de este fix, la mayoría de las estrategias llamaban con
        # days=1, y en freq=15m/30m eso da ~26/13 velas — insuficiente
        # para SMA200, EMA50, ATR(14) + contextos largos → HOLD silencioso.
        # Con este ajuste, cualquier caller que pase days=1 en 15m recibe
        # ≥3 días de historia, y en 30m ≥5 días (≈60–78 velas utilizables).
        if frequency == 15:
            days = max(days, 3)
        elif frequency == 30:
            days = max(days, 5)
        elif frequency == 5:
            days = max(days, 2)

        # ── Daily timeframe (1440 min = 1 día) ───────────────
        if frequency == 1440:
            return self._get_daily_history(symbol, candles=candles, days=max(days, 30))

        # ── 1h / 4h — fetch 30min + resample ─────────────────
        if frequency in (60, 240):
            raw = self._fetch_minute_candles(symbol, schwab_freq=30,
                                             days=max(days, 10), candles=0)
            if raw is None:
                return None
            rule = f"{frequency}min"
            df = (raw.set_index("datetime")
                     .resample(rule, label="right", closed="right")
                     .agg({"open": "first", "high": "max",
                           "low": "min", "close": "last", "volume": "sum"})
                     .dropna(subset=["close"])
                     .reset_index())
            if candles > 0:
                df = df.tail(candles).reset_index(drop=True)
            logger.debug(f"Fetched {len(df)} {frequency}min candles for {symbol} (resampled)")
            return df

        # ── Minute timeframes (1, 5, 15, 30) ─────────────────
        schwab_freq = frequency if frequency in (1, 5, 10, 15, 30) else 1
        return self._fetch_minute_candles(symbol, schwab_freq=schwab_freq,
                                          days=days, candles=candles)

    def _fetch_minute_candles(self, symbol: str, schwab_freq: int = 1,
                               days: int = 1, candles: int = 0):
        """Fetches minute candles directly from Schwab API."""
        days = max(1, min(10, days))
        url  = self.base_url + "/pricehistory"
        params = {
            "symbol":                symbol,
            "periodType":            "day",
            "period":                days,
            "frequencyType":         "minute",
            "frequency":             schwab_freq,
            "needExtendedHoursData": False,
        }
        for attempt in range(2):
            self.refresh_access_token()
            self.headers = {"Authorization": f"Bearer {self.access_token}"}
            response = requests.get(url, headers=self.headers, params=params)

            if response.status_code == 200:
                data         = response.json()
                candles_data = data.get("candles", [])
                if not candles_data:
                    logger.error(f"No candles returned for {symbol}")
                    return None
                df = pandas.DataFrame(candles_data)
                df["datetime"] = pandas.to_datetime(df["datetime"], unit="ms")
                df = df[["datetime", "open", "high", "low", "close", "volume"]]
                if candles > 0:
                    df = df.tail(candles).reset_index(drop=True)
                logger.debug(f"Fetched {len(df)} {schwab_freq}min candles for {symbol} ({days}d)")
                return df

            elif response.status_code == 401:
                if attempt == 0:
                    logger.warning(f"401 for {symbol} — token expirado, invalidando cache y reintentando...")
                    _invalidate_firestore_cache(collection_id, document_id, key)
                else:
                    logger.error(f"401 persiste para {symbol}. Token Schwab expirado.\n"
                                 f"  ➡ gcloud functions call refresh_tokens --region=us-east1")
                    return None
            else:
                logger.error(f"Error fetching {symbol}: {response.status_code} {response.text}")
                return None

    def _get_daily_history(self, symbol: str, candles: int = 50, days: int = 30):
        """Fetches daily (1d) candles from Schwab API."""
        url    = self.base_url + "/pricehistory"
        period = max(1, min(20, days // 30 + 1))  # Schwab period en meses
        params = {
            "symbol":                symbol,
            "periodType":            "month",
            "period":                period,
            "frequencyType":         "daily",
            "frequency":             1,
            "needExtendedHoursData": False,
        }
        for attempt in range(2):
            self.refresh_access_token()
            self.headers = {"Authorization": f"Bearer {self.access_token}"}
            response = requests.get(url, headers=self.headers, params=params)
            if response.status_code == 200:
                data         = response.json()
                candles_data = data.get("candles", [])
                if not candles_data:
                    return None
                df = pandas.DataFrame(candles_data)
                df["datetime"] = pandas.to_datetime(df["datetime"], unit="ms")
                df = df[["datetime", "open", "high", "low", "close", "volume"]]
                if candles > 0:
                    df = df.tail(candles).reset_index(drop=True)
                logger.debug(f"Fetched {len(df)} daily candles for {symbol}")
                return df
            elif response.status_code == 401:
                if attempt == 0:
                    _invalidate_firestore_cache(collection_id, document_id, key)
                    self.refresh_access_token()
                else:
                    return None
            else:
                return None

    def get_candles(self, symbol: str, period_type: str = "day", period: int = 1,
                    frequency: int = 5) -> pandas.DataFrame:
        """Alias para compatibilidad con close_all_open_positions en bot_main."""
        return self.get_price_history(symbol, candles=0, days=period)

    def get_quote(self, symbol: str) -> float:
        """
        Obtiene el precio en tiempo real via /quotes endpoint.
        Usa lastPrice (último trade), mark (mid bid/ask) o closePrice como fallback.
        Retorna None si falla.
        """
        self.refresh_access_token()
        self.headers = {"Authorization": f"Bearer {self.access_token}"}
        url    = f"{self.base_url}/quotes"
        params = {"symbols": symbol, "fields": "quote", "indicative": False}

        try:
            resp = requests.get(url, headers=self.headers, params=params, timeout=5)
            if resp.status_code == 200:
                data  = resp.json()
                entry = data.get(symbol, {})
                quote = entry.get("quote", {})
                price = (quote.get("lastPrice") or
                         quote.get("mark")      or
                         quote.get("closePrice"))
                if price:
                    logger.debug(f"Quote {symbol}: ${price}")
                    return round(float(price), 4)
            logger.warning(f"Quote fallback for {symbol}: status={resp.status_code}")
        except Exception as e:
            logger.warning(f"Quote error for {symbol}: {e}")
        return None

    def get_quotes(self, symbols: list) -> dict:
        """
        Obtiene precios en tiempo real para múltiples tickers en una sola llamada.
        Retorna dict {ticker: price}.
        """
        self.refresh_access_token()
        self.headers = {"Authorization": f"Bearer {self.access_token}"}
        url    = f"{self.base_url}/quotes"
        params = {"symbols": ",".join(symbols), "fields": "quote", "indicative": False}

        try:
            resp = requests.get(url, headers=self.headers, params=params, timeout=8)
            if resp.status_code == 200:
                data   = resp.json()
                result = {}
                for sym in symbols:
                    entry = data.get(sym, {})
                    quote = entry.get("quote", {})
                    price = (quote.get("lastPrice") or
                             quote.get("mark")      or
                             quote.get("closePrice"))
                    if price:
                        result[sym] = round(float(price), 4)
                logger.debug(f"Quotes fetched: {result}")
                return result
        except Exception as e:
            logger.warning(f"Bulk quotes error: {e}")
        return {}