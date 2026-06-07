#!/usr/bin/env python3
"""Render a spice-vs-score-quality SVG (the scobility plot) for one player.

Same source/score options as generate_playlist.py. Each played chart is plotted
at (chart spice, score quality); the two-segment horizon fit is drawn over it,
points are sized by pass count and tinted by recency, and any charts rejected
from the fit by --spice-iqr are shown as hollow rings.

    python spice_plot.py "HFocus77"
    python spice_plot.py "HFocus77" --mode api --spice-iqr 4.0
"""

import argparse
import datetime
import math
import os
import shutil
import subprocess
import sys

import sources
from itldata import ITLData
from scobility import DEFAULT_API_BASE
from groovestats import DEFAULT_ITL_BASE

W, H = 960, 640
ML, MR, MT, MB = 72, 30, 78, 62
PW, PH = W - ML - MR, H - MT - MB


def _esc(s):
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def svg_to_png(svg_path, png_path, width=1920):
    """Render an SVG to PNG via whatever renderer is on the system. Returns the
    method used, or None if no renderer is available."""
    if shutil.which('rsvg-convert'):
        try:
            subprocess.run(['rsvg-convert', svg_path, '-w', str(width), '-o', png_path],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return 'rsvg-convert'
        except Exception:
            pass
    if shutil.which('inkscape'):
        try:
            subprocess.run(['inkscape', svg_path, '--export-type=png',
                            '--export-width', str(width), '--export-filename', png_path],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return 'inkscape'
        except Exception:
            pass
    try:
        import cairosvg
        cairosvg.svg2png(url=svg_path, write_to=png_path, output_width=width)
        return 'cairosvg'
    except Exception:
        return None


def _date_ordinal(date_str):
    if not date_str:
        return None
    try:
        return datetime.date.fromisoformat(date_str[:10]).toordinal()
    except ValueError:
        return None


def _recency_color(t):
    # t in [0,1]: 0 = oldest (cool blue), 1 = newest (warm orange).
    if t is None:
        return '#adb5bd'
    cold, warm = (59, 91, 219), (232, 89, 12)
    r, g, b = (round(c + (w - c) * t) for c, w in zip(cold, warm))
    return f'#{r:02x}{g:02x}{b:02x}'


def _ticks(lo, hi, step):
    t, out = math.ceil(lo / step) * step, []
    while t <= hi + 1e-9:
        out.append(round(t, 4))
        t += step
    return out


def render_svg(played, rejected_hashes, fit, title, subtitle, show_horizon=True):
    xs = [p[0] for p in played]
    ys = [p[1] for p in played]

    def fit_y(x):
        m = fit['mildSlope'] if x <= fit['horizonSpice'] else fit['hotSlope']
        return m * (x - fit['horizonSpice']) + fit['horizonQuality']

    xmin, xmax = min(xs + [0.0]), max(xs)
    xmax += (xmax - xmin) * 0.03 or 0.1
    fit_ys = [fit_y(xmin), fit_y(xmax), fit['horizonQuality']]
    ymin, ymax = min(ys + fit_ys), max(ys + fit_ys)
    pad = (ymax - ymin) * 0.08 or 0.5
    ymin, ymax = ymin - pad, ymax + pad

    def sx(x):
        return ML + (x - xmin) / (xmax - xmin) * PW

    def sy(y):
        return MT + (ymax - y) / (ymax - ymin) * PH

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
           f'viewBox="0 0 {W} {H}" font-family="sans-serif">']
    out.append(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')
    out.append(f'<rect x="{ML}" y="{MT}" width="{PW}" height="{PH}" fill="#f8f9fa" stroke="#ced4da"/>')

    # gridlines + ticks
    for gx in _ticks(xmin, xmax, 0.5):
        x = sx(gx)
        out.append(f'<line x1="{x:.1f}" y1="{MT}" x2="{x:.1f}" y2="{MT + PH}" stroke="#e9ecef"/>')
        out.append(f'<text x="{x:.1f}" y="{MT + PH + 18}" font-size="12" fill="#495057" text-anchor="middle">{gx:g}</text>')
    for gy in _ticks(ymin, ymax, (ymax - ymin) / 5):
        y = sy(gy)
        out.append(f'<line x1="{ML}" y1="{y:.1f}" x2="{ML + PW}" y2="{y:.1f}" stroke="#e9ecef"/>')
        out.append(f'<text x="{ML - 8}" y="{y + 4:.1f}" font-size="12" fill="#495057" text-anchor="end">{gy:.2f}</text>')

    # horizon marker
    hx = sx(fit['horizonSpice'])
    if show_horizon and ML <= hx <= ML + PW:
        out.append(f'<line x1="{hx:.1f}" y1="{MT}" x2="{hx:.1f}" y2="{MT + PH}" stroke="#868e96" stroke-dasharray="4 4"/>')
        out.append(f'<text x="{hx + 4:.1f}" y="{MT + 14}" font-size="11" fill="#868e96">horizon {fit["horizonSpice"]:.2f}</text>')

    # fit line (two segments, bend at the horizon)
    pts = [(xmin, fit_y(xmin)), (fit['horizonSpice'], fit_y(fit['horizonSpice'])), (xmax, fit_y(xmax))]
    poly = ' '.join(f'{sx(x):.1f},{sy(y):.1f}' for x, y in pts if xmin <= x <= xmax)
    out.append(f'<polyline points="{poly}" fill="none" stroke="#1c7ed6" stroke-width="2.5"/>')

    # points: size by plays, tint by recency; rejected charts hollow.
    ords = [_date_ordinal(s.date) for _, _, s in played]
    real = [o for o in ords if o is not None]
    omin, omax = (min(real), max(real)) if real else (0, 1)
    for (spice, quality, s) in played:
        o = _date_ordinal(s.date)
        t = None if o is None else (o - omin) / (omax - omin or 1)
        r = 3 + 1.7 * math.sqrt(s.plays or 1)
        cx, cy = sx(spice), sy(quality)
        if s.hsh in rejected_hashes:
            out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="none" stroke="#e03131" stroke-width="2"/>')
        else:
            out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{_recency_color(t)}" fill-opacity="0.72" stroke="#ffffff" stroke-width="0.6"/>')

    # axis titles + heading
    out.append(f'<text x="{ML + PW / 2:.0f}" y="{H - 16}" font-size="14" fill="#212529" text-anchor="middle">Chart spice (log2)</text>')
    out.append(f'<text x="18" y="{MT + PH / 2:.0f}" font-size="14" fill="#212529" text-anchor="middle" transform="rotate(-90 18 {MT + PH / 2:.0f})">Score quality</text>')
    out.append(f'<text x="{ML}" y="30" font-size="20" font-weight="bold" fill="#212529">{_esc(title)}</text>')
    out.append(f'<text x="{ML}" y="52" font-size="13" fill="#495057">{_esc(subtitle)}</text>')
    out.append('</svg>')
    return '\n'.join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('username', nargs='?', help='player name (defaults to the export filename with --itl-json)')
    parser.add_argument('--mode', choices=['auto', 'snapshot', 'api'], default='auto', help='source (default: auto)')
    parser.add_argument('--itl-json', help='read scores from an ITL2026.json export instead')
    parser.add_argument('--refresh', action='store_true', help='api mode: re-fetch cached spice/index')
    parser.add_argument('--fit', choices=['adaptive', 'horizon'], default='adaptive', help='spice fit (default: adaptive)')
    parser.add_argument('--adaptive-n', type=int, default=40, help='adaptive fit: horizon at/above this many charts, else flat-shrunk (default: 40)')
    parser.add_argument('--spice-iqr', type=float, metavar='K', help='reject spice outliers beyond Q1-K*IQR / Q3+K*IQR from the fit')
    parser.add_argument('--snapshot', help='snapshot mode: path to a snapshot JSON')
    parser.add_argument('--snapshot-dir', default=sources.DEFAULT_SNAPSHOT_DIR, help='snapshot/charts search dir')
    parser.add_argument('--charts', help='snapshot mode: explicit charts.json path')
    parser.add_argument('--catalog', default='itl2026', help='catalog prefix (default: itl2026)')
    parser.add_argument('--api-base', default=DEFAULT_API_BASE, help=f'scobility API base URL (default: {DEFAULT_API_BASE})')
    parser.add_argument('--itl-base', default=DEFAULT_ITL_BASE, help=f'ITL GrooveStats API base URL (default: {DEFAULT_ITL_BASE})')
    parser.add_argument('-o', '--output', help='output SVG path (default: plots/ITL - <username>.svg)')
    args = parser.parse_args(argv)

    if not args.itl_json and not args.username:
        parser.error('a username is required (or pass --itl-json)')

    try:
        scooby, charts, unlock_folders, _snapshot, _mode, src_lines = sources.resolve_catalog(
            args.mode, snapshot_dir=args.snapshot_dir, catalog=args.catalog,
            api_base=args.api_base, itl_base=args.itl_base,
            snapshot_path=args.snapshot, charts_path=args.charts, refresh=args.refresh,
        )
    except FileNotFoundError as e:
        parser.error(str(e))
    for line in src_lines:
        print(line)

    scores, player_name = sources.resolve_scores(args, scooby)
    if scores is None:
        return 1

    data = ITLData(charts, unlock_folders, scores)
    try:
        scooby.processPlayer(player_name, data, spice_iqr_mult=args.spice_iqr,
                             fit=args.fit, adaptive_n=args.adaptive_n)
    except ValueError as e:
        print(f'\nCould not compute the fit: {e}', file=sys.stderr)
        return 1

    played = [(s.spice, s.quality, s) for s in data.hashes.values() if s.quality is not None]
    rejected = {s.hsh for s in data.rejected_outliers}
    fit = {k: getattr(data, k) for k in ('horizonSpice', 'horizonQuality', 'mildSlope', 'hotSlope')}
    is_horizon = data.fit_used == 'horizon'
    title = f'Scobility - {player_name}'
    shape = (f'horizon {data.horizonSpice:.2f} | mild {data.mildSlope:.2f} / hot {data.hotSlope:.2f}'
             if is_horizon else f'{data.fit_used} | slope {data.mildSlope:.2f}')
    subtitle = (f'{len(played)} played | timing power {data.timingPower:.2f} | ' + shape
                + (f' | {len(rejected)} outlier(s) excluded' if rejected else ''))

    svg = render_svg(played, rejected, fit, title, subtitle, show_horizon=is_horizon)
    output = args.output or os.path.join(os.path.dirname(__file__), 'plots', f'ITL - {player_name}.svg')
    os.makedirs(os.path.dirname(os.path.abspath(output)), exist_ok=True)
    with open(output, 'w', encoding='utf-8') as f:
        f.write(svg)
    print(f'\nPlotted {len(played)} charts to {output}')

    png = os.path.splitext(output)[0] + '.png'
    method = svg_to_png(output, png)
    if method:
        print(f'Rendered PNG to {png} (via {method})')
    else:
        print('No SVG->PNG renderer found (install rsvg-convert, inkscape, or cairosvg); wrote SVG only.',
              file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
