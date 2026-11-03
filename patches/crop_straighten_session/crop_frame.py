"""Phase 9: Lightroom-style persistent crop frame — the INTERACTION math.

Pure functions only (no Qt, headless-testable). This module changes how a
crop is SET, never what a given crop produces: the values it manipulates are
the existing scan-crop params (crop_center in 0-1 per axis, crop_size as a
fraction of the image's LONG edge per axis — the app's historical
convention), consumed unchanged by ResizingService / apply_scan_geometry.

Coordinate frames:
- uv rect: (u0, v0, u1, v1), normalized 0-1 PER AXIS over the full image.
- center/size params: the persisted crop values (size long-edge-normalized).
"""

from __future__ import annotations

import math

HANDLES = ('nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w')
# 2026-07-23; see design/DECISIONS.md#d-crop_frame-321
MIN_CROP_SIZE = 0.04
HANDLE_RADIUS_PX = 8.0

# Ratio presets: the historical set + Original + Cinema. Each ratio once:
# the Frame group's landscape / portrait pair gives the portrait version.
RATIO_PRESETS = (
    'Free', 'Original',
    '3:2', '4:3', '5:4', '6:7', '1:1', '65:24',
    '16:9', '1.85:1', '2.39:1',                    # Cinema
)


def parse_ratio(text: str, image_wh: tuple[float, float] | None = None):
    """'16:9' / '1.85:1' -> width/height float; 'Original' -> the image's
    aspect (None if unknown); 'Free' / unparsable -> None (unlocked)."""
    label = (text or '').strip().lower()
    if label in ('', 'free'):
        return None
    if label == 'original':
        if not image_wh:
            return None
        width, height = image_wh
        return float(width) / float(height) if height else None
    if ':' in label:
        try:
            w_r, h_r = label.split(':')
            return float(w_r) / float(h_r)
        except (ValueError, ZeroDivisionError):
            return None
    return None


# ------------------------------------------------------------------ frames
def crop_rect_uv(center, size, image_wh):
    """Persisted (center, size) -> uv rect. size is long-edge-normalized.

    2026-10-08; see design/DECISIONS.md#d-crop_frame-322
    """
    width, height = max(float(image_wh[0]), 1.0), max(float(image_wh[1]), 1.0)
    long_edge = max(width, height)
    half_u = float(size[0]) * long_edge / width / 2.0
    half_v = float(size[1]) * long_edge / height / 2.0
    cx, cy = float(center[0]), float(center[1])
    return clamp_rect((cx - half_u, cy - half_v, cx + half_u, cy + half_v))


def rect_to_center_size(rect_uv, image_wh):
    """uv rect -> persisted (center, size)."""
    width, height = max(float(image_wh[0]), 1.0), max(float(image_wh[1]), 1.0)
    long_edge = max(width, height)
    u0, v0, u1, v1 = rect_uv
    center = ((u0 + u1) / 2.0, (v0 + v1) / 2.0)
    size = (abs(u1 - u0) * width / long_edge, abs(v1 - v0) * height / long_edge)
    return center, size


def clamp_rect(rect_uv, min_size_uv=(0.0, 0.0)):
    """Slide the rect back into [0, 1] without shrinking it (shrink only if
    larger than the image); enforce a minimum size."""
    u0, v0, u1, v1 = rect_uv
    u0, u1 = min(u0, u1), max(u0, u1)
    v0, v1 = min(v0, v1), max(v0, v1)
    w = min(max(u1 - u0, min_size_uv[0]), 1.0)
    h = min(max(v1 - v0, min_size_uv[1]), 1.0)
    u0 = min(max(u0, 0.0), 1.0 - w)
    v0 = min(max(v0, 0.0), 1.0 - h)
    return (u0, v0, u0 + w, v0 + h)


# ------------------------------------------------------------ interaction
#: Where a press takes the frame (widget px). A whole edge grabs within
#: EDGE_GRAB_PX of its line, a corner within CORNER_GRAB_PX, and anything up
#: to GRAB_BAND_PX OUTSIDE the frame takes the nearest edge or corner -- a
#: grab a little off a handle resizes and never turns the photo; the turn
#: starts beyond the band. (Fluent asks for 24 px targets at least; the old
#: 16 px handles on an edge's midpoint left the rest of the edge line a
#: knife-edge between moving the frame and turning the photo.)
EDGE_GRAB_PX = 10.0
CORNER_GRAB_PX = 14.0
GRAB_BAND_PX = 20.0


