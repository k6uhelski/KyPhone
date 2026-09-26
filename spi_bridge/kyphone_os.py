"""
kyphone_os.py — KyPhone OS (the version number is defined once, in version.py)

Screens: lock | home | texts_list | thread | compose | confirm |
         contacts_pick | contact | contact_edit | stub |
         calls_list | dial | outgoing | incoming | in_call |
         library | reader

Reader: READ on the home menu opens the library (the .epub files in data/books/);
a book opens in the reader, which shows one page at a time. The book is parsed and
laid out here (reader_epub.py, reader_layout.py); the Inkplate is sent the lines.

Call screens (unchanged in 0.2.1, and simulated: there is no telephony until the
cellular modem exists):
    calls_list   DIAL A NUMBER, then the call log, windowed to six rows
    dial         a number buffer with four quick-dial contacts underneath
    outgoing     CALLSTATE|OUT    ringing; Enter answers it (a demo), Esc goes back
    incoming     CALLSTATE|IN     the `i` key on the home menu raises one (a demo)
    in_call      CALLSTATE|ACTIVE a running timer; Esc (or Q) hangs up

Run:
    python3 spi_bridge/kyphone_os.py          # hardware mode (Radxa)
    python3 spi_bridge/kyphone_os.py --sim    # simulator (Mac)
"""

import os
import re
import sys
import time
import json
import threading
from datetime import datetime, timedelta

SIM_MODE = '--sim' in sys.argv

if not SIM_MODE:
    import spidev
    import gpiod
    from input_handler import KeyboardHandler
    from trackpad_handler import TrackpadHandler

import reader_epub
import reader_layout as rl
import music_library
import music_player
import modem
import network_control as netctl
import version

VERSION = version.VERSION

# --- Config ---
CHIP            = 'gpiochip3'
HANDSHAKE_LINE  = 21
SPI_BUS         = 3
SPI_DEV         = 0
SPI_SPEED_HZ    = 10000
PAYLOAD_BYTES   = 256

SMS_POLL_INTERVAL    = 2
CLOCK_UPDATE_INTERVAL = 60

# --- List windows (OS 0.2.1) ---
# A list screen is drawn from ONE command of at most MAX_COMMAND_CHARS
# characters (build_payload truncates anything longer), so lists are windowed:
# only the visible rows are sent, and the window re-sends when the selection
# moves. Row counts and column caps: docs/02-design/design_handoff_os_0_2.
MAX_COMMAND_CHARS = PAYLOAD_BYTES - 3
TEXTS_ROWS    = 5
CONTACTS_ROWS = 7
CALLS_ROWS    = 6
LIBRARY_ROWS  = 5
CALL_LOG_MAX  = 50                                            # how many finished calls the log keeps
LIST_NAME_MAX    = 14   # texts + calls name column
CONTACT_NAME_MAX = 18   # contacts list name column
PREVIEW_MAX      = 24   # texts list preview line

COMPOSE_TO_MAX     = 20   # New Message TO field
CONTACT_FIELD_MAX  = 18   # first / last name fields
NUMBER_FIELD_MAX   = 18   # phone number field

# --- Thread (OS 0.2.1) ---
THREAD_BUBBLES  = 3     # newest messages drawn per thread screen
THREAD_NAME_MAX = 20
COMPOSER_COLS   = 30    # 24px glyphs across the 552px composer
COMPOSER_LINES  = 3

# Sim only: what the fake radio does with a send. The phone has no cellular
# service yet, so "not_sent" is what the real device does today; set
# KYPHONE_SIM_SEND=sent to see the SENDING... -> SENT path in the emulator.
SIM_SEND       = os.environ.get('KYPHONE_SIM_SEND', 'not_sent')   # 'sent' | 'not_sent'
SIM_SEND_DELAY = 1.4    # seconds SENDING... stays on screen in the sim

# --- Contacts ---
# Address book records: [{first, last, number}, ...]. OS 0.1 stored a flat
# {number: name} dict; that shape is auto-migrated to this one on load.
# KYPHONE_DATA_DIR moves contacts.json and messages.json (tests and the emulator
# point it at a scratch folder so they never touch the real data).
DATA_DIR = os.path.abspath(os.path.expanduser(os.environ.get('KYPHONE_DATA_DIR') or
                                              os.path.join(os.path.dirname(__file__), '..', 'data')))
_contacts_path = os.path.join(DATA_DIR, 'contacts.json')


def _save_contacts(contacts):
    try:
        os.makedirs(os.path.dirname(_contacts_path), exist_ok=True)
        with open(_contacts_path, 'w') as f:
            json.dump(contacts, f, indent=2)
    except Exception as e:
        print(f"Warning: could not save contacts: {e}")


def _load_contacts():
    try:
        with open(_contacts_path) as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"Warning: could not load contacts: {e}")
        return []

    if isinstance(data, dict):
        # OS 0.1 format: {number: name}. Migrate to {first, last, number}.
        migrated = [{'first': name, 'last': '', 'number': number} for number, name in data.items()]
        _save_contacts(migrated)
        print(f"Migrated {len(migrated)} contacts to the OS 0.2 format.")
        return migrated
    return data


CONTACTS = _load_contacts()


def dispname(c):
    return f"{c.get('first', '')} {c.get('last', '')}".strip()


# ─── Phone numbers ────────────────────────────────────────────────────────────

def digits(n):
    return re.sub(r'\D', '', str(n or ''))


def number_valid(n):
    """Dialable: ten digits, or eleven starting with 1. Spaces, dashes and
    brackets are fine."""
    d = digits(n)
    return len(d) == 10 or (len(d) == 11 and d[0] == '1')


def format_number(n):
    """(555) 019-9002 for a dialable number; anything else is shown as typed.
    Never truncated: it is 14 characters, the width of the name column."""
    d = digits(n)
    core = d[1:] if len(d) == 11 and d[0] == '1' else d
    if len(core) == 10:
        return f"({core[:3]}) {core[3:6]}-{core[6:]}"
    return str(n or '')


def same_number(a, b):
    """Two numbers are the same person if their last ten digits agree, however
    they were typed: +15550100001, 1 555 010 0001 and (555) 010-0001."""
    da, db = digits(a), digits(b)
    return bool(da) and bool(db) and da[-10:] == db[-10:]


def normalize_number(n):
    """+1XXXXXXXXXX for a dialable number, so a reply lands in the same thread
    as the person's own texts; anything else is left as given."""
    d = digits(n)
    if len(d) == 10:
        return '+1' + d
    if len(d) == 11 and d[0] == '1':
        return '+' + d
    return str(n or '')


def find_contact(number=None, name=None):
    for c in CONTACTS:
        if number is not None and same_number(c.get('number'), number):
            return c
        if name is not None and (dispname(c) == name or c.get('first') == name):
            return c
    return None


def contact_index_for(number):
    """Position in CONTACTS of the contact with this number, or None."""
    for i, c in enumerate(CONTACTS):
        if same_number(c.get('number'), number):
            return i
    return None

# --- Persistence Paths ---
MESSAGES_FILE = os.path.join(DATA_DIR, 'messages.json')     # DATA_DIR is defined with the contacts path above
CALLS_FILE    = os.path.join(DATA_DIR, 'calls.json')        # the call log
SETTINGS_FILE = os.path.join(DATA_DIR, 'settings.json')     # the phone's own settings (screen light, activity mark)
LIGHT_LEVELS  = 8                                            # screen light steps; 0 = off

# --- Reader (books) ---
BOOKS_DIR         = os.path.join(DATA_DIR, 'books')          # drop .epub files here
READING_FILE      = os.path.join(DATA_DIR, 'reading.json')   # {'font': 'M', 'books': {id: {chapter, offset, pct}}}
LIBRARY_MAX_BOOKS = 200
LIBRARY_TITLE_MAX  = 24
LIBRARY_AUTHOR_MAX = 14
READER_FULL_EVERY  = 0        # partial refreshes allowed between full (flashing) ones: 0 = every page turn is a full refresh (Kyle's call, no ghosting)
DEFAULT_READER_SIZE = 'M'

# --- Music (LISTEN) ---
MUSIC_DIR         = os.path.join(DATA_DIR, 'music')             # drop music files here, in any folders
MUSIC_INDEX_FILE  = os.path.join(DATA_DIR, 'music_index.json')  # cache of what each file's tags say
LISTENING_FILE    = os.path.join(DATA_DIR, 'listening.json')    # {'volume': 40, 'last': {'path', 'position'}}
MUSIC_ROWS        = 5
MUSIC_TITLE_MAX   = 22
MUSIC_SUB_MAX     = 24

# --- Settings (Wi-Fi / Bluetooth) ---
NET_ROWS      = 5    # visible rows in the Wi-Fi / Bluetooth / Other devices lists (they scroll)
NET_NAME_MAX  = 22   # a network SSID or device name column
NET_SUB_MAX   = 30   # the settings list's status subtitle ("Connected: ...")
NET_PASS_MAX  = 63   # the longest a WPA passphrase can be
NOW_TITLE_MAX     = 56       # two 28-character lines
NOW_LINE_MAX      = 44
MUSIC_TICK_SECONDS = 30      # while the now-playing screen is up, redraw this often so the time moves
_MUSIC_CLOCK      = time.monotonic

# --- Lock Screen Quotes ---
# Matches the fixed list in the OS 0.2 design prototype. Quotes cycle each
# time the device returns to lock.
QUOTES = [
    "Smile, breathe, and go slowly.",
    "Because you are alive, everything is possible.",
    "The most precious gift we can offer others is our presence.",
    "Life can be found only in the present moment.",
    "Walk as if you are kissing the Earth with your feet.",
    "Our own life has to be our message.",
    "Drink your tea slowly and reverently.",
    "There is no path to peace - peace is the path.",
    "Letting go gives us freedom, and freedom is the only condition for happiness.",
    "We are more than our pain.",
]

# An unbuilt feature is a stop alert, not a silent no-op: each one says what
# happened, why it happened, and what to do instead.
STUB_INFO = {}       # (READ and LISTEN were the last two; both are real screens now)

# Stop alerts for input the phone will not act on: a no-op is never silent (the
# only deliberate silence is a rejected keystroke).
ALERTS = {
    'BT_KEEP_ON':   ('BLUETOOTH', 'BLUETOOTH STAYS ON WHILE THE KEYBOARD IS CONNECTED BY IT. SWITCHED OFF, THE PHONE WOULD HAVE NO WAY TO TYPE, SO NOTHING COULD SWITCH IT BACK ON.'),
    'BT_KEEP_KEYBOARD': ('BLUETOOTH', "THE KEYBOARD CANNOT BE FORGOTTEN. IT IS THE PHONE'S WAY TO TYPE, AND PAIRING IT AGAIN WOULD NEED A KEYBOARD."),
    'NET_FAILED':   ('SETTINGS', '{what}. TRY AGAIN IN A MOMENT.'),
    'EMPTY_SEND':   ('NEW MESSAGE', 'THERE IS NOTHING TO SEND. TYPE A MESSAGE FIRST, THEN PRESS SEND.'),
    'NO_RECIPIENT': ('NEW MESSAGE', 'THERE IS NO ONE TO SEND THIS TO. TYPE A NUMBER IN THE TO FIELD, OR PRESS + TO PICK A CONTACT.'),
    'NEED_FIRST':   ('CONTACT', 'A CONTACT NEEDS A FIRST NAME. TYPE ONE IN THE FIRST NAME FIELD, THEN PRESS SAVE.'),
    'NEED_NUMBER':  ('CONTACT', 'A CONTACT NEEDS A PHONE NUMBER. TYPE ONE IN THE PHONE NUMBER FIELD, THEN PRESS SAVE.'),
    'BAD_RECIPIENT': ('NEW MESSAGE', 'THAT NUMBER CANNOT BE TEXTED. A NUMBER NEEDS TEN DIGITS, OR ELEVEN STARTING WITH 1. SPACES, DASHES AND BRACKETS ARE FINE.'),
    'BAD_NUMBER':   ('CONTACT', 'THAT NUMBER CANNOT BE DIALED. A NUMBER NEEDS TEN DIGITS, OR ELEVEN STARTING WITH 1. SPACES, DASHES AND BRACKETS ARE FINE.'),
    'DUP_NUMBER':   ('CONTACT', 'THAT NUMBER IS ALREADY SAVED AS {name}. EDIT THAT CONTACT INSTEAD, OR TYPE A DIFFERENT NUMBER.'),
    'BAD_BOOK':     ('READ', 'THIS BOOK CANNOT BE OPENED. {reason}. PRESS ENTER TO GO BACK TO YOUR BOOKS.'),
    'END_OF_BOOK':  ('READ', 'THAT WAS THE LAST PAGE OF THE BOOK. PRESS ENTER TO GO BACK TO THE PAGE, THEN Q FOR YOUR BOOKS.'),
    'START_OF_BOOK': ('READ', 'THIS IS THE FIRST PAGE OF THE BOOK. PRESS ENTER TO GO BACK TO THE PAGE.'),
    'BAD_TRACK':    ('LISTEN', '{title} CANNOT BE PLAYED: {reason}. IT WAS SKIPPED. PRESS ENTER TO GO ON.'),
    'NO_AUDIO':     ('LISTEN', 'THIS PHONE HAS NO SOUND OUTPUT RIGHT NOW: {reason}. PRESS ENTER TO GO BACK.'),
    'BIGGEST_FONT': ('READ', 'THE TEXT IS ALREADY AT ITS LARGEST SIZE. PRESS ENTER TO GO BACK TO THE PAGE.'),
    'SMALLEST_FONT': ('READ', 'THE TEXT IS ALREADY AT ITS SMALLEST SIZE. PRESS ENTER TO GO BACK TO THE PAGE.'),
}

# --- State ---
# The order is Kyle's (2026-09-19): texts, calls, books, music, address book. The design
# handoff had CONTACTS third; only the first three rows are on screen on first view.
HOME_MENU = ['TEXT', 'CALL', 'READ', 'LISTEN', 'CONTACTS', 'NOTES', 'SETTINGS']

# How the home menu looks: pixel icons (the design's default), icons with their
# words, or words only. Set KYPHONE_HOME_STYLE=icons|both|words; the renderer draws
# whichever it is told, so changing it needs no reflash.
HOME_STYLES = {'icons': 'I', 'both': 'B', 'words': 'W'}
HOME_STYLE  = HOME_STYLES.get(os.environ.get('KYPHONE_HOME_STYLE', 'icons'), 'I')

state = {
    'screen':           'lock',
    'home_index':       0,          # -1=header | position in HOME_MENU: 0=TEXT 1=CALL 2=READ 3=LISTEN 4=CONTACTS 5=SETTINGS
    'texts_index':      0,          # -1=header row selected
    'texts_start':      0,          # first thread in the 5-row window
    'texts_header_sel': 'back',     # 'back' | 'plus'
    'thread_id':        None,       # sender phone number
    'thread_draft':     '',
    'thread_header_sel': None,      # None=typing | 'back' | 'info'
    'thread_msg_sel':   -1,         # -1=composer | index into the shown bubbles (a not-sent one, for retry)
    'compose_to':       '',
    'compose_msg':      '',
    'compose_to_active': True,
    'compose_header_sel': None,     # None=typing | 'x'
    'compose_plus_sel': False,      # '+' next to an empty TO field selected
    'compose_send_sel': False,      # SEND button selected
    'confirm_kind':     'discard_message',   # 'discard_message' | 'delete_contact' | 'forget_wifi' | 'bt_device' | 'delete_note'
    'confirm_sel':      'keep',     # 'keep' (the safe, right-hand default) | 'go' (the destructive one)
    'stub_key':         '',
    'stub_return':      'home',     # screen to return to on Esc/Enter
    'stub_text':        None,       # (title, body) for a stop alert; None = STUB_INFO[stub_key]
    'quote_index':      0,
    'messages':         [],         # [{sender, name, body, read, ts}]

    'contacts_query':      '',
    'contacts_index':      0,       # -1=header row selected
    'contacts_start':      0,       # first contact in the 7-row window
    'contacts_header_sel': 'back',  # 'back' | 'plus'
    'contacts_return':     'home',  # 'home' | 'compose' — where Esc/back leads

    'contact_idx':    None,         # position in CONTACTS of the contact being viewed; None = a number that is not saved
    'contact_number': '',           # that number, when it is not saved
    'contact_sel':    'call',       # 'back' | 'call' | 'text' | 'edit'
    'contact_return': 'contacts_pick',

    'edit_idx':    None,            # position of the contact being edited; None = a new one
    'edit_return': 'contact',       # where X / Esc leads: 'contact' | 'contacts_pick' | 'compose'
    'edit_first':  '',
    'edit_last':   '',
    'edit_number': '',
    'edit_number_locked': False,    # the phone field is fixed (a contact made for an existing conversation's number)
    'edit_index':  0,               # -1=cancel | 0=first | 1=last | 2=number | 3=save

    'calls':            [],         # [{name, tag OUT/IN/MISS, ts, duration}] the call log, newest first (calls.json)
    'call_dir':         'OUT',      # 'OUT' | 'IN': which way the call in progress is going
    'calls_index':      0,          # -1=header | 0=DIAL A NUMBER | 1..=calls[i-1]
    'calls_start':      0,          # first entry in the 6-row window
    'dial_buffer':      '',
    'dial_quick_index': -1,         # -1=buffer active, >=0 selects a quick-dial contact
    'call_name':        '',
    'call_started_at':  None,

    'library_books':  [],           # [{path, id, title, author, pct, error}] scanned from BOOKS_DIR when READ opens
    'library_index':  0,            # -1=header | index into library_books
    'library_start':  0,            # first book in the 5-row window

    'book':           None,         # the open reader_epub.Book, or None
    'r_id':           '',           # its id in reading.json
    'r_chapter':      0,            # chapter index in reading order
    'r_offset':       0,            # reading position in that chapter (independent of font size)
    'r_size':         DEFAULT_READER_SIZE,   # 'S' | 'M' | 'L' | 'X'
    'r_pages':        None,         # pages of the current chapter at the current size
    'r_pages_key':    None,         # (chapter, size) those pages belong to
    'r_turns':        0,            # partial refreshes since the last full one

    'music_lib':      None,         # the scanned music_library.Library
    'music_index':    0,            # -1=header | row in the album list (NOW PLAYING / RESUME first, when there is one)
    'music_start':    0,            # first row in the 5-row window
    'album':          None,         # the Album whose tracks are showing
    'tracks_index':   0,            # -1=header | track in that album
    'tracks_start':   0,
    'music_return':   'music',      # where Esc on the now-playing screen goes: 'music' | 'tracks'
    'music_last':     None,         # {'path', 'position'} from listening.json, offered as RESUME

    'settings_index': 0,            # -1=header | 0=Wi-Fi | 1=Bluetooth
    'settings_wifi_on': True,       # the Wi-Fi switch, read when SETTINGS opens
    'notes':          [],           # [{text, ts}], newest first — data/notes.json
    'notes_index':    0,            # -1 = the header (notes_header_sel: back / plus)
    'notes_start':    0,
    'notes_header_sel': 'back',
    'note_idx':       None,         # the note being edited (an index into notes), or None for a new one
    'note_text':      '',
    'note_hdr':       None,         # None = typing | 'back' | 'delete' — the editor's header selection
    'light':          0,            # the screen light, 0 (off) .. LIGHT_LEVELS — saved in settings.json
    'activity_mark':  True,         # a * by the lock screen's clock for an unread text or an unseen missed call
    'settings_wifi':  None,         # network_control.WifiStatus, refreshed when SETTINGS opens or a connect succeeds
    'settings_bt':    None,         # network_control.BtStatus, same

    'net_kind':       'W',          # 'W' the Wi-Fi list | 'B' the Bluetooth list | 'P' Other devices (pairing)
    'net_on':         True,         # the list's switch (Wi-Fi / Bluetooth on)
    'net_current':    None,         # the joined Wi-Fi network's name, or None
    'net_rows':       [],           # W: the last search's WifiNetworks · B: the paired BtDevices · P: new BtDevices
    'net_index':      0,            # -1=header, else a row of _net_entries()
    'net_start':      0,
    'net_scanning':   False,        # a search is running in the background (SEARCHING... ends the list)
    'net_confirm_connected': False, # the known device on the FORGET / RE-CONNECT screen is connected
    'net_confirm_input':     False, # ... is an input device (the keyboard): it can never be forgotten
    'net_ssid':       '',           # the Wi-Fi network being typed a password for or connected to
    'net_pass':       '',           # the password typed so far on NETPASS (never sent to the screen)
    'net_pass_hdr':   False,        # NETPASS: True = the header's < is selected (Enter there backs out)
    'net_source':     'wifi',       # where NETSTATE's attempt started: 'wifi' | 'netpass' | 'bluetooth' | 'btpair' | 'forget'
    'net_mac':        '',           # the Bluetooth device being paired/connected
    'net_name':       '',           # that device's name, for the NETSTATE message
    'net_status':     'WORKING',    # NETSTATE: 'WORKING' | 'OK' | 'FAIL'
    'net_detail':     '',           # NETSTATE's short message

    'running': True,
    'lock':    threading.Lock(),
}

