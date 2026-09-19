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
        'thread_msg_sel': -1,
        'compose_to': '',
        'compose_msg': '',
        'compose_to_active': True,
        'compose_header_sel': None,
        'compose_plus_sel': False,
        'compose_send_sel': False,
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
        reset_state(screen='home', home_index=3)
        kyphone_os.handle_key('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'stub')
        self.assertEqual(kyphone_os.state['stub_key'], 'READ')

    @patch.object(kyphone_os, 'push_screen')
    def test_enter_listen_goes_to_stub(self, _ps):
        reset_state(screen='home', home_index=4)
        kyphone_os.handle_key('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'stub')
        self.assertEqual(kyphone_os.state['stub_key'], 'LISTEN')

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
    def test_enter_with_empty_draft_raises_an_alert_and_sends_nothing(self, mock_send, ps):
        kyphone_os.handle_key('KEY_ENTER')
        mock_send.assert_not_called()
        self.assertEqual(kyphone_os.state['screen'], 'stub')            # never a silent no-op
        self.assertEqual(kyphone_os.state['stub_return'], 'thread')
        self.assertTrue(ps.call_args[0][0].startswith('STUB|NEW MESSAGE|THERE IS NOTHING TO SEND'))

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
        self.assertTrue(_rows(wire, 2)[-1].startswith('(555) 000-0000'))     # thread 0 is the oldest, number formatted

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
        self.assertEqual(kyphone_os.state['contact_idx'], 9)
        self.assertEqual(kyphone_os.dispname(kyphone_os.CONTACTS[9]), 'Name10')

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


# ═══════════════════════════════════════════════════════════════════════════════
# Messages, sending and retry (OS 0.2.1)
# ═══════════════════════════════════════════════════════════════════════════════

import json  # noqa: E402
import random  # noqa: E402
import tempfile  # noqa: E402
import time as _time  # noqa: E402

ALICE = '+15550100001'


def _inbound(body='Are we still on for lunch?', peer=ALICE, read=True):
    return {'sender': peer, 'name': 'Alice', 'body': body, 'read': read, 'ts': datetime.now().isoformat()}


def _entries(wire):
    """The bubble entries of a THREAD2 command: [code, time, text]."""
    return [e.split(CELL, 2) for e in wire.split('|')[4:] if e]


class SendBase(ListWindowBase):
    """Thread tests: the radio is a mock and 'in the background' runs inline."""
    def setUp(self):
        super().setUp()
        self.radio = patch.object(kyphone_os, '_transport_send')
        self.transport = self.radio.start()
        self.inline = patch.object(kyphone_os, '_run_async', new=lambda fn: fn())
        self.inline.start()
        reset_state(screen='thread', thread_id=ALICE, messages=[_inbound()])

    def tearDown(self):
        self.radio.stop()
        self.inline.stop()
        super().tearDown()

    def type_text(self, text):
        with patch.object(kyphone_os, 'push_screen'):
            for ch in text:
                kyphone_os.handle_key('CHAR:' + ch)

    def key(self, *keys):
        with patch.object(kyphone_os, 'push_screen') as ps:
            for k in keys:
                kyphone_os.handle_key(k)
        return _wire(ps)

    def thread_wire(self):
        with patch.object(kyphone_os, 'push_screen') as ps:
            kyphone_os.push_thread2()
        return _wire(ps)

    def texts_wire(self):
        with patch.object(kyphone_os, 'push_screen') as ps:
            kyphone_os.push_texts()
        return _wire(ps)


class TestSendingAndStates(SendBase):
    def test_a_sent_reply_stays_in_the_conversation(self):
        self.type_text('hello')
        self.key('KEY_ENTER')
        self.transport.assert_called_once_with(ALICE, 'hello')
        entries = _entries(self.thread_wire())
        self.assertEqual([e[0] for e in entries], ['R', 'Y1'])
        self.assertEqual(entries[-1][2], 'hello')

    def test_sent_messages_are_keyed_on_the_other_party_not_the_phone(self):
        self.type_text('hello')
        self.key('KEY_ENTER')
        threads = kyphone_os.get_threads()
        self.assertEqual([t['sender'] for t in threads], [ALICE])   # no phantom thread
        sent = [m for m in kyphone_os.state['messages'] if m.get('dir') == 'out'][0]
        self.assertEqual(sent['peer'], ALICE)
        self.assertEqual(sent['state'], 'sent')

    def test_failed_send_is_kept_and_marked_not_sent(self):
        self.transport.side_effect = RuntimeError('no service')
        self.type_text('hello')
        self.key('KEY_ENTER')
        self.assertEqual(kyphone_os.state['thread_draft'], '')
        self.assertEqual([e[0] for e in _entries(self.thread_wire())], ['R', 'Y2'])

    def test_texts_list_preview_says_you_or_bang(self):
        self.type_text('hello')
        self.key('KEY_ENTER')
        self.assertIn(f'You: hello{CELL}', self.texts_wire())
        self.transport.side_effect = RuntimeError('no service')
        self.type_text('again')
        self.key('KEY_ENTER')
        self.assertIn(f'! again{CELL}', self.texts_wire())

    def test_message_to_a_new_number_joins_the_list_even_if_not_sent(self):
        self.transport.side_effect = RuntimeError('no service')
        reset_state(screen='compose', compose_to='+15550109999', compose_msg='hi', messages=[_inbound()])
        with patch.object(kyphone_os, 'push_screen'):
            kyphone_os._send_compose()
        self.assertEqual(kyphone_os.state['screen'], 'thread')
        self.assertIn('+15550109999', [t['sender'] for t in kyphone_os.get_threads()])
        self.assertEqual(_entries(self.thread_wire())[-1][:1], ['Y2'])

    def test_old_sent_messages_without_a_recipient_are_hidden_not_shown_as_a_conversation(self):
        legacy = {'sender': kyphone_os.TWILIO_NUMBER, 'name': 'You', 'body': 'old reply', 'read': True,
                  'ts': datetime.now().isoformat()}
        reset_state(screen='thread', thread_id=ALICE, messages=[_inbound(), legacy])
        self.assertEqual([t['sender'] for t in kyphone_os.get_threads()], [ALICE])
        self.assertNotIn('old reply', self.thread_wire())

    def test_sending_is_a_visible_state_before_the_radio_answers(self):
        pending = []
        with patch.object(kyphone_os, '_run_async', new=pending.append):
            self.type_text('hello')
            self.key('KEY_ENTER')
        self.assertEqual(_entries(self.thread_wire())[-1][0], 'Y0')          # SENDING...
        pending[0]()                                                        # the radio answers
        self.assertEqual(_entries(self.thread_wire())[-1][0], 'Y1')

    def test_send_does_not_block_the_keyboard(self):
        release = threading.Event()
        self.transport.side_effect = lambda to, body: release.wait(5)
        with patch.object(kyphone_os, '_run_async', new=lambda fn: threading.Thread(target=fn, daemon=True).start()):
            self.type_text('hello')
            t0 = _time.monotonic()
            self.key('KEY_ENTER')
            took = _time.monotonic() - t0
        self.assertLess(took, 0.5)
        self.assertEqual(_entries(self.thread_wire())[-1][0], 'Y0')
        release.set()
        for _ in range(100):
            if kyphone_os.state['messages'][-1]['state'] == 'sent':
                break
            _time.sleep(0.02)
        self.assertEqual(kyphone_os.state['messages'][-1]['state'], 'sent')

    def test_a_send_interrupted_by_a_restart_loads_as_not_sent(self):
        path = os.path.join(tempfile.mkdtemp(), 'messages.json')
        with open(path, 'w') as f:
            json.dump({'messages': [{'dir': 'out', 'peer': ALICE, 'name': 'You', 'body': 'x', 'read': True,
                                     'ts': datetime.now().isoformat(), 'state': 'sending'}], 'last_sid': None}, f)
        with patch.object(kyphone_os, 'MESSAGES_FILE', path):
            kyphone_os.load_messages()
        self.assertEqual(kyphone_os.state['messages'][0]['state'], 'not_sent')


class TestRetry(SendBase):
    def setUp(self):
        super().setUp()
        self.transport.side_effect = RuntimeError('no service')
        self.type_text('lost')
        self.key('KEY_ENTER')
        self.transport.reset_mock()

    def test_arrow_up_selects_the_newest_not_sent_message_before_the_header(self):
        wire = self.key('KEY_UP')
        self.assertEqual(kyphone_os.state['thread_msg_sel'], 1)
        self.assertIsNone(kyphone_os.state['thread_header_sel'])
        self.assertEqual(_entries(wire)[-1][0], 'Y3')                       # the retry prompt

    def test_enter_on_the_selected_message_sends_it_again(self):
        self.transport.side_effect = None
        self.key('KEY_UP')
        wire = self.key('KEY_ENTER')
        self.transport.assert_called_once_with(ALICE, 'lost')
        self.assertEqual(_entries(wire)[-1][0], 'Y1')
        self.assertEqual(kyphone_os.state['thread_msg_sel'], -1)
        self.assertEqual(len([m for m in kyphone_os.state['messages'] if m.get('dir') == 'out']), 1)   # same message

    def test_retry_that_fails_again_stays_not_sent(self):
        self.key('KEY_UP')
        wire = self.key('KEY_ENTER')
        self.assertEqual(_entries(wire)[-1][0], 'Y2')

    def test_arrow_up_again_reaches_the_header_and_down_returns_to_the_composer(self):
        self.key('KEY_UP')
        self.key('KEY_UP')
        self.assertEqual(kyphone_os.state['thread_msg_sel'], -1)
        self.assertEqual(kyphone_os.state['thread_header_sel'], 'back')
        self.key('KEY_DOWN')
        self.assertIsNone(kyphone_os.state['thread_header_sel'])

    def test_arrow_down_from_a_selected_message_returns_to_the_composer(self):
        self.key('KEY_UP')
        self.key('KEY_DOWN')
        self.assertEqual(kyphone_os.state['thread_msg_sel'], -1)
        self.assertIsNone(kyphone_os.state['thread_header_sel'])

    def test_typing_while_a_message_is_selected_goes_to_the_composer(self):
        self.key('KEY_UP')
        self.type_text('x')
        self.assertEqual(kyphone_os.state['thread_msg_sel'], -1)
        self.assertEqual(kyphone_os.state['thread_draft'], 'x')

    def test_with_nothing_to_retry_arrow_up_goes_straight_to_the_header(self):
        reset_state(screen='thread', thread_id=ALICE, messages=[_inbound()])
        self.key('KEY_UP')
        self.assertEqual(kyphone_os.state['thread_header_sel'], 'back')
        self.assertEqual(kyphone_os.state['thread_msg_sel'], -1)


# ═══════════════════════════════════════════════════════════════════════════════
# What the panel can draw
# ═══════════════════════════════════════════════════════════════════════════════

class TestDrawableText(SendBase):
    def test_reserved_and_undrawable_typed_keys_are_ignored(self):
        self.type_text('a|b·cé\U0001F600d')
        self.assertEqual(kyphone_os.state['thread_draft'], 'abcd')

    def test_received_typographic_characters_become_their_ascii_look_alikes(self):
        self.assertEqual(kyphone_os.sanitize('it’s “fine” — ok…'), 'it\'s "fine" - ok...')

    def test_received_undrawable_characters_become_question_marks(self):
        self.assertEqual(kyphone_os.sanitize('hi \U0001F600 a|b·c'), 'hi ? a?b?c')
        self.assertEqual(kyphone_os.sanitize('line one\nline two'), 'line one line two')

    def test_nothing_the_panel_cannot_draw_reaches_the_wire(self):
        reset_state(screen='thread', thread_id=ALICE,
                    messages=[_inbound('it’s fine \U0001F600 | ok · done')])
        wire = self.thread_wire()
        for ch in wire:
            self.assertTrue(ch == CELL or ' ' <= ch <= '~', repr(ch))
        self.assertEqual(max(kyphone_os.build_payload(wire)), max(ord(c) for c in wire))
        self.assertLessEqual(max(kyphone_os.build_payload(wire)), 255)


class TestComposerView(unittest.TestCase):
    view = staticmethod(kyphone_os.composer_view)

    def _lines(self, text):
        return kyphone_os.wrap_words('> ' + text + '#', kyphone_os.COMPOSER_COLS)

    def test_short_draft_is_shown_whole(self):
        self.assertEqual(self.view('hello'), 'hello')
        self.assertEqual(self.view(''), '')

    def test_a_draft_that_fills_three_lines_is_still_shown_whole(self):
        draft = 'x' * 87       # 2-char prompt + 87 + 1-column cursor = 90 = three full lines
        self.assertEqual(self.view(draft), draft)

    def test_a_longer_draft_shows_its_end_behind_three_dots(self):
        draft = 'start ' + 'w' * 200 + ' the very end'
        shown = self.view(draft)
        self.assertTrue(shown.startswith('...'))
        self.assertTrue(shown.endswith('the very end'))
        self.assertLessEqual(len(self._lines(shown)), 3)

    def test_the_cursor_is_always_visible_for_any_draft(self):
        rng = random.Random(7)
        alphabet = 'abc de fgh ijklmnop  qrstuvwxyz0123456789'
        for _ in range(300):
            draft = ''.join(rng.choice(alphabet) for _ in range(rng.randint(0, 400)))
            shown = self.view(draft)
            self.assertLessEqual(len(self._lines(shown)), kyphone_os.COMPOSER_LINES, draft)
            if shown != draft:
                self.assertTrue(draft.endswith(shown[3:]))          # it is the END of the draft

    def test_wrap_words_hard_breaks_a_word_longer_than_a_line(self):
        # the long word fills the rest of the line it starts on, then continues below
        self.assertEqual(kyphone_os.wrap_words('a ' + 'b' * 25, 10), ['a ' + 'b' * 8, 'b' * 10, 'b' * 7])


class TestThreadFitsTheFrame(SendBase):
    def test_three_ordinary_messages_all_fit(self):
        msgs = [_inbound('are you close?'), _inbound('yeah leaving now'), _inbound('still here?')]
        reset_state(screen='thread', thread_id=ALICE, messages=msgs)
        wire = self.thread_wire()
        self.assertLessEqual(len(wire), kyphone_os.MAX_COMMAND_CHARS)
        self.assertEqual(len(_entries(wire)), 3)

    def test_a_long_message_pushes_the_older_bubbles_out_and_is_cut_with_dots(self):
        msgs = [_inbound('older one'), _inbound('older two'), _inbound('L' * 400)]
        reset_state(screen='thread', thread_id=ALICE, messages=msgs)
        wire = self.thread_wire()
        self.assertLessEqual(len(wire), kyphone_os.MAX_COMMAND_CHARS)
        entries = _entries(wire)
        self.assertEqual(len(entries), 1)
        self.assertTrue(entries[0][2].endswith('...'))

    def test_a_three_line_draft_and_three_messages_still_fit_and_keep_the_newest(self):
        msgs = [_inbound('m' * 60), _inbound('n' * 60), _inbound('newest message here')]
        reset_state(screen='thread', thread_id=ALICE, messages=msgs, thread_draft='d' * 200)
        wire = self.thread_wire()
        self.assertLessEqual(len(wire), kyphone_os.MAX_COMMAND_CHARS)
        self.assertEqual(_entries(wire)[-1][2], 'newest message here')

    def test_the_selected_retry_bubble_is_never_the_one_dropped(self):
        reset_state(screen='thread', thread_id=ALICE, messages=[_inbound('a' * 90), _inbound('b' * 90)])
        self.transport.side_effect = RuntimeError('no service')
        self.type_text('pick me')
        self.key('KEY_ENTER')
        self.key('KEY_UP')
        entries = _entries(self.thread_wire())
        self.assertIn('Y3', [e[0] for e in entries])


# ═══════════════════════════════════════════════════════════════════════════════
# Phone numbers and the contact page (OS 0.2.1 step 4)
# ═══════════════════════════════════════════════════════════════════════════════

class TestPhoneNumbers(unittest.TestCase):
    def test_ten_digits_and_eleven_starting_with_one_format_the_same_way(self):
        for n in ('5550199002', '15550199002', '+15550199002', '(555) 019-9002', '555-019-9002', '1 555 019 9002'):
            self.assertEqual(kyphone_os.format_number(n), '(555) 019-9002', n)

    def test_anything_else_is_shown_as_typed(self):
        self.assertEqual(kyphone_os.format_number('12345'), '12345')
        self.assertEqual(kyphone_os.format_number('+44 20 7946 0958'), '+44 20 7946 0958')
        self.assertEqual(kyphone_os.format_number(''), '')

    def test_formatted_number_is_exactly_the_name_column_width(self):
        self.assertEqual(len(kyphone_os.format_number('5550199002')), kyphone_os.LIST_NAME_MAX)

    def test_same_number_ignores_how_it_was_typed(self):
        self.assertTrue(kyphone_os.same_number('+15550100001', '(555) 010-0001'))
        self.assertTrue(kyphone_os.same_number('5550100001', '1 555 010 0001'))
        self.assertFalse(kyphone_os.same_number('+15550100001', '+15550100002'))
        self.assertFalse(kyphone_os.same_number('', ''))
        self.assertFalse(kyphone_os.same_number('', '5550100001'))

    def test_number_valid(self):
        for ok in ('5550100001', '15550100001', '(555) 010-0001', '+1 555 010 0001'):
            self.assertTrue(kyphone_os.number_valid(ok), ok)
        for bad in ('', '555', '55501000012', '25550100001', 'abc'):
            self.assertFalse(kyphone_os.number_valid(bad), bad)

    def test_normalize_number(self):
        self.assertEqual(kyphone_os.normalize_number('(555) 010-0001'), '+15550100001')
        self.assertEqual(kyphone_os.normalize_number('15550100001'), '+15550100001')
        self.assertEqual(kyphone_os.normalize_number('12345'), '12345')


class ContactBase(ListWindowBase):
    def setUp(self):
        super().setUp()
        kyphone_os.CONTACTS[:] = [
            {'first': 'Alice', 'last': 'Test', 'number': '+15550100001'},
            {'first': 'Bob', 'last': '', 'number': '(555) 010-0002'},
            {'first': 'Sam', 'last': 'Whitfield', 'number': ''},
            {'first': 'Bob', 'last': '', 'number': '+15550100004'},          # a second "Bob"
        ]
        reset_state(screen='contacts_pick', contacts_index=0, contacts_return='home')

    def key(self, *keys):
        with patch.object(kyphone_os, 'push_screen') as ps:
            for k in keys:
                kyphone_os.handle_key(k)
        return _wire(ps)

    def open_contact(self, row):
        reset_state(screen='contacts_pick', contacts_index=0, contacts_return='home')
        return self.key(*(['KEY_DOWN'] * row), 'KEY_ENTER')


class TestNamesFromNumbers(ContactBase):
    def test_a_saved_contact_is_recognised_however_its_number_was_typed(self):
        self.assertEqual(kyphone_os.format_name('+15550100002'), 'Bob')          # saved as (555) 010-0002

    def test_an_unsaved_number_is_shown_formatted_never_truncated(self):
        self.assertEqual(kyphone_os.format_name('+15550199002'), '(555) 019-9002')

    def test_two_unsaved_senders_do_not_look_the_same_in_the_texts_list(self):
        reset_state(screen='texts_list', messages=[_inbound('a', peer='+15550199001'), _inbound('b', peer='+15550199002')])
        with patch.object(kyphone_os, 'push_screen') as ps:
            kyphone_os.push_texts()
        names = [r.split(CELL)[0] for r in _rows(_wire(ps), 2)]
        self.assertEqual(sorted(names), ['(555) 019-9001', '(555) 019-9002'])

    def test_contacts_list_shows_formatted_numbers(self):
        with patch.object(kyphone_os, 'push_screen') as ps:
            kyphone_os.push_contacts()
        rows = _rows(_wire(ps), 4)
        self.assertEqual(rows[0], f'Alice Test{CELL}(555) 010-0001')
        self.assertEqual(rows[2], f'Sam Whitfield{CELL}')                       # no number: renderer shows NO NUMBER

    def test_typing_a_number_in_compose_shows_it_as_typed_until_it_is_a_contact(self):
        reset_state(screen='compose', compose_to='555019', compose_to_active=True)
        with patch.object(kyphone_os, 'push_screen') as ps:
            kyphone_os.push_compose()
        self.assertTrue(_wire(ps).startswith('COMPOSE|555019|'))
        reset_state(screen='compose', compose_to='+15550100001', compose_to_active=False)
        with patch.object(kyphone_os, 'push_screen') as ps:
            kyphone_os.push_compose()
        self.assertTrue(_wire(ps).startswith('COMPOSE|Alice Test|'))


class TestContactPage(ContactBase):
    def test_saved_contact_with_a_number(self):
        wire = self.open_contact(0)
        self.assertEqual(wire, 'CONTACT|Alice Test|(555) 010-0001|S|C')

    def test_saved_contact_without_a_number_offers_only_add_number(self):
        wire = self.open_contact(2)
        self.assertEqual(wire, 'CONTACT|Sam Whitfield|NO NUMBER SAVED|N|A')
        self.assertEqual(kyphone_os.state['contact_sel'], 'addnum')
        self.key('KEY_RIGHT')                                               # nothing else on the row
        self.assertEqual(kyphone_os.state['contact_sel'], 'addnum')

    def test_unsaved_number_shows_it_formatted_with_call_text_and_save(self):
        reset_state(screen='thread', thread_id='+15550199002', messages=[_inbound('hi', peer='+15550199002')])
        self.key('KEY_UP', 'KEY_RIGHT', 'KEY_ENTER')                        # header -> info
        self.assertEqual(kyphone_os.state['screen'], 'contact')
        self.assertIsNone(kyphone_os.state['contact_idx'])
        with patch.object(kyphone_os, 'push_screen') as ps:
            kyphone_os.push_contact()
        self.assertEqual(_wire(ps), 'CONTACT|(555) 019-9002|NOT IN CONTACTS|U|C')

    def test_navigation_between_the_top_row_and_the_action_row(self):
        self.open_contact(0)                                                # saved: back, edit / call, text
        self.key('KEY_RIGHT'); self.assertEqual(kyphone_os.state['contact_sel'], 'text')
        self.key('KEY_RIGHT'); self.assertEqual(kyphone_os.state['contact_sel'], 'text')   # end of the row
        self.key('KEY_UP');    self.assertEqual(kyphone_os.state['contact_sel'], 'edit')   # text is under edit
        self.key('KEY_LEFT');  self.assertEqual(kyphone_os.state['contact_sel'], 'back')
        self.key('KEY_UP');    self.assertEqual(kyphone_os.state['contact_sel'], 'back')   # nothing above
        self.key('KEY_DOWN');  self.assertEqual(kyphone_os.state['contact_sel'], 'call')
        self.key('KEY_DOWN');  self.assertEqual(kyphone_os.state['contact_sel'], 'call')

    def test_unsaved_page_row_has_three_buttons(self):
        reset_state(screen='thread', thread_id='+15550199002', messages=[_inbound('hi', peer='+15550199002')])
        self.key('KEY_UP', 'KEY_RIGHT', 'KEY_ENTER')
        self.key('KEY_RIGHT', 'KEY_RIGHT')
        self.assertEqual(kyphone_os.state['contact_sel'], 'save')
        self.key('KEY_UP')
        self.assertEqual(kyphone_os.state['contact_sel'], 'back')           # no EDIT on an unsaved page

    def test_esc_returns_to_where_the_page_was_opened_from(self):
        self.open_contact(0)
        self.key('KEY_ESC')
        self.assertEqual(kyphone_os.state['screen'], 'contacts_pick')

    def test_text_opens_the_existing_thread_however_the_number_was_typed(self):
        reset_state(screen='contacts_pick', contacts_index=1, contacts_return='home',
                    messages=[_inbound('hi', peer='+15550100002')])          # contact saved as (555) 010-0002
        self.key('KEY_ENTER', 'KEY_RIGHT', 'KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'thread')
        self.assertEqual(kyphone_os.state['thread_id'], '+15550100002')

    def test_text_with_no_thread_starts_a_message_to_that_number(self):
        reset_state(screen='contacts_pick', contacts_index=0, contacts_return='home', messages=[])
        self.key('KEY_ENTER', 'KEY_RIGHT', 'KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'compose')
        self.assertEqual(kyphone_os.state['compose_to'], '+15550100001')

    def test_call_uses_the_name_or_the_formatted_number(self):
        self.open_contact(0)
        self.key('KEY_ENTER')
        self.assertEqual(kyphone_os.state['call_name'], 'Alice Test')
        reset_state(screen='thread', thread_id='+15550199002', messages=[_inbound('hi', peer='+15550199002')])
        self.key('KEY_UP', 'KEY_RIGHT', 'KEY_ENTER', 'KEY_ENTER')
        self.assertEqual(kyphone_os.state['call_name'], '(555) 019-9002')


class TestSameNameContacts(ContactBase):
    def test_picking_the_second_bob_opens_the_second_bob(self):
        wire = self.open_contact(3)
        self.assertEqual(kyphone_os.state['contact_idx'], 3)
        self.assertEqual(wire, 'CONTACT|Bob|(555) 010-0004|S|C')             # not the first Bob's number

    def test_editing_the_second_bob_changes_only_the_second_bob(self):
        self.open_contact(3)
        self.key('KEY_UP', 'KEY_RIGHT', 'KEY_ENTER')                        # edit
        self.key('KEY_DOWN')                                                # last name
        for ch in 'Ray':
            self.key(f'CHAR:{ch}')
        self.key('KEY_DOWN', 'KEY_DOWN', 'KEY_ENTER')                       # number -> save
        self.assertEqual(kyphone_os.CONTACTS[3]['last'], 'Ray')
        self.assertEqual(kyphone_os.CONTACTS[1]['last'], '')                # the first Bob is untouched
        self.assertEqual(kyphone_os.CONTACTS[1]['number'], '(555) 010-0002')


class TestSaveAnUnsavedNumber(ContactBase):
    def setUp(self):
        super().setUp()
        reset_state(screen='thread', thread_id='+15550199002', messages=[_inbound('hi', peer='+15550199002')])
        self.key('KEY_UP', 'KEY_RIGHT', 'KEY_ENTER')                        # info -> unsaved contact page
        self.key('KEY_RIGHT', 'KEY_RIGHT', 'KEY_ENTER')                     # SAVE

    def test_save_opens_a_blank_form_with_the_number_filled_in_and_formatted(self):
        self.assertEqual(kyphone_os.state['screen'], 'contact_edit')
        self.assertEqual(kyphone_os.state['edit_first'], '')
        self.assertEqual(kyphone_os.state['edit_number'], '(555) 019-9002')
        self.assertEqual(kyphone_os.state['edit_index'], 0)                 # cursor in FIRST NAME
        self.assertIsNone(kyphone_os.state['edit_idx'])

    def test_saving_adds_the_contact_and_shows_its_page(self):
        for ch in 'Dave':
            self.key(f'CHAR:{ch}')
        self.key('KEY_DOWN', 'KEY_DOWN', 'KEY_DOWN', 'KEY_ENTER')
        self.assertEqual(len(kyphone_os.CONTACTS), 5)
        names = [kyphone_os.dispname(c) for c in kyphone_os.CONTACTS]
        self.assertEqual(names, sorted(names, key=str.lower))                 # the address book stays alphabetical
        self.assertEqual(kyphone_os.state['screen'], 'contact')
        self.assertEqual(kyphone_os.dispname(kyphone_os.CONTACTS[kyphone_os.state['contact_idx']]), 'Dave')
        self.assertEqual(kyphone_os.format_name('+15550199002'), 'Dave')    # the thread now shows the name

    def test_cancelling_returns_to_the_unsaved_page(self):
        self.key('KEY_ESC')
        self.assertEqual(kyphone_os.state['screen'], 'contact')
        self.assertIsNone(kyphone_os.state['contact_idx'])
        self.assertEqual(len(kyphone_os.CONTACTS), 4)


class TestRepliesLandInTheSameThread(SendBase):
    def test_a_message_to_a_number_typed_differently_joins_the_existing_thread(self):
        with patch.object(kyphone_os, 'push_screen'):
            kyphone_os.send_reply('(555) 010-0001', 'hi')
        peers = {kyphone_os.peer_of(m) for m in kyphone_os.state['messages']}
        self.assertEqual(peers, {ALICE})                                    # not a second conversation
        self.transport.assert_called_once_with(ALICE, 'hi')

    def test_a_message_to_a_new_number_is_stored_normalized(self):
        with patch.object(kyphone_os, 'push_screen'):
            kyphone_os.send_reply('(555) 019-9002', 'hi')
        self.assertEqual(kyphone_os.state['messages'][-1]['peer'], '+15550199002')

    def test_compose_opens_the_thread_the_message_was_stored_under(self):
        reset_state(screen='compose', compose_to='5550199002', compose_msg='hi', messages=[_inbound()])
        with patch.object(kyphone_os, 'push_screen'):
            kyphone_os._send_compose()
        self.assertEqual(kyphone_os.state['thread_id'], '+15550199002')
        self.assertEqual(_entries(self.thread_wire())[-1][2], 'hi')


# ═══════════════════════════════════════════════════════════════════════════════
# New contact, validation and stop alerts (OS 0.2.1 step 5)
# ═══════════════════════════════════════════════════════════════════════════════

def _fill(t, first='', last='', number=''):
    """Type into the open contact form: first name, then last, then number."""
    def typ(text):
        for ch in text:
            t.key(f'CHAR:{ch}')
    typ(first)
    t.key('KEY_DOWN'); typ(last)
    t.key('KEY_DOWN'); typ(number)


class NewContactBase(ContactBase):
    def open_new_from_list(self):
        reset_state(screen='contacts_pick', contacts_index=0, contacts_return='home')
        with patch.object(kyphone_os, 'push_screen') as ps:
            kyphone_os.handle_key('KEY_UP')                # header
            kyphone_os.handle_key('KEY_RIGHT')             # +
            kyphone_os.handle_key('KEY_ENTER')
        return _wire(ps)

    def save(self):
        return self.key('KEY_DOWN', 'KEY_ENTER') if kyphone_os.state['edit_index'] == 2 else self.key('KEY_ENTER')


class TestNewContact(NewContactBase):
    def test_plus_in_the_contacts_header_opens_a_blank_form_titled_new_contact(self):
        wire = self.open_new_from_list()
        self.assertEqual(kyphone_os.state['screen'], 'contact_edit')
        self.assertEqual(wire, 'CONTACTEDIT||||0|N')                         # blank, cursor in FIRST NAME, kind N

    def test_editing_an_existing_contact_is_titled_edit_contact(self):
        reset_state(screen='contacts_pick', contacts_index=0, contacts_return='home')
        self.key('KEY_ENTER', 'KEY_UP', 'KEY_RIGHT', 'KEY_ENTER')
        with patch.object(kyphone_os, 'push_screen') as ps:
            kyphone_os.push_contact_edit()
        self.assertTrue(_wire(ps).endswith('|E'))

    def test_saving_creates_the_contact_stores_the_number_formatted_and_shows_its_page(self):
        self.open_new_from_list()
        _fill(self, 'Zed', '', '5550100077')
        self.key('KEY_DOWN', 'KEY_ENTER')
        rec = next(c for c in kyphone_os.CONTACTS if c['first'] == 'Zed')
        self.assertEqual(rec['number'], '(555) 010-0077')
        self.assertEqual(rec['last'], '')                                     # last name is optional
        self.assertEqual(kyphone_os.state['screen'], 'contact')
        self.assertIs(kyphone_os.CONTACTS[kyphone_os.state['contact_idx']], rec)
        self.assertEqual(kyphone_os.state['contact_return'], 'contacts_pick')
        self.assertEqual(kyphone_os.state['contact_sel'], 'call')

    def test_it_lands_in_alphabetical_order(self):
        self.open_new_from_list()
        _fill(self, 'Aaron', '', '5550100077')
        self.key('KEY_DOWN', 'KEY_ENTER')
        self.assertEqual(kyphone_os.dispname(kyphone_os.CONTACTS[0]), 'Aaron')
        self.assertEqual(kyphone_os.CONTACTS[kyphone_os.state['contact_idx']]['first'], 'Aaron')

    def test_x_and_esc_go_back_to_the_contacts_list(self):
        self.open_new_from_list()
        self.key('KEY_ESC')
        self.assertEqual(kyphone_os.state['screen'], 'contacts_pick')
        self.assertEqual(len(kyphone_os.CONTACTS), 4)
        self.open_new_from_list()
        self.key('KEY_UP', 'KEY_ENTER')                                       # the X control
        self.assertEqual(kyphone_os.state['screen'], 'contacts_pick')

    def test_name_fields_hold_18_characters_and_the_number_field_only_number_characters(self):
        self.open_new_from_list()
        _fill(self, 'F' * 25, 'L' * 25, '555-010-0077 x9abc')
        self.assertEqual(kyphone_os.state['edit_first'], 'F' * 18)
        self.assertEqual(kyphone_os.state['edit_last'], 'L' * 18)
        self.assertEqual(kyphone_os.state['edit_number'], '555-010-0077 x9abc'.replace('x', '').replace('abc', '')[:18])


class TestValidation(NewContactBase):
    def alert(self, first='', last='', number=''):
        self.open_new_from_list()
        _fill(self, first, last, number)
        with patch.object(kyphone_os, 'push_screen') as ps:
            kyphone_os.handle_key('KEY_DOWN')
            kyphone_os.handle_key('KEY_ENTER')
        return _wire(ps)

    def test_a_first_name_is_required_and_the_alert_selects_that_field(self):
        wire = self.alert('', 'Only', '5550100077')
        self.assertEqual(wire, 'STUB|CONTACT|A CONTACT NEEDS A FIRST NAME. TYPE ONE IN THE FIRST NAME FIELD, THEN PRESS SAVE.')
        self.assertEqual(kyphone_os.state['edit_index'], 0)
        self.assertEqual(len(kyphone_os.CONTACTS), 4)

    def test_a_number_is_required(self):
        wire = self.alert('Zed', '', '')
        self.assertTrue(wire.startswith('STUB|CONTACT|A CONTACT NEEDS A PHONE NUMBER.'))
        self.assertEqual(kyphone_os.state['edit_index'], 2)

    def test_the_number_must_be_dialable(self):
        for bad in ('555', '55501000123', '25550100077'):
            wire = self.alert('Zed', '', bad)
            self.assertTrue(wire.startswith('STUB|CONTACT|THAT NUMBER CANNOT BE DIALED.'), bad)
            self.assertEqual(kyphone_os.state['edit_index'], 2)
        self.assertEqual(len(kyphone_os.CONTACTS), 4)

    def test_a_duplicate_number_names_the_contact_who_has_it(self):
        wire = self.alert('Zed', '', '1 555 010 0001')                        # Alice's number, typed differently
        self.assertEqual(wire, 'STUB|CONTACT|THAT NUMBER IS ALREADY SAVED AS ALICE TEST. '
                               'EDIT THAT CONTACT INSTEAD, OR TYPE A DIFFERENT NUMBER.')
        self.assertEqual(kyphone_os.state['edit_index'], 2)

    def test_dismissing_an_alert_returns_to_the_form_with_everything_as_typed(self):
        self.alert('Zed', 'Ray', '555')
        self.key('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'contact_edit')
        self.assertEqual((kyphone_os.state['edit_first'], kyphone_os.state['edit_last'],
                          kyphone_os.state['edit_number']), ('Zed', 'Ray', '555'))
        self.assertEqual(kyphone_os.state['edit_index'], 2)
        self.alert('Zed', 'Ray', '555')
        self.key('KEY_ESC')                                                    # Esc dismisses too
        self.assertEqual(kyphone_os.state['screen'], 'contact_edit')

    def test_saving_a_blank_form_raises_the_first_name_alert(self):
        wire = self.alert()
        self.assertTrue(wire.startswith('STUB|CONTACT|A CONTACT NEEDS A FIRST NAME.'))

    def test_saving_an_edit_with_its_own_number_is_not_a_duplicate(self):
        reset_state(screen='contacts_pick', contacts_index=0, contacts_return='home')
        self.key('KEY_ENTER', 'KEY_UP', 'KEY_RIGHT', 'KEY_ENTER')             # edit Alice
        self.key('KEY_DOWN', 'KEY_DOWN', 'KEY_DOWN', 'KEY_ENTER')             # SAVE, nothing changed
        self.assertEqual(kyphone_os.state['screen'], 'contact')
        self.assertEqual(kyphone_os.CONTACTS[0]['number'], '(555) 010-0001')   # and it is now stored formatted

    def test_editing_a_number_to_another_contacts_number_is_a_duplicate(self):
        reset_state(screen='contacts_pick', contacts_index=0, contacts_return='home')
        self.key('KEY_ENTER', 'KEY_UP', 'KEY_RIGHT', 'KEY_ENTER')
        self.key('KEY_DOWN', 'KEY_DOWN')
        for _ in range(20):
            self.key('KEY_BACKSPACE')
        for ch in '5550100004':                                                # the second Bob's number
            self.key(f'CHAR:{ch}')
        wire = self.key('KEY_DOWN', 'KEY_ENTER')
        self.assertTrue(wire.startswith('STUB|CONTACT|THAT NUMBER IS ALREADY SAVED AS BOB.'))


class TestNewContactFromOtherScreens(NewContactBase):
    def test_from_the_compose_picker_a_save_returns_to_compose_with_the_contact_in_to(self):
        reset_state(screen='compose', compose_to='', compose_msg='hello', compose_to_active=True)
        self.key('KEY_ENTER')                                                  # empty TO: opens the picker
        self.assertEqual(kyphone_os.state['screen'], 'contacts_pick')
        self.key('KEY_UP', 'KEY_RIGHT', 'KEY_ENTER')                           # +
        _fill(self, 'Zed', '', '5550100077')
        self.key('KEY_DOWN', 'KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'compose')
        self.assertEqual(kyphone_os.state['compose_to'], '(555) 010-0077')
        self.assertFalse(kyphone_os.state['compose_to_active'])
        self.assertEqual(kyphone_os.state['compose_msg'], 'hello')             # the message in progress survives
        with patch.object(kyphone_os, 'push_screen') as ps:
            kyphone_os.push_compose()
        self.assertTrue(_wire(ps).startswith('COMPOSE|Zed|hello|0|'))          # TO shows the name

    def test_from_the_compose_picker_cancel_returns_to_compose(self):
        reset_state(screen='compose', compose_to='', compose_msg='hello', compose_to_active=True)
        self.key('KEY_ENTER', 'KEY_UP', 'KEY_RIGHT', 'KEY_ENTER', 'KEY_ESC')
        self.assertEqual(kyphone_os.state['screen'], 'compose')
        self.assertEqual(len(kyphone_os.CONTACTS), 4)

    def test_from_an_unsaved_number_a_save_lands_on_its_page_and_esc_goes_back_to_the_thread(self):
        reset_state(screen='thread', thread_id='+15550199002', messages=[_inbound('hi', peer='+15550199002')])
        self.key('KEY_UP', 'KEY_RIGHT', 'KEY_ENTER', 'KEY_RIGHT', 'KEY_RIGHT', 'KEY_ENTER')     # SAVE
        for ch in 'Dave':
            self.key(f'CHAR:{ch}')
        self.key('KEY_DOWN', 'KEY_DOWN', 'KEY_DOWN', 'KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'contact')
        self.assertEqual(kyphone_os.CONTACTS[kyphone_os.state['contact_idx']]['first'], 'Dave')
        self.key('KEY_ESC')
        self.assertEqual(kyphone_os.state['screen'], 'thread')


class TestNoSilentNoOps(SendBase):
    def test_sending_with_an_empty_message_from_compose_raises_the_alert(self):
        reset_state(screen='compose', compose_to='+15550109999', compose_msg='   ')
        with patch.object(kyphone_os, 'push_screen') as ps:
            kyphone_os._send_compose()
        self.assertEqual(_wire(ps), 'STUB|NEW MESSAGE|THERE IS NOTHING TO SEND. TYPE A MESSAGE FIRST, THEN PRESS SEND.')
        self.assertEqual(kyphone_os.state['stub_return'], 'compose')
        self.transport.assert_not_called()

    def test_sending_with_no_recipient_raises_the_alert(self):
        reset_state(screen='compose', compose_to='', compose_msg='hello')
        with patch.object(kyphone_os, 'push_screen') as ps:
            kyphone_os._send_compose()
        self.assertTrue(_wire(ps).startswith('STUB|NEW MESSAGE|THERE IS NO ONE TO SEND THIS TO.'))
        self.transport.assert_not_called()

    def test_no_recipient_is_reported_before_an_empty_message(self):
        reset_state(screen='compose', compose_to='', compose_msg='')
        with patch.object(kyphone_os, 'push_screen') as ps:
            kyphone_os._send_compose()
        self.assertIn('NO ONE TO SEND THIS TO', _wire(ps))

    def test_enter_or_esc_dismisses_the_alert_back_to_compose_intact(self):
        for dismiss in ('KEY_ENTER', 'KEY_ESC'):
            reset_state(screen='compose', compose_to='+15550109999', compose_msg='', compose_to_active=False)
            with patch.object(kyphone_os, 'push_screen'):
                kyphone_os._send_compose()
            self.key(dismiss)
            self.assertEqual(kyphone_os.state['screen'], 'compose')
            self.assertEqual(kyphone_os.state['compose_to'], '+15550109999')

    def test_a_thread_with_an_empty_draft_raises_the_alert_and_returns_to_the_thread(self):
        self.key('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'stub')
        self.key('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'thread')
        self.transport.assert_not_called()

    def test_a_rejected_keystroke_is_the_only_silence(self):
        reset_state(screen='contact_edit', edit_idx=None, edit_first='', edit_last='', edit_number='', edit_index=2)
        with patch.object(kyphone_os, 'push_screen') as ps:
            kyphone_os.handle_key('CHAR:x')                                    # not a number character
        self.assertIsNone(ps.call_args)
        self.assertEqual(kyphone_os.state['edit_number'], '')


class TestNewMessageScreenLimits(SendBase):
    def setUp(self):
        super().setUp()
        reset_state(screen='compose', compose_to='', compose_msg='', compose_to_active=True)

    def compose_wire(self):
        with patch.object(kyphone_os, 'push_screen') as ps:
            kyphone_os.push_compose()
        return _wire(ps)

    def test_to_holds_20_characters(self):
        self.type_text('9' * 30)
        self.assertEqual(kyphone_os.state['compose_to'], '9' * 20)

    def test_the_message_has_no_cap(self):
        self.key('KEY_TAB')
        self.type_text('m' * 500)
        self.assertEqual(len(kyphone_os.state['compose_msg']), 500)

    def test_a_short_message_is_sent_whole_and_a_long_one_shows_its_end_within_the_frame(self):
        reset_state(screen='compose', compose_to='+15550100001', compose_msg='short one', compose_to_active=False)
        self.assertIn('|short one|', self.compose_wire())
        reset_state(screen='compose', compose_to='+15550100001', compose_msg='start ' + 'w' * 400 + ' the end',
                    compose_to_active=False)
        wire = self.compose_wire()
        self.assertLessEqual(len(wire), kyphone_os.MAX_COMMAND_CHARS)
        msg = wire.split('|')[2]
        self.assertTrue(msg.startswith('...'))
        self.assertTrue(msg.endswith('w the end'))

    def test_a_long_message_is_still_sent_in_full(self):
        reset_state(screen='compose', compose_to='+15550100001', compose_msg='x' * 500, compose_to_active=False)
        with patch.object(kyphone_os, 'push_screen'):
            kyphone_os._send_compose()
        self.transport.assert_called_once_with('+15550100001', 'x' * 500)


class TestComposeArrowUp(SendBase):
    """Arrow up moves one step: SEND -> MESSAGE -> TO -> the X in the header."""
    def press(self, *keys):
        with patch.object(kyphone_os, 'push_screen'):
            for k in keys:
                kyphone_os.handle_key(k)

    def test_up_walks_from_send_to_message_to_to_to_the_header(self):
        reset_state(screen='compose', compose_to='+15550100001', compose_msg='hi', compose_to_active=False,
                    compose_send_sel=True)
        self.press('KEY_UP')
        self.assertFalse(kyphone_os.state['compose_send_sel'])            # SEND -> MESSAGE
        self.assertFalse(kyphone_os.state['compose_to_active'])
        self.assertIsNone(kyphone_os.state['compose_header_sel'])
        self.press('KEY_UP')
        self.assertTrue(kyphone_os.state['compose_to_active'])            # MESSAGE -> TO
        self.assertIsNone(kyphone_os.state['compose_header_sel'])
        self.press('KEY_UP')
        self.assertEqual(kyphone_os.state['compose_header_sel'], 'x')     # TO -> the X
        self.press('KEY_DOWN')
        self.assertIsNone(kyphone_os.state['compose_header_sel'])         # and back down into the form

    def test_tab_leaving_the_send_button_clears_it(self):
        reset_state(screen='compose', compose_to='+15550100001', compose_msg='hi', compose_to_active=False,
                    compose_send_sel=True)
        self.press('KEY_TAB')
        self.assertFalse(kyphone_os.state['compose_send_sel'])

    def test_up_on_the_plus_button_does_nothing(self):
        reset_state(screen='compose', compose_to='', compose_msg='', compose_to_active=True, compose_plus_sel=True)
        self.press('KEY_UP')
        self.assertIsNone(kyphone_os.state['compose_header_sel'])
        self.assertTrue(kyphone_os.state['compose_plus_sel'])


# ═══════════════════════════════════════════════════════════════════════════════
# Delete a contact, and the confirmation screen (OS 0.2.1 step 6)
# ═══════════════════════════════════════════════════════════════════════════════

class DeleteBase(ContactBase):
    def open_edit(self, row):
        """Contacts list -> the edit form of the contact in `row`."""
        reset_state(screen='contacts_pick', contacts_index=0, contacts_return='home')
        self.key(*(['KEY_DOWN'] * row), 'KEY_ENTER', 'KEY_UP', 'KEY_RIGHT', 'KEY_ENTER')

    def to_delete_button(self, row):
        self.open_edit(row)
        self.key('KEY_DOWN', 'KEY_DOWN', 'KEY_DOWN', 'KEY_LEFT')      # ... SAVE, then left to DELETE


class TestDeleteButton(DeleteBase):
    def test_left_from_save_reaches_delete_and_right_returns_to_save(self):
        self.open_edit(0)
        self.key('KEY_DOWN', 'KEY_DOWN', 'KEY_DOWN')
        self.assertEqual(kyphone_os.state['edit_index'], kyphone_os.EDIT_SAVE)
        self.key('KEY_LEFT')
        self.assertEqual(kyphone_os.state['edit_index'], kyphone_os.EDIT_DELETE)
        self.key('KEY_RIGHT')
        self.assertEqual(kyphone_os.state['edit_index'], kyphone_os.EDIT_SAVE)

    def test_arrow_up_from_delete_goes_to_the_number_field_and_down_never_reaches_it(self):
        self.to_delete_button(0)
        self.key('KEY_UP')
        self.assertEqual(kyphone_os.state['edit_index'], 2)
        self.key('KEY_DOWN', 'KEY_DOWN', 'KEY_DOWN')
        self.assertEqual(kyphone_os.state['edit_index'], kyphone_os.EDIT_SAVE)

    def test_a_new_contact_has_no_delete_button(self):
        reset_state(screen='contacts_pick', contacts_index=0, contacts_return='home')
        self.key('KEY_UP', 'KEY_RIGHT', 'KEY_ENTER')                   # + -> NEW CONTACT
        self.key('KEY_DOWN', 'KEY_DOWN', 'KEY_DOWN', 'KEY_LEFT')
        self.assertEqual(kyphone_os.state['edit_index'], kyphone_os.EDIT_SAVE)

    def test_typing_does_nothing_while_delete_is_selected(self):
        self.to_delete_button(0)
        self.key('CHAR:x', 'KEY_BACKSPACE')
        self.assertEqual(kyphone_os.state['edit_first'], 'Alice')
        self.assertEqual(kyphone_os.state['edit_number'], '+15550100001')


class TestDeleteConfirm(DeleteBase):
    def confirm_wire(self, row=0):
        self.to_delete_button(row)
        return self.key('KEY_ENTER')

    def test_enter_on_delete_asks_first_and_the_safe_button_is_selected(self):
        wire = self.confirm_wire(0)
        self.assertEqual(kyphone_os.state['screen'], 'confirm')
        self.assertEqual(wire, 'CONFIRM|DELETE CONTACT|DELETE ALICE TEST? THE MESSAGES STAY IN THE TEXT LIST, '
                               'LABELED WITH THE NUMBER. THE NAME CANNOT BE BROUGHT BACK.|DELETE|KEEP CONTACT|K')
        self.assertEqual(len(kyphone_os.CONTACTS), 4)                   # nothing deleted yet

    def test_left_selects_the_destructive_button_and_right_the_safe_one(self):
        self.confirm_wire()
        self.assertTrue(self.key('KEY_LEFT').endswith('|D'))
        self.assertTrue(self.key('KEY_RIGHT').endswith('|K'))

    def test_enter_on_the_safe_button_keeps_the_contact_and_returns_to_the_form(self):
        self.confirm_wire()
        self.key('KEY_ENTER')
        self.assertEqual(len(kyphone_os.CONTACTS), 4)
        self.assertEqual(kyphone_os.state['screen'], 'contact_edit')
        self.assertEqual(kyphone_os.state['edit_index'], kyphone_os.EDIT_DELETE)

    def test_esc_is_the_safe_choice_too_even_with_delete_selected(self):
        self.confirm_wire()
        self.key('KEY_LEFT', 'KEY_ESC')
        self.assertEqual(len(kyphone_os.CONTACTS), 4)
        self.assertEqual(kyphone_os.state['screen'], 'contact_edit')
        self.assertEqual(kyphone_os.state['confirm_sel'], 'keep')       # and it re-opens on the safe button

    def test_the_destructive_button_deletes_and_lands_on_the_contacts_list(self):
        self.confirm_wire(0)
        wire = self.key('KEY_LEFT', 'KEY_ENTER')
        self.assertEqual([kyphone_os.dispname(c) for c in kyphone_os.CONTACTS], ['Bob', 'Sam Whitfield', 'Bob'])
        self.assertEqual(kyphone_os.state['screen'], 'contacts_pick')
        self.assertEqual((kyphone_os.state['contacts_query'], kyphone_os.state['contacts_index'],
                          kyphone_os.state['contacts_start'], kyphone_os.state['contacts_header_sel'],
                          kyphone_os.state['contacts_return']), ('', 0, 0, 'back', 'home'))
        self.assertTrue(wire.startswith('CONTACTSPICK|0||1 / 3|'))

    def test_the_change_is_saved(self):
        self.confirm_wire(0)
        self.key('KEY_LEFT', 'KEY_ENTER')
        saved = self._cs_patch.target._save_contacts                    # the mock installed by ListWindowBase
        self.assertEqual(len(saved.call_args[0][0]), 3)

    def test_deleting_the_second_of_two_same_named_contacts_removes_that_one(self):
        self.confirm_wire(3)                                            # the second "Bob"
        wire = self.key('KEY_LEFT', 'KEY_ENTER')
        self.assertEqual([c['number'] for c in kyphone_os.CONTACTS],
                         ['+15550100001', '(555) 010-0002', ''])        # the first Bob (with his number) is still there

    def test_the_conversation_stays_and_falls_back_to_the_formatted_number(self):
        self.to_delete_button(0)
        kyphone_os.state['messages'] = [_inbound('still here', peer='+15550100001')]   # after the helper's reset
        self.assertEqual(kyphone_os.get_threads()[0]['name'], 'Alice Test')
        self.key('KEY_ENTER', 'KEY_LEFT', 'KEY_ENTER')
        threads = kyphone_os.get_threads()
        self.assertEqual(len(threads), 1)                               # no thread disappears
        self.assertEqual(threads[0]['name'], '(555) 010-0001')          # the row falls back to the number
        self.assertEqual(threads[0]['messages'][0]['body'], 'still here')

    def test_the_wire_fits_the_frame_for_the_longest_name(self):
        kyphone_os.CONTACTS[0].update(first='F' * 18, last='L' * 18)
        wire = self.confirm_wire(0)
        self.assertLessEqual(len(wire), kyphone_os.MAX_COMMAND_CHARS)


class TestDiscardConfirmStillWorks(SendBase):
    def start(self, msg='hello'):
        reset_state(screen='compose', compose_to='+15550100001', compose_msg=msg, compose_to_active=False)
        with patch.object(kyphone_os, 'push_screen') as ps:
            kyphone_os.handle_key('KEY_ESC')
        return _wire(ps)

    def test_leaving_a_message_in_progress_asks_with_the_safe_button_selected(self):
        wire = self.start()
        self.assertEqual(kyphone_os.state['screen'], 'confirm')
        self.assertEqual(wire, 'CONFIRM|NEW MESSAGE|DISCARD THIS MESSAGE? IT HAS NOT BEEN SENT, AND THE PHONE KEEPS NO '
                               'DRAFTS, SO THE TEXT CANNOT BE BROUGHT BACK.|DISCARD|KEEP EDITING|K')

    def test_keep_editing_returns_to_the_message(self):
        self.start()
        with patch.object(kyphone_os, 'push_screen'):
            kyphone_os.handle_key('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'compose')
        self.assertEqual(kyphone_os.state['compose_msg'], 'hello')

    def test_discard_clears_the_message_and_goes_to_the_texts_list(self):
        self.start()
        with patch.object(kyphone_os, 'push_screen'):
            kyphone_os.handle_key('KEY_LEFT')
            kyphone_os.handle_key('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'texts_list')
        self.assertEqual(kyphone_os.state['compose_msg'], '')
        self.assertEqual(kyphone_os.state['compose_to'], '')

    def test_an_empty_compose_leaves_without_asking(self):
        reset_state(screen='compose', compose_to='', compose_msg='')
        with patch.object(kyphone_os, 'push_screen'):
            kyphone_os.handle_key('KEY_ESC')
        self.assertEqual(kyphone_os.state['screen'], 'texts_list')


# ═══════════════════════════════════════════════════════════════════════════════
# Home menu order (OS 0.2.1 step 7)
# ═══════════════════════════════════════════════════════════════════════════════

class TestHomeMenuOrder(unittest.TestCase):
    def setUp(self):
        self._save_patch = patch.object(kyphone_os, 'save_messages')
        self._save_patch.start()

    def tearDown(self):
        self._save_patch.stop()

    def enter_row(self, index, **state):
        reset_state(screen='home', home_index=index, **state)
        with patch.object(kyphone_os, 'push_screen') as ps:
            kyphone_os.handle_key('KEY_ENTER')
        return _wire(ps)

    def test_contacts_is_third(self):
        self.assertEqual(kyphone_os.HOME_MENU, ['TEXT', 'CALL', 'CONTACTS', 'READ', 'LISTEN'])

    def test_each_row_opens_its_screen(self):
        self.enter_row(0);  self.assertEqual(kyphone_os.state['screen'], 'texts_list')
        self.enter_row(1);  self.assertEqual(kyphone_os.state['screen'], 'calls_list')
        self.enter_row(2);  self.assertEqual(kyphone_os.state['screen'], 'contacts_pick')
        self.enter_row(3);  self.assertEqual((kyphone_os.state['screen'], kyphone_os.state['stub_key']), ('stub', 'READ'))
        self.enter_row(4);  self.assertEqual((kyphone_os.state['screen'], kyphone_os.state['stub_key']), ('stub', 'LISTEN'))

    def test_read_and_listen_alerts_use_their_own_text_not_a_leftover_alert(self):
        kyphone_os.state['stub_text'] = ('CONTACT', 'A LEFTOVER ALERT')
        wire = self.enter_row(3)
        self.assertTrue(wire.startswith('STUB|READ|READ CANNOT OPEN YET.'))

    def test_contacts_opens_a_fresh_list_returning_to_home(self):
        self.enter_row(2, contacts_query='old', contacts_index=5, contacts_start=3)
        self.assertEqual((kyphone_os.state['contacts_query'], kyphone_os.state['contacts_index'],
                          kyphone_os.state['contacts_start'], kyphone_os.state['contacts_return']), ('', 0, 0, 'home'))

    def test_texts_and_calls_open_with_their_windows_at_the_top(self):
        self.enter_row(0, texts_index=6, texts_start=4)
        self.assertEqual((kyphone_os.state['texts_index'], kyphone_os.state['texts_start']), (0, 0))
        self.enter_row(1, calls_index=5, calls_start=2)
        self.assertEqual((kyphone_os.state['calls_index'], kyphone_os.state['calls_start']), (0, 0))

    def test_leaving_contacts_returns_to_the_contacts_row(self):
        reset_state(screen='contacts_pick', contacts_return='home', contacts_index=0)
        with patch.object(kyphone_os, 'push_screen') as ps:
            kyphone_os.handle_key('KEY_ESC')
        self.assertEqual(kyphone_os.state['screen'], 'home')
        self.assertEqual(kyphone_os.state['home_index'], 2)
        self.assertEqual(_wire(ps).split('|')[2], '2')                          # HOME2|time|2|unread|style

    def test_down_walks_the_new_order_and_stops_at_listen(self):
        reset_state(screen='home', home_index=0)
        with patch.object(kyphone_os, 'push_screen'):
            seen = []
            for _ in range(6):
                kyphone_os.handle_key('KEY_DOWN')
                seen.append(kyphone_os.HOME_MENU[kyphone_os.state['home_index']])
        self.assertEqual(seen, ['CALL', 'CONTACTS', 'READ', 'LISTEN', 'LISTEN', 'LISTEN'])

class TestHomeStyle(unittest.TestCase):
    def wire(self, style=None):
        reset_state(screen='home', home_index=0)
        with patch.object(kyphone_os, 'push_screen') as ps:
            if style is None:
                kyphone_os.push_home2()
            else:
                with patch.object(kyphone_os, 'HOME_STYLE', style):
                    kyphone_os.push_home2()
        return _wire(ps)

    def test_the_wire_carries_the_style_and_icons_are_the_default(self):
        self.assertEqual(kyphone_os.HOME_STYLE, 'I')
        self.assertTrue(self.wire().endswith('|I'))
        self.assertTrue(self.wire('B').endswith('|B'))
        self.assertTrue(self.wire('W').endswith('|W'))

    def test_the_setting_names_map_to_the_wire_letters(self):
        self.assertEqual(kyphone_os.HOME_STYLES, {'icons': 'I', 'both': 'B', 'words': 'W'})


if __name__ == '__main__':
    unittest.main()
