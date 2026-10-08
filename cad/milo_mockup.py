"""Milo 3D mock-up, built in Blender (bpy) from plain numbers.

Run (needs the `bpy` and `pillow` Python packages, see cad/README.md):

    python cad/milo_mockup.py            # all renders into docs/img/3d/ + cad/milo_mockup.blend
    python cad/milo_mockup.py hero       # one view: hero, views, exploded, coded, section, plate, bought
    python cad/milo_mockup.py report     # only print the fit and balance report

All sizes are millimetres. x = left/right, y = front (negative) to back (positive), z = up.
Every part is tagged PRINTED (P..) or BOUGHT (B..). This is a design mock-up, not
production CAD: bought-part sizes are approximate (marked "assumed" in docs/3d-mockups.md)
and must be checked against the real part before anything is ordered or printed.
"""
import json
import math
import os
import sys

import bpy  # must come first: it registers bmesh and mathutils
import bmesh
from mathutils import Euler, Matrix, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
IMG_DIR = os.path.join(ROOT, "docs", "img", "3d")
S = 0.001  # mm -> Blender metres

# ---------------------------------------------------------------- design numbers
W_BODY, Y0_BODY, Y1_BODY = 122.0, -65.0, 70.0     # body width, front and back faces
Z0_BODY, Z1_BODY, Z_SPLIT = 8.0, 95.0, 52.0       # belly clearance, top, shell seam
WALL = 3.0
R_BODY = 24.0
WHEEL_D, WHEEL_W, WHEEL_GAP = 65.0, 18.0, 1.0
AXLE_Y = -8.0                                     # wheel axle, in front of the centre of mass
STACK_Y = 2.0                                     # centre of the Pi and battery board
AXLE_Z = WHEEL_D / 2
NECK_Y, NECK_OD, NECK_ID = -10.0, 56.0, 44.0
HEAD_W, HEAD_Y0, HEAD_Y1, HEAD_Z0, HEAD_Z1 = 132.0, -60.0, 30.0, 103.0, 190.0
BEZEL_T = 8.0                                     # thickness of the face bezel slab
HEAD_WALL = 2.4
R_HEAD = 12.0
SCREEN_CZ = 145.0
MIC_D = 70.0                                      # assumed XVF3800 board diameter
MIC_T = 6.5                                       # assumed thickness with parts

PRINTED_COLOR = (0.90, 0.87, 0.80, 1)
CODED_PRINTED = (0.85, 0.30, 0.02, 1)
CODED_BOUGHT = (0.05, 0.22, 0.75, 1)
DENSITY_PRINTED = 1.25 * 0.9                      # g/cm3, PETG/PLA with a little infill
PARTS = []                                        # filled by build()
STYLE = None                                      # optional shell style, see cad/milo_styles.py


# ---------------------------------------------------------------- mesh helpers
def v(x, y, z):
    return Vector((x * S, y * S, z * S))


def _obj(name, bm):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def apply_mods(ob):
    deps = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(ob.evaluated_get(deps))
    old = ob.data
    ob.modifiers.clear()
    ob.data = me
    bpy.data.meshes.remove(old)
    return ob


def box(name, sx, sy, sz, cx, cy, cz, bevel=0.0, seg=5):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for vt in bm.verts:
        vt.co = Vector((vt.co.x * sx * S + cx * S, vt.co.y * sy * S + cy * S, vt.co.z * sz * S + cz * S))
    ob = _obj(name, bm)
    if bevel > 0:
        m = ob.modifiers.new("b", "BEVEL")
        m.width = min(bevel, min(sx, sy, sz) / 2.05) * S
        m.segments = seg
        m.limit_method = "NONE"
        apply_mods(ob)
    return ob


def cyl(name, d, h, cx, cy, cz, axis="z", bevel=0.0, seg=48, d2=None):
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=seg,
                          radius1=d / 2 * S, radius2=(d2 if d2 else d) / 2 * S, depth=h * S)
    rot = {"z": Matrix.Identity(4), "x": Matrix.Rotation(math.pi / 2, 4, "Y"),
           "y": Matrix.Rotation(math.pi / 2, 4, "X")}[axis]
    bmesh.ops.transform(bm, matrix=rot, verts=bm.verts)
    bmesh.ops.translate(bm, vec=v(cx, cy, cz), verts=bm.verts)
    ob = _obj(name, bm)
    if bevel > 0:
        m = ob.modifiers.new("b", "BEVEL")
        m.width = bevel * S
        m.segments = 3
        apply_mods(ob)
    return ob


def boolean(ob, other, op="DIFFERENCE", keep=False):
    m = ob.modifiers.new("bool", "BOOLEAN")
    m.operation = op
    m.object = other
    m.solver = "EXACT"
    apply_mods(ob)
    if not keep:
        bpy.data.objects.remove(other, do_unlink=True)
    return ob


def duplicate(ob, name):
    c = ob.copy()
    c.data = ob.data.copy()
    c.name = name
    bpy.context.scene.collection.objects.link(c)
    return c


def join(name, objs):
    bm = bmesh.new()
    for o in objs:
        bm.from_mesh(o.data)
        bpy.data.objects.remove(o, do_unlink=True)
    return _obj(name, bm)


