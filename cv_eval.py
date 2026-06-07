#!/usr/bin/env python3
"""Cross-validate spice -> EX prediction models, per player.

Loads a scobility snapshot, builds each player's (spice, EX) points, and runs
k-fold CV scoring each model by mean absolute EX error -- overall, on the
hardest spice quartile (closest to the extrapolation we actually care about),
and aggregated separately for sparse players (few charts), where a population
prior should help most.

Models:
  constant       a flat offset (your average score quality)
  linear         OLS line in quality-vs-spice
  horizon        the production two-segment "horizon" fit
  horizon+iqrK   horizon after rejecting spice outliers beyond Q1/Q3 +- K*IQR
  linear+shrink  linear, slope shrunk toward a prior (flat by default; --shrink-
                 toward pop uses the population slope) with pseudo-count K
  adaptive       horizon when the player has >= --adaptive-n charts, else
                 shrink-to-flat
  tiered         flat (< --flat-n) -> linear (< --linear-n) -> horizon
  select         per player, inner-CV the candidates and keep the most
                 predictive one (nested CV, so the score stays honest)

    python cv_eval.py                       # aggregate over a sample of players
    python cv_eval.py HFocus77 madewithlinux BOT   # specific players, with detail
"""

import argparse
import math
import statistics
import sys

import sources
from scobility import Scobility, spiceHorizonFit, dumbassLSQFree

PO = 1.003


def quality(x, exfrac):
    return x - math.log2(PO - exfrac)


def pred_ex(qf, x):
    return min(100.0, max(0.0, 100.0 * (PO - 2 ** (x - qf))))


def fit_constant(train, prior, args):
    m = statistics.fmean(quality(x, e) for x, e in train)
    return lambda x: m


def fit_linear(train, prior, args):
    c0, c1, _ = dumbassLSQFree([x for x, _ in train], [quality(x, e) for x, e in train])
    return lambda x: c0 + c1 * x


def fit_horizon(train, prior, args):
    tr = sorted(train)
    f = spiceHorizonFit([x for x, _ in tr], [quality(x, e) for x, e in tr])
    if not f:
        return fit_linear(train, prior, args)
    hs, hq, mi, ho = f['horizonSpice'], f['horizonQuality'], f['mildSlope'], f['hotSlope']
    return lambda x: (mi if x <= hs else ho) * (x - hs) + hq


def fit_horizon_iqr(train, prior, args):
    xs = sorted(x for x, _ in train)
    q1, _q2, q3 = statistics.quantiles(xs, n=4, method='exclusive')
    iqr = q3 - q1
    lo, hi = q1 - args.iqr_k * iqr, q3 + args.iqr_k * iqr
    kept = [(x, e) for x, e in train if lo <= x <= hi]
    return fit_horizon(kept if len(kept) >= 5 else train, prior, args)


def fit_linear_shrink(train, prior, args):
    n = len(train)
    _c0, c1, _ = dumbassLSQFree([x for x, _ in train], [quality(x, e) for x, e in train])
    b = (n * c1 + args.shrink_k * prior['slope']) / (n + args.shrink_k)
    a = statistics.fmean(quality(x, e) - b * x for x, e in train)   # re-anchor skill to the player
    return lambda x: a + b * x


def fit_adaptive(train, prior, args):
    # Horizon when there's enough data to trust its shape; shrink-to-flat when sparse.
    # Tier on the player's full chart count, not the fold's training size.
    if args.full_n >= args.adaptive_n:
        return fit_horizon(train, prior, args)
    return fit_linear_shrink(train, prior, args)


def fit_tiered(train, prior, args):
    # flat -> linear -> horizon as the player's chart count grows.
    if args.full_n < args.flat_n:
        return fit_constant(train, prior, args)
    if args.full_n < args.linear_n:
        return fit_linear(train, prior, args)
    return fit_horizon(train, prior, args)


SELECT_CANDIDATES = [('constant', fit_constant), ('linear', fit_linear), ('horizon', fit_horizon)]