def hit_test(rect_px, x, y, handle_radius=None):
    """'nw'|'n'|...|'w' when on a handle, 'inside' within the frame,
    'outside' beyond the grab band. rect_px in widget pixels.

    CORNERS are tested BEFORE edges (NAT 2026-07-23): a corner resizes both
    axes, an edge resizes ONE — so an ambiguous grab near a corner must pick
    the corner, else dragging collapses one dimension into a thin sliver (the
    'crop gives a long vertical line' report). Corners also get the larger
    reach so they win the overlap zone. `handle_radius` is kept for callers;
    the reaches are the constants above."""
    x0, y0, x1, y1 = rect_px
    if math.hypot(max(x0 - x, 0.0, x - x1), max(y0 - y, 0.0, y - y1)) > GRAB_BAND_PX:
        return 'outside'
    width, height = max(x1 - x0, 1.0), max(y1 - y0, 1.0)

    def side(v, low, high, reach):
        if v <= low + reach:
            return -1
        if v >= high - reach:
            return 1
        return 0

    # a small frame keeps a middle that moves it
    corner_x = side(x, x0, x1, min(CORNER_GRAB_PX, width / 3.0))
    corner_y = side(y, y0, y1, min(CORNER_GRAB_PX, height / 3.0))
    if corner_x and corner_y:
        return {(-1, -1): 'nw', (1, -1): 'ne', (1, 1): 'se', (-1, 1): 'sw'}[(corner_x, corner_y)]
    edge_x = side(x, x0, x1, min(EDGE_GRAB_PX, width / 4.0))
    edge_y = side(y, y0, y1, min(EDGE_GRAB_PX, height / 4.0))
    if edge_y:
        return 'n' if edge_y < 0 else 's'
    if edge_x:
        return 'w' if edge_x < 0 else 'e'
    return 'inside'


def resize_rect(rect_uv, handle, du, dv, *, ratio_uv=None, min_size=MIN_CROP_SIZE,
                clamp=True):
    """Drag a handle by (du, dv) in uv units. Corner handles move both edges,
    edge handles one; the opposite side stays anchored. ``ratio_uv`` (a
    width/height ratio ALREADY converted to uv units) constrains the shape;
    for edge handles the perpendicular size follows, centered on the anchor
    edge. Result is clamped to [0, 1] -- unless ``clamp`` is False, for a
    caller that keeps the rect inside itself (`furthest_drag`)."""
    u0, v0, u1, v1 = rect_uv
    if 'w' in handle:
        u0 = u0 + du
    if 'e' in handle:
        u1 = u1 + du
    if 'n' in handle:
        v0 = v0 + dv
    if 's' in handle:
        v1 = v1 + dv
    u0, u1 = min(u0, u1), max(u0, u1)
    v0, v1 = min(v0, v1), max(v0, v1)
    w = max(u1 - u0, min_size)
    h = max(v1 - v0, min_size)

    if ratio_uv:
        if handle in ('n', 's'):
            w = h * ratio_uv
            cx = (u0 + u1) / 2.0
            u0, u1 = cx - w / 2.0, cx + w / 2.0
        elif handle in ('e', 'w'):
            h = w / ratio_uv
            cy = (v0 + v1) / 2.0
            v0, v1 = cy - h / 2.0, cy + h / 2.0
        else:
            # corner: the DRAG's dominant axis wins, the other follows the
            # ratio; the anchor (opposite) corner stays fixed
            if abs(du) >= abs(dv):
                h = w / max(ratio_uv, 1e-9)
            else:
                w = h * ratio_uv
            if 'w' in handle:
                u0 = u1 - w
            else:
                u1 = u0 + w
            if 'n' in handle:
                v0 = v1 - h
            else:
                v1 = v0 + h
    else:
        if 'w' in handle:
            u0 = u1 - w
        elif 'e' in handle:
            u1 = u0 + w
        if 'n' in handle:
            v0 = v1 - h
        elif 's' in handle:
            v1 = v0 + h

    if not clamp:
        return (u0, v0, u1, v1)
    return clamp_rect((u0, v0, u1, v1), (min_size, min_size))


