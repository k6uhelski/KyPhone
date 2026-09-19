"""
test_music_player.py — the playback rules and the players (spi_bridge/music_player.py).

No audio and no GStreamer: the Session is driven through a fake player and through SimPlayer with a clock the test
advances by hand. GstPlayer's own behaviour is checked on the phone; here we check it fails politely without GStreamer.

    python3 -m pytest spi_bridge/tests/test_music_player.py -v
"""

import os
import sys
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
import music_player as mp  # noqa: E402


class T:
    """A track, as far as the player cares."""

    def __init__(self, name, seconds=100):
        self.path, self.seconds, self.title = '/music/%s.mp3' % name, seconds, name


class FakePlayer:
    """A player the test controls: nothing plays, it records what it is asked and reports what the test says."""

    def __init__(self):
        self.calls, self.pos, self.dur, self.callback, self.fail_paths, self.state = [], 0.0, None, None, set(), 'stopped'

    def on_event(self, cb):
        self.callback = cb

    def load(self, path, start=0.0, play=True):
        if path in self.fail_paths:
            raise mp.PlayerError('NO GOOD')
        self.calls.append(('load', path, start, play))
        self.pos, self.state = start, 'playing' if play else 'paused'

    def play(self):
        self.calls.append(('play',)); self.state = 'playing'

    def pause(self):
        self.calls.append(('pause',)); self.state = 'paused'

    def stop(self):
        self.calls.append(('stop',)); self.state = 'stopped'; self.pos = 0.0

    def seek(self, s):
        self.calls.append(('seek', s)); self.pos = s

    def set_volume(self, v):
        self.calls.append(('volume', v))

    def position(self):
        return self.pos

    def duration(self):
        return self.dur

    def poll(self):
        pass

    def close(self):
        self.calls.append(('close',))

    def emit(self, event, message=''):
        self.callback(event, message) if message else self.callback(event)

    def loaded(self):
        return [c[1] for c in self.calls if c[0] == 'load']


class SessionCase(unittest.TestCase):
    def make(self, n=4, **kw):
        self.player = FakePlayer()
        self.events, self.bad = [], []
        self.session = mp.Session(self.player, on_change=self.events.append, on_bad_track=lambda t, m: self.bad.append((t.title, m)), **kw)
        self.tracks = [T('t%d' % i) for i in range(n)]
        return self.session


class Playing(SessionCase):
    def test_a_new_session_has_nothing_to_show(self):
        s = self.make()
        self.assertIsNone(s.now())
        self.assertFalse(s.active)
        self.assertFalse(s.playing)

    def test_play_tracks_starts_at_the_chosen_track(self):
        s = self.make()
        self.assertTrue(s.play_tracks(self.tracks, 2))
        self.assertEqual(self.player.loaded(), ['/music/t2.mp3'])
        now = s.now()
        self.assertEqual((now.track.title, now.index, now.count, now.state), ('t2', 2, 4, 'playing'))
        self.assertTrue(s.playing and s.active)
        self.assertEqual(self.events, ['track'])

    def test_an_empty_queue_or_a_wild_index_is_handled(self):
        s = self.make()
        self.assertFalse(s.play_tracks([], 0))
        self.assertTrue(s.play_tracks(self.tracks, 99))
        self.assertEqual(s.now().index, 3)
        self.assertTrue(s.play_tracks(self.tracks, -5))
        self.assertEqual(s.now().index, 0)

    def test_toggle_pauses_resumes_and_replays_after_the_end(self):
        s = self.make()
        s.play_tracks(self.tracks, 0)
        s.toggle()
        self.assertEqual((s.now().state, self.player.state), ('paused', 'paused'))
        s.toggle()
        self.assertEqual((s.now().state, self.player.state), ('playing', 'playing'))
        self.player.emit('eos')                                             # ... to the end of the last track
        s.play_tracks(self.tracks, 3)
        self.player.emit('eos')
        self.assertEqual(s.now().state, 'stopped')
        s.toggle()                                                          # play the finished track again
        self.assertEqual((s.now().state, s.now().index), ('playing', 3))
        self.assertEqual(self.player.loaded()[-1], '/music/t3.mp3')

    def test_toggle_with_nothing_queued_does_nothing(self):
        self.assertFalse(self.make().toggle())

    def test_pause_only_when_playing(self):
        s = self.make()
        s.play_tracks(self.tracks)
        self.assertTrue(s.pause())
        self.assertFalse(s.pause())


