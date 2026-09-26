"""
test_state_machine.py — Unit tests for KyPhone OS 0.1 state machine.

Runs without hardware (SPI/GPIO) or a display. All SPI sends are mocked.

    python3 -m pytest spi_bridge/tests/test_state_machine.py -v
"""

import os
import sys
import json
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

# ── Mock hardware modules before importing kyphone_os ─────────────────────────
sys.argv = ['test', '--sim']  # force SIM_MODE=True so hardware imports are skipped
sys.modules.setdefault('spidev', MagicMock())
sys.modules.setdefault('gpiod', MagicMock())
sys.modules.setdefault('input_handler', MagicMock())
# pygame and simulator are faked only for the import below and only if the real ones are not
# already loaded. The fakes are removed afterwards: left in sys.modules they made the simulator
# and firmware test files skip silently when this file was collected first.
_faked = [name for name in ('pygame', 'simulator') if name not in sys.modules]
for _name in _faked:
    sys.modules[_name] = MagicMock()
sys.modules.setdefault('evdev', MagicMock())

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import kyphone_os  # noqa: E402  (import after sys.path manipulation)
for _name in _faked:
    if isinstance(sys.modules.get(_name), MagicMock):
        del sys.modules[_name]

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
        'running': True,
    }
    defaults.update(overrides)
    for k, v in defaults.items():
        kyphone_os.state[k] = v


# ═══════════════════════════════════════════════════════════════════════════════
# Lock Screen
# ═══════════════════════════════════════════════════════════════════════════════

class TestLockScreenVersion(unittest.TestCase):
    def test_the_lock_command_carries_the_version_and_fits_with_every_quote(self):
        import version
        for i in range(len(kyphone_os.QUOTES)):
            reset_state(screen='lock', quote_index=i, messages=[], calls=[], light=8, activity_mark=True)
            kyphone_os.state['messages'] = [_inbound(read=False)]                 # the longest tail: '*' and 8
            with patch.object(kyphone_os, 'push_screen') as ps:
                kyphone_os.push_lock()
            wire = _wire(ps)
            self.assertTrue(wire.endswith('|- THICH NHAT HANH|' + version.VERSION + '|*|8'), wire[-40:])
            self.assertLessEqual(len(wire), kyphone_os.MAX_COMMAND_CHARS, i)


class TestActivityMark(unittest.TestCase):
    def lock_tail(self):
        with patch.object(kyphone_os, 'push_screen') as ps:
            kyphone_os.push_lock()
        return _wire(ps).split('|')[-2:]                                          # [mark, light]

    def setUp(self):
        reset_state(screen='lock', messages=[], calls=[], light=0, activity_mark=True)

    def test_nothing_new_no_mark(self):
        kyphone_os.state['messages'] = [_inbound(read=True)]
        self.assertEqual(self.lock_tail(), ['', '0'])

    def test_an_unread_text_shows_the_mark(self):
        kyphone_os.state['messages'] = [_inbound(read=False)]
        self.assertEqual(self.lock_tail()[0], '*')

    def test_my_own_unsent_text_is_not_new_activity(self):
        kyphone_os.state['messages'] = [{'dir': 'out', 'peer': ALICE, 'name': 'You', 'body': 'x', 'read': False,
                                         'ts': datetime.now().isoformat(), 'state': 'not_sent'}]
        self.assertEqual(self.lock_tail()[0], '')

    def test_a_missed_call_shows_the_mark_until_the_call_list_is_opened(self):
        kyphone_os.state['call_name'] = 'Ann'
        with patch.object(kyphone_os, '_save_calls') as save:
            kyphone_os._log_call('MISS')
            self.assertEqual(self.lock_tail()[0], '*')
            kyphone_os.state['screen'] = 'home'
            kyphone_os.state['home_index'] = kyphone_os.HOME_MENU.index('CALL')
            with patch.object(kyphone_os, 'push_screen'):
                kyphone_os.handle_key('KEY_ENTER')                                # open the call list
            self.assertGreaterEqual(save.call_count, 2)                            # the seen flag is saved
        self.assertEqual(self.lock_tail()[0], '')

    def test_answered_calls_never_show_the_mark(self):
        kyphone_os.state['call_name'] = 'Ann'
        with patch.object(kyphone_os, '_save_calls'):
            kyphone_os._log_call('IN', 30)
        self.assertEqual(self.lock_tail()[0], '')

    def test_switched_off_there_is_no_mark(self):
        kyphone_os.state['messages'] = [_inbound(read=False)]
        kyphone_os.state['activity_mark'] = False
        self.assertEqual(self.lock_tail()[0], '')

    def test_an_unseen_missed_call_survives_a_restart_and_old_logs_count_as_seen(self):
        path = os.path.join(tempfile.mkdtemp(), 'calls.json')
        with open(path, 'w') as f:
            json.dump([{'name': 'Ann', 'tag': 'MISS', 'ts': '', 'duration': '', 'seen': False},
                       {'name': 'Bo', 'tag': 'MISS', 'ts': '', 'duration': ''}], f)
        with patch.object(kyphone_os, 'CALLS_FILE', path):
            kyphone_os.load_calls()
        self.assertEqual([c.get('seen', True) for c in kyphone_os.state['calls']], [False, True])

    def test_a_text_arriving_on_the_lock_screen_redraws_it(self):
        fake = md.SimModem()
        fake.deliver('+15550100009', 'hi')
        kyphone_os._modem = fake
        self.addCleanup(setattr, kyphone_os, '_modem', None)
        reset_state(screen='lock', running=True, messages=[], calls=[])
        def stop(_):
            kyphone_os.state['running'] = False
        with patch.object(kyphone_os, 'save_messages'), patch.object(kyphone_os.time, 'sleep', stop), \
                patch.object(kyphone_os, 'push_screen') as ps:
            kyphone_os.modem_sms_loop()
        self.assertTrue(_wire(ps).startswith('LOCK|'))
        self.assertEqual(_wire(ps).split('|')[-2], '*')


