#!/usr/bin/env python3
"""Rebuild the bundled tech-profile percentile cohort from the live API.

Fetches spice from the scobility API and each SP chart's leaderboard from the
ITL API (chartTopScores), reconstructs every player's per-chart scores, and
writes the per-feature gamma/SE distribution to data/ITL2026/tech_population.json
(the cohort that --mode api uses for radar percentiles). Run deliberately: it's
one request per chart against a live third-party server.

    python build_tech_population.py
    python build_tech_population.py --sleep 1.0
"""

import argparse
import json
import os
import sys

import sources
import tech
from scobility import Scobility, DEFAULT_API_BASE
from groovestats import DEFAULT_ITL_BASE


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--catalog', default='itl2026')
    parser.add_argument('--api-base', default=DEFAULT_API_BASE)
    parser.add_argument('--itl-base', default=DEFAULT_ITL_BASE)
    parser.add_argument('--charts', help='charts.json path (default: bundled data/ITL2026 or newest scrape)')
    parser.add_argument('--snapshot-dir', default=sources.DEFAULT_SNAPSHOT_DIR)
    parser.add_argument('--sleep', type=float, default=0.5, help='seconds between chart requests (default: 0.5)')
    args = parser.parse_args(argv)

    print(f'Spice:   {args.api_base}/catalog/{args.catalog.upper()}/chart/all')
    raw, spice_calc_time = Scobility.fetch_api_chart_all(args.catalog.upper(), args.api_base)
    spice = Scobility.from_raw_spice(raw).spice
    print(f'Spice calc time: {spice_calc_time}  (the cohort is stamped with this)')
    charts_path = (args.charts
                   or (sources.CHARTS_CACHE if os.path.isfile(sources.CHARTS_CACHE) else None)
                   or sources.find_latest_scratch_charts(args.snapshot_dir, args.catalog))
    print(f'Charts:  {charts_path}')
    with open(charts_path, encoding='utf-8') as f:
        charts = json.load(f)

    mean, std, _hi = tech.feature_stats(charts)
    feat_by_hash = tech.feature_vectors(charts, mean, std)
    n_sp = sum(1 for c in charts.values() if c.get('playstyle') == 1 and c['hash'] in spice)
    print(f'Scraping {n_sp} SP chart leaderboards from {args.itl_base} (sleep {args.sleep}s)...')

    cols = tech.build_population_api(spice, feat_by_hash, charts, args.itl_base, sleep=args.sleep, log=print)
    tech.write_population(cols, spice_calc_time)
    print(f'\nWrote {tech.POP_CACHE}: {len(cols["staminaLevel"])} players, spice basis {spice_calc_time}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
