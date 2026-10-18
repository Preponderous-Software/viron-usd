"""Chunked layers stitched with USD value clips, and ``flatten``.

The clip mechanics (stitching, manifest defaults, flatten) are usd-recorder's; this module keeps
what is specific to viron-usd: chunk naming, rotation with layout re-reads, and the static scene.

A run is written as fixed-size chunk layers (``chunk_NNNN.usda``, or ``.usdc`` when binary),
each holding only its own range of time samples and authoring its own start/end time codes.
``close()`` stitches them with ``UsdUtils.StitchClips`` into ``run.usda`` (plus
``run.topology.usda`` and ``run.manifest.usda``), writes the static scene into the topology
layer, and authors ``visibility = invisible`` as a default on every entity in the manifest so
an entity is hidden in chunks recorded before it first appeared.
See ARCHITECTURE.md § Layer strategy for long runs.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Callable

from pxr import Gf, Sdf, Vt
from usd_recorder import clips as recorder_clips
from usd_recorder.clips import default_flat_path as _default_flat_path
from usd_recorder.clips import has_clips

from . import mapping
from .snapshot import WorldLayout
from .writer import DEFAULT_TICKS_PER_SECOND, TimeSampledWriter, configure_stage, write_static_scene

DEFAULT_CHUNK_SIZE = 1000
ROOT_LAYER = "run.usda"


def chunk_name(index: int, binary: bool = False) -> str:
    return f"chunk_{index:04d}.{'usdc' if binary else 'usda'}"


class ChunkedWriter:
    """Rotates to a new :class:`TimeSampledWriter` every ``chunk_size`` ticks.

    Chunk *k* holds the ticks ``[first + k × chunk_size, first + (k + 1) × chunk_size)`` where
    *first* is the first captured tick. ``refresh_layout`` (optional) is called at each chunk
    boundary to re-read the environment's grids; a grid created mid-chunk appears from the next
    chunk.
    """

    def __init__(
        self,
        out_dir: str | Path,
        layout: WorldLayout,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        ticks_per_second: float = DEFAULT_TICKS_PER_SECOND,
        binary: bool = False,
        refresh_layout: Callable[[], WorldLayout] | None = None,
    ):
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if not math.isfinite(ticks_per_second) or ticks_per_second <= 0:
            raise ValueError("ticks_per_second must be a finite positive number")
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.layout = layout
        self.chunk_size = int(chunk_size)
        self.ticks_per_second = float(ticks_per_second)
        self.binary = binary
        self.refresh_layout = refresh_layout
        self.chunk_paths: list[Path] = []
        self._writer: TimeSampledWriter | None = None
        self._chunk_index: int | None = None
        self._carried: dict[int, dict] = {}
        self._refresh_pending = False
        self.first_tick: int | None = None
        self.last_tick: int | None = None
        self.closed = False

    @property
    def root_path(self) -> Path:
        return self.out_dir / ROOT_LAYER

    def _chunk_of(self, tick: int) -> int:
        assert self.first_tick is not None
        return (tick - self.first_tick) // self.chunk_size

    def begin_tick(self, tick: int) -> None:
        """Rotate to the chunk that holds ``tick`` (re-reading grids) before it is snapshotted."""
        if self.closed:
            raise RuntimeError("writer is closed")
        if self.last_tick is not None and tick <= self.last_tick:
            raise ValueError(f"tick {tick} is not after the previous tick {self.last_tick}")
        if self.last_tick is None:
            # Anchor chunk boundaries to the first tick actually written: an attempted tick whose
            # capture failed must not move them.
            self.first_tick = tick
            if self._writer is not None:
                self._chunk_index = 0
        index = self._chunk_of(tick)
        if self._writer is not None and index == self._chunk_index:
            return
        if self._writer is not None:
            self._finish_current()
            self._refresh_pending = True
        if self._refresh_pending and self.refresh_layout is not None:
            # Retried on the next attempt if it raises. Grids seen earlier keep their index, so
            # their origins (and every position already recorded on them) do not move, and a
            # grid removed mid-run keeps its plane in the stitched scene.
            self.layout = self.layout.merged_with(self.refresh_layout())
        self._refresh_pending = False
        path = self.out_dir / chunk_name(len(self.chunk_paths), self.binary)
        self.chunk_paths.append(path)
        self._writer = TimeSampledWriter.create(
            path, self.layout, ticks_per_second=self.ticks_per_second, static_scene=False,
            carried=self._carried,
        )
        self._chunk_index = index

    def _finish_current(self) -> None:
        """Close the open chunk; a chunk that received no tick (a failed capture) is dropped."""
        assert self._writer is not None
        writer, self._writer = self._writer, None
        self._carried = writer.carry_state()
        writer.close(save=writer.first_tick is not None)
        if writer.first_tick is None:
            path = self.chunk_paths.pop()
            path.unlink(missing_ok=True)

    def write(self, snapshot) -> None:
        self.begin_tick(int(snapshot.tick))
        assert self._writer is not None
        self._writer.write(snapshot)
        self.last_tick = int(snapshot.tick)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self._writer is not None:
            self._finish_current()
        if not self.chunk_paths:
            return
        stitch(self.root_path, self.chunk_paths, self.layout, self.ticks_per_second)


def stitch(root_path: Path, chunk_paths: list[Path], layout: WorldLayout, ticks_per_second: float) -> None:
    """Stitch chunk layers into ``root_path`` + topology + manifest (usd-recorder's ``stitch``,
    which publishes the three layers atomically), with the static scene in the topology layer,
    ``visibility = invisible`` defaults in the manifest, and the stage metadata on the root."""

    def topology(stage) -> None:
        write_static_scene(stage, layout)

    def manifest(layer: Sdf.Layer) -> None:
        _patch_manifest_visibility(layer, layout.environment_id)

    def root(stage) -> None:
        configure_stage(stage, layout)
        stage.SetTimeCodesPerSecond(ticks_per_second)
        stage.SetFramesPerSecond(ticks_per_second)

    recorder_clips.stitch(
        root_path, chunk_paths, mapping.WORLD_PATH, topology=topology, manifest=manifest, root=root
    )


def _patch_manifest_visibility(manifest: Sdf.Layer, environment_id: int) -> None:
    """Manifest defaults used while a clip has no samples for an entity: ``invisible`` (late
    arrivals stay hidden) and, where a driver colour is animated, the id-hash colour (so chunks
    before the first driver colour keep it; defaults inside clip layers are ignored)."""
    entities_path = mapping.entities_path(environment_id)
    if not recorder_clips.default_invisible(manifest, entities_path):
        return
    defaults = {}
    for child in manifest.GetPrimAtPath(entities_path).nameChildren:
        if child.name.startswith("e_"):
            color = Vt.Vec3fArray([Gf.Vec3f(*mapping.entity_color(int(child.name[2:])))])
            defaults[f"{child.path}/geom.primvars:displayColor"] = color
    recorder_clips.set_manifest_defaults(manifest, defaults)


# -- flatten -----------------------------------------------------------------------------------


def default_flat_path(run: str | Path, binary: bool = False) -> Path:
    return _default_flat_path(run, binary)


def flatten(run: str | Path, out: str | Path | None = None, *, binary: bool = False) -> Path:
    """Bake ``run`` (value clips and all) into one layer with every time sample resolved.

    Writes ``run_flat.usda`` next to ``run`` (``run_flat.usdc`` with ``binary``) unless ``out``
    is given; an ``out`` extension that disagrees with ``binary`` raises ``ValueError``.
    (usd-recorder's ``flatten``, which writes atomically.)
    """
    return recorder_clips.flatten(run, out, binary=binary)


__all__ = [
    "ChunkedWriter",
    "DEFAULT_CHUNK_SIZE",
    "chunk_name",
    "default_flat_path",
    "flatten",
    "has_clips",
    "stitch",
]
