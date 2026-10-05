"""
Multi-Timeframe Multi-Agent Architecture for Institutional Gold (XAUUSD) Trading.
"""
from app.agents.trade_proposal import TradeProposal
from app.agents.macro_director import MacroDirector
from app.agents.structural_scout import StructuralScout
from app.agents.micro_sniper import MicroSniper
from app.agents.swing_trader import SwingTrader
from app.agents.scalp_trader import ScalpTrader
from app.agents.arbiter import ChiefRiskArbiter

__all__ = [
    "TradeProposal",
    "MacroDirector",
    "StructuralScout",
    "MicroSniper",
    "SwingTrader",
    "ScalpTrader",
    "ChiefRiskArbiter"
]
