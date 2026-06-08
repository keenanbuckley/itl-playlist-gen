"""Source resolution for the two scobility modes.

snapshot mode: spice + scores come from a local scobility snapshot; the catalog
(folders/points) comes from the scrape's local charts.json with unlock groups
derived from unlockId. Nothing is written.

api mode: spice, catalog, unlock list, and entrant index all come from the APIs
and are cached under data/ITL2026 (refresh=True re-fetches). Scores come from a
live GrooveStats scrape (handled by the caller).
"""

import datetime
import glob
import json
import os
import re
import sys
import urllib.request

import fetch_catalog
from groovestats import write_index_cache, suggest_names, scrape_entrant_scores
from scobility import Scobility, fetch_player_index

REPO = os.path.dirname(__file__)
DATA_DIR = os.path.join(REPO, 'data', 'ITL2026')
SPICE_CACHE = os.path.join(DATA_DIR, 'spice.json')
CATALOG_CACHE = os.path.join(DATA_DIR, 'catalog.json')
CHARTS_CACHE = os.path.join(DATA_DIR, 'charts.json')
UNLOCK_CACHE = os.path.join(DATA_DIR, 'unlock_folders.txt')
INDEX_CACHE = os.path.join(DATA_DIR, 'entrant_index.json')

DEFAULT_SNAPSHOT_DIR = os.environ.get(
    'SCOBILITY_SCRATCH', os.path.expanduser('~/scobility/scratch')
)


def _write_json(path, obj):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f)


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
                'plays': None,    # ITL2026.json export has no pass count
            }
    return scores


def name_from_export(export_path):
    stem = os.path.splitext(os.path.basename(export_path))[0]
    for prefix in ('ITL2026 ', 'ITL2026_', 'ITL2026'):
        if stem.startswith(prefix):
            return stem[len(prefix):].strip() or stem
    return stem


def find_latest_snapshot(directory, catalog='itl2026'):
    cands = sorted(glob.glob(os.path.join(directory, f'scobility_{catalog}*.json')))
    if not cands:
        raise FileNotFoundError(
            f'no scobility_{catalog}*.json in {directory} (set --snapshot or $SCOBILITY_SCRATCH)'
        )
    return cands[-1]


def find_latest_scratch_charts(directory, catalog='itl2026'):
    cands = sorted(glob.glob(os.path.join(directory, f'{catalog}_data', '*', 'charts.json')))
    return cands[-1] if cands else None


def _snapshot_date(path):
    m = re.search(r'(\d{8})', os.path.basename(path))
    if m:
        return datetime.datetime.strptime(m.group(1), '%Y%m%d').date()
    return datetime.date.fromtimestamp(os.path.getmtime(path))


def _fetch_api_spice_and_date(api_base, catalog):
    """({hash: raw spice}, latest spice_calc_time date) from the scobility API."""
    url = f'{api_base}/catalog/{catalog}/chart/all'
    with urllib.request.urlopen(url, timeout=60) as r:
        data = json.load(r).get('data', {})
    raw = {h: e['spice'] for h, e in data.items() if e.get('spice') is not None}
    dates = [e['spice_calc_time'][:10] for e in data.values() if e.get('spice_calc_time')]
    latest = max((datetime.date.fromisoformat(d) for d in dates), default=None)
    return raw, latest


def _choose_mode(snapshot_dir, catalog, api_base, snapshot_path):
    """Pick the source with the newer scobility. Returns (mode, prefetched_raw)."""
    try:
        snap = snapshot_path or find_latest_snapshot(snapshot_dir, catalog)
        snap_date = _snapshot_date(snap)
    except FileNotFoundError:
        snap_date = None

    api_raw, api_date = None, None
    try:
        api_raw, api_date = _fetch_api_spice_and_date(api_base, catalog.upper())
    except Exception as e:
        print(f'(scobility API unavailable for the auto check: {e})')

    if snap_date and api_date:
        if api_date > snap_date:
            print(f'Auto:         API spice ({api_date}) newer than snapshot ({snap_date}) -> api')
            return 'api', api_raw
        print(f'Auto:         snapshot ({snap_date}) >= API spice ({api_date}) -> snapshot')
        return 'snapshot', None
    if api_date:
        print('Auto:         no local snapshot -> api')
        return 'api', api_raw
    if snap_date:
        print('Auto:         scobility API unreachable -> snapshot')
        return 'snapshot', None
    raise FileNotFoundError('no local snapshot and the scobility API is unreachable')


