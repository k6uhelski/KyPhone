"""KyPhone enclosure concepts: rough layouts of the real parts, not printable cases.

Each concept is exported as one STEP assembly (every part a separate, named
component, so it can be moved in Fusion) and a PNG sketch (front and side).

Axes: X = width, Y = height (up the phone), Z = thickness (+Z is the face the
user looks at). Units: mm.

Part sizes come from the makers' own files (see README.md); parts marked
PLACEHOLDER are not chosen yet.

Run:  .venv/bin/python concepts.py      -> out/<concept>.step, out/<concept>.png
"""
from pathlib import Path

from build123d import (Box, Color, Compound, ExportSVG, Pos,
                       fillet, export_step, Axis)

OUT = Path(__file__).parent / "out"

WALL = 2.0     # printed wall thickness
GAP = 1.0      # clearance around every part
RADIUS = 6.0   # rounding of the outer corners


# ---------------------------------------------------------------- the parts
# Each part is a box envelope, centred on X/Y, sitting on Z = 0 (front face up).

def inkplate():
    """Inkplate 4 TEMPERA board with its glass panel (Soldered STEP, V1.2.0): 73.7 x 78.0 x 13.4."""
    return Box(74, 78, 13.4)


def keyboard():
    """ZitaoTech BBQ10 V2 keyboard in its own shell (maker's drawing): 76.7 x 54.1 x 13.2."""
    return Box(76.7, 54.1, 13.2)


def rock3a():
    """Radxa Rock 3A v1.31 (Radxa STEP): PCB 85 x 56, parts under it to -4.1,
    parts over it to 9.7, and the USB/Ethernet end (x 9.9..31.2 in Radxa's
    frame, 21 mm of the board) up to 17.5 mm. The port end is +X here."""
    board = span(-42.5, 42.5, -28, 28, -4.1, 10.9)      # PCB, parts under and over it
    ports = span(21.2, 44.5, -27, 27, -4.1, 17.5)       # USB x2 + Ethernet, 2 mm past the edge
    return board + ports