class Navigating(SessionCase):
    def test_next_walks_the_queue_and_stops_at_the_end_without_error(self):
        s = self.make(3)
        s.play_tracks(self.tracks, 0)
        self.assertTrue(s.next())
        self.assertTrue(s.next())
        self.assertEqual(s.now().index, 2)
        self.assertFalse(s.next())                                          # nothing after the last track
        self.assertEqual((s.now().index, s.now().state), (2, 'playing'))

    def test_previous_goes_back_when_near_the_start_and_restarts_when_further_in(self):
        s = self.make()
        s.play_tracks(self.tracks, 2)
        self.player.pos = mp.RESTART_AFTER + 1
        s.previous()
        self.assertEqual((s.now().index, self.player.pos), (2, 0.0))        # restarted, still track 2
        self.player.pos = 1.0
        s.previous()
        self.assertEqual(s.now().index, 1)

    def test_previous_on_the_first_track_restarts_it(self):
        s = self.make()
        s.play_tracks(self.tracks, 0)
        self.player.pos = 1.0
        self.assertTrue(s.previous())
        self.assertEqual((s.now().index, self.player.loaded()[-1]), (0, '/music/t0.mp3'))

    def test_previous_after_the_end_restarts_the_last_track(self):
        s = self.make(2)
        s.play_tracks(self.tracks, 1)
        self.player.emit('eos')
        s.previous()
        self.assertEqual((s.now().index, s.now().state), (1, 'playing'))

    def test_nothing_queued_next_and_previous_do_nothing(self):
        s = self.make()
        self.assertFalse(s.next())
        self.assertFalse(s.previous())

    def test_seek_by_moves_relative_and_stays_inside_the_track(self):
        s = self.make()
        s.play_tracks(self.tracks, 0)
        self.player.dur, self.player.pos = 100.0, 50.0
        s.seek_by(15)
        self.assertEqual(self.player.pos, 65.0)
        s.seek_by(-500)
        self.assertEqual(self.player.pos, 0.0)
        s.seek_by(500)
        self.assertEqual(self.player.pos, 99.0)                             # one second short of the end

    def test_seek_without_a_known_length_still_works(self):
        s = self.make()
        s.play_tracks([T('x', None)], 0)
        self.player.pos = 10.0
        s.seek_by(15)
        self.assertEqual(self.player.pos, 25.0)

    def test_seek_when_stopped_or_empty_is_refused(self):
        s = self.make()
        self.assertFalse(s.seek_by(15))
        s.play_tracks(self.tracks, 3)
        self.player.emit('eos')
        self.assertFalse(s.seek_by(15))


class Endings(SessionCase):
    def test_end_of_track_plays_the_next_one(self):
        s = self.make()
        s.play_tracks(self.tracks, 0)
        self.player.emit('eos')
        self.assertEqual((s.now().index, self.player.loaded()[-1]), (1, '/music/t1.mp3'))

    def test_end_of_the_last_track_stops_and_says_finished(self):
        s = self.make(2)
        s.play_tracks(self.tracks, 1)
        self.player.emit('eos')
        self.assertEqual((s.now().state, s.now().index), ('stopped', 1))
        self.assertEqual(s.now().position, 0.0)
        self.assertEqual(self.events[-1], 'finished')
        self.assertTrue(s.active)                                           # still there to show, and to replay

    def test_a_bad_track_is_skipped_and_reported_once(self):
        s = self.make()
        s.play_tracks(self.tracks, 0)
        self.player.emit('error', 'NOT SUPPORTED')
        self.assertEqual(self.bad, [('t0', 'NOT SUPPORTED')])
        self.assertEqual((s.now().index, s.now().state), (1, 'playing'))

    def test_a_file_that_will_not_load_is_skipped_at_the_start(self):
        s = self.make()
        self.player.fail_paths = {'/music/t0.mp3'}
        self.assertTrue(s.play_tracks(self.tracks, 0))
        self.assertEqual((s.now().index, [b[0] for b in self.bad]), (1, ['t0']))

    def test_several_bad_tracks_in_a_row_are_all_skipped(self):
        s = self.make(5)
        self.player.fail_paths = {'/music/t1.mp3', '/music/t2.mp3'}
        s.play_tracks(self.tracks, 0)
        self.player.emit('eos')
        self.assertEqual(s.now().index, 3)
        self.assertEqual([b[0] for b in self.bad], ['t1', 't2'])

    def test_when_every_remaining_track_is_bad_playback_stops(self):
        s = self.make(3)
        self.player.fail_paths = {'/music/t1.mp3', '/music/t2.mp3'}
        s.play_tracks(self.tracks, 0)
        self.player.emit('eos')
        self.assertEqual((s.now().state, [b[0] for b in self.bad]), ('stopped', ['t1', 't2']))
        self.assertEqual(self.events[-1], 'finished')

    def test_an_error_on_the_last_track_stops(self):
        s = self.make(2)
        s.play_tracks(self.tracks, 1)
        self.player.emit('error', 'BROKEN')
        self.assertEqual((s.now().state, self.bad), ('stopped', [('t1', 'BROKEN')]))

    def test_a_player_event_with_nothing_queued_is_ignored(self):
        s = self.make()
        self.player.emit('eos')
        self.player.emit('error', 'x')
        self.assertEqual((self.events, self.bad), ([], []))


