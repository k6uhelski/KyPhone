"""
test_navigation.py — every screen's keys, checked against the spec (CLAUDE.md, "Behaviour, by screen").

ROUTES reaches every screen from the home menu with real key presses, over a phone with something in every app
(contacts, two conversations, a book, an album, a note, a call). MAP is the navigation table: from a screen, these
keys, this screen, and what it should feel like on the phone. The same table is the phone checklist
(tools/make_phone_checklist.py), so the tests and the hand checks never drift apart. Then every screen is put
through the coloured buttons, auto-lock and Esc (no screen is a dead end), and every list through its edges:
empty, one row, and hundreds.

    ~/.venvs/kyphone/bin/python spi_bridge/tools/run_tests.py -k navigation
"""

import collections
import os
import re
import sys
from datetime import datetime
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_scenarios import PhoneCase  # noqa: E402  (sets up the pretend phone, as the scenarios do)
import kyphone_os  # noqa: E402

ANN, PIP, UNSAVED = '+15550100103', '+15550100101', '+15550199002'
DIAL_PIP = ['CHAR:' + c for c in '5550100101']


def to_net(name):
    """A route step: ↓ through a Wi-Fi / Bluetooth list until the row called `name` is selected."""
    def step(t):
        for _ in range(12):
            entries = kyphone_os._net_entries(*kyphone_os._net_snapshot()[:5])
            idx = t.st['net_index']
            if 0 <= idx < len(entries) and entries[idx][2][0] == name:
                return
            t.key('KEY_DOWN')
        t.fail('no row called %s' % name)
    return step


# name: (home menu item to open, or None for the home menu itself; then the keys, or steps, from there)
ROUTES = {
    'home':                   (None, []),
    'lock':                   (None, ['KEY_ESC']),
    'incoming':               (None, ['CHAR:i']),
    'texts_list':             ('TEXT', []),
    'thread':                 ('TEXT', ['KEY_ENTER']),                               # Ann's, the newest
    'thread_unsaved':         ('TEXT', ['KEY_DOWN', 'KEY_ENTER']),                   # a number not in contacts
    'message':                ('TEXT', ['KEY_ENTER', 'KEY_UP', 'KEY_ENTER']),
    'stub':                   ('TEXT', ['KEY_ENTER', 'KEY_ENTER']),                  # sending nothing
    'compose':                ('TEXT', ['CHAR:+']),
    'confirm_discard':        ('TEXT', ['CHAR:+', 'CHAR:5', 'KEY_ESC']),
    'contacts_pick':          ('CONTACTS', []),
    'contact_saved':          ('CONTACTS', ['KEY_ENTER']),                           # Ann Lee
    'contact_nonumber':       ('CONTACTS', ['KEY_DOWN', 'KEY_DOWN', 'KEY_ENTER']),   # Zed, no number
    'contact_unsaved':        ('TEXT', ['KEY_DOWN', 'KEY_ENTER', 'KEY_UP', 'KEY_RIGHT', 'KEY_ENTER']),
    'contact_edit':           ('CONTACTS', ['KEY_ENTER', 'KEY_UP', 'KEY_RIGHT', 'KEY_ENTER']),
    'confirm_delete_contact': ('CONTACTS', ['KEY_ENTER', 'KEY_UP', 'KEY_RIGHT', 'KEY_ENTER',
                                            'KEY_DOWN', 'KEY_DOWN', 'KEY_DOWN', 'KEY_LEFT', 'KEY_ENTER']),
    'calls_list':             ('CALL', []),
    'dial':                   ('CALL', ['KEY_ENTER']),
    'outgoing':               ('CALL', ['KEY_ENTER'] + DIAL_PIP + ['KEY_ENTER']),
    'in_call':                ('CALL', ['KEY_ENTER'] + DIAL_PIP + ['KEY_ENTER', 'KEY_ENTER']),
    'library':                ('READ', []),
    'reader':                 ('READ', ['KEY_ENTER']),
    'chapters':               ('READ', ['KEY_ENTER', 'CHAR:c']),
    'music':                  ('LISTEN', []),
    'tracks':                 ('LISTEN', ['KEY_ENTER']),
    'nowplaying':             ('LISTEN', ['KEY_ENTER', 'KEY_ENTER']),
    'notes_list':             ('NOTES', []),
    'note':                   ('NOTES', ['KEY_ENTER']),
    'confirm_delete_note':    ('NOTES', ['KEY_ENTER', 'KEY_UP', 'KEY_RIGHT', 'KEY_ENTER']),
    'settings':               ('SETTINGS', []),
    'light':                  ('SETTINGS', ['KEY_DOWN', 'KEY_DOWN', 'KEY_ENTER']),
    'upload':                 ('SETTINGS', ['KEY_DOWN'] * 4 + ['KEY_ENTER']),
    'wifi':                   ('SETTINGS', ['KEY_ENTER']),
    'confirm_forget_wifi':    ('SETTINGS', ['KEY_ENTER', 'KEY_ENTER']),              # opens on the joined network
    'netpass':                ('SETTINGS', ['KEY_ENTER', to_net('Birch_5G'), 'KEY_ENTER']),
    'netstate':               ('SETTINGS', ['KEY_ENTER', to_net('Birch_5G'), 'KEY_ENTER', 'CHAR:x', 'KEY_ENTER']),
    'bluetooth':              ('SETTINGS', ['KEY_DOWN', 'KEY_ENTER']),
    'confirm_bt_device':      ('SETTINGS', ['KEY_DOWN', 'KEY_ENTER', to_net('Headphones'), 'KEY_ENTER']),
    'btpair':                 ('SETTINGS', ['KEY_DOWN', 'KEY_ENTER', to_net('PAIR NEW DEVICE'), 'KEY_ENTER']),
}


def screen_of(route):
    """The state['screen'] a route ends on (a route name is the screen, or the screen plus what kind of it)."""
    for screen in ('confirm', 'contact_edit', 'contact', 'thread'):
        if route.startswith(screen + '_') and route != 'contact_edit':
            return screen
    return route


def is_(field, value):
    return lambda t: t.assertEqual(t.st[field], value, field)


def wire_starts(prefix):
    return lambda t: t.assertTrue(t.wire.startswith(prefix), t.wire[:60])


Row = collections.namedtuple('Row', 'start keys expect says check')


def row(start, keys, expect, says, check=None):
    return Row(start, keys if isinstance(keys, list) else [keys], expect, says, check)


