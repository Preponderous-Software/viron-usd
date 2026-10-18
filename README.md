# Viron USD

**Viron USD** is a **flight recorder for Viron worlds** — it captures a [Viron](https://github.com/Preponderous-Software/viron)-backed simulation once per tick and writes it as an [OpenUSD](https://openusd.org) stage whose attributes are **time-sampled**, so a whole run can be scrubbed and replayed in `usdview` or Blender's timeline.

> **Status: MVP implemented (0.1.0).** All eight [MVP milestones](MVP.md#-milestones) have landed: driven and poll-mode recording, chunked value-clip layers, `viron-usd flatten`, an integration suite that runs against a real Viron in CI, and a playback demo. One acceptance check is still manual and open: watching a run in usdview and the Blender UI ([MVP criterion 11](MVP.md#-acceptance-criteria); see [docs/PLAYBACK.md](docs/PLAYBACK.md#6-manual-verification-criterion-11)).

---

## 🚀 Why Viron USD Exists

Viron answers *where is everything right now?* It does not keep a history: every call returns the present state of environments, grids, locations, and entities, and the previous state is gone.

That makes a simulation hard to look back on. When an agent wanders off, a cluster forms, or a bug moves an entity somewhere it should not be, the moment has passed by the time anyone asks about it.

With Viron USD:
- Every tick of a run becomes one frame on a USD timeline.
- Entities become prims whose position and presence change over time.
- The run plays back in standard 3D tools — no custom viewer to build or maintain.
- Long runs stay manageable by splitting the recording into chunked layers stitched back together with USD value clips.

---

## 🌍 A Run, Replayed

1. A simulation drives Viron as usual — creating an environment, placing entities, moving them each tick.
2. After each tick, the recorder reads Viron's REST API and records what changed.
3. When the run ends, a `run.usda` stage is left on disk.
4. `usdview run.usda` opens it; pressing play shows every entity moving across its grid, appearing when placed and disappearing when removed.
5. For Blender, `viron-usd flatten` bakes the run into a single layer that Blender's USD importer reads as animated transforms.

---

## 🎯 Purpose

Viron USD is a **read-only observer** of Viron. It never creates, moves, or deletes anything in Viron; it only reads.

Core responsibilities:
- Reading environments, grids, locations, and entities from Viron's REST API.
- Mapping each 2D grid onto a plane in 3D space.
- Writing entity position, presence, and name as USD time samples, one time code per tick.
- Chunking long runs into per-range layers and stitching them with value clips.
- Providing a playback demo that runs end to end against a local Viron.

---

## 📄 Documents

| Document | What it covers |
|---|---|
| [CONCEPT.md](CONCEPT.md) | The problem, who it is for, non-goals, and prior art. |
| [MVP.md](MVP.md) | The smallest end-to-end slice, acceptance criteria, and the milestone list (one GitHub issue per milestone). |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Components, data flow, the Viron→USD mapping, the time-code scheme, the layer strategy for long runs, measured sizes and latency, dependencies, testing, and open questions. |
| [docs/PLAYBACK.md](docs/PLAYBACK.md) | Recording the random-walk demo against a local Viron and playing it back in usdview and Blender, with what was checked in Blender. |

---

## 🛠 Tech Stack

- Python 3.10+
- [`usd-core`](https://pypi.org/project/usd-core/) (Pixar's official OpenUSD Python wheel)
- `requests` (Viron REST API)
- pytest (testing)
- Docker Compose (running Viron locally for integration tests and the demo)

Why Python rather than Java, given that Viron is a Java/Spring Boot service: OpenUSD ships official Python bindings and no Java bindings, and Viron's state is fully readable over REST, so the recorder can live outside Viron. See [ARCHITECTURE.md § Where the recorder lives](ARCHITECTURE.md#where-the-recorder-lives).

---

## 📂 Project Structure

viron-usd/  
 ├── src/viron_usd/  
 │    ├── client.py      # Read-only Viron REST client  
 │    ├── snapshot.py    # One tick's world state, as plain data  
 │    ├── mapping.py     # Viron coordinates → USD prim paths and positions  
 │    ├── writer.py      # Time-sampled USD writing (sparse, with hold keys)  
 │    ├── chunks.py      # Per-chunk layers + value-clip stitching  
 │    ├── recorder.py    # Ties snapshot → writer, driven or polled  
 │    └── cli.py         # `viron-usd record` / `viron-usd flatten`  
 ├── examples/  
 │    └── random_walk.py # Playback demo: entities wander a Viron grid  
 ├── benchmarks/  
 │    └── stage_size.py  # Stage-size measurement for ARCHITECTURE.md § Size  
 ├── tests/              # Unit tests (fake Viron); tests/integration/ runs against a real Viron  
 ├── docs/  
 │    └── PLAYBACK.md    # Demo + usdview/Blender walkthrough  
 ├── CONCEPT.md  
 ├── MVP.md  
 ├── ARCHITECTURE.md  
 └── README.md           # This file  

---

## ▶️ Usage

For a complete walkthrough — start a local Viron, record the random-walk demo, and play it back in
usdview and Blender — see **[docs/PLAYBACK.md](docs/PLAYBACK.md)**.

Viron needs a JWT bearer token (HS256, signed with the Viron instance's `JWT_SECRET`, `iss` matching its `JWT_ISSUER`). Pass it with `--token` or the `VIRON_TOKEN` environment variable. The recorder only sends `GET` requests.

### Driven mode (recommended)

A simulation that drives Viron calls `capture(tick)` after each of its ticks, so every snapshot is consistent:

```python
from viron_usd.client import VironClient
from viron_usd.recorder import Recorder

client = VironClient("http://localhost:9999", token="...")
with Recorder.create(client, environment_id=1, out_dir="runs/demo") as recorder:
    for tick in range(100):
        step_simulation()                      # the driver's own writes to Viron
        recorder.capture(tick)                 # GET-only snapshot, written at time code `tick`
        # optional per-entity extras, time-sampled as `extras:<key>`; `color` drives displayColor:
        # recorder.capture(tick, extras={7: {"color": (0.9, 0.2, 0.1), "category": "wolf"}})
# -> runs/demo/run.usda
```

Ticks must increase from call to call. `Recorder.create(..., binary=True)` writes `run.usdc` instead.

### Poll mode

For a driver that cannot call in, `viron-usd record` captures on a wall-clock interval, numbering ticks from 0:

```bash
export VIRON_TOKEN=...
viron-usd record --environment 1 --interval 0.5 --ticks 200 --out runs/poll
# --viron-url (default http://localhost:9999), --ticks-per-second (playback rate, default 10),
# --binary (binary .usdc time-sample layers). Without --ticks it records until Ctrl-C, then saves the stage.
```

`record` writes the run as chunk layers of `--chunk-size` ticks (default 1000) stitched into
`runs/poll/run.usda` with USD value clips; `--chunk-size 0` writes a single layer instead. For
tools that do not read value clips (such as Blender), bake the run into one layer:

```bash
viron-usd flatten runs/poll/run.usda                  # -> runs/poll/run_flat.usda
viron-usd flatten runs/poll/run.usda --binary         # -> runs/poll/run_flat.usdc
viron-usd flatten runs/poll/run.usda -o baked.usda    # explicit output (extension must match --binary)
```

In driven mode, `Recorder.create(..., chunk_size=1000)` gives the same chunked layout.

Poll-mode snapshots can be torn while the driver is writing; torn ticks that a retry does not resolve are listed in the layer's `customLayerData` under `vironUsd:tornTicks`.

---

## 🧑‍💻 Development

Installation is from a git checkout only (the package is not published to PyPI):

```bash
git clone https://github.com/Preponderous-Software/viron-usd.git
cd viron-usd
python3 -m venv .venv && . .venv/bin/activate   # Python 3.10 or newer
pip install -e ".[dev]"
pytest -m "not integration"
viron-usd --help
```

The integration suite needs Docker: it starts a throwaway Viron at a pinned commit, then runs
against it (CI runs it as a separate job):

```bash
tests/integration/start_viron.sh          # clone Viron at the pinned commit, build, start, wait
pytest -m integration
tests/integration/start_viron.sh down
```

---

## 🔗 Related

- [Viron](https://github.com/Preponderous-Software/viron) — the spatial simulation service this project records.
- [OpenUSD](https://openusd.org) — Universal Scene Description.
- [OpenUSD value clips](https://openusd.org/release/api/_usd__page__value_clips.html) — the mechanism used for long runs.

---

## 📜 License

This project is licensed under the MIT License — see [LICENSE](LICENSE).

**Copyright © 2026 Daniel McCoy Stephenson.**

---

## 📬 Contact

For inquiries, feature requests, or contributions, please open an issue on the GitHub repository:  
https://github.com/Preponderous-Software/viron-usd