_spi_lock       = threading.Lock()  # serializes the SPI sender thread's own transfers
_pending_lock   = threading.Lock()
_pending_command = None
_pending_event  = threading.Event()

# --- Hardware Init ---
if not SIM_MODE:
    chip      = gpiod.Chip(CHIP)
    handshake = chip.get_line(HANDSHAKE_LINE)
    handshake.request(consumer='kyphone-os', type=gpiod.LINE_REQ_DIR_IN)

    spi = spidev.SpiDev()
    try:
        spi.open(SPI_BUS, SPI_DEV)
    except FileNotFoundError:
        print(f"Error: /dev/spidev{SPI_BUS}.{SPI_DEV} not found.")
        sys.exit(1)
    spi.max_speed_hz = SPI_SPEED_HZ
    spi.mode = 0

# --- Simulator ---
simulator = None
if SIM_MODE:
    from simulator import Simulator
    simulator = Simulator(lambda keycode: handle_key(keycode))


# ─── Helpers ──────────────────────────────────────────────────────────────────

def format_name(number):
    """A saved contact's name, else the number formatted — never truncated, so
    two unsaved senders never look the same."""
    c = find_contact(number=number)
    return dispname(c) if c else format_number(number)


def format_msg_time(ts):
    """Relative display time for a message, matching the OS 0.2 design's
    convention: clock time today, 'Yesterday', a weekday name within the
    last week, else a short date. `ts` is an ISO timestamp string, or
    falsy for messages saved before timestamps existed."""
    if not ts:
        return ''
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return ''
    now = datetime.now()
    if dt.date() == now.date():
        return dt.strftime("%-I:%M %p")
    elif dt.date() == (now - timedelta(days=1)).date():
        return "Yesterday"
    elif (now - dt).days < 7:
        return dt.strftime("%A").upper()
    else:
        return dt.strftime("%-m/%-d/%y")


# ─── Messages ─────────────────────────────────────────────────────────────────
# Incoming:  {sender: <other party's number>, name, body, read, ts}
# Outgoing:  {dir: 'out', peer: <other party's number>, name: 'You', body, read,
#             ts, state: 'sending' | 'sent' | 'not_sent'}
# A thread is keyed on the OTHER party's number. The phone never needs, and the
# interface never shows, its own number.

def is_outgoing(m):
    if m.get('dir') == 'out':
        return True
    # OS 0.2 stored a sent message as sender=<own number>, name 'You', with no recipient.
    return 'peer' not in m and m.get('name') == 'You'


def peer_of(m):
    """The other party's number — or None for an old sent message that never
    recorded who it went to, which is left out of every thread."""
    if 'peer' in m:
        return m['peer']
    return None if is_outgoing(m) else m.get('sender')


# ─── Text the panel can draw ──────────────────────────────────────────────────
# The panel draws printable ASCII and nothing else, and '|' and '·' are the SPI
# field separators. A typed character that cannot be drawn is ignored; text we
# receive is not under our control, so it is cleaned instead.
_RESERVED = ('|', '\xb7')
_TRANSLIT = {
    '‘': "'", '’': "'", '“': '"', '”': '"', '–': '-',
    '—': '-', '…': '...', ' ': ' ', '\n': ' ', '\r': ' ', '\t': ' ',
}


def can_draw(ch):
    return len(ch) == 1 and ' ' <= ch <= '~' and ch not in _RESERVED


def sanitize(text):
    """Make received text drawable: common typographic characters become their
    ASCII look-alikes, anything else undrawable becomes '?'."""
    out = []
    for c in str(text):
        c = _TRANSLIT.get(c, c)
        out.append(c if all(can_draw(x) for x in c) else '?')
    return ''.join(out)


def wrap_words(text, cols):
    """Greedy word wrap into lines of at most `cols` characters; a word longer
    than a line is broken. The renderers wrap exactly like this, which is what
    lets composer_view() promise how many lines a draft takes."""
    lines, cur = [], ''
    for word in text.split(' '):
        while len(word) > cols:
            if cur:
                room = cols - len(cur) - 1            # fill the current line first
                if room > 0:
                    cur += ' ' + word[:room]
                    word = word[room:]
                lines.append(cur)
                cur = ''
            else:
                lines.append(word[:cols])
                word = word[cols:]
        if not cur:
            cur = word
        elif len(cur) + 1 + len(word) <= cols:
            cur += ' ' + word
        else:
            lines.append(cur)
            cur = word
    lines.append(cur)
    return lines


def composer_view(draft):
    """What the thread composer shows: the whole draft while it fits in three
    lines, else its END behind a leading '...' so the cursor is always visible
    and the user can always see what they are typing. The prompt '> ' and the
    one-column cursor block are part of the wrapped text."""
    def lines(t):
        return wrap_words('> ' + t + '#', COMPOSER_COLS)      # '#' stands for the cursor

    if len(lines(draft)) <= COMPOSER_LINES:
        return draft
    tail = draft[-(COMPOSER_COLS * COMPOSER_LINES):]
    for keep in range(len(tail), 0, -1):
        view = '...' + tail[-keep:]
        if len(lines(view)) <= COMPOSER_LINES:
            return view
    return '...'


def get_threads():
    """Group flat messages by the other party. Return every thread, newest
    first — the texts list windows over them, so there is no maximum stored
    count."""
    with state['lock']:
        msgs = list(state['messages'])

    thread_map = {}
    for i, m in enumerate(msgs):
        s = peer_of(m)
        if s is None:
            continue
        if s not in thread_map:
            thread_map[s] = {
                'sender': s,
                'name': format_name(s),
                'messages': [],
                'unread': False,
                '_last_i': i,
            }
        thread_map[s]['messages'].append(m)
        thread_map[s]['_last_i'] = i
        if not m['read']:
            thread_map[s]['unread'] = True

    sorted_threads = sorted(thread_map.values(), key=lambda t: t['_last_i'], reverse=True)
    for t in sorted_threads:
        del t['_last_i']
    return sorted_threads


def window_start(start, index, rows, total):
    """First item of a `rows`-tall window over `total` items that follows
    `index` one row at a time — it never pages, because on e-ink a page jump
    loses the reader's place — and always keeps `index` on screen."""
    last_start = max(0, total - rows)
    top = min(start, last_start)
    if index < top:
        top = index
    if index > top + rows - 1:
        top = index - rows + 1
    return max(0, min(top, last_start))


def _list_command(head, rows, shrink_order, floor=6):
    """Join `head` fields and row entries (lists of fields, joined by the
    field separator) into one command that fits MAX_COMMAND_CHARS.

    The column caps alone can overflow the frame on unlucky data (five
    max-length texts rows are ~267 chars), and build_payload would then cut the
    last row mid-field. Instead, shorten the fields named in `shrink_order`
    (longest first, one character at a time, never below `floor`) so every
    row survives whole."""
    def build():
        return "|".join(head + ["\xb7".join(r) for r in rows])

    cmd = build()
    while len(cmd) > MAX_COMMAND_CHARS:
        for f in shrink_order:
            longest = max(rows, key=lambda r: len(r[f]), default=None)
            if longest is not None and len(longest[f]) > floor:
                longest[f] = longest[f][:-1]
                break
        else:
            break    # nothing left to shorten; build_payload truncates
        cmd = build()
    return cmd


# ─── SPI ──────────────────────────────────────────────────────────────────────

def wait_for_ready(timeout_s=10):
    if SIM_MODE:
        return True
    t0 = time.monotonic()
    while int(handshake.get_value()) == 0:
        if time.monotonic() - t0 > timeout_s:
            return False
        time.sleep(0.01)
    return True


def wait_for_taken(timeout_s=3):
    """After a frame has gone out, wait for the Inkplate to say it has taken it: the ready line drops. The firmware
    ends a frame after 600 ms of clock silence and only then pulls the line low, so until it drops a second frame
    sent straight away runs into the first one (the extra bits are lost). Returns False if it never dropped."""
    if SIM_MODE:
        return True
    t0 = time.monotonic()
    while int(handshake.get_value()) == 1:
        if time.monotonic() - t0 > timeout_s:
            return False
        time.sleep(0.01)
    return True


def build_payload(text):
    payload = [0x00, 0x00, 0x02] + [ord(c) for c in text[:PAYLOAD_BYTES - 3]]
    payload += [0x00] * (PAYLOAD_BYTES - len(payload))
    return payload


def push_screen(command):
    """Queue a screen command. Non-blocking — the actual SPI transfer happens
    on a dedicated sender thread (see _spi_sender_loop), so a caller (e.g.
    handle_key, invoked directly from the keyboard/trackpad's read loop)
    never blocks on hardware I/O. If commands arrive faster than the SPI
    transfer + e-ink refresh can keep up (~1s each), only the latest one
    is kept — a fast burst of input coalesces to the final state instead
    of rendering every intermediate frame."""
    print(f"  → {command[:80]}")
    if SIM_MODE:
        simulator.render(command)
        return
    global _pending_command
    with _pending_lock:
        _pending_command = command
    _pending_event.set()


def push_page(frames):
    """Queue one whole page of book text: a list of frames sent back to back. It is one queue item, so a newer
    page replaces it rather than interleaving; and the Inkplate only refreshes on the last frame (RFOOT), so a page
    abandoned part-way is never shown half-drawn."""
    print(f"  → page of {len(frames)} frames: {frames[0][:60]}")
    if SIM_MODE:
        simulator.render_page(frames)
        return
    global _pending_command
    with _pending_lock:
        _pending_command = list(frames)
    _pending_event.set()


def _spi_sender_loop():
    global _pending_command
    while state['running']:
        _pending_event.wait()
        with _pending_lock:
            command = _pending_command
            _pending_command = None
            _pending_event.clear()
        if command is None:
            continue
        with _spi_lock:
            _send_command(command)


def _send_command(command):
    """Send one command, or a page (a list of frames) in order. If a newer command arrives while a page is going
    out, the rest of that page is dropped: nothing shows until its last frame, so the panel simply moves on."""
    frames = command if isinstance(command, list) else [command]
    for k, frame in enumerate(frames):
        if k and _pending_event.is_set():
            print(f"  (page abandoned after {k} of {len(frames)} frames: a newer one is waiting)")
            return
        if not wait_for_ready():
            print(f"Warning: Inkplate not ready, skipping: {frame[:40]}")
            return
        spi.xfer2(build_payload(frame))
        if not wait_for_taken():
            print(f"Warning: Inkplate never signalled busy after: {frame[:40]}")


# ─── Screen Builders ──────────────────────────────────────────────────────────

def push_lock():
    now = datetime.now()
    time_str = now.strftime("%-I:%M %p")
    date_str = now.strftime("%A, %B %-d").upper()
    quote = QUOTES[state['quote_index'] % len(QUOTES)]
    # Truncate quote to fit within PAYLOAD_BYTES (prefix + separators ≈ 30 chars overhead)
    mark  = '*' if _new_activity() else ''
    with state['lock']:
        light = state['light']
    tail  = f"|{VERSION}|{mark}|{light}"
    max_quote = PAYLOAD_BYTES - 3 - len("LOCK|") - len(time_str) - len(date_str) - len("- THICH NHAT HANH") - len(tail) - 4
    push_screen(f"LOCK|{time_str}|{date_str}|{quote[:max_quote]}|- THICH NHAT HANH{tail}")


def _new_activity():
    """The lock screen's *: an unread text, or a missed call not yet seen on the call list (unless switched off)."""
    with state['lock']:
        if not state['activity_mark']:
            return False
        unread = any(not m.get('read', True) and not is_outgoing(m) for m in state['messages'])
        missed = any(c['tag'] == 'MISS' and not c.get('seen', True) for c in state['calls'])
    return unread or missed


def push_home2():
    now = datetime.now()
    time_str = now.strftime("%-I:%M %p")
    with state['lock']:
        unread = sum(1 for m in state['messages'] if not m['read'])
        home_index = state['home_index']
    playing = 1 if (_music is not None and _music.playing) else 0        # the equalizer mark on the music row
    push_screen(f"HOME2|{time_str}|{home_index}|{unread}|{HOME_STYLE}|{playing}")


def _settle_texts_selection(threads):
    """An empty list has no row to select, so its selection lives in the header, on `+` (the one useful action:
    PRESS + TO WRITE THE FIRST MESSAGE). Returns (index, header_sel)."""
    with state['lock']:
        if not threads and state['texts_index'] >= 0:
            state['texts_index'] = -1
            state['texts_header_sel'] = 'plus'
        return state['texts_index'], state['texts_header_sel']


def push_texts():
    threads = get_threads()
    idx, hdr = _settle_texts_selection(threads)

    # Clamp row index
    if idx >= 0 and threads:
        idx = min(idx, len(threads) - 1)

    # Window over the whole list; the header counts as row 0 for scrolling.
    with state['lock']:
        start = window_start(state['texts_start'], max(0, idx), TEXTS_ROWS, len(threads))
        state['texts_start'] = start

    # Encode the selection: -1=back button, -2=plus button, else the row
    # within the window (0..TEXTS_ROWS-1).
    if idx == -1:
        send_idx = -2 if hdr == 'plus' else -1
    else:
        send_idx = idx - start

    rows = []
    for t in threads[start:start + TEXTS_ROWS]:
        last    = t['messages'][-1] if t['messages'] else None
        name    = sanitize(t['name'])[:LIST_NAME_MAX]
        # 'You: ' for a message that left the phone, '! ' while the last one is unsent
        prefix  = ''
        if last and is_outgoing(last):
            prefix = '! ' if last.get('state') == 'not_sent' else 'You: '
        preview = sanitize(prefix + last['body'])[:PREVIEW_MAX] if last else ''
        unread  = '1' if t['unread'] else '0'
        time_str = format_msg_time(last.get('ts')) if last else ''
        rows.append([name, preview, unread, time_str])
    # No rows = empty list: the renderer shows the NO CONVERSATIONS state.
    push_screen(_list_command(["TEXTS", str(send_idx)], rows, shrink_order=(1, 0)))


def _thread_messages(peer):
    """Every message with `peer`, oldest first."""
    with state['lock']:
        return [m for m in state['messages'] if peer_of(m) == peer]


# Bubble codes on the wire: R = received; Y0 sending, Y1 sent, Y2 not sent,
# Y3 not sent AND selected (the retry prompt). The renderer builds the label.
_BUBBLE_CODE = {'sending': 'Y0', 'sent': 'Y1', 'not_sent': 'Y2'}


