"""
test_firmware_host.py — the Inkplate firmware's screen renderers, tested on a computer.

The firmware draws every OS 0.2.1 screen from Inkplate_SPI_Peripheral/ui_screens.h, which depends only
on a handful of Adafruit_GFX calls. tests/firmware_host/ builds that same C++ against a fake display
(with the real GFX 5x7 font) and renders a wire command to a 600x600 frame. These tests check:

  * exact geometry of the firmware's own frames (selected rows, rules, buttons, wrapping),
  * that the firmware and simulator.py agree on every full-width rule and fill (the two renderers are
    hand-ported from one another; their text differs only by font),
  * memory safety: thousands of malformed commands under the address and undefined-behaviour sanitizers.

Skipped when clang++ or Adafruit_GFX's glcdfont.c is not available.

    ~/.venvs/kyphone/bin/python -m pytest spi_bridge/tests/test_firmware_host.py -v
"""

import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = os.path.join(HERE, 'firmware_host')
sys.path.insert(0, HOST)
sys.path.insert(0, os.path.join(HERE, '..'))
from screens import SCREENS                  # noqa: E402

GLCDFONT = os.environ.get('GLCDFONT_C') or os.path.expanduser(
    '~/Documents/Arduino/libraries/Adafruit_GFX_Library/glcdfont.c')
GFXFONT = os.environ.get('GFXFONT_H') or os.path.join(os.path.dirname(GLCDFONT), 'gfxfont.h')
CLANG = shutil.which('clang++')
AVAILABLE = bool(CLANG) and os.path.exists(GLCDFONT) and os.path.exists(GFXFONT)

W = 600


def build(out_path, sanitize=False, source='render_host.cpp'):
    cmd = [CLANG, '-std=c++17', '-Wall', '-Wextra', '-Wno-unused-function', '-DGLCDFONT_C="%s"' % GLCDFONT,
           '-DGFXFONT_H="%s"' % GFXFONT,
           '-o', out_path, os.path.join(HOST, source)]
    if sanitize:
        cmd[1:1] = ['-g', '-O1', '-fsanitize=address,undefined', '-fno-omit-frame-pointer']
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError(done.stderr[:2000])


