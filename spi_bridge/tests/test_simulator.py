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


if __name__ == '__main__':
    unittest.main()