# The navigation table. `says` is written for a person holding the phone: it is the phone checklist.
MAP = [
    # ── lock and home ──
    row('lock', 'KEY_DOWN', 'home', 'Locked by hand (Esc on home): any key opens the home menu, on TEXT',
        is_('home_index', 0)),
    row('home', 'KEY_ESC', 'lock', 'Esc on the home menu locks the phone'),
    row('home', 'CHAR:q', 'lock', 'Q on the home menu locks the phone'),
    row('home', 'KEY_UP', 'home', 'Up on TEXT stays on TEXT (the status bar is never selected)', is_('home_index', 0)),
    row('home', ['KEY_DOWN'] * 9, 'home', 'Down stops at SETTINGS, the last row', is_('home_index', 6)),
    row('home', ['KEY_RIGHT', 'KEY_LEFT'], 'home', 'Right and Left move like Down and Up', is_('home_index', 0)),
    row('home', ['KEY_DOWN', 'KEY_ENTER'], 'calls_list', 'Enter on CALL opens the call list'),
    row('home', ['KEY_DOWN'] * 2 + ['KEY_ENTER'], 'library', 'Enter on READ opens the books'),
    row('home', ['KEY_DOWN'] * 3 + ['KEY_ENTER'], 'music', 'Enter on LISTEN opens the albums'),
    row('home', ['KEY_DOWN'] * 4 + ['KEY_ENTER'], 'contacts_pick', 'Enter on CONTACTS opens the address book'),
    row('home', ['KEY_DOWN'] * 5 + ['KEY_ENTER'], 'notes_list', 'Enter on NOTES opens the notes'),
    row('home', ['KEY_DOWN'] * 6 + ['KEY_ENTER'], 'settings', 'Enter on SETTINGS opens Settings'),
    row('home', 'KEY_ENTER', 'texts_list', 'Enter on TEXT opens the texts'),

    # ── texts ──
    row('texts_list', 'KEY_ESC', 'home', 'Esc on the texts goes home, still on TEXT', is_('home_index', 0)),
    row('texts_list', 'CHAR:q', 'home', 'Q on the texts goes home'),
    row('texts_list', 'KEY_BACKSPACE', 'home', 'Backspace on the texts goes home'),
    row('texts_list', 'KEY_ENTER', 'thread', 'Enter on a conversation opens it'),
    row('texts_list', 'CHAR:+', 'compose', 'The + key opens New Message'),
    row('texts_list', 'KEY_UP', 'texts_list', 'Up from the first conversation selects the header back <',
        is_('texts_index', -1)),
    row('texts_list', ['KEY_UP', 'KEY_ENTER'], 'home', 'Enter on the header < goes home'),
    row('texts_list', ['KEY_UP', 'KEY_RIGHT', 'KEY_ENTER'], 'compose', 'Enter on the header + opens New Message'),
    row('texts_list', ['KEY_UP', 'KEY_DOWN'], 'texts_list', 'Down from the header returns to the first conversation',
        is_('texts_index', 0)),
    row('texts_list', ['KEY_DOWN'] * 5, 'texts_list', 'Down stops at the last conversation', is_('texts_index', 1)),

    row('thread', 'KEY_ESC', 'texts_list', 'Esc in a conversation goes back to the texts'),
    row('thread', 'CHAR:q', 'thread', 'Q in a conversation is a letter: it types q', is_('thread_draft', 'q')),
    row('thread', 'KEY_UP', 'thread', 'Up from the composer selects the newest message', is_('thread_msg_sel', 0)),
    row('thread', ['KEY_UP', 'KEY_UP'], 'thread', 'Up from the oldest message selects the header back <',
        is_('thread_header_sel', 'back')),
    row('thread', ['KEY_UP', 'KEY_RIGHT'], 'thread', 'Right from a selected message jumps to the header info i',
        is_('thread_header_sel', 'info')),
    row('thread', ['KEY_UP', 'KEY_LEFT'], 'thread', 'Left from a selected message jumps to the header back <',
        is_('thread_header_sel', 'back')),
    row('thread', ['KEY_UP', 'KEY_UP', 'KEY_ENTER'], 'texts_list', 'Enter on the header < goes back to the texts'),
    row('thread', ['KEY_UP', 'KEY_RIGHT', 'KEY_ENTER'], 'contact', 'Enter on the header i opens the person'),
    row('thread', ['KEY_UP', 'KEY_UP', 'KEY_DOWN', 'KEY_DOWN'], 'thread',
        'Down from the header walks back down to the composer', is_('thread_msg_sel', -1)),
    row('thread', ['KEY_UP', 'KEY_ENTER'], 'message', 'Enter on a selected message opens it full screen'),
    row('thread', ['KEY_UP', 'CHAR:h'], 'thread', 'Typing with a message selected returns to the composer and types',
        is_('thread_draft', 'h')),
    row('thread_unsaved', ['KEY_UP', 'KEY_RIGHT', 'KEY_ENTER'], 'contact',
        "A stranger's conversation: i opens their number as NOT IN CONTACTS", is_('contact_idx', None)),
    row('thread_unsaved', 'KEY_ESC', 'texts_list', "Esc in a stranger's conversation goes back to the texts"),
    row('thread', 'KEY_ENTER', 'stub','Enter with nothing typed says there is nothing to send', wire_starts('STUB|NEW')),
    row('stub', 'KEY_ENTER', 'thread', 'Enter on that alert returns to the conversation'),
    row('stub', 'KEY_ESC', 'thread', 'Esc on that alert returns to the conversation too'),

    row('message', 'KEY_ESC', 'thread', 'Esc on a full-screen message returns with that message still selected',
        is_('thread_msg_sel', 0)),
    row('message', 'KEY_ENTER', 'thread', 'Enter on a full-screen message returns to the conversation'),
    row('message', 'KEY_BACKSPACE', 'thread', 'Backspace on a full-screen message returns to the conversation'),
    row('message', 'KEY_DOWN', 'message', 'Down on a one-page message stays (it redraws)', is_('message_page', 0)),

    row('compose', 'KEY_ESC', 'texts_list', 'Esc on an empty New Message goes back to the texts without asking'),
    row('compose', 'KEY_UP', 'compose', 'Up from TO selects the header X', is_('compose_header_sel', 'x')),
    row('compose', ['KEY_UP', 'KEY_DOWN'], 'compose', 'Down from the X returns to TO', is_('compose_header_sel', None)),
    row('compose', ['KEY_UP', 'KEY_ENTER'], 'texts_list', 'Enter on the X of an empty message leaves'),
    row('compose', 'KEY_ENTER', 'contacts_pick', 'Enter on an empty TO opens the contact picker'),
    row('compose', ['KEY_RIGHT', 'KEY_ENTER'], 'contacts_pick', 'Right from an empty TO selects +, Enter opens the picker'),
    row('compose', ['KEY_RIGHT', 'KEY_LEFT'], 'compose', 'Left from + returns to TO', is_('compose_plus_sel', False)),
    row('compose', 'CHAR:q', 'compose', 'Q in New Message is a letter', is_('compose_to', 'q')),
    row('compose', DIAL_PIP + ['KEY_ENTER'], 'compose', 'Enter on a filled TO moves to MESSAGE',
        is_('compose_to_active', False)),
    row('compose', DIAL_PIP + ['KEY_ENTER', 'KEY_UP'], 'compose', 'Up from MESSAGE returns to TO',
        is_('compose_to_active', True)),
    row('compose', DIAL_PIP + ['KEY_ENTER', 'KEY_DOWN', 'KEY_UP'], 'compose', 'Down to SEND, Up back to MESSAGE',
        is_('compose_send_sel', False)),
    row('compose', DIAL_PIP + ['KEY_ENTER', 'KEY_ENTER'], 'stub', 'Enter on an empty MESSAGE says there is nothing to send',
        wire_starts('STUB|NEW MESSAGE|THERE IS NOTHING')),
    row('compose', ['KEY_TAB', 'CHAR:h', 'KEY_ENTER'], 'stub', 'Sending with no TO says there is no one to send to',
        wire_starts('STUB|NEW MESSAGE|THERE IS NO ONE')),
    row('compose', ['CHAR:1', 'CHAR:2', 'KEY_TAB', 'CHAR:h', 'KEY_ENTER', 'KEY_ENTER'], 'compose',
        'A number that cannot be texted: after the alert the cursor is back on TO, the message kept',
        lambda t: t.assertEqual((t.st['compose_to_active'], t.st['compose_msg']), (True, 'h'))),
    row('compose', DIAL_PIP + ['KEY_ENTER', 'CHAR:h', 'KEY_ENTER'], 'thread', 'Enter on a written message sends it'),
    row('compose', ['CHAR:5', 'KEY_ESC'], 'confirm', 'Esc with something typed asks before discarding',
        is_('confirm_sel', 'keep')),

    row('confirm_discard', 'KEY_ESC', 'compose', 'Esc on Discard? keeps editing, nothing lost', is_('compose_to', '5')),
    row('confirm_discard', 'KEY_ENTER', 'compose', 'Enter on KEEP EDITING keeps editing', is_('compose_to', '5')),
    row('confirm_discard', ['KEY_LEFT', 'KEY_RIGHT', 'KEY_ENTER'], 'compose', 'Left then Right returns to the safe button'),
    row('confirm_discard', ['KEY_LEFT', 'KEY_ENTER'], 'texts_list', 'Left, Enter on DISCARD throws it away',
        is_('compose_to', '')),

    # ── contacts ──
    row('contacts_pick', 'KEY_ESC', 'home', 'Esc on the address book goes home, on CONTACTS', is_('home_index', 4)),
    row('contacts_pick', 'CHAR:q', 'contacts_pick', 'Q in the address book is a letter (it searches)',
        is_('contacts_query', 'q')),
    row('contacts_pick', 'KEY_UP', 'contacts_pick', 'Up from the first contact selects the header back <',
        is_('contacts_index', -1)),
    row('contacts_pick', ['KEY_UP', 'KEY_ENTER'], 'home', 'Enter on the header < goes home'),
    row('contacts_pick', ['KEY_UP', 'KEY_RIGHT', 'KEY_ENTER'], 'contact_edit', 'Enter on the header + starts a new contact'),
    row('contacts_pick', 'CHAR:+', 'contact_edit', 'The + key with nothing typed starts a new contact'),
    row('contacts_pick', ['CHAR:p', 'CHAR:+'], 'contacts_pick', 'With letters typed, + is just a character',
        is_('contacts_query', 'p+')),
    row('contacts_pick', ['CHAR:p', 'KEY_ENTER'], 'contact', 'Typing filters by name; Enter opens the match',
        lambda t: t.assertTrue(t.wire.startswith('CONTACT|Pip Okonkwo'), t.wire)),
    row('contacts_pick', ['CHAR:z', 'CHAR:z', 'KEY_BACKSPACE', 'KEY_BACKSPACE'], 'contacts_pick',
        'Backspace clears the search', is_('contacts_query', '')),
    row('contacts_pick', ['KEY_DOWN'] * 9, 'contacts_pick', 'Down stops at the last contact', is_('contacts_index', 2)),

    row('contact_saved', 'KEY_ESC', 'contacts_pick', 'Esc on a person goes back to the address book'),
    row('contact_saved', [], 'contact', 'A saved person opens on CALL', is_('contact_sel', 'call')),
    row('contact_saved', ['KEY_UP', 'KEY_ENTER'], 'contacts_pick', 'Up selects < ; Enter goes back'),
    row('contact_saved', ['KEY_UP', 'KEY_RIGHT', 'KEY_ENTER'], 'contact_edit', 'Up, Right selects EDIT; Enter opens the form'),
    row('contact_saved', 'KEY_ENTER', 'outgoing', 'Enter on CALL calls them'),
    row('contact_saved', ['KEY_RIGHT', 'KEY_ENTER'], 'thread', 'TEXT opens the conversation with them',
        is_('thread_id', ANN)),
    row('contact_saved', ['KEY_RIGHT', 'KEY_RIGHT'], 'contact', 'Right stops at TEXT', is_('contact_sel', 'text')),
    row('contact_nonumber', [], 'contact', 'A person with no number opens on ADD NUMBER', is_('contact_sel', 'addnum')),
    row('contact_nonumber', 'KEY_ENTER', 'contact_edit', 'Enter on ADD NUMBER opens the form'),
    row('contact_unsaved', 'KEY_ESC', 'thread', 'Esc on an unsaved number goes back to its conversation'),
    row('contact_unsaved', 'KEY_DOWN', 'contact', 'Down from CALL reaches CREATE CONTACT', is_('contact_sel', 'save')),
    row('contact_unsaved', ['KEY_DOWN', 'KEY_UP'], 'contact', 'Up from CREATE CONTACT returns to CALL',
        is_('contact_sel', 'call')),
    row('contact_unsaved', ['KEY_RIGHT', 'KEY_RIGHT'], 'contact', 'Right steps CALL, TEXT, CREATE CONTACT',
        is_('contact_sel', 'save')),
    row('contact_unsaved', ['KEY_DOWN', 'KEY_ENTER'], 'contact_edit',
        'CREATE CONTACT opens the form with the number filled in and locked', is_('edit_number_locked', True)),

    row('contact_edit', 'KEY_ESC', 'contact', 'Esc on the form goes back without saving'),
    row('contact_edit', 'CHAR:q', 'contact_edit', 'Q on the form is a letter', is_('edit_first', 'Annq')),
    row('contact_edit', ['KEY_UP', 'KEY_ENTER'], 'contact', 'Up from FIRST selects the header X; Enter leaves'),
    row('contact_edit', ['KEY_DOWN'] * 6, 'contact_edit', 'Down stops at SAVE', is_('edit_index', 3)),
    row('contact_edit', ['KEY_DOWN'] * 3 + ['KEY_LEFT', 'KEY_RIGHT'], 'contact_edit', 'Left from SAVE reaches DELETE, Right returns',
        is_('edit_index', 3)),
    row('contact_edit', ['KEY_BACKSPACE'] * 3 + ['KEY_DOWN'] * 3 + ['KEY_ENTER'], 'stub',
        'SAVE with no first name says one is needed', wire_starts('STUB|CONTACT|A CONTACT NEEDS A FIRST')),
    row('contact_edit', ['KEY_BACKSPACE'] * 3 + ['KEY_DOWN'] * 3 + ['KEY_ENTER', 'KEY_ENTER'], 'contact_edit',
        'After that alert, FIRST is selected', is_('edit_index', 0)),
    row('contact_edit', ['KEY_DOWN', 'KEY_DOWN'] + ['KEY_BACKSPACE'] * 14 + DIAL_PIP + ['KEY_DOWN', 'KEY_ENTER'], 'stub',
        "A number already saved is refused, naming who has it", wire_starts('STUB|CONTACT|THAT NUMBER IS ALREADY SAVED AS PIP')),
    row('contact_edit', ['KEY_DOWN', 'KEY_DOWN', 'CHAR:x'], 'contact_edit', 'Letters cannot be typed into the number',
        is_('edit_number', '(555) 010-0103')),
    row('confirm_delete_contact', [], 'confirm', 'DELETE asks first, on KEEP CONTACT', is_('confirm_sel', 'keep')),
    row('confirm_delete_contact', 'KEY_ESC', 'contact_edit', 'Esc keeps the contact'),
    row('confirm_delete_contact', 'KEY_ENTER', 'contact_edit', 'Enter on KEEP CONTACT keeps it'),
    row('confirm_delete_contact', ['KEY_LEFT', 'KEY_ENTER'], 'contacts_pick', 'Left, Enter on DELETE removes it',
        lambda t: t.assertEqual([c['first'] for c in t.contacts], ['Pip', 'Zed'])),

    # ── calls ──
    row('calls_list', 'KEY_ESC', 'home', 'Esc on the call list goes home, on CALL', is_('home_index', 1)),
    row('calls_list', 'KEY_BACKSPACE', 'home', 'Backspace on the call list goes home'),
    row('calls_list', 'KEY_ENTER', 'dial', 'Enter on DIAL A NUMBER opens the dialer'),
    row('calls_list', ['KEY_DOWN', 'KEY_ENTER'], 'contact', "Enter on a logged call opens that person's page",
        is_('contact_return', 'calls_list')),
    row('calls_list', ['KEY_DOWN', 'KEY_ENTER', 'KEY_ESC'], 'calls_list', 'Esc from that page returns to the call list'),
    row('calls_list', ['KEY_UP', 'KEY_ENTER'], 'home', 'Up to the header, Enter goes home'),
    row('calls_list', ['KEY_DOWN'] * 5, 'calls_list', 'Down stops at the last call', is_('calls_index', 1)),
    row('dial', 'KEY_ESC', 'calls_list', 'Esc on the dialer goes back to the call list'),
    row('dial', 'KEY_ENTER', 'dial', 'Enter with nothing dialed does nothing'),
    row('dial', ['CHAR:x', 'CHAR:5'], 'dial', 'Only digits, # and * can be dialed', is_('dial_buffer', '5')),
    row('dial', ['KEY_DOWN', 'KEY_ENTER'], 'outgoing', 'Down selects a quick-dial contact; Enter calls them',
        is_('call_name', 'Ann Lee')),
    row('outgoing', 'KEY_ESC', 'calls_list', 'Esc while ringing cancels; the call is logged with no length',
        lambda t: t.assertEqual((t.st['calls'][0]['tag'], t.st['calls'][0]['duration']), ('OUT', ''))),
    row('outgoing', 'KEY_ENTER', 'in_call', '(demo) Enter while ringing: they answer'),
    row('outgoing', ['KEY_HOME', 'KEY_LOCK', 'KEY_DOWN'], 'outgoing', 'The coloured buttons do nothing during a call'),
    row('in_call', 'KEY_ESC', 'calls_list', 'Esc hangs up; the call is logged with its length',
        lambda t: t.assertRegex(t.st['calls'][0]['duration'], r'^\d+:\d\d$')),
    row('in_call', ['KEY_DOWN', 'KEY_ENTER', 'CHAR:q'], 'calls_list', 'Q hangs up too; other keys do nothing'),
    row('incoming', 'KEY_ESC', 'home', 'Esc on an incoming call: missed, logged as MISS',
        lambda t: t.assertEqual(t.st['calls'][0]['tag'], 'MISS')),
    row('incoming', 'KEY_ENTER', 'in_call', 'Enter answers'),
    row('incoming', ['KEY_ENTER', 'KEY_ESC'], 'calls_list', 'Hanging up an answered call logs it as IN',
        lambda t: t.assertEqual(t.st['calls'][0]['tag'], 'IN')),

    # ── books ──
    row('library', 'KEY_ESC', 'home', 'Esc on the books goes home, on READ', is_('home_index', 2)),
    row('library', 'KEY_ENTER', 'reader', 'Enter opens a book'),
    row('library', ['KEY_UP', 'KEY_ENTER'], 'home', 'Up to the header, Enter goes home'),
    row('reader', 'KEY_ESC', 'library', 'Esc in a book goes back to the books, on that book', is_('library_index', 0)),
    row('reader', 'CHAR:q', 'library', 'Q in a book goes back to the books'),
    row('reader', 'CHAR:c', 'chapters', 'C opens the chapters'),
    row('reader', 'KEY_LEFT', 'stub', 'Back on the first page says it is the first page', wire_starts('STUB|READ|THIS IS THE FIRST')),
    row('reader', ['KEY_LEFT', 'KEY_ENTER'], 'reader', 'Enter on that alert returns to the page'),
    row('reader', ['CHAR:+', 'CHAR:+', 'CHAR:+'], 'stub', 'Bigger past the largest size says so',
        wire_starts('STUB|READ|THE TEXT IS ALREADY AT ITS LARGEST')),
    row('reader', ['CHAR:-', 'CHAR:-'], 'stub', 'Smaller past the smallest size says so',
        wire_starts('STUB|READ|THE TEXT IS ALREADY AT ITS SMALLEST')),
    row('chapters', 'KEY_ESC', 'reader', 'Esc on the chapters returns to the same page'),
    row('chapters', [], 'chapters', 'The chapter being read is selected (HERE)', is_('toc_index', 0)),
    row('chapters', ['KEY_UP', 'KEY_ENTER'], 'reader', 'Up to the header, Enter returns to the page'),
    row('chapters', ['KEY_DOWN', 'KEY_ENTER'], 'reader', 'Enter on a chapter jumps to its start', is_('r_chapter', 1)),
    row('chapters', ['KEY_DOWN'] * 9, 'chapters', 'Down stops at the last chapter', is_('toc_index', 2)),

    # ── music ──
    row('music', 'KEY_ESC', 'home', 'Esc on the albums goes home, on LISTEN', is_('home_index', 3)),
    row('music', 'KEY_ENTER', 'tracks', 'Enter opens an album'),
    row('music', ['KEY_UP', 'KEY_ENTER'], 'home', 'Up to the header, Enter goes home'),
    row('tracks', 'KEY_ESC', 'music', 'Esc on an album goes back to the albums'),
    row('tracks', ['KEY_UP', 'KEY_ENTER'], 'music', 'Up to the header, Enter goes back to the albums'),
    row('tracks', ['KEY_DOWN', 'KEY_ENTER'], 'nowplaying', 'Enter plays the album from that track',
        wire_starts('NOWPLAYING|P|Freddie')),
    row('tracks', ['KEY_DOWN'] * 9, 'tracks', 'Down stops at the last track', is_('tracks_index', 2)),
    row('nowplaying', 'KEY_ESC', 'tracks', 'Esc on now playing goes back to the album; the music goes on',
        lambda t: t.assertTrue(kyphone_os._music.playing)),
    row('nowplaying', ['KEY_ESC', 'KEY_ESC', 'KEY_ENTER'], 'nowplaying', 'NOW PLAYING leads the albums; Enter shows it'),
    row('nowplaying', ['KEY_ESC', 'KEY_ESC', 'KEY_ENTER', 'KEY_ESC'], 'music',
        'Opened from the albums, Esc on now playing goes back to the albums'),
    row('nowplaying', 'KEY_ENTER', 'nowplaying', 'Enter pauses', wire_starts('NOWPLAYING|U|')),
    row('nowplaying', 'KEY_RIGHT', 'nowplaying', 'Right plays the next track', wire_starts('NOWPLAYING|P|Freddie')),
    row('nowplaying', ['KEY_RIGHT'] * 4, 'nowplaying', 'Right at the last track redraws and stays', wire_starts('NOWPLAYING|P|Blue')),

    # ── notes ──
    row('notes_list', 'KEY_ESC', 'home', 'Esc on the notes goes home, on NOTES', is_('home_index', 5)),
    row('notes_list', 'KEY_ENTER', 'note', 'Enter opens a note'),
    row('notes_list', 'CHAR:+', 'note', 'The + key starts a new note', is_('note_idx', None)),
    row('notes_list', ['KEY_UP', 'KEY_ENTER'], 'home', 'Up to the header <, Enter goes home'),
    row('notes_list', ['KEY_UP', 'KEY_RIGHT', 'KEY_ENTER'], 'note', 'Header +, Enter starts a new note'),
    row('note', 'KEY_ESC', 'notes_list', 'Esc on a note saves it and goes back'),
    row('note', 'CHAR:q', 'note', 'Q in a note is a letter', is_('note_text', 'first noteq')),
    row('note', 'KEY_ENTER', 'note', 'Enter starts a new line', is_('note_text', 'first note\n')),
    row('note', 'KEY_UP', 'note', 'Up selects SAVE in the header', is_('note_hdr', 'save')),
    row('note', ['KEY_UP', 'KEY_LEFT', 'KEY_ENTER'], 'notes_list', 'Left to <, Enter saves and goes back'),
    row('note', ['KEY_UP', 'KEY_ENTER'], 'notes_list', 'Enter on SAVE saves and goes back'),
    row('note', ['KEY_UP', 'KEY_DOWN', 'CHAR:!'], 'note', 'Down from the header returns to the text',
        is_('note_text', 'first note!')),
    row('confirm_delete_note', [], 'confirm', 'DELETE asks first, on KEEP NOTE', is_('confirm_sel', 'keep')),
    row('confirm_delete_note', 'KEY_ESC', 'note', 'Esc keeps the note'),
    row('confirm_delete_note', ['KEY_LEFT', 'KEY_ENTER'], 'notes_list', 'Left, Enter on DELETE removes it',
        is_('notes', [])),

    # ── settings ──
    row('settings', 'KEY_ESC', 'home', 'Esc on Settings goes home, on SETTINGS', is_('home_index', 6)),
    row('settings', ['KEY_UP', 'KEY_ENTER'], 'home', 'Up to the header, Enter goes home'),
    row('settings', ['KEY_DOWN'] * 9, 'settings', 'Down stops at Add from a computer', is_('settings_index', 4)),
    row('settings', 'KEY_ENTER', 'wifi', 'Enter on Wi-Fi opens the Wi-Fi list'),
    row('settings', ['KEY_DOWN', 'KEY_ENTER'], 'bluetooth', 'Enter on Bluetooth opens the Bluetooth list'),
    row('settings', ['KEY_DOWN'] * 3 + ['KEY_ENTER'], 'settings', 'Enter on Activity mark switches it, in place',
        is_('activity_mark', False)),
    row('light', 'KEY_ESC', 'settings', 'Esc on Screen light returns to Settings, on Screen light', is_('settings_index', 2)),
    row('light', 'KEY_ENTER', 'settings', 'Enter on Screen light is done'),
    row('light', ['KEY_RIGHT'] * 12, 'light', 'Brighter stops at 8', is_('light', 8)),
    row('light', ['KEY_LEFT'] * 12, 'light', 'Dimmer stops at off', is_('light', 0)),
    row('upload', 'KEY_ESC', 'settings', 'Esc on Add from a computer stops the page',
        lambda t: t.assertIsNone(kyphone_os._upload)),
    row('upload', 'CHAR:q', 'settings', 'Q on Add from a computer stops the page'),
    row('upload', 'KEY_ENTER', 'settings', 'Enter on Add from a computer stops the page'),

    row('wifi', [], 'wifi', 'The Wi-Fi list opens on the joined network', is_('net_index', 1)),
    row('wifi', 'KEY_ESC', 'settings', 'Esc on Wi-Fi goes back to Settings'),
    row('wifi', ['KEY_UP', 'KEY_UP', 'KEY_ENTER'], 'settings', 'Up past the switch to the header, Enter goes back'),
    row('wifi', 'KEY_DOWN', 'wifi', 'Down skips the OTHER NETWORKS label', is_('net_index', 3)),
    row('wifi', ['KEY_DOWN', 'KEY_UP'], 'wifi', 'Up skips the label too', is_('net_index', 1)),
    row('wifi', ['KEY_DOWN'] * 9, 'wifi', 'Down stops at SEARCH AGAIN', is_('net_index', 5)),
    row('wifi', [to_net('OpenCafe'), 'KEY_ENTER'], 'netstate', 'An open network connects at once',
        wire_starts('NETSTATE|W|OK|')),
    row('confirm_forget_wifi', [], 'confirm', 'Enter on the joined network asks before forgetting it',
        is_('confirm_sel', 'keep')),
    row('confirm_forget_wifi', 'KEY_ESC', 'wifi', 'Esc keeps the network'),
    row('confirm_forget_wifi', ['KEY_LEFT', 'KEY_ENTER'], 'wifi', 'Left, Enter on FORGET forgets it and redraws the list',
        is_('net_current', None)),
    row('netpass', 'KEY_ESC', 'wifi', 'Esc on the password box goes back to the Wi-Fi list'),
    row('netpass', ['KEY_UP', 'KEY_ENTER'], 'wifi', 'Up selects <, Enter goes back without connecting'),
    row('netpass', ['KEY_UP', 'CHAR:h'], 'netpass', 'Typing with < selected returns to the field and types',
        lambda t: t.assertEqual((t.st['net_pass'], t.st['net_pass_hdr']), ('h', False))),
    row('netpass', 'CHAR:q', 'netpass', 'Q in the password box is a letter', is_('net_pass', 'q')),
    row('netpass', ['CHAR:' + c for c in 'hunter22'] + ['KEY_ENTER'], 'netstate', 'The right password connects',
        wire_starts('NETSTATE|W|OK|')),
    row('netpass', ['CHAR:' + c for c in 'hunter22'] + ['KEY_ENTER', 'KEY_ENTER'], 'wifi',
        'Enter on CONNECTED shows the Wi-Fi list, joined', is_('net_current', 'Birch_5G')),
    row('netstate', [], 'netstate', 'A wrong password says it could not connect', wire_starts('NETSTATE|W|FAIL|')),
    row('netstate', 'KEY_ENTER', 'netpass', 'Enter on that returns to the password box, typing kept', is_('net_pass', 'x')),
    row('netstate', 'KEY_ESC', 'netpass', 'Esc on that returns to the password box too'),

    row('bluetooth', 'KEY_ESC', 'settings', 'Esc on Bluetooth goes back to Settings'),
    row('bluetooth', 'KEY_ENTER', 'stub', 'Switching Bluetooth off while the keyboard uses it is refused',
        wire_starts('STUB|BLUETOOTH|BLUETOOTH STAYS ON')),
    row('bluetooth', ['KEY_ENTER', 'KEY_ENTER'], 'bluetooth', 'Enter on that alert returns to the Bluetooth list'),
    row('bluetooth', [to_net('Keyboard'), 'KEY_ENTER', 'KEY_LEFT', 'KEY_ENTER'], 'stub',
        'FORGET on the keyboard is refused', wire_starts('STUB|BLUETOOTH|THE KEYBOARD CANNOT')),
    row('confirm_bt_device', [], 'confirm', 'Enter on a paired device asks, RE-CONNECT selected', is_('confirm_sel', 'keep')),
    row('confirm_bt_device', 'KEY_ESC', 'bluetooth', 'Esc goes back to the Bluetooth list'),
    row('confirm_bt_device', 'KEY_ENTER', 'netstate', 'Enter on RE-CONNECT connects', wire_starts('NETSTATE|B|OK|')),
    row('confirm_bt_device', ['KEY_ENTER', 'KEY_ENTER'], 'bluetooth', 'Enter on CONNECTED returns to the Bluetooth list'),
    row('confirm_bt_device', ['KEY_LEFT', 'KEY_ENTER'], 'bluetooth', 'Left, Enter on FORGET forgets it'),
    row('btpair', 'KEY_ESC', 'bluetooth', 'Esc on OTHER DEVICES goes back to the Bluetooth list'),
    row('btpair', 'CHAR:q', 'bluetooth', 'Q on OTHER DEVICES goes back too'),
    row('btpair', 'KEY_ENTER', 'netstate', 'Enter on a new device pairs it', wire_starts('NETSTATE|B|OK|Connected to Speaker')),
    row('btpair', ['KEY_ENTER', 'KEY_ENTER'], 'bluetooth', 'Enter on CONNECTED returns to the Bluetooth list'),
]


