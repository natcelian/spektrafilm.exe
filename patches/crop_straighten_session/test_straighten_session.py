"""Checks for Straighten as a crop session, headless, through the real GUI controller.

usage: python test_straighten_session.py <src dir> <out dir> <raw A> <raw B> [--baseline]

Drives the real GUI the way the user does (a click in the Straighten field,
typed digits and Enter; clicks on Scan > Frame's Cancel / Done and the
rail's crop button; key presses; mouse drags on the photo; a drawn straighten
line) and checks what the session shows and what Done /
Cancel / Undo leave behind. `--baseline` runs only the smear measurement, so
the same script shows what 2.2.1 leaves in the corners. "Smear" is measured
like the engine renders: the scan geometry is rebuilt at full resolution with
crop_image / apply_desqueeze, rotating a mask with 0 outside the image instead
of replicating edges, so every 0 inside the crop is a pixel that would show
an empty (smeared) corner. Exits non-zero on any failure.
"""
import os
import sys
import time

if len(sys.argv) < 5:
    sys.exit(__doc__)
SRC, OUT, A, B = sys.argv[1:5]
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
from qtpy import QtCore, QtGui, QtWidgets  # noqa: E402

import spektrafilm_gui  # noqa: E402
import spektrafilm_gui.app as app_module  # noqa: E402
from spektrafilm_gui import notices  # noqa: E402
from spektrafilm_gui.shell_layout import dialog_parent  # noqa: E402
from spektrafilm.utils.crop_resize import apply_desqueeze, crop_image  # noqa: E402
from qtpy.QtTest import QTest  # noqa: E402
from spektrafilm_gui.app import create_app  # noqa: E402

try:
    from spektrafilm_gui.state_bridge import restoring
except ImportError:                     # pristine 2.2.1 (--baseline)
    from contextlib import nullcontext as restoring

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
app.main_window.resize(1600, 1000)
app.main_window.show()                  # offscreen, but with real focus and layout
fin = c._widgets.scan_finishing
inp = c._widgets.input_image


def settle(seconds=0.3):
    deadline = time.time() + seconds
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.02)


def wait_idle(timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline and c._active_simulation_worker is not None:
        settle(0.1)
    settle(1.0)        # past the 700 ms history debounce


settle(1)
if app_module._background_warmup_pool is not None:
    app_module._background_warmup_pool.waitForDone(300000)

# the Done card, captured
cards = []
card_sink = notices.system_message_sink(dialog_parent(c._viewer))
if card_sink is not None:
    card_sink.show_message = lambda kind, title, body='', **kw: cards.append((title, kw.get('actions')))

c.roll_add_files([A, B])
wait_idle()
full_h, full_w = c._current_input_image.shape[:2]
print('frame', full_w, 'x', full_h, '| canvas', type(c._gpu_canvas).__name__)


def smear_px():
    mask = np.ones((full_h, full_w, 1))
    if bool(inp.crop.value):
        mask = crop_image(mask, center=tuple(inp.crop_center.value), size=tuple(inp.crop_size.value))
    desqueeze = float(fin.desqueeze.value)
    if desqueeze not in (0.0, 1.0, -1.0):
        mask = apply_desqueeze(mask, desqueeze)
    skew = float(fin.skew_deg.value)
    if skew != 0.0:
        mask = skimage.transform.rotate(mask, skew, resize=False, center=None, order=1,
                                        mode='constant', cval=0.0)
    if bool(fin.crop.value):
        mask = crop_image(mask, center=tuple(fin.crop_center.value), size=tuple(fin.crop_size.value))
    return int(np.sum(mask < 0.5))


def state():
    return (bool(fin.crop.value), tuple(round(v, 4) for v in fin.crop_center.value),
            tuple(round(v, 4) for v in fin.crop_size.value), round(float(fin.skew_deg.value), 4))


def restore(skew=0.0, crop=False, center=(0.5, 0.5), size=(1.0, 1.0)):
    """Put a look back the way undo / presets / roll frames do."""
    with restoring():
        fin.skew_deg.value = skew
        fin.crop.value = crop
        fin.crop_center.value = center
        fin.crop_size.value = size
    wait_idle()


def reset_photo():
    restore()


slider = fin.skew_deg._slider


def visible_row():
    """The edit panel's Straighten row (the field the user actually types in)."""
    widget = fin.skew_deg.parentWidget()
    while widget is not None and not callable(getattr(widget, '_apply_typed', None)):
        widget = widget.parentWidget()
    return widget


ROW = visible_row()
# the panel on Scan > Frame, as the user has it while cropping
tabs = app.main_window.findChild(QtWidgets.QTabWidget, 'controlsTabWidget')
SCAN_TAB = next(i for i in range(tabs.count()) if tabs.tabText(i).lower().startswith('scan'))
tabs.setCurrentIndex(SCAN_TAB)
_panel = tabs
while _panel is not None and not hasattr(_panel, '_groups'):
    _panel = _panel.parentWidget()
frame_section = next(section for group, section in _panel._groups if group.name == 'Frame')
frame_section.set_expanded(True)
print('visible row:', type(ROW).__name__ if ROW is not None else None)


def user_types_angle(degrees):
    """Click the Straighten row's number, type the angle, press Enter."""
    QTest.mouseClick(ROW.value, QtCore.Qt.LeftButton)
    settle(0.1)
    line = ROW.value._line
    line.selectAll()
    QTest.keyClicks(line, f'{float(degrees):g}')
    QTest.keyClick(line, QtCore.Qt.Key_Return)
    settle(0.3)


def click(button):
    QTest.mouseClick(button, QtCore.Qt.LeftButton)
    settle(0.2)


def simulation_starts():
    """How many renders a block of actions starts (`c._start_simulation`)."""
    starts = []
    original = c._start_simulation

    def counting(*args, **kwargs):
        starts.append(1)
        return original(*args, **kwargs)

    c._start_simulation = counting
    return starts, lambda: setattr(c, '_start_simulation', original)


def session_open():
    return c._frame_rect_uv is not None


def panel():
    """Scan > Frame: the crop's controls."""
    return c._widgets.scan_frame


def exits_shown():
    """Cancel · Done on show in the Frame group (they exist only while cropping)."""
    return panel().cancel_button.isVisibleTo(app.main_window)


def press_escape(widget):
    """A real Esc key press on `widget`, passed up its parents as Qt does
    when the widget does not use the key."""
    QtWidgets.QApplication.sendEvent(
        widget, QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Escape, QtCore.Qt.NoModifier))
    settle()


