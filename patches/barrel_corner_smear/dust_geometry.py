"""Where a dust spot of the decoded frame lands on the picture, and where a click on the picture lands in it.

Spots are found on the decode. The picture the user sees has gone through the lens correction, Barrel, the
quarter turns the user gave it and the camera crop. Each step is mapped both ways, so the rings of Show spots
and the point of a Manual click stay on the dust. Points are (x, y) in pixels of the decode, which has the same
size before and after the lens steps.
"""
from __future__ import annotations

import lensfunpy
import numpy as np
from scipy.ndimage import map_coordinates

from spektrafilm.utils.raw_file_processor import (
    _find_lens_candidates, _select_lens_candidate, read_exif_metadata)
from spektrafilm.utils.raw_recover import BARREL_AT_100, barrel_fit

_GRID = 48
_NEWTON_STEPS = 8


def barrel_k(barrel: float) -> float:
    return BARREL_AT_100 * barrel / 100.0


def barrel_backward(points: np.ndarray, width: int, height: int, barrel: float) -> np.ndarray:
    """Picture pixel -> where the barrel step took it from (the formula the remap itself uses)."""
    cx, cy = (width - 1) / 2.0, (height - 1) / 2.0
    half_diagonal = np.hypot(cx, cy)
    d = (points - [cx, cy]) / half_diagonal
    r2 = (d ** 2).sum(axis=1, keepdims=True)
    k = barrel_k(barrel)
    return np.array([cx, cy]) + (points - [cx, cy]) * (1.0 + k * r2) * barrel_fit(k)


def barrel_forward(points: np.ndarray, width: int, height: int, barrel: float) -> np.ndarray:
    """Source pixel -> where the barrel step puts it: the radius s with s (1 + k (s / R)^2) fit = r.
    NaN for a pixel the step takes off the picture."""
    cx, cy = (width - 1) / 2.0, (height - 1) / 2.0
    half_diagonal = np.hypot(cx, cy)
    k = barrel_k(barrel)
    fit = barrel_fit(k)
    offset = points - [cx, cy]
    r = np.hypot(offset[:, 0], offset[:, 1])
    s = r.copy()
    for _ in range(_NEWTON_STEPS):
        f = s * (1.0 + k * (s / half_diagonal) ** 2) * fit - r
        df = (1.0 + 3.0 * k * (s / half_diagonal) ** 2) * fit
        s = s - f / np.where(np.abs(df) < 1e-6, 1e-6, df)
    scale = np.divide(s, r, out=np.ones_like(r), where=r > 1e-9)
    placed = np.array([cx, cy]) + offset * scale[:, None]
    # A source pixel the step pushes off the picture has no place on it.
    reachable = np.abs(barrel_backward(placed, width, height, barrel) - points).max(axis=1) < 0.05
    placed[~reachable] = np.nan
    return placed


def turn_forward(points: np.ndarray, width: int, height: int, turns: int) -> tuple[np.ndarray, int, int]:
    """Points through np.rot90(image, k=turns); also the new width and height."""
    x, y = points[:, 0], points[:, 1]
    k = turns % 4
    if k == 0:
        return points.copy(), width, height
    if k == 1:
        return np.stack([y, width - 1 - x], axis=1), height, width
    if k == 2:
        return np.stack([width - 1 - x, height - 1 - y], axis=1), width, height
    return np.stack([height - 1 - y, x], axis=1), height, width


def turn_backward(points: np.ndarray, width: int, height: int, turns: int) -> np.ndarray:
    """The inverse; width and height are those of the picture after the turns."""
    k = turns % 4
    inverse_turns = (4 - k) % 4
    mapped, _, _ = turn_forward(points, width, height, inverse_turns)
    return mapped


def crop_rect(width: int, height: int, center, size) -> tuple[int, int, int, int]:
    """(x0, y0, w, h) of the camera crop, the arithmetic of crop_resize.crop_image."""
    shape = np.array([height, width])
    centre = np.round(shape * np.array(center)[::-1])
    extent = np.round(float(shape.max()) * np.array(size)[::-1])
    extent = np.minimum(extent, shape.astype(np.float64))
    origin = np.round(centre - extent / 2)
    extent = extent.astype(np.int64)
    origin = origin.astype(np.int64)
    origin[origin < 0] = 0
    if origin[0] + extent[0] > shape[0]:
        origin[0] = shape[0] - extent[0]
    if origin[1] + extent[1] > shape[1]:
        origin[1] = shape[1] - extent[1]
    return int(origin[1]), int(origin[0]), int(extent[1]), int(extent[0])


