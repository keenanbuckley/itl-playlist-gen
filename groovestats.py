"""Resolve a GrooveStats/ITL entrant by name and scrape their current scores.

The ITL2026.json export carries no identifier, so a profile is named by its
GrooveStats entrant name (which may differ from the in-game profile name). Names
resolve to ITL entrant ids via a local index built from the scraped entrant_info
directory; the index is cached so the ~2000 files are only read once. The
entrant's *current* scores then come from one live API request.
"""

import glob
import json
import os
import urllib.request


DEFAULT_ITL_BASE = 'https://itl2026.groovestats.com'


def find_latest_entrant_info(scratch_dir, catalog='itl2026'):
    pattern = os.path.join(scratch_dir, f'{catalog}_data', '*', 'entrant_info')
    candidates = sorted(d for d in glob.glob(pattern) if os.path.isdir(d))
    if not candidates:
        raise FileNotFoundError(
            f'no {catalog}_data/*/entrant_info found under {scratch_dir} (set --entrant-info)'
        )
    return candidates[-1]


def _entrant_of(file_data):
    return file_data.get('entrant') or file_data.get('data', {}).get('entrant')


def build_entrant_index(entrant_info_dir, cache_path=None, rebuild=False):
    """Return {lowercased name: [entrant_id, canonical name]} for a scrape dir.

    Cached to cache_path; rebuilt when missing, stale, or rebuild=True.
    """
    if cache_path and not rebuild and os.path.isfile(cache_path):
        if os.path.getmtime(cache_path) >= os.path.getmtime(entrant_info_dir):
            with open(cache_path, encoding='utf-8') as f:
                cached = json.load(f)
            if cached.get('source') == os.path.abspath(entrant_info_dir):
                return cached['names']

    index = {}
    for f in glob.glob(os.path.join(entrant_info_dir, '*.json')):
        with open(f, encoding='utf-8') as fp:
            ent = _entrant_of(json.load(fp))
        if ent and ent.get('name') is not None:
            index[ent['name'].lower()] = [ent['id'], ent['name']]

    if cache_path:
        with open(cache_path, 'w', encoding='utf-8') as fp:
            json.dump({'source': os.path.abspath(entrant_info_dir), 'names': index}, fp)
    return index


def suggest_names(query, index, limit=10):
    q = query.lower()
    return sorted(name for lower, (_id, name) in index.items() if q in lower)[:limit]


def scrape_entrant_scores(entrant_id, base_url=DEFAULT_ITL_BASE, timeout=30):
    """Fetch an entrant's current top scores as hash -> {value, clear, last_played}."""
    url = f'{base_url}/api/entrant/{entrant_id}'
    with urllib.request.urlopen(url, timeout=timeout) as r:
        payload = json.load(r)
    data = payload.get('data', payload)

    scores = {}
    for s in data.get('topScores', []):
        if s.get('clearType', 0) > 0:
            scores[s['chartHash']] = {
                'value': 1.0 - s['ex'] / 10000.0,    # ex is EX% x100; value is diff-from-perfect
                'clear': s['clearType'],
                'last_played': s.get('lastImproved') or s.get('dateAdded'),
            }
    return scores