def volume_mm3(ob):
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    bm.transform(ob.matrix_world)
    vol = abs(bm.calc_volume()) / (S ** 3)
    bm.free()
    return vol


def refresh_masses():
    """Recompute printed weights after cuts and added parts."""
    for p in PARTS:
        if p["kind"] == "printed":
            p["mass"] = volume_mm3(p["ob"]) / 1000.0 * DENSITY_PRINTED


def smooth(ob):
    """Smooth the small curved faces; keep big flat faces flat so they shade cleanly."""
    for p in ob.data.polygons:
        p.use_smooth = p.area < 400 * S * S


def mirror_x(ob, name):
    c = duplicate(ob, name)
    for vt in c.data.vertices:
        vt.co.x = -vt.co.x
    c.data.flip_normals()
    return c


# ---------------------------------------------------------------- part registry
def reg(ob, pid, label, kind, mat, mass=None, grp="", note="", qty=1, size=None):
    ob.name = pid
    smooth(ob)
    d = dict(ob=ob, id=pid, label=label, kind=kind, mat=mat, grp=grp, note=note, qty=qty, size=size)
    if kind == "printed":
        d["mass"] = volume_mm3(ob) / 1000.0 * DENSITY_PRINTED
    else:
        d["mass"] = mass
    PARTS.append(d)
    return d