class LensMap:
    """lensfun's geometry for one frame, on a coarse grid, in both directions."""

    def __init__(self, grid: np.ndarray, width: int, height: int) -> None:
        self._grid = grid                    # (G, G, 2): picture position -> decode position, in decode pixels
        self._width, self._height = width, height

    def backward(self, points: np.ndarray) -> np.ndarray:
        """Picture pixel -> the decode pixel it shows."""
        gx = np.clip(points[:, 0] / max(self._width - 1, 1), 0, 1) * (_GRID - 1)
        gy = np.clip(points[:, 1] / max(self._height - 1, 1), 0, 1) * (_GRID - 1)
        x0 = np.clip(gx.astype(int), 0, _GRID - 2)
        y0 = np.clip(gy.astype(int), 0, _GRID - 2)
        fx, fy = (gx - x0)[:, None], (gy - y0)[:, None]
        top = self._grid[y0, x0] * (1 - fx) + self._grid[y0, x0 + 1] * fx
        bottom = self._grid[y0 + 1, x0] * (1 - fx) + self._grid[y0 + 1, x0 + 1] * fx
        return top * (1 - fy) + bottom * fy

    def forward(self, points: np.ndarray) -> np.ndarray:
        """Decode pixel -> where the picture shows it, by Newton's method on `backward`; NaN when it has no place."""
        guess = points.astype(np.float64).copy()
        for _ in range(_NEWTON_STEPS):
            error = self.backward(guess) - points
            step = 1.0
            jx = (self.backward(guess + [step, 0.0]) - self.backward(guess)) / step
            jy = (self.backward(guess + [0.0, step]) - self.backward(guess)) / step
            det = jx[:, 0] * jy[:, 1] - jx[:, 1] * jy[:, 0]
            det = np.where(np.abs(det) < 1e-9, 1e-9, det)
            dx = (error[:, 0] * jy[:, 1] - error[:, 1] * jy[:, 0]) / det
            dy = (jx[:, 0] * error[:, 1] - jx[:, 1] * error[:, 0]) / det
            guess = guess - np.stack([dx, dy], axis=1)
        guess[np.abs(self.backward(guess) - points).max(axis=1) > 0.5] = np.nan
        return guess


def lens_map(raw_path, width: int, height: int) -> LensMap | None:
    """The lens correction's geometry for this frame, or None when lensfun does not know the lens."""
    exif = read_exif_metadata(raw_path)
    if not exif.lens_model.strip():
        return None
    db = lensfunpy.Database()
    cameras = db.find_cameras(exif.make, exif.model, loose_search=True)
    if not cameras:
        return None
    lenses = _find_lens_candidates(db, cameras[0], exif)
    if not lenses:
        return None
    lens = _select_lens_candidate(lenses, exif)
    small_w = _GRID * 8
    small_h = max(2, int(round(small_w * height / width)))
    modifier = lensfunpy.Modifier(lens, cameras[0].crop_factor, small_w, small_h)
    modifier.initialize(exif.focal_length, exif.f_number, pixel_format=np.float32, flags=lensfunpy.ModifyFlags.ALL)
    coords = modifier.apply_subpixel_geometry_distortion()
    if coords is None:
        return None
    # Positions are fractions of the picture ((pixel + 0.5) / size), so the small modifier and the full
    # frame agree; the grid sits at _GRID evenly spaced full-frame pixels.
    gx = np.linspace(0, width - 1, _GRID)
    gy = np.linspace(0, height - 1, _GRID)
    sx = (gx + 0.5) / width * small_w - 0.5
    sy = (gy + 0.5) / height * small_h - 0.5
    yy, xx = np.meshgrid(sy, sx, indexing='ij')
    grid = np.empty((_GRID, _GRID, 2))
    for axis, size, full in ((0, small_w, width), (1, small_h, height)):
        values = map_coordinates(coords[:, :, 1, axis], [yy, xx], order=1, mode='nearest')
        grid[..., axis] = (values + 0.5) / size * full - 0.5
    return LensMap(grid, width, height)


class PictureMap:
    """The whole chain for one frame: decode pixels <-> positions on the picture, as fractions of it.

    `width` and `height` are the decode's, before the quarter turns. `crop` is None or (center, size) of the
    camera crop; the scan frame's crop, straighten and desqueeze are not mapped (the caller does not draw then).
    """

    def __init__(self, width: int, height: int, *, lens: LensMap | None = None, barrel: float = 0.0,
                 turns: int = 0, crop=None) -> None:
        self._lens, self._barrel, self._turns = lens, barrel, turns % 4
        self._width, self._height = width, height
        self._turned_w, self._turned_h = (height, width) if self._turns % 2 else (width, height)
        self._rect = (crop_rect(self._turned_w, self._turned_h, *crop) if crop is not None
                      else (0, 0, self._turned_w, self._turned_h))

    @property
    def picture_width(self) -> int:
        """Width of the picture in decode pixels: what a ring's radius is measured against."""
        return self._rect[2]

    def to_picture(self, points) -> np.ndarray:
        """Decode pixels (N, 2) -> (u, v) fractions of the picture; NaN where the picture does not show them."""
        points = np.asarray(points, dtype=np.float64).reshape(-1, 2)
        if self._lens is not None:
            points = self._lens.forward(points)
        if self._barrel:
            points = barrel_forward(points, self._width, self._height, self._barrel)
        points, _, _ = turn_forward(points, self._width, self._height, self._turns)
        x0, y0, w, h = self._rect
        w, h = max(w, 1), max(h, 1)           # a crop being dragged out can be 0 wide for a moment
        out = np.stack([(points[:, 0] - x0 + 0.5) / w, (points[:, 1] - y0 + 0.5) / h], axis=1)
        out[(out < 0).any(axis=1) | (out > 1).any(axis=1)] = np.nan
        return out

    def to_decode(self, u: float, v: float) -> tuple[float, float]:
        """A position on the picture (fractions) -> the decode pixel under it."""
        x0, y0, w, h = self._rect
        point = np.array([[u * w + x0 - 0.5, v * h + y0 - 0.5]])
        point = turn_backward(point, self._turned_w, self._turned_h, self._turns)
        if self._barrel:
            point = barrel_backward(point, self._width, self._height, self._barrel)
        if self._lens is not None:
            point = self._lens.backward(point)
        return float(point[0, 0]), float(point[0, 1])
