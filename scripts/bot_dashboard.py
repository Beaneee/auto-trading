"""Small local web dashboard for configuring and running the trading bot."""
from __future__ import annotations

import html
import json
from datetime import datetime, time as dt_time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import traceback
import time
from urllib.parse import parse_qs


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

SETTINGS_PATH = ROOT_DIR / ".cache" / "dashboard_settings.json"
REPORT_DIR = ROOT_DIR / "reports"
PYTHON = sys.executable
PROCESS: subprocess.Popen | None = None
SCORE_CACHE: dict[str, tuple[float, int, list[str]]] = {}
SCORE_CACHE_TTL = 60


DEFAULT_SETTINGS = {
    "kis_interval": "3.0",
    "days": "3",
    "kr_slots": "3",
    "us_slots": "3",
    "kr_total_budget": "",
    "us_total_budget": "",
    "kr_batch_size": "8",
    "us_batch_size": "6",
    "kr_cycle_min": "4",
    "us_cycle_min": "15",
    "kr_scan_markets": "KOSPI,KOSDAQ",
    "kr_scan_top": "1000",
    "kr_scan_candidate_limit": "30",
    "kr_full_scan_interval_min": "5",
}


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.startswith("/report/"):
            self.render_report(self.path.removeprefix("/report/"))
            return
        if self.path.startswith("/slots"):
            self.render_slots()
            return
        if self.path.startswith("/settings"):
            self.render_settings()
            return
        if self.path.startswith("/tier2"):
            self.render_tier2()
            return
        if self.path.startswith("/score/"):
            self.render_score(self.path.removeprefix("/score/"))
            return
        self.render_home()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        form = parse_qs(self.rfile.read(length).decode("utf-8"))
        action = form.get("action", ["save"])[0]
        settings = normalize_settings(form)
        save_settings(settings)

        if action == "start":
            message = start_supervisor(settings)
        elif action == "stop":
            message = stop_supervisor()
        else:
            message = "Settings saved."
        self.render_home(message)

    def render_home(self, message: str = "") -> None:
        settings = load_settings()
        reports = sorted(REPORT_DIR.glob("*_report.md"), reverse=True) if REPORT_DIR.exists() else []
        status = "RUNNING" if PROCESS and PROCESS.poll() is None else "STOPPED"
        session = current_session()
        slots_html = render_slots_dashboard(settings)
        tier2_top_html = render_tier2_top5()
        body = f"""
        <html>
        <head>
          <meta charset="utf-8" />
          <meta http-equiv="refresh" content="60" />
          <title>Trading Bot Dashboard</title>
          <style>
            body {{ font-family: Segoe UI, sans-serif; margin: 0; color: #dbeafe; background: #020617; }}
            main {{ max-width: 1180px; margin: 0 auto; padding: 28px; }}
            h1 {{ margin: 0 0 8px; color: #f8fafc; letter-spacing: 0; }}
            h2 {{ color: #93c5fd; }}
            label {{ display: block; font-size: 12px; margin: 14px 0 4px; color: #94a3b8; }}
            input {{ width: 220px; padding: 8px; border: 1px solid #1e293b; border-radius: 6px; background: #0f172a; color: #e2e8f0; }}
            button {{ padding: 9px 14px; border: 1px solid #38bdf8; border-radius: 6px; background: #0ea5e9; color: #03131f; cursor: pointer; font-weight: 700; }}
            button.secondary {{ background: #111827; color: #cbd5e1; border-color: #334155; }}
            .grid {{ display: grid; grid-template-columns: repeat(3, minmax(220px, 1fr)); gap: 12px 24px; }}
            .panel {{ border: 1px solid #1e3a8a; border-radius: 8px; padding: 18px; margin: 18px 0; background: linear-gradient(180deg, #07111f, #050816); box-shadow: 0 0 24px rgba(14,165,233,.12); }}
            .hud {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin: 18px 0; }}
            .hud-card {{ border: 1px solid #164e63; background: #061826; border-radius: 8px; padding: 16px; }}
            .hud-label {{ color: #67e8f9; font-size: 12px; text-transform: uppercase; }}
            .hud-value {{ color: #f8fafc; font-size: 24px; font-weight: 800; margin-top: 6px; }}
            .status {{ font-weight: 800; color: #22c55e; }}
            .slots {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }}
            table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
            th, td {{ border-bottom: 1px solid #1e293b; padding: 8px; text-align: right; }}
            th:first-child, td:first-child, th:nth-child(2), td:nth-child(2) {{ text-align: left; }}
            th {{ color: #67e8f9; font-weight: 700; }}
            .profit {{ color: #22c55e; }}
            .loss {{ color: #f87171; }}
            a {{ color: #38bdf8; text-decoration: none; }}
            ul {{ line-height: 1.8; }}
            pre {{ color: #bae6fd; white-space: pre-wrap; }}
          </style>
        </head>
        <body>
          <main>
            <h1>Trading Bot Dashboard</h1>
            <div class="hud">
              <div class="hud-card"><div class="hud-label">Bot</div><div class="hud-value">{status}</div></div>
              <div class="hud-card"><div class="hud-label">Market Session</div><div class="hud-value">{session}</div></div>
              <div class="hud-card"><div class="hud-label">Updated</div><div class="hud-value">{datetime.now().strftime("%H:%M:%S")}</div></div>
            </div>
            <p>{html.escape(message)}</p>
            <div class="panel">
              <h2>Live Slots</h2>
              <p><a href="/">Refresh</a> · <a href="/slots">Detailed slot status</a></p>
              {slots_html}
            </div>
            <div class="panel">
              <h2>Tier-2 Watchlist</h2>
              <p><a href="/tier2">Open full tier-2 log</a></p>
              {tier2_top_html}
            </div>
            <form method="post">
              <button type="submit" name="action" value="save" class="secondary">Save</button>
              <button type="submit" name="action" value="start">Start 24h Supervisor</button>
              <button type="submit" name="action" value="stop" class="secondary">Stop</button>
            </form>
            <div class="panel">
              <h2>Navigation</h2>
              <p><a href="/settings">Environment Settings</a> · <a href="/tier2">Tier-2 Watchlist & Signal Log</a></p>
            </div>
            <div class="panel">
              <h2>Reports</h2>
              {render_report_list(reports)}
            </div>
            <div class="panel">
              <h2>Terminal Command</h2>
              <pre>{html.escape(build_command_preview(settings))}</pre>
            </div>
          </main>
        </body>
        </html>
        """
        self.send_html(body)

    def render_settings(self, message: str = "") -> None:
        settings = load_settings()
        body = f"""
        <html><head><meta charset="utf-8" /><title>Settings</title>{dashboard_style()}</head>
        <body><main>
          <p><a href="/">Back to dashboard</a></p>
          <h1>Environment Settings</h1>
          <p>{html.escape(message)}</p>
          <form method="post">
            <div class="panel">
              <div class="grid">
                {input_field("days", "Run days", settings)}
                {input_field("kr_total_budget", "KR budget KRW", settings)}
                {input_field("kr_slots", "KR slots", settings)}
                {input_field("us_total_budget", "US budget USD", settings)}
                {input_field("us_slots", "US slots", settings)}
                {input_field("kr_cycle_min", "KR cycle minutes", settings)}
                {input_field("us_cycle_min", "US cycle minutes", settings)}
                {input_field("kr_scan_markets", "KR scan markets", settings)}
                {input_field("kr_scan_top", "KR scan top N", settings)}
                {input_field("kr_scan_candidate_limit", "KR candidate limit", settings)}
                {input_field("kr_full_scan_interval_min", "KR full scan minutes", settings)}
              </div>
            </div>
            <button type="submit" name="action" value="save">Save Settings</button>
          </form>
        </main></body></html>
        """
        self.send_html(body)

    def render_tier2(self) -> None:
        tier2 = load_tier2_data()
        signals = load_recent_signal_rows()
        body = f"""
        <html><head><meta charset="utf-8" /><meta http-equiv="refresh" content="60" /><title>Tier-2 Watchlist</title>{dashboard_style()}</head>
        <body><main>
          <p><a href="/">Back to dashboard</a></p>
          <h1>Tier-2 Watchlist & Signal Log</h1>
          <div class="panel">
            <h2>Current Tier-2 Symbols</h2>
            {tier2_symbols_table(tier2)}
          </div>
          <div class="panel">
            <h2>Tier-2 Events</h2>
            {tier2_events_table(tier2)}
          </div>
          <div class="panel">
            <h2>Recent Candidate Signals</h2>
            {signal_rows_table(signals)}
          </div>
        </main></body></html>
        """
        self.send_html(body)

    def render_score(self, target: str) -> None:
        try:
            market, symbol = target.split("/", maxsplit=1)
            body_inner = render_score_detail(market, symbol)
        except Exception:
            body_inner = f"<pre>{html.escape(traceback.format_exc())}</pre>"
        body = f"""
        <html><head><meta charset="utf-8" /><title>Score Detail</title>{dashboard_style()}</head>
        <body><main>
          <p><a href="/">Back to dashboard</a></p>
          <h1>Score Detail</h1>
          {body_inner}
        </main></body></html>
        """
        self.send_html(body)

    def render_slots(self) -> None:
        settings = load_settings()
        try:
            kr_html = render_market_slots("kr", int(settings.get("kr_slots") or 3))
        except Exception:
            kr_html = f"<pre>{html.escape(traceback.format_exc())}</pre>"

        try:
            us_html = render_market_slots("us", int(settings.get("us_slots") or 3))
        except Exception:
            us_html = f"<pre>{html.escape(traceback.format_exc())}</pre>"

        body = f"""
        <html>
        <head>
          <meta charset="utf-8" />
          <title>Slot Status</title>
          <style>
            body {{ font-family: Segoe UI, sans-serif; margin: 32px; color: #1f2933; }}
            main {{ max-width: 1100px; margin: 0 auto; }}
            table {{ border-collapse: collapse; width: 100%; margin: 12px 0 28px; }}
            th, td {{ border-bottom: 1px solid #d9e2ec; padding: 9px; text-align: right; }}
            th:first-child, td:first-child, th:nth-child(2), td:nth-child(2) {{ text-align: left; }}
            a {{ color: #1d4ed8; text-decoration: none; }}
          </style>
        </head>
        <body>
          <main>
            <p><a href="/">Back to dashboard</a></p>
            <h1>Slot Status</h1>
            <p>Updated: {html.escape(__import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"))}</p>
            <h2>Korean Market</h2>
            {kr_html}
            <h2>US Market</h2>
            {us_html}
          </main>
        </body>
        </html>
        """
        self.send_html(body)

    def render_report(self, name: str) -> None:
        path = (REPORT_DIR / name).resolve()
        if REPORT_DIR.resolve() not in path.parents or not path.exists():
            self.send_error(404)
            return
        try:
            market, date, _ = path.stem.split("_", maxsplit=2)
            from scripts.generate_daily_report import build_report_html, read_signal_rows
            from utils.trade_log import read_orders

            report_body = build_report_html(market, date, read_orders(market, date), read_signal_rows(market, date))
        except Exception:
            report_body = f"<pre>{html.escape(path.read_text(encoding='utf-8'))}</pre>"
        body = f"""
        <html>
        <head>
          <meta charset="utf-8" />
          <title>{html.escape(name)}</title>
          <style>
            body {{ font-family: Segoe UI, sans-serif; margin: 0; background: #020617; color: #dbeafe; }}
            main {{ max-width: 1180px; margin: 0 auto; padding: 28px; }}
            h1 {{ color: #f8fafc; }}
            h2 {{ color: #93c5fd; margin-top: 28px; }}
            table {{ border-collapse: collapse; width: 100%; margin: 12px 0 24px; font-size: 13px; }}
            th, td {{ border-bottom: 1px solid #1e293b; padding: 8px; text-align: right; }}
            th:first-child, td:first-child, th:nth-child(2), td:nth-child(2) {{ text-align: left; }}
            th {{ color: #67e8f9; }}
            .summary-grid {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; }}
            .summary-card {{ border: 1px solid #164e63; background: #061826; border-radius: 8px; padding: 14px; }}
            .summary-card span {{ display:block; color:#67e8f9; font-size:12px; }}
            .summary-card strong {{ display:block; color:#f8fafc; font-size:22px; margin-top:6px; }}
            .note {{ color: #94a3b8; }}
            a {{ color: #38bdf8; text-decoration: none; }}
          </style>
        </head>
        <body><main><p><a href="/">Back to dashboard</a></p>{report_body}</main></body>
        </html>
        """
        self.send_html(body)

    def send_html(self, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def input_field(name: str, label: str, settings: dict[str, str]) -> str:
    value = html.escape(settings.get(name, ""))
    return f"<div><label for='{name}'>{label}</label><input id='{name}' name='{name}' value='{value}' /></div>"


def dashboard_style() -> str:
    return """
    <style>
      body { font-family: Segoe UI, sans-serif; margin: 0; color: #dbeafe; background: #020617; }
      main { max-width: 1180px; margin: 0 auto; padding: 28px; }
      h1 { margin: 0 0 8px; color: #f8fafc; letter-spacing: 0; }
      h2 { color: #93c5fd; }
      label { display: block; font-size: 12px; margin: 14px 0 4px; color: #94a3b8; }
      input { width: 220px; padding: 8px; border: 1px solid #1e293b; border-radius: 6px; background: #0f172a; color: #e2e8f0; }
      button { padding: 9px 14px; border: 1px solid #38bdf8; border-radius: 6px; background: #0ea5e9; color: #03131f; cursor: pointer; font-weight: 700; }
      .grid { display: grid; grid-template-columns: repeat(3, minmax(220px, 1fr)); gap: 12px 24px; }
      .panel { border: 1px solid #1e3a8a; border-radius: 8px; padding: 18px; margin: 18px 0; background: linear-gradient(180deg, #07111f, #050816); box-shadow: 0 0 24px rgba(14,165,233,.12); }
      table { border-collapse: collapse; width: 100%; font-size: 13px; }
      th, td { border-bottom: 1px solid #1e293b; padding: 8px; text-align: right; }
      th:first-child, td:first-child, th:nth-child(2), td:nth-child(2) { text-align: left; }
      th { color: #67e8f9; font-weight: 700; }
      a { color: #38bdf8; text-decoration: none; }
      pre { color: #bae6fd; white-space: pre-wrap; }
    </style>
    """


def render_report_list(reports: list[Path]) -> str:
    if not reports:
        return "<p>No reports yet.</p>"
    items = "\n".join(f"<li><a href='/report/{html.escape(path.name)}'>{html.escape(path.name)}</a></li>" for path in reports[:20])
    return f"<ul>{items}</ul>"


def load_tier2_data() -> dict:
    path = ROOT_DIR / ".cache" / "kr_tier2_watchlist.json"
    if not path.exists():
        return {"symbols": [], "events": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"symbols": [], "events": []}


def tier2_symbols_table(data: dict) -> str:
    scores = load_score_store()
    symbols = data.get("symbols") or []
    if not symbols:
        return "<p>No dynamic tier-2 symbols yet. Defaults still include 삼성전자(005930), SK하이닉스(000660), 한화에어로스페이스(012450).</p>"
    rows = "".join(
        f"<tr><td>{idx}</td><td>{html.escape(symbol)}</td><td><a href='/score/kr/{html.escape(symbol)}'>{score_label('kr', symbol, scores)}</a></td></tr>"
        for idx, symbol in enumerate(symbols, start=1)
    )
    return f"<table><thead><tr><th>#</th><th>Symbol</th><th>Score</th></tr></thead><tbody>{rows}</tbody></table>"


def render_tier2_top5() -> str:
    data = load_tier2_data()
    scores = load_score_store()
    name_map = load_name_map()
    symbols = data.get("symbols") or []
    rows = []
    for original_idx, symbol in enumerate(symbols, start=1):
        score_data = scores.get(f"kr:{symbol}") or {}
        score = int(score_data.get("score") or -1)
        rows.append((score, symbol, score_data.get("updated_at", ""), original_idx))
    rows.sort(key=lambda item: (item[0], -item[3]), reverse=True)
    rows = rows[:10]
    if not rows:
        return "<p>No tier-2 symbols yet.</p>"
    html_rows = "".join(
        "<tr>"
        f"<td>{idx}</td>"
        f"<td>{html.escape(format_symbol_for_display(symbol, name_map))}</td>"
        f"<td><a href='/score/kr/{html.escape(symbol)}'>{score if score >= 0 else 'not scored'}</a></td>"
        f"<td>{html.escape(updated)}</td>"
        "</tr>"
        for idx, (score, symbol, updated, _) in enumerate(rows, start=1)
    )
    return "<table><thead><tr><th>#</th><th>Symbol</th><th>Score</th><th>Updated</th></tr></thead><tbody>" + html_rows + "</tbody></table>"


def load_name_map() -> dict[str, str]:
    import csv

    paths = [
        ROOT_DIR / "data" / "market_ohlcv" / "stock_universe_top100.csv",
        ROOT_DIR / "data" / "watchlists" / "rulebook_focus.csv",
    ]
    mapping: dict[str, str] = {}
    for path in paths:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                code = str(row.get("code") or "").zfill(6)
                name = str(row.get("name") or "").strip()
                if code and name:
                    mapping[code] = name
    return mapping


def format_symbol_for_display(symbol: str, name_map: dict[str, str]) -> str:
    name = name_map.get(symbol)
    return f"{name}({symbol})" if name else symbol


def tier2_events_table(data: dict) -> str:
    events = list(reversed(data.get("events") or []))[:100]
    if not events:
        return "<p>No tier-2 events yet.</p>"
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(event.get('timestamp', '')))}</td>"
        f"<td>{html.escape(str(event.get('symbol', '')))}</td>"
        f"<td>{html.escape(str(event.get('reason', '')))}</td>"
        "</tr>"
        for event in events
    )
    return f"<table><thead><tr><th>Time</th><th>Symbol</th><th>Reason</th></tr></thead><tbody>{rows}</tbody></table>"


