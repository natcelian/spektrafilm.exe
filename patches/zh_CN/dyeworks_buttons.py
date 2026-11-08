"""The Dyeworks buttons: the toggle switch, wide buttons and button pairs, the
attempts list, icon strips, segmented enums, preset buttons and the stage
tab bar.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from qtpy import QtCore, QtGui, QtWidgets

from spektrafilm_gui import motion, theme_palette as palette
from spektrafilm_gui.dyeworks_icons import dyeworks_icon, ICON_PX
from spektrafilm_gui.dyeworks_labels import Hint, _mono_font, _ui_font, mix
from spektrafilm_gui.theme_tokens import RADIUS_7, TYPE_CAPTION, TYPE_MICRO, TYPE_NOTE, TYPE_VALUE

Qt = QtCore.Qt


ICON_STRIP_HEIGHT = 34                      # .wb
WIDE_BUTTON_HEIGHT = 32                     # .wide
#: How narrow a `.wide` button may be squeezed before it stops giving way.
#: Two side by side plus the pair's 7px gap and the section's margins must
#: fit the 300px library panel, which is what sets this.
WIDE_BUTTON_MIN_WIDTH = 96
WIDE_HINT_GAP = 8                           # `Keep version` -> `· K`
TOGGLE_SIZE = (30, 17)                      # .tgl


# ==========================================================================
# controls
# ==========================================================================
class ToggleSwitch(QtWidgets.QAbstractButton):
    """`.tgl` — a 30x17 switch. PAINTED, because QSS cannot make a checkbox
    into a sliding switch and the design uses one everywhere a boolean
    appears.

    `.tgl { transition: background 140ms, border-color 140ms }` and
    `.tgl::after { transition: left 140ms, background 140ms }`: the checked
    state flips at once, the track tints and the knob slides after it
    (`motion.Tween`, motion pass 2026-09-28)."""

    #: paints and tweens itself: the stylesheet-fade filter leaves it alone
    MOTION_OWN = True

    def __init__(self, checked: bool = False) -> None:
        super().__init__()
        self.setObjectName('dyeworksToggle')
        self.setCheckable(True)
        self.setChecked(checked)
        self.knob = motion.Tween(self, 1.0 if self.isChecked() else 0.0,
                                 motion.HOVER_MS)
        self.toggled.connect(
            lambda on: self.knob.set_target(1.0 if on else 0.0))
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(*TOGGLE_SIZE)
        self.setFocusPolicy(Qt.StrongFocus)

    def checkStateSet(self) -> None:  # noqa: N802 - Qt API name
        """A PROGRAMMATIC `setChecked`: the knob jumps to it, unanimated.

        The knob used to move only on `toggled`, and every row that mirrors
        an editor syncs the switch with its signals BLOCKED (so the sync does
        not push back) -- which left the knob where the switch was built:
        checked, but painted off. Every boolean row that starts on read as
        off (QA 2026-09-29: "toggles appear off but are actually on"). Qt
        calls this on every `setChecked`, blocked or not -- a click too, so
        a click is let through to `toggled`, which animates it."""
        super().checkStateSet()
        knob = getattr(self, 'knob', None)         # __init__ sets the state first
        if knob is not None and not getattr(self, '_clicking', False):
            knob.set_target(1.0 if self.isChecked() else 0.0, animate=False)

    def nextCheckState(self) -> None:  # noqa: N802 - Qt API name
        self._clicking = True
        try:
            super().nextCheckState()
        finally:
            self._clicking = False

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API name
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        width, height = self.width(), self.height()
        radius = height / 2.0
        # 0 off .. 1 on; between the two only while the knob is travelling
        t = self.knob.value()
        enabled = self.isEnabled()

        if not enabled:
            track, border, knob = (QtGui.QColor(palette.DISABLED_BG),
                                   QtGui.QColor(palette.BORDER_LINE),
                                   QtGui.QColor(palette.DISABLED_TEXT))
        else:
            # .tgl[aria-pressed=true]: accent 30% over bg-2, border accent 55%
            track = motion.mix(palette.RAMP_CONTROL, mix(
                palette.ACCENT_COLOR_TEXT, palette.RAMP_CONTROL, 0.30), t)
            border = motion.mix(palette.BORDER_LINE_STRONG, mix(
                palette.ACCENT_COLOR_TEXT, palette.BORDER_LINE_STRONG, 0.55), t)
            knob = motion.mix(palette.TEXT_DIM, palette.TEXT_HI, t)

        painter.setPen(QtGui.QPen(border, 1))
        painter.setBrush(track)
        painter.drawRoundedRect(
            QtCore.QRectF(0.5, 0.5, width - 1, height - 1), radius, radius)

        diameter = height - 6
        x = 3 + (width - diameter - 6) * t
        painter.setPen(Qt.NoPen)
        painter.setBrush(knob)
        painter.drawEllipse(QtCore.QRectF(x, 3, diameter, diameter))

        if self.hasFocus() and self._keyboard_focus:
            painter.setPen(QtGui.QPen(QtGui.QColor(palette.ACCENT_COLOR_TEXT), 1.5))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(
                QtCore.QRectF(-1.5, -1.5, width + 3, height + 3),
                radius + 2, radius + 2)
        painter.end()

    # 2026-09-30; see design/DECISIONS.md#d-dyeworks_widgets-409
    _keyboard_focus = False

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt API name
        self._keyboard_focus = event.reason() in (
            Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason)
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt API name
        self._keyboard_focus = False
        super().focusOutEvent(event)
        self.update()

    def sizeHint(self) -> QtCore.QSize:  # noqa: N802 - Qt API name
        return QtCore.QSize(*TOGGLE_SIZE)


class WideButton(QtWidgets.QPushButton):
    """`.wide` — the panel's full-width action: 32px, bg-2, line-2, 7px."""

    def __init__(self, text: str = '', glyph: str = '') -> None:
        super().__init__(text)
        self.setObjectName('dyeworksWideButton')
        self.setProperty('role', 'wide')
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(WIDE_BUTTON_HEIGHT)
        # A push button defaults to a horizontal policy of `Minimum`, which
        # means "may grow, never shrink" -- so a layout takes its sizeHint as
        # the floor and `minimumSizeHint` below would be ignored. `Preferred`
        # is what lets the pair squeeze into a 300px panel.
        self.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                           self.sizePolicy().verticalPolicy())
        self.setFont(_ui_font(self, TYPE_NOTE))
        self._label = text
        self._shortcut_hint = ''
        self._name_label = None
        self._hint_label = None
        if glyph:
            self.setIcon(dyeworks_icon(glyph, palette.TEXT_HI))
            self.setIconSize(QtCore.QSize(ICON_PX, ICON_PX))

    def minimumSizeHint(self):  # noqa: N802 - Qt API name
        """Narrow enough that a PAIR of these fits a 300px panel.

        A push button's natural minimum is its whole label plus padding --
        'Reprocess raw' asks 191px, and two of them side by side asked 291,
        which with the section's margins put the Roll panel's content at 321
        inside a 300px viewport and clipped the right-hand button's edge.

        The height is untouched (`.wide` is 32px and that is the design). Only
        the width gives, and Qt elides the label into whatever room it gets.
        `text()` is deliberately NOT changed: the cheat sheet, the tooltips
        and the tests all read it as the button's NAME.
        """
        hint = super().minimumSizeHint()
        hint.setWidth(min(hint.width(), WIDE_BUTTON_MIN_WIDTH))
        return hint

    def setShortcutHint(self, key: str) -> None:  # noqa: N802 - Qt naming
        """`Keep version<span class="mono">K</span>` — the key, quietly (no
        middle dot since the 20.2 copy rules, R5).

        2026-08-04; see design/DECISIONS.md#d-dyeworks_widgets-469

        The button's own text is emptied so it draws none of its own, and
        `text()` goes on answering the button's NAME — the cheat sheet, the
        tooltips and the tests all read it, and none of them want a key
        glued on the end.
        """
        self._shortcut_hint = str(key or '')
        if not self._shortcut_hint:
            return
        if self._name_label is None:
            layout = QtWidgets.QHBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(WIDE_HINT_GAP)
            layout.addStretch(1)
            self._name_label = QtWidgets.QLabel(self._label)
            self._name_label.setObjectName('dyeworksWideButtonLabel')
            self._name_label.setFont(self.font())
            self._name_label.setAttribute(Qt.WA_TransparentForMouseEvents)
            self._hint_label = QtWidgets.QLabel()
            self._hint_label.setObjectName('dyeworksWideButtonHint')
            self._hint_label.setFont(_mono_font(TYPE_MICRO))
            self._hint_label.setAttribute(Qt.WA_TransparentForMouseEvents)
            layout.addWidget(self._name_label, 0)
            layout.addWidget(self._hint_label, 0)
            layout.addStretch(1)
            super().setText('')
        self._hint_label.setText(self._shortcut_hint)

    # -- text: the NAME, whatever the button happens to be drawing ---------
    def text(self) -> str:
        return self._label

    def setText(self, text: str) -> None:  # noqa: N802 - Qt API name
        self._label = str(text)
        if self._name_label is None:
            super().setText(self._label)
        else:
            self._name_label.setText(self._label)


