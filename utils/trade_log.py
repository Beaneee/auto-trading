"""JSONL trade log for bot-submitted orders."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


LOG_DIR = Path("logs") / "trades"


def log_order(market: str, order: dict[str, Any], result: dict[str, Any]) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    path = LOG_DIR / f"{market}_{now:%Y%m%d}.jsonl"
    record = {
        "timestamp": now.isoformat(timespec="seconds"),
        "market": market,
        "order": order,
        "result": result,
        "accepted": result.get("rt_cd") == "0",
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def read_orders(market: str, yyyymmdd: str) -> list[dict[str, Any]]:
    path = LOG_DIR / f"{market}_{yyyymmdd}.jsonl"
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows
