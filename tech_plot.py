#!/usr/bin/env python3
"""Render a player's tech profile as two SVGs (+PNGs):

  tech bars  - EX%-impact per reliable tech (how many EX points a heavy chart of
               that tech is worth to you, beyond spice), with error bars.
  tech radar - the same techs as percentiles vs the field (50 = average player).

Same source/score options as generate_playlist.py.

    python tech_plot.py "HFocus77"
    python tech_plot.py "HFocus77" --mode api
"""

import argparse
import math
import os
import statistics
import sys

import sources
import tech
from scobility import Scobility, DEFAULT_API_BASE
from groovestats import DEFAULT_ITL_BASE
from spice_plot import svg_to_png

W, H = 760, 520


def _esc(s):
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _color(v):
    return '#2f9e44' if v >= 0 else '#e03131'   # green strength / red weakness


def render_bars(title, rows):
    """rows: [(label, ex_impact, ex_se, percentile_or_None)] sorted by impact."""
    ml, mr, mt, mb = 110, 90, 60, 30
    pw, ph = W - ml - mr, H - mt - mb
    mx = max((abs(r[1]) + r[2] for r in rows), default=1.0) or 1.0
    cx = ml + pw / 2

    def sx(v):
        return cx + (v / mx) * (pw / 2)

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="sans-serif">']
    out.append(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')
    out.append(f'<text x="{ml}" y="32" font-size="19" font-weight="bold" fill="#212529">{_esc(title)}</text>')
    out.append(f'<text x="{ml}" y="50" font-size="12" fill="#868e96">EX-point swing on a chart heavy in this tech, vs spice</text>')
    out.append(f'<line x1="{cx:.1f}" y1="{mt}" x2="{cx:.1f}" y2="{mt + ph}" stroke="#adb5bd"/>')
    n = len(rows)
    band = ph / n
    for i, (label, val, se, pct) in enumerate(rows):
        y = mt + band * (i + 0.5)
        x = sx(val)
        x0 = sx(0)
        out.append(f'<rect x="{min(x, x0):.1f}" y="{y - 11:.1f}" width="{abs(x - x0):.1f}" height="22" '
                   f'fill="{_color(val)}" fill-opacity="0.78"/>')
        lo, hi = sx(val - se), sx(val + se)
        out.append(f'<line x1="{lo:.1f}" y1="{y:.1f}" x2="{hi:.1f}" y2="{y:.1f}" stroke="#343a40" stroke-width="1.4"/>')
        out.append(f'<text x="{ml - 8}" y="{y + 4:.1f}" font-size="13" fill="#212529" text-anchor="end">{_esc(label)}</text>')
        out.append(f'<text x="{ml + pw + 10}" y="{y + 4:.1f}" font-size="12" fill="{_color(val)}" text-anchor="start">{val:+.1f} EX</text>')
    out.append('</svg>')
    return '\n'.join(out)


def render_radar(title, rows):
    """rows: [(label, percentile)]."""
    cx, cy, R = W / 2, H / 2 + 10, 165
    n = len(rows)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="sans-serif">']
    out.append(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')
    out.append(f'<text x="{W / 2:.0f}" y="30" font-size="19" font-weight="bold" fill="#212529" text-anchor="middle">{_esc(title)}</text>')
    out.append(f'<text x="{W / 2:.0f}" y="48" font-size="12" fill="#868e96" text-anchor="middle">percentile vs the field (50 = average)</text>')

    def pt(i, frac):
        ang = math.radians(-90 + i * 360 / n)
        return cx + R * frac * math.cos(ang), cy + R * frac * math.sin(ang)

    for g in (0.25, 0.5, 0.75, 1.0):
        poly = ' '.join(f'{x:.1f},{y:.1f}' for x, y in (pt(i, g) for i in range(n)))
        col = '#ced4da' if g == 0.5 else '#e9ecef'
        out.append(f'<polygon points="{poly}" fill="none" stroke="{col}"/>')
    for i, (label, pct) in enumerate(rows):
        ex, ey = pt(i, 1.0)
        out.append(f'<line x1="{cx:.1f}" y1="{cy:.1f}" x2="{ex:.1f}" y2="{ey:.1f}" stroke="#e9ecef"/>')
        lx, ly = pt(i, 1.15)
        anchor = 'middle' if abs(lx - cx) < 25 else ('start' if lx > cx else 'end')
        g = tech.grade(pct)
        out.append(f'<text x="{lx:.1f}" y="{ly:.1f}" font-size="13" fill="#212529" text-anchor="{anchor}">{_esc(label)}</text>')
        out.append(f'<text x="{lx:.1f}" y="{ly + 17:.1f}" font-size="13" font-weight="bold" fill="#212529" text-anchor="{anchor}">{g} · {pct:.0f}</text>')
    poly = ' '.join(f'{x:.1f},{y:.1f}' for x, y in (pt(i, max(0.02, rows[i][1] / 100)) for i in range(n)))
    out.append(f'<polygon points="{poly}" fill="#1c7ed6" fill-opacity="0.30" stroke="#1c7ed6" stroke-width="2"/>')
    out.append('</svg>')
    return '\n'.join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('username', nargs='?', help='player name (or export filename with --itl-json)')
    parser.add_argument('--mode', choices=['auto', 'snapshot', 'api'], default='auto')
    parser.add_argument('--itl-json', help='read scores from an ITL2026.json export')
    parser.add_argument('--refresh', action='store_true', help='api mode: re-fetch cached spice/index')
    parser.add_argument('--snapshot')
    parser.add_argument('--snapshot-dir', default=sources.DEFAULT_SNAPSHOT_DIR)
    parser.add_argument('--charts')
    parser.add_argument('--catalog', default='itl2026')
    parser.add_argument('--api-base', default=DEFAULT_API_BASE)
    parser.add_argument('--itl-base', default=DEFAULT_ITL_BASE)
    parser.add_argument('--out-dir', help='output dir (default: plots/)')
    args = parser.parse_args(argv)
    if not args.itl_json and not args.username:
        parser.error('a username is required (or pass --itl-json)')

    try:
        scooby, charts, _unlock, snapshot, _mode, src_lines = sources.resolve_catalog(
            args.mode, snapshot_dir=args.snapshot_dir, catalog=args.catalog,
            api_base=args.api_base, itl_base=args.itl_base,
            snapshot_path=args.snapshot, charts_path=args.charts, refresh=args.refresh)
    except FileNotFoundError as e:
        parser.error(str(e))
    for line in src_lines:
        print(line)

    scores, player_name = sources.resolve_scores(args, scooby)
    if scores is None:
        return 1

    mean, std, hi = tech.feature_stats(charts)
    feat_by_hash = tech.feature_vectors(charts, mean, std)
    pairs = tech.pairs_from_scores(scores, scooby.spice)
    if len(pairs) < 20:
        print(f'\nOnly {len(pairs)} scored charts; a tech profile needs more data.', file=sys.stderr)
        return 1
    if len(pairs) < 60:
        print(f'(only {len(pairs)} charts; profile is low-confidence)', file=sys.stderr)

    fit, gamma, se, n = tech.player_profile(pairs, feat_by_hash)
    spice_ref = statistics.median([s for s, _q, _h in pairs])
    z_load = {f: (hi[f] - mean[f]) / std[f] for f in tech.FEATURES}

    # Percentile cohort: snapshot mode rebuilds fresh from the snapshot (its
    # spice and scores match the target); api mode uses the bundled cache, which
    # is stamped with the spice it was built against (see build_tech_population.py).
    if snapshot is not None:
        population = tech.build_population(snapshot, feat_by_hash)
        print(f'Cohort:       snapshot ({len(population["staminaLevel"])} players)')
    else:
        population, meta = tech.load_bundled_population()
        if population:
            print(f'Cohort:       bundled, spice {(meta.get("spice_calc_time") or "?")[:10]} ({meta.get("cohort")} players)')
            try:
                _raw, live_sct = Scobility.fetch_api_chart_all(args.catalog.upper(), args.api_base)
                if meta.get('spice_calc_time') and live_sct and meta['spice_calc_time'][:10] != live_sct[:10]:
                    print(f'WARNING: cohort spice basis {meta["spice_calc_time"][:10]} != live {live_sct[:10]}; '
                          f'rerun build_tech_population.py to resync', file=sys.stderr)
            except Exception:
                pass

    print(f'Player:       {player_name} - {n} charts')
    sn = tech.snr(gamma, se)   # percentiles are on the t-statistic, not raw gamma
    bar_rows = []
    for feat, label in tech.DISPLAY:
        imp = tech.ex_impact(gamma[feat], fit, spice_ref, z_load[feat])
        imp_se = abs(tech.ex_impact(gamma[feat] + se[feat], fit, spice_ref, z_load[feat]) - imp)
        pct = tech.percentile(sn[feat], population[feat]) if population and feat in population else None
        bar_rows.append((label, imp, imp_se, pct))
    bar_rows.sort(key=lambda r: r[1], reverse=True)

    out_dir = args.out_dir or os.path.join(os.path.dirname(__file__), 'plots')
    os.makedirs(out_dir, exist_ok=True)

    bars_svg = render_bars(f'Tech profile - {player_name}', bar_rows)
    bpath = os.path.join(out_dir, f'ITL - tech bars - {player_name}.svg')
    open(bpath, 'w', encoding='utf-8').write(bars_svg)
    svg_to_png(bpath, os.path.splitext(bpath)[0] + '.png')
    print(f'Wrote {bpath} (+png)')

    if population:
        radar_rows = [(label, tech.percentile(sn[feat], population[feat]) or 0.0) for feat, label in tech.DISPLAY]
        rsvg = render_radar(f'Tech profile - {player_name}', radar_rows)
        rpath = os.path.join(out_dir, f'ITL - tech radar - {player_name}.svg')
        open(rpath, 'w', encoding='utf-8').write(rsvg)
        svg_to_png(rpath, os.path.splitext(rpath)[0] + '.png')
        print(f'Wrote {rpath} (+png)')
    else:
        print('(no population cohort available; skipped radar. Run build_tech_population.py.)', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
