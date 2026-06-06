#!/usr/bin/env python3
"""Generate an ITGmania playlist of RP-gaining ITL charts for one player.

Chart spice comes from either a local scobility snapshot or the live scobility
API. The player's scores come from one of: that snapshot (by username), a
player's ITL2026.json game export, or a live scrape of their current GrooveStats
scores (by entrant name). The complete chart catalog (folders, point ceilings,
ratings) comes from the scrape's charts.json, and the authoritative unlock-folder
list from unlock_folders.txt (extracted from the "ITL Online 2026 Unlocks" pack).
Writes a StepMania playlist .txt for the game machine.

    # snapshot spice + snapshot scores (by username)
    python generate_playlist.py "PlayerName"

    # live API spice + a player's export (no snapshot needed)
    python generate_playlist.py --spice api --itl-json "ITL2026 Kiki.json"

    # live API spice + live current GrooveStats scores (by entrant name)
    python generate_playlist.py "HFocus77" --scrape --spice api
"""

import argparse
import glob
import json
import os
import sys

from itldata import ITLData
from scobility import Scobility, DEFAULT_API_BASE
from groovestats import (
    DEFAULT_ITL_BASE,
    find_latest_entrant_info,
    build_entrant_index,
    suggest_names,
    scrape_entrant_scores,
)

DEFAULT_INDEX_CACHE = os.path.join(os.path.dirname(__file__), 'entrant_index.json')


def scores_from_export(export_path):
    """Build hash -> {value, clear, last_played} from an ITL2026.json export."""
    with open(export_path, encoding='utf-8') as f:
        export = json.load(f)
    scores = {}
    for hsh, entry in export['hashMap'].items():
        if entry.get('clearType', 0) > 0:
            scores[hsh] = {
                'value': 1.0 - entry['ex'] / 10000.0,    # ex is EX% x100; value is diff-from-perfect
                'clear': entry['clearType'],
                'last_played': entry.get('date'),
            }
    return scores


def name_from_export(export_path):
    stem = os.path.splitext(os.path.basename(export_path))[0]
    for prefix in ('ITL2026 ', 'ITL2026_', 'ITL2026'):
        if stem.startswith(prefix):
            return stem[len(prefix):].strip() or stem
    return stem


def _percentile(sorted_vals, p):
    import math
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p / 100.0
    f, c = math.floor(k), math.ceil(k)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)


def resolve_min_ex(spec, scores):
    """Turn a --min-ex spec into a numeric EX cutoff and a status message.

    'none'/'off' disables; a number is used directly; 'auto'/'auto:P' takes the
    Pth percentile (default 10) of the player's passing EX -- the score they
    usually at least reach when they pass.
    """
    if spec is None:
        return None, None
    s = str(spec).strip().lower()
    if s in ('none', 'off', ''):
        return None, 'min-EX:       disabled'
    if s.startswith('auto'):
        pctile = float(s.split(':', 1)[1]) if ':' in s else 10.0
        ex_vals = sorted((1.0 - v['value']) * 100 for v in scores.values())
        val = _percentile(ex_vals, pctile)
        if val is None:
            return None, 'min-EX:       auto skipped (no scores)'
        return val, f'min-EX:       {val:.1f}% (auto: p{pctile:g} of {len(ex_vals)} passing scores)'
    return float(s), f'min-EX:       {float(s):.1f}%'


DEFAULT_SNAPSHOT_DIR = os.environ.get(
    'SCOBILITY_SCRATCH', os.path.expanduser('~/scobility/scratch')
)
DEFAULT_UNLOCK_FOLDERS = os.path.join(os.path.dirname(__file__), 'unlock_folders.txt')


def find_latest_snapshot(directory, catalog='itl2026'):
    pattern = os.path.join(directory, f'scobility_{catalog}*.json')
    candidates = sorted(glob.glob(pattern))
    if not candidates:
        raise FileNotFoundError(
            f'no scobility_{catalog}*.json found in {directory} '
            f'(set --snapshot or $SCOBILITY_SCRATCH)'
        )
    # Filenames embed the scrape date, so lexical sort puts the newest last.
    return candidates[-1]


def find_latest_charts(scratch_dir, catalog='itl2026'):
    pattern = os.path.join(scratch_dir, f'{catalog}_data', '*', 'charts.json')
    candidates = sorted(glob.glob(pattern))
    if not candidates:
        raise FileNotFoundError(
            f'no {catalog}_data/*/charts.json found under {scratch_dir} (set --charts)'
        )
    # Parent dirs are dated (YYYYMMDD), so lexical sort puts the newest last.
    return candidates[-1]


