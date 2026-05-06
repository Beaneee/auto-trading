"""Generate a daily trading report from local bot order logs."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from utils.trade_log import read_orders


REPORT_DIR = Path("reports")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a daily bot trading report.")
    parser.add_argument("--market", choices=["kr", "us"], required=True)
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"), help="YYYYMMDD. Default: today")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    rows = read_orders(args.market, args.date)
    report = build_report(args.market, args.date, rows)

    REPORT_DIR.mkdir(exist_ok=True)
    path = REPORT_DIR / f"{args.market}_{args.date}_report.md"
    path.write_text(report, encoding="utf-8")
    print(f"Report written: {path}")
    print(report)
    return 0


def build_report(market: str, yyyymmdd: str, rows: list[dict]) -> str:
    title = "Korean Market" if market == "kr" else "US Market"
    lines = [
        f"# {title} Trading Report - {yyyymmdd}",
        "",
        "## Orders",
        "",
    ]

    if not rows:
        lines.append("No bot-submitted orders were logged.")
        return "\n".join(lines) + "\n"

    lines.extend(
        [
            "| Time | Symbol | Side | Qty | Price | Accepted | Order No | Message |",
            "|---|---:|---:|---:|---:|---:|---:|---|",
        ]
    )

    totals = defaultdict(float)
    for row in rows:
        order = row.get("order", {})
        result = row.get("result", {})
        symbol = order.get("symbol", "")
        side = order.get("side", "")
        qty = float(order.get("quantity") or 0)
        price = float(order.get("limit_price") or order.get("price") or 0)
        amount = qty * price
        if side == "BUY":
            totals[symbol] -= amount
        elif side == "SELL":
            totals[symbol] += amount

        output = result.get("output") or {}
        order_no = output.get("ODNO") or output.get("odno") or ""
        lines.append(
            "| "
            f"{row.get('timestamp', '')} | {symbol} | {side} | {qty:g} | {price:g} | "
            f"{'Y' if row.get('accepted') else 'N'} | {order_no} | {result.get('msg1', '')} |"
        )

    lines.extend(["", "## Net Cash Flow By Symbol", ""])
    lines.extend(["| Symbol | Net Cash Flow |", "|---|---:|"])
    for symbol, amount in sorted(totals.items()):
        lines.append(f"| {symbol} | {amount:,.2f} |")

    lines.extend(
        [
            "",
            "Note: this report is based on bot-submitted order logs. "
            "Exact fill price, realized P/L, and fees require broker execution inquiry integration.",
        ]
    )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
