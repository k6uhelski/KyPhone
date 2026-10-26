#!/usr/bin/env python3
"""Generate the lock-screen cats: one ASCII cat for each part of the day, drawn as fixed 1-bit bitmaps.

The lock screen shows a small ASCII cat in its bottom-right corner, and the cat changes with the time of day
(Kyle, 2026-10-26; drafted on the KyPhone Lock Screen Cats design canvas). POSES below is the one source: the hour
each pose starts and its art. The Radxa picks the pose from the hour and sends its number in the LOCK command; the
firmware and the emulator draw the same bitmap, so the cat looks identical on both. A pose is at most 4 lines and
27 characters (it must clear the quote above it).

Font: Courier Prime Bold at 20 px (12 px per character, lines 26 px apart, as the original cat), SIL Open Font
License 1.1 (see spi_bridge/assets/courierprime-NOTICE.txt), cut to black and white and emboldened by one pixel.

Writes two files from that one source:
    spi_bridge/Inkplate_SPI_Peripheral/ui_cats.h   C arrays for the firmware (Adafruit GFX drawBitmap format)
    spi_bridge/cat_poses.py                        the schedule (pose_for) and the bitmaps for kyphone_os.py and simulator.py

    ~/.venvs/kyphone/bin/python spi_bridge/tools/make_cats.py            # regenerate both
    ~/.venvs/kyphone/bin/python spi_bridge/tools/make_cats.py --check    # exit 1 if either is out of date (a test does this)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FONT = os.path.join(ROOT, 'assets', 'CourierPrime-Bold.ttf')
HEADER = os.path.join(ROOT, 'Inkplate_SPI_Peripheral', 'ui_cats.h')
TABLE = os.path.join(ROOT, 'cat_poses.py')

FONT_PX, ADVANCE, LINE_H = 20, 12, 26
INK_BELOW = 230          # an anti-aliased pixel darker than this is ink (with the 1 px bold, the original cat's weight)
MAX_LINES, MAX_CHARS = 4, 27

# (the hour it starts, what it is doing, the art). In order through the day; the last runs to midnight.
POSES = [
    (0, 'asleep', [
        r"   )\._.,--....,'``.",
        r"  /-   _.. \   _\  (`._ ,.",
        r" `._.-(,_..'--(,_..'`-.;.'",
    ]),
    (6, 'stretch', [
        r"   /\_/\ ",
        r"  ( -o- )____________   ,",
        r" ==\_                 \_/",
        '    `""--"-----""--"""\'',
    ]),
    (8, 'breakfast', [
        r"    /\_/\ ",
        r"   ( o.o )",
        r"   /  ^  \      ___",
        r"  (_|___|_)~   \___/",
    ]),
    (10, 'washing', [
        r"    /\_/\  _",
        r"   ( ^.^ )/ )",
        r"    \ - /_/",
        r"   (_)_(_)~~",
    ]),
    (12, 'sun loaf', [
        r"    /\_/\_____",
        r"   ( -.- )    )",
        r"    `-u--u---'~",
    ]),
    (15, 'play', [
        r"   /\_/\ ",
        r"  ( O.O )      _",
        r"   / ) )\___~~(@)",
        r"  (_(_/ (__)",
    ]),
    (18, 'window', [
        r"    /\ /\ ",
        r"   (     )",
        r"   (     )",
        r"  (___ ___)_/",
    ]),
    (21, 'yawn', [
        r"    /\_/\ ",
        r"   ( >O< )",
        r"    \ ~ /",
        r'   (")_(")~',
    ]),
]


def check_poses():
    hours = [h for h, _n, _a in POSES]
    assert hours[0] == 0 and hours == sorted(hours) and len(set(hours)) == len(hours), 'hours must start at 0, rising'
    for _h, name, art in POSES:
        assert 1 <= len(art) <= MAX_LINES, '%s: at most %d lines' % (name, MAX_LINES)
        for line in art:
            assert len(line.rstrip()) <= MAX_CHARS, '%s: %r is wider than %d characters' % (name, line, MAX_CHARS)
            assert all(' ' <= c <= '~' for c in line), '%s: printable ASCII only' % name


