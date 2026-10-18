import logging

import pytest

from viron_usd.snapshot import Placement, Snapshot, WorldLayout, take_snapshot


def world(viron, grids=1, rows=3, cols=4):
    env = viron.create_environment("w", grids, rows, cols)
    return env, viron.grids_of(env)


def test_layout_has_grids_in_id_order_and_every_location(viron):
    env, grids = world(viron, grids=2)
    layout = WorldLayout.fetch(viron.client(), env)
    assert layout.environment_name == "w"
    assert [g.grid_id for g in layout.grids] == grids
    assert [g.index for g in layout.grids] == [0, 1]
    assert len(layout.locations) == 24
    loc = viron.location_id(grids[1], 2, 3)
    assert layout.locations[loc] == (grids[1], 2, 3)
    assert layout.grid(grids[1]).rows == 3
    assert len(layout.location_order[grids[0]]) == 12
    with pytest.raises(KeyError):
        layout.grid(999)


def test_empty_grid_gives_empty_snapshot(viron):
    env, _ = world(viron)
    client = viron.client()
    snap = take_snapshot(client, WorldLayout.fetch(client, env), 0, clock=lambda: 12.5)
    assert snap == Snapshot(0, 12.5, {}, {}, ())


def test_several_grids(viron):
    env, (g1, g2) = world(viron, grids=2)
    a, b = viron.create_entity("a"), viron.create_entity("b")
    viron.place(a, g1, 0, 1)
    viron.place(b, g2, 2, 3)
    client = viron.client()
    snap = take_snapshot(client, WorldLayout.fetch(client, env), 7)
    assert snap.tick == 7
    assert dict(snap.entities) == {
        a: Placement("a", g1, viron.location_id(g1, 0, 1)),
        b: Placement("b", g2, viron.location_id(g2, 2, 3)),
    }


def test_two_entities_on_one_location(viron):
    env, (g,) = world(viron)
    a, b = viron.create_entity("a"), viron.create_entity("b")
    viron.place(a, g, 1, 1)
    viron.place(b, g, 1, 1)
    client = viron.client()
    snap = take_snapshot(client, WorldLayout.fetch(client, env), 0)
    assert snap.entities[a].location_id == snap.entities[b].location_id


def test_unplaced_and_other_environment_entities_are_absent(viron):
    env, (g,) = world(viron)
    other_env, (og,) = world(viron)
    placed, unplaced, elsewhere = (viron.create_entity(n) for n in "abc")
    viron.place(placed, g, 0, 0)
    viron.place(elsewhere, og, 0, 0)
    client = viron.client()
    snap = take_snapshot(client, WorldLayout.fetch(client, env), 0)
    assert set(snap.entities) == {placed}


def test_fallback_costs_g_plus_n_requests(viron):
    env, (g1, g2) = world(viron, grids=2)
    for i in range(5):
        viron.place(viron.create_entity(str(i)), g1 if i % 2 else g2, 0, i % 4)
    client = viron.client()
    layout = WorldLayout.fetch(client, env)
    before = client.request_count
    take_snapshot(client, layout, 0)
    assert client.request_count - before == 2 + 5
    assert all(m == "GET" for m, _ in viron.requests)


def test_extras_are_carried(viron):
    env, (g,) = world(viron)
    e = viron.create_entity("a")
    viron.place(e, g, 0, 0)
    client = viron.client()
    snap = take_snapshot(client, WorldLayout.fetch(client, env), 0, extras={e: {"category": "wolf"}})
    assert {k: dict(v) for k, v in snap.extras.items()} == {e: {"category": "wolf"}}


def _tear_once(viron, path_suffix, action):
    """Run ``action`` the first time a GET path ends with ``path_suffix`` (a driver write mid-read)."""
    state = {"done": False}

    def hook(path):
        if not state["done"] and path.endswith(path_suffix):
            state["done"] = True
            action()

    viron.before_get.append(hook)
    return state


def test_entity_removed_between_calls_is_retried_and_resolved(viron, caplog):
    env, (g,) = world(viron)
    e = viron.create_entity("a")
    viron.place(e, g, 0, 0)
    client = viron.client()
    layout = WorldLayout.fetch(client, env)
    state = _tear_once(viron, f"/locations/entity/{e}", lambda: viron.remove(e))
    with caplog.at_level(logging.INFO):
        snap = take_snapshot(client, layout, 3)
    assert state["done"]
    assert snap.entities == {} and snap.warnings == ()
    assert "retrying once" in caplog.text


