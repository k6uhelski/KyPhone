#!/usr/bin/env python3
"""link_speed_test.py — how fast can the Radxa -> Inkplate SPI link run without errors?

Run ON THE RADXA (it needs the SPI port and the handshake line, so it stops kyphone.service for the length of the
test and starts it again afterwards, whatever happens). For each clock speed it sends test screens exactly the way
the phone does (wait for Ready, one 256-byte transfer, wait for the Inkplate to take it) and records what it sent in
a JSON file. The Inkplate prints every screen it accepts on its USB serial log ("SUCCESS! MSG: ..."), so comparing
that log with the JSON shows, per speed, how many screens arrived exactly right, how many were rejected (a wrong bit
count) and — the dangerous kind — how many arrived with the right length but wrong characters.

    sudo /usr/bin/python3 spi_bridge/tools/link_speed_test.py --speeds 10000,20000,40000,80000 --frames 30

Each test screen is a harmless stop alert ("LINK TEST") whose body cycles through every printable character, so
every bit pattern is exercised. The phone is back to normal when the script ends.
"""
import argparse
import json
import subprocess
import sys
import time

CHIP, HANDSHAKE_LINE, SPI_BUS, SPI_DEV, PAYLOAD_BYTES = 'gpiochip3', 21, 3, 0, 256
CHARS = [chr(c) for c in range(0x20, 0x7f) if chr(c) != '|']


def test_screen(hz, n):
    head = 'STUB|LINK TEST %d HZ|%03d ' % (hz, n)
    room = PAYLOAD_BYTES - 3 - len(head)
    body = ''.join(CHARS[(n * 7 + i) % len(CHARS)] for i in range(room))
    return head + body


def crc8(data):
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def payload(text, checked=True):
    """The phone's own frame: [0xA5, crc8(text bytes), 0x02, text, padding] (checked=False: the old unchecked form)."""
    body = [ord(c) for c in text[:PAYLOAD_BYTES - 3]]
    body += [0x00] * (PAYLOAD_BYTES - 3 - len(body))
    return ([0xA5, crc8(body), 0x02] if checked else [0x00, 0x00, 0x02]) + body


def wait(line, value, timeout):
    t0 = time.monotonic()
    while int(line.get_value()) != value:
        if time.monotonic() - t0 > timeout:
            return False
        time.sleep(0.002)
    return True


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--speeds', default='10000,20000,40000,80000')
    ap.add_argument('--frames', type=int, default=30)
    ap.add_argument('--out', default='/tmp/link_speed_test.json')
    ap.add_argument('--unchecked', action='store_true', help='send the old frame form (firmware before 0.6.3)')
    args = ap.parse_args(argv)
    speeds = [int(s) for s in args.speeds.split(',')]

    import gpiod
    import spidev
    subprocess.run(['systemctl', 'stop', 'kyphone'], check=False)
    time.sleep(1.5)
    results = {'started': time.time(), 'runs': []}
    try:
        chip = gpiod.Chip(CHIP)
        line = chip.get_line(HANDSHAKE_LINE)
        line.request(consumer='link-speed-test', type=gpiod.LINE_REQ_DIR_IN)
        spi = spidev.SpiDev()
        spi.open(SPI_BUS, SPI_DEV)
        spi.mode = 0
        for hz in speeds:
            spi.max_speed_hz = hz
            run = {'hz': hz, 'frames': []}
            for n in range(args.frames):
                text = test_screen(hz, n)
                t_ready = time.monotonic()
                ready = wait(line, 1, 10)
                t_send = time.monotonic()
                spi.xfer2(payload(text, checked=not args.unchecked))
                t_sent = time.monotonic()
                taken = wait(line, 0, 3)
                t_taken = time.monotonic()
                run['frames'].append({'n': n, 'text': text, 'ready': ready, 'taken': taken,
                                      'ready_wait': round(t_send - t_ready, 3), 'transfer': round(t_sent - t_send, 3),
                                      'until_taken': round(t_taken - t_sent, 3), 'at': time.time()})
            results['runs'].append(run)
            tr = sorted(f['transfer'] for f in run['frames'])
            print('%6d Hz: transfer %.3f s, taken %d/%d' % (hz, tr[len(tr) // 2],
                  sum(f['taken'] for f in run['frames']), len(run['frames'])), flush=True)
        spi.close()
        line.release()
    finally:
        results['ended'] = time.time()
        with open(args.out, 'w') as f:
            json.dump(results, f)
        subprocess.run(['systemctl', 'start', 'kyphone'], check=False)
    return 0


if __name__ == '__main__':
    sys.exit(main())
