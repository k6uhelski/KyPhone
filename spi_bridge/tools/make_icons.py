#!/usr/bin/env python3
"""Generate the home-menu icon bitmaps from their SVG paths (no imaging library needed).

The five icons are pixelarticons (MIT, (c) Gerrit Halfmann; see spi_bridge/assets/pixelarticons-LICENSE.txt),
taken verbatim from the OS 0.2.1 design prototype. They are 24x24 pixel art drawn with `crispEdges` at 56x56,
so each 56x56 target pixel is on if its CENTRE falls inside the path — exactly what a browser does, which
makes the result match the designer's captures. Every path here is made of axis-aligned rectangles, so a
small non-zero-winding rasteriser is all that is needed.

Writes two files from this one source:
    spi_bridge/Inkplate_SPI_Peripheral/ui_icons.h   C arrays for the firmware (Adafruit GFX drawBitmap format)
    spi_bridge/home_icons.py                        the same bitmaps for simulator.py

    python3 spi_bridge/tools/make_icons.py            # regenerate both
    python3 spi_bridge/tools/make_icons.py --check    # exit 1 if either file is out of date (a test does this)
"""
import os
import re
import sys

SIZE = 56          # pixels on the panel
GRID = 24          # the icon's own pixel grid

# name -> (label on the menu, SVG path data)
ICONS = {
    'TEXT':     "M20 2H4v2h16zm0 14H6v2h14zm2-12h-2v12h2zM4 4H2v18h2zm2 14H4v2h2zm0-6h4v2H6zm0-4h8v2H6z",
    'CALL':     "M4 1h5v2H4zm5 2h2v4H9zM7 7h2v4H7zm-3 5h2v2H4zM2 3h2v9H2zm7 8h2v2H9zm2 2h2v2h-2zm2 2h4v2h-4zm4-2h4v2h-4zm4 2h2v5h-2zM6 14h2v2H6zm2 2h2v2H8zm2 2h2v2h-2zm2 2h9v2h-9z",
    'CONTACTS': "M2 2h20v2H2zM0 4h2v16H0zm22 0h2v16h-2zM2 20h20v2H2zM14 7h6v2h-6zm0 4h6v2h-6zm0 4h4v2h-4zM6 7h4v4H6zm0 6h4v2H6zm4 2h2v2h-2zm-6 0h2v2H4z",
    'READ':     "M2 3h9v2H2zM0 19h11v2H0zM13 3h9v2h-9zm0 16h11v2H13zM11 5h2v18h-2zM0 5h2v14H0zm22 0h2v14h-2zm-7 2h5v2h-5zm0 4h5v2h-5zm0 4h2v2h-2z",
    'LISTEN':   "M4 12h4v2H4zm-2 2h2v4H2zm2 4h4v2H4zM8 6h2v12H8zm10 0h2v12h-2zm-6 8h2v4h-2zm2-2h4v2h-4zm0 6h4v2h-4zM10 4h8v2h-8z",
}
ORDER = ['TEXT', 'CALL', 'READ', 'LISTEN', 'CONTACTS']      # the home menu order

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_H = os.path.join(HERE, '..', 'Inkplate_SPI_Peripheral', 'ui_icons.h')
OUT_PY = os.path.join(HERE, '..', 'home_icons.py')


def parse_path(d):
    """SVG path data (M m H h V v L l Z z) -> list of closed polygons, each a list of (x, y)."""
    tokens = re.findall(r'[MmHhVvLlZz]|-?\d*\.?\d+', d)
    polys, poly, cur, start, i, cmd = [], [], (0.0, 0.0), (0.0, 0.0), 0, None

    def num():
        nonlocal i
        v = float(tokens[i]); i += 1
        return v

    while i < len(tokens):
        if re.match(r'[A-Za-z]', tokens[i]):
            cmd = tokens[i]; i += 1
            if cmd in 'Zz':
                if poly:
                    polys.append(poly)
                poly, cur = [], start
                continue
        rel = cmd.islower()
        if cmd in 'Mm':
            if poly:
                polys.append(poly)
            x, y = num(), num()
            cur = (cur[0] + x, cur[1] + y) if rel else (x, y)
            start, poly = cur, [cur]
            cmd = 'l' if rel else 'L'                 # further pairs after a move are line-tos
        elif cmd in 'Ll':
            x, y = num(), num()
            cur = (cur[0] + x, cur[1] + y) if rel else (x, y)
            poly.append(cur)
        elif cmd in 'Hh':
            x = num()
            cur = (cur[0] + x if rel else x, cur[1]); poly.append(cur)
        elif cmd in 'Vv':
            y = num()
            cur = (cur[0], cur[1] + y if rel else y); poly.append(cur)
        else:
            raise ValueError('unsupported path command %r' % cmd)
    if poly:
        polys.append(poly)
    return polys


