# **KyPhone**

KyPhone is a minimal phone designed to revolt against the attention economy. It uses a **Radxa Rock 3A** (SBC running Linux) as the logic master and an **Inkplate 4 TEMPERA** (ESP32-based E-ink display) as the peripheral. The goal is a device that gets out of the way of real-world connection.

> *"Moving away from the sterile black/white brick design. A device that sparks creativity and real connection."*

---

## **Current Status — September 2026**

**Current version: 0.4.0** (defined once, in `spi_bridge/version.py`; the lock screen shows it as "OS 0.4.0"). **Design library: 0.2.1** — the reader and music screens are newer than it and are not in it yet.

A Python state machine on the Radxa draws every screen on the Inkplate over SPI, and a pygame emulator runs the same screens on a Mac so the UI can be built and tested without hardware. The work is on the `music-build` branch (which includes `os-0.2.1-build` and `reader-build`) until it is merged.

### **What works today**
- **SPI transport:** Radxa → Inkplate over 3-wire software SPI plus a handshake pin, 256-byte frames. Every screen is one command of at most 253 characters.
- **Texting UI:** conversation list, threads, New Message (type a number or pick a contact), and per-message states — SENDING, SENT, NOT SENT with retry from the thread.
- **Contacts:** look up by name, create, edit and delete (with a confirmation), duplicate and bad-number checks, numbers stored as `(555) 010-0001` and matched by their last ten digits.
- **Stop alerts:** anything the phone will not do (READ and LISTEN aren't built yet, an empty message, no recipient) says so on screen instead of doing nothing.
- **Home menu:** TEXT · CALL · READ · LISTEN · CONTACTS (texts, calls, books, music, address book) with pixel-art icons.
- **Music (built, not yet on the phone):** LISTEN browses the music files in `data/music/` by album and plays them through the Radxa's headphone jack with GStreamer: play/pause, next/previous, seek, volume, keeps playing in the background (the home menu shows a small mark), remembers volume and where you stopped. Bluetooth headphones are next.
- **Reader (built, not yet on the phone):** READ opens a library of the `.epub` files in `data/books/`; a book is read a page at a time with four font sizes (FreeSerif 9/12/18/24pt), remembers where you stopped, and refreshes the panel in full every few turns to clear ghosting. Text only for now.
- **Long lists and long text:** lists are windowed and the composer shows the end of a long draft, so nothing is cut mid-word or overflows a frame.
- **Auto-start on boot:** the `kyphone` systemd service launches the OS on the Radxa.
- **Emulator:** the pygame emulator, run with the `--sim` flag as shown under Running it below, mirrors the panel and is what the tests drive.

### **What does not work yet**
- **Nothing can actually be sent yet.** Texts go through a cellular modem (a SIM7600G-H USB dongle); until it is plugged into the Radxa with an active SIM, a sent message ends as NOT SENT. That is the phone's normal outcome for now. (Twilio, the old way, has been removed.)
- **Calls are simulated.** The CALL screens exist but there is no telephony.
- **READ and LISTEN** (books and music) work in the emulator and on a computer-built copy of the firmware, but have not been flashed to the panel yet, and the music has not been heard through the headphone jack.

### **Running it**
```bash
# On Radxa (hardware — auto-starts via systemd, or manually:)
sudo systemctl restart kyphone

# On Mac (emulator) — no hardware and no credentials needed
pip3 install pygame
KYPHONE_DATA_DIR=$(mktemp -d) python3 spi_bridge/kyphone_os.py --sim     # a scratch data folder; put .epub files in its books/ folder to try READ
#   KYPHONE_SIM_SEND=sent        make the fake radio succeed (default: not sent)
#   KYPHONE_HOME_STYLE=icons|both|words

# Books: copy .epub files into data/books/ (any Project Gutenberg EPUB works), then READ opens them.
# Reader keys: right/down/enter/space = next page, left/up = back, + / - = font size, esc = library.
# Music: put audio files in the music/ folder of the data folder, then LISTEN. Keys: space = play/pause, right/left = next/previous,
#        up/down or + / - = volume, . and , = seek 15 s, esc = back (the music keeps playing). No sound in the emulator: it keeps time silently.

# Tests (name the files; do not point pytest at the whole tests/ folder)
pip3 install pytest
KYPHONE_DATA_DIR=$(mktemp -d) python3 -m pytest spi_bridge/tests/test_state_machine.py \
  spi_bridge/tests/test_reader_state.py spi_bridge/tests/test_reader_epub.py \
  spi_bridge/tests/test_reader_fonts.py spi_bridge/tests/test_reader_layout.py \
  spi_bridge/tests/test_music_library.py spi_bridge/tests/test_music_player.py spi_bridge/tests/test_music_state.py \
  spi_bridge/tests/test_simulator.py spi_bridge/tests/test_firmware_host.py spi_bridge/tests/test_version.py \
  spi_bridge/tests/test_network_control.py spi_bridge/tests/test_modem.py   # expect 772 passed
```
The emulator and the tests read and write the `data/` folder beside `spi_bridge/`; set `KYPHONE_DATA_DIR` to a scratch folder (as above) to keep your real contacts and messages out of it. Full technical detail — wire protocol, firmware, deploy and rollback steps — is in [`CLAUDE.md`](CLAUDE.md). The design spec is `docs/02-design/design_handoff_os_0_2/` and the build log is `planning/os-0.2.1-build-plan.md`.

### **What's next**
- Try the music player with headphones and real files; check each audio format; time page turns after the 0.3.1 speed-up
- SIM7600G-H cellular modem (direct AT commands), so a send can leave the phone
- BlackBerry Q10 keyboard integration
- Enclosure + battery design

---

## **Development Log**

### **September 2026: music**

A music player behind LISTEN, built and tested on a computer (branch `music-build`); not flashed and not yet heard.

- `music_library.py` reads MP3, FLAC, Ogg/Opus, M4A and WAV tags and lengths with the standard library only (cover art is skipped), groups albums, and caches what it read.
- `music_player.py` has the playback rules in a `Session` (next, previous, seek, volume, skipping a bad file) over a silent simulated player and a GStreamer player for the phone; the real player was also run on the Radxa against a silent output.
- Music keeps playing in the background; the home menu shows a mark; a 30-second ticker keeps the now-playing time honest without hammering the e-ink.
- Volume and where you stopped are remembered; after a restart LISTEN offers RESUME.
- 163 new tests; every decoder needed is on the Radxa; nothing new to install for the headphone jack.

### **September 2026: the reader**

An EPUB reader, built and tested on a computer (branch `reader-build`); not flashed yet.

- `reader_epub.py` reads EPUB 2 and 3 with the standard library only and loads chapters lazily: a 2.6-million-character novel opens in 0.09 s on the Radxa.
- `reader_layout.py` wraps text with the exact glyph widths the panel uses and paginates each chapter; a saved place survives a change of font size.
- The Inkplate draws pages with the FreeSerif fonts from a new `ui_reader.h`; a page is several SPI frames and the panel refreshes only on the last.
- The emulator draws the same glyph bitmaps, and the tests show the firmware, the layout module and the emulator agree pixel for pixel at all four sizes.
- 386 new tests (639 in all, counting the reader and the music player); checked against real Project Gutenberg books (Alice in Wonderland, The Count of Monte Cristo).

### **September 2026: OS 0.2.1**

Tested texting, creating a contact and deleting a contact in the emulator, took the gaps to Claude Design, and built the resulting design.

- Sent messages now stay in the thread with SENDING / SENT / NOT SENT states, and NOT SENT can be retried.
- Contacts can be created and deleted from the UI; before, deletion was only possible by editing `contacts.json` by hand.
- One confirmation screen for every destructive choice, and one stop-alert screen for everything the phone will not do.
- Lists are windowed and text is kept inside the 253-character frame, so a long thread or contact list can no longer be cut off.
- The Inkplate firmware draws the new screens and has a USB preview (`@<command>` on the serial port) for checking a screen on the panel without the Radxa.
- Home menu icons (pixelarticons, MIT) generated from the design's SVG paths.
- There are now 639 tests, including pixel checks on the emulator and a host-side build of the firmware renderers under sanitizers.
- Twilio switched off (nothing costs money); sends end as NOT SENT until there is a modem.

### **April 2026: UI Polish + Dev Workflow**

Full UI redesign pass and developer tooling improvements.

- MSG_THREAD redesigned to AIM-style (`Name: body`) — cleaner than bubble approach for E-ink
- MSG_LIST header navigation: `<` and `+` buttons focus and invert on keypress
- ASCII art added to HOME screen (top-right corner)
- Partial refresh on MSG_LIST navigation keypresses
- VS Code Remote SSH configured for direct editing on Radxa
- Payload expanded from 128 → 256 bytes; SCLK timeout adjusted to 1.5s
- SIM7600G-H 4G USB dongle ordered to replace Twilio

---

### **(Previous Milestones)**

### **Milestone 4: Conditional Screen Updates & Full Gestures**

The current system constantly refreshes the e-ink screen every few seconds, even if the Android UI is static. This is inefficient and creates a poor user experience. Our next goal is to make the e-ink display a true **synchronized mirror** of the Android UI, updating *only* when the UI *actually* changes.

* **Change Detection:** The Android app will be updated to compare the current frame to the last-sent frame. If they are identical, no data will be sent.
* **Differential Updates:** When a change *is* detected, the app will calculate a "diff" (the difference) and send *only* the changed pixels.
* **Partial Refresh:** The Inkplate firmware will be updated to receive these partial "diff" commands and draw them directly, allowing for fast, flash-free updates for small UI changes.

### **Milestone 3: Live Touchscreen Integration Complete**

The goal of a stable, live-touch bidirectional link is complete. The system now reliably mirrors the Android screen to the hardware and forwards **real** user taps from the Inkplate's touchscreen controller back to the emulator to trigger live taps.

* **Live Touch:** The Inkplate firmware is now non-blocking and sends real `TAP:X,Y` coordinates from the touchscreen controller.
* **App-Side Parsing:** The Android `AccessibilityService` correctly parses the `TAP:X,Y` protocol and injects live tap events.
* **`adb`-Free:** The `adb reverse` dependency has been eliminated by updating the app to connect directly to the host IP (`10.0.2.2`).

### **Milestone 2: Tap Injection Moved to Android App**

The architecture has been refactored to remove the `adb` dependency. All tap injection logic, which was previously handled by the Python proxy, has been moved into the Android application itself.

* **No More `adb`:** The `proxy.py` script no longer uses `subprocess` to call `adb`. It is now a simple data bridge that forwards image data and coordinate strings.
* **Accessibility Service:** The `ScreenCaptureService` has been refactored into an `AccessibilityService`.
* **Live App-Side Taps:** The service now listens for coordinates from the proxy and uses `dispatchGesture()` to inject system-wide taps directly, completing the new control loop (`Inkplate` → `Proxy` → `App` → `Android System`).

### **Milestone 1: Bidirectional MVP Complete**

The initial goal of a stable, bidirectional link is complete. The system now reliably mirrors the Android screen to the hardware and forwards mock touch events from the hardware back to the emulator to trigger live taps.

* **Reliable, Timed Updates:** The full pipeline (App → Proxy → Inkplate) works reliably.
* **Bidirectional Communication:** The Inkplate sends mock coordinates back to the proxy.
* **Live Touch Interaction:** The proxy uses `adb` to inject the received coordinates as tap events into the Android emulator, creating a live demo loop.

---

## **How It Works**

The system uses a network proxy bridge to connect the Android emulator to the physical hardware.

* **The Android App (Client):** The KyPhone UI runs in the emulator. An `AccessibilityService` performs three tasks:
    1.  **Change Detection:** Captures the display and compares pixels to the previous frame.
    2.  **Transmission:** Sends image data to the Host IP (`10.0.2.2`) only if changes are detected.
    3.  **Input:** Listens for `DOWN`, `DRAG`, and `UP` commands and injects them as system gestures.
* **`proxy.py` (Server/Bridge):** A Python script running on the host computer. It accepts a TCP connection from the Emulator and a Serial connection from the Inkplate, forwarding data between them.
* **`inkplate_touch_simulator.ino` (Device Firmware):** An Arduino sketch running on the Inkplate. It renders received images to the screen and reads touch input, sending coordinate data over the USB Serial connection.

---

## **Setup & Usage**

The system requires three components to be running simultaneously in the correct order.

### **1\. Configure the Inkplate**

Open `inkplate_touch_simulator/inkplate_touch_simulator.ino` in the Arduino IDE and upload the latest sketch.

### **2\. Prepare the Host Computer (Terminal)**

**Start the Proxy Server:** Navigate to your project folder and run:  
Bash  
python3 ./proxy.py

* 

**Start Port Forwarding:** In a *second terminal*, navigate to your Android SDK's `platform-tools` directory and run:  
Bash  
./adb reverse tcp:65432 tcp:65432

* 

### **3\. Run the App (Android Studio)**

* Open the KyPhone project, start the emulator, and run the app.  
* One-Time Setup: Go to Settings > Accessibility > KyPhone and enable the "Use KyPhone" toggle. You must grant the permission for the service to inject taps.
* Tap "Start Screen Mirroring" and grant screen capture permission.

---

## **Development Log**

### **March 30, 2026: KyPhoneOS — Full SMS App Running on Hardware**

The SPI transport is now a solved problem. This session was entirely product: a full phone OS running on the Radxa, pushing semantic screen commands to the Inkplate over the proven SPI bridge.

**What got built:**
- **KyPhone OS app** (`kyphone_app.py`): Replaces the old test scripts. Polls Twilio every 2 seconds for inbound SMS, manages navigation state, and pushes screen updates to the Inkplate in real time.
- **Navigation state machine:** Three screens — HOME, MSG_LIST, MSG_THREAD — with full keyboard navigation via evdev. Arrow keys + Enter move between screens; messages mark as read when opened.
- **Message persistence:** Conversations saved to `data/messages.json` on disk. Survive reboots. SID tracking prevents replaying old messages on startup.
- **Partial clock updates:** Home screen refreshes the clock every minute using `HOME_FAST` + `partialUpdate()` instead of a full E-ink flash. Noticeably faster.
- **Pygame simulator:** Added `--sim` flag to run the full app locally on Mac — no Inkplate or Radxa needed. A 600x600 pygame window mimics the display layout exactly, dramatically accelerating UI development and iteration.
- **Home screen redesign:** Replaced the single YAP circle button with a 4-button row — TEXT, CALL, READ, LISTEN — grouped under YAP (communication) and CHILL (media) labels. LEFT/RIGHT navigate, ENTER activates.

**Key decisions:**
- Twilio for SMS instead of a modem for now. 10DLC campaign registration submitted for the KyPhone number — pending approval before outbound replies work reliably.
- BlackBerry Q10 keyboard ordered (white BLE+USB, AliExpress, ~$74) for physical input. Arriving May. Until then, USB keyboard or simulator.
- Cellular modem deferred until the device has a battery and enclosure.

---

### **March 23, 2026: SPI Transport Proven — "hello world" on E-ink**

Solved the SPI communication layer completely. Key breakthroughs:

* **IO_MUX reclaim:** `Inkplate.h` calls `SPI.begin()` which reassigns GPIO 13 (MOSI) and GPIO 15 (CS) to the hardware SPI peripheral at the IO_MUX level, bypassing the GPIO Matrix. `attachInterrupt()` is blind to pins in IO_MUX mode. Fix: call `PIN_FUNC_SELECT(IO_MUX_GPIO13_REG, 2)` and `PIN_FUNC_SELECT(IO_MUX_GPIO15_REG, 2)` after `display.begin()` to force both pins back to GPIO mode.
* **CS abandoned:** GPIO 15 is the ESP32 MTDO strapping pin. The display controller PCB traces hold it LOW permanently. Switched to SCLK-timeout framing — 500ms of silence after the last clock edge signals end-of-message.
* **Partial accumulation:** The Rockchip SPI DMA at 5kHz delivers all 272 bits in one 54ms burst, but we accumulated bits across multiple attempts while debugging. Current firmware never resets `bit_counter` on a partial timeout — it just waits for more bits.
* **30-second bottleneck diagnosed:** `timing_baseline.py` revealed the Radxa sends all bits in 54ms, but the firmware waited 30 seconds before evaluating. Reducing the timeout to 500ms dropped end-to-end latency from ~2 minutes to 1.7 seconds.
* **Payload expanded:** 34 → 128 bytes. Enables full SMS message display.

---

## Archive — Pre-SPI Era (Aug–Nov 2025)

*In mid-2025 the project used a completely different architecture: an Android emulator running a KyPhone UI app, with a Python proxy bridging screen data over TCP to the Inkplate via USB serial. The project pivoted to the current native Linux/SPI approach in early 2026. Entries below document that phase in full.*

---

### **March 15, 2026: Phase 1 - The SPI Handshake Nightmare (UNSTABLE)**

Achieved the first *sporadic* text transfers between the Radxa Rock 3A and the Inkplate 4 TEMPERA. While a physical link was established, the system remains **highly unreliable and is currently a "one-hit wonder"**—it typically works for exactly one message and then fails consistently on all subsequent attempts until a hard reset.

**The Current (Unstable) Solution: Synchronous Payload Polling**
We moved away from "Blind Blasting" to a two-way conversation, but it is not yet robust:
*   **Physical Change:** Connected the **Yellow Wire (MISO)** from Radxa Pin 21 to Inkplate IO 12 to enable bidirectional communication.
*   **The ACK Strategy:** The Inkplate pre-loads its SPI outbox with `0x06` (ACK) when ready. The Radxa sends the full 34-byte message as a "poll" and checks the received first byte for the ACK.
*   **Smart Scan:** The Inkplate firmware now scans the entire 34-byte buffer for the `0x02` header to handle shifting offsets.
*   **Sync Logic:** Added `[0x00, 0x00]` dummy bytes to satisfy hardware wake-up lag.

**Current Failure Point:** After the first successful display update, the ESP32 SPI slave consistently fails to re-synchronize after the E-ink refresh. The system is currently stuck in a state where it cannot receive multiple messages in a row.

### **November 28, 2025: Milestone 4 - Conditional Screen Updates & Full Gestures**

Successfully integrated the Inkplate's real touchscreen controller, completing the emulator-to-hardware loop. Replaced the mock coordinate system with a robust, non-blocking hardware loop.

* **Firmware:** Upgraded `inkplate_touch_simulator.ino` to be fully non-blocking. It now handles simultaneous image receiving and touch polling, capturing all tap events without dropping input.
* **Android App:** Updated `ScreenCaptureService.kt` to parse the new `TAP:X,Y` string protocol, removing the parsing crash.

### **November 10, 2025: Milestone 2 - Tap Injection Refactored to Android**

Successfully refactored the architecture to move all tap injection logic from the Python proxy into the Android app. This removes the `adb` dependency and is a major step toward the final on-device software.

* **Proxy Server:** Removed `adb` and `subprocess` logic. The proxy is now a simple, fast data bridge.
* **Android App:** Refactored `ScreenCaptureService` into an `AccessibilityService`. The service now manages the socket connection (sending images and receiving coordinates) and uses `dispatchGesture()` to inject system-wide taps.

### **September 25, 2025: Bidirectional MVP Stabilized**

Completed a major debugging effort to achieve a stable, bidirectional link between the Android app and the Inkplate. This involved fixing several layered issues across the entire stack.

* **Firmware:** Resolved a critical out-of-memory crash on the ESP32 by replacing the `drawBitmap()` function with a memory-efficient `drawPixel()` loop. This prevents the device from crashing when receiving the 45KB image payload. Also fixed a subsequent bug that caused garbled screens by ensuring both black and white pixels were drawn.  
* **Proxy Server:** Implemented a buffering mechanism to handle TCP stream chunking, ensuring the full 45KB image is received from the app before being forwarded to the Inkplate.  
* **Protocol:** Implemented a robust request/acknowledgment (ACK) protocol between all three components to fix a series of race conditions, deadlocks, and uncontrolled loops that were preventing reliable, continuous screen updates.  
* **Project Cleanup:** Archived all legacy scripts and firmware from previous development stages into an `_archive` directory to clean up the main project structure.

### **September 14, 2025: Milestone 1 Complete \- Reliable PoC Achieved**

Successfully debugged and stabilized the entire emulator-to-hardware pipeline. The system now reliably mirrors the Android screen to the Inkplate with timed updates.

* **Firmware:** Fixed a critical serial buffer overflow on the ESP32 by implementing a chunked data reading loop in the `.ino` sketch.  
* **Proxy Server:** Resolved network race conditions by implementing an ACK protocol.  
* **Android App:** Fixed multiple issues, including an event-driven screen capture that failed on static screens.  
* **Visuals:** Corrected color inversion and aspect ratio distortion.

### **September 11, 2025: Investigating Continuous Mirroring Bug**

* **Issue:** While the first frame is transmitted and displayed correctly, all subsequent frames fail to appear on the Inkplate hardware.

### **September 10, 2025: Android Prototyping and Emulator-to-Hardware Bridge**

* **Key Milestones Achieved:** Android Dev Environment Setup, Emulator-to-Hardware Bridge Architecture, Live Screen Mirroring (Polling Method).

### **August 30, 2025**

* **Investigated MuditaOS** as an alternative to a custom Linux/Android build.  
* **Decision:** The best path forward is to stick with the Rock 3A and build our custom UI on top of a minimal Linux or AOSP build.

---

## **Appendix: Original Python-Only Demo (Legacy)**

The following documentation describes the initial, legacy milestone of the project. All of these files are now in the `_archive` folder.

* `image_sender.py` (Client/Host): Python script that prepared images and managed communication.  
* `inkplate_receiver.ino` (Server/Device): Arduino sketch for the Inkplate that used `drawBitmap` to render images.
