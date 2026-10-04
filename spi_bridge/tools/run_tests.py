#!/usr/bin/env python3
"""Run every test suite: each spi_bridge/tests/test_*.py, and nothing else in that folder (the hardware diagnostics
there have other names and run on import). A scratch KYPHONE_DATA_DIR is made unless one is set, so the real data/
is never touched. A skipped test fails the run, because a skip here means a missing tool (pygame, clang++), not a
passing test.

    ~/.venvs/kyphone/bin/python spi_bridge/tools/run_tests.py              # everything
    ~/.venvs/kyphone/bin/python spi_bridge/tools/run_tests.py -k emoji     # extra arguments go to pytest
    ~/.venvs/kyphone/bin/python spi_bridge/tools/run_tests.py --allow-skips
"""
import glob
import os
import re
import subprocess
import sys
import tempfile

TESTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tests')


def suites():
    return sorted(glob.glob(os.path.join(TESTS, 'test_*.py')))


def main(argv):
    allow_skips = '--allow-skips' in argv
    extra = [a for a in argv if a != '--allow-skips']
    env = dict(os.environ)
    env.setdefault('KYPHONE_DATA_DIR', tempfile.mkdtemp(prefix='kyphone-tests-'))
    done = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-rs'] + suites() + extra,
                          env=env, capture_output=True, text=True)
    out = done.stdout + done.stderr
    summary = (out.strip().splitlines() or [''])[-1]
    if done.returncode != 0:
        print('\n'.join(out.splitlines()[-40:]))
        return done.returncode
    skipped = re.search(r'(\d+) skipped', summary)
    if skipped and not allow_skips:
        print('\n'.join(l for l in out.splitlines() if l.startswith('SKIPPED')))
        print('%s skipped: a tool is missing (pygame in this Python? clang++?) - %s' % (skipped.group(1), summary))
        return 1
    print(summary)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
