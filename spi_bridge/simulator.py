"""
simulator.py — KyPhone display simulator for local development.

Renders KyPhone OS screen commands in a 600x600 pygame window.
Keyboard input maps to the same keycodes as the evdev handler.

Install: pip3 install pygame
Run:     python3 spi_bridge/kyphone_os.py --sim
"""

import sys
import threading
import pygame

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)

CLOCK_FONT = 'futura'

_FONT_CACHE = {}


def _get_font(px_size, bold=False, clock=False):
    key = (px_size, bold, clock)
    if key not in _FONT_CACHE:
        name = CLOCK_FONT if clock else 'courier'
        _FONT_CACHE[key] = pygame.font.SysFont(name, px_size, bold=bold)
    return _FONT_CACHE[key]


def wrap_words(text, cols):
    """Greedy word wrap to lines of at most `cols` characters; a word longer than
    a line is broken. MUST match kyphone_os.wrap_words (a test asserts it)."""
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


class Simulator:
    WIDTH  = 600
    HEIGHT = 600

    # Inkplate textSize N → char cell: width=6N px, height=8N px

    KEY_MAP = {
        pygame.K_UP:        'KEY_UP',
        pygame.K_DOWN:      'KEY_DOWN',
        pygame.K_LEFT:      'KEY_LEFT',
        pygame.K_RIGHT:     'KEY_RIGHT',
        pygame.K_RETURN:    'KEY_ENTER',
        pygame.K_BACKSPACE: 'KEY_BACKSPACE',
        pygame.K_ESCAPE:    'KEY_ESC',
        pygame.K_TAB:       'KEY_TAB',
    }

    def __init__(self, on_key):
        self.on_key   = on_key
        self._lock    = threading.Lock()
        self._pending = None
        self._surface = None
        self._ready   = False

    def init(self):
        pygame.init()
        self._surface = pygame.display.set_mode((self.WIDTH, self.HEIGHT))
        self._ready   = True
        pygame.display.set_caption('KyPhone Simulator')
        self._surface.fill(WHITE)
        pygame.display.flip()

    def render(self, command):
        if not self._ready:
            return
        with self._lock:
            self._pending = command
        pygame.event.post(pygame.event.Event(pygame.USEREVENT))

    def run_loop(self):
        clock = pygame.time.Clock()
        while True:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit(0)
                elif event.type == pygame.USEREVENT:
                    with self._lock:
                        cmd = self._pending
                    if cmd:
                        self._draw(cmd)
                elif event.type == pygame.KEYDOWN:
                    keycode = self.KEY_MAP.get(event.key)
                    if keycode:
                        threading.Thread(
                            target=self.on_key, args=(keycode,), daemon=True
                        ).start()
                    elif event.unicode and event.unicode.isprintable() and len(event.unicode) == 1:
                        char = event.unicode
                        threading.Thread(
                            target=self.on_key, args=(f'CHAR:{char}',), daemon=True
                        ).start()
            clock.tick(60)

    # ── Internal draw helpers ─────────────────────────────────────────

    def _font(self, text_size, bold=False):
        return _get_font(text_size * 8, bold)

    def _char_w(self, text_size):
        return text_size * 6

    def _text(self, text, x, y, text_size, color=BLACK, bold=False):
        font = self._font(text_size, bold)
        img  = font.render(str(text), True, color)
        self._surface.blit(img, (x, y))

    def _text_centered(self, text, y, text_size, color=BLACK, clock=False, bold=False):
        font = _get_font(text_size * 8, bold=(bold or clock), clock=clock)
        w    = font.size(str(text))[0]
        x    = (self.WIDTH - w) // 2
        self._surface.blit(font.render(str(text), True, color), (x, y))

    def _line(self, y, weight=1):
        pygame.draw.line(self._surface, BLACK, (0, y), (self.WIDTH, y), weight)

    def _wrap_lines(self, text, text_size, max_px):
        char_w   = self._char_w(text_size)
        max_chars = max_px // char_w
        words     = text.split(' ')
        lines, current = [], ''
        for word in words:
            if not current:
                current = word
            elif len(current) + 1 + len(word) <= max_chars:
                current += ' ' + word
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines or ['']

    def _draw(self, command):
        self._surface.fill(WHITE)
        if '|' in command:
            prefix, rest = command.split('|', 1)
        else:
            prefix, rest = command, ''

        if prefix == 'LOCK':
            self._draw_lock(rest)
        elif prefix == 'HOME2':
            self._draw_home2(rest)
        elif prefix == 'TEXTS':
            self._draw_texts(rest)
        elif prefix == 'THREAD2':
            self._draw_thread2(rest)
        elif prefix == 'COMPOSE':
            self._draw_compose(rest)
        elif prefix == 'STUB':
            self._draw_stub(rest)
        elif prefix == 'CONFIRMDISCARD':
            self._draw_confirm_discard(rest)
        elif prefix == 'CONTACTSPICK':
            self._draw_contacts_pick(rest)
        elif prefix == 'CONTACT':
            self._draw_contact(rest)
        elif prefix == 'CONTACTEDIT':
            self._draw_contact_edit(rest)
        elif prefix == 'CALLS':
            self._draw_calls(rest)
        elif prefix == 'DIAL':
            self._draw_dial(rest)
        elif prefix == 'CALLSTATE':
            self._draw_call_state(rest)
        # OS 0.0 legacy screens (kept for kyphone_app.py compatibility)
        elif prefix in ('HOME', 'HOME_FAST'):
            self._draw_home(rest)
        elif prefix in ('MSG_LIST', 'MSG_LIST_FAST'):
            self._draw_msg_list(rest)
        elif prefix == 'MSG_THREAD':
            self._draw_msg_thread(rest)
        else:
            self._draw_sms(command)

        pygame.display.flip()

    # ── OS 0.1 Screen Renderers ───────────────────────────────────────

    def _draw_lock(self, data):
        # data = "time_str|date_str|quote|attribution"
        parts = data.split('|')
        time_str = parts[0] if len(parts) > 0 else ''
        date_str = parts[1] if len(parts) > 1 else ''
        quote    = parts[2] if len(parts) > 2 else ''
        attr     = parts[3] if len(parts) > 3 else ''

        # OS version — bottom left, textSize 2 (18px design token)
        self._text('OS 0.2', 10, self.HEIGHT - 8 - 2 * 8, 2)

        # ASCII cat — bottom right (fixed bitmap on device; unchanged since 0.1)
        cat = [
            r"   )\._.,--....,'``.",
            r"  /,   _.. \   _\  (`._ ,.",
            r" `._.-(,_..'--(,_..'`-.;.'",
        ]
        cat_font = pygame.font.SysFont('courier', 12, bold=True)
        lh       = 18
        max_w    = max(cat_font.size(l)[0] for l in cat)
        cat_x    = self.WIDTH - max_w - 8
        cat_y    = self.HEIGHT - (lh * len(cat)) - 6  # bottom margin, computed from content height
        for i, line in enumerate(cat):
            img = cat_font.render(line, True, BLACK)
            self._surface.blit(img, (cat_x, cat_y + i * lh))

        # Clock — textSize 8, centered, top:159
        self._text_centered(time_str, 159, 8, clock=True)

        # Date — textSize 3, centered, top:243
        self._text_centered(date_str, 243, 3)

        # Quote — textSize 3, centered, wrapped, top:356, line-height 34
        quote_y  = 356
        margin   = 60
        max_px   = self.WIDTH - margin * 2
        lines    = self._wrap_lines(quote, 3, max_px)
        line_h   = 34
        for line in lines:
            w    = len(line) * self._char_w(3)
            x    = (self.WIDTH - w) // 2
            self._text(line, x, quote_y, 3)
            quote_y += line_h

        # Attribution — textSize 2, centered, 12px below the quote block
        if attr:
            w = len(attr) * self._char_w(2)
            x = (self.WIDTH - w) // 2
            self._text(attr, x, quote_y + 12, 2)

    def _draw_home2(self, data):
        # data = "time_str|home_index|unread"
        parts = data.split('|')
        time_str   = parts[0] if len(parts) > 0 else ''
        try:
            home_index = int(parts[1]) if len(parts) > 1 else 0
        except ValueError:
            home_index = 0
        try:
            unread = int(parts[2]) if len(parts) > 2 else 0
        except ValueError:
            unread = 0

        header_h   = 60
        header_sel = home_index == -1

        # Header: clock (left), battery + signal (right) — no KYPHONE label in 0.2
        if header_sel:
            pygame.draw.rect(self._surface, BLACK, (0, 0, self.WIDTH, header_h))
        header_fg = WHITE if header_sel else BLACK
        header_bg = BLACK if header_sel else WHITE
        self._text(time_str, 24, 18, 3, header_fg)
        self._draw_status_group(header_fg, header_bg, mid_y=header_h // 2)
        self._line(header_h, weight=2)

        # 5-row menu (TEXT/CALL/READ/LISTEN/CONTACTS), scrolling: 4 fill the
        # panel, the 5th scrolls into view when selected.
        row_h     = 135
        view_top  = header_h + 2
        view_h    = self.HEIGHT - view_top
        n         = len(self.HOME_MENU)
        shift     = max(0, (max(0, home_index) + 1) * row_h - view_h)

        prev_clip = self._surface.get_clip()
        self._surface.set_clip(pygame.Rect(0, view_top, self.WIDTH, view_h))
        for i, label in enumerate(self.HOME_MENU):
            y   = view_top + i * row_h - shift
            if y + row_h < view_top or y > self.HEIGHT:
                continue
            sel = i == home_index
            fg  = WHITE if sel else BLACK
            if sel:
                pygame.draw.rect(self._surface, BLACK, (0, y, self.WIDTH, row_h))

            # Label — textSize 6, bold, centered (icons are a separate task)
            label_font = _get_font(6 * 8, bold=True)
            label_w    = label_font.size(label)[0]
            label_x    = (self.WIDTH - label_w) // 2
            label_y    = y + (row_h - 6 * 8) // 2
            self._text(label, label_x, label_y, 6, fg, bold=True)

            # Unread count hangs to the right of the TEXT row's content
            if label == 'TEXT' and unread > 0:
                count_x = label_x + label_w + 24
                self._text(str(unread), count_x, label_y + (6 * 8 - 3 * 8) // 2, 3, fg)

            self._line(y + row_h, weight=1)
        self._surface.set_clip(prev_clip)

        # "More below" chevron — three shrinking bars, bottom right
        if n > 4 and home_index <= 3:
            cx = self.WIDTH - 12
            cy = self.HEIGHT - 6
            for w in (14, 8, 3):
                pygame.draw.rect(self._surface, BLACK, (cx - w, cy - 3, w, 3))
                cy -= 5

    HOME_MENU = ['TEXT', 'CALL', 'READ', 'LISTEN', 'CONTACTS']

    def _draw_status_group(self, fg, bg, mid_y):
        """Battery block + percentage + 4-bar signal staircase, right-aligned
        in the home header. No real telemetry exists yet — fixed placeholder
        values, swappable for real readings later."""
        batt_pct = 82
        pct_str  = f'{batt_pct}%'
        pct_w    = len(pct_str) * self._char_w(3)
        sig_heights = [5, 9, 13, 17]
        sig_w    = 4
        sig_gap  = 3
        sig_group_w = sig_w * 4 + sig_gap * 3

        batt_w, batt_h, batt_border = 34, 18, 2
        nub_w, nub_h = 3, 8

        total_w = batt_w + 2 + nub_w + 14 + pct_w + 14 + sig_group_w
        x = self.WIDTH - 24 - total_w

        # Battery block
        by = mid_y - batt_h // 2
        pygame.draw.rect(self._surface, fg, (x, by, batt_w, batt_h), batt_border)
        fill_w = int((batt_w - 2 * batt_border) * (batt_pct / 100))
        pygame.draw.rect(self._surface, fg, (x + batt_border, by + batt_border, fill_w, batt_h - 2 * batt_border))
        x += batt_w + 2
        pygame.draw.rect(self._surface, fg, (x, mid_y - nub_h // 2, nub_w, nub_h))
        x += nub_w + 14

        # Percentage
        self._text(pct_str, x, mid_y - 3 * 8 // 2, 3, fg)
        x += pct_w + 14

        # Signal staircase — last bar is an outline only
        sig_bottom = mid_y + sig_heights[-1] // 2
        for i, h in enumerate(sig_heights):
            rect = (x, sig_bottom - h, sig_w, h)
            if i == len(sig_heights) - 1:
                pygame.draw.rect(self._surface, fg, rect, 1)
            else:
                pygame.draw.rect(self._surface, fg, rect)
            x += sig_w + sig_gap

    def _text_bl(self, text, x, baseline, text_size, color=BLACK, bold=False):
        """Draw text with its baseline at `baseline`, so vertical placement
        does not depend on the font's own leading."""
        font = self._font(text_size, bold)
        self._surface.blit(font.render(str(text), True, color), (x, baseline - font.get_ascent()))

    def _text_right(self, text, right, baseline, text_size, color=BLACK, bold=False):
        font = self._font(text_size, bold)
        w = font.size(str(text))[0]
        self._surface.blit(font.render(str(text), True, color), (right - w, baseline - font.get_ascent()))
        return right - w

    def _draw_empty_state(self, title, hint, top, bottom):
        """Centered two-line message for an empty list: a 24px bold line naming
        the emptiness over an 18px line saying what to do (gap 14)."""
        lines = self._wrap_lines(hint, 2, self.WIDTH - 56)
        block = 24 + 14 + 22 * len(lines)
        y = top + (bottom - top - block) // 2
        self._text_centered(title, y, 3, bold=True)
        for i, line in enumerate(lines):
            self._text_centered(line, y + 24 + 14 + 22 * i, 2)

    def _draw_header_bar(self, title, back_active, plus_active, height=44, rule_weight=1):
        """Shared back/title/+ header used by list screens — each control is
        a literal 38x34 hit box that inverts when selected."""
        self._line(height - 1, weight=rule_weight)
        box_w, box_h = 38, 34

        def _btn(char, x, active):
            if active:
                pygame.draw.rect(self._surface, BLACK, (x, 6, box_w, box_h))
                fg = WHITE
            else:
                fg = BLACK
            cw = self._char_w(3)
            self._text(char, x + (box_w - cw) // 2, 6 + (box_h - 3 * 8) // 2, 3, fg, bold=True)

        _btn('<', 16, back_active)
        title_w = len(title) * self._char_w(3)
        self._text(title, (self.WIDTH - title_w) // 2, 10, 3, bold=True)
        _btn('+', self.WIDTH - 16 - box_w, plus_active)

    def _draw_texts(self, data):
        # data = "sel|name·preview·unread·time|..."
        # sel: -1=back, -2=plus, 0..4=row within the 5-row window
        parts = data.split('|')
        try:
            idx     = int(parts[0])
            entries = parts[1:]
        except (ValueError, IndexError):
            idx     = 0
            entries = parts
        entries = [e for e in entries if e]

        self._draw_header_bar('TEXT', idx == -1, idx == -2)
        if not entries:
            self._draw_empty_state('NO CONVERSATIONS', 'PRESS + TO WRITE THE FIRST MESSAGE.', 44, self.HEIGHT)
            return

        row_h  = 111
        margin = 28
        for i, entry in enumerate(entries[:5]):
            y = 44 + i * row_h
            name, preview, unread, time_str = (entry.split('\xb7') + ['', '', '', ''])[:4]
            sel = i == idx
            fg  = WHITE if sel else BLACK
            if sel:
                pygame.draw.rect(self._surface, BLACK, (0, y, self.WIDTH, row_h))

            # Name (left, bold if unread); time + chevron (right) share its baseline.
            self._text_bl(name, margin, y + 40, 3, fg, bold=(unread == '1'))
            chev_x = self._text_right('>', self.WIDTH - margin, y + 40, 2, fg)
            if time_str:
                self._text_right(time_str, chev_x - 10, y + 40, 2, fg)

            self._text_bl(preview, margin, y + 77, 2, fg)
            self._line(y + row_h - 1)

    def _draw_thread2(self, data):
        # data = "name|draft|hdr|code·time·text|..."   hdr: ''=composer 'B'=back 'I'=info
        # code: R received | Y0 sending | Y1 sent | Y2 not sent | Y3 not sent + selected (retry prompt)
        parts   = data.split('|')
        name    = parts[0] if parts else ''
        draft   = parts[1] if len(parts) > 1 else ''
        hdr     = parts[2] if len(parts) > 2 else ''
        entries = [e for e in parts[3:] if e]

        box_w, box_h = 38, 34
        back_sel, info_sel = hdr == 'B', hdr == 'I'
        if back_sel:
            pygame.draw.rect(self._surface, BLACK, (16, 6, box_w, box_h))
        self._text('<', 16 + (box_w - self._char_w(3)) // 2, 6 + (box_h - 24) // 2, 3,
                    WHITE if back_sel else BLACK, bold=True)
        self._text_centered(name, 10, 3, bold=True)
        info_x = self.WIDTH - 16 - box_w
        if info_sel:
            pygame.draw.rect(self._surface, BLACK, (info_x, 6, box_w, box_h))
        self._text('i', info_x + (box_w - self._char_w(3)) // 2, 6 + (box_h - 24) // 2, 3,
                    WHITE if info_sel else BLACK, bold=True)
        pygame.draw.rect(self._surface, BLACK, (0, 46, self.WIDTH, 2))

        parsed = []
        for e in entries:
            code, time_str, text = (e.split('\xb7', 2) + ['', '', ''])[:3]
            parsed.append((code, time_str, text))
        composer_active = not hdr and not any(c == 'Y3' for c, _, _ in parsed)

        # Composer: '> ' + the draft, word-wrapped to 30 columns, at most three
        # lines (kyphone_os.composer_view already trimmed the draft to fit).
        wrapped    = wrap_words('> ' + draft + '\0', 30)[:3]
        composer_h = 45 + (len(wrapped) - 1) * 34
        pygame.draw.rect(self._surface, BLACK, (0, self.HEIGHT - composer_h - 2, self.WIDTH, 2))
        for i, line in enumerate(wrapped):
            base = self.HEIGHT - composer_h + 8 + 34 * i + 26
            shown = line.replace('\0', '')
            self._text_bl(shown, 24, base, 3)
            if '\0' in line and composer_active:
                cx = 24 + self._font(3).size(shown)[0]
                pygame.draw.rect(self._surface, BLACK, (cx, base - 19, 18, 24))

        # Message area: bottom-anchored column of bubbles, 16px apart. An older
        # bubble (or a long one) runs off the top edge, clipped.
        area_top, area_bottom = 62, self.HEIGHT - composer_h - 17
        tail_widths, tail_seg_h = [4, 8, 14, 8, 4], 4
        line_h, pad_x, name_h, meta_h, gap = 35, 12, 27, 25, 16
        blocks = []
        for code, time_str, text in parsed:
            outgoing = code.startswith('Y')
            lines    = wrap_words(text, 20)
            bubble_h = 4 + 16 + line_h * len(lines)
            blocks.append({'code': code, 'time': time_str, 'lines': lines, 'outgoing': outgoing,
                           'bubble_h': bubble_h,
                           'h': (0 if outgoing else name_h) + bubble_h + meta_h})

        self._surface.set_clip(pygame.Rect(0, area_top, self.WIDTH, area_bottom - area_top))
        y_bottom = area_bottom
        for b in reversed(blocks):
            top = y_bottom - b['h']
            y0  = top
            if not b['outgoing']:
                self._text_bl(name.upper(), 30, top + 16, 2, bold=True)
                y0 += name_h
            widest = max((len(l) for l in b['lines']), default=0) * self._char_w(3)
            bw = min(400, widest + 2 * pad_x + 4)
            bx = (self.WIDTH - 16 - 14 - bw) if b['outgoing'] else 30
            filled = b['code'] == 'Y1'
            pygame.draw.rect(self._surface, BLACK if filled else WHITE, (bx, y0, bw, b['bubble_h']))
            pygame.draw.rect(self._surface, BLACK, (bx, y0, bw, b['bubble_h']), 2)
            if b['code'] == 'Y3':          # selection ring: 3px inside the border, inside the box
                pygame.draw.rect(self._surface, BLACK, (bx + 5, y0 + 5, bw - 10, b['bubble_h'] - 10), 2)
            for j, line in enumerate(b['lines']):
                self._text_bl(line, bx + 2 + pad_x, y0 + 10 + line_h * j + 25, 3, WHITE if filled else BLACK)
            tail_y = y0 + (b['bubble_h'] - tail_seg_h * len(tail_widths)) // 2
            for i, w in enumerate(tail_widths):
                tx = (bx + bw) if b['outgoing'] else (bx - w)
                pygame.draw.rect(self._surface, BLACK, (tx, tail_y + i * tail_seg_h, w, tail_seg_h))

            meta = {'Y0': 'SENDING...', 'Y1': 'SENT ' + b['time'], 'Y2': 'NOT SENT',
                    'Y3': 'NOT SENT - ENTER TO RETRY'}.get(b['code'], b['time'])
            baseline = y0 + b['bubble_h'] + 20
            bold = b['code'] in ('Y2', 'Y3')
            if b['outgoing']:
                self._text_right(meta, bx + bw, baseline, 2, bold=bold)
            else:
                self._text_bl(meta, bx, baseline, 2, bold=bold)
            y_bottom = top - gap
        self._surface.set_clip(None)

    def _draw_field_label(self, text, x, y, active):
        """A field label that inverts (fills ink, text flips to paper) while
        its field is active — the mode is marked at the field, not just by
        cursor position."""
        w = len(text) * self._char_w(2) + 4
        h = 8 * 2 + 4
        if active:
            pygame.draw.rect(self._surface, BLACK, (x - 2, y - 2, w, h))
        self._text(text, x, y, 2, WHITE if active else BLACK)

    def _draw_compose(self, data):
        # data = "to|msg|to_active|hdr|plus_sel|send_sel"  hdr: ''=typing 'X'=exit
        parts     = data.split('|')
        to_str    = parts[0] if len(parts) > 0 else ''
        msg_str   = parts[1] if len(parts) > 1 else ''
        to_active = parts[2] != '0' if len(parts) > 2 else True
        x_sel     = parts[3] == 'X' if len(parts) > 3 else False
        plus_sel  = parts[4] == '1' if len(parts) > 4 else False
        send_sel  = parts[5] == '1' if len(parts) > 5 else False

        box_w, box_h = 38, 34

        # Header
        self._text('NEW MESSAGE', 24, 10, 3, bold=True)
        self._text('NEW MESSAGE', 25, 10, 3, bold=True)
        x_box = (self.WIDTH - 16 - box_w, 8)
        if x_sel:
            pygame.draw.rect(self._surface, BLACK, (*x_box, box_w, box_h))
        self._text('X', x_box[0] + (box_w - self._char_w(3)) // 2, x_box[1] + (box_h - 24) // 2, 3,
                    WHITE if x_sel else BLACK, bold=True)
        self._line(44, weight=2)

        # TO: field — label inverts while active; empty+active shows a '+'
        # hint on the right that opens the contact picker
        self._draw_field_label('TO:', 24, 58, to_active)
        self._text(to_str, 24, 84, 3)
        if to_active and not to_str:
            plus_box = (self.WIDTH - 24 - box_w, 79)
            if plus_sel:
                pygame.draw.rect(self._surface, BLACK, (*plus_box, box_w, box_h))
            self._text('+', plus_box[0] + (box_w - self._char_w(3)) // 2, plus_box[1] + (box_h - 24) // 2,
                        3, WHITE if plus_sel else BLACK, bold=True)
        elif to_active and not plus_sel:
            cursor_x = 24 + len(to_str) * self._char_w(3)
            pygame.draw.rect(self._surface, BLACK, (cursor_x, 84, self._char_w(3), 24))
        self._line(122)

        # MESSAGE: field — label inverts while active
        self._draw_field_label('MESSAGE:', 24, 134, not to_active)
        msg_active = not to_active and not send_sel
        lines  = self._wrap_lines(msg_str, 3, self.WIDTH - 48) if msg_str else ['']
        msg_y  = 162
        line_h = 34
        for line in lines:
            self._text(line, 24, msg_y, 3)
            msg_y += line_h
        if msg_active:
            last_line = lines[-1] if lines else ''
            cursor_x  = 24 + len(last_line) * self._char_w(3)
            cursor_y  = msg_y - line_h
            pygame.draw.rect(self._surface, BLACK, (cursor_x, cursor_y, 12, 16))

        # SEND — bottom right; activates the same as Enter on a filled-out message
        send_str = 'SEND'
        send_w   = len(send_str) * self._char_w(2) + 36
        send_x   = self.WIDTH - 24 - send_w
        send_y   = self.HEIGHT - 14 - (16 + 12)
        if send_sel:
            pygame.draw.rect(self._surface, BLACK, (send_x, send_y, send_w, 16 + 12))
        pygame.draw.rect(self._surface, BLACK, (send_x, send_y, send_w, 16 + 12), 3)
        self._text(send_str, send_x + 18, send_y + 6, 2, WHITE if send_sel else BLACK, bold=True)

    def _draw_alert_icon_body(self, top, body):
        """Boxed '!' + prose paragraph — the alert pattern used by stub
        screens and the discard confirmation: what happened, why, what to
        do instead, all in one paragraph."""
        icon_size = 46
        icon_x    = 56
        pygame.draw.rect(self._surface, BLACK, (icon_x, top, icon_size, icon_size), 2)
        self._text('!', icon_x + (icon_size - self._char_w(3)) // 2,
                    top + (icon_size - 24) // 2, 3, bold=True)
        text_x  = icon_x + icon_size + 22
        max_px  = self.WIDTH - 56 - text_x
        lines   = self._wrap_lines(body, 3, max_px)
        ly = top
        for line in lines:
            self._text(line, text_x, ly, 3)
            ly += 34

    def _draw_stub(self, data):
        # data = "title|body"
        parts = data.split('|', 1)
        title = parts[0] if len(parts) > 0 else ''
        body  = parts[1] if len(parts) > 1 else ''
        self._text(title, 24, 10, 3, bold=True)
        self._line(44, weight=2)
        self._draw_alert_icon_body(212, body)

        label = 'OK'
        w, h  = len(label) * self._char_w(2) + 36, 16 + 12
        x, y  = self.WIDTH - 24 - w, self.HEIGHT - 24 - h
        pygame.draw.rect(self._surface, BLACK, (x, y, w, h), 3)
        self._text(label, x + 18, y + 6, 2, bold=True)

    def _draw_confirm_discard(self, data):
        discard_sel = data.strip() == 'D'
        self._text('NEW MESSAGE', 24, 10, 3, bold=True)
        self._line(44, weight=2)
        body = ("DISCARD THIS MESSAGE? IT HAS NOT BEEN SENT, AND THE PHONE KEEPS "
                "NO DRAFTS, SO THE TEXT CANNOT BE BROUGHT BACK.")
        self._draw_alert_icon_body(196, body)

        d_label, d_h = 'DISCARD', 16 + 14
        d_w = len(d_label) * self._char_w(2) + 36
        d_x, d_y = 56, self.HEIGHT - 24 - d_h
        if discard_sel:
            pygame.draw.rect(self._surface, BLACK, (d_x, d_y, d_w, d_h))
        pygame.draw.rect(self._surface, BLACK, (d_x, d_y, d_w, d_h), 2)
        self._text(d_label, d_x + 18, d_y + 7, 2, WHITE if discard_sel else BLACK)

        k_label, k_h = 'KEEP EDITING', 16 + 12
        k_w = len(k_label) * self._char_w(2) + 36
        k_x, k_y = self.WIDTH - 24 - k_w, self.HEIGHT - 24 - k_h
        if not discard_sel:
            pygame.draw.rect(self._surface, BLACK, (k_x, k_y, k_w, k_h))
        pygame.draw.rect(self._surface, BLACK, (k_x, k_y, k_w, k_h), 3)
        self._text(k_label, k_x + 18, k_y + 6, 2, WHITE if not discard_sel else BLACK, bold=True)

    def _draw_contacts_pick(self, data):
        # data = "sel|query|position|name·number|..."
        # sel: -1=back, -2=plus, 0..6=row within the 7-row window; position e.g. "3 / 14"
        parts = data.split('|')
        try:
            idx = int(parts[0])
        except (ValueError, IndexError):
            idx = 0
        query    = parts[1] if len(parts) > 1 else ''
        position = parts[2] if len(parts) > 2 else ''
        entries  = [e for e in parts[3:] if e]

        self._draw_header_bar('CONTACTS', idx == -1, idx == -2)

        row_h, bar_h = 64, 45
        for i, entry in enumerate(entries[:7]):
            y = 44 + i * row_h
            fields = entry.split('\xb7')
            name   = fields[0] if len(fields) > 0 else ''
            number = fields[1] if len(fields) > 1 else ''
            sel    = i == idx
            fg     = WHITE if sel else BLACK
            if sel:
                pygame.draw.rect(self._surface, BLACK, (0, y, self.WIDTH, row_h))
            self._text_bl(name, 28, y + 38, 3, fg, bold=True)
            self._text_right(number or 'NO NUMBER', self.WIDTH - 28, y + 36, 2, fg)
            self._line(y + row_h - 1)

        if not entries:
            if query:
                self._draw_empty_state('NO MATCH', 'NO NAME STARTS WITH THAT. PRESS BACKSPACE TO WIDEN THE SEARCH.',
                                       44, self.HEIGHT - bar_h - 2)
            else:
                self._draw_empty_state('NO CONTACTS', 'PRESS + TO SAVE THE FIRST ONE.', 44, self.HEIGHT - bar_h - 2)

        # Footer: a 2px rule riding the top of a 45px strip; LOOK UP left, position right.
        pygame.draw.rect(self._surface, BLACK, (0, self.HEIGHT - bar_h - 2, self.WIDTH, 2))
        label = 'LOOK UP:'
        lab_w = self._font(2).size(label)[0]
        self._text_bl(label, 28, 583, 2)
        qx = 28 + lab_w + 14
        self._text_bl(query, qx, 584, 3)
        cursor_x = qx + (self._font(3).size(query)[0] if query else 0)
        pygame.draw.rect(self._surface, BLACK, (cursor_x, 566, 18, 24))
        if position:
            self._text_right(position, self.WIDTH - 28, 583, 2)

    def _draw_contact(self, data):
        # data = "title|sub|kind|sel"
        # kind: S saved | N saved with no number | U number not in the address book
        # sel:  B back, E edit, C call, T text, V save, A add number
        parts = (data.split('|') + ['', '', 'S', 'C'])[:4]
        title, sub, kind, sel = parts
        box_w, box_h = 38, 34

        back_sel = sel == 'B'
        if back_sel:
            pygame.draw.rect(self._surface, BLACK, (16, 6, box_w, box_h))
        self._text('<', 16 + (box_w - self._char_w(3)) // 2, 6 + (box_h - 24) // 2, 3,
                    WHITE if back_sel else BLACK, bold=True)
        self._text_centered('CONTACT', 10, 3, bold=True)

        if kind in ('S', 'N'):                       # EDIT is only there for a saved contact
            edit_sel = sel == 'E'
            edit_w, edit_h = len('EDIT') * self._char_w(2) + 20, 34
            edit_x = self.WIDTH - 16 - edit_w
            if edit_sel:
                pygame.draw.rect(self._surface, BLACK, (edit_x, 6, edit_w, edit_h))
            self._text_bl('EDIT', edit_x + 10, 6 + edit_h // 2 + 6, 2, WHITE if edit_sel else BLACK, bold=True)
        self._line(43)

        self._text_bl(title, 28, 187, 6, bold=True)
        self._text_bl(sub, 28, 234, 3)
        pygame.draw.rect(self._surface, BLACK, (28, 330, self.WIDTH - 56, 2))

        actions = {'S': [('CALL', 'C'), ('TEXT', 'T')],
                   'N': [('ADD NUMBER', 'A')],
                   'U': [('CALL', 'C'), ('TEXT', 'T'), ('SAVE', 'V')]}.get(kind, [])
        x = 28
        for label, code in actions:
            w = len(label) * self._char_w(3) + 44 + 6          # 22px padding a side + 3px border a side
            picked = sel == code
            if picked:
                pygame.draw.rect(self._surface, BLACK, (x, 360, w, 46))
            pygame.draw.rect(self._surface, BLACK, (x, 360, w, 46), 3)
            self._text_bl(label, x + 25, 391, 3, WHITE if picked else BLACK, bold=True)
            x += w + 16

    def _draw_contact_edit(self, data):
        # data = "first|last|number|idx"  idx: -1=cancel, 0/1/2=field, 3=save
        parts = data.split('|')
        first  = parts[0] if len(parts) > 0 else ''
        last   = parts[1] if len(parts) > 1 else ''
        number = parts[2] if len(parts) > 2 else ''
        try:
            idx = int(parts[3]) if len(parts) > 3 else 0
        except ValueError:
            idx = 0
        box_w, box_h = 38, 34

        cancel_sel = idx == -1
        if cancel_sel:
            pygame.draw.rect(self._surface, BLACK, (16, 6, box_w, box_h))
        self._text('X', 16 + (box_w - self._char_w(3)) // 2, 6 + (box_h - 24) // 2, 3,
                    WHITE if cancel_sel else BLACK, bold=True)
        self._text_centered('EDIT CONTACT', 10, 3, bold=True)
        self._line(43)

        def _field(label, value, y_label, y_value, y_rule, active):
            self._draw_field_label(label, 24, y_label, active)
            self._text(value, 24, y_value, 3)
            if active:
                cx = 24 + len(value) * self._char_w(3)
                pygame.draw.rect(self._surface, BLACK, (cx, y_value, self._char_w(3), 24))
            self._line(y_rule)

        _field('FIRST NAME:', first, 70, 100, 138, idx == 0)
        _field('LAST NAME:', last, 158, 188, 226, idx == 1)
        _field('PHONE NUMBER:', number, 246, 276, 314, idx == 2)

        save_sel = idx == 3
        label = 'SAVE'
        w, h  = len(label) * self._char_w(2) + 36, 16 + 12
        x, y  = self.WIDTH - 24 - w, self.HEIGHT - 14 - h
        if save_sel:
            pygame.draw.rect(self._surface, BLACK, (x, y, w, h))
        pygame.draw.rect(self._surface, BLACK, (x, y, w, h), 3)
        self._text(label, x + 18, y + 6, 2, WHITE if save_sel else BLACK, bold=True)

    def _draw_calls(self, data):
        # data = "sel|name·tag·time·duration|..."
        # sel: -1=back, 0..5=row within the 6-row window (DIAL A NUMBER is row 0 of the list)
        parts = data.split('|')
        try:
            idx = int(parts[0])
        except (ValueError, IndexError):
            idx = 0
        entries = [e for e in parts[1:] if e]

        self._draw_header_bar_back_only('CALL', idx == -1)

        row_h = 92
        for i, entry in enumerate(entries[:6]):
            y = 44 + i * row_h
            name, tag, t, dur = (entry.split('\xb7') + ['', '', '', ''])[:4]
            sel = i == idx
            fg  = WHITE if sel else BLACK
            if sel:
                pygame.draw.rect(self._surface, BLACK, (0, y, self.WIDTH, row_h))
            self._text_bl(name, 28, y + 53, 3, fg, bold=True)
            if tag:
                tag_x = 28 + self._font(3, True).size(name)[0] + 12
                tag_w = self._font(2).size(tag)[0] + 12
                pygame.draw.rect(self._surface, fg, (tag_x, y + 35, tag_w, 22), 1)
                self._text_bl(tag, tag_x + 6, y + 51, 2, fg)
            if t:
                self._text_right(t, self.WIDTH - 28, y + 40, 2, fg)
            if dur:
                self._text_right(dur, self.WIDTH - 28, y + 62, 2, fg)
            self._line(y + row_h - 1)

    def _draw_header_bar_back_only(self, title, back_active, height=44):
        """Header with only a back control — used by CALL, whose right side
        carries no additive action."""
        self._line(height - 1)
        box_w, box_h = 38, 34
        if back_active:
            pygame.draw.rect(self._surface, BLACK, (16, 6, box_w, box_h))
        self._text('<', 16 + (box_w - self._char_w(3)) // 2, 6 + (box_h - 24) // 2, 3,
                    WHITE if back_active else BLACK, bold=True)
        title_w = len(title) * self._char_w(3)
        self._text(title, (self.WIDTH - title_w) // 2, 10, 3, bold=True)

    def _draw_dial(self, data):
        # data = "buffer|quick_idx|name|..."
        parts = data.split('|')
        buf  = parts[0] if len(parts) > 0 else ''
        try:
            qidx = int(parts[1]) if len(parts) > 1 else -1
        except ValueError:
            qidx = -1
        names = parts[2:] if len(parts) > 2 else []

        self._text_centered('DIAL', 40, 2)
        buf_w, cursor_w = len(buf) * self._char_w(6), 28
        x = (self.WIDTH - buf_w - cursor_w) // 2
        self._text(buf, x, 70, 6, bold=True)
        pygame.draw.rect(self._surface, BLACK, (x + buf_w, 70, cursor_w, 48))
        self._line(170, weight=2)
        self._text('RECENT', 28, 186, 2)

        row_h = 56
        y = 210
        for i, name in enumerate(names):
            sel = i == qidx
            fg  = WHITE if sel else BLACK
            if sel:
                pygame.draw.rect(self._surface, BLACK, (28, y, self.WIDTH - 56, row_h))
            self._text(name, 32, y + (row_h - 24) // 2, 3, fg, bold=True)
            pygame.draw.line(self._surface, BLACK, (28, y + row_h - 1), (self.WIDTH - 28, y + row_h - 1), 1)
            y += row_h

    def _draw_call_state(self, data):
        # data = "OUT|IN|ACTIVE|name|timer"
        parts = data.split('|')
        call_state = parts[0] if len(parts) > 0 else 'OUT'
        name  = parts[1] if len(parts) > 1 else ''
        timer = parts[2] if len(parts) > 2 else '00:00'

        if call_state == 'IN':
            self._surface.fill(BLACK)
            fg, label, hint = WHITE, 'INCOMING CALL', 'ENTER ACCEPT \xb7 ESC DECLINE'
        elif call_state == 'ACTIVE':
            fg, label, hint = BLACK, 'IN CALL', 'ESC HANG UP'
        else:
            fg, label, hint = BLACK, 'CALLING…', 'ESC HANG UP'

        self._text_centered(label, 220, 2, color=fg)
        self._text_centered(name, 280, 6, color=fg, bold=True)
        if call_state == 'ACTIVE':
            self._text_centered(timer, 360, 3, color=fg)
        self._text_centered(hint, self.HEIGHT - 34 - 16, 2, color=fg)

    # ── OS 0.0 Legacy Renderers (kept for kyphone_app.py) ────────────

    def _draw_home(self, data):
        parts = data.split('|')
        time_str = parts[0] if len(parts) > 0 else ''
        date_str = parts[1] if len(parts) > 1 else ''
        try:
            unread = int(parts[2]) if len(parts) > 2 else 0
        except ValueError:
            unread = 0
        try:
            home_sel = int(parts[3]) if len(parts) > 3 else -1
        except ValueError:
            home_sel = -1

        cat = [
            r"   )\._.,--....,'``.",
            r"  /,   _.. \   _\  (`._ ,.",
            r" `._.-(,_..'--(,_..'`-.;.'",
        ]
        cat_font = pygame.font.SysFont('courier', 13, bold=True)
        lh       = 13
        max_w    = max(cat_font.size(l)[0] for l in cat)
        cat_x    = self.WIDTH - max_w - 6
        for i, line in enumerate(cat):
            img = cat_font.render(line, True, BLACK)
            self._surface.blit(img, (cat_x, 6 + i * lh))

        total_h = 64 + 24 + 24
        start_y = 35 + (475 - total_h) // 2

        self._text_centered(time_str, start_y, 8, clock=True)
        self._text_centered(date_str, start_y + 64 + 24, 3)

        buttons      = ['Texts', 'Calls', 'Books', 'Music']
        btn_positions = [0, 150, 300, 450]
        btn_w, btn_h, btn_y = 150, 65, 535
        for i, (label, bx) in enumerate(zip(buttons, btn_positions)):
            cw       = len(label) * self._char_w(2)
            lx       = bx + (btn_w - cw) // 2
            ly       = btn_y + (btn_h - 16) // 2
            selected = i == home_sel and home_sel >= 0
            if selected:
                pygame.draw.rect(self._surface, BLACK, (bx, btn_y, btn_w, btn_h))
                self._text(label, lx, ly, 2, WHITE)
                self._text(label, lx + 1, ly, 2, WHITE)
            else:
                pygame.draw.rect(self._surface, BLACK, (bx, btn_y, btn_w, btn_h), 2)
                self._text(label, lx, ly, 2, BLACK)
                self._text(label, lx + 1, ly, 2, BLACK)

            if i == 0 and unread > 0:
                badge_size = 24
                pygame.draw.rect(self._surface, WHITE if selected else BLACK,
                                 (bx + 2, btn_y + 2, badge_size, badge_size))
                badge_label = str(min(unread, 9))
                bx2 = bx + 2 + (badge_size - self._char_w(2)) // 2
                by2 = btn_y + 2 + (badge_size - 16) // 2
                self._text(badge_label, bx2, by2, 2, BLACK if selected else WHITE)

    def _draw_msg_list(self, data):
        parts = data.split('|')
        try:
            sel     = int(parts[0])
            entries = parts[1:]
        except (ValueError, IndexError):
            sel     = 0
            entries = parts

        header_h = 44
        self._line(header_h - 1)

        def _header_btn(char, x, active=False):
            cw = self._char_w(3)
            ch = 3 * 8
            if active:
                pygame.draw.rect(self._surface, BLACK, (x - 4, 6, cw + 8, ch + 8))
                self._text(char, x, 10, 3, WHITE)
            else:
                self._text(char, x, 10, 3, BLACK)

        _header_btn('<', 16, active=(sel == -1))
        title_w = len('TEXTS') * self._char_w(3)
        self._text('TEXTS', (self.WIDTH - title_w) // 2, 10, 3)
        _header_btn('+', self.WIDTH - 16 - self._char_w(3), active=(sel == -2))

        row_h  = 72
        margin = 16
        y      = header_h
        for i, entry in enumerate(entries):
            fields  = entry.split('\xb7')
            name    = fields[0] if len(fields) > 0 else ''
            preview = fields[1] if len(fields) > 1 else ''
            ts      = fields[2] if len(fields) > 2 else ''

            fg = WHITE if i == sel else BLACK
            if i == sel:
                pygame.draw.rect(self._surface, BLACK, (0, y, self.WIDTH, row_h))

            self._text(name, margin, y + 8, 3, fg, bold=True)

            chevron_w = self._char_w(2)
            chevron_x = self.WIDTH - margin - chevron_w
            chevron_y = y + (row_h - 16) // 2
            self._text('>', chevron_x, chevron_y, 2, fg)

            if ts:
                ts_w = len(ts) * self._char_w(2)
                self._text(ts, chevron_x - ts_w - 8, y + 16, 2, fg)

            self._text(preview, margin, y + 40, 2, fg)
            self._line(y + row_h - 1)
            y += row_h

    def _draw_msg_thread(self, data):
        parts = data.split('|')
        name  = parts[0] if parts else ''
        msgs  = parts[1:] if len(parts) > 1 else []

        self._text('<', 16, 10, 3)
        self._text_centered(name, 10, 3, bold=True)
        self._text('i', self.WIDTH - 16 - self._char_w(3), 10, 3)
        self._line(46)

        y        = 56
        ts       = 3
        line_h   = 32
        margin   = 16
        max_w    = self.WIDTH - margin
        last_time = None

        for msg in msgs:
            if len(msg) >= 2 and msg[1] == ':':
                align = msg[0]
                rest  = msg[2:]
            else:
                align, rest = 'R', msg

            if '~' in rest:
                time_str, body = rest.split('~', 1)
            else:
                time_str, body = '', rest

            if time_str and time_str != last_time:
                last_time = time_str
                self._text_centered(time_str, y, 1)
                y += ts * 8 + 10

            sender_label = 'Me' if align == 'Y' else name
            prefix       = f"{sender_label}:"
            font_bold    = _get_font(ts * 8, bold=True)
            prefix_w     = font_bold.size(prefix + ' ')[0]
            wrap_w       = max_w - prefix_w

            lines = self._wrap_lines(body, ts, wrap_w)
            for i, line in enumerate(lines):
                if i == 0:
                    self._surface.blit(font_bold.render(prefix, True, BLACK), (margin, y))
                    self._text(line, margin + prefix_w, y, ts)
                else:
                    self._text(line, margin + prefix_w, y, ts)
                y += line_h
            y += 4

    def _draw_sms(self, text):
        if '|' in text:
            sender, body = text.split('|', 1)
        else:
            sender, body = text, ''
        self._text(sender, 10, 10, 3)
        self._text(body,   10, 60, 4)
