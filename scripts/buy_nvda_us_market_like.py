"""Buy one NVIDIA share with a market-like overseas limit order."""
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
    parser = argparse.ArgumentParser(description="Buy 1 NVDA share using a market-like limit order.")
    parser.add_argument("--execute", action="store_true", help="Actually send the order.")
    parser.add_argument("--wait-for-us-open", action="store_true", help="Wait until 22:30 KST before sending.")
    parser.add_argument("--slippage-pct", type=float, default=1.0, help="Limit above current price. Default: 1.0")
    parser.add_argument("--allow-real", action="store_true", help="Allow real-account order when KIS_IS_REAL=true.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.wait_for_us_open:
        wait_until_time("22:30")

    if kis_config.is_real and not args.allow_real:
        print("ABORTED: KIS_IS_REAL=true. Add --allow-real only if you intentionally want real trading.")
        return 3

    client = KISClient()
    market = OverseasMarketAPI(client)
    price = parse_overseas_price(market.get_price("NVDA", "NASD"))
    limit_price = round(price * (1 + args.slippage_pct / 100), 2)

    print(f"Account mode: {'real' if kis_config.is_real else 'sim'}")
    print(f"Order: NASD:NVDA BUY qty=1")
    print(f"Current price: {price:.2f} USD")
    print(f"Market-like limit: {limit_price:.2f} USD")

    if not args.execute:
        print("DRY RUN: add --execute to send this order.")
        return 0

    order = OverseasOrderRequest(
        symbol="NVDA",
        exchange="NASD",
        side=OrderSide.BUY,
        quantity=1,
        limit_price=limit_price,
    )
    result = OverseasOrderExecutor(client).send(order)
    log_order(
        "us",
        {
            "symbol": order.symbol,
            "exchange": order.exchange,
            "side": order.side.value,
            "quantity": order.quantity,
            "limit_price": order.limit_price,
            "order_type": order.order_type,
        },
        result,
    )
    print(result)
    return 0 if result.get("rt_cd") == "0" else 1


def wait_until_time(value: str) -> None:
    hour, minute = value.split(":", maxsplit=1)
    now = datetime.now()
    target = datetime.combine(now.date(), dt_time(hour=int(hour), minute=int(minute)))
    if target <= now:
        return
    seconds = (target - now).total_seconds()
    print(f"Waiting until {target.strftime('%Y-%m-%d %H:%M:%S')} before sending NVDA order.")
    time.sleep(seconds)


if __name__ == "__main__":
    raise SystemExit(main())
