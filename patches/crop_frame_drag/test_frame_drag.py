"""Checks for the crop frame dragging fix, headless, through the real app.

usage: python test_frame_drag.py <src dir> <out dir> <raw> [--baseline]

With the crop frame armed (the rail's crop button) on a photo turned 10
degrees, three things are checked, with the app's own handlers and real
mouse events on the canvas:

1. dragging a handle moves ONLY that handle, and it stops at the photo's
   edge (2.2.1 re-fitted the whole frame after every move, which slid and
   shrank it, moving the edges the user was not holding);
2. a press on or just outside the frame resizes it and never turns the
   photo, with a cursor that says so (2.2.1 turned the photo for any press
   a pixel outside the frame, except within 8 px of a handle);
3. turning the photo (a drag outside the frame) keeps the frame where the
   user put it, shrinking it about its centre (2.2.1 slid it).

`--baseline` runs the same against 2.2.1 and lists what fails there, for the
before/after table. Exits non-zero on a failure (not with --baseline).
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

from qtpy import QtCore, QtWidgets  # noqa: E402
from qtpy.QtTest import QTest  # noqa: E402

import spektrafilm_gui  # noqa: E402
import spektrafilm_gui.app as app_module  # noqa: E402
from spektrafilm_gui.app import create_app  # noqa: E402
from spektrafilm_gui.crop_frame import fit_rect_in_rotated_image  # noqa: E402

assert os.path.samefile(os.path.dirname(spektrafilm_gui.__file__), os.path.join(SRC, 'spektrafilm_gui')), (
    f'imported {spektrafilm_gui.__file__}, not the GUI under {SRC}')

failures = []


def check(name, ok, detail=''):
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
    if not ok:
        failures.append(name)


qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
app = create_app()
c = app.controller
win = app.main_window
win.resize(1600, 1000)
win.show()
fin = c._widgets.scan_finishing


def settle(seconds=0.2):
    deadline = time.time() + seconds
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


def wait_idle(timeout=180):
    deadline = time.time() + timeout
    settle(0.4)
    while time.time() < deadline and c._active_simulation_worker is not None:
        settle(0.05)
    settle(0.8)


def fits(rect, skew):
    """Inside the turned photo: a re-fit would leave it where it is (the
    stock fit, so the check means the same on 2.2.1)."""
    fitted = fit_rect_in_rotated_image(rect, skew, c._crop_image_wh())
    return all(abs(a - b) < 1e-4 for a, b in zip(rect, fitted))


def release():
    """The canvas's 'handle let go' (2.2.1 has no handler for it)."""
    handler = getattr(c, '_frame_drag_released', None)
    if callable(handler):
        handler()


def turn_to(angle):
    """A drag outside the frame, as the canvas reports it."""
    c._skew_drag_started()
    c._skew_dragged(angle - float(fin.skew_deg.value), False)
    c._skew_drag_released()
    wait_idle()


if app_module._background_warmup_pool is not None:
    app_module._background_warmup_pool.waitForDone(300000)
c.load_raw_image(RAW)
wait_idle()
c.set_input_mode(False)
wait_idle()
c.run_preview()
wait_idle()
c.set_crop_mode(True)                     # the rail's crop button
wait_idle()
check('the crop frame is armed', c._frame_rect_uv is not None)
turn_to(10.0)
check('the photo turned to 10° and the frame fits inside it',
      abs(float(fin.skew_deg.value) - 10.0) < 1e-6 and fits(c._frame_rect_uv, 10.0),
      f'({c._frame_rect_uv})')

# 1. a handle moves alone and stops at the edge
c._frame_dragged('s', 0.0, -0.1, False)                 # pull the bottom in first
release()
before = c._frame_rect_uv
for _ in range(60):                                     # then the top, far past the edge
    c._frame_dragged('n', 0.0, -0.01, False)
release()
after = c._frame_rect_uv
others_still = all(abs(a - b) < 1e-9 for a, b in ((after[0], before[0]), (after[2], before[2]),
                                                    (after[3], before[3])))
check('1a. dragging the top handle past the edge: left, right and bottom never move', others_still,
      f'({tuple(round(v, 4) for v in before)} -> {tuple(round(v, 4) for v in after)})')
check('1b. ...and the top stops inside the turned photo', fits(after, 10.0))
corner_from = c._frame_rect_uv
for _ in range(60):
    c._frame_dragged('nw', -0.01, -0.002, False)
release()
corner = c._frame_rect_uv
check('1c. dragging a corner: the opposite corner never moves',
      abs(corner[2] - corner_from[2]) < 1e-9 and abs(corner[3] - corner_from[3]) < 1e-9,
      f'({tuple(round(v, 4) for v in corner)})')
size = (corner[2] - corner[0], corner[3] - corner[1])
for _ in range(80):
    c._frame_dragged('inside', 0.01, 0.0, False)
release()
moved = c._frame_rect_uv
check('1d. moving the frame past the edge keeps its size, inside the photo',
      abs((moved[2] - moved[0]) - size[0]) < 1e-9 and abs((moved[3] - moved[1]) - size[1]) < 1e-9
      and fits(moved, 10.0), f'({tuple(round(v, 4) for v in moved)})')