def _thread_command(head, entries):
    """Join the thread fields into one command that fits the frame. When it is
    tight the OLDEST bubble goes first (never the selected one); if the newest
    alone is still too long its text is cut with '...'."""
    def build():
        return "|".join(head + ["\xb7".join(e) for e in entries])

    cmd = build()
    while len(cmd) > MAX_COMMAND_CHARS and len(entries) > 1:
        drop = next((i for i, e in enumerate(entries[:-1]) if e[0] != 'Y3'), 0)
        del entries[drop]
        cmd = build()
    if len(cmd) > MAX_COMMAND_CHARS and entries:
        text = entries[-1][2]
        keep = max(0, len(text) - (len(cmd) - MAX_COMMAND_CHARS) - 3)
        entries[-1][2] = text[:keep] + '...'
        cmd = build()
    return cmd


def push_thread2():
    with state['lock']:
        thread_id  = state['thread_id']
        draft      = state['thread_draft']
        header_sel = state['thread_header_sel']
        msg_sel    = state['thread_msg_sel']

    shown = _thread_messages(thread_id)[-THREAD_BUBBLES:]
    name  = sanitize(format_name(thread_id))[:THREAD_NAME_MAX] if thread_id else ''
    hdr   = {'back': 'B', 'info': 'I'}.get(header_sel, '')

    entries = []
    for i, m in enumerate(shown):
        if not str(m['body']).strip():
            continue
        if is_outgoing(m):
            code = _BUBBLE_CODE.get(m.get('state'), 'Y1')
            if code == 'Y2' and i == msg_sel:
                code = 'Y3'
        else:
            code = 'R'
        entries.append([code, format_msg_time(m.get('ts')), sanitize(m['body'])])

    push_screen(_thread_command(["THREAD2", name, sanitize(composer_view(draft)), hdr], entries))


def push_compose():
    with state['lock']:
        to_raw    = state['compose_to']
        msg       = state['compose_msg']
        to_active = '1' if state['compose_to_active'] else '0'
        hdr       = 'X' if state['compose_header_sel'] == 'x' else ''
        plus_sel  = '1' if state['compose_plus_sel'] else '0'
        send_sel  = '1' if state['compose_send_sel'] else '0'
    # A number that resolves to a saved contact displays as their name —
    # "the interactive reference drops the name into the TO field" — while
    # sending still uses the underlying number captured in compose_to.
    to_c = find_contact(number=to_raw) if to_raw else None
    to_display = sanitize(dispname(to_c) if to_c else to_raw)[:40]
    # The message has no cap, but the frame does: past what fits, show the END
    # of it behind '...' (the same rule as the thread composer).
    tail = f"|{to_active}|{hdr}|{plus_sel}|{send_sel}"
    room = MAX_COMMAND_CHARS - len(f"COMPOSE|{to_display}|") - len(tail)
    msg  = sanitize(msg)
    if len(msg) > room:
        msg = '...' + msg[-(room - 3):]
    push_screen(f"COMPOSE|{to_display}|{msg}{tail}")


def push_stub():
    with state['lock']:
        key  = state['stub_key']
        text = state['stub_text']
    if text:
        title, body = text
    else:
        info = STUB_INFO.get(key, {'title': key, 'body': f'{key} CANNOT OPEN YET.'})
        title, body = info['title'], info['body']
    push_screen(f"STUB|{sanitize(title)}|{sanitize(body)}")


def _show_alert(key, ret, **fields):
    """Raise a stop alert (boxed exclamation, OK bottom right). Enter or Esc
    dismisses it and returns to `ret` with everything as it was."""
    title, body = ALERTS[key]
    with state['lock']:
        state['screen']      = 'stub'
        state['stub_key']    = key
        state['stub_text']   = (title, body.format(**fields))
        state['stub_return'] = ret
    push_stub()


def push_confirm():
    """One confirmation layout for every destructive choice: the destructive
    button is on the left, the safe one on the right, and the safe one holds
    the selection when the screen opens."""
    with state['lock']:
        kind = state['confirm_kind']
        sel  = state['confirm_sel']
        eidx = state['edit_idx']
    if kind in ('forget_wifi', 'bt_device'):
        title, body, go, keep = _net_confirm_text(kind)
    elif kind == 'delete_note':
        title, body = 'NOTE', 'DELETE THIS NOTE? IT CANNOT BE BROUGHT BACK.'
        go, keep = 'DELETE', 'KEEP NOTE'
    elif kind == 'delete_contact':
        name = sanitize(dispname(CONTACTS[eidx])).upper() if eidx is not None and 0 <= eidx < len(CONTACTS) else ''
        title = 'DELETE CONTACT'
        body  = (f'DELETE {name}? THE MESSAGES STAY IN THE TEXT LIST, LABELED WITH THE NUMBER. '
                 'THE NAME CANNOT BE BROUGHT BACK.')
        go, keep = 'DELETE', 'KEEP CONTACT'
    else:
        title = 'NEW MESSAGE'
        body  = ('DISCARD THIS MESSAGE? IT HAS NOT BEEN SENT, AND THE PHONE KEEPS NO DRAFTS, '
                 'SO THE TEXT CANNOT BE BROUGHT BACK.')
        go, keep = 'DISCARD', 'KEEP EDITING'
    push_screen(f"CONFIRM|{title}|{body}|{go}|{keep}|{'D' if sel == 'go' else 'K'}")


def _filtered_contacts():
    with state['lock']:
        query = state['contacts_query'].lower()
    return [c for c in CONTACTS if dispname(c).lower().startswith(query)]


def _settle_contacts_selection():
    """With no contacts at all (and nothing typed) there is no row to select, so the selection lives in the header on
    `+`: PRESS + TO SAVE THE FIRST ONE. Returns (index, header_sel, query)."""
    with state['lock']:
        if not CONTACTS and not state['contacts_query'] and state['contacts_index'] >= 0:
            state['contacts_index']      = -1
            state['contacts_header_sel'] = 'plus'
        return state['contacts_index'], state['contacts_header_sel'], state['contacts_query']


def push_contacts():
    idx, hdr, query = _settle_contacts_selection()
    filtered = _filtered_contacts()
    with state['lock']:
        start = window_start(state['contacts_start'], max(0, idx), CONTACTS_ROWS, len(filtered))
        state['contacts_start'] = start

    if idx == -1:
        send_idx = -2 if hdr == 'plus' else -1
    else:
        send_idx = idx - start

    # Footer counter, e.g. "3 / 14"; empty when there is nothing to count.
    position = f"{max(1, idx + 1)} / {len(filtered)}" if filtered else ''
    rows = [[sanitize(dispname(c))[:CONTACT_NAME_MAX], format_number(c.get('number', ''))]
            for c in filtered[start:start + CONTACTS_ROWS]]
    # No rows = the renderer shows NO MATCH (a query is set) or NO CONTACTS.
    push_screen(_list_command(["CONTACTSPICK", str(send_idx), query, position], rows, shrink_order=(0,)))


def _contact_view():
    """(record, number, kind) for the contact page. kind: 'S' saved, 'N' saved
    with no number, 'U' a number that is not in the address book."""
    with state['lock']:
        idx    = state['contact_idx']
        number = state['contact_number']
    if idx is not None and 0 <= idx < len(CONTACTS):
        rec = CONTACTS[idx]
        num = rec.get('number', '')
        return rec, num, ('S' if num else 'N')
    return None, number, 'U'


def _contact_label():
    """Name (or formatted number) of the contact being viewed."""
    rec, number, _ = _contact_view()
    return dispname(rec) if rec else format_number(number)


# Contact page controls: the top row, then the action row underneath.
_CONTACT_TOP = {'S': ['back', 'edit'], 'N': ['back', 'edit'], 'U': ['back']}
_CONTACT_ROW = {'S': ['call', 'text'], 'N': ['addnum'], 'U': ['call', 'text', 'save']}
_CONTACT_CODE = {'back': 'B', 'edit': 'E', 'call': 'C', 'text': 'T', 'save': 'V', 'addnum': 'A'}


def _open_contact_page(idx, number, ret):
    """Show the contact page for CONTACTS[idx], or for an unsaved `number`."""
    with state['lock']:
        state['contact_idx']    = idx
        state['contact_number'] = number if idx is None else ''
        state['contact_return'] = ret
        state['screen']         = 'contact'
    _, _, kind = _contact_view()
    with state['lock']:
        state['contact_sel'] = _CONTACT_ROW[kind][0]
    push_contact()


def push_contact():
    rec, number, kind = _contact_view()
    with state['lock']:
        sel = state['contact_sel']
    if rec:
        title = dispname(rec)
        sub   = format_number(number) if number else 'NO NUMBER SAVED'
    else:
        title, sub = format_number(number), 'NOT IN CONTACTS'
    if sel not in _CONTACT_TOP[kind] + _CONTACT_ROW[kind]:
        sel = _CONTACT_ROW[kind][0]
    # 48px title: at most 15 cells across the panel
    push_screen(f"CONTACT|{sanitize(title)[:15]}|{sanitize(sub)}|{kind}|{_CONTACT_CODE[sel]}")


def push_contact_edit():
    with state['lock']:
        first  = state['edit_first']
        last   = state['edit_last']
        number = state['edit_number']
        idx    = state['edit_index']
        kind   = 'N' if state['edit_idx'] is None else 'E'
    push_screen(f"CONTACTEDIT|{sanitize(first)}|{sanitize(last)}|{sanitize(number)}|{idx}|{kind}")


def push_calls():
    with state['lock']:
        idx   = state['calls_index']
        calls = list(state['calls'])
    # DIAL A NUMBER is the first entry of the list, so it scrolls like any row.
    entries = [('DIAL A NUMBER', 'NEW', '', '')] + [
        (c['name'][:LIST_NAME_MAX], c['tag'], _call_time(c), c['duration']) for c in calls]
    with state['lock']:
        start = window_start(state['calls_start'], max(0, idx), CALLS_ROWS, len(entries))
        state['calls_start'] = start
    send_idx = idx if idx < 0 else idx - start
    rows = [list(e) for e in entries[start:start + CALLS_ROWS]]
    push_screen(_list_command(["CALLS", str(send_idx)], rows, shrink_order=(0,)))


def _quick_dial_names(n=4):
    return [dispname(c) for c in CONTACTS[:n]]


def push_dial():
    with state['lock']:
        buf  = state['dial_buffer']
        qidx = state['dial_quick_index']
    parts = [buf, str(qidx)] + _quick_dial_names()
    push_screen("DIAL|" + "|".join(parts))


def push_call_screen():
    with state['lock']:
        screen  = state['screen']
        name    = state['call_name']
        started = state['call_started_at']
    code = {'outgoing': 'OUT', 'incoming': 'IN'}.get(screen, 'ACTIVE')
    elapsed = int(time.time() - started) if (code == 'ACTIVE' and started) else 0
    mm, ss = divmod(elapsed, 60)
    push_screen(f"CALLSTATE|{code}|{name}|{mm:02d}:{ss:02d}")


def _push_for_screen(screen_name):
    """Push the wire command for whatever screen we just navigated to —
    used where a transition's destination is data-driven (e.g. stub's
    return screen) rather than a fixed literal."""
    pushers = {
        'home': push_home2, 'lock': push_lock, 'texts_list': push_texts,
        'thread': push_thread2, 'compose': push_compose, 'stub': push_stub,
        'confirm': push_confirm, 'contacts_pick': push_contacts,
        'contact': push_contact, 'contact_edit': push_contact_edit,
        'calls_list': push_calls, 'dial': push_dial,
        'outgoing': push_call_screen, 'incoming': push_call_screen,
        'in_call': push_call_screen,
        'library': push_library, 'reader': lambda: push_reader(force_full=True),
        'music': push_music, 'tracks': push_tracks, 'nowplaying': push_nowplaying,
        'notes_list': push_notes, 'note': push_note,
        'settings': push_settings, 'light': push_light, 'wifi': push_net, 'bluetooth': push_net, 'btpair': push_net,
        'netpass': push_netpass, 'netstate': push_netstate,
    }
    pusher = pushers.get(screen_name)
    if pusher:
        pusher()


# ─── State Machine ────────────────────────────────────────────────────────────

# Screens where printable characters are real typed input, so Q/WASD must
# stay literal there instead of acting as back/arrow shortcuts.
TYPING_SCREENS = ('thread', 'compose', 'contacts_pick', 'contact_edit', 'netpass', 'note')


def handle_key(keycode):
    with state['lock']:
        screen = state['screen']
    print(f"[handle_key] screen={screen} keycode={keycode}")

    # 'Q' is a universal back/up shortcut (same as Esc), and WASD mirrors
    # the arrow keys, except where they're needed as real typed characters.
    if screen not in TYPING_SCREENS:
        if keycode in ('CHAR:q', 'CHAR:Q'):
            keycode = 'KEY_ESC'
        elif keycode in ('CHAR:w', 'CHAR:W'):
            keycode = 'KEY_UP'
        elif keycode in ('CHAR:a', 'CHAR:A'):
            keycode = 'KEY_LEFT'
        elif keycode in ('CHAR:s', 'CHAR:S'):
            keycode = 'KEY_DOWN'
        elif keycode in ('CHAR:d', 'CHAR:D'):
            keycode = 'KEY_RIGHT'
    elif keycode.startswith('CHAR:') and not can_draw(keycode[5:]):
        return    # the panel cannot draw it (or it is a field separator): ignored, silently

    if screen == 'lock':
        _from_lock(keycode)

    elif screen == 'home':
        _from_home(keycode)

    elif screen == 'texts_list':
        _from_texts_list(keycode)

    elif screen == 'thread':
        _from_thread(keycode)

    elif screen == 'compose':
        _from_compose(keycode)

    elif screen == 'confirm':
        _from_confirm(keycode)

    elif screen == 'contacts_pick':
        _from_contacts_pick(keycode)

    elif screen == 'contact':
        _from_contact(keycode)

    elif screen == 'contact_edit':
        _from_contact_edit(keycode)

    elif screen == 'calls_list':
        _from_calls_list(keycode)

    elif screen == 'dial':
        _from_dial(keycode)

    elif screen == 'outgoing':
        _from_outgoing(keycode)

    elif screen == 'incoming':
        _from_incoming(keycode)

    elif screen == 'in_call':
        _from_in_call(keycode)

    elif screen == 'library':
        _from_library(keycode)

    elif screen == 'reader':
        _from_reader(keycode)

    elif screen == 'music':
        _from_music(keycode)

    elif screen == 'tracks':
        _from_tracks(keycode)

    elif screen == 'nowplaying':
        _from_nowplaying(keycode)

    elif screen == 'settings':
        _from_settings(keycode)

    elif screen in ('wifi', 'bluetooth', 'btpair'):
        _from_net(keycode)

    elif screen == 'light':
        _from_light(keycode)

    elif screen == 'notes_list':
        _from_notes(keycode)

    elif screen == 'note':
        _from_note(keycode)

    elif screen == 'netpass':
        _from_netpass(keycode)

    elif screen == 'netstate':
        _from_netstate(keycode)

    elif screen == 'stub':
        if keycode in ('KEY_ESC', 'KEY_ENTER'):
            with state['lock']:
                target = state['stub_return']
                state['screen'] = target
            _push_for_screen(target)


def _from_lock(keycode):
    with state['lock']:
        state['screen'] = 'home'
        state['home_index'] = 0
    push_home2()


def _from_home(keycode):
    if keycode in ('KEY_DOWN', 'KEY_RIGHT'):
        with state['lock']:
            old = state['home_index']
            state['home_index'] = min(len(HOME_MENU) - 1, old + 1)
            changed = state['home_index'] != old
        if changed:
            push_home2()
    elif keycode in ('KEY_UP', 'KEY_LEFT'):
        with state['lock']:
            old = state['home_index']
            state['home_index'] = max(-1, old - 1)  # -1 = header selected
            changed = state['home_index'] != old
        if changed:
            push_home2()
    elif keycode == 'KEY_ENTER':
        with state['lock']:
            idx = state['home_index']
        if idx == -1:  # header — same as Esc
            with state['lock']:
                state['screen'] = 'lock'
                state['quote_index'] += 1
            push_lock()
        elif HOME_MENU[idx] == 'TEXT':
            with state['lock']:
                state['screen']           = 'texts_list'
                state['texts_index']      = 0
                state['texts_start']      = 0
                state['texts_header_sel'] = 'back'
            push_texts()
        elif HOME_MENU[idx] == 'CALL':
            with state['lock']:
                state['screen']      = 'calls_list'
                state['calls_index'] = 0
                state['calls_start'] = 0
                unseen = [c for c in state['calls'] if c.get('seen') is False]
                for c in unseen:
                    c['seen'] = True                            # opening the call list counts as seeing them
            if unseen:
                _save_calls()
            push_calls()
        elif HOME_MENU[idx] == 'CONTACTS':
            with state['lock']:
                state['screen']              = 'contacts_pick'
                state['contacts_query']      = ''
                state['contacts_index']      = 0
                state['contacts_start']      = 0
                state['contacts_header_sel'] = 'back'
                state['contacts_return']     = 'home'
            push_contacts()
        elif HOME_MENU[idx] == 'READ':
            _open_library()
        elif HOME_MENU[idx] == 'LISTEN':
            _open_music()
        elif HOME_MENU[idx] == 'NOTES':
            _open_notes()
        elif HOME_MENU[idx] == 'SETTINGS':
            _open_settings()
    elif keycode == 'CHAR:i':
        # Demo shortcut: simulate an incoming call.
        with state['lock']:
            caller = dispname(CONTACTS[0]) if CONTACTS else 'Unknown Caller'
            state['screen']    = 'incoming'
            state['call_name'] = caller
        push_call_screen()
    elif keycode == 'KEY_ESC':
        with state['lock']:
            state['screen'] = 'lock'
            state['quote_index'] += 1
        push_lock()


