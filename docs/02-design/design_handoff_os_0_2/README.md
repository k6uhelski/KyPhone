# Handoff: KyPhone OS 0.2.1 interface

## Overview
The complete interface for KyPhone OS 0.2.1 — a Radxa Rock 3A driving an Inkplate 4 over SPI. A 600x600 1-bit e-paper panel, a hardware keyboard, no touch. This revision answers the nine undefined behaviors found by click-testing the 0.2 build in the emulator.

## About the design files
The files in this bundle are **design references written in HTML**. They are prototypes of the intended look and behavior, not production code to lift.

This project already has a target environment: the Python OS and renderer in `spi_bridge/` (`kyphone_os.py`, `simulator.py`, `input_handler.py`) and the Arduino peripheral sketch `spi_bridge/Inkplate_SPI_Peripheral/`. Recreate these screens there, in the existing state-machine and draw-call patterns. Do not introduce a web framework.

## Fidelity
**High fidelity.** `GEOMETRY.md` carries the literal offsets, heights and rule weights for every screen, taken from the prototype's markup. Two values of ink only: #111111 and #FFFFFF, no grays, no opacity, no tints.

## What to read, in order
1. **`KyPhone OS 0.2.1 Design Doc.dc.html`** — the specification. What changed since 0.2, the payload budget per list, the scrolling rule, message states, length limits, unsaved numbers, new contact and validation, delete, and four open questions.
2. **`GEOMETRY.md`** — every screen's literal geometry. Use this rather than measuring screenshots.
3. **`screens/`** — 600x600 captures, 1:1 with the panel, of every new and changed screen. Use these as the diff target for `simulator.py` output, not as a source of measurements.
4. **`KyPhone UI v4.dc.html`** — the working prototype. Open it in a browser and drive it with the keyboard: any key wakes the lock screen, arrows move, Enter activates, Escape backs out, `i` on the home menu simulates an incoming call. Read it for exact behavior when the doc and your reading of it disagree. Its logic class holds the state machine, one screen per branch in `handleKey`, which maps onto the OS's `_from_*` transition functions.

`KyPhone UI v3.dc.html` is also included: it is the build the emulator test was made from, kept so you can diff 0.2 against 0.2.1.

## Screens changed in 0.2.1

| Screen | What changed |
| --- | --- |
| Home | Menu order: CONTACTS moved to third, so it is on screen without scrolling |
| Texts list | Windowed to 5 rows; preview capped at 24 chars; position counter and MORE BELOW footer; `!` prefix for unsent; unsaved senders shown as formatted numbers; empty state |
| Thread | Sent messages persist; sending / sent / not-sent states with labels; retry by selecting a not-sent bubble; composer wraps to 3 lines and shows the draft's end; message length uncapped; bubbles no longer truncate |
| Compose | Empty-send and no-recipient alerts replace silent no-ops |
| Contact page | Unsaved numbers show the formatted number and NOT IN CONTACTS with a SAVE action; a saved contact with no number shows ADD NUMBER |
| New contact | New screen (was a "cannot be saved yet" alert): the edit form, blank, titled NEW CONTACT, no DELETE |
| Edit contact | DELETE button added bottom left |
| Delete confirm | New screen, discard-dialog layout, KEEP CONTACT as the right-hand default |
| Validation alerts | Four new stop alerts: first name required, number required, number not dialable, number already saved |
| Contacts list | Windowed to 7 rows; position counter; NO MATCH and NO CONTACTS empty states; the new-contact row is gone (it is the header +) |
| Calls list | Windowed to 6 rows; position counter and footer |

Unchanged: lock screen, stop-alert geometry, discard-message wording, header and button patterns, the type scale, dial and the three call states.

## The payload budget
Every screen is drawn from one message of at most 253 characters, so lists are windowed — only the visible rows are sent, and the window re-sends when the selection moves.

| List | Rows | Per row | Payload |
| --- | --- | --- | --- |
| Texts | 5 | 14 name + 24 preview + 8 time + 3 seps = 49 | 245 |
| Contacts | 7 | 18 name + 14 number + 2 seps = 34 | 238 |
| Calls | 6 | 14 name + 4 tag + 10 time + 6 duration + 4 seps = 38 | 228 |
| Thread | 3 bubbles | uncapped message + 8 time + state flag | one long message can be the whole payload |

## Type scale
The Adafruit GFX built-in font is a 5x7 glyph in a 6x8 cell and scales by whole multiples only.

| Design px | textSize | Used for |
| --- | --- | --- |
| 64 | 8 | The lock clock, and nothing else |
| 48 | 6 | Home menu words, call names, dial buffer, contact name |
| 24 | 3 | Screen titles, row names, status bar, message text, field text, buttons |
| 18 | 2 | Timestamps, previews, field labels, counters, state labels, hints |

18px is off the cell grid on purpose and rounds to textSize 2 on device.

## Assets
- **Home menu icons** — pixelarticons, MIT, (c) Gerrit Halfmann. Drawn on a 24x24 grid at 1px outline weight. The repo has no icon assets yet; `planning/kyphone_backlog.md` still lists "Bitmap icon system (1-bit, Adafruit GFX byte arrays)" as open. These SVG paths need converting to GFX byte arrays via PIL, the same path the cat bitmap took. That is a separate task.
- **Lock screen cat** — already a fixed 1-bit bitmap in the firmware (`cat_bitmap[]`). Unchanged.
- **Fonts** — none to ship. The prototype uses VT323 as a stand-in for the GFX built-in font; the device draws its own. VT323's advance is tighter, so composer line counts in the prototype look narrower than on device — 30 characters per 24px line is the device figure.

## Decisions taken after the emulator test
- Texts list keeps 5 rows with 24-char previews, not 3 with the full 44.
- No cap on message length, and no character counter anywhere.
- The phone has no notion of its own number and never displays one.

## Open question
- Should DELETE sit on the edit form (as specified) or on the contact page next to CALL and TEXT?

## Build order
Easiest first:
1. Windowing and the footer counter, applied to all three lists. One helper, three call sites.
2. Message states and retry in the thread.
3. Composer wrapping and the character filter.
4. Unsaved-number formatting, and the contact page's three variants.
5. New contact, the four validation alerts, and the empty-send alerts.
6. Delete and its confirmation.
7. Home menu reorder.
8. Icon conversion to GFX byte arrays (separate task).
9. Call, dial, and the three call states, named in a `kyphone_os.py` docstring only.

## Files
- `KyPhone OS 0.2.1 Design Doc.dc.html` — the specification
- `GEOMETRY.md` — literal geometry per screen
- `screens/` — 600x600 captures of every new and changed screen
- `KyPhone UI v4.dc.html` — the 0.2.1 prototype
- `KyPhone UI v3.dc.html` — the 0.2 prototype, for diffing
- `doc-page.js`, `support.js` — runtime files the documents load
