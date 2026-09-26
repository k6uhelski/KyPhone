# KyPhone Project Backlog
*Updated: September 25, 2026 (OS 0.6.0)*

## Next
- [ ] Real texting through the SIM7600G-H modem: activate the SIM, plug the dongle into the Radxa, find its AT port (`tools/find_modem_port.py`), set `KYPHONE_MODEM_PORT`, send and receive one real text
- [ ] Try Add from a computer on the real phone: a book, an album, a contacts file

## Messaging
- [x] Compose, reply, retry a text that did not send, message states (SENDING / SENT / NOT SENT)
- [x] Address book: look up, create, edit, delete; numbers matched by their last ten digits
- [x] Real sending path through a cellular modem (Twilio removed, 0.4.0)
- [ ] Prove the modem path end to end with a real SIM (see Next)

## Calls
- [x] Call screens and a call log (simulated until the modem carries voice)
- [x] Calls and texts per person: a call-log row opens the person's page; a conversation shows the last call (0.6.0)
- [ ] Real voice calls through the modem (audio routing on the Radxa)

## Reading and listening
- [x] READ: EPUB reader with four text sizes, progress, resume
- [x] LISTEN: albums, tracks, now playing, background play, resume
- [ ] Reader: a chapter menu
- [ ] Music: shuffle and repeat
- [ ] Bluetooth headphones (PulseAudio's Bluetooth module; the service runs as root)

## Tools
- [x] Notes (0.6.0)
- [x] Add books, music and contacts from a computer over home Wi-Fi (0.6.0)

## Settings
- [x] Wi-Fi: switch, joined network (forget), networks nearby, password box (0.4.x)
- [x] Bluetooth: switch, known devices (forget / re-connect), pair new devices; the keyboard can never be forgotten (0.4.x)
- [x] Screen light and the lock screen's new-activity mark (0.5.0)
- [ ] Bluetooth devices that need a pairing code

## Hardware
- [x] Keyboard (ZitaoTech Q10, Bluetooth)
- [x] Cellular modem in hand (Waveshare SIM7600G-H USB dongle); SIM bought, not yet activated
- [ ] Battery and enclosure

## Polish
- [x] Bitmap icon system for the home menu (`spi_bridge/tools/make_icons.py`)
- [ ] A design handoff that covers the screens laid out by us (READ, LISTEN, SETTINGS, NOTES, the upload screen, stop alerts)
- [ ] Font and layout refinements