def build_playlist_lines(data, min_ex=None):
    def ex_ok(song):
        # Keep only charts the fit predicts the player scores at least min_ex on.
        return min_ex is None or (song.targetEX is not None and song.targetEX >= min_ex)

    allTargets = []
    targetsByRating = {}
    for song in data.songs:
        if song.potentialRP == 0 or not ex_ok(song):
            continue
        allTargets.append(song)
        targetsByRating.setdefault(song.rating, []).append(song)

    # Highest spice in each rating's EX trapezoid; charts at or below it can
    # still move your EP total.
    spiceEpCeilings = {
        rating: max((s.spice for s in songs if s.spice is not None), default=None)
        for rating, songs in data.exTrapezoid.items()
    }

    # The score-points floor: the lowest max-point chart still worth chasing.
    targetPerSong = data.currentSP() / 75 if data.songs else 0
    i = 0
    while i < len(data.songs) and targetPerSong < data.songs[i].points:
        targetPerSong = (data.currentSP() - sum(s.points for s in data.songs[:i + 1])) / max(1, 75 - (i + 1))
        i += 1
    mpFloor = min(
        (x.maxPoints for x in data.top75() if x.points > targetPerSong),
        default=0,
    )

    targetsByDate = {}
    for song in data.songs:
        if not ex_ok(song):
            continue
        ceiling = spiceEpCeilings.get(song.rating)
        ep_relevant = (
            ceiling is not None and song.spice is not None and song.spice <= ceiling
        )
        if (song.maxPoints > mpFloor) or ep_relevant:
            passDate = f'Best score from {song.date[:7]}' if song.date else 'Never passed'
            targetsByDate.setdefault(passDate, []).append(song)

    lines = []

    lines.append("---All +RP")
    for target in sorted(allTargets, reverse=True, key=lambda x: x.potentialRP):
        lines.append(target.path)

    for rating in sorted(targetsByRating.keys()):
        minRP = min(x.potentialRP for x in targetsByRating[rating])
        maxRP = max(x.potentialRP for x in targetsByRating[rating])
        if minRP == maxRP:
            lines.append(f'---[{rating:02}] +{minRP} RP')
        else:
            lines.append(f'---[{rating:02}] +{minRP}-{maxRP} RP')
        lines += [x.path for x in sorted(targetsByRating[rating], reverse=True, key=lambda x: x.potentialRP)]

    for passDate in sorted(targetsByDate.keys()):
        lines.append(f'---{passDate}')
        for target in sorted(targetsByDate[passDate], key=lambda x: x.maxPoints):
            lines.append(target.path)

    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('username', nargs='?', help='player name (snapshot/scrape lookup; defaults to the export filename with --itl-json)')
    parser.add_argument('--spice', choices=['snapshot', 'api'], default='snapshot', help='spice source (default: snapshot)')
    parser.add_argument('--itl-json', help="a player's ITL2026.json export to read scores from (instead of the snapshot)")
    parser.add_argument('--scrape', action='store_true', help="scrape the username's current GrooveStats scores live (resolved via the entrant_info index)")
    parser.add_argument('--entrant-info', help='entrant_info dir for the name->id index (default: newest <catalog>_data/*/entrant_info)')
    parser.add_argument('--itl-base', default=DEFAULT_ITL_BASE, help=f'ITL GrooveStats API base URL (default: {DEFAULT_ITL_BASE})')
    parser.add_argument('--rebuild-index', action='store_true', help='force a rebuild of the cached entrant name->id index')
    parser.add_argument('--snapshot', help='path to a scobility snapshot JSON (default: newest in $SCOBILITY_SCRATCH)')
    parser.add_argument('--snapshot-dir', default=DEFAULT_SNAPSHOT_DIR, help='where to look for the newest snapshot and charts.json')
    parser.add_argument('--catalog', default='itl2026', help='snapshot/charts catalog prefix (default: itl2026)')
    parser.add_argument('--api-base', default=DEFAULT_API_BASE, help=f'scobility API base URL (default: {DEFAULT_API_BASE})')
    parser.add_argument('--charts', help='path to the scrape charts.json (default: newest <catalog>_data/*/charts.json)')
    parser.add_argument('--unlock-folders', default=DEFAULT_UNLOCK_FOLDERS, help='newline-separated unlock song folders (default: bundled unlock_folders.txt)')
    parser.add_argument('--min-ex', default='auto', metavar='EX|auto[:P]',
                        help="only keep charts whose predicted EX is at least this: a number (e.g. 70), "
                             "'auto' for the p10 of your passing scores, 'auto:P' for the Pth percentile, "
                             "or 'none' to disable (default: auto)")
    parser.add_argument('-o', '--output', help='output playlist path (default: playlists/ITL - <username>.txt)')
    args = parser.parse_args(argv)

    if args.itl_json and args.scrape:
        parser.error('--itl-json and --scrape are mutually exclusive score sources')
    if args.spice == 'api' and not (args.itl_json or args.scrape):
        parser.error('--spice api has no score source; add --itl-json or --scrape (the API serves spice, not scores)')
    if not args.itl_json and not args.username:
        parser.error('a username is required for snapshot/scrape scores (or pass --itl-json)')

    charts_path = args.charts or find_latest_charts(args.snapshot_dir, args.catalog)
    if not os.path.isfile(args.unlock_folders):
        parser.error(f'unlock folder list not found at {args.unlock_folders} (use --unlock-folders)')

    print(f'Charts:       {charts_path}')
    print(f'Unlocks:      {args.unlock_folders}')

    with open(charts_path, encoding='utf-8') as f:
        charts = json.load(f)
    with open(args.unlock_folders, encoding='utf-8') as f:
        unlock_folders = {line.strip() for line in f if line.strip()}

    # Every unlock folder should resolve to a real chart. Entries missing from
    # charts.json mean the list and the scrape disagree, so flag it loudly.
    catalog_folders = {c['chartSongDir'] for c in charts.values()}
    sp_folders = {c['chartSongDir'] for c in charts.values() if c.get('playstyle') == 1}
    orphan_unlocks = unlock_folders - catalog_folders
    print(f'Unlock folders: {len(unlock_folders)} '
          f'({len(unlock_folders & sp_folders)} SP charts in catalog)')
    if orphan_unlocks:
        print(f'WARNING: {len(orphan_unlocks)} unlock folders not in charts.json '
              f'(list may be stale vs the scrape): {sorted(orphan_unlocks)[:5]}', file=sys.stderr)

    # Spice source.
    if args.spice == 'api':
        url = f'{args.api_base}/catalog/{args.catalog.upper()}/chart/all'
        print(f'Spice:        live API ({url})')
        scooby = Scobility.from_api(args.catalog.upper(), args.api_base)
    else:
        snapshot = args.snapshot or find_latest_snapshot(args.snapshot_dir, args.catalog)
        print(f'Spice:        snapshot ({snapshot})')
        scooby = Scobility.from_snapshot(snapshot)

    # Score source.
    if args.scrape:
        info_dir = args.entrant_info or find_latest_entrant_info(args.snapshot_dir, args.catalog)
        index = build_entrant_index(info_dir, cache_path=DEFAULT_INDEX_CACHE, rebuild=args.rebuild_index)
        entry = index.get(args.username.lower())
        if entry is None:
            print(f'\nNo GrooveStats entrant named {args.username!r} in {info_dir}.', file=sys.stderr)
            near = suggest_names(args.username, index)
            if near:
                print('Did you mean: ' + ', '.join(near), file=sys.stderr)
            return 1
        entrant_id, player_name = entry
        print(f'Scores:       live scrape of {player_name} (ITL entrant #{entrant_id})')
        scores = scrape_entrant_scores(entrant_id, args.itl_base)
    elif args.itl_json:
        scores = scores_from_export(args.itl_json)
        player_name = args.username or name_from_export(args.itl_json)
        print(f'Scores:       export ({args.itl_json})')
    else:
        try:
            player, scores = scooby.find_player(args.username)
        except KeyError:
            print(f'\nNo player named {args.username!r} in the snapshot. Available names:', file=sys.stderr)
            for name in scooby.player_names():
                print(f'  {name}', file=sys.stderr)
            return 1
        player_name = player['name']
        print(f'Scores:       snapshot player #{player["e_id"]}')

    print(f'Player:       {player_name} - {len(scores)} scored charts')

    data = ITLData(charts, unlock_folders, scores)
    try:
        scooby.processPlayer(player_name, data)
    except ValueError as e:
        print(f'\nCould not compute targets: {e}', file=sys.stderr)
        return 1

    print('\nScobility fit (two-segment horizon):')
    print(f'  timing power:    {data.timingPower:7.3f}')
    print(f'  spice horizon:   {data.horizonSpice:7.3f}  (quality {data.horizonQuality:.3f})')
    print(f'  mild sauce:      {data.mildSlope:7.3f}  (slope below the horizon)')
    print(f'  hot sauce:       {data.hotSlope:7.3f}  (slope above the horizon)')
    print(f'  fit residual:    {data.residual:7.3f}')

    min_ex, min_ex_msg = resolve_min_ex(args.min_ex, scores)
    if min_ex_msg:
        print('\n' + min_ex_msg)

    lines = build_playlist_lines(data, min_ex=min_ex)

    output = args.output or os.path.join(
        os.path.dirname(__file__), 'playlists', f'ITL - {player_name}.txt'
    )
    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
    with open(output, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print(f'\nWrote {len(lines)} lines to {output}')
    print('Copy it to <ITGmania>/Save/<profile>/Playlists/ on the game machine.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
