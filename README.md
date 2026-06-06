# itl-playlist-gen

Generates an ITGmania playlist of the ITL charts that would gain a player the
most ranking points, derived from [scobility](../scobility) spice. One player at
a time, written as a standalone `.txt` you copy to the game machine.

## Modes

Everything is sourced one of two ways, picked with `--mode` (see `sources.py`).
The default is **`--mode auto`**: it compares the local snapshot's date with the
scobility API's `spice_calc_time` and uses whichever scobility is newer (falling
back to the one that's available if only one is). Force a specific source with
`--mode snapshot` / `--mode api`.

### `--mode snapshot`: local, nothing cached

- **Spice + scores** come from a local scobility snapshot
  `scobility_itl2026_<date>.json` (newest in `$SCOBILITY_SCRATCH`, default
  `~/scobility/scratch`). The player is selected by **snapshot player name**.
  Snapshots are produced by the offline
  [scobility pipeline](https://github.com/telperion/scobility/tree/api-breakout).
- **Catalog** (song folders, point ceilings, ratings) comes from the scrape's
  local `charts.json` next to the snapshot; **unlock groups** are derived from
  each chart's `unlockId` (`!= -1` means the `ITL Online 2026 Unlocks` group).
- Nothing is written to `data/ITL2026`.

### `--mode api`: everything from the APIs, cached under `data/ITL2026`

- **Spice** from the [scobility](https://scobility.telp.gg/) API
  (`/catalog/{c}/chart/all`, served from `scobility.azurewebsites.net`), cached
  to `data/ITL2026/spice.json`.
- **Catalog** scraped from the ITL API on first run (then cached as
  `data/ITL2026/charts.json`); **unlock groups** derived + cached as
  `unlock_folders.txt`.
- **Scores** from a **live GrooveStats scrape** of the entrant's current top
  scores, always up to date. The `username` is the **GrooveStats entrant name**
  (which may differ from the in-game name, e.g. `Kiki` plays as `HFocus77`),
  resolved via the scobility API player list, cached to `entrant_index.json`.
- `--refresh` re-fetches spice and the entrant index (both single calls). The
  chart catalog is static for the event, so `--refresh` does **not** re-scrape
  it; run `fetch_catalog.py` (or delete `data/ITL2026/charts.json`) for that.

In **either** mode, `--itl-json <file>` overrides the score source with a
player's `ITL2026.json` export (per-chart `ex` / `clearType` / `date`); the
username then defaults to the file name (`ITL2026 Kiki.json` -> `Kiki`).

### Refreshing the catalog (`fetch_catalog.py`)

Both files can be regenerated straight from the ITL API without the full
scobility pipeline:

```bash
python fetch_catalog.py          # -> data/ITL2026/{charts.json, unlock_folders.txt}
python fetch_catalog.py --sleep 1.0   # gentler on the server
```

It walks the per-chart endpoint to build the full catalog
(`data/ITL2026/charts.json`), then derives `data/ITL2026/unlock_folders.txt` as
every chart with `unlockId != -1`, which matches the actual Unlocks pack
exactly. `--mode api` does the same scrape automatically on a cache miss; run
`fetch_catalog.py` to rebuild the catalog deliberately (the scrape hits a live
third-party server one chart at a time, so it takes a few minutes).

## Usage

```bash
# auto (default): newer of snapshot / API; snapshot scores by player name,
# api scores by live GrooveStats scrape
python generate_playlist.py "HFocus77"

# force a specific source
python generate_playlist.py "HFocus77" --mode snapshot
python generate_playlist.py "HFocus77" --mode api

# either mode, scores from an ITL2026.json export instead
python generate_playlist.py --mode api --itl-json "ITL2026 Kiki.json"

# only charts the fit predicts you'd score at least 70% EX on
python generate_playlist.py "HFocus77" --mode api --min-ex 70

# api mode, refresh cached spice + entrant index
python generate_playlist.py "HFocus77" --mode api --refresh

# unknown name -> prints close matches / the available players
python generate_playlist.py "whoami"
```

Output defaults to `playlists/ITL - <username>.txt`. Copy it to
`<ITGmania>/Save/<profile>/Playlists/` on the game machine.

### Spice-ordered playlist (no player)

`spice_playlist.py` writes every chart ordered by ascending spice, the true
difficulty order, which ignores block ratings. It takes the same `--mode`
(auto/snapshot/api) as the main tool:

```bash
python spice_playlist.py            # auto: newer of snapshot / API (default)
python spice_playlist.py --mode snapshot
python spice_playlist.py --mode api
```

Output defaults to `playlists/ITL - spice order.txt`: a full `---All (spice
order)` section, then bins of 10 charts (`--bin-size` to change) each labeled
with that bin's min/max spice. Every chart appears in both. Unspiced charts are
skipped and counted.

`--min-ex` drops any chart whose *predicted* EX (what the fit thinks you'd
score, not a pass probability) is below the cutoff, so the list stays to charts
you'd actually score well on. Charts you've already passed are always kept; the
cutoff only gates new charts:

- `--min-ex auto` (the **default**) sets the cutoff to the **p10 of your own
  passing scores**, the EX you usually at least reach when you pass. `auto:P`
  uses a different percentile (e.g. `auto:20` is stricter).
- `--min-ex 70` sets an explicit cutoff; `--min-ex none` disables filtering.

Predicted EX clusters fairly high, so the cutoff bites mostly in the 65-90 range.
For one player: full catalog ~970 playlist lines, `auto` (p10 ~68%) ~530,
`auto:20` (~70%) ~290, explicit `75` ~95.

The playlist also ends with two sections: `---Unmastered` (charts you've passed
fewer than `--practice-passes` times, default 3, AND scored under `--practice-ex`,
default 85, including never-played, in ascending spice order) and
`---Unmastered unlocks` (the same list restricted to unlock-pack charts). These
need a pass count, so they're only emitted in snapshot/api mode, not from an
`--itl-json` export. Charts *required* to trigger unlocks aren't exposed by the
API or the export, so they can't be included.

No third-party dependencies. Standard-library Python 3 only (the API mode uses
`urllib`).

## How it works

`scobility.py` loads spice (from a snapshot or the live API), fits the player's
two-segment "horizon" curve (score quality vs. log-spice) over the charts they've
actually played, then inverts it to a target EX for every chart with known spice
and converts that to potential SP/EP/RP. `generate_playlist.py` groups those
targets into playlist sections (all +RP, per block rating, by pass month).
`itldata.py` holds the ITL scoring math and merges chart-static fields from
charts.json with per-player scores (from the snapshot or an export), deriving
each chart's on-disk group from `unlock_folders.txt`.

## Files

| File | Role |
|---|---|
| `generate_playlist.py` | CLI entrypoint; score resolution, target grouping, playlist writing |
| `sources.py` | the two modes: resolves spice/catalog/unlock (and api-mode caching) |
| `scobility.py` | spice loader (snapshot/API/raw), player lookup, horizon fit, RP targets |
| `spice_playlist.py` | player-independent playlist of every chart ordered by spice |
| `groovestats.py` | live GrooveStats score scrape + name suggestions |
| `fetch_catalog.py` | scrape charts.json + derive unlock_folders.txt from the ITL API |
| `itldata.py` | ITL scoring math; per-player chart model |
| `data/ITL2026/` | api-mode cache: `spice.json`, `charts.json`, `unlock_folders.txt`, `entrant_index.json` |
