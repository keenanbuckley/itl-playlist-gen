# itl-playlist-gen

Generates an ITGmania playlist of the ITL charts that would gain a player the
most ranking points, derived from [scobility](../scobility) spice. One player at
a time, written as a standalone `.txt` you copy to the game machine.

## Modes

Everything is sourced one of two ways, picked with `--mode` (see `sources.py`).
The default is **`--mode auto`**, which chooses spice and scores independently:

- **Spice** (and the catalog): compares the local snapshot's date with the
  scobility API's `spice_calc_time` and uses whichever scobility is newer.
- **Scores**: prefers the live GrooveStats scrape (always the newest), and falls
  back to the snapshot if GrooveStats is unreachable.

So auto can read spice from a newer local snapshot while still using your current
live scores. Force a single coupled source with `--mode snapshot` (snapshot
spice + snapshot scores) or `--mode api` (API spice + live scores).

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

# nudge target EX by your per-tech strengths/weaknesses (beyond spice)
python generate_playlist.py "HFocus77" --mode api --tech-target

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

Output defaults to `playlists/ITL - spice order.txt`. It contains a full `---All
(spice order)` section, then bins of 10 charts (`--bin-size` to change) each
labeled with that bin's min/max spice, then two catalog-wide sections:

- `Spice traps (hardest for their block)` - charts whose spice most exceeds the
  average for their block rating (`--trap-count`, default 40).
- `Spice gifts (easiest for their block)` - the opposite: spice well below their
  block average, i.e. easier to score than the rating suggests.
- `Tech: <type>` - each chart under its dominant tech (crossover, bracket,
  footswitch, jack, sideswitch, doublestep, stamina), levels normalized per tech.

Every chart can appear in several sections. Unspiced charts are skipped and counted.

### Spice plot (`spice_plot.py`)

Renders the scobility scatter as a standalone SVG: each played chart at (chart
spice, score quality), the two-segment horizon fit over it, points sized by pass
count and tinted by recency, and any `--spice-iqr` outliers as hollow rings.
Same source/score options as `generate_playlist.py`.

```bash
python spice_plot.py "HFocus77"
python spice_plot.py "HFocus77" --mode api --spice-iqr 4.0
```

Writes `plots/ITL - <username>.svg` and, if a renderer is on the system
(`rsvg-convert`, `inkscape`, or the `cairosvg` module), a matching `.png`. The
SVG itself needs no dependencies; only the optional PNG render does.

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

`--spice-iqr K` (off by default) drops spice outliers from the horizon fit:
passed charts whose spice falls outside `Q1 - K*IQR .. Q3 + K*IQR` of your passed
charts are excluded from the fit (they still appear in the playlist). `K=4.0` is
a good value - it rejects only egregious traps (a [07] like Gruntilda's Lair at
spice ~1.4 for a player who otherwise passes ~0.5) while rejecting nothing on the
full catalog. Smaller K (e.g. the textbook 1.5) also trims legitimate low-spice
charts, because the catalog spice is right-skewed.

`--fit` chooses the spice fit. The default `adaptive` uses the two-segment
horizon fit when the player has at least `--adaptive-n` played charts (40), and a
linear fit with the slope shrunk toward flat when they have fewer (horizon
overfits on sparse data). Cross-validation across ~400 players found `adaptive`
predicts held-out EX best overall; `--fit horizon` forces the plain horizon fit,
matching official scobility. (See `cv_eval.py` for the comparison harness.)

`--tech-target` (off by default) nudges each chart's target EX by your per-tech
strengths and weaknesses, beyond what spice predicts: it ridge-regresses your
score-quality residual (actual minus the spice fit) on the chart's z-scored tech
features, then adds that back when inverting to a target. Targets rise on charts
heavy in tech you're strong at and fall where you're weak. `--tech-cap` (default
5) bounds how far a chart's target can move from the spice-only value, since
unplayed charts can carry tech the fit never saw. The gain is real but small in
cross-validation (~0.04-0.06 EX MAE, never worse; see `cv_tech.py`), so it's
opt-in. It's the same per-player tech model `tech_plot.py` visualizes.

### Tech profile (`tech_plot.py`)

