#!/usr/bin/env python3
"""Generate a playlist of every ITL chart ordered by ascending spice.

Player-independent. Spice and catalog come from whichever mode you pick (see
sources.py): auto (default; newer of snapshot/API), snapshot (local), or api
(cached under data/ITL2026). Charts without a spice rating are skipped (and
reported).

    python spice_playlist.py            # auto: newer of snapshot / API
    python spice_playlist.py --mode snapshot
    python spice_playlist.py --mode api
"""

import argparse
import math
import os
import sys

import sources
from itldata import ITLData
from scobility import DEFAULT_API_BASE
from groovestats import DEFAULT_ITL_BASE


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--mode', choices=['auto', 'snapshot', 'api'], default='auto', help='source: snapshot, api, or auto = whichever has newer scobility (default: auto)')
    parser.add_argument('--refresh', action='store_true', help='api mode: re-fetch the cached spice/catalog')
    parser.add_argument('--snapshot', help='snapshot mode: path to a snapshot JSON (default: newest in $SCOBILITY_SCRATCH)')
    parser.add_argument('--snapshot-dir', default=sources.DEFAULT_SNAPSHOT_DIR, help='snapshot mode: where to look for the snapshot and charts.json')
    parser.add_argument('--charts', help='snapshot mode: explicit charts.json path')
    parser.add_argument('--catalog', default='itl2026', help='catalog prefix (default: itl2026)')
    parser.add_argument('--api-base', default=DEFAULT_API_BASE, help=f'scobility API base URL (default: {DEFAULT_API_BASE})')
    parser.add_argument('--itl-base', default=DEFAULT_ITL_BASE, help=f'ITL GrooveStats API base URL (default: {DEFAULT_ITL_BASE})')
    parser.add_argument('--no-headers', action='store_true', help='omit the per-spice-band divider lines')
    parser.add_argument('-o', '--output', help='output playlist path (default: playlists/ITL - spice order.txt)')
    args = parser.parse_args(argv)

    try:
        scooby, charts, unlock_folders, _snapshot, _mode, src_lines = sources.resolve_catalog(
            args.mode, snapshot_dir=args.snapshot_dir, catalog=args.catalog,
            api_base=args.api_base, itl_base=args.itl_base,
            snapshot_path=args.snapshot, charts_path=args.charts, refresh=args.refresh,
        )
    except FileNotFoundError as e:
        parser.error(str(e))
    for line in src_lines:
        print(line)

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