def span(x0, x1, y0, y1, z0, z1):
    """A box from corner to corner."""
    return Pos((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2) * Box(x1 - x0, y1 - y0, z1 - z0)


# The modem: every option is the same SIM7600G-H chip (same AT commands, so
# modem.py works unchanged); only the board around it differs.
# name -> (width, height, thickness, where the numbers come from)
# Only makers' published sizes. The boards' thickness is not published: the
# SIMCom chip is 30 x 30 x 2.9 (SIMCom hardware design) on a board of about
# 1.6, so 4.5 is the floor; 8 leaves room for the SIM slot and USB socket
# underneath. Measure it when one arrives.
UNPUBLISHED_T = 8
MODEMS = {
    "dongle": (89.2, 45.3, 14.6,
               "Waveshare SIM7600G-H 4G DONGLE, as bought: 89.2 x 45.3 x 14.6 with the USB plug "
               "(maker's drawing)"),
    "module": (38.8, 42.0, UNPUBLISHED_T,
               "Waveshare SIM7600G-H 4G Module: 38.78 x 42.0 (maker's drawing), USB-C, nano-SIM "
               "slot, IPEX antenna sockets (buy the IPEX version: the SMA one has a connector "
               "sticking out); thickness not published"),
    "hat_b": (65.0, 32.0, UNPUBLISHED_T,
              "Waveshare SIM7600G-H 4G HAT (B): 65 x 32 (maker's drawing), micro-USB, nano-SIM "
              "slot, IPEX antenna sockets; thickness not published"),
}
# Looked at and left out:
# * mini PCIe card (SIMCom SIM7600G-H-PCIE with SIM holder): 50.8 x 31 x 5.35
#   (SIMCom drawing), but it needs a mini-PCIe-to-USB adapter and a 3.3 V supply,
#   and no adapter we found publishes its size.
# * M.2 card (42 x 31.4 x 3.8) on Waveshare's USB TO M.2 B KEY adapter:
#   75 x 30.1 x 20.8 with its heatsink (maker's drawing) - bigger than the dongle.
# * The dongle with its shell off: no published size; measure it.


def modem(name):
    w, h, t, _ = MODEMS[name]
    return Box(w, h, t)


def battery():
    """PLACEHOLDER: a 1S LiPo around 4000-5000 mAh (e.g. 10 x 55 x 85)."""
    return Box(85, 55, 10)


def power_board():
    """PLACEHOLDER: charger + 5 V / 3 A boost board for the Rock 3A (about 50 x 25 x 8)."""
    return Box(50, 25, 8)


COLOURS = {
    "inkplate": Color(0.85, 0.85, 0.80),
    "keyboard": Color(0.15, 0.15, 0.15),
    "rock3a": Color(0.10, 0.45, 0.20),
    "modem": Color(0.05, 0.05, 0.05),
    "battery": Color(0.20, 0.35, 0.80),
    "power_board": Color(0.80, 0.30, 0.10),
    "shell": Color(0.95, 0.60, 0.20, 0.35),
}


def place(shape, x, y, z_top, rot=0):
    """Put a part with its centre at (x, y) and its front face at z_top."""
    bb = shape.bounding_box()
    shape = shape.rotate(Axis.Z, rot) if rot else shape
    return Pos(x, y, z_top - bb.max.Z) * shape


def place_modem(name, x, y_top, z_top):
    """Put the modem with its top edge (not its centre) at y_top, so every
    option sits against the same neighbour."""
    return place(modem(name), x, y_top - MODEMS[name][1] / 2, z_top)


# ---------------------------------------------------------------- the shell

def shell(parts, windows):
    """A hollow rounded box around the parts, with openings in the front face.

    windows: list of (x, y, w, h) openings through the front wall.
    """
    bb = Compound(children=list(parts.values())).bounding_box()
    w = bb.size.X + 2 * (WALL + GAP)
    h = bb.size.Y + 2 * (WALL + GAP)
    t = bb.size.Z + 2 * (WALL + GAP)
    c = bb.center()
    outer = Pos(c.X, c.Y, c.Z) * Box(w, h, t)
    outer = fillet(outer.edges().filter_by(Axis.Z), RADIUS)
    inner = Pos(c.X, c.Y, c.Z) * Box(w - 2 * WALL, h - 2 * WALL, t - 2 * WALL)
    body = outer - inner
    front = c.Z + t / 2
    for x, y, ww, hh in windows:
        body -= Pos(x, y, front - WALL / 2) * Box(ww, hh, WALL * 3)
    return body, (w, h, t)


# ---------------------------------------------------------------- concepts
# Front face of the phone is z = 0; everything sits behind it (z < 0).

SCREEN = (68, 68)          # the panel's 600 x 600 active area, about 68 mm square


def brick(m):
    """1. BRICK: one body, two layers. Screen over keyboard on the front;
    battery + modem behind the screen, the Rock 3A behind the keyboard."""
    parts = {
        "inkplate": place(inkplate(), 0, 30, 0),
        "keyboard": place(keyboard(), 0, -39, 0),
        "battery": place(battery(), 0, 30, -15),
        "modem": place_modem(m, 0, 42.7, -26),
        "rock3a": place(rock3a(), 0, -39, -15),
        "power_board": place(power_board(), 0, 56, -26),
    }
    windows = [(0, 30, *SCREEN), (0, -39, 74, 51)]
    return parts, windows


def remote(m):
    """2. REMOTE: one tall, thinner body. Screen, keyboard, then a 'chin' for
    the Rock 3A lying flat; modem behind the screen, battery behind the keyboard."""
    parts = {
        "inkplate": place(inkplate(), 0, 60, 0),
        "keyboard": place(keyboard(), 0, -8, 0),
        "modem": place_modem(m, 0, 72.7, -15),
        "battery": place(battery(), 0, -8, -14.5),
        "rock3a": place(rock3a(), 0, -72, -1),
        "power_board": place(power_board(), 0, 87, -15),
    }
    windows = [(0, 60, *SCREEN), (0, -8, 74, 51)]
    return parts, windows


def corded(m):
    """3. CORDED: a thin handset (screen + keyboard, each on its own battery,
    as they already are) on a coiled cord to a pocket 'base' holding the
    Rock 3A, the modem and the main battery. The cord carries the 4 SPI
    wires, ground and 5 V."""
    handset = {
        "inkplate": place(inkplate(), 0, 30, 0),
        "keyboard": place(keyboard(), 0, -39, 0),
    }
    base = {
        "rock3a": place(rock3a(), 0, 0, 0),
        "modem": place_modem(m, 0, -29.4, 0),
        "battery": place(battery(), 0, -56.9, -(MODEMS[m][2] + 1)),
        "power_board": place(power_board(), 0, 0, -22.6),
    }
    return (handset, [(0, 30, *SCREEN), (0, -39, 74, 51)]), (base, [])


# ---------------------------------------------------------------- export

def assemble(name, parts, windows, offset=(0, 0, 0)):
    body, size = shell(parts, windows)
    children = []
    for label, shape in list(parts.items()) + [("shell", body)]:
        shape = Pos(*offset) * shape
        shape.label = f"{name}_{label}"
        shape.color = COLOURS[label]
        children.append(shape)
    return children, size


def sketch(path, title, groups):
    """Front view (what the user sees) and side view, parts as coloured boxes.

    groups: list of (children, (w, h, t)) - one per body.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch, Rectangle

    fig, axes = plt.subplots(1, 2 * len(groups), figsize=(5 * len(groups) + 1, 7),
                             gridspec_kw={"width_ratios": [3, 1.6] * len(groups)})
    for g, (children, (w, h, t)) in enumerate(groups):
        front, side = axes[2 * g], axes[2 * g + 1]
        parts = [c for c in children if not c.label.endswith("_shell")]
        shell_bb = [c for c in children if c.label.endswith("_shell")][0].bounding_box()
        name = shell_bb and children[0].label.rsplit("_", 1)[0]
        # draw back to front, so the front parts sit on top in the front view
        for c in sorted(parts, key=lambda c: c.bounding_box().max.Z):
            bb = c.bounding_box()
            colour = tuple(c.color)[:3]
            label = c.label.split("_", 2)[-1].replace("corded_handset_", "").replace("corded_base_", "")
            for ax, (x0, x1) in ((front, (bb.min.X, bb.max.X)), (side, (bb.min.Z, bb.max.Z))):
                ax.add_patch(Rectangle((x0, bb.min.Y), x1 - x0, bb.size.Y, facecolor=colour,
                                       alpha=0.55, edgecolor="black", lw=0.6,
                                       label=label if ax is front else None))
        for ax, (x0, x1) in ((front, (shell_bb.min.X, shell_bb.max.X)), (side, (shell_bb.min.Z, shell_bb.max.Z))):
            ax.add_patch(FancyBboxPatch((x0, shell_bb.min.Y), x1 - x0, shell_bb.size.Y,
                                        boxstyle=f"round,pad=0,rounding_size={RADIUS if ax is front else 1}",
                                        fill=False, edgecolor="darkorange", lw=2))
            ax.set_xlim(x0 - 8, x1 + 8)
            ax.set_ylim(shell_bb.min.Y - 8, shell_bb.max.Y + 8)
            ax.set_aspect("equal")
            ax.grid(alpha=0.2)
        front.legend(loc="upper center", bbox_to_anchor=(0.5, -0.06), ncol=3, fontsize=7, frameon=False)
        front.set_title(f"{name}\nfront: {w:.0f} x {h:.0f} mm", fontsize=9)
        side.set_title(f"side\n{t:.0f} mm thick", fontsize=9)
        side.set_xlabel("back  <-  z  ->  front", fontsize=7)
    fig.suptitle(title, fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def modem_chart(path):
    """The modem options side by side, to scale (front and edge)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    fig, ax = plt.subplots(figsize=(8, 3.6))
    x = 0
    for name, (w, h, t, _) in MODEMS.items():
        ax.add_patch(Rectangle((x, 0), w, h, facecolor="0.25", alpha=0.8))
        ax.add_patch(Rectangle((x, -t - 8), w, t, facecolor="0.55"))
        thick = f"{t:g}" if t != UNPUBLISHED_T else "?"
        ax.text(x + w / 2, max(h for _, h, _, _ in MODEMS.values()) + 3,
                f"{name}\n{w:g} x {h:g} x {thick}", ha="center", fontsize=8)
        x += w + 12
    ax.text(-4, h / 2, "top", ha="right", fontsize=8)
    ax.text(-4, -12, "edge", ha="right", fontsize=8)
    ax.set_xlim(-20, x)
    ax.set_ylim(-35, 65)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title("Modem options, to scale (mm) - all the same SIM7600G-H chip\n"
                 f"? = thickness not published (drawn as {UNPUBLISHED_T})", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def build(m):
    """Every concept with modem option m, into out/<m>/. Returns the size rows."""
    out = OUT / m
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, fn in (("1_brick", brick), ("2_remote", remote)):
        parts, windows = fn(m)
        children, size = assemble(name, parts, windows)
        export_step(Compound(children=children, label=name), out / f"{name}.step")
        title = fn.__doc__.split(":")[0].split(". ", 1)[1]
        sketch(out / f"{name}.png", f"{title} - modem: {m}", [(children, size)])
        rows.append((name, size))

    (handset, hw), (base, bw) = corded(m)
    hc, hsize = assemble("3_corded_handset", handset, hw)
    bc, bsize = assemble("3_corded_base", base, bw, offset=(130, 0, 0))
    export_step(Compound(children=hc + bc, label="3_corded"), out / "3_corded.step")
    sketch(out / "3_corded.png", f"CORDED - modem: {m}", [(hc, hsize), (bc, bsize)])
    rows += [("3_corded handset", hsize), ("3_corded base", bsize)]
    return rows


def main():
    OUT.mkdir(exist_ok=True)
    modem_chart(OUT / "modems.png")
    lines = ["# Concept sizes by modem option (mm, w x h x t)", "",
             "Generated by `concepts.py`; do not edit.", "",
             "| Concept | " + " | ".join(MODEMS) + " |",
             "| :--- |" + " :--- |" * len(MODEMS)]
    table = {}
    for m in MODEMS:
        for name, size in build(m):
            table.setdefault(name, []).append(" x ".join(f"{v:.0f}" for v in size))
    for name, sizes in table.items():
        lines.append(f"| {name} | " + " | ".join(sizes) + " |")
    lines += ["", f"Thickness is not published for `module` and `hat_b`; modelled as {UNPUBLISHED_T} "
              "(the chip and board alone are 4.5).", "", "Modem options:", ""] + [f"* `{m}` - {d[3]}" for m, d in MODEMS.items()]
    (OUT / "sizes.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