# ---------------------------------------------------------------- the model
def build():
    PARTS.clear()
    yc_body = (Y0_BODY + Y1_BODY) / 2
    L = W_BODY / 2

    # ---- body: one hollow rounded shell, split into lower and upper halves at the seam
    outer = box("outer", W_BODY, Y1_BODY - Y0_BODY, Z1_BODY - Z0_BODY, 0, yc_body, (Z0_BODY + Z1_BODY) / 2, R_BODY, 8)
    cav = box("cav", W_BODY - 2 * WALL, Y1_BODY - Y0_BODY - 2 * WALL, Z1_BODY - Z0_BODY - 2 * WALL,
              0, yc_body, (Z0_BODY + Z1_BODY) / 2, R_BODY - WALL, 8)
    shell = boolean(outer, cav)
    lower = duplicate(shell, "lower")
    upper = duplicate(shell, "upper")
    bpy.data.objects.remove(shell, do_unlink=True)
    boolean(lower, box("h", 400, 400, 200, 0, 0, Z_SPLIT - 100), "INTERSECT")
    boolean(upper, box("h", 400, 400, 200, 0, 0, Z_SPLIT + 100), "INTERSECT")

    # lower shell openings: axle holes, cliff windows, front distance window, USB-C panel jack
    for sx in (-1, 1):
        boolean(lower, cyl("ax", 16, 20, sx * L, AXLE_Y, AXLE_Z, "x"))
        boolean(lower, cyl("cliff", 9, 10, sx * 30, -36, Z0_BODY + 1))
    boolean(lower, box("tof", 15, 8, 7, 0, Y0_BODY + 1, 40))
    boolean(lower, box("usbc", 10, 8, 4, 0, Y1_BODY - 1, 36, 1.2))
    reg(lower, "P01", "Lower body shell", "printed", "printed", grp="body",
        note="Holds the motors, cliff sensors, front distance sensor, the skid and the USB-C jack. Print floor down.")

    # upper shell openings: neck hole, speaker grille (ring pattern as in the concept), rear vent slots
    boolean(upper, cyl("neck", 34, 20, 0, NECK_Y, Z1_BODY))
    holes = [cyl("g0", 3.2, 12, 0, Y1_BODY, 58, "y", seg=12)]
    for ring, (rad, n) in enumerate(((5, 6), (10, 12), (15, 18))):
        for k in range(n):
            a = 2 * math.pi * k / n + ring * 0.3
            holes.append(cyl(f"g{ring}{k}", 3.2, 12, rad * math.cos(a), Y1_BODY, 58 + rad * math.sin(a), "y", seg=12))
    boolean(upper, join("grille", holes))
    reg(upper, "P02", "Upper body shell", "printed", "printed", grp="body",
        note="Roof, neck seat and the speaker grille. Open side down; needs supports under the roof.")

    # ---- skid, neck collar, hub caps, motor clamps, battery cradle
    skid = box("skid", 30, 26, Z0_BODY + 1.5, 0, 56, (Z0_BODY + 1.5) / 2, 6, 4)
    reg(skid, "P03", "Rear skid", "printed", "printed", grp="body",
        note="Low-friction runner under the tail, two screws into the lower shell. Print the flat screw face down.")

    collar = cyl("collar", NECK_OD, 8.5, 0, NECK_Y, Z1_BODY + 4.25)
    boolean(collar, cyl("c", NECK_ID, 20, 0, NECK_Y, Z1_BODY))
    reg(collar, "P04", "Neck collar", "printed", "printed", grp="neck",
        note="Joins head and body, passes the display and power cables. Parked neck tilt would replace this.")

    cradle = box("cr", 50, 78, 24, 0, 8, 11 + 12, 3, 3)
    boolean(cradle, box("cc", 46, 74, 30, 0, 8, 11 + 15 + 2))
    posts = [box("p", 6, 6, 45, sx * 29, STACK_Y + sy * 24.5, 11 + 22.5, 1.5, 2) for sx in (-1, 1) for sy in (-1, 1)]
    bar = box("bar", 64, 6, 22, 0, 8, 11 + 11)  # bridge so the posts and tray print as one piece
    cradle = join("cradle", [cradle] + posts + [bar])
    reg(cradle, "P05", "Battery cradle and Pi posts", "printed", "printed", grp="stack",
        note="Tray for the four cells plus four posts that carry the battery board and the Pi.")

    clamp = box("clamp", 18, 20, 19, L - 9, AXLE_Y, AXLE_Z, 3, 3)
    boolean(clamp, cyl("m", 10.4, 40, L - 9, AXLE_Y, AXLE_Z, "x"))
    boolean(clamp, box("m2", 12.2, 12.2, 40, L - 9, AXLE_Y, AXLE_Z))
    clampL = clamp
    reg(clampL, "P06", "Motor clamp", "printed", "printed", grp="drive", qty=2,
        note="Holds one motor against the wall. Two of the same part (mirrored).")
    clampR = mirror_x(clampL, "P06b")
    clampR.parent = None
    PARTS.append(dict(ob=clampR, id="P06b", label="Motor clamp (mirror)", kind="printed", mat="printed",
                      grp="drive", note="", qty=0, mass=PARTS[-1]["mass"], size=None))

    hubs = []
    for sx in (-1, 1):
        hc = cyl("hub", 40, 3, sx * (L + WHEEL_GAP + WHEEL_W - 1.5), AXLE_Y, AXLE_Z, "x", 1.0)
        hubs.append(hc)
    reg(hubs[0], "P07", "Hub cap", "printed", "printed", grp="drive", qty=2, note="Clip-on cover over the wheel screw. Two pieces.")
    PARTS.append(dict(ob=hubs[1], id="P07b", label="Hub cap", kind="printed", mat="printed", grp="drive",
                      note="", qty=0, mass=PARTS[-1]["mass"], size=None))
    hubs[1].name = "P07b"
    smooth(hubs[1])

    # ---- head
    hh = HEAD_Z1 - HEAD_Z0
    hc_y = (HEAD_Y0 + HEAD_Y1) / 2
    houter = box("ho", HEAD_W, HEAD_Y1 - HEAD_Y0, hh, 0, hc_y, (HEAD_Z0 + HEAD_Z1) / 2, R_HEAD, 8)
    hcav = box("hc", HEAD_W - 2 * HEAD_WALL, HEAD_Y1 - HEAD_Y0 - 2 * HEAD_WALL, hh - 2 * HEAD_WALL,
               0, hc_y, (HEAD_Z0 + HEAD_Z1) / 2, R_HEAD - HEAD_WALL, 8)
    hshell = duplicate(houter, "hshell")
    boolean(hshell, hcav)
    seam_y = HEAD_Y0 + BEZEL_T
    # rear shell = hollow head, front slab removed
    mic_cy = (seam_y + HEAD_Y1 - HEAD_WALL) / 2
    hrear = hshell
    boolean(hrear, box("cut", 400, 200, 400, 0, seam_y + 100, 150), "INTERSECT")
    boolean(hrear, cyl("neckhole", 30, 10, 0, NECK_Y, HEAD_Z0 + 1))
    for mx in (-12, 12):  # microphone openings through the roof, over the mic array's four mics
        for my in (-12, 12):
            boolean(hrear, cyl("mh", 3.0, 8, mx, mic_cy + my, HEAD_Z1 - 1, seg=16))
    boolean(hrear, box("sw", 12, 6, 6, -34, HEAD_Y1 - 1, 140, 1.0))
    reg(hrear, "P08", "Head shell", "printed", "printed", grp="head",
        note="Rear half of the head: holds the mic array under the roof and has the mic-kill switch slot. Print back wall down.")

    bez = houter
    boolean(bez, box("cut", 400, 200, 400, 0, seam_y - 100, 150), "INTERSECT")
    boolean(bez, box("win", 90, 12, 54, 0, HEAD_Y0 + 2, SCREEN_CZ + 1, 3, 3))
    boolean(bez, box("pocket", 103.5, 6, 63.5, 0, seam_y - 1.5, SCREEN_CZ + 1))
    reg(bez, "P09", "Face bezel", "printed", "printed", grp="head",
        note="Front plate with the screen window and a pocket that clamps the display. Print face down.")

    # ---- bought parts -------------------------------------------------
    # Raspberry Pi 5 with Active Cooler
    pi_cx, pi_cy, pi_z = 0, STACK_Y, 63
    pi = box("pi", 85, 56, 1.6, pi_cx, pi_cy, pi_z + 0.8, 2, 2)
    cool = box("cool", 40, 40, 11, pi_cx + 4, pi_cy, pi_z + 1.6 + 5.5, 1.5, 2)
    ports = box("ports", 22, 56 * 0.7, 13, pi_cx - 30, pi_cy + 4, pi_z + 1.6 + 6.5, 1)
    pi = join("pi", [pi, cool, ports])
    reg(pi, "B01", "Raspberry Pi 5 (4 GB) with Active Cooler", "bought", "pcb", 50, "stack", size="85 x 56 x 17")

    ups = box("ups", 85, 56, 1.8, 0, STACK_Y, 56.9, 2, 2)
    reg(ups, "B02", "Battery board (UPS HAT E type)", "bought", "pcb2", 40, "stack",
        note="Layout of cells assumed 2 x 2.", size="85 x 56 x 2 (assumed)")

    cells = [cyl("cell", 21.4, 70, cx, 8, 11 + 10.7 + cz * 21.6, "y", 0.8) for cx in (-10.8, 10.8) for cz in (0, 1)]
    cells = join("cells", cells)
    reg(cells, "B03", "21700 cells, 4 pieces", "bought", "cell", 280, "stack", qty=4,
        note="About 70 g each. Named brand only, safety review before ordering.", size="21.4 x 70 each")

    # display module
    disp = box("disp", 101, 5, 61, 0, seam_y - 3 + 0.0 + 2.5 - 1.5, SCREEN_CZ + 1, 0.8, 2)
    reg(disp, "B04", "Waveshare 4 inch DSI touch display", "bought", "display", 45, "head",
        note="Portrait panel turned to landscape. Outline assumed.", size="101 x 61 x 5 (assumed)")

    # microphone array
    mic = cyl("mic", MIC_D, MIC_T, 0, mic_cy, HEAD_Z1 - HEAD_WALL - 1.5 - MIC_T / 2, "z", 0.8, 64)
    reg(mic, "B05", "reSpeaker XVF3800 mic array (USB)", "bought", "pcb", 20, "head",
        note="Diameter assumed 70 mm: it sets the head depth.", size="70 dia x 6.5 (assumed)")

    # speaker
    spk = cyl("spk", 40, 16, 0, 55, 58, "y", 1.5)
    reg(spk, "B06", "Speaker, 40 mm", "bought", "speaker", 15, "body", note="Wired to the mic array's jack.", size="40 dia x 16 (assumed)")

    # motors and wheels
    def motor(sx):
        x_in = sx * (L - WALL)
        parts = [box("g", 9, 12, 10, x_in - sx * 4.5, AXLE_Y, AXLE_Z, 0.8, 2),
                 box("m", 20, 12, 10, x_in - sx * 19, AXLE_Y, AXLE_Z, 2, 3),
                 box("e", 4, 12, 10, x_in - sx * 31, AXLE_Y, AXLE_Z, 0.3, 2),
                 cyl("sh", 3, 20, sx * (L + 6), AXLE_Y, AXLE_Z, "x", seg=16)]
        return join("mot", parts)
    ml = motor(-1)
    reg(ml, "B07", "N20 gearmotor with encoder", "bought", "metal", 12, "drive", qty=2, size="12 x 10 x 33 (assumed)")
    mr = motor(1)
    PARTS.append(dict(ob=mr, id="B07b", label="N20 gearmotor with encoder", kind="bought", mat="metal", grp="drive",
                      note="", qty=0, mass=12, size=None))
    mr.name = "B07b"
    smooth(mr)

    def wheel(sx, name):
        xc = sx * (L + WHEEL_GAP + WHEEL_W / 2)
        tire = cyl(name, WHEEL_D, WHEEL_W, xc, AXLE_Y, AXLE_Z, "x", 3.0, 64)
        boolean(tire, cyl("th", 42, WHEEL_W + 2, xc, AXLE_Y, AXLE_Z, "x", seg=48))  # recess for the hub cap
        hub = cyl("h", 44, WHEEL_W - 3.0, xc - sx * 1.5, AXLE_Y, AXLE_Z, "x", 1.0, 48)  # leaves room for the hub cap
        return tire, hub
    wl, hl = wheel(-1, "wl")
    reg(wl, "B08", "Wheel, 65 mm rubber tyre", "bought", "rubber", 20, "drive", qty=2, size="65 dia x 18")
    reg(hl, "B08h", "Wheel hub", "bought", "hub", 0, "drive", qty=0)
    wr, hr = wheel(1, "wr")
    PARTS.append(dict(ob=wr, id="B08b", label="Wheel", kind="bought", mat="rubber", grp="drive", note="", qty=0, mass=20, size=None))
    wr.name = "B08b"
    smooth(wr)
    PARTS.append(dict(ob=hr, id="B08bh", label="Wheel hub", kind="bought", mat="hub", grp="drive", note="", qty=0, mass=0, size=None))
    hr.name = "B08bh"
    smooth(hr)

    # controller board with motor driver (one assembly) and sensors
    pico = box("pico", 51, 6, 21, 0, Y0_BODY + 12, 27, 0.5, 2)
    drv = box("drv", 25, 4, 18, 0, Y0_BODY + 8, 45)
    pico = join("pico", [pico, drv])
    reg(pico, "B09", "Pico 2 controller, motor driver and motion sensor", "bought", "pcb2", 15, "body",
        note="Stands in front of the battery. The motion sensor sits on the same board.", size="51 x 21 x 8 (assumed)")
    tof = [box("tf", 13, 3, 18, 0, Y0_BODY + 2.5, 40, 0.3, 2)]
    for sx in (-1, 1):
        tof.append(box("tc", 13, 18, 3, sx * 30, -36, Z0_BODY + 3, 0.3, 2))
    tof = join("tof", tof)
    reg(tof, "B10", "Distance sensors, 3 pieces (front and two cliff)", "bought", "pcb2", 5, "body", qty=3,
        note="Time-of-flight modules, one forward, two looking down.", size="13 x 18 x 3 each")

    sw = box("sw", 11, 5, 5.5, -34, HEAD_Y1 - HEAD_WALL - 2.5, 140, 0.5, 2)
    reg(sw, "B11", "Mic-kill slide switch", "bought", "metal", 2, "head", note="Cuts the microphone's power: the privacy promise.")
    usb = box("usb", 9, 8, 3.2, 0, Y1_BODY - 5, 36, 0.6, 2)
    reg(usb, "B12", "USB-C panel jack (charging)", "bought", "metal", 5, "body", note="A panel-mount extension to the battery board.")
    rib = box("rib", 36, 1.2, 46, 0, NECK_Y + 4, (Z1_BODY + 60 + SCREEN_CZ) / 2 - 4, 0.2, 2)
    reg(rib, "B13", "Display ribbon cable (DSI, about 15 cm)", "bought", "ribbon", 5, "head", note="Runs up the neck.", size="15 cm")
    return PARTS