def _from_texts_list(keycode):
    threads = get_threads()
    idx, hdr = _settle_texts_selection(threads)
    max_idx = max(0, len(threads) - 1)

    if keycode == 'KEY_DOWN':
        changed = False
        if idx == -1:
            # From header → first row (an empty list has none: stay on the header)
            if threads:
                with state['lock']:
                    state['texts_index'] = 0
                changed = True
        else:
            new_idx = min(idx + 1, max_idx)
            if new_idx != idx:
                with state['lock']:
                    state['texts_index'] = new_idx
                changed = True
        if changed:
            push_texts()

    elif keycode == 'KEY_UP':
        changed = False
        if idx == 0:
            with state['lock']:
                state['texts_index']      = -1
                state['texts_header_sel'] = 'back'
            changed = True
        elif idx > 0:
            with state['lock']:
                state['texts_index'] = idx - 1
            changed = True
        if changed:
            push_texts()

    elif keycode == 'KEY_RIGHT' and idx == -1:
        with state['lock']:
            changed = state['texts_header_sel'] != 'plus'
            state['texts_header_sel'] = 'plus'
        if changed:
            push_texts()

    elif keycode == 'KEY_LEFT' and idx == -1:
        with state['lock']:
            changed = state['texts_header_sel'] != 'back'
            state['texts_header_sel'] = 'back'
        if changed:
            push_texts()

    elif keycode == 'KEY_ENTER':
        if idx == -1 and hdr == 'back':
            with state['lock']:
                state['screen'] = 'home'
            push_home2()
        elif idx == -1 and hdr == 'plus':
            _open_compose()
        elif idx >= 0 and threads and idx < len(threads):
            _open_thread(threads[idx]['sender'])

    elif keycode in ('CHAR:+',):
        _open_compose()

    elif keycode in ('KEY_ESC', 'KEY_BACKSPACE'):
        with state['lock']:
            state['screen'] = 'home'
        push_home2()


def _open_thread(sender):
    with state['lock']:
        state['screen']            = 'thread'
        state['thread_id']         = sender
        state['thread_draft']      = ''
        state['thread_header_sel'] = None
        state['thread_msg_sel']    = -1
        for m in state['messages']:
            if peer_of(m) == sender:
                m['read'] = True
    save_messages()
    push_thread2()


def _open_compose():
    with state['lock']:
        state['screen']             = 'compose'
        state['compose_to']         = ''
        state['compose_msg']        = ''
        state['compose_to_active']  = True
        state['compose_header_sel'] = None
        state['compose_plus_sel']   = False
        state['compose_send_sel']   = False
    push_compose()


def _from_thread(keycode):
    with state['lock']:
        header_sel = state['thread_header_sel']
        msg_sel    = state['thread_msg_sel']
        thread_id  = state['thread_id']
    shown = _thread_messages(thread_id)[-THREAD_BUBBLES:]

    # A typed key while a bubble is selected first returns to the composer, so
    # nothing is ever typed into a composer that shows no cursor.
    if msg_sel >= 0 and (keycode == 'KEY_BACKSPACE' or keycode.startswith('CHAR:')):
        with state['lock']:
            state['thread_msg_sel'] = msg_sel = -1

    if keycode == 'KEY_ESC':
        with state['lock']:
            state['screen']         = 'texts_list'
            state['thread_msg_sel'] = -1
        push_texts()

    elif msg_sel >= 0 and keycode == 'KEY_UP':
        with state['lock']:
            state['thread_msg_sel']    = -1
            state['thread_header_sel'] = 'back'
        push_thread2()

    elif msg_sel >= 0 and keycode == 'KEY_DOWN':
        with state['lock']:
            state['thread_msg_sel'] = -1
        push_thread2()

    elif msg_sel >= 0 and keycode == 'KEY_ENTER':
        with state['lock']:
            state['thread_msg_sel'] = -1
        if msg_sel < len(shown):
            retry_message(shown[msg_sel])
        push_thread2()

    elif keycode == 'KEY_UP':
        if header_sel is None:
            # A not-sent message is the only thing in a transcript worth
            # selecting, so arrow up reaches the newest one before the header.
            retryable = [i for i, m in enumerate(shown)
                         if is_outgoing(m) and m.get('state') == 'not_sent']
            with state['lock']:
                if retryable:
                    state['thread_msg_sel'] = retryable[-1]
                else:
                    state['thread_header_sel'] = 'back'
            push_thread2()
        # already at the header — nothing further up

    elif header_sel is not None and keycode == 'KEY_DOWN':
        with state['lock']:
            state['thread_header_sel'] = None
        push_thread2()

    elif header_sel is not None and keycode == 'KEY_RIGHT':
        with state['lock']:
            changed = state['thread_header_sel'] != 'info'
            state['thread_header_sel'] = 'info'
        if changed:
            push_thread2()

    elif header_sel is not None and keycode == 'KEY_LEFT':
        with state['lock']:
            changed = state['thread_header_sel'] != 'back'
            state['thread_header_sel'] = 'back'
        if changed:
            push_thread2()

    elif header_sel is not None and keycode == 'KEY_ENTER':
        if header_sel == 'back':
            with state['lock']:
                state['screen'] = 'texts_list'
            push_texts()
        else:  # 'info' — open the contact page for this thread
            _open_contact_page(contact_index_for(thread_id), thread_id, 'thread')

    elif header_sel is None and keycode == 'KEY_BACKSPACE':
        with state['lock']:
            state['thread_draft'] = state['thread_draft'][:-1]
        push_thread2()

    elif header_sel is None and keycode == 'KEY_ENTER':
        with state['lock']:
            draft = state['thread_draft'].strip()
        if draft:
            with state['lock']:
                state['thread_draft'] = ''
            send_reply(thread_id, draft)
            push_thread2()
        else:
            _show_alert('EMPTY_SEND', 'thread')

    elif header_sel is None and keycode.startswith('CHAR:'):
        char = keycode[5:]
        with state['lock']:
            state['thread_draft'] += char
        push_thread2()


def _compose_has_draft():
    with state['lock']:
        return bool(state['compose_to'].strip() or state['compose_msg'].strip())


def _leave_compose():
    if _compose_has_draft():
        with state['lock']:
            state['screen']             = 'confirm'
            state['confirm_kind']       = 'discard_message'
            state['confirm_sel']        = 'keep'
            state['compose_header_sel'] = None
        push_confirm()
    else:
        with state['lock']:
            state['screen']             = 'texts_list'
            state['compose_header_sel'] = None
        push_texts()


def _send_compose():
    with state['lock']:
        to_val  = state['compose_to'].strip()
        msg_val = state['compose_msg'].strip()
    if not to_val:
        _show_alert('NO_RECIPIENT', 'compose')
        return
    if not number_valid(to_val):                           # a number that can never be texted (or saved as a contact)
        with state['lock']:
            state['compose_to_active']  = True             # put the cursor back on the number, ready to fix
            state['compose_header_sel'] = None
            state['compose_plus_sel']   = False
            state['compose_send_sel']   = False
        _show_alert('BAD_RECIPIENT', 'compose')
        return
    if not msg_val:
        _show_alert('EMPTY_SEND', 'compose')
        return
    send_reply(to_val, msg_val)
    peer = resolve_peer(to_val)          # takes the state lock, so not inside the block below
    with state['lock']:
        state['screen']            = 'thread'
        state['thread_id']         = peer
        state['thread_draft']      = ''
        state['thread_header_sel'] = None
        state['thread_msg_sel']    = -1
        state['compose_send_sel']  = False
    push_thread2()


def _from_compose(keycode):
    with state['lock']:
        header_sel = state['compose_header_sel']
        to_active  = state['compose_to_active']
        to_val     = state['compose_to']
        plus_sel   = state['compose_plus_sel']
        send_sel   = state['compose_send_sel']

    # Order matches the 0.2.1 interactive reference's handleKey. Arrow up moves
    # one step: SEND -> MESSAGE -> TO -> the X in the header. (0.2 sent every
    # arrow up straight to the header, so SEND could not go back to the message.)
    if keycode == 'KEY_ESC':
        _leave_compose()

    elif header_sel is None and not plus_sel and not send_sel and to_active and keycode == 'KEY_UP':
        with state['lock']:
            state['compose_header_sel'] = 'x'
        push_compose()

    elif header_sel is not None and keycode == 'KEY_DOWN':
        with state['lock']:
            state['compose_header_sel'] = None
        push_compose()

    elif header_sel is not None and keycode == 'KEY_ENTER':
        _leave_compose()

    elif header_sel is None and to_active and not to_val and keycode == 'KEY_RIGHT':
        with state['lock']:
            state['compose_plus_sel'] = True
        push_compose()

    elif header_sel is None and plus_sel and keycode == 'KEY_LEFT':
        with state['lock']:
            state['compose_plus_sel'] = False
        push_compose()

    elif header_sel is None and plus_sel and keycode == 'KEY_ENTER':
        with state['lock']:
            state['screen']           = 'contacts_pick'
            state['contacts_query']   = ''
            state['contacts_index']   = 0
            state['contacts_return']  = 'compose'
            state['compose_plus_sel'] = False
        push_contacts()

    elif header_sel is None and not to_active and not send_sel and keycode == 'KEY_DOWN':
        with state['lock']:
            state['compose_send_sel'] = True
        push_compose()

    elif header_sel is None and send_sel and keycode == 'KEY_UP':
        with state['lock']:
            state['compose_send_sel'] = False
        push_compose()

    elif header_sel is None and send_sel and keycode == 'KEY_ENTER':
        _send_compose()

    elif header_sel is None and keycode == 'KEY_TAB':
        with state['lock']:
            state['compose_to_active'] = not state['compose_to_active']
            state['compose_send_sel']  = False
        push_compose()

    elif header_sel is None and not to_active and keycode == 'KEY_UP':
        with state['lock']:
            state['compose_to_active'] = True               # MESSAGE -> TO
        push_compose()

    elif header_sel is None and keycode == 'KEY_ENTER':
        if to_active:
            if to_val.strip():
                with state['lock']:
                    state['compose_to_active'] = False
                push_compose()
            else:
                with state['lock']:
                    state['screen']          = 'contacts_pick'
                    state['contacts_query']  = ''
                    state['contacts_index']  = 0
                    state['contacts_return'] = 'compose'
                push_contacts()
        else:
            _send_compose()

    elif header_sel is None and keycode == 'KEY_BACKSPACE':
        with state['lock']:
            if state['compose_to_active']:
                state['compose_to'] = state['compose_to'][:-1]
            else:
                state['compose_msg'] = state['compose_msg'][:-1]
        push_compose()

    elif header_sel is None and keycode.startswith('CHAR:'):
        char = keycode[5:]
        with state['lock']:
            if state['compose_to_active']:
                state['compose_to']       = (state['compose_to'] + char)[:COMPOSE_TO_MAX]
            else:
                state['compose_msg']      = state['compose_msg'] + char
            state['compose_send_sel'] = False
        push_compose()


def _cancel_confirm():
    """Esc, or Enter on the safe button: back to where the question came from."""
    with state['lock']:
        kind   = state['confirm_kind']
        target = {'discard_message': 'compose', 'forget_wifi': 'wifi', 'bt_device': 'bluetooth',
                  'delete_note': 'note'}.get(kind, 'contact_edit')
        state['screen']      = target
        state['confirm_sel'] = 'keep'
    _push_for_screen(target)


def _delete_contact():
    """Remove the contact being edited. Its conversations are left alone: a
    thread is keyed on the number, so the row simply falls back to the
    formatted number — nothing disappears and nothing is orphaned."""
    with state['lock']:
        eidx = state['edit_idx']
    if eidx is not None and 0 <= eidx < len(CONTACTS):
        del CONTACTS[eidx]
        _save_contacts(CONTACTS)
    with state['lock']:
        state['screen']              = 'contacts_pick'
        state['contacts_query']      = ''
        state['contacts_index']      = 0
        state['contacts_start']      = 0
        state['contacts_header_sel'] = 'back'
        state['contacts_return']     = 'home'
        state['confirm_sel']         = 'keep'
        state['contact_idx']         = None
        state['contact_number']      = ''
        state['edit_idx']            = None
    push_contacts()


def _from_confirm(keycode):
    if keycode == 'KEY_LEFT':
        with state['lock']:
            state['confirm_sel'] = 'go'
        push_confirm()
    elif keycode == 'KEY_RIGHT':
        with state['lock']:
            state['confirm_sel'] = 'keep'
        push_confirm()
    elif keycode == 'KEY_ESC':
        _cancel_confirm()                          # Esc is the safe choice too
    elif keycode == 'KEY_ENTER':
        with state['lock']:
            kind = state['confirm_kind']
            sel  = state['confirm_sel']
        if sel != 'go' and kind == 'bt_device':
            with state['lock']:
                mac, name = state['net_mac'], state['net_name']
            _open_netstate_bt(mac, name, source='bluetooth')          # RE-CONNECT
        elif sel != 'go':
            _cancel_confirm()
        elif kind in ('forget_wifi', 'bt_device'):
            _net_confirm_go(kind)
        elif kind == 'delete_note':
            _delete_note()
        elif kind == 'delete_contact':
            _delete_contact()
        else:
            with state['lock']:
                state['screen']             = 'texts_list'
                state['compose_to']         = ''
                state['compose_msg']        = ''
                state['compose_header_sel'] = None
                state['confirm_sel']        = 'keep'
            push_texts()


def _leave_contacts():
    with state['lock']:
        ret = state['contacts_return']
    if ret == 'home':
        with state['lock']:
            state['screen']     = 'home'
            state['home_index'] = HOME_MENU.index('CONTACTS')
        push_home2()
    else:
        with state['lock']:
            state['screen']            = 'compose'
            state['compose_to_active'] = True
        push_compose()


def _from_contacts_pick(keycode):
    idx, hdr, query = _settle_contacts_selection()
    with state['lock']:
        ret   = state['contacts_return']
    filtered  = _filtered_contacts()
    on_header = idx == -1

    if keycode == 'KEY_ESC':
        _leave_contacts()
    elif keycode == 'KEY_UP':
        with state['lock']:
            state['contacts_index'] = max(-1, idx - 1)
        push_contacts()
    elif keycode == 'KEY_DOWN':
        with state['lock']:
            state['contacts_index'] = min(len(filtered) - 1, idx + 1)
        push_contacts()
    elif on_header and keycode == 'KEY_LEFT':
        with state['lock']:
            state['contacts_header_sel'] = 'back'
        push_contacts()
    elif on_header and keycode == 'KEY_RIGHT':
        with state['lock']:
            state['contacts_header_sel'] = 'plus'
        push_contacts()
    elif keycode == 'KEY_ENTER':
        if on_header:
            if hdr == 'back':
                _leave_contacts()
            else:
                _open_new_contact('', 'compose' if ret == 'compose' else 'contacts_pick')
        elif idx < len(filtered):
            picked = filtered[idx]
            if ret == 'home':
                pos = next(i for i, c in enumerate(CONTACTS) if c is picked)
                _open_contact_page(pos, '', 'contacts_pick')
            else:
                with state['lock']:
                    state['screen']            = 'compose'
                    state['compose_to']        = picked.get('number', '')
                    state['compose_to_active'] = False
                push_compose()
    elif keycode == 'KEY_BACKSPACE':
        with state['lock']:
            state['contacts_query'] = state['contacts_query'][:-1]
            state['contacts_index'] = 0
        push_contacts()
    elif keycode == 'CHAR:+' and not query:                # the + key, with nothing typed in the search: a new contact
        _open_new_contact('', 'compose' if ret == 'compose' else 'contacts_pick')
    elif keycode.startswith('CHAR:'):
        with state['lock']:
            state['contacts_query'] += keycode[5:]
            state['contacts_index'] = 0
        push_contacts()


def _open_text_for(number):
    """Open the existing thread with this number, or start composing to it if
    there is none yet — the contact page's TEXT button."""
    existing = next((t for t in get_threads() if same_number(t['sender'], number)), None)
    if existing:
        _open_thread(existing['sender'])
    else:
        with state['lock']:
            state['screen']             = 'compose'
            state['compose_to']         = number
            state['compose_msg']        = ''
            state['compose_to_active']  = False
            state['compose_header_sel'] = None
            state['compose_plus_sel']   = False
            state['compose_send_sel']   = False
        push_compose()


def _from_contact(keycode):
    rec, number, kind = _contact_view()
    top, row = _CONTACT_TOP[kind], _CONTACT_ROW[kind]
    with state['lock']:
        sel = state['contact_sel']
        ret = state['contact_return']
    if sel not in top + row:
        sel = row[0]
    in_top = sel in top

    def go(new_sel):
        with state['lock']:
            state['contact_sel'] = new_sel
        push_contact()

    if keycode == 'KEY_ESC':
        with state['lock']:
            state['screen'] = ret
        _push_for_screen(ret)
    elif keycode == 'KEY_UP':
        go(sel if in_top else top[min(row.index(sel), len(top) - 1)])
    elif keycode == 'KEY_DOWN':
        go(row[min(top.index(sel), len(row) - 1)] if in_top else sel)
    elif keycode in ('KEY_LEFT', 'KEY_RIGHT'):
        line = top if in_top else row
        step = -1 if keycode == 'KEY_LEFT' else 1
        go(line[max(0, min(len(line) - 1, line.index(sel) + step))])
    elif keycode == 'KEY_ENTER':
        if sel == 'back':
            with state['lock']:
                state['screen'] = ret
            _push_for_screen(ret)
        elif sel == 'text':
            _open_text_for(number)
        elif sel == 'call':
            label = _contact_label()         # takes the state lock, so not inside the block below
            with state['lock']:
                state['screen']    = 'outgoing'
                state['call_name'] = label
            push_call_screen()
        elif sel == 'save':
            _open_contact_edit(new=True)
        else:  # edit, addnum
            _open_contact_edit(new=False)


