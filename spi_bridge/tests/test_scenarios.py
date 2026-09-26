"""
test_scenarios.py — the phone used the way a person uses it, end to end.

Every scenario drives the real kyphone_os.handle_key, key by key, against a whole pretend phone: a scratch data
folder, a fake cellular modem (modem.SimModem), a fake Wi-Fi/Bluetooth stack, a real test book (EPUB) and real tiny
audio files. Then a random-key stress run presses thousands of keys across every screen. Everything any of it sends
to the panel is checked: it fits one frame, it is drawable ASCII, the emulator can draw it, and the firmware's own
renderers draw it under the address and undefined-behaviour sanitizers.

    KYPHONE_DATA_DIR=$(mktemp -d) python3 -m pytest spi_bridge/tests/test_scenarios.py -v
"""

import copy
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.argv = ['test', '--sim']
for _name in ('spidev', 'gpiod', 'input_handler', 'evdev'):
    sys.modules.setdefault(_name, MagicMock())
_faked = [name for name in ('pygame', 'simulator') if name not in sys.modules]
for _name in _faked:
    sys.modules[_name] = MagicMock()
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)
import kyphone_os  # noqa: E402
import modem as md  # noqa: E402
import network_control as nc  # noqa: E402
import audio_fixtures as fx  # noqa: E402
from epub_fixtures import make_epub  # noqa: E402
for _name in _faked:
    if isinstance(sys.modules.get(_name), MagicMock):
        del sys.modules[_name]

SEP = '\xb7'
BASE_STATE = {k: copy.deepcopy(v) for k, v in kyphone_os.state.items() if k != 'lock'}
ALL_COMMANDS = set()           # every command any test here pushed, checked by the renderers at the end


class Clock:
    def __init__(self):
        self.t = 5000.0

    def __call__(self):
        return self.t


class FakeNet:
    def __init__(self):
        self.wifi_on, self.current, self.bt_on = True, 'Maple', True
        self.nearby = [nc.WifiNetwork('Maple', 90, True, True), nc.WifiNetwork('Birch_5G', 50, True, False),
                       nc.WifiNetwork('OpenCafe', 30, False, False)]
        self.known = [nc.BtDevice('11:22:33:44:55:66', 'Keyboard', True, True, is_input=True),
                      nc.BtDevice('AA:AA:AA:AA:AA:AA', 'Headphones', True, False)]
        self.new = [nc.BtDevice('BB:BB:BB:BB:BB:BB', 'Speaker', False, False)]
        self.password = 'hunter22'

    def connect(self, ssid, password=None):
        net = next((n for n in self.nearby if n.ssid == ssid), None)
        if net and (not net.secured or password == self.password):
            self.current = ssid
            return nc.Result(True)
        return nc.Result(False, 'wrong password')

    def patches(self):
        f = self
        return patch.multiple(
            nc, wifi_enabled=lambda: f.wifi_on,
            wifi_status=lambda: nc.WifiStatus(bool(f.current and f.wifi_on), f.current if f.wifi_on else None),
            wifi_scan=lambda: list(f.nearby), wifi_connect=f.connect,
            wifi_set_enabled=lambda on: (setattr(f, 'wifi_on', on), nc.Result(True))[1],
            wifi_forget=lambda ssid: (setattr(f, 'current', None), nc.Result(True))[1],
            bt_powered=lambda: f.bt_on,
            bt_status=lambda: nc.BtStatus(f.bt_on, [d.name for d in f.known if d.connected]),
            bt_known=lambda: list(f.known), bt_scan=lambda seconds=0: list(f.known) + list(f.new),
            bt_set_powered=lambda on: (setattr(f, 'bt_on', on), nc.Result(True))[1],
            bt_forget=lambda mac: nc.Result(True), bt_pair_connect=lambda mac: nc.Result(True))


class FakeUploadServer:
    def __init__(self, books, music, on_event, on_contacts, on_stopped, address=None, **kw):
        self.on_event, self.on_contacts, self.on_stopped = on_event, on_contacts, on_stopped
        self.code, self.port, self.running = '482193', 8080, False

    def start(self):
        self.running = True
        return self

    def stop(self):
        self.running = False


