import logging
import datetime
from typing import Optional, Dict, Any, List

from app.risk.market_filters import MarketFilters
from app.agents.macro_director import MacroDirector
from app.data.mt5_data import MT5DataClient

logger = logging.getLogger(__name__)


class NFPEventHandler:
    """Handles pre‑release macro intelligence and post‑release volatility for the NFP (Non‑Farm Payrolls) event.

    The handler works in three phases:
    1. **Pre‑Release Intelligence** – fetches the upcoming NFP calendar entry, builds a macro
       briefing (headline, unemployment, earnings) and derives a directional bias based on the
       yield/DXY macro director.
    2. **Historical Volatility Reference** – provides a typical 15‑minute price range for XAUUSD
       during past NFP releases.  In a production system this would query a DB or CSV; for now we
       use a hard‑coded range derived from the current build specification (20 – 45 USD).
    3. **Post‑Release State** – after the release, the handler stays active for a configurable
       window (default 30 min) so that downstream agents can adjust order sizing and limit‑price
       placement according to the observed volatility.
    """

    # How long after the NFP release the post‑event window stays active (seconds)
    POST_EVENT_WINDOW = 30 * 60

    def __init__(self):
        self.market_filters = MarketFilters()
        self.macro_director = MacroDirector()
        self.nfp_event: Optional[Dict[str, Any]] = None  # the upcoming NFP calendar entry
        self.bias: str = "NEUTRAL"
        self.volatility_range: List[float] = [20.0, 45.0]  # low, high (USD) – placeholder
        self.post_release_active: bool = False
        self.post_release_ts: Optional[datetime.datetime] = None

    # ---------------------------------------------------------------------
    # 1. Calendar handling
    # ---------------------------------------------------------------------
    def _refresh_nfp_event(self) -> None:
        """Look through the market‑filter calendar for the next NFP event.

        The ``MarketFilters.HIGH_IMPACT_KEYWORDS`` list already contains the keyword
        ``"NFP"``.  We fetch the cached calendar and pick the first future event whose
        title contains the keyword ``NFP`` (case‑insensitive).
        """
        events = self.market_filters.fetch_economic_calendar()
        now = datetime.datetime.now(datetime.timezone.utc)
        next_nfp = None
        for ev in events:
            title = ev.get("title", "").upper()
            if "NFP" in title:
                ev_time = ev.get("time_utc")
                if ev_time and ev_time > now:
                    next_nfp = ev
                    break
        self.nfp_event = next_nfp
        if next_nfp:
            logger.info(f"[NFPHandler] Upcoming NFP event found: {next_nfp['title']} at {next_nfp['time_utc']}")
        else:
            logger.debug("[NFPHandler] No upcoming NFP event detected.")

    def minutes_to_nfp(self) -> Optional[int]:
        """Return minutes until the next NFP release, or ``None`` if none found."""
        if not self.nfp_event:
            self._refresh_nfp_event()
        if not self.nfp_event:
            return None
        now = datetime.datetime.now(datetime.timezone.utc)
        delta = self.nfp_event["time_utc"] - now
        return int(delta.total_seconds() // 60)

    # ---------------------------------------------------------------------
    # 2. Macro briefing & bias calculation
    # ---------------------------------------------------------------------
    def _derive_macro_bias(self) -> str:
        """Combine the macro director regime with a simple NFP‑specific rule.

        * If the macro regime is **BULLISH** and the NFP surprise (jobs) is expected to be
          negative, we flip to **BEARISH** (because USD weakness would hurt Gold).
        * Conversely, a **BEARISH** macro regime combined with a strong‑jobs surprise
          flips to **BULLISH**.
        * If we cannot determine the surprise (no consensus data), we keep the macro regime.
        """
        # Use the existing macro director on the latest H4/H1 bars (the orchestrator already
        # updates ``self.macro_director`` – we reuse it here by pulling a quick snapshot.
        # For simplicity we request a single recent bar series from MT5DataClient.
        data_client = MT5DataClient()
        bars_h4 = data_client.fetch_bars(symbol="XAUUSD", timeframe="4h", num_bars=2)
        bars_h1 = data_client.fetch_bars(symbol="XAUUSD", timeframe="1h", num_bars=2)
        if not bars_h4 or not bars_h1:
            logger.warning("[NFPHandler] Unable to fetch HTF bars for macro bias – defaulting NEUTRAL")
            return "NEUTRAL"
        regime_info = self.macro_director.analyze_regime(bars_h4, bars_h1, symbol="XAUUSD")
        macro_regime = regime_info.get("regime", "NEUTRAL")

        # Placeholder for consensus vs. expected jobs – in a real implementation we would pull
        # a consensus feed.  Here we assume a neutral surprise (0) which means we keep the macro.
        consensus_surprise = 0  # jobs difference from consensus (positive = strong print)
        if macro_regime == "BULLISH" and consensus_surprise < -50000:
            return "BEARISH"
        if macro_regime == "BEARISH" and consensus_surprise > 50000:
            return "BULLISH"
        return macro_regime

    def build_pre_release_briefing(self) -> Dict[str, Any]:
        """Assemble a dict with macro facts, bias and a simple driver/outcome matrix.

        Example output::

            {
                "event_title": "NONFARM PAYROLLS",
                "event_time_utc": "2026-10-02T06:00:00Z",
                "bias": "BEARISH",
                "driver": "Hot jobs + rising wages → higher real yields → USD strength → Gold bearish",
                "volatility_range": [20.0, 45.0]
            }
        """
        minutes = self.minutes_to_nfp()
        if minutes is None:
            return {}
        bias = self._derive_macro_bias()
        driver = (
            "Hot jobs + rising wages → higher real yields → USD strength → Gold bearish"
            if bias == "BEARISH"
            else "Weak jobs → rate‑cut expectations → USD weakness → Gold bullish"
        )
        briefing = {
            "event_title": self.nfp_event.get("title", "NONFARM PAYROLLS"),
            "event_time_utc": self.nfp_event.get("time_utc"),
            "minutes_to_event": minutes,
            "bias": bias,
            "driver": driver,
            "volatility_range": self.volatility_range,
        }
        logger.info(f"[NFPHandler] Pre‑release briefing prepared: {briefing}")
        return briefing

    # ---------------------------------------------------------------------
    # 3. Post‑release activation
    # ---------------------------------------------------------------------
    def activate_post_release_window(self) -> None:
        """Mark the post‑release window as active for the configured duration."""
        self.post_release_active = True
        self.post_release_ts = datetime.datetime.now(datetime.timezone.utc)
        logger.info("[NFPHandler] Post‑release volatility window activated.")

    def check_post_release_window(self) -> bool:
        """Return ``True`` while the post‑release window is still active; otherwise reset.
        """
        if not self.post_release_active or not self.post_release_ts:
            return False
        elapsed = (datetime.datetime.now(datetime.timezone.utc) - self.post_release_ts).total_seconds()
        if elapsed > self.POST_EVENT_WINDOW:
            self.post_release_active = False
            self.post_release_ts = None
            logger.debug("[NFPHandler] Post‑release window expired.")
        return self.post_release_active

    # ---------------------------------------------------------------------
    # 4. Public update hook used by the orchestrator
    # ---------------------------------------------------------------------
    def update_state(self) -> None:
        """Refresh calendar, recompute bias and manage the post‑release lifecycle.
        """
        self._refresh_nfp_event()
        if self.nfp_event:
            minutes = self.minutes_to_nfp()
            # If we are within the blackout window (30 min before ↔ 15 min after) we let the
            # generic ``MarketFilters`` handle trade halts, but we also expose the bias for
            # downstream agents.
            if minutes is not None and minutes <= 30:
                # Pre‑release – compute bias for use by the arbiter.
                self.bias = self._derive_macro_bias()
                logger.debug(f"[NFPHandler] Pre‑release bias set to {self.bias}")
            # Detect the moment the event just passed (within 2 min) to start the post‑release
            # window.
            now = datetime.datetime.now(datetime.timezone.utc)
            if 0 <= (now - self.nfp_event["time_utc"]).total_seconds() <= 120:
                self.activate_post_release_window()
        # Refresh post‑release flag if needed.
        self.check_post_release_window()
