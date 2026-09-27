"""tools/link_report.py: the hourly wiring check on the Mac mini, against made-up serial logs and journals."""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import link_report as lr  # noqa: E402

OK = '[%s] SUCCESS! MSG: LOCK|8:45 PM|...'
SHORT = '[%s] >> PARTIAL: 230/2048 bits in 49ms. Resetting.'
CRC = '[%s] >> CHECKSUM MISMATCH (sent 1A, got 2B). Frame dropped.'
RADXA_FINE = {'not_taken': 0, 'modem_err': 0, 'modem_lost': 0, 'modem_up': 0, 'modem_wait': 0, 'restarts': 0}


class Summary(unittest.TestCase):
    def test_an_outage_is_three_or_more_bad_frames_in_a_row(self):
        s = lr.summarize_serial([OK % '20:48:29', SHORT % '20:49:29', SHORT % '20:50:04', CRC % '20:50:29',
                                 OK % '20:54:06', SHORT % '20:55:00', OK % '20:55:01'])
        self.assertEqual(s['outages'], [('20:49:29', '20:54:06', 3)])
        self.assertEqual((s['ok'], s['short'], s['crc']), (3, 3, 1))

    def test_an_outage_still_going_is_reported(self):
        s = lr.summarize_serial([OK % '21:00:00'] + [SHORT % '21:01:0%d' % i for i in range(4)])
        self.assertEqual(s['ongoing'], ('21:01:00', 4))


class Report(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.log = os.path.join(self.dir, 'serial.log')
        for name, value in (('SERIAL_LOG', self.log), ('OUT_DIR', self.dir),
                            ('HEALTH_LOG', os.path.join(self.dir, 'health.log')),
                            ('STATE_FILE', os.path.join(self.dir, 'state.json'))):
            p = patch.object(lr, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.radxa = dict(RADXA_FINE)
        p = patch.object(lr, 'read_radxa', lambda since: self.radxa)
        p.start()
        self.addCleanup(p.stop)
        self.write('[20:00:00] >> KyPhone firmware 0.6.11')     # history before the first run

    def write(self, *lines):
        with open(self.log, 'a') as f:
            f.write('\n'.join(lines) + '\n')

    def run_once(self):
        lr.main([])
        with open(lr.HEALTH_LOG) as f:
            return f.read().splitlines()[-1]

    def test_first_run_starts_from_now_then_each_run_reads_only_what_is_new(self):
        first = self.run_once()                                  # the old history is not re-reported
        self.assertIn('  OK  ', first)
        self.assertIn('first run', first)
        self.write(OK % '21:00:01', OK % '21:00:02')
        line = self.run_once()
        self.assertIn('  OK  ', line)
        self.assertIn('screens 2 ok, 0 cut short', line)
        self.write(OK % '22:00:01')
        self.assertIn('screens 1 ok', self.run_once())

    def test_an_outage_is_a_problem_with_its_times(self):
        self.run_once()
        self.write(OK % '21:00:01', SHORT % '21:01:00', SHORT % '21:02:00', SHORT % '21:03:00', OK % '21:05:00')
        line = self.run_once()
        self.assertIn('PROBLEM', line)
        self.assertIn('outage 21:01:00-21:05:00 (3 frames lost)', line)

    def test_a_little_boot_noise_is_not_a_problem(self):
        self.run_once()
        self.write(SHORT % '21:00:00', OK % '21:00:01', SHORT % '21:00:05', OK % '21:00:06')
        self.assertIn('  OK  ', self.run_once())

    def test_a_silent_log_or_a_missing_radxa_is_a_problem(self):
        self.run_once()
        self.assertIn('the Inkplate log did not grow', self.run_once())
        self.write(OK % '21:00:01')
        self.radxa = None
        self.assertIn('the Radxa did not answer', self.run_once())

    def test_modem_trouble_is_a_problem(self):
        self.run_once()
        self.write(OK % '21:00:01')
        self.radxa = dict(RADXA_FINE, modem_lost=1)
        self.assertIn('modem stopped answering', self.run_once())
        self.write(OK % '21:00:02')
        self.radxa = dict(RADXA_FINE, modem_wait=6)
        self.assertIn('modem not up', self.run_once())

    def test_a_log_that_starts_over_is_read_from_the_top(self):
        self.run_once()
        os.remove(self.log)
        self.write(OK % '21:00:01')
        self.assertIn('screens 1 ok', self.run_once())

    def test_no_message_text_reaches_the_health_log(self):
        self.run_once()
        self.write('[21:00:01] SUCCESS! MSG: THREAD2|Contact 1|secret draft|...')
        self.assertNotIn('secret', self.run_once())


if __name__ == '__main__':
    unittest.main()
