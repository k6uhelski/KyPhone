# CLAUDE.md - KyPhone Project Technical Handoff

## 1. Project Overview
**KyPhone** is a project to create an E-ink "phone" interface by using a **Radxa Rock 3A** (Single Board Computer) as the logic master and an **Inkplate 4 TEMPERA** (ESP32-based E-ink display) as the synchronized display peripheral.

*   **Final Goal:** Unidirectional data transmission from Radxa to Inkplate to mirror a UI or display system notifications with low power consumption and high sunlight readability.
*   **System Architecture:** 
    *   **Master (Radxa):** Runs a Python-based SPI controller. It manages the application logic and "pushes" display updates.
    *   **Slave (Inkplate):** Runs a custom interrupt-driven software SPI peripheral. It listens for incoming bitstreams and renders them to the E-ink panel.
    *   **Handshake:** A 1-wire "Ready" signal from the Slave to the Master prevents buffer overflows and ensures the Slave is not busy with a display refresh.
    *   **Latency Requirements:** Must be sufficient for smooth UI navigation (text updates), though E-ink refresh rates are the ultimate bottleneck.

## 2. Hardware Inventory
*   **Master:** Radxa Rock 3A (Rockchip RK3568, Debian 11).
*   **Slave:** Inkplate 4 TEMPERA (ESP32-WROVER-E, 8MB PSRAM).
*   **OS/Firmware:**
    *   **Radxa:** Linux 5.10 kernel, `gpiod` v1.6.
    *   **Inkplate:** Arduino Core for ESP32 (v2.x or v3.x), `Inkplate.h` library.

### **Pin Assignment & Wiring**
| Function | Radxa Pin | Wire Color | `gpiod` / `ESP32` Label | Role |
| :--- | :--- | :--- | :--- | :--- |
| **MOSI** | Pin 19 | 🟣 Purple | `gpiochip3`, Line 9 / **IO 13** | Data |
| **SCLK** | Pin 23 | ⚪ White | `gpiochip3`, Line 8 / **IO 14** | Clock |
| **CS (SS)** | Pin 24 | 🔵 Blue | `gpiochip3`, Line 10 / **IO 15** | Framing |
| **Handshake**| Pin 13 | 🟡 Yellow | `gpiochip3`, Line 21 / **P1-0** | Flow Control |
| **GND** | Pin 6 | 🔘 Grey | Common Ground | Stability |

### **PCB-Level Constraints**
*   **Shared Nets:** Pins 13, 14, and 15 are physically hardwired to the on-board E-ink peripheral controller. 
*   **Strapping Pin:** **GPIO 15** is the ESP32 `MTDO` strapping pin. It has an internal pull-down at boot to select flash voltage/mode.
*   **Signal Integrity:** The display controller adds significant impedance and ringing to these traces, making hardware SPI peripherals fail at the silicon logic level.

## 3. Communication Layer — Full History
1.  **Hardware SPI (Failed):** Attempted standard ESP-IDF `spi_slave`. The hardware SPI peripheral rejected the noisy signals arriving on the shared display pins. No data was captured.
2.  **Naked SPI Test (Failed):** Removed the `Inkplate.h` library to eliminate software interference. Hardware SPI still failed, proving the display controller hardware itself is the source of the signal degradation.
3.  **Polling Software SPI (Partially Successful):** Used a `while(digitalRead(SCLK))` loop. Proved that voltage is reaching the pins and counted exactly 272 pulses. Abandoned because polling blocks the CPU and misses edges when the display is active.
4.  **V3 Interrupt-Driven SPI (Current):** Switched to ISRs on `SCLK` (posedge) and `CS` (anyedge).
    *   **Issue:** `SCLK` interrupts fire perfectly (272 counts), but `CS` interrupts remain at 0.

## 4. SPI Transport Layer (V4 Firmware — still in use)

### **Framing: CS-less SCLK-Timeout**
CS (Pin 15) is unreliable on the Inkplate PCB (see §5), so `SCLK` does double duty as both clock and frame delimiter.

*   Master waits for Handshake HIGH, then sends 256-byte SPI transfer.
*   Slave counts SCLK rising edges via IRAM-pinned ISR into a 256-byte `rx_buf`.
*   If SCLK is silent for > 50 ms and exactly 2048 bits were received, `transfer_complete` is set.
*   After each display refresh, `reclaim_pin15_for_gpio()` re-asserts IO_MUX ownership of Pin 15 (the Inkplate library silently reclaims it during `display.display()`).

**Risk:** A single noise pulse on SCLK shifts the bitstream. Mitigated by exact-bit-count check; no CRC yet.

### **Key firmware constants**
```cpp
#define PAYLOAD_BYTES 256   // must match kyphone_os.py PAYLOAD_BYTES
#define TOTAL_BITS    2048  // PAYLOAD_BYTES * 8
#define PIN_MOSI 13
#define PIN_SCLK 14
#define PIN_CS   15
#define PIN_HANDSHAKE IO_PIN_B0
```

## 5. The Pin 15 Problem — Everything You Know
*   **Symptoms:** `SCLK` interrupts fire correctly. `CS` (Pin 15) interrupts fire 0 times, or fire hundreds of times (noise) but never a clean framing pulse.
*   **Hypothesis 1 (IO_MUX):** The `Inkplate.h` library calls `SPI.begin()`, which sets `IO_MUX_GPIO15_REG` to Function 1 (SPI CS0). This routes the pin directly to the SPI peripheral, bypassing the GPIO Matrix required for `attachInterrupt()`. **Status: Confirmed — `reclaim_pin15_for_gpio()` is the workaround, but the library re-steals it on every refresh.**
*   **Hypothesis 2 (Strapping Pin):** GPIO 15 is `MTDO`. It has a boot-time pull-down. On the Inkplate PCB, it might be heavily clamped to 0V or 3.3V to ensure boot stability. **Status: Mitigating with internal PULLUP.**
*   **Hypothesis 3 (Library Theft):** The Inkplate library re-initializes the display peripheral during `display.display()` or `display.einkOff()`, silently re-claiming Pin 15 for the SPI hardware.

