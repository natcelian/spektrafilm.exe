"""Verify the grain sub-layer reconciliation in runtime/pipeline.py.

usage: python -I verify_fix.py <engine dir> <image> [--quick]

<engine dir> holds the `spektrafilm` and `spektrafilm_gui` packages to test.
Every check must pass; a check that cannot run raises and fails the script.
The DIR-coupler "inversion has folded" warnings printed along the way come from
the curves themselves and are expected; they do not depend on this patch.
"""
import glob
import os
import sys
from dataclasses import replace

args = [a for a in sys.argv[1:] if not a.startswith('--')]
if len(args) != 2:
    sys.exit(__doc__)
ENGINE, IMAGE = args
QUICK = '--quick' in sys.argv
sys.path.insert(0, ENGINE)

import numpy as np
from PIL import Image

import spektrafilm
from spektrafilm.model.density_curves import select_development_time
from spektrafilm.profiles.io import load_profile
from spektrafilm.runtime.params_builder import digest_params
from spektrafilm.runtime.pipeline import SimulationPipeline
from spektrafilm.runtime.process import Simulator
from spektrafilm_gui.params_mapper import build_params_from_state
from spektrafilm_gui.state import PROJECT_DEFAULT_GUI_STATE as S

# a mistyped engine dir must not silently test whatever spektrafilm is installed
assert os.path.samefile(os.path.dirname(spektrafilm.__file__), os.path.join(ENGINE, 'spektrafilm')), (
    f'imported {spektrafilm.__file__}, not the engine under {ENGINE}')

TOLERANCE = 0.01
failures = []


def check(name, ok, detail=''):
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")
    if not ok:
        failures.append(name)


def params_for(film, paper='kodak_portra_endura', **sim):
    state = replace(S, simulation=replace(S.simulation, film_stock=film, print_paper=paper, **sim))
    return build_params_from_state(state)


def sum_error(data, normalised=False):
    total = np.asarray(data.density_curves, dtype=float)
    if normalised:                      # what develop() hands the grain
        total = total - np.nanmin(total, axis=0)
    layers = np.asarray(data.density_curves_layers, dtype=float)
    return float(np.nanmax(np.abs(np.nansum(layers, axis=1) - total)))


# 1. Pipeline-level arrays, every shipped profile selected as the film.
names = sorted(os.path.basename(p)[:-5]
               for p in glob.glob(os.path.join(ENGINE, 'spektrafilm', 'data', 'profiles', '*.json')))
checked_bw = set()
for name in names:
    if getattr(load_profile(name).data, 'density_curves_layers', None) is None:
        continue
    p = digest_params(params_for(name), apply_stocks_specifics=False)
    before_total = np.array(p.film.data.density_curves, copy=True)
    before_layers = np.array(p.film.data.density_curves_layers, copy=True)
    d = SimulationPipeline(p).film.data
    err = sum_error(d)
    if p.film.is_bw:
        # B&W: the pipeline collapses the development-time family first
        check(f'{name}: sub-layers sum to total', err <= TOLERANCE, f'(max err {err:.4f})')
        checked_bw.add(name)
    else:
        same = (np.array_equal(np.asarray(d.density_curves_layers), before_layers, equal_nan=True)
                and np.array_equal(np.asarray(d.density_curves), before_total, equal_nan=True))
        check(f'{name}: colour stock byte-identical', same, f'(sum err {err:.4f})')
    check(f'{name}: caller params untouched',          # the pipeline deep-copies
          np.array_equal(np.asarray(p.film.data.density_curves_layers), before_layers, equal_nan=True)
          and np.array_equal(np.asarray(p.film.data.density_curves), before_total, equal_nan=True))
check('the B&W stocks were all checked', {'kodak_trix', 'kodak_doublex', 'kodak_2302'} <= checked_bw,
      f'(checked {sorted(checked_bw)})')

# 2. The B&W total curve must not move; only the sub-layers change.
for name in ('kodak_trix', 'kodak_doublex', 'kodak_2302'):
    p = digest_params(params_for(name), apply_stocks_specifics=False)
    rendered = SimulationPipeline(p).film.data.density_curves
    shipped = load_profile(name).data
    select_development_time(shipped, p.film_render.chemistry.development_time)
    check(f'{name}: total curve unchanged',
          np.array_equal(np.asarray(rendered), np.asarray(shipped.density_curves), equal_nan=True))

