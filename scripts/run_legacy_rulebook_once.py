"""Run the legacy rulebook strategy once.

Default behavior is a dry run. Add --execute to send orders to the KIS
environment selected by KIS_IS_REAL. With KIS_IS_REAL=false, orders go to the
simulation account.
"""
from __future__ import annotations

import argparse
import csv
import copy
from datetime import datetime, time as dt_time
import os
import sys
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config.settings import kis_config
from kis.client import KISClient
from kis.market import MarketAPI
from order.executor import OrderExecutor, OrderRequest, OrderType
from order.portfolio import Portfolio
from strategy.legacy_rulebook import LegacyRulebookStrategy
from strategy.local_prescreen import DEFAULT_5M_CSV, describe_prescreen, prescreen_from_5m_csv
from utils.trade_log import log_order


DEFAULT_UNIVERSE = ROOT_DIR / "data" / "market_ohlcv" / "stock_universe_top100.csv"
DEFAULT_WATCHLIST = ROOT_DIR / "data" / "watchlists" / "rulebook_focus.csv"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the legacy 1jo rulebook strategy once.")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually send generated orders. Without this flag, only a dry run is printed.",
    )
    parser.add_argument(
        "--symbols",
        nargs="*",
        help="Optional stock codes to scan. Example: --symbols 005930 000660 005380",
    )
    parser.add_argument(
        "--universe",
        default=str(DEFAULT_UNIVERSE),
        help="CSV universe path used when --symbols is omitted.",
    )
    parser.add_argument(
        "--watchlist",
        help=f"CSV watchlist path with a code column. Example: {DEFAULT_WATCHLIST}",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of universe symbols to pre-screen locally. Default: 50",
    )
    parser.add_argument(
        "--candidate-limit",
        type=int,
        default=5,
        help="Maximum number of non-held symbols to verify with KIS after local pre-screen. Default: 5",
    )
    parser.add_argument(
        "--no-prescreen",
        action="store_true",
        help="Skip local CSV pre-screening and verify all loaded symbols with KIS.",
    )
    parser.add_argument(
        "--prescreen-csv",
        default=str(DEFAULT_5M_CSV),
        help="Local 5-minute CSV used for pre-screening.",
    )
    parser.add_argument(
        "--market-guard",
        action="store_true",
        help="Manually activate market guard: block new entries and use -1%% stop loss.",
    )
    parser.add_argument(
        "--until",
        help="Repeat until local time HH:MM. Example: --until 16:00",
    )
    parser.add_argument(
        "--until-kr-close",
        action="store_true",
        help="Repeat until the Korean regular market close, 15:30 local time.",
    )
    parser.add_argument(
        "--wait-for-kr-open",
        action="store_true",
        help="Wait until 09:00 local time before starting.",
    )
    parser.add_argument(
        "--report-on-close",
        action="store_true",
        help="Generate a local daily report after the repeat loop stops.",
    )
    parser.add_argument(
        "--repeat-interval-min",
        type=float,
        default=5.0,
        help="Minutes between repeated runs when --until is set. Default: 5",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        help="Rotate through loaded symbols in batches of this size while repeating.",
    )
    parser.add_argument(
        "--cycle-min",
        type=float,
        help="Reset rotating batches back to the first symbols every N minutes.",
    )
    parser.add_argument(
        "--append-universe",
        action="store_true",
        help="When --watchlist is set, append symbols from --universe after the watchlist.",
    )
    parser.set_defaults(batch_offset=0)
    return parser


def load_symbols(args: argparse.Namespace) -> list[str]:
    if args.symbols:
        return [symbol.zfill(6) for symbol in args.symbols]

    if args.watchlist:
        symbols = _read_symbol_csv(Path(args.watchlist), limit=None)
        if args.append_universe:
            symbols.extend(_read_symbol_csv(Path(args.universe), limit=args.limit))
        return _dedupe(symbols)

    universe_path = Path(args.universe)
    return _read_symbol_csv(universe_path, limit=args.limit)


