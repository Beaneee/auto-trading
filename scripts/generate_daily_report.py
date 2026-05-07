"""Generate a daily trading report from local bot order and signal logs."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import html
import json
from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from utils.trade_log import read_orders


REPORT_DIR = Path("reports")
SIGNAL_DIR = Path("logs") / "signals"
NAME_MAP_PATHS = [
    Path("data") / "market_ohlcv" / "stock_universe_top100.csv",
    Path("data") / "watchlists" / "rulebook_focus.csv",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a daily bot trading report.")
    parser.add_argument("--market", choices=["kr", "us"], required=True)
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"), help="YYYYMMDD. Default: today")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    rows = read_orders(args.market, args.date)
    signals = read_signal_rows(args.market, args.date)
    report = build_report(args.market, args.date, rows, signals)

    REPORT_DIR.mkdir(exist_ok=True)
    path = REPORT_DIR / f"{args.market}_{args.date}_report.md"
    path.write_text(report, encoding="utf-8")
    print(f"Report written: {path}")
    print(report)
    return 0


def build_report(market: str, yyyymmdd: str, rows: list[dict], signals: list[dict] | None = None) -> str:
    signals = signals or []
    title = "Korean Market" if market == "kr" else "US Market"
    summary = summarize_orders(rows)
    name_map = load_name_map()

    lines = [
        f"# {title} Trading Report - {yyyymmdd}",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Submitted orders | {summary['orders']} |",
        f"| Accepted orders | {summary['accepted']} |",
        f"| Buy orders | {summary['buys']} |",
        f"| Sell orders | {summary['sells']} |",
        f"| Unique symbols | {summary['symbols']} |",
        f"| Candidate signals | {len(signals)} |",
        "",
        "## Orders",
        "",
    ]

    if rows:
        lines.extend(
            [
                "| Time | Symbol | Side | Qty | Price | Amount | Accepted | Order No | Message |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        for row in rows:
            order = row.get("order", {})
            result = row.get("result", {})
            symbol = order.get("symbol", "")
            side = order.get("side", "")
            qty = float(order.get("quantity") or 0)
            price = float(order.get("limit_price") or order.get("price") or 0)
            amount = qty * price
            output = result.get("output") or {}
            order_no = output.get("ODNO") or output.get("odno") or ""
            lines.append(
                "| "
                f"{row.get('timestamp', '')} | {format_symbol(symbol, name_map)} | {side} | {qty:g} | {price:g} | {amount:,.2f} | "
                f"{'Y' if row.get('accepted') else 'N'} | {order_no} | {result.get('msg1', '')} |"
            )
    else:
        lines.append("No bot-submitted orders were logged.")

    lines.extend(["", "## Net Cash Flow By Symbol", ""])
    lines.extend(["| Symbol | Net Cash Flow |", "|---|---:|"])
    if summary["cash_flow"]:
        for symbol, amount in sorted(summary["cash_flow"].items()):
            lines.append(f"| {symbol} | {amount:,.2f} |")
    else:
        lines.append("| - | 0.00 |")

    lines.extend(["", "## Candidate Signals", ""])
    if signals:
        lines.extend(["| Time | Symbol | Score | Reason |", "|---|---:|---:|---|"])
        for signal in signals[:100]:
            symbol = signal.get("symbol") or signal.get("code") or ""
            lines.append(
                "| "
                f"{signal.get('timestamp', '')} | {format_symbol(symbol, name_map)} | {signal.get('score', '')} | "
                f"{'; '.join(signal.get('reasons') or [])} |"
            )
    else:
        lines.append("No candidate signals were logged.")

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- This report is based on bot-submitted order logs and scanner signal logs.",
            "- Exact fill price, fees, and realized P/L require broker execution inquiry integration.",
        ]
    )
    return "\n".join(lines) + "\n"


def build_report_html(market: str, yyyymmdd: str, rows: list[dict], signals: list[dict] | None = None) -> str:
    signals = signals or []
    title = "Korean Market" if market == "kr" else "US Market"
    summary = summarize_orders(rows)
    name_map = load_name_map()
    return f"""
    <h1>{html.escape(title)} Trading Report - {html.escape(yyyymmdd)}</h1>
    <section class="summary-grid">
      {summary_card("Submitted", summary["orders"])}
      {summary_card("Accepted", summary["accepted"])}
      {summary_card("Buys", summary["buys"])}
      {summary_card("Sells", summary["sells"])}
      {summary_card("Symbols", summary["symbols"])}
      {summary_card("Signals", len(signals))}
    </section>
    <h2>Orders</h2>
    {orders_table(rows, name_map)}
    <h2>Net Cash Flow By Symbol</h2>
    {cash_flow_table(summary["cash_flow"])}
    <h2>Candidate Signals</h2>
    {signals_table(signals, name_map)}
    <p class="note">Exact fill price, fees, and realized P/L require broker execution inquiry integration.</p>
    """


def summarize_orders(rows: list[dict]) -> dict:
    cash_flow = defaultdict(float)
    symbols: set[str] = set()
    buys = sells = accepted = 0
    for row in rows:
        order = row.get("order", {})
        symbol = order.get("symbol", "")
        if symbol:
            symbols.add(symbol)
        side = order.get("side", "")
        qty = float(order.get("quantity") or 0)
        price = float(order.get("limit_price") or order.get("price") or 0)
        amount = qty * price
        if side == "BUY":
            buys += 1
            cash_flow[symbol] -= amount
        elif side == "SELL":
            sells += 1
            cash_flow[symbol] += amount
        if row.get("accepted"):
            accepted += 1
    return {
        "orders": len(rows),
        "accepted": accepted,
        "buys": buys,
        "sells": sells,
        "symbols": len(symbols),
        "cash_flow": dict(cash_flow),
    }


def read_signal_rows(market: str, yyyymmdd: str) -> list[dict]:
    path = SIGNAL_DIR / f"{market}_{yyyymmdd}.jsonl"
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def summary_card(label: str, value) -> str:
    return f"<div class='summary-card'><span>{html.escape(label)}</span><strong>{html.escape(str(value))}</strong></div>"


def orders_table(rows: list[dict], name_map: dict[str, str]) -> str:
    if not rows:
        return "<p>No bot-submitted orders were logged.</p>"
    body = []
    for row in rows:
        order = row.get("order", {})
        result = row.get("result", {})
        qty = float(order.get("quantity") or 0)
        price = float(order.get("limit_price") or order.get("price") or 0)
        output = result.get("output") or {}
        body.append(
            "<tr>"
            f"<td>{html.escape(row.get('timestamp', ''))}</td>"
            f"<td>{html.escape(format_symbol(str(order.get('symbol', '')), name_map))}</td>"
            f"<td>{html.escape(str(order.get('side', '')))}</td>"
            f"<td>{qty:g}</td>"
            f"<td>{price:g}</td>"
            f"<td>{qty * price:,.2f}</td>"
            f"<td>{'Y' if row.get('accepted') else 'N'}</td>"
            f"<td>{html.escape(str(output.get('ODNO') or output.get('odno') or ''))}</td>"
            f"<td>{html.escape(str(result.get('msg1', '')))}</td>"
            "</tr>"
        )
    return table("Time,Symbol,Side,Qty,Price,Amount,Accepted,Order No,Message", body)


def cash_flow_table(cash_flow: dict[str, float]) -> str:
    if not cash_flow:
        return "<p>No cash flow.</p>"
    body = [f"<tr><td>{html.escape(symbol)}</td><td>{amount:,.2f}</td></tr>" for symbol, amount in sorted(cash_flow.items())]
    return table("Symbol,Net Cash Flow", body)


def signals_table(signals: list[dict], name_map: dict[str, str]) -> str:
    if not signals:
        return "<p>No candidate signals were logged.</p>"
    body = []
    for signal in signals[:100]:
        symbol = signal.get("symbol") or signal.get("code") or ""
        body.append(
            "<tr>"
            f"<td>{html.escape(str(signal.get('timestamp', '')))}</td>"
            f"<td>{html.escape(format_symbol(str(symbol), name_map))}</td>"
            f"<td>{html.escape(str(signal.get('score', '')))}</td>"
            f"<td>{html.escape('; '.join(signal.get('reasons') or []))}</td>"
            "</tr>"
        )
    return table("Time,Symbol,Score,Reason", body)


def table(headers: str, body_rows: list[str]) -> str:
    header_html = "".join(f"<th>{html.escape(item)}</th>" for item in headers.split(","))
    return f"<table><thead><tr>{header_html}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"


def load_name_map() -> dict[str, str]:
    import csv

    mapping: dict[str, str] = {}
    for path in NAME_MAP_PATHS:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                code = str(row.get("code") or "").zfill(6)
                name = str(row.get("name") or "").strip()
                if code and name:
                    mapping[code] = name
    return mapping


def format_symbol(symbol: str, name_map: dict[str, str]) -> str:
    symbol = str(symbol)
    name = name_map.get(symbol)
    return f"{name}({symbol})" if name else symbol


if __name__ == "__main__":
    raise SystemExit(main())
