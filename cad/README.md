# cad/: Artemis 3D mock-up

`artemis_mockup.py` builds the Arty body in Blender from plain numbers (millimetres) and renders it. The results and what they mean are in `docs/3d-mockups.md`.

The script uses Blender as a Python module, so no Blender window or install is needed.

```
python3 -m venv .venv
.venv/bin/pip install -r cad/requirements.txt
.venv/bin/python cad/artemis_mockup.py            # every render, plus cad/artemis_mockup.blend
.venv/bin/python cad/artemis_mockup.py exploded   # one view: hero, views, exploded, coded, section, plate, bought
.venv/bin/python cad/artemis_mockup.py report     # only the fit and balance report (a few seconds)
```

`bpy` is a large download (about 400 MB) and needs Python 3.13 for the version used here (Blender 5.2). Renders go to `docs/img/3d/`. They use the CPU and take a minute or two each; set `ARTEMIS_SAMPLES=12` for quick drafts.

`artemis_styles.py` adds shell styles on top (retro, mint, cassette) and renders them:

```
.venv/bin/python cad/artemis_styles.py            # every style, three views each, plus the comparison sheet
.venv/bin/python cad/artemis_styles.py mint       # one style
.venv/bin/python cad/artemis_styles.py report     # fit and weight numbers per style
```

To change the design, edit the numbers at the top of the file (body size, wheel, head, axle position) or the part definitions in `build()`. Every part is registered as printed (`P..`) or bought (`B..`), which is what feeds the colours, the weights and the labels.

To open the scene in Blender, use `cad/artemis_mockup.blend` (it is written by the "hero" render).
