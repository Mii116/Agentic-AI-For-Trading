import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import desc
import MetaTrader5 as mt5

from app.db.session import get_db, SessionLocal
from app.models.trading import TradeJournal, Position, AccountBalance
from app.trading.mt5_engine import MT5ExecutionEngine
from app.trading.danger_sentry import DangerSentry

logger = logging.getLogger(__name__)

# Strict Malaysia Time (MYT / UTC+8)
MYT_TZ = timezone(timedelta(hours=8))

class ManualTradeRequest(BaseModel):
    symbol: str = "XAUUSD"
    side: str = "BUY"  # BUY or SELL
    volume: float = 0.10
    sl_price: Optional[float] = None
    tp_price: Optional[float] = None
    sl_distance: Optional[float] = 2.50
    tp_distance: Optional[float] = 5.00
    comment: Optional[str] = "Manual Dashboard Execution"

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])

@router.get("/status")
def get_dashboard_status(db: Session = Depends(get_db)):
    """Fetches real-time account status, gold ticker, and open positions."""
    terminal_path = r"C:\Program Files\MetaTrader 5\terminal64.exe"
    if not mt5.initialize(path=terminal_path):
        account_info = None
        tick = None
        open_positions = []
    else:
        account_info = mt5.account_info()
        tick = mt5.symbol_info_tick("XAUUSD")
        symbol_info = mt5.symbol_info("XAUUSD")
        open_positions = mt5.positions_get(symbol="XAUUSD") or []

    # Calculate real-time stats from TradeJournal
    journals = db.query(TradeJournal).all()
    closed_trades = [j for j in journals if j.status in ["PASS", "FAIL", "BREAKEVEN"]]
    pass_count = sum(1 for j in closed_trades if j.status == "PASS")
    fail_count = sum(1 for j in closed_trades if j.status == "FAIL")
    total_closed = len(closed_trades)
    win_rate = round((pass_count / total_closed * 100), 1) if total_closed > 0 else 0.0
    total_realized_pnl = round(sum(j.realized_pnl or 0.0 for j in closed_trades), 2)

    # Format open positions with danger meter
    formatted_positions = []
    point = 0.01
    current_bid = tick.bid if tick else 0.0
    current_ask = tick.ask if tick else 0.0

    for p in open_positions:
        side = "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL"
        curr_price = current_bid if side == "BUY" else current_ask
        sl = float(p.sl)
        entry = float(p.price_open)
        
        # Calculate Danger Meter (% distance moved towards Stop Loss)
        danger_pct = 0.0
        if sl > 0:
            if side == "BUY" and entry > sl:
                total_risk = entry - sl
                loss_distance = entry - curr_price
                danger_pct = max(0.0, min(100.0, (loss_distance / total_risk) * 100))
            elif side == "SELL" and sl > entry:
                total_risk = sl - entry
                loss_distance = curr_price - entry
                danger_pct = max(0.0, min(100.0, (loss_distance / total_risk) * 100))

        pos_open_time = float(p.time)
        now_ts = datetime.now(timezone.utc).timestamp()
        pos_age_sec = max(0.0, now_ts - pos_open_time)
        in_grace_period = pos_age_sec < 180.0
        grace_remaining_sec = max(0.0, 180.0 - pos_age_sec)

        danger_level = "CRITICAL" if danger_pct >= 80 else ("WARNING" if danger_pct >= 50 else "SAFE")
        role = "SWING (1001)" if p.magic == 1001 else ("SCALP (2002)" if p.magic == 2002 else f"MAGIC {p.magic}")

        formatted_positions.append({
            "ticket": p.ticket,
            "magic": p.magic,
            "role": role,
            "symbol": p.symbol,
            "side": side,
            "lot_size": p.volume,
            "entry_price": entry,
            "current_price": curr_price,
            "stop_loss": sl,
            "take_profit": float(p.tp),
            "unrealized_pnl": round(float(p.profit), 2),
            "comment": p.comment,
            "danger_pct": round(danger_pct, 1),
            "danger_level": danger_level,
            "in_grace_period": in_grace_period,
            "grace_remaining_sec": round(grace_remaining_sec, 0),
            "pos_age_sec": round(pos_age_sec, 0),
            "opened_at": datetime.fromtimestamp(p.time, tz=timezone.utc).astimezone(MYT_TZ).strftime("%Y-%m-%d %H:%M:%S MYT")
        })

    spread_points = round((tick.ask - tick.bid) / point, 1) if tick else 0.0

    return {
        "account": {
            "login": account_info.login if account_info else "N/A",
            "server": account_info.server if account_info else "N/A",
            "equity": round(account_info.equity, 2) if account_info else 0.0,
            "balance": round(account_info.balance, 2) if account_info else 0.0,
            "free_margin": round(account_info.margin_free, 2) if account_info else 0.0,
            "margin": round(account_info.margin, 2) if account_info else 0.0
        },
        "gold_ticker": {
            "symbol": "XAUUSD",
            "bid": current_bid,
            "ask": current_ask,
            "spread_points": spread_points,
            "spread_safe": spread_points <= 40.0
        },
        "performance": {
            "total_trades": total_closed,
            "passes": pass_count,
            "fails": fail_count,
            "win_rate_pct": win_rate,
            "total_realized_pnl": total_realized_pnl,
            "active_positions_count": len(open_positions)
        },
        "active_positions": formatted_positions
    }

