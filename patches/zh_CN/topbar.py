"""The application top bar (Phase 18D; regrouped for 20.2, port wave 6).

2026-10-06; see design/DECISIONS.md#d-topbar-837

    left    library panel toggle | shelf
    centre  Input > Negative > Print (what you see) | Compare and its target
    right   Reset | Preview | Scan | Export (a menu: the image or a LUT)
            | edit-panel toggle

The frame's own actions (prev / next, rotate, flag, keep, discard, the
rating) moved to the FRAME BAR over the filmstrip (`frame_bar`), and the
looking tools (crop, fit, 1:1, loupe, scopes, test strip) to the RAIL on the
image (`viewer_rail`).

STRUCTURE ONLY. Every button here forwards to a callback the controller
already exposes. `ViewSwitch` replaces the old Print | Split | Negative
segment AND the Input/Edit flip, and answers the controller with the same
contract both of them did (`set_mode`, `is_split`, `set_*_available`,
`set_input_active`, and the 'off' | 'input' | 'negative' | 'reference' |
'kept' compare modes), so no render path changes.

NAT 2026-07-30, on the controls the design has no slot for: zoom 200%/400%,
mirror h/v, the canvas background swatches and Full screen are DROPPED from
the UI. Their shortcuts remain the access path (Ctrl+L cycles the
background, F11/Ctrl+P is full screen, and mirror gained Ctrl+Shift+H /
Ctrl+Shift+V so the capability did not disappear with its buttons).
"""

from __future__ import annotations

from typing import Callable

from qtpy import QtCore, QtWidgets

from qtpy import QtGui

from spektrafilm_gui import motion
from spektrafilm_gui.dyeworks_icons import ICON_PX, dyeworks_icon
from spektrafilm_gui.theme_palette import STATE_KEPT, TEXT_DIM, TEXT_HI
import inspect
from spektrafilm_gui.dyeworks_menu import DyeworksMenu

Qt = QtCore.Qt

#: `.app { grid-template-rows: 56px ... }`
TOPBAR_HEIGHT = 56
#: `.iconbtn { width: 30px; height: 30px }`
ICON_BUTTON_PX = 30
#: `.tb-div { width: 1px; height: 18px }`
DIVIDER_PX = 18


def _icon_button(name: str, tooltip: str, callback: Callable[[], None] | None,
                 *, object_name: str = '', checkable: bool = False) -> QtWidgets.QToolButton:
    button = QtWidgets.QToolButton()
    if object_name:
        button.setObjectName(object_name)
    button.setIcon(dyeworks_icon(name, TEXT_DIM))
    button.setIconSize(QtCore.QSize(ICON_PX, ICON_PX))
    button.setFixedSize(ICON_BUTTON_PX, ICON_BUTTON_PX)
    button.setToolTip(tooltip)
    button.setCheckable(checkable)
    button.setCursor(Qt.PointingHandCursor)
    button.setAutoRaise(True)
    # The glyph is stroked in currentColor in the design; Qt cannot restyle a
    # rendered pixmap, so the two states are two cached icons.
    button.setProperty('dyeworksIcon', name)
    if callback is not None:
        _connect(button, callback)
    return button


def _connect(button: QtWidgets.QAbstractButton, callback) -> None:
    """Wire a button, passing the toggle state only if it is wanted.

    2026-08-03; see design/DECISIONS.md#d-topbar-838
    """
    if _wants_state(callback):
        button.clicked.connect(lambda checked=False: callback(bool(checked)))
    else:
        button.clicked.connect(lambda *_: callback())


def _wants_state(callback) -> bool:
    """Does this callback take a REQUIRED positional argument?

    Anything not introspectable counts as no-arg: that is what every such
    callback here has been, and guessing the other way would break all of
    them rather than one.
    """

    try:
        parameters = inspect.signature(callback).parameters
    except (TypeError, ValueError):
        return False
    for parameter in parameters.values():
        if parameter.kind in (parameter.POSITIONAL_ONLY,
                              parameter.POSITIONAL_OR_KEYWORD):
            return parameter.default is parameter.empty
        if parameter.kind is parameter.VAR_POSITIONAL:
            return False
    return False


def refresh_icon_tint(button: QtWidgets.QToolButton, *, active: bool) -> None:
    """Re-tint a top-bar glyph. Called when a toggle's state changes."""
    name = button.property('dyeworksIcon')
    if not name:
        return
    button.setIcon(dyeworks_icon(str(name), TEXT_HI if active else TEXT_DIM))


def _divider() -> QtWidgets.QFrame:
    line = QtWidgets.QFrame()
    line.setObjectName('topbarDivider')
    line.setFrameShape(QtWidgets.QFrame.VLine)
    line.setFixedSize(1, DIVIDER_PX)
    return line


