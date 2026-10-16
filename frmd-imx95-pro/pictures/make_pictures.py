#!/usr/bin/env python3
"""Pictures of the FRDM-IMX95-PRO for the JTAG work, from the design files.

Inputs (rendered before): schematic pages at 300 dpi (hi-sch-N-NN.png), the
layout sheets at 1000 dpi (hi-dnp-1/2.png), the Gerber copper at 1200 dpi
(g-top/bot-1200.png), the label positions of the layout sheets (dnp-bbox.html).
"""
import glob
import re
import sys

from PIL import Image, ImageDraw, ImageFont

Image.MAX_IMAGE_PIXELS = None
SP = sys.argv[1]
OUT = sys.argv[2]
FB = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
RED, GRN, BLU, ORG, CYN = (220, 0, 0), (0, 150, 0), (0, 90, 220), (230, 120, 0), (0, 200, 220)


def font(size):
    return ImageFont.truetype(FB, size)


def sch(page):
    return Image.open(glob.glob(f"{SP}/hi-sch-{page}-*.png")[0]).convert("RGB")


S = 300 / 110  # the crops below were read off 110-dpi views


def crop110(im, box):
    return im.crop(tuple(int(v * S) for v in box))


def caption(im, text, size=30, color=(0, 0, 0), minw=1100):
    """Add a caption band above the picture, wrapped to its width (at least minw)."""
    f = font(size)
    w = max(im.width, minw)
    lines = []
    for para in text.split("\n"):
        line = ""
        for word in para.split(" "):
            t = (line + " " + word).strip()
            if f.getlength(t) > w - 32 and line:
                lines.append(line)
                line = word
            else:
                line = t
        lines.append(line)
    h = (size + 10) * len(lines) + 20
    out = Image.new("RGB", (w, im.height + h), "white")
    out.paste(im, (0, h))
    d = ImageDraw.Draw(out)
    for i, line in enumerate(lines):
        d.text((16, 10 + i * (size + 10)), line, fill=color, font=f)
    return out


def stack(images, gap=20):
    w = max(i.width for i in images)
    h = sum(i.height for i in images) + gap * (len(images) - 1)
    out = Image.new("RGB", (w, h), "white")
    y = 0
    for i in images:
        out.paste(i, (0, y))
        y += i.height + gap
    return out


def side(images, gap=30):
    h = max(i.height for i in images)
    w = sum(i.width for i in images) + gap * (len(images) - 1)
    out = Image.new("RGB", (w, h), "white")
    x = 0
    for i in images:
        out.paste(i, (x, 0))
        x += i.width + gap
    return out


def save(im, name):
    im.save(f"{OUT}/{name}", optimize=True)
    print(name, im.size)


# --- label positions on the layout sheets (points), from pdftotext -bbox ------------
html = open(f"{SP}/dnp-bbox.html").read()
LAB = {}
for i, p in enumerate(html.split("<page ")[1:], 1):
    for m in re.finditer(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]+)</word>', p):
        x0, y0, x1, y1 = map(float, m.groups()[:4])
        LAB.setdefault((i, m.group(5)), ((x0 + x1) / 2, (y0 + y1) / 2))

# ======================= schematic =======================
save(caption(crop110(sch(2), (160, 170, 960, 705)),
             "Block diagram (schematic p2). Debug: one USB-C (J22) to a CH9114F, four UARTs.\n"
             "M.2 Key-E (J8) = the Wi-Fi/BT card; its UART is the one sharing the JTAG pads."),
     "01-block-diagram.png")

im = sch(3)
d = ImageDraw.Draw(im)
d.rectangle(tuple(int(v * S) for v in (803, 141, 1243, 156)), outline=RED, width=8)
save(caption(crop110(im, (0, 0, 1250, 800)),
             "Power tree (p3). The red row puts NVCC_CCM_DAP (the JTAG pads' supply) on 1.8 V.\n"
             "The netlist says otherwise: balls AC23/AD24 are on VDD_3V3, as are VTREF, the pull-ups\n"
             "and the level shifter's SoC side. Treat the pads as 3.3 V; this table is wrong.",
             color=RED),
     "02-power-tree-dap-voltage.png")

