"""Ties snapshots to a writer: driven mode (``capture``) and poll mode (``run_polling``)."""

from __future__ import annotations

import logging
import math
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from .client import VironClient
from .snapshot import Snapshot, WorldLayout, take_snapshot
from .writer import DEFAULT_TICKS_PER_SECOND, TimeSampledWriter

log = logging.getLogger(__name__)

RUN_LAYER = "run"


def run_layer_path(out_dir: str | Path, binary: bool = False) -> Path:
    """Path of the single layer of an unchunked recording inside ``out_dir``."""
    return Path(out_dir) / f"{RUN_LAYER}.{'usdc' if binary else 'usda'}"


class Recorder:
    """Records one Viron environment.

    ``writer`` is any object with ``layout``, ``write(snapshot)`` and ``close()`` — a
    :class:`~viron_usd.writer.TimeSampledWriter` for a single layer.

    *Driven mode:* the simulation calls :meth:`capture` after each of its ticks, optionally with
    per-entity ``extras``. *Poll mode:* :meth:`run_polling` captures on a wall-clock interval,
    numbering ticks from 0. The recorder only ever reads from Viron.
    """

    def __init__(
        self,
        client: VironClient,
        environment_id: int,
        writer: Any,
        *,
        clock: Callable[[], float] = time.time,
    ):
        if writer.layout.environment_id != int(environment_id):
            raise ValueError("writer layout is for a different environment")
        self.client = client
        self.environment_id = int(environment_id)
        self.writer = writer
        self.clock = clock
        self.last_tick: int | None = None
        self.captures = 0
        self.closed = False

    @classmethod
    def create(
        cls,
        client: VironClient,
        environment_id: int,
        out_dir: str | Path,
        *,
        ticks_per_second: float = DEFAULT_TICKS_PER_SECOND,
        binary: bool = False,
        clock: Callable[[], float] = time.time,
    ) -> "Recorder":
        """Fetch the environment's layout and record into ``out_dir/run.usda`` (``.usdc`` with
        ``binary``)."""
        layout = WorldLayout.fetch(client, environment_id)
        path = run_layer_path(out_dir, binary)
        path.parent.mkdir(parents=True, exist_ok=True)
        writer = TimeSampledWriter.create(path, layout, ticks_per_second=ticks_per_second)
        return cls(client, environment_id, writer, clock=clock)

    @property
    def layout(self) -> WorldLayout:
        return self.writer.layout

    def capture(self, tick: int, extras: Mapping[int, Mapping[str, Any]] | None = None) -> Snapshot:
        """Read Viron's current state and record it at time code ``tick``.

        Ticks must increase from call to call. ``extras`` maps entity id → ``{key: value}``
        and is written as time-sampled ``extras:<key>`` attributes.
        """
        if self.closed:
            raise RuntimeError("recorder is closed")
        tick = int(tick)
        if self.last_tick is not None and tick <= self.last_tick:
            raise ValueError(f"tick {tick} is not after the previous tick {self.last_tick}")
        snapshot = take_snapshot(self.client, self.layout, tick, extras=extras, clock=self.clock)
        self.writer.write(snapshot)
        self.last_tick = tick
        self.captures += 1
        return snapshot

    def run_polling(
        self,
        interval: float,
        ticks: int | None = None,
        *,
        sleep: Callable[[float], None] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> int:
        """Capture every ``interval`` seconds, numbering ticks from 0 (or after the last tick).

        Stops after ``ticks`` captures, or runs until interrupted when ``ticks`` is ``None``.
        Returns the number of captures made. Poll mode records no extras.
        """
        sleep = sleep or time.sleep
        monotonic = monotonic or time.monotonic
        if not math.isfinite(interval) or interval < 0:
            raise ValueError("interval must be a finite number of seconds, not negative")
        if ticks is not None and ticks < 0:
            raise ValueError("ticks must not be negative")
        tick = 0 if self.last_tick is None else self.last_tick + 1
        made = 0
        deadline = monotonic()
        while ticks is None or made < ticks:
            self.capture(tick)
            made += 1
            tick += 1
            if ticks is not None and made >= ticks:
                break
            deadline += interval
            delay = deadline - monotonic()
            if delay > 0:
                sleep(delay)
            else:
                deadline = monotonic()  # fell behind: do not try to catch up with a burst
        return made

    def close(self) -> None:
        if not self.closed:
            self.writer.close()
            self.closed = True

    def __enter__(self) -> "Recorder":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