class TestSavedSettings(unittest.TestCase):
    def test_round_trip_and_bad_values_keep_defaults(self):
        path = os.path.join(tempfile.mkdtemp(), 'settings.json')
        with patch.object(kyphone_os, 'SETTINGS_FILE', path):
            reset_state(light=5, activity_mark=False)
            kyphone_os.save_settings()
            reset_state(light=0, activity_mark=True)
            kyphone_os.load_settings()
            self.assertEqual((kyphone_os.state['light'], kyphone_os.state['activity_mark']), (5, False))
            for bad in ({'light': 99, 'activity_mark': 'yes'}, {'light': True}, [], 'x'):
                with open(path, 'w') as f:
                    json.dump(bad, f)
                reset_state(light=2, activity_mark=True)
                kyphone_os.load_settings()
                self.assertEqual((kyphone_os.state['light'], kyphone_os.state['activity_mark']), (2, True), bad)
            os.remove(path)
            kyphone_os.load_settings()                                             # no file: nothing changes
            self.assertEqual(kyphone_os.state['light'], 2)

    def test_the_os_uses_the_one_version(self):
        import version
        self.assertEqual(kyphone_os.VERSION, version.VERSION)


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
    def test_down_clamped_at_the_last_row(self, _ps):
        last = len(kyphone_os.HOME_MENU) - 1
        reset_state(screen='home', home_index=last)
        kyphone_os.handle_key('KEY_DOWN')
        self.assertEqual(kyphone_os.state['home_index'], last)

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
        reset_state(screen='home', home_index=0, messages=list(_MSGS))     # with conversations: starts on the first row
        kyphone_os.handle_key('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'texts_list')
        self.assertEqual(kyphone_os.state['texts_index'], 0)

    @patch.object(kyphone_os, 'READING_FILE', '/nonexistent-kyphone/reading.json')
    @patch.object(kyphone_os, 'BOOKS_DIR', '/nonexistent-kyphone/books')
    @patch.object(kyphone_os, 'push_screen')
    def test_enter_read_opens_the_library(self, _ps):
        reset_state(screen='home', home_index=kyphone_os.HOME_MENU.index('READ'))
        kyphone_os.handle_key('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'library')

    @patch.object(kyphone_os, 'LISTENING_FILE', '/nonexistent-kyphone/listening.json')
    @patch.object(kyphone_os, 'MUSIC_INDEX_FILE', '/nonexistent-kyphone/music_index.json')
    @patch.object(kyphone_os, 'MUSIC_DIR', '/nonexistent-kyphone/music')
    @patch.object(kyphone_os, 'push_screen')
    def test_enter_listen_opens_the_music_list(self, _ps):
        reset_state(screen='home', home_index=kyphone_os.HOME_MENU.index('LISTEN'))
        kyphone_os.handle_key('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'music')

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


class TestEmptyTextsList(unittest.TestCase):
    """An empty list has no row to select, so it opens with `+` selected: the one useful action."""

    def setUp(self):
        reset_state(screen='home', home_index=0, texts_index=0, texts_header_sel='back', messages=[])
        self._save_patch = patch.object(kyphone_os, 'save_messages')
        self._save_patch.start()
        self._ps = patch.object(kyphone_os, 'push_screen')
        self.ps = self._ps.start()

    def tearDown(self):
        self._ps.stop()
        self._save_patch.stop()

    def open_texts(self):
        kyphone_os.handle_key('KEY_ENTER')                                    # TEXT is the first home row
        self.assertEqual(kyphone_os.state['screen'], 'texts_list')

    def test_it_opens_with_plus_selected(self):
        self.open_texts()
        self.assertEqual(_wire(self.ps), 'TEXTS|-2')
        self.assertEqual((kyphone_os.state['texts_index'], kyphone_os.state['texts_header_sel']), (-1, 'plus'))

    def test_however_it_is_reached_an_empty_list_shows_plus_selected(self):
        reset_state(screen='texts_list', texts_index=0, texts_header_sel='back', messages=[])
        kyphone_os.push_texts()                                               # e.g. returning from another screen
        self.assertEqual(_wire(self.ps), 'TEXTS|-2')

    def test_left_and_right_move_between_back_and_plus_straight_away(self):
        self.open_texts()
        kyphone_os.handle_key('KEY_LEFT')
        self.assertEqual(_wire(self.ps), 'TEXTS|-1')
        kyphone_os.handle_key('CHAR:d')                                       # D is Right
        self.assertEqual(_wire(self.ps), 'TEXTS|-2')
        kyphone_os.handle_key('CHAR:a')                                       # A is Left
        self.assertEqual(_wire(self.ps), 'TEXTS|-1')
        kyphone_os.handle_key('KEY_RIGHT')
        self.assertEqual(_wire(self.ps), 'TEXTS|-2')

    def test_enter_on_plus_writes_a_message_and_enter_on_back_goes_home(self):
        self.open_texts()
        kyphone_os.handle_key('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'compose')
        reset_state(screen='texts_list', texts_index=-1, texts_header_sel='back', messages=[])
        kyphone_os.handle_key('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'home')

    def test_down_and_up_have_nowhere_to_go_and_change_nothing(self):
        self.open_texts()
        count = self.ps.call_count
        kyphone_os.handle_key('KEY_DOWN')
        kyphone_os.handle_key('KEY_UP')
        self.assertEqual(self.ps.call_count, count)
        self.assertEqual((kyphone_os.state['texts_index'], kyphone_os.state['texts_header_sel']), (-1, 'plus'))

    def test_down_does_not_undo_a_back_selection(self):
        self.open_texts()
        kyphone_os.handle_key('KEY_LEFT')
        kyphone_os.handle_key('KEY_DOWN')
        self.assertEqual(kyphone_os.state['texts_header_sel'], 'back')

    def test_the_plus_key_and_esc_still_work(self):
        self.open_texts()
        kyphone_os.handle_key('KEY_ESC')
        self.assertEqual(kyphone_os.state['screen'], 'home')
        reset_state(screen='texts_list', texts_index=-1, texts_header_sel='plus', messages=[])
        kyphone_os.handle_key('CHAR:+')
        self.assertEqual(kyphone_os.state['screen'], 'compose')

    def test_a_list_with_conversations_still_opens_on_its_first_row(self):
        reset_state(screen='home', home_index=0, texts_index=0, messages=list(_MSGS))
        kyphone_os.handle_key('KEY_ENTER')
        self.assertTrue(_wire(self.ps).startswith('TEXTS|0|'))


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
        reset_state(screen='compose', compose_to='+15550101999', compose_msg='', compose_to_active=True)
        kyphone_os.handle_key('KEY_ENTER')
        self.assertFalse(kyphone_os.state['compose_to_active'])
        self.assertEqual(kyphone_os.state['screen'], 'compose')

    @patch.object(kyphone_os, 'push_screen')
    @patch.object(kyphone_os, 'send_reply')
    def test_enter_on_message_with_both_creates_thread(self, mock_send, _ps):
        reset_state(screen='compose', compose_to='+15550101999', compose_msg='Hello!',
                    compose_to_active=False)
        kyphone_os.handle_key('KEY_ENTER')
        mock_send.assert_called_once_with('+15550101999', 'Hello!')
        self.assertEqual(kyphone_os.state['screen'], 'thread')
        self.assertEqual(kyphone_os.state['thread_id'], '+15550101999')

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
        # five max-length names/previews and the longest weekday name reachable within the last week
        # (usually "WEDNESDAY"; today shows as a time and yesterday as "Yesterday", never a weekday
        # name, so on Wednesdays and Thursdays this picks the longest of the other five instead):
        # the column caps alone come to ~267 characters, over the 253-character frame.
        candidates = [datetime.now() - timedelta(days=k) for k in range(2, 7)]
        worst = max(candidates, key=lambda d: len(d.strftime('%A')))
        msgs = [{'sender': f'+1555000{i:04d}', 'name': 'x', 'body': 'p' * 60, 'read': False,
                 'ts': worst.isoformat()} for i in range(5)]
        reset_state(screen='texts_list', messages=msgs)
        kyphone_os.CONTACTS[:] = [{'first': 'N' * 14, 'last': '', 'number': m['sender']} for m in msgs]
        wire = self._push()
        self.assertLessEqual(len(wire), kyphone_os.MAX_COMMAND_CHARS)
        rows = _rows(wire, 2)
        self.assertEqual(len(rows), 5)
        expected = worst.strftime('%A').upper()
        for row in rows:
            self.assertEqual(row.count(CELL), 3)              # no row cut mid-field
            self.assertEqual(row.split(CELL)[3], expected)    # the time survives intact

    def test_empty_list_sends_no_rows_and_selects_plus(self):
        reset_state(screen='texts_list', messages=[])
        self.assertEqual(self._push(), 'TEXTS|-2')


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

    def test_no_contacts_sends_no_rows_and_selects_plus(self):
        kyphone_os.CONTACTS[:] = []
        self.assertEqual(self._push(), 'CONTACTSPICK|-2||')

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
        legacy = {'sender': '+15550100099', 'name': 'You', 'body': 'old reply', 'read': True,
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
                                     'ts': datetime.now().isoformat(), 'state': 'sending'}], 'last_sid': 'SMxxxx'}, f)
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

    ROW = staticmethod(lambda name: kyphone_os.HOME_MENU.index(name))

    def test_the_order_is_texts_calls_books_music_address_book_settings(self):
        # Kyle's order (2026-09-19; SETTINGS added 2026-09-22, NOTES 2026-09-25). The design handoff had
        # CONTACTS third; that reordering is deliberate.
        self.assertEqual(kyphone_os.HOME_MENU, ['TEXT', 'CALL', 'READ', 'LISTEN', 'CONTACTS', 'NOTES', 'SETTINGS'])

    def test_each_row_opens_its_screen(self):
        self.enter_row(self.ROW('TEXT'));  self.assertEqual(kyphone_os.state['screen'], 'texts_list')
        self.enter_row(self.ROW('CALL'));  self.assertEqual(kyphone_os.state['screen'], 'calls_list')
        self.enter_row(self.ROW('CONTACTS'));  self.assertEqual(kyphone_os.state['screen'], 'contacts_pick')
        with patch.object(kyphone_os, 'BOOKS_DIR', '/nonexistent-kyphone/books'), \
                patch.object(kyphone_os, 'READING_FILE', '/nonexistent-kyphone/reading.json'):
            self.enter_row(self.ROW('READ'));  self.assertEqual(kyphone_os.state['screen'], 'library')
            with patch.object(kyphone_os, 'MUSIC_DIR', '/nonexistent-kyphone/music'), \
                    patch.object(kyphone_os, 'MUSIC_INDEX_FILE', '/nonexistent-kyphone/music_index.json'), \
                    patch.object(kyphone_os, 'LISTENING_FILE', '/nonexistent-kyphone/listening.json'):
                self.enter_row(self.ROW('LISTEN'));  self.assertEqual(kyphone_os.state['screen'], 'music')
        with patch.object(kyphone_os.netctl, 'wifi_status', return_value=kyphone_os.netctl.WifiStatus(False)), \
                patch.object(kyphone_os.netctl, 'bt_status', return_value=kyphone_os.netctl.BtStatus(True, [])):
            self.enter_row(self.ROW('SETTINGS'));  self.assertEqual(kyphone_os.state['screen'], 'settings')

    def test_contacts_opens_a_fresh_list_returning_to_home(self):
        saved = list(kyphone_os.CONTACTS)
        kyphone_os.CONTACTS[:] = _contacts(3)                                # with contacts, the list opens on the first row
        try:
            self.enter_row(self.ROW('CONTACTS'), contacts_query='old', contacts_index=5, contacts_start=3)
        finally:
            kyphone_os.CONTACTS[:] = saved
        self.assertEqual((kyphone_os.state['contacts_query'], kyphone_os.state['contacts_index'],
                          kyphone_os.state['contacts_start'], kyphone_os.state['contacts_return']), ('', 0, 0, 'home'))

    def test_texts_and_calls_open_with_their_windows_at_the_top(self):
        self.enter_row(0, texts_index=6, texts_start=4, messages=list(_MSGS))
        self.assertEqual((kyphone_os.state['texts_index'], kyphone_os.state['texts_start']), (0, 0))
        self.enter_row(1, calls_index=5, calls_start=2)
        self.assertEqual((kyphone_os.state['calls_index'], kyphone_os.state['calls_start']), (0, 0))

    def test_leaving_contacts_returns_to_the_contacts_row(self):
        reset_state(screen='contacts_pick', contacts_return='home', contacts_index=0)
        with patch.object(kyphone_os, 'push_screen') as ps:
            kyphone_os.handle_key('KEY_ESC')
        self.assertEqual(kyphone_os.state['screen'], 'home')
        row = self.ROW('CONTACTS')
        self.assertEqual(kyphone_os.state['home_index'], row)
        self.assertEqual(_wire(ps).split('|')[2], str(row))                     # HOME2|time|row|unread|style

    def test_down_walks_the_order_and_stops_at_the_last_row(self):
        reset_state(screen='home', home_index=0)
        with patch.object(kyphone_os, 'push_screen'):
            seen = []
            for _ in range(8):
                kyphone_os.handle_key('KEY_DOWN')
                seen.append(kyphone_os.HOME_MENU[kyphone_os.state['home_index']])
        self.assertEqual(seen, ['CALL', 'READ', 'LISTEN', 'CONTACTS', 'NOTES', 'SETTINGS', 'SETTINGS', 'SETTINGS'])

class TestNotes(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), 'notes.json')
        self._file = patch.object(kyphone_os, 'NOTES_FILE', self.path)
        self._file.start()
        self.addCleanup(self._file.stop)
        reset_state(screen='home', home_index=kyphone_os.HOME_MENU.index('NOTES'), notes=[])

    def press(self, *keys):
        wire = None
        for key in keys:
            with patch.object(kyphone_os, 'push_screen') as ps:
                kyphone_os.handle_key(key)
            wire = _wire(ps) or wire
        return wire

    def type(self, text):
        wire = None
        for ch in text:
            wire = self.press('KEY_ENTER' if ch == '\n' else 'CHAR:' + ch)
        return wire

    def saved(self):
        with open(self.path) as f:
            return json.load(f)

    def test_an_empty_list_opens_on_plus(self):
        self.assertEqual(self.press('KEY_ENTER'), 'NOTES|-2')
        self.assertEqual(kyphone_os.state['screen'], 'notes_list')

    def test_write_a_note_and_it_is_saved_and_listed_newest_first(self):
        self.press('KEY_ENTER', 'KEY_ENTER')                       # NOTES, then + (selected on an empty list)
        self.assertEqual(kyphone_os.state['screen'], 'note')
        wire = self.type('Groceries\neggs')
        self.assertEqual(wire, 'NOTE||Groceries' + CELL + 'eggs')
        wire = self.press('KEY_UP', 'KEY_ENTER')                   # < in the header: save and back
        self.assertEqual(kyphone_os.state['screen'], 'notes_list')
        self.assertEqual([n['text'] for n in self.saved()], ['Groceries\neggs'])
        self.assertTrue(wire.startswith('NOTES|0|Groceries' + CELL))
        self.press('CHAR:+')
        self.type('Second')
        wire = self.press('KEY_ESC')
        self.assertEqual([n['text'] for n in self.saved()], ['Second', 'Groceries\neggs'])
        self.assertEqual([r.split(CELL)[0] for r in _rows(wire, 2)], ['Second', 'Groceries'])

    def test_q_and_wasd_are_letters_in_the_editor(self):
        self.press('KEY_ENTER', 'KEY_ENTER')
        self.assertEqual(self.type('qwasd'), 'NOTE||qwasd')
        self.assertEqual(kyphone_os.state['screen'], 'note')

    def test_an_empty_note_is_not_kept(self):
        self.press('KEY_ENTER', 'KEY_ENTER')
        self.type('  ')
        self.press('KEY_UP', 'KEY_ENTER')
        self.assertEqual(kyphone_os.state['notes'], [])
        self.assertFalse(os.path.exists(self.path))

    def test_editing_moves_a_note_to_the_top_and_opening_without_change_does_not(self):
        kyphone_os.state['notes'] = [{'text': 'A', 'ts': '2026-09-20T09:00:00'}, {'text': 'B', 'ts': '2026-09-19T09:00:00'}]
        self.press('KEY_ENTER', 'KEY_DOWN', 'KEY_ENTER')           # open B
        self.assertEqual(kyphone_os.state['note_text'], 'B')
        self.press('KEY_ESC')                                     # unchanged: nothing saved, still second
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(kyphone_os.state['notes_index'], 1)
        self.press('KEY_ENTER')
        self.type('!')
        self.press('KEY_ESC')
        self.assertEqual([n['text'] for n in self.saved()], ['B!', 'A'])
        self.assertEqual(kyphone_os.state['notes_index'], 0)

    def test_backspace_and_a_note_emptied_is_removed(self):
        kyphone_os.state['notes'] = [{'text': 'ab', 'ts': ''}]
        self.press('KEY_ENTER', 'KEY_ENTER')
        self.assertEqual(self.press('KEY_BACKSPACE'), 'NOTE||a')
        self.press('KEY_BACKSPACE', 'KEY_ESC')
        self.assertEqual(self.saved(), [])

    def test_delete_asks_first_and_keep_goes_back_to_the_note(self):
        kyphone_os.state['notes'] = [{'text': 'secret plan', 'ts': ''}]
        self.press('KEY_ENTER', 'KEY_ENTER')
        wire = self.press('KEY_UP', 'KEY_RIGHT')
        self.assertTrue(wire.startswith('NOTE|D|'))
        wire = self.press('KEY_ENTER')
        self.assertEqual(wire, 'CONFIRM|NOTE|DELETE THIS NOTE? IT CANNOT BE BROUGHT BACK.|DELETE|KEEP NOTE|K')
        self.press('KEY_ENTER')                                   # KEEP NOTE
        self.assertEqual((kyphone_os.state['screen'], kyphone_os.state['note_text']), ('note', 'secret plan'))
        self.press('KEY_UP', 'KEY_RIGHT', 'KEY_ENTER', 'KEY_LEFT', 'KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'notes_list')
        self.assertEqual(self.saved(), [])

    def test_the_header_and_typing_after_it(self):
        self.press('KEY_ENTER', 'KEY_ENTER')
        self.type('hi')
        self.assertTrue(self.press('KEY_UP').startswith('NOTE|B|'))
        self.assertEqual(self.press('CHAR:!'), 'NOTE||hi!')        # typing goes back to the text
        self.press('KEY_UP')
        self.assertEqual(self.press('KEY_DOWN'), 'NOTE||hi!')

    def test_a_long_note_shows_its_end_behind_dots_and_every_frame_fits(self):
        self.press('KEY_ENTER', 'KEY_ENTER')
        kyphone_os.state['note_text'] = ' '.join(f'word{i}' for i in range(400)) + '\n\nlast line'
        wire = self.press('CHAR:.')
        lines = wire.split('|', 2)[2].split(CELL)
        self.assertEqual(lines[0], '...')
        self.assertEqual(lines[-1], 'last line.')
        self.assertLessEqual(len(wire), kyphone_os.MAX_COMMAND_CHARS)
        self.assertLessEqual(len(lines), kyphone_os.NOTE_LINES)
        self.assertTrue(all(len(l) <= kyphone_os.NOTE_COLS for l in lines))

    def test_a_full_line_leaves_room_for_the_cursor(self):
        view = kyphone_os.note_view('x' * 30, 250)
        self.assertEqual(view, ['x' * 30, ''])                   # a full line: the cursor starts the next one
        self.assertEqual(kyphone_os.note_view('ab cd', 250), ['ab cd'])
        self.assertEqual(kyphone_os.note_view('', 250), [''])

    def test_the_note_is_capped(self):
        self.press('KEY_ENTER', 'KEY_ENTER')
        kyphone_os.state['note_text'] = 'x' * kyphone_os.NOTE_MAX
        self.assertIsNone(self.press('CHAR:y'))                   # full: the key is not taken
        self.assertEqual(len(kyphone_os.state['note_text']), kyphone_os.NOTE_MAX)

    def test_the_list_windows_and_the_header(self):
        kyphone_os.state['notes'] = [{'text': f'N{i}', 'ts': ''} for i in range(8)]
        self.press('KEY_ENTER')
        wire = self.press(*['KEY_DOWN'] * 7)
        self.assertEqual(wire.split('|')[1], '4')                 # the last row of the window
        self.assertEqual(len(_rows(wire, 2)), kyphone_os.NOTES_ROWS)
        self.press(*['KEY_UP'] * 8)
        self.assertEqual(self.press('KEY_RIGHT'), 'NOTES|-2|' + '|'.join(f'N{i}' + CELL + CELL for i in range(5)))
        self.press('KEY_ENTER')
        self.assertEqual((kyphone_os.state['screen'], kyphone_os.state['note_idx']), ('note', None))

    def test_a_long_first_line_is_shortened_on_the_list(self):
        kyphone_os.state['notes'] = [{'text': 'Ideas for the case: walnut, brass buttons', 'ts': ''}]
        wire = self.press('KEY_ENTER')
        self.assertEqual(_rows(wire, 2)[0].split(CELL)[0], 'Ideas for the case:...')
        self.assertLessEqual(len(_rows(wire, 2)[0].split(CELL)[0]), kyphone_os.NOTE_TITLE_MAX)

    def test_esc_on_the_list_goes_home_on_notes(self):
        self.press('KEY_ENTER', 'KEY_ESC')
        self.assertEqual((kyphone_os.state['screen'], kyphone_os.state['home_index']),
                         ('home', kyphone_os.HOME_MENU.index('NOTES')))

    def test_saved_notes_load_and_bad_ones_are_dropped(self):
        with open(self.path, 'w') as f:
            json.dump([{'text': 'good', 'ts': 'x'}, {'text': '   '}, {'nope': 1}, 'x', {'text': 5}], f)
        kyphone_os.load_notes()
        self.assertEqual(kyphone_os.state['notes'], [{'text': 'good', 'ts': 'x'}])
        with open(self.path, 'w') as f:
            f.write('not json')
        kyphone_os.load_notes()
        self.assertEqual(kyphone_os.state['notes'], [])

    def test_undrawable_text_is_cleaned_on_the_wire_and_newlines_stay_lines(self):
        kyphone_os.state['notes'] = [{'text': 'caf\u00e9 \u201cquoted\u201d\nline two', 'ts': ''}]
        self.press('KEY_ENTER')
        wire = self.press('KEY_ENTER')
        self.assertEqual(wire, 'NOTE||caf? "quoted"' + CELL + 'line two')


class TestCallsAndTextsPerPerson(unittest.TestCase):
    """A call is logged with the other party's number, the call log opens that person's page, and a conversation
    shows the last call with them."""
    NUM = '(555) 010-0001'

    def setUp(self):
        self._save = patch.object(kyphone_os, '_save_calls')
        self._save.start()
        self.addCleanup(self._save.stop)
        self._msgs = patch.object(kyphone_os, 'save_messages')
        self._msgs.start()
        self.addCleanup(self._msgs.stop)

    def press(self, *keys):
        wire = None
        for key in keys:
            with patch.object(kyphone_os, 'push_screen') as ps:
                kyphone_os.handle_key(key)
            wire = _wire(ps) or wire
        return wire

    def call_from_contact_page(self):
        reset_state(screen='contact', contact_idx=None, contact_number=ALICE, contact_sel='call',
                    contact_return='calls_list', calls=[])
        self.press('KEY_ENTER', 'KEY_ESC')                         # call, then cancel before it connects

    def test_a_call_from_a_contact_page_is_logged_with_the_number(self):
        self.call_from_contact_page()
        entry = kyphone_os.state['calls'][0]
        self.assertEqual((entry['tag'], entry['number']), ('OUT', kyphone_os.format_number(ALICE)))

    def test_a_dialled_number_and_the_incoming_demo_are_logged_with_numbers(self):
        reset_state(screen='dial', dial_buffer='5550100077', dial_quick_index=-1, calls=[])
        self.press('KEY_ENTER', 'KEY_ESC')
        self.assertEqual(kyphone_os.state['calls'][0]['number'], '(555) 010-0077')
        reset_state(screen='dial', dial_buffer='12', dial_quick_index=-1, calls=[])
        self.press('KEY_ENTER', 'KEY_ESC')
        self.assertNotIn('number', kyphone_os.state['calls'][0])   # not a dialable number: no link

    def test_enter_on_a_logged_call_opens_the_persons_page_with_call_selected(self):
        reset_state(screen='calls_list', calls_index=1, calls_start=0,
                    calls=[{'name': 'Stranger', 'tag': 'MISS', 'ts': '', 'duration': '', 'number': '(555) 010-0099'}])
        wire = self.press('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'contact')
        self.assertTrue(wire.startswith('CONTACT|(555) 010-0099|'))
        self.assertTrue(wire.endswith('|U|C'))                     # not saved; CALL selected
        self.press('KEY_ENTER')                                    # Enter again calls back
        self.assertEqual(kyphone_os.state['screen'], 'outgoing')
        self.assertEqual(kyphone_os.state['call_number'], '(555) 010-0099')
        self.press('KEY_ESC')                                      # cancel: back to the call log
        self.assertEqual(kyphone_os.state['screen'], 'calls_list')

    def test_an_older_log_row_without_a_number_still_redials(self):
        reset_state(screen='calls_list', calls_index=1, calls_start=0,
                    calls=[{'name': 'Ann', 'tag': 'OUT', 'ts': '', 'duration': ''}])
        self.press('KEY_ENTER')
        self.assertEqual((kyphone_os.state['screen'], kyphone_os.state['call_name']), ('outgoing', 'Ann'))

    def thread_wire(self, calls):
        reset_state(screen='thread', thread_id=ALICE, thread_draft='', thread_header_sel=None, thread_msg_sel=None,
                    messages=[_inbound()], calls=calls)
        with patch.object(kyphone_os, 'push_screen') as ps:
            kyphone_os.push_thread2()
        return _wire(ps)

    def test_a_conversation_shows_the_last_call_with_that_number(self):
        yesterday = (datetime.now() - timedelta(days=1)).isoformat()
        calls = [{'name': 'Bo', 'tag': 'IN', 'ts': '', 'duration': '1:00', 'number': '(555) 010-0002'},
                 {'name': 'Alice', 'tag': 'MISS', 'ts': yesterday, 'duration': '', 'number': kyphone_os.format_number(ALICE)},
                 {'name': 'Alice', 'tag': 'OUT', 'ts': '', 'duration': '2:05', 'number': kyphone_os.format_number(ALICE)}]
        wire = self.thread_wire(calls)
        self.assertEqual(_entries(wire)[0], ['C', '', 'LAST CALL: MISSED, YESTERDAY'])
        self.assertEqual(_entries(wire)[1][0], 'R')

    def test_a_connected_call_shows_its_length_and_no_call_means_no_line(self):
        calls = [{'name': 'Alice', 'tag': 'OUT', 'ts': '', 'duration': '2:05', 'number': kyphone_os.format_number(ALICE)}]
        self.assertEqual(_entries(self.thread_wire(calls))[0][2], 'LAST CALL: OUT 2:05')
        self.assertEqual(_entries(self.thread_wire([]))[0][0], 'R')
        calls = [{'name': 'Alice', 'tag': 'OUT', 'ts': '', 'duration': '2:05'}]   # no number: never matched by name
        self.assertEqual(_entries(self.thread_wire(calls))[0][0], 'R')

    def test_the_call_line_is_kept_when_the_frame_is_tight(self):
        calls = [{'name': 'Alice', 'tag': 'MISS', 'ts': '', 'duration': '', 'number': kyphone_os.format_number(ALICE)}]
        reset_state(screen='thread', thread_id=ALICE, thread_draft='', thread_header_sel=None, thread_msg_sel=None,
                    messages=[_inbound(body='x ' * 70) for _ in range(3)], calls=calls)
        with patch.object(kyphone_os, 'push_screen') as ps:
            kyphone_os.push_thread2()
        wire = _wire(ps)
        self.assertLessEqual(len(wire), kyphone_os.MAX_COMMAND_CHARS)
        self.assertEqual(_entries(wire)[0][0], 'C')
        self.assertEqual(_entries(wire)[-1][0], 'R')

    def test_the_number_survives_a_restart_and_a_bad_one_is_dropped(self):
        path = os.path.join(tempfile.mkdtemp(), 'calls.json')
        with open(path, 'w') as f:
            json.dump([{'name': 'A', 'tag': 'OUT', 'ts': '', 'duration': '', 'number': '(555) 010-0001'},
                       {'name': 'B', 'tag': 'OUT', 'ts': '', 'duration': '', 'number': 'nope'}], f)
        with patch.object(kyphone_os, 'CALLS_FILE', path):
            kyphone_os.load_calls()
        self.assertEqual([c.get('number') for c in kyphone_os.state['calls']], ['(555) 010-0001', None])


class FakeUploadServer:
    instances = []

    def __init__(self, books, music, on_event, on_contacts, on_stopped, fail=False, address=None):
        self.address = address
        self.on_event, self.on_contacts, self.on_stopped = on_event, on_contacts, on_stopped
        self.code, self.port, self.running, self.fail = '4821', 8080, False, fail
        FakeUploadServer.instances.append(self)

    def start(self):
        if self.fail:
            raise OSError(98, 'Address already in use')
        self.running = True
        return self

    def stop(self):
        self.running = False


class TestAddFromAComputer(unittest.TestCase):
    def setUp(self):
        FakeUploadServer.instances = []
        self.addCleanup(setattr, kyphone_os, '_upload', None)

    def press(self, *keys):
        wire = None
        for key in keys:
            with patch.object(kyphone_os, 'push_screen') as ps:
                kyphone_os.handle_key(key)
            wire = _wire(ps) or wire
        return wire

    def open(self, ip='192.168.1.23', fail=False):
        reset_state(screen='settings', settings_index=4)
        with patch.object(kyphone_os.upload_server, 'lan_address', return_value=ip), \
                patch.object(kyphone_os.upload_server, 'UploadServer',
                             side_effect=lambda *a, **k: FakeUploadServer(*a, fail=fail, **k)):
            return self.press('KEY_ENTER')

    def test_the_settings_row(self):
        reset_state(screen='home', home_index=kyphone_os.HOME_MENU.index('SETTINGS'))
        with patch.object(nc, 'wifi_enabled', return_value=True), \
                patch.object(nc, 'wifi_status', return_value=nc.WifiStatus(False)), \
                patch.object(nc, 'bt_status', return_value=nc.BtStatus(True, [])):
            rows = _rows(self.press('KEY_ENTER'), 2)
        self.assertEqual(rows[4], 'Add from a computer' + CELL + 'Books, music, contacts' + CELL)

    def test_opening_starts_the_page_and_shows_the_address_and_code(self):
        wire = self.open()
        self.assertEqual(wire, 'UPLOAD|192.168.1.23:8080|4821|')
        self.assertTrue(FakeUploadServer.instances[0].running)

    def test_what_arrives_is_listed_newest_last_three_at_most(self):
        self.open()
        server = FakeUploadServer.instances[0]
        for name in ('a.epub', 'b.epub', 'c.mp3', 'd.mp3'):
            with patch.object(kyphone_os, 'push_screen') as ps:
                server.on_event(name)
        self.assertEqual(_wire(ps), 'UPLOAD|192.168.1.23:8080|4821|b.epub' + CELL + 'c.mp3' + CELL + 'd.mp3')

    def test_leaving_stops_the_page(self):
        self.open()
        with patch.object(nc, 'wifi_enabled', return_value=True), \
                patch.object(nc, 'wifi_status', return_value=nc.WifiStatus(False)), \
                patch.object(nc, 'bt_status', return_value=nc.BtStatus(True, [])):
            self.press('CHAR:q')
        self.assertEqual(kyphone_os.state['screen'], 'settings')
        self.assertFalse(FakeUploadServer.instances[0].running)
        self.assertIsNone(kyphone_os._upload)

    def test_no_network_says_so_and_starts_nothing(self):
        wire = self.open(ip=None)
        self.assertTrue(wire.startswith('STUB|SETTINGS|THE PHONE IS NOT ON A NETWORK.'))
        self.assertEqual(FakeUploadServer.instances, [])

    def test_a_server_that_cannot_start_says_why(self):
        wire = self.open(fail=True)
        self.assertTrue(wire.startswith('STUB|SETTINGS|THE UPLOAD PAGE COULD NOT START (ADDRESS ALREADY IN USE)'))
        self.assertEqual(kyphone_os.state['stub_return'], 'settings')

    def test_the_server_only_answers_to_the_phones_own_address(self):
        self.open()
        self.assertEqual(FakeUploadServer.instances[0].address, '192.168.1.23')

    def test_an_idle_stop_says_so(self):
        self.open()
        with patch.object(kyphone_os, 'push_screen') as ps:
            FakeUploadServer.instances[0].on_stopped('idle')
        self.assertTrue(_wire(ps).startswith('STUB|SETTINGS|THE UPLOAD PAGE STOPPED AFTER 10 MINUTES WITHOUT USE'))

    def test_too_many_wrong_codes_raise_an_alert(self):
        self.open()
        with patch.object(kyphone_os, 'push_screen') as ps:
            FakeUploadServer.instances[0].on_stopped('too many wrong codes')
        self.assertTrue(_wire(ps).startswith('STUB|SETTINGS|THE UPLOAD PAGE STOPPED'))

    def test_contacts_merge_under_the_forms_rules(self):
        before = list(kyphone_os.CONTACTS)
        self.addCleanup(lambda: kyphone_os.CONTACTS.__setitem__(slice(None), before))
        kyphone_os.CONTACTS[:] = [{'first': 'Zed', 'last': '', 'number': '(555) 010-0001'}]
        with patch.object(kyphone_os, '_save_contacts') as save:
            summary = kyphone_os._import_contacts([
                ('Ann', 'Lee', '+1 555 010 0002'),        # added
                ('Zed', 'Again', '5550100001'),           # already here (same number)
                ('NoNumber', '', ''),                     # not usable
                ('Bad', '', '12'),                        # not dialable
                ('Bj\u00f6rk', 'A very very long surname here', '555-010-0003')])
        self.assertEqual(summary, 'Contacts: 2 added, 1 already here, 2 with no usable number')
        save.assert_called_once()
        self.assertEqual([kyphone_os.dispname(c) for c in kyphone_os.CONTACTS],
                         ['Ann Lee', 'Bj?rk A very very long s', 'Zed'])
        self.assertEqual(kyphone_os.CONTACTS[0]['number'], '(555) 010-0002')

    def test_nothing_new_saves_nothing(self):
        with patch.object(kyphone_os, '_save_contacts') as save:
            self.assertEqual(kyphone_os._import_contacts([]), 'Contacts: 0 added')
        save.assert_not_called()


class TestNothingPrivateIsLogged(unittest.TestCase):
    """The service's output goes to the Radxa's journal on disk: no typed letter (a Wi-Fi password, a note, a text)
    and no screen content may appear in it."""

    def test_typed_letters_and_screen_content_stay_out_of_the_log(self):
        import io
        from contextlib import redirect_stdout
        reset_state(screen='netpass', net_ssid='Maple', net_pass='', net_pass_hdr=False, net_kind='W')
        out = io.StringIO()
        with redirect_stdout(out), patch.object(kyphone_os, 'SIM_MODE', False), \
                patch.object(kyphone_os, '_pending_lock', threading.Lock()):
            for ch in 'hunter2Z':
                kyphone_os.handle_key('CHAR:' + ch)
            kyphone_os.push_screen('THREAD2|Pip|||R\xb79:00\xb7secret meeting at noon')
        kyphone_os._pending_command = None                    # (nothing is really sent in tests)
        log = out.getvalue()
        self.assertNotIn('hunter', log)
        self.assertNotIn('CHAR:', log)
        self.assertNotIn('secret', log)
        self.assertIn('THREAD2', log)                          # the screen's name is still there for debugging


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
        self.assertTrue(self.wire().endswith('|I|0'))                       # style, then the "music is playing" flag
        self.assertTrue(self.wire('B').endswith('|B|0'))
        self.assertTrue(self.wire('W').endswith('|W|0'))

    def test_the_setting_names_map_to_the_wire_letters(self):
        self.assertEqual(kyphone_os.HOME_STYLES, {'icons': 'I', 'both': 'B', 'words': 'W'})


class TestDataDirOverride(unittest.TestCase):
    """KYPHONE_DATA_DIR moves the data folder. Each case imports kyphone_os in a fresh
    interpreter, because the paths are fixed when the module is imported. The snippet
    only prints the paths; the tests save nothing except into their own scratch folder, and never
    import the real module without the override (that would load, and could migrate, the real data)."""

    SNIPPET = (
        "import sys, json\n"
        "from unittest.mock import MagicMock\n"
        "sys.argv = ['x', '--sim']\n"
        "for m in ('spidev', 'gpiod', 'input_handler', 'pygame', 'simulator', 'evdev'):\n"
        "    sys.modules[m] = MagicMock()\n"
        "sys.modules['twilio'] = None   # Twilio is gone: importing it at all would fail here\n"
        "sys.path.insert(0, %r)\n"
        "import kyphone_os as k\n"
        "%s"
        "print(json.dumps([k.DATA_DIR, k._contacts_path, k.MESSAGES_FILE]))\n"
    )

    def run_import(self, env_value, save=False, module_dir=None):
        import json
        import subprocess
        import tempfile
        env = {k: v for k, v in os.environ.items() if k != 'KYPHONE_DATA_DIR'}
        if env_value is not None:
            env['KYPHONE_DATA_DIR'] = env_value
        here = module_dir or os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
        save_line = "k._save_contacts([{'first': 'Ada', 'last': '', 'number': '(555) 010-0001'}])\n" if save else ''
        out = subprocess.run([sys.executable, '-c', self.SNIPPET % (here, save_line)],
                             env=env, capture_output=True, text=True, timeout=60, cwd=tempfile.gettempdir())
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout.strip().splitlines()[-1])

    def test_the_override_moves_contacts_and_messages(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            scratch = os.path.join(tmp, 'scratch')
            data_dir, contacts, messages = self.run_import(scratch, save=True)
            self.assertEqual(data_dir, scratch)
            self.assertEqual(contacts, os.path.join(scratch, 'contacts.json'))
            self.assertEqual(messages, os.path.join(scratch, 'messages.json'))
            # the save created the folder and wrote there
            self.assertTrue(os.path.exists(contacts))

    def test_a_relative_or_home_path_is_made_absolute(self):
        data_dir, _, _ = self.run_import('~/kyphone-scratch-not-created')
        self.assertEqual(data_dir, os.path.join(os.path.expanduser('~'), 'kyphone-scratch-not-created'))
        self.assertTrue(os.path.isabs(self.run_import('some/relative/dir')[0]))

    def test_without_it_the_data_folder_sits_beside_spi_bridge(self):
        # Importing kyphone_os loads (and may migrate) whatever data folder it resolves, so this
        # must never import the real module without the override: it imports a COPY of it placed
        # in a scratch tree, where "beside spi_bridge" is the scratch tree's own data/.
        import shutil
        import tempfile
        import glob
        here = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
        with tempfile.TemporaryDirectory() as tmp:
            tmp = os.path.realpath(tmp)
            os.makedirs(os.path.join(tmp, 'spi_bridge'))
            for src in ([os.path.join(here, 'kyphone_os.py'), os.path.join(here, 'version.py'),
                         os.path.join(here, 'network_control.py'), os.path.join(here, 'modem.py'),
                         os.path.join(here, 'upload_server.py')]
                        + glob.glob(os.path.join(here, 'reader_*.py')) + glob.glob(os.path.join(here, 'music_*.py'))):
                shutil.copy(src, os.path.join(tmp, 'spi_bridge', os.path.basename(src)))      # the module and what it imports
            for unset in (None, ''):                  # an empty value means "not set"
                data_dir, contacts, messages = self.run_import(unset, module_dir=os.path.join(tmp, 'spi_bridge'))
                self.assertEqual(data_dir, os.path.join(tmp, 'data'))
                self.assertEqual(contacts, os.path.join(tmp, 'data', 'contacts.json'))
                self.assertEqual(messages, os.path.join(tmp, 'data', 'messages.json'))


class TestNewMessageNumberCheck(unittest.TestCase):
    """New Message refuses a number that can never be texted (or saved as a contact), and keeps what you typed."""

    def setUp(self):
        self._save = patch.object(kyphone_os, 'save_messages')
        self._save.start()
        self._ps = patch.object(kyphone_os, 'push_screen')
        self.ps = self._ps.start()
        self._send = patch.object(kyphone_os, 'send_reply')
        self.send = self._send.start()

    def tearDown(self):
        self._send.stop()
        self._ps.stop()
        self._save.stop()

    def press_send(self, to, msg='hello', **state):
        reset_state(screen='compose', compose_to=to, compose_msg=msg, compose_to_active=False, **state)
        kyphone_os.handle_key('KEY_ENTER')

    def test_numbers_that_cannot_be_texted_raise_an_alert_and_send_nothing(self):
        for bad in ('123123', '555', '12345678', '123456789012', '25551234567', 'abc', '5551234567890'):
            self.send.reset_mock()
            self.press_send(bad)
            self.assertEqual((kyphone_os.state['screen'], kyphone_os.state['stub_key']), ('stub', 'BAD_RECIPIENT'), bad)
            self.send.assert_not_called()

    def test_every_form_of_a_ten_or_eleven_digit_number_goes_through(self):
        for good in ('5551234567', '(555) 123-4567', '555-123-4567', '555.123.4567', '+15551234567', '15551234567', '+1 (555) 123-4567'):
            self.send.reset_mock()
            self.press_send(good)
            self.send.assert_called_once_with(good, 'hello')
            self.assertEqual(kyphone_os.state['screen'], 'thread', good)

    def test_the_alert_returns_to_the_number_field_with_everything_kept(self):
        self.press_send('123123', msg='a long message I typed')
        kyphone_os.handle_key('KEY_ENTER')                                  # dismiss the alert
        self.assertEqual(kyphone_os.state['screen'], 'compose')
        self.assertTrue(kyphone_os.state['compose_to_active'])              # the cursor is on TO, ready to fix it
        self.assertEqual((kyphone_os.state['compose_to'], kyphone_os.state['compose_msg']), ('123123', 'a long message I typed'))

    def test_the_send_button_is_checked_too(self):
        self.press_send('123123', compose_send_sel=True)
        self.assertEqual(kyphone_os.state['stub_key'], 'BAD_RECIPIENT')
        self.send.assert_not_called()

    def test_the_number_is_checked_before_an_empty_message(self):
        self.press_send('123', msg='')
        self.assertEqual(kyphone_os.state['stub_key'], 'BAD_RECIPIENT')

    def test_an_empty_number_still_asks_for_one_and_an_empty_message_still_asks_for_text(self):
        self.press_send('', msg='hi')
        self.assertEqual(kyphone_os.state['stub_key'], 'NO_RECIPIENT')
        self.press_send('5551234567', msg='   ')
        self.assertEqual(kyphone_os.state['stub_key'], 'EMPTY_SEND')

    def test_the_alert_text_fits_a_frame_and_is_drawable(self):
        title, body = kyphone_os.ALERTS['BAD_RECIPIENT']
        self.assertLessEqual(len('STUB|%s|%s' % (title, body)), kyphone_os.MAX_COMMAND_CHARS)
        self.assertEqual(body, kyphone_os.sanitize(body))


class TestContactNumberLockedToTheConversation(unittest.TestCase):
    """Saving a contact from a conversation's info page keeps the conversation's number, so the name attaches."""

    def setUp(self):
        self._contacts = list(kyphone_os.CONTACTS)
        self.addCleanup(lambda: kyphone_os.CONTACTS.__setitem__(slice(None), self._contacts))
        kyphone_os.CONTACTS[:] = []
        for name in ('save_messages', '_save_contacts'):
            p = patch.object(kyphone_os, name)
            p.start()
            self.addCleanup(p.stop)
        self._ps = patch.object(kyphone_os, 'push_screen')
        self.ps = self._ps.start()
        self.addCleanup(self._ps.stop)

    def press(self, *keys):
        for k in keys:
            kyphone_os.handle_key(k)

    def typ(self, text):
        self.press(*['CHAR:' + c for c in text])

    def text_a_number_then_open_the_save_form(self, number='5551234567'):
        reset_state(screen='texts_list', texts_index=-1, texts_header_sel='plus', messages=[])
        self.press('KEY_ENTER')                                             # + : new message
        self.typ(number)
        self.press('KEY_ENTER')
        self.typ('hello')
        self.press('KEY_ENTER')                                             # send
        self.press('KEY_UP', 'KEY_UP', 'KEY_RIGHT', 'KEY_ENTER')            # the NOT SENT bubble, the header, i, open it
        self.press('KEY_RIGHT', 'KEY_RIGHT', 'KEY_ENTER')                   # CALL, TEXT, SAVE
        self.assertEqual(kyphone_os.state['screen'], 'contact_edit')

    def test_the_form_opens_with_the_number_locked(self):
        self.text_a_number_then_open_the_save_form()
        self.assertTrue(kyphone_os.state['edit_number_locked'])
        self.assertEqual(kyphone_os.state['edit_number'], '(555) 123-4567')

    def test_the_arrows_step_over_the_number_field(self):
        self.text_a_number_then_open_the_save_form()
        self.press('KEY_DOWN')
        self.assertEqual(kyphone_os.state['edit_index'], 1)                  # first -> last
        self.press('KEY_DOWN')
        self.assertEqual(kyphone_os.state['edit_index'], kyphone_os.EDIT_SAVE)   # last -> SAVE, skipping the number
        self.press('KEY_UP')
        self.assertEqual(kyphone_os.state['edit_index'], 1)
        self.press('KEY_ENTER')                                             # Enter on the last name moves on to SAVE
        self.assertEqual(kyphone_os.state['edit_index'], kyphone_os.EDIT_SAVE)

    def test_the_number_cannot_be_changed_even_if_a_key_reaches_it(self):
        self.text_a_number_then_open_the_save_form()
        kyphone_os.state['edit_index'] = 2
        self.typ('999')
        self.press('KEY_BACKSPACE')
        self.assertEqual(kyphone_os.state['edit_number'], '(555) 123-4567')

    def test_saving_the_name_makes_the_conversation_show_it(self):
        self.text_a_number_then_open_the_save_form()
        self.typ('Sam')
        self.press('KEY_DOWN')
        self.typ('Lee')
        self.press('KEY_DOWN', 'KEY_ENTER')                                 # SAVE
        self.assertEqual(kyphone_os.state['screen'], 'contact')
        self.assertEqual([(c['first'], c['last'], c['number']) for c in kyphone_os.CONTACTS], [('Sam', 'Lee', '(555) 123-4567')])
        self.press('KEY_ESC', 'KEY_ESC')                                    # back to the text list
        self.assertEqual(kyphone_os.state['screen'], 'texts_list')
        kyphone_os.push_texts()
        self.assertEqual(self.ps.call_args[0][0].split('|')[2].split(CELL)[0], 'Sam Lee')

    def test_it_works_for_every_way_of_writing_the_number(self):
        for typed in ('555-123-4567', '(555) 123-4567', '+15551234567', '15551234567'):
            kyphone_os.CONTACTS[:] = []
            self.text_a_number_then_open_the_save_form(typed)
            self.typ('Sam')
            self.press('KEY_DOWN', 'KEY_DOWN', 'KEY_ENTER')
            self.assertEqual(kyphone_os.state['screen'], 'contact', typed)
            self.press('KEY_ESC', 'KEY_ESC')
            kyphone_os.push_texts()
            self.assertEqual(self.ps.call_args[0][0].split('|')[2].split(CELL)[0], 'Sam', typed)

    def test_a_number_that_cannot_be_dialed_stays_editable_so_it_can_be_fixed(self):
        # an old conversation with a short number (made before New Message checked): nothing to lock to
        reset_state(screen='contact', messages=[{'sender': '123123', 'name': '123123', 'body': 'hi', 'read': True}],
                    contact_idx=None, contact_number='123123', contact_sel='save', contact_return='thread')
        kyphone_os.handle_key('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'contact_edit')
        self.assertFalse(kyphone_os.state['edit_number_locked'])
        self.press('KEY_DOWN', 'KEY_DOWN')
        self.assertEqual(kyphone_os.state['edit_index'], 2)                  # the number field can be reached

    def test_other_forms_are_not_locked(self):
        reset_state(screen='contacts_pick', contacts_index=-1, contacts_header_sel='plus', contacts_return='home')
        kyphone_os.handle_key('KEY_ENTER')                                  # the + on the contacts list: a blank form
        self.assertFalse(kyphone_os.state['edit_number_locked'])
        kyphone_os.CONTACTS[:] = [{'first': 'Ann', 'last': '', 'number': '(555) 010-0001'}, {'first': 'Bo', 'last': '', 'number': ''}]
        for idx in (0, 1):                                                  # EDIT on a saved contact; ADD NUMBER on one without
            reset_state(screen='contact', contact_idx=idx, contact_number='', contact_sel='edit', contact_return='contacts_pick')
            kyphone_os.handle_key('KEY_ENTER')
            self.assertEqual(kyphone_os.state['screen'], 'contact_edit')
            self.assertFalse(kyphone_os.state['edit_number_locked'], idx)
            self.press('KEY_DOWN', 'KEY_DOWN')
            self.assertEqual(kyphone_os.state['edit_index'], 2, idx)

    def test_the_lock_is_forgotten_when_another_form_opens(self):
        self.text_a_number_then_open_the_save_form()
        self.assertTrue(kyphone_os.state['edit_number_locked'])
        kyphone_os.CONTACTS[:] = [{'first': 'Ann', 'last': '', 'number': '(555) 010-0001'}]
        reset_state(screen='contact', contact_idx=0, contact_number='', contact_sel='edit', contact_return='contacts_pick')
        kyphone_os.handle_key('KEY_ENTER')
        self.assertFalse(kyphone_os.state['edit_number_locked'])


class TestContactsPlusKey(unittest.TestCase):
    """The + key opens a new contact when nothing is typed in the search; an empty list opens with + selected."""

    def setUp(self):
        self._saved = list(kyphone_os.CONTACTS)
        self.addCleanup(lambda: kyphone_os.CONTACTS.__setitem__(slice(None), self._saved))
        kyphone_os.CONTACTS[:] = []
        self._save = patch.object(kyphone_os, '_save_contacts')
        self._save.start()
        self.addCleanup(self._save.stop)
        self._ps = patch.object(kyphone_os, 'push_screen')
        self.ps = self._ps.start()
        self.addCleanup(self._ps.stop)

    def open_list(self, **kw):
        reset_state(screen='home', home_index=kyphone_os.HOME_MENU.index('CONTACTS'), **kw)
        kyphone_os.handle_key('KEY_ENTER')

    def press(self, *keys):
        for k in keys:
            kyphone_os.handle_key(k)

    def test_the_plus_key_with_an_empty_search_opens_a_blank_form(self):
        kyphone_os.CONTACTS[:] = _contacts(3)
        self.open_list()
        self.press('CHAR:+')
        self.assertEqual(kyphone_os.state['screen'], 'contact_edit')
        self.assertEqual((kyphone_os.state['edit_first'], kyphone_os.state['edit_number'], kyphone_os.state['edit_number_locked']), ('', '', False))
        self.assertEqual(kyphone_os.state['edit_return'], 'contacts_pick')

    def test_from_the_compose_picker_the_form_returns_to_compose(self):
        kyphone_os.CONTACTS[:] = _contacts(3)
        reset_state(screen='contacts_pick', contacts_return='compose', contacts_query='', contacts_index=0)
        self.press('CHAR:+')
        self.assertEqual((kyphone_os.state['screen'], kyphone_os.state['edit_return']), ('contact_edit', 'compose'))

    def test_a_plus_typed_after_other_letters_is_part_of_the_search(self):
        kyphone_os.CONTACTS[:] = _contacts(3)
        self.open_list()
        self.press('CHAR:N', 'CHAR:+')
        self.assertEqual((kyphone_os.state['screen'], kyphone_os.state['contacts_query']), ('contacts_pick', 'N+'))

    def test_an_empty_list_opens_with_plus_selected_and_the_arrows_work_at_once(self):
        self.open_list()
        self.assertEqual(_wire(self.ps), 'CONTACTSPICK|-2||')
        self.press('KEY_LEFT')
        self.assertEqual(_wire(self.ps), 'CONTACTSPICK|-1||')
        self.press('KEY_RIGHT')
        self.assertEqual(_wire(self.ps), 'CONTACTSPICK|-2||')
        self.press('KEY_DOWN', 'KEY_UP')
        self.assertEqual(_wire(self.ps), 'CONTACTSPICK|-2||')                 # nowhere to go
        self.press('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'contact_edit')

    def test_typing_in_an_empty_list_searches_and_clearing_it_brings_plus_back(self):
        self.open_list()
        self.press('CHAR:a')
        self.assertEqual(_wire(self.ps), 'CONTACTSPICK|0|a|')
        self.press('KEY_BACKSPACE')
        self.assertEqual(_wire(self.ps), 'CONTACTSPICK|-2||')

    def test_a_list_with_contacts_still_opens_on_the_first_row(self):
        kyphone_os.CONTACTS[:] = _contacts(3)
        self.open_list()
        self.assertTrue(_wire(self.ps).startswith('CONTACTSPICK|0||'))

    def test_the_whole_add_flow_from_the_plus_key_saves_the_contact(self):
        self.open_list()
        self.press('CHAR:+')
        self.press(*['CHAR:' + c for c in 'Sam'])
        self.press('KEY_DOWN', 'KEY_DOWN')
        self.press(*['CHAR:' + c for c in '5550100123'])
        self.press('KEY_DOWN', 'KEY_ENTER')
        self.assertEqual([(c['first'], c['number']) for c in kyphone_os.CONTACTS], [('Sam', '(555) 010-0123')])
        self.assertEqual(kyphone_os.state['screen'], 'contact')


class TestCallLog(unittest.TestCase):
    """Calls are recorded when they end, newest first, and kept across restarts."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.file = os.path.join(self._tmp.name, 'calls.json')
        self.clock = [5000.0]
        self._saved = list(kyphone_os.CONTACTS)
        self.addCleanup(lambda: kyphone_os.CONTACTS.__setitem__(slice(None), self._saved))
        kyphone_os.CONTACTS[:] = []
        for name, value in (('CALLS_FILE', self.file), ('DATA_DIR', self._tmp.name)):
            p = patch.object(kyphone_os, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(kyphone_os.time, 'time', lambda: self.clock[0])
        p.start()
        self.addCleanup(p.stop)
        self._ps = patch.object(kyphone_os, 'push_screen')
        self.ps = self._ps.start()
        self.addCleanup(self._ps.stop)
        reset_state(screen='calls_list', calls=[], calls_index=0, calls_start=0, call_name='', call_started_at=None)

    def press(self, *keys):
        for k in keys:
            kyphone_os.handle_key(k)

    def dial(self, digits):
        reset_state(screen='dial', dial_buffer='', dial_quick_index=-1, calls=kyphone_os.state['calls'])
        self.press(*['CHAR:' + c for c in digits])
        self.press('KEY_ENTER')                                       # ring
        self.assertEqual(kyphone_os.state['screen'], 'outgoing')

    def log(self):
        return kyphone_os.state['calls']

    def test_an_answered_outgoing_call_is_logged_with_its_length(self):
        self.dial('5551234567')
        self.press('KEY_ENTER')                                       # answered
        self.clock[0] += 75
        self.press('KEY_ESC')                                         # hung up
        self.assertEqual(kyphone_os.state['screen'], 'calls_list')
        e = self.log()[0]
        self.assertEqual((e['name'], e['tag'], e['duration']), ('(555) 123-4567', 'OUT', '1:15'))

    def test_a_call_cancelled_before_it_connects_is_logged_with_no_length(self):
        self.dial('5551234567')
        self.press('KEY_ESC')
        e = self.log()[0]
        self.assertEqual((e['tag'], e['duration']), ('OUT', ''))

    def test_an_answered_incoming_call_is_IN_and_an_unanswered_one_is_MISS(self):
        kyphone_os.CONTACTS[:] = [{'first': 'Ann', 'last': 'Lee', 'number': '(555) 010-0001'}]
        reset_state(screen='home', calls=[])
        self.press('CHAR:i', 'KEY_ENTER')
        self.clock[0] += 200
        self.press('KEY_ESC')
        reset_state(screen='home', calls=self.log())
        self.press('CHAR:i', 'KEY_ESC')
        self.assertEqual(kyphone_os.state['screen'], 'home')
        newest, older = self.log()[0], self.log()[1]
        self.assertEqual((newest['name'], newest['tag'], newest['duration']), ('Ann Lee', 'MISS', ''))
        self.assertEqual((older['tag'], older['duration']), ('IN', '3:20'))

    def test_q_hangs_up_and_declines_like_esc_since_the_phone_keyboard_has_no_esc_key(self):
        self.dial('5551234567')
        self.press('KEY_ENTER', 'CHAR:q')                              # answered, then Q hangs up
        self.assertEqual(kyphone_os.state['screen'], 'calls_list')
        self.assertEqual(self.log()[0]['tag'], 'OUT')
        self.dial('5550100002')
        self.press('CHAR:q')                                           # Q while it is still ringing cancels
        self.assertEqual(kyphone_os.state['screen'], 'calls_list')
        reset_state(screen='home', calls=self.log())
        self.press('CHAR:i', 'CHAR:q')                                 # Q declines an incoming call
        self.assertEqual(kyphone_os.state['screen'], 'home')
        self.assertEqual(self.log()[0]['tag'], 'MISS')

    def test_the_log_is_newest_first_and_shows_in_the_call_list_under_dial_a_number(self):
        for number in ('5550100001', '5550100002'):
            self.dial(number)
            self.press('KEY_ENTER')
            self.clock[0] += 5
            self.press('KEY_ESC')
        self.assertEqual([e['name'] for e in self.log()], ['(555) 010-0002', '(555) 010-0001'])
        kyphone_os.push_calls()
        rows = _wire(self.ps).split('|')[2:]
        self.assertEqual(rows[0].split(CELL)[0], 'DIAL A NUMBER')
        first = rows[1].split(CELL)
        self.assertEqual((first[0], first[1], first[3]), ('(555) 010-0002', 'OUT', '0:05'))
        self.assertRegex(first[2], r'^\d{1,2}:\d{2} [AP]M$')                # today: a clock time

    def test_a_dialed_number_that_is_a_contact_is_logged_by_name_and_a_quick_dial_too(self):
        kyphone_os.CONTACTS[:] = [{'first': 'Ann', 'last': '', 'number': '(555) 010-0001'}]
        self.dial('5550100001')
        self.press('KEY_ESC')
        self.assertEqual(self.log()[0]['name'], 'Ann')
        reset_state(screen='dial', dial_buffer='', dial_quick_index=-1, calls=self.log())
        self.press('KEY_DOWN', 'KEY_ENTER', 'KEY_ESC')                # the first quick-dial contact
        self.assertEqual(self.log()[0]['name'], 'Ann')

    def test_redialing_from_the_log_calls_again_and_logs_again(self):
        self.dial('5551234567')
        self.press('KEY_ESC')
        reset_state(screen='calls_list', calls=self.log(), calls_index=1)   # the first log row
        self.press('KEY_ENTER')                                              # the person's page, CALL selected
        self.assertEqual(kyphone_os.state['screen'], 'contact')
        self.press('KEY_ENTER')
        self.assertEqual((kyphone_os.state['screen'], kyphone_os.state['call_name']), ('outgoing', '(555) 123-4567'))
        self.press('KEY_ENTER')
        self.clock[0] += 9
        self.press('KEY_ESC')
        self.assertEqual([(e['name'], e['duration']) for e in self.log()], [('(555) 123-4567', '0:09'), ('(555) 123-4567', '')])

    def test_the_log_keeps_the_newest_fifty(self):
        for i in range(kyphone_os.CALL_LOG_MAX + 5):
            kyphone_os.state['call_name'] = 'Caller %d' % i
            kyphone_os._log_call('IN', 3)
        self.assertEqual(len(self.log()), kyphone_os.CALL_LOG_MAX)
        self.assertEqual((self.log()[0]['name'], self.log()[-1]['name']), ('Caller 54', 'Caller 5'))

    def test_a_long_log_is_windowed_and_the_frame_fits(self):
        for i in range(10):
            kyphone_os.state['call_name'] = 'A very long caller name %d' % i
            kyphone_os._log_call('OUT', 3599 + i)
        reset_state(screen='calls_list', calls=self.log(), calls_index=0, calls_start=0)
        kyphone_os.push_calls()
        wire = _wire(self.ps)
        self.assertEqual(len(wire.split('|')[2:]), kyphone_os.CALLS_ROWS)
        self.assertLessEqual(len(wire), kyphone_os.MAX_COMMAND_CHARS)
        self.assertEqual(kyphone_os._duration_text(3600), '1:00:00')
        self.assertEqual([kyphone_os._duration_text(x) for x in (0, 9, 75, 3599, 3723)], ['0:00', '0:09', '1:15', '59:59', '1:02:03'])

    def test_the_log_is_saved_and_comes_back_after_a_restart(self):
        self.dial('5551234567')
        self.press('KEY_ENTER')
        self.clock[0] += 30
        self.press('KEY_ESC')
        with open(self.file) as f:
            saved = json.load(f)
        self.assertEqual((saved[0]['name'], saved[0]['tag'], saved[0]['duration']), ('(555) 123-4567', 'OUT', '0:30'))
        kyphone_os.state['calls'] = []                                 # as after a restart
        kyphone_os.load_calls()
        self.assertEqual([(e['name'], e['tag'], e['duration']) for e in self.log()], [('(555) 123-4567', 'OUT', '0:30')])

    def test_a_damaged_log_file_or_odd_entries_are_ignored(self):
        for content in ('{{{ not json', '"a string"', '{"a": 1}', '[1, "x", null, {"name": 5}, {"name": "x", "tag": "WHAT"}]'):
            with open(self.file, 'w') as f:
                f.write(content)
            kyphone_os.state['calls'] = [{'name': 'stale', 'tag': 'IN', 'ts': '', 'duration': ''}]
            kyphone_os.load_calls()
            self.assertEqual(self.log(), [], content)
        with open(self.file, 'w') as f:
            json.dump([{'name': 'ok', 'tag': 'MISS', 'ts': '', 'duration': ''}, {'name': 'bad', 'tag': 'X'}], f)
        kyphone_os.load_calls()
        self.assertEqual([e['name'] for e in self.log()], ['ok'])

    def test_a_missing_log_file_is_an_empty_log(self):
        kyphone_os.state['calls'] = [{'name': 'x', 'tag': 'IN', 'ts': '', 'duration': ''}]
        kyphone_os.load_calls()
        self.assertEqual(self.log(), [])

    def test_when_a_call_happened_reads_as_a_time_a_day_or_a_date(self):
        from datetime import datetime, timedelta
        now = datetime.now()
        for delta, expect in ((timedelta(0), None), (timedelta(days=1), 'Yesterday')):
            entry = {'name': 'x', 'tag': 'IN', 'ts': (now - delta).isoformat(), 'duration': ''}
            shown = kyphone_os._call_time(entry)
            if expect:
                self.assertEqual(shown, expect)
            else:
                self.assertRegex(shown, r'^\d{1,2}:\d{2} [AP]M$')
        self.assertEqual(kyphone_os._call_time({'name': 'old', 'tag': 'IN', 'time': '4:03 PM', 'duration': ''}), '4:03 PM')   # an old-format entry

    def test_hanging_up_a_call_with_no_start_time_still_logs_it(self):
        reset_state(screen='in_call', call_name='Ann', call_started_at=None, calls=[])
        kyphone_os.state['call_dir'] = 'OUT'
        self.press('KEY_ESC')
        self.assertEqual((self.log()[0]['tag'], self.log()[0]['duration']), ('OUT', '0:00'))


# ═══════════════════════════════════════════════════════════════════════════════
# Settings — Wi-Fi and Bluetooth (network_control.py is mocked throughout; no real
# nmcli/bluetoothctl call happens in this suite)
# ═══════════════════════════════════════════════════════════════════════════════

nc = kyphone_os.netctl
md = kyphone_os.modem


class FakeNet:
    """A pretend Wi-Fi and Bluetooth stack in place of network_control's commands; records what the phone asked."""
    def __init__(self, wifi_on=True, current='Maple', nearby=None, bt_on=True, known=None, new=None):
        self.wifi_on, self.current, self.bt_on = wifi_on, current, bt_on
        self.nearby = nearby if nearby is not None else [
            nc.WifiNetwork('Maple', 90, True, True), nc.WifiNetwork('willow', 60, True, False),
            nc.WifiNetwork('Birch_5G', 40, True, False), nc.WifiNetwork('OpenCafe', 30, False, False)]
        self.known = known if known is not None else [
            nc.BtDevice('11:22:33:44:55:66', 'ZitaoTech_q10', True, True, is_input=True),
            nc.BtDevice('AA:AA:AA:AA:AA:AA', 'Headphones', True, False)]
        self.new = new if new is not None else [nc.BtDevice('BB:BB:BB:BB:BB:BB', 'Speaker', False, False)]
        self.calls = []
        self.connect_result = nc.Result(True)

    def patches(self):
        f = self
        def log(name, result):
            def fn(*a, **k):
                f.calls.append((name,) + a)
                return result() if callable(result) else result
            return fn
        return patch.multiple(
            nc,
            wifi_enabled=lambda: f.wifi_on,
            wifi_status=lambda: nc.WifiStatus(bool(f.current and f.wifi_on), f.current if f.wifi_on else None),
            wifi_scan=log('wifi_scan', lambda: list(f.nearby)),
            wifi_set_enabled=log('wifi_set_enabled', lambda: f._switch('wifi_on')),
            wifi_forget=log('wifi_forget', nc.Result(True)),
            wifi_connect=log('wifi_connect', lambda: f.connect_result),
            bt_status=lambda: nc.BtStatus(f.bt_on, [d.name for d in f.known if d.connected]),
            bt_powered=lambda: f.bt_on,
            bt_known=log('bt_known', lambda: list(f.known)),
            bt_scan=log('bt_scan', lambda: list(f.known) + list(f.new)),
            bt_set_powered=log('bt_set_powered', lambda: f._switch('bt_on')),
            bt_forget=log('bt_forget', nc.Result(True)),
            bt_pair_connect=log('bt_pair_connect', lambda: f.connect_result),
        )

    def _switch(self, attr):
        setattr(self, attr, self.calls[-1][1])
        return nc.Result(True)

    def called(self, name):
        return [c[1:] for c in self.calls if c[0] == name]


class SettingsBase(unittest.TestCase):
    def setUp(self):
        self._run_async = patch.object(kyphone_os, '_run_async', new=lambda fn: fn())   # synchronous for tests
        self._run_async.start()
        self.addCleanup(self._run_async.stop)
        self._settle = patch.object(kyphone_os, 'WIFI_ON_SETTLE', 0)
        self._settle.start()
        self.addCleanup(self._settle.stop)
        self.net = FakeNet()
        self._net = self.net.patches()
        self._net.start()
        self.addCleanup(self._net.stop)

    def press(self, keycode):
        with patch.object(kyphone_os, 'push_screen') as ps:
            kyphone_os.handle_key(keycode)
        return _wire(ps)

    def titles(self, wire):
        return [r.split(CELL)[0] for r in _rows(wire, 3)]

    def open_settings(self, row):
        reset_state(screen='home', home_index=kyphone_os.HOME_MENU.index('SETTINGS'))
        self.press('KEY_ENTER')
        if row:
            self.press('KEY_DOWN')
        return self.press('KEY_ENTER')

    def select(self, title, wire):
        """Move down from the current row to the one titled `title`; returns the last wire."""
        for _ in range(15):
            if self.selected(wire) == title:
                return wire
            wire = self.press('KEY_DOWN')
        self.fail(f'{title} not found in {wire}')

    def selected(self, wire):
        f = wire.split('|')
        sel = int(f[2])
        return f[3 + sel].split(CELL)[0] if sel >= 0 else None


class TestSettingsScreen(SettingsBase):
    def test_opening_shows_each_status(self):
        reset_state(screen='home', home_index=kyphone_os.HOME_MENU.index('SETTINGS'))
        wire = self.press('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'settings')
        rows = _rows(wire, 2)
        self.assertIn('Connected: Maple', rows[0])
        self.assertIn('Connected: ZitaoTech_q10', rows[1])

    def test_a_switched_off_radio_reads_off(self):
        self.net.wifi_on, self.net.bt_on = False, False
        reset_state(screen='home', home_index=kyphone_os.HOME_MENU.index('SETTINGS'))
        rows = _rows(self.press('KEY_ENTER'), 2)
        self.assertIn(CELL + 'Off' + CELL, rows[0])
        self.assertIn(CELL + 'Off' + CELL, rows[1])

    def test_up_down_and_header(self):
        reset_state(screen='home', home_index=kyphone_os.HOME_MENU.index('SETTINGS'))
        self.press('KEY_ENTER')
        for _ in range(6):
            self.press('KEY_DOWN')                             # clamped: five rows
        self.assertEqual(kyphone_os.state['settings_index'], 4)
        for _ in range(5):
            self.press('KEY_UP')
        self.assertEqual(kyphone_os.state['settings_index'], -1)
        self.press('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'home')

    def test_screen_light_and_activity_mark_rows(self):
        reset_state(screen='home', home_index=kyphone_os.HOME_MENU.index('SETTINGS'), light=3, activity_mark=True)
        rows = _rows(self.press('KEY_ENTER'), 2)
        self.assertEqual(rows[2], 'Screen light' + CELL + 'Level 3 of 8' + CELL)
        self.assertEqual(rows[3], 'Activity mark' + CELL + 'A * on the lock screen' + CELL + 'ON')

    def test_the_activity_mark_switch(self):
        reset_state(screen='home', home_index=kyphone_os.HOME_MENU.index('SETTINGS'), activity_mark=True)
        self.press('KEY_ENTER')
        for _ in range(3):
            self.press('KEY_DOWN')
        with patch.object(kyphone_os, 'save_settings') as save:
            wire = self.press('KEY_ENTER')
        save.assert_called_once()
        self.assertFalse(kyphone_os.state['activity_mark'])
        self.assertTrue(_rows(wire, 2)[3].endswith(CELL + 'OFF'))

    def test_the_screen_light_screen(self):
        reset_state(screen='home', home_index=kyphone_os.HOME_MENU.index('SETTINGS'), light=0)
        self.press('KEY_ENTER')
        self.press('KEY_DOWN')
        self.press('KEY_DOWN')
        with patch.object(kyphone_os, 'save_settings') as save:
            self.assertEqual(self.press('KEY_ENTER'), 'LIGHTSET|0')
            self.assertEqual(self.press('KEY_RIGHT'), 'LIGHTSET|1')
            self.assertEqual(self.press('KEY_UP'), 'LIGHTSET|2')
            self.assertEqual(self.press('KEY_LEFT'), 'LIGHTSET|1')
            self.assertEqual(self.press('KEY_DOWN'), 'LIGHTSET|0')
            self.assertEqual(self.press('KEY_DOWN'), 'LIGHTSET|0')      # at the end: redrawn, so it is answered
            for _ in range(12):
                wire = self.press('KEY_RIGHT')
            self.assertEqual(wire, 'LIGHTSET|8')
            self.assertGreater(save.call_count, 5)                     # saved as it changes
        self.assertEqual(self.press('CHAR:d'), 'LIGHTSET|8')          # D is an arrow here too
        wire = self.press('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'settings')
        self.assertIn('Level 8 of 8', _rows(wire, 2)[2])

    def test_the_lock_command_carries_the_light(self):
        reset_state(screen='lock', light=6, messages=[], calls=[])
        with patch.object(kyphone_os, 'push_screen') as ps:
            kyphone_os.push_lock()
        self.assertEqual(_wire(ps).split('|')[-1], '6')

    def test_esc_returns_home_on_settings(self):
        reset_state(screen='home', home_index=kyphone_os.HOME_MENU.index('SETTINGS'))
        self.press('KEY_ENTER')
        self.press('KEY_ESC')
        self.assertEqual(kyphone_os.state['screen'], 'home')
        self.assertEqual(kyphone_os.state['home_index'], kyphone_os.HOME_MENU.index('SETTINGS'))


class TestSettingsResponsiveness(SettingsBase):
    """Found in review: reading Wi-Fi and Bluetooth on the keyboard's thread held up key presses."""
    def test_after_the_first_time_settings_draws_at_once_and_refreshes_in_the_background(self):
        reset_state(screen='home', home_index=kyphone_os.HOME_MENU.index('SETTINGS'), settings_wifi=None)
        self.press('KEY_ENTER')                                   # first open: read, then draw
        self.press('KEY_ESC')
        pending = []
        with patch.object(kyphone_os, '_run_async', new=pending.append), \
                patch.object(kyphone_os, 'push_screen') as ps:
            kyphone_os.handle_key('KEY_ENTER')                    # again: drawn at once from what was known
            self.assertEqual(ps.call_count, 1)
            self.assertEqual(len(pending), 1)                     # the read waits for the background
            pending[0]()                                          # nothing changed: no second refresh
            self.assertEqual(ps.call_count, 1)
            self.net.current = 'Birch_5G'
            kyphone_os.handle_key('KEY_ESC')
            kyphone_os.handle_key('KEY_ENTER')
            before = ps.call_count
            pending[-1]()                                         # something changed: one redraw
        self.assertEqual(ps.call_count, before + 1)
        self.assertIn('Connected: Birch_5G', _rows(_wire(ps), 2)[0])


class TestWifiList(SettingsBase):
    def test_the_switch_then_the_joined_network_then_the_rest_a_to_z(self):
        wire = self.open_settings(0)
        self.assertEqual(kyphone_os.state['screen'], 'wifi')
        self.assertTrue(wire.startswith('NETLIST|W|0|'))
        self.assertEqual(self.titles(wire), ['Wi-Fi', 'Maple', 'Birch_5G', 'OpenCafe', 'willow'])
        rows = _rows(wire, 3)
        self.assertEqual(rows[0], 'Wi-Fi' + CELL + 'Wi-Fi is on' + CELL + 'ON')
        self.assertEqual(rows[1], 'Maple' + CELL + 'Connected' + CELL)
        self.assertIn(CELL + 'Open' + CELL, rows[3])

    def test_the_list_is_drawn_before_the_search_answers(self):
        pending = []
        reset_state(screen='home', home_index=kyphone_os.HOME_MENU.index('SETTINGS'))
        self.press('KEY_ENTER')
        with patch.object(kyphone_os, '_run_async', new=pending.append):
            wire = self.press('KEY_ENTER')
        self.assertEqual(self.titles(wire), ['Wi-Fi', 'Maple', 'SEARCHING...'])
        self.assertEqual(self.net.called('wifi_scan'), [])     # the search has not even run yet
        with patch.object(kyphone_os, 'push_screen') as ps:
            pending[0]()                                       # the search answers: one redraw
        self.assertEqual(self.titles(_wire(ps))[-1], 'willow')        # (the window shows the first five rows)
        self.assertEqual(ps.call_count, 1)

    def test_search_again_is_the_last_row_and_runs_another_search(self):
        self.open_settings(0)
        for _ in range(8):
            wire = self.press('KEY_DOWN')
        self.assertEqual(self.selected(wire), 'SEARCH AGAIN')
        self.press('KEY_ENTER')
        self.assertEqual(len(self.net.called('wifi_scan')), 2)

    def test_an_empty_search_says_so(self):
        self.net.current, self.net.nearby = None, []
        wire = self.open_settings(0)
        self.assertEqual(_rows(wire, 3)[1], 'SEARCH AGAIN' + CELL + 'No networks found' + CELL)

    def test_switched_off_the_list_is_only_the_switch_and_nothing_searches(self):
        self.net.wifi_on = False
        wire = self.open_settings(0)
        self.assertEqual(_rows(wire, 3), ['Wi-Fi' + CELL + 'Wi-Fi is off' + CELL + 'OFF'])
        self.assertEqual(self.net.called('wifi_scan'), [])

    def test_the_switch_turns_wifi_off_and_on(self):
        self.open_settings(0)
        wire = self.press('KEY_ENTER')                         # on the switch row
        self.assertEqual(self.net.called('wifi_set_enabled'), [(False,)])
        self.assertEqual(_rows(wire, 3), ['Wi-Fi' + CELL + 'Wi-Fi is off' + CELL + 'OFF'])
        wire = self.press('KEY_ENTER')
        self.assertEqual(self.net.called('wifi_set_enabled'), [(False,), (True,)])
        self.assertEqual(self.titles(wire)[:2], ['Wi-Fi', 'Maple'])

    def test_the_joined_network_offers_forget_this_network_on_the_safe_default(self):
        self.open_settings(0)
        self.press('KEY_DOWN')
        wire = self.press('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'confirm')
        self.assertTrue(wire.startswith('CONFIRM|WI-FI|FORGET MAPLE?'))
        self.assertTrue(wire.endswith('|FORGET|KEEP|K'))
        self.press('KEY_ENTER')                                # KEEP: nothing forgotten
        self.assertEqual((kyphone_os.state['screen'], self.net.called('wifi_forget')), ('wifi', []))

    def test_forget_this_network(self):
        self.open_settings(0)
        self.press('KEY_DOWN')
        self.press('KEY_ENTER')
        self.press('KEY_LEFT')
        self.net.current = None                               # (what nmcli would report afterwards)
        wire = self.press('KEY_ENTER')
        self.assertEqual(self.net.called('wifi_forget'), [('Maple',)])
        self.assertEqual(kyphone_os.state['screen'], 'wifi')
        self.assertEqual(self.titles(wire)[1], 'Birch_5G')

    def test_a_secured_network_asks_for_its_password(self):
        wire = self.open_settings(0)
        self.select('Birch_5G', wire)
        wire = self.press('KEY_ENTER')
        self.assertEqual(wire, 'NETPASS|Birch_5G||')

    def test_an_open_network_connects_at_once_and_ok_returns_to_the_list(self):
        wire = self.open_settings(0)
        self.select('OpenCafe', wire)
        wire = self.press('KEY_ENTER')
        self.assertEqual(self.net.called('wifi_connect'), [('OpenCafe', None)])
        self.assertEqual(wire, 'NETSTATE|W|OK|Connected to OpenCafe.')
        self.press('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'wifi')

    def test_esc_and_the_header_go_back_to_settings(self):
        self.open_settings(0)
        self.press('KEY_ESC')
        self.assertEqual(kyphone_os.state['screen'], 'settings')
        self.open_settings(0)
        self.press('KEY_UP')
        self.press('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'settings')

    def test_a_long_list_scrolls(self):
        self.net.nearby = [nc.WifiNetwork(f'Net{i:02d}', 50, True, False) for i in range(12)]
        self.net.current = None
        self.open_settings(0)
        for _ in range(13):                                    # the switch, 12 networks, SEARCH AGAIN
            wire = self.press('KEY_DOWN')
        self.assertEqual(self.selected(wire), 'SEARCH AGAIN')
        self.assertLessEqual(len(_rows(wire, 3)), kyphone_os.NET_ROWS)

    def test_a_search_answering_after_leaving_is_dropped(self):
        pending = []
        reset_state(screen='home', home_index=kyphone_os.HOME_MENU.index('SETTINGS'))
        self.press('KEY_ENTER')
        with patch.object(kyphone_os, '_run_async', new=pending.append):
            self.press('KEY_ENTER')
        self.press('KEY_ESC')
        with patch.object(kyphone_os, 'push_screen') as ps:
            pending[0]()
        ps.assert_not_called()
        self.assertEqual(kyphone_os.state['screen'], 'settings')


class TestBluetoothList(SettingsBase):
    def test_the_switch_then_known_devices_then_pair_new_device_and_no_scan(self):
        wire = self.open_settings(1)
        self.assertEqual(kyphone_os.state['screen'], 'bluetooth')
        self.assertTrue(wire.startswith('NETLIST|B|0|'))
        self.assertEqual(self.titles(wire), ['Bluetooth', 'ZitaoTech_q10', 'Headphones', 'PAIR NEW DEVICE'])
        self.assertIn(CELL + 'Connected' + CELL, _rows(wire, 3)[1])
        self.assertIn(CELL + 'Not connected' + CELL, _rows(wire, 3)[2])
        self.assertEqual(self.net.called('bt_scan'), [])

    def test_switched_off_the_list_is_only_the_switch(self):
        self.net.bt_on = False
        self.net.known = []
        wire = self.open_settings(1)
        self.assertEqual(_rows(wire, 3), ['Bluetooth' + CELL + 'Bluetooth is off' + CELL + 'OFF'])
        self.press('KEY_ENTER')
        self.assertEqual(self.net.called('bt_set_powered'), [(True,)])

    def test_bluetooth_is_not_switched_off_while_the_keyboard_is_connected_by_it(self):
        self.open_settings(1)
        wire = self.press('KEY_ENTER')
        self.assertEqual(self.net.called('bt_set_powered'), [])
        self.assertTrue(wire.startswith('STUB|BLUETOOTH|BLUETOOTH STAYS ON'))
        self.press('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'bluetooth')

    def test_with_no_keyboard_connected_bluetooth_switches_off(self):
        self.net.known[0].connected = False
        self.open_settings(1)
        self.press('KEY_ENTER')
        self.assertEqual(self.net.called('bt_set_powered'), [(False,)])

    def test_a_known_device_offers_forget_or_re_connect_on_the_safe_default(self):
        wire = self.open_settings(1)
        self.select('Headphones', wire)
        wire = self.press('KEY_ENTER')
        self.assertTrue(wire.startswith('CONFIRM|BLUETOOTH|HEADPHONES IS NOT CONNECTED.'))
        self.assertTrue(wire.endswith('|FORGET|RE-CONNECT|K'))

    def test_re_connect(self):
        wire = self.open_settings(1)
        self.select('Headphones', wire)
        self.press('KEY_ENTER')
        wire = self.press('KEY_ENTER')                         # RE-CONNECT holds the selection
        self.assertEqual(self.net.called('bt_pair_connect'), [('AA:AA:AA:AA:AA:AA',)])
        self.assertEqual(wire, 'NETSTATE|B|OK|Connected to Headphones.')
        self.press('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'bluetooth')

    def test_esc_on_the_question_does_nothing(self):
        wire = self.open_settings(1)
        self.select('Headphones', wire)
        self.press('KEY_ENTER')
        self.press('KEY_ESC')
        self.assertEqual((kyphone_os.state['screen'], self.net.called('bt_pair_connect')), ('bluetooth', []))

    def test_forget_a_device(self):
        wire = self.open_settings(1)
        self.select('Headphones', wire)
        self.press('KEY_ENTER')
        self.press('KEY_LEFT')
        self.press('KEY_ENTER')
        self.assertEqual(self.net.called('bt_forget'), [('AA:AA:AA:AA:AA:AA',)])
        self.assertEqual(kyphone_os.state['screen'], 'bluetooth')

    def test_the_keyboard_cannot_be_forgotten_even_when_switched_off(self):
        for connected in (True, False):
            self.net.known[0].connected = connected
            self.net.calls.clear()
            self._forget_keyboard()

    def _forget_keyboard(self):
        wire = self.open_settings(1)
        self.select('ZitaoTech_q10', wire)
        self.press('KEY_ENTER')
        self.press('KEY_LEFT')
        wire = self.press('KEY_ENTER')
        self.assertEqual(self.net.called('bt_forget'), [])
        self.assertTrue(wire.startswith('STUB|BLUETOOTH|THE KEYBOARD CANNOT BE FORGOTTEN'))
        self.press('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'bluetooth')

    def test_pair_new_device_searches_and_lists_only_new_devices(self):
        wire = self.open_settings(1)
        self.select('PAIR NEW DEVICE', wire)
        wire = self.press('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'btpair')
        self.assertTrue(wire.startswith('NETLIST|P|0|'))
        self.assertEqual(self.titles(wire), ['Speaker', 'SEARCH AGAIN'])
        self.assertEqual(len(self.net.called('bt_scan')), 1)

    def test_pairing_a_new_device_and_back(self):
        wire = self.open_settings(1)
        self.select('PAIR NEW DEVICE', wire)
        self.press('KEY_ENTER')
        wire = self.press('KEY_ENTER')                         # Speaker
        self.assertEqual(wire, 'NETSTATE|B|OK|Connected to Speaker.')
        self.press('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'bluetooth')

    def test_a_failed_pairing_returns_to_other_devices(self):
        self.net.connect_result = nc.Result(False, 'pairing was refused')
        wire = self.open_settings(1)
        self.select('PAIR NEW DEVICE', wire)
        self.press('KEY_ENTER')
        wire = self.press('KEY_ENTER')
        self.assertEqual(wire, 'NETSTATE|B|FAIL|pairing was refused')
        wire = self.press('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'btpair')
        self.assertTrue(wire.startswith('NETLIST|P|'))

    def test_esc_from_other_devices_goes_back_to_bluetooth(self):
        wire = self.open_settings(1)
        self.select('PAIR NEW DEVICE', wire)
        self.press('KEY_ENTER')
        self.press('KEY_ESC')
        self.assertEqual(kyphone_os.state['screen'], 'bluetooth')

    def test_no_new_devices_says_so(self):
        self.net.new = []
        wire = self.open_settings(1)
        self.select('PAIR NEW DEVICE', wire)
        wire = self.press('KEY_ENTER')
        self.assertEqual(_rows(wire, 3), ['SEARCH AGAIN' + CELL + 'No devices found' + CELL])

    def test_q_cancels_a_pairing_in_progress(self):
        """The connecting screen's hint says Q: the phone keyboard has no Esc key."""
        wire = self.open_settings(1)
        self.select('PAIR NEW DEVICE', wire)
        self.press('KEY_ENTER')
        with patch.object(kyphone_os, '_run_async', new=lambda fn: None):      # the pairing never answers
            self.press('KEY_ENTER')
        self.assertEqual(kyphone_os.state['net_status'], 'WORKING')
        self.press('CHAR:q')
        self.assertEqual(kyphone_os.state['screen'], 'btpair')


class TestWifiPassword(SettingsBase):
    def open(self, ssid='Birch_5G'):
        wire = self.open_settings(0)
        self.select(ssid, wire)
        self.press('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'netpass')

    def test_typing_never_puts_the_real_password_on_the_wire(self):
        self.open()
        wire = self.press('CHAR:h')
        self.assertNotIn('h', wire.split('|')[2])                 # only the mask travels
        for ch in 'unter2':
            wire = self.press('CHAR:' + ch)
        self.assertEqual(kyphone_os.state['net_pass'], 'hunter2')
        self.assertEqual(wire.split('|')[2], '*' * len('hunter2'))

    def test_backspace_edits_the_typed_password(self):
        self.open()
        kyphone_os.state['net_pass'] = 'wrongpw'
        wire = self.press('KEY_BACKSPACE')
        self.assertEqual(kyphone_os.state['net_pass'], 'wrongp')
        self.assertEqual(wire.split('|')[2], '*' * len('wrongp'))

    def test_enter_attempts_the_connection(self):
        self.open()
        kyphone_os.state['net_pass'] = 'hunter2'
        self.press('KEY_ENTER')
        self.assertEqual(self.net.called('wifi_connect'), [('Birch_5G', 'hunter2')])
        self.assertEqual(kyphone_os.state['net_status'], 'OK')

    def test_a_wrong_password_returns_to_the_password_screen_with_it_kept(self):
        self.net.connect_result = nc.Result(False, 'wrong password')
        self.open()
        kyphone_os.state['net_pass'] = 'wrongpw'
        self.press('KEY_ENTER')                                   # -> netstate, FAIL
        self.press('KEY_ENTER')                                   # dismiss the result
        self.assertEqual(kyphone_os.state['screen'], 'netpass')
        self.assertEqual(kyphone_os.state['net_pass'], 'wrongpw')  # not cleared — one correction away, not a retype

    def test_esc_goes_back_to_the_list(self):
        self.open()
        self.press('KEY_ESC')
        self.assertEqual(kyphone_os.state['screen'], 'wifi')

    def test_up_selects_the_back_arrow_and_enter_there_goes_back_to_the_list(self):
        """The phone keyboard has no Esc key: ↑ then Enter is the way out, as on New Message."""
        self.open()
        kyphone_os.state['net_pass'] = 'hunt'
        wire = self.press('KEY_UP')
        self.assertEqual(wire.split('|')[3], 'B')
        wire = self.press('KEY_ENTER')
        self.assertEqual(self.net.called('wifi_connect'), [])      # backing out never tries to connect
        self.assertEqual(kyphone_os.state['screen'], 'wifi')
        self.assertTrue(wire.startswith('NETLIST|W|'))

    def test_down_returns_to_the_field(self):
        self.open()
        self.press('KEY_UP')
        wire = self.press('KEY_DOWN')
        self.assertEqual(wire.split('|')[3], '')
        self.assertFalse(kyphone_os.state['net_pass_hdr'])

    def test_typing_while_the_back_arrow_is_selected_returns_to_the_field_and_types(self):
        self.open()
        self.press('KEY_UP')
        wire = self.press('CHAR:q')                               # q is a letter here, not back
        self.assertEqual(kyphone_os.state['screen'], 'netpass')
        self.assertEqual(kyphone_os.state['net_pass'], 'q')
        self.assertEqual(wire.split('|')[2:], ['*', ''])

    def test_reopening_starts_in_the_field(self):
        self.open()
        self.press('KEY_UP')
        self.press('KEY_ENTER')                                   # back to the list, on Birch_5G
        wire = self.press('KEY_ENTER')
        self.assertEqual(wire, 'NETPASS|Birch_5G||')


class TestNetstate(SettingsBase):
    def test_success_refreshes_settings_status(self):
        wire = self.open_settings(0)
        self.select('OpenCafe', wire)
        self.press('KEY_ENTER')
        self.net.current = 'OpenCafe'
        self.press('KEY_ENTER')                                   # OK -> the refreshed list
        wire = self.press('KEY_ESC')                              # -> Settings, read again
        self.assertIn('Connected: OpenCafe', _rows(wire, 2)[0])

    def test_leaving_while_working_abandons_it(self):
        reset_state(screen='netstate', net_kind='B', net_status='WORKING', net_detail='Pairing...',
                    net_source='bluetooth', net_rows=[])
        self.press('KEY_ESC')
        self.assertEqual(kyphone_os.state['screen'], 'bluetooth')

    def test_a_result_that_arrives_after_leaving_is_dropped(self):
        reset_state(screen='netstate', net_kind='W', net_ssid='Maple', net_status='WORKING', net_source='wifi')
        pending = []
        with patch.object(kyphone_os, '_run_async', new=pending.append):
            with patch.object(kyphone_os, 'push_screen'):
                kyphone_os._open_netstate_wifi('Maple', None, source='wifi')
        kyphone_os.state['screen'] = 'home'                        # navigated away before the call returned
        pending[-1]()
        self.assertEqual(kyphone_os.state['net_status'], 'WORKING')   # never overwritten

    def test_every_settings_screen_is_plain_ascii(self):
        wires = [self.open_settings(0), self.open_settings(1)]
        with patch.object(kyphone_os, '_run_async', new=lambda fn: None):
            wires.append(self.open_settings(0))                  # SEARCHING...
        for wire in wires:
            self.assertTrue(all(32 <= ord(c) < 127 or c == CELL for c in wire), wire)


# ═══════════════════════════════════════════════════════════════════════════════
# The real cellular modem (spi_bridge/modem.py) is the only way texts are sent and received. modem.SimModem is used throughout — no real hardware or pyserial anywhere in this file.
# ═══════════════════════════════════════════════════════════════════════════════

class TestModemTransport(unittest.TestCase):
    def setUp(self):
        self._save = patch.object(kyphone_os, 'save_messages')
        self._save.start()
        self.addCleanup(self._save.stop)
        self.addCleanup(setattr, kyphone_os, '_modem', None)   # never leak a fake modem into other tests

    def test_transport_send_goes_through_the_modem(self):
        fake = md.SimModem()
        kyphone_os._modem = fake
        with patch.object(kyphone_os, 'SIM_MODE', False):
            kyphone_os._transport_send('555-010-0001', 'hi')
        self.assertEqual(fake.sent, [('+15550100001', 'hi')])

    def test_transport_send_raises_no_service_without_a_modem(self):
        kyphone_os._modem = None
        with patch.object(kyphone_os, 'SIM_MODE', False):
            with self.assertRaises(RuntimeError):
                kyphone_os._transport_send('555-010-0001', 'hi')

    def test_twilio_is_gone(self):
        with open(kyphone_os.__file__) as f:
            self.assertNotIn('twilio', f.read().lower())

    def test_a_modem_send_failure_propagates_like_any_other_transport_failure(self):
        fake = md.SimModem()
        fake.fail_next_send('no signal')
        kyphone_os._modem = fake
        with patch.object(kyphone_os, 'SIM_MODE', False):
            with self.assertRaises(md.ModemError):
                kyphone_os._transport_send('555-010-0001', 'hi')

    def test_init_modem_does_nothing_in_sim_mode(self):
        kyphone_os._modem = None
        with patch.object(kyphone_os, 'SIM_MODE', True):
            kyphone_os._init_modem()
        self.assertIsNone(kyphone_os._modem)

    def test_init_modem_does_nothing_without_the_env_var(self):
        kyphone_os._modem = None
        with patch.object(kyphone_os, 'SIM_MODE', False):
            os.environ.pop(md.MODEM_PORT_ENV, None)
            kyphone_os._init_modem()
        self.assertIsNone(kyphone_os._modem)

    def test_init_modem_picks_up_a_working_dongle(self):
        kyphone_os._modem = None
        fake = md.SimModem()
        with patch.object(kyphone_os, 'SIM_MODE', False), \
                patch.dict(os.environ, {md.MODEM_PORT_ENV: '/dev/ttyUSB2'}), \
                patch.object(md, 'SerialModem', return_value=fake):
            kyphone_os._init_modem()
        self.assertIs(kyphone_os._modem, fake)

    def test_init_modem_falls_back_quietly_when_the_dongle_does_not_answer(self):
        kyphone_os._modem = None
        with patch.object(kyphone_os, 'SIM_MODE', False), \
                patch.dict(os.environ, {md.MODEM_PORT_ENV: '/dev/ttyUSB2'}), \
                patch.object(md, 'SerialModem', side_effect=md.ModemError('no such device')):
            kyphone_os._init_modem()
        self.assertIsNone(kyphone_os._modem)


class TestModemReceiveLoop(unittest.TestCase):
    """modem_sms_loop drains whatever the modem hands back into the message list — with no dedup
    bookkeeping, since the modem's own storage already dedupes (poll_new removes each message as it's
    read)."""

    def setUp(self):
        self._save = patch.object(kyphone_os, 'save_messages')
        self._save.start()
        self.addCleanup(self._save.stop)
        self.addCleanup(setattr, kyphone_os, '_modem', None)

    def run_one_pass(self, fake):
        """modem_sms_loop's body is one iteration of an infinite poll loop; run exactly one pass by
        having the loop's own time.sleep stop state['running'] right after the first poll."""
        kyphone_os._modem = fake

        def fake_sleep(_):
            with kyphone_os.state['lock']:
                kyphone_os.state['running'] = False

        reset_state(running=True, screen='texts_list', messages=[])
        with patch.object(kyphone_os.time, 'sleep', fake_sleep):
            kyphone_os.modem_sms_loop()

    def test_a_delivered_text_lands_in_messages_unread(self):
        fake = md.SimModem()
        fake.deliver('+15550100009', 'surprise!')
        self.run_one_pass(fake)
        self.assertEqual(len(kyphone_os.state['messages']), 1)
        msg = kyphone_os.state['messages'][0]
        self.assertEqual((msg['sender'], msg['body'], msg['read']), ('+15550100009', 'surprise!', False))

    def test_two_delivered_texts_both_land(self):
        fake = md.SimModem()
        fake.deliver('+15550100009', 'one')
        fake.deliver('+15550100008', 'two')
        self.run_one_pass(fake)
        self.assertEqual([m['body'] for m in kyphone_os.state['messages']], ['one', 'two'])

    def test_nothing_delivered_is_a_quiet_no_op(self):
        fake = md.SimModem()
        self.run_one_pass(fake)
        self.assertEqual(kyphone_os.state['messages'], [])

    def test_a_reply_in_national_format_joins_the_existing_conversation(self):
        fake = md.SimModem()
        fake.deliver('5550100001', 'reply')                  # the network dropped the +1
        kyphone_os._modem = fake
        reset_state(running=True, screen='thread', thread_id='+15550100001',
                    messages=[{'dir': 'out', 'peer': '+15550100001', 'name': 'You', 'body': 'hi', 'read': True,
                               'ts': '', 'state': 'sent'}])
        def stop(_):
            kyphone_os.state['running'] = False
        with patch.object(kyphone_os.time, 'sleep', stop), patch.object(kyphone_os, 'push_screen') as ps:
            kyphone_os.modem_sms_loop()
        self.assertEqual(kyphone_os.state['messages'][-1]['sender'], '+15550100001')
        self.assertTrue(_wire(ps).startswith('THREAD2|'))     # the open conversation redrew

    def test_an_unexpected_error_does_not_end_the_loop(self):
        class Flaky(md.SimModem):
            calls = 0

            def poll_new(self):
                Flaky.calls += 1
                if Flaky.calls == 1:
                    raise OSError(5, 'Input/output error')     # not a ModemError
                self._inbox, got = [], list(self._inbox)
                return got
        fake = Flaky()
        fake.deliver('+15550100009', 'after the glitch')
        kyphone_os._modem = fake
        reset_state(running=True, screen='texts_list', messages=[])
        naps = []
        def sleep(_):
            naps.append(1)
            if len(naps) == 2:
                kyphone_os.state['running'] = False
        with patch.object(kyphone_os.time, 'sleep', sleep), patch.object(kyphone_os, 'push_screen'):
            kyphone_os.modem_sms_loop()
        self.assertEqual([m['body'] for m in kyphone_os.state['messages']], ['after the glitch'])

    def test_no_modem_configured_returns_at_once(self):
        kyphone_os._modem = None
        kyphone_os.modem_sms_loop()   # must return immediately, not loop forever waiting on nothing


if __name__ == '__main__':
    unittest.main()
