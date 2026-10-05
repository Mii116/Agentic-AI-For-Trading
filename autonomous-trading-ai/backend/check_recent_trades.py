import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.db.session import SessionLocal
from app.models.strategy import TradingHypothesis
from app.models.trading import Order, Position, TradeJournal

db = SessionLocal()

print("="*60)
print("RECENT TRADE JOURNALS (Last 10):")
print("="*60)
journals = db.query(TradeJournal).order_by(TradeJournal.id.desc()).limit(10).all()
if not journals:
    print("No TradeJournal entries found.")
for j in journals:
    print(f"Journal #{j.id} | Ticket: {j.ticket} | {j.side} {j.symbol} @ {j.entry_price} (Exit: {j.exit_price})")
    print(f"Status: {j.status} | PnL: ${j.realized_pnl:.2f} | Technique: {j.technique_used}")
    print(f"Reason: {j.reason}")
    if j.invalidation_condition:
        print(f"Invalidation: {j.invalidation_condition}")
    if j.exit_reason:
        print(f"Exit Reason: {j.exit_reason}")
    if j.why_it_went_wrong:
        print(f"Why it went wrong: {j.why_it_went_wrong}")
    if j.lessons_learned:
        print(f"Lessons learned: {j.lessons_learned}")
    print(f"Opened: {j.opened_at} | Closed: {j.closed_at}")
    print("-" * 50)

print("\n" + "="*60)
print("RECENT TRADING HYPOTHESES (Last 5):")
print("="*60)
hyps = db.query(TradingHypothesis).order_by(TradingHypothesis.id.desc()).limit(5).all()
if not hyps:
    print("No TradingHypothesis entries found.")
for h in hyps:
    print(f"Hypothesis #{h.id} | {h.direction} {h.symbol} | Conf: {h.confidence:.2f} | Status: {h.status} | Time: {h.created_at}")
    print(f"Strategy Type: {h.strategy_type}")
    print(f"Thesis: {h.thesis}")
    print(f"Supporting: {h.supporting_evidence}")
    print(f"Counter: {h.counter_evidence}")
    print(f"Est Entry: {h.entry_price_estimate} | SL: {h.suggested_stop_loss} | TP: {h.suggested_take_profit}")
    print("-" * 50)

print("\n" + "="*60)
print("RECENT ORDERS (Last 10):")
print("="*60)
orders = db.query(Order).order_by(Order.id.desc()).limit(10).all()
if not orders:
    print("No Order entries found.")
for o in orders:
    print(f"Order #{o.id} | {o.side} {o.quantity} {o.symbol} @ {o.price} | Type: {o.order_type} | Status: {o.status} | Mode: {o.mode} | Time: {o.created_at}")

print("\n" + "="*60)
print("RECENT POSITIONS (Last 10):")
print("="*60)
positions = db.query(Position).order_by(Position.id.desc()).limit(10).all()
if not positions:
    print("No Position entries found.")
for p in positions:
    print(f"Pos #{p.id} | {p.side} {p.quantity} {p.symbol} | Entry: {p.entry_price} | Current: {p.current_price} | PnL: ${p.unrealized_pnl:.2f} (Realized: ${p.realized_pnl:.2f}) | Open: {p.is_open} | SL: {p.stop_loss} | TP: {p.take_profit}")

db.close()

# Also check MT5 directly if initialized
try:
    import MetaTrader5 as mt5
    if mt5.initialize():
        print("\n" + "="*60)
        print("MT5 DIRECT POSITIONS:")
        print("="*60)
        mt5_pos = mt5.positions_get()
        if mt5_pos:
            for mp in mt5_pos:
                print(f"Ticket: {mp.ticket} | Symbol: {mp.symbol} | Type: {'BUY' if mp.type == 0 else 'SELL'} | Vol: {mp.volume} | Open Price: {mp.price_open} | Current: {mp.price_current} | SL: {mp.sl} | TP: {mp.tp} | Profit: {mp.profit} | Magic: {mp.magic} | Comment: {mp.comment}")
        else:
            print("No open positions on MT5.")
            
        print("\n" + "="*60)
        print("MT5 RECENT DEALS (Last 24h):")
        print("="*60)
        from datetime import datetime, timedelta
        deals = mt5.history_deals_get(datetime.now() - timedelta(days=2), datetime.now() + timedelta(days=1))
        if deals:
            for d in list(deals)[-10:]:
                print(f"Deal #{d.ticket} | Order #{d.order} | Symbol: {d.symbol} | Type: {d.type} | Entry: {d.entry} | Vol: {d.volume} | Price: {d.price} | Profit: {d.profit} | Magic: {d.magic} | Comment: {d.comment} | Time: {datetime.fromtimestamp(d.time)}")
        else:
            print("No deals found in history.")
        mt5.shutdown()
except Exception as e:
    print(f"MT5 check error: {e}")
