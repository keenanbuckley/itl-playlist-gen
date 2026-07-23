#!/usr/bin/env python3
"""Scan Songs/ and write chart metadata to a JSON cache.

Run this once (or after adding/changing songs) to build the cache; then
generate-itg-playlist.py reads from it instead of re-scanning each time.

    python parse_song_data.py
    python parse_song_data.py --songs-dir /path/to/Songs --output song_cache.json
    python parse_song_data.py --workers 8
"""

import argparse
import concurrent.futures
import datetime
import json
import os
import sys

try:
    import simfile
    from simfile.notes import NoteData, NoteType as SNoteType
    from simfile.timing import Beat
except ImportError:
    sys.exit("simfile not installed: pip install simfile")

try:
    import mutagen
except ImportError:
    sys.exit("mutagen not installed: pip install mutagen")


import logging

log = logging.getLogger(__name__)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SONGS_DIR = os.path.join(SCRIPT_DIR, "Songs")
DEFAULT_CACHE = os.path.join(SCRIPT_DIR, "song_cache.json")

JACK_NOTE_TYPES = frozenset({SNoteType.TAP, SNoteType.HOLD_HEAD, SNoteType.ROLL_HEAD})
# Two same-column notes within 1/8 note (= 1/2 beat) count as a jack.
JACK_THRESHOLD = Beat(1, 2)


def parse_note_counts(chart):
    """Return (note_count, jump_count, jack_count) for a simfile chart.

    Uses Beat-based comparison so jack detection is correct across measures
    with different subdivisions (the previous row-index approach was not).
    """
    if not chart.notes:
        return 0, 0, 0

    note_count = jump_count = jack_count = 0
    last_beat_by_col: dict[int, Beat] = {}
    prev_beat: Beat | None = None
    notes_this_beat = 0

    for note in NoteData(chart):
        if note.note_type not in JACK_NOTE_TYPES:
            continue
        beat = note.beat
        col = note.column

        if prev_beat is None or beat != prev_beat:
            if notes_this_beat >= 2:
                jump_count += 1
            notes_this_beat = 0
            prev_beat = beat

        note_count += 1
        notes_this_beat += 1

        if col in last_beat_by_col and beat - last_beat_by_col[col] <= JACK_THRESHOLD:
            jack_count += 1
        last_beat_by_col[col] = beat

    if notes_this_beat >= 2:
        jump_count += 1

    return note_count, jump_count, jack_count


def get_audio_length(song_dir, music_filename):
    if not music_filename:
        log.debug("get_audio_length: no music filename for %s", song_dir)
        return None
    path = os.path.join(song_dir, music_filename)
    if not os.path.isfile(path):
        lower = music_filename.lower()
        match = next((f for f in os.listdir(song_dir) if f.lower() == lower), None)
        if match:
            path = os.path.join(song_dir, match)
        else:
            audio_exts = {".ogg", ".mp3", ".wav", ".flac", ".opus", ".m4a"}
            audio_files = [f for f in os.listdir(song_dir) if os.path.splitext(f)[1].lower() in audio_exts]
            if len(audio_files) == 1:
                path = os.path.join(song_dir, audio_files[0])
                log.debug("get_audio_length: falling back to only audio file: %s", path)
            else:
                log.debug("get_audio_length: file not found: %s", path)
                return None
    try:
        audio = mutagen.File(path)
        log.debug("get_audio_length: mutagen.File(%s) = %r (truthy=%s)", path, audio, bool(audio))
        if audio is None:
            log.debug("get_audio_length: mutagen returned None for %s", path)
            return None
        if not hasattr(audio.info, "length"):
            log.debug("get_audio_length: audio.info has no length attr for %s", path)
            return None
        log.debug("get_audio_length: length=%.2f for %s", audio.info.length, path)
        return audio.info.length
    except Exception as e:
        log.debug("get_audio_length: exception for %s: %s", path, e)
    return None


def process_song(pack, song_folder, song_dir):
    """Parse one song directory; return a metadata dict, or None if no simfile found."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        sf, _ = simfile.opendir(song_dir, strict=False)
    except FileNotFoundError:
        return None
    length_s = get_audio_length(song_dir, sf.get("MUSIC", ""))
    song_label = f"{pack}/{song_folder}"

    if length_s is None:
        log.error("%s: missing audio length", song_label)

    charts = []
    for chart in sf.charts:
        note_count, jump_count, jack_count = parse_note_counts(chart)
        try:
            meter = int(chart.meter)
        except (ValueError, TypeError):
            meter = None

        chart_label = f"{song_label} [{chart.stepstype} {chart.difficulty}]"
        if meter is None:
            log.error("%s: missing meter", chart_label)
        if not note_count:
            log.error("%s: zero note count", chart_label)

        charts.append({
            "steps_type": chart.stepstype,
            "difficulty": chart.difficulty,
            "meter": meter,
            "notes": note_count,
            "jumps": jump_count,
            "jacks": jack_count,
        })

    return {
        "pack": pack,
        "song_folder": song_folder,
        "title": sf.title or "",
        "artist": sf.get("ARTIST", "") or "",
        "length_s": length_s,
        "charts": charts,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--songs-dir", default=DEFAULT_SONGS_DIR,
                        help=f"root Songs directory to scan (default: {DEFAULT_SONGS_DIR})")
    parser.add_argument("--output", default=DEFAULT_CACHE,
                        help=f"output JSON cache path (default: {DEFAULT_CACHE})")
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 4,
                        help="parallel worker threads (default: cpu count)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if not os.path.isdir(args.songs_dir):
        sys.exit(f"Songs directory not found: {args.songs_dir}")

    tasks = []
    for pack in sorted(os.listdir(args.songs_dir)):
        pack_dir = os.path.join(args.songs_dir, pack)
        if not os.path.isdir(pack_dir):
            continue
        for song_folder in sorted(os.listdir(pack_dir)):
            song_dir = os.path.join(pack_dir, song_folder)
            if os.path.isdir(song_dir):
                tasks.append((pack, song_folder, song_dir))

    print(f"Scanning {len(tasks)} songs with {args.workers} workers...")

    songs = []
    errors = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(process_song, pack, folder, d): (pack, folder)
            for pack, folder, d in tasks
        }
        for future in concurrent.futures.as_completed(futures):
            pack, folder = futures[future]
            try:
                result = future.result()
                if result is not None:
                    songs.append(result)
            except Exception as e:
                errors.append(f"  {pack}/{folder}: {e}")

    songs.sort(key=lambda s: (s["pack"], s["song_folder"]))

    cache = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "songs_dir": os.path.abspath(args.songs_dir),
        "songs": songs,
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)

    for msg in errors:
        print(msg, file=sys.stderr)
    suffix = f" ({len(errors)} errors)" if errors else ""
    print(f"Wrote {len(songs)} songs to {args.output}{suffix}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
