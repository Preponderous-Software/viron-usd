"""USD writing: the static scene (this module's first half) and time-sampled entities.

The static scene is everything that does not change during a run: ``/World`` with its stage
metadata, the environment prim, and one grid plane with a cell mesh per grid. See
ARCHITECTURE.md § Viron → USD mapping.
"""

from __future__ import annotations

from pxr import Gf, Sdf, Usd, UsdGeom, Vt

from . import __version__, mapping
from .snapshot import WorldLayout

CELL_COLOR = (0.55, 0.55, 0.55)
MISSING_LOCATION = -1


def _set(prim: Usd.Prim, name: str, type_name: Sdf.ValueTypeName, value) -> Usd.Attribute:
    attr = prim.CreateAttribute(name, type_name, custom=True)
    attr.Set(value)
    return attr


def configure_stage(stage: Usd.Stage, layout: WorldLayout) -> None:
    """Stage-level metadata: Z up, 1 metre per unit, default prim ``/World``, axis convention."""
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    world = UsdGeom.Xform.Define(stage, mapping.WORLD_PATH).GetPrim()
    stage.SetDefaultPrim(world)
    layer = stage.GetRootLayer()
    data = dict(layer.customLayerData)
    data.update(
        {
            "vironUsd:axisConvention": mapping.AXIS_CONVENTION,
            "vironUsd:environmentId": int(layout.environment_id),
            "vironUsd:version": __version__,
        }
    )
    layer.customLayerData = data


def _cell_mesh(stage: Usd.Stage, path: str, grid, location_ids: list[int]) -> UsdGeom.Mesh:
    rows, cols = grid.rows, grid.columns
    mesh = UsdGeom.Mesh.Define(stage, path)

    def vid(x: int, y: int) -> int:
        return x * (cols + 1) + y

    points = [Gf.Vec3f(x, y, 0.0) for x in range(rows + 1) for y in range(cols + 1)]
    counts, indices = [], []
    for x in range(rows):
        for y in range(cols):  # face index x * columns + y
            counts.append(4)
            indices.extend([vid(x, y), vid(x + 1, y), vid(x + 1, y + 1), vid(x, y + 1)])
    mesh.CreatePointsAttr(Vt.Vec3fArray(points))
    mesh.CreateFaceVertexCountsAttr(Vt.IntArray(counts))
    mesh.CreateFaceVertexIndicesAttr(Vt.IntArray(indices))
    mesh.CreateExtentAttr(Vt.Vec3fArray([Gf.Vec3f(0, 0, 0), Gf.Vec3f(rows, cols, 0)]))
    mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    mesh.CreateDoubleSidedAttr(True)
    mesh.CreateDisplayColorAttr(Vt.Vec3fArray([Gf.Vec3f(*CELL_COLOR)]))
    _set(mesh.GetPrim(), "viron:locationIds", Sdf.ValueTypeNames.IntArray, Vt.IntArray(location_ids))
    return mesh


def write_static_scene(stage: Usd.Stage, layout: WorldLayout) -> None:
    """Author ``/World``, the environment, and every grid plane + cell mesh of ``layout``."""
    configure_stage(stage, layout)
    env_id = layout.environment_id
    env = UsdGeom.Xform.Define(stage, mapping.environment_path(env_id)).GetPrim()
    _set(env, "viron:environmentId", Sdf.ValueTypeNames.Int, int(env_id))
    _set(env, "viron:name", Sdf.ValueTypeNames.String, layout.environment_name)
    UsdGeom.Scope.Define(stage, mapping.grids_path(env_id))
    UsdGeom.Scope.Define(stage, mapping.entities_path(env_id))

    by_cell: dict[int, dict[tuple[int, int], int]] = {}
    for loc_id, info in layout.locations.items():
        by_cell.setdefault(info.grid_id, {})[(info.x, info.y)] = loc_id

    for grid in layout.grids:
        xform = UsdGeom.Xform.Define(stage, mapping.grid_path(env_id, grid.grid_id))
        xform.AddTranslateOp().Set(Gf.Vec3d(*mapping.grid_origin(grid)))
        prim = xform.GetPrim()
        _set(prim, "viron:gridId", Sdf.ValueTypeNames.Int, int(grid.grid_id))
        _set(prim, "viron:rows", Sdf.ValueTypeNames.Int, int(grid.rows))
        _set(prim, "viron:columns", Sdf.ValueTypeNames.Int, int(grid.columns))
        _set(prim, "viron:name", Sdf.ValueTypeNames.String, grid.name or "")
        cells = by_cell.get(grid.grid_id, {})
        location_ids = [
            cells.get((x, y), MISSING_LOCATION) for x in range(grid.rows) for y in range(grid.columns)
        ]
        _cell_mesh(stage, mapping.cells_path(env_id, grid.grid_id), grid, location_ids)


