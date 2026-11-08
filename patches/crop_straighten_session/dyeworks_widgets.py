"""The design's component primitives, as real widgets (Phase 18F).

2026-10-07; see design/DECISIONS.md#d-dyeworks_widgets-453

WHAT QT CANNOT DO, and what is done instead:
  - `letter-spacing` / `text-transform` are not QSS properties, but they ARE
    QFont settings (`setLetterSpacing`, `setCapitalization`). Used here.
  - `box-shadow` has no QSS equivalent; painted where it matters.
  - `transition` has no QSS equivalent. Hover/press states switch instantly;
    a QVariantAnimation can be added per widget if a fade is wanted.
  - `color-mix(in oklch, ...)` is resolved in Python and emitted as hex.

Every widget here is presentation only. None of them own application state:
they take values and emit signals, so the existing sections, the state
bridge and the controller are unaffected by construction.
"""

from __future__ import annotations

from qtpy import QtCore, QtGui, QtWidgets

from spektrafilm_gui import theme_palette as palette
from spektrafilm_gui.dyeworks_icons import RESET_ICON_PX, reset_icon
from spektrafilm_gui.theme_tokens import TYPE_GROUP_LABEL, TYPE_NOTE
from spektrafilm_gui.crop_frame import parse_ratio
from spektrafilm_gui.widget_slider import _ResetButton
from spektrafilm_gui.dyeworks_chips import ChipSelect
from spektrafilm_gui.dyeworks_editors import HueSatRow
from spektrafilm_gui.dyeworks_labels import (COMPACT_BODY_PADDING, COMPACT_HEADER_PADDING,
                                             GROUP_BODY_GAP, GROUP_BODY_PADDING, GROUP_COUNT_GAP,
                                             GROUP_HEADER_PADDING, GROUP_META_CHANGED_PX,
                                             GROUP_META_RIGHT_PX, GROUP_TWISTY_PX, LabelledRow,
                                             ROW_RESET_PX, SECTION_BODY_GAP, SECTION_BODY_PADDING,
                                             SECTION_HEADER_PADDING, ShrinkingLabel,
                                             _SectionHeader, _mono_font, _ui_font)

Qt = QtCore.Qt


class RatioSelect(ChipSelect):
    """`rowRatio` -- the crop's aspect ratio as the page's big `.select`, not a labelled chip.

    2026-09-30; see design/DECISIONS.md#d-dyeworks_widgets-458
    """

    _BOX = 28           # `ratioIcon(r, box=28)`: a 28px cell, 22 tall

    def __init__(self, combo: QtWidgets.QComboBox, *,
                 trailing: QtWidgets.QWidget | None = None,
                 portrait=None) -> None:
        # `portrait`: () -> bool, the crop's orientation, so the swatch and
        # caption show 3:2 as 0.67, portrait when the frame is turned
        self._portrait = portrait
        super().__init__(combo, caption='', chip='ratio')
        self.swatch.setFixedSize(self._BOX, 22)
        if trailing is not None:
            # the crop's landscape / portrait pair, at the row's right end
            self.layout().addSpacing(8)
            self.layout().addWidget(trailing, 0, Qt.AlignVCenter)
        self._paint_swatch()
        self._sync()

    def _ratio(self):
        from spektrafilm_gui.crop_frame import oriented_ratio

        portrait = getattr(self, '_portrait', None)
        return oriented_ratio(parse_ratio(self._combo.currentText()),
                              bool(portrait()) if callable(portrait) else False)

    def refresh(self) -> None:
        """Show the combo and orientation again (set without a signal)."""
        self._sync()

    def _paint_swatch(self) -> None:
        if getattr(self, '_chip', None) != 'ratio':
            super()._paint_swatch()
            return
        ratio = self._ratio()
        scale = 2.0
        image = QtGui.QPixmap(int(self._BOX * scale), int(22 * scale))
        image.setDevicePixelRatio(scale)
        image.fill(Qt.transparent)
        painter = QtGui.QPainter(image)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        if not ratio:
            # `border: 1px dashed var(--line-2)`, 18 x 12, radius 1.5
            pen = QtGui.QPen(QtGui.QColor(palette.BORDER_LINE_STRONG), 1)
            pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(QtCore.QRectF((self._BOX - 18) / 2 + 0.5, (22 - 12) / 2 + 0.5,
                                                  17, 11), 1.5, 1.5)
        else:
            inner = self._BOX - 4
            land = ratio >= 1
            width = inner if land else inner * ratio
            height = min(inner / ratio if land else inner, 22)
            rect = QtCore.QRectF((self._BOX - width) / 2, (22 - height) / 2, width, height)
            # `oklch(0.55 0.02 60 / .5)` with an inset 1px `oklch(1 0 0 / .5)`
            painter.setPen(Qt.NoPen)
            painter.setBrush(QtGui.QColor(140, 131, 122, 128))
            painter.drawRoundedRect(rect, 1.5, 1.5)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 128), 1))
            painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 1.5, 1.5)
        painter.end()
        self.swatch.setPixmap(image)

    def _sync(self) -> None:
        text = self._combo.currentText()
        ratio = self._ratio()
        self.name.setText(text[:1].upper() + text[1:])
        if text.strip().lower() == 'original':
            self.sub.setText("the image's own aspect")
        elif not ratio:
            self.sub.setText('the frame as scanned')
        else:
            self.sub.setText('%.2f, %s' % (ratio, 'landscape' if ratio >= 1 else 'portrait'))
        self._paint_swatch()
        self.setToolTip(self._combo.toolTip())


DYE_COLUMNS = (88, 52)
CARD_COLUMNS = (129, 52)


class _ChipHug(QtCore.QObject):
    """Keeps a combo as wide as WHAT IT SAYS (see `hug_chip`)."""

    _EVENTS = (QtCore.QEvent.FontChange, QtCore.QEvent.Polish, QtCore.QEvent.Show,
               QtCore.QEvent.StyleChange)

    def __init__(self, combo: QtWidgets.QComboBox) -> None:
        super().__init__(combo)
        self._combo = combo
        combo.currentIndexChanged.connect(self.fit)
        combo.installEventFilter(self)

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API name
        if event.type() in self._EVENTS:
            # after the event: the sheet's font and padding have not landed at Polish
            QtCore.QTimer.singleShot(0, self._later)
        return False

    def _later(self) -> None:
        try:
            self.fit()
        except RuntimeError:            # the combo was deleted before the timer fired
            pass

    def fit(self, *_args) -> None:
        combo = self._combo
        if not combo.count():
            return
        combo.ensurePolished()
        metrics = combo.fontMetrics()
        widest = max(metrics.horizontalAdvance(combo.itemText(i)) for i in range(combo.count()))
        current = metrics.horizontalAdvance(combo.currentText())
        # Qt's own hint keeps the frame, the sheet's padding and the caret gutter; only its
        # text contribution is swapped (the same rule as `ChipComboBox.sizeHint`)
        width = max(1, QtWidgets.QComboBox.sizeHint(combo).width() - widest + current)
        if combo.width() != width or combo.minimumWidth() != width:
            combo.setFixedWidth(int(width))
        # the dropdown stays as wide as its longest item, whatever the chip is showing
        view = combo.view()
        if view is not None:
            view.setMinimumWidth(int(max(width, QtWidgets.QComboBox.sizeHint(combo).width())))


def hug_chip(combo: QtWidgets.QComboBox) -> QtWidgets.QComboBox:
    """`.enum .pick`: the chip is as wide as the value it shows, not stretched across the row.

    2026-09-30; see design/DECISIONS.md#d-dyeworks_widgets-465
    """
    if getattr(combo, '_chip_hug', None) is None:
        combo._chip_hug = _ChipHug(combo)
        combo._chip_hug.fit()
    return combo


#: a mini slider's sub-label is indented under its header (`padding-left: 12px`)
SUB_LABEL_INDENT = 12


