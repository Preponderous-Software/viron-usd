# MVP — Viron USD

The MVP is the **smallest end-to-end slice** that turns a real Viron run into a USD stage that
plays back correctly: entities move across a grid plane on the timeline, appear when placed, and
disappear when removed — and a long run is split into chunk layers rather than one ever-growing
file.

Design details live in [ARCHITECTURE.md](ARCHITECTURE.md); motivation in [CONCEPT.md](CONCEPT.md).

---

## 🎯 The slice

```text
random_walk.py (drives Viron)  ──tick──▶  Recorder.capture(tick)
                                               │  GET-only REST calls
                                               ▼
                                         Viron (localhost:9999)
                                               │  Snapshot
                                               ▼
                                   TimeSampledWriter → chunk_0000.usdc, chunk_0001.usdc, …
                                               │  UsdUtils.StitchClips
                                               ▼
                                   run.usda  ──▶  usdview   /   viron-usd flatten ──▶ Blender
```

---

## ✅ In scope

- A read-only Python client for the Viron endpoints the recorder needs, with bearer-token auth.
- Recording **one Viron environment** (all of its grids) per run.
- Static scene: one plane per grid, laid out side by side; a cell mesh with one face per location.
- Entities as prims with time-sampled `xformOp:translate`, `visibility`, and `viron:name`.
- Sparse sampling with hold keys (a sample is written only when a value changes, plus the
  unchanged value at the tick before the change).
- **Driven mode**: a Python simulation calls `recorder.capture(tick)` after each tick.
- **Poll mode**: `viron-usd record` captures on a fixed wall-clock interval, numbering ticks itself.
- Fixed-size chunk layers (`.usdc`) stitched into `run.usda` with value clips, including a
  manifest default that keeps late-arriving entities hidden before they first appear.
- `viron-usd flatten` to bake a run into one layer for tools that do not read value clips.
- Unit tests against a fake Viron; integration tests against a real Viron in Docker Compose.
- A playback demo: a random-walk driver, a recorded run, and instructions for usdview and Blender.

## 🚫 Out of scope (for the MVP)

- Any change to Viron itself, or any write request to Viron.
- Recording several environments in one stage.
- Per-location prims (locations are faces of one mesh per grid; see
  [ARCHITECTURE.md § Open questions](ARCHITECTURE.md#open-questions)).
- Time-sampled location occupancy colouring.
- Live playback while the simulation runs.
- A Java recorder or an in-process exporter inside Viron.
- Rendering (Hydra/Storm in usdview is the only viewer the MVP targets, plus Blender's viewport).
- Packaging for PyPI (installation from a git checkout is enough for the MVP).

---

## 🧪 Acceptance criteria

The MVP is done when **all** of these hold. Each one is checked by an automated test unless it
says otherwise.

1. **Read-only.** During a recorded run, the recorder issues only `GET` requests to Viron
   (asserted by the fake-Viron transport rejecting any other method).
2. **Correct placement.** For a run where entity *e* is at location `(x, y)` in grid *g* at tick
   *t*, the stage's `/World/env_<E>/entities/e_<id>` prim has, at time code *t*, a local
   translation equal to `mapping.cell_center(g, x, y)`.
3. **Presence.** An entity that is not placed in any of the environment's grids at tick *t*
   (never placed yet, removed, or deleted) has `ComputeVisibility(t) == invisible`; a placed
   one has `inherited`.
4. **No drift.** For an entity that stays still from tick *a* to tick *b* and moves at *b+1*, the
   translation sampled at every integer time code in `[a, b]` equals its position at *a*
   (hold keys work).
5. **Sparse.** For an entity that never moves over *N* ticks, its translate attribute carries at
   most 2 time samples, not *N*.
6. **Rename.** Renaming an entity in Viron between two ticks changes `viron:name` from that tick on.
7. **Chunking.** A 2,500-tick run with `--chunk-size 1000` produces exactly three chunk layers and a
   `run.usda` whose `startTimeCode`/`endTimeCode` are the first and last tick, and whose resolved
   values equal those of the same run recorded unchunked.
8. **Late arrivals.** An entity first placed in chunk 3 is invisible at every time code in chunks 1–2.
9. **Flatten.** `viron-usd flatten run.usda -o run_flat.usdc` produces a single layer with no
   `clips` metadata whose resolved values equal those of `run.usda` at every integer time code.
10. **Real Viron.** The integration suite starts Viron with Docker Compose, mints a test token,
    runs the random-walk demo for 50 ticks, and criteria 1–5 hold on the result.
11. **Playback (manual, documented).** `run.usda` opens in usdview and the entities move when
    the timeline plays; `run_flat.usdc` imports into Blender with animated entities. A short
    screen capture or screenshots are attached to the demo issue.

---

## 🗺 Milestones

Each milestone is one GitHub issue (label `mvp`). They are listed in dependency order.

### M1 — Project scaffold and CI

Python package layout (`src/viron_usd`), `pyproject.toml` pinning `usd-core==26.8`, pytest,
a GitHub Actions workflow running tests on Python 3.10 and 3.12, and a smoke test that imports
`pxr` and writes and re-reads a one-prim stage.

### M2 — Read-only Viron client and tick snapshot

A small client over the `GET` endpoints listed in
[ARCHITECTURE.md § What Viron exposes](ARCHITECTURE.md#what-viron-exposes), with bearer-token
auth, and a `Snapshot` dataclass (environment, grids, locations per grid, entity → (grid,
location) placements, entity names) built from one tick's reads. Includes the fake-Viron
transport used by the rest of the unit tests, and torn-snapshot detection for poll mode.

### M3 — Viron → USD mapping and static scene

`mapping.py` (prim paths, grid offsets, cell centres, colours) and the static part of the
stage: `/World`, Z-up, metres-per-unit, one grid plane and cell mesh per grid with
`viron:*` identity attributes and the face-index → location-id table.

### M4 — Time-sampled entity writer

`TimeSampledWriter`: defines entity prims on first sight, writes `xformOp:translate`,
`visibility`, and `viron:name` sparsely with hold keys, and sets the stage time range.
Covers acceptance criteria 2–6 on synthetic snapshots.

### M5 — Recorder and `viron-usd record` CLI

`Recorder` with `capture(tick)` (driven mode) and a polling loop (poll mode), plus the
`viron-usd record` command (`--viron-url`, `--token`/`VIRON_TOKEN`, `--environment`,
`--interval`, `--ticks`, `--out`, `--ticks-per-second`). Writes a single unchunked layer.

### M6 — Chunked layers, value clips, and `flatten`

Per-chunk `.usdc` layers with their own time range, stitched with `UsdUtils.StitchClips`
into `run.usda`; manifest defaults for late arrivals; `viron-usd flatten`. Covers
acceptance criteria 7–9 and records the size measurements listed in
[ARCHITECTURE.md § Layer strategy](ARCHITECTURE.md#layer-strategy-for-long-runs).

### M7 — Integration tests against a real Viron

Docker Compose setup for Viron + PostgreSQL at a pinned Viron commit, an HS256 test-token
helper, and an integration suite (marked, run in CI as a separate job) covering acceptance
criteria 1 and 10.

### M8 — Playback demo

`examples/random_walk.py` (creates an environment, places entities, moves each to a random
neighbour per tick, captures each tick), a `docs/PLAYBACK.md` walkthrough for usdview and
Blender, and the manual check in acceptance criterion 11.
