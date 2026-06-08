#!/usr/bin/env python3
"""Cross-validate whether a tech residual on top of the spice fit predicts EX
better than the spice fit alone, per player.

For each player we k-fold their (spice, EX, hash) points. On each training fold
we fit the adaptive spice curve, then ridge-regress its residual on the chart's
z-scored tech features (the same model tech.py uses for the profile). We predict
held-out EX two ways -- spice fit only vs spice fit + tech residual -- and score
both by mean absolute EX error, overall and on the hardest spice quartile (the
extrapolation targets actually care about).

    python cv_tech.py                  # aggregate over a sample
    python cv_tech.py HFocus77         # one player, detailed
"""

import argparse
import json
import math
import os
import statistics
import sys

import sources
import tech
from scobility import Scobility


def _fold_models(train, feat_by_hash, lam):
    """(spice_fit, ridge_beta) from a training fold."""
    fit = tech.adaptive_fit([(s, q) for s, q, _h in train])
    X, y = [], []
    for s, q, h in train:
        if h in feat_by_hash:
            X.append(feat_by_hash[h])
            y.append(q - fit(s))
    if len(X) < len(feat_by_hash[next(iter(feat_by_hash))]):   # need rows > params
        return fit, None
    beta, _cov = tech._ridge_cov(X, y, lam)
    return fit, beta


def _tech_adj(beta, vec):
    return sum(beta[k] * vec[k] for k in range(len(beta)))


def cv_player(data, feat_by_hash, folds, lam):
    """data: [(spice, exfrac, hash)] -> {'base':(overall,hard), 'tech':(overall,hard)}."""
    pts = [(s, tech.quality(s, e), h, e) for s, e, h in data]
    thr = statistics.quantiles([s for s, *_ in pts], n=4)[2]
    acc = {'base': ([], []), 'tech': ([], [])}
    for f in range(folds):
        train = [(s, q, h) for i, (s, q, h, _e) in enumerate(pts) if i % folds != f]
        test = [(s, q, h, e) for i, (s, q, h, e) in enumerate(pts) if i % folds == f]
        if len(train) < 5 or not test:
            continue
        fit, beta = _fold_models(train, feat_by_hash, lam)
        for s, _q, h, e in test:
            base = fit(s)
            err_base = abs(tech.pred_ex(base, s) - e * 100)
            if beta is not None and h in feat_by_hash:
                err_tech = abs(tech.pred_ex(base + _tech_adj(beta, feat_by_hash[h]), s) - e * 100)
            else:
                err_tech = err_base
            for key, err in (('base', err_base), ('tech', err_tech)):
                acc[key][0].append(err)
                if s >= thr:
                    acc[key][1].append(err)
    return {k: (statistics.fmean(o) if o else float('nan'),
                statistics.fmean(hh) if hh else float('nan'))
            for k, (o, hh) in acc.items()}


def player_points(snapshot, scooby):
    songs = {s['s_id']: s for s in snapshot['songs']}
    pts = {}
    for v in snapshot['scores']:
        so = songs.get(v['s_id'])
        if not so or so.get('style') != 'Single' or so['hash'] not in scooby.spice:
            continue
        pts.setdefault(v['e_id'], []).append((scooby.spice[so['hash']], 1 - v['value'], so['hash']))
    return pts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('players', nargs='*', help='player names to detail (default: aggregate)')
    parser.add_argument('--snapshot', help='snapshot JSON (default: newest)')
    parser.add_argument('--snapshot-dir', default=sources.DEFAULT_SNAPSHOT_DIR)
    parser.add_argument('--charts', help='charts.json (default: bundled or newest scrape)')
    parser.add_argument('--catalog', default='itl2026')
    parser.add_argument('--folds', type=int, default=5)
    parser.add_argument('--lam', type=float, default=tech.DEFAULT_LAMBDA, help='ridge lambda (default: %(default)s)')
    parser.add_argument('--min-charts', type=int, default=20, help='skip players with fewer charts (default: 20)')
    parser.add_argument('--limit', type=int, default=300)
    args = parser.parse_args(argv)

    snap_path = args.snapshot or sources.find_latest_snapshot(args.snapshot_dir, args.catalog)
    print(f'Snapshot: {snap_path}')
    scooby = Scobility.from_snapshot(snap_path)
    pts = player_points(scooby.snapshot, scooby)

    charts_path = (args.charts
                   or (sources.CHARTS_CACHE if os.path.isfile(sources.CHARTS_CACHE) else None)
                   or sources.find_latest_scratch_charts(args.snapshot_dir, args.catalog))
    with open(charts_path, encoding='utf-8') as f:
        charts = json.load(f)
    mean, std, _hi = tech.feature_stats(charts)
    feat_by_hash = tech.feature_vectors(charts, mean, std)
    print(f'Charts: {charts_path} | lam={args.lam} folds={args.folds}\n')

    name_by_eid = {p['e_id']: p['name'] for p in scooby.snapshot['players']}
    eid_by_name = {p['name'].lower(): p['e_id'] for p in scooby.snapshot['players']}

    if args.players:
        targets = []
        for nm in args.players:
            eid = eid_by_name.get(nm.lower())
            if eid is None or len(pts.get(eid, [])) < args.min_charts:
                print(f'(skip {nm}: not found or too few charts)', file=sys.stderr)
            else:
                targets.append(eid)
    else:
        targets = [e for e, d in sorted(pts.items()) if len(d) >= args.min_charts][:args.limit]

    rows = []
    for eid in targets:
        rows.append((eid, len(pts[eid]), cv_player(pts[eid], feat_by_hash, args.folds, args.lam)))

    if args.players:
        for eid, n, res in rows:
            print(f'{name_by_eid[eid]} (n={n}):   overall / hardest-quartile')
            for k in ('base', 'tech'):
                o, h = res[k]
                print(f'    {k:6s}: {o:5.2f}  /  {h:5.2f}')
            bo = res['base'][0] - res['tech'][0]
            bh = res['base'][1] - res['tech'][1]
            print(f'    delta : {bo:+5.2f}  /  {bh:+5.2f}  (positive = tech helps)\n')

    def agg(idx, k):
        vals = [r[2][k][idx] for r in rows if not math.isnan(r[2][k][idx])]
        return statistics.fmean(vals) if vals else float('nan')

    print(f'=== Aggregate over {len(rows)} players (mean per-player CV EX error) ===')
    print(f'{"":8s}{"overall":>10s}{"hardest-Q":>12s}')
    for k in ('base', 'tech'):
        print(f'{k:8s}{agg(0, k):>10.2f}{agg(1, k):>12.2f}')
    print(f'{"delta":8s}{agg(0, "base") - agg(0, "tech"):>+10.2f}{agg(1, "base") - agg(1, "tech"):>+12.2f}'
          f'   (positive = tech helps)')

    buckets = [(args.min_charts, 40), (40, 100), (100, 200), (200, 10 ** 9)]
    print('\n=== By chart count (overall EX err: base -> tech) ===')
    print('band'.ljust(11) + 'players'.rjust(8) + 'base'.rjust(9) + 'tech'.rjust(9) + 'delta'.rjust(9))
    for lo, hi in buckets:
        sub = [r for r in rows if lo <= r[1] < hi]
        if not sub:
            continue
        b = statistics.fmean(r[2]['base'][0] for r in sub)
        t = statistics.fmean(r[2]['tech'][0] for r in sub)
        band = f'[{lo},{"inf" if hi >= 10 ** 9 else hi})'
        print(band.ljust(11) + str(len(sub)).rjust(8) + f'{b:9.2f}{t:9.2f}{b - t:+9.2f}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
