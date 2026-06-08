"""Per-player spice fit and RP-target computation, reading a local scobility
snapshot instead of the live API.

The two-segment "horizon" fit (mild slope below your skill horizon, hot slope
above it) is unchanged from the original itl2026 toolkit; only the data sources
moved: chart spice and the player's scores now come from a scobility snapshot
JSON produced by the offline pipeline.
"""

import json
import math
import statistics
import urllib.request

from itldata import EX2SP, EX2EP


DEFAULT_API_BASE = 'https://scobility.azurewebsites.net'


# Pushes scores away from the log asymptote at a perfect EX. 1.003 here applies
# to the ex fraction (log2(PERFECT_OFFSET - ex)); the offline itl2026 run applies
# 0.003 to the diff-from-perfect value (log2(0.003 + value)). Since value == 1 -
# ex, these are identical, so this matches the pipeline that computed the spice.
# (Only the GrooveStats catalog uses 0.03 -> set this to 1.03 for that source.)
PERFECT_OFFSET = 1.003


def _target_ex_from_quality(spice, qf):
    """Invert the score-quality fit to a target EX (clamped to 0..100, 2dp)."""
    ex = 100.0 * (PERFECT_OFFSET - pow(2, spice - qf))
    if ex > 100:
        return 100
    if ex < 0:
        return 0
    return math.floor(ex * 100 + 0.5) * 0.01


def dumbassLSQComponents(a, b):
    s, s2, q, sq = 0, 0, 0, 0
    for i, va in enumerate(a):
        vb = b[i]
        s += va
        s2 += (va * va)
        q += vb
        sq += (va * vb)
    return len(a), s, s2, q, sq


def dumbassLSQFree(a, b):
    ones, s, s2, q, sq = dumbassLSQComponents(a, b)
    det = ones * s2 - s * s
    c1 = (-s * q + ones * sq) / det
    c0 = (s2 * q - s * sq) / det
    residual = 0
    for i, va in enumerate(a):
        vb = b[i]
        vr = vb - (c1 * va + c0)
        residual = residual + vr * vr
    return c0, c1, residual


def dumbassLSQWithCutPoint(a, b, anchor):
    al = a[:anchor]
    ar = a[anchor:]
    bl = b[:anchor]
    br = b[anchor:]
    if len(al) < 2 or len(ar) < 2:
        return None

    c0l, c1l, resl = dumbassLSQFree(al, bl)
    c0r, c1r, resr = dumbassLSQFree(ar, br)
    horizonSpice = (c1r - c1l) / (c0l - c0r)
    horizonQuality = c1l * horizonSpice + c0l
    timingPower = c0l if (horizonSpice > 0) else c0r
    return {
        "cutPoint": anchor,
        "timingPower": timingPower,
        "horizonSpice": horizonSpice,
        "horizonQuality": horizonQuality,
        "mildSlope": c1l,
        "hotSlope": c1r,
        "residual": resl + resr,
    }


def dumbassLSQAnchored(a, b, anchor):
    k = a[anchor]
    aOffset = []
    ones = len(a)
    q = 0
    for i, v in enumerate(a):
        aOffset.append(v - k)
        q += b[i]

    al = aOffset[:anchor]
    ar = aOffset[anchor:]
    bl = b[:anchor]
    br = b[anchor:]
    if len(al) < 2 or len(ar) < 2:
        return None

    onesl, sl, s2l, ql, sql = dumbassLSQComponents(al, bl)
    onesr, sr, s2r, qr, sqr = dumbassLSQComponents(ar, br)

    m11 = ones * s2r - sr * sr
    m12 = sl * sr
    m13 = -sl * s2r
    m22 = ones * s2l - sl * sl
    m23 = -s2l * sr
    m33 = s2l * s2r
    det = ones * s2r * s2l - sr * sr * s2l - sl * sl * s2r

    c1l = (m11 * sql + m12 * sqr + m13 * q) / det
    c1r = (m12 * sql + m22 * sqr + m23 * q) / det
    c0 = (m13 * sql + m23 * sqr + m33 * q) / det

    residual = 0
    for i, va in enumerate(aOffset):
        vb = b[i]
        c1choice = c1l if (i < anchor) else c1r
        vr = vb - (c1choice * va + c0)
        residual += vr * vr

    return {
        "c0": c0,
        "c1l": c1l,
        "c1r": c1r,
        "residual": residual,
    }


