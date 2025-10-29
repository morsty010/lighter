import asyncio
import os
import time
import argparse
from dotenv import load_dotenv
import lighter  # 正确导入

load_dotenv()

def get_position(client, ticker):
    """获取仓位：用 AccountApi.get_account，假设返回 positions list。"""
    account_api = lighter.AccountApi(client)
    # 假设你的账户 index=1，调整为你的（从 get_info.py 查）
    account = asyncio.run(account_api.get_account(by="index", value="1"))
    for pos in account.get('positions', []):
        if pos.get('symbol') == ticker.upper():  # BTC -> BTC-USD? 调整 symbol
            return pos
    return None

async def place_sl_tp(client, ticker, entry_price, close_side, take_profit_pct, stop_loss_pct):
    """放置 TP (limit) 和 SL (stop market)，reduce_only=True。"""
    order_api = lighter.OrderApi(client)
    pos = get_position(client, ticker)
    if not pos:
        return
    close_size = abs(pos['size'])  # full close

    # TP: limit order
    side = 'SELL' if close_side == 'sell' else 'BUY'
    tp_price = entry_price * (1 + take_profit_pct if side == 'SELL' else 1 - take_profit_pct)
    # 从 examples/create_cancel_order.py 复制真实调用，调整 params
    tp_order = await order_api.create_order(
        symbol=ticker,
        side=side,
        size=close_size,
        price=tp_price,
        type='limit',
        reduce_only=True
    )
    print(f"Placed TP {close_side} limit at {tp_price}, order: {tp_order}")

    # SL: stop market
    sl_trigger = entry_price * (1 + stop_loss_pct if side == 'SELL' else 1 - stop_loss_pct)
    sl_order = await order_api.create_order(
        symbol=ticker,
        side=side,
        size=close_size,
        trigger_price=sl_trigger,  # 或 stop_price
        type='stop_market',
        reduce_only=True
    )
    print(f"Placed SL {close_side} stop at {sl_trigger}, order: {sl_order}")

async def close_position(client, ticker, close_side):
    """市价平仓。"""
    order_api = lighter.OrderApi(client)
    pos = get_position(client, ticker)
    if not pos or pos['size'] == 0:
        return
    side = 'SELL' if close_side == 'sell' else 'BUY'
    size = abs(pos['size'])
    res = await order_api.create_order(
        symbol=ticker,
        side=side,
        size=size,
        type='market',
        reduce_only=True
    )
    print(f"Closed with market {close_side}, res: {res}")

async def main():
    parser = argparse.ArgumentParser(description="Lighter Trading Bot")
    parser.add_argument('--exchange', default='lighter')
    parser.add_argument('--ticker', default='BTC')
    parser.add_argument('--quantity', type=float, default=0.00045)
    parser.add_argument('--take-profit', type=float, default=0.03)
    parser.add_argument('--stop-price', type=float, default=-0.03)  # 用这个！
    parser.add_argument('--direction', default='buy', choices=['buy', 'sell'])
    parser.add_argument('--max-orders', type=int, default=1)
    parser.add_argument('--wait-time', type=int, default=20)
    parser.add_argument('--env-file', default='.env')
    parser.add_argument('--grid-step', type=float, default=0.01)
    args = parser.parse_args()

    load_dotenv(args.env_file)
    private_key = os.getenv('PRIVATE_KEY') or os.getenv('LIGHTER_API_SECRET')
    if not private_key:
        raise ValueError("Set PRIVATE_KEY or LIGHTER_API_SECRET in .env (your wallet private key)")

    # 初始化（从 examples 风格）
    client = lighter.ApiClient(private_key=private_key)
    # 如果需要 RPC URL： client = lighter.ApiClient(private_key=private_key, rpc_url='your_starknet_rpc')

    ticker = f"{args.ticker}-USD"  # Lighter symbol 通常是 BTC-USD，确认 docs
    quantity = args.quantity
    direction = args.direction
    close_side = 'sell' if direction == 'buy' else 'buy'
    open_side = 'BUY' if direction == 'buy' else 'SELL'
    open_size = quantity

    order_api = lighter.OrderApi(client)
    start_time = time.time()
    filled = False
    entry_price = 0

    while not filled and (time.time() - start_time < 1800):
        print(f"Placing {direction} market order for {open_size} {ticker}...")
        # 真实 market order 调用（从 examples 复制 params）
        res = await order_api.create_order(
            symbol=ticker,
            side=open_side,
            size=open_size,
            type='market'
        )
        if 'order_id' in res:  # 假设返回 dict with order_id
            order_id = res['order_id']
            print(f"Order placed: {order_id}")

            await asyncio.sleep(1)  # 1s 检查

            # 检查活跃订单（假设 get_open_orders 方法）
            active_orders = await order_api.get_open_orders(symbol=ticker)
            order_still_active = any(o.get('order_id') == order_id for o in active_orders or [])

            if not order_still_active:
                print("Order filled in 1s!")
                filled = True
                pos = get_position(client, ticker)
                entry_price = pos.get('avgEntryPrice', 0) if pos else 0
                if entry_price == 0:
                    # Fallback: 从 fills 或 ticker price
                    print("Warning: Entry price not fetched, set manually if needed")
            else:
                print("Not filled, canceling...")
                await order_api.cancel_order(order_id=order_id, symbol=ticker)

        if not filled:
            await asyncio.sleep(args.wait_time)

    if not filled:
        print("Timeout: No position opened")
        return

    print(f"Opened at ~{entry_price}")

    # TP/SL
    await place_sl_tp(client, ticker, entry_price, close_side, args.take_profit, args.stop_price)

    # 持仓 5min
    hold_start = time.time()
    while time.time() - hold_start < 300:
        pos = get_position(client, ticker)
        if not pos or abs(pos.get('size', 0)) < 0.00001:  # ~0
            print("Position closed early (TP/SL?)")
            return
        await asyncio.sleep(5)
        print("Holding...")

    await close_position(client, ticker, close_side)
    print("Done!")

if __name__ == "__main__":
    asyncio.run(main())
