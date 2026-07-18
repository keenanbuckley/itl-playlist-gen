#!/usr/bin/env python3
"""Generate an ITGmania playlist from non-ITL songs filtered by length and block level.

Scans Songs/ subdirectories (packs) for .ssc/.sm files, reads each chart's
meter (block level) and the song's audio length, then writes a playlist of
matching charts grouped by block level.

    # all dance-single charts between 1:30 and 3:00 at blocks 8-11
    python generate-itg-playlist.py --min-length 1:30 --max-length 3:00 --min-block 8 --max-block 11

    # length as seconds, any block
    python generate-itg-playlist.py --min-length 90 --max-length 200

    # specific difficulty column only
    python generate-itg-playlist.py --min-block 10 --max-block 13 --difficulty Challenge
"""

import argparse
import os
import sys

try:
    import simfile
except ImportError:
    sys.exit("simfile not installed: pip install simfile")

try:
    import mutagen
except ImportError:
    sys.exit("mutagen not installed: pip install mutagen")


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SONGS_DIR = os.path.join(SCRIPT_DIR, "Songs")


def parse_length(s):
    """Parse a length spec: seconds (float) or M:SS / H:MM:SS string."""
    s = s.strip()
    parts = s.split(":")
    if len(parts) == 1:
        return float(parts[0])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    elif len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    raise ValueError(f"Cannot parse length: {s!r}")


def fmt_length(seconds):
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def get_audio_length(song_dir, music_filename):
    """Return audio length in seconds, or None if unreadable."""
    if not music_filename:
        return None
    path = os.path.join(song_dir, music_filename)
    if not os.path.isfile(path):
        return None
    try:
        audio = mutagen.File(path)
        if audio and hasattr(audio, "info") and hasattr(audio.info, "length"):
            return audio.info.length
    except Exception:
        pass
    return None


def scan_songs(songs_dir, steps_type, difficulty_filter, min_block, max_block,
               min_length, max_length):
    """Yield (pack, song_folder, block, length_s) for each matching chart."""
    if not os.path.isdir(songs_dir):
        sys.exit(f"Songs directory not found: {songs_dir}")

    errors = []
    for pack in sorted(os.listdir(songs_dir)):
        pack_dir = os.path.join(songs_dir, pack)
        if not os.path.isdir(pack_dir):
            continue

        for song_folder in sorted(os.listdir(pack_dir)):
            song_dir = os.path.join(pack_dir, song_folder)
            if not os.path.isdir(song_dir):
                continue

            try:
                sf, _ = simfile.opendir(song_dir, strict=False)
            except Exception as e:
                errors.append(f"  {pack}/{song_folder}: {e}")
                continue

            length_s = get_audio_length(song_dir, sf.get("MUSIC", ""))

            if min_length is not None and (length_s is None or length_s < min_length):
                continue
            if max_length is not None and (length_s is None or length_s > max_length):
                continue

            seen_blocks = set()
            for chart in sf.charts:
                if chart.stepstype != steps_type:
                    continue
                if difficulty_filter and chart.difficulty.lower() != difficulty_filter.lower():
                    continue
                try:
                    block = int(chart.meter)
                except (ValueError, TypeError):
                    continue
                if min_block is not None and block < min_block:
                    continue
                if max_block is not None and block > max_block:
                    continue
                if block in seen_blocks:
                    continue
                seen_blocks.add(block)
                yield pack, song_folder, block, length_s

    for msg in errors:
        print(msg, file=sys.stderr)
    if errors:
        print(f"  ({len(errors)} songs skipped due to parse errors)", file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--songs-dir", default=DEFAULT_SONGS_DIR,
                        help=f"root Songs directory to scan (default: {DEFAULT_SONGS_DIR})")
    parser.add_argument("--min-length", metavar="LEN",
                        help="minimum song length: seconds or M:SS (inclusive)")
    parser.add_argument("--max-length", metavar="LEN",
                        help="maximum song length: seconds or M:SS (inclusive)")
    parser.add_argument("--min-block", type=int, metavar="N",
                        help="minimum block/meter level (inclusive)")
    parser.add_argument("--max-block", type=int, metavar="N",
                        help="maximum block/meter level (inclusive)")
    parser.add_argument("--steps-type", default="dance-single",
                        help="StepMania steps type to include (default: dance-single)")
    parser.add_argument("--difficulty", metavar="DIFF",
                        help="filter to a specific difficulty column (e.g. Challenge, Hard)")
    parser.add_argument("-o", "--output",
                        help="output playlist path (default: playlists/itg-<criteria>.txt)")
    args = parser.parse_args(argv)

    try:
        min_length = parse_length(args.min_length) if args.min_length else None
        max_length = parse_length(args.max_length) if args.max_length else None
    except ValueError as e:
        parser.error(str(e))

    results = list(scan_songs(
        args.songs_dir, args.steps_type, args.difficulty,
        args.min_block, args.max_block, min_length, max_length,
    ))

    if not results:
        print("No charts matched the criteria.")
        return 0

    # Deduplicate: one path per song (a song can match multiple blocks).
    seen = set()
    deduped = []
    for entry in results:
        key = (entry[0], entry[1])
        if key not in seen:
            seen.add(key)
            deduped.append(entry)
    results = deduped

    # Group by 1-minute length bucket; within each bucket sort by length then pack/song.
    by_minute = {}
    unknown_length = []
    for pack, song_folder, block, length_s in results:
        if length_s is None:
            unknown_length.append((pack, song_folder, block, length_s))
        else:
            bucket = int(length_s / 60)
            by_minute.setdefault(bucket, []).append((pack, song_folder, block, length_s))

    for bucket in by_minute:
        by_minute[bucket].sort(key=lambda r: (r[3], r[0], r[1]))

    lines = []
    for bucket in sorted(by_minute):
        entries = by_minute[bucket]
        lo = fmt_length(bucket * 60)
        hi = fmt_length((bucket + 1) * 60)
        lines.append(f"---{lo}-{hi} ({len(entries)} songs)")
        for pack, song_folder, block, length_s in entries:
            lines.append(f"{pack}\\{song_folder}")

    if unknown_length:
        lines.append(f"---unknown length ({len(unknown_length)} songs)")
        for pack, song_folder, block, length_s in sorted(unknown_length, key=lambda r: (r[0], r[1])):
            lines.append(f"{pack}\\{song_folder}")

    # Build a human-readable criteria summary for the default filename.
    parts = []
    if args.difficulty:
        parts.append(args.difficulty)
    if args.min_block is not None or args.max_block is not None:
        lo = args.min_block if args.min_block is not None else "any"
        hi = args.max_block if args.max_block is not None else "any"
        parts.append(f"block{lo}-{hi}")
    if min_length is not None or max_length is not None:
        lo = fmt_length(min_length) if min_length is not None else "any"
        hi = fmt_length(max_length) if max_length is not None else "any"
        parts.append(f"len{lo}-{hi}")
    tag = " ".join(parts) if parts else "all"

    output = args.output or os.path.join(
        os.path.dirname(SCRIPT_DIR), "playlists", f"itg-{tag}.txt"
    )
    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    total = sum(len(v) for v in by_minute.values()) + len(unknown_length)
    buckets_str = ", ".join(
        f"{fmt_length(b*60)}-{fmt_length((b+1)*60)}×{len(by_minute[b])}"
        for b in sorted(by_minute)
    )
    print(f"{total} charts across buckets: {buckets_str}")
    if unknown_length:
        print(f"  + {len(unknown_length)} with unknown length")
    print(f"Wrote {len(lines)} lines to {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