@unittest.skipUnless(AVAILABLE, 'needs clang++ and Adafruit_GFX (glcdfont.c)')
class FirmwareFrames(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='kyphone-fw-')
        cls.exe = os.path.join(cls.tmp, 'render_host')
        build(cls.exe)
        wires = {name: wire for name, (wire, _) in SCREENS.items()}
        cls.frames = cls.render(wires)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def render(cls, wires):
        """{name: wire} -> {name: 600*600 bytes, 0 = ink}"""
        out = os.path.join(cls.tmp, 'out')
        os.makedirs(out, exist_ok=True)
        lines = ''.join('%s\t%s\n' % (n, w) for n, w in wires.items()).encode('latin-1')
        done = subprocess.run([cls.exe, out], input=lines, capture_output=True)
        assert done.returncode == 0, done.stderr.decode()[:500]
        return {n: open(os.path.join(out, n + '.raw'), 'rb').read() for n in wires}

    def frame(self, wire):
        return self.render({'adhoc': wire})['adhoc']

    @staticmethod
    def ink(frame, x, y):
        return frame[y * W + x] == 0

    def ink_count(self, frame):
        return frame.count(0)

    # ── every screen draws something, and only inside the panel ────────────────
    def test_every_screen_draws(self):
        for name, frame in self.frames.items():
            self.assertGreater(self.ink_count(frame), 300, name)

    # ── lists ──────────────────────────────────────────────────────────────────
    def test_texts_rows_are_111px_and_only_the_selected_one_is_inverted(self):
        f = self.frames['texts']                                   # sel = 2
        for row in range(5):
            self.assertEqual(self.ink(f, 4, 44 + row * 111 + 4), row == 2, row)
        self.assertTrue(self.ink(f, 300, 43))                      # the header rule

    def test_contacts_footer_rule_sits_above_a_45px_strip(self):
        f = self.frames['contacts']
        self.assertTrue(self.ink(f, 300, 553) and self.ink(f, 300, 554))
        self.assertFalse(self.ink(f, 300, 552))
        self.assertTrue(self.ink(f, 4, 48))                        # the first row is selected

    def test_library_rows_are_111px_like_the_texts_list(self):
        f = self.frames['library']                                 # sel = 1
        self.assertEqual([self.ink(f, 4, 44 + row * 111 + 4) for row in range(3)], [False, True, False])
        self.assertTrue(self.ink(f, 300, 43))                      # the header rule
        self.assertFalse(self.has_ink(f, 500, 6, 585, 40))         # back only: no + control

    def test_an_empty_library_says_so(self):
        f = self.frames['library_empty']
        self.assertGreater(self.ink_count(f), 300)
        self.assertFalse(any(self.ink(f, 4, y) for y in (60, 120, 200, 300, 400)))

    def test_calls_rows_are_92px(self):
        f = self.frames['calls']                                   # sel = 1
        self.assertEqual([self.ink(f, 4, 44 + i * 92 + 4) for i in range(4)], [False, True, False, False])

    def test_empty_lists_say_so_and_have_no_inverted_row(self):
        for name in ('texts_empty', 'contacts_nomatch'):
            f = self.frames[name]
            self.assertFalse(any(self.ink(f, 4, y) for y in (60, 120, 200, 300, 400)), name)

    # ── thread ─────────────────────────────────────────────────────────────────
    def test_a_sent_bubble_is_filled_and_a_sending_one_is_not(self):
        sending = self.ink_count(self.frames['thread_sending'])              # last bubble is Y0
        sent = self.ink_count(self.frame(SCREENS['thread_sending'][0].replace('Y0', 'Y1')))
        self.assertGreater(sent, sending + 2000)

    def test_the_retry_prompt_adds_a_ring(self):
        self.assertGreater(self.ink_count(self.frames['thread_retry']), self.ink_count(self.frames['thread_notsent']))

    def test_the_composer_rule_rises_one_line_at_a_time(self):
        for draft, composer_h in (('short', 45), ('w ' * 20, 79), ('w ' * 40, 113)):
            f = self.frame('THREAD2|Pip|%s||%s' % (draft, '\xb7'.join(['R', '6:52 PM', 'hi'])))
            self.assertTrue(self.ink(f, 595, W - composer_h - 2), (draft, composer_h))
            self.assertFalse(self.ink(f, 595, W - composer_h + 3), (draft, composer_h))

    def test_an_over_tall_bubble_is_clipped_below_the_header(self):
        f = self.frame('THREAD2|Pip|||' + '\xb7'.join(['R', '6:52 PM', 'word ' * 40]))
        self.assertFalse(any(self.ink(f, x, 55) for x in range(0, W)))         # nothing bleeds into the header strip

    # ── buttons and confirmations ──────────────────────────────────────────────
    def test_ok_on_a_stop_alert_is_inverted(self):
        x, y = W - 24 - (2 * 12 + 36 + 6), W - 24 - 36
        self.assertTrue(self.ink(self.frames['alert_bad_number'], x + 8, y + 8))

    def test_the_safe_button_holds_the_selection_when_a_confirm_opens(self):
        keep_x = W - 24 - (len('KEEP CONTACT') * 12 + 36 + 6)
        opened, destructive = self.frames['confirm_delete'], self.frames['confirm_delete_go']
        self.assertTrue(self.ink(opened, keep_x + 8, 548))
        self.assertFalse(self.ink(opened, 56 + 6, 548))
        self.assertFalse(self.ink(destructive, keep_x + 8, 548))
        self.assertTrue(self.ink(destructive, 56 + 6, 548))

    def test_send_inverts_when_selected(self):
        x = W - 24 - (4 * 12 + 36 + 6)
        self.assertTrue(self.ink(self.frames['compose'], x + 8, W - 14 - 36 + 8))
        self.assertFalse(self.ink(self.frames['compose_empty'], x + 8, W - 14 - 36 + 8))

    # ── contact pages and the edit form ────────────────────────────────────────
    def has_ink(self, f, x0, y0, x1, y1):
        return any(self.ink(f, x, y) for x in range(x0, x1) for y in range(y0, y1))

    def test_edit_is_only_offered_for_a_saved_contact(self):
        self.assertTrue(self.has_ink(self.frames['contact_saved'], 500, 6, 585, 40))
        self.assertTrue(self.has_ink(self.frames['contact_nonum'], 500, 6, 585, 40))
        self.assertFalse(self.has_ink(self.frames['contact_unsaved'], 500, 6, 585, 40))

    def test_an_unsaved_number_offers_call_text_and_save(self):
        f = self.frames['contact_unsaved']                                    # SAVE selected
        call_x, text_x, save_x = 28, 28 + 122 + 16, 28 + 122 + 16 + 122 + 16
        self.assertEqual([self.ink(f, x + 6, 366) for x in (call_x, text_x, save_x)], [False, False, True])

    def test_a_saved_contact_without_a_number_has_only_add_number(self):
        f = self.frames['contact_nonum']
        self.assertTrue(self.ink(f, 34, 366))
        self.assertFalse(self.has_ink(f, 28 + 230 + 20, 362, 500, 404))

    def test_delete_is_only_on_an_edit_form_and_inverts_when_selected(self):
        self.assertTrue(self.ink(self.frames['edit_delete'], 30, 556))
        self.assertFalse(self.ink(self.frames['edit_edit'], 30, 556))
        self.assertTrue(self.has_ink(self.frames['edit_edit'], 24, 549, 140, 585))       # present, not selected
        self.assertFalse(self.has_ink(self.frames['edit_new'], 24, 549, 140, 585))       # a new contact has none

    def test_home_row_three_is_contacts_and_on_screen_without_scrolling(self):
        f = self.frames['home_contacts']                                       # index 2
        self.assertEqual([self.ink(f, 4, 62 + i * 135 + 4) for i in range(3)], [False, False, True])

    # ── home menu icons ────────────────────────────────────────────────────────
    def icon_matches(self, frame, name, x, y, selected):
        from home_icons import ICONS
        for j, row in enumerate(ICONS[name]):
            for i in range(56):
                on = (row >> (55 - i)) & 1
                is_ink = self.ink(frame, x + i, y + j)
                if is_ink != (on != selected):          # selected row: ink is paper, the fill is ink
                    return False
        return True

    def test_the_firmware_draws_each_icon_bitmap_where_the_design_puts_it(self):
        f = self.frames['home']                                                  # TEXT selected
        for i, name in enumerate(['TEXT', 'CALL', 'CONTACTS']):
            self.assertTrue(self.icon_matches(f, name, 272, 62 + i * 135 + 39, i == 0), name)

    def test_icons_and_words_are_centred_as_one_unit(self):
        x0 = (600 - (56 + 28 + 4 * 36)) // 2
        self.assertTrue(self.icon_matches(self.frames['home_both'], 'CALL', x0, 62 + 135 + 39, True))

    def test_words_style_draws_no_icon(self):
        self.assertFalse(self.icon_matches(self.frames['home_words'], 'TEXT', 272, 62 + 39, True))

    def test_the_more_below_cue_is_a_downward_funnel(self):
        f = self.frames['home']
        for y, x0, x1 in ((581, 574, 587), (586, 577, 584), (591, 580, 582)):
            self.assertTrue(all(self.ink(f, x, y + 1) for x in range(x0, x1 + 1)), (y, x0, x1))
            self.assertFalse(self.ink(f, x0 - 1, y + 1))
            self.assertFalse(self.ink(f, x1 + 1, y + 1))


