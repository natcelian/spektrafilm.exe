"""Phase 6A: wgpu display canvas (feature-flagged beside napari).

Two layers, split for testability:

- :class:`GPUImageRenderer` — pure wgpu: uploads an RGB(A) numpy buffer as a
  texture and renders it as a bicubic-sampled quad (NegPy's gpu_widget shader,
  verbatim math) with fit-to-viewport + zoom/pan uniforms, into ANY target
  texture view. Headless-testable: ``render_to_array`` draws into an offscreen
  rgba8unorm texture and reads it back — at 1:1 fit the bicubic kernel is an
  exact identity, so display parity is asserted byte-for-byte.
- :class:`GPUCanvasWidget` — the PySide6 shell: a rendercanvas.pyside6
  RenderCanvas inside a QWidget, driving the renderer at the swapchain, with
  wheel zoom (anchored), drag pan, double-click reset, and background color.

The widget swaps in behind the ``SPEKTRAFILM_WGPU_CANVAS`` env flag without
removing napari (Phase 6C does the removal). It reuses the compute
:class:`spektrafilm.gpu.device.GPUDevice` singleton — one device for compute
AND display, which the 6D resident path requires.
"""

from __future__ import annotations

import logging
import struct
from typing import Any, Optional

import numpy as np
import math
from qtpy import QtCore, QtGui
from spektrafilm_gui import theme_palette as _palette

_canvas_logger = logging.getLogger("spektrafilm.gui.canvas")

FLAG_ENV = 'SPEKTRAFILM_WGPU_CANVAS'

# Composition guide overlays (Ctrl+O cycles): drawn by the interaction overlay
# over the displayed image rect, following zoom/pan.
GUIDE_MODES = ('off', 'grid', 'thirds', 'golden', 'diagonals', 'triangle', 'golden_spiral', 'center')

#: `.cropov .ruler { top: 10px; width: 170px; height: 7px }` and its
#: `repeating-linear-gradient(..., 0 1px, transparent 1px 10px)` — one tick
#: every 10px. `.ruler-c { top: 7px; height: 13px }`.
RULER_WIDTH_PX = 170
RULER_HEIGHT_PX = 7
RULER_TOP_PX = 10
RULER_TICK_GAP_PX = 10
RULER_MARK_PX = 13
#: How far the scale travels per degree. One tick per degree reads directly
#: and puts ±8.5° either side of the mark in the page's 170 — the range you
#: actually straighten a horizon in. The field itself allows ±45; the ruler
#: is a fine adjustment beside it, not a replacement for it.
RULER_PITCH_PX = 10.0
_PHI_LO = 1.0 - 1.0 / 1.618033988749895   # 0.381966...
_PHI_HI = 1.0 / 1.618033988749895         # 0.618033...


def guide_lines(mode: str, rect: tuple[float, float, float, float], orient: int = 0):
    """Line segments ((x0, y0), (x1, y1)) for a composition guide over the
    image rect (x, y, w, h) in widget px. Pure function (testable headless).

    - grid:      uniform 4x4 grid (lines at 1/4, 1/2, 3/4).
    - thirds:    rule-of-thirds grid (lines at 1/3 and 2/3).
    - golden:    golden-ratio grid (lines at 1-1/phi and 1/phi).
    - diagonals: the two main diagonals plus the four 45-degree corner
                 diagonals of the 'diagonal method'.
    - triangle:  main diagonal plus perpendiculars from the other two corners
                 (Lightroom's 'Triangle').
    - golden_spiral: a phi-growth logarithmic spiral (the golden / Fibonacci
                 spiral) anchored at the golden-section 'eye', fit to the rect.
    - center:    center cross.
    """
    x, y, w, h = rect
    if mode == 'thirds':
        fractions = (1.0 / 3.0, 2.0 / 3.0)
    elif mode == 'grid':
        fractions = (0.25, 0.5, 0.75)
    elif mode == 'golden':
        fractions = (_PHI_LO, _PHI_HI)
    elif mode == 'center':
        fractions = (0.5,)
    elif mode == 'diagonals':
        m = min(w, h)
        lines = [
            ((x, y), (x + w, y + h)),
            ((x + w, y), (x, y + h)),
        ]
        if abs(w - h) > 1e-6:  # 45-deg corner diagonals differ from the mains
            lines += [
                ((x, y), (x + m, y + m)),
                ((x + w, y), (x + w - m, y + m)),
                ((x, y + h), (x + m, y + h - m)),
                ((x + w, y + h), (x + w - m, y + h - m)),
            ]
        return lines
    elif mode == 'triangle':
        dd = w * w + h * h
        if dd <= 1e-12:
            return []
        t_a = (w * w) / dd
        t_b = (h * h) / dd
        if orient % 2 == 0:   # main diagonal TL->BR; perpendiculars from TR and BL
            return [
                ((x, y), (x + w, y + h)),
                ((x + w, y), (x + t_a * w, y + t_a * h)),
                ((x, y + h), (x + t_b * w, y + t_b * h)),
            ]
        # anti-diagonal TR->BL; perpendiculars from TL and BR
        return [
            ((x + w, y), (x, y + h)),
            ((x, y), (x + w - t_a * w, y + t_a * h)),
            ((x + w, y + h), (x + w - t_b * w, y + t_b * h)),
        ]
    elif mode == 'golden_spiral':
        return _golden_spiral_lines(x, y, w, h, orient)
    else:
        return []
    lines = []
    for f in fractions:
        lines.append(((x + f * w, y), (x + f * w, y + h)))   # vertical
        lines.append(((x, y + f * h), (x + w, y + f * h)))   # horizontal
    return lines


def _golden_spiral_lines(x, y, w, h, orient=0):
    """Polyline approximation of the golden (Fibonacci) spiral: a logarithmic
    spiral that grows by phi every quarter turn, anchored at the golden-section
    'eye' of the rect. The raw log-spiral only fits a golden-ratio rectangle,
    so the polyline is affinely FIT to the image rect (Lightroom-style stretch)
    — it touches the frame and never leaves it, at any aspect ratio. `orient`
    (0..3, Shift+O) picks the corner by mirroring the fitted spiral: 0 none,
    1 horizontal, 2 both (180 deg), 3 vertical."""
    if w <= 1e-6 or h <= 1e-6:
        return []
    phi = 1.618033988749895
    inv_phi = 1.0 / phi
    ex, ey = x + w * inv_phi, y + h * inv_phi     # golden-section eye
    r0 = math.hypot(x - ex, y - ey)               # reach toward the (x, y) corner
    if r0 <= 1e-6:
        return []
    theta0 = math.atan2(y - ey, x - ex)
    b = math.log(phi) / (math.pi / 2.0)           # phi per quarter turn
    pts = []
    steps = 256
    span = 4.25 * 2.0 * math.pi                   # ~4 turns inward
    for i in range(steps + 1):
        t = (i / steps) * span
        r = r0 * math.exp(-b * t)
        if r < 0.4:
            break
        ang = theta0 - t                          # wind inward
        pts.append((ex + r * math.cos(ang), ey + r * math.sin(ang)))
    if len(pts) < 2:
        return []
    # Fit the polyline's bounding box exactly onto the rect: in-frame at any
    # aspect (the log-spiral overshoots non-golden rects between tangencies).
    min_x = min(p[0] for p in pts)
    max_x = max(p[0] for p in pts)
    min_y = min(p[1] for p in pts)
    max_y = max(p[1] for p in pts)
    scale_x = w / max(max_x - min_x, 1e-9)
    scale_y = h / max(max_y - min_y, 1e-9)
    pts = [(x + (px - min_x) * scale_x, y + (py - min_y) * scale_y)
           for px, py in pts]
    o = orient % 4
    flip_h = o in (1, 2)
    flip_v = o in (2, 3)
    if flip_h or flip_v:
        cx, cy = x + w / 2.0, y + h / 2.0
        pts = [((2.0 * cx - px) if flip_h else px,
                (2.0 * cy - py) if flip_v else py) for px, py in pts]
    return [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]


_SHADER = """
struct RenderUniforms {
    rect: vec4<f32>,
    transform: vec4<f32> // x: zoom, y: pan_x, z: pan_y
};
@group(0) @binding(1) var<uniform> params: RenderUniforms;

struct VertexOutput {
    @builtin(position) pos: vec4<f32>,
    @location(0) uv: vec2<f32>,
};

@vertex
fn vs_main(@builtin(vertex_index) in_vertex_index: u32) -> VertexOutput {
    var positions = array<vec2<f32>, 4>(
        vec2<f32>(-1.0, 1.0), vec2<f32>(1.0, 1.0),
        vec2<f32>(-1.0, -1.0), vec2<f32>(1.0, -1.0)
    );
    var uvs = array<vec2<f32>, 4>(
        vec2<f32>(0.0, 0.0), vec2<f32>(1.0, 0.0),
        vec2<f32>(0.0, 1.0), vec2<f32>(1.0, 1.0)
    );

    let ndc_pos = positions[in_vertex_index];
    let zoom = params.transform.x;
    let pan = params.transform.yz;

    let base_pos = vec2<f32>(
        (ndc_pos.x + 1.0) * 0.5 * params.rect.z + params.rect.x,
        (ndc_pos.y - 1.0) * 0.5 * params.rect.w + params.rect.y
    );
    let final_pos = base_pos * zoom + vec2<f32>(pan.x, -pan.y) * 2.0;

    var out: VertexOutput;
    out.pos = vec4<f32>(final_pos, 0.0, 1.0);
    out.uv = uvs[in_vertex_index];
    return out;
}

@group(0) @binding(0) var tex: texture_2d<f32>;

fn cubic(v: f32) -> f32 {
    let a = 0.5;
    let x = abs(v);
    if (x < 1.0) {
        return 1.5 * x * x * x - 2.5 * x * x + 1.0;
    } else if (x < 2.0) {
        return -0.5 * x * x * x + 2.5 * x * x - 4.0 * x + 2.0;
    }
    return 0.0;
}

fn textureSampleBicubic(uv: vec2<f32>) -> vec4<f32> {
    let dims = textureDimensions(tex);
    let fdims = vec2<f32>(f32(dims.x), f32(dims.y));

    let pixel = uv * fdims - 0.5;
    let ipos = floor(pixel);
    let fpos = fract(pixel);

    var col = vec4<f32>(0.0);
    for (var y = -1; y <= 2; y++) {
        for (var x = -1; x <= 2; x++) {
            let offset = vec2<f32>(f32(x), f32(y));
            let coord = vec2<i32>(ipos + offset);
            let c = clamp(coord, vec2<i32>(0), vec2<i32>(dims) - 1);
            let weight = cubic(f32(x) - fpos.x) * cubic(f32(y) - fpos.y);
            col += textureLoad(tex, c, 0) * weight;
        }
    }
    return col;
}

@fragment
fn fs_main(in: VertexOutput) -> @location(0) vec4<f32> {
    return textureSampleBicubic(in.uv);
}
"""