class DyeworksSection(QtWidgets.QWidget):
    """`.sect` — a collapsible block: header row, body, hairline below.

    The header carries a right-aligned META summary in the value face, which
    is how the design lets a collapsed section still tell you what is in it.
    """

    toggled = QtCore.Signal(bool)

    def __init__(self, title: str, *, meta: str = '', expanded: bool = True,
                 variant: str = 'section', rule: str = 'below',
                 compact: bool = False) -> None:
        """`variant` picks WHICH of the page's two container languages.

        2026-09-29; see design/DECISIONS.md#d-dyeworks_widgets-477

        2026-08-02; see design/DECISIONS.md#d-dyeworks_widgets-478
        """
        super().__init__()
        self.setObjectName('dyeworksSection')
        self._expanded = expanded
        self._variant = variant
        head_padding = (GROUP_HEADER_PADDING if variant == 'group'
                        else SECTION_HEADER_PADDING)
        body_padding = (GROUP_BODY_PADDING if variant == 'group'
                        else SECTION_BODY_PADDING)
        body_gap = GROUP_BODY_GAP if variant == 'group' else SECTION_BODY_GAP
        if compact and variant != 'group':
            head_padding = COMPACT_HEADER_PADDING
            body_padding = COMPACT_BODY_PADDING

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # A FRAME, not a QToolButton: a QToolButton paints its own content
        # and does not render child widgets, so a header laid out inside one
        # comes out blank. The click is handled here instead.
        header_layout = self._build_header_frame(head_padding, variant)

        self._build_title(expanded, title, variant, header_layout)

        # The META gives way, not the title. A header's minimum is twisty +
        # title + summary, and the summary is the long one ('Kodak Gold 200',
        # '9 of 9 chains - sized by expose'). It is also the DERIVED text: the
        # title says which section this is and must stay readable, while the
        # summary repeats what the rows below already show. 279px of header in
        # a 300px panel was what clipped the Roll page.
        self._build_meta(meta, variant, header_layout)

        outer.addWidget(self.header)

        self._build_body(body_padding, body_gap, outer)

        # WHERE THE HAIRLINE GOES. `.sect` draws it below; `.grp` draws it
        # ABOVE (`border-top`, suppressed on the first of a run). In a STACK
        # those look identical, which is why one rule served both -- but a
        # lone group inside a section, like White balance inside File, then
        # gets its line under the last hint instead of over its own header.
        self._build_rule(rule, outer)

        self.body.setVisible(expanded)
        self.header.clicked.connect(lambda: self.set_expanded(not self._expanded))

        # A NAMELESS section has no header. It was still drawing the bar --
        # padding, caret and all -- with nothing in it, which is furniture
        # around an empty label. The closing-note groups are nameless too,
        # and their rule comes from the section ABOVE, not from this bar.
        if not title:
            self.header.hide()

    def _build_header_frame(self, head_padding, variant):
        self.header = _SectionHeader(self)
        self.header.setObjectName('dyeworksSectionHeader')
        self.header.setCursor(Qt.PointingHandCursor)
        self.header.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                  QtWidgets.QSizePolicy.Fixed)
        header_layout = QtWidgets.QHBoxLayout(self.header)
        header_layout.setContentsMargins(*head_padding)
        # 2026-09-29; see design/DECISIONS.md#d-dyeworks_widgets-448
        header_layout.setSpacing(3 if variant == 'group' else 4)
        return header_layout

    def _build_title(self, expanded, title, variant, header_layout):
        self.twisty = QtWidgets.QLabel('▾' if expanded else '▸')
        self.twisty.setObjectName('dyeworksSectionTwisty')
        self.twisty.setAttribute(Qt.WA_TransparentForMouseEvents)

        # A GROUP HEADER IS NOT A SECTION HEADER. `.sh` is
        # `--t-label / 500 / ink`; `.gh` is `--t-note / 600 / ink-2` --
        # smaller, heavier and quieter, which is what keeps a group from
        # competing with the section it sits inside.
        self.title_label = QtWidgets.QLabel(title)
        if variant == 'group':
            self.title_label.setObjectName('dyeworksGroupTitle')
            self.title_label.setFont(_ui_font(self, TYPE_NOTE, weight=600))
        else:
            self.title_label.setObjectName('dyeworksSectionTitle')
            # 2026-09-10; see design/DECISIONS.md#d-dyeworks_widgets-449
            self.title_label.setFont(_ui_font(self, TYPE_NOTE, weight=600))
        self.title_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        # The title is as wide as its text. Preferred, the layout handed it the
        # room the (Ignored) meta did not take -- 124px for 'Grain' -- which no
        # one saw until 20.3 put the change count after it (`.gchg`, 8 after).
        self.title_label.setSizePolicy(QtWidgets.QSizePolicy.Maximum,
                                       QtWidgets.QSizePolicy.Preferred)

        # 2026-08-25; see design/DECISIONS.md#d-dyeworks_widgets-450
        self.twisty.setFixedWidth(GROUP_TWISTY_PX)
        header_layout.addWidget(self.twisty, 0)
        header_layout.addWidget(self.title_label, 0)

    def _build_meta(self, meta, variant, header_layout):
        self.meta_label = ShrinkingLabel(meta)
        self.meta_label.setObjectName('dyeworksSectionMeta')
        self.meta_label.setFont(_mono_font(TYPE_GROUP_LABEL))
        self.meta_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        # 2026-09-28; see design/DECISIONS.md#d-dyeworks_widgets-451
        self.meta_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.meta_label.setContentsMargins(
            0, 0, GROUP_META_RIGHT_PX if variant == 'group' else 0, 0)

        # 2026-10-07; see design/DECISIONS.md#d-dyeworks_widgets-452
        self.count_label = None
        self.group_reset = None
        self._recount_pending = False
        if variant == 'group':
            self.count_label = QtWidgets.QLabel('')
            self.count_label.setObjectName('dyeworksGroupCount')
            self.count_label.setFont(_ui_font(self, TYPE_GROUP_LABEL))
            self.count_label.setContentsMargins(GROUP_COUNT_GAP - 3, 0, 0, 0)
            self.count_label.setAttribute(Qt.WA_TransparentForMouseEvents)
            self.count_label.hide()
            header_layout.addWidget(self.count_label, 0)
        # The title is held to its text now, so the SPARE room goes to a
        # stretch before the meta and the meta keeps its own width, ending
        # hard right. Preferred with a zero floor: it still gives way first
        # (a ShrinkingLabel elides), where the title's floor is its text.
        self.meta_label.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                                      QtWidgets.QSizePolicy.Preferred)
        self.meta_label.setMinimumWidth(0)
        header_layout.addStretch(1)
        header_layout.addWidget(self.meta_label, 0)
        if variant == 'group':
            self.group_reset = QtWidgets.QToolButton()
            self.group_reset.setObjectName('dyeworksRowReset')
            self.group_reset.setIconSize(QtCore.QSize(RESET_ICON_PX, RESET_ICON_PX))
            self.group_reset.setFixedSize(ROW_RESET_PX, ROW_RESET_PX)
            self.group_reset.setCursor(Qt.PointingHandCursor)
            self.group_reset.setToolTip('Reset this group')
            self.group_reset.setAccessibleName('Reset this group to its baseline')
            self.group_reset.setIcon(reset_icon(palette.TEXT_SOFT, palette.TEXT_BRIGHT))
            self.group_reset.clicked.connect(lambda *_: self.reset_group())
            self.group_reset.hide()
            header_layout.addWidget(self.group_reset, 0)
        self.header.meta_label = self.meta_label

    def _build_body(self, body_padding, body_gap, outer):
        self.body = QtWidgets.QWidget()
        self.body.setObjectName('dyeworksSectionBody')
        self._body_layout = QtWidgets.QVBoxLayout(self.body)
        self._body_layout.setContentsMargins(*body_padding)
        self._body_layout.setSpacing(body_gap)
        outer.addWidget(self.body)

    def _build_rule(self, rule, outer):
        self.rule = QtWidgets.QFrame()
        self.rule.setObjectName('dyeworksRule')
        self.rule.setFixedHeight(1)
        if rule == 'above':
            outer.insertWidget(0, self.rule)
        elif rule == 'none':
            self.rule.hide()
        else:
            outer.addWidget(self.rule)

    def _on_toggled(self, expanded: bool) -> None:
        self._expanded = expanded
        self.body.setVisible(expanded)
        self.twisty.setText('▾' if expanded else '▸')
        self.toggled.emit(expanded)

    def add(self, widget: QtWidgets.QWidget) -> QtWidgets.QWidget:
        self._body_layout.addWidget(widget)
        self.schedule_recount()
        return widget

    # ------------------------------------------------- 20.3: what changed
    def schedule_recount(self) -> None:
        """Count again once the event loop is free, so a burst of changes (a
        preset, a whole group reset) counts once."""
        if self.count_label is None or self._recount_pending:
            return
        self._recount_pending = True
        QtCore.QTimer.singleShot(0, self.recount)

    def _modified_rows(self) -> list:
        """The rows in this group that differ from their baseline: every
        modified `LabelledRow`, and every knob row whose reset is lit (its
        `_ResetButton` is enabled exactly then). A slider row's own inner
        reset is hidden, so nothing is counted twice."""
        rows = [row for row in self.body.findChildren(LabelledRow) if row.is_modified()]
        rows += [row for row in self.body.findChildren(HueSatRow) if row.is_modified()]

        rows += [button for button in self.body.findChildren(_ResetButton)
                 if not button.isHidden() and button.isEnabled()]
        return rows

    def changed_count(self) -> int:
        return len(self._modified_rows())

    def recount(self) -> None:
        self._recount_pending = False
        if self.count_label is None:
            return
        count = self.changed_count()
        self.count_label.setText('' if not count else
                                 ('1 changed' if count == 1 else f'{count} changed'))
        self.count_label.setVisible(bool(count))
        self.group_reset.setVisible(bool(count))
        # `.is-changed .gh .meta { margin-right: 26px }`: the reset takes the
        # meta's 8, the meta ends 10 before it
        self.meta_label.setContentsMargins(
            0, 0, GROUP_META_CHANGED_PX if count else GROUP_META_RIGHT_PX, 0)

    def reset_group(self) -> None:
        """Every changed row in the group back to its baseline, through each
        row's own reset (so profile sync, undo and the render see ordinary
        resets)."""
        for row in self._modified_rows():
            if callable(getattr(row, 'press_reset', None)):
                row.press_reset()          # a row, or the saturation row
            else:
                row.click()                # a knob row's own reset button
        self.schedule_recount()

    def clear(self) -> None:
        """Empty the body, for a section whose contents are rebuilt.

        Rows are DELETED rather than hidden -- a mirror row keeps signal
        connections to the editor it drives, and a hidden one would go on
        answering them.
        """
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def set_meta(self, text: str) -> None:
        self.meta_label.setText(text)
        self.meta_label.updateGeometry()

    def set_expanded(self, expanded: bool) -> None:
        if bool(expanded) == self._expanded:
            return
        self._on_toggled(bool(expanded))

    def is_expanded(self) -> bool:
        return self._expanded