save(caption(crop110(sch(7), (868, 148, 1140, 322)),
             "JTAG/SWD at the SoC (p7). The DAP pads go two ways:\n"
             "to the test points through R125/R104/R135/R132 (0R, DNP),\n"
             "to the M.2 BT UART through R420/R411/R434/R428 (49.9R, fitted).\n"
             "D3 (DNP) would give TP3 a path to SYS_RST_B; R128 (DNP) pulls TCK down.", size=30),
     "03-jtag-swd-at-the-soc.png")

im = sch(26)
save(caption(side([crop110(im, (92, 598, 338, 752)), crop110(im, (688, 330, 1212, 682))]),
             "Test points and buttons (p26). TP side: TMS and TDI pulled up 10k to 3V3, TDO's pull-up\n"
             "(R19) not fitted, VTREF = VDD_3V3 through 100R. K1 pulls SYS_RST_B low: a reset you can\n"
             "also wire to. Hold K2, then press K1: boot from the TF card."),
     "04-jtag-testpoints-and-buttons.png")

im = sch(21)
save(caption(stack([crop110(im, (640, 143, 1172, 247)), crop110(im, (380, 505, 705, 605))]),
             "The BT UART path (p21). U57 (LSF0204, always enabled) shifts 3.3 V to 1.8 V for the M.2 card.\n"
             "R103/R124/R131/R134 pull every JTAG line up 4.7k to 3V3 even with no card: harmless.\n"
             "With a card fitted, its TX drives TDI and its RTS drives TCK: take the card out.", size=30),
     "05-bt-uart-level-shifter.png")

save(caption(crop110(sch(9), (0, 0, 600, 412)),
             "Boot mode (p9). SW4 sets BOOT_MODE[3:0]: x001 USB serial download, x010 eMMC,\n"
             "x011 SD, x100 FlexSPI NOR. The mode pins double as UART/CAN TX lines.", size=30),
     "06-boot-mode.png")

save(caption(crop110(sch(27), (0, 20, 850, 525)),
             "Debug USB (p27). J22 -> CH9114F: UART0 = A55 console, UART1 = M33 (System Manager),\n"
             "UART2 = M7. Every DTR/RTS pin of the CH9114F is unconnected (netlist), so a DTR pulse\n"
             "can't reset this board. No on-board SWD/JTAG probe.", size=30),
     "07-debug-usb-uarts.png")

# ======================= layout sheets (assembly views) =======================
K = 1000 / 72


def sheet(page):
    return Image.open(f"{SP}/hi-dnp-{page}.png").convert("RGB")


def mark(d, page, ref, color, text=None, r=34, f=None, dx=40, dy=-14):
    x, y = LAB[(page, ref)]
    x, y = x * K, y * K
    d.rectangle((x - r * 1.6, y - r, x + r * 1.6, y + r), outline=color, width=5)
    if text:
        d.text((x + r * 1.6 + 6 + dx - 40, y + dy), text, fill=color, font=f)


# top, the JTAG corner
im = sheet(2)
d = ImageDraw.Draw(im)
f = font(30)
for ref in ("TP1", "TP2", "TP3", "TP4", "TP5", "TP6", "TP7"):
    x, y = LAB[(2, ref)]
    d.rectangle((x * K - 30, y * K - 30, x * K + 30, y * K + 22), outline=GRN, width=4)
for ref, txt in (("R104", "fit: TMS"), ("R125", "fit: TCK"), ("R132", "fit: TDO"), ("R135", "fit: TDI")):
    x, y = LAB[(2, ref)]
    d.rectangle((x * K - 40, y * K - 20, x * K + 40, y * K + 18), outline=RED, width=4)
    d.text((x * K - 330, y * K - 16), txt, fill=RED, font=f)