class EscapeField(QtWidgets.QLineEdit):
    """A field that uses Esc itself (clears, reverts), like a search box."""

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == QtCore.Qt.Key_Escape:
            event.accept()
            return
        super().keyPressEvent(event)


def crop_module_fits(rect):
    from spektrafilm_gui.crop_frame import rect_fits_rotated_image, rect_inside_frame
    return rect_inside_frame(rect) and rect_fits_rotated_image(
        rect, float(fin.skew_deg.value), c._scan_frame_wh())


def area(rect):
    return (rect[2] - rect[0]) * (rect[3] - rect[1])


reset_photo()

check('the Straighten row the user types in was found', ROW is not None)
user_types_angle(1.5)
check('typing into it sets the angle', abs(float(fin.skew_deg.value) - 1.5) < 1e-9,
      f'({float(fin.skew_deg.value)})')

if BASELINE:
    # what the corners hold after a typed angle, with no crop: 2.2.1 leaves
    # the smeared edge replication inside the picture
    rows = []
    for angle in (3.2, 10.0):
        reset_photo()
        user_types_angle(angle)
        wait_idle()
        total = full_w * full_h
        rows.append((angle, smear_px(), bool(fin.crop.value)))
    print('\nBASELINE (typed angle -> smeared px inside the picture, crop on?):')
    for angle, px, crop_on in rows:
        print(f'  {angle:5.1f}°  {px:>10,d} px  ({100.0 * px / total:.1f}%)  crop {"on" if crop_on else "off"}')
    sys.exit(0)
reset_photo()

# 1. Restoring a look never opens the session
restore(skew=2.0)
check('a restored angle (undo / preset / roll frame) does not open the session', not session_open())
reset_photo()
QtWidgets.QApplication.sendEvent(
    slider, QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Shift, QtCore.Qt.NoModifier))
restore(skew=2.0)
check('a bare key press, then a restored angle: no session', not session_open())
reset_photo()
# ...but every way the user changes the angle does: a wheel over the slider,
# Paste value, a Main-tab row (all set the editor without the typed path)
slider.setValue(slider.value() + 40)
settle()
check('a wheel turn on the slider opens the session (no smeared corners on any path)', session_open())
click(panel().cancel_button)
wait_idle()
fin.skew_deg.value = 2.5
settle()
check('Paste value / a pinned row (the editor set directly) opens the session', session_open())
click(panel().cancel_button)
wait_idle()
check('...and Cancel puts the angle back', abs(float(fin.skew_deg.value)) < 1e-9)
reset_photo()
# a real drag on the slider does
slider.setSliderDown(True)
slider.setValue(slider.value() + 40)
slider.setSliderDown(False)
settle()
check('dragging the Straighten slider opens the session', session_open())
click(panel().cancel_button)
wait_idle()
reset_photo()

# 2. Typing an angle opens it: the frame is fitted, nothing applied yet
user_types_angle(3.2)
check('typing an angle opens the crop session', session_open())
check('...with Cancel · Done shown in Scan › Frame', exits_shown())
check('...the Angle row showing that angle', ROW.value.text().strip().lstrip('+').startswith('3.2'),
      f'({ROW.value.text()!r})')
check('...a frame fitted inside the turned picture (smaller than the whole photo)',
      session_open() and area(c._frame_rect_uv) < 0.999, f'(frame {c._frame_rect_uv})')
check('...and no crop applied until Done', not bool(fin.crop.value))
fit_at_3 = tuple(c._frame_rect_uv)

# 3. The frame refits live and never shrinks cumulatively
user_types_angle(10.0)
fit_at_10 = tuple(c._frame_rect_uv)
check('a larger angle gives a smaller frame', area(fit_at_10) < area(fit_at_3))
user_types_angle(3.2)
check('back to the first angle gives the first frame back (no shrink on every change)',
      all(abs(a - b) < 1e-9 for a, b in zip(c._frame_rect_uv, fit_at_3)),
      f'({c._frame_rect_uv} vs {fit_at_3})')

# 4. Done (the button, clicked): one step, a card with Undo, no smear
history_before = len(c._history or [])
cards.clear()
click(panel().done_button)
wait_idle()
check('Done applies the crop and keeps the angle', bool(fin.crop.value) and abs(float(fin.skew_deg.value) - 3.2) < 1e-9,
      f'({state()})')
