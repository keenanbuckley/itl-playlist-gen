"""ITL scoring math and per-player chart data.

Chart-static fields (song folder, point ceilings, block rating) come from the
scrape's charts.json -- the complete catalog. The on-disk group folder is not in
charts.json, so it's derived from the unlock-folder list extracted from the "ITL
Online 2026 Unlocks" pack: a chart lives in "ITL Online 2026 Unlocks" iff its
folder is an unlock folder, otherwise "ITL Online 2026". The per-player dynamic
fields (EX, clear, pass date, points) are reconstructed from a scobility
snapshot, so the playlist refreshes from a new snapshot without re-exporting
anything.
"""

import math


ITL_GROUP = 'ITL Online 2026'
ITL_UNLOCK_GROUP = 'ITL Online 2026 Unlocks'


# scobility snapshot stores Score.value = 1 - score_ex * SCORE_SCALAR,
# where score_ex is the EX percentage times 100 (10000 == a perfect 100.00%).
SCORE_SCALAR = 0.0001

POW_BASE = 40.0
INFLECT = 40.0
CUTOFF_EP = 85.0


def EX2SP(expct):
    """EX percentage (0..100) -> scoring-point fraction (0..100)."""
    return (
        100.0 *
        (pow(POW_BASE, expct / INFLECT) - 1) /
        (pow(POW_BASE, 100.0 / INFLECT) - 1)
    )


def SP2EX(sppct):
    century_scale = 100.0 / (pow(POW_BASE, 100.0 / INFLECT) - 1)
    return (
        INFLECT *
        math.log(sppct / century_scale + 1) /
        math.log(POW_BASE)
    )


def EX2EP(expct):
    ex_clamp = (expct - CUTOFF_EP) if (expct > CUTOFF_EP) else 0
    return min(1000, math.floor((pow(100, ex_clamp / (100.0 - CUTOFF_EP)) - 1) * (1000.0 / 99.0)))


def value_to_ex(value):
    """scobility snapshot score value -> EX percentage on the 0..100 scale."""
    return (1.0 - value) * 100.0


# How many charts per block rating contribute to the EX-points trapezoid.
EP_COUNTS = {
    7: 5,
    8: 5,
    9: 5,
    10: 5,
    11: 5,
    12: 4,
    13: 3,
    14: 2,
    15: 1,
}


class Song:
    def __init__(self, chart, unlock_folders):
        """chart: a charts.json entry; unlock_folders: set of unlock folder names."""
        self.hsh = chart['hash']

        folder = chart['chartSongDir']
        group = ITL_UNLOCK_GROUP if folder in unlock_folders else ITL_GROUP
        self.path = f'{group}\\{folder}'
        self.rating = int(chart['meter'])

        # Chart-static (same for every player, stable across snapshots).
        self.maxPoints = chart['points']
        self.maxScoringPoints = chart['pointsScoring']
        self.passingPoints = chart['pointsPassing']

        # Per-player dynamic fields; default to "never played".
        self.played = False
        self.clearType = 0
        self.date = None
        self.ex = 0.0
        self.ep = 0
        self.points = 0

        # Filled in by Scobility.processPlayer.
        self.spice = None
        self.quality = None
        self.targetEX = None        # EX the fit predicts this player would score
        self.potentialSP = 0
        self.potentialEP = 0
        self.potentialRP = 0

    def apply_score(self, value, clear, last_played):
        self.played = True
        self.clearType = clear
        self.date = last_played
        self.ex = value_to_ex(value)
        self.ep = EX2EP(self.ex)
        self.points = math.floor(self.passingPoints + self.maxScoringPoints * EX2SP(self.ex) * 0.01)


class ITLData:
    def __init__(self, charts, unlock_folders, player_scores):
        """charts: charts.json (id -> chart dict); player_scores: hash -> {'value', 'clear', 'last_played'}."""
        self.hashes = {}
        self.paths = {}

        for chart in charts.values():
            if chart.get('playstyle') != 1:     # Single only (ITL SP)
                continue
            song = Song(chart, unlock_folders)
            score = player_scores.get(song.hsh)
            if score is not None:
                song.apply_score(score['value'], score['clear'], score['last_played'])
            self.paths[song.path] = song
            self.hashes[song.hsh] = song

        self.songs = sorted(self.paths.values(), key=lambda x: x.points, reverse=True)

        self.exTrapezoid = {rating: [] for rating in EP_COUNTS}
        for song in self.songs:
            if song.rating in self.exTrapezoid:
                self.exTrapezoid[song.rating].append(song)
        for rating in EP_COUNTS:
            self.exTrapezoid[rating].sort(key=lambda x: x.ex, reverse=True)
            self.exTrapezoid[rating] = self.exTrapezoid[rating][:EP_COUNTS[rating]]

    def top75(self):
        return self.songs[:75]

    def floorSP(self):
        if len(self.songs) < 75:
            return 0
        return self.songs[74].points

    def floorEPEX(self, rating):
        if rating not in EP_COUNTS:
            return 100
        if len(self.exTrapezoid[rating]) < EP_COUNTS[rating]:
            return 0
        return self.exTrapezoid[rating][-1].ex

    def currentSP(self):
        return sum(x.points for x in self.top75())

    def currentEP(self):
        return sum(sum(EX2EP(song.ex) for song in row) for row in self.exTrapezoid.values())