# 3. End to end: grain-on render vs the grain-free preview, same image.
src = Image.open(IMAGE).convert('RGB')


def resized(width):
    return np.asarray(src.resize((width, round(width * src.height / src.width)), Image.LANCZOS),
                      dtype=np.float64) / 255.0


def preview_and_render(make_params, img):
    out = {}
    for preview in (True, False):
        p = make_params()
        p.settings.preview_mode = preview
        out[preview] = np.asarray(Simulator(digest_params(p, apply_stocks_specifics=False)).process(img))[..., :3]
    return out[True], out[False]


cases = (('kodak_trix', 'kodak_2302', {}, None),
         ('kodak_trix', 'kodak_2302', {'scan_film': True}, None),
         ('kodak_trix', 'kodak_2302', {'scan_film': True}, 'production'),
         ('kodak_trix', 'kodak_portra_endura', {}, None),
         ('kodak_doublex', 'kodak_2302', {}, None),
         ('kodak_portra_400', 'kodak_portra_endura', {}, None))
for film, paper, sim, grain_model in cases:
    label = film + '/' + paper + (' scanned directly' if sim.get('scan_film') else '')
    label += f', {grain_model} grain' if grain_model else ''

    def make(film=film, paper=paper, sim=sim, grain_model=grain_model):
        p = params_for(film, paper, **sim)
        if grain_model:
            p.film_render.grain = replace(p.film_render.grain, model=grain_model)
        return p

    for width in ((1280,) if QUICK else (1280, 6000)):    # 6000 px is upscaled if the image is smaller
        prev, rend = preview_and_render(make, resized(width))
        dm = abs(prev.mean() - rend.mean())
        d10 = abs(np.percentile(prev, 10) - np.percentile(rend, 10))
        check(f'{label} @{width}px: grain-on render matches preview', dm < TOLERANCE and d10 < 2 * TOLERANCE,
              f'(preview mean {prev.mean():.4f}, render mean {rend.mean():.4f}, |dp10| {d10:.4f})')

# 4. The bakes after the reconcile still run and keep the sub-layers consistent
# (measured against the normalised total the grain actually sees).
small = resized(640)


def with_chemistry(p):
    p.film_render.chemistry = replace(p.film_render.chemistry, active=True, gamma_factor=1.3)
    return p


def with_expiration(p, **kw):
    p.film_render.expiration = replace(p.film_render.expiration, active=True, **kw)
    return p


for label, mutate in (
        ('push/pull, gamma 1.3', with_chemistry),
        ('expiration, speed loss 1 EV', lambda p: with_expiration(p, speed_loss_ev=1.0)),
        ('expiration, dye density 0.8', lambda p: with_expiration(p, density_scale=0.8)),
):
    make = (lambda m=mutate: m(params_for('kodak_trix', 'kodak_2302')))
    err = sum_error(SimulationPipeline(digest_params(make(), apply_stocks_specifics=False)).film.data,
                    normalised=True)
    prev, rend = preview_and_render(make, small)
    check(f'kodak_trix with {label} @640px: sub-layers consistent, render matches preview',
          err <= TOLERANCE and abs(prev.mean() - rend.mean()) < TOLERANCE,
          f'(sum err {err:.4f}, preview {prev.mean():.4f}, render {rend.mean():.4f})')

# Known remaining issue, NOT fixed by this patch, reported and not counted as a
# failure: fog lifts the total's toe and the sub-layers are not offset the way
# develop() offsets the total, so the grain still moves the mean.
print('\nKNOWN, NOT FIXED HERE (fog vs the normalised total), @640px:')
for label, film, kw in (('expiration fog 0.1', 'kodak_trix', {'fog_rgb': (0.1, 0.1, 0.1)}),
                        ('expiration aged 10 years', 'kodak_portra_400', {'age_years': 10.0})):
    make = (lambda f=film, k=kw: with_expiration(params_for(f, 'kodak_portra_endura'), **k))
    err = sum_error(SimulationPipeline(digest_params(make(), apply_stocks_specifics=False)).film.data,
                    normalised=True)
    prev, rend = preview_and_render(make, small)
    print(f'  {film} with {label}: normalised sum err {err:.4f}, '
          f'preview {prev.mean():.4f}, grain-on render {rend.mean():.4f}')

print('\nFAILURES:', failures if failures else 'none')
sys.exit(1 if failures else 0)
