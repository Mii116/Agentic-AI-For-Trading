import MetaTrader5 as mt5
from datetime import datetime, timedelta
import pandas as pd
import json

terminal_path = r'C:\Program Files\MetaTrader 5\terminal64.exe'
if not mt5.initialize(path=terminal_path):
    print('Failed to initialize MT5:', mt5.last_error())
    exit(1)

tick = mt5.symbol_info_tick('XAUUSD')
server_time = datetime.fromtimestamp(tick.time) if tick else datetime.now()
print(f"MT5 Connected. Server Time: {server_time}")

# We query history deals covering the last 2 days
from_date = datetime.now() - timedelta(days=2)
to_date = datetime.now() + timedelta(days=1)
deals = mt5.history_deals_get(from_date, to_date)
print(f"Total deals retrieved: {len(deals) if deals else 0}")

if not deals:
    print("No deals found.")
    exit(0)

positions_dict = {}
for d in deals:
    pos_id = d.position_id
    if pos_id <= 0:
        continue
    if pos_id not in positions_dict:
        positions_dict[pos_id] = {'in': [], 'out': []}
    if d.entry == mt5.DEAL_ENTRY_IN:
        positions_dict[pos_id]['in'].append(d)
    elif d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_INOUT):
        positions_dict[pos_id]['out'].append(d)

trade_records = []
for pos_id, parts in positions_dict.items():
    ins = parts['in']
    outs = parts['out']
    
    if ins:
        first_in = ins[0]
        open_time = datetime.fromtimestamp(first_in.time).strftime('%Y-%m-%d %H:%M:%S')
        side = 'BUY' if first_in.type == mt5.ORDER_TYPE_BUY else 'SELL'
        open_price = float(first_in.price)
        magic = int(first_in.magic)
        comment = str(first_in.comment or '')
        in_vol = round(sum(d.volume for d in ins), 2)
    else:
        open_time = 'N/A'
        side = 'UNKNOWN'
        open_price = 0.0
        magic = 0
        comment = ''
        in_vol = 0.0

    if outs:
        last_out = outs[-1]
        close_time = datetime.fromtimestamp(last_out.time).strftime('%Y-%m-%d %H:%M:%S')
        close_price = float(last_out.price)
        total_pnl = round(sum(d.profit for d in outs), 2)
        out_vol = round(sum(d.volume for d in outs), 2)
        status = 'CLOSED'
        out_comment = str(last_out.comment or '')
    else:
        close_time = 'OPEN'
        close_price = 0.0
        total_pnl = 0.0
        out_vol = 0.0
        status = 'OPEN'
        out_comment = ''

    trade_records.append({
        'pos_id': pos_id,
        'magic': magic,
        'strategy': 'SWING (1001)' if magic == 1001 else ('SCALP (2002)' if magic == 2002 else f'MAGIC {magic}'),
        'side': side,
        'volume': in_vol or out_vol,
        'open_time': open_time,
        'open_price': open_price,
        'close_time': close_time,
        'close_price': close_price,
        'pnl': total_pnl,
        'status': status,
        'comment': comment or out_comment
    })

df = pd.DataFrame(trade_records)

# Also check currently open positions directly
open_positions = mt5.positions_get(symbol='XAUUSD') or []
open_pos_ids = {p.ticket for p in open_positions}
for r in trade_records:
    if r['pos_id'] in open_pos_ids:
        r['status'] = 'OPEN'

with open('trades_dump.json', 'w') as f:
    json.dump(trade_records, f, indent=2)

print(f"Dumped {len(trade_records)} trades to trades_dump.json.")

# Print summary
closed = [t for t in trade_records if t['status'] == 'CLOSED']
opened = [t for t in trade_records if t['status'] == 'OPEN']
print(f"Summary: {len(closed)} CLOSED trades, {len(opened)} OPEN trades.")
total_pnl = sum(t['pnl'] for t in closed)
wins = [t for t in closed if t['pnl'] > 0]
losses = [t for t in closed if t['pnl'] < 0]
print(f"Total Net PnL of Closed Trades: ${total_pnl:.2f}")
print(f"Wins: {len(wins)} | Losses: {len(losses)}")