def move_image_under_frame(rect_uv, du, dv):
    """Drag INSIDE the frame. Our canvas displays the image FIXED and draws
    the frame as an overlay, so the frame must follow the cursor (rect moves
    WITH the drag, clamped inside the image). Rect-opposite-the-drag is only
    right when the canvas visually slides the image under a screen-fixed
    frame — with a fixed image it reads as inverted (NAT 2026-07-12)."""
    u0, v0, u1, v1 = rect_uv
    w, h = u1 - u0, v1 - v0
    u0 = min(max(u0 + du, 0.0), 1.0 - w)
    v0 = min(max(v0 + dv, 0.0), 1.0 - h)
    return (u0, v0, u0 + w, v0 + h)


def refit_to_ratio(rect_uv, ratio, image_wh):
    """Selecting a preset re-fits the crop: the largest rect of the given
    width/height ratio centered on the current center, inside the image."""
    if ratio is None:
        return clamp_rect(rect_uv, (MIN_CROP_SIZE, MIN_CROP_SIZE))
    width, height = max(float(image_wh[0]), 1.0), max(float(image_wh[1]), 1.0)
    u0, v0, u1, v1 = rect_uv
    cx, cy = (u0 + u1) / 2.0, (v0 + v1) / 2.0
    # max-fit in PIXEL terms, then normalize per axis
    w_px = width
    h_px = w_px / ratio
    if h_px > height:
        h_px = height
        w_px = h_px * ratio
    w, h = w_px / width, h_px / height
    rect = (cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)
    return clamp_rect(rect, (MIN_CROP_SIZE, MIN_CROP_SIZE))


def swap_rect_orientation(rect_uv, image_wh):
    """LR's X: landscape <-> portrait. The rect's PIXEL dimensions swap
    (keeping its size where possible), scaled down uniformly only when the
    swapped shape no longer fits the image, centered on the current center.
    (A naive size-axis swap collapses a full frame into a square — the
    'defaults to 1:1' bug of the first implementation.)"""
    width, height = max(float(image_wh[0]), 1.0), max(float(image_wh[1]), 1.0)
    u0, v0, u1, v1 = rect_uv
    w_px = (u1 - u0) * width
    h_px = (v1 - v0) * height
    if w_px <= 0.0 or h_px <= 0.0:
        return rect_uv
    new_w_px, new_h_px = h_px, w_px
    scale = min(1.0, width / new_w_px, height / new_h_px)
    new_w_px *= scale
    new_h_px *= scale
    cx, cy = (u0 + u1) / 2.0, (v0 + v1) / 2.0
    w_uv = new_w_px / width
    h_uv = new_h_px / height
    rect = (cx - w_uv / 2.0, cy - h_uv / 2.0, cx + w_uv / 2.0, cy + h_uv / 2.0)
    return clamp_rect(rect, (MIN_CROP_SIZE, MIN_CROP_SIZE))


def oriented_ratio(ratio, portrait: bool):
    """A width/height ratio turned to the frame's orientation: at most 1 in
    portrait, at least 1 in landscape (3:2 is 2:3 in portrait). None stays
    None (Free)."""
    if not ratio:
        return ratio
    if (ratio > 1.0) == bool(portrait) and abs(ratio - 1.0) > 1e-9:
        return 1.0 / ratio
    return ratio


# ------------------------------------------------------------- straighten
def angle_from_line(x0, y0, x1, y1):
    """Straighten angle from a dragged horizon/vertical line: the rotation
    that makes the line level (nearest axis, LR-style), clamped to +/-45.
    Screen coordinates (y down)."""
    dx, dy = float(x1 - x0), float(y1 - y0)
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return 0.0
    angle = math.degrees(math.atan2(dy, dx))
    # fold to the nearest axis: result in (-45, 45]
    while angle <= -90.0:
        angle += 180.0
    while angle > 90.0:
        angle -= 180.0
    if angle > 45.0:
        angle -= 90.0
    elif angle <= -45.0:
        angle += 90.0
    return max(min(angle, 45.0), -45.0)


