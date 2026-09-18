"""
test_state_machine.py — Unit tests for KyPhone OS 0.1 state machine.

Runs without hardware (SPI/GPIO) or a display. All SPI sends are mocked.

    python3 -m pytest spi_bridge/tests/test_state_machine.py -v
"""

import os
import sys
import threading
import unittest
from unittest.mock import MagicMock, patch

# ── Mock hardware modules before importing kyphone_os ─────────────────────────
sys.argv = ['test', '--sim']  # force SIM_MODE=True so hardware imports are skipped
sys.modules.setdefault('spidev', MagicMock())
sys.modules.setdefault('gpiod', MagicMock())
sys.modules.setdefault('input_handler', MagicMock())
sys.modules.setdefault('pygame', MagicMock())
sys.modules.setdefault('simulator', MagicMock())
sys.modules.setdefault('evdev', MagicMock())
_twilio_mock = MagicMock()
sys.modules.setdefault('twilio', _twilio_mock)
sys.modules.setdefault('twilio.rest', _twilio_mock)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import kyphone_os  # noqa: E402  (import after sys.path manipulation)

# ─────────────────────────────────────────────────────────────────────────────


def reset_state(**overrides):
    """Reset kyphone_os.state to a clean baseline (does not touch the Lock)."""
    defaults = {
        'screen': 'lock',
        'home_index': 0,
        'texts_index': 0,
        'texts_start': 0,
        'texts_header_sel': 'back',
        'contacts_query': '',
        'contacts_index': 0,
        'contacts_start': 0,
        'contacts_header_sel': 'back',
        'contacts_return': 'home',
        'calls': [],
        'calls_index': 0,
        'calls_start': 0,
        'thread_id': None,
        'thread_draft': '',
        'thread_header_sel': None,
        'compose_to': '',
        'compose_msg': '',
        'compose_to_active': True,
        'compose_header_sel': None,
        'quote_index': 0,
        'messages': [],
        'last_sid': None,
        'running': True,
    }
    defaults.update(overrides)
    for k, v in defaults.items():
        kyphone_os.state[k] = v


# ═══════════════════════════════════════════════════════════════════════════════
# Lock Screen
# ═══════════════════════════════════════════════════════════════════════════════