def _text_button(text: str, role: str, tooltip: str,
                 callback: Callable[[], None] | None,
                 *, icon: str = '', object_name: str = '') -> QtWidgets.QPushButton:
    button = QtWidgets.QPushButton(text)
    if object_name:
        button.setObjectName(object_name)
    button.setProperty('role', role)
    button.setToolTip(tooltip)
    button.setCursor(Qt.PointingHandCursor)
    if icon:
        button.setIcon(dyeworks_icon(icon, TEXT_HI))
        button.setIconSize(QtCore.QSize(ICON_PX, ICON_PX))
    if callback is not None:
        _connect(button, callback)
    return button


def _mirrored(icon: QtGui.QIcon) -> QtGui.QIcon:
    """`.flipx { transform: scaleX(-1) }` for an icon."""
    pixmap = icon.pixmap(QtCore.QSize(ICON_PX, ICON_PX), 2.0)
    if pixmap.isNull():
        return icon
    flipped = pixmap.transformed(QtGui.QTransform().scale(-1, 1))
    flipped.setDevicePixelRatio(pixmap.devicePixelRatio())
    return QtGui.QIcon(flipped)


#: R12 (`@media (max-width: ...)`): under 1440px the right-hand labels fold
#: to their icons; under 1240px Compare's does too
FOLD_GHOST_LABELS_BELOW = 1440
FOLD_COMPARE_LABEL_BELOW = 1240
#: `.ic.sm { width: 11px; height: 11px }` -- the carets
CARET_PX = 11
#: `.viewstep`, `.splitbtn`, `.topbar .primary, .topbar .ghost`: 32px
VIEW_HEIGHT = 32
#: `.viewstep button { height: 24px }`
VIEW_STEP_BUTTON_HEIGHT = 24
#: `.viewstep .step-ar { width: 10px; margin: 0 6px }`
STEP_ARROW_PX = 10
STEP_ARROW_MARGIN = 6

#: What Compare can put beside the live print, in the page's order
#: (`SPLIT_ORDER`), with the controller's mode names.
COMPARE_TARGETS: tuple[tuple[str, str], ...] = (
    ('input', 'Input'), ('negative', 'Negative'),
    ('reference', 'Reference frame'), ('kept', 'Kept version'))
_TARGET_WORDS = {'input': 'the input', 'negative': 'the negative',
                 'reference': 'the reference frame',
                 'kept': 'the last kept version'}