def render(art):
    """The art as rows of 0/1 pixels, MAX_CHARS * ADVANCE wide at most, LINE_H per line."""
    os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
    import pygame
    pygame.font.init()
    font = pygame.font.Font(FONT, FONT_PX)
    lines = [l.rstrip() for l in art]
    w = max(len(l) for l in lines) * ADVANCE
    h = len(lines) * LINE_H
    rows = [[0] * w for _ in range(h)]
    for k, line in enumerate(lines):
        for i, ch in enumerate(line):                     # one cell per character, so columns line up exactly
            if ch == ' ':
                continue
            glyph = font.render(ch, True, (0, 0, 0), (255, 255, 255))
            for y in range(min(glyph.get_height(), LINE_H)):
                for x in range(glyph.get_width()):
                    px, py = i * ADVANCE + x, k * LINE_H + y
                    if px < w and glyph.get_at((x, y))[0] < INK_BELOW:
                        rows[py][px] = 1
    # Drawn twice, one pixel apart, as the panel's bold text is: the original cat had this weight.
    return [[1 if row[x] or (x and row[x - 1]) else 0 for x in range(w)] for row in rows]


def pack(rows):
    """Adafruit GFX drawBitmap format: each row padded to whole bytes, most significant bit leftmost."""
    out = bytearray()
    for row in rows:
        for x in range(0, len(row), 8):
            byte = 0
            for b in range(8):
                byte = byte << 1 | (row[x + b] if x + b < len(row) else 0)
            out.append(byte)
    return bytes(out)


def build():
    check_poses()
    poses = []
    for hour, name, art in POSES:
        rows = render(art)
        poses.append((hour, name, art, len(rows[0]), len(rows), pack(rows)))
    note = 'Generated by tools/make_cats.py from Courier Prime Bold (SIL OFL 1.1; see assets/courierprime-NOTICE.txt). Do not edit.'
    h = ['// ' + note,
         '// The lock-screen cats, one per part of the day (the Radxa sends the pose number in LOCK).',
         '// Adafruit GFX drawBitmap format.',
         '#pragma once', '',
         '#define UI_CAT_POSES %d' % len(poses), '']
    for n, (hour, name, art, w, ht, data) in enumerate(poses):
        h.append('// %d: %s, from %02d:00' % (n, name, hour))
        h += ['//   ' + l.rstrip() for l in art]
        h.append('static const uint8_t ui_cat_%d[] PROGMEM = {' % n)
        for i in range(0, len(data), 16):
            h.append('    ' + ', '.join('0x%02X' % b for b in data[i:i + 16]) + ',')
        h.append('};')
    h += ['', 'static const uint8_t* const ui_cat_bitmaps[UI_CAT_POSES] = {%s};'
          % ', '.join('ui_cat_%d' % n for n in range(len(poses))),
          'static const uint16_t ui_cat_w[UI_CAT_POSES] = {%s};' % ', '.join(str(p[3]) for p in poses),
          'static const uint16_t ui_cat_h[UI_CAT_POSES] = {%s};' % ', '.join(str(p[4]) for p in poses), '']
    t = ['# ' + note,
         '# POSES: (the hour each pose starts, its name, its width and height in pixels, its bitmap: rows padded to whole',
         '# bytes, most significant bit leftmost). pose_for(hour) is the pose number for that hour.', '',
         'POSES = [']
    for hour, name, _art, w, ht, data in poses:
        t.append('    (%d, %r, %d, %d, bytes.fromhex(%r)),' % (hour, name, w, ht, data.hex()))
    t += [']', '', '',
          'def pose_for(hour):',
          '    """The pose for an hour of the day (0-23): the last one starting at or before it."""',
          '    return max(n for n, p in enumerate(POSES) if p[0] <= hour)', '']
    return {HEADER: '\n'.join(h), TABLE: '\n'.join(t)}


def main(argv):
    files = build()
    if '--check' in argv:
        stale = []
        for path, text in files.items():
            try:
                with open(path) as f:
                    if f.read() != text:
                        stale.append(path)
            except FileNotFoundError:
                stale.append(path)
        for path in stale:
            print('%s is out of date: run tools/make_cats.py' % os.path.relpath(path))
        return 1 if stale else 0
    for path, text in files.items():
        with open(path, 'w') as f:
            f.write(text)
        print('wrote %s' % os.path.relpath(path))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
