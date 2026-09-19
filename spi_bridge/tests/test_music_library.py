"""
test_music_library.py — finding music and reading its tags (spi_bridge/music_library.py).

Pure standard library: no hardware, no audio, no pygame. Files are built by audio_fixtures.py.

    python3 -m pytest spi_bridge/tests/test_music_library.py -v
"""

import os
import random
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)
import music_library as ml  # noqa: E402
import audio_fixtures as fx  # noqa: E402


class Files(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = os.path.realpath(self._tmp.name)

    def put(self, rel, data=b''):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)
        return path


# ─── Tags, format by format ───────────────────────────────────────────────────

class Id3(Files):
    def read(self, data):
        return ml.read_tags(self.put('t.mp3', data))

    def test_id3v23_latin1(self):
        t = self.read(fx.id3v2(3, title='Song', artist='Band', album='Record', number='3/12', disc='1/2') + fx.mp3_bytes())
        self.assertEqual((t['title'], t['artist'], t['album'], t['number'], t['disc']), ('Song', 'Band', 'Record', '3/12', '1/2'))

    def test_id3v24_utf8_and_syncsafe_frame_sizes(self):
        t = self.read(fx.id3v2(4, encoding=3, title='Café 你', artist='Bånd' * 40) + fx.mp3_bytes())
        self.assertEqual(t['title'], 'Café 你')
        self.assertEqual(len(t['artist']), 160)                     # a frame longer than 127 bytes: the size must be syncsafe

    def test_utf16_with_a_byte_order_mark_and_utf16be(self):
        self.assertEqual(self.read(fx.id3v2(3, encoding=1, title='Björk') + fx.mp3_bytes())['title'], 'Björk')
        self.assertEqual(self.read(fx.id3v2(4, encoding=2, title='Björk') + fx.mp3_bytes())['title'], 'Björk')

    def test_id3v22_three_letter_frames(self):
        t = self.read(fx.id3v2(2, title='Old', artist='Timer', album='Vinyl', number='7') + fx.mp3_bytes())
        self.assertEqual((t['title'], t['artist'], t['album'], t['number']), ('Old', 'Timer', 'Vinyl', '7'))

    def test_an_extended_header_is_skipped(self):
        for version in (3, 4):
            t = self.read(fx.id3v2(version, extended_header=True, title='Ext') + fx.mp3_bytes())
            self.assertEqual(t.get('title'), 'Ext', version)

    def test_a_huge_cover_picture_is_skipped_not_read_and_later_frames_still_found(self):
        data = fx.id3v2(3, art=300000, title='After the art', artist='Band', order=['title', 'artist']) + fx.mp3_bytes()
        self.assertEqual(self.read(data)['title'], 'After the art')

    def test_padding_ends_the_frames(self):
        t = self.read(fx.id3v2(3, padding=500, title='Padded') + fx.mp3_bytes())
        self.assertEqual(t['title'], 'Padded')

    def test_id3v1_alone_and_v1_1_track_number(self):
        t = self.read(fx.mp3_bytes(v1=fx.id3v1('One', 'Two', 'Three', track=9)))
        self.assertEqual((t['title'], t['artist'], t['album'], t['number']), ('One', 'Two', 'Three', '9'))

    def test_id3v2_wins_but_v1_fills_gaps(self):
        t = self.read(fx.id3v2(3, title='V2 title') + fx.mp3_bytes(v1=fx.id3v1('V1 title', 'V1 artist', 'V1 album')))
        self.assertEqual((t['title'], t['artist'], t['album']), ('V2 title', 'V1 artist', 'V1 album'))

    def test_an_absurdly_long_text_frame_is_ignored(self):
        t = self.read(fx.id3v2(3, title='x' * 6000, artist='Fine') + fx.mp3_bytes())
        self.assertNotIn('title', t)
        self.assertEqual(t['artist'], 'Fine')

    def test_cbr_length_is_estimated_from_the_bitrate(self):
        t = self.read(fx.mp3_bytes(frames=200))
        self.assertAlmostEqual(t['seconds'], 200 * 1152 / 44100.0, delta=0.15)

    def test_a_xing_header_gives_the_exact_length(self):
        t = self.read(fx.mp3_bytes(frames=20, xing_frames=5000))
        self.assertAlmostEqual(t['seconds'], 5000 * 1152 / 44100.0, places=3)

    def test_a_tag_and_an_id3v1_trailer_do_not_distort_the_length(self):
        plain = self.read(fx.mp3_bytes(frames=400))['seconds']
        wrapped = self.read(fx.id3v2(3, title='x', art=20000) + fx.mp3_bytes(frames=400, v1=fx.id3v1('a')))['seconds']
        self.assertAlmostEqual(plain, wrapped, delta=0.05)

    def test_garbage_before_the_first_frame_is_skipped(self):
        t = self.read(fx.mp3_bytes(frames=200, junk_before=300))
        self.assertAlmostEqual(t['seconds'], 200 * 1152 / 44100.0, delta=0.3)


