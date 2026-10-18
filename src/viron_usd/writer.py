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
