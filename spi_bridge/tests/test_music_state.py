"""
test_music_state.py — the LISTEN screens of the OS state machine (kyphone_os.py).

Drives the real handle_key against a temporary music folder of tiny WAV files. The player is the silent SimPlayer and
its clock is advanced by hand, so nothing is heard and nothing waits. Never touches the real data/ folder.

    python3 -m pytest spi_bridge/tests/test_music_state.py -v
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.argv = ['test', '--sim']
for _name in ('spidev', 'gpiod', 'input_handler', 'evdev', 'twilio', 'twilio.rest'):
    sys.modules.setdefault(_name, MagicMock())
_faked = [name for name in ('pygame', 'simulator') if name not in sys.modules]
for _name in _faked:
    sys.modules[_name] = MagicMock()
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, HERE)
import kyphone_os  # noqa: E402
import music_player  # noqa: E402
import audio_fixtures as fx  # noqa: E402
for _name in _faked:
    if isinstance(sys.modules.get(_name), MagicMock):
        del sys.modules[_name]

SEP = '\xb7'


class Clock:
    def __init__(self):
        self.t = 5000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class MusicCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = os.path.realpath(self._tmp.name)
        self.music = os.path.join(self.tmp, 'music')
        os.makedirs(self.music)
        self.clock = Clock()
        for name, value in (('MUSIC_DIR', self.music), ('MUSIC_INDEX_FILE', os.path.join(self.tmp, 'music_index.json')),
                            ('LISTENING_FILE', os.path.join(self.tmp, 'listening.json')), ('DATA_DIR', self.tmp),
                            ('_MUSIC_CLOCK', self.clock)):
            p = patch.object(kyphone_os, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.screens = []
        p = patch.object(kyphone_os, 'push_screen', side_effect=self.screens.append)
        p.start()
        self.addCleanup(p.stop)
        self.reset()

    def reset(self):
        kyphone_os._music = None
        kyphone_os._music_problem = None
        kyphone_os._music_tick_count = 0
        kyphone_os._music_save_count = 0
        kyphone_os._music_lengths.clear()
        kyphone_os.state.update(screen='home', home_index=kyphone_os.HOME_MENU.index('LISTEN'), music_lib=None, music_index=0,
                                music_start=0, album=None, tracks_index=0, tracks_start=0, music_return='music',
                                music_last=None, messages=[], stub_key='', stub_text=None, stub_return='home')

    # helpers
    @property
    def st(self):
        return kyphone_os.state

    def wav(self, rel, seconds=10):
        path = os.path.join(self.music, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(fx.wav_bytes(seconds, rate=800))
        return path

    def album_one(self):
        for n, name in enumerate(['First', 'Second', 'Third'], 1):
            self.wav('Artist A/Album One/%02d %s.wav' % (n, name), 10)

    def library(self):
        self.album_one()
        self.wav('Artist B/Album Two/01 Solo.wav', 20)

    def key(self, *keys):
        for k in keys:
            kyphone_os.handle_key(k)

    def open_music(self):
        self.st['screen'] = 'home'
        self.key('KEY_ENTER')
        self.assertEqual(self.st['screen'], 'music')

    def open_album(self, index=0):
        self.open_music()
        self.key(*['KEY_DOWN'] * index)
        self.key('KEY_ENTER')
        self.assertEqual(self.st['screen'], 'tracks')

    def play_track(self, track=0, album=0):
        self.open_album(album)
        self.key(*['KEY_DOWN'] * track)
        self.key('KEY_ENTER')
        self.assertEqual(self.st['screen'], 'nowplaying')

    def tick(self, seconds):
        for _ in range(int(seconds)):
            self.clock.advance(1)
            kyphone_os._music_tick()

    @property
    def last(self):
        return self.screens[-1]

    def fields(self):
        return self.last.split('|')

    def saved(self):
        with open(kyphone_os.LISTENING_FILE) as f:
            return json.load(f)


class AlbumList(MusicCase):
    def test_an_empty_library_shows_only_the_header(self):
        self.open_music()
        self.assertEqual(self.last, 'MUSIC|-1')
        self.key('KEY_DOWN')
        self.assertEqual(self.last, 'MUSIC|-1')
        self.key('KEY_ENTER')
        self.assertEqual(self.st['screen'], 'home')

    def test_albums_are_listed_a_to_z_with_artist_and_track_count(self):
        self.library()
        self.open_music()
        self.assertEqual(self.last, 'MUSIC|0|Album One%sArtist A%s3 trk|Album Two%sArtist B%s1 trk' % (SEP, SEP, SEP, SEP))

    def test_up_and_down_move_and_the_header_is_reachable(self):
        self.library()
        self.open_music()
        self.key('KEY_DOWN', 'KEY_DOWN', 'KEY_DOWN')
        self.assertEqual(self.st['music_index'], 1)                     # stops at the last album
        self.key('KEY_UP', 'KEY_UP', 'KEY_UP')
        self.assertEqual(self.st['music_index'], -1)
        self.assertTrue(self.last.startswith('MUSIC|-1|'))
        self.key('KEY_ENTER')
        self.assertEqual(self.st['screen'], 'home')

    def test_esc_and_q_go_home(self):
        self.library()
        self.open_music()
        self.key('KEY_ESC')
        self.assertEqual(self.st['screen'], 'home')
        self.open_music()
        self.key('CHAR:q')
        self.assertEqual(self.st['screen'], 'home')

    def test_more_than_five_albums_are_windowed_with_a_window_relative_selection(self):
        for i in range(8):
            self.wav('Artist/Album %d/01 Song.wav' % i, 1)
        self.open_music()
        self.assertEqual(len(self.last.split('|')[2:]), 5)
        self.key(*['KEY_DOWN'] * 6)
        self.assertEqual(self.fields()[1], '4')                            # the last visible row
        self.assertEqual(self.fields()[2].split(SEP)[0], 'Album 2')        # scrolled one row at a time
        self.assertLessEqual(len(self.last), kyphone_os.MAX_COMMAND_CHARS)

    def test_long_names_are_cut_and_the_frame_still_fits(self):
        for i in range(6):
            self.wav('%s/%s/01 Song.wav' % ('A' * 60 + str(i), 'Album ' + 'x' * 70 + str(i)), 1)
        self.open_music()
        self.assertLessEqual(len(self.last), kyphone_os.MAX_COMMAND_CHARS)
        for row in self.last.split('|')[2:]:
            title, artist, count = row.split(SEP)
            self.assertLessEqual(len(title), kyphone_os.MUSIC_TITLE_MAX)
            self.assertLessEqual(len(artist), kyphone_os.MUSIC_SUB_MAX)


class TrackList(MusicCase):
    def test_an_album_lists_its_tracks_with_artist_and_length(self):
        self.library()
        self.open_album(0)
        self.assertEqual(self.last, 'TRACKS|0|Album One|First%sArtist A%s0:10|Second%sArtist A%s0:10|Third%sArtist A%s0:10'
                         % ((SEP,) * 6))

    def test_up_reaches_the_header_and_enter_or_esc_returns_to_the_albums(self):
        self.library()
        self.open_album(0)
        self.key('KEY_UP')
        self.assertTrue(self.last.startswith('TRACKS|-1|Album One|'))
        self.key('KEY_ENTER')
        self.assertEqual(self.st['screen'], 'music')
        self.open_album(0)
        self.key('KEY_ESC')
        self.assertEqual(self.st['screen'], 'music')

    def test_a_long_album_is_windowed(self):
        for i in range(12):
            self.wav('Band/Big/%02d Song %d.wav' % (i + 1, i + 1), 1)
        self.open_album(0)
        self.assertEqual(len(self.last.split('|')[3:]), 5)
        self.key(*['KEY_DOWN'] * 7)
        self.assertEqual(self.fields()[1], '4')
        self.assertLessEqual(len(self.last), kyphone_os.MAX_COMMAND_CHARS)


class NowPlaying(MusicCase):
    def setUp(self):
        super().setUp()
        self.library()

    def test_choosing_a_track_plays_the_album_from_there_and_shows_it(self):
        self.play_track(1)
        self.assertEqual(self.last, 'NOWPLAYING|P|Second|Artist A|Album One|0|10|%d|2/3' % music_player.DEFAULT_VOLUME)
        player = kyphone_os._music.player
        self.assertEqual([os.path.basename(p) for p in player.loaded], ['02 Second.wav'])

    def test_space_and_enter_pause_and_resume(self):
        self.play_track(0)
        self.key('CHAR: ')
        self.assertEqual(self.fields()[1], 'U')
        self.key('KEY_ENTER')
        self.assertEqual(self.fields()[1], 'P')

    def test_time_moves_and_a_pause_holds_it(self):
        self.play_track(0)
        self.clock.advance(4)
        self.key('CHAR: ')                                                # pause after 4 s
        self.assertEqual(self.fields()[5], '4')
        self.clock.advance(60)
        self.key('CHAR: ')
        self.clock.advance(2)
        self.key('CHAR: ')
        self.assertEqual(self.fields()[5], '6')

    def test_right_is_next_and_at_the_end_it_redraws_instead_of_going_quiet(self):
        self.play_track(1)
        self.key('KEY_RIGHT')
        self.assertEqual((self.fields()[2], self.fields()[8]), ('Third', '3/3'))
        count = len(self.screens)
        self.key('CHAR:d')                                                # D is Right; nothing after the last track
        self.assertEqual(len(self.screens), count + 1)
        self.assertEqual(self.fields()[8], '3/3')

    def test_left_goes_back_near_the_start_and_restarts_further_in(self):
        self.play_track(2)
        self.clock.advance(5)
        self.key('KEY_LEFT')
        self.assertEqual((self.fields()[2], self.fields()[5]), ('Third', '0'))      # restarted
        self.clock.advance(1)
        self.key('CHAR:a')
        self.assertEqual(self.fields()[2], 'Second')

    def test_volume_up_and_down_by_arrows_and_plus_minus_and_it_clamps(self):
        self.play_track(0)
        base = music_player.DEFAULT_VOLUME
        self.key('KEY_UP')
        self.assertEqual(int(self.fields()[7]), base + 5)
        self.key('CHAR:+', 'CHAR:=')
        self.assertEqual(int(self.fields()[7]), base + 15)
        self.key('KEY_DOWN', 'CHAR:-', 'CHAR:_')
        self.assertEqual(int(self.fields()[7]), base)
        for _ in range(30):
            self.key('CHAR:w')                                            # W is Up
        self.assertEqual(int(self.fields()[7]), 100)
        count = len(self.screens)
        self.key('KEY_UP')
        self.assertEqual(len(self.screens), count + 1)                     # a redraw even at the limit
        for _ in range(30):
            self.key('CHAR:s')
        self.assertEqual(int(self.fields()[7]), 0)

    def test_seek_forward_and_back_stays_inside_the_track(self):
        self.wav('Artist B/Album Two/02 Long.wav', 100)
        self.play_track(1, album=1)
        self.key('CHAR:.')
        self.assertEqual(self.fields()[5], '15')
        self.key('CHAR:>')
        self.assertEqual(self.fields()[5], '30')
        self.key('CHAR:,', 'CHAR:<', 'CHAR:<', 'CHAR:<')
        self.assertEqual(self.fields()[5], '0')
        for _ in range(10):
            self.key('CHAR:.')
        self.assertEqual(self.fields()[5], '99')                           # a second short of the end

    def test_esc_returns_to_where_you_came_from_and_the_music_goes_on(self):
        self.play_track(0)
        self.key('KEY_ESC')
        self.assertEqual(self.st['screen'], 'tracks')
        self.assertTrue(kyphone_os._music.playing)
        self.key('KEY_ESC')
        self.assertEqual(self.st['screen'], 'music')

    def test_choosing_the_track_already_playing_just_shows_it(self):
        self.play_track(1)
        self.key('KEY_ESC')
        self.key('KEY_DOWN', 'KEY_DOWN')                                   # tracks_index is still 0; go to track 2 again
        self.st['tracks_index'] = 1
        self.key('KEY_ENTER')
        self.assertEqual(self.st['screen'], 'nowplaying')
        self.assertEqual(len(kyphone_os._music.player.loaded), 1)          # not reloaded

    def test_the_now_playing_row_leads_the_album_list_and_opens_the_screen(self):
        self.play_track(2)
        self.key('KEY_ESC', 'KEY_ESC')
        self.assertTrue(self.last.startswith('MUSIC|0|NOW PLAYING%sThird - Artist A%sPLAYING|Album One' % (SEP, SEP)))
        self.key('KEY_ENTER')
        self.assertEqual(self.st['screen'], 'nowplaying')
        self.key('KEY_ESC')
        self.assertEqual(self.st['screen'], 'music')

    def test_the_paused_state_shows_on_the_list_row(self):
        self.play_track(0)
        self.key('CHAR: ', 'KEY_ESC', 'KEY_ESC')
        self.assertIn('PAUSED', self.last.split('|')[2])

    def test_other_keys_are_ignored(self):
        self.play_track(0)
        count = len(self.screens)
        self.key('CHAR:x', 'KEY_TAB', 'CHAR:7')
        self.assertEqual(len(self.screens), count)

    def test_a_very_long_title_is_cut_and_the_frame_fits(self):
        self.wav('B' * 80 + '/' + 'Alb ' * 30 + '/01 ' + 'Title ' * 30 + '.wav', 5)
        self.open_music()
        self.st['music_index'] = 0
        rows = kyphone_os._music_rows()
        idx = next(i for i, r in enumerate(rows) if r['kind'] == 'album' and r['album'].artist.startswith('B' * 20))
        self.st['music_index'] = idx
        self.key('KEY_ENTER', 'KEY_ENTER')
        self.assertEqual(self.st['screen'], 'nowplaying')
        self.assertLessEqual(len(self.last), kyphone_os.MAX_COMMAND_CHARS)
        title, artist, album = self.fields()[2:5]
        self.assertLessEqual((len(title), len(artist), len(album)), (kyphone_os.NOW_TITLE_MAX, kyphone_os.NOW_LINE_MAX, kyphone_os.NOW_LINE_MAX))
        self.assertTrue(all(' ' <= c <= '~' for c in self.last if c != SEP))


class Background(MusicCase):
    def setUp(self):
        super().setUp()
        self.library()

    def test_the_home_menu_shows_a_mark_while_music_plays(self):
        self.play_track(0)
        self.key('KEY_ESC', 'KEY_ESC', 'KEY_ESC')
        self.assertEqual(self.st['screen'], 'home')
        kyphone_os.push_home2()
        self.assertTrue(self.last.endswith('|I|1'))

    def test_no_mark_when_paused_or_never_played(self):
        kyphone_os.push_home2()
        self.assertTrue(self.last.endswith('|I|0'))
        self.play_track(0)
        self.key('CHAR: ', 'KEY_ESC', 'KEY_ESC', 'KEY_ESC')
        kyphone_os.push_home2()
        self.assertTrue(self.last.endswith('|I|0'))

    def test_playback_continues_and_advances_while_you_do_other_things_without_redrawing_anything(self):
        self.play_track(0)
        self.key('KEY_ESC', 'KEY_ESC', 'KEY_ESC')
        count = len(self.screens)
        self.tick(25)                                                      # 25 s: into the third 10-second track
        self.assertEqual(len(self.screens), count)                         # the player never redraws other screens
        now = kyphone_os._music.now()
        self.assertEqual((now.track.title, now.state), ('Third', 'playing'))

    def test_when_the_album_ends_on_the_home_menu_the_mark_goes_out(self):
        self.play_track(0)
        self.key('KEY_ESC', 'KEY_ESC', 'KEY_ESC')
        self.tick(31)
        self.assertEqual(kyphone_os._music.now().state, 'stopped')
        self.assertTrue(self.last.startswith('HOME2|') and self.last.endswith('|I|0'))

    def test_when_the_album_ends_on_the_now_playing_screen_it_shows_stopped(self):
        self.play_track(2)
        self.tick(11)
        self.assertEqual(self.fields()[1], 'S')
        self.key('CHAR: ')                                                # replay the last track
        self.assertEqual(self.fields()[1], 'P')

    def test_reading_a_book_is_not_interrupted_when_the_music_changes_track(self):
        self.play_track(0)
        self.st['screen'] = 'reader'
        count = len(self.screens)
        self.tick(12)
        self.assertEqual(len(self.screens), count)


class Ticker(MusicCase):
    def setUp(self):
        super().setUp()
        self.wav('Artist/Long/01 Long.wav', 300)

    def test_now_playing_is_redrawn_every_thirty_seconds_only(self):
        self.play_track(0)
        count = len(self.screens)
        self.tick(29)
        self.assertEqual(len(self.screens), count)
        self.tick(1)
        self.assertEqual(len(self.screens), count + 1)
        self.assertEqual(self.fields()[5], '30')
        self.tick(30)
        self.assertEqual(self.fields()[5], '60')

    def test_no_redraw_while_paused_or_on_another_screen(self):
        self.play_track(0)
        self.key('CHAR: ')
        count = len(self.screens)
        self.tick(65)
        self.assertEqual(len(self.screens), count)
        self.key('CHAR: ')
        self.st['screen'] = 'home'
        count = len(self.screens)
        self.tick(65)
        self.assertEqual(len(self.screens), count)

    def test_ticking_with_no_music_is_harmless(self):
        kyphone_os._music_tick()


class Remembering(MusicCase):
    def setUp(self):
        super().setUp()
        self.library()

    def test_volume_is_saved_and_used_by_the_next_session(self):
        self.play_track(0)
        self.key('KEY_UP', 'KEY_UP')
        self.assertEqual(self.saved()['volume'], music_player.DEFAULT_VOLUME + 10)
        self.reset()                                                      # as after a restart
        self.play_track(0, album=1)                                       # row 0 is RESUME now; Album One is row 1
        self.assertEqual(int(self.fields()[7]), music_player.DEFAULT_VOLUME + 10)

    def test_the_place_is_saved_and_offered_as_resume_after_a_restart(self):
        self.play_track(1)
        self.clock.advance(4)
        self.key('CHAR: ')                                                # pausing saves the position
        self.assertEqual(self.saved()['last']['position'], 4)
        self.reset()
        self.open_music()
        self.assertTrue(self.last.startswith('MUSIC|0|RESUME%sSecond - Artist A%s|Album One' % (SEP, SEP)))
        self.key('KEY_ENTER')
        self.assertEqual(self.st['screen'], 'nowplaying')
        self.assertEqual(self.fields()[1:3], ['U', 'Second'])             # paused, ready
        self.assertEqual(self.fields()[5], '4')
        self.key('CHAR: ')
        self.assertEqual(self.fields()[1], 'P')

    def test_resume_is_not_offered_when_the_file_is_gone(self):
        self.play_track(1)
        self.key('CHAR: ')
        os.remove(os.path.join(self.music, 'Artist A', 'Album One', '02 Second.wav'))
        self.reset()
        self.open_music()
        self.assertNotIn('RESUME', self.last)

    def test_damaged_or_odd_saved_data_gives_defaults(self):
        for content in ('{{{ not json', '[]', '{"volume": 500, "last": "x"}', '{"volume": "loud", "last": {"path": 3}}',
                        '{"volume": -1, "last": {"path": "a", "position": "z"}}'):
            with open(kyphone_os.LISTENING_FILE, 'w') as f:
                f.write(content)
            got = kyphone_os._load_listening()
            self.assertEqual((got['volume'], got['last']), (music_player.DEFAULT_VOLUME, None), content)

    def test_a_missing_saved_file_gives_defaults(self):
        self.assertEqual(kyphone_os._load_listening(), {'volume': music_player.DEFAULT_VOLUME, 'last': None})


class BadFiles(MusicCase):
    def setUp(self):
        super().setUp()
        self.library()
        kyphone_os._music_session()
        self.player = kyphone_os._music.player
        self.player.fail_paths.add(os.path.join(self.music, 'Artist A', 'Album One', '02 Second.wav'))

    def test_a_bad_track_is_skipped_with_an_alert_when_you_are_looking(self):
        self.play_track(0)
        self.key('KEY_RIGHT')                                             # the next track, Second, will not play
        self.assertEqual(self.st['screen'], 'stub')
        self.assertIn('Second', self.st['stub_text'][1])
        self.assertIn('SKIPPED', self.st['stub_text'][1])
        self.key('KEY_ENTER')
        self.assertEqual(self.st['screen'], 'nowplaying')
        self.assertEqual(self.fields()[2], 'Third')                       # already moved on

    def test_a_bad_track_in_the_background_is_skipped_silently(self):
        self.play_track(0)
        self.st['screen'] = 'home'
        count = len(self.screens)
        self.tick(11)                                                      # track one ends; Second is skipped
        self.assertEqual(len(self.screens), count)
        self.assertEqual(kyphone_os._music.now().track.title, 'Third')

    def test_the_alert_text_fits_a_frame(self):
        title, body = kyphone_os.ALERTS['BAD_TRACK']
        wire = 'STUB|%s|%s' % (title, body.format(title='T' * 30, reason='R' * 40))
        self.assertLessEqual(len(wire), kyphone_os.MAX_COMMAND_CHARS)


class NoSoundSystem(MusicCase):
    def test_on_the_phone_without_audio_it_says_so_instead_of_pretending(self):
        self.library()
        with patch.object(kyphone_os, 'SIM_MODE', False), \
                patch.object(kyphone_os.music_player, 'create_player',
                             return_value=(music_player.SimPlayer(), 'NO AUDIO SYSTEM (ImportError)')):
            self.open_album(0)
            self.key('KEY_ENTER')
        self.assertEqual(self.st['screen'], 'stub')
        self.assertIn('NO AUDIO SYSTEM', self.st['stub_text'][1])
        self.assertIsNone(kyphone_os._music)
        self.key('KEY_ENTER')
        self.assertEqual(self.st['screen'], 'tracks')                      # back where you were

    def test_the_alert_text_fits_a_frame(self):
        title, body = kyphone_os.ALERTS['NO_AUDIO']
        self.assertLessEqual(len('STUB|%s|%s' % (title, body.format(reason='R' * 50))), kyphone_os.MAX_COMMAND_CHARS)


if __name__ == '__main__':
    unittest.main()