class NavCase(PhoneCase):
    def setUp(self):
        super().setUp()
        p = patch.object(kyphone_os, '_LIGHT_CLOCK', self.clock)
        p.start()
        self.addCleanup(p.stop)
        self.contacts.extend([{'first': 'Ann', 'last': 'Lee', 'number': '(555) 010-0103'},
                              {'first': 'Pip', 'last': 'Okonkwo', 'number': '(555) 010-0101'},
                              {'first': 'Zed', 'last': '', 'number': ''}])
        kyphone_os._save_contacts(self.contacts)
        self.incoming(UNSAVED, 'who is this')
        self.incoming(ANN, 'hi from Ann')                    # the newest: the first conversation
        self.add_book()
        self.add_album()
        self.st['notes'] = [{'text': 'first note', 'ts': datetime.now().isoformat()}]
        self.st['calls'] = [{'name': 'Pip Okonkwo', 'tag': 'OUT', 'ts': datetime.now().isoformat(), 'duration': '1:05',
                             'number': '(555) 010-0101', 'seen': True}]
        self.st['screen'] = 'home'
        self.st['home_index'] = 0
        self.screens.clear()                                  # (what setUp drew is not on the screen now)

    def every(self, items, label, fn):
        """fn(item) for each item from a fresh phone; every failure is collected, then all are reported together
        (pytest stops a unittest subTest at its first failure, and one wrong row must not hide the others)."""
        failed = []
        for item in items:
            self.setUp()
            try:
                fn(item)
            except AssertionError as e:
                lines = str(e).splitlines() or ['failed']
                failed.append('%s: %s' % (label(item), lines[0] if len(lines) == 1 else lines[0] + ' ' + lines[-1]))
        if failed:
            self.fail('%d of %d failed:\n  ' % (len(failed), len(items)) + '\n  '.join(failed))

    def press(self, steps):
        for step in steps:
            step(self) if callable(step) else self.key(step)

    def go(self, route):
        """From the home menu to `route`, by keys; the screen must be the one the route is named for."""
        item, steps = ROUTES[route]
        self.assertEqual(self.st['screen'], 'home')
        if item:
            self.st['home_index'] = kyphone_os.HOME_MENU.index(item)
            self.key('KEY_ENTER')
        self.press(steps)
        self.assertEqual(self.st['screen'], screen_of(route), 'route %s' % route)


