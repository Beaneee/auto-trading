"""Run the legacy rulebook strategy for US stocks.

This script is separate from the Korean-market runner so the morning KRX setup
stays untouched. Default behavior is a dry run; add --execute to send overseas
orders to the KIS environment selected by KIS_IS_REAL.
"""
from __future__ import annotations

import argparse
import copy
import csv
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
import sys
import time


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config.settings import kis_config
from kis.client import KISClient
from kis.overseas import OverseasMarketAPI
from order.overseas_executor import OverseasOrderExecutor
from order.overseas_portfolio import OverseasPortfolio
from strategy.overseas_rulebook import OverseasRulebookStrategy
from utils.trade_log import log_order


DEFAULT_WATCHLIST = ROOT_DIR / "data" / "watchlists" / "us_rulebook_focus.csv"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the legacy rulebook strategy for US stocks.")
    parser.add_argument("--execute", action="store_true", help="Actually send generated overseas orders.")
    parser.add_argument("--watchlist", default=str(DEFAULT_WATCHLIST), help="CSV with exchange,symbol columns.")
    parser.add_argument("--symbols", nargs="*", help="Optional symbols. Example: --symbols NVDA MSFT AAPL")
    parser.add_argument("--exchange", default="NASD", help="Default exchange for --symbols. NASD/NYSE/AMEX.")
    parser.add_argument("--limit", type=int, default=50, help="Maximum watchlist rows to load. Default: 50")
    parser.add_argument("--batch-size", type=int, default=6, help="Symbols per rotating batch. Default: 6")
    parser.add_argument("--batch-offset", type=int, default=0, help="Internal rotating batch offset.")
    parser.add_argument("--cycle-min", type=float, default=15, help="Reset batches every N minutes. Default: 15")
    parser.add_argument("--repeat-interval-min", type=float, default=0.1, help="Repeat interval while --until is set.")
    parser.add_argument("--until", help="Repeat until local time HH:MM.")
    parser.add_argument("--until-us-close", action="store_true", help="Repeat until 05:00 KST, near US regular-market close during US daylight time.")
    parser.add_argument("--wait-for-us-open", action="store_true", help="Wait until 22:30 KST before starting.")
    parser.add_argument("--report-on-close", action="store_true", help="Generate a local daily report after the loop stops.")
    parser.add_argument("--market-guard", action="store_true", help="Block entries and use guarded stop loss.")
    parser.add_argument("--buy-slippage-pct", type=float, default=0.7, help="Buy limit above current price. Default: 0.7")
    parser.add_argument("--sell-slippage-pct", type=float, default=0.7, help="Sell limit below current price. Default: 0.7")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.wait_for_us_open:
        wait_until_time("22:30")
    if args.until_us_close:
        args.until = "05:00"
    if args.until:
        return repeat_until(args)
    return run_once(args)