class ViewSwitch(QtWidgets.QWidget):
    """`.viewstep` + `.splitbtn`: Input > Negative > Print, and Compare.

    2026-10-06; see design/DECISIONS.md#d-topbar-839

    2026-07-25, 2026-09-10, 2026-10-07; see design/DECISIONS.md#d-topbar-840

    Compare is a MODIFIER: it survives a change between Negative and Print
    (`applyView` keeps `compareOn`), and only Input, which has nothing to
    compare, turns it off.
    """

    compare_changed = QtCore.Signal(str)
    input_mode_changed = QtCore.Signal(bool)

    _VIEWS = (('input', '原片', '原始输入图像：整理、双面板收起'),
              ('negative', '底片', '冲洗后的负片'),
              ('print', '印相', '最终印相/正片'))

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName('viewSwitch')
        # The app opens in input mode (NAT 2026-07-26), so the switch does.
        self._view = 'input'
        self._compare = False
        self._target = 'input'
        self._reference_available = False
        self._kept_available = False

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)                        # `.tb-mid { gap: 10px }`

        # -- the view, as the pipeline runs
        self.step = QtWidgets.QFrame()
        self.step.setObjectName('viewStep')
        self.step.setFixedHeight(VIEW_HEIGHT)
        step_layout = QtWidgets.QHBoxLayout(self.step)
        step_layout.setContentsMargins(3, 0, 3, 0)   # `padding: 0 3px`
        step_layout.setSpacing(0)
        self._view_buttons: dict[str, QtWidgets.QPushButton] = {}
        group = QtWidgets.QButtonGroup(self)
        group.setExclusive(True)
        for index, (key, label, tooltip) in enumerate(self._VIEWS):
            if index:
                arrow = QtWidgets.QLabel()
                arrow.setObjectName('viewStepArrow')
                arrow.setPixmap(dyeworks_icon('step', TEXT_DIM, size=STEP_ARROW_PX)
                                .pixmap(STEP_ARROW_PX, STEP_ARROW_PX))
                arrow.setFixedSize(STEP_ARROW_PX + 2 * STEP_ARROW_MARGIN, STEP_ARROW_PX)
                arrow.setAlignment(Qt.AlignCenter)
                step_layout.addWidget(arrow)
            button = QtWidgets.QPushButton(label)
            button.setObjectName(f'viewStep{label}')
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setToolTip(tooltip)
            button.setFixedHeight(VIEW_STEP_BUTTON_HEIGHT)
            button.clicked.connect(lambda *_, k=key: self.pick_view(k))
            group.addButton(button)
            step_layout.addWidget(button)
            self._view_buttons[key] = button
        layout.addWidget(self.step)

        # -- Compare, a modifier over the view, and its target
        self.split = QtWidgets.QFrame()
        self.split.setObjectName('compareSplit')
        self.split.setFixedHeight(VIEW_HEIGHT)
        split_layout = QtWidgets.QHBoxLayout(self.split)
        split_layout.setContentsMargins(0, 0, 0, 0)
        split_layout.setSpacing(0)
        self.compare_button = QtWidgets.QPushButton('对比')
        self.compare_button.setObjectName('compareButton')
        self.compare_button.setCheckable(True)
        self.compare_button.setCursor(Qt.PointingHandCursor)
        self.compare_button.setIcon(dyeworks_icon('cmp', TEXT_DIM))
        self.compare_button.setIconSize(QtCore.QSize(ICON_PX, ICON_PX))
        self.compare_button.setProperty('dyeworksIcon', 'cmp')
        self.compare_button.clicked.connect(lambda checked=False: self.set_compare(bool(checked)))
        # the page opens the target menu on a right-click of Compare too
        self.compare_button.setContextMenuPolicy(Qt.CustomContextMenu)
        self.compare_button.customContextMenuRequested.connect(
            lambda *_: self.open_target_menu())
        self.target_button = QtWidgets.QPushButton()
        self.target_button.setObjectName('compareTargetButton')
        self.target_button.setCursor(Qt.PointingHandCursor)
        self.target_button.setToolTip('Compare with')
        self.target_button.setIcon(dyeworks_icon('down', TEXT_DIM, size=11))
        self.target_button.setIconSize(QtCore.QSize(11, 11))
        # `.splitbtn button + button`: 8 either side of the caret. Qt pads an
        # icon-only button past its sheet, so the width is set.
        self.target_button.setFixedWidth(8 + CARET_PX + 8)
        self.target_button.clicked.connect(lambda *_: self.open_target_menu())
        # 2026-10-07; see design/DECISIONS.md#d-topbar-834
        self.compare_divider = QtWidgets.QFrame()
        self.compare_divider.setObjectName('compareDivider')
        self.compare_divider.setFixedSize(1, VIEW_HEIGHT - 2)
        for widget in (self.compare_button, self.compare_divider, self.target_button):
            if widget is not self.compare_divider:
                widget.setFixedHeight(VIEW_HEIGHT - 2)
            split_layout.addWidget(widget)
        layout.addWidget(self.split)
        self._menu = None
        self._refresh()

    # ------------------------------------------------------------ the view
    def view(self) -> str:
        return self._view

    def step_view(self, delta: int) -> None:
        """(Not step: that is the frame holding the view buttons.)
        Ctrl / Shift + Left / Right (QA 2026-10-07, NAT): the next view
        along the pipeline, Input > Negative > Print, stopping at the ends."""
        order = [key for key, _label, _tip in self._VIEWS]
        index = order.index(self._view) if self._view in order else len(order) - 1
        target = max(0, min(len(order) - 1, index + (1 if delta > 0 else -1)))
        if order[target] != self._view:
            self.pick_view(order[target])

    def pick_view(self, view: str) -> None:
        """What a click on Input, Negative or Print does. Compare stays on
        across Negative and Print; Input has nothing to compare."""
        old = self._view
        if view == old:
            self._refresh()          # a re-click keeps it pressed
            return
        if view == 'input':
            if self._compare or old == 'negative':
                self._compare = False
                self.compare_changed.emit('off')
            self._view = 'input'
            self._refresh()
            self.input_mode_changed.emit(True)
            return
        self._view = view
        self._refresh()
        if old == 'input':
            self.input_mode_changed.emit(False)
        self.compare_changed.emit(self.mode())

    # ------------------------------------------------------------- compare
    def _available(self) -> tuple[str, ...]:
        """The targets that exist -- and on the Negative view, not the
        negative itself."""
        missing = {name for name, available in (
            ('reference', self._reference_available),
            ('kept', self._kept_available)) if not available}
        if self._view == 'negative':
            missing.add('negative')
        return tuple(key for key, _label in COMPARE_TARGETS if key not in missing)

    def target(self) -> str:
        available = self._available()
        return self._target if self._target in available else available[0]

    def set_compare(self, on: bool) -> None:
        """Compare on or off, on the view in force. From Input it moves to
        Print first (Input has nothing to compare)."""
        on = bool(on)
        if on and self._view == 'input':
            self._view = 'print'
            self._compare = True
            self._refresh()
            self.input_mode_changed.emit(False)
        self._compare = on
        self._refresh()
        self.compare_changed.emit(self.mode())

    def pick_target(self, target: str) -> None:
        """A target from the menu: it becomes the one in force, and Compare
        comes on with it (`splitMode = ...; setCompare(true)`)."""
        if target not in self._available():
            return
        self._target = target
        self.set_compare(True)

    def cycle(self) -> None:
        """Ctrl+D: Compare on, or the next target if it already is (the app's
        "cycle split source"). Input has nothing to compare, as on the page."""
        if self._view == 'input':
            return
        if not self._compare:
            self.set_compare(True)
            return
        available = self._available()
        current = self.target()
        self._target = available[(available.index(current) + 1) % len(available)]
        self.set_compare(True)

    def open_target_menu(self) -> QtWidgets.QMenu | None:
        """`#cmpmenu`: the four targets, a check before the one in force."""
        if not self.target_button.isEnabled():
            return None
        from spektrafilm_gui.dyeworks_menu import DyeworksMenu

        menu = DyeworksMenu(self)
        menu.setObjectName('compareTargetMenu')
        available = self._available()
        current = self.target()
        for key, label in COMPARE_TARGETS:
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(key == current)
            action.setEnabled(key in available)
            action.setData(key)
            action.triggered.connect(lambda *_, k=key: self.pick_target(k))
        menu.addSeparator()
        note = menu.addAction('Ctrl+D picks the next one')
        note.setEnabled(False)
        self._menu = menu
        point = self.target_button.mapToGlobal(
            QtCore.QPoint(self.target_button.width(), self.target_button.height() + 8))
        menu.popup(QtCore.QPoint(point.x() - menu.sizeHint().width(), point.y()))
        return menu

    # ---------------------------------------- the controller's old contract
    def set_reference_available(self, available: bool) -> None:
        self._reference_available = bool(available)
        self._refresh()

    def set_kept_available(self, available: bool) -> None:
        """Held by NAME across the change, as the segment's was: a frame
        switch must not move a compare on the input to another target."""
        self._kept_available = bool(available)
        self._refresh()

    def set_mode(self, mode: str) -> None:
        """Reflect a compare mode the controller chose, without emitting."""
        mode = (mode or 'off').lower()
        if mode.startswith('negative:') and mode[len('negative:'):] in dict(COMPARE_TARGETS):
            self._view = 'negative'
            self._target = mode[len('negative:'):]
            self._compare = True
        elif mode in dict(COMPARE_TARGETS):
            self._target = mode
            self._compare = True
            self._view = 'print'
        elif mode == 'off':
            self._compare = False
            if self._view == 'negative':
                self._view = 'print'
        self._refresh()

    def mode(self) -> str:
        """The compare mode in force, as the controller names it."""
        if self._view == 'negative':
            return f'negative:{self.target()}' if self._compare else 'negative'
        if self._view == 'print' and self._compare:
            return self.target()
        return 'off'

    def is_split(self) -> bool:
        """Split framing (Compare) or the negative alone (Negative)? Both ask
        the controller for 'negative'; this tells it which one is meant.
        Print alone is not a split either."""
        return bool(self._compare and self._view != 'input')

    def set_input_active(self, active: bool) -> None:
        """Reflect input mode, silently -- what the flip's method did."""
        if active:
            self._view = 'input'
            self._compare = False
        elif self._view == 'input':
            self._view = 'print'
        self._refresh()

    def is_input_active(self) -> bool:
        return self._view == 'input'

    # ------------------------------------------------------------- painting
    def _refresh(self) -> None:
        for key, button in self._view_buttons.items():
            button.setChecked(key == self._view)
        for button in (self.compare_button, self.target_button):
            button.setEnabled(self._view != 'input')
        self.compare_button.setChecked(self._compare and self._view != 'input')
        if self._view == 'input':
            tip = 'Input is the source: there is nothing to compare it with'
        else:
            live = 'negative' if self._view == 'negative' else 'print'
            tip = (f'Compare the {live} with {_TARGET_WORDS[self.target()]} '
                   '(Ctrl+D picks the next one)')
        self.compare_button.setToolTip(tip)
        refresh_icon_tint(self.compare_button, active=self.compare_button.isChecked())


