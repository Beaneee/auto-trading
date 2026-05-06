"""Tier-2 watchlist management for Korean market scanning."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


DEFAULT_SYMBOLS = ["005930", "000660", "012450"]
WATCHLIST_PATH = Path(".cache") / "kr_tier2_watchlist.json"
MAX_SYMBOLS = 30


def load_tier2_symbols(path: Path = WATCHLIST_PATH, max_symbols: int = MAX_SYMBOLS) -> list[str]:
    data = _load(path)
    dynamic = data.get("symbols", [])
    return _dedupe([*DEFAULT_SYMBOLS, *dynamic])[:max_symbols]


def add_tier2_symbol(symbol: str, reason: str, path: Path = WATCHLIST_PATH, max_symbols: int = MAX_SYMBOLS) -> None:
    symbol = str(symbol).zfill(6)
    data = _load(path)
    symbols = _dedupe([symbol, *data.get("symbols", [])])
    data["symbols"] = symbols[:max_symbols]
    data.setdefault("events", []).append(
        {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "symbol": symbol,
            "reason": reason,
        }
    )
    _save(path, data)


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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
