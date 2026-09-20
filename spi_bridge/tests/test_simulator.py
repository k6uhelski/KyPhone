"""
test_simulator.py — pixel checks on the emulator's list and thread screens.

Draws real frames with pygame's headless driver and asserts on the pixels, against
the geometry in docs/02-design/design_handoff_os_0_2/GEOMETRY.md. Skipped when
pygame is not installed (the state-machine tests do not need it).

    ~/.venvs/kyphone/bin/python -m pytest spi_bridge/tests/test_simulator.py -v
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')

try:
    import pygame
    _REAL = not isinstance(pygame, MagicMock)
except ImportError:                                   # pragma: no cover
    pygame, _REAL = None, False

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))
if _REAL:
    import simulator as sim_module                    # noqa: E402

CELL = '\xb7'
BLACK, WHITE = (0, 0, 0), (255, 255, 255)


@unittest.skipUnless(_REAL, 'needs real pygame (python -m venv, pip install pygame)')
class SimulatorPixels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sim = sim_module.Simulator(lambda k: None)
        cls.sim.init()

    def draw(self, wire):
        self.sim._surface.fill(WHITE)
        self.sim._draw(wire)
        return self.sim._surface

    def px(self, x, y):
        return tuple(self.sim._surface.get_at((x, y)))[:3]

    def ink(self):
        """Number of black pixels on screen."""
        s = self.sim._surface
        return sum(1 for x in range(0, 600, 2) for y in range(0, 600, 2) if tuple(s.get_at((x, y)))[:3] == BLACK)

    def inverted_rows(self, tops):
        return [i for i, y in enumerate(tops) if self.px(4, y + 4) == BLACK]

    # ── lists ────────────────────────────────────────────────────────────────
    def texts_wire(self, sel, n=5):
        rows = '|'.join(f'Name{i}{CELL}preview {i}{CELL}0{CELL}9:0{i} AM' for i in range(n))
        return f'TEXTS|{sel}|{rows}'

    def test_texts_rows_are_111px_and_only_the_selected_one_is_inverted(self):
        tops = [44 + i * 111 for i in range(5)]
        for sel in range(5):
            self.draw(self.texts_wire(sel))
            self.assertEqual(self.inverted_rows(tops), [sel])

    def test_an_empty_texts_list_can_show_the_plus_control_selected(self):
        self.draw('TEXTS|-2')                                         # what the OS sends for an empty list
        self.assertEqual(self.px(548, 8), BLACK)                      # the + box (546..583 x 6..39) is filled
        self.assertEqual(self.px(18, 8), WHITE)                       # the back box is not
        self.draw('TEXTS|-1')
        self.assertEqual(self.px(18, 8), BLACK)
        self.assertEqual(self.px(548, 8), WHITE)

    def test_texts_header_selection_inverts_no_row(self):
        self.draw(self.texts_wire(-1))
        self.assertEqual(self.inverted_rows([44 + i * 111 for i in range(5)]), [])
        self.assertEqual(self.px(20, 20), BLACK)                      # the back control is inverted

    def test_contacts_rows_are_64px_and_the_footer_rule_sits_above_a_45px_strip(self):
        rows = '|'.join(f'C{i}{CELL}+1555010000{i}' for i in range(7))
        tops = [44 + i * 64 for i in range(7)]
        for sel in range(7):
            self.draw(f'CONTACTSPICK|{sel}||{sel + 1} / 14|{rows}')
            self.assertEqual(self.inverted_rows(tops), [sel])
        self.assertEqual(self.px(300, 553), BLACK)                    # 2px rule: y 553-554
        self.assertEqual(self.px(300, 554), BLACK)
        self.assertEqual(self.px(300, 552), WHITE)

    def test_calls_rows_are_92px(self):
        rows = '|'.join(f'Caller{i}{CELL}OUT{CELL}4:03 PM{CELL}12:04' for i in range(6))
        tops = [44 + i * 92 for i in range(6)]
        for sel in range(6):
            self.draw(f'CALLS|{sel}|{rows}')
            self.assertEqual(self.inverted_rows(tops), [sel])

    def test_empty_lists_draw_their_message_and_no_inverted_row(self):
        for wire in ('TEXTS|0', 'CONTACTSPICK|0||', 'CONTACTSPICK|0|zz|'):
            before = self.draw('STUB|X|Y') and self.ink()
            self.draw(wire)
            self.assertEqual(self.inverted_rows([44 + i * 64 for i in range(7)]), [], wire)
            self.assertGreater(self.ink(), 100, wire)                 # header + message are drawn

    # ── thread ───────────────────────────────────────────────────────────────
    def thread(self, code, draft='', hdr=''):
        return f'THREAD2|Pip|{draft}|{hdr}|R{CELL}6:52 PM{CELL}you close?|{code}{CELL}6:53 PM{CELL}yeah leaving now'

    def test_a_sent_bubble_is_filled_and_a_sending_one_is_not(self):
        self.draw(self.thread('Y0'))
        sending = self.ink()
        self.draw(self.thread('Y1'))
        sent = self.ink()
        self.assertGreater(sent, sending + 3000)                      # a filled bubble is a lot of ink

    def test_the_selected_not_sent_bubble_gains_a_ring(self):
        self.draw(self.thread('Y2'))
        plain = self.ink()
        self.draw(self.thread('Y3'))
        self.assertGreater(self.ink(), plain)

    def test_composer_grows_one_line_at_a_time_up_to_three(self):
        # rule: 2px, sitting just above the composer, which is 45 / 79 / 113px tall
        for draft, composer_h in (('short', 45), ('w ' * 20, 79), ('w ' * 40, 113)):
            self.draw(self.thread('Y1', draft=draft))
            rule_y = 600 - composer_h - 2
            self.assertEqual(self.px(595, rule_y), BLACK, (draft, composer_h))
            self.assertEqual(self.px(595, rule_y + 3), WHITE, (draft, composer_h))

    def test_the_cursor_is_hidden_while_a_bubble_or_the_header_is_selected(self):
        self.draw(self.thread('Y1'))
        with_cursor = self.ink()
        self.draw(self.thread('Y1', hdr='B'))
        header_selected = self.ink()
        self.assertNotEqual(with_cursor, header_selected)
        self.draw(self.thread('Y3'))
        cursor_px = [self.px(x, 575) for x in range(52, 68)]          # the block right after '> '
        self.assertNotIn(BLACK, cursor_px)

    def test_long_bubble_text_is_clipped_to_the_message_area_not_the_header(self):
        self.draw(self.thread('Y1').replace('yeah leaving now', 'word ' * 60))
        self.assertEqual(self.px(300, 30), WHITE)                     # nothing bleeds into the header strip
        self.assertEqual(self.px(4, 50), WHITE)

    # ── contact page ─────────────────────────────────────────────────────────
    def region_has_ink(self, x0, y0, x1, y1):
        return any(self.px(x, y) == BLACK for x in range(x0, x1) for y in range(y0, y1))

    def test_edit_control_is_only_on_a_saved_contact(self):
        self.draw('CONTACT|Alice Test|(555) 010-0001|S|C')
        self.assertTrue(self.region_has_ink(500, 6, 585, 40))
        self.draw('CONTACT|Sam Whitfield|NO NUMBER SAVED|N|A')
        self.assertTrue(self.region_has_ink(500, 6, 585, 40))
        self.draw('CONTACT|(555) 019-9002|NOT IN CONTACTS|U|C')
        self.assertFalse(self.region_has_ink(500, 6, 585, 40))                # nothing to edit yet

    def test_the_action_row_holds_call_text_save_for_an_unsaved_number(self):
        # buttons are 46px tall at y=360, 16px apart; widths are 18px per letter + 50
        call_x, text_x, save_x = 28, 28 + 122 + 16, 28 + 122 + 16 + 122 + 16
        for sel, filled_x in (('C', call_x), ('T', text_x), ('V', save_x)):
            self.draw(f'CONTACT|(555) 019-9002|NOT IN CONTACTS|U|{sel}')
            for x in (call_x, text_x, save_x):
                inner = self.px(x + 6, 366)                                   # just inside the 3px border
                self.assertEqual(inner, BLACK if x == filled_x else WHITE, (sel, x))

    def test_a_saved_contact_without_a_number_has_one_add_number_button(self):
        self.draw('CONTACT|Sam Whitfield|NO NUMBER SAVED|N|A')
        self.assertEqual(self.px(34, 366), BLACK)                             # ADD NUMBER, selected, filled
        self.assertEqual(self.px(28 + 230 + 16 + 8, 380), WHITE)              # no second button after it

    def test_the_rule_above_the_actions_is_2px_at_y330(self):
        self.draw('CONTACT|Alice Test|(555) 010-0001|S|C')
        self.assertEqual((self.px(300, 330), self.px(300, 331), self.px(300, 332)), (BLACK, BLACK, WHITE))

    # ── stop alerts, edit form title, New Message wrapping ───────────────────
    def test_ok_on_a_stop_alert_is_inverted_because_it_is_the_only_control(self):
        self.draw('STUB|CONTACT|A CONTACT NEEDS A FIRST NAME.')
        x, y = 600 - 24 - (2 * 12 + 36 + 6), 600 - 24 - 36                    # OK box (36px tall), bottom right
        self.assertEqual(self.px(x + 8, y + 8), BLACK)

    def test_the_edit_form_is_titled_new_or_edit_contact(self):
        self.draw('CONTACTEDIT||||0|N')
        new_form = pygame.image.tostring(self.sim._surface, 'RGB')
        self.draw('CONTACTEDIT||||0|E')
        self.assertNotEqual(new_form, pygame.image.tostring(self.sim._surface, 'RGB'))

    def test_the_new_message_text_wraps_at_30_columns_line_by_line(self):
        # message lines are 34px apart from y=162; a third line only exists for 61+ characters
        self.draw('COMPOSE|Alice|hi|0||0|0')
        self.assertFalse(self.region_has_ink(24, 236, 300, 262))
        self.draw('COMPOSE|Alice|' + 'ab ' * 25 + '|0||0|0')                    # 74 characters
        self.assertTrue(self.region_has_ink(24, 236, 300, 262))

    # ── delete button and the confirm screen ─────────────────────────────────
    def test_delete_is_only_on_an_edit_form_not_a_new_contact(self):
        self.draw('CONTACTEDIT|Pip|Okonkwo|(917) 555-0101|1|E')
        self.assertTrue(self.region_has_ink(24, 549, 140, 585))
        self.draw('CONTACTEDIT|Pip|Okonkwo|(917) 555-0101|1|N')
        self.assertFalse(self.region_has_ink(24, 549, 140, 585))

    def test_delete_button_inverts_when_selected(self):
        self.draw('CONTACTEDIT|Pip|Okonkwo|(917) 555-0101|4|E')
        self.assertEqual(self.px(30, 556), BLACK)                              # inside the 2px border: filled
        self.draw('CONTACTEDIT|Pip|Okonkwo|(917) 555-0101|1|E')
        self.assertEqual(self.px(30, 556), WHITE)

    def test_confirm_opens_with_the_safe_button_on_the_right_selected(self):
        keep_x = 600 - 24 - (len('KEEP') * 12 + 36 + 6)
        self.draw('CONFIRM|T|Body text here.|GO|KEEP|K')
        self.assertEqual(self.px(keep_x + 8, 548), BLACK)                      # safe button filled
        self.assertEqual(self.px(56 + 6, 548), WHITE)                          # destructive one is not
        self.draw('CONFIRM|T|Body text here.|GO|KEEP|D')
        self.assertEqual(self.px(keep_x + 8, 548), WHITE)
        self.assertEqual(self.px(56 + 6, 548), BLACK)

    def test_the_safe_button_has_the_heavier_border(self):
        keep_x = 600 - 24 - (len('KEEP') * 12 + 36 + 6)
        self.draw('CONFIRM|T|Body text here.|GO|KEEP|D')                       # neither filled the same way
        self.assertEqual((self.px(keep_x + 2, 556), self.px(keep_x + 3, 556)), (BLACK, WHITE))   # 3px border
        self.assertEqual((self.px(57, 556), self.px(58, 556)), (BLACK, BLACK))                    # (filled: selected)
        self.draw('CONFIRM|T|Body text here.|GO|KEEP|K')
        self.assertEqual((self.px(57, 556), self.px(58, 556)), (BLACK, WHITE))                    # 2px border

    # ── home menu ────────────────────────────────────────────────────────────
    def test_the_third_row_is_on_screen_without_scrolling_and_inverts_when_selected(self):
        # rows are 135px from y=62; the third row (READ) spans 332-467 and needs no scroll shift
        self.draw('HOME2|12:44 PM|2|3')
        self.assertEqual(self.px(4, 62 + 2 * 135 + 4), BLACK)
        self.assertEqual(self.px(4, 62 + 1 * 135 + 4), WHITE)
        self.assertEqual(self.px(4, 62 + 0 * 135 + 4), WHITE)

    def test_the_more_below_cue_shows_while_part_of_the_menu_is_below_the_fold(self):
        self.draw('HOME2|12:44 PM|0|3')
        self.assertEqual(self.px(580, 592), BLACK)                              # the widest of the three bars

    # ── home menu icons ──────────────────────────────────────────────────────
    def icon_matches(self, name, x, y, selected):
        """Is the 56x56 block at (x, y) exactly the icon bitmap (ink = paper on an inverted row)?"""
        from home_icons import ICONS
        ink, paper = (WHITE, BLACK) if selected else (BLACK, WHITE)
        for j, row in enumerate(ICONS[name]):
            for i in range(56):
                want = ink if (row >> (55 - i)) & 1 else paper
                if self.px(x + i, y + j) != want:
                    return False
        return True

    def test_icons_style_draws_each_bitmap_centred_and_39px_down_the_row(self):
        self.draw('HOME2|12:44 PM|0|3|I')                                        # TEXT selected, others not
        for i, name in enumerate(['TEXT', 'CALL', 'READ']):                    # the three rows on screen
            self.assertTrue(self.icon_matches(name, 272, 62 + i * 135 + 39, selected=(i == 0)), name)

    def test_the_last_two_rows_scroll_into_view_with_their_own_icons(self):
        # music (LISTEN) and the address book (CONTACTS) sit below the fold; once selected they ride the bottom of
        # the view, 504px down (top of the row 465 + 39).
        for index, name in ((3, 'LISTEN'), (4, 'CONTACTS')):
            self.draw('HOME2|12:44 PM|%d|0|I' % index)
            self.assertTrue(self.icon_matches(name, 272, 504, selected=True), name)

    def test_icons_and_words_share_the_row_with_a_28px_gap(self):
        self.draw('HOME2|12:44 PM|1|0|B')                                        # CALL selected
        x0 = (600 - (56 + 28 + 4 * 36)) // 2                                     # icon + 28 + "CALL"
        self.assertTrue(self.icon_matches('CALL', x0, 62 + 135 + 39, selected=True))

    def test_words_style_draws_no_icon(self):
        self.draw('HOME2|12:44 PM|1|0|W')
        self.assertFalse(self.icon_matches('CALL', 272, 62 + 135 + 39, selected=True))

    def test_the_unread_count_hangs_24px_right_of_the_icon(self):
        self.draw('HOME2|12:44 PM|0|3|I')
        self.assertTrue(self.region_has_ink(272 + 56 + 24, 62 + 55, 272 + 56 + 24 + 40, 62 + 80))   # white on the inverted row
        self.assertEqual(self.px(272 + 56 + 10, 62 + 67), BLACK)                                    # nothing in the 24px gap

    def test_the_more_below_cue_is_a_downward_funnel(self):
        self.draw('HOME2|12:44 PM|0|3|I')
        # 14, 8 and 3px bars, widest on top, centred, at y 581, 586 and 591 (the designer's capture)
        for y, x0, x1 in ((581, 574, 587), (586, 577, 584), (591, 580, 582)):
            self.assertTrue(all(self.px(x, y + 1) == BLACK for x in range(x0, x1 + 1)), (y, x0, x1))
            self.assertEqual(self.px(x0 - 1, y + 1), WHITE)
            self.assertEqual(self.px(x1 + 1, y + 1), WHITE)


CAPTURE = os.path.join(os.path.dirname(__file__), '..', '..', 'docs', '02-design', 'design_handoff_os_0_2',
                       'screens', '12_home_contacts_third.png')


class HomeIconsMatchTheDesign(unittest.TestCase):
    """The generated bitmaps against the designer's 600x600 capture of the home menu."""

    @unittest.skipUnless(_REAL and os.path.exists(CAPTURE), 'needs real pygame and the design handoff')
    def test_each_icon_overlaps_the_designers_pixels_by_at_least_98_percent(self):
        from home_icons import ICONS
        img = pygame.image.load(CAPTURE)
        lum = lambda x, y: sum(img.get_at((x, y))[:3]) / 3
        for r, name in enumerate(['TEXT', 'CALL', 'CONTACTS', 'READ']):        # LISTEN is not in the capture
            top = 62 + r * 135
            selected = lum(4, top + 4) < 110
            inter = union = 0
            for y in range(56):
                for x in range(56):
                    v = lum(272 + x, top + 39 + y)
                    cap = (v > 150) if selected else (v < 110)
                    mine = (ICONS[name][y] >> (55 - x)) & 1
                    inter += cap and mine
                    union += cap or mine
            self.assertGreaterEqual(inter / union, 0.98, name)

    def test_generated_files_are_up_to_date_with_the_generator(self):
        import make_icons
        self.assertEqual(make_icons.main(['--check']), 0)

    def test_the_menu_order_matches_the_os_and_the_renderers(self):
        import make_icons
        from home_icons import MENU_ORDER
        self.assertEqual(MENU_ORDER, make_icons.ORDER)
        self.assertEqual(MENU_ORDER, ['TEXT', 'CALL', 'READ', 'LISTEN', 'CONTACTS'])
        with open(os.path.join(os.path.dirname(__file__), '..', 'Inkplate_SPI_Peripheral', 'ui_screens.h')) as f:
            src = f.read()
        self.assertIn('{"TEXT", "CALL", "READ", "LISTEN", "CONTACTS"}', src)

    def test_every_icon_is_56_rows_of_56_bits_with_ink(self):
        from home_icons import ICONS
        for name, rows in ICONS.items():
            self.assertEqual(len(rows), 56, name)
            self.assertTrue(all(0 <= r < (1 << 56) for r in rows), name)
            self.assertGreater(sum(bin(r).count('1') for r in rows), 300, name)