def spiceHorizonFit(a, b):
    best = None
    horizonCentering = math.floor(math.sqrt(len(a)))

    for j in range(horizonCentering, len(a) - horizonCentering + 1):
        bestFitHere = dumbassLSQWithCutPoint(a, b, j)
        if (not bestFitHere) or (bestFitHere["horizonSpice"] < a[j]) or (bestFitHere["horizonSpice"] > a[j + 1]):
            bestFitHereL = dumbassLSQAnchored(a, b, j)
            bestFitHereR = dumbassLSQAnchored(a, b, j + 1)
            if bestFitHereL and bestFitHereR:
                if bestFitHereL["residual"] < bestFitHereR["residual"]:
                    bestFitHere = {
                        "cutPoint": j,
                        "horizonSpice": a[j],
                        "horizonQuality": bestFitHereL["c0"],
                        "mildSlope": bestFitHereL["c1l"],
                        "hotSlope": bestFitHereL["c1r"],
                        "timingPower": bestFitHereL["c0"] - bestFitHereL["c1l"] * a[j],
                        "residual": bestFitHereL["residual"],
                    }
                else:
                    bestFitHere = {
                        "cutPoint": j,
                        "horizonSpice": a[j + 1],
                        "horizonQuality": bestFitHereR["c0"],
                        "mildSlope": bestFitHereR["c1l"],
                        "hotSlope": bestFitHereR["c1r"],
                        "timingPower": bestFitHereR["c0"] - bestFitHereR["c1l"] * a[j + 1],
                        "residual": bestFitHereR["residual"],
                    }

        if bestFitHere and ((not best) or (bestFitHere["residual"] < best["residual"])):
            best = bestFitHere

    return best


