"""External Korean-market scanner that writes candidate signals for the bot.

This scanner intentionally does not use KIS for broad discovery. It crawls
Naver Finance data, scores many symbols locally, and writes only candidates to
signals/inbox/kr_candidates.jsonl. The trading bot can then use KIS only for
holdings, final confirmation, and orders.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from strategy.legacy_rulebook import _add_indicators, _safe_ratio


OUTBOX = ROOT_DIR / "signals" / "inbox" / "kr_candidates.jsonl"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
MARKETS = {"KOSPI": 0, "KOSDAQ": 1}


@dataclass(frozen=True)
class Stock:
    market: str
    rank: int
    code: str
    name: str


@dataclass(frozen=True)
class Candidate:
    code: str
    name: str
    market: str
    rank: int
    score: int
    reasons: list[str]
    price: float


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scan Korean stocks externally and emit buy candidates.")
    parser.add_argument("--markets", default="KOSPI,KOSDAQ", help="Comma list: KOSPI,KOSDAQ. Default: both")
    parser.add_argument("--top", type=int, default=1000, help="Total max symbols to scan after market merge. Default: 1000")
    parser.add_argument("--per-market-top", type=int, help="Optional max symbols per market before merge.")
    parser.add_argument("--candidate-limit", type=int, default=30, help="Max candidates to write. Default: 30")
    parser.add_argument("--min-score", type=int, default=2, help="Minimum scanner score. Default: 2")
    parser.add_argument("--workers", type=int, default=16, help="Crawler threads. Default: 16")
    parser.add_argument("--count", type=int, default=120, help="Minute bars to fetch per symbol. Default: 120")
    parser.add_argument("--out", default=str(OUTBOX), help="Output JSONL path.")
    parser.add_argument("--repeat", action="store_true", help="Repeat until stopped.")
    parser.add_argument("--scan-interval-min", type=float, default=5.0, help="Repeat interval minutes. Default: 5")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.repeat:
        while True:
            run_once(args)
            print(f"Next scanner run in {args.scan_interval_min:g} minute(s).")
            time.sleep(args.scan_interval_min * 60)
    run_once(args)
    return 0


def run_once(args: argparse.Namespace) -> None:
    started = datetime.now()
    stocks = load_universe(args)
    print(f"Scanner universe: {len(stocks)} symbols")

    candidates: list[Candidate] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(scan_stock, stock, args.count): stock for stock in stocks}
        for future in as_completed(futures):
            stock = futures[future]
            try:
                candidate = future.result()
            except Exception as exc:
                print(f"SCAN FAIL {stock.code} {stock.name}: {exc}")
                continue
            if candidate and candidate.score >= args.min_score:
                candidates.append(candidate)

    candidates.sort(key=lambda item: (item.score, -item.rank), reverse=True)
    candidates = candidates[: args.candidate_limit]
    write_candidates(candidates, Path(args.out), started)
    print(f"Scanner candidates written: {len(candidates)} -> {args.out}")
    for item in candidates:
        print(f"  {item.code} {item.name} score={item.score} price={item.price:g} | {'; '.join(item.reasons)}")


def load_universe(args: argparse.Namespace) -> list[Stock]:
    requested = [item.strip().upper() for item in args.markets.split(",") if item.strip()]
    stocks: list[Stock] = []
    for market in requested:
        if market not in MARKETS:
            raise ValueError(f"Unknown market: {market}")
        limit = args.per_market_top or args.top
        stocks.extend(fetch_market_symbols(MARKETS[market], market, limit))
    stocks.sort(key=lambda item: (item.rank, item.market))
    return dedupe_stocks(stocks)[: args.top]


def fetch_market_symbols(sosok: int, market: str, limit: int) -> list[Stock]:
    stocks: list[Stock] = []
    seen: set[str] = set()
    page = 1
    while len(stocks) < limit:
        url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={page}"
        html = fetch_text(url, encoding="euc-kr")
        pairs = re.findall(r'<a href="/item/main\.naver\?code=(\d{6})" class="tltle">([^<]+)</a>', html)
        if not pairs:
            break
        for code, name in pairs:
            if code in seen:
                continue
            seen.add(code)
            stocks.append(Stock(market=market, rank=len(stocks) + 1, code=code, name=name.strip()))
            if len(stocks) >= limit:
                break
        page += 1
        time.sleep(0.05)
    return stocks


def scan_stock(stock: Stock, count: int) -> Candidate | None:
    df = fetch_minute_ohlcv(stock.code, count)
    if len(df) < 35:
        return None
    df.insert(0, "name", stock.name)
    df.insert(0, "code", stock.code)
    scored = _score_rows(df)
    if not scored:
        return None
    score, reasons, price = scored
    return Candidate(stock.code, stock.name, stock.market, stock.rank, score, reasons, price)


def fetch_minute_ohlcv(code: str, count: int) -> pd.DataFrame:
    params = {"symbol": code, "requestType": 0, "count": count, "timeframe": "minute"}
    text = fetch_text("https://api.finance.naver.com/siseJson.naver?" + urlencode(params), encoding="utf-8")
    rows = ast.literal_eval(text.strip().replace("null", "None"))
    if len(rows) <= 1:
        return pd.DataFrame()
    data = rows[1:]
    # Naver minute rows are date, open, high, low, close, volume, foreign ratio.
    df = pd.DataFrame(data).iloc[:, :6]
    df.columns = ["datetime", "open", "high", "low", "close", "volume"]
    df["datetime"] = pd.to_datetime(df["datetime"].astype(str), format="%Y%m%d%H%M", errors="coerce")
    for column in ["open", "high", "low", "close", "volume"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.dropna().sort_values("datetime").reset_index(drop=True)
    if df.empty:
        return df

    # Volume is cumulative in this endpoint, so convert to per-bar volume.
    volume = df["volume"].fillna(0)
    interval_volume = volume.diff()
    interval_volume.iloc[0] = volume.iloc[0]
    df["volume"] = interval_volume.clip(lower=0)
    return df


def _score_rows(rows: pd.DataFrame) -> tuple[int, list[str], float] | None:
    df = _add_indicators(rows)
    prev = df.iloc[-2]
    curr = df.iloc[-1]

    reasons: list[str] = []
    volume_ratio = _safe_ratio(curr["volume"], curr["volume_ma20"])
    body_position = _safe_ratio(curr["close"] - curr["low"], curr["high"] - curr["low"])
    prior_high = df["high"].iloc[-6:-1].max()

    if volume_ratio >= 1.8:
        reasons.append(f"volume surge {volume_ratio:.1f}x")
    if curr["close"] > curr["open"] and body_position >= 0.65:
        reasons.append("bullish close near high")
    if curr["close"] > prior_high:
        reasons.append("breakout above recent high")
    if curr["close"] > curr["ma5"] and curr["ma5"] >= curr["ma20"]:
        reasons.append("price above rising short trend")
    if curr["rsi14"] > prev["rsi14"] and curr["rsi14"] >= 50:
        reasons.append("rsi improving above 50")
    if curr["macd_hist"] > prev["macd_hist"] and curr["macd_hist"] > 0:
        reasons.append("macd momentum expanding")

    return len(reasons), reasons or ["no trigger"], float(curr["close"])


def write_candidates(candidates: list[Candidate], path: Path, started: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for item in candidates:
            handle.write(
                json.dumps(
                    {
                        "timestamp": started.isoformat(timespec="seconds"),
                        "code": item.code,
                        "name": item.name,
                        "market": item.market,
                        "rank": item.rank,
                        "score": item.score,
                        "reasons": item.reasons,
                        "price": item.price,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


def fetch_text(url: str, encoding: str) -> str:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=10) as resp:
        return resp.read().decode(encoding, errors="ignore")


def dedupe_stocks(stocks: list[Stock]) -> list[Stock]:
    seen: set[str] = set()
    result: list[Stock] = []
    for stock in stocks:
        if stock.code in seen:
            continue
        seen.add(stock.code)
        result.append(stock)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