EDIT_FIELDS = ['first', 'last', 'number']
EDIT_SAVE   = len(EDIT_FIELDS)        # 3
EDIT_DELETE = EDIT_SAVE + 1           # 4 — bottom left, only when editing an existing contact


def _open_new_contact(number, ret):
    """The edit form with blank fields, titled NEW CONTACT. `number` fills the
    phone field (formatted); `ret` is where X / Esc leads, and where a save
    lands when the form was opened from the compose picker."""
    with state['lock']:
        state['screen']      = 'contact_edit'
        state['edit_idx']    = None
        state['edit_return'] = ret
        state['edit_first']  = ''
        state['edit_last']   = ''
        state['edit_number'] = format_number(number) if number else ''
        # A contact made for a conversation's own number keeps that number: change it and the name would not
        # attach to the conversation. (A number that cannot be dialed stays editable, so it can be fixed.)
        state['edit_number_locked'] = bool(number) and number_valid(number)
        state['edit_index']  = 0
    push_contact_edit()


def _open_contact_edit(new):
    """The edit form from the contact page. `new` = SAVE on a number that is
    not in the address book (a blank form with the number filled in);
    otherwise EDIT / ADD NUMBER on the viewed contact."""
    rec, number, _ = _contact_view()
    with state['lock']:
        cidx = state['contact_idx']
    if new or rec is None:
        _open_new_contact(number, 'contact')
        return
    with state['lock']:
        state['screen']      = 'contact_edit'
        state['edit_idx']    = cidx
        state['edit_return'] = 'contact'
        state['edit_first']  = rec.get('first', '')
        state['edit_last']   = rec.get('last', '')
        state['edit_number'] = rec.get('number', '')
        state['edit_number_locked'] = False
        state['edit_index']  = 0
    push_contact_edit()


def _leave_contact_edit():
    """X / Esc: back to wherever the form was opened from."""
    with state['lock']:
        ret = state['edit_return']
        state['screen'] = ret
        if ret == 'compose':
            state['compose_to_active'] = True
    _push_for_screen(ret)


def _edit_alert(key, field, **fields):
    """A validation alert; dismissing it returns to the form with the offending
    field selected."""
    with state['lock']:
        state['edit_index'] = field
    _show_alert(key, 'contact_edit', **fields)


def _save_contact_edit():
    with state['lock']:
        first    = state['edit_first'].strip()
        last     = state['edit_last'].strip()
        number   = state['edit_number'].strip()
        edit_idx = state['edit_idx']
        ret      = state['edit_return']

    # Four checks, in this order. Last name is optional.
    if not first:
        return _edit_alert('NEED_FIRST', 0)
    if not number:
        return _edit_alert('NEED_NUMBER', 2)
    if not number_valid(number):
        return _edit_alert('BAD_NUMBER', 2)
    dup = next((c for i, c in enumerate(CONTACTS)
                if i != edit_idx and same_number(c.get('number'), number)), None)
    if dup:                                            # same last ten digits = the same person
        return _edit_alert('DUP_NUMBER', 2, name=sanitize(dispname(dup)).upper())

    record = {'first': first, 'last': last, 'number': format_number(number)}
    if edit_idx is not None and 0 <= edit_idx < len(CONTACTS):
        CONTACTS[edit_idx].update(record)
        saved = CONTACTS[edit_idx]
    else:
        CONTACTS.append(record)
        CONTACTS.sort(key=lambda c: dispname(c).lower())      # the address book stays alphabetical
        saved = record
    _save_contacts(CONTACTS)

    if edit_idx is None and ret == 'compose':
        # Saved from the compose picker: back to the message being written,
        # with the new contact in the TO field.
        with state['lock']:
            state['screen']            = 'compose'
            state['compose_to']        = record['number']
            state['compose_to_active'] = False
        push_compose()
        return

    pos = next(i for i, c in enumerate(CONTACTS) if c is saved)
    with state['lock']:
        state['contact_idx']    = pos
        state['contact_number'] = ''
        state['screen']         = 'contact'
        state['contact_sel']    = 'call'
        if edit_idx is None and ret == 'contacts_pick':
            state['contact_return'] = 'contacts_pick'
    push_contact()


def _edit_step(idx, step, locked):
    """The form field one step from `idx`, stepping over the phone number when it is locked."""
    new = idx + step
    if locked and new == EDIT_FIELDS.index('number'):
        new += step
    return new


def _from_contact_edit(keycode):
    with state['lock']:
        idx        = state['edit_index']
        can_delete = state['edit_idx'] is not None      # a new contact has nothing to delete
        locked     = state['edit_number_locked']

    if keycode == 'KEY_ESC':
        _leave_contact_edit()
    elif keycode == 'KEY_UP':
        with state['lock']:
            state['edit_index'] = max(-1, _edit_step(EDIT_SAVE if idx == EDIT_DELETE else idx, -1, locked))
        push_contact_edit()
    elif keycode == 'KEY_DOWN':
        with state['lock']:
            state['edit_index'] = min(EDIT_SAVE, _edit_step(idx, +1, locked))
        push_contact_edit()
    elif keycode == 'KEY_LEFT' and idx == EDIT_SAVE and can_delete:
        with state['lock']:
            state['edit_index'] = EDIT_DELETE
        push_contact_edit()
    elif keycode == 'KEY_RIGHT' and idx == EDIT_DELETE:
        with state['lock']:
            state['edit_index'] = EDIT_SAVE
        push_contact_edit()
    elif keycode == 'KEY_ENTER':
        if idx == -1:
            _leave_contact_edit()
        elif idx == EDIT_SAVE:
            _save_contact_edit()
        elif idx == EDIT_DELETE:
            with state['lock']:
                state['screen']       = 'confirm'
                state['confirm_kind'] = 'delete_contact'
                state['confirm_sel']  = 'keep'
            push_confirm()
        else:
            with state['lock']:
                state['edit_index'] = _edit_step(idx, +1, locked)
            push_contact_edit()
    elif keycode == 'KEY_BACKSPACE' and 0 <= idx < len(EDIT_FIELDS) and not (locked and EDIT_FIELDS[idx] == 'number'):
        field = EDIT_FIELDS[idx]
        with state['lock']:
            state[f'edit_{field}'] = state[f'edit_{field}'][:-1]
        push_contact_edit()
    elif keycode.startswith('CHAR:') and 0 <= idx < len(EDIT_FIELDS):
        char  = keycode[5:]
        field = EDIT_FIELDS[idx]
        if field == 'number' and (locked or not re.match(r'^[0-9()+\-. ]$', char)):
            return                                     # a rejected keystroke is the one silent no-op
        limit = NUMBER_FIELD_MAX if field == 'number' else CONTACT_FIELD_MAX
        with state['lock']:
            state[f'edit_{field}'] = (state[f'edit_{field}'] + char)[:limit]
        push_contact_edit()


def _from_calls_list(keycode):
    with state['lock']:
        idx   = state['calls_index']
        calls = list(state['calls'])
    total = len(calls) + 1

    if keycode == 'KEY_UP':
        with state['lock']:
            state['calls_index'] = max(-1, idx - 1)
        push_calls()
    elif keycode == 'KEY_DOWN':
        with state['lock']:
            state['calls_index'] = min(total - 1, idx + 1)
        push_calls()
    elif keycode == 'KEY_ENTER':
        if idx == -1:
            with state['lock']:
                state['screen'] = 'home'
            push_home2()
        elif idx == 0:
            with state['lock']:
                state['screen']           = 'dial'
                state['dial_buffer']      = ''
                state['dial_quick_index'] = -1
            push_dial()
        else:
            with state['lock']:
                state['screen']    = 'outgoing'
                state['call_name'] = calls[idx - 1]['name']
            push_call_screen()
    elif keycode in ('KEY_ESC', 'KEY_BACKSPACE'):
        with state['lock']:
            state['screen'] = 'home'
        push_home2()


def _from_dial(keycode):
    with state['lock']:
        qidx = state['dial_quick_index']
        buf  = state['dial_buffer']
    names = _quick_dial_names()

    if keycode == 'KEY_ESC':
        with state['lock']:
            state['screen'] = 'calls_list'
        push_calls()
    elif keycode == 'KEY_DOWN':
        with state['lock']:
            state['dial_quick_index'] = min(len(names) - 1, qidx + 1)
        push_dial()
    elif keycode == 'KEY_UP':
        with state['lock']:
            state['dial_quick_index'] = max(-1, qidx - 1)
        push_dial()
    elif keycode == 'KEY_ENTER':
        if 0 <= qidx < len(names):
            with state['lock']:
                state['screen']    = 'outgoing'
                state['call_name'] = names[qidx]
            push_call_screen()
        elif buf.strip():
            with state['lock']:
                state['screen']    = 'outgoing'
                state['call_name'] = buf
            push_call_screen()
    elif keycode == 'KEY_BACKSPACE' and qidx == -1:
        with state['lock']:
            state['dial_buffer'] = state['dial_buffer'][:-1]
        push_dial()
    elif qidx == -1 and keycode.startswith('CHAR:') and re.match(r'^[0-9#*]$', keycode[5:]):
        with state['lock']:
            state['dial_buffer'] += keycode[5:]
        push_dial()


def _from_outgoing(keycode):
    if keycode == 'KEY_ESC':                               # cancelled before anyone answered
        _log_call('OUT')
        with state['lock']:
            state['screen'] = 'calls_list'
        push_calls()
    elif keycode == 'KEY_ENTER':
        with state['lock']:
            state['screen']          = 'in_call'
            state['call_dir']        = 'OUT'
            state['call_started_at'] = time.time()
        push_call_screen()


def _from_incoming(keycode):
    if keycode == 'KEY_ESC':                               # not answered: a missed call
        _log_call('MISS')
        with state['lock']:
            state['screen'] = 'home'
        push_home2()
    elif keycode == 'KEY_ENTER':
        with state['lock']:
            state['screen']          = 'in_call'
            state['call_dir']        = 'IN'
            state['call_started_at'] = time.time()
        push_call_screen()


def _from_in_call(keycode):
    if keycode == 'KEY_ESC':                               # hung up: log it with how long it lasted
        with state['lock']:
            tag, started = state['call_dir'], state['call_started_at']
        _log_call(tag, (time.time() - started) if started else 0)
        with state['lock']:
            state['screen']          = 'calls_list'
            state['call_started_at'] = None
        push_calls()


def _duration_text(seconds):
    """m:ss, or h:mm:ss for an hour or more."""
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _call_time(entry):
    """When a logged call happened, as the list shows it (clock time today, then Yesterday, a weekday, a date)."""
    return format_msg_time(entry.get('ts')) or entry.get('time', '')


def _log_call(tag, seconds=None):
    """Add a call to the log, newest first. tag: OUT (dialed), IN (answered), MISS (an incoming call nobody answered).
    `seconds` is how long it lasted; None = it never connected."""
    with state['lock']:
        raw = str(state['call_name'])
    if re.fullmatch(r'[0-9()+\-. #*]{7,}', raw) and digits(raw):    # dialed digits: show the contact, or the number formatted
        raw = format_name(raw)
    entry = {'name': sanitize(raw)[:30], 'tag': tag, 'ts': datetime.now().isoformat(),
             'duration': _duration_text(seconds) if seconds is not None else ''}
    if tag == 'MISS':
        entry['seen'] = False                                  # a * on the lock screen until the call list is opened
    with state['lock']:
        state['calls'].insert(0, entry)
        del state['calls'][CALL_LOG_MAX:]
    _save_calls()


def _save_calls():
    with state['lock']:
        data = list(state['calls'])
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = CALLS_FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(data, f)
        os.replace(tmp, CALLS_FILE)
    except OSError as e:
        print(f"Warning: could not save the call log: {e}")


def load_calls():
    """Read the call log back; anything that is not a well-formed entry is dropped."""
    try:
        with open(CALLS_FILE) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = []
    good = []
    for c in data if isinstance(data, list) else []:
        if (isinstance(c, dict) and isinstance(c.get('name'), str) and c.get('tag') in ('OUT', 'IN', 'MISS')
                and isinstance(c.get('duration', ''), str) and isinstance(c.get('ts', ''), str)):
            good.append({'name': c['name'], 'tag': c['tag'], 'ts': c.get('ts', ''), 'duration': c.get('duration', '')})
            if c['tag'] == 'MISS' and c.get('seen') is False:
                good[-1]['seen'] = False
    with state['lock']:
        state['calls'] = good[:CALL_LOG_MAX]


def load_settings():
    """The phone's own settings; anything missing or malformed keeps its default."""
    try:
        with open(SETTINGS_FILE) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    light = data.get('light')
    with state['lock']:
        if isinstance(light, int) and not isinstance(light, bool) and 0 <= light <= LIGHT_LEVELS:
            state['light'] = light
        if isinstance(data.get('activity_mark'), bool):
            state['activity_mark'] = data['activity_mark']


def save_settings():
    with state['lock']:
        data = {'light': state['light'], 'activity_mark': state['activity_mark']}
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = SETTINGS_FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(data, f)
        os.replace(tmp, SETTINGS_FILE)
    except OSError as e:
        print(f"Warning: could not save settings: {e}")


# ─── Persistence ──────────────────────────────────────────────────────────────

def load_messages():
    try:
        with open(MESSAGES_FILE, 'r') as f:
            data = json.load(f)
        state['messages'] = data.get('messages', [])
        # A send that was still in flight when the phone last stopped never finished.
        for m in state['messages']:
            if m.get('state') == 'sending':
                m['state'] = 'not_sent'
        print(f"Loaded {len(state['messages'])} messages.")
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"Warning: could not load messages: {e}")


def save_messages():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(MESSAGES_FILE, 'w') as f:
            json.dump({'messages': state['messages']}, f)
    except Exception as e:
        print(f"Warning: could not save messages: {e}")


# ─── Modem ────────────────────────────────────────────────────────────────────

_modem = None    # a modem.SerialModem, once _init_modem() succeeds at startup; None = no dongle configured


def _init_modem():
    """Bring up the real cellular modem once, at startup, if KYPHONE_MODEM_PORT is set — that
    environment variable is the deliberate opt-in, so the code never goes probing a serial port that
    just happens to be free for something else. Never raises: if the dongle is not there or not
    answering, _modem stays None and every send ends as 'no service' (NOT SENT)."""
    global _modem
    if SIM_MODE or not os.environ.get(modem.MODEM_PORT_ENV):
        return
    try:
        _modem = modem.SerialModem()
        print(f"Modem ready on {_modem.port}.")
        if not _modem.registered():
            print("Warning: the modem has not registered on the carrier's network yet.")
    except modem.ModemError as e:
        print(f"Warning: modem not available ({e}); texts will not send.")


def _transport_send(to_number, body):
    """Hand one text to the radio. Raises if it could not be sent.

    Sends through the real cellular modem if one came up at startup, else gives up with 'no service' —
    the phone's normal outcome until a modem with a working SIM is actually plugged in."""
    if SIM_MODE:
        time.sleep(SIM_SEND_DELAY)
        if SIM_SEND != 'sent':
            raise RuntimeError('simulated: no service')
        return
    if _modem is None:
        raise RuntimeError('no service')
    _modem.send(normalize_number(to_number), body)
    print(f"  → sent via modem: {body}")


# ─── Reader (books) ───────────────────────────────────────────────────────────
# READ opens the library: the .epub files in BOOKS_DIR, alphabetical by title, each with how far you have
# read. A book opens in the reader at the place you left it. The book is parsed and laid out here; the
# Inkplate is sent the finished lines of one page at a time (rl.page_frames) and refreshes on the last frame.
#
# A position is (chapter, offset) where the offset counts characters into the chapter, so it means the same
# thing at every font size. It is saved to READING_FILE after every page.

_reader_lock = threading.RLock()     # the keyboard and the trackpad each call handle_key from their own thread


def _load_reading():
    """READING_FILE as {'font': size, 'books': {id: {chapter, offset, pct}}}; anything unreadable is empty."""
    try:
        with open(READING_FILE) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    books = data.get('books')
    font = data.get('font')
    return {'font': font if font in rl.SIZES else DEFAULT_READER_SIZE,
            'books': books if isinstance(books, dict) else {}}


def _save_reading(reading):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = READING_FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(reading, f)
        os.replace(tmp, READING_FILE)
    except OSError as e:
        print(f"Warning: could not save reading position: {e}")


def _book_id(path):
    """A book's key in READING_FILE: its file name and size (renaming it starts it again from page one)."""
    try:
        return f"{os.path.basename(path)}:{os.path.getsize(path)}"
    except OSError:
        return os.path.basename(path)


def _scan_books():
    """[{path, id, title, author, pct, error}] for the .epub files in BOOKS_DIR, by title. Never raises: a file
    that cannot be read is still listed (by its file name) and says why when opened."""
    try:
        names = sorted(n for n in os.listdir(BOOKS_DIR) if n.lower().endswith('.epub') and not n.startswith('.'))
    except OSError:
        names = []
    saved = _load_reading()['books']
    books = []
    for name in names[:LIBRARY_MAX_BOOKS]:
        path = os.path.join(BOOKS_DIR, name)
        error = None
        try:
            title, author = reader_epub.read_info(path)
        except reader_epub.EpubError as e:
            title, author, error = reader_epub.to_drawable(os.path.splitext(name)[0]), '', str(e)
        except Exception:
            title, author, error = reader_epub.to_drawable(os.path.splitext(name)[0]), '', 'COULD NOT READ THE BOOK'
        bid = _book_id(path)
        rec = saved.get(bid)
        pct = rec.get('pct') if isinstance(rec, dict) and isinstance(rec.get('pct'), int) else None
        books.append({'path': path, 'id': bid, 'title': title, 'author': author, 'pct': pct, 'error': error})
    books.sort(key=lambda b: b['title'].lower())
    return books


