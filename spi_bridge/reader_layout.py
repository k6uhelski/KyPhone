"""reader_layout.py — wrap a book's paragraphs into pages, and pages into wire frames.

The Inkplate draws book text with FreeSerif (spi_bridge/reader_fonts.py) and sums each glyph's advance as it
prints, so this module can predict exactly how wide a line will draw. It never asks the panel; it decides which
words go on which line and which lines go on which page, and the panel just draws the lines it is sent.

    pages = paginate(chapter.paras, 'M')          # [Page(start, lines)]
    i = page_index(pages, saved_offset)           # the page that contains a saved reading position
    frames = page_frames('M', pages[i].lines, 'Chapter one', '3/40  7%', 'P')

A reading position is an integer offset into the chapter's text: the paragraphs' characters, one extra count
per paragraph. It does not depend on the font size, so a saved position survives a change of size.

Geometry (the panel is 600x600), shared with the emulator and the firmware:
    text column   x = TEXT_X .. TEXT_X + TEXT_W, first line box at TOP, room for lines_per_page(size) lines
    footer        a rule at FOOT_RULE_Y and a line of built-in-font text at FOOT_BASE_Y (see page_frames)
    baseline(row) = TOP + row * y_advance + (3 * y_advance) // 4       (integer arithmetic in the firmware too)
"""

import re

from reader_fonts import FONTS, SIZES

SCREEN = 600
TEXT_X = 32
TEXT_W = SCREEN - 2 * TEXT_X                  # 536
TOP = 24
TEXT_BOTTOM = 556                             # the text column ends here
FOOT_RULE_Y = 564
FOOT_BASE_Y = 590                             # baseline of the footer's built-in-font text (size 2: 12x16 cell)
FOOT_CELL = 12                                # width of one footer character
FOOT_COLS = TEXT_W // FOOT_CELL               # 44 characters across the footer

READER_MAX_ROW = 63                           # the firmware ignores rows past this (a page has at most 24)

INDENT = 3                                    # spaces before the first line of a paragraph
SEP = '\xb7'                                  # sub-field separator on the wire (one byte, 0xB7)
MAX_COMMAND_CHARS = 253                       # one SPI frame; must match kyphone_os.MAX_COMMAND_CHARS

_ADVANCE = {s: {code: g[2] for code, g in FONTS[s]['glyphs'].items()} for s in SIZES}


def text_width(size, text):
    """Pixels the panel advances while printing `text` in this size (GFX sums glyph advances; no kerning)."""
    adv = _ADVANCE[size]
    return sum(adv[ord(c)] for c in text)


def y_advance(size):
    return FONTS[size]['y_advance']


def lines_per_page(size):
    return (TEXT_BOTTOM - TOP) // y_advance(size)


def baseline(size, row):
    y = y_advance(size)
    return TOP + row * y + (3 * y) // 4


# ─── Wrapping ─────────────────────────────────────────────────────────────────

# A line may also break after a hyphen or a "--" that sits between two other characters ("Caucus-Race", "bank--the").
_BREAK_AFTER_DASH = re.compile(r'(?<=[^\s-]-)(?=[^\s-])|(?<=[^\s-]--)(?=[^\s-])')


def _segments(text):
    """[(segment, pos, joined)]: pieces that may start a line. `pos` is where the piece starts in `text`; `joined`
    means it follows the previous piece with no space between (a break after a hyphen)."""
    out, pos = [], 0
    for word in text.split(' '):
        if word == '':
            pos += 1
            continue
        start = 0
        pieces = _BREAK_AFTER_DASH.split(word)
        for k, piece in enumerate(pieces):
            out.append((piece, pos + start, k > 0))
            start += len(piece)
        pos += len(word) + 1
    return out


def _break_long(size, piece, avail):
    """Split a piece that is wider than a whole line into pieces that each fit."""
    adv = _ADVANCE[size]
    parts, cur, w = [], '', 0
    for c in piece:
        a = adv[ord(c)]
        if cur and w + a > avail:
            parts.append(cur)
            cur, w = '', 0
        cur += c
        w += a
    if cur:
        parts.append(cur)
    return parts


def _pieces(size, text, room):
    """[(piece, pos, joined)]: the words of `text` (split at spaces and after inner dashes), with any piece wider
    than `room` cut into pieces that fit. `joined` = no space before this piece."""
    out = []
    for seg, pos, joined in _segments(text):
        if text_width(size, seg) <= room:
            out.append((seg, pos, joined))
            continue
        at = pos
        for k, part in enumerate(_break_long(size, seg, room)):
            out.append((part, at, joined if k == 0 else True))
            at += len(part)
    return out


def wrap(size, text, indent=0, avail=TEXT_W):
    """Greedy word wrap. -> [(line text, start position in `text`)]. The first line is indented by `indent` spaces.
    A word wider than a line is broken. No line is wider than `avail`."""
    space = _ADVANCE[size][32]
    lead_w = space * indent
    lines = []
    cur, cur_w, cur_pos = '', 0, 0

    def close():
        nonlocal cur, cur_w
        if cur:
            lines.append((' ' * indent + cur if not lines else cur, cur_pos))
        cur, cur_w = '', 0

    for piece, pos, joined in _pieces(size, text, avail - lead_w):
        w = text_width(size, piece)
        gap = space if (cur and not joined) else 0
        if cur and cur_w + gap + w > avail - (0 if lines else lead_w):
            close()
            gap = 0
        if not cur:
            cur_pos = pos
        cur += (' ' if gap else '') + piece
        cur_w += gap + w
    close()
    return lines


