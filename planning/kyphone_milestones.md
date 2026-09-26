# KyPhone Project Milestones
*Updated: September 25, 2026 (OS 0.6.0)*

## ✅ Milestone 1: Stable SPI Bridge
Reliable one-way SPI from the Radxa to the Inkplate, with a handshake line for flow control. Now 256-byte frames, framed by 150 ms of clock silence, and the sender waits for each frame to be taken.

## ✅ Milestone 2: KyPhone OS
One state machine drives every screen from a single `state` dict; the Inkplate is a stateless renderer. A pygame emulator and a computer build of the firmware renderers mean every screen is tried before it reaches the panel.

## ✅ Milestone 3: Messaging
Compose, reply, retry, contacts, windowed lists, stop alerts. Sending goes through a cellular modem (Twilio was removed in 0.4.0); until the SIM is live, a send ends as NOT SENT.

## ✅ Milestone 4: Reading and Listening
READ (EPUB, four sizes, resume) and LISTEN (albums, tracks, now playing, background play).

## ✅ Milestone 5: Settings and Tools
Wi-Fi and Bluetooth, screen light, the lock screen's new-activity mark, Notes, calls and texts per person, and adding books, music and contacts from a computer (0.4.0 to 0.6.0).

## 🔄 Milestone 6: Untethered Hardware
Keyboard ✅, cellular modem ✅ (SIM to activate), battery and enclosure still to come. Real texts, then real calls, over the modem.

## Milestone 7: Daily Driver
Reliable enough to carry as the only phone: power management, reliable boot, updates without a cable. This is 1.0.