#: How far inside the rotated image's mathematical edge the crop stays, in
#: image px: the renderer's bilinear resample blends the last pixel with the
#: fill, and skimage centres the turn on (W-1)/2 -- about 1.5 px
#: together, measured against apply_skew.
ROTATED_FIT_INSET_PX = 2.0


def fit_rect_in_rotated_image(rect_uv, skew_deg, image_wh):
    """The crop rect kept inside the image turned by skew_deg: SLID back in
    at its own size while that is possible, shrunk (aspect kept, about the
    slid centre) only when its size fits at no position.

    2026-09-20; see design/DECISIONS.md#d-crop_frame-323

    Exact. `apply_skew` turns the frame about its centre and keeps its size,
    so the cut must lie inside BOTH the turned image (turn a point back and
    it lands inside W x H) and the frame itself. For half-extents (a, b) each
    is a box the centre must sit in: turned back, within
    half_w - (a|cos| + b|sin|) by half_h - (a|sin| + b|cos|); unturned,
    within W/2 - a by H/2 - b. A size that empties either box is scaled down
    by exactly the factor that fills it, and the centre is then projected
    into the intersection (both boxes hold the image centre, so there always
    is one).
    """
    width, height = max(float(image_wh[0]), 1.0), max(float(image_wh[1]), 1.0)
    u0, v0, u1, v1 = (float(v) for v in rect_uv)
    if abs(float(skew_deg)) < 1e-9:
        return clamp_rect((u0, v0, u1, v1))
    inset = min(ROTATED_FIT_INSET_PX, width / 4.0, height / 4.0)
    half_w, half_h = width / 2.0 - inset, height / 2.0 - inset
    theta = math.radians(float(skew_deg))
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    abs_c, abs_s = abs(cos_t), abs(sin_t)

    def back(x, y):
        return x * cos_t - y * sin_t, x * sin_t + y * cos_t

    def forward(x, y):
        return x * cos_t + y * sin_t, -x * sin_t + y * cos_t

    cx, cy = ((u0 + u1) / 2.0 - 0.5) * width, ((v0 + v1) / 2.0 - 0.5) * height
    a, b = abs(u1 - u0) * width / 2.0, abs(v1 - v0) * height / 2.0
    # the largest scale whose size fits SOMEWHERE (every box non-empty)
    scale = 1.0
    for room, extent in ((half_w, a * abs_c + b * abs_s),
                         (half_h, a * abs_s + b * abs_c),
                         (width / 2.0, a), (height / 2.0, b)):
        if extent > 0.0:
            scale = min(scale, room / extent)
    if scale < 1.0:
        a, b = a * scale, b * scale
    room_x = max(half_w - (a * abs_c + b * abs_s), 0.0)
    room_y = max(half_h - (a * abs_s + b * abs_c), 0.0)
    free_x = max(width / 2.0 - a, 0.0)
    free_y = max(height / 2.0 - b, 0.0)
    # project the centre into both boxes in turn -- they intersect (the image
    # centre is in both), and alternating projections converge onto that
    for _ in range(32):
        turned_x, turned_y = back(cx, cy)
        cx, cy = forward(min(max(turned_x, -room_x), room_x),
                         min(max(turned_y, -room_y), room_y))
        moved_x = min(max(cx, -free_x), free_x)
        moved_y = min(max(cy, -free_y), free_y)
        if abs(moved_x - cx) < 1e-9 and abs(moved_y - cy) < 1e-9:
            cx, cy = moved_x, moved_y
            break
        cx, cy = moved_x, moved_y
    w_uv, h_uv = 2.0 * a / width, 2.0 * b / height
    cu, cv = cx / width + 0.5, cy / height + 0.5
    edges = (cu - w_uv / 2.0, cv - h_uv / 2.0, cu + w_uv / 2.0, cv + h_uv / 2.0)
    # The frame box above already holds it inside, so this takes the floating
    # -point dust off an edge that landed exactly on 0 or 1 -- and ONLY that:
    # clamping a real overshoot would resize the rect and hide the fault.
    return tuple(min(max(e, 0.0), 1.0) if abs(e - min(max(e, 0.0), 1.0)) < 1e-9 else e
                 for e in edges)