@unittest.skipUnless(AVAILABLE, 'needs clang++ and Adafruit_GFX (glcdfont.c)')
class FirmwareMatchesEmulator(unittest.TestCase):
    """Both renderers draw the same rules, fills and borders; only their fonts differ."""
    NAMES = ['home', 'home_contacts', 'home_both', 'home_words', 'home_icons_end', 'texts', 'texts_empty', 'library', 'library_empty', 'contacts', 'calls', 'thread_sending', 'thread_retry',
             'compose_empty', 'alert_bad_number', 'confirm_delete', 'contact_saved', 'contact_unsaved', 'edit_new',
             'edit_delete']

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
        os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')
        try:
            import pygame
            from unittest.mock import MagicMock
            if isinstance(pygame, MagicMock):
                raise ImportError
            import simulator
        except ImportError:
            raise unittest.SkipTest('needs real pygame')
        cls.pygame, cls.simulator = pygame, simulator
        cls.tmp = tempfile.mkdtemp(prefix='kyphone-fw-')
        cls.exe = os.path.join(cls.tmp, 'render_host')
        build(cls.exe)
        cls.sim = simulator.Simulator(lambda k: None)
        cls.sim.init()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def firmware_frame(self, wire):
        out = os.path.join(self.tmp, 'out')
        os.makedirs(out, exist_ok=True)
        subprocess.run([self.exe, out], input=('x\t%s\n' % wire).encode('latin-1'), capture_output=True, check=True)
        return open(os.path.join(out, 'x.raw'), 'rb').read()

    def sim_frame(self, wire):
        self.sim._surface.fill((255, 255, 255))
        self.sim._draw(wire[:253])
        s = self.sim._surface
        return bytes(0 if sum(s.get_at((x, y))[:3]) < 200 else 255 for y in range(W) for x in range(W))

    @staticmethod
    def margin_runs(frame, x=4):
        """Vertical runs of ink down the left margin, where no text is ever drawn: every full-width rule and
        every inverted row shows up here, and the two renderers' different fonts cannot."""
        runs, start = [], None
        for y in range(W):
            black = frame[y * W + x] == 0
            if black and start is None:
                start = y
            elif not black and start is not None:
                runs.append((start, y - 1)); start = None
        if start is not None:
            runs.append((start, W - 1))
        return runs

    def test_rules_and_inverted_rows_are_where_the_emulator_puts_them(self):
        for name in self.NAMES:
            wire = SCREENS[name][0]
            fw, sim = self.margin_runs(self.firmware_frame(wire)), self.margin_runs(self.sim_frame(wire))
            self.assertEqual(len(fw), len(sim), (name, fw, sim))
            for a, b in zip(fw, sim):
                self.assertLessEqual(abs(a[0] - b[0]), 1, (name, fw, sim))       # start
                self.assertLessEqual(abs(a[1] - b[1]), 1, (name, fw, sim))       # end