def _inner_score(train, fit, prior, args):
    """Mean EX error of `fit` under an inner k-fold CV on `train`."""
    errs = []
    folds = args.inner_folds
    for f in range(folds):
        tr = [d for i, d in enumerate(train) if i % folds != f]
        te = [d for i, d in enumerate(train) if i % folds == f]
        if len(tr) < 5 or not te:
            continue
        predict = fit(tr, prior, args)
        errs += [abs(pred_ex(predict(x), x) - e * 100) for x, e in te]
    return statistics.fmean(errs) if errs else float('inf')


def fit_select(train, prior, args):
    # Per player: cross-validate each candidate on the training data and keep the
    # most predictive, then refit it on the full training set.
    _name, fit = min(SELECT_CANDIDATES, key=lambda nf: _inner_score(train, nf[1], prior, args))
    return fit(train, prior, args)


# --- Huber (robust) variants via iteratively reweighted least squares ---------
# Weighted least-squares ports of scobility's fits; with all weights = 1 they
# reduce exactly to the production fits, so IRLS just layers robustness on top.

def _wcomp(a, b, w):
    Sw = Sx = Sxx = Sy = Sxy = 0.0
    for i, x in enumerate(a):
        wi, y = w[i], b[i]
        Sw += wi
        Sx += wi * x
        Sxx += wi * x * x
        Sy += wi * y
        Sxy += wi * x * y
    return Sw, Sx, Sxx, Sy, Sxy


def _wfree(a, b, w):
    Sw, Sx, Sxx, Sy, Sxy = _wcomp(a, b, w)
    det = Sw * Sxx - Sx * Sx
    if det == 0:
        return (Sy / Sw if Sw else 0.0), 0.0
    return (Sxx * Sy - Sx * Sxy) / det, (-Sx * Sy + Sw * Sxy) / det   # c0, c1


def _wresid(a, b, w, c0l, c1l, c0r, c1r, anchor):
    r = 0.0
    for i, x in enumerate(a):
        c0, c1 = (c0l, c1l) if i < anchor else (c0r, c1r)
        r += w[i] * (b[i] - (c1 * x + c0)) ** 2
    return r


def _whorizon(a, b, w):
    """Weighted port of scobility.spiceHorizonFit -> dict or None."""
    n = len(a)
    hc = math.floor(math.sqrt(n))
    best, best_res = None, None
    for j in range(hc, n - hc + 1):
        if j < 2 or n - j < 2 or j + 1 >= n:
            continue
        c0l, c1l = _wfree(a[:j], b[:j], w[:j])
        c0r, c1r = _wfree(a[j:], b[j:], w[j:])
        bf, res = None, None
        if c0l != c0r:
            hs = (c1r - c1l) / (c0l - c0r)
            if a[j] <= hs <= a[j + 1]:
                bf = {'horizonSpice': hs, 'horizonQuality': c1l * hs + c0l,
                      'mildSlope': c1l, 'hotSlope': c1r}
                res = _wresid(a, b, w, c0l, c1l, c0r, c1r, j)
        if bf is None:        # anchored fallback at the nearer node
            k = a[j]
            ao = [x - k for x in a]
            c0a, c1la, c1ra = _wanchored(ao, b, w, j)
            if c0a is None:
                continue
            bf = {'horizonSpice': k, 'horizonQuality': c0a, 'mildSlope': c1la, 'hotSlope': c1ra}
            res = _wresid(ao, b, w, c0a, c1la, c0a, c1ra, j)
        if best is None or res < best_res:
            best, best_res = bf, res
    return best


def _wanchored(ao, b, w, anchor):
    Swl, sl, s2l, _ql, sql = _wcomp(ao[:anchor], b[:anchor], w[:anchor])
    Swr, sr, s2r, _qr, sqr = _wcomp(ao[anchor:], b[anchor:], w[anchor:])
    W = Swl + Swr
    Q = sum(w[i] * b[i] for i in range(len(b)))
    det = W * s2r * s2l - sr * sr * s2l - sl * sl * s2r
    if det == 0:
        return None, None, None
    m13, m23, m33 = -sl * s2r, -s2l * sr, s2l * s2r
    m11, m12, m22 = W * s2r - sr * sr, sl * sr, W * s2l - sl * sl
    c1l = (m11 * sql + m12 * sqr + m13 * Q) / det
    c1r = (m12 * sql + m22 * sqr + m23 * Q) / det
    c0 = (m13 * sql + m23 * sqr + m33 * Q) / det
    return c0, c1l, c1r


