#!/usr/bin/env python3
"""Draws the README comparison. Edit ROWS, then run: python3 docs/comparison.py
Marks: 2 = yes, 1 = partly, 0 = not found. Keep them to what the project's code or README shows."""
import os
HERE = os.path.dirname(os.path.abspath(__file__))
GROUPS = [('Fusion', ['Live\nFusion', 'Checked\nSTL export']),
          ('Bambu project', ['Multi-plate\n.3mf build', 'Keeps hand\nedits', 'Support\nblockers', 'Guards an\nopen file']),
          ('Beyond', ['Printer\ncontrol', 'Modelling\nwithout CAD', 'Finds existing\nmodels']),
          ('Install', ['Claude Code\nplugin'])]
ROWS = [('fusion-bambulab', [2, 2, 2, 2, 2, 2, 0, 0, 0, 2]),
        ('bambu-printer-mcp', [1, 1, 1, 0, 0, 0, 2, 1, 0, 0]),
        ('bambu-studio-ai', [0, 0, 1, 0, 0, 0, 2, 2, 2, 0]),
        ('3dprint-scout', [1, 1, 1, 0, 0, 0, 0, 2, 2, 0]),
        ('fusion360-bambu-print-skill', [1, 1, 0, 0, 0, 0, 0, 0, 0, 0]),
        ('bambu-3mf', [0, 0, 2, 0, 0, 0, 0, 0, 0, 2]),
        ('bambu-studio-model-builder', [0, 0, 1, 1, 0, 1, 0, 1, 0, 0])]
THEMES = {'light': dict(bg='#ffffff', fg='#1f2933', muted='#66737d', line='#dfe4e8', us='#fdf0e4', yes='#1f7a5c', part='#b98a12', no='#c6ced3', accent='#c2560f'),
          'dark': dict(bg='#0d1117', fg='#e6edf3', muted='#8d99a5', line='#2a323b', us='#2d2017', yes='#4fc29a', part='#e0b64a', no='#46515a', accent='#f0935a')}
NAME_W, COL_W, GAP, TOP, ROW_H, PAD = 232, 67, 14, 92, 42, 16
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"

def mark(x, y, v, c):
    if v == 2: return '<circle cx="%g" cy="%g" r="8" fill="%s"/>' % (x, y, c['yes'])
    if v == 1: return ('<circle cx="%g" cy="%g" r="7" fill="none" stroke="%s" stroke-width="2"/><path d="M%g %g a7 7 0 0 0 0 14 z" fill="%s"/>' % (x, y, c['part'], x, y - 7, c['part']))
    return '<circle cx="%g" cy="%g" r="3" fill="%s"/>' % (x, y, c['no'])

def draw(c):
    xs, groups, x = [], [], PAD + NAME_W
    for name, cols in GROUPS:
        groups.append((name, x, x + COL_W * len(cols)))
        for label in cols: xs.append((x + COL_W / 2, label)); x += COL_W
        x += GAP
    W = x - GAP + PAD; H = TOP + ROW_H * len(ROWS) + 54
    o = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %g %g" width="%g" font-family="%s">' % (W, H, W, FONT),
         '<rect width="%g" height="%g" rx="10" fill="%s"/>' % (W, H, c['bg'])]
    for name, a, b in groups:
        o.append('<text x="%g" y="26" text-anchor="middle" font-size="12.5" font-weight="600" fill="%s">%s</text>' % ((a + b) / 2, c['fg'], name))
        o.append('<line x1="%g" y1="35" x2="%g" y2="35" stroke="%s" stroke-width="2"/>' % (a + 6, b - 6, c['accent']))
    for cx, label in xs:
        for i, part in enumerate(label.split('\n')):
            o.append('<text x="%g" y="%g" text-anchor="middle" font-size="10.5" fill="%s">%s</text>' % (cx, 55 + i * 14, c['muted'], part))
    for r, (name, marks) in enumerate(ROWS):
        y = TOP + r * ROW_H
        if r == 0: o.append('<rect x="%g" y="%g" width="%g" height="%g" rx="7" fill="%s"/>' % (PAD - 6, y, W - 2 * PAD + 12, ROW_H, c['us']))
        elif r > 1: o.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="%s"/>' % (PAD, y, W - PAD, y, c['line']))
        o.append('<text x="%g" y="%g" font-size="14" font-weight="%s" fill="%s">%s</text>' % (PAD + 4, y + ROW_H / 2 + 5, 700 if r == 0 else 500, c['fg'], name))
        for (cx, _), v in zip(xs, marks): o.append(mark(cx, y + ROW_H / 2, v, c))
    y = TOP + ROW_H * len(ROWS) + 30
    o.append(mark(PAD + 12, y - 4, 2, c) + '<text x="%g" y="%g" font-size="12" fill="%s">yes</text>' % (PAD + 26, y, c['muted']))
    o.append(mark(PAD + 72, y - 4, 1, c) + '<text x="%g" y="%g" font-size="12" fill="%s">partly</text>' % (PAD + 86, y, c['muted']))
    o.append(mark(PAD + 142, y - 4, 0, c) + '<text x="%g" y="%g" font-size="12" fill="%s">not found</text>' % (PAD + 152, y, c['muted']))
    o.append('<text x="%g" y="%g" text-anchor="end" font-size="12" fill="%s">From each project\'s code and README, November 2026</text></svg>' % (W - PAD, y, c['muted']))
    return '\n'.join(o) + '\n'

for theme, colours in THEMES.items():
    open(os.path.join(HERE, 'comparison-%s.svg' % theme), 'w', encoding='utf-8').write(draw(colours))
