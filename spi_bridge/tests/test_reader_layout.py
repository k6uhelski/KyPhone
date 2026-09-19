"""
test_reader_layout.py — wrapping, pagination, reading positions and wire frames (spi_bridge/reader_layout.py).

Pure Python; no hardware, no pygame.

    python3 -m pytest spi_bridge/tests/test_reader_layout.py -v
"""

import os
import random
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import reader_fonts  # noqa: E402
import reader_layout as L  # noqa: E402

SIZES = reader_fonts.SIZES
SEP = '\xb7'

WORDS = ['the', 'Alice', 'rabbit-hole', 'wonderland', 'and', 'she', 'said,', '"Curiouser', 'curiouser!"', 'it\'s',
         'bank--the', 'birds', '(really)', 'a', 'I', 'of', 'extraordinarily', 'WWWWWWWWWW', 'mm', 'well-known-fact',
         'x' * 90, '1,000,000', 'e.g.', 'so-called', '"Hi"', '...and', 'Mr.', 'Q&A']


def sentence(rng, n):
    return ' '.join(rng.choice(WORDS) for _ in range(n))


def chapter(seed=1, paragraphs=60):
    rng = random.Random(seed)
    paras = [('h', 'CHAPTER I. Down the Rabbit-Hole')]
    for i in range(paragraphs):
        if i % 17 == 16:
            paras.append(('p', '* * *'))
        elif i % 11 == 10:
            paras.append(('h', 'Part %d' % i))
        paras.append(('p', sentence(rng, rng.choice([3, 12, 40, 90, 160]))))
    return paras


def flat(paras):
    return ''.join(t + '\n' for _k, t in paras)


def squash(s):
    return s.replace(' ', '').replace('\n', '')


class Metrics(unittest.TestCase):
    def test_width_is_the_sum_of_glyph_advances(self):
        for size in SIZES:
            glyphs = reader_fonts.FONTS[size]['glyphs']
            self.assertEqual(L.text_width(size, 'Hello, World'), sum(glyphs[ord(c)][2] for c in 'Hello, World'))
            self.assertEqual(L.text_width(size, ''), 0)

    def test_rows_per_page(self):
        self.assertEqual({s: L.lines_per_page(s) for s in SIZES}, {'S': 24, 'M': 18, 'L': 12, 'X': 9})

    def test_every_printable_glyph_sits_inside_its_row_box(self):
        for size in SIZES:
            y = L.y_advance(size)
            for row in (0, L.lines_per_page(size) - 1):
                top = L.TOP + row * y
                base = L.baseline(size, row)
                for code, (w, h, _xa, _xo, yo, _bits) in reader_fonts.FONTS[size]['glyphs'].items():
                    if h:
                        self.assertGreaterEqual(base + yo, top, '%s %r pokes above its row' % (size, chr(code)))
                        self.assertLessEqual(base + yo + h, top + y, '%s %r drops below its row' % (size, chr(code)))

    def test_the_last_row_ends_above_the_footer(self):
        for size in SIZES:
            self.assertLessEqual(L.TOP + L.lines_per_page(size) * L.y_advance(size), L.TEXT_BOTTOM)
            self.assertLess(L.TEXT_BOTTOM, L.FOOT_RULE_Y)


class Wrapping(unittest.TestCase):
    def test_a_short_paragraph_is_one_indented_line(self):
        self.assertEqual(L.wrap('M', 'Hello there.', indent=3), [('   Hello there.', 0)])
        self.assertEqual(L.wrap('M', ''), [])

    def test_no_line_is_ever_wider_than_the_column(self):
        rng = random.Random(7)
        for size in SIZES:
            for _ in range(60):
                text = sentence(rng, rng.randint(1, 80))
                for indent in (0, 3):
                    for line, _pos in L.wrap(size, text, indent=indent):
                        self.assertLessEqual(L.text_width(size, line), L.TEXT_W, (size, line))

    def test_nothing_is_lost_or_duplicated(self):
        rng = random.Random(11)
        for size in SIZES:
            for _ in range(40):
                text = sentence(rng, rng.randint(1, 120))
                got = ''.join(line for line, _p in L.wrap(size, text, indent=3))
                self.assertEqual(squash(got), squash(text))

    def test_lines_have_no_stray_spaces_and_positions_point_at_the_text(self):
        rng = random.Random(3)
        for size in SIZES:
            text = sentence(rng, 150)
            lines = L.wrap(size, text, indent=3)
            for k, (line, pos) in enumerate(lines):
                body = line[3:] if k == 0 else line
                self.assertEqual(body, body.strip(), repr(line))
                self.assertTrue(text[pos:].startswith(body), (size, pos, body))

    @staticmethod
    def filler(size, fits, overflows):
        """Repeated 'a's such that `filler + fits` fits on one line but `filler + overflows` does not."""
        for n in range(1, 200):
            head = 'a' * n
            if L.text_width(size, head + fits) <= L.TEXT_W < L.text_width(size, head + overflows):
                return head
        raise AssertionError('no filler found')

    def test_a_line_may_break_after_a_hyphen_or_double_dash(self):
        for size in SIZES:
            head = self.filler(size, ' Caucus-', ' Caucus-Race')
            lines = L.wrap(size, head + ' Caucus-Race afterwards')
            self.assertTrue(lines[0][0].endswith('Caucus-'), (size, lines))
            self.assertTrue(lines[1][0].startswith('Race'), (size, lines))
            head = self.filler(size, ' bank--', ' bank--the')
            lines = L.wrap(size, head + ' bank--the birds')
            self.assertTrue(lines[0][0].endswith('bank--'), (size, lines))
            self.assertTrue(lines[1][0].startswith('the'), (size, lines))

    def test_a_leading_hyphen_or_list_dash_never_splits_from_its_word(self):
        self.assertEqual(L.wrap('M', '- milk')[0][0], '- milk')
        self.assertEqual(L.wrap('M', '-3 degrees')[0][0], '-3 degrees')

    def test_a_word_wider_than_the_column_is_broken_to_fit(self):
        for size in SIZES:
            lines = L.wrap(size, 'W' * 300, indent=3)
            self.assertGreater(len(lines), 1)
            self.assertEqual(''.join(l for l, _ in lines).strip(), 'W' * 300)
            for line, _p in lines:
                self.assertLessEqual(L.text_width(size, line), L.TEXT_W)


