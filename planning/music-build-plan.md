# KyPhone LISTEN — build plan (music files on the Radxa, controlled from the Inkplate)


## Status (branch `music-build`, local only — nothing pushed, flashed or deployed)
| Step | What | State |
|---|---|---|
| 1 | Music library: scan `data/music`, read tags and lengths from MP3 / FLAC / Ogg / Opus / M4A / WAV (`music_library.py`, 53 tests incl. damaged files and a 5,000-track scan) | done |
| 2 | Player: `Session` (queue and rules), `SimPlayer`, `GstPlayer` (`music_player.py`, 42 tests; the real player also ran on the Radxa against a silent output) | done |
| 3 | State machine: music / tracks / nowplaying screens, keys, background playback, ticker, saved volume and resume, alerts (`kyphone_os.py`, `test_music_state.py` 41 tests) | done |
| 4 | Emulator renderers (MUSIC / TRACKS / NOWPLAYING, the home mark; 17 pixel tests; real-key click-through of a whole album) | done |
| 5 | Firmware renderers + host harness | next |
| 6 | Device: headphones, format matrix, soak (needs Kyle at the phone) | waiting |
| 7 | Docs | |

**Checked on the Radxa 2026-09-19 (silent, nothing changed):** every decoder the plan worried about is there — `mpg123audiodec` (MP3), `flacdec`, `vorbisdec`, `opusdec`, `avdec_aac`/`faad`, `avdec_alac`, `wavparse`, plus the Ogg, QuickTime and ID3 demuxers (my first plugin listing was truncated, which is why FLAC and Vorbis looked missing). `GstPlayer` was run from a temp folder with `KYPHONE_AUDIO_DEVICE=fake` (a clocked fakesink): position follows real time, pause holds it, seek works and clamps, a text file posing as an MP3 is reported ("THIS APPEARS TO BE A TEXT FILE") and skipped, the queue continues and finishes. Still to be heard on real headphones: step 6. Lesson: ALSA's `null` device is not paced, so it "plays" everything instantly; `fake` is the silent option that keeps time.

Deviations from the plan as built: **Up/Down change the volume** (so the trackpad, which only has arrows and a click, can do everything: swipe left/right = previous/next, click = play/pause, up/down = volume) and **`,` / `.` seek 15 s** back/forward (the plan had Up/Down seek); `+`/`-` still change volume. A bad track only raises its alert if the now-playing screen is showing (a skip in the background is silent, so music never interrupts a book or a text). The player is never a silent stand-in on the phone: if GStreamer is missing, choosing a track shows a NO_AUDIO alert. RESUME (after a restart) is the first row of the album list when the last track is still on the phone; `python3-mutagen` is **not** used at all (the stdlib readers cover MP3, FLAC, Ogg/Opus, M4A/AAC and WAV; anything else is described by its file name), which keeps the phone free of new dependencies; albums with no album-artist tag are grouped by album name **and folder** (two artists can each have a "Greatest Hits"; "Disc 1"/"CD 2" subfolders join their parent album).

## Context
LISTEN (the music row on the home menu) is a stop alert today. Kyle wants a music player. Decisions made with Kyle:
**music files copied onto the phone** (like books; no streaming, no accounts, free), **headphone jack first, Bluetooth headphones as
a second phase**, and **music keeps playing when you leave the screen** (a small now-playing mark on the home menu).

Same working method as the reader: Python + emulator first, then the host-side firmware harness, then the phone; no push/flash/
deploy without an explicit go-ahead; back up both devices first; nothing costs money.

## Facts (checked read-only on the Radxa, 2026-09-19)
- **Sound out:** two ALSA cards — card 0 = HDMI, card 1 = `rockchip-rk809` codec with `Headphone` and `Speaker` mixer controls and a
  `Playback Path` switch (OFF / RCV / SPK / HP / …). So the headphone jack is reachable today by setting the path to HP and playing to
  `plughw:1,0`. (Untested with real headphones: that is a phone-session check.)
- **Player engine already installed:** GStreamer 1.18.5 **and its Python bindings** (`import gi; Gst` works) plus decoders for MP3
  (mpg123), AAC/ALAC/WMA (libav), Opus and WAV. I did not see FLAC or Vorbis decoders in the truncated listing — step 2 verifies each
  format on the device and the plan does not promise a format until it has played. **No new packages are needed for wired playback**
  (mpv would pull 16 packages; not needed).
- **Not present:** PulseAudio/PipeWire (not running), `python3-mutagen`. Both are one `apt` command away (internet works; mutagen 1.45,
  pulseaudio 14.2 + `pulseaudio-module-bluetooth` are in Debian 11) — only mutagen is even optional for phase 1.
- **Bluetooth:** BlueZ is active, the controller is powered, and the Q10 keyboard is paired through it, so the radio works. Audio
  needs PulseAudio's Bluetooth module (phase 2). `kyphone.service` runs as **root**, which is fine for ALSA but awkward for PulseAudio
  (system mode or a dedicated user) — the main phase-2 risk.
