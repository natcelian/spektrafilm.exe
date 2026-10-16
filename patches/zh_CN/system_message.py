"""`.sysmsg` and `.emptyroll` -- the viewer's own voice (20.2, port wave 6e).

2026-10-06; see design/DECISIONS.md#d-system_message-804

`.emptyroll`: the app used to draw a blank sheet of paper and no words when
there was nothing loaded. The stage now says what it is and what to do, in
the app's own terms, with the two ways in.

Both float over the stage (`StageArea.add_hud`) and carry LOCAL sheets, for
the reason the frame bar and the rail do: the viewer panel's own sheet beats
the application's.
"""

from __future__ import annotations

from typing import Callable

from qtpy import QtCore, QtGui, QtWidgets

from spektrafilm_gui import theme_palette
from spektrafilm_gui.theme_palette import (
    BASE_BG,
    BORDER_LINE,
    BORDER_LINE_STRONG,
    CARD_BG,
    CONTROL_BG,
    CONTROL_BG_HOVER,
    STATE_REJECTED,
    TEXT_BRIGHT,
    TEXT_DIM,
    TEXT_PRIMARY,
    WARNING_TEXT,
)
from spektrafilm_gui.theme_tokens import RADIUS_7, TYPE_CAPTION, TYPE_NOTE
from spektrafilm_gui.theme_fonts import mono_font

Qt = QtCore.Qt

#: `.sysmsg { top: 12px; width: min(760px, calc(100% - 152px)) }`
CARD_TOP = 12
CARD_MAX_WIDTH = 760
CARD_SIDE_ROOM = 152
#: `padding: 12px 14px 12px 20px; gap: 18px`
CARD_PADDING = (20, 12, 14, 12)
CARD_GAP = 18
#: `.sysmsg::before { left: 6px; top: 10px; bottom: 10px; width: 3px }`
EDGE_X, EDGE_INSET, EDGE_WIDTH = 6, 10, 3
#: a success says its piece and goes (`sysmsgTimer = setTimeout(hide, 7000)`)
AUTO_HIDE_MS = 7000
#: `.sysmsg .sm-t { font-size: 14px; font-weight: 600 }`
TITLE_PX = 14
#: `.sysmsg .sm-acts button { height: 30px }`
ACTION_HEIGHT = 30

KINDS = ('pos', 'warn', 'neg')


def kind_colour(kind: str) -> str:
    """The leading edge's colour. A success is the ACCENT, read through the
    palette module at paint time so a retint reaches it."""
    return {'pos': theme_palette.ACCENT_COLOR_TEXT, 'warn': WARNING_TEXT,
            'neg': STATE_REJECTED}[kind]


def system_message_sheet() -> str:
    """`.sysmsg` and its buttons. The first button is the likely way out:
    an ink-2 hairline at rest, filled ink on hover (`button:first-child`)."""
    return (
        f'QFrame#systemMessage {{ background: {CARD_BG}; border: 1px solid {BORDER_LINE_STRONG};'
        f' border-radius: {RADIUS_7}px; }}'
        'QFrame#systemMessage QWidget { background: transparent; }'
        f'QLabel#systemMessageTitle {{ color: {TEXT_BRIGHT}; font-size: {TITLE_PX}px;'
        ' font-weight: 600; }'
        f'QLabel#systemMessageBody {{ color: {TEXT_PRIMARY}; font-size: {int(TYPE_NOTE)}px; }}'
        f'QLabel#systemMessageDetail {{ color: {TEXT_DIM}; font-size: {int(TYPE_CAPTION)}px; }}'
        'QFrame#systemMessage QPushButton {'
        f' background: {CARD_BG}; color: {TEXT_BRIGHT}; border: 1px solid {BORDER_LINE_STRONG};'
        f' border-radius: {RADIUS_7}px; padding: 0px 13px; font-size: {int(TYPE_NOTE)}px;'
        f' min-height: {ACTION_HEIGHT - 2}px; max-height: {ACTION_HEIGHT - 2}px; }}'
        f'QFrame#systemMessage QPushButton:hover {{ background: {CONTROL_BG}; }}'
        f'QFrame#systemMessage QPushButton[first="true"] {{ border-color: {TEXT_PRIMARY}; }}'
        'QFrame#systemMessage QPushButton[first="true"]:hover {'
        f' background: {TEXT_BRIGHT}; color: {BASE_BG}; }}'
        f'QFrame#systemMessageGauge {{ background: {CONTROL_BG_HOVER}; border: none;'
        ' border-radius: 2px; }'
    )