### **What Has NOT Been Tried**
*   **Hardware Filtering:** Adding a small capacitor to Pin 15 to damp ringing.
*   **Level Shifting:** Confirming if the Radxa (3.3V) and Inkplate (3.3V) have a voltage delta causing the "Invisible 0" on Pin 15.
*   **ESP-IDF GPIO Driver Only:** Completely bypassing Arduino `attachInterrupt`/`pinMode` and using low-level `gpio_isr_handler_add` after manual IO_MUX config.

### **Open Questions**
*   Why does `SCLK` succeed at the IO_MUX level when Pin 15 fails, even though both are used by the E-ink controller? (Theory: SCLK is an output from the ESP32 to the display, leaving its input-sense matrix path open.)
*   Does `display.einkOff()` fully release the SPI bus or just cut power to the panel?

## 6. OS 0.2.1 — Application Layer

**The spec is `docs/02-design/design_handoff_os_0_2/`** — the design doc, `GEOMETRY.md`, the prototype (`KyPhone UI v4.dc.html`) and 600×600 captures. When the prose and the prototype disagree, the prototype wins (it has behaviours the README does not list). **The build log — status, decisions, deviations, rollbacks — is `planning/os-0.2.1-build-plan.md`.**

### **Version history**
*   `5a32d00` — labelled OS 0.0 (no git tag exists): the 3-screen app (`kyphone_app.py`, HOME / MSG_LIST / MSG_THREAD), no longer tracked.
*   `039042d` — OS 0.1: 6-screen state machine (`kyphone_os.py`) and TDD suite.
*   `b2a33cc` — OS 0.2: contacts, calls, trackpad navigation.
*   OS 0.2.1 (branch `os-0.2.1-build`, until merged): windowed lists, message states and retry, formatted numbers, contact create/delete, stop alerts, icon home menu. Firmware and Radxa updated 2026-09-18 except the icons (built, not yet flashed — see the plan).
*   **Reader** (branch `reader-build`, on top of `os-0.2.1-build`; built, tested and compile-checked, **not yet flashed or deployed**): READ opens a library of EPUBs from `data/books/`; a book is read a page at a time with four font sizes. See *Reader (books)* below and `planning/reader-build-plan.md`.
*   **Music** (branch `music-build`, on top of `reader-build`; built, tested and compile-checked, **not yet flashed, deployed or heard on the headphone jack**): LISTEN plays music files from `data/music/` through GStreamer, in the background. See *Music (LISTEN)* below and `planning/music-build-plan.md`.

### **State machine**
`kyphone_os.py` is the production entry point. One `state['screen']` string drives all rendering; every mutable value lives in the single `state` dict behind one lock.

Screens: `lock · home · texts_list · thread · compose · confirm · contacts_pick · contact · contact_edit · stub · calls_list · dial · outgoing · incoming · in_call · library · reader · music · tracks · nowplaying`. `stub` is the one **stop alert** screen (validation, empty send, no recipient, the ends of a book or of the font sizes, a track that will not play, no sound system); `confirm` is the one **confirmation** screen (discard message, delete contact).

Selection convention: a list's `*_index` is an absolute position, `-1` = the header (with `*_header_sel` = `back`/`plus`). Lists are windowed (`texts_start`, `contacts_start`, `calls_start`); the wire carries the selection **relative to the window**.

### **Data model**
*   **Messages** (`data/messages.json`). Incoming: `{sender, name, body, read, ts}`. Outgoing: `{dir:'out', peer, name:'You', body, read, ts, state}` with `state` = `sending | sent | not_sent`. A thread is keyed on the **other party's** number (`peer_of()`); the phone never shows or needs its own number. Old outgoing records with no recipient are hidden, not deleted.
*   **Contacts** (`data/contacts.json`): a list of `{first, last, number}`, **identified by position, never by display name** (two contacts can share a name). Numbers are stored formatted, `(555) 010-0001`, and matched by their last ten digits (`same_number`), so `+15550100001`, `1 555 010 0001` and `(555) 010-0001` are one person. New contacts sort alphabetically.
*   **Numbers**: `format_number` (never truncated — 14 chars), `same_number`, `normalize_number` (`+1XXXXXXXXXX`, what the radio is handed), `number_valid` (ten digits, or eleven starting with 1).

