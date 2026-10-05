import logging
import urllib.request
import json
from typing import Tuple, Optional, List, Dict, Any
from datetime import datetime, timezone, timedelta
import MetaTrader5 as mt5

logger = logging.getLogger(__name__)

class MarketFilters:
    """
    Institutional Market Condition Filters for XAUUSD (Gold):
    1. Spread Guard: Aborts new executions if spread > 40 points ($0.40 on Gold).
    2. Macro News Blackout: Rejects new trades 30m before and 15m after high-impact USD economic events.
    3. Breakeven Trailing Calculator: Identifies when 1:1 R:R is achieved to move SL to entry + spread buffer.
    """

    MAX_SPREAD_POINTS = 40.0  # Max allowable spread in broker points ($0.40 on Gold)
    SPREAD_BUFFER_POINTS = 15.0  # Breakeven buffer in points ($0.15) to guarantee commission & slippage coverage

    HIGH_IMPACT_KEYWORDS = [
        "CPI", "CONSUMER PRICE INDEX",
        "PCE", "CORE PCE",
        "NONFARM PAYROLLS", "NON-FARM", "NFP",
        "FOMC", "FED INTEREST RATE", "FED RATE",
        "INTEREST RATE DECISION", "FED CHAIR POWELL",
        "FEDERAL FUNDS RATE"
    ]

    def __init__(self):
        self.cached_news_events: List[Dict[str, Any]] = []
        self.last_news_fetch_time: Optional[datetime] = None
        self.news_cache_duration = timedelta(hours=2)
    def _ensure_mt5(self):
        terminal_path = r"C:\Program Files\MetaTrader 5\terminal64.exe"
        account_info = mt5.account_info()
        if not account_info:
            mt5.initialize(path=terminal_path)

    def check_spread_guard(self, symbol: str = "XAUUSD") -> Tuple[bool, float, str]:
        """
        Measures real-time spread on XAUUSD.
        Returns: (passes_guard: bool, current_spread_points: float, reason: str)
        """
        self._ensure_mt5()
        symbol_info = mt5.symbol_info(symbol)
        if not symbol_info:
            return False, 0.0, f"Cannot retrieve symbol info for {symbol}."

        tick = mt5.symbol_info_tick(symbol)
        if not tick or tick.ask <= 0 or tick.bid <= 0:
            return False, 0.0, f"Cannot retrieve live tick for {symbol}."

        point = symbol_info.point or 0.01
        spread_price_diff = tick.ask - tick.bid
        spread_points = spread_price_diff / point

        # Alternatively fallback to symbol_info.spread if available
        if spread_points <= 0 and symbol_info.spread > 0:
            spread_points = float(symbol_info.spread)

        if spread_points > self.MAX_SPREAD_POINTS:
            msg = (
                f"SPREAD GUARD TRIGGERED: Current spread on {symbol} is {spread_points:.1f} points "
                f"(${spread_points * point:.2f}), exceeding institutional maximum threshold of "
                f"{self.MAX_SPREAD_POINTS:.1f} points ($0.40). Trade execution aborted."
            )
            logger.warning(msg)
            return False, spread_points, msg

        logger.debug(f"Spread Guard passed for {symbol}: {spread_points:.1f} pts (limit: {self.MAX_SPREAD_POINTS:.1f} pts).")
        return True, spread_points, f"Spread normal ({spread_points:.1f} pts)"

    def fetch_economic_calendar(self) -> List[Dict[str, Any]]:
        """
        Fetches or refreshes the economic calendar for USD high-impact news.
        Uses public financial calendar endpoints with robust fallback and caching.
        """
        now = datetime.now(timezone.utc)
        if self.last_news_fetch_time and (now - self.last_news_fetch_time) < self.news_cache_duration:
            return self.cached_news_events

        events = []
        try:
            # ForexFactory weekly calendar feed JSON
            url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=4) as response:
                if response.status == 200:
                    raw_data = json.loads(response.read().decode("utf-8"))
                    for item in raw_data:
                        # Only care about USD events with High impact
                        country = str(item.get("country", "")).upper()
                        impact = str(item.get("impact", "")).upper()
                        title = str(item.get("title", "")).upper()

                        if country == "USD" and (impact == "HIGH" or any(kw in title for kw in self.HIGH_IMPACT_KEYWORDS)):
                            # Format date: 2026-10-02T08:30:00-04:00
                            date_str = item.get("date")
                            if date_str:
                                try:
                                    event_dt = datetime.fromisoformat(date_str)
                                    events.append({
                                        "title": item.get("title"),
                                        "country": country,
                                        "impact": impact,
                                        "time_utc": event_dt.astimezone(timezone.utc)
                                    })
                                except Exception:
                                    pass

            logger.info(f"Loaded {len(events)} high-impact USD calendar events.")
            self.cached_news_events = events
            self.last_news_fetch_time = now
        except Exception as e:
            logger.warning(f"Could not fetch online economic calendar feed ({e}). Using existing/empty cache.")

        return self.cached_news_events

    def check_news_blackout(self, symbol: str = "XAUUSD") -> Tuple[bool, Optional[str]]:
        """
        Checks if the current time falls inside the Macro News Blackout window:
        - 30 minutes BEFORE a high-impact USD economic event
        - 15 minutes AFTER a high-impact USD economic event
        Returns: (passes_filter: bool, blackout_reason: Optional[str])
        """
        events = self.fetch_economic_calendar()
        now = datetime.now(timezone.utc)

        for ev in events:
            ev_time = ev["time_utc"]
            title = ev["title"]

            window_start = ev_time - timedelta(minutes=30)
            window_end = ev_time + timedelta(minutes=15)

            if window_start <= now <= window_end:
                diff_to_event = (ev_time - now).total_seconds() / 60.0
                if diff_to_event > 0:
                    timing_desc = f"in {diff_to_event:.0f} minutes"
                else:
                    timing_desc = f"{abs(diff_to_event):.0f} minutes ago"

                reason = (
                    f"MACRO NEWS BLACKOUT ACTIVE: High-impact USD event '{title}' occurs {timing_desc}. "
                    f"Trading is halted from 30m prior to 15m post-release to avoid institutional slippage."
                )
                logger.warning(reason)
                return False, reason

        return True, None

    def calculate_breakeven_sl(
        self,
        side: str,
        entry_price: float,
        initial_sl: float,
        current_price: float,
        current_sl: float,
        point: float = 0.01
    ) -> Optional[float]:
        """
        Checks if 1:1 Risk-to-Reward has been achieved:
        - For BUY:
          Risk distance = entry_price - initial_sl
          If (current_price - entry_price) >= Risk distance:
            New SL = entry_price + (SPREAD_BUFFER_POINTS * point)
            Return new SL if it moves current SL higher
        - For SELL:
          Risk distance = initial_sl - entry_price
          If (entry_price - current_price) >= Risk distance:
            New SL = entry_price - (SPREAD_BUFFER_POINTS * point)
            Return new SL if it moves current SL lower (or if current SL is 0)
        """
        spread_buffer = self.SPREAD_BUFFER_POINTS * point

        if side.upper() in ["BUY", "LONG"]:
            risk_dist = entry_price - initial_sl
            if risk_dist <= 0:
                return None
            gain_dist = current_price - entry_price
            # Achieved 1:1 R:R
            if gain_dist >= risk_dist:
                target_be_sl = round(entry_price + spread_buffer, 2)
                # Only move SL up, never down
                if current_sl is None or current_sl < target_be_sl:
                    return target_be_sl

        elif side.upper() in ["SELL", "SHORT"]:
            risk_dist = initial_sl - entry_price
            if risk_dist <= 0:
                return None
            gain_dist = entry_price - current_price
            # Achieved 1:1 R:R
            if gain_dist >= risk_dist:
                target_be_sl = round(entry_price - spread_buffer, 2)
                # Only move SL down, never up
                if current_sl is None or current_sl == 0.0 or current_sl > target_be_sl:
                    return target_be_sl

        return None
