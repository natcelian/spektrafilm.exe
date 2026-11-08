"""The final design's icon set, taken verbatim from the spec (Phase 18D).

2026-10-07; see design/DECISIONS.md#d-dyeworks_icons-349

Two other reasons this is a module rather than a `pyconify` lookup:

  - it is OFFLINE. `icons.section_header_icon` fetches from the network and
    degrades to a blank when that fails; the top bar is the app's primary
    control surface and cannot depend on connectivity.
  - it is TINTABLE. Every glyph is stroked in `currentColor`, so one path
    serves the rest / hover / pressed colours without three assets.

All symbols share a 16x16 viewBox, 1.4 stroke, round caps and joins, exactly
as `.ic` sets them in the HTML -- except `step`, see `_VIEWBOX`.
"""

from __future__ import annotations

from qtpy import QtCore, QtGui, QtSvg

# name -> the symbol's inner markup, verbatim from the spec.
_PATHS: dict[str, str] = {
    'panel': '<rect x="2" y="3" width="12" height="10" rx="2"/><path d="M6.4 3v10"/>',
    'scan': ('<path d="M2.6 5.4V3.4h2M13.4 5.4V3.4h-2M2.6 10.6v2h2'
             'M13.4 10.6v2h-2M2 8h12"/>'),
    'roll': ('<rect x="2.5" y="3.5" width="11" height="9" rx="1.5"/>'
             '<path d="M4.8 6h1M4.8 10h1M10.2 6h1M10.2 10h1"/>'),
    'crop': '<path d="M4.5 1.5v10h10M1.5 4.5h10v10"/>',
    'undo': '<path d="M3 8a5 5 0 1 1 1.6 3.7"/><path d="M2.4 4.6v3.2h3.2"/>',
    'share': ('<path d="M8 10.5V2.4M5.4 5l2.6-2.6L10.6 5"/>'
              '<path d="M3 9.5v3.6a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1V9.5"/>'),
    'folder': ('<path d="M1.8 12.6V4.2a.8.8 0 0 1 .8-.8h3.2l1.4 1.6h5.9a.8.8 0 0 1 '
               '.8.8v6.8a.8.8 0 0 1-.8.8H2.6a.8.8 0 0 1-.8-.8Z"/>'),
    'scope': ('<rect x="1.8" y="3" width="12.4" height="10" rx="2"/>'
              '<path d="M3.6 10.6C5 10.6 5.4 6 7 6s2 6 3.4 6 1.4-4.6 2.4-4.6"/>'),
    'eye': ('<path d="M1.8 8s2.3-4 6.2-4 6.2 4 6.2 4-2.3 4-6.2 4-6.2-4-6.2-4Z"/>'
            '<circle cx="8" cy="8" r="1.8"/>'),
    'loupe': '<circle cx="7" cy="7" r="4.6"/><path d="M10.4 10.4 14 14"/>',
    'strip': ('<rect x="1.8" y="3" width="12.4" height="10" rx="1.6"/>'
              '<path d="M5.9 3v10M10.1 3v10"/>'),
    'shelf': ('<rect x="2" y="2" width="5.2" height="5.2" rx="1.2"/>'
              '<rect x="8.8" y="2" width="5.2" height="5.2" rx="1.2"/>'
              '<rect x="2" y="8.8" width="5.2" height="5.2" rx="1.2"/>'
              '<rect x="8.8" y="8.8" width="5.2" height="5.2" rx="1.2"/>'),
    'lut': ('<rect x="2" y="2" width="12" height="12" rx="2"/>'
            '<path d="M2 6.7h12M2 11.4h12M6.7 2v12M11.4 2v12" stroke-width=".9"/>'),
    'x': '<path d="M3 3l10 10M13 3 3 13" stroke-width="1.8"/>',
    'arrow-l': '<path d="M10.5 2.8 5.3 8l5.2 5.2"/>',
    'arrow-r': '<path d="M5.5 2.8 10.7 8l-5.2 5.2"/>',
    'flag': '<path d="M3.6 14.2V2.2"/><path d="M3.6 2.6h8.2l-2 2.9 2 2.9H3.6"/>',
    'rot-l': ('<path d="M4.6 6.2A5 5 0 1 1 3.4 9.8"/>'
              '<path d="M4.9 2.9v3.3h3.3" transform="translate(-1.2,0)"/>'),
    'rot-r': ('<path d="M11.4 6.2A5 5 0 1 0 12.6 9.8"/>'
              '<path d="M12.3 2.9v3.3H9" transform="translate(1.2,0)"/>'),
    'redo': '<path d="M13 8a5 5 0 1 0-1.6 3.7"/><path d="M13.6 4.6v3.2h-3.2"/>',
    'fit': ('<path d="M6 2.5H3.5a1 1 0 0 0-1 1V6M10 2.5h2.5a1 1 0 0 1 1 1V6'
            'M6 13.5H3.5a1 1 0 0 1-1-1V10M10 13.5h2.5a1 1 0 0 0 1-1V10"/>'),
    'keep': '<path d="M4 2.5h8a.8.8 0 0 1 .8.8v10.4L8 10.6l-4.8 3.1V3.3a.8.8 0 0 1 .8-.8Z"/>',
    'discard': ('<path d="M4 2.5h8a.8.8 0 0 1 .8.8v10.4L8 10.6l-4.8 3.1V3.3a.8.8 0 0 1 .8-.8Z"/>'
                '<path d="M6.3 5.2l3.4 3.4M9.7 5.2 6.3 8.6"/>'),
    'star': ('<path d="M8 1.8l1.9 3.9 4.3.6-3.1 3 .7 4.3L8 11.6l-3.8 2 .7-4.3-3.1-3 '
             '4.3-.6z"/>'),
    'info': '<circle cx="8" cy="8" r="6.2"/><path d="M8 7.2v3.6M8 5.1v.1"/>',
    'ab': ('<rect x="1.8" y="3" width="12.4" height="10" rx="1.8"/><path d="M8 3v10"/>'
           '<path d="M4 10.2 5.2 6.6 6.4 10.2M4.35 9.2h1.7" stroke-width="1"/>'
           '<path d="M9.8 6.6h1.5a1 1 0 0 1 0 1.8H9.8m0-1.8v3.6m0-1.8h1.7a1 1 0 0 1 0 '
           '1.8H9.8" stroke-width="1"/>'),
    # white-balance presets
    'wb-cam': ('<rect x="1.8" y="4.4" width="12.4" height="8.6" rx="1.6"/>'
               '<path d="M5.4 4.4 6.5 2.8h3l1.1 1.6"/><circle cx="8" cy="8.6" r="2.4"/>'),
    'wb-sun': ('<circle cx="8" cy="8" r="2.8"/><path d="M8 1.6v1.8M8 12.6v1.8M1.6 8h1.8'
               'M12.6 8h1.8M3.5 3.5l1.3 1.3M11.2 11.2l1.3 1.3M12.5 3.5l-1.3 1.3'
               'M4.8 11.2l-1.3 1.3"/>'),
    'wb-cloud': ('<path d="M4.4 12.4a2.9 2.9 0 0 1-.3-5.8 3.9 3.9 0 0 1 7.6-.9 2.7 2.7 0 0 1'
                 '-.4 5.4h-2"/><path d="M4.4 12.4h5"/>'),
    'wb-bulb': ('<path d="M5 6.4a3 3 0 1 1 6 0c0 1.7-1.3 2.3-1.5 3.6h-3C6.3 8.7 5 8.1 5 6.4Z"/>'
                '<path d="M6.6 12h2.8M7.1 13.8h1.8"/>'),
    'wb-tune': ('<path d="M2.4 5h7M11.8 5h1.8M2.4 11h2.4M7.2 11h6.4"/>'
                '<circle cx="10.4" cy="5" r="1.5"/><circle cx="5.8" cy="11" r="1.5"/>'),
    # NOTE the spaces at the wrap points: this is one `d` attribute, and
    # joining two literals without them produced '3.8 3.81-1a...', which the
    # SVG parser rejects and renders as an empty button.
    'wb-pick': ('<path d="M9.6 3.6l2.8 2.8M13.5 4.7 11.3 2.5a1.1 1.1 0 0 0-1.6 0'
                'l-1 1 3.8 3.8 1-1a1.1 1.1 0 0 0 0-1.6ZM9.9 6.9 4.3 12.5'
                'l-2 .7.7-2 5.6-5.6"/>'),
    # metering modes (camera-style)
    'met-cw': ('<rect x="1.8" y="3.2" width="12.4" height="9.6" rx="1.6"/>'
               '<circle cx="8" cy="8" r="2.5"/>'
               '<circle cx="8" cy="8" r=".9" fill="currentColor" stroke="none"/>'),
    'met-matrix': ('<rect x="1.8" y="3.2" width="12.4" height="9.6" rx="1.6"/>'
                   '<path d="M5.9 3.2v9.6M10.1 3.2v9.6M1.8 6.4h12.4M1.8 9.6h12.4" '
                   'stroke-width=".9"/>'),
    'met-partial': ('<rect x="1.8" y="3.2" width="12.4" height="9.6" rx="1.6"/>'
                    '<circle cx="8" cy="8" r="3.2"/>'),
    # 2026-09-30; see design/DECISIONS.md#d-dyeworks_icons-347
    'met-mz': ('<rect x="1.8" y="3.2" width="12.4" height="9.6" rx="1.6"/>'
               '<circle cx="5.2" cy="6" r=".8" fill="currentColor" stroke="none"/>'
               '<circle cx="10.8" cy="6" r=".8" fill="currentColor" stroke="none"/>'
               '<circle cx="5.2" cy="10" r=".8" fill="currentColor" stroke="none"/>'
               '<circle cx="10.8" cy="10" r=".8" fill="currentColor" stroke="none"/>'
               '<circle cx="8" cy="8" r=".8" fill="currentColor" stroke="none"/>'),
    'met-hl': ('<rect x="1.8" y="3.2" width="12.4" height="9.6" rx="1.6"/>'
               '<circle cx="8" cy="8" r="1.6"/>'
               '<path d="M8 4.6v-.8M8 12.2v-.8M4.6 8h-.8M12.2 8h-.8"/>'),
    'met-med': ('<rect x="1.8" y="3.2" width="12.4" height="9.6" rx="1.6"/>'
                '<path d="M4 8h8"/>'
                '<path d="M4 5.8h4M4 10.2h4" stroke-width=".9" opacity=".55"/>'),
    'met-avg': ('<rect x="1.8" y="3.2" width="12.4" height="9.6" rx="1.6"/>'
                '<path d="M4 5.8h8M4 8h8M4 10.2h8" stroke-width=".9"/>'),
    'swap': ('<rect x="2" y="4.5" width="8.5" height="7" rx="1.4"/>'
             '<path d="M12.8 3.2v4.2M12.8 3.2 11.2 4.8M12.8 3.2l1.6 1.6" '
             'transform="translate(0,1)"/>'),
    # 20.2 (port wave 6): the top bar's Compare, its caret and the pipeline
    # step between Input, Negative and Print -- `#i-cmp`, `#i-down`, `#i-step`
    'cmp': '<rect x="1.8" y="3" width="12.4" height="10" rx="1.8"/><path d="M8 1.8v12.4"/>',
    # the crop's Straighten (a ruler on the slant) and its orientation pair
    'straighten': '<path d="M2 11.6 14 4.4"/><path d="M4.9 9.9l.8 1.3M7.9 8.1l.8 1.3M10.9 6.3l.8 1.3"/>',
    'landscape': '<rect x="2" y="4.5" width="12" height="7" rx="1.4"/>',
    'portrait': '<rect x="4.5" y="2" width="7" height="12" rx="1.4"/>',
    'down': '<path d="M4 6l4 4 4-4"/>',
    'step': '<path d="M3.6 1.8 6.8 5 3.6 8.2"/>',
}