class Routes(NavCase):
    def test_every_screen_in_the_spec_has_a_route(self):
        with open(os.path.join(HERE, '..', '..', 'CLAUDE.md')) as f:
            listed = re.search(r'^Screens: (.*)$', f.read(), re.M).group(1)
        screens = set(re.findall(r'[a-z_]+', listed.split('.')[0]))
        routed = {screen_of(r) for r in ROUTES}
        self.assertEqual(screens - routed, set(), 'a screen with no route')
        self.assertEqual(routed - screens, set(), 'a route to a screen the spec does not list')

    def test_every_route_reaches_its_screen_by_keys(self):
        self.every(list(ROUTES), str, self.go)

    def test_every_route_is_in_the_map(self):
        self.assertEqual(set(ROUTES) - {r.start for r in MAP}, set())

    def test_the_phone_checklist_is_up_to_date(self):
        sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
        import make_phone_checklist as mpc
        with open(mpc.OUT) as f:
            self.assertEqual(f.read(), mpc.checklist(), 'run spi_bridge/tools/make_phone_checklist.py')


class Map(NavCase):
    def test_every_row_of_the_navigation_table(self):
        def check(r):
            self.go(r.start)
            self.press(r.keys)
            self.assertEqual(self.st['screen'], r.expect, r.says)
            if r.check:
                r.check(self)
        self.every(MAP, lambda r: '%s, %s' % (r.start, r.says), check)


