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

### **State machine**
`kyphone_os.py` is the production entry point. One `state['screen']` string drives all rendering; every mutable value lives in the single `state` dict behind one lock.

Screens: `lock · home · texts_list · thread · compose · confirm · contacts_pick · contact · contact_edit · stub · calls_list · dial · outgoing · incoming · in_call`. `stub` is the one **stop alert** screen (unbuilt features, validation, empty send, no recipient); `confirm` is the one **confirmation** screen (discard message, delete contact).

Selection convention: a list's `*_index` is an absolute position, `-1` = the header (with `*_header_sel` = `back`/`plus`). Lists are windowed (`texts_start`, `contacts_start`, `calls_start`); the wire carries the selection **relative to the window**.

### **Data model**
*   **Messages** (`data/messages.json`). Incoming: `{sender, name, body, read, ts}`. Outgoing: `{dir:'out', peer, name:'You', body, read, ts, state}` with `state` = `sending | sent | not_sent`. A thread is keyed on the **other party's** number (`peer_of()`); the phone never shows or needs its own number. Old outgoing records with no recipient are hidden, not deleted.
*   **Contacts** (`data/contacts.json`): a list of `{first, last, number}`, **identified by position, never by display name** (two contacts can share a name). Numbers are stored formatted, `(555) 010-0001`, and matched by their last ten digits (`same_number`), so `+15550100001`, `1 555 010 0001` and `(555) 010-0001` are one person. New contacts sort alphabetically.
*   **Numbers**: `format_number` (never truncated — 14 chars), `same_number`, `normalize_number` (`+1XXXXXXXXXX`, what the radio is handed), `number_valid` (ten digits, or eleven starting with 1).

### **Behaviour, by screen**
| Screen | Keys |
| :--- | :--- |
| lock | any → home |
| home | ↑↓ move (−1 = header; Enter there → lock). Enter opens TEXT / CALL / CONTACTS (list windows reset) or a stop alert for READ / LISTEN. Esc → lock. `i` = incoming-call demo. Menu order: TEXT, CALL, CONTACTS, READ, LISTEN |
| texts_list | ↑↓; ↑ past the first row → header (←→ back/plus); Enter → thread (marks read); `+` or header plus → compose; Esc → home |
| thread | typing edits the draft; Enter sends (empty → alert). ↑ from the composer selects the newest **NOT SENT** bubble (else the header); Enter on it **retries**; ↑ again → header; ↓ → composer. Header ←→ back/info; info → contact page. Esc → texts_list |
| compose | Tab toggles TO/MESSAGE. ↑ walks SEND → MESSAGE → TO → the X in the header; ↓ MESSAGE → SEND. Enter: TO empty → contact picker, TO set → MESSAGE, MESSAGE or SEND → send (alerts if no recipient / empty message). `+` beside an empty TO opens the picker. Esc with a draft → discard confirm. TO holds 20 chars; the message is uncapped |
| confirm | ← destructive button, → safe button; **opens on the safe one**; Esc is the safe choice; Enter acts on the selection |
| contacts_pick | typing filters by name prefix (LOOK UP); ↑↓; header back/plus; `+` → new contact; Enter → contact page (from home) or picks into compose |
| contact | top row `[back, edit]`, action row `[call, text]` (saved) · `[add number]` (saved, no number) · `[call, text, save]` (not in contacts, no EDIT). SAVE opens the form with the number filled in |
| contact_edit | ↑↓ through first / last / number / SAVE; ← from SAVE reaches **DELETE** (existing contacts only). Four validation alerts (first name, number, dialable, duplicate) return to the offending field. First is required, last optional. X/Esc return to where the form was opened; a save from the compose picker returns to compose with the contact in TO |
| stub | Enter/Esc → the screen it came from, state intact |
| calls_list / dial / outgoing / incoming / in_call | unchanged in 0.2.1 and **simulated** — there is no telephony until the cellular modem exists |

`Q`/`W`/`A`/`S`/`D` act as Esc and the arrows on screens where letters are not being typed.

