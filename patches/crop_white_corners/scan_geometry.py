"""Phase D2 (2026-09-18): scan-time geometry (desqueeze, skew) on the GPU.

2026-09-18; see design/DECISIONS.md#d-scan_geometry-169

What is replicated, exactly (shaders/scan_geometry.wgsl says how):
  desqueeze = skimage rescale(order=3): [anti-alias gaussian, mirror, when
              compressing] -> ndi.zoom(order=3, grid_mode=True,
              mode='mirror') -> clip to the input's range. Separable along
              the one scaled axis.
  skew      = skimage rotate(order=1, mode='constant', cval=SKEW_FILL):
              per-channel bilinear at M @ (c, r, 1), a neighbour outside the
              picture reading SKEW_FILL. M is skimage's OWN transform
              matrix, built here the way rotate builds it.

Tiling: a desqueeze line (row or column) is independent of every other, so
bands of lines are exact with no halo. A skew band of output rows uploads
only the input rows its rotated footprint covers. Budget checked before any
allocation (plan_residency); an uncaptured device error during a band
discards the whole result. None -> the caller runs the CPU functions.

PARITY: vs the CPU functions on the same f64 input, max_abs <= 1e-5 scaled
by the output's magnitude (tests/test_scan_geometry_gpu.py).
"""

from __future__ import annotations

import math
import os
from typing import Any, Optional

import numpy as np

from spektrafilm.gpu.budget import consume_device_error, install_error_trap, plan_residency
from spektrafilm.gpu.device import GPUDevice, KernelCache, logger, note_gpu_failure, check_render_lock
from spektrafilm.gpu.backend import Backend, resolve_backend
from spektrafilm.gpu.spectral import MIN_GPU_PIXELS
from spektrafilm.utils.crop_resize import apply_desqueeze, apply_skew, crop_image
from spektrafilm.model.scan_finishing import scan_geometry_is_identity
from skimage.transform import SimilarityTransform

_SHADER_PATH = os.path.join(os.path.dirname(__file__), "shaders", "scan_geometry.wgsl")
_DPARAMS_NBYTES = 48   # struct DParams
_SPARAMS_NBYTES = 96   # struct SParams
_I32_MAX = 2 ** 31 - 1


