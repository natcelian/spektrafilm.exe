"""What the Reco tab repairs while a RAW is decoded.

Only the pieces that change the decode live here; `raw_file_processor` calls
them. The defaults are the identity: a `RecoverSettings()` decodes exactly as
before.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import rawpy
from numba import njit, prange

HIGHLIGHT_MODES = ('clip', 'blend', 'rebuild')

# Barrel and Vignette run -100..100; these are what 100 means.
BARREL_AT_100 = 0.3
_VIGNETTE_DARKEN_AT_100 = 0.9
_VIGNETTE_LIGHTEN_AT_100 = 2.0


def barrel_fit(k: float) -> float:
    """The scale that keeps a positive Barrel inside the photo. Its remap
    reads radius * (1 + k r^2), which past the edges is outside the photo and
    was clamped to the edge pixel, repeated as streaks; times this, the
    corner (r = 1) reads the corner and every other pixel reads inside -- the
    same curve, the picture just fitted to the frame. A negative Barrel
    already reads inside (1)."""
    return 1.0 / (1.0 + k) if k > 0.0 else 1.0


@dataclass(frozen=True, slots=True)
class DefectsPlan:
    """The dust to remove from a frame: the spots as JSON text (so plans compare), how, and how much."""
    spots: str
    method: str = 'fast'
    strength: float = 1.0


@dataclass(frozen=True, slots=True)
class RecoverSettings:
    highlights: str = 'clip'
    lens_barrel: float = 0.0
    lens_vignette: float = 0.0
    # Set only when this frame's denoise is already cached: '' means none.
    denoise_backend: str = ''
    denoise_amount: float = 0.0
    defects: DefectsPlan | None = None


def highlight_params(mode: str) -> dict[str, object]:
    """The `rawpy.postprocess` argument for a highlight mode; clip is LibRaw's own default."""
    if mode not in HIGHLIGHT_MODES:
        raise ValueError(f'unknown highlight mode {mode!r}')
    if mode == 'clip':
        return {}
    chosen = rawpy.HighlightMode.Blend if mode == 'blend' else rawpy.HighlightMode.ReconstructDefault
    return {'highlight_mode': chosen}


def highlight_gain(raw, params: dict[str, object]) -> float:
    """What puts a highlight mode's exposure back where clip has it.

    With a mode other than clip LibRaw scales by the strongest white-balance
    multiplier instead of the weakest, so the whole frame comes out darker by
    max / min of the multipliers in use (measured: 2.293 against 2.2932 for the
    X-H1 at daylight).
    """
    mode = params.get('highlight_mode')
    if mode is None or int(mode.value) < 2:
        return 1.0
    multipliers = raw.camera_whitebalance if params.get('use_camera_wb') else raw.daylight_whitebalance
    used = [value for value in multipliers if value > 0]
    return max(used) / min(used)


@njit(parallel=True, cache=True)
def _lens_adjust(src, dst, k, fit, gain_dark, gain_light):
    height, width, channels = src.shape
    cy = (height - 1) / 2.0
    cx = (width - 1) / 2.0
    half_diagonal = np.sqrt(cx * cx + cy * cy)
    for y in prange(height):
        for x in range(width):
            dy = (y - cy) / half_diagonal
            dx = (x - cx) / half_diagonal
            r2 = dx * dx + dy * dy
            scale = (1.0 + k * r2) * fit
            sx = cx + (x - cx) * scale
            sy = cy + (y - cy) * scale
            sx = min(max(sx, 0.0), width - 1.0)
            sy = min(max(sy, 0.0), height - 1.0)
            x0 = int(sx)
            y0 = int(sy)
            x1 = min(x0 + 1, width - 1)
            y1 = min(y0 + 1, height - 1)
            fx = sx - x0
            fy = sy - y0
            gain = max(1.0 - gain_dark * r2, 0.0) if gain_dark > 0.0 else 1.0 + gain_light * r2
            for c in range(channels):
                top = src[y0, x0, c] * (1.0 - fx) + src[y0, x1, c] * fx
                bottom = src[y1, x0, c] * (1.0 - fx) + src[y1, x1, c] * fx
                dst[y, x, c] = (top * (1.0 - fy) + bottom * fy) * gain


def apply_lens_adjust(rgb: np.ndarray, barrel: float, vignette: float) -> np.ndarray:
    """Barrel (-100..100: negative straightens a barrel lens) and Vignette
    (-100..100: negative lightens vignetting, positive darkens it).

    Radius is measured from the image centre to the half diagonal, so 1 is the
    corner. Both are empirical curves, not a lens model; zero returns `rgb`
    itself. A positive Barrel is fitted to the frame (`barrel_fit`).
    """
    if not barrel and not vignette:
        return rgb
    source = np.ascontiguousarray(rgb, dtype=np.float32)
    out = np.empty_like(source)
    gain_dark = _VIGNETTE_DARKEN_AT_100 * vignette / 100.0 if vignette > 0 else 0.0
    gain_light = _VIGNETTE_LIGHTEN_AT_100 * -vignette / 100.0 if vignette < 0 else 0.0
    k = BARREL_AT_100 * barrel / 100.0
    _lens_adjust(source, out, k, barrel_fit(k), gain_dark, gain_light)
    return out