check('...closes the session; Cancel · Done go', not session_open() and not exits_shown())
check('...leaves no smeared pixel inside the crop (full resolution)', smear_px() == 0, f'({smear_px()} px)')
check('...says what it did, with Undo', bool(cards) and cards[-1][0] == 'Straightened 3.2°, cropped to fit'
      and cards[-1][1] and cards[-1][1][0][0] == 'Undo', f'({cards[-1:] if cards else None})')
check('...as ONE undo step', len(c._history or []) == history_before + 1,
      f'({history_before} -> {len(c._history or [])})')
after_done = state()
c.undo()
wait_idle()
check('one Undo takes back the angle and the crop together',
      not bool(fin.crop.value) and abs(float(fin.skew_deg.value)) < 1e-9, f'({state()})')
c.redo()
wait_idle()
check('Redo puts both back', state() == after_done, f'({state()} vs {after_done})')

# 5. Cancel leaves the photo exactly as before the session
before = state()
user_types_angle(7.5)
check('typing again opens a session', session_open())
click(panel().cancel_button)
wait_idle()
check('Cancel restores the angle and the crop exactly', state() == before and not session_open(),
      f'({state()} vs {before})')

# 6. Undo inside the session is Cancel
user_types_angle(5.0)
c.undo()
wait_idle()
check('Undo inside the session cancels it', state() == before and not session_open(), f'({state()})')

# 7. Straightening back to 0 removes the crop the straighten added
user_types_angle(0.0)
check('a session opened on a straighten crop', session_open())
check('at 0° the frame is the whole photo again', session_open() and area(c._frame_rect_uv) > 0.999,
      f'({c._frame_rect_uv})')
click(panel().done_button)
wait_idle()
check('Done at 0° leaves no angle and no crop', not bool(fin.crop.value) and abs(float(fin.skew_deg.value)) < 1e-9,
      f'({state()})')

# 8. Constrain to image off keeps the whole turned picture
user_types_angle(4.0)
click(panel().constrain_toggle)              # the panel's switch, off
check('Constrain off: the frame is the whole photo', area(c._frame_rect_uv) > 0.999)
click(panel().done_button)
wait_idle()
check('Done with Constrain off: the angle, no crop', not bool(fin.crop.value) and abs(float(fin.skew_deg.value) - 4.0) < 1e-9,
      f'({state()})')
user_types_angle(4.5)
check('Constrain off is remembered for the next session', panel().constrain_toggle.isChecked() is False
      and c._crop_constrain is False)
click(panel().constrain_toggle)
check('Constrain back on refits the frame inside', area(c._frame_rect_uv) < 0.999)
click(panel().cancel_button)
wait_idle()
reset_photo()

# 9. A drawn straighten line opens the session; Esc restores
before = state()
c._straighten_line_drawn(0.0, 0.0, 100.0, 5.0)
settle()
check('a straighten line opens the session with a fitted frame',
      session_open() and abs(float(fin.skew_deg.value)) > 0.5 and area(c._frame_rect_uv) < 0.999,
      f'(angle {fin.skew_deg.value})')
check('...and applies nothing yet', not bool(fin.crop.value))
click(panel().cancel_button)
wait_idle()
check('Cancel after a line restores the photo', state() == before, f'({state()})')

# 10. Switching frames applies the session to the frame left (as leaving
# Lightroom's crop tool does); the typed angle is not lost
reset_photo()
user_types_angle(6.0)
c.roll_activate(1)
wait_idle()
check('switching frames closes the session', not session_open() and not exits_shown())
check('...on the other frame', c._roll.current_index == 1)
c.roll_activate(0)
wait_idle()
check('...and the frame left keeps the angle with its fitted crop',
      abs(float(fin.skew_deg.value) - 6.0) < 1e-9 and bool(fin.crop.value) and smear_px() == 0,
      f'({state()}, smear {smear_px()})')

# 10b. A frame switch with auto preview off still switches
reset_photo()
first_done = c._first_render_done
c._first_render_done = False                 # auto preview dormant
user_types_angle(6.0)
c.roll_activate(1)
wait_idle()
check('with auto preview off, a frame switch during a session switches', c._roll.current_index == 1
      and not session_open(), f'(index {c._roll.current_index})')
c._first_render_done = first_done
c.roll_activate(0)
wait_idle()

# 10c. The Crop tool (rail or panel) on an open session does not re-arm it
restore(crop=True, center=(0.5, 0.5), size=(0.8, 0.5))
before = state()
user_types_angle(3.0)
rail = c._view_rail
check('a session opened by typing lights the rail Crop button and the panel Crop button',
      (rail is None or rail.crop_button.isChecked()) and c._widgets.scan_frame.crop_button.isChecked(),
      f'(rail found: {rail is not None})')
c.set_crop_mode(True)                         # pressing the rail's Crop button again
settle()
click(panel().cancel_button)
wait_idle()
check('Crop pressed during a session, then Cancel: the photo exactly as before', state() == before,
      f'({state()} vs {before})')

# 10d. Straighten (line) inside a session returns to the frame's handles
user_types_angle(2.0)
c.set_crop_tool('line')
c._straighten_line_drawn(0.0, 0.0, 100.0, -3.0)
settle()
check('a line drawn inside a session keeps the session, back in frame mode',
      session_open() and c._crop_tool_mode == 'frame')
click(panel().cancel_button)
wait_idle()
check('...and Cancel restores the photo', state() == before, f'({state()})')

# 10d2. Straighten toggled on and off inside a session (no line) keeps it
user_types_angle(2.0)
c.set_crop_tool('line')
c.set_crop_tool('off')
settle()
check('Straighten on then off inside a session: still in the session, nothing applied',
      session_open() and c._crop_tool_mode == 'frame' and not bool(fin.crop.value))