class Pagination(unittest.TestCase):
    def test_every_page_fits_and_none_starts_or_ends_blank(self):
        paras = chapter()
        for size in SIZES:
            pages = L.paginate(paras, size)
            self.assertGreater(len(pages), 1)
            for pg in pages:
                self.assertLessEqual(len(pg.lines), L.lines_per_page(size))
                self.assertNotEqual(pg.lines[0], '')
                self.assertNotEqual(pg.lines[-1], '')

    def test_the_pages_together_are_exactly_the_chapter(self):
        paras = chapter(seed=5)
        want = squash(flat(paras))
        for size in SIZES:
            got = ''.join(line for pg in L.paginate(paras, size) for line in pg.lines)
            self.assertEqual(squash(got), want, size)

    def test_page_starts_are_increasing_and_point_at_the_text(self):
        paras = chapter(seed=2)
        text = flat(paras)
        for size in SIZES:
            pages = L.paginate(paras, size)
            self.assertEqual(pages[0].start, 0)
            self.assertEqual([p.start for p in pages], sorted(set(p.start for p in pages)))
            for pg in pages:
                first = pg.lines[0].strip()
                self.assertTrue(text[pg.start:].startswith(first), (size, pg.start, first))
            self.assertLess(pages[-1].start, L.chapter_length(paras))

    def test_headings_are_centred_with_a_blank_row_after(self):
        pages = L.paginate([('h', 'CHAPTER ONE'), ('p', 'It was dark. ' * 5)], 'M')
        lines = pages[0].lines
        self.assertTrue(lines[0].startswith(' '))                       # centred (no blank row at the top of a page)
        self.assertEqual(lines[0].strip(), 'CHAPTER ONE')
        self.assertEqual(lines[1], '')
        self.assertTrue(lines[2].startswith('   It'))                   # the paragraph after it is indented

    def test_a_heading_is_never_left_alone_at_the_bottom_of_a_page(self):
        size = 'M'
        rows = L.lines_per_page(size)
        # fill all but the last two rows, so a heading (blank row + line + blank row) would end the page
        filler = ('p', 'word ' * 12 * (rows - 2))
        paras = [filler, ('h', 'A HEADING'), ('p', 'Then some text follows the heading here. ' * 3)]
        pages = L.paginate(paras, size)
        self.assertGreater(len(pages), 1)
        self.assertNotEqual(pages[0].lines[-1].strip(), 'A HEADING')
        self.assertTrue(any(l.strip() == 'A HEADING' for l in pages[1].lines))

    def test_scene_breaks_are_centred_and_not_indented(self):
        line = next(l for l in L.paginate([('p', 'before'), ('p', '* * *'), ('p', 'after')], 'M')[0].lines if '*' in l)
        self.assertEqual(line.strip(), '* * *')
        self.assertGreater(len(line) - len(line.lstrip()), 5)

    def test_an_empty_chapter_is_one_blank_page(self):
        pages = L.paginate([], 'M')
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].start, 0)

    def test_a_long_book_paginates_quickly(self):
        import time
        paras = chapter(seed=9, paragraphs=800)
        t = time.time()
        L.paginate(paras, 'S')
        self.assertLess(time.time() - t, 5)


