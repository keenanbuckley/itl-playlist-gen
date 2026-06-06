#!/usr/bin/env python3
"""Scrape the ITL chart catalog into charts.json and unlock_folders.txt.

charts.json is the full chart list, walked from the per-chart API endpoint (the
entrant endpoint's chart list is incomplete). unlock_folders.txt is derived from
it: every chart whose unlockId != -1 belongs to the "ITL Online 2026 Unlocks"
group (this matches the actual Unlocks pack exactly). Run this to refresh both
without the full scobility offline pipeline.

    python fetch_catalog.py                 # -> data/charts.json + unlock_folders.txt
    python fetch_catalog.py --sleep 1.0     # gentler on the server
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

from groovestats import DEFAULT_ITL_BASE

REPO = os.path.dirname(__file__)
DATA_DIR = os.path.join(REPO, 'data', 'ITL2026')
DEFAULT_CHARTS_OUT = os.path.join(DATA_DIR, 'charts.json')
DEFAULT_UNLOCK_OUT = os.path.join(DATA_DIR, 'unlock_folders.txt')


def fetch_chart(base, i, timeout):
    try:
        with urllib.request.urlopen(f'{base}/api/chart/{i}', timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {'success': False, 'message': f'HTTP {e.code}'}
    except Exception as e:
        return {'success': False, 'message': str(e)}


def scrape_charts(base, start, max_id, max_strikes, sleep, timeout):
    charts = {}
    strikes = 0
    for i in range(start, max_id + 1):
        j = fetch_chart(base, i, timeout)
        if j.get('success'):
            data = j.get('data', {})
            charts[str(data.get('id', i))] = data
            strikes = 0
            print(f'  {i:4d}: {data.get("artist")} - {data.get("title")}')
        else:
            strikes += 1
            if strikes >= max_strikes:
                print(f'  stopping: {max_strikes} consecutive misses ending at id {i}')
                break
        time.sleep(sleep)
    return charts


def derive_unlock_folders(charts):
    # unlockId == -1 is the base group; anything else is an unlock-pack chart.
    return sorted({c['chartSongDir'] for c in charts.values() if c.get('unlockId', -1) != -1})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--itl-base', default=DEFAULT_ITL_BASE, help=f'ITL GrooveStats API base URL (default: {DEFAULT_ITL_BASE})')
    parser.add_argument('--charts-out', default=DEFAULT_CHARTS_OUT, help='charts.json output path')
    parser.add_argument('--unlock-out', default=DEFAULT_UNLOCK_OUT, help='unlock_folders.txt output path')
    parser.add_argument('--start', type=int, default=1, help='first chart id to try (default: 1)')
    parser.add_argument('--max-id', type=int, default=10000, help='highest chart id to try (default: 10000)')
    parser.add_argument('--max-strikes', type=int, default=100, help='stop after this many consecutive misses (default: 100)')
    parser.add_argument('--sleep', type=float, default=0.5, help='seconds between requests (default: 0.5)')
    parser.add_argument('--timeout', type=float, default=30, help='per-request timeout seconds (default: 30)')
    args = parser.parse_args(argv)

    print(f'Scraping charts from {args.itl_base}/api/chart/ (sleep {args.sleep}s)...')
    charts = scrape_charts(args.itl_base, args.start, args.max_id, args.max_strikes, args.sleep, args.timeout)
    if not charts:
        print('No charts scraped.', file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(os.path.abspath(args.charts_out)), exist_ok=True)
    with open(args.charts_out, 'w', encoding='utf-8') as f:
        json.dump(charts, f)

    folders = derive_unlock_folders(charts)
    os.makedirs(os.path.dirname(os.path.abspath(args.unlock_out)), exist_ok=True)
    with open(args.unlock_out, 'w', encoding='utf-8') as f:
        f.write('\n'.join(folders) + '\n')

    sp = sum(1 for c in charts.values() if c.get('playstyle') == 1)
    dp = sum(1 for c in charts.values() if c.get('playstyle') == 2)
    print(f'\nWrote {len(charts)} charts ({sp} SP / {dp} DP) to {args.charts_out}')
    print(f'Wrote {len(folders)} unlock folders to {args.unlock_out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