# ---------------------------------------------------------------- balance and fit report
def fillet_room(z_top, r, d):
    """Height of the inside of a rounded roof at distance d from the wall (r = inner fillet radius)."""
    if d >= r:
        return z_top
    return z_top - r + math.sqrt(max(r * r - (r - d) ** 2, 0.0))


def fit_report():
    mass = sum(p["mass"] or 0 for p in PARTS)
    cx = cy = cz = 0.0
    rows = []
    for p in PARTS:
        m = p["mass"] or 0
        bb = [p["ob"].matrix_world @ Vector(c) for c in p["ob"].bound_box]
        c = sum(bb, Vector()) / 8 / S
        cx += c.x * m
        cy += c.y * m
        cz += c.z * m
        rows.append((p["id"], p["label"], m, c))
    cx, cy, cz = cx / mass, cy / mass, cz / mass
    printed = sum(p["mass"] for p in PARTS if p["kind"] == "printed")
    def box_of(pid):
        ob = next(q["ob"] for q in PARTS if q["id"] == pid)
        bb = [ob.matrix_world @ Vector(c) for c in ob.bound_box]
        return Vector(map(min, zip(*bb))) / S, Vector(map(max, zip(*bb))) / S
    inner_roof = Z1_BODY - WALL
    clear = [
        ("Motor inner end to battery cells (x)", box_of("B07b")[0].x - box_of("B03")[1].x),
        ("Battery cells top to battery board (z)", box_of("B02")[0].z - box_of("B03")[1].z),
        ("Pi cooler top to body roof (z)", inner_roof - box_of("B01")[1].z),
        ("Speaker back to rear wall (y)", Y1_BODY - WALL - box_of("B06")[1].y),
        ("Speaker front to Pi back edge (y)", box_of("B06")[0].y - box_of("B01")[1].y),
        ("Mic array to head wall, front side (y)", box_of("B05")[0].y - (HEAD_Y0 + BEZEL_T)),
        ("Mic array to head wall, rear side (y)", HEAD_Y1 - HEAD_WALL - box_of("B05")[1].y),
        ("Mic array edge to head roof fillet (z)", fillet_room(HEAD_Z1 - HEAD_WALL, R_HEAD - HEAD_WALL,
                                                             (HEAD_Y1 - HEAD_WALL) - box_of("B05")[1].y) - box_of("B05")[1].z),
        ("Display top to mic array (z)", box_of("B05")[0].z - box_of("B04")[1].z),
        ("Wheel to body side (x)", abs(box_of("B08b")[0].x) - W_BODY / 2),
    ]
    allbb = [p["ob"].matrix_world @ Vector(c) for p in PARTS for c in p["ob"].bound_box]
    lo = Vector(map(min, zip(*allbb))) / S
    hi = Vector(map(max, zip(*allbb))) / S
    return dict(total_g=mass, printed_g=printed, com=(cx, cy, cz), clearances=clear,
                size=(hi.x - lo.x, hi.y - lo.y, hi.z - lo.z), lo=tuple(lo), hi=tuple(hi))