@unittest.skipUnless(AVAILABLE, 'needs clang++ and Adafruit_GFX (glcdfont.c)')
class FirmwareMemorySafety(unittest.TestCase):
    def test_malformed_and_maximum_length_commands_do_not_overflow_or_misbehave(self):
        tmp = tempfile.mkdtemp(prefix='kyphone-fw-asan-')
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        exe = os.path.join(tmp, 'render_host_asan')
        build(exe, sanitize=True)

        rng = random.Random(20260918)
        prefixes = ['HOME2|', 'TEXTS|', 'CONTACTSPICK|', 'CALLS|', 'THREAD2|', 'COMPOSE|', 'STUB|', 'CONFIRM|',
                    'CONTACTEDIT|', 'CONTACT|', 'LIBRARY|']
        alphabet = [chr(c) for c in range(0x20, 0x7f) if chr(c) != '|'] + ['\xb7'] * 6

        def field(n):
            return ''.join(rng.choice(alphabet) for _ in range(rng.randint(0, n)))

        lines = []
        for i in range(2500):
            body = '|'.join(field(rng.choice([0, 3, 15, 40, 120, 250])) for _ in range(rng.randint(0, 12)))
            w = (rng.choice(prefixes) + body)[:253]
            if rng.random() < .2:
                w = rng.choice(prefixes) + rng.choice(['-9', '999999999999', '-', 'x', '0']) + '|' + body
            lines.append('f%d\t%s\n' % (i, w[:253]))
        lines += [
            'long1\tTHREAD2|' + 'N' * 40 + '|' + 'd' * 90 + '||R\xb79:99 PM\xb7' + 'w' * 200 + '\n',
            'long2\tCOMPOSE|' + 'T' * 40 + '|' + 'm' * 210 + '|0||0|1\n',
            'long3\tSTUB|' + 'T' * 30 + '|' + 'B' * 220 + '\n',
            'long4\tCONFIRM|' + 'T' * 30 + '|' + 'B' * 150 + '|' + 'G' * 30 + '|' + 'K' * 30 + '|D\n',
            'long5\tTEXTS|4|' + '|'.join(('N' * 30 + '\xb7' + 'p' * 60 + '\xb71\xb7' + 't' * 20) for _ in range(9)) + '\n',
        ]
        env = dict(os.environ, ASAN_OPTIONS='halt_on_error=1:detect_leaks=0', UBSAN_OPTIONS='halt_on_error=1')
        done = subprocess.run([exe, '-'], input=''.join(lines).encode('latin-1'), capture_output=True, env=env)
        err = done.stderr.decode('latin-1')
        self.assertEqual(done.returncode, 0, err[:1200])
        self.assertNotIn('AddressSanitizer', err)
        self.assertNotIn('runtime error', err)