# -- time-sampled entities -----------------------------------------------------------------

DEFAULT_TICKS_PER_SECOND = 10.0
VISIBLE = UsdGeom.Tokens.inherited
INVISIBLE = UsdGeom.Tokens.invisible


class _Track:
    """Sparse sampling with hold keys for one attribute (ARCHITECTURE.md § Sampling strategy).

    1. The first observed value is written. 2. An unchanged value is not written; its tick is
    remembered as the pending hold. 3. On a change at tick t, the pending hold is written at its
    tick, then the new value at t. 4. ``flush`` writes any pending hold.
    """

    __slots__ = ("attr", "value", "has_value", "pending", "convert")

    def __init__(self, attr: Usd.Attribute, convert=lambda v: v):
        self.attr = attr
        self.value = None
        self.has_value = False
        self.pending: int | None = None
        self.convert = convert

    def _write(self, tick: int, value) -> None:
        self.attr.Set(self.convert(value), Usd.TimeCode(tick))

    def observe(self, tick: int, value) -> None:
        if not self.has_value:
            self._write(tick, value)
            self.value, self.has_value, self.pending = value, True, None
        elif value == self.value:
            self.pending = tick
        else:
            if self.pending is not None:
                self._write(self.pending, self.value)
            self._write(tick, value)
            self.value, self.pending = value, None

    def hold(self, tick: int) -> None:
        """The value is unchanged at ``tick`` without being observed (e.g. while unplaced)."""
        if self.has_value:
            self.pending = tick

    def flush(self) -> None:
        if self.pending is not None:
            self._write(self.pending, self.value)
            self.pending = None


class _Entity:
    __slots__ = ("prim", "translate", "visibility", "name", "extras")

    def __init__(self, prim: Usd.Prim, translate: _Track, visibility: _Track, name: _Track):
        self.prim = prim
        self.translate = translate
        self.visibility = visibility
        self.name = name
        self.extras: dict[str, _Track] = {}

    def tracks(self):
        yield self.translate
        yield self.visibility
        yield self.name
        yield from self.extras.values()


def _vec3d(value) -> Gf.Vec3d:
    return Gf.Vec3d(*value)


