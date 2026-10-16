"""`.viewrail` — how you look, on the image itself (20.2, port wave 6).

20.2, rule R1: the looking tools leave the top bar for a small vertical rail
that floats in a corner of the viewer:

    grip | crop (swap while cropping) | fit, 1:1 | loupe, scopes, test strip

2026-10-06; see design/DECISIONS.md#d-viewer_rail-868

2026-08-03; see design/DECISIONS.md#d-viewer_rail-869
"""

from __future__ import annotations

from typing import Callable

from qtpy import QtCore, QtGui, QtWidgets

from spektrafilm_gui.topbar import (
    ICON_BUTTON_PX,
    TopBar,
    ZOOM_DEFAULT,
    ZoomFlyout,
    _icon_button,
    refresh_icon_tint,
)
from spektrafilm_gui.app_settings import app_settings
from spektrafilm_gui.theme_tokens import RADIUS_5, RADIUS_7

Qt = QtCore.Qt

#: `.viewrail[data-corner] { left|right: 14px; top|bottom: 14px }`
RAIL_INSET = 14
#: `.viewrail { padding: 4px; gap: 2px }`
RAIL_PADDING = 4
RAIL_GAP = 2
#: `.viewrail .grip { height: 12px }`, `::before { width: 16px; height: 3px; top: 4px }`
GRIP_HEIGHT = 12
GRIP_BAR = (16, 3)
#: `.viewrail .rdiv { width: 18px; height: 1px; margin: 3px auto }` -- the
#: box is 7 tall and the sheet's 3px margin leaves the 1px line
DIVIDER_SIZE = (18, 7)
CORNERS = ('tl', 'tr', 'bl', 'br')
SETTINGS_KEY = 'ui/rail_corner'


def view_rail_sheet() -> str:
    """`.viewrail { background: var(--bg-1); border: 1px solid var(--line);
    border-radius: var(--r-l) }`, its `.iconbtn`s and `.rdiv`s.

    LOCAL, for the same reason as the frame bar's: the rail sits inside the
    viewer panel, whose own sheet would otherwise make it transparent and
    give its push-button instruments a control fill.
    A QSS `min-height` REPLACES a widget's minimum size, so a blanket
    `min-height: 0` undid the tool buttons' fixed 30px and the layout
    squeezed them to 19; and the app's `iconButton` role rule does the same
    to the adopted push buttons. So the push buttons' box is stated here
    (30 x 30, no padding, no border: the content box is the box).
    """
    from spektrafilm_gui.theme_palette import (
        BORDER_LINE, BORDER_LINE_STRONG, CARD_BG, CONTROL_BG, CONTROL_BG_HOVER,
        TEXT_BRIGHT, TEXT_DIM)

    buttons = 'QFrame#viewRail QToolButton, QFrame#viewRail QPushButton'
    return (
        f'QFrame#viewRail {{ background: {CARD_BG}; border: 1px solid {BORDER_LINE};'
        f' border-radius: {RADIUS_7}px; }}'
        f'QFrame#viewRail[dragging="true"] {{ border-color: {BORDER_LINE_STRONG}; }}'
        'QFrame#viewRail QWidget { background: transparent; }'
        f'{buttons} {{ background: transparent; border: none; border-radius: {RADIUS_5}px;'
        f' padding: 0px; color: {TEXT_DIM}; }}'
        'QFrame#viewRail QToolButton:hover, QFrame#viewRail QPushButton:hover'
        f' {{ background: {CONTROL_BG}; color: {TEXT_BRIGHT}; }}'
        'QFrame#viewRail QToolButton:checked, QFrame#viewRail QPushButton:checked,'
        ' QFrame#viewRail QToolButton:pressed, QFrame#viewRail QPushButton:pressed'
        f' {{ background: {CONTROL_BG_HOVER}; color: {TEXT_BRIGHT}; }}'
        'QFrame#viewRail QToolButton:disabled, QFrame#viewRail QPushButton:disabled'
        ' { background: transparent; }'
        # scoped under the rail, or `QFrame#viewRail QWidget` (one more type)
        # outranks it and the line goes transparent
        f'QFrame#viewRail QFrame#railDivider {{ background: {BORDER_LINE}; border: none;'
        ' margin: 3px 0px; }'
        f'QFrame#viewRail QPushButton {{ min-height: {ICON_BUTTON_PX}px;'
        f' max-height: {ICON_BUTTON_PX}px; min-width: {ICON_BUTTON_PX}px;'
        f' max-width: {ICON_BUTTON_PX}px; }}'
    )


def _saved_corner() -> str:
    try:
        value = str(app_settings()
                    .value(SETTINGS_KEY, 'tl') or 'tl')
    except Exception:
        return 'tl'
    return value if value in CORNERS else 'tl'


def _save_corner(corner: str) -> None:
    try:
        app_settings().setValue(SETTINGS_KEY, corner)
    except Exception:
        pass


