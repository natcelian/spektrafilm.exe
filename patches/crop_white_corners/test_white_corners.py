"""Checks for the white corners fix, headless, through the real app.

usage: python test_white_corners.py <src dir> <out dir> <raw> [--baseline]

Turning a photo (Scan > Frame > Straighten) leaves corners that are not part
of the picture. 2.2.1 filled them by replicating the picture's edge pixels
outward (`mode='edge'`), which reads as a smeared, stretched photo. The fix
fills them with white, on the CPU (`crop_resize.apply_skew`) and on the GPU
(the skew shader), as Lightroom does.

The check renders the real preview at 10 degrees with no crop and looks at
every pixel OUTSIDE the turned picture -- found exactly, by turning an
all-ones mask the way the engine turns the photo, with 0 outside. With the
fix they are white; with `--baseline` against 2.2.1 the same script reports
how many of them hold smeared picture instead. Exits non-zero on a failure.
"""
import os
import sys
import time

if len(sys.argv) < 4:
    sys.exit(__doc__)
SRC, OUT, RAW = sys.argv[1:4]
BASELINE = '--baseline' in sys.argv
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.makedirs(OUT, exist_ok=True)
INI = os.path.join(OUT, 'settings.ini')
if os.path.exists(INI):
    os.remove(INI)
os.environ['SPEKTRAFILM_SETTINGS_INI'] = INI
sys.path.insert(0, SRC)

import numpy as np  # noqa: E402
import skimage.transform  # noqa: E402
from qtpy import QtCore, QtWidgets  # noqa: E402
from qtpy.QtTest import QTest  # noqa: E402

import spektrafilm_gui  # noqa: E402
import spektrafilm_gui.app as app_module  # noqa: E402
from spektrafilm.utils.crop_resize import apply_skew  # noqa: E402
from spektrafilm_gui.app import create_app  # noqa: E402

assert os.path.samefile(os.path.dirname(spektrafilm_gui.__file__), os.path.join(SRC, 'spektrafilm_gui')), (
    f'imported {spektrafilm_gui.__file__}, not the GUI under {SRC}')

failures = []


def check(name, ok, detail=''):
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
    if not ok:
        failures.append(name)


ANGLE = 10.0

# --- the turn itself, CPU and GPU, on a plain test image
probe = np.random.default_rng(0).uniform(0.2, 0.6, (300, 400, 3))
cpu = apply_skew(probe, ANGLE)
try:
    from spektrafilm.gpu.scan_geometry import skew_dispatch
    gpu = skew_dispatch(probe.astype(np.float32), ANGLE)
except Exception as exc:                    # noqa: BLE001 - reported below
    gpu = None
    print('GPU skew unavailable:', exc)
if not BASELINE:
    from spektrafilm.utils.crop_resize import SKEW_FILL
    check('CPU: the corners the turn brings in are white', np.allclose(cpu[0, 0], SKEW_FILL)
          and np.allclose(cpu[-1, -1], SKEW_FILL), f'({cpu[0, 0]}, {cpu[-1, -1]})')
    check('GPU: the same, and it matches the CPU', gpu is not None and np.allclose(gpu[0, 0], SKEW_FILL)
          and float(np.max(np.abs(gpu - cpu))) < 1e-4,
          f'(max diff {float(np.max(np.abs(gpu - cpu))):.1e})' if gpu is not None else '(no GPU)')
    centre = (slice(120, 180), slice(160, 240))
    stock_centre = skimage.transform.rotate(probe, ANGLE, resize=False, order=1, mode='edge')[centre]
    check('inside the picture nothing changes (the turn itself is the same)',
          float(np.max(np.abs(cpu[centre] - stock_centre))) < 1e-12)

# --- the real preview, through the app
qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
app = create_app()
c = app.controller
win = app.main_window
win.resize(1600, 1000)
win.show()
fin = c._widgets.scan_finishing


def settle(seconds=0.3):
    deadline = time.time() + seconds
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.02)


def wait_idle(timeout=180):
    deadline = time.time() + timeout
    settle(0.5)
    while time.time() < deadline and c._active_simulation_worker is not None:
        settle(0.1)
    settle(1.0)


if app_module._background_warmup_pool is not None:
    app_module._background_warmup_pool.waitForDone(300000)
c.load_raw_image(RAW)
wait_idle()
c.set_input_mode(False)
wait_idle()
c.run_preview()                            # 'Click Preview once': live preview starts after it
wait_idle()

row = fin.skew_deg.parentWidget()
while row is not None and not callable(getattr(row, '_apply_typed', None)):
    row = row.parentWidget()
check('the Straighten field the user types in was found', row is not None)
QTest.mouseClick(row.value, QtCore.Qt.LeftButton)
settle(0.1)
row.value._line.selectAll()
QTest.keyClicks(row.value._line, f'{ANGLE:g}')
QTest.keyClick(row.value._line, QtCore.Qt.Key_Return)
wait_idle()
check('a render shows the turned photo', c._output_display_cached is not None)
if bool(fin.crop.value):                  # 2.2.1 applies a typed angle with no crop
    print('note: a crop is on; turning it off to see the whole turned picture')
    fin.crop.value = False
    wait_idle()
check(f'the angle is {ANGLE:g} and no crop is on', abs(float(fin.skew_deg.value) - ANGLE) < 1e-9
      and not bool(fin.crop.value))
shown = np.asarray(c._output_display_cached)[..., :3]
height, width = shown.shape[:2]
# pixels the turned picture contributes NOTHING to (the edge row, where the
# bilinear turn blends the picture's edge with the fill, is left out)
outside = skimage.transform.rotate(np.ones((height, width)), ANGLE, resize=False, order=1,
                                   mode='constant', cval=0.0) < 1e-6
values = shown[outside].astype(int)
white = int(np.sum(np.all(values >= 250, axis=1)))
total = int(outside.sum())
print(f'\n{"2.2.1" if BASELINE else "fixed"}: {total:,} preview pixels lie outside the turned picture at '
      f'{ANGLE:g}°; {white:,} of them white, {total - white:,} hold smeared picture '
      f'({100.0 * (total - white) / max(total, 1):.1f}%)')
if not BASELINE:
    check('every pixel outside the turned picture is white (none smeared)', white == total,
          f'({total - white} not white)')
QtWidgets.QApplication.instance().processEvents()
try:
    from PIL import Image
    Image.fromarray(np.ascontiguousarray(shown)).save(os.path.join(OUT, '2.2.1.png' if BASELINE else 'fixed.png'))
except Exception:                         # noqa: BLE001 - a picture is a convenience
    pass

print('\nFAILURES:', failures if failures else 'none')
sys.exit(1 if failures else 0)