def reader_pages():
    """A synthetic chapter laid out at every size: {size: [frames of one page]}, plus the page's lines."""
    import reader_layout as rl
    text = ' '.join(['gypsy jumping quickly; Wizards Fly Over Big Dwarfs, Xylophones (Q) & "Zebras" -- well-known'] * 300)
    out = {}
    for size in rl.SIZES:
        page = rl.paginate([('h', 'A HEADING'), ('p', text)], size)[0]
        out[size] = (page.lines, rl.page_frames(size, page.lines, 'A chapter title', '1/9  3%', 'P'))
    return out


def build_reader(out_path, sanitize=False):
    build(out_path, sanitize, source='render_reader.cpp')


@unittest.skipUnless(AVAILABLE, 'needs clang++ and Adafruit_GFX (glcdfont.c, gfxfont.h)')
class FirmwareReader(unittest.TestCase):
    """The firmware's own RTEXT / RFOOT code (ui_reader.h), built on a computer, play back a page frame by frame."""

    @classmethod
    def setUpClass(cls):
        import reader_layout
        cls.rl = reader_layout
        cls.tmp = tempfile.mkdtemp(prefix='kyphone-fw-reader-')
        cls.exe = os.path.join(cls.tmp, 'render_reader')
        build_reader(cls.exe)
        cls.pages = reader_pages()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def play(self, frames):
        """One page (a list of frames) -> (600*600 bytes with 0 = ink, the refresh code the last frame asked for)."""
        out = os.path.join(self.tmp, 'out')
        os.makedirs(out, exist_ok=True)
        done = subprocess.run([self.exe, out], input=('x\t%s\n' % '\t'.join(frames)).encode('latin-1'), capture_output=True)
        self.assertEqual(done.returncode, 0, done.stderr.decode()[:500])
        name, refresh = done.stdout.decode().split()
        return open(os.path.join(out, 'x.raw'), 'rb').read(), int(refresh)

    @staticmethod
    def ink_set(frame, y0=0, y1=W):
        return {(i % W, i // W) for i in range(y0 * W, y1 * W) if frame[i] == 0}

    def test_a_page_is_drawn_glyph_for_glyph_where_the_layout_module_says(self):
        rl = self.rl
        for size, (lines, frames) in self.pages.items():
            frame, _ = self.play(frames)
            want = set()
            for row, line in enumerate(lines):
                want |= {(x, y) for x, y in rl.text_ink(size, rl.TEXT_X, rl.baseline(size, row), line) if 0 <= x < W and 0 <= y < W}
            self.assertEqual(self.ink_set(frame, 0, rl.FOOT_RULE_Y), want, size)

    def test_text_frames_alone_draw_but_ask_for_no_refresh_and_the_footer_frame_refreshes(self):
        frames = self.pages['M'][1]
        frame, refresh = self.play(frames[:-1])
        self.assertEqual(refresh, 0)
        self.assertGreater(len(self.ink_set(frame)), 1000)
        self.assertEqual(self.play(frames)[1], 1)                                  # P: partial
        self.assertEqual(self.play(frames[:-1] + ['RFOOT|F|a|b'])[1], 2)            # F: full
        self.assertEqual(self.play(['RFOOT|'])[1], 1)                              # anything else: partial

    def test_the_first_frame_clears_and_a_later_one_does_not(self):
        rl = self.rl
        frame, _ = self.play(['RTEXT|M|0|S|old old old', 'RTEXT|M|5|S|new'])
        self.assertEqual(self.ink_set(frame), set(rl.text_ink('M', rl.TEXT_X, rl.baseline('M', 5), 'new')))
        frame, _ = self.play(['RTEXT|M|0|S|old', 'RTEXT|M|5|-|new'])
        self.assertEqual(self.ink_set(frame), set(rl.text_ink('M', rl.TEXT_X, rl.baseline('M', 0), 'old'))
                         | set(rl.text_ink('M', rl.TEXT_X, rl.baseline('M', 5), 'new')))

    def test_blank_lines_leave_their_row_empty(self):
        rl = self.rl
        frame, _ = self.play(['RTEXT|L|1|S|one\xb7\xb7three'])
        want = set(rl.text_ink('L', rl.TEXT_X, rl.baseline('L', 1), 'one')) | set(rl.text_ink('L', rl.TEXT_X, rl.baseline('L', 3), 'three'))
        self.assertEqual(self.ink_set(frame), want)

    def test_the_footer_is_a_one_pixel_rule_with_text_at_both_margins(self):
        rl = self.rl
        frame, _ = self.play(['RFOOT|P|CHAPTER ONE|12/40  35%'])
        ink = self.ink_set(frame)
        self.assertEqual({(x, y) for x, y in ink if y == rl.FOOT_RULE_Y}, {(x, rl.FOOT_RULE_Y) for x in range(rl.TEXT_X, rl.TEXT_X + rl.TEXT_W)})
        self.assertFalse({p for p in ink if p[1] < rl.FOOT_RULE_Y})
        text = {p for p in ink if p[1] > rl.FOOT_RULE_Y}
        self.assertTrue(text)
        self.assertLess(min(x for x, _ in text), rl.TEXT_X + 12)
        self.assertLessEqual(max(x for x, _ in text), rl.TEXT_X + rl.TEXT_W)
        self.assertGreater(max(x for x, _ in text), rl.TEXT_X + rl.TEXT_W - 24)
        self.assertLess(max(y for _, y in text), 600)

    def test_the_footer_is_drawn_in_the_built_in_font_even_after_book_text(self):
        rl = self.rl
        alone, _ = self.play(['RFOOT|P|CHAPTER ONE|12/40  35%'])
        after, _ = self.play(['RTEXT|X|0|S|Some text', 'RFOOT|P|CHAPTER ONE|12/40  35%'])
        self.assertEqual(self.ink_set(alone, rl.FOOT_RULE_Y), self.ink_set(after, rl.FOOT_RULE_Y))

    def test_a_line_wider_than_the_screen_does_not_wrap_onto_the_next_row(self):
        rl = self.rl
        frame, _ = self.play(['RTEXT|M|2|S|' + 'W' * 60])
        rows = {y for _x, y in self.ink_set(frame)}
        y = rl.y_advance('M')
        self.assertGreaterEqual(min(rows), rl.TOP + 2 * y)
        self.assertLess(max(rows), rl.TOP + 3 * y)

    def test_a_malformed_frame_draws_nothing(self):
        for frame in ('RTEXT|Q|0|S|text', 'RTEXT|MM|0|S|text', 'RTEXT|M|-1|S|text', 'RTEXT|M|64|S|text',
                      'RTEXT|M|x|S|text', 'RTEXT|M|0|S', 'RTEXT|M|0', 'RTEXT|M', 'RTEXT|', 'RTEXT|||'):
            self.assertEqual(self.ink_set(self.play([frame])[0]), set(), frame)

    def test_rows_below_the_panel_are_dropped_not_drawn_wrapped(self):
        frame, _ = self.play(['RTEXT|X|60|S|a\xb7b\xb7c\xb7d'])
        self.assertEqual(self.ink_set(frame), set())


@unittest.skipUnless(AVAILABLE, 'needs clang++ and Adafruit_GFX (glcdfont.c, gfxfont.h)')
class ReaderMatchesEmulator(unittest.TestCase):
    """The firmware and simulator.py draw a page identically: same glyphs, same rows, same footer rule."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
        os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')
        try:
            import pygame
            from unittest.mock import MagicMock
            if isinstance(pygame, MagicMock):
                raise ImportError
            import simulator
        except ImportError:
            raise unittest.SkipTest('needs real pygame')
        import reader_layout
        cls.rl, cls.pygame = reader_layout, pygame
        cls.tmp = tempfile.mkdtemp(prefix='kyphone-fw-reader-')
        cls.exe = os.path.join(cls.tmp, 'render_reader')
        build_reader(cls.exe)
        cls.sim = simulator.Simulator(lambda k: None)
        cls.sim.init()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_every_text_pixel_and_the_footer_rule_agree_at_all_four_sizes(self):
        rl = self.rl
        out = os.path.join(self.tmp, 'out')
        os.makedirs(out, exist_ok=True)
        pages = reader_pages()
        lines = ''.join('%s\t%s\n' % (size, '\t'.join(frames)) for size, (_l, frames) in pages.items())
        done = subprocess.run([self.exe, out], input=lines.encode('latin-1'), capture_output=True, check=True)
        for size, (_lines, frames) in pages.items():
            fw = open(os.path.join(out, size + '.raw'), 'rb').read()
            self.sim._surface.fill((255, 255, 255))
            self.sim._draw(frames)
            data = self.pygame.image.tostring(self.sim._surface, 'RGB')
            for y in range(0, rl.FOOT_RULE_Y + 1):                                   # the text column and the rule
                for x in range(W):
                    self.assertEqual(fw[y * W + x] == 0, data[(y * W + x) * 3] == 0, (size, x, y))


@unittest.skipUnless(AVAILABLE, 'needs clang++ and Adafruit_GFX (glcdfont.c, gfxfont.h)')
class ReaderMemorySafety(unittest.TestCase):
    def test_malformed_and_maximum_length_reader_frames_do_not_overflow_or_misbehave(self):
        tmp = tempfile.mkdtemp(prefix='kyphone-fw-reader-asan-')
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        exe = os.path.join(tmp, 'render_reader_asan')
        build_reader(exe, sanitize=True)

        rng = random.Random(20260919)
        alphabet = [chr(c) for c in list(range(0x20, 0x7f)) + list(range(0x80, 0x100)) if chr(c) != '|'] + ['\xb7'] * 8
        alphabet = [c for c in alphabet if c not in '\t\n']

        def field(n):
            return ''.join(rng.choice(alphabet) for _ in range(rng.randint(0, n)))

        def frame():
            kind = rng.choice(['RTEXT|', 'RTEXT|', 'RTEXT|', 'RFOOT|', 'RTEXT|', 'XTEXT|'])
            if kind == 'RFOOT|':
                return (kind + rng.choice(['P', 'F', '', 'X']) + '|' + field(60) + '|' + field(60))[:253]
            size = rng.choice(['S', 'M', 'L', 'X', 'Q', '', 'MM'])
            row = rng.choice(['0', '1', '5', '23', '63', '64', '-1', '99999999999', 'x', ''])
            flag = rng.choice(['S', '-', '', 'SS'])
            return (kind + size + '|' + row + '|' + flag + '|' + field(rng.choice([0, 10, 60, 240])))[:253]

        lines = []
        for i in range(2500):
            frames = [frame() for _ in range(rng.randint(1, 5))]
            lines.append('p%d\t%s\n' % (i, '\t'.join(frames)))
        lines.append('max1\tRTEXT|S|0|S|' + '\xb7'.join('i' * 4 for _ in range(48)) + '\n')
        lines.append('max2\tRTEXT|X|0|S|' + 'W' * 236 + '\n')
        lines.append('max3\tRTEXT|M|0|S|' + '\xb7' * 236 + '\n')
        env = dict(os.environ, ASAN_OPTIONS='halt_on_error=1:detect_leaks=0', UBSAN_OPTIONS='halt_on_error=1')
        done = subprocess.run([exe, '-'], input=''.join(lines).encode('latin-1'), capture_output=True, env=env)
        err = done.stderr.decode('latin-1')
        self.assertEqual(done.returncode, 0, err[:1200])
        self.assertNotIn('AddressSanitizer', err)
        self.assertNotIn('runtime error', err)


if __name__ == '__main__':
    unittest.main()