class ButtonPair(QtWidgets.QWidget):
    """`.pair` — two wide buttons sharing a row at 7px gap."""

    def __init__(self, *buttons: QtWidgets.QWidget) -> None:
        super().__init__()
        self.setObjectName('dyeworksButtonPair')
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        for button in buttons:
            layout.addWidget(button, 1)


#: `.attempt { gap: 9px; padding: 7px 9px; border-radius: 6px }`
ATTEMPT_PADDING = (9, 7, 9, 7)
ATTEMPT_GAP = 9
#: `.attempt .dot { width: 7px; height: 7px }`
ATTEMPT_DOT_PX = 7
#: `.attempts { gap: 3px }`
ATTEMPT_ROW_GAP = 3


class AttemptRow(QtWidgets.QAbstractButton):
    """`.attempt` — one version: a state dot, its id, and its note.

    THREE COLUMNS, not a list item. The design says three things at once --
    which verdict (the dot's colour), which version (the id), and what was
    changed (the note, right-aligned and quieter) -- and a QListWidget row
    can only say one of them plus an icon.

    The verdicts are the film's own vocabulary, unchanged: cyan live, yellow
    kept, magenta rejected.
    """

    #: paints and tweens itself: the stylesheet-fade filter leaves it alone
    MOTION_OWN = True

    _VERDICTS = ('live', 'kept', 'rejected')

    def __init__(self, attempt_id: str, label: str, note: str,
                 verdict: str) -> None:
        super().__init__()
        self.setObjectName('dyeworksAttempt')
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.hover = motion.Tween(self, 0.0, motion.HOVER_MS)
        self.attempt_id = str(attempt_id)
        self._verdict = verdict if verdict in self._VERDICTS else 'live'
        self.setProperty('verdict', self._verdict)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(*ATTEMPT_PADDING)
        layout.setSpacing(ATTEMPT_GAP)

        self.dot = QtWidgets.QLabel()
        self.dot.setObjectName('dyeworksAttemptDot')
        self.dot.setFixedSize(ATTEMPT_DOT_PX, ATTEMPT_DOT_PX)
        self.dot.setProperty('verdict', self._verdict)
        self.dot.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(self.dot, 0)

        self.id_label = QtWidgets.QLabel(label)
        self.id_label.setObjectName('dyeworksAttemptId')
        self.id_label.setFont(_ui_font(self, TYPE_VALUE))
        self.id_label.setProperty('verdict', self._verdict)
        self.id_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(self.id_label, 1)

        self.note_label = QtWidgets.QLabel(note)
        self.note_label.setObjectName('dyeworksAttemptNote')
        self.note_label.setFont(_ui_font(self, TYPE_CAPTION))
        self.note_label.setProperty('verdict', self._verdict)
        self.note_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(self.note_label, 0)

    def verdict(self) -> str:
        return self._verdict

    def state_colour(self) -> str:
        """The verdict's colour: cyan live, yellow kept, magenta removed.
        Read through the palette module, so an accent retint reaches it."""
        return {'live': palette.STATE_LIVE, 'kept': palette.STATE_KEPT,
                'rejected': palette.STATE_REJECTED}[self._verdict]

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API name
        """The box, and the strike through a rejected id.

        `text-decoration-color: var(--rejected)` — the line is MAGENTA while
        the text it crosses is ink-4, and Qt's `strikeOut` can only draw in
        the text's own colour. So the row draws it, one pixel, over the id's
        own rect.
        """
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        rect = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.isChecked():
            # 2026-10-07; see design/DECISIONS.md#d-dyeworks_widgets-410
            state = self.state_colour()
            fill = QtGui.QColor(state)
            fill.setAlphaF(0.12)
            painter.setBrush(fill)
            border = QtGui.QColor(state)
            border.setAlphaF(0.42)
            painter.setPen(QtGui.QPen(border, 1))
        elif self.hover.value() > 0.0:
            # `.attempt { transition: background 140ms var(--ease) }`
            fill = QtGui.QColor(palette.CONTROL_BG)
            fill.setAlphaF(self.hover.value())
            painter.setBrush(fill)
            painter.setPen(Qt.NoPen)
        else:
            painter.setBrush(Qt.NoBrush)
            painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(rect, 5, 5)   # R6: 6 -> 5

        if self._verdict == 'rejected':
            box = self.id_label.geometry()
            width = min(self.id_label.fontMetrics().horizontalAdvance(
                self.id_label.text()), box.width())
            middle = box.center().y() + 1
            painter.setPen(QtGui.QPen(QtGui.QColor(palette.STATE_REJECTED), 1))
            painter.drawLine(box.left(), middle, box.left() + width, middle)
        painter.end()

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt API name
        super().enterEvent(event)
        self.hover.set_target(1.0)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API name
        super().leaveEvent(event)
        self.hover.set_target(0.0)


