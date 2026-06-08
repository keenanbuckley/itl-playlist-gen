#!/usr/bin/env python3
"""Generate a spice-ordered playlist of every ITL chart.

Writes one file with a full spice-ordered section followed by bins of 10 charts,
each bin labeled with its min/max spice (charts appear in both).

Player-independent. Spice and catalog come from whichever mode you pick (see
sources.py): auto (default; newer of snapshot/API), snapshot (local), or api
(cached under data/ITL2026). Charts without a spice rating are skipped (and
reported).

    python spice_playlist.py            # auto: newer of snapshot / API
    python spice_playlist.py --mode snapshot
    python spice_playlist.py --mode api
"""

import argparse
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
    parser.add_argument('--bin-size', type=int, default=10, help='charts per bin in the binned sections (default: 10)')
    parser.add_argument('--trap-count', type=int, default=40, help='how many charts in the spice-traps section (default: 40)')
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

    bin_size = max(1, args.bin_size)

    # One file: a full spice-ordered section, then bins of bin_size charts each
    # labeled with that bin's min/max spice.
    lines = ['---All (spice order)']
    lines += [song.path for _, song in spiced]
    for i in range(0, len(spiced), bin_size):
        chunk = spiced[i:i + bin_size]
        lines.append(f'---{chunk[0][0]:.2f} - {chunk[-1][0]:.2f} spice')
        lines += [song.path for _, song in chunk]

    # Spice traps: charts whose spice most exceeds the average for their block.
    by_meter = {}
    for spice, song in spiced:
        by_meter.setdefault(song.rating, []).append(spice)
    meter_avg = {m: sum(v) / len(v) for m, v in by_meter.items()}
    by_divergence = sorted(spiced, key=lambda x: x[0] - meter_avg[x[1].rating])
    n = max(0, args.trap_count)
    lines.append('---Spice traps (hardest for their block)')
    lines += [song.path for _, song in by_divergence[::-1][:n]]
    # The opposite: spice well below the block average -- easier than they look.
    lines.append('---Spice gifts (easiest for their block)')
    lines += [song.path for _, song in by_divergence[:n]]

    # Tech sections: each chart's dominant tech (per-tech levels normalized by
    # their catalog max, since the raw scales differ a lot). Stamina and XMOD
    # (no-CMOD reading) get their own additive sections below instead -- they cut
    # across the others (a stamina or reading chart is usually also footswitch-
    # or crossover-heavy), so a single dominant bucket would hide most of them.
    TECHS = ['crossoverLevel', 'bracketLevel', 'footswitchLevel', 'jackLevel',
             'sideswitchLevel', 'doublestepLevel']
    chart_by_hash = {c['hash']: c for c in charts.values()}
    tech_max = {t: max((chart_by_hash[s.hsh].get(t) or 0 for _, s in spiced), default=0) for t in TECHS}
    tech_groups = {t: [] for t in TECHS}
    for spice, song in spiced:
        c = chart_by_hash.get(song.hsh)
        if not c:
            continue
        scored = [((c.get(t) or 0) / tech_max[t] if tech_max[t] else 0, t) for t in TECHS]
        strength, tech = max(scored)
        if strength > 0:
            tech_groups[tech].append((spice, song))
    for t in TECHS:
        group = sorted(tech_groups[t], key=lambda x: x[0])
        if group:
            lines.append(f'---Tech: {t[:-len("Level")].capitalize()}')
            lines += [song.path for _, song in group]

    # Stamina and XMOD: additive (a chart can also be in a dominant tech section
    # above). Stamina = at least a quarter of the catalog's peak stamina; XMOD =
    # charts played without a CMOD (reading).
    stam_max = max((chart_by_hash.get(s.hsh, {}).get('staminaLevel') or 0 for _, s in spiced), default=0)
    stamina = [(sp, so) for sp, so in spiced
               if (chart_by_hash.get(so.hsh, {}).get('staminaLevel') or 0) >= 0.25 * stam_max]
    if stamina:
        lines.append('---Tech: Stamina')
        lines += [so.path for _, so in sorted(stamina, key=lambda x: x[0])]
    xmod = [(sp, so) for sp, so in spiced if chart_by_hash.get(so.hsh, {}).get('isNoCmod')]
    if xmod:
        lines.append('---Tech: XMOD (no CMOD)')
        lines += [so.path for _, so in sorted(xmod, key=lambda x: x[0])]

    output = args.output or os.path.join(os.path.dirname(__file__), 'playlists', 'ITL - spice order.txt')
    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
    with open(output, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    n_bins = (len(spiced) + bin_size - 1) // bin_size
    print(f'\n{len(spiced)} charts ordered by spice ({skipped} unspiced skipped)')
    if spiced:
        print(f'spice range: {spiced[0][0]:.3f} -> {spiced[-1][0]:.3f}')
    print(f'Wrote {len(lines)} lines to {output} (all songs + {n_bins} bins of {bin_size})')
    return 0


if __name__ == '__main__':
    sys.exit(main())