class ScanGeometryGPU:
    def __init__(self) -> None:
        import wgpu

        self.gpu = GPUDevice.get()
        if not self.gpu.is_available:
            raise RuntimeError(f"GPU not available: {self.gpu.init_error}")
        install_error_trap()
        self._wgpu = wgpu
        device = self.gpu.device
        with open(_SHADER_PATH, "r", encoding="utf-8") as fh:
            module = device.create_shader_module(code=fh.read())
        un = wgpu.BufferBindingType.uniform
        ro = wgpu.BufferBindingType.read_only_storage
        st = wgpu.BufferBindingType.storage

        def layout(entries):
            return device.create_bind_group_layout(entries=[
                {"binding": b, "visibility": wgpu.ShaderStage.COMPUTE, "buffer": {"type": t}}
                for b, t in entries])

        self._dbgl = layout([(0, un), (1, ro), (2, st), (3, ro)])
        self._sbgl = layout([(4, un), (5, ro), (6, st)])
        dlay = device.create_pipeline_layout(bind_group_layouts=[self._dbgl])
        slay = device.create_pipeline_layout(bind_group_layouts=[self._sbgl])
        self._pipes = {
            name: device.create_compute_pipeline(
                layout=dlay, compute={"module": module, "entry_point": name})
            for name in ("aa_blur", "prefilter", "zoom_cubic",
                         "minmax_partial", "minmax_final")
        }
        self._skew_pipe = device.create_compute_pipeline(
            layout=slay, compute={"module": module, "entry_point": "skew_bilinear"})

    # ------------------------------------------------------------ helpers
    def _buf(self, nbytes: int, usage) -> Any:
        return self.gpu.device.create_buffer(label="scan_geometry:buf",
                                             size=max(int(nbytes), 16), usage=usage)

    def _read(self, encoder, buf, nbytes: int, shape) -> np.ndarray:
        check_render_lock('scan_geometry.ScanGeometryGPU._read')
        wgpu = self._wgpu
        staging = self._buf(nbytes, wgpu.BufferUsage.COPY_DST | wgpu.BufferUsage.MAP_READ)
        encoder.copy_buffer_to_buffer(buf, 0, staging, 0, nbytes)
        self.gpu.device.queue.submit([encoder.finish()])
        staging.map_sync(mode=wgpu.MapMode.READ)
        try:
            out = np.frombuffer(staging.read_mapped(0, nbytes), dtype=np.float32).reshape(shape).copy()
        finally:
            staging.unmap()
        staging.destroy()
        return out

    def _bind(self, bgl, entries):
        return self.gpu.device.create_bind_group(layout=bgl, entries=[
            {"binding": b, "resource": {"buffer": buf, "offset": 0, "size": buf.size}}
            for b, buf in entries])

    @staticmethod
    def _pass(encoder, pipe, bind, gx, gy=1):
        cp = encoder.begin_compute_pass()
        cp.set_pipeline(pipe)
        cp.set_bind_group(0, bind)
        cp.dispatch_workgroups(int(gx), int(gy), 1)
        cp.end()

    def _upload(self, arr: np.ndarray, owned: list) -> Any:
        wgpu = self._wgpu
        data = np.ascontiguousarray(arr, dtype=np.float32)
        buf = self._buf(data.nbytes, wgpu.BufferUsage.STORAGE | wgpu.BufferUsage.COPY_DST
                        | wgpu.BufferUsage.COPY_SRC)
        owned.append(buf)
        self.gpu.device.queue.write_buffer(buf, 0, data)
        return buf

    def _dparams(self, owned: list, ints, clip=(0.0, 0.0), clip_from_buf: bool = False) -> Any:
        wgpu = self._wgpu
        raw = np.zeros(12, dtype=np.uint32)
        raw[0:len(ints)] = ints
        raw[8:10] = np.array(clip, dtype=np.float32).view(np.uint32)
        raw[10] = 1 if clip_from_buf else 0
        b = self._buf(_DPARAMS_NBYTES, wgpu.BufferUsage.UNIFORM | wgpu.BufferUsage.COPY_DST)
        self.gpu.device.queue.write_buffer(b, 0, raw)
        owned.append(b)
        return b

    @staticmethod
    def _release(owned: list) -> None:
        for b in owned:
            try:
                b.destroy()
            except Exception:
                pass

    # ------------------------------------------------- encode (no submit)
    # Record into the CALLER's encoder, on caller buffers; every buffer these
    # allocate is appended to `owned` for the caller to destroy after its
    # submit has drained. Used as-is by the standalone band functions below
    # and by scan_resident's fused chain (Phase D3).
    def encode_minmax(self, encoder, owned: list, src, n_elems: int) -> Any:
        """(min, max, non-finite flag) of the first `n_elems` f32 of `src`,
        in a 16-byte device buffer."""
        STO = self._wgpu.BufferUsage.STORAGE | self._wgpu.BufferUsage.COPY_SRC
        n_wg = max(1, min(1024, math.ceil(n_elems / 256)))
        partial = self._buf(n_wg * 12, STO)
        mm = self._buf(16, STO)
        owned += [partial, mm]
        empty = self._buf(16, STO)
        owned.append(empty)
        p = self._dparams(owned, [n_elems, n_wg])
        self._pass(encoder, self._pipes["minmax_partial"],
                   self._bind(self._dbgl, [(0, p), (1, src), (2, partial), (3, empty)]), n_wg)
        self._pass(encoder, self._pipes["minmax_final"],
                   self._bind(self._dbgl, [(0, p), (1, partial), (2, mm), (3, empty)]), 1)
        return mm

    def encode_desqueeze(self, encoder, owned: list, src, h: int, w: int, n_out: int,
                         horizontal: bool, weights: Optional[np.ndarray],
                         clip=None, clip_buf=None) -> Any:
        """Desqueeze an (h, w, 3) f32 device image along one axis. Clip range
        from `clip` (lo, hi) or, fused, from `clip_buf` (encode_minmax)."""
        STO = self._wgpu.BufferUsage.STORAGE | self._wgpu.BufferUsage.COPY_DST | self._wgpu.BufferUsage.COPY_SRC
        if horizontal:
            n_lines, n_in = h, w
            in_line, in_pos, out_line, out_pos = w * 3, 3, n_out * 3, 3
            out_elems = h * n_out * 3
        else:
            n_lines, n_in = w, h
            in_line, in_pos, out_line, out_pos = 3, w * 3, 3, w * 3
            out_elems = n_out * w * 3
        nbytes = h * w * 12
        coef = self._buf(nbytes, STO)
        out = self._buf(out_elems * 4, STO)
        owned += [coef, out]
        wbuf = self._upload(np.zeros(1) if weights is None else weights, owned)
        radius = 0 if weights is None else (len(weights) - 1) // 2
        ints = [n_lines, n_in, n_out, radius, in_line, in_pos, out_line, out_pos]
        p = (self._dparams(owned, ints, clip_from_buf=True) if clip_buf is not None
             else self._dparams(owned, ints, clip=clip))
        prefilter_src = src
        if weights is not None:
            blurred = self._buf(nbytes, STO)
            owned.append(blurred)
            self._pass(encoder, self._pipes["aa_blur"],
                       self._bind(self._dbgl, [(0, p), (1, src), (2, blurred), (3, wbuf)]),
                       math.ceil(n_in / 64), n_lines * 3)
            prefilter_src = blurred
        self._pass(encoder, self._pipes["prefilter"],
                   self._bind(self._dbgl, [(0, p), (1, prefilter_src), (2, coef), (3, wbuf)]),
                   math.ceil(n_lines * 3 / 64))
        self._pass(encoder, self._pipes["zoom_cubic"],
                   self._bind(self._dbgl, [(0, p), (1, coef), (2, out),
                                           (3, clip_buf if clip_buf is not None else wbuf)]),
                   math.ceil(n_out / 64), n_lines * 3)
        return out

    def encode_skew(self, encoder, owned: list, src, rows: int, cols: int, in_r0: int,
                    in_rows: int, out_r0: int, band_rows: int, m_split: np.ndarray) -> Any:
        wgpu = self._wgpu
        STO = wgpu.BufferUsage.STORAGE | wgpu.BufferUsage.COPY_DST | wgpu.BufferUsage.COPY_SRC
        out = self._buf(band_rows * cols * 12, STO)
        owned.append(out)
        raw = np.zeros(24, dtype=np.uint32)
        raw[0:6] = [rows, cols, out_r0, band_rows, in_r0, in_rows]
        raw[8:24] = np.asarray(m_split, dtype=np.float32).view(np.uint32)
        p = self._buf(_SPARAMS_NBYTES, wgpu.BufferUsage.UNIFORM | wgpu.BufferUsage.COPY_DST)
        owned.append(p)
        self.gpu.device.queue.write_buffer(p, 0, raw)
        self._pass(encoder, self._skew_pipe,
                   self._bind(self._sbgl, [(4, p), (5, src), (6, out)]),
                   math.ceil(cols / 8), math.ceil(band_rows / 8))
        return out

    # ------------------------------------------------ standalone (bands)
    def desqueeze_band(self, band: np.ndarray, n_out: int, horizontal: bool,
                       weights: Optional[np.ndarray], clip_lo: float, clip_hi: float) -> np.ndarray:
        """One band of lines. `band` is (H, W, 3) f32; lines run along W when
        `horizontal`, else along H."""
        h, w, _ = band.shape
        out_shape = (h, n_out, 3) if horizontal else (n_out, w, 3)
        owned: list = []
        try:
            src = self._upload(band, owned)
            encoder = self.gpu.device.create_command_encoder()
            out = self.encode_desqueeze(encoder, owned, src, h, w, n_out, horizontal,
                                        weights, clip=(clip_lo, clip_hi))
            return self._read(encoder, out, int(np.prod(out_shape)) * 4, out_shape)
        finally:
            self._release(owned)

    def skew_band(self, band_in: np.ndarray, rows: int, cols: int, in_r0: int,
                  out_r0: int, band_rows: int, m_split: np.ndarray) -> np.ndarray:
        owned: list = []
        try:
            src = self._upload(band_in, owned)
            encoder = self.gpu.device.create_command_encoder()
            out = self.encode_skew(encoder, owned, src, rows, cols, in_r0,
                                   band_in.shape[0], out_r0, band_rows, m_split)
            return self._read(encoder, out, band_rows * cols * 12, (band_rows, cols, 3))
        finally:
            self._release(owned)