class SystemMessage(QtWidgets.QFrame):
    """The card. One at a time: a new message replaces the one showing."""

    dismissed = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName('systemMessage')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(system_message_sheet())
        self.setAccessibleName('System message')
        self._kind = 'pos'
        self._gauge_fraction: float | None = None

        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(*CARD_PADDING)
        row.setSpacing(CARD_GAP)

        text = QtWidgets.QWidget()
        column = QtWidgets.QVBoxLayout(text)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(3)
        self.title = QtWidgets.QLabel()
        self.title.setObjectName('systemMessageTitle')
        self.title.setWordWrap(True)
        self.body = QtWidgets.QLabel()
        self.body.setObjectName('systemMessageBody')
        self.body.setWordWrap(True)
        # R4: an exception's text is CODE -- the bundled mono floor, and
        # selectable so it can be copied into a report
        self.detail = QtWidgets.QLabel()
        self.detail.setObjectName('systemMessageDetail')
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.detail.setFont(mono_font(int(TYPE_CAPTION)))
        # `.sm-gauge`: what you have, in the live colour, on what it needs
        self.gauge = QtWidgets.QFrame()
        self.gauge.setObjectName('systemMessageGauge')
        self.gauge.setFixedHeight(4)
        self.gauge.setMaximumWidth(380)
        self.gauge.installEventFilter(self)
        for widget in (self.title, self.body, self.detail, self.gauge):
            column.addWidget(widget)
        row.addWidget(text, 1)

        self.actions_host = QtWidgets.QWidget()
        self._actions = QtWidgets.QHBoxLayout(self.actions_host)
        self._actions.setContentsMargins(0, 0, 0, 0)
        self._actions.setSpacing(8)
        row.addWidget(self.actions_host, 0, Qt.AlignVCenter)

        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        self.hide()

    # ---------------------------------------------------------------- show
    def show_message(self, kind: str, title: str, body: str = '', *,
                     actions: list[tuple[str, Callable[[], None]]] | None = None,
                     close_label: str = 'Dismiss', detail: str = '',
                     gauge: float | None = None, auto: bool | None = None) -> None:
        """`showSysmsg`. `kind` is 'pos', 'warn' or 'neg'; `auto` defaults to
        True for a success only."""
        self._kind = kind if kind in KINDS else 'warn'
        self.title.setText(title)
        self.body.setText(body)
        self.body.setVisible(bool(body))
        self.detail.setText(detail)
        self.detail.setVisible(bool(detail))
        self._gauge_fraction = None if gauge is None else max(0.0, min(1.0, float(gauge)))
        self.gauge.setVisible(self._gauge_fraction is not None)
        while self._actions.count():
            item = self._actions.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        entries = list(actions or []) + [(close_label, None)]
        for index, (label, callback) in enumerate(entries):
            button = QtWidgets.QPushButton(label)
            button.setCursor(Qt.PointingHandCursor)
            button.setProperty('first', 'true' if index == 0 else 'false')
            button.clicked.connect(lambda *_, cb=callback: self._choose(cb))
            self._actions.addWidget(button)
        self._timer.stop()
        if auto if auto is not None else self._kind == 'pos':
            self._timer.start(AUTO_HIDE_MS)
        self.place()
        self.show()
        self.raise_()
        self.update()

    def kind(self) -> str:
        return self._kind

    def action_labels(self) -> list[str]:
        return [self._actions.itemAt(i).widget().text() for i in range(self._actions.count())
                if self._actions.itemAt(i).widget() is not None]

    def _choose(self, callback) -> None:
        self.dismiss()
        if callback is not None:
            callback()

    def dismiss(self) -> None:
        self._timer.stop()
        if self.isVisible():
            self.hide()
            self.dismissed.emit()

    # --------------------------------------------------------------- place
    def place(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        width = max(280, min(CARD_MAX_WIDTH, parent.width() - CARD_SIDE_ROOM))
        self.setFixedWidth(width)
        self.adjustSize()
        self.move((parent.width() - width) // 2, CARD_TOP)

    def attach(self, stage: QtWidgets.QWidget) -> None:
        add_hud = getattr(stage, 'add_hud', None)
        if callable(add_hud):
            add_hud(self)
        else:
            self.setParent(stage)
        self.hide()
        stage.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API name
        if watched is self.parentWidget() and event.type() == QtCore.QEvent.Resize:
            if self.isVisible():
                self.place()
        elif watched is self.gauge and event.type() == QtCore.QEvent.Paint:
            self._paint_gauge()
            return True
        return False

    # --------------------------------------------------------------- paint
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API name
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QtGui.QColor(kind_colour(self._kind)))
        rect = QtCore.QRectF(EDGE_X, EDGE_INSET, EDGE_WIDTH,
                             max(1, self.height() - 2 * EDGE_INSET))
        painter.drawRoundedRect(rect, EDGE_WIDTH / 2.0, EDGE_WIDTH / 2.0)
        painter.end()

    def _paint_gauge(self) -> None:
        painter = QtGui.QPainter(self.gauge)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        rect = QtCore.QRectF(self.gauge.rect())
        painter.setBrush(QtGui.QColor(CONTROL_BG_HOVER))
        painter.drawRoundedRect(rect, 2, 2)
        if self._gauge_fraction:
            painter.setBrush(QtGui.QColor(theme_palette.ACCENT_COLOR_TEXT))
            painter.drawRoundedRect(QtCore.QRectF(0, 0, rect.width() * self._gauge_fraction,
                                                  rect.height()), 2, 2)
        painter.end()


def empty_roll_sheet() -> str:
    return (
        'QWidget#emptyRoll { background: transparent; }'
        f'QLabel#emptyRollTitle {{ color: {TEXT_BRIGHT}; font-size: 15px; font-weight: 600; }}'
        f'QLabel#emptyRollText {{ color: {TEXT_DIM}; font-size: {int(TYPE_NOTE)}px; }}'
        'QWidget#emptyRoll QPushButton {'
        f' background: {CARD_BG}; color: {TEXT_PRIMARY}; border: 1px solid {BORDER_LINE};'
        f' border-radius: {RADIUS_7}px; min-height: 30px; max-height: 30px; padding: 0px 12px; }}'
        f'QWidget#emptyRoll QPushButton:hover {{ background: {CONTROL_BG}; color: {TEXT_BRIGHT}; }}'
        'QWidget#emptyRoll QPushButton[strong="true"] {'
        f' border-color: {TEXT_PRIMARY}; color: {TEXT_BRIGHT}; font-weight: 600; }}'
    )


class EmptyRollState(QtWidgets.QWidget):
    """`.emptyroll`: 'The roll is empty', what to add, and the two ways in."""

    #: `.emptyroll { max-width: 380px }`, `.er-acts { width: 290px }`
    MAX_WIDTH = 380
    ACTIONS_WIDTH = 290

    def __init__(self, *, on_add: Callable[[], None] | None = None,
                 on_open: Callable[[], None] | None = None,
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName('emptyRoll')
        self.setStyleSheet(empty_roll_sheet())
        column = QtWidgets.QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)
        self.title = QtWidgets.QLabel('胶卷当前为空')
        self.title.setObjectName('emptyRollTitle')
        self.title.setAlignment(Qt.AlignHCenter)
        self.text = QtWidgets.QLabel('添加 RAW 或 RGB 照片（单张或多张），或打开已保存的胶卷。')
        self.text.setObjectName('emptyRollText')
        self.text.setAlignment(Qt.AlignHCenter)
        self.text.setWordWrap(True)
        column.addWidget(self.title)
        column.addWidget(self.text)
        column.addSpacing(12)
        actions = QtWidgets.QWidget()
        actions.setFixedWidth(self.ACTIONS_WIDTH)
        row = QtWidgets.QHBoxLayout(actions)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.add_button = QtWidgets.QPushButton('添加底片/照片…')
        self.add_button.setObjectName('emptyRollAdd')
        self.add_button.setProperty('strong', 'true')
        self.open_button = QtWidgets.QPushButton('打开胶卷…')
        self.open_button.setObjectName('emptyRollOpen')
        for button, callback in ((self.add_button, on_add), (self.open_button, on_open)):
            button.setCursor(Qt.PointingHandCursor)
            if callback is not None:
                button.clicked.connect(lambda *_, cb=callback: cb())
            row.addWidget(button, 1)
        column.addWidget(actions, 0, Qt.AlignHCenter)
        self.setMaximumWidth(self.MAX_WIDTH)
        self.hide()

    def attach(self, stage: QtWidgets.QWidget) -> None:
        self._stage = stage
        add_hud = getattr(stage, 'add_hud', None)
        if callable(add_hud):
            add_hud(self)
        else:
            self.setParent(stage)
        self.hide()
        stage.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API name
        if watched is self.parentWidget() and event.type() == QtCore.QEvent.Resize:
            self.place()
        return False

    def place(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.setFixedWidth(min(self.MAX_WIDTH, max(200, parent.width() - 40)))
        self.adjustSize()
        self.move((parent.width() - self.width()) // 2, (parent.height() - self.height()) // 2)

    def set_empty(self, empty: bool) -> None:
        """Show the state and hide the paper mount (`.is-empty-roll .mount
        { display: none }`), or the other way round."""
        stage = self.parentWidget()
        mount = getattr(stage, 'mount', None)
        if mount is not None:
            mount.setVisible(not empty)
        if empty:
            self.place()
            self.show()
            self.raise_()
        else:
            self.hide()