class Flac(Files):
    def test_tags_and_length(self):
        p = self.put('a.flac', fx.flac_bytes([('TITLE', 'Song'), ('artist', 'Band'), ('Album', 'Record'), ('ALBUMARTIST', 'Group'),
                                              ('TRACKNUMBER', '4/10'), ('DISCNUMBER', '2')], rate=44100, seconds=185))
        t = ml.read_tags(p)
        self.assertEqual((t['title'], t['artist'], t['album'], t['album_artist'], t['number'], t['disc']),
                         ('Song', 'Band', 'Record', 'Group', '4/10', '2'))
        self.assertAlmostEqual(t['seconds'], 185.0, places=2)

    def test_an_embedded_picture_block_is_skipped(self):
        p = self.put('a.flac', fx.flac_bytes([('TITLE', 'After the picture')], picture=500000))
        self.assertEqual(ml.read_tags(p)['title'], 'After the picture')

    def test_a_high_sample_rate(self):
        p = self.put('a.flac', fx.flac_bytes([], rate=96000, seconds=60))
        self.assertAlmostEqual(ml.read_tags(p)['seconds'], 60.0, places=2)


class Ogg(Files):
    def test_opus_tags_and_length_less_the_pre_skip(self):
        p = self.put('a.opus', fx.ogg_bytes('opus', [('TITLE', 'Song'), ('ARTIST', 'Band'), ('ALBUM', 'Record')], seconds=90, pre_skip=312))
        t = ml.read_tags(p)
        self.assertEqual((t['title'], t['artist'], t['album']), ('Song', 'Band', 'Record'))
        self.assertAlmostEqual(t['seconds'], 90.0, places=3)

    def test_vorbis_tags_and_length(self):
        p = self.put('a.ogg', fx.ogg_bytes('vorbis', [('TITLE', 'Song'), ('ARTIST', 'Band')], seconds=200, rate=44100))
        t = ml.read_tags(p)
        self.assertEqual((t['title'], t['artist']), ('Song', 'Band'))
        self.assertAlmostEqual(t['seconds'], 200.0, places=3)

    def test_a_long_file_reads_its_length_from_the_end(self):
        p = self.put('a.ogg', fx.ogg_bytes('vorbis', [('TITLE', 'Long')], seconds=100) + b'\x00' * 300000)
        self.assertEqual(ml.read_tags(p)['title'], 'Long')


class M4a(Files):
    def test_tags_and_length(self):
        p = self.put('a.m4a', fx.m4a_bytes('Song', 'Band', 'Record', 'Group', track=5, disc=2, seconds=214, timescale=44100))
        t = ml.read_tags(p)
        self.assertEqual((t['title'], t['artist'], t['album'], t['album_artist'], t['number'], t['disc']),
                         ('Song', 'Band', 'Record', 'Group', '5', '2'))
        self.assertAlmostEqual(t['seconds'], 214.0, places=2)

    def test_moov_before_or_after_the_audio_data(self):
        for first in (True, False):
            p = self.put('a.m4a', fx.m4a_bytes('Song', seconds=30, moov_first=first))
            self.assertEqual(ml.read_tags(p)['title'], 'Song', first)

    def test_cover_art_is_harmless(self):
        p = self.put('a.m4a', fx.m4a_bytes('Song', 'Band', art=800000))
        self.assertEqual(ml.read_tags(p)['artist'], 'Band')

    def test_the_aac_extension_uses_the_same_reader(self):
        p = self.put('a.aac', fx.m4a_bytes('Song'))
        self.assertEqual(ml.read_tags(p)['title'], 'Song')


class Wav(Files):
    def test_length_comes_from_the_header(self):
        p = self.put('a.wav', fx.wav_bytes(seconds=2.5, rate=8000))
        self.assertAlmostEqual(ml.read_tags(p)['seconds'], 2.5, places=2)