def repeat_until(args: argparse.Namespace) -> int:
    until_at = _parse_until(args.until)
    print(f"Repeating US runner until: {until_at.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Repeat interval: {args.repeat_interval_min:g} minute(s)")
    print(f"Batch cycle reset: {args.cycle_min:g} minute(s)")
    print()

    exit_code = 0
    run_count = 0
    cycle_started_at = datetime.now()
    while datetime.now() < until_at:
        if args.cycle_min and (datetime.now() - cycle_started_at).total_seconds() >= args.cycle_min * 60:
            cycle_started_at = datetime.now()
            run_count = 0
            print("\nUS batch cycle reset: returning to first symbols.\n")

        started_at = datetime.now()
        print("=" * 72)
        print(f"US run started: {started_at.strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 72)

        run_args = copy.copy(args)
        run_args.until = None
        run_args.batch_offset = run_count
        try:
            exit_code = max(exit_code, run_once(run_args))
        except Exception as exc:
            exit_code = 1
            print(f"RUN FAILED: {exc}")

        run_count += 1
        next_at = started_at.timestamp() + (args.repeat_interval_min * 60)
        sleep_seconds = min(max(0, next_at - time.time()), max(0, until_at.timestamp() - time.time()))
        if sleep_seconds <= 0:
            continue
        print(f"\nNext US run in {sleep_seconds / 60:.1f} minute(s).\n")
        time.sleep(sleep_seconds)

    print(f"Stopped at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if args.report_on_close:
        write_daily_report("us")
    return exit_code


def run_once(args: argparse.Namespace) -> int:
    symbols = load_symbols(args)
    if not symbols:
        raise RuntimeError("No US symbols to scan.")

    client = KISClient()
    market = OverseasMarketAPI(client)
    portfolio = OverseasPortfolio(client)
    executor = OverseasOrderExecutor(client)

    snapshot = portfolio.get_snapshot()
    selected = select_batch(symbols, set(snapshot.holdings), args.batch_size, args.batch_offset)

    strategy = OverseasRulebookStrategy(
        market=market,
        symbols=selected,
        snapshot=snapshot,
        market_guard_active=args.market_guard,
        buy_slippage_pct=args.buy_slippage_pct,
        sell_slippage_pct=args.sell_slippage_pct,
    )

    print(f"Account mode: {'real' if kis_config.is_real else 'sim'}")
    print(f"US total value: {snapshot.total_value:,} USD")
    print(f"US cash: {snapshot.cash:,} USD")
    print(f"US slot budget: {strategy.slot_budget:,} USD x {strategy.max_slots}")
    print(f"US occupied slots: {snapshot.occupied_slots}/{strategy.max_slots}")
    print_us_slots(snapshot, strategy.slot_budget, strategy.max_slots)
    print(f"Loaded US symbols: {len(symbols)}")
    print(f"KIS US verification symbols: {', '.join(f'{exchange}:{symbol}' for symbol, exchange in selected)}")
    print(f"KIS min interval: {client.min_interval:.1f}s")
    print()

    decisions = strategy.generate_decisions()
    orders = [decision for decision in decisions if decision.has_order]
    for decision in decisions:
        side = decision.side.value if decision.side else "HOLD"
        reason = "; ".join(decision.reasons)
        price = f" limit={decision.limit_price:.2f}" if decision.limit_price else ""
        print(f"{decision.exchange}:{decision.symbol} {side} qty={decision.quantity}{price} score={decision.score} | {reason}")

    if not orders:
        print("\nNo US orders generated.")
        return 0
    if not args.execute:
        print("\nDRY RUN: add --execute to send these US orders.")
        return 0

    print("\nSending US orders...")
    exit_code = 0
    order_requests = strategy.generate_orders()
    for order in order_requests:
        result = executor.send(order)
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
        print(f"{order.exchange}:{order.symbol} {order.side.value} qty={order.quantity} limit={order.limit_price:.2f}: {result}")
        if result.get("rt_cd") != "0":
            exit_code = 1
    return exit_code


def load_symbols(args: argparse.Namespace) -> list[tuple[str, str]]:
    if args.symbols:
        return [(symbol.upper(), args.exchange.upper()) for symbol in args.symbols]

    rows: list[tuple[str, str]] = []
    with Path(args.watchlist).open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            symbol = str(row.get("symbol") or "").strip().upper()
            exchange = str(row.get("exchange") or args.exchange).strip().upper()
            if symbol:
                rows.append((symbol, exchange))
            if len(rows) >= args.limit:
                break
    return _dedupe_symbols(rows)


def select_batch(
    symbols: list[tuple[str, str]],
    held_symbols: set[str],
    batch_size: int,
    batch_offset: int,
) -> list[tuple[str, str]]:
    if batch_size <= 0:
        raise ValueError("--batch-size must be greater than 0")

    held = [item for item in symbols if item[0] in held_symbols]
    non_held = [item for item in symbols if item[0] not in held_symbols]
    if not non_held:
        return held

    start = (batch_offset * batch_size) % len(non_held)
    selected = non_held[start : start + batch_size]
    if len(selected) < batch_size:
        selected.extend(non_held[: batch_size - len(selected)])
    return _dedupe_symbols([*held, *selected])


def print_us_slots(snapshot, slot_budget: int, max_slots: int) -> None:
    print("US Slots:")
    holdings = sorted(snapshot.holdings.values(), key=lambda item: item.market_value, reverse=True)
    for index in range(max_slots):
        if index < len(holdings):
            holding = holdings[index]
            used_pct = (holding.market_value / slot_budget * 100) if slot_budget else 0
            print(
                f"  Slot {index + 1}: {holding.symbol} {holding.name} "
                f"qty={holding.quantity:,} value={holding.market_value:,.2f} USD "
                f"used={used_pct:.1f}% avg={holding.average_price:,.2f} "
                f"now={holding.current_price:,.2f} pnl={holding.profit_rate:+.2f}%"
            )
        else:
            print(f"  Slot {index + 1}: EMPTY budget={slot_budget:,} USD")


def _parse_until(value: str) -> datetime:
    hour, minute = value.split(":", maxsplit=1)
    now = datetime.now()
    target = datetime.combine(now.date(), dt_time(hour=int(hour), minute=int(minute)))
    if target <= now:
        target += timedelta(days=1)
    return target


def wait_until_time(value: str) -> None:
    hour, minute = value.split(":", maxsplit=1)
    now = datetime.now()
    target = datetime.combine(now.date(), dt_time(hour=int(hour), minute=int(minute)))
    if target <= now:
        return
    seconds = (target - now).total_seconds()
    print(f"Waiting until {target.strftime('%Y-%m-%d %H:%M:%S')} before starting.")
    time.sleep(seconds)


def write_daily_report(market: str) -> None:
    from scripts.generate_daily_report import build_report
    from utils.trade_log import read_orders

    yyyymmdd = datetime.now().strftime("%Y%m%d")
    report = build_report(market, yyyymmdd, read_orders(market, yyyymmdd))
    path = Path("reports") / f"{market}_{yyyymmdd}_report.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(report, encoding="utf-8")
    print(f"Daily report written: {path}")


def _dedupe_symbols(symbols: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    for symbol, exchange in symbols:
        key = symbol.upper()
        if key in seen:
            continue
        seen.add(key)
        result.append((symbol.upper(), exchange.upper()))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
