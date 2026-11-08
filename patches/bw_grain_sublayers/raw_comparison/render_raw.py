"""Render a raw through a Dyeworks engine the way the app does: Kodak Tri-X
scanned directly (the reversal, no print), with the scan finishing below.

usage: python -I render_raw.py <engine dir> <raw> <out.npy> <grain on|off> [width]
"""
import sys
import time
from dataclasses import replace

ENGINE, RAW, OUT, GRAIN = sys.argv[1:5]
WIDTH = int(sys.argv[5]) if len(sys.argv) > 5 else 0
sys.path.insert(0, ENGINE)

import numpy as np

from spektrafilm.runtime.params_builder import digest_params
from spektrafilm.runtime.process import Simulator
from spektrafilm.utils.raw_file_processor import load_and_process_raw_file
from spektrafilm_gui.params_mapper import build_params_from_state
from spektrafilm_gui.state import PROJECT_DEFAULT_GUI_STATE as S

SEED = 20261101

# The user's look, matched against their exported LUT (output_lut_test.cube):
# Tri-X scanned directly (scan_film) plus the scan finishing from their screenshot.
state = replace(S, simulation=replace(S.simulation, film_stock='kodak_trix',
                                      print_paper='kodak_2302', scan_film=True),
                scan_finishing=replace(S.scan_finishing, active=True, color_separation=0.01,
                                       black_point=0.019, white_point=0.957,
                                       white_point_rgb=(-0.020, 0.0, 0.0), shoulder=0.37))
image = load_and_process_raw_file(
    RAW,
    white_balance=state.load_raw.white_balance,
    temperature=state.load_raw.temperature,
    tint=state.load_raw.tint,
    lens_correction=False,
    output_colorspace=state.input_image.input_color_space,
    output_cctf_encoding=state.input_image.apply_cctf_decoding,
)
if WIDTH and image.shape[1] > WIDTH:
    from spektrafilm.utils.preview import resize_for_preview
    image = resize_for_preview(image, WIDTH)

p = build_params_from_state(state)
p.film_render.grain = replace(p.film_render.grain, active=(GRAIN == 'on'), seed=SEED)
t0 = time.perf_counter()
out = np.asarray(Simulator(digest_params(p, apply_stocks_specifics=False)).process(np.double(image)))[..., :3]
np.save(OUT, out.astype(np.float32))
print(f'{OUT}: input {image.shape} {image.dtype} -> out {out.shape} mean={out.mean():.4f} '
      f'({time.perf_counter() - t0:.0f}s)')