def load_recent_signal_rows() -> list[dict]:
    signal_dir = ROOT_DIR / "logs" / "signals"
    files = sorted(signal_dir.glob("kr_*.jsonl"), reverse=True) if signal_dir.exists() else []
    rows: list[dict] = []
    for path in files[:3]:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    rows.append(json.loads(line))
    return list(reversed(rows))[-100:]


def signal_rows_table(rows: list[dict]) -> str:
    scores = load_score_store()
    if not rows:
        return "<p>No candidate signal logs yet.</p>"
    rows_html = "".join(
        "<tr>"
        f"<td>{html.escape(str(row.get('timestamp', '')))}</td>"
        f"<td>{html.escape(str(row.get('symbol') or row.get('code') or ''))}</td>"
        f"<td><a href='/score/kr/{html.escape(str(row.get('symbol') or row.get('code') or ''))}'>{html.escape(str(row.get('score') or score_label('kr', str(row.get('symbol') or row.get('code') or ''), scores)))}</a></td>"
        f"<td>{html.escape('; '.join(row.get('reasons') or []))}</td>"
        "</tr>"
        for row in reversed(rows)
    )
    return f"<table><thead><tr><th>Time</th><th>Symbol</th><th>Score</th><th>Reasons</th></tr></thead><tbody>{rows_html}</tbody></table>"