def _huber_irls(a, b, fit_w, iters=4):
    w = [1.0] * len(a)
    pred = None
    for _ in range(iters):
        pred = fit_w(a, b, w)
        if pred is None:
            return None
        resid = [b[i] - pred(a[i]) for i in range(len(a))]
        med = statistics.median(resid)
        mad = statistics.median(abs(r - med) for r in resid)
        delta = 1.345 * (mad / 0.6745) if mad > 0 else None
        if delta is None:
            break
        w = [1.0 if abs(r) <= delta else delta / abs(r) for r in resid]
    return pred


def _fitw_linear(a, b, w):
    c0, c1 = _wfree(a, b, w)
    return lambda x: c0 + c1 * x


def _fitw_horizon(a, b, w):
    f = _whorizon(a, b, w)
    if f is None:
        return _fitw_linear(a, b, w)
    hs, hq, mi, ho = f['horizonSpice'], f['horizonQuality'], f['mildSlope'], f['hotSlope']
    return lambda x: (mi if x <= hs else ho) * (x - hs) + hq


def fit_linear_huber(train, prior, args):
    a = [x for x, _ in train]
    b = [quality(x, e) for x, e in train]
    return _huber_irls(a, b, _fitw_linear) or fit_linear(train, prior, args)


def fit_horizon_huber(train, prior, args):
    tr = sorted(train)
    a = [x for x, _ in tr]
    b = [quality(x, e) for x, e in tr]
    return _huber_irls(a, b, _fitw_horizon) or fit_horizon(train, prior, args)


def fit_adaptive_huber(train, prior, args):
    if args.full_n >= args.adaptive_n:
        return fit_horizon_huber(train, prior, args)
    a = [x for x, _ in train]
    b = [quality(x, e) for x, e in train]
    pred = _huber_irls(a, b, _fitw_linear)
    if pred is None:
        return fit_linear_shrink(train, prior, args)
    n = len(train)
    b_hat = pred(1.0) - pred(0.0)
    slope = n * b_hat / (n + args.shrink_k)               # shrink toward flat
    intercept = statistics.fmean(quality(x, e) - slope * x for x, e in train)
    return lambda x: intercept + slope * x


MODELS = [
    ('constant', fit_constant),
    ('linear', fit_linear),
    ('horizon', fit_horizon),
    ('horizon+iqr', fit_horizon_iqr),
    ('linear+shrink', fit_linear_shrink),
    ('adaptive', fit_adaptive),
    ('tiered', fit_tiered),
    ('select', fit_select),
    ('linear_huber', fit_linear_huber),
    ('horizon_huber', fit_horizon_huber),
    ('adaptive_huber', fit_adaptive_huber),
]


def player_points(snapshot, scooby):
    songs = {s['s_id']: s for s in snapshot['songs']}
    pts = {}
    for v in snapshot['scores']:
        so = songs.get(v['s_id'])
        if not so or so.get('style') != 'Single' or so['hash'] not in scooby.spice:
            continue
        pts.setdefault(v['e_id'], []).append((scooby.spice[so['hash']], 1 - v['value']))
    return pts


def population_slope(all_points, min_charts=30):
    slopes = []
    for data in all_points.values():
        if len(data) >= min_charts:
            _c0, c1, _ = dumbassLSQFree([x for x, _ in data], [quality(x, e) for x, e in data])
            slopes.append(c1)
    return statistics.median(slopes) if slopes else 0.0