@router.get("/journal")
def get_trade_journal(db: Session = Depends(get_db)):
    """Fetches all journaled trades with reasons, techniques, pass/fail status, and AI post-mortems."""
    trades = db.query(TradeJournal).order_by(desc(TradeJournal.id)).limit(100).all()

    journal_list = []
    for t in trades:
        journal_list.append({
            "id": t.id,
            "ticket": t.ticket,
            "symbol": t.symbol,
            "side": t.side,
            "lot_size": t.lot_size,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "stop_loss": t.stop_loss,
            "take_profit": t.take_profit,
            "realized_pnl": round(t.realized_pnl or 0.0, 2),
            "status": t.status,  # PASS, FAIL, BREAKEVEN, OPEN
            "technique_used": t.technique_used,
            "reason": t.reason,
            "invalidation_condition": t.invalidation_condition,
            "exit_reason": t.exit_reason or "N/A",
            "danger_trigger": t.danger_trigger,
            "why_it_went_wrong": t.why_it_went_wrong,
            "lessons_learned": t.lessons_learned,
            "opened_at": (t.opened_at.replace(tzinfo=timezone.utc) if t.opened_at.tzinfo is None else t.opened_at).astimezone(MYT_TZ).strftime("%Y-%m-%d %H:%M:%S MYT") if t.opened_at else "N/A",
            "closed_at": (t.closed_at.replace(tzinfo=timezone.utc) if t.closed_at.tzinfo is None else t.closed_at).astimezone(MYT_TZ).strftime("%Y-%m-%d %H:%M:%S MYT") if t.closed_at else None
        })

    return journal_list

@router.post("/close-position/{ticket}")
def emergency_close_position(ticket: int):
    """Manually or remotely triggers emergency close for a specific position."""
    engine = MT5ExecutionEngine()
    success = engine.close_position(ticket)
    if not success:
        raise HTTPException(status_code=400, detail=f"Failed to close position #{ticket}")
    return {"status": "success", "message": f"Successfully emergency closed position #{ticket}"}

@router.post("/close-all")
def emergency_close_all():
    """Emergency closes all open positions across all symbols."""
    engine = MT5ExecutionEngine()
    engine.close_all_positions()
    return {"status": "success", "message": "Closed all active MT5 positions."}