class DamagedFiles(Files):
    def test_truncating_any_format_at_any_point_never_raises(self):
        samples = {'x.mp3': fx.id3v2(3, title='T', artist='A', art=2000) + fx.mp3_bytes(frames=20, v1=fx.id3v1('a')),
                   'x.flac': fx.flac_bytes([('TITLE', 'T')], picture=2000), 'x.opus': fx.ogg_bytes('opus', [('TITLE', 'T')]),
                   'x.ogg': fx.ogg_bytes('vorbis', [('TITLE', 'T')]), 'x.m4a': fx.m4a_bytes('T', 'A', art=2000),
                   'x.wav': fx.wav_bytes(0.2)}
        for name, data in samples.items():
            for cut in list(range(0, min(len(data), 300), 7)) + list(range(300, len(data), 173)):
                tags = ml.read_tags(self.put(name, data[:cut]))
                self.assertIsInstance(tags, dict, (name, cut))

    def test_random_bytes_in_any_audio_file_never_raise(self):
        rng = random.Random(1)
        for ext in ml.AUDIO_EXTS:
            for n in (0, 1, 10, 100, 5000):
                data = bytes(rng.randrange(256) for _ in range(n))
                self.assertIsInstance(ml.read_tags(self.put('junk' + ext, data)), dict, (ext, n))

    def test_corrupted_bytes_inside_a_good_file_never_raise(self):
        rng = random.Random(2)
        good = {'g.mp3': fx.id3v2(3, title='T', artist='A') + fx.mp3_bytes(frames=10), 'g.flac': fx.flac_bytes([('TITLE', 'T')]),
                'g.opus': fx.ogg_bytes('opus', [('TITLE', 'T')]), 'g.m4a': fx.m4a_bytes('T', 'A')}
        for name, data in good.items():
            for _ in range(60):
                bad = bytearray(data)
                for _ in range(5):
                    bad[rng.randrange(len(bad))] = rng.randrange(256)
                self.assertIsInstance(ml.read_tags(self.put(name, bytes(bad))), dict, name)

    def test_an_unknown_extension_and_a_missing_file_give_no_tags(self):
        self.assertEqual(ml.read_tags(self.put('a.xyz', b'hello')), {})
        self.assertEqual(ml.read_tags(os.path.join(self.root, 'nope.mp3')), {})


# ─── Names, folders, drawable text ────────────────────────────────────────────

class Naming(Files):
    def one(self, rel, data=b'', **kw):
        self.put(rel, data)
        lib = ml.scan(self.root)
        self.assertEqual(len(lib), 1)
        return lib.albums[0].tracks[0]

    def test_an_untagged_file_is_described_by_its_folders_and_name(self):
        t = self.one('Nina Simone/Pastel Blues/03 - Sinnerman.mp3')
        self.assertEqual((t.artist, t.album, t.title, t.number), ('Nina Simone', 'Pastel Blues', 'Sinnerman', 3))

    def test_several_numbering_styles(self):
        for name, number, title in (('07. Name.mp3', 7, 'Name'), ('5 Name.mp3', 5, 'Name'), ('12_the_song.mp3', 12, 'the song'),
                                    ('4) Song.mp3', 4, 'Song'), ('Plain title.mp3', None, 'Plain title')):
            with tempfile.TemporaryDirectory() as d:
                path = os.path.join(d, 'A', 'B', name)
                os.makedirs(os.path.dirname(path))
                open(path, 'wb').close()
                t = ml.scan(d).albums[0].tracks[0]
                self.assertEqual((t.number, t.title), (number, title), name)

    def test_a_file_directly_in_the_music_folder_is_a_loose_track(self):
        t = self.one('Lonely.mp3')
        self.assertEqual((t.album, t.artist, t.title), (ml.LOOSE_ALBUM, ml.UNKNOWN_ARTIST, 'Lonely'))

    def test_two_folders_deep_has_no_artist_folder(self):
        t = self.one('Some Album/01 Song.mp3')
        self.assertEqual((t.album, t.artist), ('Some Album', ml.UNKNOWN_ARTIST))

    def test_tags_beat_the_folders(self):
        t = self.one('Folder Artist/Folder Album/01 File.mp3', fx.id3v2(3, title='Tag Title', artist='Tag Artist', album='Tag Album') + fx.mp3_bytes())
        self.assertEqual((t.title, t.artist, t.album), ('Tag Title', 'Tag Artist', 'Tag Album'))

    def test_everything_shown_is_drawable_ascii(self):
        t = self.one('x/y/z.mp3', fx.id3v2(4, encoding=3, title='Café | Nights — 你 \U0001f600 · dot', artist='Björk\x07', album='“Quoted”') + fx.mp3_bytes())
        for text in (t.title, t.artist, t.album):
            self.assertTrue(all(' ' <= c <= '~' and c != '|' for c in text), repr(text))
        self.assertEqual(t.title, 'Cafe / Nights -- ? ? . dot')          # each separate undrawable character is one '?'
        self.assertEqual(t.artist, 'Bjork')

    def test_empty_tags_fall_back_to_the_name(self):
        t = self.one('A/B/02 Real name.mp3', fx.id3v2(3, title='   ', artist='') + fx.mp3_bytes())
        self.assertEqual((t.title, t.artist), ('Real name', 'A'))

    def test_seconds_are_whole_numbers_or_none(self):
        self.assertEqual(self.one('a/b/c.wav', fx.wav_bytes(2.6)).seconds, 3)
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, 'a', 'b'))
            open(os.path.join(d, 'a', 'b', 'c.mp3'), 'wb').close()
            self.assertIsNone(ml.scan(d).albums[0].tracks[0].seconds)

    def test_format_time(self):
        self.assertEqual([ml.format_time(x) for x in (None, -1, 0, 5, 65, 222, 3599, 3723)], ['', '', '0:00', '0:05', '1:05', '3:42', '59:59', '1:02:03'])