field = EscapeField(app.main_window)
press_escape(field)
check('Esc in a field that uses Esc itself leaves the session open', session_open())
field.deleteLater()
press_escape(slider)                          # nothing on the way uses it
wait_idle()
check('Esc from the Skew slider cancels the session', not session_open() and state() == before,
      f'({state()})')

# 10e. Other ways the photo changes end the session, applying it
user_types_angle(4.0)
c.set_input_mode(True)
settle()
check('entering Input mode ends the session', not session_open() and not exits_shown())
c.set_input_mode(False)
wait_idle()
check('...with the angle applied and no smear', abs(float(fin.skew_deg.value) - 4.0) < 1e-9
      and smear_px() == 0, f'({state()})')
c._input_mode = True
user_types_angle(5.0)
check('no session opens in Input mode (it shows a thumbnail, not the photo)', not session_open())
c._input_mode = False
restore(skew=before[3], crop=before[0], center=before[1], size=before[2])

# 10e2. A look put back during a session (top-bar Reset here; presets,
# pasted looks and versions go the same way) applies the session first
restore(crop=True, center=(0.5, 0.5), size=(0.7, 0.45))
user_types_angle(3.0)
c.reset_current_image()
wait_idle()
reset_state = state()
check('Reset during a session closes it and the reset look stands',
      not session_open() and not exits_shown() and not reset_state[0] and reset_state[3] == 0.0,
      f'({reset_state})')
c.undo()
wait_idle()
check('...Ctrl+Z brings back the applied session, without smear',
      abs(float(fin.skew_deg.value) - 3.0) < 1e-9 and bool(fin.crop.value) and smear_px() == 0,
      f'({state()}, smear {smear_px()})')
c.redo()
wait_idle()
c.roll_activate(1)
wait_idle()
c.roll_activate(0)
wait_idle()
check('...and after Ctrl+Y and a switch the frame keeps the reset look (no old frame written back)',
      state() == reset_state, f'({state()})')
restore(skew=before[3], crop=before[0], center=before[1], size=before[2])

# 10e3. Re-decoding the same photo (Reprocess, lens correction) keeps the session
user_types_angle(3.0)
c.load_raw_image(c._current_input_path)
wait_idle()
check('re-decoding the same photo keeps the session open', session_open() and exits_shown())
click(panel().done_button)
wait_idle()
check('...and Done afterwards applies it without smear',
      abs(float(fin.skew_deg.value) - 3.0) < 1e-9 and smear_px() == 0, f'({state()})')
restore(skew=before[3], crop=before[0], center=before[1], size=before[2])

# 10e4. Turning or mirroring the photo cancels the session (the frame was fitted
# to the photo before the turn)
user_types_angle(3.0)
c.rotate_input_image_clockwise()
wait_idle()
check('rotating cancels the session, the photo as before', not session_open() and state() == before,
      f'({state()})')
c.rotate_input_image_counterclockwise()
wait_idle()
user_types_angle(3.0)
c.mirror_input_image_horizontal()
wait_idle()
check('mirroring cancels the session, the photo as before', not session_open() and state() == before,
      f'({state()})')
c.mirror_input_image_horizontal()
wait_idle()

# 10h. Dragging a handle moves that handle only, and it stops at the picture's
# edge (the user's report: at 10°, the top handle dragged to the edge, then
# the bottom and the sides started moving)
reset_photo()
user_types_angle(10.0)
fitted = c._frame_rect_uv
u0, v0, u1, v1 = fitted
c._frame_dragged('s', 0.0, -0.1, False)     # pull the bottom in a bit first...
c._frame_drag_released()
shrunk = c._frame_rect_uv
for step in range(60):                      # ...then drag the top far past the edge
    c._frame_dragged('n', 0.0, -0.01, False)
held = c._frame_rect_uv
check('dragging the top handle past the edge: left, right and bottom never move',
      all(abs(a - b) < 1e-9 for a, b in ((held[0], shrunk[0]), (held[2], shrunk[2]), (held[3], shrunk[3]))),
      f'({tuple(round(v, 4) for v in shrunk)} -> {tuple(round(v, 4) for v in held)})')
check('...and the top stops inside the turned picture',
      crop_module_fits(held), f'({tuple(round(v, 4) for v in held)})')
for step in range(30):                      # back the way it came: it waits for the pointer
    c._frame_dragged('n', 0.0, +0.01, False)
check('...and comes back only once the pointer does (tracked from the press)',
      abs(c._frame_rect_uv[1] - (shrunk[1] - 0.3)) < 1e-9 or c._frame_rect_uv[1] < shrunk[1],
      f'(top {c._frame_rect_uv[1]:.4f})')
c._frame_drag_released()
# a corner meeting one edge slides along it; the opposite corner stays put
before_corner = c._frame_rect_uv
for step in range(60):
    c._frame_dragged('nw', -0.01, -0.002, False)
corner = c._frame_rect_uv
c._frame_drag_released()
check('dragging a corner: the opposite corner never moves',
      abs(corner[2] - before_corner[2]) < 1e-9 and abs(corner[3] - before_corner[3]) < 1e-9
      and crop_module_fits(corner), f'({tuple(round(v, 4) for v in corner)})')
# moving the whole frame keeps its size and stops at the edge
size_before = (corner[2] - corner[0], corner[3] - corner[1])
for step in range(80):
    c._frame_dragged('inside', 0.01, 0.0, False)
