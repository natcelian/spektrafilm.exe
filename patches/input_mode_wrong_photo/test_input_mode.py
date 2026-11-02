"""Checks that sorting (Input mode) never acts on the wrong frame, headless,
through the real GUI controller.

usage: python test_input_mode.py <src dir> <out dir> <raw A> <raw B>
Raws A and B must be different pictures. Exits non-zero on any failure
(2.2.1 fails: a single export after browsing writes A's pixels under B's
name, and browsing A -> B -> A copies A's look onto B).
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

import numpy as np  # noqa: E402
from qtpy import QtWidgets  # noqa: E402

import spektrafilm_gui  # noqa: E402
import spektrafilm_gui.app as app_module  # noqa: E402
import spektrafilm_gui.controller as controller_module  # noqa: E402
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
        time.sleep(0.05)


settle(1)
if app_module._background_warmup_pool is not None:
    app_module._background_warmup_pool.waitForDone(300000)


def wait_idle(timeout=180):
    start = time.time()
    deadline = start + timeout
    while time.time() < deadline and c._active_simulation_worker is not None:
        settle(0.2)
    settle(0.5)
    if os.environ.get('TEST_VERBOSE'):
        print(f'  (waited {time.time() - start:.1f} s; worker {"STILL RUNNING" if c._active_simulation_worker else "idle"};'
              f' label {c._active_simulation_label!r})')


# --- export capture: the request a single export would hand its worker
captured = {}


class CaptureExport:
    def __init__(self, request, render_fn=None, write_fn=None):
        captured['request'] = request

        class _Signal:
            def connect(self, *_args):
                pass

        self.signals = type('S', (), {'progress': _Signal(), 'finished': _Signal(), 'failed': _Signal()})()


class CaptureBatch(CaptureExport):
    def __init__(self, jobs, render_fn=None, write_fn=None):
        captured['jobs'] = jobs
        super().__init__(None)


class SaveTo:
    def __init__(self, *_args, **_kwargs):
        pass

    def get_save_file_name(self, *_args, **_kwargs):
        return os.path.join(OUT, 'export.jpg'), ''


controller_module.ExportWorker = CaptureExport
controller_module.runtime.BatchExportWorker = CaptureBatch
controller_module._DirMemoryDialog = SaveTo
controller_module.QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: OUT)
_real_start = c._thread_pool.start
# Swallow only the captured export jobs; renders (Preview) must really run.
c._thread_pool.start = lambda worker: (None if isinstance(worker, CaptureExport) else _real_start(worker))


def finish_export():
    c._active_export_worker = None
    c._set_export_controls_enabled(True)


def fingerprint(image):
    arr = np.asarray(image)
    return arr.shape, round(float(arr.mean()), 4)


# Reference pixels: each file loaded on its own
reference = {}
for path in (A, B):
    c.load_raw_image(path)
    wait_idle()
    reference[os.path.normcase(os.path.abspath(path))] = fingerprint(
        c._simulation_input_image(source_layer_name=controller_module.INPUT_LAYER_NAME))
print('reference pixels:', {os.path.basename(k): v for k, v in reference.items()})

# A roll of A and B, each with its own look
c.roll_add_files([A, B])
wait_idle()
a_file, b_file = c._roll.files
base = collect_gui_state(widgets=c._widgets)
look_a, look_b = copy.deepcopy(base), copy.deepcopy(base)
look_a.simulation.exposure_compensation_ev = 1.5
look_b.simulation.exposure_compensation_ev = -2.0


def ev(roll_file):
    state = c._roll.state_for(roll_file)
    return None if state is None else state.simulation.exposure_compensation_ev


def panels_ev():
    return collect_gui_state(widgets=c._widgets).simulation.exposure_compensation_ev


def start_on_a_then_sort():
    """Edit mode on A with A's look in the panels, then sort, as Add frames... does."""
    c._roll._states[a_file.path] = copy.deepcopy(look_a)
    c._roll._states[b_file.path] = copy.deepcopy(look_b)
    c.set_input_mode(False)
    c._roll.select(0)
    c._roll_load_current(restore_state=True)
    wait_idle()
    c.set_input_mode(True)
    settle(1)


# 1. Browsing in Input mode keeps every frame's own look
start_on_a_then_sort()
c.roll_activate(1)
settle(2)
c.roll_activate(0)
settle(2)
c.roll_activate(1)
settle(2)
check('browsing A -> B -> A -> B in Input mode keeps B\'s own look', ev(b_file) == -2.0, f'(B ev {ev(b_file)})')
check('...and A\'s', ev(a_file) == 1.5, f'(A ev {ev(a_file)})')
c.autosave_roll_now()
check('an autosave while sorting does not store the panels onto B', ev(b_file) == -2.0, f'(B ev {ev(b_file)})')

