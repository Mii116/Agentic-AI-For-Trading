import os
import json
import logging
import urllib.request
from typing import Dict, Any, Tuple, Optional
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

MACRO_CACHE_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "macro_state.json"))

class AlphaVantageMacroWorker:
    """
    Exogenous Macro Filter Worker:
    - Rate-limit safe polling (once every 30 minutes)
    - Queries US 10-Year Treasury Yield and Dollar trend via Alpha Vantage
    - Gating Rule: When the Swing Agent (Magic: 1001) evaluates a BUY setup,
      vetoes the trade if 10Y yields show aggressive upward acceleration.
    - Scalper Agent (Magic: 2002) completely bypasses this check.
    """

    API_KEY = "93ZPI584XNK8Z9ET"
    CACHE_EXPIRATION_MINUTES = 30

    def __init__(self, cache_file: str = MACRO_CACHE_PATH):
        self.cache_file = cache_file
        os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)

    def fetch_treasury_yield_10y(self) -> Optional[Dict[str, Any]]:
        """Queries 10-Year Treasury Constant Maturity Rate."""
        url = f"https://www.alphavantage.co/query?function=TREASURY_YIELD&interval=daily&maturity=10year&apikey={self.API_KEY}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data
        except Exception as e:
            logger.warning(f"Error fetching Treasury Yield: {e}")
            return None

    def fetch_dollar_trend(self) -> Optional[str]:
        """Queries EUR/USD to determine USD Strength (Inverse correlation to DXY)."""
        url = f"https://www.alphavantage.co/query?function=FX_DAILY&from_symbol=EUR&to_symbol=USD&apikey={self.API_KEY}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                ts = data.get("Time Series FX (Daily)")
                if ts and len(ts) >= 2:
                    dates = sorted(ts.keys(), reverse=True)
                    latest_close = float(ts[dates[0]]["4. close"])
                    prev_close = float(ts[dates[1]]["4. close"])
                    # If EUR/USD is falling, USD is strengthening (Bullish DXY)
                    if latest_close < prev_close:
                        return "BULLISH_USD"
                    elif latest_close > prev_close:
                        return "BEARISH_USD"
                    return "NEUTRAL_USD"
        except Exception as e:
            logger.warning(f"Error fetching USD trend: {e}")
        return "NEUTRAL_USD"

    def refresh_cache(self) -> Dict[str, Any]:
        """Performs live rate-safe query and persists macro state to cache."""
        now = datetime.now(timezone.utc)
        ty_data = self.fetch_treasury_yield_10y()
        dxy_trend = self.fetch_dollar_trend() or "NEUTRAL_USD"

        yield_values = []
        if ty_data and "data" in ty_data:
            for item in ty_data["data"][:5]:
                val = item.get("value")
                if val and val != ".":
                    try:
                        yield_values.append(float(val))
                    except ValueError:
                        pass

        current_yield = yield_values[0] if yield_values else 5.25
        prev_yield = yield_values[1] if len(yield_values) > 1 else current_yield

        # Calculate rate of change in 10Y Yield
        yield_delta = current_yield - prev_yield
        yield_accelerating_up = yield_delta >= 0.05  # 5+ basis points surge

        allow_swing_buy = not (yield_accelerating_up and dxy_trend == "BULLISH_USD")

        macro_state = {
            "timestamp": now.isoformat(),
            "us_10y_yield": current_yield,
            "yield_delta": round(yield_delta, 3),
            "yield_accelerating_up": yield_accelerating_up,
            "dxy_trend": dxy_trend,
            "allow_gold_swing_buy": allow_swing_buy,
            "macro_summary": (
                f"10Y Yield: {current_yield}% (delta: {yield_delta:+.2f}%) | "
                f"DXY: {dxy_trend} | Swing Gold Buy: {'APPROVED' if allow_swing_buy else 'VETOED (Yield Spike)'}"
            )
        }

        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(macro_state, f, indent=2)
            logger.info(f"[AlphaVantageMacroWorker] Refreshed macro cache: {macro_state['macro_summary']}")
        except Exception as e:
            logger.error(f"Error writing macro cache: {e}")

        return macro_state

    def update_macro_state(self) -> Dict[str, Any]:
        """Alias for refresh_cache()."""
        return self.refresh_cache()

    def get_macro_state(self) -> Dict[str, Any]:
        """Returns the current macro state, refreshing if expired (> 30 mins) or missing."""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                ts_str = data.get("timestamp")
                if ts_str:
                    cache_time = datetime.fromisoformat(ts_str)
                    if (datetime.now(timezone.utc) - cache_time) < timedelta(minutes=self.CACHE_EXPIRATION_MINUTES):
                        return data
            except Exception:
                pass

        return self.refresh_cache()

    def check_swing_buy_permission(self) -> Tuple[bool, str]:
        """
        Swing Gating Rule:
        When Swing Trader (Magic: 1001) evaluates a BUY setup, returns:
        (allow_buy: bool, reason: str)
        """
        state = self.get_macro_state()
        if not state.get("allow_gold_swing_buy", True):
            reason = (
                f"Alpha Vantage Macro Veto: US 10Y Yields surging ({state.get('us_10y_yield')}%, "
                f"delta {state.get('yield_delta'):+.2f}%) with {state.get('dxy_trend')}. "
                f"Vetoing Swing BUY on Gold to avoid institutional real-rate headwinds."
            )
            logger.warning(reason)
            return False, reason
        return True, "Macro Yield & DXY conditions favorable for Swing BUY."