for ref in ("R128", "D3"):
    x, y = LAB[(2, ref)]
    d.rectangle((x * K - 30, y * K - 45, x * K + 30, y * K + 45), outline=ORG, width=5)
c = im.crop((int(118 * K), int(228 * K), int(200 * K), int(305 * K)))
save(caption(c, "Top, the JTAG corner (layout sheet, top side). Red parts are not fitted.\n"
                "Green, the test points (0.89 mm pads, 1.52 mm apart): TP1 TMS, TP2 TDO, TP3 RST; "
                "TP4 TCK, TP5 TDI, TP6 VTREF (3V3), TP7 GND.\n"
                "Red boxes: fit these four 0402 links (0R, or the 49.9R from the bottom, picture 09).\n"
                "Orange, optional: D3 (NSR0320) gives TP3 a reset path; R128 pulls TCK down.", size=26),
     "08-top-jtag-corner.png")

# bottom, the BT resistors (the sheet is drawn as seen from below)
im = sheet(1)
d = ImageDraw.Draw(im)
for ref, txt in (("R411", "TMS"), ("R420", "TCK"), ("R428", "TDO"), ("R434", "TDI")):
    x, y = LAB[(1, ref)]
    d.rectangle((x * K - 40, y * K - 20, x * K + 40, y * K + 18), outline=BLU, width=4)
    d.text((x * K - 150, y * K - 16), txt, fill=BLU, font=font(28))
c = im.crop((int(170 * K), int(270 * K), int(240 * K), int(310 * K)))
save(caption(c, "Bottom, under the same spot (layout sheet, seen from below). "
                "R411/R420/R428/R434 (49.9R) join the JTAG nets to the BT level shifter U57. "
                "They sit exactly under R104/R125/R132/R135.\n"
                "Move each one to the top: R411 -> R104 (TMS), R420 -> R125 (TCK), "
                "R428 -> R132 (TDO), R434 -> R135 (TDI). That fits JTAG and cuts BT off at once.", size=24),
     "09-bottom-bt-resistors.png")

# overviews


