from __future__ import annotations

import ast
import csv
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd


BASE_DIR = Path(__file__).resolve().parents[1]
OUT_DIR = BASE_DIR / "data" / "market_ohlcv"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

FUND_LIKE_PREFIXES = (
    "ACE ",
    "ARIRANG ",
    "BNK ",
    "HANARO ",
    "HK ",
    "KBSTAR ",
    "KODEX ",
    "KOSEF ",
    "RISE ",
    "SOL ",
    "TIGER ",
    "TIMEFOLIO ",
    "TREX ",
    "WON ",
)


@dataclass(frozen=True)
class Stock:
    market: str
    rank: int
    code: str
    name: str


def fetch_text(url: str, encoding: str = "utf-8") -> str:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=20) as resp:
        return resp.read().decode(encoding, errors="ignore")


def fetch_market_top100(sosok: int, market: str) -> list[Stock]:
    stocks: list[Stock] = []
    seen: set[str] = set()
    for page in range(1, 8):
        url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={page}"
        html = fetch_text(url, encoding="euc-kr")
        pairs = re.findall(
            r'<a href="/item/main\.naver\?code=(\d{6})" class="tltle">([^<]+)</a>',
            html,
        )
        for code, name in pairs:
            clean_name = name.strip()
            if clean_name.startswith(FUND_LIKE_PREFIXES) or " ETN" in clean_name or clean_name.endswith("ETN"):
                continue
            if code in seen:
                continue
            seen.add(code)
            stocks.append(Stock(market=market, rank=len(stocks) + 1, code=code, name=clean_name))
            if len(stocks) >= 100:
                return stocks
        time.sleep(0.15)
    return stocks[:100]


def parse_sise_json(text: str) -> pd.DataFrame:
    cleaned = text.strip()
    if not cleaned:
        return pd.DataFrame()
    cleaned = cleaned.replace("null", "None")
    rows = ast.literal_eval(cleaned)
    if len(rows) <= 1:
        return pd.DataFrame()
    headers = rows[0]
    return pd.DataFrame(rows[1:], columns=headers)