def push_library():
    with state['lock']:
        idx = state['library_index']
        books = list(state['library_books'])
        start = window_start(state['library_start'], max(0, idx), LIBRARY_ROWS, len(books))
        state['library_start'] = start
    send_idx = idx if idx < 0 else idx - start
    rows = [[b['title'][:LIBRARY_TITLE_MAX], b['author'][:LIBRARY_AUTHOR_MAX],
             '' if b['pct'] is None else f"{b['pct']}%"] for b in books[start:start + LIBRARY_ROWS]]
    push_screen(_list_command(["LIBRARY", str(send_idx)], rows, shrink_order=(0, 1)))


def _open_library(select_id=None):
    books = _scan_books()
    idx = next((i for i, b in enumerate(books) if b['id'] == select_id), 0 if books else -1)
    with state['lock']:
        state['screen']        = 'library'
        state['library_books'] = books
        state['library_index'] = idx
        state['library_start'] = 0
    push_library()


def _from_library(keycode):
    with state['lock']:
        idx = state['library_index']
        books = list(state['library_books'])

    if keycode == 'KEY_UP':
        with state['lock']:
            state['library_index'] = max(-1, idx - 1)
        push_library()
    elif keycode == 'KEY_DOWN':
        with state['lock']:
            state['library_index'] = min(len(books) - 1, idx + 1)
        push_library()
    elif keycode == 'KEY_ENTER':
        if idx == -1 or not books:
            with state['lock']:
                state['screen'] = 'home'
            push_home2()
        else:
            _open_book(books[idx])
    elif keycode in ('KEY_ESC', 'KEY_BACKSPACE'):
        with state['lock']:
            state['screen'] = 'home'
        push_home2()


def _open_book(entry):
    if entry['error']:
        _show_alert('BAD_BOOK', 'library', reason=entry['error'])
        return
    try:
        book = reader_epub.load(entry['path'])
        first = book.first_with_text()
        saved = _load_reading()
        rec = saved['books'].get(entry['id'])
        chapter, offset = first, 0
        if isinstance(rec, dict) and isinstance(rec.get('chapter'), int) and isinstance(rec.get('offset'), int):
            if 0 <= rec['chapter'] < len(book) and rec['offset'] >= 0 and book.chapter(rec['chapter']).paras:
                chapter, offset = rec['chapter'], rec['offset']
    except reader_epub.EpubError as e:
        _show_alert('BAD_BOOK', 'library', reason=str(e))
        return
    with _reader_lock:
        with state['lock']:
            old = state['book']
            state['book']        = book
            state['r_id']        = entry['id']
            state['r_chapter']   = chapter
            state['r_offset']    = offset
            state['r_size']      = saved['font']
            state['r_pages']     = None
            state['r_pages_key'] = None
            state['r_turns']     = 0
            state['screen']      = 'reader'
        if old is not None:
            old.close()
        _push_reader_safely(force_full=True)


def _reader_view():
    """(chapter, pages, page index) at the current position, laying the chapter out if it is not already."""
    with state['lock']:
        book, ch, size, offset = state['book'], state['r_chapter'], state['r_size'], state['r_offset']
        key, pages = state['r_pages_key'], state['r_pages']
    chapter = book.chapter(ch)
    if key != (ch, size) or pages is None:
        pages = rl.paginate(chapter.paras, size)
        with state['lock']:
            state['r_pages'], state['r_pages_key'] = pages, (ch, size)
    return chapter, pages, rl.page_index(pages, offset)


def push_reader(force_full=False):
    """Draw the page at the current position, and save the position. A full refresh (which flashes) clears ghosting;
    it happens when a book opens, on a new chapter, a new font size, after an alert, and after every READER_FULL_EVERY partial turns (0: every turn)."""
    with _reader_lock:
        chapter, pages, idx = _reader_view()
        with state['lock']:
            book, ch, size, offset, book_id = state['book'], state['r_chapter'], state['r_size'], state['r_offset'], state['r_id']
            full = force_full or state['r_turns'] >= READER_FULL_EVERY
            state['r_turns'] = 0 if full else state['r_turns'] + 1
        frac = pages[idx].start / max(1, rl.chapter_length(chapter.paras))
        pct = int(round(100 * book.progress(ch, frac)))
        if ch == len(book) - 1 and idx == len(pages) - 1:
            pct = 100
        saved = _load_reading()
        saved['font'] = size
        saved['books'][book_id] = {'chapter': ch, 'offset': offset, 'pct': pct}
        _save_reading(saved)
        push_page(rl.page_frames(size, pages[idx].lines, chapter.title, f"{idx + 1}/{len(pages)}  {pct}%",
                                 'F' if full else 'P'))


def _push_reader_safely(force_full=False):
    """push_reader, but a chapter that turns out to be unreadable closes the book with an alert instead of crashing."""
    try:
        push_reader(force_full)
    except reader_epub.EpubError as e:
        _close_book('BAD_BOOK', reason=str(e))


def _close_book(alert=None, **fields):
    """Leave the reader for the library (with a stop alert, if given). The position is already saved."""
    with _reader_lock:
        with state['lock']:
            book, book_id = state['book'], state['r_id']
            state['book'], state['r_pages'], state['r_pages_key'] = None, None, None
        if book is not None:
            book.close()
    _open_library(select_id=book_id)
    if alert:
        _show_alert(alert, 'library', **fields)


_NEXT_PAGE = ('KEY_RIGHT', 'KEY_DOWN', 'KEY_ENTER', 'CHAR: ')
_PREV_PAGE = ('KEY_LEFT', 'KEY_UP', 'KEY_BACKSPACE')
_BIGGER    = ('CHAR:+', 'CHAR:=')
_SMALLER   = ('CHAR:-', 'CHAR:_')


def _from_reader(keycode):
    with _reader_lock:
        try:
            _reader_key(keycode)
        except reader_epub.EpubError as e:
            _close_book('BAD_BOOK', reason=str(e))


def _reader_key(keycode):
    if keycode == 'KEY_ESC':
        _close_book()
        return
    with state['lock']:
        book, ch, size = state['book'], state['r_chapter'], state['r_size']
    if book is None:                       # the book was closed under us; go to the library
        _open_library()
        return

    if keycode in _NEXT_PAGE:
        chapter, pages, idx = _reader_view()
        if idx + 1 < len(pages):
            with state['lock']:
                state['r_offset'] = pages[idx + 1].start
            push_reader()
            return
        nxt = book.next_with_text(ch, +1)
        if nxt is None:
            _show_alert('END_OF_BOOK', 'reader')
            return
        with state['lock']:
            state['r_chapter'], state['r_offset'] = nxt, 0
        push_reader(force_full=True)

    elif keycode in _PREV_PAGE:
        chapter, pages, idx = _reader_view()
        if idx > 0:
            with state['lock']:
                state['r_offset'] = pages[idx - 1].start
            push_reader()
            return
        prev = book.next_with_text(ch, -1)
        if prev is None:
            _show_alert('START_OF_BOOK', 'reader')
            return
        last_start = rl.paginate(book.chapter(prev).paras, size)[-1].start
        with state['lock']:
            state['r_chapter'], state['r_offset'] = prev, last_start
        push_reader(force_full=True)

    elif keycode in _BIGGER or keycode in _SMALLER:
        sizes = list(rl.SIZES)
        i = sizes.index(size) + (1 if keycode in _BIGGER else -1)
        if not 0 <= i < len(sizes):
            _show_alert('BIGGEST_FONT' if keycode in _BIGGER else 'SMALLEST_FONT', 'reader')
            return
        with state['lock']:
            state['r_size'] = sizes[i]
        push_reader(force_full=True)


# ─── Music (LISTEN) ───────────────────────────────────────────────────────────
# LISTEN opens the albums found in MUSIC_DIR (music_library.py); an album opens its tracks; a track starts playing
# the album from there and shows the now-playing screen. Music keeps playing when you leave (Esc), the home menu shows
# a small mark while it does, and LISTEN offers NOW PLAYING (or RESUME after a restart) as its first row.
#
# The player (music_player.py) makes the sound; this only decides what to show and what each key does. The e-ink is
# redrawn when something changes on the now-playing screen, and every MUSIC_TICK_SECONDS so the time moves; no other
# screen is ever redrawn by the player, except the home menu when the music finishes (so its mark does not lie).
#
# Keys on now-playing: Space/Enter play-pause, Right next, Left previous, Up/Down volume (so a trackpad can do it all),
# + and - volume, . and , seek 15 s forward/back, Esc/q back (the music goes on).

_music = None                 # the music_player.Session, made the first time something must play
_music_problem = None         # why there is no real player, if there is not
_music_lengths = {}           # path -> seconds from the last scan (the silent player needs them)
_music_tick_count = 0
_music_save_count = 0


def _load_listening():
    """LISTENING_FILE as {'volume': 0-100, 'last': {'path', 'position'} or None}; anything unreadable gives defaults."""
    try:
        with open(LISTENING_FILE) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    volume = data.get('volume')
    last = data.get('last')
    if not (isinstance(last, dict) and isinstance(last.get('path'), str) and isinstance(last.get('position'), (int, float))):
        last = None
    return {'volume': volume if isinstance(volume, int) and 0 <= volume <= 100 else music_player.DEFAULT_VOLUME,
            'last': last}


def _save_listening():
    """Remember the volume and where we are (so LISTEN can offer RESUME after a restart)."""
    sess = _music
    saved = _load_listening()
    now = sess.now() if sess is not None else None
    if sess is not None:
        saved['volume'] = sess.volume
    if now is not None:
        saved['last'] = {'path': now.track.path, 'position': 0 if now.state == 'stopped' else int(now.position)}
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = LISTENING_FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(saved, f)
        os.replace(tmp, LISTENING_FILE)
    except OSError as e:
        print(f"Warning: could not save listening state: {e}")


def _music_session():
    """The playback session, made on first use. None (and _music_problem says why) when the phone has no sound
    system: a silent stand-in is only ever used in the emulator, never quietly on the phone."""
    global _music, _music_problem
    if _music is not None:
        return _music
    player, problem = music_player.create_player(sim=SIM_MODE, clock=_MUSIC_CLOCK, duration_of=_music_lengths.get)
    if problem and not SIM_MODE:
        _music_problem = problem
        return None
    _music = music_player.Session(player, volume=_load_listening()['volume'],
                                  on_change=_music_changed, on_bad_track=_music_bad_track)
    return _music


def _music_changed(kind):
    """The session says something changed. Runs on whatever thread caused it, with no session lock held."""
    if kind in ('track', 'state', 'finished', 'volume'):
        _save_listening()
    with state['lock']:
        screen = state['screen']
    if screen == 'nowplaying':
        push_nowplaying()
    elif screen == 'home' and kind == 'finished':
        push_home2()                                       # the mark for "music is playing" goes out


def _music_bad_track(track, message):
    """A file would not play and was skipped. Only interrupt if the now-playing screen is what you are looking at."""
    with state['lock']:
        screen = state['screen']
    if screen == 'nowplaying':
        _show_alert('BAD_TRACK', 'nowplaying', title=sanitize(track.title)[:30], reason=sanitize(message)[:40])


def _find_track(lib, path):
    """(album, index) of the track with this file path, or None."""
    for album in (lib.albums if lib else []):
        for i, t in enumerate(album.tracks):
            if t.path == path:
                return album, i
    return None


def _music_rows():
    """The rows of the album list: NOW PLAYING (or RESUME) first when there is one, then every album A to Z."""
    with state['lock']:
        lib, last = state['music_lib'], state['music_last']
    rows = []
    now = _music.now() if _music is not None else None
    if now is not None:
        rows.append({'kind': 'now', 'cols': ['NOW PLAYING', f"{now.track.title} - {now.track.artist}",
                                            {'playing': 'PLAYING', 'paused': 'PAUSED'}.get(now.state, '')]})
    elif last is not None and lib is not None:
        found = _find_track(lib, last['path'])
        if found:
            track = found[0].tracks[found[1]]
            rows.append({'kind': 'resume', 'cols': ['RESUME', f"{track.title} - {track.artist}", '']})
    for album in (lib.albums if lib else []):
        rows.append({'kind': 'album', 'album': album, 'cols': [album.name, album.artist, f"{len(album.tracks)} trk"]})
    return rows


def _open_music():
    lib = music_library.scan(MUSIC_DIR, MUSIC_INDEX_FILE)
    _music_lengths.clear()
    _music_lengths.update({t.path: t.seconds for a in lib.albums for t in a.tracks if t.seconds})
    last = _load_listening()['last']
    with state['lock']:
        state['screen']     = 'music'
        state['music_lib']  = lib
        state['music_last'] = last
        state['music_start'] = 0
    state['music_index'] = 0 if _music_rows() else -1
    push_music()


def push_music():
    rows = _music_rows()
    with state['lock']:
        idx = min(state['music_index'], len(rows) - 1)
        state['music_index'] = idx
        start = window_start(state['music_start'], max(0, idx), MUSIC_ROWS, len(rows))
        state['music_start'] = start
    send_idx = idx if idx < 0 else idx - start
    shown = [[sanitize(c[0])[:MUSIC_TITLE_MAX], sanitize(c[1])[:MUSIC_SUB_MAX], c[2]] for c in
             (r['cols'] for r in rows[start:start + MUSIC_ROWS])]
    push_screen(_list_command(["MUSIC", str(send_idx)], shown, shrink_order=(0, 1)))


def _from_music(keycode):
    rows = _music_rows()
    with state['lock']:
        idx = state['music_index']
    if keycode == 'KEY_UP':
        with state['lock']:
            state['music_index'] = max(-1, idx - 1)
        push_music()
    elif keycode == 'KEY_DOWN':
        with state['lock']:
            state['music_index'] = min(len(rows) - 1, idx + 1)
        push_music()
    elif keycode == 'KEY_ENTER':
        if idx == -1 or not rows:
            with state['lock']:
                state['screen'] = 'home'
            push_home2()
        elif rows[idx]['kind'] == 'now':
            _open_nowplaying('music')
        elif rows[idx]['kind'] == 'resume':
            _resume_last()
        else:
            _open_tracks(rows[idx]['album'])
    elif keycode in ('KEY_ESC', 'KEY_BACKSPACE'):
        with state['lock']:
            state['screen'] = 'home'
        push_home2()


def _open_tracks(album):
    with state['lock']:
        state['screen']       = 'tracks'
        state['album']        = album
        state['tracks_index'] = 0
        state['tracks_start'] = 0
    push_tracks()


def push_tracks():
    with state['lock']:
        album, idx = state['album'], state['tracks_index']
        start = window_start(state['tracks_start'], max(0, idx), MUSIC_ROWS, len(album.tracks))
        state['tracks_start'] = start
    send_idx = idx if idx < 0 else idx - start
    shown = [[sanitize(t.title)[:MUSIC_TITLE_MAX], sanitize(t.artist)[:MUSIC_SUB_MAX], t.time()]
             for t in album.tracks[start:start + MUSIC_ROWS]]
    push_screen(_list_command(["TRACKS", str(send_idx), sanitize(album.name)[:20]], shown, shrink_order=(0, 1)))


def _from_tracks(keycode):
    with state['lock']:
        album, idx = state['album'], state['tracks_index']
    if keycode == 'KEY_UP':
        with state['lock']:
            state['tracks_index'] = max(-1, idx - 1)
        push_tracks()
    elif keycode == 'KEY_DOWN':
        with state['lock']:
            state['tracks_index'] = min(len(album.tracks) - 1, idx + 1)
        push_tracks()
    elif keycode == 'KEY_ENTER':
        if idx == -1:
            _leave_tracks()
        else:
            _play_from(album, idx)
    elif keycode in ('KEY_ESC', 'KEY_BACKSPACE'):
        _leave_tracks()


def _leave_tracks():
    with state['lock']:
        state['screen'] = 'music'
    push_music()


def _play_from(album, index):
    """Play `album` from track `index` and show it. Choosing the track that is already playing just shows it."""
    sess = _music_session()
    if sess is None:
        _show_alert('NO_AUDIO', 'tracks', reason=sanitize(_music_problem or 'NO PLAYER')[:50])
        return
    now = sess.now()
    with state['lock']:
        state['screen']       = 'nowplaying'                   # first, so alerts and redraws from the player land here
        state['music_return'] = 'tracks'
    if now is not None and now.track.path == album.tracks[index].path and now.count == len(album.tracks):
        push_nowplaying()
    else:
        sess.play_tracks(album.tracks, index)


def _resume_last():
    with state['lock']:
        lib, last = state['music_lib'], state['music_last']
    found = _find_track(lib, last['path']) if last else None
    sess = _music_session()
    if found is None:
        _open_music()
        return
    if sess is None:
        _show_alert('NO_AUDIO', 'music', reason=sanitize(_music_problem or 'NO PLAYER')[:50])
        return
    with state['lock']:
        state['screen']       = 'nowplaying'
        state['music_return'] = 'music'
    sess.play_tracks(found[0].tracks, found[1], start=last['position'], play=False)
    push_nowplaying()


def _open_nowplaying(return_to):
    with state['lock']:
        state['screen']       = 'nowplaying'
        state['music_return'] = return_to
    push_nowplaying()


def _now_command(now):
    t = now.track
    return "|".join(["NOWPLAYING", {'playing': 'P', 'paused': 'U'}.get(now.state, 'S'),
                     sanitize(t.title)[:NOW_TITLE_MAX], sanitize(t.artist)[:NOW_LINE_MAX], sanitize(t.album)[:NOW_LINE_MAX],
                     str(int(now.position)), str(int(now.duration or 0)), str(now.volume), f"{now.index + 1}/{now.count}"])


def push_nowplaying():
    now = _music.now() if _music is not None else None
    if now is None:                                        # nothing is queued: the album list is the place to be
        with state['lock']:
            state['screen'] = 'music'
        push_music()
        return
    push_screen(_now_command(now))