_IMPL_CACHE = KernelCache(ScanGeometryGPU, "Scan geometry GPU kernel")


def _get_impl() -> Optional[ScanGeometryGPU]:
    return _IMPL_CACHE.get()


def _gpu_requested(arr: np.ndarray, backend: str) -> bool:
    requested = (backend or "auto").strip().lower()
    if resolve_backend(requested) is not Backend.GPU:
        return False
    if requested == "auto" and arr.shape[0] * arr.shape[1] < MIN_GPU_PIXELS:
        return False
    return True


def _bands(total: int, bytes_per_unit, max_units: int = 1 << 30):
    """Largest band (in units of `total`) whose buffers fit, halving until
    they do; None when even one unit doesn't."""
    size = min(total, max_units)
    while size >= 1:
        if plan_residency(bytes_per_unit(size)) == 'gpu':
            return size
        if size == 1:
            return None
        size = max(1, size // 2)
    return None


def _split(v: float) -> tuple[float, float]:
    hi = float(np.float32(v))
    return hi, float(np.float32(v - hi))


def _split_offset(v: float) -> tuple[float, float, float, float]:
    """(integer part, fraction hi, fraction lo, 0) of a translation; the
    shader adds the integer parts separately, so none of them is rounded."""
    i = math.floor(v)
    return (float(i), *_split(v - i), 0.0)


def _desqueeze_setup(factor: float, h: int, w: int):
    """(horizontal, n_in, n_out, weights) for skimage rescale's desqueeze of
    an (h, w) frame, or None where this port declines: the exact coordinate
    ratio would overflow i32, or the anti-alias kernel is wider than a line."""
    horizontal = factor > 0
    n_in = w if horizontal else h
    n_out = max(int(round(n_in * abs(factor))), 1)
    if (2 * n_out + 1) * n_in > _I32_MAX:
        return None
    weights = None
    sigma = max(0.0, (n_in / n_out - 1.0) / 2.0)
    if sigma > 1e-15:
        radius = int(4.0 * sigma + 0.5)
        if radius >= n_in:
            return None
        x = np.arange(-radius, radius + 1, dtype=np.float64)
        weights = np.exp(-0.5 / (sigma * sigma) * x * x)
        weights = weights / weights.sum()
    return horizontal, n_in, n_out, weights


def _skew_split(rows: int, cols: int, angle: float) -> np.ndarray:
    m = _skew_matrix(rows, cols, angle)
    return np.array([*_split(m[0, 0]), *_split(m[0, 1]), *_split_offset(m[0, 2]),
                     *_split(m[1, 0]), *_split(m[1, 1]), *_split_offset(m[1, 2])],
                    dtype=np.float64)


def _crop_ranges(h: int, w: int, center, size) -> tuple[int, int, int, int]:
    """(row0, n_rows, col0, n_cols) that crop_image keeps from an (h, w)
    frame -- found by running crop_image ITSELF over zero-memory index views,
    so this cannot drift from it."""

    rows = crop_image(np.broadcast_to(np.arange(h)[:, None, None], (h, w, 1)),
                      center=tuple(center), size=tuple(size))[:, 0, 0]
    cols = crop_image(np.broadcast_to(np.arange(w)[None, :, None], (h, w, 1)),
                      center=tuple(center), size=tuple(size))[0, :, 0]
    return int(rows[0]), int(rows.size), int(cols[0]), int(cols.size)


def fused_geometry_plan(p, h: int, w: int) -> Optional[dict]:
    """Phase D3: everything apply_scan_geometry(p) needs on an (h, w) frame,
    precomputed on the host for encode_fused_geometry -- or None when the
    fused route can't express it exactly (the caller then runs the separate
    pass). Also carries `extra`: the image buffers the fused passes add, for
    the caller's residency plan."""

    if scan_geometry_is_identity(p):
        return None
    plan = {'desqueeze': None, 'skew': None, 'crop': None}
    extra = [16]                                   # min/max
    cur_h, cur_w = h, w
    factor = float(getattr(p, 'desqueeze', 0.0))
    if factor not in (0.0, 1.0, -1.0):
        setup = _desqueeze_setup(factor, h, w)
        if setup is None:
            return None
        horizontal, _n_in, n_out, weights = setup
        if (h if horizontal else w) * 3 > 65535:  # one workgroup row per (line, channel)
            return None
        plan['desqueeze'] = (n_out, horizontal, weights)
        extra += [h * w * 12] * (2 if weights is not None else 1)   # blur + coef
        cur_h, cur_w = (h, n_out) if horizontal else (n_out, w)
        extra.append(cur_h * cur_w * 12)
    angle = float(getattr(p, 'skew_deg', 0.0))
    if angle != 0.0:
        plan['skew'] = _skew_split(cur_h, cur_w, angle)
        extra.append(cur_h * cur_w * 12)
    if bool(getattr(p, 'crop', False)):
        plan['crop'] = _crop_ranges(cur_h, cur_w, p.crop_center, p.crop_size)
    plan['shape'] = (cur_h, cur_w)
    plan['extra'] = extra
    return plan


def encode_fused_geometry(impl: ScanGeometryGPU, encoder, owned: list, src,
                          h: int, w: int, plan: dict):
    """Record the plan's desqueeze -> skew into the caller's encoder, after
    its last stage. Returns (result buffer, min/max buffer). The crop is the
    caller's readback: rows plan['crop'][0:2] of the result, then the
    columns on the host."""
    mm = impl.encode_minmax(encoder, owned, src, h * w * 3)
    cur = src
    cur_h, cur_w = h, w
    if plan['desqueeze'] is not None:
        n_out, horizontal, weights = plan['desqueeze']
        cur = impl.encode_desqueeze(encoder, owned, cur, h, w, n_out, horizontal,
                                    weights, clip_buf=mm)
        cur_h, cur_w = (h, n_out) if horizontal else (n_out, w)
    if plan['skew'] is not None:
        cur = impl.encode_skew(encoder, owned, cur, cur_h, cur_w, 0, cur_h, 0, cur_h,
                               plan['skew'])
    return cur, mm


def desqueeze_dispatch(rgb, desqueeze: float, *, backend: str = "auto") -> Optional[np.ndarray]:
    """GPU drop-in for crop_resize.apply_desqueeze. float64 or None."""
    factor = float(desqueeze)
    arr = np.asarray(rgb)
    if factor in (0.0, 1.0, -1.0) or arr.ndim != 3 or arr.shape[2] != 3:
        return None
    if not np.all(np.isfinite(arr)) or not _gpu_requested(arr, backend):
        return None
    impl = _get_impl()
    if impl is None:
        return None
    h, w = arr.shape[:2]
    setup = _desqueeze_setup(factor, h, w)
    if setup is None:
        return None
    horizontal, n_in, n_out, weights = setup
    clip_lo, clip_hi = float(arr.min()), float(arr.max())
    n_lines = h if horizontal else w

    def band_bytes(nl):
        line_bytes = n_in * 12
        out_bytes = n_out * 12
        per = [nl * line_bytes] * (3 if weights is not None else 2) + [nl * out_bytes]
        return per

    # one workgroup row per (line, channel): stay under the 65535 dispatch cap
    band = _bands(n_lines, band_bytes, max_units=65535 // 3)
    if band is None:
        return None
    try:
        consume_device_error()
        pieces = []
        for s in range(0, n_lines, band):
            e = min(n_lines, s + band)
            chunk = arr[s:e] if horizontal else arr[:, s:e]
            pieces.append(impl.desqueeze_band(chunk, n_out, horizontal, weights, clip_lo, clip_hi))
            error = consume_device_error()
            if error is not None:
                logger.warning("scan geometry desqueeze trapped '%s'; discarded", error)
                return None
        out = np.concatenate(pieces, axis=0 if horizontal else 1)
        return out.astype(np.float64)
    except Exception:
        logger.exception("scan geometry desqueeze GPU dispatch failed; falling back to CPU")
        note_gpu_failure()
        return None


def _skew_matrix(rows: int, cols: int, angle: float) -> np.ndarray:
    """skimage.transform.rotate's own matrix (resize=False, center=None)."""

    center = np.array((cols, rows)) / 2.0 - 0.5
    tform = (SimilarityTransform(translation=-center)
             + SimilarityTransform(rotation=np.deg2rad(angle))
             + SimilarityTransform(translation=center))
    m = np.array(tform.params, dtype=np.float64)
    m[2] = (0, 0, 1)
    return m


def skew_dispatch(rgb, skew_deg: float, *, backend: str = "auto") -> Optional[np.ndarray]:
    """GPU drop-in for crop_resize.apply_skew. float64 or None."""
    angle = float(skew_deg)
    arr = np.asarray(rgb)
    if angle == 0.0 or arr.ndim != 3 or arr.shape[2] != 3:
        return None
    # non-finite input: skimage's output clip reads the input's min/max, so
    # its NaN handling is its own -- leave that case to it
    if not np.all(np.isfinite(arr)) or not _gpu_requested(arr, backend):
        return None
    impl = _get_impl()
    if impl is None:
        return None
    rows, cols = arr.shape[:2]
    m = _skew_matrix(rows, cols, angle)
    m_split = _skew_split(rows, cols, angle)

    def input_rows(r0, r1):
        # the rotated footprint of output rows [r0, r1) over all columns
        ys = [m[1, 0] * c + m[1, 1] * r + m[1, 2]
              for c in (0, cols - 1) for r in (r0, r1 - 1)]
        # one row of margin each way: the GPU's floor can land a hair below
        # an exactly-integer f64 coordinate
        lo = max(0, int(math.floor(min(ys))) - 1)
        hi = min(rows - 1, int(math.floor(max(ys))) + 2)
        if hi < lo:                      # footprint wholly outside: edge rows only
            lo = hi = min(max(lo, 0), rows - 1)
        return lo, hi

    def band_bytes(br):
        lo, hi = input_rows(0, br)
        worst_in = max(hi - lo + 1, 1) + br       # conservative: any band position
        return [min(worst_in, rows) * cols * 12, br * cols * 12]

    band = _bands(rows, band_bytes, max_units=65535 * 8)
    if band is None:
        return None
    try:
        consume_device_error()
        pieces = []
        for r0 in range(0, rows, band):
            r1 = min(rows, r0 + band)
            lo, hi = input_rows(r0, r1)
            pieces.append(impl.skew_band(arr[lo:hi + 1], rows, cols, lo, r0, r1 - r0, m_split))
            error = consume_device_error()
            if error is not None:
                logger.warning("scan geometry skew trapped '%s'; discarded", error)
                return None
        return np.concatenate(pieces, axis=0).astype(np.float64)
    except Exception:
        logger.exception("scan geometry skew GPU dispatch failed; falling back to CPU")
        note_gpu_failure()
        return None


def scan_geometry_dispatch(rgb, p, *, backend: str = "auto") -> Optional[np.ndarray]:
    """GPU drop-in for model.scan_finishing.apply_scan_geometry, in the same
    order (desqueeze -> skew -> crop): each resampler on the GPU, falling back
    to ITS OWN CPU function when declined (so a declined skew doesn't throw
    away a finished GPU desqueeze); crop is the same host slice. None only
    when no step ran on the GPU -- the caller then runs the CPU function."""

    if scan_geometry_is_identity(p):
        return None
    out = np.asarray(rgb)
    used_gpu = False
    desqueeze = float(getattr(p, 'desqueeze', 0.0))
    if desqueeze not in (0.0, 1.0, -1.0):
        gpu = desqueeze_dispatch(out, desqueeze, backend=backend)
        used_gpu |= gpu is not None
        out = gpu if gpu is not None else apply_desqueeze(out, desqueeze)
    skew = float(getattr(p, 'skew_deg', 0.0))
    if skew != 0.0:
        gpu = skew_dispatch(out, skew, backend=backend)
        used_gpu |= gpu is not None
        out = gpu if gpu is not None else apply_skew(out, skew)
    if not used_gpu:
        return None
    if bool(getattr(p, 'crop', False)):
        out = crop_image(out, center=tuple(p.crop_center), size=tuple(p.crop_size))
    return out
