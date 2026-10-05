import json
from datetime import datetime

with open('trades_dump.json', 'r') as f:
    trades = json.load(f)

# Sort trades: Open trades first, then closed trades by close_time descending
open_trades = [t for t in trades if t['status'] == 'OPEN']
closed_trades = [t for t in trades if t['status'] == 'CLOSED']
closed_trades.sort(key=lambda x: x['close_time'], reverse=True)

all_sorted = open_trades + closed_trades

total_trades = len(trades)
total_closed = len(closed_trades)
total_open = len(open_trades)

wins = [t for t in closed_trades if t['pnl'] > 0]
losses = [t for t in closed_trades if t['pnl'] < 0]
breakevens = [t for t in closed_trades if t['pnl'] == 0]

gross_profit = sum(t['pnl'] for t in wins)
gross_loss = sum(t['pnl'] for t in losses)
net_pnl = sum(t['pnl'] for t in closed_trades)
win_rate = (len(wins) / total_closed * 100) if total_closed > 0 else 0.0
profit_factor = (gross_profit / abs(gross_loss)) if gross_loss != 0 else float('inf')

md = []
md.append("# 📋 Complete Audit of Today's Trading Activity (XAUUSD)\n")
md.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | **Source:** MT5 Broker Live Deal History\n")
md.append("---\n")

md.append("## 📊 Executive Performance Summary\n")
md.append("| Metric | Value |")
md.append("| :--- | :--- |")
md.append(f"| **Total Account Trades Recorded** | **{total_trades}** |")
md.append(f"| **Closed Positions** | **{total_closed}** |")
md.append(f"| **Open Positions Currently Active** | **{total_open}** |")
md.append(f"| **Winning Trades** | **{len(wins)}** ({win_rate:.1f}%) |")
md.append(f"| **Losing Trades** | **{len(losses)}** ({len(losses)/total_closed*100:.1f}%) |")
md.append(f"| **Breakeven / Scratch** | **{len(breakevens)}** |")
md.append(f"| **Gross Realized Profit** | <span style='color:green'>**+${gross_profit:,.2f}**</span> |")
md.append(f"| **Gross Realized Loss** | <span style='color:red'>**-${abs(gross_loss):,.2f}**</span> |")
md.append(f"| **Net Realized PnL** | **+${net_pnl:,.2f}** |")
md.append(f"| **Profit Factor** | **{profit_factor:.2f}** |")
md.append("\n---\n")

md.append("## 🔍 Active Open Positions\n")
if open_trades:
    md.append("| Ticket | Strategy | Side | Lots | Open Time | Entry Price | Setup / Comment |")
    md.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
    for t in open_trades:
        md.append(f"| `{t['pos_id']}` | {t['strategy']} | **{t['side']}** | {t['volume']} | {t['open_time']} | ${t['open_price']:.2f} | {t['comment']} |")
else:
    md.append("No positions currently open.\n")

md.append("\n---\n")

md.append("## 📜 Full Master Table of All Closed Trades (Chronological - Newest First)\n")
md.append("| # | Ticket | Strategy | Side | Lots | Open Time | Entry ($) | Close Time | Exit ($) | Net PnL ($) | Outcome | Comment / Setup |")
md.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |")

for idx, t in enumerate(closed_trades, 1):
    pnl = t['pnl']
    if pnl > 0:
        outcome = "🟢 WIN"
        pnl_str = f"+${pnl:.2f}"
    elif pnl < 0:
        outcome = "🔴 LOSS"
        pnl_str = f"-${abs(pnl):.2f}"
    else:
        outcome = "⚪ SCRATCH"
        pnl_str = "$0.00"

    strat_short = "Scalp" if "2002" in t['strategy'] else "Swing"
    clean_comment = t['comment'].replace('|', '-').strip()
    md.append(
        f"| {idx} | `{t['pos_id']}` | {strat_short} | {t['side']} | {t['volume']} | "
        f"{t['open_time']} | {t['open_price']:.2f} | {t['close_time']} | {t['close_price']:.2f} | "
        f"**{pnl_str}** | {outcome} | {clean_comment} |"
    )

artifact_path = r"C:\Users\syahm\.gemini\antigravity-ide\brain\b1d9fc24-f5a2-4363-a1bc-c40588a8fb79\today_trades_audit.md"
with open(artifact_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(md))

print(f"Artifact written to {artifact_path}")
