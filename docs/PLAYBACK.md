# Playback — Viron USD

This walkthrough records a run of the random-walk demo against a **locally started** Viron and
plays it back in usdview and Blender. It is the manual check for
[MVP acceptance criterion 11](../MVP.md#-acceptance-criteria).

> The demo driver (`examples/random_walk.py`) **writes** to Viron — it creates an environment,
> creates and places entities, and moves them. Only point it at a throwaway Viron you started
> yourself. The recorder it feeds still sends only `GET` requests.

---

## 1. Install viron-usd

From a git checkout (the package is not on PyPI):

```bash
git clone https://github.com/Preponderous-Software/viron-usd.git
cd viron-usd
python3 -m venv .venv && . .venv/bin/activate   # Python 3.10 or newer
pip install -e ".[dev]"                          # [dev] brings PyJWT, used to mint the token
```

## 2. Start a local Viron

`tests/integration/start_viron.sh` clones Viron at the pinned commit, builds it, and starts it
with PostgreSQL in Docker Compose on `http://localhost:9999` (test JWT secret, debug endpoints and
usage reporting off):

```bash
tests/integration/start_viron.sh
```

Viron needs a JWT bearer token signed with that instance's secret. Mint one with the test helper
(it defaults to the same secret and issuer as `start_viron.sh`):

```bash
export VIRON_TOKEN="$(python -c 'import sys; sys.path.insert(0, "tests"); from viron_token import mint_token; print(mint_token())')"
```

The token is valid for 15 minutes; mint a new one if a run starts failing with `401`.

## 3. Record a run

```bash
python examples/random_walk.py --ticks 200 --entities 12 --grid-size 12 --out runs/random_walk
```

It prints the environment id, where the run was written, and the per-capture latency:

```text
recorded environment 8: 200 ticks, 12 entities -> runs/random_walk/run.usda
capture latency: mean 26.1 ms, p95 36.1 ms (13.0 GET requests per capture incl. layout reads)
```

(Your numbers will differ.) Useful flags: `--grids N` (side-by-side grids), `--seed N`,
`--chunk-size N` (default 1000; `0` writes one unchunked layer), `--ticks-per-second N`
(playback rate, default 10), `--binary` (`.usdc` chunk layers).

The run directory holds `run.usda` (the stage to open), `run.topology.usda`,
`run.manifest.usda`, and one `chunk_NNNN.usda` per 1,000 ticks. To record a simulation of your
own instead, see the [README § Usage](../README.md#-usage).

## 4. Play it in usdview

usdview is **not** part of the `usd-core` wheel ("does not provide any of the optional plugins or
imaging features"). Get it from a full OpenUSD build — `python build_scripts/build_usd.py <dir>`
from [OpenUSD](https://github.com/PixarAnimationStudios/OpenUSD) builds usdview by default — or
from a prebuilt OpenUSD distribution. Then:

```bash
usdview runs/random_walk/run.usda
```

Press **Play** (or drag the timeline). Each tick is one frame; at the default 10 ticks per second
a 200-tick run plays in 20 seconds. Entities (small cubes, one colour per entity) move across the
grey cell grid, entities placed later appear when they are placed, and removed entities
disappear. usdview reads the value clips directly, so no flattening is needed.

## 5. Play it in Blender

Bake the run into one layer first (Blender's importer is not documented to read value clips, and
this makes the file self-contained):

```bash
viron-usd flatten runs/random_walk/run.usda            # -> runs/random_walk/run_flat.usda
```

In Blender: **File › Import › Universal Scene Description (.usd*)**, select `run_flat.usda`,
tick **Set Frame Range** in the import options, and import. Then set **Output Properties › Frame
Rate** to the run's ticks per second (10 by default) — the importer sets the frame range but
leaves the scene's frame rate unchanged. Press **Play**.

### What was checked in Blender (2026-10-17)

Checked headlessly with Blender 4.2.23 LTS (the `bpy` module) on a 100-tick, 7-entity run
recorded against a local Viron, with chunk size 40 so the run had three value-clip chunks, an
entity placed at tick 50, and one removed at tick 70:

| Question | Result |
|---|---|
| Does the flattened run import with animated entities? | **Yes.** Each entity imports as one object (the `e_<id>` Xform is merged with its `geom` cube) with a **Transform Cache** constraint on its USD path, and its position follows the recording frame by frame. |
| Does Blender read the value-clip stage (`run.usda`) directly? | **Yes**, in this check: importing `run.usda` gave the same constraints and the same positions at every sampled frame as `run_flat.usda`. Flattening is still the recommended path. |
| Does animated `visibility` play back as hide/show? | **No.** No visibility animation was imported (no keyframes; `hide_viewport`/`hide_render` stay off), so entities that are not placed at a tick remain visible. In a chunked run they sit at the origin during chunks in which they had no samples (before their first placement, or after removal); in an unchunked run they hold their last position. usdview does honour the animated visibility. |
| Frame rate | The importer set the frame range (0–99) but left the scene at 24 fps; set it to 10 by hand. |

## 6. Manual verification (criterion 11)

Not yet performed — **needs a person with a display**:

- [ ] `usdview runs/random_walk/run.usda`: entities move when the timeline plays; late arrivals
      appear and removed entities disappear.
- [ ] `run_flat.usda` imported into the Blender UI with **Set Frame Range**: entities animate on
      play.
- [ ] Screenshots or a short screen capture attached to the M8 issue.

## 7. Clean up

```bash
tests/integration/start_viron.sh down    # stops Viron and PostgreSQL and deletes their volume
```
