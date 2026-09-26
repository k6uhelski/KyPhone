"""version.py — the one place KyPhone's software version is defined.

    MAJOR.MINOR.PATCH

    MINOR  a new feature you can see or use (a screen, an app), or a new generation of the design
    PATCH  fixes and refinements that add no feature
    MAJOR  0 until KyPhone is a daily-driver phone (cellular, battery, enclosure); 1.0 is that release

Where it shows up, all from this file:
    the lock screen        the Radxa sends it in the LOCK command ("OS 0.3.1", bottom left), so it is always the
                           version of the software that is actually running
    the terminal banner    kyphone_os.py prints it at start-up
    the firmware           Inkplate_SPI_Peripheral/version.h is generated from it by tools/make_version.py; the firmware
                           logs it at boot and warns on the serial log if the Radxa's version differs from its own
    the docs               CLAUDE.md and README.md each state the current version; a test checks they agree

To change it: edit VERSION below, run `python3 spi_bridge/tools/make_version.py`, update the "Current version" line in
CLAUDE.md and README.md, then flash the firmware and deploy the Python together. Tests fail if any piece disagrees.
"""

VERSION = '0.6.1'
LABEL = 'OS ' + VERSION          # what the lock screen says

# The design library (docs/02-design/design_handoff_os_0_2/) is versioned separately: it names the design generation
# the screens were built from. A test checks this against the handoff's own title. Screens added after that handoff
# (the library and reader, the music screens, the equalizer mark, and the newer stop alerts) are laid out by us, not
# from the library, until they are designed and handed off.
DESIGN = '0.2.1'
