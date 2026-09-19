# KyPhone OS 0.2.1 — Build Plan
*Updated: September 18, 2026 · branch `os-0.2.1-build` · 16 local commits, nothing pushed · **Inkplate flashed and Radxa deployed (2026-09-18); the menu icons are built but not yet on the devices***

**Goal.** Bring the running UI (OS 0.2) up to the OS 0.2.1 design in
`docs/02-design/design_handoff_os_0_2/`, which answers the undefined behaviours found by click-testing 0.2 in
the emulator: lists that can't scroll, texts that vanish, no way to create or delete a contact, and so on.

**Approach.** Python state machine and `simulator.py` first, tested in the emulator against the designer's 600×600
captures. Firmware renderers (`Inkplate_SPI_Peripheral.ino`) come after, as one pass, because every changed screen also
changes what is sent to the Inkplate. **This branch must not be flashed or deployed until that pass is done.**

## Status at a glance

| # | Step | Status |
|---|---|---|
| 1 | Windowed lists: texts (5 rows), contacts (7), calls (6), footer counter, empty states | ✅ done |
| 2 | Sent messages stay in the thread; sending / sent / not sent; retry; non-blocking send | ✅ done |
| 3 | Thread composer (wraps to 3 lines, shows the end of a draft); character filter | ✅ done ¹ |
| 4 | Unsaved numbers shown formatted; 3 contact-page variants; contacts identified by position | ✅ done |
| 5 | New-contact form; four validation alerts; empty-send and no-recipient alerts; New Message screen limits | ✅ done |
| 6 | Delete a contact + confirm screen (default KEEP CONTACT) | ✅ done |
| 7 | Home menu reorder (CONTACTS third) | ✅ done — **superseded 2026-09-19**: Kyle chose TEXT, CALL, READ, LISTEN, CONTACTS (see `planning/reader-build-plan.md`) |
| 8 | Home menu icons (pixel bitmaps) | ✅ built, tested and compiled — **not yet flashed or deployed** (see below) |
| 9 | Name the call screens in a `kyphone_os.py` docstring | ✅ done |
| F | **Firmware pass** — Arduino renderers for every changed screen, flash, check on the real panel | ✅ done — flashed 2026-09-18; ten screens put up on the panel and photographed; layout as designed |
| D | Deploy the Python to the Radxa; docs (`CLAUDE.md`, wire tables) | ✅ **deployed 2026-09-18 19:17**; `CLAUDE.md` rewritten for OS 0.2.1 (README.md still describes OS 0.0 — not done) |

¹ The *thread* composer and the character rules came with step 3; the *New Message* screen's own limits (TO 20, message
uncapped and wrapped) came with step 5.

## Completed

| Commit | What |
|---|---|
| `6e5d1f7` | Design handoff added to `docs/02-design/` |
| `4db96ff` | **Step 1.** `window_start()` helper, one window rule for three lists; old 7-thread cap removed; rows shortened (never cut mid-field) when data would overflow the 253-char frame; simulator redrawn to `GEOMETRY.md` |
| `12e128c` | **Steps 2–3.** Outgoing messages stored as `{dir:'out', peer, state}` against the *other* party; background send; retry; `composer_view()`; `can_draw()` / `sanitize()`; thread redrawn |
| `0fc4d0e` | **Step 4.** `format_number` / `same_number` / `normalize_number`; contact page kinds S / N / U; `contact_idx` replaces name lookup |
| `7f3c5a8` | This plan |
| `4c61a50` | **Step 5.** `_open_new_contact`, validation with the offending field selected, `_show_alert` (reuses the stop-alert screen), compose limits, empty-send / no-recipient alerts, compose arrow-up fix |
| `1ab7f6d` | **Step 6.** DELETE on the edit form (arrow left from SAVE); one `confirm` screen for discard and delete (safe button right, selected on open); delete removes by position, conversations stay; shared 36px emulator button |
| `8e8ed4b` | **Steps 7 and 9.** Home order TEXT, CALL, CONTACTS, READ, LISTEN (dispatch by label); call screens named in the docstring; version strings 0.2.1 |
| `059e150` | **Firmware.** `ui_screens.h` (all changed screens on the GFX font), `handle_command()` shared by SPI and a USB `@command` preview, receive-buffer terminator; host-side harness + 19 tests incl. sanitizer fuzz; `tools/preview_screens.py` |