# Where the cross (KEY_LOCK) and auto-lock come back to: the same screen, except these, which close on the way out
# (and the lock screen itself, locked by hand, which unlocks to home).
RETURNS_TO = {'note': 'notes_list', 'upload': 'settings', 'netstate': 'settings', 'lock': 'home'}
CALLS = ('outgoing', 'incoming', 'in_call')


class EveryScreen(NavCase):
    def each(self, fn):
        def run(route):
            self.go(route)
            fn(self.st['screen'])
        self.every(list(ROUTES), str, run)

    def test_the_cross_locks_and_the_next_key_comes_back(self):
        def check(screen):
            self.key('KEY_LOCK')
            if screen in CALLS:
                self.assertEqual(self.st['screen'], screen, 'a call owns the screen')
                return
            self.assertEqual(self.st['screen'], 'lock')
            self.key('KEY_DOWN')
            self.assertEqual(self.st['screen'], RETURNS_TO.get(screen, screen))
        self.each(check)

    def test_the_circle_locks_and_the_next_key_opens_home_on_text(self):
        def check(screen):
            self.key('KEY_HOME')
            if screen in CALLS:
                self.assertEqual(self.st['screen'], screen)
                return
            self.assertEqual(self.st['screen'], 'lock')
            self.key('KEY_DOWN')
            self.assertEqual((self.st['screen'], self.st['home_index']), ('home', 0))
        self.each(check)

    def test_auto_lock_comes_back_to_exactly_the_same_screen(self):
        def check(screen):
            before = self.wire if self.screens else None
            self.clock.t += kyphone_os.AUTO_LOCK_SECONDS + 1
            kyphone_os._auto_lock_tick()
            if screen in kyphone_os.NO_AUTO_LOCK or screen == 'lock':
                self.assertEqual(self.st['screen'], screen, 'never auto-locks here')
                return
            self.assertEqual(self.st['screen'], 'lock')
            self.key('KEY_DOWN')
            self.assertEqual(self.st['screen'], screen)
            if before and not before.startswith(('HOME2|', 'NOWPLAYING|')):     # (the time, and the music, moved on)
                self.assertEqual(self.wire, before, 'redrawn exactly as it was')
        self.each(check)

    def test_no_screen_is_a_dead_end_esc_always_reaches_the_lock_screen(self):
        def check(screen):
            trail = [screen]
            for _ in range(10):
                if self.st['screen'] == 'lock':
                    break
                if self.st['screen'] == 'confirm' and trail.count('confirm') > 1:   # Esc on a question is the safe
                    self.key('KEY_LEFT', 'KEY_ENTER')             # answer, so a message with text in it is left by DISCARD
                else:
                    self.key('KEY_ESC')
                trail.append(self.st['screen'])
            self.assertEqual(self.st['screen'], 'lock', ' > '.join(trail))
        self.each(check)

    def test_every_screen_answers_every_key_without_raising(self):
        keys = ['KEY_UP', 'KEY_DOWN', 'KEY_LEFT', 'KEY_RIGHT', 'KEY_ENTER', 'KEY_ESC', 'KEY_BACKSPACE', 'KEY_TAB',
                'CHAR:q', 'CHAR:+', 'CHAR:-', 'CHAR: ', 'CHAR:c', 'CHAR:i', 'CHAR:5', 'CHAR:|']
        pairs = [(r, k) for r in ROUTES for k in keys]
        def press(pair):
            self.go(pair[0])
            kyphone_os.handle_key(pair[1])                        # (an exception fails the test)
        self.every(pairs, lambda p: '%s %s' % p, press)


