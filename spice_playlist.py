#!/usr/bin/env python3
"""Generate a playlist of every ITL chart ordered by ascending spice.

Player-independent: spice comes from a scobility snapshot or the live API, song
folders/groups from charts.json + unlock_folders.txt. Charts without a spice
rating are skipped (and reported).

    python spice_playlist.py
    python spice_playlist.py --spice api
"""

import argparse
import json
import math
import os
import sys

from itldata import ITLData
from scobility import Scobility, DEFAULT_API_BASE
from generate_playlist import (
    DEFAULT_SNAPSHOT_DIR,
    DEFAULT_UNLOCK_FOLDERS,
    find_latest_snapshot,
    find_latest_charts,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--spice', choices=['snapshot', 'api'], default='snapshot', help='spice source (default: snapshot)')
    parser.add_argument('--snapshot', help='path to a scobility snapshot JSON (default: newest in $SCOBILITY_SCRATCH)')
    parser.add_argument('--snapshot-dir', default=DEFAULT_SNAPSHOT_DIR, help='where to look for the newest snapshot and charts.json')
    parser.add_argument('--catalog', default='itl2026', help='snapshot/charts catalog prefix (default: itl2026)')
    parser.add_argument('--api-base', default=DEFAULT_API_BASE, help=f'scobility API base URL (default: {DEFAULT_API_BASE})')
    parser.add_argument('--charts', help='path to the scrape charts.json (default: newest <catalog>_data/*/charts.json)')
    parser.add_argument('--unlock-folders', default=DEFAULT_UNLOCK_FOLDERS, help='newline-separated unlock song folders (default: bundled unlock_folders.txt)')
    parser.add_argument('--no-headers', action='store_true', help='omit the per-spice-band divider lines')
    parser.add_argument('-o', '--output', help='output playlist path (default: playlists/ITL - spice order.txt)')
    args = parser.parse_args(argv)

    charts_path = args.charts or find_latest_charts(args.snapshot_dir, args.catalog)
    if not os.path.isfile(args.unlock_folders):
        parser.error(f'unlock folder list not found at {args.unlock_folders} (use --unlock-folders)')

    if args.spice == 'api':
        print(f'Spice:        live API ({args.api_base}/catalog/{args.catalog.upper()}/chart/all)')
        scooby = Scobility.from_api(args.catalog.upper(), args.api_base)
    else:
        snapshot = args.snapshot or find_latest_snapshot(args.snapshot_dir, args.catalog)
        print(f'Spice:        snapshot ({snapshot})')
        scooby = Scobility.from_snapshot(snapshot)

    print(f'Charts:       {charts_path}')
    with open(charts_path, encoding='utf-8') as f:
        charts = json.load(f)
    with open(args.unlock_folders, encoding='utf-8') as f:
        unlock_folders = {line.strip() for line in f if line.strip()}

    data = ITLData(charts, unlock_folders, {})

    # spice values are stored as log2 (the "spice rating" / pepper scale).
    spiced = [(scooby.spice[s.hsh], s) for s in data.hashes.values() if s.hsh in scooby.spice]
    spiced.sort(key=lambda x: x[0])
    skipped = len(data.hashes) - len(spiced)

    lines = []
    band = None
    for spice, song in spiced:
        if not args.no_headers and math.floor(spice) != band:
            band = math.floor(spice)
            lines.append(f'---{band:.0f} spice')
        lines.append(song.path)

    output = args.output or os.path.join(os.path.dirname(__file__), 'playlists', 'ITL - spice order.txt')
    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
    with open(output, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print(f'\n{len(spiced)} charts ordered by spice ({skipped} unspiced skipped)')
    if spiced:
        print(f'spice range: {spiced[0][0]:.3f} -> {spiced[-1][0]:.3f}')
    print(f'Wrote {len(lines)} lines to {output}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