**Problems from the emulator test, now fixed:** texts and contacts lists ran off the screen with nothing highlighted ·
older conversations unreachable · sent texts vanished and a fake own-number conversation appeared · a slow send froze
the keyboard · you couldn't see what you were typing · `|` corrupted the screen · two unsaved senders looked identical ·
picking the second of two same-named contacts opened the first · `+` in contacts said "cannot be saved yet" · bad
input saved silently or did nothing · SEND with nothing to send did nothing · from SEND, arrow up jumped to the header.

## Tests and verification
- **235 tests pass** (51 at the start); 19 are the firmware host tests: `test_state_machine.py` (state, wire strings, frame limits, retry, contacts) and
  new `test_simulator.py` (pixel checks on real emulator frames, plus a check that the simulator's word-wrap matches the OS's).
- **Every step** is also click-tested in the real emulator with real key events, screenshots after each key, compared
  side by side with the designer's captures.
- Run tests on a scratch copy so real data is never touched, using `~/.venvs/kyphone/bin/python -m pytest
  spi_bridge/tests/test_state_machine.py spi_bridge/tests/test_simulator.py` (don't point pytest at the whole `tests/`
  folder — the hardware diagnostic scripts there run on import). `KYPHONE_SIM_SEND=sent` makes the emulator's fake radio succeed;
  the default is NOT SENT, which is what the phone does today.

## Remaining work

**The Python and emulator side of the build is complete** (steps 1–7 and 9). What remains is the firmware pass and the docs/deploy.

**Firmware pass (F) — done and flashed; deploy is next.**
- `ui_screens.h` draws every changed screen; the ten old renderers are gone from the `.ino` (2,421 → 1,438 lines). Compiles for the
  Inkplate 4 TEMPERA (372 KB, 11% of flash, 21% RAM).
- **Checked without the panel:** the renderers also build on a computer (`tests/firmware_host`) against a fake display with the real GFX
  font. Tests cover exact geometry, agreement with the emulator on every rule and inverted row, and memory safety (2,500 malformed
  commands under the address and undefined-behaviour sanitizers: none found).
- **Checked on the panel:** `tools/preview_screens.py` sends `@<command>` over USB serial and the board draws it; ten screens were
  put up and the board acknowledged each. **How they look to a person is for you to confirm.**
- **Flashed:** app section only (bootloader and partition table were byte-identical), 2026-09-18, hash verified.
- The Radxa service was stopped during the preview so its clock would not repaint the panel, then started again at deploy.
- **Rollbacks (both taken before touching anything):**
  - Inkplate: `~/kyphone-backups/inkplate-flash-2026-09-18.bin` (the full 4 MB as it was; sha256 alongside). Restore with
    `esptool --chip esp32 --port /dev/cu.usbserial-1140 --baud 115200 write_flash 0 <that file>`.
  - Radxa: `~/kyphone_backup_2026-09-18/` (deployed `spi_bridge`, `data`, `kyphone.service`). Restore: `sudo systemctl stop kyphone;
    cp -a ~/kyphone_backup_2026-09-18/spi_bridge_deployed/. ~/kyphone/spi_bridge/; sudo systemctl start kyphone`.
- **Deployed (2026-09-18 19:17).** Copied `kyphone_os.py` and `simulator.py` to `~/kyphone/spi_bridge/` on the Radxa. First ran the 191
  state-machine tests there on its own Python 3.9.2 (pass, in a scratch folder). Service `active`, 0 restarts, banner `KyPhone OS 0.2.1`;
  `contacts.json` / `messages.json` byte-identical to the backup. **End-to-end SPI check:** the Inkplate's own log shows it received the
  Radxa's `LOCK` command (2,048 bits, no errors), drew it, and reports screen `LOCK`.
- **Not yet exercised on the real phone:** navigating the new screens with a real keyboard. The Radxa logs `No keyboard found`; the
  Bluetooth keyboard has to be connected first. Twilio is still off, so a send ends as NOT SENT (by design).
