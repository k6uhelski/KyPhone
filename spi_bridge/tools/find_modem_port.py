#!/usr/bin/env python3
"""find_modem_port.py — which /dev/ttyUSB* is the SIM7600G-H's AT command port.

The dongle enumerates as several serial ports (AT command, diagnostic, GPS NMEA, ...) — which
/dev/ttyUSBn lands on which depends on USB enumeration order, so this probes each candidate port with a
plain "AT" and reports which one answers "OK": the standard way any AT-command tool finds the right port.
Nothing here changes the modem's settings (unlike modem.SerialModem's own startup, which also sets text
mode and the character set) — this only asks "are you listening?".

    /usr/bin/python3 spi_bridge/tools/find_modem_port.py
    /usr/bin/python3 spi_bridge/tools/find_modem_port.py --pattern '/dev/ttyUSB*' --baud 115200

Needs pyserial (the system /usr/bin/python3 usually has it — see CLAUDE.md's Mac-mini/Radxa notes).
Run this on the Radxa, after plugging the dongle in — that is where KYPHONE_MODEM_PORT is actually set.
"""
import argparse
import glob
import sys
import time

DEFAULT_PATTERNS = ['/dev/ttyUSB*', '/dev/ttyACM*']
DEFAULT_BAUD = 115200
PROBE_TIMEOUT = 2.0     # seconds to wait for a reply to one "AT"


def candidates(patterns):
    found = []
    for pattern in patterns:
        found.extend(sorted(glob.glob(pattern)))
    return found


def probe(port, baud, timeout):
    """Opens `port`, sends a plain "AT\\r", and returns (ok, detail). Never raises: a port that is busy,
    missing, or answers with anything but OK is just reported, not fatal to the rest of the scan."""
    import serial
    try:
        ser = serial.Serial(port, baud, timeout=0.2)
    except Exception as e:
        return False, 'could not open (%s)' % e
    try:
        try:
            ser.reset_input_buffer()
        except Exception:
            pass
        ser.write(b'AT\r')
        deadline = time.monotonic() + timeout
        buf = b''
        while time.monotonic() < deadline:
            buf += ser.read(max(1, ser.in_waiting))
            if b'OK' in buf:
                return True, 'answered OK'
        if buf:
            return False, 'answered, but not OK: %r' % buf[:80]
        return False, 'no answer'
    finally:
        ser.close()


def main(argv):
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument('--pattern', action='append', help='glob to scan (default: /dev/ttyUSB* and /dev/ttyACM*)')
    p.add_argument('--baud', type=int, default=DEFAULT_BAUD)
    p.add_argument('--timeout', type=float, default=PROBE_TIMEOUT)
    args = p.parse_args(argv)

    try:
        import serial  # noqa: F401
    except ImportError:
        print('pyserial is not installed (pip install pyserial; the system /usr/bin/python3 usually has it)',
              file=sys.stderr)
        return 1

    patterns = args.pattern or DEFAULT_PATTERNS
    ports = candidates(patterns)
    if not ports:
        print('No serial ports matched %s. Is the dongle plugged in?' % patterns)
        return 1

    print('Probing %d port(s) at %d baud...\n' % (len(ports), args.baud))
    hits = []
    for port in ports:
        ok, detail = probe(port, args.baud, args.timeout)
        print('  %-18s %s' % (port, ('OK  — ' + detail) if ok else detail))
        if ok:
            hits.append(port)

    print()
    if not hits:
        print("No port answered AT. Check the dongle is plugged in and try again in a few seconds —")
        print('it can take a moment to enumerate after being plugged in.')
        return 1
    if len(hits) == 1:
        print('Found it: %s' % hits[0])
        print('Set this on the Radxa:  export KYPHONE_MODEM_PORT=%s' % hits[0])
    else:
        print('More than one port answered OK — unusual, but not impossible. Try the first one; if that')
        print("turns out wrong, KyPhone will just fall back to 'no service' and the others are here too:")
        for h in hits:
            print('  KYPHONE_MODEM_PORT=%s' % h)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
