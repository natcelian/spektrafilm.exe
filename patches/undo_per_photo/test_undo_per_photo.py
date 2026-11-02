"""Checks that Undo only ever undoes the open image's own edits, headless,
through the real GUI controller.

usage: python test_undo_per_photo.py <src dir> <out dir> <raw A> <raw B>
Exits non-zero on any failure (2.2.1 fails: Ctrl+Z right after switching from
A to B puts A's look on B).
"""
import copy
import os
import sys
import time

if len(sys.argv) < 5:
    sys.exit(__doc__)
SRC, OUT, A, B = sys.argv[1:5]
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.makedirs(OUT, exist_ok=True)
os.environ['SPEKTRAFILM_SETTINGS_INI'] = os.path.join(OUT, 'settings.ini')
sys.path.insert(0, SRC)

from qtpy import QtWidgets  # noqa: E402

import spektrafilm_gui  # noqa: E402
import spektrafilm_gui.app as app_module  # noqa: E402
from spektrafilm_gui.app import create_app  # noqa: E402
from spektrafilm_gui.state_bridge import apply_gui_state, collect_gui_state  # noqa: E402

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


def settle(seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.02)


def wait_idle(timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline and c._active_simulation_worker is not None:
        settle(0.2)
    settle(1.0)     # past the 700 ms history debounce


settle(1)
if app_module._background_warmup_pool is not None:
    app_module._background_warmup_pool.waitForDone(300000)


def ev():
    return collect_gui_state(widgets=c._widgets).simulation.film_exposure_ev


def edit_film_exposure(value):
    """What dragging the slider does: change the control, which records history."""
    state = collect_gui_state(widgets=c._widgets)
    state.simulation.film_exposure_ev = value
    apply_gui_state(state, widgets=c._widgets)
    c.request_auto_preview()
    wait_idle()


c.roll_add_files([A, B])
wait_idle()
a_file, b_file = c._roll.files
base = collect_gui_state(widgets=c._widgets)
look_b = copy.deepcopy(base)
look_b.simulation.film_exposure_ev = -2.0
c._roll._states[b_file.path] = look_b          # B has its own look already
c._roll_load_current(restore_state=True)
wait_idle()

# 1. Edit A twice
edit_film_exposure(0.5)
edit_film_exposure(1.0)
check('A shows its edit', ev() == 1.0, f'(ev {ev()})')

# 2. Switch to B, Ctrl+Z: nothing of A's lands on B
c.roll_activate(1)
wait_idle()
check('B opens with its own look', ev() == -2.0, f'(ev {ev()})')
c.undo()
wait_idle()
check('Ctrl+Z right after switching to B leaves B\'s look alone', ev() == -2.0, f'(ev {ev()})')

# 3. B's own edits still undo
edit_film_exposure(-1.5)
c.undo()
wait_idle()
check('an edit on B undoes back to B\'s look', ev() == -2.0, f'(ev {ev()})')
c.redo()
wait_idle()
check('...and redoes', ev() == -1.5, f'(ev {ev()})')

# 4. Back on A: A's own undo steps are still there
c.roll_activate(0)
wait_idle()
check('A opens with its edited look', ev() == 1.0, f'(ev {ev()})')
c.undo()
wait_idle()
check('Ctrl+Z on A undoes A\'s last edit (1.0 -> 0.5)', ev() == 0.5, f'(ev {ev()})')
c.undo()
wait_idle()
check('...and the one before (0.5 -> its start)', ev() == base.simulation.film_exposure_ev, f'(ev {ev()})')
c.redo()
wait_idle()
check('redo on A goes forward again', ev() == 0.5, f'(ev {ev()})')

# 5. Undo never crosses into B's look from A either
for _ in range(5):
    c.undo()
    wait_idle()
check('undoing all the way on A never reaches B\'s look', ev() != -2.0 and ev() != -1.5, f'(ev {ev()})')

print('\nFAILURES:', failures if failures else 'none')
sys.exit(1 if failures else 0)