def inside(polys, px, py):
    """Non-zero winding rule: is the point inside the union of the polygons?"""
    wn = 0
    for poly in polys:
        n = len(poly)
        for k in range(n):
            x0, y0 = poly[k]
            x1, y1 = poly[(k + 1) % n]
            if y0 <= py < y1:                          # upward crossing
                if (x1 - x0) * (py - y0) - (px - x0) * (y1 - y0) > 0:
                    wn += 1
            elif y1 <= py < y0:                        # downward crossing
                if (x1 - x0) * (py - y0) - (px - x0) * (y1 - y0) < 0:
                    wn -= 1
    return wn != 0


def rasterise(d, size=SIZE):
    """-> list of `size` rows, each an int whose bit (size-1-x) is pixel x (1 = ink)."""
    polys = parse_path(d)
    rows = []
    for py in range(size):
        row = 0
        for px in range(size):
            if inside(polys, (px + 0.5) * GRID / size, (py + 0.5) * GRID / size):
                row |= 1 << (size - 1 - px)
        rows.append(row)
    return rows


def to_bytes(rows, size=SIZE):
    """GFX drawBitmap layout: row-major, each row padded to whole bytes, most significant bit first."""
    nbytes = (size + 7) // 8
    out = []
    for row in rows:
        row <<= nbytes * 8 - size
        out.extend((row >> (8 * (nbytes - 1 - b))) & 0xFF for b in range(nbytes))
    return out


def render_header():
    lines = [
        '// ui_icons.h — home-menu icons as 56x56 1-bit bitmaps (Adafruit GFX drawBitmap format).',
        '//',
        '// GENERATED by spi_bridge/tools/make_icons.py — do not edit; change the script and re-run it.',
        '// Artwork: pixelarticons, MIT, (c) Gerrit Halfmann (spi_bridge/assets/pixelarticons-LICENSE.txt).',
        '// A set bit is ink. Rows are 7 bytes; bit 7 of byte 0 is the left-most pixel.',
        '',
        '#ifndef KYPHONE_UI_ICONS_H',
        '#define KYPHONE_UI_ICONS_H',
        '',
        '#include <stdint.h>',
        '#ifndef PROGMEM',
        '#define PROGMEM',
        '#endif',
        '',
        '#define UI_ICON_SIZE 56',
        '',
    ]
    for name in ORDER:
        data = to_bytes(rasterise(ICONS[name]))
        lines.append('static const uint8_t ui_icon_%s[%d] PROGMEM = {' % (name.lower(), len(data)))
        for k in range(0, len(data), 14):
            lines.append('    ' + ', '.join('0x%02X' % b for b in data[k:k + 14]) + ',')
        lines.append('};')
        lines.append('')
    lines.append('// In home-menu order (HOME_MENU in kyphone_os.py / labels[] in ui_screens.h).')
    lines.append('static const uint8_t* const ui_icons[5] = { ' + ', '.join('ui_icon_' + n.lower() for n in ORDER) + ' };')
    lines.append('')
    lines.append('#endif  // KYPHONE_UI_ICONS_H')
    return '\n'.join(lines) + '\n'


def render_python():
    lines = [
        '"""Home-menu icons as 56x56 bitmaps for simulator.py.',
        '',
        'GENERATED by spi_bridge/tools/make_icons.py — do not edit; change the script and re-run it.',
        'Artwork: pixelarticons, MIT, (c) Gerrit Halfmann (spi_bridge/assets/pixelarticons-LICENSE.txt).',
        'ICONS[name] is 56 ints, one per row; bit (55 - x) of a row is pixel x (1 = ink).',
        '"""',
        '',
        'ICON_SIZE = %d' % SIZE,
        'MENU_ORDER = %r' % (ORDER,),
        '',
        'ICONS = {',
    ]
    for name in ORDER:
        lines.append('    %r: [' % name)
        rows = rasterise(ICONS[name])
        for k in range(0, len(rows), 3):
            lines.append('        ' + ', '.join('0x%014X' % r for r in rows[k:k + 3]) + ',')
        lines.append('    ],')
    lines.append('}')
    return '\n'.join(lines) + '\n'


def main(argv):
    header, python = render_header(), render_python()
    if '--check' in argv:
        stale = [p for p, text in ((OUT_H, header), (OUT_PY, python))
                 if not os.path.exists(p) or open(p).read() != text]
        if stale:
            print('out of date (run make_icons.py): ' + ', '.join(os.path.relpath(p) for p in stale))
            return 1
        print('icons up to date')
        return 0
    for path, text in ((OUT_H, header), (OUT_PY, python)):
        with open(path, 'w') as f:
            f.write(text)
        print('wrote', os.path.relpath(path))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