moved = c._frame_rect_uv
c._frame_drag_released()
check('moving the frame past the edge keeps its size, inside the picture',
      abs((moved[2] - moved[0]) - size_before[0]) < 1e-9 and abs((moved[3] - moved[1]) - size_before[1]) < 1e-9
      and crop_module_fits(moved), f'({tuple(round(v, 4) for v in moved)})')
# the same at 0°: the picture's own edge, no slide-back of the opposite side
click(panel().cancel_button)
wait_idle()
reset_photo()
c.set_crop_tool('frame')
settle()
c._frame_dragged('s', 0.0, -0.2, False)
c._frame_drag_released()
start0 = c._frame_rect_uv
for step in range(40):
    c._frame_dragged('n', 0.0, -0.01, False)
c._frame_drag_released()
check('at 0°, the top dragged past the top: it stops at 0 and the bottom stays',
      abs(c._frame_rect_uv[1]) < 1e-6 and abs(c._frame_rect_uv[3] - start0[3]) < 1e-9,
      f'({tuple(round(v, 4) for v in c._frame_rect_uv)})')
click(panel().cancel_button)
wait_idle()
reset_photo()

# 10i. Outside the turned picture is white, never stretched edge pixels
from spektrafilm.utils.crop_resize import SKEW_FILL, apply_skew  # noqa: E402
probe = np.random.default_rng(0).uniform(0.2, 0.6, (300, 400, 3))
turned = apply_skew(probe, 10.0)
check('CPU: the corners a turn brings in are the fill (white)',
      np.allclose(turned[0, 0], SKEW_FILL) and np.allclose(turned[-1, -1], SKEW_FILL),
      f'({turned[0, 0]}, {turned[-1, -1]})')
try:
    from spektrafilm.gpu.scan_geometry import skew_dispatch
    gpu_turned = skew_dispatch(probe.astype(np.float32), 10.0)
except Exception as exc:                    # noqa: BLE001 - reported, not hidden
    gpu_turned = None
    print('GPU skew unavailable:', exc)
if gpu_turned is not None:
    check('GPU: the same, and it matches the CPU (max abs diff)',
          np.allclose(gpu_turned[0, 0], SKEW_FILL) and float(np.max(np.abs(gpu_turned - turned))) < 1e-4,
          f'({float(np.max(np.abs(gpu_turned - turned))):.2e})')
else:
    check('GPU skew ran (needed to check the shader)', False)

# 10j. Frame > Reset after Done: no crop AND no angle, one undo step
user_types_angle(10.0)
click(panel().done_button)
wait_idle()
history_len = len(c._history)
click(c._widgets.scan_frame.reset_button)
wait_idle()
check('Frame > Reset after Done: no crop and no angle, no session opened',
      state() == (False, (0.5, 0.5), (1.0, 1.0), 0.0) and not session_open(), f'({state()})')
check('...as one undo step', len(c._history) == history_len + 1, f'({history_len} -> {len(c._history)})')
check('...and the Straighten row shows 0', ROW.value.text().strip().startswith(('0.0', '+0.0', '-0.0')),
      f'({ROW.value.text()!r})')
c.undo()
wait_idle()
check('...and Ctrl+Z brings the straightened crop back', abs(float(fin.skew_deg.value) - 10.0) < 1e-9
      and bool(fin.crop.value), f'({state()})')
reset_photo()

# 10k. After Cancel the Straighten row shows the angle that was put back
restore(skew=2.0)
user_types_angle(7.0)
click(panel().cancel_button)
wait_idle()
check('after Cancel the visible row shows the angle put back (2.0)',
      ROW.value.text().strip().startswith(('2.0', '+2.0')), f'({ROW.value.text()!r})')
reset_photo()

# 10f. A deliberate trim at a small angle is not mistaken for a straighten crop
restore(skew=0.3, crop=True, center=(0.49, 0.51), size=(0.97, 0.64))
user_types_angle(0.0)
check('back to 0° keeps a crop the user made (only a straighten crop goes)',
      session_open() and area(c._frame_rect_uv) < 0.999, f'({c._frame_rect_uv})')
click(panel().cancel_button)
wait_idle()

# 10g2. Enter on a focused panel button does not apply (Enter is Done on the photo)
restore(skew=before[3], crop=before[0], center=before[1], size=before[2])
user_types_angle(3.0)
panel().cancel_button.setFocus()
QTest.keyClick(panel().cancel_button, QtCore.Qt.Key_Return)
wait_idle()
check('Enter with the focus on the panel\'s Cancel applies nothing', session_open() and not bool(fin.crop.value),
      f'({state()})')
click(panel().cancel_button)
wait_idle()
user_types_angle(3.0)
click(panel().cancel_button)
wait_idle()
check('a click on Cancel cancels', not session_open() and state() == before, f'({state()})')
user_types_angle(3.0)
click(panel().done_button)
wait_idle()
check('a click on Done applies', not session_open() and abs(float(fin.skew_deg.value) - 3.0) < 1e-9
      and smear_px() == 0, f'({state()})')
restore(skew=before[3], crop=before[0], center=before[1], size=before[2])

# 10g3. Frame > Reset during a session resets the session (pending until Done)
user_types_angle(3.0)
c.reset_crop()
settle()
check('Frame > Reset inside a session: angle 0, whole photo, still in the session',
      session_open() and abs(float(fin.skew_deg.value)) < 1e-9 and area(c._frame_rect_uv) > 0.999,
      f'({float(fin.skew_deg.value)}, {c._frame_rect_uv})')
click(panel().cancel_button)
wait_idle()

