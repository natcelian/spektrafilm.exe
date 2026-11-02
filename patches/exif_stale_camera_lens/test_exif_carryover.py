"""Checks for the stale Camera/Lens Model EXIF fix, headless, through the real GUI controller.

usage: python test_exif_carryover.py <src dir> <out dir> <raw A> <raw B>
Raws A and B must come from different cameras. <out dir> receives the test
exports. Exits non-zero on any failure (2.2.1 fails the first checks: the
export of B carries A's camera and lens).
"""
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
from PIL import Image  # noqa: E402
from qtpy import QtWidgets  # noqa: E402

import spektrafilm_gui  # noqa: E402
import spektrafilm_gui.app as app_module  # noqa: E402
import spektrafilm_gui.controller as controller_module  # noqa: E402
from spektrafilm.utils.io import (read_camera_lens_model, read_image_metadata,  # noqa: E402
                                  save_image_oiio, write_image_metadata)
from spektrafilm_gui.app import create_app  # noqa: E402

assert os.path.samefile(os.path.dirname(spektrafilm_gui.__file__), os.path.join(SRC, 'spektrafilm_gui')), (
    f'imported {spektrafilm_gui.__file__}, not the GUI under {SRC}')

CAMERA, LENS = 'Exif.Image.Model', 'Exif.Photo.LensModel'
failures = []


def check(name, ok, detail=''):
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
    if not ok:
        failures.append(name)


qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
app = create_app()
controller = app.controller
panel = controller._widgets.metadata


def settle(seconds=0.3):
    deadline = time.time() + seconds
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.02)


# The start-up warm-up renders on its own pool; reading EXIF with exiv2 while
# it runs littleCMS can crash the process, so let it finish first.
deadline = time.time() + 30
while app_module._background_warmup_pool is None and time.time() < deadline:
    settle(0.05)
if app_module._background_warmup_pool is not None:
    app_module._background_warmup_pool.waitForDone(300000)


def exif_of(path):
    # Copy the values out now: an exiv2 datum dies with its ImageMetadata.
    values = {}
    for datum in read_image_metadata(path).exif:
        values.setdefault(datum.key(), str(datum.toString()))
    return lambda key: values.get(key, '')


def own(path):
    camera, lens = read_camera_lens_model(read_image_metadata(path))
    return camera or '', lens or ''


def shown():
    return panel.camera_model.text().strip(), panel.lens_model.text().strip()


def load(path, raw=True, timeout=180):
    before = getattr(controller, '_current_input_image', None)
    (controller.load_raw_image if raw else controller.load_input_image)(path)
    deadline = time.time() + timeout
    while time.time() < deadline:
        qapp.processEvents()
        image = getattr(controller, '_current_input_image', None)
        if image is not None and image is not before and controller._current_input_path \
                and os.path.normcase(controller._current_input_path) == os.path.normcase(path):
            settle()
            return
        time.sleep(0.05)
    raise TimeoutError(f'{path} did not load')


def export_single(source_path, name):
    """What a single-file export writes: the open file's EXIF plus the panel tags."""
    target = os.path.join(OUT, name)
    save_image_oiio(target, np.full((32, 48, 3), 0.5, dtype=np.float32), bit_depth=8)
    write_image_metadata(target, read_image_metadata(source_path),
                         custom_tags=controller._custom_metadata_tags())
    return exif_of(target)


print('A:', os.path.basename(A), own(A))
print('B:', os.path.basename(B), own(B))

# 1. The forum report: one camera's file, then another's, then export
load(A)
load(B)
check('after loading A then B, the metadata panel holds B\'s camera/lens', shown() == own(B),
      f'(panel {shown()}, B {own(B)})')
get, src = export_single(B, 'single_b.jpg'), exif_of(B)
check('the export of B carries B\'s own Model and LensModel, not A\'s',
      get(CAMERA) == src(CAMERA) and get(LENS) == src(LENS),
      f'(export {get(CAMERA)!r} / {get(LENS)!r}; B {src(CAMERA)!r} / {src(LENS)!r})')

# 2. A value typed into the panel is still kept, as its docstring promises
panel.lens_model.setText('Typed test lens')
load(A)
check('a typed lens is not overwritten by the next file', panel.lens_model.text() == 'Typed test lens',
      f'(panel lens {panel.lens_model.text()!r})')
check('...while the camera follows the file', shown()[0] == own(A)[0], f'(panel {shown()}, A {own(A)})')
panel.lens_model.setText('')
load(B)
check('cleared again, the lens follows the file', shown() == own(B), f'(panel {shown()}, B {own(B)})')

# 3. A file without EXIF must not inherit the previous file's camera/lens
png = os.path.join(OUT, 'no_exif.png')
Image.fromarray(np.full((40, 60, 3), 128, dtype=np.uint8)).save(png)
load(png, raw=False)
check('a file without EXIF empties Camera/Lens Model', shown() == ('', ''), f'(panel {shown()})')

# 3b. A single image's star rating stays with it (it is written as EXIF Rating)
load(A)
controller.set_current_rating(4)
check('a single image\'s rating is written into its export',
      controller._custom_metadata_tags().get('Exif.Image.Rating') == '4')
load(B)
check('the next single image does not inherit that rating',
      controller._current_rating() == 0 and 'Exif.Image.Rating' not in controller._custom_metadata_tags(),
      f'(rating {controller._current_rating()}, tags {controller._custom_metadata_tags()})')
frame_bar = getattr(controller, '_frame_bar', None)
if frame_bar is not None and hasattr(frame_bar, 'rating'):
    check('...and its stars show none', frame_bar.rating() == 0)
load(A)
check('reopening the rated image brings its rating back', controller._current_rating() == 4)
controller.set_current_rating(0)

# 4. Roll export: every frame keeps its own camera and lens
controller._roll.add_files([A, B])
load(B)
captured = {}


class CaptureWorker:
    def __init__(self, jobs, render_fn=None, write_fn=None):
        captured['jobs'] = jobs

        class _Signal:
            def connect(self, *_args):
                pass

        self.signals = type('S', (), {'progress': _Signal(), 'finished': _Signal(), 'failed': _Signal()})()


roll_out = os.path.join(OUT, 'roll')
os.makedirs(roll_out, exist_ok=True)
controller_module.QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: roll_out)
controller_module.runtime.BatchExportWorker = CaptureWorker
controller._thread_pool.start = lambda worker: None
controller.export_roll()
jobs = captured.get('jobs', [])
check('roll export built one job per frame', len(jobs) == 2, f'({len(jobs)} jobs)')
for job in jobs:
    written = controller._batch_write_frame(job, np.full((32, 48, 3), 0.5, dtype=np.float32), roll_out)
    get, src = exif_of(os.path.join(roll_out, written)), exif_of(job.path)
    check(f'roll export {written}: its own Make / Model / LensModel',
          get('Exif.Image.Make') == src('Exif.Image.Make') and get(CAMERA) == src(CAMERA)
          and get(LENS) == src(LENS),
          f'({get("Exif.Image.Make")!r} {get(CAMERA)!r} {get(LENS)!r}; source {src(CAMERA)!r} {src(LENS)!r})')

print('\nFAILURES:', failures if failures else 'none')
sys.exit(1 if failures else 0)