#: The few glyphs that are NOT on the 16-unit grid: `#i-step` is drawn in a
#: 10x10 viewBox and stroked at 1.3 (`.viewstep .step-ar`).
_VIEWBOX: dict[str, int] = {'step': 10}
_STROKE_FOR: dict[str, float] = {'step': 1.3}

# 2026-09-07; see design/DECISIONS.md#d-dyeworks_icons-348
ICON_PX = 16
_STROKE = 1.4

_cache: dict[tuple[str, str, int, bool], QtGui.QIcon] = {}


#: `.reset .ic { width: 12px; height: 12px }` -- the per-parameter reset arrow.
RESET_ICON_PX = 12


def reset_icon(normal: str, active: str, *, size: int = RESET_ICON_PX) -> QtGui.QIcon:
    """The page's `#i-undo` arrow as a button icon: `normal` at rest, `active`
    under the cursor (`.reset:hover { color: var(--ink) }`)."""
    icon = QtGui.QIcon()
    for mode, colour in ((QtGui.QIcon.Normal, normal), (QtGui.QIcon.Active, active)):
        icon.addPixmap(dyeworks_icon('undo', colour, size=size).pixmap(size, size), mode)
    return icon


def icon_names() -> tuple[str, ...]:
    return tuple(sorted(_PATHS))


