"""Command-line entry point: ``viron-usd``."""

from __future__ import annotations

import argparse
import logging
import math
import sys
from pathlib import Path

from . import __version__
from .client import DEFAULT_URL, TOKEN_ENV_VAR, VironClient, VironError
from .chunks import DEFAULT_CHUNK_SIZE
from .writer import DEFAULT_TICKS_PER_SECOND

log = logging.getLogger("viron_usd")


def _positive_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise argparse.ArgumentTypeError("must be a finite number")
    if value <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return value


def _non_negative_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise argparse.ArgumentTypeError("must be a finite number")
    if value < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return value


def _non_negative_int(text: str) -> int:
    value = int(text)
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
        "--chunk-size", type=_non_negative_int, default=DEFAULT_CHUNK_SIZE, metavar="N",
        help=(
            f"ticks per chunk layer, stitched into run.usda with value clips "
            f"(default {DEFAULT_CHUNK_SIZE}; 0 writes one unchunked layer)"
        ),
    )
    rec.add_argument(
        "--binary", action="store_true",
        help="write the time-sampled layers (chunks, or the single layer) as binary .usdc instead of text .usda",
    )

    flat = sub.add_parser(
        "flatten",
        help="bake a recorded run (value clips included) into one layer",
        description=(
            "Flatten RUN (e.g. run/run.usda) into a single layer with every time sample resolved "
            "and no value-clip metadata, for tools that do not read value clips (e.g. Blender)."
        ),
    )
    flat.add_argument("run", metavar="RUN", help="the run's root layer, e.g. run/run.usda")
    flat.add_argument(
        "-o", "--output", default=None, metavar="OUT",
        help="output layer (default: run_flat.usda next to RUN, or run_flat.usdc with --binary)",
    )
    flat.add_argument("--binary", action="store_true", help="write binary .usdc instead of text .usda")
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
            chunk_size=args.chunk_size or None,
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
    path = Path(args.out) / "run.usda" if args.chunk_size else run_layer_path(args.out, args.binary)
    print(f"recorded {recorder.captures} tick(s) to {path}")
    return status


def _flatten(args: argparse.Namespace) -> int:
    from .chunks import flatten

    if not Path(args.run).is_file():
        print(f"viron-usd: error: {args.run}: no such file", file=sys.stderr)
        return 1
    try:
        out = flatten(args.run, args.output, binary=args.binary)
    except (ValueError, OSError) as exc:
        print(f"viron-usd: error: {exc}", file=sys.stderr)
        return 2 if isinstance(exc, ValueError) else 1
    print(f"wrote {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    logging.basicConfig(level=logging.WARNING, format="%(name)s: %(levelname)s: %(message)s")
    if args.command == "record":
        return _record(args)
    if args.command == "flatten":
        return _flatten(args)
    parser.error(f"unknown command {args.command}")  # pragma: no cover
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
