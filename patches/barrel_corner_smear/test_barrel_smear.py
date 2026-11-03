"""Checks for the Barrel edge-smear fix, headless.

usage: python test_barrel_smear.py <src dir> <out dir> <raw> [--baseline]

Reco > Lens correction > Barrel remaps the decode: each pixel reads the
photo at radius * (1 + k r^2). With a positive Barrel that point lies
outside the photo near the edges; 2.2.1 clamped it to the edge pixel, so the
edge was repeated outward as streaks. The fix scales the remap so the
corners read the corners (`raw_recover.barrel_fit`): nothing is read from
outside, and the curve is the same.

1. A "where did it read" image (each pixel holds its own x and y) through
   `apply_lens_adjust`: at Barrel +25, +50 and +100 no pixel may read the
   clamped edge, and every pixel reads exactly where the fitted curve says.
2. A negative Barrel is unchanged: it reads where 2.2.1's curve says.
3. The dust-spot geometry (`dust_geometry.barrel_backward` / `_forward`)
   says the same as the remap, so Reco's spots stay on the dust.
4. Through the real app: the raw decoded at Barrel +100 equals the decode
   at 0 remapped by the fitted curve, edges included (2.2.1: smeared edges).

`--baseline` runs the same against 2.2.1 and lists what fails there. Exits
non-zero on a failure (not with --baseline).
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
from scipy.ndimage import map_coordinates  # noqa: E402

import spektrafilm  # noqa: E402
from spektrafilm.utils import dust_geometry  # noqa: E402
from spektrafilm.utils.raw_recover import BARREL_AT_100, apply_lens_adjust  # noqa: E402

assert os.path.samefile(os.path.dirname(spektrafilm.__file__), os.path.join(SRC, 'spektrafilm')), (
    f'imported {spektrafilm.__file__}, not the engine under {SRC}')

failures = []


def check(name, ok, detail=''):
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
    if not ok:
        failures.append(name)


def fitted(k):
    """The fix's scale, written out here so the check means the same on 2.2.1."""
    return 1.0 / (1.0 + k) if k > 0 else 1.0


def curve(width, height, k, fit):
    """Where each pixel of a width x height frame reads, by the formula."""
    cx, cy = (width - 1) / 2.0, (height - 1) / 2.0
    half_diagonal = np.hypot(cx, cy)
    y, x = np.mgrid[0:height, 0:width].astype(np.float64)
    r2 = ((x - cx) / half_diagonal) ** 2 + ((y - cy) / half_diagonal) ** 2
    scale = (1.0 + k * r2) * fit
    return cx + (x - cx) * scale, cy + (y - cy) * scale


# 1 + 2. where each pixel reads, through the real remap
WIDTH, HEIGHT = 600, 400
y, x = np.mgrid[0:HEIGHT, 0:WIDTH].astype(np.float32)
where = np.stack([x, y, np.zeros_like(x)], axis=2)
for barrel in (25, 50, 100, -50):
    k = BARREL_AT_100 * barrel / 100.0
    read = apply_lens_adjust(where.copy(), float(barrel), 0.0)
    rx, ry = read[..., 0].astype(np.float64), read[..., 1].astype(np.float64)
    at_edge = (rx <= 1e-3) | (rx >= WIDTH - 1 - 1e-3) | (ry <= 1e-3) | (ry >= HEIGHT - 1 - 1e-3)
    corners = np.zeros_like(at_edge)
    corners[[0, 0, -1, -1], [0, -1, 0, -1]] = True             # the corners read the corners
    smeared = float(100.0 * (at_edge & ~corners).mean())
    ex, ey = curve(WIDTH, HEIGHT, k, fitted(k))
    off = float(np.max(np.hypot(rx - ex, ry - ey)))
    if barrel > 0:
        check(f'1. Barrel {barrel:+d}: no pixel reads the clamped edge, and each reads where the fitted curve says',
              smeared == 0.0 and off < 0.01,
              f'({smeared:.1f}% of the frame read the edge pixel; furthest from the curve {off:.1f} px)')
    else:
        check(f'2. Barrel {barrel:+d}: unchanged, it reads where 2.2.1\'s curve says', off < 0.01,
              f'(furthest from the curve {off:.3f} px)')