#: `.rate-fly button { width: 22px }`, `.rate-fly button svg { width: 14px }`
FLYOUT_GLYPH_PX = 14
#: `fly.style.top = r.bottom + 8`
FLYOUT_GAP = 8


def paint_star(button: QtWidgets.QAbstractButton, *, lit: bool,
               size: int = FLYOUT_GLYPH_PX) -> None:
    """Draw one star: the SPEC'S OWN glyph, hollow when unlit.

    `.rate-fly button svg { fill: none; stroke: currentColor }` with
    `.lit { color: var(--kept) }` and `.lit svg { fill: currentColor }`, and
    the rating filter says the same thing in its own words. The star used to
    be the text character U+2605, which is always solid, always the font's
    idea of a star, and cannot be hollow at all -- so an unrated frame and a
    one-star frame were told apart only by a shade of grey.

    Qt cannot recolour a rendered pixmap from the stylesheet, so the colour
    is applied HERE rather than through the `lit` property. The property is
    still set: it is what the sheet uses for the hover row and what the
    tests read.
    """
    button.setProperty('lit', 'true' if lit else 'false')
    button.setText('')
    button.setIcon(dyeworks_icon('star', STATE_KEPT if lit else TEXT_DIM,
                              size=size, filled=lit))
    button.setIconSize(QtCore.QSize(size, size))


#: how long the pointer may be off both widgets before the flyout closes
FLYOUT_CLOSE_DELAY_MS = 220


