#!/usr/bin/env python3
"""Run every test suite: each spi_bridge/tests/test_*.py, and nothing else in that folder (the hardware diagnostics
there have other names and run on import). A scratch KYPHONE_DATA_DIR is made unless one is set, so the real data/
is never touched. A skipped test fails the run, because a skip here means a missing tool (pygame, clang++), not a
passing test. Started under Rosetta (an x86_64 shell on Apple Silicon, as a remote session's shell can be), pytest is
run as arm64, since the venv's pygame is arm64 only.

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


def under_rosetta():
    if sys.platform != 'darwin':
        return False
    try:
        return subprocess.run(['sysctl', '-n', 'sysctl.proc_translated'], capture_output=True,
                              text=True).stdout.strip() == '1'
    except OSError:
        return False


def main(argv):
    allow_skips = '--allow-skips' in argv
    extra = [a for a in argv if a != '--allow-skips']
    env = dict(os.environ)
    env.setdefault('KYPHONE_DATA_DIR', tempfile.mkdtemp(prefix='kyphone-tests-'))
    python = ['arch', '-arm64', sys.executable] if under_rosetta() else [sys.executable]
    done = subprocess.run(python + ['-m', 'pytest', '-q', '-rs'] + suites() + extra,
                          env=env, capture_output=True, text=True)
    out = done.stdout + done.stderr
    # pytest's own summary line ("987 passed, 2 skipped in 30s"), found by what it says: a test's background thread can
    # print after it, so the last line is not always the summary.
    summary = next((l for l in reversed(done.stdout.splitlines()) if re.search(r'\d+ (passed|failed|error)', l)), '')
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
