from pxr import Usd, UsdGeom

from viron_usd import mapping
from viron_usd.snapshot import WorldLayout
from viron_usd.writer import write_static_scene


def build(viron, tmp_path, grids=2, rows=3, cols=4):
    env = viron.create_environment("meadow", grids, rows, cols)
    client = viron.client()
    layout = WorldLayout.fetch(client, env)
    path = tmp_path / "static.usda"
    stage = Usd.Stage.CreateNew(str(path))
    write_static_scene(stage, layout)
    stage.GetRootLayer().Save()
    return env, layout, Usd.Stage.Open(str(path))


def test_stage_metadata(viron, tmp_path):
    env, _, stage = build(viron, tmp_path)
    assert UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z
    assert UsdGeom.GetStageMetersPerUnit(stage) == 1.0
    assert stage.GetDefaultPrim().GetPath() == "/World"
    data = stage.GetRootLayer().customLayerData
    assert data["vironUsd:axisConvention"] == mapping.AXIS_CONVENTION
    assert data["vironUsd:environmentId"] == env
    assert stage.GetCompositionErrors() == []


def test_environment_and_grid_identity(viron, tmp_path):
    env, layout, stage = build(viron, tmp_path)
    env_prim = stage.GetPrimAtPath(mapping.environment_path(env))
    assert env_prim.GetAttribute("viron:environmentId").Get() == env
    assert env_prim.GetAttribute("viron:name").Get() == "meadow"
    for grid in layout.grids:
        prim = stage.GetPrimAtPath(mapping.grid_path(env, grid.grid_id))
        assert prim.GetAttribute("viron:gridId").Get() == grid.grid_id
        assert prim.GetAttribute("viron:rows").Get() == 3
        assert prim.GetAttribute("viron:columns").Get() == 4
        assert prim.GetAttribute("viron:name").Get() == ""
        translate = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(0).ExtractTranslation()
        assert tuple(translate) == mapping.grid_origin(grid)
    assert stage.GetPrimAtPath(mapping.entities_path(env)).IsValid()


def test_two_cell_meshes_with_twelve_faces_and_location_ids(viron, tmp_path):
    env, layout, stage = build(viron, tmp_path)
    meshes = [p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)]
    assert len(meshes) == 2
    for grid in layout.grids:
        mesh = UsdGeom.Mesh(stage.GetPrimAtPath(mapping.cells_path(env, grid.grid_id)))
        assert len(mesh.GetFaceVertexCountsAttr().Get()) == 12
        ids = list(mesh.GetPrim().GetAttribute("viron:locationIds").Get())
        assert len(ids) == 12
        for x in range(3):
            for y in range(4):
                loc = ids[mapping.face_index(grid, x, y)]
                assert layout.locations[loc] == (grid.grid_id, x, y)
                assert loc == viron.location_id(grid.grid_id, x, y)
    all_ids = {
        i
        for g in layout.grids
        for i in stage.GetPrimAtPath(mapping.cells_path(env, g.grid_id)).GetAttribute("viron:locationIds").Get()
    }
    assert all_ids == set(layout.locations)


def test_face_geometry_matches_cell_center(viron, tmp_path):
    env, layout, stage = build(viron, tmp_path, grids=1, rows=2, cols=3)
    grid = layout.grids[0]
    mesh = UsdGeom.Mesh(stage.GetPrimAtPath(mapping.cells_path(env, grid.grid_id)))
    points = mesh.GetPointsAttr().Get()
    indices = mesh.GetFaceVertexIndicesAttr().Get()
    face = mapping.face_index(grid, 1, 2)
    corners = [points[i] for i in indices[face * 4 : face * 4 + 4]]
    cx = sum(p[0] for p in corners) / 4
    cy = sum(p[1] for p in corners) / 4
    assert (cx, cy) == mapping.cell_center(grid, 1, 2)[:2]
