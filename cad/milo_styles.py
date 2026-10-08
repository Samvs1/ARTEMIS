"""Shell styles for the Milo mock-up.

Every style uses the same internals and the same outer envelope as the classic body
(160 x 135 x 190 mm), so any of them could be printed over the same hardware. A style
changes the shell curvature, the colours, the trim and control parts, and the look of the
face. Run (same environment as milo_mockup.py):

    python cad/milo_styles.py                 # every style, three views each, plus the comparison sheet
    python cad/milo_styles.py retro           # one style
    python cad/milo_styles.py report          # fit and weight report for every style (fast)
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import milo_mockup as mm  # noqa: E402  (imports bpy first)
from milo_mockup import PARTS, box, boolean, cyl, join, mat, reg, v  # noqa: E402
import bmesh  # noqa: E402
import bpy  # noqa: E402
from mathutils import Vector  # noqa: E402

OUT_DIR = os.path.join(mm.IMG_DIR, "styles")


# ---------------------------------------------------------------- helpers
def pob(pid):
    return next(p["ob"] for p in PARTS if p["id"] == pid)


def prism(name, pts, y0, y1, bevel=0.5):
    """Flat shape from (x, z) points, extruded from y0 to y1."""
    bm = bmesh.new()
    a = [bm.verts.new(v(x, y0, z)) for x, z in pts]
    b = [bm.verts.new(v(x, y1, z)) for x, z in pts]
    n = len(pts)
    bm.faces.new(a)
    bm.faces.new(b[::-1])
    for i in range(n):
        bm.faces.new((a[i], a[(i + 1) % n], b[(i + 1) % n], b[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    ob = mm._obj(name, bm)
    if bevel:
        m = ob.modifiers.new("b", "BEVEL")
        m.width = bevel * mm.S
        m.segments = 2
        mm.apply_mods(ob)
    return ob


def rrect(name, w, h, cx, cz, y, depth, rot_deg=0.0, bevel=2.0):
    """Rounded rectangle in the x-z plane, rotated, as a thin slab."""
    ob = box(name, w, depth, h, 0, 0, 0, bevel, 3)
    a = math.radians(rot_deg)
    for vt in ob.data.vertices:
        x, z = vt.co.x, vt.co.z
        vt.co.x = x * math.cos(a) - z * math.sin(a) + cx * mm.S
        vt.co.z = x * math.sin(a) + z * math.cos(a) + cz * mm.S
        vt.co.y += y * mm.S
    return ob


def inlay(shell_id, pid, label, matname, sx, sz, cx, cz, face="front", depth=1.2, bevel=0.5, note=""):
    """Cut a pocket into a shell face and drop a flush coloured part into it."""
    if face == "front":
        cy = mm.Y0_BODY + depth / 2
    elif face == "bezel":
        cy = mm.HEAD_Y0 + depth / 2
    else:
        cy = mm.Y1_BODY - depth / 2
    boolean(pob(shell_id), box("pk", sx, depth, sz, cx, cy, cz))
    ob = box(pid, sx - 0.3, depth, sz - 0.3, cx, cy, cz, bevel, 2)
    reg(ob, pid, label, "printed", matname, grp="trim", note=note or "Printed in a second colour and glued flush into the shell.")
    return ob


def side_vents(levels=(62, 68, 74, 80)):
    """Cooling slots through both sides of the upper shell, behind the wheels (also helps the Pi cooler)."""
    cutters = [box("v", 10, 34, 3, sx * (mm.W_BODY / 2 - 1.5), 40, z, 1.2, 3) for sx in (-1, 1) for z in levels]
    boolean(pob("P02"), join("vents", cutters))
    PARTS[next(i for i, p in enumerate(PARTS) if p["id"] == "P02")]["note"] += " Side cooling slots (8) for the Pi cooler."


def retint(pid, matname):
    next(p for p in PARTS if p["id"] == pid)["mat"] = matname


def curve_smile(name, pts, y, matobj, width=0.9):
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    sp = cu.splines.new("BEZIER")
    sp.bezier_points.add(len(pts) - 1)
    for bp, (px, pz) in zip(sp.bezier_points, pts):
        bp.co = v(px, y, pz)
        bp.handle_left_type = bp.handle_right_type = "AUTO"
    cu.bevel_depth = width * mm.S
    cu.materials.append(matobj)
    ob = bpy.data.objects.new(name, cu)
    bpy.context.scene.collection.objects.link(ob)
    return ob


FACE_Y = mm.HEAD_Y0 + 3.2


def emit(name, color, strength):
    return mat(name, color, 0.3, emit=color, emit_strength=strength)


def painted(ob, material):
    ob.data.materials.append(material)
    return ob


# ---------------------------------------------------------------- the styles
class Style:
    name = "classic"
    title = "Classic"
    blurb = "The rounded cream shell shown so far."
    r_body, r_head, y0_body = 24.0, 12.0, -65.0
    shell = (0.90, 0.87, 0.80, 1)
    extras = {}          # extra material name -> colour
    exposure = 0.0

    def apply(self):
        mm.R_BODY, mm.R_HEAD, mm.Y0_BODY = self.r_body, self.r_head, self.y0_body
        mm.STYLE = self

    def dark_sensor(self):
        retint("B10", "speaker")  # the front distance sensor shows through its window: keep it dark

    def decorate(self):
        pass

    def materials(self, M, mode):
        if mode == "coded":
            for k in self.extras:
                M[k] = M["printed"]
            return
        M["printed"] = mat("printed", self.shell, 0.38)
        for k, col in self.extras.items():
            M[k] = mat(k, col, 0.4)

    def face(self):
        return mm.add_face()


class Retro(Style):
    name = "retro"
    title = "Retro computer"
    blurb = "Beige 1980s home computer: square corners, a thick screen lip, stripe, two side dials, green pixel face."
    r_body, r_head = 10.0, 8.0
    shell = (0.80, 0.75, 0.62, 1)
    extras = {"brown": (0.28, 0.16, 0.09, 1), "orange": (0.93, 0.48, 0.10, 1), "red": (0.78, 0.17, 0.10, 1)}

    exposure = -0.5

    def decorate(self):
        self.dark_sensor()
        side_vents()
        inlay("P02", "P10", "Stripe, orange", "orange", 98, 3.6, 0, 74)
        inlay("P02", "P11", "Stripe, red", "red", 98, 3.6, 0, 69.2)
        inlay("P02", "P12", "Stripe, brown", "brown", 98, 3.6, 0, 64.4)
        lip = box("lip", 104, 4, 68, 0, mm.HEAD_Y0 - 2, mm.SCREEN_CZ + 1, 3, 3)
        boolean(lip, box("w", 90, 12, 54, 0, mm.HEAD_Y0 - 2, mm.SCREEN_CZ + 1, 3, 3))
        reg(lip, "P13", "Screen lip", "printed", "brown", grp="head",
            note="Thick raised frame around the screen window; glued to the bezel.")
        k1 = cyl("kn", 16, 5, -(mm.HEAD_W / 2 + 2.0), -22, 150, "x", 1.0, 32)
        reg(k1, "P14", "Side dial", "printed", "brown", grp="head", qty=2, note="Decorative dial, glued on. Two pieces.")
        k2 = cyl("kn2", 16, 5, (mm.HEAD_W / 2 + 2.0), -22, 150, "x", 1.0, 32)
        PARTS.append(dict(ob=k2, id="P14b", label="Side dial", kind="printed", mat="brown", grp="head", note="", qty=0,
                          mass=PARTS[-1]["mass"], size=None))
        k2.name = "P14b"
        mm.smooth(k2)
        retint("P04", "brown")
        retint("P07", "brown")
        retint("P07b", "brown")

    def face(self):
        glow = emit("pixel", (0.25, 1.0, 0.45, 1), 7)
        pitch = 4.6
        pix = []
        def add(cx, cz):
            pix.append(box("px", pitch - 0.9, 0.6, pitch - 0.9, cx, FACE_Y, cz, 0.2, 1))
        for sx in (-1, 1):
            for i in (-1, 0, 1):
                for j in (-1, 0, 1):
                    add(sx * 20 + i * pitch, mm.SCREEN_CZ + 6 + j * pitch)
        for i, j in ((-2, 1), (-1, 0), (0, 0), (1, 0), (2, 1)):
            add(i * pitch, mm.SCREEN_CZ - 12 + j * pitch)
        o = join("pixelface", pix)
        o.data.materials.append(glow)
        return [o]


class Mint(Style):
    name = "mint"
    title = "Mint console (BMO-inspired)"
    blurb = "Teal-mint handheld-console friend: pale screen with black dot eyes, four coloured buttons and a slot on the front."
    r_body, r_head, y0_body = 14.0, 12.0, -63.5
    shell = (0.28, 0.76, 0.60, 1)
    exposure = -0.6
    extras = {"teal": (0.10, 0.40, 0.38, 1), "yellow": (0.98, 0.80, 0.12, 1), "blue": (0.20, 0.52, 0.92, 1),
              "red": (0.88, 0.17, 0.20, 1), "green": (0.30, 0.78, 0.30, 1)}

    def decorate(self):
        self.dark_sensor()
        side_vents()
        y_front = mm.Y0_BODY - 1.5               # buttons stand 1.5 mm proud, so depth stays 135 mm
        yc = mm.Y0_BODY - 0.25
        # D-pad (yellow cross)
        cross = join("cross", [box("c1", 20, 2.5, 6.5, -28, yc, 59, 1.0, 2), box("c2", 6.5, 2.5, 20, -28, yc, 59, 1.0, 2)])
        reg(cross, "P10", "Control: D-pad", "printed", "yellow", grp="trim", note="Yellow, printed flat, glued on the front panel.")
        tri = prism("tri", [(12 - 7.5, 58), (12 + 7.5, 58), (12, 71)], y_front, y_front + 2.5, 0.6)
        reg(tri, "P11", "Control: triangle button", "printed", "blue", grp="trim", note="Blue.")
        red = cyl("red", 9.5, 2.5, 33, yc, 52, "y", 0.8, 32)
        reg(red, "P12", "Control: round button", "printed", "red", grp="trim", note="Red.")
        pill = box("pill", 13, 2.5, 4.6, 2, yc, 51, 2.0, 3)
        reg(pill, "P13", "Control: pill button", "printed", "green", grp="trim", note="Green.")
        lip = box("lip", 98, 2, 62, 0, mm.HEAD_Y0 - 1, mm.SCREEN_CZ + 1, 2.5, 3)
        boolean(lip, box("w", 90, 12, 54, 0, mm.HEAD_Y0 - 1, mm.SCREEN_CZ + 1, 3, 3))
        reg(lip, "P14", "Screen rim", "printed", "teal", grp="head", note="Darker thin frame around the screen; glued to the bezel.")
        retint("P04", "teal")
        retint("P07", "teal")
        retint("P07b", "teal")

    def face(self):
        screen = emit("minty", (0.66, 0.92, 0.80, 1), 2.2)
        plate = box("plate", 90, 0.6, 54, 0, FACE_Y + 0.0, mm.SCREEN_CZ + 1, 3, 3)
        plate.data.materials.append(screen)
        ink = mat("ink", (0.01, 0.02, 0.02, 1), 0.6)
        objs = [plate]
        for sx in (-1, 1):
            e = cyl("eye", 6.5, 0.6, sx * 21, FACE_Y - 0.35, mm.SCREEN_CZ + 7, "y", seg=24)
            e.data.materials.append(ink)
            objs.append(e)
        objs.append(curve_smile("mouth", [(-8, mm.SCREEN_CZ - 5), (0, mm.SCREEN_CZ - 11), (8, mm.SCREEN_CZ - 5)], FACE_Y - 0.35, ink, 1.0))
        return objs


class Cassette(Style):
    name = "cassette"
    title = "Cassette (dark and orange)"
    blurb = "Charcoal body with orange accents, grip ribs on the head and a confident half-lidded cyan face."
    r_body, r_head = 8.0, 8.0
    shell = (0.045, 0.05, 0.056, 1)
    exposure = -1.0
    extras = {"orange": (0.96, 0.40, 0.06, 1)}

    def decorate(self):
        self.dark_sensor()
        side_vents()
        inlay("P02", "P10", "Accent band, wide", "orange", 100, 5.0, 0, 62)
        inlay("P02", "P11", "Accent band, thin", "orange", 100, 2.0, 0, 70)
        inlay("P09", "P12", "Chin band", "orange", 92, 5.0, 0, 108.5, face="bezel", depth=1.4)
        ribs = [box("r", 3.0, 2.2, 46, sx * (mm.HEAD_W / 2 + 0.5), y, 146, 0.6, 2)
                for sx in (-1, 1) for y in (-34, -27, -20, -13, -6, 1)]
        boolean(pob("P08"), join("ribs", ribs))
        PARTS[next(i for i, p in enumerate(PARTS) if p["id"] == "P08")]["note"] += " Six grip grooves on each side."
        retint("P07", "orange")
        retint("P07b", "orange")
        retint("P04", "orange")

    def face(self):
        glow = emit("cyan", (0.30, 0.92, 1.0, 1), 8)
        objs = []
        for sx, rot in ((-1, -12), (1, 12)):
            e = rrect("eye", 20, 8.5, sx * 20, mm.SCREEN_CZ + 5, FACE_Y, 0.6, rot, 2.6)
            e.data.materials.append(glow)
            objs.append(e)
        objs.append(curve_smile("smirk", [(-7, mm.SCREEN_CZ - 11), (1, mm.SCREEN_CZ - 13), (9, mm.SCREEN_CZ - 8)], FACE_Y, glow, 0.9))
        return objs


STYLES = {s.name: s for s in (Style(), Retro(), Mint(), Cassette())}


# ---------------------------------------------------------------- views
def render_style(name, views=("3q", "front", "back"), samples=None):
    style = STYLES[name]
    os.makedirs(OUT_DIR, exist_ok=True)
    done = []
    for view in views:
        style.apply()
        sc, _ = mm.setup_scene("real", samples=samples or int(os.environ.get("MILO_SAMPLES", 48)))
        mm.floor_and_lights()
        sc.view_settings.exposure = style.exposure
        if view == "3q":
            mm.camera((520, -680, 400), (0, 0, 95), 85)
            sc.render.resolution_x, sc.render.resolution_y = 900, 900
        elif view == "front":
            mm.camera((0, -1250, 150), (0, 0, 98), 140)
            sc.render.resolution_x, sc.render.resolution_y = 800, 900
        else:  # rear three-quarter: vents, grille and USB-C
            mm.camera((-430, 650, 330), (0, 5, 90), 85)
            sc.render.resolution_x, sc.render.resolution_y = 900, 900
        out = os.path.join(OUT_DIR, f"{name}-{view}.png")
        mm.render(out)
        done.append(out)
        print("wrote", out)
    return done


def compare_sheet(names=None):
    from PIL import Image, ImageDraw, ImageFont
    names = names or list(STYLES)
    col_w, row_h = 480, 480
    views = ("3q", "front", "back")
    labels = {"3q": "Front, three-quarter", "front": "Front", "back": "Back, three-quarter"}
    head, left = 70, 0
    sheet = Image.new("RGB", (col_w * len(names), head + row_h * len(views)), (250, 250, 248))
    dr = ImageDraw.Draw(sheet)
    try:
        ft, fs = ImageFont.truetype("DejaVuSans-Bold.ttf", 30), ImageFont.truetype("DejaVuSans.ttf", 19)
    except Exception:
        ft, fs = ImageFont.load_default(size=30), ImageFont.load_default(size=19)
    for ci, n in enumerate(names):
        dr.text((ci * col_w + 18, 12), STYLES[n].title.split(" (")[0], fill=(30, 30, 30), font=ft)
        for ri, vname in enumerate(views):
            im = Image.open(os.path.join(OUT_DIR, f"{n}-{vname}.png")).convert("RGB")
            im.thumbnail((col_w, row_h))
            sheet.paste(im, (ci * col_w + (col_w - im.width) // 2, head + ri * row_h + (row_h - im.height) // 2))
            if ci == 0:
                dr.text((12, head + ri * row_h + 8), labels[vname], fill=(70, 70, 70), font=fs)
    out = os.path.join(mm.IMG_DIR, "milo-styles.png")
    sheet.save(out)
    print("wrote", out)


def report_all():
    for n, style in STYLES.items():
        style.apply()
        mm.setup_scene("real", samples=1)
        r = mm.fit_report()
        print(f"\n== {style.title} ==")
        print(f"size {r['size'][0]:.0f} x {r['size'][1]:.0f} x {r['size'][2]:.0f} mm, total {r['total_g']:.0f} g, "
              f"printed {r['printed_g']:.0f} g, centre of mass y {r['com'][1]:+.1f} z {r['com'][2]:.0f}")
        worst = min(r["clearances"], key=lambda t: t[1])
        print(f"tightest fit: {worst[0]} = {worst[1]:.1f} mm")
        for name, val in r["clearances"]:
            if "fillet" in name or "Pi cooler" in name:
                print(f"  {val:6.1f}  {name}")
        for p in PARTS:
            if p["kind"] == "printed" and p["qty"] > 0 and (p["id"] >= "P10" or p["id"] in ("P02", "P08", "P09")):
                bb = [p["ob"].matrix_world @ Vector(c) for c in p["ob"].bound_box]
                sz = Vector(map(max, zip(*bb))) - Vector(map(min, zip(*bb)))
                print(f"  {p['id']} {p['label']:<26} x{p['qty']}  {p['mass']:5.1f} g  {sz.x/mm.S:4.0f} x {sz.y/mm.S:4.0f} x {sz.z/mm.S:4.0f}")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "all"
    if arg == "report":
        report_all()
    elif arg == "sheet":
        compare_sheet()
    elif arg == "all":
        for n in STYLES:
            render_style(n)
        compare_sheet()
    else:
        render_style(arg)
