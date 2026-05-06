"""Run US and Korean market sessions in one long-lived terminal process."""
from __future__ import annotations

import argparse
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
import subprocess
import sys
import time


ROOT_DIR = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run KR and US market sessions for multiple days.")
    parser.add_argument("--days", type=float, default=3.0, help="How long to keep running. Default: 3 days.")
    parser.add_argument("--continue-on-error", action="store_true", help="Keep waiting for later sessions after a session error.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    end_at = datetime.now() + timedelta(days=args.days)
    print(f"Supervisor started: {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"Supervisor will stop after: {end_at:%Y-%m-%d %H:%M:%S}")
    print()

    exit_code = 0

    while datetime.now() < end_at:
        now = datetime.now()
        next_us_open = next_time("22:30", now)
        next_kr_open = next_time("09:00", now)

        if is_between(now, "22:30", "05:00"):
            exit_code = max(exit_code, run_us_session(wait_for_open=False))
            if exit_code and not args.continue_on_error:
                return exit_code
            continue

        if is_between(now, "09:00", "15:30"):
            exit_code = max(exit_code, run_kr_session(wait_for_open=False))
            if exit_code and not args.continue_on_error:
                return exit_code
            continue

        if next_us_open < next_kr_open:
            if next_us_open > end_at:
                break
            wait_until(next_us_open, "US market open")
            exit_code = max(exit_code, run_us_session(wait_for_open=False))
        else:
            if next_kr_open > end_at:
                break
            wait_until(next_kr_open, "Korean market open")
            exit_code = max(exit_code, run_kr_session(wait_for_open=False))

        if exit_code and not args.continue_on_error:
            return exit_code

        print("Session finished. Waiting for the next market window.")

    print(f"Supervisor stopped normally: {datetime.now():%Y-%m-%d %H:%M:%S}")
    return exit_code


def run_us_session(wait_for_open: bool) -> int:
    cmd = [
        PYTHON,
        "scripts/run_us_rulebook_once.py",
        "--execute",
        "--until-us-close",
        "--batch-size",
        "6",
        "--cycle-min",
        "15",
        "--repeat-interval-min",
        "0.1",
        "--report-on-close",
    ]
    if wait_for_open:
        cmd.append("--wait-for-us-open")
    return run(cmd, "US market session")


def run_kr_session(wait_for_open: bool) -> int:
    cmd = [
        PYTHON,
        "scripts/run_legacy_rulebook_once.py",
        "--watchlist",
        "data/watchlists/rulebook_focus.csv",
        "--append-universe",
        "--limit",
        "100",
        "--no-prescreen",
        "--batch-size",
        "8",
        "--cycle-min",
        "4",
        "--execute",
        "--until-kr-close",
        "--repeat-interval-min",
        "0.1",
        "--report-on-close",
    ]
    if wait_for_open:
        cmd.append("--wait-for-kr-open")
    return run(cmd, "Korean market session")


def run(cmd: list[str], label: str) -> int:
    print("=" * 72)
    print(f"Starting {label}: {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("=" * 72)
    result = subprocess.run(cmd, cwd=ROOT_DIR)
    print(f"{label} exited with code {result.returncode}")
    return result.returncode


def next_time(value: str, now: datetime) -> datetime:
    hour, minute = value.split(":", maxsplit=1)
    target = datetime.combine(now.date(), dt_time(hour=int(hour), minute=int(minute)))
    if target <= now:
        target += timedelta(days=1)
    return target


def is_between(now: datetime, start: str, end: str) -> bool:
    start_at = datetime.combine(now.date(), _parse_time(start))
    end_at = datetime.combine(now.date(), _parse_time(end))
    if end_at <= start_at:
        return now >= start_at or now < end_at
    return start_at <= now < end_at


def _parse_time(value: str) -> dt_time:
    hour, minute = value.split(":", maxsplit=1)
    return dt_time(hour=int(hour), minute=int(minute))


def wait_until(target: datetime, label: str) -> None:
    while True:
        seconds = (target - datetime.now()).total_seconds()
        if seconds <= 0:
            return
        minutes = seconds / 60
        print(f"Waiting for {label}: {target:%Y-%m-%d %H:%M:%S} ({minutes:.1f} min left)")
        time.sleep(min(seconds, 300))


if __name__ == "__main__":
    raise SystemExit(main())