class HoverFlyout(QtWidgets.QFrame):
    """A small panel that OPENS ON HOVER under the button that owns it.

    2026-08-04; see design/DECISIONS.md#d-topbar-841

    Leaving either widget starts a short timer instead of closing at once.
    The 8px gap between button and flyout is a real leave, so without the
    grace the flyout dies on the way to itself.
    """

    def __init__(self, object_name: str,
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint)
        self.setObjectName(object_name)
        # one look for every flyout; the object names stay for findChild
        self.setProperty('role', 'flyout')
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._anchor: QtWidgets.QWidget | None = None
        self._close_timer = QtCore.QTimer(self)
        self._close_timer.setSingleShot(True)
        self._close_timer.setInterval(FLYOUT_CLOSE_DELAY_MS)
        self._close_timer.timeout.connect(self._close_if_away)

    # ------------------------------------------------------------ hover open
    def arm(self, anchor: QtWidgets.QWidget) -> None:
        """Open under `anchor` whenever the pointer is on it."""
        self._anchor = anchor
        anchor.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API name
        if watched is self._anchor:
            if event.type() == QtCore.QEvent.Enter:
                self.pop_under(watched)
            elif event.type() == QtCore.QEvent.Leave:
                self._close_timer.start()
        return False        # never consume: the button still gets its click

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt API name
        self._close_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API name
        self._close_timer.start()
        super().leaveEvent(event)

    def _close_if_away(self) -> None:
        if not self.pointer_inside():
            self.close()

    def pointer_inside(self) -> bool:
        """Is the pointer on the flyout or on the button that owns it?"""
        point = QtGui.QCursor.pos()
        if self.isVisible() and self.frameGeometry().contains(point):
            return True
        anchor = self._anchor
        if anchor is not None and anchor.isVisible():
            top_left = anchor.mapToGlobal(QtCore.QPoint(0, 0))
            if QtCore.QRect(top_left, anchor.size()).contains(point):
                return True
        return False

    # -------------------------------------------------------------- placement
    #: Where it opens: None is under the anchor; a callable returning 'left'
    #: or 'right' opens it BESIDE the anchor on that side (20.2: the zoom
    #: stops open beside the rail, on its free side).
    side_of: Callable[[], str] | None = None

    def pop_under(self, anchor: QtWidgets.QWidget) -> None:
        """Centred under the button, 8px down — `r.left + r.width/2 - 60` --
        or beside it, vertically centred, when `side_of` says so."""
        self.adjustSize()
        origin = anchor.mapToGlobal(QtCore.QPoint(0, 0))
        side = self.side_of() if callable(self.side_of) else None
        if side in ('left', 'right'):
            x = (origin.x() - FLYOUT_GAP - self.width() if side == 'left'
                 else origin.x() + anchor.width() + FLYOUT_GAP)
            point = QtCore.QPoint(x, origin.y() + (anchor.height() - self.height()) // 2)
        else:
            point = QtCore.QPoint(
                origin.x() + (anchor.width() - self.width()) // 2,
                origin.y() + anchor.height() + FLYOUT_GAP)
        # keep the flyout inside the anchor's screen (the page clamps too)
        screen = anchor.screen()
        if screen is not None:
            available = screen.availableGeometry()
            point.setX(max(available.left() + 8,
                           min(point.x(), available.right() - self.width() - 8)))
        opening = not self.isVisible()
        self.move(point)
        self.show()
        if opening:     # `.rate-fly.is-open { animation: surface-in 120ms }`
            motion.surface_in(self, motion.MENU_MS)


# 2026-08-04; see design/DECISIONS.md#d-topbar-835
ZOOM_STOPS: tuple[tuple[str, float], ...] = (
    ('1:2', 50.0), ('1:1', 100.0), ('2:1', 200.0), ('4:1', 400.0))
#: the 1:1 button's own label when no stop is in force
ZOOM_DEFAULT = '1:1'
#: How near a stop the view has to be to count as sitting on it, in percent.
#: `set_zoom_percent` divides by the fit scale and `set_zoom` clamps, so the
#: number that comes back is close but not equal; a wheel step is 15%, so
#: this is far too small to snap one of those by accident.
ZOOM_SNAP_TOLERANCE = 0.5
#: `.rate-fly button` is 22 tall; these carry text, so they are wider
ZOOM_STOP_SIZE = (34, 22)


class ZoomFlyout(HoverFlyout):
    """The zoom stops, in the `.rate-fly` language (NAT 2026-08-04).

    Fit is deliberately NOT here: it has the button immediately to the left,
    and a stop that duplicates a neighbouring button teaches the wrong
    thing about where to look.
    """

    picked = QtCore.Signal(float)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__('zoomFlyout', parent)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)
        self._percent: float | None = None
        self._stops: list[QtWidgets.QToolButton] = []
        for label, percent in ZOOM_STOPS:
            stop = QtWidgets.QToolButton()
            stop.setObjectName(f'zoomStop{label.replace(":", "")}')
            stop.setProperty('role', 'zoomStop')
            stop.setProperty('lit', 'false')
            stop.setText(label)
            stop.setFixedSize(*ZOOM_STOP_SIZE)
            stop.setCursor(Qt.PointingHandCursor)
            stop.setToolTip(_zoom_tooltip(label, percent))
            stop.clicked.connect(lambda *_, p=percent: self._pick(p))
            layout.addWidget(stop)
            self._stops.append(stop)

    def _pick(self, percent: float) -> None:
        self.set_percent(percent)
        self.picked.emit(percent)
        self.close()

    def percent(self) -> float | None:
        """The STOP in force, or None if the view is not sitting on one."""
        return self._percent

    def label(self) -> str:
        """The notation for the stop in force, or the default offer."""
        for text, percent in ZOOM_STOPS:
            if self._percent is not None and abs(percent - self._percent) < 1e-6:
                return text
        return ZOOM_DEFAULT

    def set_percent(self, percent: float | None) -> None:
        """Light the stop the view is on -- and NOTHING when it is between.

        A wheel zoom lands at 213%, which is not a stop; the flyout clears
        rather than keeping the last one lit, so neither it nor the button
        goes on claiming a zoom that is no longer in force. Snapped with a
        tolerance because `set_zoom_percent` round-trips through the fit
        scale and does not come back bit-exact.
        """
        self._percent = None
        if percent is not None:
            for _label, value in ZOOM_STOPS:
                if abs(value - float(percent)) <= ZOOM_SNAP_TOLERANCE:
                    self._percent = value
                    break
        for stop, (_label, value) in zip(self._stops, ZOOM_STOPS):
            lit = (self._percent is not None
                   and abs(value - self._percent) < 1e-6)
            stop.setProperty('lit', 'true' if lit else 'false')
            stop.style().unpolish(stop)
            stop.style().polish(stop)


