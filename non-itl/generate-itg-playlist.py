#!/usr/bin/env python3
"""Generate an ITGmania playlist from non-ITL songs filtered by length and block level.

Reads from a JSON cache built by parse_song_data.py and writes a playlist of
matching charts grouped and sorted by a chosen metric.

    # build the cache first (once, or after adding songs)
    python parse_song_data.py

    # all dance-single charts between 1:30 and 3:00 at blocks 8-11
    python generate-itg-playlist.py --min-length 1:30 --max-length 3:00 --min-block 8 --max-block 11

    # specific difficulty column, sorted and bucketed by block level
    python generate-itg-playlist.py --min-block 10 --max-block 13 --difficulty Challenge --sort block

    # sorted and bucketed by jack+jump density
    python generate-itg-playlist.py --difficulty Challenge --sort j2j

    # sorted and bucketed by BPM
    python generate-itg-playlist.py --sort bpm

    # only songs from specific packs (repeatable and/or comma-separated)
    python generate-itg-playlist.py --include-pack "In The Groove 3" --include-pack "Egg Carton 4"

    # exclude specific packs
    python generate-itg-playlist.py --exclude-pack "3guys1pack,5guys1pack"

    # include/exclude packs listed one per line in a file (# comments and blank lines ignored)
    python generate-itg-playlist.py --include-pack-file my-packs.txt
    python generate-itg-playlist.py --exclude-pack-file bad-packs.txt
"""

import argparse
import json
import os
import sys


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CACHE = os.path.join(SCRIPT_DIR, "song_cache.json")

SORT_CHOICES = ["length", "avg_nps", "block", "j2j", "jumps", "j10j", "bpm"]

# Bucket sizes for each sort metric (except block which buckets by integer level).
BUCKET_SIZE = {"length": 60, "avg_nps": 1, "j2j": 50, "jumps": 25, "j10j": 100, "bpm": 20}


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


def parse_pack_list(values):
    """Flatten a list of comma-separated --*-pack args into a set of lowercased names."""
    packs = set()
    for value in values or []:
        for name in value.split(","):
            name = name.strip()
            if name:
                packs.add(name.lower())
    return packs


def load_pack_file(path):
    """Read one pack name per line from a file (blank lines and #comments ignored)."""
    packs = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                packs.add(line.lower())
    return packs


def load_charts(cache_path, steps_type, difficulty_filter, min_block, max_block,
                min_length, max_length, include_packs=None, exclude_packs=None):
    """Load matching (song, chart) pairs from the cache.

    Returns one entry per matching chart with keys:
      pack, song_folder, length, avg_nps, block, j2j
    """
    try:
        with open(cache_path, encoding="utf-8") as f:
            cache = json.load(f)
    except FileNotFoundError:
        sys.exit(
            f"Cache not found: {cache_path}\n"
            "Run parse_song_data.py first to build it."
        )

    results = []
    for song in cache["songs"]:
        length_s = song["length_s"]
        pack_lower = song["pack"].lower()

        if include_packs and pack_lower not in include_packs:
            continue
        if exclude_packs and pack_lower in exclude_packs:
            continue
        if min_length is not None and (length_s is None or length_s < min_length):
            continue
        if max_length is not None and (length_s is None or length_s > max_length):
            continue

        for chart in song["charts"]:
            if chart["steps_type"] != steps_type:
                continue
            if difficulty_filter and chart["difficulty"].lower() != difficulty_filter.lower():
                continue
            meter = chart["meter"]
            if min_block is not None and (meter is None or meter < min_block):
                continue
            if max_block is not None and (meter is None or meter > max_block):
                continue

            avg_nps = chart["notes"] / length_s if length_s else None
            min_bpm, max_bpm = chart.get("min_bpm"), chart.get("max_bpm")
            bpm = max_bpm

            results.append({
                "pack": song["pack"],
                "song_folder": song["song_folder"],
                "length": length_s,
                "avg_nps": avg_nps,
                "block": meter,
                "jumps": chart["jumps"],
                "j2j": chart["jacks"] + 2 * chart["jumps"],
                "j10j": chart["jacks"] + 10 * chart["jumps"],
                "bpm": bpm,
                "min_bpm": min_bpm,
                "max_bpm": max_bpm,
            })

    return results


def bucket_of(entry, sort_by):
    """Return the bucket index for an entry, or None for unknown."""
    val = entry[sort_by]
    if val is None:
        return None
    if sort_by == "block":
        return int(val)
    return int(val / BUCKET_SIZE[sort_by])


def bucket_label(bucket, sort_by, count):
    if sort_by == "length":
        lo = fmt_length(bucket * BUCKET_SIZE["length"])
        hi = fmt_length((bucket + 1) * BUCKET_SIZE["length"])
        return f"---{lo}-{hi} ({count} charts)"
    if sort_by == "block":
        return f"---[{bucket:02d}] ({count} charts)"
    if sort_by == "avg_nps":
        return f"---{bucket}-{bucket + 1} NPS ({count} charts)"
    if sort_by == "j2j":
        lo = bucket * BUCKET_SIZE["j2j"]
        hi = lo + BUCKET_SIZE["j2j"]
        return f"---j2j {lo}-{hi} ({count} charts)"
    if sort_by == "jumps":
        lo = bucket * BUCKET_SIZE["jumps"]
        hi = lo + BUCKET_SIZE["jumps"]
        return f"---{lo}-{hi} jumps ({count} charts)"
    if sort_by == "j10j":
        lo = bucket * BUCKET_SIZE["j10j"]
        hi = lo + BUCKET_SIZE["j10j"]
        return f"---j10j {lo}-{hi} ({count} charts)"
    if sort_by == "bpm":
        lo = bucket * BUCKET_SIZE["bpm"]
        hi = lo + BUCKET_SIZE["bpm"]
        return f"---{lo}-{hi} BPM ({count} charts)"