class PhoneCase(unittest.TestCase):
    """A whole pretend phone in a scratch folder."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        d = self.dir = os.path.realpath(self._tmp.name)
        self.books, self.music = os.path.join(d, 'books'), os.path.join(d, 'music')
        os.makedirs(self.books)
        os.makedirs(self.music)
        self.clock = Clock()
        self.contacts = []
        paths = {'DATA_DIR': d, 'MESSAGES_FILE': os.path.join(d, 'messages.json'),
                 'CALLS_FILE': os.path.join(d, 'calls.json'), 'SETTINGS_FILE': os.path.join(d, 'settings.json'),
                 'NOTES_FILE': os.path.join(d, 'notes.json'), '_contacts_path': os.path.join(d, 'contacts.json'),
                 'BOOKS_DIR': self.books, 'READING_FILE': os.path.join(d, 'reading.json'), 'MUSIC_DIR': self.music,
                 'MUSIC_INDEX_FILE': os.path.join(d, 'music_index.json'),
                 'LISTENING_FILE': os.path.join(d, 'listening.json'), 'CONTACTS': self.contacts,
                 '_MUSIC_CLOCK': self.clock, 'WIFI_ON_SETTLE': 0}
        for name, value in paths.items():
            p = patch.object(kyphone_os, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.modem = md.SimModem()
        self.radio_ok = True

        def transport(to, body):
            if not self.radio_ok:
                raise md.ModemError('no service')
            self.modem.send(kyphone_os.normalize_number(to), body)
        self.screens, self.pages = [], []
        for name, fn in (('push_screen', self._screen), ('push_page', self._page), ('_run_async', lambda fn: fn()),
                         ('_transport_send', transport)):
            p = patch.object(kyphone_os, name, side_effect=fn) if name.startswith('push') else \
                patch.object(kyphone_os, name, fn)
            p.start()
            self.addCleanup(p.stop)
        self.net = FakeNet()
        p = self.net.patches()
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(kyphone_os.upload_server, 'lan_address', return_value='192.168.1.23')
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(kyphone_os.upload_server, 'UploadServer', FakeUploadServer)
        p.start()
        self.addCleanup(p.stop)
        self.reset_state()
        self.addCleanup(self._close_everything)

    def reset_state(self):
        for k, v in BASE_STATE.items():
            kyphone_os.state[k] = copy.deepcopy(v)
        kyphone_os._music = None
        kyphone_os._music_problem = None
        kyphone_os._music_lengths.clear()
        kyphone_os._upload = None
        kyphone_os._modem = None

    def _close_everything(self):
        book = kyphone_os.state.get('book')
        if book is not None:
            book.close()
        if kyphone_os._music is not None:
            try:
                kyphone_os._music.stop()
            except Exception:
                pass
        self.reset_state()

    def _screen(self, cmd):
        self.check_frame(cmd)
        self.screens.append(cmd)
        ALL_COMMANDS.add(cmd)

    def _page(self, frames):
        for f in frames:
            self.check_frame(f)
        self.pages.append(list(frames))

    def check_frame(self, cmd):
        self.assertLessEqual(len(cmd), kyphone_os.MAX_COMMAND_CHARS, cmd[:60])
        self.assertTrue(all(' ' <= c <= '~' or c == SEP for c in cmd), cmd[:80])

    # ── helpers ──
    @property
    def st(self):
        return kyphone_os.state

    @property
    def wire(self):
        return self.screens[-1]

    def key(self, *keys):
        for k in keys:
            kyphone_os.handle_key(k)

    def type(self, text):
        for ch in text:
            kyphone_os.handle_key('CHAR:' + ch)

    def home(self, item):
        """From anywhere: wake, go home, open a home menu item."""
        for _ in range(8):
            if self.st['screen'] in ('home', 'lock'):
                break
            self.key('KEY_ESC')
        if self.st['screen'] == 'lock':
            self.key('KEY_ENTER')
        self.assertEqual(self.st['screen'], 'home')
        self.st['home_index'] = kyphone_os.HOME_MENU.index(item)
        self.key('KEY_ENTER')

    def restart(self):
        """What a reboot does: everything in memory is gone and read back from the data folder."""
        self.reset_state()
        self.contacts[:] = kyphone_os._load_contacts()
        kyphone_os.load_messages()
        kyphone_os.load_calls()
        kyphone_os.load_settings()
        kyphone_os.load_notes()

    def incoming(self, sender, body):
        """A text arrives through the modem (one pass of the real poll loop)."""
        self.modem.deliver(sender, body)
        kyphone_os._modem = self.modem
        self.st['running'] = True
        with patch.object(kyphone_os.time, 'sleep', lambda s: self.st.__setitem__('running', False)):
            kyphone_os.modem_sms_loop()
        kyphone_os._modem = None

    def add_book(self):
        chapters = [('c%d.xhtml' % k, ('<html><body><h1>Chapter %d</h1>%s</body></html>'
                     % (k + 1, ''.join('<p>Paragraph %d of chapter %d, with enough words to fill a line or two '
                                       'on the page.</p>' % (i, k + 1) for i in range(25)))).encode())
                    for k in range(3)]
        make_epub(os.path.join(self.books, 'moby.epub'), chapters, title='Moby Dick', author='Herman Melville',
                  toc={'c0.xhtml': 'Loomings', 'c1.xhtml': 'The Carpet-Bag', 'c2.xhtml': 'The Spouter-Inn'})

    def add_album(self):
        for n, name in enumerate(['So What', 'Freddie Freeloader', 'Blue in Green'], 1):
            path = os.path.join(self.music, 'Miles Davis', 'Kind of Blue', '%02d %s.wav' % (n, name))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'wb') as f:
                f.write(fx.wav_bytes(10, rate=800))

    def saved(self, name):
        with open(os.path.join(self.dir, name)) as f:
            return json.load(f)


class Scenarios(PhoneCase):
    def test_waking_and_visiting_every_home_item(self):
        kyphone_os.push_lock()
        self.assertTrue(self.wire.startswith('LOCK|'))
        self.key('KEY_ENTER')
        self.assertEqual(self.st['screen'], 'home')
        opened = {'TEXT': 'texts_list', 'CALL': 'calls_list', 'READ': 'library', 'LISTEN': 'music',
                  'CONTACTS': 'contacts_pick', 'NOTES': 'notes_list', 'SETTINGS': 'settings'}
        for item, screen in opened.items():
            self.home(item)
            self.assertEqual(self.st['screen'], screen, item)
            self.key('KEY_ESC')
            self.assertEqual(self.st['screen'], 'home', item)
        self.key('KEY_ESC')
        self.assertEqual(self.st['screen'], 'lock')

    def test_texting_a_new_number_then_replying_and_a_reply_arriving(self):
        self.home('TEXT')
        self.key('CHAR:+')                                        # a new message
        self.type('5550100042')
        self.key('KEY_ENTER')
        self.type('Lunch at noon?')
        self.key('KEY_ENTER')
        self.assertEqual(self.st['screen'], 'thread')
        self.assertEqual(self.modem.sent, [('+15550100042', 'Lunch at noon?')])
        self.assertIn('Y1' + SEP, self.wire)                     # SENT
        self.incoming('5550100042', 'Yes!\nSee you there')     # a reply, national format, two lines
        self.assertIn('Yes! See you there', self.wire)            # joins this conversation, redraws
        self.type('Great')
        self.key('KEY_ENTER')
        self.assertEqual(self.modem.sent[-1], ('+15550100042', 'Great'))
        self.key('KEY_ESC')
        self.assertEqual(self.st['screen'], 'texts_list')
        self.assertEqual(len(kyphone_os.get_threads()), 1)       # one conversation, not two

    def test_a_text_that_does_not_send_is_retried(self):
        self.radio_ok = False
        self.home('TEXT')
        self.key('CHAR:+')
        self.type('5550100043')
        self.key('KEY_ENTER')
        self.type('hello')
        self.key('KEY_ENTER')
        self.assertIn('Y2' + SEP, self.wire)                     # NOT SENT
        self.radio_ok = True
        self.key('KEY_UP')                                        # select the NOT SENT bubble
        self.assertIn('Y3' + SEP, self.wire)
        self.key('KEY_ENTER')                                     # retry
        self.assertIn('Y1' + SEP, self.wire)
        self.assertEqual(self.modem.sent, [('+15550100043', 'hello')])

    def test_a_text_arriving_while_locked_shows_the_mark_and_reading_clears_it(self):
        kyphone_os.push_lock()
        self.incoming('+15550100044', 'Are you around?')
        self.assertEqual(self.wire.split('|')[-2], '*')          # the lock screen redrew with the mark
        self.home('TEXT')
        self.key('KEY_ENTER')                                     # open the conversation (marks it read)
        self.assertEqual(self.st['screen'], 'thread')
        kyphone_os.push_lock()
        self.assertEqual(self.wire.split('|')[-2], '')

    def test_contacts_create_find_edit_and_delete(self):
        self.home('CONTACTS')
        self.key('CHAR:+')                                        # empty search: + is a new contact
        self.assertEqual(self.st['screen'], 'contact_edit')
        self.type('Pip')
        self.key('KEY_DOWN')
        self.type('Okonkwo')
        self.key('KEY_DOWN')
        self.type('5550100101')
        self.key('KEY_DOWN', 'KEY_ENTER')                         # SAVE
        self.assertEqual(self.contacts, [{'first': 'Pip', 'last': 'Okonkwo', 'number': '(555) 010-0101'}])
        self.assertEqual(self.saved('contacts.json')[0]['first'], 'Pip')
        self.home('CONTACTS')
        self.key('CHAR:+')                                        # a second one with the same number
        self.type('Dup')
        self.key('KEY_DOWN', 'KEY_DOWN')
        self.type('555-010-0101')
        self.key('KEY_DOWN', 'KEY_ENTER')
        self.assertEqual(self.st['screen'], 'stub')               # the duplicate is refused
        self.assertEqual(len(self.contacts), 1)
        self.home('CONTACTS')
        self.type('pi')                                           # look up by name
        self.assertIn('Pip Okonkwo', self.wire)
        self.key('KEY_ENTER')
        self.assertEqual(self.st['screen'], 'contact')
        self.key('KEY_UP', 'KEY_RIGHT', 'KEY_ENTER')              # EDIT
        self.assertEqual(self.st['screen'], 'contact_edit')
        self.key('KEY_DOWN', 'KEY_DOWN', 'KEY_DOWN', 'KEY_LEFT', 'KEY_ENTER')     # DELETE, asks first
        self.assertEqual(self.st['screen'], 'confirm')
        self.key('KEY_LEFT', 'KEY_ENTER')
        self.assertEqual(self.contacts, [])

    def test_a_call_is_logged_and_a_missed_call_leads_to_the_person_and_their_texts(self):
        self.home('CALL')
        self.key('KEY_ENTER')                                     # DIAL A NUMBER
        self.type('5550100102')
        self.key('KEY_ENTER')
        self.assertEqual(self.st['screen'], 'outgoing')
        self.key('KEY_ENTER')                                     # (demo) they answer
        self.assertEqual(self.st['screen'], 'in_call')
        self.key('KEY_ESC')                                       # hang up
        entry = self.st['calls'][0]
        self.assertEqual((entry['tag'], entry['number']), ('OUT', '(555) 010-0102'))
        self.contacts.append({'first': 'Ann', 'last': 'Lee', 'number': '(555) 010-0103'})
        self.home('CALL')
        self.key('KEY_ESC')
        self.key('CHAR:i')                                        # (demo) Ann calls
        self.assertEqual(self.st['screen'], 'incoming')
        self.key('CHAR:q')                                        # not answered
        kyphone_os.push_lock()
        self.assertEqual(self.wire.split('|')[-2], '*')          # a missed call marks the lock screen
        self.home('CALL')
        self.key('KEY_DOWN', 'KEY_ENTER')                         # the missed call → Ann's page
        self.assertEqual(self.st['screen'], 'contact')
        self.assertTrue(self.wire.startswith('CONTACT|Ann Lee|'))
        self.key('KEY_RIGHT', 'KEY_ENTER')                        # TEXT: never texted, so New Message to Ann
        self.assertEqual(self.st['screen'], 'compose')
        self.assertTrue(self.wire.startswith('COMPOSE|Ann Lee|'))
        self.type('Sorry I missed you')
        self.key('KEY_ENTER')
        self.assertEqual(self.st['screen'], 'thread')
        self.assertIn('LAST CALL: MISSED', self.wire)             # the conversation shows the call
        kyphone_os.push_lock()
        self.assertEqual(self.wire.split('|')[-2], '')           # seen

    def test_reading_turning_pages_size_chapters_and_resuming(self):
        self.add_book()
        self.home('READ')
        self.assertIn('Moby Dick', self.wire)
        self.key('KEY_ENTER')
        self.assertEqual(self.st['screen'], 'reader')
        start = self.st['r_offset']
        self.key('KEY_RIGHT', 'KEY_RIGHT')
        self.assertGreater(self.st['r_offset'], start)
        self.key('CHAR:+')
        self.assertEqual(self.st['r_size'], 'L')
        self.key('CHAR:c')                                        # the chapter menu
        self.assertEqual(self.st['screen'], 'chapters')
        self.assertIn('The Spouter-Inn', self.wire)
        self.key('KEY_DOWN', 'KEY_DOWN', 'KEY_ENTER')
        self.assertEqual((self.st['screen'], self.st['r_chapter'], self.st['r_offset']), ('reader', 2, 0))
        self.key('KEY_RIGHT')
        where = (self.st['r_chapter'], self.st['r_offset'], self.st['r_size'])
        self.key('CHAR:q')                                        # back to the library
        self.assertEqual(self.st['screen'], 'library')
        self.key('KEY_ENTER')                                     # reopen: the same place and size
        self.assertEqual((self.st['r_chapter'], self.st['r_offset'], self.st['r_size']), where)

    def test_music_plays_keeps_playing_in_the_background_and_resumes(self):
        self.add_album()
        self.home('LISTEN')
        self.assertIn('Kind of Blue', self.wire)
        self.key('KEY_ENTER', 'KEY_DOWN', 'KEY_ENTER')            # the album, then its second track
        self.assertEqual(self.st['screen'], 'nowplaying')
        self.assertIn('Freddie Freeloader', self.wire)
        self.assertTrue(self.wire.startswith('NOWPLAYING|P|'))
        self.key('KEY_RIGHT')                                     # next
        self.assertIn('Blue in Green', self.wire)
        self.key(' ' and 'CHAR: ')                                # pause
        self.assertTrue(self.wire.startswith('NOWPLAYING|U|'))
        self.key('CHAR: ')                                        # play again
        self.key('KEY_UP')                                        # louder
        self.key('KEY_ESC')                                       # leave: it keeps playing
        self.home('LISTEN')
        self.key('KEY_ESC')
        self.assertTrue(self.wire.startswith('HOME2|'))
        self.assertEqual(self.wire.split('|')[5], '1')            # the equalizer mark by LISTEN
        self.home('LISTEN')
        self.assertIn('NOW PLAYING', self.wire)

    def test_settings_wifi_bluetooth_light_and_the_mark(self):
        self.home('SETTINGS')
        self.assertIn('Connected: Maple', self.wire)
        self.key('KEY_ENTER')                                     # Wi-Fi
        self.assertEqual(self.st['screen'], 'wifi')
        self.assertIn('Birch_5G', self.wire)
        self.key('KEY_DOWN', 'KEY_DOWN', 'KEY_ENTER')             # Birch_5G is secured: the password box
        self.assertEqual(self.st['screen'], 'netpass')
        self.type('wrong')
        self.key('KEY_ENTER')
        self.assertTrue(self.wire.startswith('NETSTATE|W|FAIL|'))
        self.key('KEY_ENTER')                                     # back to the password, typing kept
        for _ in range(5):
            self.key('KEY_BACKSPACE')
        self.type('hunter22')
        self.key('KEY_ENTER')
        self.assertTrue(self.wire.startswith('NETSTATE|W|OK|'))
        self.key('KEY_ENTER')
        self.key('KEY_ESC')
        self.assertIn('Connected: Birch_5G', ''.join(self.screens[-2:]))
        self.key('KEY_DOWN', 'KEY_ENTER')                         # Bluetooth
        self.key('KEY_ENTER')                                     # switch off: refused (keyboard)
        self.assertTrue(self.wire.startswith('STUB|BLUETOOTH|BLUETOOTH STAYS ON'))
        self.key('KEY_ENTER')
        self.st['net_index'] = 3                                  # PAIR NEW DEVICE
        self.key('KEY_ENTER')
        self.assertEqual(self.st['screen'], 'btpair')
        self.key('KEY_ENTER')                                     # Speaker
        self.assertTrue(self.wire.startswith('NETSTATE|B|OK|Connected to Speaker'))
        self.key('KEY_ENTER', 'KEY_ESC')
        self.assertEqual(self.st['screen'], 'settings')
        self.st['settings_index'] = 2                             # Screen light
        self.key('KEY_ENTER', 'KEY_RIGHT', 'KEY_RIGHT', 'KEY_RIGHT', 'KEY_ENTER')
        self.assertEqual(self.saved('settings.json')['light'], 3)
        self.st['settings_index'] = 3                             # Activity mark off
        self.key('KEY_ENTER')
        self.assertFalse(self.saved('settings.json')['activity_mark'])

    def test_notes_write_edit_and_delete(self):
        self.home('NOTES')
        self.key('KEY_ENTER')                                     # + (the empty list opens on it)
        self.type('Groceries')
        self.key('KEY_ENTER')
        self.type('eggs')
        self.key('KEY_UP', 'KEY_ENTER')                           # < saves
        self.assertEqual(self.saved('notes.json')[0]['text'], 'Groceries\neggs')
        self.key('KEY_ENTER')                                     # reopen, add a line
        self.key('KEY_ENTER')
        self.type('milk')
        self.key('KEY_ESC')
        self.assertEqual(self.saved('notes.json')[0]['text'], 'Groceries\neggs\nmilk')
        self.key('KEY_ENTER', 'KEY_UP', 'KEY_RIGHT', 'KEY_ENTER', 'KEY_LEFT', 'KEY_ENTER')   # DELETE, confirmed
        self.assertEqual(self.saved('notes.json'), [])

    def test_adding_from_a_computer_and_importing_contacts(self):
        self.home('SETTINGS')
        self.st['settings_index'] = 4
        self.key('KEY_ENTER')
        self.assertEqual(self.wire, 'UPLOAD|192.168.1.23:8080|482193|')
        server = kyphone_os._upload
        server.on_event('Moby Dick.epub')
        summary = server.on_contacts([('Pip', 'Okonkwo', '5550100101'), ('Bad', '', '12')])
        server.on_event(summary)
        self.assertEqual(summary, 'Contacts: 1 added, 1 with no usable number')
        self.assertIn('Contacts: 1 added', self.wire)             # (each line is cut to fit the screen)
        self.assertEqual(self.contacts[0]['number'], '(555) 010-0101')
        self.key('CHAR:q')
        self.assertFalse(server.running)
        self.assertEqual(self.st['screen'], 'settings')

    def test_everything_survives_a_restart(self):
        self.contacts.append({'first': 'Pip', 'last': '', 'number': '(555) 010-0101'})
        kyphone_os._save_contacts(self.contacts)
        self.incoming('+15550100101', 'hello')
        self.home('NOTES')
        self.key('KEY_ENTER')
        self.type('remember this')
        self.key('KEY_ESC')
        self.home('SETTINGS')
        self.st['settings_index'] = 2
        self.key('KEY_ENTER', 'KEY_RIGHT', 'KEY_ENTER')
        self.home('CALL')
        self.key('KEY_ESC')
        self.key('CHAR:i', 'CHAR:q')                              # a missed call from Pip
        self.restart()
        self.assertEqual(self.contacts[0]['first'], 'Pip')
        self.assertEqual(self.st['messages'][-1]['body'], 'hello')
        self.assertEqual(self.st['notes'][0]['text'], 'remember this')
        self.assertEqual(self.st['light'], 1)
        self.assertEqual((self.st['calls'][0]['tag'], self.st['calls'][0]['number']), ('MISS', '(555) 010-0101'))
        kyphone_os.push_lock()
        self.assertEqual(self.wire.split('|')[-2:], ['*', '1'])  # the mark and the light came back too


KEYS = (['KEY_UP'] * 6 + ['KEY_DOWN'] * 6 + ['KEY_LEFT'] * 3 + ['KEY_RIGHT'] * 3 + ['KEY_ENTER'] * 6 +
        ['KEY_ESC'] * 3 + ['KEY_BACKSPACE'] * 2 + ['KEY_TAB'] +
        ['CHAR:' + c for c in 'qwasdciabz +-=.,5019@!'] + ['CHAR:+'] * 2)


class RandomKeys(PhoneCase):
    """Thousands of random keys from every starting point: nothing may raise, and every frame must be valid."""

    def run_keys(self, seed, steps):
        rng = random.Random(seed)
        visited = set()
        for i in range(steps):
            key = rng.choice(KEYS)
            try:
                kyphone_os.handle_key(key)
            except Exception as e:                                # report where it happened
                self.fail('seed %d step %d: %s on %s raised %r' % (seed, i, key, self.st['screen'], e))
            visited.add(self.st['screen'])
            self.clock.t += 0.5
            if i % 97 == 0:                                       # sometimes things happen by themselves
                self.incoming('+1555010%04d' % rng.randrange(10000), 'random text %d' % i)
            if i % 211 == 0 and self.st['screen'] == 'home':
                self.st['home_index'] = rng.randrange(len(kyphone_os.HOME_MENU))
        return visited

    def test_random_keys_reach_every_part_of_the_phone_without_a_crash(self):
        self.add_book()
        self.add_album()
        self.contacts.extend([{'first': 'Pip', 'last': 'Okonkwo', 'number': '(555) 010-0101'},
                              {'first': 'Ann', 'last': 'Lee', 'number': '(555) 010-0103'}])
        visited = set()
        for seed in range(16):
            self.reset_state()
            self.st['screen'] = 'home'
            if seed >= 8:                                         # half the runs start inside an app
                self.st['home_index'] = seed % len(kyphone_os.HOME_MENU)
                kyphone_os.handle_key('KEY_ENTER')
                if self.st['screen'] == 'settings' and seed % 2:
                    kyphone_os.handle_key('KEY_DOWN')             # sometimes on the Bluetooth row
            visited |= self.run_keys(seed, 700)
        must = {'home', 'texts_list', 'thread', 'compose', 'calls_list', 'dial', 'library', 'reader', 'music',
                'tracks', 'nowplaying', 'contacts_pick', 'contact', 'contact_edit', 'notes_list', 'note',
                'settings', 'wifi', 'bluetooth'}
        self.assertTrue(must <= visited, 'never reached: %s' % sorted(must - visited))


# ── everything sent is drawable by the emulator and by the firmware ──────────────────────────────────────────

def _host_build():
    sys.path.insert(0, HERE)
    import test_firmware_host as fh
    return fh


class RenderEverythingSent(unittest.TestCase):
    """Runs last (by name): every command the scenarios and the random keys sent, drawn by both renderers."""

    def test_zz_the_emulator_draws_everything_that_was_sent(self):
        try:
            import pygame
            if isinstance(pygame, MagicMock):
                raise ImportError
            os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
            import simulator
        except ImportError:
            self.skipTest('needs real pygame')
        if not ALL_COMMANDS:
            self.skipTest('run with the rest of this file')
        sim = simulator.Simulator(lambda k: None)
        sim.init()
        for cmd in sorted(ALL_COMMANDS):
            sim._surface.fill((255, 255, 255))
            sim._draw(cmd)

    def test_zz_the_firmware_draws_everything_that_was_sent_under_the_sanitizers(self):
        fh = _host_build()
        if not fh.AVAILABLE:
            self.skipTest('needs clang++ and Adafruit_GFX')
        if not ALL_COMMANDS:
            self.skipTest('run with the rest of this file')
        tmp = tempfile.mkdtemp(prefix='kyphone-scen-')
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        exe = os.path.join(tmp, 'render_host_asan')
        fh.build(exe, sanitize=True)
        with open(os.path.join(HERE, '..', 'Inkplate_SPI_Peripheral', 'ui_screens.h')) as f:
            owned = tuple(re.findall(r'\{"([A-Z0-9]+\|)", ui_', f.read()))   # the screens ui_screens.h draws
        self.assertIn('NOTES|', owned)
        mine = sorted(c for c in ALL_COMMANDS if c.startswith(owned))    # (LOCK, DIAL, CALLSTATE live in the .ino)
        self.assertGreater(len(mine), 50)
        lines = ''.join('s%d\t%s\n' % (i, c) for i, c in enumerate(mine))
        env = dict(os.environ, ASAN_OPTIONS='halt_on_error=1:detect_leaks=0', UBSAN_OPTIONS='halt_on_error=1')
        done = subprocess.run([exe, '-'], input=lines.encode('latin-1'), capture_output=True, env=env, timeout=600)
        err = done.stderr.decode('latin-1')
        self.assertEqual(done.returncode, 0, err[:1200])
        self.assertNotIn('AddressSanitizer', err)
        self.assertNotIn('runtime error', err)


if __name__ == '__main__':
    unittest.main()
