"""music_player.py — play a queue of tracks, and everything that is decided about playing them.

Three layers, so nearly all of it can be tested without any audio:

    Session      the queue and the rules: next, previous, seek, volume, what to do at the end of a track or when a
                 file will not play. Pure Python. Talks to a Player.
    Player       "play this file": load / play / pause / seek / volume / position / duration, and it reports the two
                 things that happen on its own: 'eos' (the track finished) and 'error' (it could not be played).
    SimPlayer    a Player that makes no sound and keeps time itself (the emulator and the tests use it).
    GstPlayer    a Player that plays through GStreamer (the phone). GStreamer is imported only when one is made, so
                 nothing else here needs it.

    session = Session(player, volume=40, on_change=..., on_bad_track=...)
    session.play_tracks(album.tracks, 2)        # play from the third track; the queue is the album
    session.next(); session.previous(); session.toggle(); session.seek_by(+15); session.step_volume(-5)
    session.now()                               # a snapshot to draw: track, position, duration, state, volume, n / N

Callbacks are always made with no locks held, on whatever thread caused the change.
"""

import os
import subprocess
import threading
import time

DEFAULT_VOLUME = 40           # percent; low on purpose, so a first play cannot be startlingly loud
VOLUME_STEP = 5
SEEK_STEP = 15                # seconds
RESTART_AFTER = 3             # "previous" restarts the track if it is more than this many seconds in

PLAYING, PAUSED, STOPPED = 'playing', 'paused', 'stopped'


class PlayerError(Exception):
    """A player could not be made or could not load a file."""


# ─── The rules ────────────────────────────────────────────────────────────────

class Now:
    """A snapshot of what is playing, for drawing."""

    def __init__(self, track, index, count, state, position, duration, volume):
        self.track, self.index, self.count = track, index, count
        self.state, self.position, self.duration, self.volume = state, position, duration, volume


class Session:
    def __init__(self, player, volume=DEFAULT_VOLUME, on_change=None, on_bad_track=None):
        self.player = player
        self._lock = threading.RLock()
        self._tracks, self._index, self._state = [], None, STOPPED
        self._volume = max(0, min(100, int(volume)))
        self._bad = set()
        self._on_change, self._on_bad = on_change, on_bad_track
        player.on_event(self._player_event)
        player.set_volume(self._volume)

    # -- reading --
    @property
    def volume(self):
        return self._volume

    @property
    def active(self):
        """Is there a queue to show (playing, paused, or finished)?"""
        with self._lock:
            return self._index is not None

    @property
    def playing(self):
        with self._lock:
            return self._state == PLAYING

    def now(self):
        with self._lock:
            if self._index is None:
                return None
            track = self._tracks[self._index]
            duration = self.player.duration() or track.seconds
            position = 0.0 if self._state == STOPPED else self.player.position()
            if duration:
                position = min(position, duration)
            return Now(track, self._index, len(self._tracks), self._state, position, duration, self._volume)

    # -- doing --
    def play_tracks(self, tracks, index=0, start=0.0):
        """Queue `tracks` and play from `index` (0-based), `start` seconds in."""
        tracks = list(tracks)
        if not tracks:
            return False
        with self._lock:
            self._tracks, self._bad = tracks, set()
            self._index = max(0, min(index, len(tracks) - 1))
        return self._start_current(start, True)

    def toggle(self):
        with self._lock:
            state, index = self._state, self._index
        if index is None:
            return False
        if state == PLAYING:
            self.player.pause()
            self._set_state(PAUSED)
            self._notify('state')
        elif state == PAUSED:
            self.player.play()
            self._set_state(PLAYING)
            self._notify('state')
        else:                                              # finished: play the track again from the start
            self._start_current(0.0, True)
        return True

    def pause(self):
        with self._lock:
            if self._state != PLAYING:
                return False
        self.player.pause()
        self._set_state(PAUSED)
        self._notify('state')
        return True

    def next(self):
        """The next track. False (and nothing changes) at the end of the queue."""
        with self._lock:
            if self._index is None or self._index + 1 >= len(self._tracks):
                return False
            self._index += 1
        return self._start_current(0.0, True)

    def previous(self):
        """More than RESTART_AFTER seconds in (or on the first track): back to the start of this track; otherwise the
        track before."""
        with self._lock:
            if self._index is None:
                return False
            restart = self._index == 0 or self._state == STOPPED or self.player.position() > RESTART_AFTER
            if not restart:
                self._index -= 1
        return self._start_current(0.0, True)

    def seek_by(self, delta):
        with self._lock:
            if self._index is None or self._state == STOPPED:
                return False
            duration = self.player.duration() or self._tracks[self._index].seconds
            target = self.player.position() + delta
            top = (duration - 1) if duration else None
            target = max(0.0, min(target, top) if top is not None and top > 0 else target)
        self.player.seek(target)
        self._notify('seek')
        return True

    def set_volume(self, volume):
        with self._lock:
            self._volume = max(0, min(100, int(volume)))
            volume = self._volume
        self.player.set_volume(volume)
        self._notify('volume')
        return volume

    def step_volume(self, delta):
        return self.set_volume(self._volume + delta)

    def stop(self):
        self.player.stop()
        self._set_state(STOPPED)
        self._notify('state')

    def poll(self):
        """Call now and then: players that keep their own time (SimPlayer) notice the end of a track here."""
        self.player.poll()

    def close(self):
        self.player.close()

    # -- inside --
    def _set_state(self, state):
        with self._lock:
            self._state = state

    def _notify(self, kind):
        if self._on_change:
            self._on_change(kind)

    def _start_current(self, start, play):
        """Load and play the track at self._index. A track that will not load is skipped (see _bad_track).
        True if something ended up playing."""
        with self._lock:
            track = self._tracks[self._index]
        try:
            self.player.load(track.path, start, play)
        except PlayerError as e:
            return self._bad_track(track, str(e))
        self._set_state(PLAYING if play else PAUSED)
        self._notify('track')
        return True

    def _bad_track(self, track, message):
        """The file cannot be played: say so once, then move on to the next track that might play. True if one did."""
        with self._lock:
            self._bad.add(track.path)
        if self._on_bad:
            self._on_bad(track, message)
        while True:
            with self._lock:
                if self._index is None or self._index + 1 >= len(self._tracks):
                    following = None
                else:
                    self._index += 1
                    following = self._tracks[self._index]
            if following is None:                          # nothing left to try
                self.player.stop()
                self._set_state(STOPPED)
                self._notify('finished')
                return False
            try:
                self.player.load(following.path, 0.0, True)
            except PlayerError as e:
                with self._lock:
                    self._bad.add(following.path)
                if self._on_bad:
                    self._on_bad(following, str(e))
                continue
            self._set_state(PLAYING)
            self._notify('track')
            return True

    def _player_event(self, event, message=''):
        """The player reports on its own: a track finished, or could not be played."""
        with self._lock:
            if self._index is None:
                return
            track = self._tracks[self._index]
            last = self._index + 1 >= len(self._tracks)
        if event == 'eos':
            if last:
                self.player.stop()
                self._set_state(STOPPED)
                self._notify('finished')
            else:
                self.next()
        elif event == 'error':
            self._bad_track(track, message or 'COULD NOT PLAY IT')