# ── lists: empty, and far longer than the screen ──

# screen route, the index field, the window field, rows on screen, how the rows are counted, how to fill it
LISTS = {
    'texts_list':    ('texts_index', 'texts_start', kyphone_os.TEXTS_ROWS),
    'contacts_pick': ('contacts_index', 'contacts_start', kyphone_os.CONTACTS_ROWS),
    'calls_list':    ('calls_index', 'calls_start', kyphone_os.CALLS_ROWS),
    'library':       ('library_index', 'library_start', kyphone_os.LIBRARY_ROWS),
    'music':         ('music_index', 'music_start', kyphone_os.MUSIC_ROWS),
    'notes_list':    ('notes_index', 'notes_start', kyphone_os.NOTES_ROWS),
}


class LongLists(NavCase):
    def fill(self):
        now = datetime.now().isoformat()
        self.st['messages'] = [{'sender': '+1555020%04d' % i, 'name': '', 'body': 'text %d' % i, 'read': True,
                                'ts': now} for i in range(300)]
        self.contacts[:] = sorted(({'first': 'Person%03d' % i, 'last': '', 'number': '(555) 030-%04d' % i}
                                   for i in range(300)), key=lambda c: c['first'])
        self.st['calls'] = [{'name': 'Caller %d' % i, 'tag': 'IN', 'ts': now, 'duration': '0:10',
                             'number': '(555) 040-%04d' % i, 'seen': True} for i in range(50)]
        self.st['notes'] = [{'text': 'note %d' % i, 'ts': now} for i in range(120)]
        from epub_fixtures import make_epub
        for i in range(12):
            make_epub(os.path.join(self.books, 'b%02d.epub' % i), [('c.xhtml', b'<html><body><p>x</p></body></html>')],
                      title='Book %02d' % i, author='A')
        import audio_fixtures as fx
        for i in range(12):
            path = os.path.join(self.music, 'Artist', 'Album %02d' % i, '01 Song.wav')
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'wb') as f:
                f.write(fx.wav_bytes(1, rate=800))

    def total(self, route):
        return {'texts_list': len(kyphone_os.get_threads()), 'contacts_pick': len(self.contacts),
                'calls_list': len(self.st['calls']) + 1, 'library': len(self.st['library_books']),
                'music': len(kyphone_os._music_rows()), 'notes_list': len(self.st['notes'])}[route]

    def check_window(self, route):
        index, start, rows = LISTS[route]
        idx, top = self.st[index], self.st[start]
        if idx >= 0:
            self.assertTrue(top <= idx < top + rows, '%s: row %d outside the window from %d' % (route, idx, top))
            sel = int(self.wire.split('|')[1])
            self.assertEqual(sel, idx - top, '%s: the wire selects row %d of the window' % (route, sel))

    def test_down_to_the_last_row_and_up_to_the_header_keeping_the_selection_on_screen(self):
        def walk(route):
            self.fill()
            self.go(route)
            index = LISTS[route][0]
            n = self.total(route)
            self.assertGreater(n, LISTS[route][2], 'the list must be longer than the screen')
            for _ in range(n + 3):
                self.key('KEY_DOWN')
                self.check_window(route)
            self.assertEqual(self.st[index], n - 1, 'Down stops at the last row')
            for _ in range(n + 3):
                self.key('KEY_UP')
                self.check_window(route)
            self.assertEqual(self.st[index], -1, 'Up ends on the header')
            self.key('KEY_DOWN')
            self.assertEqual(self.st[index], 0)
            self.assertEqual(self.st[LISTS[route][1]], 0, 'back at the top of the list')
        self.every(list(LISTS), str, walk)