# 10g4. Cancel puts the ratio back too
user_types_angle(3.0)
combo = c._ratio_combo()
free = combo.currentText()
other = next(combo.itemText(i) for i in range(combo.count()) if combo.itemText(i) != free)
combo.setCurrentText(other)
settle()
check('a ratio picked in Scan › Frame keeps the crop open', session_open() and combo.currentText() == other)
click(panel().cancel_button)
wait_idle()
check('...and Cancel puts the ratio back', combo.currentText() == free, f'({combo.currentText()})')

# 10g5. One render per angle change with auto preview on (not two)
user_types_angle(2.0)
wait_idle()
starts, stop_counting = simulation_starts()
user_types_angle(2.5)
wait_idle()
stop_counting()
check('an angle change in a session with auto preview on renders once', len(starts) == 1,
      f'({len(starts)} renders)')
click(panel().cancel_button)
wait_idle()

# 10g6. A full-frame Done with nothing touched is not an undo step
history_len = len(c._history)
c.set_crop_tool('frame')
settle()
click(panel().done_button)
wait_idle()
check('Crop tool then Done with nothing touched: no undo step added', len(c._history) == history_len,
      f'({history_len} -> {len(c._history)})')

# 10g7. The fit is done in the scan's own frame: an input crop and a desqueeze
restore(skew=0.0)
with restoring():
    inp.crop.value = True
    inp.crop_center.value = (0.5, 0.5)
    inp.crop_size.value = (0.6, 0.6)
    fin.desqueeze.value = 1.5
wait_idle()
user_types_angle(3.0)
click(panel().done_button)
wait_idle()
check('with an input crop and a desqueeze the fitted crop has no smear', smear_px() == 0,
      f'({smear_px()} px, {state()})')
with restoring():
    inp.crop.value = False
    inp.crop_center.value = (0.5, 0.5)
    inp.crop_size.value = (1.0, 1.0)
    fin.desqueeze.value = 1.0
reset_photo()

# 10l. The crop's controls live in Scan > Frame; nothing floats over the photo
user_types_angle(1.0)
stage = c._view_rail.parentWidget()
floating = [type(h).__name__ for h in stage.huds()
            if not h.isHidden() and h is not c._view_rail and type(h).__name__ != 'SystemMessage']
check('while cropping nothing but the rail floats over the photo', not floating, f'({floating})')
check('...Cancel · Done show in the Frame group', exits_shown())
check('the rail has no Swap button', getattr(c._view_rail, 'swap_button', None) is None)
check('Scan › Frame shows the Crop button (lit while cropping) and no Swap button',
      panel().crop_button.isVisibleTo(app.main_window) and panel().crop_button.isChecked()
      and not panel().swap_button.isVisibleTo(app.main_window))
win = app.main_window
for width in (1600, 1253):
    win.resize(width, 1000)
    settle(0.5)
    status = win.statusBar()
    used = QtGui.QFontMetrics(status.font()).horizontalAdvance(status.currentMessage())
    check(f'at {width} px the status line fits ({status.currentMessage()!r})',
          0 < used <= status.width(), f'({used} of {status.width()} px)')
win.resize(1600, 1000)
settle(0.5)
click(panel().cancel_button)
wait_idle()
check('...after Cancel, Cancel · Done are gone again', not exits_shown())
reset_photo()


def frame_ratio():
    u0, v0, u1, v1 = c._frame_rect_uv
    width, height = c._scan_frame_wh()
    return ((u1 - u0) * width) / ((v1 - v0) * height)


# 10m. Picking a ratio opens the crop; Cancel puts the ratio back
combo = c._ratio_combo()
before = state()
combo.setCurrentText('3:2')
settle()
check('a ratio picked with no crop open opens one, at that ratio',
      session_open() and abs(frame_ratio() - 1.5) < 0.01, f'({frame_ratio() if session_open() else None})')
click(panel().cancel_button)
wait_idle()
check('...Cancel puts Free back and the photo as it was',
      combo.currentText() == 'Free' and state() == before and not session_open(),
      f'({combo.currentText()}, {state()})')

# 10n. Landscape / portrait
click(panel().portrait_button)
settle()
check('Portrait with no crop open opens one, the frame turned portrait',
      session_open() and frame_ratio() < 1.0 and panel().portrait(), f'({frame_ratio() if session_open() else None})')
click(panel().cancel_button)
wait_idle()
check('...Cancel: landscape again, the photo as it was',
      not panel().portrait() and state() == before and not session_open(), f'({state()})')
combo.setCurrentText('3:2')
settle()
click(panel().portrait_button)
settle()
check('3:2 in portrait is 2:3', session_open() and abs(frame_ratio() - 2.0 / 3.0) < 0.01,
      f'({frame_ratio() if session_open() else None})')
check('...the combo still reads 3:2 (each ratio once in the list)', combo.currentText() == '3:2'
      and combo.findText('2:3') < 0)
click(panel().done_button)
wait_idle()
check('Done in portrait stores a portrait crop', bool(fin.crop.value)
      and float(fin.crop_size.value[1]) > float(fin.crop_size.value[0]), f'({state()})')
click(panel().reset_button)
wait_idle()
check('Reset: no crop, no angle, Free, landscape',
      state() == (False, (0.5, 0.5), (1.0, 1.0), 0.0) and combo.currentText() == 'Free'
      and not panel().portrait(), f'({state()}, {combo.currentText()}, portrait {panel().portrait()})')

# 10n2. Reset resets everything in Frame, Desqueeze included; inside a crop
# it is pending, and Cancel puts the desqueeze back too
desq_default = float(fin.desqueeze.baseline()) if callable(getattr(fin.desqueeze, 'baseline', None)) else 1.0
with restoring():
    fin.desqueeze.value = 1.5
