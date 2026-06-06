# itl-playlist-gen

Generates an ITGmania playlist of the ITL charts that would gain a player the
most ranking points, from local [scobility](../scobility) output. It's a
descendant of the original `itl2026` playlist toolkit, with two changes:

- **Spice and player scores come from a local scobility snapshot**, not the live
  API. Refreshing the playlist for a new snapshot needs no new game export.
- **One player at a time, by username**, writing a standalone `.txt` you copy to
  the game machine instead of writing into local profiles.

## Inputs

1. **Spice** — from one of two sources:
   - a **scobility snapshot** `scobility_itl2026_<date>.json` from the offline
     pipeline (`scratch/scobility.py`), auto-picked as the newest in
     `$SCOBILITY_SCRATCH` (default `~/scobility/scratch`); or
   - the **live scobility API** (`--spice api`), which serves spice but not
     scores — so it must be paired with a non-snapshot score source.
2. **Scores** — from one of three sources:
   - the **snapshot**, selected by **username** (default); or
   - a player's **`ITL2026.json` game export** (`--itl-json <file>`), read for
     its per-chart `ex` / `clearType` / `date`. With an export, the username is
     optional and defaults to the file name (`ITL2026 Kiki.json` -> `Kiki`); or
   - a **live GrooveStats scrape** (`--scrape`) of the entrant's *current* top
     scores — one API request, always up to date. The `username` here is the
     **GrooveStats entrant name** (which may differ from the in-game profile
     name, e.g. `Kiki` plays as `HFocus77`), resolved to an entrant id via an
     index built from the scraped `entrant_info` dir and cached in
     `entrant_index.json`. A wrong name prints close matches; `--rebuild-index`
     forces a refresh.
3. **The scrape `charts.json`** — the complete chart catalog, auto-discovered at
   `<scratch>/itl2026_data/<newest date>/charts.json`. Supplies every chart's
   song folder, block rating (`meter`), and `points` / `pointsScoring` /
   `pointsPassing` ceilings. Being the full catalog, it covers all charts (so the
   playlist can recommend charts the player hasn't even revealed yet), not just
   one player's unlocked set.
4. **`unlock_folders.txt`** — the bundled, authoritative list of song folders in
   the `ITL Online 2026 Unlocks` pack. charts.json has the song folder but not
   the on-disk group, so this decides it: a chart lives in
   `ITL Online 2026 Unlocks` iff its folder is listed here, otherwise
   `ITL Online 2026`. It was extracted from the actual Unlocks pack (190 song
   folders, 150 SP / 40 DP). A player's `ITL2026.json` export is **not** a
   reliable source for this — its `unlockFolders` only lists the unlocks that
   player has revealed, not the full pack.

charts.json (folders/points) and unlock_folders.txt (groups) are always
required; spice and scores each come from one of the two sources above. The tool
warns if any `unlock_folders.txt` entry is missing from charts.json (a sign the
list is stale relative to the scrape).

### Refreshing `unlock_folders.txt`

It only changes if ITL re-releases the Unlocks pack. To regenerate from a pack
zip:

```bash
python3 - <<'PY'
import zipfile
z = zipfile.ZipFile('ITL Online 2026 Unlocks.zip')
folders = sorted({
    p.split('/')[1]
    for n in z.namelist()
    for p in [n]
    if len(p.split('/')) >= 3 and p.split('/')[2].lower().endswith(('.ssc', '.sm'))
})
open('unlock_folders.txt', 'w').write('\n'.join(folders) + '\n')
PY
```

## Usage

```bash
# snapshot spice + snapshot scores (newest snapshot + charts.json in $SCOBILITY_SCRATCH)
python generate_playlist.py "PlayerName"

# live API spice + a player's export -- no snapshot needed
python generate_playlist.py --spice api --itl-json "ITL2026 Kiki.json"

# live API spice + live current GrooveStats scores (by entrant name)
python generate_playlist.py "HFocus77" --scrape --spice api

# snapshot spice + a player's export (e.g. someone not yet in the snapshot)
python generate_playlist.py "Kiki" --itl-json "ITL2026 Kiki.json"

# only charts the fit predicts you'd score at least 70% EX on
python generate_playlist.py --spice api --itl-json "ITL2026 Kiki.json" --min-ex 70

# explicit snapshot
python generate_playlist.py "PlayerName" \
    --snapshot ~/scobility/scratch/scobility_itl2026_20260605.json

# unknown name -> prints the list of available players
python generate_playlist.py "whoami"
```

Output defaults to `playlists/ITL - <username>.txt`. Copy it to
`<ITGmania>/Save/<profile>/Playlists/` on the game machine.

### Spice-ordered playlist (no player)

`spice_playlist.py` writes every chart ordered by ascending spice — the true
difficulty order, which ignores block ratings:

```bash
python spice_playlist.py            # snapshot spice
python spice_playlist.py --spice api
```

Output defaults to `playlists/ITL - spice order.txt`, with `---N spice` band
dividers (use `--no-headers` for a flat list). Unspiced charts are skipped and
counted.

`--min-ex` drops any chart whose *predicted* EX (what the fit thinks you'd
score, not a pass probability) is below the cutoff, so the list stays to charts
you'd actually score well on:

- `--min-ex auto` (the **default**) sets the cutoff to the **p10 of your own
  passing scores** — the EX you usually at least reach when you pass. `auto:P`
  uses a different percentile (e.g. `auto:20` is stricter).
- `--min-ex 70` sets an explicit cutoff; `--min-ex none` disables filtering.

Predicted EX clusters fairly high, so the cutoff bites mostly in the 65–90 range.
For one player: full catalog ≈ 970 playlist lines, `auto` (p10 ≈ 68%) ≈ 530,
`auto:20` (≈ 70%) ≈ 290, explicit `75` ≈ 95.

No third-party dependencies — standard-library Python 3 only (the API mode uses
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
| `generate_playlist.py` | CLI entrypoint; target grouping + playlist writing |
| `scobility.py` | spice loader (snapshot/API), player lookup, horizon fit, RP targets |
| `spice_playlist.py` | player-independent playlist of every chart ordered by spice |
| `groovestats.py` | entrant name->id index + live score scrape |
| `itldata.py` | ITL scoring math; per-player chart model |
| `unlock_folders.txt` | authoritative `ITL Online 2026 Unlocks` song folders (group discriminator) |
