"""network_control.py — Wi-Fi and Bluetooth, wrapped so a missing tool, a non-Radxa host (the Mac, tests), or a
slow/failed call never raises. kyphone_os.py's state machine (SETTINGS / NETLIST / NETPASS / NETSTATE) reads only
these functions; nowhere else in the project calls `nmcli` or `bluetoothctl` directly.

Every external command is built as an argument list, never a shell string, so a typed SSID, device name or
password can never be read as extra shell syntax. Nothing here logs, prints, or hands the password to anything but
`nmcli` itself; the caller's copy is the only other one and it is never written to disk.

Checked read-only against the real Radxa (2026-09-22): `nmcli` (NetworkManager) owns the Wi-Fi interface and
`bluetoothctl` 5.55 is scriptable one command at a time. `bluetoothctl devices Connected` is not supported by this
version — connection state is read per device with `info` instead.
"""

import subprocess

WIFI_TIMEOUT = 12          # seconds for a scan or a connect attempt
BT_INFO_TIMEOUT = 8        # a single `bluetoothctl` command that talks to one device
BT_SCAN_SECONDS = 10       # how long a Bluetooth scan runs before it stops itself
BT_ACTION_TIMEOUT = 15     # a pair or connect attempt


class WifiNetwork:
    def __init__(self, ssid, signal, secured, in_use):
        self.ssid, self.signal, self.secured, self.in_use = ssid, signal, secured, in_use


class WifiStatus:
    def __init__(self, connected, ssid=None):
        self.connected, self.ssid = connected, ssid


class BtDevice:
    def __init__(self, mac, name, paired, connected):
        self.mac, self.name, self.paired, self.connected = mac, name, paired, connected


class BtStatus:
    def __init__(self, powered, connected_names):
        self.powered, self.connected_names = powered, connected_names


class Result:
    """A plain outcome for an action (connect, pair): ok, or not, with a short reason a NETSTATE screen can show."""
    def __init__(self, ok, detail=''):
        self.ok, self.detail = ok, detail


def _run(args, timeout):
    """Runs one command, list-form only (never a shell string). Never raises: a missing tool, a timeout, or any
    other failure comes back as (False, '', <reason>) instead of an exception, so a call on the Mac or in a test
    behaves like Wi-Fi/Bluetooth simply not being there."""
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode == 0, p.stdout, p.stderr
    except FileNotFoundError:
        return False, '', 'not installed'
    except subprocess.TimeoutExpired:
        return False, '', 'timed out'
    except OSError as e:
        return False, '', str(e)


# ─── Wi-Fi ──────────────────────────────────────────────────────────────────

def wifi_status():
    """The currently connected network, if any."""
    ok, out, _ = _run(['nmcli', '-t', '-f', 'DEVICE,TYPE,STATE,CONNECTION', 'dev', 'status'], WIFI_TIMEOUT)
    if not ok:
        return WifiStatus(False)
    for line in out.splitlines():
        parts = line.split(':')
        if len(parts) >= 4 and parts[1] == 'wifi' and parts[2] == 'connected':
            return WifiStatus(True, parts[3])
    return WifiStatus(False)


def wifi_scan():
    """Networks in range, strongest first. `nmcli` lists one row per access point; a network with several access
    points (most home Wi-Fi, repeaters included) is folded here to its single best signal. A hidden network (no
    SSID) is left out — there is no way to type one in on this screen yet."""
    ok, out, _ = _run(['nmcli', '-t', '-f', 'IN-USE,SSID,SIGNAL,SECURITY', 'dev', 'wifi', 'list'], WIFI_TIMEOUT)
    if not ok:
        return []
    best = {}
    for line in out.splitlines():
        parts = line.split(':')
        if len(parts) < 4:
            continue
        in_use, ssid, signal, security = parts[0], parts[1], parts[2], parts[3]
        if not ssid:
            continue
        try:
            signal = int(signal)
        except ValueError:
            signal = 0
        secured = security not in ('', '--')
        existing = best.get(ssid)
        in_use = in_use == '*' or (existing is not None and existing.in_use)   # the joined access point may be the weaker one
        if existing is None or signal > existing.signal:
            best[ssid] = WifiNetwork(ssid, signal, secured, in_use)
        else:
            existing.in_use = in_use
    return sorted(best.values(), key=lambda n: -n.signal)


