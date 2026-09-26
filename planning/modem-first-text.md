# The first real text — checklist

For the day the SIM is activated. Everything before step 4 can be done ahead of time.

## Before (can be done now)
1. **Install pyserial on the Radxa** — it is not there yet (checked 2026-09-25), and without it the phone quietly
   runs with no modem: `sudo apt install python3-serial`.
2. ModemManager is **not** installed on the Radxa (checked 2026-09-25), so nothing else will grab the modem. If it
   ever appears (`systemctl is-active ModemManager`), stop it before testing.
3. Back up both devices as usual (the modem needs no firmware change; the Python is already on the Radxa).

## With the SIM
4. **Activate the SIM** with the carrier, in another phone if they ask for one. If that phone asks for a SIM PIN,
   turn the PIN off there.
5. **SIM into the dongle** (nano-SIM, gold contacts down), antennas attached, dongle into a Radxa USB port.
   Give it a minute.
6. **Run the check** on the Radxa:

       cd ~/kyphone && sudo /usr/bin/python3 spi_bridge/tools/modem_check.py

   It walks through: the modem answers → the SIM is in and unlocked → signal → joined the network (and which
   carrier) → an SMS centre is set. Each failure says what to do. Fix the first X and run it again.
7. **Send one text and reply to it:**

       sudo /usr/bin/python3 spi_bridge/tools/modem_check.py --send <your other phone> --wait 180

   The other phone should get "Hello from KyPhone…". Reply to it; the tool prints the reply when it arrives.

## Turn it on in the phone
8. Add `Environment=KYPHONE_MODEM_PORT=/dev/ttyUSB…` (the port the check reported) to `kyphone.service`, then
   `sudo systemctl daemon-reload && sudo systemctl restart kyphone`. The start-up log shows `Modem: /dev/ttyUSB… (signal …)`.
9. On the phone: TEXT → + → a number → a message → SEND. It should go SENDING… → SENT. Reply from the other phone:
   it should appear in the same conversation, and the lock screen should show the `*`.

## If something is off
- **NOT SENT on the phone but the check sent fine:** is `KYPHONE_MODEM_PORT` set in the service, and was it restarted?
- **Replies never arrive:** `journalctl -u kyphone` shows `Modem poll error: …` lines if polling fails.
- **Long texts** go as several texts (the modem's text mode cannot join them into one).
- To stop using the modem: remove the `KYPHONE_MODEM_PORT` line and restart; sends go back to NOT SENT.