class EmptyLists(PhoneCase):
    """A new phone: nothing in any app. Every list opens, says it is empty, and gets you out again."""

    def open(self, item):
        self.st['screen'], self.st['home_index'] = 'home', kyphone_os.HOME_MENU.index(item)
        self.key('KEY_ENTER')

    def test_empty_texts_open_on_plus_so_enter_writes_the_first(self):
        self.open('TEXT')
        self.assertEqual((self.st['texts_index'], self.st['texts_header_sel']), (-1, 'plus'))
        self.key('KEY_DOWN')
        self.assertEqual(self.st['texts_index'], -1, 'Down has nowhere to go')
        self.key('KEY_ENTER')
        self.assertEqual(self.st['screen'], 'compose')
        self.key('KEY_ESC', 'KEY_LEFT', 'KEY_ENTER')
        self.assertEqual(self.st['screen'], 'home')

    def test_empty_contacts_open_on_plus(self):
        self.open('CONTACTS')
        self.assertEqual((self.st['contacts_index'], self.st['contacts_header_sel']), (-1, 'plus'))
        self.key('KEY_DOWN', 'KEY_DOWN')
        self.assertEqual(self.st['contacts_index'], -1)
        self.key('KEY_ENTER')
        self.assertEqual(self.st['screen'], 'contact_edit')
        self.key('KEY_ESC', 'KEY_LEFT', 'KEY_ENTER')
        self.assertEqual(self.st['screen'], 'home')

    def test_empty_contacts_with_letters_typed_say_no_match(self):
        self.open('CONTACTS')
        self.type('zz')
        self.key('KEY_DOWN', 'KEY_UP', 'KEY_UP')
        self.assertEqual(self.st['screen'], 'contacts_pick')

    def test_empty_calls_still_offer_dial_a_number(self):
        self.open('CALL')
        self.key('KEY_DOWN', 'KEY_DOWN')
        self.assertEqual(self.st['calls_index'], 0)
        self.key('KEY_ENTER')
        self.assertEqual(self.st['screen'], 'dial')
        self.key('KEY_DOWN', 'KEY_ENTER')                        # no quick-dial contacts: nothing to select
        self.assertEqual(self.st['screen'], 'dial')

    def test_empty_books_open_on_the_header_and_enter_goes_home(self):
        self.open('READ')
        self.assertEqual(self.st['library_index'], -1)
        self.key('KEY_DOWN')
        self.assertEqual(self.st['library_index'], -1)
        self.key('KEY_ENTER')
        self.assertEqual(self.st['screen'], 'home')

    def test_empty_music_says_no_music_and_enter_goes_home(self):
        self.open('LISTEN')
        self.assertEqual(self.st['music_index'], -1)
        self.key('KEY_DOWN', 'KEY_ENTER')
        self.assertEqual(self.st['screen'], 'home')

    def test_empty_notes_open_on_plus(self):
        self.open('NOTES')
        self.assertEqual((self.st['notes_index'], self.st['notes_header_sel']), (-1, 'plus'))
        self.key('KEY_DOWN')
        self.assertEqual(self.st['notes_index'], -1)
        self.key('KEY_ENTER')
        self.assertEqual(self.st['screen'], 'note')
        self.key('KEY_ESC')                                       # an empty note is not kept
        self.assertEqual(self.st['notes'], [])

    def test_wifi_off_shows_only_the_switch(self):
        self.net.wifi_on = False
        self.open('SETTINGS')
        self.key('KEY_ENTER')
        self.assertEqual(self.st['net_index'], 0)
        self.key('KEY_DOWN', 'KEY_DOWN')
        self.assertEqual(self.st['net_index'], 0, 'nothing below the switch')
        self.key('KEY_ENTER')                                     # switch it on: the list comes back
        self.assertTrue(self.net.wifi_on)
        self.assertEqual(self.st['screen'], 'wifi')

    def test_no_devices_found_offers_search_again(self):
        self.net.new = []
        self.open('SETTINGS')
        self.key('KEY_DOWN', 'KEY_ENTER')
        self.st['net_index'] = len(kyphone_os._net_entries(*kyphone_os._net_snapshot()[:5])) - 1
        self.key('KEY_ENTER')
        self.assertEqual(self.st['screen'], 'btpair')
        self.assertIn('No devices found', self.wire)
        self.key('KEY_ENTER')                                     # SEARCH AGAIN
        self.assertEqual(self.st['screen'], 'btpair')