def _svg(name: str, color: str, *, filled: bool) -> bytes:
    body = _PATHS[name]
    fill = color if filled else 'none'
    box = _VIEWBOX.get(name, 16)
    stroke = _STROKE_FOR.get(name, _STROKE)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {box} {box}" '
        f'fill="{fill}" stroke="{color}" stroke-width="{stroke}" '
        f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    ).encode('utf-8')


def dyeworks_icon(name: str, color: str, *, size: int = ICON_PX,
               filled: bool = False) -> QtGui.QIcon:
    """One glyph from the spec, stroked in `color`.

    `filled` is for the two state glyphs the design fills when active -- a
    rated star and a flagged frame -- rather than a separate asset.
    Returns an empty QIcon for an unknown name instead of raising: a missing
    glyph should leave a blank button, never take down the top bar.
    """
    if name not in _PATHS:
        return QtGui.QIcon()
    key = (name, color, size, filled)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    renderer = QtSvg.QSvgRenderer(QtCore.QByteArray(_svg(name, color, filled=filled)))
    # Render at 2x and let Qt downsample: the strokes are 1.4px on a 16px
    # grid and alias badly if rasterised at exactly the target size.
    pixmap = QtGui.QPixmap(size * 2, size * 2)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    try:
        renderer.render(painter)
    finally:
        painter.end()
    pixmap.setDevicePixelRatio(2.0)
    result = QtGui.QIcon(pixmap)
    _cache[key] = result
    return result
