"""The library panel (Phase 18E).

The design's left column:

    brand lockup
    Roll | Reco | Config   -- a view selector, not tabs
    Roll:   File · Roll · Versions · Presets
    Reco:   Denoise · Defects · Lens correction · Highlights
    Config: display, defaults, locations, backend
    "Show rated" star filter, pinned to the bottom

2026-07-30; see design/DECISIONS.md#d-left_panel-581
"""

from __future__ import annotations

from typing import Callable

from qtpy import QtCore, QtWidgets

from spektrafilm_gui.widget_primitives import StarRating
from spektrafilm_gui.dyeworks_labels import paint_as_panel
from spektrafilm_gui.left_panel_config import (ShortcutsSectionView, UiSectionView,
                                               display_section, gpu_section, rehomed_section)
from spektrafilm_gui.customisation_section import BrandLockup
from spektrafilm_gui.left_panel_recover import RecoverView
from spektrafilm_gui.dyeworks_labels import VIEW_SELECTOR_TOP

Qt = QtCore.Qt

#: `.side { width: 300px }` — the design's width, and now the app's.
#: Reached in 18F by rebuilding the sections on `dyeworks_widgets`: the rebuilt
#: views measure File 210 / Roll 120 / Versions 136 / Presets 128, so the
#: panel's own floor is 268 and the design number is comfortable rather than
#: aspirational. A test pins this against the widest section.
PANEL_WIDTH = 300
MIN_PANEL_WIDTH = PANEL_WIDTH


# 2026-08-25; see design/DECISIONS.md#d-left_panel-567
VIEW_SELECTOR_GUTTER = 15
VIEW_SELECTOR_BOTTOM = 6


class ViewSelector(QtWidgets.QWidget):
    """`.stagewrap.left` — Roll | Config, content-sized and left-aligned.

    The same pressed-fills-ink language as the stage tabs, but a SELECTOR
    rather than a tab bar: it swaps which library view is shown, and Config
    is a place in the library, not a stage of the process.
    """

    changed = QtCore.Signal(int)

    def __init__(self, labels: tuple[str, ...]) -> None:
        super().__init__()
        self.setObjectName('viewSelector')
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(VIEW_SELECTOR_GUTTER, VIEW_SELECTOR_TOP,
                                  VIEW_SELECTOR_GUTTER, VIEW_SELECTOR_BOTTOM)
        layout.setSpacing(0)

        box = QtWidgets.QWidget()
        box.setObjectName('viewSelectorBox')
        box_layout = QtWidgets.QHBoxLayout(box)
        # `.stages { padding: 4px; border: 1px }` around a 31px pill: 41 (was 38)
        box_layout.setContentsMargins(5, 5, 5, 5)
        box_layout.setSpacing(3)

        self._buttons: list[QtWidgets.QPushButton] = []
        group = QtWidgets.QButtonGroup(self)
        group.setExclusive(True)
        for index, label in enumerate(labels):
            button = QtWidgets.QPushButton(label)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda *_, i=index: self.changed.emit(i))
            group.addButton(button)
            box_layout.addWidget(button)
            self._buttons.append(button)
        self._buttons[0].setChecked(True)

        layout.addWidget(box, 0, Qt.AlignLeft)
        layout.addStretch(1)

    def set_index(self, index: int) -> None:
        if 0 <= index < len(self._buttons):
            self._buttons[index].setChecked(True)

    def index(self) -> int:
        for index, button in enumerate(self._buttons):
            if button.isChecked():
                return index
        return 0


class RatingFilterFooter(QtWidgets.QFrame):
    """`.ratefilter` — pinned to the bottom, filters the roll."""

    def __init__(self, on_rating_filter: Callable[[int], None] | None = None) -> None:
        super().__init__()
        self.setObjectName('ratingFilterFooter')
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(15, 11, 15, 11)
        layout.setSpacing(4)

        label = QtWidgets.QLabel('筛选星级评分')
        label.setProperty('role', 'caption')
        layout.addWidget(label)
        layout.addStretch(1)


        self.stars = StarRating(compact=True, tooltip=(
            'Filter the roll by star rating: frames below this rating are '
            'grayed out, prev/next skips them, and Export roll only writes '
            'the rest. Click the active star again to show everything.'))
        self.stars.setObjectName('ratingFilter')
        if on_rating_filter is not None:
            self.stars.valueChanged.connect(on_rating_filter)
        layout.addWidget(self.stars)