class Volume(SessionCase):
    def test_the_starting_volume_goes_to_the_player_and_is_clamped(self):
        self.make(volume=250)
        self.assertEqual(self.player.calls[0], ('volume', 100))
        self.make(volume=-3)
        self.assertEqual(self.player.calls[0], ('volume', 0))

    def test_the_default_is_low(self):
        s = self.make()
        self.assertEqual(s.volume, mp.DEFAULT_VOLUME)
        self.assertLessEqual(mp.DEFAULT_VOLUME, 50)

    def test_steps_move_by_five_and_clamp_at_both_ends(self):
        s = self.make(volume=95)
        self.assertEqual([s.step_volume(mp.VOLUME_STEP) for _ in range(3)], [100, 100, 100])
        s.set_volume(5)
        self.assertEqual([s.step_volume(-mp.VOLUME_STEP) for _ in range(3)], [0, 0, 0])
        self.assertEqual(self.player.calls[-1], ('volume', 0))
        self.assertEqual(self.events[-1], 'volume')                         # a redraw even at the limit

    def test_volume_survives_a_new_queue(self):
        s = self.make(volume=70)
        s.play_tracks(self.tracks, 0)
        self.assertEqual(s.now().volume, 70)


class Callbacks(SessionCase):
    def test_callbacks_run_with_no_lock_held_so_they_may_call_back_in(self):
        s = self.make()
        seen = []
        s._on_change = lambda kind: seen.append((kind, s.now() and s.now().index, threading.current_thread().name))
        done = threading.Event()

        def run():
            s.play_tracks(self.tracks, 1)
            s.next()
            done.set()
        t = threading.Thread(target=run, name='worker')
        t.start()
        self.assertTrue(done.wait(5))                                       # would hang if callbacks held the lock
        self.assertEqual(seen, [('track', 1, 'worker'), ('track', 2, 'worker')])

    def test_the_session_survives_events_from_another_thread(self):
        s = self.make(50)
        s.play_tracks(self.tracks, 0)
        stop = threading.Event()

        def hammer():
            while not stop.is_set():
                s.now(); s.seek_by(1); s.step_volume(1); s.step_volume(-1)
        t = threading.Thread(target=hammer)
        t.start()
        for _ in range(40):
            self.player.emit('eos')
        stop.set()
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertEqual(s.now().index, 40)


# ─── SimPlayer ────────────────────────────────────────────────────────────────

class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


class SimPlayerTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.events = []
        self.p = mp.SimPlayer(self.clock, duration_of=lambda path: 30.0)
        self.p.on_event(lambda *a: self.events.append(a))

    def test_position_advances_while_playing_and_freezes_when_paused(self):
        self.p.load('/a.mp3')
        self.clock.advance(5)
        self.assertAlmostEqual(self.p.position(), 5.0)
        self.p.pause()
        self.clock.advance(10)
        self.assertAlmostEqual(self.p.position(), 5.0)
        self.p.play()
        self.clock.advance(2)
        self.assertAlmostEqual(self.p.position(), 7.0)

    def test_starting_offset_seek_and_duration(self):
        self.p.load('/a.mp3', start=12.0)
        self.assertAlmostEqual(self.p.position(), 12.0)
        self.p.seek(3)
        self.clock.advance(4)
        self.assertAlmostEqual(self.p.position(), 7.0)
        self.assertEqual(self.p.duration(), 30.0)

    def test_loading_paused_does_not_advance(self):
        self.p.load('/a.mp3', 0.0, play=False)
        self.clock.advance(9)
        self.assertEqual(self.p.position(), 0.0)

    def test_the_end_of_the_track_is_reported_once_on_poll(self):
        self.p.load('/a.mp3')
        self.clock.advance(29)
        self.p.poll()
        self.assertEqual(self.events, [])
        self.clock.advance(2)
        self.p.poll()
        self.p.poll()
        self.assertEqual(self.events, [('eos',)])
        self.assertEqual(self.p.position(), 0.0)

    def test_position_never_passes_the_duration(self):
        self.p.load('/a.mp3')
        self.clock.advance(500)
        self.assertEqual(self.p.position(), 30.0)

    def test_failing_paths_raise_like_a_real_player(self):
        self.p.fail_paths.add('/bad.mp3')
        with self.assertRaises(mp.PlayerError):
            self.p.load('/bad.mp3')

    def test_stop_resets(self):
        self.p.load('/a.mp3')
        self.clock.advance(3)
        self.p.stop()
        self.assertEqual(self.p.position(), 0.0)