def _from_nowplaying(keycode):
    sess = _music
    if keycode in ('KEY_ESC', 'KEY_BACKSPACE'):
        with state['lock']:
            target = 'tracks' if state['music_return'] == 'tracks' and state['album'] is not None else 'music'
            state['screen'] = target
        _push_for_screen(target)
        return
    if sess is None:
        push_nowplaying()
        return
    step, seek = music_player.VOLUME_STEP, music_player.SEEK_STEP
    actions = {
        'KEY_ENTER': sess.toggle, 'CHAR: ': sess.toggle,
        'KEY_RIGHT': sess.next, 'KEY_LEFT': sess.previous,
        'KEY_UP': lambda: sess.step_volume(step) or True, 'CHAR:+': lambda: sess.step_volume(step) or True,
        'CHAR:=': lambda: sess.step_volume(step) or True,
        'KEY_DOWN': lambda: sess.step_volume(-step) or True, 'CHAR:-': lambda: sess.step_volume(-step) or True,
        'CHAR:_': lambda: sess.step_volume(-step) or True,
        'CHAR:.': lambda: sess.seek_by(seek), 'CHAR:>': lambda: sess.seek_by(seek),
        'CHAR:,': lambda: sess.seek_by(-seek), 'CHAR:<': lambda: sess.seek_by(-seek),
    }
    action = actions.get(keycode)
    if action is None:
        return
    if not action():                                       # nothing changed (the end of the album, say): still redraw,
        push_nowplaying()                                  # so the key press is visibly answered


def _music_tick():
    """Once a second: let the silent player notice the end of a track, redraw now-playing every MUSIC_TICK_SECONDS
    while it is showing and playing, and remember where we are now and then."""
    global _music_tick_count, _music_save_count
    sess = _music
    if sess is None:
        return
    sess.poll()
    with state['lock']:
        screen = state['screen']
    if sess.playing:
        _music_save_count += 1
        if _music_save_count >= MUSIC_TICK_SECONDS:
            _music_save_count = 0
            _save_listening()
    if screen == 'nowplaying' and sess.playing:
        _music_tick_count += 1
        if _music_tick_count >= MUSIC_TICK_SECONDS:
            _music_tick_count = 0
            push_nowplaying()
    else:
        _music_tick_count = 0


def music_tick_loop():
    while state['running']:
        time.sleep(1)
        try:
            _music_tick()
        except Exception as e:                             # the ticker must never die
            print(f"Warning: music tick failed: {e}")


def _run_async(fn):
    """Run `fn` off the input thread, so a slow radio never freezes the keyboard.
    (Tests replace this with a direct call.)"""
    threading.Thread(target=fn, daemon=True).start()


# ─── Notes ────────────────────────────────────────────────────────────────────
# NOTES on the home menu: plain text notes typed on the keyboard, kept in data/notes.json, newest first.
# The list is the two-line list (the first line of the note over when it was last changed) with + in the header.
# The editor types at the end of the note; Enter starts a new line. The phone keyboard has no Esc, so, as on New
# Message, Up reaches the header: < saves and goes back, DELETE asks first. A note saves itself on leaving; one left
# empty is not kept. The Radxa wraps the note into lines; a note too long for one frame shows its END behind '...'.

NOTES_FILE  = os.path.join(DATA_DIR, 'notes.json')
NOTES_ROWS  = 5        # rows on the notes list (the two-line list)
NOTE_MAX    = 4000     # characters in one note
NOTE_COLS   = 30       # 18px glyphs across the 552px text area, as in the composer
NOTE_LINES  = 14       # lines the editor can show
NOTE_TITLE_MAX = 22    # a note's name on the list (the bold 24px row title, clear of the chevron)


def load_notes():
    """The saved notes; anything malformed is dropped."""
    try:
        with open(NOTES_FILE) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = []
    good = [{'text': n['text'][:NOTE_MAX], 'ts': n.get('ts', '') if isinstance(n.get('ts', ''), str) else ''}
            for n in (data if isinstance(data, list) else [])
            if isinstance(n, dict) and isinstance(n.get('text'), str) and n['text'].strip()]
    with state['lock']:
        state['notes'] = good


def save_notes():
    with state['lock']:
        data = [dict(n) for n in state['notes']]
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = NOTES_FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(data, f)
        os.replace(tmp, NOTES_FILE)
    except OSError as e:
        print(f"Warning: could not save notes: {e}")


def note_title(text):
    """A note's name on the list: its first line that has anything on it."""
    for line in text.split('\n'):
        if line.strip():
            return line.strip()
    return ''


def _short_title(title):
    return title if len(title) <= NOTE_TITLE_MAX else title[:NOTE_TITLE_MAX - 3].rstrip() + '...'


def note_view(text, budget):
    """The lines the editor shows: the note wrapped at NOTE_COLS with room for the cursor after the last character,
    as many of the LAST lines as fit NOTE_LINES and `budget` characters; if the start is cut, the first line
    shown is '...'."""
    lines = []
    paragraphs = text.split('\n')
    for i, para in enumerate(paragraphs):
        wrapped = wrap_words(para + ('#' if i == len(paragraphs) - 1 else ''), NOTE_COLS)   # '#' = the cursor cell
        lines += wrapped
    lines[-1] = lines[-1][:-1]                                  # drop the cursor stand-in
    shown, used = [], 0
    for line in reversed(lines):
        cost = len(line) + 1
        if len(shown) >= NOTE_LINES or used + cost > budget:
            break
        shown.insert(0, line)
        used += cost
    if len(shown) < len(lines):
        while shown and (len(shown) >= NOTE_LINES or used + 4 > budget):
            used -= len(shown.pop(0)) + 1
        shown.insert(0, '...')
    return shown


def _open_notes(select=0):
    with state['lock']:
        n = len(state['notes'])
        state['screen']            = 'notes_list'
        state['notes_index']       = select if n else -1
        state['notes_start']       = 0
        state['notes_header_sel']  = 'back' if n else 'plus'     # an empty list opens on + (there is no row)
    push_notes()


def push_notes():
    with state['lock']:
        notes = list(state['notes'])
        idx   = state['notes_index']
        hsel  = state['notes_header_sel']
        start = window_start(state['notes_start'], max(0, idx), NOTES_ROWS, len(notes))
        state['notes_start'] = start
    if idx < 0:
        sel = '-2' if hsel == 'plus' else '-1'
    else:
        sel = str(idx - start)
    rows = [[_short_title(sanitize(note_title(n['text']))), format_msg_time(n.get('ts')), '']
            for n in notes[start:start + NOTES_ROWS]]
    push_screen(_list_command(["NOTES", sel], rows, shrink_order=(0,)))


def _from_notes(keycode):
    with state['lock']:
        idx  = state['notes_index']
        hsel = state['notes_header_sel']
        n    = len(state['notes'])
    if keycode == 'KEY_ESC' or (keycode == 'KEY_ENTER' and idx < 0 and hsel == 'back'):
        with state['lock']:
            state['screen']     = 'home'
            state['home_index'] = HOME_MENU.index('NOTES')
        push_home2()
    elif keycode == 'CHAR:+' or (keycode == 'KEY_ENTER' and idx < 0 and hsel == 'plus'):
        _open_note(None)
    elif keycode == 'KEY_ENTER':
        _open_note(idx)
    elif keycode == 'KEY_UP':
        with state['lock']:
            state['notes_index'] = max(-1, idx - 1)
        push_notes()
    elif keycode == 'KEY_DOWN':
        with state['lock']:
            if idx < n - 1:
                state['notes_index'] = idx + 1
        push_notes()
    elif keycode in ('KEY_LEFT', 'KEY_RIGHT') and idx < 0:
        with state['lock']:
            state['notes_header_sel'] = 'plus' if keycode == 'KEY_RIGHT' else 'back'
        push_notes()


def _open_note(idx):
    """idx: a note on the list, or None for a new one."""
    with state['lock']:
        state['screen']   = 'note'
        state['note_idx'] = idx
        state['note_text'] = state['notes'][idx]['text'] if idx is not None else ''
        state['note_hdr']  = None
    push_note()


def push_note():
    with state['lock']:
        text = state['note_text']
        hdr  = {'back': 'B', 'delete': 'D'}.get(state['note_hdr'], '')
    head = f"NOTE|{hdr}|"
    lines = note_view(sanitize_lines(text), MAX_COMMAND_CHARS - len(head))
    push_screen(head + '\xb7'.join(lines))


def sanitize_lines(text):
    """Keep the line breaks (they become separate lines on the panel), clean everything else."""
    return '\n'.join(sanitize(part) for part in text.split('\n'))


def _close_note():
    """Save what was typed (an empty note is not kept) and go back to the list, on this note."""
    with state['lock']:
        idx  = state['note_idx']
        text = state['note_text'].rstrip()
        notes = state['notes']
        if idx is not None and 0 <= idx < len(notes):
            changed = notes[idx]['text'] != text
            if not text.strip():
                del notes[idx]
            elif changed:
                del notes[idx]
                notes.insert(0, {'text': text, 'ts': datetime.now().isoformat()})
            select = 0 if changed else idx
            dirty  = changed or not text.strip()
        else:
            dirty = bool(text.strip())
            if dirty:
                notes.insert(0, {'text': text, 'ts': datetime.now().isoformat()})
            select = 0
        select = min(select, max(0, len(notes) - 1))
    if dirty:
        save_notes()
    _open_notes(select)


def _from_note(keycode):
    with state['lock']:
        hdr  = state['note_hdr']
        text = state['note_text']
    if keycode == 'KEY_ESC' or (keycode == 'KEY_ENTER' and hdr == 'back'):
        _close_note()
    elif keycode == 'KEY_ENTER' and hdr == 'delete':
        with state['lock']:
            state['screen']       = 'confirm'
            state['confirm_kind'] = 'delete_note'
            state['confirm_sel']  = 'keep'
        push_confirm()
    elif keycode == 'KEY_UP' and hdr is None:
        with state['lock']:
            state['note_hdr'] = 'back'
        push_note()
    elif keycode == 'KEY_DOWN' and hdr is not None:
        with state['lock']:
            state['note_hdr'] = None
        push_note()
    elif keycode in ('KEY_LEFT', 'KEY_RIGHT') and hdr is not None:
        with state['lock']:
            state['note_hdr'] = 'back' if keycode == 'KEY_LEFT' else 'delete'
        push_note()
    elif keycode == 'KEY_ENTER' or keycode.startswith('CHAR:'):
        add = '\n' if keycode == 'KEY_ENTER' else keycode[5:]
        if len(text) + len(add) > NOTE_MAX:
            return                                              # full: the key is not taken
        with state['lock']:
            state['note_text'] = text + add
            state['note_hdr']  = None
        push_note()
    elif keycode == 'KEY_BACKSPACE':
        with state['lock']:
            state['note_text'] = text[:-1]
            state['note_hdr']  = None
        push_note()


def _delete_note():
    with state['lock']:
        idx = state['note_idx']
        notes = state['notes']
        existed = idx is not None and 0 <= idx < len(notes)
        if existed:
            del notes[idx]
        state['confirm_sel'] = 'keep'
    if existed:
        save_notes()
    _open_notes(0)


# ─── Settings (Wi-Fi / Bluetooth) ──────────────────────────────────────────────
# Kyle's layout (2026-09-25). SETTINGS lists Wi-Fi and Bluetooth, each with its
# status. network_control.py talks to the small computer's own nmcli / bluetoothctl.
#
# WI-FI: the switch; the joined network right under it (Enter: FORGET THIS NETWORK, confirmed); then the networks
#   nearby, A-Z. A search runs once when the list opens — the switch and the joined network are there at once,
#   SEARCHING... sits at the end until the search answers, then the list redraws once — and SEARCH AGAIN runs
#   another. An open network connects at once; a secured one asks for its password first (NETPASS).
# BLUETOOTH: the switch; the known (paired) devices, A-Z (Enter: FORGET or RE-CONNECT, on the confirmation
#   screen); PAIR NEW DEVICE, which opens OTHER DEVICES: a search for devices not yet paired. Nothing scans for
#   Bluetooth until PAIR NEW DEVICE is chosen.
# The keyboard is a Bluetooth device and the phone's only way to type, so Bluetooth is never switched off while it
#   is connected by it, and a keyboard (any input device) is never forgotten at all: a stop alert says why instead.
# Connecting and pairing go through NETSTATE (WORKING, then OK or FAIL). Every slow call runs off the keyboard's
#   thread (_run_async), and a result that arrives after the phone has moved on is dropped.

NET_SCREENS = {'W': 'wifi', 'B': 'bluetooth', 'P': 'btpair'}
WIFI_ON_SETTLE = 4      # seconds to let NetworkManager rejoin a saved network after Wi-Fi is switched on


def _wifi_status_text(on, status):
    if not on:
        return 'Off'
    if status and status.connected:
        return sanitize('Connected: ' + status.ssid)[:NET_SUB_MAX]
    return 'Not connected'


def _bt_status_text(status):
    if status and not status.powered:
        return 'Off'
    if status and status.connected_names:
        return sanitize('Connected: ' + ', '.join(status.connected_names))[:NET_SUB_MAX]
    return 'Not connected'


def _refresh_settings_status():
    wifi_on = netctl.wifi_enabled()
    wifi    = netctl.wifi_status()
    bt      = netctl.bt_status()
    with state['lock']:
        state['settings_wifi_on'] = wifi_on
        state['settings_wifi']    = wifi
        state['settings_bt']      = bt


def _open_settings():
    with state['lock']:
        state['screen']          = 'settings'
        state['settings_index']  = 0
    _refresh_settings_status()
    push_settings()


def _back_to_settings():
    with state['lock']:
        state['screen'] = 'settings'
    _refresh_settings_status()
    push_settings()


def push_settings():
    with state['lock']:
        idx     = state['settings_index']
        wifi_on = state['settings_wifi_on']
        wifi    = state['settings_wifi']
        bt      = state['settings_bt']
        light   = state['light']
        mark    = state['activity_mark']
    rows = [
        ['Wi-Fi', _wifi_status_text(wifi_on, wifi), ''],
        ['Bluetooth', _bt_status_text(bt), ''],
        ['Screen light', f'Level {light} of {LIGHT_LEVELS}' if light else 'Off', ''],
        ['Activity mark', 'A * on the lock screen', 'ON' if mark else 'OFF'],
    ]
    push_screen(_list_command(["SETTINGS", str(idx)], rows, shrink_order=(1,)))


def _from_settings(keycode):
    with state['lock']:
        idx = state['settings_index']
    if keycode == 'KEY_UP':
        with state['lock']:
            state['settings_index'] = max(-1, idx - 1)
        push_settings()
    elif keycode == 'KEY_DOWN':
        with state['lock']:
            state['settings_index'] = min(SETTINGS_ROWS - 1, idx + 1)
        push_settings()
    elif keycode == 'KEY_ENTER' and idx == 0:
        _open_wifi()
    elif keycode == 'KEY_ENTER' and idx == 1:
        _open_bluetooth()
    elif keycode == 'KEY_ENTER' and idx == 2:
        with state['lock']:
            state['screen'] = 'light'
        push_light()
    elif keycode == 'KEY_ENTER' and idx == 3:
        with state['lock']:
            state['activity_mark'] = not state['activity_mark']
        save_settings()
        push_settings()
    elif keycode in ('KEY_ENTER', 'KEY_ESC', 'KEY_BACKSPACE'):   # Enter on the header, or Esc
        with state['lock']:
            state['screen']     = 'home'
            state['home_index'] = HOME_MENU.index('SETTINGS')
        push_home2()


SETTINGS_ROWS = 4       # Wi-Fi, Bluetooth, Screen light, Activity mark


def push_light():
    """SCREEN LIGHT: the level as a bar. The firmware also sets the front light from this command's level, so the
    light changes with the drawing (the sender keeps only the latest command, so a separate one could be dropped)."""
    with state['lock']:
        level = state['light']
    push_screen(f"LIGHTSET|{level}")


def _from_light(keycode):
    """<- / down: dimmer, -> / up: brighter; Enter or Esc: done. A step past either end redraws, so it is answered."""
    if keycode in ('KEY_ENTER', 'KEY_ESC', 'KEY_BACKSPACE'):
        _back_to_settings()
        return
    step = {'KEY_LEFT': -1, 'KEY_DOWN': -1, 'KEY_RIGHT': 1, 'KEY_UP': 1}.get(keycode)
    if step is None:
        return
    with state['lock']:
        state['light'] = max(0, min(LIGHT_LEVELS, state['light'] + step))
    save_settings()
    push_light()


def _open_wifi(keep_index=False):
    """The Wi-Fi list, drawn at once from the switch and the joined network; the search for networks nearby
    starts in the background and redraws the list once when it answers."""
    on     = netctl.wifi_enabled()
    status = netctl.wifi_status() if on else netctl.WifiStatus(False)
    with state['lock']:
        state['screen']       = 'wifi'
        state['net_kind']     = 'W'
        state['net_on']       = on
        state['net_current']  = status.ssid if status.connected else None
        state['net_rows']     = []
        state['net_scanning'] = on
        if not keep_index:
            state['net_index'] = 0
            state['net_start'] = 0
    push_net()
    if on:
        _run_async(lambda: _do_net_scan('W'))


def _open_bluetooth(keep_index=False):
    """The Bluetooth list: the switch and the paired devices. Reads only — nothing scans here."""
    on    = netctl.bt_status().powered
    known = netctl.bt_known() if on else []
    with state['lock']:
        state['screen']       = 'bluetooth'
        state['net_kind']     = 'B'
        state['net_on']       = on
        state['net_current']  = None
        state['net_rows']     = known
        state['net_scanning'] = False
        if not keep_index:
            state['net_index'] = 0
            state['net_start'] = 0
    push_net()