### **Behaviour, by screen**
| Screen | Keys |
| :--- | :--- |
| lock | any → home |
| home | ↑↓ move (−1 = header; Enter there → lock). Enter opens TEXT / CALL / READ / LISTEN / CONTACTS (list windows reset). The row after LISTEN's icon shows a three-bar mark while music plays. Esc → lock. `i` = incoming-call demo. Menu order (Kyle's, 2026-09-19: texts, calls, books, music, address book): TEXT, CALL, READ, LISTEN, CONTACTS — the design handoff had CONTACTS third; only the first three rows are on screen without scrolling. The order lives in four places that must agree (`kyphone_os.HOME_MENU`, `simulator.HOME_MENU`, `labels[]` in `ui_screens.h`, `tools/make_icons.py` ORDER → the generated icon tables); a test checks them |
| texts_list | ↑↓; ↑ past the first row → header (←→ back/plus); Enter → thread (marks read); `+` or header plus → compose; Esc → home. **An empty list opens with `+` selected** (there is no row to select), so ←→ and Enter work at once; ↓ has nowhere to go |
| thread | typing edits the draft; Enter sends (empty → alert). ↑ from the composer selects the newest **NOT SENT** bubble (else the header); Enter on it **retries**; ↑ again → header; ↓ → composer. Header ←→ back/info; info → contact page. Esc → texts_list |
| compose | Tab toggles TO/MESSAGE. ↑ walks SEND → MESSAGE → TO → the X in the header; ↓ MESSAGE → SEND. Enter: TO empty → contact picker, TO set → MESSAGE, MESSAGE or SEND → send (alerts, in this order: no recipient; a number that cannot be texted — needs ten digits, or eleven starting with 1 — which puts the cursor back on TO with everything kept; empty message). `+` beside an empty TO opens the picker. Esc with a draft → discard confirm. TO holds 20 chars; the message is uncapped |
| confirm | ← destructive button, → safe button; **opens on the safe one**; Esc is the safe choice; Enter acts on the selection |
| contacts_pick | typing filters by name prefix (LOOK UP); ↑↓; header back/plus; **the `+` key opens a new contact when the search box is empty** (with letters typed, `+` is just a character); an empty list (no contacts, nothing typed) opens with `+` selected, so ←→ and Enter work at once; Enter → contact page (from home) or picks into compose |
| contact | top row `[back, edit]`, action row `[call, text]` (saved) · `[add number]` (saved, no number) · `[call, text, save]` (not in contacts, no EDIT). SAVE opens the form with the number filled in |
| contact_edit | ↑↓ through first / last / number / SAVE; ← from SAVE reaches **DELETE** (existing contacts only). Four validation alerts (first name, number, dialable, duplicate) return to the offending field. First is required, last optional. **A form opened from a conversation's `i` page (a valid number) has its phone number locked**: it is pre-filled, the arrows step over it, and typing cannot change it, so the name always attaches to that conversation; a number that cannot be dialed (an old bad conversation) stays editable. X/Esc return to where the form was opened; a save from the compose picker returns to compose with the contact in TO |
| library | ↑↓ through the books (↑ past the first → header; Enter there → home); Enter opens a book (a file that cannot be read says why on a stop alert); Esc → home |
| reader | → ↓ Enter Space = next page; ← ↑ Backspace = previous; `+`/`=` bigger text, `-`/`_` smaller; Esc/`q` → library. The ends of the book and of the size range raise a stop alert; Enter/Esc returns to the page |
| music | the album list, A–Z; a NOW PLAYING row (or RESUME after a restart) leads when there is one. ↑↓; ↑ past the first → header (Enter → home); Enter opens an album (or NOW PLAYING / RESUME); Esc → home. Empty: NO MUSIC |
| tracks | one album's tracks. ↑↓; header back (Enter/Esc → the album list); Enter plays the album **from that track** and shows now-playing (choosing the track already playing just shows it) |
| nowplaying | Space/Enter play-pause · → next · ← previous (restarts the track if more than 3 s in) · ↑/↓ and `+`/`-` volume ±5 · `.` / `,` seek ±15 s · Esc/`q` back (music keeps playing). A key that changes nothing (next at the last track, volume at the limit) redraws, so it is visibly answered |
| stub | Enter/Esc → the screen it came from, state intact |
| calls_list / dial / outgoing / incoming / in_call | **simulated** — there is no telephony until the cellular modem exists. **Calls are logged when they end** (`data/calls.json`, newest 50, newest first): an answered outgoing call is OUT with its length (m:ss), one cancelled before it connects is OUT with none, an answered incoming call is IN, an incoming call nobody answers is MISS. Enter on a log row redials it. The time column reads like messages (clock time, Yesterday, a weekday, a date) |

`Q`/`W`/`A`/`S`/`D` act as Esc and the arrows on screens where letters are not being typed.

### **Sending**
`send_reply()` stores the message as **SENDING…** and returns; a worker thread hands it to `_transport_send()` so the keyboard never waits on the network. With no modem and Twilio switched off, `_transport_send` raises `no service`, so **NOT SENT is the phone's normal outcome today**, not an error case. The path to Twilio still exists if credentials are set. In the simulator, `KYPHONE_SIM_SEND=sent` makes the fake radio succeed (default: not sent, like the phone).

### **Reader (books)**
*   **Loading books:** copy `.epub` files into `data/books/` on the Radxa (no upload screen yet). READ lists them by title with progress; the list is rescanned each time it opens. DRM'd, corrupt or picture-only books are listed and explain themselves when opened.
*   **Modules** (all standard library, importable without hardware): `reader_epub.py` reads the zip → OPF → spine, titles from the EPUB 3 nav or EPUB 2 NCX, chapters as `('p'|'h', text)` paragraphs reduced to drawable ASCII; chapters load **lazily** (opening a 2.6M-character novel takes 0.09 s on the Radxa; parsing it all took 5.7 s). `reader_layout.py` wraps with the exact glyph advances the panel sums, centres headings and scene breaks, paginates a chapter, and packs a page into frames. `reader_fonts.py` is **generated** from the four FreeSerif headers (`tools/make_reader_fonts.py`; never edit it).
*   **Position** = (chapter, character offset in that chapter), independent of font size, so a change of size lands on the page containing the place. Saved to `data/reading.json` (`{font, books: {file:size → {chapter, offset, pct}}}`) after every page. Book progress (%) is by file size, so approximate.
*   **Refresh policy:** the Radxa asks for a *full* (flashing) refresh when a book opens, on a new chapter, a new font size, after a stop alert and every 8th turn (`READER_FULL_EVERY`); otherwise partial.
*   **Text limits:** printable ASCII only (accents stripped, typographic punctuation reduced, anything else `?`); no images, bold or italic; no chapter menu yet.
*   **Sending a page:** `push_page(frames)` queues the whole page as ONE item (the sender otherwise keeps only the latest command); frames go out back to back and the Inkplate refreshes only on the last. If a newer command arrives mid-page the rest is dropped (nothing was shown), so fast paging skips pages instead of queueing them.