class SessionWithSimPlayer(unittest.TestCase):
    def test_an_album_plays_through_in_time(self):
        clock = Clock()
        tracks = [T('a', 10), T('b', 10), T('c', 10)]
        secs = {t.path: t.seconds for t in tracks}
        player = mp.SimPlayer(clock, duration_of=secs.get)
        events = []
        s = mp.Session(player, on_change=events.append)
        s.play_tracks(tracks, 0)
        for _ in range(25):                                                  # 25 seconds, polled every second
            clock.advance(1)
            s.poll()
        now = s.now()
        self.assertEqual((now.index, now.state), (2, 'playing'))
        self.assertAlmostEqual(now.position, 5.0, delta=1.01)
        for _ in range(10):
            clock.advance(1)
            s.poll()
        self.assertEqual((s.now().state, events[-1]), ('stopped', 'finished'))

    def test_now_falls_back_to_the_library_length_when_the_player_does_not_know(self):
        player = mp.SimPlayer(Clock())
        s = mp.Session(player)
        s.play_tracks([T('a', 200)], 0)
        self.assertEqual(s.now().duration, 200)


# ─── The phone's player ───────────────────────────────────────────────────────

class PhonePlayer(unittest.TestCase):
    def test_the_defaults_are_the_radxa_rock_3a_headphone_jack(self):
        self.assertEqual(mp.audio_config({}), {'device': 'plughw:1,0', 'card': '1', 'path': 'HP', 'ceiling': ''})

    def test_the_environment_overrides_them(self):
        cfg = mp.audio_config({'KYPHONE_AUDIO_DEVICE': 'hw:0,0', 'KYPHONE_AUDIO_CARD': '0', 'KYPHONE_AUDIO_PATH': 'SPK',
                               'KYPHONE_AUDIO_CEILING': '60'})
        self.assertEqual((cfg['device'], cfg['card'], cfg['path'], cfg['ceiling']), ('hw:0,0', '0', 'SPK', '60'))

    def test_mixer_commands_route_to_the_jack_and_optionally_cap_it(self):
        self.assertEqual(mp.mixer_commands(mp.audio_config({})), [['amixer', '-q', '-c', '1', 'sset', 'Playback Path', 'HP']])
        self.assertEqual(mp.mixer_commands(mp.audio_config({'KYPHONE_AUDIO_PATH': '', 'KYPHONE_AUDIO_CEILING': '60'})),
                         [['amixer', '-q', '-c', '1', 'sset', 'Headphone', '60%']])
        self.assertEqual(mp.mixer_commands(mp.audio_config({'KYPHONE_AUDIO_PATH': ''})), [])

    def test_without_gstreamer_the_phone_player_says_so_instead_of_crashing(self):
        try:
            import gi  # noqa: F401
            self.skipTest('GStreamer bindings are installed here: the real player is checked on the phone')
        except ImportError:
            pass
        with self.assertRaises(mp.PlayerError) as cm:
            mp.GstPlayer()
        self.assertIn('NO AUDIO SYSTEM', str(cm.exception))

    def test_create_player_gives_a_silent_player_in_the_emulator_or_when_audio_is_missing(self):
        player, problem = mp.create_player(sim=True)
        self.assertIsInstance(player, mp.SimPlayer)
        self.assertIsNone(problem)
        try:
            import gi  # noqa: F401
            self.skipTest('GStreamer is installed here')
        except ImportError:
            player, problem = mp.create_player(sim=False)
            self.assertIsInstance(player, mp.SimPlayer)
            self.assertIn('NO AUDIO SYSTEM', problem)


if __name__ == '__main__':
    unittest.main()