# 2. A single export after browsing: B's pixels, B's name, B's look
captured.clear()
c.export_output()
request = captured.get('request')
finish_export()
check('the export runs', request is not None)
if request is not None:
    b_key = os.path.normcase(os.path.abspath(B))
    check('a single export after browsing to B renders B\'s pixels',
          request.image is not None and fingerprint(request.image) == reference[b_key],
          f'({fingerprint(request.image) if request.image is not None else None} vs B {reference[b_key]})')
    check('...with B\'s look', panels_ev() == -2.0, f'(panels ev {panels_ev()})')
    check('...and it left sorting for edit mode, as picking Print does', not c._input_mode)
wait_idle()

# 2b. Preview after browsing (the reported Preview case): B, with B's look,
# and switching away afterwards stores B's look on B, not A's
start_on_a_then_sort()
c.roll_activate(1)
settle(2)
c.run_preview()
wait_idle()
b_key = os.path.normcase(os.path.abspath(B))
pixels = c._simulation_input_image(source_layer_name=controller_module.INPUT_LAYER_NAME)
check('Preview after browsing to B renders B', pixels is not None and fingerprint(pixels) == reference[b_key],
      f'({fingerprint(pixels) if pixels is not None else None} vs B {reference[b_key]})')
check('...with B\'s look', panels_ev() == -2.0, f'(panels ev {panels_ev()})')
c.roll_activate(0)
wait_idle()
check('...and switching to A afterwards leaves B\'s look as it was', ev(b_file) == -2.0, f'(B ev {ev(b_file)})')

# 3. Roll export while sorting: every frame with its own look
start_on_a_then_sort()
c.roll_activate(1)
settle(2)
captured.clear()
c.export_roll()
finish_export()
jobs = {os.path.normcase(job.path): job for job in captured.get('jobs', [])}
check('roll export while sorting gives B its own look',
      jobs.get(b_file.path) is not None and jobs[b_file.path].state.simulation.exposure_compensation_ev == -2.0,
      f'({ {os.path.basename(k): j.state.simulation.exposure_compensation_ev for k, j in jobs.items()} })')
check('...and A its own', jobs.get(a_file.path) is not None
      and jobs[a_file.path].state.simulation.exposure_compensation_ev == 1.5)

# 4. Sorting without moving: export stays in sorting and uses A (nothing stale)
start_on_a_then_sort()
captured.clear()
c.export_output()
request = captured.get('request')
finish_export()
a_key = os.path.normcase(os.path.abspath(A))
check('export while sorting on the loaded frame renders it and stays in sorting',
      request is not None and fingerprint(request.image) == reference[a_key] and c._input_mode,
      f'(input mode {c._input_mode})')

# 5. Regression: in edit mode, leaving a frame still stores its edits
c.set_input_mode(False)
c._roll._states[a_file.path] = copy.deepcopy(look_a)
c._roll._states[b_file.path] = copy.deepcopy(look_b)
c._roll.select(0)
c._roll_load_current(restore_state=True)
wait_idle()
edited = collect_gui_state(widgets=c._widgets)
edited.simulation.exposure_compensation_ev = 0.7
apply_gui_state(edited, widgets=c._widgets)
c.roll_activate(1)
wait_idle()
check('in edit mode, switching frames still stores the edit on the frame left', ev(a_file) == 0.7,
      f'(A ev {ev(a_file)})')
check('...and shows the next frame\'s own look', panels_ev() == -2.0, f'(panels ev {panels_ev()})')

# 6. While sorting: the panels show the selected frame's look, nothing renders
start_on_a_then_sort()
renders = []
_real_start_simulation = c._start_simulation


def counting_start_simulation(**kwargs):
    renders.append(kwargs.get('mode_label'))
    return _real_start_simulation(**kwargs)


c._start_simulation = counting_start_simulation
c.roll_activate(1)
settle(2)
check('sorting onto B shows B\'s look in the panels (what Copy settings copies)', panels_ev() == -2.0,
      f'(panels ev {panels_ev()})')
check('...and look_of_frame(B) is B\'s', c.look_of_frame(1).simulation.exposure_compensation_ev == -2.0)
check('nothing renders while sorting', not renders and c._active_simulation_worker is None and c._input_mode,
      f'(renders {renders})')

