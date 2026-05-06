"""Buy one NVDA share, then sell one share for an overseas-order smoke test."""
from __future__ import annotations

import argparse
from datetime import datetime, time as dt_time
from pathlib import Path
import sys
import time


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config.settings import kis_config
from kis.client import KISClient
from kis.overseas import OverseasMarketAPI, parse_overseas_price
from order.executor import OrderSide
from order.overseas_executor import OverseasOrderExecutor, OverseasOrderRequest
from utils.trade_log import log_order


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NVDA buy/sell round-trip smoke test.")
    parser.add_argument("--execute", action="store_true", help="Actually send orders.")
    parser.add_argument("--wait-for-us-open", action="store_true", help="Wait until 22:30 KST before starting.")
    parser.add_argument("--buy-slippage-pct", type=float, default=1.0, help="Buy limit above current price.")
    parser.add_argument("--sell-slippage-pct", type=float, default=1.0, help="Sell limit below current price.")
    parser.add_argument("--sell-delay-sec", type=int, default=20, help="Seconds to wait before selling.")
    parser.add_argument("--allow-real", action="store_true", help="Allow real-account order when KIS_IS_REAL=true.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.wait_for_us_open:
        wait_until_time("22:30")

    if kis_config.is_real and not args.allow_real:
        print("ABORTED: KIS_IS_REAL=true. Add --allow-real only if this is intentional.")
        return 3

    client = KISClient()
    market = OverseasMarketAPI(client)
    executor = OverseasOrderExecutor(client)

    buy_price = round(parse_overseas_price(market.get_price("NVDA", "NASD")) * (1 + args.buy_slippage_pct / 100), 2)
    buy_order = OverseasOrderRequest("NVDA", "NASD", OrderSide.BUY, 1, buy_price)

    print(f"Account mode: {'real' if kis_config.is_real else 'sim'}")
    print(f"BUY NASD:NVDA qty=1 limit={buy_price:.2f}")
    if not args.execute:
        print("DRY RUN: add --execute to send buy and sell orders.")
        return 0

    buy_result = executor.send(buy_order)
    log_order("us", _order_log_dict(buy_order), buy_result)
    print(f"BUY result: {buy_result}")
    if buy_result.get("rt_cd") != "0":
        print("BUY was not accepted; skipping sell.")
        return 1

    print(f"Waiting {args.sell_delay_sec}s before sell...")
    time.sleep(args.sell_delay_sec)

    sell_price = round(parse_overseas_price(market.get_price("NVDA", "NASD")) * (1 - args.sell_slippage_pct / 100), 2)
    sell_order = OverseasOrderRequest("NVDA", "NASD", OrderSide.SELL, 1, sell_price)
    print(f"SELL NASD:NVDA qty=1 limit={sell_price:.2f}")
    sell_result = executor.send(sell_order)
    log_order("us", _order_log_dict(sell_order), sell_result)
    print(f"SELL result: {sell_result}")
    return 0 if sell_result.get("rt_cd") == "0" else 1


def _order_log_dict(order: OverseasOrderRequest) -> dict:
    return {
        "symbol": order.symbol,
        "exchange": order.exchange,
        "side": order.side.value,
        "quantity": order.quantity,
        "limit_price": order.limit_price,
        "order_type": order.order_type,
    }


def wait_until_time(value: str) -> None:
    hour, minute = value.split(":", maxsplit=1)
    now = datetime.now()
    target = datetime.combine(now.date(), dt_time(hour=int(hour), minute=int(minute)))
    if target <= now:
        return
    print(f"Waiting until {target.strftime('%Y-%m-%d %H:%M:%S')} before starting NVDA round trip.")
    time.sleep((target - now).total_seconds())


if __name__ == "__main__":
    raise SystemExit(main())
