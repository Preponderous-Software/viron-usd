# Concept — Viron USD

> Status: planning. Facts about Viron below are cited against
> [`Preponderous-Software/viron@c0a6fc5`](https://github.com/Preponderous-Software/viron/tree/c0a6fc54b66ddab63a9c90f4858fced55ea68e82)
> (the `main` branch head on 2026-10-17).

## The problem

[Viron](https://github.com/Preponderous-Software/viron) is a spatial simulation service. It keeps
**environments → grids → locations**, and **entities** placed at locations, behind a REST API
([README](https://github.com/Preponderous-Software/viron/blob/c0a6fc54b66ddab63a9c90f4858fced55ea68e82/README.md),
[docs/MVP.md](https://github.com/Preponderous-Software/viron/blob/c0a6fc54b66ddab63a9c90f4858fced55ea68e82/docs/MVP.md)).
The simulation logic — what moves where, and when — lives in the client that drives Viron.

Viron stores **only the present**. Its schema has one row per entity placement
(`viron.entity_location`, keyed on `entity_id` alone —
[create_tables.sql L36-47](https://github.com/Preponderous-Software/viron/blob/c0a6fc54b66ddab63a9c90f4858fced55ea68e82/db-scripts/setup/create_tables.sql#L36-L47)),
so a move overwrites the previous placement. There is no tick counter, no event log, and no
history endpoint anywhere in the API
([docs/openapi/viron-api.json](https://github.com/Preponderous-Software/viron/blob/c0a6fc54b66ddab63a9c90f4858fced55ea68e82/docs/openapi/viron-api.json)).

So once a run is over, there is nothing to look at. Answering "how did these entities end up
clustered in that corner?" means re-running the simulation and hoping it does the same thing.

## The idea

Record the world once per tick, and store the recording in a format that 3D tools already know
how to play back.

[OpenUSD](https://openusd.org) is a good fit because it models change over time natively: any
attribute can carry **time samples** — values keyed by a time code — and every USD viewer has
a timeline that scrubs through them. A Viron run maps onto that cleanly:

- each Viron **grid** becomes a flat plane in 3D;
- each **location** is a cell on that plane at its `(x, y)`;
- each **entity** becomes a prim whose position is time-sampled tick by tick, whose visibility
  goes off when it is not placed, and whose name is time-sampled in case it is renamed;
- each **tick** becomes one USD time code.

The output plays back in `usdview` and, after flattening, in Blender's timeline.

## Who it is for

- **People building simulations on Viron** who want to *see* a run after the fact — to debug a
  movement rule, to check that entities spread out as intended, or to compare two runs side by side.
- **People teaching or demonstrating** emergent behaviour, who want a replayable artifact rather
  than a screen recording.
- **People learning OpenUSD**, who want a small, real example of time-sampled data, sparse
  sampling, and value clips that is not a film-production pipeline.

## What makes it interesting

- **Viron has no notion of time, but USD does.** The recorder supplies the time axis: one tick,
  one time code. Viron itself is never modified.
- **Sparse sampling with hold keys.** Most entities do not move every tick. Writing a sample only
  on change keeps files small, but USD interpolates linearly between samples of numeric
  attributes, so a naive sparse write would make a stationary-then-moving entity drift across
  the gap. Writing a "hold" sample at the last unchanged tick before each change avoids that.
  (See [ARCHITECTURE.md § Sampling strategy](ARCHITECTURE.md#sampling-strategy).)
- **Long runs via value clips.** USD value clips were designed for exactly this shape of data —
  the OpenUSD documentation reports them "useful for efficiently animating medium to large
  crowds, and for representing very large, simulated effects" ([Value Clips](https://openusd.org/release/api/_usd__page__value_clips.html)). A run
  is written as fixed-size chunk layers and stitched into one stage, so no single file grows
  without bound.
- **A small, honest mapping.** The design uses only what Viron's API actually returns. Viron
  entities have an id, a name, and a creation date — nothing else
  ([EntityDto.java](https://github.com/Preponderous-Software/viron/blob/c0a6fc54b66ddab63a9c90f4858fced55ea68e82/src/main/java/preponderous/viron/dto/EntityDto.java)) —
  so the recorder does not invent health, facing, or other visual state.

## Non-goals

- **Modifying Viron.** The recorder only issues `GET` requests. It never places, moves, renames,
  or deletes anything, and this project does not change the Viron codebase.
- **Driving the simulation.** Simulation rules stay in the client that drives Viron. (The demo
  includes a tiny random-walk driver only so there is something to record.)
- **Live streaming or a custom viewer.** Playback uses existing USD tools. Watching a run while it
  happens is out of scope for the MVP.
- **Rich visuals.** Entities are simple shapes; there are no meshes, rigs, materials beyond a
  display colour, or rendering pipelines.
- **Visual state Viron does not have.** Anything beyond position, presence, and name would need
  to come from outside Viron, and is left for later (see open questions in
  [ARCHITECTURE.md](ARCHITECTURE.md#open-questions)).
- **Round-tripping.** USD → Viron import is not planned.

## Prior art

Each of these was checked against its own documentation; links go to the source.

| Project | What it does | What it shows for this project |
|---|---|---|
| [NVIDIA Warp — `warp.render.UsdRenderer`](https://nvidia.github.io/warp/stable/api_reference/_generated/warp.render.UsdRenderer.html) | A "USD-based renderer for creating 3D visualizations and animations" of Warp simulations. `begin_frame(time)` sets "the current time code for subsequent writes"; `save()` writes the stage. It depends on `usd-core`. | The same per-step "set time code, write state" loop this project uses, from a Python simulation library on top of `usd-core`. |
| [MuJoCo — `mujoco.usd.exporter.USDExporter`](https://mujoco.readthedocs.io/en/stable/python.html) | Saves "full trajectories" of a physics simulation to USD. Each `update_scene` call records geom, camera, and light poses for the current frame; `save_scene` writes `.usd`, `.usda`, or `.usdc`. Needs `usd-core` (`pip install mujoco[usd]`). | A second simulation engine choosing a Python exporter over `usd-core` rather than a native binding, with one capture call per step. Its [OpenUSD page](https://mujoco.readthedocs.io/en/3.4.0/OpenUSD/exporting.html) notes the feature is experimental. |
| [OpenUSD Value Clips](https://openusd.org/release/api/_usd__page__value_clips.html) | Splits time-sampled data across many layers and sequences them on a prim; documents `usdstitchclips` and the `UsdUtils` stitching functions. | The layer strategy for long runs. |
| [Blender USD importer](https://docs.blender.org/manual/en/latest/files/import_export/usd.html) | Imports time-varying transforms as a Transform Cache constraint; always imports prims with animated visibility; notes that USD file sequences ("a unique file per frame") are not supported. | Confirms the Blender playback path for time-sampled transforms, and why the recorder writes time samples rather than one file per tick. |

What none of these do is record a **REST-backed grid world** that has no time axis of its own.
That gap — Viron's present-only state model — is what this project fills.
