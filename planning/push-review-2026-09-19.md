# Push review — what going public would mean (2026-09-19)

**Nothing has been pushed.** This is the review to read before saying "push". The GitHub repo
`k6uhelski/kyphone` is **public** (an unauthenticated request to its API returns 200), so anything pushed is visible to everyone.

## What would be pushed
Three local branches, stacked (each includes the ones above it):

| Branch | Ahead of `origin/main` | Change vs `main` |
| :--- | :--- | :--- |
| `os-0.2.1-build` | 20 commits | 38 files, +10,681 / −1,706 lines |
| `reader-build` (also the home-menu reorder and the empty-TEXT fix) | 27 commits | 55 files, +16,239 / −1,719 lines |
| `music-build` (LISTEN) | 39 commits | 64 files, +20,190 / −1,744 lines |

The reader commits alone are 29 files, +5,596 / −51; the music commits alone are 17 files, +3,636 / −80. 73% of the 16,239 added lines are tests (25%), generated data such as font and
icon bitmaps (14%), and the design handoff docs (34%); the working code is the rest.

All 39 commits are authored as `k6uhelski <61721323+k6uhelski@users.noreply.github.com>`, the GitHub no-reply address, so
no personal email address is published.

## What I scanned for, and found
The scan (re-run 2026-09-19 after the music work, same result) covered every added line in the 64 files (generated bitmap data and the design prototype excluded from the text
patterns, checked separately):

