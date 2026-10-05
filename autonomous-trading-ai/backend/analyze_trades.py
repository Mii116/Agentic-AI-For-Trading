import MetaTrader5 as mt5
from datetime import datetime, timedelta
import pandas as pd

if mt5.initialize():
    # Positions
    positions = mt5.positions_get()
    print("=== CURRENT OPEN POSITIONS ===")
    if positions:
        for p in positions:
            side = "BUY" if p.type == 0 else "SELL"
            print(f"Ticket #{p.ticket} | {p.symbol} | {side} {p.volume} lots | Entry: {p.price_open} | Current: {p.price_current} | SL: {p.sl} | TP: {p.tp} | Profit: ${p.profit:.2f} | Magic: {p.magic}")
    else:
        print("No open positions.")
    # Deals
    deals = mt5.history_deals_get(datetime.now() - timedelta(days=2), datetime.now() + timedelta(days=1))
    if deals:
        print("\n=== MT5 DEALS (LAST 15) ===")
        for d in list(deals)[-15:]:
            t = datetime.fromtimestamp(d.time)
            side = "BUY" if d.type == 0 else "SELL"
            entry_type = {0: "IN", 1: "OUT", 2: "IN/OUT"}.get(d.entry, str(d.entry))
            print(f"Deal #{d.ticket} | Order #{d.order} | {t} | {side} ({entry_type}) | Vol: {d.volume} | Price: {d.price} | Profit: ${d.profit:.2f} | Magic: {d.magic} | Comment: {d.comment}")

    # Orders
    orders = mt5.history_orders_get(datetime.now() - timedelta(days=2), datetime.now() + timedelta(days=1))
    if orders:
        print("\n=== MT5 ORDERS (LAST 10) ===")
        for o in list(orders)[-10:]:
            t = datetime.fromtimestamp(o.time_setup)
            order_types = {0: "BUY", 1: "SELL", 2: "BUY_LIMIT", 3: "SELL_LIMIT", 4: "BUY_STOP", 5: "SELL_STOP"}
            otype = order_types.get(o.type, str(o.type))
            print(f"Order #{o.ticket} | {t} | {otype} | Vol: {o.volume_initial} | OpenPrice: {o.price_open} | SL: {o.sl} | TP: {o.tp} | Magic: {o.magic} | Comment: {o.comment}")

    # Market context on M1 and M5
    rates_m1 = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_M1, 0, 80)
    rates_m5 = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_M5, 0, 30)
    rates_m15 = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_M15, 0, 20)

    if rates_m1 is not None and len(rates_m1) > 0:
        df_m1 = pd.DataFrame(rates_m1)
        df_m1['datetime'] = pd.to_datetime(df_m1['time'], unit='s')
        print("\n=== M1 CANDLES AROUND RECENT EXECUTION (Last 25) ===")
        for _, r in df_m1.tail(25).iterrows():
            print(f"{r['datetime']} | O: {r['open']:.2f} | H: {r['high']:.2f} | L: {r['low']:.2f} | C: {r['close']:.2f} | Vol: {r['tick_volume']}")

    if rates_m5 is not None and len(rates_m5) > 0:
        df_m5 = pd.DataFrame(rates_m5)
        df_m5['datetime'] = pd.to_datetime(df_m5['time'], unit='s')
        print("\n=== M5 CANDLES (Last 10) ===")
        for _, r in df_m5.tail(10).iterrows():
            print(f"{r['datetime']} | O: {r['open']:.2f} | H: {r['high']:.2f} | L: {r['low']:.2f} | C: {r['close']:.2f} | Vol: {r['tick_volume']}")


    mt5.shutdown()
else:
    print("Failed to initialize MT5")