class Positions(unittest.TestCase):
    def test_page_index_finds_the_page_containing_an_offset(self):
        paras = chapter(seed=4)
        pages = L.paginate(paras, 'M')
        for k, pg in enumerate(pages):
            self.assertEqual(L.page_index(pages, pg.start), k)
            if k + 1 < len(pages):
                self.assertEqual(L.page_index(pages, pages[k + 1].start - 1), k)
        self.assertEqual(L.page_index(pages, 0), 0)
        self.assertEqual(L.page_index(pages, -5), 0)
        self.assertEqual(L.page_index(pages, 10 ** 9), len(pages) - 1)

    def test_a_position_survives_a_change_of_font_size(self):
        paras = chapter(seed=6)
        by_size = {s: L.paginate(paras, s) for s in SIZES}
        for offset in range(0, L.chapter_length(paras), 977):
            for a in SIZES:
                for b in SIZES:
                    pa = by_size[a][L.page_index(by_size[a], offset)]
                    pb = by_size[b][L.page_index(by_size[b], pa.start)]
                    # the page in the new size still contains the character the reader was at the top of
                    nxt = [p.start for p in by_size[b] if p.start > pb.start]
                    self.assertLessEqual(pb.start, pa.start)
                    self.assertTrue(not nxt or pa.start < nxt[0])


class Frames(unittest.TestCase):
    def frames_for(self, size, seed=8):
        pages = L.paginate(chapter(seed=seed), size)
        return [(pg, L.page_frames(size, pg.lines, 'CHAPTER I. Down the Rabbit-Hole', '12/40  35%', 'P')) for pg in pages]

    def test_every_frame_fits_one_command_and_is_drawable(self):
        for size in SIZES:
            for _pg, frames in self.frames_for(size):
                for f in frames:
                    self.assertLessEqual(len(f), L.MAX_COMMAND_CHARS)
                    self.assertTrue(all(' ' <= c <= '~' or c == SEP for c in f), f)

    def test_first_frame_clears_and_the_last_refreshes(self):
        for size in SIZES:
            for _pg, frames in self.frames_for(size):
                self.assertTrue(frames[0].startswith('RTEXT|%s|0|S|' % size) or frames[0].split('|')[3] == 'S')
                self.assertTrue(all(f.split('|')[3] == '-' for f in frames[1:-1]))
                self.assertTrue(frames[-1].startswith('RFOOT|P|'))
                self.assertEqual(sum(1 for f in frames if f.startswith('RFOOT')), 1)

    def test_the_frames_rebuild_the_page_row_for_row(self):
        for size in SIZES:
            for pg, frames in self.frames_for(size):
                rows = {}
                for f in frames[:-1]:
                    kind, sz, row, flag, body = f.split('|')
                    self.assertEqual((kind, sz), ('RTEXT', size))
                    for k, line in enumerate(body.split(SEP)):
                        rows[int(row) + k] = line
                for r, line in enumerate(pg.lines):
                    if line != '':
                        self.assertEqual(rows.get(r), line, (size, r))
                    else:
                        self.assertIn(rows.get(r, ''), ('',))
                self.assertLessEqual(max(rows), len(pg.lines) - 1)

    def test_a_frame_never_ends_on_a_blank_row_or_starts_at_one(self):
        for size in SIZES:
            for _pg, frames in self.frames_for(size):
                for f in frames[:-1]:
                    body = f.split('|', 4)[4]
                    self.assertNotEqual(body, '')
                    self.assertFalse(body.endswith(SEP))
                    self.assertFalse(body.startswith(SEP))

    def test_frame_counts_are_modest(self):
        counts = {}
        for size in SIZES:
            data = self.frames_for(size)
            counts[size] = max(len(fr) - 1 for _pg, fr in data)
        self.assertLessEqual(counts['M'], 6)
        self.assertLessEqual(counts['S'], 9)

    def test_a_full_refresh_is_requested_by_the_flag(self):
        self.assertTrue(L.page_frames('M', ['hello'], 'a', 'b', 'F')[-1].startswith('RFOOT|F|'))

    def test_a_line_too_long_for_one_frame_is_an_error(self):
        with self.assertRaises(ValueError):
            L.page_frames('M', ['x' * 300], 'a', 'b', 'P')

    def test_the_footer_is_fitted_to_the_width(self):
        left, right = L.fit_footer('CHAPTER III. A Caucus-Race and a Long Tale', '12/40  35%')
        self.assertLessEqual(len(left) + len(right) + 2, L.FOOT_COLS)
        self.assertTrue(left.endswith('...'))
        self.assertEqual(L.fit_footer('Short', '1/2  3%'), ('Short', '1/2  3%'))


class Ink(unittest.TestCase):
    def test_a_full_width_line_stays_inside_the_screen(self):
        for size in SIZES:
            for ch in 'WMwm@':
                for line, _p in L.wrap(size, ch * 200):
                    ink = L.text_ink(size, L.TEXT_X, L.baseline(size, 3), line)
                    xs = [x for x, _y in ink]
                    self.assertGreaterEqual(min(xs), L.TEXT_X - 4)
                    self.assertLessEqual(max(xs), L.TEXT_X + L.TEXT_W + 4)
                    self.assertLess(max(xs), L.SCREEN)

    def test_a_long_centred_heading_wraps_inside_the_column(self):
        for size in SIZES:
            pages = L.paginate([('h', 'CHAPTER III. A Caucus-Race and a Long Tale'), ('p', 'Text follows. ' * 10)], size)
            for line in pages[0].lines:
                self.assertLessEqual(L.text_width(size, line), L.TEXT_W, (size, line))


if __name__ == '__main__':
    unittest.main()
