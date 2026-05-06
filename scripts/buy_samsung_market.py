"""Place a guarded market buy order for Samsung Electronics.

This script is intentionally opt-in. It sends the order only when --execute is
provided and the current Samsung Electronics price is at or below the configured
max price. The order is sent to the environment selected by KIS_IS_REAL:
simulation when false, real trading when true.
"""
from __future__ import annotations

import argparse
import os
import sys
from pprint import pprint


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from config.settings import kis_config
from kis.client import KISClient
from kis.market import MarketAPI
from order.executor import OrderExecutor, OrderRequest, OrderSide, OrderType


SYMBOL = "005930"
DEFAULT_QUANTITY = 1
DEFAULT_MAX_PRICE = 267_000


def _parse_price(response: dict) -> int:
    output = response.get("output") or {}
    raw_price = output.get("stck_prpr")
    if not raw_price:
        raise RuntimeError(f"Could not read current price from response: {response}")
    return int(raw_price)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Buy 1 share of Samsung Electronics at market price with a safety price cap.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually send the order. Without this flag, only a dry run is printed.",
    )
    parser.add_argument(
        "--quantity",
        type=int,
        default=DEFAULT_QUANTITY,
        help=f"Order quantity. Default: {DEFAULT_QUANTITY}",
    )
    parser.add_argument(
        "--max-price",
        type=int,
        default=DEFAULT_MAX_PRICE,
        help=f"Abort if the current price is above this value. Default: {DEFAULT_MAX_PRICE}",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.quantity <= 0:
        raise ValueError("--quantity must be greater than 0")
    if args.max_price <= 0:
        raise ValueError("--max-price must be greater than 0")

    client = KISClient()
    market = MarketAPI(client)
    executor = OrderExecutor(client)

    current_price = _parse_price(market.get_price(SYMBOL))
    estimated_amount = current_price * args.quantity

    print(f"Account mode: {'real' if kis_config.is_real else 'sim'}")
    print(f"Symbol: {SYMBOL} Samsung Electronics")
    print(f"Order: market buy, {args.quantity} share(s)")
    print(f"Current price: {current_price:,} KRW")
    print(f"Safety max price: {args.max_price:,} KRW")
    print(f"Estimated amount: {estimated_amount:,} KRW")

    if current_price > args.max_price:
        print("ABORTED: current price is above the safety max price.")
        return 2

    request = OrderRequest(
        symbol=SYMBOL,
        side=OrderSide.BUY,
        quantity=args.quantity,
        price=0,
        order_type=OrderType.MARKET,
    )

    if not args.execute:
        print("DRY RUN: add --execute to send this real market order.")
        return 0

    print("Sending order...")
    result = executor.send(request)
    pprint(result)

    if result.get("rt_cd") != "0":
        print(f"ORDER REJECTED: {result.get('msg1', result)}")
        return 4

    print("ORDER ACCEPTED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