def fetch_sise(code: str, timeframe: str, count: int = 500, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    params = {"symbol": code, "timeframe": timeframe}
    if start and end:
        params.update({"requestType": 1, "startTime": start, "endTime": end})
    else:
        params.update({"requestType": 0, "count": count})
    url = "https://api.finance.naver.com/siseJson.naver?" + urlencode(params)
    return parse_sise_json(fetch_text(url, encoding="utf-8"))


def normalize_minute(df: pd.DataFrame, stock: Stock) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.rename(
        columns={
            "날짜": "datetime",
            "시가": "open",
            "고가": "high",
            "저가": "low",
            "종가": "close",
            "거래량": "volume",
            "외국인소진율": "foreign_ownership_ratio",
        }
    )
    df["datetime"] = pd.to_datetime(df["datetime"].astype(str), format="%Y%m%d%H%M", errors="coerce")
    for col in ["open", "high", "low", "close", "volume", "foreign_ownership_ratio"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["datetime", "close"]).sort_values("datetime")
    df["trade_date"] = df["datetime"].dt.strftime("%Y-%m-%d")
    latest_date = df["trade_date"].max()
    df = df[df["trade_date"] == latest_date].copy()
    for col in ["open", "high", "low"]:
        df[col] = df[col].fillna(df["close"])
    if "volume" in df.columns:
        cumulative_volume = df["volume"].fillna(0)
        interval_volume = cumulative_volume.diff()
        interval_volume.iloc[0] = cumulative_volume.iloc[0]
        df["volume"] = interval_volume.clip(lower=0)
    df.insert(0, "market", stock.market)
    df.insert(1, "rank", stock.rank)
    df.insert(2, "code", stock.code)
    df.insert(3, "name", stock.name)
    df["datetime"] = df["datetime"].dt.strftime("%Y-%m-%d %H:%M:%S")
    return df[
        [
            "market",
            "rank",
            "code",
            "name",
            "trade_date",
            "datetime",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "foreign_ownership_ratio",
        ]
    ]


def aggregate_5m(df_1m: pd.DataFrame) -> pd.DataFrame:
    if df_1m.empty:
        return df_1m
    work = df_1m.copy()
    work["datetime"] = pd.to_datetime(work["datetime"])
    work["price_for_ohlc"] = work["close"]
    out = (
        work.set_index("datetime")
        .groupby(["market", "rank", "code", "name", "trade_date"])
        .resample("5min", label="right", closed="right")
        .agg(
            open=("price_for_ohlc", "first"),
            high=("price_for_ohlc", "max"),
            low=("price_for_ohlc", "min"),
            close=("price_for_ohlc", "last"),
            volume=("volume", "sum"),
        )
        .dropna(subset=["close"])
        .reset_index()
    )
    out["datetime"] = out["datetime"].dt.strftime("%Y-%m-%d %H:%M:%S")
    return out[["market", "rank", "code", "name", "trade_date", "datetime", "open", "high", "low", "close", "volume"]]


def normalize_daily(df: pd.DataFrame, stock: Stock, target_date: str) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.rename(
        columns={
            "날짜": "trade_date",
            "시가": "open",
            "고가": "high",
            "저가": "low",
            "종가": "close",
            "거래량": "volume",
            "외국인소진율": "foreign_ownership_ratio",
        }
    )
    df["trade_date"] = pd.to_datetime(df["trade_date"].astype(str), format="%Y%m%d", errors="coerce")
    for col in ["open", "high", "low", "close", "volume", "foreign_ownership_ratio"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["trade_date", "close"]).sort_values("trade_date")
    wanted = pd.to_datetime(target_date)
    exact = df[df["trade_date"] == wanted]
    if exact.empty:
        exact = df.tail(1)
    exact = exact.copy()
    exact.insert(0, "market", stock.market)
    exact.insert(1, "rank", stock.rank)
    exact.insert(2, "code", stock.code)
    exact.insert(3, "name", stock.name)
    exact["trade_date"] = exact["trade_date"].dt.strftime("%Y-%m-%d")
    return exact[
        [
            "market",
            "rank",
            "code",
            "name",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "foreign_ownership_ratio",
        ]
    ]


def write_csv(path: Path, rows: pd.DataFrame) -> None:
    rows.to_csv(path, index=False, encoding="utf-8-sig", quoting=csv.QUOTE_MINIMAL)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    stocks = fetch_market_top100(0, "KOSPI") + fetch_market_top100(1, "KOSDAQ")
    if len(stocks) < 200:
        raise RuntimeError(f"Only fetched {len(stocks)} stocks; expected 200")

    stock_rows = pd.DataFrame([s.__dict__ for s in stocks])
    write_csv(OUT_DIR / "stock_universe_top100.csv", stock_rows)

    minute_frames: list[pd.DataFrame] = []
    five_frames: list[pd.DataFrame] = []
    daily_frames: list[pd.DataFrame] = []
    meta_rows: list[dict[str, object]] = []

    for i, stock in enumerate(stocks, start=1):
        print(f"[{i:03d}/{len(stocks)}] {stock.market} {stock.rank:03d} {stock.code} {stock.name}", flush=True)
        try:
            minute_raw = fetch_sise(stock.code, "minute", count=500)
            minute = normalize_minute(minute_raw, stock)
            if minute.empty:
                raise RuntimeError("empty minute data")
            trade_date = str(minute["trade_date"].max())
            daily_raw = fetch_sise(
                stock.code,
                "day",
                start=trade_date.replace("-", ""),
                end=trade_date.replace("-", ""),
            )
            daily = normalize_daily(daily_raw, stock, trade_date)
            five = aggregate_5m(minute)

            minute_frames.append(minute)
            five_frames.append(five)
            daily_frames.append(daily)
            meta_rows.append(
                {
                    "market": stock.market,
                    "rank": stock.rank,
                    "code": stock.code,
                    "name": stock.name,
                    "minute_trade_date": trade_date,
                    "minute_rows": len(minute),
                    "five_minute_rows": len(five),
                    "daily_trade_date": "" if daily.empty else str(daily["trade_date"].iloc[0]),
                    "daily_rows": len(daily),
                    "status": "ok",
                    "error": "",
                }
            )
        except Exception as exc:
            meta_rows.append(
                {
                    "market": stock.market,
                    "rank": stock.rank,
                    "code": stock.code,
                    "name": stock.name,
                    "minute_trade_date": "",
                    "minute_rows": 0,
                    "five_minute_rows": 0,
                    "daily_trade_date": "",
                    "daily_rows": 0,
                    "status": "error",
                    "error": repr(exc),
                }
            )
        time.sleep(0.2)

    minute_all = pd.concat(minute_frames, ignore_index=True) if minute_frames else pd.DataFrame()
    five_all = pd.concat(five_frames, ignore_index=True) if five_frames else pd.DataFrame()
    daily_all = pd.concat(daily_frames, ignore_index=True) if daily_frames else pd.DataFrame()
    meta = pd.DataFrame(meta_rows)

    write_csv(OUT_DIR / "kr_top200_1m.csv", minute_all)
    write_csv(OUT_DIR / "kr_top200_5m.csv", five_all)
    write_csv(OUT_DIR / "kr_top200_daily.csv", daily_all)
    write_csv(OUT_DIR / "fetch_metadata.csv", meta)

    summary = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stocks_requested": len(stocks),
        "stocks_ok": int((meta["status"] == "ok").sum()),
        "stocks_error": int((meta["status"] != "ok").sum()),
        "minute_rows": len(minute_all),
        "five_minute_rows": len(five_all),
        "daily_rows": len(daily_all),
        "minute_dates": ",".join(sorted(meta.loc[meta["minute_trade_date"] != "", "minute_trade_date"].unique())),
        "daily_dates": ",".join(sorted(meta.loc[meta["daily_trade_date"] != "", "daily_trade_date"].unique())),
    }
    pd.DataFrame([summary]).to_csv(OUT_DIR / "summary.csv", index=False, encoding="utf-8-sig")
    print(summary)


if __name__ == "__main__":
    main()
