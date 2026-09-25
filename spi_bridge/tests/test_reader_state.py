"""
test_reader_state.py — the library and reader screens of the OS state machine (kyphone_os.py).

Drives the real handle_key with a temporary books folder; hardware and the emulator are mocked and every
screen/page the OS pushes is captured. Never touches the real data/ folder.

    python3 -m pytest spi_bridge/tests/test_reader_state.py -v
"""

import json
import os
import random
import re
import sys
import tempfile
import threading
import unittest
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
import reader_epub  # noqa: E402
import reader_layout as rl  # noqa: E402
from epub_fixtures import make_epub, xhtml  # noqa: E402
for _name in _faked:
    if isinstance(sys.modules.get(_name), MagicMock):
        del sys.modules[_name]

SEP = '\xb7'


def prose(seed, n):
    rng = random.Random(seed)
    words = ['river', 'lantern', 'quietly', 'the', 'and', 'morning', 'walked', 'slowly', 'toward', 'harbour', 'a',
             'small', 'boat', 'she', 'said', 'nothing', 'while', 'gulls', 'circled', 'above', 'well-known', 'town']
    return ' '.join(rng.choice(words) for _ in range(n)).capitalize() + '.'


def chapter_html(k, paras=12):
    return xhtml('<h1>Chapter %d</h1>' % (k + 1) + ''.join('<p>%s</p>' % prose(k * 100 + i, 90) for i in range(paras)))


def three_chapters():
    return [('c%d.xhtml' % k, chapter_html(k, paras)) for k, paras in enumerate((14, 30, 8))]


class ReaderCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = os.path.realpath(self._tmp.name)
        self.books = os.path.join(self.tmp, 'books')
        os.makedirs(self.books)
        self.reading = os.path.join(self.tmp, 'reading.json')
        for name, value in (('BOOKS_DIR', self.books), ('READING_FILE', self.reading), ('DATA_DIR', self.tmp)):
            p = patch.object(kyphone_os, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.screens, self.pages = [], []
        for name, fn in (('push_screen', self.screens.append), ('push_page', lambda frames: self.pages.append(list(frames)))):
            p = patch.object(kyphone_os, name, side_effect=fn)
            p.start()
            self.addCleanup(p.stop)
        self.reset()
        self.addCleanup(self.close_book)

    def reset(self):
        kyphone_os.state.update(
            screen='home', home_index=kyphone_os.HOME_MENU.index('READ'), book=None, library_books=[], library_index=0, library_start=0, r_id='',
            r_chapter=0, r_offset=0, r_size='M', r_pages=None, r_pages_key=None, r_turns=0,
            stub_key='', stub_text=None, stub_return='home')

    def close_book(self):
        book = kyphone_os.state['book']
        if book is not None:
            book.close()
            kyphone_os.state['book'] = None

    # helpers
    def add_book(self, name='alpha.epub', chapters=None, **kw):
        path = os.path.join(self.books, name)
        make_epub(path, chapters or three_chapters(), **kw)
        return path

    def key(self, *keys):
        for k in keys:
            kyphone_os.handle_key(k)

    def open_library(self):
        self.reset()
        self.key('KEY_ENTER')                            # READ is on the home menu's third row
        self.assertEqual(kyphone_os.state['screen'], 'library')

    def open_book(self, index=0):
        self.open_library()
        self.key(*['KEY_DOWN'] * index)
        self.key('KEY_ENTER')
        self.assertEqual(kyphone_os.state['screen'], 'reader')

    @property
    def st(self):
        return kyphone_os.state

    def frames_ok(self, frames):
        self.assertIn(frames[-1].split('|')[0], ('RFOOT',))
        for f in frames:
            self.assertLessEqual(len(f), kyphone_os.MAX_COMMAND_CHARS)
            self.assertTrue(all(' ' <= c <= '~' or c == SEP for c in f), f)

    def refresh_of(self, frames):
        return frames[-1].split('|')[1]

    def saved(self):
        with open(self.reading) as f:
            return json.load(f)

    def book_pages(self, size='M', chapter=0):
        with reader_epub.load(os.path.join(self.books, 'alpha.epub')) as book:
            return rl.paginate(book.chapter(chapter).paras, size)


class Library(ReaderCase):
    def test_read_opens_the_library_and_lists_books_by_title(self):
        self.add_book('zeta.epub', title='Zeta', author='Z. Writer')
        self.add_book('alpha.epub', title='Alpha', author='A. Writer')
        self.open_library()
        self.assertEqual(self.screens[-1], 'LIBRARY|0|Alpha%sA. Writer%s|Zeta%sZ. Writer%s' % (SEP, SEP, SEP, SEP))

    def test_an_empty_library_shows_only_the_header(self):
        self.open_library()
        self.assertEqual(self.screens[-1], 'LIBRARY|-1')
        self.key('KEY_DOWN')
        self.assertEqual(self.screens[-1], 'LIBRARY|-1')
        self.key('KEY_ENTER')
        self.assertEqual(self.st['screen'], 'home')

    def test_a_missing_books_folder_is_an_empty_library(self):
        with patch.object(kyphone_os, 'BOOKS_DIR', os.path.join(self.tmp, 'nope')):
            self.open_library()
        self.assertEqual(self.screens[-1], 'LIBRARY|-1')

    def test_only_epub_files_are_listed(self):
        self.add_book('a.epub', title='Only')
        open(os.path.join(self.books, 'notes.txt'), 'w').write('x')
        open(os.path.join(self.books, '.hidden.epub'), 'wb').write(b'x')
        self.open_library()
        self.assertEqual(len(self.st['library_books']), 1)

    def test_up_and_down_move_and_the_header_is_reachable(self):
        for n in 'abc':
            self.add_book(n + '.epub', title='Book ' + n)
        self.open_library()
        self.key('KEY_DOWN', 'KEY_DOWN', 'KEY_DOWN')
        self.assertEqual(self.st['library_index'], 2)                 # stops at the last book
        self.key('KEY_UP', 'KEY_UP', 'KEY_UP', 'KEY_UP')
        self.assertEqual(self.st['library_index'], -1)
        self.assertTrue(self.screens[-1].startswith('LIBRARY|-1|'))
        self.key('KEY_ENTER')
        self.assertEqual(self.st['screen'], 'home')

    def test_esc_goes_home(self):
        self.add_book()
        self.open_library()
        self.key('KEY_ESC')
        self.assertEqual(self.st['screen'], 'home')

    def test_the_list_is_windowed_to_five_rows_with_a_window_relative_selection(self):
        for n in range(8):
            self.add_book('b%d.epub' % n, title='Book %d' % n)
        self.open_library()
        self.assertEqual(len(self.screens[-1].split('|')[2:]), 5)                    # five rows
        self.key(*['KEY_DOWN'] * 6)
        wire = self.screens[-1]
        self.assertEqual(wire.split('|')[1], '4')                                     # the selection sits on the last visible row
        self.assertEqual(wire.split('|')[2].split(SEP)[0], 'Book 2')                  # the window scrolled one row at a time
        self.assertLessEqual(len(wire), kyphone_os.MAX_COMMAND_CHARS)

    def test_long_titles_and_authors_are_cut_and_the_frame_still_fits(self):
        for n in range(6):
            self.add_book('b%d.epub' % n, title='T%d ' % n + 'long title ' * 8, author='Author ' * 8)
        self.open_library()
        wire = self.screens[-1]
        self.assertLessEqual(len(wire), kyphone_os.MAX_COMMAND_CHARS)
        for row in wire.split('|')[2:]:
            title, author, pct = row.split(SEP)
            self.assertLessEqual(len(title), kyphone_os.LIBRARY_TITLE_MAX)
            self.assertLessEqual(len(author), kyphone_os.LIBRARY_AUTHOR_MAX)

    def test_an_unreadable_file_is_listed_and_says_why_when_opened(self):
        open(os.path.join(self.books, 'broken.epub'), 'wb').write(b'this is not a zip file')
        self.open_library()
        self.assertTrue(self.screens[-1].startswith('LIBRARY|0|broken'))
        self.key('KEY_ENTER')
        self.assertEqual(self.st['screen'], 'stub')
        self.assertIn('NOT A VALID EPUB FILE', self.st['stub_text'][1])
        self.key('KEY_ENTER')
        self.assertEqual(self.st['screen'], 'library')

    def test_a_book_of_nothing_but_pictures_is_refused_with_a_reason(self):
        self.add_book('pics.epub', chapters=[('a.xhtml', xhtml('<img src="x.png"/>'))])
        self.open_library()
        self.key('KEY_ENTER')
        self.assertEqual(self.st['screen'], 'stub')
        self.assertIn('NO READABLE TEXT', self.st['stub_text'][1])

    def test_the_wire_and_reader_alerts_all_fit_one_frame_and_are_drawable(self):
        for key in ('BAD_BOOK', 'END_OF_BOOK', 'START_OF_BOOK', 'BIGGEST_FONT', 'SMALLEST_FONT'):
            title, body = kyphone_os.ALERTS[key]
            wire = 'STUB|%s|%s' % (title, body.format(reason='NOT A VALID EPUB FILE'))
            self.assertLessEqual(len(wire), kyphone_os.MAX_COMMAND_CHARS, key)
            self.assertEqual(title, kyphone_os.sanitize(title), key)                 # (the wire's own '|' is not drawn text)
            self.assertEqual(body, kyphone_os.sanitize(body), key)


class Opening(ReaderCase):
    def test_opening_a_book_draws_its_first_page_with_a_full_refresh(self):
        self.add_book()
        self.open_book()
        frames = self.pages[-1]
        self.frames_ok(frames)
        self.assertTrue(frames[0].startswith('RTEXT|M|0|S|'))
        self.assertEqual(self.refresh_of(frames), 'F')
        left, right = frames[-1].split('|')[2:]
        self.assertEqual(left, 'Chapter 1')
        self.assertRegex(right, r'^1/\d+  0%$')
        self.assertEqual((self.st['r_chapter'], self.st['r_offset'], self.st['r_size']), (0, 0, 'M'))

    def test_a_cover_page_without_text_is_skipped(self):
        cover = ('cover.xhtml', xhtml('<img src="c.png"/>'))
        self.add_book(chapters=[cover] + three_chapters())
        self.open_book()
        self.assertEqual(self.st['r_chapter'], 1)
        self.assertEqual(self.pages[-1][-1].split('|')[2], 'Chapter 1')

    def test_the_toc_title_is_the_footer_text(self):
        self.add_book(toc={'c0.xhtml': 'I. The Harbour', 'c1.xhtml': 'II. Night', 'c2.xhtml': 'III. Day'})
        self.open_book()
        self.assertEqual(self.pages[-1][-1].split('|')[2], 'I. The Harbour')

    def test_a_long_chapter_title_is_cut_to_fit_the_footer(self):
        self.add_book(toc={'c0.xhtml': 'CHAPTER THE FIRST IN WHICH A VERY LONG TITLE GOES ON AND ON', 'c1.xhtml': 'b', 'c2.xhtml': 'c'})
        self.open_book()
        left, right = self.pages[-1][-1].split('|')[2:]
        self.assertLessEqual(len(left) + len(right) + 2, rl.FOOT_COLS)
        self.assertTrue(left.endswith('...'))


import kyphone_os as _os_for_default                                        # noqa: E402  (the shipped setting, before any test pins it)
SHIPPED_FULL_EVERY = _os_for_default.READER_FULL_EVERY


class PageTurns(ReaderCase):
    def setUp(self):
        super().setUp()
        p = patch.object(kyphone_os, 'READER_FULL_EVERY', 8)                       # these tests are about the partial path
        p.start()
        self.addCleanup(p.stop)
        self.add_book()
        self.open_book()

    def test_right_down_enter_and_d_turn_forward_and_left_up_a_back(self):
        pages = self.book_pages()
        self.key('KEY_RIGHT')
        self.assertEqual(self.st['r_offset'], pages[1].start)
        self.key('KEY_DOWN')
        self.assertEqual(self.st['r_offset'], pages[2].start)
        self.key('KEY_ENTER')
        self.assertEqual(self.st['r_offset'], pages[3].start)
        self.key('CHAR:d')                                                       # WASD map to the arrows
        self.assertEqual(self.st['r_offset'], pages[4].start)
        self.key('KEY_LEFT')
        self.assertEqual(self.st['r_offset'], pages[3].start)
        self.key('KEY_UP', 'CHAR:a')
        self.assertEqual(self.st['r_offset'], pages[1].start)

    def test_a_turn_draws_the_new_page_with_a_partial_refresh_and_a_growing_percentage(self):
        self.key('KEY_RIGHT')
        frames = self.pages[-1]
        self.frames_ok(frames)
        self.assertEqual(self.refresh_of(frames), 'P')
        self.assertTrue(frames[0].startswith('RTEXT|M|0|S|'))
        first = int(re.search(r'(\d+)%', self.pages[-2][-1]).group(1))
        second = int(re.search(r'(\d+)%', frames[-1]).group(1))
        self.assertGreaterEqual(second, first)
        self.assertIn('2/', frames[-1].split('|')[3])

    def test_every_page_turn_is_a_full_refresh(self):
        # Kyle's call on the phone: a book page always flashes, so no ghost of the last page is ever left
        self.assertEqual(SHIPPED_FULL_EVERY, 0)
        with patch.object(kyphone_os, 'READER_FULL_EVERY', SHIPPED_FULL_EVERY):
            seen = [self.refresh_of(self.pages[-1])]
            for _ in range(4):
                self.key('KEY_RIGHT')
                seen.append(self.refresh_of(self.pages[-1]))
        self.assertEqual(seen, ['F'] * 5)

    def test_a_partial_refresh_cadence_still_works_when_the_setting_allows_it(self):
        with patch.object(kyphone_os, 'READER_FULL_EVERY', 8):
            seen = [self.refresh_of(self.pages[-1])]
            for _ in range(10):
                self.key('KEY_RIGHT')
                seen.append(self.refresh_of(self.pages[-1]))
        self.assertEqual(seen, ['F'] + ['P'] * 8 + ['F', 'P'])

    def test_the_position_is_saved_after_every_page(self):
        pages = self.book_pages()
        self.key('KEY_RIGHT', 'KEY_RIGHT')
        rec = self.saved()['books'][self.st['r_id']]
        self.assertEqual((rec['chapter'], rec['offset']), (0, pages[2].start))
        self.assertGreater(rec['pct'], 0)

    def test_crossing_into_the_next_chapter_starts_it_at_the_top_with_a_full_refresh(self):
        n = len(self.book_pages())
        self.key(*['KEY_RIGHT'] * (n - 1))
        self.assertEqual(self.st['r_chapter'], 0)
        self.assertEqual(self.refresh_of(self.pages[-1]), 'P')
        self.key('KEY_RIGHT')
        self.assertEqual((self.st['r_chapter'], self.st['r_offset']), (1, 0))
        self.assertEqual(self.refresh_of(self.pages[-1]), 'F')
        self.assertEqual(self.pages[-1][-1].split('|')[2], 'Chapter 2')

    def test_going_back_across_a_chapter_lands_on_the_last_page_of_the_previous_one(self):
        n = len(self.book_pages())
        self.key(*['KEY_RIGHT'] * n)                                             # into chapter 2, page 1
        self.key('KEY_LEFT')
        self.assertEqual(self.st['r_chapter'], 0)
        self.assertEqual(self.st['r_offset'], self.book_pages()[-1].start)
        self.assertEqual(self.refresh_of(self.pages[-1]), 'F')

    def test_the_first_page_of_the_book_cannot_be_turned_back_and_says_so(self):
        count = len(self.pages)
        self.key('KEY_LEFT')
        self.assertEqual(self.st['screen'], 'stub')
        self.assertIn('FIRST PAGE', self.st['stub_text'][1])
        self.assertEqual(len(self.pages), count)
        self.key('KEY_ENTER')                                                    # back to the page, redrawn in full
        self.assertEqual(self.st['screen'], 'reader')
        self.assertEqual(self.refresh_of(self.pages[-1]), 'F')

    def test_the_last_page_of_the_book_says_so_and_shows_100_percent(self):
        self.st['r_chapter'], self.st['r_offset'] = 2, 10 ** 9                     # somewhere at the end of the last chapter
        self.key('KEY_RIGHT')
        self.assertEqual(self.st['screen'], 'stub')
        self.assertIn('LAST PAGE', self.st['stub_text'][1])
        self.key('KEY_ESC')                                                      # Esc dismisses it too
        self.assertEqual(self.st['screen'], 'reader')
        self.assertTrue(self.pages[-1][-1].endswith('100%'))

    def test_other_keys_are_ignored(self):
        count = len(self.pages)
        self.key('CHAR:x', 'KEY_TAB', 'CHAR:7')
        self.assertEqual(len(self.pages), count)
        self.assertEqual(self.st['screen'], 'reader')

    def test_space_turns_the_page(self):
        pages = self.book_pages()
        self.key('CHAR: ')
        self.assertEqual(self.st['r_offset'], pages[1].start)


class FontSize(ReaderCase):
    def setUp(self):
        super().setUp()
        self.add_book()
        self.open_book()

    def test_plus_and_minus_step_through_the_four_sizes(self):
        seen = []
        for k in ('CHAR:+', 'CHAR:=', 'CHAR:-', 'CHAR:-', 'CHAR:_'):
            self.key(k)
            seen.append(self.st['r_size'])
        self.assertEqual(seen, ['L', 'X', 'L', 'M', 'S'])

    def test_a_size_change_redraws_in_the_new_size_with_a_full_refresh(self):
        self.key('CHAR:+')
        frames = self.pages[-1]
        self.frames_ok(frames)
        self.assertTrue(frames[0].startswith('RTEXT|L|0|S|'))
        self.assertEqual(self.refresh_of(frames), 'F')

    def test_the_ends_of_the_range_raise_an_alert_and_change_nothing(self):
        self.key('CHAR:+', 'CHAR:+')                                             # X
        self.key('CHAR:+')
        self.assertEqual((self.st['screen'], self.st['r_size']), ('stub', 'X'))
        self.assertIn('LARGEST', self.st['stub_text'][1])
        self.key('KEY_ENTER')
        self.key('CHAR:-', 'CHAR:-', 'CHAR:-', 'CHAR:-')                         # X L M S, then one too far
        self.assertEqual((self.st['screen'], self.st['r_size']), ('stub', 'S'))
        self.assertIn('SMALLEST', self.st['stub_text'][1])

    def test_the_size_is_remembered_across_books_and_restarts(self):
        self.key('CHAR:+')
        self.assertEqual(self.saved()['font'], 'L')
        self.key('KEY_ESC')
        self.open_book()
        self.assertEqual(self.st['r_size'], 'L')
        self.assertTrue(self.pages[-1][0].startswith('RTEXT|L|'))

    def test_the_place_survives_size_changes_without_drifting(self):
        self.key('KEY_RIGHT', 'KEY_RIGHT', 'KEY_RIGHT')
        offset = self.st['r_offset']
        for k in ('CHAR:+', 'CHAR:+', 'CHAR:-', 'CHAR:-', 'CHAR:-', 'CHAR:+'):
            self.key(k)
            self.assertEqual(self.st['r_offset'], offset)                        # the remembered position never moves
            pages, i = self.st['r_pages'], rl.page_index(self.st['r_pages'], offset)
            self.assertLessEqual(pages[i].start, offset)
            self.assertTrue(i + 1 == len(pages) or offset < pages[i + 1].start)
            with reader_epub.load(os.path.join(self.books, 'alpha.epub')) as book:
                want = rl.page_frames(self.st['r_size'], pages[i].lines, book.chapter(0).title, self.pages[-1][-1].split('|')[3], 'F')
            self.assertEqual(self.pages[-1], want)


class Resuming(ReaderCase):
    def test_leaving_returns_to_the_library_on_that_book_and_reopening_resumes_the_page(self):
        self.add_book('alpha.epub', title='Alpha')
        self.add_book('beta.epub', title='Beta', chapters=three_chapters())
        self.open_book(1)                                                          # Beta
        self.key('KEY_RIGHT', 'KEY_RIGHT', 'KEY_RIGHT')
        page = self.pages[-1]
        self.key('KEY_ESC')
        self.assertEqual((self.st['screen'], self.st['library_index'], self.st['book']), ('library', 1, None))
        self.assertRegex(self.screens[-1], r'Beta.*\d+%')                          # progress shows in the list
        self.key('KEY_ENTER')
        self.assertEqual(self.pages[-1][:-1], page[:-1])                           # the same lines
        self.assertEqual(self.refresh_of(self.pages[-1]), 'F')                     # redrawn in full

    def test_esc_from_the_reader_closes_the_book(self):
        self.add_book()
        self.open_book()
        book = self.st['book']
        self.key('KEY_ESC')
        self.assertIsNone(self.st['book'])
        self.assertIsNone(book._pkg.zip.fp)                                        # its file is closed

    def test_q_leaves_the_reader_too(self):
        self.add_book()
        self.open_book()
        self.key('CHAR:q')
        self.assertEqual(self.st['screen'], 'library')

    def test_a_damaged_reading_file_starts_from_the_top_at_the_default_size(self):
        self.add_book()
        open(self.reading, 'w').write('{{{ not json')
        self.open_book()
        self.assertEqual((self.st['r_chapter'], self.st['r_offset'], self.st['r_size']), (0, 0, 'M'))

    def test_a_saved_place_that_no_longer_exists_is_ignored(self):
        path = self.add_book()
        bid = kyphone_os._book_id(path)
        for rec in ({'chapter': 99, 'offset': 5}, {'chapter': -1, 'offset': 0}, {'chapter': 'x', 'offset': 0},
                    {'chapter': 1, 'offset': -4}, 'garbage', None):
            json.dump({'font': 'Q', 'books': {bid: rec}}, open(self.reading, 'w'))
            self.reset()
            self.open_book()
            self.assertEqual((self.st['r_chapter'], self.st['r_offset'], self.st['r_size']), (0, 0, 'M'), rec)
            self.close_book()

    def test_the_place_is_kept_per_book(self):
        self.add_book('a.epub', title='A book')
        self.add_book('b.epub', title='B book')
        self.open_book(0)
        self.key('KEY_RIGHT', 'KEY_RIGHT')
        first = self.st['r_offset']
        self.key('KEY_ESC')
        self.open_book(1)
        self.assertEqual(self.st['r_offset'], 0)
        self.key('KEY_ESC')
        self.open_book(0)
        self.assertEqual(self.st['r_offset'], first)


class BrokenChapters(ReaderCase):
    def test_a_chapter_that_cannot_be_read_closes_the_book_with_an_alert(self):
        chapters = three_chapters()
        chapters[1] = ('c1.xhtml', xhtml('<p>DAMAGEME %s</p>' % ('text ' * 300)))
        path = self.add_book(chapters=chapters)
        raw = bytearray(open(path, 'rb').read())
        raw[raw.find(b'DAMAGEME')] ^= 0xFF                                          # the stored bytes now fail their checksum
        open(path, 'wb').write(bytes(raw))
        self.open_book()
        n = len(self.book_pages())
        self.key(*['KEY_RIGHT'] * n)
        self.assertEqual((self.st['screen'], self.st['stub_return']), ('stub', 'library'))
        self.assertIn('COULD NOT READ', self.st['stub_text'][1])
        self.assertIsNone(self.st['book'])
        self.key('KEY_ENTER')
        self.assertEqual(self.st['screen'], 'library')


class Sending(unittest.TestCase):
    """A page is a list of frames sent back to back; a newer command abandons the rest."""

    real_wait_for_taken = staticmethod(kyphone_os.wait_for_taken)        # the real one, before setUp replaces it

    def setUp(self):
        self.spi = MagicMock()
        self.event = threading.Event()
        self.taken = MagicMock(return_value=True)
        for name, value in (('spi', self.spi), ('_pending_event', self.event), ('wait_for_taken', self.taken)):
            p = patch.object(kyphone_os, name, value, create=True)
            p.start()
            self.addCleanup(p.stop)

    def payload_text(self, call):
        return bytes(call.args[0][3:]).rstrip(b'\x00').decode('latin-1')

    def test_a_page_goes_out_frame_by_frame_in_order(self):
        with patch.object(kyphone_os, 'wait_for_ready', return_value=True):
            kyphone_os._send_command(['one', 'two', 'three'])
        self.assertEqual([self.payload_text(c) for c in self.spi.xfer2.call_args_list], ['one', 'two', 'three'])

    def test_each_frame_waits_for_the_inkplate_to_take_it_before_the_next_goes(self):
        # the firmware ends a frame after 600 ms of clock silence, and the ready line is still high until then:
        # a frame sent straight after another merges into it and is lost (found on the real phone)
        order = []
        self.spi.xfer2.side_effect = lambda payload: order.append('send ' + self.payload_text(MagicMock(args=(payload,))))
        self.taken.side_effect = lambda *a: order.append('taken') or True
        with patch.object(kyphone_os, 'wait_for_ready', side_effect=lambda *a: order.append('ready') or True):
            kyphone_os._send_command(['one', 'two'])
        self.assertEqual(order, ['ready', 'send one', 'taken', 'ready', 'send two', 'taken'])

    def test_wait_for_taken_returns_when_the_ready_line_drops_and_gives_up_if_it_never_does(self):
        line = MagicMock()
        line.get_value.side_effect = [1, 1, 1, 0]
        with patch.object(kyphone_os, 'SIM_MODE', False), patch.object(kyphone_os, 'handshake', line, create=True):
            self.assertTrue(self.real_wait_for_taken(timeout_s=2))
        stuck = MagicMock()
        stuck.get_value.return_value = 1
        with patch.object(kyphone_os, 'SIM_MODE', False), patch.object(kyphone_os, 'handshake', stuck, create=True):
            self.assertFalse(self.real_wait_for_taken(timeout_s=0.05))

    def test_a_plain_command_still_works(self):
        with patch.object(kyphone_os, 'wait_for_ready', return_value=True):
            kyphone_os._send_command('HOME2|x')
        self.assertEqual([self.payload_text(c) for c in self.spi.xfer2.call_args_list], ['HOME2|x'])

    def test_a_newer_command_abandons_the_rest_of_a_page(self):
        self.spi.xfer2.side_effect = lambda payload: self.event.set()              # something new arrives during frame one
        with patch.object(kyphone_os, 'wait_for_ready', return_value=True):
            kyphone_os._send_command(['one', 'two', 'three'])
        self.assertEqual(self.spi.xfer2.call_count, 1)

    def test_a_single_command_is_sent_even_if_something_newer_is_waiting(self):
        self.event.set()
        with patch.object(kyphone_os, 'wait_for_ready', return_value=True):
            kyphone_os._send_command('LOCK|x')
        self.assertEqual(self.spi.xfer2.call_count, 1)

    def test_an_inkplate_that_is_not_ready_stops_the_page(self):
        with patch.object(kyphone_os, 'wait_for_ready', return_value=False):
            kyphone_os._send_command(['one', 'two'])
        self.spi.xfer2.assert_not_called()

    def test_push_page_queues_the_whole_page_as_one_item(self):
        frames = ['RTEXT|M|0|S|hello', 'RFOOT|P|a|b']
        real_push_page = kyphone_os.push_page.__wrapped__ if hasattr(kyphone_os.push_page, '__wrapped__') else None
        self.assertIsNone(real_push_page)                                           # (not wrapped: we call the real one below)
        with patch.object(kyphone_os, 'SIM_MODE', False), patch.object(kyphone_os, '_pending_command', None, create=True):
            kyphone_os.push_page(frames)
            self.assertEqual(kyphone_os._pending_command, frames)
        self.assertTrue(self.event.is_set())

    def test_in_the_emulator_the_page_goes_to_render_page(self):
        sim = MagicMock()
        with patch.object(kyphone_os, 'simulator', sim):
            kyphone_os.push_page(['RTEXT|M|0|S|hello', 'RFOOT|P|a|b'])
        sim.render_page.assert_called_once_with(['RTEXT|M|0|S|hello', 'RFOOT|P|a|b'])


if __name__ == '__main__':
    unittest.main()
