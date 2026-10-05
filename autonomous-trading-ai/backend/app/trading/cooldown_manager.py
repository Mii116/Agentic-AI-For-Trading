import logging
import time
from typing import Tuple, List, Dict, Any, Optional
from datetime import datetime, timezone, timedelta

from app.db.session import SessionLocal
from app.models.trading import TradeJournal

logger = logging.getLogger(__name__)

class MarketCooldownManager:
    """
    Manages:
    1. Setup Invalidation Debounce:
       Once an order closes, blacklists re-entry in that same direction within the same $3.00 price zone for 20 minutes.
    2. Scalper Cooldown:
       Strict 15-minute cooldown after any market exit before the Scalp Trader (Magic 2002) is allowed to scan for new entries.
    Persisted against DB TradeJournal so state survives restarts.
    """
    DEBOUNCE_PRICE_ZONE = 3.00       # $3.00 price zone blacklist
    DEBOUNCE_DURATION_SEC = 20 * 60  # 20 minutes in seconds (1200s)
    SCALPER_COOLDOWN_SEC = 15 * 60   # 15 minutes in seconds (900s)

    _in_memory_blacklists: List[Dict[str, Any]] = []
    _last_market_exit_time: float = 0.0
    _last_exit_details: Optional[Dict[str, Any]] = None

    @classmethod
    def record_market_exit(
        cls,
        symbol: str,
        direction: str,
        exit_price: float,
        magic_number: int,
        ticket: Optional[int] = None
    ):
        """Records an exit to trigger debounce zone blacklist and scalper cooldown."""
        now = time.time()
        cls._last_market_exit_time = now
        cls._last_exit_details = {
            "ticket": ticket,
            "symbol": symbol,
            "direction": direction,
            "price": exit_price,
            "magic_number": magic_number,
            "timestamp": now,
            "expires_at": now + cls.DEBOUNCE_DURATION_SEC
        }
        cls._in_memory_blacklists.append(cls._last_exit_details)
        logger.warning(
            f"[COOLDOWN MANAGER] Registered Market Exit for #{ticket or 'N/A'} ({direction} at {exit_price:.2f}, Magic {magic_number}). "
            f"Blacklisting {direction} within ${cls.DEBOUNCE_PRICE_ZONE:.2f} for 20m. Scalper 15m cooldown initiated."
        )

    @classmethod
    def check_scalper_cooldown(cls) -> Tuple[bool, float, str]:
        """
        Verifies if Scalp Trader (Magic 2002) is in mandatory 15-minute cooldown after a market exit.
        Returns: (is_cooling_down: bool, remaining_seconds: float, message: str)
        """
        now_ts = time.time()
        now_dt = datetime.now(timezone.utc)

        # 1. Check in-memory timer
        if cls._last_market_exit_time > 0:
            elapsed = now_ts - cls._last_market_exit_time
            if elapsed < cls.SCALPER_COOLDOWN_SEC:
                rem_sec = cls.SCALPER_COOLDOWN_SEC - elapsed
                msg = f"SCALPER COOLDOWN ACTIVE: {rem_sec / 60.0:.1f}m remaining of 15m mandatory exit cooldown."
                return True, rem_sec, msg

        # 2. Check Database TradeJournal for recent closed trades in the last 15 minutes
        db = SessionLocal()
        try:
            cutoff = now_dt - timedelta(seconds=cls.SCALPER_COOLDOWN_SEC)
            recent_exit = db.query(TradeJournal).filter(
                TradeJournal.closed_at.isnot(None),
                TradeJournal.closed_at >= cutoff
            ).order_by(TradeJournal.closed_at.desc()).first()

            if recent_exit and recent_exit.closed_at:
                c_at = recent_exit.closed_at
                if c_at.tzinfo is None:
                    c_at = c_at.replace(tzinfo=timezone.utc)
                elapsed = (now_dt - c_at).total_seconds()
                if 0 <= elapsed < cls.SCALPER_COOLDOWN_SEC:
                    rem_sec = cls.SCALPER_COOLDOWN_SEC - elapsed
                    cls._last_market_exit_time = now_ts - elapsed  # Sync memory
                    msg = (
                        f"SCALPER COOLDOWN ACTIVE: Trade #{recent_exit.ticket or recent_exit.id} "
                        f"closed {elapsed / 60.0:.1f}m ago. {rem_sec / 60.0:.1f}m remaining of 15m cooldown."
                    )
                    return True, rem_sec, msg
        except Exception as e:
            logger.error(f"Error checking scalper cooldown against DB: {e}")
        finally:
            db.close()

        return False, 0.0, "Scalper operational (no active exit cooldown)."

    @classmethod
    def check_price_zone_debounce(cls, symbol: str, direction: str, proposed_price: float) -> Tuple[bool, float, str]:
        """
        Verifies if proposed entry falls within the $3.00 price zone of a recently closed trade
        in the same direction within 20 minutes.
        Returns: (is_blacklisted: bool, remaining_seconds: float, reason: str)
        """
        now_ts = time.time()
        now_dt = datetime.now(timezone.utc)

        # 1. Clean up expired in-memory zones
        cls._in_memory_blacklists = [z for z in cls._in_memory_blacklists if now_ts < z.get("expires_at", 0)]

        for zone in cls._in_memory_blacklists:
            if zone["symbol"] == symbol and zone["direction"].upper() == direction.upper():
                dist = abs(proposed_price - zone["price"])
                if dist <= cls.DEBOUNCE_PRICE_ZONE:
                    rem_sec = zone["expires_at"] - now_ts
                    msg = (
                        f"SETUP INVALIDATION DEBOUNCE: Entry price {proposed_price:.2f} is within "
                        f"${dist:.2f} (<= ${cls.DEBOUNCE_PRICE_ZONE:.2f}) of recent exit at {zone['price']:.2f} "
                        f"({direction}). Blacklisted for {rem_sec / 60.0:.1f} more mins."
                    )
                    return True, rem_sec, msg

        # 2. Check Database TradeJournal for trades closed in the last 20 minutes
        db = SessionLocal()
        try:
            cutoff = now_dt - timedelta(seconds=cls.DEBOUNCE_DURATION_SEC)
            recent_trades = db.query(TradeJournal).filter(
                TradeJournal.symbol == symbol,
                TradeJournal.side == direction.upper(),
                TradeJournal.closed_at.isnot(None),
                TradeJournal.closed_at >= cutoff
            ).order_by(TradeJournal.closed_at.desc()).all()

            for t in recent_trades:
                exit_ref_price = t.exit_price or t.entry_price
                if exit_ref_price:
                    dist = abs(proposed_price - exit_ref_price)
                    if dist <= cls.DEBOUNCE_PRICE_ZONE:
                        c_at = t.closed_at
                        if c_at.tzinfo is None:
                            c_at = c_at.replace(tzinfo=timezone.utc)
                        elapsed = (now_dt - c_at).total_seconds()
                        if 0 <= elapsed < cls.DEBOUNCE_DURATION_SEC:
                            rem_sec = cls.DEBOUNCE_DURATION_SEC - elapsed
                            msg = (
                                f"SETUP INVALIDATION DEBOUNCE: Entry price {proposed_price:.2f} is within "
                                f"${dist:.2f} of trade #{t.ticket or t.id} exit {exit_ref_price:.2f} "
                                f"({direction}). Blacklisted for {rem_sec / 60.0:.1f} more mins."
                            )
                            return True, rem_sec, msg
        except Exception as e:
            logger.error(f"Error checking price zone debounce against DB: {e}")
        finally:
            db.close()

        return False, 0.0, "Price zone clean (no active debounce blacklist)."

    @classmethod
    def get_active_blacklist_zones(cls) -> List[Dict[str, Any]]:
        """Returns list of currently active blacklist zones with remaining countdown for dashboard visualization."""
        now_ts = time.time()
        cls._in_memory_blacklists = [z for z in cls._in_memory_blacklists if now_ts < z.get("expires_at", 0)]
        active = []
        for z in cls._in_memory_blacklists:
            rem = max(0.0, z["expires_at"] - now_ts)
            active.append({
                "symbol": z["symbol"],
                "direction": z["direction"],
                "price": z["price"],
                "zone_range": f"${z['price'] - cls.DEBOUNCE_PRICE_ZONE:.2f} - ${z['price'] + cls.DEBOUNCE_PRICE_ZONE:.2f}",
                "remaining_seconds": round(rem, 0),
                "remaining_minutes": round(rem / 60.0, 1),
                "magic_number": z.get("magic_number")
            })
        return active
