"""Command-line entry point: ``callreport run`` and ``callreport periods``."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import __version__
from .config import ConfigError, load_config
from .export import export, run_metadata
from .ffiec import Credentials, FFIECClient, FFIECError, RateLimiter
from .periods import select_periods
from .pipeline import extract

log = logging.getLogger("callreport")

EXIT_OK, EXIT_ERROR, EXIT_PARTIAL = 0, 1, 2


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        return args.handler(args)
    except (ConfigError, FFIECError, ValueError) as exc:
        log.error("%s", exc, exc_info=args.verbose)
        return EXIT_ERROR
    except KeyboardInterrupt:
        log.error("Interrupted")
        return EXIT_ERROR


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="callreport", description="Extract FFIEC Call Report data.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    commands = parser.add_subparsers(required=True)

    run = commands.add_parser("run", help="download filings and write the dataset")
    run.add_argument("--config", type=Path, required=True, help="TOML config file")
    run.add_argument("--start", help="first quarter, e.g. 2012Q1 (overrides config)")
    run.add_argument("--end", help="last quarter or 'latest' (overrides config)")
    run.add_argument("--out-dir", type=Path, dest="output_dir", help="output directory (overrides config)")
    run.add_argument("--format", choices=["xlsx", "csv"], dest="output_format", help="output format")
    run.add_argument("--workers", type=int, help="concurrent requests (overrides config)")
    run.set_defaults(handler=_run)

    periods = commands.add_parser("periods", help="list the Call Report periods FFIEC has published")
    periods.set_defaults(handler=_periods)
    return parser


def _client(requests_per_hour: float = 2400) -> FFIECClient:
    credentials = Credentials.from_env()
    expires = credentials.expires_at
    if expires and expires - datetime.now(UTC) < timedelta(days=7):
        log.warning("FFIEC bearer token expires %s; renew it in the CDR portal soon.", f"{expires:%Y-%m-%d}")
    return FFIECClient(credentials, rate_limiter=RateLimiter(requests_per_hour))


def _periods(args: argparse.Namespace) -> int:
    with _client() as client:
        for period in client.reporting_periods():
            print(period.isoformat())
    return EXIT_OK


def _run(args: argparse.Namespace) -> int:
    config = load_config(
        args.config,
        start=args.start,
        end=args.end,
        output_dir=args.output_dir,
        output_format=args.output_format,
        workers=args.workers,
    )
    started = time.monotonic()
    with _client(config.requests_per_hour) as client:
        periods = select_periods(client.reporting_periods(), config.start, config.end)
        total = len(periods) * len(config.banks)
        log.info(
            "%d banks x %d quarters (%s to %s) = %d filings, %d fields, %d workers",
            len(config.banks),
            len(periods),
            min(periods),
            max(periods),
            total,
            len(config.fields),
            config.workers,
        )
        result = extract(
            client,
            config.banks,
            config.fields,
            periods,
            workers=config.workers,
            on_progress=_progress_logger(started),
        )

    paths = export(
        result,
        config.fields,
        config.output_dir,
        config.output_name,
        config.output_format,
        run_metadata(periods, args.config),
    )
    statuses = Counter(r.status.value for r in result.rows)
    reasons = Counter(m.reason.value for m in result.missing)
    log.info("Filings: %s", dict(statuses))
    log.info("Missing values by reason: %s", dict(reasons) or "none")
    for path in paths:
        log.info("Wrote %s", path)
    log.info("Done in %.1f minutes", (time.monotonic() - started) / 60)

    if result.failed:
        log.error(
            "%d requests failed; their rows are marked request_failed. Rerun to retry.", len(result.failed)
        )
        return EXIT_PARTIAL
    return EXIT_OK


def _progress_logger(started: float):
    def report(done: int, total: int) -> None:
        if done % 25 == 0 or done == total:
            elapsed = time.monotonic() - started
            remaining = elapsed / done * (total - done)
            log.info("Progress %d/%d, about %.0f min remaining", done, total, remaining / 60)

    return report


if __name__ == "__main__":
    sys.exit(main())