# 3. the dust-spot geometry agrees with the remap
k = BARREL_AT_100
read = apply_lens_adjust(where.copy(), 100.0, 0.0)
grid = np.stack(np.meshgrid(np.linspace(0, WIDTH - 1, 13), np.linspace(0, HEIGHT - 1, 9)), axis=-1).reshape(-1, 2)
backward = dust_geometry.barrel_backward(grid, WIDTH, HEIGHT, 100.0)
remap = np.stack([map_coordinates(read[..., c], [grid[:, 1], grid[:, 0]], order=1) for c in (0, 1)], axis=1)
inside = (backward[:, 0] >= 0) & (backward[:, 0] <= WIDTH - 1) & (backward[:, 1] >= 0) & (backward[:, 1] <= HEIGHT - 1)
round_trip = dust_geometry.barrel_forward(backward[inside], WIDTH, HEIGHT, 100.0)
check('3. Barrel +100: the dust geometry says where the remap reads, and maps it back',
      bool(inside.all()) and float(np.max(np.abs(backward - remap))) < 0.01
      and float(np.nanmax(np.abs(round_trip - grid[inside]))) < 0.01,
      f'({int((~inside).sum())} of {len(grid)} grid points read outside the photo; '
      f'furthest from the remap {float(np.max(np.abs(backward - remap))):.1f} px)')

# 4. through the real app
from qtpy import QtWidgets  # noqa: E402

import spektrafilm_gui.app as app_module  # noqa: E402
from spektrafilm_gui.app import create_app  # noqa: E402

qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
app = create_app()
c = app.controller
win = app.main_window
win.resize(1600, 1000)
win.show()
seen = []
win.statusBar().messageChanged.connect(seen.append)


def settle(seconds=0.3):
    deadline = time.time() + seconds
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.02)


def wait_idle(timeout=240):
    deadline = time.time() + timeout
    settle(0.5)
    while time.time() < deadline and c._active_simulation_worker is not None:
        settle(0.1)
    settle(1.0)


if app_module._background_warmup_pool is not None:
    app_module._background_warmup_pool.waitForDone(300000)
c.roll_add_files([RAW])                         # 'Add frames...'
wait_idle()
flat = np.asarray(c._current_input_image)[..., 1].astype(np.float64)
seen.clear()
c._widgets.recover.lens_barrel.value = 100.0    # Reco > Lens correction > Barrel
deadline = time.time() + 180
while time.time() < deadline and not any(m.startswith('Loaded raw') for m in seen):
    settle(0.2)
wait_idle()
bent = np.asarray(c._current_input_image)[..., 1].astype(np.float64)
height, width = flat.shape
ex, ey = curve(width, height, BARREL_AT_100, fitted(BARREL_AT_100))
expected = map_coordinates(flat, [ey, ex], order=1, mode='nearest')
cx, cy = (width - 1) / 2.0, (height - 1) / 2.0
yy, xx = np.mgrid[0:height, 0:width]
band = np.hypot((xx - cx) / np.hypot(cx, cy), (yy - cy) / np.hypot(cx, cy)) > 0.8     # the outer edges
error = float(np.mean(np.abs(bent[band] - expected[band])) / max(float(np.mean(flat)), 1e-9))
check('4. the app, Barrel +100: the decode is the photo remapped by the fitted curve, edges included',
      error < 0.01, f'(edge difference {100 * error:.1f}% of the mean level, {width} x {height} decode)')

print(f'\n{"2.2.1" if BASELINE else "fixed"}: {len(failures)} check(s) fail' +
      (': ' + '; '.join(failures) if failures else ''))
sys.exit(0 if BASELINE else (1 if failures else 0))
