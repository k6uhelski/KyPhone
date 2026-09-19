#!/usr/bin/env python3
"""Draw OS 0.2.1 screens on the REAL Inkplate over USB serial, without the Radxa.

The firmware accepts a line "@<command>" on its USB serial port and draws that screen exactly as if the
Radxa had sent it. This sends a set of representative screens (tests/firmware_host/screens.py), pausing
after each so you can look at the panel.

    /usr/bin/python3 spi_bridge/tools/preview_screens.py                 # every screen, 6 s apart
    /usr/bin/python3 spi_bridge/tools/preview_screens.py home texts      # just these
    /usr/bin/python3 spi_bridge/tools/preview_screens.py --list
    /usr/bin/python3 spi_bridge/tools/preview_screens.py --wire 'HOME2|12:44 PM|0|3'

    # a page of a book, as the reader would send it (needs the reader firmware flashed):
    /usr/bin/python3 spi_bridge/tools/preview_screens.py --book data/books/x.epub --size M --chapter 2 --page 3
    /usr/bin/python3 spi_bridge/tools/preview_screens.py --book data/books/x.epub --turns 10    # ten pages in a row, timed
    ...preview_screens.py --book data/books/x.epub --dry-run                                 # show the frames, touch nothing

Needs pyserial (the system /usr/bin/python3 has it) and the port free: stop
serial_log_macmini.py first (flash_macmini.sh does), and start it again afterwards.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tests', 'firmware_host'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from screens import SCREENS, encode          # noqa: E402


def book_pages(path, size, chapter, page, turns):
    """[(label, frames)] for `turns` consecutive pages of a book starting at (chapter, page). The first page is
    sent with a full refresh, the rest with partial ones (what the reader does), so timings are realistic."""
    import reader_epub
    import reader_layout as rl
    out = []
    with reader_epub.load(path) as book:
        ch = book.first_with_text() if chapter is None else chapter
        pages = rl.paginate(book.chapter(ch).paras, size)
        i = min(page, len(pages) - 1)
        for k in range(turns):
            if i >= len(pages):
                ch = book.next_with_text(ch, +1)
                if ch is None:
                    break
                pages, i = rl.paginate(book.chapter(ch).paras, size), 0
            right = '%d/%d  %d%%' % (i + 1, len(pages), int(100 * book.progress(ch, pages[i].start / max(1, rl.chapter_length(book.chapter(ch).paras)))))
            out.append(('chapter %d page %d/%d' % (ch, i + 1, len(pages)),
                        rl.page_frames(size, pages[i].lines, book.chapter(ch).title, right, 'F' if k == 0 else 'P')))
            i += 1
    return out


def send_and_wait(ser, wire, patience=20):
    """Send one "@<command>" line and wait for the board to say it has drawn it. True if it acknowledged."""
    ser.write(b'@' + encode(wire) + b'\n')
    t0 = time.time()
    while time.time() - t0 < patience:                       # e-ink refreshes take a couple of seconds
        line = ser.readline().decode('latin-1', 'replace').strip()
        if line.startswith('>> PREVIEW:') or 'Full refresh' in line:
            print('   board: ' + line[:90])
        if 'PREVIEW DONE' in line:
            return True
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('names', nargs='*', help='screens to draw (default: all)')
    ap.add_argument('--port', default='/dev/cu.usbserial-1140')
    ap.add_argument('--pause', type=float, default=6.0, help='seconds to leave each screen up')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--wire', help='send this one command instead of the canned screens')
    ap.add_argument('--book', help='an .epub to preview a page of')
    ap.add_argument('--size', default='M', choices=['S', 'M', 'L', 'X'], help='font size for --book')
    ap.add_argument('--chapter', type=int, help='chapter for --book (default: the first with text)')
    ap.add_argument('--page', type=int, default=0, help='page within the chapter for --book')
    ap.add_argument('--turns', type=int, default=1, help='with --book: send this many consecutive pages, timing each')
    ap.add_argument('--dry-run', action='store_true', help='print what would be sent and touch nothing')
    args = ap.parse_args()

    if args.list:
        print('\n'.join(SCREENS))
        return 0
    if args.book:
        pages = book_pages(args.book, args.size, args.chapter, args.page, args.turns)
        if args.dry_run:
            for label, frames in pages:
                print('%s: %d frames, longest %d chars' % (label, len(frames), max(len(f) for f in frames)))
                for f in frames:
                    print('   ' + f[:100].replace('\xb7', '|'))
            return 0
        return preview_pages(args, pages)
    if args.wire:
        todo = [('custom', args.wire)]
    else:
        names = args.names or list(SCREENS)
        unknown = [n for n in names if n not in SCREENS]
        if unknown:
            print('unknown screen(s): %s\n(use --list)' % ', '.join(unknown), file=sys.stderr)
            return 2
        todo = [(n, SCREENS[n][0]) for n in names]

    try:
        import serial
    except ImportError:
        print('pyserial is not installed (pip install pyserial)', file=sys.stderr)
        return 2
    ser = serial.Serial()
    ser.port, ser.baudrate, ser.timeout = args.port, 115200, 1
    ser.dtr = ser.rts = False                             # like serial_log_macmini.py: do not reset the board
    ser.open()
    with ser:
        time.sleep(0.5)
        ser.reset_input_buffer()        # NB: closing the port pulses the board's reset line; it reboots after the last screen
        for name, wire in todo:
            print('%-18s %s' % (name, wire[:70]))
            if send_and_wait(ser, wire):
                print('   drawn')
            else:
                print('   NO ACKNOWLEDGEMENT from the board (did it just reset? try again)', file=sys.stderr)
            time.sleep(args.pause)
    return 0


def open_port(args):
    import serial
    ser = serial.Serial()
    ser.port, ser.baudrate, ser.timeout = args.port, 115200, 1
    ser.dtr = ser.rts = False                             # like serial_log_macmini.py: do not reset the board
    ser.open()
    return ser


def preview_pages(args, pages):
    try:
        ser = open_port(args)
    except ImportError:
        print('pyserial is not installed (pip install pyserial)', file=sys.stderr)
        return 2
    with ser:
        time.sleep(0.5)
        ser.reset_input_buffer()
        for label, frames in pages:
            t0 = time.time()
            ok = all(send_and_wait(ser, f) for f in frames)
            print('%-28s %d frames  %s in %.1f s' % (label, len(frames), 'drawn' if ok else 'NOT ACKNOWLEDGED', time.time() - t0))
            if not ok:
                return 1
            time.sleep(args.pause if len(pages) == 1 else 1.0)
    return 0


if __name__ == '__main__':
    sys.exit(main())