# 7. A version is never recorded on B with A's look while browsing
attempts_before = len(c._roll.attempts_for(b_file))
c.roll_activate(0)
settle(2)
c.roll_activate(1)
settle(2)
b_attempt_evs = [a.look.get('simulation', {}).get('exposure_compensation_ev') for a in c._roll.attempts_for(b_file)]
check('browsing records no version on B carrying A\'s look', 1.5 not in b_attempt_evs,
      f'(B versions ev {b_attempt_evs}, before {attempts_before})')

# 8. Keep while sorting on B does not pin A's print as B's kept print
c.attempt_keep()
check('Keep while sorting pins no earlier-frame print as B\'s', c._kept_image() is None)

# 9. Paste while sorting lands on B, survives leaving sorting, renders nothing
pasted = copy.deepcopy(look_b)
pasted.simulation.exposure_compensation_ev = 0.3
c._look_clipboard = pasted
c._look_clipboard_fields = ('simulation.exposure_compensation_ev',)
renders.clear()
c.paste_look()
settle(2)
check('a paste while sorting changes B\'s look and renders nothing', panels_ev() == 0.3 and not renders
      and c._input_mode, f'(panels ev {panels_ev()}, renders {renders})')

# 10. Preview from sorting: one render, of B, with the pasted look kept
c.run_preview()
wait_idle()
settle(1)
wait_idle()
check('Preview from sorting renders once (no queued auto preview after it)', renders == ['Preview'],
      f'(renders {renders})')
check('...and keeps the look pasted while sorting', panels_ev() == 0.3 and ev(b_file) == 0.3,
      f'(panels {panels_ev()}, stored {ev(b_file)})')
c._start_simulation = _real_start_simulation

# 10b. Paste while sorting, then leave with Print: the paste is kept
start_on_a_then_sort()
c.roll_activate(1)
settle(2)
c._look_clipboard = pasted
c._look_clipboard_fields = ('simulation.exposure_compensation_ev',)
c.paste_look()
settle(1)
c.set_input_mode(False)          # picking Print
wait_idle()
check('a paste while sorting survives picking Print', panels_ev() == 0.3 and ev(b_file) == 0.3,
      f'(panels {panels_ev()}, stored {ev(b_file)})')
wait_idle()

# 10c. Paste while sorting WITHOUT moving, then Print: the skipped render is paid
start_on_a_then_sort()
c._look_clipboard = pasted
c._look_clipboard_fields = ('simulation.exposure_compensation_ev',)
c.paste_look()
settle(1)
renders.clear()
c._start_simulation = counting_start_simulation
c.set_input_mode(False)          # picking Print, still on A
wait_idle()
settle(1)
wait_idle()
c._start_simulation = _real_start_simulation
check('a paste while sorting on the loaded frame renders when leaving sorting', bool(renders),
      f'(renders {renders})')

# 11. Adding another batch while sorting must not forget the frame is not loaded
start_on_a_then_sort()
c.roll_activate(1)
settle(2)
c.set_input_mode(True)          # what dropping more files while sorting does
captured.clear()
c.export_output()
request = captured.get('request')
finish_export()
check('after adding a batch while sorting, export still renders B',
      request is not None and fingerprint(request.image) == reference[os.path.normcase(os.path.abspath(B))])
wait_idle()

# 11b. A thumbnail click must not render or leave sorting, even when auto
# preview is dormant (the scan-film view switch called _run_preview directly)
start_on_a_then_sort()
look_b_scan = copy.deepcopy(look_b)
look_b_scan.simulation.scan_film = not look_a.simulation.scan_film
c._roll._states[b_file.path] = look_b_scan
renders.clear()
c._start_simulation = counting_start_simulation
first_done = c._first_render_done
c._first_render_done = False          # auto preview dormant, as before the first render
c.roll_activate(1)
settle(2)
c._first_render_done = first_done
check('a click onto a frame with another scan-film view stays in sorting, no render',
      c._input_mode and not renders and c._active_simulation_worker is None, f'(renders {renders})')
c._roll._states[b_file.path] = copy.deepcopy(look_b)

# 11c. Loupe from sorting still gets its render (no swallowed preview)
c.roll_activate(0)
settle(1)
c.roll_activate(1)
settle(1)
renders.clear()
c.toggle_loupe()
wait_idle()
settle(1)
wait_idle()
check('Loupe from sorting opens the frame and it renders', not c._input_mode and bool(renders),
      f'(renders {renders})')