def sort_key(entry, sort_by):
    val = entry[sort_by]
    return (val is None, val or 0, entry["pack"], entry["song_folder"])


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--cache", default=DEFAULT_CACHE,
                        help=f"song cache JSON from parse_song_data.py (default: {DEFAULT_CACHE})")
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
    parser.add_argument("--include-pack", action="append", metavar="PACK",
                        help="only include songs from this pack (repeatable, or comma-separated)")
    parser.add_argument("--exclude-pack", action="append", metavar="PACK",
                        help="exclude songs from this pack (repeatable, or comma-separated)")
    parser.add_argument("--include-pack-file", metavar="FILE",
                        help="file with one pack name per line to include "
                             "(merged with --include-pack)")
    parser.add_argument("--exclude-pack-file", metavar="FILE",
                        help="file with one pack name per line to exclude "
                             "(merged with --exclude-pack)")
    parser.add_argument("--sort", choices=SORT_CHOICES, default="length",
                        help="sort and bucket order: length (default, 1-min buckets), "
                             "avg_nps (notes/sec, 1-NPS buckets), "
                             "block (meter level), "
                             "jumps (jump count, buckets of 25), "
                             "j2j (jacks + 2*jumps, buckets of 50), "
                             "j10j (jacks + 10*jumps, buckets of 100), "
                             "bpm (chart tempo, buckets of 20)")
    parser.add_argument("-o", "--output",
                        help="output playlist path (overrides --output-dir and auto-naming)")
    parser.add_argument("--output-dir",
                        default=os.path.join(os.path.dirname(SCRIPT_DIR), "playlists"),
                        help="directory for auto-named output files "
                             "(default: ../playlists relative to this script)")
    args = parser.parse_args(argv)

    try:
        min_length = parse_length(args.min_length) if args.min_length else None
        max_length = parse_length(args.max_length) if args.max_length else None
    except ValueError as e:
        parser.error(str(e))

    include_packs = parse_pack_list(args.include_pack)
    exclude_packs = parse_pack_list(args.exclude_pack)
    if args.include_pack_file:
        include_packs |= load_pack_file(args.include_pack_file)
    if args.exclude_pack_file:
        exclude_packs |= load_pack_file(args.exclude_pack_file)

    results = load_charts(
        args.cache, args.steps_type, args.difficulty,
        args.min_block, args.max_block, min_length, max_length,
        include_packs, exclude_packs,
    )

    if not results:
        print("No charts matched the criteria.")
        return 0

    # Group by bucket of the sort metric; within each bucket sort by that metric.
    by_bucket: dict[int, list] = {}
    unknown = []
    for entry in results:
        b = bucket_of(entry, args.sort)
        if b is None:
            unknown.append(entry)
        else:
            by_bucket.setdefault(b, []).append(entry)

    for b in by_bucket:
        by_bucket[b].sort(key=lambda e: sort_key(e, args.sort))

    lines = []
    for b in sorted(by_bucket):
        entries = by_bucket[b]
        lines.append(bucket_label(b, args.sort, len(entries)))
        for e in entries:
            lines.append(f"{e['pack']}\\{e['song_folder']}")

    if unknown:
        unknown.sort(key=lambda e: sort_key(e, args.sort))
        lines.append(f"---unknown {args.sort} ({len(unknown)} charts)")
        for e in unknown:
            lines.append(f"{e['pack']}\\{e['song_folder']}")

    # Remove adjacent duplicate song lines (headers are transparent: a song at
    # the end of one bucket and the start of the next still collapses).
    deduped = []
    last_song = None
    for line in lines:
        if line.startswith("---"):
            deduped.append(line)
        elif line != last_song:
            deduped.append(line)
            last_song = line
    lines = deduped

    # Build a human-readable criteria summary for the default filename.
    parts = []
    if args.difficulty:
        parts.append(args.difficulty)
    if args.min_block is not None or args.max_block is not None:
        lo = args.min_block if args.min_block is not None else "any"
        hi = args.max_block if args.max_block is not None else "any"
        if lo == hi:
            parts.append(f"lvl{lo}")
        else:
            parts.append(f"lvl{lo}-{hi}")
    if min_length is not None or max_length is not None:
        lo = fmt_length(min_length) if min_length is not None else "any"
        hi = fmt_length(max_length) if max_length is not None else "any"
        parts.append(f"len{lo}-{hi}")
    if include_packs:
        parts.append(f"{len(include_packs)}pack" if len(include_packs) != 1 else next(iter(include_packs)))
    if exclude_packs:
        parts.append(f"no-{len(exclude_packs)}pack")
    if args.sort != "length":
        parts.append(f"by-{args.sort}")
    tag = " ".join(parts) if parts else "all"

    output = args.output or os.path.join(args.output_dir, f"{tag}.txt")
    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    total = sum(len(v) for v in by_bucket.values()) + len(unknown)
    print(f"{total} charts in {len(by_bucket)} buckets (sort: {args.sort})")
    print(f"Wrote {len(lines)} lines to {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