class WrapParity(unittest.TestCase):
    """The simulator draws with its own copy of wrap_words; it must match the OS's."""
    def test_simulator_wrap_matches_the_os_wrap(self):
        import importlib.util
        os.environ['SDL_VIDEODRIVER'] = 'dummy'
        for name in ('spidev', 'gpiod', 'input_handler', 'twilio', 'twilio.rest', 'evdev'):
            sys.modules.setdefault(name, MagicMock())
        sys.argv = ['test', '--sim']
        import kyphone_os
        spec = importlib.util.spec_from_file_location('simulator_by_path',
                                                      os.path.join(os.path.dirname(__file__), '..', 'simulator.py'))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        samples = ['', 'hi', 'a ' + 'b' * 25, 'the quick brown fox jumps over the lazy dog ' * 4,
                   'x' * 100, '  leading and   double  spaces  ', 'w ' * 60]
        for text in samples:
            for cols in (10, 20, 30):
                self.assertEqual(mod.wrap_words(text, cols), kyphone_os.wrap_words(text, cols), (text, cols))


    def test_simulator_home_menu_matches_the_os(self):
        import importlib.util
        os.environ['SDL_VIDEODRIVER'] = 'dummy'
        for name in ('spidev', 'gpiod', 'input_handler', 'twilio', 'twilio.rest', 'evdev'):
            sys.modules.setdefault(name, MagicMock())
        sys.argv = ['test', '--sim']
        import kyphone_os
        spec = importlib.util.spec_from_file_location('simulator_by_path2',
                                                      os.path.join(os.path.dirname(__file__), '..', 'simulator.py'))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertEqual(mod.Simulator.HOME_MENU, kyphone_os.HOME_MENU)