class AttemptList(QtWidgets.QWidget):
    """`.attempts` — the version rows, 3px apart, on no card of their own.

    2026-10-07; see design/DECISIONS.md#d-dyeworks_widgets-455
    """

    selected = QtCore.Signal(str)
    context_requested = QtCore.Signal(str, QtCore.QPoint)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName('dyeworksAttempts')
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(ATTEMPT_ROW_GAP)
        self._rows: list[AttemptRow] = []
        self._empty = Hint('暂无历史版本。点击“预览”、“扫描渲染”或“保留版本”即可自动保存。')
        layout.addWidget(self._empty)
        self.customContextMenuRequested.connect(self._on_context_menu)

    def rows(self) -> list[AttemptRow]:
        return list(self._rows)

    def current_id(self) -> str:
        for row in self._rows:
            if row.isChecked():
                return row.attempt_id
        return ''

    def _on_context_menu(self, point) -> None:
        for row in self._rows:
            if row.geometry().contains(point):
                self.context_requested.emit(row.attempt_id, point)
                return

    def set_entries(self, entries: Sequence[dict], current_id: str = '') -> None:
        """Rebuild from the controller's dicts: id, label, note, verdict.
        `current_id` is the version in use; with none, the newest (the first
        row) is, since a fresh capture is the look on screen."""
        layout = self.layout()
        for row in self._rows:
            layout.removeWidget(row)
            row.setParent(None)
            row.deleteLater()
        self._rows = []
        for entry in entries:
            row = AttemptRow(entry.get('id', ''), entry.get('label', ''),
                             entry.get('note', ''), entry.get('verdict', 'live'))
            row.clicked.connect(
                lambda *_, r=row: self._pick(r))
            layout.addWidget(row)
            self._rows.append(row)
        current = next((r for r in self._rows if r.attempt_id == current_id),
                       self._rows[0] if self._rows else None)
        for row in self._rows:
            row.setChecked(row is current)
        self._empty.setVisible(not self._rows)

    def _pick(self, row: AttemptRow) -> None:
        for other in self._rows:
            other.setChecked(other is row)
        self.selected.emit(row.attempt_id)


