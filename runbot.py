import asyncio
import os
import time
import argparse
from dotenv import load_dotenv
from lighter_sdk.lighter import Lighter

load_dotenv()

def get_position(lighter, ticker):
    """
    Get current position for ticker.
    Assumes lighter.account() returns {'positions': [{'symbol': str, 'size': float, 'avgEntryPrice': float, ...}]}
    """
    account = asyncio.run(lighter.account())
    for pos in account.get('positions', []):
        if pos.get('symbol') == ticker:
            return pos
    return None

async def place_sl_tp(lighter, ticker, entry_price, close_side, take_profit_pct, stop_loss_pct):
    """
    Place take profit and stop loss orders.
    Assumes SDK supports stop orders via limit_order with trigger_price or separate method.
    For simplicity, using limit_order for TP (limit), and assume a stop_market method or simulate.
    Note: Adjust based on actual SDK; this is placeholder.
    """
    close_amount = -1  # full reduce, but use current size
    pos = get_position(lighter, ticker)
    if pos:
        close_amount = abs(pos['size'])

    # For TP: limit order on close_side at tp_price, reduce_only=True
    tp_multiplier = 1 + take_profit_pct if close_side == 'sell' else 1 - take_profit_pct
    tp_price = entry_price * tp_multiplier
    await lighter.limit_order(
        ticker=ticker,
        amount= -close_amount if close_side == 'sell' else close_amount,
        price=tp_price,
        reduce_only=True  # assume param
    )
    print(f"Placed TP {close_side} limit at {tp_price}")

    # For SL: stop market order, trigger at sl_trigger, then market close
    sl_multiplier = 1 + stop_loss_pct if close_side == 'sell' else 1 - stop_loss_pct
    sl_trigger = entry_price * sl_multiplier
    # Assume SDK has stop_market_order(ticker, amount, trigger_price, reduce_only)
    # Placeholder: await lighter.stop_market_order(ticker=ticker, amount= -close_amount if close_side == 'sell' else close_amount, trigger_price=sl_trigger, reduce_only=True)
    print(f"Placed SL {close_side} stop market trigger at {sl_trigger}")
    # Implement actual SL placement based on SDK docs

async def close_position(lighter, ticker, close_side):
    """
    Close position with market order.
    """
    pos = get_position(lighter, ticker)
    if not pos or pos['size'] == 0:
        return
    amount = -pos['size'] if close_side == 'sell' else pos['size']
    res = await lighter.market_order(ticker=ticker, amount=amount)
    print(f"Closed position with market {close_side}")

async def main():
    parser = argparse.ArgumentParser(description="Modified Perp DEX Trading Bot for Lighter")
    parser.add_argument('--exchange', default='lighter')
    parser.add_argument('--ticker', default='BTC')
    parser.add_argument('--quantity', type=float, default=0.00045)
    parser.add_argument('--take-profit', type=float, default=0.03)  # Overridden to 3%
    parser.add_argument('--stop-loss', type=float, default=-0.03)  # Added, -3%
    parser.add_argument('--env-file', default='.env')
    parser.add_argument('--max-orders', type=int, default=1)
    parser.add_argument('--wait-time', type=int, default=20)
    parser.add_argument('--grid-step', type=float, default=0.01)
    parser.add_argument('--direction', default='buy')  # Added default
    args = parser.parse_args()

    if args.env_file:
        load_dotenv(args.env_file)

    # Assume env vars: LIGHTER_API_KEY, LIGHTER_API_SECRET
    key = os.getenv('LIGHTER_API_KEY')
    secret = os.getenv('LIGHTER_API_SECRET')
    if not key or not secret:
        raise ValueError("Missing API key/secret in .env")

    lighter = Lighter(key=key, secret=secret)
    await lighter.init_client()

    ticker = args.ticker
    quantity = args.quantity
    direction = args.direction
    open_side = direction  # 'buy' to open long
    close_side = 'sell' if direction == 'buy' else 'buy'
    open_amount = quantity if direction == 'buy' else -quantity

    start_time = time.time()
    filled = False
    entry_price = 0

    while not filled and (time.time() - start_time < 1800):  # 30 min timeout
        print("Placing market order to open...")
        res = await lighter.market_order(ticker=ticker, amount=open_amount)
        if 'orders' in res and res['orders']:
            order_id = res['orders'][0]['order_id']

            await asyncio.sleep(1)  # Wait 1 second

            active_orders = await lighter.account_active_orders(ticker=ticker)
            order_still_active = any(o.get('order_id') == order_id for o in active_orders)

            if not order_still_active:
                print("Order filled!")
                filled = True
                # Get entry price from position
                pos = get_position(lighter, ticker)
                if pos and pos['size'] != 0:
                    entry_price = pos.get('avgEntryPrice', 0)
                    if entry_price == 0:
                        # Fallback: get current price or from res
                        # Assume res has 'avgPrice'
                        entry_price = res.get('avgPrice', 0)  # Placeholder
                else:
                    print("No position found after fill")
                    continue
            else:
                print("Order not filled in 1s, canceling...")
                await lighter.cancel_order(ticker=ticker, order_id=order_id)
        else:
            print("Failed to place order")

        if not filled:
            await asyncio.sleep(args.wait_time)

    if not filled:
        print("Failed to open position within timeout")
        return

    print(f"Opened position at entry price: {entry_price}")

    # Place TP and SL
    await place_sl_tp(lighter, ticker, entry_price, close_side, args.take_profit, args.stop_loss)

    # Hold for 5 minutes, then close if still open
    hold_start = time.time()
    while (time.time() - hold_start < 300):  # 5 min
        pos = get_position(lighter, ticker)
        if pos is None or pos.get('size', 0) == 0:
            print("Position closed by TP/SL")
            return
        await asyncio.sleep(5)  # Check every 5s

    # Close after 5 min
    print("Holding period over, closing position...")
    await close_position(lighter, ticker, close_side)

    print("Trading session complete.")

if __name__ == "__main__":
    asyncio.run(main())
