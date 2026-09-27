#!/usr/bin/env python3
"""link_report.py — is the phone's wiring working? One line per run, appended to a health log.

Reads what arrived since the last run from two places:
    the Inkplate's USB serial log (serial_log_macmini.py writes it): every screen it drew (SUCCESS), every frame
        that arrived with clock pulses missing (PARTIAL) or damaged (CHECKSUM MISMATCH), and firmware boots;
    the Radxa's journal for kyphone.service (over ssh): screens the Inkplate never took ("never signalled busy"),
        modem trouble, and restarts.
and appends one line to the health log: OK, or PROBLEM with what went wrong and when. Three or more bad frames
in a row with no good one between is an outage, listed with its times. A stray bad frame (a board powering up
makes a few) is counted but is not a PROBLEM on its own.

    python3 spi_bridge/tools/link_report.py            # run by launchd every hour on the Mac mini
    python3 spi_bridge/tools/link_report.py --print    # also print the line

Nothing here reads message text: only the kind of each line is counted.
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime

SERIAL_LOG = os.environ.get('KYPHONE_SERIAL_LOG', '/tmp/inkplate_serial.log')
OUT_DIR    = os.path.expanduser(os.environ.get('KYPHONE_HEALTH_DIR', '~/kyphone-logs'))
HEALTH_LOG = os.path.join(OUT_DIR, 'link-health.log')
STATE_FILE = os.path.join(OUT_DIR, '.link_state.json')
RADXA      = os.environ.get('KYPHONE_RADXA', 'radxa')    # the ssh host alias
OUTAGE_RUN = 3        # bad frames in a row that make an outage
STRAY_OK   = 2        # bad frames allowed in one run without calling it a problem (boot noise)

_STAMP = re.compile(r'^\[(\d\d:\d\d:\d\d)')


def read_new_serial(state):
    """The serial log lines written since the last run (the log restarting from empty is handled)."""
    try:
        st = os.stat(SERIAL_LOG)
    except OSError:
        return None
    size = st.st_size
    offset = state.get('offset', size)          # first run: start from now, not from the log's whole history
    if size < offset or state.get('inode', st.st_ino) != st.st_ino:
        offset = 0                              # the log was emptied or replaced: read it from the top
    state['inode'] = st.st_ino
    with open(SERIAL_LOG, 'rb') as f:
        f.seek(offset)
        data = f.read()
    state['offset'] = offset + len(data)
    return data.decode('latin-1', errors='replace').splitlines()


def summarize_serial(lines):
    ok = short = crc = boots = 0
    outages, run, run_start = [], 0, None
    for line in lines:
        m = _STAMP.match(line)
        t = m.group(1) if m else '?'
        if 'SUCCESS! MSG:' in line:
            ok += 1
            if run >= OUTAGE_RUN:
                outages.append((run_start, t, run))
            run = 0
        elif '>> PARTIAL:' in line or 'CHECKSUM MISMATCH' in line:
            if '>> PARTIAL:' in line:
                short += 1
            else:
                crc += 1
            if run == 0:
                run_start = t
            run += 1
        elif '>> KyPhone firmware' in line:
            boots += 1
    ongoing = (run_start, run) if run >= OUTAGE_RUN else None
    return {'ok': ok, 'short': short, 'crc': crc, 'boots': boots, 'outages': outages, 'ongoing': ongoing}


def read_radxa(since):
    """Counts from the Radxa's journal since `since` (an ISO time), or None if the Radxa did not answer."""
    cmd = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', RADXA,
           'sudo journalctl -u kyphone --since "%s" --no-pager -o cat' % since]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    lines = out.stdout.splitlines()
    count = lambda s: sum(1 for l in lines if s in l)
    return {'not_taken':  count('never signalled busy'),
            'modem_err':  count('Modem poll error'),
            'modem_lost': count('Modem stopped answering'),
            'modem_up':   count('Modem ready'),
            'modem_wait': count('Modem not ready yet'),
            'restarts':   count('--- KyPhone OS')}


def report(state, now):
    since = state.get('since') or now.replace(minute=0, second=0).isoformat(sep=' ')
    problems, parts = [], []
    first = 'offset' not in state
    lines = read_new_serial(state)
    if first and lines is not None:
        parts.append('first run: watching from now')
    elif lines is None:
        problems.append('no Inkplate log at %s (is serial_log_macmini.py running?)' % SERIAL_LOG)
    elif not lines:
        problems.append('the Inkplate log did not grow (USB logger stopped, or the Inkplate is unplugged/off)')
    else:
        s = summarize_serial(lines)
        parts.append('screens %d ok, %d cut short, %d bad checksum' % (s['ok'], s['short'], s['crc']))
        if s['boots']:
            parts.append('Inkplate booted %dx' % s['boots'])
        for start, end, n in s['outages']:
            problems.append('outage %s-%s (%d frames lost)' % (start, end, n))
        if s['ongoing']:
            problems.append('outage since %s, still going (%d frames lost)' % s['ongoing'])
        if not s['outages'] and not s['ongoing'] and s['short'] + s['crc'] > STRAY_OK:
            problems.append('%d bad frames' % (s['short'] + s['crc']))
    r = read_radxa(since)
    if r is None:
        problems.append('the Radxa did not answer over ssh')
    else:
        parts.append('%d not taken' % r['not_taken'])
        if r['restarts']:
            parts.append('phone software started %dx' % r['restarts'])
        if r['modem_lost']:
            problems.append('modem stopped answering %dx' % r['modem_lost'])
        elif r['modem_err']:
            parts.append('%d modem error runs' % r['modem_err'])
        if r['modem_wait'] and not r['modem_up']:
            problems.append('modem not up')
        if r['not_taken'] > STRAY_OK and not any(p.startswith('outage') for p in problems):
            problems.append('%d screens not taken by the Inkplate' % r['not_taken'])
    state['since'] = now.isoformat(sep=' ', timespec='seconds')
    head = '%s  %-7s' % (now.strftime('%Y-%m-%d %H:%M'), 'PROBLEM' if problems else 'OK')
    return head + '  ' + '; '.join(problems + parts)


def main(argv):
    os.makedirs(OUT_DIR, exist_ok=True)
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    line = report(state, datetime.now())
    with open(HEALTH_LOG, 'a') as f:
        f.write(line + '\n')
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)
    if '--print' in argv:
        print(line)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
