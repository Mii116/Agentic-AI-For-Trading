"""
Trading Arbiter Module:
Aliases and re-exports ChiefRiskArbiter from app.agents.arbiter for unified access across trading and agent modules.
"""
from app.agents.arbiter import ChiefRiskArbiter

__all__ = ["ChiefRiskArbiter"]