def cv_player(data, folds, prior, args):
    """Return {model: (mean_err, hard_quartile_err)} via k-fold CV."""
    args.full_n = len(data)   # count-based models tier on the player's real total
    thr = statistics.quantiles([x for x, _ in data], n=4)[2]
    acc = {name: ([], []) for name, _ in MODELS}
    for f in range(folds):
        train = [d for i, d in enumerate(data) if i % folds != f]
        test = [d for i, d in enumerate(data) if i % folds == f]
        if len(train) < 5 or not test:
            continue
        for name, fit in MODELS:
            predict = fit(train, prior, args)
            for x, e in test:
                err = abs(pred_ex(predict(x), x) - e * 100)
                acc[name][0].append(err)
                if x >= thr:
                    acc[name][1].append(err)
    return {name: (statistics.fmean(o) if o else float('nan'),
                   statistics.fmean(h) if h else float('nan'))
            for name, (o, h) in acc.items()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('players', nargs='*', help='player names to detail (default: aggregate over a sample)')
    parser.add_argument('--snapshot', help='snapshot JSON (default: newest in $SCOBILITY_SCRATCH)')
    parser.add_argument('--snapshot-dir', default=sources.DEFAULT_SNAPSHOT_DIR)
    parser.add_argument('--catalog', default='itl2026')
    parser.add_argument('--folds', type=int, default=5)
    parser.add_argument('--min-charts', type=int, default=10, help='skip players with fewer played charts (default: 10)')
    parser.add_argument('--limit', type=int, default=300, help='max players when aggregating (default: 300)')
    parser.add_argument('--iqr-k', type=float, default=4.0)
    parser.add_argument('--shrink-k', type=float, default=30.0)
    parser.add_argument('--shrink-toward', choices=['flat', 'pop'], default='flat', help='slope prior for shrink models (default: flat = 0)')
    parser.add_argument('--adaptive-n', type=int, default=40, help='"adaptive": use horizon at/above this many charts, else shrink-to-flat (default: 40)')
    parser.add_argument('--flat-n', type=int, default=40, help='"tiered": below this many charts use a flat fit (default: 40)')
    parser.add_argument('--linear-n', type=int, default=200, help='"tiered": below this many charts use linear, else horizon (default: 200)')
    parser.add_argument('--inner-folds', type=int, default=3, help='"select": inner CV folds for per-player model choice (default: 3)')
    args = parser.parse_args(argv)
    args.full_n = 0

    snap_path = args.snapshot or sources.find_latest_snapshot(args.snapshot_dir, args.catalog)
    print(f'Snapshot: {snap_path}')
    scooby = Scobility.from_snapshot(snap_path)
    pts = player_points(scooby.snapshot, scooby)
    pop_slope = population_slope(pts)
    prior = {'slope': 0.0 if args.shrink_toward == 'flat' else pop_slope}
    print(f'Population slope: {pop_slope:.3f} | shrink toward: {args.shrink_toward} ({prior["slope"]:.3f}) '
          f'| folds={args.folds} shrink-k={args.shrink_k} iqr-k={args.iqr_k} adaptive-n={args.adaptive_n}\n')

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

    rows = []   # (eid, n, {model: (overall, hard)})
    for eid in targets:
        data = pts[eid]
        rows.append((eid, len(data), cv_player(data, args.folds, prior, args)))

    if args.players:
        for eid, n, res in rows:
            print(f'{name_by_eid[eid]} (n={n}):   overall / hardest-quartile')
            for name, _ in MODELS:
                o, h = res[name]
                print(f'    {name:14s}: {o:5.2f}  /  {h:5.2f}')
            print()

    def agg(subset, idx):
        return {name: statistics.fmean(r[2][name][idx] for r in subset if not math.isnan(r[2][name][idx]))
                for name, _ in MODELS}

    print(f'=== Aggregate over {len(rows)} players (mean of per-player CV error) ===')
    over, hard = agg(rows, 0), agg(rows, 1)
    print(f'{"model":16s}{"overall":>10s}{"hardest-Q":>12s}')
    for name, _ in MODELS:
        print(f'{name:16s}{over[name]:>10.2f}{hard[name]:>12.2f}')

    show = ['linear', 'linear_huber', 'horizon', 'horizon_huber', 'adaptive', 'adaptive_huber']
    buckets = [(args.min_charts, 40), (40, 100), (100, 200), (200, 10 ** 9)]
    print('\n=== By chart count (overall mean EX err) ===')
    print('band'.ljust(11) + 'players'.rjust(8) + ''.join(m[:8].rjust(9) for m in show))
    for lo, hi in buckets:
        sub = [r for r in rows if lo <= r[1] < hi]
        if not sub:
            continue
        cells = ''.join(f'{statistics.fmean(r[2][m][0] for r in sub):9.2f}' for m in show)
        band = f'[{lo},{"inf" if hi >= 10 ** 9 else hi})'
        print(band.ljust(11) + str(len(sub)).rjust(8) + cells)
    return 0


if __name__ == '__main__':
    sys.exit(main())