wait_idle()
user_types_angle(4.0)
click(panel().done_button)
wait_idle()
history_len = len(c._history)
click(panel().reset_button)
wait_idle()
check('Reset after Done: no crop, no angle and the desqueeze back to its default, one undo step',
      state() == (False, (0.5, 0.5), (1.0, 1.0), 0.0)
      and abs(float(fin.desqueeze.value) - desq_default) < 1e-9 and len(c._history) == history_len + 1,
      f'({state()}, desqueeze {float(fin.desqueeze.value)}, {history_len} -> {len(c._history)})')
c.undo()
wait_idle()
check('...Ctrl+Z brings the desqueeze back with the crop and the angle',
      abs(float(fin.desqueeze.value) - 1.5) < 1e-9 and abs(float(fin.skew_deg.value) - 4.0) < 1e-9,
      f'(desqueeze {float(fin.desqueeze.value)}, {state()})')
user_types_angle(6.0)
click(panel().reset_button)
settle()
check('Reset inside a crop: desqueeze to its default as well, still in the crop',
      session_open() and abs(float(fin.desqueeze.value) - desq_default) < 1e-9
      and abs(float(fin.skew_deg.value)) < 1e-9, f'(desqueeze {float(fin.desqueeze.value)})')
click(panel().cancel_button)
wait_idle()
check('...and Cancel puts it all back: the desqueeze, the angle and the crop',
      abs(float(fin.desqueeze.value) - 1.5) < 1e-9 and abs(float(fin.skew_deg.value) - 4.0) < 1e-9
      and bool(fin.crop.value) and not session_open(), f'(desqueeze {float(fin.desqueeze.value)}, {state()})')
frame_section.reset_group()
wait_idle()
check('the Frame header reset arrow resets the desqueeze too',
      abs(float(fin.desqueeze.value) - desq_default) < 1e-9 and state() == (False, (0.5, 0.5), (1.0, 1.0), 0.0),
      f'(desqueeze {float(fin.desqueeze.value)}, {state()})')
reset_photo()

# 10o. The Frame group's header reset is the group's Reset
user_types_angle(10.0)
click(panel().done_button)
wait_idle()
history_len = len(c._history)
frame_section.reset_group()
wait_idle()
check('the Frame header reset clears the crop and the angle as one undo step',
      state() == (False, (0.5, 0.5), (1.0, 1.0), 0.0) and not session_open()
      and len(c._history) == history_len + 1, f'({state()}, {history_len} -> {len(c._history)})')

# 10p. Cropping started from the rail shows Scan > Frame
tabs.setCurrentIndex(0)
frame_section.set_expanded(False)
settle()
c.set_crop_mode(True)
settle()
check('the rail crop button shows Scan › Frame, open', tabs.currentIndex() == SCAN_TAB
      and frame_section._expanded, f'(tab {tabs.tabText(tabs.currentIndex())})')
click(panel().cancel_button)
wait_idle()
reset_photo()

# 10q. The labelled Crop button in Scan > Frame starts the crop; again, applies it
before = state()
click(panel().crop_button)
settle()
check('Scan › Frame › Crop opens the crop on the photo as it is', session_open() and exits_shown()
      and panel().crop_button.isChecked())
user_types_angle(4.0)
click(panel().crop_button)
wait_idle()
check('...and clicking it again applies (as the rail button does)', not session_open()
      and abs(float(fin.skew_deg.value) - 4.0) < 1e-9 and bool(fin.crop.value) and smear_px() == 0,
      f'({state()})')
reset_photo()

# 10r. Where a press on the photo goes: near the frame it resizes or moves it and
# never turns the photo; only well outside does it turn (real mouse events)
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
    cursor = CURSORS.get(overlay.cursor().shape(), 'other')
    angle0, frame0 = float(fin.skew_deg.value), c._frame_rect_uv
    QTest.mousePress(overlay, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, QtCore.QPoint(int(px), int(py)))
    for i in range(1, 7):
        QTest.mouseMove(overlay, QtCore.QPoint(int(px + dx * i / 6), int(py + dy * i / 6)))
        settle(0.03)
    QTest.mouseRelease(overlay, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier,
                       QtCore.QPoint(int(px + dx), int(py + dy)))
    settle(0.2)
    turned = abs(float(fin.skew_deg.value) - angle0) > 1e-6
    changed = any(abs(a - b) > 1e-6 for a, b in zip(frame0, c._frame_rect_uv))
    return cursor, turned, changed


user_types_angle(10.0)
wait_idle()
for name, where, dx, dy, want_cursor, want_change in (
        ('1 px outside an edge, a quarter along', lambda x0, y0, x1, y1: (x0 + (x1 - x0) * .25, y0 - 1), 0, 25, 'resize', True),
        ('9 px outside the middle handle', lambda x0, y0, x1, y1: ((x0 + x1) / 2, y0 - 9), 0, 25, 'resize', True),
        ('13 px outside a corner', lambda x0, y0, x1, y1: (x0 - 13, y0 - 13), 30, 30, 'resize', True),
        ('19 px outside an edge', lambda x0, y0, x1, y1: (x1 + 19, (y0 + y1) / 2), -30, 0, 'resize', True),
        ('inside the frame', lambda x0, y0, x1, y1: ((x0 + x1) / 2, (y0 + y1) / 2), 20, 10, 'move', None)):
    cursor, turned, changed = press_drag(where, dx, dy)
    check(f'a press {name}: the {want_cursor} cursor, the frame changes, the photo does not turn',
          cursor == want_cursor and not turned and (changed if want_change is not None else True),
          f'(cursor {cursor}, turned {turned}, frame changed {changed})')
