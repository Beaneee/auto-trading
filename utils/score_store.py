"""Persist latest score snapshots for dashboard display."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


SCORE_PATH = Path(".cache") / "score_store.json"


def load_scores(path: Path = SCORE_PATH) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_score(market: str, symbol: str, score_type: str, score: int, details: list, reasons: list[str]) -> None:
    data = load_scores()
    key = f"{market}:{symbol}"
    data[key] = {
        "market": market,
        "symbol": symbol,
        "type": score_type,
        "score": score,
        "details": [{"label": label, "points": points} for label, points in details],
        "reasons": reasons,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    SCORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCORE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
