# Phone session checklist — flash the reader, music and icons, deploy, click through

For when you are at the phone. About 45–60 minutes, most of it waiting. Nothing in here has been run yet: it is a plan
written from what is on the Mac and the Radxa today (checked 2026-09-19).

**What ships:** the icon home menu (built earlier, never flashed), the reader (library, page turning, four font
sizes) and the music player (LISTEN). All are on the local branch `music-build`. The firmware and the Python must go together.

**Why this order:** flash first, then deploy the Python, and **do those two back to back**. The home menu order changed
on 2026-09-19 (now TEXT, CALL, READ, LISTEN, CONTACTS), and the firmware and the Radxa's Python each hold a copy of it.
Between the flash and the deploy the icons follow the new order while the old Python still acts on the old one (so the
third row shows a book icon but Enter opens Contacts). That is harmless and ends the moment step 4 is done, but it means
step 2's "press a key" check below is only a look at the icons, not a real test. Nothing else is order-sensitive.

## You need
- The Mac mini with the Inkplate on USB (port `/dev/cu.usbserial-1140`) and `ssh radxa` working.
- The Bluetooth keyboard, charged. (The trackpad works too: swipes turn pages, a click is Enter.)
- **Headphones or a speaker for the 3.5 mm jack** (music). Keep them OFF your ears for the first play; the default volume is 40 %.
- One or two `.epub` files. Free ones from Project Gutenberg:
  ```
  curl -L -o ~/alice.epub          https://www.gutenberg.org/ebooks/11.epub.noimages
  curl -L -o ~/monte-cristo.epub   https://www.gutenberg.org/ebooks/1184.epub.noimages
  ```

## 0. Check the branch (1 min)
```
cd ~/kyphone && git branch --show-current          # music-build
KYPHONE_DATA_DIR=$(mktemp -d) ~/.venvs/kyphone/bin/python -m pytest -q -p no:cacheprovider \
  spi_bridge/tests/test_state_machine.py spi_bridge/tests/test_reader_state.py spi_bridge/tests/test_reader_epub.py \
  spi_bridge/tests/test_reader_fonts.py spi_bridge/tests/test_reader_layout.py \
  spi_bridge/tests/test_music_library.py spi_bridge/tests/test_music_player.py spi_bridge/tests/test_music_state.py \
  spi_bridge/tests/test_simulator.py spi_bridge/tests/test_firmware_host.py      # expect: 621 passed
```

## 1. Back up both devices (about 8 minutes)
Yesterday's Inkplate backup is the firmware from *before* OS 0.2.1. Take a new one so a rollback returns to the phone
as it works today.

**Inkplate** (stop the logger first: it holds the port)
```
pkill -f serial_log_macmini.py; sleep 3
~/Library/Arduino15/packages/Inkplate_Boards/tools/esptool_py/4.5.1/esptool --chip esp32 \
  --port /dev/cu.usbserial-1140 --baud 115200 read_flash 0 0x400000 ~/kyphone-backups/inkplate-flash-2026-09-19-before-reader.bin
shasum -a 256 ~/kyphone-backups/inkplate-flash-2026-09-19-before-reader.bin | tee ~/kyphone-backups/inkplate-flash-2026-09-19-before-reader.bin.sha256
```
(about 6 minutes; the file is exactly 4 MB.)

**Radxa** (the systemd unit is `/etc/systemd/system/kyphone.service`, runs as root from `/home/radxa/kyphone`)
```
ssh radxa 'mkdir -p ~/kyphone_backup_2026-09-19 && cp -a ~/kyphone/spi_bridge ~/kyphone_backup_2026-09-19/spi_bridge \
  && cp -a ~/kyphone/data ~/kyphone_backup_2026-09-19/data && sudo cp /etc/systemd/system/kyphone.service ~/kyphone_backup_2026-09-19/ \
  && ls ~/kyphone_backup_2026-09-19'
```
You should see `data  kyphone.service  spi_bridge`.

## 2. Flash the Inkplate (about 5 minutes)
```
bash ~/kyphone/flash_macmini.sh
```
It compiles (about a minute; the compile has been checked and is 392 KB, 12% of program space), stops the logger, uploads
at 115200 baud, waits 10 seconds and restarts the logger. It ends with `==> Done.`

**You should see:** the panel reboots and shows what it showed before. If you press a key, the home menu now has
**pixel-art icons** in the new order (text, call, book, music, address book), though Enter still follows the old order
until step 4 (see above).
`tail -20 /tmp/inkplate_serial.log` shows the boot lines and the commands it receives.

*Stop here if anything looks wrong* — see "If something goes wrong" below. Nothing on the Radxa has changed yet.