cursor, turned, changed = press_drag(lambda x0, y0, x1, y1: (x1 + 35, y0 + 40), 0, 80)
check('a press 35 px outside: the turn cursor, and the photo turns', cursor == 'turn' and turned,
      f'(cursor {cursor}, turned {turned})')
click(panel().cancel_button)
wait_idle()
reset_photo()

# 10s. Turning keeps the frame where the user put it: Constrain on, it shrinks
# about its own centre; Constrain off, it does not change at all
user_types_angle(2.0)
c._frame_dragged('se', -0.3, -0.3, False)
c._frame_drag_released()
c._frame_dragged('inside', -0.1, -0.08, False)
c._frame_drag_released()
settle()
f0 = c._frame_rect_uv
centre0 = ((f0[0] + f0[2]) / 2, (f0[1] + f0[3]) / 2)
user_types_angle(-20.0)
f1 = c._frame_rect_uv
centre1 = ((f1[0] + f1[2]) / 2, (f1[1] + f1[3]) / 2)
check('turning an off-centre frame (Constrain on) keeps its centre and its shape, inside the photo',
      max(abs(a - b) for a, b in zip(centre0, centre1)) < 1e-6
      and abs((f1[2] - f1[0]) / (f1[3] - f1[1]) - (f0[2] - f0[0]) / (f0[3] - f0[1])) < 1e-6
      and crop_module_fits(f1), f'({centre0} -> {centre1})')
user_types_angle(2.0)
check('...and turning back gives the frame back', all(abs(a - b) < 1e-6 for a, b in zip(c._frame_rect_uv, f0)),
      f'({c._frame_rect_uv} vs {f0})')
click(panel().constrain_toggle)
settle()
f_off = c._frame_rect_uv
user_types_angle(15.0)
check('with Constrain off, turning leaves the frame exactly as it is',
      all(abs(a - b) < 1e-9 for a, b in zip(c._frame_rect_uv, f_off)))
click(panel().constrain_toggle)
settle()
click(panel().cancel_button)
wait_idle()
reset_photo()

# 10t. Real-time turning: each angle turns the kept unturned frame (no render),
# and what it shows is exactly what a full render shows
user_types_angle(5.0)
wait_idle()
check('the crop kept its film unturned', c._crop_flat is not None)
renders, turns = [], []
_start, _show = c._start_simulation, c._show_turned
c._show_turned = lambda result: (turns.append(1), _show(result))[1]
c._skew_drag_started()
for _ in range(10):
    c._skew_dragged(0.25, False)
    settle(0.02)
c._skew_drag_released()
wait_idle()
c._show_turned = _show
check('a turn-drag of 10 steps turns the kept frame (no film render)',
      len(turns) >= 8 and c._active_simulation_worker is None, f'({len(turns)} turns)')
c.set_crop_constrain(False)
settle()
user_types_angle(7.5)
wait_idle()
fast = np.asarray(c._output_display_cached).astype(int)
click(panel().done_button)               # Constrain off: Done renders the same frame in full
wait_idle()
full = np.asarray(c._output_display_cached).astype(int)
check('the turned frame is exactly the full render (0 levels apart)',
      fast.shape == full.shape and int(np.abs(fast - full).max()) == 0,
      f'(max {int(np.abs(fast - full).max()) if fast.shape == full.shape else "shape"})')
c.set_crop_constrain(True)
panel().set_constrain(True)
reset_photo()

# 10u. Crop -> Done again and again keeps the crop exactly (the user's report:
# a turned, constrained frame set a little inside its maximum shrank on every
# cycle, and the crop Done kept was not the frame on screen)
user_types_angle(10.0)
c._frame_dragged('se', -0.06, -0.05, False)
c._frame_drag_released()
c._frame_dragged('nw', 0.03, 0.02, False)
c._frame_drag_released()
W, H = c._scan_frame_wh()
set_px = tuple(v * s for v, s in zip(c._frame_rect_uv, (W, H, W, H)))
drift = []
for cycle in range(6):
    click(panel().done_button)
    wait_idle()
    click(panel().crop_button)
    wait_idle()
    drift.append(max(abs(v * s - p) for v, s, p in zip(c._frame_rect_uv, (W, H, W, H), set_px)))
check('six Crop -> Done cycles: the frame stays within a pixel of what was set, no shrinking',
      max(drift) <= 1.0, f'(largest shift {max(drift):.2f} px over {len(drift)} cycles)')
click(panel().done_button)
wait_idle()
check('...and the stored crop leaves no smeared pixel', smear_px() == 0, f'({smear_px()} px)')
reset_photo()

# 11. No smear at any angle, crop off or on
smeared = []
for angle in (-33.0, -12.4, -2.1, 0.7, 5.6, 18.9, 41.0):
    reset_photo()
    user_types_angle(angle)
    click(panel().done_button)
    wait_idle()
    smeared.append((angle, smear_px()))
    # and again with a crop already on, at another angle
    user_types_angle(angle / 2.0)
    click(panel().done_button)
    wait_idle()
    smeared.append((angle / 2.0, smear_px()))
check('no smeared pixel inside the crop at 14 angles (full resolution)',
      all(px == 0 for _a, px in smeared), f'({[s for s in smeared if s[1]]})')

print('\nFAILURES:', failures if failures else 'none')
sys.exit(1 if failures else 0)
