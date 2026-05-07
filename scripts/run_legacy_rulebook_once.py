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
import json
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
from utils.tier2_watchlist import add_tier2_symbol, load_tier2_symbols
from utils.trade_log import log_order


DEFAULT_UNIVERSE = ROOT_DIR / "data" / "market_ohlcv" / "stock_universe_top100.csv"
DEFAULT_WATCHLIST = ROOT_DIR / "data" / "watchlists" / "rulebook_focus.csv"
STATE_DIR = ROOT_DIR / ".cache"
SIGNAL_DIR = ROOT_DIR / "logs" / "signals"
CANDIDATE_INBOX = ROOT_DIR / "signals" / "inbox" / "kr_candidates.jsonl"


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
        "--candidate-file",
        default=str(CANDIDATE_INBOX),
        help="External scanner candidate JSONL path.",
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
    parser.add_argument(
        "--priority-holdings-sec",
        type=float,
        default=60.0,
        help="Check current holdings at least this often during repeat mode. Default: 60 seconds.",
    )
    parser.add_argument(
        "--tier2-size",
        type=int,
        default=30,
        help="Maximum Korean tier-2 watchlist size. Default: 30",
    )
    parser.add_argument(
        "--max-slots",
        type=int,
        default=3,
        help="Maximum holding slots. Default: 3",
    )
    parser.add_argument(
        "--total-budget",
        type=int,
        help="Optional total KRW budget used for slot sizing. Defaults to account total value.",
    )
    parser.add_argument(
        "--scan-state",
        default=str(STATE_DIR / "kr_scan_state.json"),
        help="Path for remembering the next universe scan offset.",
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
            run_args.batch_offset = get_next_scan_offset(args)
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
    external_candidates = load_external_candidates(Path(args.candidate_file), args.candidate_limit)
    if external_candidates:
        print(f"External scanner candidates: {', '.join(external_candidates)}")
        for symbol in external_candidates:
            add_tier2_symbol(symbol, "external_scanner", max_symbols=args.tier2_size)
        symbols = _dedupe([*external_candidates, *symbols])
        loaded_symbols = _dedupe([*external_candidates, *loaded_symbols])

    if args.batch_size:
        symbols = select_batch(
            symbols=symbols,
            held_symbols=set(snapshot.holdings),
            tier2_symbols=set(load_tier2_symbols(max_symbols=args.tier2_size)),
            batch_size=args.batch_size,
            batch_offset=args.batch_offset,
            force_held=True,
        )
        print(
            f"Rotating scan offset: {args.batch_offset} "
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
        max_slots=args.max_slots,
        total_budget=args.total_budget,
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
    signal_candidates = [decision for decision in decisions if decision.score >= 2 and not decision.has_order]
    if signal_candidates:
        log_signal_candidates(signal_candidates)
        for decision in signal_candidates:
            add_tier2_symbol(decision.symbol, "signal_no_order", max_symbols=args.tier2_size)

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
        if order.side.value == "SELL" and result.get("rt_cd") == "0":
            add_tier2_symbol(order.symbol, "sold_position", max_symbols=args.tier2_size)
        print(f"{order.symbol} {order.side.value} qty={order.quantity}: {result}")
        if result.get("rt_cd") != "0":
            exit_code = 1
    return exit_code


def get_next_scan_offset(args: argparse.Namespace) -> int:
    path = Path(args.scan_state)
    state = load_json(path)
    now = time.time()
    last_holdings_check = float(state.get("last_holdings_check", 0))

    if now - last_holdings_check >= args.priority_holdings_sec:
        state["last_holdings_check"] = now
        save_json(path, state)
        return int(state.get("next_offset", 0))

    next_offset = int(state.get("next_offset", 0))
    state["next_offset"] = next_offset + 1
    save_json(path, state)
    return next_offset


def select_batch(
    symbols: list[str],
    held_symbols: set[str],
    tier2_symbols: set[str],
    batch_size: int,
    batch_offset: int,
    force_held: bool = True,
) -> list[str]:
    if batch_size <= 0:
        raise ValueError("--batch-size must be greater than 0")

    all_tier2 = [symbol for symbol in symbols if symbol in tier2_symbols and symbol not in held_symbols]
    non_priority = [symbol for symbol in symbols if symbol not in held_symbols and symbol not in tier2_symbols]
    if not non_priority:
        tier2 = rotate_slice(all_tier2, batch_size, batch_offset)
        return _dedupe([*sorted(held_symbols), *tier2])

    tier2_slots = min(len(all_tier2), max(1, batch_size // 2))
    tier2 = rotate_slice(all_tier2, tier2_slots, batch_offset)
    scan_slots = max(1, batch_size - len(tier2))
    start = (batch_offset * scan_slots) % len(non_priority)
    selected = non_priority[start : start + scan_slots]
    if len(selected) < scan_slots:
        selected.extend(non_priority[: scan_slots - len(selected)])
    return _dedupe([*sorted(held_symbols), *tier2, *selected] if force_held else [*tier2, *selected])


def rotate_slice(symbols: list[str], size: int, offset: int) -> list[str]:
    if not symbols or size <= 0:
        return []
    start = (offset * size) % len(symbols)
    selected = symbols[start : start + size]
    if len(selected) < size:
        selected.extend(symbols[: size - len(selected)])
    return selected


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def load_external_candidates(path: Path, limit: int) -> list[str]:
    if limit <= 0 or not path.exists():
        return []
    candidates: list[tuple[int, str]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                data = json.loads(line)
                symbol = str(data.get("code") or data.get("symbol") or "").strip().zfill(6)
                score = int(data.get("score") or 0)
                if symbol:
                    candidates.append((score, symbol))
    except (OSError, json.JSONDecodeError, ValueError):
        return []
    candidates.sort(reverse=True)
    return _dedupe([symbol for _, symbol in candidates])[:limit]


def log_signal_candidates(decisions) -> None:
    SIGNAL_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    path = SIGNAL_DIR / f"kr_{now:%Y%m%d}.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        for decision in decisions:
            handle.write(
                json.dumps(
                    {
                        "timestamp": now.isoformat(timespec="seconds"),
                        "symbol": decision.symbol,
                        "side": decision.side.value if decision.side else None,
                        "quantity": decision.quantity,
                        "score": decision.score,
                        "reasons": decision.reasons,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


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
