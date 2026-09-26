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
from screens import SCREENS, r                  # noqa: E402

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

    def test_an_empty_texts_list_shows_the_plus_control_selected(self):
        f = self.frames['texts_empty']                             # TEXTS|-2
        self.assertTrue(self.ink(f, 548, 8))                       # the + box (546..583 x 6..39) is filled
        self.assertFalse(self.ink(f, 18, 8))                       # the back box is not
        self.assertGreater(self.ink_count(f), 300)                 # and the NO CONVERSATIONS message is still drawn

    # ── music ──────────────────────────────────────────────────────────────────
    def test_music_rows_are_111px_and_only_the_selected_one_is_inverted(self):
        f = self.frames['music']                                     # sel = 0 (NOW PLAYING)
        self.assertEqual([self.ink(f, 4, 44 + row * 111 + 4) for row in range(3)], [True, False, False])
        self.assertFalse(self.has_ink(f, 500, 6, 585, 40))           # back only

    def test_an_empty_music_list_says_so(self):
        f = self.frames['music_empty']
        self.assertGreater(self.ink_count(f), 300)
        self.assertFalse(any(self.ink(f, 4, y) for y in (60, 120, 200, 300, 400)))
        self.assertTrue(self.ink(f, 20, 20))                          # the back control is selected

    def test_the_track_list_shows_the_album_in_the_header_and_row_two_selected(self):
        f = self.frames['tracks']
        self.assertTrue(self.has_ink(f, 150, 8, 450, 38))
        self.assertEqual([self.ink(f, 4, 44 + row * 111 + 4) for row in range(3)], [False, True, False])

    def test_now_playing_has_a_rule_and_bars_that_fill_in_proportion(self):
        def now(state='P', elapsed=0, total=200, vol=40, title='A Song'):
            return 'NOWPLAYING|%s|%s|An Artist|An Album|%d|%d|%d|1/9' % (state, title, elapsed, total, vol)
        f = self.frame(now(elapsed=100))
        self.assertTrue(all(self.ink(f, x, 62) for x in range(600)))
        self.assertFalse(self.ink(f, 300, 61))
        self.assertTrue(self.ink(f, 28, 296) and self.ink(f, 571, 311))                      # the outline's corners
        self.assertTrue(self.ink(f, 299, 302) and not self.ink(f, 320, 302))                  # half of 540: x 30..299
        self.assertFalse(self.ink(self.frame(now(elapsed=0)), 200, 302))
        self.assertTrue(self.ink(self.frame(now(elapsed=999)), 565, 302))                     # clamped inside the outline
        self.assertFalse(self.ink(self.frame(now(elapsed=999)), 575, 302))
        self.assertFalse(self.ink(self.frame(now(elapsed=50, total=0)), 200, 302))            # unknown length: no fill
        for vol, inside, outside in ((0, None, 100), (50, 250, 400), (100, 500, None)):
            g = self.frame(now(vol=vol))
            if inside is not None:
                self.assertTrue(self.ink(g, inside, 550), vol)
            if outside is not None:
                self.assertFalse(self.ink(g, outside, 550), vol)

    def test_now_playing_controls_status_and_title_wrapping(self):
        def now(state, title='A Song'):
            return 'NOWPLAYING|%s|%s|An Artist|An Album|10|200|40|1/9' % (state, title)
        def region(f, x0, y0, x1, y1):
            return bytes(f[y * W + x] for y in range(y0, y1) for x in range(x0, x1))
        seen = {}
        for state in 'PUS':
            f = self.frame(now(state))
            seen[state] = (region(f, 20, 25, 200, 50), region(f, 240, 395, 360, 445))
        self.assertEqual(len({v[0] for v in seen.values()}), 3)                             # PLAYING / PAUSED / FINISHED
        self.assertNotEqual(seen['P'][1], seen['U'][1])                                      # II versus >
        self.assertEqual(seen['U'][1], seen['S'][1])                                         # paused and finished both offer >
        long = self.frame(now('P', 'Word ' * 11))
        self.assertGreater(self.ink_box(long, 20, 160, 580, 186), 20)                        # a third title line
        self.assertEqual(self.ink_box(long, 0, 190, 600, 205), 0)                            # and a gap before the artist

    def ink_box(self, f, x0, y0, x1, y1):
        return sum(1 for x in range(x0, x1) for y in range(y0, y1) if self.ink(f, x, y))

    def test_the_playing_mark_stands_beside_the_music_icon(self):
        selected, unselected = self.frames['home_playing'], self.frames['home_playing_unselected']
        self.assertFalse(self.ink(selected, 354, 540))                                       # white bar on the inverted row
        self.assertFalse(self.ink(selected, 374, 540))
        self.assertTrue(self.ink(unselected, 354, 540))                                      # black bar on white
        self.assertTrue(self.ink(unselected, 374, 540))
        for row in range(3):
            self.assertEqual(self.ink_box(self.frame('HOME2|12:44 PM|-1|0|I|1'), 345, 62 + row * 135 + 40, 385, 62 + row * 135 + 100), 0)

    # ── settings ───────────────────────────────────────────────────────────────
    def test_settings_and_network_lists_are_111px_rows_with_the_selection_inverted(self):
        for name, sel, rows in (('settings', 0, 2), ('netlist_wifi', 2, 5), ('netlist_bt', 1, 4), ('netlist_pair', 0, 2),
                                ('netlist_off', 0, 1)):
            f = self.frames[name]
            self.assertEqual([self.ink(f, 4, 44 + r * 111 + 4) for r in range(rows)], [r == sel for r in range(rows)], name)
            if rows < 5:
                self.assertFalse(self.ink(f, 4, 44 + rows * 111 + 4), name)         # nothing below the last row
            self.assertFalse(self.has_ink(f, 500, 6, 585, 40), name)                # back only: no + control

    def test_the_network_list_title_follows_the_kind(self):
        def header(f):
            return bytes(f[y * W + x] for y in range(8, 38) for x in range(150, 450))
        titles = {header(self.frames[n]) for n in ('netlist_wifi', 'netlist_bt', 'netlist_pair')}
        self.assertEqual(len(titles), 3)                                             # WI-FI, BLUETOOTH, OTHER DEVICES

    def test_an_empty_search_shows_its_message_under_search_again(self):
        f = self.frames['netlist_empty']
        self.assertTrue(self.ink(f, 4, 48))                                          # the switch, selected
        self.assertFalse(self.ink(f, 4, 44 + 111 + 4))                               # SEARCH AGAIN, not selected
        self.assertGreater(self.ink_box(f, 28, 110 + 111, 400, 124 + 111), 0)        # its second line

    def test_the_password_box_has_a_cursor_until_the_back_arrow_is_selected(self):
        field, back = self.frames['netpass'], self.frames['netpass_back']
        mask_end = 24 + 7 * 18
        self.assertTrue(self.ink(field, mask_end + 9, 172))                          # the block cursor after the mask
        self.assertFalse(self.ink(back, mask_end + 9, 172))
        self.assertFalse(self.ink(field, 20, 8))                                     # < plain
        self.assertTrue(self.ink(back, 20, 8))                                       # < box filled
        self.assertTrue(self.ink(field, 300, 122) and not self.ink(field, 300, 123)) # the rule between the fields

    def test_only_a_failed_connection_has_the_heading(self):
        for name, heading in (('netstate_working', False), ('netstate_ok', False), ('netstate_fail', True)):
            self.assertEqual(self.ink_box(self.frames[name], 0, 200, 600, 236) > 0, heading, name)
            self.assertGreater(self.ink_box(self.frames[name], 0, 262, 600, 290), 0, name)   # the detail line
            self.assertGreater(self.ink_box(self.frames[name], 0, 548, 600, 568), 0, name)   # the hint

    def test_the_light_bar_fills_one_box_per_level_and_bad_levels_are_clamped(self):
        for wire, level in (('LIGHTSET|0', 0), ('LIGHTSET|5', 5), ('LIGHTSET|8', 8), ('LIGHTSET|99', 8),
                            ('LIGHTSET|-3', 0), ('LIGHTSET|x', 0), ('LIGHTSET|', 0)):
            f = self.frame(wire)
            self.assertEqual([self.ink(f, 60 + i * 60 + 25, 325) for i in range(8)], [i < level for i in range(8)], wire)
            self.assertTrue(self.ink(f, 60 + 7 * 60 + 1, 301), wire)                 # every box has its outline

    def test_the_notes_list_has_a_plus_and_two_line_rows(self):
        f = self.frames['notes']
        self.assertEqual([self.ink(f, 4, 44 + r * 111 + 4) for r in range(3)], [False, True, False])
        self.assertTrue(self.has_ink(f, 546, 6, 584, 40))                            # the + control
        self.assertFalse(self.ink(f, 548, 8))                                         # not selected
        e = self.frames['notes_empty']
        self.assertTrue(self.ink(e, 548, 8))                                          # an empty list: + selected
        self.assertGreater(self.ink_count(e), 300)                                    # NO NOTES

    def test_the_note_editor_draws_lines_the_cursor_and_its_header(self):
        f, b, d = self.frames['note'], self.frames['note_back'], self.frames['note_delete']
        cursor_x = 24 + len('and bread') * 18 + 9
        self.assertTrue(self.ink(f, cursor_x, 60 + 38 * 4 + 12))                       # the cursor after the last line
        self.assertEqual(self.ink_box(f, 24, 60 + 38 * 3, 300, 60 + 38 * 3 + 24), 0)   # the blank line stays blank
        self.assertFalse(self.ink(b, 24 + 4 * 18 + 9, 60 + 38 + 12))                   # no cursor while < is selected
        self.assertTrue(self.ink(b, 20, 8) and not self.ink(f, 20, 8))                 # < selected
        self.assertTrue(self.ink(d, 600 - 16 - 92 + 2, 8) and not self.ink(f, 600 - 16 - 92 + 2, 8))   # DELETE
        self.assertTrue(self.ink(f, 300, 43))                                           # the header rule

    def test_the_last_call_line_sits_under_the_header_and_is_not_a_bubble(self):
        f = self.frames['thread_call']
        self.assertGreater(self.ink_box(f, 100, 58, 500, 73), 50)                     # LAST CALL: ... text
        self.assertFalse(self.ink(f, 4, 70))                                            # no bubble or rule there
        plain = self.frame(SCREENS['thread_call'][0].replace(r('C', '', 'LAST CALL: MISSED, YESTERDAY') + '|', ''))
        self.assertEqual(self.ink_box(plain, 100, 58, 500, 73), 0)

    def test_the_upload_screen_shows_the_code_large_and_what_arrived(self):
        e, g = self.frames['upload_empty'], self.frames['upload_got']
        self.assertGreater(self.ink_box(e, 200, 224, 400, 280), 800)                   # the big code
        self.assertTrue(self.ink(e, 300, 318))                                          # the rule
        self.assertGreater(self.ink_box(g, 24, 364 + 68, 400, 364 + 68 + 24), 50)      # the third received line
        self.assertEqual(self.ink_box(e, 24, 364 + 68, 400, 364 + 68 + 24), 0)          # (empty: NOTHING YET only)

    def test_home_can_select_settings(self):
        self.assertGreater(self.ink_count(self.frames['home_settings']), 300)

    def test_an_old_home_command_without_the_flag_still_draws(self):
        self.assertGreater(self.ink_count(self.frame('HOME2|12:44 PM|0|3|I')), 300)

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

    def test_the_new_message_plus_is_centred_under_the_x_and_the_to_line_has_the_cursor(self):
        f = self.frames['compose_empty']                                      # empty To, To active, nothing selected
        cols = lambda y0, y1: [x for x in range(530, 600) if any(self.ink(f, x, y) for y in range(y0, y1))]
        x, plus = cols(4, 42), cols(70, 118)
        self.assertEqual((x[0] + x[-1]) // 2, (plus[0] + plus[-1]) // 2)
        self.assertEqual(x[-1] - x[0], plus[-1] - plus[0])
        self.assertFalse(self.ink(f, 23, 57))                                 # the TO: label is not filled
        self.assertTrue(self.ink(f, 30, 96))                                  # the cursor is at the start of the empty line
        typed = self.frame('COMPOSE|555|hi|1||0|0')
        self.assertFalse(self.ink(typed, 23, 83))                             # no bar behind the digits
        self.assertTrue(self.ink(typed, 24 + 3 * 18 + 4, 96))                 # the block cursor right after them
        self.assertFalse(self.ink(typed, 24 + 4 * 18 + 4, 96))
        self.assertFalse(self.ink(self.frame('COMPOSE||hi|1||1|0'), 30, 96))   # the + selected: no cursor

    # ── contact pages and the edit form ────────────────────────────────────────
    def has_ink(self, f, x0, y0, x1, y1):
        return any(self.ink(f, x, y) for x in range(x0, x1) for y in range(y0, y1))

    def test_edit_is_only_offered_for_a_saved_contact(self):
        self.assertTrue(self.has_ink(self.frames['contact_saved'], 500, 6, 585, 40))
        self.assertTrue(self.has_ink(self.frames['contact_nonum'], 500, 6, 585, 40))
        self.assertFalse(self.has_ink(self.frames['contact_unsaved'], 500, 6, 585, 40))

    def test_an_unsaved_number_offers_call_and_text_with_create_contact_on_a_second_row(self):
        f = self.frames['contact_unsaved']                                    # CREATE CONTACT selected
        spots = [(28, 360), (28 + 122 + 16, 360), (28, 360 + 46 + 16)]       # CALL, TEXT; CREATE CONTACT wraps below
        self.assertEqual([self.ink(f, x + 6, y + 6) for x, y in spots], [False, False, True])
        self.assertTrue(self.has_ink(f, 28 + 302 - 6, 422 + 20, 28 + 302, 422 + 26))
        self.assertFalse(self.has_ink(f, 28 + 302 + 4, 422, 600, 470))

    def test_a_saved_contact_without_a_number_has_only_add_number(self):
        f = self.frames['contact_nonum']
        self.assertTrue(self.ink(f, 34, 366))
        self.assertFalse(self.has_ink(f, 28 + 230 + 20, 362, 500, 404))

    def test_delete_is_only_on_an_edit_form_and_inverts_when_selected(self):
        self.assertTrue(self.ink(self.frames['edit_delete'], 30, 556))
        self.assertFalse(self.ink(self.frames['edit_edit'], 30, 556))
        self.assertTrue(self.has_ink(self.frames['edit_edit'], 24, 549, 140, 585))       # present, not selected
        self.assertFalse(self.has_ink(self.frames['edit_new'], 24, 549, 140, 585))       # a new contact has none

    def test_home_row_three_is_read_and_on_screen_without_scrolling(self):
        f = self.frames['home_read']                                           # index 2
        self.assertEqual([self.ink(f, 4, 62 + i * 135 + 4) for i in range(3)], [False, False, True])

    def test_the_last_two_rows_scroll_into_view_with_their_own_icons(self):
        # music (LISTEN) and the address book (CONTACTS) sit below the fold; once selected they ride the bottom
        # of the view, 504px down (top of the row 465 + 39). A mismatched icon table would show the wrong icon here.
        for frame, name in (('home_listen', 'LISTEN'), ('home_contacts', 'CONTACTS')):
            self.assertTrue(self.icon_matches(self.frames[frame], name, 272, 504, True), name)

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
        for i, name in enumerate(['TEXT', 'CALL', 'READ']):                      # the three rows on screen
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
    NAMES = ['music', 'music_empty', 'tracks', 'nowplaying', 'nowplaying_paused', 'home_playing', 'home', 'home_read', 'home_listen', 'home_contacts', 'home_both', 'home_words', 'home_icons_end', 'texts', 'texts_empty', 'library', 'library_empty', 'contacts', 'calls', 'thread_sending', 'thread_retry',
             'compose_empty', 'alert_bad_number', 'confirm_delete', 'contact_saved', 'contact_unsaved', 'edit_new',
             'edit_delete', 'home_settings', 'settings', 'netlist_wifi', 'netlist_bt', 'netlist_empty',
             'netlist_scanning', 'netlist_off', 'netlist_pair', 'lightset_off', 'lightset_mid', 'home_notes',
             'notes', 'notes_empty', 'note', 'note_back', 'note_delete', 'note_long', 'thread_call', 'chapters', 'upload_empty',
             'upload_got', 'netpass', 'netpass_back', 'netstate_working', 'netstate_ok', 'netstate_fail']

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

    def test_the_bars_and_the_home_mark_agree_with_the_emulator_to_the_pixel(self):
        # Regions with no text in them, so the two renderers' different fonts cannot differ: the progress bar and the
        # volume bar (integer fill maths on both sides), and the equalizer mark beside the music icon.
        regions = {'nowplaying': [(20, 290, 580, 318), (88, 538, 512, 562)],
                   'nowplaying_paused': [(20, 290, 580, 318), (88, 538, 512, 562)],
                   'nowplaying_finished': [(20, 290, 580, 318), (88, 538, 512, 562)],
                   'home_playing': [(340, 500, 392, 560)], 'home_playing_unselected': [(340, 500, 392, 560)]}
        for name, boxes in regions.items():
            wire = SCREENS[name][0]
            fw, sim = self.firmware_frame(wire), self.sim_frame(wire)
            for x0, y0, x1, y1 in boxes:
                for y in range(y0, y1):
                    for x in range(x0, x1):
                        self.assertEqual(fw[y * W + x], sim[y * W + x], (name, x, y))

    def test_every_elapsed_fraction_fills_the_bar_the_same_in_both(self):
        for elapsed, total in ((1, 3), (2, 3), (7, 9), (100, 621), (620, 621), (1, 1000)):
            wire = 'NOWPLAYING|P|T|A|B|%d|%d|33|1/2' % (elapsed, total)
            fw, sim = self.firmware_frame(wire), self.sim_frame(wire)
            self.assertEqual([fw[302 * W + x] for x in range(20, 580)], [sim[302 * W + x] for x in range(20, 580)], (elapsed, total))

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
                    'CONTACTEDIT|', 'CONTACT|', 'LIBRARY|', 'MUSIC|', 'TRACKS|', 'NOWPLAYING|',
                    'SETTINGS|', 'NETLIST|', 'NETPASS|', 'NETSTATE|', 'LIGHTSET|', 'NOTES|', 'NOTE|', 'UPLOAD|', 'CHAPTERS|']
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
            'long6\tNOWPLAYING|P|' + 'T' * 56 + '|' + 'A' * 44 + '|' + 'B' * 44 + '|99999999|99999999|100|99/99\n',
            'long7\tMUSIC|4|' + '|'.join(('N' * 22 + '\xb7' + 's' * 24 + '\xb7' + '99 trk') for _ in range(5)) + '\n',
            'long8\tTRACKS|0|' + 'H' * 20 + '|' + '|'.join(('N' * 22 + '\xb7' + 's' * 24 + '\xb710:21') for _ in range(5)) + '\n',
            'long9\tNOWPLAYING|P|' + 'W' * 200 + '\n',
            'long10\tNETPASS|' + 'S' * 60 + '|' + '*' * 180 + '|\n',
            'long11\tNETSTATE|W|FAIL|' + 'x' * 230 + '\n',
            'long12\tNETSTATE|B|OK|' + ' '.join(['word'] * 45) + '\n',
            'long13\tNETLIST|W|4|' + '|'.join(('N' * 22 + '\xb7' + 's' * 17 + '\xb7CONNECTED') for _ in range(6)) + '\n',
            'long14\tNETLIST|\n',
            'long15\tNOTE||' + '\xb7'.join(['y' * 40] * 6) + '\n',
            'long16\tNOTE|B|' + '\xb7' * 30 + '\n',
            'long18\tTHREAD2|N|d||C\xb7\xb7' + 'L' * 120 + '|R\xb71\xb7' + 'w' * 60 + '\n',
            'long19\tTHREAD2|N|d||' + '|'.join(['C\xb7\xb7x'] * 8) + '\n',
            'long20\tUPLOAD|' + '9' * 60 + '|' + '8' * 30 + '|' + '\xb7'.join(['z' * 60] * 5) + '\n',
            'long17\tNOTES|4|' + '|'.join(('T' * 30 + '\xb7Yesterday\xb7') for _ in range(7)) + '\n',
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