def _scrollable(*sections: QtWidgets.QWidget) -> QtWidgets.QScrollArea:
    body = QtWidgets.QWidget()
    # 2026-08-02; see design/DECISIONS.md#d-left_panel-577
    paint_as_panel(body)
    layout = QtWidgets.QVBoxLayout(body)
    # 2026-09-29; see design/DECISIONS.md#d-left_panel-578
    layout.setContentsMargins(0, 0, 2, 0)
    layout.setSpacing(0)
    layout.setAlignment(Qt.AlignTop)
    for section in sections:
        layout.addWidget(section)
    layout.addStretch(1)

    # CLAMPED to its viewport -- see `panel_scroll`. `setWidgetResizable`
    # alone will not take the body below the body's own minimum, and one
    # long label or button sets that minimum for the whole panel.
    from spektrafilm_gui.panel_scroll import PanelScrollArea

    area = PanelScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QtWidgets.QFrame.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    paint_as_panel(area)
    area.setWidget(body)
    return area


def build_left_panel(widgets, *,
                     on_rating_filter: Callable[[int], None] | None = None,
                     session_toggles: QtWidgets.QWidget | None = None,
                     on_canvas_background: Callable[[str], None] | None = None,
                     ) -> QtWidgets.QWidget:
    """The library panel: brand, view selector, the two views, star footer."""
    frame = QtWidgets.QFrame()
    frame.setObjectName('leftPanel')
    frame.setMinimumWidth(MIN_PANEL_WIDTH)
    layout = QtWidgets.QVBoxLayout(frame)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)

    layout.addWidget(BrandLockup())

    selector = ViewSelector(('胶卷', '修复', '设置'))
    layout.addWidget(selector)

    stack = QtWidgets.QStackedWidget()
    stack.setObjectName('leftPanelStack')
    # ROLL: the frame you are on, the roll it belongs to, its versions, and
    # the presets you might apply. File is a VIEW over the load_raw
    # section's editors, re-presented in the design's shape -- the editors
    # themselves are untouched, so state and wiring do not notice.
    from spektrafilm_gui.left_panel_sections import (
        FileSection, PresetsSectionView, RollSectionView, VersionsSectionView)

    file_section = FileSection(widgets.load_raw)
    roll_section = RollSectionView(widgets.roll)
    versions_section = VersionsSectionView(widgets.roll)
    presets_section = PresetsSectionView(widgets.gui_config)
    # The original sections keep the debounce, the enable/disable, the file
    # list and the signal wiring, so they stay alive as hidden collaborators
    # rather than being replaced. Everything the views drive is THEIR widget.
    for hidden in (widgets.load_raw, widgets.roll, widgets.gui_config):
        hidden.setVisible(False)
    # File, Roll, PRESETS, Versions -- the page's order (the four `.sh`
    # buttons in that sequence). Presets before Versions: you choose a look
    # and then keep attempts of it, so the history reads below the thing it
    # is a history OF.
    stack.addWidget(_scrollable(
        file_section, roll_section, presets_section, versions_section,
        widgets.load_raw, widgets.roll, widgets.gui_config))
    # RECO: the repairs made to the raw before the film sees it. The section
    # that holds the editors stays alive, hidden, like the Roll view's.
    recover_view = RecoverView(widgets)
    widgets.recover.setVisible(False)
    stack.addWidget(_scrollable(recover_view, widgets.recover))
    # 2026-08-03; see design/DECISIONS.md#d-left_panel-579
    from spektrafilm_gui.customisation_section import CustomisationSectionView

    # `CFG_GROUPS`: UI, Display, GPU, Defaults, Locations, Shortcuts, Customisation, with
    # the page's TITLES. Display and GPU are rebuilt (the old ones were four
    # of six rows, named off the field name in lower case, and there was no
    # GPU group at all); Defaults and Locations keep their controls and get
    # the page's frame.
    config_rows = [UiSectionView(on_canvas_background),
                   display_section(widgets),
                   gpu_section(widgets),
                   rehomed_section(widgets.config_defaults, 'Defaults',
                                   expanded=False),
                   rehomed_section(widgets.locations, 'Locations',
                                   expanded=False),
                   ShortcutsSectionView(),
                   # 2026-09-25; see design/DECISIONS.md#d-left_panel-580
                   CustomisationSectionView()]
    if session_toggles is not None:
        # KEPT IN THE LAYOUT, hidden. Its flags are session state the
        # controller still reads, and a widget with no parent is a widget
        # waiting to be collected -- hidden it holds its lifetime and takes
        # no space, where dropping it entirely would be a use-after-free
        # hunt later.
        session_toggles.setVisible(False)
        config_rows.append(session_toggles)
    stack.addWidget(_scrollable(*config_rows))
    selector.changed.connect(stack.setCurrentIndex)
    layout.addWidget(stack, 1)

    layout.addWidget(RatingFilterFooter(on_rating_filter))

    frame.view_selector = selector
    frame.view_stack = stack
    frame.file_section = file_section
    frame.roll_section = roll_section
    frame.versions_section = versions_section
    frame.presets_section = presets_section
    frame.recover_view = recover_view
    return frame