@router.post("/execute-market-order")
def execute_manual_market_order(req: ManualTradeRequest):
    """
    Executes a manual market BUY or SELL order directly on MT5 with explicit Stop Loss and Take Profit.
    """
    if not mt5.initialize():
        raise HTTPException(status_code=503, detail="MT5 terminal not initialized or unavailable.")

    tick = mt5.symbol_info_tick(req.symbol)
    if not tick:
        raise HTTPException(status_code=400, detail=f"Cannot retrieve live tick for {req.symbol}.")

    side = req.side.upper()
    if side not in ("BUY", "SELL"):
        raise HTTPException(status_code=400, detail="Side must be BUY or SELL.")

    ref_price = tick.ask if side == "BUY" else tick.bid

    # Calculate or validate SL and TP
    if req.sl_price is not None and req.sl_price > 0:
        sl = float(round(req.sl_price, 2))
    else:
        dist = req.sl_distance if req.sl_distance and req.sl_distance > 0 else 2.50
        sl = round(ref_price - dist if side == "BUY" else ref_price + dist, 2)

    if req.tp_price is not None and req.tp_price > 0:
        tp = float(round(req.tp_price, 2))
    else:
        dist = req.tp_distance if req.tp_distance and req.tp_distance > 0 else 5.00
        tp = round(ref_price + dist if side == "BUY" else ref_price - dist, 2)

    engine = MT5ExecutionEngine()
    vol = max(0.01, round(float(req.volume), 2))
    comment = (req.comment or "Manual Dashboard")[:31]

    success = engine.execute_custom_order(
        symbol=req.symbol,
        side=side,
        volume=vol,
        stop_loss=sl,
        take_profit=tp,
        comment=comment,
        proposal=None
    )

    if not success:
        raise HTTPException(status_code=400, detail=f"Failed to execute manual {side} order on MT5. Check margin or connection.")

    return {
        "status": "success",
        "message": f"Successfully executed manual {side} {vol} lots {req.symbol} at {ref_price:.2f} (SL: {sl:.2f}, TP: {tp:.2f})",
        "side": side,
        "volume": vol,
        "price": ref_price,
        "stop_loss": sl,
        "take_profit": tp,
        "timestamp_myt": datetime.now(MYT_TZ).strftime("%Y-%m-%d %H:%M:%S MYT")
    }

