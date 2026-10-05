import httpx
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from app.config.settings import settings
from app.models.market_data import MarketBar, MarketQuote
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

class AlphaVantageClient:
    BASE_URL = "https://www.alphavantage.co/query"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.ALPHA_VANTAGE_API_KEY

    def _get(self, params: Dict[str, Any]) -> Dict[str, Any]:
        params["apikey"] = self.api_key
        try:
            with httpx.Client(timeout=15.0) as client:
                response = client.get(self.BASE_URL, params=params)
                response.raise_for_status()
                data = response.json()
                if "Error Message" in data:
                    logger.error(f"Alpha Vantage Error: {data['Error Message']}")
                    return {}
                if "Note" in data:
                    logger.warning(f"Alpha Vantage API Notice/Limit: {data['Note']}")
                return data
        except Exception as e:
            logger.error(f"Failed to query Alpha Vantage: {e}")
            return {}

    def fetch_daily_bars(self, symbol: str, outputsize: str = "compact") -> List[Dict[str, Any]]:
        """Fetch daily OHLCV bars for a given stock or ETF symbol."""
        params = {
            "function": "TIME_SERIES_DAILY",
            "symbol": symbol,
            "outputsize": outputsize
        }
        data = self._get(params)
        time_series = data.get("Time Series (Daily)", {})
        
        bars = []
        for date_str, values in time_series.items():
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
                bars.append({
                    "symbol": symbol.upper(),
                    "timeframe": "1d",
                    "timestamp": dt,
                    "open": float(values["1. open"]),
                    "high": float(values["2. high"]),
                    "low": float(values["3. low"]),
                    "close": float(values["4. close"]),
                    "volume": float(values.get("5. volume", 0))
                })
            except Exception as e:
                logger.warning(f"Error parsing bar for {symbol} on {date_str}: {e}")

        # Sort ascending by timestamp
        bars.sort(key=lambda b: b["timestamp"])
        return bars

    def fetch_forex_daily_bars(self, from_symbol: str, to_symbol: str = "USD") -> List[Dict[str, Any]]:
        """Fetch daily OHLCV bars for Forex (e.g. XAU/USD)."""
        params = {
            "function": "FX_DAILY",
            "from_symbol": from_symbol,
            "to_symbol": to_symbol,
            "outputsize": "compact"
        }
        data = self._get(params)
        time_series = data.get("Time Series FX (Daily)", {})
        
        bars = []
        symbol = f"{from_symbol}{to_symbol}"
        for date_str, values in time_series.items():
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
                bars.append({
                    "symbol": symbol,
                    "timeframe": "1d",
                    "timestamp": dt,
                    "open": float(values["1. open"]),
                    "high": float(values["2. high"]),
                    "low": float(values["3. low"]),
                    "close": float(values["4. close"]),
                    "volume": 0.0  # FX daily doesn't always provide reliable volume
                })
            except Exception as e:
                logger.warning(f"Error parsing forex bar for {symbol} on {date_str}: {e}")

        # Sort ascending by timestamp
        bars.sort(key=lambda b: b["timestamp"])
        return bars

    def fetch_intraday_bars(self, symbol: str, interval: str = "5min") -> List[Dict[str, Any]]:
        """Fetch intraday bars (1min, 5min, 15min, 30min, 60min)."""
        params = {
            "function": "TIME_SERIES_INTRADAY",
            "symbol": symbol,
            "interval": interval,
            "outputsize": "compact"
        }
        data = self._get(params)
        key_name = f"Time Series ({interval})"
        time_series = data.get(key_name, {})

        bars = []
        for time_str, values in time_series.items():
            try:
                dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
                bars.append({
                    "symbol": symbol.upper(),
                    "timeframe": interval,
                    "timestamp": dt,
                    "open": float(values["1. open"]),
                    "high": float(values["2. high"]),
                    "low": float(values["3. low"]),
                    "close": float(values["4. close"]),
                    "volume": float(values.get("5. volume", 0))
                })
            except Exception as e:
                logger.warning(f"Error parsing intraday bar for {symbol}: {e}")

        bars.sort(key=lambda b: b["timestamp"])
        return bars

    def fetch_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch real-time global quote for a symbol."""
        params = {
            "function": "GLOBAL_QUOTE",
            "symbol": symbol
        }
        data = self._get(params)
        raw_quote = data.get("Global Quote", {})
        if not raw_quote:
            return None

        try:
            return {
                "symbol": symbol.upper(),
                "price": float(raw_quote.get("05. price", 0.0)),
                "change": float(raw_quote.get("09. change", 0.0)),
                "change_pct": float(raw_quote.get("10. change percent", "0%").replace("%", "")),
                "volume": float(raw_quote.get("06. volume", 0.0)),
                "latest_trading_day": raw_quote.get("07. latest trading day")
            }
        except Exception as e:
            logger.error(f"Error parsing quote for {symbol}: {e}")
            return None

    def fetch_news_sentiment(self, tickers: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Fetch financial news and sentiment analysis scores from Alpha Vantage."""
        params = {
            "function": "NEWS_SENTIMENT",
            "tickers": tickers,
            "limit": limit
        }
        data = self._get(params)
        return data.get("feed", [])

    def save_bars_to_db(self, bars: List[Dict[str, Any]]) -> int:
        """Upsert market bars into local database."""
        if not bars:
            return 0

        db = SessionLocal()
        saved_count = 0
        try:
            for bar_data in bars:
                existing = db.query(MarketBar).filter_by(
                    symbol=bar_data["symbol"],
                    timeframe=bar_data["timeframe"],
                    timestamp=bar_data["timestamp"]
                ).first()

                if not existing:
                    bar = MarketBar(**bar_data)
                    db.add(bar)
                    saved_count += 1
            db.commit()
            logger.info(f"Saved {saved_count} new bars to database.")
            return saved_count
        except Exception as e:
            db.rollback()
            logger.error(f"Error saving bars to db: {e}")
            return 0
        finally:
            db.close()