@unittest.skipUnless(_REAL, 'needs real pygame (python -m venv, pip install pygame)')
class ReaderPixels(unittest.TestCase):
    """The reader draws book text with the panel's own FreeSerif glyphs, frame by frame, like the firmware."""

    @classmethod
    def setUpClass(cls):
        import reader_layout
        cls.rl = reader_layout
        cls.sim = sim_module.Simulator(lambda k: None)
        cls.sim.init()

    def blank(self):
        self.sim._surface.fill(WHITE)

    def black(self, y0=0, y1=600):
        """{(x, y)} of every black pixel in rows y0..y1-1."""
        data = pygame.image.tostring(self.sim._surface, 'RGB')
        return {(i // 3 % 600, i // 3 // 600) for i in range(y0 * 1800, y1 * 1800, 3) if data[i] == 0}

    def test_a_line_is_drawn_pixel_for_pixel_like_the_panels_glyphs(self):
        rl = self.rl
        for size in reader_fonts_sizes():
            self.blank()
            line = 'Hello, World! gjpqy AWVT (fi) "quoted" 0123456789'
            self.sim._draw('RTEXT|%s|3|S|%s' % (size, line))
            want = {(x, y) for x, y in rl.text_ink(size, rl.TEXT_X, rl.baseline(size, 3), line) if 0 <= x < 600 and 0 <= y < 600}
            self.assertEqual(self.black(), want, size)

    def test_lines_land_on_consecutive_rows(self):
        rl = self.rl
        self.blank()
        self.sim._draw('RTEXT|M|2|S|first\xb7\xb7third')
        want = set()
        for row, text in ((2, 'first'), (4, 'third')):
            want |= set(rl.text_ink('M', rl.TEXT_X, rl.baseline('M', row), text))
        self.assertEqual(self.black(), want)                        # row 3 is blank: a blank line draws nothing

    def test_the_first_frame_clears_the_screen_and_later_frames_add_to_it(self):
        self.blank()
        self.sim._draw('STUB|OLD|old screen text')
        self.assertGreater(len(self.black()), 100)
        self.sim._draw('RTEXT|M|0|S|one')
        only_one = self.black()
        self.assertTrue(all(y < 60 for _x, y in only_one))          # the old screen is gone; only the first row's text remains
        self.sim._draw('RTEXT|M|5|-|two')
        both = self.black()
        self.assertTrue(only_one < both)                            # the first line is still there

    def test_a_whole_page_draws_inside_the_column_and_clear_of_the_footer_rule(self):
        rl = self.rl
        import reader_epub
        text = ' '.join(['gypsy jumping quickly; Wizards Fly Over Big Dwarfs, Xylophones (Q) & "Zebras"'] * 400)
        for size in reader_fonts_sizes():
            page = rl.paginate([('p', text)], size)[0]
            self.blank()
            self.sim._draw(rl.page_frames(size, page.lines, 'A chapter', '1/9  3%', 'P'))
            ink = self.black()
            body = {(x, y) for x, y in ink if y < rl.FOOT_RULE_Y}
            self.assertTrue(body)
            self.assertGreaterEqual(min(x for x, _y in body), rl.TEXT_X - 4, size)
            self.assertLess(max(x for x, _y in body), rl.TEXT_X + rl.TEXT_W + 4, size)
            self.assertFalse({(x, y) for x, y in ink if rl.TEXT_BOTTOM <= y < rl.FOOT_RULE_Y}, size)   # the gap above the rule

    def test_the_footer_has_a_rule_and_text_at_both_ends(self):
        rl = self.rl
        self.blank()
        self.sim._draw('RFOOT|P|CHAPTER ONE|12/40  35%')
        ink = self.black()
        rule = {(x, y) for x, y in ink if y == rl.FOOT_RULE_Y}
        self.assertEqual({x for x, _y in rule}, set(range(rl.TEXT_X, rl.TEXT_X + rl.TEXT_W)))
        text = {(x, y) for x, y in ink if y > rl.FOOT_RULE_Y}
        self.assertLess(min(x for x, _y in text), rl.TEXT_X + 30)                    # left text starts at the margin
        self.assertLessEqual(max(x for x, _y in text), rl.TEXT_X + rl.TEXT_W)         # right text ends at the margin
        self.assertGreater(max(x for x, _y in text), rl.TEXT_X + rl.TEXT_W - 40)
        self.assertFalse({(x, y) for x, y in ink if y < rl.FOOT_RULE_Y})

    def test_the_refresh_kind_of_each_page_is_recorded(self):
        self.sim.refreshes.clear()
        self.sim._draw(['RTEXT|M|0|S|x', 'RFOOT|F|a|b'])
        self.sim._draw(['RTEXT|M|0|S|x', 'RFOOT|P|a|b'])
        self.assertEqual(self.sim.refreshes, ['F', 'P'])

    def test_a_frame_the_panel_could_not_parse_draws_nothing_and_does_not_crash(self):
        self.blank()
        for wire in ('RTEXT|Q|0|S|text', 'RTEXT|M|x|S|text', 'RTEXT|M', 'RTEXT|', 'RFOOT|', 'RFOOT|P'):
            self.sim._draw(wire)
        self.assertLess(len(self.black(0, 560)), 1)

    def test_a_footer_can_be_missing_its_right_hand_text(self):
        self.blank()
        self.sim._draw('RFOOT|P|only left')
        self.assertTrue(self.black(self.rl.FOOT_RULE_Y + 1))

    # ── the library ──────────────────────────────────────────────────────────
    def library(self, sel, n=5):
        rows = '|'.join('Book %d%sAuthor %d%s%d%%' % (i, CELL, i, CELL, i * 10) for i in range(n))
        return 'LIBRARY|%d|%s' % (sel, rows)

    def px(self, x, y):
        return tuple(self.sim._surface.get_at((x, y)))[:3]

    def test_library_rows_are_111px_like_the_texts_list_and_only_the_selected_one_is_inverted(self):
        tops = [44 + i * 111 for i in range(5)]
        for sel in range(5):
            self.blank()
            self.sim._draw(self.library(sel))
            self.assertEqual([i for i, y in enumerate(tops) if self.px(4, y + 4) == BLACK], [sel])

    def test_library_header_selection_inverts_no_row(self):
        self.blank()
        self.sim._draw(self.library(-1))
        self.assertEqual([i for i in range(5) if self.px(4, 44 + i * 111 + 4) == BLACK], [])
        self.assertEqual(self.px(20, 20), BLACK)                                      # the back control

    def test_the_library_shows_progress_and_a_chevron_at_the_right_of_each_row(self):
        self.blank()
        self.sim._draw(self.library(-1, n=2))
        right = {(x, y) for x, y in self.black(44, 44 + 111) if x > 470}
        self.assertTrue(right)

    def test_an_empty_library_says_so(self):
        self.blank()
        self.sim._draw('LIBRARY|-1')
        self.assertGreater(len(self.black(100, 600)), 100)
        self.assertFalse([1 for i in range(5) if self.px(4, 44 + i * 111 + 4) == BLACK])


@unittest.skipUnless(_REAL, 'needs real pygame (python -m venv, pip install pygame)')
class MusicPixels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sim = sim_module.Simulator(lambda k: None)
        cls.sim.init()

    def draw(self, wire):
        self.sim._surface.fill(WHITE)
        self.sim._draw(wire)

    def px(self, x, y):
        return tuple(self.sim._surface.get_at((x, y)))[:3]

    def ink_in(self, x0, y0, x1, y1):
        return sum(1 for x in range(x0, x1) for y in range(y0, y1) if self.px(x, y) == BLACK)

    def rows(self, n=4, sel=1):
        r = lambda *f: CELL.join(f)
        return 'MUSIC|%d|' % sel + '|'.join(r('Album %d' % i, 'Artist %d' % i, '%d trk' % (i + 3)) for i in range(n))

    # ── the lists ────────────────────────────────────────────────────────────
    def test_music_rows_are_111px_and_only_the_selected_one_is_inverted(self):
        tops = [44 + i * 111 for i in range(5)]
        for sel in range(4):
            self.draw(self.rows(4, sel))
            self.assertEqual([i for i, y in enumerate(tops) if self.px(4, y + 4) == BLACK], [sel])

    def test_the_music_header_is_back_only_and_the_back_control_inverts(self):
        self.draw(self.rows(3, -1))
        self.assertEqual(self.px(20, 20), BLACK)                       # the back box
        self.assertEqual(self.ink_in(500, 6, 585, 40), 0)              # no + control
        self.assertEqual([self.px(4, 44 + i * 111 + 4) for i in range(3)], [WHITE] * 3)

    def test_an_empty_music_list_says_so(self):
        self.draw('MUSIC|-1')
        self.assertGreater(self.ink_in(0, 100, 600, 600), 100)

    def test_the_track_list_shows_the_album_name_in_the_header(self):
        r = lambda *f: CELL.join(f)
        self.draw('TRACKS|0|Pastel Blues|' + '|'.join([r('One', 'Artist', '3:05'), r('Two', 'Artist', '10:21')]))
        self.assertGreater(self.ink_in(150, 8, 450, 38), 100)          # the title sits centred in the header strip
        self.assertEqual(self.px(4, 48), BLACK)                        # first row selected
        self.assertEqual(self.px(4, 44 + 111 + 4), WHITE)

    # ── now playing ──────────────────────────────────────────────────────────
    def now(self, state='P', title='A Song', artist='An Artist', album='An Album', elapsed=0, total=200, vol=40, pos='1/9'):
        return 'NOWPLAYING|%s|%s|%s|%s|%d|%d|%d|%s' % (state, title, artist, album, elapsed, total, vol, pos)

    def test_a_rule_separates_the_status_line_from_the_song(self):
        self.draw(self.now())
        self.assertTrue(all(self.px(x, 62) == BLACK for x in range(0, 600)))
        self.assertEqual(self.px(300, 61), WHITE)

    def test_the_progress_bar_has_an_outline_and_fills_in_proportion(self):
        self.draw(self.now(elapsed=100, total=200))
        self.assertEqual((self.px(28, 296), self.px(571, 311)), (BLACK, BLACK))            # the outline's corners
        self.assertEqual(self.px(30, 297), BLACK)
        self.assertEqual(self.px(200, 302), BLACK)                                        # filled up to half of 540: x 30..299
        self.assertEqual(self.px(299, 302), BLACK)
        self.assertEqual(self.px(320, 302), WHITE)
        self.draw(self.now(elapsed=0, total=200))
        self.assertEqual(self.px(200, 302), WHITE)
        self.draw(self.now(elapsed=200, total=200))
        self.assertEqual(self.px(560, 302), BLACK)

    def test_an_unknown_length_shows_an_empty_bar_and_dashes(self):
        self.draw(self.now(elapsed=50, total=0))
        self.assertEqual(self.px(200, 302), WHITE)
        self.assertGreater(self.ink_in(440, 328, 575, 348), 20)                             # the "--:--" at the right

    def test_the_bar_never_overflows_if_the_time_passes_the_length(self):
        self.draw(self.now(elapsed=999, total=200))
        self.assertEqual(self.px(565, 302), BLACK)
        self.assertEqual(self.px(575, 302), WHITE)                                          # outside the outline

    def test_the_volume_bar_fills_in_proportion(self):
        for vol, inside, outside in ((0, None, 100), (50, 250, 400), (100, 500, None)):
            self.draw(self.now(vol=vol))
            if inside is not None:
                self.assertEqual(self.px(inside, 550), BLACK, vol)
            if outside is not None:
                self.assertEqual(self.px(outside, 550), WHITE, vol)

    def test_the_middle_control_changes_between_playing_and_paused(self):
        self.draw(self.now('P'))
        playing = self.ink_in(240, 395, 360, 445)
        self.draw(self.now('U'))
        paused = self.ink_in(240, 395, 360, 445)
        self.assertGreater(playing, 100)
        self.assertGreater(paused, 50)
        self.assertNotEqual(playing, paused)
        self.assertGreater(self.ink_in(80, 395, 160, 445), 100)                             # previous
        self.assertGreater(self.ink_in(440, 395, 520, 445), 100)                            # next

    def test_the_status_word_follows_the_state(self):
        counts = {}
        for state in 'PUS':
            self.draw(self.now(state))
            counts[state] = self.ink_in(20, 25, 200, 50)
        self.assertGreater(min(counts.values()), 30)
        self.assertEqual(len(set(counts.values())), 3)                                      # PLAYING, PAUSED, FINISHED differ

    def test_a_long_title_wraps_to_at_most_three_lines_above_the_artist(self):
        self.draw(self.now(title='Word ' * 11))                                             # 55 characters
        self.assertGreater(self.ink_in(20, 160, 580, 186), 20)                              # a third line
        self.assertEqual(self.ink_in(0, 190, 600, 205), 0)                                  # and a gap before the artist

    def test_a_short_title_uses_one_line(self):
        self.draw(self.now(title='Short'))
        self.assertGreater(self.ink_in(20, 95, 200, 122), 20)
        self.assertEqual(self.ink_in(0, 130, 600, 190), 0)

    def test_garbled_fields_do_not_crash(self):
        for wire in ('NOWPLAYING', 'NOWPLAYING|P', 'NOWPLAYING|P|t|a|b|x|y|z|q', 'NOWPLAYING|||||||', 'MUSIC', 'TRACKS', 'TRACKS|x'):
            self.draw(wire)

    def test_times_are_shown_as_minutes_and_seconds(self):
        self.assertEqual([self.sim._clock(x) for x in (0, -5, 5, 65, 621, 3599, 3723)], ['--:--', '--:--', '0:05', '1:05', '10:21', '59:59', '1:02:03'])

    # ── the lock screen version label ────────────────────────────────────────
    def test_the_lock_screen_shows_the_version_it_is_sent_bottom_left_and_none_when_not_sent(self):
        def marks(x0, y0, x1, y1):                        # anti-aliased text: count every non-white pixel
            return sum(1 for x in range(x0, x1) for y in range(y0, y1) if self.px(x, y) != WHITE)
        self.draw('LOCK|12:00 PM|SATURDAY, SEPTEMBER 19|Smile.|- THICH NHAT HANH|0.3.0')
        with_version = marks(8, 574, 130, 596)
        self.draw('LOCK|12:00 PM|SATURDAY, SEPTEMBER 19|Smile.|- THICH NHAT HANH|9.9.9')
        other = marks(8, 574, 130, 596)
        self.draw('LOCK|12:00 PM|SATURDAY, SEPTEMBER 19|Smile.|- THICH NHAT HANH')
        without = marks(8, 574, 130, 596)
        self.assertGreater(with_version, 40)
        self.assertEqual(without, 0)                                                        # an old Radxa sends none: no label
        self.assertNotEqual(with_version, other)                                            # it draws what it is told

    # ── the home menu mark ───────────────────────────────────────────────────
    def test_the_playing_mark_stands_beside_the_music_icon_and_only_when_playing(self):
        self.draw('HOME2|12:44 PM|3|0|I|1')                                                 # LISTEN selected: white on black
        self.assertEqual(self.px(354, 540), WHITE)
        self.assertEqual(self.px(374, 540), WHITE)
        self.draw('HOME2|12:44 PM|3|0|I|0')
        self.assertEqual(self.px(354, 540), BLACK)                                          # no mark: just the fill
        self.draw('HOME2|12:44 PM|0|0|I|1')                                                 # LISTEN below the fold, unselected
        self.assertEqual(self.px(354, 540), BLACK)
        self.draw('HOME2|12:44 PM|0|0|I|0')
        self.assertEqual(self.px(354, 540), WHITE)

    def test_the_mark_is_on_the_music_row_only(self):
        self.draw('HOME2|12:44 PM|-1|0|I|1')                                              # header selected: no row is filled
        for row in range(3):
            self.assertEqual(self.ink_in(345, 62 + row * 135 + 40, 385, 62 + row * 135 + 100), 0, row)

    def test_an_old_home_command_without_the_flag_still_draws(self):
        self.draw('HOME2|12:44 PM|0|3|I')
        self.draw('HOME2|12:44 PM|0|3')


def reader_fonts_sizes():
    import reader_fonts
    return reader_fonts.SIZES


if __name__ == '__main__':
    unittest.main()
