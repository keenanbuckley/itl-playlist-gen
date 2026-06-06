"""Live GrooveStats/ITL entrant scraping and the name->id index cache.

A profile is named by its GrooveStats entrant name (which may differ from the
in-game profile name). Names resolve to ITL entrant ids elsewhere (the scobility
API player list, in sources.py); this module fetches an entrant's current scores
in one API request and persists the resolved index.
"""

import json
import os
import urllib.request


DEFAULT_ITL_BASE = 'https://itl2026.groovestats.com'


def write_index_cache(cache_path, source, index):
    os.makedirs(os.path.dirname(os.path.abspath(cache_path)), exist_ok=True)
    with open(cache_path, 'w', encoding='utf-8') as f:
        json.dump({'source': source, 'names': index}, f)


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
                'plays': s.get('totalPasses'),
            }
    return scores