@router.get("/workflow")
def get_live_workflow(db: Session = Depends(get_db)):
    """
    Returns real-time workflow telemetry across all institutional agent minds:
    - Macro Director & Alpha Vantage US 10Y Yield
    - Swing Trader (Magic: 1001) state & anti-patterns memory
    - Scalp Trader (Magic: 2002) state, session window & 15m exit cooldown timer
    - Chief Risk Arbiter gatekeeper, shared margin % vs 20% cap, active $3.00 debounce blacklist zones
    - Danger Sentry 180s grace period status & M5 exhaustion indicators
    - Real-time decision stream (hypotheses approvals/rejections)
    """
    import time
    from app.trading.macro_worker import AlphaVantageMacroWorker
    from app.trading.cooldown_manager import MarketCooldownManager
    from app.risk.capital_manager import CapitalManager, AccountTier
    from app.models.strategy import TradingHypothesis

    now_ts = time.time()
    now_utc = datetime.now(timezone.utc)

    # 1. Macro Director & Alpha Vantage Cache
    macro_worker = AlphaVantageMacroWorker()
    macro_state = macro_worker.get_macro_state()
    yield_val = macro_state.get("us_10y_yield", 4.25)
    dxy_val = macro_state.get("dxy_trend", "NEUTRAL_USD")
    allow_buy = macro_state.get("allow_gold_swing_buy", True)
    macro_regime = "BULLISH_MACRO" if allow_buy else "YIELD_SPIKE_VETO"

    # 2. Swing Trader State & Anti-Patterns
    swing_failures = db.query(TradeJournal).filter(
        TradeJournal.magic_number == 1001,
        (TradeJournal.status == "FAIL") | (TradeJournal.realized_pnl < -5.0)
    ).order_by(desc(TradeJournal.closed_at)).limit(3).all()

    swing_anti_patterns = []
    for sf in swing_failures:
        swing_anti_patterns.append({
            "ticket": sf.ticket,
            "side": sf.side,
            "entry_price": sf.entry_price,
            "loss": round(abs(sf.realized_pnl or 0.0), 2),
            "lesson": sf.lessons_learned or "Respect invalidation boundaries and avoid chasing momentum.",
            "why": sf.why_it_went_wrong or "Structural break against position."
        })

    # 3. Scalp Trader State & Anti-Patterns
    is_scalp_cooldown, scalp_rem_sec, scalp_msg = MarketCooldownManager.check_scalper_cooldown()
    scalp_session_active = 7 <= now_utc.hour < 17

    scalp_failures = db.query(TradeJournal).filter(
        TradeJournal.magic_number == 2002,
        (TradeJournal.status == "FAIL") | (TradeJournal.realized_pnl < -5.0)
    ).order_by(desc(TradeJournal.closed_at)).limit(3).all()

    scalp_anti_patterns = []
    for scf in scalp_failures:
        scalp_anti_patterns.append({
            "ticket": scf.ticket,
            "side": scf.side,
            "entry_price": scf.entry_price,
            "loss": round(abs(scf.realized_pnl or 0.0), 2),
            "lesson": scf.lessons_learned or "Avoid choppy consolidation and false breakout sweeps.",
            "why": scf.why_it_went_wrong or "Micro-structure broke down."
        })

    # 4. Chief Risk Arbiter State
    active_zones = MarketCooldownManager.get_active_blacklist_zones()
    account_info = mt5.account_info() if mt5.initialize() else None
    equity = float(account_info.equity) if account_info else 0.0
    tier = AccountTier.get_tier(equity)
    margin_util_pct = round((account_info.margin / equity * 100.0), 1) if (account_info and equity > 0) else 0.0

    # 5. Danger Sentry Active Trackers
    open_positions = mt5.positions_get(symbol="XAUUSD") or [] if mt5.initialize() else []
    sentry_trackers = []
    for pos in open_positions:
        pos_open_time = float(pos.time)
        pos_age_sec = max(0.0, now_ts - pos_open_time)
        in_grace = pos_age_sec < 180.0
        rem_grace = max(0.0, 180.0 - pos_age_sec)
        role = "SWING (1001)" if pos.magic == 1001 else ("SCALP (2002)" if pos.magic == 2002 else f"MAGIC {pos.magic}")

        sentry_trackers.append({
            "ticket": pos.ticket,
            "role": role,
            "side": "BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL",
            "volume": pos.volume,
            "entry_price": pos.price_open,
            "current_price": pos.price_current,
            "profit": round(float(pos.profit), 2),
            "pos_age_sec": round(pos_age_sec, 0),
            "in_grace_period": in_grace,
            "grace_remaining_sec": round(rem_grace, 0),
            "status_text": f"Grace Period Active ({rem_grace:.0f}s left)" if in_grace else "Active Monitoring (Buffered M5 Exhaustion & Invalidation)"
        })

    # 6. Recent Decision Stream (Last 20 hypotheses)
    hypotheses = db.query(TradingHypothesis).order_by(desc(TradingHypothesis.id)).limit(20).all()
    decision_stream = []
    for h in hypotheses:
        # Strict Malaysia Time (UTC+8)
        placed_dt = h.created_at
        if placed_dt and placed_dt.tzinfo is None:
            placed_dt = placed_dt.replace(tzinfo=timezone.utc)
        placed_myt = placed_dt.astimezone(MYT_TZ).strftime("%Y-%m-%d %H:%M:%S MYT") if placed_dt else "N/A"

        fired_myt = placed_myt if h.status in ("APPROVED", "EXECUTED") else "Pending Fill / Gated"

        entry_val = f"${h.entry_price_estimate:.2f}" if h.entry_price_estimate else "Market Zone"
        sl_val = f"${h.suggested_stop_loss:.2f}" if h.suggested_stop_loss else "--"

        # Explicit TP 1 (Cent Scalp +$1.20 / 1:1 R:R) and TP 2 (Structural Expansion)
        tp1_val = "--"
        tp2_val = f"${h.suggested_take_profit:.2f}" if h.suggested_take_profit else "--"
        if h.entry_price_estimate and h.suggested_stop_loss:
            risk_dist = abs(h.entry_price_estimate - h.suggested_stop_loss)
            tp1_dist = min(1.20, risk_dist) if risk_dist > 0 else 1.20
            if h.direction == "BUY":
                tp1_price = round(h.entry_price_estimate + tp1_dist, 2)
            else:
                tp1_price = round(h.entry_price_estimate - tp1_dist, 2)
            tp1_val = f"${tp1_price:.2f}"

        decision_stream.append({
            "id": h.id,
            "strategy": h.strategy_type,
            "direction": h.direction,
            "confidence": f"{round((h.confidence or 0.0) * 100)}%",
            "status": h.status,
            "entry_zone": entry_val,
            "stop_loss": sl_val,
            "tp_1": tp1_val,
            "tp_2": tp2_val,
            "placed_timestamp": placed_myt,
            "fired_timestamp": fired_myt,
            "thesis": h.thesis,
            "notes": h.supporting_evidence,
            "timestamp": placed_myt
        })

    return {
        "timestamp": datetime.now(MYT_TZ).strftime("%Y-%m-%d %H:%M:%S MYT"),
        "macro_director": {
            "regime": macro_regime,
            "yield_10y": yield_val,
            "dxy": dxy_val,
            "allow_long": allow_buy,
            "allow_short": True,
            "source": "Alpha Vantage Real-Time Cache"
        },
        "swing_trader": {
            "magic": 1001,
            "name": "The Institutional Partner",
            "timeframes": "H4 / H1 / M30",
            "status": "Active Institutional Scanner",
            "risk_pct": "1.5%",
            "anti_patterns_loaded": len(swing_anti_patterns),
            "recent_anti_patterns": swing_anti_patterns
        },
        "scalp_trader": {
            "magic": 2002,
            "name": "The Liquid Sniper",
            "timeframes": "M5 / M1",
            "session_window_active": scalp_session_active,
            "session_window_text": "London/NY Volatility Window (07:00-17:00 UTC)" if scalp_session_active else "Asian / Off-Hours (Filtered)",
            "is_cooling_down": is_scalp_cooldown,
            "cooldown_remaining_sec": round(scalp_rem_sec, 0),
            "cooldown_remaining_min": round(scalp_rem_sec / 60.0, 1),
            "cooldown_status": scalp_msg,
            "risk_pct": "0.50%",
            "anti_patterns_loaded": len(scalp_anti_patterns),
            "recent_anti_patterns": scalp_anti_patterns
        },
        "arbiter": {
            "tier_name": tier.tier_name,
            "max_positions": tier.max_concurrent_positions,
            "active_positions_count": len(open_positions),
            "equity": equity,
            "margin_util_pct": margin_util_pct,
            "margin_cap_pct": 20.0,
            "margin_guard_ok": margin_util_pct <= 20.0,
            "active_blacklist_zones": active_zones
        },
        "danger_sentry": {
            "grace_period_rule": "180s (3m) Grace Period on new entries",
            "timeframe_invalidation": "M5 Clean Candle Close (1m Noise Filtered)",
            "exhaustion_buffer_rule": "2 Consecutive M5 Rejection Wicks with Falling Volume",
            "active_trackers": sentry_trackers
        },
        "decision_stream": decision_stream
    }
