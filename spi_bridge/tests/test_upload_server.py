"""
test_upload_server.py — adding books, music and contacts from a computer (spi_bridge/upload_server.py).

Runs a real server on 127.0.0.1 (a free port) and talks to it over HTTP, so the code check, the file-name cleaning,
the size and type limits and a cut-off upload are all exercised end to end. Nothing leaves this computer.

    python3 -m pytest spi_bridge/tests/test_upload_server.py -v
"""

import os
import shutil
import socket
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
import unittest.mock
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
import upload_server as us  # noqa: E402


class SafeNames(unittest.TestCase):
    def test_only_the_last_part_of_a_path_is_kept(self):
        self.assertEqual(us.safe_name('../../etc/passwd.epub', us.BOOK_TYPES), 'passwd.epub')
        self.assertEqual(us.safe_name('C:\\Users\\x\\Moby Dick.EPUB', us.BOOK_TYPES), 'Moby Dick.epub')

    def test_no_hidden_files_and_odd_characters_are_replaced(self):
        self.assertEqual(us.safe_name('.bashrc.epub', us.BOOK_TYPES), 'bashrc.epub')
        self.assertEqual(us.safe_name('Björk – Jóga.mp3', us.MUSIC_TYPES), 'Bj_rk _ J_ga.mp3')
        self.assertEqual(us.safe_name('a;rm -rf *.mp3', us.MUSIC_TYPES), 'a_rm -rf _.mp3')

    def test_the_type_must_be_one_the_phone_uses(self):
        for bad in ('virus.exe', 'book.epub.exe', 'song.mp3', '.epub', '', '...epub'):
            self.assertIsNone(us.safe_name(bad, us.BOOK_TYPES), bad)
        self.assertIsNone(us.safe_name('book.epub', us.MUSIC_TYPES))

    def test_folders_are_cleaned_the_same_way(self):
        self.assertEqual(us.safe_folder('../../Kind of Blue'), 'Kind of Blue')
        self.assertEqual(us.safe_folder('..'), '')
        self.assertEqual(us.safe_folder(''), '')

    def test_unique_path_never_overwrites(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        open(os.path.join(d, 'a.mp3'), 'w').close()
        open(os.path.join(d, 'a (2).mp3'), 'w').close()
        self.assertEqual(us.unique_path(d, 'a.mp3'), os.path.join(d, 'a (3).mp3'))

    def test_the_code_is_six_digits(self):
        for _ in range(50):
            self.assertRegex(us.new_code(), r'^\d{6}$')


class VCards(unittest.TestCase):
    def test_version_3_with_name_parts_and_the_first_number(self):
        text = ('BEGIN:VCARD\r\nVERSION:3.0\r\nN:Okonkwo;Pip;;;\r\nFN:Pip Okonkwo\r\n'
                'TEL;TYPE=CELL:(555) 010-0101\r\nTEL;TYPE=HOME:555-010-0199\r\nEND:VCARD\r\n')
        self.assertEqual(us.parse_vcards(text), [('Pip', 'Okonkwo', '(555) 010-0101')])

    def test_folded_lines_grouped_properties_and_no_n(self):
        text = ('BEGIN:VCARD\nVERSION:2.1\nFN:Sam\n Whitfield\nitem1.TEL:+1 555 010 0102\nEND:VCARD\n'
                'BEGIN:VCARD\nFN:Ada Lovelace\nEND:VCARD\n')
        self.assertEqual(us.parse_vcards(text), [('SamWhitfield', '', '+1 555 010 0102'), ('Ada', 'Lovelace', '')])

    def test_a_card_with_no_name_is_skipped_and_a_surname_alone_becomes_the_first_name(self):
        text = ('BEGIN:VCARD\nTEL:5550100103\nEND:VCARD\n'
                'BEGIN:VCARD\nN:Plumber;;;;\nTEL:5550100104\nEND:VCARD\n')
        self.assertEqual(us.parse_vcards(text), [('Plumber', '', '5550100104')])

    def test_garbage_never_raises(self):
        for text in ('', 'END:VCARD', 'BEGIN:VCARD', ':::', '\x00\xff' * 50, 'BEGIN:VCARD\nN:;\nEND:VCARD'):
            us.parse_vcards(text)


class Server(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.books, self.music = os.path.join(self.tmp, 'books'), os.path.join(self.tmp, 'music')
        self.events, self.stopped, self.cards = [], [], []

        def contacts(cards):
            self.cards.extend(cards)
            return f'Contacts: {len(cards)} added'
        self.server = us.UploadServer(self.books, self.music, self.events.append, contacts, self.stopped.append,
                                      code='4821', host='127.0.0.1', port=0).start()
        self.addCleanup(self.server.stop)
        self.base = f'http://127.0.0.1:{self.server.port}'

    def put(self, kind, name, body, code='4821', folder=''):
        q = urllib.parse.urlencode({'kind': kind, 'name': name, 'folder': folder})
        req = urllib.request.Request(f'{self.base}/upload?{q}', data=body, method='PUT', headers={'X-Code': code})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def files(self, folder):
        out = []
        for root, _, names in os.walk(folder):
            out += [os.path.relpath(os.path.join(root, n), folder) for n in names]
        return sorted(out)

    def test_the_page_is_served_and_nothing_else(self):
        with urllib.request.urlopen(self.base + '/', timeout=5) as r:
            page = r.read().decode()
        self.assertIn('ADD TO KYPHONE', page)
        self.assertIn("setRequestHeader('X-Code'", page)          # every upload carries the code
        self.assertIn('webkitGetAsEntry', page)                    # folders can be dropped
        self.assertNotIn('4821', page)                                  # the code is only on the phone
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(self.base + '/data/contacts.json', timeout=5)

    def test_every_reply_carries_the_security_headers(self):
        with urllib.request.urlopen(self.base + '/', timeout=5) as r:
            headers = r.headers
        self.assertEqual(headers['X-Frame-Options'], 'DENY')
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertIn("connect-src 'self'", headers['Content-Security-Policy'])

    def test_a_request_addressed_to_another_name_is_refused(self):
        server = us.UploadServer(self.books, self.music, self.events.append, lambda c: '', code='4821',
                                 host='127.0.0.1', port=0, address='127.0.0.1').start()
        self.addCleanup(server.stop)
        ok = urllib.request.urlopen(f'http://127.0.0.1:{server.port}/', timeout=5)
        self.assertEqual(ok.status, 200)                              # the phone's own address: fine
        req = urllib.request.Request(f'http://127.0.0.1:{server.port}/upload?kind=books&name=a.epub', data=b'x',
                                     method='PUT', headers={'X-Code': '4821', 'Host': 'evil.example:%d' % server.port})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 421)
        self.assertFalse(os.path.exists(self.books))

    def test_it_stops_by_itself_when_left_idle(self):
        import time
        with unittest.mock.patch.object(us, 'IDLE_SECONDS', 0.4):
            server = us.UploadServer(self.books, self.music, self.events.append, lambda c: '',
                                     self.stopped.append, code='4821', host='127.0.0.1', port=0).start()
            self.addCleanup(server.stop)
            for _ in range(40):
                if not server.running:
                    break
                time.sleep(0.05)
        self.assertFalse(server.running)
        self.assertEqual(self.stopped, ['idle'])

    def test_a_book_arrives_and_is_announced(self):
        self.assertEqual(self.put('books', 'Moby Dick.epub', b'PK\x03\x04book'), (200, 'added Moby Dick.epub'))
        with open(os.path.join(self.books, 'Moby Dick.epub'), 'rb') as f:
            self.assertEqual(f.read(), b'PK\x03\x04book')
        self.assertEqual(self.events, ['Moby Dick.epub'])

    def test_the_same_name_twice_is_kept_as_a_second_file(self):
        self.put('books', 'a.epub', b'one')
        self.assertEqual(self.put('books', 'a.epub', b'two'), (200, 'added a (2).epub'))
        self.assertEqual(self.files(self.books), ['a (2).epub', 'a.epub'])

    def test_music_goes_into_its_album_folder_and_paths_cannot_escape(self):
        self.put('music', '01 So What.mp3', b'ID3', folder='Kind of Blue')
        self.put('music', '../../../evil.mp3', b'ID3', folder='../..')
        self.assertEqual(self.files(self.music), ['Kind of Blue/01 So What.mp3', 'evil.mp3'])
        self.assertEqual(os.listdir(self.tmp), ['music'])                # nothing written outside it

    def test_no_code_or_a_wrong_code_is_refused_and_nothing_is_written(self):
        self.assertEqual(self.put('books', 'a.epub', b'x', code='')[0], 403)
        self.assertEqual(self.put('books', 'a.epub', b'x', code='0000')[0], 403)
        self.assertFalse(os.path.exists(self.books))

    def test_ten_wrong_codes_stop_the_server(self):
        for i in range(us.MAX_BAD_CODES):
            self.put('books', 'a.epub', b'x', code=f'{i:04d}' if i != 4821 else '9999')
        self.assertEqual(self.stopped, ['too many wrong codes'])
        self.assertFalse(self.server.running)
        try:                                                            # the right code no longer helps:
            refused = self.put('books', 'a.epub', b'x')[0] == 403        # still closing, it refuses...
        except OSError:
            refused = True                                              # ...or it is already gone
        self.assertTrue(refused)
        self.assertFalse(os.path.exists(self.books))

    def test_a_wrong_type_or_too_large_a_file_is_refused(self):
        self.assertEqual(self.put('books', 'notes.txt', b'x'), (415, 'not a file type the phone can use'))
        with patch.object(us, 'MAX_BOOK_BYTES', 10):
            self.assertEqual(self.put('books', 'big.epub', b'x' * 11), (413, 'too large'))
        self.assertFalse(os.path.exists(self.books) and os.listdir(self.books))

    def test_no_room_on_the_disk_is_refused(self):
        with patch.object(us, 'DISK_SPARE_BYTES', 10 ** 18):
            self.assertEqual(self.put('books', 'a.epub', b'x'), (507, 'not enough room on the phone'))

    def test_a_cut_off_upload_leaves_nothing_behind(self):
        s = socket.create_connection(('127.0.0.1', self.server.port), timeout=5)
        s.sendall(b'PUT /upload?kind=books&name=half.epub HTTP/1.1\r\nHost: x\r\nX-Code: 4821\r\n'
                  b'Content-Length: 100000\r\n\r\n' + b'x' * 1000)
        s.close()
        for _ in range(50):                                             # give the server a moment to notice
            if self.put('books', 'probe.epub', b'p')[0] == 200:
                break
        self.assertEqual(self.files(self.books), ['probe.epub'])      # no half.epub, no temporary file

    def test_contacts_are_parsed_and_handed_over(self):
        vcf = b'BEGIN:VCARD\nN:Okonkwo;Pip\nTEL:5550100101\nEND:VCARD\n'
        self.assertEqual(self.put('contacts', 'contacts.vcf', vcf), (200, 'Contacts: 1 added'))
        self.assertEqual(self.cards, [('Pip', 'Okonkwo', '5550100101')])
        self.assertEqual(self.events, ['Contacts: 1 added'])

    def test_stopping_closes_the_port(self):
        self.server.stop()
        for _ in range(50):
            try:
                socket.create_connection(('127.0.0.1', self.server.port), timeout=0.2).close()
            except OSError:
                break
            import time
            time.sleep(0.05)
        else:
            self.fail('the port stayed open')


if __name__ == '__main__':
    unittest.main()