| Check | Result |
| :--- | :--- |
| Twilio SIDs, API keys, tokens, private keys, "password" | none |
| Phone numbers other than fictional `555` ones | none (one test string, `9999999999`, is deliberate bad input) |
| Your real contact numbers | none of the 6 appear anywhere |
| Emails, IP addresses, `/Users/<you>` paths, `ssh radxa`, `.local` hostnames | none |
| `data/` (contacts, messages, `reading.json`, `books/`) | ignored by `.gitignore`; not tracked |
| Twilio credentials | not in the repo (they are commented out in the Radxa's own unit file, outside git) |

## Things to decide before pushing
1. **A sample name that matches a real contact.** One of your real contacts' first names appears in test sample data
   (`spi_bridge/tests/firmware_host/screens.py`, `test_simulator.py`, `test_firmware_host.py`) — it came from the design
   mock-up ("<first name> <fictional surname>", 555 numbers) and is **already public on `main`** in 2 files. Low risk
   (a first name with an invented surname). Say so if you would rather I rename it in the samples.
2. **`.claude/` is not in `.gitignore`.** It is untracked, so it has not been committed, but a careless `git add -A` at the
   repo root would add it. I would add it to `.gitignore` (a one-line change) before any push.
3. **The design handoff (19 files under `docs/02-design/design_handoff_os_0_2/`)** would become public: the design doc,
   `GEOMETRY.md`, three prototype `.html` files, 12 PNG captures, and `support.js`, a 64 KB script that begins
   `GENERATED from dc-runtime/src/*.ts` (output of the design tool's runtime). I do not know its licence. Options: keep
   everything; or drop `support.js` and the `.html` prototypes and keep the docs and PNGs (nothing in the code or tests
   reads the scripts or the `.html` files; the tests use the PNG captures). Your call.
4. **Font licence.** The reader embeds four FreeSerif font files (derived from GNU FreeFont: GPL v3+ with the font-embedding
   exception) plus tables generated from them. `spi_bridge/assets/freeserif-NOTICE.txt` explains this. FreeFont's exception
   is meant for exactly this use, but if you want a definite answer for a published repo, read the licence text at
   gnu.org/software/freefont before pushing. Not legal advice.
5. **How to land it.** Either (a) push the branches and open three stacked PRs (below), or (b) merge locally
   (`git switch main && git merge --ff-only music-build`) and push `main` — quicker, no review step, and it puts all 39
   commits on `main` at once. I would use (a) while you are alone in the repo only if you want the PR text kept as history.
6. **Rotate the Twilio credentials** at some point (they appeared in earlier tool output in a session). Low urgency because
   you stopped paying for the account; not part of the push.

## Before you say "go"
- The panel test is still to do (`planning/phone-session-checklist.md`). Merging before it means `main` holds code that has
  only run on a computer. I would push after the phone session, so any fix goes in first.

## Draft PR 1 — `os-0.2.1-build` → `main`
**Title:** OS 0.2.1: windowed lists, message states, contacts create/delete, stop alerts, icon home menu

**Body:**
> **Problem.** Testing texting, creating a contact and deleting a contact in the emulator found gaps: a contact could not be
> created or deleted from the UI, lists overflowed the 253-character frame, a failed send left an empty thread, and
> unbuilt features did nothing.
>
> **What changed.** Windowed lists (5/7/6 rows, never cut mid-field); sent messages stay in the thread as
> SENDING / SENT / NOT SENT with retry; numbers stored formatted and matched by their last ten digits; new-contact form,
> validation alerts and delete-with-confirmation; one stop-alert screen for anything the phone will not do; home menu with
> pixel-art icons; `KYPHONE_DATA_DIR` so tests never touch real data. Firmware renderers for every screen, a host-side test
> harness for them, a USB screen-preview tool, and the OS 0.2.1 design handoff under `docs/`.
>
> **Testing.** 253 tests (state machine, emulator pixels, firmware renderers built on a computer and fuzzed under
> ASan/UBSan), a real-key click-through of the emulator, and the firmware flashed to the Inkplate and the Python deployed to
> the Radxa on 2026-09-18 (icons excepted; they ship with the next flash).
>
> **Notes.** Twilio is switched off, so a send ends NOT SENT until there is a modem. No secrets or real numbers (scanned).

## Draft PR 3 — `music-build` → `reader-build` (retarget as the earlier ones merge)
**Title:** Music: play the music on the phone from LISTEN, in the background, through the headphone jack

**Body:**
> **Problem.** LISTEN was a stop alert. The goal: put music files on the phone and play them, controlled from the e-ink
> screen, with the music carrying on while you read or text.
>
> **Design.** The Radxa does everything: `music_library.py` reads tags and lengths from MP3, FLAC, Ogg/Opus, M4A and WAV with
> the standard library only; `music_player.py` holds all the playback rules in a `Session` (next, previous, seek, volume,
> skipping a bad file) over a silent simulated player (emulator, tests) and a GStreamer player for the phone (GStreamer and its
> Python bindings are already on the Radxa, so nothing new is installed for the headphone jack). LISTEN opens an album list, an
> album its tracks, a track a now-playing screen. Music keeps playing when you leave; the home menu shows a small mark;
> a 30-second ticker keeps the elapsed time honest without hammering the e-ink; nothing else is redrawn by the player. Volume
> (default 40%) and the place you stopped are saved. No new firmware protocol: three screens on the normal path.
>
> **Testing.** 621 tests in all (198 new): every tag format from synthetic files, damaged and random files, the cache, a
> 5,000-track scan, every Session rule, the OS screens against a temp music folder with a hand-advanced clock, emulator pixel
> tests, firmware pixel parity (bars filled by integer maths in both), sanitizer fuzz of the new commands, firmware
> compile-checked (12% of program space). The real GStreamer player was also run on the Radxa against a silent output:
> real-time position, pause, seek, a bad file skipped, queue finished; and every decoder needed is present there.
> **Not yet heard through the headphone jack, and no real album played** — see `planning/phone-session-checklist.md`.
>
> **Notes.** Local files only (no streaming, no accounts). Bluetooth headphones are a planned second phase (PulseAudio's Bluetooth
> module; the service runs as root, which is the awkward part). The now-playing layout is ours, not a designer's.

## Draft PR 2 — `reader-build` → `os-0.2.1-build` (retarget to `main` once PR 1 merges)
**Title:** Reader: load EPUBs on the Radxa, read them on the Inkplate with page turning and four font sizes

**Body:**
> **Problem.** READ was a stop alert. The goal: put an EPUB on the phone and read it on the e-ink display with page turns and
> a font-size control.
>
> **Design.** The Radxa parses, wraps and paginates (standard library only); the Inkplate is a dumb renderer. A page is
> several SPI frames (`RTEXT` lines, then `RFOOT` which draws the footer and refreshes), because one frame holds 253
> characters. Book text uses FreeSerif 9/12/18/24pt, so the Radxa knows every glyph's exact width. Chapters load lazily
> (a 2.6M-character novel opens in 0.09 s on the Radxa; parsing it whole took 5.7 s). A reading position is
> (chapter, offset), so it survives a font-size change. Full refresh on open, new chapter, new size and every 8th turn.
>
> **What is in it.** `reader_epub.py`, `reader_layout.py`, generated `reader_fonts.py`; library and reader screens in
> `kyphone_os.py`; emulator renderers using the panel's own glyph bitmaps; `ui_reader.h` and a `LIBRARY` screen in the
> firmware; test harness support for GFX custom fonts; `preview_screens.py --book`.
>
> **Testing.** 423 tests. The firmware code (built on a computer) draws pixel-identical pages to the emulator and to the
> layout module at all four sizes; 2,500 random reader frames under ASan/UBSan; firmware compile-checked for the Inkplate
> 4 TEMPERA (12% of program space); real Project Gutenberg books (Alice in Wonderland, The Count of Monte Cristo).
> **Not yet run on the panel** — see `planning/phone-session-checklist.md`.
>
> **Notes.** Text only (ASCII; no images, bold, italic or chapter menu). The vendored FreeSerif headers have one include
> line removed (it made the build link a second copy of the GFX library); glyph data is byte-identical to Adafruit GFX 1.12.6.
> See `spi_bridge/assets/freeserif-NOTICE.txt` for the font licence.

*(When these are actually opened, each PR body ends with the usual "Generated with Claude Code" line and session link.)*