def resolve_catalog(mode, *, snapshot_dir, catalog, api_base, itl_base,
                    snapshot_path=None, charts_path=None, refresh=False, sleep=0.5):
    """Return (scooby, charts, unlock_folders, snapshot_or_None, mode, source_lines)."""
    prefetched = None
    if mode == 'auto':
        mode, prefetched = _choose_mode(snapshot_dir, catalog, api_base, snapshot_path)
    if mode == 'api':
        return _resolve_api(catalog, api_base, itl_base, refresh, sleep, prefetched_raw=prefetched)
    return _resolve_snapshot(snapshot_dir, catalog, snapshot_path, charts_path)


def _resolve_snapshot(snapshot_dir, catalog, snapshot_path, charts_path):
    snap_path = snapshot_path or find_latest_snapshot(snapshot_dir, catalog)
    scooby = Scobility.from_snapshot(snap_path)

    cpath = charts_path or find_latest_scratch_charts(snapshot_dir, catalog)
    if not cpath or not os.path.isfile(cpath):
        raise FileNotFoundError(
            f'no local charts.json under {snapshot_dir}/{catalog}_data/*/ '
            f'(pass --charts, or use --mode api)'
        )
    with open(cpath, encoding='utf-8') as f:
        charts = json.load(f)
    unlock = set(fetch_catalog.derive_unlock_folders(charts))

    lines = [
        'Mode:         snapshot (local, nothing cached)',
        f'Spice:        snapshot ({snap_path})',
        f'Charts:       {cpath}',
        f'Unlocks:      derived from charts.json unlockId ({len(unlock)} folders)',
    ]
    return scooby, charts, unlock, scooby.snapshot, 'snapshot', lines


def _resolve_api(catalog, api_base, itl_base, refresh, sleep, prefetched_raw=None):
    catalog_api = catalog.upper()

    # Spice (cached raw).
    if prefetched_raw is not None:
        raw = prefetched_raw
        _write_json(SPICE_CACHE, raw)
        spice_src = f'API -> cached ({SPICE_CACHE})'
    elif os.path.isfile(SPICE_CACHE) and not refresh:
        with open(SPICE_CACHE, encoding='utf-8') as f:
            raw = json.load(f)
        spice_src = f'cache ({SPICE_CACHE})'
    else:
        raw = Scobility.fetch_api_spice_raw(catalog_api, api_base)
        _write_json(SPICE_CACHE, raw)
        spice_src = f'API -> cached ({SPICE_CACHE})'

    # Catalog metadata (perfect_offset for the score-quality math). Cached like
    # spice so api mode can still run entirely from cache; falls back to the ITG
    # default if the catalog row is unreachable.
    if os.path.isfile(CATALOG_CACHE) and not refresh:
        with open(CATALOG_CACHE, encoding='utf-8') as f:
            meta = json.load(f)
    else:
        try:
            meta = Scobility.fetch_catalog_meta(catalog_api, api_base)
            _write_json(CATALOG_CACHE, meta)
        except Exception as e:
            print(f'(catalog metadata unavailable, using default perfect_offset: {e})')
            meta = {}
    offset = Scobility.offset_from_catalog_meta(meta)
    scooby = Scobility.from_raw_spice(raw, offset)

    # Catalog: scraped from the ITL per-chart endpoint only on a cache miss --
    # the chart list is static for the event, so --refresh does NOT re-scrape it
    # (run fetch_catalog.py to rebuild the catalog).
    if os.path.isfile(CHARTS_CACHE):
        with open(CHARTS_CACHE, encoding='utf-8') as f:
            charts = json.load(f)
        charts_src = f'cache ({CHARTS_CACHE})'
    else:
        print('Scraping chart catalog from the ITL API (first run; takes a few minutes)...')
        charts = fetch_catalog.scrape_charts(itl_base, 1, 10000, 100, sleep, 30)
        _write_json(CHARTS_CACHE, charts)
        charts_src = f'API scrape -> cached ({CHARTS_CACHE})'

    # Unlock folders (cached; derived from unlockId on a miss).
    if os.path.isfile(UNLOCK_CACHE) and not refresh:
        with open(UNLOCK_CACHE, encoding='utf-8') as f:
            unlock = {line.strip() for line in f if line.strip()}
    else:
        folders = fetch_catalog.derive_unlock_folders(charts)
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(UNLOCK_CACHE, 'w', encoding='utf-8') as f:
            f.write('\n'.join(folders) + '\n')
        unlock = set(folders)

    lines = [
        'Mode:         api (cached under data/ITL2026)',
        f'Spice:        {spice_src}',
        f'Offset:       perfect_offset {offset:.3f} ({"catalog" if meta else "default"})',
        f'Charts:       {charts_src}',
        f'Unlocks:      derived from unlockId ({len(unlock)} folders, cached)',
    ]
    return scooby, charts, unlock, None, 'api', lines


