"""
test_version.py — the software has one version number, and everything that shows it agrees (spi_bridge/version.py).

Pure Python: it reads the files and runs the header generator; no hardware, no pygame.

    python3 -m pytest spi_bridge/tests/test_version.py -v
"""

import os
import re
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SPI = os.path.join(HERE, '..')
ROOT = os.path.join(SPI, '..')
sys.path.insert(0, SPI)
import version  # noqa: E402


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


class TheNumber(unittest.TestCase):
    def test_it_is_major_minor_patch(self):
        self.assertRegex(version.VERSION, r'^\d+\.\d+\.\d+$')
        self.assertEqual(version.LABEL, 'OS ' + version.VERSION)

    def test_the_design_generation_is_recorded_too(self):
        self.assertRegex(version.DESIGN, r'^\d+\.\d+(\.\d+)?$')


class Everywhere(unittest.TestCase):
    def test_the_firmware_header_is_generated_from_it_and_current(self):
        out = subprocess.run([sys.executable, os.path.join(SPI, 'tools', 'make_version.py'), '--check'], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn('#define KYPHONE_VERSION "%s"' % version.VERSION, read('spi_bridge', 'Inkplate_SPI_Peripheral', 'version.h'))

    def test_the_docs_state_the_current_version(self):
        for doc in ('CLAUDE.md', 'README.md'):
            m = re.search(r'Current version:\s*(\d+\.\d+\.\d+)', read(doc))
            self.assertIsNotNone(m, doc + ' has no "Current version" line')
            self.assertEqual(m.group(1), version.VERSION, doc)

    def test_the_docs_and_the_design_handoff_agree_on_the_design_generation(self):
        m = re.search(r'Design library:\s*\**\s*(\d+\.\d+(?:\.\d+)?)', read('CLAUDE.md'))
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), version.DESIGN)
        title = read('docs', '02-design', 'design_handoff_os_0_2', 'README.md').splitlines()[0]
        self.assertIn('KyPhone OS %s' % version.DESIGN, title)                 # the design library's own name for itself

    def test_the_os_prints_and_sends_the_constant_not_a_typed_number(self):
        src = read('spi_bridge', 'kyphone_os.py')
        self.assertIn('VERSION = version.VERSION', src)
        self.assertIn('KyPhone OS {VERSION}', src)
        self.assertRegex(src, r'LOCK\|\{time_str\}\|\{date_str\}\|\{quote\[:max_quote\]\}\|- THICH NHAT HANH\|\{VERSION\}')

    def test_no_drawing_code_has_a_version_typed_in(self):
        for path in (('spi_bridge', 'simulator.py'), ('spi_bridge', 'Inkplate_SPI_Peripheral', 'Inkplate_SPI_Peripheral.ino'),
                     ('spi_bridge', 'Inkplate_SPI_Peripheral', 'ui_screens.h'), ('spi_bridge', 'Inkplate_SPI_Peripheral', 'ui_reader.h')):
            self.assertNotRegex(read(*path), r'["\']OS \d+\.\d+', '/'.join(path))

    def test_the_firmware_logs_its_version_and_warns_when_the_radxa_differs(self):
        ino = read('spi_bridge', 'Inkplate_SPI_Peripheral', 'Inkplate_SPI_Peripheral.ino')
        self.assertIn('#include "version.h"', ino)
        self.assertIn('KyPhone firmware %s', ino)
        self.assertIn('strcmp(version_buf, KYPHONE_VERSION) != 0', ino)
        self.assertIn('flash and deploy them together', ino)


if __name__ == '__main__':
    unittest.main()