# 2. where a press goes (real mouse events)
overlay = c._gpu_canvas.overlay
CURSORS = {QtCore.Qt.SizeVerCursor: 'resize', QtCore.Qt.SizeHorCursor: 'resize',
           QtCore.Qt.SizeFDiagCursor: 'resize', QtCore.Qt.SizeBDiagCursor: 'resize',
           QtCore.Qt.SizeAllCursor: 'move', QtCore.Qt.BitmapCursor: 'turn'}


def frame_px():
    x0, y0, x1, y1 = overlay._host.frame_rect_px()
    off = overlay.canvas_offset()
    return (x0 + off.x(), y0 + off.y(), x1 + off.x(), y1 + off.y())


def press_drag(where, dx, dy):
    x0, y0, x1, y1 = frame_px()
    px, py = where(x0, y0, x1, y1)
    QTest.mouseMove(overlay, QtCore.QPoint(int(px), int(py)))
    settle(0.1)
    cursor = CURSORS.get(overlay.cursor().shape(), 'arrow')
    angle0, frame0 = float(fin.skew_deg.value), c._frame_rect_uv
    QTest.mousePress(overlay, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(int(px), int(py)))
    for i in range(1, 7):
        QTest.mouseMove(overlay, QtCore.QPoint(int(px + dx * i / 6), int(py + dy * i / 6)))
        settle(0.03)
    QTest.mouseRelease(overlay, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier,
                       QtCore.QPoint(int(px + dx), int(py + dy)))
    wait_idle()
    turned = abs(float(fin.skew_deg.value) - angle0) > 1e-6
    changed = any(abs(a - b) > 1e-6 for a, b in zip(frame0, c._frame_rect_uv))
    return cursor, turned, changed


for name, where, dx, dy in (
        ('1 px outside an edge, a quarter along', lambda x0, y0, x1, y1: (x0 + (x1 - x0) * .25, y0 - 1), 0, 25),
        ('9 px outside the middle handle', lambda x0, y0, x1, y1: ((x0 + x1) / 2, y0 - 9), 0, 25),
        ('13 px outside a corner', lambda x0, y0, x1, y1: (x0 - 13, y0 - 13), 30, 30),
        ('19 px outside an edge', lambda x0, y0, x1, y1: (x1 + 19, (y0 + y1) / 2), -30, 0)):
    cursor, turned, changed = press_drag(where, dx, dy)
    check(f'2. a press {name}: a resize cursor, the frame resizes, the photo does not turn',
          cursor == 'resize' and changed and not turned,
          f'(cursor {cursor}, frame changed {changed}, photo turned {turned})')
cursor, turned, changed = press_drag(lambda x0, y0, x1, y1: ((x0 + x1) / 2, (y0 + y1) / 2), 20, 10)
check('2. a press inside the frame: the move cursor, the photo does not turn',
      cursor == 'move' and not turned, f'(cursor {cursor}, photo turned {turned})')
cursor, turned, changed = press_drag(lambda x0, y0, x1, y1: (x1 + 35, y0 + 40), 0, 80)
check('2. a press 35 px outside: the turn cursor, and the photo turns', cursor == 'turn' and turned,
      f'(cursor {cursor}, photo turned {turned})')

# 3. turning keeps the frame where the user put it
turn_to(2.0)
c._frame_dragged('se', -0.3, -0.3, False)
release()
c._frame_dragged('inside', -0.1, -0.08, False)
release()
f0 = c._frame_rect_uv
centre0 = ((f0[0] + f0[2]) / 2, (f0[1] + f0[3]) / 2)
turn_to(-22.0)
f1 = c._frame_rect_uv
centre1 = ((f1[0] + f1[2]) / 2, (f1[1] + f1[3]) / 2)
check('3a. turning an off-centre frame keeps its centre and shape, inside the photo',
      max(abs(a - b) for a, b in zip(centre0, centre1)) < 1e-6
      and abs((f1[2] - f1[0]) / (f1[3] - f1[1]) - (f0[2] - f0[0]) / (f0[3] - f0[1])) < 1e-6
      and fits(f1, -22.0), f'(centre {tuple(round(v, 3) for v in centre0)} -> '
                           f'{tuple(round(v, 3) for v in centre1)})')

# 4. Done keeps the frame that was on screen
c._frame_dragged('se', -0.05, -0.04, False)
release()
W, H = c._crop_image_wh()
shown = c._frame_rect_uv
c._frame_commit()
wait_idle()
from spektrafilm_gui.crop_frame import crop_rect_uv  # noqa: E402
kept = crop_rect_uv(tuple(fin.crop_center.value), tuple(fin.crop_size.value), (W, H))
shift = max(abs(a - b) * s for a, b, s in zip(shown, kept, (W, H, W, H)))
check('4. Done keeps the frame on screen to within a pixel of the preview', shift <= 1.0,
      f'(largest edge shift {shift:.1f} px of the {int(W)} px preview)')

print(f'\n{"2.2.1" if BASELINE else "fixed"}: {len(failures)} check(s) fail' +
      (': ' + '; '.join(failures) if failures else ''))
sys.exit(0 if BASELINE else (1 if failures else 0))