class RailGrip(QtWidgets.QWidget):
    """The grabber: a 16x3 pill, brighter under the pointer and while it
    drags. It reports the drag; the rail moves and snaps."""

    pressed = QtCore.Signal(QtCore.QPoint)
    moved = QtCore.Signal(QtCore.QPoint)
    released = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName('railGrip')
        self.setFixedHeight(GRIP_HEIGHT)
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip('拖拽移动至其他角落')
        self.setAttribute(Qt.WA_Hover, True)
        self._dragging = False

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API name
        from spektrafilm_gui.theme_palette import BORDER_LINE_STRONG, TEXT_DIM

        lit = self._dragging or self.underMouse()
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QtGui.QColor(TEXT_DIM if lit else BORDER_LINE_STRONG))
        width, height = GRIP_BAR
        rect = QtCore.QRectF((self.width() - width) / 2.0, 4, width, height)
        painter.drawRoundedRect(rect, height / 2.0, height / 2.0)
        painter.end()

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt API name
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API name
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API name
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self.setCursor(Qt.ClosedHandCursor)
            self.pressed.emit(event.globalPosition().toPoint())
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API name
        if self._dragging:
            self.moved.emit(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API name
        if self._dragging and event.button() == Qt.LeftButton:
            self._dragging = False
            self.setCursor(Qt.OpenHandCursor)
            self.released.emit()
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)


def _rail_divider() -> QtWidgets.QFrame:
    line = QtWidgets.QFrame()
    line.setObjectName('railDivider')
    line.setFixedSize(*DIVIDER_SIZE)
    return line


