from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime, timezone

@dataclass
class TradeProposal:
    """
    Standardized Trade Proposal emitted by timeframe agents (Macro Director, Structural Scout, Micro Sniper).
    Timeframe agents NEVER execute orders directly.
    """
    proposal_id: str
    agent_role: str               # "STRUCTURAL_SCOUT" or "MICRO_SNIPER"
    timeframe: str                # "M15", "M5", "M1", "H1", "H4"
    symbol: str                   # "XAUUSD"
    direction: str                # "BUY", "SELL", "HOLD"
    entry_price: float
    structural_sl: float
    suggested_tp: float
    confluence_score: float       # 0.0 to 100.0
    thesis: str
    supporting_confluences: List[str] = field(default_factory=list)
    invalidation_condition: str = ""
    magic_number: int = 1001      # 1001 for Swing Trader, 2002 for Scalp Trader
    risk_pct: float = 0.015       # Individual risk budget allocated for this trade
    is_scale_in: bool = False     # True if this is a pyramiding pullback scale-in
    order_execution_type: str = "LIMIT"   # "LIMIT", "MARKET" or "ZONE_RETEST" (tick-confirmed market entry)
    setup_cluster: str = "M5_FVG_LIMIT"  # e.g. "M5_FVG_LIMIT", "M1_SWEEP_RETEST", "H1_PULLBACK_LIMIT"
    ttl_minutes: int = 20                 # Time-To-Live in minutes for pending limits / armed zones
    limit_equilibrium_pct: float = 0.50  # 50% equilibrium or edge
    parent_ticket: Optional[int] = None # Primary trade ticket if scale-in
    # --- ZONE_RETEST (dynamic confirmation entry) fields ---
    zone_low: Optional[float] = None          # Lower bound of the mitigation zone (FVG / sweep wick)
    zone_high: Optional[float] = None         # Upper bound of the mitigation zone
    invalidation_price: Optional[float] = None  # Knife filter: trading through this disarms the zone
    atr_m5: float = 0.0                       # ATR(14, M5) at arm time (drives tolerances)
    target_rr: float = 2.0                    # R multiple for TP, recomputed from actual fill
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def risk_reward_ratio(self) -> float:
        """Calculates expected Risk-to-Reward ratio."""
        risk = abs(self.entry_price - self.structural_sl)
        reward = abs(self.suggested_tp - self.entry_price)
        if risk <= 0:
            return 0.0
        return round(reward / risk, 2)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "agent_role": self.agent_role,
            "timeframe": self.timeframe,
            "symbol": self.symbol,
            "direction": self.direction,
            "order_execution_type": self.order_execution_type,
            "setup_cluster": self.setup_cluster,
            "ttl_minutes": self.ttl_minutes,
            "entry_price": self.entry_price,
            "structural_sl": self.structural_sl,
            "suggested_tp": self.suggested_tp,
            "risk_reward_ratio": self.risk_reward_ratio,
            "confluence_score": self.confluence_score,
            "thesis": self.thesis,
            "supporting_confluences": self.supporting_confluences,
            "invalidation_condition": self.invalidation_condition,
            "magic_number": self.magic_number,
            "risk_pct": self.risk_pct,
            "timestamp": self.timestamp.isoformat()
        }
