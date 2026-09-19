# KyPhone OS 0.2.1 — Build Plan
*Updated: September 18, 2026 · branch `os-0.2.1-build` · 12 local commits, nothing pushed, nothing deployed*

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
| 7 | Home menu reorder (CONTACTS third) | ✅ done |
| 8 | Home menu icons (pixel bitmaps) | ⬜ separate task, out of this build |
| 9 | Name the call screens in a `kyphone_os.py` docstring | ✅ done |
| F | **Firmware pass** — Arduino renderers for every changed screen, flash, check on the real panel | ⬜ **next; needs you at the device** |
| D | Docs (`CLAUDE.md`, wire tables) and deploy to the Radxa | ⬜ after F |

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

**Problems from the emulator test, now fixed:** texts and contacts lists ran off the screen with nothing highlighted ·
older conversations unreachable · sent texts vanished and a fake own-number conversation appeared · a slow send froze
the keyboard · you couldn't see what you were typing · `|` corrupted the screen · two unsaved senders looked identical ·
picking the second of two same-named contacts opened the first · `+` in contacts said "cannot be saved yet" · bad
input saved silently or did nothing · SEND with nothing to send did nothing · from SEND, arrow up jumped to the header.

## Tests and verification
- **216 tests pass** (51 at the start): `test_state_machine.py` (state, wire strings, frame limits, retry, contacts) and
  new `test_simulator.py` (pixel checks on real emulator frames, plus a check that the simulator's word-wrap matches the OS's).
- **Every step** is also click-tested in the real emulator with real key events, screenshots after each key, compared
  side by side with the designer's captures.
- Run tests on a scratch copy so real data is never touched, using `~/.venvs/kyphone/bin/python -m pytest
  spi_bridge/tests/test_state_machine.py spi_bridge/tests/test_simulator.py` (don't point pytest at the whole `tests/`
  folder — the hardware diagnostic scripts there run on import). `KYPHONE_SIM_SEND=sent` makes the emulator's fake radio succeed;
  the default is NOT SENT, which is what the phone does today.

## Remaining work

**The Python and emulator side of the build is complete** (steps 1–7 and 9). What remains is the firmware pass and the docs/deploy.

**Firmware pass (F) — what I found.**
- `Inkplate_SPI_Peripheral.ino` is 2,421 lines and **already has a renderer for every screen**, so this is editing about ten of
  them, not writing new ones: `render_home2` (order, `03` padding), `render_texts`, `render_contacts_pick`, `render_calls`,
  `render_thread2` (bubble states, wrapped composer), `render_compose` (wrapping), `render_contact` (three kinds),
  `render_contact_edit` (title, DELETE), `render_stub` (inverted OK, all alerts), and `render_confirm_discard` → one
  general `CONFIRM` renderer. The dispatch in `loop()` changes to match.
- **This Mac can compile it.** `arduino-cli` 1.5.1, the Inkplate board package (8.1.0) and libraries are installed; the
  unmodified firmware builds (373 KB, 11% of flash; about 3 minutes cold). Every firmware change can be compile-checked
  here before anything is flashed.
- **Flashing:** use `flash_macmini.sh` (points at `~/kyphone`, port `/dev/cu.usbserial-1140`). The tracked `flash.sh` points at a
  `~/Desktop/kyphone` that no longer exists. An Inkplate-looking serial device is currently attached.
- **Cannot be seen from here.** Whether a screen *looks* right on the e-ink panel needs your eyes (or a photo). Proposed aid: a
  dev-only USB-serial command that draws one wire string sent from the Mac, so each screen can be checked on the real panel
  without the Radxa — then replayed for every screen the emulator produces.
- **Rollback caveat.** The build I made from `origin/main` can be kept as a fallback, but the firmware *currently on the
  Inkplate* may have been built from your earlier local edits (now in `git stash`), so it is not guaranteed identical.
- **Order of operations:** write and compile-check → (with your OK) flash → check screens with the serial preview → deploy the
  Python to the Radxa together with the flash, since they must match. Flashing and deploying each need an explicit go-ahead.

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
| `HOME2` | `HOME2\|time\|index\|unread` — shape unchanged, but **index is a position in the new order**: 0 TEXT, 1 CALL, 2 CONTACTS, 3 READ, 4 LISTEN. The renderer pads the unread count to two digits (`03`) and scrolls by `max(0, (index+1)*135 − 538)` |
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
- `CLAUDE.md` is out of date (still describes the 6-screen OS 0.1). Update with the wire table above at the end.
- Menu icons need a separate 1-bit bitmap conversion (`planning/kyphone_backlog.md` item).

## Housekeeping done alongside
- Twilio: `TWILIO_SID` / `TWILIO_TOKEN` commented out on the Radxa (`kyphone.service`, `start_kyphone.sh`, backups `*.bak-2026-09-18`);
  service restarted; no outbound Twilio calls. `TWILIO_NUMBER` kept (the service exits without it).
- Mac clone brought up to `origin/main`; earlier local edits are in `git stash` (`stash@{0}`).
- Emulator environment: `~/.venvs/kyphone` (Python 3.9, pygame, pytest).
- Design feedback pack sent to Claude Design; its handoff produced this spec.