class ButtonRows(QtWidgets.QWidget):
    """Stacked `.pair` rows, 7px apart — the gap the page gives the SECOND
    pair (`style="margin-top:7px"`), not the body's 11.

    2026-08-03; see design/DECISIONS.md#d-dyeworks_widgets-456
    """

    #: `.pair + .pair { margin-top: 7px }`
    ROW_GAP = 7

    def __init__(self, *rows: QtWidgets.QWidget) -> None:
        super().__init__()
        self.setObjectName('dyeworksButtonRows')
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(self.ROW_GAP)
        for row in rows:
            layout.addWidget(row)


class IconStrip(QtWidgets.QWidget):
    """`.wbrow` / `.icrow` — an exclusive row of glyph buttons.

    The design uses this wherever a short enum is better SHOWN than listed:
    the white-balance presets, the metering modes. It replaces a combo box,
    so it exposes the same shape of API -- a current index and a signal.
    """

    changed = QtCore.Signal(int)
    #: (index, armed) for a STANDALONE member -- one that acts rather than
    #: selects, and so must not join the exclusive group.
    standalone_toggled = QtCore.Signal(int, bool)

    def __init__(self, entries: Sequence[tuple[str, str]], *,
                 height: int = ICON_STRIP_HEIGHT, gap: int = 5,
                 standalone: Iterable[int] = ()) -> None:
        """`standalone` names positions that sit IN the row but outside the
        exclusive group.

        The white-balance strip needs this: the design orders it as shot ·
        daylight · tungsten · eyedropper · custom, and the eyedropper is not
        a fourth preset -- it samples, then lands on Custom. Putting it in
        the group would make picking a preset look like it un-sampled, and
        moving it out of the row would break the design's order.
        """
        super().__init__()
        self.setObjectName('dyeworksIconStrip')
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(gap)
        self._buttons: list[QtWidgets.QToolButton] = []
        self._standalone = set(standalone)
        group = QtWidgets.QButtonGroup(self)
        group.setExclusive(True)
        for index, (glyph, tooltip) in enumerate(entries):
            button = QtWidgets.QToolButton()
            button.setObjectName(f'dyeworksIconStripItem{index}')
            button.setProperty('role', 'stripItem')
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setToolTip(tooltip)
            button.setMinimumHeight(height)
            button.setIcon(dyeworks_icon(glyph, palette.TEXT_DIM))
            button.setIconSize(QtCore.QSize(16, 16))
            button.setProperty('dyeworksIcon', glyph)
            button.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                 QtWidgets.QSizePolicy.Fixed)
            if index in self._standalone:
                button.toggled.connect(
                    lambda armed, i=index: self._on_standalone(i, armed))
            else:
                group.addButton(button)
                button.clicked.connect(lambda *_, i=index: self._select(i))
            layout.addWidget(button)
            self._buttons.append(button)
        first = self._first_selectable()
        if first is not None:
            self._buttons[first].setChecked(True)
            self._retint()

    def _first_selectable(self) -> int | None:
        for index in range(len(self._buttons)):
            if index not in self._standalone:
                return index
        return None

    def _on_standalone(self, index: int, armed: bool) -> None:
        self._retint()
        self.standalone_toggled.emit(index, bool(armed))

    def _select(self, index: int) -> None:
        # choosing a preset ends whatever a standalone member had armed
        for position in self._standalone:
            button = self._buttons[position]
            if button.isChecked():
                button.setChecked(False)
        self._retint()
        self.changed.emit(index)

    def _retint(self) -> None:
        # A rendered pixmap cannot be recoloured by QSS, so the two states
        # are two cached icons.
        for button in self._buttons:
            glyph = button.property('dyeworksIcon')
            if glyph:
                button.setIcon(dyeworks_icon(
                    str(glyph),
                    palette.TEXT_HI if button.isChecked() else palette.TEXT_DIM))

    def set_index(self, index: int) -> None:
        if 0 <= index < len(self._buttons) and index not in self._standalone:
            self._buttons[index].setChecked(True)
            self._retint()

    def index(self) -> int:
        """The selected member. Standalone members are never 'the selection'."""
        for index, button in enumerate(self._buttons):
            if index not in self._standalone and button.isChecked():
                return index
        return -1

    def set_standalone_armed(self, index: int, armed: bool) -> None:
        if index in self._standalone:
            self._buttons[index].setChecked(bool(armed))


