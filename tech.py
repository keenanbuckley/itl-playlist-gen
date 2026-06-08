"""Per-player tech profile: how a player over/under-performs on charts heavy in
each tech, beyond what spice predicts.

The profile is the ridge regression of (score quality - spice fit) on z-scored
chart tech features. Display the reliable axes (stamina, brackets, footswitch,
crossover, XMOD) per the split-half reliability study; the rest are fit but
not shown. Two views: EX%-impact (bars) and percentile-vs-field (radar).
"""

import json
import math
import os
import statistics
import time
import urllib.parse
import urllib.request

from scobility import spiceHorizonFit, dumbassLSQFree

PO = 1.003
TECHS = ['crossoverLevel', 'bracketLevel', 'footswitchLevel', 'jackLevel',
         'sideswitchLevel', 'doublestepLevel', 'staminaLevel']
FEATURES = TECHS + ['isNoCmod']
# Reliable axes (split-half SB >= ~0.7), in display order, with friendly labels.
DISPLAY = [('staminaLevel', 'stamina'), ('bracketLevel', 'brackets'),
           ('footswitchLevel', 'footswitch'), ('crossoverLevel', 'crossover'),
           ('isNoCmod', 'XMOD')]
DEFAULT_LAMBDA = 40.0
POP_CACHE = os.path.join(os.path.dirname(__file__), 'data', 'ITL2026', 'tech_population.json')


def grade(pct):
    """Percentile -> letter (C = average, S/A/B above, D/E/F below)."""
    for cut, letter in ((90, 'S'), (75, 'A'), (60, 'B'), (40, 'C'), (25, 'D'), (10, 'E')):
        if pct >= cut:
            return letter
    return 'F'


def _raw(c, f):
    if f == 'isNoCmod':
        return 1.0 if c.get('isNoCmod') else 0.0
    return c.get(f) or 0


def quality(spice, exfrac):
    return spice - math.log2(PO - exfrac)


def pred_ex(qf, spice):
    return min(100.0, max(0.0, 100.0 * (PO - 2 ** (spice - qf))))


def feature_stats(charts):
    sp = [c for c in charts.values() if c.get('playstyle') == 1]
    mean = {f: statistics.fmean(_raw(c, f) for c in sp) for f in FEATURES}
    std = {f: statistics.pstdev([_raw(c, f) for c in sp]) or 1.0 for f in FEATURES}
    hi = {f: sorted(_raw(c, f) for c in sp)[int(0.9 * (len(sp) - 1))] for f in FEATURES}  # "heavy" load
    return mean, std, hi


def feature_vectors(charts, mean, std):
    out = {}
    for c in charts.values():
        if c.get('playstyle') == 1:
            out[c['hash']] = [1.0] + [(_raw(c, f) - mean[f]) / std[f] for f in FEATURES]
    return out


def adaptive_fit(pairs):
    """pairs: [(spice, quality)] -> predictor; horizon if data-rich else flat-shrunk."""
    n = len(pairs)
    if n >= 40:
        sp = sorted(pairs)
        f = spiceHorizonFit([x for x, _ in sp], [q for _, q in sp])
        if f:
            hs, hq, mi, ho = f['horizonSpice'], f['horizonQuality'], f['mildSlope'], f['hotSlope']
            return lambda x: (mi if x <= hs else ho) * (x - hs) + hq
    a = [x for x, _ in pairs]
    b = [q for _, q in pairs]
    _c, c1, _ = dumbassLSQFree(a, b)
    s = n * c1 / (n + 30)
    i = statistics.fmean(q - s * x for x, q in pairs)
    return lambda x: i + s * x


def _solve(A, b):
    p = len(b)
    M = [A[i][:] + [b[i]] for i in range(p)]
    for col in range(p):
        piv = max(range(col, p), key=lambda r: abs(M[r][col]))
        M[col], M[piv] = M[piv], M[col]
        if abs(M[col][col]) < 1e-12:
            continue
        for r in range(p):
            if r != col:
                fr = M[r][col] / M[col][col]
                for k in range(col, p + 1):
                    M[r][k] -= fr * M[col][k]
    return [M[i][p] / M[i][i] if abs(M[i][i]) > 1e-12 else 0.0 for i in range(p)]


def _inverse(A):
    p = len(A)
    M = [A[i][:] + [1.0 if j == i else 0.0 for j in range(p)] for i in range(p)]
    for col in range(p):
        piv = max(range(col, p), key=lambda r: abs(M[r][col]))
        M[col], M[piv] = M[piv], M[col]
        d = M[col][col]
        if abs(d) < 1e-12:
            continue
        M[col] = [v / d for v in M[col]]
        for r in range(p):
            if r != col and M[r][col]:
                fr = M[r][col]
                M[r] = [M[r][k] - fr * M[col][k] for k in range(2 * p)]
    return [row[p:] for row in M]


def _ridge_cov(X, y, lam):
    p = len(X[0])
    XtX = [[sum(X[i][a] * X[i][b] for i in range(len(X))) for b in range(p)] for a in range(p)]
    Xty = [sum(X[i][a] * y[i] for i in range(len(X))) for a in range(p)]
    A = [row[:] for row in XtX]
    for j in range(1, p):
        A[j][j] += lam
    beta = _solve(A, Xty)
    n = len(X)
    rss = sum((y[i] - sum(beta[k] * X[i][k] for k in range(p))) ** 2 for i in range(n))
    sig2 = rss / max(1, n - p)
    inv = _inverse(A)
    cov_diag = [sig2 * inv[j][j] for j in range(p)]
    return beta, cov_diag