_ROTATE_CURSOR = None


# ------------------------------------------------------------ loupe glass
def _gaussian_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def paint_loupe_shadow(painter, cx: float, cy: float, radius: float) -> None:
    """`.loupe .glass { box-shadow: 0 10px 24px oklch(0 0 0 / .32) }` (spec
    D1): the disc convolved with a Gaussian (sigma = blur / 2), 10px down,
    drawn as a radial gradient under the glass (B11, 2026-09-26)."""

    sigma = 12.0
    reach = radius + 3 * sigma
    centre = QtCore.QPointF(cx, cy + 10.0)
    gradient = QtGui.QRadialGradient(centre, reach)
    for step in range(17):
        d = reach * step / 16.0
        shade = QtGui.QColor(0, 0, 0)
        shade.setAlphaF(0.32 * _gaussian_cdf((radius - d) / sigma))
        gradient.setColorAt(d / reach, shade)
    painter.save()
    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(QtGui.QBrush(gradient))
    painter.drawEllipse(centre, reach, reach)
    painter.restore()


def paint_loupe_glass(painter, cx: float, cy: float, radius: float,
                      *, dragging: bool = False) -> None:
    """What sits ON the glass (spec D1, `.loupe .glass::after`): a light
    inner vignette (`inset 0 0 20px 3px oklch(0 0 0 / .35)`), a 1px top
    highlight (`inset 0 1px 1px oklch(1 0 0 / .25)`), and -- only while
    dragging -- `outline: 2px solid var(--accent); outline-offset: 2px`."""


    centre = QtCore.QPointF(cx, cy)
    painter.save()
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    clip = QtGui.QPainterPath()
    clip.addEllipse(centre, radius, radius)
    painter.setClipPath(clip)
    painter.setPen(QtCore.Qt.NoPen)
    # the inset shadow, spread 3px, blur 20px (sigma 10), from the rim in
    sigma = 10.0
    vignette = QtGui.QRadialGradient(centre, radius)
    for step in range(17):
        d = radius * step / 16.0
        shade = QtGui.QColor(0, 0, 0)
        shade.setAlphaF(0.35 * _gaussian_cdf((d - (radius - 3.0)) / sigma))
        vignette.setColorAt(d / radius, shade)
    painter.setBrush(QtGui.QBrush(vignette))
    painter.drawEllipse(centre, radius, radius)
    # the highlight: a white rim shifted 1px down, so only its top shows
    light = QtGui.QColor(255, 255, 255)
    light.setAlphaF(0.25)
    ring = QtGui.QPainterPath()
    ring.addEllipse(centre, radius, radius)
    inner = QtGui.QPainterPath()
    inner.addEllipse(QtCore.QPointF(cx, cy + 1.0), radius, radius)
    painter.fillPath(ring.subtracted(inner), light)
    painter.setClipping(False)
    if dragging:
        painter.setBrush(QtCore.Qt.NoBrush)
        # read through the palette MODULE: the accent is choosable at runtime
        painter.setPen(QtGui.QPen(QtGui.QColor(_palette.ACCENT_COLOR_TEXT), 2.0))
        painter.drawEllipse(centre, radius + 3.0, radius + 3.0)
    painter.restore()


def rotate_cursor():
    """The cursor over the paper in crop mode, where a drag turns the image
    (2026-09-19): a curved double arrow, as other editors show there. Painted
    once, white on a dark outline so it reads over the paper and the picture."""
    global _ROTATE_CURSOR
    if _ROTATE_CURSOR is not None:
        return _ROTATE_CURSOR

    size = 24
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    arc = QtCore.QRectF(5, 5, 14, 14)
    ends = ((QtCore.QPointF(19.0, 12.0), (-3.5, -3.0), (3.0, -3.0)),   # arrow at 0 deg
            (QtCore.QPointF(12.0, 19.0), (3.0, 3.0), (3.0, -3.0)))     # arrow at 270 deg
    for colour, width in ((QtGui.QColor(0, 0, 0, 200), 3.5), (QtGui.QColor(255, 255, 255), 1.5)):
        pen = QtGui.QPen(colour, width)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        painter.setPen(pen)
        painter.drawArc(arc, 0 * 16, 270 * 16)
        for tip, (ax, ay), (bx, by) in ends:
            painter.drawLine(tip, tip + QtCore.QPointF(ax, ay))
            painter.drawLine(tip, tip + QtCore.QPointF(bx, by))
    painter.end()
    _ROTATE_CURSOR = QtGui.QCursor(pixmap, size // 2, size // 2)
    return _ROTATE_CURSOR


def _clamp_scissor(x: float, y: float, width: float, height: float,
                   view_w: float, view_h: float):
    """A scissor rect inside the viewport, or None if nothing is left.

    wgpu REJECTS a scissor that leaves the render target, and a zero-width
    one is a validation error rather than a no-op -- so a seam dragged to
    either end would take the canvas down instead of showing one image.
    """
    x0 = max(0, int(round(min(x, view_w))))
    y0 = max(0, int(round(min(y, view_h))))
    x1 = max(0, int(round(min(x + width, view_w))))
    y1 = max(0, int(round(min(y + height, view_h))))
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1 - x0, y1 - y0)


def compute_canvas_layout(viewport: tuple[float, float],
                          image_size: tuple[int, int],
                          compare_size: Optional[tuple[int, int]] = None,
                          gap_world: float = 0.05,
                          split: bool = False):
    """Pure layout math (testable without a GPU): fit the image — or the
    side-by-side union image+compare — into the viewport, centered.

    World units: each image is normalized by ITS OWN long edge (napari's
    convention, so the compare layer matches the napari path). Returns a dict
    with per-quad rects in WIDGET PIXELS at zoom=1/pan=0:
    {'image': (x, y, w, h), 'compare': (x, y, w, h) | None, 'scale': px_per_world}.

    Phase 18G, `split=True`: the design's Split view is ONE frame with the
    two images on top of each other and a seam between them, not two frames
    beside each other. Both quads then get the SAME rect -- the fit for the
    live image alone -- and `draw` scissors each to its side of the seam.
    Comparing two images that are not the same size on screen is not
    comparing them.
    """
    if split and compare_size is not None:
        single = compute_canvas_layout(viewport, image_size, None, gap_world)
        return {'image': single['image'], 'compare': single['image'],
                'scale': single['scale']}
    view_w, view_h = float(viewport[0]), float(viewport[1])
    img_w, img_h = float(image_size[0]), float(image_size[1])
    img_long = max(img_w, img_h, 1.0)
    out_w, out_h = img_w / img_long, img_h / img_long
    if compare_size is None:
        total_w, total_h = out_w, out_h
    else:
        cmp_w_px, cmp_h_px = float(compare_size[0]), float(compare_size[1])
        cmp_long = max(cmp_w_px, cmp_h_px, 1.0)
        cmp_w, cmp_h = cmp_w_px / cmp_long, cmp_h_px / cmp_long
        total_w = out_w + gap_world + cmp_w
        total_h = max(out_h, cmp_h)
    scale = min(view_w / total_w, view_h / total_h)
    origin_x = (view_w - total_w * scale) / 2.0
    center_y = view_h / 2.0
    image_rect = (origin_x, center_y - out_h * scale / 2.0, out_w * scale, out_h * scale)
    compare_rect = None
    if compare_size is not None:
        compare_rect = (origin_x + (out_w + gap_world) * scale,
                        center_y - cmp_h * scale / 2.0,
                        cmp_w * scale, cmp_h * scale)
    return {'image': image_rect, 'compare': compare_rect, 'scale': scale}


def apply_zoom_pan_to_rect(rect, viewport, zoom: float, pan) -> tuple[float, float, float, float]:
    """Replicate the shader transform in widget pixels: NDC' = NDC*zoom +
    (pan_x, -pan_y)*2. Returns the transformed rect (x, y, w, h)."""
    view_w, view_h = float(viewport[0]), float(viewport[1])
    x, y, w, h = rect
    # rect corners to NDC (y-down widget px -> y-up NDC)
    def to_ndc(px, py):
        return (px / view_w) * 2.0 - 1.0, 1.0 - (py / view_h) * 2.0
    def to_px(nx, ny):
        return (nx + 1.0) * 0.5 * view_w, (1.0 - ny) * 0.5 * view_h
    x0, y0 = to_ndc(x, y)
    x1, y1 = to_ndc(x + w, y + h)
    zx0 = x0 * zoom + pan[0] * 2.0
    zy0 = y0 * zoom - pan[1] * 2.0
    zx1 = x1 * zoom + pan[0] * 2.0
    zy1 = y1 * zoom - pan[1] * 2.0
    px0, py0 = to_px(zx0, zy0)
    px1, py1 = to_px(zx1, zy1)
    return px0, py0, px1 - px0, py1 - py0