class ViewRail(QtWidgets.QFrame):
    """The rail. A child of the viewer's stage area, kept in a corner."""

    def __init__(
        self,
        *,
        on_crop: Callable | None = None,
        on_swap: Callable[[], None] | None = None,
        on_fit: Callable[[], None] | None = None,
        on_zoom_percent: Callable[[float], None] | None = None,
        on_scopes: Callable[[], None] | None = None,
        instruments: dict[str, QtWidgets.QWidget] | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName('viewRail')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(view_rail_sheet())
        self.setProperty('dragging', 'false')
        self._corner = _saved_corner()
        self._drag_offset: QtCore.QPoint | None = None

        column = QtWidgets.QVBoxLayout(self)
        column.setContentsMargins(RAIL_PADDING, RAIL_PADDING, RAIL_PADDING, RAIL_PADDING)
        column.setSpacing(RAIL_GAP)

        self.grip = RailGrip()
        self.grip.pressed.connect(self._drag_start)
        self.grip.moved.connect(self._drag_move)
        self.grip.released.connect(self._drag_end)
        column.addWidget(self.grip)

        # QA 10-06 (NAT): Crop is the rail's first tool
        self.crop_button = _icon_button('crop', '画面裁剪', on_crop,
                                        object_name='cropModeButton', checkable=True)
        self.swap_button = _icon_button('swap', '切换裁剪方向 (横构图/竖构图)', on_swap,
                                        object_name='swapCropButton')
        self.swap_button.setVisible(False)
        self.crop_button.toggled.connect(self.swap_button.setVisible)
        self.crop_button.toggled.connect(lambda *_: self._snap())
        self.crop_button.toggled.connect(
            lambda checked: refresh_icon_tint(self.crop_button, active=checked))

        self.fit_button = _icon_button('fit', '适屏显示 (全图视野)', on_fit,
                                       object_name='homeViewButton', checkable=True)
        self.fit_button.toggled.connect(
            lambda checked: refresh_icon_tint(self.fit_button, active=checked))

        self.zoom_100_button = QtWidgets.QToolButton()
        self.zoom_100_button.setObjectName('zoom100Button')
        self.zoom_100_button.setText(ZOOM_DEFAULT)
        self.zoom_100_button.setFixedSize(ICON_BUTTON_PX, ICON_BUTTON_PX)
        self.zoom_100_button.setCheckable(True)
        self.zoom_100_button.setCursor(Qt.PointingHandCursor)
        self.zoom_100_button.setProperty('role', 'zoomRatio')
        self.zoom_100_button.setToolTip(
            '1:1 像素点对点显示。悬停可选择 1:2、2:1 或 4:1 缩放比例。')

        # 20.2: the stops open BESIDE the rail, on its free side
        self.zoom_flyout = ZoomFlyout(self)
        self.zoom_flyout.side_of = lambda: 'left' if self._corner[1] == 'r' else 'right'
        self.zoom_flyout.arm(self.zoom_100_button)
        self._on_zoom_percent = on_zoom_percent
        self.zoom_flyout.picked.connect(self._apply_zoom)
        self.zoom_100_button.clicked.connect(lambda *_: self._apply_zoom(100.0))
        # ONE zoom is in force at a time; pressing the lit one re-applies it
        for pressed, other in ((self.fit_button, self.zoom_100_button),
                               (self.zoom_100_button, self.fit_button)):
            pressed.clicked.connect(
                lambda *_, p=pressed, o=other: (p.setChecked(True), o.setChecked(False)))
        self.fit_button.clicked.connect(lambda *_: self.set_zoom_percent(None))

        instruments = instruments or {}
        self.loupe_button = self._adopt(instruments.get('loupe'), 'topbarLoupeButton', 'loupe')
        self.scopes_button = (
            _icon_button('scope', '色彩示波器 (Scopes)', on_scopes, object_name='scopesButton',
                         checkable=True)
            if on_scopes is not None else None)
        if self.scopes_button is not None:
            self.scopes_button.toggled.connect(
                lambda checked: refresh_icon_tint(self.scopes_button, active=checked))
        self.strip_button = self._adopt(instruments.get('test_strip'),
                                        'topbarTestStripButton', 'strip')

        groups = ((self.crop_button, self.swap_button),
                  (self.fit_button, self.zoom_100_button),
                  (self.loupe_button, self.scopes_button, self.strip_button))
        first = True
        for group in groups:
            present = [w for w in group if w is not None]
            if not present:
                continue
            if not first:
                column.addWidget(_rail_divider(), 0, Qt.AlignHCenter)
            first = False
            for widget in present:
                column.addWidget(widget, 0, Qt.AlignHCenter)

        if parent is not None:
            parent.installEventFilter(self)
        self.adjustSize()

    def attach(self, stage: QtWidgets.QWidget) -> None:
        """Float over `stage` (the viewer's `StageArea`) and keep its corner
        as the stage resizes."""
        add_hud = getattr(stage, 'add_hud', None)
        if callable(add_hud):
            add_hud(self)
        else:
            self.setParent(stage)
            self.show()
        stage.installEventFilter(self)
        self._snap()

    # ---------------------------------------------------------------- adopt
    @staticmethod
    def _adopt(widget: QtWidgets.QWidget | None, object_name: str,
               glyph: str) -> QtWidgets.QWidget | None:
        """The instrument buttons are the SAME widgets the simulation section
        built, so their controller wiring and state move with them."""
        if widget is None:
            return None

        TopBar._adopt(widget, object_name, glyph=glyph)
        return widget

    # ----------------------------------------------------------------- zoom
    def _apply_zoom(self, percent: float) -> None:
        self.set_zoom_percent(percent)
        if self._on_zoom_percent is not None:
            self._on_zoom_percent(float(percent))

    def set_zoom_percent(self, percent: float | None) -> None:
        """Reflect the zoom in force, without asking for it. The button reads
        the ratio; it is lit only while the view sits ON a stop."""
        self.zoom_flyout.set_percent(percent)
        self.zoom_100_button.setText(self.zoom_flyout.label())
        self.zoom_100_button.setChecked(self.zoom_flyout.percent() is not None)

    def set_tools_enabled(self, enabled: bool) -> None:
        """An empty roll has nothing to look at: every tool goes quiet
        (`.is-empty-roll .viewrail button`), the grip still drags."""
        for button in self.findChildren(QtWidgets.QAbstractButton):
            button.setEnabled(bool(enabled))

    # --------------------------------------------------------------- corner
    def corner(self) -> str:
        return self._corner

    def set_corner(self, corner: str, *, save: bool = True) -> None:
        if corner not in CORNERS:
            return
        self._corner = corner
        if save:
            _save_corner(corner)
        self._snap()

    def _snap(self) -> None:
        """Into its corner, `RAIL_INSET` from the stage's edges."""
        parent = self.parentWidget()
        if parent is None:
            return
        self.adjustSize()
        x = (RAIL_INSET if self._corner[1] == 'l'
             else parent.width() - self.width() - RAIL_INSET)
        y = (RAIL_INSET if self._corner[0] == 't'
             else parent.height() - self.height() - RAIL_INSET)
        self.move(max(0, x), max(0, y))
        self.raise_()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API name
        if watched is self.parentWidget() and event.type() == QtCore.QEvent.Resize:
            if self._drag_offset is None:
                self._snap()
        return False

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API name
        super().showEvent(event)
        self._snap()

    # ----------------------------------------------------------------- drag
    def _set_dragging(self, on: bool) -> None:
        self.setProperty('dragging', 'true' if on else 'false')
        self.style().unpolish(self)
        self.style().polish(self)

    def _drag_start(self, global_point: QtCore.QPoint) -> None:
        self._drag_offset = global_point - self.mapToGlobal(QtCore.QPoint(0, 0))
        self._set_dragging(True)
        self.raise_()

    def _drag_move(self, global_point: QtCore.QPoint) -> None:
        parent = self.parentWidget()
        if parent is None or self._drag_offset is None:
            return
        local = parent.mapFromGlobal(global_point - self._drag_offset)
        x = max(4, min(parent.width() - self.width() - 4, local.x()))
        y = max(4, min(parent.height() - self.height() - 4, local.y()))
        self.move(x, y)

    def _drag_end(self) -> None:
        """Snap to the nearest corner and remember it."""
        parent = self.parentWidget()
        self._drag_offset = None
        self._set_dragging(False)
        if parent is None:
            return
        centre = self.geometry().center()
        corner = (('t' if centre.y() < parent.height() / 2 else 'b')
                  + ('l' if centre.x() < parent.width() / 2 else 'r'))
        self.set_corner(corner)
