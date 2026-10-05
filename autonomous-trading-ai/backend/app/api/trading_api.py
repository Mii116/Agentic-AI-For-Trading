import logging
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.trading import TradeJournal
from app.schemas.trade import TradeCreate, TradeJournalResponse, TradeJournalFilter
from app.schemas.position import PositionCloseRequest, PositionModifySLTP
from app.services.trading_service import TradingService
from app.services.market_data_service import MarketDataService
from app.services.analytics_service import AnalyticsService
from app.portfolio.manager import PortfolioManager
from app.portfolio.rebalancer import PortfolioRebalancer
from app.monitoring.health import SystemHealthMonitor
from app.monitoring.alerts import AlertManager
from app.strategies.registry import StrategyRegistry
from app.reports.performance import PerformanceReporter
from app.reports.exporter import ReportExporter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Institutional Trading"])

trading_service = TradingService()
market_service = MarketDataService()
health_monitor = SystemHealthMonitor()
strategy_registry = StrategyRegistry()
portfolio_manager = PortfolioManager()
rebalancer = PortfolioRebalancer()

# === 1. OVERVIEW & KPIS ===
@router.get("/overview")
def get_executive_overview():
    """Returns high-level institutional overview (Equity, Balance, Margin, Circuit Breakers, VaR)."""
    return AnalyticsService.get_executive_overview()

# === 2. TRADES & ORDER EXECUTION ===
@router.post("/trades")
def submit_trade(req: TradeCreate):
    """Submits an institutional trade through the Smart Execution Router."""
    return trading_service.submit_trade(req)

@router.get("/trades/journal")
def get_trade_journal(
    magic_number: Optional[int] = Query(None, description="1001 for Swing, 2002 for Scalp"),
    status: Optional[str] = Query(None, description="PASS, FAIL, BREAKEVEN, OPEN"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """Fetches paginated, segregated trade journal records."""
    query = db.query(TradeJournal)
    if magic_number:
        query = query.filter(TradeJournal.magic_number == magic_number)
    if status:
        query = query.filter(TradeJournal.status == status)

    trades = query.order_by(TradeJournal.id.desc()).offset(offset).limit(limit).all()
    return trades

# === 3. ACTIVE POSITIONS & CONTROLS ===
@router.get("/positions")
def get_open_positions(symbol: Optional[str] = "XAUUSD"):
    """Returns active broker positions with floating PnL and magic numbers."""
    return trading_service.get_active_positions(symbol=symbol)

@router.post("/positions/{ticket}/close")
def close_position(ticket: int, volume: Optional[float] = Query(None, description="Optional partial close volume")):
    """Closes an active position in full or partially."""
    success = trading_service.close_position_by_ticket(ticket, volume=volume)
    if not success:
        raise HTTPException(status_code=400, detail=f"Failed to close position #{ticket}")
    return {"status": "success", "ticket": ticket, "message": f"Position #{ticket} closed successfully."}

@router.post("/positions/emergency-close-all")
def emergency_close_all(symbol: Optional[str] = "XAUUSD"):
    """Panic liquidation: Closes all active positions on symbol."""
    closed_count = trading_service.emergency_close_all(symbol=symbol)
    return {"status": "success", "closed_positions": closed_count}

# === 4. MARKET DATA & SMC INDICATORS ===
@router.get("/market/quote")
def get_live_quote(symbol: str = "XAUUSD"):
    """Returns real-time bid, ask, and spread points."""
    return market_service.get_live_quote(symbol)

@router.get("/market/smc")
def get_smc_analysis(symbol: str = "XAUUSD", timeframe: str = "1h"):
    """Returns Order Blocks, Fair Value Gaps, and BOS/CHoCH structural analysis."""
    return market_service.get_smc_analysis(symbol, timeframe)

@router.get("/market/macro")
def get_macro_state():
    """Returns US 10-Year Treasury Yield, DXY momentum, and Swing BUY permission."""
    return market_service.get_macro_state()

# === 5. PORTFOLIO & REBALANCING ===
@router.get("/portfolio")
def get_portfolio_summary():
    """Returns multi-asset allocations, net directional exposure, and effective leverage."""
    return portfolio_manager.get_portfolio_summary()

@router.get("/portfolio/rebalance-check")
def check_rebalance_status():
    """Checks whether leverage or concentration limits have triggered rebalancing warnings."""
    needs_rebalance, triggers = rebalancer.check_rebalance_triggers()
    return {"needs_rebalance": needs_rebalance, "triggers": triggers}

# === 6. STRATEGIES REGISTRY ===
@router.get("/strategies")
def list_strategies():
    """Returns all registered algorithmic strategies and their active status."""
    return strategy_registry.list_strategies()

@router.post("/strategies/{name}/toggle")
def toggle_strategy(name: str, enabled: bool = Query(...)):
    """Dynamically enables or disables a strategy at runtime."""
    success = strategy_registry.set_enabled(name, enabled)
    if not success:
        raise HTTPException(status_code=404, detail=f"Strategy '{name}' not found.")
    return {"status": "success", "strategy": name, "enabled": enabled}

# === 7. SYSTEM MONITORING & ALERTS ===
@router.get("/monitoring/health")
def get_system_health():
    """Returns telemetry on MT5 latency, database connection, host CPU/RAM, and uptime."""
    return health_monitor.check_health()

@router.get("/monitoring/alerts")
def get_recent_alerts(limit: int = 20):
    """Returns recent institutional alerts (circuit breaker trips, spread blowouts, margin alerts)."""
    return AlertManager.get_recent_alerts(limit=limit)

# === 8. PERFORMANCE & AUDIT REPORTS ===
@router.get("/reports/performance")
def get_performance_report(magic_number: Optional[int] = Query(None, description="Filter by strategy magic number")):
    """Returns win rate, profit factor, max drawdown, and expectancy."""
    return PerformanceReporter.generate_performance_metrics(magic_number=magic_number)

@router.get("/reports/export/csv")
def export_trades_csv(magic_number: Optional[int] = Query(None)):
    """Downloads a complete CSV of trade journals with AI post-mortem reasons."""
    csv_data = ReportExporter.export_to_csv(magic_number=magic_number)
    return Response(content=csv_data, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=trade_journal.csv"})

@router.get("/reports/export/markdown")
def export_trades_markdown():
    """Returns an executive Markdown audit report."""
    md_text = ReportExporter.export_to_markdown_summary()
    return {"markdown_report": md_text}