# ─── SimPlayer: no sound ──────────────────────────────────────────────────────

class SimPlayer:
    """Keeps time like a player would, without any audio. `clock` can be replaced (tests advance it by hand);
    `duration_of(path)` says how long a file is."""

    def __init__(self, clock=time.monotonic, duration_of=None):
        self._clock, self._duration_of = clock, duration_of or (lambda path: None)
        self._path, self._state, self._base, self._t0, self._duration = None, STOPPED, 0.0, None, None
        self._callback, self._volume = None, 0
        self.loaded = []                                   # every path ever loaded, for tests
        self.fail_paths = set()                            # paths that will not "play", for tests

    def on_event(self, callback):
        self._callback = callback

    def load(self, path, start=0.0, play=True):
        if path in self.fail_paths:
            raise PlayerError('THE FILE IS DAMAGED')
        self.loaded.append(path)
        self._path, self._duration = path, self._duration_of(path)
        self._base = float(start)
        self._state = PLAYING if play else PAUSED
        self._t0 = self._clock() if play else None

    def position(self):
        if self._state == PLAYING:
            pos = self._base + (self._clock() - self._t0)
        else:
            pos = self._base
        return min(pos, self._duration) if self._duration else pos

    def duration(self):
        return self._duration

    def play(self):
        if self._state == PAUSED:
            self._t0, self._state = self._clock(), PLAYING

    def pause(self):
        if self._state == PLAYING:
            self._base, self._t0, self._state = self.position(), None, PAUSED

    def stop(self):
        self._base, self._t0, self._state = 0.0, None, STOPPED

    def seek(self, seconds):
        self._base = max(0.0, float(seconds))
        if self._state == PLAYING:
            self._t0 = self._clock()

    def set_volume(self, volume):
        self._volume = volume

    def poll(self):
        """Report 'eos' once the track has played to its end."""
        if self._state == PLAYING and self._duration and self.position() >= self._duration:
            self._base, self._t0, self._state = 0.0, None, STOPPED
            if self._callback:
                self._callback('eos')

    def close(self):
        self.stop()


# ─── GstPlayer: the phone ─────────────────────────────────────────────────────

def audio_config(env=None):
    """How to reach the headphone jack, from the environment (with the Radxa Rock 3A's values as the defaults):
    KYPHONE_AUDIO_DEVICE (ALSA device; 'fake' = a silent output that keeps time), KYPHONE_AUDIO_CARD, KYPHONE_AUDIO_PATH (the codec's Playback Path; '' to leave it
    alone), KYPHONE_AUDIO_CEILING (percent for the codec's Headphone control; '' to leave it alone)."""
    env = os.environ if env is None else env
    return {'device': env.get('KYPHONE_AUDIO_DEVICE', 'plughw:1,0'),
            'card': env.get('KYPHONE_AUDIO_CARD', '1'),
            'path': env.get('KYPHONE_AUDIO_PATH', 'HP'),
            'ceiling': env.get('KYPHONE_AUDIO_CEILING', '')}