## 3. Look at book pages on the panel, without the Radxa (5 minutes)
The firmware accepts pages over USB, so you can judge the text before deploying. Stop the logger first (it holds the port),
and start it again afterwards (`nohup python3 ~/kyphone/spi_bridge/serial_log_macmini.py > /tmp/serial_log_stdout.txt 2>&1 &`).
```
pkill -f serial_log_macmini.py; sleep 2
cd ~/kyphone
/usr/bin/python3 spi_bridge/tools/preview_screens.py library music tracks nowplaying   # the library and the music screens
/usr/bin/python3 spi_bridge/tools/preview_screens.py --book ~/alice.epub --size M --chapter 3 --page 1
/usr/bin/python3 spi_bridge/tools/preview_screens.py --book ~/alice.epub --size M --chapter 3 --turns 10
```
The last one sends ten pages in a row and prints how long each took (draw + refresh, over USB, which is faster than the
real link). **Write these numbers down.** Try `--size S`, `L` and `X` too.

**You should see:** a serif page with indented paragraphs, a thin rule near the bottom, the chapter title at the left and
`page/total  percent` at the right. The first page flashes (full refresh); later ones do not.
Note that closing the USB port resets the board, so it reboots after the last page; the panel keeps its image.

## 4. Deploy the Python to the Radxa (5 minutes)
**Copy all eight files before restarting** — `kyphone_os.py` now imports the three reader modules and the two music
modules, and a missing one makes the service crash on start.
```
cd ~/kyphone/spi_bridge
scp kyphone_os.py simulator.py home_icons.py reader_epub.py reader_layout.py reader_fonts.py music_library.py music_player.py radxa:~/kyphone/spi_bridge/
ssh radxa 'mkdir -p ~/kyphone/data/books ~/kyphone/data/music/Test/Tones'
scp ~/alice.epub ~/monte-cristo.epub radxa:~/kyphone/data/books/
# a real 30-second 440 Hz test tone (quiet, so it is safe to try first), and your own music if you like:
python3 -c "import sys; sys.path.insert(0,'tests'); import audio_fixtures as f; open('/tmp/tone.wav','wb').write(f.wav_bytes(30, rate=44100, freq=440, volume=0.2))"
scp /tmp/tone.wav radxa:kyphone/data/music/Test/Tones/01_A440.wav
# scp -r ~/Music/SomeAlbum radxa:~/kyphone/data/music/
ssh radxa 'sudo systemctl restart kyphone; sleep 3; systemctl is-active kyphone; journalctl -u kyphone -n 15 --no-pager | tail -15'
```
**You should see:** `active`, and no `Traceback` in the log. (Also start the Mac's logger again if you stopped it in step 3.)

## 5. Click through it at the phone (20 minutes)
With the Bluetooth keyboard connected. Tick these off; note anything odd.

- [ ] Wake the phone: the home menu has icons in the order TEXT, CALL, READ (book), LISTEN (music), CONTACTS (address book). Only the first three are on screen; scroll down for the last two. Enter on each opens what its icon says.
- [ ] Down to READ, Enter: the **library** lists both books. Up to the header and Enter goes back home.
- [ ] Open Alice: the first page appears with a **full refresh** (flash). Footer shows the chapter and `1/n  0%`-ish.
- [ ] Turn 10 pages with Right / Enter. **Time a few turns** (Radxa → panel over the real link). Watch for ghosting; the
      9th turn should flash (full refresh).
- [ ] `+` twice (sizes L then X), `+` again: a stop alert "already at its largest size"; Enter returns to the page.
      `-` back down through M to S: check each size reads well; `-` again at S: alert.
- [ ] Left / Up turn back; at the very first page: alert "first page". Go to the end of a chapter: the next page starts the
      next chapter (flash).
- [ ] Esc: back to the library, now showing a percentage. Enter: it resumes on the same page.
- [ ] `ssh radxa 'sudo systemctl restart kyphone'`, then READ → the book resumes where you left it.
- [ ] Open Monte Cristo (a huge book): it should open instantly.
- [ ] **Music, silent checks first:** READ is row 3, LISTEN row 4. Enter on LISTEN: the album list shows `Tones` (from the test tone).
      Up to the header and Enter goes back home. With no music at all it would say NO MUSIC.
- [ ] **Music, the first sound** (headphones plugged in, held away from your ears): Enter on the album, Enter on the tone.
      The now-playing screen appears (PLAYING, a progress bar, `<< II >>`, a volume bar) and you should **hear a quiet
      steady tone**. If there is no sound, see the table below (the `Playback Path` switch).
- [ ] Space pauses and resumes (the middle glyph changes `II` <-> `>`); Up/Down or `+`/`-` change the volume in steps of 5
      (raise it slowly); `.` and `,` seek 15 s; Left restarts the track (more than 3 s in), Right at the last track redraws only.
- [ ] Esc out to the home menu **while it plays**: the tone continues, and a three-bar mark stands beside the music icon.
      Open a book and turn a page: no glitch in the sound while the e-ink refreshes.
- [ ] Back in LISTEN, the first row is NOW PLAYING and opens the screen. Let the tone finish while you sit on the home menu:
      the mark should disappear on its own. Restart the service, open LISTEN: a RESUME row appears (volume is remembered).
- [ ] **Formats** (the important check): copy one real file of each kind you own (MP3, M4A/AAC, FLAC, Ogg/Opus) into
      `data/music/`, and note which play. A file that will not play shows a "cannot be played ... skipped" alert and moves on.
- [ ] While music plays, glance at `top` on the Radxa (`ssh radxa top -bn1 | head -15`): CPU use should be small.
- [ ] Regression: TEXT list (an empty one opens with `+` selected) and a thread, CONTACTS, CALL screens all still draw normally. (A send ending in NOT SENT is
      normal: Twilio is off.)
- [ ] Optional: unplug nothing, just watch `tail -f /tmp/inkplate_serial.log` on the Mac while you turn pages: you should see
      one `SUCCESS! MSG: RTEXT…` line per frame and `Full refresh (reader page)` on the flashes.

**Numbers to bring back to me:** whether the tone and real music were heard, which formats played, the headphone volume that felt right, CPU while playing; seconds per page turn at each size; how much ghosting after 8 turns; whether the every-8th
full refresh feels right (it is `READER_FULL_EVERY` in `kyphone_os.py`); anything that looked wrong.

## If something goes wrong
| You see | Likely cause | Do |
| :--- | :--- | :--- |
| Stray text like `RTEXT\|M\|0\|S\|…` on the panel | the old firmware is still running (flash failed) | re-run step 2 |
| The service will not start / `ModuleNotFoundError: reader_…` in the journal | a file was missed in step 4 | copy all six again, restart |
| Library says NO BOOKS | books are not in `~/kyphone/data/books/` (or not `.epub`) | check with `ssh radxa ls ~/kyphone/data/books` |
| A book says it cannot be opened | it is copy-protected, corrupt, or only pictures | the alert says which; try another book |
| Panel does not update after a key | the Inkplate did not raise its ready line | wait 15 s; check `/tmp/inkplate_serial.log`; power-cycle the Inkplate |
| Page turns feel slow | expected to be a few seconds (several 10 kHz frames + refresh) | bring me the timings; raising `SPI_SPEED_HZ` is the lever |
| Music: no sound at all | the codec is not routed to the jack, or the level is at zero | `ssh radxa "amixer -c 1 sset 'Playback Path' HP"`, check `amixer -c 1 sget Headphone`; or set `KYPHONE_AUDIO_DEVICE` / `_PATH` in the service |
| Music: a "NO SOUND OUTPUT" alert when you choose a track | GStreamer's Python bindings did not load | `ssh radxa 'python3 -c "import gi; gi.require_version(\"Gst\",\"1.0\")"'`; bring me the message |
| Music: a format will not play | its decoder or a demuxer is missing | note the format; `apt install gstreamer1.0-plugins-good gstreamer1.0-plugins-ugly` is the free fallback |
| The flash script cannot find a library file / `Operation timed out` | an iCloud-evicted file in `~/Documents/Arduino/libraries` | open the named file in Finder so macOS downloads it, then re-run |

**Roll back the Radxa** (about 30 seconds; the old code does not import the new modules, so leaving them is harmless):
```
ssh radxa 'cp -a ~/kyphone_backup_2026-09-19/spi_bridge/. ~/kyphone/spi_bridge/ && sudo systemctl restart kyphone && systemctl is-active kyphone'
```
**Roll back the Inkplate** (about 6 minutes; stop the logger first):
```
pkill -f serial_log_macmini.py; sleep 3
~/Library/Arduino15/packages/Inkplate_Boards/tools/esptool_py/4.5.1/esptool --chip esp32 --port /dev/cu.usbserial-1140 \
  --baud 115200 --before default_reset --after hard_reset write_flash 0 ~/kyphone-backups/inkplate-flash-2026-09-19-before-reader.bin
```
(Check the checksum against the `.sha256` file first. Yesterday's file, `inkplate-flash-2026-09-18.bin`, is the older
pre-OS-0.2.1 firmware, a deeper rollback.)

## After it works
Tell me the timings and I will record them in `planning/reader-build-plan.md` (step 6b) and tune. Then the remaining
decision is the push — see `planning/push-review-2026-09-19.md`.