def wifi_connect(ssid, password=None):
    """Joins a network. The password, if given, is one argument in the command list — never pasted into a shell
    string — and is not logged or returned."""
    args = ['nmcli', 'dev', 'wifi', 'connect', ssid]
    if password:
        args += ['password', password]
    ok, out, err = _run(args, WIFI_TIMEOUT)
    if ok:
        return Result(True)
    text = (err or out or '').lower()
    if 'secrets were required' in text or '802-11-wireless-security' in text:
        return Result(False, 'wrong password')
    if 'no network with ssid' in text:
        return Result(False, 'network not found')
    if 'timed out' in text or text == 'timed out':
        return Result(False, 'timed out')
    return Result(False, 'could not connect')


# ─── Bluetooth ──────────────────────────────────────────────────────────────

def _parse_device_lines(out):
    """`bluetoothctl devices` / `paired-devices` print `Device <MAC> <name>` one per line."""
    devices = {}
    for line in out.splitlines():
        parts = line.split(' ', 2)
        if len(parts) == 3 and parts[0] == 'Device':
            devices[parts[1]] = parts[2]
    return devices


def _bt_connected(mac):
    ok, out, _ = _run(['bluetoothctl', 'info', mac], BT_INFO_TIMEOUT)
    return ok and 'Connected: yes' in out


def bt_status():
    """Whether the radio is on, and the names of whatever is connected right now (paired devices only — this never
    scans, so it cannot disturb anything already connected, such as the keyboard)."""
    ok, out, _ = _run(['bluetoothctl', 'show'], BT_INFO_TIMEOUT)
    powered = ok and 'Powered: yes' in out
    paired = _parse_device_lines(_run(['bluetoothctl', 'paired-devices'], BT_INFO_TIMEOUT)[1])
    connected = [name for mac, name in paired.items() if _bt_connected(mac)]
    return BtStatus(powered, connected)


def bt_scan(seconds=BT_SCAN_SECONDS):
    """Nearby devices plus everything already paired, one row per address, paired devices first then by name. The
    scan stops itself after `seconds` (`bluetoothctl --timeout`), so nothing is left running in the background."""
    paired = _parse_device_lines(_run(['bluetoothctl', 'paired-devices'], BT_INFO_TIMEOUT)[1])

    _run(['bluetoothctl', '--timeout', str(seconds), 'scan', 'on'], seconds + 5)

    seen = _parse_device_lines(_run(['bluetoothctl', 'devices'], BT_INFO_TIMEOUT)[1])
    seen.update(paired)   # a paired device keeps its name even if this scan didn't happen to see it again

    devices = [BtDevice(mac, name, mac in paired, mac in paired and _bt_connected(mac))
               for mac, name in seen.items()]
    return sorted(devices, key=lambda d: (not d.paired, d.name.lower()))


def bt_pair_connect(mac):
    """Pairs (if not already) and connects one device, by address. Never touches any other device — an existing
    connection (e.g. the keyboard) is neither scanned for nor disconnected by this call."""
    ok, out, _ = _run(['bluetoothctl', 'info', mac], BT_INFO_TIMEOUT)
    already_paired = ok and 'Paired: yes' in out

    if not already_paired:
        ok, out, err = _run(['bluetoothctl', 'pair', mac], BT_ACTION_TIMEOUT)
        if not ok:
            text = (err or out or '').lower()
            if 'timeout' in text:
                return Result(False, 'timed out')
            if 'authenticationfailed' in text:
                return Result(False, 'pairing was refused')
            return Result(False, 'could not pair')

    ok, out, err = _run(['bluetoothctl', 'connect', mac], BT_ACTION_TIMEOUT)
    if ok:
        return Result(True)
    return Result(False, 'could not connect')
