# KyPhone reader — build plan (EPUB on the Radxa, e-reader on the Inkplate)


## Status (branch `reader-build`, local only — nothing pushed, flashed or deployed)
| Step | What | State |
|---|---|---|
| 1 | EPUB parser (`reader_epub.py`, 47 tests; lazy per-chapter loading) | done |
| 2 | Font generator + `reader_fonts.py` (FreeSerif 9/12/18/24 vendored into the sketch's `fonts/`; 11 tests) | done |
| 3 | Layout / pagination (`reader_layout.py`, 31 tests) | done |
| 4 | State machine: library + reader (`kyphone_os.py`, 44 tests) | done |
| 5 | Emulator renderers (`render_page`, library list; 13 pixel tests; real-key click-through of both Gutenberg books) | done |
| 6 | Firmware (`ui_reader.h`, `LIBRARY`), host harness, USB page preview: built, tested on a computer and compile-checked for the board — **NOT flashed** | done except the panel |
| 6b | Panel: back up, flash, copy Python to the Radxa, preview a page, read a real book, measure page turns | waiting for Kyle at the phone |
| 7 | Docs (CLAUDE.md, README) | next |

Measured with real Project Gutenberg books (free): *Alice in Wonderland* (161k characters, 13 chapters) and *The Count of Monte Cristo* (2.6M characters, 123 chapters, largest chapter 61k). **On the Radxa** the first version, which parsed the whole book on open, took **5.7 s** to open Monte Cristo; making chapter loading lazy brought that to **0.09 s** (a mid-book chapter parses in 0.04 s and paginates in 0.06 s). Pagination of the whole book at any size is ~1.3 s on the Mac, but the reader only ever paginates the chapter it is in. At 12pt a page averages 4.1 wire frames (max 5); 9pt averages 6.7 (max 8); 18pt 2.0; 24pt 1.2. Every frame across the whole of Monte Cristo is ≤253 characters.

**Firmware verification (all on this Mac, nothing flashed):** the same `ui_reader.h` the panel runs is built against a fake display that reproduces Adafruit GFX's custom-font printing. Its pixels match `reader_layout.text_ink` exactly and match the emulator exactly (0 differing pixels in the text and the footer rule at all four sizes); frame semantics (first frame clears, text frames never refresh, RFOOT refreshes P/F), malformed frames, no-wrap and built-in-font restoration are tested; 2,500 random reader frames plus maximum-length ones run clean under AddressSanitizer/UBSan. The firmware **compiles and links for the Inkplate 4 TEMPERA**: 392,473 bytes (12% of program space), 69,888 bytes of RAM (21%).

**Two things found on the way.** (1) The upstream font headers start with `#include <Adafruit_GFX.h>`, which made the build link a second copy of the GFX library next to the one Inkplate.h bundles (duplicate-symbol link errors); the vendored copies have that one line replaced by a comment (glyph data verified byte-identical to release 1.12.6). (2) On this Mac, `Adafruit_MonoOLED.cpp/.h` in `~/Documents/Arduino/libraries/Adafruit_GFX_Library` are iCloud-evicted placeholders ("dataless") that cannot be downloaded from this session (`Operation timed out`); any build that pulls in the standalone GFX library fails on them. The reader firmware no longer pulls it in, so `flash_macmini.sh` compiles, but if a future build complains about that file, open it in Finder (or Arduino) to make macOS download it.

Deviations from the plan as built: the library shows **5 rows** (like the texts list) rather than 6, so title+author+percentage fit one frame; the READ stop alert is gone (LISTEN keeps its own); five new stop alerts (`BAD_BOOK`, `END_OF_BOOK`, `START_OF_BOOK`, `BIGGEST_FONT`, `SMALLEST_FONT`); the reader's page-turn keys are Right/Down/Enter/Space and Left/Up/Backspace, `+`/`=` and `-`/`_` for size, Esc/`q` to leave; the loader is **lazy** (`Book.chapter(i)`, `next_with_text`, `progress` by file size) instead of parsing everything on open; the four FreeSerif headers are vendored (unmodified) into `Inkplate_SPI_Peripheral/fonts/` so neither the generator nor the firmware build depends on the Arduino library folder; `reader_epub.to_drawable` has its own transliteration table instead of importing `kyphone_os.sanitize` (importing the OS pulls in hardware modules); em-dashes become `--` (so "bank--the" can break after the dash) rather than `-`.

## Context
The home menu's READ entry is a stop alert today ("READ CANNOT OPEN YET"). Kyle wants it to be a simple e-reader: put an EPUB on the Radxa, open it from READ, and read it on the Inkplate with page turning and adjustable font size. Decisions already made with Kyle: **serif book font (FreeSerif, proportional)**, **books are copied into a folder** (no upload UI yet), **v1 = library + reader + next/prev page + 4 font sizes + resume position + end-of-book alerts** (text only: no images, bold/italic, or chapter menu).

Nothing here costs money or needs network. Same working method as OS 0.2.1: Python + emulator first, then the host-side firmware harness, then the panel; no push/flash/deploy without an explicit go-ahead; back up both devices before flashing.

## Facts that shape the design (verified in the code)
- **A frame is ≤ 253 characters** (`MAX_COMMAND_CHARS`) and the SPI clock is **10 kHz** (`SPI_SPEED_HZ`, kyphone_os.py:44), i.e. ~0.2 s per frame. A page of book text is ~500–1500 characters, so **a page must travel as several frames**, and the firmware must not refresh the e-ink between them.
- `handle_command()` (Inkplate_SPI_Peripheral.ino:1238) currently does `clearDisplay()` → draw → `partialUpdate()`/`display()` **for every command**. Reader frames need a path that draws without clearing or refreshing.
- `_spi_sender_loop` (kyphone_os.py:551) keeps **only the latest pending command** (coalescing). Sending a page as N separate `push_screen` calls would drop all but the last. The reader needs one queue item = one whole page.
- The firmware is `Inkplate display(INKPLATE_1BIT)`; partial refresh with a full refresh every 10 min (`FULL_REFRESH_INTERVAL_MS`). Reading needs its own ghost-clearing cadence (below).
- Panel draws **printable ASCII only**; `|` and `·` (0xB7) are wire separators; `sanitize()` (kyphone_os.py:399) already maps curly quotes/dashes to ASCII. Book text needs an extra step (strip accents, `|`→`/`).
- Adafruit GFX FreeSerif headers (`~/Documents/Arduino/libraries/Adafruit_GFX_Library/Fonts/FreeSerif{9,12,18,24}pt7b.h`) are plain C arrays: glyph table (`xAdvance` per glyph, 0x20–0x7E) + bitmap + `yAdvance` (29 px at 12pt). Widths are simply summed (no kerning), so the Radxa can predict exactly how wide a line will draw.
- Radxa: Python 3.9.2, **stdlib only** (no lxml/bs4/ebooklib; `zipfile`, `xml.etree`, `html.parser`, `unicodedata` are available), 48 GB free, 7.7 GB RAM.
- Input already works for this: keyboard (arrows, Enter, Esc, `CHAR:x`; WASD map to arrows outside typing screens) and trackpad (swipes → arrows, click → Enter).

## Architecture
The Radxa does everything smart (parse, wrap, paginate, remember place); the Inkplate is a dumb renderer of pre-wrapped lines, exactly like the rest of the UI.

### New modules (importable without hardware, unit-testable)
| File | Job |
|---|---|
| `spi_bridge/reader_epub.py` | Open an EPUB with `zipfile` (never extract to disk): `META-INF/container.xml` → OPF → manifest/spine/metadata (title, author, `linear="no"` skipped); chapter titles from EPUB3 nav or EPUB2 `toc.ncx`. XHTML → paragraphs via `html.parser` (block tags make breaks; skip script/style/head/svg/images). Text → drawable ASCII (`unicodedata` NFKD accent strip + `kyphone_os.sanitize` table + `|`→`/`). Guards: cap bytes read per member, DRM (`encryption.xml` other than font obfuscation) → "cannot open", malformed → skipped with a reason. |
| `spi_bridge/reader_layout.py` | Greedy word wrap using per-glyph advances, first-line indent, heading handling, hard-split of over-long words; **paginate a whole chapter** into page start offsets (fast enough in Python; cached per (book, chapter, font)); `page_at(offset)`; chunk a page's lines into ≤253-char `RTEXT` frames. |
| `spi_bridge/reader_fonts.py` | **Generated** (like `home_icons.py`): for each of the 4 sizes, `yAdvance`, per-glyph advances, and glyph bitmaps parsed from the FreeSerif headers. |
| `spi_bridge/tools/make_reader_fonts.py` | Generator with `--check` (a test fails if the generated file is stale). Also emits/updates the licence notice in `spi_bridge/assets/` (FreeFont is GPL with the font-embedding exception, same treatment as the pixelarticons notice). |

Hooks in `kyphone_os.py` stay small (it is already ~2,100 lines).

### State machine (kyphone_os.py)
- New screens: `library` and `reader`. Home **READ** (HOME_MENU index 3) opens `library` instead of the `READ` stop alert (LISTEN stays a stub).
- **library**: rows = `.epub` files in `data/books/` (rescanned on open), title · author · % read; windowed like calls (`LIBRARY_ROWS = 6`, reuse `window_start`/`_list_command`); header back; Enter opens; Esc → home; empty state "NO BOOKS — COPY .EPUB FILES TO data/books/". Bad book → stop alert with the reason.
- **reader** keys: RIGHT / DOWN / Enter (click) = next page; LEFT / UP = previous; `+`/`=` bigger, `-` smaller (4 sizes, wraps nowhere; at the limit a stop alert per the "no silent no-op" rule); Esc/`q` = back to library. Start/end of book → stop alert ("START/END OF BOOK") returning to the same page.
- **Position** = (chapter, character offset in chapter text) — so a font change re-paginates and lands on the page containing the offset. Saved to `data/reading.json` on each turn and on leaving: `{book_id: {chapter, offset}}` plus a global `font`. `book_id` = filename + size.
- **Refresh policy** (Radxa decides, sends as a flag): full refresh on opening a book, on chapter change, on font change, and every 8th page turn; partial otherwise.
- **Sending a page**: new `push_page(commands)` puts a *list* of frames in the pending slot. `_spi_sender_loop` sends them in order; if a newer page arrives mid-send it abandons the rest (safe: the display only changes on the final frame), so fast paging skips intermediate pages instead of queueing them.
- Emulator/`SIM_MODE` sends the whole list to `simulator.render_page`.

### Wire protocol (additions; documented in CLAUDE.md's table when built)
| Command | Meaning |
|---|---|
| `RTEXT\|size\|row\|S/-\|line·line·…` | Draw lines from `row` in font `size` (S/M/L/X); `S` = first frame: clear the framebuffer. Never refreshes. |
| `RFOOT\|P/F\|left\|right` | Draw the footer (chapter title left, `12/40 · 35% · M` right) and refresh: `P` partial, `F` full. Always the last frame of a page. |
| `LIBRARY\|sel\|title·author·pct\|…` | Library list, ≤6 rows, sel −1 = back. |
Estimated cost: 12pt ≈ 48 chars × ~18 lines ≈ 850 chars ≈ 4–5 frames ≈ ~1–1.5 s of SPI, plus the e-ink refresh; 9pt is heavier (≈7–8 frames), 24pt lighter. **These are estimates — measured on the panel in step 6**; the SPI clock (10 kHz today) is the lever if page turns feel slow.

### Firmware (Inkplate_SPI_Peripheral)
- New `ui_reader.h` (included like `ui_screens.h`): the four FreeSerif fonts (`setFont`), `RTEXT`/`RFOOT` handling, `LIBRARY` list (reuse the CALLS row renderer style). `handle_command()` gets a branch **before** the clear+refresh path so `RTEXT` draws only and `RFOOT` refreshes with the requested mode.
- Line placement: baseline = top margin + (row+1)·yAdvance − small descent constant (one constant per size, in one table shared with Python via the generated file).
- The receive buffer's NUL terminator and the 253-char rule already exist; fuzz them again with reader frames.
- USB preview (`@RTEXT…` lines) lets pages be checked on the real panel without the Radxa.

### Emulator (simulator.py)
`render_page` blits the **real FreeSerif glyph bitmaps** from `reader_fonts.py`, so emulator text is pixel-identical to the panel's (same principle as the icons). Library list reuses the calls-list drawing.

## Build steps (each: tests first, commit locally, report before the next)
1. **EPUB parser** + tests on synthetic EPUBs built with `zipfile` in the test (EPUB2 + EPUB3, nested dirs, entities, `linear="no"`, malformed, encrypted, oversize member). One real public-domain EPUB checked by hand.
2. **Fonts generator** + `reader_fonts.py` + `--check` test; licence notice.
3. **Layout/pagination** + tests: no line wider than the text area; concatenating pages reproduces the chapter text (whitespace-normalised, nothing lost or duplicated); page count sane per size; offsets round-trip across a font change; every frame ≤253 chars and contains no `|`/undrawable characters.
4. **State machine**: library/reader screens, keys, alerts, `reading.json`, refresh cadence, `push_page` + sender-loop change (tests with the sender's coalescing logic exercised). Uses `KYPHONE_DATA_DIR` scratch dirs — tests never touch real `data/`.
5. **Emulator**: library + reader renderers, pixel tests, click-through with screenshots.
6. **Firmware**: `ui_reader.h`, host harness (MockDisplay gains `setFont` for GFX fonts) — geometry, emulator-vs-firmware pixel parity, ASan/UBSan fuzz with reader frames; `arduino-cli` compile check. **Then, only with Kyle's go-ahead and at the phone:** back up both devices, flash, copy Python to the Radxa, USB-preview a page, then a real book: measure page-turn time, check ghosting/full-refresh cadence, all font sizes, resume after restart.
7. **Docs**: CLAUDE.md (wire table, screens, gotchas), README, new `planning/reader-build-plan.md`.

Steps 1–5 need no hardware and can proceed while Kyle is away; step 6's panel part needs the phone.

## Critical files
`spi_bridge/kyphone_os.py` (HOME_MENU/READ hook ~L1041, `push_screen`/`_spi_sender_loop` ~L543–575, `sanitize` L399, `window_start`/`_list_command`), `spi_bridge/simulator.py`, `Inkplate_SPI_Peripheral/Inkplate_SPI_Peripheral.ino` (`handle_command` L1238), `ui_screens.h` (`ui_dispatch` L669, `ui_calls` L361), `tests/firmware_host/mock_display.h`, plus the new files above. Reuse: `_list_command`, `window_start`, `_show_alert`/`ALERTS`, `KYPHONE_DATA_DIR`, the generator/`--check` pattern from `tools/make_icons.py`.

## Verification
- Unit: `test_reader_epub.py`, `test_reader_layout.py`, reader cases in the state-machine tests; all three existing suites still 253 passing (any order) plus the new ones.
- Emulator: `KYPHONE_DATA_DIR=$(mktemp -d) python3 spi_bridge/kyphone_os.py --sim` with a test EPUB in the scratch `books/`: open from READ, turn pages both ways, change size, quit and reopen (resume), hit both ends.
- Host firmware harness: parity with emulator pixels; sanitizer fuzz.
- Panel (with Kyle): USB-preview a page, then a real book end-to-end.

## Risks / open items
- **Page-turn speed** (SPI 10 kHz × several frames + refresh) — estimated, measured in step 6; mitigations: raise the SPI clock, larger default size, fewer chars per frame overhead.
- **Ghosting** at every-8th-page full refresh — tune on the panel.
- **Very large single-file chapters** (some EPUBs are one 1 MB HTML): paginate whole chapter takes ~a second; show a "one moment" state if measured slow, or split at paragraph boundaries.
- **Non-Latin books** unsupported (ASCII only); accents are stripped, unknown characters become `?`.
- **DRM'd EPUBs** cannot be opened.
- **Font licence**: FreeFont is GPL + font exception; notice file added, keep the glyph data confined to generated files.
- **Deferred (not v1):** chapter menu, images, bold/italic, hyphenation, upload page / USB import, 3-bit grayscale text.
