# non-itl playlist generator

Scripts for generating ITGmania playlists from your local song library. These are **not** for ITL playlists — just for general ITG gameplay from your own Songs folder.

## Scripts

- **`parse_song_data.py`** — scans your `Songs/` directory and writes chart metadata (length, NPS, block level, jack density, etc.) to `song_cache.json`. Run this once, or again after adding/changing songs.
- **`generate-itg-playlist.py`** — reads the cache and outputs a playlist filtered by length, block level, difficulty, and sorted/bucketed by a chosen metric.

## Usage

```bash
# Build the cache first (once, or after adding songs)
python parse_song_data.py

# All dance-single charts between 1:30 and 3:00 at blocks 8-11
python generate-itg-playlist.py --min-length 1:30 --max-length 3:00 --min-block 8 --max-block 11

# Specific difficulty column, sorted and bucketed by block level
python generate-itg-playlist.py --min-block 10 --max-block 13 --difficulty Challenge --sort block

# Sorted and bucketed by jack+jump density
python generate-itg-playlist.py --difficulty Challenge --sort j2j
```

## Dependencies

```bash
pip install simfile mutagen
```

## Songs folder

Place your ITG song packs under `Songs/` (or pass `--songs-dir /path/to/Songs` to override).