- Debian 11, Python 3.9, 4 cores, 7.7 GB RAM, 48 GB free disk.
- Code patterns to reuse: `_scan_books` / `_load_reading` / `_save_reading` (library scan + tiny JSON store), `window_start` and
  `_list_command` (windowed lists that fit one 253-char frame), `_show_alert` + `ALERTS` (stop alerts), the LIBRARY renderer in
  `simulator.py` (`_draw_library`) and `ui_screens.h` (`ui_library`), `clock_loop` / `call_timer_loop` (background ticker threads),
  `KYPHONE_DATA_DIR`, the generator/`--check` pattern, and the reader's test scaffolding (`epub_fixtures.py`, `test_reader_state.py`).

## Architecture
Radxa does everything smart; the Inkplate stays a dumb renderer. The player runs **inside `kyphone_os.py`'s process** (a GStreamer
`playbin` driven through `gi`, its GLib loop in a daemon thread), so there is no second process to supervise.

### New modules (hardware-free where possible)
| File | Job |
|---|---|
| `spi_bridge/music_library.py` | Scan `data/music/**` (any depth) for `.mp3 .m4a .aac .flac .ogg .opus .wav` (the list is trimmed to what step 2 proves plays). Tags: `mutagen` if installed, else a small stdlib reader for ID3v2/ID3v1 (MP3) and Vorbis comments (FLAC/Ogg/Opus), else the file name. Album = album tag (fallback: parent folder), artist fallback "Unknown". Drawable-ASCII text (reuse `reader_epub.to_drawable`). Index cached in `data/music_index.json` keyed by (path, size, mtime) so a large library is not re-read every time LISTEN opens. Returns albums → ordered tracks. |
| `spi_bridge/music_player.py` | `Queue` (pure Python: current index, next / previous, "previous restarts the track if more than 3 s in", end-of-queue, skip-on-error) — fully unit-tested with no audio. `GstPlayer` (thin `playbin` wrapper: load, play, pause, seek, volume, position/duration; callbacks for state, end-of-stream, error; sink `alsasink device=plughw:1,0`, startup sets the codec's `Playback Path` to HP; both overridable by env vars). `SimPlayer` (no sound: advances position in real time, for the emulator and tests). `gi` is imported lazily so the Mac and the tests never need it. |
| `spi_bridge/tests/audio_fixtures.py` | Writes small WAV files with the standard `wave` module, and hand-built ID3v2 / Vorbis-comment byte strings, for tests and for device checks (a 5-second tone). |

### State machine (kyphone_os.py)
- **Home:** LISTEN opens `music`; the `LISTEN` stop alert is removed. `HOME2` gains a sixth field `playing` (`0/1`) and the LISTEN
  row shows a small equalizer mark while music plays. (Wire change ⇒ firmware and Python ship together, as with the reorder.)
- **`music`** — album list, windowed 5 rows (reuses the LIBRARY row layout: title bold / artist small / track count on the right).
  If something is loaded, the first row is `NOW PLAYING` and opens the now-playing screen. Empty state: NO MUSIC + "COPY MUSIC FILES INTO
  THE MUSIC FOLDER ON THE PHONE". Esc → home. Music keeps playing.
- **`tracks`** — one album's tracks, windowed; Enter plays the album from that track (queue = the album, in order); Esc → albums.
- **`nowplaying`** — title, artist, album, `elapsed / total`, a thin progress bar, play/pause state, volume, `n / N`. Keys:
  **Space / Enter** play-pause · **Right** next · **Left** previous · **Up / Down** seek ±15 s · **`+` / `-`** volume (5% steps) ·
  **Esc / `q`** back (music keeps playing). The volume/seek limits redraw the screen (visible feedback) instead of raising a full alert.
- **Errors** (stop alerts): `BAD_TRACK` (a file will not play: it is skipped, the alert says which), `NO_AUDIO` (the sound device will
  not open).
- **Background:** `music_tick_loop` (like `clock_loop`) redraws now-playing every 30 s while that screen is showing and music plays, so
  elapsed time moves without hammering the e-ink; track changes, play/pause, seek and volume redraw at once. Other screens are never
  redrawn by the player (no surprise refreshes while you read or text).
- **Persistence:** `data/listening.json` = `{volume, last: {path, position}}`; the first start after boot is paused, ready to resume.
  **Default volume 40%, capped by an ALSA ceiling**, so a first play cannot be startlingly loud.

### Wire protocol (additions; also written into CLAUDE.md when built)
| Command | Meaning |
|---|---|
| `MUSIC\|sel\|title·subtitle·right\|…` | Album list (≤5 rows, sel −1 back). Same shape as `LIBRARY`; first row may be `NOW PLAYING`. |
| `TRACKS\|sel\|album\|title·artist·time\|…` | An album's tracks (≤5 rows). |
| `NOWPLAYING\|state\|title\|artist\|album\|elapsed\|total\|vol\|pos` | `state` `P` playing / `U` paused; times in seconds; `pos` = `n/N`. One frame. |
| `HOME2\|time\|index\|unread\|style\|playing` | new sixth field. |
Standard clear-and-refresh path: **no new firmware protocol** (unlike the reader, a now-playing screen fits one frame).

### Firmware + emulator
- `ui_screens.h`: generalise `ui_library` into a shared two-line-list renderer used by `LIBRARY`, `MUSIC`, `TRACKS` (header title differs);
  add `ui_nowplaying` and the playing mark on the home LISTEN row. `simulator.py` mirrors both (same ports-by-hand convention, kept
  honest by the existing rule/inverted-row parity test plus new pixel tests).
- **Design:** the now-playing layout is proposed by us (text + one bar, built from existing pieces), not from a designer handoff. If Kyle
  wants it designed properly, a short Claude Design brief can be written first.

## Build steps (each: tests first, commit locally, report)
1. **`music_library.py`** + tests (synthetic ID3/Vorbis bytes, folder scans, ties/duplicates, ASCII + `|`/`·` safety, index cache reuse
   and invalidation, a 5,000-track scan speed check).
2. **`music_player.py`**: `Queue` + `SimPlayer` + `GstPlayer`; tests for all queue rules and the player callbacks; a `GstPlayer` test that
   skips when `gi` is missing. (Format verification on the device happens in step 6.)
3. **State machine**: screens, keys, alerts, ticker, persistence, `HOME2` playing field; tests with `SimPlayer` (turn/seek/volume,
   background playback across screens, end of album, bad track, empty library, resume).
4. **Emulator**: `MUSIC` / `TRACKS` / `NOWPLAYING` / playing mark, pixel tests, real-key click-through with `SimPlayer` and generated audio.
5. **Firmware**: renderers in `ui_screens.h`, host-harness parity (firmware = emulator), sanitizer fuzz, `arduino-cli` compile check.
6. **Device (needs Kyle at the phone; not before you say go):** back up both; flash + deploy together; check `Playback Path` → HP and
   volume safety with headphones; generated tone, then a real album; **prove each format plays (MP3, AAC/M4A, FLAC, Ogg, Opus, WAV)**
   and trim the supported list to what does; check CPU while playing, key latency, no audio glitches during e-ink refreshes and SPI
   sends, and that the tick redraws do not ghost.
7. **Docs**: CLAUDE.md (screens, keys, wire, gotchas), README, a `planning/music-build-plan.md`, an addendum to the phone checklist.

Steps 1–5 need no hardware and can run while Kyle is away.

## Phase 2 (planned, not started)
- **Bluetooth headphones:** `apt install pulseaudio pulseaudio-module-bluetooth` (free); decide system-mode PulseAudio vs running the
  phone software as a non-root user; pair via `bluetoothctl`; switch the sink to `pulsesink`; a LISTEN setting to pick the output.
  Its own plan and checklist, because it changes how the service runs.
- Later ideas, only if wanted: audiobooks/podcasts (a resume position per file), shuffle/repeat, playlists, 1-bit album art.

## Critical files
`spi_bridge/kyphone_os.py` (`HOME_MENU`/LISTEN stub, `push_home2`, `main()` thread startup ~L2431, `clock_loop` ~L2338, `ALERTS`),
`spi_bridge/simulator.py` (`_draw_library`, `_draw_home2`), `Inkplate_SPI_Peripheral/ui_screens.h` (`ui_library`, `ui_home`,
`ui_dispatch`), `spi_bridge/tests/firmware_host/screens.py`, plus the new files above.

## Verification
- Unit: `test_music_library.py`, `test_music_player.py`, `test_music_state.py`; all existing suites still pass (423 today).
- Emulator: `KYPHONE_DATA_DIR=$(mktemp -d)` with generated WAVs in `music/`: browse, play, pause, seek, volume, next/previous, leave and
  come back while playing, end of album, delete a file mid-play.
- Firmware host harness: pixel parity with the emulator for every new screen; fuzz; compile for the Inkplate 4 TEMPERA.
- Device (with Kyle): headphones plugged in, the format matrix, a 30-minute soak (no glitches, no runaway refreshes, CPU/temperature).

## Risks / open items
- **Formats:** FLAC and Ogg Vorbis decoders were not seen in the plugin list; step 6 decides what is supported (installing
  `gstreamer1.0-plugins-good`/`-ugly` is a free apt fallback).
- **E-ink and time:** elapsed time updates every 30 s, not every second; a refresh takes 1–2 s. That is a deliberate fit to e-ink.
- **Loudness:** first play is capped low; the ALSA path/ceiling must be set on the device, not assumed.
- **Root + audio:** fine for ALSA; the phase-2 Bluetooth work is where it bites.
- **Copyright/DRM:** local files only; DRM-protected purchases (e.g. some `.m4p`) will not play.
- **CPU/SPI contention while decoding** is expected to be negligible on 4 cores but is measured in step 6.