### **Music (LISTEN)**
*   **Loading music:** copy audio files into `data/music/` on the Radxa, in any folders. MP3, M4A/AAC, FLAC, Ogg (Vorbis, Opus) and WAV are read; the Radxa's GStreamer has the decoders for all of them (mpg123, flacdec, vorbisdec, opusdec, avdec_aac/alac, wavparse) but **each format is to be proven with a real file on the phone**.
*   **Modules** (importable without hardware): `music_library.py` reads tags and lengths straight from the files with the standard library (ID3v1/v2, FLAC and Ogg comments, M4A atoms, WAV), only the bytes that matter (cover art is skipped), describes untagged files by their folders and name (`Artist/Album/03 - Song.mp3`), groups albums by name plus album-artist or folder, and caches results in `data/music_index.json` keyed by (path, size, mtime). `music_player.py`: `Session` holds the queue and every rule (next stops quietly at the end, previous restarts a track more than 3 s in, a file that will not play is reported once and skipped, end of track advances, callbacks made with no locks held); `SimPlayer` is silent and keeps time (emulator and tests); `GstPlayer` plays through a GStreamer `playbin` (via `gi`) to the codec's ALSA device, setting its `Playback Path` to HP with `amixer`. Environment: `KYPHONE_AUDIO_DEVICE` (default `plughw:1,0`; **`fake` = a silent output that keeps real time**), `KYPHONE_AUDIO_CARD`, `KYPHONE_AUDIO_PATH`, `KYPHONE_AUDIO_CEILING`.
*   **Background playback:** leaving LISTEN does not stop the music. `HOME2` carries a sixth field (`1` = playing) for the mark. A ticker redraws now-playing every 30 s while it is showing and playing (so the time moves without hammering the e-ink); nothing else is redrawn by the player, except the home menu when the album finishes (so the mark goes out). A bad track only raises its alert if now-playing is on screen; in the background it is skipped silently.
*   **No silent fakes on the phone:** if GStreamer is unavailable there, choosing a track shows a NO_AUDIO alert. The silent player is only ever used in the emulator.
*   **Saved:** `data/listening.json` = `{volume, last: {path, position}}`. The first play uses volume 40 (low on purpose). After a restart the album list offers RESUME.
*   **Not built yet:** Bluetooth headphones (needs PulseAudio's Bluetooth module; the service runs as root, which is the awkward part), shuffle/repeat, playlists, podcasts/audiobooks.

### **One command = one frame**
Every screen is drawn from **one command of at most 253 characters** (`MAX_COMMAND_CHARS`; `PAYLOAD_BYTES` 256 minus the 3-byte header), so:
*   **Lists are windowed** — only the visible rows are sent (texts 5, contacts 7, calls 6, `window_start()`), and the window re-sends as the selection moves one row at a time.
*   **Nothing is cut mid-field.** `_list_command()` shortens the flexible column (preview / name) when unlucky data would overflow; `_thread_command()` drops the oldest bubble first, never the selected one, and cuts a lone over-long message with `...`.
*   The thread composer shows the **end** of a long draft behind `...` (`composer_view()`, three lines); New Message shows the end of a long message the same way. Sending is uncapped.

### **Text the panel can draw**
The panel draws **printable ASCII only**, and `|` and `·` (byte 0xB7) are wire separators. Typed keys that cannot be drawn are ignored silently (the one deliberate silence); received text goes through `sanitize()` (curly quotes and dashes become ASCII, newlines become spaces, anything else `?`). Our own labels obey the same rule: `SENDING...`, never an ellipsis character.

### **Wire protocol**
All commands: `PREFIX|field|field|…`, sub-fields split on `·`, latin-1 bytes, ≤253 characters.

| Screen | Command |
| :--- | :--- |
| Lock | `LOCK\|time\|DAY, MON DD\|quote\|attribution` |
| Home | `HOME2\|time\|index\|unread\|style\|playing` — index −1 header, 0 TEXT 1 CALL 2 READ 3 LISTEN 4 CONTACTS; `playing` 1 = music is playing (the equalizer mark by the LISTEN row); style `I` icons (default) / `B` icons and words / `W` words |
| Texts | `TEXTS\|sel\|name·preview·unread·time\|…` — sel −1 back, −2 plus, else the row in the window; ≤5 rows; preview starts `You: ` or `! ` (unsent); no rows = empty state |
| Contacts | `CONTACTSPICK\|sel\|query\|3 / 14\|name·number\|…` — ≤7 rows; no rows = NO MATCH (query set) or NO CONTACTS |
| Calls | `CALLS\|sel\|name·tag·time·duration\|…` — ≤6 rows; the first list entry is `DIAL A NUMBER·NEW··` |
| Thread | `THREAD2\|name\|draft\|hdr\|code·time·text\|…` — hdr `''`/`B`/`I`; code `R` received, `Y0` sending, `Y1` sent, `Y2` not sent, `Y3` not sent and selected; ≤3 bubbles |
| Compose | `COMPOSE\|to\|msg\|to_active\|hdr\|plus_sel\|send_sel` |
| Contact | `CONTACT\|title\|sub\|kind\|sel` — kind `S` saved, `N` no number, `U` unsaved; sel `B E C T V A` |
| Contact form | `CONTACTEDIT\|first\|last\|number\|idx\|kind` — idx −1 cancel, 0–2 fields, 3 save, 4 delete; kind `N` new, `E` edit |
| Stop alert | `STUB\|title\|body` |
| Confirm | `CONFIRM\|title\|body\|go\|keep\|sel` — sel `D` / `K` |
| Library | `LIBRARY\|sel\|title·author·pct\|…` — sel −1 back, else the row in the window; ≤5 rows; no rows = NO BOOKS |
| Book text | `RTEXT\|size\|row\|S/-\|line·line·…` — size `S M L X` (FreeSerif 9/12/18/24pt), `row` = the first line's row (digits only), `S` = first frame of a page: clear. Draws only; never refreshes. Blank lines are empty fields |
| Book footer | `RFOOT\|P/F\|left\|right` — the footer rule, chapter title at the left margin, `12/40  35%` at the right; then refresh: `P` partial, `F` full. Always a page's last frame |
| Music list | `MUSIC\|sel\|title·subtitle·right\|…` — ≤5 rows, sel −1 back; the first row may be `NOW PLAYING·title - artist·PLAYING/PAUSED` or `RESUME·…`; albums are `name·artist·N trk`; no rows = NO MUSIC |
| Tracks | `TRACKS\|sel\|album\|title·artist·time\|…` — ≤5 rows; the album name (≤20) is the header |
| Now playing | `NOWPLAYING\|state\|title\|artist\|album\|elapsed\|total\|volume\|n/N` — state `P` playing, `U` paused, `S` finished; times in whole seconds (0 = unknown); one frame |
| Call screens | `DIAL\|…`, `CALLSTATE\|OUT/IN/ACTIVE\|name\|mm:ss` (unchanged) |

### **Inkplate firmware**
*   **`ui_screens.h`** draws every OS 0.2.1 screen with the Adafruit GFX built-in font, ported from `simulator.py`. It depends only on `display.setCursor / setTextSize / setTextColor / print / fillRect / drawRect / drawBitmap`, so the same code also builds on a computer (below). **`ui_icons.h`** holds the home-menu bitmaps and is **generated** — edit `tools/make_icons.py`, never the header.
*   `Inkplate_SPI_Peripheral.ino` keeps the V4 transport, the lock screen (with its fixed cat bitmap), dial, call state and the OS 0.0 screens. Command handling is `handle_command()`, shared by the SPI link and a **USB preview**: a line `@<command>` on the USB serial port draws that screen as if the Radxa had sent it (`tools/preview_screens.py`), so screens can be checked on the panel without the Radxa.
*   **GFX geometry:** a size-N glyph is a 5×7 shape in a 6×8 cell scaled by N, so its baseline sits `7*N` below the cursor. `ui_text()` draws at a baseline, which is how the designer's measured baselines are used directly. Bold is printing twice, one pixel apart. The design's 18 px text is textSize 2 (16 px) on the device.
*   **`ui_reader.h`** draws book pages with the four FreeSerif fonts (vendored into `Inkplate_SPI_Peripheral/fonts/`, one include line removed — see `assets/freeserif-NOTICE.txt`). `handle_command()` routes `RTEXT`/`RFOOT` **before** its clear-and-refresh path. A GFX custom font's cursor y is the baseline; row *r* sits on `24 + r*yAdvance + (3*yAdvance)/4`, the same integer rule as `reader_layout.baseline()`. It turns text wrap off while drawing and restores the built-in font afterwards.
*   The receive buffer is `PAYLOAD_BYTES + 1` with a guaranteed terminator: a 253-character command fills all 256 bytes.

### **Tests and tools**
Ten suites, 621 tests (`test_state_machine.py` 238, `test_reader_state.py` 44, `test_reader_epub.py` 47, `test_reader_fonts.py` 11, `test_reader_layout.py` 31, `test_music_library.py` 53, `test_music_player.py` 43, `test_music_state.py` 41, `test_simulator.py` 66, `test_firmware_host.py` 47):
*   **`test_state_machine.py`** — state transitions, wire strings, frame limits, sending/retry, contacts. Hardware mocked at import time; `push_screen` is patched to capture the SPI command.
*   **`test_simulator.py`** — pixel checks on real emulator frames (headless pygame): rows, rules, buttons, icons pixel-for-pixel, the icons against the designer's capture, the generator's output being up to date, and that `simulator.wrap_words` matches the OS's.
*   **`test_reader_epub.py`** (synthetic EPUBs built with `zipfile`, via `epub_fixtures.py`), **`test_reader_fonts.py`** (the generated tables agree with the headers, read a second way), **`test_reader_layout.py`** (widths, nothing lost or duplicated, headings, positions across font sizes, frame sizes), **`test_reader_state.py`** (the real `handle_key` against a temp books folder: library, opening, turning, refresh cadence, chapter and book ends, font size, resume, corrupt saved data, the sender loop).
*   **`test_music_library.py`** (every tag format from synthetic files built by `audio_fixtures.py`, damaged and random files never raise, naming fallbacks, albums, the cache, a 5,000-track scan), **`test_music_player.py`** (every Session rule against a fake player, `SimPlayer` in simulated time, threads, the phone player's configuration), **`test_music_state.py`** (the real `handle_key` against a temp music folder with tiny WAVs and a hand-advanced clock: browsing, playing, every key, background playback, the ticker, saved volume and resume, bad files, no sound system).
*   **`test_firmware_host.py`** — builds `ui_screens.h` for the computer with `tests/firmware_host/` (a fake display using the real GFX font) and checks exact geometry, that firmware and emulator agree on every rule and inverted row, and memory safety (thousands of malformed and maximum-length commands under the address and undefined-behaviour sanitizers). Needs `clang++` and Adafruit_GFX's `glcdfont.c`; skips otherwise.

```
KYPHONE_DATA_DIR=$(mktemp -d) python3 -m pytest spi_bridge/tests/test_state_machine.py spi_bridge/tests/test_reader_state.py spi_bridge/tests/test_reader_epub.py spi_bridge/tests/test_reader_fonts.py spi_bridge/tests/test_reader_layout.py spi_bridge/tests/test_music_library.py spi_bridge/tests/test_music_player.py spi_bridge/tests/test_music_state.py spi_bridge/tests/test_simulator.py spi_bridge/tests/test_firmware_host.py
```
Name the files — **do not point pytest at the whole `tests/` folder**: the hardware diagnostic scripts there run on import. Expect `621 passed`; if the simulator and firmware tests show as skipped, pygame is not installed in that Python. The simulator and firmware suites need `pygame`; use a virtualenv (`pip install pygame pytest`).

**Set `KYPHONE_DATA_DIR` to a scratch folder** (as above) so the tests never touch the real `data/`: importing `kyphone_os` loads, and can rewrite, `contacts.json`. The same variable works for the simulator (`KYPHONE_DATA_DIR=$(mktemp -d) python3 spi_bridge/kyphone_os.py --sim`).

Other tools: `tools/make_icons.py [--check]` (icon bitmaps), `tools/make_reader_fonts.py [--check]` (book-font tables; `preview_screens.py --book x.epub --turns 10` sends real pages to the panel over USB and times them; `--dry-run` shows the frames), `tools/preview_screens.py` (draw screens on the real panel over USB; use the system `/usr/bin/python3`, which has pyserial), `flash_macmini.sh` (compile and flash from the Mac mini), `serial_log_macmini.py` (streams the Inkplate's serial output to `/tmp/inkplate_serial.log`).

### **Simulator**
```
pip3 install pygame twilio          # twilio is imported even in emulator mode (no account or credentials needed)
KYPHONE_DATA_DIR=$(mktemp -d) python3 spi_bridge/kyphone_os.py --sim
```
To try the reader, make `books/` inside that scratch folder and put an `.epub` in it before starting (any Project Gutenberg EPUB works). A window opens; press any key to wake, Down ×3 and Enter for READ. The tests do not need twilio (they mock it), only the emulator run does.
Renders every screen in a 600×600 pygame window with full keyboard navigation. Environment: `KYPHONE_SIM_SEND=sent|not_sent`, `KYPHONE_HOME_STYLE=icons|both|words`, `KYPHONE_DATA_DIR=<folder>` (where `contacts.json` and `messages.json` live; default `data/` beside `spi_bridge/`). The emulator's text is narrower than the panel's fixed 6×8-cell font, so wrapping follows the device figures (composer 30 columns, bubbles 20) rather than the font.

### **Deploying**
*   **Radxa:** systemd `kyphone.service` runs `spi_bridge/kyphone_os.py` as root from `~/kyphone`. To update: copy `kyphone_os.py`, `simulator.py`, `home_icons.py` and (reader) `reader_epub.py`, `reader_layout.py`, `reader_fonts.py`, `music_library.py`, `music_player.py`; put books in `~/kyphone/data/books/` and music in `~/kyphone/data/music/` (wired playback needs no new packages: GStreamer and its Python bindings are already on the Radxa); `sudo systemctl restart kyphone`; check `journalctl -u kyphone` on the Radxa and `/tmp/inkplate_serial.log` **on the Mac mini** (its logger streams the Inkplate's USB serial output, one line per command it receives). The Radxa's git clone is not kept in step — files are copied in.
*   **Inkplate:** `flash_macmini.sh`, or write only the app image at `0x10000` with esptool (the bootloader and partition table do not change). **Stop `serial_log_macmini.py` first** — it holds the port — and start it again afterwards.
*   **Always back up both first.** Inkplate: `esptool read_flash 0 0x400000 <file>` gives an exact 4 MB rollback (about 6 minutes at 115200). Radxa: copy `spi_bridge/`, `data/` and the unit file. Python and firmware must ship **together** when a wire format changes.

### **Security / privacy constraints**
*   Phone numbers live only on the Radxa in gitignored `data/contacts.json`, `data/messages.json` and `data/calls.json` (the call log) — never committed. Tests and screenshots use fictional `(555) 01x-xxxx` numbers.
*   **Twilio is switched off** on the Radxa: `TWILIO_SID` and `TWILIO_TOKEN` are commented out in `kyphone.service` and `start_kyphone.sh` (backups `*.bak-2026-09-18`). `TWILIO_NUMBER` must stay set — the module exits without it. Credentials never belong in git or in this file; rotate any that have appeared in a transcript.
*   `kyphone_app.py` (OS 0.0, demo mode) is untracked and gitignored, and the OS 0.2 code has no demo mode.

## 7. File & Directory Map
*   `spi_bridge/kyphone_os.py` — production entry point and state machine.
*   `spi_bridge/simulator.py` — pygame simulator (`--sim`); mirrors the firmware's screens. `spi_bridge/home_icons.py` — generated icon bitmaps for it.
*   `spi_bridge/input_handler.py`, `trackpad_handler.py` — evdev keyboard and trackpad readers; forward nav keys and `CHAR:<c>`.
*   `spi_bridge/Inkplate_SPI_Peripheral/Inkplate_SPI_Peripheral.ino` — firmware: transport, lock screen, dial/call state, `handle_command()`, USB preview. `ui_screens.h` — the OS 0.2.1 renderers. `ui_icons.h` — generated bitmaps.
*   `spi_bridge/reader_epub.py`, `reader_layout.py`, `reader_fonts.py` (generated) — the reader's parser, layout and font tables; `spi_bridge/Inkplate_SPI_Peripheral/ui_reader.h` and `fonts/` — the firmware side.
*   `spi_bridge/music_library.py`, `music_player.py` — the music library scan / tag readers and the playback session and players.

*   `spi_bridge/tools/` — `make_icons.py`, `make_reader_fonts.py`, `preview_screens.py`. `spi_bridge/assets/` — the icon and font licence notices.
*   `spi_bridge/tests/` — the ten test files above, `epub_fixtures.py`, `audio_fixtures.py`, `firmware_host/` (fake display with GFX custom-font printing, `render_host.cpp`, `render_reader.cpp`, canned screens); the older hardware diagnostics (`Signal_Detector.ino`, `wire_verifier.py`, …) are not unit tests.
*   `docs/02-design/design_handoff_os_0_2/` — the design spec, geometry table, prototypes and captures.
*   `planning/os-0.2.1-build-plan.md` — build status, wire changes, decisions, rollbacks. `planning/kyphone_backlog.md`, `kyphone_milestones.md` — longer-range plans.
*   `flash_macmini.sh` (untracked, machine-specific), `serial_log_macmini.py`.

## 8. Design Principles

### **Architectural principles**
How the system is built — decisions that shape every layer.

1.  **Unidirectional data flow.** Radxa is the sole source of truth; Inkplate is a stateless sink. Nothing is stored on the ESP32 between frames. The display never sends data back (the Handshake Ready line is the only return signal, and it is flow control, not data).
2.  **Single state dict, single lock.** All mutable state lives in one `state` dict. A single `threading.Lock()` protects it. No per-screen state objects, no event queues, no scattered globals.
3.  **Handshake-gated writes.** The master never fires SPI without confirming the slave's Ready line is HIGH. Skipping this causes display corruption. Non-negotiable.
4.  **Human-readable wire protocol.** Commands are pipe-delimited ASCII strings, not binary structs. Chosen for debuggability — any transfer can be read in a serial monitor without a decoder.
5.  **No ACK / fire-and-forget.** After the handshake is observed, the command is sent and the master advances state. There is no confirmation that the Inkplate rendered correctly. UI state always advances even if the display glitched.
6.  **Firmware is a dumb renderer.** All logic — state transitions, thread grouping, number formatting, message states, what to truncate — lives in Python on the Radxa. The Inkplate receives a fully-formed string and draws it. The only "logic" in `ui_screens.h` is layout and word-wrapping, and that mirrors the emulator's. Never put business logic in the `.ino`.
7.  **One command, one frame.** A screen is at most 253 characters, so the Python side decides what fits (windowing, shortening, dropping the oldest bubble) and never sends a frame the firmware would have to cut.
8.  **A no-op is never silent.** Input the phone will not act on raises a stop alert. The only deliberate silence is a rejected keystroke.

### **Process principles**
How we work on this project.

1.  **New entry point over modifying legacy files.** When a major rework is needed, write a new file rather than patching the old one. (`kyphone_os.py` replaced `kyphone_app.py`, which is no longer tracked.)
2.  **Simulator first, then the host-side firmware render, then the panel.** Every screen is fully navigable via `--sim` before firmware work; the firmware's renderers are then built and looked at on a computer (`test_firmware_host.py`) before anything is flashed; the panel is the last check, using the USB preview.
3.  **TDD for the state machine.** State transitions are pure Python and can be tested without hardware. Patch `push_screen` to intercept SPI calls.
4.  **The design is measured, not eyeballed.** Layout numbers come from `GEOMETRY.md` and from measuring the designer's 600×600 captures; every screen is compared side by side with its capture, and pixel tests pin the geometry.
5.  **Rollback safety.** Take a full flash dump of the Inkplate and a copy of the Radxa's code and data before changing either; the old renderers live in git history rather than beside the new ones.
6.  **Privacy by architecture.** Phone numbers and credentials never touch git. The public repo contains zero PII; test data is fictional.
7.  **Exact-count framing, no silent corruption.** The SCLK-timeout approach only accepts a transfer if exactly 2048 bits arrived. Partial or noisy frames are discarded. No CRC yet — if one is added, it goes here, not in the renderer.

### **Known constraints / gotchas**
Non-obvious facts that will bite future maintainers if undocumented.

*   **`reclaim_pin15_for_gpio()` must be called after every `display.display()`**, not just at startup. The Inkplate library re-steals Pin 15 on every refresh. The firmware already does this — do not remove it.
*   **`PAYLOAD_BYTES = 256` must stay in sync** between `kyphone_os.py` and the `.ino` (and `MAX_COMMAND_CHARS` = 253 follows from it). There is no compile-time check.
*   **Wire changes need every layer at once:** `kyphone_os.py`, `simulator.py`, `ui_screens.h`, the tests, and the wire table above. Several things exist in more than one place and are guarded by tests: `wrap_words` (`kyphone_os.py` and `simulator.py`, asserted equal; `ui_screens.h` covered by the host geometry tests), the home menu order (`HOME_MENU`, the simulator's list, `labels[]` in `ui_screens.h`, `make_icons.ORDER`), and the generated icon files (`make_icons.py --check`).
*   **The reader geometry lives in three places** — `reader_layout.py` (`TEXT_X`, `TOP`, the baseline rule, footer rows), `ui_reader.h` (`READER_*`) and the emulator (which uses `reader_layout`) — with no compile-time link. `test_firmware_host.py` compares firmware pixels to the layout module's glyph ink and to the emulator's frame, so a drift fails a test. Change all three together, and flash the firmware together with the Python: an old firmware draws `RTEXT` frames as stray text.
*   **Vendored font headers must not `#include <Adafruit_GFX.h>`.** That include makes the Arduino build link the standalone Adafruit GFX library next to the copy `Inkplate.h` bundles (duplicate-symbol link errors). The four FreeSerif headers have that one line replaced by a comment.
*   **`~/Documents/Arduino/libraries` on the Mac mini can be iCloud-evicted** ("dataless" files that fail with `Operation timed out`; seen with `Adafruit_MonoOLED.cpp/.h`). The reader firmware does not pull that library in, so `flash_macmini.sh` still compiles; if a build ever complains about a library file timing out, open the file in Finder to make macOS download it.
*   **Music: never call a `Session` method while holding `state['lock']`.** The session calls back into the OS (which takes that lock) from whatever thread caused the change, including GStreamer's GLib thread; the session itself holds no lock during a callback.
*   **ALSA's `null` device is not paced**: it "plays" everything instantly, so it makes every track end at once. For a silent test that keeps real time use `KYPHONE_AUDIO_DEVICE=fake`.
*   **Music wire changes ship with the firmware.** An old firmware draws `MUSIC` / `TRACKS` / `NOWPLAYING` as stray text (as with the reader's frames); flash and deploy together.
*   **`state['lock']` is not re-entrant.** Never call a helper that takes it (`_contact_view`, `_thread_messages`, `resolve_peer`, `get_threads`, …) from inside a `with state['lock']:` block — that deadlocks. It happened twice during development; compute the value first, then take the lock.
*   **Importing `kyphone_os` touches `data/`** (it loads contacts and may migrate/rewrite the file), and the simulator reads and writes it too. Set `KYPHONE_DATA_DIR` to a scratch folder for tests and experiments; it is read once at import, so it must be set before the process starts.
*   **Closing the Inkplate's USB serial port resets it** (the DTR/RTS pulse), so the preview tool leaves the board rebooting after its last screen; the panel keeps its image. Open the port with DTR and RTS off, as `serial_log_macmini.py` does.
*   **The middot separator is one byte, 0xB7** (latin-1). Anything above 255 in a payload would be garbled by the SPI byte list, which is why text is sanitized before it is sent.
*   **e-ink refresh:** the first screen after boot is a full refresh; otherwise partial, with a full refresh every 10 minutes to clear ghosting.
*   **The lock screen's ASCII cat is a fixed 1-bit bitmap (`cat_bitmap[]`, `drawBitmap()`), not live `display.print()` text.** Live text rendering of the cat's dense punctuation was visually distorting on real hardware (root cause never fully identified). It was generated via PIL (Courier Bold, `stroke_width=1`); if the art needs to change, regenerate the byte array, don't hand-edit it. Its position is computed from `CAT_BITMAP_W`/`CAT_BITMAP_H`, not hardcoded.
*   **`HOME2` / `THREAD2` names** date from the OS 0.0→0.1 rollout; their formats changed in 0.2.1 and the names were kept. They can be renamed in a future firmware change.

### **Product principles**
The device exists to be a revolt against the attention economy, not just a "dumb phone."

1.  **Presence over connection.** The phone is for people who are *around*, not people who aren't. Features that pull attention toward the absent (feeds, notifications, algorithmic content) are explicitly out of scope.
2.  **Minimalist but expressive.** Not a sterile black/white brick. The UI should feel handcrafted — pixel icons, ASCII art, AIM-style bubbles, real typographic choices. Limitations are a canvas, not a constraint.
3.  **Physical intent.** Hardware features should communicate social state (e.g., a "open for a chat" button). The device should be readable in sunlight and usable with presence, not against it.
4.  **Artist collaborations.** Limited editions (e.g., Tombolo) make the device a statement piece. The industrial design should be as considered as the software.
5.  **Limitations spark creativity.** E-ink refresh rate, 256-byte payloads, a 600px screen — these are the medium, not obstacles. Work with them.

## 9. Product Inspiration
KyPhone is more than a technical exercise; it is a revolt against the attention economy.

*   **The Problem:** Smartphones are designed to keep us connected to people who *aren't* around, often at the expense of those who *are*. They are purveyors of "social time" that cannibalize real-world presence.
*   **The Vision:** A "Minimal Phone" (not just a "Dumb Phone"). Moving away from the sterile black/white brick design toward something expressive, intentional, and worth owning.
*   **Current Priority:** OS 0.2.1 is built and mostly on the devices (see `planning/os-0.2.1-build-plan.md`); the **reader** and the **music player** are built and tested on a computer (`planning/reader-build-plan.md`, `planning/music-build-plan.md`) but not flashed. Next, at the phone: flash the firmware (icons, reader, music), copy the Python to the Radxa, put a book in `data/books/` and music in `data/music/`, click through every screen with the Bluetooth keyboard connected, time real page turns, and **listen** (headphones, low volume first; prove each audio format); push and merge the branches; then a cellular modem so a send can actually leave the phone.