def test_entity_moved_grid_between_calls_is_retried(viron):
    env, (g1, g2) = world(viron, grids=2)
    e = viron.create_entity("a")
    viron.place(e, g1, 0, 0)
    client = viron.client()
    layout = WorldLayout.fetch(client, env)
    _tear_once(viron, f"/locations/entity/{e}", lambda: viron.place(e, g2, 1, 1))
    snap = take_snapshot(client, layout, 0)
    assert snap.entities[e] == Placement("a", g2, viron.location_id(g2, 1, 1))
    assert snap.warnings == ()


def test_persistently_torn_read_is_reported_not_silently_recorded(viron, caplog):
    env, (g1, g2) = world(viron, grids=2)
    e = viron.create_entity("a")
    viron.place(e, g1, 0, 0)
    client = viron.client()
    layout = WorldLayout.fetch(client, env)

    def flip(path):  # every location read sees the entity in the other grid
        if path.endswith(f"/locations/entity/{e}"):
            loc = viron.placement[e]
            target = g2 if viron.locations[loc][0] == g1 else g1
            viron.place(e, target, 0, 0)

    viron.before_get.append(flip)
    with caplog.at_level(logging.WARNING):
        snap = take_snapshot(client, layout, 5)
    assert len(snap.warnings) == 1 and f"entity {e}" in snap.warnings[0]
    assert "torn snapshot recorded after retry" in caplog.text
    # The location read came last, so the entity is recorded where it actually is.
    assert snap.entities[e].grid_id == viron.locations[viron.placement[e]][0]


def test_location_outside_environment_is_torn(viron):
    env, (g,) = world(viron)
    _, (og,) = world(viron)
    e = viron.create_entity("a")
    viron.place(e, g, 0, 0)
    client = viron.client()
    layout = WorldLayout.fetch(client, env)

    def away(path):
        if path.endswith(f"/locations/entity/{e}"):
            viron.place(e, og, 0, 0)

    def back(path):
        if path.endswith(f"/entities/grid/{g}"):
            viron.place(e, g, 0, 0)

    viron.before_get.extend([back, away])
    snap = take_snapshot(client, layout, 0)
    assert e not in snap.entities
    assert "not in environment" in snap.warnings[0]


def test_snapshot_is_immutable(viron):
    env, _ = world(viron)
    client = viron.client()
    snap = take_snapshot(client, WorldLayout.fetch(client, env), 0)
    with pytest.raises(Exception):
        snap.tick = 1
    with pytest.raises(TypeError):
        snap.entities[1] = Placement("x", 1, 1)


def test_entity_listed_in_two_grids_is_retried(viron):
    env, (g1, g2) = world(viron, grids=2)
    e = viron.create_entity("a")
    viron.place(e, g1, 0, 0)
    client = viron.client()
    layout = WorldLayout.fetch(client, env)
    state = _tear_once(viron, f"/entities/grid/{g2}", lambda: viron.place(e, g2, 0, 0))
    snap = take_snapshot(client, layout, 0)
    assert state["done"]
    assert snap.entities[e].grid_id == g2 and snap.warnings == ()


def test_captured_extras_are_frozen_against_later_driver_changes(viron):
    env, (g,) = world(viron)
    e = viron.create_entity("a")
    viron.place(e, g, 0, 0)
    client = viron.client()
    color = [0.9, 0.2, 0.1]
    values = {"color": color, "category": "wolf"}
    extras = {e: values}
    snap = take_snapshot(client, WorldLayout.fetch(client, env), 0, extras=extras)
    color[0] = 0.0
    values["category"] = "sheep"
    extras[e + 1] = {"category": "x"}
    assert dict(snap.extras[e]) == {"color": (0.9, 0.2, 0.1), "category": "wolf"}
    assert set(snap.extras) == {e}
    with pytest.raises(TypeError):
        snap.extras[e]["category"] = "changed"
    with pytest.raises(TypeError):
        snap.extras[e + 2] = {}


def test_layout_merge_keeps_indices_and_appends_new_grids(viron):
    env, (g1, g2) = world(viron, grids=2)
    client = viron.client()
    old = WorldLayout.fetch(client, env)
    g3 = viron.add_grid(env, 3, 4)
    del viron.grids[g1]
    newer = WorldLayout.fetch(client, env)
    assert [g.index for g in newer.grids] == [0, 1]  # fetch re-indexes
    merged = old.merged_with(newer)
    assert [(g.grid_id, g.index) for g in merged.grids] == [(g1, 0), (g2, 1), (g3, 2)]
    assert set(merged.locations) == set(old.locations) | set(newer.locations)


def test_removed_grid_answering_404_reads_as_empty(viron):
    env, (g1, g2) = world(viron, grids=2)
    e = viron.create_entity("a")
    viron.place(e, g2, 0, 0)
    client = viron.client()
    layout = WorldLayout.fetch(client, env)
    viron.inject(rf"/entities/grid/{g1}$", 404)
    snap = take_snapshot(client, layout, 0)
    assert set(snap.entities) == {e}