def index_from_snapshot(snapshot):
    """Name -> [entrant_id, name] from a snapshot's players (no network/cache)."""
    return {
        p['name'].lower(): [p['e_id'], p['name']]
        for p in snapshot.get('players', [])
        if p.get('name') is not None and p.get('e_id') is not None
    }


def _live_scores(args, index):
    """Scrape current GrooveStats scores via a name->id index. (scores, name) or None."""
    entry = index.get(args.username.lower())
    if entry is None:
        return None
    entrant_id, player_name = entry
    return scrape_entrant_scores(entrant_id, args.itl_base), player_name


def _snapshot_scores(args, scooby):
    """Snapshot scores for the player; loads a snapshot if scooby lacks one.

    Returns (scores, player_name) or (None, None).
    """
    if scooby.snapshot is None:
        try:
            path = args.snapshot or find_latest_snapshot(args.snapshot_dir, args.catalog)
        except FileNotFoundError:
            return None, None
        scooby = Scobility.from_snapshot(path)
    try:
        player, scores = scooby.find_player(args.username)
    except KeyError:
        return None, None
    return scores, player['name']


def resolve_scores(args, scooby):
    """Resolve the player's scores per the requested mode, independent of spice.

    snapshot uses the snapshot, api uses a live scrape, and auto prefers the live
    scrape (always newest) and falls back to the snapshot. Returns
    (scores, player_name), or (None, None) on a handled lookup failure.
    """
    if args.itl_json:
        print(f'Scores:       export ({args.itl_json})')
        return scores_from_export(args.itl_json), (args.username or name_from_export(args.itl_json))

    if args.mode == 'snapshot':
        scores, name = _snapshot_scores(args, scooby)
        if scores is None:
            print(f'\nNo player named {args.username!r} in the snapshot.', file=sys.stderr)
            return None, None
        print(f'Scores:       snapshot player ({name})')
        return scores, name

    if args.mode == 'api':
        index = entrant_index_api(args.catalog, args.api_base, refresh=args.refresh)
        live = _live_scores(args, index)
        if live is None:
            print(f'\nNo GrooveStats entrant named {args.username!r}.', file=sys.stderr)
            near = suggest_names(args.username, index)
            if near:
                print('Did you mean: ' + ', '.join(near), file=sys.stderr)
            return None, None
        scores, name = live
        print(f'Scores:       live GrooveStats scrape ({name})')
        return scores, name

    # auto: newest player scores win -> live scrape if reachable, else snapshot.
    try:
        index = index_from_snapshot(scooby.snapshot) if scooby.snapshot else \
            entrant_index_api(args.catalog, args.api_base, refresh=args.refresh)
        live = _live_scores(args, index)
        if live is not None:
            scores, name = live
            print(f'Scores:       live GrooveStats scrape ({name}) [newest]')
            return scores, name
    except Exception as e:
        print(f'(live scores unavailable: {e}; using snapshot)', file=sys.stderr)

    scores, name = _snapshot_scores(args, scooby)
    if scores is None:
        print(f'\nCould not resolve scores for {args.username!r} (no live data and no snapshot match).', file=sys.stderr)
        return None, None
    print(f'Scores:       snapshot player ({name})')
    return scores, name


def entrant_index_api(catalog, api_base, refresh=False):
    """Name -> [id, name] from the scobility API players list, cached."""
    if os.path.isfile(INDEX_CACHE) and not refresh:
        with open(INDEX_CACHE, encoding='utf-8') as f:
            return json.load(f)['names']
    index = fetch_player_index(catalog.upper(), api_base)
    write_index_cache(INDEX_CACHE, 'scobility API', index)
    return index
