"""Local CSV pre-screening for reducing KIS API calls."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from strategy.legacy_rulebook import _add_indicators


DEFAULT_5M_CSV = Path("data") / "market_ohlcv" / "kr_top200_5m.csv"


@dataclass(frozen=True)
class PrescreenCandidate:
    symbol: str
    name: str
    score: int
    reasons: list[str]


def prescreen_from_5m_csv(
    universe_symbols: list[str],
    held_symbols: set[str],
    csv_path: Path = DEFAULT_5M_CSV,
    candidate_limit: int = 5,
) -> list[str]:
    """Return held symbols plus the best local 5-minute candidates.

    This is only a traffic reducer. Final decisions still use fresh KIS data.
    """
    if candidate_limit <= 0:
        return _dedupe([*held_symbols])
    if not csv_path.exists():
        return _dedupe([*held_symbols, *universe_symbols[:candidate_limit]])

    df = pd.read_csv(csv_path, dtype={"code": str})
    df = df[df["code"].isin(universe_symbols)]

    candidates: list[PrescreenCandidate] = []
    for symbol, rows in df.groupby("code", sort=False):
        candidate = _score_symbol(rows)
        if candidate:
            candidates.append(candidate)

    candidates.sort(key=lambda item: item.score, reverse=True)
    selected = [candidate.symbol for candidate in candidates[:candidate_limit]]
    if len(selected) < candidate_limit:
        selected.extend(universe_symbols[: candidate_limit - len(selected)])
    return _dedupe([*held_symbols, *selected])


def describe_prescreen(
    universe_symbols: list[str],
    csv_path: Path = DEFAULT_5M_CSV,
    top_n: int = 10,
) -> list[PrescreenCandidate]:
    if not csv_path.exists():
        return []

    df = pd.read_csv(csv_path, dtype={"code": str})
    df = df[df["code"].isin(universe_symbols)]
    candidates = [candidate for _, rows in df.groupby("code", sort=False) if (candidate := _score_symbol(rows))]
    candidates.sort(key=lambda item: item.score, reverse=True)
    return candidates[:top_n]


def _score_symbol(rows: pd.DataFrame) -> PrescreenCandidate | None:
    rows = rows.rename(columns={"code": "symbol"}).copy()
    rows = rows[["symbol", "name", "datetime", "open", "high", "low", "close", "volume"]]
    for column in ["open", "high", "low", "close", "volume"]:
        rows[column] = pd.to_numeric(rows[column], errors="coerce")
    rows = rows.dropna().sort_values("datetime").reset_index(drop=True)
    if len(rows) < 35:
        return None

    df = _add_indicators(rows)
    prev = df.iloc[-2]
    curr = df.iloc[-1]

    reasons: list[str] = []
    if prev["ma5"] <= prev["ma20"] and curr["ma5"] > curr["ma20"]:
        reasons.append("5m ma cross")
    if prev["low"] <= prev["bb_lower"] and curr["close"] > curr["open"]:
        reasons.append("5m bb bounce")
    if (prev["rsi14"] <= 30 < curr["rsi14"]) or (prev["rsi14"] < 50 <= curr["rsi14"]):
        reasons.append("5m rsi cross")
    if prev["macd"] <= prev["macd_signal"] and curr["macd"] > curr["macd_signal"]:
        reasons.append("5m macd cross")
    if curr["volume"] > curr["volume_ma5"]:
        reasons.append("5m volume ok")

    return PrescreenCandidate(
        symbol=str(curr["symbol"]).zfill(6),
        name=str(curr["name"]),
        score=len(reasons),
        reasons=reasons or ["no local trigger"],
    )


def _dedupe(symbols: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for symbol in symbols:
        normalized = str(symbol).zfill(6)
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result
