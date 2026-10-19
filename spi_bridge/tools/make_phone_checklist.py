#!/usr/bin/env python3
"""Write planning/phone-checklist.md: the checks to do by hand on the real phone.

Part one is what no test can see: the panel, the radio, the sound, the keyboard and trackpad, the light. Part two is
the navigation table from tests/test_navigation.py, the same rows the tests check in the emulator, written for a
person holding the phone, so a hand check and a test never describe different behaviour. Never edit the .md;
`--check` (a test) fails when it is out of date.

    python3 spi_bridge/tools/make_phone_checklist.py            # write it
    python3 spi_bridge/tools/make_phone_checklist.py --check    # is it up to date?
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, '..', '..', 'planning', 'phone-checklist.md')
sys.path.insert(0, os.path.join(HERE, '..', 'tests'))

# What the emulator cannot show. Each is (what to do, what should happen).
HARDWARE = [
    ('The panel', [
        ('Wake the phone and open every home menu item', 'Every screen is drawn whole: no missing rows, no garbage, nothing cut mid-word'),
        ('Scroll a long list (texts, contacts) up and down', 'Partial refreshes only, no flashing while scrolling; a full refresh moving between home and an app'),
        ('Leave a screen up for a minute, then move', 'No ghosting left behind after the next full refresh'),
        ('Open a book and turn ten pages', 'Every page arrives complete; the footer shows the chapter, page and %'),
        ('Receive a text with an emoji', 'The emoji is drawn as a picture in the bubble and the texts list'),
    ]),
    ('Speed (the timing line on the serial log)', [
        ('Move one row on the home menu', 'About half a second; the log shows total near 465 ms for a partial frame'),
        ('Open an app', 'About 1.3 s for its full refresh'),
    ]),
    ('Keyboard and trackpad', [
        ('Type a sentence in a note', 'Every letter appears; Q, W, A, S, D type letters'),
        ('Swipe the trackpad in all four directions, in both trackpad modes', 'Each moves the selection the right way (left is left)'),
        ('Press the coloured buttons: circle, triangle, cross', 'Circle: lock, then home; triangle: up a level; cross: lock, then back where you were'),
        ('Leave the phone alone for 30 s on the home menu', 'It locks itself; any key returns to the home menu'),
    ]),
    ('Screen light', [
        ('Settings > Screen light, step it up and down', 'The light follows each step, 0 to 8'),
        ('Leave it for 5 s', 'The light goes off; the next key turns it back on'),
    ]),
    ('Texting (the modem; sends use the plan allowance, so a handful only)', [
        ('Look at the home menu', 'Signal bars match the reception (not a fixed three)'),
        ('Send a text to your own phone', 'SENDING... then SENT; it arrives'),
        ('Reply from your own phone, including a long text and an emoji', 'It arrives in the same conversation, joined, with the emoji'),
        ('Take the SIM out (or go out of signal) and send', 'NOT SENT; Enter on it retries once signal is back'),
        ('Power the phone off and on, then text it', 'Texting works without a restart (the modem is retried every 10 s)'),
    ]),
    ('Music (the headphone output)', [
        ('Play an album with headphones in', 'Sound in both ears; Up/Down change the volume'),
        ('Leave now playing, go home', 'The music goes on; the equalizer mark shows by LISTEN'),
        ('Restart the phone, open LISTEN', 'RESUME leads the list and starts where it stopped'),
    ]),
    ('Wi-Fi and Bluetooth (the real radios)', [
        ('Settings > Wi-Fi, join a network with a wrong then a right password', 'COULDNT CONNECT, then CONNECTED; Settings shows it joined'),
        ('Settings > Bluetooth, PAIR NEW DEVICE with headphones', 'They pair and connect'),
        ('Try to switch Bluetooth off', 'Refused while the keyboard is connected'),
    ]),
    ('Add from a computer', [
        ('Settings > Add from a computer, open the address on a laptop', 'The page asks for the six-digit code shown on the phone'),
        ('Drop a book, a song and a .vcf', 'Each is listed on the phone; the book, album and contacts appear in their apps'),
    ]),
]

KEY_NAMES = {'KEY_UP': 'Up', 'KEY_DOWN': 'Down', 'KEY_LEFT': 'Left', 'KEY_RIGHT': 'Right', 'KEY_ENTER': 'Enter',
             'KEY_ESC': 'Esc', 'KEY_BACKSPACE': 'Backspace', 'KEY_TAB': 'Tab', 'KEY_HOME': 'circle',
             'KEY_LOCK': 'cross', 'CHAR: ': 'Space'}

# Where each route starts, for a person (routes are named for the screen they reach).
PLACES = {
    'home': 'the home menu', 'lock': 'the lock screen (locked from home)', 'incoming': 'an incoming call (i on home)',
    'texts_list': 'the texts', 'thread': "Ann's conversation", 'thread_unsaved': "a stranger's conversation",
    'message': 'a message opened full screen', 'stub': 'the nothing-to-send alert', 'compose': 'New Message',
    'confirm_discard': 'Discard this message?', 'contacts_pick': 'the address book',
    'contact_saved': "Ann's page", 'contact_nonumber': "the page of a contact with no number",
    'contact_unsaved': "a stranger's page", 'contact_edit': "Ann's edit form",
    'confirm_delete_contact': 'Delete contact?', 'calls_list': 'the call list', 'dial': 'the dialer',
    'outgoing': 'a call ringing out', 'in_call': 'a call in progress', 'library': 'the books', 'reader': 'a book',
    'chapters': 'the chapters', 'music': 'the albums', 'tracks': 'an album', 'nowplaying': 'now playing',
    'notes_list': 'the notes', 'note': 'a note', 'confirm_delete_note': 'Delete note?', 'settings': 'Settings',
    'light': 'Screen light', 'upload': 'Add from a computer', 'wifi': 'the Wi-Fi list',
    'confirm_forget_wifi': 'Forget network?', 'netpass': 'the Wi-Fi password box', 'netstate': 'a failed connection',
    'bluetooth': 'the Bluetooth list', 'confirm_bt_device': 'a paired device (Forget / Re-connect)',
    'btpair': 'OTHER DEVICES',
}

APPS = [('Lock and home', ('lock', 'home', 'incoming')),
        ('Texts', ('texts_list', 'thread', 'thread_unsaved', 'message', 'stub', 'compose', 'confirm_discard')),
        ('Contacts', ('contacts_pick', 'contact_saved', 'contact_nonumber', 'contact_unsaved', 'contact_edit',
                      'confirm_delete_contact')),
        ('Calls', ('calls_list', 'dial', 'outgoing', 'in_call')),
        ('Books', ('library', 'reader', 'chapters')),
        ('Music', ('music', 'tracks', 'nowplaying')),
        ('Notes', ('notes_list', 'note', 'confirm_delete_note')),
        ('Settings', ('settings', 'light', 'upload', 'wifi', 'confirm_forget_wifi', 'netpass', 'netstate', 'bluetooth',
                      'confirm_bt_device', 'btpair'))]


def keys_text(keys):
    words, run = [], None
    for k in keys:
        if callable(k):
            word = 'down to %s' % k.__closure__[0].cell_contents
        else:
            word = KEY_NAMES.get(k, k[5:] if k.startswith('CHAR:') else k)
        if words and words[-1][0] == word and len(word) > 1:
            words[-1][1] += 1
        else:
            words.append([word, 1])
    out = []
    for word, n in words:
        out.append(word if n == 1 else '%s x%d' % (word, n))
    text = ', '.join(out)
    for typed in ('5, 5, 5, 0, 1, 0, 0, 1, 0, 1', 'h, u, n, t, e, r, 2, 2'):          # typed words read as words
        text = text.replace(typed, 'type %s' % typed.replace(', ', ''))
    return text or '(as it opens)'


def checklist():
    import test_navigation as nav
    order = [r for _app, routes in APPS for r in routes]
    missing = set(nav.ROUTES) - set(order)
    assert not missing, 'routes with no app in make_phone_checklist.APPS: %s' % sorted(missing)
    lines = ['# KyPhone phone checklist', '',
             '*Generated by `spi_bridge/tools/make_phone_checklist.py` from `tests/test_navigation.py`; never edit '
             'by hand.*', '',
             'Part 1 is what only the real phone can show. Part 2 is the navigation the tests already check in the '
             'emulator, for a run-through on the phone after a big change. The test phone has three contacts (Ann Lee, '
             'Pip Okonkwo, Zed with no number), a conversation with Ann and one with a number not in contacts, a book, '
             'an album, a note and a call; on the real phone, use your own.', '',
             '## Part 1: only on the phone', '']
    for title, checks in HARDWARE:
        lines += ['### ' + title, '']
        lines += ['- [ ] %s. **Expect:** %s.' % (do, expect) for do, expect in checks]
        lines.append('')
    lines += ['## Part 2: navigation, screen by screen (%d checks)' % len(nav.MAP), '']
    for app, routes in APPS:
        lines += ['### ' + app, '']
        for route in routes:
            rows = [r for r in nav.MAP if r.start == route]
            if not rows:
                continue
            lines.append('**From %s:**' % PLACES[route])
            lines.append('')
            lines += ['- [ ] %s: %s.' % (keys_text(r.keys), r.says) for r in rows]
            lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


def main(argv):
    text = checklist()
    if '--check' in argv:
        with open(OUT) as f:
            if f.read() != text:
                print('planning/phone-checklist.md is out of date: run tools/make_phone_checklist.py')
                return 1
        return 0
    with open(OUT, 'w') as f:
        f.write(text)
    print('wrote %s' % os.path.relpath(OUT))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