class TestLockScreen(unittest.TestCase):
    def setUp(self):
        reset_state(screen='lock')
        self._save_patch = patch.object(kyphone_os, 'save_messages')
        self._save_patch.start()

    def tearDown(self):
        self._save_patch.stop()

    @patch.object(kyphone_os, 'push_screen')
    def test_enter_goes_to_home(self, _ps):
        kyphone_os.handle_key('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'home')
        self.assertEqual(kyphone_os.state['home_index'], 0)

    @patch.object(kyphone_os, 'push_screen')
    def test_arrow_goes_to_home(self, _ps):
        kyphone_os.handle_key('KEY_DOWN')
        self.assertEqual(kyphone_os.state['screen'], 'home')

    @patch.object(kyphone_os, 'push_screen')
    def test_esc_goes_to_home(self, _ps):
        kyphone_os.handle_key('KEY_ESC')
        self.assertEqual(kyphone_os.state['screen'], 'home')


# ═══════════════════════════════════════════════════════════════════════════════
# Home Screen
# ═══════════════════════════════════════════════════════════════════════════════

class TestHomeScreen(unittest.TestCase):
    def setUp(self):
        reset_state(screen='home', home_index=0)
        self._save_patch = patch.object(kyphone_os, 'save_messages')
        self._save_patch.start()

    def tearDown(self):
        self._save_patch.stop()

    @patch.object(kyphone_os, 'push_screen')
    def test_down_increments_index(self, _ps):
        kyphone_os.handle_key('KEY_DOWN')
        self.assertEqual(kyphone_os.state['home_index'], 1)

    @patch.object(kyphone_os, 'push_screen')
    def test_up_decrements_index(self, _ps):
        reset_state(screen='home', home_index=2)
        kyphone_os.handle_key('KEY_UP')
        self.assertEqual(kyphone_os.state['home_index'], 1)

    @patch.object(kyphone_os, 'push_screen')
    def test_down_clamped_at_4(self, _ps):
        # OS 0.2 home menu has 5 rows (TEXT/CALL/READ/LISTEN/CONTACTS).
        reset_state(screen='home', home_index=4)
        kyphone_os.handle_key('KEY_DOWN')
        self.assertEqual(kyphone_os.state['home_index'], 4)

    @patch.object(kyphone_os, 'push_screen')
    def test_up_at_0_enters_header(self, _ps):
        kyphone_os.handle_key('KEY_UP')
        self.assertEqual(kyphone_os.state['home_index'], -1)

    @patch.object(kyphone_os, 'push_screen')
    def test_up_clamped_at_header(self, _ps):
        reset_state(screen='home', home_index=-1)
        kyphone_os.handle_key('KEY_UP')
        self.assertEqual(kyphone_os.state['home_index'], -1)

    @patch.object(kyphone_os, 'push_screen')
    def test_enter_on_header_goes_to_lock(self, _ps):
        reset_state(screen='home', home_index=-1, quote_index=5)
        kyphone_os.handle_key('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'lock')
        self.assertEqual(kyphone_os.state['quote_index'], 6)

    @patch.object(kyphone_os, 'push_screen')
    def test_enter_text_goes_to_texts_list(self, _ps):
        reset_state(screen='home', home_index=0)
        kyphone_os.handle_key('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'texts_list')
        self.assertEqual(kyphone_os.state['texts_index'], 0)

    @patch.object(kyphone_os, 'push_screen')
    def test_enter_read_goes_to_stub(self, _ps):
        reset_state(screen='home', home_index=2)
        kyphone_os.handle_key('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'stub')

    @patch.object(kyphone_os, 'push_screen')
    def test_enter_listen_goes_to_stub(self, _ps):
        reset_state(screen='home', home_index=3)
        kyphone_os.handle_key('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'stub')

    @patch.object(kyphone_os, 'push_screen')
    def test_esc_goes_to_lock_and_advances_quote(self, _ps):
        reset_state(screen='home', quote_index=5)
        kyphone_os.handle_key('KEY_ESC')
        self.assertEqual(kyphone_os.state['screen'], 'lock')
        self.assertEqual(kyphone_os.state['quote_index'], 6)


# ═══════════════════════════════════════════════════════════════════════════════
# Texts List Screen
# ═══════════════════════════════════════════════════════════════════════════════

# +1002 is at index 0 in messages → not the newest thread.
# +1001 sent the LAST message → newest → index 0 in the thread list.
_MSGS = [
    {'sender': '+1002', 'name': 'Bob',   'body': 'Hey',   'read': False},
    {'sender': '+1001', 'name': 'Alice', 'body': 'Hello', 'read': True},
]


class TestTextsListScreen(unittest.TestCase):
    def setUp(self):
        reset_state(screen='texts_list', texts_index=0, messages=list(_MSGS))
        self._save_patch = patch.object(kyphone_os, 'save_messages')
        self._save_patch.start()

    def tearDown(self):
        self._save_patch.stop()

    @patch.object(kyphone_os, 'push_screen')
    def test_down_increments_index(self, _ps):
        kyphone_os.handle_key('KEY_DOWN')
        self.assertEqual(kyphone_os.state['texts_index'], 1)

    @patch.object(kyphone_os, 'push_screen')
    def test_up_at_0_enters_header(self, _ps):
        kyphone_os.handle_key('KEY_UP')
        self.assertEqual(kyphone_os.state['texts_index'], -1)
        self.assertEqual(kyphone_os.state['texts_header_sel'], 'back')

    @patch.object(kyphone_os, 'push_screen')
    def test_right_in_header_goes_to_plus(self, _ps):
        reset_state(screen='texts_list', texts_index=-1, texts_header_sel='back', messages=list(_MSGS))
        kyphone_os.handle_key('KEY_RIGHT')
        self.assertEqual(kyphone_os.state['texts_header_sel'], 'plus')

    @patch.object(kyphone_os, 'push_screen')
    def test_left_in_header_goes_to_back(self, _ps):
        reset_state(screen='texts_list', texts_index=-1, texts_header_sel='plus', messages=list(_MSGS))
        kyphone_os.handle_key('KEY_LEFT')
        self.assertEqual(kyphone_os.state['texts_header_sel'], 'back')

    @patch.object(kyphone_os, 'push_screen')
    def test_enter_on_back_header_goes_home(self, _ps):
        reset_state(screen='texts_list', texts_index=-1, texts_header_sel='back', messages=list(_MSGS))
        kyphone_os.handle_key('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'home')

    @patch.object(kyphone_os, 'push_screen')
    def test_enter_on_plus_header_goes_compose(self, _ps):
        reset_state(screen='texts_list', texts_index=-1, texts_header_sel='plus', messages=list(_MSGS))
        kyphone_os.handle_key('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'compose')

    @patch.object(kyphone_os, 'push_screen')
    def test_plus_char_goes_compose(self, _ps):
        kyphone_os.handle_key('CHAR:+')
        self.assertEqual(kyphone_os.state['screen'], 'compose')

    @patch.object(kyphone_os, 'push_screen')
    def test_esc_goes_home(self, _ps):
        kyphone_os.handle_key('KEY_ESC')
        self.assertEqual(kyphone_os.state['screen'], 'home')

    @patch.object(kyphone_os, 'push_screen')
    def test_enter_on_row_goes_to_thread(self, _ps):
        kyphone_os.handle_key('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'thread')
        # texts_index=0 → newest thread = +1001 (Alice, last message in _MSGS)
        self.assertEqual(kyphone_os.state['thread_id'], '+1001')

    @patch.object(kyphone_os, 'push_screen')
    def test_enter_on_row_marks_read(self, _ps):
        reset_state(screen='texts_list', texts_index=1, messages=list(_MSGS))
        kyphone_os.handle_key('KEY_ENTER')
        # texts_index=1 → second thread = +1002 (Bob)
        bob_msgs = [m for m in kyphone_os.state['messages'] if m['sender'] == '+1002']
        self.assertTrue(all(m['read'] for m in bob_msgs))

    @patch.object(kyphone_os, 'push_screen')
    def test_down_in_header_goes_to_first_row(self, _ps):
        reset_state(screen='texts_list', texts_index=-1, texts_header_sel='back', messages=list(_MSGS))
        kyphone_os.handle_key('KEY_DOWN')
        self.assertEqual(kyphone_os.state['texts_index'], 0)


# ═══════════════════════════════════════════════════════════════════════════════
# Thread Screen
# ═══════════════════════════════════════════════════════════════════════════════

class TestThreadScreen(unittest.TestCase):
    def setUp(self):
        reset_state(screen='thread', thread_id='+1001', thread_draft='',
                    messages=[{'sender': '+1001', 'name': 'Alice', 'body': 'Hello', 'read': True}])
        self._save_patch = patch.object(kyphone_os, 'save_messages')
        self._save_patch.start()

    def tearDown(self):
        self._save_patch.stop()

    @patch.object(kyphone_os, 'push_screen')
    def test_char_appends_to_draft(self, _ps):
        kyphone_os.handle_key('CHAR:h')
        self.assertEqual(kyphone_os.state['thread_draft'], 'h')

    @patch.object(kyphone_os, 'push_screen')
    def test_multiple_chars_accumulate(self, _ps):
        kyphone_os.handle_key('CHAR:h')
        kyphone_os.handle_key('CHAR:i')
        self.assertEqual(kyphone_os.state['thread_draft'], 'hi')

    @patch.object(kyphone_os, 'push_screen')
    def test_backspace_deletes_last_char(self, _ps):
        reset_state(screen='thread', thread_id='+1001', thread_draft='hi', messages=[])
        kyphone_os.handle_key('KEY_BACKSPACE')
        self.assertEqual(kyphone_os.state['thread_draft'], 'h')

    @patch.object(kyphone_os, 'push_screen')
    def test_backspace_on_empty_draft_stays(self, _ps):
        kyphone_os.handle_key('KEY_BACKSPACE')
        self.assertEqual(kyphone_os.state['thread_draft'], '')
        self.assertEqual(kyphone_os.state['screen'], 'thread')

    @patch.object(kyphone_os, 'push_screen')
    @patch.object(kyphone_os, 'send_reply')
    def test_enter_with_draft_sends_and_clears(self, mock_send, _ps):
        reset_state(screen='thread', thread_id='+1001', thread_draft='hello', messages=[])
        kyphone_os.handle_key('KEY_ENTER')
        mock_send.assert_called_once_with('+1001', 'hello')
        self.assertEqual(kyphone_os.state['thread_draft'], '')

    @patch.object(kyphone_os, 'push_screen')
    @patch.object(kyphone_os, 'send_reply')
    def test_enter_with_empty_draft_noop(self, mock_send, _ps):
        kyphone_os.handle_key('KEY_ENTER')
        mock_send.assert_not_called()
        self.assertEqual(kyphone_os.state['screen'], 'thread')

    @patch.object(kyphone_os, 'push_screen')
    @patch.object(kyphone_os, 'send_reply')
    def test_enter_with_whitespace_draft_noop(self, mock_send, _ps):
        reset_state(screen='thread', thread_id='+1001', thread_draft='   ', messages=[])
        kyphone_os.handle_key('KEY_ENTER')
        mock_send.assert_not_called()

    @patch.object(kyphone_os, 'push_screen')
    def test_esc_goes_to_texts_list(self, _ps):
        kyphone_os.handle_key('KEY_ESC')
        self.assertEqual(kyphone_os.state['screen'], 'texts_list')

    @patch.object(kyphone_os, 'push_screen')
    def test_up_enters_header_on_back(self, _ps):
        kyphone_os.handle_key('KEY_UP')
        self.assertEqual(kyphone_os.state['thread_header_sel'], 'back')

    @patch.object(kyphone_os, 'push_screen')
    def test_right_in_header_moves_to_info(self, _ps):
        reset_state(screen='thread', thread_id='+1001', thread_header_sel='back', messages=[])
        kyphone_os.handle_key('KEY_RIGHT')
        self.assertEqual(kyphone_os.state['thread_header_sel'], 'info')

    @patch.object(kyphone_os, 'push_screen')
    def test_left_in_header_moves_to_back(self, _ps):
        reset_state(screen='thread', thread_id='+1001', thread_header_sel='info', messages=[])
        kyphone_os.handle_key('KEY_LEFT')
        self.assertEqual(kyphone_os.state['thread_header_sel'], 'back')

    @patch.object(kyphone_os, 'push_screen')
    def test_down_in_header_returns_to_typing(self, _ps):
        reset_state(screen='thread', thread_id='+1001', thread_header_sel='back', messages=[])
        kyphone_os.handle_key('KEY_DOWN')
        self.assertIsNone(kyphone_os.state['thread_header_sel'])

    @patch.object(kyphone_os, 'push_screen')
    def test_enter_on_back_goes_to_texts_list(self, _ps):
        reset_state(screen='thread', thread_id='+1001', thread_header_sel='back', messages=[])
        kyphone_os.handle_key('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'texts_list')

    @patch.object(kyphone_os, 'push_screen')
    @patch.object(kyphone_os, 'send_reply')
    def test_enter_on_info_opens_contact_page(self, mock_send, _ps):
        # OS 0.2: every reachable control does something — info opens the
        # contact page instead of no-op'ing.
        reset_state(screen='thread', thread_id='+1001', thread_header_sel='info', messages=[])
        kyphone_os.handle_key('KEY_ENTER')
        mock_send.assert_not_called()
        self.assertEqual(kyphone_os.state['screen'], 'contact')
        self.assertEqual(kyphone_os.state['contact_return'], 'thread')

    @patch.object(kyphone_os, 'push_screen')
    def test_char_ignored_while_header_selected(self, _ps):
        reset_state(screen='thread', thread_id='+1001', thread_header_sel='back',
                     thread_draft='', messages=[])
        kyphone_os.handle_key('CHAR:h')
        self.assertEqual(kyphone_os.state['thread_draft'], '')


# ═══════════════════════════════════════════════════════════════════════════════
# Compose Screen
# ═══════════════════════════════════════════════════════════════════════════════

class TestComposeScreen(unittest.TestCase):
    def setUp(self):
        reset_state(screen='compose', compose_to='', compose_msg='', compose_to_active=True)
        self._save_patch = patch.object(kyphone_os, 'save_messages')
        self._save_patch.start()

    def tearDown(self):
        self._save_patch.stop()

    @patch.object(kyphone_os, 'push_screen')
    def test_char_appends_to_to_field(self, _ps):
        kyphone_os.handle_key('CHAR:a')
        self.assertEqual(kyphone_os.state['compose_to'], 'a')

    @patch.object(kyphone_os, 'push_screen')
    def test_char_appends_to_message_field_when_active(self, _ps):
        reset_state(screen='compose', compose_to='Alice', compose_msg='', compose_to_active=False)
        kyphone_os.handle_key('CHAR:h')
        self.assertEqual(kyphone_os.state['compose_msg'], 'h')

    @patch.object(kyphone_os, 'push_screen')
    def test_enter_on_to_empty_opens_contacts_pick(self, _ps):
        # OS 0.2: an empty TO field's Enter opens the contact picker instead
        # of no-op'ing.
        kyphone_os.handle_key('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'contacts_pick')
        self.assertEqual(kyphone_os.state['contacts_return'], 'compose')

    @patch.object(kyphone_os, 'push_screen')
    def test_enter_on_to_with_content_moves_to_message(self, _ps):
        reset_state(screen='compose', compose_to='+1999', compose_msg='', compose_to_active=True)
        kyphone_os.handle_key('KEY_ENTER')
        self.assertFalse(kyphone_os.state['compose_to_active'])
        self.assertEqual(kyphone_os.state['screen'], 'compose')

    @patch.object(kyphone_os, 'push_screen')
    @patch.object(kyphone_os, 'send_reply')
    def test_enter_on_message_with_both_creates_thread(self, mock_send, _ps):
        reset_state(screen='compose', compose_to='+1999', compose_msg='Hello!',
                    compose_to_active=False)
        kyphone_os.handle_key('KEY_ENTER')
        mock_send.assert_called_once_with('+1999', 'Hello!')
        self.assertEqual(kyphone_os.state['screen'], 'thread')
        self.assertEqual(kyphone_os.state['thread_id'], '+1999')

    @patch.object(kyphone_os, 'push_screen')
    def test_tab_toggles_active_field(self, _ps):
        kyphone_os.handle_key('KEY_TAB')
        self.assertFalse(kyphone_os.state['compose_to_active'])
        kyphone_os.handle_key('KEY_TAB')
        self.assertTrue(kyphone_os.state['compose_to_active'])

    @patch.object(kyphone_os, 'push_screen')
    def test_backspace_deletes_from_active_field(self, _ps):
        reset_state(screen='compose', compose_to='Ali', compose_msg='', compose_to_active=True)
        kyphone_os.handle_key('KEY_BACKSPACE')
        self.assertEqual(kyphone_os.state['compose_to'], 'Al')

    @patch.object(kyphone_os, 'push_screen')
    def test_esc_goes_to_texts_list(self, _ps):
        kyphone_os.handle_key('KEY_ESC')
        self.assertEqual(kyphone_os.state['screen'], 'texts_list')

    @patch.object(kyphone_os, 'push_screen')
    def test_up_enters_header_on_x(self, _ps):
        kyphone_os.handle_key('KEY_UP')
        self.assertEqual(kyphone_os.state['compose_header_sel'], 'x')

    @patch.object(kyphone_os, 'push_screen')
    def test_down_in_header_returns_to_typing(self, _ps):
        reset_state(screen='compose', compose_header_sel='x')
        kyphone_os.handle_key('KEY_DOWN')
        self.assertIsNone(kyphone_os.state['compose_header_sel'])

    @patch.object(kyphone_os, 'push_screen')
    def test_enter_on_x_goes_to_texts_list(self, _ps):
        reset_state(screen='compose', compose_header_sel='x')
        kyphone_os.handle_key('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'texts_list')

    @patch.object(kyphone_os, 'push_screen')
    def test_char_ignored_while_header_selected(self, _ps):
        reset_state(screen='compose', compose_header_sel='x', compose_to='')
        kyphone_os.handle_key('CHAR:a')
        self.assertEqual(kyphone_os.state['compose_to'], '')


# ═══════════════════════════════════════════════════════════════════════════════
# List windows (OS 0.2.1) — 5 texts rows, 7 contacts rows, 6 calls rows
# ═══════════════════════════════════════════════════════════════════════════════

from datetime import datetime, timedelta  # noqa: E402

CELL = '\xb7'


def _wire(mock_push):
    """The last command pushed, or None if nothing was (e.g. a key that changed nothing)."""
    return mock_push.call_args[0][0] if mock_push.call_args else None


def _rows(wire, skip):
    """The row entries of a list command (after `skip` head fields)."""
    return [r for r in wire.split('|')[skip:] if r]


def _thread_msgs(n, body='hello'):
    """n one-message threads; thread n-1 is the newest (index 0 in the list)."""
    return [{'sender': f'+1555000{i:04d}', 'name': f'T{i}', 'body': body, 'read': True,
             'ts': datetime.now().isoformat()} for i in range(n)]


def _contacts(n, first='Name', number=None):
    return [{'first': f'{first}{i + 1:02d}', 'last': '', 'number': number or f'+1555010{i + 1:04d}'}
            for i in range(n)]


class ListWindowBase(unittest.TestCase):
    def setUp(self):
        self._save_patch = patch.object(kyphone_os, 'save_messages')
        self._save_patch.start()
        self._contacts_backup = list(kyphone_os.CONTACTS)
        self._cs_patch = patch.object(kyphone_os, '_save_contacts')
        self._cs_patch.start()

    def tearDown(self):
        self._save_patch.stop()
        self._cs_patch.stop()
        kyphone_os.CONTACTS[:] = self._contacts_backup


class TestWindowStart(unittest.TestCase):
    ws = staticmethod(kyphone_os.window_start)

    def test_top_of_list(self):
        self.assertEqual(self.ws(0, 0, 5, 12), 0)

    def test_moving_down_shifts_one_row_at_a_time(self):
        self.assertEqual(self.ws(0, 4, 5, 12), 0)     # still inside the window
        self.assertEqual(self.ws(0, 5, 5, 12), 1)     # one past the bottom: shift by one
        self.assertEqual(self.ws(1, 6, 5, 12), 2)

    def test_moving_up_shifts_one_row_at_a_time(self):
        self.assertEqual(self.ws(3, 3, 5, 12), 3)
        self.assertEqual(self.ws(3, 2, 5, 12), 2)

    def test_window_never_runs_past_the_end(self):
        self.assertEqual(self.ws(0, 11, 5, 12), 7)
        self.assertEqual(self.ws(9, 11, 5, 12), 7)    # stale start is clamped

    def test_short_list_stays_at_top(self):
        self.assertEqual(self.ws(0, 2, 5, 3), 0)
        self.assertEqual(self.ws(4, 0, 5, 0), 0)

    def test_selection_is_always_inside_the_window(self):
        for rows in (5, 6, 7):
            for total in range(1, 25):
                start = 0
                for index in list(range(total)) + list(range(total - 1, -1, -1)):
                    start = self.ws(start, index, rows, total)
                    self.assertTrue(start <= index < start + rows, (rows, total, index, start))
                    self.assertTrue(0 <= start <= max(0, total - rows))


class TestTextsWindow(ListWindowBase):
    def setUp(self):
        super().setUp()
        reset_state(screen='texts_list', texts_index=0, messages=_thread_msgs(12))

    def _push(self):
        with patch.object(kyphone_os, 'push_screen') as ps:
            kyphone_os.push_texts()
        return _wire(ps)

    def _key(self, key, times=1):
        with patch.object(kyphone_os, 'push_screen') as ps:
            for _ in range(times):
                kyphone_os.handle_key(key)
        return _wire(ps)

    def test_every_thread_is_reachable(self):
        self.assertEqual(len(kyphone_os.get_threads()), 12)    # no 7-thread cap

    def test_sends_only_five_rows(self):
        wire = self._push()
        self.assertTrue(wire.startswith('TEXTS|0|'))
        self.assertEqual(len(_rows(wire, 2)), 5)

    def test_down_past_the_window_shifts_it_by_one(self):
        wire = self._key('KEY_DOWN', 5)
        self.assertEqual(kyphone_os.state['texts_index'], 5)
        self.assertEqual(kyphone_os.state['texts_start'], 1)
        self.assertTrue(wire.startswith('TEXTS|4|'))          # selection = last row of the window

    def test_can_reach_the_oldest_thread(self):
        wire = self._key('KEY_DOWN', 11)
        self.assertEqual(kyphone_os.state['texts_index'], 11)
        self.assertEqual(kyphone_os.state['texts_start'], 7)
        self.assertTrue(wire.startswith('TEXTS|4|'))
        self.assertTrue(_rows(wire, 2)[-1].startswith('+15550000000'[:14]))   # thread 0 is the oldest

    def test_down_on_last_row_does_nothing(self):
        self._key('KEY_DOWN', 11)
        self._key('KEY_DOWN', 3)
        self.assertEqual(kyphone_os.state['texts_index'], 11)

    def test_enter_opens_the_thread_under_the_selection(self):
        self._key('KEY_DOWN', 8)
        self._key('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'thread')
        self.assertEqual(kyphone_os.state['thread_id'], kyphone_os.get_threads()[8]['sender'])

    def test_up_from_first_row_selects_header_and_window_returns_to_top(self):
        self._key('KEY_DOWN', 7)
        for _ in range(8):
            wire = self._key('KEY_UP')
        self.assertEqual(kyphone_os.state['texts_index'], -1)
        self.assertEqual(kyphone_os.state['texts_start'], 0)
        self.assertTrue(wire.startswith('TEXTS|-1|'))

    def test_name_and_preview_are_capped(self):
        reset_state(screen='texts_list', messages=_thread_msgs(1, body='b' * 60))
        kyphone_os.CONTACTS[:] = [{'first': 'N' * 20, 'last': '', 'number': '+15550000000'}]
        row = _rows(self._push(), 2)[0].split(CELL)
        self.assertEqual(len(row[0]), kyphone_os.LIST_NAME_MAX)
        self.assertEqual(len(row[1]), kyphone_os.PREVIEW_MAX)

    def test_worst_case_rows_still_fit_the_frame_whole(self):
        # five max-length names/previews and the longest time string ("WEDNESDAY"):
        # the column caps alone come to ~267 characters, over the 253-character frame.
        wed = next(datetime.now() - timedelta(days=k) for k in range(2, 7)
                   if (datetime.now() - timedelta(days=k)).weekday() == 2)
        msgs = [{'sender': f'+1555000{i:04d}', 'name': 'x', 'body': 'p' * 60, 'read': False,
                 'ts': wed.isoformat()} for i in range(5)]
        reset_state(screen='texts_list', messages=msgs)
        kyphone_os.CONTACTS[:] = [{'first': 'N' * 14, 'last': '', 'number': m['sender']} for m in msgs]
        wire = self._push()
        self.assertLessEqual(len(wire), kyphone_os.MAX_COMMAND_CHARS)
        rows = _rows(wire, 2)
        self.assertEqual(len(rows), 5)
        for row in rows:
            self.assertEqual(row.count(CELL), 3)              # no row cut mid-field
            self.assertEqual(row.split(CELL)[3], 'WEDNESDAY')  # the time survives intact

    def test_empty_list_sends_no_rows(self):
        reset_state(screen='texts_list', messages=[])
        self.assertEqual(self._push(), 'TEXTS|0')


class TestContactsWindow(ListWindowBase):
    def setUp(self):
        super().setUp()
        kyphone_os.CONTACTS[:] = _contacts(14)
        reset_state(screen='contacts_pick', contacts_index=0)

    def _push(self):
        with patch.object(kyphone_os, 'push_screen') as ps:
            kyphone_os.push_contacts()
        return _wire(ps)

    def _key(self, key, times=1):
        with patch.object(kyphone_os, 'push_screen') as ps:
            for _ in range(times):
                kyphone_os.handle_key(key)
        return _wire(ps)

    def test_sends_seven_rows_and_counter(self):
        wire = self._push()
        self.assertTrue(wire.startswith('CONTACTSPICK|0||1 / 14|'))
        self.assertEqual(len(_rows(wire, 4)), 7)

    def test_counter_follows_the_selection(self):
        wire = self._key('KEY_DOWN', 8)
        self.assertEqual(kyphone_os.state['contacts_start'], 2)
        self.assertTrue(wire.startswith('CONTACTSPICK|6||9 / 14|'))

    def test_last_contact_is_reachable_and_down_stops_there(self):
        wire = self._key('KEY_DOWN', 20)
        self.assertEqual(kyphone_os.state['contacts_index'], 13)
        self.assertTrue(wire.startswith('CONTACTSPICK|6||14 / 14|'))
        self.assertTrue(_rows(wire, 4)[-1].startswith('Name14'))

    def test_header_selection_shows_first_position(self):
        wire = self._key('KEY_UP', 1)
        self.assertEqual(kyphone_os.state['contacts_index'], -1)
        self.assertTrue(wire.startswith('CONTACTSPICK|-1||1 / 14|'))

    def test_enter_on_a_scrolled_row_opens_that_contact(self):
        self._key('KEY_DOWN', 9)
        self._key('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'contact')
        self.assertEqual(kyphone_os.state['contact_for'], 'Name10')

    def test_typing_filters_and_counts_the_matches(self):
        for ch in 'name1':
            self._key(f'CHAR:{ch}')
        wire = self._push()
        self.assertTrue(wire.startswith('CONTACTSPICK|0|name1|1 / 5|'))     # Name10..Name14
        self.assertEqual(len(_rows(wire, 4)), 5)

    def test_no_match_sends_no_rows(self):
        for ch in 'zzz':
            self._key(f'CHAR:{ch}')
        self.assertEqual(self._push(), 'CONTACTSPICK|0|zzz|')

    def test_no_contacts_sends_no_rows(self):
        kyphone_os.CONTACTS[:] = []
        self.assertEqual(self._push(), 'CONTACTSPICK|0||')

    def test_worst_case_rows_still_fit_the_frame_whole(self):
        kyphone_os.CONTACTS[:] = [{'first': 'B' * 18, 'last': '', 'number': '(555) 019-9002'} for _ in range(14)]
        reset_state(screen='contacts_pick', contacts_index=13, contacts_query='b')
        wire = self._push()
        self.assertLessEqual(len(wire), kyphone_os.MAX_COMMAND_CHARS)
        rows = _rows(wire, 4)
        self.assertEqual(len(rows), 7)
        for row in rows:
            self.assertTrue(row.endswith('(555) 019-9002'))   # numbers are never cut


class TestCallsWindow(ListWindowBase):
    def setUp(self):
        super().setUp()
        calls = [{'name': f'Caller{i:02d}', 'tag': 'OUT', 'time': '4:03 PM', 'duration': '12:04'} for i in range(10)]
        reset_state(screen='calls_list', calls=calls, calls_index=0)

    def _push(self):
        with patch.object(kyphone_os, 'push_screen') as ps:
            kyphone_os.push_calls()
        return _wire(ps)

    def _key(self, key, times=1):
        with patch.object(kyphone_os, 'push_screen') as ps:
            for _ in range(times):
                kyphone_os.handle_key(key)
        return _wire(ps)

    def test_dial_a_number_is_the_first_row_of_the_list(self):
        wire = self._push()
        rows = _rows(wire, 2)
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[0], f'DIAL A NUMBER{CELL}NEW{CELL}{CELL}')

    def test_down_past_the_window_shifts_it(self):
        wire = self._key('KEY_DOWN', 6)
        self.assertEqual(kyphone_os.state['calls_start'], 1)
        self.assertTrue(wire.startswith('CALLS|5|'))
        self.assertTrue(_rows(wire, 2)[0].startswith('Caller00'))   # DIAL A NUMBER scrolled off

    def test_enter_on_a_scrolled_row_calls_that_entry(self):
        self._key('KEY_DOWN', 8)
        self._key('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'outgoing')
        self.assertEqual(kyphone_os.state['call_name'], 'Caller07')

    def test_worst_case_rows_still_fit_the_frame_whole(self):
        calls = [{'name': 'N' * 20, 'tag': 'MISS', 'time': 'Wednesday', 'duration': '99:59:59'} for _ in range(10)]
        reset_state(screen='calls_list', calls=calls, calls_index=9)
        wire = self._push()
        self.assertLessEqual(len(wire), kyphone_os.MAX_COMMAND_CHARS)
        self.assertEqual(len(_rows(wire, 2)), 6)


if __name__ == '__main__':
    unittest.main()