class SegmentedEnum(QtWidgets.QWidget):
    """`.segrow` / `.pp` — a short enum as a row of equal buttons.

    The design uses this for two-to-five-choice enums, where a dropdown
    hides the alternatives behind a click. Values can be anything; the
    labels are what the design shows.
    """

    changed = QtCore.Signal(object)

    def __init__(self, choices: Sequence[tuple[object, str]], *,
                 gap: int = 4, columns: int = 0, linked: bool = False) -> None:
        """`linked=True` draws `.seg` instead of `.segrow`.

        2026-09-03; see design/DECISIONS.md#d-dyeworks_widgets-472

        The difference is a spacing of 0, a `segPosition` on each button
        for the corner radii, and the container carrying
        `role="linkedSegment"`; the styling lives in one place either way.
        """
        super().__init__()
        self.setObjectName('dyeworksSegmented')
        self._linked = linked
        if linked:
            self.setProperty('role', 'linkedSegment')
        # `grid-template-columns: repeat(n, 1fr)` — ONE line unless the page
        # says otherwise, which it does exactly once: ten aspect ratios wrap
        # at five. A grid rather than a box so both cases are the same code.
        layout = QtWidgets.QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0 if linked else gap)
        per_row = columns or max(1, len(tuple(choices)))
        self._buttons: list[QtWidgets.QPushButton] = []
        group = self._group = QtWidgets.QButtonGroup(self)
        group.setExclusive(True)
        ordered = tuple(choices)
        for position, (value, label) in enumerate(ordered):
            button = QtWidgets.QPushButton(label)
            if linked:
                # The corner radii and the shared hairline: QSS cannot round
                # a container's children, so each button says where it sits.
                button.setProperty(
                    'segPosition',
                    'first' if position == 0
                    else 'last' if position == len(ordered) - 1
                    else 'middle')
            else:
                button.setProperty('role', 'segmented')
            button.setProperty('segValue', value)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                 QtWidgets.QSizePolicy.Fixed)
            button.clicked.connect(lambda *_, v=value: self.changed.emit(v))
            group.addButton(button)
            index = len(self._buttons)
            layout.addWidget(button, index // per_row, index % per_row)
            self._buttons.append(button)
        for column in range(min(per_row, max(1, len(self._buttons)))):
            layout.setColumnStretch(column, 1)
        # The buttons ask for 31px and are vertically Fixed, yet in some
        # parents they are laid out at 13 and their labels vanish entirely.
        # I did not find what overrides the size policy; this pins the row
        # to what the buttons themselves ask for, which is the number the
        # page's `padding: 6px 0` produces anyway.
        if self._buttons:
            self.setMinimumHeight(
                max(b.sizeHint().height() for b in self._buttons)
                * (1 + (len(self._buttons) - 1) // per_row)
                + gap * ((len(self._buttons) - 1) // per_row))
        if self._buttons:
            self._buttons[0].setChecked(True)

    def fit_to_content(self) -> None:
        """Size the segments to their LABELS instead of sharing the width.

        2026-08-02, 2026-08-03, 2026-08-05; see design/DECISIONS.md#d-dyeworks_widgets-473
        """
        layout = self.layout()
        for column in range(layout.columnCount()):
            layout.setColumnStretch(column, 0)
        for button in self._buttons:
            button.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                                 QtWidgets.QSizePolicy.Fixed)

    def set_value(self, value) -> None:
        """Light the matching segment — or NONE of them, for a value the row
        does not offer.

        2026-08-05; see design/DECISIONS.md#d-dyeworks_widgets-474
        """
        self._group.setExclusive(False)
        for button in self._buttons:
            button.setChecked(button.property('segValue') == value)
        self._group.setExclusive(True)

    def value(self):
        for button in self._buttons:
            if button.isChecked():
                return button.property('segValue')
        return None


class PresetButtons(QtWidgets.QWidget):
    """A row of one-tap presets that write a single value.

    The design's desqueeze row: 1.00x / 1.33x / 1.5x / 2x rather than a bare
    slider, because anamorphic squeeze factors are a short list of real
    lenses, not a continuum.
    """

    def __init__(self, presets: Sequence[tuple[float, str]], editor) -> None:
        super().__init__()
        self.setObjectName('dyeworksPresetButtons')
        self._editor = editor
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self._buttons: list[QtWidgets.QPushButton] = []
        group = QtWidgets.QButtonGroup(self)
        group.setExclusive(True)
        for value, label in presets:
            button = QtWidgets.QPushButton(label)
            button.setProperty('role', 'segmented')
            button.setProperty('segValue', value)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                 QtWidgets.QSizePolicy.Fixed)
            button.clicked.connect(lambda *_, v=value: self._push(v))
            group.addButton(button)
            layout.addWidget(button, 1)
            self._buttons.append(button)
        editor.valueChanged.connect(lambda *_: self._pull())
        self._pull()

    def _push(self, value: float) -> None:
        self._editor.value = float(value)

    def _pull(self, *_args) -> None:
        current = float(self._editor.value)
        for button in self._buttons:
            button.setChecked(abs(float(button.property('segValue')) - current) < 1e-6)


class StageTabBar(QtWidgets.QTabBar):
    """`.stages` — the bg-0 well the stage buttons sit in.

    2026-08-02; see design/DECISIONS.md#d-dyeworks_widgets-462

    The design's DOM is `.stagewrap > .stages > button`, so the fill, border
    and radius belong to a container and only the pills belong to the
    buttons. Doing it in `paintEvent` keeps the whole QTabWidget API -- and
    every test written against it -- rather than swapping in a hand-rolled
    segmented control.
    """

    def _slot_widths(self) -> list[int]:
        """`.stages { grid-auto-columns: 1fr }` — the stages SHARE the panel
        width, one fifth each.

        2026-08-04; see design/DECISIONS.md#d-dyeworks_widgets-475

        The remainder is spread one pixel at a time rather than dropped, or
        the last pill comes up short of the well by up to four.
        """
        count = self.count()
        if count <= 0:
            return []
        parent = self.parentWidget()
        # THE PARENT'S FULL WIDTH, with nothing taken off for the bar's
        # padding or the tabs' margins. Measured: the bar ends up exactly as
        # wide as its tabs add up to, so anything subtracted here comes off
        # the RIGHT of the well and is the dead space over again -- 342 of
        # panel showed 309 of bar. The 3px inset the sheet asks for is inside
        # each pill's own rect, not between them and the bar.
        total = parent.width() if parent is not None else self.width()
        # the strip ends 26px short of the panel edge (page: 331 wide in 372); the
        # tab widget itself now runs to the edge so the scroll bar can
        from spektrafilm_gui.theme_styles import STAGE_STRIP_RIGHT_TRIM

        total -= STAGE_STRIP_RIGHT_TRIM
        if total <= 0:
            return []
        base, extra = divmod(total, count)
        return [base + (1 if index < extra else 0) for index in range(count)]

    def tabSizeHint(self, index):             # noqa: N802 - Qt API name
        hint = super().tabSizeHint(index)
        widths = self._slot_widths()
        if widths and 0 <= index < len(widths):
            # 2026-08-05; see design/DECISIONS.md#d-dyeworks_widgets-437
            hint.setWidth(widths[index])
        return hint

    def minimumSizeHint(self):                # noqa: N802 - Qt API name
        """The TEXT's width, not the expanded one.

        2026-08-04; see design/DECISIONS.md#d-dyeworks_widgets-476
        """
        hint = super().minimumSizeHint()
        if self.count():
            # the C++ implementation, deliberately: `self.tabSizeHint` is the
            # expanded one and would give the inflated answer again
            natural = sum(QtWidgets.QTabBar.tabSizeHint(self, index).width()
                          for index in range(self.count()))
            hint.setWidth(min(hint.width(), natural))
        return hint

    def resizeEvent(self, event) -> None:     # noqa: N802 - Qt API name
        super().resizeEvent(event)
        # the hints are a function of the PARENT's width, so they have to be
        # re-asked when it changes. Guarded, or the re-layout resizes the bar
        # and asks again.
        width = self.width()
        if width != getattr(self, '_laid_out_at', None):
            self._laid_out_at = width
            self.updateGeometry()

    def paintEvent(self, event) -> None:      # noqa: N802 - Qt API name
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        rect = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QtGui.QPainterPath()
        path.addRoundedRect(rect, RADIUS_7, RADIUS_7)   # `.stages`: 7 on the 20.2 scale (R6)
        painter.fillPath(path, QtGui.QColor(palette.WINDOW_BG))     # bg-0
        painter.setPen(QtGui.QPen(QtGui.QColor(palette.BORDER_LINE), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
        painter.end()
        super().paintEvent(event)             # then Qt draws the pills


#: `.segrow { gap: 4px }`, buttons `padding: 6px 0; border-radius: 5px`
SEG_GAP = 4


def segmented_for_combo(combo: QtWidgets.QComboBox,
                        short: dict[str, str] | None = None,
                        columns: int = 0,
                        order: Sequence[str] | None = None) -> 'SegmentedEnum':
    """`rowSegEnum` over a COMBO — the page's `{seg:1}` dispatch.

    2026-08-03; see design/DECISIONS.md#d-dyeworks_widgets-467

    The combo stays the model: it holds the slugs, the state stores them,
    profile sync and presets write to it. Only the picking changes, so
    nothing downstream learns anything.

    `short` is what the buttons READ -- 'RMS Prod Prev Synth' where the menu
    says 'RMS granularity', 'Production'... Four names side by side say the
    choice IS four things; a dropdown shows one and hides three.

    `order` is what order they sit IN, when the combo's is not it. The combo's
    order is the enum's, which is the order the options were added to the code
    -- fine for a dropdown nobody reads twice, not always right for four
    buttons on one line. Names not in `order` keep their place at the end, so
    an option added to the enum appears rather than vanishing.
    """
    # 2026-08-03; see design/DECISIONS.md#d-dyeworks_widgets-441
    short = short or {}
    items = [(index, combo.itemText(index)) for index in range(combo.count())]
    if order:
        rank = {name: position for position, name in enumerate(order)}
        # A name the order does not mention keeps its place AFTER the ones it
        # does, in the combo's own sequence -- an option added to the enum
        # shows up at the end rather than disappearing off the row.
        items.sort(key=lambda pair: (rank.get(pair[1], len(rank)), pair[0]))
    choices = [(index, short.get(full, full).replace(chr(38), chr(38) * 2))
               for index, full in items]
    row = SegmentedEnum(tuple(choices), gap=SEG_GAP, columns=columns)
    combo.hide()
    for button, (index, full) in zip(row._buttons, items):
        # The FULL name stays reachable: a short label saves space, it does
        # not rename anything, and 'Prev' vs 'Prod' needs the long form.
        button.setToolTip(full)
    row.changed.connect(combo.setCurrentIndex)
    combo.currentIndexChanged.connect(row.set_value)
    row.set_value(combo.currentIndex())
    return row