# ─── Albums ───────────────────────────────────────────────────────────────────

class Albums(Files):
    def tag(self, rel, **kw):
        self.put(rel, fx.id3v2(3, **kw) + fx.mp3_bytes(frames=5))

    def test_albums_are_sorted_a_to_z_ignoring_case(self):
        for i, name in enumerate(['zebra', 'Apple', 'mango', 'Banana']):
            self.tag('f%d.mp3' % i, album=name, artist='X', title='t%d' % i)
        self.assertEqual([a.name for a in ml.scan(self.root).albums], ['Apple', 'Banana', 'mango', 'zebra'])

    def test_tracks_run_by_disc_then_number_with_unnumbered_last(self):
        self.tag('a.mp3', album='A', artist='X', title='Two', number='2')
        self.tag('b.mp3', album='A', artist='X', title='One', number='1')
        self.tag('c.mp3', album='A', artist='X', title='Disc two first', number='1', disc='2/2')
        self.tag('d.mp3', album='A', artist='X', title='No number')
        self.assertEqual([t.title for t in ml.scan(self.root).albums[0].tracks], ['One', 'Two', 'No number', 'Disc two first'])

    def test_a_compilation_with_an_album_artist_tag_is_one_album_by_that_artist(self):
        self.tag('a/1.mp3', album='Hits', artist='One', album_artist='Various Artists', title='a')
        self.tag('b/2.mp3', album='Hits', artist='Two', album_artist='Various Artists', title='b')
        albums = ml.scan(self.root).albums
        self.assertEqual((len(albums), albums[0].artist, len(albums[0].tracks)), (1, 'Various Artists', 2))

    def test_a_folder_of_one_album_with_different_track_artists_is_various(self):
        self.tag('comp/1.mp3', album='Mix', artist='One', title='a')
        self.tag('comp/2.mp3', album='Mix', artist='Two', title='b')
        albums = ml.scan(self.root).albums
        self.assertEqual((len(albums), albums[0].artist), (1, 'Various artists'))

    def test_two_artists_with_the_same_album_name_stay_separate(self):
        self.tag('A/Greatest Hits/1.mp3', album='Greatest Hits', artist='A', title='a')
        self.tag('B/Greatest Hits/1.mp3', album='Greatest Hits', artist='B', title='b')
        self.assertEqual(sorted(a.artist for a in ml.scan(self.root).albums), ['A', 'B'])

    def test_disc_subfolders_join_their_album(self):
        self.tag('Band/Big Album/CD 1/1.mp3', album='Big Album', artist='Band', title='a', number='1')
        self.tag('Band/Big Album/CD 2/1.mp3', album='Big Album', artist='Band', title='b', number='1', disc='2')
        albums = ml.scan(self.root).albums
        self.assertEqual((len(albums), len(albums[0].tracks)), (1, 2))

    def test_a_single_artist_album_is_credited_to_that_artist(self):
        self.tag('1.mp3', album='Solo', artist='Someone', title='a')
        self.tag('2.mp3', album='Solo', artist='Someone', title='b')
        self.assertEqual(ml.scan(self.root).albums[0].artist, 'Someone')


# ─── Scanning and the cache ───────────────────────────────────────────────────