def _read_symbol_csv(path: Path, limit: int | None = None) -> list[str]:
    symbols: list[str] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            symbol = str(row.get("code") or "").strip().zfill(6)
            if symbol:
                symbols.append(symbol)
            if limit and len(symbols) >= limit:
                break
    return symbols


def main() -> int:
    args = build_parser().parse_args()
    if args.wait_for_kr_open:
        wait_until_time("09:00")
    if args.until_kr_close:
        args.until = "15:30"
    if args.until:
        return repeat_until(args)
    return run_once(args)


def repeat_until(args: argparse.Namespace) -> int:
    until_at = _parse_until(args.until)
    if args.repeat_interval_min <= 0:
        raise ValueError("--repeat-interval-min must be greater than 0")

    print(f"Repeating until: {until_at.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Repeat interval: {args.repeat_interval_min:g} minute(s)")
    if args.cycle_min:
        print(f"Batch cycle reset: {args.cycle_min:g} minute(s)")
    print()

    exit_code = 0
    run_count = 0
    cycle_started_at = datetime.now()
    while datetime.now() < until_at:
        if args.cycle_min and (datetime.now() - cycle_started_at).total_seconds() >= args.cycle_min * 60:
            cycle_started_at = datetime.now()
            run_count = 0
            print("\nBatch cycle reset: returning to first symbols.\n")

        started_at = datetime.now()
        print("=" * 72)
        print(f"Run started: {started_at.strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 72)

        run_args = copy.copy(args)
        run_args.until = None
        if args.batch_size:
            run_args.batch_offset = run_count
        try:
            exit_code = max(exit_code, run_once(run_args))
        except Exception as exc:
            exit_code = 1
            print(f"RUN FAILED: {exc}")

        next_at = started_at.timestamp() + (args.repeat_interval_min * 60)
        sleep_seconds = min(max(0, next_at - time.time()), max(0, until_at.timestamp() - time.time()))
        if sleep_seconds <= 0:
            break
        print(f"\nNext run in {sleep_seconds / 60:.1f} minute(s).\n")
        time.sleep(sleep_seconds)
        run_count += 1

    print(f"Stopped at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if args.report_on_close:
        write_daily_report("kr")
    return exit_code


def run_once(args: argparse.Namespace) -> int:
    symbols = load_symbols(args)
    if not symbols:
        raise RuntimeError("No symbols to scan.")

    client = KISClient()
    market = MarketAPI(client)
    portfolio = Portfolio(client)
    executor = OrderExecutor(client)

    snapshot = portfolio.get_snapshot()

    loaded_symbols = symbols
    if args.batch_size:
        symbols = select_batch(symbols, set(snapshot.holdings), args.batch_size, args.batch_offset)
        print(
            f"Rotating batch: {args.batch_offset + 1} "
            f"(batch_size={args.batch_size}, selected={len(symbols)}/{len(loaded_symbols)})"
        )
        print()

    if not args.no_prescreen:
        preview = describe_prescreen(loaded_symbols, Path(args.prescreen_csv), top_n=args.candidate_limit)
        print("Local pre-screen candidates:")
        for candidate in preview:
            print(f"  {candidate.symbol} {candidate.name} score={candidate.score} | {'; '.join(candidate.reasons)}")
        print()

        symbols = prescreen_from_5m_csv(
            universe_symbols=loaded_symbols,
            held_symbols=set(snapshot.holdings),
            csv_path=Path(args.prescreen_csv),
            candidate_limit=args.candidate_limit,
        )

    strategy = LegacyRulebookStrategy(
        market=market,
        symbols=symbols,
        snapshot=snapshot,
        market_guard_active=args.market_guard,
    )

    print(f"Account mode: {'real' if kis_config.is_real else 'sim'}")
    print(f"Total value: {snapshot.total_value:,} KRW")
    print(f"Cash: {snapshot.cash:,} KRW")
    print(f"Slot budget: {strategy.slot_budget:,} KRW x {strategy.max_slots}")
    print(f"Occupied slots: {snapshot.occupied_slots}/{strategy.max_slots}")
    print_slot_status(snapshot, strategy.slot_budget, strategy.max_slots)
    print(f"Loaded universe symbols: {len(loaded_symbols)}")
    print(f"KIS verification symbols: {', '.join(symbols)}")
    print(f"Market guard: {'ON' if args.market_guard else 'OFF'}")
    print(f"KIS min interval: {client.min_interval:.1f}s")
    print()

    decisions = strategy.generate_decisions()
    orders = [decision for decision in decisions if decision.has_order]

    for decision in decisions:
        side = decision.side.value if decision.side else "HOLD"
        reason = "; ".join(decision.reasons)
        print(f"{decision.symbol} {side} qty={decision.quantity} score={decision.score} | {reason}")

    if not orders:
        print("\nNo orders generated.")
        return 0

    if not args.execute:
        print("\nDRY RUN: add --execute to send these orders.")
        return 0

    print("\nSending orders...")
    exit_code = 0
    for decision in orders:
        order = OrderRequest(
            symbol=decision.symbol,
            side=decision.side,
            quantity=decision.quantity,
            price=0,
            order_type=OrderType.MARKET,
        )
        result = executor.send(order)
        log_order(
            "kr",
            {
                "symbol": order.symbol,
                "side": order.side.value,
                "quantity": order.quantity,
                "price": order.price,
                "order_type": order.order_type,
            },
            result,
        )
        print(f"{order.symbol} {order.side.value} qty={order.quantity}: {result}")
        if result.get("rt_cd") != "0":
            exit_code = 1
    return exit_code


def select_batch(symbols: list[str], held_symbols: set[str], batch_size: int, batch_offset: int) -> list[str]:
    if batch_size <= 0:
        raise ValueError("--batch-size must be greater than 0")

    non_held = [symbol for symbol in symbols if symbol not in held_symbols]
    if not non_held:
        return sorted(held_symbols)

    start = (batch_offset * batch_size) % len(non_held)
    selected = non_held[start : start + batch_size]
    if len(selected) < batch_size:
        selected.extend(non_held[: batch_size - len(selected)])
    return _dedupe([*sorted(held_symbols), *selected])


def _dedupe(symbols: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for symbol in symbols:
        if symbol in seen:
            continue
        seen.add(symbol)
        result.append(symbol)
    return result


def print_slot_status(snapshot, slot_budget: int, max_slots: int) -> None:
    print("Slots:")
    holdings = sorted(snapshot.holdings.values(), key=lambda item: item.market_value, reverse=True)
    for index in range(max_slots):
        if index < len(holdings):
            holding = holdings[index]
            slot_used_pct = (holding.market_value / slot_budget * 100) if slot_budget else 0
            print(
                "  "
                f"Slot {index + 1}: {holding.symbol} {holding.name} "
                f"qty={holding.quantity:,} "
                f"value={holding.market_value:,.0f} KRW "
                f"used={slot_used_pct:.1f}% "
                f"avg={holding.average_price:,.0f} "
                f"now={holding.current_price:,.0f} "
                f"pnl={holding.profit_rate:+.2f}%"
            )
        else:
            print(f"  Slot {index + 1}: EMPTY budget={slot_budget:,} KRW")


def _parse_until(value: str) -> datetime:
    hour, minute = value.split(":", maxsplit=1)
    target_time = dt_time(hour=int(hour), minute=int(minute))
    now = datetime.now()
    target = datetime.combine(now.date(), target_time)
    if target <= now:
        raise ValueError(f"--until {value} is not in the future today.")
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


if __name__ == "__main__":
    raise SystemExit(main())