def _centred(size, line):
    """Pad with spaces so `line` sits in the middle of the column (space granularity)."""
    space = _ADVANCE[size][32]
    pad = max(0, (TEXT_W - text_width(size, line)) // 2 // space)
    return ' ' * pad + line


def _is_break_mark(text):
    return bool(text) and not text.strip('* ')


# ─── Pages ────────────────────────────────────────────────────────────────────

class Page:
    def __init__(self, start, lines):
        self.start = start                    # chapter offset of the first character on the page
        self.lines = lines                    # the rows drawn, '' for a blank row; never blank at the top or bottom


def chapter_length(paras):
    """Length of the chapter's text as positions count it: each paragraph's characters plus one."""
    return sum(len(t) + 1 for _k, t in paras)


def paginate(paras, size):
    """[('p'|'h', text)] -> [Page]. Headings and scene breaks are centred; a heading gets a blank row either side
    and is never left alone at the bottom of a page."""
    rows = lines_per_page(size)
    pages, cur, cur_start = [], [], None

    def new_page():
        nonlocal cur, cur_start
        while cur and cur[-1][0] == '':
            cur.pop()
        if cur:
            pages.append(Page(cur_start, [t for t, _p in cur]))
        cur, cur_start = [], None

    def put(text, pos):
        nonlocal cur_start
        if text == '' and not cur:
            return                            # never a blank row at the top of a page
        if len(cur) >= rows:
            new_page()
            if text == '':
                return
        if cur_start is None:
            cur_start = pos
        cur.append((text, pos))

    base = 0
    for kind, text in paras:
        heading = kind == 'h'
        centred = heading or _is_break_mark(text)
        wrapped = wrap(size, text, indent=0 if centred else INDENT)
        if heading:
            # keep a heading with what follows it: it needs its blank rows and two lines of text on the page
            if cur and len(cur) + 1 + len(wrapped) + 1 + 2 > rows:
                new_page()
            put('', base)
        for line, pos in wrapped:
            put(_centred(size, line.strip()) if centred else line, base + pos)
        if heading:
            put('', base)
        base += len(text) + 1
    new_page()
    return pages or [Page(0, [''])]


def page_index(pages, offset):
    """The page that contains chapter offset `offset` (the last page that starts at or before it)."""
    lo, hi = 0, len(pages) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if pages[mid].start <= offset:
            lo = mid
        else:
            hi = mid - 1
    return lo


# ─── Frames for the wire ──────────────────────────────────────────────────────

def fit_footer(left, right):
    """Truncate `left` with '...' so both fit across the footer (FOOT_COLS characters) with two spaces between."""
    room = FOOT_COLS - len(right) - 2
    if len(left) > room:
        left = left[:max(0, room - 3)].rstrip() + '...' if room > 3 else left[:max(0, room)]
    return left, right


def page_frames(size, lines, left, right, refresh):
    """One page as the commands to send, in order: RTEXT frames (the first clears the screen), then RFOOT, which
    draws the footer and refreshes the panel ('P' partial, 'F' full). Every frame is at most MAX_COMMAND_CHARS
    long. Blank rows are never sent; each frame starts at the row of its first line and never ends on a blank."""
    assert size in SIZES and refresh in ('P', 'F')
    frames, i, n, first = [], 0, len(lines), True
    while i < n:
        if lines[i] == '':
            i += 1
            continue
        head = 'RTEXT|%s|%d|%s|' % (size, i, 'S' if first else '-')
        if len(head) + len(lines[i]) > MAX_COMMAND_CHARS:
            raise ValueError('a line does not fit in one frame')
        body, last_real, j = [lines[i]], i, i + 1
        while j < n and len(head) + len(SEP.join(body + [lines[j]])) <= MAX_COMMAND_CHARS:
            body.append(lines[j])
            if lines[j] != '':
                last_real = j
            j += 1
        frames.append(head + SEP.join(body[:last_real - i + 1]))
        i, first = last_real + 1, False
    left, right = fit_footer(left, right)
    frames.append('RFOOT|%s|%s|%s' % (refresh, left, right))
    return frames


# ─── Glyph pixels (the emulator draws with these; tests measure with them) ────

def glyph_ink(size, ch):
    """[(dx, dy)] ink pixels of one glyph relative to (cursor x, baseline)."""
    w, h, _xa, xo, yo, bits = FONTS[size]['glyphs'][ord(ch)]
    if not w or not h:
        return []
    value = int(bits, 16)
    total = len(bits) * 4
    return [(xo + i % w, yo + i // w) for i in range(w * h) if (value >> (total - 1 - i)) & 1]


def text_ink(size, x, base, text):
    """[(x, y)] ink pixels of a whole string printed with its origin at (x, baseline)."""
    out = []
    cx = x
    for ch in text:
        out.extend((cx + dx, base + dy) for dx, dy in glyph_ink(size, ch))
        cx += _ADVANCE[size][ord(ch)]
    return out
