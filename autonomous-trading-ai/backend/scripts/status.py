import os
import sys
import MetaTrader5 as mt5

def main():
    terminal_path = r"C:\Program Files\MetaTrader 5\terminal64.exe"
    if not mt5.initialize(path=terminal_path):
        print(f"Failed to initialize MT5: {mt5.last_error()}")
        return

    acc = mt5.account_info()
    if not acc:
        print("No active account.")
        return

    print("================ MT5 ACCOUNT STATUS ================")
    print(f"Login: {acc.login} | Server: {acc.server}")
    print(f"Balance: ${acc.balance:,.2f} | Equity: ${acc.equity:,.2f}")
    print(f"Margin: ${acc.margin:,.2f} | Free Margin: ${acc.margin_free:,.2f}")
    print("================== OPEN POSITIONS ==================")

    positions = mt5.positions_get()
    if positions:
        for p in positions:
            side = "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL"
            print(f"Ticket #{p.ticket}: {p.symbol} {side} {p.volume:.2f} lots")
            print(f"   Entry: {p.price_open:.2f} -> Current: {p.price_current:.2f}")
            print(f"   SL: {p.sl} | TP: {p.tp} | Unrealized Profit: ${p.profit:+.2f}")
            print(f"   Comment: {p.comment}")
    else:
        print("No open positions currently.")

    print("====================================================")
    mt5.shutdown()

if __name__ == "__main__":
    main()