Renders how a player over/under-performs on charts heavy in a given tech, beyond
what spice alone predicts. It regresses your per-chart score quality (minus your
spice fit) on z-scored chart tech features, then shows the reliable axes (stamina,
brackets, footswitch, crossover, no CMOD) two ways: an **EX%-impact** bar chart (how
many EX points a chart heavy in that tech is worth to you, with error bars) and a
**percentile radar** vs the field (50 = average), each axis labelled with a letter
grade (C = average, S/A/B above, D/E/F below). Same source/score options as
`generate_playlist.py`.

```bash
python tech_plot.py "HFocus77"
python tech_plot.py "HFocus77" --mode api
```

Writes `plots/ITL - tech bars - <username>.svg` and `... tech radar - <username>.svg`
(plus `.png` if a renderer is present, like `spice_plot.py`). A profile needs
enough scored charts: it warns under 60 and refuses under 20.

The radar percentiles are placed against a **population cohort** of other players'
profiles. In snapshot mode the cohort is rebuilt from the snapshot (its spice and
scores match yours). In api mode it uses the bundled
`data/ITL2026/tech_population.json` - an API-derived cohort of ~970 players,
stamped with the `spice_calc_time` it was built against; `tech_plot.py` warns if
that stamp has drifted from the live spice. Rebuild it with `build_tech_population.py`
when spice recomputes (it scrapes ~400 chart leaderboards from the ITL API, so run
it deliberately):

```bash
python build_tech_population.py            # -> data/ITL2026/tech_population.json
python build_tech_population.py --sleep 1.0   # gentler on the server
```

### Sections

`generate_playlist.py` emits these `---` sections (a chart can appear in several):

- `All +RP` / per-block `[NN] +RP` - everything with RP to gain.
- `Passed +RP`, `Passed +SP`, `Passed +EP` - already-passed charts with room to
  gain, overall and split by points pool.
- `Efficient RP` - most RP per unit of (linear) spice: the low-hanging fruit.
- `Underperformed (vs your fit)` - played charts you scored below what your skill
  curve predicts: your weak spots, biggest gap first.
- `At your ceiling` - charts at and just above your fitted spice horizon.
- `Best score from <month>` / `Never passed` - passed charts by month, and new
  charts (spice order).
- `Unmastered` / `Unmastered unlocks` - passed fewer than `--practice-passes`
  times (3) AND under `--practice-ex` (85), including never-played; and the same
  restricted to unlock-pack charts. These need a pass count, so they're only
  emitted in snapshot/api mode, not from an `--itl-json` export. (Charts
  *required* to trigger unlocks aren't exposed by the API or export.)
- `Tech: <name>` (with `--tech-sections`) - one section per reliable tech
  (stamina/brackets/footswitch/crossover/no CMOD), each listing RP-gaining charts
  whose dominant reliable tech is that one, spice-ordered. Sections run **weakest
  tech first**, and each header shows your grade and percentile for that tech
  (e.g. `---Tech: footswitch (you: E, p16)`), so the charts you most need to
  practice surface at the top. Uses the same per-player tech profile as
  `tech_plot.py` and the same percentile cohort.

No third-party dependencies. Standard-library Python 3 only (the API mode uses
`urllib`).

## How it works

`scobility.py` loads spice (from a snapshot or the live API), fits the player's
score-quality vs. log-spice curve (the adaptive fit by default; see `--fit`) over the charts they've
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
| `spice_plot.py` | SVG (+ PNG) spice-vs-score-quality scatter with the fit |
| `tech_plot.py` | SVG (+ PNG) per-player tech profile: EX%-impact bars + percentile radar |
| `tech.py` | tech-feature ridge fit, EX%-impact, percentile cohort math |
| `build_tech_population.py` | rebuild the bundled radar cohort from the live API |
| `cv_eval.py` | cross-validate spice->EX fit models per player (analysis) |
| `cv_tech.py` | cross-validate the tech-aware target adjustment vs spice-only (analysis) |
| `groovestats.py` | live GrooveStats score scrape + name suggestions |
| `fetch_catalog.py` | scrape charts.json + derive unlock_folders.txt from the ITL API |
| `itldata.py` | ITL scoring math; per-player chart model |
| `data/ITL2026/` | bundled catalog (`charts.json`, `unlock_folders.txt`, `tech_population.json`) + ignored api caches (`spice.json`, `catalog.json`, `entrant_index.json`) |