def overview(page, items, title, name):
    im = sheet(page)
    d = ImageDraw.Draw(im)
    f = font(70)
    for ref, txt, col, dx, dy in items:
        if (page, ref) not in LAB:
            print("no label", ref)
            continue
        x, y = LAB[(page, ref)]
        x, y = x * K, y * K
        d.ellipse((x - 110, y - 110, x + 110, y + 110), outline=col, width=14)
        d.text((x + 120 + dx, y - 40 + dy), txt, fill=col, font=f)
    im = im.crop((int(15 * K), int(195 * K), int(345 * K), int(485 * K)))
    im = im.resize((im.width // 3, im.height // 3), Image.LANCZOS)
    save(caption(im, title, size=30), name)


overview(2, [("TP1", "JTAG test points", GRN, 0, -60), ("R104", "JTAG links (DNP)", RED, 0, 0),
             ("K1", "K1 reset", BLU, -330, 150), ("K2", "K2 boot", BLU, 0, 60),
             ("SW4", "SW4 boot mode", BLU, 0, -40), ("SW3", "SW3 on/off", BLU, 0, 0),
             ("J22", "J22 debug USB", ORG, -320, 160), ("J7", "J7 USB3", ORG, 0, 100),
             ("J11", "J11 power in", ORG, -300, 150), ("J1", "J1 TF card", ORG, 0, 0),
             ("U25", "U25 i.MX95", RED, 0, 0), ("J16", "J16 DSI/CSI", ORG, -900, 0),
             ("J18", "J18 CSI", ORG, -700, 0), ("J21", "J21 HDMI", ORG, -150, -230)],
         "Top side (layout sheet): where things are.", "10-top-overview.png")
overview(1, [("J8", "J8 M.2 Key-E (Wi-Fi/BT card)", RED, -1300, -230), ("R411", "BT resistors + U57", BLU, 0, 0),
             ("U67", "U67 CH9114F", ORG, -800, 0), ("TP146", "TP145-148 debug UART", ORG, 0, 60)],
         "Bottom side (layout sheet, seen from below).", "11-bottom-overview.png")

# ======================= copper (Gerbers) =======================
D = 1200


def P(x, y):
    return ((x + 0.2054) * D, (6.1317 - y) * D)


IPC = {
    "TP1": (1.67, 3.065, "TP1 TMS"), "TP2": (1.73, 3.065, "TP2 TDO"), "TP3": (1.86, 3.065, "TP3 RST"),
    "TP4": (1.67, 3.005, "TP4 TCK"), "TP5": (1.73, 3.005, "TP5 TDI"), "TP6": (1.80, 3.005, "TP6 3V3"),
    "TP7": (1.86, 3.005, "TP7 GND"),
}
LINKS = {"R104": (2.41, "TMS"), "R125": (2.375, "TCK"), "R132": (2.34, "TDO"), "R135": (2.305, "TDI")}

im = Image.open(f"{SP}/g-top-1200.png").convert("RGB")
l, t = P(1.36, 3.13)
r, b = P(2.08, 2.25)
c = im.crop((int(l), int(t), int(r), int(b)))
d = ImageDraw.Draw(c)
for x, y, txt in IPC.values():
    px, py = P(x, y)
    px, py = px - l, py - t
    d.ellipse((px - 26, py - 26, px + 26, py + 26), outline=CYN, width=4)
    d.text((px - 30, py - 62 if y > 3.03 else py + 30), txt.split()[1], fill=CYN, font=font(20))
for ref, (y, sig) in LINKS.items():
    for x in (1.53, 1.57):
        px, py = P(x, y)
        d.rectangle((px - l - 16, py - t - 16, px - l + 16, py - t + 16), outline=RED, width=4)
    px, py = P(1.47, y)
    d.text((px - l - 100, py - t - 12), f"{ref} {sig}", fill=RED, font=font(20))
for x, y, txt in ((1.90, 2.945, "D3 C"), (2.00, 2.945, "D3 A")):
    px, py = P(x, y)
    d.rectangle((px - l - 22, py - t - 30, px - l + 22, py - t + 30), outline=ORG, width=4)
    d.text((px - l - 26, py - t + 34), txt, fill=ORG, font=font(18))
save(caption(c, "Top copper and silkscreen (Gerber L1 + silk). Cyan: the test points, labelled\n"
                "with their signal. Red: the 0402 link pads, SoC side on the right (x 1.57 in),\n"
                "test-point side on the left. Orange: D3's pads.", size=24),
     "12-top-copper-jtag.png")

im = Image.open(f"{SP}/g-bot-1200.png").convert("RGB")
l, t = P(1.25, 2.55)
r, b = P(1.72, 2.20)
c = im.crop((int(l), int(t), int(r), int(b))).transpose(Image.FLIP_LEFT_RIGHT)
d = ImageDraw.Draw(c)
W = c.width
for ref, (y, sig) in (("R411", (2.41, "TMS")), ("R420", (2.375, "TCK")),
                      ("R428", (2.34, "TDO")), ("R434", (2.305, "TDI"))):
    for x in (1.53, 1.57):
        px, py = P(x, y)
        d.rectangle((W - (px - l) - 16, py - t - 16, W - (px - l) + 16, py - t + 16), outline=BLU, width=4)
    px, py = P(1.50, y)
    d.text((W - (px - l) + 26, py - t - 12), f"{ref} {sig}", fill=BLU, font=font(20))
px, py = P(1.395, 2.375)
d.text((W - (px - l) - 30, py - t - 90), "U57", fill=BLU, font=font(26))
save(caption(c, "Bottom copper, seen from below (Gerber L10 + silk, mirrored).\n"
                "Blue: the BT resistors' pads, right under the top links.", size=24),
     "13-bottom-copper-bt.png")