### **Sending**
`send_reply()` stores the message as **SENDING…** and returns; a worker thread hands it to `_transport_send()` so the keyboard never waits on the network. With no modem and Twilio switched off, `_transport_send` raises `no service`, so **NOT SENT is the phone's normal outcome today**, not an error case. The path to Twilio still exists if credentials are set. In the simulator, `KYPHONE_SIM_SEND=sent` makes the fake radio succeed (default: not sent, like the phone).

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
| Home | `HOME2\|time\|index\|unread\|style` — index −1 header, 0 TEXT 1 CALL 2 CONTACTS 3 READ 4 LISTEN; style `I` icons (default) / `B` icons and words / `W` words |
| Texts | `TEXTS\|sel\|name·preview·unread·time\|…` — sel −1 back, −2 plus, else the row in the window; ≤5 rows; preview starts `You: ` or `! ` (unsent); no rows = empty state |
| Contacts | `CONTACTSPICK\|sel\|query\|3 / 14\|name·number\|…` — ≤7 rows; no rows = NO MATCH (query set) or NO CONTACTS |
| Calls | `CALLS\|sel\|name·tag·time·duration\|…` — ≤6 rows; the first list entry is `DIAL A NUMBER·NEW··` |
| Thread | `THREAD2\|name\|draft\|hdr\|code·time·text\|…` — hdr `''`/`B`/`I`; code `R` received, `Y0` sending, `Y1` sent, `Y2` not sent, `Y3` not sent and selected; ≤3 bubbles |
| Compose | `COMPOSE\|to\|msg\|to_active\|hdr\|plus_sel\|send_sel` |
| Contact | `CONTACT\|title\|sub\|kind\|sel` — kind `S` saved, `N` no number, `U` unsaved; sel `B E C T V A` |
| Contact form | `CONTACTEDIT\|first\|last\|number\|idx\|kind` — idx −1 cancel, 0–2 fields, 3 save, 4 delete; kind `N` new, `E` edit |
| Stop alert | `STUB\|title\|body` |
| Confirm | `CONFIRM\|title\|body\|go\|keep\|sel` — sel `D` / `K` |
| Call screens | `DIAL\|…`, `CALLSTATE\|OUT/IN/ACTIVE\|name\|mm:ss` (unchanged) |

### **Inkplate firmware**
*   **`ui_screens.h`** draws every OS 0.2.1 screen with the Adafruit GFX built-in font, ported from `simulator.py`. It depends only on `display.setCursor / setTextSize / setTextColor / print / fillRect / drawRect / drawBitmap`, so the same code also builds on a computer (below). **`ui_icons.h`** holds the home-menu bitmaps and is **generated** — edit `tools/make_icons.py`, never the header.
*   `Inkplate_SPI_Peripheral.ino` keeps the V4 transport, the lock screen (with its fixed cat bitmap), dial, call state and the OS 0.0 screens. Command handling is `handle_command()`, shared by the SPI link and a **USB preview**: a line `@<command>` on the USB serial port draws that screen as if the Radxa had sent it (`tools/preview_screens.py`), so screens can be checked on the panel without the Radxa.
*   **GFX geometry:** a size-N glyph is a 5×7 shape in a 6×8 cell scaled by N, so its baseline sits `7*N` below the cursor. `ui_text()` draws at a baseline, which is how the designer's measured baselines are used directly. Bold is printing twice, one pixel apart. The design's 18 px text is textSize 2 (16 px) on the device.
*   The receive buffer is `PAYLOAD_BYTES + 1` with a guaranteed terminator: a 253-character command fills all 256 bytes.

### **Tests and tools**
Three suites, 250 tests (`test_state_machine.py` 193, `test_simulator.py` 34, `test_firmware_host.py` 23):
*   **`test_state_machine.py`** — state transitions, wire strings, frame limits, sending/retry, contacts. Hardware mocked at import time; `push_screen` is patched to capture the SPI command.
*   **`test_simulator.py`** — pixel checks on real emulator frames (headless pygame): rows, rules, buttons, icons pixel-for-pixel, the icons against the designer's capture, the generator's output being up to date, and that `simulator.wrap_words` matches the OS's.
*   **`test_firmware_host.py`** — builds `ui_screens.h` for the computer with `tests/firmware_host/` (a fake display using the real GFX font) and checks exact geometry, that firmware and emulator agree on every rule and inverted row, and memory safety (thousands of malformed and maximum-length commands under the address and undefined-behaviour sanitizers). Needs `clang++` and Adafruit_GFX's `glcdfont.c`; skips otherwise.

```
python3 -m pytest spi_bridge/tests/test_simulator.py spi_bridge/tests/test_firmware_host.py spi_bridge/tests/test_state_machine.py
```
Name the three files — **do not point pytest at the whole `tests/` folder**: the hardware diagnostic scripts there run on import. **Keep this order:** `test_state_machine.py` puts a fake pygame in `sys.modules` if pygame is not loaded yet, so if it is collected first the simulator and firmware tests skip silently ("220 passed, 30 skipped" instead of "250 passed"). Check the total. The simulator and firmware suites need `pygame`; use a virtualenv (`pip install pygame pytest`).