class TimeSampledWriter:
    """Owns one ``Usd.Stage``: writes the static scene once, then one ``write(snapshot)`` per tick.

    Positions, presence (``visibility``), and names are written sparsely with hold keys; one tick
    is one time code. ``close()`` flushes pending holds, sets the stage time range, and saves.
    """

    def __init__(
        self,
        stage: Usd.Stage,
        layout: WorldLayout,
        *,
        ticks_per_second: float = DEFAULT_TICKS_PER_SECOND,
        static_scene: bool = True,
    ):
        if ticks_per_second <= 0:
            raise ValueError("ticks_per_second must be positive")
        self.stage = stage
        self.layout = layout
        self.ticks_per_second = float(ticks_per_second)
        if static_scene:
            write_static_scene(stage, layout)
        else:
            configure_stage(stage, layout)
            env_id = layout.environment_id
            UsdGeom.Xform.Define(stage, mapping.environment_path(env_id))
            UsdGeom.Scope.Define(stage, mapping.entities_path(env_id))
        stage.SetTimeCodesPerSecond(self.ticks_per_second)
        stage.SetFramesPerSecond(self.ticks_per_second)
        world = stage.GetPrimAtPath(mapping.WORLD_PATH)
        self._wall_clock = _Track(
            world.CreateAttribute("viron:wallClock", Sdf.ValueTypeNames.Double, custom=True)
        )
        self._entities: dict[int, _Entity] = {}
        self.first_tick: int | None = None
        self.last_tick: int | None = None
        self._torn: dict[str, str] = {}
        self.closed = False

    @classmethod
    def create(cls, path, layout: WorldLayout, **kwargs) -> "TimeSampledWriter":
        """Create a new layer at ``path`` (``.usda`` text or ``.usdc`` binary by extension)."""
        return cls(Usd.Stage.CreateNew(str(path)), layout, **kwargs)

    # -- entity prims -------------------------------------------------------------------

    def _define_entity(self, entity_id: int) -> _Entity:
        env_id = self.layout.environment_id
        xform = UsdGeom.Xform.Define(self.stage, mapping.entity_path(env_id, entity_id))
        translate = xform.AddTranslateOp()
        prim = xform.GetPrim()
        prim.CreateAttribute("viron:entityId", Sdf.ValueTypeNames.Int, custom=True).Set(int(entity_id))
        cube = UsdGeom.Cube.Define(self.stage, mapping.entity_geom_path(env_id, entity_id))
        cube.CreateSizeAttr(mapping.ENTITY_SIZE)
        half = mapping.ENTITY_SIZE / 2
        cube.CreateExtentAttr(Vt.Vec3fArray([Gf.Vec3f(-half), Gf.Vec3f(half)]))
        cube.CreateDisplayColorAttr(Vt.Vec3fArray([Gf.Vec3f(*mapping.entity_color(entity_id))]))
        entity = _Entity(
            prim,
            _Track(translate.GetAttr(), _vec3d),
            _Track(xform.CreateVisibilityAttr()),
            _Track(prim.CreateAttribute("viron:name", Sdf.ValueTypeNames.String, custom=True)),
        )
        self._entities[entity_id] = entity
        return entity

    def entity_ids(self) -> list[int]:
        return sorted(self._entities)

    # -- per tick -----------------------------------------------------------------------

    def write(self, snapshot) -> None:
        if self.closed:
            raise RuntimeError("writer is closed")
        tick = int(snapshot.tick)
        if self.last_tick is not None and tick <= self.last_tick:
            raise ValueError(f"tick {tick} is not after the previous tick {self.last_tick}")
        previous = self.last_tick
        if self.first_tick is None:
            self.first_tick = tick
        self.last_tick = tick

        self._wall_clock.observe(tick, float(snapshot.wall_clock))
        for entity_id in sorted(snapshot.entities):
            placement = snapshot.entities[entity_id]
            entity = self._entities.get(entity_id)
            if entity is None:
                entity = self._define_entity(entity_id)
                if previous is not None:
                    # Not placed at earlier ticks: invisible up to (and held before) this one.
                    entity.visibility.observe(previous, INVISIBLE)
            info = self.layout.locations[placement.location_id]
            grid = self.layout.grid(info.grid_id)
            entity.translate.observe(tick, mapping.entity_position(grid, info.x, info.y, entity_id))
            entity.visibility.observe(tick, VISIBLE)
            entity.name.observe(tick, placement.name)
        for entity_id, entity in self._entities.items():
            if entity_id not in snapshot.entities:
                entity.visibility.observe(tick, INVISIBLE)
                # Keep the last position and name exactly while unplaced, so they do not
                # interpolate towards wherever the entity is next placed.
                entity.translate.hold(tick)
                entity.name.hold(tick)
        if snapshot.warnings:
            self._torn[str(tick)] = "; ".join(snapshot.warnings)

    # -- end ----------------------------------------------------------------------------

    def flush(self) -> None:
        """Write every pending hold key, so final values hold through the end of the range."""
        self._wall_clock.flush()
        for entity in self._entities.values():
            for track in entity.tracks():
                track.flush()

    def close(self, save: bool = True) -> None:
        if self.closed:
            return
        self.flush()
        if self.first_tick is not None:
            self.stage.SetStartTimeCode(self.first_tick)
            self.stage.SetEndTimeCode(self.last_tick)
        if self._torn:
            layer = self.stage.GetRootLayer()
            data = dict(layer.customLayerData)
            data["vironUsd:tornTicks"] = dict(self._torn)
            layer.customLayerData = data
        if save:
            self.stage.GetRootLayer().Save()
        self.closed = True
