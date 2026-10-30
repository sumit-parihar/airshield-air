"""Explicit one-shot ThingSpeak history import for analytics."""

import argparse
import asyncio

from app.config import get_settings
from app.database import SessionLocal
from app.ingestion import ThingSpeakIngestionService


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Persist valid recent ThingSpeak feeds as historical analytics data without evaluating live alerts."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=8000,
        help="Number of most recent channel feeds to request (1-8000; default 8000).",
    )
    args = parser.parse_args()
    if not 1 <= args.limit <= 8000:
        parser.error("--limit must be between 1 and 8000")

    settings = get_settings()
    result = asyncio.run(ThingSpeakIngestionService(settings, SessionLocal).backfill_history_once(args.limit))
    print(
        "historical_backfill "
        f"request_succeeded={result.request_succeeded} received={result.received} "
        f"inserted={result.inserted} duplicates={result.duplicates}"
    )
    return 0 if result.request_succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