**Run tests on a copy of `spi_bridge/` with an empty `data/`**, not in place: importing `kyphone_os` loads (and can rewrite) `data/contacts.json`. The same applies to the simulator, which reads and writes the real `data/` — see Known constraints.

Other tools: `tools/make_icons.py [--check]` (icon bitmaps), `tools/preview_screens.py` (draw screens on the real panel over USB; use the system `/usr/bin/python3`, which has pyserial), `flash_macmini.sh` (compile and flash from the Mac mini), `serial_log_macmini.py` (streams the Inkplate's serial output to `/tmp/inkplate_serial.log`).

### **Simulator**
```
python3 spi_bridge/kyphone_os.py --sim
```
Renders every screen in a 600×600 pygame window with full keyboard navigation. Environment: `KYPHONE_SIM_SEND=sent|not_sent`, `KYPHONE_HOME_STYLE=icons|both|words`. The emulator's text is narrower than the panel's fixed 6×8-cell font, so wrapping follows the device figures (composer 30 columns, bubbles 20) rather than the font.

### **Deploying**
*   **Radxa:** systemd `kyphone.service` runs `spi_bridge/kyphone_os.py` as root from `~/kyphone`. To update: copy `kyphone_os.py`, `simulator.py`, `home_icons.py`; `sudo systemctl restart kyphone`; check `journalctl -u kyphone` and `/tmp/inkplate_serial.log` (the Inkplate logs each command it receives). The Radxa's git clone is not kept in step — files are copied in.
*   **Inkplate:** `flash_macmini.sh`, or write only the app image at `0x10000` with esptool (the bootloader and partition table do not change). **Stop `serial_log_macmini.py` first** — it holds the port — and start it again afterwards.
*   **Always back up both first.** Inkplate: `esptool read_flash 0 0x400000 <file>` gives an exact 4 MB rollback (about 6 minutes at 115200). Radxa: copy `spi_bridge/`, `data/` and the unit file. Python and firmware must ship **together** when a wire format changes.

### **Security / privacy constraints**
*   Phone numbers live only on the Radxa in gitignored `data/contacts.json` and `data/messages.json` — never committed. Tests and screenshots use fictional `(555) 01x-xxxx` numbers.
*   **Twilio is switched off** on the Radxa: `TWILIO_SID` and `TWILIO_TOKEN` are commented out in `kyphone.service` and `start_kyphone.sh` (backups `*.bak-2026-09-18`). `TWILIO_NUMBER` must stay set — the module exits without it. Credentials never belong in git or in this file; rotate any that have appeared in a transcript.
*   `kyphone_app.py` (OS 0.0, demo mode) is untracked and gitignored, and the OS 0.2 code has no demo mode.

## 7. File & Directory Map
*   `spi_bridge/kyphone_os.py` — production entry point and state machine.
*   `spi_bridge/simulator.py` — pygame simulator (`--sim`); mirrors the firmware's screens. `spi_bridge/home_icons.py` — generated icon bitmaps for it.
*   `spi_bridge/input_handler.py`, `trackpad_handler.py` — evdev keyboard and trackpad readers; forward nav keys and `CHAR:<c>`.
*   `spi_bridge/Inkplate_SPI_Peripheral/Inkplate_SPI_Peripheral.ino` — firmware: transport, lock screen, dial/call state, `handle_command()`, USB preview. `ui_screens.h` — the OS 0.2.1 renderers. `ui_icons.h` — generated bitmaps.
*   `spi_bridge/tools/` — `make_icons.py`, `preview_screens.py`. `spi_bridge/assets/` — the icon licence notice.
*   `spi_bridge/tests/` — `test_state_machine.py`, `test_simulator.py`, `test_firmware_host.py`, `firmware_host/` (fake display, host renderer, canned screens); the older hardware diagnostics (`Signal_Detector.ino`, `wire_verifier.py`, …) are not unit tests.
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
*   **`state['lock']` is not re-entrant.** Never call a helper that takes it (`_contact_view`, `_thread_messages`, `resolve_peer`, `get_threads`, …) from inside a `with state['lock']:` block — that deadlocks. It happened twice during development; compute the value first, then take the lock.
*   **Importing `kyphone_os` touches `data/`** (it loads contacts and may migrate/rewrite the file). Run tests on a copy with an empty `data/`. The simulator also reads and writes the real `data/`. There is no data-directory override; adding one (an environment variable read where `data/` paths are built) would fix both.
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
*   **Current Priority:** OS 0.2.1 is built and mostly on the devices (see `planning/os-0.2.1-build-plan.md`). Next: flash the icon home menu; click through every screen on the real phone with the Bluetooth keyboard connected; push and merge the branch; then a cellular modem so a send can actually leave the phone.