def mixer_commands(config):
    """The amixer commands that route sound to the headphone jack (and cap its level), as argument lists."""
    cmds = []
    if config['path']:
        cmds.append(['amixer', '-q', '-c', config['card'], 'sset', 'Playback Path', config['path']])
    if config['ceiling']:
        cmds.append(['amixer', '-q', '-c', config['card'], 'sset', 'Headphone', '%s%%' % config['ceiling']])
    return cmds


class GstPlayer:
    """Plays with a GStreamer playbin to the codec's ALSA device. Needs PyGObject and GStreamer (both are on the
    Radxa). Its GLib main loop runs in a daemon thread so end-of-stream and error messages arrive."""

    def __init__(self, config=None):
        try:
            import gi
            gi.require_version('Gst', '1.0')
            from gi.repository import GLib, Gst
        except (ImportError, ValueError) as e:
            raise PlayerError('NO AUDIO SYSTEM (%s)' % e.__class__.__name__)
        self._Gst, self._GLib = Gst, GLib
        Gst.init(None)
        self._config = config or audio_config()
        for cmd in mixer_commands(self._config):
            try:
                subprocess.run(cmd, check=False, timeout=5, capture_output=True)
            except (OSError, subprocess.SubprocessError):
                pass                                        # best effort: the sound may still work
        self._callback = None
        self._play = Gst.ElementFactory.make('playbin', 'kyphone')
        if self._config['device'] == 'fake':                # silent, but keeps real time: for testing without headphones
            sink = Gst.ElementFactory.make('fakesink', 'out')
            if sink is not None:
                sink.set_property('sync', True)
        else:
            sink = Gst.ElementFactory.make('alsasink', 'out')
            if sink is not None:
                sink.set_property('device', self._config['device'])
        if self._play is None or sink is None:
            raise PlayerError('NO AUDIO SYSTEM (missing GStreamer parts)')
        self._play.set_property('audio-sink', sink)
        self._play.set_property('flags', 0x2)               # audio only
        bus = self._play.get_bus()
        bus.add_signal_watch()
        bus.connect('message::eos', self._on_eos)
        bus.connect('message::error', self._on_error)
        self._loop = GLib.MainLoop()
        threading.Thread(target=self._loop.run, daemon=True).start()
        self._volume = 0

    def on_event(self, callback):
        self._callback = callback

    def _fire(self, *args):
        if self._callback:
            self._callback(*args)

    def _on_eos(self, _bus, _msg):
        self._play.set_state(self._Gst.State.NULL)
        self._fire('eos')

    def _on_error(self, _bus, msg):
        err, _debug = msg.parse_error()
        self._play.set_state(self._Gst.State.NULL)
        self._fire('error', str(err.message).upper()[:60])

    def _wait(self, timeout_s=3):
        self._play.get_state(int(timeout_s * self._Gst.SECOND))

    def load(self, path, start=0.0, play=True):
        Gst = self._Gst
        if not os.path.exists(path):
            raise PlayerError('THE FILE IS MISSING')
        self._play.set_state(Gst.State.NULL)
        self._play.set_property('uri', Gst.filename_to_uri(os.path.abspath(path)))
        self._play.set_state(Gst.State.PAUSED)
        self._wait()
        if start > 0:
            self._play.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, int(start * Gst.SECOND))
        self._play.set_state(Gst.State.PLAYING if play else Gst.State.PAUSED)

    def position(self):
        ok, ns = self._play.query_position(self._Gst.Format.TIME)
        return ns / self._Gst.SECOND if ok else 0.0

    def duration(self):
        ok, ns = self._play.query_duration(self._Gst.Format.TIME)
        return ns / self._Gst.SECOND if ok and ns > 0 else None

    def play(self):
        self._play.set_state(self._Gst.State.PLAYING)

    def pause(self):
        self._play.set_state(self._Gst.State.PAUSED)

    def stop(self):
        self._play.set_state(self._Gst.State.NULL)

    def seek(self, seconds):
        Gst = self._Gst
        self._play.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, int(max(0.0, seconds) * Gst.SECOND))

    def set_volume(self, volume):
        self._volume = volume
        self._play.set_property('volume', (volume / 100.0) ** 2)      # a squared curve: the low end is gentle

    def poll(self):
        pass

    def close(self):
        self._play.set_state(self._Gst.State.NULL)
        self._loop.quit()


def create_player(sim=False, clock=time.monotonic, duration_of=None):
    """The phone's player, or (sim=True, or when GStreamer cannot be used) a silent one. Returns (player, problem):
    problem is None, or a short reason the real player is unavailable."""
    if sim:
        return SimPlayer(clock, duration_of), None
    try:
        return GstPlayer(), None
    except PlayerError as e:
        return SimPlayer(clock, duration_of), str(e)
