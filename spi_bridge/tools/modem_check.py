#!/usr/bin/env python3
"""modem_check.py — the first-real-text test, one step at a time, in plain words.

Run on the Radxa with the SIM7600G-H plugged in (and the phone software NOT using the modem yet, i.e. before
KYPHONE_MODEM_PORT is set in kyphone.service). Each step says what it checked and, if it failed, what to do:

    1. the modem answers AT                 (finds the AT port itself, like find_modem_port.py)
    2. the SIM is in and unlocked           (AT+CPIN?)
    3. there is signal                      (AT+CSQ)
    4. the modem has joined the network     (AT+CREG? / AT+CEREG?)  and which carrier (AT+COPS?)
    5. an SMS centre number is set          (AT+CSCA?)
    6. optional: send one text              --send 5550100001        (through modem.SerialModem, as the phone does)
    7. optional: wait for a reply           --wait 180               (prints what arrives)

    sudo /usr/bin/python3 spi_bridge/tools/modem_check.py
    sudo /usr/bin/python3 spi_bridge/tools/modem_check.py --send 5550100001 --wait 180

Needs pyserial (`sudo apt install python3-serial`). Nothing here changes the carrier setup; it only asks.
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))

CPIN_MEANING = {
    'READY':   (True,  'the SIM is in and unlocked'),
    'SIM PIN': (False, 'the SIM is locked with a PIN: remove the PIN in another phone first (Settings > SIM PIN)'),
    'SIM PUK': (False, 'the SIM is PUK-locked (too many wrong PINs): the carrier gives the PUK'),
}
REG_MEANING = {
    '0': (False, 'not registered and not searching: is the SIM activated? is the antenna attached?'),
    '1': (True,  'registered on the home network'),
    '2': (False, 'searching for a network (wait a minute; check the antenna)'),
    '3': (False, 'registration refused by the network: the SIM may not be activated yet'),
    '4': (False, 'unknown registration state'),
    '5': (True,  'registered (roaming)'),
}


def interpret_cpin(lines):
    for line in lines:
        if line.startswith('+CPIN:'):
            state = line.split(':', 1)[1].strip()
            return CPIN_MEANING.get(state, (False, 'the SIM reports "%s"' % state))
    return False, 'no SIM detected: is it inserted (gold contacts down) and pushed fully in?'


def interpret_csq(lines):
    for line in lines:
        if line.startswith('+CSQ:'):
            v = int(line.split(':', 1)[1].split(',')[0])
            if v == 99:
                return False, 'no signal: attach the antenna; try near a window'
            words = 'weak' if v < 10 else 'fair' if v < 15 else 'good' if v < 20 else 'excellent'
            return v >= 5, 'signal %d of 31 (%s)' % (v, words)
    return False, 'the modem gave no signal reading'


def interpret_reg(lines):
    """Registration from +CREG (2G/3G) or +CEREG (4G): the better of the two answers."""
    best = None
    for line in lines:
        for tag in ('+CREG:', '+CEREG:'):
            if line.startswith(tag):
                fields = [x.strip() for x in line.split(':', 1)[1].split(',')]
                stat = fields[1] if len(fields) > 1 else fields[0]
                ok, words = REG_MEANING.get(stat, (False, 'registration state %s' % stat))
                if best is None or (ok and not best[0]):
                    best = (ok, words)
    return best or (False, 'the modem gave no registration answer')


def interpret_csca(lines):
    for line in lines:
        if line.startswith('+CSCA:'):
            number = line.split(':', 1)[1].split(',')[0].strip().strip('"')
            if number:
                return True, 'SMS centre set (%s)' % number
    return False, 'no SMS centre number: the carrier normally sets it on the SIM; ask them for it (AT+CSCA="+1...")'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--port', help='the AT port, e.g. /dev/ttyUSB2 (found automatically if left out)')
    ap.add_argument('--send', metavar='NUMBER', help='send one test text to this number')
    ap.add_argument('--text', default='Hello from KyPhone. Reply to this to test receiving.')
    ap.add_argument('--wait', type=int, default=0, metavar='SECONDS', help='then wait this long for a reply')
    args = ap.parse_args(argv)

    try:
        import serial  # noqa: F401
    except ImportError:
        print('X  pyserial is missing. Install it:  sudo apt install python3-serial')
        return 2
    import modem
    import find_modem_port as fmp

    port = args.port
    if not port:
        for candidate in fmp.candidates(fmp.DEFAULT_PATTERNS):
            ok, _ = fmp.probe(candidate, fmp.DEFAULT_BAUD, fmp.PROBE_TIMEOUT)
            if ok:
                port = candidate
                break
    if not port:
        print('X  1. no port answered AT. Is the dongle plugged in? (ls /dev/ttyUSB*)  Is anything else using it?')
        return 1
    print('ok 1. the modem answers on %s' % port)

    try:
        m = modem.SerialModem(port=port)
    except modem.ModemError as e:
        print('X  1. could not set the modem up: %s' % e)
        return 1

    steps = [('2', 'AT+CPIN?', interpret_cpin), ('3', 'AT+CSQ', interpret_csq),
             ('4', None, interpret_reg), ('5', 'AT+CSCA?', interpret_csca)]
    failed = False
    for num, command, interpret in steps:
        try:
            with m._lock:
                if command is None:
                    lines = m._cmd('AT+CREG?') + m._cmd('AT+CEREG?')
                else:
                    lines = m._cmd(command)
        except modem.ModemError:
            lines = []                                       # e.g. +CME ERROR: 10, "SIM not inserted"
        ok, words = interpret(lines)
        print(('ok ' if ok else 'X  ') + '%s. %s' % (num, words))
        failed = failed or not ok
        if num == '4' and ok:
            try:
                with m._lock:
                    op = [l for l in m._cmd('AT+COPS?') if l.startswith('+COPS:')]
                if op and '"' in op[0]:
                    print('      carrier: %s' % op[0].split('"')[1])
            except modem.ModemError:
                pass
    if failed:
        print('\nFix the first X above, then run this again.')
        return 1

    if args.send:
        digits = ''.join(ch for ch in args.send if ch.isdigit())
        number = '+1' + digits if len(digits) == 10 else '+' + digits if len(digits) == 11 else args.send
        try:
            m.send(number, args.text)                        # +1XXXXXXXXXX, as the phone itself sends
            print('ok 6. sent a test text to %s: check that phone' % number)
        except modem.ModemError as e:
            print('X  6. the text did not send: %s' % e)
            return 1

    if args.wait:
        print('   7. waiting %d s for a text (reply from the other phone now)...' % args.wait)
        end = time.monotonic() + args.wait
        while time.monotonic() < end:
            try:
                for msg in m.poll_new():
                    print('ok 7. received from %s at %s: %s' % (msg['sender'], msg['ts'], msg['body']))
                    return 0
            except modem.ModemError as e:
                print('   (poll: %s)' % e)
            time.sleep(3)
        print('X  7. nothing arrived in %d s' % args.wait)
        return 1
    print('\nAll checks passed. Next: set KYPHONE_MODEM_PORT=%s in kyphone.service and restart.' % port)
    return 0


if __name__ == '__main__':
    sys.exit(main())
