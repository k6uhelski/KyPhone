# KyPhone case

Layout concepts for the enclosure, written in [build123d](https://build123d.readthedocs.io) (Python CAD).
The parts are box envelopes, sized from the makers' own files. These files are for choosing a layout, not for printing.

## Concepts (`out/`)

| Concept | Size (w × h × t, mm) | Idea |
| :--- | :--- | :--- |
| `1_brick` | 95 × 142 × 47 | One body, two layers: screen over keyboard; battery and dongle behind the screen, the Rock 3A behind the keyboard |
| `2_remote` | 95 × 206 × 36 | One tall body: screen, keyboard, and a "chin" for the Rock 3A lying flat |
| `3_corded` | handset 83 × 141 × 19, base 95 × 118 × 37 | A thin handset (screen and keyboard, each already has its own battery) on a coiled cord to a pocket base with the Rock 3A, dongle and battery |

Every concept is built once per **modem option** (`out/<modem>/`; all are the same SIM7600G-H chip, so `modem.py` works with any): `dongle` (as bought), `module` (Waveshare SIM7600G-H 4G Module, 38.8 × 42) and `hat_b` (Waveshare 4G HAT (B), 65 × 32). Only published sizes are used; the two small boards' thickness is not published, so it is drawn as 8 mm (see `MODEMS` in `concepts.py`, which also lists the options left out and why). `out/modems.png` compares them to scale and `out/sizes.md` lists every concept's size with each. The sizes in the table above are with the dongle as bought.

Each `.step` is an assembly with every part a separate named component (movable in Fusion 360: *File → Open → the .step*). Each `.png` shows a front and side view.

## Part sizes and sources

| Part | Envelope (mm) | Source |
| :--- | :--- | :--- |
| Inkplate 4 TEMPERA (board + glass panel) | 74 × 78 × 13.4 | [Soldered hardware repo](https://github.com/SolderedElectronics/Soldered-Inkplate-4-TEMPERA-with-glass-panel-hardware-design), `OUTPUTS/V1.2.0/Main board/…3D.step`; Soldered's own case: `CAD/V1.2.0/Source 3D files/` (STEP and `.f3z` for Fusion) |
| Radxa Rock 3A v1.31 | 85 × 56 board; −4.1 to +10.9 over most of it, +17.5 over the 21 mm USB/Ethernet end | [dl.radxa.com/rock3/docs/hw/3a](https://dl.radxa.com/rock3/docs/hw/3a/) (`radxa_rock_3a_v1310_3d.zip`, DXF outlines) |
| ZitaoTech BBQ10 keyboard V2 (in its shell) | 76.7 × 54.1 × 13.2 | [ZitaoTech repo](https://github.com/ZitaoTech/BBQ10-USB_BLE_Keyboard_V2): `Mechanical drawing/`, `3D-modell/` (STLs of its case) |
| Waveshare SIM7600G-H dongle | 89.2 × 45.3 × 14.6 (with the USB plug) | [Waveshare product page](https://www.waveshare.com/sim7600g-h-4g-dongle.htm) |
| Modem alternatives | see `MODEMS` in `concepts.py` | [4G Module](https://www.waveshare.com/sim7600g-h-4g-module.htm) 38.78 × 42.0; [4G HAT (B)](https://www.waveshare.com/sim7600g-h-4g-hat-b.htm) 65 × 32; thickness of both not published. The SIM7600G-H chip itself is 30 × 30 × 2.9 (SIMCom hardware design) |
| Battery | 85 × 55 × 10 | **PLACEHOLDER**: 1S LiPo, ~4000–5000 mAh |
| Charger + 5 V boost board | 50 × 25 × 8 | **PLACEHOLDER** |

The downloaded models go in `models/` (gitignored; the Rock 3A STEP alone is 131 MB). Fetch them again from the links above.

## Running

```
cd hardware/case
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python build123d ocp_vscode matplotlib
.venv/bin/python concepts.py
```

To view the parts in 3D while editing, use the *OCP CAD Viewer* VS Code extension.

## Open questions

* **Battery and power board**: not chosen. The Rock 3A wants 5 V at up to ~2–3 A with the modem.
* **The dongle is the bulkiest part.** The Waveshare 4G Module (38.8 × 42) is the smallest ready-made replacement found; the dongle's own board (shell off) has no published size. Either way it needs an internal (flex) LTE antenna instead of the external one.
* **Rock 3A ports**: the concepts leave room for the USB/Ethernet end but have no cut-outs yet (USB-C power, microSD, the dongle's USB).
