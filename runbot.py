import asyncio
import os
import time
import argparse
from dotenv import load_dotenv
from lighter_sdk.lighter import Lighter  # 正确 import

load_dotenv()

def get_position(lighter, ticker):
    """获取仓位：从 account()['positions']。"""
    account = asyncio.run(lighter.account())
    for pos in account.get('positions', []):
        if pos.get('symbol') == ticker:
            return pos
    return None

async def place_sl_tp(lighter, ticker, entry_price, close_side, take_profit_pct, stop_loss_pct):
    """放置 TP (limit) 和 SL (stop limit placeholder)。"""
    pos = get_position(lighter, ticker)
    if not pos:
        return
    close_amount = abs(pos['size'])

    # TP: limit order
    tp_multiplier = 1 + take_profit_pct if close_side == 'sell' else 1 - take_profit_pct
    tp_price = entry_price * tp_multiplier
    await lighter.limit_order(
        ticker=ticker,
        amount= -close_amount if close_side == 'sell' else close_amount,
        price=tp_price
    )
    print(f"Placed TP {close_side} limit at {tp_price}")

    # SL: 用 limit_order 模拟 stop (SDK 可能无专用 stop；实际调为 trigger_price 如果支持)
    sl_multiplier = 1 + stop_loss_pct if close_side == 'sell' else 1 - stop_loss_pct
    sl_price = entry_price * sl_multiplier
    await lighter.limit_order(
        ticker=ticker,
        amount= -close_amount if close_side == 'sell' else close_amount,
        price=sl_price
    )
    print(f"Placed SL {close_side} limit at {sl_price} (simulate stop)")

async def close_position(lighter, ticker, close_side):
    """市价平仓。"""
    pos = get_position(lighter, ticker)
    if not pos or pos['size'] == 0:
        return
    amount = -pos['size'] if close_side == 'sell' else pos['size']
    res = await lighter.market_order(ticker=ticker, amount=amount)
    print(f"Closed position with market {close_side}")

async def main():
    parser = argparse.ArgumentParser(description="Lighter Trading Bot")
    parser.add_argument('--exchange', default='lighter')
    parser.add_argument('--ticker', default='BTC')
    parser.add_argument('--quantity', type=float, default=0.00045)
    parser.add_argument('--take-profit', type=float, default=0.03)
    parser.add_argument('--stop-price', type=float, default=-0.03)
    parser.add_argument('--direction', default='buy', choices=['buy', 'sell'])
    parser.add_argument('--max-orders', type=int, default=1)
    parser.add_argument('--wait-time', type=int, default=20)
    parser.add_argument('--env-file', default='.env')
    parser.add_argument('--grid-step', type=float, default=0.01)
    args = parser.parse_args()

    load_dotenv(args.env_file)
    key = os.getenv('LIGHTER_KEY')
    secret = os.getenv('LIGHTER_SECRET')
    if not key or not secret:
        raise ValueError("Set LIGHTER_KEY and LIGHTER_SECRET in .env")

    lighter = Lighter(key=key, secret=secret)
    await lighter.init_client()

    ticker = args.ticker
    quantity = args.quantity
    direction = args.direction
    close_side = 'sell' if direction == 'buy' else 'buy'
    open_amount = quantity if direction == 'buy' else -quantity

    start_time = time.time()
    filled = False
    entry_price = 0

    while not filled and (time.time() - start_time < 1800):
        print(f"Placing {direction} market order...")
        res = await lighter.market_order(ticker=ticker, amount=open_amount)
        if 'orders' in res and res['orders']:
            order_id = res['orders'][0]['order_id']

            await asyncio.sleep(1)  # 1s 检查

            active_orders = await lighter.account_active_orders(ticker=ticker)
            order_still_active = any(o.get('order_id') == order_id for o in active_orders)

            if not order_still_active:
                print("Order filled!")
                filled = True
                pos = get_position(lighter, ticker)
                entry_price = pos.get('avgEntryPrice', 0) if pos else 0
                if entry_price == 0:
                    print("Warning: Use manual entry price if needed")
            else:
                print("Not filled in 1s, canceling...")
                await lighter.cancel_order(ticker=ticker, order_id=order_id)

        if not filled:
            await asyncio.sleep(args.wait_time)

    if not filled:
        print("Timeout: No open")
        return

    print(f"Opened at {entry_price}")

    await place_sl_tp(lighter, ticker, entry_price, close_side, args.take_profit, args.stop_price)

    hold_start = time.time()
    while time.time() - hold_start < 300:
        pos = get_position(lighter, ticker)
        if not pos or abs(pos.get('size', 0)) < 0.00001:
            print("Closed by TP/SL")
            return
        await asyncio.sleep(5)

    print("5min over, closing...")
    await close_position(lighter, ticker, close_side)

    print("Complete!")

if __name__ == "__main__":
    asyncio.run(main())
