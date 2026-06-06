#!/usr/bin/env python3
"""Generate an ITGmania playlist of RP-gaining ITL charts for one player.

Two modes (see sources.py):
  snapshot  spice + scores from a local scobility snapshot; catalog from the
            scrape's local charts.json. Nothing is cached.
  api       spice / catalog / unlock list / entrant index from the APIs, cached
            under data/ITL2026 (--refresh re-fetches); scores from a live
            GrooveStats scrape by entrant name.

    # snapshot mode (default): scores from the snapshot by player name
    python generate_playlist.py "PlayerName"

    # api mode: live spice + live GrooveStats scores by entrant name
    python generate_playlist.py "HFocus77" --mode api

    # either mode, scores from an ITL2026.json export instead
    python generate_playlist.py --mode api --itl-json "ITL2026 Kiki.json"
"""

import argparse
import math
import os
import sys

import sources
from itldata import ITLData
from scobility import DEFAULT_API_BASE
from groovestats import DEFAULT_ITL_BASE, suggest_names, scrape_entrant_scores
from sources import scores_from_export, name_from_export


def _percentile(sorted_vals, p):
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


def resolve_scores(args, mode, scooby):
    """Return (scores, player_name) or (None, None) on a handled lookup failure."""
    if args.itl_json:
        print(f'Scores:       export ({args.itl_json})')
        return scores_from_export(args.itl_json), (args.username or name_from_export(args.itl_json))

    if mode == 'api':
        index = sources.entrant_index_api(args.catalog, args.api_base, refresh=args.refresh)
        entry = index.get(args.username.lower())
        if entry is None:
            print(f'\nNo GrooveStats entrant named {args.username!r}.', file=sys.stderr)
            near = suggest_names(args.username, index)
            if near:
                print('Did you mean: ' + ', '.join(near), file=sys.stderr)
            return None, None
        entrant_id, player_name = entry
        print(f'Scores:       live scrape of {player_name} (ITL entrant #{entrant_id})')
        return scrape_entrant_scores(entrant_id, args.itl_base), player_name

    # snapshot mode
    try:
        player, scores = scooby.find_player(args.username)
    except KeyError:
        print(f'\nNo player named {args.username!r} in the snapshot. Available names:', file=sys.stderr)
        for name in scooby.player_names():
            print(f'  {name}', file=sys.stderr)
        return None, None
    print(f'Scores:       snapshot player #{player["e_id"]}')
    return scores, player['name']


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('username', nargs='?', help='player name (snapshot player / GrooveStats entrant; defaults to the export filename with --itl-json)')
    parser.add_argument('--mode', choices=['auto', 'snapshot', 'api'], default='auto', help='source: snapshot, api, or auto = whichever has newer scobility (default: auto)')
    parser.add_argument('--itl-json', help="read scores from an ITL2026.json export instead of the mode's score source")
    parser.add_argument('--refresh', action='store_true', help='api mode: re-fetch the cached spice/catalog/index')
    parser.add_argument('--snapshot', help='snapshot mode: path to a snapshot JSON (default: newest in $SCOBILITY_SCRATCH)')
    parser.add_argument('--snapshot-dir', default=sources.DEFAULT_SNAPSHOT_DIR, help='snapshot mode: where to look for the snapshot and charts.json')
    parser.add_argument('--charts', help='snapshot mode: explicit charts.json path')
    parser.add_argument('--catalog', default='itl2026', help='catalog prefix (default: itl2026)')
    parser.add_argument('--api-base', default=DEFAULT_API_BASE, help=f'scobility API base URL (default: {DEFAULT_API_BASE})')
    parser.add_argument('--itl-base', default=DEFAULT_ITL_BASE, help=f'ITL GrooveStats API base URL (default: {DEFAULT_ITL_BASE})')
    parser.add_argument('--min-ex', default='auto', metavar='EX|auto[:P]',
                        help="only keep charts whose predicted EX is at least this: a number (e.g. 70), "
                             "'auto' for the p10 of your passing scores, 'auto:P' for the Pth percentile, "
                             "or 'none' to disable (default: auto)")
    parser.add_argument('-o', '--output', help='output playlist path (default: playlists/ITL - <username>.txt)')
    args = parser.parse_args(argv)

    if not args.itl_json and not args.username:
        parser.error('a username is required (or pass --itl-json)')

    try:
        scooby, charts, unlock_folders, _snapshot, mode, src_lines = sources.resolve_catalog(
            args.mode, snapshot_dir=args.snapshot_dir, catalog=args.catalog,
            api_base=args.api_base, itl_base=args.itl_base,
            snapshot_path=args.snapshot, charts_path=args.charts, refresh=args.refresh,
        )
    except FileNotFoundError as e:
        parser.error(str(e))
    for line in src_lines:
        print(line)

    catalog_folders = {c['chartSongDir'] for c in charts.values()}
    orphan = unlock_folders - catalog_folders
    if orphan:
        print(f'WARNING: {len(orphan)} unlock folders not in charts.json: {sorted(orphan)[:5]}', file=sys.stderr)

    scores, player_name = resolve_scores(args, mode, scooby)
    if scores is None:
        return 1

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
