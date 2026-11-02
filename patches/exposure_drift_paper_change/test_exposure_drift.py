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
from dataclasses import asdict, is_dataclass


def compare(original_path, patched_path):
    original = json.load(open(original_path, encoding='utf-8'))
    patched = json.load(open(patched_path, encoding='utf-8'))
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
    json.dump(dumps, open(DUMP, 'w', encoding='utf-8'), indent=1)
    print(f'state after each of {len(dumps)} picks written to {DUMP}')
print('\nFAILURES:', failures if failures else 'none')
sys.exit(1 if failures else 0)