def render_market_slots(market: str, max_slots: int, lightweight: bool = False) -> str:
    if lightweight and market == "us":
        return "<p>US slot auto-refresh is paused to avoid KIS request limits. Use <a href='/slots'>Detailed slot status</a> when needed.</p>"

    if market == "kr":
        from kis.client import KISClient
        from order.portfolio import Portfolio

        snapshot = Portfolio(KISClient()).get_snapshot()
        unit = "KRW"
    else:
        from kis.client import KISClient
        from order.overseas_portfolio import OverseasPortfolio

        snapshot = OverseasPortfolio(KISClient()).get_snapshot()
        unit = "USD"

    slot_budget = snapshot.total_value // max_slots if max_slots else 0
    holdings = sorted(snapshot.holdings.values(), key=lambda item: item.market_value, reverse=True)
    rows = []
    for index in range(max_slots):
        if index < len(holdings):
            holding = holdings[index]
            used_pct = (holding.market_value / slot_budget * 100) if slot_budget else 0
            pnl_class = "profit" if holding.profit_rate >= 0 else "loss"
            label = score_label(market, holding.symbol)
            rows.append(
                "<tr>"
                f"<td>Slot {index + 1}</td>"
                f"<td>{html.escape(format_symbol_name(holding.symbol, holding.name))}</td>"
                f"<td><a href='/score/{market}/{holding.symbol}'>{label}</a></td>"
                f"<td>{holding.quantity:,}</td>"
                f"<td>{holding.market_value:,.2f} {unit}</td>"
                f"<td>{used_pct:.1f}%</td>"
                f"<td>{holding.average_price:,.2f}</td>"
                f"<td>{holding.current_price:,.2f}</td>"
                f"<td class='{pnl_class}'>{holding.profit_rate:+.2f}%</td>"
                "</tr>"
            )
        else:
            rows.append(
                "<tr>"
                f"<td>Slot {index + 1}</td><td>EMPTY</td><td>-</td><td>0</td>"
                f"<td>0 {unit}</td><td>0.0%</td><td></td><td></td><td></td>"
                "</tr>"
            )

    return (
        f"<p>Total value: {snapshot.total_value:,.2f} {unit} / Cash: {snapshot.cash:,.2f} {unit} / "
        f"Slot budget: {slot_budget:,.2f} {unit}</p>"
        "<table><thead><tr><th>Slot</th><th>Symbol</th><th>Score</th><th>Qty</th><th>Value</th>"
        "<th>Used</th><th>Avg</th><th>Now</th><th>P/L</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def render_slots_dashboard(settings: dict[str, str]) -> str:
    parts = []
    for market, title, slots_key in [("kr", "Korean Slots", "kr_slots"), ("us", "US Slots", "us_slots")]:
        try:
            parts.append(f"<div><h2>{title}</h2>{render_market_slots(market, int(settings.get(slots_key) or 3), lightweight=True)}</div>")
        except Exception as exc:
            parts.append(f"<div><h2>{title}</h2><pre>{html.escape(str(exc))}</pre></div>")
    return f"<div class='slots'>{''.join(parts)}</div>"


def format_symbol_name(symbol: str, name: str) -> str:
    clean_name = name.strip() if name else symbol
    return f"{clean_name}({symbol})"


def get_holding_score(market: str, symbol: str, holding) -> tuple[int, list[str]]:
    key = f"{market}:{symbol}"
    cached = SCORE_CACHE.get(key)
    now = time.time()
    if cached and now - cached[0] < SCORE_CACHE_TTL:
        return cached[1], cached[2]

    if market == "kr":
        from kis.client import KISClient
        from kis.market import MarketAPI
        from strategy.legacy_rulebook import LegacyRulebookStrategy, score_holding_strength

        df = LegacyRulebookStrategy(MarketAPI(KISClient()), [symbol], None)._load_ohlcv(symbol)
        score, reasons = score_holding_strength(df, holding)
        SCORE_CACHE[key] = (now, score, reasons)
        return score, reasons

    from kis.client import KISClient
    from kis.overseas import OverseasMarketAPI
    from strategy.legacy_rulebook import score_holding_strength
    from strategy.overseas_rulebook import OverseasRulebookStrategy
    from order.portfolio import PortfolioSnapshot

    market_api = OverseasMarketAPI(KISClient())
    snapshot = PortfolioSnapshot(cash=0, total_value=0, holdings={symbol: holding})
    strategy = OverseasRulebookStrategy(market_api, [(symbol, "NASD")], snapshot)
    df = strategy._load_ohlcv(symbol, "NASD")
    score, reasons = score_holding_strength(df, holding)
    SCORE_CACHE[key] = (now, score, reasons)
    return score, reasons


def get_cached_score(market: str, symbol: str) -> int | None:
    cached = SCORE_CACHE.get(f"{market}:{symbol}")
    if not cached:
        return None
    if time.time() - cached[0] >= SCORE_CACHE_TTL:
        return None
    return cached[1]


def load_score_store() -> dict:
    path = ROOT_DIR / ".cache" / "score_store.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def score_label(market: str, symbol: str, scores: dict | None = None) -> str:
    scores = scores if scores is not None else load_score_store()
    data = scores.get(f"{market}:{symbol}")
    return str(data.get("score")) if data else "calc"


def render_score_detail(market: str, symbol: str) -> str:
    stored = load_score_store().get(f"{market}:{symbol}")
    if stored:
        rows = "".join(
            f"<tr><td>{idx}</td><td>{html.escape(str(item.get('label', '')))}</td><td>{html.escape(str(item.get('points', '')))}</td></tr>"
            for idx, item in enumerate(stored.get("details") or [], start=1)
        )
        if not rows:
            rows = "".join(
                f"<tr><td>{idx}</td><td>{html.escape(reason)}</td><td>-</td></tr>"
                for idx, reason in enumerate(stored.get("reasons") or [], start=1)
            )
        return (
            f"<div class='panel'><h2>{html.escape(symbol)}</h2>"
            f"<p>Score: <strong>{html.escape(str(stored.get('score')))}</strong> / Type: {html.escape(str(stored.get('type', '')))} / Updated: {html.escape(str(stored.get('updated_at', '')))}</p>"
            "<table><thead><tr><th>#</th><th>Reason</th><th>Points</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></div>"
        )

    if market == "kr":
        from kis.client import KISClient
        from order.portfolio import Portfolio

        snapshot = Portfolio(KISClient()).get_snapshot()
    else:
        from kis.client import KISClient
        from order.overseas_portfolio import OverseasPortfolio

        snapshot = OverseasPortfolio(KISClient()).get_snapshot()

    holding = snapshot.holdings.get(symbol.upper() if market == "us" else symbol)
    if not holding:
        return f"<p>No holding found for {html.escape(symbol)}.</p>"
    score, reasons = get_holding_score(market, holding.symbol, holding)
    reason_rows = "".join(f"<tr><td>{idx}</td><td>{html.escape(reason)}</td><td>-</td></tr>" for idx, reason in enumerate(reasons, start=1))
    return (
        f"<div class='panel'><h2>{html.escape(format_symbol_name(holding.symbol, holding.name))}</h2>"
        f"<p>Score: <strong>{score}</strong></p>"
        f"<p>Quantity: {holding.quantity:,} / Avg: {holding.average_price:,.2f} / Now: {holding.current_price:,.2f} / P/L: {holding.profit_rate:+.2f}%</p>"
        "<table><thead><tr><th>#</th><th>Reason</th><th>Points</th></tr></thead>"
        f"<tbody>{reason_rows}</tbody></table></div>"
    )


def current_session() -> str:
    now = datetime.now()
    if is_between(now, "09:00", "15:30"):
        return "KR OPEN"
    if is_between(now, "22:30", "05:00"):
        return "US OPEN"
    return "WAITING"


def is_between(now: datetime, start: str, end: str) -> bool:
    start_at = datetime.combine(now.date(), parse_time(start))
    end_at = datetime.combine(now.date(), parse_time(end))
    if end_at <= start_at:
        return now >= start_at or now < end_at
    return start_at <= now < end_at


def parse_time(value: str) -> dt_time:
    hour, minute = value.split(":", maxsplit=1)
    return dt_time(hour=int(hour), minute=int(minute))


def normalize_settings(form: dict[str, list[str]]) -> dict[str, str]:
    settings = load_settings()
    for key in DEFAULT_SETTINGS:
        settings[key] = form.get(key, [settings.get(key, DEFAULT_SETTINGS[key])])[0].strip()
    return settings


def load_settings() -> dict[str, str]:
    if not SETTINGS_PATH.exists():
        return DEFAULT_SETTINGS.copy()
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DEFAULT_SETTINGS.copy()
    return {**DEFAULT_SETTINGS, **{key: str(value) for key, value in data.items()}}


def save_settings(settings: dict[str, str]) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def start_supervisor(settings: dict[str, str]) -> str:
    global PROCESS
    if PROCESS and PROCESS.poll() is None:
        return "Supervisor is already running."

    env = None
    command = build_supervisor_command(settings)
    PROCESS = subprocess.Popen(command, cwd=ROOT_DIR, env=env)
    return f"Supervisor started with PID {PROCESS.pid}."


def stop_supervisor() -> str:
    global PROCESS
    if not PROCESS or PROCESS.poll() is not None:
        return "Supervisor is not running."
    PROCESS.terminate()
    return "Stop requested."


def build_supervisor_command(settings: dict[str, str]) -> list[str]:
    return [
        PYTHON,
        str(ROOT_DIR / "scripts" / "run_market_day.py"),
        "--days",
        settings["days"] or "3",
        "--kis-interval",
        settings.get("kis_interval") or "3.0",
        "--kr-slots",
        settings["kr_slots"] or "3",
        "--us-slots",
        settings["us_slots"] or "3",
        "--kr-batch-size",
        settings["kr_batch_size"] or "8",
        "--us-batch-size",
        settings["us_batch_size"] or "6",
        "--kr-cycle-min",
        settings["kr_cycle_min"] or "4",
        "--us-cycle-min",
        settings["us_cycle_min"] or "15",
        "--kr-scan-markets",
        settings["kr_scan_markets"] or "KOSPI,KOSDAQ",
        "--kr-scan-top",
        settings["kr_scan_top"] or "1000",
        "--kr-scan-candidate-limit",
        settings["kr_scan_candidate_limit"] or "30",
        "--kr-full-scan-interval-min",
        settings["kr_full_scan_interval_min"] or "5",
        *optional_arg("--kr-total-budget", settings.get("kr_total_budget")),
        *optional_arg("--us-total-budget", settings.get("us_total_budget")),
    ]


def optional_arg(name: str, value: str | None) -> list[str]:
    return [name, value] if value else []


def build_command_preview(settings: dict[str, str]) -> str:
    return " ".join(f'"{part}"' if " " in part else part for part in build_supervisor_command(settings))


def main() -> int:
    port = 8765
    server = ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
    print(f"Dashboard running: http://127.0.0.1:{port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