def snr(gamma, se):
    """Signal-to-noise (coefficient / its standard error) per feature."""
    return {f: (gamma[f] / se[f] if se.get(f, 0) > 0 else 0.0) for f in gamma}


def player_profile(pairs, feat_by_hash, lam=DEFAULT_LAMBDA):
    """pairs: [(spice, quality, hash)]. Returns (fit, gamma{feat:coef}, se{feat}, n)."""
    f = adaptive_fit([(s, q) for s, q, _h in pairs])
    X, y = [], []
    for s, q, h in pairs:
        if h in feat_by_hash:
            X.append(feat_by_hash[h])
            y.append(q - f(s))
    beta, cov = _ridge_cov(X, y, lam)
    gamma = {feat: beta[i + 1] for i, feat in enumerate(FEATURES)}
    se = {feat: math.sqrt(max(0.0, cov[i + 1])) for i, feat in enumerate(FEATURES)}
    return f, gamma, se, len(X)


def ex_impact(gamma_f, fit, spice_ref, z_load):
    """EX-point swing on a 'heavy' chart of this tech at the player's typical spice."""
    qf = fit(spice_ref)
    return pred_ex(qf + gamma_f * z_load, spice_ref) - pred_ex(qf, spice_ref)


def pairs_from_scores(scores, spice):
    """scores: hash -> {value,...}; spice: hash -> log2 spice. -> [(spice, quality, hash)]."""
    out = []
    for h, sc in scores.items():
        if h in spice:
            out.append((spice[h], quality(spice[h], 1 - sc['value']), h))
    return out


def _cols_from_players(by_player, feat_by_hash, lam, min_charts):
    """by_player: eid -> [(spice, quality, hash)] -> {feature: sorted gamma/SE}.

    Uses the t-statistic (coefficient / its standard error) so players are ranked
    by confidence-weighted strength; under-determined coefficients fall near the
    median instead of being ranked on heavily-shrunk gammas.
    """
    cols = {f: [] for f in FEATURES}
    for d in by_player.values():
        if len(d) < min_charts:
            continue
        _f, gamma, se, _n = player_profile(d, feat_by_hash, lam)
        sn = snr(gamma, se)
        for f in FEATURES:
            cols[f].append(sn[f])
    for f in cols:
        cols[f].sort()
    return cols


def build_population(snapshot, feat_by_hash, lam=DEFAULT_LAMBDA, min_charts=60):
    """Population percentile basis from a local snapshot (snapshot mode)."""
    songs = {s['s_id']: s for s in snapshot['songs']}
    spice = {s['hash']: math.log2(s['spice']) for s in snapshot['songs']
             if s.get('spice') and s.get('style') == 'Single'}
    by_player = {}
    for v in snapshot['scores']:
        so = songs.get(v['s_id'])
        if so and so.get('style') == 'Single' and so['hash'] in spice:
            sp = spice[so['hash']]
            by_player.setdefault(v['e_id'], []).append((sp, quality(sp, 1 - v['value']), so['hash']))
    return _cols_from_players(by_player, feat_by_hash, lam, min_charts)


def _chart_leaderboard(chart_hash, itl_base, timeout=30):
    body = urllib.parse.urlencode({'chartHash': chart_hash}).encode()
    req = urllib.request.Request(f'{itl_base}/api/score/chartTopScores', data=body)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r).get('data', {}).get('leaderboard', [])


def build_population_api(spice, feat_by_hash, charts, itl_base, sleep=0.5,
                         lam=DEFAULT_LAMBDA, min_charts=60, log=None):
    """Population percentile basis from the live API (one chartTopScores request
    per SP chart, reconstructing the player x chart matrix from leaderboards)."""
    hashes = [c['hash'] for c in charts.values() if c.get('playstyle') == 1 and c['hash'] in spice]
    by_player = {}
    for i, h in enumerate(hashes):
        for e in _chart_leaderboard(h, itl_base):
            if e.get('clearType', 0) > 0 and e.get('ex') is not None:
                by_player.setdefault(e['entrantId'], []).append((spice[h], quality(spice[h], 1 - e['ex'] / 10000.0), h))
        if log and i % 50 == 0:
            log(f'  {i}/{len(hashes)} charts scraped')
        time.sleep(sleep)
    return _cols_from_players(by_player, feat_by_hash, lam, min_charts)


def write_population(cols, spice_calc_time=None):
    os.makedirs(os.path.dirname(POP_CACHE), exist_ok=True)
    cohort = len(next(iter(cols.values()), []))
    payload = {'spice_calc_time': spice_calc_time, 'cohort': cohort, 'features': cols}
    with open(POP_CACHE, 'w', encoding='utf-8') as fp:
        json.dump(payload, fp)


def load_bundled_population():
    """The committed, API-derived cohort (used by api mode). -> (features, meta)."""
    if not os.path.isfile(POP_CACHE):
        return None, {}
    with open(POP_CACHE, encoding='utf-8') as fp:
        d = json.load(fp)
    if 'features' in d:
        return d['features'], {'spice_calc_time': d.get('spice_calc_time'), 'cohort': d.get('cohort')}
    return d, {}   # legacy bare-columns format


def percentile(value, sorted_vals):
    if not sorted_vals:
        return None
    below = sum(1 for v in sorted_vals if v < value)
    return 100.0 * below / len(sorted_vals)
