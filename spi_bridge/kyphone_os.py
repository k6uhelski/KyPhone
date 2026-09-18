"""
kyphone_os.py — KyPhone OS 0.2

Screens: lock | home | texts_list | thread | compose | confirm_discard |
         contacts_pick | contact | contact_edit | stub |
         calls_list | dial | outgoing | incoming | in_call

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

from twilio.rest import Client

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
LIST_NAME_MAX    = 14   # texts + calls name column
CONTACT_NAME_MAX = 18   # contacts list name column
PREVIEW_MAX      = 24   # texts list preview line

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

# --- Twilio Credentials ---
ACCOUNT_SID   = os.environ.get('TWILIO_SID')
AUTH_TOKEN    = os.environ.get('TWILIO_TOKEN')
TWILIO_NUMBER = os.environ.get('TWILIO_NUMBER') or ('+1sim' if SIM_MODE else None)

if not all([ACCOUNT_SID, AUTH_TOKEN, TWILIO_NUMBER]):
    if not SIM_MODE and os.environ.get('TWILIO_NUMBER'):
        print("Warning: Twilio env vars not set — SMS polling disabled.")
    elif not SIM_MODE:
        print("Error: set TWILIO_SID, TWILIO_TOKEN, and TWILIO_NUMBER env vars.")
        sys.exit(1)

# --- Contacts ---
# Address book records: [{first, last, number}, ...]. OS 0.1 stored a flat
# {number: name} dict; that shape is auto-migrated to this one on load.
_contacts_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'contacts.json')


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
DATA_DIR      = os.path.join(os.path.dirname(__file__), '..', 'data')
MESSAGES_FILE = os.path.join(DATA_DIR, 'messages.json')

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
STUB_INFO = {
    'READ': {
        'title': 'READ',
        'body': 'READ CANNOT OPEN YET. THIS BUILD CARRIES TEXT AND CALL ONLY, AND NO BOOKS ARE ON THE PHONE. PRESS ENTER TO GO BACK TO THE MENU.',
    },
    'LISTEN': {
        'title': 'LISTEN',
        'body': 'LISTEN CANNOT OPEN YET. THIS BUILD CARRIES TEXT AND CALL ONLY, AND NO AUDIO IS ON THE PHONE. PRESS ENTER TO GO BACK TO THE MENU.',
    },
    'NEWCONTACT': {
        'title': 'NEW CONTACT',
        'body': 'A CONTACT CANNOT BE SAVED YET. THE PHONE HAS ROOM TO STORE ONE, BUT THIS SCREEN IS NOT BUILT. TYPE THE NUMBER INTO THE TO FIELD FOR NOW.',
    },
}

# --- State ---
HOME_MENU = ['TEXT', 'CALL', 'READ', 'LISTEN', 'CONTACTS']

state = {
    'screen':           'lock',
    'home_index':       0,          # -1=header | 0=TEXT 1=CALL 2=READ 3=LISTEN 4=CONTACTS
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
    'confirm_sel':      'keep',     # 'keep' | 'discard' — leaving compose with a draft
    'stub_key':         '',
    'stub_return':      'home',     # screen to return to on Esc/Enter
    'quote_index':      0,
    'messages':         [],         # [{sender, name, body, read, ts}]
    'last_sid':         None,

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
    'edit_first':  '',
    'edit_last':   '',
    'edit_number': '',
    'edit_index':  0,               # -1=cancel | 0=first | 1=last | 2=number | 3=save

    'calls':            [],         # [{name, tag, time, duration}] session call log
    'calls_index':      0,          # -1=header | 0=DIAL A NUMBER | 1..=calls[i-1]
    'calls_start':      0,          # first entry in the 6-row window
    'dial_buffer':      '',
    'dial_quick_index': -1,         # -1=buffer active, >=0 selects a quick-dial contact
    'call_name':        '',
    'call_started_at':  None,

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

client = Client(ACCOUNT_SID, AUTH_TOKEN) if all([ACCOUNT_SID, AUTH_TOKEN]) else None

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
    # OS 0.2 stored a sent message as sender=<own number> with no recipient.
    return 'peer' not in m and bool(TWILIO_NUMBER) and m.get('sender') == TWILIO_NUMBER


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
            if not wait_for_ready():
                print(f"Warning: Inkplate not ready, skipping: {command[:40]}")
                continue
            spi.xfer2(build_payload(command))


# ─── Screen Builders ──────────────────────────────────────────────────────────

def push_lock():
    now = datetime.now()
    time_str = now.strftime("%-I:%M %p")
    date_str = now.strftime("%A, %B %-d").upper()
    quote = QUOTES[state['quote_index'] % len(QUOTES)]
    # Truncate quote to fit within PAYLOAD_BYTES (prefix + separators ≈ 30 chars overhead)
    max_quote = PAYLOAD_BYTES - 3 - len("LOCK|") - len(time_str) - len(date_str) - len("- THICH NHAT HANH") - 4
    push_screen(f"LOCK|{time_str}|{date_str}|{quote[:max_quote]}|- THICH NHAT HANH")


def push_home2():
    now = datetime.now()
    time_str = now.strftime("%-I:%M %p")
    with state['lock']:
        unread = sum(1 for m in state['messages'] if not m['read'])
        home_index = state['home_index']
    push_screen(f"HOME2|{time_str}|{home_index}|{unread}")


def push_texts():
    threads = get_threads()
    with state['lock']:
        idx = state['texts_index']
        hdr = state['texts_header_sel']

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
        msg       = state['compose_msg'][:60]
        to_active = '1' if state['compose_to_active'] else '0'
        hdr       = 'X' if state['compose_header_sel'] == 'x' else ''
        plus_sel  = '1' if state['compose_plus_sel'] else '0'
        send_sel  = '1' if state['compose_send_sel'] else '0'
    # A number that resolves to a saved contact displays as their name —
    # "the interactive reference drops the name into the TO field" — while
    # sending still uses the underlying number captured in compose_to.
    to_c = find_contact(number=to_raw) if to_raw else None
    to_display = (dispname(to_c) if to_c else to_raw)[:40]
    push_screen(f"COMPOSE|{to_display}|{msg}|{to_active}|{hdr}|{plus_sel}|{send_sel}")


def push_stub():
    with state['lock']:
        key = state['stub_key']
    info = STUB_INFO.get(key, {'title': key, 'body': f'{key} CANNOT OPEN YET.'})
    push_screen(f"STUB|{info['title']}|{info['body']}")


def push_confirm_discard():
    with state['lock']:
        sel = state['confirm_sel']
    push_screen(f"CONFIRMDISCARD|{'D' if sel == 'discard' else 'K'}")


def _filtered_contacts():
    with state['lock']:
        query = state['contacts_query'].lower()
    return [c for c in CONTACTS if dispname(c).lower().startswith(query)]


def push_contacts():
    with state['lock']:
        idx   = state['contacts_index']
        hdr   = state['contacts_header_sel']
        query = state['contacts_query']
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
    push_screen(f"CONTACTEDIT|{first}|{last}|{number}|{idx}")


def push_calls():
    with state['lock']:
        idx   = state['calls_index']
        calls = list(state['calls'])
    # DIAL A NUMBER is the first entry of the list, so it scrolls like any row.
    entries = [('DIAL A NUMBER', 'NEW', '', '')] + [
        (c['name'][:LIST_NAME_MAX], c['tag'], c['time'], c['duration']) for c in calls]
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
        'confirm_discard': push_confirm_discard, 'contacts_pick': push_contacts,
        'contact': push_contact, 'contact_edit': push_contact_edit,
        'calls_list': push_calls, 'dial': push_dial,
        'outgoing': push_call_screen, 'incoming': push_call_screen,
        'in_call': push_call_screen,
    }
    pusher = pushers.get(screen_name)
    if pusher:
        pusher()


# ─── State Machine ────────────────────────────────────────────────────────────

# Screens where printable characters are real typed input, so Q/WASD must
# stay literal there instead of acting as back/arrow shortcuts.
TYPING_SCREENS = ('thread', 'compose', 'contacts_pick', 'contact_edit')


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

    elif screen == 'confirm_discard':
        _from_confirm_discard(keycode)

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
        elif idx == 0:   # TEXT
            with state['lock']:
                state['screen']           = 'texts_list'
                state['texts_index']      = 0
                state['texts_header_sel'] = 'back'
            push_texts()
        elif idx == 1:  # CALL
            with state['lock']:
                state['screen']      = 'calls_list'
                state['calls_index'] = 0
            push_calls()
        elif idx == 4:  # CONTACTS
            with state['lock']:
                state['screen']              = 'contacts_pick'
                state['contacts_query']      = ''
                state['contacts_index']      = 0
                state['contacts_header_sel'] = 'back'
                state['contacts_return']     = 'home'
            push_contacts()
        else:  # READ, LISTEN — not built yet
            with state['lock']:
                state['screen']      = 'stub'
                state['stub_key']    = HOME_MENU[idx]
                state['stub_return'] = 'home'
            push_stub()
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
    with state['lock']:
        idx = state['texts_index']
        hdr = state['texts_header_sel']

    threads = get_threads()
    max_idx = max(0, len(threads) - 1)

    if keycode == 'KEY_DOWN':
        changed = False
        if idx == -1:
            # From header → first row
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
            state['screen']             = 'confirm_discard'
            state['confirm_sel']        = 'keep'
            state['compose_header_sel'] = None
        push_confirm_discard()
    else:
        with state['lock']:
            state['screen']             = 'texts_list'
            state['compose_header_sel'] = None
        push_texts()


def _send_compose():
    with state['lock']:
        to_val  = state['compose_to'].strip()
        msg_val = state['compose_msg'].strip()
    if not (to_val and msg_val):
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

    # Order matches the interactive reference's handleKey exactly — including
    # the fact that plain ArrowUp always claims the header before send_sel's
    # own ArrowUp branch ever gets a chance to run.
    if keycode == 'KEY_ESC':
        _leave_compose()

    elif header_sel is None and keycode == 'KEY_UP':
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
                state['compose_to']       = (state['compose_to'] + char)[:40]
            else:
                state['compose_msg']      = (state['compose_msg'] + char)[:60]
            state['compose_send_sel'] = False
        push_compose()


def _from_confirm_discard(keycode):
    if keycode == 'KEY_LEFT':
        with state['lock']:
            state['confirm_sel'] = 'discard'
        push_confirm_discard()
    elif keycode == 'KEY_RIGHT':
        with state['lock']:
            state['confirm_sel'] = 'keep'
        push_confirm_discard()
    elif keycode == 'KEY_ESC':
        with state['lock']:
            state['screen']      = 'compose'
            state['confirm_sel'] = 'keep'
        push_compose()
    elif keycode == 'KEY_ENTER':
        with state['lock']:
            sel = state['confirm_sel']
        if sel == 'discard':
            with state['lock']:
                state['screen']             = 'texts_list'
                state['compose_to']         = ''
                state['compose_msg']        = ''
                state['compose_header_sel'] = None
                state['confirm_sel']        = 'keep'
            push_texts()
        else:
            with state['lock']:
                state['screen']      = 'compose'
                state['confirm_sel'] = 'keep'
            push_compose()


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
    with state['lock']:
        idx   = state['contacts_index']
        hdr   = state['contacts_header_sel']
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
                with state['lock']:
                    state['screen']      = 'stub'
                    state['stub_key']    = 'NEWCONTACT'
                    state['stub_return'] = 'contacts_pick'
                push_stub()
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


def _open_contact_edit(new):
    """The edit form. `new` = a blank contact for a number that is not saved
    yet (the number is filled in, formatted); otherwise the viewed contact."""
    rec, number, _ = _contact_view()
    with state['lock']:
        cidx = state['contact_idx']
    if new or rec is None:
        edit_idx, first, last, num = None, '', '', format_number(number)
    else:
        edit_idx, first, last, num = cidx, rec.get('first', ''), rec.get('last', ''), rec.get('number', '')
    with state['lock']:
        state['screen']      = 'contact_edit'
        state['edit_idx']    = edit_idx
        state['edit_first']  = first
        state['edit_last']   = last
        state['edit_number'] = num
        state['edit_index']  = 0
    push_contact_edit()


def _save_contact_edit():
    with state['lock']:
        first    = state['edit_first'].strip()
        last     = state['edit_last'].strip()
        number   = state['edit_number'].strip()
        edit_idx = state['edit_idx']
    if not first and not last:
        return
    record = {'first': first, 'last': last, 'number': number}
    if edit_idx is not None and 0 <= edit_idx < len(CONTACTS):
        CONTACTS[edit_idx].update(record)
        pos = edit_idx
    else:
        CONTACTS.append(record)
        pos = len(CONTACTS) - 1
    _save_contacts(CONTACTS)
    with state['lock']:
        state['contact_idx']    = pos
        state['contact_number'] = ''
        state['screen']         = 'contact'
        state['contact_sel']    = 'edit'
    push_contact()


def _from_contact_edit(keycode):
    with state['lock']:
        idx = state['edit_index']

    if keycode == 'KEY_ESC':
        with state['lock']:
            state['screen'] = 'contact'
        push_contact()
    elif keycode == 'KEY_UP':
        with state['lock']:
            state['edit_index'] = max(-1, idx - 1)
        push_contact_edit()
    elif keycode == 'KEY_DOWN':
        with state['lock']:
            state['edit_index'] = min(len(EDIT_FIELDS), idx + 1)
        push_contact_edit()
    elif keycode == 'KEY_ENTER':
        if idx == -1:
            with state['lock']:
                state['screen'] = 'contact'
            push_contact()
        elif idx == len(EDIT_FIELDS):
            _save_contact_edit()
        else:
            with state['lock']:
                state['edit_index'] = idx + 1
            push_contact_edit()
    elif keycode == 'KEY_BACKSPACE' and 0 <= idx < len(EDIT_FIELDS):
        field = EDIT_FIELDS[idx]
        with state['lock']:
            state[f'edit_{field}'] = state[f'edit_{field}'][:-1]
        push_contact_edit()
    elif keycode.startswith('CHAR:') and 0 <= idx < len(EDIT_FIELDS):
        char  = keycode[5:]
        field = EDIT_FIELDS[idx]
        if field == 'number' and not re.match(r'^[0-9()+\-. ]$', char):
            return
        limit = 18 if field == 'number' else 10
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
    if keycode == 'KEY_ESC':
        with state['lock']:
            state['screen'] = 'calls_list'
        push_calls()
    elif keycode == 'KEY_ENTER':
        with state['lock']:
            state['screen']          = 'in_call'
            state['call_started_at'] = time.time()
        push_call_screen()


def _from_incoming(keycode):
    if keycode == 'KEY_ESC':
        with state['lock']:
            state['screen'] = 'home'
        push_home2()
    elif keycode == 'KEY_ENTER':
        with state['lock']:
            state['screen']          = 'in_call'
            state['call_started_at'] = time.time()
        push_call_screen()


def _from_in_call(keycode):
    if keycode == 'KEY_ESC':
        with state['lock']:
            state['screen']          = 'calls_list'
            state['call_started_at'] = None
        push_calls()


# ─── Persistence ──────────────────────────────────────────────────────────────

def load_messages():
    try:
        with open(MESSAGES_FILE, 'r') as f:
            data = json.load(f)
        state['messages'] = data.get('messages', [])
        state['last_sid']  = data.get('last_sid')
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
            json.dump({'messages': state['messages'], 'last_sid': state['last_sid']}, f)
    except Exception as e:
        print(f"Warning: could not save messages: {e}")


# ─── Twilio ───────────────────────────────────────────────────────────────────

def _transport_send(to_number, body):
    """Hand one text to the radio. Raises if it could not be sent.

    There is no cellular modem yet and Twilio is switched off, so on the phone
    this raises ('no service') and the message becomes NOT SENT. That is the
    normal outcome for now, not an error case."""
    if SIM_MODE:
        time.sleep(SIM_SEND_DELAY)
        if SIM_SEND != 'sent':
            raise RuntimeError('simulated: no service')
        return
    if client is None:
        raise RuntimeError('no service')
    msg = client.messages.create(body=body, from_=TWILIO_NUMBER, to=normalize_number(to_number))
    print(f"  → sent: {body} (SID: {msg.sid})")


def _run_async(fn):
    """Run `fn` off the input thread, so a slow radio never freezes the keyboard.
    (Tests replace this with a direct call.)"""
    threading.Thread(target=fn, daemon=True).start()


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


def sms_loop():
    if client is None:
        print("SMS polling disabled (no Twilio credentials).")
        return
    print(f"Polling for SMS every {SMS_POLL_INTERVAL}s...")
    while state['running']:
        try:
            messages = client.messages.list(to=TWILIO_NUMBER, limit=5)
            for msg in messages:
                if msg.sid == state['last_sid']:
                    break
                if msg.direction != 'inbound':
                    continue
                with state['lock']:
                    state['last_sid'] = messages[0].sid
                    name = format_name(msg.from_)
                    state['messages'].append({
                        'sender': msg.from_,
                        'name':   name,
                        'body':   msg.body,
                        'read':   False,
                        'ts':     datetime.now().isoformat(),
                    })
                save_messages()
                print(f"\n[NEW SMS] {name}: {msg.body}")
                with state['lock']:
                    current_screen = state['screen']
                    thread_id      = state['thread_id']
                if current_screen == 'thread' and thread_id == msg.from_:
                    push_thread2()
                elif current_screen in ('texts_list',):
                    push_texts()
                # Other screens: message waits silently (badge visible on home/texts)
                break
        except Exception as e:
            print(f"Poll error: {e}")
        time.sleep(SMS_POLL_INTERVAL)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    load_messages()

    # Backfill recent messages from Twilio on first run
    if not SIM_MODE and state['last_sid'] is None and client is not None:
        try:
            recent  = client.messages.list(to=TWILIO_NUMBER, limit=20)
            if recent:
                state['last_sid'] = recent[0].sid
            inbound = [m for m in recent if m.direction == 'inbound']
            for msg in reversed(inbound):
                ts = msg.date_created.isoformat() if msg.date_created else datetime.now().isoformat()
                state['messages'].append({
                    'sender': msg.from_,
                    'name':   format_name(msg.from_),
                    'body':   msg.body,
                    'read':   True,
                    'ts':     ts,
                })
            if inbound:
                save_messages()
                print(f"Backfilled {len(inbound)} messages.")
        except Exception as e:
            print(f"Warning: could not backfill: {e}")

    threading.Thread(target=clock_loop,      daemon=True).start()
    threading.Thread(target=sms_loop,        daemon=True).start()
    threading.Thread(target=call_timer_loop, daemon=True).start()

    if not SIM_MODE:
        threading.Thread(target=_spi_sender_loop, daemon=True).start()
        KeyboardHandler(handle_key).start()
        TrackpadHandler(handle_key).start()

    print("\n--- KyPhone OS 0.2 ---")
    if TWILIO_NUMBER and not SIM_MODE:
        print(f"Number: {TWILIO_NUMBER}")

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