c._start_simulation = _real_start_simulation

# 11d. Browsing A -> B -> A needs no reload: A is still the loaded frame
start_on_a_then_sort()
c.roll_activate(1)
settle(1)
c.roll_activate(0)
settle(1)
captured.clear()
c.export_output()
request = captured.get('request')
finish_export()
check('A -> B -> A then export renders A without leaving sorting',
      request is not None and fingerprint(request.image) == reference[os.path.normcase(os.path.abspath(A))]
      and c._input_mode, f'(input mode {c._input_mode})')

# 11e. Flagging B as reference while sorting drops the old pin, never shows another print as B's
start_on_a_then_sort()
c._reference_image = np.zeros((4, 4, 3), dtype=np.float32)   # an earlier pin
c.roll_activate(1)
settle(1)
c.toggle_reference_frame()
check('flagging the reference while sorting does not keep another frame\'s print', c._reference_image is None)
c.toggle_reference_frame()

# 11f. The white-balance eyedropper samples the selected frame's own pixels
c.arm_neutral_sample(True)
pixels = c._simulation_input_image(source_layer_name=controller_module.INPUT_LAYER_NAME)
check('arming the eyedropper while sorting loads the selected frame',
      pixels is not None and fingerprint(pixels) == reference[os.path.normcase(os.path.abspath(B))])
c.arm_neutral_sample(False)
wait_idle()

# 11g. Full screen (F11 / Ctrl+P) while sorting shows the selected frame
import spektrafilm_gui.controller_compare as compare_module  # noqa: E402
shown_fullscreen = []
_real_fullscreen = compare_module.show_fullscreen_image
compare_module.show_fullscreen_image = lambda image, parent=None: shown_fullscreen.append(np.asarray(image))
b_key = os.path.normcase(os.path.abspath(B))
start_on_a_then_sort()
c.roll_activate(1)
settle(1)
c.show_fullscreen_output()
wait_idle()
settle(1)
pixels = c._simulation_input_image(source_layer_name=controller_module.INPUT_LAYER_NAME)
check('F11 while sorting renders the selected frame and then goes full screen',
      len(shown_fullscreen) == 1 and pixels is not None and fingerprint(pixels) == reference[b_key],
      f'(full screen shown {len(shown_fullscreen)} time(s))')
compare_module.show_fullscreen_image = _real_fullscreen

# 11h. Before/after (Ctrl+R) while sorting opens the selected frame
start_on_a_then_sort()
c.roll_activate(1)
settle(1)
c.toggle_preview_view()
wait_idle()
pixels = c._simulation_input_image(source_layer_name=controller_module.INPUT_LAYER_NAME)
check('Ctrl+R while sorting opens and renders the selected frame',
      pixels is not None and fingerprint(pixels) == reference[b_key] and not c._input_mode)
wait_idle()

# 11i. Preview while sorting on the loaded frame leaves sorting properly
# (2.2.1: the finished render cleared the flag but left the panels hidden and locked)
locks = []
_real_lock = getattr(c, '_set_right_panel_locked', None)
if callable(_real_lock):
    def recording_lock(locked):
        locks.append(bool(locked))
        return _real_lock(locked)
    c._set_right_panel_locked = recording_lock
start_on_a_then_sort()
locks.clear()
c.run_preview()
wait_idle()
check('Preview while sorting on the loaded frame unlocks the edit panel (leaves sorting properly)',
      not c._input_mode and bool(locks) and locks[-1] is False, f'(lock calls {locks})')
if callable(_real_lock):
    c._set_right_panel_locked = _real_lock

# 12. A frame that cannot be decoded: no export of the earlier frame under its name
broken = os.path.join(OUT, 'broken.ARW')
with open(broken, 'wb') as target:     # not a raw at all (a truncated raw decodes, black)
    target.write(b'not a raw file ' * 20000)
c.roll_add_files([broken])
settle(1)
start_on_a_then_sort()
broken_index = next(i for i, f in enumerate(c._roll.files) if f.path == os.path.normcase(os.path.abspath(broken)))
c.roll_activate(broken_index)
settle(2)
captured.clear()
c.export_output()
finish_export()
check('an unreadable frame exports nothing (not the earlier frame\'s pixels)', 'request' not in captured)
captured.clear()
c.export_output()               # a second try, now out of sorting
finish_export()
check('...and a second try is refused too', 'request' not in captured)
settle(1)

print('\nFAILURES:', failures if failures else 'none')
sys.exit(1 if failures else 0)
