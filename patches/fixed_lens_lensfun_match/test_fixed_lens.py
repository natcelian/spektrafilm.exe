"""Checks for the fixed-lens lens-correction fix, headless.

usage: python test_fixed_lens.py <src dir> <out dir> --fixed <raw> [<raw> ...]
                                 [--other <raw> ...] [--baseline]

`--fixed`: raws from fixed-lens cameras (Leica Q, Q2, Q3). lensfun has ONE
lens for each, named after the camera ("LEICA Q2 & compatibles"), and the
camera writes its lens as "SUMMILUX 1:1.7/28 ASPH." or "28.0 mm f/1.7". 2.2.1
keeps only lenses whose name shares a word with that, so it finds none and
applies no correction. Checked for each:

1. the lens lookup (`_find_lens_candidates`) finds the camera's one lens;
2. the correction it drives changes the picture (a flat grey frame of the
   camera's shape comes out with its corners brightened by the vignetting
   correction);
3. through the real app, with Lens correction switched on, loading the first
   raw reports "applied lens correction" and names the lens.

`--other`: raws from interchangeable-lens cameras. The new rule must never
reach them: their camera lists more than one lens. The lens each gets is
printed, so `--baseline` shows it is the same as in 2.2.1.

`--baseline` runs the same against 2.2.1 and lists what fails there. Exits
non-zero on a failure (not with --baseline).
"""
import os
import sys
import time

args = sys.argv[1:]
BASELINE = '--baseline' in args
args = [a for a in args if a != '--baseline']
if len(args) < 4 or '--fixed' not in args:
    sys.exit(__doc__)
SRC, OUT = args[0], args[1]
rest = args[2:]
FIXED = rest[rest.index('--fixed') + 1:rest.index('--other') if '--other' in rest else None]
OTHER = rest[rest.index('--other') + 1:] if '--other' in rest else []
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.makedirs(OUT, exist_ok=True)
INI = os.path.join(OUT, 'settings.ini')
if os.path.exists(INI):
    os.remove(INI)
os.environ['SPEKTRAFILM_SETTINGS_INI'] = INI
sys.path.insert(0, SRC)

import lensfunpy  # noqa: E402
import numpy as np  # noqa: E402

import spektrafilm  # noqa: E402
from spektrafilm.utils import raw_file_processor as rfp  # noqa: E402

assert os.path.samefile(os.path.dirname(spektrafilm.__file__), os.path.join(SRC, 'spektrafilm')), (
    f'imported {spektrafilm.__file__}, not the engine under {SRC}')

failures = []


def check(name, ok, detail=''):
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
    if not ok:
        failures.append(name)


db = lensfunpy.Database()


def lookup(path):
    meta = rfp._read_exif_metadata(path)
    camera = db.find_cameras(meta.make, meta.model, loose_search=True)[0]
    every = db.find_lenses(camera, None, None, loose_search=True)
    found = rfp._find_lens_candidates(db, camera, meta)
    return meta, camera, every, found


# 1 + 2. the lookup and the correction it drives, per fixed-lens raw
for path in FIXED:
    meta, camera, every, found = lookup(path)
    name = os.path.basename(path)
    print(f'\n{name}: {meta.model}, EXIF lens {meta.lens_model!r} at {meta.focal_length:g} mm; '
          f'lensfun has {[lens.model for lens in every]}')
    check(f'1. {name}: the lookup finds the camera\'s one lens',
          len(found) == 1 and found[0].model == every[0].model,
          f'(found {[lens.model for lens in found]})')
    # a left-to-right ramp (value = column) of the Q's 3:2 frame: after the
    # correction, the top-left pixel's value says which column it came from
    height, width = 400, 600
    ramp = np.repeat(np.repeat(np.linspace(0.0, width - 1.0, width, dtype=np.float32)[None, :, None],
                               height, axis=0), 3, axis=2)
    corrected, summary = rfp._apply_lens_correction(ramp.copy(), meta)
    shift = float(corrected[0, 0, 1])
    check(f'2. {name}: the distortion correction is applied (the corner pixel comes from further in)',
          bool(summary) and shift > 5.0,
          f'(summary {summary!r}, corner moved {shift:.1f} px of {width})')

# interchangeable-lens cameras: the new rule never reaches them
for path in OTHER:
    meta, camera, every, found = lookup(path)
    chosen = rfp._select_lens_candidate(found, meta).model if found else None
    name = os.path.basename(path)
    print(f'\n{name}: {meta.model}, EXIF lens {meta.lens_model!r}; lens used: {chosen!r}')
    check(f'{name}: an interchangeable-lens camera lists more than one lens, so the new rule cannot apply',
          len(every) > 1, f'({len(every)} lenses for its mount)')

# 3. through the real app
from qtpy import QtWidgets  # noqa: E402

import spektrafilm_gui.app as app_module  # noqa: E402
from spektrafilm_gui.app import create_app  # noqa: E402

qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
app = create_app()
c = app.controller
win = app.main_window
win.resize(1600, 1000)
win.show()


def settle(seconds=0.3):
    deadline = time.time() + seconds
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.02)


if app_module._background_warmup_pool is not None:
    app_module._background_warmup_pool.waitForDone(300000)
settle(1)
c._widgets.load_raw.lens_correction.value = True     # Recover > Lens correction, on
settle(0.5)
c.load_raw_image(FIXED[0])
settle(1)
status = win.statusBar().currentMessage()
expected = lookup(FIXED[0])[2][0].model
check(f'3. the app, Lens correction on, loading {os.path.basename(FIXED[0])}: "applied lens correction", '
      f'with the lens named', 'applied lens correction' in status and expected in status, f'(status: {status!r})')

print(f'\n{"2.2.1" if BASELINE else "fixed"}: {len(failures)} check(s) fail' +
      (': ' + '; '.join(failures) if failures else ''))
sys.exit(0 if BASELINE else (1 if failures else 0))