def _open_btpair():
    """OTHER DEVICES: search once for devices that are not paired yet."""
    with state['lock']:
        state['screen']       = 'btpair'
        state['net_kind']     = 'P'
        state['net_on']       = True
        state['net_rows']     = []
        state['net_scanning'] = True
        state['net_index']    = 0
        state['net_start']    = 0
    push_net()
    _run_async(lambda: _do_net_scan('P'))


def _search_again(kind):
    with state['lock']:
        state['net_scanning'] = True      # the list stays as it was, SEARCHING... in place of SEARCH AGAIN
    push_net()
    _run_async(lambda: _do_net_scan(kind))


def _do_net_scan(kind):
    rows = netctl.wifi_scan() if kind == 'W' else [d for d in netctl.bt_scan() if not d.paired]
    with state['lock']:
        if state['screen'] != NET_SCREENS[kind] or state['net_kind'] != kind:
            return                                            # moved on meanwhile: the result is dropped
        state['net_scanning'] = False
        state['net_rows']     = rows
    push_net()


def _net_entries(kind, on, current, rows, scanning):
    """The rows of a Wi-Fi / Bluetooth / Other devices list, top to bottom, as (what, item, [title, sub, right])."""
    if kind in ('W', 'B'):
        word = 'Wi-Fi' if kind == 'W' else 'Bluetooth'
        entries = [('toggle', None, [word, f'{word} is {"on" if on else "off"}', 'ON' if on else 'OFF'])]
        if not on:
            return entries
    else:
        entries = []
    if kind == 'B':
        entries += [('known', d, [d.name, 'Connected' if d.connected else 'Not connected', '']) for d in rows]
        entries.append(('pairnew', None, ['PAIR NEW DEVICE', 'Put the device in pairing mode first', '']))
        return entries
    if kind == 'W':
        if current:
            entries.append(('current', current, [current, 'Connected', '']))
        nearby = sorted((n for n in rows if n.ssid != current), key=lambda n: n.ssid.lower())
        entries += [('net', n, [n.ssid, 'Secured' if n.secured else 'Open', '']) for n in nearby]
    else:
        entries += [('device', d, [d.name, 'New device', '']) for d in sorted(rows, key=lambda d: d.name.lower())]
    if scanning:
        entries.append(('searching', None, ['SEARCHING...', '', '']))
    else:
        none = ('No networks found' if kind == 'W' else 'No devices found') if not rows else ''
        entries.append(('again', None, ['SEARCH AGAIN', none, '']))
    return entries


def _net_snapshot():
    with state['lock']:
        return (state['net_kind'], state['net_on'], state['net_current'], list(state['net_rows']),
                state['net_scanning'], state['net_index'])


def push_net():
    kind, on, current, rows, scanning, idx = _net_snapshot()
    entries = _net_entries(kind, on, current, rows, scanning)
    idx = min(idx, len(entries) - 1)
    with state['lock']:
        state['net_index'] = idx
        start = window_start(state['net_start'], max(0, idx), NET_ROWS, len(entries))
        state['net_start'] = start
    send_idx = idx if idx < 0 else idx - start
    shown = [[sanitize(entry[2][0])[:NET_NAME_MAX], sanitize(entry[2][1]), entry[2][2]]
             for entry in entries[start:start + NET_ROWS]]
    push_screen(_list_command(["NETLIST", kind, str(send_idx)], shown, shrink_order=(0,)))


def _leave_net(kind):
    if kind == 'P':
        _open_bluetooth()
    else:
        _back_to_settings()


def _from_net(keycode):
    kind, on, current, rows, scanning, idx = _net_snapshot()
    entries = _net_entries(kind, on, current, rows, scanning)

    if keycode in ('KEY_ESC', 'KEY_BACKSPACE') or (keycode == 'KEY_ENTER' and idx == -1):
        _leave_net(kind)
    elif keycode in ('KEY_UP', 'KEY_DOWN'):
        step = -1 if keycode == 'KEY_UP' else 1
        with state['lock']:
            state['net_index'] = max(-1, min(len(entries) - 1, idx + step))
        push_net()
    elif keycode == 'KEY_ENTER' and 0 <= idx < len(entries):
        what, item, _ = entries[idx]
        if what == 'toggle':
            _net_toggle(kind, on, rows)
        elif what == 'current':
            _open_net_confirm('forget_wifi', ssid=item)
        elif what == 'net':
            if item.secured:
                _open_netpass(item.ssid)
            else:
                _open_netstate_wifi(item.ssid, None, source='wifi')
        elif what == 'again':
            _search_again(kind)
        elif what == 'known':
            _open_net_confirm('bt_device', device=item)
        elif what == 'pairnew':
            _open_btpair()
        elif what == 'device':
            _open_netstate_bt(item.mac, item.name, source='btpair')
        else:                                                 # SEARCHING...: nothing to do yet, but answer the key
            push_net()


def _net_toggle(kind, on, rows):
    if kind == 'B' and on and any(d.connected and d.is_input for d in rows):
        _show_alert('BT_KEEP_ON', 'bluetooth')
        return

    def work():
        if kind == 'W':
            result = netctl.wifi_set_enabled(not on)
            if result.ok and not on:
                time.sleep(WIFI_ON_SETTLE)
        else:
            result = netctl.bt_set_powered(not on)
        with state['lock']:
            if state['screen'] != NET_SCREENS[kind]:
                return
        if not result.ok:
            _show_alert('NET_FAILED', NET_SCREENS[kind], what=result.detail.upper())
        elif kind == 'W':
            _open_wifi(keep_index=True)
        else:
            _open_bluetooth(keep_index=True)
    _run_async(work)


def _open_net_confirm(kind, ssid='', device=None):
    """FORGET THIS NETWORK, or a known device's FORGET / RE-CONNECT, on the one confirmation layout."""
    with state['lock']:
        state['screen']       = 'confirm'
        state['confirm_kind'] = kind
        state['confirm_sel']  = 'keep'
        if kind == 'forget_wifi':
            state['net_ssid'] = ssid
        else:
            state['net_mac']       = device.mac
            state['net_name']      = device.name
            state['net_confirm_connected'] = device.connected
            state['net_confirm_input']     = device.is_input      # a keyboard is never forgotten, connected or not
    push_confirm()


def _net_confirm_text(kind):
    with state['lock']:
        ssid, name = state['net_ssid'], state['net_name']
        connected  = state['net_confirm_connected']
    if kind == 'forget_wifi':
        return ('WI-FI', f'FORGET {sanitize(ssid).upper()}? THE PHONE WILL DISCONNECT FROM IT, AND JOINING AGAIN '
                         'WILL NEED THE PASSWORD.', 'FORGET', 'KEEP')
    state_word = 'IS CONNECTED' if connected else 'IS NOT CONNECTED'
    return ('BLUETOOTH', f'{sanitize(name).upper()} {state_word}. CONNECT TO IT AGAIN, OR FORGET IT? A FORGOTTEN '
                         'DEVICE HAS TO BE PAIRED AGAIN.', 'FORGET', 'RE-CONNECT')


def _net_confirm_go(kind):
    """The left (destructive) button: forget the network or the device."""
    if kind == 'bt_device':
        with state['lock']:
            is_input = state['net_confirm_input']
        if is_input:
            _show_alert('BT_KEEP_KEYBOARD', 'bluetooth')
            return
    with state['lock']:
        ssid, mac = state['net_ssid'], state['net_mac']
        state['screen']       = 'netstate'
        state['net_kind']     = 'W' if kind == 'forget_wifi' else 'B'
        state['net_source']   = 'forget'
        state['net_status']   = 'WORKING'
        state['net_detail']   = 'Forgetting...'
        state['confirm_sel']  = 'keep'
    push_netstate()

    def work():
        result = netctl.wifi_forget(ssid) if kind == 'forget_wifi' else netctl.bt_forget(mac)
        with state['lock']:
            if state['screen'] != 'netstate' or state['net_source'] != 'forget':
                return
        if result.ok:
            _open_wifi() if kind == 'forget_wifi' else _open_bluetooth()
        else:
            with state['lock']:
                state['net_status'] = 'FAIL'
                state['net_detail'] = result.detail
            push_netstate()
    _run_async(work)


def _open_netpass(ssid):
    with state['lock']:
        state['screen']   = 'netpass'
        state['net_ssid'] = ssid
        state['net_pass'] = ''
        state['net_pass_hdr'] = False
    push_netpass()


def push_netpass():
    """The password never travels on the wire — only a `*` mask the same length, so the firmware never draws
    (and no capture of the SPI link ever carries) what was actually typed. The last field is `B` when the
    header's < is selected."""
    with state['lock']:
        ssid  = state['net_ssid']
        typed = state['net_pass']
        hdr   = 'B' if state['net_pass_hdr'] else ''
    push_screen(f"NETPASS|{sanitize(ssid)}|{'*' * min(len(typed), NET_PASS_MAX)}|{hdr}")


def _from_netpass(keycode):
    """Like New Message: the phone keyboard has no Esc, so ↑ selects the header's < and Enter there goes
    back to the Wi-Fi list. Typing while < is selected returns to the field and types."""
    with state['lock']:
        ssid  = state['net_ssid']
        typed = state['net_pass']
        hdr   = state['net_pass_hdr']
    if keycode == 'KEY_ESC' or (hdr and keycode == 'KEY_ENTER'):
        with state['lock']:
            state['screen']       = 'wifi'
            state['net_pass_hdr'] = False
        push_net()
    elif keycode in ('KEY_UP', 'KEY_DOWN'):
        with state['lock']:
            state['net_pass_hdr'] = keycode == 'KEY_UP'
        push_netpass()
    elif keycode == 'KEY_ENTER':
        _open_netstate_wifi(ssid, typed, source='netpass')
    elif keycode == 'KEY_BACKSPACE':
        with state['lock']:
            state['net_pass']     = typed[:-1]
            state['net_pass_hdr'] = False
        push_netpass()
    elif keycode.startswith('CHAR:'):
        with state['lock']:
            state['net_pass']     = (typed + keycode[5:])[:NET_PASS_MAX]
            state['net_pass_hdr'] = False
        push_netpass()


def _open_netstate_wifi(ssid, password, source):
    with state['lock']:
        state['screen']     = 'netstate'
        state['net_kind']   = 'W'
        state['net_ssid']   = ssid
        state['net_source'] = source
        state['net_status'] = 'WORKING'
        state['net_detail'] = f'Connecting to {ssid}...'
    push_netstate()

    def work():
        result = netctl.wifi_connect(ssid, password or None)
        with state['lock']:
            if state['screen'] != 'netstate' or state['net_kind'] != 'W' or state['net_ssid'] != ssid:
                return                                         # navigated away meanwhile: the result is dropped
            state['net_status'] = 'OK' if result.ok else 'FAIL'
            state['net_detail'] = f'Connected to {ssid}.' if result.ok else result.detail
        push_netstate()
    _run_async(work)


def _open_netstate_bt(mac, name, source):
    with state['lock']:
        state['screen']     = 'netstate'
        state['net_kind']   = 'B'
        state['net_mac']    = mac
        state['net_name']   = name
        state['net_source'] = source
        state['net_status'] = 'WORKING'
        state['net_detail'] = f'Connecting to {name}...' if source == 'bluetooth' else f'Pairing with {name}...'
    push_netstate()

    def work():
        result = netctl.bt_pair_connect(mac)
        with state['lock']:
            if state['screen'] != 'netstate' or state['net_kind'] != 'B' or state['net_mac'] != mac:
                return                                         # navigated away meanwhile: the result is dropped
            state['net_status'] = 'OK' if result.ok else 'FAIL'
            state['net_detail'] = f'Connected to {name}.' if result.ok else result.detail
        push_netstate()
    _run_async(work)


def push_netstate():
    with state['lock']:
        kind   = state['net_kind']
        status = state['net_status']
        detail = state['net_detail']
    push_screen(f"NETSTATE|{kind}|{status}|{sanitize(detail)}")


def _from_netstate(keycode):
    """OK goes back to the refreshed Wi-Fi / Bluetooth list. FAIL goes back to where the attempt started (the
    password box keeps what was typed). Leaving while WORKING abandons it; the late result is dropped."""
    if keycode not in ('KEY_ENTER', 'KEY_ESC', 'KEY_BACKSPACE'):
        return
    with state['lock']:
        kind   = state['net_kind']
        status = state['net_status']
        source = state['net_source']
        state['net_source'] = '' if source == 'forget' else source    # a late forget result is dropped
    if status == 'OK' or source == 'forget':
        _open_wifi() if kind == 'W' else _open_bluetooth()
        return
    if kind == 'W':
        target = 'netpass' if (status == 'FAIL' and source == 'netpass') else 'wifi'
    else:
        target = source if source in ('bluetooth', 'btpair') else 'bluetooth'
    with state['lock']:
        state['screen']   = target
        state['net_kind'] = {'wifi': 'W', 'netpass': 'W', 'bluetooth': 'B', 'btpair': 'P'}[target]
    _push_for_screen(target)


def _refresh_after_send(peer):
    with state['lock']:
        screen, thread_id = state['screen'], state['thread_id']
    if screen == 'thread' and thread_id == peer:
        push_thread2()
    elif screen == 'texts_list':
        push_texts()


def _dispatch_send(msg):
    def work():
        try:
            _transport_send(msg['peer'], msg['body'])
            outcome = 'sent'
        except Exception as e:
            print(f"  → send failed: {e}")
            outcome = 'not_sent'
        with state['lock']:
            msg['state'] = outcome
        save_messages()
        _refresh_after_send(msg['peer'])
    _run_async(work)


def resolve_peer(number):
    """The key a conversation with this person is stored under: the key of an
    existing thread with the same number however it was typed, else the number
    normalized to +1XXXXXXXXXX."""
    with state['lock']:
        keys = [peer_of(m) for m in state['messages']]
    return next((k for k in keys if k and same_number(k, number)), None) or normalize_number(number)


def send_reply(to_number, body):
    """Send a text. The message joins the thread at once as SENDING..., and the
    radio's answer settles it to SENT or NOT SENT. A new conversation joins the
    texts list on the first send whether or not it succeeds."""
    msg = {
        'dir':   'out',
        'peer':  resolve_peer(to_number),
        'name':  'You',
        'body':  body,
        'read':  True,
        'ts':    datetime.now().isoformat(),
        'state': 'sending',
    }
    with state['lock']:
        state['messages'].append(msg)
    save_messages()
    _dispatch_send(msg)
    return msg


def retry_message(msg):
    """Send a not-sent message again."""
    with state['lock']:
        if msg.get('state') != 'not_sent':
            return
        msg['state'] = 'sending'
    save_messages()
    _dispatch_send(msg)


# ─── Background Loops ─────────────────────────────────────────────────────────

def clock_loop():
    if SIM_MODE:
        while simulator is None or not simulator._ready:
            time.sleep(0.05)
    push_lock()
    while state['running']:
        time.sleep(CLOCK_UPDATE_INTERVAL)
        if not state['running']:
            break
        with state['lock']:
            screen = state['screen']
        if screen == 'home':
            push_home2()
        elif screen == 'lock':
            push_lock()


def call_timer_loop():
    """Ticks the in-call duration display once a second."""
    while state['running']:
        time.sleep(1)
        with state['lock']:
            screen = state['screen']
        if screen == 'in_call':
            push_call_screen()


def modem_sms_loop():
    """Polls the real modem for texts that arrived while the phone wasn't looking. Each one the modem
    hands back is already removed from its own storage (modem.SerialModem.poll_new deletes as it reads),
    so there is no dedup bookkeeping to do here."""
    if _modem is None:
        return
    print(f"Polling the modem for texts every {SMS_POLL_INTERVAL}s...")
    while state['running']:
        try:
            for msg in _modem.poll_new():
                with state['lock']:
                    name = format_name(msg['sender'])
                    state['messages'].append({
                        'sender': msg['sender'],
                        'name':   name,
                        'body':   msg['body'],
                        'read':   False,
                        'ts':     msg['ts'],
                    })
                save_messages()
                print(f"\n[NEW SMS] {name}: {msg['body']}")
                with state['lock']:
                    current_screen = state['screen']
                    thread_id      = state['thread_id']
                if current_screen == 'thread' and thread_id == msg['sender']:
                    push_thread2()
                elif current_screen == 'texts_list':
                    push_texts()
                elif current_screen == 'lock':
                    push_lock()                                 # the new-activity * appears
        except modem.ModemError as e:
            print(f"Modem poll error: {e}")
        time.sleep(SMS_POLL_INTERVAL)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    load_messages()
    load_calls()
    load_settings()
    load_notes()
    _init_modem()

    threading.Thread(target=clock_loop,      daemon=True).start()
    threading.Thread(target=modem_sms_loop,  daemon=True).start()
    threading.Thread(target=call_timer_loop, daemon=True).start()
    threading.Thread(target=music_tick_loop, daemon=True).start()

    if not SIM_MODE:
        threading.Thread(target=_spi_sender_loop, daemon=True).start()
        KeyboardHandler(handle_key).start()
        TrackpadHandler(handle_key).start()

    print(f"\n--- KyPhone OS {VERSION} ---")
    if _modem is not None:
        print(f"Modem: {_modem.port} (signal {_modem.signal_quality()})")

    try:
        if SIM_MODE:
            simulator.init()
            simulator.run_loop()
        elif sys.stdin.isatty():
            while True:
                cmd = input("KyPhone> ").strip()
                if cmd.lower() in ('exit', 'quit'):
                    break
                elif cmd.lower() == 'home':
                    with state['lock']:
                        state['screen'] = 'home'
                    push_home2()
                elif cmd.lower() == 'texts':
                    with state['lock']:
                        state['screen']      = 'texts_list'
                        state['texts_index'] = 0
                    push_texts()
        else:
            while state['running']:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        state['running'] = False
        if not SIM_MODE:
            spi.close()
            handshake.release()
        print("\nExiting.")


if __name__ == '__main__':
    main()
