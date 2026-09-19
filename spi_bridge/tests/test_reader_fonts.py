"""
test_reader_fonts.py — the generated book-font tables (spi_bridge/reader_fonts.py).

Checks the generated file is current, is consistent with itself, and agrees with the FreeSerif headers
the firmware draws with, read a second way (through each glyph row's trailing comment).

    python3 -m pytest spi_bridge/tests/test_reader_fonts.py -v
"""

import os
import re
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
import reader_fonts  # noqa: E402

FONT_DIR = os.path.join(HERE, '..', 'Inkplate_SPI_Peripheral', 'fonts')
POINTS = {'S': 9, 'M': 12, 'L': 18, 'X': 24}
Y_ADVANCE = {'S': 22, 'M': 29, 'L': 42, 'X': 56}          # GFX yAdvance in each header


class GeneratedFile(unittest.TestCase):
    def test_it_is_up_to_date(self):
        out = subprocess.run([sys.executable, os.path.join(HERE, '..', 'tools', 'make_reader_fonts.py'), '--check'],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_the_four_sizes_in_order(self):
        self.assertEqual(reader_fonts.SIZES, ('S', 'M', 'L', 'X'))
        self.assertEqual(sorted(reader_fonts.FONTS), sorted(reader_fonts.SIZES))


class Tables(unittest.TestCase):
    def test_line_heights_and_point_sizes(self):
        for size, font in reader_fonts.FONTS.items():
            self.assertEqual(font['points'], POINTS[size])
            self.assertEqual(font['y_advance'], Y_ADVANCE[size])

    def test_every_printable_ascii_character_has_a_glyph(self):
        for size, font in reader_fonts.FONTS.items():
            self.assertEqual(sorted(font['glyphs']), list(range(0x20, 0x7F)), size)

    def test_each_bitmap_is_exactly_as_long_as_its_dimensions_say(self):
        for size, font in reader_fonts.FONTS.items():
            for code, (w, h, _xa, _xo, _yo, bits) in font['glyphs'].items():
                self.assertEqual(len(bits), ((w * h + 7) // 8) * 2, '%s %r' % (size, chr(code)))

    def test_a_space_draws_nothing_but_advances(self):
        for size, font in reader_fonts.FONTS.items():
            w, h, xa, _xo, _yo, bits = font['glyphs'][0x20]
            self.assertEqual((w, h, bits), (0, 0, ''))
            self.assertGreater(xa, 0)

    def test_glyphs_scale_with_the_size(self):
        for a, b in zip(reader_fonts.SIZES, reader_fonts.SIZES[1:]):
            for ch in 'nWA':
                self.assertLess(reader_fonts.FONTS[a]['glyphs'][ord(ch)][2], reader_fonts.FONTS[b]['glyphs'][ord(ch)][2])

    def test_a_known_glyph_is_bit_exact(self):
        # FreeSerif12pt7b '!': offset 0, 2x16 pixels = 4 bytes, straight from the first bytes of the header's bitmap
        w, h, xa, xo, yo, bits = reader_fonts.FONTS['M']['glyphs'][ord('!')]
        self.assertEqual((w, h, xa, xo, yo), (2, 16, 8, 3, -15))
        self.assertEqual(bits, 'fffea83f')

    def test_glyphs_sit_on_the_baseline_inside_the_line_height(self):
        for size, font in reader_fonts.FONTS.items():
            for code, (w, h, _xa, _xo, yo, _bits) in font['glyphs'].items():
                if h:
                    self.assertLessEqual(-yo, font['y_advance'], '%s %r rises above the line' % (size, chr(code)))
                    self.assertLessEqual(yo + h, font['y_advance'] // 3 + 2, '%s %r drops too far' % (size, chr(code)))


class AgainstTheHeaders(unittest.TestCase):
    """Every glyph row in a header ends with a comment like  // 0x41 'A'. Reading the numbers that way (rather than
    the way the generator does) checks the generator did not misalign the table."""

    ROW = re.compile(r'\{\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(-?\d+),\s*(-?\d+)\s*\}[,}; ]*//\s*0x([0-9A-Fa-f]{2})')

    def test_every_glyph_matches(self):
        for size, points in POINTS.items():
            with open(os.path.join(FONT_DIR, 'FreeSerif%dpt7b.h' % points)) as f:
                rows = self.ROW.findall(f.read())
            self.assertEqual(len(rows), 95, size)
            for off, w, h, xa, xo, yo, code in rows:
                got = reader_fonts.FONTS[size]['glyphs'][int(code, 16)]
                self.assertEqual(got[:5], (int(w), int(h), int(xa), int(xo), int(yo)), '%s 0x%s' % (size, code))


class Drawing(unittest.TestCase):
    def test_a_glyph_has_ink_and_fits_its_box(self):
        w, h, _xa, _xo, _yo, bits = reader_fonts.FONTS['M']['glyphs'][ord('A')]
        value = int(bits, 16)
        total = len(bits) * 4
        pixels = [(value >> (total - 1 - i)) & 1 for i in range(w * h)]
        self.assertGreater(sum(pixels), 10)
        rows_with_ink = {i // w for i, p in enumerate(pixels) if p}
        self.assertEqual((min(rows_with_ink), max(rows_with_ink)), (0, h - 1))       # the box is tight vertically


if __name__ == '__main__':
    unittest.main()
