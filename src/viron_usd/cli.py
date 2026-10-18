"""Command-line entry point: ``viron-usd``."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .client import DEFAULT_URL, TOKEN_ENV_VAR, VironClient, VironError
from .writer import DEFAULT_TICKS_PER_SECOND

log = logging.getLogger("viron_usd")


def _positive_float(text: str) -> float:
    value = float(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return value


def _non_negative_float(text: str) -> float:
    value = float(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return value


def _positive_int(text: str) -> int:
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="viron-usd",
        description="Record a Viron world tick by tick as a time-sampled OpenUSD stage.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    rec = sub.add_parser(
        "record",
        help="poll a Viron environment on an interval and record it (poll mode)",
        description=(
            "Capture a Viron environment every --interval seconds, numbering ticks from 0, "
            "and write the run to --out. Only GET requests are sent to Viron. "
            "Ctrl-C stops recording and still saves the stage."
        ),
    )
    rec.add_argument("--viron-url", default=DEFAULT_URL, help=f"Viron base URL (default {DEFAULT_URL})")
    rec.add_argument(
        "--token",
        default=None,
        help=f"JWT bearer token for Viron (default: ${TOKEN_ENV_VAR})",
    )
    rec.add_argument("--environment", type=int, required=True, metavar="ID", help="Viron environment id to record")
    rec.add_argument(
        "--interval", type=_non_negative_float, default=1.0, metavar="SECONDS",
        help="wall-clock seconds between captures (default 1.0)",
    )
    rec.add_argument(
        "--ticks", type=_positive_int, default=None, metavar="N",
        help="stop after N captures (default: run until Ctrl-C)",
    )
    rec.add_argument("--out", default="run", metavar="DIR", help="output directory (default ./run)")
    rec.add_argument(
        "--ticks-per-second", type=_positive_float, default=DEFAULT_TICKS_PER_SECOND, metavar="N",
        help=f"playback rate: time codes (= frames) per second (default {DEFAULT_TICKS_PER_SECOND:g})",
    )
    rec.add_argument(
        "--binary", action="store_true",
        help="write the time-sampled layer as binary .usdc instead of text .usda",
    )
    return parser


def _record(args: argparse.Namespace) -> int:
    from .recorder import Recorder, run_layer_path

    client = VironClient(args.viron_url, args.token)
    if not client.token:
        log.warning("no token given (--token or $%s); Viron will reject the requests", TOKEN_ENV_VAR)
    try:
        recorder = Recorder.create(
            client, args.environment, args.out,
            ticks_per_second=args.ticks_per_second, binary=args.binary,
        )
    except VironError as exc:
        print(f"viron-usd: error: {exc}", file=sys.stderr)
        return 1
    status = 0
    try:
        recorder.run_polling(args.interval, args.ticks)
    except KeyboardInterrupt:
        print("viron-usd: interrupted; saving the recording", file=sys.stderr)
        status = 130
    except VironError as exc:
        print(f"viron-usd: error: {exc}; saving what was recorded", file=sys.stderr)
        status = 1
    finally:
        recorder.close()
    path = run_layer_path(args.out, args.binary)
    print(f"recorded {recorder.captures} tick(s) to {path}")
    return status


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    logging.basicConfig(level=logging.WARNING, format="%(name)s: %(levelname)s: %(message)s")
    if args.command == "record":
        return _record(args)
    parser.error(f"unknown command {args.command}")  # pragma: no cover
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
