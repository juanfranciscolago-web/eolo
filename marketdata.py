import os
import pandas
import requests
from loguru import logger

from helpers import retrieve_firestore_value

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
        # Initialize access token, base_url, and headers
        # during class instantiation
        # Instance variable
        self.access_token = None
        # Method
        self.refresh_access_token()
        self.base_url = "https://api.schwabapi.com/marketdata/v1"
        self.headers = {"Authorization": f"Bearer {self.access_token}"}

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
                          frequency: int = 1):
        """
        Fetches intraday candles for a symbol via Schwab Market Data API.
        Returns a DataFrame with columns: datetime, open, high, low, close, volume
        Returns None on error.
        Auto-retries once if token is expired (401).

        frequency: tamaño de vela en minutos (1 o 5). Default: 1.
        days     : días de historia (1-10).
                   Con frequency=1: 1 día ≈ 390 velas, suficiente para SMA200.
                   Con frequency=5: 1 día ≈  78 velas.
        candles  : cuántas velas finales devolver (tail). 0 = todas.
        """
        days      = max(1, min(10, days))
        frequency = frequency if frequency in (1, 5, 10, 15, 30) else 1

        url = self.base_url + "/pricehistory"
        params = {
            "symbol":                symbol,
            "periodType":            "day",
            "period":                days,
            "frequencyType":         "minute",
            "frequency":             frequency,
            "needExtendedHoursData": False,
        }

        for attempt in range(2):   # try up to 2 times (once fresh, once after token refresh)
            self.refresh_access_token()
            self.headers = {"Authorization": f"Bearer {self.access_token}"}
            response = requests.get(url, headers=self.headers, params=params)

            if response.status_code == 200:
                data = response.json()
                candles_data = data.get("candles", [])
                if not candles_data:
                    logger.error(f"No candles returned for {symbol}")
                    return None
                df = pandas.DataFrame(candles_data)
                df["datetime"] = pandas.to_datetime(df["datetime"], unit="ms")
                df = df[["datetime", "open", "high", "low", "close", "volume"]]
                if candles > 0:
                    df = df.tail(candles).reset_index(drop=True)
                logger.debug(f"Fetched {len(df)} 5-min candles for {symbol} ({days}d)")
                return df

            elif response.status_code == 401:
                if attempt == 0:
                    logger.warning(f"401 for {symbol} — token may be expired. "
                                   f"Retrying after re-fetch from Firestore...")
                else:
                    logger.error(
                        f"401 persists for {symbol}. Your Schwab access token is expired.\n"
                        f"  ➡ Run your token refresh Cloud Function to get a new one:\n"
                        f"     gcloud functions call refresh_tokens --region=us-east1"
                    )
                    return None
            else:
                logger.error(f"Error fetching price history for {symbol}: "
                             f"{response.status_code} {response.text}")
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