def _zoom_tooltip(label: str, percent: float) -> str:
    if percent == 100.0:
        return '1:1: one screen pixel per image pixel'
    if percent < 100.0:
        return f'{label}: the image at {percent:g}%, half size'
    return f'{label}: the image at {percent:g}%'


class TopBar(QtWidgets.QFrame):
    """The 56px header. Built from callbacks; owns no state of its own."""

    def __init__(
        self,
        *,
        on_toggle_left_panel: Callable[[], None] | None = None,
        on_toggle_right_panel: Callable[[], None] | None = None,
        on_preview: Callable[[], None] | None = None,
        on_scan: Callable[[], None] | None = None,
        on_reset: Callable[[], None] | None = None,
        on_export: Callable[[], None] | None = None,
        on_lut: Callable[[], None] | None = None,
        on_compare: Callable[[str], None] | None = None,
        on_input_mode: Callable[[bool], None] | None = None,
        instruments: dict[str, QtWidgets.QWidget] | None = None,
    ) -> None:
        super().__init__()
        self.setObjectName('topBar')
        self.setFixedHeight(TOPBAR_HEIGHT)

        # THREE ZONES, the two side zones held to the SAME width, which is
        # what centres the view switch -- `grid-template-columns:
        # minmax(0,1fr) auto minmax(0,1fr)` on the page.
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(16)                        # `.topbar { gap: 16px }`

        self._left_zone = QtWidgets.QWidget()
        left_layout = QtWidgets.QHBoxLayout(self._left_zone)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)                    # `.tb-left { gap: 6px }`

        self._right_zone = QtWidgets.QWidget()
        right_layout = QtWidgets.QHBoxLayout(self._right_zone)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)                   # `.tb-right { gap: 8px }`

        # -- left: the panels and the shelf -------------------------------
        self._build_left_zone(on_toggle_left_panel, left_layout, instruments)

        # -- centre: what you see ------------------------------------------
        self._build_centre(on_compare, on_input_mode)

        # 2026-10-06, 2026-10-07; see design/DECISIONS.md#d-topbar-836
        self._build_actions(on_reset, on_preview, on_scan, on_export, on_lut, right_layout)

        self._build_panel_toggles(on_toggle_right_panel, right_layout)

        right_layout.insertStretch(0, 1)      # the right zone hugs the right edge
        layout.addWidget(self._left_zone, 0)
        layout.addStretch(1)
        layout.addWidget(self._centre, 0)
        layout.addStretch(1)
        layout.addWidget(self._right_zone, 0)
        self._balance_zones()

    def _build_left_zone(self, on_toggle_left_panel, left_layout, instruments):
        self.left_panel_button = _icon_button(
            'panel', 'Show or hide the library panel', on_toggle_left_panel,
            object_name='toggleLeftPanelButton', checkable=True)
        self.left_panel_button.setChecked(True)
        refresh_icon_tint(self.left_panel_button, active=True)
        left_layout.addWidget(self.left_panel_button)

        instruments = instruments or {}
        shelf = instruments.get('shelf')
        if shelf is not None:
            self._adopt(shelf, 'topbarShelfButton', glyph='shelf')
            left_layout.addWidget(shelf)
        left_layout.addStretch(1)

    def _build_centre(self, on_compare, on_input_mode):
        self._centre = QtWidgets.QWidget()
        self._centre.setObjectName('topbarCenter')
        centre_layout = QtWidgets.QHBoxLayout(self._centre)
        centre_layout.setContentsMargins(0, 0, 0, 0)
        centre_layout.setSpacing(0)
        self.view_switch = ViewSwitch()
        if on_compare is not None:
            self.view_switch.compare_changed.connect(on_compare)
        if on_input_mode is not None:
            self.view_switch.input_mode_changed.connect(on_input_mode)
        centre_layout.addWidget(self.view_switch)

    def _build_actions(self, on_reset, on_preview, on_scan, on_export, on_lut, right_layout):
        self.reset_button = _text_button('重置 (Reset)', 'ghost',
                                         "Reset this image's settings (Ctrl+Z undoes it)",
                                         on_reset, icon='undo',
                                         object_name='topbarResetButton')
        self.preview_button = _text_button(
            '预览 (Preview)', 'ghost', 'Render a preview of the current settings (F5)',
            on_preview, icon='eye', object_name='topbarPreviewButton')
        # R2: the bar's ONE primary
        self.scan_button = _text_button(
            '扫描渲染 (Scan)', 'accentAction', 'Render at full resolution (F6)',
            on_scan, icon='scan', object_name='topbarScanButton')
        # Export is a MENU (the report's control map): the image, through the
        # scanner settings, or a LUT of the look -- LUT is an output format.
        self.export_button = _text_button(
            '导出 (Export)', 'ghost', 'Export the image or a LUT of the look', None,
            icon='share', object_name='topbarExportButton')
        # the caret after the label: a push button holds one icon, so the
        # caret is a small glyph laid in its right padding
        self.export_caret = QtWidgets.QLabel(self.export_button)
        self.export_caret.setObjectName('exportCaret')
        self.export_caret.setPixmap(dyeworks_icon('down', TEXT_DIM, size=CARET_PX)
                                    .pixmap(CARET_PX, CARET_PX))
        self.export_caret.setFixedSize(CARET_PX, CARET_PX)
        self.export_caret.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.export_caret.setStyleSheet('background: transparent;')
        self.export_button.setStyleSheet(f'padding-right: {11 + CARET_PX + 6}px;')
        self.export_button.installEventFilter(self)
        self._on_export = on_export
        self._on_lut = on_lut
        self.export_button.clicked.connect(lambda *_: self.open_export_menu())
        self.export_menu = None
        for button in (self.reset_button, self.preview_button, self.scan_button,
                       self.export_button):
            button.setFixedHeight(VIEW_HEIGHT)
            right_layout.addWidget(button)

    def _build_panel_toggles(self, on_toggle_right_panel, right_layout):
        self.right_panel_button = _icon_button(
            'panel', 'Show or hide the edit panel', on_toggle_right_panel,
            object_name='toggleRightPanelButton', checkable=True)
        self.right_panel_button.setChecked(True)
        refresh_icon_tint(self.right_panel_button, active=True)
        # `.flipx`: the right toggle is the left one mirrored
        self.right_panel_button.setIcon(_mirrored(self.right_panel_button.icon()))
        self.right_panel_button.toggled.connect(
            lambda checked, b=self.right_panel_button: b.setIcon(_mirrored(
                dyeworks_icon('panel', TEXT_HI if checked else TEXT_DIM))))
        right_layout.addWidget(self.right_panel_button)

        self.left_panel_button.toggled.connect(
            lambda checked, b=self.left_panel_button: refresh_icon_tint(b, active=checked))

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API name
        if watched is self.export_button and event.type() == QtCore.QEvent.Resize:
            button = self.export_button
            self.export_caret.move(button.width() - 11 - CARET_PX,
                                   (button.height() - CARET_PX) // 2)
        return super().eventFilter(watched, event)

    # ------------------------------------------------------------- export
    def open_export_menu(self) -> QtWidgets.QMenu:
        """`#xmenu`: Export image... (Ctrl+E) or Export a LUT of this look..."""

        menu = DyeworksMenu(self)
        menu.setObjectName('exportMenu')
        # The key as TEXT, not a QAction shortcut: Ctrl+E is bound app-wide
        # (app.py), and a second binding would make it ambiguous.
        image = menu.addAction('Export image\u2026\tCtrl+E')
        lut = menu.addAction('Export a LUT of this look\u2026')
        if self._on_export is not None:
            image.triggered.connect(lambda *_: self._on_export())
        if self._on_lut is not None:
            lut.triggered.connect(lambda *_: self._on_lut())
        self.export_menu = menu
        button = self.export_button
        point = button.mapToGlobal(QtCore.QPoint(button.width(), button.height() + 8))
        menu.popup(QtCore.QPoint(point.x() - menu.sizeHint().width(), point.y()))
        return menu

    # ----------------------------------------------------------------- state
    def set_compare_enabled(self, enabled: bool) -> None:
        """Input mode has nothing to compare (kept for the controller's
        chrome pass; the switch also works it out from its own view)."""
        if not enabled:
            self.view_switch.set_input_active(True)

    # ------------------------------------------------------------------ util
    @staticmethod
    def _adopt(widget: QtWidgets.QWidget, object_name: str, *, glyph: str = '') -> None:
        """Re-home a widget built elsewhere without disturbing its wiring.

        The instrument buttons arrive as labelled QPushButtons ('loupe',
        'test strip', 'shelf'). The design shows them as icon buttons, so the
        label moves to the tooltip and the glyph takes its place -- the
        widget OBJECT is untouched, so its signals, its checked state and the
        controller's references to it all survive.
        """
        widget.setObjectName(object_name)
        widget.setFixedHeight(ICON_BUTTON_PX)
        if not glyph:
            return
        label = widget.text().strip() if hasattr(widget, 'text') else ''
        if label and not widget.toolTip():
            widget.setToolTip(label)
        if hasattr(widget, 'setIcon'):
            widget.setText('')
            widget.setIcon(dyeworks_icon(glyph, TEXT_DIM))
            widget.setIconSize(QtCore.QSize(ICON_PX, ICON_PX))
            widget.setFixedWidth(ICON_BUTTON_PX)
            widget.setProperty('dyeworksIcon', glyph)
            widget.setProperty('role', 'iconButton')
            if hasattr(widget, 'toggled'):
                widget.toggled.connect(
                    lambda checked, w=widget: refresh_icon_tint(w, active=checked))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API name
        super().resizeEvent(event)
        self.fold_labels(self.width())
        self._balance_zones()

    def fold_labels(self, width: int) -> None:
        """R12: a narrow window keeps every control and drops words.

        Under 1440px Reset, Preview and Export show their icons only
        (`padding: 0 9px`); under 1240px Compare does too. Scan, the one
        primary, keeps its word at every width, as on the page."""
        if width <= 0:
            return
        ghosts = width < FOLD_GHOST_LABELS_BELOW
        for button, label in ((self.reset_button, '重置'),
                              (self.preview_button, '预览'),
                              (self.export_button, '导出')):
            button.setText('' if ghosts else label)
            button.setProperty('folded', 'true' if ghosts else 'false')
        self.export_button.setStyleSheet(
            f'padding-left: 9px; padding-right: {9 + CARET_PX + 4}px;' if ghosts
            else f'padding-right: {11 + CARET_PX + 6}px;')
        for button in (self.reset_button, self.preview_button):
            button.setStyleSheet('padding: 0px 9px;' if ghosts else '')
        switch = self.view_switch
        switch.compare_button.setText(
            '' if width < FOLD_COMPARE_LABEL_BELOW else '对比')

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API name
        super().showEvent(event)
        self._balance_zones()

    #: Qt's "no maximum". `QWIDGETSIZE_MAX` is not exported to Python.
    _NO_MAX_WIDTH = 16777215

    def _balance_zones(self) -> None:
        """Give the two side zones the SAME width, which is what puts the
        navigation cluster in the middle of the WINDOW -- WHEN THERE IS ROOM.

        The design centres it absolutely (`.tb-center { left: 50% }`). Done
        literally in Qt it floated over the right-hand group, which is wider
        here than in the mockup. Centring it between two plain stretches does
        not work either: the zones' size hints differ, so an equal share of
        the leftover space still leaves them different widths -- measured
        ~90px off centre. Equal FIXED side widths plus equal stretches is
        exact, and the centre is a real layout item so nothing can overlap.

        2026-09-04; see design/DECISIONS.md#d-topbar-842

        So the balance is now a PREFERENCE, not a promise. When it does not
        fit, the zones are released to their own widths: the cluster drifts
        off true centre by half their difference, and every control stays on
        screen. A cluster a few pixels off centre is a cosmetic loss; a
        button you cannot reach is a functional one.
        """
        left_hint = self._left_zone.sizeHint().width()
        right_hint = self._right_zone.sizeHint().width()
        widest = max(left_hint, right_hint)
        available = self.width()
        # `available <= 0` is the pre-show pass, where the hints are all we
        # have and the balanced answer is the right one to start from.
        fits = available <= 0 or (2 * widest
                                  + self._centre.sizeHint().width()) <= available
        for zone, hint in ((self._left_zone, left_hint),
                           (self._right_zone, right_hint)):
            if fits:
                if zone.width() != widest or zone.minimumWidth() != widest:
                    zone.setFixedWidth(widest)
            elif zone.maximumWidth() != self._NO_MAX_WIDTH:
                # Release BOTH bounds: `setFixedWidth` pinned them together,
                # and leaving the minimum behind would keep the floor that
                # caused the clipping.
                zone.setMinimumWidth(0)
                zone.setMaximumWidth(self._NO_MAX_WIDTH)