def print_report():
    r = fit_report()
    print(f"Overall size  W {r['size'][0]:.0f}  D {r['size'][1]:.0f}  H {r['size'][2]:.0f} mm")
    print(f"Mass          total {r['total_g']:.0f} g, printed parts {r['printed_g']:.0f} g")
    print(f"Centre of mass x {r['com'][0]:+.1f}  y {r['com'][1]:+.1f}  z {r['com'][2]:.1f} mm"
          f"   (axle y = {AXLE_Y:+.0f}, skid y = +56)")
    print("Clearances (mm, negative = parts collide):")
    for name, val in r["clearances"]:
        print(f"  {val:6.1f}  {name}")
    for p in PARTS:
        if p["qty"] > 0 and p["kind"] == "printed":
            bb = [p["ob"].matrix_world @ Vector(c) for c in p["ob"].bound_box]
            sz = Vector(map(max, zip(*bb))) - Vector(map(min, zip(*bb)))
            print(f"  {p['id']} {p['label']:<32} {p['mass']:6.1f} g  bbox {sz.x/S:5.0f} x {sz.y/S:5.0f} x {sz.z/S:5.0f}")
    return r


# ---------------------------------------------------------------- materials, scene, render
def mat(name, color, rough=0.5, metal=0.0, emit=None, emit_strength=0.0, alpha=1.0, trans=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = color
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metal
    if emit:
        b.inputs["Emission Color"].default_value = emit
        b.inputs["Emission Strength"].default_value = emit_strength
    if trans:
        b.inputs["Transmission Weight"].default_value = trans
    return m


def materials(mode):
    if mode == "coded":
        printed = mat("printed", CODED_PRINTED, 0.45)
        bought = lambda n: mat(n, CODED_BOUGHT, 0.45)
        M = {k: bought(k) for k in ("pcb", "pcb2", "cell", "display", "speaker", "metal", "rubber", "hub", "ribbon")}
        M["display"] = mat("display", (0.02, 0.02, 0.03, 1), 0.2)
    else:
        printed = mat("printed", PRINTED_COLOR, 0.38)
        M = dict(pcb=mat("pcb", (0.05, 0.35, 0.16, 1), 0.4), pcb2=mat("pcb2", (0.04, 0.25, 0.5, 1), 0.4),
                 cell=mat("cell", (0.1, 0.35, 0.75, 1), 0.3, 0.2), display=mat("display", (0.015, 0.015, 0.02, 1), 0.15),
                 speaker=mat("speaker", (0.05, 0.05, 0.05, 1), 0.5), metal=mat("metal", (0.7, 0.7, 0.72, 1), 0.3, 1.0),
                 rubber=mat("rubber", (0.025, 0.025, 0.025, 1), 0.85), hub=mat("hub", (0.08, 0.08, 0.08, 1), 0.5),
                 ribbon=mat("ribbon", (0.8, 0.45, 0.1, 1), 0.5))
    M["printed"] = printed
    return M


def add_face():
    glow = mat("glow", (0.7, 0.9, 1, 1), 0.3, emit=(0.55, 0.85, 1, 1), emit_strength=9)
    y = HEAD_Y0 + 3.2
    objs = []
    for sx in (-1, 1):
        e = cyl("eye", 17, 0.6, 0, 0, 0, "y", seg=40)
        for vt in e.data.vertices:
            vt.co.z *= 1.25
            vt.co += v(sx * 19, y, SCREEN_CZ + 5)
        e.data.materials.append(glow)
        objs.append(e)
        hl = cyl("hl", 5, 0.7, sx * 19 + 4, y - 0.2, SCREEN_CZ + 11, "y", seg=20)
        hl.data.materials.append(mat("hl", (1, 1, 1, 1), 0.3, emit=(1, 1, 1, 1), emit_strength=20))
        objs.append(hl)
    cu = bpy.data.curves.new("smile", "CURVE")
    cu.dimensions = "3D"
    sp = cu.splines.new("BEZIER")
    sp.bezier_points.add(2)
    pts = [(-9, SCREEN_CZ - 8), (0, SCREEN_CZ - 14), (9, SCREEN_CZ - 8)]
    for bp, (px, pz) in zip(sp.bezier_points, pts):
        bp.co = v(px, y, pz)
        bp.handle_left_type = bp.handle_right_type = "AUTO"
    cu.bevel_depth = 0.9 * S
    cu.materials.append(glow)
    so = bpy.data.objects.new("smile", cu)
    bpy.context.scene.collection.objects.link(so)
    return objs + [so]


def setup_scene(mode, res=(1100, 1100), samples=int(os.environ.get('MILO_SAMPLES', 48))):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.samples = samples
    sc.cycles.use_denoising = True
    sc.cycles.device = "CPU"
    sc.render.resolution_x, sc.render.resolution_y = res
    sc.render.film_transparent = False
    sc.view_settings.view_transform = "AgX"
    sc.view_settings.look = "None"
    w = bpy.data.worlds.new("w")
    sc.world = w
    w.use_nodes = True
    bg = w.node_tree.nodes["Background"]
    bg.inputs[0].default_value = (0.93, 0.94, 0.95, 1)
    bg.inputs[1].default_value = 0.55
    build()
    if STYLE:
        STYLE.decorate()
        refresh_masses()
    M = materials(mode)
    if STYLE:
        STYLE.materials(M, mode)
    for p in PARTS:
        for poly in p["ob"].data.polygons:  # boolean cuts can leave stray slot indices behind
            poly.material_index = 0
        p["ob"].data.materials.clear()  # boolean results arrive with an empty first slot
        p["ob"].data.materials.append(M[p["mat"]])
    face = (STYLE.face() if STYLE else add_face()) if mode != "coded" else []
    return sc, face


def floor_and_lights(z=0.0, dark=False):
    fl = box("floor", 20000, 20000, 2, 0, 0, z - 1)
    fm = mat("floor", (0.30, 0.33, 0.38, 1) if dark else (0.97, 0.97, 0.96, 1), 0.7)
    fl.data.materials.append(fm)
    for name, loc, energy, sz in (("key", (260, -320, 420), 7, 400), ("fill", (-380, -150, 250), 3, 500),
                                  ("rim", (60, 380, 330), 4, 300)):
        ld = bpy.data.lights.new(name, "AREA")
        ld.energy = energy
        ld.size = sz * S * 1000 / 1000 * 1
        lo = bpy.data.objects.new(name, ld)
        lo.location = v(*loc)
        bpy.context.scene.collection.objects.link(lo)
        d = Vector((0, -10 * S, 100 * S)) - lo.location
        lo.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
        ld.size = sz * S
    return fl


def camera(loc, target, lens=70, ortho=None):
    cd = bpy.data.cameras.new("cam")
    cd.lens = lens
    if ortho:
        cd.type = "ORTHO"
        cd.ortho_scale = ortho * S
    cd.clip_end = 20
    co = bpy.data.objects.new("cam", cd)
    co.location = v(*loc)
    d = v(*target) - co.location
    co.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.collection.objects.link(co)
    bpy.context.scene.camera = co
    return co


def explode(vectors, face=()):
    for p in PARTS:
        d = vectors.get(p["id"]) or vectors.get(p["grp"])
        if d:
            p["ob"].location += v(*d)
    for f in face:  # the face drawing travels with the display
        f.location += v(*vectors["B04"])


def section_cut(side="x", at=0.0):
    """Remove everything on one side of a plane by a boolean with a big block (visual only)."""
    for p in PARTS:
        ob = p["ob"]
        if p["kind"] == "bought" and p["id"][:3] in ("B03",):
            pass
        cutter = box("sec", 700, 700, 700, 350 + at, 0, 100)
        m = ob.modifiers.new("sec", "BOOLEAN")
        m.operation = "DIFFERENCE"
        m.object = cutter
        m.solver = "EXACT"
        apply_mods(ob)
        bpy.data.objects.remove(cutter, do_unlink=True)


def render(path):
    bpy.context.scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def label_image(path, items, legend=None, title=None, font_size=26):
    from PIL import Image, ImageDraw, ImageFont
    from bpy_extras.object_utils import world_to_camera_view
    im = Image.open(path).convert("RGB")
    dr = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", font_size)
    except Exception:
        font = ImageFont.load_default(size=font_size)
    sc = bpy.context.scene
    W, H = im.size
    for text, ob, col in items:
        bb = [ob.matrix_world @ Vector(c) for c in ob.bound_box]
        c = sum(bb, Vector()) / 8 + v(*LABEL_SHIFT.get(text, (0, 0, 0)))
        p = world_to_camera_view(sc, sc.camera, c)
        x, y = p.x * W, (1 - p.y) * H
        r = font_size * 0.78
        dr.ellipse((x - r, y - r, x + r, y + r), fill=col, outline=(255, 255, 255), width=3)
        tw = dr.textlength(text, font=font)
        dr.text((x - tw / 2, y - font_size * 0.6), text, fill=(255, 255, 255), font=font)
    if title:
        tf = ImageFont.truetype("DejaVuSans-Bold.ttf", 34) if os.path.exists("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf") else ImageFont.load_default(size=34)
        dr.text((28, 22), title, fill=(30, 30, 30), font=tf)
    im.save(path)


# ---------------------------------------------------------------- views
LABEL_SHIFT = {"P08": (45, 0, 30), "B11": (0, 0, 22), "B13": (0, 0, -28), "B01": (35, 20, 0), "P01": (-40, 0, 12),
               "P05": (0, 0, 14), "P06": (0, 0, 10), "B09": (0, 0, 14), "B10": (0, 0, -10)}
EXPLODE = {
    "P03": (0, 0, -22), "P01": (0, 0, 0), "B07": (-34, 0, 0), "B07b": (34, 0, 0), "B08": (-52, 0, 0), "B08h": (-52, 0, 0),
    "B08b": (52, 0, 0), "B08bh": (52, 0, 0), "P06": (-24, 0, 6), "P06b": (24, 0, 6), "P07": (-70, 0, 0), "P07b": (70, 0, 0),
    "P05": (0, 0, 16), "B03": (0, 0, 34), "B02": (0, 0, 56), "B01": (0, 0, 80), "B06": (0, 60, 40), "B09": (0, -55, 25),
    "B10": (0, -40, 8), "B12": (0, 55, 0), "P02": (0, 0, 118), "P04": (0, 0, 160),
    "P08": (0, 40, 195), "B05": (0, 40, 245), "B11": (0, 70, 195), "B13": (0, 0, 195), "B04": (0, -40, 195), "P09": (0, -80, 195),
}


def run(view):
    os.makedirs(IMG_DIR, exist_ok=True)
    mode = "coded" if view in ("coded", "plate", "bought") else "real"
    sc, face = setup_scene(mode)
    if view == "report":
        print_report()
        return
    floor_and_lights(dark=view in ("plate", "bought"))
    if mode == "coded":
        sc.view_settings.exposure = -1.1
    out = os.path.join(IMG_DIR, f"milo-{view}.png")

    if view == "hero":
        camera((520, -680, 400), (0, 0, 95), 85)
        sc.render.resolution_x, sc.render.resolution_y = 1100, 1100
        render(out)
    elif view == "views":
        shots = {}
        for name, loc in (("front", (0, -900, 95)), ("side", (900, 0, 95)), ("back", (0, 900, 95)), ("top", (0, 0, 900))):
            camera(loc, (0, 0, 95), ortho=250 if name != "top" else 220)
            sc.render.resolution_x, sc.render.resolution_y = 520, 620
            p = os.path.join(IMG_DIR, f"_{name}.png")
            render(p)
            shots[name] = p
        from PIL import Image, ImageDraw, ImageFont
        sheet = Image.new("RGB", (4 * 520, 620 + 54), (250, 250, 248))
        dr = ImageDraw.Draw(sheet)
        try:
            f = ImageFont.truetype("DejaVuSans-Bold.ttf", 26)
        except Exception:
            f = ImageFont.load_default(size=26)
        for i, (name, p) in enumerate(shots.items()):
            sheet.paste(Image.open(p), (i * 520, 54))
            dr.text((i * 520 + 20, 12), name.capitalize() + " view", fill=(30, 30, 30), font=f)
            os.remove(p)
        sheet.save(out)
    elif view == "exploded":
        explode(EXPLODE, face)
        shift = Vector((0, 0, 0))
        camera((480, -820, 520), (0, 10, 185), 70)
        sc.render.resolution_x, sc.render.resolution_y = 1100, 1500
        render(out)
        items = []
        for p in PARTS:
            if p["qty"] > 0 and not p["id"].endswith("h"):
                items.append((p["id"], p["ob"], (200, 90, 10) if p["kind"] == "printed" else (30, 90, 200)))
        label_image(out, items)
    elif view == "coded":
        camera((520, -680, 400), (0, 0, 95), 85)
        sc.render.resolution_x, sc.render.resolution_y = 1100, 1100
        render(out)
    elif view == "section":
        section_cut()
        camera((620, -520, 170), (0, 0, 100), 95)
        sc.render.resolution_x, sc.render.resolution_y = 1300, 1100
        # side camera for a clean cut: look from +x toward -x
        sc.camera.location = v(900, -60, 120)
        d = v(0, -20, 105) - sc.camera.location
        sc.camera.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
        render(out)
    elif view in ("plate", "bought"):
        items, extent = plate_layout(view)
        camera((0, -560, 820), (0, 10, 0), 52)
        sc.render.resolution_x, sc.render.resolution_y = 1400, 1000
        render(out)
        col = (200, 90, 10) if view == "plate" else (30, 90, 200)
        label_image(out, [(i, o, col) for i, o, _ in items])
    if view == "hero":
        bpy.context.preferences.filepaths.save_version = 0  # no .blend1 backup files
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(HERE, "milo_mockup.blend"))
    print("wrote", out)


