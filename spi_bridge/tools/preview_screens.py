#!/usr/bin/env python3
"""Draw OS 0.2.1 screens on the REAL Inkplate over USB serial, without the Radxa.

The firmware accepts a line "@<command>" on its USB serial port and draws that screen exactly as if the
Radxa had sent it. This sends a set of representative screens (tests/firmware_host/screens.py), pausing
after each so you can look at the panel.

    /usr/bin/python3 spi_bridge/tools/preview_screens.py                 # every screen, 6 s apart
    /usr/bin/python3 spi_bridge/tools/preview_screens.py home texts      # just these
    /usr/bin/python3 spi_bridge/tools/preview_screens.py --list
    /usr/bin/python3 spi_bridge/tools/preview_screens.py --wire 'HOME2|12:44 PM|0|3'

Needs pyserial (the system /usr/bin/python3 has it) and the port free: stop
serial_log_macmini.py first (flash_macmini.sh does), and start it again afterwards.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tests', 'firmware_host'))
from screens import SCREENS, encode          # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('names', nargs='*', help='screens to draw (default: all)')
    ap.add_argument('--port', default='/dev/cu.usbserial-1140')
    ap.add_argument('--pause', type=float, default=6.0, help='seconds to leave each screen up')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--wire', help='send this one command instead of the canned screens')
    args = ap.parse_args()

    if args.list:
        print('\n'.join(SCREENS))
        return 0
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
        ser.reset_input_buffer()
        for name, wire in todo:
            print('%-18s %s' % (name, wire[:70]))
            ser.write(b'@' + encode(wire) + b'\n')
            t0 = time.time()
            while time.time() - t0 < 20:                  # e-ink refreshes take a couple of seconds
                line = ser.readline().decode('latin-1', 'replace').strip()
                if 'PREVIEW DONE' in line:
                    break
            else:
                print('   (no acknowledgement from the board)', file=sys.stderr)
            time.sleep(args.pause)
    return 0


if __name__ == '__main__':
    sys.exit(main())
