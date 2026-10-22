import logging
import random

import pytest

from fake_viron import FakeViron
from viron_usd.snapshot import (
    GridInfo,
    LocationInfo,
    Placement,
    Snapshot,
    WorldLayout,
    freeze_extras,
    take_snapshot,
)


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
    assert client.supports_bulk_placements(env) is False  # the one-off probe
    before = client.request_count
    take_snapshot(client, layout, 0)
    assert client.request_count - before == 2 + 5
    assert all(m == "GET" for m, _ in viron.requests)


def test_bulk_read_costs_two_requests(viron_bulk):
    env, (g1, g2) = world(viron_bulk, grids=2)
    for i in range(5):
        viron_bulk.place(viron_bulk.create_entity(str(i)), g1 if i % 2 else g2, 0, i % 4)
    client = viron_bulk.client()
    layout = WorldLayout.fetch(client, env)
    assert client.supports_bulk_placements(env) is True
    before = len(viron_bulk.requests)
    snap = take_snapshot(client, layout, 0)
    assert viron_bulk.get_paths()[before:] == [
        f"/api/v1/locations/placements?environmentId={env}",
        f"/api/v1/entities/environment/{env}",
    ]
    assert len(snap.entities) == 5 and snap.warnings == ()
    assert all(m == "GET" for m, _ in viron_bulk.requests)


def _walk(fake, ticks=25, seed=3):
    """Drive a seeded random walk on ``fake`` and yield after each tick's writes."""
    rng = random.Random(seed)
    env, grids = world(fake, grids=2)
    _, (og,) = world(fake)
    ents = [fake.create_entity(f"e{i}") for i in range(6)]
    for tick in range(ticks):
        for e in list(ents):
            r = rng.random()
            if r < 0.1:
                fake.remove(e)
            elif r < 0.15:
                fake.place(e, og, 0, 0)  # placed in another environment
            elif r < 0.85:
                fake.place(e, rng.choice(grids), rng.randrange(3), rng.randrange(4))
            if rng.random() < 0.1:
                fake.rename(e, f"e{e}-t{tick}")
        if tick == ticks // 2:
            fake.delete_entity(ents.pop(0))
            ents.append(fake.create_entity("late"))
        yield env, tick


def test_bulk_and_fallback_paths_read_the_same_snapshots():
    fallback, bulk = FakeViron(), FakeViron(bulk_placements=True)
    got = {}
    for fake in (fallback, bulk):
        client = fake.client()
        layout = None
        for env, tick in _walk(fake):
            layout = layout or WorldLayout.fetch(client, env)
            got.setdefault(tick, []).append(take_snapshot(client, layout, tick, clock=lambda: 0.0))
    assert any(s[0].entities for s in got.values())
    for tick, (a, b) in got.items():
        assert dict(a.entities) == dict(b.entities), tick
        assert a.warnings == b.warnings == ()
    assert any("/locations/placements" in p for p in bulk.get_paths()[1:])
    assert not any("/locations/entity/" in p for p in bulk.get_paths())


def test_bulk_read_looks_up_a_placed_entity_missing_from_the_name_list(viron_bulk):
    env, (g,) = world(viron_bulk)
    e = viron_bulk.create_entity("a")
    viron_bulk.place(e, g, 1, 1)
    client = viron_bulk.client()
    layout = WorldLayout.fetch(client, env)
    # Removed between the placement read and the name read: placed as of the placement query.
    state = _tear_once(viron_bulk, f"/entities/environment/{env}", lambda: viron_bulk.remove(e))
    snap = take_snapshot(client, layout, 0)
    assert state["done"]
    assert snap.entities == {e: Placement("a", g, viron_bulk.location_id(g, 1, 1))}
    assert snap.warnings == ()
    assert f"/api/v1/entities/{e}" in viron_bulk.get_paths()


def test_bulk_read_of_an_entity_deleted_mid_read_is_retried(viron_bulk, caplog):
    env, (g,) = world(viron_bulk)
    e = viron_bulk.create_entity("a")
    viron_bulk.place(e, g, 1, 1)
    client = viron_bulk.client()
    layout = WorldLayout.fetch(client, env)
    state = _tear_once(viron_bulk, f"/entities/environment/{env}", lambda: viron_bulk.delete_entity(e))
    with caplog.at_level(logging.INFO):
        snap = take_snapshot(client, layout, 0)
    assert state["done"]
    assert snap.entities == {} and snap.warnings == ()
    assert "no longer exists" in caplog.text and "retrying once" in caplog.text


@pytest.mark.parametrize("bulk", [False, True])
def test_entity_on_a_grid_added_after_the_layout_is_not_read(bulk):
    fake = FakeViron(bulk_placements=bulk)
    env, (g,) = world(fake)
    client = fake.client()
    layout = WorldLayout.fetch(client, env)
    g2 = fake.add_grid(env, 1, 1)
    a, b = fake.create_entity("a"), fake.create_entity("b")
    fake.place(a, g, 0, 0)
    fake.place(b, g2, 0, 0)
    snap = take_snapshot(client, layout, 0)
    assert set(snap.entities) == {a} and snap.warnings == ()


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


def test_layout_merge_takes_newer_name_and_unions_location_order():
    old = WorldLayout(
        1, "before", (GridInfo(5, 1, 1, None, 0),), {10: LocationInfo(5, 0, 0)}, {5: (10,)}
    )
    newer = WorldLayout(
        1, "after", (GridInfo(7, 1, 2, "b", 0),), {20: LocationInfo(7, 0, 0), 21: LocationInfo(7, 0, 1)}, {7: (20, 21)}
    )
    merged = old.merged_with(newer)
    assert merged.environment_name == "after"
    assert merged.grid_ids() == (5, 7)
    assert merged.grid(7) == GridInfo(7, 1, 2, "b", 1)
    assert dict(merged.location_order) == {5: (10,), 7: (20, 21)}
    with pytest.raises(TypeError):
        merged.locations[30] = LocationInfo(7, 0, 0)


def test_layout_merge_onto_a_layout_with_no_grids_starts_at_index_zero():
    empty = WorldLayout(1, "w", (), {})
    newer = WorldLayout(1, "w", (GridInfo(3, 1, 1, None, 4),), {})
    assert [(g.grid_id, g.index) for g in empty.merged_with(newer).grids] == [(3, 0)]


def test_freeze_extras_copies_deeply_and_normalizes_keys():
    assert dict(freeze_extras(None)) == {}
    inner = [1, 2]
    frozen = freeze_extras({"4": {"path": [inner, (3, [4])], "n": 1}})
    inner.append(9)
    assert set(frozen) == {4}
    assert frozen[4]["path"] == ((1, 2), (3, (4,)))
    assert frozen[4]["n"] == 1


def test_removed_grid_answering_404_reads_as_empty(viron):
    env, (g1, g2) = world(viron, grids=2)
    e = viron.create_entity("a")
    viron.place(e, g2, 0, 0)
    client = viron.client()
    layout = WorldLayout.fetch(client, env)
    viron.inject(rf"/entities/grid/{g1}$", 404)
    snap = take_snapshot(client, layout, 0)
    assert set(snap.entities) == {e}