def rect_inside_frame(rect_uv) -> bool:
    return all(-1e-9 <= e <= 1.0 + 1e-9 for e in rect_uv)


def rect_fits_rotated_image(rect_uv, skew_deg, image_wh, tolerance_px=0.05) -> bool:
    """True when the rect lies inside the turned picture: each corner,
    turned back about the centre as `fit_rect_in_rotated_image` turns it,
    lands inside W x H less the same inset. Tested exactly, corner by
    corner -- comparing with a re-fit was off by the fit's own rounding
    right at the edge, which refused drags that kept a corner there."""
    if abs(float(skew_deg)) < 1e-9:
        return rect_inside_frame(rect_uv)
    width, height = max(float(image_wh[0]), 1.0), max(float(image_wh[1]), 1.0)
    inset = min(ROTATED_FIT_INSET_PX, width / 4.0, height / 4.0)
    half_w, half_h = width / 2.0 - inset + tolerance_px, height / 2.0 - inset + tolerance_px
    theta = math.radians(float(skew_deg))
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    u0, v0, u1, v1 = (float(v) for v in rect_uv)
    for u in (u0, u1):
        for v in (v0, v1):
            x, y = (u - 0.5) * width, (v - 0.5) * height
            if (abs(x * cos_t - y * sin_t) > half_w or abs(x * sin_t + y * cos_t) > half_h):
                return False
    return True


def furthest_drag(rect_at, fits, steps=30) -> float:
    """How far along a drag the frame can go and still fit: the largest t in
    [0, 1] with ``fits(rect_at(t))``, ``rect_at(0)`` being where the drag
    started. Bisected, so the frame STOPS where it meets the picture's edge
    -- nothing is slid or shrunk back in, which moved the edges the user
    was not holding."""
    if fits(rect_at(1.0)):
        return 1.0
    low, high = 0.0, 1.0
    for _ in range(steps):
        middle = (low + high) / 2.0
        if fits(rect_at(middle)):
            low = middle
        else:
            high = middle
    return low


def fit_rect_about_centre(rect_uv, skew_deg, image_wh):
    """The rect kept inside the image turned by skew_deg by SHRINKING it
    about its own centre (aspect kept) -- what a turn does to the frame the
    user set, so the part of the photo they framed stays where they put it
    (`fit_rect_in_rotated_image` slides it toward the middle instead). Only
    when no size fits at that centre does it fall back to sliding."""
    if abs(float(skew_deg)) < 1e-9:
        return clamp_rect(tuple(rect_uv))
    u0, v0, u1, v1 = (float(v) for v in rect_uv)
    cu, cv = (u0 + u1) / 2.0, (v0 + v1) / 2.0
    hu, hv = (u1 - u0) / 2.0, (v1 - v0) / 2.0

    def at(scale):
        return (cu - hu * scale, cv - hv * scale, cu + hu * scale, cv + hv * scale)

    def fits(rect):
        return rect_inside_frame(rect) and rect_fits_rotated_image(rect, skew_deg, image_wh)

    if fits(at(1.0)):
        return at(1.0)
    if not fits(at(1e-3)):
        return fit_rect_in_rotated_image(rect_uv, skew_deg, image_wh)
    return at(max(furthest_drag(at, fits), 1e-3))


# ---------------------------------------------------------- skew by dragging
def pointer_angle_deg(cx, cy, x, y):
    """The pointer's angle about (cx, cy) in screen coordinates (y down),
    counter-clockwise positive -- the sense skew_deg turns the picture in, so
    dragging round the centre turns the image with the hand (2026-09-19)."""
    return math.degrees(math.atan2(-(float(y) - float(cy)), float(x) - float(cx)))


def wrap_angle_deg(angle):
    """An angle difference folded into (-180, 180]: a drag that crosses the
    +-180 line is a small turn, not a full one."""
    angle = math.fmod(float(angle), 360.0)
    if angle <= -180.0:
        angle += 360.0
    elif angle > 180.0:
        angle -= 360.0
    return angle