- **The Radxa's git clone is not updated** (its working tree was copied into, on an old commit). After the branch is pushed and merged,
  `git pull` there will need its local edits reconciled.
- `flash.sh` (tracked) still points at a `~/Desktop/kyphone` that no longer exists; `flash_macmini.sh` is the right one for this Mac.

## Home menu icons (step 8) — built, waiting to ship
- **What:** each home row is a 56×56 pixel icon (pixelarticons, MIT — notice in `spi_bridge/assets/`), as in the design's default. The
  design has three styles; the renderer draws whichever the wire names, so it can change without reflashing:
  `KYPHONE_HOME_STYLE=icons` (default) · `both` (icon + word) · `words` (what the phone showed before).
- **How:** `spi_bridge/tools/make_icons.py` holds the five SVG paths from the design and writes `Inkplate_SPI_Peripheral/ui_icons.h` (firmware)
  and `spi_bridge/home_icons.py` (emulator) from one source. Its rasteriser (no imaging library) puts a pixel on where the target pixel's
  centre falls inside the path, as a browser does. **Against the designer's capture the four icons that appear in it overlap 98.7–99.9%**
  (1 to 8 pixels differ); LISTEN is not in the capture.
- **Also fixed:** the "more below" mark at the bottom of the home menu pointed the wrong way (an upside-down staircase); it is now the
  designer's downward funnel, measured from the capture — in both the emulator and the firmware, and it had been wrong since OS 0.2.
- **Wire:** `HOME2|time|index|unread|style` (style I / B / W). Either side can ship first: old Python + new firmware shows icons; new Python
  + old firmware ignores the extra field.
- **Checked:** 250 tests pass (icons pixel-for-pixel in both renderers, all three styles, the funnel, the generator's output up to date,
  overlap with the capture); firmware compiles (374 KB, 11% of flash, RAM unchanged).
- **To put it on the phone:** flash the Inkplate (`flash_macmini.sh`, or write `ui` app image as before) and copy `kyphone_os.py`,
  `simulator.py`, `home_icons.py` to the Radxa. Held back on purpose until you can look at the panel.

## Wire changes the firmware must implement
Rules for all screens: one command ≤ 253 characters; fields split on `|`, sub-fields on `·` (U+00B7); printable ASCII
otherwise.

| Command | Format |
|---|---|
| `TEXTS` | `TEXTS\|sel\|name·preview·unread·time\|…` — sel −1 back, −2 plus, else row **within the window** (0–4); ≤5 rows; no rows = empty state. Preview starts `You: ` or `! ` (unsent) |
| `CONTACTSPICK` | `CONTACTSPICK\|sel\|query\|3 / 14\|name·number\|…` — sel as above, 0–6; ≤7 rows; numbers formatted; no rows = NO MATCH (query set) or NO CONTACTS |
| `CALLS` | `CALLS\|sel\|name·tag·time·duration\|…` — ≤6 rows; first entry of the list is `DIAL A NUMBER·NEW··` |
| `THREAD2` | `THREAD2\|name\|draft\|hdr\|code·time·text\|…` — hdr `''`/`B`/`I`; code `R` received, `Y0` sending, `Y1` sent, `Y2` not sent, `Y3` not sent + selected; ≤3 bubbles; renderer wraps draft at 30 columns (≤3 lines) and bubbles at 20, greedy word wrap, a long word breaks at the end of the line |
| `CONTACT` | `CONTACT\|title\|sub\|kind\|sel` — kind `S` saved / `N` saved without number / `U` unsaved; sel `B E C T V A` (back, edit, call, text, save, add number) |
| `CONTACTEDIT` | `CONTACTEDIT\|first\|last\|number\|idx\|kind` — idx −1 cancel, 0–2 fields, 3 save, **4 DELETE**; kind `N` new (title NEW CONTACT, no DELETE) / `E` edit (EDIT CONTACT, DELETE bottom left) |
| `CONFIRM` | `CONFIRM\|title\|body\|go\|keep\|sel` — replaces `CONFIRMDISCARD`. Destructive button left (2px), safe button right (3px); sel `D` / `K`; opens on `K`. Discard message and delete contact both use it |
| `STUB` (stop alert) | `STUB\|title\|body` — boxed `!`, body wrapped, **OK drawn inverted** (the only control). Carries every alert: unbuilt feature, validation, empty send, no recipient |
| `HOME2` | `HOME2\|time\|index\|unread\|style` — **index is a position in the new order**: 0 TEXT, 1 CALL, 2 CONTACTS, 3 READ, 4 LISTEN. `style` I icons (default) / B icons and words / W words. The renderer pads the unread count to two digits (`03`), scrolls by `max(0, (index+1)*135 − 538)`, and draws the downward "more below" funnel while rows remain below |
| `COMPOSE` | unchanged shape; the message field is uncapped in state but the wire shows its END behind `...` when it would not fit; renderer wraps it at 30 columns |