def _to_rgba8(image: np.ndarray) -> np.ndarray:
    """Any RGB(A) buffer (float 0-1 or uint8) -> contiguous (H, W, 4) uint8."""
    data = np.asarray(image)
    if data.ndim != 3 or data.shape[2] not in (3, 4):
        raise ValueError(f'expected (H, W, 3|4) image, got {data.shape}')
    if np.issubdtype(data.dtype, np.floating):
        data = (np.clip(data, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
    elif data.dtype != np.uint8:
        data = np.clip(data, 0, 255).astype(np.uint8)
    if data.shape[2] == 3:
        rgba = np.empty((*data.shape[:2], 4), dtype=np.uint8)
        rgba[..., :3] = data
        rgba[..., 3] = 255
        data = rgba
    return np.ascontiguousarray(data)


class GPUImageRenderer:
    """Pure-wgpu image quad renderer (no Qt). One per target format."""

    def __init__(self, device: Any, target_format: str):
        import wgpu

        self._wgpu = wgpu
        self.device = device
        self.target_format = target_format
        self._texture = None
        self._compare_texture = None
        self.image_size: tuple[int, int] = (1, 1)
        self.compare_size: Optional[tuple[int, int]] = None
        #: Where the Split seam sits, 0..1 across the image. None = the
        #: side-by-side compare the app has always had.
        self.split_position: Optional[float] = None

        self.uniform_buffer = device.create_buffer(label="gpu_canvas:uniform_buffer", 
            size=32, usage=wgpu.BufferUsage.UNIFORM | wgpu.BufferUsage.COPY_DST)
        self.compare_uniform_buffer = device.create_buffer(label="gpu_canvas:compare_uniform_buffer", 
            size=32, usage=wgpu.BufferUsage.UNIFORM | wgpu.BufferUsage.COPY_DST)
        shader = device.create_shader_module(code=_SHADER)
        self.bind_group_layout = device.create_bind_group_layout(entries=[
            {
                'binding': 0,
                'visibility': wgpu.ShaderStage.FRAGMENT,
                'texture': {
                    'sample_type': wgpu.TextureSampleType.unfilterable_float,
                    'view_dimension': wgpu.TextureViewDimension.d2,
                },
            },
            {
                'binding': 1,
                'visibility': wgpu.ShaderStage.VERTEX | wgpu.ShaderStage.FRAGMENT,
                'buffer': {'type': wgpu.BufferBindingType.uniform, 'min_binding_size': 32},
            },
        ])
        self.pipeline = device.create_render_pipeline(
            layout=device.create_pipeline_layout(bind_group_layouts=[self.bind_group_layout]),
            vertex={'module': shader, 'entry_point': 'vs_main'},
            primitive={
                'topology': wgpu.PrimitiveTopology.triangle_strip,
                'strip_index_format': wgpu.IndexFormat.uint32,
            },
            fragment={'module': shader, 'entry_point': 'fs_main',
                      'targets': [{'format': target_format}]},
        )

    # -- content -------------------------------------------------------------
    def _upload_device_frame(self, frame, texture, current_size):
        """A spektrafilm.gpu.display.DisplayFrame: packed RGBA8 already on
        THIS device (the canvas shares the compute device), rows padded to the
        256-byte multiple a buffer->texture copy needs. Copied device to
        device -- no host pixels (2026-09-19)."""
        wgpu = self._wgpu
        width, height = int(frame.w), int(frame.h)
        # 2026-09-19; see design/DECISIONS.md#d-gpu_canvas-548
        row_bytes = int(frame.stride) * 4
        if (width <= 0 or height <= 0 or frame.stride < width or row_bytes % 256
                or int(frame.buffer.size) < row_bytes * height):
            raise ValueError('device frame geometry does not fit its buffer')
        if texture is None or current_size != (width, height):
            if texture is not None:
                texture.destroy()
            texture = self.device.create_texture(label="gpu_canvas:texture",
                size=(width, height, 1),
                format=wgpu.TextureFormat.rgba8unorm,
                usage=wgpu.TextureUsage.TEXTURE_BINDING | wgpu.TextureUsage.COPY_DST,
            )
        encoder = self.device.create_command_encoder()
        encoder.copy_buffer_to_texture(
            {'buffer': frame.buffer, 'offset': 0,
             'bytes_per_row': int(frame.stride) * 4, 'rows_per_image': height},
            {'texture': texture, 'mip_level': 0, 'origin': (0, 0, 0)},
            (width, height, 1),
        )
        self.device.queue.submit([encoder.finish()])
        return texture, (width, height)

    def _upload(self, image: np.ndarray, texture, current_size):
        if getattr(image, 'on_device', False) and hasattr(image, 'stride'):
            try:
                return self._upload_device_frame(image, texture, current_size)
            except Exception:
                pass        # fall through: read it back and upload as any array
        wgpu = self._wgpu
        rgba = _to_rgba8(image)
        height, width = rgba.shape[:2]
        if texture is None or current_size != (width, height):
            if texture is not None:
                texture.destroy()
            texture = self.device.create_texture(label="gpu_canvas:texture", 
                size=(width, height, 1),
                format=wgpu.TextureFormat.rgba8unorm,
                usage=wgpu.TextureUsage.TEXTURE_BINDING | wgpu.TextureUsage.COPY_DST,
            )
        self.device.queue.write_texture(
            {'texture': texture},
            rgba,
            {'bytes_per_row': width * 4, 'rows_per_image': height},
            (width, height, 1),
        )
        return texture, (width, height)

    def set_image(self, image: np.ndarray) -> None:
        self._texture, self.image_size = self._upload(image, self._texture, self.image_size)

    def set_compare_image(self, image: np.ndarray) -> None:
        self._compare_texture, self.compare_size = self._upload(
            image, self._compare_texture, self.compare_size)

    def clear_compare_image(self) -> None:
        if self._compare_texture is not None:
            self._compare_texture.destroy()
        self._compare_texture = None
        self.compare_size = None

    def clear_image(self) -> None:
        if self._texture is not None:
            self._texture.destroy()
        self._texture = None
        self.image_size = (1, 1)

    @property
    def has_image(self) -> bool:
        return self._texture is not None

    def layout(self, viewport: tuple[float, float]):
        """Current zoom-1 layout (widget px) — shared with the interaction
        code so the mouse math and the shader agree by construction."""
        return compute_canvas_layout(
            viewport, self.image_size,
            self.compare_size if self._compare_texture is not None else None,
            split=self.split_position is not None)

    # -- drawing -------------------------------------------------------------
    def draw(self, target_view: Any, viewport: tuple[int, int], *,
             zoom: float = 1.0, pan: tuple[float, float] = (0.0, 0.0),
             background: tuple[float, float, float] = (0.02, 0.02, 0.02)) -> None:
        wgpu = self._wgpu
        encoder = self.device.create_command_encoder()
        render_pass = encoder.begin_render_pass(color_attachments=[{
            'view': target_view,
            'load_op': wgpu.LoadOp.clear,
            'store_op': wgpu.StoreOp.store,
            'clear_value': (*background, 1),
        }])
        if self._texture is not None:
            view_w, view_h = float(viewport[0]), float(viewport[1])
            layout = self.layout(viewport)

            def _pack_rect(rect):
                x, y, w, h = rect
                return struct.pack(
                    'ffffffff',
                    (x / view_w) * 2.0 - 1.0,
                    1.0 - (y / view_h) * 2.0,
                    (w / view_w) * 2.0,
                    (h / view_h) * 2.0,
                    float(zoom), float(pan[0]), float(pan[1]), 0.0,
                )

            def _quad(texture, uniform_buffer, rect):
                self.device.queue.write_buffer(uniform_buffer, 0, _pack_rect(rect))
                bind_group = self.device.create_bind_group(
                    layout=self.bind_group_layout,
                    entries=[
                        {'binding': 0, 'resource': texture.create_view()},
                        {'binding': 1, 'resource': {'buffer': uniform_buffer,
                                                    'offset': 0, 'size': 32}},
                    ])
                render_pass.set_pipeline(self.pipeline)
                render_pass.set_bind_group(0, bind_group)
                render_pass.draw(4, 1, 0, 0)

            split = (self.split_position
                     if self._compare_texture is not None else None)
            if split is None:
                _quad(self._texture, self.uniform_buffer, layout['image'])
                if (self._compare_texture is not None
                        and layout['compare'] is not None):
                    _quad(self._compare_texture, self.compare_uniform_buffer,
                          layout['compare'])
            else:
                # SPLIT: one rect, two draws, each scissored to its side of
                # the seam. The seam is measured across the IMAGE, not the
                # viewport, so it stays on the picture when you pan or zoom.
                x, y, w, h = layout['image']
                seam = x + w * max(0.0, min(1.0, float(split)))
                left = _clamp_scissor(x, y, seam - x, h, view_w, view_h)
                right = _clamp_scissor(seam, y, x + w - seam, h,
                                       view_w, view_h)
                if left is not None:
                    render_pass.set_scissor_rect(*left)
                    _quad(self._texture, self.uniform_buffer, layout['image'])
                if right is not None:
                    render_pass.set_scissor_rect(*right)
                    _quad(self._compare_texture, self.compare_uniform_buffer,
                          layout['compare'])
                render_pass.set_scissor_rect(0, 0, int(view_w), int(view_h))
        render_pass.end()
        self.device.queue.submit([encoder.finish()])

    def render_to_array(self, width: int, height: int, *,
                        zoom: float = 1.0, pan: tuple[float, float] = (0.0, 0.0),
                        background: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> np.ndarray:
        """Headless draw into an offscreen rgba8unorm texture; returns
        (height, width, 4) uint8 — the display-parity instrument."""
        wgpu = self._wgpu
        target = self.device.create_texture(label="gpu_canvas:target", 
            size=(width, height, 1),
            format=wgpu.TextureFormat.rgba8unorm,
            usage=wgpu.TextureUsage.RENDER_ATTACHMENT | wgpu.TextureUsage.COPY_SRC,
        )
        self.draw(target.create_view(), (width, height),
                  zoom=zoom, pan=pan, background=background)
        bytes_per_row = (width * 4 + 255) // 256 * 256  # buffer copies need 256 alignment
        readback = self.device.create_buffer(label="gpu_canvas:readback", 
            size=bytes_per_row * height,
            usage=wgpu.BufferUsage.COPY_DST | wgpu.BufferUsage.MAP_READ,
        )
        encoder = self.device.create_command_encoder()
        encoder.copy_texture_to_buffer(
            {'texture': target},
            {'buffer': readback, 'bytes_per_row': bytes_per_row, 'rows_per_image': height},
            (width, height, 1),
        )
        self.device.queue.submit([encoder.finish()])
        readback.map_sync(mode=wgpu.MapMode.READ)
        try:
            raw = np.frombuffer(readback.read_mapped(), dtype=np.uint8)
        finally:
            readback.unmap()
        rows = raw.reshape(height, bytes_per_row)[:, :width * 4]
        result = rows.reshape(height, width, 4).copy()
        target.destroy()
        # The readback buffer too -- it was sized `bytes_per_row * height` and
        # was being left to the garbage collector, which for a wgpu buffer
        # means the device holds it. Destroying a per-dispatch temporary AFTER
        # its readback is the safe case (nothing else ever saw this one), so
        # this does not need `retire_buffer`.
        readback.destroy()
        return result


class GPUCanvasWidget:
    """PySide6 shell around GPUImageRenderer (constructed lazily so importing
    this module never requires Qt). Call :func:`create_gpu_canvas_widget`."""


def create_cpu_canvas_widget(parent=None):
    """Software display fallback for machines with NO compatible GPU (Phase 6
    non-negotiable: the app must still launch and run). A fit-scaled QLabel
    with the same public API subset the controller drives; interaction is
    reduced (no pan/zoom, crop by numeric entry only)."""
    from qtpy import QtCore, QtGui, QtWidgets

    class _CpuCanvas(QtWidgets.QLabel):
        #: see `_Widget.view_listener`
        view_listener = None

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setAlignment(QtCore.Qt.AlignCenter)
            self.setStyleSheet('background-color: #070707;')
            self.setMinimumSize(64, 64)
            self._pixmap = None
            self.zoom = 1.0
            self.pan = [0.0, 0.0]
            self.crop_mode = 'off'
            self.guide_mode = 'off'
            self.guide_orient = 0
            self.content_inset = (0.0, 0.0)
            self.crop_frame_uv = None
            self.dust_marks = []
            self.dust_zones = []
            self.crop_angle_deg = 0.0
            self.renderer = SimpleNamespaceRenderer()

        def set_image(self, image) -> None:
            rgba = _to_rgba8(image)
            height, width = rgba.shape[:2]
            self.renderer.image_size = (width, height)
            qimage = _qimage_from_rgba(rgba)
            self._pixmap = QtGui.QPixmap.fromImage(qimage)
            self._rescale()

        def clear_image(self) -> None:
            self._pixmap = None
            self.renderer.image_size = (1, 1)
            self.clear()

        def set_compare(self, image) -> None:  # single-image fallback
            return None

        def clear_compare(self) -> None:
            return None

        def set_background_hex(self, color: str) -> None:
            self.setStyleSheet(f'background-color: {color};')

        def reset_view(self) -> None:
            self._rescale()

        def set_zoom(self, zoom: float) -> None:
            return None

        def set_zoom_percent(self, percent: float) -> None:
            return None

        def arm_loupe(self, on_move=None, on_lock=None, on_resize=None) -> None:
            pass                     # interactive loupe needs the GPU canvas

        def loupe_armed(self) -> bool:
            return False

        def set_loupe_view(self, rgba, *, dragging: bool = False) -> None:
            pass

        def arm_strip_click(self, on_click=None) -> None:
            pass                     # strip adopt needs the GPU canvas

        def set_dust_marks(self, marks) -> None:
            self.dust_marks = list(marks)

        def set_dust_zones(self, zones) -> None:
            self.dust_zones = list(zones)

        def arm_zone_drag(self, on_zone=None) -> None:
            pass                     # drawing a circle needs the GPU canvas

        def arm_crop_tool(self, mode: str, **handlers) -> None:
            self.crop_mode = 'off'   # interactive crop needs the GPU canvas

        def set_crop_frame(self, rect_uv) -> None:
            return None              # the LR frame needs the GPU canvas overlay

        def set_crop_angle(self, degrees: float) -> None:
            self.crop_angle_deg = float(degrees or 0.0)

        def image_rect_px(self):
            """The scaled pixmap's rect inside the label, centred.

            The fallback has no zoom or pan, so this is the fit rect -- but
            it still has an ASPECT, which is the half of the problem that
            bites hardest: a portrait photo in a landscape board.
            """
            pixmap = getattr(self, '_pixmap', None)
            if pixmap is None or pixmap.isNull():
                return None
            ratio = max(pixmap.devicePixelRatio(), 1.0)
            image_w = pixmap.width() / ratio
            image_h = pixmap.height() / ratio
            if image_w <= 0 or image_h <= 0:
                return None
            scale = min(self.width() / image_w, self.height() / image_h)
            width, height = image_w * scale, image_h * scale
            return ((self.width() - width) / 2.0, (self.height() - height) / 2.0,
                    width, height)

        def set_guide_mode(self, mode: str) -> None:
            return None              # guides need the GPU canvas overlay

        def cycle_guide(self) -> str:
            return 'off'

        def set_guide_orient(self, orient: int) -> None:
            return None

        def flip_guide(self) -> int:
            return 0

        def set_content_inset(self, fx: float, fy: float) -> None:
            self.content_inset = (max(float(fx), 0.0), max(float(fy), 0.0))

        def request_redraw(self) -> None:
            self._rescale()

        def resizeEvent(self, event) -> None:  # noqa: N802
            super().resizeEvent(event)
            self._rescale()

        def _rescale(self) -> None:
            if self._pixmap is not None:
                self.setPixmap(self._pixmap.scaled(
                    self.size(), QtCore.Qt.KeepAspectRatio,
                    QtCore.Qt.SmoothTransformation))
            # The paper board is cut to the image here too -- the fallback
            # has no zoom, but it does have a new image and a new size.
            listener = getattr(self, 'view_listener', None)
            if callable(listener):
                listener()

    class SimpleNamespaceRenderer:
        image_size = (1, 1)
        compare_size = None

    def _qimage_from_rgba(rgba):
        height, width = rgba.shape[:2]
        return QtGui.QImage(bytes(rgba.data), width, height, width * 4,
                            QtGui.QImage.Format_RGBA8888).copy()

    return _CpuCanvas(parent)


def create_gpu_canvas_widget(parent=None):
    """Build the Qt widget (rendercanvas.pyside6), or None.

    2026-08-03; see design/DECISIONS.md#d-gpu_canvas-560

        ModuleNotFoundError: No module named 'rendercanvas'
          File "app.py", line 390, in build_main_window_for_app

    A missing optional dependency, a wgpu that will not import, a driver
    that refuses a surface -- all of them are "no GPU canvas here", which is
    the case the CPU fallback exists for. The golden rule is that the CPU
    path is always there; a hard import made it unreachable.
    """
    from qtpy import QtCore, QtWidgets

    try:
        from rendercanvas.pyside6 import RenderCanvas
    except Exception as exc:
        _canvas_logger.warning(
            'wgpu canvas unavailable (%s: %s); using the CPU canvas',
            type(exc).__name__, exc)
        return None

    from spektrafilm.gpu.device import GPUDevice

    gpu = GPUDevice.get()
    if gpu.device is None or gpu.adapter is None:
        return None

    class _InteractionOverlay(QtWidgets.QWidget):
        """Transparent surface on top of the RenderCanvas (which consumes Qt
        mouse events itself — NegPy's CanvasOverlay pattern): handles wheel
        zoom / drag pan, and the crop rubber band when a tool is armed,
        painting the scrim + white rectangle."""

        def __init__(self, host):
            super().__init__(host)
            self._host = host
            self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
            self.setMouseTracking(True)
            self._pan_origin = None
            self._rubber = None      # (x0, y0, x1, y1) widget px while drawing
            self._zone = None        # (x0, y0, x1, y1) widget px while drawing a Defects circle
            self._move_origin = None
            self._frame_drag = None  # (hit, last_x, last_y) while dragging the LR frame
            self._line = None        # (x0, y0, x1, y1) widget px straighten line
            self._seam_drag = False  # dragging the Split seam (18G)
            self._skew_drag = None   # last pointer angle (deg) while turning the image
            # Phase 17B on the canvas chrome: the loupe circle is painted
            # HERE with QPainter (no wgpu device involvement at all).
            self._loupe_image = None  # QImage of the masked circle
            self._cursor = None       # (x, y) widget px of the loupe center

        # -- canvas-local coordinates -------------------------------------
        def canvas_offset(self):
            """Where the canvas's top-left sits in THIS widget's coordinates.

            2026-08-27; see design/DECISIONS.md#d-gpu_canvas-561

            Everything in this class still speaks canvas-local coordinates:
            the host hands out canvas-local pixels (`image_norm_to_widget`,
            `frame_rect_px`, `seam_x_px`), and the paint handlers translate
            by this once rather than each doing its own arithmetic.
            """
            parent = self.parentWidget()
            if parent is None or parent is self._host:
                return QtCore.QPoint(0, 0)
            return self._host.mapTo(parent, QtCore.QPoint(0, 0)) - self.pos()

        def _canvas_rect(self):
            """The canvas's rect in THIS widget's coordinates."""
            return QtCore.QRect(self.canvas_offset(), self._host.size())

        def _to_canvas(self, pos):
            """An event position, in canvas-local pixels."""
            offset = self.canvas_offset()
            return QtCore.QPointF(pos.x() - offset.x(), pos.y() - offset.y())

        def _outside_canvas(self, pos) -> bool:
            """Events over the SURROUND are not this overlay's.

            Growing the overlay to the room made it the widget under the
            paper as well as under the picture, and every handler below
            assumes it was clicked on the picture. Rather than teach six of
            them about the paper, the ones that act on the image decline
            there, exactly as they did when the overlay stopped at the
            canvas edge.
            """
            return not self._canvas_rect().contains(pos.toPoint())

        def _loupe_center(self):
            """Widget-px circle center: the pinned image position
            (re-projected, so it tracks pan/zoom) or the cursor."""
            locked = getattr(self._host, 'loupe_locked_uv', None)
            if locked is not None:
                return self._host.image_norm_to_widget(*locked)
            return self._cursor

        def loupe_logical_radius(self) -> float:
            if self._loupe_image is None:
                return 0.0
            ratio = max(self._loupe_image.devicePixelRatio(), 1.0)
            return self._loupe_image.width() / ratio / 2.0

        def set_loupe_view(self, rgba, *, dragging: bool = False) -> None:
            """RGBA uint8 circle (or None to clear), painted 1:1 widget
            pixels at the cursor — the honest 1:1 mapping. ``dragging``
            draws the accent outline outside the glass (spec D1)."""
            self._loupe_dragging = bool(dragging)
            if rgba is None:
                self._loupe_image = None
            else:
                import numpy as _np
                from qtpy import QtGui
                array = _np.ascontiguousarray(rgba)
                height, width = array.shape[:2]
                image = QtGui.QImage(
                    bytes(array.data), width, height, width * 4,
                    QtGui.QImage.Format_RGBA8888).copy()
                # The tile is rendered at PHYSICAL resolution: stamp the
                # device ratio so Qt paints it 1:1 device pixels (crisp on
                # scaled displays instead of a logical-px upscale).
                try:
                    image.setDevicePixelRatio(self.devicePixelRatioF())
                except (AttributeError, TypeError):
                    pass
                self._loupe_image = image
            self.update()

        # -- painting -------------------------------------------------------
        def paintEvent(self, event) -> None:  # noqa: N802
            frame_active = (self._host.crop_mode == 'frame'
                            and self._host.crop_frame_uv is not None)
            if (self._rubber is None and self._line is None and not frame_active
                    and self._host.guide_mode == 'off'
                    and self._loupe_image is None and not self._dust_drawn()):
                return
            offset = self.canvas_offset()
            loupe_center = self._loupe_center()
            if self._loupe_image is not None and loupe_center is not None:
                from qtpy import QtGui
                loupe_painter = QtGui.QPainter(self)
                # NO CLIP. This is the whole point of the room-sized
                # overlay: the circle may hang off the picture and onto the
                # paper, and it is drawn there rather than cut flat at the
                # canvas edge.
                loupe_painter.translate(offset)
                loupe_painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
                # Logical size (width() is PHYSICAL px once the device
                # ratio is stamped) so the circle centers on the cursor.
                ratio = max(self._loupe_image.devicePixelRatio(), 1.0)
                logical_w = self._loupe_image.width() / ratio
                logical_h = self._loupe_image.height() / ratio
                # 2026-09-26; see design/DECISIONS.md#d-gpu_canvas-549
                glass_radius = min(logical_w, logical_h) / 2.0
                paint_loupe_shadow(loupe_painter, loupe_center[0],
                                   loupe_center[1], glass_radius)
                loupe_painter.drawImage(
                    QtCore.QPointF(loupe_center[0] - logical_w / 2.0,
                                   loupe_center[1] - logical_h / 2.0),
                    self._loupe_image)
                paint_loupe_glass(loupe_painter, loupe_center[0], loupe_center[1],
                                  glass_radius,
                                  dragging=getattr(self, '_loupe_dragging', False))
                loupe_painter.end()
            # Phase 18G: `.split-line` — the seam, over the image. Painted
            # here with the rest of the chrome rather than in the shader:
            # it is a piece of UI on the picture, not part of it, and it
            # must not end up in a render or an export.
            seam_x = self._host.seam_x_px()
            if seam_x is not None:
                from qtpy import QtGui
                from spektrafilm_gui import theme_palette as _seam_palette

                seam_painter = QtGui.QPainter(self)
                seam_painter.translate(offset)
                # 2026-08-03; see design/DECISIONS.md#d-gpu_canvas-550
                seam_painter.setPen(QtGui.QPen(
                    QtGui.QColor(_seam_palette.TEXT_HI), 1))
                # The canvas's height, not the overlay's: the seam belongs
                # to the picture and stops where it stops.
                seam_painter.drawLine(QtCore.QPointF(seam_x, 0),
                                      QtCore.QPointF(seam_x,
                                                     self._host.height()))
                seam_painter.end()

            if (self._rubber is None and self._line is None and not frame_active
                    and self._host.guide_mode == 'off' and not self._dust_drawn()):
                return
            from qtpy import QtGui
            painter = QtGui.QPainter(self)
            painter.translate(offset)
            # ...and the chrome that belongs to the picture is clipped to it.
            # Only the loupe is allowed onto the paper.
            painter.setClipRect(self._host.rect())
            if self._dust_drawn():
                self._paint_dust(painter)
            if self._host.guide_mode != 'off' and not frame_active:
                self._paint_guides(painter)
            if frame_active:
                self._paint_frame(painter)
            if self._line is not None:
                pen = QtGui.QPen(QtGui.QColor(255, 255, 255, 220), 1)
                painter.setPen(pen)
                painter.drawLine(QtCore.QLineF(*self._line))
            if self._rubber is None:
                return
            x0, y0, x1, y1 = self._rubber
            rect = QtCore.QRectF(QtCore.QPointF(x0, y0), QtCore.QPointF(x1, y1)).normalized()
            self._paint_scrim(painter, rect)
            pen = QtGui.QPen(QtGui.QColor(255, 255, 255), 1, QtCore.Qt.DashLine)
            painter.setPen(pen)
            painter.drawRect(rect)

        def _dust_drawn(self) -> bool:
            return bool(self._host.dust_marks or self._host.dust_zones or self._zone is not None)

        def _paint_dust(self, painter) -> None:
            """The Reco tab's spots: a ring and a label each, switched-off ones grey and thin; then its circles."""
            _, _, content_w, _ = self._host.content_rect_px()
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            font = painter.font()
            font.setPixelSize(10)
            painter.setFont(font)
            circles = [(*self._host.image_norm_to_widget(u, v), radius * content_w, kind)
                       for u, v, radius, kind in self._host.dust_zones]
            if self._zone is not None:
                x0, y0, x1, y1 = self._zone
                circles.append((x0, y0, math.hypot(x1 - x0, y1 - y0), None))
            for cx, cy, r, kind in circles:
                colour = (QtGui.QColor(255, 255, 255, 220) if kind is None
                          else QtGui.QColor(224, 85, 85, 230) if kind == 'ignore' else QtGui.QColor(90, 200, 120, 230))
                painter.setPen(QtGui.QPen(colour, 1.5, QtCore.Qt.DashLine))
                painter.setBrush(QtCore.Qt.NoBrush)
                painter.drawEllipse(QtCore.QPointF(cx, cy), r, r)
            for u, v, radius, label, off in self._host.dust_marks:
                cx, cy = self._host.image_norm_to_widget(u, v)
                r = max(5.0, radius * content_w)
                colour = QtGui.QColor(150, 150, 150, 200) if off else QtGui.QColor(52, 209, 224, 235)
                painter.setPen(QtGui.QPen(colour, 1.0 if off else 1.5))
                painter.setBrush(QtCore.Qt.NoBrush)
                painter.drawEllipse(QtCore.QPointF(cx, cy), r, r)
                painter.drawText(QtCore.QPointF(cx + r * 0.75 + 2, cy - r * 0.75 - 2), label)

        def _paint_scrim(self, painter, rect) -> None:
            from qtpy import QtGui
            scrim = QtGui.QColor(0, 0, 0, 120)
            # The CANVAS's rect. The painter is already translated into
            # canvas-local coordinates, and the overlay is the whole room
            # now -- scrimming its rect would black out the paper as well
            # as the picture.
            full = QtCore.QRectF(self._host.rect())
            for side in (
                QtCore.QRectF(full.left(), full.top(), full.width(), rect.top() - full.top()),
                QtCore.QRectF(full.left(), rect.bottom(), full.width(), full.bottom() - rect.bottom()),
                QtCore.QRectF(full.left(), rect.top(), rect.left() - full.left(), rect.height()),
                QtCore.QRectF(rect.right(), rect.top(), full.right() - rect.right(), rect.height()),
            ):
                painter.fillRect(side, scrim)

        def _paint_frame(self, painter) -> None:
            """The persistent Lightroom-style crop frame: scrim outside, solid
            border, 8 handles, and the composition guides INSIDE the frame
            (thirds by default while the tool is armed)."""
            from qtpy import QtGui
            frame = self._host.frame_rect_px()
            if frame is None:
                return
            x0, y0, x1, y1 = frame
            rect = QtCore.QRectF(QtCore.QPointF(x0, y0), QtCore.QPointF(x1, y1)).normalized()
            self._paint_scrim(painter, rect)
            # guides inside the crop box, following its aspect
            mode = self._host.guide_mode if self._host.guide_mode != 'off' else 'thirds'
            lines = guide_lines(mode, (rect.left(), rect.top(), rect.width(), rect.height()),
                                getattr(self._host, 'guide_orient', 0))
            if lines:
                segments = [QtCore.QLineF(QtCore.QPointF(*a), QtCore.QPointF(*b)) for a, b in lines]
                # `.cropov .v` / `.h` — 1px at `oklch(1 0 0 / .45)`, which
                # is alpha 115. It was 150. The dark underlay is OURS: the
                # page's guides sit on a mock, these sit on any photograph.
                painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 90), 3))
                painter.drawLines(segments)
                painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 115), 1))
                painter.drawLines(segments)
            # 2026-08-05; see design/DECISIONS.md#d-gpu_canvas-551
            from spektrafilm_gui import theme_palette
            edge = QtGui.QColor(theme_palette.TEXT_BRIGHT)
            painter.setPen(QtGui.QPen(edge, 1.5))
            painter.drawRect(rect)
            # 8 handles: corners + edge midpoints
            xm, ym = rect.center().x(), rect.center().y()
            painter.setBrush(edge)
            for hx, hy in ((rect.left(), rect.top()), (xm, rect.top()),
                           (rect.right(), rect.top()), (rect.right(), ym),
                           (rect.right(), rect.bottom()), (xm, rect.bottom()),
                           (rect.left(), rect.bottom()), (rect.left(), ym)):
                painter.drawRect(QtCore.QRectF(hx - 3.0, hy - 3.0, 6.0, 6.0))
            self._paint_angle_ruler(painter)

        def _paint_angle_ruler(self, painter) -> None:
            """`.cropov .ruler` + `.ruler-c` — the straighten ruler.

            2026-08-05; see design/DECISIONS.md#d-gpu_canvas-562
            """
            from qtpy import QtGui
            from spektrafilm_gui import theme_palette

            angle = float(getattr(self._host, 'crop_angle_deg', 0.0) or 0.0)
            centre_x = self._host.width() / 2.0
            left = centre_x - RULER_WIDTH_PX / 2.0
            top = RULER_TOP_PX

            painter.save()
            painter.setClipRect(QtCore.QRectF(left, top - 4,
                                              RULER_WIDTH_PX, RULER_HEIGHT_PX + 8))
            # `repeating-linear-gradient(90deg, oklch(1 0 0 / .6) 0 1px,
            #  transparent 1px 10px)` — one 1px tick every 10, offset so the
            # scale moves and the mark stays put
            painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 153), 1))
            shift = (-angle * RULER_PITCH_PX) % RULER_TICK_GAP_PX
            x = left + shift - RULER_TICK_GAP_PX
            while x <= left + RULER_WIDTH_PX:
                painter.drawLine(QtCore.QPointF(x, top),
                                 QtCore.QPointF(x, top + RULER_HEIGHT_PX))
                x += RULER_TICK_GAP_PX
            painter.restore()

            # `.ruler-c { top: 7px; width: 1.5px; height: 13px }`, accent:
            # the one coloured thing here, because where the angle IS is the
            # only state on this overlay.
            painter.setPen(QtGui.QPen(
                QtGui.QColor(theme_palette.ACCENT_COLOR_TEXT), 1.5))
            painter.drawLine(QtCore.QPointF(centre_x, top - 3),
                             QtCore.QPointF(centre_x, top - 3 + RULER_MARK_PX))

            # ...and the number, because a ruler you cannot read a value off
            # is still only half an indicator
            text = f'{angle:+.1f}°' if angle else '0.0°'
            font = QtGui.QFont(painter.font())
            font.setPixelSize(11)
            painter.setFont(font)
            metrics = QtGui.QFontMetrics(font)
            box = QtCore.QRectF(centre_x - 40,
                                top - 3 + RULER_MARK_PX + 3, 80,
                                metrics.height() + 2)
            painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 150), 3))
            painter.drawText(box.translated(0, 1), QtCore.Qt.AlignHCenter, text)
            painter.setPen(QtGui.QPen(QtGui.QColor(theme_palette.TEXT_BRIGHT), 1))
            painter.drawText(box, QtCore.Qt.AlignHCenter, text)

        def _paint_guides(self, painter) -> None:
            """Composition guides over the displayed image rect (follows
            zoom/pan via the shared layout math). Dark underlay + light line
            so the guides read on any image content."""
            from qtpy import QtGui
            if self._host.renderer.image_size == (1, 1):
                return
            # Guides frame the PHOTO, not the baked white border around it.
            rect = self._host.content_rect_px()
            lines = guide_lines(self._host.guide_mode, rect, getattr(self._host, 'guide_orient', 0))
            if not lines:
                return
            painter.setClipRect(self._host.rect())
            segments = [
                QtCore.QLineF(QtCore.QPointF(*a), QtCore.QPointF(*b))
                for a, b in lines
            ]
            painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 110), 3))
            painter.drawLines(segments)
            painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 190), 1))
            painter.drawLines(segments)

        # -- interaction ------------------------------------------------------
        def wheelEvent(self, event) -> None:  # noqa: N802
            # Over the paper this widget is not the one being scrolled --
            # it only reaches there because it is room-sized for the loupe.
            if self._outside_canvas(event.position()):
                event.ignore()
                return
            delta = event.angleDelta().y() / 120.0
            # Ctrl+scroll with the loupe armed resizes the circle
            # (proposal 17B: 180-420 px) instead of zooming the canvas.
            if (self._host.loupe_armed()
                    and event.modifiers() & QtCore.Qt.ControlModifier):
                self._host.emit_loupe_resize(delta)
                return
            self._host.set_zoom(self._host.zoom * (1.15 ** delta))

        def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
            if self._outside_canvas(event.position()):
                event.ignore()
                return
            if self._host.crop_mode == 'frame':
                self._host.emit_frame_commit()
                return
            self._host.reset_view()

        def keyPressEvent(self, event) -> None:  # noqa: N802
            if self._host.crop_mode == 'frame':
                if event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                    self._host.emit_frame_commit()
                    return
                if event.key() == QtCore.Qt.Key_Escape:
                    self._host.emit_frame_cancel()
                    return
            event.ignore()

        #: `.split-grab { width: 18px }` — how near the seam counts as on it
        SEAM_GRAB_PX = 18

        def _grab_seam(self, x: float) -> bool:
            seam = self._host.seam_x_px()
            if seam is None:
                return False
            if abs(float(x) - seam) > self.SEAM_GRAB_PX / 2:
                return False
            self._seam_drag = True
            self._host.seam_from_x(x)
            return True

        def _update_seam_cursor(self, x: float) -> None:
            """A seam you can grab should say so before you press."""
            seam = self._host.seam_x_px()
            over = seam is not None and abs(float(x) - seam) <= self.SEAM_GRAB_PX / 2
            self._set_cursor(QtCore.Qt.SplitHCursor if over else QtCore.Qt.ArrowCursor)

        def _set_cursor(self, wanted) -> None:
            """A Qt cursor shape, or a QCursor (the rotate cursor)."""
            from qtpy import QtGui

            if isinstance(wanted, QtGui.QCursor):
                if self.cursor().shape() != QtCore.Qt.BitmapCursor:
                    self.setCursor(wanted)
                return
            if self.cursor().shape() != wanted:
                self.setCursor(wanted)

        def _skew_centre(self):
            """What the image turns about: the picture's centre (apply_skew
            rotates about the image centre), in canvas-local px."""
            rx, ry, rw, rh = self._host.content_rect_px()
            return rx + rw / 2.0, ry + rh / 2.0

        def _pointer_angle(self, pos) -> float:
            from spektrafilm_gui.crop_frame import pointer_angle_deg

            cx, cy = self._skew_centre()
            return pointer_angle_deg(cx, cy, pos.x(), pos.y())

        def _skew_armed(self) -> bool:
            return (self._host.crop_mode == 'frame'
                    and self._host.crop_frame_uv is not None)

        def _in_skew_zone(self, event_pos) -> bool:
            """Where a press turns the image: anywhere OUTSIDE the crop frame
            (its handles included) -- the dark stage around the print, or the
            paper beyond the canvas -- as in other editors. The canvas widget
            fills the whole stage, so 'outside the picture' is judged against
            the frame, not the widget."""
            if not self._skew_armed():
                return False
            # the frame's grab band first: it reaches past the picture, and a
            # press in it resizes, never turns
            hit = self._frame_hit(event_pos)
            if hit is None:
                return self._outside_canvas(event_pos)
            return hit == 'outside'

        def _frame_hit(self, event_pos):
            """What a press here takes of the armed crop frame ('outside'
            beyond its grab band), or None with no frame armed."""
            if self._host.crop_mode != 'frame':
                return None
            frame = self._host.frame_rect_px()
            if frame is None:
                return None
            from spektrafilm_gui.crop_frame import hit_test

            pos = self._to_canvas(event_pos)
            return hit_test(frame, pos.x(), pos.y())

        #: the cursor that says what a press on the frame will do
        _FRAME_CURSORS = {
            'n': QtCore.Qt.SizeVerCursor, 's': QtCore.Qt.SizeVerCursor,
            'e': QtCore.Qt.SizeHorCursor, 'w': QtCore.Qt.SizeHorCursor,
            'nw': QtCore.Qt.SizeFDiagCursor, 'se': QtCore.Qt.SizeFDiagCursor,
            'ne': QtCore.Qt.SizeBDiagCursor, 'sw': QtCore.Qt.SizeBDiagCursor,
            'inside': QtCore.Qt.SizeAllCursor,
        }

        def _frame_cursor(self, event_pos):
            return self._FRAME_CURSORS.get(self._frame_hit(event_pos))

        def mousePressEvent(self, event) -> None:  # noqa: N802
            if event.button() != QtCore.Qt.LeftButton:
                return
            # 2026-09-19; see design/DECISIONS.md#d-gpu_canvas-552
            if self._in_skew_zone(event.position()):
                self._skew_drag = self._pointer_angle(self._to_canvas(event.position()))
                self._host.emit_skew_start()
                return
            # The overlay is the size of the ROOM now, so a press can land on
            # the paper. Every branch below acts on the PICTURE, and they all
            # take canvas-local pixels -- so decline outside it and translate
            # inside it, which is exactly what happened when the overlay
            # stopped at the canvas edge.
            if self._outside_canvas(event.position()):
                hit = self._frame_hit(event.position())
                if hit not in (None, 'outside', 'inside'):
                    # the frame's grab band reaches past the picture's edge
                    pos = self._to_canvas(event.position())
                    self._frame_drag = (hit, pos.x(), pos.y())
                    return
                event.ignore()
                return
            pos = self._to_canvas(event.position())
            # Phase 18G: the split seam is grabbed BEFORE anything else. It
            # is a thin target sitting over the image, so a tool that also
            # wants this click would otherwise always win it.
            if self._grab_seam(pos.x()):
                return
            # 2026-07-29; see design/DECISIONS.md#d-gpu_canvas-553
            if self._host.emit_loupe_press(pos.x(), pos.y()):
                return
            # Phase 17C on the canvas chrome: a click adopts a strip band.
            if self._host.emit_strip_click(pos.x(), pos.y()):
                return
            if callable(self._host._zone_handler):
                self._zone = (pos.x(), pos.y(), pos.x(), pos.y())
                self.update()
                return
            mode = self._host.crop_mode
            if mode == 'frame':
                frame = self._host.frame_rect_px()
                if frame is not None:
                    from spektrafilm_gui.crop_frame import hit_test
                    hit = hit_test(frame, pos.x(), pos.y())
                    if hit != 'outside':
                        self._frame_drag = (hit, pos.x(), pos.y())
                        return
                self._pan_origin = (pos.x(), pos.y(), self._host.pan[0], self._host.pan[1])
            elif mode == 'line':
                self._line = (pos.x(), pos.y(), pos.x(), pos.y())
                self.update()
            elif mode == 'draw':
                self._rubber = (pos.x(), pos.y(), pos.x(), pos.y())
                self.update()
            elif mode == 'move':
                self._move_origin = (pos.x(), pos.y())
            else:
                self._pan_origin = (pos.x(), pos.y(), self._host.pan[0], self._host.pan[1])

        def mouseMoveEvent(self, event) -> None:  # noqa: N802
            pos = self._to_canvas(event.position())
            # A DRAG THAT LEFT THE PICTURE still belongs to whoever started
            # it -- releasing the pointer off the edge mid-crop must not
            # abandon the crop. Only a move with nothing in progress is
            # declined, and that one has to be, or the loupe would follow
            # the cursor out over the paper and read pixels that are not
            # there.
            if self._skew_drag is not None:
                from spektrafilm_gui.crop_frame import wrap_angle_deg

                angle = self._pointer_angle(pos)
                delta = wrap_angle_deg(angle - self._skew_drag)
                self._skew_drag = angle
                fine = bool(event.modifiers() & QtCore.Qt.ShiftModifier)
                self._host.emit_skew_drag(delta, fine)
                return
            if self._zone is not None:
                self._zone = (self._zone[0], self._zone[1], pos.x(), pos.y())
                self.update()
                return
            in_progress = (self._seam_drag or self._frame_drag is not None
                           or self._line is not None or self._rubber is not None
                           or self._move_origin is not None
                           or self._pan_origin is not None)
            # outside the crop frame in crop mode a press turns the image:
            # say so before the press
            skew_zone = not in_progress and self._in_skew_zone(event.position())
            if not in_progress and self._outside_canvas(event.position()):
                if self._cursor is not None:
                    self._cursor = None
                    self.update()
                self._set_cursor(rotate_cursor() if skew_zone else
                                 (self._frame_cursor(event.position()) or QtCore.Qt.ArrowCursor))
                event.ignore()
                return
            if self._seam_drag:
                self._host.seam_from_x(pos.x())
                return
            frame_cursor = None if (in_progress or skew_zone) else self._frame_cursor(event.position())
            if skew_zone:
                self._set_cursor(rotate_cursor())
            elif frame_cursor is not None:
                self._set_cursor(frame_cursor)
            else:
                self._update_seam_cursor(pos.x())
            # Loupe hover tracking (never blocks the drag tools below).
            if self._host.loupe_armed():
                self._cursor = (pos.x(), pos.y())
                self._host.emit_loupe_move(pos.x(), pos.y())
                self.update()
            if self._frame_drag is not None:
                hit, last_x, last_y = self._frame_drag
                fine = bool(event.modifiers() & QtCore.Qt.ShiftModifier)
                self._host.emit_frame_drag(hit, pos.x() - last_x, pos.y() - last_y, fine)
                self._frame_drag = (hit, pos.x(), pos.y())
                return
            if self._line is not None:
                self._line = (self._line[0], self._line[1], pos.x(), pos.y())
                self.update()
                return
            if self._rubber is not None:
                x0, y0 = self._rubber[:2]
                x1, y1 = self._host.constrain_corner(x0, y0, pos.x(), pos.y())
                self._rubber = (x0, y0, x1, y1)
                self.update()
                return
            if self._move_origin is not None:
                x0, y0 = self._move_origin
                fine = bool(event.modifiers() & QtCore.Qt.ShiftModifier)
                self._host.emit_crop_move(pos.x() - x0, pos.y() - y0, fine)
                return
            if self._pan_origin is not None:
                x0, y0, px0, py0 = self._pan_origin
                # The canvas's span: pan is a fraction of the PICTURE, and
                # the overlay is the whole room.
                width = max(self._host.width(), 1)
                height = max(self._host.height(), 1)
                self._host.pan[0] = px0 + (pos.x() - x0) / width
                self._host.pan[1] = py0 + (pos.y() - y0) / height
                self._host.request_redraw()

        def mouseReleaseEvent(self, event) -> None:  # noqa: N802
            if self._skew_drag is not None:
                self._skew_drag = None
                self._host.emit_skew_release()
                return
            if self._seam_drag:
                self._seam_drag = False
                return
            if self._frame_drag is not None:
                self._frame_drag = None
                self._host.emit_frame_release()
                return
            if self._zone is not None:
                zone, self._zone = self._zone, None
                self.update()
                self._host.emit_zone(*zone)
                return
            if self._line is not None:
                line, self._line = self._line, None
                self.update()
                self._host.emit_line_drawn(*line)
                return
            if self._rubber is not None:
                rubber, self._rubber = self._rubber, None
                self.update()
                self._host.emit_crop_drawn(*rubber)
                return
            if self._move_origin is not None:
                self._move_origin = None
                self._host.emit_crop_finished()
                return
            self._pan_origin = None

    class _Widget(QtWidgets.QWidget):
        # 2026-08-05; see design/DECISIONS.md#d-gpu_canvas-554
        view_listener = None

        # 2026-08-04; see design/DECISIONS.md#d-gpu_canvas-555
        zoom_changed = QtCore.Signal(float)

        def __init__(self, parent=None):
            super().__init__(parent)
            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            self.canvas = RenderCanvas(parent=self)
            layout.addWidget(self.canvas)

            self._context = self.canvas.get_context('wgpu')
            # Strip -srgb like NegPy: the pipeline output is already encoded.
            self._format = self._context.get_preferred_format(gpu.adapter).replace('-srgb', '')
            self._configure_context()
            self.renderer = GPUImageRenderer(gpu.device, self._format)

            self.zoom = 1.0
            self.pan = [0.0, 0.0]
            # 2026-07-12, 2026-08-04; see design/DECISIONS.md#d-gpu_canvas-556
            self._background = (0x07 / 255.0, 0x07 / 255.0, 0x07 / 255.0)

            # Interaction overlay ON TOP of the canvas (the canvas consumes
            # mouse events itself, so the overlay is the interaction surface).
            self.crop_mode = 'off'
            self.guide_mode = 'off'
            self.guide_orient = 0
            self.content_inset = (0.0, 0.0)
            self.crop_frame_uv = None     # (u0, v0, u1, v1) persistent LR frame
            self.dust_marks = []          # (u, v, radius, label, off): the Reco tab's spots
            self.dust_zones = []          # (u, v, radius, kind): its ignore / only circles
            self._zone_handler = None
            self._crop_handlers = {}
            self._loupe_handlers = {}       # Phase 17B canvas loupe
            self.loupe_locked_uv = None     # (u, v) while the loupe is pinned
            self._strip_click_handler = None  # Phase 17C band adopt
            self.overlay = _InteractionOverlay(self)
            self.sync_overlay_geometry()

            self._resize_timer = QtCore.QTimer(self)
            self._resize_timer.setSingleShot(True)
            self._resize_timer.setInterval(50)
            self._resize_timer.timeout.connect(self._reconfigure)

            self.canvas.request_draw(self._draw_frame)

        # -- public API (mirrors what the controller needs) ----------------
        def set_image(self, image) -> None:
            # Texture uploads touch the shared device too: serialize with
            # the worker pipelines (uploads are rare; brief blocking is ok).
            from spektrafilm_gui.controller_runtime import PIPELINE_RENDER_LOCK
            with PIPELINE_RENDER_LOCK:
                self.renderer.set_image(image)
            self.request_redraw()

        def clear_image(self) -> None:
            from spektrafilm_gui.controller_runtime import PIPELINE_RENDER_LOCK
            with PIPELINE_RENDER_LOCK:
                self.renderer.clear_image()
            self.request_redraw()

        def set_compare(self, image) -> None:
            from spektrafilm_gui.controller_runtime import PIPELINE_RENDER_LOCK
            with PIPELINE_RENDER_LOCK:
                self.renderer.set_compare_image(image)
            self.request_redraw()

        # ---------------------------------------------------- split (18G)
        def set_split(self, position) -> None:
            """Show the two images in ONE frame, seamed at `position`.

            `None` restores the side-by-side compare the app has always
            had. The design's Split is a seam you drag across one picture,
            which is the only way to compare a detail: two pictures side by
            side are half the size and never quite aligned.
            """
            self.renderer.split_position = (
                None if position is None
                else max(0.0, min(1.0, float(position))))
            self.request_redraw()

        def split_position(self):
            return self.renderer.split_position

        def seam_x_px(self):
            """Where the seam is on screen, or None when not split.

            A seam AT an edge is not a seam: the far side is the whole
            frame. That is how the NEGATIVE view is drawn -- one picture,
            the negative, with no line over it and nothing to grab. A drag
            can never land there (`seam_from_x` stops short of both edges),
            so this only ever hides a seam that was placed deliberately.
            """
            position = self.renderer.split_position
            if position is None or not self.renderer.has_image:
                return None
            if position <= 0.0 or position >= 1.0:
                return None
            layout = self.renderer.layout((self.width(), self.height()))
            x, _y, w, _h = layout['image']
            return x + w * position

        def seam_from_x(self, x: float) -> None:
            """Put the seam under the pointer, in IMAGE coordinates."""
            if not self.renderer.has_image:
                return
            layout = self.renderer.layout((self.width(), self.height()))
            left, _y, width, _h = layout['image']
            if width <= 0:
                return
            # STOPS SHORT OF BOTH EDGES. A seam dragged all the way out
            # would be a seam you cannot get back -- there would be nothing
            # left of it to grab -- and an edge seam is how the solo
            # NEGATIVE view is drawn.
            self.set_split(min(0.99, max(0.01, (float(x) - left) / width)))

        def clear_compare(self) -> None:
            from spektrafilm_gui.controller_runtime import PIPELINE_RENDER_LOCK
            with PIPELINE_RENDER_LOCK:
                self.renderer.clear_compare_image()
            self.request_redraw()

        def set_background_hex(self, color: str) -> None:
            color = color.lstrip('#')
            r, g, b = (int(color[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
            self._background = (r, g, b)
            self.request_redraw()

        def reset_view(self) -> None:
            self.zoom = 1.0
            self.pan = [0.0, 0.0]
            self.request_redraw()
            self._announce_zoom()

        def set_zoom(self, zoom: float) -> None:
            self.zoom = max(min(float(zoom), 64.0), 0.02)
            self.request_redraw()
            self._announce_zoom()

        def _fit_px_per_image_px(self) -> float:
            viewport = (max(self.width(), 1), max(self.height(), 1))
            layout = self.renderer.layout(viewport)
            image_w = max(self.renderer.image_size[0], 1)
            return layout['image'][2] / image_w

        def zoom_percent(self) -> float:
            """Where the view actually is, in the same units the buttons use."""
            return self.zoom * self._fit_px_per_image_px() * 100.0

        def _announce_zoom(self) -> None:
            try:
                self.zoom_changed.emit(self.zoom_percent())
            except Exception:
                pass          # a readout must never be able to break the view

        def set_zoom_percent(self, percent: float) -> None:
            """Screen-pixel semantics like the napari buttons: 100% maps one
            image pixel to one (logical) widget pixel."""
            fit_px_per_image_px = self._fit_px_per_image_px()
            if fit_px_per_image_px > 0:
                self.set_zoom((percent / 100.0) / fit_px_per_image_px)

        def request_redraw(self) -> None:
            self.canvas.request_draw(self._draw_frame)
            # Guides live on the overlay and must track zoom/pan/image changes.
            if self.guide_mode != 'off':
                self.overlay.update()
            # ...and so does the paper board, which is cut to the image.
            # EVERY zoom, pan and new image comes through here, which is why
            # the hook is here and not on each setter.
            listener = getattr(self, 'view_listener', None)
            if callable(listener):
                listener()

        # -- composition guides -------------------------------------------------
        def set_guide_mode(self, mode: str) -> None:
            self.guide_mode = mode if mode in GUIDE_MODES else 'off'
            self.overlay.update()

        def set_dust_marks(self, marks) -> None:
            self.dust_marks = list(marks)
            self.overlay.update()

        def set_dust_zones(self, zones) -> None:
            """(u, v, radius, kind) circles of the Defects tool, radius as a fraction of the picture's width."""
            self.dust_zones = list(zones)
            self.overlay.update()

        def arm_zone_drag(self, on_zone=None) -> None:
            """A drag on the picture draws a circle: on_zone(u, v, radius) with the radius a fraction of the
            picture's width. None disarms."""
            self._zone_handler = on_zone if callable(on_zone) else None

        def emit_zone(self, x0: float, y0: float, x1: float, y1: float) -> None:
            if not callable(self._zone_handler):
                return
            u, v = self.widget_to_image_norm(x0, y0)
            _, _, content_w, _ = self.content_rect_px()
            self._zone_handler(u, v, math.hypot(x1 - x0, y1 - y0) / max(content_w, 1.0))

        def cycle_guide(self) -> str:
            index = GUIDE_MODES.index(self.guide_mode) if self.guide_mode in GUIDE_MODES else 0
            self.set_guide_mode(GUIDE_MODES[(index + 1) % len(GUIDE_MODES)])
            return self.guide_mode

        def set_guide_orient(self, orient: int) -> None:
            self.guide_orient = int(orient) % 4
            self.overlay.update()

        def flip_guide(self) -> int:
            self.guide_orient = (int(self.guide_orient) + 1) % 4
            self.overlay.update()
            return self.guide_orient

        # -- loupe + strip bridges (Phase 17 instruments) ------------------
        def arm_loupe(self, on_move=None, on_lock=None, on_resize=None) -> None:
            """Hover tracking for the loupe: on_move(u, v) in normalized
            image coordinates; on_lock(u, v) when a click pins the loupe
            (on_lock(None, None) on release); on_resize(steps) for
            Ctrl+scroll size changes. None disarms, clears the circle and
            any lock."""
            self._loupe_handlers = (
                {'move': on_move, 'lock': on_lock, 'resize': on_resize}
                if callable(on_move) else {})
            self.loupe_locked_uv = None
            if not self._loupe_handlers:
                self.overlay._cursor = None
                self.overlay.set_loupe_view(None)

        def loupe_armed(self) -> bool:
            return bool(self._loupe_handlers)

        def set_loupe_view(self, rgba, *, dragging: bool = False) -> None:
            self.overlay.set_loupe_view(rgba, dragging=dragging)

        def emit_loupe_move(self, x: float, y: float) -> None:
            if self.loupe_locked_uv is not None:
                return                      # pinned: the circle stays put
            handler = self._loupe_handlers.get('move')
            if callable(handler):
                u, v = self.widget_to_image_norm(x, y)
                handler(u, v)

        def emit_loupe_resize(self, steps: float) -> None:
            handler = self._loupe_handlers.get('resize')
            if callable(handler):
                handler(float(steps))

        def emit_loupe_press(self, x: float, y: float) -> bool:
            """Click flow (NAT 2026-07-29): a click PINS the loupe at that
            position; clicking the pinned circle releases it to follow the
            cursor again. Returns True when the press was consumed."""
            if not self.loupe_armed():
                return False
            if self.loupe_locked_uv is not None:
                cx, cy = self.image_norm_to_widget(*self.loupe_locked_uv)
                radius = self.overlay.loupe_logical_radius()
                if (x - cx) ** 2 + (y - cy) ** 2 <= radius * radius:
                    self.loupe_locked_uv = None      # release: follow again
                    self.overlay._cursor = (x, y)
                    handler = self._loupe_handlers.get('lock')
                    if callable(handler):
                        handler(None, None)
                    self.emit_loupe_move(x, y)
                    self.overlay.update()
                    return True
                return False    # elsewhere while pinned: normal tools/pan
            u, v = self.widget_to_image_norm(x, y)
            self.loupe_locked_uv = (u, v)
            handler = self._loupe_handlers.get('lock')
            if callable(handler):
                handler(u, v)
            self.overlay.update()
            return True

        def arm_strip_click(self, on_click=None) -> None:
            """Band adopt for the test strip: on_click(u, v) in normalized
            image coordinates; None disarms."""
            self._strip_click_handler = on_click if callable(on_click) else None

        def emit_strip_click(self, x: float, y: float) -> bool:
            if not callable(self._strip_click_handler):
                return False
            u, v = self.widget_to_image_norm(x, y)
            self._strip_click_handler(u, v)
            return True

        # -- crop tool bridge -------------------------------------------------
        def set_crop_angle(self, degrees: float) -> None:
            """The straighten value the ruler reads (`scan_finishing.skew_deg`)."""
            value = float(degrees or 0.0)
            if value == self.crop_angle_deg:
                return
            self.crop_angle_deg = value
            overlay = getattr(self, 'overlay', None)
            if overlay is not None:
                overlay.update()

        def arm_crop_tool(self, mode: str, **handlers) -> None:
            """mode 'frame' | 'line' | 'draw' | 'move' | 'off'.

            2026-09-19; see design/DECISIONS.md#d-gpu_canvas-563
            """
            self.crop_mode = mode if mode in ('frame', 'line', 'draw', 'move') else 'off'
            self._crop_handlers = handlers if self.crop_mode != 'off' else {}
            if self.crop_mode == 'frame':
                self.overlay.setFocusPolicy(QtCore.Qt.StrongFocus)
                self.overlay.setFocus()
            else:
                self.overlay.setFocusPolicy(QtCore.Qt.NoFocus)
            if self.crop_mode == 'off':
                self.crop_frame_uv = None
            self.crop_angle_deg = 0.0
            self.overlay.update()

        def set_crop_frame(self, rect_uv) -> None:
            """The persistent frame in normalized image uv (None hides it)."""
            self.crop_frame_uv = tuple(float(v) for v in rect_uv) if rect_uv is not None else None
            self.overlay.update()

        def frame_rect_px(self):
            """The persistent frame mapped to widget px via the content rect."""
            if self.crop_frame_uv is None:
                return None
            rx, ry, rw, rh = self.content_rect_px()
            u0, v0, u1, v1 = self.crop_frame_uv
            return (rx + u0 * rw, ry + v0 * rh, rx + u1 * rw, ry + v1 * rh)

        def emit_frame_drag(self, hit, dx_px, dy_px, fine) -> None:
            handler = self._crop_handlers.get('on_frame_drag')
            if callable(handler):
                _, _, rw, rh = self.content_rect_px()
                handler(hit, dx_px / max(rw, 1e-9), dy_px / max(rh, 1e-9), fine)

        def emit_frame_release(self) -> None:
            handler = self._crop_handlers.get('on_frame_release')
            if callable(handler):
                handler()

        def emit_frame_commit(self) -> None:
            handler = self._crop_handlers.get('on_frame_commit')
            if callable(handler):
                handler()

        def emit_frame_cancel(self) -> None:
            handler = self._crop_handlers.get('on_frame_cancel')
            if callable(handler):
                handler()

        def emit_skew_start(self) -> None:
            handler = self._crop_handlers.get('on_skew_start')
            if callable(handler):
                handler()

        def emit_skew_drag(self, delta_deg: float, fine: bool) -> None:
            handler = self._crop_handlers.get('on_skew')
            if callable(handler):
                handler(float(delta_deg), bool(fine))

        def emit_skew_release(self) -> None:
            handler = self._crop_handlers.get('on_skew_release')
            if callable(handler):
                handler()

        def emit_line_drawn(self, x0, y0, x1, y1) -> None:
            handler = self._crop_handlers.get('on_line')
            if callable(handler):
                handler(x0, y0, x1, y1)

        def constrain_corner(self, x0, y0, x1, y1):
            aspect = self._crop_handlers.get('aspect_ratio')
            ratio = aspect() if callable(aspect) else None
            if ratio is None:
                return x1, y1
            dx, dy = x1 - x0, y1 - y0
            if abs(dx) > abs(dy) * ratio:
                dx = abs(dy) * ratio * (1 if dx >= 0 else -1)
            else:
                dy = abs(dx) / ratio * (1 if dy >= 0 else -1)
            return x0 + dx, y0 + dy

        def _image_rect_px(self):
            viewport = (max(self.width(), 1), max(self.height(), 1))
            layout = self.renderer.layout(viewport)
            return apply_zoom_pan_to_rect(layout['image'], viewport, self.zoom, self.pan)

        def image_rect_px(self):
            """Where the whole displayed image is, zoom and pan included.

            2026-08-05; see design/DECISIONS.md#d-gpu_canvas-564
            """
            if self.renderer.image_size == (1, 1):
                return None
            return self._image_rect_px()

        def set_content_inset(self, fx: float, fy: float) -> None:
            """Fraction of the uploaded texture taken by a baked-in border on
            EACH side (the controller's white padding). Crop mapping and the
            composition guides work on the CONTENT rect inside it."""
            self.content_inset = (max(float(fx), 0.0), max(float(fy), 0.0))
            self.overlay.update()

        def content_rect_px(self):
            rx, ry, rw, rh = self._image_rect_px()
            fx, fy = self.content_inset
            return (rx + fx * rw, ry + fy * rh,
                    rw * max(1.0 - 2.0 * fx, 1e-9), rh * max(1.0 - 2.0 * fy, 1e-9))

        def widget_to_image_norm(self, x: float, y: float) -> tuple[float, float]:
            rx, ry, rw, rh = self.content_rect_px()
            return (x - rx) / max(rw, 1e-9), (y - ry) / max(rh, 1e-9)

        def image_norm_to_widget(self, u: float, v: float) -> tuple[float, float]:
            """Inverse of widget_to_image_norm — a LOCKED loupe anchors in
            image space and re-projects per paint, so it tracks pan/zoom."""
            rx, ry, rw, rh = self.content_rect_px()
            return rx + float(u) * rw, ry + float(v) * rh

        def emit_crop_drawn(self, x0, y0, x1, y1) -> None:
            handler = self._crop_handlers.get('on_draw')
            if callable(handler):
                u0, v0 = self.widget_to_image_norm(x0, y0)
                u1, v1 = self.widget_to_image_norm(x1, y1)
                handler(u0, v0, u1, v1)

        def emit_crop_move(self, dx_px, dy_px, fine) -> None:
            handler = self._crop_handlers.get('on_move')
            if callable(handler):
                _, _, rw, rh = self.content_rect_px()
                handler(dx_px / max(rw, 1e-9), dy_px / max(rh, 1e-9), fine)

        def emit_crop_finished(self) -> None:
            handler = self._crop_handlers.get('on_finish')
            if callable(handler):
                handler()

        # -- internals --------------------------------------------------------
        def _configure_context(self) -> None:
            last_error = None
            for alpha_mode in ('premultiplied', 'opaque'):
                try:
                    self._context.configure(device=gpu.device, format=self._format,
                                            alpha_mode=alpha_mode)
                    return
                except Exception as exc:  # try the next platform alpha mode
                    last_error = exc
            if last_error is not None:
                raise last_error

        def sync_overlay_geometry(self) -> None:
            """Keep the interaction overlay exactly over the canvas.

            PARENT-AWARE, because the overlay does not always belong to the
            canvas. `StageArea` re-homes it to the room and stacks it ABOVE
            the paper mount, so the loupe is not painted over by the board
            (NAT: "why is the loop going under the frame"). When it has been
            moved, its geometry is the canvas's own geometry in the room's
            coordinates; when it has not, it is simply the canvas's rect.

            Either way the overlay covers the same pixels, so everything it
            draws and every event it handles keeps working in canvas-local
            coordinates.
            """
            overlay = getattr(self, 'overlay', None)
            if overlay is None:
                return
            parent = overlay.parentWidget()
            if parent is self:
                overlay.setGeometry(self.rect())
            else:
                # 2026-08-27; see design/DECISIONS.md#d-gpu_canvas-557
                overlay.setGeometry(parent.rect())
            overlay.raise_()

        def resizeEvent(self, event) -> None:  # noqa: N802
            super().resizeEvent(event)
            self.sync_overlay_geometry()
            self._resize_timer.start()

        def _reconfigure(self) -> None:
            try:
                self._configure_context()
                self.request_redraw()
            except Exception:
                pass

        def _draw_frame(self) -> None:
            # 2026-07-29; see design/DECISIONS.md#d-gpu_canvas-558
            from spektrafilm_gui.controller_runtime import PIPELINE_RENDER_LOCK
            if not PIPELINE_RENDER_LOCK.acquire(blocking=False):
                QtCore.QTimer.singleShot(16, self.request_redraw)
                return
            try:
                try:
                    current = self._context.get_current_texture()
                except Exception:
                    return  # swapchain unavailable mid-resize; skip the frame
                if current is None:
                    return
                self.renderer.draw(
                    current.create_view(), (current.width, current.height),
                    zoom=self.zoom, pan=tuple(self.pan), background=self._background)
            finally:
                PIPELINE_RENDER_LOCK.release()

    # 2026-08-03, 2026-08-30; see design/DECISIONS.md#d-gpu_canvas-559
    try:
        return _Widget(parent)
    except Exception as exc:
        _canvas_logger.warning(
            'wgpu canvas construction failed (%s: %s); using the CPU canvas',
            type(exc).__name__, exc)
        return None