def plate_layout(view):
    """Lay parts out flat on the floor in print orientation (printed) or as a parts tray (bought).
    Returns the (label, object, colour) items for the badges, and the layout size in mm."""
    want = "printed" if view == "plate" else "bought"
    rot = {"P08": (-90, 0, 0), "P09": (90, 0, 0), "P03": (180, 0, 0),
           "P06": (0, 90, 0), "P06b": (0, 90, 0), "P07": (0, 90, 0), "P07b": (0, 90, 0)}
    hubs = {"B08": "B08h", "B08b": "B08bh"}
    byid = {p["id"]: p for p in PARTS}
    for p in PARTS:
        if p["kind"] != want:
            bpy.data.objects.remove(p["ob"], do_unlink=True)
    for o in list(bpy.data.objects):
        if o.name == "smile" or o.name.startswith(("eye", "hl")):
            bpy.data.objects.remove(o, do_unlink=True)
    objs = [p for p in PARTS if p["kind"] == want and not p["id"].endswith("h")]
    placed = []
    for p in objs:
        ob = p["ob"]
        bb = [Vector(c) for c in ob.bound_box]
        c = sum(bb, Vector()) / 8
        ob.data.transform(Matrix.Translation(-c))
        kids = [byid[hubs[p["id"]]]["ob"]] if p["id"] in hubs else []
        for k in kids:
            k.data.transform(Matrix.Translation(-c))
            k.parent = ob
        ob.location = Vector((0, 0, 0))
        ob.rotation_euler = Euler([math.radians(a) for a in rot.get(p["id"], (0, 0, 0))])
        bpy.context.view_layer.update()
        wb = [ob.matrix_world @ Vector(cc) for cc in ob.bound_box]
        lo = Vector(map(min, zip(*wb)))
        hi = Vector(map(max, zip(*wb)))
        placed.append([p, (hi.x - lo.x) / S, (hi.y - lo.y) / S, lo, hi])
    placed.sort(key=lambda t: -t[2])
    max_w, gap = 560.0, 22.0
    x = y = row_d = 0.0
    pos = []
    for p, w, d, lo, hi in placed:
        if x + w > max_w and x > 0:
            x, y, row_d = 0.0, y - (row_d + gap), 0.0
        pos.append((p, x, y, w, d, lo, hi))
        x += w + gap
        row_d = max(row_d, d)
    total_w = max(px + w for _, px, _, w, _, _, _ in pos)
    total_d = -(y - row_d)
    items = []
    for p, px, py, w, d, lo, hi in pos:
        ob = p["ob"]
        ctr = Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))
        ob.location = v(px + w / 2 - total_w / 2, py - d / 2 + total_d / 2, 0) - ctr
        items.append((p["id"], ob, (255, 255, 255)))
    return items, max(total_w, total_d)


if __name__ == "__main__":
    view = sys.argv[1] if len(sys.argv) > 1 else "all"
    if view == "all":
        for vname in ("hero", "views", "exploded", "coded", "section", "plate", "bought"):
            run(vname)
    else:
        run(view)