class Scanning(Files):
    def test_a_missing_folder_or_a_file_is_an_empty_library(self):
        self.assertEqual(len(ml.scan(os.path.join(self.root, 'nope'))), 0)
        self.assertEqual(len(ml.scan(self.put('afile.mp3', b''))), 0)

    def test_only_audio_files_count_and_hidden_ones_are_ignored(self):
        self.put('a/song.mp3')
        self.put('a/notes.txt', b'x')
        self.put('a/cover.jpg', b'x')
        self.put('a/._song.mp3', b'x')                             # macOS resource fork
        self.put('.hidden/x.mp3')
        self.put('a/UPPER.MP3')
        self.assertEqual(len(ml.scan(self.root)), 2)

    def test_subfolders_are_searched_to_a_depth_limit(self):
        self.put('/'.join(['d'] * 3) + '/ok.mp3')
        self.put('/'.join(['d'] * (ml.MAX_DEPTH + 3)) + '/toodeep.mp3')
        self.assertEqual(len(ml.scan(self.root)), 1)

    def test_a_huge_library_is_capped(self):
        for i in range(30):
            self.put('a/%02d.mp3' % i)
        old, ml.MAX_FILES = ml.MAX_FILES, 10
        try:
            self.assertEqual(len(ml.scan(self.root)), 10)
        finally:
            ml.MAX_FILES = old

    def test_the_index_is_used_on_the_second_scan_and_only_changes_are_reread(self):
        index = os.path.join(self.root, '..', 'idx-%d.json' % os.getpid())
        self.addCleanup(lambda: os.path.exists(index) and os.remove(index))
        paths = [self.put('m/%d.mp3' % i, fx.id3v2(3, title='T%d' % i) + fx.mp3_bytes(frames=4)) for i in range(5)]
        calls = []
        real = ml.read_tags
        ml.read_tags = lambda p: (calls.append(p), real(p))[1]
        try:
            ml.scan(self.root, index)
            self.assertEqual(len(calls), 5)
            calls.clear()
            lib = ml.scan(self.root, index)
            self.assertEqual(calls, [])                            # everything from the index
            self.assertEqual(sorted(t.title for t in lib.albums[0].tracks), ['T0', 'T1', 'T2', 'T3', 'T4'])
            with open(paths[2], 'wb') as f:                        # one file changes
                f.write(fx.id3v2(3, title='Changed') + fx.mp3_bytes(frames=9))
            self.put('m/new.mp3', fx.id3v2(3, title='New') + fx.mp3_bytes(frames=4))
            os.remove(paths[0])
            calls.clear()
            lib = ml.scan(self.root, index)
            self.assertEqual(sorted(os.path.basename(c) for c in calls), ['2.mp3', 'new.mp3'])
            self.assertEqual(sorted(t.title for t in lib.albums[0].tracks), ['Changed', 'New', 'T1', 'T3', 'T4'])
        finally:
            ml.read_tags = real

    def test_a_deleted_file_leaves_the_index(self):
        import json
        index = os.path.join(self.root, '..', 'idx2-%d.json' % os.getpid())
        self.addCleanup(lambda: os.path.exists(index) and os.remove(index))
        a, b = self.put('x/a.mp3'), self.put('x/b.mp3')
        ml.scan(self.root, index)
        os.remove(a)
        ml.scan(self.root, index)
        with open(index) as f:
            self.assertEqual(list(json.load(f)), [os.path.join('x', 'b.mp3')])

    def test_a_damaged_or_unwritable_index_is_harmless(self):
        self.put('a/1.mp3')
        bad = self.put('bad-index.json', b'{{{ not json')
        self.assertEqual(len(ml.scan(self.root, bad)), 1)
        self.assertEqual(len(ml.scan(self.root, os.path.join(self.root, 'a', '1.mp3', 'cannot', 'be', 'made.json'))), 1)

    def test_five_thousand_tracks_scan_quickly_and_faster_from_the_index(self):
        for i in range(5000):
            self.put('Artist%d/Album%d/%03d.mp3' % (i // 100, i // 10, i % 1000),
                     fx.id3v2(3, title='Song %d' % i, artist='Artist %d' % (i // 100), album='Album %d' % (i // 10), number=str(i % 10 + 1)) + fx.mp3_bytes(frames=3))
        index = os.path.join(self.root, '..', 'idx3-%d.json' % os.getpid())
        self.addCleanup(lambda: os.path.exists(index) and os.remove(index))
        t0 = time.time()
        lib = ml.scan(self.root, index)
        cold = time.time() - t0
        t0 = time.time()
        ml.scan(self.root, index)
        warm = time.time() - t0
        self.assertEqual((len(lib), len(lib.albums)), (5000, 500))
        self.assertLess(cold, 15)
        self.assertLess(warm, cold)


if __name__ == '__main__':
    unittest.main()