class Scobility:
    def __init__(self, spice, snapshot=None):
        """spice: dict of chart hash -> log2(spice). snapshot enables find_player."""
        self.spice = spice
        self.snapshot = snapshot
        self._song_by_sid = {s['s_id']: s for s in snapshot['songs']} if snapshot else {}
        self.playerData = {}

    @classmethod
    def from_snapshot(cls, snapshot_path):
        with open(snapshot_path, encoding='utf-8') as f:
            snap = json.load(f)
        # Single-style charts only (ITL is SP); spice stored as raw, kept as log2.
        spice = {
            s['hash']: math.log2(s['spice'])
            for s in snap['songs']
            if s.get('spice') is not None and s.get('style') == 'Single'
        }
        return cls(spice, snapshot=snap)

    @staticmethod
    def fetch_api_chart_all(catalog='ITL2026', base_url=DEFAULT_API_BASE, timeout=60):
        """({hash: raw spice}, latest spice_calc_time) from the scobility API."""
        url = f'{base_url}/catalog/{catalog}/chart/all'
        with urllib.request.urlopen(url, timeout=timeout) as r:
            data = json.load(r).get('data', {})
        raw = {h: e['spice'] for h, e in data.items() if e.get('spice') is not None}
        times = [e['spice_calc_time'] for e in data.values() if e.get('spice_calc_time')]
        return raw, (max(times) if times else None)

    @staticmethod
    def fetch_api_spice_raw(catalog='ITL2026', base_url=DEFAULT_API_BASE, timeout=60):
        """{hash: raw spice} from the scobility API (cacheable as-is)."""
        return Scobility.fetch_api_chart_all(catalog, base_url, timeout)[0]

    @classmethod
    def from_raw_spice(cls, raw):
        """Build from a {hash: raw spice} dict (snapshot/API/cache agnostic)."""
        return cls({h: math.log2(s) for h, s in raw.items() if s and s > 0})

    @classmethod
    def from_api(cls, catalog='ITL2026', base_url=DEFAULT_API_BASE, timeout=60):
        return cls.from_raw_spice(cls.fetch_api_spice_raw(catalog, base_url, timeout))

    def player_names(self):
        return sorted(p['name'] for p in self.snapshot['players'])

    def find_player(self, username):
        """Return (player dict, scores-by-hash) for a username, case-insensitive."""
        matches = [p for p in self.snapshot['players'] if p['name'].lower() == username.lower()]
        if not matches:
            raise KeyError(username)
        player = matches[0]
        e_id = player['e_id']

        scores = {}
        for v in self.snapshot['scores']:
            if v['e_id'] != e_id:
                continue
            song = self._song_by_sid.get(v['s_id'])
            if song is None or song.get('style') != 'Single':
                continue
            scores[song['hash']] = {
                'value': v['value'],
                'clear': v['clear'],
                'last_played': v.get('last_played'),
                'plays': v.get('plays'),
            }
        return player, scores

    def processPlayer(self, playerKey, itlData, spice_iqr_mult=None,
                      fit='horizon', adaptive_n=40, shrink_k=30.0):
        self.playerData[playerKey] = itlData

        # Attach spice to every chart we know (so unplayed charts can still be
        # recommended); attach quality only where the player has a score.
        for hsh, song in itlData.hashes.items():
            if hsh in self.spice:
                song.spice = self.spice[hsh]
                if song.clearType > 0:
                    song.quality = song.spice - math.log2(PERFECT_OFFSET - song.ex * 0.01)

        # Fit on played charts only. (The original sorted every chart by spice,
        # which assumes the input only holds played charts.)
        played = sorted(
            (s for s in itlData.hashes.values() if s.quality is not None),
            key=lambda s: s.spice,
        )
        if len(played) < 5:
            raise ValueError(
                f'not enough played charts with known spice to fit ({len(played)} found, need >= 5)'
            )

        # Optional Tukey-fence rejection of spice outliers from the fit set. The
        # rejected charts keep their spice/targets; they're just out of the fit.
        itlData.rejected_outliers = []
        if spice_iqr_mult is not None and len(played) >= 4:
            spices = sorted(s.spice for s in played)
            q1, _q2, q3 = statistics.quantiles(spices, n=4, method='exclusive')
            iqr = q3 - q1
            lo, hi = q1 - spice_iqr_mult * iqr, q3 + spice_iqr_mult * iqr
            kept = [s for s in played if lo <= s.spice <= hi]
            if len(kept) >= 5:
                itlData.rejected_outliers = [s for s in played if not (lo <= s.spice <= hi)]
                played = kept

        # adaptive: horizon when there's enough data to trust its shape; for
        # sparse players a linear fit with the slope shrunk toward flat (which
        # cross-validates better than horizon there). Stored in the horizon
        # shape (mild==hot, horizon at 0) so the downstream math is identical.
        if fit == 'adaptive' and len(played) < adaptive_n:
            xs = [s.spice for s in played]
            qs = [s.quality for s in played]
            n = len(played)
            _c0, slope, _r = dumbassLSQFree(xs, qs)
            b = n * slope / (n + shrink_k)
            a = statistics.fmean(q - b * x for x, q in zip(xs, qs))
            itlData.cutPoint = 0
            itlData.horizonSpice = 0.0
            itlData.horizonQuality = a
            itlData.mildSlope = b
            itlData.hotSlope = b
            itlData.timingPower = a
            itlData.residual = sum((q - (a + b * x)) ** 2 for x, q in zip(xs, qs))
            itlData.fit_used = f'linear shrunk-to-flat (n={n} < {adaptive_n})'
        else:
            coefs = spiceHorizonFit([x.spice for x in played], [x.quality for x in played])
            itlData.cutPoint = coefs["cutPoint"]
            itlData.horizonSpice = coefs["horizonSpice"]
            itlData.horizonQuality = coefs["horizonQuality"]
            itlData.mildSlope = coefs["mildSlope"]
            itlData.hotSlope = coefs["hotSlope"]
            itlData.timingPower = coefs["timingPower"]
            itlData.residual = coefs["residual"]
            itlData.fit_used = 'horizon'

        self.recompute_targets(itlData)

    def recompute_targets(self, itlData, overlay=None, ex_cap=None):
        """Set each chart's qualityFit/targetEX/potential* from the spice fit.

        `overlay`, if given, is a callable song -> extra score quality (the
        tech-aware adjustment); the spice-only fit is kept on `song.qualityFit`,
        the adjusted prediction drives targetEX, and `ex_cap` bounds how far the
        adjustment can move a chart's target EX from the spice-only value.
        """
        for hsh, song in itlData.hashes.items():
            if song.spice is None:
                continue

            if song.spice <= itlData.horizonSpice:
                qualityFit = itlData.mildSlope * (song.spice - itlData.horizonSpice) + itlData.horizonQuality
            else:
                qualityFit = itlData.hotSlope * (song.spice - itlData.horizonSpice) + itlData.horizonQuality
            song.qualityFit = qualityFit

            targetEX = _target_ex_from_quality(song.spice, qualityFit)
            if overlay is not None:
                adj = _target_ex_from_quality(song.spice, qualityFit + overlay(song))
                if ex_cap is not None:
                    adj = min(targetEX + ex_cap, max(targetEX - ex_cap, adj))
                targetEX = adj

            song.targetEX = targetEX

            targetSP = math.floor(song.passingPoints + song.maxScoringPoints * EX2SP(targetEX) * 0.01)
            targetEP = 1000 if (targetEX == 100) else EX2EP(targetEX)

            if any(s.hsh == hsh for s in itlData.top75()):
                song.potentialSP = targetSP - song.points
            else:
                song.potentialSP = targetSP - itlData.floorSP()
            if song.potentialSP < 0:
                song.potentialSP = 0

            if song.rating not in itlData.exTrapezoid:
                song.potentialEP = 0
            elif any(s.hsh == hsh for s in itlData.exTrapezoid[song.rating]):
                song.potentialEP = targetEP - EX2EP(song.ex)
            else:
                song.potentialEP = targetEP - EX2EP(itlData.floorEPEX(song.rating))
            if song.potentialEP < 0:
                song.potentialEP = 0

            song.potentialRP = song.potentialSP + song.potentialEP


def fetch_player_index(catalog='ITL2026', base_url=DEFAULT_API_BASE, timeout=30):
    """Name -> [entrant_id, name] from the scobility API's player listing.

    One request to /catalog/{c}/players -- a snapshot-free way to resolve a
    GrooveStats entrant name to the id used for scraping.
    """
    url = f'{base_url}/catalog/{catalog}/players'
    with urllib.request.urlopen(url, timeout=timeout) as r:
        payload = json.load(r)
    data = payload.get('data', payload)
    index = {}
    for entry in data.values():
        name, eid = entry.get('name'), entry.get('entrant_id')
        if name is not None and eid is not None:
            index[name.lower()] = [eid, name]
    return index
