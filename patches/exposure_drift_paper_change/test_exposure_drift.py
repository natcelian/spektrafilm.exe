"""Checks for the Camera-exposure drift fix, headless, through the real GUI controller.

usage:
  python test_exposure_drift.py <src dir> [--dump states.json]
  python test_exposure_drift.py --compare original.json patched.json

<src dir> holds the spektrafilm and spektrafilm_gui packages. Exits non-zero on
any failure. --dump writes the full GUI state after every stock pick so two
builds can be compared: with --compare, the only allowed difference is
simulation.exposure_compensation_ev.
"""
import json
import os
import sys
from dataclasses import asdict


def load_json(path):
    with open(path, encoding='utf-8') as file:
        return json.load(file)


def compare(original_path, patched_path):
    original = load_json(original_path)
    patched = load_json(patched_path)
    differences = []
    for step, (a, b) in enumerate(zip(original, patched)):
        for key in sorted(set(a) | set(b)):
            if a.get(key) != b.get(key) and key != 'simulation.exposure_compensation_ev':
                differences.append(f'step {step}: {key}: {a.get(key)!r} -> {b.get(key)!r}')
    print(f'{len(original)} steps compared; differences other than Camera exposure: {len(differences)}')
    for line in differences[:40]:
        print('  ' + line)
    return 1 if differences or len(original) != len(patched) else 0


if '--compare' in sys.argv:
    i = sys.argv.index('--compare')
    sys.exit(compare(sys.argv[i + 1], sys.argv[i + 2]))

args = [a for a in sys.argv[1:] if not a.startswith('--')]
if not args:
    sys.exit(__doc__)
SRC = args[0]
DUMP = sys.argv[sys.argv.index('--dump') + 1] if '--dump' in sys.argv else None
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
# A throwaway settings file: without it the app reads and writes the real
# settings store (the registry on Windows), see spektrafilm_gui.app_settings.
import tempfile  # noqa: E402
SCRATCH = tempfile.mkdtemp(prefix='test_exposure_drift_')
os.environ.setdefault('SPEKTRAFILM_SETTINGS_INI', os.path.join(SCRATCH, 'settings.ini'))
sys.path.insert(0, SRC)

from qtpy import QtWidgets  # noqa: E402

import spektrafilm_gui  # noqa: E402
from spektrafilm_gui.app import create_app  # noqa: E402
from spektrafilm_gui.state_bridge import collect_gui_state  # noqa: E402

assert os.path.samefile(os.path.dirname(spektrafilm_gui.__file__), os.path.join(SRC, 'spektrafilm_gui')), (
    f'imported {spektrafilm_gui.__file__}, not the GUI under {SRC}')

failures = []


def check(name, ok, detail=''):
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
    if not ok:
        failures.append(name)


def flatten(state):
    flat = {}
    for section, values in asdict(state).items():
        if isinstance(values, dict):
            for key, value in values.items():
                flat[f'{section}.{key}'] = repr(value)
    return flat


qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
app = create_app()
controller = app.controller
sim = controller._widgets.simulation
dumps = []


def pick(selector, name):
    getattr(sim, selector).setCurrentText(name)
    controller.apply_profile_defaults(name)
    qapp.processEvents()
    dumps.append(flatten(collect_gui_state(widgets=controller._widgets)))
    return float(sim.exposure_compensation_ev.value)


sequence = [('print_paper', 'kodak_portra_endura'), ('print_paper', 'kodak_supra_endura'),
            ('print_paper', 'kodak_portra_endura'), ('print_paper', 'kodak_ultra_endura'),
            ('film_stock', 'kodak_gold_200'), ('film_stock', 'kodak_portra_400'),
            ('film_stock', 'kodak_gold_200'), ('print_paper', 'fujifilm_crystal_archive_typeii')]

# 1-2. Camera exposure keeps the user's value through stock changes
for camera_ev, film_ev in ((0.0, -1.0), (0.7, -1.0), (0.7, 0.0), (-0.3, 1.5)):
    sim.film_exposure_ev.value = film_ev
    sim.exposure_compensation_ev.value = camera_ev
    qapp.processEvents()
    seen = [pick(selector, name) for selector, name in sequence]
    check(f'Camera exposure {camera_ev:+.1f} with Film exposure {film_ev:+.1f} survives '
          f'{len(sequence)} stock picks', all(abs(v - camera_ev) < 1e-9 for v in seen),
          f"(seen {', '.join(f'{v:+.2f}' for v in seen)})")
    check(f'Film exposure {film_ev:+.1f} untouched', abs(float(sim.film_exposure_ev.value) - film_ev) < 1e-9)

# 3. Reset (top bar / filmstrip) goes back to the stock baseline, which must not
#    hold the composed Camera + Film sum
import time  # noqa: E402

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

image_path = os.path.join(SCRATCH, 'reset_test.png')   # Reset needs an open image
Image.fromarray(np.full((60, 90, 3), 120, dtype=np.uint8)).save(image_path)
controller.load_input_image(image_path)
deadline = time.time() + 60
while not controller._current_input_path and time.time() < deadline:
    qapp.processEvents()
    time.sleep(0.05)
sim.film_exposure_ev.value = -1.0
sim.exposure_compensation_ev.value = 0.7
qapp.processEvents()
pick('print_paper', 'kodak_supra_endura')
baseline = controller.baseline_look_state()
check('Reset baseline: Camera exposure 0, Film exposure 0 (not the drifted sum)',
      baseline.simulation.exposure_compensation_ev == 0.0 and baseline.simulation.film_exposure_ev == 0.0,
      f'(baseline Camera {baseline.simulation.exposure_compensation_ev:+.2f}, '
      f'Film {baseline.simulation.film_exposure_ev:+.2f})')
controller.reset_current_image()
qapp.processEvents()
check('top-bar Reset leaves Camera exposure at 0', abs(float(sim.exposure_compensation_ev.value)) < 1e-9,
      f'(Camera exposure after Reset {float(sim.exposure_compensation_ev.value):+.2f})')

# 4. No stock sets Camera exposure as a selection default
from spektrafilm.runtime.api import init_params  # noqa: E402
from spektrafilm_gui.options import FilmStocks, PrintStocks  # noqa: E402
from spektrafilm_gui.state import digest_after_selection  # noqa: E402

films = [member.value for member in FilmStocks]
papers = [member.value for member in PrintStocks]
pairs = [(film, papers[0]) for film in films] + [(films[0], paper) for paper in papers[1:]]
moved = []
for film, paper in pairs:
    params = digest_after_selection(init_params(film_profile=film, print_profile=paper))
    if params.camera.exposure_compensation_ev != 0.0:
        moved.append(f'{film}/{paper}: {params.camera.exposure_compensation_ev}')
check(f'no film or paper sets Camera exposure as a selection default ({len(pairs)} pairs)',
      not moved, f'({moved[:5]})')

if DUMP:
    with open(DUMP, 'w', encoding='utf-8') as file:
        json.dump(dumps, file, indent=1)
    print(f'state after each of {len(dumps)} picks written to {DUMP}')
import shutil  # noqa: E402
shutil.rmtree(SCRATCH, ignore_errors=True)
print('\nFAILURES:', failures if failures else 'none')
sys.exit(1 if failures else 0)