## Decisions and deviations from the design doc
- **DELETE lives on the edit form** (the handoff's one open question), chosen by the recommendation; moving it is a small change. It is built and tested there.
- **Long messages** are cut with `...` when one alone exceeds the frame; the oldest bubble drops first when frame space is tight.
- **Old sent messages** with no recorded recipient (3 on the phone) are hidden, not deleted.
- **Not in the doc:** newlines become spaces and curly quotes/dashes become ASCII (instead of `?`); a word longer than a line
  fills the current line first; the composer counts its `> ` prompt and cursor, so it always fits 3 lines.
- **Contacts are placed alphabetically when one is created**, and numbers are stored formatted, as the prototype does. A
  rename does not re-sort. First name is required (last name optional); name fields hold 18, TO holds 20.
- **The handoff README does not list every change in the v4 prototype.** Compose arrow-up (SEND → MESSAGE → TO → header)
  differs between v3 and v4 and is the reference; ported and tested. When the prose and the prototype disagree, the prototype wins.
- **Payload budget:** the handoff's per-row tables omit separators and the command head; worst-case rows overflowed by
  5–14 characters. Fixed in code by shortening the flexible field, with tests.
- **Every small button is 36px tall** (SAVE, SEND, OK, DELETE, confirm), measured from the captures; the emulator had drawn them at 28px.
- **The emulator's text is narrower than the device's** fixed 18 px glyph cells; layouts match the mock within a few
  pixels, and line-wrapping follows the device figure (30 / 20 columns).

## Risks and open items
- Firmware can't be verified in the emulator; the first flash may show mismatches. Keep the old firmware/renderers for rollback.
- **Nothing here is on the Radxa.** It still runs OS 0.2. Python and firmware must ship together.
- Once cellular hardware exists, real send/receive replaces `_transport_send`; number normalisation and `NOT SENT` handling are ready for it.
- ~~`CLAUDE.md` is out of date~~ — rewritten for OS 0.2.1 (done). `README.md` is still stale (OS 0.0, `kyphone_app.py`, Twilio polling).
- Menu icons need a separate 1-bit bitmap conversion (`planning/kyphone_backlog.md` item).

## Housekeeping done alongside
- Twilio: `TWILIO_SID` / `TWILIO_TOKEN` commented out on the Radxa (`kyphone.service`, `start_kyphone.sh`, backups `*.bak-2026-09-18`);
  service restarted; no outbound Twilio calls. `TWILIO_NUMBER` kept (the service exits without it).
- Mac clone brought up to `origin/main`; earlier local edits are in `git stash` (`stash@{0}`).
- Emulator environment: `~/.venvs/kyphone` (Python 3.9, pygame, pytest).
- Design feedback pack sent to Claude Design; its handoff produced this spec.

## Follow-up: test hygiene (2026-09-18)

- `KYPHONE_DATA_DIR` moves `contacts.json` and `messages.json`; the tests and the emulator can now run against a scratch folder. Read once at import.
- `test_state_machine.py` no longer leaves a fake `pygame`/`simulator` in `sys.modules`; collected first, it used to make 30 simulator and firmware tests skip silently. The suite is 253 tests and passes in any file order.
- **Incident:** while verifying this, a new test imported the real `kyphone_os` with no override, which loaded and migrated this Mac's `data/contacts.json` from the OS 0.1 `{number: name}` shape to the OS 0.2 list in place (content preserved, format changed). Fixed: that test now imports a copy of the module inside a scratch tree, and the whole run was re-proved against an old-format data file (byte-identical afterwards). The Radxa's data was not involved.

