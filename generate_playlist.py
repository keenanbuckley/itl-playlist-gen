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
import tech
from itldata import ITLData
from scobility import DEFAULT_API_BASE
from groovestats import DEFAULT_ITL_BASE


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


def build_playlist_lines(data, min_ex=None, include_practice=False, practice_passes=3,
                         practice_ex=85.0, tech_sections=None):
    def ex_ok(song):
        # Charts you've already passed always stay; min_ex only gates new charts
        # the fit predicts you'd score below the cutoff.
        if min_ex is None or song.played:
            return True
        return song.targetEX is not None and song.targetEX >= min_ex

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

    ex_note = '' if min_ex is None else f' (predicted EX >= {min_ex:.1f}%)'
    lines = []

    lines.append(f"---All +RP{ex_note}")
    for target in sorted(allTargets, reverse=True, key=lambda x: x.potentialRP):
        lines.append(target.path)

    passed = [s for s in allTargets if s.played]
    if passed:
        lines.append("---Passed +RP")
        for target in sorted(passed, reverse=True, key=lambda x: x.potentialRP):
            lines.append(target.path)

    # Passed charts with room to gain, split by points pool.
    passed_sp = sorted((s for s in data.hashes.values() if s.played and s.potentialSP > 0),
                       reverse=True, key=lambda s: s.potentialSP)
    if passed_sp:
        lines.append("---Passed +SP")
        lines += [s.path for s in passed_sp]
    passed_ep = sorted((s for s in data.hashes.values() if s.played and s.potentialEP > 0),
                       reverse=True, key=lambda s: s.potentialEP)
    if passed_ep:
        lines.append("---Passed +EP")
        lines += [s.path for s in passed_ep]

    # Most RP per unit of (linear) difficulty -- the low-hanging fruit.
    efficient = sorted((s for s in allTargets if s.spice is not None),
                       reverse=True, key=lambda s: s.potentialRP / (2 ** s.spice))
    if efficient:
        lines.append("---Efficient RP (most gain per spice)")
        lines += [s.path for s in efficient]

    # Played charts you scored below what your fit predicts: your weak spots.
    under = sorted(
        (s for s in data.hashes.values()
         if s.played and s.quality is not None and s.qualityFit is not None and s.quality < s.qualityFit),
        reverse=True, key=lambda s: s.qualityFit - s.quality,
    )
    if under:
        lines.append("---Underperformed (vs your fit)")
        lines += [s.path for s in under]

    # Charts at and just above your skill horizon -- level-up targets.
    hz = getattr(data, 'horizonSpice', None)
    if hz is not None:
        lo, hi = hz, hz + 0.75
        ceiling_charts = sorted(
            (s for s in data.hashes.values() if s.spice is not None and lo <= s.spice <= hi),
            key=lambda s: s.spice,
        )
        if ceiling_charts:
            lines.append(f'---At your ceiling ({lo:.2f}-{hi:.2f} spice)')
            lines += [s.path for s in ceiling_charts]

    for rating in sorted(targetsByRating.keys()):
        minRP = min(x.potentialRP for x in targetsByRating[rating])
        maxRP = max(x.potentialRP for x in targetsByRating[rating])
        if minRP == maxRP:
            lines.append(f'---[{rating:02}] +{minRP} RP')
        else:
            lines.append(f'---[{rating:02}] +{minRP}-{maxRP} RP')
        lines += [x.path for x in sorted(targetsByRating[rating], reverse=True, key=lambda x: x.potentialRP)]

    for passDate in sorted(targetsByDate.keys()):
        if passDate == 'Never passed':
            lines.append(f'---{passDate}{ex_note}')
            section = sorted(targetsByDate[passDate], key=lambda x: (x.spice is None, x.spice))
        else:
            lines.append(f'---{passDate}')
            section = sorted(targetsByDate[passDate], key=lambda x: x.maxPoints)
        for target in section:
            lines.append(target.path)

    # Charts not yet mastered: passed fewer than practice_passes times AND below
    # practice_ex. Needs a pass count, so it's only emitted for snapshot/scrape.
    if include_practice:
        suffix = f'(<{practice_passes} passes, <{practice_ex:.0f}% EX)'
        unmastered = sorted(
            (s for s in data.hashes.values() if (s.plays or 0) < practice_passes and s.ex < practice_ex),
            key=lambda s: (s.spice is None, s.spice),
        )
        lines.append(f'---Unmastered {suffix}')
        lines += [s.path for s in unmastered]

        # Same list, restricted to unlock-pack charts. (Charts *required* for
        # unlocks aren't in the API, so they can't be added.)
        lines.append(f'---Unmastered unlocks {suffix}')
        lines += [s.path for s in unmastered if s.is_unlock]

    # Per-tech sections (weakest tech first): charts whose dominant reliable tech
    # is this one, with RP to gain and predicted EX past the floor. Header shows
    # your grade/percentile for that tech.
    if tech_sections:
        for sec in tech_sections:
            songs = sorted(
                (data.hashes[h] for h in sec['hashes']
                 if h in data.hashes and data.hashes[h].potentialRP > 0 and ex_ok(data.hashes[h])),
                key=lambda s: (s.spice is None, s.spice),
            )
            if not songs:
                continue
            pct = sec['percentile']
            tag = f" (you: {sec['grade']}, p{pct:.0f})" if pct is not None else ''
            lines.append(f"---Tech: {sec['label']}{tag}{ex_note}")
            lines += [s.path for s in songs]

    return lines


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
    parser.add_argument('--fit', choices=['adaptive', 'horizon'], default='adaptive', help='spice fit: adaptive (horizon when data-rich, flat-shrunk when sparse) or horizon (official scobility) (default: adaptive)')
    parser.add_argument('--adaptive-n', type=int, default=40, help='adaptive fit: use horizon at/above this many played charts, else flat-shrunk linear (default: 40)')
    parser.add_argument('--spice-iqr', type=float, metavar='K', help='reject spice outliers from the horizon fit beyond Q1-K*IQR / Q3+K*IQR of your passed charts (e.g. 4.0; off by default)')
    parser.add_argument('--tech-target', action='store_true', help='nudge target EX by your per-tech strengths/weaknesses (ridge on tech features beyond spice; off by default)')
    parser.add_argument('--tech-cap', type=float, default=5.0, metavar='EX', help='--tech-target: max EX a chart\'s target can move from the spice-only value (default: 5)')
    parser.add_argument('--tech-sections', action='store_true', help='add a playlist section per reliable tech (charts heavy in it, weakest tech first, header shows your grade)')
    parser.add_argument('--practice-passes', type=int, default=3, help='"Unmastered" section: passes at which a chart counts as mastered (default: 3)')
    parser.add_argument('--practice-ex', type=float, default=85.0, help='"Unmastered" section: Ex%% at which a chart counts as mastered (default: 85)')
    parser.add_argument('-o', '--output', help='output playlist path (default: playlists/ITL - <username>.txt)')
    args = parser.parse_args(argv)

    if not args.itl_json and not args.username:
        parser.error('a username is required (or pass --itl-json)')

    try:
        scooby, charts, unlock_folders, snapshot, _mode, src_lines = sources.resolve_catalog(
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

    scores, player_name = sources.resolve_scores(args, scooby)
    if scores is None:
        return 1

    print(f'Player:       {player_name} - {len(scores)} scored charts')

    data = ITLData(charts, unlock_folders, scores)
    try:
        scooby.processPlayer(player_name, data, spice_iqr_mult=args.spice_iqr,
                             fit=args.fit, adaptive_n=args.adaptive_n)
    except ValueError as e:
        print(f'\nCould not compute targets: {e}', file=sys.stderr)
        return 1

    if data.rejected_outliers:
        names = ', '.join(s.path.split('\\')[-1] for s in data.rejected_outliers[:5])
        more = '' if len(data.rejected_outliers) <= 5 else f' (+{len(data.rejected_outliers) - 5} more)'
        print(f'Spice outliers rejected from fit (IQR x{args.spice_iqr:g}): {len(data.rejected_outliers)} - {names}{more}')

    print(f'\nScobility fit ({data.fit_used}):')
    print(f'  timing power:    {data.timingPower:7.3f}')
    if data.fit_used == 'horizon':
        print(f'  spice horizon:   {data.horizonSpice:7.3f}  (quality {data.horizonQuality:.3f})')
        print(f'  mild sauce:      {data.mildSlope:7.3f}  (slope below the horizon)')
        print(f'  hot sauce:       {data.hotSlope:7.3f}  (slope above the horizon)')
    else:
        print(f'  slope:           {data.mildSlope:7.3f}  (quality per spice, shrunk toward flat)')
    print(f'  fit residual:    {data.residual:7.3f}')

    if args.tech_target:
        overlay = tech.build_target_overlay(data, charts)
        if overlay is None:
            print('  tech target:     skipped (too few played charts to fit)')
        else:
            scooby.recompute_targets(data, overlay=overlay, ex_cap=args.tech_cap)
            print(f'  tech target:     on (per-chart EX capped at +-{args.tech_cap:g})')

    tech_sections = None
    if args.tech_sections:
        mean, std, _hi = tech.feature_stats(charts)
        feat_by_hash = tech.feature_vectors(charts, mean, std)
        population = tech.population_for(snapshot, feat_by_hash)
        tech_sections = tech.section_data(data, charts, feat_by_hash, population)
        if tech_sections is None:
            print('  tech sections:   skipped (too few scored charts to fit a profile)')
        else:
            graded = sum(1 for s in tech_sections if s['percentile'] is not None)
            print(f'  tech sections:   on ({graded}/{len(tech_sections)} graded'
                  f'{"" if population else "; no cohort, ungraded"})')

    min_ex, min_ex_msg = resolve_min_ex(args.min_ex, scores)
    if min_ex_msg:
        print('\n' + min_ex_msg)

    lines = build_playlist_lines(
        data, min_ex=min_ex,
        include_practice=(args.itl_json is None),    # export has no pass count
        practice_passes=args.practice_passes, practice_ex=args.practice_ex,
        tech_sections=tech_sections,
    )

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
